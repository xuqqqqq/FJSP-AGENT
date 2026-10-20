"""Read model-returned reasoning from this task's isolated OpenCode session."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


MAX_REASONING_CHARS = 240_000


def reasoning_trace_record(
    part: dict[str, Any], *, label: str, record_id: str, timestamp: Any,
) -> dict[str, Any] | None:
    text = part.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    timing = part.get("time") if isinstance(part.get("time"), dict) else {}
    return {
        "id": f"{label}:reasoning:{part.get('id') or record_id}",
        "attempt": label,
        "timestamp": timing.get("start") or timestamp,
        "updated_at": timing.get("end") or timestamp,
        "kind": "reasoning",
        "text": text[-MAX_REASONING_CHARS:],
        "text_length": len(text),
        "streaming": not bool(timing.get("end")),
    }


def read_session_reasoning(
    database_path: Path, *, session_id: str, started_at: float, label: str,
    finished_at: float | None = None,
) -> list[dict[str, Any]]:
    if not session_id or not database_path.is_file():
        return []
    try:
        connection = sqlite3.connect(
            database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05,
        )
        try:
            rows = connection.execute(
                "SELECT id, time_updated, data FROM part "
                "WHERE session_id = ? AND time_created >= ? "
                "AND time_created <= ? "
                "AND json_extract(data, '$.type') = 'reasoning' "
                "ORDER BY time_created DESC LIMIT 24",
                (session_id, started_at, finished_at if finished_at is not None else 2**63 - 1),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        # OpenCode storage is optional and may differ between installed versions.
        return []
    records = []
    for part_id, updated_at, raw in reversed(rows):
        try:
            part = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(part, dict):
            continue
        part["id"] = part_id
        record = reasoning_trace_record(
            part, label=label, record_id=part_id, timestamp=updated_at,
        )
        if record is not None:
            records.append(record)
    return records


def read_reasoning_events(
    path: Path, *, session_id: str, started_at: float, label: str,
) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    parts: dict[str, dict[str, Any]] = {}
    updated: dict[str, Any] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("sessionID") != session_id:
            continue
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, (int, float)) or timestamp < started_at:
            continue
        if event.get("type") == "reasoning" and isinstance(event.get("part"), dict):
            part = event["part"]
            if part.get("type") != "reasoning" or not part.get("id"):
                continue
            part_id = str(part["id"])
            parts[part_id] = dict(part)
            updated[part_id] = timestamp
        elif event.get("type") == "reasoning_delta":
            part_id = str(event.get("partID") or "")
            delta = event.get("delta")
            if part_id not in parts or not isinstance(delta, str):
                continue
            parts[part_id]["text"] = str(parts[part_id].get("text") or "") + delta
            updated[part_id] = timestamp
    return [
        record for part_id, part in parts.items()
        if (record := reasoning_trace_record(
            part, label=label, record_id=part_id, timestamp=updated[part_id],
        )) is not None
    ]


def merge_trace_records(
    previous: list[dict[str, Any]], records: list[dict[str, Any]], *, limit: int,
) -> list[dict[str, Any]]:
    merged = {str(record["id"]): record for record in previous}
    for record in records:
        existing = merged.get(str(record["id"]))
        if existing is not None and record.get("kind") == "reasoning":
            if not existing.get("streaming") and record.get("streaming"):
                continue
            if (existing.get("updated_at") or 0) > (record.get("updated_at") or 0):
                continue
        merged[str(record["id"])] = record
    return list(merged.values())[-limit:]
