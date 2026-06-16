from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import TaskContract


@dataclass(frozen=True)
class ContextPacketRequest:
    contract_path: Path
    output_path: Path
    docs: list[Path] = field(default_factory=list)
    knowledge_cards: list[Path] = field(default_factory=list)
    hypothesis: str = ""
    previous_report: Path | None = None
    previous_pipeline_memory: Path | None = None
    project_intake_manifest: Path | None = None
    max_chars_per_source: int = 12000


def build_context_packet(request: ContextPacketRequest) -> dict[str, Any]:
    contract = TaskContract.load(request.contract_path)
    contract_raw = json.loads(request.contract_path.read_text(encoding="utf-8-sig"))
    docs = [_source_payload(path, request.max_chars_per_source) for path in request.docs]
    knowledge_cards = [_source_payload(path, request.max_chars_per_source) for path in request.knowledge_cards]
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
    project_intake = (
        _project_intake_payload(request.project_intake_manifest, request.max_chars_per_source)
        if request.project_intake_manifest
        else None
    )
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
    if project_intake:
        required_order.insert(1, "Review project_intake before proposing code changes.")
    if previous_pipeline_memory:
        required_order.insert(1, "Review previous_pipeline_memory before proposing the next loop change.")
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
        "documents": docs,
        "knowledge_cards": knowledge_cards,
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
    refreshed["loop_feedback"] = loop_feedback

    worker_instruction = dict(refreshed.get("worker_instruction") or {})
    required_order = list(worker_instruction.get("required_order") or [])
    feedback_step = "Review loop_feedback and avoid repeating rolled-back changes unless the new proposal is materially different."
    if feedback_step not in required_order:
        required_order.insert(1, feedback_step)
    worker_instruction["required_order"] = required_order
    worker_instruction["round_feedback_rule"] = (
        "Treat loop_feedback as Core evaluator evidence.  Promoted rounds show "
        "directions worth preserving; rolled-back rounds show directions to avoid "
        "or modify.  Do not use worker self-claims as success evidence."
    )
    refreshed["worker_instruction"] = worker_instruction
    refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


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
        "evidence_signal": memory.get("evidence_signal") or {},
        "recommendations": (memory.get("recommendations") or [])[:20],
        "artifacts": memory.get("artifacts") or {},
        "error": error,
    }


def _contract_review_payload(review: dict[str, Any]) -> dict[str, Any]:
    document_schema = _compact_document_schema(review.get("document_schema") or {})
    return {
        "status": review.get("status"),
        "uncertain_fields": (review.get("uncertain_fields") or [])[:30],
        "extracted_problem_features": _compact_feature_hints(review.get("extracted_problem_features") or [], limit=30),
        "metric_hints": _compact_metric_hints(review.get("metric_hints") or [], limit=30),
        "document_schema": document_schema,
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
