from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .benchmark_suite import BenchmarkSuiteRequest, run_benchmark_suite
from .evidence import EvidenceIndexRequest, build_evidence_index
from .health_check import HealthCheckRequest, run_health_check
from .intent_alignment import IntentAlignmentRequest, write_intent_alignment
from .standard_worker_loop import StandardWorkerLoopRequest, run_standard_worker_loop
from .worker import CodingWorker


@dataclass(frozen=True)
class StandardPipelineRequest:
    """Request for the full standard-FJSP loop-engineering smoke pipeline."""

    suite_config: Path
    output_dir: Path
    project_root: Path
    worker: CodingWorker
    worker_docs: list[Path]
    worker_instance_dir: Path
    health_contract: Path | None = None
    health_repeats: int = 2
    health_max_instances: int = 1
    health_max_seeds: int = 1
    health_allow_draft: bool = False
    worker_pattern: str = "*.txt"
    worker_best_known_csv: Path | None = None
    worker_knowledge_cards: list[Path] | None = None
    benchmark_source: str = "user_provided"
    require_intent_alignment: bool = True
    max_suites: int | None = None
    worker_max_instances: int | None = None
    worker_seeds: list[int] | None = None
    worker_timeout_seconds: int = 60
    worker_max_workers: int = 1
    worker_solver: str = "portfolio"
    worker_portfolio_size: int = 16
    worker_local_search_restarts: int = 1
    worker_local_search_initial_pool_size: int = 1
    worker_local_search_iterations: int = 40
    worker_local_search_neighbor_limit: int = 100
    worker_local_search_time_limit_sec: float = 2.0
    worker_local_search_neighborhood_profile: str = "combined"
    worker_iterations: int = 1
    worker_max_steps: int = 4
    worker_max_runtime_seconds: int = 120
    worker_apply_changes: bool = False
    worker_experiment_id: str = "standard_pipeline_worker_loop"
    worker_hypothesis: str = (
        "Improve the standard FJSP solver under the fixed evaluator. "
        "State the rule-level idea before editing code."
    )
    title: str = "Standard FJSP Loop Pipeline Evidence"


def run_standard_pipeline(request: StandardPipelineRequest) -> dict[str, Any]:
    """Run benchmark, code-evolution, and evidence-index stages in order."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_dir = output_dir / "benchmark_suite"
    worker_dir = output_dir / "standard_worker_loop"
    evidence_dir = output_dir / "evidence_index"
    health_dir = output_dir / "health_check"
    intent_dir = output_dir / "intent_alignment"

    health_manifest = None
    intent_manifest = None
    if request.health_contract:
        health_manifest = run_health_check(
            HealthCheckRequest(
                contract_path=request.health_contract,
                output_dir=health_dir,
                project_root=request.project_root,
                repeats=request.health_repeats,
                max_instances=request.health_max_instances,
                max_seeds=request.health_max_seeds,
                allow_draft=request.health_allow_draft,
            )
        )
    if request.health_contract and request.require_intent_alignment:
        intent_manifest = write_intent_alignment(
            IntentAlignmentRequest(
                contract_path=request.health_contract,
                output_dir=intent_dir,
                project_root=request.project_root,
                health_manifest_path=Path(str(health_manifest["artifacts"]["manifest"])),
                benchmark_source=request.benchmark_source,
                allow_draft=request.health_allow_draft,
                require_health=True,
            )
        )
    admission_passed = admission_gate_passed(health_manifest=health_manifest, intent_manifest=intent_manifest)
    suite_manifest = None
    worker_manifest = None
    if admission_passed:
        suite_manifest = run_benchmark_suite(
            BenchmarkSuiteRequest(
                config_path=request.suite_config,
                output_dir=suite_dir,
                project_root=request.project_root,
                max_suites=request.max_suites,
            )
        )
        worker_manifest = run_standard_worker_loop(
            StandardWorkerLoopRequest(
                docs=request.worker_docs,
                knowledge_cards=request.worker_knowledge_cards or [],
                instance_dir=request.worker_instance_dir,
                pattern=request.worker_pattern,
                output_dir=worker_dir,
                project_root=request.project_root,
                worker=request.worker,
                best_known_csv=request.worker_best_known_csv,
                max_instances=request.worker_max_instances,
                seeds=request.worker_seeds or [0],
                timeout_seconds=max(1, request.worker_timeout_seconds),
                max_workers=max(1, request.worker_max_workers),
                solver=request.worker_solver,
                portfolio_size=max(1, request.worker_portfolio_size),
                local_search_restarts=max(1, request.worker_local_search_restarts),
                local_search_initial_pool_size=max(1, request.worker_local_search_initial_pool_size),
                local_search_iterations=max(0, request.worker_local_search_iterations),
                local_search_neighbor_limit=max(1, request.worker_local_search_neighbor_limit),
                local_search_time_limit_sec=max(0.1, request.worker_local_search_time_limit_sec),
                local_search_neighborhood_profile=request.worker_local_search_neighborhood_profile,
                iterations=max(0, request.worker_iterations),
                max_steps=max(1, request.worker_max_steps),
                max_runtime_seconds=max(1, request.worker_max_runtime_seconds),
                apply_worker_changes=bool(request.worker_apply_changes),
                experiment_id=request.worker_experiment_id,
                hypothesis=request.worker_hypothesis,
            )
        )
    evidence_input_dirs = []
    if health_manifest:
        evidence_input_dirs.append(health_dir)
    if intent_manifest:
        evidence_input_dirs.append(intent_dir)
    if suite_manifest:
        evidence_input_dirs.append(suite_dir)
    if worker_manifest:
        evidence_input_dirs.append(worker_dir)
    evidence_index = build_evidence_index(
        EvidenceIndexRequest(
            input_dirs=evidence_input_dirs,
            output_dir=evidence_dir,
            title=request.title,
        )
    )

    manifest_path = output_dir / "standard_pipeline_manifest.json"
    report_path = output_dir / "standard_pipeline_report.md"
    manifest = standard_pipeline_manifest(
        request=request,
        health_manifest=health_manifest,
        intent_manifest=intent_manifest,
        admission_passed=admission_passed,
        suite_manifest=suite_manifest,
        worker_manifest=worker_manifest,
        evidence_index=evidence_index,
        output_dir=output_dir,
        manifest_path=manifest_path,
        report_path=report_path,
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_standard_pipeline_report(manifest), encoding="utf-8")
    return manifest


def standard_pipeline_manifest(
    *,
    request: StandardPipelineRequest,
    health_manifest: dict[str, Any] | None,
    intent_manifest: dict[str, Any] | None,
    admission_passed: bool,
    suite_manifest: dict[str, Any] | None,
    worker_manifest: dict[str, Any] | None,
    evidence_index: dict[str, Any],
    output_dir: Path,
    manifest_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    evidence_summary = evidence_index.get("summary") or {}
    status = pipeline_status(health_manifest, intent_manifest, admission_passed, suite_manifest, worker_manifest, evidence_index)
    return {
        "status": status,
        "request": {
            "suite_config": str(request.suite_config),
            "max_suites": request.max_suites,
            "health_contract": str(request.health_contract) if request.health_contract else None,
            "health_repeats": max(1, request.health_repeats),
            "benchmark_source": request.benchmark_source,
            "worker_docs": [str(path) for path in request.worker_docs],
            "worker_instance_dir": str(request.worker_instance_dir),
            "worker_pattern": request.worker_pattern,
            "worker_best_known_csv": str(request.worker_best_known_csv) if request.worker_best_known_csv else None,
            "worker_seeds": request.worker_seeds or [0],
            "worker_solver": request.worker_solver,
            "worker_iterations": max(0, request.worker_iterations),
            "worker_apply_changes": bool(request.worker_apply_changes),
        },
        "stage_status": {
            "admission_gate": "passed" if admission_passed else "blocked",
            "health_check": health_manifest.get("status") if health_manifest else None,
            "intent_alignment": intent_manifest.get("status") if intent_manifest else None,
            "benchmark_suite": suite_manifest.get("status") if suite_manifest else "skipped_admission_gate",
            "standard_worker_loop": worker_manifest.get("status") if worker_manifest else "skipped_admission_gate",
            "evidence_index_entries": evidence_index.get("entry_count", 0),
            "missing_artifact_count": evidence_summary.get("missing_artifact_count", 0),
        },
        "health_check": {
            "status": health_manifest.get("status") if health_manifest else None,
            "quick_test": (health_manifest.get("quick_test") if health_manifest else None),
            "stability_probe": (health_manifest.get("stability_probe") if health_manifest else None),
            "artifacts": (health_manifest.get("artifacts") if health_manifest else {}),
        },
        "intent_alignment": {
            "status": intent_manifest.get("status") if intent_manifest else None,
            "ready_for_optimization": intent_manifest.get("ready_for_optimization") if intent_manifest else None,
            "blockers": intent_manifest.get("blockers") if intent_manifest else [],
            "warnings": intent_manifest.get("warnings") if intent_manifest else [],
            "artifacts": (intent_manifest.get("artifacts") if intent_manifest else {}),
        },
        "benchmark_suite": {
            "suite_count": suite_manifest.get("suite_count", 0) if suite_manifest else 0,
            "aggregate": suite_manifest.get("aggregate") if suite_manifest else {},
            "artifacts": suite_manifest.get("artifacts") if suite_manifest else {},
        },
        "standard_worker_loop": {
            "baseline_key": worker_manifest.get("baseline_key") if worker_manifest else None,
            "final_key": worker_manifest.get("final_key") if worker_manifest else None,
            "improved": worker_manifest.get("improved") if worker_manifest else None,
            "round_count": worker_manifest.get("round_count", 0) if worker_manifest else 0,
            "promoted_rounds": worker_manifest.get("promoted_rounds", 0) if worker_manifest else 0,
            "artifacts": worker_manifest.get("artifacts") if worker_manifest else {},
        },
        "evidence_index": {
            "entry_count": evidence_index.get("entry_count", 0),
            "summary": evidence_summary,
            "artifacts": evidence_index.get("artifacts") or {},
        },
        "artifacts": {
            "manifest": str(manifest_path.resolve()),
            "report": str(report_path.resolve()),
            "health_check_manifest": str((output_dir / "health_check" / "health_check_manifest.json").resolve())
            if health_manifest
            else None,
            "health_check_report": str((output_dir / "health_check" / "health_check_report.md").resolve())
            if health_manifest
            else None,
            "intent_alignment_manifest": str((output_dir / "intent_alignment" / "intent_alignment_manifest.json").resolve())
            if intent_manifest
            else None,
            "intent_alignment_report": str((output_dir / "intent_alignment" / "intent_alignment_report.md").resolve())
            if intent_manifest
            else None,
            "benchmark_suite_manifest": str((output_dir / "benchmark_suite" / "suite_manifest.json").resolve())
            if suite_manifest
            else None,
            "benchmark_suite_report": str((output_dir / "benchmark_suite" / "suite_report.md").resolve())
            if suite_manifest
            else None,
            "standard_worker_loop_manifest": str(
                (output_dir / "standard_worker_loop" / "standard_worker_loop_manifest.json").resolve()
            )
            if worker_manifest
            else None,
            "standard_worker_loop_report": str(
                (output_dir / "standard_worker_loop" / "standard_worker_loop_report.md").resolve()
            )
            if worker_manifest
            else None,
            "evidence_index_json": str((output_dir / "evidence_index" / "evidence_index.json").resolve()),
            "evidence_index_markdown": str((output_dir / "evidence_index" / "evidence_index.md").resolve()),
        },
    }


def pipeline_status(
    health_manifest: dict[str, Any] | None,
    intent_manifest: dict[str, Any] | None,
    admission_passed: bool,
    suite_manifest: dict[str, Any] | None,
    worker_manifest: dict[str, Any] | None,
    evidence_index: dict[str, Any],
) -> str:
    evidence_summary = evidence_index.get("summary") or {}
    if not admission_passed:
        return "partial_failed"
    if health_manifest and health_manifest.get("status") != "ok":
        return "partial_failed"
    if intent_manifest and intent_manifest.get("status") != "ready":
        return "partial_failed"
    if not suite_manifest or suite_manifest.get("status") != "ok":
        return "partial_failed"
    if not worker_manifest or worker_manifest.get("status") != "ok":
        return "partial_failed"
    if int(evidence_index.get("entry_count", 0) or 0) < 2:
        return "partial_failed"
    if int(evidence_summary.get("missing_artifact_count", 0) or 0) > 0:
        return "partial_failed"
    return "ok"


def render_standard_pipeline_report(manifest: dict[str, Any]) -> str:
    stage_status = manifest.get("stage_status") or {}
    health = manifest.get("health_check") or {}
    intent = manifest.get("intent_alignment") or {}
    benchmark = manifest.get("benchmark_suite") or {}
    worker = manifest.get("standard_worker_loop") or {}
    evidence = manifest.get("evidence_index") or {}
    lines = [
        "# Standard FJSP Loop Pipeline Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Admission gate: `{stage_status.get('admission_gate')}`",
        f"- Health-check status: `{stage_status.get('health_check')}`",
        f"- Intent-alignment status: `{stage_status.get('intent_alignment')}`",
        f"- Benchmark suite status: `{stage_status.get('benchmark_suite')}`",
        f"- Worker-loop status: `{stage_status.get('standard_worker_loop')}`",
        f"- Evidence entries: `{stage_status.get('evidence_index_entries', 0)}`",
        f"- Missing referenced artifacts: `{stage_status.get('missing_artifact_count', 0)}`",
        "",
        "## Stage Summary",
        "",
        "| Stage | Key Evidence |",
        "| --- | --- |",
        f"| Health check | `{json.dumps(compact_health_summary(health), ensure_ascii=False)}` |",
        f"| Intent alignment | `{json.dumps(compact_intent_summary(intent), ensure_ascii=False)}` |",
        f"| Benchmark suite | `{json.dumps(benchmark.get('aggregate') or {}, ensure_ascii=False)}` |",
        f"| Coding-worker loop | baseline `{json.dumps(worker.get('baseline_key'), ensure_ascii=False)}`, "
        f"final `{json.dumps(worker.get('final_key'), ensure_ascii=False)}`, "
        f"promoted `{worker.get('promoted_rounds', 0)}`/`{worker.get('round_count', 0)}` |",
        f"| Evidence index | `{json.dumps(evidence.get('summary') or {}, ensure_ascii=False)}` |",
        "",
        "## Artifacts",
        "",
    ]
    for name, path in (manifest.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{path}`")
    lines.extend(
        [
            "",
            "The pipeline is orchestration glue.  Benchmark quality, worker promotion, and evidence completeness remain decided by the fixed evaluator-backed components that produced the referenced manifests.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def compact_health_summary(health: dict[str, Any]) -> dict[str, Any]:
    if not health or health.get("status") is None:
        return {"enabled": False}
    probe = health.get("stability_probe") or {}
    return {
        "enabled": True,
        "status": health.get("status"),
        "quick_test_status": (health.get("quick_test") or {}).get("status"),
        "stability_status": probe.get("status"),
        "stable": probe.get("stable"),
        "valid": probe.get("valid"),
        "total": probe.get("total"),
    }


def compact_intent_summary(intent: dict[str, Any]) -> dict[str, Any]:
    if not intent or intent.get("status") is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "status": intent.get("status"),
        "ready_for_optimization": intent.get("ready_for_optimization"),
        "blockers": intent.get("blockers") or [],
        "warnings": intent.get("warnings") or [],
    }


def admission_gate_passed(
    *,
    health_manifest: dict[str, Any] | None,
    intent_manifest: dict[str, Any] | None,
) -> bool:
    if health_manifest and health_manifest.get("status") != "ok":
        return False
    if intent_manifest and intent_manifest.get("status") != "ready":
        return False
    return True
