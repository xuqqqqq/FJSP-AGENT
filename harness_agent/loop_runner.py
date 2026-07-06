from __future__ import annotations

import hashlib
import json
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context_packet import write_refreshed_context_packet
from .graph_runner import GraphHarnessRunner
from .ledger import ExperimentRecord
from .models import ObjectiveSpec, TaskContract
from .runner import RunSummary
from .worker import CodingWorker, WorkerResult
from .worker_cycle import prepare_candidate_worktree, run_worker_cycle


AGENT_GENERATED_BASELINE_HIDDEN_INCUMBENT_FILES = (
    "examples/standard_fjsp_solver.py",
    "examples/standard_fjsp_portfolio_solver.py",
    "examples/standard_fjsp_local_search_solver.py",
    "examples/standard_fjsp_awls_solver.py",
    "examples/standard_fjsp_awls_cpp_backend.py",
    "examples/awls_evolved_slots.py",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LoopRoundRecord:
    round_index: int
    decision: str
    candidate_key: tuple[float, ...]
    incumbent_key_after: tuple[float, ...]
    worker_status: str
    worker_changed_files: list[str]
    proposal_fingerprint: str
    duplicate_proposal: bool
    proposal_diagnostics: dict[str, Any]
    candidate_summary: dict[str, Any]
    promotion_check: dict[str, Any]
    cycle_dir: str
    context_packet_path: str
    delta_path: str
    patch_path: str
    promoted_worktree: str | None


@dataclass(frozen=True)
class WorkerLoopResult:
    baseline_key: tuple[float, ...]
    final_key: tuple[float, ...]
    final_worktree: Path
    rounds: list[LoopRoundRecord]
    baseline_summary: RunSummary
    baseline_source: str = "current_project"
    baseline_generation: dict[str, Any] | None = None


def run_worker_loop(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
    context_packet_path: Path,
    worker: CodingWorker,
    experiment_id: str,
    iterations: int,
    max_steps: int,
    max_runtime_seconds: int,
    apply_worker_changes: bool,
    promotion_repeats: int = 1,
    baseline_source: str = "current_project",
    baseline_worker: CodingWorker | None = None,
) -> WorkerLoopResult:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized_baseline_source = normalize_baseline_source(baseline_source)
    baseline_generation: dict[str, Any] | None = None
    if normalized_baseline_source == "agent_generated":
        baseline_summary, baseline_worktree, baseline_generation = run_agent_generated_baseline(
            contract=contract,
            project_root=project_root,
            output_dir=output_dir,
            context_packet_path=context_packet_path,
            worker=baseline_worker or worker,
            experiment_id=experiment_id,
            max_steps=max_steps,
            max_runtime_seconds=max_runtime_seconds,
        )
    else:
        baseline_worktree = output_dir / "baseline_worktree"
        prepare_candidate_worktree(
            project_root=project_root.resolve(),
            contract=contract,
            worktree_path=baseline_worktree,
        )
        baseline_summary = _run_harness(
            contract=contract,
            project_root=baseline_worktree,
            output_dir=output_dir / "baseline_harness",
        )
    incumbent_key = summary_objective_key(baseline_summary, contract.objectives)
    incumbent_worktree = baseline_worktree

    round_records: list[LoopRoundRecord] = []
    seen_proposal_fingerprints: set[str] = set()
    for round_index in range(max(0, iterations)):
        cycle_dir = output_dir / f"round_{round_index:03d}"
        round_context_packet_path = write_refreshed_context_packet(
            base_context_packet_path=context_packet_path,
            output_path=cycle_dir / "context_packet.json",
            loop_feedback=loop_feedback_payload(
                round_index=round_index,
                contract=contract,
                baseline_summary=baseline_summary,
                baseline_key=summary_objective_key(baseline_summary, contract.objectives),
                incumbent_key_before=incumbent_key,
                incumbent_worktree=incumbent_worktree,
                previous_rounds=round_records,
            ),
            project_root=incumbent_worktree,
        )
        try:
            cycle = run_worker_cycle(
                contract=contract,
                project_root=incumbent_worktree,
                output_dir=cycle_dir,
                context_packet_path=round_context_packet_path,
                worker=worker,
                experiment_id=f"{experiment_id}_round_{round_index:03d}",
                max_steps=max_steps,
                max_runtime_seconds=max_runtime_seconds,
                apply_worker_changes=apply_worker_changes,
            )
        except Exception as exc:  # noqa: BLE001 - failed worker rounds are feedback, not loop-ending failures.
            exception_path = cycle_dir / "cycle_exception.txt"
            patch_path = cycle_dir / "worker_changes.patch"
            delta_path = cycle_dir / "worker_worktree_delta.json"
            cycle_dir.mkdir(parents=True, exist_ok=True)
            exception_path.write_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                encoding="utf-8",
            )
            patch_path.write_text("", encoding="utf-8")
            delta_path.write_text(
                json.dumps(
                    {
                        "counts": {"added": 0, "modified": 0, "deleted": 0, "total_changed": 0},
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            candidate_key = tuple(float("-inf") for _ in contract.objectives)
            proposal_diagnostics = {
                "status": "worker_exception",
                "reason": str(exc),
                "exception_path": str(exception_path),
            }
            proposal_fingerprint = _hash_json(proposal_diagnostics)
            duplicate_proposal = proposal_fingerprint in seen_proposal_fingerprints
            seen_proposal_fingerprints.add(proposal_fingerprint)
            round_records.append(
                LoopRoundRecord(
                    round_index=round_index,
                    decision="rolled_back",
                    candidate_key=candidate_key,
                    incumbent_key_after=incumbent_key,
                    worker_status="worker_exception",
                    worker_changed_files=[],
                    proposal_fingerprint=proposal_fingerprint,
                    duplicate_proposal=duplicate_proposal,
                    proposal_diagnostics=proposal_diagnostics,
                    candidate_summary={
                        "total": 0,
                        "valid": 0,
                        "failed": 0,
                        "error": str(exc),
                    },
                    promotion_check={
                        "status": "skipped",
                        "reason": "worker_exception",
                        "promoted": False,
                        "required_repeats": max(1, promotion_repeats),
                    },
                    cycle_dir=str(cycle_dir),
                    context_packet_path=str(round_context_packet_path),
                    delta_path=str(delta_path),
                    patch_path=str(patch_path),
                    promoted_worktree=None,
                )
            )
            continue

        proposal_fingerprint = worker_proposal_fingerprint(cycle.worker_result)
        duplicate_proposal = proposal_fingerprint in seen_proposal_fingerprints
        seen_proposal_fingerprints.add(proposal_fingerprint)
        proposal_diagnostics = worker_proposal_diagnostics(cycle.worker_result)
        candidate_key = summary_objective_key(cycle.summary, contract.objectives)
        promotion_check = evaluate_promotion_check(
            contract=contract,
            incumbent_worktree=incumbent_worktree,
            candidate_worktree=cycle.worktree_path,
            output_dir=cycle_dir / "promotion_check",
            incumbent_key=incumbent_key,
            candidate_key=candidate_key,
            promotion_repeats=promotion_repeats,
        )
        promoted = bool(promotion_check.get("promoted"))
        if promoted:
            incumbent_key = tuple(float(item) for item in promotion_check.get("accepted_key", candidate_key))
            incumbent_worktree = cycle.worktree_path
        round_records.append(
            LoopRoundRecord(
                round_index=round_index,
                decision="promoted" if promoted else "rolled_back",
                candidate_key=candidate_key,
                incumbent_key_after=incumbent_key,
                worker_status=cycle.worker_result.status,
                worker_changed_files=cycle.worker_result.changed_files,
                proposal_fingerprint=proposal_fingerprint,
                duplicate_proposal=duplicate_proposal,
                proposal_diagnostics=proposal_diagnostics,
                candidate_summary=summary_payload(cycle.summary),
                promotion_check=promotion_check,
                cycle_dir=str(cycle_dir),
                context_packet_path=str(round_context_packet_path),
                delta_path=str(cycle.delta_path),
                patch_path=str(cycle.patch_path),
                promoted_worktree=str(cycle.worktree_path) if promoted else None,
            )
        )

    result = WorkerLoopResult(
        baseline_key=summary_objective_key(baseline_summary, contract.objectives),
        final_key=incumbent_key,
        final_worktree=incumbent_worktree,
        rounds=round_records,
        baseline_summary=baseline_summary,
        baseline_source=normalized_baseline_source,
        baseline_generation=baseline_generation,
    )
    write_loop_report(output_dir=output_dir, result=result)
    return result


def normalize_baseline_source(value: str) -> str:
    normalized = str(value or "current_project").strip().lower().replace("-", "_")
    if normalized in {"agent", "agent_generated", "agent_written", "generated"}:
        return "agent_generated"
    return "current_project"


def run_agent_generated_baseline(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
    context_packet_path: Path,
    worker: CodingWorker,
    experiment_id: str,
    max_steps: int,
    max_runtime_seconds: int,
) -> tuple[RunSummary, Path, dict[str, Any]]:
    """Ask the worker to create the initial solver before measuring baseline.

    This mode is intentionally different from incumbent improvement.  Core
    still supplies parser/evaluator files and knowledge context, but incumbent
    solver entrypoints are removed from the worker source tree.  The solver
    entrypoint named by the contract is expected to be created by the coding
    worker before the first evaluator run.
    """

    baseline_dir = output_dir / "agent_generated_baseline"
    source_project, hidden_incumbent_files = prepare_agent_generated_baseline_source_project(
        project_root=project_root,
        contract=contract,
        output_dir=baseline_dir,
    )
    baseline_context_path = write_baseline_generation_context_packet(
        base_context_packet_path=context_packet_path,
        output_path=baseline_dir / "context_packet.json",
        hidden_incumbent_files=hidden_incumbent_files,
    )
    try:
        cycle = run_worker_cycle(
            contract=contract,
            project_root=source_project,
            output_dir=baseline_dir,
            context_packet_path=baseline_context_path,
            worker=worker,
            experiment_id=f"{experiment_id}_agent_generated_baseline",
            max_steps=max_steps,
            max_runtime_seconds=max_runtime_seconds,
            apply_worker_changes=True,
        )
        generation_payload = {
            "status": "ok",
            "source": "agent_generated",
            "cycle_dir": str(baseline_dir),
            "context_packet_path": str(baseline_context_path),
            "source_project": str(source_project),
            "hidden_incumbent_files": hidden_incumbent_files,
            "worktree": str(cycle.worktree_path),
            "worker_status": cycle.worker_result.status,
            "worker_changed_files": cycle.worker_result.changed_files,
            "proposal_diagnostics": worker_proposal_diagnostics(cycle.worker_result),
            "summary": summary_payload(cycle.summary),
            "agentic_judgment": cycle.agentic_judgment.to_payload(),
            "agentic_error_analysis": cycle.agentic_error_analysis.to_payload()
            if cycle.agentic_error_analysis
            else None,
        }
        return cycle.summary, cycle.worktree_path, generation_payload
    except Exception as exc:  # noqa: BLE001 - invalid generated baselines should become evaluator feedback.
        fallback_worktree = output_dir / "agent_generated_baseline_failed_worktree"
        prepare_candidate_worktree(
            project_root=source_project,
            contract=contract,
            worktree_path=fallback_worktree,
        )
        exception_path = baseline_dir / "baseline_generation_exception.txt"
        exception_path.parent.mkdir(parents=True, exist_ok=True)
        exception_path.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        summary = RunSummary(
            total=0,
            valid=0,
            failed=0,
            best_experiment_id=None,
            best_metrics={},
            best_candidate_id=None,
            best_candidate_metrics=None,
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"agent_generated_baseline_exception": str(exc)},
        )
        generation_payload = {
            "status": "worker_exception",
            "source": "agent_generated",
            "cycle_dir": str(baseline_dir),
            "context_packet_path": str(baseline_context_path),
            "source_project": str(source_project),
            "hidden_incumbent_files": hidden_incumbent_files,
            "worktree": str(fallback_worktree),
            "exception_path": str(exception_path),
            "reason": str(exc),
            "summary": summary_payload(summary),
        }
        return summary, fallback_worktree, generation_payload


def prepare_agent_generated_baseline_source_project(
    *,
    project_root: Path,
    contract: TaskContract,
    output_dir: Path,
) -> tuple[Path, list[str]]:
    source_project = output_dir / "source_project_without_incumbent_solvers"
    prepare_candidate_worktree(
        project_root=project_root.resolve(),
        contract=contract,
        worktree_path=source_project,
    )
    hidden: list[str] = []
    for relative in AGENT_GENERATED_BASELINE_HIDDEN_INCUMBENT_FILES:
        target = source_project / relative
        if target.exists() and target.is_file():
            target.unlink()
            hidden.append(relative)
    note_path = source_project / "examples" / "AGENT_GENERATED_BASELINE.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "\n".join(
            [
                "# Agent-Generated Baseline Source",
                "",
                "Incumbent solver entrypoints were removed from this worktree.",
                "Generate the solver entrypoint named by the task contract from the IO/requirement docs,",
                "domain-pack metadata, knowledge cards, and fixed parser/evaluator helpers.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return source_project, hidden


def write_baseline_generation_context_packet(
    *,
    base_context_packet_path: Path,
    output_path: Path,
    hidden_incumbent_files: list[str] | None = None,
) -> Path:
    packet = json.loads(base_context_packet_path.read_text(encoding="utf-8-sig"))
    parent_hash = packet.get("packet_hash") or _hash_text(json.dumps(packet, ensure_ascii=False, sort_keys=True))
    refreshed = dict(packet)
    refreshed.pop("packet_hash", None)
    refreshed["created_at"] = _utc_now_iso()
    refreshed["parent_packet_hash"] = parent_hash
    refreshed["refresh_reason"] = "agent_generated_baseline"
    refreshed["baseline_generation"] = {
        "source": "agent_generated",
        "purpose": (
            "Generate the initial runnable solver from the IO document, requirement document, "
            "instance diagnostics, domain-pack capability, and knowledge cards before any incumbent comparison."
        ),
        "rules": [
            "Do not copy a complete incumbent solver as the baseline.",
            "Create or replace the solver entrypoint named in evaluator_protocol.solver_command_template.",
            "Reuse fixed parser/evaluator helper APIs when the context exposes them.",
            "Treat LB/UB/BKS as diagnostics only; optimize the declared objective.",
        ],
        "hidden_incumbent_files": hidden_incumbent_files or [],
    }
    worker_instruction = dict(refreshed.get("worker_instruction") or {})
    required_order = list(worker_instruction.get("required_order") or [])
    generation_step = (
        "This is agent-generated baseline creation: write the initial runnable solver from docs, "
        "knowledge_cards, and evaluator_protocol before Core measures baseline."
    )
    if generation_step not in required_order:
        required_order.insert(1, generation_step)
    worker_instruction["required_order"] = required_order
    worker_instruction["baseline_generation_rule"] = (
        "The first measured baseline must come from worker-written code, not from an existing incumbent solver."
    )
    refreshed["worker_instruction"] = worker_instruction
    hypothesis = str(refreshed.get("hypothesis") or "")
    generation_hypothesis = (
        "First generate a complete runnable solver entrypoint for the command in evaluator_protocol. "
        "Base the implementation on the requirement document, IO document, instance_diagnostics, "
        "domain-pack metadata, and knowledge cards.  Do not edit the evaluator or benchmark data."
    )
    refreshed["hypothesis"] = f"{generation_hypothesis}\n\n{hypothesis}".strip()
    refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def summary_objective_key(summary: RunSummary, objectives: list[ObjectiveSpec]) -> tuple[float, ...]:
    metrics = summary.best_candidate_metrics or summary.best_metrics or {}
    if not metrics:
        return tuple(float("-inf") for _ in objectives)
    valid_instances = metrics.get("valid_instances")
    expected_instances = metrics.get("expected_instances")
    if isinstance(valid_instances, (int, float)) and isinstance(expected_instances, (int, float)):
        if float(valid_instances) < float(expected_instances):
            return tuple(float("-inf") for _ in objectives)

    ordered = sorted(objectives, key=lambda item: item.priority)
    key: list[float] = []
    for objective in ordered:
        raw_value = metrics.get(f"avg_{objective.name}", metrics.get(objective.name))
        if not isinstance(raw_value, (int, float)):
            key.append(float("-inf"))
            continue
        value = float(raw_value)
        if objective.threshold is not None:
            if objective.direction == "maximize" and value < objective.threshold:
                key.append(float("-inf"))
                continue
            if objective.direction == "minimize" and value > objective.threshold:
                key.append(float("-inf"))
                continue
        key.append(value if objective.direction == "maximize" else -value)
    return tuple(key)


def evaluate_promotion_check(
    *,
    contract: TaskContract,
    incumbent_worktree: Path,
    candidate_worktree: Path,
    output_dir: Path,
    incumbent_key: tuple[float, ...],
    candidate_key: tuple[float, ...],
    promotion_repeats: int,
) -> dict[str, Any]:
    """Return the evaluator-backed promotion decision for a worker candidate.

    The default path keeps the historic loop semantics: one evaluator-backed
    strict improvement promotes.  When promotion_repeats is greater than one,
    the candidate must also beat the current incumbent on an equal repeated
    probe, using the mean objective key across all repeated records.
    """

    repeats = max(1, int(promotion_repeats))
    initially_better = candidate_key > incumbent_key
    if not initially_better:
        return {
            "status": "skipped",
            "reason": "candidate_not_strictly_better",
            "required_repeats": repeats,
            "incumbent_key": list(incumbent_key),
            "candidate_key": list(candidate_key),
            "promoted": False,
            "accepted_key": list(incumbent_key),
        }
    if repeats <= 1:
        return {
            "status": "single_run",
            "reason": "strict_objective_improvement",
            "required_repeats": repeats,
            "incumbent_key": list(incumbent_key),
            "candidate_key": list(candidate_key),
            "promoted": True,
            "accepted_key": list(candidate_key),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    repeat_contract = replace(
        contract,
        task_id=f"{contract.task_id}_promotion_check",
        commands=replace(contract.commands, quick_test=None),
        budget=replace(contract.budget, rounds=repeats),
    )
    incumbent_summary, incumbent_records = _run_harness_with_records(
        contract=repeat_contract,
        project_root=incumbent_worktree,
        output_dir=output_dir / "incumbent",
    )
    candidate_summary, candidate_records = _run_harness_with_records(
        contract=repeat_contract,
        project_root=candidate_worktree,
        output_dir=output_dir / "candidate",
    )
    expected_runs = repeats * len(contract.instances) * len(contract.budget.seeds)
    incumbent_repeat_key = repeated_records_objective_key(
        incumbent_records,
        contract.objectives,
        expected_runs=expected_runs,
    )
    candidate_repeat_key = repeated_records_objective_key(
        candidate_records,
        contract.objectives,
        expected_runs=expected_runs,
    )
    promoted = candidate_repeat_key > incumbent_repeat_key
    return {
        "status": "passed" if promoted else "failed",
        "reason": "repeat_objective_improvement" if promoted else "repeat_objective_not_strictly_better",
        "required_repeats": repeats,
        "expected_runs": expected_runs,
        "incumbent_key": list(incumbent_key),
        "candidate_key": list(candidate_key),
        "incumbent_repeat_key": list(incumbent_repeat_key),
        "candidate_repeat_key": list(candidate_repeat_key),
        "incumbent_summary": summary_payload(incumbent_summary),
        "candidate_summary": summary_payload(candidate_summary),
        "promoted": promoted,
        "accepted_key": list(candidate_repeat_key if promoted else incumbent_key),
    }


def repeated_records_objective_key(
    records: list[ExperimentRecord],
    objectives: list[ObjectiveSpec],
    *,
    expected_runs: int,
) -> tuple[float, ...]:
    valid_records = [record for record in records if record.valid]
    if len(records) != expected_runs or len(valid_records) != expected_runs:
        return tuple(float("-inf") for _ in objectives)
    return tuple(
        sum(record.objective_key[index] for record in valid_records) / len(valid_records)
        for index in range(len(objectives))
    )


def summary_payload(summary: RunSummary) -> dict[str, Any]:
    return {
        "total": summary.total,
        "valid": summary.valid,
        "failed": summary.failed,
        "best_experiment_id": summary.best_experiment_id,
        "best_metrics": summary.best_metrics,
        "best_candidate_id": summary.best_candidate_id,
        "best_candidate_metrics": summary.best_candidate_metrics,
        "candidate_summaries": summary.candidate_summaries or [],
        "pareto_frontier": summary.pareto_frontier or [],
        "validation_summary": summary.validation_summary or {},
    }


def loop_feedback_payload(
    *,
    round_index: int,
    contract: TaskContract,
    baseline_summary: RunSummary,
    baseline_key: tuple[float, ...],
    incumbent_key_before: tuple[float, ...],
    incumbent_worktree: Path,
    previous_rounds: list[LoopRoundRecord],
) -> dict[str, Any]:
    ordered_objectives = sorted(contract.objectives, key=lambda item: item.priority)
    return {
        "purpose": "Provide evaluator-backed history for the next coding-worker proposal.",
        "round_index": round_index,
        "objective_key_order": [
            {
                "name": objective.name,
                "direction": objective.direction,
                "priority": objective.priority,
                "threshold": objective.threshold,
            }
            for objective in ordered_objectives
        ],
        "baseline_key": list(baseline_key),
        "incumbent_key_before": list(incumbent_key_before),
        "incumbent_worktree": str(incumbent_worktree),
        "baseline_summary": summary_payload(baseline_summary),
        "previous_rounds": [round_record_payload(item) for item in previous_rounds],
        "instructions": [
            "Use only Core evaluator metrics as promotion evidence.",
            "Preserve successful ideas from promoted rounds unless a better alternative is justified.",
            "Do not repeat rolled-back edits unchanged; explain what is materially different if revisiting them.",
            "If promotion_check failed, treat the candidate as a noisy or unstable improvement and change the rule-level idea.",
            "Use proposal_diagnostics to inspect whether prior proposals used project_intake, touched solver or validator files, or missed quick-test guidance.",
            "Prefer small, reversible solver changes whose effect can be attributed in the next evaluator run.",
        ],
    }


def round_record_payload(item: LoopRoundRecord) -> dict[str, Any]:
    return {
        "round_index": item.round_index,
        "decision": item.decision,
        "candidate_key": list(item.candidate_key),
        "incumbent_key_after": list(item.incumbent_key_after),
        "worker_status": item.worker_status,
        "worker_changed_files": item.worker_changed_files,
        "proposal_fingerprint": item.proposal_fingerprint,
        "duplicate_proposal": item.duplicate_proposal,
        "proposal_diagnostics": item.proposal_diagnostics,
        "candidate_summary": item.candidate_summary,
        "promotion_check": item.promotion_check,
        "cycle_dir": item.cycle_dir,
        "context_packet_path": item.context_packet_path,
        "delta_path": item.delta_path,
        "patch_path": item.patch_path,
        "promoted_worktree": item.promoted_worktree,
    }


def worker_proposal_fingerprint(worker_result: WorkerResult) -> str:
    """Return a stable proposal fingerprint for duplicate-proposal diagnostics."""

    artifacts = worker_result.artifacts or {}
    proposal_path_value = artifacts.get("proposal")
    if proposal_path_value:
        proposal_path = Path(proposal_path_value)
        if proposal_path.exists():
            try:
                proposal = json.loads(proposal_path.read_text(encoding="utf-8-sig"))
                return _hash_json({"proposal": proposal})
            except (OSError, json.JSONDecodeError):
                try:
                    return _hash_text(proposal_path.read_text(encoding="utf-8-sig", errors="replace"))
                except OSError:
                    pass
    return _hash_json(
        {
            "status": worker_result.status,
            "changed_files": sorted(worker_result.changed_files),
            "artifacts": sorted((worker_result.artifacts or {}).keys()),
        }
    )


def worker_proposal_diagnostics(worker_result: WorkerResult) -> dict[str, Any]:
    """Extract compact proposal diagnostics for the next self-evolution round.

    The diagnostics are reflection context only.  Promotion still depends solely
    on the fixed evaluator objective key.
    """

    artifacts = worker_result.artifacts or {}
    proposal_path_value = artifacts.get("proposal")
    if not proposal_path_value:
        return {"status": "missing", "reason": "worker_result_has_no_proposal_artifact"}

    proposal_path = Path(proposal_path_value)
    if not proposal_path.exists():
        return {
            "status": "missing",
            "reason": "proposal_artifact_not_found",
            "proposal_path": str(proposal_path),
        }

    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "unreadable",
            "reason": str(exc),
            "proposal_path": str(proposal_path),
        }

    audit = proposal.get("proposal_audit")
    if not isinstance(audit, dict):
        audit = {}
    context_usage = proposal.get("context_usage")
    if not isinstance(context_usage, dict):
        context_usage = {}

    return {
        "status": "ok",
        "proposal_path": str(proposal_path),
        "summary": _bounded_text(proposal.get("summary")),
        "strategy_intent": _bounded_text(proposal.get("strategy_intent")),
        "rule_operator_hypotheses": compact_rule_operator_hypotheses(
            proposal.get("rule_operator_hypotheses") or [],
            limit=12,
        ),
        "context_usage": {
            "used_project_intake": bool(context_usage.get("used_project_intake")),
            "referenced_files": _bounded_list(context_usage.get("referenced_files"), limit=40),
            "notes": _bounded_text(context_usage.get("notes")),
        },
        "proposal_audit": {
            "project_intake_present": audit.get("project_intake_present"),
            "project_intake_status": audit.get("project_intake_status"),
            "declared_project_intake_used": audit.get("declared_project_intake_used"),
            "slot_id": audit.get("slot_id"),
            "target_file": audit.get("target_file"),
            "accepted_change_count": audit.get("accepted_change_count"),
            "rejected_change_count": audit.get("rejected_change_count"),
            "accepted_change_paths": _bounded_list(audit.get("accepted_change_paths"), limit=40),
            "failure_memory_status": audit.get("failure_memory_status"),
            "avoid_pattern_count": audit.get("avoid_pattern_count"),
            "rolled_back_round_count": audit.get("rolled_back_round_count"),
            "detected_referenced_intake_files": _bounded_list(
                audit.get("detected_referenced_intake_files"), limit=40
            ),
            "changed_core_algorithm_files": _bounded_list(audit.get("changed_core_algorithm_files"), limit=40),
            "changed_validator_files": _bounded_list(audit.get("changed_validator_files"), limit=40),
            "changed_benchmark_files": _bounded_list(audit.get("changed_benchmark_files"), limit=40),
            "referenced_test_commands": _bounded_list(audit.get("referenced_test_commands"), limit=20),
            "operator_lineage": audit.get("operator_lineage") or {},
            "warnings": _bounded_list(audit.get("warnings"), limit=20),
        },
    }


def write_loop_report(*, output_dir: Path, result: WorkerLoopResult) -> None:
    payload = {
        "baseline_key": list(result.baseline_key),
        "final_key": list(result.final_key),
        "final_worktree": str(result.final_worktree),
        "baseline_summary": summary_payload(result.baseline_summary),
        "rounds": [round_record_payload(item) for item in result.rounds],
    }
    (output_dir / "loop_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Worker Loop Report",
        "",
        f"- Baseline key: `{json.dumps(result.baseline_key, ensure_ascii=False)}`",
        f"- Final key: `{json.dumps(result.final_key, ensure_ascii=False)}`",
        f"- Final worktree: `{result.final_worktree}`",
        "",
        "## Baseline",
        "",
        f"`{json.dumps(summary_payload(result.baseline_summary), ensure_ascii=False)}`",
        "",
        "## Rounds",
        "",
        "| Round | Decision | Worker | Duplicate Proposal | Promotion Check | Proposal Audit | Candidate Key | Incumbent Key After | Context Packet | Worktree Delta | Changed Files |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in result.rounds:
        proposal_audit = compact_proposal_audit(item.proposal_diagnostics)
        lines.append(
            f"| {item.round_index} | {item.decision} | {item.worker_status} | "
            f"{'yes' if item.duplicate_proposal else 'no'} | "
            f"`{json.dumps(compact_promotion_check(item.promotion_check), ensure_ascii=False)}` | "
            f"`{json.dumps(proposal_audit, ensure_ascii=False)}` | "
            f"`{json.dumps(item.candidate_key, ensure_ascii=False)}` | "
            f"`{json.dumps(item.incumbent_key_after, ensure_ascii=False)}` | "
            f"`{item.context_packet_path}` | "
            f"`{item.delta_path}` | "
            f"`{json.dumps(item.worker_changed_files, ensure_ascii=False)}` |"
        )
    lines.extend(
        [
            "",
            "A round is promoted only when its Core evaluator-backed objective key is strictly better than the incumbent key.",
            "When a repeat promotion check is configured, the candidate must also beat the incumbent on the repeated Core evaluator probe.",
            "Rolled-back rounds leave the incumbent worktree unchanged.",
            "Proposal audit fields are reflection inputs for later rounds; they are not promotion gates.",
        ]
    )
    (output_dir / "loop_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compact_proposal_audit(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Return the report-friendly subset of proposal diagnostics."""

    audit = diagnostics.get("proposal_audit")
    if not isinstance(audit, dict):
        audit = {}
    return {
        "status": diagnostics.get("status"),
        "used_intake": (diagnostics.get("context_usage") or {}).get("used_project_intake")
        if isinstance(diagnostics.get("context_usage"), dict)
        else None,
        "hypotheses": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
            }
            for item in compact_rule_operator_hypotheses(
                diagnostics.get("rule_operator_hypotheses") or [],
                limit=6,
            )
        ],
        "operator_lineage": audit.get("operator_lineage") or {},
        "slot_id": audit.get("slot_id"),
        "accepted_change_paths": audit.get("accepted_change_paths") or [],
        "failure_memory_status": audit.get("failure_memory_status"),
        "avoid_pattern_count": audit.get("avoid_pattern_count"),
        "rolled_back_round_count": audit.get("rolled_back_round_count"),
        "changed_core": audit.get("changed_core_algorithm_files") or [],
        "changed_validators": audit.get("changed_validator_files") or [],
        "warnings": audit.get("warnings") or [],
    }


def compact_promotion_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": check.get("status"),
        "reason": check.get("reason"),
        "required_repeats": check.get("required_repeats"),
        "promoted": check.get("promoted"),
        "candidate_repeat_key": check.get("candidate_repeat_key"),
        "incumbent_repeat_key": check.get("incumbent_repeat_key"),
    }


def compact_rule_operator_hypotheses(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "name": _bounded_text(item.get("name"), limit=120),
                "type": _bounded_text(item.get("type"), limit=80),
                "novelty": _bounded_text(item.get("novelty"), limit=240),
                "expected_effect": _bounded_text(item.get("expected_effect"), limit=240),
                "target_files": _bounded_list(item.get("target_files"), limit=12),
                "evidence_used": _bounded_list(item.get("evidence_used"), limit=12),
                "ablation_plan": _bounded_text(item.get("ablation_plan"), limit=240),
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _run_harness(*, contract: TaskContract, project_root: Path, output_dir: Path) -> RunSummary:
    runner = GraphHarnessRunner(contract=contract, project_root=project_root, output_dir=output_dir)
    try:
        return runner.run()
    finally:
        runner.close()


def _run_harness_with_records(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
) -> tuple[RunSummary, list[ExperimentRecord]]:
    runner = GraphHarnessRunner(contract=contract, project_root=project_root, output_dir=output_dir)
    try:
        summary = runner.run()
        records = runner.ledger.list_records()
        return summary, records
    finally:
        runner.close()


def _hash_json(payload: Any) -> str:
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bounded_text(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _bounded_list(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]
