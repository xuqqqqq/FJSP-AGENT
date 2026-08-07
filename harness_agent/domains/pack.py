"""加载外置 domain pack、知识映射和只读 Core 依赖。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_PACK_ROOT = PROJECT_ROOT / "domain_packs"


@dataclass(frozen=True)
class DomainCapability:
    """问题族能力卡。

    这张卡描述的是某个问题族在平台中的公共契约边界：支持哪些变体、典型目标、
    IO 备注、evaluator 不变量和可选专用钩子，而不是具体算法实现。
    """

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
    """Domain Pack 声明的可选编辑策略。

    它通常指向额外的 manifest/模板/规则资产，例如 slot-based edit 插件；默认
    闭环可以完全不用这些策略。
    """

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
    """问题族内的一个 Method Package。

    Method Package 是给 worker 看的方法资料组合，不是 Python 插件或运行时对象。
    它用 `required_features/excluded_features` 指定适用边界，避免错误迁移算法。
    """

    package_id: str
    title: str
    description: str = ""
    strategy_types: list[str] = field(default_factory=list)
    activation_tags: list[str] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    assets: list[Path] = field(default_factory=list)
    semantic_assets: list[Path] = field(default_factory=list)
    implementation_asset: Path | None = None
    implementation_contract_asset: Path | None = None
    implementation_contract_assets: list[Path] = field(default_factory=list)
    implementation_contract: dict[str, Any] = field(default_factory=dict)
    default_priority: int = 0
    selection_enabled: bool = True
    disabled_reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "title": self.title,
            "description": self.description,
            "strategy_types": list(self.strategy_types),
            "activation_tags": list(self.activation_tags),
            "required_features": list(self.required_features),
            "excluded_features": list(self.excluded_features),
            "assets": [str(path) for path in self.assets],
            "semantic_assets": [str(path) for path in self.semantic_assets],
            "implementation_asset": str(self.implementation_asset) if self.implementation_asset else None,
            "implementation_contract_asset": (
                str(self.implementation_contract_asset) if self.implementation_contract_asset else None
            ),
            "implementation_contract_assets": [str(path) for path in self.implementation_contract_assets],
            "implementation_contract": dict(self.implementation_contract),
            "default_priority": self.default_priority,
            "selection_enabled": self.selection_enabled,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class DomainMethodFamily:
    """Main 可选择的规范方法族，不包含实现路径。"""

    family_id: str
    title: str
    description: str = ""
    query_tags: list[str] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    incompatible_with: list[str] = field(default_factory=list)
    default_priority: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "title": self.title,
            "description": self.description,
            "query_tags": list(self.query_tags),
            "required_features": list(self.required_features),
            "excluded_features": list(self.excluded_features),
            "incompatible_with": list(self.incompatible_with),
            "default_priority": self.default_priority,
        }


@dataclass(frozen=True)
class DomainWorkerImplementationSkill:
    """Domain Pack 声明的 Coding Agent 实现 Skill。"""

    skill_id: str
    title: str
    source_path: Path
    description: str = ""
    method_families: list[str] = field(default_factory=list)
    activation_tags: list[str] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    default_priority: int = 0
    always_include: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "title": self.title,
            "description": self.description,
            "source_path": str(self.source_path),
            "method_families": list(self.method_families),
            "activation_tags": list(self.activation_tags),
            "required_features": list(self.required_features),
            "excluded_features": list(self.excluded_features),
            "default_priority": self.default_priority,
            "always_include": self.always_include,
        }


@dataclass(frozen=True)
class DomainPack:
    """一个问题族的外置 Domain Pack。

    Domain Pack 的边界是“提供能力说明、知识卡、方法包和可选编辑资产”。它不承担
    parser/validator 运行逻辑，也不直接决定当前任务一定使用哪套方法。
    """

    family_id: str
    aliases: list[str]
    capability: DomainCapability
    base_cards: list[Path] = field(default_factory=list)
    tagged_cards: dict[str, list[Path]] = field(default_factory=dict)
    strategy_selection_cards: list[Path] = field(default_factory=list)
    direction_selection_cards: list[Path] = field(default_factory=list)
    knowledge_query_default_limit: int = 6
    knowledge_query_excluded_path_markers: list[str] = field(default_factory=list)
    knowledge_query_tag_descriptions: dict[str, str] = field(default_factory=dict)
    rag_config: dict[str, Any] = field(default_factory=dict)
    method_families: list[DomainMethodFamily] = field(default_factory=list)
    worker_implementation_skills: list[DomainWorkerImplementationSkill] = field(default_factory=list)
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

    def method_family(self, family_id: str) -> DomainMethodFamily | None:
        normalized = _normalize_key(family_id)
        for family in self.method_families:
            if _normalize_key(family.family_id) == normalized:
                return family
        return None

    def worker_implementation_skill(self, skill_id: str) -> DomainWorkerImplementationSkill | None:
        normalized = _normalize_key(skill_id)
        for skill in self.worker_implementation_skills:
            if _normalize_key(skill.skill_id) == normalized:
                return skill
        return None

    def selection_cards(self, stage: str) -> list[Path]:
        normalized = _normalize_key(stage)
        if normalized == "strategy":
            return list(self.strategy_selection_cards)
        if normalized == "direction":
            return list(self.direction_selection_cards)
        return []


def load_domain_pack(path: Path, *, project_root: Path = PROJECT_ROOT) -> DomainPack:
    """从 `domain_pack.json` 加载一个问题族包。"""

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    family_id = str(payload.get("family_id") or path.parent.name)
    aliases = [str(alias) for alias in payload.get("aliases") or [] if str(alias).strip()]
    capability_payload = payload.get("capability") or {}
    knowledge = payload.get("knowledge") or {}
    selection_cards_payload = knowledge.get("selection_cards") or {}
    knowledge_query_payload = knowledge.get("knowledge_query") or {}
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
        strategy_selection_cards=[
            _resolve_pack_path(value, project_root=project_root)
            for value in selection_cards_payload.get("strategy") or []
            if str(value).strip()
        ],
        direction_selection_cards=[
            _resolve_pack_path(value, project_root=project_root)
            for value in selection_cards_payload.get("direction") or []
            if str(value).strip()
        ],
        knowledge_query_default_limit=max(1, int(knowledge_query_payload.get("default_limit") or 6)),
        knowledge_query_excluded_path_markers=[
            str(value).strip().lower()
            for value in knowledge_query_payload.get("exclude_path_markers") or []
            if str(value).strip()
        ],
        knowledge_query_tag_descriptions={
            str(tag).strip().lower(): str(description).strip()
            for tag, description in (knowledge_query_payload.get("tag_descriptions") or {}).items()
            if str(tag).strip() and str(description).strip()
        },
        rag_config=dict(knowledge.get("rag") or {}),
        method_families=[
            _load_method_family(value)
            for value in payload.get("method_families") or []
            if isinstance(value, dict) and str(value.get("family_id") or "").strip()
        ],
        worker_implementation_skills=[
            _load_worker_implementation_skill(value, project_root=project_root)
            for value in payload.get("worker_implementation_skills") or []
            if isinstance(value, dict) and str(value.get("skill_id") or "").strip()
        ],
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
    """按 family_id 或别名获取 Domain Pack。

    `fallback_to_standard=True` 时会回退到标准 FJSP 包，目的是保证通用流程可继续，
    不是在语义上宣称未知问题族等价于 FJSP。
    """

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


def _load_method_family(value: dict[str, Any]) -> DomainMethodFamily:
    return DomainMethodFamily(
        family_id=str(value.get("family_id") or "").strip().lower(),
        title=str(value.get("title") or value.get("family_id") or "").strip(),
        description=str(value.get("description") or "").strip(),
        query_tags=_normalized_terms(value.get("query_tags")),
        required_features=_normalized_terms(value.get("required_features")),
        excluded_features=_normalized_terms(value.get("excluded_features")),
        incompatible_with=_normalized_terms(value.get("incompatible_with")),
        default_priority=int(value.get("default_priority") or 0),
    )


def _load_worker_implementation_skill(
    value: dict[str, Any],
    *,
    project_root: Path,
) -> DomainWorkerImplementationSkill:
    return DomainWorkerImplementationSkill(
        skill_id=str(value.get("skill_id") or "").strip().lower(),
        title=str(value.get("title") or value.get("skill_id") or "").strip(),
        description=str(value.get("description") or "").strip(),
        source_path=_resolve_pack_path(value.get("source_path") or "", project_root=project_root),
        method_families=_normalized_terms(value.get("method_families")),
        activation_tags=_normalized_terms(value.get("activation_tags")),
        required_features=_normalized_terms(value.get("required_features")),
        excluded_features=_normalized_terms(value.get("excluded_features")),
        default_priority=int(value.get("default_priority") or 0),
        always_include=bool(value.get("always_include", False)),
    )


def _normalized_terms(value: Any) -> list[str]:
    return [
        str(item).strip().lower()
        for item in value or []
        if str(item).strip()
    ]


def _load_method_package(value: dict[str, Any], *, project_root: Path) -> DomainMethodPackage:
    implementation_value = str(value.get("implementation_asset") or "").strip()
    contract_value = str(value.get("implementation_contract_asset") or "").strip()
    contract_path = _resolve_pack_path(contract_value, project_root=project_root) if contract_value else None
    implementation_contract, contract_assets = _load_method_implementation_contract(
        contract_path,
        project_root=project_root,
    )
    return DomainMethodPackage(
        package_id=str(value.get("package_id") or "").strip(),
        title=str(value.get("title") or value.get("package_id") or "").strip(),
        description=str(value.get("description") or "").strip(),
        strategy_types=[str(item) for item in value.get("strategy_types") or [] if str(item).strip()],
        activation_tags=[str(item).strip().lower() for item in value.get("activation_tags") or [] if str(item).strip()],
        required_features=[str(item) for item in value.get("required_features") or [] if str(item).strip()],
        excluded_features=[str(item) for item in value.get("excluded_features") or [] if str(item).strip()],
        assets=[
            _resolve_pack_path(item, project_root=project_root)
            for item in value.get("assets") or []
            if str(item).strip()
        ],
        semantic_assets=[
            _resolve_pack_path(item, project_root=project_root)
            for item in value.get("semantic_assets") or []
            if str(item).strip()
        ],
        implementation_asset=(
            _resolve_pack_path(implementation_value, project_root=project_root)
            if implementation_value
            else None
        ),
        implementation_contract_asset=contract_path,
        implementation_contract_assets=contract_assets,
        implementation_contract=implementation_contract,
        default_priority=int(value.get("default_priority") or 0),
        selection_enabled=bool(value.get("selection_enabled", True)),
        disabled_reason=str(value.get("disabled_reason") or "").strip(),
    )


def _load_method_implementation_contract(
    path: Path | None,
    *,
    project_root: Path,
    loading: tuple[Path, ...] = (),
) -> tuple[dict[str, Any], list[Path]]:
    """读取并合并通用契约继承链；后端不解释任何组件的算法含义。"""

    if path is None:
        return {}, []
    path = path.resolve()
    if path in loading:
        cycle = " -> ".join(str(item) for item in (*loading, path))
        raise ValueError(f"method implementation contract inheritance cycle: {cycle}")
    if not path.is_file():
        raise ValueError(f"method implementation contract does not exist: {path}")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"method implementation contract must be a JSON object: {path}")
    _validate_method_contract_fragment(raw, path=path)

    payload: dict[str, Any] = {}
    source_assets: list[Path] = []
    extends = raw.get("extends") or []
    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list):
        raise ValueError(f"method implementation contract extends must be a path list: {path}")
    for parent_value in extends:
        parent_text = str(parent_value or "").strip()
        if not parent_text:
            continue
        parent_path = _resolve_pack_path(parent_text, project_root=project_root)
        parent, parent_assets = _load_method_implementation_contract(
            parent_path,
            project_root=project_root,
            loading=(*loading, path),
        )
        payload = _merge_method_contracts(payload, parent)
        source_assets.extend(parent_assets)
    payload = _merge_method_contracts(payload, raw)
    source_assets.append(path)
    source_assets = list(dict.fromkeys(source_assets))
    _validate_method_implementation_contract(payload, path=path)
    return payload, source_assets


def _validate_method_contract_fragment(payload: dict[str, Any], *, path: Path) -> None:
    """继承合并前拒绝会被合并器静默忽略的畸形条目。"""

    for list_key, id_key in (("required_components", "component_id"), ("coupled_groups", "group_id")):
        values = payload.get(list_key) or []
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError(f"method implementation contract {list_key} must be an object list: {path}")
        identifiers = [str(item.get(id_key) or "").strip() for item in values]
        if any(not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError(f"method implementation contract has missing/duplicate {id_key}: {path}")


def _merge_method_contracts(base: dict[str, Any], extension: dict[str, Any]) -> dict[str, Any]:
    """按稳定 ID 合并契约片段；同名组件只能追加行为，不能删除父契约要求。"""

    merged = {key: value for key, value in base.items() if key not in {"required_components", "coupled_groups"}}
    merged.update(
        {
            key: value
            for key, value in extension.items()
            if key not in {"required_components", "coupled_groups", "extends"}
        }
    )
    components: dict[str, dict[str, Any]] = {}
    for value in [*(base.get("required_components") or []), *(extension.get("required_components") or [])]:
        if not isinstance(value, dict):
            continue
        component_id = str(value.get("component_id") or "").strip()
        previous = components.get(component_id, {})
        behaviors = list(
            dict.fromkeys(
                [
                    *(str(item) for item in previous.get("required_behaviors") or [] if str(item).strip()),
                    *(str(item) for item in value.get("required_behaviors") or [] if str(item).strip()),
                ]
            )
        )
        components[component_id] = {**previous, **value, "required_behaviors": behaviors}
    groups: dict[str, dict[str, Any]] = {}
    for value in [*(base.get("coupled_groups") or []), *(extension.get("coupled_groups") or [])]:
        if not isinstance(value, dict):
            continue
        group_id = str(value.get("group_id") or "").strip()
        previous = groups.get(group_id, {})
        component_ids = list(
            dict.fromkeys(
                [
                    *(str(item) for item in previous.get("component_ids") or [] if str(item).strip()),
                    *(str(item) for item in value.get("component_ids") or [] if str(item).strip()),
                ]
            )
        )
        groups[group_id] = {**previous, **value, "component_ids": component_ids}
    merged["required_components"] = list(components.values())
    merged["coupled_groups"] = list(groups.values())
    return merged


def _validate_method_implementation_contract(payload: dict[str, Any], *, path: Path) -> None:
    """验证通用 schema 以及继承合并后的引用完整性。"""

    components = payload.get("required_components")
    if not isinstance(components, list) or not components:
        raise ValueError(f"method implementation contract must declare required_components: {path}")
    component_ids: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError(f"method implementation contract components must be objects: {path}")
        component_id = str(component.get("component_id") or "").strip()
        if not component_id or component_id in component_ids:
            raise ValueError(f"method implementation contract has missing/duplicate component_id: {path}")
        behaviors = component.get("required_behaviors")
        if not isinstance(behaviors, list) or not any(str(item).strip() for item in behaviors):
            raise ValueError(f"method component {component_id!r} must declare required_behaviors: {path}")
        component_ids.add(component_id)

    group_ids: set[str] = set()
    for group in payload.get("coupled_groups") or []:
        if not isinstance(group, dict):
            raise ValueError(f"method implementation contract coupled_groups must be objects: {path}")
        group_id = str(group.get("group_id") or "").strip()
        if not group_id or group_id in group_ids:
            raise ValueError(f"method implementation contract has missing/duplicate group_id: {path}")
        referenced = {str(item).strip() for item in group.get("component_ids") or [] if str(item).strip()}
        if not referenced or referenced - component_ids:
            unknown = sorted(referenced - component_ids)
            raise ValueError(
                f"method coupled group {group_id!r} must reference declared components; unknown={unknown}: {path}"
            )
        if not str(group.get("rule") or "").strip():
            raise ValueError(f"method coupled group {group_id!r} must declare a rule: {path}")
        group_ids.add(group_id)


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower()
