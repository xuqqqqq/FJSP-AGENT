from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harness_agent.agents.quality_contract import build_solver_runtime_feature_contract
from harness_agent.context.knowledge import method_package_catalog
from harness_agent.core.models import TaskContract
from harness_agent.domains.context import get_domain_context_provider
from harness_agent.domains.families import get_domain_pack
from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule
from harness_agent.orchestration.standard import fixed_problem_contract
from harness_agent.web.server import inspect_instance_profile


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / "examples" / "calendar_reentrant_smoke.calendar_reentrant.json"


def valid_schedule() -> list[ScheduleRecord]:
    return [
        ScheduleRecord(0, 0, 0, 3, 6),
        ScheduleRecord(0, 1, 0, 12, 14),
        ScheduleRecord(0, 2, 0, 14, 16),
        ScheduleRecord(0, 3, 1, 18, 20),
        ScheduleRecord(1, 0, 1, 20, 22),
        ScheduleRecord(1, 1, 1, 22, 25),
        ScheduleRecord(1, 2, 1, 25, 28),
        ScheduleRecord(1, 3, 0, 28, 30),
    ]


class FjspCalendarReentrantTests(unittest.TestCase):
    def test_parser_expands_loops_and_activates_exact_feature_subset(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)

        self.assertEqual("fjsp_calendar_reentrant", instance.variant)
        self.assertEqual((6, 8), (instance.original_operation_count, instance.operation_count))
        self.assertEqual((3, 5), instance.job_release_times)
        self.assertEqual((2, 0), instance.machine_available_times)
        self.assertEqual(2, len(instance.unavailability_intervals))
        self.assertTrue(instance.has_release_times)
        self.assertTrue(instance.has_machine_availability)
        self.assertTrue(instance.has_reentrant_routes)
        self.assertFalse(instance.has_sequence_dependent_setup)
        self.assertFalse(instance.has_minimum_time_lags)
        self.assertFalse(instance.has_batch_processing)

    def test_parser_rejects_feature_drift(self) -> None:
        payload = json.loads(INSTANCE.read_text(encoding="utf-8"))
        payload["active_features"].append("minimum_time_lag")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.calendar_reentrant.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "active_features must be exactly"):
                parse_standard_fjsp(path)

    def test_joint_validator_accepts_valid_and_rejects_each_calendar_bound(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        errors, metrics = validate_standard_schedule(instance, valid_schedule())
        self.assertEqual([], errors)
        self.assertEqual(30.0, metrics["makespan"])
        self.assertEqual(2.0, metrics["reentrant_added_operation_count"])
        self.assertEqual(0.0, metrics["machine_availability_violations"])

        release_violation = list(valid_schedule())
        release_violation[0] = replace(release_violation[0], start=2, end=5)
        release_errors, _ = validate_standard_schedule(instance, release_violation)
        self.assertTrue(any("job release-time violation" in error for error in release_errors))

        downtime_violation = list(valid_schedule())
        downtime_violation[1] = replace(downtime_violation[1], start=9, end=11)
        downtime_errors, _ = validate_standard_schedule(instance, downtime_violation)
        self.assertTrue(any("machine availability violation" in error for error in downtime_errors))

    def test_web_contract_domain_pack_and_runtime_guards_are_combined(self) -> None:
        profile = inspect_instance_profile(INSTANCE)
        self.assertTrue(profile["valid"], profile.get("error"))
        self.assertEqual(
            "examples/fjsp_calendar_reentrant_evaluator.py", profile["fixed_evaluator"]
        )
        self.assertTrue(
            {"release_time", "machine_availability", "reentrant_route", "multi_feature"}
            .issubset(profile["variant_features"])
        )

        _, evaluator, objectives = fixed_problem_contract([INSTANCE])
        self.assertEqual("examples/fjsp_calendar_reentrant_evaluator.py", evaluator)
        self.assertEqual(["makespan"], [item["name"] for item in objectives])

        pack = get_domain_pack("fjsp_calendar_reentrant")
        self.assertIsNotNone(pack)
        self.assertIsNotNone(pack.worker_implementation_skill("fjsp-calendar-reentrant-adapter-worker"))
        self.assertIsNotNone(pack.method_package("fjsp_calendar_reentrant_adaptation"))
        catalog = method_package_catalog(
            problem_family="FJSP",
            active_features=profile["variant_features"],
        )
        self.assertEqual("fjsp_calendar_reentrant_adaptation", catalog["recommended_package_id"])

        runtime = build_solver_runtime_feature_contract(
            {
                "instance_diagnostics": {
                    "status": "available",
                    "summary": {
                        "profiled_count": 1,
                        "release_time_instance_count": 1,
                        "machine_availability_instance_count": 1,
                        "reentrant_instance_count": 1,
                    },
                    "instances": [
                        {
                            "variant": "fjsp_calendar_reentrant",
                            "unavailability_interval_count": 2,
                            "reentrant_loop_count": 2,
                        }
                    ],
                }
            }
        )
        self.assertTrue(
            {
                "release_time_parser_and_job_ready_guard",
                "machine_initial_availability_guard",
                "machine_calendar_availability_guard",
                "reentrant_loop_parser_and_expansion_guard",
            }.issubset(runtime["variant_required_code_capabilities"])
        )

    def test_fixed_evaluator_accepts_jointly_valid_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps({"schedule": [record.__dict__ for record in valid_schedule()]}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_calendar_reentrant_evaluator.py"),
                    "--instance",
                    str(INSTANCE),
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

    def test_context_provider_reports_all_three_feature_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = Path(tmp) / "contract.json"
            contract_path.write_text(
                json.dumps(
                    {
                        "task_id": "calendar_reentrant_context",
                        "problem_family": "FJSP",
                        "description": "calendar reentrant context test",
                        "instances": [{"id": INSTANCE.stem, "path": str(INSTANCE)}],
                        "objectives": [{"name": "makespan", "direction": "minimize"}],
                        "commands": {"solver": "python solver.py", "evaluator": "python evaluator.py"},
                        "review": {"status": "confirmed"},
                    }
                ),
                encoding="utf-8",
            )
            contract = TaskContract.load(contract_path)
            provider = get_domain_context_provider(contract.problem_family)
            diagnostics = provider.inspect_instances(contract, project_root=ROOT)
            features = provider.active_features(
                contract=contract,
                instance_diagnostics=diagnostics,
                contract_review_evidence={},
            )

        summary = diagnostics["summary"]
        self.assertEqual(1, summary["release_time_instance_count"])
        self.assertEqual(1, summary["machine_availability_instance_count"])
        self.assertEqual(1, summary["reentrant_instance_count"])
        self.assertTrue(
            {"release_time", "machine_availability", "reentrant_route"}.issubset(features)
        )


if __name__ == "__main__":
    unittest.main()
