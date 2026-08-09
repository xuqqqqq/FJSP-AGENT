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
    configure_structured_worker_lanes,
    configure_exact_probe_tournament,
    enforce_improvement_direction_contract,
    fallback_improvement_order,
    fallback_research_context,
    fallback_tournament_families,
    normalize_activation_checks,
    normalize_direction_plan,
    should_reserve_exact_probe,
)


ROOT = Path(__file__).resolve().parents[1]


class MainAgentTests(unittest.TestCase):
    def test_low_flexibility_tournament_prioritizes_sequence_exact_and_memetic(self) -> None:
        context = {
            "instance_diagnostics": {
                "summary": {
                    "avg_candidate_count": 1.2,
                    "avg_flexible_operation_ratio": 0.066667,
                }
            },
            "method_family_catalog": {
                "families": [
                    {"family_id": "constructive_search"},
                    {"family_id": "coupled_local_search"},
                    {"family_id": "exact_hybrid"},
                    {"family_id": "population_memetic"},
                ]
            },
        }

        selected = fallback_tournament_families(context)

        self.assertEqual(
            ["coupled_local_search", "exact_hybrid", "population_memetic"],
            [item["id"] for item in selected],
        )

    def test_structured_fast_main_configures_requested_parallel_lanes(self) -> None:
        plan = configure_structured_worker_lanes(
            {
                "experiment_stage": "probe",
                "candidate_variants": [{"candidate_id": "model-supplied"}],
                "activation_checks": [{"path": "diagnostics.moves"}],
            },
            round_index=0,
            loop_feedback={"competition": {"max_competing_workers": 3}},
        )

        self.assertEqual("delegated_to_worker", plan["worker_lane_policy"]["mechanism_selection"])
        self.assertEqual(3, plan["worker_lane_policy"]["lane_count"])
        self.assertEqual([], plan["candidate_variants"])
        self.assertEqual([], plan["activation_checks"])

    def test_small_instance_reserves_independently_bound_exact_lane(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "instance_diagnostics": {
                "status": "available",
                "summary": {
                    "profiled_count": 1,
                    "max_operation_count": 15,
                    "avg_candidate_count": 2.2,
                    "avg_flexible_operation_ratio": 0.933333,
                },
                "instances": [{"operation_count": 15}],
            },
            "method_family_catalog": {
                "families": [
                    {"family_id": "constructive_search"},
                    {"family_id": "coupled_local_search"},
                    {"family_id": "exact_hybrid"},
                    {"family_id": "population_memetic"},
                ]
            },
            "method_package_catalog": {
                "active_features": ["machine_availability", "machine_calendar"]
            },
        }
        base = {
            "direction_id": "d000",
            "method_family": "coupled_local_search",
            "method_families": [{"id": "coupled_local_search", "role": "primary"}],
            "method_package_id": "standard_fjsp_awls_hgtsa",
            "hypothesis": "Improve the incumbent with coupled moves.",
            "experiment_stage": "probe",
        }

        self.assertTrue(should_reserve_exact_probe(context, max_workers=3))
        plan = configure_exact_probe_tournament(base, context=context, max_workers=3)

        self.assertTrue(plan["exact_probe_policy"]["reserved"])
        self.assertEqual(
            ["coupled_local_search", "exact_hybrid", "population_memetic"],
            [item["method_family"] for item in plan["candidate_variants"]],
        )
        exact = plan["candidate_variants"][1]
        self.assertEqual("fjsp_machine_availability_adaptation", exact["method_package_id"])
        self.assertIn("objective bound", exact["worker_objective"])

    def test_large_high_flexibility_instance_does_not_force_exact_lane(self) -> None:
        context = {
            "instance_diagnostics": {
                "summary": {
                    "max_operation_count": 400,
                    "avg_candidate_count": 4.0,
                    "avg_flexible_operation_ratio": 1.0,
                }
            },
            "method_family_catalog": {"families": [{"family_id": "exact_hybrid"}]},
        }

        self.assertFalse(should_reserve_exact_probe(context, max_workers=3))

    def test_bounded_high_flexibility_downtime_model_reserves_exact_lane(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "instance_diagnostics": {
                "summary": {
                    "max_operation_count": 240,
                    "avg_candidate_count": 2.983333,
                    "avg_flexible_operation_ratio": 0.920833,
                    "max_unavailability_interval_count": 97,
                    "sdst_instance_count": 0,
                },
                "instances": [
                    {
                        "operation_count": 240,
                        "avg_candidate_count": 2.983333,
                        "setup_time_kind": "none",
                    }
                ],
            },
            "method_family_catalog": {
                "families": [
                    {"family_id": "constructive_search"},
                    {"family_id": "exact_hybrid"},
                    {"family_id": "coupled_local_search"},
                ]
            },
            "method_package_catalog": {
                "active_features": ["machine_availability", "machine_calendar"]
            },
        }
        base = {
            "direction_id": "d000",
            "method_family": "constructive_search",
            "method_families": [{"id": "constructive_search", "role": "primary"}],
            "experiment_stage": "probe",
            "avoid": [
                "Do not edit evaluator semantics.",
                "Do not introduce CP-SAT or exact_hybrid in this constructive lane.",
                "不要引入精确模型。",
            ],
        }

        self.assertTrue(should_reserve_exact_probe(context, max_workers=3))
        plan = configure_exact_probe_tournament(base, context=context, max_workers=3)

        exact = next(
            item
            for item in plan["candidate_variants"]
            if item["method_family"] == "exact_hybrid"
        )
        self.assertEqual(["Do not edit evaluator semantics."], exact["avoid"])
        self.assertEqual(["exact_active_variant_cp_sat_probe"], exact["implementation_order"])
        self.assertEqual("exact_active_variant_cp_sat_probe", exact["deliverables"][0]["id"])
        self.assertIn("bounded parallel CP-SAT search", exact["deliverables"][0]["behavior"])
        self.assertIn("num_search_workers", exact["deliverables"][0]["evidence_required"])
        self.assertEqual("small_instance_or_bounded_exact_model", plan["exact_probe_policy"]["reason"])

    def test_high_flexibility_model_above_interval_threshold_does_not_force_exact_lane(self) -> None:
        context = {
            "instance_diagnostics": {
                "summary": {
                    "max_operation_count": 250,
                    "avg_candidate_count": 5.0,
                    "avg_flexible_operation_ratio": 1.0,
                    "max_unavailability_interval_count": 200,
                },
                "instances": [{"operation_count": 250, "avg_candidate_count": 5.0}],
            },
            "method_family_catalog": {"families": [{"family_id": "exact_hybrid"}]},
        }

        self.assertFalse(should_reserve_exact_probe(context, max_workers=3))

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
            self.assertEqual("research_tournament", stored["experiment_stage"])
            self.assertGreaterEqual(len(stored["candidate_variants"]), 2)
            self.assertGreaterEqual(
                len({item["method_family"] for item in stored["candidate_variants"]}),
                2,
            )
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

    def test_direction_plan_normalizes_at_most_four_competing_variants(self) -> None:
        plan = normalize_direction_plan(
            {
                "candidate_variants": [
                    {
                        "candidate_id": f"candidate {index}",
                        "hypothesis": f"test variant {index}",
                        "next_mutation": {"change": f"change symbol {index}"},
                    }
                    for index in range(6)
                ]
            },
            round_index=1,
        )

        self.assertEqual(4, len(plan["candidate_variants"]))
        self.assertEqual("candidate-0", plan["candidate_variants"][0]["candidate_id"])

    def test_activation_checks_are_machine_checkable_and_bounded(self) -> None:
        checks = normalize_activation_checks(
            [
                {
                    "id": "expanded",
                    "path": "best_metrics.solver_evidence.diagnostics.expanded_states",
                    "operator": "gt",
                    "expected": 0,
                },
                {"path": "best_metrics.flag", "operator": "unsupported"},
            ]
        )

        self.assertEqual(1, len(checks))
        self.assertEqual("expanded", checks[0]["id"])
        self.assertEqual("gt", checks[0]["operator"])
        self.assertTrue(checks[0]["required"])

    def test_direction_plan_preserves_detailed_analysis_fields(self) -> None:
        plan = normalize_direction_plan(
            {
                "observed_shortcomings": ["Reassignment checks too few insertion positions."],
                "reasoning_trace": [
                    {
                        "stage": "结构观察",
                        "summary": "Beam exists but is narrow.",
                        "evidence": ["beam_width=3"],
                        "inference": "State diversity may collapse early.",
                        "decision": "Do not rebuild Beam.",
                        "next_check": "Measure expanded states.",
                    }
                ],
                "incumbent_assessment": {
                    "verified_capabilities": ["Beam construction is reachable."],
                    "implementation_limits": ["beam_width is bounded by 3."],
                    "bottleneck_hypotheses": ["State diversity collapses too early."],
                    "evidence_refs": ["examples/solver.py:712 beam_width"],
                    "unknowns": ["Per-layer deduplication rate is not measured."],
                },
                "evidence_summary": ["Round 0 was legal but rolled back at makespan 2300."],
                "direction_judgment": "Keep the decoder and widen only the critical reassignment neighborhood.",
                "next_mutation": {
                    "target_symbols": ["solve.beam_width"],
                    "change": "Scale the existing Beam under the shared deadline.",
                    "preserve": ["Complete decoding."],
                    "expected_effect": "Retain more structurally distinct partial schedules.",
                    "falsification_metrics": ["expanded states", "makespan", "runtime"],
                },
            },
            round_index=1,
        )

        self.assertEqual(1, len(plan["observed_shortcomings"]))
        self.assertEqual("结构观察", plan["reasoning_trace"][0]["stage"])
        self.assertEqual(1, len(plan["evidence_summary"]))
        self.assertIn("widen", plan["direction_judgment"])
        self.assertEqual(["beam_width is bounded by 3."], plan["incumbent_assessment"]["implementation_limits"])
        self.assertEqual(["solve.beam_width"], plan["next_mutation"]["target_symbols"])

    def test_evidence_main_agent_uses_generic_query_when_no_method_package_is_enabled(self) -> None:
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
        self.assertEqual("", plan["method_package_id"])
        self.assertEqual([], plan["knowledge_paths"])
        self.assertIn("initialization", plan["knowledge_query"])

    def test_evidence_main_agent_prefers_high_flex_query_for_high_flex_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            payload = json.loads(context_path.read_text(encoding="utf-8"))
            payload["instance_diagnostics"]["summary"].update(
                {
                    "avg_candidate_count": 5.015504,
                    "avg_flexible_operation_ratio": 0.992248,
                    "avg_duration_spread_ratio": 0.137452,
                    "max_machine_eligibility_cv": 0.054545,
                    "max_fractional_min_load_cv": 0.07614,
                }
            )
            context_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            plan = EvidenceDrivenMainAgent().plan_direction(
                DirectionPlanRequest(
                    round_index=-1,
                    context_packet_path=context_path,
                    loop_feedback={"round_type": "agent_generated_baseline"},
                    output_dir=tmp_path / "main_agent",
                )
            )

        self.assertEqual("constructive_search", plan["method_family"])
        self.assertIn("high_flexibility", plan["knowledge_query"])
        self.assertIn("assignment_regret", plan["knowledge_query"])

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
                                "component_dependencies": [
                                    {
                                        "component_id": "toy_search",
                                        "depends_on": ["toy_decoder"],
                                        "reason": "Search depends on decoded toy states.",
                                    }
                                ],
                                "coupled_groups": [
                                    {
                                        "group_id": "toy_loop",
                                        "component_ids": ["toy_decoder", "toy_search"],
                                        "rule": "Decoder output must feed the search.",
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
        self.assertEqual(
            ["toy_search"],
            [item["component_id"] for item in plan["implementation_bundle"]["component_dependencies"]],
        )
        self.assertEqual(
            ["direct_evidence"],
            [item["track_id"] for item in plan["implementation_bundle"]["competition_tracks"]],
        )
        self.assertEqual(
            ["toy_legality"],
            [item["check_id"] for item in plan["implementation_bundle"]["checkpoint_checks"]],
        )
        self.assertIn(
            "Implement and verify the complete selected method bundle in one coherent direction: toy_decoder, toy_search",
            plan["change_scope"],
        )
        self.assertIn(
            "Every required component in implementation_bundle must have reachable source evidence; partial package implementation is not complete.",
            plan["acceptance_checks"],
        )
        self.assertIn(
            "Checkpoint toy_legality: Toy decoding and search must remain behaviorally aligned.",
            plan["acceptance_checks"],
        )
        self.assertIn("reference_solver.py", " ".join(plan["knowledge_paths"]))
        self.assertEqual(
            "knowledge/method_packages/toy_complete/implementation_contract.json",
            plan["knowledge_paths"][0],
        )

    def test_empty_or_unknown_method_package_never_uses_recommended_fallback(self) -> None:
        context = self._toy_method_catalog()
        for requested in ("", "unknown_package"):
            with self.subTest(requested=requested):
                plan = bind_direction_plan_to_method_catalog(
                    normalize_direction_plan(
                        {
                            "strategy_type": "local_search_operator",
                            "method_package_id": requested,
                        },
                        round_index=0,
                    ),
                    context=context,
                )

                self.assertEqual("", plan["method_package_id"])
                self.assertIsNone(plan["method_package_selection"]["selected"])
                self.assertEqual(bool(requested), plan["method_package_selection"]["fallback_used"])
                self.assertNotIn("implementation_bundle", plan)

    def test_improvement_fallback_binds_one_package_declared_component(self) -> None:
        context = self._toy_method_catalog()
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(
                {
                    "strategy_type": "local_search_operator",
                    "method_package_id": "toy_complete_bundle",
                },
                round_index=0,
            ),
            context=context,
        )

        self.assertEqual(["toy_search"], plan["implementation_order"])
        self.assertEqual(["toy_search"], [item["id"] for item in plan["deliverables"]])

    def test_improvement_keeps_valid_model_selected_component_order(self) -> None:
        context = self._toy_method_catalog()
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(
                {
                    "strategy_type": "local_search_operator",
                    "method_package_id": "toy_complete_bundle",
                    "implementation_order": ["toy_decoder", "unknown_component"],
                },
                round_index=0,
            ),
            context=context,
        )

        self.assertEqual(["toy_decoder"], plan["implementation_order"])

    def test_tracked_competition_plan_keeps_full_package_scope_for_delegated_lanes(self) -> None:
        context = {
            "method_package_catalog": {
                "recommended_package_id": "toy_complete_bundle",
                "packages": [
                    {
                        "package_id": "toy_complete_bundle",
                        "assets": ["knowledge/toy/reference_solver.py"],
                        "implementation_contract_asset": "knowledge/toy/contract.json",
                        "implementation_contract": {
                            "contract_id": "toy_complete_contract",
                            "fallback_improvement_order": ["toy_search", "toy_decoder"],
                            "required_components": [
                                {"component_id": "toy_decoder", "title": "Toy decoder"},
                                {"component_id": "toy_search", "title": "Toy search"},
                            ],
                            "component_dependencies": [
                                {"component_id": "toy_search", "depends_on": ["toy_decoder"]}
                            ],
                            "competition_tracks": [
                                {
                                    "track_id": "direct_evidence",
                                    "component_ids": ["toy_decoder", "toy_search"],
                                }
                            ],
                            "checkpoint_checks": [
                                {
                                    "check_id": "toy_legality",
                                    "component_ids": ["toy_decoder", "toy_search"],
                                    "requirement": "Toy decoding and search must stay aligned.",
                                }
                            ],
                        },
                    }
                ],
            }
        }
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(
                {
                    "strategy_type": "local_search_operator",
                    "method_package_id": "toy_complete_bundle",
                    "planner": "opencode_main_agent_fast",
                },
                round_index=0,
            ),
            context=context,
        )

        self.assertEqual(["toy_decoder", "toy_search"], plan["implementation_order"])
        self.assertEqual(["toy_decoder", "toy_search"], [item["id"] for item in plan["deliverables"]])

    def test_fallback_improvement_advances_past_used_components(self) -> None:
        context = self._toy_method_catalog()
        selected = fallback_improvement_order(
            context=context,
            loop_feedback={
                "previous_rounds": [
                    {"direction_plan": {"implementation_order": ["toy_search"]}}
                ]
            },
            round_index=1,
        )

        self.assertEqual(["toy_decoder"], selected)

    @staticmethod
    def _toy_method_catalog() -> dict:
        return {
            "method_package_catalog": {
                "recommended_package_id": "toy_complete_bundle",
                "packages": [
                    {
                        "package_id": "toy_complete_bundle",
                        "assets": ["knowledge/toy/reference_solver.py"],
                        "implementation_contract_asset": "knowledge/toy/contract.json",
                        "implementation_contract": {
                            "contract_id": "toy_complete_contract",
                            "fallback_improvement_order": ["toy_search", "toy_decoder"],
                            "required_components": [
                                {"component_id": "toy_decoder", "title": "Toy decoder"},
                                {"component_id": "toy_search", "title": "Toy search"},
                            ],
                        },
                    }
                ],
            }
        }

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
                            "implementation_order": ["critical_graph", "n8_move"],
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
            ["critical_graph", "n8_move"],
            feedback["previous_rounds"][0]["implementation_order"],
        )
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

    def test_constructive_improvement_is_not_mislabeled_as_local_search(self) -> None:
        plan = enforce_improvement_direction_contract(
            normalize_direction_plan(
                {
                    "method_family": "constructive_search",
                    "strategy_type": "baseline_constructor",
                },
                round_index=1,
            ),
            round_index=1,
            loop_feedback={"incumbent_key_before": [-1202.0]},
        )

        self.assertEqual("dispatch_rule", plan["strategy_type"])

    def test_dispatch_rule_family_alias_is_normalized_for_skill_matching(self) -> None:
        plan = normalize_direction_plan(
            {
                "method_family": "fjsp_dispatch_rule",
                "strategy_type": "baseline_constructor",
            },
            round_index=-1,
        )

        self.assertEqual("constructive_search", plan["method_family"])
        self.assertEqual([{"id": "constructive_search", "role": "primary"}], plan["method_families"])

    def test_model_mechanism_names_map_to_canonical_method_families(self) -> None:
        plan = normalize_direction_plan(
            {
                "method_families": [
                    {"id": "fjsp_constructive_dispatch"},
                    {"id": "fjsp_critical_block_neighborhood"},
                ],
                "strategy_type": "baseline_constructor",
            },
            round_index=-1,
        )

        self.assertEqual("constructive_search", plan["method_family"])
        self.assertEqual(
            ["constructive_search", "coupled_local_search"],
            [item["id"] for item in plan["method_families"]],
        )

    def test_post_baseline_probe_inherits_active_family_for_every_main_backend(self) -> None:
        plan = enforce_improvement_direction_contract(
            normalize_direction_plan(
                {
                    "direction_id": "d002",
                    "method_family": "coupled_local_search",
                    "method_families": [{"id": "coupled_local_search", "role": "primary"}],
                    "knowledge_query": ["critical_path"],
                    "experiment_stage": "pivot",
                },
                round_index=2,
            ),
            round_index=2,
            loop_feedback={
                "incumbent_key_before": [-2200.0],
                "previous_rounds": [
                    {
                        "round_index": 1,
                        "decision": "promoted",
                        "direction_plan": {
                            "direction_id": "d001",
                            "method_family": "constructive_search",
                            "method_families": [{"id": "constructive_search", "role": "primary"}],
                            "knowledge_query": ["beam_search"],
                            "activation_checks": [
                                {"path": "diagnostics.expanded", "operator": "gt", "expected": 0}
                            ],
                        },
                        "competition_result": {
                            "candidates": [
                                {
                                    "candidate_id": "c00",
                                    "activation_required": True,
                                    "mechanism_activation": {"passed": True},
                                    "observed_session_id": "ses-c00",
                                    "session_event_stream_bytes": 128,
                                }
                            ]
                        },
                        "round_reflection": {
                            "hypothesis_outcome": "supported",
                            "next_action": {"action": "scale"},
                        },
                    }
                ],
            },
        )

        self.assertEqual("constructive_search", plan["method_family"])
        self.assertEqual(["beam_search"], plan["knowledge_query"])
        self.assertEqual("scale", plan["experiment_stage"])
        self.assertEqual("inherit", plan["research_transition"]["method_family_policy"])

    def test_fallback_research_context_builds_neutral_family_tournament(self) -> None:
        context = {
            "method_family_catalog": {
                "families": [
                    {"family_id": "constructive_search", "query_tags": ["beam_search"]},
                    {"family_id": "coupled_local_search", "query_tags": ["critical_path"]},
                ]
            }
        }
        selected = fallback_research_context(
            context,
            {
                "previous_rounds": [
                    {
                        "decision": "rolled_back",
                        "direction_plan": {
                            "direction_id": "d001",
                            "method_family": "constructive_search",
                            "method_families": [{"id": "constructive_search", "role": "primary"}],
                            "knowledge_query": ["beam_search"],
                            "activation_checks": [
                                {
                                    "id": "expanded",
                                    "path": "diagnostics.telemetry.expanded",
                                    "operator": "gt",
                                    "expected": 0,
                                }
                            ],
                            "candidate_variants": [
                                {
                                    "candidate_id": "wide",
                                    "hypothesis": "Widen the beam.",
                                    "next_mutation": {"change": "Increase beam diversity."},
                                    "activation_checks": [
                                        {
                                            "id": "expanded",
                                            "path": "diagnostics.telemetry.expanded",
                                            "operator": "gt",
                                            "expected": 0,
                                        }
                                    ],
                                }
                            ],
                        },
                        "round_reflection": {
                            "hypothesis_outcome": "refuted",
                            "next_action": {"action": "pivot"},
                        },
                    }
                ]
            },
        )

        self.assertIsNone(selected["method_family"])
        self.assertEqual(
            {"constructive_search", "coupled_local_search"},
            {item["id"] for item in selected["method_families"]},
        )
        self.assertEqual([], selected["knowledge_query"])
        self.assertEqual("research_tournament", selected["experiment_stage"])
        self.assertFalse(selected["transition_deferred"])
        self.assertEqual(2, len(selected["candidate_variants"]))
        self.assertEqual(
            {"constructive_search", "coupled_local_search"},
            {item["method_family"] for item in selected["candidate_variants"]},
        )


if __name__ == "__main__":
    unittest.main()
