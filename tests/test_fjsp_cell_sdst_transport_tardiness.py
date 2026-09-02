from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import parse_standard_fjsp, validate_standard_schedule, ScheduleRecord
from harness_agent.agents.quality_contract import build_solver_runtime_feature_contract
from harness_agent.context.knowledge import method_package_catalog
from harness_agent.domains.families import get_domain_pack
from harness_agent.orchestration.standard import fixed_problem_contract
from harness_agent.web.server import inspect_instance_profile, method_package_features


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / "examples" / "fjsp_cell_sdst_transport_Ins01.fjcs.json"


class CellSdstTransportTardinessTest(unittest.TestCase):
    def test_public_instance_contract_and_profile(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        self.assertEqual("fjsp_cell_sdst_transport_tardiness", instance.variant)
        self.assertEqual((4, 6, 13), (instance.job_count, instance.machine_count, instance.operation_count))
        self.assertEqual((2, 4, 9, 22), instance.source_job_ids)
        self.assertEqual((77, 3, 50, 11), instance.job_due_dates)
        self.assertTrue(instance.has_sequence_dependent_setup)
        self.assertTrue(instance.has_transport_times)
        self.assertTrue(instance.has_reentrant_routes)

        profile = inspect_instance_profile(INSTANCE)
        self.assertTrue(profile["valid"])
        self.assertEqual(
            "examples/fjsp_cell_sdst_transport_tardiness_evaluator.py",
            profile["fixed_evaluator"],
        )
        self.assertEqual(["makespan", "total_tardiness"], profile["objective_names"])
        self.assertIn("cell_transport", method_package_features(profile))
        self.assertIn("total_tardiness", method_package_features(profile))

    def test_joint_validator_recomputes_lexicographic_metrics(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        rows = [
            (0, 0, 2, 0, 4), (0, 1, 0, 4, 7), (0, 2, 1, 7, 11),
            (1, 0, 3, 0, 6), (1, 1, 4, 6, 8), (1, 2, 3, 14, 20),
            (2, 0, 1, 11, 16), (2, 1, 0, 16, 20), (2, 2, 1, 20, 25),
            (3, 0, 4, 0, 3), (3, 1, 3, 6, 14), (3, 2, 5, 14, 17),
            (3, 3, 3, 20, 28),
        ]
        schedule = [ScheduleRecord(*row) for row in rows]
        errors, metrics = validate_standard_schedule(instance, schedule)
        self.assertEqual([], errors)
        self.assertEqual(28.0, metrics["makespan"])
        self.assertEqual(34.0, metrics["total_tardiness"])
        self.assertEqual(2.0, metrics["tardy_job_count"])
        self.assertEqual(0.0, metrics["transport_violations"])

    def test_evaluator_rejects_false_tardiness_declaration(self) -> None:
        rows = [
            (0, 0, 2, 0, 4), (0, 1, 0, 4, 7), (0, 2, 1, 7, 11),
            (1, 0, 3, 0, 6), (1, 1, 4, 6, 8), (1, 2, 3, 14, 20),
            (2, 0, 1, 11, 16), (2, 1, 0, 16, 20), (2, 2, 1, 20, 25),
            (3, 0, 4, 0, 3), (3, 1, 3, 6, 14), (3, 2, 5, 14, 17),
            (3, 3, 3, 20, 28),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            solution = Path(tmp) / "solution.json"
            metrics = Path(tmp) / "metrics.json"
            solution.write_text(
                json.dumps(
                    {
                        "format": "standard_fjsp_schedule_v1",
                        "makespan": 28,
                        "total_tardiness": 33,
                        "schedule": [
                            {"job_id": j, "op_id": o, "machine_id": m, "start": s, "end": e}
                            for j, o, m, s, e in rows
                        ],
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_cell_sdst_transport_tardiness_evaluator.py"),
                    "--instance", str(INSTANCE), "--solution", str(solution), "--metrics", str(metrics),
                ],
                check=True,
            )
            payload = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertFalse(payload["valid"])
            self.assertTrue(any("total_tardiness mismatch" in error for error in payload["errors"]))

    def test_fixed_contract_and_method_package_are_registered(self) -> None:
        family, evaluator, objectives = fixed_problem_contract([INSTANCE])
        self.assertEqual("FJSP", family)
        self.assertEqual("examples/fjsp_cell_sdst_transport_tardiness_evaluator.py", evaluator)
        self.assertEqual(["makespan", "total_tardiness"], [item["name"] for item in objectives])
        pack = get_domain_pack("fjsp_cell_sdst_transport_tardiness")
        self.assertIsNotNone(
            pack.method_package("fjsp_cell_sdst_transport_tardiness_adaptation")
        )
        self.assertIsNotNone(
            pack.worker_implementation_skill("fjsp-cell-sdst-transport-tardiness-adapter-worker")
        )

        runtime = build_solver_runtime_feature_contract(
            {
                "instance_diagnostics": {
                    "status": "available",
                    "summary": {
                        "profiled_count": 1,
                        "cell_sdst_transport_instance_count": 1,
                        "sdst_instance_count": 1,
                        "reentrant_instance_count": 1,
                    },
                    "instances": [{"variant": "fjsp_cell_sdst_transport_tardiness"}],
                }
            }
        )
        self.assertTrue(
            {
                "setup_aware_machine_arc_timing",
                "transport_time_guard",
                "explicit_reentrant_operation_identity_guard",
                "due_date_or_tardiness_objective_guard",
                "declared_objective_priority_guard",
            }.issubset(runtime["variant_required_code_capabilities"])
        )

    def test_method_contract_uses_expanded_route_and_family_specific_moves(self) -> None:
        contract = json.loads(
            (
                ROOT
                / "knowledge"
                / "method_packages"
                / "fjsp_cell_sdst_transport_tardiness_adaptation"
                / "implementation_contract.json"
            ).read_text(encoding="utf-8")
        )
        components = {
            item["component_id"]: " ".join(item["required_behaviors"])
            for item in contract["required_components"]
        }

        self.assertIn("jobs[].operations", components["normalized_fjcs_parser"])
        self.assertIn("不要求或假设另有 reentrant_route 字段", components["normalized_fjcs_parser"])
        self.assertIn("transactional_candidate_acceptance", components)
        self.assertNotIn("transactional_moves", components)
        self.assertIn(
            "局部搜索算子",
            contract["coupled_groups"][0]["rule"],
        )
        self.assertIn(
            "移动后完整重算",
            contract["method_family_rules"]["coupled_local_search"]["required_behavior"],
        )

    def test_method_package_resolves_for_each_supported_competing_family(self) -> None:
        features = [
            "fjsp_cell_sdst_transport_tardiness",
            "cell_transport",
            "family_sequence_dependent_setup",
            "reentrant_route",
            "due_date",
            "total_tardiness",
            "multi_feature",
        ]
        for family in (
            "constructive_search",
            "coupled_local_search",
            "exact_hybrid",
            "population_memetic",
        ):
            with self.subTest(family=family):
                catalog = method_package_catalog(
                    problem_family="FJSP",
                    active_features=features,
                    knowledge_query_tags=[family],
                )
                self.assertIn(
                    "fjsp_cell_sdst_transport_tardiness_adaptation",
                    [item["package_id"] for item in catalog["packages"]],
                )


if __name__ == "__main__":
    unittest.main()
