from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harness_agent.context.worker import build_worker_assignment
from harness_agent.orchestration.cycle import stage_worker_runtime_controls
from harness_agent.worker import WorkerAssignment


class WorkerCheckBudgetTests(unittest.TestCase):
    def assignment(self, budget=None):
        context = {
            "task": {"problem_family": "FJSP"},
            "guidance_ablation": {"mode": "none"},
            "evaluator_protocol": {
                "solver_command_template": "python solver.py --input {instance}",
                "solution_contract": {
                    "format": "test-output",
                    "required_top_level_fields": ["format"],
                    "worker_smoke_validation": "schema_only",
                },
            },
            "edit_policy": {"allowed_paths": ["solver.py"]},
        }
        if budget is not None:
            context["worker_execution_budget"] = budget
        return build_worker_assignment(
            context=context,
            direction_plan={"direction_id": "test", "hypothesis": "Create a standalone candidate."},
            loop_feedback={}, round_index=-1, attempt_index=0,
            max_steps=4, max_runtime_seconds=900,
        )

    def stage(self, root, budget=None):
        assignment = self.assignment(budget)
        assignment_path = root / "assignment.json"
        assignment_path.write_text(json.dumps(assignment.to_payload()), encoding="utf-8")
        inputs = root / ".algoforge_worker_inputs"
        inputs.mkdir()
        (inputs / "manifest.json").write_text(
            json.dumps({"instances": [{"local_path": "instance.json"}]}), encoding="utf-8")
        (root / "instance.json").write_text("{}", encoding="utf-8")
        stage_worker_runtime_controls(assignment_path=assignment_path, worktree_path=root)
        return root / ".algoforge_worker_runtime"

    def run_wrapper(self, root):
        return subprocess.run(
            [sys.executable, str(root / ".algoforge_worker_runtime" / "run_smoke.py")],
            cwd=root, capture_output=True, text=True, timeout=15,
        )

    def write_stub(self, root, body):
        # This fixture only exercises the generic subprocess/schema boundary.
        target = root / self.assignment().target_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import json, sys\nfrom pathlib import Path\n" + body, encoding="utf-8")

    def test_default_and_explicit_assignment_budgets(self):
        default = self.assignment()
        self.assertNotIn("max_agent_steps", default.budgets)
        self.assertEqual(1, default.budgets["max_solver_smokes"])
        self.assertEqual(3, default.budgets["max_solver_smoke_seconds"])
        self.assertIn("Compile the target solver once.", default.checks)
        custom = self.assignment({"max_agent_steps": 96, "max_solver_smokes": 4, "max_solver_smoke_seconds": 30})
        self.assertEqual(96, custom.budgets["max_agent_steps"])
        self.assertTrue(any("4 fixed-seed" in check and "30 seconds" in check for check in custom.checks))
        self.assertFalse(custom.validate())

    def test_rejects_noninteger_and_out_of_range_budgets(self):
        for name, maximum in (("max_agent_steps", 256), ("max_solver_smokes", 10), ("max_solver_smoke_seconds", 120)):
            for value in (True, "3", 1.5, None, 0, -1, maximum + 1):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        self.assignment({name: value})
                    base = self.assignment()
                    invalid = replace(base, budgets={**base.budgets, name: value})
                    with self.assertRaisesRegex(ValueError, name):
                        WorkerAssignment.from_payload(invalid.to_payload())
        for budget in ([], {"unexpected": 1}):
            with self.assertRaisesRegex(ValueError, "worker_execution_budget"):
                self.assignment(budget)

    def test_default_single_use_and_legacy_marker_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.stage(root)
            self.write_stub(root, 'Path(sys.argv[sys.argv.index("--output") + 1]).write_text(json.dumps({"format": "test-output"}))\n')
            self.assertEqual(0, self.run_wrapper(root).returncode)
            self.assertEqual(3, self.run_wrapper(root).returncode)
            (runtime / "smoke.used").unlink()
            self.assertEqual(0, self.run_wrapper(root).returncode)
            (runtime / "smoke.used").write_text("used\n", encoding="utf-8")
            self.assertEqual(3, self.run_wrapper(root).returncode)

    def test_failed_attempt_consumes_budget_but_allows_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.stage(root, {"max_solver_smokes": 2})
            self.write_stub(root, "sys.exit(7)\n")
            self.assertEqual(7, self.run_wrapper(root).returncode)
            self.write_stub(root, 'Path(sys.argv[sys.argv.index("--output") + 1]).write_text(json.dumps({"format": "test-output"}))\n')
            self.assertEqual(0, self.run_wrapper(root).returncode)
            self.assertEqual(3, self.run_wrapper(root).returncode)
            self.assertEqual("2", (runtime / "smoke.used").read_text().strip())
            self.assertFalse((runtime / "smoke.lock").exists())

    def test_stale_output_cannot_make_next_empty_attempt_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.stage(root, {"max_solver_smokes": 2})
            self.write_stub(root, 'Path(sys.argv[sys.argv.index("--output") + 1]).write_text(json.dumps({"format": "test-output"}))\n')
            self.assertEqual(0, self.run_wrapper(root).returncode)
            self.write_stub(root, "pass\n")
            self.assertEqual(4, self.run_wrapper(root).returncode)
            self.assertFalse((runtime / "smoke_solution.json").exists())

    def test_longer_smoke_budget_reaches_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.stage(root, {"max_solver_smoke_seconds": 12})
            self.write_stub(root,
                'assert sys.argv[sys.argv.index("--time-limit-sec") + 1] == "12"\n'
                'Path(sys.argv[sys.argv.index("--output") + 1]).write_text(json.dumps({"format": "test-output"}))\n')
            self.assertEqual(12, json.loads((runtime / "smoke_config.json").read_text())["time_limit_seconds"])
            result = self.run_wrapper(root)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_existing_lock_rejects_overlap_without_consuming_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.stage(root, {"max_solver_smokes": 2})
            (runtime / "smoke.lock").write_text("", encoding="utf-8")
            self.assertEqual(3, self.run_wrapper(root).returncode)
            self.assertFalse((runtime / "smoke.used").exists())
            self.assertTrue((runtime / "smoke.lock").exists())


if __name__ == "__main__":
    unittest.main()
