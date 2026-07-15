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
    packages = []
    for package in pack.method_packages:
        required = {str(item).strip().lower() for item in package.required_features if str(item).strip()}
        excluded = {str(item).strip().lower() for item in package.excluded_features if str(item).strip()}
        if required - features or excluded & features:
            continue
        packages.append(package.to_payload())
    packages.sort(key=lambda item: (-int(item.get("default_priority") or 0), str(item.get("package_id") or "")))
    return {
        "status": "ok",
        "problem_family": problem_family,
        "active_features": sorted(features),
        "packages": packages,
        "recommended_package_id": packages[0]["package_id"] if packages else None,
    }


def resolve_method_package(
    *,
    problem_family: str,
    package_id: str | None,
    active_features: list[str] | None = None,
) -> dict[str, Any] | None:
    """解析一个 Method Package 请求。

    如果调用方没有指定或指定了无效 package_id，会回退到当前特征下的推荐包，
    这样 worker 始终只会拿到一个明确的资料包，而不是混合多套方法。
    """

    catalog = method_package_catalog(problem_family=problem_family, active_features=active_features)
    packages = [item for item in catalog.get("packages") or [] if isinstance(item, dict)]
    requested = str(package_id or "").strip().lower()
    for package in packages:
        if str(package.get("package_id") or "").strip().lower() == requested:
            return package
    recommended = str(catalog.get("recommended_package_id") or "").strip().lower()
    return next(
        (
            package
            for package in packages
            if str(package.get("package_id") or "").strip().lower() == recommended
        ),
        None,
    )


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
