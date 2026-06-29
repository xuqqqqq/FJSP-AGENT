from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProblemFamilyCapability:
    """Machine-readable capability card for one optimization problem family."""

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
        return asdict(self)


def default_problem_families() -> dict[str, ProblemFamilyCapability]:
    fjsp = ProblemFamilyCapability(
        family_id="standard_fjsp",
        display_name="Standard Flexible Job-Shop Scheduling",
        description=(
            "Standard FJSP instances with operation precedence, alternative "
            "machines per operation, non-overlap machine capacity, and makespan "
            "as the default objective."
        ),
        supported_variants=[
            "standard_fjsp",
            "fjsp_with_best_known_gap",
            "fjsp_dispatch_rule_baseline",
            "fjsp_awls_local_search",
            "fjsp_sdst",
        ],
        canonical_objectives=[
            {"name": "makespan", "direction": "minimize", "priority": 1},
            {"name": "gap_pct", "direction": "minimize", "priority": 2, "optional": True},
            {"name": "runtime_seconds", "direction": "minimize", "priority": 3, "optional": True},
        ],
        io_contract_notes=[
            "Instances use the common text format: job_count machine_count max_candidate_count followed by jobs.",
            "Solutions must use standard_fjsp_schedule_v1 JSON with one record per operation.",
            "Evaluator owns all legality checks: precedence, machine eligibility, duration, and machine overlap.",
            "FJSP-SDST instances require the dedicated setup-matrix IO contract; setup time is evaluated between consecutive operations on the same machine.",
        ],
        evaluator_invariants=[
            "Do not modify examples/standard_fjsp_evaluator.py when evolving solver code.",
            "Do not modify harness_agent/standard_fjsp.py parser or validator without a new user-confirmed IO contract.",
            "Candidate solvers may self-check, but promotion depends only on Core evaluator output.",
            "Do not use the AWLS standard-FJSP backend for FJSP-SDST unless setup-aware decoding and neighborhood evaluation are selected.",
        ],
        solver_entrypoints=[
            "examples/standard_fjsp_portfolio_solver.py",
            "examples/standard_fjsp_local_search_solver.py",
            "examples/standard_fjsp_awls_solver.py",
        ],
        specialization_hooks=[
            "strategy_profile_generation",
            "slot_manifest_guided_edits",
            "awls_zi_formula_or_slot",
            "neighborhood_operator_slots",
            "best_known_gap_feedback",
            "setup_aware_dispatch_or_insertion",
            "sdst_io_contract",
        ],
        knowledge_tags=[
            "fjsp",
            "dispatch_rules",
            "critical_path",
            "critical_block_neighborhood",
            "machine_reassignment",
            "tabu_search",
            "awls",
            "sdst",
            "sequence_dependent_setup",
        ],
    )
    return {fjsp.family_id: fjsp, "FJSP": fjsp, "standard_fjsp": fjsp, "fjsp_sdst": fjsp}


def get_problem_family(family_id: str) -> ProblemFamilyCapability:
    families = default_problem_families()
    key = str(family_id or "standard_fjsp")
    return families.get(key) or families.get(key.lower()) or families["standard_fjsp"]


def write_problem_family_card(*, family_id: str, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = get_problem_family(family_id).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
