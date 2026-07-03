from __future__ import annotations

from pathlib import Path
from typing import Any

from .domain_pack import get_domain_pack


def auto_knowledge_cards(
    *,
    problem_family: str,
    problem_family_tags: list[str] | None = None,
    slot_manifest: dict[str, Any] | None = None,
) -> list[Path]:
    """Select knowledge cards declared by the active external domain pack."""

    pack = get_domain_pack(problem_family)
    if pack is None:
        return []

    selected: list[Path] = list(pack.base_cards)
    tags = {str(tag).strip().lower() for tag in (problem_family_tags or []) if str(tag).strip()}
    if slot_manifest:
        for slot in slot_manifest.get("slots") or []:
            if not isinstance(slot, dict) or not slot.get("user_confirmed"):
                continue
            tags.update(str(tag).strip().lower() for tag in slot.get("knowledge_tags") or [] if str(tag).strip())

    for tag in sorted(tags):
        selected.extend(pack.tagged_cards.get(tag, []))
    return _existing_unique_paths(selected)


def _existing_unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        unique.append(path)
    return unique
