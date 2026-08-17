from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import ScheduleRecord, load_solution, parse_standard_fjsp, validate_standard_schedule


ROOT = Path(__file__).resolve().parents[1]
TINY = ROOT / "examples" / "fjsp_alternative_path_tiny.apfjsp"


def alternative_schedule() -> list[ScheduleRecord]:
    return [
        ScheduleRecord(job_id=0, op_id=0, machine_id=0, start=0, end=2),
        ScheduleRecord(job_id=0, op_id=2, machine_id=0, start=2, end=3),
        ScheduleRecord(job_id=1, op_id=0, machine_id=1, start=0, end=2),
        ScheduleRecord(job_id=1, op_id=2, machine_id=1, start=2, end=3),
        ScheduleRecord(job_id=1, op_id=1, machine_id=0, start=3, end=5),
    ]


class AlternativePathTests(unittest.TestCase):
    def test_method_packages_publish_canonical_activation_telemetry(self) -> None:
        package_root = ROOT / "knowledge" / "method_packages"
        expected = {
            "fjsp_alternative_path_constructive_adaptation": {
                "diagnostics.activation.constructive_search.candidates_evaluated",
                "diagnostics.activation.alternative_path.route_configurations_evaluated",
            },
            "fjsp_alternative_path_coupled_local_search": {
                "diagnostics.activation.coupled_local_search.moves_evaluated",
                "diagnostics.activation.alternative_path.route_switch_moves_evaluated",
            },
            "fjsp_alternative_path_exact_hybrid": {
                "diagnostics.cp_sat_called",
                "diagnostics.solver_evidence.route_one_hot_constraints_posted",
                "diagnostics.solver_evidence.route_optional_intervals_posted",
                "diagnostics.solver_evidence.route_conditional_precedences_posted",
            },
        }
        for package_id, required_fields in expected.items():
            with self.subTest(package_id=package_id):
                contract = json.loads(
                    (package_root / package_id / "implementation_contract.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    required_fields,
                    set(contract["activation_evidence"]["required_fields"]),
                )

    def test_direction_hints_do_not_leak_max_lag_language(self) -> None:
        from harness_agent.domains.standard_fjsp import _instance_direction_hints

        hints = _instance_direction_hints(
            {
                "alternative_path_instance_count": 1,
                "max_route_option_count": 2,
                "avg_alternative_route_length_ratio": 0.8,
            },
            [{"variant": "fjsp_alternative_path"}],
        )

        self.assertIn("Measured route structure", " ".join(hints))
        self.assertNotIn("max-lag", " ".join(hints).lower())

    def test_parser_reads_subset_and_reordered_routes(self) -> None:
        instance = parse_standard_fjsp(TINY)

        self.assertEqual("fjsp_alternative_path", instance.variant)
        self.assertTrue(instance.has_alternative_routes)
        self.assertEqual(((0, 2),), instance.alternative_routes[0])
        self.assertEqual(((0, 2, 1),), instance.alternative_routes[1])
        self.assertEqual(((0, 1, 2), (0, 2)), instance.route_options(0))

    def test_selected_subset_and_reordered_routes_are_valid(self) -> None:
        errors, metrics = validate_standard_schedule(
            parse_standard_fjsp(TINY),
            alternative_schedule(),
            selected_routes={0: 1, 1: 1},
        )

        self.assertEqual([], errors)
        self.assertEqual(5.0, metrics["operation_count"])
        self.assertEqual(6.0, metrics["operation_pool_count"])
        self.assertEqual(2.0, metrics["selected_alternative_route_count"])

    def test_route_metadata_is_required_and_range_checked(self) -> None:
        instance = parse_standard_fjsp(TINY)
        missing, _ = validate_standard_schedule(instance, alternative_schedule())
        out_of_range, _ = validate_standard_schedule(
            instance,
            alternative_schedule(),
            selected_routes={0: 2, 1: 1},
        )

        self.assertTrue(any("must contain selected_routes" in error for error in missing))
        self.assertTrue(any("route is out of range" in error for error in out_of_range))

    def test_unselected_operation_and_wrong_route_precedence_are_rejected(self) -> None:
        instance = parse_standard_fjsp(TINY)
        schedule = alternative_schedule()
        schedule[1] = ScheduleRecord(job_id=0, op_id=1, machine_id=1, start=2, end=5)
        route_errors, _ = validate_standard_schedule(instance, schedule, selected_routes={0: 1, 1: 1})

        reordered = alternative_schedule()
        reordered[3] = ScheduleRecord(job_id=1, op_id=2, machine_id=1, start=3, end=4)
        reordered[4] = ScheduleRecord(job_id=1, op_id=1, machine_id=0, start=2, end=4)
        precedence_errors, _ = validate_standard_schedule(
            instance,
            reordered,
            selected_routes={0: 1, 1: 1},
        )

        self.assertTrue(any("not on selected route" in error for error in route_errors))
        self.assertTrue(any("precedence violation" in error for error in precedence_errors))

    def test_parser_rejects_duplicate_out_of_range_and_trailing_route_tokens(self) -> None:
        prefix = "1 1 1\n3 1 0 1 1 0 1 1 0 1\n"
        tails = {
            "duplicate_op": "1\n2 0 0\n",
            "out_of_range": "1\n2 0 3\n",
            "duplicate_route": "1\n3 0 1 2\n",
            "trailing": "0\n99\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, tail in tails.items():
                with self.subTest(name=name):
                    path = Path(tmp) / f"{name}.apfjsp"
                    path.write_text(prefix + tail, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        parse_standard_fjsp(path)

    def test_dedicated_evaluator_consumes_selected_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps(
                    {
                        "selected_routes": {"0": 1, "1": 1},
                        "schedule": [record.__dict__ for record in alternative_schedule()],
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_alternative_path_evaluator.py"),
                    "--instance",
                    str(TINY),
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
        self.assertEqual(5.0, payload["metrics"]["operation_count"])

    def test_legacy_solution_loader_remains_schedule_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "solution.json"
            path.write_text(
                json.dumps(
                    {
                        "selected_routes": {"0": 1, "1": 1},
                        "schedule": [record.__dict__ for record in alternative_schedule()],
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_solution(path)

        self.assertEqual(alternative_schedule(), loaded)


if __name__ == "__main__":
    unittest.main()
