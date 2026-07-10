from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context_packet import ContextPacketRequest, write_context_packet
from harness_agent.loop_runner import run_worker_loop
from harness_agent.models import TaskContract
from harness_agent.worker import NullWorker, WorkerCapabilities, WorkerResult
from harness_agent.worker_cycle import render_worktree_patch, run_worker_cycle


ROOT = Path(__file__).resolve().parents[1]


class ImproveOnceWorker:
    """Test worker that improves the dummy solver once inside the candidate tree."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="improve-once",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        changed_files: list[str] = []
        if "10 + args.seed" in text:
            solver_path.write_text(text.replace("10 + args.seed", "8 + args.seed"), encoding="utf-8")
            changed_files = ["examples/dummy_solver.py"]
        return WorkerResult(
            status="ok",
            changed_files=changed_files,
            summary="Improve the dummy end time if the baseline expression is still present.",
        )


class UnstableImproveWorker:
    """Test worker whose first run improves but repeated runs regress."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="unstable-improve",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        replacement = (
            "8 if args.output.parent.name.startswith(\"round_000__\") else 20"
        )
        solver_path.write_text(text.replace("10 + args.seed", replacement), encoding="utf-8")
        return WorkerResult(
            status="ok",
            changed_files=["examples/dummy_solver.py"],
            summary="Improve only the first run so repeat promotion should reject the noisy candidate.",
        )


class RuntimeFailWorker:
    """Test worker that compiles but fails the one-seed evaluator smoke."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="runtime-fail",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(
            text.replace(
                "args = parser.parse_args()",
                "args = parser.parse_args()\n    raise RuntimeError('smoke should catch this before full evaluation')",
            ),
            encoding="utf-8",
        )
        return WorkerResult(
            status="ok",
            changed_files=["examples/dummy_solver.py"],
            summary="Introduce a runtime failure that py_compile cannot catch.",
        )


class PartialApplyRejectionWorker:
    """Test worker that changed a helper but failed to patch the intended entrypoint."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="partial-apply-rejection",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        helper_path = Path(spec.worktree_path) / "examples" / "new_helper.py"
        helper_path.write_text("VALUE = 1\n", encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Create a helper but fail to patch the solver entrypoint.",
                    "strategy_intent": "This simulates a text_replace old block mismatch.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "helper_plus_entrypoint_patch",
                            "type": "repair_rule",
                            "target_files": ["examples/dummy_solver.py", "examples/new_helper.py"],
                        }
                    ],
                    "changes": [
                        {
                            "path": "examples/new_helper.py",
                            "action": "create_or_replace",
                            "content": "VALUE = 1\n",
                        },
                        {
                            "path": "examples/dummy_solver.py",
                            "action": "text_replace",
                            "old": "missing anchor",
                            "new": "replacement",
                        },
                    ],
                    "apply_rejections": [
                        {"path": "examples/dummy_solver.py", "reason": "old text not found"}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/new_helper.py"],
            summary="Only the helper file changed; the entrypoint patch was rejected.",
            artifacts={"proposal": str(proposal_path)},
        )


class IncompleteLocalSearchWorker:
    """Test worker that adds a local-search decoder accepting incomplete schedules."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="incomplete-local-search",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(
            text
            + "\n\n"
            + "def risky_decode(machine_sequences):\n"
            + "    schedule = []\n"
            + "    while True:\n"
            + "        best_machine = None\n"
            + "        if best_machine is None:\n"
            + "            break\n"
            + "    return schedule\n"
            + "\n\n"
            + "def risky_local_search():\n"
            + "    new_schedule = risky_decode([])\n"
            + "    new_makespan = max(op['end'] for op in new_schedule) if new_schedule else 0\n"
            + "    return new_makespan\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="ok",
            changed_files=["examples/dummy_solver.py"],
            summary="Add a risky local search that treats empty schedules as improvements.",
        )


class ProposalAuditWorker:
    """Test worker that writes a structured proposal artifact without changing files."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="proposal-audit",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Try a solver-rule change based on the project intake.",
                    "strategy_intent": "Prefer solver-side changes and leave validators untouched.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "dummy_finish_shift",
                            "type": "dispatch_rule",
                            "novelty": "Changes the dummy solver expression rather than repeating a no-op proposal.",
                            "expected_effect": "Increase primary_score in the fixed dummy evaluator.",
                            "evidence_used": ["project_intake.summary.entry_files"],
                            "target_files": ["examples/dummy_solver.py"],
                            "ablation_plan": "Compare the candidate key against the baseline key.",
                        }
                    ],
                    "context_usage": {
                        "used_project_intake": True,
                        "referenced_files": ["examples/dummy_solver.py", "configs/task_contract.example.json"],
                        "notes": "Used intake to identify the dummy solver entry point.",
                    },
                    "proposal_audit": {
                        "project_intake_present": True,
                        "project_intake_status": "ok",
                        "declared_project_intake_used": True,
                        "detected_referenced_intake_files": ["examples/dummy_solver.py"],
                        "changed_core_algorithm_files": ["examples/dummy_solver.py"],
                        "changed_validator_files": [],
                        "changed_benchmark_files": [],
                        "referenced_test_commands": ["python -m compileall harness_agent examples"],
                        "operator_lineage": {
                            "hypothesis_count": 1,
                            "hypothesis_types": ["dispatch_rule"],
                            "hypothesis_target_files": ["examples/dummy_solver.py"],
                            "target_files_overlap_changes": [],
                        },
                        "slot_id": "dummy_slot",
                        "target_file": "examples/dummy_solver.py",
                        "accepted_change_count": 1,
                        "rejected_change_count": 0,
                        "accepted_change_paths": ["examples/dummy_solver.py"],
                        "failure_memory_status": "available",
                        "avoid_pattern_count": 2,
                        "rolled_back_round_count": 1,
                        "warnings": [],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="proposal_created",
            changed_files=[],
            summary="Proposal artifact was written for diagnostics.",
            artifacts={"proposal": str(proposal_path)},
        )


class MissingHypothesisEditWorker:
    """Worker that changes code but omits auditable rule/operator hypotheses."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="missing-hypothesis-edit",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(text.replace("10 + args.seed", "9 + args.seed"), encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Change the solver without explaining the operator.",
                    "strategy_intent": "This should be rejected in incremental iteration mode.",
                    "rule_operator_hypotheses": [],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
                    "context_usage": {"used_project_intake": False, "referenced_files": ["examples/dummy_solver.py"]},
                    "quick_test_plan": "python -m compileall examples/dummy_solver.py",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary="Changed code without rule/operator hypothesis.",
            artifacts={"proposal": str(proposal_path)},
        )


class InRoundRepairWorker:
    """Worker that repairs a JA-rejected first attempt when same-round feedback is present."""

    def __init__(self) -> None:
        self.calls = 0
        self.saw_repair_feedback = False

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="in-round-repair",
            supports_code_generation=True,
            supports_repair=True,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        self.calls += 1
        context = json.loads(Path(spec.context_packet_path).read_text(encoding="utf-8"))
        repair_feedback = (context.get("loop_feedback") or {}).get("current_round_repair")
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = output_dir / "proposal.json"
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"

        if not repair_feedback:
            helper_path = Path(spec.worktree_path) / "examples" / "new_helper.py"
            helper_path.write_text("VALUE = 1\n", encoding="utf-8")
            proposal_path.write_text(
                json.dumps(
                    {
                        "summary": "First attempt changes a helper but misses the entrypoint anchor.",
                        "strategy_intent": "Trigger same-round repair by producing apply_rejections.",
                        "rule_operator_hypotheses": [
                            {
                                "name": "bad_anchor_probe",
                                "type": "repair_rule",
                                "target_files": ["examples/dummy_solver.py"],
                            }
                        ],
                        "changes": [
                            {
                                "path": "examples/new_helper.py",
                                "action": "create_or_replace",
                                "content": "VALUE = 1\n",
                            },
                            {
                                "path": "examples/dummy_solver.py",
                                "action": "text_replace",
                                "old": "missing solver expression",
                                "new": "8 + args.seed",
                            },
                        ],
                        "apply_rejections": [
                            {"path": "examples/dummy_solver.py", "reason": "old text not found"}
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return WorkerResult(
                status="applied",
                changed_files=["examples/new_helper.py"],
                summary="Bad first attempt.",
                artifacts={"proposal": str(proposal_path)},
            )

        self.saw_repair_feedback = True
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(text.replace("10 + args.seed", "8 + args.seed"), encoding="utf-8")
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Repair the rejected anchor by editing the known solver expression directly.",
                    "strategy_intent": "Use current_round_repair feedback to avoid the failed helper-only change.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "repair_dummy_finish_shift",
                            "type": "repair_rule",
                            "novelty": "Repairs the rejected anchor by making the actual entrypoint edit.",
                            "expected_effect": "Improve the dummy objective while passing JA.",
                            "evidence_used": ["loop_feedback.current_round_repair"],
                            "target_files": ["examples/dummy_solver.py"],
                        }
                    ],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
                    "context_usage": {
                        "used_project_intake": False,
                        "referenced_files": ["examples/dummy_solver.py"],
                    },
                    "quick_test_plan": "python -m compileall examples/dummy_solver.py",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary="Repaired first attempt using same-round feedback.",
            artifacts={"proposal": str(proposal_path)},
        )


class SameDirectionRefinementWorker:
    """Worker that refines a legal no-improvement attempt inside one direction."""

    def __init__(self) -> None:
        self.calls = 0
        self.saw_refinement_feedback = False

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="same-direction-refinement",
            supports_code_generation=True,
            supports_repair=True,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        self.calls += 1
        context = json.loads(Path(spec.context_packet_path).read_text(encoding="utf-8"))
        repair_feedback = (context.get("loop_feedback") or {}).get("current_round_repair")
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = output_dir / "proposal.json"
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")

        if repair_feedback and repair_feedback.get("status") == "refinement_required":
            self.saw_refinement_feedback = True
            solver_path.write_text(text.replace("10 + args.seed", "8 + args.seed"), encoding="utf-8")
            summary = "Refine the same direction into a measured finish shift."
            hypothesis_name = "same_direction_refined_finish_shift"
            evidence = ["loop_feedback.current_round_repair"]
        else:
            solver_path.write_text(text + "\n# legal no-improvement probe\n", encoding="utf-8")
            summary = "Make a legal but non-improving probe in the same direction."
            hypothesis_name = "same_direction_comment_probe"
            evidence = ["knowledge_cards"]

        proposal_path.write_text(
            json.dumps(
                {
                    "summary": summary,
                    "strategy_intent": "Stay within one direction and refine if Core reports legal no improvement.",
                    "rule_operator_hypotheses": [
                        {
                            "name": hypothesis_name,
                            "type": "dispatch_rule",
                            "novelty": "Uses same-direction feedback instead of starting a new unrelated idea.",
                            "expected_effect": "Improve the dummy objective after a bounded refinement.",
                            "evidence_used": evidence,
                            "target_files": ["examples/dummy_solver.py"],
                        }
                    ],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
                    "context_usage": {"used_project_intake": False, "referenced_files": ["examples/dummy_solver.py"]},
                    "quick_test_plan": "python -m compileall examples/dummy_solver.py",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary=summary,
            artifacts={"proposal": str(proposal_path)},
        )


class PromotingProposalWorker:
    """Worker that improves the dummy solver and emits an auditable hypothesis."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="promoting-proposal",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        if "10 + args.seed" in text:
            solver_path.write_text(text.replace("10 + args.seed", "8 + args.seed"), encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Improve dummy finish expression.",
                    "strategy_intent": "Preserve the promoted finish shift in later rounds.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "dummy_finish_shift",
                            "type": "dispatch_rule",
                            "novelty": "Shifts the dummy solver finish time while preserving the output contract.",
                            "expected_effect": "Improve the fixed evaluator objective.",
                            "target_files": ["examples/dummy_solver.py"],
                        }
                    ],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
                    "context_usage": {"used_project_intake": False, "referenced_files": ["examples/dummy_solver.py"]},
                    "quick_test_plan": "python -m compileall harness_agent examples",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary="Improved dummy solver and emitted proposal diagnostics.",
            artifacts={"proposal": str(proposal_path)},
        )


class EmptySlotProposalWorker:
    """Test worker that emits an empty confirmed-slot proposal without risk notes."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="empty-slot-proposal",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "No slot edit was emitted.",
                    "strategy_intent": "Avoid unsafe changes, but without explaining why.",
                    "rule_operator_hypotheses": [],
                    "changes": [],
                    "rejected_changes": [],
                    "risk_notes": [],
                    "proposal_audit": {
                        "slot_id": "awls_sdst_move_evaluation",
                        "target_file": "examples/standard_fjsp_awls_solver.py",
                        "accepted_change_count": 0,
                        "rejected_change_count": 0,
                        "warnings": ["empty_slot_proposal_without_risk_note"],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="proposal_created",
            changed_files=[],
            summary="Empty slot proposal artifact was written.",
            artifacts={"proposal": str(proposal_path)},
        )


class BadStandardParserWorker:
    """Test worker that should be rejected by the code judgment gate."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="bad-standard-parser",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "standard_fjsp_portfolio_solver.py"
        solver_path.write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "",
                    "def parse_instance(path):",
                    "    return {'jobs': []}",
                    "",
                    "def main():",
                    "    parse_instance('dummy')",
                    "",
                    "if __name__ == '__main__':",
                    "    main()",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="ok",
            changed_files=["examples/standard_fjsp_portfolio_solver.py"],
            summary="Replace the standard solver with a custom parser.",
        )


class AgentBaselineWorker:
    """Test worker that writes the initial solver used as the measured baseline."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="agent-baseline",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "agent_generated_solver.py"
        solver_path.write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "import argparse",
                    "import json",
                    "from pathlib import Path",
                    "",
                    "def main() -> int:",
                    "    parser = argparse.ArgumentParser()",
                    "    parser.add_argument('--input', required=True, type=Path)",
                    "    parser.add_argument('--output', required=True, type=Path)",
                    "    parser.add_argument('--seed', type=int, default=0)",
                    "    args = parser.parse_args()",
                    "    instance = json.loads(args.input.read_text(encoding='utf-8'))",
                    "    solution = {",
                    "        'instance': instance['name'],",
                    "        'seed': args.seed,",
                    "        'schedule': [{'job_id': 'J1', 'operation_id': 'J1-O1', 'machine_id': 'M1', 'start': 0, 'end': 12 + args.seed}],",
                    "    }",
                    "    args.output.parent.mkdir(parents=True, exist_ok=True)",
                    "    args.output.write_text(json.dumps(solution), encoding='utf-8')",
                    "    return 0",
                    "",
                    "if __name__ == '__main__':",
                    "    raise SystemExit(main())",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="ok",
            changed_files=["examples/agent_generated_solver.py"],
            summary="Create the initial solver from the context packet.",
        )


class AgentGeneratedBackendImportWorker:
    """Worker that writes a helper import unsafe for standalone generated solvers."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="agent-generated-backend-import",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        helper_path = Path(spec.worktree_path) / "examples" / "agent_generated_helper.py"
        helper_path.write_text(
            "from harness_agent.standard_fjsp import setup_time_between\n\n"
            "def helper():\n"
            "    return setup_time_between\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="ok",
            changed_files=["examples/agent_generated_helper.py"],
            summary="Add helper that imports backend package.",
        )


class ProtectedFactRegressionWorker:
    """Worker that tries to remove a protected promoted mechanism."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="protected-fact-regression",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        solver_path.write_text(solver_path.read_text(encoding="utf-8") + "\n# remove normalization\n", encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Remove the normalization mechanism from the promoted solver.",
                    "strategy_intent": "Drop normalization and use raw machine ids unchanged.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "remove_normalization",
                            "type": "repair_rule",
                            "target_files": ["examples/dummy_solver.py"],
                            "novelty": "Remove normalization instead of preserving it.",
                        }
                    ],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
                    "quick_test_plan": "python -m compileall harness_agent examples",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary="Remove protected normalization.",
            artifacts={"proposal": str(proposal_path)},
        )


class ProtectedFactAblationPlanWorker:
    """Worker that preserves a protected mechanism but documents a future ablation."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="protected-fact-ablation-plan",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(text.replace("10 + args.seed", "9 + args.seed"), encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Add a bounded refinement while preserving the promoted insertion mechanism.",
                    "strategy_intent": "Keep insertion active and add one attributable refinement.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "bounded_refinement_after_insertion",
                            "type": "local_search_operator",
                            "target_files": ["examples/dummy_solver.py"],
                            "novelty": "Adds a second pass without changing insertion.",
                            "expected_effect": "Improve the fixed evaluator objective.",
                            "ablation_plan": "Remove the insertion call only in a later ablation run to isolate its effect.",
                        }
                    ],
                    "changes": [
                        {
                            "path": "examples/dummy_solver.py",
                            "action": "text_replace",
                            "old": "10 + args.seed",
                            "new": "9 + args.seed",
                            "rationale": "Add the bounded refinement while preserving insertion.",
                        }
                    ],
                    "quick_test_plan": "python -m compileall examples/dummy_solver.py",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary="Preserve insertion and document a later ablation.",
            artifacts={"proposal": str(proposal_path)},
        )


class SafeFeasibilityProtectedEditWorker:
    """Worker that edits a solver under an empty-schedule protected fact without reintroducing the risk."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="safe-feasibility-protected-edit",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(text.replace("10 + args.seed", "9 + args.seed"), encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Remove the pure greedy schedule tie while preserving complete solution output.",
                    "strategy_intent": "Safely tune the dispatch expression without touching feasibility guards.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "safe_dispatch_shift",
                            "type": "dispatch_rule",
                            "novelty": "Changes objective expression while preserving the promoted empty-schedule repair.",
                            "expected_effect": "Improve dummy score under fixed evaluator.",
                            "target_files": ["examples/dummy_solver.py"],
                        }
                    ],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
                    "context_usage": {"used_project_intake": False, "referenced_files": ["examples/dummy_solver.py"]},
                    "quick_test_plan": "python -m compileall examples/dummy_solver.py",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="applied",
            changed_files=["examples/dummy_solver.py"],
            summary="Safe edit under feasibility protected fact.",
            artifacts={"proposal": str(proposal_path)},
        )


class WorkerLoopTests(unittest.TestCase):
    def test_refreshed_context_records_previous_round_and_duplicate_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                experiment_id="test_null_loop",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertEqual((990.0, -0.01), result.baseline_key)
            self.assertEqual((990.0, -0.01), result.final_key)
            self.assertEqual(["rolled_back", "rolled_back"], [item.decision for item in result.rounds])
            self.assertEqual([False, True], [item.duplicate_proposal for item in result.rounds])
            self.assertEqual("missing", result.rounds[0].proposal_diagnostics["status"])

            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            self.assertEqual("worker_loop_round_feedback", round_001_context["refresh_reason"])
            self.assertEqual(1, round_001_context["loop_feedback"]["round_index"])
            self.assertEqual("rolled_back", round_001_context["loop_feedback"]["previous_rounds"][0]["decision"])
            self.assertEqual("incremental_after_baseline", round_001_context["iteration_edit_contract"]["mode"])
            self.assertIn("improvement round", round_001_context["hypothesis"])
            self.assertIn("examples/dummy_solver.py", round_001_context["incumbent_code_context"]["files"][0]["relative_path"])
            self.assertIn("def main", round_001_context["incumbent_code_context"]["files"][0]["snippet"])
            self.assertTrue(round_001_context["worker_instruction"]["round_feedback_rule"])
            self.assertTrue(round_001_context["worker_instruction"]["incremental_edit_rule"])

            round_001_delta = json.loads((tmp_path / "loop" / "round_001" / "worker_worktree_delta.json").read_text(encoding="utf-8"))
            self.assertEqual(0, round_001_delta["counts"]["total_changed"])

    def test_refreshed_context_carries_failure_memory_and_next_round_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=PartialApplyRejectionWorker(),
                experiment_id="test_failure_memory",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            feedback = round_001_context["loop_feedback"]
            self.assertEqual("available", feedback["failure_memory"]["status"])
            self.assertIn("proposal_apply_rejections", feedback["failure_memory"]["must_avoid"])
            self.assertIn("proposal_apply_rejections", feedback["next_round_guidance"]["avoid"])
            self.assertTrue(feedback["next_round_guidance"]["must_do"])

    def test_promoted_hypotheses_become_protected_facts_in_next_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=PromotingProposalWorker(),
                experiment_id="test_protected_promoted_facts",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertEqual("promoted", result.rounds[0].decision)
            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            protected = round_001_context["loop_feedback"]["protected_promoted_facts"]
            self.assertEqual("dummy_finish_shift", protected[0]["name"])
            self.assertIn("examples/dummy_solver.py", protected[0]["target_files"])

    def test_in_round_repair_rechecks_repaired_proposal_before_spending_next_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            worker = InRoundRepairWorker()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=worker,
                experiment_id="test_in_round_repair",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

            self.assertEqual(2, worker.calls)
            self.assertTrue(worker.saw_repair_feedback)
            self.assertEqual(["promoted"], [item.decision for item in result.rounds])
            self.assertEqual((992.0, -0.01), result.final_key)

            repair = result.rounds[0].proposal_diagnostics["in_round_repair"]
            self.assertEqual(2, repair["attempt_count"])
            self.assertEqual(1, repair["repair_attempt_count"])
            self.assertTrue(repair["recovered"])
            self.assertIn("proposal_apply_rejections", repair["attempts"][0]["failure_signatures"])
            rejected_edits = repair["attempts"][0]["proposal_diagnostics"]["rejected_edits"]
            self.assertEqual("missing solver expression", rejected_edits[0]["old"])

            repair_context = json.loads(
                (tmp_path / "loop" / "round_000" / "repair_001" / "context_packet.json").read_text(encoding="utf-8")
            )
            current_repair = repair_context["loop_feedback"]["current_round_repair"]
            self.assertEqual("repair_required", current_repair["status"])
            self.assertEqual(1, current_repair["attempt_index"])
            self.assertIn("proposal_apply_rejections", current_repair["avoid"])
            self.assertEqual(
                "missing solver expression",
                current_repair["previous_attempts"][0]["proposal_diagnostics"]["rejected_edits"][0]["old"],
            )
            self.assertIn("repair current_round_repair.previous_attempts", repair_context["loop_feedback"]["instructions"][0])

    def test_legal_no_improvement_refines_inside_same_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            worker = SameDirectionRefinementWorker()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=worker,
                experiment_id="test_same_direction_refinement",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

            self.assertEqual(2, worker.calls)
            self.assertTrue(worker.saw_refinement_feedback)
            self.assertEqual(["promoted"], [item.decision for item in result.rounds])
            repair = result.rounds[0].proposal_diagnostics["in_round_repair"]
            self.assertIn("legal_but_not_strictly_better", repair["attempts"][0]["failure_signatures"])

            repair_context = json.loads(
                (tmp_path / "loop" / "round_000" / "repair_001" / "context_packet.json").read_text(encoding="utf-8")
            )
            self.assertEqual("refinement_required", repair_context["loop_feedback"]["current_round_repair"]["status"])
            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual(1, loop_result["hypothesis_graph"]["direction_count"])
            self.assertEqual(2, loop_result["hypothesis_graph"]["attempt_count"])
            self.assertTrue(loop_result["experience_memory"]["memory_tiers"]["candidate_lessons"])

    def test_agent_generated_baseline_is_written_before_first_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_agent_baseline_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=AgentBaselineWorker(),
                baseline_source="agent_generated",
                experiment_id="test_agent_generated_baseline",
                iterations=0,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertEqual("agent_generated", result.baseline_source)
            self.assertEqual((988.0, -0.01), result.baseline_key)
            self.assertEqual(result.baseline_key, result.final_key)
            self.assertIsNotNone(result.baseline_generation)
            self.assertEqual("ok", result.baseline_generation["status"])
            self.assertIn("examples/agent_generated_solver.py", result.baseline_generation["worker_changed_files"])
            self.assertIn("examples/standard_fjsp_awls_solver.py", result.baseline_generation["hidden_incumbent_files"])
            source_project = Path(result.baseline_generation["source_project"])
            self.assertFalse((source_project / "examples" / "standard_fjsp_awls_solver.py").exists())
            self.assertFalse((source_project / "examples" / "standard_fjsp_portfolio_solver.py").exists())
            self.assertTrue((result.final_worktree / "examples" / "agent_generated_solver.py").exists())
            baseline_context = json.loads(
                (tmp_path / "loop" / "agent_generated_baseline" / "context_packet.json").read_text(encoding="utf-8")
            )
            self.assertEqual("agent_generated_baseline", baseline_context["refresh_reason"])
            self.assertIn("baseline_generation_rule", baseline_context["worker_instruction"])
            self.assertIn("examples/standard_fjsp_awls_solver.py", baseline_context["baseline_generation"]["hidden_incumbent_files"])

    def test_loop_promotes_only_strict_objective_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=ImproveOnceWorker(),
                experiment_id="test_improve_once",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertEqual((990.0, -0.01), result.baseline_key)
            self.assertEqual((992.0, -0.01), result.final_key)
            self.assertEqual(["promoted", "rolled_back"], [item.decision for item in result.rounds])
            self.assertEqual([False, False], [item.duplicate_proposal for item in result.rounds])

            round_000_delta = json.loads((tmp_path / "loop" / "round_000" / "worker_worktree_delta.json").read_text(encoding="utf-8"))
            self.assertEqual(1, round_000_delta["counts"]["modified"])
            self.assertEqual("examples/dummy_solver.py", round_000_delta["modified"][0]["path"])
            round_000_patch = (tmp_path / "loop" / "round_000" / "worker_changes.patch").read_text(encoding="utf-8")
            self.assertIn("examples/dummy_solver.py", round_000_patch)
            self.assertIn("8 + args.seed", round_000_patch)

    def test_repeat_promotion_check_rejects_noisy_single_run_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_single_seed_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=UnstableImproveWorker(),
                experiment_id="test_unstable_improve",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                promotion_repeats=2,
            )

            self.assertEqual((990.0, -0.01), result.baseline_key)
            self.assertEqual((990.0, -0.01), result.final_key)
            self.assertEqual(["rolled_back"], [item.decision for item in result.rounds])
            self.assertEqual((992.0, -0.01), result.rounds[0].candidate_key)
            self.assertEqual("failed", result.rounds[0].promotion_check["status"])
            self.assertFalse(result.rounds[0].promotion_check["promoted"])
            self.assertEqual([990.0, -0.01], result.rounds[0].promotion_check["incumbent_repeat_key"])
            self.assertEqual([986.0, -0.01], result.rounds[0].promotion_check["candidate_repeat_key"])

            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", loop_result["rounds"][0]["promotion_check"]["status"])

    def test_candidate_smoke_gate_blocks_full_evaluation_on_runtime_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=RuntimeFailWorker(),
                experiment_id="test_smoke_gate_runtime_failure",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertIsNotNone(result.smoke_summary)
            self.assertEqual(1, result.smoke_summary.total)
            self.assertEqual(0, result.smoke_summary.valid)
            self.assertFalse(result.full_evaluation_started)
            self.assertEqual(1, result.summary.total)
            self.assertTrue((tmp_path / "cycle" / "harness_smoke" / "report.md").exists())
            self.assertFalse((tmp_path / "cycle" / "harness" / "report.md").exists())
            cycle_result = json.loads((tmp_path / "cycle" / "cycle_result.json").read_text(encoding="utf-8"))
            self.assertTrue(cycle_result["smoke_gate"]["enabled"])
            self.assertFalse(cycle_result["smoke_gate"]["passed"])
            self.assertFalse(cycle_result["smoke_gate"]["full_evaluation_started"])

    def test_code_judgment_rejects_incomplete_local_search_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=IncompleteLocalSearchWorker(),
                experiment_id="test_incomplete_local_search",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("incomplete_solution_acceptance_risk", result.agentic_judgment.issues)
            self.assertEqual(0, result.summary.total)
            self.assertFalse(result.full_evaluation_started)
            checks = result.agentic_judgment.checks
            self.assertIn("empty_schedule_scored_as_zero", checks["incomplete_solution_acceptance_risks"][0])
            self.assertIn("decoder_can_return_partial_schedule", checks["incomplete_solution_acceptance_risks"][0])

    def test_code_judgment_rejects_partial_apply_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=PartialApplyRejectionWorker(),
                experiment_id="test_partial_apply_rejection",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=True,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("proposal_apply_rejections", result.agentic_judgment.issues)
            self.assertEqual(0, result.summary.total)
            self.assertFalse(result.full_evaluation_started)
            self.assertEqual(
                [{"path": "examples/dummy_solver.py", "reason": "old text not found"}],
                result.agentic_judgment.checks["apply_rejections"],
            )

    def test_render_worktree_patch_ignores_line_ending_noise(self) -> None:
        patch = render_worktree_patch(
            root=ROOT,
            before_snapshot={
                "examples/solver.py": {
                    "sha256": "before",
                    "size": 20,
                    "_text": "alpha\r\nvalue = 1\r\nomega\r\n",
                }
            },
            after_snapshot={
                "examples/solver.py": {
                    "sha256": "after",
                    "size": 17,
                    "_text": "alpha\nvalue = 2\nomega\n",
                }
            },
            delta={
                "added": [],
                "modified": [{"path": "examples/solver.py"}],
                "deleted": [],
            },
        )

        self.assertIn("-value = 1", patch)
        self.assertIn("+value = 2", patch)
        self.assertNotIn("-alpha", patch)
        self.assertNotIn("+alpha", patch)
        self.assertNotIn("-omega", patch)
        self.assertNotIn("+omega", patch)

    def test_proposal_diagnostics_feed_next_round_context_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=ProposalAuditWorker(),
                experiment_id="test_proposal_diagnostics",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            diagnostics = result.rounds[0].proposal_diagnostics
            self.assertEqual("ok", diagnostics["status"])
            self.assertEqual("dummy_finish_shift", diagnostics["rule_operator_hypotheses"][0]["name"])
            self.assertEqual(1, diagnostics["proposal_audit"]["operator_lineage"]["hypothesis_count"])
            self.assertEqual("dummy_slot", diagnostics["proposal_audit"]["slot_id"])
            self.assertEqual("available", diagnostics["proposal_audit"]["failure_memory_status"])
            self.assertEqual(2, diagnostics["proposal_audit"]["avoid_pattern_count"])
            self.assertTrue(diagnostics["context_usage"]["used_project_intake"])
            self.assertEqual(["examples/dummy_solver.py"], diagnostics["proposal_audit"]["changed_core_algorithm_files"])

            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            previous = round_001_context["loop_feedback"]["previous_rounds"][0]
            self.assertEqual("ok", previous["proposal_diagnostics"]["status"])
            self.assertEqual(
                "dummy_finish_shift",
                previous["proposal_diagnostics"]["rule_operator_hypotheses"][0]["name"],
            )
            self.assertEqual(
                1,
                previous["proposal_diagnostics"]["proposal_audit"]["operator_lineage"]["hypothesis_count"],
            )
            self.assertEqual("dummy_slot", previous["proposal_diagnostics"]["proposal_audit"]["slot_id"])
            self.assertEqual(
                "available",
                previous["proposal_diagnostics"]["proposal_audit"]["failure_memory_status"],
            )
            self.assertTrue(previous["proposal_diagnostics"]["context_usage"]["used_project_intake"])
            self.assertEqual(
                ["examples/dummy_solver.py"],
                previous["proposal_diagnostics"]["proposal_audit"]["changed_core_algorithm_files"],
            )

            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("ok", loop_result["rounds"][0]["proposal_diagnostics"]["status"])

    def test_code_judgment_rejects_standard_parser_reimplementation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=BadStandardParserWorker(),
                experiment_id="test_bad_parser",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("standard_fjsp_parser_reimplementation_detected", result.agentic_judgment.issues)
            self.assertEqual(0, result.summary.total)
            self.assertIsNotNone(result.agentic_error_analysis)
            self.assertTrue((tmp_path / "cycle" / "agentic_judgment.json").exists())
            self.assertTrue((tmp_path / "cycle" / "agentic_error_analysis.md").exists())

    def test_code_judgment_rejects_agent_generated_backend_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_agent_baseline_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=AgentGeneratedBackendImportWorker(),
                experiment_id="test_agent_generated_backend_import",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("agent_generated_solver_imports_backend_package", result.agentic_judgment.issues)
            self.assertFalse(result.full_evaluation_started)
            self.assertEqual(0, result.summary.total)
            risks = result.agentic_judgment.checks["agent_generated_runtime_import_risks"]
            self.assertIn("examples/agent_generated_helper.py", risks[0])

    def test_code_judgment_rejects_protected_promoted_fact_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["loop_feedback"] = {
                "protected_promoted_facts": [
                    {
                        "round_index": 1,
                        "name": "offset_machine_id_normalization",
                        "type": "repair_rule",
                        "target_files": ["examples/dummy_solver.py"],
                        "novelty": "Preserve normalization after Core promotion.",
                    }
                ]
            }
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=ProtectedFactRegressionWorker(),
                experiment_id="test_protected_fact_regression",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("protected_promoted_fact_regression", result.agentic_judgment.issues)
            regressions = result.agentic_judgment.checks["protected_promoted_fact_regressions"]
            self.assertIn("normalization", regressions[0])

    def test_protected_fact_guard_ignores_future_ablation_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["loop_feedback"] = {
                "protected_promoted_facts": [
                    {
                        "round_index": 1,
                        "name": "machine_sequence_insertion_local_search",
                        "type": "local_search_operator",
                        "target_files": ["examples/dummy_solver.py"],
                        "novelty": "Preserve the promoted insertion mechanism.",
                    }
                ]
            }
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=ProtectedFactAblationPlanWorker(),
                experiment_id="test_protected_fact_ablation_plan",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertNotIn("protected_promoted_fact_regression", result.agentic_judgment.issues)

    def test_feasibility_protected_fact_allows_safe_dispatch_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["loop_feedback"] = {
                "protected_promoted_facts": [
                    {
                        "round_index": 0,
                        "name": "repair_empty_schedule_acceptance_risk",
                        "type": "dispatch_rule",
                        "target_files": ["examples/dummy_solver.py"],
                        "novelty": (
                            "This repair directly removes the conditional so that an empty schedule cannot be "
                            "scored as zero."
                        ),
                        "expected_effect": "Eliminates incomplete_solution_acceptance_risk.",
                    }
                ]
            }
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=SafeFeasibilityProtectedEditWorker(),
                experiment_id="test_safe_feasibility_protected_edit",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertEqual([], result.agentic_judgment.checks["protected_promoted_fact_regressions"])

    def test_code_judgment_rejects_incremental_edit_without_rule_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=MissingHypothesisEditWorker(),
                experiment_id="test_missing_hypothesis",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("missing_rule_operator_hypotheses", result.agentic_judgment.issues)
            self.assertFalse(result.full_evaluation_started)

    def test_code_judgment_rejects_empty_slot_proposal_without_risk_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=EmptySlotProposalWorker(),
                experiment_id="test_empty_slot_proposal",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=True,
            )

            self.assertFalse(result.agentic_judgment.accepted)
            self.assertIn("no_changed_files_after_apply", result.agentic_judgment.issues)
            self.assertIn("empty_slot_proposal_without_risk_note", result.agentic_judgment.issues)
            self.assertEqual(0, result.summary.total)
            self.assertIsNotNone(result.agentic_error_analysis)


def _write_test_context(tmp_path: Path, *, contract_path: Path | None = None) -> Path:
    output_path = tmp_path / "context_packet.json"
    return write_context_packet(
        ContextPacketRequest(
            contract_path=contract_path or ROOT / "configs" / "task_contract.example.json",
            output_path=output_path,
            docs=[ROOT / "README.md"],
            hypothesis="Worker-loop regression test context.",
        )
    )


def _write_single_seed_contract(tmp_path: Path) -> Path:
    contract = json.loads((ROOT / "configs" / "task_contract.example.json").read_text(encoding="utf-8"))
    contract["task_id"] = "single_seed_dummy_contract"
    contract["budget"] = {
        **contract["budget"],
        "rounds": 1,
        "seeds": [0],
        "max_workers": 1,
    }
    output_path = tmp_path / "single_seed_contract.json"
    output_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _write_agent_baseline_contract(tmp_path: Path) -> Path:
    contract = json.loads((ROOT / "configs" / "task_contract.example.json").read_text(encoding="utf-8"))
    contract["task_id"] = "agent_generated_baseline_dummy_contract"
    contract["commands"] = {
        **contract["commands"],
        "solver": "python examples/agent_generated_solver.py --input {instance} --output {solution} --seed {seed}",
        "quick_test": "python -m py_compile examples/agent_generated_solver.py examples/dummy_evaluator.py",
    }
    output_path = tmp_path / "agent_baseline_contract.json"
    output_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


if __name__ == "__main__":
    unittest.main()
