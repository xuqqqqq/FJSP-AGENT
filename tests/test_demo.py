from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.demo import StandardDemoRequest, run_standard_demo


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


if __name__ == "__main__":
    unittest.main()
