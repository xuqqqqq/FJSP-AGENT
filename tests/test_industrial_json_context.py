from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.quality_contract import build_solver_runtime_feature_contract
from harness_agent.context.knowledge import select_knowledge_cards, select_tagged_knowledge_cards
from harness_agent.context.packet import ContextPacketRequest, build_context_packet
from harness_agent.context.worker import build_worker_assignment
from harness_agent.domains.context import get_domain_context_provider
from harness_agent.domains.industrial_json import IndustrialJsonContextProvider, inspect_industrial_json
from harness_agent.domains.pack import get_domain_pack


ROOT = Path(__file__).resolve().parents[1]
REAL_INSTANCE = (
    ROOT.parent / "huawei_fjsp_llm/data/generated_small_cases_extended_v2_20260716_tight3840"
    / "small_20_random_seed20260716.json"
)
OBJECTIVES = [
    {"name": "completed_weight_within_horizon", "direction": "maximize", "priority": 1},
    {"name": "setup_count_positive", "direction": "minimize", "priority": 2},
]


def _instance_payload(*, mixed_machine=False):
    ordinary = {"is_batch_type": False, "eqp_list": {"m0": {"process_time": 3}}}
    batch_machine = "m0" if mixed_machine else "m1"
    batch = {"is_batch_type": True, "eqp_list": {batch_machine: {"process_time": 4, "curr_batch_size": 2}}}
    return {
        "time": {"current_time": 10}, "config": {"max_output_horizon": 100},
        "eqp": {"m0": {"eqp_down_interval": [[20, 30]]}, "m1": {"eqp_down_interval": []}},
        "task": {"j0": {"earliest_ava_time": 0, "process_path": {"r0": {
            "process_list": {"1": ordinary, "2": batch},
            "qtime_info": {"q0": {
                "start_process_seq": "1", "end_process_seq": "2",
                "start_process_type": "end", "end_process_type": "start",
                "min_process_interval": 0, "max_process_interval": 20,
            }},
        }}}},
        "setup": {}, "transition": {"m0": {"m1": 1}},
    }


class IndustrialJsonContextTests(unittest.TestCase):
    def test_independent_pack_audits_do_not_inherit_standard_variant_labels(self):
        for family in ("fjsp_industrial_json", "fjsp_distributed_transfer"):
            for features in ([], ["sequence_dependent_setup", "alternative_path"]):
                with self.subTest(family=family, features=features):
                    initial = select_knowledge_cards(problem_family=family, active_features=features)
                    tagged = select_tagged_knowledge_cards(
                        problem_family=family, active_features=features,
                        knowledge_query_tags=["constructive_search"],
                    )
                    self.assertEqual(family, initial.audit["active_variant"])
                    self.assertEqual(family, tagged.audit["active_variant"])

    def test_baseline_checks_follow_industrial_io_in_full_none_and_repairs(self):
        schema = IndustrialJsonContextProvider().solution_contract()
        for mode in ("full", "none"):
            for attempt in (0, 1):
                with self.subTest(mode=mode, attempt=attempt):
                    context = {
                        "task": {"problem_family": "fjsp_industrial_json"},
                        "guidance_ablation": {"mode": mode},
                        "instance_diagnostics": {"active_features": ["industrial_json", "window_weight_objective"]},
                        "evaluator_protocol": {
                            "solver_command_template": "python examples/agent_generated_industrial_solver.py --input {instance}",
                            "solution_contract": schema,
                        },
                        "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
                    }
                    feedback = {} if attempt == 0 else {"current_round_repair": {
                        "baseline_trial": 1, "attempt_index": attempt, "resume_incomplete_baseline": True,
                    }}
                    assignment = build_worker_assignment(
                        context=context,
                        direction_plan={
                            "direction_id": "industrial-baseline", "strategy_type": "baseline_constructor",
                            "method_family": "constructive_search",
                            "method_families": [{"id": "constructive_search", "role": "primary"}],
                        },
                        loop_feedback=feedback, round_index=-1, attempt_index=attempt,
                        max_steps=4, max_runtime_seconds=300,
                    )
                    self.assertTrue(any("active IO contract" in item and "preserve their association" in item
                                        for item in assignment.checks))
                    self.assertFalse(any("structured (machine_id, processing_time) pairs" in item
                                         for item in assignment.checks))
                    self.assertEqual(schema, assignment.runtime_contract["solution_contract"])
                    self.assertNotIn("makespan_objective", assignment.runtime_contract["active_features"])
                    if mode == "none":
                        self.assertEqual([], assignment.implementation_skills)
                        self.assertEqual("", assignment.method_package["package_id"])

    def test_pack_resolves_without_standard_fallback(self):
        pack = get_domain_pack("fjsp_industrial_json", fallback_to_standard=False)
        self.assertIsNotNone(pack)
        self.assertEqual("fjsp_industrial_json", pack.family_id)
        self.assertEqual(OBJECTIVES, pack.capability.canonical_objectives)
        self.assertEqual({"constructive_search", "coupled_local_search", "exact_hybrid", "population_memetic"},
                         {family.family_id for family in pack.method_families})
        self.assertTrue(pack.base_cards)
        self.assertTrue(all(path.is_file() for path in pack.base_cards))
        self.assertTrue(all("industrial_json" in path.parts for path in pack.base_cards))
        self.assertIsInstance(get_domain_context_provider("fjsp_industrial_json"), IndustrialJsonContextProvider)

    def test_fixture_profiles_qtime_batch_calendar_and_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            instance = Path(temp) / "case.json"
            instance.write_text(json.dumps(_instance_payload()), encoding="utf-8")
            profile = inspect_industrial_json(instance)
        self.assertEqual(2, profile["all_route_operation_count"])
        self.assertEqual(1, profile["batch_operation_count"])
        self.assertEqual(1, profile["minimum_time_lag_count"])
        self.assertEqual(1, profile["maximum_time_lag_count"])
        self.assertEqual(["end-start"], profile["qtime_anchor_types"])
        self.assertEqual(1, profile["calendar_intervals_in_output_window"])
        self.assertTrue({"batching", "industrial_qtime", "machine_calendar", "transportation"}
                        <= set(profile["active_features"]))

    def test_mixed_ordinary_and_batch_machine_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            instance = Path(temp) / "mixed.json"
            instance.write_text(json.dumps(_instance_payload(mixed_machine=True)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mixed ordinary/batch"):
                inspect_industrial_json(instance)

    def test_full_none_preserve_identical_objectives_schema_and_features(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            instance = directory / "case.json"
            instance.write_text(json.dumps(_instance_payload()), encoding="utf-8")
            contract = directory / "contract.json"
            contract.write_text(json.dumps({
                "task_id": "industrial_context_test", "problem_family": "fjsp_industrial_json",
                "description": "Frozen industrial JSON contract",
                "instances": [{"id": "fixture", "path": str(instance)}], "objectives": OBJECTIVES,
                "commands": {
                    "solver": "python examples/agent_generated_industrial_solver.py --input {instance} --output {solution} --seed {seed}",
                    "evaluator": "python frozen_industrial_evaluator.py --input {instance} --solution {solution} --metrics {metrics}",
                },
                "budget": {"rounds": 3, "seeds": [0], "timeout_seconds": 60},
                "paths": {"allowed_paths": ["examples/agent_generated_industrial_solver.py"], "forbidden_paths": [".git"]},
                "review": {"status": "confirmed", "baseline_source": "agent_generated"},
            }), encoding="utf-8")
            packets = {mode: build_context_packet(ContextPacketRequest(
                contract_path=contract, output_path=directory / f"{mode}.json", project_root=ROOT,
                docs=[ROOT / "docs/variants/industrial_json/requirement.md", ROOT / "docs/variants/industrial_json/io.md"],
                guidance_mode=mode,
            )) for mode in ("full", "none")}
        full, none = packets["full"], packets["none"]
        for field in ("task", "documents", "evaluator_protocol", "instance_diagnostics"):
            self.assertEqual(full[field], none[field], field)
        for packet in packets.values():
            self.assertEqual("fjsp_industrial_json", packet["problem_family_capability"]["family_id"])
            self.assertEqual(OBJECTIVES, packet["problem_family_capability"]["canonical_objectives"])
            self.assertEqual([item["name"] for item in OBJECTIVES], [item["name"] for item in packet["task"]["objectives"]])
            schema = packet["evaluator_protocol"]["solution_contract"]
            self.assertEqual("schema_only", schema["worker_smoke_validation"])
            self.assertEqual(["task"], schema["required_top_level_fields"])
            self.assertEqual(["task"], schema["required_object_fields"])
            self.assertNotIn("format", schema)
            features = set(build_solver_runtime_feature_contract(packet)["active_features"])
            self.assertTrue({"window_weight_objective", "setup_count_objective", "multi_objective"} <= features)
            self.assertNotIn("makespan_objective", features)
            self.assertNotIn("due_dates", features)
            self.assertNotIn("reentrant_route", features)
        self.assertTrue(full["knowledge_cards"])
        self.assertEqual("fjsp_industrial_json", full["knowledge_selection"]["domain_pack"])
        self.assertEqual("fjsp_industrial_json", full["knowledge_selection"]["active_variant"])
        for field in ("knowledge_cards", "auto_knowledge_cards", "knowledge_selection", "method_family_catalog", "method_package_catalog"):
            self.assertNotIn(field, none)
        self.assertFalse(none["guidance_ablation"]["worker_skills"])
        self.assertFalse(none["guidance_ablation"]["domain_knowledge"])
        self.assertEqual([], none["problem_family_capability"]["knowledge_tags"])
        self.assertEqual([], none["problem_family_capability"]["specialization_hooks"])

    @unittest.skipUnless(REAL_INSTANCE.is_file(), "external industrial instance is not installed")
    def test_selected_real_instance_profile(self):
        profile = inspect_industrial_json(REAL_INSTANCE)
        expected = {
            "job_count": 20, "machine_count": 59, "used_machine_count": 47,
            "all_route_operation_count": 156, "minimum_selected_operation_count": 138,
            "maximum_selected_operation_count": 141, "alternative_path_job_count": 3,
            "batch_operation_count": 8, "minimum_time_lag_count": 133, "maximum_time_lag_count": 34,
            "future_release_job_count": 0, "calendar_interval_count": 20,
            "calendar_intervals_in_output_window": 2, "positive_transport_arc_count": 560,
        }
        for field, value in expected.items():
            self.assertEqual(value, profile[field], field)
        self.assertGreater(profile["positive_setup_arc_count"], 0)
        self.assertTrue({"alternative_path", "batching", "industrial_qtime", "machine_calendar",
                         "sequence_dependent_setup", "transportation"} <= set(profile["active_features"]))
        self.assertNotIn("reentrant_route", profile["active_features"])
        self.assertEqual(3, len(profile["coverage_limits"]))


if __name__ == "__main__":
    unittest.main()
