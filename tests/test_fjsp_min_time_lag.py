from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "examples" / "fjsp_min_time_lag_tiny.mitfjsp"


class FjspMinimumTimeLagTests(unittest.TestCase):
    def test_parser_reads_adjacent_minimum_time_lag_constraints(self) -> None:
        instance = parse_standard_fjsp(TINY)

        self.assertEqual("fjsp_min_time_lag_tiny", instance.name)
        self.assertFalse(instance.has_sequence_dependent_setup)
        self.assertTrue(instance.has_minimum_time_lags)
        self.assertEqual(1, len(instance.minimum_time_lags))
        constraint = instance.minimum_time_lags[0]
        self.assertEqual((0, 0, 1, 5), (constraint.job_id, constraint.from_op, constraint.to_op, constraint.lag))

    def test_validator_accepts_schedule_at_minimum_gap(self) -> None:
        instance = parse_standard_fjsp(TINY)
        schedule = self._schedule(successor_start=8)

        errors, metrics = validate_standard_schedule(instance, schedule)

        self.assertEqual([], errors)
        self.assertEqual(1.0, metrics["min_time_lag_constraints"])
        self.assertEqual(0.0, metrics["min_time_lag_violations"])

    def test_validator_rejects_schedule_below_minimum_gap(self) -> None:
        instance = parse_standard_fjsp(TINY)
        schedule = self._schedule(successor_start=7)

        errors, metrics = validate_standard_schedule(instance, schedule)

        self.assertEqual(1.0, metrics["min_time_lag_violations"])
        self.assertTrue(any("minimum time-lag violation" in error for error in errors))
        self.assertFalse(any("precedence violation" in error for error in errors))

    def test_parser_rejects_non_adjacent_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.mitfjsp"
            path.write_text("1 1 1\n3 1 0 1 1 0 1 1 0 1\n1\n0 0 2 1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "adjacent operations"):
                parse_standard_fjsp(path)

    def test_zero_constraint_tail_preserves_variant_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.mitfjsp"
            path.write_text("1 1 1\n1 1 0 1\n0\n", encoding="utf-8")

            instance = parse_standard_fjsp(path)

        self.assertEqual("fjsp_min_time_lag", instance.variant)
        self.assertTrue(instance.has_minimum_time_lags)
        self.assertEqual((), instance.minimum_time_lags)

    def test_zero_duration_zero_lag_operations_may_share_a_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zero_duration.mitfjsp"
            path.write_text(
                "1 1 1\n2 1 0 0 1 0 0\n1\n0 0 1 0\n",
                encoding="utf-8",
            )
            instance = parse_standard_fjsp(path)
            schedule = [
                ScheduleRecord(job_id=0, op_id=0, machine_id=0, start=0, end=0),
                ScheduleRecord(job_id=0, op_id=1, machine_id=0, start=0, end=0),
            ]

            errors, metrics = validate_standard_schedule(instance, schedule)

        self.assertEqual([], errors)
        self.assertEqual(0.0, metrics["min_time_lag_violations"])

    def test_evaluator_reports_variant_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps(
                    {
                        "format": "standard_fjsp_schedule_v1",
                        "variant": "fjsp_min_time_lag",
                        "schedule": [record.__dict__ for record in self._schedule(successor_start=8)],
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "standard_fjsp_evaluator.py"),
                    "--instance",
                    str(TINY),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(metrics.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(payload["valid"])
        self.assertEqual(0.0, payload["metrics"]["min_time_lag_violations"])

    @staticmethod
    def _schedule(*, successor_start: int) -> list[ScheduleRecord]:
        return [
            ScheduleRecord(job_id=0, op_id=0, machine_id=0, start=0, end=3),
            ScheduleRecord(job_id=0, op_id=1, machine_id=1, start=successor_start, end=successor_start + 2),
            ScheduleRecord(job_id=1, op_id=0, machine_id=1, start=10, end=14),
            ScheduleRecord(job_id=1, op_id=1, machine_id=0, start=14, end=15),
        ]


if __name__ == "__main__":
    unittest.main()
