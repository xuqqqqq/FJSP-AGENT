from __future__ import annotations

import json
import unittest

from harness_agent.agents.hypothesis import (
    HypothesisRecord,
    build_experience_memory,
    render_hypothesis_graph_markdown,
    summarize_direction_graph,
    summarize_hypothesis_graph,
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

    def test_validated_lessons_allow_unavailable_semantic_review_but_block_failed_activation(self) -> None:
        rounds = [
            {
                "round_index": 0,
                "decision": "promoted",
                "candidate_key": [-88.0],
                "incumbent_key_after": [-88.0],
                "worker_status": "applied",
                "worker_changed_files": ["examples/solver.py"],
                "context_packet_path": "round_000/context_packet.json",
                "cycle_dir": "round_000",
                "patch_path": "round_000/worker_changes.patch",
                "delta_path": "round_000/worker_worktree_delta.json",
                "mechanism_activation": {
                    "status": "not_declared",
                    "passed": True,
                    "declared_check_count": 0,
                    "required_check_count": 0,
                    "required_failure_count": 0,
                    "checks": [],
                },
                "round_reflection": {
                    "hypothesis_outcome": "mixed",
                    "summary": "The change improved the incumbent but did not isolate every causal factor.",
                    "candidate_findings": [],
                    "next_action": {"action": "probe", "rationale": "Add a sharper ablation next round."},
                },
                "proposal_diagnostics": {
                    "summary": "Probe mixed critical-block insertion.",
                    "rule_operator_hypotheses": [{"name": "critical_block_probe", "type": "local_search_operator"}],
                },
                "semantic_review": {
                    "status": "unavailable",
                    "accepted": False,
                    "findings": [],
                },
            },
            {
                "round_index": 1,
                "decision": "promoted",
                "candidate_key": [-87.0],
                "incumbent_key_after": [-87.0],
                "worker_status": "applied",
                "worker_changed_files": ["examples/solver.py"],
                "context_packet_path": "round_001/context_packet.json",
                "cycle_dir": "round_001",
                "patch_path": "round_001/worker_changes.patch",
                "delta_path": "round_001/worker_worktree_delta.json",
                "mechanism_activation": {
                    "status": "failed",
                    "passed": False,
                    "declared_check_count": 1,
                    "required_check_count": 1,
                    "required_failure_count": 1,
                    "checks": [{"id": "missing_counter", "path": "stats.counter", "required": True, "passed": False}],
                },
                "round_reflection": {
                    "hypothesis_outcome": "inconclusive_not_exercised",
                    "summary": "The mechanism was not actually exercised.",
                    "candidate_findings": [],
                    "next_action": {"action": "probe", "rationale": "Instrument the missing path first."},
                },
                "proposal_diagnostics": {
                    "summary": "Probe activation failure handling.",
                    "rule_operator_hypotheses": [{"name": "inactive_probe", "type": "local_search_operator"}],
                },
                "semantic_review": {
                    "status": "warning",
                    "accepted": True,
                    "findings": [],
                },
            },
        ]
        unavailable_with_blocker = json.loads(json.dumps(rounds[0]))
        unavailable_with_blocker["round_index"] = 2
        unavailable_with_blocker["proposal_diagnostics"]["rule_operator_hypotheses"][0]["name"] = (
            "unavailable_but_blocked"
        )
        unavailable_with_blocker["semantic_review"] = {
            "status": "unavailable",
            "accepted": False,
            "findings": [{"blocking": True, "category": "verified_contract_violation"}],
        }
        rounds.append(unavailable_with_blocker)

        memory = build_experience_memory(rounds, problem_family="FJSP")
        validated = memory["memory_tiers"]["validated_lessons"]

        self.assertEqual(1, len(validated))
        self.assertEqual("critical_block_probe", validated[0]["strategy"])
        self.assertEqual("inconclusive", validated[0]["hypothesis_outcome"])
        self.assertEqual("core_and_activation_validated", validated[0]["confidence"])


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


if __name__ == "__main__":
    unittest.main()
