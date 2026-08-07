from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.main import DirectionPlanRequest, EvidenceDrivenMainAgent
from harness_agent.context.loader import load_context_dict
from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.context.worker import (
    WORKER_ASSIGNMENT_MAX_CHARS,
    WORKER_ASSIGNMENT_SOFT_CHARS,
    build_worker_assignment,
    write_worker_assignment,
)
from harness_agent.worker import WorkerAssignment


ROOT = Path(__file__).resolve().parents[1]


class WorkerAssignmentTests(unittest.TestCase):
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
            diagnostics = payload["runtime_contract"]["optional_solver_diagnostics"]
            self.assertIn("candidate_runs", diagnostics["bounded_schema"])
            self.assertIn("search_counters", diagnostics["bounded_schema"])
            self.assertIn("never affect", diagnostics["purpose"])

            assignment_path = write_worker_assignment(tmp_path / "worker_assignment.json", assignment)
            self.assertEqual(payload, WorkerAssignment.load(assignment_path).to_payload())

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
        self.assertEqual(component_ids, assignment.implementation_order)
        self.assertEqual(component_ids, [item["id"] for item in assignment.deliverables])
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

    def test_vague_legal_no_improvement_feedback_cannot_issue_repair_assignment(self) -> None:
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
                "avoid": ["legal_but_not_strictly_better"],
                "repair_targets": {},
            }
        }

        with self.assertRaisesRegex(ValueError, "concrete repair_targets"):
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

    def test_distributed_assignment_exposes_contract_rag_docs_and_fixed_evaluator(self) -> None:
        evaluator_path = "examples/fjsp_distributed_transfer_evaluator.py"
        context = {
            "task": {
                "problem_family": "fjsp_distributed_transfer",
                "instances": [
                    {
                        "id": "DFM01",
                        "path": (
                            "ALL-Input-Information/10-distributed-FJSP/10-Instance/small size/"
                            "DFM01_10x2x6.txt"
                        ),
                    }
                ],
            },
            "evaluator_protocol": {
                "solver_command_template": (
                    "python examples/agent_generated_fjsp_solver.py --input {instance} "
                    "--output {solution} --seed {seed}"
                ),
                "evaluator_command_template": (
                    f"python {evaluator_path} --instance {{instance}} "
                    "--solution {solution} --metrics {metrics}"
                ),
                "solution_format": "distributed_fjsp_schedule_v1",
                "solution_contract": {
                    "format": "distributed_fjsp_schedule_v1",
                    "schedule_record_fields": [
                        "job_id",
                        "op_id",
                        "factory_id",
                        "machine_id",
                        "start",
                        "end",
                    ],
                },
            },
            "edit_policy": {
                "allowed_paths": ["examples"],
                "forbidden_paths": [".git"],
            },
            "instance_diagnostics": {
                "status": "available",
                "summary": {
                    "profiled_count": 1,
                    "distributed_transfer_instance_count": 1,
                },
                "instances": [
                    {
                        "variant": "fjsp_distributed_transfer",
                        "has_distributed_transfer": True,
                    }
                ],
            },
            "documents": [
                {"path": "ALL-Input-Information/10-distributed-FJSP/10-Problem description.md"},
                {"path": "ALL-Input-Information/10-distributed-FJSP/10-Instances/README.md"},
            ],
            "active_direction_knowledge": {
                "paths": [
                    "knowledge/rag_generated_cards/fjsp_distributed_transfer/d000_factory_transfer.md"
                ],
            },
            "method_package_catalog": {
                "active_features": [
                    "fjsp_distributed_transfer",
                    "factory_assignment",
                    "transfer_time",
                    "energy_consumption",
                ],
                "packages": [],
            },
            "baseline_generation": {"source": "agent_generated"},
        }
        direction = {
            "direction_id": "d000-distributed",
            "worker_objective": "Build a distributed FJSP solver with factory transfer timing.",
            "knowledge_query": ["factory_assignment", "transfer_time", "energy_aware_scheduling"],
            "change_scope": ["Implement factory-aware parsing, decoding, and metrics."],
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
        payload = assignment.to_payload()
        runtime = payload["runtime_contract"]
        read_paths = [item["path"] for item in payload["read_set"]]

        self.assertEqual("distributed_fjsp_schedule_v1", runtime["solution_format"])
        self.assertEqual(
            ["job_id", "op_id", "factory_id", "machine_id", "start", "end"],
            runtime["solution_contract"]["schedule_record_fields"],
        )
        self.assertIn(evaluator_path, runtime["evaluator_command_template"])
        self.assertIn(evaluator_path, runtime["forbidden_paths"])
        self.assertIn(evaluator_path, payload["forbidden"])
        self.assertIn("factory_assignment_guard", runtime["variant_required_code_capabilities"])
        self.assertIn("transfer_time_precedence_guard", runtime["variant_required_code_capabilities"])
        self.assertIn("energy_and_workload_metric_guard", runtime["variant_required_code_capabilities"])
        self.assertIn(
            "knowledge/rag_generated_cards/fjsp_distributed_transfer/d000_factory_transfer.md",
            read_paths,
        )
        self.assertIn(".algoforge_worker_inputs/instances/000_DFM01.txt", read_paths)
        self.assertIn(
            ".algoforge_worker_inputs/docs/000_10-Problem_description.md",
            read_paths,
        )
        self.assertIn(".algoforge_worker_inputs/docs/001_README.md", read_paths)

    def test_priority_assignment_exposes_contract_rag_docs_and_fixed_evaluator(self) -> None:
        evaluator_path = "examples/fjsp_job_priority_evaluator.py"
        context = {
            "task": {
                "problem_family": "fjsp_job_priority",
                "instances": [
                    {
                        "id": "mt10c1_priority",
                        "path": (
                            "ALL-Input-Information/11-priority-FJSP/11-Instances/"
                            "fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722.txt"
                        ),
                    }
                ],
                "objectives": [
                    {"name": "makespan", "direction": "minimize", "priority": 1},
                    {"name": "priority_completion_time", "direction": "minimize", "priority": 2},
                ],
            },
            "evaluator_protocol": {
                "solver_command_template": (
                    "python examples/agent_generated_fjsp_solver.py --input {instance} "
                    "--output {solution} --seed {seed}"
                ),
                "evaluator_command_template": (
                    f"python {evaluator_path} --instance {{instance}} "
                    "--solution {solution} --metrics {metrics}"
                ),
                "solution_format": "standard_fjsp_schedule_v1",
                "solution_contract": {
                    "format": "standard_fjsp_schedule_v1",
                    "schedule_record_fields": ["job_id", "op_id", "machine_id", "start", "end"],
                    "objective_metrics": ["makespan", "priority_completion_time"],
                },
            },
            "edit_policy": {
                "allowed_paths": ["examples"],
                "forbidden_paths": [".git"],
            },
            "instance_diagnostics": {
                "status": "available",
                "summary": {
                    "profiled_count": 1,
                    "priority_job_instance_count": 1,
                    "priority_job_count_max": 3,
                },
                "instances": [
                    {
                        "variant": "fjsp_priority",
                        "has_job_priority": True,
                        "priority_job_count": 3,
                        "priority_job_ids": [1, 6, 8],
                    }
                ],
            },
            "documents": [
                {"path": "ALL-Input-Information/11-priority-FJSP/11-Problem description.md"},
                {"path": "ALL-Input-Information/11-priority-FJSP/11-Instance format.md"},
            ],
            "active_direction_knowledge": {
                "paths": [
                    "knowledge/rag_generated_cards/fjsp_job_priority/d000_priority_tail_and_objective.md"
                ],
            },
            "method_package_catalog": {
                "active_features": [
                    "fjsp_job_priority",
                    "job_priority",
                    "priority_jobs",
                    "priority_completion_time",
                    "multi_objective",
                    "lexicographic_objective",
                ],
                "packages": [],
            },
            "baseline_generation": {"source": "agent_generated"},
        }
        direction = {
            "direction_id": "d000-priority",
            "worker_objective": "Build a priority-FJSP solver that reads priority tail and optimizes priority completion.",
            "knowledge_query": ["priority_completion_time", "priority_dispatch_rule"],
            "change_scope": ["Implement priority-tail parsing, metric calculation, and priority-aware dispatch."],
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
        payload = assignment.to_payload()
        runtime = payload["runtime_contract"]
        read_paths = [item["path"] for item in payload["read_set"]]

        self.assertEqual("standard_fjsp_schedule_v1", runtime["solution_format"])
        self.assertEqual(
            ["job_id", "op_id", "machine_id", "start", "end"],
            runtime["solution_contract"]["schedule_record_fields"],
        )
        self.assertIn(
            {"name": "priority_completion_time", "direction": "minimize", "priority": 2},
            runtime["objectives"],
        )
        self.assertIn(evaluator_path, runtime["evaluator_command_template"])
        self.assertIn(evaluator_path, runtime["forbidden_paths"])
        self.assertIn(evaluator_path, payload["forbidden"])
        self.assertIn("priority_tail_parser_guard", runtime["variant_required_code_capabilities"])
        self.assertIn("priority_completion_metric_guard", runtime["variant_required_code_capabilities"])
        self.assertIn(
            "knowledge/rag_generated_cards/fjsp_job_priority/d000_priority_tail_and_objective.md",
            read_paths,
        )
        self.assertIn(".algoforge_worker_inputs/instances/000_mt10c1_priority.txt", read_paths)
        self.assertIn(
            ".algoforge_worker_inputs/docs/000_11-Problem_description.md",
            read_paths,
        )
        self.assertIn(".algoforge_worker_inputs/docs/001_11-Instance_format.md", read_paths)


if __name__ == "__main__":
    unittest.main()
