from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from harness_agent.context.compaction import ROUND_CONTEXT_MAX_CHARS
from harness_agent.context.contract import DraftContractRequest, build_draft_contract
from harness_agent.context.packet import ContextPacketRequest, write_context_packet, write_refreshed_context_packet
from harness_agent.context.intake import ProjectIntakeRequest, write_project_intake


ROOT = Path(__file__).resolve().parents[1]


class ContextPacketTests(unittest.TestCase):
    def test_standard_fjsp_packet_exposes_exact_solution_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=Path(tmp) / "context.json",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        protocol = packet["evaluator_protocol"]
        self.assertEqual("standard_fjsp_schedule_v1", protocol["solution_format"])
        self.assertEqual(
            ["job_id", "op_id", "machine_id", "start", "end"],
            protocol["solution_contract"]["schedule_record_fields"],
        )

    def test_standard_instance_profile_exposes_finite_method_selection_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=Path(tmp) / "context.json",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        profile = packet["instance_diagnostics"]["instances"][0]
        fields = {
            "jobs_per_machine",
            "operations_per_machine",
            "candidate_count_cv",
            "flexible_operation_ratio",
            "full_flexibility_ratio",
            "processing_time_cv",
            "duration_spread_avg",
            "duration_spread_ratio_avg",
            "duration_spread_ratio_max",
            "machine_eligibility_cv",
            "fractional_min_load_cv",
            "mandatory_load_max",
            "job_min_workload_max",
        }
        self.assertTrue(fields.issubset(profile))
        for field in fields:
            with self.subTest(field=field):
                self.assertTrue(math.isfinite(float(profile[field])))
                self.assertGreaterEqual(float(profile[field]), 0.0)

    def test_two_stage_knowledge_selection_hides_awls_until_direction_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                )
            )
            base = json.loads(base_path.read_text(encoding="utf-8"))
            refreshed_path = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback={
                    "round_index": 0,
                    "current_direction_plan": {
                        "direction_id": "d000",
                        "method_family": "constructive_search",
                        "method_families": [
                            {"id": "constructive_search", "role": "primary"},
                            {"id": "coupled_local_search", "role": "complementary"},
                        ],
                        "knowledge_query": ["initialization", "local_search"],
                        "method_package_id": "",
                    },
                },
                project_root=ROOT,
            )
            refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))

        selection_names = [Path(item["path"]).name for item in base["strategy_selection_cards"]]
        self.assertIn("fjsp_method_selection_zh.md", selection_names)
        self.assertEqual([], base["method_package_catalog"]["packages"])
        self.assertIn(
            "constructive_search",
            {item["family_id"] for item in base["method_family_catalog"]["families"]},
        )
        active = refreshed["active_direction_knowledge"]
        self.assertEqual(["initialization", "local_search"], active["query"])
        self.assertEqual(
            ["constructive_search", "coupled_local_search"],
            [item["id"] for item in active["method_families"]],
        )
        self.assertTrue(active["paths"])
        self.assertFalse(any("awls" in path.lower() or "hgtsa" in path.lower() for path in active["paths"]))
        selected_skills = refreshed["active_worker_implementation_skills"]
        self.assertEqual("ok", selected_skills["status"])
        self.assertIn(
            "fjsp-constructive-search-worker",
            [item["skill_id"] for item in selected_skills["skills"]],
        )
        self.assertIn(
            "fjsp-coupled-local-search-worker",
            [item["skill_id"] for item in selected_skills["skills"]],
        )

    def test_none_guidance_mode_strips_knowledge_and_pipeline_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            memory_path = tmp_path / "experience_memory.json"
            memory_path.write_text(
                json.dumps({"schema_version": 1, "memory_tiers": {"validated_lessons": [{"lesson_id": "x"}]}}),
                encoding="utf-8",
            )
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                    guidance_mode="none",
                    knowledge_cards=[ROOT / "README.md"],
                    previous_pipeline_memory=memory_path,
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            {
                "mode": "none",
                "domain_knowledge": False,
                "worker_skills": False,
                "method_packages": False,
                "experience_memory": False,
            },
            packet["guidance_ablation"],
        )
        self.assertEqual([], packet["problem_family_capability"]["knowledge_tags"])
        self.assertNotIn("knowledge_cards", packet)
        self.assertNotIn("strategy_selection_cards", packet)
        self.assertNotIn("auto_knowledge_cards", packet)
        self.assertNotIn("knowledge_selection", packet)
        self.assertNotIn("knowledge_query_catalog", packet)
        self.assertNotIn("method_family_catalog", packet)
        self.assertNotIn("method_package_catalog", packet)
        self.assertNotIn("previous_pipeline_memory", packet)

    def test_none_guidance_mode_skips_direction_activation_and_experience_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                    guidance_mode="none",
                )
            )
            refreshed_path = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback={
                    "round_index": 0,
                    "experience_memory": {
                        "memory_tiers": {"candidate_lessons": [{"lesson_id": "should_not_leak"}]}
                    },
                    "skill_usage_summary": {"fjsp-constructive-search-worker": 1},
                    "current_direction_plan": {
                        "direction_id": "d000",
                        "method_family": "constructive_search",
                        "method_families": [{"id": "constructive_search", "role": "primary"}],
                        "knowledge_query": ["initialization"],
                        "method_package_id": "standard_fjsp_awls_hgtsa",
                    },
                },
                project_root=ROOT,
            )
            refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))

        self.assertEqual({}, refreshed["loop_feedback"]["experience_memory"])
        self.assertEqual({}, refreshed["loop_feedback"]["skill_usage_summary"])
        self.assertNotIn("active_method_package", refreshed)
        self.assertNotIn("active_direction_knowledge", refreshed)
        self.assertNotIn("active_worker_implementation_skills", refreshed)

    def test_refreshed_context_packet_compacts_large_round_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                    hypothesis="Bound round history.",
                )
            )
            large_rounds = [
                {
                    "round_index": index,
                    "decision": "rolled_back",
                    "proposal_diagnostics": {
                        "summary": "failed direction " + ("x" * 6000),
                        "in_round_repair": {
                            "attempts": [
                                {
                                    "failure_signatures": ["invalid_candidate"],
                                    "error_diagnosis": ["y" * 5000],
                                }
                                for _ in range(5)
                            ]
                        },
                    },
                }
                for index in range(30)
            ]
            output = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback={
                    "round_index": 30,
                    "previous_rounds": large_rounds,
                    "instructions": ["preserve incumbent"],
                },
            )

            raw = output.read_text(encoding="utf-8")
            packet = json.loads(raw)

            self.assertLessEqual(len(raw), 180_000)
            self.assertEqual("bounded_round_context", packet["context_compaction"]["mode"])
            self.assertLess(len(packet["loop_feedback"]["previous_rounds"]), len(large_rounds))
            self.assertNotIn("active_method_package", packet)

    def test_refreshed_context_packet_projects_loop_feedback_without_root_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                    hypothesis="Bound projected loop feedback.",
                )
            )
            output = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback=self._long_history_feedback(round_count=120),
            )

            raw = output.read_text(encoding="utf-8")
            packet = json.loads(raw)
            feedback = packet["loop_feedback"]

            self.assertLessEqual(len(raw), ROUND_CONTEXT_MAX_CHARS)
            self.assertEqual("bounded_loop_feedback", feedback["projection_kind"])
            self.assertNotIn("_compacted", json.dumps(feedback, ensure_ascii=False))
            self.assertEqual("Critical path retry", feedback["current_direction_plan"]["title"])
            self.assertEqual(6, len(feedback["previous_rounds"]))
            self.assertEqual(120, feedback["round_history_summary"]["source_round_count"])
            self.assertEqual(114, feedback["round_history_summary"]["omitted_round_count"])
            self.assertEqual("provisional_review_required", feedback["failure_memory"]["status"])
            self.assertEqual(8, len(feedback["protected_promoted_facts"]))
            self.assertEqual(6, len(feedback["direction_graph"]["directions"]))
            self.assertEqual(120, feedback["experience_memory"]["memory_tiers"]["validated_lesson_count"])
            self.assertEqual(6, len(feedback["experience_memory"]["memory_tiers"]["validated_lessons"]))
            artifact_kinds = {item["kind"] for item in feedback["artifact_refs"]}
            self.assertIn("hypothesis_graph", artifact_kinds)
            self.assertIn("experience_memory", artifact_kinds)
            self.assertIn("loop_result", artifact_kinds)
            self.assertEqual("schema_projection", packet["context_compaction"]["feedback_profile"])

    def test_refreshed_context_packet_keeps_size_near_fixed_under_long_history_growth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                    hypothesis="Keep long histories bounded.",
                )
            )
            shorter = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context_040.json",
                loop_feedback=self._long_history_feedback(round_count=40),
            ).read_text(encoding="utf-8")
            longer = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context_160.json",
                loop_feedback=self._long_history_feedback(round_count=160),
            ).read_text(encoding="utf-8")

            self.assertLessEqual(len(shorter), ROUND_CONTEXT_MAX_CHARS)
            self.assertLessEqual(len(longer), ROUND_CONTEXT_MAX_CHARS)
            self.assertLess(abs(len(longer) - len(shorter)), 12_000)

    def test_refreshed_context_adds_structured_incumbent_capability_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solver = tmp_path / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(
                "def solve(instance):\n"
                "    beam_width = min(3, max(2, instance['machine_count'] // 4 + 1))\n"
                "    modes = ['critical', 'finish', 'balance', 'randomized']\n"
                "    for mode in modes:\n"
                "        run_beam_construction(instance, beam_width)\n",
                encoding="utf-8",
            )
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                )
            )
            output = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback={"round_index": 0},
                project_root=tmp_path,
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        audit = packet["incumbent_capability_audit"]
        configurations = {
            item["name"]: item
            for item in audit["files"][0]["configurations"]
        }
        self.assertIn("beam_width", configurations)
        self.assertEqual(4, configurations["modes"]["collection_size"])
        self.assertNotIn("def solve", json.dumps(audit, ensure_ascii=False))

    def test_refreshed_context_activates_matching_package_only_after_final_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = json.loads((ROOT / "configs" / "standard_fjsp_tiny.example.json").read_text(encoding="utf-8"))
            contract["commands"]["solver"] = (
                "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}"
            )
            contract_path = tmp_path / "contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract_path,
                    output_path=tmp_path / "base_context.json",
                )
            )
            output = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback={
                    "current_direction_plan": {
                        "method_package_id": "standard_fjsp_awls_hgtsa",
                        "strategy_type": "local_search_operator",
                        "knowledge_query": ["local_search", "critical_path"],
                    }
                },
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            "standard_fjsp_awls_hgtsa",
            packet["active_method_package"]["package_id"],
        )
        self.assertEqual([], packet["method_package_catalog"]["packages"])
        self.assertTrue(any("reference_solver.py" in str(item) for item in packet["knowledge_cards"]))

    def test_explicit_variant_package_activates_when_base_catalog_has_no_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "base_context.json",
                )
            )
            base = json.loads(base_path.read_text(encoding="utf-8"))
            base["method_package_catalog"] = {
                "status": "not_applicable",
                "active_features": ["reentrant_route", "loop_expansion"],
                "packages": [],
            }
            base["packet_hash"] = "test-parent"
            base_path.write_text(json.dumps(base), encoding="utf-8")

            output = write_refreshed_context_packet(
                base_context_packet_path=base_path,
                output_path=tmp_path / "round_context.json",
                loop_feedback={
                    "current_direction_plan": {
                        "method_family": "exact_hybrid",
                        "method_package_id": "fjsp_reentrant_adaptation",
                        "knowledge_query": ["exact_hybrid", "reentrant_route", "loop_expansion"],
                    }
                },
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("fjsp_reentrant_adaptation", packet["active_method_package"]["package_id"])
        self.assertTrue(
            any(
                str(item.get("path") or "").endswith("fjsp_reentrant_adaptation\\implementation_contract.json")
                or str(item.get("path") or "").endswith("fjsp_reentrant_adaptation/implementation_contract.json")
                for item in packet["knowledge_cards"]
            )
        )

    def test_context_packet_embeds_project_intake_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            examples_dir = project_root / "examples"
            examples_dir.mkdir(parents=True)
            (project_root / "README.md").write_text("# Intake fixture\n", encoding="utf-8")
            (examples_dir / "agent_generated_fjsp_solver.py").write_text(
                "def main():\n    return 0\n",
                encoding="utf-8",
            )
            (examples_dir / "standard_fjsp_evaluator.py").write_text(
                "def main():\n    return 0\n",
                encoding="utf-8",
            )
            intake = write_project_intake(
                ProjectIntakeRequest(
                    project_root=project_root,
                    output_dir=tmp_path / "project_intake",
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    max_files=40,
                )
            )
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context_packet.json",
                    docs=[ROOT / "README.md"],
                    project_intake_manifest=Path(intake["artifacts"]["manifest"]),
                    hypothesis="Context-packet intake regression test.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

            self.assertTrue(packet["project_intake"]["exists"])
            self.assertEqual("ok", packet["project_intake"]["status"])
            self.assertIn(
                packet["project_intake"]["summary"]["language_summary"]["primary_language"],
                {"Python", "Documentation"},
            )
            self.assertIn("examples/standard_fjsp_evaluator.py", packet["project_intake"]["summary"]["entry_files"])
            self.assertTrue(packet["project_intake"]["report"]["exists"])
            self.assertIn("Review project_intake", " ".join(packet["worker_instruction"]["required_order"]))

    def test_context_packet_embeds_previous_pipeline_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            memory_path = tmp_path / "standard_pipeline_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pipeline_status": "ok",
                        "stage_status": {"admission_gate": "passed"},
                        "admission": {"gate": "passed"},
                        "benchmark_signal": {"avg_reported_gap_pct": 16.67},
                        "worker_signal": {
                            "baseline_key": [-7.0],
                            "final_key": [-7.0],
                            "improved": False,
                            "round_count": 1,
                            "promoted_rounds": 0,
                            "rounds": [
                                {
                                    "round_index": 0,
                                    "decision": "rolled_back",
                                    "worker_status": "skipped",
                                    "proposal_diagnostics": {"status": "missing"},
                                }
                            ],
                        },
                        "operator_lineage_signal": {
                            "hypothesis_count": 3,
                            "missing_hypothesis_rounds": 1,
                            "type_counts": {"local_search_operator": 2, "dispatch_rule": 1},
                            "decision_counts": {"promoted": 1, "rolled_back": 2},
                            "target_file_counts": {"examples/standard_fjsp_solver.py": 3},
                            "promoted_hypotheses": [
                                {
                                    "round_index": 0,
                                    "decision": "promoted",
                                    "duplicate_proposal": False,
                                    "name": "machine_load_insert",
                                    "type": "dispatch_rule",
                                    "target_files": ["examples/standard_fjsp_solver.py"],
                                    "expected_effect": "Reduce average makespan.",
                                    "novelty": "Uses load-aware insertion.",
                                }
                            ],
                            "rolled_back_hypotheses": [
                                {
                                    "round_index": 1,
                                    "decision": "rolled_back",
                                    "duplicate_proposal": False,
                                    "name": "critical_block_swap",
                                    "type": "local_search_operator",
                                    "target_files": ["examples/standard_fjsp_solver.py"],
                                    "expected_effect": "Reduce critical path length.",
                                    "novelty": "Swaps adjacent critical operations.",
                                }
                            ],
                            "duplicate_hypotheses": [
                                {
                                    "round_index": 2,
                                    "decision": "rolled_back",
                                    "duplicate_proposal": True,
                                    "name": "critical_block_swap",
                                    "type": "local_search_operator",
                                    "target_files": ["examples/standard_fjsp_solver.py"],
                                    "expected_effect": "Reduce critical path length.",
                                    "novelty": "Repeated the same swap.",
                                }
                            ],
                        },
                        "direction_graph_signal": {
                            "schema_version": 2,
                            "round_semantics": "direction",
                            "direction_count": 2,
                            "attempt_count": 3,
                            "status_counts": {"validated_success": 1, "no_improvement": 1},
                            "decision_counts": {"promoted": 1, "rolled_back": 1},
                            "recent_directions": [
                                {
                                    "direction_id": "d001",
                                    "round_index": 1,
                                    "title": "machine_load_insert",
                                    "status": "validated_success",
                                    "decision": "promoted",
                                    "strategy_type": "dispatch_rule",
                                    "attempt_count": 2,
                                }
                            ],
                            "guidance": ["Preserve promoted directions."],
                        },
                        "experience_memory_signal": {
                            "schema_version": 1,
                            "candidate_lesson_count": 1,
                            "candidate_lessons": [
                                {
                                    "lesson_id": "lesson_001",
                                    "lesson_type": "successful_strategy",
                                    "strategy": "machine_load_insert",
                                    "strategy_type": "dispatch_rule",
                                    "outcome": "promoted_by_core_evaluator",
                                    "confidence": "candidate",
                                }
                            ],
                            "next_context_guidance": ["Keep candidate lessons separate from curated skills."],
                        },
                        "skill_usage_signal": {"record_count": 2, "by_source_kind": {"knowledge_card": 1}},
                        "recommendations": ["Require a materially different rule."],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context_packet.json",
                    previous_pipeline_memory=memory_path,
                    hypothesis="Context-packet previous-memory regression test.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

            memory = packet["previous_pipeline_memory"]
            self.assertTrue(memory["exists"])
            self.assertEqual("ok", memory["pipeline_status"])
            self.assertEqual(16.67, memory["benchmark_signal"]["avg_reported_gap_pct"])
            self.assertEqual("rolled_back", memory["worker_signal"]["rounds"][0]["decision"])
            self.assertEqual(3, memory["operator_lineage_signal"]["hypothesis_count"])
            self.assertEqual(2, memory["direction_graph_signal"]["direction_count"])
            self.assertEqual(1, memory["experience_memory_signal"]["candidate_lesson_count"])
            self.assertEqual(2, memory["skill_usage_signal"]["record_count"])
            guidance = memory["operator_guidance"]
            self.assertEqual("available", guidance["status"])
            self.assertIn("Use Core evaluator metrics", " ".join(guidance["must_do"]))
            self.assertIn("include 1 to 3 concrete hypotheses", " ".join(guidance["must_do"]))
            self.assertEqual("machine_load_insert", guidance["preserve"][0]["name"])
            self.assertEqual("critical_block_swap", guidance["mutate"][0]["name"])
            self.assertEqual("critical_block_swap", guidance["avoid"][0]["name"])
            self.assertIn("Review previous_pipeline_memory", " ".join(packet["worker_instruction"]["required_order"]))
            self.assertIn(
                "operator_guidance",
                " ".join(packet["worker_instruction"]["required_order"]),
            )
            self.assertIn(
                "direction_graph_signal",
                " ".join(packet["worker_instruction"]["required_order"]),
            )
            self.assertIn(
                "experience_memory_signal",
                " ".join(packet["worker_instruction"]["required_order"]),
            )

    def test_context_packet_accepts_raw_worker_experience_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            memory_path = tmp_path / "experience_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "memory_tiers": {
                            "candidate_lessons": [{"lesson_id": "candidate", "confidence": "candidate"}],
                            "validated_lessons": [
                                {
                                    "lesson_id": "validated",
                                    "strategy": "AWLS method adaptation",
                                    "strategy_type": "local_search_operator",
                                    "method_package_id": "standard_fjsp_awls_hgtsa",
                                    "confidence": "core_and_semantic_validated",
                                }
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context_packet.json",
                    previous_pipeline_memory=memory_path,
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        signal = packet["previous_pipeline_memory"]["experience_memory_signal"]
        self.assertEqual(1, signal["candidate_lesson_count"])
        self.assertEqual([], signal["candidate_lessons"])
        self.assertTrue(signal["candidate_lessons_withheld"])
        self.assertEqual(1, signal["validated_lesson_count"])
        self.assertEqual("standard_fjsp_awls_hgtsa", signal["validated_lessons"][0]["method_package_id"])

    def test_context_packet_embeds_sdst_instance_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            instance = tmp_path / "oddla20.txt"
            instance.write_text((ROOT / "examples" / "fjsp_sdst_hudata_tiny.txt").read_text(encoding="utf-8"), encoding="utf-8")
            best_known = tmp_path / "lbub.csv"
            best_known.write_text("instance,best\nla20,997\n", encoding="utf-8")
            contract = tmp_path / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_diagnostics_context",
                        "problem_family": "standard_fjsp",
                        "description": "diagnostics smoke",
                        "instances": [{"id": "oddla20", "path": str(instance)}],
                        "objectives": [{"name": "makespan", "direction": "minimize"}],
                        "commands": {
                            "solver": "python solver.py",
                            "evaluator": "python evaluator.py",
                            "quick_test": "python -m compileall .",
                        },
                        "budget": {"rounds": 1, "seeds": [0]},
                        "paths": {"allowed_paths": ["examples"], "forbidden_paths": [".git"]},
                        "resources": {"best_known_csv": str(best_known)},
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
                    hypothesis="Use SDST diagnostics.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        diagnostics = packet["instance_diagnostics"]
        self.assertEqual("available", diagnostics["status"])
        self.assertEqual(1, diagnostics["summary"]["sdst_instance_count"])
        self.assertEqual("diagnostic_only_score_remains_negative_makespan", diagnostics["summary"]["best_known_semantics"])
        self.assertIn("job_pair", diagnostics["summary"]["setup_time_kinds"])
        self.assertEqual(997.0, diagnostics["instances"][0]["best_known_makespan"])
        self.assertEqual("fjsp_sdst", diagnostics["instances"][0]["variant"])
        self.assertGreater(diagnostics["instances"][0]["setup_time_max"], 0)
        self.assertIn("Review instance_diagnostics", " ".join(packet["worker_instruction"]["required_order"]))
        self.assertEqual("fjsp_sdst", packet["knowledge_selection"]["active_variant"])
        auto_cards = {Path(path).name for path in packet["auto_knowledge_cards"]}
        self.assertIn("awls_sdst_adapter_notes.md", auto_cards)
        self.assertIn("awls_sdst_agent_generated_transfer_notes.md", auto_cards)
        self.assertIn("agent_generated_decoder_neighborhood.md", auto_cards)
        self.assertNotIn("awls_sdst_hudata20_baseline_notes.md", auto_cards)
        self.assertNotIn("fjsp_sdst_search_observation_20260723.md", auto_cards)

    def test_context_packet_summarizes_multiple_sdst_shapes_without_prefix_bias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            instance_dir = tmp_path / "instances"
            instance_dir.mkdir()
            first = ROOT / "examples" / "fjsp_sdst_hudata_tiny.txt"
            second = tmp_path / "oddla13.txt"
            setup_tail_rows = [
                " ".join(str((machine_id + row + col) % 6) for col in range(20))
                for machine_id in range(5)
                for row in range(20)
            ]
            second.write_text(
                "\n".join(
                    [
                        "20 5 1",
                        *("5 1 1 5 1 2 5 1 3 5 1 4 5 1 5 5" for _ in range(20)),
                        *setup_tail_rows,
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            instances = []
            best_known_rows = ["instance,best"]
            for index in range(20):
                source = first if index < 12 else second
                name = f"oddla{index + 1:02d}.txt"
                path = instance_dir / name
                path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                instances.append({"id": path.stem, "path": str(path)})
                best_known_rows.append(f"la{index + 1:02d},{700 + index}")
            best_known = tmp_path / "lbub.csv"
            best_known.write_text("\n".join(best_known_rows) + "\n", encoding="utf-8")
            contract = tmp_path / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_multi_shape_context",
                        "problem_family": "standard_fjsp",
                        "description": "diagnostics multi-shape smoke",
                        "instances": instances,
                        "objectives": [{"name": "makespan", "direction": "minimize"}],
                        "commands": {
                            "solver": "python solver.py",
                            "evaluator": "python evaluator.py",
                            "quick_test": "python -m compileall .",
                        },
                        "budget": {"rounds": 1, "seeds": [0]},
                        "paths": {"allowed_paths": ["examples"], "forbidden_paths": [".git"]},
                        "resources": {"best_known_csv": str(best_known)},
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
                    hypothesis="Use SDST diagnostics across shapes.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        diagnostics = packet["instance_diagnostics"]
        self.assertTrue(diagnostics["truncated"])
        self.assertEqual(20, diagnostics["summary"]["instance_count"])
        self.assertEqual(2, diagnostics["summary"]["shape_group_count"])
        shape_keys = {group["shape_key"] for group in diagnostics["shape_groups"]}
        self.assertIn("j2_m2_ops3_c1_job_pair", shape_keys)
        self.assertIn("j20_m5_ops100_c1_job_pair", shape_keys)
        sampled_ids = {item["id"] for item in diagnostics["instances"]}
        self.assertIn("oddla20", sampled_ids)
        self.assertIn(
            "avoid overfitting a single instance/seed probe",
            " ".join(diagnostics["direction_hints"]),
        )
        self.assertIn("Measured machine concentration", " ".join(diagnostics["direction_hints"]))
        self.assertIn("fractional_min_load_cv_max", " ".join(diagnostics["direction_hints"]))

    def test_context_packet_embeds_compact_contract_review_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "requirements.md"
            instance = tmp_path / "case.json"
            contract_path = tmp_path / "draft_contract.json"
            doc.write_text(
                """
# FJSP 需求

## 目标指标

目标包括产量和 setup 切换次数。

## 约束清单

需要满足释放时间、维修窗口和组批约束。

## 输入输出结构

输入包含工序、候选机器和维修窗口。

## 算法提示

优先考虑局部搜索和强化学习风格的策略迭代。
                """.strip(),
                encoding="utf-8",
            )
            instance.write_text("{}", encoding="utf-8")
            contract_payload = build_draft_contract(
                DraftContractRequest(
                    task_id="draft_context_case",
                    docs=[doc],
                    instances=[instance],
                    output=contract_path,
                )
            )
            contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract_path,
                    output_path=tmp_path / "context_packet.json",
                    hypothesis="Use document schema grounding.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

            evidence = packet["contract_review_evidence"]
            self.assertTrue(evidence["has_document_schema"])
            self.assertEqual("draft_requires_human_confirmation", evidence["status"])
            self.assertGreaterEqual(evidence["document_schema"]["section_count"], 2)
            role_counts = evidence["document_schema"]["role_counts"]
            self.assertGreaterEqual(role_counts["objectives"], 1)
            self.assertGreaterEqual(role_counts["input_output"], 1)
            headings = [
                section["heading"]
                for document in evidence["document_schema"]["documents"]
                for section in document["sections"]
            ]
            self.assertIn("目标指标", headings)
            self.assertTrue(any(item["metric"] == "completed_weight" for item in evidence["metric_hints"]))
            self.assertIn("Review contract_review_evidence", " ".join(packet["worker_instruction"]["required_order"]))
            self.assertIn(
                "role_prioritized_sections",
                " ".join(packet["worker_instruction"]["required_order"]),
            )

            prioritized = evidence["role_prioritized_sections"]
            self.assertGreaterEqual(len(prioritized), 3)
            self.assertEqual("目标指标", prioritized[0]["heading"])
            prioritized_headings = [item["heading"] for item in prioritized]
            self.assertIn("约束清单", prioritized_headings)
            self.assertIn("输入输出结构", prioritized_headings)
            self.assertTrue(prioritized[0]["priority_reason"].startswith("roles=objectives"))

    @staticmethod
    def _long_history_feedback(*, round_count: int) -> dict[str, object]:
        previous_rounds = []
        validated_lessons = []
        directions = []
        promoted_count = 0
        for index in range(round_count):
            decision = "promoted" if index % 5 == 0 else "rolled_back"
            if decision == "promoted":
                promoted_count += 1
            previous_rounds.append(
                {
                    "round_index": index,
                    "decision": decision,
                    "candidate_key": [-3600.0 + index],
                    "incumbent_key_after": [-3599.0 + index],
                    "worker_status": "completed",
                    "failure_signatures": [f"failure_{index}", "decoder_guard_missing"],
                    "direction_plan": {
                        "direction_id": f"d{index:03d}",
                        "title": f"Direction {index}",
                        "strategy_type": "local_search_operator",
                        "method_family": "coupled_local_search",
                        "method_package_id": "standard_fjsp_awls_hgtsa",
                        "implementation_order": [
                            "critical_graph",
                            "n8_move",
                            f"operator_{index}",
                        ],
                    },
                    "candidate_summary": {
                        "total": 6,
                        "valid": 4,
                        "failed": 2,
                        "best_experiment_id": f"exp_{index}",
                        "best_metrics": {
                            "makespan": 1000 - index,
                            "solver_evidence": {
                                "diagnostics": {
                                    "instance_name": f"ta{index:03d}",
                                    "decode_attempts": 3,
                                    "beam_state_expansions": 30 + index,
                                }
                            },
                        },
                        "validation_summary": {
                            "top_errors": ["decoder mismatch", "schedule overlap"],
                        },
                    },
                    "proposal_diagnostics": {
                        "summary": "failed direction " + ("x" * 1500),
                        "strategy_intent": "Tighten critical-path move gating " + ("y" * 900),
                        "proposal_audit": {
                            "warnings": ["kept stale helper", "skipped validator sync"],
                            "accepted_change_paths": [f"solver_{index}.py"],
                        },
                    },
                    "semantic_review": {
                        "status": "repair_required",
                        "accepted": False,
                        "summary": "Need decoder legality checks.",
                        "findings": [
                            {
                                "blocking": True,
                                "category": "decoder",
                                "summary": "Missing legality guard",
                                "repair": "Add guard before move application",
                            }
                        ],
                    },
                    "smoke_gate": {
                        "passed": index % 4 == 0,
                        "full_evaluation_started": index % 4 == 0,
                        "summary": {
                            "validation_summary": {"top_errors": ["invalid candidate"]},
                        },
                    },
                    "promotion_check": {
                        "promoted": decision == "promoted",
                        "eligible": decision == "promoted",
                        "reason": "Only stable directions promote.",
                        "selected_candidate_id": f"candidate_{index}",
                    },
                    "cycle_dir": f"C:/runs/round_{index:03d}",
                    "patch_path": f"C:/runs/round_{index:03d}/candidate.patch",
                    "context_packet_path": f"C:/runs/round_{index:03d}/context_packet.json",
                }
            )
            validated_lessons.append(
                {
                    "lesson_id": f"lesson_{index}",
                    "lesson_type": "mechanism",
                    "strategy": f"Strategy lesson {index}",
                    "strategy_type": "local_search_operator",
                    "outcome": "validated" if decision == "promoted" else "provisional",
                    "applicability": ["high-flexibility", "critical path"],
                    "contraindications": ["decoder instability"],
                    "confidence": "medium",
                    "evidence": {
                        "direction_id": f"d{index:03d}",
                        "round_index": index,
                        "decision": decision,
                        "status": "evaluated",
                        "score_relation": "improved" if decision == "promoted" else "worse",
                    },
                }
            )
            directions.append(
                {
                    "direction_id": f"d{index:03d}",
                    "parent_id": f"d{max(0, index - 1):03d}",
                    "round_index": index,
                    "title": f"Direction {index}",
                    "status": "evaluated",
                    "decision": decision,
                    "strategy_type": "local_search_operator",
                    "target_files": [f"solver_{index}.py", "validator.py"],
                    "score_relation": "improved" if decision == "promoted" else "worse",
                    "attempt_count": 2,
                }
            )
        return {
            "purpose": "Evaluator-backed loop feedback for a long-history packet regression.",
            "round_semantics": {
                "user_visible_round": "improvement_direction",
                "core_atomic_unit": "worker_attempt",
            },
            "competition": {
                "max_competing_workers": 3,
                "isolation_rule": "Use isolated worktrees.",
                "selection_rule": "Promote only the best eligible candidate.",
            },
            "round_index": round_count,
            "current_direction": {
                "direction_id": f"d{round_count:03d}",
                "status": "planned",
            },
            "current_direction_plan": {
                "direction_id": f"d{round_count:03d}",
                "title": "Critical path retry",
                "strategy_type": "local_search_operator",
                "method_family": "coupled_local_search",
                "method_package_id": "standard_fjsp_awls_hgtsa",
                "knowledge_query": ["critical_path", "local_search"],
                "change_scope": ["Tighten N8 acceptance rule.", "Preserve decoder legality guard."],
                "preserve": ["Keep promoted decoder legality checks."],
                "avoid": ["Do not remove incumbent feasibility guards."],
                "implementation_order": ["critical_graph", "n8_move", "acceptance_rule"],
                "activation_checks": [
                    {
                        "id": "critical_graph_enabled",
                        "path": "solver.py",
                        "operator": "contains",
                        "expected": "critical_graph",
                        "required": True,
                        "description": "Critical graph path remains wired.",
                    }
                ],
            },
            "objective_key_order": [
                {"name": "makespan", "direction": "minimize", "priority": 0, "threshold": None}
            ],
            "baseline_key": [-3700.0],
            "incumbent_key_before": [-3600.0],
            "incumbent_worktree": "C:/runs/incumbent",
            "baseline_summary": {
                "total": 6,
                "valid": 4,
                "failed": 2,
                "best_experiment_id": "baseline_exp",
                "best_metrics": {"makespan": 1088},
                "validation_summary": {"top_errors": ["legacy decoder drift"]},
            },
            "incumbent_summary": {
                "total": 6,
                "valid": 5,
                "failed": 1,
                "best_experiment_id": "incumbent_exp",
                "best_metrics": {"makespan": 1012},
                "validation_summary": {"top_errors": ["operator regression"]},
            },
            "agent_generated_baseline_memory": {
                "accepted_as_incumbent": True,
                "baseline_key": [-3700.0],
                "proposal_summary": "Recovered a legal incumbent baseline.",
                "strategy_intent": "Use recovered baseline as incumbent anchor.",
                "best_core_valid_anchor": {
                    "objective_key": [-2688.0],
                    "semantic_status": "repair_required",
                },
                "protection_rule": "Preserve baseline legality scaffolding.",
            },
            "previous_rounds": previous_rounds,
            "direction_graph": {
                "schema_version": 1,
                "round_semantics": "direction",
                "direction_count": round_count,
                "attempt_count": round_count * 2,
                "status_counts": {"evaluated": round_count},
                "decision_counts": {"promoted": promoted_count, "rolled_back": round_count - promoted_count},
                "promoted_direction_ids": [f"d{index:03d}" for index in range(0, round_count, 5)],
                "directions": directions,
                "guidance": ["Preserve validated decoder guards.", "Change only one operator at a time."],
            },
            "experience_memory": {
                "schema_version": 1,
                "write_policy": {"validated_only": False},
                "memory_tiers": {
                    "candidate_lessons": validated_lessons[:12],
                    "validated_lessons": validated_lessons,
                },
                "agent_generated_quality_memory": {
                    "attempt_count": round_count,
                    "rejected_attempt_count": round_count - promoted_count,
                    "recurring_quality_risks": ["helper drift", "validator mismatch"],
                },
                "algorithm_semantic_memory": {
                    "attempt_count": round_count,
                    "repair_required_attempt_count": round_count - promoted_count,
                    "recurring_categories": ["decoder", "feasibility"],
                    "knowledge_paths": ["knowledge/standard_fjsp_critical_path.md"],
                },
                "skill_usage_summary": {"fjsp-coupled-local-search-worker": round_count},
                "self_evolution_metrics": {"validated_ratio": 0.2},
                "next_context_guidance": ["Keep decoder and operator mutations coupled to evidence."],
            },
            "skill_usage_summary": {"fjsp-coupled-local-search-worker": round_count},
            "protected_promoted_facts": [
                {
                    "direction_id": f"d{index:03d}",
                    "round_index": index,
                    "fact_type": "preserve_mechanism",
                    "title": f"Protected fact {index}",
                    "summary": "Keep the legality guard and decoder normalization path.",
                    "preserve_rule": "Preserve incumbent legality scaffolding.",
                    "evidence_refs": [f"C:/runs/round_{index:03d}/context_packet.json"],
                }
                for index in range(12)
            ],
            "failure_memory": {
                "status": "provisional_review_required",
                "review_required": True,
                "must_avoid": [],
                "recent_failures": [
                    {
                        "round_index": index,
                        "direction_id": f"d{index:03d}",
                        "failure_signatures": [f"failure_{index}", "decoder_guard_missing"],
                        "decision": "rolled_back",
                        "summary": "Evaluator rejected the move without legality recovery.",
                    }
                    for index in range(10)
                ],
            },
            "next_round_guidance": {
                "must_do": ["Repair legality guard first."],
                "preserve": ["Keep promoted incumbent structure."],
                "avoid": ["Do not replay the same rolled-back patch."],
            },
            "instructions": [
                "Use only evaluator metrics as promotion evidence.",
                "Repair current_round_repair.previous_attempts before switching direction.",
            ],
            "current_round_repair": {
                "status": "pending",
                "attempt_index": 2,
                "max_repair_attempts": 4,
                "repair_targets": {
                    "algorithm_semantic_review": {
                        "status": "repair_required",
                        "blocking_findings": [
                            {
                                "finding_id": "decoder_guard",
                                "category": "decoder",
                                "repair": "Add feasibility guard before committing a swap.",
                            }
                        ],
                    }
                },
                "previous_attempts": [
                    {
                        "attempt_index": 0,
                        "worker_status": "completed",
                        "changed_files": ["solver.py"],
                        "failure_signatures": ["decoder_guard_missing"],
                        "proposal_diagnostics": {
                            "summary": "Added a move without a legality gate.",
                            "strategy_intent": "Patch acceptance rule only.",
                            "proposal_audit": {
                                "accepted_change_paths": ["solver.py"],
                                "warnings": ["validator not updated"],
                            },
                        },
                        "semantic_review": {"status": "repair_required"},
                    }
                ],
            },
            "hypothesis_graph_path": "C:/runs/graphs/hypothesis_graph.json",
            "experience_memory_path": "C:/runs/memory/experience_memory.json",
            "loop_result_path": "C:/runs/results/loop_result.json",
        }


if __name__ == "__main__":
    unittest.main()
