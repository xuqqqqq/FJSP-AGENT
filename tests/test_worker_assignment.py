from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.main import DirectionPlanRequest, EvidenceDrivenMainAgent
from harness_agent.context.knowledge import method_package_catalog
from harness_agent.context.loader import load_context_dict
from harness_agent.context.packet import (
    ContextPacketRequest,
    activate_direction_knowledge_context,
    activate_method_package_context,
    write_context_packet,
)
from harness_agent.context.worker import (
    WORKER_ASSIGNMENT_MAX_CHARS,
    WORKER_ASSIGNMENT_SOFT_CHARS,
    build_worker_assignment,
    write_worker_assignment,
)
from harness_agent.worker import WorkerAssignment


ROOT = Path(__file__).resolve().parents[1]


class WorkerAssignmentTests(unittest.TestCase):
    def test_minimum_time_lag_cards_are_mandatory_for_generic_direction_query(self) -> None:
        package_catalog = method_package_catalog(
            problem_family="FJSP",
            active_features=["minimum_time_lag"],
            knowledge_query_tags=["constructive_search"],
        )
        active_package = next(
            item
            for item in package_catalog["packages"]
            if item["package_id"] == "fjsp_min_time_lag_constructive_adaptation"
        )
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "method_package_catalog": package_catalog,
            "active_method_package": active_package,
        }
        direction = {
            "direction_id": "d000",
            "method_family": "constructive_search",
            "method_package_id": "fjsp_min_time_lag_constructive_adaptation",
            "implementation_bundle": active_package["implementation_contract"],
            "knowledge_query": ["constructive_search"],
            "hypothesis": "Improve a lag-aware constructor.",
            "worker_lane_policy": {
                "mechanism_selection": "delegated_to_worker",
                "lane_count": 3,
            },
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback={},
            round_index=0,
            attempt_index=0,
            max_steps=2,
            max_runtime_seconds=60,
        )

        read_paths = {item["path"] for item in assignment.read_set}
        self.assertIn(
            "knowledge/references/min_time_lag/min_time_lag_semantics_and_decoder.md",
            read_paths,
        )
        self.assertIn(
            "knowledge/references/min_time_lag/min_time_lag_search_adaptation.md",
            read_paths,
        )

    def test_fast_selected_awls_package_materializes_contract_and_n7_nk_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=Path(tmp) / "context.json",
                )
            )
            context = load_context_dict(context_path)
            original_catalog = context["method_package_catalog"]
            context["method_package_catalog"] = method_package_catalog(
                problem_family="FJSP",
                active_features=original_catalog.get("active_features") or [],
                knowledge_query_tags=["assignment_aware_local_search", "adaptive_weight"],
            )
            direction = {
                "direction_id": "d000",
                "method_family": "coupled_local_search",
                "method_families": [{"id": "coupled_local_search", "role": "primary"}],
                "method_package_id": "standard_fjsp_awls_hgtsa",
                "knowledge_query": ["assignment_aware_local_search", "adaptive_weight"],
                "hypothesis": "Use AWLS neighborhoods to improve the incumbent.",
                "worker_lane_policy": {
                    "mechanism_selection": "delegated_to_worker",
                    "lane_count": 3,
                },
            }
            activate_method_package_context(context, direction_plan=direction)
            activate_direction_knowledge_context(context, direction_plan=direction)

            assignment = build_worker_assignment(
                context=context,
                direction_plan=direction,
                loop_feedback={},
                round_index=0,
                attempt_index=0,
                max_steps=2,
                max_runtime_seconds=60,
            )

            read_paths = [item["path"].replace("\\", "/") for item in assignment.read_set]
            self.assertEqual("standard_fjsp_awls_hgtsa", assignment.method_package["package_id"])
            self.assertIn(
                "knowledge/method_packages/standard_fjsp_awls_hgtsa/implementation_contract.json",
                read_paths,
            )
            self.assertIn(
                "knowledge/references/standard_fjsp/standard_fjsp_awls_hgtsa_execution_skeleton.md",
                read_paths,
            )
            self.assertFalse(any(path.endswith("/reference_solver.py") for path in read_paths))
            self.assertLessEqual(len(read_paths), 7)
            self.assertIn(
                "fjsp-coupled-local-search-worker",
                [item["skill_id"] for item in assignment.implementation_skills],
            )
            self.assertLessEqual(len(assignment.implementation_skills), 2)

    def test_assignment_size_uses_soft_target_and_hard_ceiling(self) -> None:
        payload = {
            "assignment_id": "d000-a00",
            "direction_id": "d000",
            "mode": "baseline",
            "target_file": "examples/agent_generated_fjsp_solver.py",
            "objective": "x" * 12_100,
            "method_package": {},
            "read_set": [{"path": "contract.json", "role": "contract", "required": True}],
            "deliverables": [{"id": "solver", "behavior": "Create a legal solver."}],
            "implementation_order": ["solver"],
            "preserve": [],
            "forbidden": [],
            "latest_feedback": {},
            "checks": [],
            "budgets": {"max_edit_steps": 2, "max_runtime_seconds": 60},
            "completion_rule": "Compile the solver.",
            "lineage": {},
            "runtime_contract": {},
        }
        assignment = WorkerAssignment.from_payload(payload)

        serialized = json.dumps(assignment.to_payload(), ensure_ascii=False, indent=2) + "\n"
        self.assertGreater(len(serialized), WORKER_ASSIGNMENT_SOFT_CHARS)
        self.assertLess(len(serialized), WORKER_ASSIGNMENT_MAX_CHARS)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_worker_assignment(Path(tmp) / "worker_assignment.json", assignment)
            self.assertTrue(path.is_file())

        oversized = WorkerAssignment.from_payload({**payload, "objective": "x" * WORKER_ASSIGNMENT_MAX_CHARS})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "exceeds 24000 chars"):
                write_worker_assignment(Path(tmp) / "worker_assignment.json", oversized)

    def test_assignment_resolves_worker_skills_from_candidate_families_not_stale_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = load_context_dict(context_path)
            context["active_worker_implementation_skills"] = {
                "method_families": [{"id": "constructive_search", "role": "primary"}],
                "skills": [{"skill_id": "fjsp-constructive-search-worker"}],
            }

            assignment = build_worker_assignment(
                context=context,
                direction_plan={
                    "direction_id": "d000-exact",
                    "method_family": "exact_hybrid",
                    "method_families": [{"id": "exact_hybrid", "role": "primary"}],
                    "knowledge_query": ["cp_sat", "trust_region"],
                    "hypothesis": "Test one bounded exact-hybrid candidate.",
                    "change_scope": ["Add one bounded exact-hybrid stage."],
                },
                loop_feedback={},
                round_index=0,
                attempt_index=0,
                max_steps=4,
                max_runtime_seconds=300,
            )

        skill_ids = [item["skill_id"] for item in assignment.implementation_skills]
        self.assertIn("fjsp-experiment-design-worker", skill_ids)
        self.assertIn("fjsp-solver-foundation-worker", skill_ids)
        self.assertIn("fjsp-exact-hybrid-worker", skill_ids)
        self.assertNotIn("fjsp-constructive-search-worker", skill_ids)
        read_paths = [item["path"] for item in assignment.read_set]
        self.assertTrue(any(path.endswith("cp_sat_hybrid_blueprint.md") for path in read_paths))

    def test_baseline_assignment_is_bounded_and_names_only_selected_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = load_context_dict(context_path)
            plan = EvidenceDrivenMainAgent().plan_direction(
                DirectionPlanRequest(
                    round_index=-1,
                    context_packet_path=context_path,
                    loop_feedback={"round_type": "agent_generated_baseline"},
                    output_dir=tmp_path / "main_agent",
                )
            )

            assignment = build_worker_assignment(
                context=context,
                direction_plan=plan,
                loop_feedback={},
                round_index=-1,
                attempt_index=0,
                max_steps=4,
                max_runtime_seconds=300,
            )
            payload = assignment.to_payload()
            serialized = json.dumps(payload, ensure_ascii=False, indent=2)

            self.assertLessEqual(len(serialized), WORKER_ASSIGNMENT_MAX_CHARS)
            self.assertEqual("baseline", payload["mode"])
            self.assertEqual("examples/agent_generated_fjsp_solver.py", payload["target_file"])
            self.assertEqual("", payload["method_package"]["package_id"])
            read_paths = [item["path"] for item in payload["read_set"]]
            target_input = next(
                item for item in payload["read_set"] if item["path"] == payload["target_file"]
            )
            self.assertEqual("target_file", target_input["role"])
            self.assertFalse(target_input["required"])
            self.assertFalse(any("reference_solver.py" in path for path in read_paths))
            self.assertFalse(any("standard_fjsp_awls_hgtsa" in path for path in read_paths))
            self.assertIn(".algoforge_worker_inputs/manifest.json", read_paths)
            self.assertIn(
                ".algoforge_worker_inputs/instances/000_tiny.fjs",
                read_paths,
            )
            self.assertNotIn("method_package_catalog", serialized)
            self.assertNotIn("experience_memory", serialized)
            self.assertNotIn("previous_rounds", serialized)
            self.assertTrue(
                any("must not import harness_agent" in value for value in payload["forbidden"])
            )
            self.assertNotIn("optional_solver_diagnostics", payload["runtime_contract"])
            self.assertEqual(
                ["parser_and_model", "simple_legal_constructor", "cli_and_output", "deterministic_fallback"],
                payload["implementation_order"],
            )
            assignment_path = write_worker_assignment(tmp_path / "worker_assignment.json", assignment)
            self.assertEqual(payload, WorkerAssignment.load(assignment_path).to_payload())

    def test_provided_project_assignment_uses_primary_target_and_supporting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = load_context_dict(context_path)
            context["evaluator_protocol"].update(
                {
                    "baseline_source": "provided_project",
                    "worker_target_file": "fjsp/solver.py",
                    "provided_project_read_paths": ["solver.py", "fjsp/model.py"],
                }
            )
            assignment = build_worker_assignment(
                context=context,
                direction_plan={
                    "direction_id": "d000-existing",
                    "hypothesis": "Improve one existing solver mechanism.",
                    "change_scope": ["Preserve the existing CLI and edit one mechanism."],
                },
                loop_feedback={},
                round_index=0,
                attempt_index=0,
                max_steps=4,
                max_runtime_seconds=120,
            )

        self.assertEqual("fjsp/solver.py", assignment.target_file)
        provided = {
            item["path"]: item
            for item in assignment.read_set
            if item.get("role") == "provided_project_source"
        }
        self.assertEqual({"solver.py", "fjsp/model.py"}, set(provided))
        self.assertTrue(all(item["required"] for item in provided.values()))

    def test_verbose_baseline_plan_does_not_repeat_incumbent_narrative_to_worker(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
        }
        component_ids = [
            "cli_parser_writer",
            "canonical_problem_model",
            "search_state",
            "ready_gap_dispatch",
            "full_decoder_guards",
            "bounded_beam_constructor",
            "self_check_and_output",
        ]
        direction = {
            "direction_id": "d-01",
            "worker_objective": "Build a complete bounded constructive baseline.",
            "implementation_order": component_ids,
            "deliverables": [
                {
                    "id": component_id,
                    "behavior": "Implement the complete reachable behavior for " + component_id + "." * 20,
                    "evidence_required": "Point to source evidence and the bounded check." * 20,
                }
                for component_id in component_ids
            ],
            "incumbent_assessment": {
                "implementation_limits": ["No incumbent evidence is available." * 40 for _ in range(4)],
                "bottleneck_hypotheses": ["This is a baseline hypothesis." * 40 for _ in range(4)],
                "evidence_refs": ["implementation_planning_packet.json:255" * 20 for _ in range(12)],
                "unknowns": ["Runtime behavior is unknown." * 40 for _ in range(4)],
            },
            "next_mutation": {
                "target_symbols": [f"examples/agent_generated_fjsp_solver.py:{item}" for item in component_ids],
                "change": "Implement the full constructive baseline." * 30,
                "preserve": ["Preserve the fixed IO contract." * 20 for _ in range(4)],
                "expected_effect": "Produce a legal schedule." * 30,
                "falsification_metrics": ["Compile and bounded smoke evidence." * 20 for _ in range(6)],
            },
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback={},
            round_index=-1,
            attempt_index=0,
            max_steps=4,
            max_runtime_seconds=300,
        )
        serialized = json.dumps(assignment.to_payload(), ensure_ascii=False, indent=2)

        self.assertEqual({}, assignment.latest_feedback)
        staged_ids = ["parser_and_model", "simple_legal_constructor", "cli_and_output", "deterministic_fallback"]
        self.assertEqual(staged_ids, assignment.implementation_order)
        self.assertEqual(staged_ids, [item["id"] for item in assignment.deliverables])
        self.assertNotIn("bounded_beam_constructor", serialized)
        self.assertLessEqual(len(serialized), WORKER_ASSIGNMENT_MAX_CHARS)

    def test_repair_assignment_prioritizes_concrete_result_revalidation_error(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
                "solution_format": "standard_fjsp_schedule_v1",
                "solution_contract": {
                    "format": "standard_fjsp_schedule_v1",
                    "schedule_record_fields": ["job_id", "op_id", "machine_id", "start", "end"],
                },
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "active_method_package": {
                "package_id": "toy",
                "implementation_asset": "knowledge/toy/reference.py",
                "implementation_contract_assets": ["knowledge/toy/contract.json"],
            },
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "Finish the selected method coherently.",
            "method_package_id": "toy",
            "implementation_bundle": {
                "completion_rule": "Implement all components.",
                "required_components": [
                    {"component_id": "decoder", "title": "Decoder"},
                    {"component_id": "search", "title": "Search"},
                ],
            },
        }
        feedback = {
            "current_round_repair": {
                "status": "repair_required",
                "repair_targets": {
                    "agentic_judgment_issues": ["candidate_result_revalidation_failed"],
                    "result_revalidation_top_errors": [
                        {"error": "schedule record 0 is missing job_id"}
                    ],
                },
            }
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback=feedback,
            round_index=0,
            attempt_index=1,
            max_steps=2,
            max_runtime_seconds=60,
            parent_assignment_id="d000-a00",
        )

        self.assertEqual("repair", assignment.mode)
        self.assertEqual(["repair_result_revalidation_00"], assignment.implementation_order)
        self.assertEqual(["repair_result_revalidation_00"], [item["id"] for item in assignment.deliverables])
        self.assertIn("missing job_id", assignment.deliverables[0]["behavior"])
        self.assertEqual("d000-a00", assignment.lineage["parent_assignment_id"])
        self.assertTrue(assignment.latest_feedback)
        repair_contract = assignment.latest_feedback["repair_contract"]
        self.assertEqual(["examples/agent_generated_fjsp_solver.py"], repair_contract["allowed_paths"])
        self.assertIn("result_revalidation_failure", repair_contract["defect_ids"])
        self.assertIn("solver_command_template", repair_contract["input_contract"])
        self.assertEqual(
            "standard_fjsp_schedule_v1",
            repair_contract["output_contract"]["solution_contract"]["format"],
        )

    def test_improvement_assignment_carries_main_incumbent_assessment_and_mutation(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "A wider existing Beam may preserve more useful states.",
            "incumbent_assessment": {
                "verified_capabilities": ["run_beam_construction is reachable."],
                "implementation_limits": ["beam_width=min(3, ...)."],
                "bottleneck_hypotheses": ["State diversity collapses early."],
                "evidence_refs": ["examples/solver.py:712 beam_width"],
                "unknowns": ["Expanded-state count is unmeasured."],
            },
            "next_mutation": {
                "target_symbols": ["solve.beam_width"],
                "change": "Scale the existing Beam under the shared deadline.",
                "preserve": ["Preserve complete decoding and incumbent fallback."],
                "expected_effect": "Retain more distinct partial schedules.",
                "falsification_metrics": ["expanded states", "makespan", "runtime"],
            },
            "change_scope": ["Scale the existing Beam under the shared deadline."],
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback={},
            round_index=0,
            attempt_index=0,
            max_steps=2,
            max_runtime_seconds=60,
        )

        self.assertEqual("Scale the existing Beam under the shared deadline.", assignment.objective)
        self.assertIn(
            "Preserve complete decoding and incumbent fallback.",
            assignment.preserve,
        )
        self.assertEqual(
            ["beam_width=min(3, ...)."],
            assignment.latest_feedback["main_agent_incumbent_assessment"]["implementation_limits"],
        )
        self.assertEqual(
            ["solve.beam_width"],
            assignment.latest_feedback["main_agent_next_mutation"]["target_symbols"],
        )

    def test_high_flex_assignment_loads_playbook_skill_and_read_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = load_context_dict(context_path)
            assignment = build_worker_assignment(
                context=context,
                direction_plan={
                    "direction_id": "d000-high-flex",
                    "method_family": "constructive_search",
                    "method_families": [{"id": "constructive_search", "role": "primary"}],
                    "knowledge_query": ["high_flexibility", "assignment_regret"],
                    "hypothesis": "Use assignment-first routing for a high-flexibility profile.",
                    "change_scope": ["Preserve the incumbent and probe assignment-first controls."],
                    "worker_lane_policy": {
                        "mechanism_selection": "delegated_to_worker",
                        "lane_count": 3,
                    },
                },
                loop_feedback={},
                round_index=0,
                attempt_index=0,
                max_steps=2,
                max_runtime_seconds=60,
            )

        skill_ids = [item["skill_id"] for item in assignment.implementation_skills]
        self.assertIn("high-flexibility-fjsp-playbook", skill_ids)
        self.assertIn("fjsp-constructive-search-worker", skill_ids)
        self.assertNotIn("fjsp-solver-foundation-worker", skill_ids)
        self.assertNotIn("fjsp-experiment-design-worker", skill_ids)
        self.assertTrue(
            any(
                item["path"].endswith("high_flexibility_assignment_first_playbook.md")
                for item in assignment.read_set
            )
        )
        self.assertLessEqual(len(assignment.read_set), 7)
        self.assertFalse(
            any(item["role"] == "requirement_or_io_contract" for item in assignment.read_set)
        )

    def test_agent_generated_high_flex_baseline_stages_scope_skills_and_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = load_context_dict(context_path)
            direction = {
                "direction_id": "d000-high-flex-baseline",
                "method_family": "constructive_search",
                "method_families": [{"id": "constructive_search", "role": "primary"}],
                "knowledge_query": [
                    "constructive_search",
                    "high_flexibility",
                    "idle_gap",
                    "assignment_regret",
                    "decoder",
                ],
                "hypothesis": "Build the high-flexibility baseline in bounded stages.",
                "change_scope": ["Create a complete standalone solver."],
            }

            trial_1 = build_worker_assignment(
                context=context,
                direction_plan=direction,
                loop_feedback={},
                round_index=-1,
                attempt_index=0,
                max_steps=4,
                max_runtime_seconds=300,
            )
            refinement_feedback = {
                "current_round_repair": {
                    "status": "refinement_required",
                    "allow_objective_refinement": True,
                    "repair_targets": {},
                }
            }
            trial_2 = build_worker_assignment(
                context=context,
                direction_plan=direction,
                loop_feedback=refinement_feedback,
                round_index=-1,
                attempt_index=1,
                max_steps=4,
                max_runtime_seconds=300,
                parent_assignment_id=trial_1.assignment_id,
            )
            trial_3 = build_worker_assignment(
                context=context,
                direction_plan=direction,
                loop_feedback=refinement_feedback,
                round_index=-1,
                attempt_index=2,
                max_steps=4,
                max_runtime_seconds=300,
                parent_assignment_id=trial_2.assignment_id,
            )

        self.assertEqual(
            ["parser_and_model", "simple_legal_constructor", "cli_and_output", "deterministic_fallback"],
            [item["id"] for item in trial_1.deliverables],
        )
        self.assertEqual(
            ["fjsp-solver-foundation-worker"],
            [item["skill_id"] for item in trial_1.implementation_skills],
        )
        self.assertNotIn("optional_solver_diagnostics", trial_1.runtime_contract)

        trial_2_ids = [item["id"] for item in trial_2.deliverables]
        self.assertEqual(
            ["earliest_gap", "operation_pressure", "exact_assignment_regret", "low_pressure_order"],
            trial_2_ids,
        )
        trial_2_skills = [item["skill_id"] for item in trial_2.implementation_skills]
        self.assertIn("fjsp-solver-foundation-worker", trial_2_skills)
        self.assertIn("fjsp-constructive-search-worker", trial_2_skills)
        self.assertIn("high-flexibility-fjsp-playbook", trial_2_skills)
        self.assertNotIn("fjsp-experiment-design-worker", trial_2_skills)
        self.assertNotIn("optional_solver_diagnostics", trial_2.runtime_contract)

        self.assertEqual(
            ["activation_telemetry", "mechanism_refinement"],
            [item["id"] for item in trial_3.deliverables],
        )
        self.assertIn(
            "fjsp-experiment-design-worker",
            [item["skill_id"] for item in trial_3.implementation_skills],
        )
        self.assertIn("optional_solver_diagnostics", trial_3.runtime_contract)

        redundant_names = {
            "constructive_multistart_blueprint.md",
            "optimization_playbook.md",
            "idle_critical_beam_implementation_template.md",
            "core_pseudocode.md",
        }
        for assignment in (trial_1, trial_2, trial_3):
            self.assertTrue(
                redundant_names.isdisjoint(Path(item["path"]).name for item in assignment.read_set)
            )

    def test_incomplete_agent_generated_baseline_retries_trial_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = load_context_dict(context_path)
            direction = {
                "direction_id": "d000-high-flex-baseline",
                "method_family": "constructive_search",
                "method_families": [{"id": "constructive_search", "role": "primary"}],
                "knowledge_query": ["high_flexibility", "assignment_regret"],
                "hypothesis": "Build the high-flexibility baseline in bounded stages.",
            }
            retry = build_worker_assignment(
                context=context,
                direction_plan=direction,
                loop_feedback={
                    "current_round_repair": {
                        "status": "repair_required",
                        "baseline_trial": 1,
                        "resume_incomplete_baseline": True,
                        "repair_targets": {
                            "agentic_judgment_issues": ["worker_status_not_usable: timeout"]
                        },
                    }
                },
                round_index=-1,
                attempt_index=1,
                max_steps=4,
                max_runtime_seconds=300,
                parent_assignment_id="d000-high-flex-baseline-a00",
            )

        self.assertEqual("baseline", retry.mode)
        self.assertEqual(1, retry.lineage["baseline_trial"])
        self.assertEqual(
            ["parser_and_model", "simple_legal_constructor", "cli_and_output", "deterministic_fallback"],
            retry.implementation_order,
        )
        target_row = next(item for item in retry.read_set if item["path"] == retry.target_file)
        self.assertFalse(target_row["required"])

    def test_semantic_review_alone_cannot_trigger_repair(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
                "solution_format": "schedule_json",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "Repair inverse tabu semantics.",
            "method_package_id": "",
            "implementation_order": ["tabu_memory"],
        }
        feedback = {
            "current_round_repair": {
                "status": "repair_required",
                "repair_targets": {
                    "algorithm_semantic_review": {
                        "blocking_findings": [
                            {
                                "finding_id": "inverse_tabu_attribute",
                                "source_path": "examples/agent_generated_fjsp_solver.py",
                                "line_start": 920,
                                "line_end": 970,
                                "repair": "Use one canonical inverse move attribute for test and storage.",
                                "required_test": "Apply a move and prove its immediate reverse is tabu.",
                            }
                        ]
                    }
                },
            }
        }

        with self.assertRaisesRegex(ValueError, "semantic-review-only"):
            build_worker_assignment(
                context=context,
                direction_plan=direction,
                loop_feedback=feedback,
                round_index=0,
                attempt_index=1,
                max_steps=2,
                max_runtime_seconds=60,
                parent_assignment_id="d000-a00",
            )

    def test_assignment_rejects_unsafe_target_and_missing_budget(self) -> None:
        payload = {
            "assignment_id": "d000-a00",
            "direction_id": "d000",
            "mode": "baseline",
            "target_file": "../outside.py",
            "objective": "Create a legal solver.",
            "read_set": [{"path": "contract.json", "role": "contract", "required": True}],
            "deliverables": [{"id": "solver", "behavior": "legal solver"}],
            "completion_rule": "Pass Core.",
            "budgets": {},
        }

        with self.assertRaisesRegex(ValueError, "safe relative path"):
            WorkerAssignment.from_payload(payload)

    def test_ja_repair_assignment_does_not_repeat_full_method_bundle(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "active_method_package": {
                "package_id": "toy",
                "implementation_asset": "knowledge/toy/reference.py",
                "implementation_contract_assets": ["knowledge/toy/contract.json"],
            },
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "Implement the complete selected method.",
            "method_package_id": "toy",
            "implementation_bundle": {
                "required_components": [
                    {"component_id": "decoder", "title": "Decoder"},
                    {"component_id": "search", "title": "Search"},
                ],
            },
        }
        feedback = {
            "current_round_repair": {
                "status": "repair_required",
                "repair_targets": {
                    "agentic_judgment_issues": [
                        "agent_generated_solver_imports_backend_package",
                        "agent_generated_solver_self_check_incomplete",
                    ],
                    "agentic_judgment_suggestions": ["Keep the solver standalone and add reachable self-check evidence."],
                },
            }
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback=feedback,
            round_index=-1,
            attempt_index=1,
            max_steps=2,
            max_runtime_seconds=60,
            parent_assignment_id="d000-a00",
        )

        self.assertEqual(
            [
                "repair_agent_generated_solver_imports_backend_package",
                "repair_agent_generated_solver_self_check_incomplete",
            ],
            assignment.implementation_order,
        )
        self.assertNotIn("decoder", assignment.implementation_order)
        self.assertIn("Repair only the blocking items", assignment.objective)
        self.assertIsNone(assignment.method_package["implementation_asset"])
        self.assertNotIn(
            "knowledge/toy/reference.py",
            [item["path"] for item in assignment.read_set],
        )
        self.assertTrue(
            any(value.startswith("Preserve all code unrelated") for value in assignment.preserve)
        )

    def test_legal_no_improvement_feedback_issues_bounded_refinement_assignment(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "active_method_package": {
                "package_id": "toy",
                "implementation_asset": "knowledge/toy/reference.py",
                "implementation_contract_assets": ["knowledge/toy/contract.json"],
                "semantic_assets": ["knowledge/toy/behavior.md"],
            },
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "Improve one current search rule.",
            "method_package_id": "toy",
            "implementation_order": ["decoder", "search", "tabu"],
            "implementation_bundle": {
                "required_components": [
                    {"component_id": "decoder", "title": "Decoder"},
                    {"component_id": "search", "title": "Search"},
                    {"component_id": "tabu", "title": "Tabu"},
                ],
            },
        }
        feedback = {
            "current_round_repair": {
                "status": "refinement_required",
                "allow_objective_refinement": True,
                "avoid": ["legal_but_not_strictly_better"],
                "repair_targets": {},
            }
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback=feedback,
            round_index=0,
            attempt_index=1,
            max_steps=2,
            max_runtime_seconds=60,
            parent_assignment_id="d000-a00",
        )

        self.assertEqual("improvement", assignment.mode)
        self.assertEqual(["same_direction_objective_refinement"], assignment.implementation_order)
        self.assertEqual(
            ["same_direction_objective_refinement"],
            [item["id"] for item in assignment.deliverables],
        )
        self.assertIn("one bounded objective-improvement edit", assignment.objective)
        self.assertIsNone(assignment.method_package["implementation_asset"])

    def test_improvement_reads_incumbent_contract_and_semantics_without_reference_solver(self) -> None:
        context = {
            "task": {"problem_family": "FJSP"},
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "active_method_package": {
                "package_id": "toy",
                "assets": ["knowledge/toy/reference.py", "knowledge/toy/README.md"],
                "implementation_asset": "knowledge/toy/reference.py",
                "implementation_contract_assets": ["knowledge/toy/contract.json"],
                "semantic_assets": ["knowledge/toy/behavior.md"],
            },
            "active_direction_knowledge": {
                "paths": ["knowledge/toy/operator.md"],
            },
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "Improve the current search operator.",
            "method_package_id": "toy",
            # Main 的自由文本路径不能旁路二阶段检索；reference.py 应继续被隔离。
            "knowledge_paths": ["knowledge/toy/reference.py"],
            "implementation_order": ["search"],
            "implementation_bundle": {
                "required_components": [{"component_id": "search", "title": "Search"}],
            },
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback={},
            round_index=0,
            attempt_index=0,
            max_steps=2,
            max_runtime_seconds=60,
        )

        read_paths = [item["path"] for item in assignment.read_set]
        self.assertEqual("improvement", assignment.mode)
        self.assertIsNone(assignment.method_package["implementation_asset"])
        self.assertIn("examples/agent_generated_fjsp_solver.py", read_paths)
        incumbent_input = next(
            item
            for item in assignment.read_set
            if item["path"] == "examples/agent_generated_fjsp_solver.py"
        )
        self.assertEqual("incumbent", incumbent_input["role"])
        self.assertTrue(incumbent_input["required"])
        self.assertIn("knowledge/toy/contract.json", read_paths)
        self.assertIn("knowledge/toy/behavior.md", read_paths)
        self.assertIn("knowledge/toy/operator.md", read_paths)
        self.assertNotIn("knowledge/toy/reference.py", read_paths)

    def test_delegated_assignment_deduplicates_staged_manifest_from_provided_project(self) -> None:
        context = {
            "task": {
                "problem_family": "FJSP",
                "instances": [{"id": "dp17a", "path": "inputs/dp17a.txt"}],
            },
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
                "provided_project_read_paths": [
                    ".algoforge_worker_inputs/manifest.json",
                    "examples/helper.py",
                ],
            },
            "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
            "active_method_package": {
                "package_id": "toy",
                "implementation_contract_assets": ["knowledge/toy/contract.json"],
            },
        }
        direction = {
            "direction_id": "d000",
            "hypothesis": "Run one delegated lane.",
            "method_package_id": "toy",
            "implementation_order": ["search"],
            "implementation_bundle": {
                "required_components": [{"component_id": "search", "title": "Search"}],
            },
            "worker_lane_policy": {
                "mechanism_selection": "delegated_to_worker",
                "lane_count": 3,
            },
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback={},
            round_index=0,
            attempt_index=0,
            max_steps=2,
            max_runtime_seconds=60,
        )

        manifest_rows = [
            item
            for item in assignment.read_set
            if item["path"] == ".algoforge_worker_inputs/manifest.json"
        ]
        self.assertEqual(1, len(manifest_rows))
        self.assertEqual("instance_manifest", manifest_rows[0]["role"])
        self.assertEqual(len(assignment.read_set), len({item["path"] for item in assignment.read_set}))

    def test_assignment_preserves_tournament_stage_lineage(self) -> None:
        context = {
            "task": {
                "problem_family": "FJSP",
                "instances": [{"id": "min-lag", "path": "inputs/min-lag.txt"}],
            },
            "evaluator_protocol": {
                "solver_command_template": "python solver.py --input {instance}",
            },
            "edit_policy": {"allowed_paths": ["solver.py"], "forbidden_paths": ["outputs"]},
        }
        direction = {
            "direction_id": "min-lag-local-search",
            "worker_objective": "Implement the first lag-aware local-search stage.",
            "experiment_stage": "research_tournament",
            "method_package_id": "fjsp_min_time_lag_coupled_local_search",
            "implementation_order": [
                "lag_graph_decoder",
                "transactional_search_state",
                "cross_machine_reinsertion",
            ],
            "implementation_bundle": {
                "required_components": [
                    {"component_id": "lag_graph_decoder", "title": "Lag decoder"},
                    {"component_id": "transactional_search_state", "title": "Transactions"},
                    {"component_id": "cross_machine_reinsertion", "title": "Cross-machine moves"},
                ],
            },
            "worker_lane": {
                "track_id": "direct_evidence",
                "stage": 0,
                "parent_checkpoint": None,
                "verified_components": [],
                "mechanism_selection": "family_hypothesis_tournament",
            },
        }

        assignment = build_worker_assignment(
            context=context,
            direction_plan=direction,
            loop_feedback={},
            round_index=0,
            attempt_index=0,
            max_steps=2,
            max_runtime_seconds=60,
        )

        self.assertEqual("direct_evidence", assignment.lineage["track"])
        self.assertEqual(0, assignment.lineage["stage"])
        self.assertIsNone(assignment.lineage["parent_checkpoint"])
        self.assertEqual([], assignment.lineage["verified_components"])


if __name__ == "__main__":
    unittest.main()
