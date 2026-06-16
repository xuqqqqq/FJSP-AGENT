from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from harness_agent.demo import StandardDemoRequest, run_standard_demo, summarize_benchmark_result


ROOT = Path(__file__).resolve().parents[1]


class StandardDemoTests(unittest.TestCase):
    def test_standard_demo_runs_document_to_evaluator_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "demo"

            manifest = run_standard_demo(
                StandardDemoRequest(
                    docs=[ROOT / "README.md"],
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    output_dir=output_dir,
                    project_root=ROOT,
                    max_instances=1,
                    max_rounds=1,
                    seeds=[0],
                    timeout_seconds=30,
                    max_workers=1,
                    solver="portfolio",
                    portfolio_size=4,
                    strategy_candidates=1,
                    profile_mode="template",
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual([], manifest["artifact_checks"]["missing"])
            self.assertGreaterEqual(manifest["artifact_checks"]["contract_count"], 1)
            self.assertGreaterEqual(manifest["artifact_checks"]["harness_report_count"], 1)
            self.assertEqual(1, manifest["agent_result"]["last_summary"]["valid"])
            self.assertTrue((output_dir / "demo_manifest.json").exists())
            self.assertTrue((output_dir / "demo_report.md").exists())
            self.assertTrue((output_dir / "standard_agent" / "hypothesis_graph.md").exists())

    def test_standard_demo_reports_best_known_gap_when_csv_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            best_csv = tmp_path / "best.csv"
            best_csv.write_text("instance,best\nstandard_fjsp_tiny,6\n", encoding="utf-8")
            output_dir = tmp_path / "demo_gap"

            manifest = run_standard_demo(
                StandardDemoRequest(
                    docs=[ROOT / "README.md"],
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    best_known_csv=best_csv,
                    output_dir=output_dir,
                    project_root=ROOT,
                    max_instances=1,
                    max_rounds=1,
                    seeds=[0],
                    timeout_seconds=30,
                    max_workers=1,
                    solver="portfolio",
                    portfolio_size=4,
                    strategy_candidates=1,
                    profile_mode="template",
                )
            )

            benchmark = manifest["benchmark_summary"]
            self.assertTrue(benchmark["has_best_known_gap"])
            self.assertIn("avg_gap_pct", benchmark["gap_metrics"])
            self.assertIn("avg_best_known_makespan", benchmark["best_known_metrics"])

            persisted = json.loads((output_dir / "demo_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(persisted["benchmark_summary"]["has_best_known_gap"])
            self.assertIn("Gap metrics", (output_dir / "demo_report.md").read_text(encoding="utf-8"))

    def test_benchmark_summary_handles_missing_gap_metrics(self) -> None:
        summary = summarize_benchmark_result(
            {
                "last_summary": {
                    "total": 1,
                    "valid": 1,
                    "failed": 0,
                    "best_metrics": {"makespan": 7},
                    "best_candidate_metrics": {"avg_makespan": 7, "valid_instances": 1},
                    "candidate_summaries": [{"candidate_id": "c0"}],
                    "pareto_frontier": [{"candidate_id": "c0"}],
                }
            }
        )

        self.assertFalse(summary["has_best_known_gap"])
        self.assertEqual({}, summary["gap_metrics"])
        self.assertEqual(1, summary["candidate_count"])


if __name__ == "__main__":
    unittest.main()
