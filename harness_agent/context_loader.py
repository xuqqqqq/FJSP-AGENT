from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context_compaction import stable_worker_context


_ENVELOPE_KEYS = {"base_context", "context_delta", "context_sections"}
_DYNAMIC_KEYS = (
    "hypothesis",
    "knowledge_cards",
    "previous_report",
    "previous_pipeline_memory",
    "slot_manifest",
    "iteration_edit_contract",
    "incumbent_code_context",
    "loop_feedback",
    "baseline_generation",
    "current_round_repair",
    "context_compaction",
    "refresh_reason",
    "parent_packet_hash",
    "base_context_ref",
)


class ContextPacketLoadError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedContextPacket:
    path: Path
    raw: dict[str, Any]
    schema_version: int
    effective_context: dict[str, Any]
    stable_context: dict[str, Any]
    dynamic_context: dict[str, Any]
    integrity: dict[str, Any]
    diagnostics: tuple[str, ...]


def load_context_packet(
    path: Path,
    *,
    artifact_root: Path | None = None,
    resolve_base_ref: bool = True,
) -> LoadedContextPacket:
    source_path = Path(path).resolve()
    raw = _read_json_object(source_path)
    diagnostics: list[str] = []
    integrity = _packet_integrity(raw)
    if integrity["status"] == "mismatch":
        diagnostics.append("packet_hash does not match the loaded packet content")

    schema_version = _schema_version(raw)
    effective = _effective_context(
        raw,
        source_path=source_path,
        artifact_root=artifact_root,
        resolve_base_ref=resolve_base_ref,
        diagnostics=diagnostics,
    )
    stable = stable_worker_context(effective)
    dynamic = {
        key: effective.get(key)
        for key in _DYNAMIC_KEYS
        if effective.get(key) is not None
    }
    worker_instruction = effective.get("worker_instruction")
    if isinstance(worker_instruction, dict):
        instruction_delta = {
            key: value
            for key, value in worker_instruction.items()
            if key not in {"role", "success_rule", "baseline_generation_rule"}
        }
        if instruction_delta:
            dynamic["worker_instruction_delta"] = instruction_delta

    return LoadedContextPacket(
        path=source_path,
        raw=raw,
        schema_version=schema_version,
        effective_context=effective,
        stable_context=stable,
        dynamic_context=dynamic,
        integrity=integrity,
        diagnostics=tuple(diagnostics),
    )


def load_context_dict(path: Path, *, artifact_root: Path | None = None) -> dict[str, Any]:
    return load_context_packet(path, artifact_root=artifact_root).effective_context


def try_load_context_dict(path: Path, *, artifact_root: Path | None = None) -> dict[str, Any]:
    try:
        return load_context_dict(path, artifact_root=artifact_root)
    except (OSError, json.JSONDecodeError, ContextPacketLoadError):
        return {}


def _effective_context(
    raw: dict[str, Any],
    *,
    source_path: Path,
    artifact_root: Path | None,
    resolve_base_ref: bool,
    diagnostics: list[str],
) -> dict[str, Any]:
    delta = raw.get("context_delta")
    if not isinstance(delta, dict):
        return dict(raw)

    flat_context = {key: value for key, value in raw.items() if key not in _ENVELOPE_KEYS}
    if _has_task_context(flat_context):
        return _deep_merge(flat_context, delta)

    base_context = raw.get("base_context")
    if isinstance(base_context, dict):
        return _deep_merge(base_context, delta)

    if not resolve_base_ref:
        diagnostics.append("context_delta is present but base_context_ref resolution is disabled")
        return _deep_merge(flat_context, delta)

    base_ref = raw.get("base_context_ref")
    base_path = _trusted_base_path(
        base_ref,
        source_path=source_path,
        artifact_root=artifact_root,
        diagnostics=diagnostics,
    )
    if base_path is None:
        return _deep_merge(flat_context, delta)

    base_raw = _read_json_object(base_path)
    declared_base_hash = base_ref.get("packet_hash") if isinstance(base_ref, dict) else None
    actual_base_hash = _packet_hash(base_raw)
    if declared_base_hash and declared_base_hash != actual_base_hash:
        diagnostics.append("base_context_ref packet_hash does not match the referenced packet")
    return _deep_merge(base_raw, delta)


def _trusted_base_path(
    base_ref: Any,
    *,
    source_path: Path,
    artifact_root: Path | None,
    diagnostics: list[str],
) -> Path | None:
    if not isinstance(base_ref, dict) or not base_ref.get("path"):
        diagnostics.append("context_delta has no readable base_context_ref.path")
        return None
    raw_path = Path(str(base_ref["path"]))
    candidate = raw_path if raw_path.is_absolute() else source_path.parent / raw_path
    candidate = candidate.resolve()
    if artifact_root is None:
        if raw_path.is_absolute():
            diagnostics.append("absolute base_context_ref.path is advisory without artifact_root")
            return None
    else:
        trusted_root = artifact_root.resolve()
        try:
            candidate.relative_to(trusted_root)
        except ValueError:
            diagnostics.append("base_context_ref.path is outside the trusted artifact_root")
            return None
    if not candidate.is_file():
        diagnostics.append("base_context_ref.path does not exist")
        return None
    return candidate


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ContextPacketLoadError(f"context packet must be a JSON object: {path}")
    return payload


def _packet_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    declared = payload.get("packet_hash")
    actual = _packet_hash(payload)
    if not declared:
        status = "unavailable"
    else:
        status = "valid" if str(declared) == actual else "mismatch"
    return {
        "status": status,
        "declared_packet_hash": declared,
        "actual_packet_hash": actual,
    }


def _packet_hash(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("packet_hash", None)
    text = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _schema_version(payload: dict[str, Any]) -> int:
    try:
        return max(1, int(payload.get("schema_version") or 1))
    except (TypeError, ValueError):
        return 1


def _has_task_context(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("task"), dict) and isinstance(payload.get("evaluator_protocol"), dict)


def _deep_merge(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in delta.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged
