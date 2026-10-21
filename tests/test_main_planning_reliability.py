from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_agent.agents.main import DirectionPlanRequest, MainPlanningError, generic_ablation_history_fallback_plan
from harness_agent.agents.opencode_main import (
    OpenCodeMainAgent,
    normalize_generic_method_variants,
    planning_response_failure,
    resolve_main_planning_timeout,
)


def method(name: str) -> dict:
    return {"method_name": name, "hypothesis": "Improve measured quality.",
            "worker_objective": "Implement and evaluate with incumbent fallback.", "strategy_type": "search"}


class MainPlanningReliabilityTests(unittest.TestCase):
    def test_main_timeout_defaults_and_overrides(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(600, resolve_main_planning_timeout("qiming/dsv41-1017"))
            self.assertEqual(120, resolve_main_planning_timeout("deepseek/deepseek-v4-pro"))
            self.assertEqual(23, resolve_main_planning_timeout("qiming/dsv41-1017", 23))
            self.assertIsNone(resolve_main_planning_timeout("qiming/dsv41-1017", 0))
        with patch.dict(os.environ, {"OPENCODE_MAIN_TIMEOUT_SECONDS": "700"}):
            self.assertEqual(700, resolve_main_planning_timeout("other/model"))
            self.assertEqual(31, resolve_main_planning_timeout("other/model", 31))
        with self.assertRaises(ValueError):
            resolve_main_planning_timeout("other/model", -1)

    def test_unicode_methods_are_retained_and_width_variants_deduplicated(self) -> None:
        names = ["禁忌搜索", "遗传算法", "模拟退火", "禁忌 搜索", "ＧＡ", "ga"]
        variants = normalize_generic_method_variants([method(name) for name in names], expected=4)
        self.assertEqual(["禁忌搜索", "遗传算法", "模拟退火", "ＧＡ"], [v["method_name"] for v in variants])
        self.assertEqual(4, len({v["candidate_id"] for v in variants}))

    def test_malformed_variant_container_is_a_schema_failure(self) -> None:
        for value in (None, 3, "methods", {"method_name": "search"}):
            with self.subTest(value=value):
                self.assertEqual([], normalize_generic_method_variants(value, expected=3))

    def test_chinese_history_is_available_without_inventing_new_methods(self) -> None:
        methods = [method(name) for name in ("禁忌搜索", "遗传算法", "模拟退火")]
        with tempfile.TemporaryDirectory() as tmp:
            request = DirectionPlanRequest(0, Path(tmp) / "unused.json", {
                "competition": {"max_competing_workers": 3},
                "previous_rounds": [{"direction_plan": {"candidate_variants": methods}}],
            }, Path(tmp))
            plan = generic_ablation_history_fallback_plan(request=request, context={})
        self.assertEqual([v["method_name"] for v in methods], [v["method_name"] for v in plan["candidate_variants"]])

    def test_generic_chinese_plan_succeeds_once_with_configured_timeout(self) -> None:
        methods = [method(name) for name in ("禁忌搜索", "遗传算法", "模拟退火")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "context.json"
            context.write_text(json.dumps({"guidance_ablation": {"mode": "none"}}), encoding="utf-8")
            request = DirectionPlanRequest(0, context, {"competition": {"max_competing_workers": 3}}, root)
            agent = OpenCodeMainAgent(model="qiming/dsv41-1017", timeout_seconds=600)
            run = {"stdout": json.dumps({"type": "text", "part": {"text": json.dumps({
                "direction_plan": {"candidate_variants": methods}}, ensure_ascii=False)}}), "returncode": 0}
            with patch.object(agent, "_run_once", return_value=run) as invoke:
                plan = agent._plan_direction_none(request)
            self.assertEqual(3, len(plan["candidate_variants"]))
            invoke.assert_called_once()
            self.assertEqual(600, invoke.call_args.kwargs["timeout_seconds"])

    def test_timeout_does_not_start_another_full_planning_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "context.json"
            context.write_text("{}", encoding="utf-8")
            request = DirectionPlanRequest(0, context, {}, root)
            agent = OpenCodeMainAgent(timeout_seconds=20)
            with patch.object(agent, "_run_once", return_value={"stdout": "", "timed_out": True}) as invoke:
                with self.assertRaises(MainPlanningError) as caught:
                    agent._plan_direction_none(request)
            self.assertEqual("timeout", caught.exception.reason)
            invoke.assert_called_once()
            self.assertEqual("timeout", json.loads((root / "main_planning_failure.json").read_text())["attempts"][0]["reason"])

    def test_generation_failure_categories(self) -> None:
        for run, expected in [
            ({"timed_out": True}, "timeout"),
            ({"stalled": True}, "stalled"),
            ({"stdout": ""}, "zero_event_stream"),
            ({"stdout": json.dumps({"type": "error"})}, "provider_error"),
            ({"stdout": json.dumps({"type": "step_finish", "part": {"reason": "length"}})}, "context_limit"),
            ({"stdout": json.dumps({"type": "reasoning", "part": {"text": "thinking"}})}, "no_final_output"),
            ({"stdout": json.dumps({"type": "text", "part": {"text": "bad json"}})}, "invalid_plan_schema"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(expected, planning_response_failure(run)["reason"])


if __name__ == "__main__":
    unittest.main()
