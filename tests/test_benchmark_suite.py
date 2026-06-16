from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.benchmark_suite import BenchmarkSuiteRequest, run_benchmark_suite


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkSuiteTests(unittest.TestCase):
    def test_configured_standard_suite_writes_aggregate_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "suite"

            manifest = run_benchmark_suite(
                BenchmarkSuiteRequest(
                    config_path=ROOT / "configs" / "standard_fjsp_suite.example.json",
                    output_dir=output_dir,
                    project_root=ROOT,
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual(1, manifest["suite_count"])
            self.assertEqual({"ok": 1}, manifest["aggregate"]["suite_status_counts"])
            self.assertEqual(1, manifest["aggregate"]["valid_experiments"])
            self.assertEqual(1, manifest["aggregate"]["gap_suite_count"])
            self.assertAlmostEqual(16.666666666666664, manifest["aggregate"]["avg_reported_gap_pct"])
            self.assertTrue((output_dir / "suite_manifest.json").exists())
            self.assertTrue((output_dir / "suite_report.md").exists())
            self.assertIn("tiny-standard-fjsp", (output_dir / "suite_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
