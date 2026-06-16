from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.evaluator import EvaluationResult
from harness_agent.ledger import ExperimentRecord
from harness_agent.models import ObjectiveSpec
from harness_agent.runner import pareto_frontier, validation_summary


class EvaluatorRunnerTests(unittest.TestCase):
    def test_required_objective_metric_must_be_numeric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = Path(tmp) / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "error_count": 0,
                        "errors": [],
                        "metrics": {"primary_score": "not-a-number"},
                    }
                ),
                encoding="utf-8",
            )
            result = EvaluationResult.from_metrics_file(
                metrics_path,
                [ObjectiveSpec(name="primary_score", direction="maximize")],
            )

            self.assertFalse(result.valid)
            self.assertEqual("failed_validation", result.status)
            self.assertIn("non-numeric objective metric: primary_score", result.errors)

    def test_pareto_frontier_filters_dominated_candidates(self) -> None:
        candidates = [
            {"candidate_id": "high_score", "complete": True, "objective_key": (10.0, -5.0), "metrics": {}},
            {"candidate_id": "fast", "complete": True, "objective_key": (9.0, -2.0), "metrics": {}},
            {"candidate_id": "dominated", "complete": True, "objective_key": (8.0, -6.0), "metrics": {}},
            {"candidate_id": "incomplete", "complete": False, "objective_key": (100.0, 100.0), "metrics": {}},
        ]

        frontier_ids = {item["candidate_id"] for item in pareto_frontier(candidates)}

        self.assertEqual({"high_score", "fast"}, frontier_ids)

    def test_validation_summary_counts_status_and_errors(self) -> None:
        records = [
            _record("ok", "success", True, None),
            _record("bad_a", "failed_validation", False, "missing required metric: score"),
            _record("bad_b", "failed_validation", False, "missing required metric: score"),
        ]

        summary = validation_summary(records)

        self.assertEqual({"failed_validation": 2, "success": 1}, summary["status_counts"])
        self.assertEqual(
            [{"error": "missing required metric: score", "count": 2}],
            summary["top_errors"],
        )


def _record(experiment_id: str, status: str, valid: bool, error: str | None) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        task_id="test",
        round_index=0,
        instance_id="instance",
        seed=0,
        status=status,
        valid=valid,
        objective_key=(1.0,) if valid else (float("-inf"),),
        metrics={"score": 1.0} if valid else {},
        paths={},
        error=error,
    )


if __name__ == "__main__":
    unittest.main()
