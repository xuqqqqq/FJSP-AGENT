"""知识检索与方法包选择。

本模块只根据领域包、实例诊断和已确认特征选择资料，不包含任何求解算法，
也不会把历史算例的具体解或目标值注入候选 solver。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_agent.domains.pack import get_domain_pack


_SDST_TAGS = {
    "sdst",
    "fjsp_sdst",
    "sequence_dependent_setup",
    "setup_time",
    "setup_times",
    "setup_matrix",
    "agent_generated_transfer",
}

_SDST_PATH_MARKERS = (
    "fjsp_sdst",
    "_sdst_",
    "sdst_hudata",
    "sdst_fattahi",
    "decoder_neighborhood.md",
)


@dataclass(frozen=True)
class KnowledgeSelection:
    """知识卡选择结果和审计信息。

    `cards` 给 worker 实际阅读，`audit` 给上游记录“为什么选/为什么没选”，
    便于解释 SDST 相关知识卡是否被 variant gate 拦住。
    """

    cards: list[Path]
    audit: dict[str, Any]


def method_package_catalog(
    *,
    problem_family: str,
    active_features: list[str] | None = None,
    knowledge_query_tags: list[str] | None = None,
) -> dict[str, Any]:
    """列出当前特征条件下可用的 Method Package。

    Method Package 是 domain pack 中声明的“算法方法资料包”，不是运行时插件。
    这里仅做兼容性筛选和推荐顺序计算，不负责真正执行其中的算法。
    """

    pack = get_domain_pack(problem_family)
    if pack is None:
        return {
            "status": "missing_domain_pack",
            "problem_family": problem_family,
            "packages": [],
            "recommended_package_id": None,
        }
    features = {str(item).strip().lower() for item in active_features or [] if str(item).strip()}
    query_tags = {str(item).strip().lower() for item in knowledge_query_tags or [] if str(item).strip()}
    packages = []
    for package in pack.method_packages:
        required = {str(item).strip().lower() for item in package.required_features if str(item).strip()}
        excluded = {str(item).strip().lower() for item in package.excluded_features if str(item).strip()}
        activation = {str(item).strip().lower() for item in package.activation_tags if str(item).strip()}
        if not package.selection_enabled or required - features or excluded & features:
            continue
        if query_tags and (not activation or not query_tags.intersection(activation)):
            continue
        packages.append(package.to_payload())
    packages.sort(key=lambda item: (-int(item.get("default_priority") or 0), str(item.get("package_id") or "")))
    return {
        "status": "ok",
        "problem_family": problem_family,
        "active_features": sorted(features),
        "knowledge_query_tags": sorted(query_tags),
        "packages": packages,
        "recommended_package_id": packages[0]["package_id"] if packages else None,
    }


def method_family_catalog(
    *,
    problem_family: str,
    active_features: list[str] | None = None,
) -> dict[str, Any]:
    """Expose canonical, feature-compatible method families to Main."""

    pack = get_domain_pack(problem_family)
    if pack is None:
        return {
            "status": "missing_domain_pack",
            "problem_family": problem_family,
            "families": [],
            "max_selected": 4,
        }
    features = _normalized_terms(active_features)
    families = []
    for family in pack.method_families:
        required = _normalized_terms(family.required_features)
        excluded = _normalized_terms(family.excluded_features)
        if required - features or excluded & features:
            continue
        families.append(family.to_payload())
    families.sort(key=lambda item: (-int(item.get("default_priority") or 0), str(item.get("family_id") or "")))
    return {
        "status": "ok",
        "problem_family": problem_family,
        "active_features": sorted(features),
        "families": families,
        "max_selected": 4,
    }


def resolve_worker_implementation_skills(
    *,
    problem_family: str,
    method_families: list[Any] | None,
    active_features: list[str] | None = None,
    knowledge_query_tags: list[str] | None = None,
    max_skills: int = 8,
) -> dict[str, Any]:
    """Resolve trusted Worker Skills from canonical families and active features.

    Callers provide family IDs, never filesystem paths. Skill source paths come
    only from the active Domain Pack and are later staged into an isolated
    ``.opencode/skills`` directory.
    """

    pack = get_domain_pack(problem_family)
    if pack is None:
        return {
            "status": "missing_domain_pack",
            "problem_family": problem_family,
            "method_families": [],
            "skills": [],
            "audit": {"rejected_method_families": [], "uncovered_method_families": []},
        }
    family_catalog = method_family_catalog(
        problem_family=problem_family,
        active_features=active_features,
    )
    eligible = {
        str(item.get("family_id") or "").strip().lower(): item
        for item in family_catalog.get("families") or []
        if isinstance(item, dict) and str(item.get("family_id") or "").strip()
    }
    selected: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in method_families or []:
        family_id = _method_family_id(item)
        if not family_id or family_id in selected:
            continue
        family = eligible.get(family_id)
        if family is None:
            rejected.append({"family_id": family_id, "reason": "unknown_or_feature_incompatible"})
            continue
        incompatible = {
            str(value).strip().lower()
            for value in family.get("incompatible_with") or []
            if str(value).strip()
        }
        conflict = next(
            (
                value
                for value in selected
                if value in incompatible
                or family_id
                in {
                    str(item).strip().lower()
                    for item in (eligible.get(value) or {}).get("incompatible_with") or []
                    if str(item).strip()
                }
            ),
            "",
        )
        if conflict:
            rejected.append({"family_id": family_id, "reason": f"incompatible_with:{conflict}"})
            continue
        selected.append(family_id)
        if len(selected) >= int(family_catalog.get("max_selected") or 4):
            break

    features = _normalized_terms(active_features)
    query_tags = _normalized_terms(knowledge_query_tags)
    candidates: list[tuple[int, dict[str, Any]]] = []
    excluded_skills: list[dict[str, str]] = []
    for skill in pack.worker_implementation_skills:
        required = _normalized_terms(skill.required_features)
        excluded = _normalized_terms(skill.excluded_features)
        covered = set(skill.method_families).intersection(selected)
        if required - features or excluded & features:
            excluded_skills.append({"skill_id": skill.skill_id, "reason": "feature_incompatible"})
            continue
        if not selected or (not skill.always_include and not covered):
            continue
        if not skill.source_path.is_dir() or not (skill.source_path / "SKILL.md").is_file():
            excluded_skills.append({"skill_id": skill.skill_id, "reason": "missing_skill_source"})
            continue
        tag_overlap = len(set(skill.activation_tags).intersection(query_tags))
        score = len(covered) * 1000 + tag_overlap * 100 + int(skill.default_priority)
        if skill.always_include:
            score += 10_000
        payload = skill.to_payload()
        payload["matched_method_families"] = [item for item in selected if item in covered]
        payload["sandbox_path"] = f".opencode/skills/{skill.skill_id}"
        candidates.append((score, payload))
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("skill_id") or "")))
    skills = [item[1] for item in candidates[: max(1, min(8, int(max_skills)))]]
    covered_families = {
        family_id
        for skill in skills
        for family_id in skill.get("matched_method_families") or []
    }
    uncovered = [family_id for family_id in selected if family_id not in covered_families]
    return {
        "status": "ok" if selected and not uncovered else "partial" if selected else "no_selection",
        "problem_family": problem_family,
        "active_features": sorted(features),
        "method_families": [
            {"id": family_id, "role": "primary" if index == 0 else "complementary"}
            for index, family_id in enumerate(selected)
        ],
        "skills": skills,
        "audit": {
            "requested_method_families": [_method_family_id(item) for item in method_families or []],
            "rejected_method_families": rejected,
            "uncovered_method_families": uncovered,
            "excluded_skills": excluded_skills,
            "selected_skill_ids": [str(item.get("skill_id") or "") for item in skills],
        },
    }


def resolve_method_package(
    *,
    problem_family: str,
    package_id: str | None,
    active_features: list[str] | None = None,
    knowledge_query_tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """解析一个 Method Package 请求。

    只接受调用方显式选择且与当前特征、查询标签兼容的 package_id。
    空值或无效值必须返回 ``None``，避免推荐包绕过两阶段 Main 选择。
    """

    catalog = method_package_catalog(
        problem_family=problem_family,
        active_features=active_features,
        knowledge_query_tags=knowledge_query_tags,
    )
    packages = [item for item in catalog.get("packages") or [] if isinstance(item, dict)]
    requested = str(package_id or "").strip().lower()
    if not requested:
        return None
    for package in packages:
        if str(package.get("package_id") or "").strip().lower() == requested:
            return package
    return None


def knowledge_query_catalog(*, problem_family: str) -> dict[str, Any]:
    """Expose the curated first-stage query vocabulary without card contents.

    `tagged_cards` also contains implementation-level aliases such as concrete
    neighborhood names.  Only tags with an explicit Domain Pack description are
    public to the first-stage Main Agent; this keeps retrieval expressive without
    leaking whichever Method Package currently has the richest documentation.
    """

    pack = get_domain_pack(problem_family)
    if pack is None:
        return {"status": "missing_domain_pack", "tags": []}
    tags = []
    public_tags = set(pack.knowledge_query_tag_descriptions).intersection(pack.tagged_cards)
    for tag in sorted(public_tags):
        tags.append(
            {
                "tag": tag,
                "description": pack.knowledge_query_tag_descriptions.get(tag, ""),
                "card_count": len(pack.tagged_cards.get(tag) or []),
            }
        )
    return {
        "status": "ok",
        "default_limit": pack.knowledge_query_default_limit,
        "tags": tags,
    }


def auto_knowledge_cards(
    *,
    problem_family: str,
    problem_family_tags: list[str] | None = None,
    slot_manifest: dict[str, Any] | None = None,
    instance_diagnostics: dict[str, Any] | None = None,
    active_features: list[str] | None = None,
) -> list[Path]:
    """Select knowledge cards declared by the active external domain pack."""

    return select_knowledge_cards(
        problem_family=problem_family,
        problem_family_tags=problem_family_tags,
        slot_manifest=slot_manifest,
        instance_diagnostics=instance_diagnostics,
        active_features=active_features,
    ).cards


def selection_cards(
    *,
    problem_family: str,
    stage: str,
) -> list[Path]:
    """Return first-stage strategy/direction selection cards from the active Domain Pack."""

    pack = get_domain_pack(problem_family)
    if pack is None:
        return []
    return _existing_unique_paths(pack.selection_cards(stage))


def select_knowledge_cards(
    *,
    problem_family: str,
    problem_family_tags: list[str] | None = None,
    slot_manifest: dict[str, Any] | None = None,
    instance_diagnostics: dict[str, Any] | None = None,
    active_features: list[str] | None = None,
) -> KnowledgeSelection:
    """Select knowledge cards and record why variant-specific cards were gated.

    Domain Pack 只声明“这个问题族理论上支持什么资料和变体”，并不证明当前实例
    一定真的启用了这些特征。因此这里把已解析实例特征、已确认 slot 和显式标签
    当成更强证据，避免错误把 SDST 专用知识注入普通 FJSP。
    """

    pack = get_domain_pack(problem_family)
    if pack is None:
        return KnowledgeSelection(cards=[], audit={"status": "missing_domain_pack", "problem_family": problem_family})

    tags = {str(tag).strip().lower() for tag in (problem_family_tags or []) if str(tag).strip()}
    requested_tags = sorted(tags)
    confirmed_slot_ids: list[str] = []
    if slot_manifest:
        for slot in slot_manifest.get("slots") or []:
            if not isinstance(slot, dict) or not slot.get("user_confirmed"):
                continue
            slot_id = str(slot.get("slot_id") or "").strip()
            if slot_id:
                confirmed_slot_ids.append(slot_id)
            tags.update(str(tag).strip().lower() for tag in slot.get("knowledge_tags") or [] if str(tag).strip())

    sdst_active = _sequence_dependent_setup_active(
        tags=tags,
        slot_manifest=slot_manifest,
        instance_diagnostics=instance_diagnostics,
        active_features=active_features,
    )
    if sdst_active:
        tags.update({"sdst", "setup_time", "sequence_dependent_setup"})
    else:
        tags.difference_update(_SDST_TAGS)

    selected: list[Path] = []
    excluded: list[dict[str, str]] = []

    def add_candidate(path: Path, *, source: str) -> None:
        if _is_experiment_memory_path(path):
            excluded.append(
                {
                    "path": str(path),
                    "source": source,
                    "reason": "experiment_memory_requires_explicit_replay",
                }
            )
            return
        if not sdst_active and _is_sdst_specific_path(path):
            excluded.append(
                {
                    "path": str(path),
                    "source": source,
                    "reason": "inactive_sequence_dependent_setup",
                }
            )
            return
        selected.append(path)

    for path in pack.base_cards:
        add_candidate(path, source="base_cards")
    for tag in sorted(tags):
        for path in pack.tagged_cards.get(tag, []):
            add_candidate(path, source=f"tag:{tag}")

    cards = _existing_unique_paths(selected)
    audit = {
        "status": "ok",
        "problem_family": problem_family,
        "domain_pack": pack.family_id,
        "active_variant": "fjsp_sdst" if sdst_active else "standard_fjsp",
        "active_features": sorted(_active_feature_terms(instance_diagnostics, active_features)),
        "requested_tags": requested_tags,
        "effective_tags": sorted(tags),
        "confirmed_slot_ids": confirmed_slot_ids,
        "excluded_cards": excluded[:40],
        "excluded_card_count": len(excluded),
        "selected_card_count": len(cards),
    }
    return KnowledgeSelection(cards=cards, audit=audit)


def select_tagged_knowledge_cards(
    *,
    problem_family: str,
    knowledge_query_tags: list[str] | None = None,
    problem_family_tags: list[str] | None = None,
    slot_manifest: dict[str, Any] | None = None,
    instance_diagnostics: dict[str, Any] | None = None,
    active_features: list[str] | None = None,
    max_cards: int | None = None,
) -> KnowledgeSelection:
    """Second-stage retrieval for detailed tagged cards chosen by Main.

    这里不返回 base cards，只根据 Main 已经选中的 knowledge_query tags 取更细的
    tagged cards。筛选流程必须经过变体 gate、去重、数据声明的路径排除规则，以及
    数量上限，避免在 standard FJSP 中再次混入 Domain Pack 暂时禁用的方法资产。
    """

    pack = get_domain_pack(problem_family)
    if pack is None:
        return KnowledgeSelection(cards=[], audit={"status": "missing_domain_pack", "problem_family": problem_family})

    requested_tags = sorted({str(tag).strip().lower() for tag in knowledge_query_tags or [] if str(tag).strip()})
    sdst_active = _sequence_dependent_setup_active(
        tags={str(tag).strip().lower() for tag in problem_family_tags or [] if str(tag).strip()},
        slot_manifest=slot_manifest,
        instance_diagnostics=instance_diagnostics,
        active_features=active_features,
    )
    effective_tags = set(requested_tags)
    if sdst_active:
        effective_tags.update({"sdst", "setup_time", "sequence_dependent_setup"})
    else:
        effective_tags.difference_update(_SDST_TAGS)

    selected: list[Path] = []
    excluded: list[dict[str, str]] = []

    def add_candidate(path: Path, *, source: str) -> None:
        if _is_experiment_memory_path(path):
            excluded.append(
                {
                    "path": str(path),
                    "source": source,
                    "reason": "experiment_memory_requires_explicit_replay",
                }
            )
            return
        if not sdst_active and _is_sdst_specific_path(path):
            excluded.append(
                {
                    "path": str(path),
                    "source": source,
                    "reason": "inactive_sequence_dependent_setup",
                }
            )
            return
        if _matches_query_excluded_path_marker(path, markers=pack.knowledge_query_excluded_path_markers):
            excluded.append(
                {
                    "path": str(path),
                    "source": source,
                    "reason": "domain_pack_secondary_query_exclusion",
                }
            )
            return
        selected.append(path)

    for tag in requested_tags:
        for path in pack.tagged_cards.get(tag, []):
            add_candidate(path, source=f"tag:{tag}")

    unique_cards = _existing_unique_paths(selected)
    limit = max(1, int(max_cards or pack.knowledge_query_default_limit or 6))
    cards = unique_cards[:limit]
    audit = {
        "status": "ok",
        "problem_family": problem_family,
        "domain_pack": pack.family_id,
        "selection_mode": "tagged_query",
        "active_variant": "fjsp_sdst" if sdst_active else "standard_fjsp",
        "active_features": sorted(_active_feature_terms(instance_diagnostics, active_features)),
        "requested_tags": requested_tags,
        "effective_tags": sorted(effective_tags),
        "selected_card_count": len(cards),
        "candidate_card_count": len(unique_cards),
        "max_cards": limit,
        "excluded_cards": excluded[:40],
        "excluded_card_count": len(excluded),
    }
    return KnowledgeSelection(cards=cards, audit=audit)


def _sequence_dependent_setup_active(
    *,
    tags: set[str],
    slot_manifest: dict[str, Any] | None,
    instance_diagnostics: dict[str, Any] | None,
    active_features: list[str] | None,
) -> bool:
    """综合判断当前任务是否真的启用了 SDST。

    判定优先级大致是：已确认 slot 需求 > 已解析实例诊断 > 活跃特征 > 弱标签。
    这样可最大限度减少仅凭文件名/家族能力误判变体的情况。
    """

    if _slot_requests_sdst(slot_manifest):
        return True
    diagnostic_state = _sdst_state_from_diagnostics(instance_diagnostics)
    if diagnostic_state is not None:
        return diagnostic_state
    feature_terms = _active_feature_terms(instance_diagnostics, active_features)
    if feature_terms & _SDST_TAGS:
        return True
    return bool(tags & _SDST_TAGS)


def _active_feature_terms(
    instance_diagnostics: dict[str, Any] | None,
    active_features: list[str] | None,
) -> set[str]:
    terms = {str(value).strip().lower() for value in (active_features or []) if str(value).strip()}
    if _sdst_state_from_diagnostics(instance_diagnostics):
        terms.update({"fjsp_sdst", "sequence_dependent_setup", "setup_time"})
    return terms


def _sdst_state_from_diagnostics(instance_diagnostics: dict[str, Any] | None) -> bool | None:
    if not isinstance(instance_diagnostics, dict):
        return None
    summary = instance_diagnostics.get("summary") if isinstance(instance_diagnostics.get("summary"), dict) else {}
    profiled_count = int(summary.get("profiled_count") or 0)
    instance_count = int(summary.get("instance_count") or 0)
    instances = [item for item in instance_diagnostics.get("instances") or [] if isinstance(item, dict)]
    diagnostics_have_shape = (
        instance_diagnostics.get("status") in {"available", "partial"}
        and (profiled_count > 0 or instance_count > 0 or bool(instances))
    )
    if not diagnostics_have_shape:
        return None

    setup_kinds = [str(kind).strip().lower() for kind in summary.get("setup_time_kinds") or []]
    if int(summary.get("sdst_instance_count") or 0) > 0:
        return True
    if any(kind not in {"", "none", "null"} for kind in setup_kinds):
        return True
    for item in instances:
        variant = str(item.get("variant") or "").strip().lower()
        setup_kind = str(item.get("setup_time_kind") or "").strip().lower()
        if variant == "fjsp_sdst" or setup_kind not in {"", "none", "null"}:
            return True
    return False


def _slot_requests_sdst(slot_manifest: dict[str, Any] | None) -> bool:
    if not isinstance(slot_manifest, dict):
        return False
    for slot in slot_manifest.get("slots") or []:
        if not isinstance(slot, dict) or not slot.get("user_confirmed"):
            continue
        tags = {str(tag).strip().lower() for tag in slot.get("knowledge_tags") or [] if str(tag).strip()}
        if tags & _SDST_TAGS:
            return True
    return False


def _is_sdst_specific_path(path: Path) -> bool:
    normalized = str(path).replace("\\", "/").lower()
    return any(marker in normalized for marker in _SDST_PATH_MARKERS)


def _is_experiment_memory_path(path: Path) -> bool:
    """实验记录只能通过显式经验回放进入 Main，不能作为默认方法知识。"""

    parts = {part.lower() for part in path.parts}
    return "experiment_memory" in parts


def _matches_query_excluded_path_marker(path: Path, *, markers: list[str]) -> bool:
    normalized = str(path).replace("\\", "/").lower()
    return any(marker in normalized for marker in markers)


def _normalized_terms(values: Any) -> set[str]:
    return {
        str(item).strip().lower()
        for item in values or []
        if str(item).strip()
    }


def _method_family_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("family_id")
    return str(value or "").strip().lower()


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
