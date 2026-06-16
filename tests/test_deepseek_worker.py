from __future__ import annotations

import unittest

from harness_agent.workers.deepseek_worker import DeepSeekWorker, render_code_edit_markdown


class DeepSeekWorkerProposalAuditTests(unittest.TestCase):
    def test_proposal_audit_records_project_intake_usage(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_intake()
        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Adjust examples/standard_fjsp_solver.py using the project map.",
                "strategy_intent": "Use project_intake to locate the constructive solver and leave evaluator files unchanged.",
                "rule_operator_hypotheses": [
                    {
                        "name": "critical_block_bias",
                        "type": "local_search_operator",
                        "novelty": "Biases the existing search toward critical-block moves instead of another dispatch-only tweak.",
                        "expected_effect": "Reduce average makespan under the fixed evaluator.",
                        "evidence_used": ["project_intake.core_algorithm_files", "loop_feedback.previous_rounds"],
                        "target_files": ["examples/standard_fjsp_solver.py"],
                        "ablation_plan": "Run the same suite with and without the critical-block bias.",
                    }
                ],
                "changes": [
                    {
                        "path": "examples/standard_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": "print('solver')\n",
                        "rationale": "The intake marks this as an entry/core algorithm file.",
                    }
                ],
                "context_usage": {
                    "used_project_intake": True,
                    "referenced_files": ["examples/standard_fjsp_solver.py", "examples/standard_fjsp_evaluator.py"],
                    "notes": "The solver is editable; the evaluator is only a validation reference.",
                },
                "quick_test_plan": "python -m compileall harness_agent examples",
                "risk_notes": ["Do not edit evaluator semantics."],
            },
            context,
        )

        audit = normalized["proposal_audit"]
        self.assertTrue(audit["project_intake_present"])
        self.assertTrue(audit["declared_project_intake_used"])
        self.assertIn("examples/standard_fjsp_solver.py", audit["detected_referenced_intake_files"])
        self.assertEqual(1, audit["operator_lineage"]["hypothesis_count"])
        self.assertEqual(["local_search_operator"], audit["operator_lineage"]["hypothesis_types"])
        self.assertEqual(
            ["examples/standard_fjsp_solver.py"],
            audit["operator_lineage"]["target_files_overlap_changes"],
        )
        self.assertEqual(["examples/standard_fjsp_solver.py"], audit["changed_core_algorithm_files"])
        self.assertEqual([], audit["changed_validator_files"])
        self.assertIn("python -m compileall harness_agent examples", audit["referenced_test_commands"])
        self.assertNotIn("project_intake_present_but_not_referenced", audit["warnings"])

        markdown = render_code_edit_markdown(normalized)
        self.assertIn("## Context Usage", markdown)
        self.assertIn("## Rule / Operator Hypotheses", markdown)
        self.assertIn("critical_block_bias", markdown)
        self.assertIn("## Proposal Audit", markdown)
        self.assertIn("changed_core_algorithm_files", markdown)

    def test_proposal_audit_warns_when_intake_is_ignored(self) -> None:
        worker = DeepSeekWorker()
        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Make a small edit.",
                "strategy_intent": "Try a solver tweak.",
                "changes": [],
                "quick_test_plan": "",
                "risk_notes": [],
            },
            _context_packet_with_intake(),
        )

        self.assertIn("project_intake_present_but_not_referenced", normalized["proposal_audit"]["warnings"])


def _context_packet_with_intake() -> dict[str, object]:
    return {
        "edit_policy": {
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "project_intake": {
            "status": "ok",
            "summary": {
                "entry_files": ["examples/standard_fjsp_solver.py", "examples/standard_fjsp_evaluator.py"],
                "core_algorithm_files": ["examples/standard_fjsp_solver.py"],
                "validator_files": ["examples/standard_fjsp_evaluator.py"],
                "benchmark_files": ["harness_agent/benchmark_suite.py"],
                "dependency_files": ["pyproject.toml"],
                "test_commands": [
                    {
                        "source": "contract.quick_test",
                        "command": "python -m compileall harness_agent examples",
                    }
                ],
                "risk_flags": [{"code": "dirty_worktree", "message": "test risk"}],
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
