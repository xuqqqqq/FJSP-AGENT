from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge_registry import auto_knowledge_cards
from .models import TaskContract
from .problem_families import get_problem_family
from .slot_contract import ResolvedCodeSlot
from .slot_manifest import load_slot_manifest
from .standard_fjsp import parse_standard_fjsp


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
    contract = TaskContract.load(request.contract_path)
    contract_raw = json.loads(request.contract_path.read_text(encoding="utf-8-sig"))
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
    instance_diagnostics = _instance_diagnostics_payload(contract, project_root=request.project_root)
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
    problem_family_capability = get_problem_family(contract.problem_family).to_payload()
    problem_family_tags = list(problem_family_capability.get("knowledge_tags") or [])
    if _uses_agent_generated_solver(contract):
        problem_family_tags.append("agent_generated_solver")
    auto_cards = auto_knowledge_cards(
        problem_family=contract.problem_family,
        problem_family_tags=problem_family_tags,
        slot_manifest=slot_manifest,
    )
    knowledge_card_paths = _unique_paths([*request.knowledge_cards, *auto_cards])
    knowledge_cards = [_source_payload(path, request.max_chars_per_source) for path in knowledge_card_paths]
    contract_review_evidence = _contract_review_payload(contract.review)
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
                "Use previous_pipeline_memory.experience_memory_signal as candidate lessons only; do not treat them as curated skills.",
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
            "resources": {key: str(value) for key, value in contract.resources.items()},
            "formal_verdict_owner": "AlgoForge Core",
            "worker_self_evaluation_policy": (
                "Worker may run quick tests and evaluator self-checks, but final success is decided only by Core."
            ),
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
        "auto_knowledge_cards": [str(path) for path in auto_cards],
        "previous_report": previous_report,
        "previous_pipeline_memory": previous_pipeline_memory,
    }
    packet["packet_hash"] = _hash_text(json.dumps(packet, ensure_ascii=False, sort_keys=True))
    return packet


def write_context_packet(request: ContextPacketRequest) -> Path:
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
    """Write a round-specific context packet with evaluator-backed loop history.

    The initial context packet contains static task/document information.  A
    multi-round coding loop also needs measured feedback from earlier rounds;
    this helper keeps the original bounded context and appends compact,
    machine-readable promotion/rollback evidence before recomputing the packet
    hash.
    """

    packet = json.loads(base_context_packet_path.read_text(encoding="utf-8-sig"))
    parent_hash = packet.get("packet_hash") or _hash_text(json.dumps(packet, ensure_ascii=False, sort_keys=True))

    refreshed = dict(packet)
    refreshed.pop("packet_hash", None)
    refreshed["created_at"] = datetime.now(timezone.utc).isoformat()
    refreshed["parent_packet_hash"] = parent_hash
    refreshed["refresh_reason"] = "worker_loop_round_feedback"
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
    refreshed["loop_feedback"] = loop_feedback
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
    refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


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


def _instance_diagnostics_payload(contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
    """Summarize parsed instance shape for worker strategy selection.

    This payload is prompt material only.  It must not change scoring,
    evaluator semantics, or the solver command; Core still promotes only by the
    fixed evaluator objective.
    """

    best_known_csv = _resolve_optional_context_path(
        contract.resources.get("best_known_csv"),
        project_root=project_root,
        base_dir=contract.source_path.parent,
    )
    detailed: list[dict[str, Any]] = []
    profiled: list[dict[str, Any]] = []
    for instance_spec in contract.instances:
        instance_path = _resolve_context_path(
            instance_spec.path,
            project_root=project_root,
            base_dir=contract.source_path.parent,
        )
        payload = _single_instance_diagnostics(
            instance_id=instance_spec.id,
            path=instance_path,
            best_known_csv=best_known_csv,
        )
        detailed.append(payload)
        if payload.get("parsed"):
            profiled.append(payload)

    status = "available" if profiled and len(profiled) == len(contract.instances) else "partial" if profiled else "unavailable"
    sdst_instances = [item for item in profiled if item.get("variant") == "fjsp_sdst"]
    best_known_count = sum(1 for item in profiled if item.get("best_known_makespan") is not None)
    summary = {
        "instance_count": len(contract.instances),
        "profiled_count": len(profiled),
        "sdst_instance_count": len(sdst_instances),
        "shape_group_count": len(_instance_shape_groups(profiled)),
        "setup_time_kinds": sorted({str(item.get("setup_time_kind")) for item in profiled if item.get("setup_time_kind")}),
        "max_operation_count": max((int(item.get("operation_count", 0) or 0) for item in profiled), default=0),
        "max_scale": max((int(item.get("scale", 0) or 0) for item in profiled), default=0),
        "avg_candidate_count": _rounded_average(
            float(item.get("avg_candidate_count", 0.0) or 0.0) for item in profiled
        ),
        "max_setup_to_processing_avg_ratio": max(
            (float(item.get("setup_to_processing_avg_ratio", 0.0) or 0.0) for item in profiled),
            default=0.0,
        ),
        "best_known_available_count": best_known_count,
        "best_known_semantics": "diagnostic_only_score_remains_negative_makespan",
    }
    return {
        "status": status,
        "summary": summary,
        "direction_hints": _instance_direction_hints(summary, profiled),
        "best_known_csv": str(best_known_csv) if best_known_csv else None,
        "shape_groups": _instance_shape_group_summaries(profiled),
        "instances": _representative_instance_diagnostics(detailed, limit=12),
        "truncated": len(detailed) > 12,
    }


def _single_instance_diagnostics(
    *,
    instance_id: str,
    path: Path,
    best_known_csv: Path | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": instance_id,
        "path": str(path),
        "exists": path.exists(),
        "parsed": False,
    }
    try:
        instance = parse_standard_fjsp(path)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not fail context generation.
        payload["error"] = str(exc)
        return payload

    candidate_counts = [len(op.candidates) for job in instance.jobs for op in job.operations]
    durations = [candidate.duration for job in instance.jobs for op in job.operations for candidate in op.candidates]
    setup_stats = _setup_matrix_stats(instance.setup_times)
    processing_avg = _rounded_average(float(value) for value in durations)
    setup_ratio = (
        _round_float(float(setup_stats["avg_nonzero"]) / processing_avg)
        if processing_avg > 0 and setup_stats["avg_nonzero"] is not None
        else 0.0
    )
    best_known = _load_best_known_diagnostic(best_known_csv, instance.name)
    payload.update(
        {
            "parsed": True,
            "name": instance.name,
            "variant": "fjsp_sdst" if instance.has_sequence_dependent_setup else "standard_fjsp",
            "job_count": instance.job_count,
            "machine_count": instance.machine_count,
            "operation_count": instance.operation_count,
            "max_candidate_count": instance.max_candidate_count,
            "scale": instance.job_count * instance.machine_count * instance.operation_count,
            "avg_candidate_count": _rounded_average(float(value) for value in candidate_counts),
            "min_candidate_count": min(candidate_counts, default=0),
            "max_observed_candidate_count": max(candidate_counts, default=0),
            "processing_time_min": min(durations, default=0),
            "processing_time_max": max(durations, default=0),
            "processing_time_avg": processing_avg,
            "setup_time_kind": instance.setup_time_kind,
            "setup_entry_count": setup_stats["entry_count"],
            "setup_nonzero_count": setup_stats["nonzero_count"],
            "setup_density": setup_stats["density"],
            "setup_time_min_positive": setup_stats["min_positive"],
            "setup_time_max": setup_stats["max"],
            "setup_time_avg_nonzero": setup_stats["avg_nonzero"],
            "setup_time_avg_all": setup_stats["avg_all"],
            "setup_to_processing_avg_ratio": setup_ratio,
            "best_known_makespan": best_known,
            "best_known_diagnostic_only": best_known is not None,
        }
    )
    return payload


def _setup_matrix_stats(setup_times: Any) -> dict[str, Any]:
    entry_count = 0
    total = 0
    nonzero_count = 0
    nonzero_total = 0
    min_positive: int | None = None
    max_value = 0
    for machine_matrix in setup_times or ():
        for row in machine_matrix:
            for raw_value in row:
                value = int(raw_value)
                entry_count += 1
                total += value
                max_value = max(max_value, value)
                if value > 0:
                    nonzero_count += 1
                    nonzero_total += value
                    min_positive = value if min_positive is None else min(min_positive, value)
    return {
        "entry_count": entry_count,
        "nonzero_count": nonzero_count,
        "density": _round_float(nonzero_count / entry_count) if entry_count else 0.0,
        "min_positive": min_positive,
        "max": max_value,
        "avg_nonzero": _round_float(nonzero_total / nonzero_count) if nonzero_count else 0.0,
        "avg_all": _round_float(total / entry_count) if entry_count else 0.0,
    }


def _instance_shape_groups(profiled: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in profiled:
        groups.setdefault(_instance_shape_key(item), []).append(item)
    return groups


def _instance_shape_key(item: dict[str, Any]) -> str:
    return (
        f"j{int(item.get('job_count', 0) or 0)}_"
        f"m{int(item.get('machine_count', 0) or 0)}_"
        f"ops{int(item.get('operation_count', 0) or 0)}_"
        f"c{int(item.get('max_candidate_count', 0) or 0)}_"
        f"{item.get('setup_time_kind') or 'none'}"
    )


def _instance_shape_group_summaries(profiled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for key, items in _instance_shape_groups(profiled).items():
        first = items[0]
        best_known_values = [
            float(item["best_known_makespan"])
            for item in items
            if isinstance(item.get("best_known_makespan"), (int, float))
        ]
        setup_ratios = [float(item.get("setup_to_processing_avg_ratio", 0.0) or 0.0) for item in items]
        summaries.append(
            {
                "shape_key": key,
                "count": len(items),
                "instance_ids": [str(item.get("id") or item.get("name") or "") for item in items],
                "job_count": int(first.get("job_count", 0) or 0),
                "machine_count": int(first.get("machine_count", 0) or 0),
                "operation_count": int(first.get("operation_count", 0) or 0),
                "max_candidate_count": int(first.get("max_candidate_count", 0) or 0),
                "scale": int(first.get("scale", 0) or 0),
                "setup_time_kind": first.get("setup_time_kind"),
                "avg_candidate_count": _rounded_average(
                    float(item.get("avg_candidate_count", 0.0) or 0.0) for item in items
                ),
                "setup_to_processing_avg_ratio_avg": _rounded_average(setup_ratios),
                "setup_to_processing_avg_ratio_max": max(setup_ratios, default=0.0),
                "best_known_min": min(best_known_values) if best_known_values else None,
                "best_known_max": max(best_known_values) if best_known_values else None,
            }
        )
    return sorted(
        summaries,
        key=lambda item: (
            -int(item.get("scale", 0) or 0),
            -int(item.get("count", 0) or 0),
            str(item.get("shape_key") or ""),
        ),
    )


def _representative_instance_diagnostics(detailed: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Keep a compact but representative instance sample for worker prompts.

    Earlier packets kept only the first N instances.  On HUdata this hides the
    later shape groups where the AWLS-SDST baseline is weakest, so sampling must
    preserve group coverage and scale/setup extremes before filling by order.
    """

    if len(detailed) <= limit:
        return detailed

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def item_key(item: dict[str, Any]) -> str:
        return str(item.get("path") or item.get("id") or item.get("name") or len(seen))

    def add(item: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        key = item_key(item)
        if key in seen:
            return
        selected.append(item)
        seen.add(key)

    unparsed = [item for item in detailed if not item.get("parsed")]
    for item in unparsed[:2]:
        add(item)

    parsed = [item for item in detailed if item.get("parsed")]
    for group_items in _instance_shape_groups(parsed).values():
        ordered = sorted(group_items, key=lambda item: str(item.get("id") or item.get("name") or item.get("path") or ""))
        if not ordered:
            continue
        candidate_indices = {0, len(ordered) // 2, len(ordered) - 1}
        for index in sorted(candidate_indices):
            add(ordered[index])

    for item in sorted(
        parsed,
        key=lambda item: (
            float(item.get("setup_to_processing_avg_ratio", 0.0) or 0.0),
            int(item.get("scale", 0) or 0),
            float(item.get("best_known_makespan", 0.0) or 0.0),
        ),
        reverse=True,
    ):
        add(item)

    for item in detailed:
        add(item)
        if len(selected) >= limit:
            break
    return selected


def _instance_direction_hints(summary: dict[str, Any], profiled: list[dict[str, Any]]) -> list[str]:
    if not profiled:
        return ["Instance parsing failed or no instances were supplied; do not infer scale or SDST setup strength from filenames."]

    hints = ["Use actual parsed instance content, not filename shape, when choosing budget-sensitive slot strategies."]
    if int(summary.get("shape_group_count", 0) or 0) > 1:
        hints.append("Multiple instance shapes are present; inspect shape_groups and avoid overfitting a single oddla/seed probe.")
    if int(summary.get("sdst_instance_count", 0) or 0) > 0:
        hints.append("SDST setup is present; keep setup_time_between usage inside confirmed slots and preserve parser/evaluator semantics.")
        setup_ratio = float(summary.get("max_setup_to_processing_avg_ratio", 0.0) or 0.0)
        if setup_ratio >= 0.75:
            hints.append("Setup is large relative to processing; prioritize setup-aware insertion, N7/NK scoring, and critical-block ordering over pure processing-time rules.")
        elif setup_ratio >= 0.25:
            hints.append("Setup is material; combine setup deltas with tail, criticality, or bottleneck pressure instead of optimizing setup alone.")
    else:
        hints.append("No SDST setup matrix was detected; SDST-only setup changes should preserve standard FJSP behavior.")

    avg_candidates = float(summary.get("avg_candidate_count", 0.0) or 0.0)
    if avg_candidates <= 1.2:
        hints.append("Machine alternatives are sparse; sequence/neighborhood ordering likely has more leverage than reassignment-only rules.")
    elif avg_candidates >= 3.0:
        hints.append("Many machine alternatives exist; machine assignment, insertion, and change-machine NK slots may have useful leverage.")

    has_large_five_machine_group = any(
        int(item.get("job_count", 0) or 0) >= 20
        and int(item.get("machine_count", 0) or 0) <= 5
        and int(item.get("operation_count", 0) or 0) >= 100
        for item in profiled
    )
    if has_large_five_machine_group:
        hints.append(
            "A large 20-job/5-machine SDST shape is present; include bottleneck-machine sequencing and load balance evidence, not only la20-style 10x10 behavior."
        )

    if int(summary.get("best_known_available_count", 0) or 0) > 0:
        hints.append("Best-known/LB/UB values are gap diagnostics only; promotion remains strict Core makespan improvement.")
    return hints


def _resolve_optional_context_path(
    path: Path | None,
    *,
    project_root: Path | None,
    base_dir: Path,
) -> Path | None:
    if path is None:
        return None
    resolved = _resolve_context_path(path, project_root=project_root, base_dir=base_dir)
    return resolved if resolved.exists() else None


def _resolve_context_path(path: Path, *, project_root: Path | None, base_dir: Path) -> Path:
    if path.is_absolute():
        return path
    repo_root = Path(__file__).resolve().parents[1]
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / path)
    candidates.extend([base_dir / path, repo_root / path])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _load_best_known_diagnostic(path: Path | None, instance_name: str) -> float | None:
    if path is None:
        return None
    try:
        from examples.standard_fjsp_evaluator import load_best_known

        return load_best_known(path, instance_name)
    except Exception:  # noqa: BLE001 - diagnostics must not fail context generation.
        return None


def _rounded_average(values: Any) -> float:
    values_list = [float(value) for value in values]
    if not values_list:
        return 0.0
    return _round_float(sum(values_list) / len(values_list))


def _round_float(value: float) -> float:
    return round(float(value), 6)


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
    repo_root = Path(__file__).resolve().parents[1]
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
        "experience_memory_signal": _compact_experience_memory_signal(memory.get("experience_memory_signal") or {}),
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
        "self_evolution_metrics": signal.get("self_evolution_metrics") or {},
        "next_context_guidance": (signal.get("next_context_guidance") or [])[:8],
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
