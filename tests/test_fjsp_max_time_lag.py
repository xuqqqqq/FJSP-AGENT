from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "examples" / "fjsp_max_time_lag_tiny.tlfjsp"


def valid_schedule() -> list[ScheduleRecord]:
    return [
        ScheduleRecord(job_id=0, op_id=0, machine_id=0, start=0, end=2),
        ScheduleRecord(job_id=0, op_id=1, machine_id=1, start=2, end=4),
        ScheduleRecord(job_id=0, op_id=2, machine_id=0, start=4, end=6),
        ScheduleRecord(job_id=1, op_id=0, machine_id=1, start=4, end=7),
        ScheduleRecord(job_id=1, op_id=1, machine_id=0, start=7, end=8),
    ]


class MaximumTimeLagTests(unittest.TestCase):
    def test_method_packages_publish_canonical_activation_telemetry(self) -> None:
        package_root = ROOT / "knowledge" / "method_packages"
        expected = {
            "fjsp_max_time_lag_constructive_adaptation": {
                "diagnostics.activation.constructive_search.candidates_evaluated",
                "diagnostics.activation.maximum_time_lag.constructive_candidates_evaluated",
            },
            "fjsp_max_time_lag_coupled_local_search": {
                "diagnostics.activation.coupled_local_search.moves_evaluated",
                "diagnostics.activation.maximum_time_lag.moves_evaluated",
            },
            "fjsp_max_time_lag_exact_hybrid": {
                "diagnostics.cp_sat_called",
                "diagnostics.solver_evidence.max_lag_constraints_posted",
            },
        }
        for package_id, required_fields in expected.items():
            with self.subTest(package_id=package_id):
                contract = json.loads(
                    (package_root / package_id / "implementation_contract.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    required_fields,
                    set(contract["activation_evidence"]["required_fields"]),
                )

    def test_parser_reads_zero_and_non_adjacent_maximum_lags(self) -> None:
        instance = parse_standard_fjsp(TINY)

        self.assertEqual("fjsp_max_time_lag", instance.variant)
        self.assertTrue(instance.has_maximum_time_lags)
        self.assertEqual(
            [(0, 0, 1, 0), (0, 0, 2, 3)],
            [(item.job_id, item.from_op, item.to_op, item.lag) for item in instance.maximum_time_lags],
        )

    def test_exact_upper_bounds_are_valid(self) -> None:
        errors, metrics = validate_standard_schedule(parse_standard_fjsp(TINY), valid_schedule())

        self.assertEqual([], errors)
        self.assertEqual(2.0, metrics["max_time_lag_constraints"])
        self.assertEqual(0.0, metrics["max_time_lag_violations"])
        self.assertEqual(8.0, metrics["makespan"])

    def test_gap_above_upper_bound_is_invalid(self) -> None:
        schedule = valid_schedule()
        schedule[1] = ScheduleRecord(job_id=0, op_id=1, machine_id=1, start=3, end=5)
        schedule[2] = ScheduleRecord(job_id=0, op_id=2, machine_id=0, start=6, end=8)
        schedule[3] = ScheduleRecord(job_id=1, op_id=0, machine_id=1, start=5, end=8)
        schedule[4] = ScheduleRecord(job_id=1, op_id=1, machine_id=0, start=8, end=9)

        errors, metrics = validate_standard_schedule(parse_standard_fjsp(TINY), schedule)

        self.assertEqual(2.0, metrics["max_time_lag_violations"])
        self.assertTrue(any("maximum time-lag violation" in error for error in errors))

    def test_zero_count_tail_preserves_variant_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zero.tlfjsp"
            path.write_text("1 1 1\n1 1 0 2\n0\n", encoding="utf-8")

            instance = parse_standard_fjsp(path)

        self.assertEqual("fjsp_max_time_lag", instance.variant)
        self.assertTrue(instance.has_maximum_time_lags)
        self.assertEqual((), instance.maximum_time_lags)

    def test_invalid_maximum_lag_tails_are_rejected(self) -> None:
        bodies = {
            "negative": "1\n0 0 1 -1\n",
            "duplicate": "2\n0 0 1 1\n0 0 1 2\n",
            "reversed": "1\n0 1 0 1\n",
            "out_of_range": "1\n0 0 2 1\n",
        }
        prefix = "1 1 1\n2 1 0 2 1 0 3\n"
        with tempfile.TemporaryDirectory() as tmp:
            for name, tail in bodies.items():
                with self.subTest(name=name):
                    path = Path(tmp) / f"{name}.tlfjsp"
                    path.write_text(prefix + tail, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        parse_standard_fjsp(path)

    def test_standard_evaluator_reports_maximum_lag_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps(
                    {
                        "schedule": [
                            {
                                "job_id": item.job_id,
                                "op_id": item.op_id,
                                "machine_id": item.machine_id,
                                "start": item.start,
                                "end": item.end,
                            }
                            for item in valid_schedule()
                        ]
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
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(metrics.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(payload["valid"], payload["errors"])
        self.assertEqual(0.0, payload["metrics"]["max_time_lag_violations"])


if __name__ == "__main__":
    unittest.main()
