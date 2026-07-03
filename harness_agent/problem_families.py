from __future__ import annotations

import json
from pathlib import Path

from .domain_pack import DomainCapability, get_domain_pack, load_domain_packs


ProblemFamilyCapability = DomainCapability


def default_problem_families() -> dict[str, ProblemFamilyCapability]:
    """Return problem-family capability cards from external domain packs."""

    packs = load_domain_packs()
    families: dict[str, ProblemFamilyCapability] = {}
    for key, pack in packs.items():
        families[key] = pack.capability
        families[pack.family_id] = pack.capability
    if not families:
        fallback = _fallback_standard_fjsp()
        families[fallback.family_id] = fallback
        families["fjsp"] = fallback
        families["FJSP"] = fallback
        families["standard_fjsp"] = fallback
    return families


def get_problem_family(family_id: str) -> ProblemFamilyCapability:
    pack = get_domain_pack(family_id)
    if pack is not None:
        return pack.capability
    return _fallback_standard_fjsp()


def write_problem_family_card(*, family_id: str, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = get_problem_family(family_id).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _fallback_standard_fjsp() -> ProblemFamilyCapability:
    return ProblemFamilyCapability(
        family_id="standard_fjsp",
        display_name="Standard Flexible Job-Shop Scheduling",
        description="Fallback capability used only when the external standard_fjsp domain pack is unavailable.",
        supported_variants=["standard_fjsp", "fjsp_sdst"],
        canonical_objectives=[{"name": "makespan", "direction": "minimize", "priority": 1}],
        io_contract_notes=[],
        evaluator_invariants=[],
        solver_entrypoints=[],
        specialization_hooks=[],
        knowledge_tags=[],
    )
