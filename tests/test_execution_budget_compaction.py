from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context.loader import load_context_packet
from harness_agent.context.packet import _fit_refreshed_packet, write_refreshed_context_packet


class ExecutionBudgetCompactionTests(unittest.TestCase):
    budget = {"max_agent_steps": 96, "max_solver_smokes": 4, "max_solver_smoke_seconds": 30}

    def test_late_budget_survives_key_count_compaction(self):
        payload = {f"padding_{i}": "x" * 10000 for i in range(40)}
        payload["worker_execution_budget"] = dict(self.budget)
        payload["loop_feedback"] = {"current_round_repair": {"attempt_index": 2}}
        fitted, meta = _fit_refreshed_packet(payload, max_chars=16000)
        self.assertTrue(meta["compacted"])
        self.assertEqual(self.budget, fitted["worker_execution_budget"])
        self.assertEqual(payload["loop_feedback"], fitted["loop_feedback"])
        self.assertLessEqual(len(json.dumps(fitted, ensure_ascii=False, indent=2)), 16000)

    def test_refresh_and_reload_preserve_trusted_budget_over_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.json"
            payload = {
                "task": {"problem_family": "FJSP"},
                "guidance_ablation": {"mode": "none"},
                **{f"padding_{i}": "x" * 10000 for i in range(100)},
                "worker_execution_budget": dict(self.budget),
            }
            base.write_text(json.dumps(payload), encoding="utf-8")
            output = write_refreshed_context_packet(
                base_context_packet_path=base, output_path=root / "refreshed.json",
                loop_feedback={
                    "round_index": 0,
                    "worker_execution_budget": {"max_agent_steps": 1},
                    "current_direction_plan": {"worker_execution_budget": {"max_solver_smokes": 1}},
                },
            )
            loaded = load_context_packet(output)
            self.assertEqual(self.budget, loaded.effective_context["worker_execution_budget"])
            self.assertEqual(self.budget, loaded.stable_context["worker_execution_budget"])
            self.assertNotEqual("mismatch", loaded.integrity["status"])
            self.assertNotIn("worker_execution_budget", loaded.dynamic_context["loop_feedback"])
            second = write_refreshed_context_packet(
                base_context_packet_path=output, output_path=root / "second.json",
                loop_feedback={"round_index": 1},
            )
            self.assertEqual(self.budget, load_context_packet(second).effective_context["worker_execution_budget"])

    def test_default_packets_do_not_gain_a_budget(self):
        for payload in ({"task": {}}, {f"padding_{i}": "x" * 10000 for i in range(40)}):
            fitted, _ = _fit_refreshed_packet(payload, max_chars=16000)
            self.assertNotIn("worker_execution_budget", fitted)

    def test_refresh_budget_validation_is_not_bypassed_by_small_packet(self):
        for budget in ([], {"max_agent_steps": True}, {"max_solver_smokes": 11},
                       {"max_solver_smoke_seconds": "30"}, {"unexpected": 1}):
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, "worker_execution_budget"):
                _fit_refreshed_packet({"worker_execution_budget": budget}, max_chars=16000)


if __name__ == "__main__":
    unittest.main()
