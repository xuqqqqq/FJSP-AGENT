"""从需求、IO 和实例特征生成 solver 能力自检契约，不提供实现代码。"""

from __future__ import annotations

import json
import re
from typing import Any


def build_agent_generated_solver_quality_contract(context: dict[str, Any]) -> dict[str, Any]:
    """从当前任务特征生成 Agent 自写 solver 的最低能力清单。

    该清单描述 parser、表示、合法 decoder、运行边界等工程能力，不提供
    具体搜索算法。变种能力只根据当前需求/IO/算例激活，不能因知识库支持
    某变种就误判当前算例也具有该约束。它要求生成 solver 在 Core 花费正式
    evaluator 时间前，用源码证明这些不变量确实存在。
    """

    if not isinstance(context, dict):
        return {"enabled": False, "reason": "context_unavailable"}
    if not is_agent_generated_solver_context(context):
        return {"enabled": False, "reason": "not_agent_generated_solver_context"}
    if not is_real_fjsp_solver_context(context):
        return {"enabled": False, "reason": "not_fjsp_solver_context"}

    runtime_features = build_solver_runtime_feature_contract(context)
    features = set(runtime_features["active_features"])
    required = [
        "standalone_cli_interface",
        "active_io_parser",
        "declared_output_schema",
        "stable_operation_identity",
        "operation_level_ready_list_constructor",
        "complete_schedule_coverage_guard",
        "machine_eligibility_guard",
        "processing_duration_guard",
        "job_precedence_guard",
        "machine_non_overlap_guard",
        "bounded_runtime_or_iteration_guard",
        "incumbent_preservation_on_failed_candidate",
    ]
    variant_required = list(runtime_features["variant_required_code_capabilities"])
    return {
        "enabled": True,
        "problem_family": "fjsp",
        "active_features": sorted(features),
        "required_code_capabilities": required,
        "variant_required_code_capabilities": variant_required,
        "capability_playbook": capability_playbook(
            required + variant_required,
            active_features=features,
        ),
        "quality_rule": (
            "Generated solver code must first satisfy the invariant contract "
            "implied by requirement/IO/diagnostics, then mutate one bounded "
            "rule or operator.  The backend supplies no solver implementation."
        ),
        "baseline_generation_rule": (
            "When creating the first generated solver, write a standalone parser, "
            "stable operation representation, complete schedule builder/decoder, "
            "self-checks for the active constraints, and a bounded seeded search."
        ),
        "improvement_rule": (
            "When an incumbent exists, preserve its parser and valid skeleton.  "
            "If the incumbent lacks one required capability, repair that missing "
            "capability before adding another heuristic idea."
        ),
    }


def build_solver_runtime_feature_contract(context: dict[str, Any]) -> dict[str, list[str]]:
    """Return active variant requirements regardless of solver baseline source."""

    features = extract_variant_features(context) if isinstance(context, dict) else set()
    variant_required: list[str] = []
    if "sequence_dependent_setup" in features:
        variant_required.extend(
            [
                "setup_aware_machine_arc_timing",
                "setup_aware_full_decoder_for_sequence_moves",
            ]
        )
    if "no_wait" in features:
        variant_required.append("no_wait_start_time_guard")
    if "maximum_time_lag" in features:
        variant_required.append("maximum_time_lag_parser_and_propagation_guard")
    elif "minimum_time_lag" in features:
        variant_required.append("minimum_time_lag_parser_and_propagation_guard")
    elif "time_lag" in features:
        variant_required.append("time_lag_precedence_guard")
    if "alternative_path" in features:
        variant_required.append("alternative_path_route_selection_guard")
    if "machine_calendar" in features:
        variant_required.append("machine_calendar_availability_guard")
    if "batching" in features:
        variant_required.extend(
            [
                "batch_capacity_guard",
                "batch_family_compatibility_guard",
                "parallel_batch_timing_guard",
            ]
        )
    if "transportation" in features:
        variant_required.append("transport_time_guard")
    if "machine_transport" in features:
        variant_required.append("machine_transport_matrix_guard")
    if "operation_setup" in features:
        variant_required.append("operation_setup_occupancy_guard")
    if "cross_job_precedence" in features:
        variant_required.append("cross_job_precedence_guard")
    if "release_time" in features:
        variant_required.append("release_time_parser_and_job_ready_guard")
    if "machine_initial_availability" in features:
        variant_required.append("machine_initial_availability_guard")
    if "reentrant_route" in features or "loop_expansion" in features:
        if "fjsp_cell_sdst_transport_tardiness" in features:
            variant_required.append("explicit_reentrant_operation_identity_guard")
        else:
            variant_required.append("reentrant_loop_parser_and_expansion_guard")
    if "release_dates" in features:
        variant_required.append("release_date_guard")
    if "due_dates" in features:
        variant_required.append("due_date_or_tardiness_objective_guard")
    if "multi_objective" in features:
        variant_required.append("declared_objective_priority_guard")

    return {
        "active_features": sorted(features),
        "variant_required_code_capabilities": variant_required,
    }


def capability_playbook(capabilities: list[str], *, active_features: set[str]) -> list[dict[str, str]]:
    """为每项能力提供“应看到什么证据、缺失时怎么修”的提示资料。"""

    playbook: list[dict[str, str]] = []
    for name in capabilities:
        spec = _CAPABILITY_PLAYBOOK.get(name)
        if spec is None:
            spec = {
                "evidence": f"Show where `{name}` is implemented for the active IO contract.",
                "repair": f"Add or repair `{name}` before optimizing the objective.",
            }
        playbook.append(
            {
                "name": name,
                "evidence": spec["evidence"],
                "repair": spec["repair"],
            }
        )
    if "sequence_dependent_setup" not in active_features:
        playbook = [item for item in playbook if not item["name"].startswith("setup_aware")]
    return playbook


_CAPABILITY_PLAYBOOK = {
    "standalone_cli_interface": {
        "evidence": "Cite the main/argparse path that accepts --input, --output, and --seed, then writes the solution file.",
        "repair": "Add a runnable script entrypoint before adding or tuning any heuristic rule.",
    },
    "active_io_parser": {
        "evidence": "Cite the parser function and loops that read the active instance file and derive every job, operation, candidate machine, duration, and variant datum.",
        "repair": "Implement parsing from the IO document first; do not read the file and then hardcode op_info, machine_sequences, toy schedules, or previous solution files.",
    },
    "declared_output_schema": {
        "evidence": "Cite the JSON writer that emits the declared schedule format and job_id/op_id/machine_id/start/end fields.",
        "repair": "Build the output object from decoded schedule records and write it through the configured --output path.",
    },
    "stable_operation_identity": {
        "evidence": "Cite the single operation key representation, preferably (job_id, op_id), used across op_info, assignment, sequences, decode, and output.",
        "repair": "Normalize operation identity before adding search; avoid mixing global ids, tuples, and schedule dictionaries.",
    },
    "operation_level_ready_list_constructor": {
        "evidence": "Cite the constructor that maintains one ready next operation per unfinished job, evaluates eligible machines for each ready operation, and uses a seeded tie-break or multi-start rule.",
        "repair": "Replace fixed job-by-job construction with an operation-level ready list that scores all ready operations and eligible machines before committing one operation. Selecting one ready operation and then calling rng.choice(eligible) is not sufficient.",
    },
    "complete_schedule_coverage_guard": {
        "evidence": "Cite the check that decoded/output schedule covers every expected operation exactly once and rejects duplicates/missing ops.",
        "repair": "Add expected-op and seen-op guards; never score a partial or empty decoded schedule.",
    },
    "machine_eligibility_guard": {
        "evidence": "Cite the check that every assigned machine belongs to the operation's eligible/candidate machine set.",
        "repair": "Validate eligibility before scheduling and after each move; reject infeasible candidates.",
    },
    "processing_duration_guard": {
        "evidence": "Cite the check that every output interval satisfies end - start equals the selected machine processing time.",
        "repair": "Use the parsed processing time for the chosen machine when computing end times and self-checking output.",
    },
    "job_precedence_guard": {
        "evidence": "Cite the job-ready/predecessor logic that prevents op k from starting before op k-1 completes.",
        "repair": "Decode schedules with job predecessor readiness and reject deadlocks instead of returning partial schedules.",
    },
    "machine_non_overlap_guard": {
        "evidence": (
            "Cite either the machine-sequence decoder that enforces each predecessor machine arc, or the output "
            "self-check that groups records by machine, sorts each group by (start, end, job_id, op_id), and checks "
            "adjacent intervals. Schedule record order is not a resource-order guarantee."
        ),
        "repair": (
            "Decode each explicit machine sequence from scratch. For output validation, group records by machine "
            "and sort by time before checking adjacent intervals; never update machine_last_end while traversing "
            "jobs/operations because gap-inserted records need not be machine-chronological."
        ),
    },
    "bounded_runtime_or_iteration_guard": {
        "evidence": "Cite the CLI/runtime budget, shared deadline, inner candidate-loop checks, max restarts/iterations, and neighborhood shortlist or cap.",
        "repair": "Accept the harness time-limit argument, reserve exit headroom, and check the shared deadline inside nested candidate scans as well as outer search loops.",
    },
    "incumbent_preservation_on_failed_candidate": {
        "evidence": "Cite the transactional clone/snapshot path where failed or infeasible candidates are discarded without mutating current/best, and the incumbent changes only after strict improvement.",
        "repair": "Apply moves to a clone or fully roll back every mutation; keep current and best unchanged when decode fails, times out, or produces no strict improvement.",
    },
    "setup_aware_machine_arc_timing": {
        "evidence": "Cite the setup lookup applied between adjacent operations on the same machine during start-time computation.",
        "repair": "Treat setup as machine arc time before the current operation; do not optimize setup alone as the objective.",
    },
    "setup_aware_full_decoder_for_sequence_moves": {
        "evidence": "Cite the decoder that rebuilds all start/end times from assignment and machine sequences under setup constraints.",
        "repair": "Run every sequence/neighborhood candidate through the setup-aware full decoder before comparing makespan.",
    },
    "no_wait_start_time_guard": {
        "evidence": "Cite the guard that forces each no-wait successor to start exactly when its job predecessor finishes.",
        "repair": "Add no-wait predecessor timing checks to construction, decode, and output self-check before scoring candidates.",
    },
    "time_lag_precedence_guard": {
        "evidence": "Cite the min/max lag checks that bound successor start times relative to predecessor completion.",
        "repair": "Parse lag data from the active IO contract and enforce it during decode and schedule validation.",
    },
    "minimum_time_lag_parser_and_propagation_guard": {
        "evidence": "Cite the K + K*4 tail parser, the adjacent-pair lag lookup, and the reachable construction/full-decode path that applies successor_start >= predecessor_end + L_min.",
        "repair": "Parse every declared adjacent-pair minimum lag and propagate it through construction, full decoding, move evaluation, and output self-check before comparing makespan.",
    },
    "maximum_time_lag_parser_and_propagation_guard": {
        "evidence": "Cite the K + K*4 tail parser, sparse possibly non-adjacent upper-bound lookup, and reachable construction/full-decode path that enforces successor_start <= predecessor_end + L_max. Exact lanes must cite the actual CP-SAT constraint and solve/extraction evidence.",
        "repair": "Parse every declared maximum lag without assuming adjacency; validate each complete candidate after propagation or full redecode. Do not use successor right-shift as a generic repair because it can increase an already bounded gap.",
    },
    "alternative_path_route_selection_guard": {
        "evidence": "Cite the complete per-job K route-tail parser, original route id 0 plus alternatives, selected_routes output, dynamic operation-set validation, and route-order precedence. Search must expose a route-changing mechanism; exact lanes must cite one-hot route choice, conditional operation presence/precedence, and actual CP-SAT execution evidence.",
        "repair": "Parse every job's full route tail, choose exactly one route, schedule exactly that route's operation sequence, and full-redecode after route changes. Do not confuse route choice with ordinary machine flexibility or emit every operation in the original pool.",
    },
    "machine_calendar_availability_guard": {
        "evidence": "Cite the downtime-tail parser, normalized per-machine calendar, non-preemptive complete-gap placement used by every candidate path, and output self-check against every original window on the selected machine. CP paths must also cite fixed maintenance intervals included in machine NoOverlap.",
        "repair": "Parse every downtime window, share one calendar representation across construction and all move decoders, place each operation wholly in an available gap, and reject candidates intersecting any selected-machine window. Do not claim calendar awareness from append-only machine-ready logic.",
    },
    "batch_capacity_guard": {
        "evidence": "Cite the check that every batch respects capacity, family/compatibility, and operation coverage constraints.",
        "repair": "Track batch membership and capacity during construction/decode; reject over-capacity or incompatible batches.",
    },
    "batch_family_compatibility_guard": {
        "evidence": "Cite the parsed family id for every job and the check that all operations in one batch belong to exactly one family.",
        "repair": "Carry job-family identity into batch formation and reject every mixed-family batch; family is compatibility, not precedence or priority.",
    },
    "parallel_batch_timing_guard": {
        "evidence": "Cite the shared batch start/end, max-member-processing-time duration rule, and machine non-overlap check over collapsed batch intervals.",
        "repair": "Decode each batch as one machine activity whose members start and end together and whose duration is the maximum selected processing time; never apply ordinary per-operation overlap inside a batch.",
    },
    "transport_time_guard": {
        "evidence": "Cite the transport/travel-time transition added between consecutive operations when machines or locations change.",
        "repair": "Parse transport data and add it to job readiness before scheduling the successor operation.",
    },
    "machine_transport_matrix_guard": {
        "evidence": "Cite the machine-pair transport lookup used on every within-job and cross-job precedence arc after both endpoint machines are selected.",
        "repair": "Parse the full transport matrix and recompute both incoming and outgoing transport whenever a machine assignment changes.",
    },
    "operation_setup_occupancy_guard": {
        "evidence": "Cite the operation-machine setup calculation and the machine-capacity check that reserves setup immediately before processing, including the first operation on a machine.",
        "repair": "Treat operation setup as machine occupancy before the current operation; do not model it as sequence-dependent setup or omit it for the first machine operation.",
    },
    "cross_job_precedence_guard": {
        "evidence": "Cite the parsed cross-job DAG and the readiness rule requiring every predecessor job's last operation plus transport before the successor job's first operation.",
        "repair": "Topologically propagate all BOM edges and reject cycles or schedules that treat hard job precedence as a soft priority score.",
    },
    "release_time_parser_and_job_ready_guard": {
        "evidence": "Cite the release-time tail parser and the construction/full-decode path that prevents each job's first operation from starting before its parsed release time.",
        "repair": "Parse every job release time, initialize job readiness from it, and preserve the lower bound in construction, full decoding, move evaluation, and output self-checks.",
    },
    "machine_initial_availability_guard": {
        "evidence": "Cite the machine-availability tail parser and the construction/full-decode path that prevents selected-machine operations from starting before that machine's initial available time.",
        "repair": "Parse every machine initial available time and apply it as a selected-machine start lower bound in construction, full decoding, move evaluation, and output self-checks.",
    },
    "reentrant_loop_parser_and_expansion_guard": {
        "evidence": "Cite the exact job_count*3 tail parser, loop boundary/repeat validation, pre + body*repeat + post expansion, continuous expanded op_id assignment, and complete expanded-operation output guard.",
        "repair": "Consume every loop triple after the standard body, validate its job-local bounds, expand every pass before construction/search, and require exactly one output record for every expanded (job_id, op_id). Never ignore trailing loop tokens or schedule only the original route.",
    },
    "explicit_reentrant_operation_identity_guard": {
        "evidence": "Cite the parser and schedule coverage checks that retain every already-expanded route visit as a distinct (job_id, op_id), including repeated logical-machine visits.",
        "repair": "Preserve the published full operation row order and assign a unique op_id to every visit; do not infer, collapse, or re-expand a loop tail that is absent from this IO contract.",
    },
    "release_date_guard": {
        "evidence": "Cite the guard that prevents an operation or job from starting before its parsed release time/date.",
        "repair": "Initialize readiness from release dates and validate every output start against the parsed release constraint.",
    },
    "due_date_or_tardiness_objective_guard": {
        "evidence": "Cite the due-date/tardiness calculation and where it enters validation or objective scoring.",
        "repair": "Parse due dates and compute the declared lateness/tardiness term instead of optimizing makespan alone.",
    },
    "declared_objective_priority_guard": {
        "evidence": "Cite the objective-comparison code that applies the declared weights, priority order, or Pareto rule.",
        "repair": "Compare candidates by the IO/requirement objective definition; do not silently fall back to makespan-only scoring.",
    },
}


def is_agent_generated_solver_context(context: dict[str, Any]) -> bool:
    protocol = context.get("evaluator_protocol") if isinstance(context.get("evaluator_protocol"), dict) else {}
    solver_command = str(protocol.get("solver_command_template") or "")
    if "agent_generated" in solver_command.replace("\\", "/"):
        return True
    generation = context.get("baseline_generation") if isinstance(context.get("baseline_generation"), dict) else {}
    if str(generation.get("source") or "") == "agent_generated":
        return True
    instruction_text = json.dumps(context.get("worker_instruction") or {}, ensure_ascii=False)
    return "agent-generated" in instruction_text or "agent_generated" in instruction_text


def is_real_fjsp_solver_context(context: dict[str, Any]) -> bool:
    """Exclude dummy agent-generated smoke contracts from FJSP quality gates."""

    protocol = context.get("evaluator_protocol") if isinstance(context.get("evaluator_protocol"), dict) else {}
    solver_command = str(protocol.get("solver_command_template") or "").replace("\\", "/").lower()
    evaluator_command = str(protocol.get("evaluator_command_template") or "").replace("\\", "/").lower()
    diagnostics = context.get("instance_diagnostics")
    capability = context.get("problem_family_capability")

    if "dummy_evaluator.py" in evaluator_command:
        return False
    if "standard_fjsp_evaluator.py" in evaluator_command:
        return True
    if "agent_generated_fjsp" in solver_command:
        return True
    if isinstance(diagnostics, dict) and diagnostics.get("status") == "available":
        return True
    if isinstance(capability, dict):
        family = str(capability.get("family_id") or capability.get("display_name") or "").lower()
        variants = " ".join(str(item).lower() for item in capability.get("supported_variants") or [])
        if "fjsp" in family or "fjsp" in variants:
            return True
    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    description = f"{task.get('problem_family') or ''} {task.get('description') or ''}".lower()
    return ("fjsp" in description or "flexible job" in description) and (
        "schedule" in evaluator_command or "standard_fjsp" in evaluator_command
    )


def extract_variant_features(context: dict[str, Any]) -> set[str]:
    """提取当前任务真正激活的约束特征，实例诊断优先于文档关键词。"""

    features = {"alternative_machines", "operation_precedence", "machine_capacity", "makespan_objective"}

    diagnostics = context.get("instance_diagnostics") if isinstance(context.get("instance_diagnostics"), dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    setup_kinds = summary.get("setup_time_kinds") if isinstance(summary.get("setup_time_kinds"), list) else []
    diagnostics_available = bool(
        diagnostics.get("status") == "available"
        and (
            int(summary.get("profiled_count") or 0) > 0
            or int(summary.get("instance_count") or 0) > 0
            or diagnostics.get("instances")
        )
    )
    setup_from_diagnostics = (
        int(summary.get("sdst_instance_count") or 0) > 0
        or any(str(kind).lower() not in {"", "none", "null"} for kind in setup_kinds)
        or any(
            str(item.get("variant") or "").lower() == "fjsp_sdst"
            or str(item.get("setup_time_kind") or "").lower() not in {"", "none", "null"}
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    minimum_lag_from_diagnostics = (
        int(summary.get("min_time_lag_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_min_time_lag"
            or int(item.get("min_time_lag_constraint_count") or 0) > 0
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    maximum_lag_from_diagnostics = (
        int(summary.get("max_time_lag_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_max_time_lag"
            or int(item.get("max_time_lag_constraint_count") or 0) > 0
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    alternative_path_from_diagnostics = (
        int(summary.get("alternative_path_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_alternative_path"
            or int(item.get("alternative_route_count") or 0) > 0
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    release_time_from_diagnostics = (
        int(summary.get("release_time_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_release_time"
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    machine_availability_from_diagnostics = (
        int(summary.get("machine_availability_instance_count") or 0) > 0
        or int(summary.get("unavailability_interval_count") or 0) > 0
        or int(summary.get("max_unavailability_interval_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_machine_availability"
            or int(item.get("unavailability_interval_count") or 0) > 0
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    reentrant_from_diagnostics = (
        int(summary.get("reentrant_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_reentrant"
            or int(item.get("reentrant_loop_count") or 0) > 0
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    jpc_tst_from_diagnostics = (
        int(summary.get("jpc_tst_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_jpc_tst"
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    pbpm_from_diagnostics = (
        int(summary.get("pbpm_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower() == "fjsp_pbpm"
            or int(item.get("batch_machine_count") or 0) > 0
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    cell_sdst_transport_from_diagnostics = (
        int(summary.get("cell_sdst_transport_instance_count") or 0) > 0
        or any(
            str(item.get("variant") or "").lower()
            == "fjsp_cell_sdst_transport_tardiness"
            for item in diagnostics.get("instances") or []
            if isinstance(item, dict)
        )
    )
    active_text = _active_problem_feature_text(
        context,
        include_documents=not diagnostics_available,
    )
    if setup_from_diagnostics or (not diagnostics_available and _mentions_sequence_dependent_setup(active_text)):
        features.add("sequence_dependent_setup")
    if minimum_lag_from_diagnostics or (
        not diagnostics_available and _mentions_minimum_time_lag(active_text)
    ):
        features.update({"minimum_time_lag", "time_lag"})
    if maximum_lag_from_diagnostics or (
        not diagnostics_available and _mentions_maximum_time_lag(active_text)
    ):
        features.update({"maximum_time_lag", "time_lag"})
    if alternative_path_from_diagnostics or (
        not diagnostics_available and _mentions_alternative_path(active_text)
    ):
        features.update({"alternative_path", "route_choice"})
    if release_time_from_diagnostics or (
        not diagnostics_available and _mentions_release_time(active_text)
    ):
        features.update({"release_time", "machine_initial_availability"})
    if machine_availability_from_diagnostics or (
        not diagnostics_available and _mentions_machine_availability(active_text)
    ):
        features.update({"machine_availability", "machine_calendar"})
    if reentrant_from_diagnostics or (
        not diagnostics_available and _mentions_reentrant(active_text)
    ):
        features.update({"reentrant_route", "loop_expansion"})
    if jpc_tst_from_diagnostics:
        features.update(
            {
                "fjsp_jpc_tst",
                "cross_job_precedence",
                "machine_transport",
                "operation_setup",
                "multi_feature",
            }
        )
    if pbpm_from_diagnostics or (not diagnostics_available and _mentions_pbpm(active_text)):
        features.update(
            {
                "fjsp_pbpm",
                "batching",
                "parallel_batch_machine",
                "batch_capacity",
                "incompatible_job_families",
            }
        )
    if cell_sdst_transport_from_diagnostics:
        features.update(
            {
                "fjsp_cell_sdst_transport_tardiness",
                "transportation",
                "cell_transport",
                "family_sequence_dependent_setup",
                "reentrant_route",
                "due_dates",
                "multi_objective",
                "total_tardiness",
                "multi_feature",
            }
        )
    if _has_any_pattern(active_text, [r"\bno[-_\s]?wait\b"]):
        features.add("no_wait")
    if _has_any_pattern(active_text, [r"\btime[-_\s]?lag\b"]):
        features.add("time_lag")
    if _has_any_pattern(active_text, [r"\btransport(?:ation)?\b"]):
        features.add("transportation")
    if _has_any_pattern(active_text, [r"\brelease[-_\s]?date(?:s)?\b"]):
        features.add("release_dates")
    if _has_any_pattern(active_text, [r"\bdue[-_\s]?date(?:s)?\b", r"\btardiness\b"]):
        features.add("due_dates")
    if _has_any_pattern(active_text, [r"\bmulti[-_\s]?objective\b"]):
        features.add("multi_objective")
    return features


def _mentions_sequence_dependent_setup(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?sdst\b",
            r"\bsd-st\b",
            r"\bsequence[-_\s]?dependent[-_\s]?setup\b",
            r"\bsetup[-_\s]?matrix\b",
            r"\bsetup[-_\s]?time(?:s)?\b",
        ],
    )


def _mentions_minimum_time_lag(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?min(?:imum)?[-_\s]?time[-_\s]?lag\b",
            r"\bmin(?:imum)?[-_\s]?time[-_\s]?lag\b",
            r"最小时间(?:间隔|滞后)",
            r"最小生产间隔",
        ],
    )


def _mentions_maximum_time_lag(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?(?:max|maximum)[-_\s]?time[-_\s]?lag\b",
            r"\bmax(?:imum)?[-_\s]?time[-_\s]?lag\b",
            r"最大时间(?:间隔|滞后)",
            r"最大生产间隔",
        ],
    )


def _mentions_alternative_path(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?alternative[-_\s]?(?:path|route)\b",
            r"\balternative[-_\s]?(?:process[-_\s]?)?(?:path|route)\b",
            r"替代工艺(?:路径|路线)",
            r"可选工艺(?:路径|路线)",
        ],
    )


def _mentions_release_time(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?release[-_\s]?time\b",
            r"\brelease[-_\s]?time(?:s)?\b",
            r"\bmachine[-_\s]?initial[-_\s]?(?:availability|available[-_\s]?time)\b",
            r"工件释放时间",
            r"机器初始可用",
        ],
    )


def _mentions_machine_availability(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?machine[-_\s]?availability\b",
            r"\bmachine[-_\s]?availability\b",
            r"\bmachine[-_\s]?(?:calendar|unavailability)\b",
            r"\b(?:maintenance|downtime|unavailability|unavailable)\b",
            r"维修",
            r"维护",
            r"停机",
            r"不可用",
        ],
    )


def _mentions_reentrant(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bfjsp[-_]?reentrant\b",
            r"\breentrant[-_\s]?(?:fjsp|route|loop|scheduling)\b",
            r"\bre[-_\s]?entrant\b",
            r"可重入",
        ],
    )


def _mentions_pbpm(text: str) -> bool:
    return _has_any_pattern(
        text,
        [
            r"\bpbpm[-_\s]?(?:fjsp)?\b",
            r"\bfjsp[-_\s]?pbpm\b",
            r"\bparallel[-_\s]?batch(?:[-_\s]?(?:processing[-_\s]?)?machine)?\b",
            r"并行组批",
            r"同族组批",
            r"批处理机",
        ],
    )


def _has_any_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _active_problem_feature_text(context: dict[str, Any], *, include_documents: bool) -> str:
    """Return text that describes the active problem, not reusable capability inventory.

    Domain packs, knowledge tags, and RAG cards often list every supported FJSP
    variant.  Those are retrieval capabilities, not proof that the current
    instance has setup, batching, or another constraint.  When parsed instance
    diagnostics are available, they are the source of truth for active variant
    features.
    """

    keys = [
        "task",
        "evaluator_protocol",
        "hypothesis",
        "contract_review_evidence",
        "instance_diagnostics",
    ]
    subset = {key: context.get(key) for key in keys if key in context}
    if include_documents:
        value = context.get("documents")
        if isinstance(value, list):
            subset["documents"] = [
                {
                    "path": item.get("path"),
                    "snippet": item.get("snippet"),
                }
                for item in value[:4]
                if isinstance(item, dict)
            ]
    return json.dumps(subset, ensure_ascii=False).lower()
