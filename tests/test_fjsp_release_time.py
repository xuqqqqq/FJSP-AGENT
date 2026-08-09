from __future__ import annotations

import unittest
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]


class ReleaseTimeFjspTests(unittest.TestCase):
    def test_parser_and_validator_enforce_both_release_rows(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "fjsp_release_time_tiny.rtfjsp.txt")
        self.assertEqual("fjsp_release_time", instance.variant)
        self.assertEqual((5, 0), instance.job_release_times)
        self.assertEqual((0, 7), instance.machine_available_times)
        schedule = [
            ScheduleRecord(0, 0, 0, 0, 3),
            ScheduleRecord(1, 0, 1, 0, 4),
        ]
        errors, metrics = validate_standard_schedule(instance, schedule)
        self.assertTrue(any("job release-time violation" in item for item in errors))
        self.assertTrue(any("machine available-time violation" in item for item in errors))
        self.assertEqual(5.0, metrics["max_job_release_time"])
        self.assertEqual(7.0, metrics["max_machine_available_time"])


if __name__ == "__main__":
    unittest.main()
