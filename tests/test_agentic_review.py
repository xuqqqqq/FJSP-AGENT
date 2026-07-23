from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.judgment import judge_worker_result
from harness_agent.worker import WorkerResult


class AgenticResultVerificationTests(unittest.TestCase):
    def _judge(
        self,
        *,
        source: str,
        changed_file: str = "examples/agent_generated_fjsp_solver.py",
        status: str = "completed",
        allowed_paths: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = root / changed_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        context_path = root / "context_packet.json"
        context_path.write_text(
            json.dumps(
                {
                    "task": {"problem_family": "FJSP"},
                    "evaluator_protocol": {
                        "solver_command_template": (
                            "python examples/agent_generated_fjsp_solver.py --input {instance} "
                            "--output {solution} --seed {seed}"
                        )
                    },
                    "edit_policy": {
                        "allowed_paths": allowed_paths if allowed_paths is not None else ["examples"],
                        "forbidden_paths": forbidden_paths if forbidden_paths is not None else ["outputs", ".git"],
                    },
                }
            ),
            encoding="utf-8",
        )
        return judge_worker_result(
            worker_result=WorkerResult(status=status, changed_files=[changed_file], summary="test candidate"),
            worktree_path=root,
            context_packet_path=context_path,
            output_dir=root / "review",
            apply_worker_changes=True,
        )

    def test_accepts_compilable_candidate_without_semantic_source_shape(self) -> None:
        judgment = self._judge(source="def solve():\n    return []\n")

        self.assertTrue(judgment.accepted)
        self.assertNotIn("agent_generated_solver_self_check_incomplete", judgment.issues)
        self.assertNotIn("agent_generated_solver_quality_contract_missing", judgment.issues)

    def test_rejects_python_syntax_error(self) -> None:
        judgment = self._judge(source="def solve(:\n    pass\n")

        self.assertFalse(judgment.accepted)
        self.assertTrue(any(issue.startswith("python_syntax_error") for issue in judgment.issues))

    def test_rejects_change_outside_allowed_paths(self) -> None:
        judgment = self._judge(source="VALUE = 1\n", changed_file="src/solver.py")

        self.assertFalse(judgment.accepted)
        self.assertIn("changed_files_outside_edit_policy", judgment.issues)
        self.assertEqual(["src/solver.py"], judgment.checks["path_policy_violations"])

    def test_rejects_forbidden_path_even_when_parent_is_allowed(self) -> None:
        judgment = self._judge(
            source="VALUE = 1\n",
            changed_file="examples/standard_fjsp_evaluator.py",
            allowed_paths=["examples"],
            forbidden_paths=["examples/standard_fjsp_evaluator.py"],
        )

        self.assertFalse(judgment.accepted)
        self.assertIn("changed_files_outside_edit_policy", judgment.issues)

    def test_rejects_agent_generated_solver_importing_backend(self) -> None:
        judgment = self._judge(source="from harness_agent.domains.io import load_solution\n")

        self.assertFalse(judgment.accepted)
        self.assertIn("agent_generated_solver_imports_backend_package", judgment.issues)

    def test_rejects_obvious_hardcoded_instance_metadata(self) -> None:
        judgment = self._judge(
            source=(
                "def parse_instance(path):\n"
                "    op_info = {(0, 0): {'eligible': {0: 3}}}\n"
                "    return {'op_info': op_info}\n"
            )
        )

        self.assertFalse(judgment.accepted)
        self.assertIn("agent_generated_solver_hardcodes_instance_data", judgment.issues)

    def test_timeout_with_compilable_diff_is_not_rejected_by_timeout_alone(self) -> None:
        judgment = self._judge(source="VALUE = 1\n", status="timeout")

        self.assertTrue(judgment.accepted)
        self.assertIn("worker_timeout_after_code_change", judgment.checks["proposal_audit_warnings"])


if __name__ == "__main__":
    unittest.main()
