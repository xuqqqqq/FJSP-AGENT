"""Build the task-only planning view used by the None guidance ablation."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from harness_agent.context.compaction import ROUND_CONTEXT_MAX_CHARS, compact_json


GENERIC_PLANNING_PACKET_MAX_CHARS = ROUND_CONTEXT_MAX_CHARS
GENERIC_PLANNING_FACTS_MAX_CHARS = 12_000
_ADVICE_KEY_PARTS = (
    "activation", "algorithm", "direction", "guidance", "hint", "hypothesis",
    "knowledge", "method", "operator", "recommend", "skill", "strategy",
)


def build_generic_planning_packet(
    context: dict[str, Any],
    planning_packet: dict[str, Any],
    expected_methods: int,
    round_index: int,
) -> dict[str, Any]:
    """Preserve source contracts; compact only optional facts, never domain advice.

    Documents already contain context-bounded source snippets. Oversized source
    contracts fail explicitly rather than acquiring another silent truncation.
    The size limit uses the repository's UTF-8 JSON attachment representation.
    """

    if not 1 <= expected_methods <= 4:
        raise ValueError("expected_methods must be between 1 and 4")
    task = _dict(context.get("task"))
    objectives = context.get("objectives") or task.get("objectives") or []
    incumbent = _dict(planning_packet.get("incumbent_evidence"))
    code_context = _dict(context.get("incumbent_code_context"))
    evaluation = _dict(incumbent.get("evaluation"))
    incumbent_available = round_index >= 0 and bool(
        incumbent.get("objective_key")
        or incumbent.get("files")
        or code_context.get("files")
        or evaluation.get("valid")
        or evaluation.get("best_metrics")
    )
    io_digest = {
        key: deepcopy(context.get(key, {}))
        for key in ("evaluator_protocol", "edit_policy")
    }
    if "io_contract" in context:
        io_digest["io_contract"] = deepcopy(context["io_contract"])
    packet = {
        "schema_version": 1,
        "planning_stage": "generic_guidance_ablation",
        "phase": "baseline" if round_index < 0 else "improvement",
        "round_index": round_index,
        "incumbent_available": incumbent_available,
        "requires_incumbent_preservation": incumbent_available,
        "task": deepcopy(task),
        "task_digest": {
            "task_id": task.get("task_id") or context.get("task_id"),
            "problem_family": task.get("problem_family") or context.get("problem_family"),
            "description": task.get("description"),
            "objectives": deepcopy(objectives),
        },
        "io_digest": io_digest,
        "documents": deepcopy(context.get("documents") or []),
        "output_contract": {
            "candidate_method_count": expected_methods,
            "required_candidate_fields": [
                "candidate_id", "method_name", "hypothesis", "worker_objective", "strategy_type",
            ],
            "methods_must_be_distinct": True,
        },
    }
    # Reserve room for fact-compaction and size metadata before spending the
    # remaining space on optional observations.
    facts_budget = min(
        GENERIC_PLANNING_FACTS_MAX_CHARS,
        GENERIC_PLANNING_PACKET_MAX_CHARS - _serialized_chars(packet) - 1_024,
    )
    if facts_budget < 512:
        raise ValueError(
            "Generic planning packet task/contracts/documents exceed the "
            f"{GENERIC_PLANNING_PACKET_MAX_CHARS}-character budget; "
            "reduce source context explicitly (source contracts were not truncated)"
        )
    diagnostics = _dict(
        context.get("instance_diagnostics", planning_packet.get("instance_diagnostics"))
    )
    recent = planning_packet.get("recent_round_evidence") or []
    facts = {
        "instance_diagnostics": {
            key: diagnostics[key]
            for key in ("status", "summary", "instances", "shape_groups", "completeness")
            if key in diagnostics
        },
        "incumbent_evidence": {
            "objective_key": incumbent.get("objective_key") if incumbent_available else None,
            "evaluation": evaluation if incumbent_available else {},
        },
        "recent_round_evidence": [
            {
                key: item[key]
                for key in (
                    "round_index", "status", "decision", "incumbent_key_before",
                    "incumbent_key_after", "candidate_key", "core", "competition_result",
                )
                if key in item
            }
            for item in recent[-3:]
            if isinstance(item, dict)
        ] if isinstance(recent, list) else [],
        "runtime_limits": {
            key: value
            for key, value in _dict(planning_packet.get("runtime_limits")).items()
            if key in {"max_competing_workers", "one_direction", "backend_algorithm_agnostic"}
        },
    }
    compacted = compact_json(_without_domain_advice(facts), max_chars=facts_budget)
    packet.update(compacted.payload)
    packet["packet_budget"] = {
        "limit_chars": GENERIC_PLANNING_PACKET_MAX_CHARS,
        "facts_limit_chars": facts_budget,
        "facts_compacted": compacted.compacted,
        "facts_compaction_profile": compacted.profile,
        "source_contracts_truncated": False,
    }
    # The reported count includes its own digits.
    for _ in range(4):
        size = _serialized_chars(packet)
        if packet["packet_budget"].get("serialized_chars") == size:
            break
        packet["packet_budget"]["serialized_chars"] = size
    if _serialized_chars(packet) > GENERIC_PLANNING_PACKET_MAX_CHARS:
        raise ValueError("Generic planning packet exceeds its size budget; source contracts were not truncated")
    return packet


def _without_domain_advice(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_domain_advice(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in _ADVICE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_without_domain_advice(item) for item in value]
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _serialized_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2))
