from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context_packet import write_refreshed_context_packet
from .graph_runner import GraphHarnessRunner
from .models import ObjectiveSpec, TaskContract
from .runner import RunSummary
from .worker import CodingWorker, WorkerResult
from .worker_cycle import prepare_candidate_worktree, run_worker_cycle


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
    candidate_summary: dict[str, Any]
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
) -> WorkerLoopResult:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline_worktree = output_dir / "baseline_worktree"
    prepare_candidate_worktree(
        project_root=project_root.resolve(),
        contract=contract,
        worktree_path=baseline_worktree,
    )
    baseline_summary = _run_harness(contract=contract, project_root=baseline_worktree, output_dir=output_dir / "baseline_harness")
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
        )
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
        proposal_fingerprint = worker_proposal_fingerprint(cycle.worker_result)
        duplicate_proposal = proposal_fingerprint in seen_proposal_fingerprints
        seen_proposal_fingerprints.add(proposal_fingerprint)
        candidate_key = summary_objective_key(cycle.summary, contract.objectives)
        promoted = candidate_key > incumbent_key
        if promoted:
            incumbent_key = candidate_key
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
                candidate_summary=summary_payload(cycle.summary),
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
    )
    write_loop_report(output_dir=output_dir, result=result)
    return result


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
        "candidate_summary": item.candidate_summary,
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
        "| Round | Decision | Worker | Duplicate Proposal | Candidate Key | Incumbent Key After | Context Packet | Worktree Delta | Changed Files |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in result.rounds:
        lines.append(
            f"| {item.round_index} | {item.decision} | {item.worker_status} | "
            f"{'yes' if item.duplicate_proposal else 'no'} | "
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
            "Rolled-back rounds leave the incumbent worktree unchanged.",
        ]
    )
    (output_dir / "loop_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_harness(*, contract: TaskContract, project_root: Path, output_dir: Path) -> RunSummary:
    runner = GraphHarnessRunner(contract=contract, project_root=project_root, output_dir=output_dir)
    try:
        return runner.run()
    finally:
        runner.close()


def _hash_json(payload: Any) -> str:
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
