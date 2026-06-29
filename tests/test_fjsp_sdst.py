from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.standard_fjsp import (
    ScheduleRecord,
    parse_standard_fjsp,
    setup_time_between,
    validate_standard_schedule,
)


ROOT = Path(__file__).resolve().parents[1]
SDST_TINY = ROOT / "examples" / "fjsp_sdst_fattahi_setup_01.fjs"
HUDATA_TINY = ROOT / "examples" / "fjsp_sdst_hudata_tiny.txt"


class FjspSdstTests(unittest.TestCase):
    def test_fattahi_sdst_parser_reads_setup_matrix(self) -> None:
        instance = parse_standard_fjsp(SDST_TINY)

        self.assertEqual("fjsp_sdst_fattahi_setup_01", instance.name)
        self.assertEqual(2, instance.job_count)
        self.assertEqual(2, instance.machine_count)
        self.assertEqual(4, instance.operation_count)
        self.assertTrue(instance.has_sequence_dependent_setup)
        self.assertEqual("operation_pair", instance.setup_time_kind)
        self.assertEqual(2, len(instance.setup_times))
        self.assertEqual(4, len(instance.setup_times[0]))
        self.assertEqual(6, setup_time_between(instance, 0, (0, 0), (0, 0)))
        self.assertEqual(4, setup_time_between(instance, 1, (1, 0), (1, 1)))

    def test_hudata_sdst_parser_reads_job_pair_setup_matrix(self) -> None:
        instance = parse_standard_fjsp(HUDATA_TINY)

        self.assertEqual(2, instance.job_count)
        self.assertEqual(2, instance.machine_count)
        self.assertEqual(3, instance.operation_count)
        self.assertEqual("job_pair", instance.setup_time_kind)
        self.assertEqual(7, setup_time_between(instance, 0, (0, 0), (1, 0)))
        self.assertEqual(13, setup_time_between(instance, 1, (1, 0), (0, 0)))

    def test_validator_requires_setup_gap_between_same_machine_operations(self) -> None:
        instance = parse_standard_fjsp(SDST_TINY)
        schedule = [
            ScheduleRecord(job_id=0, op_id=0, machine_id=0, start=0, end=25),
            ScheduleRecord(job_id=1, op_id=0, machine_id=0, start=25, end=70),
            ScheduleRecord(job_id=0, op_id=1, machine_id=1, start=25, end=49),
            ScheduleRecord(job_id=1, op_id=1, machine_id=1, start=70, end=135),
        ]

        errors, metrics = validate_standard_schedule(instance, schedule)

        self.assertGreater(metrics["setup_time"], 0)
        self.assertTrue(any("setup violation" in error for error in errors))

    def test_portfolio_solver_generates_valid_sdst_solution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            solution = Path(tmp) / "solution.json"
            metrics = Path(tmp) / "metrics.json"

            subprocess.run(
                [
                    sys.executable,
                    "examples/standard_fjsp_portfolio_solver.py",
                    "--input",
                    str(SDST_TINY),
                    "--output",
                    str(solution),
                    "--seed",
                    "0",
                    "--portfolio-size",
                    "8",
                ],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    "examples/standard_fjsp_evaluator.py",
                    "--instance",
                    str(SDST_TINY),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
                cwd=ROOT,
                check=True,
            )

            payload = json.loads(metrics.read_text(encoding="utf-8"))
            solution_payload = json.loads(solution.read_text(encoding="utf-8"))

        self.assertTrue(payload["valid"], payload["errors"])
        self.assertEqual("fjsp_sdst", solution_payload["variant"])
        self.assertEqual("implicit_by_evaluator", solution_payload["setup_time_policy"])
        self.assertIn("setup_time", payload["metrics"])
        self.assertIn("setup_count", payload["metrics"])


if __name__ == "__main__":
    unittest.main()
