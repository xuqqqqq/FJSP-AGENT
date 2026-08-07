from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.orchestration.loop import (
    CandidateIncumbent,
    LaneDevelopmentState,
    WorkerLoopResult,
    apply_user_direction_revision,
    agent_generated_baseline_is_accepted,
    agent_generated_baseline_memory_payload,
    candidate_incumbent_payload,
    competitive_direction_plans,
    compact_round_direction_plan,
    continue_current_direction_plan,
    continuing_direction_worker_lane,
    continuing_direction_worker_session,
    current_round_repair_feedback,
    direction_revision_base,
    evaluate_exact_solver_execution,
    evaluate_mechanism_activation,
    evaluate_lane_checkpoint,
    load_worker_loop_result,
    lane_development_state_for_incumbent,
    local_trial_candidate_eligible,
    round_attempt_payload,
    normalize_user_intervention,
    plan_agent_generated_baseline_direction,
    plan_direction_with_fallback,
    run_agent_generated_baseline,
    run_competing_worker_cycles,
    run_algorithm_semantic_review,
    run_worker_cycle_with_in_round_repairs,
    run_worker_loop,
    reusable_lane_development_state,
    select_agent_generated_baseline_cycle,
    should_attempt_in_round_repair,
    update_lane_development_states,
    worker_proposal_diagnostics,
    worker_session_telemetry,
)
from harness_agent.core.models import TaskContract
from harness_agent.orchestration.standard import (
    worker_loop_agent_quality_summary,
    worker_loop_semantic_review_summary,
)
from harness_agent.worker import NullWorker, WorkerCapabilities, WorkerResult
from harness_agent.agents.judgment import AgenticJudgment
from harness_agent.core.runner import RunSummary
from harness_agent.agents.semantic import AlgorithmSemanticReviewResult
from harness_agent.orchestration.cycle import (
    collect_worktree_snapshot,
    judgment_with_result_revalidation,
    prepare_candidate_worktree,
    render_worktree_patch,
    run_worker_cycle,
    should_soft_accept_agent_generated_quality_rejection,
    soften_agent_generated_quality_judgment,
)


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


class AgentGeneratedBareListOutputWorker:
    """Generated solver that compiles but writes a bare schedule list."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="agent-generated-bare-list-output",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "agent_generated_fjsp_solver.py"
        source = _standard_agent_generated_solver_source().replace(
            "Path(args.output).write_text(json.dumps(solution), encoding='utf-8')",
            "with Path(args.output).open('w', encoding='utf-8') as handle:\n        json.dump(solution['schedule'], handle)",
        )
        solver_path.write_text(source, encoding="utf-8")
        return WorkerResult(
            status="ok",
            changed_files=["examples/agent_generated_fjsp_solver.py"],
            summary="Write a generated solver that falsely claims the object schema but emits a bare list.",
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
        self.requested_sessions: list[str | None] = []
        self.requested_session_candidates: list[tuple[str, str | None]] = []

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="same-direction-refinement",
            supports_code_generation=True,
            supports_repair=True,
            supports_structured_output=True,
            supports_session_reuse=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        self.calls += 1
        self.requested_sessions.append(spec.session_id)
        self.requested_session_candidates.append(
            (str(spec.experiment_id).rsplit("__", 1)[-1].split("_round_", 1)[0], spec.session_id)
        )
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
            artifacts={"proposal": str(proposal_path), "session_id": "ses_refinement"},
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


class SemanticRepairWorker:
    """Improve a legal candidate, then repair one semantic finding in the same direction."""

    def __init__(self) -> None:
        self.calls = 0
        self.saw_semantic_repair = False

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="semantic-repair",
            supports_code_generation=True,
            supports_repair=True,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        self.calls += 1
        context = json.loads(Path(spec.context_packet_path).read_text(encoding="utf-8"))
        repair = (context.get("loop_feedback") or {}).get("current_round_repair") or {}
        semantic_target = (repair.get("repair_targets") or {}).get("algorithm_semantic_review") or {}
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        source = solver_path.read_text(encoding="utf-8")
        if semantic_target:
            self.saw_semantic_repair = bool(
                semantic_target.get("blocking_findings")
                and semantic_target["blocking_findings"][0].get("required_test")
            )
            source = source.replace("8 + args.seed", "7 + args.seed")
            name = "repair_inverse_tabu_attribute"
            evidence = ["loop_feedback.current_round_repair.repair_targets.algorithm_semantic_review"]
        else:
            source = source.replace("10 + args.seed", "8 + args.seed")
            name = "add_tabu_direction"
            evidence = ["knowledge_cards"]
        solver_path.write_text(source, encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Implement or repair one measured tabu direction.",
                    "strategy_intent": "Keep the same direction and consume semantic evidence before promotion.",
                    "rule_operator_hypotheses": [
                        {
                            "name": name,
                            "type": "local_search_operator",
                            "target_files": ["examples/dummy_solver.py"],
                            "evidence_used": evidence,
                        }
                    ],
                    "changes": [{"path": "examples/dummy_solver.py", "action": "text_replace"}],
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
            summary="Applied semantic repair candidate.",
            artifacts={"proposal": str(proposal_path)},
        )


class SequencedSemanticReviewer:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = list(statuses)
        self.requests = []

    def review(self, request) -> AlgorithmSemanticReviewResult:  # noqa: ANN001 - protocol-compatible test double.
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else "pass"
        if status == "repair_required":
            findings = [
                {
                    "finding_id": "reverse_move_memory",
                    "category": "reverse_move_memory",
                    "severity": "blocking",
                    "blocking": True,
                    "confidence": 0.95,
                    "source_path": "examples/dummy_solver.py",
                    "line_start": 1,
                    "line_end": 3,
                    "knowledge_path": "knowledge/tabu_contract.md",
                    "knowledge_quote": "Store the inverse move attribute.",
                    "explanation": "The accepted forward attribute is stored unchanged.",
                    "repair": "Store the inverse attribute and preserve global best.",
                    "required_test": "Accept one move and prove its inverse remains tabu until expiry.",
                }
            ]
            accepted = False
        else:
            findings = []
            accepted = True
        return AlgorithmSemanticReviewResult(
            status=status,
            accepted=accepted,
            summary="Semantic review test result.",
            findings=findings,
            reviewed_files=["examples/dummy_solver.py"],
            knowledge_paths=["knowledge/tabu_contract.md"],
            reviewer="sequenced_test_reviewer",
            artifacts={"review": str(request.output_dir / "review.json")},
        )


class UnavailableSemanticReviewer:
    def __init__(self) -> None:
        self.requests = []

    def review(self, request) -> AlgorithmSemanticReviewResult:  # noqa: ANN001 - protocol-compatible test double.
        self.requests.append(request)
        return AlgorithmSemanticReviewResult(
            status="unavailable",
            accepted=False,
            summary="Provider unavailable.",
            findings=[],
            reviewed_files=[],
            knowledge_paths=[],
            reviewer="unavailable_test_reviewer",
            artifacts={},
        )


class CoverageOnlySemanticReviewer:
    """Reviewer with no verified mismatch but incomplete observability coverage."""

    def __init__(self) -> None:
        self.requests = []

    def review(self, request) -> AlgorithmSemanticReviewResult:  # noqa: ANN001 - protocol-compatible test double.
        self.requests.append(request)
        return AlgorithmSemanticReviewResult(
            status="repair_required",
            accepted=False,
            summary="No verified semantic mismatch remains; runtime observability is partial.",
            findings=[],
            reviewed_files=["examples/dummy_solver.py"],
            knowledge_paths=["knowledge/runtime_observability.md"],
            reviewer="coverage_only_test_reviewer",
            artifacts={},
            component_coverage=[
                {
                    "component_id": "runtime_and_observability",
                    "status": "partial",
                    "missing_behaviors": ["Report feasible and improving move counts separately."],
                }
            ],
            coverage_complete=False,
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
            targets.get("agent_generated_solver_self_check_risks")
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
            "from harness_agent.domains.io import setup_time_between\n\n"
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


class AdditiveNeighborhoodMoveWorker:
    """Worker that describes a remove-and-reinsert move while only adding code."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="additive-neighborhood-move",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        solver_path.write_text(text + "\n# additive insertion-neighborhood helper placeholder\n", encoding="utf-8")
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": (
                        "Add a critical insertion operator: remove an operation from a trial sequence, "
                        "try eligible machines, and reinsert it while preserving promoted mechanisms."
                    ),
                    "strategy_intent": (
                        "This is an additive neighborhood description, not a deletion of the existing "
                        "critical operator or eligibility guard."
                    ),
                    "rule_operator_hypotheses": [
                        {
                            "name": "additive_critical_insertion_neighborhood",
                            "type": "local_search_operator",
                            "target_files": ["examples/dummy_solver.py"],
                            "novelty": "Add a new insertion neighborhood while preserving the incumbent operator.",
                            "expected_effect": "Improve the evaluator objective without removing protected facts.",
                        }
                    ],
                    "changes": [
                        {
                            "path": "examples/dummy_solver.py",
                            "action": "insert_before",
                            "rationale": "Additive helper only; no existing promoted mechanism is removed.",
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
            summary="Add additive insertion-neighborhood helper text.",
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
    def test_agent_generated_baseline_still_asks_main_once_in_fast_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = _write_test_context(tmp_path)
            expected = {
                "direction_id": "agent_generated_baseline",
                "method_family": "constructive_search",
            }
            planner = SimpleNamespace(plan_direction=MagicMock(return_value=expected))

            actual = plan_agent_generated_baseline_direction(
                planner=planner,
                context_packet_path=context_path,
                output_dir=tmp_path / "main",
            )

        self.assertEqual(expected, actual)
        planner.plan_direction.assert_called_once()
        request = planner.plan_direction.call_args.args[0]
        self.assertEqual(-1, request.round_index)
        self.assertEqual("agent_generated_baseline", request.loop_feedback["round_type"])

    def test_session_reuse_requires_command_observation_and_nonempty_stream(self) -> None:
        requested = "ses_direction_123"

        dropped = worker_session_telemetry(
            {
                "requested_session_id": requested,
                "resume_strategy": "restart_equivalent_context",
                "event_stream_bytes": "120",
            },
            requested_session_id=requested,
        )
        empty = worker_session_telemetry(
            {
                "requested_session_id": requested,
                "command_session_id": requested,
                "observed_session_id": requested,
                "event_stream_bytes": "0",
            },
            requested_session_id=requested,
        )
        resumed = worker_session_telemetry(
            {
                "requested_session_id": requested,
                "command_session_id": requested,
                "observed_session_id": requested,
                "event_stream_bytes": "2048",
            },
            requested_session_id=requested,
        )

        self.assertFalse(dropped["session_reused"])
        self.assertFalse(dropped["session_resume_commanded"])
        self.assertFalse(empty["session_reused"])
        self.assertTrue(empty["session_resume_observed"])
        self.assertTrue(resumed["session_reused"])

    def test_wrapper_planner_fallback_preserves_experiment_contract_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = _write_test_context(tmp_path)
            planner = SimpleNamespace(
                plan_direction=MagicMock(side_effect=RuntimeError("packet budget failure"))
            )
            prior_checks = [
                {
                    "id": "expanded",
                    "path": "diagnostics.telemetry.expanded",
                    "operator": "gt",
                    "expected": 0,
                }
            ]
            prior_variants = [
                {
                    "candidate_id": "wide",
                    "hypothesis": "Widen the beam.",
                    "next_mutation": {"change": "Increase beam diversity."},
                    "activation_checks": prior_checks,
                }
            ]
            plan = plan_direction_with_fallback(
                planner=planner,
                round_index=2,
                context_packet_path=context_path,
                loop_feedback={
                    "previous_rounds": [
                        {
                            "round_index": 1,
                            "decision": "rolled_back",
                            "direction_plan": {
                                "direction_id": "d001",
                                "method_family": "constructive_search",
                                "method_families": [
                                    {"id": "constructive_search", "role": "primary"}
                                ],
                                "knowledge_query": ["beam_search"],
                                "activation_checks": prior_checks,
                                "candidate_variants": prior_variants,
                            },
                            "round_reflection": {
                                "hypothesis_outcome": "inconclusive",
                                "next_action": {"action": "probe"},
                            },
                        }
                    ]
                },
                output_dir=tmp_path / "main",
            )

            stored = json.loads(Path(plan["artifact_path"]).read_text(encoding="utf-8"))

        self.assertEqual("expanded", plan["activation_checks"][0]["id"])
        self.assertEqual(
            "diagnostics.telemetry.expanded",
            plan["activation_checks"][0]["path"],
        )
        self.assertEqual("wide", plan["candidate_variants"][0]["candidate_id"])
        self.assertEqual(1, plan["activation_contract_version"])
        self.assertEqual("packet budget failure", plan["planner_fallback"]["reason"])
        self.assertEqual(plan["planner_fallback"], stored["planner_fallback"])

    def test_wrapper_planner_fallback_builds_family_tournament_but_marks_activation_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = plan_direction_with_fallback(
                planner=SimpleNamespace(
                    plan_direction=MagicMock(side_effect=RuntimeError("provider unavailable"))
                ),
                round_index=0,
                context_packet_path=_write_test_context(tmp_path),
                loop_feedback={"competition": {"max_competing_workers": 4}},
                output_dir=tmp_path / "main",
            )

        contract_status = plan["planning_contract_status"]
        self.assertEqual("degraded", contract_status["status"])
        self.assertEqual(2, contract_status["minimum_candidate_variants"])
        self.assertEqual(3, contract_status["actual_candidate_variants"])
        self.assertNotIn("minimum_candidate_variants_not_met", contract_status["issues"])
        self.assertIn("main_activation_checks_missing", contract_status["issues"])
        self.assertEqual(0, plan["activation_contract_version"])
        self.assertEqual("legacy_compatibility", contract_status["activation_mode"])
        self.assertEqual(3, len(plan["candidate_variants"]))

    def test_user_revision_preserves_unspecified_candidates_activation_and_family(self) -> None:
        original = {
            "direction_id": "d003",
            "title": "Scale active beam",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "knowledge_query": ["beam_search"],
            "candidate_variants": [{"candidate_id": "wide", "hypothesis": "Widen it."}],
            "activation_checks": [{"id": "expanded", "path": "solver.expanded", "operator": "gt", "expected": 0}],
            "change_scope": ["beam width only"],
        }
        intervention = normalize_user_intervention(
            "Focus on frontier diversity without changing the method family.",
            round_index=3,
        )
        revised = {
            "direction_id": "d003",
            "title": "Measure frontier diversity",
            "method_family": "coupled_local_search",
            "method_families": [{"id": "coupled_local_search", "role": "primary"}],
            "knowledge_query": [],
            "candidate_variants": [],
            "activation_checks": [],
            "change_scope": ["instrument frontier diversity"],
        }

        merged, audit = apply_user_direction_revision(
            original,
            revised,
            user_intervention=intervention,
        )

        self.assertEqual("Measure frontier diversity", merged["title"])
        self.assertEqual("constructive_search", merged["method_family"])
        self.assertEqual(original["method_families"], merged["method_families"])
        self.assertEqual(["beam_search"], merged["knowledge_query"])
        self.assertEqual(original["candidate_variants"], merged["candidate_variants"])
        self.assertEqual(original["activation_checks"], merged["activation_checks"])
        self.assertIn("method_family", {item["field"] for item in audit["rejected_operations"]})

    def test_rejected_direction_change_revises_from_active_family(self) -> None:
        previous = {
            "direction_id": "d002",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "candidate_variants": [{"candidate_id": "pressure-regret"}],
        }
        proposed = {
            "direction_id": "d003",
            "method_family": "coupled_local_search",
            "method_families": [{"id": "coupled_local_search", "role": "primary"}],
            "candidate_variants": [{"candidate_id": "critical-block"}],
        }
        intervention = normalize_user_intervention(
            {
                "source": "direction_change_timeout_default_continue",
                "direction_patch": {
                    "action": "revise",
                    "instructions": "Continue the active family.",
                },
            },
            round_index=3,
        )

        base = direction_revision_base(
            proposed_direction_plan=proposed,
            previous_direction_plan=previous,
            user_intervention=intervention,
        )

        self.assertEqual("d003", base["direction_id"])
        self.assertEqual("d002", base["parent_direction_id"])
        self.assertEqual("constructive_search", base["method_family"])
        self.assertEqual(previous["candidate_variants"], base["candidate_variants"])

    def test_continue_current_direction_preserves_family_and_candidates(self) -> None:
        previous = {
            "direction_id": "d002",
            "title": "Pressure-regret refinement",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "knowledge_query": ["initialization", "decoder"],
            "candidate_variants": [{"candidate_id": "pressure-regret"}],
            "competition_result": {"selected_candidate_id": "pressure-regret"},
        }
        proposed = {
            "direction_id": "d003-proposed",
            "method_family": "coupled_local_search",
            "candidate_variants": [{"candidate_id": "critical-block"}],
        }

        continued = continue_current_direction_plan(
            previous_direction_plan=previous,
            proposed_direction_plan=proposed,
            round_index=3,
        )

        self.assertEqual("d003", continued["direction_id"])
        self.assertEqual("d002", continued["parent_direction_id"])
        self.assertEqual("constructive_search", continued["method_family"])
        self.assertEqual(previous["method_families"], continued["method_families"])
        self.assertEqual(previous["candidate_variants"], continued["candidate_variants"])
        self.assertNotIn("competition_result", continued)
        self.assertEqual(
            "coupled_local_search",
            continued["continuation"]["skipped_proposed_method_family"],
        )

    def test_continue_current_direction_preserves_research_tournament_stage(self) -> None:
        previous = {
            "direction_id": "d000",
            "experiment_stage": "research_tournament",
            "candidate_variants": [
                {
                    "candidate_id": "constructive",
                    "method_family": "constructive_search",
                    "method_package_id": "constructive-package",
                },
                {
                    "candidate_id": "exact",
                    "method_family": "exact_hybrid",
                    "method_package_id": "exact-package",
                },
            ],
        }

        continued = continue_current_direction_plan(
            previous_direction_plan=previous,
            proposed_direction_plan={"direction_id": "d001-proposed"},
            round_index=1,
        )

        self.assertEqual("research_tournament", continued["experiment_stage"])
        self.assertEqual(previous["candidate_variants"], continued["candidate_variants"])

    def test_user_pivot_applies_only_declared_fields_and_rejects_protected_clear(self) -> None:
        original = {
            "direction_id": "d004",
            "title": "Constructive probe",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "knowledge_query": ["beam_search"],
            "candidate_variants": [{"candidate_id": "beam"}],
            "activation_checks": [{"id": "expanded", "path": "expanded", "operator": "gt"}],
        }
        intervention = normalize_user_intervention(
            {
                "direction": "Pivot to a coupled neighborhood.",
                "direction_patch": {
                    "action": "pivot",
                    "set_fields": ["method_family", "method_families", "knowledge_query", "title"],
                    "clear_fields": ["candidate_variants"],
                },
            },
            round_index=4,
        )
        revised = {
            "direction_id": "d004-rewritten",
            "title": "Coupled neighborhood probe",
            "method_family": "coupled_local_search",
            "method_families": [{"id": "coupled_local_search", "role": "primary"}],
            "knowledge_query": ["critical_path"],
            "candidate_variants": [],
            "activation_checks": [],
        }

        merged, audit = apply_user_direction_revision(
            original,
            revised,
            user_intervention=intervention,
        )

        self.assertEqual("d004", merged["direction_id"])
        self.assertEqual("coupled_local_search", merged["method_family"])
        self.assertEqual(["critical_path"], merged["knowledge_query"])
        self.assertEqual([], merged["candidate_variants"])
        self.assertEqual([], merged["activation_checks"])
        self.assertIn("candidate_variants", {item["field"] for item in audit["rejected_operations"]})

    def test_user_revision_planner_fallback_cannot_replace_the_reviewed_plan(self) -> None:
        original = {
            "direction_id": "d005",
            "title": "Reviewed beam refinement",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "knowledge_query": ["beam_search"],
            "candidate_variants": [{"candidate_id": "wide"}],
            "activation_checks": [{"id": "expanded", "path": "expanded", "operator": "gt"}],
        }
        fallback = {
            "direction_id": "d005",
            "title": "Generic evidence fallback",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "knowledge_query": ["initialization"],
            "candidate_variants": [],
            "activation_checks": [],
            "planner": "evidence_fallback",
        }

        merged, audit = apply_user_direction_revision(
            original,
            fallback,
            user_intervention=normalize_user_intervention("Keep the measured beam evidence.", round_index=5),
        )

        self.assertEqual(original["title"], merged["title"])
        self.assertEqual(original["candidate_variants"], merged["candidate_variants"])
        self.assertEqual(original["activation_checks"], merged["activation_checks"])
        self.assertEqual("preserved_original_due_planner_fallback", audit["status"])

    def test_resume_state_skips_baseline_generation_and_keeps_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            incumbent = tmp_path / "existing_incumbent"
            incumbent.mkdir()
            baseline_summary = RunSummary(
                total=1,
                valid=1,
                failed=0,
                best_experiment_id="baseline",
                best_metrics={"makespan": 120.0},
            )
            resume_state = WorkerLoopResult(
                baseline_key=(-120.0,),
                final_key=(-110.0,),
                final_worktree=incumbent,
                rounds=[],
                baseline_summary=baseline_summary,
                baseline_source="agent_generated",
                baseline_generation={"status": "ok", "source": "agent_generated"},
            )
            contract = TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json")

            with patch("harness_agent.orchestration.loop.run_agent_generated_baseline") as generate_baseline:
                result = run_worker_loop(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "worker_loop",
                    context_packet_path=tmp_path / "context.json",
                    worker=NullWorker(),
                    experiment_id="resume-test",
                    iterations=0,
                    max_steps=1,
                    max_runtime_seconds=1,
                    apply_worker_changes=False,
                    baseline_source="agent_generated",
                    resume_from=resume_state,
                )

        generate_baseline.assert_not_called()
        self.assertEqual((-120.0,), result.baseline_key)
        self.assertEqual((-110.0,), result.final_key)
        self.assertEqual(incumbent.resolve(), result.final_worktree)
        self.assertIsNotNone(result.best_legal_incumbent)
        self.assertEqual((-110.0,), result.best_legal_incumbent.objective_key)
        self.assertEqual("resumed_promoted_incumbent", result.best_legal_incumbent.candidate_id)

    def test_resume_ignores_malformed_optional_candidate_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            incumbent = tmp_path / "incumbent"
            incumbent.mkdir()
            result_path = tmp_path / "loop_result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "baseline_key": [-120.0],
                        "final_key": [-110.0],
                        "final_worktree": str(incumbent),
                        "rounds": [],
                        "baseline_summary": {
                            "total": 1,
                            "valid": 1,
                            "failed": 0,
                            "best_metrics": {"makespan": 120.0},
                        },
                        "best_legal_incumbent": {
                            "objective_key": ["not-a-float"],
                            "worktree": str(incumbent),
                            "round_index": "not-an-int",
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = load_worker_loop_result(result_path)

        self.assertIsNotNone(result.best_legal_incumbent)
        self.assertEqual((-110.0,), result.best_legal_incumbent.objective_key)
        self.assertEqual("legacy_promoted_incumbent", result.best_legal_incumbent.candidate_id)

    def test_candidate_incumbent_payload_bounds_nested_evaluator_matrices(self) -> None:
        incumbent = CandidateIncumbent(
            objective_key=(-100.0,),
            worktree=ROOT,
            candidate_id="measured",
            round_index=2,
            summary={
                "total": 100,
                "valid": 100,
                "failed": 0,
                "best_metrics": {"makespan": 100.0},
                "candidate_summaries": [
                    {"candidate_id": f"c{index}", "trace": "x" * 2_000}
                    for index in range(100)
                ],
                "pareto_frontier": [{"candidate_id": f"c{index}"} for index in range(100)],
                "validation_summary": {"valid": 100},
            },
            activation_status="passed",
        )

        payload = candidate_incumbent_payload(incumbent)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertNotIn("candidate_summaries", payload["summary"])
        self.assertNotIn("pareto_frontier", payload["summary"])
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), 13_000)

    def test_ja_acceptance_is_bound_to_validated_candidate_output(self) -> None:
        initial = AgenticJudgment(
            accepted=True,
            right=True,
            stage="code_generation",
            issues=[],
            suggestions=[],
            checks={},
        )
        invalid = RunSummary(
            total=1,
            valid=0,
            failed=1,
            best_experiment_id=None,
            best_metrics={},
            validation_summary={"top_errors": [{"error": "schedule record 0 is malformed"}]},
        )

        judgment = judgment_with_result_revalidation(judgment=initial, smoke_summary=invalid)

        self.assertTrue(judgment.accepted)
        self.assertEqual([], judgment.issues)
        self.assertFalse(judgment.checks["result_revalidation"]["passed"])
        self.assertEqual(
            ["schedule record 0 is malformed"],
            judgment.checks["result_revalidation"]["top_errors"],
        )

    def test_ja_accepts_reproduced_valid_candidate_output(self) -> None:
        initial = AgenticJudgment(
            accepted=True,
            right=True,
            stage="code_generation",
            issues=[],
            suggestions=[],
            checks={},
        )
        valid = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="smoke",
            best_metrics={"makespan": 42},
        )

        judgment = judgment_with_result_revalidation(judgment=initial, smoke_summary=valid)

        self.assertTrue(judgment.accepted)
        self.assertEqual("code_generation", judgment.stage)
        self.assertTrue(judgment.checks["result_revalidation"]["passed"])

    def test_competing_workers_execute_candidates_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json")
            gate = threading.Barrier(3, timeout=2.0)
            worker_threads: set[int] = set()
            thread_lock = threading.Lock()

            def fake_cycle(**kwargs):  # noqa: ANN003 - mirrors the orchestration call surface.
                candidate_id = kwargs["direction_plan"]["candidate_variant"]["candidate_id"]
                with thread_lock:
                    worker_threads.add(threading.get_ident())
                gate.wait()
                summary = RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id=candidate_id,
                    best_metrics={"makespan": 100},
                )
                cycle = SimpleNamespace(
                    summary=summary,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="accepted",
                        issues=[],
                        suggestions=[],
                        checks={},
                    ),
                    worker_result=SimpleNamespace(
                        status="ok",
                        changed_files=["examples/solver.py"],
                    ),
                    worktree_path=tmp_path / candidate_id,
                    patch_path=tmp_path / f"{candidate_id}.patch",
                )
                return cycle, tmp_path / f"{candidate_id}.json", [{"semantic_review": {"status": "pass"}}]

            direction_plan = {
                "direction_id": "parallel",
                "candidate_variants": [
                    {"candidate_id": f"c{index}", "next_mutation": {"change": str(index)}}
                    for index in range(3)
                ],
            }
            with patch(
                "harness_agent.orchestration.loop.run_worker_cycle_with_in_round_repairs",
                side_effect=fake_cycle,
            ):
                _cycle, _context, _attempts, result, _selected = run_competing_worker_cycles(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "round_000",
                    base_context_packet_path=tmp_path / "context.json",
                    round_index=0,
                    worker=NullWorker(),
                    experiment_id="parallel-competition-test",
                    max_steps=1,
                    max_runtime_seconds=10,
                    apply_worker_changes=False,
                    baseline_summary=RunSummary(0, 0, 0, None, {}),
                    incumbent_key=(-120.0,),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=0,
                    direction_plan=direction_plan,
                    semantic_reviewer=None,
                    assignment_issuer=SimpleNamespace(),
                    worker_input_root=ROOT,
                    user_intervention=None,
                    max_competing_workers=3,
                )

            self.assertEqual(3, len(worker_threads))
            self.assertEqual("parallel", result["execution_mode"])
            self.assertEqual(3, result["max_concurrency"])
            self.assertEqual(["c0", "c1", "c2"], [item["candidate_id"] for item in result["candidates"]])

    def test_mechanism_activation_uses_nested_evaluator_telemetry(self) -> None:
        summary = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="candidate",
            best_metrics={
                "makespan": 100,
                "solver_evidence": {"diagnostics": {"expanded_states": 12, "modes": ["beam"]}},
            },
        )
        activation = evaluate_mechanism_activation(
            {
                "activation_checks": [
                    {
                        "id": "expanded",
                        "path": "best_metrics.solver_evidence.diagnostics.expanded_states",
                        "operator": "gte",
                        "expected": 10,
                    },
                    {
                        "id": "beam_mode",
                        "path": "best_metrics.solver_evidence.diagnostics.modes",
                        "operator": "contains",
                        "expected": "beam",
                    },
                ]
            },
            summary,
        )

        self.assertTrue(activation["passed"])
        self.assertEqual("passed", activation["status"])
        self.assertTrue(all(item["passed"] for item in activation["checks"]))

    def test_mechanism_activation_resolves_declared_diagnostics_root_forms(self) -> None:
        summary = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="candidate",
            best_metrics={
                "makespan": 100,
                "solver_evidence": {
                    "diagnostics": {
                        "telemetry": {
                            "critical_gap_rcl_run_status": "ok",
                            "critical_gap_rcl_draws": 387,
                            "critical_gap_rcl_rank_span": 4,
                        }
                    }
                },
            },
        )
        activation = evaluate_mechanism_activation(
            {
                "activation_checks": [
                    {
                        "id": "diagnostics_prefixed",
                        "path": "diagnostics.telemetry.critical_gap_rcl_run_status",
                        "operator": "eq",
                        "expected": "ok",
                    },
                    {
                        "id": "root_relative",
                        "path": "telemetry.critical_gap_rcl_draws",
                        "operator": "gte",
                        "expected": 1,
                    },
                    {
                        "id": "absolute",
                        "path": (
                            "best_metrics.solver_evidence.diagnostics.telemetry."
                            "critical_gap_rcl_rank_span"
                        ),
                        "operator": "gte",
                        "expected": 2,
                    },
                ]
            },
            summary,
        )

        self.assertTrue(activation["passed"])
        self.assertEqual(
            [
                "best_metrics.solver_evidence.diagnostics.telemetry.critical_gap_rcl_run_status",
                "best_metrics.solver_evidence.diagnostics.telemetry.critical_gap_rcl_draws",
                "best_metrics.solver_evidence.diagnostics.telemetry.critical_gap_rcl_rank_span",
            ],
            [item["resolved_path"] for item in activation["checks"]],
        )

    def test_mechanism_activation_aggregates_across_seed_evidence(self) -> None:
        summary = RunSummary(
            total=2,
            valid=2,
            failed=0,
            best_experiment_id="seed_1",
            best_metrics={
                "solver_evidence": {
                    "diagnostics": {"telemetry": {"critical_gap_trigger_steps": 0}}
                }
            },
            activation_evidence=[
                {
                    "experiment_id": "seed_1",
                    "seed": 1,
                    "best_metrics": {
                        "solver_evidence": {
                            "diagnostics": {"telemetry": {"critical_gap_trigger_steps": 0}}
                        }
                    },
                },
                {
                    "experiment_id": "seed_0",
                    "seed": 0,
                    "best_metrics": {
                        "solver_evidence": {
                            "diagnostics": {"telemetry": {"critical_gap_trigger_steps": 1}}
                        }
                    },
                },
            ],
        )
        activation = evaluate_mechanism_activation(
            {
                "activation_checks": [
                    {
                        "id": "triggered_any_seed",
                        "path": "diagnostics.telemetry.critical_gap_trigger_steps",
                        "operator": "gt",
                        "expected": 0,
                    },
                    {
                        "id": "triggered_all_seeds",
                        "path": "diagnostics.telemetry.critical_gap_trigger_steps",
                        "operator": "gt",
                        "expected": 0,
                        "aggregation": "all",
                        "required": False,
                    },
                ]
            },
            summary,
        )

        self.assertTrue(activation["passed"])
        any_seed, all_seeds = activation["checks"]
        self.assertTrue(any_seed["passed"])
        self.assertEqual(1, any_seed["passed_run_count"])
        self.assertEqual(2, any_seed["evaluated_run_count"])
        self.assertFalse(all_seeds["passed"])

    def test_mechanism_activation_without_declared_checks_is_unknown(self) -> None:
        activation = evaluate_mechanism_activation(
            {},
            RunSummary(
                total=1,
                valid=1,
                failed=0,
                best_experiment_id="candidate",
                best_metrics={"makespan": 100},
            ),
        )

        self.assertEqual("not_declared", activation["status"])
        self.assertIsNone(activation["passed"])
        self.assertEqual(0, activation["declared_check_count"])
        self.assertEqual(0, activation["required_failure_count"])

    def test_only_research_tournament_variants_may_change_method_family(self) -> None:
        base = {
            "direction_id": "d000",
            "experiment_stage": "probe",
            "method_family": "beam",
            "method_package_id": "beam-package",
            "candidate_variants": [
                {
                    "candidate_id": "tabu",
                    "hypothesis": "Compare tabu.",
                    "next_mutation": {"change": "Implement tabu comparison."},
                    "method_family": "tabu",
                    "method_package_id": "tabu-package",
                    "experiment_stage": "research_tournament",
                    "activation_checks": [{"path": "best_metrics.tabu_moves", "operator": "gt", "expected": 0}],
                }
            ],
        }

        probe = competitive_direction_plans(base, limit=2)[0]
        self.assertEqual("beam", probe["method_family"])
        self.assertEqual("beam-package", probe["method_package_id"])
        self.assertEqual("probe", probe["experiment_stage"])
        self.assertEqual(1, len(probe["activation_checks"]))

        tournament = dict(base)
        tournament["experiment_stage"] = "research_tournament"
        compared = competitive_direction_plans(tournament, limit=2)[0]
        self.assertEqual("tabu", compared["method_family"])
        self.assertEqual("tabu-package", compared["method_package_id"])

    def test_research_tournament_compiles_each_family_to_one_package_stage(self) -> None:
        packages = {
            family: json.loads(
                (
                    ROOT
                    / "knowledge"
                    / "method_packages"
                    / package_id
                    / "implementation_contract.json"
                ).read_text(encoding="utf-8")
            )
            for family, package_id in {
                "constructive_search": "fjsp_min_time_lag_constructive_adaptation",
                "coupled_local_search": "fjsp_min_time_lag_coupled_local_search",
                "exact_hybrid": "fjsp_min_time_lag_exact_hybrid",
            }.items()
        }
        base = {
            "direction_id": "min-lag-family-tournament",
            "experiment_stage": "research_tournament",
            "candidate_variants": [
                {
                    "candidate_id": family,
                    "method_family": family,
                    "method_package_id": contract["contract_id"],
                    "implementation_bundle": contract,
                }
                for family, contract in packages.items()
            ],
        }

        expanded = competitive_direction_plans(base, limit=3)

        self.assertEqual(3, len(expanded))
        self.assertEqual(list(packages), [item["method_family"] for item in expanded])
        stage_is_strict_subset: list[bool] = []
        for plan in expanded:
            contract = packages[plan["method_family"]]
            lane = plan["worker_lane"]
            self.assertEqual("direct_evidence", lane["track_id"])
            self.assertEqual(0, lane["stage"])
            self.assertTrue(lane["stage_id"])
            self.assertEqual("family_hypothesis_tournament", lane["mechanism_selection"])
            self.assertLessEqual(
                len(plan["implementation_order"]),
                len(contract["required_components"]),
            )
            stage_is_strict_subset.append(
                len(plan["implementation_order"]) < len(contract["required_components"])
            )
            expected_stage = contract["competition_tracks"][0]["stages"][0]
            self.assertTrue(
                set(expected_stage["component_ids"]).issubset(plan["implementation_order"])
            )
            self.assertEqual(
                set(plan["implementation_order"]),
                {item["component_id"] for item in plan["deliverables"]},
            )
            self.assertTrue(plan["checkpoint_checks"])
            acceptance_checkpoint_ids = {
                check.split(":", 1)[0].removeprefix("Checkpoint ")
                for check in plan["acceptance_checks"]
                if check.startswith("Checkpoint ")
            }
            self.assertEqual(
                {check["check_id"] for check in plan["checkpoint_checks"]},
                acceptance_checkpoint_ids,
            )
        self.assertTrue(any(stage_is_strict_subset))

        constructive = expanded[0]
        self.assertNotIn(
            "beam_state_evidence",
            " ".join(constructive["acceptance_checks"]),
        )
        local_search = expanded[1]
        self.assertIn(
            "neighborhood_reachability",
            {check["check_id"] for check in local_search["checkpoint_checks"]},
        )

    def test_delegated_worker_lane_policy_expands_generic_parallel_lanes(self) -> None:
        base = {
            "direction_id": "d000",
            "title": "Fast delegated checkpoint",
            "experiment_stage": "probe",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "knowledge_query": ["initialization", "decoder"],
            "candidate_variants": [],
            "worker_lane_policy": {
                "mechanism_selection": "delegated_to_worker",
                "lane_count": 3,
            },
        }

        expanded = competitive_direction_plans(base, limit=4)

        self.assertEqual(3, len(expanded))
        self.assertEqual(
            ["constructive_search", "constructive_search", "constructive_search"],
            [item["method_family"] for item in expanded],
        )
        self.assertEqual(
            [["initialization", "decoder"]] * 3,
            [item["knowledge_query"] for item in expanded],
        )
        self.assertTrue(all(item["candidate_variants"] == [] for item in expanded))
        self.assertEqual(
            3,
            len(
                {
                    item["candidate_variant"]["candidate_id"]
                    for item in expanded
                    if isinstance(item.get("candidate_variant"), dict)
                }
            ),
        )

    def test_awls_delegated_lanes_have_distinct_dependency_closed_stage_bundles(self) -> None:
        contract = json.loads(
            (
                ROOT
                / "knowledge"
                / "method_packages"
                / "standard_fjsp_awls_hgtsa"
                / "implementation_contract.json"
            ).read_text(encoding="utf-8")
        )
        base = {
            "direction_id": "awls",
            "method_family": "coupled_local_search",
            "method_package_id": "standard_fjsp_awls_hgtsa",
            "implementation_bundle": contract,
            "candidate_variants": [],
            "worker_lane_policy": {
                "mechanism_selection": "delegated_to_worker",
                "lane_count": 3,
            },
        }

        expanded = competitive_direction_plans(base, limit=3)
        bundles = [tuple(item["implementation_order"]) for item in expanded]

        self.assertEqual(3, len(set(bundles)))
        self.assertIn("alternative_machine_neighborhood", bundles[0])
        self.assertNotIn("same_machine_neighborhood", bundles[0])
        self.assertIn("same_machine_neighborhood", bundles[1])
        self.assertNotIn("alternative_machine_neighborhood", bundles[1])
        self.assertIn("same_machine_neighborhood", bundles[2])
        self.assertIn("alternative_machine_neighborhood", bundles[2])
        self.assertTrue(all("progress_decoder" in bundle for bundle in bundles))
        self.assertTrue(all(item["worker_lane"]["stage"] == 0 for item in expanded))
        self.assertTrue(
            all(
                [check["check_id"] for check in item["checkpoint_checks"]]
                == ["decoder_legality_surface"]
                for item in expanded
            )
        )

        states = {
            item["candidate_variant"]["candidate_id"]: LaneDevelopmentState(
                candidate_id=item["candidate_variant"]["candidate_id"],
                method_family="coupled_local_search",
                method_package_id="standard_fjsp_awls_hgtsa",
                checkpoint_worktree=ROOT,
                objective_key=(-2241.0,),
                track=item["worker_lane"]["track_id"],
                stage=1,
                verified_components=list(item["implementation_order"]),
            )
            for item in expanded
        }
        next_stage = competitive_direction_plans(
            base,
            limit=3,
            lane_development_states=states,
        )
        next_bundles = [tuple(item["implementation_order"]) for item in next_stage]
        self.assertEqual(3, len(set(next_bundles)))
        self.assertTrue(all(item["worker_lane"]["stage"] == 1 for item in next_stage))
        self.assertIn("tabu_and_aspiration", next_bundles[0])
        self.assertIn("alternative_machine_neighborhood", next_bundles[1])
        self.assertIn("tabu_and_aspiration", next_bundles[2])

        completed_states = {
            item["candidate_variant"]["candidate_id"]: LaneDevelopmentState(
                candidate_id=item["candidate_variant"]["candidate_id"],
                method_family="coupled_local_search",
                method_package_id="standard_fjsp_awls_hgtsa",
                checkpoint_worktree=ROOT,
                objective_key=(-2241.0,),
                track=item["worker_lane"]["track_id"],
                stage=item["worker_lane"]["stage_count"],
                verified_components=list(item["implementation_order"]),
            )
            for item in expanded
        }
        completed = competitive_direction_plans(
            base,
            limit=4,
            lane_development_states=completed_states,
        )
        self.assertEqual(3, len(completed))
        self.assertTrue(
            all(item["worker_lane"]["stage_status"] == "completed" for item in completed)
        )
        self.assertTrue(
            all(
                item["worker_lane"]["stage"] == item["worker_lane"]["stage_count"]
                for item in completed
            )
        )
        self.assertTrue(all(item["checkpoint_checks"] == [] for item in completed))

    def test_minimum_time_lag_constructive_lanes_have_distinct_stage_contracts(self) -> None:
        contract = json.loads(
            (
                ROOT
                / "knowledge"
                / "method_packages"
                / "fjsp_min_time_lag_constructive_adaptation"
                / "implementation_contract.json"
            ).read_text(encoding="utf-8")
        )
        base = {
            "direction_id": "min-lag-constructive",
            "method_family": "constructive_search",
            "method_package_id": "fjsp_min_time_lag_constructive_adaptation",
            "implementation_bundle": contract,
            "candidate_variants": [],
            "worker_lane_policy": {
                "mechanism_selection": "delegated_to_worker",
                "lane_count": 3,
            },
        }

        expanded = competitive_direction_plans(base, limit=3)
        bundles = [tuple(item["implementation_order"]) for item in expanded]

        self.assertEqual(3, len(set(bundles)))
        self.assertIn("lag_aware_idle_gap_insertion", bundles[0])
        self.assertIn("assignment_pressure_and_regret", bundles[1])
        self.assertIn("bounded_beam_states", bundles[2])
        self.assertTrue(all("lag_state_and_decoder" in bundle for bundle in bundles))
        self.assertTrue(all(item["checkpoint_checks"] for item in expanded))

    def test_lane_checkpoint_accepts_equal_candidate_without_touching_official_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            states = {
                "lane-01-direct_evidence": LaneDevelopmentState(
                    candidate_id="lane-01-direct_evidence",
                    method_family="coupled_local_search",
                    method_package_id="standard_fjsp_awls_hgtsa",
                    checkpoint_worktree=parent,
                    objective_key=(-100.0,),
                    track="direct_evidence",
                    stage=0,
                    verified_components=[],
                    session_id="ses-lane-1",
                )
            }
            plan = {
                "method_family": "coupled_local_search",
                "method_package_id": "standard_fjsp_awls_hgtsa",
                "candidate_variant": {"candidate_id": "lane-01-direct_evidence"},
                "implementation_order": ["progress_decoder", "alternative_machine_neighborhood"],
                "checkpoint_checks": [
                    {
                        "check_id": "critical_search_evidence",
                        "requirement": "The assigned neighborhood is reachable and executed.",
                    }
                ],
                "worker_lane": {
                    "track_id": "direct_evidence",
                    "stage": 0,
                    "stage_count": 3,
                },
            }
            outcome = {
                "candidate_id": "lane-01-direct_evidence",
                "status": "completed",
                "core_eligible": True,
                "semantic_eligible": True,
                "ja_accepted": True,
                "objective_key": [-100.0],
                "summary": {
                    "total": 1,
                    "valid": 1,
                    "failed": 0,
                    "best_metrics": {"makespan": 100},
                },
                "worktree": str(candidate),
                "requested_session_id": "ses-lane-1",
                "command_session_id": "ses-lane-1",
                "observed_session_id": "ses-lane-1",
                "session_reused": True,
                "session_event_stream_bytes": 12,
                "semantic_review": {
                    "status": "pass",
                    "accepted": True,
                    "reviewer": "test_semantic_reviewer",
                },
            }

            update_lane_development_states(
                states,
                candidate_plans=[plan],
                outcomes=[outcome],
                incumbent_worktree=parent,
                incumbent_key=(-100.0,),
                round_index=1,
            )

            state = states["lane-01-direct_evidence"]
            self.assertEqual(candidate.resolve(), state.checkpoint_worktree)
            self.assertEqual((-100.0,), state.objective_key)
            self.assertEqual(1, state.stage)
            self.assertEqual("continued", state.session_status)
            self.assertTrue(outcome["checkpoint_decision"]["accepted"])

    def test_timeout_noop_cannot_advance_or_replace_lane_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            states = {
                "lane": LaneDevelopmentState(
                    candidate_id="lane",
                    method_family="constructive_search",
                    method_package_id="",
                    checkpoint_worktree=parent,
                    objective_key=(-100.0,),
                    track="direct_evidence",
                    stage=0,
                    verified_components=[],
                )
            }
            plan = {
                "target_file": "examples/solver.py",
                "method_family": "constructive_search",
                "candidate_variant": {"candidate_id": "lane"},
                "implementation_order": ["lag_aware_dispatch"],
                "checkpoint_checks": [
                    {
                        "check_id": "lag_aware_dispatch_used",
                        "requirement": "The assigned dispatch mechanism is reachable.",
                    }
                ],
                "worker_lane": {"track_id": "direct_evidence", "stage": 0, "stage_count": 2},
            }
            outcome = {
                "candidate_id": "lane",
                "status": "timeout",
                "worker_status": "timeout",
                "worker_changed_files": [],
                "target_changed": False,
                "core_eligible": True,
                "ja_accepted": True,
                "objective_key": [-100.0],
                "summary": {"total": 1, "valid": 1, "failed": 0},
                "worktree": str(candidate),
                "semantic_review": {
                    "status": "pass",
                    "accepted": True,
                    "reviewer": "test_semantic_reviewer",
                },
            }

            update_lane_development_states(
                states,
                candidate_plans=[plan],
                outcomes=[outcome],
                incumbent_worktree=parent,
                incumbent_key=(-100.0,),
                round_index=1,
            )

            self.assertEqual(parent.resolve(), states["lane"].checkpoint_worktree)
            self.assertEqual(0, states["lane"].stage)
            self.assertEqual([], states["lane"].verified_components)
            self.assertFalse(outcome["checkpoint_decision"]["accepted"])
            self.assertFalse(outcome["checkpoint_decision"]["stage_complete"])

    def test_valid_timeout_target_change_can_checkpoint_without_stage_completion(self) -> None:
        checkpoint = evaluate_lane_checkpoint(
            {
                "status": "completed",
                "worker_status": "timeout",
                "target_changed": True,
                "core_eligible": True,
                "ja_accepted": True,
                "objective_key": [-100.0],
                "summary": {"total": 1, "valid": 1, "failed": 0},
                "semantic_review": {
                    "status": "pass",
                    "accepted": True,
                    "reviewer": "test_semantic_reviewer",
                },
            },
            parent_key=(-100.0,),
            candidate_plan={
                "implementation_order": ["lag_aware_dispatch"],
                "checkpoint_checks": [
                    {
                        "check_id": "lag_aware_dispatch_used",
                        "requirement": "The assigned mechanism is reachable.",
                    }
                ],
            },
        )

        self.assertTrue(checkpoint["accepted"])
        self.assertFalse(checkpoint["stage_complete"])
        self.assertEqual("worker_not_completed", checkpoint["stage_reason"])

    def test_lane_checkpoint_advances_assigned_stage_when_full_package_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            states = {
                "lane": LaneDevelopmentState(
                    candidate_id="lane",
                    method_family="coupled_local_search",
                    method_package_id="awls",
                    checkpoint_worktree=parent,
                    objective_key=(-100.0,),
                    track="direct_evidence",
                    stage=0,
                    verified_components=[],
                )
            }
            plan = {
                "method_family": "coupled_local_search",
                "method_package_id": "awls",
                "candidate_variant": {"candidate_id": "lane"},
                "implementation_order": [
                    "state_and_initialization",
                    "progress_decoder",
                    "alternative_machine_neighborhood",
                ],
                "checkpoint_checks": [
                    {
                        "check_id": "decoder_legality_surface",
                        "component_ids": ["state_and_initialization", "progress_decoder"],
                        "requirement": "The decoder remains complete and legal.",
                    }
                ],
                "worker_lane": {"track_id": "direct_evidence", "stage": 0, "stage_count": 3},
            }
            outcome = {
                "candidate_id": "lane",
                "status": "completed",
                "core_eligible": True,
                "semantic_eligible": False,
                "ja_accepted": True,
                "objective_key": [-99.0],
                "summary": {"total": 1, "valid": 1, "failed": 0},
                "worktree": str(candidate),
                "semantic_review": {
                    "status": "repair_required",
                    "accepted": False,
                    "reviewer": "test_semantic_reviewer",
                    "findings": [],
                    "coverage_complete": False,
                    "component_coverage": [
                        {"component_id": "state_and_initialization", "status": "implemented"},
                        {"component_id": "progress_decoder", "status": "implemented"},
                        {
                            "component_id": "alternative_machine_neighborhood",
                            "status": "implemented",
                        },
                        {"component_id": "tabu_and_aspiration", "status": "missing"},
                    ],
                },
            }

            update_lane_development_states(
                states,
                candidate_plans=[plan],
                outcomes=[outcome],
                incumbent_worktree=parent,
                incumbent_key=(-100.0,),
                round_index=0,
            )

            state = states["lane"]
            self.assertEqual(candidate.resolve(), state.checkpoint_worktree)
            self.assertEqual((-99.0,), state.objective_key)
            self.assertEqual(1, state.stage)
            self.assertEqual(plan["implementation_order"], state.verified_components)
            self.assertTrue(outcome["checkpoint_decision"]["accepted"])
            self.assertTrue(outcome["checkpoint_decision"]["stage_complete"])

    def test_lane_checkpoint_keeps_equal_code_when_semantic_review_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            states = {
                "lane": LaneDevelopmentState(
                    candidate_id="lane",
                    method_family="coupled_local_search",
                    method_package_id="pkg",
                    checkpoint_worktree=parent,
                    objective_key=(-100.0,),
                    track="direct",
                    stage=0,
                    verified_components=[],
                )
            }
            plan = {
                "method_family": "coupled_local_search",
                "method_package_id": "pkg",
                "candidate_variant": {"candidate_id": "lane"},
                "implementation_order": ["alternative_machine_neighborhood"],
                "checkpoint_checks": [
                    {
                        "check_id": "component_reachable",
                        "requirement": "The component must be reachable in the candidate source.",
                    }
                ],
                "worker_lane": {"track_id": "direct", "stage": 0, "stage_count": 2},
            }
            outcome = {
                "candidate_id": "lane",
                "status": "completed",
                "core_eligible": True,
                "semantic_eligible": True,
                "ja_accepted": True,
                "objective_key": [-100.0],
                "summary": {"total": 1, "valid": 1, "failed": 0},
                "worktree": str(candidate),
                "semantic_review": {
                    "status": "skipped",
                    "accepted": True,
                    "reviewer": "none",
                },
            }

            update_lane_development_states(
                states,
                candidate_plans=[plan],
                outcomes=[outcome],
                incumbent_worktree=parent,
                incumbent_key=(-100.0,),
                round_index=1,
            )

            self.assertEqual(candidate.resolve(), states["lane"].checkpoint_worktree)
            self.assertEqual(0, states["lane"].stage)
            self.assertEqual([], states["lane"].verified_components)
            self.assertEqual("checkpoint_review_unavailable", states["lane"].last_failure)
            self.assertTrue(outcome["checkpoint_decision"]["accepted"])
            self.assertFalse(outcome["checkpoint_decision"]["stage_complete"])

    def test_equal_lane_checkpoint_runs_descriptive_semantic_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reviewer = SequencedSemanticReviewer(["pass"])
            summary = RunSummary(
                total=1,
                valid=1,
                failed=0,
                best_experiment_id=None,
                best_metrics={"makespan": 100},
                best_candidate_id=None,
                best_candidate_metrics=None,
                candidate_summaries=[],
                pareto_frontier=[],
                validation_summary={},
            )
            cycle = SimpleNamespace(
                summary=summary,
                worktree_path=tmp_path,
                worker_result=SimpleNamespace(changed_files=["solver.py"]),
            )

            review = run_algorithm_semantic_review(
                reviewer=reviewer,
                cycle=cycle,
                context_packet_path=tmp_path / "context.json",
                direction_plan={
                    "checkpoint_checks": [
                        {
                            "check_id": "component_reachable",
                            "requirement": "The component must be reachable in candidate source.",
                        }
                    ]
                },
                round_index=1,
                attempt_index=0,
                output_dir=tmp_path / "review",
                incumbent_key=(-100.0,),
                candidate_key=(-100.0,),
            )

            self.assertEqual("pass", review["status"])
            self.assertEqual(1, len(reviewer.requests))

    def test_lane_session_continuation_requires_commanded_session_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            states = {
                "lane": LaneDevelopmentState(
                    candidate_id="lane",
                    method_family="coupled_local_search",
                    method_package_id="pkg",
                    checkpoint_worktree=parent,
                    objective_key=(-100.0,),
                    track="direct",
                    stage=0,
                    verified_components=[],
                    session_id="ses-requested",
                )
            }
            plan = {
                "method_family": "coupled_local_search",
                "method_package_id": "pkg",
                "candidate_variant": {"candidate_id": "lane"},
                "implementation_order": ["move"],
                "worker_lane": {"track_id": "direct", "stage": 0, "stage_count": 2},
            }
            outcome = {
                "candidate_id": "lane",
                "status": "completed",
                "core_eligible": True,
                "semantic_eligible": True,
                "ja_accepted": True,
                "objective_key": [-100.0],
                "summary": {"total": 1, "valid": 1, "failed": 0},
                "worktree": str(candidate),
                "requested_session_id": "ses-requested",
                "command_session_id": "ses-other",
                "observed_session_id": "ses-requested",
                "session_reused": True,
                "session_event_stream_bytes": 12,
            }

            update_lane_development_states(
                states,
                candidate_plans=[plan],
                outcomes=[outcome],
                incumbent_worktree=parent,
                incumbent_key=(-100.0,),
                round_index=1,
            )

            self.assertEqual("continuity_failed", states["lane"].session_status)
            self.assertIsNone(states["lane"].session_id)
            self.assertEqual(0, states["lane"].stage)
            self.assertEqual([], states["lane"].verified_components)
            self.assertEqual(candidate.resolve(), states["lane"].checkpoint_worktree)
            self.assertEqual("session_continuity_failed", states["lane"].last_failure)

    def test_lane_checkpoint_keeps_parent_for_worse_or_invalid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            plan = {
                "method_family": "coupled_local_search",
                "method_package_id": "pkg",
                "candidate_variant": {"candidate_id": "lane"},
                "implementation_order": ["move"],
                "worker_lane": {"track_id": "direct", "stage": 0, "stage_count": 2},
            }
            cases = (
                ([-101.0], {}),
                (
                    [-99.0],
                    {
                        "status": "repair_required",
                        "accepted": False,
                        "findings": [{"blocking": True}],
                    },
                ),
            )
            for objective_key, semantic_review in cases:
                states = {
                    "lane": LaneDevelopmentState(
                        candidate_id="lane",
                        method_family="coupled_local_search",
                        method_package_id="pkg",
                        checkpoint_worktree=parent,
                        objective_key=(-100.0,),
                        track="direct",
                        stage=0,
                        verified_components=[],
                    )
                }
                outcome = {
                    "candidate_id": "lane",
                    "status": "completed",
                    "core_eligible": True,
                    "semantic_eligible": not bool(semantic_review),
                    "ja_accepted": True,
                    "objective_key": objective_key,
                    "summary": {"total": 1, "valid": 1, "failed": 0},
                    "worktree": str(candidate),
                    "semantic_review": semantic_review,
                }
                update_lane_development_states(
                    states,
                    candidate_plans=[plan],
                    outcomes=[outcome],
                    incumbent_worktree=parent,
                    incumbent_key=(-100.0,),
                    round_index=1,
                )
                self.assertEqual(parent.resolve(), states["lane"].checkpoint_worktree)
                self.assertEqual(0, states["lane"].stage)
                self.assertFalse(outcome["checkpoint_decision"]["accepted"])

    def test_failed_component_check_keeps_legal_code_without_advancing_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parent = tmp_path / "parent"
            candidate = tmp_path / "candidate"
            parent.mkdir()
            candidate.mkdir()
            states = {
                "lane": LaneDevelopmentState(
                    candidate_id="lane",
                    method_family="coupled_local_search",
                    method_package_id="pkg",
                    checkpoint_worktree=parent,
                    objective_key=(-100.0,),
                    track="direct",
                    stage=0,
                    verified_components=[],
                )
            }
            plan = {
                "method_family": "coupled_local_search",
                "method_package_id": "pkg",
                "candidate_variant": {"candidate_id": "lane"},
                "implementation_order": ["alternative_machine_neighborhood"],
                "checkpoint_checks": [
                    {
                        "id": "component_ran",
                        "path": "diagnostics.telemetry.component_ready",
                        "operator": "gt",
                        "expected": 0,
                    }
                ],
                "worker_lane": {"track_id": "direct", "stage": 0, "stage_count": 2},
            }
            outcome = {
                "candidate_id": "lane",
                "status": "completed",
                "core_eligible": True,
                "semantic_eligible": True,
                "ja_accepted": True,
                "objective_key": [-100.0],
                "summary": {
                    "total": 1,
                    "valid": 1,
                    "failed": 0,
                    "best_metrics": {"makespan": 100},
                },
                "worktree": str(candidate),
            }

            update_lane_development_states(
                states,
                candidate_plans=[plan],
                outcomes=[outcome],
                incumbent_worktree=parent,
                incumbent_key=(-100.0,),
                round_index=1,
            )

            self.assertEqual(candidate.resolve(), states["lane"].checkpoint_worktree)
            self.assertEqual(0, states["lane"].stage)
            self.assertEqual([], states["lane"].verified_components)
            self.assertEqual("checkpoint_checks_failed", states["lane"].last_failure)
            self.assertTrue(outcome["checkpoint_decision"]["accepted"])
            self.assertFalse(outcome["checkpoint_decision"]["stage_complete"])

    def test_lane_package_pivot_archives_and_resets_lineage(self) -> None:
        state = LaneDevelopmentState(
            candidate_id="lane",
            method_family="coupled_local_search",
            method_package_id="awls",
            checkpoint_worktree=ROOT,
            objective_key=(-100.0,),
            track="direct_evidence",
            stage=2,
            verified_components=["progress_decoder"],
            session_id="ses-old",
        )

        reusable, archived = reusable_lane_development_state(
            state,
            candidate_plan={
                "method_family": "population_memetic",
                "method_package_id": "memetic",
            },
        )

        self.assertIsNone(reusable)
        self.assertIs(state, archived)

    def test_stale_lane_code_rebases_to_official_incumbent_but_keeps_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "stale-lane"
            incumbent = root / "official-incumbent"
            stale.mkdir()
            incumbent.mkdir()
            state = LaneDevelopmentState(
                candidate_id="lane",
                method_family="coupled_local_search",
                method_package_id="pkg",
                checkpoint_worktree=stale,
                objective_key=(-9972.0,),
                track="direct_evidence",
                stage=2,
                verified_components=["stale_component"],
                session_id="ses-lane",
            )

            rebased, archived = lane_development_state_for_incumbent(
                state,
                candidate_plan={
                    "method_family": "coupled_local_search",
                    "method_package_id": "pkg",
                },
                incumbent_worktree=incumbent,
                incumbent_key=(-4191.0,),
            )

            self.assertIsNotNone(rebased)
            self.assertIs(state, archived)
            self.assertEqual(incumbent.resolve(), rebased.checkpoint_worktree)
            self.assertEqual((-4191.0,), rebased.objective_key)
            self.assertEqual(0, rebased.stage)
            self.assertEqual([], rebased.verified_components)
            self.assertEqual("ses-lane", rebased.session_id)
            self.assertEqual("rebased_to_official_incumbent", rebased.last_failure)

    def test_delegated_lane_plan_rebases_stale_stage_to_official_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "stale-lane"
            incumbent = root / "official-incumbent"
            stale.mkdir()
            incumbent.mkdir()
            candidate_id = "lane-01-direct_evidence"
            states = {
                candidate_id: LaneDevelopmentState(
                    candidate_id=candidate_id,
                    method_family="coupled_local_search",
                    method_package_id="pkg",
                    checkpoint_worktree=stale,
                    objective_key=(-9972.0,),
                    track="direct_evidence",
                    stage=2,
                    verified_components=["stale_component"],
                    session_id="ses-lane",
                )
            }
            plan = {
                "direction_id": "d001",
                "method_family": "coupled_local_search",
                "method_package_id": "pkg",
                "worker_lane_policy": {
                    "mechanism_selection": "delegated_to_worker",
                    "lane_count": 1,
                    "roles": ["direct_evidence"],
                },
            }

            expanded = competitive_direction_plans(
                plan,
                limit=1,
                lane_development_states=states,
                incumbent_worktree=incumbent,
                incumbent_key=(-4191.0,),
            )

            lane = expanded[0]["worker_lane"]
            self.assertEqual(0, lane["stage"])
            self.assertEqual([], lane["verified_components"])
            self.assertEqual(str(incumbent.resolve()), lane["parent_checkpoint"])

    def test_compact_direction_plan_keeps_candidate_semantic_status_for_history(self) -> None:
        compacted = compact_round_direction_plan(
            {
                "direction_id": "d000",
                "competition_result": {
                    "candidates": [
                        {
                            "candidate_id": "beam",
                            "semantic_review": {"status": "pass", "accepted": True},
                        }
                    ]
                },
            }
        )

        semantic = compacted["competition_result"]["candidates"][0]["semantic_review"]
        self.assertEqual("pass", semantic["status"])
        self.assertTrue(semantic["accepted"])

    def test_competing_workers_select_best_core_candidate_with_semantic_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json")
            scores = {"c0": 100, "c1": 80, "c2": 90}

            def fake_cycle(**kwargs):  # noqa: ANN003 - mirrors the orchestration call surface.
                candidate_id = kwargs["direction_plan"]["candidate_variant"]["candidate_id"]
                makespan = scores[candidate_id]
                summary = RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id=candidate_id,
                    best_metrics={
                        "makespan": makespan,
                        "solver_evidence": {"diagnostics": {"telemetry": {"variant_ran": 1}}},
                    },
                    best_candidate_id=candidate_id,
                    best_candidate_metrics={"avg_makespan": makespan},
                    candidate_summaries=[],
                    pareto_frontier=[],
                    validation_summary={},
                )
                cycle = SimpleNamespace(
                    summary=summary,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="accepted",
                        issues=[],
                        suggestions=[],
                        checks={},
                    ),
                    worker_result=SimpleNamespace(
                        status="ok",
                        changed_files=["examples/solver.py"],
                    ),
                    worktree_path=tmp_path / candidate_id,
                    patch_path=tmp_path / f"{candidate_id}.patch",
                )
                semantic = (
                    {"status": "repair_required", "accepted": False, "findings": [{"blocking": True}]}
                    if candidate_id == "c1"
                    else {"status": "pass", "accepted": True, "findings": []}
                )
                return cycle, tmp_path / f"{candidate_id}.json", [{"semantic_review": semantic}]

            direction_plan = {
                "direction_id": "d000",
                "target_file": "examples/solver.py",
                "activation_checks": [
                    {
                        "id": "variant_ran",
                        "path": "diagnostics.telemetry.variant_ran",
                        "operator": "gte",
                        "expected": 1,
                    }
                ],
                "candidate_variants": [
                    {
                        "candidate_id": candidate_id,
                        "hypothesis": f"variant {candidate_id}",
                        "next_mutation": {"change": f"change {candidate_id}"},
                    }
                    for candidate_id in scores
                ],
            }
            baseline = RunSummary(0, 0, 0, None, {})
            with patch(
                "harness_agent.orchestration.loop.run_worker_cycle_with_in_round_repairs",
                side_effect=fake_cycle,
            ):
                cycle, _context, _attempts, result, selected = run_competing_worker_cycles(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "round_000",
                    base_context_packet_path=tmp_path / "context.json",
                    round_index=0,
                    worker=NullWorker(),
                    experiment_id="competition-test",
                    max_steps=1,
                    max_runtime_seconds=10,
                    apply_worker_changes=False,
                    baseline_summary=baseline,
                    incumbent_key=(-120.0,),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=0,
                    direction_plan=direction_plan,
                    semantic_reviewer=None,
                    assignment_issuer=SimpleNamespace(),
                    worker_input_root=ROOT,
                    user_intervention=None,
                    max_competing_workers=4,
                )

            self.assertEqual("c1", selected["candidate_variant"]["candidate_id"])
            self.assertEqual("c1", result["selected_candidate_id"])
            self.assertTrue(result["selected_for_promotion"])
            self.assertEqual("c1", result["best_legal_candidate"]["candidate_id"])
            self.assertEqual("c1", result["best_activated_candidate"]["candidate_id"])
            self.assertEqual([-80.0], result["best_legal_candidate"]["objective_key"])
            blocked = next(item for item in result["candidates"] if item["candidate_id"] == "c1")
            self.assertFalse(blocked["semantic_eligible"])
            self.assertTrue(blocked["eligible"])
            self.assertEqual(80, cycle.summary.best_metrics["makespan"])

            unactivated_plan = dict(direction_plan)
            unactivated_plan["activation_contract_version"] = 1
            unactivated_plan["activation_checks"] = [
                {
                    "id": "variant_ran_twice",
                    "path": "diagnostics.telemetry.variant_ran",
                    "operator": "gte",
                    "expected": 2,
                }
            ]
            with patch(
                "harness_agent.orchestration.loop.run_worker_cycle_with_in_round_repairs",
                side_effect=fake_cycle,
            ):
                _cycle, _context, _attempts, advisory_result, _selected = run_competing_worker_cycles(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "round_001",
                    base_context_packet_path=tmp_path / "context.json",
                    round_index=1,
                    worker=NullWorker(),
                    experiment_id="competition-no-eligible-test",
                    max_steps=1,
                    max_runtime_seconds=10,
                    apply_worker_changes=False,
                    baseline_summary=baseline,
                    incumbent_key=(-120.0,),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=0,
                    direction_plan=unactivated_plan,
                    semantic_reviewer=None,
                    assignment_issuer=SimpleNamespace(),
                    worker_input_root=ROOT,
                    user_intervention=None,
                    max_competing_workers=4,
                )

            self.assertEqual("selected", advisory_result["status"])
            self.assertEqual("c1", advisory_result["selected_candidate_id"])
            self.assertEqual([-80.0], advisory_result["selected_objective_key"])
            self.assertEqual("c1", advisory_result["measured_candidate_id"])
            self.assertEqual("c1", advisory_result["best_legal_candidate"]["candidate_id"])
            self.assertIsNone(advisory_result["best_activated_candidate"])
            self.assertTrue(advisory_result["activation_advisory_only"])
            self.assertTrue(
                all(not item["activation_eligible"] for item in advisory_result["candidates"])
            )

    def test_exact_execution_requires_observed_cp_sat_call(self) -> None:
        missing = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="missing",
            best_metrics={"solver_evidence": {"diagnostics": {"cp_sat_available": False}}},
        )
        called = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="called",
            best_metrics={
                "solver_evidence": {
                    "diagnostics": {"cp_sat": {"cp_sat_called": True, "solve_status": "OPTIMAL"}}
                }
            },
        )

        self.assertFalse(
            evaluate_exact_solver_execution({"method_family": "exact_hybrid"}, missing)["passed"]
        )
        self.assertTrue(
            evaluate_exact_solver_execution({"method_family": "exact_hybrid"}, called)["passed"]
        )
        self.assertIsNone(
            evaluate_exact_solver_execution({"method_family": "coupled_local_search"}, missing)["passed"]
        )

    def test_exact_fallback_cannot_beat_effective_heuristic_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json")

            def fake_cycle(**kwargs):  # noqa: ANN003 - mirrors orchestration call surface.
                plan = kwargs["direction_plan"]
                candidate_id = plan["candidate_variant"]["candidate_id"]
                is_exact = plan["method_family"] == "exact_hybrid"
                makespan = 80 if is_exact else 90
                summary = RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id=candidate_id,
                    best_metrics={
                        "makespan": makespan,
                        "solver_evidence": {
                            "diagnostics": {"cp_sat_available": False, "cp_sat_called": False}
                        },
                    },
                    best_candidate_id=candidate_id,
                    best_candidate_metrics={"avg_makespan": makespan},
                    candidate_summaries=[],
                    pareto_frontier=[],
                    validation_summary={},
                )
                cycle = SimpleNamespace(
                    summary=summary,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="accepted",
                        issues=[],
                        suggestions=[],
                        checks={},
                    ),
                    worker_result=SimpleNamespace(
                        status="ok",
                        changed_files=["examples/solver.py"],
                    ),
                    worktree_path=tmp_path / candidate_id,
                    patch_path=tmp_path / f"{candidate_id}.patch",
                )
                return cycle, tmp_path / f"{candidate_id}.json", [{"semantic_review": {}}]

            direction_plan = {
                "direction_id": "d000",
                "target_file": "examples/solver.py",
                "experiment_stage": "research_tournament",
                "candidate_variants": [
                    {
                        "candidate_id": "exact",
                        "hypothesis": "exact fallback",
                        "next_mutation": {"change": "try CP-SAT"},
                        "method_family": "exact_hybrid",
                        "method_families": [{"id": "exact_hybrid", "role": "primary"}],
                    },
                    {
                        "candidate_id": "heuristic",
                        "hypothesis": "heuristic search",
                        "next_mutation": {"change": "run local search"},
                        "method_family": "coupled_local_search",
                        "method_families": [{"id": "coupled_local_search", "role": "primary"}],
                    },
                ],
            }
            with patch(
                "harness_agent.orchestration.loop.run_worker_cycle_with_in_round_repairs",
                side_effect=fake_cycle,
            ):
                _cycle, _context, _attempts, result, selected = run_competing_worker_cycles(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "round_000",
                    base_context_packet_path=tmp_path / "context.json",
                    round_index=0,
                    worker=NullWorker(),
                    experiment_id="exact-evidence-test",
                    max_steps=1,
                    max_runtime_seconds=10,
                    apply_worker_changes=False,
                    baseline_summary=RunSummary(0, 0, 0, None, {}),
                    incumbent_key=(-120.0,),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=0,
                    direction_plan=direction_plan,
                    semantic_reviewer=None,
                    assignment_issuer=SimpleNamespace(),
                    worker_input_root=ROOT,
                    user_intervention=None,
                    max_competing_workers=2,
                )

            exact = next(item for item in result["candidates"] if item["candidate_id"] == "exact")
            self.assertFalse(exact["exact_execution_eligible"])
            self.assertFalse(exact["eligible"])
            self.assertEqual("heuristic", result["selected_candidate_id"])
            self.assertEqual("heuristic", selected["candidate_variant"]["candidate_id"])
            self.assertTrue(result["selected_for_promotion"])

    def test_noop_candidate_cannot_win_effective_lane_competition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json")

            def fake_cycle(**kwargs):  # noqa: ANN003 - mirrors orchestration call surface.
                candidate_id = kwargs["direction_plan"]["candidate_variant"]["candidate_id"]
                makespan = 80 if candidate_id == "noop" else 90
                summary = RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id=candidate_id,
                    best_metrics={"makespan": makespan},
                    best_candidate_id=candidate_id,
                    best_candidate_metrics={"avg_makespan": makespan},
                    candidate_summaries=[],
                    pareto_frontier=[],
                    validation_summary={},
                )
                cycle = SimpleNamespace(
                    summary=summary,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="accepted",
                        issues=[],
                        suggestions=[],
                        checks={},
                    ),
                    worker_result=SimpleNamespace(
                        status="ok",
                        changed_files=[] if candidate_id == "noop" else ["examples/solver.py"],
                    ),
                    worktree_path=tmp_path / candidate_id,
                    patch_path=tmp_path / f"{candidate_id}.patch",
                )
                return cycle, tmp_path / f"{candidate_id}.json", [
                    {"semantic_review": {"status": "pass", "accepted": True}}
                ]

            direction_plan = {
                "direction_id": "d000",
                "target_file": "examples/solver.py",
                "candidate_variants": [
                    {"candidate_id": "changed"},
                    {"candidate_id": "noop"},
                ],
            }
            with patch(
                "harness_agent.orchestration.loop.run_worker_cycle_with_in_round_repairs",
                side_effect=fake_cycle,
            ):
                _cycle, _context, _attempts, result, selected = run_competing_worker_cycles(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "round_000",
                    base_context_packet_path=tmp_path / "context.json",
                    round_index=0,
                    worker=NullWorker(),
                    experiment_id="competition-noop-test",
                    max_steps=1,
                    max_runtime_seconds=10,
                    apply_worker_changes=False,
                    baseline_summary=RunSummary(0, 0, 0, None, {}),
                    incumbent_key=(-100.0,),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=0,
                    direction_plan=direction_plan,
                    semantic_reviewer=None,
                    assignment_issuer=SimpleNamespace(),
                    worker_input_root=ROOT,
                    user_intervention=None,
                    max_competing_workers=2,
                )

            self.assertEqual("changed", result["selected_candidate_id"])
            self.assertEqual("changed", selected["candidate_variant"]["candidate_id"])
            noop = next(item for item in result["candidates"] if item["candidate_id"] == "noop")
            self.assertFalse(noop["eligible"])
            self.assertFalse(noop["target_changed"])

    def test_failed_activation_is_advisory_for_local_trial_parent(self) -> None:
        cycle = SimpleNamespace(
            agentic_judgment=AgenticJudgment(
                accepted=True,
                right=True,
                stage="accepted",
                issues=[],
                suggestions=[],
                checks={},
            ),
            summary=RunSummary(
                total=1,
                valid=1,
                failed=0,
                best_experiment_id="candidate",
                best_metrics={"completed_weight": 12.0},
            ),
        )

        self.assertTrue(
            local_trial_candidate_eligible(
                cycle,
                candidate_key=(12.0,),
                semantic_review={"status": "pass", "accepted": True},
                mechanism_activation={"status": "failed", "passed": False},
                activation_required=True,
            )
        )

    def test_failed_activation_does_not_block_final_promotion(self) -> None:
        class UnactivatedPlanner:
            def plan_direction(self, request):  # noqa: ANN001 - follows planner protocol.
                return {
                    "schema_version": 1,
                    "direction_id": "unactivated-improvement",
                    "title": "Objective improvement with advisory telemetry",
                    "strategy_type": "dispatch_rule",
                    "hypothesis": "Improve the fixed objective while recording missing telemetry.",
                    "worker_objective": "Improve the dummy solver.",
                    "change_scope": ["dummy finish expression"],
                    "preserve": ["output contract"],
                    "avoid": ["evaluator changes"],
                    "acceptance_checks": ["fixed evaluator valid"],
                    "activation_contract_version": 1,
                    "activation_checks": [
                        {
                            "id": "missing_counter",
                            "path": "diagnostics.telemetry.missing_counter",
                            "operator": "gt",
                            "expected": 0,
                        }
                    ],
                    "candidate_variants": [],
                }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=_write_test_context(tmp_path),
                worker=PromotingProposalWorker(),
                main_agent=UnactivatedPlanner(),
                experiment_id="activation-advisory-promotion",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=0,
                max_competing_workers=1,
            )

        self.assertEqual("promoted", result.rounds[0].decision)
        self.assertFalse(result.rounds[0].mechanism_activation["passed"])
        self.assertNotEqual(
            "mechanism_not_activated",
            result.rounds[0].promotion_check.get("reason"),
        )

    def test_soft_accepted_legal_non_improvement_does_not_consume_repair_attempt(self) -> None:
        cycle = SimpleNamespace(
            worker_result=WorkerResult(
                status="ok",
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                summary="Legal but not better.",
            ),
            agentic_judgment=AgenticJudgment(
                accepted=False,
                right=False,
                stage="quality_contract",
                issues=["agent_generated_solver_self_check_incomplete"],
                suggestions=[],
                checks={
                    "soft_accepted_by_diagnostic_smoke": {
                        "reason": "fixed evaluator proved complete legal output",
                    }
                },
            ),
            summary=RunSummary(
                total=1,
                valid=1,
                failed=0,
                best_experiment_id="candidate",
                best_metrics={"makespan": 2648},
            ),
        )

        self.assertFalse(
            should_attempt_in_round_repair(
                cycle,
                incumbent_key=(-2648.0,),
                semantic_review={"status": "pass", "accepted": True},
            )
        )

    def test_candidate_worktree_stages_first_instance_for_worker_read_only_access(self) -> None:
        contract = TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    docs=[ROOT / "examples" / "web_demo_io.md"],
                    output_path=tmp_path / "context.json",
                )
            )
            worktree = tmp_path / "candidate"
            prepare_candidate_worktree(
                project_root=ROOT,
                contract=contract,
                worktree_path=worktree,
                context_packet_path=context_path,
            )

            manifest_path = worktree / ".algoforge_worker_inputs" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            local_path = worktree / manifest["instances"][0]["local_path"]

            self.assertTrue(manifest["read_only"])
            self.assertEqual("tiny", manifest["instances"][0]["id"])
            self.assertEqual(
                (ROOT / "examples" / "standard_fjsp_tiny.fjs").read_bytes(),
                local_path.read_bytes(),
            )
            mirrored_io = worktree / ".algoforge_worker_inputs" / "docs" / "000_web_demo_io.md"
            self.assertEqual(
                (ROOT / "examples" / "web_demo_io.md").read_text(encoding="utf-8"),
                mirrored_io.read_text(encoding="utf-8"),
            )

    def test_main_agent_direction_plan_reaches_every_attempt_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            class StaticPlanner:
                def plan_direction(self, request):  # noqa: ANN001 - follows planner protocol.
                    return {
                        "schema_version": 1,
                        "direction_id": "d000",
                        "title": "decoder repair",
                        "strategy_type": "repair_rule",
                        "hypothesis": "Repair the decoder before objective tuning.",
                        "preserve": ["current parser"],
                        "change_scope": ["decoder only"],
                        "avoid": ["unrelated tie break"],
                        "knowledge_paths": [],
                        "acceptance_checks": ["fixed evaluator valid"],
                        "stop_conditions": ["repair budget exhausted"],
                        "planner": "test",
                    }

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                main_agent=StaticPlanner(),
                experiment_id="test_main_agent_plan",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=0,
            )

            round_context = json.loads(
                (tmp_path / "loop" / "round_000" / "context_packet.json").read_text(encoding="utf-8")
            )

            self.assertEqual("decoder repair", round_context["loop_feedback"]["current_direction_plan"]["title"])
            self.assertEqual(["decoder only"], round_context["loop_feedback"]["current_direction_plan"]["change_scope"])
            self.assertTrue(result.rounds[0].round_reflection)
            self.assertTrue(
                (tmp_path / "loop" / "round_000" / "main_agent_reflection" / "round_reflection.json").is_file()
            )

    def test_round_gate_pauses_after_main_analysis_and_user_direction_replans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            planner_feedback: list[dict] = []
            gate_calls: list[tuple[int, str]] = []

            class InterventionPlanner:
                def plan_direction(self, request):  # noqa: ANN001 - follows planner protocol.
                    planner_feedback.append(request.loop_feedback)
                    user_direction = (request.loop_feedback.get("user_intervention") or {}).get("direction")
                    return {
                        "schema_version": 1,
                        "direction_id": f"d{request.round_index:03d}",
                        "title": user_direction or f"automatic direction {request.round_index}",
                        "strategy_type": "local_search_operator",
                        "hypothesis": user_direction or "Improve one bounded operator.",
                        "diagnosis": "The prior round did not improve the incumbent.",
                        "observed_shortcomings": ["The candidate neighborhood remained too narrow."],
                        "evidence_summary": ["Core rolled the candidate back."],
                        "direction_judgment": user_direction or "Try the next evidence-backed component.",
                        "change_scope": [user_direction or "one operator"],
                        "preserve": [],
                        "avoid": [],
                        "knowledge_paths": [],
                        "acceptance_checks": ["fixed evaluator"],
                        "stop_conditions": ["repair budget exhausted"],
                    }

            def gate(next_round_index, previous_round, proposed_direction):  # noqa: ANN001
                gate_calls.append((next_round_index, proposed_direction["title"]))
                self.assertEqual(0, previous_round.round_index)
                return "Expand critical reassignment insertion positions."

            run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                main_agent=InterventionPlanner(),
                experiment_id="test_round_intervention",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=0,
                round_intervention=gate,
            )

            round_context = json.loads(
                (tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8")
            )
            patch_audit = json.loads(
                (
                    tmp_path
                    / "loop"
                    / "round_001"
                    / "main_agent_user_revision"
                    / "direction_patch.json"
                ).read_text(encoding="utf-8")
            )
            applied_plan = json.loads(
                (
                    tmp_path
                    / "loop"
                    / "round_001"
                    / "main_agent_user_revision"
                    / "applied_direction_plan.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual([(1, "automatic direction 1")], gate_calls)
        self.assertEqual(3, len(planner_feedback))
        self.assertEqual(
            "Expand critical reassignment insertion positions.",
            planner_feedback[-1]["user_intervention"]["direction"],
        )
        self.assertEqual(
            "Expand critical reassignment insertion positions.",
            round_context["loop_feedback"]["user_intervention"]["direction"],
        )
        self.assertEqual("revise", patch_audit["action"])
        self.assertTrue(patch_audit["preserved_fields"])
        self.assertEqual(
            "Expand critical reassignment insertion positions.",
            applied_plan["title"],
        )

    def test_round_gate_continue_skips_revision_call_and_keeps_active_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            class PivotSuggestingPlanner:
                def __init__(self) -> None:
                    self.calls = 0

                def plan_direction(self, request):  # noqa: ANN001 - follows planner protocol.
                    self.calls += 1
                    pivot = request.round_index > 0
                    family = "coupled_local_search" if pivot else "constructive_search"
                    candidate = "critical-block" if pivot else "pressure-regret"
                    return {
                        "schema_version": 1,
                        "direction_id": f"d{request.round_index:03d}",
                        "title": f"{family} proposal",
                        "method_family": family,
                        "method_families": [{"id": family, "role": "primary"}],
                        "strategy_type": "bounded_probe",
                        "hypothesis": "Run one bounded candidate.",
                        "change_scope": ["one bounded mechanism"],
                        "candidate_variants": [{"candidate_id": candidate}],
                        "activation_checks": [],
                    }

            planner = PivotSuggestingPlanner()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                main_agent=planner,
                experiment_id="test_continue_active_direction",
                iterations=3,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=0,
                round_intervention=lambda *_args: {
                    "source": "direction_change_timeout_default_continue",
                    "direction_patch": {
                        "action": "continue",
                        "instructions": "Continue the active direction.",
                    },
                },
            )

            audit = json.loads(
                (
                    tmp_path
                    / "loop"
                    / "round_001"
                    / "main_agent_user_revision"
                    / "direction_patch.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(3, planner.calls)
        self.assertEqual("deterministic_continue", audit["status"])
        self.assertTrue(audit["skipped_planner_revision"])
        self.assertEqual("constructive_search", result.rounds[1].direction_plan["method_family"])
        self.assertEqual(
            "pressure-regret",
            result.rounds[1].direction_plan["candidate_variants"][0]["candidate_id"],
        )

    def test_in_round_repair_continues_from_previous_candidate_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            project_roots: list[Path] = []
            worktrees: list[Path] = []

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors worker-cycle API.
                attempt_index = len(project_roots)
                project_roots.append(Path(kwargs["project_root"]))
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True, exist_ok=True)
                worktree = output_dir / "candidate_worktree"
                worktree.mkdir(parents=True, exist_ok=True)
                worktrees.append(worktree)
                patch_path = output_dir / "worker_changes.patch"
                delta_path = output_dir / "worker_worktree_delta.json"
                patch_path.write_text("", encoding="utf-8")
                delta_path.write_text("{}", encoding="utf-8")
                accepted = attempt_index == 1
                return SimpleNamespace(
                    worker_result=WorkerResult(
                        status="applied",
                        changed_files=["examples/dummy_solver.py"],
                        summary=f"attempt {attempt_index}",
                    ),
                    summary=RunSummary(
                        total=1 if accepted else 0,
                        valid=1 if accepted else 0,
                        failed=0,
                        best_experiment_id="candidate" if accepted else None,
                        best_metrics={"completed_weight": 12.0, "runtime_seconds": 0.01} if accepted else {},
                    ),
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=accepted,
                        right=accepted,
                        stage="code_generation",
                        issues=[] if accepted else ["python_compile_error"],
                        suggestions=[],
                        checks={},
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=None,
                    smoke_output_dir=None,
                    diagnostic_smoke_summary=None,
                    diagnostic_smoke_output_dir=None,
                    full_evaluation_started=accepted,
                )

            with patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle):
                cycle, _context, _attempts = run_worker_cycle_with_in_round_repairs(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop" / "round_000",
                    base_context_packet_path=context_path,
                    round_index=0,
                    worker=NullWorker(),
                    experiment_id="direction_workspace",
                    max_steps=1,
                    max_runtime_seconds=30,
                    apply_worker_changes=True,
                    baseline_summary=RunSummary(
                        total=1,
                        valid=1,
                        failed=0,
                        best_experiment_id="baseline",
                        best_metrics={"completed_weight": 10.0, "runtime_seconds": 0.01},
                    ),
                    incumbent_key=(10.0, -0.01),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=1,
                )

            self.assertEqual(2, len(project_roots))
            self.assertEqual(worktrees[0], project_roots[1])
            self.assertEqual(worktrees[1], cycle.worktree_path)

    def test_session_local_trials_reuse_session_and_select_best_valid_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            project_roots: list[Path] = []
            requested_sessions: list[str | None] = []
            worktrees: list[Path] = []
            scores = [12.0, 11.0, 10.0]

            session_worker = SimpleNamespace(
                capabilities=lambda: WorkerCapabilities(
                    name="session-worker",
                    supports_code_generation=True,
                    supports_repair=True,
                    supports_structured_output=False,
                    supports_session_reuse=True,
                )
            )

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors worker-cycle API.
                attempt_index = len(project_roots)
                project_roots.append(Path(kwargs["project_root"]))
                requested_sessions.append(kwargs.get("session_id"))
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True, exist_ok=True)
                worktree = output_dir / "candidate_worktree"
                worktree.mkdir(parents=True, exist_ok=True)
                worktrees.append(worktree)
                patch_path = output_dir / "worker_changes.patch"
                delta_path = output_dir / "worker_worktree_delta.json"
                patch_path.write_text("", encoding="utf-8")
                delta_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    worker_result=WorkerResult(
                        status="completed",
                        changed_files=["examples/dummy_solver.py"],
                        summary=f"local trial {attempt_index + 1}",
                        artifacts={"session_id": "ses_direction_123"},
                    ),
                    summary=RunSummary(
                        total=1,
                        valid=1,
                        failed=0,
                        best_experiment_id=f"trial_{attempt_index + 1}",
                        best_metrics={"completed_weight": scores[attempt_index], "runtime_seconds": 0.01},
                    ),
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="candidate_result_revalidation",
                        issues=[],
                        suggestions=[],
                        checks={},
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=None,
                    smoke_output_dir=None,
                    diagnostic_smoke_summary=None,
                    diagnostic_smoke_output_dir=None,
                    full_evaluation_started=True,
                )

            with patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle):
                cycle, _context, attempts = run_worker_cycle_with_in_round_repairs(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop" / "round_000",
                    base_context_packet_path=context_path,
                    round_index=0,
                    worker=session_worker,
                    experiment_id="session_direction",
                    max_steps=1,
                    max_runtime_seconds=30,
                    apply_worker_changes=True,
                    baseline_summary=RunSummary(
                        total=1,
                        valid=1,
                        failed=0,
                        best_experiment_id="baseline",
                        best_metrics={"completed_weight": 10.0, "runtime_seconds": 0.01},
                    ),
                    incumbent_key=(10.0, -0.01),
                    baseline_generation=None,
                    previous_rounds=[],
                    repair_attempts=3,
                    direction_plan={
                        "direction_id": "session-direction",
                        "worker_objective": "Refine the current direction with bounded Core feedback.",
                        "deliverables": [
                            {
                                "id": "bounded_refinement",
                                "behavior": "Make one bounded same-direction refinement.",
                                "evidence_required": "Core-valid candidate output.",
                            }
                        ],
                        "implementation_order": ["bounded_refinement"],
                        "activation_checks": [],
                    },
                )

            self.assertEqual([None, "ses_direction_123", "ses_direction_123"], requested_sessions)
            self.assertEqual([ROOT, worktrees[0], worktrees[0]], project_roots)
            self.assertEqual(worktrees[0], cycle.worktree_path)
            self.assertEqual(["selected", "rejected", "rejected"], [item["disposition"] for item in attempts])
            self.assertEqual([False, True, True], [item["session_reused"] for item in attempts])
            self.assertEqual("checkpoint_interval_reached", attempts[-1]["termination_reason"])

    def test_same_direction_reuses_winning_session_across_checkpoint_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            worker = SameDirectionRefinementWorker()

            class SameFamilyPlanner:
                def plan_direction(self, request):  # noqa: ANN001 - follows planner protocol.
                    return {
                        "schema_version": 1,
                        "direction_id": f"same-family-{request.round_index}",
                        "title": "Continue constructive refinement",
                        "method_family": "constructive_search",
                        "strategy_type": "dispatch_rule",
                        "hypothesis": "Continue the same family with a different bounded mutation.",
                        "worker_objective": "Refine the current constructive rule.",
                        "change_scope": ["one dispatch rule"],
                        "preserve": ["current legal incumbent"],
                        "avoid": ["method-family switch"],
                        "acceptance_checks": ["fixed evaluator remains valid"],
                        "activation_checks": [],
                        "candidate_variants": [],
                    }

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=worker,
                main_agent=SameFamilyPlanner(),
                experiment_id="test_cross_batch_session",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=2,
                max_competing_workers=1,
            )

            self.assertEqual([None, "ses_refinement", "ses_refinement", "ses_refinement"], worker.requested_sessions)
            self.assertEqual(["ses_refinement", "ses_refinement"], [item.worker_session_id for item in result.rounds])
            second_competition = result.rounds[1].direction_plan["competition_result"]
            self.assertEqual("ses_refinement", second_competition["continued_session_id"])
            restored = load_worker_loop_result(tmp_path / "loop" / "loop_result.json")
            self.assertEqual(["ses_refinement", "ses_refinement"], [item.worker_session_id for item in restored.rounds])

    def test_fast_planning_runs_three_stable_lanes_and_continues_every_lane_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            worker = SameDirectionRefinementWorker()

            class FastPlanner:
                planning_mode = "fast"

                def plan_direction(self, request):  # noqa: ANN001 - follows planner protocol.
                    return {
                        "schema_version": 1,
                        "direction_id": f"fast-{request.round_index}",
                        "title": "One checkpoint per outer round",
                        "method_family": "constructive_search",
                        "method_families": [{"id": "constructive_search", "role": "primary"}],
                        "strategy_type": "dispatch_rule",
                        "hypothesis": "Continue one bounded constructive experiment.",
                        "worker_objective": "Run one Core-checked checkpoint.",
                        "change_scope": ["one dispatch rule"],
                        "activation_checks": [],
                        "candidate_variants": [],
                        "worker_lane_policy": {
                            "mechanism_selection": "delegated_to_worker",
                            "lane_count": 3,
                        },
                    }

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=worker,
                main_agent=FastPlanner(),
                experiment_id="test_fast_checkpoint",
                iterations=3,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=3,
                max_competing_workers=3,
            )
            restored = load_worker_loop_result(tmp_path / "loop" / "loop_result.json")

            round_zero_candidates = {
                item["candidate_id"]: item
                for item in result.rounds[0].direction_plan["competition_result"]["candidates"]
            }
            round_one_candidates = {
                item["candidate_id"]: item
                for item in result.rounds[1].direction_plan["competition_result"]["candidates"]
            }
            round_one_parents = {
                Path(item["parent_checkpoint"]).resolve()
                for item in round_one_candidates.values()
            }
            self.assertEqual(1, len(round_one_parents))
            official_parent = next(iter(round_one_parents))
            for candidate_id, first_outcome in round_zero_candidates.items():
                first_state = first_outcome["lane_development_state"]
                self.assertTrue(first_outcome["checkpoint_decision"]["accepted"])
                self.assertEqual(
                    official_parent,
                    Path(round_one_candidates[candidate_id]["parent_checkpoint"]).resolve(),
                )
                self.assertEqual(0, first_state["stage"])
                self.assertEqual([], first_state["verified_components"])
                self.assertFalse(first_outcome["checkpoint_decision"]["stage_complete"])

        self.assertEqual(9, worker.calls)
        self.assertEqual(3, len(result.rounds))
        first_winner = result.rounds[0].direction_plan["selected_candidate_variant"]["candidate_id"]
        final_winner = result.rounds[-1].direction_plan["selected_candidate_variant"]["candidate_id"]
        self.assertEqual(first_winner, final_winner)
        continued = [
            item for item in worker.requested_session_candidates if item[1] is not None
        ]
        self.assertEqual(6, len(continued))
        self.assertEqual(
            {
                "lane-01-direct_evidence",
                "lane-02-minimal_risk",
                "lane-03-orthogonal_mechanism",
            },
            {candidate_id for candidate_id, _session_id in continued},
        )
        self.assertEqual(3, len(result.lane_development_states or {}))
        self.assertTrue(
            all(state.session_id == "ses_refinement" for state in (result.lane_development_states or {}).values())
        )
        self.assertEqual(
            set(result.lane_development_states),
            set(restored.lane_development_states),
        )
        self.assertTrue(
            all(state.session_id == "ses_refinement" for state in restored.lane_development_states.values())
        )

    def test_worker_session_is_cleared_only_for_direction_pivot(self) -> None:
        previous = SimpleNamespace(
            worker_session_id="ses_direction",
            direction_plan={
                "method_family": "constructive_search",
                "selected_candidate_variant": {"candidate_id": "lane-02-minimal_risk"},
            },
        )

        self.assertEqual(
            "ses_direction",
            continuing_direction_worker_session(
                previous,
                {"method_family": "constructive_search", "experiment_stage": "probe"},
            ),
        )
        self.assertIsNone(
            continuing_direction_worker_session(
                previous,
                {"method_family": "coupled_local_search", "experiment_stage": "pivot"},
            )
        )
        self.assertEqual(
            "lane-02-minimal_risk",
            continuing_direction_worker_lane(
                previous,
                {"method_family": "constructive_search", "experiment_stage": "probe"},
            ),
        )
        self.assertIsNone(
            continuing_direction_worker_lane(
                previous,
                {"method_family": "coupled_local_search", "experiment_stage": "pivot"},
            )
        )
        previous.direction_plan["selected_candidate_variant"] = {}
        previous.direction_plan["competition_result"] = {
            "selected_candidate_id": "lane-02-minimal_risk"
        }
        self.assertEqual(
            "lane-02-minimal_risk",
            continuing_direction_worker_lane(
                previous,
                {"method_family": "constructive_search", "experiment_stage": "scale"},
            ),
        )

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
            self.assertFalse(
                (tmp_path / "loop" / "round_000" / "repair_001" / "context_packet.json").exists()
            )
            self.assertGreater(round_record.candidate_summary["total"], 0)

    def test_timeout_with_observed_session_continues_local_trial(self) -> None:
        cycle = SimpleNamespace(
            worker_result=WorkerResult(
                status="timeout",
                changed_files=[],
                summary="Timed out after producing a resumable event stream.",
                artifacts={
                    "session_id": "ses_resumable",
                    "observed_session_id": "ses_resumable",
                    "event_stream_bytes": "325622",
                },
            ),
            summary=RunSummary(
                total=0,
                valid=0,
                failed=0,
                best_experiment_id=None,
                best_metrics={},
            ),
        )

        self.assertTrue(should_attempt_in_round_repair(cycle))

    def test_invalid_assignment_does_not_spend_another_local_trial(self) -> None:
        cycle = SimpleNamespace(
            worker_result=WorkerResult(
                status="invalid_assignment",
                changed_files=[],
                summary="Required incumbent target is missing.",
                artifacts={"assignment_error": "worker_assignment_error.json"},
            ),
            summary=RunSummary(
                total=0,
                valid=0,
                failed=0,
                best_experiment_id=None,
                best_metrics={},
            ),
        )

        self.assertFalse(should_attempt_in_round_repair(cycle))

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
            self.assertEqual("provisional_review_required", feedback["failure_memory"]["status"])
            self.assertEqual([], feedback["failure_memory"]["must_avoid"])
            self.assertTrue(feedback["failure_memory"]["review_required"])
            self.assertIn(
                "legal_but_not_strictly_better",
                feedback["failure_memory"]["recent_failures"][0]["failure_signatures"],
            )
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
                max_competing_workers=1,
            )

            self.assertEqual(1, worker.calls)
            self.assertFalse(worker.saw_repair_feedback)
            self.assertEqual(["rolled_back"], [item.decision for item in result.rounds])
            self.assertEqual((990.0, -0.01), result.final_key)
            self.assertNotIn("in_round_repair", result.rounds[0].proposal_diagnostics)

    def test_semantic_review_does_not_drive_worker_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            worker = SemanticRepairWorker()
            reviewer = SequencedSemanticReviewer(["repair_required", "pass"])

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=worker,
                semantic_reviewer=reviewer,
                experiment_id="test_semantic_repair",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
                max_competing_workers=1,
            )

            self.assertEqual(1, worker.calls)
            self.assertFalse(worker.saw_semantic_repair)
            self.assertIsNone(result.rounds[0].semantic_review)
            self.assertNotIn("in_round_repair", result.rounds[0].proposal_diagnostics)
            repair_context = json.loads(
                (tmp_path / "loop" / "round_000" / "repair_001" / "context_packet.json").read_text(
                    encoding="utf-8"
                )
            )
            serialized = json.dumps(
                repair_context["loop_feedback"]["current_round_repair"],
                ensure_ascii=False,
            )
            self.assertNotIn("algorithm_semantic_review", serialized)
            self.assertNotIn("reverse_move_memory", serialized)

    def test_semantic_review_blocker_is_advisory_when_core_objective_improves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            reviewer = SequencedSemanticReviewer(["repair_required", "repair_required"])

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=PromotingProposalWorker(),
                semantic_reviewer=reviewer,
                experiment_id="test_semantic_rollback",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=2,
                max_competing_workers=1,
            )

            self.assertEqual("promoted", result.rounds[0].decision)
            self.assertNotEqual(result.baseline_key, result.final_key)
            self.assertTrue(result.rounds[0].promotion_check["promoted"])
            payload = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("repair_required", payload["rounds"][0]["semantic_review"]["status"])
            round_one_context = json.loads(
                (tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8")
            )
            failure_memory = round_one_context["loop_feedback"]["failure_memory"]
            self.assertEqual([], failure_memory["must_avoid"])
            self.assertEqual([], failure_memory["recent_failures"])

    def test_semantic_review_unavailable_is_advisory_for_core_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            worker = PromotingProposalWorker()
            reviewer = UnavailableSemanticReviewer()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=worker,
                semantic_reviewer=reviewer,
                experiment_id="test_semantic_unavailable",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=2,
                max_competing_workers=1,
            )

            self.assertEqual(1, len(reviewer.requests))
            self.assertEqual("promoted", result.rounds[0].decision)
            self.assertTrue(result.rounds[0].promotion_check["promoted"])
            self.assertNotIn("in_round_repair", result.rounds[0].proposal_diagnostics)
            memory = json.loads((tmp_path / "loop" / "experience_memory.json").read_text(encoding="utf-8"))
            self.assertEqual([], memory["memory_tiers"]["validated_lessons"])
            self.assertEqual(
                0,
                memory.get("algorithm_semantic_memory", {}).get("repair_required_attempt_count", 0),
            )

    def test_legal_no_improvement_continues_as_bounded_session_refinement(self) -> None:
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
                in_round_repair_attempts=2,
                max_competing_workers=1,
            )

            self.assertEqual(2, worker.calls)
            self.assertTrue(worker.saw_refinement_feedback)
            self.assertEqual([None, "ses_refinement"], worker.requested_sessions)
            self.assertEqual(["promoted"], [item.decision for item in result.rounds])
            self.assertIn("in_round_repair", result.rounds[0].proposal_diagnostics)
            self.assertTrue((tmp_path / "loop" / "round_000" / "repair_001").exists())
            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual(1, loop_result["hypothesis_graph"]["direction_count"])
            self.assertEqual(2, loop_result["hypothesis_graph"]["attempt_count"])
            self.assertTrue(loop_result["experience_memory"]["memory_tiers"]["candidate_lessons"])

    def test_baseline_memory_keeps_better_core_valid_semantic_repair_anchor(self) -> None:
        generation = {
            "source": "agent_generated",
            "status": "ok",
            "summary": {"total": 1, "valid": 1},
            "agentic_judgment": {"accepted": True, "issues": []},
            "semantic_review": {"status": "pass", "accepted": True},
            "in_round_repair": {
                "repair_attempt_count": 2,
                "recovered": True,
                "attempts": [
                    {
                        "attempt_index": 0,
                        "candidate_key": [-2596.0],
                        "summary": {"total": 1, "valid": 1},
                        "semantic_review": {"status": "repair_required", "accepted": False},
                        "context_packet_path": "C:/run/attempt_0/context.json",
                        "patch_path": "C:/run/attempt_0/worker.patch",
                    },
                    {
                        "attempt_index": 1,
                        "candidate_key": [-3695.0],
                        "summary": {"total": 1, "valid": 1},
                        "semantic_review": {"status": "pass", "accepted": True},
                        "context_packet_path": "C:/run/attempt_1/context.json",
                    },
                ],
            },
        }

        memory = agent_generated_baseline_memory_payload(generation, baseline_key=(-3695.0,))
        anchor = memory["best_core_valid_anchor"]

        self.assertEqual([-2596.0], anchor["objective_key"])
        self.assertEqual("repair_required", anchor["semantic_status"])
        self.assertFalse(anchor["promotion_eligible"])
        self.assertTrue(memory["accepted_as_incumbent"])

    def test_baseline_memory_prefers_semantic_pass_on_equal_core_key(self) -> None:
        generation = {
            "source": "agent_generated",
            "status": "ok",
            "summary": {"total": 1, "valid": 1},
            "agentic_judgment": {"accepted": True, "issues": []},
            "semantic_review": {"status": "pass", "accepted": True},
            "in_round_repair": {
                "repair_attempt_count": 1,
                "recovered": True,
                "attempts": [
                    {
                        "attempt_index": 0,
                        "candidate_key": [-2751.0],
                        "summary": {"total": 1, "valid": 1},
                        "semantic_review": {"status": "repair_required", "accepted": False},
                    },
                    {
                        "attempt_index": 1,
                        "candidate_key": [-2751.0],
                        "summary": {"total": 1, "valid": 1},
                        "semantic_review": {"status": "pass", "accepted": True},
                    },
                ],
            },
        }

        memory = agent_generated_baseline_memory_payload(generation, baseline_key=(-2751.0,))
        anchor = memory["best_core_valid_anchor"]

        self.assertEqual(1, anchor["attempt_index"])
        self.assertEqual([-2751.0], anchor["objective_key"])
        self.assertTrue(anchor["promotion_eligible"])

    def test_regressed_semantic_repair_cannot_supersede_better_core_anchor(self) -> None:
        def cycle(makespan: int):
            return SimpleNamespace(
                agentic_judgment=AgenticJudgment(
                    accepted=True,
                    right=True,
                    stage="code_generation",
                    issues=[],
                    suggestions=[],
                    checks={},
                ),
                summary=RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id="candidate",
                    best_metrics={"makespan": makespan},
                ),
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="candidate",
                ),
                diagnostic_smoke_summary=None,
            )

        selected = select_agent_generated_baseline_cycle(
            [
                (
                    0,
                    cycle(2596),
                    Path("anchor_context.json"),
                    {"status": "repair_required", "accepted": False},
                ),
                (
                    1,
                    cycle(3686),
                    Path("regressed_context.json"),
                    {"status": "quality_regressed", "accepted": False},
                ),
            ],
            objectives=[TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json").objectives[0]],
        )

        self.assertEqual(0, selected[0])
        self.assertEqual(2596, selected[1].summary.best_metrics["makespan"])

    def test_coverage_only_baseline_ranks_above_unavailable_review(self) -> None:
        def cycle(makespan: int):
            return SimpleNamespace(
                agentic_judgment=AgenticJudgment(
                    accepted=True,
                    right=True,
                    stage="code_generation",
                    issues=[],
                    suggestions=[],
                    checks={},
                ),
                summary=RunSummary(
                    total=2,
                    valid=2,
                    failed=0,
                    best_experiment_id="candidate",
                    best_metrics={"makespan": makespan},
                ),
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="candidate",
                ),
                diagnostic_smoke_summary=None,
            )

        selected = select_agent_generated_baseline_cycle(
            [
                (
                    2,
                    cycle(2323),
                    Path("coverage_context.json"),
                    {
                        "status": "repair_required",
                        "accepted": False,
                        "coverage_complete": False,
                        "findings": [],
                    },
                ),
                (
                    3,
                    cycle(2347),
                    Path("unavailable_context.json"),
                    {"status": "unavailable", "accepted": False, "findings": []},
                ),
            ],
            objectives=[TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json").objectives[0]],
        )

        self.assertEqual(2, selected[0])
        self.assertEqual(2323, selected[1].summary.best_metrics["makespan"])

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
        self.assertTrue(any("repair_targets" in item for item in feedback["must_do"]))
        self.assertNotIn("agent_generated_solver_repair_plan", targets)
        self.assertNotIn("agent_generated_solver_method_stage", targets)

    def test_semantic_feedback_is_excluded_from_repair_targets(self) -> None:
        feedback = current_round_repair_feedback(
            attempt_index=1,
            max_repair_attempts=3,
            previous_attempts=[
                {
                    "attempt_index": 0,
                    "candidate_key": [-2489.0],
                    "summary": {"total": 2, "valid": 2},
                    "agentic_judgment": {"accepted": True, "checks": {}},
                    "semantic_review": {
                        "status": "repair_required",
                        "accepted": False,
                        "summary": "Fix inverse tabu; consider optional insertion-window tuning.",
                        "findings": [
                            {
                                "finding_id": "inverse_tabu",
                                "blocking": True,
                                "repair": "Store the inverse move attribute.",
                            },
                            {
                                "finding_id": "insertion_window",
                                "blocking": False,
                                "repair": "Optionally tune the insertion window.",
                            },
                        ],
                    },
                }
            ],
        )

        serialized = json.dumps(feedback, ensure_ascii=False)
        self.assertNotIn("algorithm_semantic_review", feedback["repair_targets"])
        self.assertNotIn("inverse_tabu", serialized)
        self.assertNotIn("insertion_window", serialized)
        self.assertNotIn("optional insertion-window", serialized)

    def test_semantic_coverage_is_excluded_from_repair_targets(self) -> None:
        feedback = current_round_repair_feedback(
            attempt_index=2,
            max_repair_attempts=3,
            previous_attempts=[
                {
                    "attempt_index": 0,
                    "semantic_review": {
                        "status": "repair_required",
                        "accepted": False,
                        "component_coverage": [
                            {"component_id": "decoder", "status": "missing"},
                            {"component_id": "search", "status": "missing"},
                        ],
                        "coupled_group_coverage": [
                            {"group_id": "decode_search", "status": "missing"}
                        ],
                        "findings": [],
                    },
                },
                {
                    "attempt_index": 1,
                    "semantic_review": {
                        "status": "repair_required",
                        "accepted": False,
                        "component_coverage": [
                            {"component_id": "decoder", "status": "implemented"},
                            {"component_id": "search", "status": "partial"},
                        ],
                        "coupled_group_coverage": [
                            {"group_id": "decode_search", "status": "partial"}
                        ],
                        "findings": [],
                    },
                },
            ],
        )

        serialized = json.dumps(feedback, ensure_ascii=False)
        self.assertNotIn("algorithm_semantic_review", feedback["repair_targets"])
        self.assertNotIn("decode_search", serialized)
        self.assertNotIn('"decoder", "status": "missing"', serialized)

    def test_non_core_repair_base_is_not_labeled_core_anchor(self) -> None:
        feedback = current_round_repair_feedback(
            attempt_index=1,
            max_repair_attempts=2,
            previous_attempts=[],
            repair_anchor={
                "attempt_index": 0,
                "candidate_key": [],
                "summary": {"total": 0, "valid": 0},
                "semantic_review": {"status": "skipped", "accepted": True},
            },
        )

        self.assertNotIn("baseline_core_valid_anchor", feedback["repair_targets"])

    def test_unknown_ja_diagnostics_are_not_forwarded_as_repair_targets(self) -> None:
        feedback = current_round_repair_feedback(
            attempt_index=1,
            max_repair_attempts=2,
            previous_attempts=[
                {
                    "agentic_judgment": {
                        "accepted": False,
                        "checks": {
                            "advisory_runtime_hint": {"missing": ["unrequested_behavior"]}
                        },
                    }
                }
            ],
        )

        self.assertNotIn("advisory_runtime_hint", feedback["repair_targets"])
        self.assertNotIn("checks", feedback["previous_attempts"][0]["agentic_judgment"])

    def test_repeated_quality_risks_do_not_trigger_algorithm_specific_escalation(self) -> None:
        quality_risk = "agent_generated_solver: missing base capabilities: stable_operation_identity"
        attempt = {
            "agentic_judgment": {
                "accepted": False,
                "issues": ["agent_generated_solver_quality_contract_missing"],
                "checks": {
                    "agent_generated_solver_quality_risks": [quality_risk],
                },
            },
        }

        feedback = current_round_repair_feedback(
            attempt_index=2,
            max_repair_attempts=3,
            previous_attempts=[attempt, attempt],
        )

        self.assertEqual(
            [quality_risk],
            feedback["repair_targets"]["agent_generated_solver_quality_risks"],
        )
        self.assertNotIn("agent_generated_repair_escalation", feedback["repair_targets"])

    def test_worker_proposal_diagnostics_preserves_solver_self_check_audit_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proposal_path = Path(tmp) / "proposal.json"
            proposal_path.write_text(
                json.dumps(
                    {
                        "summary": "Generated solver had unsupported self-check prose.",
                        "proposal_audit": {
                            "warnings": ["agent_generated_solver_self_check_narrative_source_mismatch"],
                            "agent_generated_unwired_helpers": [
                                "decoder `decode_schedule` is defined but not reachable from generated solver entry flow"
                            ],
                            "solver_contract_self_check": {
                                "required": True,
                                "present": True,
                                "missing_narrative_fields": ["decoder"],
                                "narrative_with_source_mismatch": ["variant_handling"],
                                "capabilities_with_source_mismatch": ["active_io_parser"],
                                "warnings": ["agent_generated_solver_self_check_narrative_source_mismatch"],
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            diagnostics = worker_proposal_diagnostics(
                WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated solver proposal with self-check audit details.",
                    artifacts={"proposal": str(proposal_path)},
                )
            )

        audit = diagnostics["proposal_audit"]["solver_contract_self_check"]
        self.assertEqual(["decoder"], audit["missing_narrative_fields"])
        self.assertEqual(["variant_handling"], audit["narrative_with_source_mismatch"])
        self.assertEqual(["active_io_parser"], audit["capabilities_with_source_mismatch"])
        self.assertEqual(
            ["decoder `decode_schedule` is defined but not reachable from generated solver entry flow"],
            diagnostics["proposal_audit"]["agent_generated_unwired_helpers"],
        )

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
            self.assertEqual([], result.baseline_generation["hidden_incumbent_files"])
            source_project = Path(result.baseline_generation["source_project"])
            self.assertFalse((source_project / "examples" / "standard_fjsp_awls_solver.py").exists())
            self.assertFalse((source_project / "examples" / "standard_fjsp_portfolio_solver.py").exists())
            self.assertFalse((source_project / "harness_agent" / "awls_benchmark.py").exists())
            self.assertFalse((source_project / "harness_agent" / "standard_agent.py").exists())
            self.assertFalse((source_project / "harness_agent" / "strategy_variants.py").exists())
            self.assertTrue((source_project / "harness_agent" / "domains" / "io.py").exists())
            self.assertTrue((result.final_worktree / "examples" / "agent_generated_solver.py").exists())
            baseline_context = json.loads(
                (tmp_path / "loop" / "agent_generated_baseline" / "context_packet.json").read_text(encoding="utf-8")
            )
            self.assertEqual("agent_generated_baseline", baseline_context["refresh_reason"])
            self.assertIn("baseline_generation_rule", baseline_context["worker_instruction"])
            self.assertEqual([], baseline_context["baseline_generation"]["hidden_incumbent_files"])

    def test_agent_generated_baseline_does_not_repair_source_quality_contract(self) -> None:
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

            self.assertFalse(worker.saw_quality_repair_targets)
            self.assertEqual("agent_generated", result.baseline_source)
            self.assertIsNotNone(result.baseline_generation)
            generation = result.baseline_generation or {}
            self.assertEqual("ok", generation["status"])
            self.assertEqual("code_generation", generation["agentic_judgment"]["stage"])
            self.assertTrue(generation["agentic_judgment"]["accepted"], generation["agentic_judgment"]["issues"])
            self.assertEqual(1, generation["summary"]["valid"])
            self.assertTrue(generation["agentic_judgment"]["checks"]["result_revalidation"]["passed"])
            self.assertEqual(2, generation["in_round_repair"]["attempt_count"])
            self.assertEqual(1, generation["in_round_repair"]["repair_attempt_count"])
            self.assertTrue(generation["in_round_repair"]["recovered"])
            repair_context = json.loads(
                (tmp_path / "loop" / "agent_generated_baseline" / "repair_001" / "context_packet.json").read_text(
                    encoding="utf-8"
                )
            )
            repair_targets = repair_context["loop_feedback"]["current_round_repair"]["repair_targets"]
            self.assertIn("result_revalidation_top_errors", repair_targets)
            self.assertLess(result.baseline_key[0], 0.0)
            quality_summary = worker_loop_agent_quality_summary(result)
            self.assertTrue(quality_summary["baseline"]["enabled"])
            self.assertTrue(quality_summary["baseline"]["ja_accepted"])
            self.assertEqual(1, quality_summary["baseline"]["repair_attempt_count"])
            self.assertTrue(quality_summary["baseline"]["repair_recovered"])
            self.assertEqual(0, quality_summary["round_count"])

    def test_agent_generated_baseline_repair_reuses_worker_session(self) -> None:
        class SessionBaselineRepairWorker(AgentBaselineRepairWorker):
            def __init__(self) -> None:
                super().__init__()
                self.requested_sessions: list[str | None] = []

            def capabilities(self) -> WorkerCapabilities:
                base = super().capabilities()
                return WorkerCapabilities(
                    name=base.name,
                    supports_code_generation=base.supports_code_generation,
                    supports_repair=base.supports_repair,
                    supports_structured_output=base.supports_structured_output,
                    supports_session_reuse=True,
                )

            def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001
                self.requested_sessions.append(spec.session_id)
                result = super().run_experiment(spec)
                return WorkerResult(
                    status=result.status,
                    changed_files=result.changed_files,
                    summary=result.summary,
                    raw_log_path=result.raw_log_path,
                    artifacts={**(result.artifacts or {}), "session_id": "ses_baseline_123"},
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            worker = SessionBaselineRepairWorker()

            run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=worker,
                baseline_source="agent_generated",
                experiment_id="test_agent_generated_session_repair",
                iterations=0,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

        self.assertEqual([None, "ses_baseline_123"], worker.requested_sessions)

    def test_agent_generated_baseline_runs_three_staged_trials_after_legal_trial_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            project_roots: list[Path] = []
            worktrees: list[Path] = []

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors worker-cycle API.
                attempt_index = len(project_roots)
                project_roots.append(Path(kwargs["project_root"]))
                output_dir = Path(kwargs["output_dir"])
                worktree = output_dir / "candidate_worktree"
                worktrees.append(worktree)
                target = worktree / "examples" / "agent_generated_fjsp_solver.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"# staged trial {attempt_index + 1}\n", encoding="utf-8")
                patch_path = output_dir / "worker_changes.patch"
                delta_path = output_dir / "worker_worktree_delta.json"
                patch_path.write_text(f"trial {attempt_index + 1}\n", encoding="utf-8")
                delta_path.write_text("{}\n", encoding="utf-8")
                summary = RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id=f"trial_{attempt_index + 1}",
                    best_metrics={"makespan": 3000 - attempt_index},
                )
                return SimpleNamespace(
                    worker_result=WorkerResult(
                        status="ok",
                        changed_files=["examples/agent_generated_fjsp_solver.py"],
                        summary=f"staged trial {attempt_index + 1}",
                    ),
                    summary=summary,
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="code_generation",
                        issues=[],
                        suggestions=[],
                        checks={},
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=summary,
                    smoke_output_dir=output_dir / "harness_smoke",
                    diagnostic_smoke_summary=summary,
                    diagnostic_smoke_output_dir=output_dir / "harness_diagnostic_smoke",
                    full_evaluation_started=True,
                )

            with (
                patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle),
                patch(
                    "harness_agent.orchestration.loop.run_algorithm_semantic_review",
                    return_value={"status": "pass", "accepted": True, "summary": "staged solver is legal"},
                ),
            ):
                _, _, generation = run_agent_generated_baseline(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop",
                    context_packet_path=context_path,
                    worker=NullWorker(),
                    experiment_id="test_staged_baseline",
                    max_steps=2,
                    max_runtime_seconds=30,
                    repair_attempts=2,
                )

        self.assertEqual(3, len(project_roots))
        self.assertEqual(worktrees[0], project_roots[1])
        self.assertEqual(worktrees[1], project_roots[2])
        self.assertEqual(2, generation["selected_attempt_index"])
        self.assertEqual([1, 2, 3], [item["local_trial_index"] for item in generation["in_round_repair"]["attempts"]])

    def test_agent_generated_baseline_advances_stage_only_after_core_valid_result(self) -> None:
        class SessionCapableWorker(NullWorker):
            def capabilities(self) -> WorkerCapabilities:
                return WorkerCapabilities(
                    name="session-capable-test-worker",
                    supports_code_generation=True,
                    supports_repair=True,
                    supports_structured_output=False,
                    supports_session_reuse=True,
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            assignment_stages: list[int] = []
            requested_sessions: list[str | None] = []

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors worker-cycle API.
                attempt_index = len(assignment_stages)
                assignment = json.loads(Path(kwargs["worker_assignment_path"]).read_text(encoding="utf-8"))
                assignment_stages.append(int(assignment["lineage"]["baseline_trial"]))
                requested_sessions.append(kwargs.get("session_id"))
                output_dir = Path(kwargs["output_dir"])
                worktree = output_dir / "candidate_worktree"
                worktree.mkdir(parents=True, exist_ok=True)
                patch_path = output_dir / "worker_changes.patch"
                delta_path = output_dir / "worker_worktree_delta.json"
                patch_path.write_text("", encoding="utf-8")
                delta_path.write_text("{}\n", encoding="utf-8")
                if attempt_index == 0:
                    worker_result = WorkerResult(
                        status="timeout",
                        changed_files=[],
                        summary="Timed out before the first target checkpoint.",
                        artifacts={
                            "observed_session_id": "ses_baseline_resume",
                            "event_stream_bytes": "84728",
                        },
                    )
                    summary = RunSummary(
                        total=1,
                        valid=0,
                        failed=1,
                        best_experiment_id=None,
                        best_metrics={},
                    )
                else:
                    target = worktree / "examples" / "agent_generated_fjsp_solver.py"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(f"# legal stage attempt {attempt_index}\n", encoding="utf-8")
                    worker_result = WorkerResult(
                        status="completed",
                        changed_files=["examples/agent_generated_fjsp_solver.py"],
                        summary="Created a legal staged solver.",
                        artifacts={
                            "requested_session_id": "ses_baseline_resume",
                            "command_session_id": "ses_baseline_resume",
                            "observed_session_id": "ses_baseline_resume",
                            "event_stream_bytes": "1024",
                        },
                    )
                    summary = RunSummary(
                        total=1,
                        valid=1,
                        failed=0,
                        best_experiment_id=f"stage_{attempt_index}",
                        best_metrics={"makespan": 3000 - attempt_index},
                    )
                return SimpleNamespace(
                    worker_result=worker_result,
                    summary=summary,
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=attempt_index > 0,
                        right=attempt_index > 0,
                        stage="code_generation",
                        issues=[] if attempt_index > 0 else ["worker_status_not_usable: timeout"],
                        suggestions=[],
                        checks={},
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=summary if attempt_index > 0 else None,
                    smoke_output_dir=output_dir / "harness_smoke" if attempt_index > 0 else None,
                    diagnostic_smoke_summary=summary if attempt_index > 0 else None,
                    diagnostic_smoke_output_dir=(
                        output_dir / "harness_diagnostic_smoke" if attempt_index > 0 else None
                    ),
                    full_evaluation_started=attempt_index > 0,
                )

            with (
                patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle),
                patch(
                    "harness_agent.orchestration.loop.run_algorithm_semantic_review",
                    return_value={"status": "pass", "accepted": True, "summary": "staged solver is legal"},
                ),
            ):
                run_agent_generated_baseline(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop",
                    context_packet_path=context_path,
                    worker=SessionCapableWorker(),
                    experiment_id="test_staged_baseline_resume",
                    max_steps=2,
                    max_runtime_seconds=30,
                    repair_attempts=2,
                )

        self.assertEqual([1, 1, 2], assignment_stages)
        self.assertEqual([None, "ses_baseline_resume", "ses_baseline_resume"], requested_sessions)

    def test_agent_generated_baseline_stops_staging_after_nonrepairable_worker_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            cycle_calls: list[Path] = []

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors worker-cycle API.
                output_dir = Path(kwargs["output_dir"])
                cycle_calls.append(output_dir)
                worktree = output_dir / "candidate_worktree"
                worktree.mkdir(parents=True, exist_ok=True)
                patch_path = output_dir / "worker_changes.patch"
                delta_path = output_dir / "worker_worktree_delta.json"
                patch_path.write_text("", encoding="utf-8")
                delta_path.write_text("{}\n", encoding="utf-8")
                return SimpleNamespace(
                    worker_result=WorkerResult(
                        status="failed_runtime",
                        changed_files=[],
                        summary="Provider stream retry was exhausted.",
                        artifacts={
                            "provider_retry_exhausted": "true",
                            "observed_session_id": "ses_failed_provider",
                            "event_stream_bytes": "1024",
                        },
                    ),
                    summary=RunSummary(
                        total=1,
                        valid=0,
                        failed=1,
                        best_experiment_id=None,
                        best_metrics={},
                    ),
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=False,
                        right=False,
                        stage="code_generation",
                        issues=["worker_status_not_usable: failed_runtime"],
                        suggestions=[],
                        checks={},
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=None,
                    smoke_output_dir=None,
                    diagnostic_smoke_summary=None,
                    diagnostic_smoke_output_dir=None,
                    full_evaluation_started=False,
                )

            with (
                patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle),
                patch(
                    "harness_agent.orchestration.loop.run_algorithm_semantic_review",
                    return_value={"status": "skipped", "accepted": True, "summary": "not reviewed"},
                ),
            ):
                run_agent_generated_baseline(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop",
                    context_packet_path=context_path,
                    worker=NullWorker(),
                    experiment_id="test_staged_baseline_provider_failure",
                    max_steps=2,
                    max_runtime_seconds=30,
                    repair_attempts=3,
                )

        self.assertEqual(1, len(cycle_calls))
        self.assertFalse((tmp_path / "loop" / "agent_generated_baseline" / "repair_001").exists())

    def test_agent_generated_baseline_repair_keeps_best_changed_attempt_after_empty_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            project_roots: list[Path] = []
            worktrees: list[Path] = []

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors patched worker-cycle API.
                attempt_index = len(project_roots)
                project_roots.append(Path(kwargs["project_root"]))
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True, exist_ok=True)
                worktree = output_dir / "candidate_worktree"
                worktree.mkdir(parents=True, exist_ok=True)
                worktrees.append(worktree)
                patch_path = output_dir / "candidate.patch"
                delta_path = output_dir / "candidate_delta.json"
                patch_path.write_text(f"attempt {attempt_index}\n", encoding="utf-8")
                delta_path.write_text("{}", encoding="utf-8")
                changed_files = ["examples/agent_generated_fjsp_solver.py"] if attempt_index < 2 else []
                diagnostic = (
                    RunSummary(
                        total=1,
                        valid=1,
                        failed=0,
                        best_experiment_id="diagnostic",
                        best_metrics={"makespan": 123},
                    )
                    if attempt_index == 1
                    else None
                )
                return SimpleNamespace(
                    worker_result=WorkerResult(
                        status="ok",
                        changed_files=changed_files,
                        summary=f"attempt {attempt_index}",
                    ),
                    summary=RunSummary(
                        total=0,
                        valid=0,
                        failed=0,
                        best_experiment_id=None,
                        best_metrics={},
                    ),
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=False,
                        right=False,
                        stage="code_generation",
                        issues=["agent_generated_solver_quality_contract_missing"],
                        suggestions=[],
                        checks={},
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=None,
                    smoke_output_dir=None,
                    diagnostic_smoke_summary=diagnostic,
                    diagnostic_smoke_output_dir=output_dir / "harness_diagnostic_smoke" if diagnostic else None,
                    full_evaluation_started=False,
                )

            with patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle):
                summary, worktree, generation = run_agent_generated_baseline(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop",
                    context_packet_path=context_path,
                    worker=NullWorker(),
                    experiment_id="test_baseline_best_attempt",
                    max_steps=2,
                    max_runtime_seconds=30,
                    repair_attempts=2,
                )

            self.assertEqual(3, len(project_roots))
            self.assertEqual(worktrees[0], project_roots[1])
            self.assertEqual(worktrees[1], project_roots[2])
            self.assertEqual(1, generation["selected_attempt_index"])
            self.assertEqual(str(worktrees[1]), generation["worktree"])
            self.assertEqual(worktrees[1], worktree)
            self.assertEqual(0, summary.total)
            self.assertEqual(0, generation["summary"]["total"])
            self.assertTrue(generation["in_round_repair"]["final_attempt_superseded"])
            self.assertEqual(
                "changed_candidate_with_valid_diagnostic_smoke",
                generation["in_round_repair"]["selection_reason"],
            )
            self.assertEqual(2, generation["in_round_repair"]["final_attempt_index"])

    def test_agent_generated_baseline_ignores_semantic_repair_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            scores = [2596, 3686, 2500]
            project_roots: list[Path] = []
            worktrees: list[Path] = []
            semantic_attempts: list[int] = []

            def fake_run_worker_cycle(**kwargs):  # noqa: ANN001 - mirrors patched worker-cycle API.
                attempt_index = len(project_roots)
                project_roots.append(Path(kwargs["project_root"]))
                output_dir = Path(kwargs["output_dir"])
                output_dir.mkdir(parents=True, exist_ok=True)
                worktree = output_dir / "candidate_worktree"
                worktree.mkdir(parents=True, exist_ok=True)
                worktrees.append(worktree)
                patch_path = output_dir / "worker_changes.patch"
                delta_path = output_dir / "worker_worktree_delta.json"
                patch_path.write_text(f"attempt {attempt_index}\n", encoding="utf-8")
                delta_path.write_text("{}\n", encoding="utf-8")
                summary = RunSummary(
                    total=1,
                    valid=1,
                    failed=0,
                    best_experiment_id=f"attempt_{attempt_index}",
                    best_metrics={"makespan": scores[attempt_index]},
                )
                return SimpleNamespace(
                    worker_result=WorkerResult(
                        status="ok",
                        changed_files=["examples/agent_generated_fjsp_solver.py"],
                        summary=f"attempt {attempt_index}",
                    ),
                    summary=summary,
                    worktree_path=worktree,
                    harness_output_dir=output_dir / "harness",
                    delta_path=delta_path,
                    patch_path=patch_path,
                    agentic_judgment=AgenticJudgment(
                        accepted=True,
                        right=True,
                        stage="code_generation",
                        issues=[],
                        suggestions=[],
                        checks={
                            "agent_generated_solver_method_stage": {
                                "stage_name": "informational_only",
                                "missing_for_next_stage": ["unrequested_neighborhood"],
                            }
                        },
                    ),
                    agentic_error_analysis=None,
                    smoke_summary=summary,
                    smoke_output_dir=output_dir / "harness_smoke",
                    diagnostic_smoke_summary=None,
                    diagnostic_smoke_output_dir=None,
                    full_evaluation_started=True,
                )

            def fake_semantic_review(**kwargs):  # noqa: ANN001 - mirrors semantic-review wrapper.
                semantic_attempts.append(kwargs["attempt_index"])
                if kwargs["attempt_index"] == 0:
                    return {
                        "status": "repair_required",
                        "accepted": False,
                        "summary": "Two blocking findings remain.",
                        "findings": [
                            {
                                "finding_id": "inverse_move",
                                "category": "move_memory",
                                "blocking": True,
                                "repair": "Store the inverse move attribute.",
                            },
                            {
                                "finding_id": "tight_arc",
                                "category": "operator_fidelity",
                                "blocking": True,
                                "repair": "Split blocks on non-tight machine arcs.",
                            },
                        ],
                        "knowledge_paths": ["knowledge/tabu_contract.md"],
                    }
                if kwargs["attempt_index"] == 1:
                    return {
                        "status": "repair_required",
                        "accepted": False,
                        "summary": "Only tight-arc extraction remains.",
                        "findings": [
                            {
                                "finding_id": "tight_arc",
                                "category": "operator_fidelity",
                                "blocking": True,
                                "repair": "Split blocks on non-tight machine arcs.",
                            }
                        ],
                        "knowledge_paths": ["knowledge/tabu_contract.md"],
                    }
                return {
                    "status": "pass",
                    "accepted": True,
                    "summary": "The bounded repair preserves the method.",
                    "findings": [],
                }

            with (
                patch("harness_agent.orchestration.loop.run_worker_cycle", side_effect=fake_run_worker_cycle),
                patch("harness_agent.orchestration.loop.run_algorithm_semantic_review", side_effect=fake_semantic_review),
            ):
                summary, worktree, generation = run_agent_generated_baseline(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop",
                    context_packet_path=context_path,
                    worker=NullWorker(),
                    experiment_id="test_baseline_anchor_branch",
                    max_steps=2,
                    max_runtime_seconds=30,
                    repair_attempts=2,
                )

            self.assertEqual([0, 1, 2], semantic_attempts)
            self.assertEqual(3, len(project_roots))
            self.assertEqual("source_project_without_incumbent_solvers", project_roots[0].name)
            self.assertEqual(worktrees[0], project_roots[1])
            self.assertEqual(worktrees[1], project_roots[2])
            self.assertEqual(1, summary.total)
            self.assertEqual(2500, summary.best_metrics["makespan"])
            self.assertEqual(worktrees[2], worktree)
            self.assertEqual(2, generation["selected_attempt_index"])
            self.assertEqual(3, generation["in_round_repair"]["attempt_count"])

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
                semantic_reviewer=SequencedSemanticReviewer(["pass"]),
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
            self.assertNotIn("rejected_attempt_count", quality_memory)
            self.assertNotIn("recovered_direction_count", quality_memory)
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

    def test_agent_generated_baseline_repair_excludes_semantic_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            reviewer = SequencedSemanticReviewer(["repair_required", "pass"])

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=AgentBaselineRepairWorker(),
                baseline_source="agent_generated",
                semantic_reviewer=reviewer,
                experiment_id="test_agent_generated_baseline_semantic_repair",
                iterations=0,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=2,
            )

            generation = result.baseline_generation or {}
            self.assertEqual("pass", generation["semantic_review"]["status"])
            self.assertEqual(2, generation["selected_attempt_index"])
            self.assertTrue(generation["in_round_repair"]["recovered"])
            repair_context = json.loads(
                (
                    tmp_path
                    / "loop"
                    / "agent_generated_baseline"
                    / "repair_002"
                    / "context_packet.json"
                ).read_text(encoding="utf-8")
            )
            repair_targets = repair_context["loop_feedback"]["current_round_repair"]["repair_targets"]
            self.assertNotIn("algorithm_semantic_review", repair_targets)
            semantic_summary = worker_loop_semantic_review_summary(result)
            self.assertEqual(3, semantic_summary["baseline_review_attempt_count"])
            self.assertEqual(1, semantic_summary["status_counts"]["repair_required"])
            self.assertEqual(1, semantic_summary["blocking_finding_count"])

    def test_unavailable_baseline_semantic_review_does_not_cancel_requested_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            reviewer = UnavailableSemanticReviewer()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=AgentBaselineRepairWorker(),
                baseline_source="agent_generated",
                semantic_reviewer=reviewer,
                experiment_id="test_unavailable_baseline_semantic_review",
                iterations=3,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

            self.assertEqual("ok", result.status)
            self.assertEqual(3, len(result.rounds))
            generation = result.baseline_generation or {}
            self.assertEqual("ok", generation["status"])
            self.assertTrue(generation["semantic_review_degraded"])
            self.assertEqual("unavailable", generation["semantic_review"]["status"])
            self.assertTrue(generation["in_round_repair"]["recovered"])
            self.assertTrue((tmp_path / "loop" / "round_002").exists())

    def test_coverage_only_baseline_is_degraded_incumbent_and_runs_requested_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            reviewer = CoverageOnlySemanticReviewer()

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=AgentBaselineRepairWorker(),
                baseline_source="agent_generated",
                semantic_reviewer=reviewer,
                experiment_id="test_coverage_only_baseline_semantic_review",
                iterations=3,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=2,
            )

            self.assertEqual("ok", result.status)
            self.assertEqual(3, len(result.rounds))
            generation = result.baseline_generation or {}
            self.assertEqual("ok", generation["status"])
            self.assertTrue(generation["semantic_review_degraded"])
            self.assertEqual(
                "coverage_incomplete_without_verified_blocker",
                generation["semantic_review_degraded_reason"],
            )
            self.assertEqual("repair_required", generation["semantic_review"]["status"])
            self.assertEqual([], generation["semantic_review"]["findings"])
            self.assertTrue((tmp_path / "loop" / "round_002").exists())

            first_round_context = json.loads(
                (tmp_path / "loop" / "round_000" / "context_packet.json").read_text(encoding="utf-8")
            )
            feedback = first_round_context["loop_feedback"]
            baseline_memory = feedback["agent_generated_baseline_memory"]
            self.assertTrue(baseline_memory["accepted_as_incumbent"])
            self.assertEqual("core_valid_semantic_review_degraded", baseline_memory["evidence_level"])
            self.assertEqual("degraded_baseline", feedback["direction_graph"]["directions"][0]["status"])
            self.assertEqual([], feedback["experience_memory"]["memory_tiers"]["validated_lessons"])

    def test_coverage_only_review_is_advisory_for_improvement_promotion(self) -> None:
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
                semantic_reviewer=CoverageOnlySemanticReviewer(),
                experiment_id="test_coverage_only_improvement_review",
                iterations=1,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=0,
            )

            self.assertEqual("promoted", result.rounds[0].decision)
            self.assertTrue(result.rounds[0].promotion_check["promoted"])
            self.assertEqual(
                "repair_required",
                result.rounds[0].promotion_check["semantic_review_advisory"]["status"],
            )

    def test_verified_blocking_finding_still_rejects_agent_generated_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)
            reviewer = SequencedSemanticReviewer(["repair_required"])

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                baseline_worker=AgentBaselineRepairWorker(),
                baseline_source="agent_generated",
                semantic_reviewer=reviewer,
                experiment_id="test_verified_blocking_baseline_review",
                iterations=3,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=1,
            )

            self.assertEqual("baseline_generation_failed", result.status)
            self.assertEqual("semantic_review_rejected", result.stop_reason)
            self.assertEqual([], result.rounds)

    def test_invalid_agent_generated_baseline_stops_before_improvement_rounds(self) -> None:
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
                experiment_id="test_agent_generated_baseline_hard_stop",
                iterations=2,
                max_steps=2,
                max_runtime_seconds=30,
                apply_worker_changes=False,
                in_round_repair_attempts=0,
            )

            self.assertEqual("agent_generated", result.baseline_source)
            self.assertEqual("baseline_generation_failed", result.status)
            self.assertEqual("evaluator_rejected_baseline", result.stop_reason)
            self.assertEqual([], result.rounds)
            self.assertEqual((float("-inf"),), result.baseline_key)
            self.assertEqual(result.baseline_key, result.final_key)
            self.assertIsNotNone(result.baseline_generation)
            generation = result.baseline_generation or {}
            self.assertEqual("rejected", generation["status"])
            self.assertFalse(generation["accepted_as_incumbent"])
            self.assertEqual("evaluator_rejected_baseline", generation["failure_reason"])
            self.assertTrue(generation["stopped_before_rounds"])
            self.assertEqual("evaluator_rejected_baseline", generation["stop_reason"])
            self.assertTrue(generation["agentic_judgment"]["accepted"])
            self.assertFalse(
                generation["agentic_judgment"]["checks"]["result_revalidation"]["passed"]
            )
            self.assertFalse((tmp_path / "loop" / "round_000").exists())
            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("baseline_generation_failed", loop_result["status"])
            self.assertEqual("evaluator_rejected_baseline", loop_result["stop_reason"])
            self.assertEqual([], loop_result["rounds"])
            self.assertTrue(loop_result["baseline_generation"]["stopped_before_rounds"])

    def test_invalid_provided_baseline_stops_before_worker_can_rewrite_it(self) -> None:
        invalid = RunSummary(
            total=1,
            valid=0,
            failed=1,
            best_experiment_id=None,
            best_metrics={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            with patch("harness_agent.orchestration.loop._run_harness", return_value=invalid):
                result = run_worker_loop(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "loop",
                    context_packet_path=_write_test_context(tmp_path),
                    worker=NullWorker(),
                    baseline_source="provided_project",
                    experiment_id="test_provided_baseline_hard_stop",
                    iterations=3,
                    max_steps=1,
                    max_runtime_seconds=1,
                    apply_worker_changes=False,
                )

            self.assertEqual("provided_project", result.baseline_source)
            self.assertEqual("provided_baseline_failed", result.status)
            self.assertEqual("provided_project_baseline_invalid", result.stop_reason)
            self.assertEqual([], result.rounds)
            self.assertFalse((tmp_path / "loop" / "round_000").exists())
            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("provided_baseline_failed", loop_result["status"])
            self.assertEqual([], loop_result["rounds"])

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
            self.assertIsNotNone(result.best_legal_incumbent)
            self.assertEqual((992.0, -0.01), result.best_legal_incumbent.objective_key)

            round_000_delta = json.loads((tmp_path / "loop" / "round_000" / "worker_worktree_delta.json").read_text(encoding="utf-8"))
            self.assertEqual(1, round_000_delta["counts"]["modified"])
            self.assertEqual("examples/dummy_solver.py", round_000_delta["modified"][0]["path"])
            round_000_patch = (tmp_path / "loop" / "round_000" / "worker_changes.patch").read_text(encoding="utf-8")
            self.assertIn("examples/dummy_solver.py", round_000_patch)
            self.assertIn("8 + args.seed", round_000_patch)
            loop_payload = json.loads(
                (tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [992.0, -0.01],
                loop_payload["best_legal_incumbent"]["objective_key"],
            )

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
            self.assertFalse(result.agentic_judgment.checks["result_revalidation"]["passed"])
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

    def test_smoke_quick_test_failure_becomes_repairable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            failure = subprocess.CalledProcessError(
                1,
                "python -m py_compile examples/dummy_solver.py",
                stderr="compile failed",
            )

            with patch("harness_agent.orchestration.cycle.GraphHarnessRunner.run", side_effect=failure):
                result = run_worker_cycle(
                    contract=contract,
                    project_root=ROOT,
                    output_dir=tmp_path / "cycle",
                    context_packet_path=context_path,
                    worker=ImproveOnceWorker(),
                    experiment_id="test_quick_test_failure",
                    max_steps=1,
                    max_runtime_seconds=30,
                    apply_worker_changes=False,
                )

            self.assertEqual(1, result.summary.total)
            self.assertEqual(0, result.summary.valid)
            self.assertEqual(1, result.summary.failed)
            self.assertEqual(
                {"failed_quick_test": 1},
                result.summary.validation_summary["status_counts"],
            )

    def test_candidate_snapshot_ignores_opencode_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "solver.py"
            runtime_file = root / ".opencode" / "node_modules" / "runtime.js"
            solver.parent.mkdir(parents=True)
            runtime_file.parent.mkdir(parents=True)
            solver.write_text("print('ok')\n", encoding="utf-8")
            runtime_file.write_text("generated\n", encoding="utf-8")

            snapshot = collect_worktree_snapshot(root)

            self.assertIn("examples/solver.py", snapshot)
            self.assertFalse(any(path.startswith(".opencode/") for path in snapshot))

    def test_agent_generated_result_revalidation_failure_drives_repair_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract_path = _write_standard_agent_generated_contract(tmp_path)
            contract = TaskContract.load(contract_path)
            context_path = _write_test_context(tmp_path, contract_path=contract_path)

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=AgentGeneratedBareListOutputWorker(),
                experiment_id="test_agent_generated_diagnostic_smoke",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertFalse(result.agentic_judgment.checks["result_revalidation"]["passed"])
            self.assertEqual(1, result.summary.total)
            self.assertIsNotNone(result.smoke_summary)
            self.assertEqual(1, result.smoke_summary.total)
            self.assertEqual(0, result.smoke_summary.valid)
            revalidation_payload = json.dumps(
                result.agentic_judgment.checks["result_revalidation"],
                ensure_ascii=False,
            )
            self.assertIn("list", revalidation_payload)

            cycle_result = json.loads((tmp_path / "cycle" / "cycle_result.json").read_text(encoding="utf-8"))
            self.assertTrue(cycle_result["smoke_gate"]["enabled"])
            self.assertFalse(cycle_result["smoke_gate"]["passed"])
            self.assertFalse(cycle_result["diagnostic_smoke"]["enabled"])

            attempt = round_attempt_payload(
                result,
                attempt_index=0,
                context_packet_path=context_path,
            )
            feedback = current_round_repair_feedback(
                attempt_index=1,
                max_repair_attempts=2,
                previous_attempts=[attempt],
            )
            self.assertIn("evaluator_invalid_candidate", feedback["avoid"])
            self.assertIn("result_revalidation_top_errors", feedback["repair_targets"])
            self.assertIn("list", json.dumps(feedback["repair_targets"]["result_revalidation_top_errors"], ensure_ascii=False))

    def test_soft_accepts_generated_solver_quality_gap_after_valid_diagnostic_smoke(self) -> None:
        judgment = AgenticJudgment(
            accepted=False,
            right=False,
            stage="code_generation",
            issues=["agent_generated_solver_quality_contract_missing"],
            suggestions=["repair soft source-shape evidence"],
            checks={
                "agent_generated_solver_quality_risks": [
                    "agent_generated_solver: self-check evidence names a helper but the submitted code uses an alias"
                ]
            },
        )
        diagnostic = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="tiny",
            best_metrics={"makespan": 10.0},
            best_candidate_id="candidate",
            best_candidate_metrics={"avg_makespan": 10.0},
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"status_counts": {"success": 1}},
        )

        self.assertTrue(
            should_soft_accept_agent_generated_quality_rejection(
                agentic_judgment=judgment,
                diagnostic_smoke_summary=diagnostic,
            )
        )
        softened = soften_agent_generated_quality_judgment(
            agentic_judgment=judgment,
            diagnostic_smoke_summary=diagnostic,
        )
        self.assertTrue(softened.accepted)
        self.assertEqual([], softened.issues)
        self.assertIn("soft_accepted_by_diagnostic_smoke", softened.checks)
        softened_payload = softened.to_payload()
        softened_payload["checks"] = {
            **softened_payload["checks"],
            "agent_generated_solver_method_stage": {"stage_name": "name_inferred_stage"},
            "agent_generated_solver_repair_plan": {"repair_mode": "method_stage_migration"},
        }
        feedback = current_round_repair_feedback(
            attempt_index=1,
            max_repair_attempts=2,
            previous_attempts=[{"agentic_judgment": softened_payload}],
        )
        self.assertNotIn("agent_generated_solver_quality_risks", feedback["repair_targets"])
        self.assertNotIn("agent_generated_solver_method_stage", feedback["repair_targets"])
        self.assertNotIn("agent_generated_solver_repair_plan", feedback["repair_targets"])

    def test_soft_accepts_generated_solver_self_check_gap_after_valid_diagnostic_smoke(self) -> None:
        judgment = AgenticJudgment(
            accepted=False,
            right=False,
            stage="code_generation",
            issues=["agent_generated_solver_self_check_incomplete"],
            suggestions=["repair source evidence"],
            checks={
                "agent_generated_solver_quality_risks": [
                    "agent_generated_solver: missing base capabilities: complete_schedule_coverage_guard"
                ],
                "agent_generated_solver_blocking_quality_risks": [],
                "agent_generated_solver_self_check_risks": [
                    "source-level self-check missing capability evidence: complete_schedule_coverage_guard, machine_non_overlap_guard"
                ],
            },
        )
        diagnostic = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="tiny",
            best_metrics={"makespan": 10.0},
            best_candidate_id="candidate",
            best_candidate_metrics={"avg_makespan": 10.0},
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"status_counts": {"success": 1}},
        )

        self.assertTrue(
            should_soft_accept_agent_generated_quality_rejection(
                agentic_judgment=judgment,
                diagnostic_smoke_summary=diagnostic,
            )
        )
        softened = soften_agent_generated_quality_judgment(
            agentic_judgment=judgment,
            diagnostic_smoke_summary=diagnostic,
        )
        evidence = softened.checks["soft_accepted_by_diagnostic_smoke"]
        self.assertEqual(["agent_generated_solver_self_check_incomplete"], evidence["original_issues"])
        self.assertIn("complete_schedule_coverage_guard", evidence["original_self_check_risks"][0])

    def test_soft_accepts_evaluator_valid_solver_with_source_shape_capability_gap(self) -> None:
        judgment = AgenticJudgment(
            accepted=False,
            right=False,
            stage="code_generation",
            issues=["agent_generated_solver_quality_contract_missing"],
            suggestions=["repair missing constructor structure"],
            checks={
                "agent_generated_solver_quality_risks": [
                    "agent_generated_solver: missing base capabilities: operation_level_ready_list_constructor"
                ]
            },
        )
        diagnostic = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="tiny",
            best_metrics={"makespan": 10.0},
            best_candidate_id="candidate",
            best_candidate_metrics={"avg_makespan": 10.0},
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"status_counts": {"success": 1}},
        )

        self.assertTrue(
            should_soft_accept_agent_generated_quality_rejection(
                agentic_judgment=judgment,
                diagnostic_smoke_summary=diagnostic,
            )
        )

    def test_does_not_soft_accept_failed_in_place_move_without_rollback(self) -> None:
        judgment = AgenticJudgment(
            accepted=False,
            right=False,
            stage="code_generation",
            issues=["agent_generated_solver_quality_contract_missing"],
            suggestions=["make move application transactional"],
            checks={
                "agent_generated_solver_quality_risks": [
                    "agent_generated_solver: failed_move_mutates_current_without_rollback"
                ]
            },
        )
        diagnostic = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="tiny",
            best_metrics={"makespan": 10.0},
            best_candidate_id="candidate",
            best_candidate_metrics={"avg_makespan": 10.0},
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"status_counts": {"success": 1}},
        )

        self.assertFalse(
            should_soft_accept_agent_generated_quality_rejection(
                agentic_judgment=judgment,
                diagnostic_smoke_summary=diagnostic,
            )
        )

    def test_does_not_soft_accept_generated_solver_when_hard_issue_remains(self) -> None:
        judgment = AgenticJudgment(
            accepted=False,
            right=False,
            stage="code_generation",
            issues=["incomplete_solution_acceptance_risk", "agent_generated_solver_quality_contract_missing"],
            suggestions=["repair empty fallback"],
            checks={
                "incomplete_solution_acceptance_risks": [
                    "examples/agent_generated_fjsp_solver.py: empty_schedule_fallback_emitted"
                ],
                "agent_generated_solver_quality_risks": [
                    "agent_generated_solver: missing base capabilities: complete_schedule_coverage_guard"
                ],
            },
        )
        diagnostic = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="tiny",
            best_metrics={"makespan": 10.0},
            best_candidate_id="candidate",
            best_candidate_metrics={"avg_makespan": 10.0},
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"status_counts": {"success": 1}},
        )

        self.assertFalse(
            should_soft_accept_agent_generated_quality_rejection(
                agentic_judgment=judgment,
                diagnostic_smoke_summary=diagnostic,
            )
        )

    def test_code_judgment_does_not_inspect_local_search_source_semantics(self) -> None:
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

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertNotIn("incomplete_solution_acceptance_risk", result.agentic_judgment.issues)
            self.assertTrue(result.full_evaluation_started)

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
            self.assertGreater(result.summary.total, 0)
            self.assertEqual(result.summary.total, result.summary.valid)
            self.assertTrue(result.full_evaluation_started)
            self.assertEqual(
                [{"path": "examples/dummy_solver.py", "reason": "old text not found"}],
                result.agentic_judgment.checks["apply_rejections"],
            )

    def test_agent_generated_baseline_acceptance_ignores_advisory_ja(self) -> None:
        summary = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id="candidate",
            best_metrics={"makespan": 2240},
        )
        generation = {
            "status": "ok",
            "source": "agent_generated",
            "agentic_judgment": {
                "accepted": False,
                "issues": ["advisory_only"],
            },
            "semantic_review": {"status": "accepted", "accepted": True, "findings": []},
        }

        self.assertTrue(
            agent_generated_baseline_is_accepted(
                generation,
                baseline_summary=summary,
                baseline_key=(-2240.0,),
            )
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
            self.assertGreater(result.summary.total, 0)
            self.assertTrue((tmp_path / "cycle" / "agentic_judgment.json").exists())

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
            self.assertGreater(result.summary.total, 0)
            risks = result.agentic_judgment.checks["agent_generated_runtime_import_risks"]
            self.assertIn("examples/agent_generated_helper.py", risks[0])

    def test_code_judgment_does_not_semantically_inspect_protected_facts(self) -> None:
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

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertNotIn("protected_promoted_fact_regression", result.agentic_judgment.issues)
            self.assertNotIn("protected_promoted_fact_regressions", result.agentic_judgment.checks)

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

    def test_protected_fact_guard_allows_additive_remove_and_reinsert_neighborhood(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["loop_feedback"] = {
                "protected_promoted_facts": [
                    {
                        "round_index": 1,
                        "name": "critical_op_machine_reassignment_local_search",
                        "type": "local_search_operator",
                        "target_files": ["examples/dummy_solver.py"],
                        "novelty": "Preserve critical operator and eligible-machine reassignment behavior.",
                    }
                ]
            }
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_worker_cycle(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "cycle",
                context_packet_path=context_path,
                worker=AdditiveNeighborhoodMoveWorker(),
                experiment_id="test_additive_neighborhood_move",
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertNotIn("protected_promoted_fact_regressions", result.agentic_judgment.checks)

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
            self.assertNotIn("protected_promoted_fact_regressions", result.agentic_judgment.checks)

    def test_code_judgment_does_not_require_rule_hypothesis_metadata(self) -> None:
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

            self.assertTrue(result.agentic_judgment.accepted)
            self.assertNotIn("missing_rule_operator_hypotheses", result.agentic_judgment.issues)
            self.assertTrue(result.full_evaluation_started)

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
            self.assertGreater(result.summary.total, 0)


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
