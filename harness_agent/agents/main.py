"""Main Agent：基于文档、检索知识和历史证据规划下一条改进方向。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness_agent.context.compaction import compact_json, compact_source_records, stable_worker_context_json
from harness_agent.context.loader import load_context_dict
from harness_agent.deepseek_client import DeepSeekClient, is_deepseek_configured


@dataclass(frozen=True)
class DirectionPlanRequest:
    """Main Agent 单次规划输入：稳定任务上下文、动态证据和产物目录。"""

    round_index: int
    context_packet_path: Path
    loop_feedback: dict[str, Any]
    output_dir: Path


class DirectionPlanningAgent(Protocol):
    """方向规划协议；实现只能返回计划，不能直接修改 solver。"""

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
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
        hypothesis = str(context.get("hypothesis") or "Improve the incumbent under the fixed evaluator.").strip()
        baseline_generation = request.round_index < 0 or request.loop_feedback.get("round_type") == "agent_generated_baseline"
        plan = bind_direction_plan_to_method_catalog(normalize_direction_plan(
            {
                "direction_id": f"d{request.round_index:03d}",
                "title": must_do[0] if must_do else hypothesis[:160],
                "strategy_type": (
                    "baseline_constructor" if baseline_generation else "repair_rule" if avoid else "local_search_operator"
                ),
                "hypothesis": hypothesis,
                "preserve": preserve,
                "change_scope": must_do[:3] or ["Make one bounded evaluator-checkable solver change."],
                "avoid": avoid,
                "knowledge_paths": list(context.get("auto_knowledge_cards") or [])[:8],
                "method_package_id": (
                    (context.get("method_package_catalog") or {}).get("recommended_package_id")
                ),
                "acceptance_checks": [
                    "Candidate passes deterministic preflight and the fixed evaluator.",
                    "Candidate preserves complete legal output under the active task contract.",
                    "Candidate is strictly better than the incumbent before promotion.",
                ],
                "stop_conditions": [
                    "Stop this direction after its repair budget is exhausted.",
                    "Do not switch to an unrelated method inside a repair attempt.",
                ],
                "planner": "evidence_fallback",
            },
            round_index=request.round_index,
        ), context=context)
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        return write_direction_plan(request.output_dir, plan)


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
                        "Plan one experiment direction only. Do not write code. Return valid JSON only."
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


def normalize_direction_plan(value: Any, *, round_index: int) -> dict[str, Any]:
    """将模型自由 JSON 收敛为固定 DirectionPlan schema 和长度上限。"""

    raw = value if isinstance(value, dict) else {}
    plan = {
        "schema_version": 1,
        "direction_id": str(raw.get("direction_id") or f"d{round_index:03d}")[:80],
        "title": str(raw.get("title") or raw.get("hypothesis") or "Bounded improvement direction")[:200],
        "strategy_type": str(raw.get("strategy_type") or "repair_rule")[:80],
        "hypothesis": str(raw.get("hypothesis") or "Make one bounded change and measure it with Core.")[:1200],
        "preserve": _strings(raw.get("preserve"), limit=10),
        "change_scope": _strings(raw.get("change_scope"), limit=8),
        "avoid": _strings(raw.get("avoid"), limit=10),
        "knowledge_paths": _strings(raw.get("knowledge_paths"), limit=12),
        "method_package_id": str(raw.get("method_package_id") or "")[:120],
        "acceptance_checks": _strings(raw.get("acceptance_checks"), limit=10),
        "stop_conditions": _strings(raw.get("stop_conditions"), limit=8),
        "planner": str(raw.get("planner") or "unknown")[:80],
    }
    if not plan["change_scope"]:
        plan["change_scope"] = ["Modify one coherent rule or operator around the incumbent."]
    if not plan["acceptance_checks"]:
        plan["acceptance_checks"] = ["Pass deterministic preflight and fixed evaluator comparison."]
    return plan


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
    recommended = str(catalog.get("recommended_package_id") or "").strip()
    selected = requested if requested in available else recommended if recommended in available else ""
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
            plan["change_scope"] = _strings(
                [
                    (
                        "Implement and verify the complete selected method bundle in one coherent direction: "
                        + ", ".join(component_ids)
                    ),
                    *(plan.get("change_scope") or []),
                ],
                limit=8,
            )
            plan["acceptance_checks"] = _strings(
                [
                    "Every required component in implementation_bundle must have reachable source evidence; partial package implementation is not complete.",
                    "All coupled_groups must remain behaviorally closed across generation, scoring, application, memory, and search control.",
                    *(plan.get("acceptance_checks") or []),
                ],
                limit=10,
            )
    return plan


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
    path = output_dir / "direction_plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = dict(plan)
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
  "method_package_id": "exact package_id from method_package_catalog",
  "preserve": ["promoted mechanisms that must remain"],
  "change_scope": ["one coherent method direction; when a package contract exists it covers the complete component bundle"],
  "avoid": ["failed or unsupported patterns not to repeat"],
  "knowledge_paths": ["only cards that directly support this direction"],
  "acceptance_checks": ["code/evaluator evidence required before success"],
  "stop_conditions": ["when coding repair should stop"]
}}

Rules:
- Do not write source code or patch instructions.
- Choose exactly one coherent direction for the Coding Agent.
- Select exactly one compatible method package from method_package_catalog and keep that package for same-direction repairs.
- Read the selected package implementation_contract before planning. Your direction must cover its complete required_components
  and coupled_groups in one implementation bundle; do not ask the Coding Agent to implement only one convenient component.
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
