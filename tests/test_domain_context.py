from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

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

    def test_standard_fjsp_provider_detects_machine_availability_from_parsed_instance(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=ROOT / "examples" / "nfa_ffcr01.txt",
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual(1, diagnostics["summary"]["nfa_instance_count"])
        self.assertEqual(7, diagnostics["summary"]["total_unavailability_count"])
        self.assertEqual("fjsp_machine_availability", diagnostics["instances"][0]["variant"])
        self.assertTrue(diagnostics["instances"][0]["has_machine_availability"])
        self.assertEqual(
            ["fjsp_machine_availability", "machine_calendar", "maintenance"],
            features,
        )

    def test_provider_detects_distributed_transfer_from_dfm_header(self) -> None:
        contract = self._contract(
            problem_family="fjsp_distributed_transfer",
            instance=(
                ROOT
                / "ALL-Input-Information"
                / "10-distributed-FJSP"
                / "10-Instance"
                / "small size"
                / "DFM01_10x2x6.txt"
            ),
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual("available", diagnostics["status"])
        self.assertEqual(1, diagnostics["summary"]["distributed_transfer_instance_count"])
        self.assertEqual(2, diagnostics["summary"]["factory_count_max"])
        self.assertEqual(6, diagnostics["summary"]["machines_per_factory_max"])
        self.assertEqual("fjsp_distributed_transfer", diagnostics["instances"][0]["variant"])
        self.assertTrue(diagnostics["instances"][0]["has_distributed_transfer"])
        self.assertTrue(diagnostics["instances"][0]["energy_enabled"])
        self.assertEqual(
            {
                "fjsp_distributed_transfer",
                "distributed_factories",
                "factory_assignment",
                "transfer_time",
                "energy_consumption",
            },
            set(features)
            & {
                "fjsp_distributed_transfer",
                "distributed_factories",
                "factory_assignment",
                "transfer_time",
                "energy_consumption",
            },
        )

    def test_provider_detects_job_priority_from_standard_tail(self) -> None:
        contract = self._contract(
            problem_family="FJSP",
            instance=(
                ROOT
                / "ALL-Input-Information"
                / "11-priority-FJSP"
                / "11-Instances"
                / "fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722.txt"
            ),
        )
        provider = get_domain_context_provider(contract.problem_family)

        diagnostics = provider.inspect_instances(contract, project_root=ROOT)
        features = provider.active_features(
            contract=contract,
            instance_diagnostics=diagnostics,
            contract_review_evidence={},
        )

        self.assertEqual("available", diagnostics["status"])
        self.assertEqual(1, diagnostics["summary"]["priority_job_instance_count"])
        self.assertEqual(3, diagnostics["summary"]["priority_job_count_max"])
        self.assertEqual(0.3, diagnostics["summary"]["priority_job_ratio_avg"])
        self.assertEqual("fjsp_priority", diagnostics["instances"][0]["variant"])
        self.assertTrue(diagnostics["instances"][0]["has_job_priority"])
        self.assertEqual([1, 6, 8], diagnostics["instances"][0]["priority_job_ids"])
        self.assertEqual(3, diagnostics["instances"][0]["priority_job_count"])
        self.assertEqual(0.3, diagnostics["instances"][0]["priority_job_ratio"])
        self.assertEqual(
            [
                "fjsp_job_priority",
                "fjsp_priority",
                "job_priority",
                "priority_jobs",
                "priority_completion_time",
                "multi_objective",
                "lexicographic_objective",
            ],
            features,
        )

    def test_priority_provider_alias_exposes_standard_schedule_with_priority_metrics(self) -> None:
        provider = get_domain_context_provider("fjspjp")

        contract = provider.solution_contract()

        self.assertEqual("standard_fjsp_schedule_v1", contract["format"])
        self.assertEqual(
            ["job_id", "op_id", "machine_id", "start", "end"],
            contract["schedule_record_fields"],
        )
        self.assertEqual(
            ["makespan", "priority_completion_time"],
            contract["objective_metrics"],
        )
        self.assertEqual("AlgoForge Core job-priority evaluator", contract["legality_owner"])

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
