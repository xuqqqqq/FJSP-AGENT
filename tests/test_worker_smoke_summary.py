"""Bounded smoke feedback preserves actual evidence and validation decisions."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from harness_agent.orchestration.cycle import WORKER_SMOKE_RUNNER_SOURCE


class WorkerSmokeSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.scope = {"__name__": "isolated_smoke_draft", "__file__": str(self.runtime / "run_smoke.py")}
        exec(compile(WORKER_SMOKE_RUNNER_SOURCE, "isolated_run_smoke.py", "exec"), self.scope)

    def run_smoke(self, payload, *, errors=(), schema_only=False, solver_outcome=0):
        output = self.runtime / "smoke_solution.json"
        config = {"output_path": str(output), "target_file": "not_executed.py", "instance_path": "not_read.txt",
                  "time_limit_seconds": 3, "max_solver_smokes": 1,
                  "solution_contract": {"required_top_level_fields": ["makespan"]}}
        if schema_only:
            config["solution_contract"]["worker_smoke_validation"] = "schema_only"
        (self.runtime / "smoke_config.json").write_text(json.dumps(config))

        def fake_run(*args, **kwargs):
            if isinstance(solver_outcome, Exception):
                raise solver_outcome
            output.write_text(json.dumps(payload))
            return types.SimpleNamespace(returncode=solver_outcome)

        out = io.StringIO()
        with patch.object(subprocess, "run", side_effect=fake_run) as called, \
             patch("harness_agent.domains.io.parse_standard_fjsp", return_value=types.SimpleNamespace()), \
             patch("harness_agent.domains.io.load_solution_document", return_value=types.SimpleNamespace(schedule=[], selected_routes={})), \
             patch("harness_agent.domains.io.validate_standard_schedule", return_value=(list(errors), {"makespan": 12})), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = self.scope["run_bounded_smoke"](self.runtime)
            second = self.scope["run_bounded_smoke"](self.runtime)
        self.assertEqual(second, 3)
        self.assertEqual(called.call_count, 1)
        self.assertEqual((self.runtime / "smoke.used").read_text().strip(), "1")
        if solver_outcome != 0:
            self.assertNotIn("WORKER_SMOKE_SUMMARY", out.getvalue())
            return code, None
        line = next(line for line in out.getvalue().splitlines() if line.startswith("WORKER_SMOKE_SUMMARY "))
        self.assertLessEqual(len(line), 6000)
        return code, json.loads(line.split(" ", 1)[1])

    def test_huge_single_line_schedule_keeps_real_diagnostics(self):
        payload = {"makespan": 12, "schedule": [{"unused": "x" * 1000}] * 5000,
                   "diagnostics": {"cp_sat_called": True, "solver_status": "FEASIBLE", "model_size": {"variables": 50},
                                   "activation": {"alternative_path": {"route_switch_moves_evaluated": 17}}}}
        code, summary = self.run_smoke(payload)
        self.assertEqual(code, 0)
        self.assertEqual(summary["smoke_status"], "validated")
        self.assertEqual(summary["reported"]["diagnostics.activation.alternative_path.route_switch_moves_evaluated"], 17)
        self.assertEqual(summary["reported"]["diagnostics.model_size.variables"], 50)
        self.assertNotIn("schedule", str(summary))

    def test_oversized_diagnostics_hard_limit_and_priority_fields(self):
        diag = {"noise" + str(i): "z" * 200 for i in range(5000)}
        diag.update({"status": "UNKNOWN", "model_size": {"constraints": 90},
                     "activation": {"alternative_path": {"route_configurations_evaluated": 2}}})
        code, summary = self.run_smoke({"makespan": 12, "diagnostics": diag}, schema_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(summary["smoke_status"], "schema_only")
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["reported"]["diagnostics.status"], "UNKNOWN")
        self.assertEqual(summary["reported"]["diagnostics.activation.alternative_path.route_configurations_evaluated"], 2)

    def test_validation_error_unchanged_and_missing_counts_not_invented(self):
        code, summary = self.run_smoke({"makespan": 12}, errors=["selected-route mismatch"])
        self.assertEqual(code, 4)
        self.assertEqual(summary["error"], "selected-route mismatch")
        self.assertEqual(summary["smoke_status"], "rejected")
        self.assertFalse(any("activation" in key for key in summary["reported"]))

    def test_unicode_noise_does_not_hide_status_model_or_custom_activation(self):
        diag = {"noise" + str(i): "\U0001f600" * 10000 for i in range(1000)}
        diag.update({"solver_status": "FEASIBLE", "error": "真实错误",
                     "model_size": {"variables": 42},
                     "activation": {"custom_method": {"moves_evaluated": 37}}})
        code, summary = self.run_smoke({"makespan": 12, "diagnostics": diag})
        self.assertEqual(code, 0)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["reported"]["diagnostics.error"], "真实错误")
        self.assertEqual(summary["reported"]["diagnostics.solver_status"], "FEASIBLE")
        self.assertEqual(summary["reported"]["diagnostics.model_size.variables"], 42)
        self.assertEqual(summary["reported"]["diagnostics.activation.custom_method.moves_evaluated"], 37)

    def test_oversized_unicode_validation_error_stays_bounded(self):
        error = "\U0001f600" * 10000
        code, summary = self.run_smoke({"makespan": 12}, errors=[error])
        self.assertEqual(code, 4)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["error"], error[:256])

    def test_invalid_top_level_shape_still_rejected(self):
        code, summary = self.run_smoke([])
        self.assertEqual(code, 4)
        self.assertEqual(summary["error"], "solution must be a JSON object")
        self.assertEqual(summary["reported"], {})

    def test_objective_mismatch_still_rejected(self):
        code, summary = self.run_smoke({"makespan": 99})
        self.assertEqual(code, 4)
        self.assertIn("declared makespan mismatch", summary["error"])
        self.assertEqual(summary["reported"]["makespan"], 99)

    def test_solver_runtime_failure_preserves_exit_and_budget(self):
        code, _ = self.run_smoke({}, solver_outcome=7)
        self.assertEqual(code, 7)

    def test_solver_timeout_preserves_exit_and_budget(self):
        code, _ = self.run_smoke({}, solver_outcome=subprocess.TimeoutExpired("mock", 5))
        self.assertEqual(code, 124)


if __name__ == "__main__":
    unittest.main()
