from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .graph_runner import GraphHarnessRunner
from .models import ObjectiveSpec, TaskContract
from .runner import RunSummary
from .worker import CodingWorker
from .worker_cycle import prepare_candidate_worktree, run_worker_cycle


@dataclass(frozen=True)
class LoopRoundRecord:
    round_index: int
    decision: str
    candidate_key: tuple[float, ...]
    incumbent_key_after: tuple[float, ...]
    worker_status: str
    worker_changed_files: list[str]
    candidate_summary: dict[str, Any]
    cycle_dir: str
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
    for round_index in range(max(0, iterations)):
        cycle_dir = output_dir / f"round_{round_index:03d}"
        cycle = run_worker_cycle(
            contract=contract,
            project_root=incumbent_worktree,
            output_dir=cycle_dir,
            context_packet_path=context_packet_path,
            worker=worker,
            experiment_id=f"{experiment_id}_round_{round_index:03d}",
            max_steps=max_steps,
            max_runtime_seconds=max_runtime_seconds,
            apply_worker_changes=apply_worker_changes,
        )
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
                candidate_summary=summary_payload(cycle.summary),
                cycle_dir=str(cycle_dir),
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
    }


def write_loop_report(*, output_dir: Path, result: WorkerLoopResult) -> None:
    payload = {
        "baseline_key": list(result.baseline_key),
        "final_key": list(result.final_key),
        "final_worktree": str(result.final_worktree),
        "baseline_summary": summary_payload(result.baseline_summary),
        "rounds": [
            {
                "round_index": item.round_index,
                "decision": item.decision,
                "candidate_key": list(item.candidate_key),
                "incumbent_key_after": list(item.incumbent_key_after),
                "worker_status": item.worker_status,
                "worker_changed_files": item.worker_changed_files,
                "candidate_summary": item.candidate_summary,
                "cycle_dir": item.cycle_dir,
                "promoted_worktree": item.promoted_worktree,
            }
            for item in result.rounds
        ],
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
        "| Round | Decision | Worker | Candidate Key | Incumbent Key After | Changed Files |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for item in result.rounds:
        lines.append(
            f"| {item.round_index} | {item.decision} | {item.worker_status} | "
            f"`{json.dumps(item.candidate_key, ensure_ascii=False)}` | "
            f"`{json.dumps(item.incumbent_key_after, ensure_ascii=False)}` | "
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
