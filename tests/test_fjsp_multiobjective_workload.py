from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule
from harness_agent.orchestration.standard import fixed_problem_contract
from harness_agent.web.server import inspect_instance_profile, method_package_features


ROOT = Path(__file__).resolve().parents[1]


class MultiobjectiveWorkloadFjspTests(unittest.TestCase):
    def test_parser_and_validator_recompute_workload_objectives(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tiny.mofjsp.txt"
            path.write_text("2 2 2\n1 2 0 3 1 5\n1 2 0 4 1 2\n", encoding="utf-8")
            instance = parse_standard_fjsp(path)

        self.assertEqual("fjsp_multiobjective_workload", instance.variant)
        self.assertTrue(instance.has_workload_objectives)
        errors, metrics = validate_standard_schedule(
            instance,
            [ScheduleRecord(0, 0, 0, 0, 3), ScheduleRecord(1, 0, 1, 0, 2)],
        )
        self.assertFalse(errors)
        self.assertEqual(3.0, metrics["makespan"])
        self.assertEqual(3.0, metrics["max_machine_workload"])
        self.assertEqual(5.0, metrics["total_workload"])

    def test_fixed_contract_and_web_profile_expose_three_objectives(self) -> None:
        path = ROOT / "examples" / "fjsp.brandimarte.Mk01.m6j10c3.mofjsp.txt"
        family, evaluator, objectives = fixed_problem_contract([path])
        self.assertEqual("FJSP", family)
        self.assertEqual("examples/fjsp_multiobjective_workload_evaluator.py", evaluator)
        self.assertEqual(
            ["makespan", "max_machine_workload", "total_workload"],
            [item["name"] for item in objectives],
        )
        profile = inspect_instance_profile(path)
        self.assertEqual("fjsp_multiobjective_workload", profile["variant"])
        self.assertEqual(["makespan", "max_machine_workload", "total_workload"], profile["objective_names"])
        self.assertEqual(["multiobjective_workload"], method_package_features(profile))

    def test_evaluator_rejects_stale_declared_workload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance_path = root / "tiny.mofjsp.txt"
            solution_path = root / "solution.json"
            metrics_path = root / "metrics.json"
            instance_path.write_text("2 2 2\n1 2 0 3 1 5\n1 2 0 4 1 2\n", encoding="utf-8")
            solution = {
                "format": "standard_fjsp_schedule_v1",
                "makespan": 3,
                "max_machine_workload": 3,
                "total_workload": 5,
                "schedule": [
                    {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 0, "end": 3},
                    {"job_id": 1, "op_id": 0, "machine_id": 1, "start": 0, "end": 2},
                ],
            }
            solution_path.write_text(json.dumps(solution), encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "examples" / "fjsp_multiobjective_workload_evaluator.py"),
                "--instance",
                str(instance_path),
                "--solution",
                str(solution_path),
                "--metrics",
                str(metrics_path),
            ]
            subprocess.run(command, cwd=ROOT, check=True)
            accepted = json.loads(metrics_path.read_text(encoding="utf-8"))
            solution["total_workload"] = 4
            solution_path.write_text(json.dumps(solution), encoding="utf-8")
            subprocess.run(command, cwd=ROOT, check=True)
            rejected = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertTrue(accepted["valid"])
        self.assertFalse(rejected["valid"])
        self.assertTrue(any("declared total_workload mismatch" in item for item in rejected["errors"]))


if __name__ == "__main__":
    unittest.main()
