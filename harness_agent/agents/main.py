"""Main Agent：基于文档、检索知识和历史证据规划下一条改进方向。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness_agent.context.compaction import compact_json, compact_source_records, stable_worker_context_json
from harness_agent.context.loader import load_context_dict
from harness_agent.context.planning_packet import project_research_state
from harness_agent.context.worker import build_worker_assignment, write_worker_assignment
from harness_agent.deepseek_client import DeepSeekClient, is_deepseek_configured
from harness_agent.worker import WorkerAssignment


@dataclass(frozen=True)
class DirectionPlanRequest:
    """Main Agent 单次规划输入：稳定任务上下文、动态证据和产物目录。"""

    round_index: int
    context_packet_path: Path
    loop_feedback: dict[str, Any]
    output_dir: Path


@dataclass(frozen=True)
class WorkerAssignmentRequest:
    """Main Agent 将一个方向编译为 Coding Worker 可见任务书的输入。"""

    round_index: int
    attempt_index: int
    context_packet_path: Path
    direction_plan: dict[str, Any]
    loop_feedback: dict[str, Any]
    output_dir: Path
    max_steps: int
    max_runtime_seconds: int
    parent_assignment_path: Path | None = None


@dataclass(frozen=True)
class WorkerAssignmentIssue:
    """一次已校验、已落盘的 Main Agent 任务书签发结果。"""

    assignment: WorkerAssignment
    artifact_path: Path


@dataclass(frozen=True)
class RoundReflectionRequest:
    """Evaluator-backed evidence for Main's post-round causal reflection."""

    round_index: int
    direction_plan: dict[str, Any]
    competition_result: dict[str, Any]
    promotion_check: dict[str, Any]
    incumbent_key_before: tuple[float, ...]
    incumbent_key_after: tuple[float, ...]
    output_dir: Path


class DirectionPlanningAgent(Protocol):
    """方向规划协议；实现只能返回计划，不能直接修改 solver。"""

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        ...

    def issue_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        ...

    def revise_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        ...

    def reflect_on_round(self, request: RoundReflectionRequest) -> dict[str, Any]:
        ...


class EvidenceDrivenMainAgent:
    """无模型时的确定性兜底，只把 Core 证据整理为一个改进方向。"""

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        context = load_context_dict(request.context_packet_path)
        guidance = request.loop_feedback.get("next_round_guidance")
        if not isinstance(guidance, dict):
            guidance = {}
        must_do = _strings(guidance.get("must_do"), limit=6)
        preserve = _strings(guidance.get("preserve"), limit=6)
        avoid = _strings(guidance.get("avoid"), limit=6)
        hypothesis = str(context.get("hypothesis") or "在固定 evaluator 下改进当前 incumbent。").strip()
        baseline_generation = request.round_index < 0 or request.loop_feedback.get("round_type") == "agent_generated_baseline"
        fallback_order = fallback_improvement_order(
            context=context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        inherited = fallback_research_context(context, request.loop_feedback)
        selected_method_families = inherited.get("method_families") or [{"id": "constructive_search", "role": "primary"}]
        selected_method_family = inherited.get("method_family") or "constructive_search"
        default_query = default_direction_knowledge_query(
            instance_diagnostics=context.get("instance_diagnostics"),
            method_families=selected_method_families,
            fallback=["initialization", "decoder"],
        )
        plan = bind_direction_plan_to_method_catalog(normalize_direction_plan(
            {
                "direction_id": f"d{request.round_index:03d}",
                "title": must_do[0] if must_do else hypothesis[:160],
                "strategy_type": (
                    "baseline_constructor" if baseline_generation else "repair_rule" if avoid else "local_search_operator"
                ),
                "hypothesis": hypothesis,
                "diagnosis": (
                    "下一候选必须保留 incumbent，并针对最近一次 evaluator 证据指出的缺口做有界修改。"
                    if request.round_index >= 0
                    else "当前还没有可运行 incumbent；应先根据实例特征选择合适的方法族，再生成满足活动契约的完整 baseline。"
                ),
                "observed_shortcomings": must_do[:3] or [
                    "现有证据尚不能证明某个具体算子能够带来严格改进。"
                ],
                "reasoning_trace": [
                    {
                        "stage": "证据盘点",
                        "summary": "先确认 incumbent 的合法性、目标值和已验证机制，再判断下一轮研究压力。",
                        "evidence": ["Core evaluator 与语义审查是当前权威证据。"],
                        "inference": "缺少模型规划时只能选择一个保守、可归因的增量实验。",
                        "decision": "保留 incumbent，不重建 baseline。",
                        "next_check": "由下一次 Core 结果判断修改是否严格提升。",
                    },
                    {
                        "stage": "方向收敛",
                        "summary": must_do[0] if must_do else "只测试一个有证据支持的 incumbent 组件。",
                        "evidence": must_do[:3] or ["当前没有已证明有效的新算子证据。"],
                        "inference": "扩大修改范围会降低归因能力并增加合法性风险。",
                        "decision": "签发一个有界 Worker 任务书。",
                        "next_check": "检查编译、合法性、运行时间和声明目标。",
                    },
                ],
                "incumbent_assessment": _fallback_incumbent_assessment(context),
                "evidence_summary": [
                    "Core evaluator 的合法性和 promotion 结果是质量判断的唯一权威。"
                ],
                "direction_judgment": (
                    must_do[0]
                    if must_do
                    else "保留 incumbent，只测试一个有证据支持且可独立归因的组件。"
                ),
                "alternatives_considered": [
                    "若合法性或实现覆盖仍有缺口，应先修复，再调整目标质量。",
                    "只有 incumbent 保持完整可执行时，才细化单个连贯算子。",
                ],
                "selection_rationale": (
                    "该方向遵循当前优先级最高的证据，并且能够由 Core 独立度量。"
                ),
                "preserve": preserve,
                "change_scope": must_do[:3] or ["只做一个可由 evaluator 检查的有界 solver 修改。"],
                "next_mutation": {
                    "target_symbols": [],
                    "change": must_do[0] if must_do else "只测试一个可独立归因的 incumbent 增量修改。",
                    "preserve": preserve,
                    "expected_effect": "由 Core evaluator 判断该修改是否带来严格目标改进。",
                    "falsification_metrics": ["合法性", "声明目标", "运行时间"],
                },
                "implementation_order": [] if baseline_generation else fallback_order,
                "avoid": avoid,
                "knowledge_paths": [],
                "experiment_stage": inherited.get("experiment_stage") or "probe",
                "knowledge_query": inherited.get("knowledge_query") or default_query,
                "method_family": selected_method_family,
                "method_families": selected_method_families,
                "method_package_id": "",
                "acceptance_checks": [
                    "候选通过确定性预检和固定 evaluator。",
                    "候选在活动任务契约下保持完整合法输出。",
                    "候选只有严格优于 incumbent 才能 promotion。",
                ],
                "activation_checks": inherited.get("activation_checks") or [],
                "activation_contract_version": 1 if inherited.get("activation_checks") else 0,
                "candidate_variants": inherited.get("candidate_variants") or [],
                "stop_conditions": [
                    "该方向的具体修补预算耗尽后停止。",
                    "修补过程中不得切换到无关方法。",
                ],
                "completion_rule": (
                    "Complete all selected method components and pass the bounded checks before handoff."
                    if baseline_generation
                    else "Complete only the selected incremental component while preserving the complete incumbent method."
                ),
                "planner": "evidence_fallback",
            },
            round_index=request.round_index,
        ), context=context)
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        plan["planning_contract_status"] = fallback_planning_contract_status(
            plan,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        if inherited.get("transition_deferred"):
            plan["fallback_transition"] = {
                "status": "deferred",
                "requested_action": inherited.get("deferred_action"),
                "reason": (
                    "The deterministic fallback cannot safely select a new method family and regenerate its "
                    "experiment contract; the last complete contract is preserved for an instrumented probe."
                ),
            }
        return write_direction_plan(request.output_dir, plan)

    def issue_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        return _compile_worker_assignment(request)

    def revise_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        return _compile_worker_assignment(request, require_parent=True)

    def reflect_on_round(self, request: RoundReflectionRequest) -> dict[str, Any]:
        return write_round_reflection(request.output_dir, deterministic_round_reflection(request))


def fallback_research_context(
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
) -> dict[str, Any]:
    """Apply the same research transition when provider failure forces planning."""

    rounds = [item for item in loop_feedback.get("previous_rounds") or [] if isinstance(item, dict)]
    if not rounds:
        return {}
    state = project_research_state(
        rounds,
        next_round_guidance=loop_feedback.get("next_round_guidance"),
        user_intervention=loop_feedback.get("user_intervention"),
    )
    latest_plan = (
        rounds[-1].get("direction_plan")
        if isinstance(rounds[-1].get("direction_plan"), dict)
        else {}
    )
    reflection = (
        rounds[-1].get("round_reflection")
        if isinstance(rounds[-1].get("round_reflection"), dict)
        else {}
    )
    next_action = reflection.get("next_action") if isinstance(reflection.get("next_action"), dict) else {}
    activation_checks = (
        latest_plan.get("activation_checks")
        or next_action.get("required_activation_checks")
        or []
    )
    transition_deferred = state.get("method_family_policy") == "reselect"
    return {
        "method_family": latest_plan.get("method_family") or state.get("active_method_family"),
        "method_families": latest_plan.get("method_families") or state.get("active_method_families") or [],
        "knowledge_query": latest_plan.get("knowledge_query") or state.get("active_knowledge_query") or [],
        "experiment_stage": "probe" if transition_deferred else state.get("experiment_stage") or "probe",
        "activation_checks": activation_checks,
        "candidate_variants": latest_plan.get("candidate_variants") or [],
        "transition_deferred": transition_deferred,
        "deferred_action": state.get("next_action") if transition_deferred else None,
    }


def fallback_planning_contract_status(
    plan: dict[str, Any],
    *,
    loop_feedback: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    """Expose fallback contract loss without inventing unsupported experiments."""

    competition = (
        loop_feedback.get("competition")
        if isinstance(loop_feedback.get("competition"), dict)
        else {}
    )
    max_workers = max(
        1,
        min(4, _positive_int(competition.get("max_competing_workers"), default=1)),
    )
    minimum_variants = 2 if round_index >= 0 and max_workers > 1 else 0
    variants = [item for item in plan.get("candidate_variants") or [] if isinstance(item, dict)]
    issues: list[str] = []
    if round_index >= 0 and not normalize_activation_checks(plan.get("activation_checks")):
        issues.append("main_activation_checks_missing")
    if len(variants) < minimum_variants:
        issues.append("minimum_candidate_variants_not_met")
    if any(not normalize_activation_checks(item.get("activation_checks")) for item in variants):
        issues.append("candidate_activation_checks_missing")
    return {
        "schema_version": 1,
        "status": "degraded" if issues else "satisfied",
        "source": "deterministic_fallback",
        "maximum_candidate_variants": max_workers,
        "minimum_candidate_variants": minimum_variants,
        "actual_candidate_variants": len(variants),
        "issues": issues,
        "activation_mode": (
            "legacy_compatibility"
            if round_index >= 0 and "main_activation_checks_missing" in issues
            else "declared_contract"
        ),
        "promotion_policy": "legacy_evaluator_and_semantic_gates" if issues else "normal",
    }


class DeepSeekMainAgent:
    """Plan one method direction; never write or apply solver code."""

    def __init__(self, model: str = "deepseek-v4-pro") -> None:
        self.model = model
        self.fallback = EvidenceDrivenMainAgent()

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        if not is_deepseek_configured():
            return self.fallback.plan_direction(request)

        context = load_context_dict(request.context_packet_path)
        stable_context = stable_worker_context_json(context).text
        dynamic_context = compact_main_agent_dynamic_context(
            context=context,
            loop_feedback=request.loop_feedback,
        )
        prompt = _direction_prompt(
            round_index=request.round_index,
            stable_context=stable_context,
            dynamic_context=dynamic_context,
        )
        client = DeepSeekClient.from_env(model=self.model)
        response = client.chat_with_usage(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the Main Agent for an evaluator-gated algorithm evolution system. "
                        "Plan one experiment direction only. Do not write code. Return valid JSON only. "
                        "All user-visible natural-language JSON values must use Simplified Chinese; keep keys, IDs, paths, and code symbols unchanged."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.15,
            max_tokens=3500,
            json_mode=True,
        )
        usage = response.usage
        try:
            raw = json.loads(response.content)
        except json.JSONDecodeError:
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "main_agent_invalid_response.txt").write_text(response.content, encoding="utf-8")
            retry = client.chat_with_usage(
                [
                    {
                        "role": "system",
                        "content": "Repair the supplied planning response into one valid JSON object. Return JSON only.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Keep the same experiment direction and method_package_id. Use only the documented "
                            "direction-plan keys and remove prose outside JSON.\n\n" + response.content[:12000]
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=3500,
                json_mode=True,
            )
            (request.output_dir / "main_agent_json_retry.json").write_text(retry.content, encoding="utf-8")
            usage = _merge_usage(response.usage, retry.usage)
            try:
                raw = json.loads(retry.content)
            except json.JSONDecodeError:
                return self.fallback.plan_direction(request)
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(raw, round_index=request.round_index),
            context=context,
        )
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        plan["planner"] = "deepseek_main_agent"
        request.output_dir.mkdir(parents=True, exist_ok=True)
        (request.output_dir / "main_agent_usage.json").write_text(
            json.dumps(
                {"usage": usage, "cache_hit_ratio": response.cache_hit_ratio},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return write_direction_plan(request.output_dir, plan)

    def issue_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        # 当前模型调用负责方法判断和方向分解；任务书编译保持确定性，避免
        # Worker 权限边界被自由文本或不稳定 JSON 扩大。
        return _compile_worker_assignment(request)

    def revise_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        return _compile_worker_assignment(request, require_parent=True)

    def reflect_on_round(self, request: RoundReflectionRequest) -> dict[str, Any]:
        return self.fallback.reflect_on_round(request)


def normalize_direction_plan(value: Any, *, round_index: int) -> dict[str, Any]:
    """将模型自由 JSON 收敛为固定 DirectionPlan schema 和长度上限。"""

    raw = value if isinstance(value, dict) else {}
    incumbent_assessment = normalize_incumbent_assessment(raw.get("incumbent_assessment"))
    next_mutation = _normalize_next_mutation(raw.get("next_mutation"))
    reasoning_trace = normalize_public_reasoning_trace(raw.get("reasoning_trace"))
    method_families = normalize_method_families(raw.get("method_families"), raw.get("method_family"))
    plan = {
        "schema_version": 1,
        "direction_id": str(raw.get("direction_id") or f"d{round_index:03d}")[:80],
        "title": str(raw.get("title") or raw.get("hypothesis") or "Bounded improvement direction")[:200],
        "strategy_type": str(raw.get("strategy_type") or "repair_rule")[:80],
        "hypothesis": str(raw.get("hypothesis") or "Make one bounded change and measure it with Core.")[:1200],
        "worker_objective": str(raw.get("worker_objective") or raw.get("hypothesis") or "")[:1200],
        "diagnosis": str(raw.get("diagnosis") or raw.get("problem_diagnosis") or "")[:1600],
        "experiment_stage": normalize_experiment_stage(raw.get("experiment_stage"), round_index=round_index),
        "method_family": method_families[0]["id"] if method_families else str(raw.get("method_family") or "")[:160],
        "method_families": method_families,
        "knowledge_query": _strings(raw.get("knowledge_query"), limit=8),
        "observed_shortcomings": _strings(raw.get("observed_shortcomings"), limit=10),
        "reasoning_trace": reasoning_trace,
        "incumbent_assessment": incumbent_assessment,
        "evidence_summary": _strings(raw.get("evidence_summary"), limit=10),
        "direction_judgment": str(raw.get("direction_judgment") or "")[:2000],
        "alternatives_considered": _strings(raw.get("alternatives_considered"), limit=6),
        "selection_rationale": str(raw.get("selection_rationale") or "")[:1600],
        "preserve": _strings(raw.get("preserve"), limit=10),
        "change_scope": _strings(raw.get("change_scope"), limit=8),
        "next_mutation": next_mutation,
        "implementation_order": _strings(raw.get("implementation_order"), limit=32),
        "deliverables": [
            dict(item)
            for item in raw.get("deliverables") or []
            if isinstance(item, dict)
        ][:32],
        "avoid": _strings(raw.get("avoid"), limit=10),
        "knowledge_paths": _strings(raw.get("knowledge_paths"), limit=12),
        "method_package_id": str(raw.get("method_package_id") or "")[:120],
        "acceptance_checks": _strings(raw.get("acceptance_checks"), limit=10),
        "activation_checks": normalize_activation_checks(raw.get("activation_checks")),
        "activation_contract_version": _positive_int(
            raw.get("activation_contract_version"),
            default=1,
            allow_zero=True,
        ),
        "stop_conditions": _strings(raw.get("stop_conditions"), limit=8),
        "completion_rule": str(raw.get("completion_rule") or "")[:1200],
        "candidate_variants": normalize_candidate_variants(raw.get("candidate_variants")),
        "planner": str(raw.get("planner") or "unknown")[:80],
    }
    if not plan["change_scope"]:
        plan["change_scope"] = (
            [next_mutation["change"]]
            if next_mutation.get("change")
            else ["Modify one coherent rule or operator around the incumbent."]
        )
    if not plan["acceptance_checks"]:
        plan["acceptance_checks"] = ["Pass deterministic preflight and fixed evaluator comparison."]
    if not plan["selection_rationale"]:
        plan["selection_rationale"] = "This is the smallest coherent direction supported by the current evidence."
    if not plan["direction_judgment"]:
        plan["direction_judgment"] = plan["selection_rationale"]
    return plan


def normalize_candidate_variants(value: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    """Normalize independent Coding Worker experiments proposed by Main."""

    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        candidate_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(item.get("candidate_id") or f"c{index:02d}"))
        candidate_id = candidate_id.strip("-")[:48] or f"c{index:02d}"
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        method_families = normalize_method_families(item.get("method_families"), item.get("method_family"))
        variant = {
            "candidate_id": candidate_id,
            "title": str(item.get("title") or item.get("hypothesis") or candidate_id)[:200],
            "hypothesis": str(item.get("hypothesis") or "")[:1200],
            "worker_objective": str(item.get("worker_objective") or item.get("hypothesis") or "")[:1200],
            "strategy_type": str(item.get("strategy_type") or "bounded_variant")[:80],
            "method_family": method_families[0]["id"] if method_families else str(item.get("method_family") or "")[:160],
            "method_families": method_families,
            "method_package_id": str(item.get("method_package_id") or "")[:120],
            "knowledge_query": _strings(item.get("knowledge_query"), limit=8),
            "experiment_stage": normalize_experiment_stage(item.get("experiment_stage"), round_index=0),
            "change_scope": _strings(item.get("change_scope"), limit=8),
            "next_mutation": _normalize_next_mutation(item.get("next_mutation")),
            "implementation_order": _strings(item.get("implementation_order"), limit=16),
            "deliverables": [
                dict(row)
                for row in item.get("deliverables") or []
                if isinstance(row, dict)
            ][:16],
            "preserve": _strings(item.get("preserve"), limit=10),
            "avoid": _strings(item.get("avoid"), limit=10),
            "acceptance_checks": _strings(item.get("acceptance_checks"), limit=10),
            "activation_checks": normalize_activation_checks(item.get("activation_checks")),
            "completion_rule": str(item.get("completion_rule") or "")[:1200],
        }
        if not variant["hypothesis"] or not variant["next_mutation"].get("change"):
            continue
        result.append(variant)
        if len(result) >= max(1, min(4, limit)):
            break
    return result


def normalize_method_families(value: Any, legacy_primary: Any = None, *, limit: int = 4) -> list[dict[str, str]]:
    """Keep ordered canonical family IDs while accepting legacy single-family plans."""

    rows = value if isinstance(value, list) else [legacy_primary] if legacy_primary else []
    result: list[dict[str, str]] = []
    for item in rows:
        family_id = str(
            (item.get("id") or item.get("family_id")) if isinstance(item, dict) else item or ""
        ).strip().lower()[:80]
        if not family_id or any(row["id"] == family_id for row in result):
            continue
        result.append({"id": family_id, "role": "primary" if not result else "complementary"})
        if len(result) >= max(1, min(4, int(limit))):
            break
    return result


def normalize_public_reasoning_trace(value: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    """保留可公开审计的研究摘要，不接收或暴露模型隐藏思维链。"""

    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        row = {
            "stage": str(item.get("stage") or f"分析步骤 {index + 1}")[:80],
            "summary": str(item.get("summary") or "")[:1600],
            "evidence": _strings(item.get("evidence"), limit=8),
            "inference": str(item.get("inference") or "")[:1200],
            "decision": str(item.get("decision") or "")[:1200],
            "next_check": str(item.get("next_check") or "")[:1000],
        }
        if row["summary"] and (row["evidence"] or row["inference"] or row["decision"]):
            result.append(row)
        if len(result) >= limit:
            break
    return result


def merge_public_reasoning_traces(*values: Any, limit: int = 12) -> list[dict[str, Any]]:
    """按阶段合并两次 Main 调用的公开研究日志并去重。"""

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        for row in normalize_public_reasoning_trace(value, limit=limit):
            key = (str(row.get("stage") or ""), str(row.get("summary") or ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
            if len(result) >= limit:
                return result
    return result


def normalize_incumbent_assessment(value: Any) -> dict[str, list[str]]:
    raw = value if isinstance(value, dict) else {}
    return {
        "verified_capabilities": _strings(raw.get("verified_capabilities"), limit=12),
        "implementation_limits": _strings(raw.get("implementation_limits"), limit=12),
        "bottleneck_hypotheses": _strings(raw.get("bottleneck_hypotheses"), limit=8),
        "evidence_refs": _strings(raw.get("evidence_refs"), limit=16),
        "unknowns": _strings(raw.get("unknowns"), limit=8),
    }


def _normalize_next_mutation(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "target_symbols": _strings(raw.get("target_symbols"), limit=16),
        "change": str(raw.get("change") or "")[:1600],
        "preserve": _strings(raw.get("preserve"), limit=12),
        "expected_effect": str(raw.get("expected_effect") or "")[:1200],
        "falsification_metrics": _strings(raw.get("falsification_metrics"), limit=10),
    }


def normalize_experiment_stage(value: Any, *, round_index: int) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    allowed = {"baseline", "probe", "scale", "pivot", "research_tournament"}
    if normalized in allowed:
        return normalized
    return "baseline" if round_index < 0 else "probe"


def _positive_int(value: Any, *, default: int, allow_zero: bool = False) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(0 if allow_zero else 1, parsed)


def normalize_activation_checks(value: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    """Normalize machine-checkable proof that a proposed mechanism actually ran."""

    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    allowed_operators = {"exists", "truthy", "eq", "ne", "gt", "gte", "lt", "lte", "contains"}
    for item in rows:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("telemetry_path") or "").strip()[:300]
        operator = str(item.get("operator") or "exists").strip().lower()
        if not path or operator not in allowed_operators:
            continue
        result.append(
            {
                "id": str(item.get("id") or f"activation_{len(result) + 1}")[:80],
                "path": path,
                "operator": operator,
                "expected": item.get("expected", item.get("value")),
                "required": item.get("required") is not False,
                "aggregation": (
                    str(item.get("aggregation") or "any").strip().lower()
                    if str(item.get("aggregation") or "any").strip().lower() in {"any", "all", "min_passes"}
                    else "any"
                ),
                "min_passes": _positive_int(item.get("min_passes"), default=1),
                "description": str(item.get("description") or "")[:500],
            }
        )
        if len(result) >= max(1, min(12, limit)):
            break
    return result


def activation_check_schema_errors(
    value: Any,
    *,
    field_name: str = "activation_checks",
) -> list[str]:
    """Validate the declared machine-checkable activation schema."""

    rows = value if isinstance(value, list) else []
    errors: list[str] = []
    allowed_operators = {"exists", "truthy", "eq", "ne", "gt", "gte", "lt", "lte", "contains"}
    expected_required = {"eq", "ne", "gt", "gte", "lt", "lte", "contains"}
    allowed_aggregations = {"any", "all", "min_passes"}
    for index, item in enumerate(rows):
        prefix = f"{field_name}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        path = str(item.get("path") or item.get("telemetry_path") or "").strip()
        if not path:
            errors.append(f"{prefix}.path is empty")
        operator = str(item.get("operator") or "exists").strip().lower()
        if operator not in allowed_operators:
            errors.append(f"{prefix}.operator is invalid")
        elif operator in expected_required and "expected" not in item and "value" not in item:
            errors.append(f"{prefix}.expected is required for operator {operator}")
        aggregation = str(item.get("aggregation") or "any").strip().lower()
        if aggregation not in allowed_aggregations:
            errors.append(f"{prefix}.aggregation is invalid")
        if aggregation == "min_passes":
            try:
                min_passes = int(item.get("min_passes"))
            except (TypeError, ValueError):
                min_passes = 0
            if min_passes <= 0:
                errors.append(f"{prefix}.min_passes must be a positive integer when aggregation=min_passes")
    return errors


def default_direction_knowledge_query(
    *,
    instance_diagnostics: Any,
    method_families: Any,
    fallback: list[str],
    limit: int = 6,
) -> list[str]:
    """Prefer high-flexibility query tags when the parsed instance profile supports them."""

    compatible_tags: list[str] = []
    for item in method_families or []:
        family_id = str((item.get("id") if isinstance(item, dict) else item) or "").strip().lower()
        if family_id == "constructive_search":
            compatible_tags.extend(["high_flexibility", "assignment_regret", "idle_gap"])
        elif family_id == "coupled_local_search":
            compatible_tags.extend(
                ["high_flexibility", "assignment_regret", "assignment_trust_region", "order_preserving_redecode"]
            )
    preferred = high_flexibility_query_tags(
        instance_diagnostics,
        compatible_tags=compatible_tags,
        limit=limit,
    )
    if preferred:
        return preferred
    return _strings(fallback, limit=limit)


def high_flexibility_query_tags(
    instance_diagnostics: Any,
    *,
    compatible_tags: list[str] | set[str] | None = None,
    limit: int = 4,
) -> list[str]:
    """Return canonical high-flexibility query tags for compatible families only."""

    diagnostics = instance_diagnostics if isinstance(instance_diagnostics, dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    avg_candidates = float(summary.get("avg_candidate_count", 0.0) or 0.0)
    flexible_ratio = float(summary.get("avg_flexible_operation_ratio", 0.0) or 0.0)
    if not (avg_candidates >= 3.0 and flexible_ratio >= 0.5):
        return []
    preferred = [
        "high_flexibility",
        "assignment_regret",
        "assignment_trust_region",
        "order_preserving_redecode",
        "idle_gap",
    ]
    allowed = {str(item).strip().lower() for item in compatible_tags or [] if str(item).strip()}
    result: list[str] = []
    for tag in preferred:
        if allowed and tag not in allowed:
            continue
        result.append(tag)
        if len(result) >= max(1, limit):
            break
    return result


def normalize_round_reflection(value: Any, *, request: RoundReflectionRequest) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    allowed = {"supported", "refuted", "mixed", "inconclusive_not_exercised", "inconclusive"}
    outcome = str(raw.get("hypothesis_outcome") or raw.get("status") or "").strip().lower()
    if outcome not in allowed:
        return deterministic_round_reflection(request)
    findings = []
    for item in raw.get("candidate_findings") or []:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "candidate_id": str(item.get("candidate_id") or "")[:80],
                "outcome": str(item.get("outcome") or "")[:80],
                "evidence": _strings(item.get("evidence"), limit=8),
                "causal_interpretation": str(item.get("causal_interpretation") or "")[:1200],
            }
        )
    next_action = raw.get("next_action") if isinstance(raw.get("next_action"), dict) else {}
    return {
        "schema_version": 1,
        "round_index": request.round_index,
        "hypothesis_outcome": outcome,
        "summary": str(raw.get("summary") or "")[:2000],
        "candidate_findings": findings[:4],
        "next_action": {
            "action": normalize_experiment_stage(next_action.get("action"), round_index=request.round_index),
            "rationale": str(next_action.get("rationale") or "")[:1600],
            "required_activation_checks": normalize_activation_checks(
                next_action.get("required_activation_checks")
            ),
        },
        "reasoning_trace": normalize_public_reasoning_trace(raw.get("reasoning_trace"), limit=6),
    }


def deterministic_round_reflection(request: RoundReflectionRequest) -> dict[str, Any]:
    candidates = [
        item for item in request.competition_result.get("candidates") or [] if isinstance(item, dict)
    ]
    findings: list[dict[str, Any]] = []
    measured_candidate_count = 0
    inconclusive_candidate_count = 0
    for candidate in candidates[:4]:
        activation = candidate.get("mechanism_activation") if isinstance(candidate.get("mechanism_activation"), dict) else {}
        activation_required = candidate.get("activation_required") is True
        activated = (
            activation.get("passed") is True
            if activation_required
            else activation.get("passed") is not False
        )
        if activated:
            measured_candidate_count += 1
        else:
            inconclusive_candidate_count += 1
        findings.append(
            {
                "candidate_id": str(candidate.get("candidate_id") or "")[:80],
                "outcome": "measured" if activated else "inconclusive_not_exercised",
                "evidence": [
                    f"objective_key={candidate.get('objective_key')}",
                    f"mechanism_activation={activation.get('status') or 'not_declared'}",
                ],
                "causal_interpretation": (
                    "机制激活检查未通过，当前结果不能用于否定算法假设。"
                    if not activated
                    else "候选机制已被执行，目标结果可用于更新该方向的证据。"
                ),
            }
        )
    promoted = bool(request.promotion_check.get("promoted"))
    no_measured_candidate = measured_candidate_count == 0
    outcome = "supported" if promoted else "inconclusive_not_exercised" if no_measured_candidate else "refuted"
    action = "scale" if promoted else "probe" if no_measured_candidate else "pivot"
    return {
        "schema_version": 1,
        "round_index": request.round_index,
        "hypothesis_outcome": outcome,
        "summary": (
            "候选严格提升并通过晋级检查。"
            if promoted
            else "没有候选真正触达声明机制，不能把无提升解释为算法无效。"
            if no_measured_candidate
            else (
                "至少一个候选机制已执行但没有严格提升，应更新主要假设或方法层级；"
                f"另有 {inconclusive_candidate_count} 个候选保持为未执行证据。"
            )
        ),
        "candidate_findings": findings,
        "next_action": {
            "action": action,
            "rationale": "根据机制激活与固定 evaluator 的联合证据决定下一轮范围。",
            "required_activation_checks": [],
        },
        "reasoning_trace": [],
    }


def write_round_reflection(output_dir: Path, reflection: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "round_reflection.json").write_text(
        json.dumps(reflection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return reflection


def _fallback_incumbent_assessment(context: dict[str, Any]) -> dict[str, list[str]]:
    audit = (
        context.get("incumbent_capability_audit")
        if isinstance(context.get("incumbent_capability_audit"), dict)
        else {}
    )
    verified: list[str] = []
    limits: list[str] = []
    refs: list[str] = []
    for file_report in audit.get("files") or []:
        if not isinstance(file_report, dict) or file_report.get("parse_status") != "ok":
            continue
        path = str(file_report.get("relative_path") or "incumbent")
        for item in (file_report.get("functions") or [])[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("qualified_name") or "")
            line = item.get("line")
            if name:
                verified.append(f"已检测到可达源码符号 `{name}`。")
                refs.append(f"{path}:{line} {name}")
        for item in (file_report.get("configurations") or [])[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            expression = str(item.get("expression") or "")
            line = item.get("line")
            if name:
                limits.append(f"搜索控制 `{name}` 当前表达式为 `{expression}`。")
                refs.append(f"{path}:{line} {name}")
    return {
        "verified_capabilities": _strings(verified, limit=12),
        "implementation_limits": _strings(limits, limit=12),
        "bottleneck_hypotheses": [],
        "evidence_refs": _strings(refs, limit=16),
        "unknowns": _strings(audit.get("limitations"), limit=8),
    }


def fallback_improvement_order(
    *,
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
    round_index: int,
) -> list[str]:
    """Choose one package-declared fallback component without algorithm knowledge.

    Model planning remains preferred. This path exists for provider timeout or
    malformed output, where repeating the complete Method Package would turn an
    improvement round into an expensive baseline rewrite.
    """

    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    recommended = str(catalog.get("recommended_package_id") or "")
    package = next(
        (
            item
            for item in catalog.get("packages") or []
            if isinstance(item, dict) and str(item.get("package_id") or "") == recommended
        ),
        {},
    )
    contract = package.get("implementation_contract") if isinstance(package.get("implementation_contract"), dict) else {}
    declared = _strings(contract.get("fallback_improvement_order"), limit=32)
    component_ids = {
        str(item.get("component_id") or "")
        for item in contract.get("required_components") or []
        if isinstance(item, dict) and str(item.get("component_id") or "").strip()
    }
    declared = [component_id for component_id in declared if component_id in component_ids]
    if not declared:
        declared = list(component_ids)
    if not declared:
        return []

    used: set[str] = set()
    for previous in loop_feedback.get("previous_rounds") or []:
        if not isinstance(previous, dict):
            continue
        direction = previous.get("direction_plan") if isinstance(previous.get("direction_plan"), dict) else {}
        used.update(_strings(direction.get("implementation_order"), limit=32))
    remaining = [component_id for component_id in declared if component_id not in used]
    if remaining:
        return remaining[:1]
    return [declared[max(0, round_index) % len(declared)]]


def bind_direction_plan_to_method_catalog(
    plan: dict[str, Any],
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """把模型请求的方法包约束到当前问题特征允许的 catalog 内。"""

    catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    packages = [item for item in catalog.get("packages") or [] if isinstance(item, dict)]
    available = {
        str(item.get("package_id") or "").strip(): item
        for item in packages
        if str(item.get("package_id") or "").strip()
    }
    requested = str(plan.get("method_package_id") or "").strip()
    selected = requested if requested in available else ""
    plan["method_package_id"] = selected
    plan["method_package_selection"] = {
        "requested": requested or None,
        "selected": selected or None,
        "fallback_used": bool(requested and requested != selected),
        "available_package_ids": list(available),
    }
    if selected:
        selected_package = available[selected]
        package_assets = [str(item) for item in selected_package.get("assets") or [] if str(item).strip()]
        plan["knowledge_paths"] = _strings([*package_assets, *plan.get("knowledge_paths", [])], limit=12)
        implementation_bundle = method_implementation_bundle(selected_package)
        if implementation_bundle:
            contract_paths = [
                str(item) for item in implementation_bundle.get("contract_paths") or [] if str(item).strip()
            ]
            plan["implementation_bundle"] = implementation_bundle
            contract_paths = list(dict.fromkeys(contract_paths))
            supplemental_limit = max(0, 12 - len(contract_paths))
            supplemental_paths = (
                _strings(
                    [*package_assets, *plan.get("knowledge_paths", [])],
                    limit=supplemental_limit,
                )
                if supplemental_limit
                else []
            )
            plan["knowledge_paths"] = [*contract_paths, *supplemental_paths]
            component_ids = [
                str(item.get("component_id") or "")
                for item in implementation_bundle.get("required_components") or []
                if isinstance(item, dict) and str(item.get("component_id") or "").strip()
            ]
            requested_order = _strings(plan.get("implementation_order"), limit=32)
            requested_order = [component_id for component_id in requested_order if component_id in component_ids]
            baseline_direction = str(plan.get("strategy_type") or "") == "baseline_constructor"
            if baseline_direction:
                selected_component_ids = requested_order or component_ids
                scope_prefix = (
                    "Implement and verify the complete selected method bundle in one coherent direction: "
                    + ", ".join(component_ids)
                )
            else:
                package_fallback = [
                    component_id
                    for component_id in _strings(implementation_bundle.get("fallback_improvement_order"), limit=32)
                    if component_id in component_ids
                ]
                selected_component_ids = requested_order or package_fallback[:1] or component_ids[:1]
                scope_prefix = (
                    "Change only the selected incremental method component(s) while preserving the complete incumbent: "
                    + ", ".join(selected_component_ids)
                )
            plan["implementation_order"] = selected_component_ids
            plan["change_scope"] = _strings(
                [scope_prefix, *(plan.get("change_scope") or [])],
                limit=8,
            )
            declared_deliverables = [
                item
                for item in plan.get("deliverables") or []
                if isinstance(item, dict)
                and str(item.get("id") or item.get("component_id") or "") in selected_component_ids
            ]
            if declared_deliverables:
                plan["deliverables"] = declared_deliverables
            else:
                plan["deliverables"] = [
                    {
                        "id": str(item.get("component_id") or "")[:160],
                        "behavior": str(
                            item.get("title")
                            or item.get("description")
                            or item.get("component_id")
                            or "required method component"
                        )[:600],
                        "evidence_required": "Reachable source and bounded behavioral evidence.",
                    }
                    for item in implementation_bundle.get("required_components") or []
                    if isinstance(item, dict)
                    and str(item.get("component_id") or "").strip() in selected_component_ids
                ]
            plan["acceptance_checks"] = _strings(
                [
                    (
                        "Every required component in implementation_bundle must have reachable source evidence; partial package implementation is not complete."
                        if baseline_direction
                        else "Every selected incremental component must have reachable source evidence while all unselected incumbent components remain intact."
                    ),
                    "All coupled_groups must remain behaviorally closed across generation, scoring, application, memory, and search control.",
                    *(plan.get("acceptance_checks") or []),
                ],
                limit=10,
            )
    return plan


def request_worker_assignment(
    planner: DirectionPlanningAgent,
    request: WorkerAssignmentRequest,
) -> WorkerAssignmentIssue:
    """统一调用 Main Agent 的签发接口，并为旧测试替身提供确定性兼容。"""

    method_name = "revise_worker_assignment" if request.attempt_index > 0 else "issue_worker_assignment"
    method = getattr(planner, method_name, None)
    if callable(method):
        issue = method(request)
    else:
        issue = _compile_worker_assignment(request, require_parent=request.attempt_index > 0)
    _validate_assignment_issue(issue, request=request)
    return issue


def _compile_worker_assignment(
    request: WorkerAssignmentRequest,
    *,
    require_parent: bool = False,
) -> WorkerAssignmentIssue:
    """把 Main 方向确定性编译为最小任务书，并锁定同方向修补 lineage。"""

    parent: WorkerAssignment | None = None
    if request.parent_assignment_path is not None:
        parent = WorkerAssignment.load(request.parent_assignment_path)
    if require_parent and parent is None:
        raise ValueError("repair assignment requires a parent assignment")

    context = load_context_dict(request.context_packet_path)
    assignment = build_worker_assignment(
        context=context,
        direction_plan=request.direction_plan,
        loop_feedback=request.loop_feedback,
        round_index=request.round_index,
        attempt_index=request.attempt_index,
        max_steps=request.max_steps,
        max_runtime_seconds=request.max_runtime_seconds,
        parent_assignment_id=parent.assignment_id if parent else None,
    )
    if parent is not None:
        _validate_assignment_revision(parent=parent, revision=assignment)

    filename = (
        "worker_assignment.json"
        if request.attempt_index == 0
        else f"assignment_revision_{request.attempt_index:03d}.json"
    )
    artifact_path = write_worker_assignment(request.output_dir / filename, assignment).resolve()
    return WorkerAssignmentIssue(assignment=assignment, artifact_path=artifact_path)


def _validate_assignment_revision(*, parent: WorkerAssignment, revision: WorkerAssignment) -> None:
    """Repair 只能收敛同一任务，不得更换方向、方法包或 solver 入口。"""

    if revision.direction_id != parent.direction_id:
        raise ValueError("repair assignment cannot change direction_id")
    parent_package = str(parent.method_package.get("package_id") or "")
    revision_package = str(revision.method_package.get("package_id") or "")
    if revision_package != parent_package:
        raise ValueError("repair assignment cannot change method_package.package_id")
    if revision.target_file != parent.target_file:
        raise ValueError("repair assignment cannot change target_file")
    parent_skills = [str(item.get("skill_id") or "") for item in parent.implementation_skills]
    revision_skills = [str(item.get("skill_id") or "") for item in revision.implementation_skills]
    staged_baseline_revision = (
        int(parent.lineage.get("round_index", 0) or 0) == -1
        and int(revision.lineage.get("round_index", 0) or 0) == -1
        and int(revision.lineage.get("baseline_trial", 0) or 0)
        == int(parent.lineage.get("baseline_trial", 0) or 0) + 1
    )
    if revision_skills != parent_skills and not staged_baseline_revision:
        raise ValueError("repair assignment cannot change implementation_skills")
    if revision.lineage.get("parent_assignment_id") != parent.assignment_id:
        raise ValueError("repair assignment must reference its parent assignment")


def _validate_assignment_issue(
    issue: WorkerAssignmentIssue,
    *,
    request: WorkerAssignmentRequest,
) -> None:
    errors = issue.assignment.validate()
    if errors:
        raise ValueError("Main Agent issued an invalid worker assignment: " + "; ".join(errors))
    if issue.assignment.direction_id != str(request.direction_plan.get("direction_id") or ""):
        raise ValueError("Main Agent assignment does not match the planned direction")
    if request.parent_assignment_path is not None:
        parent = WorkerAssignment.load(request.parent_assignment_path)
        _validate_assignment_revision(parent=parent, revision=issue.assignment)
    if not issue.artifact_path.is_file():
        raise ValueError("Main Agent assignment artifact was not written")


def method_implementation_bundle(package: dict[str, Any]) -> dict[str, Any]:
    """把知识包契约原样绑定到方向计划；后端只处理通用组件 schema。"""

    contract = package.get("implementation_contract")
    if not isinstance(contract, dict):
        return {}
    components = [item for item in contract.get("required_components") or [] if isinstance(item, dict)]
    if not components:
        return {}
    return {
        "contract_id": str(contract.get("contract_id") or "")[:160],
        "contract_path": str(package.get("implementation_contract_asset") or ""),
        "contract_paths": [
            str(item)
            for item in package.get("implementation_contract_assets")
            or [package.get("implementation_contract_asset")]
            if str(item or "").strip()
        ],
        "mode": str(contract.get("mode") or "complete_method_package")[:80],
        "completion_rule": str(contract.get("completion_rule") or "")[:1200],
        "variant_rule": str(contract.get("variant_rule") or "")[:1200],
        "fallback_improvement_order": _strings(contract.get("fallback_improvement_order"), limit=32),
        # 完整性契约不能静默截断，否则后面的组件永远不会进入实现和审查。
        "required_components": components,
        "coupled_groups": [item for item in contract.get("coupled_groups") or [] if isinstance(item, dict)],
    }


def compact_main_agent_dynamic_context(
    *,
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
) -> str:
    """Keep planning-critical run state explicit instead of recursively compacting it away."""

    baseline_memory = (
        loop_feedback.get("agent_generated_baseline_memory")
        if isinstance(loop_feedback.get("agent_generated_baseline_memory"), dict)
        else {}
    )
    previous_rounds = []
    for value in (loop_feedback.get("previous_rounds") or [])[-8:]:
        if not isinstance(value, dict):
            continue
        direction = value.get("direction_plan") if isinstance(value.get("direction_plan"), dict) else {}
        semantic = value.get("semantic_review") if isinstance(value.get("semantic_review"), dict) else {}
        previous_rounds.append(
            {
                "round_index": value.get("round_index"),
                "decision": value.get("decision"),
                "candidate_key": value.get("candidate_key"),
                "incumbent_key_after": value.get("incumbent_key_after"),
                "title": direction.get("title"),
                "strategy_type": direction.get("strategy_type"),
                "hypothesis": direction.get("hypothesis"),
                "method_package_id": direction.get("method_package_id"),
                "implementation_order": _strings(direction.get("implementation_order"), limit=8),
                "failure_signatures": (value.get("failure_signatures") or [])[:8],
                "semantic_status": semantic.get("status"),
            }
        )
    experience = (
        loop_feedback.get("experience_memory")
        if isinstance(loop_feedback.get("experience_memory"), dict)
        else {}
    )
    memory_tiers = experience.get("memory_tiers") if isinstance(experience.get("memory_tiers"), dict) else {}
    payload = {
        "loop_feedback": {
            "round_index": loop_feedback.get("round_index"),
            "baseline_key": loop_feedback.get("baseline_key"),
            "incumbent_key_before": loop_feedback.get("incumbent_key_before"),
            "objective_key_order": loop_feedback.get("objective_key_order") or [],
            "agent_generated_baseline_memory": {
                "accepted_as_incumbent": baseline_memory.get("accepted_as_incumbent"),
                "baseline_key": baseline_memory.get("baseline_key"),
                "semantic_review": baseline_memory.get("semantic_review") or {},
                "best_core_valid_anchor": baseline_memory.get("best_core_valid_anchor") or {},
                "protection_rule": baseline_memory.get("protection_rule"),
            },
            "previous_rounds": previous_rounds,
            "failure_memory": loop_feedback.get("failure_memory") or {},
            "next_round_guidance": loop_feedback.get("next_round_guidance") or {},
            "user_intervention": loop_feedback.get("user_intervention") or {},
            "protected_promoted_facts": (loop_feedback.get("protected_promoted_facts") or [])[-8:],
            "validated_lessons": (memory_tiers.get("validated_lessons") or [])[-6:],
            "current_round_repair": loop_feedback.get("current_round_repair") or {},
            "instructions": loop_feedback.get("instructions") or [],
        },
        "incumbent_code_context": {
            "source": (context.get("incumbent_code_context") or {}).get("source"),
            "files": compact_source_records(
                (context.get("incumbent_code_context") or {}).get("files"),
                max_items=4,
                max_snippet_chars=2500,
            ),
        },
        "knowledge_cards": compact_source_records(
            context.get("knowledge_cards"),
            max_items=12,
            max_snippet_chars=500,
        ),
    }
    return compact_json(payload, max_chars=32_000).text


def enforce_improvement_direction_contract(
    plan: dict[str, Any],
    *,
    round_index: int,
    loop_feedback: dict[str, Any],
) -> dict[str, Any]:
    """Prevent a post-baseline planning response from discarding the incumbent."""

    # baseline 尚无 incumbent，可以选择完整构造方向；正式轮次则必须增量改进。
    if round_index < 0:
        return plan
    result = dict(plan)
    previous_rounds = [
        item for item in loop_feedback.get("previous_rounds") or [] if isinstance(item, dict)
    ]
    research_state = project_research_state(
        previous_rounds,
        next_round_guidance=loop_feedback.get("next_round_guidance"),
        user_intervention=loop_feedback.get("user_intervention"),
    )
    if previous_rounds and research_state.get("method_family_policy") == "inherit":
        latest_plan = (
            previous_rounds[-1].get("direction_plan")
            if isinstance(previous_rounds[-1].get("direction_plan"), dict)
            else {}
        )
        for field in ("method_family", "method_families", "knowledge_query"):
            if latest_plan.get(field) not in (None, "", [], {}):
                result[field] = latest_plan[field]
    state_stage = str(research_state.get("experiment_stage") or "").strip()
    if previous_rounds and state_stage in {"probe", "scale", "pivot", "research_tournament"}:
        result["experiment_stage"] = state_stage
    result["research_transition"] = {
        key: research_state.get(key)
        for key in (
            "planning_mode",
            "selection_required",
            "selection_reason",
            "method_family_policy",
            "requested_next_action",
            "next_action",
            "transition_adjustment",
        )
    }
    required_preserve = (
        "Preserve the promoted incumbent parser, operation representation, constructor, decoder, output schema, "
        "and semantically validated search mechanisms."
    )
    result["preserve"] = _strings([required_preserve, *(result.get("preserve") or [])], limit=10)
    result["avoid"] = _strings(
        [
            "Do not replace the promoted solver or restart from a baseline constructor.",
            *(result.get("avoid") or []),
        ],
        limit=10,
    )
    if str(result.get("strategy_type") or "") == "baseline_constructor":
        guidance = (
            loop_feedback.get("next_round_guidance")
            if isinstance(loop_feedback.get("next_round_guidance"), dict)
            else {}
        )
        bounded_scope = _strings(guidance.get("must_do"), limit=2)
        result["title"] = "Incrementally refine the promoted incumbent"
        result["strategy_type"] = "local_search_operator"
        result["hypothesis"] = (
            "A bounded operator-level mutation of the promoted incumbent can improve the declared objective while "
            "preserving its evaluator- and semantic-review-backed mechanisms."
        )
        result["change_scope"] = bounded_scope or [
            "Make one bounded operator-level refinement around the promoted incumbent."
        ]
    incumbent_key = loop_feedback.get("incumbent_key_before")
    result["acceptance_checks"] = _strings(
        [
            *(result.get("acceptance_checks") or []),
            f"Candidate must be strictly better than incumbent objective key {incumbent_key} before promotion.",
            "Algorithm semantic review must pass before promotion.",
        ],
        limit=10,
    )
    return result


def write_direction_plan(output_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """先持久化方向计划，再把 artifact_path 附加给下游审计。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    persisted = dict(plan)
    reasoning_trace = normalize_public_reasoning_trace(persisted.get("reasoning_trace"), limit=12)
    if reasoning_trace:
        reasoning_trace_path = output_dir / "main_reasoning_trace.json"
        reasoning_trace_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "direction_id": persisted.get("direction_id"),
                    "entries": reasoning_trace,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        persisted["reasoning_trace"] = reasoning_trace
        planning_evidence = (
            dict(persisted.get("planning_evidence"))
            if isinstance(persisted.get("planning_evidence"), dict)
            else {}
        )
        planning_evidence["reasoning_trace_path"] = str(reasoning_trace_path.resolve())
        persisted["planning_evidence"] = planning_evidence
    path = output_dir / "direction_plan.json"
    path.write_text(json.dumps(persisted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = dict(persisted)
    result["artifact_path"] = str(path.resolve())
    return result


def _direction_prompt(*, round_index: int, stable_context: str, dynamic_context: str) -> str:
    phase_rule = (
        "This is agent-generated baseline planning. Select one complete method package and plan its adaptation "
        "to the active IO/CLI; there is no promoted incumbent yet."
        if round_index < 0
        else "Preserve the current promoted incumbent unless evaluator evidence identifies it as the failure source."
    )
    return f"""
Plan improvement direction {round_index} for the current algorithm-evolution run.

Return JSON only:
{{
  "direction_id": "d{round_index:03d}",
  "title": "short method-level title",
  "strategy_type": "baseline_constructor | dispatch_rule | local_search_operator | repair_rule | parameter_policy | path_selection",
  "hypothesis": "why this one change should improve the declared objective",
  "diagnosis": "evidence-backed reason the incumbent is limited or invalid",
  "observed_shortcomings": ["specific missing, weak, or misleading behaviors in the current solver"],
  "reasoning_trace": [{{
    "stage": "结构观察 | 瓶颈假设 | 方案比较 | 验证计划 | 方向结论",
    "summary": "public engineering summary of this step",
    "evidence": ["exact audit, evaluator, semantic, or history facts"],
    "inference": "bounded inference from those facts",
    "decision": "decision made at this step",
    "next_check": "evidence needed next"
  }}],
  "incumbent_assessment": {{
    "verified_capabilities": ["mechanisms supported by static audit or evaluator evidence"],
    "implementation_limits": ["specific parameter, coverage, reachability, or budget limitations"],
    "bottleneck_hypotheses": ["falsifiable explanation for the current objective gap"],
    "evidence_refs": ["artifact path, symbol, line, metric, or audit field"],
    "unknowns": ["facts not established by the available evidence"]
  }},
  "evidence_summary": ["Core, JA, semantic-review, or source evidence supporting the diagnosis"],
  "direction_judgment": "detailed judgment connecting evidence and shortcomings to the selected next direction",
  "alternatives_considered": ["plausible direction not selected and why"],
  "selection_rationale": "why this method and scope are preferred now",
  "method_family": "primary canonical family id (compatibility field)",
  "method_families": [
    {{"id": "canonical family id from method_family_catalog", "role": "primary"}},
    {{"id": "optional compatible family id", "role": "complementary"}}
  ],
  "knowledge_query": ["2-6 tags for second-stage detailed knowledge retrieval"],
  "method_package_id": "exact enabled package_id, or empty when no exact package is enabled",
  "preserve": ["promoted mechanisms that must remain"],
  "change_scope": ["one coherent method direction; when a package contract exists it covers the complete component bundle"],
  "next_mutation": {{
    "target_symbols": ["existing symbol or configuration to change"],
    "change": "one bounded mutation of the incumbent",
    "preserve": ["verified incumbent behavior that must remain"],
    "expected_effect": "why this mutation addresses the bottleneck hypothesis",
    "falsification_metrics": ["measurements that can reject the hypothesis"]
  }},
  "implementation_order": ["required component ids in dependency order"],
  "deliverables": [{{"id": "component id", "behavior": "observable behavior", "evidence_required": "proof"}}],
  "avoid": ["failed or unsupported patterns not to repeat"],
  "knowledge_paths": ["only cards that directly support this direction"],
  "acceptance_checks": ["code/evaluator evidence required before success"],
  "stop_conditions": ["when coding repair should stop"],
  "completion_rule": "all coupled deliverables and checks required before completion"
}}

Rules:
- Do not write source code or patch instructions.
- Choose one coherent direction. It may compose up to three compatible method families when the evidence requires
  multiple mechanisms; do not add a family merely because it is available.
- Give a detailed evidence-backed account of current shortcomings and the direction judgment so a user can review or override it between rounds.
- reasoning_trace is a concise public research journal, not hidden chain-of-thought. For improvement rounds include at least
  three entries covering incumbent observation, bottleneck/alternative comparison, and next-mutation validation. Every entry
  must cite available evidence, state a bounded inference, and name a decision or next check. Never claim to have run a command
  or experiment that is not present in the attached evidence.
- For improvement rounds, treat incumbent_capability_audit as the source of truth about existing symbols and search-control settings.
  Distinguish a missing mechanism from an implemented-but-weak mechanism. If the audit shows a mechanism or call path,
  do not ask the Worker to reimplement it; identify the concrete scale, coverage, reachability, or budget limitation instead.
- Cite exact audit evidence such as `relative_path:line`, symbol names, configuration expressions, loop controls, and call edges.
  Static evidence is not runtime proof: state runtime quality claims as bottleneck_hypotheses and provide falsification_metrics.
- next_mutation must target existing audited symbols/configurations when round_index >= 0 and must explain why this is the next
  useful mutation rather than a repetition of an already implemented method label.
- If user_intervention is present, honor it as controlling intent unless it conflicts with hard legality, evaluator, or package constraints; explain any reconciliation.
- First choose a method family from strategy_selection_cards and return a focused knowledge_query for second-stage retrieval.
- Select a Method Package only when the enabled method_package_catalog contains an exact compatible package. Do not infer or
  revive a disabled package. An empty method_package_id is valid.
- Treat knowledge cards, reference source, recommended build order, and incremental staging as advisory. They may support a
  bounded operator, a hybrid composition, or a coherent complete-method adaptation; do not infer that a recommendation to
  stage work incrementally prohibits selecting the complete method. Never require mechanical source copying.
- When a package is selected, read its implementation_contract and cover complete required_components and coupled_groups.
  Without a package, define explicit observable deliverables and let second-stage retrieved cards provide implementation detail.
- Same-direction repairs may focus on missing/partial components, but the direction is complete only when every required
  component has reachable evidence.
- Prefer the recommended package unless task evidence makes another compatible package more appropriate.
- {phase_rule}
- For round_index >= 0, never return strategy_type=baseline_constructor. Read incumbent_key_before and previous_rounds,
  preserve the promoted incumbent, and choose a materially different bounded refinement after a rollback.
- Treat agent_generated_baseline_memory.best_core_valid_anchor as a high-performing structure-preservation reference,
  not as promotion-eligible code when its semantic_status is blocking.
- Use only active requirement, IO, instance, evaluator, knowledge, and run-memory evidence.
- Do not use previous solution files, fixed schedules, or instance-specific target scores as method knowledge.
- If legality or representation is still broken, plan repair before objective tuning.
- A repair attempt must continue this direction instead of switching methods.
- All user-visible natural-language values must be written in Simplified Chinese. Keep JSON keys, IDs, paths, and code symbols unchanged.

Stable task context:
{stable_context}

Dynamic evaluator and knowledge context:
{dynamic_context}
""".strip()


def _strings(value: Any, *, limit: int) -> list[str]:
    if limit <= 0:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text[:600])
        if len(result) >= limit:
            break
    return result


def _merge_usage(first: dict[str, int] | None, second: dict[str, int] | None) -> dict[str, int]:
    keys = set(first or {}) | set(second or {})
    return {key: int((first or {}).get(key, 0) or 0) + int((second or {}).get(key, 0) or 0) for key in keys}
