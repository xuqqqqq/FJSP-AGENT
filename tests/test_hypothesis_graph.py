from __future__ import annotations

import unittest

from harness_agent.hypothesis import (
    HypothesisRecord,
    render_hypothesis_graph_markdown,
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
