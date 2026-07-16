from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.domains.pack import get_domain_pack, load_domain_pack, load_domain_packs
from harness_agent.context.knowledge import auto_knowledge_cards, method_package_catalog
from harness_agent.domains.families import get_problem_family
from harness_agent.slots.manifest import write_default_slot_manifest


ROOT = Path(__file__).resolve().parents[1]


class DomainPackTests(unittest.TestCase):
    def test_standard_fjsp_capability_loads_from_external_domain_pack(self) -> None:
        pack = get_domain_pack("FJSP")

        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertEqual("standard_fjsp", pack.family_id)
        self.assertTrue(pack.source_path)
        self.assertTrue(str(pack.source_path).endswith("domain_packs\\standard_fjsp\\domain_pack.json") or str(pack.source_path).endswith("domain_packs/standard_fjsp/domain_pack.json"))
        self.assertNotIn("fjsp_sdst", pack.aliases)
        self.assertNotIn("sdst", pack.capability.knowledge_tags)
        self.assertNotIn("sequence_dependent_setup", pack.capability.knowledge_tags)
        self.assertIsNone(pack.edit_strategy("slot_based_edit"))

        capability = get_problem_family("FJSP")
        self.assertEqual("standard_fjsp", capability.family_id)
        self.assertNotIn("FJSP-SDST", " ".join(capability.io_contract_notes))
        self.assertEqual([], pack.agent_generated_baseline_hidden_paths)
        self.assertTrue(pack.semantic_review_cards)
        self.assertTrue(
            any(path.name == "standard_fjsp_algorithm_semantic_review_contract.md" for path in pack.semantic_review_cards)
        )
        self.assertIn(
            "examples/standard_fjsp_evaluator.py",
            pack.agent_generated_baseline_preserve_paths,
        )
        self.assertNotIn(
            "harness_agent/orchestration/standard.py",
            pack.agent_generated_baseline_preserve_paths,
        )
        method_package = pack.method_package("standard_fjsp_awls_hgtsa")
        self.assertEqual("standard_fjsp_awls_hgtsa", method_package.package_id)
        self.assertTrue(any(path.name == "behavior_contract.md" for path in method_package.semantic_assets))
        self.assertFalse(any(path.name == "reference_solver.py" for path in method_package.semantic_assets))

    def test_domain_pack_declares_knowledge_retrieval_without_backend_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "domain_packs" / "toy"
            pack_dir.mkdir(parents=True)
            card = tmp_path / "toy_card.md"
            card.write_text("# toy\n", encoding="utf-8")
            slot_manifest = tmp_path / "toy_slots.json"
            slot_manifest.write_text('{"schema_version": 1, "problem_family": "toy", "slots": []}', encoding="utf-8")
            manifest = pack_dir / "domain_pack.json"
            manifest.write_text(
                json.dumps(
                    {
                        "family_id": "toy",
                        "aliases": ["toy_alias"],
                        "capability": {
                            "display_name": "Toy",
                            "description": "A toy external pack.",
                            "supported_variants": ["toy"],
                            "canonical_objectives": [{"name": "cost", "direction": "minimize"}],
                            "io_contract_notes": ["toy IO"],
                            "evaluator_invariants": ["toy evaluator"],
                            "solver_entrypoints": ["solver.py"],
                            "knowledge_tags": ["toy_tag"],
                        },
                        "knowledge": {
                            "base_cards": [str(card)],
                            "tagged_cards": {"toy_tag": [str(card)]},
                        },
                        "edit_strategies": [
                            {
                                "name": "slot_based_edit",
                                "description": "toy slots",
                                "assets": {"slot_manifest": str(slot_manifest)},
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            pack = load_domain_pack(manifest, project_root=tmp_path)
            packs = load_domain_packs(domain_pack_root=tmp_path / "domain_packs", project_root=tmp_path)

        self.assertEqual("toy", pack.family_id)
        self.assertIn("toy", packs)
        self.assertIn("toy_alias", packs)
        self.assertEqual([card.resolve()], pack.base_cards)
        self.assertEqual([card.resolve()], pack.tagged_cards["toy_tag"])
        strategy = pack.edit_strategy("slot_based_edit")
        self.assertIsNotNone(strategy)
        assert strategy is not None
        self.assertEqual(slot_manifest.resolve(), strategy.asset_path("slot_manifest"))

    def test_non_awls_method_package_contract_loads_generically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "domain_packs" / "toy"
            knowledge_dir = tmp_path / "knowledge" / "method_packages" / "toy_complete"
            pack_dir.mkdir(parents=True)
            knowledge_dir.mkdir(parents=True)
            implementation_asset = knowledge_dir / "reference_solver.py"
            contract_asset = knowledge_dir / "implementation_contract.json"
            implementation_asset.write_text("def solve():\n    return None\n", encoding="utf-8")
            contract_asset.write_text(
                json.dumps(
                    {
                        "contract_id": "toy_complete_contract",
                        "mode": "complete_method_package",
                        "completion_rule": "Implement every required component.",
                        "variant_rule": "Keep toy constraints active.",
                        "required_components": [
                            {
                                "component_id": "toy_decoder",
                                "title": "Toy decoder",
                                "required_behaviors": ["Decode the toy state into a schedule."],
                            },
                            {
                                "component_id": "toy_search",
                                "title": "Toy search",
                                "required_behaviors": ["Iteratively improve the toy schedule."],
                            },
                        ],
                        "coupled_groups": [
                            {
                                "group_id": "toy_loop",
                                "component_ids": ["toy_decoder", "toy_search"],
                                "rule": "Decoder and search must stay behaviorally aligned.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            manifest = pack_dir / "domain_pack.json"
            manifest.write_text(
                json.dumps(
                    {
                        "family_id": "toy",
                        "capability": {
                            "display_name": "Toy",
                            "description": "Toy pack with generic method contract.",
                            "supported_variants": ["toy"],
                            "canonical_objectives": [{"name": "cost", "direction": "minimize"}],
                            "io_contract_notes": ["toy IO"],
                            "evaluator_invariants": ["toy evaluator"],
                            "solver_entrypoints": ["solver.py"],
                            "knowledge_tags": ["toy_tag"],
                        },
                        "method_packages": [
                            {
                                "package_id": "toy_complete_bundle",
                                "title": "Toy complete bundle",
                                "description": "Synthetic non-AWLS package.",
                                "strategy_types": ["baseline_constructor"],
                                "required_features": ["toy"],
                                "assets": [str(implementation_asset), str(contract_asset)],
                                "implementation_asset": str(implementation_asset),
                                "implementation_contract_asset": str(contract_asset),
                                "default_priority": 9,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            pack = load_domain_pack(manifest, project_root=tmp_path)

        package = pack.method_package("toy_complete_bundle")
        self.assertIsNotNone(package)
        assert package is not None
        self.assertEqual("toy_complete_bundle", package.package_id)
        self.assertEqual(implementation_asset.resolve(), package.implementation_asset)
        self.assertEqual(contract_asset.resolve(), package.implementation_contract_asset)
        self.assertEqual([contract_asset.resolve()], package.implementation_contract_assets)
        self.assertEqual("toy_complete_contract", package.implementation_contract["contract_id"])
        self.assertEqual(
            ["toy_decoder", "toy_search"],
            [item["component_id"] for item in package.implementation_contract["required_components"]],
        )

    def test_unknown_problem_family_does_not_get_standard_fjsp_slot_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "slots.json"

            with self.assertRaisesRegex(ValueError, "no slot manifest edit strategy"):
                write_default_slot_manifest(problem_family="unknown_variant", output=output)

    def test_auto_knowledge_cards_uses_domain_pack_feature_tags(self) -> None:
        cards = auto_knowledge_cards(
            problem_family="standard_fjsp",
            problem_family_tags=["sdst", "weight_update"],
            slot_manifest=None,
        )
        card_paths = {str(path.relative_to(ROOT)).replace("\\", "/") for path in cards if path.is_relative_to(ROOT)}

        self.assertIn("knowledge/benchmarks/standard_fjsp_format.md", card_paths)
        self.assertIn("knowledge/principles/fjsp_variant_domain_pack_rag.md", card_paths)
        self.assertIn("knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md", card_paths)
        self.assertIn("knowledge/papers/awls_sdst_hudata20_baseline_notes.md", card_paths)
        self.assertIn("knowledge/papers/awls_sdst_weight_update_notes.md", card_paths)

    def test_context_packet_embeds_domain_pack_capability_and_auto_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = tmp_path / "contract.json"
            instance = tmp_path / "tiny.fjs"
            instance.write_text((ROOT / "examples" / "standard_fjsp_tiny.fjs").read_text(encoding="utf-8"), encoding="utf-8")
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "domain_pack_context",
                        "problem_family": "fjsp_sdst",
                        "description": "domain pack context smoke",
                        "instances": [{"id": "tiny", "path": str(instance)}],
                        "objectives": [{"name": "makespan", "direction": "minimize"}],
                        "commands": {
                            "solver": "python solver.py",
                            "evaluator": "python evaluator.py",
                            "quick_test": "python -m compileall .",
                        },
                        "budget": {"rounds": 1, "seeds": [0]},
                        "paths": {"allowed_paths": ["examples"], "forbidden_paths": [".git"]},
                        "review": {"status": "confirmed"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=tmp_path / "context_packet.json",
                    hypothesis="Domain pack context smoke.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("standard_fjsp", packet["problem_family_capability"]["family_id"])
        self.assertEqual("standard_fjsp", packet["knowledge_selection"]["active_variant"])
        auto_cards = {Path(path).name for path in packet["auto_knowledge_cards"]}
        self.assertIn("standard_fjsp_format.md", auto_cards)
        self.assertIn("fjsp_variant_domain_pack_rag.md", auto_cards)
        self.assertNotIn("fjsp_sdst_agent_generated_search_memory_20260707.md", auto_cards)
        self.assertNotIn("awls_sdst_hudata20_baseline_notes.md", auto_cards)

    def test_context_packet_adds_agent_generated_solver_quality_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = tmp_path / "contract.json"
            instance = tmp_path / "tiny.fjs"
            instance.write_text((ROOT / "examples" / "standard_fjsp_tiny.fjs").read_text(encoding="utf-8"), encoding="utf-8")
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "agent_generated_context",
                        "problem_family": "FJSP",
                        "description": "agent-generated solver context smoke",
                        "instances": [{"id": "tiny", "path": str(instance)}],
                        "objectives": [{"name": "makespan", "direction": "minimize"}],
                        "commands": {
                            "solver": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
                            "evaluator": "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}",
                            "quick_test": "python -m py_compile examples/agent_generated_fjsp_solver.py",
                        },
                        "budget": {"rounds": 1, "seeds": [0]},
                        "paths": {"allowed_paths": ["examples"], "forbidden_paths": [".git"]},
                        "review": {"status": "confirmed"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=tmp_path / "context_packet.json",
                    hypothesis="Agent-generated context smoke.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        auto_cards = {Path(path).name for path in packet["auto_knowledge_cards"]}
        self.assertIn("agent_generated_variant_quality_contracts.md", auto_cards)
        self.assertIn("solver_contract.md", auto_cards)
        self.assertIn("standard_fjsp_agent_generated_reference_skeleton.md", auto_cards)
        self.assertNotIn("standard_fjsp_agent_generated_neighborhood_templates.md", auto_cards)
        self.assertNotIn("standard_fjsp_awls_hgtsa_execution_skeleton.md", auto_cards)
        self.assertNotIn("fjsp_sdst_agent_generated_search_memory_20260707.md", auto_cards)
        self.assertNotIn("decoder_neighborhood.md", auto_cards)
        catalog = packet["method_package_catalog"]
        self.assertEqual("standard_fjsp_awls_hgtsa", catalog["recommended_package_id"])
        self.assertEqual(
            ["standard_fjsp_awls_hgtsa"],
            [item["package_id"] for item in catalog["packages"]],
        )
        self.assertTrue(
            any(str(path).endswith("reference_solver.py") for path in catalog["packages"][0]["assets"])
        )

    def test_standard_fjsp_agent_generated_code_template_cards_exist(self) -> None:
        skeleton = (
            ROOT
            / "knowledge"
            / "imported_huawei_fjsp_knowledge"
            / "operators"
            / "standard_fjsp_agent_generated_reference_skeleton.md"
        )
        neighborhood = (
            ROOT
            / "knowledge"
            / "imported_huawei_fjsp_knowledge"
            / "operators"
            / "standard_fjsp_agent_generated_neighborhood_templates.md"
        )

        skeleton_text = skeleton.read_text(encoding="utf-8")
        neighborhood_text = neighborhood.read_text(encoding="utf-8")

        self.assertIn("def parse_instance", skeleton_text)
        self.assertIn("def initial_ready_list_state", skeleton_text)
        self.assertIn("def decode_state", skeleton_text)
        self.assertIn("def coverage_ok", skeleton_text)
        self.assertIn("def apply_sequence_move", neighborhood_text)
        self.assertIn("def critical_tail_windows", neighborhood_text)
        self.assertIn("def reverse_move_signature", neighborhood_text)
        self.assertIn("def tabu_best_improvement", neighborhood_text)

    def test_standard_fjsp_skeleton_card_contains_executable_neighborhood_templates(self) -> None:
        card = (
            ROOT
            / "knowledge"
            / "imported_huawei_fjsp_knowledge"
            / "operators"
            / "standard_fjsp_awls_hgtsa_execution_skeleton.md"
        )
        text = card.read_text(encoding="utf-8")

        self.assertIn("def generate_n8_like_neighbors", text)
        self.assertIn("def generate_k_insertion_neighbors", text)
        self.assertIn("def tabu_search", text)
        self.assertIn("def perturb_state", text)
        self.assertIn("diversification", text)

    def test_method_package_catalog_filters_standard_and_sdst_packages(self) -> None:
        standard = method_package_catalog(problem_family="FJSP", active_features=[])
        sdst = method_package_catalog(
            problem_family="FJSP",
            active_features=["fjsp_sdst", "sequence_dependent_setup", "setup_time"],
        )

        self.assertEqual("standard_fjsp_awls_hgtsa", standard["recommended_package_id"])
        self.assertEqual(
            ["standard_fjsp_awls_hgtsa"],
            [item["package_id"] for item in standard["packages"]],
        )
        self.assertEqual("fjsp_sdst_awls_adaptation", sdst["recommended_package_id"])
        self.assertEqual(
            ["fjsp_sdst_awls_adaptation"],
            [item["package_id"] for item in sdst["packages"]],
        )
        standard_component_ids = {
            item["component_id"]
            for item in standard["packages"][0]["implementation_contract"]["required_components"]
        }
        sdst_component_ids = {
            item["component_id"]
            for item in sdst["packages"][0]["implementation_contract"]["required_components"]
        }
        self.assertNotIn("sdst_setup_semantics_and_decoder", standard_component_ids)
        self.assertTrue(
            {
                "sdst_setup_semantics_and_decoder",
                "sdst_setup_aware_neighborhood_evaluation",
                "sdst_critical_timing_and_adaptation",
            }.issubset(sdst_component_ids)
        )
        self.assertGreater(len(sdst_component_ids), len(standard_component_ids))
        contract_sources = [str(path).replace("\\", "/") for path in sdst["packages"][0]["implementation_contract_assets"]]
        self.assertTrue(any("standard_fjsp_awls_hgtsa/implementation_contract.json" in path for path in contract_sources))
        self.assertTrue(any("fjsp_sdst_awls_adaptation/implementation_contract.json" in path for path in contract_sources))


if __name__ == "__main__":
    unittest.main()
