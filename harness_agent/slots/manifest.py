"""代码槽 manifest 的解析和确认状态管理。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_agent.domains.pack import get_domain_pack


@dataclass(frozen=True)
class CodeSlotSpec:
    """slot manifest 中的单个槽位声明。

    它描述的是“哪里可以改、为什么改、不能破坏什么、需要跑什么验证”，用于把
    可选插件编辑约束显式化，而不是让 worker 自行在全仓库猜测改动边界。
    """

    slot_id: str
    title: str
    target_file: str
    marker_start: str
    marker_end: str
    slot_kind: str
    language: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    invariants: list[str]
    allowed_edits: list[str]
    forbidden_edits: list[str]
    validation_commands: list[str] = field(default_factory=list)
    knowledge_tags: list[str] = field(default_factory=list)
    user_confirmed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "slot_id": self.slot_id,
            "title": self.title,
            "target_file": self.target_file,
            "marker_start": self.marker_start,
            "marker_end": self.marker_end,
            "slot_kind": self.slot_kind,
            "language": self.language,
            "purpose": self.purpose,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "invariants": list(self.invariants),
            "allowed_edits": list(self.allowed_edits),
            "forbidden_edits": list(self.forbidden_edits),
            "validation_commands": list(self.validation_commands),
            "knowledge_tags": list(self.knowledge_tags),
            "user_confirmed": bool(self.user_confirmed),
        }
        payload.update(self.extra)
        payload["user_confirmed"] = bool(self.user_confirmed)
        return payload


@dataclass(frozen=True)
class SlotManifest:
    """一组代码槽的确认状态。

    一个 manifest 可以列出很多潜在槽位，但只有 `user_confirmed=true` 的槽位才可
    进入自动编辑闭环，因此这里同时承担“能力声明”和“人工授权”两个角色。
    """

    schema_version: int
    problem_family: str
    status: str
    slots: list[CodeSlotSpec]
    confirmation_required: bool = True
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "problem_family": self.problem_family,
            "status": self.status,
            "confirmation_required": self.confirmation_required,
            "notes": list(self.notes),
            "slots": [slot.to_payload() for slot in self.slots],
        }
        payload.update(self.extra)
        payload["schema_version"] = self.schema_version
        payload["problem_family"] = self.problem_family
        payload["status"] = self.status
        payload["confirmation_required"] = self.confirmation_required
        payload["notes"] = list(self.notes)
        payload["slots"] = [slot.to_payload() for slot in self.slots]
        return payload


def load_slot_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def default_slot_manifest(*, problem_family: str, confirmed: bool = False) -> SlotManifest:
    """生成某问题族的默认 slot manifest。

    默认 manifest 反映 Domain Pack 提供了哪些可选槽位；除非显式传入 `confirmed`
    或后续再选中确认，否则它们仍处于锁定状态。
    """

    manifest = _load_domain_slot_manifest(problem_family)
    slots = [_replace_slot_confirmation(slot, confirmed) for slot in manifest.slots]
    return SlotManifest(
        schema_version=manifest.schema_version,
        problem_family=manifest.problem_family,
        status="confirmed" if confirmed else "draft_requires_user_confirmation",
        confirmation_required=not confirmed,
        notes=manifest.notes,
        slots=slots,
        extra=manifest.extra,
    )


def selected_slot_manifest(*, problem_family: str, selected_slot_ids: list[str]) -> SlotManifest:
    """生成“仅部分 slot 被确认”的 manifest。"""

    selected = {str(slot_id) for slot_id in selected_slot_ids if str(slot_id).strip()}
    if not selected:
        raise ValueError("at least one selected slot_id is required")
    manifest = default_slot_manifest(problem_family=problem_family, confirmed=False)
    known = {slot.slot_id for slot in manifest.slots}
    unknown = sorted(selected - known)
    if unknown:
        raise ValueError(f"unknown {manifest.problem_family} slot_id(s): {', '.join(unknown)}")
    slots = [_replace_slot_confirmation(slot, slot.slot_id in selected) for slot in manifest.slots]
    return SlotManifest(
        schema_version=manifest.schema_version,
        problem_family=manifest.problem_family,
        status="confirmed",
        confirmation_required=False,
        notes=manifest.notes
        + [
            "Only selected slots have user_confirmed=true; unselected slots remain locked.",
        ],
        slots=slots,
        extra=manifest.extra,
    )


def default_standard_fjsp_slot_manifest(*, confirmed: bool = False) -> SlotManifest:
    return default_slot_manifest(problem_family="standard_fjsp", confirmed=confirmed)


def selected_standard_fjsp_slot_manifest(*, selected_slot_ids: list[str]) -> SlotManifest:
    return selected_slot_manifest(problem_family="standard_fjsp", selected_slot_ids=selected_slot_ids)


def write_default_slot_manifest(*, problem_family: str, output: Path, confirmed: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = default_slot_manifest(problem_family=problem_family, confirmed=confirmed).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def write_selected_slot_manifest(*, problem_family: str, output: Path, selected_slot_ids: list[str]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = selected_slot_manifest(problem_family=problem_family, selected_slot_ids=selected_slot_ids).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _load_domain_slot_manifest(problem_family: str) -> SlotManifest:
    path = _domain_slot_manifest_path(problem_family)
    if path is None:
        raise ValueError(f"no slot manifest edit strategy is available for problem family: {problem_family}")
    if not path.exists():
        raise ValueError(f"slot manifest asset does not exist for problem family {problem_family}: {path}")
    payload = load_slot_manifest(path)
    return _slot_manifest_from_payload(payload, source_path=path)


def _domain_slot_manifest_path(problem_family: str) -> Path | None:
    """解析问题族的 slot manifest 资产路径。

    优先走 Domain Pack 显式声明的 edit strategy；只有未声明时才尝试约定式路径。
    """

    pack = get_domain_pack(problem_family, fallback_to_standard=False)
    if pack is None:
        return None
    strategy = pack.edit_strategy("slot_based_edit")
    path = strategy.asset_path("slot_manifest") if strategy is not None else None
    if path is not None:
        return path
    if pack.source_path is not None:
        convention_path = pack.source_path.parent / "slot_manifest.json"
        if convention_path.exists():
            return convention_path
    return None


def _slot_manifest_from_payload(payload: dict[str, Any], *, source_path: Path | None = None) -> SlotManifest:
    if not isinstance(payload, dict):
        raise ValueError("slot manifest payload must be a JSON object")
    raw_slots = payload.get("slots")
    if not isinstance(raw_slots, list):
        raise ValueError("slot manifest must contain a slots list")
    problem_family = str(payload.get("problem_family") or "")
    if not problem_family:
        raise ValueError("slot manifest must declare problem_family")
    extra = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "schema_version",
            "problem_family",
            "status",
            "confirmation_required",
            "notes",
            "slots",
        }
    }
    if source_path is not None:
        extra.setdefault("source_path", str(source_path))
    return SlotManifest(
        schema_version=int(payload.get("schema_version") or 1),
        problem_family=problem_family,
        status=str(payload.get("status") or "draft_requires_user_confirmation"),
        confirmation_required=bool(payload.get("confirmation_required", True)),
        notes=_string_list(payload.get("notes")),
        slots=[_slot_from_payload(slot) for slot in raw_slots if isinstance(slot, dict)],
        extra=extra,
    )


def _slot_from_payload(payload: dict[str, Any]) -> CodeSlotSpec:
    known_fields = {
        "slot_id",
        "title",
        "target_file",
        "marker_start",
        "marker_end",
        "slot_kind",
        "language",
        "purpose",
        "inputs",
        "outputs",
        "invariants",
        "allowed_edits",
        "forbidden_edits",
        "validation_commands",
        "knowledge_tags",
        "user_confirmed",
    }
    slot_id = str(payload.get("slot_id") or "").strip()
    if not slot_id:
        raise ValueError("slot manifest contains a slot without slot_id")
    return CodeSlotSpec(
        slot_id=slot_id,
        title=str(payload.get("title") or slot_id),
        target_file=str(payload.get("target_file") or ""),
        marker_start=str(payload.get("marker_start") or ""),
        marker_end=str(payload.get("marker_end") or ""),
        slot_kind=str(payload.get("slot_kind") or "marked_block"),
        language=str(payload.get("language") or "python"),
        purpose=str(payload.get("purpose") or ""),
        inputs=_string_list(payload.get("inputs")),
        outputs=_string_list(payload.get("outputs")),
        invariants=_string_list(payload.get("invariants")),
        allowed_edits=_string_list(payload.get("allowed_edits")),
        forbidden_edits=_string_list(payload.get("forbidden_edits")),
        validation_commands=_string_list(payload.get("validation_commands")),
        knowledge_tags=_string_list(payload.get("knowledge_tags")),
        user_confirmed=bool(payload.get("user_confirmed", False)),
        extra={key: value for key, value in payload.items() if key not in known_fields},
    )


def _replace_slot_confirmation(slot: CodeSlotSpec, user_confirmed: bool) -> CodeSlotSpec:
    return CodeSlotSpec(
        slot_id=slot.slot_id,
        title=slot.title,
        target_file=slot.target_file,
        marker_start=slot.marker_start,
        marker_end=slot.marker_end,
        slot_kind=slot.slot_kind,
        language=slot.language,
        purpose=slot.purpose,
        inputs=list(slot.inputs),
        outputs=list(slot.outputs),
        invariants=list(slot.invariants),
        allowed_edits=list(slot.allowed_edits),
        forbidden_edits=list(slot.forbidden_edits),
        validation_commands=list(slot.validation_commands),
        knowledge_tags=list(slot.knowledge_tags),
        user_confirmed=bool(user_confirmed),
        extra=dict(slot.extra),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
