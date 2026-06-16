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
    project_intake = (
        _project_intake_payload(request.project_intake_manifest, request.max_chars_per_source)
        if request.project_intake_manifest
        else None
    )
    required_order = [
        "Read this context packet.",
        "State a natural-language strategy before editing code.",
        "Modify only allowed files.",
        "Run the quick test before benchmark self-evaluation.",
        "Return structured changed files, test results, benchmark summary, and failure analysis.",
    ]
    if project_intake:
        required_order.insert(1, "Review project_intake before proposing code changes.")
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
        "project_intake": project_intake,
        "documents": docs,
        "knowledge_cards": knowledge_cards,
        "previous_report": previous_report,
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
