from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .benchmark_suite import BenchmarkSuiteRequest, run_benchmark_suite
from .evidence import EvidenceIndexRequest, build_evidence_index
from .health_check import HealthCheckRequest, run_health_check
from .intent_alignment import IntentAlignmentRequest, write_intent_alignment
from .project_intake import ProjectIntakeRequest, write_project_intake
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
    previous_pipeline_memory: Path | None = None
    run_project_intake: bool = True
    project_intake_max_files: int = 200
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


@dataclass(frozen=True)
class StandardPipelineLoopRequest:
    """Request for running several standard pipeline iterations as one loop."""

    base_request: StandardPipelineRequest
    rounds: int = 2


def run_standard_pipeline(request: StandardPipelineRequest) -> dict[str, Any]:
    """Run benchmark, code-evolution, and evidence-index stages in order."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_dir = output_dir / "benchmark_suite"
    worker_dir = output_dir / "standard_worker_loop"
    evidence_dir = output_dir / "evidence_index"
    intake_dir = output_dir / "project_intake"
    health_dir = output_dir / "health_check"
    intent_dir = output_dir / "intent_alignment"

    intake_manifest = None
    if request.run_project_intake:
        intake_manifest = write_project_intake(
            ProjectIntakeRequest(
                project_root=request.project_root,
                output_dir=intake_dir,
                contract_path=request.health_contract,
                max_files=max(1, request.project_intake_max_files),
            )
        )
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
                project_intake_manifest=Path(str(intake_manifest["artifacts"]["manifest"]))
                if intake_manifest
                else None,
                previous_pipeline_memory=request.previous_pipeline_memory,
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
    if intake_manifest:
        evidence_input_dirs.append(intake_dir)
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
        intake_manifest=intake_manifest,
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
    memory_path = output_dir / "standard_pipeline_memory.json"
    memory_report_path = output_dir / "standard_pipeline_memory.md"
    manifest["artifacts"]["standard_pipeline_memory_json"] = str(memory_path.resolve())
    manifest["artifacts"]["standard_pipeline_memory_markdown"] = str(memory_report_path.resolve())
    memory = standard_pipeline_memory(manifest)
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    memory_report_path.write_text(render_standard_pipeline_memory(memory), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_standard_pipeline_report(manifest), encoding="utf-8")
    return manifest


def run_standard_pipeline_loop(request: StandardPipelineLoopRequest) -> dict[str, Any]:
    """Run standard pipeline rounds while feeding each round's memory to the next."""

    output_dir = request.base_request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rounds = max(1, request.rounds)
    previous_memory = request.base_request.previous_pipeline_memory
    iterations: list[dict[str, Any]] = []

    for index in range(rounds):
        iteration_dir = output_dir / f"iteration_{index:03d}"
        iteration_request = replace(
            request.base_request,
            output_dir=iteration_dir,
            previous_pipeline_memory=previous_memory,
            worker_experiment_id=f"{request.base_request.worker_experiment_id}_iter_{index:03d}",
            title=f"{request.base_request.title} Iteration {index + 1}/{rounds}",
        )
        iteration_manifest = run_standard_pipeline(iteration_request)
        iteration_record = standard_pipeline_loop_iteration(index, previous_memory, iteration_manifest)
        iterations.append(iteration_record)

        memory_path = iteration_record.get("memory_path")
        previous_memory = Path(str(memory_path)) if memory_path else None

    manifest_path = output_dir / "standard_pipeline_loop_manifest.json"
    report_path = output_dir / "standard_pipeline_loop_report.md"
    loop_manifest = standard_pipeline_loop_manifest(
        request=request,
        iterations=iterations,
        output_dir=output_dir,
        manifest_path=manifest_path,
        report_path=report_path,
    )
    manifest_path.write_text(json.dumps(loop_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_standard_pipeline_loop_report(loop_manifest), encoding="utf-8")
    return loop_manifest


def standard_pipeline_loop_iteration(
    index: int,
    previous_memory: Path | None,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Create a compact record for one chained standard-pipeline iteration."""

    artifacts = manifest.get("artifacts") or {}
    worker = manifest.get("standard_worker_loop") or {}
    memory_path = artifacts.get("standard_pipeline_memory_json")
    memory = read_json_if_exists(Path(str(memory_path))) if memory_path else {}
    return {
        "iteration_index": index,
        "status": manifest.get("status"),
        "stage_status": manifest.get("stage_status") or {},
        "input_previous_memory": str(previous_memory.resolve()) if previous_memory else None,
        "manifest": artifacts.get("manifest"),
        "report": artifacts.get("report"),
        "memory_path": memory_path,
        "benchmark_signal": compact_benchmark_signal(manifest.get("benchmark_suite") or {}),
        "worker_signal": {
            "baseline_key": worker.get("baseline_key"),
            "final_key": worker.get("final_key"),
            "improved": worker.get("improved"),
            "round_count": worker.get("round_count", 0),
            "promoted_rounds": worker.get("promoted_rounds", 0),
        },
        "recommendations": memory.get("recommendations") or [],
    }


def standard_pipeline_loop_manifest(
    *,
    request: StandardPipelineLoopRequest,
    iterations: list[dict[str, Any]],
    output_dir: Path,
    manifest_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    final_iteration = iterations[-1] if iterations else {}
    status = "ok" if iterations and all(item.get("status") == "ok" for item in iterations) else "partial_failed"
    return {
        "schema_version": 1,
        "status": status,
        "round_count": len(iterations),
        "request": {
            "loop_rounds": max(1, request.rounds),
            "initial_previous_memory": str(request.base_request.previous_pipeline_memory)
            if request.base_request.previous_pipeline_memory
            else None,
            "suite_config": str(request.base_request.suite_config),
            "worker": request.base_request.worker.capabilities().__dict__,
            "worker_instance_dir": str(request.base_request.worker_instance_dir),
            "worker_pattern": request.base_request.worker_pattern,
            "worker_solver": request.base_request.worker_solver,
        },
        "iterations": iterations,
        "final": {
            "status": final_iteration.get("status"),
            "manifest": final_iteration.get("manifest"),
            "report": final_iteration.get("report"),
            "memory_path": final_iteration.get("memory_path"),
            "benchmark_signal": final_iteration.get("benchmark_signal") or {},
            "worker_signal": final_iteration.get("worker_signal") or {},
            "recommendations": final_iteration.get("recommendations") or [],
        },
        "artifacts": {
            "manifest": str(manifest_path.resolve()),
            "report": str(report_path.resolve()),
            "final_memory": final_iteration.get("memory_path"),
            "final_pipeline_manifest": final_iteration.get("manifest"),
            "final_pipeline_report": final_iteration.get("report"),
            "iteration_manifests": [item.get("manifest") for item in iterations if item.get("manifest")],
            "iteration_reports": [item.get("report") for item in iterations if item.get("report")],
        },
        "output_dir": str(output_dir.resolve()),
    }


def standard_pipeline_manifest(
    *,
    request: StandardPipelineRequest,
    intake_manifest: dict[str, Any] | None,
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
    status = pipeline_status(
        intake_manifest,
        health_manifest,
        intent_manifest,
        admission_passed,
        suite_manifest,
        worker_manifest,
        evidence_index,
    )
    return {
        "status": status,
        "request": {
            "suite_config": str(request.suite_config),
            "max_suites": request.max_suites,
            "run_project_intake": bool(request.run_project_intake),
            "project_intake_max_files": max(1, request.project_intake_max_files),
            "health_contract": str(request.health_contract) if request.health_contract else None,
            "health_repeats": max(1, request.health_repeats),
            "benchmark_source": request.benchmark_source,
            "worker_docs": [str(path) for path in request.worker_docs],
            "previous_pipeline_memory": str(request.previous_pipeline_memory) if request.previous_pipeline_memory else None,
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
            "project_intake": intake_manifest.get("status") if intake_manifest else "skipped",
            "health_check": health_manifest.get("status") if health_manifest else None,
            "intent_alignment": intent_manifest.get("status") if intent_manifest else None,
            "benchmark_suite": suite_manifest.get("status") if suite_manifest else "skipped_admission_gate",
            "standard_worker_loop": worker_manifest.get("status") if worker_manifest else "skipped_admission_gate",
            "evidence_index_entries": evidence_index.get("entry_count", 0),
            "missing_artifact_count": evidence_summary.get("missing_artifact_count", 0),
        },
        "project_intake": {
            "status": intake_manifest.get("status") if intake_manifest else None,
            "language_summary": intake_manifest.get("language_summary") if intake_manifest else {},
            "risk_flags": intake_manifest.get("risk_flags") if intake_manifest else [],
            "artifacts": (intake_manifest.get("artifacts") if intake_manifest else {}),
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
            "rounds": worker_manifest.get("rounds", []) if worker_manifest else [],
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
            "project_intake_manifest": str((output_dir / "project_intake" / "project_intake_manifest.json").resolve())
            if intake_manifest
            else None,
            "project_intake_report": str((output_dir / "project_intake" / "project_intake_report.md").resolve())
            if intake_manifest
            else None,
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


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def pipeline_status(
    intake_manifest: dict[str, Any] | None,
    health_manifest: dict[str, Any] | None,
    intent_manifest: dict[str, Any] | None,
    admission_passed: bool,
    suite_manifest: dict[str, Any] | None,
    worker_manifest: dict[str, Any] | None,
    evidence_index: dict[str, Any],
) -> str:
    evidence_summary = evidence_index.get("summary") or {}
    if intake_manifest and intake_manifest.get("status") != "ok":
        return "partial_failed"
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
    intake = manifest.get("project_intake") or {}
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
        f"- Project-intake status: `{stage_status.get('project_intake')}`",
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
        f"| Project intake | `{json.dumps(compact_intake_summary(intake), ensure_ascii=False)}` |",
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


def render_standard_pipeline_loop_report(manifest: dict[str, Any]) -> str:
    final = manifest.get("final") or {}
    lines = [
        "# Standard FJSP Pipeline Loop Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Rounds: `{manifest.get('round_count', 0)}`",
        f"- Final memory: `{final.get('memory_path')}`",
        "",
        "## Iterations",
        "",
        "| Iteration | Status | Previous Memory | Avg Gap % | Worker Promoted | Worker Improved | Memory |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in manifest.get("iterations") or []:
        benchmark = item.get("benchmark_signal") or {}
        worker = item.get("worker_signal") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.get('iteration_index')}`",
                    f"`{item.get('status')}`",
                    f"`{short_path(item.get('input_previous_memory'))}`",
                    f"`{benchmark.get('avg_reported_gap_pct')}`",
                    f"`{worker.get('promoted_rounds', 0)}/{worker.get('round_count', 0)}`",
                    f"`{worker.get('improved')}`",
                    f"`{short_path(item.get('memory_path'))}`",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Final Recommendations",
            "",
        ]
    )
    for item in final.get("recommendations") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Artifacts", ""])
    for name, path in (manifest.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{path}`")
    lines.extend(
        [
            "",
            "Each iteration is a full standard pipeline run.  The next iteration receives only the previous round's compact memory artifact; evaluator and promotion decisions remain owned by the referenced per-iteration manifests.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def short_path(value: object) -> str:
    if not value:
        return ""
    text = str(value)
    parts = Path(text).parts
    return str(Path(*parts[-3:])) if len(parts) > 3 else text


def standard_pipeline_memory(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build compact machine-readable memory for the next loop iteration.

    This artifact is intentionally derived from fixed-stage manifests.  It gives
    future worker prompts a small evidence packet without creating a second
    source of truth for acceptance decisions.
    """

    benchmark = manifest.get("benchmark_suite") or {}
    worker = manifest.get("standard_worker_loop") or {}
    evidence = manifest.get("evidence_index") or {}
    stage_status = manifest.get("stage_status") or {}
    rounds = compact_worker_rounds(worker)
    memory = {
        "schema_version": 1,
        "purpose": "Compact evidence packet for the next standard-FJSP loop-engineering iteration.",
        "pipeline_status": manifest.get("status"),
        "stage_status": stage_status,
        "admission": {
            "gate": stage_status.get("admission_gate"),
            "health_check": stage_status.get("health_check"),
            "intent_alignment": stage_status.get("intent_alignment"),
            "ready_for_optimization": (manifest.get("intent_alignment") or {}).get("ready_for_optimization"),
            "blockers": (manifest.get("intent_alignment") or {}).get("blockers") or [],
            "warnings": (manifest.get("intent_alignment") or {}).get("warnings") or [],
        },
        "benchmark_signal": compact_benchmark_signal(benchmark),
        "worker_signal": {
            "baseline_key": worker.get("baseline_key"),
            "final_key": worker.get("final_key"),
            "improved": worker.get("improved"),
            "round_count": worker.get("round_count", 0),
            "promoted_rounds": worker.get("promoted_rounds", 0),
            "rounds": rounds,
        },
        "evidence_signal": {
            "entry_count": evidence.get("entry_count"),
            "summary": evidence.get("summary") or {},
        },
        "recommendations": standard_pipeline_recommendations(manifest=manifest, compact_rounds=rounds),
        "artifacts": {
            key: value
            for key, value in (manifest.get("artifacts") or {}).items()
            if key
            in {
                "manifest",
                "report",
                "benchmark_suite_manifest",
                "benchmark_suite_report",
                "standard_worker_loop_manifest",
                "standard_worker_loop_report",
                "evidence_index_json",
                "evidence_index_markdown",
            }
        },
    }
    return memory


def compact_benchmark_signal(benchmark: dict[str, Any]) -> dict[str, Any]:
    aggregate = benchmark.get("aggregate") or {}
    return {
        "suite_count": benchmark.get("suite_count", 0),
        "total_experiments": aggregate.get("total_experiments"),
        "valid_experiments": aggregate.get("valid_experiments"),
        "failed_experiments": aggregate.get("failed_experiments"),
        "avg_reported_gap_pct": aggregate.get("avg_reported_gap_pct"),
        "gap_suite_count": aggregate.get("gap_suite_count"),
    }


def compact_worker_rounds(worker: dict[str, Any]) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    for item in worker.get("rounds") or []:
        diagnostics = item.get("proposal_diagnostics") or {}
        audit = diagnostics.get("proposal_audit") if isinstance(diagnostics, dict) else {}
        if not isinstance(audit, dict):
            audit = {}
        context_usage = diagnostics.get("context_usage") if isinstance(diagnostics, dict) else {}
        if not isinstance(context_usage, dict):
            context_usage = {}
        rounds.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "worker_status": item.get("worker_status"),
                "duplicate_proposal": item.get("duplicate_proposal"),
                "candidate_key": item.get("candidate_key"),
                "incumbent_key_after": item.get("incumbent_key_after"),
                "changed_files": item.get("worker_changed_files") or [],
                "proposal_diagnostics": {
                    "status": diagnostics.get("status") if isinstance(diagnostics, dict) else None,
                    "used_project_intake": context_usage.get("used_project_intake"),
                    "changed_core_algorithm_files": audit.get("changed_core_algorithm_files") or [],
                    "changed_validator_files": audit.get("changed_validator_files") or [],
                    "changed_benchmark_files": audit.get("changed_benchmark_files") or [],
                    "warnings": audit.get("warnings") or [],
                },
            }
        )
    return rounds


def standard_pipeline_recommendations(*, manifest: dict[str, Any], compact_rounds: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    stage_status = manifest.get("stage_status") or {}
    benchmark_signal = compact_benchmark_signal(manifest.get("benchmark_suite") or {})
    worker = manifest.get("standard_worker_loop") or {}
    evidence_summary = (manifest.get("evidence_index") or {}).get("summary") or {}

    if stage_status.get("admission_gate") != "passed":
        recommendations.append("Resolve admission blockers before spending worker budget on solver evolution.")
    if int(stage_status.get("missing_artifact_count", 0) or 0) > 0:
        recommendations.append("Repair missing referenced artifacts so future evidence indexes remain reproducible.")

    avg_gap = benchmark_signal.get("avg_reported_gap_pct")
    if isinstance(avg_gap, (int, float)) and avg_gap > 0:
        recommendations.append(
            "Use benchmark gap evidence to focus the next strategy on makespan quality rather than only feasibility."
        )

    if int(worker.get("round_count", 0) or 0) > 0 and int(worker.get("promoted_rounds", 0) or 0) == 0:
        recommendations.append(
            "No worker round was promoted; require the next proposal to explain a materially different rule or operator."
        )

    if any(item.get("duplicate_proposal") for item in compact_rounds):
        recommendations.append("Duplicate proposal fingerprints were observed; enforce stronger candidate diversity.")

    proposal_warnings = [
        warning
        for item in compact_rounds
        for warning in (item.get("proposal_diagnostics") or {}).get("warnings", [])
    ]
    if proposal_warnings:
        recommendations.append(
            "Address proposal-audit warnings before promotion attempts: "
            + ", ".join(sorted({str(item) for item in proposal_warnings}))
        )

    if int(evidence_summary.get("valid_experiments", 0) or 0) < int(evidence_summary.get("total_experiments", 0) or 0):
        recommendations.append("Investigate invalid experiments before using quality metrics for strong claims.")

    return recommendations or ["Continue with a small, evaluator-backed solver improvement proposal."]


def render_standard_pipeline_memory(memory: dict[str, Any]) -> str:
    lines = [
        "# Standard FJSP Pipeline Memory",
        "",
        f"- Pipeline status: `{memory.get('pipeline_status')}`",
        f"- Stage status: `{json.dumps(memory.get('stage_status') or {}, ensure_ascii=False)}`",
        f"- Benchmark signal: `{json.dumps(memory.get('benchmark_signal') or {}, ensure_ascii=False)}`",
        f"- Worker signal: `{json.dumps(memory.get('worker_signal') or {}, ensure_ascii=False)}`",
        "",
        "## Recommendations",
        "",
    ]
    for item in memory.get("recommendations") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Artifacts", ""])
    for name, path in (memory.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{path}`")
    return "\n".join(lines).strip() + "\n"


def compact_intake_summary(intake: dict[str, Any]) -> dict[str, Any]:
    if not intake or intake.get("status") is None:
        return {"enabled": False}
    language = intake.get("language_summary") or {}
    return {
        "enabled": True,
        "status": intake.get("status"),
        "primary_language": language.get("primary_language"),
        "risk_count": len(intake.get("risk_flags") or []),
    }


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
