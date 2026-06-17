from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .demo import StandardDemoRequest, run_standard_demo


@dataclass(frozen=True)
class BenchmarkSuiteRequest:
    """Request for running multiple standard-FJSP demo loops as one suite."""

    config_path: Path
    output_dir: Path
    project_root: Path
    max_suites: int | None = None


def run_benchmark_suite(request: BenchmarkSuiteRequest) -> dict[str, Any]:
    """Run all configured standard-FJSP suites and aggregate evaluator evidence."""

    config_path = request.config_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    defaults = dict(config.get("defaults") or {})
    suite_specs = list(config.get("suites") or [])
    if request.max_suites is not None:
        suite_specs = suite_specs[: max(0, request.max_suites)]
    if not suite_specs:
        raise ValueError("benchmark suite config must contain at least one suite")

    suite_results: list[dict[str, Any]] = []
    for index, suite_spec in enumerate(suite_specs):
        merged = {**defaults, **dict(suite_spec)}
        suite_name = str(merged.get("name") or f"suite_{index:02d}")
        suite_dir = output_dir / "suites" / safe_name(suite_name)
        try:
            manifest = run_standard_demo(build_demo_request(merged, config_path=config_path, output_dir=suite_dir, project_root=request.project_root))
            suite_results.append(
                {
                    "name": suite_name,
                    "status": manifest["status"],
                    "output_dir": str(suite_dir),
                    "benchmark_summary": manifest.get("benchmark_summary") or {},
                    "artifacts": manifest.get("artifacts") or {},
                    "request": manifest.get("request") or {},
                }
            )
        except Exception as exc:  # noqa: BLE001 - suite reports should preserve failed suite facts.
            suite_results.append(
                {
                    "name": suite_name,
                    "status": "failed",
                    "output_dir": str(suite_dir),
                    "error": str(exc),
                    "benchmark_summary": {},
                    "artifacts": {},
                    "request": merged,
                }
            )

    manifest = {
        "status": "ok" if all(item["status"] == "ok" for item in suite_results) else "partial_failed",
        "config": str(config_path),
        "suite_count": len(suite_results),
        "suite_results": suite_results,
        "aggregate": aggregate_suite_results(suite_results),
    }
    manifest_path = output_dir / "suite_manifest.json"
    report_path = output_dir / "suite_report.md"
    manifest["artifacts"] = {
        "manifest": str(manifest_path.resolve()),
        "report": str(report_path.resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_benchmark_suite_report(manifest), encoding="utf-8")
    return manifest


def build_demo_request(
    spec: dict[str, Any],
    *,
    config_path: Path,
    output_dir: Path,
    project_root: Path,
) -> StandardDemoRequest:
    base_dir = config_path.parent
    return StandardDemoRequest(
        docs=[resolve_config_path(base_dir, item) for item in list(spec.get("docs") or [])],
        instance_dir=resolve_config_path(base_dir, spec["instance_dir"]),
        pattern=str(spec.get("pattern", "*.txt")),
        best_known_csv=resolve_config_path(base_dir, spec["best_known_csv"]) if spec.get("best_known_csv") else None,
        output_dir=output_dir,
        project_root=project_root,
        max_instances=spec.get("max_instances"),
        max_rounds=int(spec.get("max_rounds", 1)),
        seeds=[int(item) for item in spec.get("seeds", [0])],
        timeout_seconds=int(spec.get("timeout_seconds", 60)),
        max_workers=int(spec.get("max_workers", 1)),
        solver=str(spec.get("solver", "portfolio")),
        portfolio_size=int(spec.get("portfolio_size", 16)),
        local_search_restarts=int(spec.get("local_search_restarts", 1)),
        local_search_initial_pool_size=int(spec.get("local_search_initial_pool_size", 1)),
        local_search_iterations=int(spec.get("local_search_iterations", 20)),
        local_search_neighbor_limit=int(spec.get("local_search_neighbor_limit", 60)),
        local_search_time_limit_sec=float(spec.get("local_search_time_limit_sec", 2.0)),
        local_search_neighborhood_profiles=list(spec.get("local_search_neighborhood_profiles") or ["random"]),
        local_search_run_profiles=list(spec.get("local_search_run_profiles") or []) or None,
        awls_restarts=int(spec.get("awls_restarts", 2)),
        awls_cycles_per_restart=int(spec.get("awls_cycles_per_restart", 1000)),
        awls_iterations=int(spec.get("awls_iterations", 10000)),
        awls_time_limit_sec=float(spec.get("awls_time_limit_sec", 5.0)),
        awls_init=str(spec.get("awls_init", "random")),
        awls_exact_select_top_k=int(spec.get("awls_exact_select_top_k", 0)),
        awls_beta=int(spec.get("awls_beta", 500)),
        awls_gamma=int(spec.get("awls_gamma", 40)),
        awls_theta=int(spec.get("awls_theta", 5)),
        awls_portfolio_lanes=str(spec.get("awls_portfolio_lanes", "")),
        strategy_candidates=int(spec.get("strategy_candidates", 1)),
        profile_mode=str(spec.get("profile_mode", "template")),
        deepseek_model=str(spec.get("deepseek_model", "deepseek-v4-pro")),
    )


def aggregate_suite_results(suite_results: list[dict[str, Any]]) -> dict[str, Any]:
    total_experiments = 0
    valid_experiments = 0
    failed_experiments = 0
    gap_values: list[float] = []
    for item in suite_results:
        summary = item.get("benchmark_summary") or {}
        total_experiments += int(summary.get("total_experiments", 0) or 0)
        valid_experiments += int(summary.get("valid_experiments", 0) or 0)
        failed_experiments += int(summary.get("failed_experiments", 0) or 0)
        for value in (summary.get("gap_metrics") or {}).values():
            if isinstance(value, (int, float)):
                gap_values.append(float(value))
    return {
        "total_experiments": total_experiments,
        "valid_experiments": valid_experiments,
        "failed_experiments": failed_experiments,
        "suite_status_counts": status_counts(suite_results),
        "gap_suite_count": sum(1 for item in suite_results if (item.get("benchmark_summary") or {}).get("has_best_known_gap")),
        "avg_reported_gap_pct": sum(gap_values) / len(gap_values) if gap_values else None,
        "max_reported_gap_pct": max(gap_values) if gap_values else None,
        "best_reached_metric_count": sum(1 for value in gap_values if value <= 0.0),
        "within_1pct_metric_count": sum(1 for value in gap_values if value <= 1.0),
        "within_2pct_metric_count": sum(1 for value in gap_values if value <= 2.0),
    }


def render_benchmark_suite_report(manifest: dict[str, Any]) -> str:
    aggregate = manifest.get("aggregate") or {}
    lines = [
        "# Standard FJSP Benchmark Suite Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Suites: `{manifest.get('suite_count', 0)}`",
        f"- Total experiments: `{aggregate.get('total_experiments', 0)}`",
        f"- Valid experiments: `{aggregate.get('valid_experiments', 0)}`",
        f"- Failed experiments: `{aggregate.get('failed_experiments', 0)}`",
        f"- Suites with best-known gap: `{aggregate.get('gap_suite_count', 0)}`",
        f"- Average reported gap pct: `{aggregate.get('avg_reported_gap_pct')}`",
        "",
        "## Suite Results",
        "",
        "| Suite | Status | Valid/Total | Best Candidate | Makespan Metrics | Gap Metrics | Report |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for item in manifest.get("suite_results", []):
        summary = item.get("benchmark_summary") or {}
        artifacts = item.get("artifacts") or {}
        valid = summary.get("valid_experiments", 0)
        total = summary.get("total_experiments", 0)
        report = artifacts.get("report", "")
        report_cell = f"[report]({report})" if report else "N/A"
        lines.append(
            f"| {item.get('name')} | {item.get('status')} | {valid}/{total} | "
            f"{summary.get('best_candidate_id') or 'N/A'} | "
            f"`{json.dumps(summary.get('makespan_metrics') or {}, ensure_ascii=False)}` | "
            f"`{json.dumps(summary.get('gap_metrics') or {}, ensure_ascii=False)}` | "
            f"{report_cell} |"
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


def resolve_config_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "suite"


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))
