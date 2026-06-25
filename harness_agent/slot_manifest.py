from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodeSlotSpec:
    slot_id: str
    title: str
    target_file: str
    marker_start: str
    marker_end: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    invariants: list[str]
    allowed_edits: list[str]
    forbidden_edits: list[str]
    validation_commands: list[str] = field(default_factory=list)
    knowledge_tags: list[str] = field(default_factory=list)
    user_confirmed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SlotManifest:
    schema_version: int
    problem_family: str
    status: str
    slots: list[CodeSlotSpec]
    confirmation_required: bool = True
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "problem_family": self.problem_family,
            "status": self.status,
            "confirmation_required": self.confirmation_required,
            "notes": self.notes,
            "slots": [slot.to_payload() for slot in self.slots],
        }


def default_standard_fjsp_slot_manifest(*, confirmed: bool = False) -> SlotManifest:
    slots = [
        CodeSlotSpec(
            slot_id="awls_zi_policy",
            title="AWLS adaptive zi weighting policy",
            target_file="examples/awls_evolved_slots.py",
            marker_start="# EVOLVE_START",
            marker_end="# EVOLVE_END",
            purpose="Control the numeric zi perturbation used by AWLS move scoring.",
            inputs=[
                "values['base']: base zi score from the fixed AWLS shell",
                "values['weight']: adaptive operation weight",
                "values['cooldown']: operation cooldown/timing signal",
                "values['rr'], values['gamma'], values['cooling']",
                "values['is_critical'], values['forward'], values['backward']",
                "values['duration'], values['machine_load'], values['position']",
            ],
            outputs=["A finite non-negative float. The wrapper clamps unsafe values."],
            invariants=[
                "Function name remains evolved_zi(values).",
                "No imports, subprocesses, file IO, randomness, network, or evaluator access.",
                "Must not change solver input/output schema.",
            ],
            allowed_edits=[
                "Rewrite the body of evolved_zi inside EVOLVE markers.",
                "Use arithmetic, local variables, values.get(...), if/else, and whitelisted numeric functions.",
            ],
            forbidden_edits=[
                "Changing parser/evaluator/benchmark files.",
                "Changing the solution JSON schema.",
                "Changing AWLS graph/state data structures.",
            ],
            validation_commands=[
                "python -m compileall examples/awls_evolved_slots.py examples/standard_fjsp_awls_solver.py",
                "python examples/standard_fjsp_awls_solver.py --input examples/fjsp.brandimarte.Mk01.m6j10c3.txt --output outputs/slot_smoke.json --zi-policy slot --time-limit-sec 1",
            ],
            knowledge_tags=["awls", "zi", "adaptive_weight", "move_scoring"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="local_search_neighborhood_actions",
            title="Local-search neighborhood action generation",
            target_file="examples/standard_fjsp_local_search_solver.py",
            marker_start="# SLOT neighborhood_actions START",
            marker_end="# SLOT neighborhood_actions END",
            purpose="Generate candidate moves for improving a decoded standard-FJSP schedule.",
            inputs=[
                "instance: fixed StandardFjspInstance",
                "state: current machine assignment and machine sequences",
                "decoded: current schedule, makespan, predecessors, successors, and topological order",
                "rng: seeded random source",
                "neighbor_limit: maximum candidate move budget",
            ],
            outputs=["A bounded list of existing Move objects compatible with apply_move/decode_state."],
            invariants=[
                "Do not change Move fields or SearchState/DecodedState schemas.",
                "All moves must remain checkable by decode_state and validate_standard_schedule.",
                "No evaluator, parser, or IO contract edits.",
            ],
            allowed_edits=[
                "Add or adjust move generators inside the marked slot.",
                "Use critical path/block, machine load, idle-gap, and candidate-machine signals already available in context.",
            ],
            forbidden_edits=[
                "Changing benchmark/evaluator semantics.",
                "Changing command-line arguments or solution output schema.",
                "Creating unbounded candidate lists or non-deterministic external side effects.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_local_search_solver.py",
                "python examples/standard_fjsp_local_search_solver.py --input examples/fjsp.brandimarte.Mk01.m6j10c3.txt --output outputs/neighborhood_slot_smoke.json --time-limit-sec 1",
            ],
            knowledge_tags=["critical_path", "critical_block", "neighborhood", "machine_reassignment"],
            user_confirmed=confirmed,
        ),
    ]
    return SlotManifest(
        schema_version=1,
        problem_family="standard_fjsp",
        status="confirmed" if confirmed else "draft_requires_user_confirmation",
        confirmation_required=not confirmed,
        notes=[
            "Slots are functional edit regions with explicit IO and invariants.",
            "User confirmation should happen before an LLM is allowed to edit a slot.",
            "Evaluator/parser/metric semantics remain fixed unless a new IO contract is confirmed.",
        ],
        slots=slots,
    )


def load_slot_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_default_slot_manifest(*, problem_family: str, output: Path, confirmed: bool = False) -> Path:
    normalized_family = str(problem_family).strip().lower()
    if normalized_family not in {"fjsp", "standard_fjsp"}:
        raise ValueError(f"no default slot manifest is available for problem family: {problem_family}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = default_standard_fjsp_slot_manifest(confirmed=confirmed).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
