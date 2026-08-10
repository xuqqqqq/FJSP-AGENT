from __future__ import annotations

import tempfile
import json
import subprocess
import sys
import unittest
from pathlib import Path

from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.context.worker import build_worker_assignment
from harness_agent.domains.pack import get_domain_pack
from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule
from harness_agent.web.server import inspect_instance_profile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(
    r"C:\Users\ASUS\Downloads\ALL-Input-Information\ALL-Input-Information\6-re_entrant-FJSP\6-Instance"
)


class ReentrantFjspTests(unittest.TestCase):
    def test_parser_expands_each_loop_pass_with_stable_operation_ids(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "fjsp_reentrant_tiny.rjsp.txt")

        self.assertEqual("fjsp_reentrant", instance.variant)
        self.assertTrue(instance.has_reentrant_routes)
        self.assertEqual(8, instance.original_operation_count)
        self.assertEqual(12, instance.operation_count)
        self.assertEqual((6, 6), tuple(len(job.operations) for job in instance.jobs))
        self.assertEqual((0, 1, 2, 3, 4, 5), tuple(op.op_id for op in instance.jobs[0].operations))
        self.assertEqual((1, 2, 2), (
            instance.reentrant_loops[0].loop_start,
            instance.reentrant_loops[0].loop_end,
            instance.reentrant_loops[0].repeat,
        ))
        self.assertEqual(
            (1, 0, 1, 0, 1),
            tuple(op.candidates[0].machine_id for op in instance.jobs[0].operations[1:]),
        )

    def test_validator_requires_every_expanded_operation(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "fjsp_reentrant_tiny.rjsp.txt")
        schedule: list[ScheduleRecord] = []
        now = 0
        for job in instance.jobs:
            for operation in job.operations:
                option = operation.candidates[0]
                schedule.append(
                    ScheduleRecord(job.job_id, operation.op_id, option.machine_id, now, now + option.duration)
                )
                now += option.duration

        errors, metrics = validate_standard_schedule(instance, schedule)
        self.assertFalse(errors)
        self.assertEqual(12.0, metrics["operation_count"])
        self.assertEqual(8.0, metrics["original_operation_count"])
        self.assertEqual(4.0, metrics["reentrant_added_operation_count"])

        errors, _ = validate_standard_schedule(instance, schedule[:-1])
        self.assertTrue(any("missing operation" in error for error in errors))

    def test_reentrant_tail_rejects_malformed_encodings(self) -> None:
        body = (
            "2 2 1\n"
            "4 1 0 2 1 1 3 1 0 2 1 1 1\n"
            "4 1 1 2 1 0 2 1 1 2 1 0 1\n"
        )
        cases = {
            "missing": "",
            "short": "1 2 2\n",
            "loop_starts_at_first_op": "0 2 2\n1 2 2\n",
            "loop_ends_at_last_op": "1 3 2\n1 2 2\n",
            "repeat_too_small": "1 2 1\n1 2 2\n",
            "trailing": "1 2 2\n1 2 2\n99\n",
        }
        for label, tail in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / f"case.{label}.rjsp.txt"
                path.write_text(body + tail, encoding="utf-8")
                with self.assertRaises(ValueError):
                    parse_standard_fjsp(path)

    def test_real_barnes_reentrant_instance(self) -> None:
        path = SOURCE_ROOT / "fjsp.barnes.mt10c1.m11j10c2.rjsp.seed20260714.txt"
        if not path.exists():
            self.skipTest("reentrant benchmark source directory is unavailable")
        instance = parse_standard_fjsp(path)
        self.assertEqual("fjsp_reentrant", instance.variant)
        self.assertEqual(100, instance.original_operation_count)
        self.assertEqual(182, instance.operation_count)
        self.assertEqual(10, len(instance.reentrant_loops))

    def test_web_profile_routes_to_reentrant_evaluator(self) -> None:
        profile = inspect_instance_profile(ROOT / "examples" / "fjsp_reentrant_tiny.rjsp.txt")
        self.assertTrue(profile["valid"])
        self.assertEqual("fjsp_reentrant", profile["variant"])
        self.assertEqual("examples/fjsp_reentrant_evaluator.py", profile["fixed_evaluator"])
        self.assertEqual(8, profile["original_operation_count"])
        self.assertEqual(12, profile["operation_count"])
        self.assertIn("reentrant_route", profile["variant_features"])

    def test_domain_pack_exposes_reentrant_skill_and_method_package(self) -> None:
        domain_pack = get_domain_pack("fjsp_reentrant")
        self.assertIsNotNone(domain_pack)
        assert domain_pack is not None
        self.assertIsNotNone(domain_pack.worker_implementation_skill("fjsp-reentrant-adapter-worker"))
        self.assertIsNotNone(domain_pack.method_package("fjsp_reentrant_adaptation"))

    def test_reentrant_quality_contract_reaches_worker_assignment(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
                "evaluator_command_template": "python examples/fjsp_reentrant_evaluator.py",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "instance_diagnostics": {
                "status": "available",
                "summary": {
                    "instance_count": 1,
                    "profiled_count": 1,
                    "reentrant_instance_count": 1,
                    "max_reentrant_expansion_ratio": 1.5,
                },
                "instances": [
                    {"variant": "fjsp_reentrant", "reentrant_loop_count": 2}
                ],
            },
        }
        quality = build_agent_generated_solver_quality_contract(context)
        self.assertIn("reentrant_route", quality["active_features"])
        self.assertIn("loop_expansion", quality["active_features"])
        self.assertIn(
            "reentrant_loop_parser_and_expansion_guard",
            quality["variant_required_code_capabilities"],
        )

        assignment = build_worker_assignment(
            context=context,
            direction_plan={
                "direction_id": "d000-reentrant",
                "method_family": "coupled_local_search",
                "method_package_id": "fjsp_reentrant_adaptation",
                "knowledge_query": ["reentrant_route", "reentrant_aware_search"],
                "hypothesis": "Improve the expanded route with repeated-pass critical-block search.",
            },
            loop_feedback={},
            round_index=0,
            attempt_index=0,
            max_steps=4,
            max_runtime_seconds=300,
        )
        self.assertIn("reentrant_route", assignment.runtime_contract["active_features"])
        skill_ids = [item["skill_id"] for item in assignment.implementation_skills]
        self.assertIn("fjsp-reentrant-adapter-worker", skill_ids)

    def test_reentrant_evaluator_executes_expanded_schedule(self) -> None:
        instance = parse_standard_fjsp(ROOT / "examples" / "fjsp_reentrant_tiny.rjsp.txt")
        now = 0
        schedule = []
        for job in instance.jobs:
            for operation in job.operations:
                option = operation.candidates[0]
                schedule.append(
                    {
                        "job_id": job.job_id,
                        "op_id": operation.op_id,
                        "machine_id": option.machine_id,
                        "start": now,
                        "end": now + option.duration,
                    }
                )
                now += option.duration
        with tempfile.TemporaryDirectory() as temp:
            solution_path = Path(temp) / "solution.json"
            metrics_path = Path(temp) / "metrics.json"
            solution_path.write_text(json.dumps({"schedule": schedule}), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_reentrant_evaluator.py"),
                    "--instance",
                    str(ROOT / "examples" / "fjsp_reentrant_tiny.rjsp.txt"),
                    "--solution",
                    str(solution_path),
                    "--metrics",
                    str(metrics_path),
                ],
                cwd=ROOT,
                check=True,
            )
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["valid"], payload)
        self.assertEqual(12.0, payload["metrics"]["operation_count"])


if __name__ == "__main__":
    unittest.main()
