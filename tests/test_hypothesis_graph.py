from __future__ import annotations

import json
import unittest

from harness_agent.agents.hypothesis import (
    HypothesisRecord,
    algorithm_semantic_direction_recovered,
    build_experience_memory,
    direction_has_verified_blocking_semantic_finding,
    direction_recovered,
    direction_validated_lesson_eligible,
    make_direction_id,
    render_hypothesis_graph_markdown,
    summarize_direction_graph,
    summarize_hypothesis_graph,
    validated_lesson_confidence,
)


class HypothesisGraphTests(unittest.TestCase):
    def test_empty_graph_requests_diverse_exploration(self) -> None:
        summary = summarize_hypothesis_graph([])

        self.assertEqual(0, summary["record_count"])
        self.assertIsNone(summary["best_hypothesis_id"])
        self.assertIn("explore diverse baseline rules", summary["mutation_guidance"][0])

    def test_graph_summary_promotes_prunes_and_guides_mutation(self) -> None:
        records = [
            _record("h0", 0, None, score=-10.0, delta=None),
            _record("h1", 1, "h0", score=-8.0, delta=2.0),
            _record("h2", 2, "h1", score=-12.0, delta=-4.0),
            _record("h3", 3, "h2", score=None, delta=None, status="missing_summary"),
        ]

        summary = summarize_hypothesis_graph(records, max_promoted=1, max_pruned=2)
        decisions = {item["hypothesis_id"]: item["decision"] for item in summary["decisions"]}

        self.assertEqual("h1", summary["best_hypothesis_id"])
        self.assertEqual("promote", decisions["h1"])
        self.assertEqual("prune", decisions["h2"])
        self.assertEqual("prune", decisions["h3"])
        self.assertEqual("mutate", decisions["h0"])
        self.assertIn("promote", summary["decision_counts"])
        self.assertTrue(any("Preserve" in item for item in summary["mutation_guidance"]))

        markdown = render_hypothesis_graph_markdown(summary)
        self.assertIn("Hypothesis Graph Summary", markdown)
        self.assertIn("h1", markdown)
        self.assertIn("promote", markdown)

    def test_direction_graph_groups_attempts_and_method_level_lessons(self) -> None:
        rounds = [
            {
                "round_index": 0,
                "decision": "promoted",
                "candidate_key": [-90.0],
                "incumbent_key_after": [-90.0],
                "worker_status": "applied",
                "worker_changed_files": ["examples/solver.py"],
                "context_packet_path": "round_000/context_packet.json",
                "cycle_dir": "round_000",
                "patch_path": "round_000/worker_changes.patch",
                "delta_path": "round_000/worker_worktree_delta.json",
                "mechanism_activation": {
                    "status": "passed",
                    "passed": True,
                    "declared_check_count": 1,
                    "required_check_count": 1,
                    "required_failure_count": 0,
                    "checks": [
                        {
                            "id": "beam_telemetry",
                            "path": "best_metrics.activation.beam",
                            "required": True,
                            "passed": True,
                            "observed": 9,
                            "description": "Beam telemetry reached the expected path.",
                        }
                    ],
                },
                "round_reflection": {
                    "hypothesis_outcome": "supported",
                    "summary": "The candidate preserved the incumbent and activated bounded insertion.",
                    "candidate_findings": [
                        {
                            "candidate_id": "candidate_round_000",
                            "outcome": "supported",
                            "evidence": ["objective_key=[-90.0]"],
                            "causal_interpretation": "The measured run exercised the intended insertion mechanism.",
                        }
                    ],
                    "next_action": {
                        "action": "scale",
                        "rationale": "Keep the activated mechanism and broaden its search radius.",
                        "required_activation_checks": ["beam_telemetry"],
                    },
                },
                "proposal_diagnostics": {
                    "summary": "Add bounded insertion search.",
                    "strategy_intent": "Use knowledge_cards and loop_feedback to preserve the incumbent and add insertion.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "bounded_insertion",
                            "type": "local_search_operator",
                            "target_files": ["examples/solver.py"],
                            "evidence_used": ["knowledge_cards", "loop_feedback"],
                        }
                    ],
                    "in_round_repair": {
                        "attempts": [
                            {
                                "attempt_index": 0,
                                "worker_status": "applied",
                                "changed_files": ["examples/helper.py"],
                                "candidate_key": [float("-inf")],
                                "failure_signatures": ["proposal_apply_rejections"],
                                "agentic_judgment": {
                                    "accepted": False,
                                    "issues": [
                                        "agent_generated_solver_quality_contract_missing",
                                        "agent_generated_solver_self_check_incomplete",
                                    ],
                                    "checks": {
                                        "agent_generated_solver_quality_risks": [
                                            "agent_generated_solver: missing base capabilities: active_io_parser, operation_level_ready_list_constructor",
                                        ],
                                        "agent_generated_solver_self_check_risks": [
                                            "solver_contract_self_check missing implemented capabilities: active_io_parser",
                                        ],
                                        "agent_generated_solver_quality_contract": {
                                            "active_features": ["alternative_machines"],
                                            "required_code_capabilities": [
                                                "active_io_parser",
                                                "operation_level_ready_list_constructor",
                                            ],
                                            "variant_required_code_capabilities": [],
                                        },
                                    },
                                },
                                "semantic_review": {
                                    "status": "repair_required",
                                    "accepted": False,
                                    "findings": [
                                        {
                                            "finding_id": "reverse_move",
                                            "category": "reverse_move_memory",
                                            "blocking": True,
                                            "confidence": 0.95,
                                            "source_path": "examples/solver.py",
                                            "line_start": 10,
                                            "line_end": 12,
                                            "knowledge_path": "knowledge/tabu_contract.md",
                                            "repair": "Store the inverse move attribute.",
                                            "required_test": "Prove immediate reversal remains tabu.",
                                        }
                                    ],
                                },
                            },
                            {
                                "attempt_index": 1,
                                "worker_status": "applied",
                                "changed_files": ["examples/solver.py"],
                                "candidate_key": [-90.0],
                                "failure_signatures": [],
                                "semantic_review": {
                                    "status": "pass",
                                    "accepted": True,
                                    "findings": [],
                                },
                            },
                        ]
                    },
                },
                "promotion_check": {"promoted": True},
                "smoke_gate": {"passed": True},
            }
        ]

        graph = summarize_direction_graph(rounds)
        memory = build_experience_memory(rounds, problem_family="FJSP")

        self.assertEqual("direction", graph["round_semantics"])
        self.assertEqual(1, graph["direction_count"])
        self.assertEqual(2, graph["attempt_count"])
        self.assertEqual("validated_success", graph["directions"][0]["status"])
        self.assertEqual("repair", graph["directions"][0]["attempts"][1]["kind"])
        self.assertEqual("passed", graph["directions"][0]["mechanism_activation"]["status"])
        self.assertEqual("supported", graph["directions"][0]["hypothesis_outcome"])
        self.assertEqual("supported", graph["directions"][0]["round_reflection"]["hypothesis_outcome"])

        lessons = memory["memory_tiers"]["candidate_lessons"]
        validated_lessons = memory["memory_tiers"]["validated_lessons"]
        self.assertTrue(any(item["lesson_type"] == "successful_strategy" for item in lessons))
        self.assertTrue(any(item["lesson_type"] == "repair_recovery" for item in lessons))
        self.assertTrue(any(item["lesson_type"] == "agent_generated_quality_gap" for item in lessons))
        self.assertTrue(any(item["lesson_type"] == "algorithm_semantic_gap" for item in lessons))
        self.assertEqual(1, len(validated_lessons))
        self.assertEqual("supported", validated_lessons[0]["hypothesis_outcome"])
        self.assertEqual(
            "core_activation_and_semantic_validated",
            validated_lessons[0]["confidence"],
        )
        self.assertTrue(memory["write_policy"]["no_instance_score_as_method"])
        reusable_memory = json.dumps(memory["memory_tiers"], ensure_ascii=False)
        for forbidden_field in (
            "score_value",
            "avg_gap_pct",
            "best_known_makespan",
            "candidate_key",
            "incumbent_key_after",
            "schedule",
            "objective_key",
        ):
            self.assertNotIn(f'"{forbidden_field}"', reusable_memory)
        quality_memory = memory["agent_generated_quality_memory"]
        self.assertEqual(1, quality_memory["rejected_attempt_count"])
        self.assertEqual(1, quality_memory["recovered_direction_count"])
        self.assertIn("active_io_parser", quality_memory["recurring_quality_risks"][0]["text"])
        self.assertIn("parser/representation/constructor", quality_memory["next_prompt_rule"])
        self.assertEqual(1, memory["self_evolution_metrics"]["agent_quality_rejected_attempt_count"])
        semantic_memory = memory["algorithm_semantic_memory"]
        self.assertEqual(1, semantic_memory["repair_required_attempt_count"])
        self.assertEqual(1, semantic_memory["recovered_direction_count"])
        self.assertIn("reverse_move_memory", semantic_memory["recurring_categories"][0]["text"])
        self.assertGreaterEqual(memory["skill_usage_summary"]["promoted_usage_count"], 1)

    def test_direction_graph_prefers_explicit_parent_direction_id_over_predecessor(self) -> None:
        rounds = [
            _direction_round(0, decision="promoted"),
            _direction_round(1, decision="rolled_back"),
            _direction_round(2, decision="rolled_back"),
            _direction_round(3, decision="promoted"),
        ]
        direction_ids = [
            make_direction_id(round_record["round_index"], round_record["proposal_diagnostics"])
            for round_record in rounds
        ]
        rounds[1]["direction_plan"]["parent_direction_id"] = direction_ids[0]
        rounds[2]["direction_plan"]["parent_direction_id"] = direction_ids[1]
        rounds[3]["direction_plan"]["parent_direction_id"] = direction_ids[1]

        partial_graph = summarize_direction_graph(rounds[:3])
        full_graph = summarize_direction_graph(rounds)

        self.assertEqual(
            [None, direction_ids[0], direction_ids[1]],
            [item["parent_id"] for item in partial_graph["directions"]],
        )
        self.assertEqual(direction_ids[0], partial_graph["active_parent_id"])
        self.assertEqual(direction_ids[1], full_graph["directions"][3]["parent_id"])
        self.assertEqual(direction_ids[3], full_graph["active_parent_id"])

    def test_direction_graph_falls_back_to_immediate_predecessor_after_promotion(self) -> None:
        rounds = [
            _direction_round(0, decision="promoted"),
            _direction_round(1, decision="rolled_back"),
            _direction_round(2, decision="rolled_back"),
            _direction_round(3, decision="promoted"),
        ]

        partial_graph = summarize_direction_graph(rounds[:3])
        full_graph = summarize_direction_graph(rounds)
        direction_ids = [item["direction_id"] for item in full_graph["directions"]]

        self.assertEqual(
            [None, direction_ids[0], direction_ids[1]],
            [item["parent_id"] for item in partial_graph["directions"]],
        )
        self.assertEqual(direction_ids[0], partial_graph["active_parent_id"])
        self.assertEqual(
            [None, direction_ids[0], direction_ids[1], direction_ids[2]],
            [item["parent_id"] for item in full_graph["directions"]],
        )
        self.assertEqual(direction_ids[3], full_graph["active_parent_id"])

    def test_direction_graph_resolves_main_plan_parent_ids_to_graph_node_ids(self) -> None:
        rounds = [
            _direction_round(0, decision="promoted"),
            _direction_round(1, decision="rolled_back"),
            _direction_round(2, decision="rolled_back"),
        ]
        for index, round_record in enumerate(rounds):
            round_record["direction_plan"]["direction_id"] = f"d{index:03d}"
            if index:
                round_record["direction_plan"]["parent_direction_id"] = f"d{index - 1:03d}"

        graph = summarize_direction_graph(rounds)
        graph_ids = [item["direction_id"] for item in graph["directions"]]

        self.assertEqual([None, graph_ids[0], graph_ids[1]], [item["parent_id"] for item in graph["directions"]])
        self.assertNotEqual("d000", graph_ids[0])

    def test_blocking_semantic_finding_stays_blocked_after_unavailable_review(self) -> None:
        direction = summarize_direction_graph(
            [
                _semantic_round(
                    round_index=0,
                    mechanism_activation={
                        "status": "passed",
                        "passed": True,
                        "declared_check_count": 1,
                        "required_check_count": 1,
                        "required_failure_count": 0,
                        "checks": [{"id": "telemetry", "path": "solution.json#/diagnostics/telemetry"}],
                    },
                    attempts=[
                        {
                            "attempt_index": 0,
                            "worker_status": "applied",
                            "changed_files": ["examples/solver.py"],
                            "candidate_key": [-90.0],
                            "failure_signatures": ["proposal_apply_rejections"],
                            "semantic_review": {
                                "status": "repair_required",
                                "accepted": False,
                                "findings": [{"blocking": True, "category": "verified_contract_violation"}],
                            },
                        },
                        {
                            "attempt_index": 1,
                            "worker_status": "applied",
                            "changed_files": ["examples/solver.py"],
                            "candidate_key": [-89.0],
                            "failure_signatures": ["legal_but_not_strictly_better"],
                            "semantic_review": {
                                "status": "unavailable",
                                "accepted": False,
                                "findings": [],
                            },
                        },
                    ],
                )
            ]
        )["directions"][0]

        self.assertTrue(direction_has_verified_blocking_semantic_finding(direction))
        self.assertFalse(algorithm_semantic_direction_recovered(direction))
        self.assertFalse(direction_validated_lesson_eligible(direction))

    def test_blocking_semantic_finding_stays_blocked_after_non_repair_pass_review(self) -> None:
        direction = summarize_direction_graph(
            [
                _semantic_round(
                    round_index=0,
                    mechanism_activation={
                        "status": "passed",
                        "passed": True,
                        "declared_check_count": 1,
                        "required_check_count": 1,
                        "required_failure_count": 0,
                        "checks": [{"id": "telemetry", "path": "solution.json#/diagnostics/telemetry"}],
                    },
                    attempts=[
                        {
                            "attempt_index": 0,
                            "worker_status": "applied",
                            "changed_files": ["examples/solver.py"],
                            "candidate_key": [-90.0],
                            "failure_signatures": ["proposal_apply_rejections"],
                            "semantic_review": {
                                "status": "repair_required",
                                "accepted": False,
                                "findings": [{"blocking": True, "category": "verified_contract_violation"}],
                            },
                        },
                        {
                            "attempt_index": 1,
                            "worker_status": "applied",
                            "changed_files": ["examples/solver.py"],
                            "candidate_key": [-89.0],
                            "failure_signatures": ["legal_but_not_strictly_better"],
                            "semantic_review": {
                                "status": "pass",
                                "accepted": True,
                                "findings": [],
                            },
                        },
                    ],
                )
            ]
        )["directions"][0]

        self.assertTrue(direction_has_verified_blocking_semantic_finding(direction))
        self.assertFalse(algorithm_semantic_direction_recovered(direction))
        self.assertFalse(direction_validated_lesson_eligible(direction))

    def test_blocking_semantic_finding_clears_after_explicit_repair_pass(self) -> None:
        direction = summarize_direction_graph(
            [
                _semantic_round(
                    round_index=0,
                    mechanism_activation={
                        "status": "passed",
                        "passed": True,
                        "declared_check_count": 1,
                        "required_check_count": 1,
                        "required_failure_count": 0,
                        "checks": [{"id": "telemetry", "path": "solution.json#/diagnostics/telemetry"}],
                    },
                    attempts=[
                        {
                            "attempt_index": 0,
                            "worker_status": "applied",
                            "changed_files": ["examples/solver.py"],
                            "candidate_key": [-90.0],
                            "failure_signatures": ["proposal_apply_rejections"],
                            "semantic_review": {
                                "status": "repair_required",
                                "accepted": False,
                                "findings": [{"blocking": True, "category": "verified_contract_violation"}],
                            },
                        },
                        {
                            "attempt_index": 1,
                            "worker_status": "applied",
                            "changed_files": ["examples/solver.py"],
                            "candidate_key": [-89.0],
                            "failure_signatures": [],
                            "semantic_review": {
                                "status": "warning",
                                "accepted": True,
                                "findings": [],
                            },
                        },
                    ],
                )
            ]
        )["directions"][0]

        self.assertFalse(direction_has_verified_blocking_semantic_finding(direction))
        self.assertTrue(algorithm_semantic_direction_recovered(direction))
        self.assertTrue(direction_validated_lesson_eligible(direction))

    def test_not_declared_activation_does_not_enter_validated_memory(self) -> None:
        rounds = [
            _semantic_round(
                round_index=0,
                mechanism_activation={
                    "status": "not_declared",
                    "passed": True,
                    "declared_check_count": 0,
                    "required_check_count": 0,
                    "required_failure_count": 0,
                    "checks": [],
                },
                attempts=[
                    {
                        "attempt_index": 0,
                        "worker_status": "applied",
                        "changed_files": ["examples/solver.py"],
                        "candidate_key": [-88.0],
                        "failure_signatures": [],
                        "semantic_review": {
                            "status": "pass",
                            "accepted": True,
                            "findings": [],
                        },
                    }
                ],
            )
        ]

        memory = build_experience_memory(rounds, problem_family="FJSP")
        validated = memory["memory_tiers"]["validated_lessons"]
        direction = summarize_direction_graph(rounds)["directions"][0]

        self.assertEqual([], validated)
        self.assertFalse(direction_validated_lesson_eligible(direction))
        self.assertEqual("candidate", validated_lesson_confidence(direction))


def _record(
    hypothesis_id: str,
    round_index: int,
    parent_id: str | None,
    *,
    score: float | None,
    delta: float | None,
    status: str = "evaluated",
) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_id=hypothesis_id,
        parent_id=parent_id,
        round_index=round_index,
        source="test",
        solver="local-search",
        status=status,
        score_metric="avg_gap_pct" if score is not None else None,
        score_value=score,
        delta_from_parent=delta,
        summary={},
        artifacts={},
        note="test",
        candidate_id=f"candidate_{hypothesis_id}",
        candidate_results=[],
    )


def _semantic_round(
    *,
    round_index: int,
    mechanism_activation: dict[str, object],
    attempts: list[dict[str, object]],
    hypothesis_name: str = "critical_block_probe",
) -> dict[str, object]:
    return {
        "round_index": round_index,
        "decision": "promoted",
        "candidate_key": [-90.0],
        "incumbent_key_after": [-90.0],
        "worker_status": "applied",
        "worker_changed_files": ["examples/solver.py"],
        "context_packet_path": f"round_{round_index:03d}/context_packet.json",
        "cycle_dir": f"round_{round_index:03d}",
        "patch_path": f"round_{round_index:03d}/worker_changes.patch",
        "delta_path": f"round_{round_index:03d}/worker_worktree_delta.json",
        "mechanism_activation": mechanism_activation,
        "round_reflection": {
            "hypothesis_outcome": "mixed",
            "summary": "Semantic recovery regression test.",
            "candidate_findings": [],
            "next_action": {"action": "probe", "rationale": "Keep the same direction under review."},
        },
        "proposal_diagnostics": {
            "summary": "Semantic blocker regression test.",
            "strategy_intent": "Keep the current direction visible to the learning loop.",
            "rule_operator_hypotheses": [
                {
                    "name": hypothesis_name,
                    "type": "local_search_operator",
                    "target_files": ["examples/solver.py"],
                    "evidence_used": ["loop_feedback"],
                }
            ],
            "in_round_repair": {"attempts": attempts},
        },
    }


def _direction_round(
    round_index: int,
    *,
    decision: str,
) -> dict[str, object]:
    return {
        "round_index": round_index,
        "decision": decision,
        "candidate_key": [-90.0 + float(round_index)],
        "incumbent_key_after": [-90.0],
        "worker_status": "applied",
        "worker_changed_files": [f"examples/solver_{round_index}.py"],
        "context_packet_path": f"round_{round_index:03d}/context_packet.json",
        "cycle_dir": f"round_{round_index:03d}",
        "patch_path": f"round_{round_index:03d}/worker_changes.patch",
        "delta_path": f"round_{round_index:03d}/worker_worktree_delta.json",
        "mechanism_activation": {
            "status": "passed",
            "passed": True,
            "declared_check_count": 1,
            "required_check_count": 1,
            "required_failure_count": 0,
            "checks": [{"id": f"telemetry_{round_index}", "path": "solution.json#/diagnostics/telemetry"}],
        },
        "round_reflection": {
            "hypothesis_outcome": "mixed" if decision != "promoted" else "supported",
            "summary": f"Direction lineage regression round {round_index}.",
            "candidate_findings": [],
            "next_action": {"action": "probe", "rationale": "Track experiment lineage independently from incumbent ancestry."},
        },
        "direction_plan": {
            "title": f"direction_{round_index}",
            "strategy_type": "local_search_operator",
        },
        "proposal_diagnostics": {
            "summary": f"Direction lineage regression round {round_index}.",
            "strategy_intent": "Exercise direction lineage without mutating incumbent ancestry semantics.",
            "rule_operator_hypotheses": [
                {
                    "name": f"direction_{round_index}",
                    "type": "local_search_operator",
                    "target_files": [f"examples/solver_{round_index}.py"],
                    "evidence_used": ["loop_feedback"],
                }
            ],
        },
        "promotion_check": {"promoted": decision == "promoted"},
        "smoke_gate": {"passed": True},
    }


if __name__ == "__main__":
    unittest.main()
