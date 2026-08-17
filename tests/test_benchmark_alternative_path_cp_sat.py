from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import load_solution_document, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "benchmark_alternative_path_cp_sat.py"
TINY = ROOT / "examples" / "fjsp_alternative_path_tiny.apfjsp"
EVALUATOR = ROOT / "examples" / "fjsp_alternative_path_evaluator.py"


class AlternativePathCpSatBenchmarkTests(unittest.TestCase):
    def test_benchmark_emits_evaluator_compatible_selected_routes_solution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution_path = tmp_path / "solution.json"
            diagnostics_path = tmp_path / "diagnostics.json"
            metrics_path = tmp_path / "metrics.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--input",
                    str(TINY),
                    "--output",
                    str(solution_path),
                    "--diagnostics",
                    str(diagnostics_path),
                    "--time-limit-sec",
                    "5",
                    "--workers",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

            solution = load_solution_document(solution_path)
            instance = parse_standard_fjsp(TINY)
            errors, metrics = validate_standard_schedule(
                instance,
                solution.schedule,
                selected_routes=solution.selected_routes,
            )
            self.assertEqual([], errors)
            self.assertEqual({0: 1, 1: 1}, solution.selected_routes)
            self.assertEqual(5.0, metrics["makespan"])
            self.assertEqual(2.0, metrics["selected_alternative_route_count"])

            payload = json.loads(solution_path.read_text(encoding="utf-8"))
            self.assertEqual("standard_fjsp_schedule_v1", payload["format"])
            self.assertEqual({"0": 1, "1": 1}, payload["selected_routes"])
            self.assertTrue(payload["diagnostics"]["cp_sat_called"])
            self.assertGreater(
                payload["diagnostics"]["solver_evidence"]["route_one_hot_constraints_posted"],
                0,
            )
            self.assertGreater(
                payload["diagnostics"]["solver_evidence"]["route_optional_intervals_posted"],
                0,
            )
            self.assertGreater(
                payload["diagnostics"]["solver_evidence"][
                    "route_conditional_precedences_posted"
                ],
                0,
            )

            evaluator = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR),
                    "--instance",
                    str(TINY),
                    "--solution",
                    str(solution_path),
                    "--metrics",
                    str(metrics_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            evaluator_payload = json.loads(metrics_path.read_text(encoding="utf-8"))

            self.assertEqual(0, evaluator.returncode, evaluator.stderr)
            self.assertTrue(evaluator_payload["valid"], evaluator_payload["errors"])


if __name__ == "__main__":
    unittest.main()
