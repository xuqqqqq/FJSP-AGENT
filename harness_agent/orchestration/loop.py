"""多轮闭环编排：方向规划、同轮修补、Core 复验、晋升/回滚和经验沉淀。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from harness_agent.context.compaction import compact_json
from harness_agent.context.loader import load_context_packet
from harness_agent.context.packet import (
    activate_direction_knowledge_context,
    activate_method_package_context,
    write_refreshed_context_packet,
)
from harness_agent.domains.pack import get_domain_pack
from harness_agent.core.cancellation import CancellationToken, TaskCancelled
from harness_agent.core.graph import GraphHarnessRunner
from harness_agent.agents.hypothesis import (
    build_experience_memory,
    compact_algorithm_semantic_review,
    render_direction_graph_markdown,
    render_experience_memory_markdown,
    summarize_direction_graph,
)
from harness_agent.core.ledger import ExperimentRecord
from harness_agent.agents.main import (
    DirectionPlanRequest,
    DirectionPlanningAgent,
    EvidenceDrivenMainAgent,
    RoundReflectionRequest,
    WorkerAssignmentRequest,
    method_implementation_bundle,
    request_worker_assignment,
    write_direction_plan,
)
from harness_agent.core.models import ObjectiveSpec, TaskContract
from harness_agent.core.runner import RunSummary
from harness_agent.agents.semantic import (
    AlgorithmSemanticReviewer,
    AlgorithmSemanticReviewRequest,
)
from harness_agent.worker import CodingWorker, WorkerResult
from harness_agent.orchestration.cycle import prepare_candidate_worktree, run_worker_cycle


DEFAULT_IN_ROUND_REPAIR_ATTEMPTS = 3


def planner_uses_fast_mode(planner: DirectionPlanningAgent) -> bool:
    return str(getattr(planner, "planning_mode", "")).strip().lower() == "fast"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LoopRoundRecord:
    """一个用户可见“方向轮”的最终证据快照。

    一轮内部可以有多次 Worker attempt，但这里只记录该方向最终用于
    promotion 判断的候选，以及方向计划、语义审查、patch 和 Core 指标。
    """

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
    mechanism_activation: dict[str, Any] | None = None
    round_reflection: dict[str, Any] | None = None
    worker_session_id: str | None = None


@dataclass(frozen=True)
class CandidateIncumbent:
    """Best observed candidate for one evidence tier, independent of promotion."""

    objective_key: tuple[float, ...]
    worktree: Path
    candidate_id: str
    round_index: int
    summary: dict[str, Any]
    activation_status: str | None = None


@dataclass(frozen=True)
class LaneDevelopmentState:
    """Persistent development lineage for one competing Coding Worker lane."""

    candidate_id: str
    method_family: str
    method_package_id: str
    checkpoint_worktree: Path
    objective_key: tuple[float, ...]
    track: str
    stage: int
    verified_components: list[str]
    session_id: str | None = None
    session_status: str = "not_started"
    event_stream_status: str = "unknown"
    last_failure: str | None = None
    last_update_round: int = -1


@dataclass(frozen=True)
class WorkerLoopResult:
    """完整闭环的返回值：初始 incumbent、最终 incumbent 和每轮证据。"""

    baseline_key: tuple[float, ...]
    final_key: tuple[float, ...]
    final_worktree: Path
    rounds: list[LoopRoundRecord]
    baseline_summary: RunSummary
    baseline_source: str = "current_project"
    baseline_generation: dict[str, Any] | None = None
    status: str = "ok"
    stop_reason: str | None = None
    best_legal_incumbent: CandidateIncumbent | None = None
    best_activated_incumbent: CandidateIncumbent | None = None
    lane_development_states: dict[str, LaneDevelopmentState] = field(default_factory=dict)


def materialize_selected_round_artifacts(
    *,
    cycle_dir: Path,
    context_packet_path: Path,
    delta_path: Path,
    patch_path: Path,
) -> None:
    """Keep legacy round-level aliases for the lane selected by competition."""

    cycle_dir.mkdir(parents=True, exist_ok=True)
    for source, name in (
        (context_packet_path, "context_packet.json"),
        (delta_path, "worker_worktree_delta.json"),
        (patch_path, "worker_changes.patch"),
    ):
        source = Path(source)
        target = cycle_dir / name
        if source.resolve() == target.resolve() or not source.is_file():
            continue
        target.write_bytes(source.read_bytes())


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
    worker_input_root: Path | None = None,
    in_round_repair_attempts: int = DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
    max_competing_workers: int = 4,
    round_intervention: Callable[[int, LoopRoundRecord, dict[str, Any]], Any] | None = None,
    cancellation: CancellationToken | None = None,
    resume_from: WorkerLoopResult | None = None,
) -> WorkerLoopResult:
    """运行完整闭环；每轮只推进一个方向，incumbent 始终由 Core 证据保护。

    主流程分成三段：先建立可运行 baseline，再按 Main Agent 方向生成候选，
    最后将 promotion/rollback 结果写入经验。函数不会把失败候选覆盖到仓库，
    `incumbent_worktree` 只有在 promotion check 通过时才前移。
    """

    if cancellation is not None:
        cancellation.raise_if_cancelled()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    direction_planner = main_agent or EvidenceDrivenMainAgent()
    max_competing_workers = max(1, min(4, int(max_competing_workers)))

    # 阶段 1：首次运行建立 baseline；续跑则恢复原始 baseline、完整历史和
    # Core 已晋升的 incumbent，绝不重新生成 baseline 或丢失旧轮次证据。
    if resume_from is not None:
        normalized_baseline_source = normalize_baseline_source(resume_from.baseline_source)
        baseline_generation = resume_from.baseline_generation
        baseline_summary = resume_from.baseline_summary
        baseline_worktree = resume_from.final_worktree.resolve()
        if not baseline_worktree.exists():
            raise FileNotFoundError(f"resume incumbent worktree does not exist: {baseline_worktree}")
    else:
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
                assignment_issuer=direction_planner,
                direction_plan=plan_agent_generated_baseline_direction(
                    planner=direction_planner,
                    context_packet_path=context_packet_path,
                    output_dir=output_dir / "agent_generated_baseline" / "main_agent",
                ),
                repair_attempts=worker_loop_repair_attempt_budget(
                    baseline_worker_for_generation,
                    in_round_repair_attempts,
                ),
                cancellation=cancellation,
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
                cancellation=cancellation,
            )
    baseline_key = (
        tuple(resume_from.baseline_key)
        if resume_from is not None
        else summary_objective_key(baseline_summary, contract.objectives)
    )
    incumbent_key = (
        tuple(resume_from.final_key)
        if resume_from is not None
        else summary_objective_key(baseline_summary, contract.objectives)
    )
    incumbent_worktree = baseline_worktree
    best_legal_incumbent = (
        resume_from.best_legal_incumbent
        or CandidateIncumbent(
            objective_key=tuple(resume_from.final_key),
            worktree=resume_from.final_worktree.resolve(),
            candidate_id="resumed_promoted_incumbent",
            round_index=max((item.round_index for item in resume_from.rounds), default=-1),
            summary={},
        )
        if resume_from is not None
        else candidate_incumbent_from_baseline(
            objective_key=incumbent_key,
            worktree=baseline_worktree,
            summary=baseline_summary,
        )
    )
    best_activated_incumbent = (
        resume_from.best_activated_incumbent if resume_from is not None else None
    )
    lane_development_states = dict(
        resume_from.lane_development_states or {}
        if resume_from is not None
        else {}
    )
    if (
        resume_from is None
        and normalized_baseline_source == "agent_generated"
        and not agent_generated_baseline_is_accepted(
            baseline_generation,
            baseline_summary=baseline_summary,
            baseline_key=incumbent_key,
        )
    ):
        stop_reason = agent_generated_baseline_failure_reason(
            baseline_generation,
            baseline_summary=baseline_summary,
            baseline_key=incumbent_key,
        )
        if isinstance(baseline_generation, dict):
            if baseline_generation.get("status") == "ok":
                baseline_generation["status"] = "rejected"
            baseline_generation["accepted_as_incumbent"] = False
            baseline_generation["failure_reason"] = stop_reason
            baseline_generation["stopped_before_rounds"] = True
            baseline_generation["stop_reason"] = stop_reason
        result = WorkerLoopResult(
            baseline_key=incumbent_key,
            final_key=incumbent_key,
            final_worktree=incumbent_worktree,
            rounds=[],
            baseline_summary=baseline_summary,
            baseline_source=normalized_baseline_source,
            baseline_generation=baseline_generation,
            status="baseline_generation_failed",
            stop_reason=stop_reason,
            best_legal_incumbent=best_legal_incumbent,
            best_activated_incumbent=best_activated_incumbent,
            lane_development_states=lane_development_states,
        )
        write_loop_report(output_dir=output_dir, result=result, problem_family=contract.problem_family)
        return result
    if (
        resume_from is None
        and normalized_baseline_source == "provided_project"
        and (
            baseline_summary.total <= 0
            or baseline_summary.valid != baseline_summary.total
            or _all_negative_infinity(incumbent_key)
        )
    ):
        if baseline_summary.total <= 0:
            stop_reason = "provided_project_evaluator_produced_no_results"
        elif baseline_summary.valid != baseline_summary.total:
            stop_reason = "provided_project_baseline_invalid"
        else:
            stop_reason = "provided_project_objective_missing"
        result = WorkerLoopResult(
            baseline_key=incumbent_key,
            final_key=incumbent_key,
            final_worktree=incumbent_worktree,
            rounds=[],
            baseline_summary=baseline_summary,
            baseline_source=normalized_baseline_source,
            baseline_generation=None,
            status="provided_baseline_failed",
            stop_reason=stop_reason,
            best_legal_incumbent=best_legal_incumbent,
            best_activated_incumbent=best_activated_incumbent,
            lane_development_states=lane_development_states,
        )
        write_loop_report(output_dir=output_dir, result=result, problem_family=contract.problem_family)
        return result
    # 阶段 2：每个外层 round 对应一个改进方向；repair attempt 不额外消耗轮数。
    effective_repair_attempts = worker_loop_repair_attempt_budget(worker, in_round_repair_attempts)
    if planner_uses_fast_mode(direction_planner):
        # A fast round is one parallel checkpoint. The selected lane continues
        # in the next outer round with its existing session and Core feedback.
        effective_repair_attempts = 0
    round_records = list(resume_from.rounds) if resume_from is not None else []
    seen_proposal_fingerprints = {
        item.proposal_fingerprint for item in round_records if item.proposal_fingerprint
    }
    first_round_index = max((item.round_index for item in round_records), default=-1) + 1
    for round_offset in range(max(0, iterations)):
        round_index = first_round_index + round_offset
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        cycle_dir = output_dir / f"round_{round_index:03d}"
        incumbent_key_before_round = incumbent_key
        round_context_packet_path = cycle_dir / "context_packet.json"
        in_round_attempts: list[dict[str, Any]] = []
        # Main Agent 先读取 incumbent 和历史成败，只规划方向，不直接写代码。
        planning_feedback = loop_feedback_payload(
            round_index=round_index,
            contract=contract,
            baseline_summary=baseline_summary,
            baseline_key=summary_objective_key(baseline_summary, contract.objectives),
            incumbent_key_before=incumbent_key,
            incumbent_worktree=incumbent_worktree,
            best_legal_incumbent=best_legal_incumbent,
            best_activated_incumbent=best_activated_incumbent,
            baseline_generation=baseline_generation,
            previous_rounds=round_records,
            current_round_repair=None,
            max_competing_workers=max_competing_workers,
        )
        # Main must plan from the actual promoted worktree, not from the immutable
        # pre-baseline packet. This refreshed planning-only packet carries an
        # incumbent file/hash summary while preserving the stable base prefix.
        planning_context_packet_path = write_refreshed_context_packet(
            base_context_packet_path=context_packet_path,
            output_path=cycle_dir / "main_agent_context_packet.json",
            loop_feedback=planning_feedback,
            project_root=incumbent_worktree,
        )
        # Coding Agent/JA/Core/语义审查的任意异常都转为本轮失败证据，
        # 不允许一次坏候选终止后续方向。
        direction_plan = plan_direction_with_fallback(
            planner=direction_planner,
            round_index=round_index,
            context_packet_path=planning_context_packet_path,
            loop_feedback=planning_feedback,
            output_dir=cycle_dir / "main_agent",
        )
        direction_plan = dict(direction_plan)
        if round_records:
            previous_plan = round_records[-1].direction_plan or {}
            previous_direction_id = previous_plan.get("direction_id")
            if previous_direction_id:
                direction_plan.setdefault("parent_direction_id", previous_direction_id)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        user_intervention: dict[str, Any] | None = None
        # Round 0 starts immediately. Later rounds pause only after Main has
        # reviewed the completed prior round and published a concrete proposal.
        if round_index > 0 and round_records and round_intervention is not None:
            user_direction = round_intervention(round_index, round_records[-1], direction_plan)
            if user_direction is not None and (
                not isinstance(user_direction, str) or user_direction.strip()
            ):
                user_intervention = normalize_user_intervention(
                    user_direction,
                    round_index=round_index,
                )
                planning_feedback = apply_user_intervention_to_feedback(
                    planning_feedback,
                    user_intervention=user_intervention,
                )
                intervention_action = user_intervention["direction_patch"]["action"]
                if intervention_action == "continue":
                    direction_plan = continue_current_direction_plan(
                        previous_direction_plan=previous_plan,
                        proposed_direction_plan=direction_plan,
                        round_index=round_index,
                    )
                    revision_dir = cycle_dir / "main_agent_user_revision"
                    revision_dir.mkdir(parents=True, exist_ok=True)
                    applied_plan_path = revision_dir / "applied_direction_plan.json"
                    applied_plan_path.write_text(
                        json.dumps(direction_plan, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    patch_audit = {
                        "schema_version": 1,
                        "status": "deterministic_continue",
                        "action": "continue",
                        "preserved_previous_direction": True,
                        "skipped_planner_revision": True,
                        "applied_plan_path": str(applied_plan_path.resolve()),
                    }
                    patch_path = revision_dir / "direction_patch.json"
                    patch_path.write_text(
                        json.dumps(patch_audit, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    user_intervention["direction_patch_path"] = str(patch_path.resolve())
                elif intervention_action != "accept":
                    original_direction_plan = direction_revision_base(
                        proposed_direction_plan=direction_plan,
                        previous_direction_plan=previous_plan,
                        user_intervention=user_intervention,
                    )
                    revision_dir = cycle_dir / "main_agent_user_revision"
                    revised_direction_plan = plan_direction_with_fallback(
                        planner=direction_planner,
                        round_index=round_index,
                        context_packet_path=planning_context_packet_path,
                        loop_feedback=planning_feedback,
                        output_dir=revision_dir,
                    )
                    direction_plan, patch_audit = apply_user_direction_revision(
                        original_direction_plan,
                        revised_direction_plan,
                        user_intervention=user_intervention,
                    )
                    revision_dir.mkdir(parents=True, exist_ok=True)
                    applied_plan_path = revision_dir / "applied_direction_plan.json"
                    applied_plan_path.write_text(
                        json.dumps(direction_plan, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    patch_audit["applied_plan_path"] = str(applied_plan_path.resolve())
                    patch_path = revision_dir / "direction_patch.json"
                    patch_path.write_text(
                        json.dumps(patch_audit, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    user_intervention["direction_patch_path"] = str(patch_path.resolve())
                direction_plan["user_intervention"] = user_intervention
        try:
            (
                cycle,
                round_context_packet_path,
                in_round_attempts,
                competition_result,
                selected_direction_plan,
            ) = run_competing_worker_cycles(
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
                assignment_issuer=direction_planner,
                worker_input_root=(worker_input_root or project_root),
                user_intervention=user_intervention,
                max_competing_workers=max_competing_workers,
                lane_development_states=lane_development_states,
                cancellation=cancellation,
            )
            direction_plan = dict(direction_plan)
            direction_plan["competition_result"] = competition_result
            direction_plan["selected_candidate_variant"] = selected_direction_plan.get("candidate_variant") or {}
            direction_plan["mechanism_activation"] = selected_direction_plan.get("mechanism_activation") or {}
            best_legal_incumbent = update_candidate_incumbent(
                best_legal_incumbent,
                competition_result.get("best_legal_candidate"),
                round_index=round_index,
            )
            best_activated_incumbent = update_candidate_incumbent(
                best_activated_incumbent,
                competition_result.get("best_activated_candidate"),
                round_index=round_index,
            )
            materialize_selected_round_artifacts(
                cycle_dir=cycle_dir,
                context_packet_path=round_context_packet_path,
                delta_path=cycle.delta_path,
                patch_path=cycle.patch_path,
            )
        except TaskCancelled:
            raise
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
            promotion_check = {
                "status": "skipped",
                "reason": "worker_exception",
                "promoted": False,
                "required_repeats": max(1, promotion_repeats),
            }
            competition_result = _load_json_object(cycle_dir / "competition_result.json") or {
                "status": "worker_exception",
                "candidates": [],
                "error": str(exc),
            }
            round_record = LoopRoundRecord(
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
                promotion_check=promotion_check,
                cycle_dir=str(cycle_dir),
                context_packet_path=str(round_context_packet_path),
                delta_path=str(delta_path),
                patch_path=str(patch_path),
                promoted_worktree=None,
                direction_plan=direction_plan,
                semantic_review=None,
            )
            reflection = reflect_on_completed_round(
                planner=direction_planner,
                request=RoundReflectionRequest(
                    round_index=round_index,
                    direction_plan=direction_plan,
                    competition_result=competition_result,
                    promotion_check=promotion_check,
                    incumbent_key_before=incumbent_key_before_round,
                    incumbent_key_after=incumbent_key,
                    output_dir=cycle_dir / "main_agent_reflection",
                ),
            )
            round_records.append(replace(round_record, round_reflection=reflection))
            continue

        # 阶段 3：将最终 attempt 归一化为轮级证据，并判断是否更新 incumbent。
        proposal_fingerprint = worker_proposal_fingerprint(cycle.worker_result)
        duplicate_proposal = proposal_fingerprint in seen_proposal_fingerprints
        seen_proposal_fingerprints.add(proposal_fingerprint)
        proposal_diagnostics = worker_proposal_diagnostics(cycle.worker_result)
        repair_summary = in_round_repair_summary(in_round_attempts)
        if repair_summary["repair_attempt_count"]:
            proposal_diagnostics["in_round_repair"] = repair_summary
            proposal_diagnostics["local_trials"] = repair_summary
        selected_attempt = next(
            (
                item
                for item in in_round_attempts
                if isinstance(item, dict) and item.get("disposition") == "selected"
            ),
            in_round_attempts[-1] if in_round_attempts else None,
        )
        semantic_review = (
            selected_attempt.get("semantic_review")
            if isinstance(selected_attempt, dict)
            else None
        )
        if isinstance(semantic_review, dict):
            proposal_diagnostics["algorithm_semantic_review"] = semantic_review
        candidate_key = summary_objective_key(cycle.summary, contract.objectives)
        mechanism_activation = (
            direction_plan.get("mechanism_activation")
            if isinstance(direction_plan.get("mechanism_activation"), dict)
            else {}
        )
        if competition_result.get("selected_for_promotion") is False:
            promotion_check = {
                "status": "skipped",
                "reason": "no_eligible_competition_candidate",
                "promoted": False,
                "required_repeats": max(1, promotion_repeats),
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
                cancellation=cancellation,
            )
        if isinstance(semantic_review, dict):
            promotion_check["semantic_review_advisory"] = semantic_review
        promoted = bool(promotion_check.get("promoted"))
        # 只有 promotion check 能修改 incumbent 指针；rollback 只保留产物。
        if promoted:
            incumbent_key = tuple(float(item) for item in promotion_check.get("accepted_key", candidate_key))
            incumbent_worktree = cycle.worktree_path
        round_record = LoopRoundRecord(
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
            mechanism_activation=mechanism_activation,
            worker_session_id=str(competition_result.get("selected_session_id") or "") or None,
        )
        reflection = reflect_on_completed_round(
            planner=direction_planner,
            request=RoundReflectionRequest(
                round_index=round_index,
                direction_plan=direction_plan,
                competition_result=competition_result,
                promotion_check=promotion_check,
                incumbent_key_before=incumbent_key_before_round,
                incumbent_key_after=incumbent_key,
                output_dir=cycle_dir / "main_agent_reflection",
            ),
        )
        round_records.append(replace(round_record, round_reflection=reflection))

    result = WorkerLoopResult(
        baseline_key=baseline_key,
        final_key=incumbent_key,
        final_worktree=incumbent_worktree,
        rounds=round_records,
        baseline_summary=baseline_summary,
        baseline_source=normalized_baseline_source,
        baseline_generation=baseline_generation,
        best_legal_incumbent=best_legal_incumbent,
        best_activated_incumbent=best_activated_incumbent,
        lane_development_states=lane_development_states,
    )
    write_loop_report(output_dir=output_dir, result=result, problem_family=contract.problem_family)
    return result


def run_competing_worker_cycles(
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
    direction_plan: dict[str, Any],
    semantic_reviewer: AlgorithmSemanticReviewer | None,
    assignment_issuer: DirectionPlanningAgent,
    worker_input_root: Path,
    user_intervention: dict[str, Any] | None,
    max_competing_workers: int,
    initial_session_id: str | None = None,
    initial_session_candidate_id: str | None = None,
    lane_development_states: dict[str, LaneDevelopmentState] | None = None,
    cancellation: CancellationToken | None = None,
) -> tuple[Any, Path, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Evaluate isolated Coding Worker variants and return the best eligible lane."""

    lane_states = lane_development_states if lane_development_states is not None else {}
    candidate_plans = competitive_direction_plans(
        direction_plan,
        limit=max_competing_workers,
        lane_development_states=lane_states,
        incumbent_worktree=project_root,
        incumbent_key=incumbent_key,
    )
    multiple = len(candidate_plans) > 1

    def run_candidate(
        indexed_plan: tuple[int, dict[str, Any]],
    ) -> tuple[
        int,
        dict[str, Any],
        tuple[tuple[float, ...], bool, Any, Path, list[dict[str, Any]], dict[str, Any]] | None,
    ]:
        candidate_index, candidate_plan = indexed_plan
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        variant = candidate_plan.get("candidate_variant") or {}
        candidate_id = str(variant.get("candidate_id") or f"c{candidate_index:02d}")
        candidate_dir = output_dir if not multiple else output_dir / "candidates" / candidate_id
        parent_state, archived_state = lane_development_state_for_incumbent(
            lane_states.get(candidate_id),
            candidate_plan=candidate_plan,
            incumbent_worktree=project_root,
            incumbent_key=incumbent_key,
        )
        parent_worktree = (
            parent_state.checkpoint_worktree
            if parent_state is not None and parent_state.checkpoint_worktree.exists()
            else project_root
        )
        requested_session_id = (
            parent_state.session_id
            if parent_state is not None
            else initial_session_id
            if initial_session_id
            and (
                candidate_id == initial_session_candidate_id
                or (not initial_session_candidate_id and candidate_index == 0)
            )
            else None
        )
        try:
            cycle, context_path, attempts = run_worker_cycle_with_in_round_repairs(
                contract=contract,
                project_root=parent_worktree,
                worker=worker,
                output_dir=candidate_dir,
                base_context_packet_path=base_context_packet_path,
                round_index=round_index,
                experiment_id=f"{experiment_id}__{candidate_id}",
                max_steps=max_steps,
                max_runtime_seconds=max_runtime_seconds,
                apply_worker_changes=apply_worker_changes,
                baseline_summary=baseline_summary,
                incumbent_key=incumbent_key,
                semantic_review_floor_key=(
                    parent_state.objective_key if parent_state is not None else incumbent_key
                ),
                baseline_generation=baseline_generation,
                previous_rounds=previous_rounds,
                repair_attempts=repair_attempts,
                direction_plan=candidate_plan,
                semantic_reviewer=semantic_reviewer,
                assignment_issuer=assignment_issuer,
                worker_input_root=worker_input_root,
                user_intervention=user_intervention,
                initial_session_id=requested_session_id,
                cancellation=cancellation,
            )
            key = summary_objective_key(cycle.summary, contract.objectives)
            selected_attempt = next(
                (
                    item
                    for item in attempts
                    if isinstance(item, dict) and item.get("disposition") == "selected"
                ),
                attempts[-1] if attempts else None,
            )
            semantic_review = (
                selected_attempt.get("semantic_review")
                if isinstance(selected_attempt, dict)
                else None
            )
            ja_accepted = bool(cycle.agentic_judgment.accepted)
            core_eligible = not _all_negative_infinity(key)
            semantic_eligible = not semantic_review_blocks_promotion(semantic_review)
            mechanism_activation = evaluate_mechanism_activation(candidate_plan, cycle.summary)
            activation_required = activation_contract_required(candidate_plan)
            # Preserve the activation verdict as audit evidence, but do not
            # include it in the promotion eligibility decision.
            activation_eligible = (
                mechanism_activation.get("passed") is True
                if activation_required
                else mechanism_activation.get("passed") is not False
            )
            worker_changed_files = list(getattr(cycle.worker_result, "changed_files", []) or [])
            target_file = str(candidate_plan.get("target_file") or "")
            target_changed = bool(
                target_file in worker_changed_files if target_file else worker_changed_files
            )
            exact_execution = evaluate_exact_solver_execution(candidate_plan, cycle.summary)
            exact_execution_eligible = exact_execution.get("passed") is not False
            eligible = core_eligible and target_changed and exact_execution_eligible
            outcome = {
                "candidate_id": candidate_id,
                "candidate_index": candidate_index,
                # The candidate cycle produced a Core-evaluable outcome. Keep
                # that lifecycle status separate from the underlying Worker
                # process status used by lane checkpoint accounting.
                "status": "completed",
                "eligible": eligible,
                "ja_accepted": ja_accepted,
                "ja_advisory_only": True,
                "ja_stage": cycle.agentic_judgment.stage,
                "ja_issues": list(cycle.agentic_judgment.issues),
                "core_eligible": core_eligible,
                "semantic_eligible": semantic_eligible,
                "activation_eligible": activation_eligible,
                "activation_required": activation_required,
                "activation_advisory_only": True,
                "mechanism_activation": mechanism_activation,
                "exact_execution_eligible": exact_execution_eligible,
                "exact_execution": exact_execution,
                "objective_key": list(key),
                "worker_status": cycle.worker_result.status,
                "worker_changed_files": worker_changed_files,
                "target_changed": target_changed,
                "worker_model": worker_model_from_result(cycle.worker_result),
                "summary": summary_payload(cycle.summary),
                "smoke_gate": cycle_smoke_gate_payload(cycle),
                "proposal_diagnostics": compact_round_proposal_diagnostics(
                    worker_proposal_diagnostics(cycle.worker_result)
                ),
                "local_trials": in_round_repair_summary(attempts),
                "worker_session_id": str((selected_attempt or {}).get("session_id") or "") or None,
                "requested_session_id": (selected_attempt or {}).get("requested_session_id"),
                "command_session_id": (selected_attempt or {}).get("command_session_id"),
                "observed_session_id": (selected_attempt or {}).get("observed_session_id"),
                "session_reused": bool((selected_attempt or {}).get("session_reused")),
                "session_event_stream_bytes": (selected_attempt or {}).get("session_event_stream_bytes"),
                "semantic_review": semantic_review or {},
                "cycle_dir": str(candidate_dir),
                "worktree": str(cycle.worktree_path),
                "patch_path": str(cycle.patch_path),
                "parent_checkpoint": str(parent_worktree),
                "parent_objective_key": list(
                    parent_state.objective_key if parent_state is not None else incumbent_key
                ),
                "archived_lineage": (
                    lane_development_state_payload(archived_state)
                    if archived_state is not None
                    else None
                ),
            }
            candidate_plan = dict(candidate_plan)
            candidate_plan["mechanism_activation"] = mechanism_activation
            completed = (key, eligible, cycle, context_path, attempts, candidate_plan)
            return candidate_index, outcome, completed
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - one failed lane must not cancel its competitors.
            candidate_dir.mkdir(parents=True, exist_ok=True)
            exception_path = candidate_dir / "candidate_exception.txt"
            exception_path.write_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                encoding="utf-8",
            )
            outcome = {
                "candidate_id": candidate_id,
                "candidate_index": candidate_index,
                "status": "failed",
                "eligible": False,
                "objective_key": [float("-inf") for _ in contract.objectives],
                "error": str(exc),
                "exception_path": str(exception_path),
                "cycle_dir": str(candidate_dir),
                "parent_checkpoint": str(parent_worktree),
                "parent_objective_key": list(
                    parent_state.objective_key if parent_state is not None else incumbent_key
                ),
                "archived_lineage": (
                    lane_development_state_payload(archived_state)
                    if archived_state is not None
                    else None
                ),
            }
            return candidate_index, outcome, None

    indexed_plans = list(enumerate(candidate_plans))
    concurrency = min(max(1, int(max_competing_workers)), len(indexed_plans))
    if concurrency == 1:
        candidate_results = [run_candidate(item) for item in indexed_plans]
    else:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="coding-candidate") as executor:
            candidate_results = list(executor.map(run_candidate, indexed_plans))

    candidate_results.sort(key=lambda item: item[0])
    outcomes = [item[1] for item in candidate_results]
    update_lane_development_states(
        lane_states,
        candidate_plans=candidate_plans,
        outcomes=outcomes,
        incumbent_worktree=project_root,
        incumbent_key=incumbent_key,
        round_index=round_index,
    )
    completed = [item[2] for item in candidate_results if item[2] is not None]
    eligible_completed = [item for item in completed if item[1]]
    selection_pool = eligible_completed or completed
    if not selection_pool:
        result = {
            "status": "all_candidates_failed",
            "candidate_count": len(candidate_plans),
            "execution_mode": "parallel" if concurrency > 1 else "serial",
            "max_concurrency": concurrency,
            "candidates": outcomes,
            "lane_development_states": lane_development_states_payload(lane_states),
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "competition_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError("all competing Coding Worker candidates failed before Core selection")
    winner = max(selection_pool, key=lambda item: item[0])
    has_eligible_winner = bool(eligible_completed)
    selected_plan = winner[5]
    selected_variant = selected_plan.get("candidate_variant") or {}
    winner_attempts = winner[4]
    winner_attempt = next(
        (
            item
            for item in winner_attempts
            if isinstance(item, dict) and item.get("disposition") == "selected"
        ),
        winner_attempts[-1] if winner_attempts else {},
    )
    best_legal_candidate = best_competition_candidate(
        outcomes,
        predicate=lambda item: bool(item.get("core_eligible")),
    )
    best_activated_candidate = best_competition_candidate(
        outcomes,
        predicate=lambda item: (
            bool(item.get("core_eligible"))
            and (item.get("mechanism_activation") or {}).get("passed") is True
            and item.get("exact_execution_eligible") is not False
        ),
    )
    winner_outcome = next(
        (
            item
            for item in outcomes
            if item.get("candidate_id") == (selected_variant.get("candidate_id") or "c00")
        ),
        {},
    )
    winner_lane_state = (
        winner_outcome.get("lane_development_state")
        if isinstance(winner_outcome.get("lane_development_state"), dict)
        else {}
    )
    continued_session_id = (
        str(winner_lane_state.get("session_id") or "") or None
        if winner_lane_state.get("session_status") == "continued"
        else None
    )
    result = {
        "status": "selected" if has_eligible_winner else "no_eligible_candidate",
        "candidate_count": len(candidate_plans),
        "eligible_candidate_count": len(eligible_completed),
        "execution_mode": "parallel" if concurrency > 1 else "serial",
        "max_concurrency": concurrency,
        "selected_candidate_id": (
            (selected_variant.get("candidate_id") or "c00") if has_eligible_winner else None
        ),
        "selected_objective_key": list(winner[0]) if has_eligible_winner else [],
        "measured_candidate_id": selected_variant.get("candidate_id") or "c00",
        "measured_objective_key": list(winner[0]),
        "continued_session_id": continued_session_id,
        "continued_session_candidate_id": (
            (selected_variant.get("candidate_id") or "c00")
            if continued_session_id
            else None
        ),
        "selected_session_id": str(winner_attempt.get("session_id") or "") or None,
        "selected_for_promotion": has_eligible_winner,
        "selected_for_promotion_check": has_eligible_winner,
        "selection_rule": (
            "best Core objective among changed Core-legal isolated candidates; exact_hybrid candidates "
            "must prove diagnostics.cp_sat_called=true; semantic review and other activation checks are advisory"
        ),
        "activation_advisory_only": True,
        "best_legal_candidate": best_legal_candidate,
        "best_activated_candidate": best_activated_candidate,
        "candidates": outcomes,
        "lane_development_states": lane_development_states_payload(lane_states),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "competition_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return winner[2], winner[3], winner[4], result, selected_plan


def best_competition_candidate(
    outcomes: list[dict[str, Any]],
    *,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    eligible = [
        item
        for item in outcomes
        if item.get("status") == "completed"
        and predicate(item)
        and not _all_negative_infinity(tuple(float(value) for value in item.get("objective_key") or []))
    ]
    if not eligible:
        return None
    winner = max(eligible, key=lambda item: tuple(float(value) for value in item["objective_key"]))
    return {
        "candidate_id": winner.get("candidate_id"),
        "objective_key": list(winner.get("objective_key") or []),
        "worktree": winner.get("worktree"),
        "summary": winner.get("summary") or {},
        "mechanism_activation": winner.get("mechanism_activation") or {},
        "exact_execution_eligible": winner.get("exact_execution_eligible"),
        "exact_execution": winner.get("exact_execution") or {},
        "ja_accepted": winner.get("ja_accepted"),
        "semantic_eligible": winner.get("semantic_eligible"),
    }


def reusable_lane_development_state(
    state: LaneDevelopmentState | None,
    *,
    candidate_plan: dict[str, Any],
) -> tuple[LaneDevelopmentState | None, LaneDevelopmentState | None]:
    """Return a reusable same-package state and an optional archived pivot state."""

    if state is None:
        return None, None
    method_family = str(candidate_plan.get("method_family") or "")
    method_package_id = str(candidate_plan.get("method_package_id") or "")
    same_direction = (
        state.method_family == method_family
        and state.method_package_id == method_package_id
        and state.checkpoint_worktree.exists()
    )
    return (state, None) if same_direction else (None, state)


def lane_development_state_for_incumbent(
    state: LaneDevelopmentState | None,
    *,
    candidate_plan: dict[str, Any],
    incumbent_worktree: Path | None,
    incumbent_key: tuple[float, ...] | None,
) -> tuple[LaneDevelopmentState | None, LaneDevelopmentState | None]:
    """Keep lane sessions, but rebase stale lane code onto the official incumbent."""

    reusable, archived = reusable_lane_development_state(
        state,
        candidate_plan=candidate_plan,
    )
    if reusable is None or incumbent_worktree is None or incumbent_key is None:
        return reusable, archived
    incumbent = incumbent_worktree.resolve()
    if reusable.checkpoint_worktree.resolve() == incumbent:
        return reusable, archived
    return (
        replace(
            reusable,
            checkpoint_worktree=incumbent,
            objective_key=incumbent_key,
            stage=0,
            verified_components=[],
            last_failure="rebased_to_official_incumbent",
        ),
        reusable,
    )


def lane_development_state_payload(state: LaneDevelopmentState) -> dict[str, Any]:
    return {
        "candidate_id": state.candidate_id,
        "method_family": state.method_family,
        "method_package_id": state.method_package_id,
        "checkpoint_worktree": str(state.checkpoint_worktree),
        "objective_key": list(state.objective_key),
        "track": state.track,
        "stage": state.stage,
        "verified_components": list(state.verified_components),
        "session_id": state.session_id,
        "session_status": state.session_status,
        "event_stream_status": state.event_stream_status,
        "last_failure": state.last_failure,
        "last_update_round": state.last_update_round,
    }


def lane_development_states_payload(
    states: dict[str, LaneDevelopmentState],
) -> dict[str, dict[str, Any]]:
    return {
        candidate_id: lane_development_state_payload(state)
        for candidate_id, state in sorted(states.items())
    }


def lane_development_state_from_payload(value: Any) -> LaneDevelopmentState | None:
    if not isinstance(value, dict):
        return None
    candidate_id = str(value.get("candidate_id") or "").strip()
    checkpoint = str(value.get("checkpoint_worktree") or "").strip()
    objective = value.get("objective_key")
    if not candidate_id or not checkpoint or not isinstance(objective, (list, tuple)) or not objective:
        return None
    try:
        objective_key = tuple(float(item) for item in objective)
        stage = max(0, int(value.get("stage", 0) or 0))
        raw_last_update_round = value.get("last_update_round", -1)
        last_update_round = int(
            raw_last_update_round if raw_last_update_round is not None else -1
        )
    except (TypeError, ValueError):
        return None
    return LaneDevelopmentState(
        candidate_id=candidate_id,
        method_family=str(value.get("method_family") or ""),
        method_package_id=str(value.get("method_package_id") or ""),
        checkpoint_worktree=Path(checkpoint).resolve(),
        objective_key=objective_key,
        track=str(value.get("track") or ""),
        stage=stage,
        verified_components=_dedupe(
            [str(item) for item in value.get("verified_components") or [] if str(item).strip()]
        ),
        session_id=str(value.get("session_id") or "") or None,
        session_status=str(value.get("session_status") or "not_started"),
        event_stream_status=str(value.get("event_stream_status") or "unknown"),
        last_failure=str(value.get("last_failure") or "") or None,
        last_update_round=last_update_round,
    )


def _lane_track_metadata(plan: dict[str, Any]) -> tuple[str, int, int]:
    lane = plan.get("worker_lane") if isinstance(plan.get("worker_lane"), dict) else {}
    track = str(lane.get("track_id") or lane.get("lane_role") or "")
    try:
        stage = max(0, int(lane.get("stage", 0) or 0))
    except (TypeError, ValueError):
        stage = 0
    try:
        stage_count = max(1, int(lane.get("stage_count", 1) or 1))
    except (TypeError, ValueError):
        stage_count = 1
    return track, stage, stage_count


def evaluate_lane_checkpoint(
    outcome: dict[str, Any],
    *,
    parent_key: tuple[float, ...],
    candidate_plan: dict[str, Any],
) -> dict[str, Any]:
    objective_key = tuple(float(item) for item in outcome.get("objective_key") or [])
    summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
    worker_status = str(outcome.get("worker_status") or outcome.get("status") or "")
    worker_completed = worker_status in {"completed", "ok", "applied"}
    timeout_artifact_usable = bool(worker_status == "timeout" and outcome.get("target_changed"))
    checks = {
        "worker_completed": worker_completed,
        "worker_artifact_usable": worker_completed or timeout_artifact_usable,
        "core_legal": bool(
            outcome.get("core_eligible")
            and int(summary.get("total", 0) or 0) > 0
            and int(summary.get("valid", 0) or 0) == int(summary.get("total", 0) or 0)
            and int(summary.get("failed", 0) or 0) == 0
        ),
        "path_and_semantic_review": bool(
            outcome.get("ja_accepted")
            and not semantic_review_has_verified_blocking_finding(outcome.get("semantic_review"))
        ),
        "objective_not_worse": bool(
            objective_key
            and not _all_negative_infinity(objective_key)
            and objective_key >= parent_key
        ),
    }
    accepted = all(
        value
        for name, value in checks.items()
        if name != "worker_completed"
    )
    declared = [
        item
        for item in candidate_plan.get("checkpoint_checks") or []
        if isinstance(item, dict)
    ]
    executable = [item for item in declared if str(item.get("path") or "").strip()]
    descriptive = [item for item in declared if item not in executable]
    semantic_review = (
        outcome.get("semantic_review")
        if isinstance(outcome.get("semantic_review"), dict)
        else {}
    )
    semantic_status = str(semantic_review.get("status") or "missing").strip().lower()
    semantic_reviewer = str(semantic_review.get("reviewer") or "").strip().lower()
    full_review_passed = bool(
        semantic_status == "pass"
        and semantic_review.get("accepted") is True
        and semantic_reviewer not in {"", "none"}
    )
    implementation_order = _dedupe(
        [str(item) for item in candidate_plan.get("implementation_order") or [] if str(item).strip()]
    )
    component_status = {
        str(item.get("component_id") or ""): str(item.get("status") or "").strip().lower()
        for item in semantic_review.get("component_coverage") or []
        if isinstance(item, dict) and str(item.get("component_id") or "").strip()
    }
    assigned_components_passed = bool(
        implementation_order
        and all(component_status.get(component_id) == "implemented" for component_id in implementation_order)
    )
    semantic_checkpoint_passed = bool(
        not semantic_review_has_verified_blocking_finding(semantic_review)
        and (full_review_passed or assigned_components_passed)
    )
    checkpoint_evidence: dict[str, Any]
    if executable:
        check_plan = dict(candidate_plan)
        check_plan["activation_checks"] = executable
        executable_evidence = evaluate_mechanism_activation(
            check_plan,
            run_summary_from_payload(summary),
        )
    else:
        executable_evidence = {
            "status": "not_required",
            "passed": True,
            "declared_check_count": 0,
            "checks": [],
        }
    descriptive_passed = not descriptive or semantic_checkpoint_passed
    stage_complete = bool(
        accepted
        and worker_completed
        and implementation_order
        and declared
        and executable_evidence.get("passed") is True
        and descriptive_passed
    )
    if stage_complete:
        stage_reason = "completed"
    elif not worker_completed:
        stage_reason = "worker_not_completed"
    elif descriptive and semantic_status in {"missing", "skipped", "not_required", "unavailable"}:
        stage_reason = "checkpoint_review_unavailable"
    else:
        stage_reason = "checkpoint_checks_failed"
    checkpoint_evidence = {
        "status": "passed" if stage_complete else "failed",
        "passed": stage_complete,
        "declared_check_count": len(declared),
        "executable_check_count": len(executable),
        "descriptive_check_count": len(descriptive),
        "executable": executable_evidence,
        "semantic_review": {
            "required": bool(descriptive),
            "status": semantic_status,
            "reviewer": semantic_review.get("reviewer"),
            "accepted": semantic_review.get("accepted"),
            "passed": descriptive_passed,
            "full_review_passed": full_review_passed,
            "assigned_components_passed": assigned_components_passed,
            "assigned_components": implementation_order,
        },
    }
    failed_checks = [
        name
        for name, passed in checks.items()
        if not passed and name != "worker_completed"
    ]
    return {
        "accepted": accepted,
        "stage_complete": stage_complete,
        "checks": checks,
        "checkpoint_checks": checkpoint_evidence,
        "reason": "accepted" if accepted else ",".join(failed_checks),
        "stage_reason": stage_reason,
    }


def update_lane_development_states(
    states: dict[str, LaneDevelopmentState],
    *,
    candidate_plans: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    incumbent_worktree: Path,
    incumbent_key: tuple[float, ...],
    round_index: int,
) -> None:
    plans_by_id = {
        str((plan.get("candidate_variant") or {}).get("candidate_id") or f"c{index:02d}"): plan
        for index, plan in enumerate(candidate_plans)
    }
    for outcome in outcomes:
        candidate_id = str(outcome.get("candidate_id") or "")
        plan = plans_by_id.get(candidate_id, {})
        reusable, _archived = lane_development_state_for_incumbent(
            states.get(candidate_id),
            candidate_plan=plan,
            incumbent_worktree=incumbent_worktree,
            incumbent_key=incumbent_key,
        )
        parent_worktree = reusable.checkpoint_worktree if reusable else incumbent_worktree
        parent_key = reusable.objective_key if reusable else incumbent_key
        track, stage, stage_count = _lane_track_metadata(plan)
        if reusable is not None:
            stage = reusable.stage
        checkpoint = evaluate_lane_checkpoint(
            outcome,
            parent_key=parent_key,
            candidate_plan=plan,
        )
        outcome["checkpoint_decision"] = checkpoint

        raw_event_bytes = outcome.get("session_event_stream_bytes")
        try:
            event_bytes = int(raw_event_bytes) if raw_event_bytes is not None else None
        except (TypeError, ValueError):
            event_bytes = 0
        requested_session = str(outcome.get("requested_session_id") or "") or None
        commanded_session = str(outcome.get("command_session_id") or "") or None
        observed_session = str(outcome.get("observed_session_id") or "") or None
        if observed_session is None and requested_session is None and event_bytes is None:
            observed_session = str(outcome.get("worker_session_id") or "") or None
        nonzero_stream = event_bytes is None or event_bytes > 0
        if requested_session:
            continuity_ok = bool(
                outcome.get("session_reused")
                and commanded_session == requested_session
                and observed_session == requested_session
                and nonzero_stream
            )
            session_status = "continued" if continuity_ok else "continuity_failed"
        else:
            continuity_ok = bool(observed_session and nonzero_stream)
            session_status = "started" if continuity_ok else "not_observed"
        session_id = observed_session if continuity_ok else None
        event_stream_status = (
            "nonzero" if event_bytes is not None and event_bytes > 0
            else "zero" if event_bytes == 0
            else "unknown"
        )

        checkpoint_accepted = bool(checkpoint["accepted"])
        stage_complete = bool(checkpoint["stage_complete"])
        checkpoint["session_continuity"] = {
            "required": bool(requested_session),
            "passed": continuity_ok if requested_session else True,
            "requested_session_id": requested_session,
            "command_session_id": commanded_session,
            "observed_session_id": observed_session,
            "event_stream_status": event_stream_status,
        }
        if requested_session and not continuity_ok:
            stage_complete = False
            checkpoint["stage_complete"] = False
            checkpoint["stage_reason"] = "session_continuity_failed"
        implementation_order = _dedupe(
            [str(item) for item in plan.get("implementation_order") or [] if str(item).strip()]
        )
        verified_components = list(reusable.verified_components) if reusable else []
        if stage_complete:
            verified_components = _dedupe([*verified_components, *implementation_order])
        next_stage = min(stage + 1, stage_count) if stage_complete else stage
        failure: str | None = None
        outcome_worker_status = str(outcome.get("worker_status") or outcome.get("status") or "")
        if outcome_worker_status not in {"completed", "ok", "applied"}:
            failure = str(outcome.get("error") or outcome_worker_status or "candidate_failed")
        elif not checkpoint_accepted:
            failure = str(checkpoint.get("reason") or "checkpoint_rejected")
        elif not stage_complete:
            failure = str(checkpoint.get("stage_reason") or "checkpoint_checks_failed")
        elif requested_session and not continuity_ok:
            failure = "session_continuity_failed"
        state = LaneDevelopmentState(
            candidate_id=candidate_id,
            method_family=str(plan.get("method_family") or ""),
            method_package_id=str(plan.get("method_package_id") or ""),
            checkpoint_worktree=(
                Path(str(outcome.get("worktree"))).resolve()
                if checkpoint_accepted and outcome.get("worktree")
                else parent_worktree.resolve()
            ),
            objective_key=(
                tuple(float(item) for item in outcome.get("objective_key") or [])
                if checkpoint_accepted
                else parent_key
            ),
            track=track,
            stage=next_stage,
            verified_components=verified_components,
            session_id=session_id,
            session_status=session_status,
            event_stream_status=event_stream_status,
            last_failure=failure,
            last_update_round=round_index,
        )
        states[candidate_id] = state
        outcome["lane_development_state"] = lane_development_state_payload(state)


def competitive_direction_plans(
    direction_plan: dict[str, Any],
    *,
    limit: int,
    lane_development_states: dict[str, LaneDevelopmentState] | None = None,
    incumbent_worktree: Path | None = None,
    incumbent_key: tuple[float, ...] | None = None,
) -> list[dict[str, Any]]:
    """Expand bounded variants; only research tournaments may cross method families."""

    limit = max(1, min(4, int(limit)))
    variants = [
        item
        for item in direction_plan.get("candidate_variants") or []
        if isinstance(item, dict)
    ][:limit]
    lane_policy = (
        direction_plan.get("worker_lane_policy")
        if isinstance(direction_plan.get("worker_lane_policy"), dict)
        else {}
    )
    delegated = lane_policy.get("mechanism_selection") == "delegated_to_worker"
    if not variants and delegated:
        return delegated_worker_lane_plans(
            direction_plan,
            limit=limit,
            lane_development_states=lane_development_states or {},
            incumbent_worktree=incumbent_worktree,
            incumbent_key=incumbent_key,
        )
    if limit <= 1 or not variants:
        return [direction_plan]
    result: list[dict[str, Any]] = []
    base_direction_id = str(direction_plan.get("direction_id") or "direction")
    for index, variant in enumerate(variants):
        candidate_id = str(variant.get("candidate_id") or f"c{index:02d}")
        plan = dict(direction_plan)
        plan["direction_id"] = f"{base_direction_id}-{candidate_id}"[:80]
        plan["title"] = str(variant.get("title") or plan.get("title") or candidate_id)[:200]
        for name in (
            "hypothesis",
            "worker_objective",
            "strategy_type",
            "completion_rule",
        ):
            if variant.get(name):
                plan[name] = variant[name]
        for name in (
            "change_scope",
            "implementation_order",
            "deliverables",
            "knowledge_paths",
            "acceptance_checks",
            "activation_checks",
            "checkpoint_checks",
        ):
            if variant.get(name):
                plan[name] = variant[name]
        parent_stage = str(plan.get("experiment_stage") or "probe").strip()
        # Candidate variants cannot escalate a probe/scale round into a
        # cross-family tournament. That transition belongs to Main's
        # round-level research state and, when enabled, applies to every lane.
        experiment_stage = "research_tournament" if parent_stage == "research_tournament" else parent_stage
        plan["experiment_stage"] = experiment_stage
        if experiment_stage == "research_tournament":
            for name in (
                "method_family",
                "method_families",
                "method_package_id",
                "method_package_selection",
                "implementation_bundle",
                "knowledge_query",
            ):
                if variant.get(name):
                    plan[name] = variant[name]
            bundle = (
                plan.get("implementation_bundle")
                if isinstance(plan.get("implementation_bundle"), dict)
                else {}
            )
            package_tracks = [
                item
                for item in bundle.get("competition_tracks") or []
                if isinstance(item, dict)
            ]
            if package_tracks:
                track = next(
                    (
                        item
                        for item in package_tracks
                        if str(item.get("track_id") or item.get("id") or "")
                        == "direct_evidence"
                    ),
                    package_tracks[0],
                )
                track_id = str(track.get("track_id") or track.get("id") or "")
                stages = _track_stages(track)
                prior_state, _archived = lane_development_state_for_incumbent(
                    (lane_development_states or {}).get(candidate_id),
                    candidate_plan=plan,
                    incumbent_worktree=incumbent_worktree,
                    incumbent_key=incumbent_key,
                )
                stage_index = prior_state.stage if prior_state is not None else 0
                stage_index = min(stage_index, len(stages))
                stage_complete = stage_index >= len(stages)
                stage = {} if stage_complete else stages[stage_index]
                implementation_order = _dependency_closed_order(
                    [
                        *(prior_state.verified_components if prior_state is not None else []),
                        *(
                            [str(item) for item in track.get("component_ids") or []]
                            if stage_complete
                            else _stage_component_ids(stage)
                        ),
                    ],
                    _component_dependency_map(bundle),
                )
                selected_ids = set(implementation_order)
                plan["implementation_order"] = implementation_order
                plan["deliverables"] = [
                    item
                    for item in bundle.get("required_components") or []
                    if isinstance(item, dict)
                    and str(item.get("component_id") or "") in selected_ids
                ]
                stage_checkpoint_checks = (
                    []
                    if stage_complete
                    else _track_checkpoint_checks(
                        bundle,
                        track,
                        stage,
                        stage_index=stage_index,
                        implementation_order=implementation_order,
                    )
                )
                plan["checkpoint_checks"] = stage_checkpoint_checks
                plan["acceptance_checks"] = _stage_acceptance_checks(
                    plan,
                    stage_checkpoint_checks,
                )
                plan["worker_lane"] = {
                    "schema_version": 1,
                    "lane_index": index,
                    "lane_role": track_id,
                    "track_id": track_id,
                    "stage": stage_index,
                    "stage_count": max(1, len(stages)),
                    "stage_status": "completed" if stage_complete else "active",
                    "stage_id": (
                        f"maintenance:{track_id}"
                        if stage_complete
                        else str(stage.get("stage_id") or stage.get("id") or stage_index)
                    ),
                    "parent_checkpoint": (
                        str(prior_state.checkpoint_worktree)
                        if prior_state is not None
                        else None
                    ),
                    "verified_components": (
                        list(prior_state.verified_components)
                        if prior_state is not None
                        else []
                    ),
                    "mechanism_selection": "family_hypothesis_tournament",
                }
        for name in ("preserve", "avoid"):
            plan[name] = _dedupe(
                [
                    *(str(item) for item in plan.get(name) or []),
                    *(str(item) for item in variant.get(name) or []),
                ]
            )[:12]
        if variant.get("next_mutation"):
            plan["next_mutation"] = variant["next_mutation"]
        plan["candidate_variant"] = variant
        plan["candidate_variants"] = []
        result.append(plan)
    return result or [direction_plan]


DELEGATED_WORKER_LANES = (
    (
        "direct_evidence",
        "Direct evidence",
        "Choose the bounded mechanism with the strongest direct support in current Core evidence and authorized Skills.",
    ),
    (
        "minimal_risk",
        "Minimal risk",
        "Choose the smallest coherent mechanism change likely to preserve legality and incumbent behavior.",
    ),
    (
        "orthogonal_mechanism",
        "Orthogonal mechanism",
        "Choose a mechanism materially different from the obvious direct-evidence lane while staying in the selected family.",
    ),
    (
        "diagnostic_value",
        "Diagnostic value",
        "Choose a bounded mechanism whose Core result will most clearly confirm or falsify the direction hypothesis.",
    ),
)


def _component_dependency_map(bundle: dict[str, Any]) -> dict[str, list[str]]:
    raw = bundle.get("component_dependencies")
    if isinstance(raw, dict):
        return {
            str(component_id): _dedupe(
                [str(item) for item in dependencies or [] if str(item).strip()]
            )
            for component_id, dependencies in raw.items()
            if str(component_id).strip() and isinstance(dependencies, (list, tuple))
        }
    result: dict[str, list[str]] = {}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or item.get("id") or "").strip()
        if component_id:
            result[component_id] = _dedupe(
                [
                    str(value)
                    for value in item.get("depends_on") or item.get("dependencies") or []
                    if str(value).strip()
                ]
            )
    return result


def _dependency_closed_order(
    component_ids: list[str],
    dependencies: dict[str, list[str]],
) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in ordered:
            return
        if component_id in visiting:
            raise ValueError(f"cyclic Method Package component dependency at {component_id}")
        visiting.add(component_id)
        for dependency in dependencies.get(component_id, []):
            visit(dependency)
        visiting.remove(component_id)
        ordered.append(component_id)

    for component_id in component_ids:
        visit(component_id)
    return ordered


def _track_stages(track: dict[str, Any]) -> list[dict[str, Any]]:
    stages = track.get("stages") or track.get("implementation_stages") or []
    return [item for item in stages if isinstance(item, dict)]


def _stage_component_ids(stage: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            str(item)
            for item in (
                stage.get("component_ids")
                or stage.get("implementation_order")
                or stage.get("components")
                or []
            )
            if str(item).strip()
        ]
    )


def _track_checkpoint_checks(
    bundle: dict[str, Any],
    track: dict[str, Any],
    stage: dict[str, Any],
    *,
    stage_index: int,
    implementation_order: list[str],
) -> list[dict[str, Any]]:
    direct = stage.get("checkpoint_checks")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    track_id = str(track.get("track_id") or track.get("id") or "")
    result: list[dict[str, Any]] = []
    for item in bundle.get("checkpoint_checks") or []:
        if not isinstance(item, dict):
            continue
        item_track = str(item.get("track_id") or "")
        raw_stage = item.get("stage", item.get("stage_index"))
        if item_track and item_track != track_id:
            continue
        if raw_stage is not None:
            try:
                if int(raw_stage) != stage_index:
                    continue
            except (TypeError, ValueError):
                continue
        component_ids = {
            str(value) for value in item.get("component_ids") or [] if str(value).strip()
        }
        if component_ids:
            selected_ids = set(implementation_order)
            component_match = str(
                item.get("component_match") or item.get("component_mode") or "all"
            ).strip().lower()
            if component_match == "any":
                if component_ids.isdisjoint(selected_ids):
                    continue
            elif not component_ids.issubset(selected_ids):
                continue
        result.append(item)
    return result


def _stage_acceptance_checks(
    plan: dict[str, Any],
    checkpoint_checks: list[dict[str, Any]],
) -> list[str]:
    preserved = [
        str(item)
        for item in plan.get("acceptance_checks") or []
        if str(item).strip() and not str(item).lstrip().startswith("Checkpoint")
    ]
    selected: list[str] = []
    for item in checkpoint_checks:
        requirement = str(item.get("requirement") or item.get("title") or "").strip()
        if not requirement:
            continue
        check_id = str(item.get("check_id") or "").strip()
        selected.append(
            f"Checkpoint {check_id}: {requirement}"
            if check_id
            else f"Checkpoint: {requirement}"
        )
    return _dedupe([*preserved, *selected])[:12]


def delegated_worker_lane_plans(
    direction_plan: dict[str, Any],
    *,
    limit: int,
    lane_development_states: dict[str, LaneDevelopmentState] | None = None,
    incumbent_worktree: Path | None = None,
    incumbent_key: tuple[float, ...] | None = None,
) -> list[dict[str, Any]]:
    """Compile generic parallel lanes without choosing an algorithm for Workers."""

    policy = (
        direction_plan.get("worker_lane_policy")
        if isinstance(direction_plan.get("worker_lane_policy"), dict)
        else {}
    )
    try:
        requested = int(policy.get("lane_count") or limit)
    except (TypeError, ValueError):
        requested = limit
    lane_count = max(1, min(4, int(limit), requested))
    requested_roles = [str(item).strip() for item in policy.get("roles") or [] if str(item).strip()]
    lane_development_states = lane_development_states or {}
    bundle = (
        direction_plan.get("implementation_bundle")
        if isinstance(direction_plan.get("implementation_bundle"), dict)
        else {}
    )
    package_tracks = [
        item for item in bundle.get("competition_tracks") or [] if isinstance(item, dict)
    ]
    tracks_by_id = {
        str(item.get("track_id") or item.get("id") or ""): item
        for item in package_tracks
        if str(item.get("track_id") or item.get("id") or "").strip()
    }
    dependencies = _component_dependency_map(bundle)
    definitions = {item[0]: item for item in DELEGATED_WORKER_LANES}
    if package_tracks:
        package_roles = {
            str(track.get("track_id") or track.get("id") or ""): (
                str(track.get("track_id") or track.get("id") or ""),
                str(track.get("title") or track.get("track_id") or track.get("id") or "Package track"),
                str(track.get("selection_hint") or "Implement the current package track stage."),
            )
            for track in package_tracks
            if str(track.get("track_id") or track.get("id") or "").strip()
        }
        ordered = [package_roles[role] for role in requested_roles if role in package_roles]
        ordered.extend(item for item in package_roles.values() if item not in ordered)
        lane_count = min(lane_count, len(ordered))
        if lane_count == 0:
            raise ValueError("delegated Method Package declares no usable competition tracks")
    else:
        ordered = [definitions[role] for role in requested_roles if role in definitions]
        ordered.extend(item for item in DELEGATED_WORKER_LANES if item not in ordered)

    result: list[dict[str, Any]] = []
    base_direction_id = str(direction_plan.get("direction_id") or "direction")
    base_title = str(direction_plan.get("title") or "Worker-selected mechanism")
    base_hypothesis = str(direction_plan.get("hypothesis") or "")
    distinct_contracts: set[tuple[tuple[str, ...], int | str]] = set()
    for index, (role_id, role_title, role_objective) in enumerate(ordered[:lane_count]):
        candidate_id = f"lane-{index + 1:02d}-{role_id}"[:48]
        plan = dict(direction_plan)
        track = tracks_by_id.get(role_id, {})
        stages = _track_stages(track)
        prior_state, _archived = lane_development_state_for_incumbent(
            lane_development_states.get(candidate_id),
            candidate_plan=plan,
            incumbent_worktree=incumbent_worktree,
            incumbent_key=incumbent_key,
        )
        stage_index = prior_state.stage if prior_state is not None else 0
        stage_status = "active"
        if stages:
            stage_index = min(stage_index, len(stages))
            stage_complete = stage_index >= len(stages)
            stage_status = "completed" if stage_complete else "active"
            stage = {} if stage_complete else stages[stage_index]
            implementation_order = _dependency_closed_order(
                [
                    *(prior_state.verified_components if prior_state is not None else []),
                    *(
                        [str(item) for item in track.get("component_ids") or []]
                        if stage_complete
                        else _stage_component_ids(stage)
                    ),
                ],
                dependencies,
            )
            contract_stage: int | str = (
                f"maintenance:{role_id}" if stage_complete else stage_index
            )
            contract_key = (tuple(implementation_order), contract_stage)
            if contract_key in distinct_contracts:
                raise ValueError(
                    "delegated lane planning contract produced duplicate component bundle/stage: "
                    f"{role_id} stage {stage_index}"
                )
            distinct_contracts.add(contract_key)
            plan["implementation_order"] = implementation_order
            selected_ids = set(implementation_order)
            plan["deliverables"] = [
                item
                for item in bundle.get("required_components") or []
                if isinstance(item, dict)
                and str(item.get("component_id") or "") in selected_ids
            ]
            stage_checkpoint_checks = (
                []
                if stage_complete
                else _track_checkpoint_checks(
                    bundle,
                    track,
                    stage,
                    stage_index=stage_index,
                    implementation_order=implementation_order,
                )
            )
            plan["checkpoint_checks"] = stage_checkpoint_checks
            plan["acceptance_checks"] = _stage_acceptance_checks(
                plan,
                stage_checkpoint_checks,
            )
        plan["direction_id"] = f"{base_direction_id}-{candidate_id}"[:80]
        plan["title"] = f"{base_title}: {role_title}"[:200]
        plan["worker_objective"] = (
            f"{role_objective} Inspect the incumbent and authorized Skills first, then independently choose and "
            "implement one concrete mechanism. Preserve the incumbent fallback; do not stop at telemetry-only changes."
        )[:1200]
        plan["candidate_variant"] = {
            "candidate_id": candidate_id,
            "title": role_title,
            "hypothesis": base_hypothesis,
            "strategy_type": str(plan.get("strategy_type") or "worker_selected_mechanism"),
            "lane_role": role_id,
            "mechanism_selection": "delegated_to_worker",
        }
        plan["worker_lane"] = {
            "schema_version": 1,
            "lane_index": index,
            "lane_role": role_id,
            "track_id": role_id,
            "stage": stage_index,
            "stage_count": max(1, len(stages)),
            "stage_status": stage_status,
            "stage_id": (
                f"maintenance:{role_id}"
                if stage_status == "completed"
                else str(stage.get("stage_id") or stage.get("id") or stage_index)
                if stages
                else str(stage_index)
            ),
            "parent_checkpoint": (
                str(prior_state.checkpoint_worktree) if prior_state is not None else None
            ),
            "verified_components": (
                list(prior_state.verified_components) if prior_state is not None else []
            ),
            "mechanism_selection": "delegated_to_worker",
        }
        plan["candidate_variants"] = []
        plan["activation_checks"] = []
        plan["activation_contract_version"] = 0
        result.append(plan)
    return result


def evaluate_mechanism_activation(
    direction_plan: dict[str, Any],
    summary: RunSummary,
) -> dict[str, Any]:
    """Evaluate telemetry assertions proving that the proposed mechanism ran.

    Activation is deliberately separate from solution quality. A failed required
    assertion makes the mechanism claim inconclusive but does not block promotion.
    A plan without assertions is unverifiable and reports an unknown result,
    rather than falsely passing or being conflated with a declared failure.
    """

    checks = [
        item
        for item in direction_plan.get("activation_checks") or []
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ][:12]
    if not checks:
        return {
            "status": "not_declared",
            "passed": None,
            "declared_check_count": 0,
            "required_check_count": 0,
            "required_failure_count": 0,
            "checks": [],
        }

    evidence_payloads = [
        item
        for item in summary.activation_evidence or []
        if isinstance(item, dict)
    ] or [summary_payload(summary)]
    evaluated: list[dict[str, Any]] = []
    required_failures = 0
    for index, check in enumerate(checks):
        path = str(check.get("path") or "").strip()
        operator = str(check.get("operator") or "exists").strip().lower()
        expected = check.get("expected", check.get("value"))
        required = check.get("required") is not False
        aggregation = str(check.get("aggregation") or "any").strip().lower()
        if aggregation not in {"any", "all", "min_passes"}:
            aggregation = "any"
        min_passes = max(1, int(check.get("min_passes") or 1))
        observations: list[dict[str, Any]] = []
        for evidence_index, payload in enumerate(evidence_payloads):
            found, observed, resolved_path = _resolve_activation_path_with_canonical(payload, path)
            observation_passed = _activation_predicate(
                found=found,
                observed=observed,
                operator=operator,
                expected=expected,
            )
            observations.append(
                {
                    "experiment_id": payload.get("experiment_id") or summary.best_experiment_id,
                    "instance_id": payload.get("instance_id"),
                    "seed": payload.get("seed"),
                    "evidence_index": evidence_index,
                    "found": found,
                    "observed": observed,
                    "resolved_path": resolved_path,
                    "passed": observation_passed,
                }
            )
        pass_count = sum(1 for item in observations if item["passed"])
        if aggregation == "all":
            passed = bool(observations) and pass_count == len(observations)
        elif aggregation == "min_passes":
            passed = pass_count >= min_passes
        else:
            passed = pass_count > 0
        representative = next(
            (item for item in observations if item["passed"]),
            observations[0],
        )
        if required and not passed:
            required_failures += 1
        evaluated.append(
            {
                "id": str(check.get("id") or f"activation_{index + 1}")[:80],
                "path": path,
                "resolved_path": resolved_path,
                "operator": operator,
                "expected": expected,
                "required": required,
                "aggregation": aggregation,
                "min_passes": min_passes,
                "evaluated_run_count": len(observations),
                "passed_run_count": pass_count,
                "found": any(item["found"] for item in observations),
                "observed": representative["observed"],
                "resolved_path": representative["resolved_path"],
                "passed": passed,
                "observations": observations[:16],
                "description": str(check.get("description") or "")[:500],
            }
        )
    passed = required_failures == 0
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "declared_check_count": len(evaluated),
        "required_check_count": sum(1 for item in evaluated if item["required"]),
        "required_failure_count": required_failures,
        "checks": evaluated,
    }


def evaluate_exact_solver_execution(
    direction_plan: dict[str, Any],
    summary: RunSummary,
) -> dict[str, Any]:
    """Require runtime proof that an exact_hybrid candidate actually called CP-SAT."""

    method_family = str(direction_plan.get("method_family") or "").strip()
    required = method_family == "exact_hybrid"
    if not required:
        return {
            "status": "not_required",
            "required": False,
            "passed": None,
            "cp_sat_called": None,
            "observed_run_count": 0,
        }

    payloads = [
        item
        for item in summary.activation_evidence or []
        if isinstance(item, dict)
    ] or [summary_payload(summary)]
    observations = [
        value
        for payload in payloads
        for value in _values_for_key(payload, "cp_sat_called")
    ]
    called = any(value is True for value in observations)
    return {
        "status": "passed" if called else "failed",
        "required": True,
        "passed": called,
        "cp_sat_called": called,
        "observed_run_count": len(payloads),
        "observed_values": observations[:16],
        "reason": None if called else "exact_hybrid_without_cp_sat_execution_evidence",
    }


def _values_for_key(value: Any, key: str, *, depth: int = 0) -> list[Any]:
    if depth >= 10:
        return []
    if isinstance(value, dict):
        found = [value[key]] if key in value else []
        for child in value.values():
            found.extend(_values_for_key(child, key, depth=depth + 1))
        return found
    if isinstance(value, list):
        found: list[Any] = []
        for child in value[:64]:
            found.extend(_values_for_key(child, key, depth=depth + 1))
        return found
    return []


def _resolve_activation_path(payload: Any, path: str) -> tuple[bool, Any]:
    found, observed, _resolved_path = _resolve_activation_path_with_canonical(payload, path)
    return found, observed


def _resolve_activation_path_with_canonical(
    payload: Any,
    path: str,
) -> tuple[bool, Any, str | None]:
    candidates = [path]
    if path.startswith("diagnostics."):
        candidates.append(f"best_metrics.solver_evidence.{path}")
    elif path != "diagnostics":
        candidates.append(f"best_metrics.solver_evidence.diagnostics.{path}")

    for candidate in _dedupe(candidates):
        found, observed = _resolve_dotted_path(payload, candidate)
        if found:
            return True, observed, candidate
    return False, None, None


def _resolve_dotted_path(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for segment in (item for item in path.split(".") if item):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return False, None
    return True, current


def _activation_predicate(
    *,
    found: bool,
    observed: Any,
    operator: str,
    expected: Any,
) -> bool:
    if operator == "exists":
        return found
    if not found:
        return False
    if operator == "truthy":
        return bool(observed)
    if operator == "eq":
        return observed == expected
    if operator == "ne":
        return observed != expected
    if operator == "contains":
        try:
            return expected in observed
        except (TypeError, ValueError):
            return False
    comparisons = {
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
    }
    comparator = comparisons.get(operator)
    if comparator is None:
        return False
    try:
        return bool(comparator(observed, expected))
    except (TypeError, ValueError):
        return False


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
    semantic_review_floor_key: tuple[float, ...] | None = None,
    baseline_generation: dict[str, Any] | None,
    previous_rounds: list[LoopRoundRecord],
    repair_attempts: int,
    direction_plan: dict[str, Any] | None = None,
    semantic_reviewer: AlgorithmSemanticReviewer | None = None,
    assignment_issuer: DirectionPlanningAgent | None = None,
    worker_input_root: Path | None = None,
    user_intervention: dict[str, Any] | None = None,
    initial_session_id: str | None = None,
    cancellation: CancellationToken | None = None,
) -> tuple[Any, Path, list[dict[str, Any]]]:
    """Run one checkpoint batch of same-direction Trials and return its best result.

    Session-capable Workers keep one model session across checkpoint batches
    until the user accepts a direction pivot. Harness feedback is attached to
    every new assignment, and later degraded trials do not replace the best
    Core-valid, semantic-valid, activated parent.
    """

    max_repair_attempts = max(0, int(repair_attempts))
    attempts: list[dict[str, Any]] = []
    last_cycle: Any | None = None
    last_context_packet_path = output_dir / "context_packet.json"
    best_cycle: Any | None = None
    best_context_packet_path: Path | None = None
    best_assignment_path: Path | None = None
    best_attempt: dict[str, Any] | None = None
    best_key: tuple[float, ...] | None = None
    termination_reason = "checkpoint_interval_reached"
    worker_session_id = str(initial_session_id or "").strip() or None
    try:
        session_reuse_enabled = bool(worker.capabilities().supports_session_reuse)
    except Exception:  # noqa: BLE001 - optional capability must fail closed.
        session_reuse_enabled = False
    local_trial_count = (
        max(1, max_repair_attempts)
        if session_reuse_enabled
        else max_repair_attempts + 1
    )
    direction_project_root = project_root
    parent_assignment_path: Path | None = None
    planner = assignment_issuer or EvidenceDrivenMainAgent()
    effective_direction_plan = direction_plan or EvidenceDrivenMainAgent().plan_direction(
        DirectionPlanRequest(
            round_index=round_index,
            context_packet_path=base_context_packet_path,
            loop_feedback={
                "round_index": round_index,
                "incumbent_key_before": list(incumbent_key),
                "next_round_guidance": {
                    "must_do": ["Make one bounded evaluator-checkable change."],
                },
            },
            output_dir=output_dir / "main_agent_fallback",
        )
    )
    for attempt_index in range(local_trial_count):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        attempt_dir = output_dir if attempt_index == 0 else output_dir / f"repair_{attempt_index:03d}"
        # repair feedback 只带最近失败、精确门禁和 patch 证据，控制上下文增长。
        repair_feedback = (
            current_round_repair_feedback(
                attempt_index=attempt_index,
                max_repair_attempts=max(0, local_trial_count - 1),
                previous_attempts=attempts,
                repair_anchor=best_attempt,
                local_trial_refinement=session_reuse_enabled,
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
                current_direction_plan=effective_direction_plan,
                user_intervention=user_intervention,
            ),
            project_root=direction_project_root,
        )
        assignment_feedback = load_context_packet(last_context_packet_path).effective_context.get("loop_feedback") or {}
        assignment_issue = request_worker_assignment(
            planner,
            WorkerAssignmentRequest(
                round_index=round_index,
                attempt_index=attempt_index,
                context_packet_path=last_context_packet_path,
                direction_plan=effective_direction_plan,
                loop_feedback=assignment_feedback,
                output_dir=attempt_dir,
                max_steps=max_steps,
                max_runtime_seconds=max_runtime_seconds,
                parent_assignment_path=parent_assignment_path,
            ),
        )
        requested_session_id = worker_session_id if session_reuse_enabled else None
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
            worker_assignment_path=assignment_issue.artifact_path,
            worker_input_root=worker_input_root,
            session_id=requested_session_id,
            local_trial_index=attempt_index,
            local_trial_count=local_trial_count,
            cancellation=cancellation,
        )
        artifacts = last_cycle.worker_result.artifacts or {}
        session_telemetry = worker_session_telemetry(
            artifacts,
            requested_session_id=requested_session_id,
        )
        observed_session_id = str(session_telemetry.get("observed_session_id") or "").strip()
        if session_reuse_enabled and observed_session_id:
            worker_session_id = (
                observed_session_id
                if requested_session_id is None or session_telemetry.get("session_reused") is True
                else None
            )
        semantic_review = run_algorithm_semantic_review(
            reviewer=semantic_reviewer,
            cycle=last_cycle,
            context_packet_path=last_context_packet_path,
            direction_plan=effective_direction_plan,
            round_index=round_index,
            attempt_index=attempt_index,
            output_dir=attempt_dir / "semantic_review",
            incumbent_key=incumbent_key,
            candidate_key=summary_objective_key(last_cycle.summary, contract.objectives),
        )
        attempt_payload = round_attempt_payload(
            last_cycle,
            attempt_index=attempt_index,
            context_packet_path=last_context_packet_path,
            incumbent_key=incumbent_key,
            semantic_review=semantic_review,
        )
        attempt_payload["worker_assignment_path"] = str(assignment_issue.artifact_path)
        attempt_payload["assignment_id"] = assignment_issue.assignment.assignment_id
        attempt_payload["local_trial_index"] = attempt_index + 1
        attempt_payload["local_trial_count"] = local_trial_count
        attempt_payload["session_id"] = worker_session_id
        attempt_payload.update(session_telemetry)
        attempt_payload["parent_attempt_index"] = (
            best_attempt.get("attempt_index") if isinstance(best_attempt, dict) else None
        )
        mechanism_activation = evaluate_mechanism_activation(effective_direction_plan, last_cycle.summary)
        attempt_payload["mechanism_activation"] = mechanism_activation
        attempt_payload["activation_required"] = activation_contract_required(effective_direction_plan)
        attempts.append(attempt_payload)

        candidate_key = summary_objective_key(last_cycle.summary, contract.objectives)
        candidate_eligible = local_trial_candidate_eligible(
            last_cycle,
            candidate_key=candidate_key,
            semantic_review=semantic_review,
            mechanism_activation=mechanism_activation,
            activation_required=attempt_payload["activation_required"],
        )
        improved_parent = candidate_eligible and (best_key is None or candidate_key > best_key)
        if improved_parent:
            best_cycle = last_cycle
            best_context_packet_path = last_context_packet_path
            best_assignment_path = assignment_issue.artifact_path
            best_attempt = attempt_payload
            best_key = candidate_key

        if attempt_index >= local_trial_count - 1:
            termination_reason = "checkpoint_interval_reached"
            break
        if is_nonrepairable_worker_failure(last_cycle):
            termination_reason = "nonrepairable_worker_failure"
            break
        if not session_reuse_enabled and not should_attempt_in_round_repair(
            last_cycle,
            incumbent_key=semantic_review_floor_key or incumbent_key,
            semantic_review=semantic_review,
        ):
            termination_reason = "repair_not_required"
            break

        if best_cycle is not None:
            direction_project_root = Path(best_cycle.worktree_path)
            parent_assignment_path = best_assignment_path
        else:
            direction_project_root = Path(last_cycle.worktree_path)
            parent_assignment_path = assignment_issue.artifact_path

    if last_cycle is None:
        raise RuntimeError("worker cycle did not produce an attempt")
    selected_cycle = best_cycle or last_cycle
    selected_context_packet_path = best_context_packet_path or last_context_packet_path
    selected_attempt = best_attempt or attempts[-1]
    for item in attempts:
        item["disposition"] = "selected" if item is selected_attempt else "rejected"
        item["termination_reason"] = termination_reason if item is attempts[-1] else None
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "local_trial_ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "direction_id": effective_direction_plan.get("direction_id"),
                "session_reuse_enabled": session_reuse_enabled,
                "session_id": worker_session_id,
                "checkpoint_interval": local_trial_count,
                "direction_change_requires_user_confirmation": True,
                "selected_attempt_index": selected_attempt.get("attempt_index"),
                "termination_reason": termination_reason,
                "attempts": attempts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    attempts[-1]["local_trial_ledger_path"] = str(ledger_path.resolve())
    return selected_cycle, selected_context_packet_path, attempts


def worker_session_telemetry(
    artifacts: dict[str, Any],
    *,
    requested_session_id: str | None,
) -> dict[str, Any]:
    """Distinguish requested continuity from a session proven in the event stream."""

    requested = str(requested_session_id or "").strip() or None
    observed = str(
        artifacts.get("observed_session_id") or artifacts.get("session_id") or ""
    ).strip() or None
    has_runtime_telemetry = any(
        key in artifacts
        for key in (
            "requested_session_id",
            "command_session_id",
            "observed_session_id",
            "resume_strategy",
            "event_stream_bytes",
        )
    )
    commanded = str(artifacts.get("command_session_id") or "").strip() or None
    if not has_runtime_telemetry and requested:
        # Compatibility for non-OpenCode Workers that expose only session_id.
        commanded = requested
    event_stream_bytes: int | None = None
    if "event_stream_bytes" in artifacts:
        try:
            event_stream_bytes = max(0, int(artifacts["event_stream_bytes"]))
        except (TypeError, ValueError):
            event_stream_bytes = 0
    reused = bool(
        requested
        and commanded == requested
        and observed == requested
        and (event_stream_bytes is None or event_stream_bytes > 0)
    )
    return {
        "session_resume_requested": bool(requested),
        "session_resume_commanded": bool(commanded and commanded == requested),
        "session_resume_observed": bool(observed and observed == requested),
        "session_reused": reused,
        "requested_session_id": requested,
        "command_session_id": commanded,
        "observed_session_id": observed,
        "session_event_stream_bytes": event_stream_bytes,
        "session_resume_strategy": str(artifacts.get("resume_strategy") or "").strip() or None,
    }


def local_trial_candidate_eligible(
    cycle: Any,
    *,
    candidate_key: tuple[float, ...],
    semantic_review: dict[str, Any] | None,
    mechanism_activation: dict[str, Any],
    activation_required: bool,
) -> bool:
    """Return whether a Local Trial may become the next objective parent."""

    summary = getattr(cycle, "summary", None)
    if summary is None or summary.total <= 0 or summary.valid != summary.total or summary.failed:
        return False
    if not candidate_key or _all_negative_infinity(candidate_key):
        return False
    # Keep the arguments in this compatibility surface because callers still
    # persist activation and semantic diagnostics, but neither gates the
    # objective parent selected inside a bounded trial series.
    del semantic_review, mechanism_activation, activation_required
    return True


# ---------------------------------------------------------------------------
# 同轮修补与语义审查
# ---------------------------------------------------------------------------

def worker_loop_repair_attempt_budget(worker: CodingWorker, requested_attempts: int) -> int:
    """同时受用户预算和 Worker 能力约束，得到实际可用修补次数。"""

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
    """只对 Core 合法且可能晋升的候选执行昂贵语义审查。

    非法或不优于 incumbent 的候选无需证明方法完整性，因为它本来就没有
    promotion 资格；这样既节省模型调用，也避免语义结果掩盖 Core 事实。
    """

    if reviewer is None:
        return {
            "schema_version": 1,
            "status": "skipped",
            "accepted": True,
            "summary": "No algorithm semantic reviewer was configured.",
            "findings": [],
            "reviewer": "none",
        }
    summary = getattr(cycle, "summary", None)
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
    descriptive_checkpoint_review = any(
        isinstance(item, dict) and not str(item.get("path") or "").strip()
        for item in direction_plan.get("checkpoint_checks") or []
    )
    deferred_by_objective = bool(
        incumbent_key is not None
        and effective_candidate_key
        and (
            effective_candidate_key < incumbent_key
            if descriptive_checkpoint_review
            else effective_candidate_key <= incumbent_key
        )
    )
    if deferred_by_objective:
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


def semantic_review_has_verified_blocking_finding(value: dict[str, Any] | None) -> bool:
    """Return whether semantic review contains an evidence-backed blocker."""

    if not isinstance(value, dict):
        return False
    return any(
        isinstance(finding, dict) and bool(finding.get("blocking"))
        for finding in (value.get("findings") or [])
    )


def semantic_review_baseline_degraded_reason(value: dict[str, Any] | None) -> str | None:
    """Classify Core-valid baselines that may proceed without full semantic proof."""

    if not isinstance(value, dict):
        return None
    if value.get("status") == "unavailable":
        return "reviewer_unavailable"
    if (
        value.get("status") == "repair_required"
        and value.get("coverage_complete") is False
        and not semantic_review_has_verified_blocking_finding(value)
    ):
        return "coverage_incomplete_without_verified_blocker"
    return None


def semantic_review_requires_repair(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("status") == "repair_required" or (
        value.get("accepted") is False and value.get("status") != "unavailable"
    )


def semantic_review_blocks_baseline_acceptance(value: dict[str, Any] | None) -> bool:
    """Only an authoritative semantic finding can invalidate a Core-valid baseline.

    A provider timeout or malformed model response is an observability failure,
    not evidence that the generated baseline is semantically wrong.  The
    baseline may seed requested improvement rounds in degraded-review mode;
    later candidates still use the stricter promotion gate.
    """

    if not isinstance(value, dict):
        return False
    if semantic_review_has_verified_blocking_finding(value):
        return True
    if semantic_review_baseline_degraded_reason(value):
        return False
    return semantic_review_requires_repair(value)


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

    # Provider-level or coverage-only semantic failures are not a concrete code
    # repair target. Spend another Worker attempt only when the review contains
    # an evidence-backed finding or an explicit incomplete component/group.
    if semantic_review_requires_repair(semantic_review) and semantic_review_has_concrete_repair_target(
        semantic_review
    ):
        return True

    summary = getattr(cycle, "summary", None)
    if summary is None:
        return False
    total = int(getattr(summary, "total", 0) or 0)
    valid = int(getattr(summary, "valid", 0) or 0)
    failed = int(getattr(summary, "failed", 0) or 0)
    if total == 0:
        return True
    if failed > 0 or valid < total:
        return True
    candidate_key = _summary_objective_key_from_cycle(cycle)
    # A legal candidate that is not strictly better is an experiment result,
    # not a code defect. Without a concrete repair target, another generic
    # "improve it" attempt only invites arbitrary edits and token-heavy loops.
    if incumbent_key is not None and candidate_key and candidate_key <= incumbent_key:
        return False
    return False


def semantic_review_has_concrete_repair_target(value: dict[str, Any] | None) -> bool:
    """Require source-backed findings or explicit incomplete coverage before repair."""

    if not isinstance(value, dict):
        return False
    if semantic_review_has_verified_blocking_finding(value):
        return True
    return any(
        isinstance(item, dict) and item.get("status") not in {None, "", "implemented"}
        for key in ("component_coverage", "coupled_group_coverage")
        for item in (value.get(key) or [])
    )


def is_nonrepairable_worker_failure(cycle: Any) -> bool:
    worker_result = getattr(cycle, "worker_result", None)
    if worker_result is None:
        return False
    status = str(getattr(worker_result, "status", "") or "")
    if status not in {
        "unavailable",
        "timeout",
        "failed_runtime",
        "authorization_required",
        "invalid_assignment",
        "skipped",
    }:
        return False
    if getattr(worker_result, "changed_files", None):
        return False
    artifacts = getattr(worker_result, "artifacts", None) or {}
    if status == "timeout":
        session_id = str(
            artifacts.get("observed_session_id") or artifacts.get("session_id") or ""
        ).strip()
        try:
            event_stream_bytes = int(artifacts.get("event_stream_bytes") or 0)
        except (TypeError, ValueError):
            event_stream_bytes = 0
        if session_id and event_stream_bytes > 0:
            return False
    return not bool(artifacts.get("proposal"))


def current_round_repair_feedback(
    *,
    attempt_index: int,
    max_repair_attempts: int,
    previous_attempts: list[dict[str, Any]],
    repair_anchor: dict[str, Any] | None = None,
    local_trial_refinement: bool = False,
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
    repair_targets = collect_current_round_repair_targets(previous_attempts)
    status = (
        "repair_required"
        if repair_targets
        else "refinement_required"
        if local_trial_refinement or legal_no_improvement or anchor_quality_regression
        else "repair_required"
    )
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
            "rule": (
                "The candidate worktree for this repair was recreated from this best Core-valid attempt. "
                "Preserve its effective mechanisms and repair accumulated findings with the smallest coherent edit."
            ),
        }
    must_do = [
        "Treat the previous attempt as rejected inside this same direction; do not repeat its unsafe actions or protected-fact regressions.",
        "Repair the listed deterministic preflight/Core issues before introducing an unrelated objective-improvement idea.",
        "Preserve the repair-base worktree and verified mechanisms; make one bounded legal edit that can pass preflight and Core validation.",
    ]
    if legal_no_improvement or anchor_quality_regression:
        must_do.append(
            "Keep the same direction and make one material refinement to the rule/operator mechanism before trying a new direction."
        )
    if repair_targets:
        must_do.append(
            "Repair the blocking items in repair_targets explicitly; do not rewrite unrelated working behavior."
        )
    if repair_targets.get("result_revalidation_top_errors"):
        must_do.append(
            "Treat result_revalidation_top_errors as the primary fixed validator evidence for this repair; remove those concrete runtime/schema/legality failures before addressing generic candidate_result_revalidation_failed."
        )
    elif repair_targets.get("diagnostic_smoke_top_errors"):
        must_do.append(
            "Treat diagnostic_smoke_top_errors as concrete Core/evaluator evidence from the rejected attempt; repair those runtime/schema/legality errors before claiming the solver is ready."
        )
    if repair_targets.get("baseline_core_valid_anchor"):
        must_do.append(
            "This repair starts from repair_targets.baseline_core_valid_anchor, not from the most recent degraded attempt. "
            "Keep unrelated anchor behavior byte-for-byte where practical; do not broaden the patch merely to compensate "
            "for stochastic objective movement."
        )
    return {
        "status": status,
        "allow_objective_refinement": bool(local_trial_refinement),
        "attempt_index": attempt_index,
        "max_repair_attempts": max_repair_attempts,
        "previous_attempts": [
            repair_attempt_context_payload(attempt)
            for index, attempt in enumerate(recent)
        ],
        "repair_targets": repair_targets,
        "must_do": must_do,
        "avoid": sorted(
            {
                signature
                for attempt in recent
                for signature in (attempt.get("failure_signatures") or [])
                if isinstance(signature, str) and not signature.startswith("algorithm_semantic_")
            }
        ),
    }


def repair_attempt_context_payload(
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Keep deterministic repair evidence and remove advisory reviews.

    `repair_targets` is the authoritative aggregation of compile, path,
    runtime, and fixed-validator failures. Repeating JA check details or a
    legacy semantic review here would let advisory diagnostics bypass that
    filtering and become accidental worker tasks.
    """

    payload = dict(attempt)
    signatures = payload.get("failure_signatures")
    if isinstance(signatures, list):
        payload["failure_signatures"] = [
            item
            for item in signatures
            if isinstance(item, str) and not item.startswith("algorithm_semantic_")
        ]
    judgment = payload.get("agentic_judgment")
    if isinstance(judgment, dict):
        payload["agentic_judgment"] = {
            key: judgment.get(key)
            for key in ("accepted", "right", "stage", "issues", "suggestions")
            if key in judgment
        }
    payload.pop("semantic_review", None)
    return payload


def _blocking_semantic_repair_summary(
    findings: list[dict[str, Any]],
    *,
    incomplete_components: list[dict[str, Any]] | None = None,
    incomplete_groups: list[dict[str, Any]] | None = None,
) -> str:
    """只概括阻塞项，避免非阻塞 warning 被 Coding Agent 当成修补任务。"""

    coverage_labels = [
        *(str(item.get("component_id") or "") for item in incomplete_components or []),
        *(str(item.get("group_id") or "") for item in incomplete_groups or []),
    ]
    coverage_labels = [item for item in coverage_labels if item]
    if not findings and not coverage_labels:
        return "No evidence-backed blocking semantic findings remain."
    labels = [
        str(finding.get("repair") or finding.get("claim") or finding.get("finding_id") or "semantic finding")
        for finding in findings
    ]
    parts = []
    if coverage_labels:
        parts.append("Incomplete method coverage: " + ", ".join(coverage_labels))
    if labels:
        parts.append("Evidence-backed findings: " + "; ".join(labels[:8]))
    return " | ".join(parts)


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
            add_list("agentic_judgment_issues", judgment.get("issues"))
            add_list("agentic_judgment_suggestions", judgment.get("suggestions"))
            add_list("agent_generated_solver_self_check_risks", checks.get("agent_generated_solver_self_check_risks"))
            add_list("incomplete_solution_acceptance_risks", checks.get("incomplete_solution_acceptance_risks"))
            add_list("protected_promoted_fact_regressions", checks.get("protected_promoted_fact_regressions"))
        result_revalidation = checks.get("result_revalidation") if isinstance(checks.get("result_revalidation"), dict) else {}
        add_list("result_revalidation_top_errors", result_revalidation.get("top_errors"))
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
    if semantic_review_has_verified_blocking_finding(semantic_review):
        signatures.append(semantic_review_promotion_block_reason(semantic_review))
        for finding in semantic_review.get("findings") or []:
            if isinstance(finding, dict) and finding.get("blocking"):
                signatures.append(f"algorithm_semantic_{finding.get('category') or 'method_semantics'}")
    return _dedupe([_normalize_failure_token(item) for item in signatures if item])


def in_round_repair_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    repair_attempt_count = max(0, len(attempts) - 1)
    final_attempt = attempts[-1] if attempts else {}
    selected_attempt = next(
        (
            item
            for item in attempts
            if isinstance(item, dict) and item.get("disposition") == "selected"
        ),
        final_attempt,
    )
    final_summary = selected_attempt.get("summary") if isinstance(selected_attempt, dict) else {}
    final_total = int((final_summary or {}).get("total", 0) or 0) if isinstance(final_summary, dict) else 0
    final_valid = int((final_summary or {}).get("valid", 0) or 0) if isinstance(final_summary, dict) else 0
    final_accepted = final_total > 0 and final_valid == final_total
    return {
        "attempt_count": len(attempts),
        "repair_attempt_count": repair_attempt_count,
        "recovered": bool(
            repair_attempt_count
            and final_accepted
            and final_valid == final_total
        ),
        "final_attempt_index": final_attempt.get("attempt_index"),
        "selected_attempt_index": selected_attempt.get("attempt_index"),
        "final_attempt_superseded": selected_attempt is not final_attempt,
        "selection_reason": (
            "best_valid_activated_objective"
            if selected_attempt is not final_attempt
            else "final_trial_selected"
        ),
        "session_reuse_enabled": any(bool(item.get("session_id")) for item in attempts),
        "reused_trial_count": sum(1 for item in attempts if item.get("session_reused")),
        "termination_reason": final_attempt.get("termination_reason"),
        "ledger_path": final_attempt.get("local_trial_ledger_path"),
        "attempts": attempts,
    }


def normalize_baseline_source(value: str) -> str:
    normalized = str(value or "current_project").strip().lower().replace("-", "_")
    if normalized in {"agent", "agent_generated", "agent_written", "generated"}:
        return "agent_generated"
    if normalized in {"provided", "provided_project", "starter_project", "uploaded_project"}:
        return "provided_project"
    return "current_project"


def continuing_direction_worker_session(
    previous_round: LoopRoundRecord | None,
    proposed_direction: dict[str, Any],
) -> str | None:
    """Carry the winning Worker session until an explicit direction pivot."""

    if previous_round is None or not previous_round.worker_session_id:
        return None
    previous_plan = previous_round.direction_plan or {}
    previous_family = primary_method_family(previous_plan)
    proposed_family = primary_method_family(proposed_direction)
    if not previous_family or previous_family != proposed_family:
        return None
    stage = str(proposed_direction.get("experiment_stage") or "").strip().lower().replace("-", "_")
    if stage in {"pivot", "research_tournament"}:
        return None
    return previous_round.worker_session_id


def continuing_direction_worker_lane(
    previous_round: LoopRoundRecord | None,
    proposed_direction: dict[str, Any],
) -> str | None:
    """Return the stable lane ID that owns a reusable Worker session."""

    if continuing_direction_worker_session(previous_round, proposed_direction) is None:
        return None
    previous_plan = previous_round.direction_plan or {}
    selected = (
        previous_plan.get("selected_candidate_variant")
        if isinstance(previous_plan.get("selected_candidate_variant"), dict)
        else {}
    )
    candidate_id = str(selected.get("candidate_id") or "").strip()
    if candidate_id:
        return candidate_id
    competition = (
        previous_plan.get("competition_result")
        if isinstance(previous_plan.get("competition_result"), dict)
        else {}
    )
    return str(competition.get("selected_candidate_id") or "").strip() or None


def primary_method_family(plan: dict[str, Any]) -> str:
    direct = str(plan.get("method_family") or "").strip().lower()
    if direct:
        return direct
    for item in plan.get("method_families") or []:
        if isinstance(item, dict):
            family = str(item.get("id") or "").strip().lower()
        else:
            family = str(item or "").strip().lower()
        if family:
            return family
    return ""


def agent_generated_baseline_is_accepted(
    baseline_generation: dict[str, Any] | None,
    *,
    baseline_summary: RunSummary,
    baseline_key: tuple[float, ...],
) -> bool:
    if not isinstance(baseline_generation, dict) or baseline_generation.get("source") != "agent_generated":
        return False
    semantic_review = (
        baseline_generation.get("semantic_review")
        if isinstance(baseline_generation.get("semantic_review"), dict)
        else {}
    )
    return (
        baseline_generation.get("status") == "ok"
        and not semantic_review_blocks_baseline_acceptance(semantic_review)
        and baseline_summary.total > 0
        and baseline_summary.valid == baseline_summary.total
        and not _all_negative_infinity(baseline_key)
    )


def agent_generated_baseline_failure_reason(
    baseline_generation: dict[str, Any] | None,
    *,
    baseline_summary: RunSummary,
    baseline_key: tuple[float, ...],
) -> str:
    """返回 baseline 无法成为 incumbent 的首个确定性阻塞原因。"""

    if not isinstance(baseline_generation, dict):
        return "baseline_generation_metadata_missing"
    if baseline_generation.get("source") != "agent_generated":
        return "baseline_generation_source_invalid"
    generation_status = str(baseline_generation.get("status") or "")
    if generation_status and generation_status != "ok":
        return generation_status
    semantic_review = (
        baseline_generation.get("semantic_review")
        if isinstance(baseline_generation.get("semantic_review"), dict)
        else {}
    )
    if semantic_review_blocks_baseline_acceptance(semantic_review):
        return "semantic_review_rejected"
    if baseline_summary.total <= 0:
        return "evaluator_produced_no_results"
    if baseline_summary.valid != baseline_summary.total:
        return "evaluator_rejected_baseline"
    if _all_negative_infinity(baseline_key):
        return "objective_missing"
    return "baseline_acceptance_contract_failed"


def agent_generated_baseline_cycle_is_core_accepted(cycle: Any) -> bool:
    summary = getattr(cycle, "summary", None)
    return (
        summary is not None
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


def select_best_baseline_repair_cycle(
    cycles: list[tuple[int, Any, Path, dict[str, Any]]],
    *,
    objectives: list[ObjectiveSpec],
) -> tuple[int, Any, Path, dict[str, Any]] | None:
    """选择下一次修补基底：先保留语义进度，再比较 Core 目标。"""

    core_valid = [item for item in cycles if agent_generated_baseline_cycle_is_core_accepted(item[1])]
    if not core_valid:
        return None
    return max(
        core_valid,
        key=lambda item: (
            *_semantic_repair_progress_rank(item[3]),
            *summary_objective_key(item[1].summary, objectives),
            item[0],
        ),
    )


def _semantic_repair_progress_rank(semantic_review: dict[str, Any] | None) -> tuple[int, int]:
    if not semantic_review_blocks_promotion(semantic_review):
        return (2, 0)
    findings = semantic_review.get("findings") if isinstance(semantic_review, dict) else []
    blocking_count = sum(
        1 for finding in findings or [] if isinstance(finding, dict) and finding.get("blocking")
    )
    if blocking_count:
        return (1, -blocking_count)
    return (0, -1_000_000)


def semantic_review_baseline_rank(semantic_review: dict[str, Any] | None) -> int:
    """Rank semantic evidence for baseline selection without relaxing promotion."""

    value = semantic_review if isinstance(semantic_review, dict) else {}
    status = value.get("status")
    if status in {"pass", "warning"} and not semantic_review_blocks_promotion(value):
        return 4
    degraded_reason = semantic_review_baseline_degraded_reason(value)
    if degraded_reason == "coverage_incomplete_without_verified_blocker":
        return 3
    if degraded_reason == "reviewer_unavailable":
        return 2
    if not semantic_review_blocks_baseline_acceptance(value):
        return 4
    return 0


def agent_generated_baseline_cycle_rank(
    cycle: Any,
    *,
    attempt_index: int,
    objectives: list[ObjectiveSpec],
    semantic_review: dict[str, Any] | None = None,
) -> tuple[float, ...]:
    summary = getattr(cycle, "summary", None)
    worker_result = getattr(cycle, "worker_result", None)
    changed_files = list(getattr(worker_result, "changed_files", []) or [])
    has_changed_files = bool(changed_files)
    core_total = int(getattr(summary, "total", 0) or 0) if summary is not None else 0
    core_valid = int(getattr(summary, "valid", 0) or 0) if summary is not None else 0
    diagnostic = getattr(cycle, "diagnostic_smoke_summary", None)
    diagnostic_total = int(getattr(diagnostic, "total", 0) or 0) if diagnostic is not None else 0
    diagnostic_valid = int(getattr(diagnostic, "valid", 0) or 0) if diagnostic is not None else 0
    artifacts = getattr(worker_result, "artifacts", None) or {}

    scored_summary = summary if core_total > 0 and core_valid == core_total else diagnostic
    objective_key = summary_objective_key(scored_summary, objectives) if scored_summary is not None else ()
    if core_total > 0 and core_valid == core_total:
        semantic_rank = 900 + 25 * semantic_review_baseline_rank(semantic_review)
        return (semantic_rank, *objective_key, attempt_index)
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
        degraded_reason = semantic_review_baseline_degraded_reason(semantic_review)
        if degraded_reason == "reviewer_unavailable":
            return "core_evaluator_valid_with_degraded_semantic_review"
        if degraded_reason == "coverage_incomplete_without_verified_blocker":
            return "core_evaluator_valid_with_incomplete_semantic_coverage"
        if semantic_review_blocks_baseline_acceptance(semantic_review):
            return "core_evaluator_valid_but_algorithm_semantic_repair_required"
        return "core_evaluator_valid"
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


# ---------------------------------------------------------------------------
# Agent-generated baseline：从空 solver 起步，并保护修补过程中最强合法锚点。
# ---------------------------------------------------------------------------

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
    assignment_issuer: DirectionPlanningAgent | None = None,
    direction_plan: dict[str, Any] | None = None,
    repair_attempts: int = DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
    cancellation: CancellationToken | None = None,
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
    local_trial_count = max_repair_attempts + 1
    worker_session_id: str | None = None
    try:
        session_reuse_enabled = bool(worker.capabilities().supports_session_reuse)
    except Exception:  # noqa: BLE001 - optional capability must fail closed.
        session_reuse_enabled = False
    baseline_context_path = baseline_dir / "context_packet.json"
    attempts: list[dict[str, Any]] = []
    baseline_stage = 1
    try:
        cycle: Any | None = None
        cycle_attempts: list[tuple[int, Any, Path, dict[str, Any]]] = []
        repair_project_root = source_project
        repair_anchor_attempt_index: int | None = None
        parent_assignment_path: Path | None = None
        planner = assignment_issuer or EvidenceDrivenMainAgent()
        for attempt_index in range(local_trial_count):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
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
                    local_trial_refinement=bool(
                        isinstance(repair_anchor_attempt, dict)
                        and int((repair_anchor_attempt.get("summary") or {}).get("total", 0) or 0) > 0
                        and int((repair_anchor_attempt.get("summary") or {}).get("valid", 0) or 0)
                        == int((repair_anchor_attempt.get("summary") or {}).get("total", 0) or 0)
                    ),
                )
                if attempt_index > 0
                else None
            )
            if repair_feedback is not None:
                repair_feedback["baseline_trial"] = baseline_stage
                repair_feedback["resume_incomplete_baseline"] = baseline_stage == 1
            baseline_context_path = write_baseline_generation_context_packet(
                base_context_packet_path=context_packet_path,
                output_path=attempt_dir / "context_packet.json",
                hidden_incumbent_files=hidden_incumbent_files,
                current_round_repair=repair_feedback,
                direction_plan=direction_plan,
            )
            assignment_feedback = load_context_packet(baseline_context_path).effective_context.get("loop_feedback") or {}
            assignment_issue = request_worker_assignment(
                planner,
                WorkerAssignmentRequest(
                    round_index=-1,
                    attempt_index=attempt_index,
                    context_packet_path=baseline_context_path,
                    direction_plan=direction_plan or baseline_semantic_direction_plan(baseline_context_path),
                    loop_feedback=assignment_feedback,
                    output_dir=attempt_dir,
                    max_steps=max_steps,
                    max_runtime_seconds=max_runtime_seconds,
                    parent_assignment_path=parent_assignment_path,
                ),
            )
            requested_session_id = worker_session_id if session_reuse_enabled else None
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
                worker_assignment_path=assignment_issue.artifact_path,
                worker_input_root=project_root,
                session_id=requested_session_id,
                local_trial_index=attempt_index,
                local_trial_count=local_trial_count,
                cancellation=cancellation,
            )
            session_telemetry = worker_session_telemetry(
                cycle.worker_result.artifacts or {},
                requested_session_id=requested_session_id,
            )
            observed_session_id = str(
                session_telemetry.get("observed_session_id") or ""
            ).strip()
            if session_reuse_enabled and observed_session_id:
                worker_session_id = (
                    observed_session_id
                    if requested_session_id is None or session_telemetry.get("session_reused") is True
                    else None
                )
            # baseline 尚无正式 incumbent，因此单独保存迄今最强的 Core 合法
            # attempt。后续修补若退化，会回到该锚点继续，而不是越修越差。
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
            # 正确性和目标质量是两条独立证据轴。即使 makespan 暂时退化，也必须
            # 完成语义复审，否则已修好的 finding 会被原样塞进下一次 prompt，形成死循环。
            semantic_review = run_algorithm_semantic_review(
                reviewer=semantic_reviewer,
                cycle=cycle,
                context_packet_path=baseline_context_path,
                direction_plan=direction_plan or baseline_semantic_direction_plan(baseline_context_path),
                round_index=-1,
                attempt_index=attempt_index,
                output_dir=attempt_dir / "semantic_review",
            )
            if anchor_quality_regressed and prior_core_anchor is not None:
                semantic_review = {
                    **semantic_review,
                    "core_quality_regression": {
                        "anchor_attempt_index": prior_core_anchor[0],
                        "anchor_key": list(prior_anchor_key),
                        "candidate_key": list(candidate_key),
                    },
                }
            attempt_payload = round_attempt_payload(
                cycle,
                attempt_index=attempt_index,
                context_packet_path=baseline_context_path,
                semantic_review=semantic_review,
            )
            attempt_payload["worker_assignment_path"] = str(assignment_issue.artifact_path)
            attempt_payload["assignment_id"] = assignment_issue.assignment.assignment_id
            attempt_payload["repair_base_attempt_index"] = repair_anchor_attempt_index
            attempt_payload["local_trial_index"] = attempt_index + 1
            attempt_payload["local_trial_count"] = local_trial_count
            attempt_payload["baseline_trial"] = baseline_stage
            attempt_payload["session_id"] = worker_session_id
            attempt_payload.update(session_telemetry)
            attempts.append(attempt_payload)
            parent_assignment_path = assignment_issue.artifact_path
            cycle_attempts.append((attempt_index, cycle, baseline_context_path, semantic_review))
            best_repair_anchor = select_best_baseline_repair_cycle(
                cycle_attempts,
                objectives=contract.objectives,
            )
            semantic_passed = agent_generated_baseline_cycle_is_core_accepted(
                cycle
            ) and not semantic_review_blocks_baseline_acceptance(semantic_review)
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
            completed_baseline_stage = baseline_stage
            if agent_generated_baseline_cycle_is_core_accepted(cycle):
                baseline_stage = min(3, baseline_stage + 1)
            staged_baseline_trial_remaining = (
                attempt_index < max_repair_attempts
                and completed_baseline_stage < 3
                and agent_generated_baseline_cycle_is_core_accepted(cycle)
                and not is_nonrepairable_worker_failure(cycle)
            )
            if semantic_passed and not staged_baseline_trial_remaining:
                break
            should_repair = (
                staged_baseline_trial_remaining
                or anchor_quality_regressed
                or should_attempt_in_round_repair(
                    cycle,
                    semantic_review=semantic_review,
                )
            )
            if attempt_index >= max_repair_attempts or not should_repair:
                break
            if best_repair_anchor is not None:
                repair_anchor_attempt_index = best_repair_anchor[0]
                repair_project_root = Path(best_repair_anchor[1].worktree_path)
            else:
                repair_anchor_attempt_index = attempt_index
                repair_project_root = Path(cycle.worktree_path)
        if cycle is None:
            raise RuntimeError("agent-generated baseline did not produce a candidate")
        # 最终选择最强且可接受的 baseline attempt，不简单采用最后一次修补。
        selected_attempt_index, selected_cycle, selected_context_path, selected_semantic_review = select_agent_generated_baseline_cycle(
            cycle_attempts,
            objectives=contract.objectives,
        )
        repair_summary = in_round_repair_summary(attempts)
        if (
            repair_summary.get("repair_attempt_count")
            and agent_generated_baseline_cycle_is_core_accepted(selected_cycle)
            and not semantic_review_blocks_baseline_acceptance(selected_semantic_review)
        ):
            repair_summary["recovered"] = True
        repair_summary["selected_attempt_index"] = selected_attempt_index
        repair_summary["selection_reason"] = agent_generated_baseline_selection_reason(
            selected_cycle,
            selected_semantic_review,
        )
        if selected_attempt_index != repair_summary.get("final_attempt_index"):
            repair_summary["final_attempt_superseded"] = True
        semantic_review_degraded_reason = semantic_review_baseline_degraded_reason(selected_semantic_review)
        if semantic_review_degraded_reason:
            repair_summary["recovery_level"] = "core_valid_semantic_review_degraded"
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
            "diagnostic_smoke": compact_diagnostic_smoke(selected_cycle),
            "agentic_judgment": selected_cycle.agentic_judgment.to_payload(),
            "agentic_error_analysis": selected_cycle.agentic_error_analysis.to_payload()
            if selected_cycle.agentic_error_analysis
            else None,
            "semantic_review": selected_semantic_review,
            "semantic_review_degraded": bool(semantic_review_degraded_reason),
            "semantic_review_degraded_reason": semantic_review_degraded_reason,
            "direction_plan": direction_plan or {},
            "worker_assignments": [
                {
                    "attempt_index": item.get("attempt_index"),
                    "assignment_id": item.get("assignment_id"),
                    "artifact_path": item.get("worker_assignment_path"),
                }
                for item in attempts
            ],
        }
        return selected_cycle.summary, selected_cycle.worktree_path, generation_payload
    except TaskCancelled:
        raise
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
    active_package = loaded.get("active_method_package") if isinstance(loaded.get("active_method_package"), dict) else {}
    plan = {
        "schema_version": 1,
        "direction_id": "agent_generated_baseline",
        "title": "Verify generated baseline method claims against retrieved contracts",
        "strategy_type": "baseline_constructor",
        "hypothesis": str(loaded.get("hypothesis") or "Generate a legal standalone baseline solver.")[:1200],
        "knowledge_paths": list(loaded.get("auto_knowledge_cards") or [])[:12],
        # 语义复核只能检查 Main 已显式激活的包；目录推荐项不能旁路两阶段选择。
        "method_package_id": active_package.get("package_id"),
    }
    bundle = method_implementation_bundle(active_package)
    if bundle:
        plan["implementation_bundle"] = bundle
        contract_paths = list(
            dict.fromkeys(
                str(item) for item in bundle.get("contract_paths") or [] if str(item).strip()
            )
        )
        supplemental_paths = list(
            dict.fromkeys(
                [
                    *(str(item) for item in active_package.get("assets") or []),
                    *(str(item) for item in loaded.get("auto_knowledge_cards") or []),
                ]
            )
        )
        supplemental_paths = [
            item for item in supplemental_paths if item and item not in contract_paths
        ][: max(0, 12 - len(contract_paths))]
        plan["knowledge_paths"] = [*contract_paths, *supplemental_paths]
    return plan


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
    request = DirectionPlanRequest(
        round_index=-1,
        context_packet_path=context_packet_path,
        loop_feedback=feedback,
        output_dir=output_dir,
    )
    try:
        return planner.plan_direction(request)
    except TaskCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - baseline planning must retain deterministic fallback.
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "planner_exception.txt").write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        return EvidenceDrivenMainAgent().plan_direction(request)


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
                "Combine only the method families explicitly selected in this baseline direction.",
            ],
        }
        activate_method_package_context(refreshed, direction_plan=direction_plan)
        activate_direction_knowledge_context(refreshed, direction_plan=direction_plan)
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


# ---------------------------------------------------------------------------
# 目标比较与晋升复验
# ---------------------------------------------------------------------------

def summary_objective_key(summary: RunSummary, objectives: list[ObjectiveSpec]) -> tuple[float, ...]:
    """把多目标指标统一成“字典序越大越好”的可比较 key。

    minimize 指标取负，maximize 保持原值；缺失指标、不完整算例覆盖或违反
    threshold 都变成 `-inf`，从而天然失去 promotion 资格。
    """

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
    cancellation: CancellationToken | None = None,
) -> dict[str, Any]:
    """Return the evaluator-backed promotion decision for a worker candidate.

    The default path keeps the historic loop semantics: one evaluator-backed
    strict improvement promotes.  When promotion_repeats is greater than one,
    the candidate must also beat the current incumbent on an equal repeated
    probe, using the mean objective key across all repeated records.
    """

    # 首次比较不过就无需重复运行；只有看起来严格提升时才支付复验成本。
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
    # incumbent 与 candidate 使用完全相同的重复契约，避免比较预算不一致。
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
        cancellation=cancellation,
    )
    candidate_summary, candidate_records = _run_harness_with_records(
        contract=repeat_contract,
        project_root=candidate_worktree,
        output_dir=output_dir / "candidate",
        cancellation=cancellation,
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


def candidate_incumbent_from_baseline(
    *,
    objective_key: tuple[float, ...],
    worktree: Path,
    summary: RunSummary,
) -> CandidateIncumbent | None:
    if _all_negative_infinity(objective_key):
        return None
    return CandidateIncumbent(
        objective_key=objective_key,
        worktree=worktree.resolve(),
        candidate_id="baseline",
        round_index=-1,
        summary=summary_payload(summary),
        activation_status=None,
    )


def update_candidate_incumbent(
    current: CandidateIncumbent | None,
    candidate: Any,
    *,
    round_index: int,
) -> CandidateIncumbent | None:
    if not isinstance(candidate, dict):
        return current
    key_values = candidate.get("objective_key") or []
    worktree_value = str(candidate.get("worktree") or "").strip()
    if not key_values or not worktree_value:
        return current
    objective_key = tuple(float(value) for value in key_values)
    if _all_negative_infinity(objective_key):
        return current
    worktree = Path(worktree_value).resolve()
    if not worktree.exists() or (current is not None and objective_key <= current.objective_key):
        return current
    activation = candidate.get("mechanism_activation") or {}
    return CandidateIncumbent(
        objective_key=objective_key,
        worktree=worktree,
        candidate_id=str(candidate.get("candidate_id") or "unknown"),
        round_index=round_index,
        summary=dict(candidate.get("summary") or {}),
        activation_status=str(activation.get("status") or "") or None,
    )


def candidate_incumbent_payload(value: CandidateIncumbent | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "objective_key": list(value.objective_key),
        "worktree": str(value.worktree),
        "candidate_id": value.candidate_id,
        "round_index": value.round_index,
        "summary": bounded_candidate_incumbent_summary(value.summary),
        "activation_status": value.activation_status,
    }


def bounded_candidate_incumbent_summary(value: Any) -> dict[str, Any]:
    """Project only bounded decision evidence for secondary incumbent tiers."""

    summary = value if isinstance(value, dict) else {}
    projected = {
        "total": summary.get("total"),
        "valid": summary.get("valid"),
        "failed": summary.get("failed"),
        "best_experiment_id": summary.get("best_experiment_id"),
        "best_metrics": summary.get("best_metrics") or {},
        "best_candidate_id": summary.get("best_candidate_id"),
        "best_candidate_metrics": summary.get("best_candidate_metrics"),
        "validation_summary": summary.get("validation_summary") or {},
    }
    return compact_json(projected, max_chars=12_000).payload


def activation_contract_required(direction_plan: Any) -> bool:
    if not isinstance(direction_plan, dict):
        return False
    try:
        return int(direction_plan.get("activation_contract_version") or 0) >= 1
    except (TypeError, ValueError):
        return False


def candidate_incumbent_from_payload(value: Any) -> CandidateIncumbent | None:
    if not isinstance(value, dict):
        return None
    worktree_value = str(value.get("worktree") or "").strip()
    key_values = value.get("objective_key") or []
    if not worktree_value or not key_values:
        return None
    worktree = Path(worktree_value).resolve()
    if not worktree.exists():
        return None
    try:
        objective_key = tuple(float(item) for item in key_values)
        round_index = int(value.get("round_index", -1))
        summary = dict(value.get("summary") or {})
    except (TypeError, ValueError):
        return None
    if not objective_key or any(math.isnan(item) for item in objective_key):
        return None
    return CandidateIncumbent(
        objective_key=objective_key,
        worktree=worktree,
        candidate_id=str(value.get("candidate_id") or "unknown"),
        round_index=round_index,
        summary=summary,
        activation_status=str(value.get("activation_status") or "") or None,
    )


# ---------------------------------------------------------------------------
# 轮间记忆：把完整历史压缩成下一轮真正需要的保护项、失败项和经验层级。
# ---------------------------------------------------------------------------

def loop_feedback_payload(
    *,
    round_index: int,
    contract: TaskContract,
    baseline_summary: RunSummary,
    baseline_key: tuple[float, ...],
    incumbent_key_before: tuple[float, ...],
    incumbent_worktree: Path,
    previous_rounds: list[LoopRoundRecord],
    best_legal_incumbent: CandidateIncumbent | None = None,
    best_activated_incumbent: CandidateIncumbent | None = None,
    baseline_generation: dict[str, Any] | None = None,
    current_round_repair: dict[str, Any] | None = None,
    current_direction_plan: dict[str, Any] | None = None,
    user_intervention: dict[str, Any] | None = None,
    max_competing_workers: int = 4,
) -> dict[str, Any]:
    """构建 Main/Coding Agent 共用的 evaluator-backed 动态反馈。

    这里不复制历史 solver，也不把具体分数沉淀为方法知识；只保留方向、
    门禁、promotion 事实、失败签名和产物引用。
    """

    ordered_objectives = sorted(contract.objectives, key=lambda item: item.priority)
    previous_round_payloads = [round_record_payload(item) for item in previous_rounds]
    incumbent_summary = summary_payload(baseline_summary)
    for previous_round in previous_rounds:
        if previous_round.decision == "promoted":
            incumbent_summary = previous_round.candidate_summary
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
        "competition": {
            "max_competing_workers": max(1, min(4, int(max_competing_workers))),
            "isolation_rule": "Each Coding Worker candidate must start from the same incumbent in a separate worktree.",
            "selection_rule": "JA/Core/semantic gates run per candidate; only the best eligible candidate may enter promotion.",
        },
        "round_index": round_index,
        "current_direction": {
            "direction_id": f"d{round_index:03d}",
            "attempt_budget": "bounded_by_in_round_repair_attempts",
            "status": "planned" if current_direction_plan else "planning",
            "rule": "Implement one planned method direction, then repair or refine it inside this direction before moving on.",
        },
        "current_direction_plan": compact_round_direction_plan(current_direction_plan),
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
        "incumbent_tiers": {
            "promoted": {
                "objective_key": list(incumbent_key_before),
                "worktree": str(incumbent_worktree),
            },
            "best_legal": candidate_incumbent_payload(best_legal_incumbent),
            "best_activated": candidate_incumbent_payload(best_activated_incumbent),
            "rule": (
                "Only promoted is the editing base. Best-legal and best-activated are retained "
                "as evidence/recovery anchors and never bypass promotion gates."
            ),
        },
        "baseline_summary": summary_payload(baseline_summary),
        "incumbent_summary": incumbent_summary,
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
            "Treat failure_memory.recent_failures as provisional observations requiring causal review. "
            "Do not turn a rollback into a method-family prohibition; only an identical patch lineage is barred from unchanged replay.",
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
    if user_intervention:
        payload = apply_user_intervention_to_feedback(payload, user_intervention=user_intervention)
    return payload


DIRECTION_PATCH_ACTIONS = {"accept", "continue", "revise", "pivot", "research_tournament"}
DIRECTION_PATCH_FIELDS = {
    "title",
    "strategy_type",
    "hypothesis",
    "worker_objective",
    "diagnosis",
    "experiment_stage",
    "method_family",
    "method_families",
    "knowledge_query",
    "observed_shortcomings",
    "reasoning_trace",
    "incumbent_assessment",
    "evidence_summary",
    "direction_judgment",
    "alternatives_considered",
    "selection_rationale",
    "preserve",
    "change_scope",
    "next_mutation",
    "implementation_order",
    "deliverables",
    "avoid",
    "knowledge_paths",
    "method_package_id",
    "acceptance_checks",
    "activation_checks",
    "stop_conditions",
    "completion_rule",
    "candidate_variants",
}
DIRECTION_FAMILY_FIELDS = {
    "method_family",
    "method_families",
    "knowledge_query",
    "method_package_id",
}
DIRECTION_NON_CLEARABLE_FIELDS = {
    "method_family",
    "method_families",
    "knowledge_query",
    "candidate_variants",
    "activation_checks",
}
DIRECTION_PIVOT_FIELDS = {
    "title",
    "strategy_type",
    "hypothesis",
    "worker_objective",
    "diagnosis",
    "experiment_stage",
    "method_family",
    "method_families",
    "knowledge_query",
    "method_package_id",
    "change_scope",
    "next_mutation",
    "candidate_variants",
    "activation_checks",
}


def continue_current_direction_plan(
    *,
    previous_direction_plan: dict[str, Any],
    proposed_direction_plan: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    """Advance the previous plan without paying for another Main revision."""

    if not previous_direction_plan:
        return dict(proposed_direction_plan)
    plan = dict(previous_direction_plan)
    previous_direction_id = str(plan.get("direction_id") or "").strip()
    for key in (
        "artifact_path",
        "competition_result",
        "mechanism_activation",
        "selected_candidate_variant",
        "user_intervention",
    ):
        plan.pop(key, None)
    plan["direction_id"] = f"d{round_index:03d}"
    if previous_direction_id:
        plan["parent_direction_id"] = previous_direction_id
    previous_stage = str(plan.get("experiment_stage") or "probe").strip()
    # A family tournament is a typed cross-family state. Collapsing it to a
    # probe makes its candidate variants ineligible to bind their own Method
    # Packages and destroys per-family lane lineage on the next round.
    plan["experiment_stage"] = (
        "research_tournament" if previous_stage == "research_tournament" else "probe"
    )
    plan["continuation"] = {
        "status": "continued_by_user_policy",
        "skipped_proposed_direction_id": proposed_direction_plan.get("direction_id"),
        "skipped_proposed_method_family": proposed_direction_plan.get("method_family"),
    }
    return plan


def normalize_user_intervention(value: Any, *, round_index: int) -> dict[str, Any]:
    """Turn UI text or an API patch into one versioned between-round contract."""

    raw = value if isinstance(value, dict) else {}
    patch_raw = raw.get("direction_patch") if isinstance(raw.get("direction_patch"), dict) else raw
    direction = str(raw.get("direction") if raw else value or "").strip()[:4_000]
    action = str(patch_raw.get("action") or "revise").strip().lower().replace("-", "_")
    if action not in DIRECTION_PATCH_ACTIONS:
        action = "revise"
    set_payload = {
        str(key): item
        for key, item in (patch_raw.get("set") or {}).items()
        if str(key) in DIRECTION_PATCH_FIELDS
    } if isinstance(patch_raw.get("set"), dict) else {}
    clear_fields = [
        str(item)
        for item in patch_raw.get("clear_fields") or patch_raw.get("clear") or []
        if str(item) in DIRECTION_PATCH_FIELDS
    ]
    return {
        "schema_version": 1,
        "direction": direction,
        "applies_to_round": round_index,
        "source": str(raw.get("source") or "user_between_rounds")[:80],
        "direction_patch": {
            "schema_version": 1,
            "action": action,
            "instructions": str(patch_raw.get("instructions") or direction)[:4_000],
            "set": set_payload,
            "set_fields": [
                str(item)
                for item in patch_raw.get("set_fields") or []
                if str(item) in DIRECTION_PATCH_FIELDS
            ],
            "clear_fields": clear_fields,
            "preserve_unspecified": True,
        },
    }


def apply_user_direction_revision(
    original: dict[str, Any],
    revised: dict[str, Any],
    *,
    user_intervention: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a planner revision as a validated field patch over the original plan."""

    requested = dict(user_intervention.get("direction_patch") or {})
    declared = (
        dict(revised.get("user_revision_patch"))
        if isinstance(revised.get("user_revision_patch"), dict)
        else {}
    )
    fallback_revision = str(revised.get("planner") or "") == "evidence_fallback"
    action = str(declared.get("action") or requested.get("action") or "revise").strip().lower()
    if action not in DIRECTION_PATCH_ACTIONS:
        action = "revise"
    explicit_set = dict(requested.get("set") or {})
    if isinstance(declared.get("set"), dict):
        explicit_set.update(declared["set"])
    set_fields = [
        str(item)
        for item in declared.get("set_fields") or requested.get("set_fields") or []
        if str(item) in DIRECTION_PATCH_FIELDS
    ]
    clear_fields = [
        str(item)
        for item in [
            *(requested.get("clear_fields") or []),
            *(declared.get("clear_fields") or []),
        ]
        if str(item) in DIRECTION_PATCH_FIELDS
    ]

    patch_values: dict[str, Any] = {
        key: value for key, value in explicit_set.items() if key in DIRECTION_PATCH_FIELDS
    }
    source = "planner_fallback_explicit_only" if fallback_revision else "declared_patch"
    if set_fields and not fallback_revision:
        for field in set_fields:
            if field in revised:
                patch_values[field] = revised[field]
    elif not patch_values and not fallback_revision:
        # Compatibility for non-OpenCode planners: compile their revised full
        # plan into an explicit diff, while refusing empty/default erasure.
        source = "legacy_planner_diff"
        for field in DIRECTION_PATCH_FIELDS:
            value = revised.get(field)
            if value != original.get(field) and _meaningful_patch_value(value):
                patch_values[field] = value

    if action in {"pivot", "research_tournament"} and not fallback_revision:
        # A pivot is a typed state transition. Its family, experiment, and
        # candidate contract must move together or old-family candidates would
        # survive under a newly selected method label.
        for field in DIRECTION_PIVOT_FIELDS:
            if field in revised and (
                _meaningful_patch_value(revised[field])
                or field in {"method_package_id", "candidate_variants", "activation_checks"}
            ):
                patch_values[field] = revised[field]

    rejected: list[dict[str, str]] = []
    if action not in {"pivot", "research_tournament"}:
        for field in sorted(DIRECTION_FAMILY_FIELDS.intersection(patch_values)):
            if patch_values[field] != original.get(field):
                patch_values.pop(field, None)
                rejected.append(
                    {
                        "field": field,
                        "reason": "method-family changes require pivot or research_tournament",
                    }
                )

    result = dict(original)
    changed_fields: list[str] = []
    for field, value in patch_values.items():
        pivot_reset = action in {"pivot", "research_tournament"} and field in {
            "method_package_id",
            "candidate_variants",
            "activation_checks",
        }
        if field not in DIRECTION_PATCH_FIELDS or not (_meaningful_patch_value(value) or pivot_reset):
            continue
        if result.get(field) != value:
            result[field] = value
            changed_fields.append(field)
    cleared_fields: list[str] = []
    for field in clear_fields:
        if field in DIRECTION_NON_CLEARABLE_FIELDS:
            rejected.append({"field": field, "reason": "field cannot be cleared by a revision"})
            continue
        if field in result and result.get(field) not in (None, "", [], {}):
            result[field] = [] if isinstance(result[field], list) else {} if isinstance(result[field], dict) else ""
            cleared_fields.append(field)

    result["direction_id"] = original.get("direction_id") or revised.get("direction_id")
    result["user_revision_patch"] = {
        "schema_version": 1,
        "action": action,
        "set_fields": sorted(changed_fields),
        "clear_fields": sorted(cleared_fields),
        "preserve_unspecified": True,
    }
    preserved_fields = sorted(
        field
        for field in DIRECTION_PATCH_FIELDS
        if field in original and field not in changed_fields and field not in cleared_fields
    )
    audit = {
        "schema_version": 1,
        "status": (
            "preserved_original_due_planner_fallback"
            if fallback_revision and not changed_fields and not cleared_fields
            else "applied"
        ),
        "action": action,
        "source": source,
        "instructions": str(requested.get("instructions") or "")[:4_000],
        "changed_fields": sorted(changed_fields),
        "set": {field: result.get(field) for field in sorted(changed_fields)},
        "cleared_fields": sorted(cleared_fields),
        "preserved_fields": preserved_fields,
        "rejected_operations": rejected,
        "base_direction_id": original.get("direction_id"),
        "result_direction_id": result.get("direction_id"),
    }
    return result, audit


def direction_revision_base(
    *,
    proposed_direction_plan: dict[str, Any],
    previous_direction_plan: dict[str, Any],
    user_intervention: dict[str, Any],
) -> dict[str, Any]:
    """Use the active plan as base when a proposed family switch is rejected."""

    source = str(user_intervention.get("source") or "").strip().lower()
    if source not in {
        "user_rejected_direction_change",
        "direction_change_timeout_default_continue",
    }:
        return dict(proposed_direction_plan)
    base = dict(previous_direction_plan)
    if proposed_direction_plan.get("direction_id"):
        base["direction_id"] = proposed_direction_plan["direction_id"]
    if previous_direction_plan.get("direction_id"):
        base["parent_direction_id"] = previous_direction_plan["direction_id"]
    return base


def _meaningful_patch_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def apply_user_intervention_to_feedback(
    feedback: dict[str, Any],
    *,
    user_intervention: dict[str, Any],
) -> dict[str, Any]:
    """Make an explicit between-round user direction the next plan's priority."""

    updated = dict(feedback)
    intervention = dict(user_intervention)
    direction = str(intervention.get("direction") or "").strip()[:4_000]
    direction_patch = dict(intervention.get("direction_patch") or {})
    updated["user_intervention"] = intervention
    guidance = dict(updated.get("next_round_guidance") or {})
    must_do = [str(item) for item in guidance.get("must_do") or [] if str(item).strip()]
    if direction:
        guidance["must_do"] = [direction, *[item for item in must_do if item != direction]][:8]
    updated["next_round_guidance"] = guidance
    instructions = [str(item) for item in updated.get("instructions") or []]
    updated["instructions"] = [
        "The user explicitly intervened between rounds. Return a typed direction_patch with action, set_fields, and clear_fields. Preserve every original plan field not named by the patch, and reconcile the requested intent with hard evaluator and legality constraints.",
        *instructions,
    ]
    updated["direction_patch_contract"] = {
        "schema_version": 1,
        "allowed_actions": sorted(DIRECTION_PATCH_ACTIONS),
        "allowed_fields": sorted(DIRECTION_PATCH_FIELDS),
        "preserve_unspecified": True,
        "requested_action": direction_patch.get("action") or "revise",
    }
    return updated


def plan_direction_with_fallback(
    *,
    planner: DirectionPlanningAgent,
    round_index: int,
    context_packet_path: Path,
    loop_feedback: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Run Main planning without allowing a provider failure to lose a round."""

    request = DirectionPlanRequest(
        round_index=round_index,
        context_packet_path=context_packet_path,
        loop_feedback=loop_feedback,
        output_dir=output_dir,
    )
    try:
        return planner.plan_direction(request)
    except TaskCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - planner failure falls back to evidence-only planning.
        planner_error_path = output_dir / "planner_exception.txt"
        planner_error_path.parent.mkdir(parents=True, exist_ok=True)
        planner_error_path.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        fallback_plan = EvidenceDrivenMainAgent().plan_direction(request)
        fallback_plan = {
            key: value for key, value in fallback_plan.items() if key != "artifact_path"
        }
        fallback_plan["planner_fallback"] = {
            "source": type(planner).__name__,
            "fallback": "EvidenceDrivenMainAgent",
            "reason": str(exc)[:1_000],
            "exception_path": str(planner_error_path.resolve()),
        }
        return write_direction_plan(output_dir, fallback_plan)


def reflect_on_completed_round(
    *,
    planner: DirectionPlanningAgent,
    request: RoundReflectionRequest,
) -> dict[str, Any]:
    """Always produce a round-closing causal interpretation, including the last round."""

    method = getattr(planner, "reflect_on_round", None)
    if callable(method):
        try:
            reflection = method(request)
            if isinstance(reflection, dict):
                return reflection
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - reflection must not discard evaluator evidence.
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "reflection_exception.txt").write_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                encoding="utf-8",
            )
    return EvidenceDrivenMainAgent().reflect_on_round(request)


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
    semantic_review_degraded_reason = (
        str(baseline_generation.get("semantic_review_degraded_reason") or "").strip()
        or semantic_review_baseline_degraded_reason(semantic_review)
    )
    semantic_review_degraded = bool(semantic_review_degraded_reason)
    final_key = list(baseline_key)
    valid = int(summary.get("valid", 0) or 0)
    total = int(summary.get("total", 0) or 0)
    accepted_as_incumbent = (
        baseline_generation.get("status") == "ok"
        and not semantic_review_blocks_baseline_acceptance(semantic_review)
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
            "reason": (
                "accepted_as_initial_incumbent_with_degraded_semantic_review"
                if accepted_as_incumbent and semantic_review_degraded
                else "accepted_as_initial_incumbent"
                if accepted_as_incumbent
                else "baseline_not_valid"
            ),
            "promoted": False,
        },
        "cycle_dir": baseline_generation.get("cycle_dir"),
        "context_packet_path": baseline_generation.get("context_packet_path"),
        "delta_path": "",
        "patch_path": "",
        "promoted_worktree": baseline_generation.get("worktree") if accepted_as_incumbent else None,
        "semantic_review": semantic_review,
        "semantic_review_degraded": semantic_review_degraded,
        "semantic_review_degraded_reason": semantic_review_degraded_reason or None,
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
        "semantic_review_degraded": semantic_review_degraded,
        "semantic_review_degraded_reason": semantic_review_degraded_reason or None,
        "evidence_level": (
            "core_valid_semantic_review_degraded"
            if semantic_review_degraded
            else "core_and_semantic_validated"
            if semantic_review.get("status") in {"pass", "warning"} and semantic_review.get("accepted") is not False
            else "core_valid_semantic_review_not_authoritative"
        ),
        "best_core_valid_anchor": best_core_valid_anchor,
        "proposal_summary": diagnostics.get("summary"),
        "strategy_intent": diagnostics.get("strategy_intent"),
        "rule_operator_hypotheses": (diagnostics.get("rule_operator_hypotheses") or [])[:6],
        "round_payload": round_payload,
        "protection_rule": (
            "This generated baseline is the measured Core-valid incumbent, but semantic method coverage remains "
            "degraded. Preserve its effective structure during improvement rounds and reverify incomplete coverage "
            "before promoting its method claims into validated knowledge or Skills."
            if semantic_review_degraded
            else "This generated baseline is the measured incumbent. Preserve its parser, operation representation, "
            "constructor, decoder, output schema, and active variant repairs unless loop feedback identifies them "
            "as the direct failure source."
        ),
    }


def best_core_valid_baseline_anchor(repair: dict[str, Any]) -> dict[str, Any]:
    """Keep the strongest Core-valid baseline attempt even before semantic repair.

    This anchor preserves effective search structure. Semantic review remains
    the authority for validated method claims, not Core objective promotion.
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
        # Keep the legacy key for persisted baseline-memory compatibility. It
        # now describes semantic-claim eligibility, not Core promotion.
        "promotion_eligible": not semantic_review_blocks_promotion(semantic),
        "semantic_claim_eligible": not semantic_review_blocks_promotion(semantic),
        "semantic_summary": str(semantic.get("summary") or "")[:800],
        "context_packet_path": context_path,
        "candidate_worktree": worktree,
        "patch_path": str(best_attempt.get("patch_path") or ""),
        "rule": (
            "Preserve effective mechanisms from this Core-valid anchor while repairing its semantic findings. "
            "Do not promote its method claims into validated knowledge until semantic review passes."
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
        "proposal_diagnostics": compact_round_proposal_diagnostics(item.proposal_diagnostics),
        "candidate_summary": item.candidate_summary,
        "smoke_gate": item.smoke_gate,
        "promotion_check": item.promotion_check,
        "cycle_dir": item.cycle_dir,
        "context_packet_path": item.context_packet_path,
        "delta_path": item.delta_path,
        "patch_path": item.patch_path,
        "promoted_worktree": item.promoted_worktree,
        "direction_plan": compact_round_direction_plan(item.direction_plan),
        "semantic_review": item.semantic_review or {},
        "mechanism_activation": item.mechanism_activation or {},
        "round_reflection": item.round_reflection or {},
        "worker_session_id": item.worker_session_id,
        "failure_signatures": round_failure_signatures(item),
    }


def load_worker_loop_result(path: Path) -> WorkerLoopResult:
    """Restore a completed loop so later rounds can continue the same experiment."""

    source = path.resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load worker loop result: {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"worker loop result must be a JSON object: {source}")
    final_worktree_value = str(payload.get("final_worktree") or "").strip()
    if not final_worktree_value:
        raise ValueError(f"worker loop result has no incumbent worktree: {source}")
    final_worktree = Path(final_worktree_value).resolve()
    if not final_worktree.exists():
        raise ValueError(f"worker loop incumbent is missing: {final_worktree}")
    baseline_payload = payload.get("baseline_summary")
    if not isinstance(baseline_payload, dict):
        raise ValueError(f"worker loop result has no baseline summary: {source}")
    rounds_payload = payload.get("rounds") or []
    if not isinstance(rounds_payload, list):
        raise ValueError(f"worker loop rounds must be a list: {source}")
    return WorkerLoopResult(
        baseline_key=_float_tuple(payload.get("baseline_key")),
        final_key=_float_tuple(payload.get("final_key")),
        final_worktree=final_worktree,
        rounds=[
            loop_round_record_from_payload(item)
            for item in rounds_payload
            if isinstance(item, dict)
        ],
        baseline_summary=run_summary_from_payload(baseline_payload),
        baseline_source=str(payload.get("baseline_source") or "agent_generated"),
        baseline_generation=(
            payload.get("baseline_generation")
            if isinstance(payload.get("baseline_generation"), dict)
            else None
        ),
        status=str(payload.get("status") or "ok"),
        stop_reason=str(payload.get("stop_reason") or "") or None,
        best_legal_incumbent=(
            candidate_incumbent_from_payload(payload.get("best_legal_incumbent"))
            or CandidateIncumbent(
                objective_key=_float_tuple(payload.get("final_key")),
                worktree=final_worktree,
                candidate_id="legacy_promoted_incumbent",
                round_index=-1,
                summary={},
            )
        ),
        best_activated_incumbent=candidate_incumbent_from_payload(
            payload.get("best_activated_incumbent")
        ),
        lane_development_states={
            candidate_id: state
            for candidate_id, item in (
                payload.get("lane_development_states")
                if isinstance(payload.get("lane_development_states"), dict)
                else {}
            ).items()
            if (state := lane_development_state_from_payload(item)) is not None
        },
    )


def run_summary_from_payload(payload: dict[str, Any]) -> RunSummary:
    return RunSummary(
        total=int(payload.get("total", 0) or 0),
        valid=int(payload.get("valid", 0) or 0),
        failed=int(payload.get("failed", 0) or 0),
        best_experiment_id=str(payload.get("best_experiment_id") or "") or None,
        best_metrics=dict(payload.get("best_metrics") or {}),
        best_candidate_id=str(payload.get("best_candidate_id") or "") or None,
        best_candidate_metrics=(
            dict(payload.get("best_candidate_metrics") or {})
            if payload.get("best_candidate_metrics") is not None
            else None
        ),
        candidate_summaries=list(payload.get("candidate_summaries") or []),
        pareto_frontier=list(payload.get("pareto_frontier") or []),
        validation_summary=dict(payload.get("validation_summary") or {}),
    )


def loop_round_record_from_payload(payload: dict[str, Any]) -> LoopRoundRecord:
    return LoopRoundRecord(
        round_index=int(payload.get("round_index", 0) or 0),
        decision=str(payload.get("decision") or "rolled_back"),
        candidate_key=_float_tuple(payload.get("candidate_key")),
        incumbent_key_after=_float_tuple(payload.get("incumbent_key_after")),
        worker_status=str(payload.get("worker_status") or "unknown"),
        worker_changed_files=[str(item) for item in payload.get("worker_changed_files") or []],
        proposal_fingerprint=str(payload.get("proposal_fingerprint") or ""),
        duplicate_proposal=bool(payload.get("duplicate_proposal")),
        proposal_diagnostics=dict(payload.get("proposal_diagnostics") or {}),
        candidate_summary=dict(payload.get("candidate_summary") or {}),
        smoke_gate=dict(payload.get("smoke_gate") or {}),
        promotion_check=dict(payload.get("promotion_check") or {}),
        cycle_dir=str(payload.get("cycle_dir") or ""),
        context_packet_path=str(payload.get("context_packet_path") or ""),
        delta_path=str(payload.get("delta_path") or ""),
        patch_path=str(payload.get("patch_path") or ""),
        promoted_worktree=str(payload.get("promoted_worktree") or "") or None,
        direction_plan=dict(payload.get("direction_plan") or {}),
        semantic_review=dict(payload.get("semantic_review") or {}),
        mechanism_activation=dict(payload.get("mechanism_activation") or {}),
        round_reflection=dict(payload.get("round_reflection") or {}),
        worker_session_id=str(payload.get("worker_session_id") or "") or None,
    )


def _float_tuple(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("worker loop objective key is missing")
    return tuple(float(item) for item in value)


def compact_round_proposal_diagnostics(value: dict[str, Any] | None) -> dict[str, Any]:
    """Keep decision evidence while avoiding recursive round-feedback growth."""

    diagnostics = value if isinstance(value, dict) else {}
    audit = diagnostics.get("proposal_audit") if isinstance(diagnostics.get("proposal_audit"), dict) else {}
    usage = diagnostics.get("context_usage") if isinstance(diagnostics.get("context_usage"), dict) else {}
    repair = diagnostics.get("in_round_repair") if isinstance(diagnostics.get("in_round_repair"), dict) else {}
    return {
        "status": diagnostics.get("status"),
        "summary": _bounded_text(diagnostics.get("summary"), limit=500),
        "rule_operator_hypotheses": compact_rule_operator_hypotheses(
            diagnostics.get("rule_operator_hypotheses"),
            limit=6,
        ),
        "proposal_audit": {
            "operator_lineage": audit.get("operator_lineage") or {},
            "slot_id": audit.get("slot_id"),
            "failure_memory_status": audit.get("failure_memory_status"),
            "avoid_pattern_count": audit.get("avoid_pattern_count"),
            "changed_core_algorithm_files": list(audit.get("changed_core_algorithm_files") or [])[:12],
            "changed_validator_files": list(audit.get("changed_validator_files") or [])[:12],
            "warnings": list(audit.get("warnings") or [])[:8],
        },
        "context_usage": {
            key: usage.get(key)
            for key in (
                "used_project_intake",
                "used_instance_diagnostics",
                "used_knowledge_cards",
                "used_previous_pipeline_memory",
            )
            if key in usage
        },
        "in_round_repair": {
            **{
                key: repair.get(key)
                for key in (
                    "attempt_count",
                    "repair_attempt_count",
                    "recovered",
                    "recovery_level",
                    "final_attempt_index",
                    "selected_attempt_index",
                    "selection_reason",
                )
                if key in repair
            },
            "attempts": [
                compact_round_attempt_for_history(attempt)
                for attempt in (repair.get("attempts") or [])[-4:]
            ],
        },
    }


def compact_round_attempt_for_history(value: Any) -> dict[str, Any]:
    attempt = value if isinstance(value, dict) else {}
    judgment = attempt.get("agentic_judgment") if isinstance(attempt.get("agentic_judgment"), dict) else {}
    checks = judgment.get("checks") if isinstance(judgment.get("checks"), dict) else {}
    return {
        "attempt_index": attempt.get("attempt_index"),
        "worker_status": attempt.get("worker_status"),
        "changed_files": list(attempt.get("changed_files") or [])[:12],
        "candidate_key": list(attempt.get("candidate_key") or [])[:4],
        "failure_signatures": list(attempt.get("failure_signatures") or [])[:16],
        "agentic_judgment": {
            "accepted": judgment.get("accepted"),
            "issues": list(judgment.get("issues") or [])[:8],
            "checks": {
                key: checks.get(key)
                for key in (
                    "agent_generated_solver_blocking_quality_risks",
                    "agent_generated_solver_quality_risks",
                    "agent_generated_solver_self_check_risks",
                    "agent_generated_runtime_import_risks",
                )
                if key in checks
            },
        },
        "semantic_review": compact_algorithm_semantic_review(attempt.get("semantic_review")),
        "assignment_id": attempt.get("assignment_id"),
        "worker_assignment_path": attempt.get("worker_assignment_path"),
        "context_packet_path": attempt.get("context_packet_path"),
        "patch_path": attempt.get("patch_path"),
        "delta_path": attempt.get("delta_path"),
    }


def compact_round_direction_plan(value: dict[str, Any] | None) -> dict[str, Any]:
    """Keep historical Main decisions without recursively copying full handoffs."""

    plan = value if isinstance(value, dict) else {}
    assessment = plan.get("incumbent_assessment") if isinstance(plan.get("incumbent_assessment"), dict) else {}
    mutation = plan.get("next_mutation") if isinstance(plan.get("next_mutation"), dict) else {}
    competition = plan.get("competition_result") if isinstance(plan.get("competition_result"), dict) else {}
    return {
        "direction_id": plan.get("direction_id"),
        "parent_direction_id": plan.get("parent_direction_id"),
        "title": _bounded_text(plan.get("title"), limit=200),
        "strategy_type": plan.get("strategy_type"),
        "planner": plan.get("planner"),
        "activation_contract_version": plan.get("activation_contract_version"),
        "fallback_transition": plan.get("fallback_transition") or {},
        "planner_fallback": plan.get("planner_fallback") or {},
        "planning_contract_status": plan.get("planning_contract_status") or {},
        "experiment_stage": plan.get("experiment_stage"),
        "method_family": plan.get("method_family"),
        "method_families": [
            dict(item)
            for item in plan.get("method_families") or []
            if isinstance(item, dict)
        ][:4],
        "method_package_id": plan.get("method_package_id"),
        "method_package_selection": plan.get("method_package_selection") or {},
        "worker_lane": plan.get("worker_lane") or {},
        "knowledge_query": _bounded_list(plan.get("knowledge_query"), limit=8),
        "hypothesis": _bounded_text(plan.get("hypothesis"), limit=500),
        "worker_objective": _bounded_text(plan.get("worker_objective"), limit=500),
        "diagnosis": _bounded_text(plan.get("diagnosis"), limit=500),
        "observed_shortcomings": _bounded_list(plan.get("observed_shortcomings"), limit=6),
        "incumbent_assessment": {
            key: _bounded_list(assessment.get(key), limit=6)
            for key in (
                "verified_capabilities",
                "implementation_limits",
                "bottleneck_hypotheses",
                "evidence_refs",
                "unknowns",
            )
        },
        "next_mutation": {
            "target_symbols": _bounded_list(mutation.get("target_symbols"), limit=8),
            "change": _bounded_text(mutation.get("change"), limit=500),
            "expected_effect": _bounded_text(mutation.get("expected_effect"), limit=400),
            "falsification_metrics": _bounded_list(mutation.get("falsification_metrics"), limit=6),
        },
        "change_scope": _bounded_list(plan.get("change_scope"), limit=6),
        "activation_checks": [
            dict(item)
            for item in plan.get("activation_checks") or []
            if isinstance(item, dict)
        ][:8],
        "checkpoint_checks": [
            dict(item)
            for item in plan.get("checkpoint_checks") or []
            if isinstance(item, dict)
        ][:12],
        "candidate_variants": [
            {
                key: item.get(key)
                for key in (
                    "candidate_id",
                    "title",
                    "hypothesis",
                    "strategy_type",
                    "method_family",
                    "method_families",
                    "knowledge_query",
                    "experiment_stage",
                    "change_scope",
                    "next_mutation",
                    "activation_checks",
                )
                if key in item
            }
            for item in plan.get("candidate_variants") or []
            if isinstance(item, dict)
        ][:4],
        "direction_selection": {
            key: (plan.get("direction_selection") or {}).get(key)
            for key in (
                "method_family",
                "method_families",
                "primary_search_pressure",
                "knowledge_query",
                "selection_rationale",
                "selection_source",
            )
            if isinstance(plan.get("direction_selection"), dict)
            and key in plan["direction_selection"]
        },
        "mechanism_activation": plan.get("mechanism_activation") or {},
        "preserve": _bounded_list(plan.get("preserve"), limit=6),
        "avoid": _bounded_list(plan.get("avoid"), limit=6),
        "implementation_order": _bounded_list(plan.get("implementation_order"), limit=10),
        "acceptance_checks": _bounded_list(plan.get("acceptance_checks"), limit=8),
        "completion_rule": _bounded_text(plan.get("completion_rule"), limit=500),
        "selection_rationale": _bounded_text(plan.get("selection_rationale"), limit=500),
        "selected_candidate_variant": plan.get("selected_candidate_variant") or {},
        "competition_result": {
            "status": competition.get("status"),
            "candidate_count": competition.get("candidate_count"),
            "eligible_candidate_count": competition.get("eligible_candidate_count"),
            "selected_candidate_id": competition.get("selected_candidate_id"),
            "selected_objective_key": competition.get("selected_objective_key") or [],
            "measured_candidate_id": competition.get("measured_candidate_id"),
            "measured_objective_key": competition.get("measured_objective_key") or [],
            "selected_for_promotion": competition.get("selected_for_promotion"),
            "best_legal_candidate": compact_observed_candidate(
                competition.get("best_legal_candidate")
            ),
            "best_activated_candidate": compact_observed_candidate(
                competition.get("best_activated_candidate")
            ),
            "lane_development_states": competition.get("lane_development_states") or {},
            "candidates": [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "status": candidate.get("status"),
                    "eligible": candidate.get("eligible"),
                    "objective_key": candidate.get("objective_key") or [],
                    "ja_accepted": candidate.get("ja_accepted"),
                    "core_eligible": candidate.get("core_eligible"),
                    "semantic_eligible": candidate.get("semantic_eligible"),
                    "activation_eligible": candidate.get("activation_eligible"),
                    "activation_required": candidate.get("activation_required"),
                    "mechanism_activation": candidate.get("mechanism_activation") or {},
                    "exact_execution_eligible": candidate.get("exact_execution_eligible"),
                    "exact_execution": candidate.get("exact_execution") or {},
                    "semantic_review": compact_algorithm_semantic_review(
                        candidate.get("semantic_review")
                    ),
                    "worker_model": candidate.get("worker_model"),
                    "worker_status": candidate.get("worker_status"),
                    "requested_session_id": candidate.get("requested_session_id"),
                    "command_session_id": candidate.get("command_session_id"),
                    "observed_session_id": candidate.get("observed_session_id"),
                    "session_reused": candidate.get("session_reused"),
                    "session_event_stream_bytes": candidate.get("session_event_stream_bytes"),
                    "parent_checkpoint": candidate.get("parent_checkpoint"),
                    "parent_objective_key": candidate.get("parent_objective_key") or [],
                    "checkpoint_decision": candidate.get("checkpoint_decision") or {},
                    "lane_development_state": candidate.get("lane_development_state") or {},
                    "archived_lineage": candidate.get("archived_lineage"),
                    "summary": compact_candidate_evaluator_evidence(candidate.get("summary")),
                    "smoke_gate": compact_candidate_smoke_evidence(candidate.get("smoke_gate")),
                    "proposal_diagnostics": compact_candidate_proposal_evidence(
                        candidate.get("proposal_diagnostics")
                    ),
                    "patch_path": candidate.get("patch_path"),
                }
                for candidate in competition.get("candidates") or []
                if isinstance(candidate, dict)
            ][:4],
        }
        if competition
        else {},
    }


def compact_observed_candidate(value: Any) -> dict[str, Any] | None:
    candidate = value if isinstance(value, dict) else {}
    if not candidate:
        return None
    activation = (
        candidate.get("mechanism_activation")
        if isinstance(candidate.get("mechanism_activation"), dict)
        else {}
    )
    return {
        "candidate_id": candidate.get("candidate_id"),
        "objective_key": list(candidate.get("objective_key") or [])[:4],
        "worktree": candidate.get("worktree"),
        "activation_status": activation.get("status"),
        "activation_passed": activation.get("passed"),
        "exact_execution_eligible": candidate.get("exact_execution_eligible"),
        "exact_execution": candidate.get("exact_execution") or {},
        "ja_accepted": candidate.get("ja_accepted"),
        "semantic_eligible": candidate.get("semantic_eligible"),
        "summary": compact_candidate_evaluator_evidence(candidate.get("summary")),
    }


def compact_candidate_evaluator_evidence(value: Any) -> dict[str, Any]:
    summary = value if isinstance(value, dict) else {}
    best_metrics = summary.get("best_metrics") if isinstance(summary.get("best_metrics"), dict) else {}
    solver_evidence = (
        best_metrics.get("solver_evidence")
        if isinstance(best_metrics.get("solver_evidence"), dict)
        else {}
    )
    return {
        "total": summary.get("total"),
        "valid": summary.get("valid"),
        "failed": summary.get("failed"),
        "best_experiment_id": summary.get("best_experiment_id"),
        "best_metrics": {
            key: best_metrics.get(key)
            for key in ("makespan", "avg_makespan", "runtime_seconds")
            if key in best_metrics
        },
        "solver_diagnostics": compact_json(
            solver_evidence.get("diagnostics") or {},
            max_chars=1_200,
        ).payload,
        "validation_summary": compact_json(
            summary.get("validation_summary") or {},
            max_chars=600,
        ).payload,
    }


def compact_candidate_smoke_evidence(value: Any) -> dict[str, Any]:
    smoke = value if isinstance(value, dict) else {}
    summary = smoke.get("summary") if isinstance(smoke.get("summary"), dict) else {}
    return {
        "enabled": smoke.get("enabled"),
        "passed": smoke.get("passed"),
        "full_evaluation_started": smoke.get("full_evaluation_started"),
        "best_metrics": compact_candidate_evaluator_evidence(summary).get("best_metrics") or {},
        "output_dir": smoke.get("output_dir"),
    }


def compact_candidate_proposal_evidence(value: Any) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    return {
        "status": diagnostics.get("status"),
        "summary": _bounded_text(diagnostics.get("summary"), limit=300),
        "rule_operator_hypotheses": compact_rule_operator_hypotheses(
            diagnostics.get("rule_operator_hypotheses"),
            limit=3,
        ),
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
    """Preserve rollback evidence without promoting an unreviewed causal lesson."""

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
    return {
        "status": "provisional_review_required" if recent_failures else "empty",
        "recent_failures": recent_failures,
        "must_avoid": [],
        "review_required": bool(recent_failures),
        "evidence_class": "run_local_observation",
        "rule": (
            "Do not replay an identical rolled-back patch unchanged. Failure signatures describe observed gates, "
            "not a verified causal lesson or a prohibition on the method family. Human review is required before "
            "moving any failure interpretation into reusable knowledge or a Worker Skill."
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
    mechanism_failed = bool(
        isinstance(item.mechanism_activation, dict)
        and item.mechanism_activation.get("passed") is False
    )
    if mechanism_failed:
        signatures.append("mechanism_not_activated")

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
    if semantic_review_has_verified_blocking_finding(item.semantic_review):
        signatures.append("algorithm_semantic_review_repair_required")
        for finding in (item.semantic_review or {}).get("findings") or []:
            if isinstance(finding, dict) and finding.get("blocking"):
                signatures.append(
                    _failure_token(f"algorithm_semantic_{finding.get('category') or 'method_semantics'}")
                )
    if (
        item.decision == "rolled_back"
        and not _all_negative_infinity(item.candidate_key)
        and not mechanism_failed
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
    summary = getattr(cycle, "smoke_summary", None)
    return {
        "enabled": summary is not None,
        "passed": bool(summary and summary.total > 0 and summary.valid == summary.total),
        "full_evaluation_started": bool(getattr(cycle, "full_evaluation_started", True)),
        "summary": summary_payload(summary) if summary else None,
        "output_dir": (
            str(getattr(cycle, "smoke_output_dir"))
            if getattr(cycle, "smoke_output_dir", None)
            else None
        ),
    }


def worker_proposal_diagnostics(worker_result: WorkerResult) -> dict[str, Any]:
    """Extract compact proposal diagnostics for the next self-evolution round.

    The diagnostics are reflection context only.  Promotion still depends solely
    on the fixed evaluator objective key.
    """

    artifacts = getattr(worker_result, "artifacts", None) or {}
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


def worker_model_from_result(worker_result: WorkerResult) -> str | None:
    """Recover the actual model argument recorded by the Coding Worker runtime."""

    command_path = (getattr(worker_result, "artifacts", None) or {}).get("command")
    if not command_path:
        return None
    command = _load_json_object(Path(command_path))
    if command is None:
        try:
            raw = json.loads(Path(command_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        command_items = raw if isinstance(raw, list) else []
    else:
        command_items = command.get("command") if isinstance(command.get("command"), list) else []
    for index, item in enumerate(command_items[:-1]):
        if str(item) == "--model":
            return str(command_items[index + 1])[:160]
    return None


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


# ---------------------------------------------------------------------------
# 报告与持久化：同一批轮记录派生方向图、经验分层和知识使用记录。
# ---------------------------------------------------------------------------

def write_loop_report(*, output_dir: Path, result: WorkerLoopResult, problem_family: str | None = None) -> None:
    """写出闭环的机器可读 JSON 和面向人的 Markdown，不再重新运行实验。"""

    round_payloads = [round_record_payload(item) for item in result.rounds]
    direction_graph = summarize_direction_graph(round_payloads)
    experience_memory = build_experience_memory(round_payloads, problem_family=problem_family)
    skill_usage_records = experience_memory.get("skill_usage_records") or []
    payload = {
        "status": result.status,
        "stop_reason": result.stop_reason,
        "baseline_key": list(result.baseline_key),
        "final_key": list(result.final_key),
        "final_worktree": str(result.final_worktree),
        "best_legal_incumbent": candidate_incumbent_payload(result.best_legal_incumbent),
        "best_activated_incumbent": candidate_incumbent_payload(result.best_activated_incumbent),
        "lane_development_states": lane_development_states_payload(
            result.lane_development_states or {}
        ),
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
        f"- Best legal incumbent: `{json.dumps(candidate_incumbent_payload(result.best_legal_incumbent), ensure_ascii=False)}`",
        f"- Best activated incumbent: `{json.dumps(candidate_incumbent_payload(result.best_activated_incumbent), ensure_ascii=False)}`",
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


def _run_harness(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
    cancellation: CancellationToken | None = None,
) -> RunSummary:
    runner = GraphHarnessRunner(
        contract=contract,
        project_root=project_root,
        output_dir=output_dir,
        cancellation=cancellation,
    )
    try:
        return runner.run()
    finally:
        runner.close()


def _run_harness_with_records(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
    cancellation: CancellationToken | None = None,
) -> tuple[RunSummary, list[ExperimentRecord]]:
    runner = GraphHarnessRunner(
        contract=contract,
        project_root=project_root,
        output_dir=output_dir,
        cancellation=cancellation,
    )
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


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _bounded_text(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _bounded_list(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]
