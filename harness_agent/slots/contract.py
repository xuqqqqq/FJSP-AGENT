"""可选代码槽的边界、输入输出与替换契约。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyw": "python",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".sh": "shell",
    ".html": "html",
    ".xml": "xml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
}


@dataclass(frozen=True)
class ResolvedCodeSlot:
    slot_id: str
    title: str
    target_file: str
    marker_start: str
    marker_end: str
    purpose: str
    inputs: list[Any]
    outputs: list[Any]
    invariants: list[Any]
    allowed_edits: list[Any]
    forbidden_edits: list[Any]
    validation_commands: list[Any]
    knowledge_tags: list[Any]
    language: str
    line_start: int | None = None
    line_end: int | None = None
    block_name: str = ""
    context_before: str = ""
    context_after: str = ""
    original_content: str = ""

    @classmethod
    def from_manifest_slot(cls, slot: dict[str, Any], *, source_text: str | None = None) -> "ResolvedCodeSlot":
        target_file = str(slot.get("target_file", ""))
        marker_start = str(slot.get("marker_start", ""))
        marker_end = str(slot.get("marker_end", ""))
        block = locate_marked_block(source_text, marker_start, marker_end) if source_text is not None else None
        return cls(
            slot_id=str(slot.get("slot_id", "")),
            title=str(slot.get("title", "")),
            target_file=target_file,
            marker_start=marker_start,
            marker_end=marker_end,
            purpose=str(slot.get("purpose", "")),
            inputs=list(slot.get("inputs") or []),
            outputs=list(slot.get("outputs") or []),
            invariants=list(slot.get("invariants") or []),
            allowed_edits=list(slot.get("allowed_edits") or []),
            forbidden_edits=list(slot.get("forbidden_edits") or []),
            validation_commands=list(slot.get("validation_commands") or []),
            knowledge_tags=list(slot.get("knowledge_tags") or []),
            language=str(slot.get("language") or language_for_path(target_file)),
            line_start=block.line_start if block else _optional_int(slot.get("line_start")),
            line_end=block.line_end if block else _optional_int(slot.get("line_end")),
            block_name=str(slot.get("block_name") or block.block_name if block else slot.get("block_name") or ""),
            context_before=block.context_before if block else str(slot.get("context_before") or ""),
            context_after=block.context_after if block else str(slot.get("context_after") or ""),
            original_content=block.original_content if block else str(slot.get("original_content") or ""),
        )

    def to_block_payload(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "title": self.title,
            "file_path": self.target_file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "block_name": self.block_name,
            "language": self.language,
            "marker_start": self.marker_start,
            "marker_end": self.marker_end,
            "purpose": self.purpose,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "invariants": self.invariants,
            "allowed_edits": self.allowed_edits,
            "forbidden_edits": self.forbidden_edits,
            "validation_commands": self.validation_commands,
            "knowledge_tags": self.knowledge_tags,
            "original_content": self.original_content,
            "context_before": self.context_before,
            "context_after": self.context_after,
        }


@dataclass(frozen=True)
class MarkedBlock:
    marker_start: str
    marker_end: str
    line_start: int
    line_end: int
    block_name: str
    original_content: str
    context_before: str
    context_after: str


def validate_slot_manifest_gate(
    context: dict[str, Any],
    slot_id: str,
    *,
    expected_target_file: str | None = None,
    expected_marker_start: str | None = None,
    expected_marker_end: str | None = None,
) -> list[str]:
    errors: list[str] = []
    manifest, slot, lookup_errors = _lookup_manifest_slot(context, slot_id)
    if lookup_errors:
        return lookup_errors

    if manifest.get("status") != "confirmed":
        errors.append("slot_manifest.status must be confirmed")
    if bool(manifest.get("confirmation_required", True)):
        errors.append("slot_manifest.confirmation_required must be false")
    if not bool(slot.get("user_confirmed", False)):
        errors.append(f"slot {slot_id!r} must be user_confirmed")
    if expected_target_file is not None and str(slot.get("target_file", "")) != expected_target_file:
        errors.append(f"slot target_file must be {expected_target_file!r}")
    if expected_marker_start is not None and str(slot.get("marker_start", "")) != expected_marker_start:
        errors.append(f"slot marker_start must be {expected_marker_start!r}")
    if expected_marker_end is not None and str(slot.get("marker_end", "")) != expected_marker_end:
        errors.append(f"slot marker_end must be {expected_marker_end!r}")
    return errors


def find_confirmed_slot(
    context: dict[str, Any],
    slot_id: str,
    *,
    worktree_path: Path | None = None,
) -> ResolvedCodeSlot:
    errors = validate_slot_manifest_gate(context, slot_id)
    if errors:
        raise ValueError("; ".join(errors))
    _manifest, slot, lookup_errors = _lookup_manifest_slot(context, slot_id)
    if lookup_errors:
        raise ValueError("; ".join(lookup_errors))

    source_text = None
    if worktree_path is not None:
        source_text = (worktree_path / str(slot.get("target_file", ""))).read_text(encoding="utf-8")
    return ResolvedCodeSlot.from_manifest_slot(slot, source_text=source_text)


def locate_marked_block(
    text: str,
    marker_start: str,
    marker_end: str,
    *,
    context_lines: int = 5,
) -> MarkedBlock:
    lines = text.splitlines(keepends=True)
    start_index = _find_marker_line(lines, marker_start)
    end_index = _find_marker_line(lines, marker_end)
    if start_index is None or end_index is None or end_index <= start_index:
        raise ValueError(f"missing ordered markers {marker_start!r}/{marker_end!r}")
    original_content = "".join(lines[start_index + 1 : end_index])
    before_start = max(0, start_index - context_lines)
    after_end = min(len(lines), end_index + 1 + context_lines)
    return MarkedBlock(
        marker_start=marker_start,
        marker_end=marker_end,
        line_start=start_index + 1,
        line_end=end_index + 1,
        block_name=extract_block_name(marker_start),
        original_content=original_content,
        context_before="".join(lines[before_start:start_index]),
        context_after="".join(lines[end_index + 1 : after_end]),
    )


def extract_marked_block(text: str, marker_start: str, marker_end: str) -> str:
    return locate_marked_block(text, marker_start, marker_end).original_content


def replace_marked_block(text: str, marker_start: str, marker_end: str, replacement: str) -> str:
    lines = text.splitlines(keepends=True)
    start_index = _find_marker_line(lines, marker_start)
    end_index = _find_marker_line(lines, marker_end)
    if start_index is None or end_index is None or end_index <= start_index:
        raise ValueError(f"missing ordered markers {marker_start!r}/{marker_end!r}")
    prefix = "".join(lines[: start_index + 1]).rstrip("\n")
    suffix = "".join(lines[end_index:])
    return f"{prefix}\n{replacement.rstrip()}\n{suffix}"


def language_for_path(path: str) -> str:
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "plaintext")


def extract_block_name(marker_start: str) -> str:
    normalized = _strip_comment_wrapper(marker_start)
    raw_tokens = normalized.split()
    if raw_tokens and raw_tokens[-1].upper() in {"START", "BEGIN"}:
        if raw_tokens[0].upper() in {"SLOT", "EVOLVE"}:
            return " ".join(raw_tokens[1:-1]).strip()
        return " ".join(raw_tokens[:-1]).strip()

    tokens = normalized.replace("_", " ").replace("-", " ").split()
    upper_tokens = [token.upper() for token in tokens]
    for stop_token in ("START", "BEGIN"):
        if stop_token not in upper_tokens:
            continue
        stop_index = upper_tokens.index(stop_token)
        if stop_index == 0:
            return ""
        if upper_tokens[0] in {"SLOT", "EVOLVE"}:
            name_tokens = tokens[1:stop_index]
        else:
            name_tokens = tokens[:stop_index]
        return " ".join(name_tokens).strip()
    return ""


def _strip_comment_wrapper(marker: str) -> str:
    stripped = marker.strip()
    for prefix in ("#", "//"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
    if stripped.startswith("/*") and stripped.endswith("*/"):
        stripped = stripped[2:-2].strip()
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        stripped = stripped[4:-3].strip()
    return stripped


def _lookup_manifest_slot(
    context: dict[str, Any],
    slot_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    manifest = context.get("slot_manifest")
    if not isinstance(manifest, dict) or not manifest.get("exists", True):
        return {}, {}, ["context packet is missing a readable slot_manifest"]
    slots = manifest.get("slots")
    if not isinstance(slots, list):
        return manifest, {}, ["slot_manifest.slots must be a list"]
    slot = next(
        (
            item
            for item in slots
            if isinstance(item, dict) and item.get("slot_id") == slot_id
        ),
        None,
    )
    if slot is None:
        return manifest, {}, [f"slot_manifest does not contain required slot_id {slot_id!r}"]
    return manifest, slot, []


def _find_marker_line(lines: list[str], marker: str) -> int | None:
    stripped_marker = marker.strip()
    for index, line in enumerate(lines):
        if line.strip() == stripped_marker:
            return index
    return None


def _optional_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
