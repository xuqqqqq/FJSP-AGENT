from __future__ import annotations

from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


FJSP_BASE_CARDS = [
    "knowledge/benchmarks/standard_fjsp_format.md",
    "knowledge/benchmarks/fjsplib.md",
    "knowledge/principles/harness_agent_design.md",
    "knowledge/papers/fjsp_scene_survey_2025_10_17.md",
]

TAGGED_CARDS = {
    "awls": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
        "knowledge/imported_huawei_fjsp_knowledge/lessons/profile_driven_local_search_20260615.md",
    ],
    "sdst": [
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
        "knowledge/papers/awls_sdst_initialization_notes.md",
        "knowledge/papers/awls_sdst_same_machine_notes.md",
        "knowledge/papers/awls_sdst_move_evaluation_notes.md",
        "knowledge/papers/awls_sdst_portfolio_search_control_notes.md",
        "knowledge/papers/awls_sdst_zi_feature_notes.md",
        "knowledge/papers/awls_sdst_weight_update_notes.md",
        "knowledge/papers/awls_sdst_search_transition_notes.md",
        "knowledge/papers/awls_sdst_tabu_memory_notes.md",
    ],
    "zi": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
        "knowledge/papers/awls_sdst_zi_feature_notes.md",
        "knowledge/papers/awls_sdst_weight_update_notes.md",
    ],
    "adaptive_weight": [
        "knowledge/imported_huawei_fjsp_knowledge/lessons/profile_driven_local_search_20260615.md",
        "knowledge/papers/awls_sdst_zi_feature_notes.md",
        "knowledge/papers/awls_sdst_weight_update_notes.md",
    ],
    "move_scoring": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/xiejin_hgtsa_n8_k_insertion_tabu_spec.md",
        "knowledge/papers/awls_sdst_move_evaluation_notes.md",
        "knowledge/papers/awls_sdst_move_selection_notes.md",
    ],
    "critical_path": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/critical_path_machine_block_neighborhood.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/xiejin_hgtsa_n8_k_insertion_tabu_spec.md",
    ],
    "critical_block": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/critical_path_machine_block_neighborhood.md",
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
    ],
    "neighborhood": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/hgtsa_fjsp_n8_k_insertion_blueprint.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/xiejin_hgtsa_n8_k_insertion_tabu_spec.md",
    ],
    "machine_reassignment": [
        "knowledge/imported_huawei_fjsp_knowledge/operators/operation_machine_reassignment.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/hgtsa_fjsp_n8_k_insertion_blueprint.md",
    ],
    "setup_time": [
        "knowledge/papers/awls_sdst_initialization_notes.md",
        "knowledge/papers/awls_sdst_same_machine_notes.md",
        "knowledge/papers/awls_sdst_zi_feature_notes.md",
    ],
    "initialization": [
        "knowledge/papers/awls_sdst_initialization_notes.md",
    ],
    "same_machine": [
        "knowledge/papers/awls_sdst_same_machine_notes.md",
    ],
    "n7_neighborhood": [
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
        "knowledge/papers/awls_sdst_same_machine_notes.md",
    ],
    "nk_neighborhood": [
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
        "knowledge/papers/awls_sdst_move_evaluation_notes.md",
    ],
    "change_machine": [
        "knowledge/papers/awls_sdst_move_evaluation_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/operation_machine_reassignment.md",
    ],
    "candidate_generation": [
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
    ],
    "portfolio": [
        "knowledge/papers/awls_sdst_portfolio_search_control_notes.md",
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
    ],
    "search_control": [
        "knowledge/papers/awls_sdst_portfolio_search_control_notes.md",
        "knowledge/papers/awls_sdst_search_transition_notes.md",
        "knowledge/papers/awls_sdst_tabu_memory_notes.md",
        "knowledge/papers/awls_sdst_move_selection_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
    ],
    "move_selection": [
        "knowledge/papers/awls_sdst_move_selection_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
    ],
    "zi_features": [
        "knowledge/papers/awls_sdst_zi_feature_notes.md",
        "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
    ],
    "weight_update": [
        "knowledge/papers/awls_sdst_weight_update_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
    ],
    "tabu_search": [
        "knowledge/papers/awls_sdst_search_transition_notes.md",
        "knowledge/papers/awls_sdst_tabu_memory_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
    ],
    "search_transition": [
        "knowledge/papers/awls_sdst_search_transition_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
    ],
    "tabu_memory": [
        "knowledge/papers/awls_sdst_tabu_memory_notes.md",
        "knowledge/imported_huawei_fjsp_knowledge/operators/tabu_search_loop.md",
    ],
}


def auto_knowledge_cards(
    *,
    problem_family: str,
    problem_family_tags: list[str] | None = None,
    slot_manifest: dict[str, Any] | None = None,
) -> list[Path]:
    """Select local FJSP knowledge cards for the current family and confirmed slots."""

    selected: list[Path] = []
    normalized_family = str(problem_family or "").strip().lower()
    if normalized_family in {"fjsp", "standard_fjsp"}:
        selected.extend(_project_paths(FJSP_BASE_CARDS))

    tags = {str(tag).strip().lower() for tag in (problem_family_tags or []) if str(tag).strip()}
    if slot_manifest:
        for slot in slot_manifest.get("slots") or []:
            if not isinstance(slot, dict) or not slot.get("user_confirmed"):
                continue
            tags.update(str(tag).strip().lower() for tag in slot.get("knowledge_tags") or [] if str(tag).strip())

    for tag in sorted(tags):
        selected.extend(_project_paths(TAGGED_CARDS.get(tag, [])))
    return _existing_unique_paths(selected)


def _project_paths(paths: list[str]) -> list[Path]:
    return [(PROJECT_ROOT / path).resolve() for path in paths]


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
