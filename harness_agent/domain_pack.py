from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_PACK_ROOT = PROJECT_ROOT / "domain_packs"


@dataclass(frozen=True)
class DomainCapability:
    """Machine-readable capability card loaded from an external domain pack."""

    family_id: str
    display_name: str
    description: str
    supported_variants: list[str]
    canonical_objectives: list[dict[str, Any]]
    io_contract_notes: list[str]
    evaluator_invariants: list[str]
    solver_entrypoints: list[str]
    specialization_hooks: list[str] = field(default_factory=list)
    knowledge_tags: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "display_name": self.display_name,
            "description": self.description,
            "supported_variants": list(self.supported_variants),
            "canonical_objectives": list(self.canonical_objectives),
            "io_contract_notes": list(self.io_contract_notes),
            "evaluator_invariants": list(self.evaluator_invariants),
            "solver_entrypoints": list(self.solver_entrypoints),
            "specialization_hooks": list(self.specialization_hooks),
            "knowledge_tags": list(self.knowledge_tags),
        }


@dataclass(frozen=True)
class DomainPack:
    """External domain assets for one optimization problem family."""

    family_id: str
    aliases: list[str]
    capability: DomainCapability
    base_cards: list[Path] = field(default_factory=list)
    tagged_cards: dict[str, list[Path]] = field(default_factory=dict)
    edit_strategies: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def keys(self) -> set[str]:
        return {_normalize_key(self.family_id), *(_normalize_key(alias) for alias in self.aliases)}


def load_domain_pack(path: Path, *, project_root: Path = PROJECT_ROOT) -> DomainPack:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    family_id = str(payload.get("family_id") or path.parent.name)
    aliases = [str(alias) for alias in payload.get("aliases") or [] if str(alias).strip()]
    capability_payload = payload.get("capability") or {}
    knowledge = payload.get("knowledge") or {}
    tagged_cards_payload = knowledge.get("tagged_cards") or {}

    capability = DomainCapability(
        family_id=family_id,
        display_name=str(capability_payload.get("display_name") or family_id),
        description=str(capability_payload.get("description") or ""),
        supported_variants=[str(value) for value in capability_payload.get("supported_variants") or []],
        canonical_objectives=[
            item for item in capability_payload.get("canonical_objectives") or [] if isinstance(item, dict)
        ],
        io_contract_notes=[str(value) for value in capability_payload.get("io_contract_notes") or []],
        evaluator_invariants=[str(value) for value in capability_payload.get("evaluator_invariants") or []],
        solver_entrypoints=[str(value) for value in capability_payload.get("solver_entrypoints") or []],
        specialization_hooks=[str(value) for value in capability_payload.get("specialization_hooks") or []],
        knowledge_tags=[str(value) for value in capability_payload.get("knowledge_tags") or []],
    )
    base_cards = [_resolve_pack_path(value, project_root=project_root) for value in knowledge.get("base_cards") or []]
    tagged_cards = {
        str(tag).strip().lower(): [
            _resolve_pack_path(value, project_root=project_root)
            for value in paths
            if str(value).strip()
        ]
        for tag, paths in tagged_cards_payload.items()
        if isinstance(paths, list)
    }
    return DomainPack(
        family_id=family_id,
        aliases=aliases,
        capability=capability,
        base_cards=base_cards,
        tagged_cards=tagged_cards,
        edit_strategies=[str(value) for value in payload.get("edit_strategies") or []],
        source_path=path,
    )


def load_domain_packs(
    *,
    domain_pack_root: Path = DOMAIN_PACK_ROOT,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, DomainPack]:
    packs: dict[str, DomainPack] = {}
    if not domain_pack_root.exists():
        return packs
    for manifest in sorted(domain_pack_root.glob("*/domain_pack.json")):
        pack = load_domain_pack(manifest, project_root=project_root)
        for key in pack.keys:
            packs[key] = pack
    return packs


def get_domain_pack(family_id: str, *, packs: dict[str, DomainPack] | None = None) -> DomainPack | None:
    loaded = packs if packs is not None else load_domain_packs()
    return loaded.get(_normalize_key(family_id)) or loaded.get("standard_fjsp")


def _resolve_pack_path(value: Any, *, project_root: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower()
