from __future__ import annotations

import unittest
from pathlib import Path

from harness_agent.domains.families import get_domain_pack
from harness_agent.agents.quality_contract import build_solver_runtime_feature_contract
from harness_agent.domains.io import parse_standard_fjsp, validate_standard_schedule
from harness_agent.orchestration.standard import fixed_problem_contract
from harness_agent.web.server import inspect_instance_profile


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / "examples" / "fjsp_jpc_tst_T01.jpctst.json"


class FjspJpcTstTests(unittest.TestCase):
    def test_public_t01_parses_all_combined_features(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        self.assertEqual("fjsp_jpc_tst", instance.variant)
        self.assertEqual((10, 4, 39), (instance.job_count, instance.machine_count, instance.operation_count))
        self.assertEqual(9, len(instance.job_precedences))
        self.assertTrue(instance.has_transport_times)
        self.assertTrue(instance.has_operation_setup_times)
        self.assertFalse(instance.has_sequence_dependent_setup)

    def test_empty_schedule_reports_combined_contract_metrics(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        errors, metrics = validate_standard_schedule(instance, [])
        self.assertTrue(errors)
        self.assertEqual(9.0, metrics["job_precedence_constraints"])
        self.assertEqual(0.0, metrics["transport_violations"])
        self.assertEqual(0.0, metrics["operation_setup_count"])

    def test_web_and_core_route_to_combined_evaluator(self) -> None:
        profile = inspect_instance_profile(INSTANCE)
        self.assertEqual("examples/fjsp_jpc_tst_evaluator.py", profile["fixed_evaluator"])
        self.assertEqual(
            {"job_precedence", "machine_transport", "operation_setup"},
            set(profile["variant_features"]),
        )
        _, evaluator, objectives = fixed_problem_contract([INSTANCE])
        self.assertEqual("examples/fjsp_jpc_tst_evaluator.py", evaluator)
        self.assertEqual(["makespan"], [item["name"] for item in objectives])

    def test_domain_pack_exposes_joint_skill_and_package(self) -> None:
        pack = get_domain_pack("fjsp_jpc_tst")
        self.assertIsNotNone(pack)
        self.assertIsNotNone(pack.worker_implementation_skill("fjsp-jpc-tst-adapter-worker"))
        self.assertIsNotNone(pack.method_package("fjsp_jpc_tst_adaptation"))

    def test_quality_contract_requires_all_combined_guards(self) -> None:
        runtime = build_solver_runtime_feature_contract(
            {
                "instance_diagnostics": {
                    "status": "available",
                    "summary": {"profiled_count": 1, "jpc_tst_instance_count": 1},
                    "instances": [{"variant": "fjsp_jpc_tst"}],
                }
            }
        )
        self.assertTrue(
            {
                "cross_job_precedence",
                "machine_transport",
                "operation_setup",
            }.issubset(runtime["active_features"])
        )
        self.assertTrue(
            {
                "cross_job_precedence_guard",
                "machine_transport_matrix_guard",
                "operation_setup_occupancy_guard",
            }.issubset(runtime["variant_required_code_capabilities"])
        )


if __name__ == "__main__":
    unittest.main()
