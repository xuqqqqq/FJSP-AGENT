from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import (
    CROSS_FACTORY_TRANSFER_TIME,
    DistributedFjspInstance,
    DistributedJob,
    DistributedMachineOption,
    DistributedOperation,
    DistributedScheduleRecord,
    SAME_FACTORY_TRANSFER_TIME,
    TRANSFER_UNIT_ENERGY,
    load_distributed_solution,
    parse_distributed_fjsp,
    validate_distributed_schedule,
)


ROOT = Path(__file__).resolve().parents[1]
DFJSPT_ROOT = ROOT / "ALL-Input-Information" / "10-distributed-FJSP" / "10-Instance"
DFM01 = DFJSPT_ROOT / "small size" / "DFM01_10x2x6.txt"
DFM18 = DFJSPT_ROOT / "middle size" / "DFM18_10x4x8.txt"


class DistributedFjspIoTests(unittest.TestCase):
    def test_parse_distributed_fjsp_reads_header_and_transfer_constants(self) -> None:
        instance = parse_distributed_fjsp(DFM01)

        self.assertEqual("DFM01_10x2x6", instance.name)
        self.assertEqual("la0110x5", instance.source_id)
        self.assertEqual(10, instance.job_count)
        self.assertEqual(2, instance.factory_count)
        self.assertEqual(6, instance.machines_per_factory)
        self.assertEqual(1, instance.min_machines_per_operation_per_factory)
        self.assertEqual(2, instance.max_machines_per_operation_per_factory)
        self.assertEqual(50, instance.operation_count)
        self.assertEqual(SAME_FACTORY_TRANSFER_TIME, instance.same_factory_transfer_time)
        self.assertEqual(CROSS_FACTORY_TRANSFER_TIME, instance.cross_factory_transfer_time)
        self.assertEqual(TRANSFER_UNIT_ENERGY, instance.transfer_unit_energy)

    def test_parse_distributed_fjsp_handles_omitted_factory_id_candidates(self) -> None:
        instance = parse_distributed_fjsp(DFM01)

        op1 = instance.jobs[0].operations[1]
        self.assertEqual(
            [
                (0, 2, 46, 4),
                (0, 3, 46, 5),
                (1, 7, 43, 9),
            ],
            [
                (candidate.factory_id, candidate.machine_id, candidate.duration, candidate.unit_energy)
                for candidate in op1.candidates
            ],
        )

    def test_parse_distributed_fjsp_uses_machine_upper_bound_to_resolve_ambiguous_groups(self) -> None:
        instance = parse_distributed_fjsp(DFM18)

        first_op = instance.jobs[0].operations[0]
        self.assertEqual(10, len(first_op.candidates))
        self.assertEqual(
            [
                (0, 0, 62, 2),
                (0, 1, 57, 14),
                (1, 8, 41, 3),
                (1, 12, 48, 5),
                (2, 15, 57, 15),
                (2, 17, 50, 10),
                (2, 18, 45, 19),
                (3, 22, 65, 5),
                (3, 23, 55, 15),
                (3, 25, 47, 4),
            ],
            [
                (candidate.factory_id, candidate.machine_id, candidate.duration, candidate.unit_energy)
                for candidate in first_op.candidates
            ],
        )

    def test_parse_distributed_fjsp_loads_all_dfm_instances(self) -> None:
        paths = sorted(DFJSPT_ROOT.glob("* size/DFM*.txt"))
        self.assertEqual(40, len(paths))

        for path in paths:
            with self.subTest(path=path.name):
                instance = parse_distributed_fjsp(path)
                self.assertGreater(instance.operation_count, 0)
                self.assertGreater(instance.max_candidate_count, 0)
                self.assertEqual(instance.job_count, len(instance.jobs))
                self.assertLessEqual(
                    max(
                        candidate.machine_id
                        for job in instance.jobs
                        for operation in job.operations
                        for candidate in operation.candidates
                    ),
                    instance.machine_count - 1,
                )


class DistributedFjspValidationTests(unittest.TestCase):
    def test_validate_distributed_schedule_accepts_complete_transfer_aware_solution(self) -> None:
        instance = _tiny_distributed_instance()
        schedule = [
            DistributedScheduleRecord(job_id=0, op_id=0, factory_id=0, machine_id=0, start=0, end=10),
            DistributedScheduleRecord(job_id=1, op_id=0, factory_id=0, machine_id=0, start=10, end=22),
            DistributedScheduleRecord(job_id=0, op_id=1, factory_id=0, machine_id=1, start=40, end=47),
            DistributedScheduleRecord(job_id=1, op_id=1, factory_id=1, machine_id=2, start=82, end=91),
        ]

        errors, metrics = validate_distributed_schedule(instance, schedule)

        self.assertEqual([], errors)
        self.assertEqual(91.0, metrics["makespan"])
        self.assertEqual(29.0, metrics["max_factory_workload"])
        self.assertEqual(2.0, metrics["transfer_count"])
        self.assertEqual(90.0, metrics["transfer_time_total"])
        self.assertEqual(10 * 2 + 7 * 3 + 12 * 4 + 9 * 5 + 90 * TRANSFER_UNIT_ENERGY, metrics["total_energy_consumption"])
        self.assertEqual(4.0, metrics["scheduled_operations"])
        self.assertEqual(4.0, metrics["operation_count"])

    def test_validate_distributed_schedule_reports_variant_specific_errors(self) -> None:
        instance = _tiny_distributed_instance()
        schedule = [
            DistributedScheduleRecord(job_id=0, op_id=0, factory_id=0, machine_id=0, start=0, end=10),
            DistributedScheduleRecord(job_id=1, op_id=0, factory_id=0, machine_id=0, start=5, end=17),
            DistributedScheduleRecord(job_id=0, op_id=1, factory_id=0, machine_id=1, start=25, end=32),
            DistributedScheduleRecord(job_id=1, op_id=1, factory_id=2, machine_id=1, start=80, end=89),
        ]

        errors, metrics = validate_distributed_schedule(instance, schedule)

        self.assertTrue(any("machine overlap violation" in error for error in errors))
        self.assertTrue(any("transfer/precedence violation" in error for error in errors))
        self.assertTrue(any("factory id out of range" in error for error in errors))
        self.assertTrue(any("factory-machine pair is not a candidate" in error for error in errors))
        self.assertEqual(90.0, metrics["transfer_time_total"])

    def test_load_distributed_solution_requires_factory_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "solution.json"
            path.write_text(
                json.dumps(
                    {
                        "schedule": [
                            {
                                "job_id": 0,
                                "op_id": 0,
                                "machine_id": 0,
                                "start": 0,
                                "end": 10,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "malformed"):
                load_distributed_solution(path)


def _tiny_distributed_instance() -> DistributedFjspInstance:
    return DistributedFjspInstance(
        name="tiny_dfjspt",
        source_id="tiny",
        job_count=2,
        factory_count=2,
        machines_per_factory=3,
        min_machines_per_operation_per_factory=1,
        max_machines_per_operation_per_factory=2,
        jobs=(
            DistributedJob(
                job_id=0,
                operations=(
                    DistributedOperation(
                        job_id=0,
                        op_id=0,
                        candidates=(
                            DistributedMachineOption(factory_id=0, machine_id=0, duration=10, unit_energy=2),
                        ),
                    ),
                    DistributedOperation(
                        job_id=0,
                        op_id=1,
                        candidates=(
                            DistributedMachineOption(factory_id=0, machine_id=1, duration=7, unit_energy=3),
                        ),
                    ),
                ),
            ),
            DistributedJob(
                job_id=1,
                operations=(
                    DistributedOperation(
                        job_id=1,
                        op_id=0,
                        candidates=(
                            DistributedMachineOption(factory_id=0, machine_id=0, duration=12, unit_energy=4),
                        ),
                    ),
                    DistributedOperation(
                        job_id=1,
                        op_id=1,
                        candidates=(
                            DistributedMachineOption(factory_id=1, machine_id=2, duration=9, unit_energy=5),
                        ),
                    ),
                ),
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
