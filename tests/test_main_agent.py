from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.agents.main import (
    DirectionPlanRequest,
    EvidenceDrivenMainAgent,
    bind_direction_plan_to_method_catalog,
    compact_main_agent_dynamic_context,
    enforce_improvement_direction_contract,
    normalize_direction_plan,
)


ROOT = Path(__file__).resolve().parents[1]


class MainAgentTests(unittest.TestCase):
    def test_evidence_main_agent_writes_one_bounded_direction_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "task_contract.example.json",
                    output_path=tmp_path / "context.json",
                    hypothesis="Improve one scheduling rule.",
                )
            )
            plan = EvidenceDrivenMainAgent().plan_direction(
                DirectionPlanRequest(
                    round_index=2,
                    context_packet_path=context_path,
                    loop_feedback={
                        "next_round_guidance": {
                            "must_do": ["Repair the decoder before tuning."],
                            "preserve": ["Keep the promoted parser."],
                            "avoid": ["Do not return partial schedules."],
                        }
                    },
                    output_dir=tmp_path / "main_agent",
                )
            )

            stored = json.loads(Path(plan["artifact_path"]).read_text(encoding="utf-8"))

            self.assertEqual("d002", stored["direction_id"])
            self.assertEqual("repair_rule", stored["strategy_type"])
            self.assertEqual(["Repair the decoder before tuning."], stored["change_scope"])
            self.assertIn("Keep the promoted parser.", stored["preserve"])
            self.assertEqual("", stored["method_package_id"])

    def test_direction_plan_normalization_never_accepts_unbounded_lists(self) -> None:
        plan = normalize_direction_plan(
            {
                "hypothesis": "x" * 5000,
                "change_scope": [f"change {index}" for index in range(40)],
                "knowledge_paths": [f"knowledge/{index}.md" for index in range(40)],
            },
            round_index=1,
        )

        self.assertLessEqual(len(plan["hypothesis"]), 1200)
        self.assertEqual(8, len(plan["change_scope"]))
        self.assertEqual(12, len(plan["knowledge_paths"]))

    def test_evidence_main_agent_selects_recommended_agent_generated_method_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = json.loads((ROOT / "configs" / "standard_fjsp_tiny.example.json").read_text(encoding="utf-8"))
            contract["commands"]["solver"] = (
                "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}"
            )
            contract_path = tmp_path / "contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract_path,
                    output_path=tmp_path / "context.json",
                )
            )

            plan = EvidenceDrivenMainAgent().plan_direction(
                DirectionPlanRequest(
                    round_index=-1,
                    context_packet_path=context_path,
                    loop_feedback={"round_type": "agent_generated_baseline"},
                    output_dir=tmp_path / "main_agent",
                )
            )

        self.assertEqual("baseline_constructor", plan["strategy_type"])
        self.assertEqual("standard_fjsp_awls_hgtsa", plan["method_package_id"])
        self.assertIn("reference_solver.py", " ".join(plan["knowledge_paths"]))

    def test_selected_method_package_binds_full_implementation_bundle(self) -> None:
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(
                {
                    "title": "Adapt synthetic package",
                    "strategy_type": "baseline_constructor",
                    "change_scope": ["Start from the synthetic package."],
                    "acceptance_checks": ["Pass evaluator legality."],
                    "method_package_id": "toy_complete_bundle",
                },
                round_index=-1,
            ),
            context={
                "method_package_catalog": {
                    "recommended_package_id": "toy_complete_bundle",
                    "packages": [
                        {
                            "package_id": "toy_complete_bundle",
                            "assets": [
                                "knowledge/method_packages/toy_complete/README.md",
                                "knowledge/method_packages/toy_complete/reference_solver.py",
                            ],
                            "implementation_contract_asset": (
                                "knowledge/method_packages/toy_complete/implementation_contract.json"
                            ),
                            "implementation_contract": {
                                "contract_id": "toy_complete_contract",
                                "mode": "complete_method_package",
                                "completion_rule": "Implement every required component.",
                                "variant_rule": "Keep toy constraints active.",
                                "required_components": [
                                    {"component_id": "toy_decoder", "title": "Toy decoder"},
                                    {"component_id": "toy_search", "title": "Toy search"},
                                ],
                                "coupled_groups": [
                                    {
                                        "group_id": "toy_loop",
                                        "component_ids": ["toy_decoder", "toy_search"],
                                        "rule": "Decoder output must feed the search.",
                                    }
                                ],
                            },
                        }
                    ],
                }
            },
        )

        self.assertEqual("toy_complete_bundle", plan["method_package_id"])
        self.assertEqual(
            "toy_complete_contract",
            plan["implementation_bundle"]["contract_id"],
        )
        self.assertEqual(
            ["toy_decoder", "toy_search"],
            [item["component_id"] for item in plan["implementation_bundle"]["required_components"]],
        )
        self.assertIn(
            "Implement and verify the complete selected method bundle in one coherent direction: toy_decoder, toy_search",
            plan["change_scope"],
        )
        self.assertIn(
            "Every required component in implementation_bundle must have reachable source evidence; partial package implementation is not complete.",
            plan["acceptance_checks"],
        )
        self.assertIn("reference_solver.py", " ".join(plan["knowledge_paths"]))
        self.assertEqual(
            "knowledge/method_packages/toy_complete/implementation_contract.json",
            plan["knowledge_paths"][0],
        )

    def test_main_agent_dynamic_context_keeps_incumbent_history_and_core_anchor(self) -> None:
        rendered = compact_main_agent_dynamic_context(
            context={
                "incumbent_code_context": {
                    "source": "promoted_incumbent_worktree",
                    "files": [{"relative_path": "solver.py", "snippet": "def x(): pass\n" * 1000}],
                },
                "knowledge_cards": [],
            },
            loop_feedback={
                "round_index": 2,
                "baseline_key": [-3695.0],
                "incumbent_key_before": [-3644.0],
                "agent_generated_baseline_memory": {
                    "accepted_as_incumbent": True,
                    "baseline_key": [-3695.0],
                    "best_core_valid_anchor": {
                        "objective_key": [-2596.0],
                        "semantic_status": "repair_required",
                        "promotion_eligible": False,
                    },
                },
                "previous_rounds": [
                    {
                        "round_index": 0,
                        "decision": "promoted",
                        "candidate_key": [-3644.0],
                        "incumbent_key_after": [-3644.0],
                        "direction_plan": {
                            "title": "Critical-block refinement",
                            "strategy_type": "local_search_operator",
                        },
                    }
                ],
            },
        )

        payload = json.loads(rendered)
        feedback = payload["loop_feedback"]
        self.assertEqual([-3644.0], feedback["incumbent_key_before"])
        self.assertEqual("Critical-block refinement", feedback["previous_rounds"][0]["title"])
        self.assertEqual(
            [-2596.0],
            feedback["agent_generated_baseline_memory"]["best_core_valid_anchor"]["objective_key"],
        )

    def test_post_baseline_direction_cannot_restart_from_constructor(self) -> None:
        plan = enforce_improvement_direction_contract(
            normalize_direction_plan(
                {
                    "title": "Rebuild earliest finish baseline",
                    "strategy_type": "baseline_constructor",
                    "change_scope": ["Replace the solver with earliest finish construction."],
                },
                round_index=1,
            ),
            round_index=1,
            loop_feedback={
                "incumbent_key_before": [-3644.0],
                "next_round_guidance": {"must_do": ["Refine one incumbent neighborhood operator."]},
            },
        )

        self.assertEqual("local_search_operator", plan["strategy_type"])
        self.assertEqual(["Refine one incumbent neighborhood operator."], plan["change_scope"])
        self.assertTrue(any("Preserve the promoted incumbent" in item for item in plan["preserve"]))
        self.assertTrue(any("strictly better" in item for item in plan["acceptance_checks"]))


if __name__ == "__main__":
    unittest.main()
