from __future__ import annotations

import json
import random
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.standard_fjsp_awls_solver import parse_portfolio_lanes, solve_awls
from examples.standard_fjsp_evaluator import load_best_known

from .benchmark_bounds import BenchmarkBounds, benchmark_family_label, find_bounds, load_bounds_table
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
    bounds_csv: Path | None = None
    max_instances: int | None = None
    include_families: list[str] | None = None
    instance_names: list[str] | None = None
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
    zi_policy: str = "cpp"
    zi_formula: str = ""
    initial_state: str = "reset"
    time_check_interval: int = 1
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
    bounds = load_bounds_table(request.bounds_csv or request.best_known_csv)

    seeds = request.seeds or [0]
    jobs: list[tuple[Path, int]] = [(path, seed) for path in instances for seed in seeds]
    max_workers = max(1, min(request.max_workers, len(jobs)))

    run_results: list[dict[str, Any]] = []
    if max_workers == 1:
        for instance_path, seed in jobs:
            run_results.append(run_one_awls_job(request, instance_path, seed, run_dir, solution_dir, bounds))
            write_awls_manifest(output_dir, request, instances, run_results, status="running")
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_one_awls_job, request, instance_path, seed, run_dir, solution_dir, bounds): (
                    instance_path,
                    seed,
                )
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
        "selected_instance_names": [path.name for path in instances],
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
    if request.instance_names:
        return select_named_instances(paths, request.instance_names)
    if request.sample_count is not None:
        paths = stratified_sample_instances(paths, request.sample_count, request.sample_seed)
    if request.max_instances is not None:
        paths = paths[: max(0, request.max_instances)]
    return paths


def select_named_instances(paths: list[Path], names: list[str]) -> list[Path]:
    """Select an exact, ordered benchmark subset by file name or relative path."""

    by_key: dict[str, Path] = {}
    for path in paths:
        by_key[path.name] = path
        by_key[path.as_posix()] = path

    selected: list[Path] = []
    missing: list[str] = []
    seen: set[Path] = set()
    for raw_name in names:
        name = raw_name.strip()
        if not name or name.startswith("#"):
            continue
        normalized = Path(name).as_posix()
        path = by_key.get(name) or by_key.get(normalized)
        if path is None:
            missing.append(name)
            continue
        if path not in seen:
            selected.append(path)
            seen.add(path)
    if missing:
        missing_text = ", ".join(missing[:10])
        raise ValueError(f"requested AWLS benchmark instances not found: {missing_text}")
    return selected


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
    bounds_table: dict[str, BenchmarkBounds],
) -> dict[str, Any]:
    started = time.perf_counter()
    safe_stem = safe_instance_stem(instance_path)
    solution_path = solution_dir / f"{safe_stem}_seed{seed}.json"
    metrics_path = run_dir / f"{safe_stem}_seed{seed}_metrics.json"
    if request.resume:
        resumed = load_resumed_result(
            instance_path,
            seed,
            metrics_path,
            solution_path,
            bounds_table=bounds_table,
            best_known_csv=request.best_known_csv,
        )
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
            zi_policy=request.zi_policy,
            zi_formula=request.zi_formula,
            initial_state=request.initial_state,
            exact_select_top_k=max(0, request.exact_select_top_k),
            same_machine_eval=request.same_machine_eval,
            portfolio_lanes=lane_specs,
            critical_block_exhaustive_pct=max(0, min(100, request.critical_block_exhaustive_pct)),
            time_check_interval=max(1, request.time_check_interval),
        )
        errors, metrics = validate_standard_schedule(instance, schedule)
        if not errors:
            write_solution(solution_path, instance, schedule, strategy)
        lower_bound, upper_bound, bounds_source, bounds_note = resolve_instance_bounds(
            bounds_table,
            request.best_known_csv,
            instance.name,
        )
        attach_bounds_to_metrics(metrics, lower_bound=lower_bound, upper_bound=upper_bound)
        runtime_sec = round(time.perf_counter() - started, 3)
        payload = {
            "valid": not errors,
            "error_count": len(errors),
            "errors": errors,
            "metrics": metrics,
            "runtime_sec": runtime_sec,
            "time_limit_sec": time_limit_sec,
            "strategy": strategy,
            "solution": str(solution_path) if not errors else None,
        }
        metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "instance": instance_path.name,
            "family_label": benchmark_family_label(instance_path.name),
            "seed": seed,
            "status": "ok" if not errors else "invalid",
            "valid": not errors,
            "error_count": len(errors),
            "errors": errors[:20],
            "makespan": metrics.get("makespan"),
            "lower_bound_makespan": metrics.get("lower_bound_makespan"),
            "upper_bound_makespan": metrics.get("upper_bound_makespan"),
            "best_known_makespan": metrics.get("best_known_makespan"),
            "gap_pct": metrics.get("gap_pct"),
            "gap_to_lb_pct": metrics.get("gap_to_lb_pct"),
            "gap_to_ub_pct": metrics.get("gap_to_ub_pct"),
            "bounds_source": bounds_source,
            "bounds_note": bounds_note,
            "runtime_sec": runtime_sec,
            "time_limit_sec": time_limit_sec,
            "resumed": False,
            "solution": str(solution_path) if not errors else None,
            "metrics": str(metrics_path),
            "strategy": strategy,
        }
    except Exception as exc:  # noqa: BLE001 - benchmark reports preserve per-instance failures.
        lower_bound, upper_bound, bounds_source, bounds_note = resolve_instance_bounds(
            bounds_table,
            request.best_known_csv,
            instance_path.name,
        )
        payload = {
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "metrics": {},
            "runtime_sec": round(time.perf_counter() - started, 3),
            "time_limit_sec": effective_time_limit_sec(request, instance_path),
            "strategy": None,
            "solution": None,
        }
        metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "instance": instance_path.name,
            "family_label": benchmark_family_label(instance_path.name),
            "seed": seed,
            "status": "failed",
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "makespan": None,
            "lower_bound_makespan": lower_bound,
            "upper_bound_makespan": upper_bound,
            "best_known_makespan": None,
            "gap_pct": None,
            "gap_to_lb_pct": None,
            "gap_to_ub_pct": None,
            "bounds_source": bounds_source,
            "bounds_note": bounds_note,
            "runtime_sec": payload["runtime_sec"],
            "time_limit_sec": payload["time_limit_sec"],
            "resumed": False,
            "solution": None,
            "metrics": str(metrics_path),
            "strategy": None,
        }


def load_resumed_result(
    instance_path: Path,
    seed: int,
    metrics_path: Path,
    solution_path: Path,
    *,
    bounds_table: dict[str, BenchmarkBounds] | None = None,
    best_known_csv: Path | None = None,
) -> dict[str, Any] | None:
    if not metrics_path.exists():
        return None
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    metrics = dict(payload.get("metrics") or {})
    valid = bool(payload.get("valid"))
    solution = payload.get("solution") if isinstance(payload.get("solution"), str) else None
    strategy = payload.get("strategy") if isinstance(payload.get("strategy"), str) else None
    if valid and solution_path.exists():
        solution = solution or str(solution_path)
        try:
            raw_solution = json.loads(solution_path.read_text(encoding="utf-8"))
            strategy = strategy or raw_solution.get("strategy")
        except (OSError, json.JSONDecodeError):
            pass
    elif valid:
        return None
    runtime_sec = payload.get("runtime_sec")
    if not isinstance(runtime_sec, (int, float)):
        runtime_sec = None
    time_limit_sec = payload.get("time_limit_sec")
    if not isinstance(time_limit_sec, (int, float)):
        time_limit_sec = None
    lower_bound, upper_bound, bounds_source, bounds_note = resolve_instance_bounds(
        bounds_table or {},
        best_known_csv,
        instance_path.name,
    )
    if lower_bound is None:
        lower_bound = metrics.get("lower_bound_makespan")
    if upper_bound is None:
        upper_bound = metrics.get("upper_bound_makespan") or metrics.get("best_known_makespan")
    attach_bounds_to_metrics(metrics, lower_bound=lower_bound, upper_bound=upper_bound)
    return {
        "instance": instance_path.name,
        "family_label": benchmark_family_label(instance_path.name),
        "seed": seed,
        "status": "ok" if valid else "invalid",
        "valid": valid,
        "error_count": int(payload.get("error_count", 0) or 0),
        "errors": list(payload.get("errors") or [])[:20],
        "makespan": metrics.get("makespan"),
        "lower_bound_makespan": metrics.get("lower_bound_makespan"),
        "upper_bound_makespan": metrics.get("upper_bound_makespan"),
        "best_known_makespan": metrics.get("best_known_makespan"),
        "gap_pct": metrics.get("gap_pct"),
        "gap_to_lb_pct": metrics.get("gap_to_lb_pct"),
        "gap_to_ub_pct": metrics.get("gap_to_ub_pct") or metrics.get("gap_pct"),
        "bounds_source": bounds_source,
        "bounds_note": bounds_note,
        "runtime_sec": runtime_sec,
        "time_limit_sec": time_limit_sec,
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
    if policy == "scaled":
        return max(fixed, scaled_time_limit_sec(instance_path))
    if policy == "mae2019":
        parsed = parse_instance_for_time_policy(instance_path)
        if parsed is not None and filename_shape_mismatch(instance_path, parsed):
            if parsed.job_count >= 15 or parsed.machine_count >= 8 or parsed.operation_count >= 100:
                return 300.0
            return 90.0
        family = instance_family(instance_path)
        if family in {"barnes", "brandimarte"}:
            return 90.0
        if family in {"dauzere", "hurink"}:
            return 300.0
        return fixed if fixed > 0 else 300.0
    if policy == "mae2019-hour":
        return 3600.0
    raise ValueError(f"unknown AWLS benchmark time policy: {request.time_policy}")


def resolve_instance_bounds(
    bounds_table: dict[str, BenchmarkBounds],
    best_known_csv: Path | None,
    instance_name: str,
) -> tuple[float | None, float | None, str | None, str | None]:
    entry = find_bounds(bounds_table, instance_name)
    if entry is not None:
        return entry.lower_bound, entry.upper_bound, entry.source, entry.note
    best_known = load_best_known(best_known_csv, instance_name)
    if best_known is not None:
        return None, float(best_known), str(best_known_csv) if best_known_csv is not None else None, None
    return None, None, None, None


def attach_bounds_to_metrics(metrics: dict[str, Any], *, lower_bound: Any, upper_bound: Any) -> None:
    makespan = metrics.get("makespan")
    if upper_bound is not None:
        metrics["upper_bound_makespan"] = float(upper_bound)
        metrics["best_known_makespan"] = float(upper_bound)
    if lower_bound is not None:
        metrics["lower_bound_makespan"] = float(lower_bound)
    if not isinstance(makespan, (int, float)):
        return
    if isinstance(upper_bound, (int, float)) and upper_bound > 0:
        metrics["gap_to_ub_pct"] = (float(makespan) - float(upper_bound)) / float(upper_bound) * 100.0
        metrics["gap_pct"] = metrics["gap_to_ub_pct"]
    if isinstance(lower_bound, (int, float)) and lower_bound > 0:
        metrics["gap_to_lb_pct"] = (float(makespan) - float(lower_bound)) / float(lower_bound) * 100.0


def scaled_time_limit_sec(instance_path: Path) -> float:
    instance = parse_standard_fjsp(instance_path)
    scale = instance.job_count * instance.machine_count * instance.operation_count
    if scale <= 1_000:
        return 30.0
    if scale <= 6_000:
        return 90.0
    if scale <= 20_000:
        return 300.0
    return 600.0


def parse_instance_for_time_policy(instance_path: Path):
    try:
        return parse_standard_fjsp(instance_path)
    except (OSError, ValueError):
        return None


def filename_shape_mismatch(instance_path: Path, instance: Any) -> bool:
    shape = filename_shape(instance_path.name)
    if shape is None:
        return False
    return (
        shape["job_count"] != instance.job_count
        or shape["machine_count"] != instance.machine_count
        or shape["max_candidate_count"] != instance.max_candidate_count
    )


def filename_shape(name: str) -> dict[str, int] | None:
    match = re.search(r"m(?P<machines>\d+)j(?P<jobs>\d+)c(?P<candidates>\d+)", name.lower())
    if not match:
        return None
    return {
        "job_count": int(match.group("jobs")),
        "machine_count": int(match.group("machines")),
        "max_candidate_count": int(match.group("candidates")),
    }


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
    gap_to_lb_values = [
        float(item["gap_to_lb_pct"]) for item in valid_instances if isinstance(item.get("gap_to_lb_pct"), (int, float))
    ]
    gap_to_ub_values = [
        float(item["gap_to_ub_pct"]) for item in valid_instances if isinstance(item.get("gap_to_ub_pct"), (int, float))
    ]
    makespans = [float(item["makespan"]) for item in valid_instances if isinstance(item.get("makespan"), (int, float))]
    seeds = sorted({int(item.get("seed", 0)) for item in run_results})
    family_aggregate = aggregate_by_family(instance_results)
    return {
        "instance_count": len(instance_results),
        "seed_count": len(seeds),
        "seeds": seeds,
        "run_count": len(run_results),
        "valid_run_count": len(valid_runs),
        "invalid_run_count": len(run_results) - len(valid_runs),
        "valid_instance_count": len(valid_instances),
        "invalid_instance_count": len(instance_results) - len(valid_instances),
        "avg_makespan": sum(makespans) / len(makespans) if makespans else None,
        "avg_gap_pct": sum(gap_values) / len(gap_values) if gap_values else None,
        "avg_gap_to_lb_pct": sum(gap_to_lb_values) / len(gap_to_lb_values) if gap_to_lb_values else None,
        "avg_gap_to_ub_pct": sum(gap_to_ub_values) / len(gap_to_ub_values) if gap_to_ub_values else None,
        "median_gap_pct": statistics.median(gap_values) if gap_values else None,
        "max_gap_pct": max(gap_values) if gap_values else None,
        "best_reached_count": sum(1 for value in gap_values if value <= 0.0),
        "within_1pct_count": sum(1 for value in gap_values if value <= 1.0),
        "within_2pct_count": sum(1 for value in gap_values if value <= 2.0),
        "gap_count": len(gap_values),
        "gap_to_lb_count": len(gap_to_lb_values),
        "gap_to_ub_count": len(gap_to_ub_values),
        "family_aggregate": family_aggregate,
    }


def aggregate_by_family(instance_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in instance_results:
        grouped.setdefault(str(item.get("family_label") or benchmark_family_label(str(item.get("instance", "")))), []).append(item)

    aggregate: dict[str, dict[str, Any]] = {}
    for family, items in sorted(grouped.items()):
        valid_items = [item for item in items if item.get("valid")]
        makespans = [float(item["makespan"]) for item in valid_items if isinstance(item.get("makespan"), (int, float))]
        gap_to_lb = [float(item["gap_to_lb_pct"]) for item in valid_items if isinstance(item.get("gap_to_lb_pct"), (int, float))]
        gap_to_ub = [float(item["gap_to_ub_pct"]) for item in valid_items if isinstance(item.get("gap_to_ub_pct"), (int, float))]
        aggregate[family] = {
            "instance_count": len(items),
            "valid_instance_count": len(valid_items),
            "invalid_instance_count": len(items) - len(valid_items),
            "avg_makespan": sum(makespans) / len(makespans) if makespans else None,
            "avg_gap_to_lb_pct": sum(gap_to_lb) / len(gap_to_lb) if gap_to_lb else None,
            "avg_gap_to_ub_pct": sum(gap_to_ub) / len(gap_to_ub) if gap_to_ub else None,
            "gap_to_lb_count": len(gap_to_lb),
            "gap_to_ub_count": len(gap_to_ub),
        }
    return aggregate


def render_awls_benchmark_report(manifest: dict[str, Any]) -> str:
    aggregate = manifest.get("aggregate") or {}
    lines = [
        "# AWLS Standard FJSP Benchmark Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Instances: `{aggregate.get('instance_count')}`",
        f"- Seed count: `{aggregate.get('seed_count')}`",
        f"- Runs: `{aggregate.get('run_count')}`",
        f"- Valid runs: `{aggregate.get('valid_run_count')}`",
        f"- Invalid runs: `{aggregate.get('invalid_run_count')}`",
        f"- Average gap pct: `{aggregate.get('avg_gap_pct')}`",
        f"- Average gap to LB pct: `{aggregate.get('avg_gap_to_lb_pct')}`",
        f"- Average gap to UB pct: `{aggregate.get('avg_gap_to_ub_pct')}`",
        f"- Median gap pct: `{aggregate.get('median_gap_pct')}`",
        f"- Max gap pct: `{aggregate.get('max_gap_pct')}`",
        f"- Best reached: `{aggregate.get('best_reached_count')}/{aggregate.get('gap_count')}`",
        f"- Within 1 pct: `{aggregate.get('within_1pct_count')}/{aggregate.get('gap_count')}`",
        f"- Within 2 pct: `{aggregate.get('within_2pct_count')}/{aggregate.get('gap_count')}`",
        "",
        "## Family Aggregate",
        "",
        "| Family | Instances | Valid | Avg Makespan | Avg Gap to LB % | Avg Gap to UB % | LB Count | UB Count |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for family, item in sorted((aggregate.get("family_aggregate") or {}).items()):
        lines.append(
            f"| {family} | {item.get('instance_count')} | {item.get('valid_instance_count')} | "
            f"{format_cell(item.get('avg_makespan'))} | {format_cell(item.get('avg_gap_to_lb_pct'))} | "
            f"{format_cell(item.get('avg_gap_to_ub_pct'))} | {item.get('gap_to_lb_count')} | "
            f"{item.get('gap_to_ub_count')} |"
        )
    lines.extend(
        [
            "",
            "## Instance Results",
            "",
            "| Family | Instance | Valid | Makespan | LB | UB/BKS | Gap to LB % | Gap to UB % | Seed | Runtime s | Solution |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for item in manifest.get("instances", []):
        solution = item.get("solution")
        solution_cell = f"[json]({solution})" if solution else "N/A"
        lines.append(
            f"| {item.get('family_label')} | {item.get('instance')} | `{item.get('valid')}` | "
            f"{format_cell(item.get('makespan'))} | {format_cell(item.get('lower_bound_makespan'))} | "
            f"{format_cell(item.get('upper_bound_makespan') or item.get('best_known_makespan'))} | "
            f"{format_cell(item.get('gap_to_lb_pct'))} | {format_cell(item.get('gap_to_ub_pct') or item.get('gap_pct'))} | "
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
    for key in ("instance_dir", "output_dir", "best_known_csv", "bounds_csv"):
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
