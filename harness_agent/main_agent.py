from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .context_compaction import compact_json, compact_source_records, stable_worker_context_json
from .context_loader import load_context_dict
from .deepseek_client import DeepSeekClient, is_deepseek_configured


@dataclass(frozen=True)
class DirectionPlanRequest:
    round_index: int
    context_packet_path: Path
    loop_feedback: dict[str, Any]
    output_dir: Path


class DirectionPlanningAgent(Protocol):
    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        ...


class EvidenceDrivenMainAgent:
    """Deterministic fallback that turns evaluator evidence into one direction."""

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        context = load_context_dict(request.context_packet_path)
        guidance = request.loop_feedback.get("next_round_guidance")
        if not isinstance(guidance, dict):
            guidance = {}
        must_do = _strings(guidance.get("must_do"), limit=6)
        preserve = _strings(guidance.get("preserve"), limit=6)
        avoid = _strings(guidance.get("avoid"), limit=6)
        hypothesis = str(context.get("hypothesis") or "Improve the incumbent under the fixed evaluator.").strip()
        plan = normalize_direction_plan(
            {
                "direction_id": f"d{request.round_index:03d}",
                "title": must_do[0] if must_do else hypothesis[:160],
                "strategy_type": "repair_rule" if avoid else "local_search_operator",
                "hypothesis": hypothesis,
                "preserve": preserve,
                "change_scope": must_do[:3] or ["Make one bounded evaluator-checkable solver change."],
                "avoid": avoid,
                "knowledge_paths": list(context.get("auto_knowledge_cards") or [])[:8],
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
        dynamic_context = compact_json(
            {
                "loop_feedback": request.loop_feedback,
                "incumbent_code_context": context.get("incumbent_code_context") or {},
                "knowledge_cards": compact_source_records(
                    context.get("knowledge_cards"),
                    max_items=24,
                    max_snippet_chars=700,
                ),
            },
            max_chars=32_000,
        ).text
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
        try:
            raw = json.loads(response.content)
        except json.JSONDecodeError:
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "main_agent_invalid_response.txt").write_text(response.content, encoding="utf-8")
            return self.fallback.plan_direction(request)
        plan = normalize_direction_plan(raw, round_index=request.round_index)
        plan["planner"] = "deepseek_main_agent"
        request.output_dir.mkdir(parents=True, exist_ok=True)
        (request.output_dir / "main_agent_usage.json").write_text(
            json.dumps(
                {"usage": response.usage, "cache_hit_ratio": response.cache_hit_ratio},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return write_direction_plan(request.output_dir, plan)


def normalize_direction_plan(value: Any, *, round_index: int) -> dict[str, Any]:
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
        "acceptance_checks": _strings(raw.get("acceptance_checks"), limit=10),
        "stop_conditions": _strings(raw.get("stop_conditions"), limit=8),
        "planner": str(raw.get("planner") or "unknown")[:80],
    }
    if not plan["change_scope"]:
        plan["change_scope"] = ["Modify one coherent rule or operator around the incumbent."]
    if not plan["acceptance_checks"]:
        plan["acceptance_checks"] = ["Pass deterministic preflight and fixed evaluator comparison."]
    return plan


def write_direction_plan(output_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "direction_plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = dict(plan)
    result["artifact_path"] = str(path.resolve())
    return result


def _direction_prompt(*, round_index: int, stable_context: str, dynamic_context: str) -> str:
    return f"""
Plan improvement direction {round_index} for the current algorithm-evolution run.

Return JSON only:
{{
  "direction_id": "d{round_index:03d}",
  "title": "short method-level title",
  "strategy_type": "dispatch_rule | local_search_operator | repair_rule | parameter_policy | path_selection",
  "hypothesis": "why this one change should improve the declared objective",
  "preserve": ["promoted mechanisms that must remain"],
  "change_scope": ["one bounded implementation target"],
  "avoid": ["failed or unsupported patterns not to repeat"],
  "knowledge_paths": ["only cards that directly support this direction"],
  "acceptance_checks": ["code/evaluator evidence required before success"],
  "stop_conditions": ["when coding repair should stop"]
}}

Rules:
- Do not write source code or patch instructions.
- Choose exactly one coherent direction for the Coding Agent.
- Preserve the current promoted incumbent unless evaluator evidence identifies it as the failure source.
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
