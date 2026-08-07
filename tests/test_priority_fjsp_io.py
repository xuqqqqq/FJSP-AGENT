from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import (
    ScheduleRecord,
    parse_standard_fjsp,
    validate_standard_schedule,
)


ROOT = Path(__file__).resolve().parents[1]
PRIORITY_ROOT = ROOT / "ALL-Input-Information" / "11-priority-FJSP" / "11-Instances"
MT10C1 = PRIORITY_ROOT / "fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722.txt"


class PriorityFjspIoTests(unittest.TestCase):
    def test_parse_standard_fjsp_reads_priority_tail(self) -> None:
        instance = parse_standard_fjsp(MT10C1)

        self.assertEqual("fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722", instance.name)
        self.assertEqual(10, instance.job_count)
        self.assertEqual(11, instance.machine_count)
        self.assertEqual(100, instance.operation_count)
        self.assertTrue(instance.has_job_priority)
        self.assertEqual((1, 6, 8), instance.priority_job_ids)
        self.assertFalse(instance.has_sequence_dependent_setup)
        self.assertFalse(instance.has_machine_availability)

    def test_validate_standard_schedule_reports_priority_metrics(self) -> None:
        instance = parse_standard_fjsp(MT10C1)
        schedule = _serial_first_candidate_schedule(instance)

        errors, metrics = validate_standard_schedule(instance, schedule)

        self.assertEqual([], errors)
        self.assertEqual(100.0, metrics["scheduled_operations"])
        self.assertEqual(100.0, metrics["operation_count"])
        self.assertIn("makespan", metrics)
        self.assertIn("priority_completion_time", metrics)
        self.assertEqual(3.0, metrics["priority_job_count"])
        self.assertLessEqual(metrics["priority_completion_time"], metrics["makespan"])

    def test_priority_tail_rejects_duplicate_or_unsorted_job_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_priority.fjs"
            path.write_text(
                "\n".join(
                    [
                        "8 2 1",
                        "1 1 0 3",
                        "1 1 0 4",
                        "1 1 1 5",
                        "1 1 1 6",
                        "1 1 0 7",
                        "1 1 0 8",
                        "1 1 1 9",
                        "1 1 1 10",
                        "2",
                        "2",
                        "2",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "priority job ids must be unique"):
                parse_standard_fjsp(path)

            path.write_text(
                "\n".join(
                    [
                        "8 2 1",
                        "1 1 0 3",
                        "1 1 0 4",
                        "1 1 1 5",
                        "1 1 1 6",
                        "1 1 0 7",
                        "1 1 0 8",
                        "1 1 1 9",
                        "1 1 1 10",
                        "2",
                        "5",
                        "1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "priority job ids must be sorted"):
                parse_standard_fjsp(path)

            path.write_text(
                "\n".join(
                    [
                        "4 2 1",
                        "1 1 0 3",
                        "1 1 0 4",
                        "1 1 1 5",
                        "1 1 1 6",
                        "1",
                        "99",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "priority job id out of range"):
                parse_standard_fjsp(path)


def _serial_first_candidate_schedule(instance) -> list[ScheduleRecord]:
    machine_available = [0 for _ in range(instance.machine_count)]
    job_ready = [0 for _ in range(instance.job_count)]
    schedule: list[ScheduleRecord] = []
    for job in instance.jobs:
        for operation in job.operations:
            candidate = operation.candidates[0]
            start = max(job_ready[job.job_id], machine_available[candidate.machine_id])
            end = start + candidate.duration
            record = ScheduleRecord(
                job_id=job.job_id,
                op_id=operation.op_id,
                machine_id=candidate.machine_id,
                start=start,
                end=end,
            )
            schedule.append(record)
            job_ready[job.job_id] = end
            machine_available[candidate.machine_id] = end
    return schedule


if __name__ == "__main__":
    unittest.main()
