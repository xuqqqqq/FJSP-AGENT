from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.fjsp_distributed_transfer_evaluator import main
from harness_agent.domains.io import parse_distributed_fjsp


ROOT = Path(__file__).resolve().parents[1]
DFM01 = (
    ROOT
    / "ALL-Input-Information"
    / "10-distributed-FJSP"
    / "10-Instance"
    / "small size"
    / "DFM01_10x2x6.txt"
)


class DistributedFjspEvaluatorTests(unittest.TestCase):
    def test_distributed_evaluator_accepts_valid_greedy_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            _write_serial_greedy_solution(DFM01, solution)

            with patch.object(
                sys,
                "argv",
                [
                    "fjsp_distributed_transfer_evaluator.py",
                    "--instance",
                    str(DFM01),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
            ):
                self.assertEqual(0, main())

            payload = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertTrue(payload["valid"])
            self.assertEqual(0, payload["error_count"])
            self.assertEqual(50.0, payload["metrics"]["operation_count"])
            self.assertEqual(50.0, payload["metrics"]["scheduled_operations"])
            self.assertIn("makespan", payload["metrics"])
            self.assertIn("total_energy_consumption", payload["metrics"])

    def test_distributed_evaluator_reports_invalid_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps(
                    {
                        "format": "standard_fjsp_schedule_v1",
                        "variant": "fjsp_distributed_transfer",
                        "schedule": [
                            {
                                "job_id": 0,
                                "op_id": 0,
                                "factory_id": 99,
                                "machine_id": 99,
                                "start": 0,
                                "end": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                sys,
                "argv",
                [
                    "fjsp_distributed_transfer_evaluator.py",
                    "--instance",
                    str(DFM01),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
            ):
                self.assertEqual(0, main())

            payload = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertFalse(payload["valid"])
            self.assertGreater(payload["error_count"], 0)
            joined_errors = "\n".join(payload["errors"])
            self.assertIn("operation count mismatch", joined_errors)
            self.assertIn("factory id out of range", joined_errors)
            self.assertIn("factory-machine pair is not a candidate", joined_errors)


def _write_serial_greedy_solution(instance_path: Path, solution_path: Path) -> None:
    instance = parse_distributed_fjsp(instance_path)
    machine_available: dict[tuple[int, int], int] = {}
    job_ready = [0 for _ in range(instance.job_count)]
    previous_record_by_job: dict[int, dict[str, int]] = {}
    schedule: list[dict[str, int]] = []

    for job in instance.jobs:
        for operation in job.operations:
            best: tuple[int, int, dict[str, int]] | None = None
            for candidate in operation.candidates:
                previous = previous_record_by_job.get(job.job_id)
                transfer_time = 0
                if previous is not None:
                    if previous["factory_id"] != candidate.factory_id:
                        transfer_time = instance.cross_factory_transfer_time
                    elif previous["machine_id"] != candidate.machine_id:
                        transfer_time = instance.same_factory_transfer_time
                resource = (candidate.factory_id, candidate.machine_id)
                start = max(
                    job_ready[job.job_id] + transfer_time,
                    machine_available.get(resource, 0),
                )
                end = start + candidate.duration
                record = {
                    "job_id": job.job_id,
                    "op_id": operation.op_id,
                    "factory_id": candidate.factory_id,
                    "machine_id": candidate.machine_id,
                    "start": start,
                    "end": end,
                }
                score = (end, candidate.duration)
                if best is None or score < best[:2]:
                    best = (*score, record)
            assert best is not None
            record = best[2]
            schedule.append(record)
            machine_available[(record["factory_id"], record["machine_id"])] = record["end"]
            job_ready[job.job_id] = record["end"]
            previous_record_by_job[job.job_id] = record

    payload = {
        "format": "standard_fjsp_schedule_v1",
        "variant": "fjsp_distributed_transfer",
        "instance": instance.name,
        "strategy": "serial_greedy_test_fixture",
        "makespan": max(record["end"] for record in schedule),
        "schedule": schedule,
    }
    solution_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
