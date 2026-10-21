from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.agents.semantic import OpenCodeAlgorithmSemanticReviewer
from harness_agent.core.cancellation import CancellationToken
from harness_agent.core.runner import RunSummary
from harness_agent.orchestration.loop import (
    ControllerTimeBudget,
    run_algorithm_semantic_review,
    semantic_review_blocks_promotion,
)


class SemanticControllerBudgetTests(unittest.TestCase):
    def review(self, root, reviewer, budget, parent=None):
        return run_algorithm_semantic_review(
            reviewer=reviewer,
            cycle=SimpleNamespace(
                summary=RunSummary(1, 1, 0, "test", {"makespan": 1}),
                worktree_path=root,
                worker_result=SimpleNamespace(changed_files=["solver.py"]),
            ),
            context_packet_path=root / "context.json",
            direction_plan={}, round_index=0, attempt_index=0,
            output_dir=root / "review", incumbent_key=(-2,), candidate_key=(-1,),
            controller_budget=budget, cancellation=parent,
        )

    def test_expired_budget_skips_model_and_blocks_unreviewed_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewer = SimpleNamespace(review=lambda _: self.fail("Model must not run"))
            budget = ControllerTimeBudget(1, root, started_at_epoch=0)
            result = self.review(root, reviewer, budget)
            self.assertEqual("controller_budget_exhausted", result["reason"])
            self.assertTrue(semantic_review_blocks_promotion(result))
            self.assertEqual(result, json.loads((root / "review/algorithm_semantic_review.json").read_text()))

    def test_budget_interrupts_review_without_cancelling_parent_or_shared_reviewer(self):
        class WaitingReviewer:
            timeout_seconds = 300
            cancellation = None

            def review(self, request):
                terminated = threading.Event()
                registration = self.cancellation.register_terminator(terminated.set)
                try:
                    if not terminated.wait(2):
                        raise AssertionError("Review did not receive budget cancellation")
                    self.cancellation.raise_if_cancelled()
                finally:
                    self.cancellation.unregister_terminator(registration)

        with tempfile.TemporaryDirectory() as tmp:
            root, parent, reviewer = Path(tmp), CancellationToken(), WaitingReviewer()
            result = self.review(root, reviewer, ControllerTimeBudget(0.05, root), parent)
            self.assertFalse(result["accepted"])
            self.assertFalse(parent.cancelled)
            self.assertIsNone(reviewer.cancellation)
            self.assertEqual(300, reviewer.timeout_seconds)

    def test_no_reviewer_stays_disabled_after_budget_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.review(root, None, ControllerTimeBudget(1, root, started_at_epoch=0))
            self.assertEqual("none", result["reviewer"])
            self.assertTrue(result["accepted"])

    def test_opencode_reviewer_passes_cancellation_to_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, token = Path(tmp), CancellationToken()
            reviewer = OpenCodeAlgorithmSemanticReviewer(model="test/model", project_root=root, cancellation=token)
            event = json.dumps({"type": "text", "part": {"type": "text", "text": '{"summary":"ok","findings":[]}'}})
            with patch("harness_agent.agents.semantic.load_context_dict", return_value={}), patch(
                "harness_agent.agents.semantic.load_review_sources", return_value={"solver.py": "pass"}
            ), patch("harness_agent.agents.semantic.load_review_knowledge", return_value={"contract.md": "contract"}), patch(
                "harness_agent.agents.opencode_main.OpenCodeMainAgent"
            ) as runtime:
                runtime.return_value._run_once.return_value = {"stdout": event}
                self.review(root, reviewer, None)
                self.assertIs(token, runtime.call_args.kwargs["cancellation"])


if __name__ == "__main__":
    unittest.main()
