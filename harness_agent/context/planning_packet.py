"""Schema-driven Planning Packet projections for OpenCode Main.

The packet is compiled from Harness facts.  It is not recursively compacted as
one JSON blob: catalogs and current evidence are protected, recent rounds use a
typed projection, and older history is represented by bounded aggregates.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness_agent.agents.incumbent_audit import compact_incumbent_capability_audit
from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.context.compaction import compact_json, compact_source_records


PLANNING_PACKET_SCHEMA_VERSION = 2
PLANNING_PACKET_MAX_CHARS = 48_000
IMPLEMENTATION_PACKET_TARGET_CHARS = 44_000
RECENT_ROUND_LIMIT = 2
PLANNING_PACKET_SECTION_SPECS = (
    ("overview", "Planning metadata, budgets, and protected completeness state."),
    ("task_io", "Task digest, IO contract, runtime limits, and planner output contract."),
    ("instance_and_catalogs", "Instance diagnostics plus first-stage query and family catalogs."),
    ("research_state", "Cross-round transition state and next-action policy."),
    ("incumbent", "Incumbent evidence and static capability audit."),
    ("evidence_history", "Recent rounds, historical aggregates, and latest attempt evidence."),
    ("direction_context", "Second-stage direction knowledge and eligible method packages."),
    ("control_context", "Guidance, intervention, memory, and artifact references."),
)


class PlanningPacketBudgetError(ValueError):
    """Raised when protected planning facts cannot fit the configured budget."""


def activation_check_schema_contract() -> dict[str, Any]:
    """Machine-checkable contract injected into Main prompts and packets."""

    return {
        "schema_version": 2,
        "type": "array",
        "item_type": "object",
        "item_required_fields": ["path", "operator"],
        "item_optional_fields": [
            "id",
            "expected",
            "required",
            "aggregation",
            "min_passes",
            "description",
        ],
        "operators": ["exists", "truthy", "eq", "ne", "gt", "gte", "lt", "lte", "contains", "one_of"],
        "operators_requiring_expected": ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "one_of"],
        "operators_allowing_omitted_expected": ["exists", "truthy"],
        "default_required": True,
        "default_aggregation": "any",
        "allowed_aggregations": ["any", "all", "min_passes"],
        "min_passes_rule": "required only when aggregation == min_passes",
        "purpose": "prove mechanism execution rather than result quality",
    }


def build_planning_packet(
    *,
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    """Compile Main's bounded, traceable direction-selection view."""

    task = _dict(context.get("task"))
    catalog = _dict(context.get("method_package_catalog"))
    incumbent = _dict(context.get("incumbent_code_context"))
    incumbent_audit = _dict(context.get("incumbent_capability_audit"))
    experience = _dict(loop_feedback.get("experience_memory"))
    tiers = _dict(experience.get("memory_tiers"))
    all_rounds = [item for item in loop_feedback.get("previous_rounds") or [] if isinstance(item, dict)]
    recent_source = all_rounds[-RECENT_ROUND_LIMIT:]
    historical_source = all_rounds[:-RECENT_ROUND_LIMIT]
    recent_rounds = [project_recent_round(item) for item in recent_source]
    max_competing_workers = max(
        1,
        min(
            4,
            int((_dict(loop_feedback.get("competition"))).get("max_competing_workers") or 1),
        ),
    )

    payload: dict[str, Any] = {
        "schema_version": PLANNING_PACKET_SCHEMA_VERSION,
        "planning_stage": "direction_selection",
        "phase": "baseline" if round_index < 0 else "improvement",
        "direction_id": f"d{round_index:03d}",
        "task_digest": {
            "task_id": task.get("task_id") or context.get("task_id"),
            "problem_family": task.get("problem_family") or context.get("problem_family"),
            "description": task.get("description"),
            "objectives": context.get("objectives") or [],
            "hypothesis": context.get("hypothesis") or "",
        },
        "io_digest": {
            "evaluator_protocol": context.get("evaluator_protocol") or {},
            "edit_policy": context.get("edit_policy") or {},
            "quality_contract": build_agent_generated_solver_quality_contract(context),
        },
        "instance_diagnostics": project_instance_diagnostics(context.get("instance_diagnostics")),
        "strategy_selection_cards": compact_source_records(
            context.get("strategy_selection_cards"),
            max_items=4,
            max_snippet_chars=2_000,
        ),
        # These catalogs are machine contracts.  They are copied losslessly and
        # never passed through the generic recursive compactor.
        "knowledge_query_catalog": context.get("knowledge_query_catalog") or {"tags": []},
        "method_family_catalog": context.get("method_family_catalog") or {"families": []},
        "method_package_catalog": {
            "active_features": catalog.get("active_features") or [],
            "available_after_direction_selection": True,
            "packages": [],
        },
        "research_state": project_research_state(
            all_rounds,
            next_round_guidance=loop_feedback.get("next_round_guidance"),
            user_intervention=loop_feedback.get("user_intervention"),
        ),
        "incumbent_evidence": {
            "objective_key": loop_feedback.get("incumbent_key_before"),
            "source": incumbent.get("source"),
            "evaluation": project_run_summary(loop_feedback.get("incumbent_summary")),
            "files": [
                {
                    key: item.get(key)
                    for key in ("relative_path", "path", "sha256", "chars", "truncated")
                    if key in item
                }
                for item in incumbent.get("files") or []
                if isinstance(item, dict)
            ][:6],
        },
        "incumbent_capability_audit": project_incumbent_audit(incumbent_audit),
        "recent_round_evidence": recent_rounds,
        "latest_evidence": latest_evidence_pointer(recent_rounds),
        "historical_aggregates": aggregate_historical_rounds(historical_source),
        "latest_attempt_evidence": project_latest_attempt(loop_feedback.get("current_round_repair")),
        "next_round_guidance": loop_feedback.get("next_round_guidance") or {},
        "user_intervention": loop_feedback.get("user_intervention") or {},
        "direction_patch_contract": loop_feedback.get("direction_patch_contract") or {},
        "validated_memory": compact_direction_selection_memory(
            (tiers.get("validated_lessons") or [])[-6:]
        ),
        "artifact_index": build_artifact_index(
            incumbent_files=incumbent.get("files") or [],
            recent_rounds=recent_source,
            loop_feedback=loop_feedback,
        ),
        "runtime_limits": {
            "one_direction": True,
            "backend_algorithm_agnostic": True,
            "worker_full_context_visible": False,
            "main_reads_full_incumbent_source": True,
            "main_receives_structured_incumbent_audit": bool(incumbent_audit),
            "max_competing_workers": max_competing_workers,
        },
        "planner_output_contract": {
            "competition_policy": {
                "parallel_competition_required": max_competing_workers > 1,
                "minimum_candidate_variants": 2 if max_competing_workers > 1 else 0,
                "maximum_candidate_variants": max_competing_workers,
                "actual_started_candidates_source": "competition_result.candidates",
            },
            "candidate_variants_must_declare_activation_checks": True,
            "candidate_variant_required_fields": [
                "hypothesis",
                "strategy_type",
                "next_mutation.target_symbols",
                "next_mutation.change",
                "activation_checks",
            ],
            "activation_checks_purpose": "prove mechanism execution rather than result quality",
            "activation_check_schema": activation_check_schema_contract(),
            "experiment_stage_options": [
                "baseline",
                "probe",
                "scale",
                "pivot",
                "research_tournament",
            ],
            "research_tournament_scope": (
                "may compare across method families when round evidence invalidates the current family-level assumption"
            ),
        },
        "packet_completeness": {
            "protected_complete": True,
            "protected_sections": [
                "/knowledge_query_catalog",
                "/method_family_catalog",
                "/research_state",
                "/latest_evidence",
                "/latest_attempt_evidence",
                "/next_round_guidance",
                "/user_intervention",
                "/direction_patch_contract",
                "/planner_output_contract",
            ],
            "source_round_count": len(all_rounds),
            "recent_round_count": len(recent_rounds),
            "historical_round_count": len(historical_source),
        },
    }
    return finalize_planning_packet(payload)


def build_implementation_planning_packet(
    *,
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
    round_index: int,
    direction_selection: dict[str, Any],
) -> dict[str, Any]:
    """Compile Main's second-stage implementation view without root compaction."""

    packet = build_planning_packet(
        context=context,
        loop_feedback=loop_feedback,
        round_index=round_index,
    )
    packet.pop("packet_budget", None)
    packet["planning_stage"] = "implementation_planning"
    packet["direction_selection"] = direction_selection
    packet.pop("strategy_selection_cards", None)
    packet.pop("knowledge_query_catalog", None)
    packet.pop("method_family_catalog", None)
    active = _dict(context.get("active_direction_knowledge"))
    packet["active_direction_knowledge"] = {
        "method_family": active.get("method_family"),
        "method_families": active.get("method_families") or [],
        "query": active.get("query") or [],
        "paths": active.get("paths") or [],
        "cards": compact_source_records(
            active.get("asset_records"),
            max_items=6,
            max_snippet_chars=1_500,
        ),
        "audit": active.get("audit") or {},
    }
    catalog = _dict(context.get("method_package_catalog"))
    packet["eligible_method_packages"] = compact_method_package_candidates(catalog)
    packet["method_package_catalog"] = {
        "active_features": catalog.get("active_features") or [],
        "knowledge_query_tags": catalog.get("knowledge_query_tags") or [],
        "eligible_package_ids": [
            item.get("package_id")
            for item in catalog.get("packages") or []
            if isinstance(item, dict) and item.get("package_id")
        ],
    }
    io_digest = _dict(packet.get("io_digest"))
    io_digest["quality_contract"] = project_implementation_quality_contract(
        _dict(io_digest.get("quality_contract"))
    )
    packet["io_digest"] = io_digest
    completeness = _dict(packet.get("packet_completeness"))
    completeness["protected_sections"] = [
        path
        for path in completeness.get("protected_sections") or []
        if path not in {"/knowledge_query_catalog", "/method_family_catalog"}
    ]
    completeness["protected_sections"].extend(
        [
            "/direction_selection",
            "/active_direction_knowledge/method_family",
            "/active_direction_knowledge/method_families",
            "/active_direction_knowledge/query",
            "/active_direction_knowledge/paths",
            "/active_direction_knowledge/audit",
            "/method_package_catalog",
        ]
    )
    packet["packet_completeness"] = completeness
    compact_implementation_history(packet)
    return finalize_planning_packet(packet)


def project_implementation_quality_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Carry hard capability names forward after stage one reviewed the playbook."""

    return {
        key: contract.get(key)
        for key in (
            "enabled",
            "problem_family",
            "active_features",
            "required_code_capabilities",
            "variant_required_code_capabilities",
            "quality_rule",
            "baseline_generation_rule",
            "improvement_rule",
        )
        if key in contract
    }


def compact_implementation_history(packet: dict[str, Any]) -> None:
    """Keep the latest round detailed and make the prior round causal, not verbose."""

    rounds = packet.get("recent_round_evidence")
    if not isinstance(rounds, list) or len(rounds) < 2:
        return
    for index, item in enumerate(rounds[:-1]):
        if not isinstance(item, dict):
            continue
        competition = _dict(item.get("competition_result"))
        candidates = []
        for candidate in competition.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            diagnostics = _dict(candidate.get("diagnostics"))
            candidates.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "status": candidate.get("status"),
                    "objective": candidate.get("objective") or [],
                    "activation_required": _dict(candidate.get("diagnostics")).get("activation_required"),
                    "mechanism_activation": project_mechanism_activation(
                        candidate.get("mechanism_activation")
                    ),
                    "semantic_review": project_semantic_review(
                        diagnostics.get("semantic_review")
                    ),
                }
            )
        rounds[index] = {
            key: item.get(key)
            for key in (
                "round_index",
                "decision",
                "candidate_key",
                "incumbent_key_after",
                "direction_id",
                "title",
                "method_family",
                "method_families",
                "strategy_type",
                "experiment_stage",
                "hypothesis",
                "activation_checks",
                "promotion_check",
                "round_reflection",
                "artifact_refs",
            )
            if key in item
        }
        rounds[index]["competition_result"] = {
            "status": competition.get("status"),
            "selected_candidate_id": competition.get("selected_candidate_id"),
            "selected_objective_key": competition.get("selected_objective_key") or [],
            "measured_candidate_id": competition.get("measured_candidate_id"),
            "measured_objective_key": competition.get("measured_objective_key") or [],
            "selected_for_promotion": competition.get("selected_for_promotion"),
            "best_legal_candidate": project_observed_candidate(
                competition.get("best_legal_candidate")
            ),
            "best_activated_candidate": project_observed_candidate(
                competition.get("best_activated_candidate")
            ),
            "candidates": candidates,
        }


def project_instance_diagnostics(value: Any) -> dict[str, Any]:
    diagnostics = _dict(value)
    instances = [item for item in diagnostics.get("instances") or [] if isinstance(item, dict)]
    shape_groups = [item for item in diagnostics.get("shape_groups") or [] if isinstance(item, dict)]
    return {
        "status": diagnostics.get("status"),
        "summary": diagnostics.get("summary") or {},
        "direction_hints": _bounded_strings(diagnostics.get("direction_hints"), limit=8, chars=700),
        "best_known_csv": diagnostics.get("best_known_csv"),
        "shape_groups": [dict(item) for item in shape_groups[:8]],
        "instances": [project_instance(item) for item in instances[:8]],
        "completeness": {
            "source_count": len(instances),
            "included_count": min(len(instances), 8),
            "omitted_count": max(0, len(instances) - 8),
            "complete": len(instances) <= 8,
        },
    }


def project_instance(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "path",
        "exists",
        "parsed",
        "name",
        "variant",
        "job_count",
        "machine_count",
        "operation_count",
        "max_candidate_count",
        "scale",
        "avg_candidate_count",
        "min_candidate_count",
        "max_observed_candidate_count",
        "candidate_count_cv",
        "flexible_operation_ratio",
        "full_flexibility_ratio",
        "processing_time_min",
        "processing_time_max",
        "processing_time_avg",
        "processing_time_cv",
        "duration_spread_avg",
        "duration_spread_ratio",
        "machine_eligibility_cv",
        "fractional_min_load_cv",
        "setup_time_kind",
    )
    return {key: value.get(key) for key in keys if key in value}


def project_research_state(
    rounds: list[dict[str, Any]],
    *,
    next_round_guidance: Any,
    user_intervention: Any = None,
) -> dict[str, Any]:
    """Compile the cross-round research transition, not just a history summary."""

    latest = rounds[-1] if rounds else {}
    direction = _dict(latest.get("direction_plan"))
    selection = _dict(direction.get("direction_selection"))
    reflection = _dict(latest.get("round_reflection"))
    next_action = _dict(reflection.get("next_action"))
    requested_action = str(next_action.get("action") or "").strip().lower().replace("-", "_")
    intervention = _dict(user_intervention)
    intervention_patch = _dict(intervention.get("direction_patch"))
    intervention_action = str(
        intervention_patch.get("action") or intervention.get("action") or ""
    ).strip().lower().replace("-", "_")
    method_families = direction.get("method_families") or selection.get("method_families") or []
    method_family = direction.get("method_family") or selection.get("method_family")
    if method_family and not method_families:
        method_families = [{"id": str(method_family), "role": "primary"}]
    knowledge_query = direction.get("knowledge_query") or selection.get("knowledge_query") or []
    outcome = str(reflection.get("hypothesis_outcome") or "").strip().lower()
    last_decision = str(latest.get("decision") or "").strip().lower()
    activation_inconclusive_count = consecutive_activation_inconclusive_rounds(
        rounds,
        method_family=str(method_family or ""),
    )
    activation_evidence_exhausted = activation_inconclusive_count >= 2
    scale_evidence = scale_activation_evidence(latest, direction=direction)
    effective_action = requested_action
    transition_adjustment = None
    if outcome == "refuted" and requested_action not in {"pivot", "research_tournament"}:
        effective_action = "pivot"
        transition_adjustment = "refuted_hypothesis_requires_pivot"
    elif requested_action == "scale" and last_decision != "promoted":
        effective_action = "probe"
        transition_adjustment = "scale_requires_a_promoted_predecessor"
    elif requested_action == "scale" and not scale_evidence["passed"]:
        effective_action = "research_tournament"
        transition_adjustment = f"scale_activation_not_verified:{scale_evidence['reason']}"

    selection_reason = "active_method_family_continues"
    selection_required = False
    if not rounds or not method_family:
        selection_required = True
        selection_reason = "no_active_method_family"
    elif intervention_action in {"pivot", "research_tournament"}:
        selection_required = True
        selection_reason = f"user_requested_{intervention_action}"
    elif intervention_action in {"continue", "revise", "probe", "scale"}:
        selection_required = False
        selection_reason = f"user_requested_{intervention_action}_within_active_family"
    elif activation_evidence_exhausted:
        selection_required = True
        effective_action = "research_tournament"
        experiment_stage = "research_tournament"
        selection_reason = "activation_evidence_exhausted"
    elif effective_action in {"pivot", "research_tournament"}:
        selection_required = True
        selection_reason = transition_adjustment or f"reflection_requested_{effective_action}"

    experiment_stage = effective_action or str(direction.get("experiment_stage") or "probe")
    if intervention_action in {"continue", "probe", "scale", "pivot", "research_tournament"}:
        experiment_stage = "probe" if intervention_action == "continue" else intervention_action
    elif intervention_action == "revise":
        experiment_stage = "probe"
    return {
        "schema_version": 1,
        "active_direction_id": direction.get("direction_id"),
        "active_method_family": method_family,
        "active_method_families": method_families,
        "active_knowledge_query": knowledge_query,
        "active_primary_search_pressure": selection.get("primary_search_pressure"),
        "active_hypothesis": direction.get("hypothesis"),
        "experiment_stage": experiment_stage,
        "required_activation_checks": project_activation_checks(direction.get("activation_checks")),
        "last_decision": latest.get("decision"),
        "last_hypothesis_outcome": outcome or None,
        "requested_next_action": requested_action or None,
        "next_action": effective_action or None,
        "transition_adjustment": transition_adjustment,
        "activation_inconclusive_count": activation_inconclusive_count,
        "activation_evidence_exhausted": activation_evidence_exhausted,
        "scale_activation_evidence": scale_evidence,
        "next_action_rationale": str(next_action.get("rationale") or "")[:1_200],
        "next_round_guidance": next_round_guidance or {},
        "planning_mode": "direction_selection" if selection_required else "direction_continuation",
        "selection_required": selection_required,
        "selection_reason": selection_reason,
        "method_family_policy": "reselect" if selection_required else "inherit",
        "transition_contract": {
            "inherit_actions": ["continue", "probe", "scale"],
            "reselect_actions": ["pivot", "research_tournament"],
            "rule": (
                "probe/scale continue the active method families; only pivot, research_tournament, "
                "missing active family, or explicit invalidation may reselect them"
            ),
        },
    }


def scale_activation_evidence(
    latest_round: dict[str, Any],
    *,
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require executable mechanism evidence before a family may enter ``scale``."""

    active_direction = direction if isinstance(direction, dict) else _dict(latest_round.get("direction_plan"))
    if not active_direction.get("activation_checks"):
        return {"passed": False, "reason": "activation_checks_not_declared"}
    competition = _dict(latest_round.get("competition_result")) or _dict(
        active_direction.get("competition_result")
    )
    candidates = [item for item in competition.get("candidates") or [] if isinstance(item, dict)]
    if not candidates:
        return {"passed": False, "reason": "candidate_evidence_missing"}
    active_family = str(active_direction.get("method_family") or "").strip()
    active_package = str(active_direction.get("method_package_id") or "").strip()
    for candidate in candidates:
        if candidate.get("activation_required") is not True:
            continue
        if _dict(candidate.get("mechanism_activation")).get("passed") is not True:
            continue
        candidate_family = str(candidate.get("method_family") or active_family).strip()
        candidate_package = str(candidate.get("method_package_id") or active_package).strip()
        if active_family and candidate_family != active_family:
            continue
        if active_package and candidate_package != active_package:
            continue
        try:
            event_bytes = int(candidate.get("session_event_stream_bytes") or 0)
        except (TypeError, ValueError):
            event_bytes = 0
        requested = str(candidate.get("requested_session_id") or "").strip()
        commanded = str(candidate.get("command_session_id") or "").strip()
        observed = str(candidate.get("observed_session_id") or candidate.get("worker_session_id") or "").strip()
        if event_bytes <= 0 or not observed:
            continue
        if requested and (commanded != requested or observed != requested):
            continue
        return {
            "passed": True,
            "reason": "activated_candidate_with_continuous_nonzero_session",
            "candidate_id": candidate.get("candidate_id"),
        }
    return {"passed": False, "reason": "no_activated_candidate_with_continuous_nonzero_session"}


def consecutive_activation_inconclusive_rounds(
    rounds: list[dict[str, Any]],
    *,
    method_family: str,
) -> int:
    count = 0
    for item in reversed(rounds):
        direction = _dict(item.get("direction_plan"))
        if str(direction.get("method_family") or "") != method_family:
            break
        reflection = _dict(item.get("round_reflection"))
        outcome = str(reflection.get("hypothesis_outcome") or "").lower()
        if not outcome.startswith("inconclusive"):
            break
        competition = _dict(item.get("competition_result")) or _dict(direction.get("competition_result"))
        candidates = [row for row in competition.get("candidates") or [] if isinstance(row, dict)]
        if not candidates or any(
            _dict(row.get("mechanism_activation")).get("passed") is True
            for row in candidates
        ):
            break
        count += 1
    return count


def project_incumbent_audit(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        return {}
    audit = compact_incumbent_capability_audit(value)
    files = []
    for raw in audit.get("files") or []:
        if not isinstance(raw, dict):
            continue
        files.append(
            {
                **{
                    key: raw.get(key)
                    for key in (
                        "relative_path",
                        "sha256",
                        "line_count",
                        "parse_status",
                        "entrypoints",
                        "has_main_guard",
                        "truncated",
                        "error",
                    )
                    if key in raw
                },
                "configurations": [dict(item) for item in raw.get("configurations") or [] if isinstance(item, dict)][:32],
                "loops": [dict(item) for item in raw.get("loops") or [] if isinstance(item, dict)][:20],
                "functions": [dict(item) for item in raw.get("functions") or [] if isinstance(item, dict)][:24],
                "classes": [dict(item) for item in raw.get("classes") or [] if isinstance(item, dict)][:12],
                "internal_call_edges": [
                    dict(item) for item in raw.get("internal_call_edges") or [] if isinstance(item, dict)
                ][:32],
            }
        )
    return {
        "schema_version": audit.get("schema_version") or 1,
        "source": audit.get("source"),
        "purpose": audit.get("purpose"),
        "summary": audit.get("summary") or {},
        "files": files,
        "interpretation_rules": list(audit.get("interpretation_rules") or [])[:6],
        "limitations": list(audit.get("limitations") or [])[:6],
    }


def project_recent_round(item: dict[str, Any]) -> dict[str, Any]:
    direction = _dict(item.get("direction_plan"))
    return {
        "round_index": item.get("round_index"),
        "decision": item.get("decision"),
        "candidate_key": item.get("candidate_key"),
        "incumbent_key_after": item.get("incumbent_key_after"),
        "direction_id": direction.get("direction_id"),
        "title": direction.get("title"),
        "method_family": direction.get("method_family"),
        "method_families": direction.get("method_families") or [],
        "strategy_type": direction.get("strategy_type"),
        "experiment_stage": direction.get("experiment_stage"),
        "planner": direction.get("planner"),
        "planner_fallback": direction.get("planner_fallback") or {},
        "planning_contract_status": direction.get("planning_contract_status") or {},
        "hypothesis": str(direction.get("hypothesis") or "")[:800],
        "implementation_order": _bounded_strings(direction.get("implementation_order"), limit=8, chars=160),
        "activation_checks": project_activation_checks(direction.get("activation_checks")),
        "failure_signatures": _bounded_strings(item.get("failure_signatures"), limit=8, chars=300),
        "candidate_summary": project_run_summary(item.get("candidate_summary")),
        "competition_result": compact_round_competition_result(
            item.get("competition_result"),
            direction=direction,
        ),
        "promotion_check": project_promotion_check(item.get("promotion_check")),
        "round_reflection": project_round_reflection(item.get("round_reflection")),
        "artifact_refs": round_artifact_refs(item),
    }


def latest_evidence_pointer(recent_rounds: list[dict[str, Any]]) -> dict[str, Any]:
    if not recent_rounds:
        return {"status": "unavailable", "round_index": None, "source": None}
    latest = recent_rounds[-1]
    return {
        "status": "available",
        "round_index": latest.get("round_index"),
        "direction_id": latest.get("direction_id"),
        "decision": latest.get("decision"),
        "source": f"/recent_round_evidence/{len(recent_rounds) - 1}",
        "complete": True,
    }


def aggregate_historical_rounds(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    decision_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    activation_counts: Counter[str] = Counter()
    semantic_counts: Counter[str] = Counter()
    for item in rounds:
        decision_counts[str(item.get("decision") or "unknown")] += 1
        direction = _dict(item.get("direction_plan"))
        family = str(direction.get("method_family") or "unknown")
        family_counts[family] += 1
        reflection = _dict(item.get("round_reflection"))
        outcome_counts[str(reflection.get("hypothesis_outcome") or "unknown")] += 1
        competition = _dict(item.get("competition_result")) or _dict(direction.get("competition_result"))
        for candidate in competition.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            activation_counts[str(_dict(candidate.get("mechanism_activation")).get("status") or "unknown")] += 1
            semantic_counts[str(_dict(candidate.get("semantic_review")).get("status") or "unknown")] += 1
    round_indexes = [item.get("round_index") for item in rounds if item.get("round_index") is not None]
    source_refs = _historical_boundary_refs(rounds)
    return {
        "schema_version": 1,
        "source_count": len(rounds),
        "included_count": len(rounds),
        "omitted_count": 0,
        "aggregate_complete": True,
        "detail_complete": not rounds,
        "round_range": [round_indexes[0], round_indexes[-1]] if round_indexes else [],
        "decision_counts": _bounded_counter(decision_counts),
        "method_family_counts": _bounded_counter(family_counts),
        "hypothesis_outcome_counts": _bounded_counter(outcome_counts),
        "activation_status_counts": _bounded_counter(activation_counts),
        "semantic_status_counts": _bounded_counter(semantic_counts),
        "source_refs": source_refs,
    }


def compact_round_competition_result(
    value: Any,
    *,
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    direction = direction if isinstance(direction, dict) else {}
    competition = _dict(value)
    if not competition and isinstance(direction.get("competition_result"), dict):
        competition = direction["competition_result"]
    if not competition:
        return {}
    candidates = [
        compact_round_candidate_evidence(item, direction=direction)
        for item in competition.get("candidates") or []
        if isinstance(item, dict)
    ][:4]
    return {
        "status": competition.get("status"),
        "candidate_count": int(competition.get("candidate_count", len(candidates)) or 0),
        "eligible_candidate_count": int(competition.get("eligible_candidate_count", 0) or 0),
        "selected_candidate_id": competition.get("selected_candidate_id"),
        "selected_objective_key": competition.get("selected_objective_key") or [],
        "measured_candidate_id": competition.get("measured_candidate_id"),
        "measured_objective_key": competition.get("measured_objective_key") or [],
        "selected_for_promotion": competition.get("selected_for_promotion"),
        "best_legal_candidate": project_observed_candidate(
            competition.get("best_legal_candidate")
        ),
        "best_activated_candidate": project_observed_candidate(
            competition.get("best_activated_candidate")
        ),
        "selection_rule": str(competition.get("selection_rule") or "")[:500],
        "candidates": candidates,
        "completeness": {
            "source_count": len([item for item in competition.get("candidates") or [] if isinstance(item, dict)]),
            "included_count": len(candidates),
            "omitted_count": max(
                0,
                len([item for item in competition.get("candidates") or [] if isinstance(item, dict)]) - len(candidates),
            ),
        },
    }


def compact_round_candidate_evidence(
    candidate: dict[str, Any],
    *,
    direction: dict[str, Any],
) -> dict[str, Any]:
    summary = _dict(candidate.get("summary"))
    diagnostics_payload = {
        "eligible": candidate.get("eligible"),
        "core_eligible": candidate.get("core_eligible"),
        "semantic_eligible": candidate.get("semantic_eligible"),
        "activation_eligible": candidate.get("activation_eligible"),
        "activation_required": candidate.get("activation_required"),
        "worker_status": candidate.get("worker_status"),
        "ja_stage": candidate.get("ja_stage"),
        "ja_issues": _bounded_strings(candidate.get("ja_issues"), limit=8, chars=500),
        "semantic_review": project_semantic_review(candidate.get("semantic_review")),
        "validation_summary": summary.get("validation_summary") or {},
    }
    return {
        "candidate_id": candidate.get("candidate_id"),
        "status": candidate.get("status"),
        "model": candidate_model_hint(candidate, direction=direction),
        "objective": candidate.get("objective_key") or [],
        "mechanism_activation": project_mechanism_activation(candidate.get("mechanism_activation")),
        "activation_checks": project_activation_checks(candidate.get("activation_checks")),
        "selected_candidate_variant": candidate.get("selected_candidate_variant") or {},
        "summary": project_run_summary(summary),
        "diagnostics": diagnostics_payload,
        "patch_path": candidate.get("patch_path"),
    }


def project_observed_candidate(value: Any) -> dict[str, Any] | None:
    candidate = _dict(value)
    if not candidate:
        return None
    activation = _dict(candidate.get("mechanism_activation"))
    summary = _dict(candidate.get("summary"))
    return {
        "candidate_id": candidate.get("candidate_id"),
        "objective_key": candidate.get("objective_key") or [],
        "worktree": candidate.get("worktree"),
        "activation_status": activation.get("status"),
        "activation_passed": activation.get("passed"),
        "ja_accepted": candidate.get("ja_accepted"),
        "semantic_eligible": candidate.get("semantic_eligible"),
        "summary": project_run_summary(summary),
    }
def candidate_model_hint(candidate: dict[str, Any], *, direction: dict[str, Any]) -> str:
    for key in ("model", "worker_model", "candidate_model"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value[:200]
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    selected_variant = _dict(direction.get("selected_candidate_variant"))
    variants = [selected_variant, *[item for item in direction.get("candidate_variants") or [] if isinstance(item, dict)]]
    for item in variants:
        if candidate_id and candidate_id != str(item.get("candidate_id") or "").strip():
            continue
        for key in ("title", "hypothesis", "method_family", "strategy_type", "experiment_stage"):
            value = str(item.get(key) or "").strip()
            if value:
                return value[:200]
    for key in ("title", "hypothesis", "method_family", "strategy_type", "experiment_stage"):
        value = str(direction.get(key) or "").strip()
        if value:
            return value[:200]
    return ""


def project_mechanism_activation(value: Any) -> dict[str, Any]:
    activation = _dict(value)
    if not activation:
        return {}
    return {
        key: activation.get(key)
        for key in (
            "status",
            "passed",
            "declared_check_count",
            "required_check_count",
            "required_failure_count",
        )
        if key in activation
    } | {"checks": project_activation_checks(activation.get("checks"), include_observation=True)}


def project_activation_checks(value: Any, *, include_observation: bool = False) -> list[dict[str, Any]]:
    checks = []
    keys = [
        "id",
        "path",
        "operator",
        "expected",
        "required",
        "aggregation",
        "min_passes",
        "description",
    ]
    if include_observation:
        keys.extend(
            [
                "found",
                "observed",
                "passed",
                "evaluated_run_count",
                "passed_run_count",
            ]
        )
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        checks.append({key: item.get(key) for key in keys if key in item})
        if len(checks) >= 12:
            break
    return checks


def project_semantic_review(value: Any) -> dict[str, Any]:
    review = _dict(value)
    if not review:
        return {}
    findings = []
    for item in review.get("findings") or []:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                key: (str(item.get(key) or "")[:700] if key in {"summary", "message", "repair"} else item.get(key))
                for key in (
                    "blocking",
                    "category",
                    "summary",
                    "message",
                    "repair",
                    "knowledge_path",
                )
                if key in item
            }
        )
        if len(findings) >= 8:
            break
    return {
        "status": review.get("status"),
        "accepted": review.get("accepted"),
        "summary": str(review.get("summary") or "")[:1_200],
        "findings": findings,
        "reviewer": review.get("reviewer"),
    }


def project_promotion_check(value: Any) -> dict[str, Any]:
    check = _dict(value)
    return {
        key: (str(check.get(key) or "")[:800] if key in {"reason", "summary"} else check.get(key))
        for key in (
            "promoted",
            "eligible",
            "reason",
            "summary",
            "candidate_key",
            "incumbent_key_before",
            "incumbent_key_after",
            "selected_candidate_id",
        )
        if key in check
    }


def project_round_reflection(value: Any) -> dict[str, Any]:
    reflection = _dict(value)
    if not reflection:
        return {}
    findings = []
    for item in reflection.get("candidate_findings") or []:
        if isinstance(item, dict):
            findings.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "outcome": item.get("outcome"),
                    "evidence": _bounded_strings(item.get("evidence"), limit=6, chars=400),
                    "causal_interpretation": str(item.get("causal_interpretation") or "")[:800],
                }
            )
        if len(findings) >= 4:
            break
    next_action = _dict(reflection.get("next_action"))
    return {
        "hypothesis_outcome": reflection.get("hypothesis_outcome"),
        "summary": str(reflection.get("summary") or "")[:1_200],
        "candidate_findings": findings,
        "next_action": {
            "action": next_action.get("action"),
            "rationale": str(next_action.get("rationale") or "")[:800],
            "required_activation_checks": project_activation_checks(
                next_action.get("required_activation_checks")
            ),
        },
    }


def project_run_summary(value: Any) -> dict[str, Any]:
    summary = _dict(value)
    if not summary:
        return {}
    best_metrics = _dict(summary.get("best_metrics"))
    solver_evidence = _dict(best_metrics.get("solver_evidence"))
    diagnostics = _dict(solver_evidence.get("diagnostics"))
    selected_diagnostics = {
        key: diagnostics.get(key)
        for key in (
            "instance_name",
            "job_count",
            "machine_count",
            "operation_count",
            "seed",
            "time_limit_sec",
            "deadline_hit",
            "fallback_used",
            "selected_source",
            "decode_attempts",
            "validate_attempts",
            "best_updates",
            "assignment_fingerprint_count",
            "order_fingerprint_count",
            "beam_width_used",
            "beam_branch_width_used",
            "beam_layers",
            "beam_state_expansions",
            "beam_retained_states",
            "beam_pruned_states",
            "beam_duplicate_pruned",
            "beam_complete_candidates",
            "gap_insertion_count",
        )
        if key in diagnostics
    }
    projected_best = {
        key: best_metrics.get(key)
        for key in (
            "makespan",
            "scheduled_operations",
            "operation_count",
            "best_known_makespan",
            "gap_pct",
        )
        if key in best_metrics
    }
    if solver_evidence:
        projected_best["solver_evidence"] = {
            "reported_makespan": solver_evidence.get("reported_makespan"),
            "reported_operation_count": solver_evidence.get("reported_operation_count"),
            "diagnostics": selected_diagnostics,
        }
    candidate_summaries = [
        dict(item) for item in summary.get("candidate_summaries") or [] if isinstance(item, dict)
    ][:4]
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
        "best_metrics": projected_best,
        "candidate_summaries": candidate_summaries,
        "validation_summary": summary.get("validation_summary") or {},
    }


def project_latest_attempt(value: Any) -> dict[str, Any]:
    attempt = _dict(value)
    if not attempt:
        return {}
    return {
        key: attempt.get(key)
        for key in (
            "attempt_index",
            "status",
            "worker_status",
            "failure_signatures",
            "repair_targets",
            "mechanism_activation",
            "algorithm_semantic_review",
        )
        if key in attempt
    }


def compact_direction_selection_memory(values: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        evidence = _dict(item.get("evidence"))
        result.append(
            {
                "lesson_type": item.get("lesson_type"),
                "problem_family": item.get("problem_family"),
                "strategy": str(item.get("strategy") or "")[:160],
                "strategy_type": item.get("strategy_type"),
                "outcome": item.get("outcome"),
                "applicability": _bounded_strings(item.get("applicability"), limit=4, chars=300),
                "contraindications": _bounded_strings(item.get("contraindications"), limit=4, chars=300),
                "evidence": {
                    "direction_id": evidence.get("direction_id"),
                    "round_index": evidence.get("round_index"),
                    "decision": evidence.get("decision"),
                    "status": evidence.get("status"),
                    "score_relation": evidence.get("score_relation"),
                },
                "confidence": item.get("confidence"),
            }
        )
    return result


def compact_method_package_candidates(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in catalog.get("packages") or []:
        if not isinstance(item, dict):
            continue
        implementation_asset = str(item.get("implementation_asset") or "")
        asset_records: list[dict[str, Any]] = []
        for raw_path in item.get("assets") or []:
            path = Path(str(raw_path))
            if not path.is_file() or str(path) == implementation_asset:
                continue
            try:
                snippet = path.read_text(encoding="utf-8")[:1_500]
            except OSError:
                continue
            asset_records.append({"path": str(path), "snippet": snippet})
            if len(asset_records) >= 2:
                break
        contract = _dict(item.get("implementation_contract"))
        bounded_contract = compact_json(contract, max_chars=6_000).payload if contract else {}
        result.append(
            {
                "package_id": item.get("package_id"),
                "title": item.get("title"),
                "description": item.get("description"),
                "activation_tags": item.get("activation_tags") or [],
                "strategy_types": item.get("strategy_types") or [],
                "implementation_contract": bounded_contract,
                "planning_assets": asset_records,
            }
        )
    return result[:3]


def build_artifact_index(
    *,
    incumbent_files: Any,
    recent_rounds: list[dict[str, Any]],
    loop_feedback: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in incumbent_files if isinstance(incumbent_files, list) else []:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or item.get("relative_path")
        if path:
            result.append(_artifact_ref("incumbent_source", path, sha256=item.get("sha256")))
    for item in recent_rounds:
        for kind, key in (
            ("round_dir", "cycle_dir"),
            ("patch", "patch_path"),
            ("delta", "delta_path"),
            ("context_packet", "context_packet_path"),
        ):
            if item.get(key):
                result.append(_artifact_ref(kind, item[key]))
    for key in ("hypothesis_graph_path", "experience_memory_path", "loop_result_path"):
        if loop_feedback.get(key):
            result.append(_artifact_ref(key.removesuffix("_path"), loop_feedback[key]))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in result:
        marker = (str(item.get("kind") or ""), str(item.get("path") or ""))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
        if len(deduped) >= 16:
            break
    return deduped


def round_artifact_refs(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("cycle_dir", "patch_path", "delta_path", "context_packet_path")
        if item.get(key)
    }


def finalize_planning_packet(payload: dict[str, Any]) -> dict[str, Any]:
    """Fit optional sections while refusing to truncate protected facts."""

    packet = json.loads(json.dumps(payload, ensure_ascii=False))
    protected_paths = _dict(packet.get("packet_completeness")).get("protected_sections") or []
    protected_payload = {
        path: _json_pointer(packet, path)
        for path in protected_paths
    }
    protected_chars = len(json.dumps(protected_payload, ensure_ascii=False))
    if protected_chars > PLANNING_PACKET_MAX_CHARS:
        raise PlanningPacketBudgetError(
            f"protected Planning Packet facts exceed {PLANNING_PACKET_MAX_CHARS} chars: {protected_chars}"
        )

    target_chars = (
        IMPLEMENTATION_PACKET_TARGET_CHARS
        if packet.get("planning_stage") == "implementation_planning"
        else PLANNING_PACKET_MAX_CHARS
    )
    _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > target_chars:
        _strip_record_snippets(packet.get("strategy_selection_cards"), limit=600)
        implementation_stage = packet.get("planning_stage") == "implementation_planning"
        _strip_record_snippets(
            _dict(packet.get("active_direction_knowledge")).get("cards"),
            limit=350 if implementation_stage else 600,
        )
        for package in packet.get("eligible_method_packages") or []:
            if isinstance(package, dict):
                _strip_record_snippets(
                    package.get("planning_assets"),
                    limit=300 if implementation_stage else 600,
                )
        _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > target_chars:
        audit = _dict(packet.get("incumbent_capability_audit"))
        for item in audit.get("files") or []:
            if not isinstance(item, dict):
                continue
            item["configurations"] = list(item.get("configurations") or [])[:12]
            item["loops"] = list(item.get("loops") or [])[:12]
            item["functions"] = list(item.get("functions") or [])[:8]
            item["classes"] = list(item.get("classes") or [])[:4]
            item["internal_call_edges"] = list(item.get("internal_call_edges") or [])[:8]
        for round_item in packet.get("recent_round_evidence") or []:
            if not isinstance(round_item, dict):
                continue
            round_item["candidate_summary"] = {}
            for candidate in _dict(round_item.get("competition_result")).get("candidates") or []:
                if isinstance(candidate, dict):
                    candidate["summary"] = {}
        _attach_budget_report(packet, protected_chars=protected_chars)
    if (
        packet.get("planning_stage") == "implementation_planning"
        and _serialized_chars(packet) > target_chars
    ):
        _remove_record_snippets(_dict(packet.get("active_direction_knowledge")).get("cards"))
        for package in packet.get("eligible_method_packages") or []:
            if isinstance(package, dict):
                _remove_record_snippets(package.get("planning_assets"))
        audit = _dict(packet.get("incumbent_capability_audit"))
        for item in audit.get("files") or []:
            if not isinstance(item, dict):
                continue
            item["configurations"] = list(item.get("configurations") or [])[:8]
            item["loops"] = list(item.get("loops") or [])[:8]
            item["functions"] = list(item.get("functions") or [])[:6]
            item["classes"] = list(item.get("classes") or [])[:3]
            item["internal_call_edges"] = list(item.get("internal_call_edges") or [])[:6]
        _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > target_chars:
        compact_implementation_history(packet)
        _record_packet_degradation(packet, "prior_recent_rounds_compacted")
        _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > target_chars:
        _compact_latest_round_for_budget(packet)
        _record_packet_degradation(packet, "latest_round_candidate_details_compacted")
        _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > target_chars:
        _compact_optional_planning_context(packet)
        _record_packet_degradation(packet, "optional_audit_and_cards_compacted")
        _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > target_chars:
        _skeletonize_recent_rounds(packet)
        _record_packet_degradation(packet, "recent_rounds_skeletonized")
        _attach_budget_report(packet, protected_chars=protected_chars)
    if _serialized_chars(packet) > PLANNING_PACKET_MAX_CHARS:
        _drop_unprotected_root_sections_for_budget(
            packet,
            protected_paths=protected_paths,
            protected_chars=protected_chars,
        )
    final_chars = _serialized_chars(packet)
    if final_chars > PLANNING_PACKET_MAX_CHARS:
        raise PlanningPacketBudgetError(
            f"Planning Packet exceeds {PLANNING_PACKET_MAX_CHARS} chars after optional projections: {final_chars}"
        )
    return packet


def _drop_unprotected_root_sections_for_budget(
    packet: dict[str, Any],
    *,
    protected_paths: list[str],
    protected_chars: int,
) -> None:
    """Guarantee the hard limit by dropping only unprotected root sections."""

    protected_roots = {
        path.strip("/").split("/", 1)[0].replace("~1", "/").replace("~0", "~")
        for path in protected_paths
        if path.strip("/")
    }
    always_keep = {
        "schema_version",
        "planning_stage",
        "phase",
        "direction_id",
        "packet_completeness",
        "packet_budget",
    }
    removable = [
        key
        for key in packet
        if key not in protected_roots and key not in always_keep
    ]
    removable.sort(key=lambda key: _serialized_chars(packet.get(key)), reverse=True)
    removed: list[str] = []
    for key in removable:
        if _serialized_chars(packet) <= PLANNING_PACKET_MAX_CHARS:
            break
        packet.pop(key, None)
        removed.append(key)
    if removed:
        _record_packet_degradation(packet, "unprotected_root_sections_dropped")
        completeness = _dict(packet.get("packet_completeness"))
        completeness["dropped_optional_sections"] = removed
        packet["packet_completeness"] = completeness
        _attach_budget_report(packet, protected_chars=protected_chars)


def planning_packet_text(packet: dict[str, Any]) -> str:
    """Serialize exactly the attachment representation covered by the budget."""

    text = _planning_packet_json_text(packet)
    if len(text) > PLANNING_PACKET_MAX_CHARS:
        raise PlanningPacketBudgetError(
            f"serialized Planning Packet attachment exceeds {PLANNING_PACKET_MAX_CHARS} chars: {len(text)}"
        )
    return text


def planning_packet_bundle_files(packet: dict[str, Any], *, stem: str) -> dict[str, str]:
    """Provide a pageable attachment bundle beside the bounded root packet."""

    root_name = f"{stem}.json"
    index_name = f"{stem}.index.json"
    sections = _planning_packet_sections(packet, stem=stem)
    bundle = {root_name: planning_packet_text(packet)}
    index_payload = {
        "schema_version": 1,
        "planning_stage": packet.get("planning_stage"),
        "root_file": root_name,
        "section_count": len(sections),
        "sections": [
            {
                "key": section["key"],
                "file": section["file"],
                "description": section["description"],
                "chars": len(_planning_packet_json_text(section["payload"])),
                "json_pointer": section["json_pointer"],
            }
            for section in sections
        ],
    }
    bundle[index_name] = _planning_packet_json_text(index_payload)
    for section in sections:
        bundle[section["file"]] = _planning_packet_json_text(section["payload"])
    return bundle


def _attach_budget_report(packet: dict[str, Any], *, protected_chars: int) -> None:
    packet.pop("packet_budget", None)
    report = {
        "schema_version": 1,
        "limit_chars": PLANNING_PACKET_MAX_CHARS,
        "serialized_chars": 0,
        "utf8_bytes": 0,
        "estimated_tokens": 0,
        "estimate_source": "conservative_ascii4_non_ascii1",
        "protected_chars": protected_chars,
        "protected_complete": True,
        "degradation_steps": list(
            _dict(packet.get("packet_completeness")).get("degradation_steps") or []
        ),
    }
    packet["packet_budget"] = report
    # The report is part of the serialized packet, so its own digit widths can
    # change the totals. Iterate to a fixed point instead of reporting the size
    # of a pre-report payload.
    for _ in range(12):
        text = _planning_packet_json_text(packet)
        updated = {
            "serialized_chars": len(text),
            "utf8_bytes": len(text.encode("utf-8")),
            "estimated_tokens": _estimate_tokens(text),
        }
        if all(report[key] == value for key, value in updated.items()):
            break
        report.update(updated)


def _record_packet_degradation(packet: dict[str, Any], step: str) -> None:
    completeness = _dict(packet.get("packet_completeness"))
    steps = [str(item) for item in completeness.get("degradation_steps") or []]
    if step not in steps:
        steps.append(step)
    completeness["degradation_steps"] = steps
    packet["packet_completeness"] = completeness


def _compact_latest_round_for_budget(packet: dict[str, Any]) -> None:
    rounds = packet.get("recent_round_evidence")
    if not isinstance(rounds, list) or not rounds or not isinstance(rounds[-1], dict):
        return
    latest = rounds[-1]
    latest["candidate_summary"] = {}
    competition = _dict(latest.get("competition_result"))
    compact_candidates = []
    for candidate in competition.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        diagnostics = _dict(candidate.get("diagnostics"))
        semantic = _dict(diagnostics.get("semantic_review"))
        compact_candidates.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "status": candidate.get("status"),
                "model": candidate.get("model"),
                "objective": candidate.get("objective") or [],
                "mechanism_activation": project_mechanism_activation(
                    candidate.get("mechanism_activation")
                ),
                "diagnostics": {
                    "eligible": diagnostics.get("eligible"),
                    "core_eligible": diagnostics.get("core_eligible"),
                    "semantic_eligible": diagnostics.get("semantic_eligible"),
                    "activation_eligible": diagnostics.get("activation_eligible"),
                    "worker_status": diagnostics.get("worker_status"),
                    "semantic_review": {
                        key: semantic.get(key)
                        for key in ("status", "accepted", "summary")
                        if key in semantic
                    },
                },
                "patch_path": candidate.get("patch_path"),
            }
        )
    competition["candidates"] = compact_candidates[:4]
    latest["competition_result"] = competition
    reflection = _dict(latest.get("round_reflection"))
    reflection["candidate_findings"] = list(reflection.get("candidate_findings") or [])[:2]
    reflection["reasoning_trace"] = []
    latest["round_reflection"] = reflection


def _compact_optional_planning_context(packet: dict[str, Any]) -> None:
    audit = _dict(packet.get("incumbent_capability_audit"))
    compact_files = []
    for item in audit.get("files") or []:
        if not isinstance(item, dict):
            continue
        compact_files.append(
            {
                key: item.get(key)
                for key in ("relative_path", "path", "parse_status", "sha256", "chars")
                if key in item
            }
            | {
                "function_names": [
                    str(row.get("name") or "")[:160]
                    for row in item.get("functions") or []
                    if isinstance(row, dict) and row.get("name")
                ][:12],
                "class_names": [
                    str(row.get("name") or "")[:160]
                    for row in item.get("classes") or []
                    if isinstance(row, dict) and row.get("name")
                ][:6],
            }
        )
    packet["incumbent_capability_audit"] = {
        "schema_version": audit.get("schema_version"),
        "source": audit.get("source"),
        "summary": audit.get("summary") or {},
        "files": compact_files[:6],
        "limitations": _bounded_strings(audit.get("limitations"), limit=4, chars=300),
    }
    for card in packet.get("strategy_selection_cards") or []:
        if not isinstance(card, dict):
            continue
        for key in list(card):
            if key not in {"path", "title", "tags", "source", "snippet"}:
                card.pop(key, None)
        if "snippet" in card:
            card["snippet"] = str(card.get("snippet") or "")[:240]


def _skeletonize_recent_rounds(packet: dict[str, Any]) -> None:
    rounds = packet.get("recent_round_evidence")
    if not isinstance(rounds, list):
        return
    skeletons = []
    for item in rounds:
        if not isinstance(item, dict):
            continue
        competition = _dict(item.get("competition_result"))
        skeletons.append(
            {
                key: item.get(key)
                for key in (
                    "round_index",
                    "decision",
                    "candidate_key",
                    "incumbent_key_after",
                    "direction_id",
                    "title",
                    "method_family",
                    "method_families",
                    "strategy_type",
                    "experiment_stage",
                    "activation_checks",
                    "promotion_check",
                    "artifact_refs",
                )
                if key in item
            }
            | {
                "hypothesis": str(item.get("hypothesis") or "")[:300],
                "competition_result": {
                    key: competition.get(key)
                    for key in (
                        "status",
                        "candidate_count",
                        "eligible_candidate_count",
                        "selected_candidate_id",
                        "selected_objective_key",
                        "measured_candidate_id",
                        "measured_objective_key",
                        "selected_for_promotion",
                    )
                    if key in competition
                },
                "round_reflection": project_round_reflection(item.get("round_reflection")),
            }
        )
    packet["recent_round_evidence"] = skeletons
    latest = _dict(packet.get("latest_evidence"))
    latest["complete"] = False
    latest["projection"] = "skeleton"
    packet["latest_evidence"] = latest


def _estimate_tokens(text: str) -> int:
    ascii_count = sum(ord(char) < 128 for char in text)
    return (ascii_count + 3) // 4 + (len(text) - ascii_count)


def _serialized_chars(value: Any) -> int:
    return len(_planning_packet_json_text(value))


def _planning_packet_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _planning_packet_sections(packet: dict[str, Any], *, stem: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, (key, description) in enumerate(PLANNING_PACKET_SECTION_SPECS, start=1):
        payload = _planning_packet_section_payload(packet, key)
        if not payload:
            continue
        sections.append(
            {
                "key": key,
                "description": description,
                "file": f"{stem}.sections/{index:02d}_{key}.json",
                "json_pointer": f"/sections/{key}",
                "payload": payload,
            }
        )
    return sections


def _planning_packet_section_payload(packet: dict[str, Any], key: str) -> dict[str, Any]:
    if key == "overview":
        return {
            "schema_version": packet.get("schema_version"),
            "planning_stage": packet.get("planning_stage"),
            "phase": packet.get("phase"),
            "direction_id": packet.get("direction_id"),
            "packet_budget": packet.get("packet_budget") or {},
            "packet_completeness": packet.get("packet_completeness") or {},
        }
    if key == "task_io":
        return {
            "task_digest": packet.get("task_digest") or {},
            "io_digest": packet.get("io_digest") or {},
            "runtime_limits": packet.get("runtime_limits") or {},
            "planner_output_contract": packet.get("planner_output_contract") or {},
        }
    if key == "instance_and_catalogs":
        return {
            name: packet.get(name) or {}
            for name in (
                "instance_diagnostics",
                "strategy_selection_cards",
                "knowledge_query_catalog",
                "method_family_catalog",
                "method_package_catalog",
            )
            if packet.get(name) not in (None, [], {})
        }
    if key == "research_state":
        return {"research_state": packet.get("research_state") or {}}
    if key == "incumbent":
        return {
            name: packet.get(name) or {}
            for name in ("incumbent_evidence", "incumbent_capability_audit")
            if packet.get(name) not in (None, [], {})
        }
    if key == "evidence_history":
        return {
            name: packet.get(name) or {}
            for name in (
                "recent_round_evidence",
                "latest_evidence",
                "historical_aggregates",
                "latest_attempt_evidence",
            )
            if packet.get(name) not in (None, [], {})
        }
    if key == "direction_context":
        return {
            name: packet.get(name) or {}
            for name in (
                "direction_selection",
                "active_direction_knowledge",
                "eligible_method_packages",
            )
            if packet.get(name) not in (None, [], {})
        }
    if key == "control_context":
        return {
            name: packet.get(name) or {}
            for name in (
                "next_round_guidance",
                "user_intervention",
                "direction_patch_contract",
                "validated_memory",
                "artifact_index",
            )
            if packet.get(name) not in (None, [], {})
        }
    return {}


def _strip_record_snippets(value: Any, *, limit: int) -> None:
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("snippet"), str):
            continue
        snippet = item["snippet"]
        if len(snippet) > limit:
            item["snippet"] = snippet[:limit]
            item["snippet_compacted_for_budget"] = True


def _remove_record_snippets(value: Any) -> None:
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.pop("snippet", None), str):
            item["snippet_compacted_for_budget"] = True


def _json_pointer(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.strip("/").split("/") if path.strip("/") else []:
        if not isinstance(current, dict):
            return None
        current = current.get(part.replace("~1", "/").replace("~0", "~"))
    return current


def _historical_boundary_refs(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rounds:
        return []
    selected = rounds if len(rounds) <= 2 else [rounds[0], rounds[-1]]
    refs = []
    for item in selected:
        path = item.get("cycle_dir") or item.get("context_packet_path")
        if path:
            refs.append(_artifact_ref("historical_round_boundary", path))
    return refs


def _artifact_ref(kind: str, path_value: Any, *, sha256: Any = None) -> dict[str, Any]:
    path_text = str(path_value or "")
    path = Path(path_text)
    result = {
        "kind": kind,
        "path": path_text,
        "exists": path.exists(),
    }
    if sha256:
        result["sha256"] = sha256
    if path.exists() and path.is_file():
        try:
            result["bytes"] = path.stat().st_size
        except OSError:
            pass
    return result


def _bounded_counter(value: Counter[str], *, limit: int = 12) -> dict[str, int]:
    return {
        key: count
        for key, count in sorted(value.items(), key=lambda item: (-item[1], item[0]))[:limit]
    }


def _bounded_strings(value: Any, *, limit: int, chars: int) -> list[str]:
    result: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = str(item).strip()
        if text and text not in result:
            result.append(text[:chars])
        if len(result) >= limit:
            break
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
