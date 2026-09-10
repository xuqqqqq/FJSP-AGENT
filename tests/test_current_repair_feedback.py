from __future__ import annotations

import unittest

from harness_agent.context.worker import _assignment_feedback, _repair_deliverables, build_worker_assignment
from harness_agent.orchestration.loop import collect_current_round_repair_targets, current_round_repair_feedback


def failed_attempt(index, errors, compile_errors=None):
    return {
        "attempt_index": index,
        "agentic_judgment": {
            "accepted": False, "issues": ["candidate_result_revalidation_failed"],
            "checks": {"result_revalidation": {"top_errors": errors},
                       "python_compile_errors": compile_errors or {}},
        },
        "summary": {"total": 1, "valid": 0},
    }


def feedback(attempts, anchor=None):
    return current_round_repair_feedback(
        attempt_index=len(attempts), max_repair_attempts=19,
        previous_attempts=attempts, repair_anchor=anchor,
    )


class CurrentRepairFeedbackTests(unittest.TestCase):
    def test_foundation_retry_gets_concrete_core_deliverable(self):
        error = "op: violates lower time bound; start=80, lower=200"
        current = feedback([failed_attempt(0, [error])])
        current.update(baseline_trial=1, resume_incomplete_baseline=True)
        assignment = build_worker_assignment(
            context={"task": {"problem_family": "FJSP"},
                     "evaluator_protocol": {"solver_command_template": "python examples/solver.py --input {instance}"},
                     "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]}},
            direction_plan={"direction_id": "foundation", "hypothesis": "Deliver a legal solver."},
            loop_feedback={"current_round_repair": current},
            round_index=-1, attempt_index=1, max_steps=16, max_runtime_seconds=1800,
        )
        self.assertEqual("baseline", assignment.mode)
        self.assertEqual(["repair_result_revalidation_00"], assignment.implementation_order)
        self.assertIn(error, assignment.deliverables[0]["behavior"])
        self.assertIn("blocking items", assignment.objective)

    def test_resolved_runtime_does_not_pollute_current_constraint_failure(self):
        result = feedback([failed_attempt(0, ["resolved RuntimeError"]),
                           failed_attempt(1, ["current Q lower violation"] )])
        self.assertEqual(["current Q lower violation"], result["repair_targets"]["result_revalidation_top_errors"])
        self.assertEqual([0, 1], [item["attempt_index"] for item in result["previous_attempts"]])

    def test_sixth_round_new_failure_reaches_assignment_deliverable(self):
        attempts = [failed_attempt(i, [f"obsolete failure {i}/{j}" for j in range(8)]) for i in range(5)]
        attempts.append(failed_attempt(5, ["CURRENT_NEW_FAILURE_A", "CURRENT_NEW_FAILURE_B"]))
        latest = _assignment_feedback({"current_round_repair": feedback(attempts)}, attempt_index=6)
        behaviors = "\n".join(item["behavior"] for item in _repair_deliverables(latest))
        self.assertIn("CURRENT_NEW_FAILURE_A", behaviors)
        self.assertIn("CURRENT_NEW_FAILURE_B", behaviors)
        self.assertNotIn("obsolete failure", behaviors)

    def test_successful_latest_attempt_clears_obsolete_blockers(self):
        legal = {"attempt_index": 1, "summary": {"total": 1, "valid": 1},
                 "agentic_judgment": {"accepted": True, "checks": {}},
                 "failure_signatures": ["legal_but_not_strictly_better"]}
        result = feedback([failed_attempt(0, ["old exception"]), legal])
        self.assertEqual({}, result["repair_targets"])
        self.assertEqual("refinement_required", result["status"])

    def test_older_legal_anchor_preserves_its_activation_gap_not_discarded_child_error(self):
        anchor = {
            "attempt_index": 0, "summary": {"total": 1, "valid": 1}, "candidate_key": [-100, 10],
            "agentic_judgment": {"accepted": True, "checks": {}},
            "activation_required": True,
            "mechanism_activation": {
                "passed": False, "required_failure_count": 1,
                "checks": [{"id": "entrypoint_called", "path": "diagnostics.method_calls",
                            "required": True, "passed": False, "observed": 0,
                            "operator": ">=", "expected": 1}],
            },
        }
        result = feedback([anchor, failed_attempt(1, ["discarded child exception"])], anchor=anchor)
        self.assertIn("mechanism_activation_failure", result["repair_targets"])
        self.assertNotIn("result_revalidation_top_errors", result["repair_targets"])
        self.assertEqual(0, result["repair_targets"]["baseline_core_valid_anchor"]["attempt_index"])
        self.assertEqual(2, len(result["previous_attempts"]))

    def test_explicit_invalid_anchor_also_controls_repair_base(self):
        anchor = failed_attempt(1, ["anchor defect"])
        result = feedback([anchor, failed_attempt(2, ["discarded newer defect"])], anchor=anchor)
        self.assertEqual(["anchor defect"], result["repair_targets"]["result_revalidation_top_errors"])

    def test_collect_lists_and_dictionaries_have_strict_global_cap(self):
        attempts = [failed_attempt(i, [f"failure-{i}-{j}" for j in range(8)],
                                   {f"file-{i}-{j}": "compile error" for j in range(8)}) for i in range(3)]
        result = collect_current_round_repair_targets(attempts)
        self.assertEqual(8, len(result["result_revalidation_top_errors"]))
        self.assertEqual(8, len(result["python_compile_errors"]))


if __name__ == "__main__":
    unittest.main()
