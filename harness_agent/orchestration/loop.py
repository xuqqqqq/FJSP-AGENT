"""多轮闭环编排：方向规划、同轮修补、Core 复验、晋升/回滚和经验沉淀。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_agent.context.loader import load_context_packet
from harness_agent.context.packet import activate_method_package_context, write_refreshed_context_packet
from harness_agent.domains.pack import get_domain_pack
from harness_agent.core.graph import GraphHarnessRunner
from harness_agent.agents.hypothesis import (
    build_experience_memory,
    render_direction_graph_markdown,
    render_experience_memory_markdown,
    summarize_direction_graph,
)
from harness_agent.core.ledger import ExperimentRecord
from harness_agent.agents.main import DirectionPlanRequest, DirectionPlanningAgent, EvidenceDrivenMainAgent
from harness_agent.core.models import ObjectiveSpec, TaskContract
from harness_agent.core.runner import RunSummary
from harness_agent.agents.semantic import (
    AlgorithmSemanticReviewer,
    AlgorithmSemanticReviewRequest,
)
from harness_agent.worker import CodingWorker, WorkerResult
from harness_agent.orchestration.cycle import prepare_candidate_worktree, run_worker_cycle


DEFAULT_IN_ROUND_REPAIR_ATTEMPTS = 3


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
    smoke_gate: dict[str, Any]
    promotion_check: dict[str, Any]
    cycle_dir: str
    context_packet_path: str
    delta_path: str
    patch_path: str
    promoted_worktree: str | None
    direction_plan: dict[str, Any] | None = None
    semantic_review: dict[str, Any] | None = None


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
    main_agent: DirectionPlanningAgent | None = None,
    semantic_reviewer: AlgorithmSemanticReviewer | None = None,
    experiment_id: str,
    iterations: int,
    max_steps: int,
    max_runtime_seconds: int,
    apply_worker_changes: bool,
    promotion_repeats: int = 1,
    baseline_source: str = "current_project",
    baseline_worker: CodingWorker | None = None,
    in_round_repair_attempts: int = DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
) -> WorkerLoopResult:
    """运行完整闭环；每轮只推进一个方向，incumbent 始终由 Core 证据保护。"""

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    direction_planner = main_agent or EvidenceDrivenMainAgent()

    normalized_baseline_source = normalize_baseline_source(baseline_source)
    baseline_generation: dict[str, Any] | None = None
    if normalized_baseline_source == "agent_generated":
        baseline_worker_for_generation = baseline_worker or worker
        baseline_summary, baseline_worktree, baseline_generation = run_agent_generated_baseline(
            contract=contract,
            project_root=project_root,
            output_dir=output_dir,
            context_packet_path=context_packet_path,
            worker=baseline_worker_for_generation,
            experiment_id=experiment_id,
            max_steps=max_steps,
            max_runtime_seconds=max_runtime_seconds,
            semantic_reviewer=semantic_reviewer,
            direction_plan=plan_agent_generated_baseline_direction(
                planner=direction_planner,
                context_packet_path=context_packet_path,
                output_dir=output_dir / "agent_generated_baseline" / "main_agent",
            ),
            repair_attempts=worker_loop_repair_attempt_budget(
                baseline_worker_for_generation,
                in_round_repair_attempts,
            ),
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
    if normalized_baseline_source == "agent_generated" and not agent_generated_baseline_is_accepted(
        baseline_generation,
        baseline_summary=baseline_summary,
        baseline_key=incumbent_key,
    ):
        if isinstance(baseline_generation, dict):
            baseline_generation["stopped_before_rounds"] = True
            baseline_generation["stop_reason"] = "agent_generated_baseline_not_valid"
        result = WorkerLoopResult(
            baseline_key=incumbent_key,
            final_key=incumbent_key,
            final_worktree=incumbent_worktree,
            rounds=[],
            baseline_summary=baseline_summary,
            baseline_source=normalized_baseline_source,
            baseline_generation=baseline_generation,
        )
        write_loop_report(output_dir=output_dir, result=result, problem_family=contract.problem_family)
        return result
    effective_repair_attempts = worker_loop_repair_attempt_budget(worker, in_round_repair_attempts)
    round_records: list[LoopRoundRecord] = []
    seen_proposal_fingerprints: set[str] = set()
    for round_index in range(max(0, iterations)):
        cycle_dir = output_dir / f"round_{round_index:03d}"
        round_context_packet_path = cycle_dir / "context_packet.json"
        in_round_attempts: list[dict[str, Any]] = []
        planning_feedback = loop_feedback_payload(
            round_index=round_index,
            contract=contract,
            baseline_summary=baseline_summary,
            baseline_key=summary_objective_key(baseline_summary, contract.objectives),
            incumbent_key_before=incumbent_key,
            incumbent_worktree=incumbent_worktree,
            baseline_generation=baseline_generation,
            previous_rounds=round_records,
            current_round_repair=None,
        )
        try:
            direction_plan = direction_planner.plan_direction(
                DirectionPlanRequest(
                    round_index=round_index,
                    context_packet_path=context_packet_path,
                    loop_feedback=planning_feedback,
                    output_dir=cycle_dir / "main_agent",
                )
            )
        except Exception as exc:  # noqa: BLE001 - planner failure should fall back, not lose an experiment round.
            planner_error_path = cycle_dir / "main_agent" / "planner_exception.txt"
            planner_error_path.parent.mkdir(parents=True, exist_ok=True)
            planner_error_path.write_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                encoding="utf-8",
            )
            direction_plan = EvidenceDrivenMainAgent().plan_direction(
                DirectionPlanRequest(
                    round_index=round_index,
                    context_packet_path=context_packet_path,
                    loop_feedback=planning_feedback,
                    output_dir=cycle_dir / "main_agent",
                )
            )
        try:
            cycle, round_context_packet_path, in_round_attempts = run_worker_cycle_with_in_round_repairs(
                contract=contract,
                project_root=incumbent_worktree,
                worker=worker,
                output_dir=cycle_dir,
                base_context_packet_path=context_packet_path,
                round_index=round_index,
                experiment_id=experiment_id,
                max_steps=max_steps,
                max_runtime_seconds=max_runtime_seconds,
                apply_worker_changes=apply_worker_changes,
                baseline_summary=baseline_summary,
                incumbent_key=incumbent_key,
                baseline_generation=baseline_generation,
                previous_rounds=round_records,
                repair_attempts=effective_repair_attempts,
                direction_plan=direction_plan,
                semantic_reviewer=semantic_reviewer,
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
                    smoke_gate={
                        "enabled": False,
                        "passed": False,
                        "full_evaluation_started": False,
                        "summary": None,
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
                    direction_plan=direction_plan,
                    semantic_review=None,
                )
            )
            continue

        proposal_fingerprint = worker_proposal_fingerprint(cycle.worker_result)
        duplicate_proposal = proposal_fingerprint in seen_proposal_fingerprints
        seen_proposal_fingerprints.add(proposal_fingerprint)
        proposal_diagnostics = worker_proposal_diagnostics(cycle.worker_result)
        repair_summary = in_round_repair_summary(in_round_attempts)
        if repair_summary["repair_attempt_count"]:
            proposal_diagnostics["in_round_repair"] = repair_summary
        semantic_review = (
            in_round_attempts[-1].get("semantic_review")
            if in_round_attempts and isinstance(in_round_attempts[-1], dict)
            else None
        )
        if isinstance(semantic_review, dict):
            proposal_diagnostics["algorithm_semantic_review"] = semantic_review
        candidate_key = summary_objective_key(cycle.summary, contract.objectives)
        if semantic_review_blocks_promotion(semantic_review):
            promotion_check = {
                "status": "skipped",
                "reason": semantic_review_promotion_block_reason(semantic_review),
                "promoted": False,
                "required_repeats": max(1, promotion_repeats),
                "semantic_review": semantic_review,
            }
        else:
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
                smoke_gate=cycle_smoke_gate_payload(cycle),
                promotion_check=promotion_check,
                cycle_dir=str(cycle_dir),
                context_packet_path=str(round_context_packet_path),
                delta_path=str(cycle.delta_path),
                patch_path=str(cycle.patch_path),
                promoted_worktree=str(cycle.worktree_path) if promoted else None,
                direction_plan=direction_plan,
                semantic_review=semantic_review if isinstance(semantic_review, dict) else None,
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
    write_loop_report(output_dir=output_dir, result=result, problem_family=contract.problem_family)
    return result


def run_worker_cycle_with_in_round_repairs(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
    base_context_packet_path: Path,
    round_index: int,
    worker: CodingWorker,
    experiment_id: str,
    max_steps: int,
    max_runtime_seconds: int,
    apply_worker_changes: bool,
    baseline_summary: RunSummary,
    incumbent_key: tuple[float, ...],
    baseline_generation: dict[str, Any] | None,
    previous_rounds: list[LoopRoundRecord],
    repair_attempts: int,
    direction_plan: dict[str, Any] | None = None,
    semantic_reviewer: AlgorithmSemanticReviewer | None = None,
) -> tuple[Any, Path, list[dict[str, Any]]]:
    """在同一方向内修补非法候选，修补耗尽后才把该方向记为失败。"""

    """Run one round, retrying repairable illegal candidates inside the same round."""

    max_repair_attempts = max(0, int(repair_attempts))
    attempts: list[dict[str, Any]] = []
    last_cycle: Any | None = None
    last_context_packet_path = output_dir / "context_packet.json"
    direction_project_root = project_root
    for attempt_index in range(max_repair_attempts + 1):
        attempt_dir = output_dir if attempt_index == 0 else output_dir / f"repair_{attempt_index:03d}"
        repair_feedback = (
            current_round_repair_feedback(
                attempt_index=attempt_index,
                max_repair_attempts=max_repair_attempts,
                previous_attempts=attempts,
            )
            if attempt_index > 0
            else None
        )
        last_context_packet_path = write_refreshed_context_packet(
            base_context_packet_path=base_context_packet_path,
            output_path=attempt_dir / "context_packet.json",
            loop_feedback=loop_feedback_payload(
                round_index=round_index,
                contract=contract,
                baseline_summary=baseline_summary,
                baseline_key=summary_objective_key(baseline_summary, contract.objectives),
                incumbent_key_before=incumbent_key,
                incumbent_worktree=project_root,
                baseline_generation=baseline_generation,
                previous_rounds=previous_rounds,
                current_round_repair=repair_feedback,
                current_direction_plan=direction_plan,
            ),
            project_root=direction_project_root,
        )
        last_cycle = run_worker_cycle(
            contract=contract,
            project_root=direction_project_root,
            output_dir=attempt_dir,
            context_packet_path=last_context_packet_path,
            worker=worker,
            experiment_id=f"{experiment_id}_round_{round_index:03d}_attempt_{attempt_index:02d}",
            max_steps=max_steps,
            max_runtime_seconds=max_runtime_seconds,
            apply_worker_changes=apply_worker_changes,
        )
        semantic_review = run_algorithm_semantic_review(
            reviewer=semantic_reviewer,
            cycle=last_cycle,
            context_packet_path=last_context_packet_path,
            direction_plan=direction_plan or {},
            round_index=round_index,
            attempt_index=attempt_index,
            output_dir=attempt_dir / "semantic_review",
            incumbent_key=incumbent_key,
            candidate_key=summary_objective_key(last_cycle.summary, contract.objectives),
        )
        attempts.append(
            round_attempt_payload(
                last_cycle,
                attempt_index=attempt_index,
                context_packet_path=last_context_packet_path,
                incumbent_key=incumbent_key,
                semantic_review=semantic_review,
            )
        )
        if attempt_index >= max_repair_attempts or not should_attempt_in_round_repair(
            last_cycle,
            incumbent_key=incumbent_key,
            semantic_review=semantic_review,
        ):
            break
        direction_project_root = Path(last_cycle.worktree_path)

    if last_cycle is None:
        raise RuntimeError("worker cycle did not produce an attempt")
    return last_cycle, last_context_packet_path, attempts


def worker_loop_repair_attempt_budget(worker: CodingWorker, requested_attempts: int) -> int:
    requested = max(0, int(requested_attempts))
    if requested == 0:
        return 0
    try:
        capabilities = worker.capabilities()
    except Exception:  # noqa: BLE001 - missing capabilities should disable optional repair retries.
        return 0
    if not capabilities.supports_repair:
        return 0
    return requested


def run_algorithm_semantic_review(
    *,
    reviewer: AlgorithmSemanticReviewer | None,
    cycle: Any,
    context_packet_path: Path,
    direction_plan: dict[str, Any],
    round_index: int,
    attempt_index: int,
    output_dir: Path,
    incumbent_key: tuple[float, ...] | None = None,
    candidate_key: tuple[float, ...] | None = None,
) -> dict[str, Any]:
    if reviewer is None:
        return {
            "schema_version": 1,
            "status": "skipped",
            "accepted": True,
            "summary": "No algorithm semantic reviewer was configured.",
            "findings": [],
            "reviewer": "none",
        }
    judgment = getattr(cycle, "agentic_judgment", None)
    summary = getattr(cycle, "summary", None)
    if judgment is None or not bool(getattr(judgment, "accepted", False)):
        return {
            "schema_version": 1,
            "status": "skipped",
            "accepted": True,
            "summary": "Semantic review waits for JA-accepted source.",
            "findings": [],
            "reviewer": type(reviewer).__name__,
        }
    if summary is None or summary.total <= 0 or summary.valid != summary.total:
        return {
            "schema_version": 1,
            "status": "skipped",
            "accepted": True,
            "summary": "Semantic review waits for a Core-valid candidate.",
            "findings": [],
            "reviewer": type(reviewer).__name__,
        }
    effective_candidate_key = candidate_key or _summary_objective_key_from_cycle(cycle)
    if incumbent_key is not None and effective_candidate_key and effective_candidate_key <= incumbent_key:
        return {
            "schema_version": 1,
            "status": "not_required",
            "accepted": True,
            "summary": "Semantic review is deferred because the Core-valid candidate is not strictly better than the incumbent.",
            "findings": [],
            "reviewer": type(reviewer).__name__,
        }
    result = reviewer.review(
        AlgorithmSemanticReviewRequest(
            round_index=round_index,
            attempt_index=attempt_index,
            context_packet_path=context_packet_path,
            worktree_path=Path(cycle.worktree_path),
            changed_files=list(getattr(cycle.worker_result, "changed_files", []) or []),
            direction_plan=direction_plan,
            candidate_summary=summary_payload(summary),
            output_dir=output_dir,
        )
    )
    return result.to_payload()


def semantic_review_blocks_promotion(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("status") in {"repair_required", "unavailable"} or value.get("accepted") is False


def semantic_review_requires_repair(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("status") == "repair_required" or (
        value.get("accepted") is False and value.get("status") != "unavailable"
    )


def semantic_review_promotion_block_reason(value: dict[str, Any] | None) -> str:
    if isinstance(value, dict) and value.get("status") == "unavailable":
        return "algorithm_semantic_review_unavailable"
    return "algorithm_semantic_review_repair_required"


def should_attempt_in_round_repair(
    cycle: Any,
    *,
    incumbent_key: tuple[float, ...] | None = None,
    semantic_review: dict[str, Any] | None = None,
) -> bool:
    """Return whether the same direction should spend another bounded attempt."""

    if is_nonrepairable_worker_failure(cycle):
        return False

    if semantic_review_requires_repair(semantic_review):
        return True

    judgment = getattr(cycle, "agentic_judgment", None)
    if judgment is not None and not bool(getattr(judgment, "accepted", False)):
        return True
    summary = getattr(cycle, "summary", None)
    if summary is None:
        return False
    total = int(getattr(summary, "total", 0) or 0)
    valid = int(getattr(summary, "valid", 0) or 0)
    failed = int(getattr(summary, "failed", 0) or 0)
    if total == 0:
        return False
    if failed > 0 or valid < total:
        return True
    candidate_key = _summary_objective_key_from_cycle(cycle)
    if incumbent_key is not None and candidate_key and candidate_key <= incumbent_key:
        return True
    return False


def is_nonrepairable_worker_failure(cycle: Any) -> bool:
    worker_result = getattr(cycle, "worker_result", None)
    if worker_result is None:
        return False
    status = str(getattr(worker_result, "status", "") or "")
    if status not in {"unavailable", "timeout", "failed_runtime", "authorization_required", "skipped"}:
        return False
    if getattr(worker_result, "changed_files", None):
        return False
    artifacts = getattr(worker_result, "artifacts", None) or {}
    return not bool(artifacts.get("proposal"))


def current_round_repair_feedback(
    *,
    attempt_index: int,
    max_repair_attempts: int,
    previous_attempts: list[dict[str, Any]],
    repair_anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recent = previous_attempts[-3:]
    legal_no_improvement = any(
        "legal_but_not_strictly_better" in (attempt.get("failure_signatures") or [])
        for attempt in recent
        if isinstance(attempt, dict)
    )
    anchor_quality_regression = any(
        "baseline_core_anchor_quality_regression" in (attempt.get("failure_signatures") or [])
        for attempt in recent
        if isinstance(attempt, dict)
    )
    status = "refinement_required" if legal_no_improvement or anchor_quality_regression else "repair_required"
    repair_targets = collect_current_round_repair_targets(previous_attempts)
    anchor_summary = (
        repair_anchor.get("summary")
        if isinstance(repair_anchor, dict) and isinstance(repair_anchor.get("summary"), dict)
        else {}
    )
    anchor_total = int(anchor_summary.get("total", 0) or 0)
    anchor_valid = int(anchor_summary.get("valid", 0) or 0)
    if isinstance(repair_anchor, dict) and repair_anchor and anchor_total > 0 and anchor_valid == anchor_total:
        repair_targets["baseline_core_valid_anchor"] = {
            "attempt_index": repair_anchor.get("attempt_index"),
            "candidate_key": repair_anchor.get("candidate_key") or [],
            "semantic_review": repair_anchor.get("semantic_review") or {},
            "rule": (
                "The candidate worktree for this repair was recreated from this best Core-valid attempt. "
                "Preserve its effective mechanisms and repair accumulated findings with the smallest coherent edit."
            ),
        }
    must_do = [
        "Treat the previous attempt as rejected inside this same direction; do not repeat its anchors, unsafe actions, or protected-fact regressions.",
        (
            "If the previous attempt was legal but not strictly better, keep the same direction and make a material refinement "
            "to the rule/operator mechanism before trying a new direction."
        ),
        "Repair the listed JA/evaluator issues before introducing an unrelated objective-improvement idea.",
        "Preserve the incumbent worktree and promoted mechanisms; make one bounded legal edit that can pass JA and smoke before Core scoring.",
    ]
    if repair_targets:
        must_do.append(
            "Repair the blocking items in repair_targets explicitly; do not rewrite unrelated working behavior."
        )
    if repair_targets.get("diagnostic_smoke_top_errors"):
        must_do.append(
            "Treat diagnostic_smoke_top_errors as concrete Core/evaluator evidence from the rejected attempt; repair those runtime/schema/legality errors before claiming the solver is ready."
        )
    if repair_targets.get("algorithm_semantic_review"):
        must_do.append(
            "Repair only the evidence-backed blocking findings in repair_targets.algorithm_semantic_review. Use the smallest "
            "coherent edit, preserve unrelated constructor and neighborhood behavior, and add the required behavioral tests. "
            "If the source overclaims a method that the direction did not request, narrow the claim instead of expanding the solver."
        )
    if repair_targets.get("baseline_core_valid_anchor"):
        must_do.append(
            "This repair starts from repair_targets.baseline_core_valid_anchor, not from the most recent degraded attempt. "
            "Keep the anchor's effective search structure, apply all accumulated repair targets, and do not accept a weaker Core objective merely to pass semantic review."
        )
    return {
        "status": status,
        "attempt_index": attempt_index,
        "max_repair_attempts": max_repair_attempts,
        "previous_attempts": [repair_attempt_context_payload(attempt) for attempt in recent],
        "repair_targets": repair_targets,
        "must_do": must_do,
        "avoid": sorted(
            {
                signature
                for attempt in recent
                for signature in (attempt.get("failure_signatures") or [])
                if isinstance(signature, str)
            }
        ),
    }


def repair_attempt_context_payload(attempt: dict[str, Any]) -> dict[str, Any]:
    """Keep repair evidence while removing non-blocking static diagnostics.

    `repair_targets` is the authoritative aggregation of JA/Core/semantic
    blockers. Repeating the entire JA checks object here would let advisory
    diagnostics bypass that filtering and become accidental worker tasks.
    """

    payload = dict(attempt)
    judgment = payload.get("agentic_judgment")
    if isinstance(judgment, dict):
        payload["agentic_judgment"] = {
            key: judgment.get(key)
            for key in ("accepted", "right", "stage", "issues", "suggestions")
            if key in judgment
        }
    return payload


def collect_current_round_repair_targets(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    targets: dict[str, Any] = {}

    def add_list(key: str, value: Any, *, limit: int = 8) -> None:
        if not isinstance(value, list) or not value:
            return
        existing = targets.setdefault(key, [])
        if not isinstance(existing, list):
            existing = []
            targets[key] = existing
        for item in value:
            if item not in existing:
                existing.append(item)
            if len(existing) >= limit:
                break

    def add_dict(key: str, value: Any, *, limit: int = 8) -> None:
        if not isinstance(value, dict) or not value:
            return
        existing = targets.setdefault(key, {})
        if not isinstance(existing, dict):
            existing = {}
            targets[key] = existing
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= limit:
                break
            existing[str(item_key)] = item_value

    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        judgment = attempt.get("agentic_judgment") if isinstance(attempt.get("agentic_judgment"), dict) else {}
        checks = judgment.get("checks") if isinstance(judgment.get("checks"), dict) else {}
        judgment_accepted = bool(judgment.get("accepted"))
        static_shape_soft_accepted = bool(checks.get("soft_accepted_by_diagnostic_smoke"))
        quality_risks = (
            checks.get("agent_generated_solver_blocking_quality_risks")
            if "agent_generated_solver_blocking_quality_risks" in checks
            else checks.get("agent_generated_solver_quality_risks")
        )
        if not judgment_accepted and not static_shape_soft_accepted:
            add_list("agent_generated_solver_quality_risks", quality_risks)
        if not judgment_accepted:
            add_list("agent_generated_solver_self_check_risks", checks.get("agent_generated_solver_self_check_risks"))
            add_list("incomplete_solution_acceptance_risks", checks.get("incomplete_solution_acceptance_risks"))
            add_list("protected_promoted_fact_regressions", checks.get("protected_promoted_fact_regressions"))
        add_dict("python_compile_errors", checks.get("python_compile_errors"))
        apply_rejections = checks.get("apply_rejections")
        if isinstance(apply_rejections, list):
            add_list("apply_rejections", apply_rejections)

        diagnostic_smoke = attempt.get("diagnostic_smoke") if isinstance(attempt.get("diagnostic_smoke"), dict) else {}
        diagnostic_summary = (
            diagnostic_smoke.get("summary")
            if isinstance(diagnostic_smoke.get("summary"), dict)
            else {}
        )
        diagnostic_validation = (
            diagnostic_summary.get("validation_summary")
            if isinstance(diagnostic_summary.get("validation_summary"), dict)
            else {}
        )
        add_list("diagnostic_smoke_top_errors", diagnostic_validation.get("top_errors"))

        semantic_review = attempt.get("semantic_review") if isinstance(attempt.get("semantic_review"), dict) else {}
        if semantic_review_blocks_promotion(semantic_review):
            semantic_target = targets.setdefault(
                "algorithm_semantic_review",
                {
                    "status": semantic_review.get("status"),
                    "summaries": [],
                    "blocking_findings": [],
                    "knowledge_paths": [],
                    "artifacts": {},
                },
            )
            semantic_target["status"] = semantic_review.get("status")
            summary_text = str(semantic_review.get("summary") or "").strip()
            if summary_text and summary_text not in semantic_target["summaries"]:
                semantic_target["summaries"].append(summary_text)
                semantic_target["summaries"] = semantic_target["summaries"][-4:]
            existing_finding_keys = {
                (
                    str(finding.get("finding_id") or ""),
                    str(finding.get("category") or ""),
                    str(finding.get("explanation") or ""),
                )
                for finding in semantic_target["blocking_findings"]
                if isinstance(finding, dict)
            }
            for finding in semantic_review.get("findings") or []:
                if not isinstance(finding, dict) or not finding.get("blocking"):
                    continue
                finding_key = (
                    str(finding.get("finding_id") or ""),
                    str(finding.get("category") or ""),
                    str(finding.get("explanation") or ""),
                )
                if finding_key not in existing_finding_keys:
                    semantic_target["blocking_findings"].append(finding)
                    existing_finding_keys.add(finding_key)
                if len(semantic_target["blocking_findings"]) >= 8:
                    break
            for knowledge_path in semantic_review.get("knowledge_paths") or []:
                if knowledge_path not in semantic_target["knowledge_paths"]:
                    semantic_target["knowledge_paths"].append(knowledge_path)
                if len(semantic_target["knowledge_paths"]) >= 12:
                    break
            semantic_target["artifacts"].update(semantic_review.get("artifacts") or {})

        quality_contract = checks.get("agent_generated_solver_quality_contract")
        if not judgment_accepted and isinstance(quality_contract, dict) and quality_contract.get("enabled"):
            expected_capabilities: list[str] = []
            for key in ("required_code_capabilities", "variant_required_code_capabilities"):
                for item in quality_contract.get(key) or []:
                    if isinstance(item, str) and item not in expected_capabilities:
                        expected_capabilities.append(item)
            targets["agent_generated_solver_expected_contract"] = {
                "active_features": (quality_contract.get("active_features") or [])[:16],
                "capabilities": expected_capabilities[:24],
                "capability_playbook": (quality_contract.get("capability_playbook") or [])[:24],
            }
    return targets


def round_attempt_payload(
    cycle: Any,
    *,
    attempt_index: int,
    context_packet_path: Path,
    incumbent_key: tuple[float, ...] | None = None,
    semantic_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    judgment = getattr(cycle, "agentic_judgment", None)
    analysis = getattr(cycle, "agentic_error_analysis", None)
    summary = getattr(cycle, "summary", None)
    worker_result = getattr(cycle, "worker_result", None)
    diagnostics = worker_proposal_diagnostics(worker_result) if worker_result is not None else {"status": "missing"}
    payload = {
        "attempt_index": attempt_index,
        "context_packet_path": str(context_packet_path),
        "worker_status": getattr(worker_result, "status", None),
        "changed_files": list(getattr(worker_result, "changed_files", []) or []),
        "candidate_key": list(_summary_objective_key_from_cycle(cycle)),
        "summary": compact_attempt_summary(summary),
        "diagnostic_smoke": compact_diagnostic_smoke(cycle),
        "agentic_judgment": judgment.to_payload() if judgment else None,
        "agentic_error_analysis": analysis.to_payload() if analysis else None,
        "proposal_diagnostics": diagnostics,
        "semantic_review": semantic_review or {},
        "failure_signatures": attempt_failure_signatures(
            cycle,
            diagnostics,
            incumbent_key=incumbent_key,
            semantic_review=semantic_review,
        ),
        "patch_path": str(getattr(cycle, "patch_path", "")),
        "delta_path": str(getattr(cycle, "delta_path", "")),
    }
    return payload


def compact_diagnostic_smoke(cycle: Any) -> dict[str, Any] | None:
    summary = getattr(cycle, "diagnostic_smoke_summary", None)
    if summary is None:
        return None
    output_dir = getattr(cycle, "diagnostic_smoke_output_dir", None)
    return {
        "diagnostic_only": True,
        "passed": bool(summary.total > 0 and summary.valid == summary.total),
        "summary": compact_attempt_summary(summary),
        "output_dir": str(output_dir) if output_dir else None,
    }


def _summary_objective_key_from_cycle(cycle: Any) -> tuple[float, ...]:
    summary = getattr(cycle, "summary", None)
    if summary is None:
        return ()
    best_metrics = getattr(summary, "best_metrics", None)
    if not best_metrics:
        return ()
    makespan = best_metrics.get("makespan") if isinstance(best_metrics, dict) else None
    if isinstance(makespan, (int, float)):
        return (-float(makespan),)
    return ()


def compact_attempt_summary(summary: RunSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "total": summary.total,
        "valid": summary.valid,
        "failed": summary.failed,
        "best_experiment_id": summary.best_experiment_id,
        "best_metrics": summary.best_metrics,
        "best_candidate_id": summary.best_candidate_id,
        "best_candidate_metrics": summary.best_candidate_metrics,
        "validation_summary": summary.validation_summary or {},
    }


def attempt_failure_signatures(
    cycle: Any,
    diagnostics: dict[str, Any],
    *,
    incumbent_key: tuple[float, ...] | None = None,
    semantic_review: dict[str, Any] | None = None,
) -> list[str]:
    signatures: list[str] = []
    if is_nonrepairable_worker_failure(cycle):
        signatures.append("worker_infrastructure_failure")
    judgment = getattr(cycle, "agentic_judgment", None)
    if judgment is not None and not bool(getattr(judgment, "accepted", False)):
        signatures.extend(str(item) for item in (getattr(judgment, "issues", []) or []) if item)
    summary = getattr(cycle, "summary", None)
    if summary is not None:
        total = int(getattr(summary, "total", 0) or 0)
        valid = int(getattr(summary, "valid", 0) or 0)
        failed = int(getattr(summary, "failed", 0) or 0)
        if total > 0 and (failed > 0 or valid < total):
            signatures.append("evaluator_invalid_candidate")
        candidate_key = _summary_objective_key_from_cycle(cycle)
        if (
            incumbent_key is not None
            and total > 0
            and valid == total
            and candidate_key
            and not _all_negative_infinity(candidate_key)
            and candidate_key <= incumbent_key
        ):
            signatures.append("legal_but_not_strictly_better")
    diagnostic_smoke = getattr(cycle, "diagnostic_smoke_summary", None)
    if diagnostic_smoke is not None:
        total = int(getattr(diagnostic_smoke, "total", 0) or 0)
        valid = int(getattr(diagnostic_smoke, "valid", 0) or 0)
        failed = int(getattr(diagnostic_smoke, "failed", 0) or 0)
        if total > 0 and (failed > 0 or valid < total):
            signatures.append("diagnostic_smoke_invalid_candidate")
    audit = diagnostics.get("proposal_audit") if isinstance(diagnostics, dict) else None
    if isinstance(audit, dict):
        signatures.extend(str(item) for item in (audit.get("warnings") or []) if item)
        if audit.get("rejected_change_count"):
            signatures.append("proposal_changes_rejected")
    if semantic_review_blocks_promotion(semantic_review):
        signatures.append(semantic_review_promotion_block_reason(semantic_review))
        for finding in semantic_review.get("findings") or []:
            if isinstance(finding, dict) and finding.get("blocking"):
                signatures.append(f"algorithm_semantic_{finding.get('category') or 'method_semantics'}")
    return _dedupe([_normalize_failure_token(item) for item in signatures if item])


def in_round_repair_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    repair_attempt_count = max(0, len(attempts) - 1)
    final_attempt = attempts[-1] if attempts else {}
    final_judgment = final_attempt.get("agentic_judgment") if isinstance(final_attempt, dict) else {}
    final_semantic_review = final_attempt.get("semantic_review") if isinstance(final_attempt, dict) else {}
    final_summary = final_attempt.get("summary") if isinstance(final_attempt, dict) else {}
    final_accepted = bool(isinstance(final_judgment, dict) and final_judgment.get("accepted"))
    final_total = int((final_summary or {}).get("total", 0) or 0) if isinstance(final_summary, dict) else 0
    final_valid = int((final_summary or {}).get("valid", 0) or 0) if isinstance(final_summary, dict) else 0
    return {
        "attempt_count": len(attempts),
        "repair_attempt_count": repair_attempt_count,
        "recovered": bool(
            repair_attempt_count
            and final_accepted
            and not semantic_review_blocks_promotion(
                final_semantic_review if isinstance(final_semantic_review, dict) else {}
            )
            and (final_total == 0 or final_valid == final_total)
        ),
        "final_attempt_index": final_attempt.get("attempt_index"),
        "attempts": attempts,
    }


def normalize_baseline_source(value: str) -> str:
    normalized = str(value or "current_project").strip().lower().replace("-", "_")
    if normalized in {"agent", "agent_generated", "agent_written", "generated"}:
        return "agent_generated"
    return "current_project"


def agent_generated_baseline_is_accepted(
    baseline_generation: dict[str, Any] | None,
    *,
    baseline_summary: RunSummary,
    baseline_key: tuple[float, ...],
) -> bool:
    if not isinstance(baseline_generation, dict) or baseline_generation.get("source") != "agent_generated":
        return False
    judgment = (
        baseline_generation.get("agentic_judgment")
        if isinstance(baseline_generation.get("agentic_judgment"), dict)
        else {}
    )
    semantic_review = (
        baseline_generation.get("semantic_review")
        if isinstance(baseline_generation.get("semantic_review"), dict)
        else {}
    )
    return (
        baseline_generation.get("status") == "ok"
        and bool(judgment.get("accepted"))
        and not semantic_review_blocks_promotion(semantic_review)
        and baseline_summary.total > 0
        and baseline_summary.valid == baseline_summary.total
        and not _all_negative_infinity(baseline_key)
    )


def agent_generated_baseline_cycle_is_core_accepted(cycle: Any) -> bool:
    judgment = getattr(cycle, "agentic_judgment", None)
    summary = getattr(cycle, "summary", None)
    return (
        judgment is not None
        and bool(getattr(judgment, "accepted", False))
        and summary is not None
        and int(getattr(summary, "total", 0) or 0) > 0
        and int(getattr(summary, "valid", 0) or 0) == int(getattr(summary, "total", 0) or 0)
    )


def select_agent_generated_baseline_cycle(
    cycles: list[tuple[int, Any, Path, dict[str, Any]]],
    *,
    objectives: list[ObjectiveSpec],
) -> tuple[int, Any, Path, dict[str, Any]]:
    if not cycles:
        raise RuntimeError("agent-generated baseline did not record any attempts")
    return max(
        cycles,
        key=lambda item: agent_generated_baseline_cycle_rank(
            item[1],
            attempt_index=item[0],
            objectives=objectives,
            semantic_review=item[3],
        ),
    )


def select_best_core_valid_baseline_cycle(
    cycles: list[tuple[int, Any, Path, dict[str, Any]]],
    *,
    objectives: list[ObjectiveSpec],
) -> tuple[int, Any, Path, dict[str, Any]] | None:
    core_valid = [item for item in cycles if agent_generated_baseline_cycle_is_core_accepted(item[1])]
    if not core_valid:
        return None
    return max(
        core_valid,
        key=lambda item: (
            *summary_objective_key(item[1].summary, objectives),
            item[0],
        ),
    )


def agent_generated_baseline_cycle_rank(
    cycle: Any,
    *,
    attempt_index: int,
    objectives: list[ObjectiveSpec],
    semantic_review: dict[str, Any] | None = None,
) -> tuple[float, ...]:
    judgment = getattr(cycle, "agentic_judgment", None)
    summary = getattr(cycle, "summary", None)
    worker_result = getattr(cycle, "worker_result", None)
    changed_files = list(getattr(worker_result, "changed_files", []) or [])
    has_changed_files = bool(changed_files)
    agentic_accepted = bool(judgment is not None and getattr(judgment, "accepted", False))
    core_total = int(getattr(summary, "total", 0) or 0) if summary is not None else 0
    core_valid = int(getattr(summary, "valid", 0) or 0) if summary is not None else 0
    diagnostic = getattr(cycle, "diagnostic_smoke_summary", None)
    diagnostic_total = int(getattr(diagnostic, "total", 0) or 0) if diagnostic is not None else 0
    diagnostic_valid = int(getattr(diagnostic, "valid", 0) or 0) if diagnostic is not None else 0
    artifacts = getattr(worker_result, "artifacts", None) or {}

    scored_summary = summary if core_total > 0 and core_valid == core_total else diagnostic
    objective_key = summary_objective_key(scored_summary, objectives) if scored_summary is not None else ()
    if agentic_accepted and core_total > 0 and core_valid == core_total:
        semantic_rank = 900 if semantic_review_blocks_promotion(semantic_review) else 1000
        return (semantic_rank, *objective_key, attempt_index)
    if agentic_accepted and has_changed_files:
        return (400, *objective_key, attempt_index)
    if core_total > 0 and core_valid == core_total and has_changed_files:
        return (350, *objective_key, attempt_index)
    if diagnostic_total > 0 and diagnostic_valid == diagnostic_total and has_changed_files:
        return (300, *objective_key, attempt_index)
    if has_changed_files:
        return (200, *objective_key, attempt_index)
    if artifacts.get("proposal"):
        return (100, *objective_key, attempt_index)
    return (0, *objective_key, attempt_index)


def agent_generated_baseline_selection_reason(
    cycle: Any,
    semantic_review: dict[str, Any] | None = None,
) -> str:
    if agent_generated_baseline_cycle_is_core_accepted(cycle):
        if semantic_review_blocks_promotion(semantic_review):
            return "core_evaluator_valid_but_algorithm_semantic_repair_required"
        return "agentic_judgment_accepted_and_core_evaluator_valid"
    worker_result = getattr(cycle, "worker_result", None)
    diagnostic = getattr(cycle, "diagnostic_smoke_summary", None)
    if (
        diagnostic is not None
        and int(getattr(diagnostic, "total", 0) or 0) > 0
        and int(getattr(diagnostic, "valid", 0) or 0) == int(getattr(diagnostic, "total", 0) or 0)
        and getattr(worker_result, "changed_files", None)
    ):
        return "changed_candidate_with_valid_diagnostic_smoke"
    if getattr(worker_result, "changed_files", None):
        return "latest_changed_candidate"
    artifacts = getattr(worker_result, "artifacts", None) or {}
    if artifacts.get("proposal"):
        return "candidate_with_proposal_artifact"
    return "last_recorded_candidate"


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
    semantic_reviewer: AlgorithmSemanticReviewer | None = None,
    direction_plan: dict[str, Any] | None = None,
    repair_attempts: int = DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
) -> tuple[RunSummary, Path, dict[str, Any]]:
    """先让 Coding Agent 写出初始 solver，再测量 baseline。

    生成阶段与后续增量改进不同：Core 只提供只读 parser/evaluator 和知识
    上下文，工作区中不存在历史 incumbent solver。首个 baseline 必须来自
    Coding Agent 实际创建的契约入口。
    """

    baseline_dir = output_dir / "agent_generated_baseline"
    source_project, hidden_incumbent_files = prepare_agent_generated_baseline_source_project(
        project_root=project_root,
        contract=contract,
        output_dir=baseline_dir,
    )
    max_repair_attempts = max(0, int(repair_attempts))
    baseline_context_path = baseline_dir / "context_packet.json"
    attempts: list[dict[str, Any]] = []
    try:
        cycle: Any | None = None
        cycle_attempts: list[tuple[int, Any, Path, dict[str, Any]]] = []
        repair_project_root = source_project
        repair_anchor_attempt_index: int | None = None
        for attempt_index in range(max_repair_attempts + 1):
            attempt_dir = baseline_dir if attempt_index == 0 else baseline_dir / f"repair_{attempt_index:03d}"
            repair_anchor_attempt = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.get("attempt_index") == repair_anchor_attempt_index
                ),
                None,
            )
            repair_feedback = (
                current_round_repair_feedback(
                    attempt_index=attempt_index,
                    max_repair_attempts=max_repair_attempts,
                    previous_attempts=attempts,
                    repair_anchor=repair_anchor_attempt,
                )
                if attempt_index > 0
                else None
            )
            baseline_context_path = write_baseline_generation_context_packet(
                base_context_packet_path=context_packet_path,
                output_path=attempt_dir / "context_packet.json",
                hidden_incumbent_files=hidden_incumbent_files,
                current_round_repair=repair_feedback,
                direction_plan=direction_plan,
            )
            cycle = run_worker_cycle(
                contract=contract,
                project_root=repair_project_root,
                output_dir=attempt_dir,
                context_packet_path=baseline_context_path,
                worker=worker,
                experiment_id=f"{experiment_id}_agent_generated_baseline_attempt_{attempt_index:02d}",
                max_steps=max_steps,
                max_runtime_seconds=max_runtime_seconds,
                apply_worker_changes=True,
            )
            prior_core_anchor = select_best_core_valid_baseline_cycle(
                cycle_attempts,
                objectives=contract.objectives,
            )
            candidate_key = summary_objective_key(cycle.summary, contract.objectives)
            prior_anchor_key = (
                summary_objective_key(prior_core_anchor[1].summary, contract.objectives)
                if prior_core_anchor is not None
                else ()
            )
            anchor_quality_regressed = bool(
                agent_generated_baseline_cycle_is_core_accepted(cycle)
                and candidate_key
                and prior_anchor_key
                and candidate_key < prior_anchor_key
            )
            if anchor_quality_regressed:
                semantic_review = {
                    "schema_version": 1,
                    "status": "quality_regressed",
                    "accepted": False,
                    "summary": (
                        "Semantic review was skipped because this repair regressed below the best Core-valid "
                        "baseline anchor. Restore anchor quality before requesting another method review."
                    ),
                    "findings": [],
                    "reviewer": "baseline_core_anchor_gate",
                    "anchor_attempt_index": prior_core_anchor[0],
                    "anchor_key": list(prior_anchor_key),
                    "candidate_key": list(candidate_key),
                }
            else:
                semantic_review = run_algorithm_semantic_review(
                    reviewer=semantic_reviewer,
                    cycle=cycle,
                    context_packet_path=baseline_context_path,
                    direction_plan=direction_plan or baseline_semantic_direction_plan(baseline_context_path),
                    round_index=-1,
                    attempt_index=attempt_index,
                    output_dir=attempt_dir / "semantic_review",
                )
            attempt_payload = round_attempt_payload(
                cycle,
                attempt_index=attempt_index,
                context_packet_path=baseline_context_path,
                semantic_review=semantic_review,
            )
            attempt_payload["repair_base_attempt_index"] = repair_anchor_attempt_index
            attempts.append(attempt_payload)
            cycle_attempts.append((attempt_index, cycle, baseline_context_path, semantic_review))
            best_core_anchor = select_best_core_valid_baseline_cycle(
                cycle_attempts,
                objectives=contract.objectives,
            )
            semantic_passed = agent_generated_baseline_cycle_is_core_accepted(
                cycle
            ) and not semantic_review_blocks_promotion(semantic_review)
            if anchor_quality_regressed and prior_core_anchor is not None:
                attempt_payload["failure_signatures"] = _dedupe(
                    [
                        *(attempt_payload.get("failure_signatures") or []),
                        "baseline_core_anchor_quality_regression",
                    ]
                )
                attempt_payload["baseline_core_anchor_quality"] = {
                    "anchor_attempt_index": prior_core_anchor[0],
                    "anchor_key": list(prior_anchor_key),
                    "candidate_key": list(candidate_key),
                    "repair_required": True,
                }
            if semantic_passed and not anchor_quality_regressed:
                break
            should_repair = anchor_quality_regressed or should_attempt_in_round_repair(
                cycle,
                semantic_review=semantic_review,
            )
            if attempt_index >= max_repair_attempts or not should_repair:
                break
            if best_core_anchor is not None:
                repair_anchor_attempt_index = best_core_anchor[0]
                repair_project_root = Path(best_core_anchor[1].worktree_path)
            else:
                repair_anchor_attempt_index = attempt_index
                repair_project_root = Path(cycle.worktree_path)
        if cycle is None:
            raise RuntimeError("agent-generated baseline did not produce a candidate")
        selected_attempt_index, selected_cycle, selected_context_path, selected_semantic_review = select_agent_generated_baseline_cycle(
            cycle_attempts,
            objectives=contract.objectives,
        )
        repair_summary = in_round_repair_summary(attempts)
        repair_summary["selected_attempt_index"] = selected_attempt_index
        repair_summary["selection_reason"] = agent_generated_baseline_selection_reason(
            selected_cycle,
            selected_semantic_review,
        )
        if selected_attempt_index != repair_summary.get("final_attempt_index"):
            repair_summary["final_attempt_superseded"] = True
        generation_payload = {
            "status": "ok",
            "source": "agent_generated",
            "cycle_dir": str(baseline_dir),
            "final_cycle_dir": str(Path(selected_cycle.patch_path).parent),
            "selected_attempt_index": selected_attempt_index,
            "context_packet_path": str(selected_context_path),
            "source_project": str(source_project),
            "hidden_incumbent_files": hidden_incumbent_files,
            "worktree": str(selected_cycle.worktree_path),
            "worker_status": selected_cycle.worker_result.status,
            "worker_changed_files": selected_cycle.worker_result.changed_files,
            "proposal_diagnostics": worker_proposal_diagnostics(selected_cycle.worker_result),
            "in_round_repair": repair_summary,
            "summary": summary_payload(selected_cycle.summary),
            "agentic_judgment": selected_cycle.agentic_judgment.to_payload(),
            "agentic_error_analysis": selected_cycle.agentic_error_analysis.to_payload()
            if selected_cycle.agentic_error_analysis
            else None,
            "semantic_review": selected_semantic_review,
            "direction_plan": direction_plan or {},
        }
        return selected_cycle.summary, selected_cycle.worktree_path, generation_payload
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


def baseline_semantic_direction_plan(context_packet_path: Path) -> dict[str, Any]:
    """Describe baseline review without embedding problem-family algorithm rules."""

    loaded = load_context_packet(context_packet_path).effective_context
    return {
        "schema_version": 1,
        "direction_id": "agent_generated_baseline",
        "title": "Verify generated baseline method claims against retrieved contracts",
        "strategy_type": "baseline_constructor",
        "hypothesis": str(loaded.get("hypothesis") or "Generate a legal standalone baseline solver.")[:1200],
        "knowledge_paths": list(loaded.get("auto_knowledge_cards") or [])[:12],
        "method_package_id": (
            (loaded.get("active_method_package") or {}).get("package_id")
            or (loaded.get("method_package_catalog") or {}).get("recommended_package_id")
        ),
    }


def plan_agent_generated_baseline_direction(
    *,
    planner: DirectionPlanningAgent,
    context_packet_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    feedback = {
        "round_type": "agent_generated_baseline",
        "baseline_source": "agent_generated",
        "instructions": [
            "Choose one compatible method package before the Coding Agent writes the initial solver.",
            "Generate from active IO and requirements; do not copy instance-specific schedules or scores.",
        ],
    }
    try:
        return planner.plan_direction(
            DirectionPlanRequest(
                round_index=-1,
                context_packet_path=context_packet_path,
                loop_feedback=feedback,
                output_dir=output_dir,
            )
        )
    except Exception as exc:  # noqa: BLE001 - baseline planning must retain deterministic fallback.
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "planner_exception.txt").write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        return EvidenceDrivenMainAgent().plan_direction(
            DirectionPlanRequest(
                round_index=-1,
                context_packet_path=context_packet_path,
                loop_feedback=feedback,
                output_dir=output_dir,
            )
        )


def prepare_agent_generated_baseline_source_project(
    *,
    project_root: Path,
    contract: TaskContract,
    output_dir: Path,
) -> tuple[Path, list[str]]:
    source_project = output_dir / "source_project_without_incumbent_solvers"
    domain_pack = get_domain_pack(contract.problem_family)
    if domain_pack is None or not domain_pack.agent_generated_baseline_preserve_paths:
        raise ValueError(
            f"problem family {contract.problem_family!r} does not declare an agent-generated baseline workspace"
        )
    preserve_paths = list(domain_pack.agent_generated_baseline_preserve_paths)
    preserve_paths.extend(_contract_relative_baseline_inputs(contract))
    baseline_contract = replace(
        contract,
        paths=replace(
            contract.paths,
            allowed_paths=sorted(set(preserve_paths)),
        ),
    )
    prepare_candidate_worktree(
        project_root=project_root.resolve(),
        contract=baseline_contract,
        worktree_path=source_project,
    )
    hidden: list[str] = []
    for relative in domain_pack.agent_generated_baseline_hidden_paths:
        source_reference = project_root / relative
        target = source_project / relative
        if source_reference.exists() and source_reference.is_file():
            hidden.append(relative)
        if target.exists() and target.is_file():
            target.unlink()
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


def _contract_relative_baseline_inputs(contract: TaskContract) -> list[str]:
    """Keep non-algorithm task inputs that are stored inside the project."""

    paths = _relative_python_entrypoints(contract.commands.evaluator)
    for instance in contract.instances:
        if not instance.path.is_absolute():
            paths.append(instance.path.as_posix())
    for resource_path in contract.resources.values():
        if not resource_path.is_absolute():
            paths.append(resource_path.as_posix())
    return paths


def _relative_python_entrypoints(command: str) -> list[str]:
    paths: list[str] = []
    for token in shlex.split(command, posix=False):
        normalized = token.strip("\"'")
        path = Path(normalized)
        if path.suffix.lower() != ".py" or path.is_absolute() or ".." in path.parts:
            continue
        paths.append(path.as_posix())
    return paths


def write_baseline_generation_context_packet(
    *,
    base_context_packet_path: Path,
    output_path: Path,
    hidden_incumbent_files: list[str] | None = None,
    current_round_repair: dict[str, Any] | None = None,
    direction_plan: dict[str, Any] | None = None,
) -> Path:
    loaded_packet = load_context_packet(base_context_packet_path)
    packet = loaded_packet.effective_context
    parent_hash = loaded_packet.raw.get("packet_hash") or loaded_packet.integrity["actual_packet_hash"]
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
            "If that entrypoint file does not exist in this baseline worktree, use create_or_replace with full file content; do not use text_replace or insert anchors against a nonexistent file.",
            "Reuse fixed parser/evaluator helper APIs when the context exposes them.",
            "Treat LB/UB/BKS as diagnostics only; optimize the declared objective.",
            "When a method package is selected, preserve its executable decoder, neighborhood, tabu, adaptive-search, and diversification structure; do not reduce it to a ready-list-only solver.",
        ],
        "hidden_incumbent_files": hidden_incumbent_files or [],
    }
    if direction_plan:
        refreshed["loop_feedback"] = {
            "round_index": "agent_generated_baseline",
            "current_direction_plan": direction_plan,
            "instructions": [
                "Implement the selected method package against the active IO and solver contract.",
                "Do not blend a second method family into this baseline direction.",
            ],
        }
        activate_method_package_context(refreshed, direction_plan=direction_plan)
    if current_round_repair:
        refreshed["loop_feedback"] = {
            "round_index": "agent_generated_baseline",
            "current_direction_plan": direction_plan or {},
            "current_round_repair": current_round_repair,
            "instructions": [
                "This is an in-baseline repair attempt. The worktree starts from the previous baseline-generation candidate; repair that candidate before Core measures baseline.",
                "Keep this as baseline generation, not incumbent improvement: create a complete legal solver entrypoint from docs/IO.",
                "If prior apply_rejections say target file does not exist, the next proposal must create_or_replace the full solver entrypoint instead of using text_replace/insert anchors.",
            ],
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
        "The first measured baseline must come from worker-written code, not from an existing incumbent solver. "
        "During baseline generation or repair, missing solver entrypoints require create_or_replace with full content. "
        "If a method package is active, adapt its complete implementation structure to the active IO and CLI. "
        "Any method claim must be supported by reachable behavior and the package's required tests."
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
    baseline_generation: dict[str, Any] | None = None,
    current_round_repair: dict[str, Any] | None = None,
    current_direction_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered_objectives = sorted(contract.objectives, key=lambda item: item.priority)
    previous_round_payloads = [round_record_payload(item) for item in previous_rounds]
    baseline_memory = agent_generated_baseline_memory_payload(
        baseline_generation,
        baseline_key=baseline_key,
    )
    baseline_round_payload = baseline_memory.get("round_payload") if isinstance(baseline_memory, dict) else None
    history_round_payloads = (
        [baseline_round_payload] if isinstance(baseline_round_payload, dict) else []
    ) + previous_round_payloads
    direction_graph = summarize_direction_graph(history_round_payloads)
    experience_memory = build_experience_memory(
        history_round_payloads,
        problem_family=contract.problem_family,
    )
    protected_facts = protected_baseline_generation_facts(baseline_memory) + protected_promoted_facts(previous_rounds)
    payload = {
        "purpose": "Provide evaluator-backed history for the next coding-worker proposal.",
        "round_semantics": {
            "user_visible_round": "improvement_direction",
            "core_atomic_unit": "worker_attempt",
            "rule": (
                "One outer loop round is one hypothesis direction. Same-direction repair/refinement attempts "
                "must be consumed before switching to an unrelated direction."
            ),
        },
        "round_index": round_index,
        "current_direction": {
            "direction_id": f"d{round_index:03d}",
            "attempt_budget": "bounded_by_in_round_repair_attempts",
            "status": "planned" if current_direction_plan else "planning",
            "rule": "Implement one planned method direction, then repair or refine it inside this direction before moving on.",
        },
        "current_direction_plan": current_direction_plan or {},
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
        "agent_generated_baseline_memory": baseline_memory,
        "previous_rounds": previous_round_payloads,
        "direction_graph": direction_graph,
        "experience_memory": experience_memory,
        "skill_usage_summary": experience_memory.get("skill_usage_summary") or {},
        "protected_promoted_facts": protected_facts[-8:],
        "failure_memory": round_failure_memory(previous_rounds),
        "next_round_guidance": next_round_guidance(
            previous_rounds,
            has_agent_generated_baseline=bool(baseline_memory.get("accepted_as_incumbent")),
        ),
        "instructions": [
            "Use only Core evaluator metrics as promotion evidence.",
            "Treat the outer loop index as a direction lifecycle, not a single blind patch.",
            "Implement current_direction_plan as the controlling experiment contract; do not replace it with an unrelated worker-authored method.",
            "Preserve successful ideas from promoted rounds unless a better alternative is justified.",
            "If agent_generated_baseline_memory is present, treat its recovered baseline mechanisms as incumbent structure to preserve before adding a new heuristic.",
            "Treat protected_promoted_facts as mechanisms to preserve; do not remove or disable them in the next proposal unless the proposal explicitly ablates them with a legality-preserving fallback.",
            "Treat failure_memory.must_avoid as hard negative memory for the next proposal, not as optional report text.",
            "Use direction_graph and experience_memory to choose whether to preserve, mutate, or prune prior directions.",
            "Follow next_round_guidance.must_do before selecting a new code change.",
            "Do not repeat rolled-back edits unchanged; explain what is materially different if revisiting them.",
            "If promotion_check failed, treat the candidate as a noisy or unstable improvement and change the rule-level idea.",
            "Use proposal_diagnostics to inspect whether prior proposals used project_intake, touched solver or validator files, or missed quick-test guidance.",
            "Prefer small, reversible solver changes whose effect can be attributed in the next evaluator run.",
        ],
    }
    if current_round_repair:
        payload["current_round_repair"] = current_round_repair
        payload["instructions"].insert(
            0,
            "This is an in-round repair attempt. First repair current_round_repair.previous_attempts before trying a new optimization idea.",
        )
    return payload


def agent_generated_baseline_memory_payload(
    baseline_generation: dict[str, Any] | None,
    *,
    baseline_key: tuple[float, ...],
) -> dict[str, Any]:
    """Return prompt-safe memory from agent-generated baseline creation.

    Baseline generation is not a normal improvement round, but its repair
    attempts often contain the most important parser/representation/decoder
    lessons for the first true improvement round. Keep only method-level
    diagnostics and artifact paths; never copy solver source into memory.
    """

    if not isinstance(baseline_generation, dict) or baseline_generation.get("source") != "agent_generated":
        return {}
    summary = baseline_generation.get("summary") if isinstance(baseline_generation.get("summary"), dict) else {}
    repair = (
        baseline_generation.get("in_round_repair")
        if isinstance(baseline_generation.get("in_round_repair"), dict)
        else {}
    )
    best_core_valid_anchor = best_core_valid_baseline_anchor(repair)
    diagnostics = (
        baseline_generation.get("proposal_diagnostics")
        if isinstance(baseline_generation.get("proposal_diagnostics"), dict)
        else {}
    )
    agentic_judgment = (
        baseline_generation.get("agentic_judgment")
        if isinstance(baseline_generation.get("agentic_judgment"), dict)
        else {}
    )
    semantic_review = (
        baseline_generation.get("semantic_review")
        if isinstance(baseline_generation.get("semantic_review"), dict)
        else {}
    )
    final_key = list(baseline_key)
    valid = int(summary.get("valid", 0) or 0)
    total = int(summary.get("total", 0) or 0)
    accepted_as_incumbent = (
        baseline_generation.get("status") == "ok"
        and bool(agentic_judgment.get("accepted"))
        and not semantic_review_blocks_promotion(semantic_review)
        and total > 0
        and valid == total
        and not _all_negative_infinity(final_key)
    )
    round_payload = {
        "round_index": -1,
        "decision": "baseline_incumbent" if accepted_as_incumbent else "rolled_back",
        "candidate_key": final_key,
        "incumbent_key_after": final_key,
        "worker_status": baseline_generation.get("worker_status"),
        "worker_changed_files": baseline_generation.get("worker_changed_files") or [],
        "proposal_fingerprint": _hash_json(diagnostics) if diagnostics else "",
        "duplicate_proposal": False,
        "proposal_diagnostics": {
            **diagnostics,
            "summary": diagnostics.get("summary") or "Agent-generated baseline creation.",
            "in_round_repair": repair,
        },
        "candidate_summary": summary,
        "smoke_gate": {
            "enabled": total > 0,
            "passed": bool(total > 0 and valid == total),
            "full_evaluation_started": bool(total > 0 and valid == total),
            "summary": summary,
        },
        "promotion_check": {
            "status": "baseline_generation",
            "reason": "accepted_as_initial_incumbent" if accepted_as_incumbent else "baseline_not_valid",
            "promoted": False,
        },
        "cycle_dir": baseline_generation.get("cycle_dir"),
        "context_packet_path": baseline_generation.get("context_packet_path"),
        "delta_path": "",
        "patch_path": "",
        "promoted_worktree": baseline_generation.get("worktree") if accepted_as_incumbent else None,
        "semantic_review": semantic_review,
        "best_core_valid_anchor": best_core_valid_anchor,
    }
    return {
        "status": baseline_generation.get("status"),
        "accepted_as_incumbent": accepted_as_incumbent,
        "baseline_key": final_key,
        "worker_status": baseline_generation.get("worker_status"),
        "worker_changed_files": baseline_generation.get("worker_changed_files") or [],
        "repair_attempt_count": int(repair.get("repair_attempt_count", 0) or 0),
        "repair_recovered": bool(repair.get("recovered")),
        "agentic_accepted": agentic_judgment.get("accepted"),
        "agentic_issues": (agentic_judgment.get("issues") or [])[:8],
        "semantic_review": semantic_review,
        "best_core_valid_anchor": best_core_valid_anchor,
        "proposal_summary": diagnostics.get("summary"),
        "strategy_intent": diagnostics.get("strategy_intent"),
        "rule_operator_hypotheses": (diagnostics.get("rule_operator_hypotheses") or [])[:6],
        "round_payload": round_payload,
        "protection_rule": (
            "This generated baseline is the measured incumbent. Preserve its parser, operation representation, "
            "constructor, decoder, output schema, and active variant repairs unless loop feedback identifies them "
            "as the direct failure source."
        ),
    }


def best_core_valid_baseline_anchor(repair: dict[str, Any]) -> dict[str, Any]:
    """Keep the strongest Core-valid baseline attempt even before semantic repair.

    This anchor is evidence for preserving effective search structure, not a
    promotion candidate.  Promotion still requires a passing semantic review.
    """

    best_rank: tuple[float, ...] | None = None
    best_attempt: dict[str, Any] | None = None
    for value in repair.get("attempts") or []:
        if not isinstance(value, dict):
            continue
        summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
        total = int(summary.get("total", 0) or 0)
        valid = int(summary.get("valid", 0) or 0)
        raw_key = value.get("candidate_key") or []
        try:
            key = tuple(float(item) for item in raw_key)
        except (TypeError, ValueError):
            continue
        if total <= 0 or valid != total or not key or not all(math.isfinite(item) for item in key):
            continue
        semantic = value.get("semantic_review") if isinstance(value.get("semantic_review"), dict) else {}
        semantic_accepted = not semantic_review_blocks_promotion(semantic)
        attempt_index = int(value.get("attempt_index", -1) or 0)
        rank = (*key, 1.0 if semantic_accepted else 0.0, float(attempt_index))
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_attempt = value
    if best_rank is None or best_attempt is None:
        return {}

    semantic = (
        best_attempt.get("semantic_review")
        if isinstance(best_attempt.get("semantic_review"), dict)
        else {}
    )
    context_path = str(best_attempt.get("context_packet_path") or "").strip()
    worktree = str((Path(context_path).parent / "candidate_worktree").resolve()) if context_path else ""
    return {
        "attempt_index": best_attempt.get("attempt_index"),
        "objective_key": list(best_rank[:-2]),
        "core_valid": True,
        "semantic_status": semantic.get("status"),
        "promotion_eligible": not semantic_review_blocks_promotion(semantic),
        "semantic_summary": str(semantic.get("summary") or "")[:800],
        "context_packet_path": context_path,
        "candidate_worktree": worktree,
        "patch_path": str(best_attempt.get("patch_path") or ""),
        "rule": (
            "Preserve effective mechanisms from this Core-valid anchor while repairing its semantic findings. "
            "Do not promote it until semantic review passes."
        ),
    }


def protected_baseline_generation_facts(baseline_memory: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(baseline_memory, dict) or not baseline_memory.get("accepted_as_incumbent"):
        return []
    facts: list[dict[str, Any]] = []
    hypotheses = baseline_memory.get("rule_operator_hypotheses") or []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        name = str(hypothesis.get("name") or "").strip()
        if not name:
            continue
        facts.append(
            {
                "round_index": -1,
                "name": name[:160],
                "type": str(hypothesis.get("type") or "agent_generated_baseline")[:80],
                "target_files": [
                    str(path).replace("\\", "/")
                    for path in (hypothesis.get("target_files") or [])
                    if isinstance(path, str) and path.strip()
                ][:8],
                "novelty": str(hypothesis.get("novelty") or "")[:500],
                "expected_effect": str(hypothesis.get("expected_effect") or "")[:500],
                "protection_rule": baseline_memory.get("protection_rule"),
            }
        )
    if not facts:
        facts.append(
            {
                "round_index": -1,
                "name": "agent_generated_baseline_incumbent",
                "type": "baseline_constructor",
                "target_files": baseline_memory.get("worker_changed_files") or [],
                "novelty": "Initial solver generated from IO, requirements, diagnostics, and knowledge cards.",
                "expected_effect": "Provide the legal incumbent skeleton for subsequent incremental improvement.",
                "protection_rule": baseline_memory.get("protection_rule"),
            }
        )
    return facts


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
        "smoke_gate": item.smoke_gate,
        "promotion_check": item.promotion_check,
        "cycle_dir": item.cycle_dir,
        "context_packet_path": item.context_packet_path,
        "delta_path": item.delta_path,
        "patch_path": item.patch_path,
        "promoted_worktree": item.promoted_worktree,
        "direction_plan": item.direction_plan or {},
        "semantic_review": item.semantic_review or {},
    }


def protected_promoted_facts(previous_rounds: list[LoopRoundRecord], *, limit: int = 8) -> list[dict[str, Any]]:
    """Return promoted rule/operator mechanisms that later rounds should not casually remove."""

    facts: list[dict[str, Any]] = []
    for item in previous_rounds:
        if item.decision != "promoted":
            continue
        diagnostics = item.proposal_diagnostics if isinstance(item.proposal_diagnostics, dict) else {}
        hypotheses = diagnostics.get("rule_operator_hypotheses") or []
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            name = str(hypothesis.get("name") or "").strip()
            if not name:
                continue
            facts.append(
                {
                    "round_index": item.round_index,
                    "name": name[:160],
                    "type": str(hypothesis.get("type") or "")[:80],
                    "target_files": [
                        str(path).replace("\\", "/")
                        for path in (hypothesis.get("target_files") or [])
                        if isinstance(path, str) and path.strip()
                    ][:8],
                    "novelty": str(hypothesis.get("novelty") or "")[:500],
                    "expected_effect": str(hypothesis.get("expected_effect") or "")[:500],
                    "protection_rule": (
                        "Preserve this Core-promoted mechanism in later edits. "
                        "If a proposal changes it, keep a legality-preserving fallback and explain the ablation."
                    ),
                }
            )
    return facts[-limit:]


def round_failure_memory(previous_rounds: list[LoopRoundRecord], *, limit: int = 8) -> dict[str, Any]:
    """Summarize recent evaluator and JA failures as explicit negative memory."""

    failures: list[dict[str, Any]] = []
    for item in previous_rounds:
        if item.decision != "rolled_back":
            continue
        signatures = round_failure_signatures(item)
        if not signatures:
            signatures = ["candidate_not_strictly_better"]
        failures.append(
            {
                "round_index": item.round_index,
                "failure_signatures": signatures,
                "hypotheses": [
                    {
                        "name": str(hypothesis.get("name") or "")[:120],
                        "type": str(hypothesis.get("type") or "")[:80],
                    }
                    for hypothesis in (
                        (item.proposal_diagnostics or {}).get("rule_operator_hypotheses") or []
                    )
                    if isinstance(hypothesis, dict)
                ][:4],
                "changed_files": item.worker_changed_files[:8],
                "candidate_key": list(item.candidate_key),
                "incumbent_key_after": list(item.incumbent_key_after),
                "summary": _bounded_text((item.proposal_diagnostics or {}).get("summary"), limit=300),
            }
        )
    recent_failures = failures[-limit:]
    must_avoid = sorted({signature for failure in recent_failures for signature in failure["failure_signatures"]})
    return {
        "status": "available" if recent_failures else "empty",
        "recent_failures": recent_failures,
        "must_avoid": must_avoid,
        "rule": (
            "Do not repeat a recent rolled-back mechanism unchanged. If revisiting a failed family, "
            "state the concrete representation, legality, or neighborhood change that makes it different."
        ),
    }


def next_round_guidance(
    previous_rounds: list[LoopRoundRecord],
    *,
    has_agent_generated_baseline: bool = False,
) -> dict[str, Any]:
    """Convert loop history into compact mandatory guidance for the next worker call."""

    promoted = [item for item in previous_rounds if item.decision == "promoted"]
    rolled_back = [item for item in previous_rounds if item.decision == "rolled_back"]
    recent_signatures = [
        signature
        for item in previous_rounds[-6:]
        for signature in round_failure_signatures(item)
    ]
    valid_non_improving = [
        item
        for item in rolled_back
        if not _all_negative_infinity(item.candidate_key)
        and (item.smoke_gate or {}).get("passed")
        and not semantic_review_blocks_promotion(item.semantic_review)
    ]
    must_do = [
        "Start from the current promoted incumbent; make one small incremental edit.",
        "State 1-3 concrete rule/operator hypotheses before code, with target files.",
        "Compile changed Python files mentally and structurally: no dangling try/def blocks, no top-level helper inserted inside another function.",
        "If adding local search or decoder logic, verify full operation coverage before scoring or replacing the incumbent schedule.",
    ]
    if promoted:
        must_do.append("Preserve promoted mechanisms unless the proposal explicitly provides a legal fallback.")
    if has_agent_generated_baseline:
        must_do.append(
            "Preserve the agent-generated baseline's parser, operation representation, constructor, decoder, output schema, and active variant repairs; improve by adding one bounded rule/operator around that skeleton."
        )
    if any("no_changed_files_after_apply" in item for item in recent_signatures):
        must_do.append("Submit an actual accepted edit; an empty or fully rejected proposal is not a useful iteration.")
    if any("python_syntax_error" in item for item in recent_signatures):
        must_do.append("Prefer a small helper file or insert_before a top-level def; avoid fragile indentation-heavy patches.")
    if any("protected_promoted_fact_regression" in item for item in recent_signatures):
        must_do.append("Do not remove the promoted setup-aware dispatch/list-scheduler mechanism.")
    if any("algorithm_semantic_review_repair_required" in item for item in recent_signatures):
        must_do.append(
            "Repair the evidence-backed algorithm semantic findings and run their required behavioral tests before restating the method claim."
        )
    if len(valid_non_improving) >= 1:
        must_do.append(
            "A legal no-improvement round means the previous mechanism is saturated; ask the Main Agent for a "
            "materially different method-level direction grounded in retrieved knowledge, not another cosmetic tweak."
        )
    avoid = sorted(set(recent_signatures))
    return {
        "status": "available" if previous_rounds else "empty",
        "must_do": must_do,
        "avoid": avoid,
        "preferred_direction": (
            "Follow the next Main Agent direction and its selected method package. The orchestration layer does not "
            "choose a problem-specific operator; it only rejects unchanged rolled-back mechanisms."
        ),
    }


def round_failure_signatures(item: LoopRoundRecord) -> list[str]:
    signatures: list[str] = []
    if item.duplicate_proposal:
        signatures.append("duplicate_proposal")
    if not item.worker_changed_files:
        signatures.append("no_changed_files_after_apply")
    if _all_negative_infinity(item.candidate_key):
        signatures.append("invalid_or_rejected_candidate")

    candidate_validation = (item.candidate_summary or {}).get("validation_summary")
    if isinstance(candidate_validation, dict):
        judgment = candidate_validation.get("agentic_judgment")
        if isinstance(judgment, dict):
            for issue in judgment.get("issues") or []:
                signatures.append(_failure_token(str(issue)))
        for error in candidate_validation.get("top_errors") or []:
            signatures.append(_failure_token(_error_text(error)))

    smoke_summary = (item.smoke_gate or {}).get("summary")
    if isinstance(smoke_summary, dict):
        validation = smoke_summary.get("validation_summary")
        if isinstance(validation, dict):
            for error in validation.get("top_errors") or []:
                signatures.append(_failure_token(_error_text(error)))

    audit = (item.proposal_diagnostics or {}).get("proposal_audit")
    if isinstance(audit, dict):
        if audit.get("rejected_change_count"):
            signatures.append("proposal_changes_rejected")
        for warning in audit.get("warnings") or []:
            signatures.append(_failure_token(str(warning)))
    if semantic_review_blocks_promotion(item.semantic_review):
        signatures.append("algorithm_semantic_review_repair_required")
        for finding in (item.semantic_review or {}).get("findings") or []:
            if isinstance(finding, dict) and finding.get("blocking"):
                signatures.append(
                    _failure_token(f"algorithm_semantic_{finding.get('category') or 'method_semantics'}")
                )
    if (
        item.decision == "rolled_back"
        and not _all_negative_infinity(item.candidate_key)
        and not semantic_review_blocks_promotion(item.semantic_review)
    ):
        signatures.append("legal_but_not_strictly_better")
    return _dedupe([signature for signature in signatures if signature])


def _all_negative_infinity(values: tuple[float, ...] | list[Any]) -> bool:
    if not values:
        return False
    return all(isinstance(value, (int, float)) and float(value) == float("-inf") for value in values)


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("error") or error.get("message") or error)
    return str(error)


def _failure_token(text: str, *, limit: int = 120) -> str:
    lowered = text.strip().replace("\\", "/")
    lowered = _normalize_failure_token(lowered)
    if len(lowered) > limit:
        lowered = lowered[:limit].rstrip("_")
    return lowered or "unknown_failure"


def _normalize_failure_token(text: str) -> str:
    token = text.lower()
    token = re.sub(r"f:/[^ |)]+", "path", token)
    token = re.sub(r"line \d+", "line", token)
    token = re.sub(r"\d+", "n", token)
    token = re.sub(r"[^a-z0-9_]+", "_", token)
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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


def cycle_smoke_gate_payload(cycle: Any) -> dict[str, Any]:
    summary = cycle.smoke_summary
    return {
        "enabled": summary is not None,
        "passed": bool(summary and summary.total > 0 and summary.valid == summary.total),
        "full_evaluation_started": bool(cycle.full_evaluation_started),
        "summary": summary_payload(summary) if summary else None,
        "output_dir": str(cycle.smoke_output_dir) if cycle.smoke_output_dir else None,
    }


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
    apply_rejections = compact_apply_rejections(proposal.get("apply_rejections"))
    proposal_changes = compact_proposal_changes(proposal.get("changes"))

    return {
        "status": "ok",
        "proposal_path": str(proposal_path),
        "summary": _bounded_text(proposal.get("summary")),
        "strategy_intent": _bounded_text(proposal.get("strategy_intent")),
        "rule_operator_hypotheses": compact_rule_operator_hypotheses(
            proposal.get("rule_operator_hypotheses") or [],
            limit=12,
        ),
        "apply_rejections": apply_rejections,
        "rejected_edits": rejected_proposal_edits(proposal_changes, apply_rejections),
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
            "solver_contract_self_check": compact_solver_contract_self_check_audit(
                audit.get("solver_contract_self_check")
            ),
            "agent_generated_unwired_helpers": _bounded_list(
                audit.get("agent_generated_unwired_helpers"),
                limit=12,
            ),
            "warnings": _bounded_list(audit.get("warnings"), limit=20),
        },
    }


def compact_solver_contract_self_check_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "required": value.get("required"),
        "present": value.get("present"),
        "changed_agent_generated_solver": value.get("changed_agent_generated_solver"),
        "missing_active_features": _bounded_list(value.get("missing_active_features"), limit=12),
        "missing_capabilities": _bounded_list(value.get("missing_capabilities"), limit=18),
        "missing_variant_handling": _bounded_list(value.get("missing_variant_handling"), limit=12),
        "missing_narrative_fields": _bounded_list(value.get("missing_narrative_fields"), limit=8),
        "capabilities_without_evidence": _bounded_list(value.get("capabilities_without_evidence"), limit=18),
        "capabilities_with_vague_evidence": _bounded_list(value.get("capabilities_with_vague_evidence"), limit=18),
        "capabilities_without_concrete_source_evidence": _bounded_list(
            value.get("capabilities_without_concrete_source_evidence"),
            limit=18,
        ),
        "capabilities_with_source_mismatch": _bounded_list(value.get("capabilities_with_source_mismatch"), limit=18),
        "narrative_without_concrete_source_evidence": _bounded_list(
            value.get("narrative_without_concrete_source_evidence"),
            limit=8,
        ),
        "narrative_with_source_mismatch": _bounded_list(value.get("narrative_with_source_mismatch"), limit=8),
        "warnings": _bounded_list(value.get("warnings"), limit=20),
    }


def compact_apply_rejections(value: Any, *, limit: int = 12) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = _bounded_text(item.get("path"), limit=500)
        reason = _bounded_text(item.get("reason"), limit=500)
        if path or reason:
            result.append({"path": path, "reason": reason})
        if len(result) >= limit:
            break
    return result


def compact_proposal_changes(value: Any, *, limit: int = 20) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact = {
            "path": _bounded_text(item.get("path"), limit=500),
            "action": _bounded_text(item.get("action"), limit=100),
        }
        for key in ("slot_id", "anchor", "old"):
            text = _bounded_text(item.get(key), limit=2400)
            if text:
                compact[key] = text
        result.append(compact)
        if len(result) >= limit:
            break
    return result


def rejected_proposal_edits(
    changes: list[dict[str, str]],
    apply_rejections: list[dict[str, str]],
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    """Keep exact failed edit anchors so a same-round repair does not guess again."""

    result: list[dict[str, str]] = []
    for rejection in apply_rejections:
        path = rejection.get("path", "")
        matching = [change for change in changes if change.get("path") == path]
        if not matching:
            result.append(dict(rejection))
            continue
        for change in matching:
            item = dict(rejection)
            item.update(change)
            result.append(item)
            if len(result) >= limit:
                return result
    return result[:limit]


def write_loop_report(*, output_dir: Path, result: WorkerLoopResult, problem_family: str | None = None) -> None:
    round_payloads = [round_record_payload(item) for item in result.rounds]
    direction_graph = summarize_direction_graph(round_payloads)
    experience_memory = build_experience_memory(round_payloads, problem_family=problem_family)
    skill_usage_records = experience_memory.get("skill_usage_records") or []
    payload = {
        "baseline_key": list(result.baseline_key),
        "final_key": list(result.final_key),
        "final_worktree": str(result.final_worktree),
        "baseline_source": result.baseline_source,
        "baseline_generation": result.baseline_generation,
        "baseline_summary": summary_payload(result.baseline_summary),
        "round_semantics": {
            "user_visible_round": "improvement_direction",
            "core_atomic_unit": "worker_attempt",
        },
        "hypothesis_graph": direction_graph,
        "experience_memory": experience_memory,
        "skill_usage_records": skill_usage_records,
        "rounds": round_payloads,
    }
    (output_dir / "loop_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "hypothesis_graph.json").write_text(
        json.dumps(direction_graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "hypothesis_graph.md").write_text(
        render_direction_graph_markdown(direction_graph),
        encoding="utf-8",
    )
    (output_dir / "experience_memory.json").write_text(
        json.dumps(experience_memory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "experience_memory.md").write_text(
        render_experience_memory_markdown(experience_memory),
        encoding="utf-8",
    )
    (output_dir / "skill_usage_records.json").write_text(
        json.dumps(skill_usage_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Worker Loop Report",
        "",
        f"- Baseline key: `{json.dumps(result.baseline_key, ensure_ascii=False)}`",
        f"- Final key: `{json.dumps(result.final_key, ensure_ascii=False)}`",
        f"- Final worktree: `{result.final_worktree}`",
        f"- Direction count: `{direction_graph.get('direction_count', 0)}`",
        f"- Attempt count: `{direction_graph.get('attempt_count', 0)}`",
        f"- Candidate lessons: `{len((experience_memory.get('memory_tiers') or {}).get('candidate_lessons') or [])}`",
        f"- Skill usage records: `{len(skill_usage_records)}`",
        "",
        "## Baseline",
        "",
        f"- Source: `{result.baseline_source}`",
        f"- Generation: `{json.dumps(result.baseline_generation or {}, ensure_ascii=False)}`",
        "",
        f"`{json.dumps(summary_payload(result.baseline_summary), ensure_ascii=False)}`",
        "",
        "## Rounds",
        "",
        "| Round | Decision | Worker | Duplicate Proposal | Smoke Gate | Semantic Review | Promotion Check | Proposal Audit | Candidate Key | Incumbent Key After | Context Packet | Worktree Delta | Changed Files |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in result.rounds:
        proposal_audit = compact_proposal_audit(item.proposal_diagnostics)
        lines.append(
            f"| {item.round_index} | {item.decision} | {item.worker_status} | "
            f"{'yes' if item.duplicate_proposal else 'no'} | "
            f"`{json.dumps(compact_smoke_gate(item.smoke_gate), ensure_ascii=False)}` | "
            f"`{json.dumps(compact_semantic_review(item.semantic_review), ensure_ascii=False)}` | "
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
            "Smoke Gate runs the first seed through the fixed evaluator before the full benchmark; failed smoke rounds skip the full evaluator run.",
        ]
    )
    lines.extend(
        [
            "",
            "## Direction Graph",
            "",
            f"- Direction decisions: `{json.dumps(direction_graph.get('decision_counts') or {}, ensure_ascii=False)}`",
            f"- Direction statuses: `{json.dumps(direction_graph.get('status_counts') or {}, ensure_ascii=False)}`",
            f"- Artifact: `{output_dir / 'hypothesis_graph.json'}`",
            "",
            "## Experience Memory",
            "",
            f"- Candidate lessons: `{len((experience_memory.get('memory_tiers') or {}).get('candidate_lessons') or [])}`",
            f"- Skill usage summary: `{json.dumps(experience_memory.get('skill_usage_summary') or {}, ensure_ascii=False)}`",
            f"- Artifact: `{output_dir / 'experience_memory.json'}`",
        ]
    )
    (output_dir / "loop_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compact_semantic_review(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    return {
        "status": value.get("status"),
        "accepted": value.get("accepted"),
        "summary": str(value.get("summary") or "")[:240],
        "blocking_finding_count": sum(
            1
            for finding in value.get("findings") or []
            if isinstance(finding, dict) and finding.get("blocking")
        ),
        "warning_finding_count": sum(
            1
            for finding in value.get("findings") or []
            if isinstance(finding, dict) and not finding.get("blocking")
        ),
        "artifacts": value.get("artifacts") or {},
    }


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
        "agent_generated_unwired_helpers": audit.get("agent_generated_unwired_helpers") or [],
        "warnings": audit.get("warnings") or [],
    }


def compact_smoke_gate(smoke_gate: dict[str, Any]) -> dict[str, Any]:
    summary = smoke_gate.get("summary") if isinstance(smoke_gate, dict) else None
    return {
        "enabled": bool(smoke_gate.get("enabled")) if isinstance(smoke_gate, dict) else False,
        "passed": bool(smoke_gate.get("passed")) if isinstance(smoke_gate, dict) else False,
        "full": bool(smoke_gate.get("full_evaluation_started")) if isinstance(smoke_gate, dict) else False,
        "total": summary.get("total") if isinstance(summary, dict) else None,
        "valid": summary.get("valid") if isinstance(summary, dict) else None,
        "failed": summary.get("failed") if isinstance(summary, dict) else None,
        "errors": ((summary.get("validation_summary") or {}).get("top_errors") or [])[:2]
        if isinstance(summary, dict)
        else [],
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
