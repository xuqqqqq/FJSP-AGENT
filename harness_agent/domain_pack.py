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
class DomainEditStrategy:
    """Optional editing strategy declared by a domain pack."""

    name: str
    description: str = ""
    assets: dict[str, Path] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def asset_path(self, key: str) -> Path | None:
        return self.assets.get(str(key))

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "assets": {key: str(path) for key, path in self.assets.items()},
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class DomainMethodPackage:
    """One reusable algorithm method package owned by a domain pack."""

    package_id: str
    title: str
    description: str = ""
    strategy_types: list[str] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    assets: list[Path] = field(default_factory=list)
    implementation_asset: Path | None = None
    default_priority: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "title": self.title,
            "description": self.description,
            "strategy_types": list(self.strategy_types),
            "required_features": list(self.required_features),
            "excluded_features": list(self.excluded_features),
            "assets": [str(path) for path in self.assets],
            "implementation_asset": str(self.implementation_asset) if self.implementation_asset else None,
            "default_priority": self.default_priority,
        }


@dataclass(frozen=True)
class DomainPack:
    """External domain assets for one optimization problem family."""

    family_id: str
    aliases: list[str]
    capability: DomainCapability
    base_cards: list[Path] = field(default_factory=list)
    tagged_cards: dict[str, list[Path]] = field(default_factory=dict)
    method_packages: list[DomainMethodPackage] = field(default_factory=list)
    edit_strategies: list[DomainEditStrategy] = field(default_factory=list)
    semantic_review_cards: list[Path] = field(default_factory=list)
    agent_generated_baseline_preserve_paths: list[str] = field(default_factory=list)
    agent_generated_baseline_hidden_paths: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def keys(self) -> set[str]:
        return {_normalize_key(self.family_id), *(_normalize_key(alias) for alias in self.aliases)}

    def edit_strategy(self, name: str) -> DomainEditStrategy | None:
        normalized = _normalize_key(name)
        for strategy in self.edit_strategies:
            if _normalize_key(strategy.name) == normalized:
                return strategy
        return None

    def method_package(self, package_id: str) -> DomainMethodPackage | None:
        normalized = _normalize_key(package_id)
        for package in self.method_packages:
            if _normalize_key(package.package_id) == normalized:
                return package
        return None


def load_domain_pack(path: Path, *, project_root: Path = PROJECT_ROOT) -> DomainPack:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    family_id = str(payload.get("family_id") or path.parent.name)
    aliases = [str(alias) for alias in payload.get("aliases") or [] if str(alias).strip()]
    capability_payload = payload.get("capability") or {}
    knowledge = payload.get("knowledge") or {}
    semantic_review = payload.get("semantic_review") or {}
    agent_generated_baseline = payload.get("agent_generated_baseline") or {}
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
        method_packages=[
            _load_method_package(value, project_root=project_root)
            for value in payload.get("method_packages") or []
            if isinstance(value, dict) and str(value.get("package_id") or "").strip()
        ],
        edit_strategies=[
            _load_edit_strategy(value, project_root=project_root)
            for value in payload.get("edit_strategies") or []
            if _loadable_edit_strategy(value)
        ],
        semantic_review_cards=[
            _resolve_pack_path(value, project_root=project_root)
            for value in semantic_review.get("cards") or []
            if str(value).strip()
        ],
        agent_generated_baseline_preserve_paths=[
            str(value).replace("\\", "/")
            for value in agent_generated_baseline.get("preserve_paths") or []
            if str(value).strip()
        ],
        agent_generated_baseline_hidden_paths=[
            str(value).replace("\\", "/")
            for value in agent_generated_baseline.get("hidden_reference_paths") or []
            if str(value).strip()
        ],
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


def get_domain_pack(
    family_id: str,
    *,
    packs: dict[str, DomainPack] | None = None,
    fallback_to_standard: bool = True,
) -> DomainPack | None:
    loaded = packs if packs is not None else load_domain_packs()
    pack = loaded.get(_normalize_key(family_id))
    if pack is not None:
        return pack
    if fallback_to_standard:
        return loaded.get("standard_fjsp")
    return None


def _resolve_pack_path(value: Any, *, project_root: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _loadable_edit_strategy(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(str(value.get("name") or "").strip())
    return False


def _load_edit_strategy(value: Any, *, project_root: Path) -> DomainEditStrategy:
    if isinstance(value, str):
        return DomainEditStrategy(name=value)
    payload = value if isinstance(value, dict) else {}
    raw_assets = payload.get("assets") or {}
    assets = {
        str(key): _resolve_pack_path(asset_path, project_root=project_root)
        for key, asset_path in raw_assets.items()
        if str(key).strip() and str(asset_path).strip()
    }
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    return DomainEditStrategy(
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        assets=assets,
        options=dict(options),
    )


def _load_method_package(value: dict[str, Any], *, project_root: Path) -> DomainMethodPackage:
    implementation_value = str(value.get("implementation_asset") or "").strip()
    return DomainMethodPackage(
        package_id=str(value.get("package_id") or "").strip(),
        title=str(value.get("title") or value.get("package_id") or "").strip(),
        description=str(value.get("description") or "").strip(),
        strategy_types=[str(item) for item in value.get("strategy_types") or [] if str(item).strip()],
        required_features=[str(item) for item in value.get("required_features") or [] if str(item).strip()],
        excluded_features=[str(item) for item in value.get("excluded_features") or [] if str(item).strip()],
        assets=[
            _resolve_pack_path(item, project_root=project_root)
            for item in value.get("assets") or []
            if str(item).strip()
        ],
        implementation_asset=(
            _resolve_pack_path(implementation_value, project_root=project_root)
            if implementation_value
            else None
        ),
        default_priority=int(value.get("default_priority") or 0),
    )


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower()
