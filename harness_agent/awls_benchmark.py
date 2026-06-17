from __future__ import annotations

import json
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.standard_fjsp_awls_solver import parse_portfolio_lanes, solve_awls
from examples.standard_fjsp_evaluator import load_best_known

from .standard_fjsp import parse_standard_fjsp, validate_standard_schedule, write_solution


@dataclass(frozen=True)
class AwlsBenchmarkRequest:
    """Direct benchmark request for the embedded AWLS standard-FJSP solver.

    This runner intentionally bypasses the LLM worker loop.  Its role is to
    produce stable evaluator-backed evidence for the AWLS solver template:
    every selected instance is solved, validated, compared with best-known
    makespan when available, and written to an auditable output directory.
    """

    instance_dir: Path
    pattern: str
    output_dir: Path
    best_known_csv: Path | None = None
    max_instances: int | None = None
    include_families: list[str] | None = None
    sample_count: int | None = None
    sample_seed: int = 0
    seeds: list[int] | None = None
    max_workers: int = 1
    restarts: int = 2
    cycles_per_restart: int = 1000
    iterations: int = 10000
    time_limit_sec: float = 10.0
    init_mode: str = "random"
    exact_select_top_k: int = 0
    beta: int = 500
    gamma: int = 40
    theta: int = 5
    portfolio_lanes: str = ""
    critical_block_exhaustive_pct: int = 0
    same_machine_eval: str = "stable"
    time_policy: str = "fixed"
    resume: bool = False


def run_awls_benchmark(request: AwlsBenchmarkRequest) -> dict[str, Any]:
    """Run AWLS on a batch of standard FJSP instances and write a report."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / "runs"
    solution_dir = output_dir / "solutions"
    run_dir.mkdir(parents=True, exist_ok=True)
    solution_dir.mkdir(parents=True, exist_ok=True)

    instances = selected_instances(request)
    if not instances:
        raise ValueError(f"no instances matched {request.instance_dir / request.pattern}")

    seeds = request.seeds or [0]
    lane_specs = parse_portfolio_lanes(request.portfolio_lanes) if request.portfolio_lanes else None
    effective_seeds = [seeds[0]] if lane_specs else seeds
    jobs: list[tuple[Path, int]] = [(path, seed) for path in instances for seed in effective_seeds]
    max_workers = max(1, min(request.max_workers, len(jobs)))

    run_results: list[dict[str, Any]] = []
    if max_workers == 1:
        for instance_path, seed in jobs:
            run_results.append(run_one_awls_job(request, instance_path, seed, run_dir, solution_dir))
            write_awls_manifest(output_dir, request, instances, run_results, status="running")
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_one_awls_job, request, instance_path, seed, run_dir, solution_dir): (instance_path, seed)
                for instance_path, seed in jobs
            }
            for future in as_completed(futures):
                run_results.append(future.result())
                write_awls_manifest(output_dir, request, instances, run_results, status="running")

    run_results.sort(key=lambda item: (str(item.get("instance", "")), int(item.get("seed", 0))))
    manifest = write_awls_manifest(output_dir, request, instances, run_results, status=None)
    return manifest


def write_awls_manifest(
    output_dir: Path,
    request: AwlsBenchmarkRequest,
    instances: list[Path],
    run_results: list[dict[str, Any]],
    *,
    status: str | None,
) -> dict[str, Any]:
    """Write the current benchmark state so long runs can be inspected/resumed."""

    sorted_runs = sorted(run_results, key=lambda item: (str(item.get("instance", "")), int(item.get("seed", 0))))
    instance_results = best_result_by_instance(sorted_runs)
    aggregate = aggregate_awls_results(instance_results, sorted_runs)
    if status is None:
        status = "ok" if aggregate["invalid_run_count"] == 0 else "partial_failed"
    manifest = {
        "status": status,
        "request": request_to_json(request),
        "instance_count": len(instances),
        "run_count": len(sorted_runs),
        "aggregate": aggregate,
        "instances": instance_results,
        "runs": sorted_runs,
    }
    manifest_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    manifest["artifacts"] = {
        "summary": str(manifest_path),
        "report": str(report_path),
        "solutions": str(output_dir / "solutions"),
        "runs": str(output_dir / "runs"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_awls_benchmark_report(manifest), encoding="utf-8")
    return manifest


def selected_instances(request: AwlsBenchmarkRequest) -> list[Path]:
    instance_dir = request.instance_dir.resolve()
    paths = sorted(path for path in instance_dir.glob(request.pattern) if path.is_file())
    if request.include_families:
        allowed = {family.lower() for family in request.include_families}
        paths = [path for path in paths if instance_family(path) in allowed]
    if request.sample_count is not None:
        paths = stratified_sample_instances(paths, request.sample_count, request.sample_seed)
    if request.max_instances is not None:
        paths = paths[: max(0, request.max_instances)]
    return paths


def stratified_sample_instances(paths: list[Path], sample_count: int, sample_seed: int) -> list[Path]:
    """Select a reproducible, family-balanced sample from benchmark instances."""

    count = max(0, sample_count)
    if count >= len(paths):
        return sorted(paths)
    if count == 0 or not paths:
        return []

    grouped: dict[str, list[Path]] = {}
    for path in sorted(paths):
        grouped.setdefault(instance_family(path), []).append(path)

    families = sorted(grouped)
    quota = {family: 0 for family in families}
    active = set(families)
    remaining = count
    while remaining > 0 and active:
        for family in families:
            if remaining <= 0:
                break
            if family not in active:
                continue
            if quota[family] < len(grouped[family]):
                quota[family] += 1
                remaining -= 1
            else:
                active.remove(family)

    rng = random.Random(sample_seed)
    selected: list[Path] = []
    for family in families:
        candidates = list(grouped[family])
        rng.shuffle(candidates)
        selected.extend(candidates[: quota[family]])
    return sorted(selected, key=lambda path: (instance_family(path), path.name))


def run_one_awls_job(
    request: AwlsBenchmarkRequest,
    instance_path: Path,
    seed: int,
    run_dir: Path,
    solution_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    safe_stem = safe_instance_stem(instance_path)
    solution_path = solution_dir / f"{safe_stem}_seed{seed}.json"
    metrics_path = run_dir / f"{safe_stem}_seed{seed}_metrics.json"
    if request.resume:
        resumed = load_resumed_result(instance_path, seed, metrics_path, solution_path)
        if resumed is not None:
            return resumed

    try:
        instance = parse_standard_fjsp(instance_path)
        lane_specs = parse_portfolio_lanes(request.portfolio_lanes) if request.portfolio_lanes else None
        time_limit_sec = effective_time_limit_sec(request, instance_path)
        schedule, strategy = solve_awls(
            instance,
            seed=seed,
            restarts=max(1, request.restarts),
            cycles_per_restart=max(1, request.cycles_per_restart),
            iterations=max(0, request.iterations),
            time_limit_sec=time_limit_sec,
            init_mode=request.init_mode,
            beta=max(1, request.beta),
            gamma=max(1, request.gamma),
            theta=max(0, request.theta),
            exact_select_top_k=max(0, request.exact_select_top_k),
            same_machine_eval=request.same_machine_eval,
            portfolio_lanes=lane_specs,
            critical_block_exhaustive_pct=max(0, min(100, request.critical_block_exhaustive_pct)),
        )
        errors, metrics = validate_standard_schedule(instance, schedule)
        if not errors:
            write_solution(solution_path, instance, schedule, strategy)
        best_known = load_best_known(request.best_known_csv, instance.name)
        if best_known and best_known > 0:
            metrics["best_known_makespan"] = float(best_known)
            metrics["gap_pct"] = (metrics["makespan"] - best_known) / best_known * 100.0
        payload = {
            "valid": not errors,
            "error_count": len(errors),
            "errors": errors,
            "metrics": metrics,
        }
        metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "instance": instance_path.name,
            "seed": seed,
            "status": "ok" if not errors else "invalid",
            "valid": not errors,
            "error_count": len(errors),
            "errors": errors[:20],
            "makespan": metrics.get("makespan"),
            "best_known_makespan": metrics.get("best_known_makespan"),
            "gap_pct": metrics.get("gap_pct"),
            "runtime_sec": round(time.perf_counter() - started, 3),
            "time_limit_sec": time_limit_sec,
            "resumed": False,
            "solution": str(solution_path) if not errors else None,
            "metrics": str(metrics_path),
            "strategy": strategy,
        }
    except Exception as exc:  # noqa: BLE001 - benchmark reports preserve per-instance failures.
        payload = {
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "metrics": {},
        }
        metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "instance": instance_path.name,
            "seed": seed,
            "status": "failed",
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "makespan": None,
            "best_known_makespan": None,
            "gap_pct": None,
            "runtime_sec": round(time.perf_counter() - started, 3),
            "time_limit_sec": effective_time_limit_sec(request, instance_path),
            "resumed": False,
            "solution": None,
            "metrics": str(metrics_path),
            "strategy": None,
        }


def load_resumed_result(instance_path: Path, seed: int, metrics_path: Path, solution_path: Path) -> dict[str, Any] | None:
    if not metrics_path.exists():
        return None
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    metrics = dict(payload.get("metrics") or {})
    valid = bool(payload.get("valid"))
    solution = None
    strategy = None
    if valid and solution_path.exists():
        solution = str(solution_path)
        try:
            raw_solution = json.loads(solution_path.read_text(encoding="utf-8"))
            strategy = raw_solution.get("strategy")
        except (OSError, json.JSONDecodeError):
            strategy = None
    elif valid:
        return None
    return {
        "instance": instance_path.name,
        "seed": seed,
        "status": "ok" if valid else "invalid",
        "valid": valid,
        "error_count": int(payload.get("error_count", 0) or 0),
        "errors": list(payload.get("errors") or [])[:20],
        "makespan": metrics.get("makespan"),
        "best_known_makespan": metrics.get("best_known_makespan"),
        "gap_pct": metrics.get("gap_pct"),
        "runtime_sec": 0.0,
        "time_limit_sec": None,
        "resumed": True,
        "solution": solution,
        "metrics": str(metrics_path),
        "strategy": strategy,
    }


def effective_time_limit_sec(request: AwlsBenchmarkRequest, instance_path: Path) -> float:
    policy = request.time_policy.lower()
    fixed = max(0.0, request.time_limit_sec)
    if policy == "fixed":
        return fixed
    if policy == "mae2019":
        family = instance_family(instance_path)
        if family in {"barnes", "brandimarte"}:
            return 90.0
        if family in {"dauzere", "hurink"}:
            return 300.0
        return fixed if fixed > 0 else 300.0
    if policy == "mae2019-hour":
        return 3600.0
    raise ValueError(f"unknown AWLS benchmark time policy: {request.time_policy}")


def instance_family(instance_path: Path) -> str:
    parts = instance_path.name.lower().split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "fjsp" else parts[0]


def best_result_by_instance(run_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in run_results:
        grouped.setdefault(str(item.get("instance")), []).append(item)
    selected: list[dict[str, Any]] = []
    for instance_name, items in sorted(grouped.items()):
        valid_items = [item for item in items if item.get("valid") and isinstance(item.get("makespan"), (int, float))]
        if valid_items:
            best = min(valid_items, key=lambda item: float(item["makespan"]))
        else:
            best = items[0]
        selected.append(dict(best, run_count=len(items)))
    return selected


def aggregate_awls_results(instance_results: list[dict[str, Any]], run_results: list[dict[str, Any]]) -> dict[str, Any]:
    valid_runs = [item for item in run_results if item.get("valid")]
    valid_instances = [item for item in instance_results if item.get("valid")]
    gap_values = [float(item["gap_pct"]) for item in valid_instances if isinstance(item.get("gap_pct"), (int, float))]
    makespans = [float(item["makespan"]) for item in valid_instances if isinstance(item.get("makespan"), (int, float))]
    return {
        "instance_count": len(instance_results),
        "run_count": len(run_results),
        "valid_run_count": len(valid_runs),
        "invalid_run_count": len(run_results) - len(valid_runs),
        "valid_instance_count": len(valid_instances),
        "invalid_instance_count": len(instance_results) - len(valid_instances),
        "avg_makespan": sum(makespans) / len(makespans) if makespans else None,
        "avg_gap_pct": sum(gap_values) / len(gap_values) if gap_values else None,
        "median_gap_pct": statistics.median(gap_values) if gap_values else None,
        "max_gap_pct": max(gap_values) if gap_values else None,
        "best_reached_count": sum(1 for value in gap_values if value <= 0.0),
        "within_1pct_count": sum(1 for value in gap_values if value <= 1.0),
        "within_2pct_count": sum(1 for value in gap_values if value <= 2.0),
        "gap_count": len(gap_values),
    }


def render_awls_benchmark_report(manifest: dict[str, Any]) -> str:
    aggregate = manifest.get("aggregate") or {}
    lines = [
        "# AWLS Standard FJSP Benchmark Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Instances: `{aggregate.get('instance_count')}`",
        f"- Runs: `{aggregate.get('run_count')}`",
        f"- Valid runs: `{aggregate.get('valid_run_count')}`",
        f"- Invalid runs: `{aggregate.get('invalid_run_count')}`",
        f"- Average gap pct: `{aggregate.get('avg_gap_pct')}`",
        f"- Median gap pct: `{aggregate.get('median_gap_pct')}`",
        f"- Max gap pct: `{aggregate.get('max_gap_pct')}`",
        f"- Best reached: `{aggregate.get('best_reached_count')}/{aggregate.get('gap_count')}`",
        f"- Within 1 pct: `{aggregate.get('within_1pct_count')}/{aggregate.get('gap_count')}`",
        f"- Within 2 pct: `{aggregate.get('within_2pct_count')}/{aggregate.get('gap_count')}`",
        "",
        "## Instance Results",
        "",
        "| Instance | Valid | Makespan | Best Known | Gap % | Seed | Runtime s | Solution |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in manifest.get("instances", []):
        solution = item.get("solution")
        solution_cell = f"[json]({solution})" if solution else "N/A"
        lines.append(
            f"| {item.get('instance')} | `{item.get('valid')}` | {format_cell(item.get('makespan'))} | "
            f"{format_cell(item.get('best_known_makespan'))} | {format_cell(item.get('gap_pct'))} | "
            f"{item.get('seed')} | {format_cell(item.get('runtime_sec'))} | {solution_cell} |"
        )
    lines.extend(
        [
            "",
            "## Aggregate JSON",
            "",
            f"```json\n{json.dumps(aggregate, ensure_ascii=False, indent=2)}\n```",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def request_to_json(request: AwlsBenchmarkRequest) -> dict[str, Any]:
    payload = dict(request.__dict__)
    for key in ("instance_dir", "output_dir", "best_known_csv"):
        value = payload.get(key)
        payload[key] = str(value) if value is not None else None
    return payload


def safe_instance_stem(path: Path) -> str:
    return path.name.replace("/", "_").replace("\\", "_").replace(" ", "_")


def format_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value is None:
        return "N/A"
    return str(value)
