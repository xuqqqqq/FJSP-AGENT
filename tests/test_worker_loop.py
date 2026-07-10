from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context_packet import ContextPacketRequest, write_context_packet
from harness_agent.loop_runner import current_round_repair_feedback, run_worker_loop
from harness_agent.models import TaskContract
from harness_agent.standard_worker_loop import worker_loop_agent_quality_summary
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


class DiffOnlyWorker:
    """Test worker that edits the worktree but does not report changed files."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="diff-only",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=False,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(text.replace("10 + args.seed", "9 + args.seed"), encoding="utf-8")
        return WorkerResult(
            status="ok",
            changed_files=[],
            summary="Edited the worktree but left changed_files empty.",
        )


class InfrastructureFailureWorker:
    """Test worker that fails before producing a proposal or code diff."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="infrastructure-failure",
            supports_code_generation=True,
            supports_repair=True,
            supports_structured_output=False,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        return WorkerResult(
            status="authorization_required",
            changed_files=[],
            summary="Authorization Required",
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


class AgentBaselineRepairWorker:
    """Worker that repairs an agent-generated baseline after quality-contract feedback."""

    def __init__(self) -> None:
        self.saw_quality_repair_targets = False

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="agent-baseline-repair",
            supports_code_generation=True,
            supports_repair=True,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        context = json.loads(Path(spec.context_packet_path).read_text(encoding="utf-8"))
        repair_feedback = (context.get("loop_feedback") or {}).get("current_round_repair")
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "agent_generated_fjsp_solver.py"
        proposal_path = output_dir / "proposal.json"
        if not repair_feedback:
            solver_path.write_text("def solve():\n    return []\n", encoding="utf-8")
            proposal_path.write_text(
                json.dumps(
                    {
                        "summary": "Create an intentionally weak generated solver.",
                        "strategy_intent": "Trigger the quality contract repair path.",
                        "rule_operator_hypotheses": [
                            {
                                "name": "weak_generated_baseline",
                                "type": "baseline_constructor",
                                "target_files": ["examples/agent_generated_fjsp_solver.py"],
                            }
                        ],
                        "changes": [
                            {
                                "path": "examples/agent_generated_fjsp_solver.py",
                                "action": "create_or_replace",
                                "content": solver_path.read_text(encoding="utf-8"),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return WorkerResult(
                status="ok",
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                summary="Weak baseline proposal should be rejected before evaluator execution.",
                artifacts={"proposal": str(proposal_path)},
            )

        targets = repair_feedback.get("repair_targets") or {}
        self.saw_quality_repair_targets = bool(
            targets.get("agent_generated_solver_quality_risks")
            and targets.get("agent_generated_solver_self_check_risks")
            and targets.get("agent_generated_solver_expected_contract")
        )
        solver_source = _standard_agent_generated_solver_source()
        solver_path.write_text(solver_source, encoding="utf-8")
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Repair the generated solver to satisfy the quality contract.",
                    "strategy_intent": "Use current_round_repair targets to add parser, stable op identity, decoder guards, and self-check.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "repair_contract_complete_constructor",
                            "type": "baseline_constructor_repair",
                            "target_files": ["examples/agent_generated_fjsp_solver.py"],
                            "evidence_used": ["loop_feedback.current_round_repair.repair_targets"],
                        }
                    ],
                    "solver_contract_self_check": _standard_agent_generated_self_check(),
                    "changes": [
                        {
                            "path": "examples/agent_generated_fjsp_solver.py",
                            "action": "create_or_replace",
                            "content": solver_source,
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="ok",
            changed_files=["examples/agent_generated_fjsp_solver.py"],
            summary="Repaired generated baseline using same-round quality targets.",
            artifacts={"proposal": str(proposal_path)},
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
    def test_worker_cycle_uses_actual_worktree_delta_when_worker_omits_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=DiffOnlyWorker(),
                experiment_id="test_diff_only_worker",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=True,
            )

            self.assertEqual(["examples/dummy_solver.py"], result.worker_result.changed_files)
            self.assertTrue(result.agentic_judgment.accepted, result.agentic_judgment.issues)
            delta = json.loads(result.delta_path.read_text(encoding="utf-8"))
            self.assertEqual(1, delta["counts"]["total_changed"])
            self.assertEqual(["examples/dummy_solver.py"], [item["path"] for item in delta["modified"]])

    def test_infrastructure_worker_failure_does_not_spend_repair_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=InfrastructureFailureWorker(),
                experiment_id="test_infra_failure_no_repair",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=True,
                in_round_repair_attempts=2,
            )

            self.assertEqual(1, len(result.rounds))
            round_record = result.rounds[0]
            self.assertEqual("authorization_required", round_record.worker_status)
            self.assertNotIn("in_round_repair", round_record.proposal_diagnostics)
            self.assertFalse((tmp_path / "loop" / "round_000" / "repair_001").exists())
            issues = round_record.candidate_summary["validation_summary"]["agentic_judgment"]["issues"]
            self.assertIn("worker_status_not_usable: authorization_required", issues)

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

    def test_current_round_repair_feedback_carries_quality_targets(self) -> None:
        feedback = current_round_repair_feedback(
            attempt_index=1,
            max_repair_attempts=2,
            previous_attempts=[
                {
                    "attempt_index": 0,
                    "agentic_judgment": {
                        "accepted": False,
                        "issues": ["agent_generated_solver_quality_contract_missing"],
                        "checks": {
                            "agent_generated_solver_quality_risks": [
                                "agent_generated_solver: missing base capabilities: stable_operation_identity"
                            ],
                            "agent_generated_solver_self_check_risks": [
                                "solver_contract_self_check missing implemented capabilities: stable_operation_identity"
                            ],
                            "agent_generated_solver_quality_contract": {
                                "enabled": True,
                                "active_features": ["alternative_machines", "sequence_dependent_setup"],
                                "required_code_capabilities": ["stable_operation_identity"],
                                "variant_required_code_capabilities": ["setup_aware_full_decoder_for_sequence_moves"],
                                "capability_playbook": [
                                    {
                                        "name": "stable_operation_identity",
                                        "evidence": "Cite operation-key representation.",
                                        "repair": "Normalize operation identity first.",
                                    }
                                ],
                            },
                        },
                    },
                }
            ],
        )

        targets = feedback["repair_targets"]
        self.assertIn("stable_operation_identity", targets["agent_generated_solver_quality_risks"][0])
        self.assertIn("stable_operation_identity", targets["agent_generated_solver_self_check_risks"][0])
        self.assertEqual(
            ["alternative_machines", "sequence_dependent_setup"],
            targets["agent_generated_solver_expected_contract"]["active_features"],
        )
        self.assertEqual(
            "stable_operation_identity",
            targets["agent_generated_solver_expected_contract"]["capability_playbook"][0]["name"],
        )
        self.assertIn("repair_targets", feedback["must_do"][-1])

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

    def test_agent_generated_baseline_repairs_quality_contract_before_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            worker = AgentBaselineRepairWorker()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=worker,
                baseline_source="agent_generated",
                experiment_id="test_agent_generated_quality_repair",
                iterations=0,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

            self.assertTrue(worker.saw_quality_repair_targets)
            self.assertEqual("agent_generated", result.baseline_source)
            self.assertIsNotNone(result.baseline_generation)
            generation = result.baseline_generation or {}
            self.assertEqual("ok", generation["status"])
            self.assertEqual("code_generation", generation["agentic_judgment"]["stage"])
            self.assertTrue(generation["agentic_judgment"]["accepted"], generation["agentic_judgment"]["issues"])
            self.assertEqual(1, generation["summary"]["valid"])
            self.assertEqual(2, generation["in_round_repair"]["attempt_count"])
            self.assertEqual(1, generation["in_round_repair"]["repair_attempt_count"])
            self.assertTrue(generation["in_round_repair"]["recovered"])
            first_attempt = generation["in_round_repair"]["attempts"][0]
            self.assertIn("agent_generated_solver_quality_contract_missing", first_attempt["failure_signatures"])
            self.assertIn("agent_generated_solver_self_check_incomplete", first_attempt["failure_signatures"])
            repair_context = json.loads(
                (tmp_path / "loop" / "agent_generated_baseline" / "repair_001" / "context_packet.json").read_text(
                    encoding="utf-8"
                )
            )
            repair_targets = repair_context["loop_feedback"]["current_round_repair"]["repair_targets"]
            self.assertIn("agent_generated_solver_expected_contract", repair_targets)
            self.assertIn("stable_operation_identity", json.dumps(repair_targets, ensure_ascii=False))
            self.assertLess(result.baseline_key[0], 0.0)
            quality_summary = worker_loop_agent_quality_summary(result)
            self.assertTrue(quality_summary["baseline"]["enabled"])
            self.assertTrue(quality_summary["baseline"]["ja_accepted"])
            self.assertEqual(1, quality_summary["baseline"]["repair_attempt_count"])
            self.assertTrue(quality_summary["baseline"]["repair_recovered"])
            self.assertEqual(0, quality_summary["round_count"])

    def test_agent_generated_baseline_memory_reaches_first_improvement_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=AgentBaselineRepairWorker(),
                baseline_source="agent_generated",
                experiment_id="test_agent_generated_baseline_memory",
                iterations=1,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

            self.assertEqual("agent_generated", result.baseline_source)
            round_context = json.loads(
                (tmp_path / "loop" / "round_000" / "context_packet.json").read_text(encoding="utf-8")
            )
            feedback = round_context["loop_feedback"]
            baseline_memory = feedback["agent_generated_baseline_memory"]
            self.assertTrue(baseline_memory["accepted_as_incumbent"])
            self.assertTrue(baseline_memory["repair_recovered"])
            self.assertEqual(1, baseline_memory["repair_attempt_count"])
            self.assertEqual("baseline_incumbent", baseline_memory["round_payload"]["decision"])
            self.assertEqual("validated_baseline", feedback["direction_graph"]["directions"][0]["status"])
            self.assertIn("agent_generated_quality_memory", feedback["experience_memory"])
            quality_memory = feedback["experience_memory"]["agent_generated_quality_memory"]
            self.assertEqual(1, quality_memory["rejected_attempt_count"])
            self.assertEqual(1, quality_memory["recovered_direction_count"])
            self.assertTrue(
                any(
                    (
                        fact.get("round_index") == -1
                        and "agent_generated" in str(fact.get("name") or "")
                    )
                    or fact.get("type") == "baseline_constructor_repair"
                    for fact in feedback["protected_promoted_facts"]
                )
            )
            self.assertTrue(
                any(
                    "Preserve the agent-generated baseline" in item
                    for item in feedback["next_round_guidance"]["must_do"]
                )
            )

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


def _write_standard_agent_generated_contract(tmp_path: Path) -> Path:
    contract = json.loads((ROOT / "configs" / "standard_fjsp_tiny.example.json").read_text(encoding="utf-8"))
    contract["task_id"] = "standard_agent_generated_quality_contract"
    contract["commands"] = {
        **contract["commands"],
        "solver": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
        "quick_test": "python -m py_compile examples/agent_generated_fjsp_solver.py examples/standard_fjsp_evaluator.py",
    }
    contract["budget"] = {
        **contract["budget"],
        "rounds": 1,
        "seeds": [0],
        "max_workers": 1,
    }
    output_path = tmp_path / "standard_agent_generated_contract.json"
    output_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _standard_agent_generated_self_check() -> dict[str, object]:
    capabilities = [
        "standalone_cli_interface",
        "active_io_parser",
        "declared_output_schema",
        "stable_operation_identity",
        "operation_level_ready_list_constructor",
        "complete_schedule_coverage_guard",
        "machine_eligibility_guard",
        "processing_duration_guard",
        "job_precedence_guard",
        "machine_non_overlap_guard",
        "bounded_runtime_or_iteration_guard",
        "incumbent_preservation_on_failed_candidate",
    ]
    return {
        "present": True,
        "active_features": [
            "alternative_machines",
            "machine_capacity",
            "makespan_objective",
            "operation_precedence",
        ],
        "capabilities": [
            {
                "name": name,
                "status": "implemented",
                "evidence": f"{name} is implemented in parse_instance/decode_schedule/solve/main.",
            }
            for name in capabilities
        ],
        "representation": "op_info uses (job_id, op_id); assignment and machine_sequences keep the same op_key.",
        "decoder": "decode_schedule rebuilds all operations and rejects missing, duplicate, ineligible, or mistimed records.",
        "runtime_bounds": "max_iterations bounds solve; decode rejects infeasible candidates without looping.",
        "incumbent_preservation": "best_schedule is kept unless candidate_makespan strictly improves best_makespan.",
        "variant_handling": [],
        "remaining_gaps": [],
    }


def _standard_agent_generated_solver_source() -> str:
    return "\n".join(
        [
            "from __future__ import annotations",
            "import argparse",
            "import json",
            "import random",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    numbers = [int(token) for token in Path(path).read_text(encoding='utf-8').split()]",
            "    idx = 0",
            "    job_count, machine_count, _max_candidates = numbers[idx:idx + 3]",
            "    idx += 3",
            "    raw_ops = []",
            "    machine_ids = []",
            "    for job_id in range(job_count):",
            "        op_count = numbers[idx]",
            "        idx += 1",
            "        for op_id in range(op_count):",
            "            candidate_count = numbers[idx]",
            "            idx += 1",
            "            candidates = []",
            "            for _ in range(candidate_count):",
            "                machine_id = numbers[idx]",
            "                duration = numbers[idx + 1]",
            "                idx += 2",
            "                machine_ids.append(machine_id)",
            "                candidates.append((machine_id, duration))",
            "            raw_ops.append((job_id, op_id, candidates))",
            "    machine_base = 0 if min(machine_ids) == 0 else 1",
            "    op_info = {}",
            "    for job_id, op_id, candidates in raw_ops:",
            "        eligible = {machine_id - machine_base: duration for machine_id, duration in candidates}",
            "        op_key = (job_id, op_id)",
            "        machine_id = min(eligible, key=lambda item: (eligible[item], item))",
            "        op_info[op_key] = {'eligible': eligible, 'processing_time': eligible[machine_id]}",
            "    return {",
            "        'name': Path(path).stem,",
            "        'op_info': op_info,",
            "        'machine_count': machine_count,",
            "        'total_ops': len(op_info),",
            "    }",
            "",
            "def construct_initial_solution(instance, seed=0, restart_count=2):",
            "    rng = random.Random(seed)",
            "    best_assignment = None",
            "    best_machine_sequences = None",
            "    best_makespan = None",
            "    job_ids = sorted({job_id for job_id, _op_id in instance['op_info']})",
            "    for _restart in range(max(1, restart_count)):",
            "        next_op_by_job = {job_id: 0 for job_id in job_ids}",
            "        job_ready = {job_id: 0 for job_id in job_ids}",
            "        machine_ready = {machine_id: 0 for machine_id in range(instance['machine_count'])}",
            "        assignment = {}",
            "        machine_sequences = {machine_id: [] for machine_id in range(instance['machine_count'])}",
            "        while len(assignment) < instance['total_ops']:",
            "            ready_ops = []",
            "            for job_id in job_ids:",
            "                op_id = next_op_by_job[job_id]",
            "                op_key = (job_id, op_id)",
            "                if op_key not in instance['op_info']:",
            "                    continue",
            "                eligible = instance['op_info'][op_key]['eligible']",
            "                for machine_id, duration in eligible.items():",
            "                    start = max(job_ready[job_id], machine_ready[machine_id])",
            "                    finish = start + duration",
            "                    ready_ops.append((finish, start, duration, rng.random(), op_key, machine_id))",
            "            if not ready_ops:",
            "                return None, None",
            "            best_choice = min(ready_ops)",
            "            finish, _start, _duration, _tie, op_key, machine_id = best_choice",
            "            assignment[op_key] = machine_id",
            "            machine_sequences[machine_id].append(op_key)",
            "            job_id, op_id = op_key",
            "            next_op_by_job[job_id] = op_id + 1",
            "            job_ready[job_id] = finish",
            "            machine_ready[machine_id] = finish",
            "        candidate = decode_schedule(dict(assignment), {m: list(v) for m, v in machine_sequences.items()}, instance['op_info'], instance['total_ops'])",
            "        if candidate is None:",
            "            continue",
            "        candidate_makespan = max(item['end'] for item in candidate)",
            "        if best_makespan is None or candidate_makespan < best_makespan:",
            "            best_assignment = assignment",
            "            best_machine_sequences = machine_sequences",
            "            best_makespan = candidate_makespan",
            "    return best_assignment, best_machine_sequences",
            "",
            "def decode_schedule(assignment, machine_sequences, op_info, total_ops):",
            "    expected_ops = set(op_info)",
            "    seen_ops = set()",
            "    schedule = []",
            "    job_ready = {}",
            "    machine_ready = {}",
            "    if set(assignment) != expected_ops:",
            "        return None",
            "    queues = {machine_id: list(sequence) for machine_id, sequence in machine_sequences.items()}",
            "    while len(schedule) < total_ops:",
            "        progressed = False",
            "        for machine_id in sorted(queues):",
            "            sequence = queues[machine_id]",
            "            if not sequence:",
            "                continue",
            "            job_id, op_id = sequence[0]",
            "            op_key = (job_id, op_id)",
            "            if op_key in seen_ops:",
            "                return None  # duplicate operation",
            "            if op_id > 0 and (job_id, op_id - 1) not in seen_ops:",
            "                continue",
            "            eligible = op_info[op_key]['eligible']",
            "            if machine_id not in eligible:",
            "                return None",
            "            duration = eligible[machine_id]",
            "            start = max(job_ready.get(job_id, 0), machine_ready.get(machine_id, 0))",
            "            end = start + duration",
            "            if end - start != duration:",
            "                return None",
            "            schedule.append({'job_id': job_id, 'op_id': op_id, 'machine_id': machine_id, 'start': start, 'end': end})",
            "            seen_ops.add(op_key)",
            "            sequence.pop(0)",
            "            job_ready[job_id] = end",
            "            machine_ready[machine_id] = end",
            "            progressed = True",
            "        if not progressed:",
            "            return None",
            "    if len(schedule) != total_ops or seen_ops != expected_ops:",
            "        return None",
            "    return schedule",
            "",
            "def solve(input_path, seed=0, max_iterations=1):",
            "    instance = parse_instance(input_path)",
            "    assignment, machine_sequences = construct_initial_solution(instance, seed=seed)",
            "    if assignment is None or machine_sequences is None:",
            "        raise ValueError('infeasible generated schedule')",
            "    best_schedule = decode_schedule(",
            "        dict(assignment),",
            "        {machine_id: list(sequence) for machine_id, sequence in machine_sequences.items()},",
            "        instance['op_info'],",
            "        instance['total_ops'],",
            "    )",
            "    if best_schedule is None:",
            "        raise ValueError('infeasible generated schedule')",
            "    best_makespan = max(item['end'] for item in best_schedule)",
            "    for _iteration in range(max_iterations):",
            "        candidate = decode_schedule(",
            "            dict(assignment),",
            "            {machine_id: list(sequence) for machine_id, sequence in machine_sequences.items()},",
            "            instance['op_info'],",
            "            instance['total_ops'],",
            "        )",
            "        if candidate is None:",
            "            continue",
            "        candidate_makespan = max(item['end'] for item in candidate)",
            "        if candidate_makespan < best_makespan:",
            "            best_schedule = candidate",
            "            best_makespan = candidate_makespan",
            "    return {",
            "        'format': 'standard_fjsp_schedule_v1',",
            "        'variant': 'standard_fjsp',",
            "        'instance': instance['name'],",
            "        'seed': seed,",
            "        'schedule': best_schedule,",
            "        'makespan': best_makespan,",
            "    }",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    solution = solve(args.input, seed=args.seed)",
            "    Path(args.output).write_text(json.dumps(solution), encoding='utf-8')",
            "",
            "if __name__ == '__main__':",
            "    raise SystemExit(main())",
        ]
    ) + "\n"


if __name__ == "__main__":
    unittest.main()
