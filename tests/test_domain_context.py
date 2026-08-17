from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.domains.context import get_domain_context_provider, register_domain_context_provider
from harness_agent.core.models import TaskContract


ROOT = Path(__file__).resolve().parents[1]


class _ToyContextProvider:
    def inspect_instances(self, contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
        return {"status": "available", "summary": {"family": contract.problem_family}, "instances": []}

    def active_features(
        self,
        *,
        contract: TaskContract,
        instance_diagnostics: dict[str, Any],
        contract_review_evidence: dict[str, Any],
    ) -> list[str]:
        return ["toy_feature"]


class DomainContextTests(unittest.TestCase):
    def test_standard_fjsp_provider_profiles_standard_instance_without_sdst_features(self) -> None:
        contract = self._contract(
            problem_family="standard_fjsp",
            instance=ROOT / "examples" / "standard_fjsp_tiny.fjs",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual("available", diagnostics["status"])
        self.assertEqual(0, diagnostics["summary"]["sdst_instance_count"])
        self.assertEqual("standard_fjsp", diagnostics["instances"][0]["variant"])
        self.assertEqual([], features)

    def test_standard_fjsp_provider_detects_sdst_from_parsed_instance(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "fjsp_sdst_hudata_tiny.txt",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["sdst_instance_count"])
        self.assertEqual("job_pair", diagnostics["instances"][0]["setup_time_kind"])
        self.assertEqual(["fjsp_sdst", "sequence_dependent_setup", "setup_time"], features)

    def test_standard_fjsp_provider_detects_minimum_time_lag_from_parsed_instance(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "fjsp_min_time_lag_tiny.mitfjsp",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["min_time_lag_instance_count"])
        self.assertEqual("fjsp_min_time_lag", diagnostics["instances"][0]["variant"])
        self.assertEqual(1, diagnostics["instances"][0]["min_time_lag_constraint_count"])
        self.assertEqual(["fjsp_min_time_lag", "minimum_time_lag", "time_lag"], features)

        quality = build_agent_generated_solver_quality_contract(
            {
                "evaluator_protocol": {
                    "solver_command_template": "python examples/agent_generated_fjsp_solver.py",
                    "evaluator_command_template": "python examples/standard_fjsp_evaluator.py",
                },
                "instance_diagnostics": diagnostics,
                "problem_family_capability": {
                    "family_id": "standard_fjsp",
                    "supported_variants": ["standard_fjsp", "fjsp_min_time_lag"],
                },
                "baseline_generation": {"source": "agent_generated"},
            }
        )
        self.assertIn("minimum_time_lag", quality["active_features"])
        self.assertIn(
            "minimum_time_lag_parser_and_propagation_guard",
            quality["variant_required_code_capabilities"],
        )

    def test_release_time_diagnostics_flow_into_quality_contract(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "fjsp_release_time_tiny.rtfjsp.txt",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["release_time_instance_count"])
        self.assertEqual("fjsp_release_time", diagnostics["instances"][0]["variant"])
        self.assertEqual(
            ["fjsp_release_time", "release_time", "machine_initial_availability"],
            features,
        )

        quality = build_agent_generated_solver_quality_contract(
            {
                "evaluator_protocol": {
                    "solver_command_template": "python examples/agent_generated_fjsp_solver.py",
                    "evaluator_command_template": "python examples/fjsp_release_time_evaluator.py",
                },
                "instance_diagnostics": diagnostics,
                "baseline_generation": {"source": "agent_generated"},
            }
        )
        self.assertIn("release_time", quality["active_features"])
        self.assertIn("machine_initial_availability", quality["active_features"])
        self.assertIn(
            "release_time_parser_and_job_ready_guard",
            quality["variant_required_code_capabilities"],
        )
        self.assertIn(
            "machine_initial_availability_guard",
            quality["variant_required_code_capabilities"],
        )

    def test_maximum_time_lag_diagnostics_flow_into_quality_contract(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "fjsp_max_time_lag_tiny.tlfjsp",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["max_time_lag_instance_count"])
        self.assertEqual("fjsp_max_time_lag", diagnostics["instances"][0]["variant"])
        self.assertEqual(2, diagnostics["instances"][0]["max_time_lag_constraint_count"])
        self.assertEqual(["fjsp_max_time_lag", "maximum_time_lag", "time_lag"], features)

        quality = build_agent_generated_solver_quality_contract(
            {
                "evaluator_protocol": {
                    "solver_command_template": "python examples/agent_generated_fjsp_solver.py",
                    "evaluator_command_template": "python examples/standard_fjsp_evaluator.py",
                },
                "instance_diagnostics": diagnostics,
                "problem_family_capability": {
                    "family_id": "standard_fjsp",
                    "supported_variants": ["standard_fjsp", "fjsp_max_time_lag"],
                },
                "baseline_generation": {"source": "agent_generated"},
            }
        )
        self.assertIn("maximum_time_lag", quality["active_features"])
        self.assertNotIn("minimum_time_lag", quality["active_features"])
        self.assertIn(
            "maximum_time_lag_parser_and_propagation_guard",
            quality["variant_required_code_capabilities"],
        )

    def test_machine_availability_diagnostics_flow_into_quality_contract(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "FFCR_tiny.txt",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["machine_availability_instance_count"])
        self.assertEqual("fjsp_machine_availability", diagnostics["instances"][0]["variant"])
        self.assertEqual(
            ["fjsp_machine_availability", "machine_availability", "machine_calendar", "maintenance"],
            features,
        )

        quality = build_agent_generated_solver_quality_contract(
            {
                "evaluator_protocol": {
                    "solver_command_template": "python examples/agent_generated_fjsp_solver.py",
                    "evaluator_command_template": "python examples/fjsp_machine_availability_evaluator.py",
                },
                "instance_diagnostics": diagnostics,
                "baseline_generation": {"source": "agent_generated"},
            }
        )
        self.assertIn("machine_availability", quality["active_features"])
        self.assertIn("machine_calendar", quality["active_features"])
        self.assertIn(
            "machine_calendar_availability_guard",
            quality["variant_required_code_capabilities"],
        )

    def test_alternative_path_diagnostics_flow_into_quality_contract(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "fjsp_alternative_path_tiny.apfjsp",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["alternative_path_instance_count"])
        self.assertEqual("fjsp_alternative_path", diagnostics["instances"][0]["variant"])
        self.assertEqual(2, diagnostics["instances"][0]["alternative_route_count"])
        self.assertEqual(["fjsp_alternative_path", "alternative_path", "route_choice"], features)

        quality = build_agent_generated_solver_quality_contract(
            {
                "evaluator_protocol": {
                    "solver_command_template": "python examples/agent_generated_fjsp_solver.py",
                    "evaluator_command_template": "python examples/fjsp_alternative_path_evaluator.py",
                },
                "instance_diagnostics": diagnostics,
                "baseline_generation": {"source": "agent_generated"},
            }
        )
        self.assertIn("alternative_path", quality["active_features"])
        self.assertIn("route_choice", quality["active_features"])
        self.assertIn(
            "alternative_path_route_selection_guard",
            quality["variant_required_code_capabilities"],
        )
        self.assertIn(
            "selected_routes",
            provider.solution_contract()["variant_required_top_level_fields"]["fjsp_alternative_path"],
        )

    def test_unknown_problem_family_uses_generic_provider(self) -> None:
        contract = self._contract(
            problem_family="unknown_variant",
            instance=ROOT / "examples" / "standard_fjsp_tiny.fjs",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)

        self.assertEqual("unavailable", diagnostics["status"])
        self.assertEqual(0, diagnostics["summary"]["profiled_count"])
        self.assertEqual([], diagnostics["instances"])

    def test_provider_registration_is_normalized(self) -> None:
        provider = _ToyContextProvider()
        register_domain_context_provider("Toy Variant", provider)

        self.assertIs(provider, get_domain_context_provider("toy-variant"))

    def _contract(self, *, problem_family: str, instance: Path) -> TaskContract:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        contract_path = Path(temporary.name) / "contract.json"
        contract_path.write_text(
            json.dumps(
                {
                    "task_id": "domain_context_test",
                    "problem_family": problem_family,
                    "description": "domain context test",
                    "instances": [{"id": instance.stem, "path": str(instance)}],
                    "objectives": [{"name": "makespan", "direction": "minimize"}],
                    "commands": {"solver": "python solver.py", "evaluator": "python evaluator.py"},
                    "review": {"status": "confirmed"},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return TaskContract.load(contract_path)


if __name__ == "__main__":
    unittest.main()
