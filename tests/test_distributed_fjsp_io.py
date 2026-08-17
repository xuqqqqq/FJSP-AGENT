from __future__ import annotations

import unittest
from pathlib import Path

from harness_agent.domains.distributed_fjsp import (
    DistributedFjspInstance,
    DistributedJob,
    DistributedMachineOption,
    DistributedOperation,
    DistributedScheduleRecord,
    parse_distributed_fjsp,
    validate_distributed_schedule,
)


ROOT = Path(__file__).resolve().parents[1]


class DistributedFjspIoTests(unittest.TestCase):
    def test_worker_contract_requires_global_ids_and_full_row_consumption(self) -> None:
        io_doc = (
            ROOT / "docs" / "variants" / "distributed_transfer" / "fjsp_distributed_transfer_io.md"
        ).read_text(encoding="utf-8")
        skill = (
            ROOT / ".codex" / "skills" / "fjsp-distributed-transfer-adapter-worker" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("全局", io_doc)
        self.assertNotIn("每工厂内独立编号", io_doc)
        self.assertIn("完整消耗整行作业数据", skill)
        self.assertIn("先比较工厂 ID，再比较机器 ID", skill)

    def test_validator_disambiguates_duplicate_resource_options_by_duration(self) -> None:
        operation = DistributedOperation(
            0,
            0,
            (
                DistributedMachineOption(1, 51, 10, 4),
                DistributedMachineOption(1, 51, 5, 2),
            ),
        )
        instance = DistributedFjspInstance(
            name="duplicate-resource",
            source_id="unit",
            job_count=1,
            factory_count=2,
            machines_per_factory=30,
            min_machines_per_operation_per_factory=1,
            max_machines_per_operation_per_factory=2,
            jobs=(DistributedJob(0, (operation,)),),
        )

        errors_long, metrics_long = validate_distributed_schedule(
            instance,
            [DistributedScheduleRecord(0, 0, 1, 51, 0, 10)],
        )
        errors_short, metrics_short = validate_distributed_schedule(
            instance,
            [DistributedScheduleRecord(0, 0, 1, 51, 0, 5)],
        )

        self.assertEqual([], errors_long)
        self.assertEqual(40.0, metrics_long["total_energy_consumption"])
        self.assertEqual([], errors_short)
        self.assertEqual(10.0, metrics_short["total_energy_consumption"])

    def test_parser_restores_factory_groups_and_validator_scores_three_objectives(self) -> None:
        instance = parse_distributed_fjsp(ROOT / "examples" / "fjsp_distributed_transfer_tiny.txt")
        self.assertEqual(2, instance.factory_count)
        self.assertEqual(2, instance.operation_count)
        self.assertEqual({0, 1}, {item.factory_id for item in instance.jobs[0].operations[0].candidates})
        errors, metrics = validate_distributed_schedule(
            instance,
            [
                DistributedScheduleRecord(0, 0, 0, 0, 0, 3),
                DistributedScheduleRecord(0, 1, 1, 1, 63, 68),
            ],
        )
        self.assertEqual([], errors)
        self.assertEqual(68.0, metrics["makespan"])
        self.assertEqual(5.0, metrics["max_factory_workload"])
        self.assertEqual(3 * 2 + 5 * 4 + 60 * 6, metrics["total_energy_consumption"])

    def test_validator_rejects_missing_transfer_wait(self) -> None:
        instance = parse_distributed_fjsp(ROOT / "examples" / "fjsp_distributed_transfer_tiny.txt")
        errors, _ = validate_distributed_schedule(
            instance,
            [
                DistributedScheduleRecord(0, 0, 0, 0, 0, 3),
                DistributedScheduleRecord(0, 1, 1, 1, 3, 8),
            ],
        )
        self.assertTrue(any("transfer/precedence violation" in item for item in errors))

    def test_real_dfm01_preserves_global_machine_ids(self) -> None:
        instance = parse_distributed_fjsp(
            ROOT / "examples" / "variant_instances" / "DFM01_10x2x6.txt"
        )

        self.assertEqual(50, instance.operation_count)
        self.assertEqual(
            {(0, 4), (1, 5)},
            {
                (option.factory_id, option.machine_id)
                for option in instance.jobs[0].operations[0].candidates
            },
        )


if __name__ == "__main__":
    unittest.main()
