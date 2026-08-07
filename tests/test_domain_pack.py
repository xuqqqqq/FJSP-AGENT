from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.domains.pack import get_domain_pack, load_domain_pack, load_domain_packs
from harness_agent.context.knowledge import (
    auto_knowledge_cards,
    knowledge_query_catalog,
    method_family_catalog,
    method_package_catalog,
    resolve_method_package,
    resolve_worker_implementation_skills,
    select_knowledge_cards,
    select_tagged_knowledge_cards,
    selection_cards,
)
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
        self.assertTrue(method_package.selection_enabled)
        self.assertIn("local_search", method_package.activation_tags)
        self.assertTrue(any(path.name == "behavior_contract.md" for path in method_package.semantic_assets))
        self.assertFalse(any(path.name == "reference_solver.py" for path in method_package.semantic_assets))
        self.assertEqual(
            [
                "fjsp_instance_feature_method_router.md",
                "fjsp_method_selection_zh.md",
                "fjsp_scene_survey_2025_10_17.md",
            ],
            [path.name for path in pack.selection_cards("strategy")],
        )
        self.assertEqual(
            ["fjsp_instance_feature_method_router.md", "fjsp_method_selection_zh.md"],
            [path.name for path in pack.selection_cards("direction")],
        )
        self.assertIsNotNone(pack.method_family("constructive_search"))
        self.assertIsNotNone(pack.worker_implementation_skill("fjsp-constructive-search-worker"))
        high_flexibility_skill = pack.worker_implementation_skill("high-flexibility-fjsp-playbook")
        self.assertIsNotNone(high_flexibility_skill)
        assert high_flexibility_skill is not None
        self.assertTrue(high_flexibility_skill.require_activation_tag_match)
        self.assertTrue((high_flexibility_skill.source_path / "SKILL.md").is_file())

    def test_worker_skills_match_multiple_canonical_families_without_unselected_skills(self) -> None:
        catalog = method_family_catalog(problem_family="FJSP")
        self.assertEqual("ok", catalog["status"])
        self.assertIn("constructive_search", {item["family_id"] for item in catalog["families"]})

        resolved = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=[
                {"id": "constructive_search", "role": "primary"},
                {"id": "coupled_local_search", "role": "complementary"},
            ],
            knowledge_query_tags=["beam_search", "assignment_aware_local_search"],
        )

        self.assertEqual("ok", resolved["status"])
        self.assertEqual(
            ["constructive_search", "coupled_local_search"],
            [item["id"] for item in resolved["method_families"]],
        )
        skill_ids = [item["skill_id"] for item in resolved["skills"]]
        self.assertIn("fjsp-experiment-design-worker", skill_ids)
        self.assertIn("fjsp-solver-foundation-worker", skill_ids)
        self.assertIn("fjsp-constructive-search-worker", skill_ids)
        self.assertIn("fjsp-coupled-local-search-worker", skill_ids)
        self.assertNotIn("fjsp-exact-hybrid-worker", skill_ids)
        self.assertNotIn("fjsp-sdst-adapter-worker", skill_ids)
        self.assertEqual([], resolved["audit"]["uncovered_method_families"])

    def test_high_flexibility_worker_skill_requires_matching_query_tag(self) -> None:
        ordinary = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["constructive_search"],
            knowledge_query_tags=["construction", "beam_search"],
        )
        constructive = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["constructive_search"],
            knowledge_query_tags=["high_flexibility", "assignment_regret"],
        )
        local = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["coupled_local_search"],
            knowledge_query_tags=["assignment_trust_region"],
        )

        self.assertNotIn(
            "high-flexibility-fjsp-playbook",
            [item["skill_id"] for item in ordinary["skills"]],
        )
        self.assertIn(
            {"skill_id": "high-flexibility-fjsp-playbook", "reason": "activation_tag_mismatch"},
            ordinary["audit"]["excluded_skills"],
        )
        self.assertIn(
            "high-flexibility-fjsp-playbook",
            [item["skill_id"] for item in constructive["skills"]],
        )
        self.assertIn(
            "high-flexibility-fjsp-playbook",
            [item["skill_id"] for item in local["skills"]],
        )

    def test_high_flexibility_worker_skill_excludes_sdst(self) -> None:
        resolved = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["constructive_search", "coupled_local_search"],
            active_features=["sequence_dependent_setup"],
            knowledge_query_tags=["high_flexibility", "assignment_trust_region"],
        )

        self.assertNotIn(
            "high-flexibility-fjsp-playbook",
            [item["skill_id"] for item in resolved["skills"]],
        )
        self.assertIn(
            {"skill_id": "high-flexibility-fjsp-playbook", "reason": "feature_incompatible"},
            resolved["audit"]["excluded_skills"],
        )

    def test_high_flexibility_route_uses_exact_assignment_first_contract(self) -> None:
        skill_text = (
            ROOT / ".codex" / "skills" / "high-flexibility-fjsp-playbook" / "SKILL.md"
        ).read_text(encoding="utf-8")
        card_text = (
            ROOT
            / "knowledge"
            / "references"
            / "standard_fjsp"
            / "high_flexibility_assignment_first_playbook.md"
        ).read_text(encoding="utf-8")
        router_text = (
            ROOT
            / "knowledge"
            / "references"
            / "general_fjsp"
            / "fjsp_instance_feature_method_router.md"
        ).read_text(encoding="utf-8")

        self.assertIn("pressure = (候选机器数 - 1) * duration_span", skill_text)
        self.assertIn(
            "pressure(op) = (candidate_count(op) - 1) * duration_span(op)",
            card_text,
        )
        for text in (skill_text, card_text):
            self.assertIn("assignment_cost", text)
            self.assertIn("theoretical_fastest", text)
            self.assertIn("完整 score 元组", text)
            self.assertIn("order_rank_edges_preserved", text)

        self.assertIn("high_flexibility_assignment_first_playbook.md", router_text)
        self.assertNotIn("high_flexibility_idle_critical_beam_blueprint.md", router_text)

    def test_worker_skill_activation_tags_are_queryable_by_a_compatible_family(self) -> None:
        pack = get_domain_pack("FJSP")
        self.assertIsNotNone(pack)
        assert pack is not None
        public_tags = {
            item["tag"]
            for item in knowledge_query_catalog(problem_family="FJSP")["tags"]
        }
        family_tags = {
            family.family_id: set(family.query_tags)
            for family in pack.method_families
        }

        for skill in pack.worker_implementation_skills:
            if not skill.require_activation_tag_match:
                continue
            compatible_tags = {
                tag
                for family_id in skill.method_families
                for tag in family_tags.get(family_id, set())
            }
            for tag in set(skill.activation_tags).intersection(public_tags):
                self.assertIn(
                    tag,
                    compatible_tags,
                    msg=f"{skill.skill_id} exposes {tag} but no compatible family can query it",
                )

    def test_domain_pack_loader_rejects_unqueryable_worker_skill_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = tmp_path / "domain_pack.json"
            manifest.write_text(
                json.dumps(
                    {
                        "family_id": "broken",
                        "knowledge": {
                            "knowledge_query": {
                                "tag_descriptions": {
                                    "construction": "constructive search",
                                    "assignment_regret": "assignment pressure",
                                }
                            }
                        },
                        "method_families": [
                            {
                                "family_id": "constructive_search",
                                "query_tags": ["construction"],
                            }
                        ],
                        "worker_implementation_skills": [
                            {
                                "skill_id": "broken-skill",
                                "source_path": ".codex/skills/broken-skill",
                                "method_families": ["constructive_search"],
                                "activation_tags": ["assignment_regret"],
                                "require_activation_tag_match": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "no compatible family can query"):
                load_domain_pack(manifest, project_root=tmp_path)

    def test_worker_skill_matching_uses_feature_gate_for_sdst_adapter(self) -> None:
        standard = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["coupled_local_search"],
        )
        sdst = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["coupled_local_search"],
            active_features=["sequence_dependent_setup"],
            knowledge_query_tags=["sdst", "setup_time"],
        )
        min_lag = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["coupled_local_search"],
            active_features=["minimum_time_lag", "time_lag"],
            knowledge_query_tags=["minimum_time_lag", "lag_aware_decoder"],
        )
        self.assertNotIn(
            "fjsp-sdst-adapter-worker",
            [item["skill_id"] for item in standard["skills"]],
        )
        self.assertIn(
            "fjsp-experiment-design-worker",
            [item["skill_id"] for item in standard["skills"]],
        )
        self.assertIn(
            "fjsp-sdst-adapter-worker",
            [item["skill_id"] for item in sdst["skills"]],
        )
        self.assertNotIn(
            "fjsp-min-time-lag-adapter-worker",
            [item["skill_id"] for item in standard["skills"]],
        )
        self.assertNotIn(
            "fjsp-min-time-lag-adapter-worker",
            [item["skill_id"] for item in sdst["skills"]],
        )
        self.assertIn(
            "fjsp-min-time-lag-adapter-worker",
            [item["skill_id"] for item in min_lag["skills"]],
        )
        self.assertNotIn(
            "fjsp-sdst-adapter-worker",
            [item["skill_id"] for item in min_lag["skills"]],
        )

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
                            "selection_cards": {
                                "strategy": [str(card)],
                                "direction": [str(card)],
                            },
                            "knowledge_query": {
                                "default_limit": 3,
                                "exclude_path_markers": ["forbidden_marker.md"],
                            },
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
        self.assertEqual([card.resolve()], pack.selection_cards("strategy"))
        self.assertEqual([card.resolve()], pack.selection_cards("direction"))
        self.assertEqual(3, pack.knowledge_query_default_limit)
        self.assertEqual(["forbidden_marker.md"], pack.knowledge_query_excluded_path_markers)
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
                        "component_dependencies": [
                            {
                                "component_id": "toy_search",
                                "depends_on": ["toy_decoder"],
                                "reason": "Toy search depends on toy decoding.",
                            }
                        ],
                        "coupled_groups": [
                            {
                                "group_id": "toy_loop",
                                "component_ids": ["toy_decoder", "toy_search"],
                                "rule": "Decoder and search must stay behaviorally aligned.",
                            }
                        ],
                        "competition_tracks": [
                            {
                                "track_id": "direct_evidence",
                                "component_ids": ["toy_decoder", "toy_search"],
                                "selection_hint": "Keep both toy components available to delegated lanes.",
                            }
                        ],
                        "checkpoint_checks": [
                            {
                                "check_id": "toy_legality",
                                "component_ids": ["toy_decoder", "toy_search"],
                                "requirement": "Toy decoding and search must remain behaviorally aligned.",
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
                                "selection_enabled": False,
                                "disabled_reason": "Historical audit only.",
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
        self.assertFalse(package.selection_enabled)
        self.assertEqual("Historical audit only.", package.disabled_reason)
        self.assertEqual(
            ["toy_decoder", "toy_search"],
            [item["component_id"] for item in package.implementation_contract["required_components"]],
        )
        self.assertEqual(
            ["toy_search"],
            [item["component_id"] for item in package.implementation_contract["component_dependencies"]],
        )
        self.assertEqual(
            ["direct_evidence"],
            [item["track_id"] for item in package.implementation_contract["competition_tracks"]],
        )
        self.assertEqual(
            ["toy_legality"],
            [item["check_id"] for item in package.implementation_contract["checkpoint_checks"]],
        )

    def test_domain_pack_resolves_relative_assets_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_dir = root / "domain_packs" / "toy"
            knowledge_dir = root / "knowledge" / "references" / "toy"
            package_dir = root / "knowledge" / "method_packages" / "toy_complete"
            pack_dir.mkdir(parents=True)
            knowledge_dir.mkdir(parents=True)
            package_dir.mkdir(parents=True)
            card = knowledge_dir / "card.md"
            implementation = package_dir / "reference_solver.py"
            contract = package_dir / "implementation_contract.json"
            card.write_text("# Toy card\n", encoding="utf-8")
            implementation.write_text("def solve():\n    return None\n", encoding="utf-8")
            contract.write_text(
                json.dumps(
                    {
                        "required_components": [
                            {
                                "component_id": "toy_solver",
                                "required_behaviors": ["Solve the declared toy problem."],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            manifest = pack_dir / "domain_pack.json"
            manifest.write_text(
                json.dumps(
                    {
                        "family_id": "toy",
                        "capability": {"display_name": "Toy"},
                        "knowledge": {"base_cards": ["knowledge/references/toy/card.md"]},
                        "method_packages": [
                            {
                                "package_id": "toy_complete",
                                "title": "Toy complete",
                                "assets": ["knowledge/references/toy/card.md"],
                                "implementation_asset": "knowledge/method_packages/toy_complete/reference_solver.py",
                                "implementation_contract_asset": "knowledge/method_packages/toy_complete/implementation_contract.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            pack = load_domain_pack(manifest, project_root=root)

        package = pack.method_package("toy_complete")
        self.assertEqual([card.resolve()], pack.base_cards)
        self.assertIsNotNone(package)
        assert package is not None
        self.assertEqual([card.resolve()], package.assets)
        self.assertEqual(implementation.resolve(), package.implementation_asset)
        self.assertEqual(contract.resolve(), package.implementation_contract_asset)

    def test_method_package_contract_extends_merges_parent_and_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_dir = root / "domain_packs" / "toy"
            parent_dir = root / "knowledge" / "method_packages" / "parent"
            child_dir = root / "knowledge" / "method_packages" / "child"
            pack_dir.mkdir(parents=True)
            parent_dir.mkdir(parents=True)
            child_dir.mkdir(parents=True)
            parent = parent_dir / "implementation_contract.json"
            child = child_dir / "implementation_contract.json"
            implementation = child_dir / "reference_solver.py"
            parent.write_text(
                json.dumps(
                    {
                        "required_components": [
                            {
                                "component_id": "decoder",
                                "required_behaviors": ["Decode every operation."],
                            },
                            {
                                "component_id": "search",
                                "required_behaviors": ["Search from a legal incumbent."],
                            },
                        ],
                        "coupled_groups": [
                            {
                                "group_id": "solve_loop",
                                "component_ids": ["decoder"],
                                "rule": "Decode every accepted move.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            child.write_text(
                json.dumps(
                    {
                        "extends": ["knowledge/method_packages/parent/implementation_contract.json"],
                        "required_components": [
                            {
                                "component_id": "decoder",
                                "required_behaviors": ["Preserve machine eligibility."],
                            }
                        ],
                        "coupled_groups": [
                            {
                                "group_id": "solve_loop",
                                "component_ids": ["search"],
                                "rule": "Decode every accepted move.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            implementation.write_text("def solve():\n    return None\n", encoding="utf-8")
            manifest = pack_dir / "domain_pack.json"
            manifest.write_text(
                json.dumps(
                    {
                        "family_id": "toy",
                        "capability": {"display_name": "Toy"},
                        "method_packages": [
                            {
                                "package_id": "child",
                                "title": "Child",
                                "implementation_asset": "knowledge/method_packages/child/reference_solver.py",
                                "implementation_contract_asset": "knowledge/method_packages/child/implementation_contract.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            package = load_domain_pack(manifest, project_root=root).method_package("child")

        self.assertIsNotNone(package)
        assert package is not None
        components = {
            item["component_id"]: item["required_behaviors"]
            for item in package.implementation_contract["required_components"]
        }
        self.assertEqual(
            ["Decode every operation.", "Preserve machine eligibility."],
            components["decoder"],
        )
        self.assertIn("search", components)
        self.assertEqual(
            ["decoder", "search"],
            package.implementation_contract["coupled_groups"][0]["component_ids"],
        )
        self.assertEqual([parent.resolve(), child.resolve()], package.implementation_contract_assets)

    def test_experiment_memory_is_excluded_from_default_knowledge_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stable = root / "knowledge" / "references" / "standard_fjsp" / "decoder.md"
            experiment = root / "knowledge" / "experiment_memory" / "agent_generated" / "run.md"
            stable.parent.mkdir(parents=True)
            experiment.parent.mkdir(parents=True)
            stable.write_text("# Stable decoder\n", encoding="utf-8")
            experiment.write_text("# One run\n", encoding="utf-8")
            pack = SimpleNamespace(
                family_id="standard_fjsp",
                base_cards=[stable, experiment],
                tagged_cards={},
            )

            with patch("harness_agent.context.knowledge.get_domain_pack", return_value=pack):
                selection = select_knowledge_cards(problem_family="standard_fjsp")

        self.assertEqual([stable], selection.cards)
        self.assertEqual(1, selection.audit["excluded_card_count"])
        self.assertEqual(
            "experiment_memory_requires_explicit_replay",
            selection.audit["excluded_cards"][0]["reason"],
        )

    def test_unknown_problem_family_does_not_get_standard_fjsp_slot_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "slots.json"

            with self.assertRaisesRegex(ValueError, "no slot manifest edit strategy"):
                write_default_slot_manifest(problem_family="unknown_variant", output=output)

    def test_auto_knowledge_cards_uses_stable_feature_cards_not_experiment_memory(self) -> None:
        cards = auto_knowledge_cards(
            problem_family="standard_fjsp",
            problem_family_tags=["sdst", "weight_update"],
            slot_manifest=None,
        )
        card_paths = {str(path.relative_to(ROOT)).replace("\\", "/") for path in cards if path.is_relative_to(ROOT)}

        self.assertIn("knowledge/benchmarks/standard_fjsp_format.md", card_paths)
        self.assertIn("knowledge/principles/fjsp_variant_domain_pack_rag.md", card_paths)
        self.assertIn("knowledge/references/sdst/awls_sdst_adapter_notes.md", card_paths)
        self.assertIn("knowledge/references/sdst/awls_sdst_agent_generated_transfer_notes.md", card_paths)
        self.assertIn(
            "knowledge/references/sdst/awls_sdst_adaptation_implementation.md",
            card_paths,
        )
        self.assertFalse(any(path.startswith("knowledge/experiment_memory/") for path in card_paths))

    def test_minimum_time_lag_diagnostics_select_only_variant_cards(self) -> None:
        diagnostics = {
            "status": "available",
            "summary": {
                "instance_count": 1,
                "profiled_count": 1,
                "min_time_lag_instance_count": 1,
                "sdst_instance_count": 0,
            },
            "instances": [{"variant": "fjsp_min_time_lag", "min_time_lag_constraint_count": 1}],
        }

        active = select_knowledge_cards(
            problem_family="standard_fjsp",
            instance_diagnostics=diagnostics,
            active_features=["fjsp_min_time_lag", "minimum_time_lag", "time_lag"],
        )
        standard = select_knowledge_cards(
            problem_family="standard_fjsp",
            active_features=[],
        )

        active_names = {path.name for path in active.cards}
        standard_names = {path.name for path in standard.cards}
        self.assertEqual("fjsp_min_time_lag", active.audit["active_variant"])
        self.assertIn("min_time_lag_semantics_and_decoder.md", active_names)
        self.assertIn("min_time_lag_search_adaptation.md", active_names)
        self.assertNotIn("min_time_lag_semantics_and_decoder.md", standard_names)
        self.assertNotIn("min_time_lag_search_adaptation.md", standard_names)
        semantics_card = next(path for path in active.cards if path.name == "min_time_lag_semantics_and_decoder.md")
        semantics_text = semantics_card.read_text(encoding="utf-8")
        self.assertIn("零权 SCC", semantics_text)
        self.assertIn("无正权环", semantics_text)

    def test_minimum_time_lag_constructive_package_is_eligible(self) -> None:
        catalog = method_package_catalog(
            problem_family="standard_fjsp",
            active_features=["fjsp_min_time_lag", "minimum_time_lag", "time_lag"],
            knowledge_query_tags=["constructive_search", "minimum_time_lag"],
        )

        package_ids = {item["package_id"] for item in catalog["packages"]}
        self.assertIn("fjsp_min_time_lag_constructive_adaptation", package_ids)
        self.assertNotIn("standard_fjsp_awls_hgtsa", package_ids)
        selected = next(
            item
            for item in catalog["packages"]
            if item["package_id"] == "fjsp_min_time_lag_constructive_adaptation"
        )
        self.assertEqual(3, len(selected["implementation_contract"]["competition_tracks"]))

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
        self.assertNotIn("fjsp_sdst_search_observation_20260723.md", auto_cards)
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
        self.assertNotIn("fjsp_sdst_search_observation_20260723.md", auto_cards)
        self.assertNotIn("decoder_neighborhood.md", auto_cards)
        catalog = packet["method_package_catalog"]
        self.assertIsNone(catalog["recommended_package_id"])
        self.assertEqual([], catalog["packages"])

    def test_standard_fjsp_agent_generated_code_template_cards_exist(self) -> None:
        skeleton = (
            ROOT
            / "knowledge"
            / "references"
            / "standard_fjsp"
            / "standard_fjsp_agent_generated_reference_skeleton.md"
        )
        neighborhood = (
            ROOT
            / "knowledge"
            / "references"
            / "standard_fjsp"
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
            / "references"
            / "standard_fjsp"
            / "standard_fjsp_awls_hgtsa_execution_skeleton.md"
        )
        text = card.read_text(encoding="utf-8")

        self.assertIn("def generate_n8_like_neighbors", text)
        self.assertIn("def generate_k_insertion_neighbors", text)
        self.assertIn("def tabu_search", text)
        self.assertIn("def perturb_state", text)
        self.assertIn("diversification", text)

    def test_method_package_catalog_routes_enabled_packages_by_direction_query(self) -> None:
        standard = method_package_catalog(problem_family="FJSP", active_features=[])
        sdst = method_package_catalog(
            problem_family="FJSP",
            active_features=["fjsp_sdst", "sequence_dependent_setup", "setup_time"],
        )
        min_lag = method_package_catalog(
            problem_family="FJSP",
            active_features=["fjsp_min_time_lag", "minimum_time_lag", "time_lag"],
        )
        construction = method_package_catalog(
            problem_family="FJSP",
            active_features=[],
            knowledge_query_tags=["construction", "decoder"],
        )
        local_search = method_package_catalog(
            problem_family="FJSP",
            active_features=[],
            knowledge_query_tags=["local_search", "critical_path"],
        )
        pack = get_domain_pack("FJSP")

        self.assertEqual("standard_fjsp_awls_hgtsa", standard["recommended_package_id"])
        self.assertEqual("fjsp_sdst_awls_adaptation", sdst["recommended_package_id"])
        self.assertEqual(
            "fjsp_min_time_lag_constructive_adaptation",
            min_lag["recommended_package_id"],
        )
        self.assertEqual(
            {
                "fjsp_min_time_lag_constructive_adaptation",
                "fjsp_min_time_lag_coupled_local_search",
                "fjsp_min_time_lag_exact_hybrid",
            },
            {item["package_id"] for item in min_lag["packages"]},
        )
        self.assertEqual([], construction["packages"])
        self.assertEqual("standard_fjsp_awls_hgtsa", local_search["recommended_package_id"])
        self.assertEqual(
            ["standard_fjsp_awls_hgtsa"],
            [item["package_id"] for item in local_search["packages"]],
        )
        sdst_local_search = method_package_catalog(
            problem_family="FJSP",
            active_features=["fjsp_sdst", "sequence_dependent_setup", "setup_time"],
            knowledge_query_tags=["local_search", "critical_path", "setup_time"],
        )
        self.assertEqual(
            ["fjsp_sdst_awls_adaptation"],
            [item["package_id"] for item in sdst_local_search["packages"]],
        )
        min_lag_local_search = method_package_catalog(
            problem_family="FJSP",
            active_features=["fjsp_min_time_lag", "minimum_time_lag", "time_lag"],
            knowledge_query_tags=["coupled_local_search", "critical_path", "minimum_time_lag"],
        )
        self.assertEqual(
            ["fjsp_min_time_lag_coupled_local_search"],
            [item["package_id"] for item in min_lag_local_search["packages"]],
        )
        min_lag_exact = method_package_catalog(
            problem_family="FJSP",
            active_features=["fjsp_min_time_lag", "minimum_time_lag", "time_lag"],
            knowledge_query_tags=["exact_hybrid", "cp_sat", "minimum_time_lag"],
        )
        self.assertEqual(
            ["fjsp_min_time_lag_exact_hybrid"],
            [item["package_id"] for item in min_lag_exact["packages"]],
        )
        self.assertIsNone(
            resolve_method_package(
                problem_family="FJSP",
                package_id=None,
                knowledge_query_tags=["local_search"],
            )
        )
        self.assertIsNone(
            resolve_method_package(
                problem_family="FJSP",
                package_id="unknown_package",
                knowledge_query_tags=["local_search"],
            )
        )
        self.assertIsNotNone(pack)
        assert pack is not None
        standard_package = pack.method_package("standard_fjsp_awls_hgtsa")
        sdst_package = pack.method_package("fjsp_sdst_awls_adaptation")
        self.assertIsNotNone(standard_package)
        self.assertIsNotNone(sdst_package)
        assert standard_package is not None
        assert sdst_package is not None
        self.assertTrue(standard_package.selection_enabled)
        self.assertTrue(sdst_package.selection_enabled)
        standard_component_ids = {
            item["component_id"]
            for item in standard_package.implementation_contract["required_components"]
        }
        sdst_component_ids = {
            item["component_id"]
            for item in sdst_package.implementation_contract["required_components"]
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
        contract_sources = [str(path).replace("\\", "/") for path in sdst_package.implementation_contract_assets]
        self.assertTrue(any("standard_fjsp_awls_hgtsa/implementation_contract.json" in path for path in contract_sources))
        self.assertTrue(any("fjsp_sdst_awls_adaptation/implementation_contract.json" in path for path in contract_sources))

    def test_selection_cards_and_second_stage_tagged_query_stay_generic(self) -> None:
        strategy_cards = selection_cards(problem_family="standard_fjsp", stage="strategy")
        direction_cards = selection_cards(problem_family="standard_fjsp", stage="direction")
        detailed = select_tagged_knowledge_cards(
            problem_family="standard_fjsp",
            knowledge_query_tags=["critical_path", "tabu_search", "sdst"],
        )

        self.assertEqual(
            [
                "fjsp_instance_feature_method_router.md",
                "fjsp_method_selection_zh.md",
                "fjsp_scene_survey_2025_10_17.md",
            ],
            [path.name for path in strategy_cards],
        )
        self.assertEqual(
            ["fjsp_instance_feature_method_router.md", "fjsp_method_selection_zh.md"],
            [path.name for path in direction_cards],
        )
        selected_names = [path.name for path in detailed.cards]
        self.assertLessEqual(len(selected_names), 6)
        self.assertEqual(len(selected_names), len(set(selected_names)))
        self.assertIn("standard_fjsp_algorithm_semantic_review_contract.md", selected_names)
        self.assertIn("critical_path_machine_block_neighborhood.md", selected_names)
        self.assertIn("standard_fjsp_agent_generated_neighborhood_templates.md", selected_names)
        self.assertNotIn("standard_fjsp_awls_hgtsa_execution_skeleton.md", selected_names)
        self.assertNotIn("xiejin_hgtsa_n8_k_insertion_tabu_spec.md", selected_names)
        self.assertNotIn("tabu_search_loop.md", selected_names)
        self.assertFalse(any("sdst" in name for name in selected_names))
        self.assertEqual("standard_fjsp", detailed.audit["active_variant"])
        self.assertEqual(6, detailed.audit["max_cards"])
        excluded_reasons = {item["reason"] for item in detailed.audit["excluded_cards"]}
        self.assertIn("inactive_sequence_dependent_setup", excluded_reasons)
        self.assertIn("domain_pack_secondary_query_exclusion", excluded_reasons)

        public_query_tags = {
            item["tag"] for item in knowledge_query_catalog(problem_family="standard_fjsp")["tags"]
        }
        self.assertTrue(
            {
                "construction",
                "constructive_search",
                "beam_search",
                "idle_gap",
                "critical_dispatch",
                "critical_path",
                "assignment_aware_local_search",
                "high_flexibility",
                "assignment_regret",
                "assignment_trust_region",
                "order_preserving_redecode",
                "cp_sat",
                "population",
            }.issubset(public_query_tags)
        )
        self.assertTrue(
            {"awls", "n7_neighborhood", "nk_neighborhood", "zi_features"}.isdisjoint(
                public_query_tags
            )
        )

        method_selection_text = (
            ROOT / "knowledge" / "references" / "general_fjsp" / "fjsp_method_selection_zh.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## 决策状态与伪代码", method_selection_text)
        self.assertIn("## 第一阶段验证清单", method_selection_text)
        self.assertIn("不要过早下结论的信号", method_selection_text)
        self.assertIn("fjsp_instance_feature_method_router.md", method_selection_text)

        high_flexibility = select_tagged_knowledge_cards(
            problem_family="standard_fjsp",
            knowledge_query_tags=["high_flexibility", "assignment_regret", "idle_gap"],
        )
        high_flexibility_names = [path.name for path in high_flexibility.cards]
        self.assertIn("high_flexibility_assignment_first_playbook.md", high_flexibility_names)
        self.assertNotIn("high_flexibility_idle_critical_beam_blueprint.md", high_flexibility_names)
        self.assertFalse(any("awls" in name or "hgtsa" in name for name in high_flexibility_names))

        beam = select_tagged_knowledge_cards(
            problem_family="standard_fjsp",
            knowledge_query_tags=["beam_search"],
        )
        beam_names = [path.name for path in beam.cards]
        self.assertIn("idle_critical_beam_implementation_template.md", beam_names)
        self.assertNotIn("high_flexibility_assignment_first_playbook.md", beam_names)
        self.assertFalse(
            (
                ROOT
                / "knowledge"
                / "references"
                / "standard_fjsp"
                / "high_flexibility_idle_critical_beam_blueprint.md"
            ).exists()
        )

    def test_second_stage_query_cannot_reactivate_awls_or_hgtsa_assets(self) -> None:
        detailed = select_tagged_knowledge_cards(
            problem_family="standard_fjsp",
            knowledge_query_tags=["adaptive_weight", "awls", "zi_features"],
        )

        selected_paths = [str(path).replace("\\", "/").lower() for path in detailed.cards]
        self.assertFalse(any("awls" in path or "hgtsa" in path for path in selected_paths))
        self.assertGreaterEqual(detailed.audit["excluded_card_count"], 1)


if __name__ == "__main__":
    unittest.main()
