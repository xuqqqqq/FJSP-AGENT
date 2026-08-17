"""Context Packet：把稳定任务事实与逐轮动态反馈组织成有界上下文。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_agent.agents.incumbent_audit import build_incumbent_capability_audit

from harness_agent.context.compaction import (
    ROUND_CONTEXT_MAX_CHARS,
    ROUND_FEEDBACK_MAX_CHARS,
    compact_json,
    compact_source_records,
)
from harness_agent.context.loader import load_context_packet
from harness_agent.domains.context import get_domain_context_provider
from harness_agent.context.knowledge import (
    knowledge_query_catalog,
    method_family_catalog,
    method_package_catalog,
    resolve_worker_implementation_skills,
    resolve_method_package,
    select_knowledge_cards,
    select_tagged_knowledge_cards,
    selection_cards,
)
from harness_agent.core.models import TaskContract
from harness_agent.domains.families import get_problem_family
from harness_agent.slots.contract import ResolvedCodeSlot
from harness_agent.slots.manifest import load_slot_manifest


SECTION_ROLE_PRIORITY = {
    "objectives": 0,
    "constraints": 1,
    "input_output": 2,
    "acceptance": 3,
    "algorithm_guidance": 4,
    "instance_data": 5,
    "general": 9,
}


@dataclass(frozen=True)
class ContextPacketRequest:
    """构建 Context Packet 所需的所有外部输入。

    这里汇总的是稳定任务材料：确认后的 Task Contract、补充文档、知识卡、项目
    intake、slot manifest、上一轮报告/经验等。真正的求解结果与回滚信息则在
    `write_refreshed_context_packet()` 里以动态区方式追加。
    """

    contract_path: Path
    output_path: Path
    docs: list[Path] = field(default_factory=list)
    knowledge_cards: list[Path] = field(default_factory=list)
    project_root: Path | None = None
    hypothesis: str = ""
    previous_report: Path | None = None
    previous_pipeline_memory: Path | None = None
    project_intake_manifest: Path | None = None
    slot_manifest: Path | None = None
    max_chars_per_source: int = 12000


def build_context_packet(request: ContextPacketRequest) -> dict[str, Any]:
    """构建首轮完整 Context Packet。

    这个函数是多路上下文的汇合点：Task Contract 文档抽取证据、问题族能力、
    实例特征诊断、知识卡选择、Method Package 候选、项目地图和 slot 编辑边界
    都会在这里合并成同一份首轮上下文。
    """

    contract = TaskContract.load(request.contract_path)
    contract_raw = json.loads(request.contract_path.read_text(encoding="utf-8-sig"))
    domain_context_provider = get_domain_context_provider(contract.problem_family)
    docs = [_source_payload(path, request.max_chars_per_source) for path in request.docs]
    previous_report = (
        _source_payload(request.previous_report, request.max_chars_per_source)
        if request.previous_report
        else None
    )
    previous_pipeline_memory = (
        _pipeline_memory_payload(request.previous_pipeline_memory)
        if request.previous_pipeline_memory
        else None
    )
    instance_diagnostics = domain_context_provider.inspect_instances(
        contract,
        project_root=request.project_root,
    )
    project_intake = (
        _project_intake_payload(request.project_intake_manifest, request.max_chars_per_source)
        if request.project_intake_manifest
        else None
    )
    slot_manifest = (
        _slot_manifest_payload(request.slot_manifest, project_root=request.project_root)
        if request.slot_manifest
        else None
    )
    contract_review_evidence = _contract_review_payload(contract.review)
    problem_family_capability = get_problem_family(contract.problem_family).to_payload()
    agent_generated_solver = _uses_agent_generated_solver(contract)
    problem_family_tags = (
        [] if agent_generated_solver else list(problem_family_capability.get("knowledge_tags") or [])
    )
    if agent_generated_solver:
        problem_family_tags.append("agent_generated_solver")
    active_features = domain_context_provider.active_features(
        contract=contract,
        instance_diagnostics=instance_diagnostics,
        contract_review_evidence=contract_review_evidence,
    )
    # 知识卡选择和 Method Package 推荐都建立在“领域能力 + 当前实例特征”之上。
    # Domain Pack 提供边界与素材，实例诊断/slot 确认决定当前 round 实际可用什么。
    knowledge_selection = select_knowledge_cards(
        problem_family=contract.problem_family,
        problem_family_tags=problem_family_tags,
        slot_manifest=slot_manifest,
        instance_diagnostics=instance_diagnostics,
        active_features=active_features,
    )
    auto_cards = knowledge_selection.cards
    strategy_card_paths = selection_cards(
        problem_family=contract.problem_family,
        stage="strategy",
    )
    package_catalog = (
        method_package_catalog(
            problem_family=contract.problem_family,
            active_features=active_features,
            knowledge_query_tags=["__direction_selection_pending__"],
        )
        if agent_generated_solver
        else {
            "status": "not_applicable",
            "problem_family": contract.problem_family,
            "active_features": active_features,
            "packages": [],
            "recommended_package_id": None,
        }
    )
    query_catalog = knowledge_query_catalog(problem_family=contract.problem_family)
    family_catalog = method_family_catalog(
        problem_family=contract.problem_family,
        active_features=active_features,
    )
    knowledge_card_paths = _unique_paths([*request.knowledge_cards, *auto_cards])
    knowledge_cards = [_source_payload(path, request.max_chars_per_source) for path in knowledge_card_paths]
    strategy_selection_cards = [
        _source_payload(path, min(request.max_chars_per_source, 12_000))
        for path in strategy_card_paths
    ]
    # `required_order` 是 worker 的最小阅读顺序控制，用于把“先看契约/实例/slot，
    # 再改代码”这种流程固化在上下文里，而不是依赖模型自行猜顺序。
    required_order = [
        "Read this context packet.",
        "State a natural-language strategy before editing code.",
        "Modify only allowed files.",
        "Run the quick test before benchmark self-evaluation.",
        "Return structured changed files, test results, benchmark summary, and failure analysis.",
    ]
    if contract_review_evidence.get("has_document_schema"):
        required_order.insert(1, "Review contract_review_evidence before interpreting document snippets.")
    if contract_review_evidence.get("role_prioritized_sections"):
        required_order.insert(2, "Start document grounding from contract_review_evidence.role_prioritized_sections.")
    if project_intake:
        required_order.insert(1, "Review project_intake before proposing code changes.")
    if slot_manifest:
        required_order.insert(1, "Review slot_manifest and edit only user-confirmed selected slots.")
    if instance_diagnostics.get("status") in {"available", "partial"}:
        required_order.insert(
            1,
            "Review instance_diagnostics before choosing a slot strategy; best-known/LB/UB values are diagnostics only.",
        )
    if previous_pipeline_memory:
        required_order.insert(1, "Review previous_pipeline_memory before proposing the next loop change.")
        if previous_pipeline_memory.get("operator_guidance"):
            required_order.insert(
                2,
                "Apply previous_pipeline_memory.operator_guidance when choosing rule/operator hypotheses.",
            )
        if previous_pipeline_memory.get("direction_graph_signal"):
            required_order.insert(
                2,
                "Use previous_pipeline_memory.direction_graph_signal to preserve, mutate, or prune prior improvement directions.",
            )
        if previous_pipeline_memory.get("experience_memory_signal"):
            required_order.insert(
                2,
                "Use validated lessons from previous_pipeline_memory.experience_memory_signal as reusable method evidence; keep candidate lessons provisional.",
            )
    packet = {
        "packet_type": "algoforge_context_packet",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract_path": str(request.contract_path),
        "contract_hash": _hash_text(json.dumps(contract_raw, ensure_ascii=False, sort_keys=True)),
        "task": {
            "task_id": contract.task_id,
            "problem_family": contract.problem_family,
            "description": contract.description,
            "review_status": contract.review_status,
            "requires_human_confirmation": contract.requires_human_confirmation,
            "objectives": [
                {
                    "name": objective.name,
                    "direction": objective.direction,
                    "priority": objective.priority,
                    "invalid_if_missing": objective.invalid_if_missing,
                    "threshold": objective.threshold,
                }
                for objective in contract.objectives
            ],
            "instances": [{"id": instance.id, "path": str(instance.path)} for instance in contract.instances],
            "budget": {
                "rounds": contract.budget.rounds,
                "seeds": contract.budget.seeds,
                "timeout_seconds": contract.budget.timeout_seconds,
                "max_workers": contract.budget.max_workers,
            },
        },
        "problem_family_capability": problem_family_capability,
        "evaluator_protocol": {
            "solver_command_template": contract.commands.solver,
            "evaluator_command_template": contract.commands.evaluator,
            "quick_test_command": contract.commands.quick_test,
            "baseline_source": contract.review.get("baseline_source"),
            "worker_target_file": contract.review.get("worker_target_file"),
            "provided_project_read_paths": contract.review.get("provided_project_read_paths") or [],
            "solution_contract": domain_context_provider.solution_contract(),
            "solution_format": domain_context_provider.solution_contract().get("format"),
            "resources": {key: str(value) for key, value in contract.resources.items()},
            "formal_verdict_owner": "AlgoForge Core",
            "worker_self_evaluation_policy": (
                "Worker may compile changed code and run one fixed-seed short smoke only; formal evaluator, "
                "multi-seed, repeated, and benchmark runs belong exclusively to Core."
            ),
            "worker_execution_budget": {
                "compile_runs": 1,
                "smoke_runs": 1,
                "smoke_seed": contract.budget.seeds[0] if contract.budget.seeds else 0,
                "smoke_timeout_seconds": min(10, max(5, contract.budget.timeout_seconds)),
                "forbidden": [
                    "multi-seed evaluation",
                    "formal benchmark command",
                    "full test suite",
                    "repeated evaluator runs",
                ],
            },
        },
        "edit_policy": {
            "allowed_paths": contract.paths.allowed_paths,
            "forbidden_paths": contract.paths.forbidden_paths,
            "must_not_modify": [".git", "outputs", "confirmed evaluator semantics unless explicitly requested"],
        },
        "worker_instruction": {
            "role": "Coding Agent / CodingWorker",
            "required_order": required_order,
            "success_rule": "Do not claim success unless AlgoForge Core reruns evaluator/validator and accepts the result.",
        },
        "hypothesis": request.hypothesis,
        "contract_review_evidence": contract_review_evidence,
        "project_intake": project_intake,
        "slot_manifest": slot_manifest,
        "instance_diagnostics": instance_diagnostics,
        "documents": docs,
        "knowledge_cards": knowledge_cards,
        "strategy_selection_cards": strategy_selection_cards,
        "auto_knowledge_cards": [str(path) for path in auto_cards],
        "knowledge_selection": knowledge_selection.audit,
        "knowledge_query_catalog": query_catalog,
        "method_family_catalog": family_catalog,
        "method_package_catalog": package_catalog,
        "previous_report": previous_report,
        "previous_pipeline_memory": previous_pipeline_memory,
    }
    packet["packet_hash"] = _hash_text(json.dumps(packet, ensure_ascii=False, sort_keys=True))
    return packet


def write_context_packet(request: ContextPacketRequest) -> Path:
    """生成首轮完整上下文；需求、IO、domain pack 和知识选择在这里汇合。"""

    payload = build_context_packet(request)
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    request.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request.output_path


def write_refreshed_context_packet(
    *,
    base_context_packet_path: Path,
    output_path: Path,
    loop_feedback: dict[str, Any],
    project_root: Path | None = None,
) -> Path:
    """追加 Core 反馈并生成逐轮上下文。

    首轮包保留稳定任务信息；动态 promotion/rollback、修补和失败证据追加在
    后部并按结构压缩，从而控制长度并尽量复用模型缓存前缀。
    """

    loaded_packet = load_context_packet(base_context_packet_path)
    packet = loaded_packet.effective_context
    parent_hash = loaded_packet.raw.get("packet_hash") or loaded_packet.integrity["actual_packet_hash"]

    refreshed = dict(packet)
    refreshed.pop("packet_hash", None)
    refreshed["created_at"] = datetime.now(timezone.utc).isoformat()
    refreshed["parent_packet_hash"] = parent_hash
    refreshed["refresh_reason"] = "worker_loop_round_feedback"
    refreshed["base_context_ref"] = {
        "path": str(base_context_packet_path.resolve()),
        "packet_hash": parent_hash,
        "immutable": True,
    }
    refreshed["documents"] = compact_source_records(
        refreshed.get("documents"),
        max_items=8,
        max_snippet_chars=6000,
    )
    refreshed["knowledge_cards"] = compact_source_records(
        refreshed.get("knowledge_cards"),
        max_items=40,
        max_snippet_chars=2400,
    )
    refreshed["iteration_edit_contract"] = {
        "mode": "incremental_after_baseline",
        "preserve_incumbent_rule": (
            "Start from the incumbent worktree and preserve the best promoted solver structure unless the proposal "
            "names a measured weakness and makes a smaller, evaluator-checkable mutation."
        ),
        "whole_file_rewrite_policy": (
            "Do not use create_or_replace on an existing solver file during improvement rounds. Use text_replace, "
            "insert_after, or a confirmed replace_slot_block for small changes; create_or_replace is reserved for "
            "new helper files or baseline-generation entrypoints."
        ),
        "required_pre_full_eval_gate": (
            "Core runs a one-seed evaluator smoke before the full benchmark; proposals should be small enough for "
            "that smoke to diagnose quickly."
        ),
    }
    incumbent_code_context = _incumbent_code_context(refreshed, project_root=project_root)
    if incumbent_code_context:
        refreshed["incumbent_code_context"] = incumbent_code_context
        incumbent_capability_audit = build_incumbent_capability_audit(
            incumbent_code_context,
            project_root=project_root,
        )
        if incumbent_capability_audit:
            refreshed["incumbent_capability_audit"] = incumbent_capability_audit
    projected_feedback, feedback_compaction = _project_loop_feedback(loop_feedback)
    refreshed["loop_feedback"] = projected_feedback
    activate_method_package_context(
        refreshed,
        direction_plan=(
            loop_feedback.get("current_direction_plan")
            if isinstance(loop_feedback.get("current_direction_plan"), dict)
            else None
        ),
    )
    activate_direction_knowledge_context(
        refreshed,
        direction_plan=(
            loop_feedback.get("current_direction_plan")
            if isinstance(loop_feedback.get("current_direction_plan"), dict)
            else None
        ),
    )
    refreshed["hypothesis"] = _improvement_round_hypothesis(str(refreshed.get("hypothesis") or ""))
    if project_root is not None:
        refreshed["slot_manifest"] = _refresh_slot_manifest_sources(
            refreshed.get("slot_manifest"),
            project_root=project_root,
        )

    worker_instruction = dict(refreshed.get("worker_instruction") or {})
    required_order = list(worker_instruction.get("required_order") or [])
    feedback_step = "Review loop_feedback and avoid repeating rolled-back changes unless the new proposal is materially different."
    if feedback_step not in required_order:
        required_order.insert(1, feedback_step)
    incumbent_step = "Preserve the current promoted incumbent; make a small incremental edit rather than rewriting the solver."
    if incumbent_step not in required_order:
        required_order.insert(2, incumbent_step)
    worker_instruction["required_order"] = required_order
    worker_instruction["round_feedback_rule"] = (
        "Treat loop_feedback as Core evaluator evidence.  Promoted rounds show "
        "directions worth preserving; rolled-back rounds show directions to avoid "
        "or modify.  Do not use worker self-claims as success evidence."
    )
    worker_instruction["incremental_edit_rule"] = (
        "After an incumbent exists, keep the promoted solver skeleton and mutate one bounded rule/operator at a time. "
        "A full-file rewrite of an existing solver is not an acceptable improvement-round edit."
    )
    refreshed["worker_instruction"] = worker_instruction
    refreshed["context_compaction"] = {
        "mode": "bounded_round_context",
        "max_context_chars": ROUND_CONTEXT_MAX_CHARS,
        "max_feedback_chars": ROUND_FEEDBACK_MAX_CHARS,
        "feedback_original_chars": feedback_compaction["original_chars"],
        "feedback_stored_chars": feedback_compaction["stored_chars"],
        "feedback_compacted": feedback_compaction["compacted"],
        "feedback_profile": feedback_compaction["profile"],
        "feedback_schema_version": feedback_compaction["schema_version"],
        "history_policy": "schema_projection_keep_recent_evidence_and_artifact_references",
    }

    refreshed, packet_compaction = _fit_refreshed_packet(
        refreshed,
        max_chars=ROUND_CONTEXT_MAX_CHARS - 256,
    )
    compaction = refreshed.get("context_compaction")
    if isinstance(compaction, dict):
        compaction["packet_original_chars_before_final_bound"] = packet_compaction["original_chars"]
        compaction["packet_profile"] = packet_compaction["profile"]
        compaction["packet_compacted"] = packet_compaction["compacted"]
    refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_text = json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n"
    if len(output_text) > ROUND_CONTEXT_MAX_CHARS:
        refreshed.pop("packet_hash", None)
        refreshed, _ = _fit_refreshed_packet(
            refreshed,
            max_chars=ROUND_CONTEXT_MAX_CHARS - 1024,
        )
        refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))
        output_text = json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n"
    output_path.write_text(output_text, encoding="utf-8")
    return output_path


def _project_loop_feedback(loop_feedback: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    original_text = json.dumps(loop_feedback, ensure_ascii=False, indent=2)
    previous_rounds = [item for item in loop_feedback.get("previous_rounds") or [] if isinstance(item, dict)]
    current_direction_plan = (
        loop_feedback.get("current_direction_plan")
        if isinstance(loop_feedback.get("current_direction_plan"), dict)
        else {}
    )
    projection = {
        "schema_version": 1,
        "projection_kind": "bounded_loop_feedback",
        "purpose": _short_text(loop_feedback.get("purpose"), 800),
        "round_semantics": _compact_json_value(loop_feedback.get("round_semantics"), max_chars=1_200),
        "competition": _project_competition(loop_feedback.get("competition")),
        "round_index": loop_feedback.get("round_index"),
        "current_direction": _compact_json_value(loop_feedback.get("current_direction"), max_chars=1_200),
        "current_direction_plan": _project_direction_plan(current_direction_plan),
        "objective_key_order": _project_objective_key_order(loop_feedback.get("objective_key_order")),
        "baseline_key": loop_feedback.get("baseline_key") or [],
        "incumbent_key_before": loop_feedback.get("incumbent_key_before") or [],
        "incumbent_worktree": _short_text(loop_feedback.get("incumbent_worktree"), 400),
        "baseline_summary": _project_run_summary(loop_feedback.get("baseline_summary")),
        "incumbent_summary": _project_run_summary(loop_feedback.get("incumbent_summary")),
        "agent_generated_baseline_memory": _project_agent_generated_baseline_memory(
            loop_feedback.get("agent_generated_baseline_memory")
        ),
        "previous_rounds": [_project_previous_round(item) for item in previous_rounds[-6:]],
        "round_history_summary": _project_round_history_summary(previous_rounds),
        "direction_graph": _project_direction_graph(loop_feedback.get("direction_graph")),
        "experience_memory": _project_experience_memory(loop_feedback.get("experience_memory")),
        "skill_usage_summary": _compact_json_value(loop_feedback.get("skill_usage_summary"), max_chars=1_000),
        "protected_promoted_facts": _project_protected_facts(
            loop_feedback.get("protected_promoted_facts")
        ),
        "failure_memory": _project_failure_memory(loop_feedback.get("failure_memory")),
        "next_round_guidance": _project_next_round_guidance(loop_feedback.get("next_round_guidance")),
        "user_intervention": _compact_json_value(loop_feedback.get("user_intervention"), max_chars=1_800),
        "direction_patch_contract": _compact_json_value(
            loop_feedback.get("direction_patch_contract"),
            max_chars=2_400,
        ),
        "instructions": _bounded_strings(loop_feedback.get("instructions"), limit=12, chars=400),
        "current_round_repair": _project_current_round_repair(loop_feedback.get("current_round_repair")),
        "artifact_refs": _project_feedback_artifact_refs(loop_feedback, previous_rounds),
        "hypothesis_graph_path": _short_text(loop_feedback.get("hypothesis_graph_path"), 400),
        "experience_memory_path": _short_text(loop_feedback.get("experience_memory_path"), 400),
        "loop_result_path": _short_text(loop_feedback.get("loop_result_path"), 400),
    }
    stored_text = json.dumps(projection, ensure_ascii=False, indent=2)
    return projection, {
        "schema_version": 1,
        "original_chars": len(original_text),
        "stored_chars": len(stored_text),
        "compacted": projection != loop_feedback,
        "profile": "schema_projection",
    }


def _fit_refreshed_packet(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], dict[str, Any]]:
    original_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(original_text) <= max_chars:
        return payload, {
            "original_chars": len(original_text),
            "compacted": False,
            "profile": "none",
        }
    loop_feedback = payload.get("loop_feedback")
    other_payload = dict(payload)
    other_payload.pop("loop_feedback", None)
    target_other_chars = max(
        4_000,
        max_chars - len(json.dumps({"loop_feedback": loop_feedback}, ensure_ascii=False, indent=2)) - 1_024,
    )
    fitted = compact_json(other_payload, max_chars=target_other_chars)
    merged = dict(fitted.payload)
    merged["loop_feedback"] = loop_feedback
    merged_text = json.dumps(merged, ensure_ascii=False, indent=2)
    attempts = 0
    while len(merged_text) > max_chars and attempts < 8:
        overflow = len(merged_text) - max_chars
        target_other_chars = max(1_200, target_other_chars - overflow - 512)
        fitted = compact_json(other_payload, max_chars=target_other_chars)
        merged = dict(fitted.payload)
        merged["loop_feedback"] = loop_feedback
        merged_text = json.dumps(merged, ensure_ascii=False, indent=2)
        attempts += 1
    return merged, {
        "original_chars": len(original_text),
        "compacted": fitted.compacted,
        "profile": f"{fitted.profile}_preserve_loop_feedback",
    }


def _project_competition(value: Any) -> dict[str, Any]:
    competition = value if isinstance(value, dict) else {}
    return {
        "max_competing_workers": competition.get("max_competing_workers"),
        "isolation_rule": _short_text(competition.get("isolation_rule"), 400),
        "selection_rule": _short_text(competition.get("selection_rule"), 400),
    }


def _project_direction_plan(value: Any) -> dict[str, Any]:
    plan = value if isinstance(value, dict) else {}
    assessment = plan.get("incumbent_assessment") if isinstance(plan.get("incumbent_assessment"), dict) else {}
    mutation = plan.get("next_mutation") if isinstance(plan.get("next_mutation"), dict) else {}
    competition = plan.get("competition_result") if isinstance(plan.get("competition_result"), dict) else {}
    return {
        "direction_id": plan.get("direction_id"),
        "parent_direction_id": plan.get("parent_direction_id"),
        "title": _short_text(plan.get("title"), 200),
        "strategy_type": plan.get("strategy_type"),
        "experiment_stage": plan.get("experiment_stage"),
        "method_family": plan.get("method_family"),
        "method_families": _project_method_families(plan.get("method_families")),
        "method_package_id": plan.get("method_package_id"),
        "knowledge_query": _bounded_strings(plan.get("knowledge_query"), limit=8, chars=160),
        "hypothesis": _short_text(plan.get("hypothesis"), 800),
        "worker_objective": _short_text(plan.get("worker_objective"), 500),
        "diagnosis": _short_text(plan.get("diagnosis"), 500),
        "observed_shortcomings": _bounded_strings(plan.get("observed_shortcomings"), limit=6, chars=300),
        "incumbent_assessment": {
            key: _bounded_strings(assessment.get(key), limit=6, chars=240)
            for key in (
                "verified_capabilities",
                "implementation_limits",
                "bottleneck_hypotheses",
                "evidence_refs",
                "unknowns",
            )
        },
        "next_mutation": {
            "target_symbols": _bounded_strings(mutation.get("target_symbols"), limit=8, chars=180),
            "change": _short_text(mutation.get("change"), 500),
            "expected_effect": _short_text(mutation.get("expected_effect"), 400),
            "falsification_metrics": _bounded_strings(mutation.get("falsification_metrics"), limit=6, chars=160),
        },
        "change_scope": _bounded_strings(plan.get("change_scope"), limit=6, chars=300),
        "activation_checks": _project_activation_checks(plan.get("activation_checks")),
        "candidate_variants": [
            {
                "candidate_id": item.get("candidate_id"),
                "title": _short_text(item.get("title"), 180),
                "hypothesis": _short_text(item.get("hypothesis"), 400),
                "method_family": item.get("method_family"),
                "method_families": _project_method_families(item.get("method_families")),
                "knowledge_query": _bounded_strings(item.get("knowledge_query"), limit=8, chars=160),
                "change_scope": _bounded_strings(item.get("change_scope"), limit=4, chars=240),
                "activation_checks": _project_activation_checks(item.get("activation_checks")),
            }
            for item in plan.get("candidate_variants") or []
            if isinstance(item, dict)
        ][:4],
        "direction_selection": _compact_json_value(plan.get("direction_selection"), max_chars=1_200),
        "mechanism_activation": _compact_json_value(plan.get("mechanism_activation"), max_chars=1_000),
        "preserve": _bounded_strings(plan.get("preserve"), limit=6, chars=260),
        "avoid": _bounded_strings(plan.get("avoid"), limit=6, chars=260),
        "implementation_order": _bounded_strings(plan.get("implementation_order"), limit=10, chars=160),
        "acceptance_checks": _bounded_strings(plan.get("acceptance_checks"), limit=8, chars=220),
        "completion_rule": _short_text(plan.get("completion_rule"), 500),
        "selection_rationale": _short_text(plan.get("selection_rationale"), 500),
        "competition_result": _project_direction_competition(competition),
    }


def _project_method_families(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "reason": _short_text(item.get("reason"), 220),
            }
        )
        if len(result) >= 4:
            break
    return result


def _project_direction_competition(value: Any) -> dict[str, Any]:
    competition = value if isinstance(value, dict) else {}
    candidates = []
    for item in competition.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "candidate_id": item.get("candidate_id"),
                "status": item.get("status"),
                "eligible": item.get("eligible"),
                "objective_key": item.get("objective_key") or [],
                "worker_model": item.get("worker_model"),
                "worker_status": item.get("worker_status"),
                "mechanism_activation": _compact_json_value(item.get("mechanism_activation"), max_chars=800),
                "summary": _project_run_summary(item.get("summary")),
                "patch_path": _short_text(item.get("patch_path"), 400),
            }
        )
        if len(candidates) >= 4:
            break
    return {
        "status": competition.get("status"),
        "candidate_count": competition.get("candidate_count"),
        "eligible_candidate_count": competition.get("eligible_candidate_count"),
        "selected_candidate_id": competition.get("selected_candidate_id"),
        "selected_objective_key": competition.get("selected_objective_key") or [],
        "selected_for_promotion": competition.get("selected_for_promotion"),
        "candidates": candidates,
    }


def _project_objective_key_order(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "name": item.get("name"),
                "direction": item.get("direction"),
                "priority": item.get("priority"),
                "threshold": item.get("threshold"),
            }
        )
        if len(result) >= 8:
            break
    return result


def _project_run_summary(value: Any) -> dict[str, Any]:
    summary = value if isinstance(value, dict) else {}
    best_metrics = summary.get("best_metrics") if isinstance(summary.get("best_metrics"), dict) else {}
    return {
        key: summary.get(key)
        for key in (
            "total",
            "valid",
            "failed",
            "best_experiment_id",
            "best_candidate_id",
        )
        if key in summary
    } | {
        "best_metrics": _compact_json_value(best_metrics, max_chars=1_600),
        "validation_summary": _compact_json_value(summary.get("validation_summary"), max_chars=1_200),
    }


def _project_agent_generated_baseline_memory(value: Any) -> dict[str, Any]:
    memory = value if isinstance(value, dict) else {}
    return {
        "status": memory.get("status"),
        "accepted_as_incumbent": memory.get("accepted_as_incumbent"),
        "baseline_key": memory.get("baseline_key") or [],
        "worker_status": memory.get("worker_status"),
        "worker_changed_files": _bounded_strings(memory.get("worker_changed_files"), limit=8, chars=180),
        "repair_attempt_count": memory.get("repair_attempt_count"),
        "repair_recovered": memory.get("repair_recovered"),
        "agentic_accepted": memory.get("agentic_accepted"),
        "agentic_issues": _bounded_strings(memory.get("agentic_issues"), limit=8, chars=240),
        "proposal_summary": _short_text(memory.get("proposal_summary"), 500),
        "strategy_intent": _short_text(memory.get("strategy_intent"), 800),
        "rule_operator_hypotheses": _project_rule_operator_hypotheses(memory.get("rule_operator_hypotheses")),
        "semantic_review": _compact_json_value(memory.get("semantic_review"), max_chars=1_000),
        "semantic_review_degraded": memory.get("semantic_review_degraded"),
        "semantic_review_degraded_reason": _short_text(
            memory.get("semantic_review_degraded_reason"),
            400,
        ),
        "evidence_level": memory.get("evidence_level"),
        "best_core_valid_anchor": _compact_json_value(memory.get("best_core_valid_anchor"), max_chars=1_200),
        "round_payload": _project_previous_round(memory.get("round_payload") or {}),
        "protection_rule": _short_text(memory.get("protection_rule"), 800),
    }


def _project_previous_round(value: dict[str, Any]) -> dict[str, Any]:
    direction = value.get("direction_plan") if isinstance(value.get("direction_plan"), dict) else {}
    diagnostics = value.get("proposal_diagnostics") if isinstance(value.get("proposal_diagnostics"), dict) else {}
    semantic = value.get("semantic_review") if isinstance(value.get("semantic_review"), dict) else {}
    smoke = value.get("smoke_gate") if isinstance(value.get("smoke_gate"), dict) else {}
    promotion = value.get("promotion_check") if isinstance(value.get("promotion_check"), dict) else {}
    return {
        "round_index": value.get("round_index"),
        "decision": value.get("decision"),
        "candidate_key": value.get("candidate_key") or [],
        "incumbent_key_after": value.get("incumbent_key_after") or [],
        "direction_id": direction.get("direction_id"),
        "parent_direction_id": direction.get("parent_direction_id"),
        "title": _short_text(direction.get("title"), 200),
        "strategy_type": direction.get("strategy_type"),
        "experiment_stage": direction.get("experiment_stage"),
        "method_family": direction.get("method_family"),
        "method_families": _project_method_families(direction.get("method_families")),
        "method_package_id": direction.get("method_package_id"),
        "knowledge_query": _bounded_strings(direction.get("knowledge_query"), limit=8, chars=160),
        "hypothesis": _short_text(direction.get("hypothesis"), 500),
        "implementation_order": _bounded_strings(direction.get("implementation_order"), limit=8, chars=160),
        "activation_checks": _project_activation_checks(direction.get("activation_checks")),
        "mechanism_activation": _compact_json_value(
            value.get("mechanism_activation") or direction.get("mechanism_activation"),
            max_chars=1_000,
        ),
        "competition_result": _project_direction_competition(direction.get("competition_result")),
        "failure_signatures": _bounded_strings(value.get("failure_signatures"), limit=8, chars=220),
        "worker_status": value.get("worker_status"),
        "candidate_summary": _project_run_summary(value.get("candidate_summary")),
        "proposal_diagnostics": _project_proposal_diagnostics(diagnostics),
        "semantic_review": _compact_json_value(semantic, max_chars=1_600),
        "smoke_gate": _compact_json_value(smoke, max_chars=900),
        "promotion_check": {
            "promoted": promotion.get("promoted"),
            "eligible": promotion.get("eligible"),
            "reason": _short_text(promotion.get("reason"), 500),
            "selected_candidate_id": promotion.get("selected_candidate_id"),
        },
        "round_reflection": _project_round_reflection(value.get("round_reflection")),
        "artifact_refs": _project_round_artifact_refs(value),
    }


def _project_round_reflection(value: Any) -> dict[str, Any]:
    reflection = value if isinstance(value, dict) else {}
    next_action = reflection.get("next_action") if isinstance(reflection.get("next_action"), dict) else {}
    return {
        "hypothesis_outcome": reflection.get("hypothesis_outcome"),
        "summary": _short_text(reflection.get("summary"), 600),
        "next_action": {
            "action": next_action.get("action"),
            "rationale": _short_text(next_action.get("rationale"), 500),
            "required_activation_checks": _project_activation_checks(
                next_action.get("required_activation_checks")
            ),
        },
    }


def _project_proposal_diagnostics(value: Any) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    hypotheses = []
    for item in diagnostics.get("rule_operator_hypotheses") or []:
        if not isinstance(item, dict):
            continue
        hypotheses.append(
            {
                "name": _short_text(item.get("name"), 160),
                "type": item.get("type"),
                "target_files": _bounded_strings(item.get("target_files"), limit=8, chars=180),
                "novelty": _short_text(item.get("novelty"), 300),
                "expected_effect": _short_text(item.get("expected_effect"), 300),
            }
        )
        if len(hypotheses) >= 4:
            break
    rejected_edits = []
    for item in diagnostics.get("rejected_edits") or []:
        if not isinstance(item, dict):
            continue
        rejected_edits.append(
            {
                key: _short_text(item.get(key), 300)
                for key in ("path", "old", "new", "reason")
                if item.get(key) not in (None, "")
            }
        )
        if len(rejected_edits) >= 6:
            break
    return {
        "status": diagnostics.get("status"),
        "summary": _short_text(diagnostics.get("summary"), 700),
        "strategy_intent": _short_text(diagnostics.get("strategy_intent"), 700),
        "rule_operator_hypotheses": hypotheses,
        "proposal_audit": _compact_json_value(diagnostics.get("proposal_audit"), max_chars=4_000),
        "context_usage": _compact_json_value(diagnostics.get("context_usage"), max_chars=1_200),
        "rejected_edits": rejected_edits,
    }


def _project_round_history_summary(previous_rounds: list[dict[str, Any]]) -> dict[str, Any]:
    decision_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    for item in previous_rounds:
        decision = str(item.get("decision") or "unknown")
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        direction = item.get("direction_plan") if isinstance(item.get("direction_plan"), dict) else {}
        strategy = str(direction.get("strategy_type") or "unknown")
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    recent_source = previous_rounds[-6:]
    return {
        "source_round_count": len(previous_rounds),
        "included_recent_round_count": len(recent_source),
        "omitted_round_count": max(0, len(previous_rounds) - len(recent_source)),
        "decision_counts": decision_counts,
        "strategy_type_counts": strategy_counts,
    }


def _project_direction_graph(value: Any) -> dict[str, Any]:
    graph = value if isinstance(value, dict) else {}
    directions = graph.get("recent_directions") if isinstance(graph.get("recent_directions"), list) else graph.get("directions")
    compact_directions = []
    for item in directions if isinstance(directions, list) else []:
        if not isinstance(item, dict):
            continue
        compact_directions.append(
            {
                "direction_id": item.get("direction_id"),
                "parent_id": item.get("parent_id"),
                "round_index": item.get("round_index"),
                "title": _short_text(item.get("title"), 160),
                "status": item.get("status"),
                "decision": item.get("decision"),
                "strategy_type": item.get("strategy_type"),
                "target_files": _bounded_strings(item.get("target_files"), limit=8, chars=180),
                "score_relation": item.get("score_relation"),
                "attempt_count": item.get("attempt_count"),
            }
        )
    return {
        "schema_version": graph.get("schema_version"),
        "round_semantics": graph.get("round_semantics"),
        "direction_count": graph.get("direction_count"),
        "attempt_count": graph.get("attempt_count"),
        "status_counts": graph.get("status_counts") or {},
        "decision_counts": graph.get("decision_counts") or {},
        "promoted_direction_ids": _bounded_strings(graph.get("promoted_direction_ids"), limit=8, chars=120),
        "active_parent_id": graph.get("active_parent_id"),
        "directions": compact_directions[-6:],
        "guidance": _bounded_strings(graph.get("guidance"), limit=6, chars=300),
    }


def _project_experience_memory(value: Any) -> dict[str, Any]:
    memory = value if isinstance(value, dict) else {}
    tiers = memory.get("memory_tiers") if isinstance(memory.get("memory_tiers"), dict) else {}
    return {
        "schema_version": memory.get("schema_version"),
        "write_policy": memory.get("write_policy") or {},
        "memory_tiers": {
            "candidate_lessons": _project_lessons(tiers.get("candidate_lessons"), limit=6),
            "validated_lessons": _project_lessons(tiers.get("validated_lessons"), limit=6),
            "candidate_lesson_count": len([item for item in tiers.get("candidate_lessons") or [] if isinstance(item, dict)]),
            "validated_lesson_count": len([item for item in tiers.get("validated_lessons") or [] if isinstance(item, dict)]),
        },
        "agent_generated_quality_memory": _compact_json_value(
            memory.get("agent_generated_quality_memory"),
            max_chars=1_400,
        ),
        "algorithm_semantic_memory": _compact_json_value(
            memory.get("algorithm_semantic_memory"),
            max_chars=1_600,
        ),
        "skill_usage_summary": _compact_json_value(memory.get("skill_usage_summary"), max_chars=1_000),
        "self_evolution_metrics": _compact_json_value(memory.get("self_evolution_metrics"), max_chars=1_000),
        "next_context_guidance": _bounded_strings(memory.get("next_context_guidance"), limit=6, chars=300),
    }


def _project_lessons(value: Any, *, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    rows = value if isinstance(value, list) else []
    for item in rows[-limit:]:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        result.append(
            {
                "lesson_id": item.get("lesson_id"),
                "lesson_type": item.get("lesson_type"),
                "strategy": _short_text(item.get("strategy"), 180),
                "strategy_type": item.get("strategy_type"),
                "outcome": item.get("outcome"),
                "applicability": _bounded_strings(item.get("applicability"), limit=4, chars=220),
                "contraindications": _bounded_strings(item.get("contraindications"), limit=4, chars=220),
                "confidence": item.get("confidence"),
                "evidence": {
                    "direction_id": evidence.get("direction_id"),
                    "round_index": evidence.get("round_index"),
                    "decision": evidence.get("decision"),
                    "status": evidence.get("status"),
                    "score_relation": evidence.get("score_relation"),
                },
            }
        )
        if len(result) >= limit:
            break
    return result


def _project_protected_facts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    rows = value if isinstance(value, list) else []
    for item in rows[-8:]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "direction_id": item.get("direction_id"),
                "round_index": item.get("round_index"),
                "name": _short_text(item.get("name"), 180),
                "type": item.get("type"),
                "target_files": _bounded_strings(item.get("target_files"), limit=8, chars=180),
                "novelty": _short_text(item.get("novelty"), 300),
                "expected_effect": _short_text(item.get("expected_effect"), 300),
                "fact_type": item.get("fact_type"),
                "title": _short_text(item.get("title"), 180),
                "summary": _short_text(item.get("summary"), 400),
                "preserve_rule": _short_text(item.get("preserve_rule"), 400),
                "evidence_refs": _bounded_strings(item.get("evidence_refs"), limit=6, chars=220),
            }
        )
        if len(result) >= 8:
            break
    return result


def _project_failure_memory(value: Any) -> dict[str, Any]:
    memory = value if isinstance(value, dict) else {}
    recent_failures = []
    failures = [item for item in memory.get("recent_failures") or [] if isinstance(item, dict)]
    for item in failures[-6:]:
        recent_failures.append(
            {
                "round_index": item.get("round_index"),
                "direction_id": item.get("direction_id"),
                "failure_signatures": _bounded_strings(item.get("failure_signatures"), limit=8, chars=220),
                "decision": item.get("decision"),
                "summary": _short_text(item.get("summary"), 500),
            }
        )
        if len(recent_failures) >= 6:
            break
    return {
        "status": memory.get("status"),
        "review_required": memory.get("review_required"),
        "must_avoid": _bounded_strings(memory.get("must_avoid"), limit=8, chars=240),
        "recent_failures": recent_failures,
    }


def _project_next_round_guidance(value: Any) -> dict[str, Any]:
    guidance = value if isinstance(value, dict) else {}
    return {
        "must_do": _bounded_strings(guidance.get("must_do"), limit=8, chars=260),
        "preserve": _bounded_strings(guidance.get("preserve"), limit=8, chars=260),
        "avoid": _bounded_strings(guidance.get("avoid"), limit=8, chars=260),
        "promote_only_if": _bounded_strings(guidance.get("promote_only_if"), limit=6, chars=260),
    }


def _project_current_round_repair(value: Any) -> dict[str, Any]:
    repair = value if isinstance(value, dict) else {}
    if not repair:
        return {}
    attempts = []
    for item in repair.get("previous_attempts") or []:
        if not isinstance(item, dict):
            continue
        diagnostics = item.get("proposal_diagnostics") if isinstance(item.get("proposal_diagnostics"), dict) else {}
        audit = diagnostics.get("proposal_audit") if isinstance(diagnostics.get("proposal_audit"), dict) else {}
        attempts.append(
            {
                "attempt_index": item.get("attempt_index"),
                "worker_status": item.get("worker_status"),
                "changed_files": _bounded_strings(item.get("changed_files"), limit=8, chars=180),
                "failure_signatures": _bounded_strings(item.get("failure_signatures"), limit=10, chars=220),
                "proposal_summary": _short_text(diagnostics.get("summary"), 500),
                "proposal_strategy": _short_text(diagnostics.get("strategy_intent"), 500),
                "proposal_diagnostics": _project_proposal_diagnostics(diagnostics),
                "accepted_change_paths": _bounded_strings(audit.get("accepted_change_paths"), limit=8, chars=180),
                "rejected_change_count": audit.get("rejected_change_count"),
                "warnings": _bounded_strings(audit.get("warnings"), limit=8, chars=240),
                "semantic_review": _compact_json_value(item.get("semantic_review"), max_chars=1_000),
            }
        )
        if len(attempts) >= 3:
            break
    return {
        key: repair.get(key)
        for key in (
            "status",
            "allow_objective_refinement",
            "attempt_index",
            "max_repair_attempts",
            "must_do",
            "avoid",
        )
        if repair.get(key) not in (None, "", [], {})
    } | {
        "repair_targets": _compact_json_value(repair.get("repair_targets"), max_chars=4_000),
        "previous_attempts": attempts,
    }


def _project_feedback_artifact_refs(loop_feedback: dict[str, Any], previous_rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for kind, key in (
        ("hypothesis_graph", "hypothesis_graph_path"),
        ("experience_memory", "experience_memory_path"),
        ("loop_result", "loop_result_path"),
    ):
        path = _short_text(loop_feedback.get(key), 400)
        if path:
            refs.append({"kind": kind, "path": path})
    for item in previous_rounds[-6:]:
        refs.extend(_project_round_artifact_refs(item))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in refs:
        marker = (str(item.get("kind") or ""), str(item.get("path") or ""))
        if marker in seen or not marker[1]:
            continue
        seen.add(marker)
        deduped.append(item)
        if len(deduped) >= 16:
            break
    return deduped


def _project_round_artifact_refs(value: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for kind, key in (
        ("round_dir", "cycle_dir"),
        ("patch", "patch_path"),
        ("delta", "delta_path"),
        ("context_packet", "context_packet_path"),
    ):
        path = _short_text(value.get(key), 400)
        if path:
            refs.append({"kind": kind, "path": path})
    return refs


def _project_activation_checks(value: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                "id": item.get("id"),
                "path": _short_text(item.get("path"), 180),
                "operator": item.get("operator"),
                "expected": _compact_scalar(item.get("expected")),
                "required": item.get("required"),
                "description": _short_text(item.get("description"), 300),
            }
        )
        if len(checks) >= 8:
            break
    return checks


def _project_rule_operator_hypotheses(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "name": _short_text(item.get("name"), 160),
                "type": item.get("type"),
                "novelty": _short_text(item.get("novelty"), 220),
                "expected_effect": _short_text(item.get("expected_effect"), 260),
                "ablation_plan": _short_text(item.get("ablation_plan"), 260),
            }
        )
        if len(result) >= 4:
            break
    return result


def _compact_json_value(value: Any, *, max_chars: int) -> Any:
    if value in (None, "", [], {}):
        return {} if isinstance(value, dict) else [] if isinstance(value, list) else value
    return compact_json(value, max_chars=max_chars).payload


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _short_text(value, 180)
    if isinstance(value, list):
        return [_compact_scalar(item) for item in value[:8]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 8:
                break
            result[str(key)[:80]] = _compact_scalar(item)
        return result
    return value


def _bounded_strings(value: Any, *, limit: int, chars: int) -> list[str]:
    result: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = _short_text(item, chars)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def activate_method_package_context(
    context: dict[str, Any],
    *,
    direction_plan: dict[str, Any] | None,
    max_chars_per_asset: int = 16000,
) -> dict[str, Any] | None:
    """把一个选中的 Method Package 注入 worker 上下文。

    一轮最多激活一个完整 Method Package，避免两套完整参考实现互相覆盖。
    这不限制 Main 选择多个兼容方法族；互补实现知识由独立的 Worker Skills
    精确匹配并组合。
    """

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    active_features = [str(item) for item in catalog.get("active_features") or []]
    requested_id = str((direction_plan or {}).get("method_package_id") or "").strip()
    if not requested_id:
        context.pop("active_method_package", None)
        return None
    package = resolve_method_package(
        problem_family=str(task.get("problem_family") or ""),
        package_id=requested_id,
        active_features=active_features,
        knowledge_query_tags=[
            str(item)
            for item in (direction_plan or {}).get("knowledge_query") or []
            if str(item).strip()
        ],
    )
    if not package:
        context.pop("active_method_package", None)
        return None

    contract_assets = [
        str(value)
        for value in package.get("implementation_contract_assets")
        or [package.get("implementation_contract_asset")]
        if str(value or "").strip()
    ]
    asset_paths = [
        Path(str(value))
        for value in [*contract_assets, *(package.get("assets") or [])]
        if str(value).strip()
    ]
    existing_cards = [item for item in context.get("knowledge_cards") or [] if isinstance(item, dict)]
    by_path = {str(item.get("path") or ""): item for item in existing_cards if str(item.get("path") or "")}
    package_cards: list[dict[str, Any]] = []
    for asset_path in asset_paths:
        key = str(asset_path)
        card = by_path.get(key) or _source_payload(asset_path, max_chars_per_asset)
        by_path[key] = card
        package_cards.append(card)
    context["knowledge_cards"] = list(by_path.values())
    context["auto_knowledge_cards"] = _unique_strings(
        [
            *(str(item) for item in context.get("auto_knowledge_cards") or []),
            *(str(path) for path in asset_paths),
        ]
    )
    context["active_method_package"] = {
        **package,
        "requested_package_id": requested_id or None,
        "selection": "requested" if requested_id == package.get("package_id") else "recommended_fallback",
        "asset_records": package_cards,
        "worker_rule": (
            "Adapt this one package to the active IO and solver contract. Implement every required component and "
            "coupled group in its implementation_contract before claiming the method is complete. It may be "
            "combined only with method families explicitly selected by Main and matched to Worker Skills. "
            "Same-direction repairs must close the latest missing/partial component matrix rather than switching "
            "methods or patching one symptom in isolation."
        ),
    }
    return context["active_method_package"]


def activate_direction_knowledge_context(
    context: dict[str, Any],
    *,
    direction_plan: dict[str, Any] | None,
    max_chars_per_asset: int = 10_000,
) -> dict[str, Any] | None:
    """Run second-stage retrieval after Main has selected a method direction."""

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    plan = direction_plan if isinstance(direction_plan, dict) else {}
    query = [str(item).strip().lower() for item in plan.get("knowledge_query") or [] if str(item).strip()]
    method_families = plan.get("method_families") or [plan.get("method_family")]
    skill_selection = resolve_worker_implementation_skills(
        problem_family=str(task.get("problem_family") or ""),
        method_families=method_families,
        active_features=[str(item) for item in catalog.get("active_features") or []],
        knowledge_query_tags=query,
    )
    context["active_worker_implementation_skills"] = skill_selection
    if not query:
        context.pop("active_direction_knowledge", None)
        return None
    selection = select_tagged_knowledge_cards(
        problem_family=str(task.get("problem_family") or ""),
        knowledge_query_tags=query,
        instance_diagnostics=(
            context.get("instance_diagnostics")
            if isinstance(context.get("instance_diagnostics"), dict)
            else None
        ),
        active_features=[str(item) for item in catalog.get("active_features") or []],
    )
    paths = [str(path) for path in selection.cards]
    existing_cards = [item for item in context.get("knowledge_cards") or [] if isinstance(item, dict)]
    by_path = {str(item.get("path") or ""): item for item in existing_cards if str(item.get("path") or "")}
    records: list[dict[str, Any]] = []
    for path in selection.cards:
        key = str(path)
        record = by_path.get(key) or _source_payload(path, max_chars_per_asset)
        by_path[key] = record
        records.append(record)
    context["knowledge_cards"] = list(by_path.values())
    context["active_direction_knowledge"] = {
        "method_family": str(plan.get("method_family") or ""),
        "method_families": skill_selection.get("method_families") or [],
        "query": query,
        "paths": paths,
        "asset_records": records,
        "audit": selection.audit,
        "worker_rule": (
            "Read only these second-stage cards and the matched Worker Implementation Skills. Combine only the "
            "method families selected by Main, and convert them into explicit behavioral deliverables."
        ),
    }
    return context["active_direction_knowledge"]


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _improvement_round_hypothesis(base_hypothesis: str) -> str:
    baseline_generation_pattern = (
        r"If baseline_source is agent_generated,\s*first create a runnable solver entrypoint at .*?"
        r"rather than relying on an incumbent solver\.\s*"
    )
    cleaned = re.sub(baseline_generation_pattern, "", base_hypothesis, flags=re.IGNORECASE | re.DOTALL).strip()
    prefix = (
        "This is an improvement round, not baseline generation. A measured incumbent solver already exists. "
        "Use incumbent_code_context and loop_feedback to make one small patch to the promoted solver; do not "
        "create the initial solver again and do not replace the whole existing solver file."
    )
    if not cleaned:
        return prefix
    return f"{prefix}\n\nOriginal task context, with baseline-generation instructions superseded:\n{cleaned}"


def _incumbent_code_context(packet: dict[str, Any], *, project_root: Path | None, max_chars: int = 16000) -> dict[str, Any] | None:
    if project_root is None:
        return None
    evaluator_protocol = packet.get("evaluator_protocol")
    if not isinstance(evaluator_protocol, dict):
        return None
    solver_template = str(evaluator_protocol.get("solver_command_template") or "")
    relative_paths = _python_paths_from_command(solver_template)
    files: list[dict[str, Any]] = []
    for relative in relative_paths:
        source = (project_root / relative).resolve()
        try:
            source.relative_to(project_root.resolve())
        except ValueError:
            continue
        if not source.is_file():
            continue
        payload = _source_payload(source, max_chars)
        payload["relative_path"] = relative.as_posix()
        files.append(payload)
    if not files:
        return None
    return {
        "source": "promoted_incumbent_worktree",
        "root": str(project_root),
        "purpose": (
            "Current solver source available for incremental text_replace or insert_after proposals. "
            "Preserve this structure unless loop_feedback identifies a measured weakness."
        ),
        "files": files,
    }


def _python_paths_from_command(command: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?P<quote>['\"])?(?P<path>[A-Za-z0-9_./\\-]+\.py)(?P=quote)?", command):
        raw_path = match.group("path").replace("\\", "/").strip()
        if not raw_path or "{" in raw_path or "}" in raw_path:
            continue
        path = Path(raw_path)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            continue
        normalized = path.as_posix()
        if normalized not in seen:
            seen.add(normalized)
            paths.append(Path(normalized))
    return paths


def _source_payload(path: Path, max_chars: int) -> dict[str, Any]:
    """把文档/知识卡文件变成可嵌入 packet 的源记录。

    统一保留路径、存在性、文本摘要和 sha256，方便后续压缩、去重和人工审计。
    """

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        exists = True
        error = None
    except OSError as exc:
        text = ""
        exists = False
        error = str(exc)
    truncated = len(text) > max_chars
    snippet = text[:max_chars]
    return {
        "path": str(path),
        "exists": exists,
        "sha256": _hash_text(text) if exists else None,
        "chars": len(text),
        "truncated": truncated,
        "snippet": snippet,
        "error": error,
    }


def _uses_agent_generated_solver(contract: TaskContract) -> bool:
    solver = contract.commands.solver.replace("\\", "/").lower()
    return "agent_generated" in solver or "generated_fjsp" in solver


def _project_intake_payload(path: Path, max_chars: int) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        manifest = json.loads(text)
        exists = True
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        text = ""
        manifest = {}
        exists = False
        error = str(exc)

    artifacts = manifest.get("artifacts") or {}
    report_path = Path(str(artifacts["report"])) if artifacts.get("report") else None
    report = _source_payload(report_path, max_chars) if report_path else None
    return {
        "path": str(path),
        "exists": exists,
        "sha256": _hash_text(text) if exists else None,
        "status": manifest.get("status"),
        "error": error,
        "summary": _compact_project_intake(manifest),
        "report": report,
    }


def _slot_manifest_payload(path: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    """把 slot manifest 转成上下文记录。

    slot 插件是可选能力，不是默认执行路径；因此这里既携带 manifest 元数据，也尽量
    补齐已确认 slot 的块位置信息，供需要 slot-based edit 的 worker 使用。
    """

    try:
        manifest = load_slot_manifest(path)
        exists = True
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}
        exists = False
        error = str(exc)
    slots = manifest.get("slots") if isinstance(manifest, dict) else []
    if not isinstance(slots, list):
        slots = []
    manifest_root = path.resolve().parent
    repo_root = Path(__file__).resolve().parents[2]
    resolved_project_root = project_root.resolve() if project_root else None
    selected_slots = []
    for item in slots:
        if not isinstance(item, dict):
            continue
        target_file = str(item.get("target_file", ""))
        source_text = None
        if target_file:
            target_path = Path(target_file)
            candidates = []
            if target_path.is_absolute():
                candidates.append(target_path)
            else:
                if resolved_project_root is not None:
                    candidates.append(resolved_project_root / target_path)
                candidates.extend([repo_root / target_path, manifest_root / target_path])
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        source_text = candidate.read_text(encoding="utf-8")
                        break
                except OSError:
                    continue
        resolved = ResolvedCodeSlot.from_manifest_slot(item, source_text=source_text)
        selected_slots.append(
            {
            "slot_id": str(item.get("slot_id", "")),
            "title": str(item.get("title", "")),
            "problem_family": str(manifest.get("problem_family") or ""),
            "target_file": target_file,
            "marker_start": str(item.get("marker_start", "")),
            "marker_end": str(item.get("marker_end", "")),
            "slot_kind": str(item.get("slot_kind", "")),
            "language": str(item.get("language", "")),
            "line_start": resolved.line_start,
            "line_end": resolved.line_end,
            "block_name": resolved.block_name,
            "context_before": resolved.context_before,
            "context_after": resolved.context_after,
            "original_content": resolved.original_content,
            "purpose": str(item.get("purpose", "")),
            "inputs": item.get("inputs", []),
            "outputs": item.get("outputs", []),
            "invariants": item.get("invariants", []),
            "allowed_edits": item.get("allowed_edits", []),
            "forbidden_edits": item.get("forbidden_edits", []),
            "validation_commands": item.get("validation_commands", []),
            "knowledge_tags": item.get("knowledge_tags", []),
            "user_confirmed": bool(item.get("user_confirmed", False)),
            }
        )
    return {
        "path": str(path),
        "exists": exists,
        "status": manifest.get("status") if isinstance(manifest, dict) else None,
        "problem_family": manifest.get("problem_family") if isinstance(manifest, dict) else None,
        "confirmation_required": bool(manifest.get("confirmation_required", True)) if isinstance(manifest, dict) else True,
        "slots": selected_slots,
        "error": error,
    }


def _refresh_slot_manifest_sources(slot_manifest: Any, *, project_root: Path) -> Any:
    if not isinstance(slot_manifest, dict):
        return slot_manifest
    slots = slot_manifest.get("slots")
    if not isinstance(slots, list):
        return slot_manifest

    refreshed = dict(slot_manifest)
    refreshed_slots: list[dict[str, Any]] = []
    resolved_project_root = project_root.resolve()
    for item in slots:
        if not isinstance(item, dict):
            continue
        refreshed_slot = dict(item)
        target_file = str(refreshed_slot.get("target_file", ""))
        source_text = None
        if target_file:
            target_path = Path(target_file)
            candidate = target_path if target_path.is_absolute() else resolved_project_root / target_path
            try:
                if candidate.is_file():
                    source_text = candidate.read_text(encoding="utf-8")
            except OSError:
                source_text = None
        resolved = ResolvedCodeSlot.from_manifest_slot(refreshed_slot, source_text=source_text)
        refreshed_slot.update(
            {
                "line_start": resolved.line_start,
                "line_end": resolved.line_end,
                "block_name": resolved.block_name,
                "context_before": resolved.context_before,
                "context_after": resolved.context_after,
                "original_content": resolved.original_content,
            }
        )
        refreshed_slots.append(refreshed_slot)
    refreshed["slots"] = refreshed_slots
    return refreshed


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _pipeline_memory_payload(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        memory = json.loads(text)
        exists = True
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        text = ""
        memory = {}
        exists = False
        error = str(exc)

    raw_experience_signal = _raw_experience_memory_signal(memory)
    experience_signal = memory.get("experience_memory_signal") or raw_experience_signal

    return {
        "path": str(path),
        "exists": exists,
        "sha256": _hash_text(text) if exists else None,
        "schema_version": memory.get("schema_version"),
        "pipeline_status": memory.get("pipeline_status"),
        "stage_status": memory.get("stage_status") or {},
        "admission": memory.get("admission") or {},
        "benchmark_signal": memory.get("benchmark_signal") or {},
        "worker_signal": _compact_worker_signal(memory.get("worker_signal") or {}),
        "operator_lineage_signal": _compact_operator_lineage_signal(memory.get("operator_lineage_signal") or {}),
        "direction_graph_signal": _compact_direction_graph_signal(memory.get("direction_graph_signal") or {}),
        "experience_memory_signal": _compact_experience_memory_signal(experience_signal),
        "skill_usage_signal": memory.get("skill_usage_signal") or {},
        "operator_guidance": _operator_guidance_from_memory(memory),
        "evidence_signal": memory.get("evidence_signal") or {},
        "recommendations": (memory.get("recommendations") or [])[:20],
        "artifacts": memory.get("artifacts") or {},
        "error": error,
    }


def _compact_direction_graph_signal(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal:
        return {}
    return {
        "schema_version": signal.get("schema_version"),
        "round_semantics": signal.get("round_semantics"),
        "direction_count": signal.get("direction_count", 0),
        "attempt_count": signal.get("attempt_count", 0),
        "status_counts": signal.get("status_counts") or {},
        "decision_counts": signal.get("decision_counts") or {},
        "promoted_direction_ids": (signal.get("promoted_direction_ids") or [])[:8],
        "recent_directions": _compact_direction_records(signal.get("recent_directions") or [], limit=8),
        "guidance": (signal.get("guidance") or [])[:8],
    }


def _compact_experience_memory_signal(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal:
        return {}
    return {
        "schema_version": signal.get("schema_version"),
        "write_policy": signal.get("write_policy") or {},
        "candidate_lesson_count": signal.get("candidate_lesson_count", 0),
        "candidate_lessons": _compact_lesson_records(signal.get("candidate_lessons") or [], limit=10),
        "candidate_lessons_withheld": bool(signal.get("candidate_lessons_withheld")),
        "validated_lesson_count": signal.get("validated_lesson_count", 0),
        "validated_lessons": _compact_lesson_records(signal.get("validated_lessons") or [], limit=10),
        "self_evolution_metrics": signal.get("self_evolution_metrics") or {},
        "algorithm_semantic_memory": signal.get("algorithm_semantic_memory") or {},
        "next_context_guidance": (signal.get("next_context_guidance") or [])[:8],
    }


def _raw_experience_memory_signal(memory: dict[str, Any]) -> dict[str, Any]:
    tiers = memory.get("memory_tiers") if isinstance(memory.get("memory_tiers"), dict) else {}
    if not tiers:
        return {}
    candidate = [item for item in tiers.get("candidate_lessons") or [] if isinstance(item, dict)]
    validated = [item for item in tiers.get("validated_lessons") or [] if isinstance(item, dict)]
    return {
        "schema_version": memory.get("schema_version"),
        "write_policy": memory.get("write_policy") or {},
        "candidate_lesson_count": len(candidate),
        "candidate_lessons": [],
        "candidate_lessons_withheld": bool(candidate),
        "validated_lesson_count": len(validated),
        "validated_lessons": validated,
        "self_evolution_metrics": memory.get("self_evolution_metrics") or {},
        "algorithm_semantic_memory": memory.get("algorithm_semantic_memory") or {},
        "next_context_guidance": memory.get("next_context_guidance") or [],
    }


def _compact_direction_records(records: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "direction_id": item.get("direction_id"),
                "round_index": item.get("round_index"),
                "title": str(item.get("title") or "")[:160],
                "status": item.get("status"),
                "decision": item.get("decision"),
                "strategy_type": item.get("strategy_type"),
                "method_package_id": item.get("method_package_id"),
                "attempt_count": item.get("attempt_count"),
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _compact_lesson_records(records: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "lesson_id": item.get("lesson_id"),
                "lesson_type": item.get("lesson_type"),
                "strategy": str(item.get("strategy") or "")[:160],
                "strategy_type": item.get("strategy_type"),
                "method_package_id": item.get("method_package_id"),
                "outcome": item.get("outcome"),
                "confidence": item.get("confidence"),
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _compact_operator_lineage_signal(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal:
        return {}
    return {
        "hypothesis_count": signal.get("hypothesis_count", 0),
        "missing_hypothesis_rounds": signal.get("missing_hypothesis_rounds", 0),
        "type_counts": signal.get("type_counts") or {},
        "decision_counts": signal.get("decision_counts") or {},
        "target_file_counts": signal.get("target_file_counts") or {},
        "promoted_hypotheses": _compact_lineage_records(signal.get("promoted_hypotheses") or [], limit=8),
        "rolled_back_hypotheses": _compact_lineage_records(signal.get("rolled_back_hypotheses") or [], limit=8),
        "duplicate_hypotheses": _compact_lineage_records(signal.get("duplicate_hypotheses") or [], limit=8),
    }


def _operator_guidance_from_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """Translate pipeline memory into worker-facing rule/operator instructions.

    The guidance is prompt material only.  It helps a coding worker produce more
    auditable and diverse hypotheses while leaving evaluator acceptance
    unchanged.
    """

    signal = _compact_operator_lineage_signal(memory.get("operator_lineage_signal") or {})
    if not signal:
        return {
            "status": "missing_lineage",
            "must_do": ["Declare explicit rule_operator_hypotheses before code changes."],
            "preserve": [],
            "mutate": [],
            "avoid": [],
            "evidence": [],
        }

    promoted = signal.get("promoted_hypotheses") or []
    rolled_back = signal.get("rolled_back_hypotheses") or []
    duplicate = signal.get("duplicate_hypotheses") or []
    missing_rounds = int(signal.get("missing_hypothesis_rounds", 0) or 0)
    must_do = [
        "Use Core evaluator metrics as the only success evidence.",
        "State the natural-language rule/operator idea before editing code.",
    ]
    if missing_rounds > 0:
        must_do.append(
            "Previous rounds lacked auditable rule/operator hypotheses; include 1 to 3 concrete hypotheses with target files."
        )

    return {
        "status": "available",
        "must_do": must_do,
        "preserve": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
                "reason": "Promoted in prior evaluator-backed loop; preserve or ablate before replacing.",
            }
            for item in promoted[:5]
        ],
        "mutate": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
                "reason": "Rolled back in prior evaluator-backed loop; do not repeat unchanged.",
            }
            for item in rolled_back[:5]
        ],
        "avoid": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
                "reason": "Duplicate proposal lineage; novelty must explain a material difference.",
            }
            for item in duplicate[:5]
        ],
        "evidence": [
            f"hypothesis_count={signal.get('hypothesis_count', 0)}",
            f"missing_hypothesis_rounds={missing_rounds}",
            f"type_counts={json.dumps(signal.get('type_counts') or {}, ensure_ascii=False)}",
        ],
    }


def _compact_lineage_records(records: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "duplicate_proposal": item.get("duplicate_proposal"),
                "name": str(item.get("name") or "")[:120],
                "type": str(item.get("type") or "")[:80],
                "target_files": [str(value) for value in (item.get("target_files") or [])[:12]],
                "expected_effect": str(item.get("expected_effect") or "")[:240],
                "novelty": str(item.get("novelty") or "")[:240],
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _contract_review_payload(review: dict[str, Any]) -> dict[str, Any]:
    """压缩 Task Contract review 证据。

    目标不是重复整份 review，而是保留 worker 最先该看的证据骨架：不确定字段、
    特征/指标提示、文档结构和推荐阅读 section。
    """

    document_schema = _compact_document_schema(review.get("document_schema") or {})
    role_prioritized_sections = _role_prioritized_sections(document_schema, limit=16)
    return {
        "status": review.get("status"),
        "uncertain_fields": (review.get("uncertain_fields") or [])[:30],
        "extracted_problem_features": _compact_feature_hints(review.get("extracted_problem_features") or [], limit=30),
        "metric_hints": _compact_metric_hints(review.get("metric_hints") or [], limit=30),
        "document_schema": document_schema,
        "role_prioritized_sections": role_prioritized_sections,
        "has_document_schema": bool(document_schema.get("section_count")),
        "extraction_method": review.get("extraction_method"),
    }


def _compact_document_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """压缩文档结构抽取结果。

    文档 section 数量可能远大于 worker 首轮可读预算，因此这里保留结构化骨架，
    再由 `_role_prioritized_sections()` 导出一个更短的优先阅读列表。
    """

    if not schema:
        return {}
    compact_documents = []
    section_budget = 40
    for document in schema.get("documents") or []:
        sections = []
        for section in document.get("sections") or []:
            if section_budget <= 0:
                break
            sections.append(
                {
                    "heading": section.get("heading"),
                    "level": section.get("level"),
                    "line_start": section.get("line_start"),
                    "line_end": section.get("line_end"),
                    "roles": section.get("roles") or [],
                    "feature_hints": _compact_feature_hints(section.get("feature_hints") or [], limit=8),
                    "metric_hints": _compact_metric_hints(section.get("metric_hints") or [], limit=8),
                    "evidence_excerpt": str(section.get("evidence_excerpt") or "")[:180],
                }
            )
            section_budget -= 1
        compact_documents.append(
            {
                "path": document.get("path"),
                "section_count": document.get("section_count", len(sections)),
                "sections": sections,
            }
        )
        if section_budget <= 0:
            break
    return {
        "schema_version": schema.get("schema_version"),
        "document_count": schema.get("document_count", len(compact_documents)),
        "section_count": schema.get("section_count", sum(len(item["sections"]) for item in compact_documents)),
        "role_counts": schema.get("role_counts") or {},
        "documents": compact_documents,
        "truncated": section_budget <= 0,
    }


def _role_prioritized_sections(schema: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    """Return the most useful Markdown sections for a worker's first read.

    The full schema is preserved for auditability.  This derived list is a
    bounded reading order for long documents, so workers inspect objective,
    constraint, IO, and acceptance evidence before generic prose.
    """

    candidates: list[dict[str, Any]] = []
    for document_index, document in enumerate(schema.get("documents") or []):
        for section_index, section in enumerate(document.get("sections") or []):
            roles = list(section.get("roles") or ["general"])
            best_role_rank = min(SECTION_ROLE_PRIORITY.get(str(role), 8) for role in roles)
            hint_bonus = len(section.get("feature_hints") or []) + len(section.get("metric_hints") or [])
            candidates.append(
                {
                    "sort_key": (best_role_rank, -hint_bonus, document_index, section_index),
                    "payload": {
                        "source": document.get("path"),
                        "heading": section.get("heading"),
                        "line_start": section.get("line_start"),
                        "line_end": section.get("line_end"),
                        "roles": roles,
                        "feature_hints": _compact_feature_hints(section.get("feature_hints") or [], limit=8),
                        "metric_hints": _compact_metric_hints(section.get("metric_hints") or [], limit=8),
                        "evidence_excerpt": str(section.get("evidence_excerpt") or "")[:220],
                        "priority_reason": _priority_reason(roles, hint_bonus),
                    },
                }
            )

    candidates.sort(key=lambda item: item["sort_key"])
    return [item["payload"] for item in candidates[:limit]]


def _priority_reason(roles: list[str], hint_bonus: int) -> str:
    role_text = ", ".join(roles) if roles else "general"
    if hint_bonus:
        return f"roles={role_text}; contains {hint_bonus} extracted feature/metric hints"
    return f"roles={role_text}"


def _compact_feature_hints(hints: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    compact = []
    for item in hints[:limit]:
        compact.append(
            {
                "name": item.get("name"),
                "category": item.get("category"),
                "matched_pattern": item.get("matched_pattern"),
            }
        )
    return compact


def _compact_metric_hints(hints: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    compact = []
    for item in hints[:limit]:
        compact.append(
            {
                "metric": item.get("metric"),
                "direction": item.get("direction"),
                "matched_pattern": item.get("matched_pattern"),
            }
        )
    return compact


def _compact_worker_signal(worker_signal: dict[str, Any]) -> dict[str, Any]:
    rounds = []
    for item in worker_signal.get("rounds") or []:
        if not isinstance(item, dict):
            continue
        rounds.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "worker_status": item.get("worker_status"),
                "duplicate_proposal": item.get("duplicate_proposal"),
                "candidate_key": item.get("candidate_key"),
                "incumbent_key_after": item.get("incumbent_key_after"),
                "changed_files": (item.get("changed_files") or [])[:20],
                "proposal_diagnostics": item.get("proposal_diagnostics") or {},
            }
        )
        if len(rounds) >= 20:
            break
    return {
        "baseline_key": worker_signal.get("baseline_key"),
        "final_key": worker_signal.get("final_key"),
        "improved": worker_signal.get("improved"),
        "round_count": worker_signal.get("round_count", 0),
        "promoted_rounds": worker_signal.get("promoted_rounds", 0),
        "rounds": rounds,
    }


def _compact_project_intake(manifest: dict[str, Any]) -> dict[str, Any]:
    if not manifest:
        return {}
    context_index = []
    for item in manifest.get("context_index") or []:
        context_index.append(
            {
                "path": item.get("path"),
                "line_count": item.get("line_count"),
                "symbols": item.get("symbols") or [],
                "imports": item.get("imports") or [],
            }
        )
        if len(context_index) >= 40:
            break
    return {
        "project_root": manifest.get("project_root"),
        "git": {
            "branch": (manifest.get("git") or {}).get("branch"),
            "commit": (manifest.get("git") or {}).get("commit"),
            "dirty": (manifest.get("git") or {}).get("dirty"),
            "recent_hotspots": (manifest.get("git") or {}).get("recent_hotspots") or [],
        },
        "language_summary": manifest.get("language_summary") or {},
        "file_tree_summary": manifest.get("file_tree_summary") or {},
        "entry_files": (manifest.get("entry_files") or [])[:20],
        "core_algorithm_files": (manifest.get("core_algorithm_files") or [])[:30],
        "dependency_files": manifest.get("dependency_files") or [],
        "benchmark_files": (manifest.get("benchmark_files") or [])[:20],
        "validator_files": (manifest.get("validator_files") or [])[:20],
        "test_commands": manifest.get("test_commands") or [],
        "data_dirs": manifest.get("data_dirs") or [],
        "output_format_hints": manifest.get("output_format_hints") or {},
        "edit_policy": manifest.get("edit_policy") or {},
        "risk_flags": manifest.get("risk_flags") or [],
        "context_index": context_index,
    }


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
