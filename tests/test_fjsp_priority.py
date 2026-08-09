from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(
    r"C:\Users\ASUS\Downloads\ALL-Input-Information\ALL-Input-Information\11-priority-FJSP\11-Instances"
)


class PriorityFjspTests(unittest.TestCase):
    def test_parser_and_validator_recompute_priority_completion(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "fjsp_priority_tiny.priority.txt")
        self.assertEqual("fjsp_priority", instance.variant)
        self.assertEqual((0,), instance.priority_job_ids)
        self.assertTrue(instance.has_job_priorities)

        errors, metrics = validate_standard_schedule(
            instance,
            [ScheduleRecord(0, 0, 0, 0, 3), ScheduleRecord(1, 0, 1, 0, 4)],
        )
        self.assertFalse(errors)
        self.assertEqual(4.0, metrics["makespan"])
        self.assertEqual(3.0, metrics["priority_completion_time"])
        self.assertEqual(1.0, metrics["priority_job_count"])

    def test_priority_marker_adds_no_hard_scheduling_constraint(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "fjsp_priority_tiny.priority.txt")
        errors, _ = validate_standard_schedule(
            instance,
            [ScheduleRecord(0, 0, 0, 10, 13), ScheduleRecord(1, 0, 1, 0, 4)],
        )
        self.assertFalse(errors)

    def test_priority_tail_rejects_malformed_encodings(self) -> None:
        cases = {
            "missing": "",
            "wrong_count": "1\n0\n",
            "unsorted": "2\n3\n1\n",
            "duplicate": "2\n1\n1\n",
            "out_of_range": "2\n1\n8\n",
            "trailing": "2\n1\n3\n5\n",
        }
        body = "8 1 1\n" + "\n".join("1 1 0 1" for _ in range(8)) + "\n"
        for label, tail in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / f"case.{label}.priority.txt"
                path.write_text(body + tail, encoding="utf-8")
                with self.assertRaises(ValueError):
                    parse_standard_fjsp(path)

    def test_real_barnes_priority_instance(self) -> None:
        path = SOURCE_ROOT / "fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722.txt"
        if not path.exists():
            self.skipTest("priority benchmark source directory is unavailable")
        instance = parse_standard_fjsp(path)
        self.assertEqual("fjsp_priority", instance.variant)
        self.assertEqual(10, instance.job_count)
        self.assertEqual((1, 6, 8), instance.priority_job_ids)


if __name__ == "__main__":
    unittest.main()
