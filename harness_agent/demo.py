from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .standard_agent import StandardFjspAgentRunner


@dataclass(frozen=True)
class StandardDemoRequest:
    """Configuration for a small end-to-end standard-FJSP harness demo."""

    docs: list[Path]
    instance_dir: Path
    pattern: str
    output_dir: Path
    project_root: Path
    best_known_csv: Path | None = None
    max_instances: int | None = None
    max_rounds: int = 2
    seeds: list[int] | None = None
    timeout_seconds: int = 60
    max_workers: int = 1
    solver: str = "portfolio"
    portfolio_size: int = 16
    local_search_restarts: int = 1
    local_search_initial_pool_size: int = 1
    local_search_iterations: int = 20
    local_search_neighbor_limit: int = 60
    local_search_time_limit_sec: float = 2.0
    local_search_neighborhood_profiles: list[str] | None = None
    local_search_run_profiles: list[dict[str, Any]] | None = None
    awls_restarts: int = 2
    awls_cycles_per_restart: int = 1000
    awls_iterations: int = 10000
    awls_time_limit_sec: float = 5.0
    awls_init: str = "random"
    awls_exact_select_top_k: int = 0
    awls_beta: int = 500
    awls_gamma: int = 40
    awls_theta: int = 5
    strategy_candidates: int = 2
    profile_mode: str = "template"
    deepseek_model: str = "deepseek-v4-pro"


def run_standard_demo(request: StandardDemoRequest) -> dict[str, Any]:
    """Run a compact document-to-evaluator loop and write demo artifacts.

    The demo is intentionally thin: it delegates actual solving and validation to
    the normal standard-FJSP agent and only adds a manifest-level artifact check.
    This keeps the demonstration faithful to the same evaluator path used by
    larger experiments.
    """

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runner = StandardFjspAgentRunner(
        docs=request.docs,
        instance_dir=request.instance_dir,
        pattern=request.pattern,
        output_dir=output_dir / "standard_agent",
        best_known_csv=request.best_known_csv,
        max_instances=request.max_instances,
        max_rounds=max(1, request.max_rounds),
        seeds=request.seeds or [0],
        timeout_seconds=max(1, request.timeout_seconds),
        max_workers=max(1, request.max_workers),
        solver=request.solver,
        portfolio_size=max(1, request.portfolio_size),
        local_search_restarts=max(1, request.local_search_restarts),
        local_search_initial_pool_size=max(1, request.local_search_initial_pool_size),
        local_search_iterations=max(0, request.local_search_iterations),
        local_search_neighbor_limit=max(1, request.local_search_neighbor_limit),
        local_search_time_limit_sec=max(0.1, request.local_search_time_limit_sec),
        local_search_neighborhood_profiles=request.local_search_neighborhood_profiles or ["random"],
        local_search_run_profiles=request.local_search_run_profiles,
        awls_restarts=max(1, request.awls_restarts),
        awls_cycles_per_restart=max(1, request.awls_cycles_per_restart),
        awls_iterations=max(0, request.awls_iterations),
        awls_time_limit_sec=max(0.1, request.awls_time_limit_sec),
        awls_init=request.awls_init,
        awls_exact_select_top_k=max(0, request.awls_exact_select_top_k),
        awls_beta=max(1, request.awls_beta),
        awls_gamma=max(1, request.awls_gamma),
        awls_theta=max(0, request.awls_theta),
        strategy_candidates=max(1, request.strategy_candidates),
        profile_mode=request.profile_mode,
        deepseek_model=request.deepseek_model,
        project_root=request.project_root,
    )
    agent_result = runner.run()
    artifact_checks = verify_standard_demo_artifacts(
        output_dir / "standard_agent",
        rounds=max(1, request.max_rounds),
    )
    manifest = {
        "status": "ok" if not artifact_checks["missing"] else "missing_artifacts",
        "flow": [
            "load requirement documents",
            "generate strategy profile candidates",
            "build evaluator-backed standard FJSP contracts",
            "run solver and fixed evaluator through LangGraph harness",
            "record hypothesis ledger and graph guidance",
            "write demo manifest and report",
        ],
        "request": {
            "docs": [str(path) for path in request.docs],
            "instance_dir": str(request.instance_dir),
            "pattern": request.pattern,
            "max_rounds": max(1, request.max_rounds),
            "seeds": request.seeds or [0],
            "solver": request.solver,
            "profile_mode": request.profile_mode,
            "strategy_candidates": max(1, request.strategy_candidates),
        },
        "agent_result": agent_result,
        "artifact_checks": artifact_checks,
        "benchmark_summary": summarize_benchmark_result(agent_result),
    }
    manifest_path = output_dir / "demo_manifest.json"
    report_path = output_dir / "demo_report.md"
    manifest["artifacts"] = {
        "manifest": str(manifest_path.resolve()),
        "report": str(report_path.resolve()),
        "standard_agent_report": str((output_dir / "standard_agent" / "agent_report.md").resolve()),
        "hypothesis_graph": str((output_dir / "standard_agent" / "hypothesis_graph.md").resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_demo_report(manifest), encoding="utf-8")
    return manifest


def verify_standard_demo_artifacts(agent_dir: Path, *, rounds: int) -> dict[str, Any]:
    required = [
        agent_dir / "agent_report.md",
        agent_dir / "hypotheses.jsonl",
        agent_dir / "hypothesis_graph.json",
        agent_dir / "hypothesis_graph.md",
    ]
    required.extend(agent_dir / f"round_{index:02d}" / "reflection.md" for index in range(rounds))
    missing = [str(path) for path in required if not path.exists()]
    contract_paths = sorted(agent_dir.glob("round_*/candidates/*/contract.json"))
    harness_reports = sorted(agent_dir.glob("round_*/candidates/*/harness/report.md"))
    return {
        "required_count": len(required),
        "missing": missing,
        "contract_count": len(contract_paths),
        "harness_report_count": len(harness_reports),
        "contracts": [str(path) for path in contract_paths[:10]],
        "harness_reports": [str(path) for path in harness_reports[:10]],
    }


def summarize_benchmark_result(agent_result: dict[str, Any]) -> dict[str, Any]:
    """Extract benchmark-facing evidence from the evaluator-backed summary."""

    last_summary = agent_result.get("last_summary") or {}
    best_metrics = dict(last_summary.get("best_metrics") or {})
    best_candidate_metrics = dict(last_summary.get("best_candidate_metrics") or {})
    candidate_summaries = list(last_summary.get("candidate_summaries") or [])
    pareto_frontier = list(last_summary.get("pareto_frontier") or [])
    metrics = {**best_metrics, **best_candidate_metrics}
    gap_metrics = {
        name: value
        for name, value in sorted(metrics.items())
        if "gap_pct" in name and isinstance(value, (int, float))
    }
    best_known_metrics = {
        name: value
        for name, value in sorted(metrics.items())
        if "best_known" in name and isinstance(value, (int, float))
    }
    makespan_metrics = {
        name: value
        for name, value in sorted(metrics.items())
        if "makespan" in name and isinstance(value, (int, float))
    }
    return {
        "total_experiments": int(last_summary.get("total", 0) or 0),
        "valid_experiments": int(last_summary.get("valid", 0) or 0),
        "failed_experiments": int(last_summary.get("failed", 0) or 0),
        "candidate_count": len(candidate_summaries),
        "pareto_count": len(pareto_frontier),
        "best_experiment_id": last_summary.get("best_experiment_id"),
        "best_candidate_id": last_summary.get("best_candidate_id"),
        "makespan_metrics": makespan_metrics,
        "best_known_metrics": best_known_metrics,
        "gap_metrics": gap_metrics,
        "has_best_known_gap": bool(gap_metrics),
    }


def render_demo_report(manifest: dict[str, Any]) -> str:
    checks = manifest.get("artifact_checks", {})
    agent_result = manifest.get("agent_result", {})
    last_summary = agent_result.get("last_summary") or {}
    benchmark = manifest.get("benchmark_summary") or {}
    lines = [
        "# Loop Engineering Demo Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Rounds: `{agent_result.get('rounds')}`",
        f"- Profile source: `{agent_result.get('profile_source')}`",
        f"- Reflection source: `{agent_result.get('reflection_source')}`",
        f"- Best metrics: `{json.dumps(last_summary.get('best_metrics') or {}, ensure_ascii=False)}`",
        f"- Candidate frontier size: `{len(last_summary.get('pareto_frontier') or [])}`",
        f"- Missing required artifacts: `{len(checks.get('missing') or [])}`",
        f"- Generated contracts: `{checks.get('contract_count', 0)}`",
        f"- Harness reports: `{checks.get('harness_report_count', 0)}`",
        f"- Best-known gap available: `{benchmark.get('has_best_known_gap', False)}`",
        f"- Gap metrics: `{json.dumps(benchmark.get('gap_metrics') or {}, ensure_ascii=False)}`",
        "",
        "## Flow",
        "",
    ]
    lines.extend(f"- {item}" for item in manifest.get("flow", []))
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
        ]
    )
    for name, path in (manifest.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{path}`")
    lines.extend(
        [
            "",
            "## Benchmark Summary",
            "",
            f"```json\n{json.dumps(benchmark, ensure_ascii=False, indent=2)}\n```",
        ]
    )
    return "\n".join(lines).strip() + "\n"
