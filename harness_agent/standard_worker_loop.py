from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context_packet import ContextPacketRequest, write_context_packet
from .loop_runner import WorkerLoopResult, compact_proposal_audit, run_worker_loop, summary_payload
from .models import TaskContract
from .worker import CodingWorker


@dataclass(frozen=True)
class StandardWorkerLoopRequest:
    """High-level request for a standard-FJSP code-evolution loop."""

    docs: list[Path]
    instance_dir: Path
    pattern: str
    output_dir: Path
    project_root: Path
    worker: CodingWorker
    best_known_csv: Path | None = None
    knowledge_cards: list[Path] | None = None
    project_intake_manifest: Path | None = None
    max_instances: int | None = None
    seeds: list[int] | None = None
    timeout_seconds: int = 60
    max_workers: int = 1
    solver: str = "portfolio"
    portfolio_size: int = 16
    local_search_restarts: int = 1
    local_search_initial_pool_size: int = 1
    local_search_iterations: int = 40
    local_search_neighbor_limit: int = 100
    local_search_time_limit_sec: float = 2.0
    local_search_neighborhood_profile: str = "combined"
    iterations: int = 1
    max_steps: int = 4
    max_runtime_seconds: int = 120
    apply_worker_changes: bool = False
    experiment_id: str = "standard_worker_loop"
    hypothesis: str = (
        "Improve the standard FJSP solver under the fixed evaluator. "
        "State the rule-level idea before editing code."
    )


def run_standard_worker_loop(request: StandardWorkerLoopRequest) -> dict[str, Any]:
    """Build a standard-FJSP contract, context packet, and coding-worker loop."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contract_path = output_dir / "standard_worker_contract.json"
    context_path = output_dir / "context_packet.json"
    contract_payload = build_standard_worker_contract_payload(request)
    contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contract = TaskContract.load(contract_path)
    errors = contract.validate(request.project_root)
    if errors:
        raise ValueError(f"generated standard worker contract is invalid: {errors}")

    write_context_packet(
        ContextPacketRequest(
            contract_path=contract_path,
            output_path=context_path,
            docs=request.docs,
            knowledge_cards=request.knowledge_cards or [],
            project_intake_manifest=request.project_intake_manifest,
            hypothesis=request.hypothesis,
        )
    )
    loop_result = run_worker_loop(
        contract=contract,
        project_root=request.project_root,
        output_dir=output_dir / "worker_loop",
        context_packet_path=context_path,
        worker=request.worker,
        experiment_id=request.experiment_id,
        iterations=max(0, request.iterations),
        max_steps=max(1, request.max_steps),
        max_runtime_seconds=max(1, request.max_runtime_seconds),
        apply_worker_changes=bool(request.apply_worker_changes),
    )
    manifest = standard_worker_manifest(
        request=request,
        contract_path=contract_path,
        context_path=context_path,
        loop_result=loop_result,
        output_dir=output_dir,
    )
    manifest_path = output_dir / "standard_worker_loop_manifest.json"
    report_path = output_dir / "standard_worker_loop_report.md"
    manifest["artifacts"] = {
        "manifest": str(manifest_path.resolve()),
        "report": str(report_path.resolve()),
        "contract": str(contract_path.resolve()),
        "context_packet": str(context_path.resolve()),
        "loop_report": str((output_dir / "worker_loop" / "loop_report.md").resolve()),
        "loop_result": str((output_dir / "worker_loop" / "loop_result.json").resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_standard_worker_report(manifest), encoding="utf-8")
    return manifest


def build_standard_worker_contract_payload(request: StandardWorkerLoopRequest) -> dict[str, Any]:
    instance_dir = resolve_input_path(request.project_root, request.instance_dir)
    paths = sorted(instance_dir.glob(request.pattern))
    if request.max_instances is not None:
        paths = paths[: request.max_instances]
    if not paths:
        raise FileNotFoundError(f"no standard FJSP instances matched {instance_dir / request.pattern}")

    resources: dict[str, str] = {}
    solver = standard_solver_command(request)
    evaluator = "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
    if request.best_known_csv:
        best_known_csv = resolve_input_path(request.project_root, request.best_known_csv)
        resources["best_known_csv"] = str(best_known_csv)
        evaluator += " --best-known-csv {best_known_csv}"

    return {
        "task_id": request.experiment_id,
        "problem_family": "FJSP",
        "description": "Standard-FJSP coding-worker loop contract generated by the harness.",
        "instances": [{"id": path.stem, "path": str(path)} for path in paths],
        "objectives": [
            {
                "name": "makespan",
                "direction": "minimize",
                "priority": 1,
                "invalid_if_missing": True,
            }
        ],
        "commands": {
            "solver": solver,
            "evaluator": evaluator,
            "quick_test": "python -m compileall harness_agent examples",
        },
        "budget": {
            "rounds": 1,
            "seeds": request.seeds or [0],
            "timeout_seconds": max(1, request.timeout_seconds),
            "max_workers": max(1, request.max_workers),
        },
        "paths": {
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "resources": resources,
        "review": {
            "status": "confirmed",
            "note": "Generated from standard FJSP harness parameters; fixed evaluator remains authoritative.",
        },
    }


def standard_solver_command(request: StandardWorkerLoopRequest) -> str:
    if request.solver == "portfolio":
        return (
            "python examples/standard_fjsp_portfolio_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {max(1, request.portfolio_size)}"
        )
    if request.solver == "local-search":
        return (
            "python examples/standard_fjsp_local_search_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {max(1, request.portfolio_size)} "
            f"--restarts {max(1, request.local_search_restarts)} "
            f"--initial-pool-size {max(1, request.local_search_initial_pool_size)} "
            f"--iterations {max(0, request.local_search_iterations)} "
            f"--neighbor-limit {max(1, request.local_search_neighbor_limit)} "
            f"--time-limit-sec {max(0.1, request.local_search_time_limit_sec)} "
            f"--neighborhood-profile {request.local_search_neighborhood_profile}"
        )
    raise ValueError(f"unknown standard worker solver: {request.solver}")


def standard_worker_manifest(
    *,
    request: StandardWorkerLoopRequest,
    contract_path: Path,
    context_path: Path,
    loop_result: WorkerLoopResult,
    output_dir: Path,
) -> dict[str, Any]:
    promoted_rounds = sum(1 for item in loop_result.rounds if item.decision == "promoted")
    return {
        "status": "ok",
        "request": {
            "docs": [str(path) for path in request.docs],
            "instance_dir": str(request.instance_dir),
            "pattern": request.pattern,
            "best_known_csv": str(request.best_known_csv) if request.best_known_csv else None,
            "project_intake_manifest": str(request.project_intake_manifest) if request.project_intake_manifest else None,
            "seeds": request.seeds or [0],
            "solver": request.solver,
            "iterations": max(0, request.iterations),
            "apply_worker_changes": bool(request.apply_worker_changes),
        },
        "contract_path": str(contract_path),
        "context_packet_path": str(context_path),
        "baseline_key": list(loop_result.baseline_key),
        "final_key": list(loop_result.final_key),
        "improved": loop_result.final_key > loop_result.baseline_key,
        "round_count": len(loop_result.rounds),
        "promoted_rounds": promoted_rounds,
        "final_worktree": str(loop_result.final_worktree),
        "baseline_summary": summary_payload(loop_result.baseline_summary),
        "rounds": [
            {
                "round_index": item.round_index,
                "decision": item.decision,
                "worker_status": item.worker_status,
                "worker_changed_files": item.worker_changed_files,
                "duplicate_proposal": item.duplicate_proposal,
                "proposal_diagnostics": item.proposal_diagnostics,
                "candidate_key": list(item.candidate_key),
                "incumbent_key_after": list(item.incumbent_key_after),
                "cycle_dir": item.cycle_dir,
                "delta_path": item.delta_path,
                "patch_path": item.patch_path,
            }
            for item in loop_result.rounds
        ],
    }


def render_standard_worker_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# Standard FJSP Worker Loop Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Baseline key: `{json.dumps(manifest.get('baseline_key'), ensure_ascii=False)}`",
        f"- Final key: `{json.dumps(manifest.get('final_key'), ensure_ascii=False)}`",
        f"- Improved: `{manifest.get('improved')}`",
        f"- Rounds: `{manifest.get('round_count')}`",
        f"- Promoted rounds: `{manifest.get('promoted_rounds')}`",
        f"- Final worktree: `{manifest.get('final_worktree')}`",
        "",
        "## Artifacts",
        "",
    ]
    for name, path in (manifest.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{path}`")
    lines.extend(
        [
            "",
            "## Baseline Summary",
            "",
            f"```json\n{json.dumps(manifest.get('baseline_summary') or {}, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Rounds",
            "",
            "| Round | Decision | Worker | Duplicate | Proposal Audit | Candidate Key | Changed Files | Patch |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in manifest.get("rounds", []):
        diagnostics = item.get("proposal_diagnostics") or {}
        proposal_audit = compact_proposal_audit(diagnostics) if isinstance(diagnostics, dict) else {}
        lines.append(
            f"| {item.get('round_index')} | {item.get('decision')} | {item.get('worker_status')} | "
            f"{item.get('duplicate_proposal')} | "
            f"`{json.dumps(proposal_audit, ensure_ascii=False)}` | "
            f"`{json.dumps(item.get('candidate_key'), ensure_ascii=False)}` | "
            f"`{json.dumps(item.get('worker_changed_files') or [], ensure_ascii=False)}` | "
            f"`{item.get('patch_path')}` |"
        )
    lines.extend(
        [
            "",
            "Promotion is allowed only when the Core evaluator-backed objective key is strictly better than the incumbent key.",
            "Proposal-audit diagnostics are carried forward as reflection context and do not change promotion semantics.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def resolve_input_path(project_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return (project_root / path).resolve()
