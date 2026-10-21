from __future__ import annotations

import copy
import json
import unittest

from harness_agent.context.generic_planning import (
    GENERIC_PLANNING_PACKET_MAX_CHARS,
    build_generic_planning_packet,
)
from harness_agent.context.planning_packet import build_planning_packet


class GenericPlanningPacketTests(unittest.TestCase):
    def context(self) -> dict:
        return {
            "task": {
                "task_id": "generic-test",
                "problem_family": "custom-scheduling",
                "description": "Follow all supplied constraints.",
                "objectives": [
                    {"name": "tardiness", "direction": "minimize", "priority": 1},
                    {"name": "energy", "direction": "minimize", "priority": 2},
                ],
                "budget": {"timeout_seconds": 30},
            },
            "evaluator_protocol": {
                "solver_command_template": "python solver.py --input {instance}",
                "solution_contract": {"format": "json", "required": ["schedule", "energy"]},
            },
            "io_contract": {"input": {"machines": "array"}, "output": {"schedule": "array"}},
            "edit_policy": {"allowed_paths": ["solver.py"], "forbidden_paths": ["evaluator.py"]},
            "documents": [
                {"path": "requirements.md", "snippet": "hard deadline\n" * 500,
                 "chars": 20_000, "truncated": True, "sha256": "req-hash"},
                {"path": "io.md", "snippet": "output must contain schedule and energy\n" * 100,
                 "chars": 3_900, "truncated": False, "sha256": "io-hash"},
            ],
            "guidance_ablation": {"mode": "none"},
        }

    def test_fresh_baseline_has_no_incumbent_and_keeps_source_contracts(self) -> None:
        context = self.context()
        planning = build_planning_packet(context=context, loop_feedback={}, round_index=-1)
        packet = build_generic_planning_packet(context, planning, 1, -1)
        self.assertEqual("baseline", packet["phase"])
        self.assertFalse(packet["incumbent_available"])
        self.assertFalse(packet["requires_incumbent_preservation"])
        self.assertEqual(context["task"], packet["task"])
        self.assertEqual(context["task"]["objectives"], packet["task_digest"]["objectives"])
        self.assertEqual(context["documents"], packet["documents"])
        for key in ("evaluator_protocol", "io_contract", "edit_policy"):
            self.assertEqual(context[key], packet["io_digest"][key])
        self.assertEqual(1, packet["output_contract"]["candidate_method_count"])

    def test_objective_override_and_empty_override_fallback(self) -> None:
        context = self.context()
        override = [{"name": "weighted_cost", "direction": "minimize", "priority": 1}]
        context["objectives"] = override
        packet = build_generic_planning_packet(context, {}, 2, -1)
        self.assertEqual(override, packet["task_digest"]["objectives"])
        self.assertEqual(context["task"], packet["task"])
        context["objectives"] = []
        packet = build_generic_planning_packet(context, {}, 2, -1)
        self.assertEqual(context["task"]["objectives"], packet["task_digest"]["objectives"])

    def test_improvement_availability_uses_real_evidence(self) -> None:
        context = self.context()
        planning = build_planning_packet(context=context, loop_feedback={}, round_index=0)
        self.assertFalse(build_generic_planning_packet(context, planning, 2, 0)["incumbent_available"])
        planning["incumbent_evidence"]["objective_key"] = [0.0]
        packet = build_generic_planning_packet(context, planning, 2, 0)
        self.assertEqual("improvement", packet["phase"])
        self.assertTrue(packet["incumbent_available"])
        self.assertTrue(packet["requires_incumbent_preservation"])
        self.assertEqual([0.0], packet["incumbent_evidence"]["objective_key"])
        self.assertFalse(build_generic_planning_packet(context, planning, 1, -1)["incumbent_available"])

    def test_none_excludes_domain_advice_in_optional_facts(self) -> None:
        context = self.context()
        advice = "DOMAIN_ADVICE_SENTINEL"
        context.update({
            "knowledge_cards": [advice], "skills": [advice],
            "active_method_package": {"recommended": advice},
            "instance_diagnostics": {
                "summary": {"operation_count": 100, "recommended_methods": [advice]},
                "direction_hints": [advice],
                "instances": [{"job_count": 20, "skill": advice, "knowledge_query": advice}],
            },
        })
        planning = {
            "method_family_catalog": {"families": [advice]},
            "strategy_selection_cards": [advice],
            "io_digest": {"quality_contract": {"method_packages": [advice]}},
            "runtime_limits": {"max_competing_workers": 3, "skill": advice},
            "recent_round_evidence": [{
                "round_index": 0, "direction_plan": advice,
                "competition_result": {"candidate_key": [10], "method_package": advice},
            }],
        }
        packet = build_generic_planning_packet(context, planning, 3, 1)
        self.assertNotIn(advice, json.dumps(packet))
        self.assertEqual(100, packet["instance_diagnostics"]["summary"]["operation_count"])
        self.assertEqual(20, packet["instance_diagnostics"]["instances"][0]["job_count"])
        self.assertEqual(3, packet["runtime_limits"]["max_competing_workers"])

    def test_optional_facts_cannot_displace_documents_or_contracts(self) -> None:
        context = self.context()
        context["instance_diagnostics"] = {
            "instances": [{"id": str(i), "observations": "f" * 20_000} for i in range(30)]
        }
        packet = build_generic_planning_packet(context, {}, 4, -1)
        size = len(json.dumps(packet, ensure_ascii=False, indent=2))
        self.assertLessEqual(size, GENERIC_PLANNING_PACKET_MAX_CHARS)
        self.assertEqual(size, packet["packet_budget"]["serialized_chars"])
        self.assertTrue(packet["packet_budget"]["facts_compacted"])
        self.assertFalse(packet["packet_budget"]["source_contracts_truncated"])
        self.assertEqual(context["documents"], packet["documents"])
        self.assertEqual(context["evaluator_protocol"], packet["io_digest"]["evaluator_protocol"])

    def test_oversized_source_contract_is_rejected_without_mutation(self) -> None:
        context = self.context()
        context["evaluator_protocol"]["schema"] = "x" * GENERIC_PLANNING_PACKET_MAX_CHARS
        before = copy.deepcopy(context)
        with self.assertRaisesRegex(ValueError, "source contracts were not truncated"):
            build_generic_planning_packet(context, {}, 1, -1)
        self.assertEqual(before, context)

    def test_result_does_not_alias_or_mutate_source_contracts(self) -> None:
        context = self.context()
        planning = {"incumbent_evidence": {"objective_key": [100.0]}}
        before_context = copy.deepcopy(context)
        before_planning = copy.deepcopy(planning)
        packet = build_generic_planning_packet(context, planning, 2, 0)
        packet["task"]["objectives"][0]["name"] = "changed"
        packet["documents"][0]["snippet"] = "changed"
        packet["io_digest"]["edit_policy"]["allowed_paths"].append("other.py")
        packet["incumbent_evidence"]["objective_key"].append(7)
        self.assertEqual(before_context, context)
        self.assertEqual(before_planning, planning)


if __name__ == "__main__":
    unittest.main()
