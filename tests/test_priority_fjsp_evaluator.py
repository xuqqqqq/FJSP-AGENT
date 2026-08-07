from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.fjsp_job_priority_evaluator import main
from harness_agent.domains.io import parse_standard_fjsp


ROOT = Path(__file__).resolve().parents[1]
PRIORITY_ROOT = ROOT / "ALL-Input-Information" / "11-priority-FJSP" / "11-Instances"
MT10C1 = PRIORITY_ROOT / "fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722.txt"
STANDARD_TINY = ROOT / "examples" / "standard_fjsp_tiny.fjs"


class PriorityFjspEvaluatorTests(unittest.TestCase):
    def test_priority_evaluator_accepts_valid_standard_schedule_and_reports_priority_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            _write_serial_first_candidate_solution(MT10C1, solution)

            with patch.object(
                sys,
                "argv",
                [
                    "fjsp_job_priority_evaluator.py",
                    "--instance",
                    str(MT10C1),
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
            self.assertEqual(100.0, payload["metrics"]["operation_count"])
            self.assertEqual(100.0, payload["metrics"]["scheduled_operations"])
            self.assertEqual(3.0, payload["metrics"]["priority_job_count"])
            self.assertIn("makespan", payload["metrics"])
            self.assertIn("priority_completion_time", payload["metrics"])

    def test_priority_evaluator_reports_invalid_standard_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps(
                    {
                        "format": "standard_fjsp_schedule_v1",
                        "variant": "fjsp_priority",
                        "schedule": [
                            {
                                "job_id": 0,
                                "op_id": 0,
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
                    "fjsp_job_priority_evaluator.py",
                    "--instance",
                    str(MT10C1),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
            ):
                self.assertEqual(0, main())

            payload = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertFalse(payload["valid"])
            joined_errors = "\n".join(payload["errors"])
            self.assertIn("operation count mismatch", joined_errors)
            self.assertIn("machine is not a candidate", joined_errors)

    def test_priority_evaluator_rejects_plain_standard_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(json.dumps({"schedule": []}), encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                [
                    "fjsp_job_priority_evaluator.py",
                    "--instance",
                    str(STANDARD_TINY),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
            ):
                self.assertEqual(0, main())

            payload = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertFalse(payload["valid"])
            self.assertIn("job-priority tail", payload["errors"][0])


def _write_serial_first_candidate_solution(instance_path: Path, solution_path: Path) -> None:
    instance = parse_standard_fjsp(instance_path)
    machine_available = [0 for _ in range(instance.machine_count)]
    job_ready = [0 for _ in range(instance.job_count)]
    schedule: list[dict[str, int]] = []
    completion_by_job = [0 for _ in range(instance.job_count)]

    for job in instance.jobs:
        for operation in job.operations:
            candidate = operation.candidates[0]
            start = max(job_ready[job.job_id], machine_available[candidate.machine_id])
            end = start + candidate.duration
            schedule.append(
                {
                    "job_id": job.job_id,
                    "op_id": operation.op_id,
                    "machine_id": candidate.machine_id,
                    "start": start,
                    "end": end,
                }
            )
            job_ready[job.job_id] = end
            machine_available[candidate.machine_id] = end
            completion_by_job[job.job_id] = end

    payload = {
        "format": "standard_fjsp_schedule_v1",
        "variant": "fjsp_priority",
        "instance": instance.name,
        "strategy": "serial_first_candidate_test_fixture",
        "makespan": max(completion_by_job, default=0),
        "priority_completion_time": max(
            (completion_by_job[job_id] for job_id in instance.priority_job_ids),
            default=0,
        ),
        "schedule": schedule,
    }
    solution_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
