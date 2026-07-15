"""面向 Coding Worker 的上下文视图与优先级整理。"""

from __future__ import annotations

from typing import Any

from harness_agent.context.compaction import compact_json, stable_worker_context_json
from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract


WORKER_DYNAMIC_CONTEXT_MAX_CHARS = 48_000


def worker_context_sections(context: dict[str, Any]) -> dict[str, str]:
    """Return provider-neutral stable and dynamic worker context sections."""

    stable = stable_worker_context_json(context).text
    dynamic_payload = {
        "round_type": (
            "improvement_round"
            if (context.get("iteration_edit_contract") or {}).get("mode") == "incremental_after_baseline"
            else "baseline_or_single_round"
        ),
        "iteration_edit_contract": context.get("iteration_edit_contract") or {},
        "hypothesis": context.get("hypothesis") or "",
        "agent_generated_solver_quality_contract": build_agent_generated_solver_quality_contract(context),
        "incumbent_code_context": context.get("incumbent_code_context") or {},
        "loop_feedback": context.get("loop_feedback") or {},
        "active_method_package": context.get("active_method_package") or {},
        "active_knowledge_paths": [
            str(item)
            for item in context.get("auto_knowledge_cards") or []
            if str(item).strip()
        ],
        "worker_instruction_delta": {
            key: (context.get("worker_instruction") or {}).get(key)
            for key in ("required_order", "round_feedback_rule", "incremental_edit_rule")
            if (context.get("worker_instruction") or {}).get(key) is not None
        },
        "context_compaction": context.get("context_compaction") or {},
    }
    dynamic = compact_json(dynamic_payload, max_chars=WORKER_DYNAMIC_CONTEXT_MAX_CHARS).text
    return {"stable": stable, "dynamic": dynamic}
