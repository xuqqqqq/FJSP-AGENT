"""多轮闭环编排：方向规划、同轮修补、Core 复验、晋升/回滚和经验沉淀。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
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
    max_competing_workers: int = 4,
    round_intervention: Callable[[int, LoopRoundRecord, dict[str, Any]], str | None] | None = None,
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
        )
        write_loop_report(output_dir=output_dir, result=result, problem_family=contract.problem_family)
        return result
    # 阶段 2：每个外层 round 对应一个改进方向；repair attempt 不额外消耗轮数。
    effective_repair_attempts = worker_loop_repair_attempt_budget(worker, in_round_repair_attempts)
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
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        user_intervention: dict[str, Any] | None = None
        # Round 0 starts immediately. Later rounds pause only after Main has
        # reviewed the completed prior round and published a concrete proposal.
        if round_index > 0 and round_records and round_intervention is not None:
            user_direction = round_intervention(round_index, round_records[-1], direction_plan)
            if str(user_direction or "").strip():
                user_intervention = {
                    "direction": str(user_direction).strip()[:4_000],
                    "applies_to_round": round_index,
                    "source": "user_between_rounds",
                }
                planning_feedback = apply_user_intervention_to_feedback(
                    planning_feedback,
                    user_intervention=user_intervention,
                )
                direction_plan = plan_direction_with_fallback(
                    planner=direction_planner,
                    round_index=round_index,
                    context_packet_path=planning_context_packet_path,
                    loop_feedback=planning_feedback,
                    output_dir=cycle_dir / "main_agent_user_revision",
                )
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
                worker_input_root=project_root,
                user_intervention=user_intervention,
                max_competing_workers=max_competing_workers,
                cancellation=cancellation,
            )
            direction_plan = dict(direction_plan)
            direction_plan["competition_result"] = competition_result
            direction_plan["selected_candidate_variant"] = selected_direction_plan.get("candidate_variant") or {}
            direction_plan["mechanism_activation"] = selected_direction_plan.get("mechanism_activation") or {}
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
        semantic_review = (
            in_round_attempts[-1].get("semantic_review")
            if in_round_attempts and isinstance(in_round_attempts[-1], dict)
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
        if mechanism_activation.get("passed") is False:
            promotion_check = {
                "status": "skipped",
                "reason": "mechanism_not_activated",
                "promoted": False,
                "required_repeats": max(1, promotion_repeats),
                "mechanism_activation": mechanism_activation,
            }
        elif semantic_review_blocks_promotion(semantic_review):
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
                cancellation=cancellation,
            )
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
    cancellation: CancellationToken | None = None,
) -> tuple[Any, Path, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Evaluate isolated Coding Worker variants and return the best eligible lane."""

    candidate_plans = competitive_direction_plans(direction_plan, limit=max_competing_workers)
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
        try:
            cycle, context_path, attempts = run_worker_cycle_with_in_round_repairs(
                contract=contract,
                project_root=project_root,
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
                baseline_generation=baseline_generation,
                previous_rounds=previous_rounds,
                repair_attempts=repair_attempts,
                direction_plan=candidate_plan,
                semantic_reviewer=semantic_reviewer,
                assignment_issuer=assignment_issuer,
                worker_input_root=worker_input_root,
                user_intervention=user_intervention,
                cancellation=cancellation,
            )
            key = summary_objective_key(cycle.summary, contract.objectives)
            semantic_review = (
                attempts[-1].get("semantic_review")
                if attempts and isinstance(attempts[-1], dict)
                else None
            )
            ja_accepted = bool(cycle.agentic_judgment.accepted)
            core_eligible = not _all_negative_infinity(key)
            semantic_eligible = not semantic_review_blocks_promotion(semantic_review)
            mechanism_activation = evaluate_mechanism_activation(candidate_plan, cycle.summary)
            activation_eligible = bool(mechanism_activation.get("passed"))
            eligible = ja_accepted and core_eligible and semantic_eligible and activation_eligible
            outcome = {
                "candidate_id": candidate_id,
                "candidate_index": candidate_index,
                "status": "completed",
                "eligible": eligible,
                "ja_accepted": ja_accepted,
                "ja_stage": cycle.agentic_judgment.stage,
                "ja_issues": list(cycle.agentic_judgment.issues),
                "core_eligible": core_eligible,
                "semantic_eligible": semantic_eligible,
                "activation_eligible": activation_eligible,
                "mechanism_activation": mechanism_activation,
                "objective_key": list(key),
                "worker_status": cycle.worker_result.status,
                "worker_model": worker_model_from_result(cycle.worker_result),
                "summary": summary_payload(cycle.summary),
                "smoke_gate": cycle_smoke_gate_payload(cycle),
                "proposal_diagnostics": compact_round_proposal_diagnostics(
                    worker_proposal_diagnostics(cycle.worker_result)
                ),
                "semantic_review": semantic_review or {},
                "cycle_dir": str(candidate_dir),
                "worktree": str(cycle.worktree_path),
                "patch_path": str(cycle.patch_path),
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
    result = {
        "status": "selected" if has_eligible_winner else "no_eligible_candidate",
        "candidate_count": len(candidate_plans),
        "eligible_candidate_count": len(eligible_completed),
        "execution_mode": "parallel" if concurrency > 1 else "serial",
        "max_concurrency": concurrency,
        "selected_candidate_id": selected_variant.get("candidate_id") or "c00",
        "selected_objective_key": list(winner[0]),
        "selected_for_promotion": has_eligible_winner,
        "selected_for_promotion_check": has_eligible_winner,
        "selection_rule": "best Core objective among JA/Core/semantic-eligible isolated candidates",
        "candidates": outcomes,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "competition_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return winner[2], winner[3], winner[4], result, selected_plan


def competitive_direction_plans(direction_plan: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    """Expand bounded variants; only research tournaments may cross method families."""

    limit = max(1, min(4, int(limit)))
    variants = [
        item
        for item in direction_plan.get("candidate_variants") or []
        if isinstance(item, dict)
    ][:limit]
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
            "acceptance_checks",
            "activation_checks",
        ):
            if variant.get(name):
                plan[name] = variant[name]
        parent_stage = str(plan.get("experiment_stage") or "probe").strip()
        experiment_stage = (
            "research_tournament"
            if parent_stage == "research_tournament"
            else str(variant.get("experiment_stage") or parent_stage).strip()
        )
        plan["experiment_stage"] = experiment_stage
        if experiment_stage == "research_tournament":
            for name in ("method_family", "method_families", "method_package_id", "knowledge_query"):
                if variant.get(name):
                    plan[name] = variant[name]
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


def evaluate_mechanism_activation(
    direction_plan: dict[str, Any],
    summary: RunSummary,
) -> dict[str, Any]:
    """Evaluate telemetry assertions proving that the proposed mechanism ran.

    Activation is deliberately separate from solution quality. A failed required
    assertion makes the experiment inconclusive and ineligible for promotion; a
    plan without assertions retains backward-compatible behavior.
    """

    checks = [
        item
        for item in direction_plan.get("activation_checks") or []
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ][:12]
    if not checks:
        return {
            "status": "not_declared",
            "passed": True,
            "declared_check_count": 0,
            "required_check_count": 0,
            "checks": [],
        }

    payload = summary_payload(summary)
    evaluated: list[dict[str, Any]] = []
    required_failures = 0
    for index, check in enumerate(checks):
        path = str(check.get("path") or "").strip()
        operator = str(check.get("operator") or "exists").strip().lower()
        expected = check.get("expected", check.get("value"))
        required = check.get("required") is not False
        found, observed = _resolve_activation_path(payload, path)
        passed = _activation_predicate(
            found=found,
            observed=observed,
            operator=operator,
            expected=expected,
        )
        if required and not passed:
            required_failures += 1
        evaluated.append(
            {
                "id": str(check.get("id") or f"activation_{index + 1}")[:80],
                "path": path,
                "operator": operator,
                "expected": expected,
                "required": required,
                "found": found,
                "observed": observed,
                "passed": passed,
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


def _resolve_activation_path(payload: Any, path: str) -> tuple[bool, Any]:
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
    baseline_generation: dict[str, Any] | None,
    previous_rounds: list[LoopRoundRecord],
    repair_attempts: int,
    direction_plan: dict[str, Any] | None = None,
    semantic_reviewer: AlgorithmSemanticReviewer | None = None,
    assignment_issuer: DirectionPlanningAgent | None = None,
    worker_input_root: Path | None = None,
    user_intervention: dict[str, Any] | None = None,
    cancellation: CancellationToken | None = None,
) -> tuple[Any, Path, list[dict[str, Any]]]:
    """在同一方向内修补候选，修补耗尽后才把该方向记为失败。

    触发修补的不只是语法/合法性错误；合法但不优于 incumbent 的候选也可
    在剩余预算内继续细化。后续 attempt 从上一 attempt 的 worktree 出发，
    因而修的是同一实现方向，而不是重新生成无关 solver。
    """

    max_repair_attempts = max(0, int(repair_attempts))
    attempts: list[dict[str, Any]] = []
    last_cycle: Any | None = None
    last_context_packet_path = output_dir / "context_packet.json"
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
    for attempt_index in range(max_repair_attempts + 1):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        attempt_dir = output_dir if attempt_index == 0 else output_dir / f"repair_{attempt_index:03d}"
        # repair feedback 只带最近失败、精确门禁和 patch 证据，控制上下文增长。
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
            cancellation=cancellation,
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
        attempts.append(attempt_payload)
        parent_assignment_path = assignment_issue.artifact_path
        # 严格提升且各门禁通过时立即结束；不可修复的 provider 故障也不盲重试。
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
    """只对 JA 接受、Core 合法且可能晋升的候选执行昂贵语义审查。

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

    judgment = getattr(cycle, "agentic_judgment", None)
    judgment_checks = getattr(judgment, "checks", {}) if judgment is not None else {}
    soft_accepted = bool(
        isinstance(judgment_checks, dict)
        and judgment_checks.get("soft_accepted_by_diagnostic_smoke")
    )
    if judgment is not None and not bool(getattr(judgment, "accepted", False)) and not soft_accepted:
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
    judgment = (
        baseline_generation.get("agentic_judgment")
        if isinstance(baseline_generation.get("agentic_judgment"), dict)
        else {}
    )
    if not bool(judgment.get("accepted")):
        return "judgment_rejected"
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
        semantic_rank = 900 + 25 * semantic_review_baseline_rank(semantic_review)
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
        degraded_reason = semantic_review_baseline_degraded_reason(semantic_review)
        if degraded_reason == "reviewer_unavailable":
            return "core_evaluator_valid_with_degraded_semantic_review"
        if degraded_reason == "coverage_incomplete_without_verified_blocker":
            return "core_evaluator_valid_with_incomplete_semantic_coverage"
        if semantic_review_blocks_baseline_acceptance(semantic_review):
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
    baseline_context_path = baseline_dir / "context_packet.json"
    attempts: list[dict[str, Any]] = []
    try:
        cycle: Any | None = None
        cycle_attempts: list[tuple[int, Any, Path, dict[str, Any]]] = []
        repair_project_root = source_project
        repair_anchor_attempt_index: int | None = None
        parent_assignment_path: Path | None = None
        planner = assignment_issuer or EvidenceDrivenMainAgent()
        for attempt_index in range(max_repair_attempts + 1):
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
                cancellation=cancellation,
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
            if semantic_passed:
                break
            should_repair = anchor_quality_regressed or should_attempt_in_round_repair(
                cycle,
                semantic_review=semantic_review,
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
    try:
        return planner.plan_direction(
            DirectionPlanRequest(
                round_index=-1,
                context_packet_path=context_packet_path,
                loop_feedback=feedback,
                output_dir=output_dir,
            )
        )
    except TaskCancelled:
        raise
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


def apply_user_intervention_to_feedback(
    feedback: dict[str, Any],
    *,
    user_intervention: dict[str, Any],
) -> dict[str, Any]:
    """Make an explicit between-round user direction the next plan's priority."""

    updated = dict(feedback)
    intervention = dict(user_intervention)
    direction = str(intervention.get("direction") or "").strip()[:4_000]
    updated["user_intervention"] = intervention
    guidance = dict(updated.get("next_round_guidance") or {})
    must_do = [str(item) for item in guidance.get("must_do") or [] if str(item).strip()]
    if direction:
        guidance["must_do"] = [direction, *[item for item in must_do if item != direction]][:8]
    updated["next_round_guidance"] = guidance
    instructions = [str(item) for item in updated.get("instructions") or []]
    updated["instructions"] = [
        "The user explicitly intervened between rounds. Treat user_intervention.direction as the controlling next-round intent; reconcile it with hard evaluator and legality constraints before issuing the Worker assignment.",
        *instructions,
    ]
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
        return EvidenceDrivenMainAgent().plan_direction(request)


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
        and bool(agentic_judgment.get("accepted"))
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
        "title": _bounded_text(plan.get("title"), limit=200),
        "strategy_type": plan.get("strategy_type"),
        "experiment_stage": plan.get("experiment_stage"),
        "method_family": plan.get("method_family"),
        "method_package_id": plan.get("method_package_id"),
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
            "selected_for_promotion": competition.get("selected_for_promotion"),
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
                    "mechanism_activation": candidate.get("mechanism_activation") or {},
                    "worker_model": candidate.get("worker_model"),
                    "worker_status": candidate.get("worker_status"),
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
