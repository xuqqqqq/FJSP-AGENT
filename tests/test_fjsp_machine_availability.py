from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]


class MachineAvailabilityFjspTests(unittest.TestCase):
    def test_parser_and_validator_enforce_half_open_maintenance_windows(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "FFCR_tiny.txt")
        self.assertEqual("fjsp_machine_availability", instance.variant)
        self.assertEqual(1, len(instance.unavailability_intervals))
        errors, metrics = validate_standard_schedule(
            instance,
            [ScheduleRecord(0, 0, 0, 0, 3), ScheduleRecord(1, 0, 1, 0, 4)],
        )
        self.assertTrue(any("machine availability violation" in item for item in errors))
        self.assertEqual(1.0, metrics["machine_availability_violations"])
        self.assertEqual(4.0, metrics["total_unavailable_duration"])

    def test_half_open_boundaries_are_legal_but_intersection_is_not(self) -> None:
        instance = self._parse_instance(processing_time=3, windows=[(0, 3, 5)])

        before_errors, _ = validate_standard_schedule(
            instance, [ScheduleRecord(0, 0, 0, 0, 3)]
        )
        after_errors, _ = validate_standard_schedule(
            instance, [ScheduleRecord(0, 0, 0, 5, 8)]
        )
        overlap_errors, overlap_metrics = validate_standard_schedule(
            instance, [ScheduleRecord(0, 0, 0, 2, 5)]
        )

        self.assertFalse(before_errors)
        self.assertFalse(after_errors)
        self.assertTrue(any("machine availability violation" in item for item in overlap_errors))
        self.assertEqual(1.0, overlap_metrics["machine_availability_violations"])

    def test_each_original_overlapping_window_is_checked(self) -> None:
        instance = self._parse_instance(
            processing_time=3,
            windows=[(0, 1, 4), (0, 3, 6)],
        )
        errors, metrics = validate_standard_schedule(
            instance, [ScheduleRecord(0, 0, 0, 2, 5)]
        )
        boundary_errors, _ = validate_standard_schedule(
            instance, [ScheduleRecord(0, 0, 0, 6, 9)]
        )

        self.assertEqual(2, sum("machine availability violation" in item for item in errors))
        self.assertEqual(2.0, metrics["machine_availability_violations"])
        self.assertFalse(boundary_errors)

    def _parse_instance(self, *, processing_time: int, windows: list[tuple[int, int, int]]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "FFCR_test.txt"
        tail = "\n".join(f"{machine} {start} {end}" for machine, start, end in windows)
        path.write_text(
            f"1 1 1\n1 1 0 {processing_time}\n{len(windows)}\n{tail}\n",
            encoding="utf-8",
        )
        return parse_standard_fjsp(path)


if __name__ == "__main__":
    unittest.main()
