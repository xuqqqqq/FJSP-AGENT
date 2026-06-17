from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.awls_compare import AwlsCompareRequest, compare_awls_benchmarks


class AwlsCompareTests(unittest.TestCase):
    def test_compare_awls_benchmarks_reports_instance_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_path = root / "baseline.json"
            candidate_path = root / "candidate.json"
            output_dir = root / "compare"
            baseline_path.write_text(
                json.dumps(
                    {
                        "instances": [
                            {"instance": "improved.txt", "valid": True, "makespan": 105, "gap_pct": 5.0},
                            {"instance": "tied.txt", "valid": True, "makespan": 100, "gap_pct": 0.0},
                            {"instance": "worsened.txt", "valid": True, "makespan": 90, "gap_pct": 0.0},
                            {"instance": "baseline_only.txt", "valid": True, "makespan": 50, "gap_pct": 0.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            candidate_path.write_text(
                json.dumps(
                    {
                        "instances": [
                            {"instance": "improved.txt", "valid": True, "makespan": 100, "gap_pct": 0.0},
                            {"instance": "tied.txt", "valid": True, "makespan": 100, "gap_pct": 0.0},
                            {"instance": "worsened.txt", "valid": True, "makespan": 91, "gap_pct": 1.1},
                            {"instance": "candidate_only.txt", "valid": True, "makespan": 40, "gap_pct": 0.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manifest = compare_awls_benchmarks(
                AwlsCompareRequest(
                    baseline_summary=baseline_path,
                    candidate_summary=candidate_path,
                    output_dir=output_dir,
                )
            )

            aggregate = manifest["aggregate"]
            self.assertEqual("ok", manifest["status"])
            self.assertEqual(5, aggregate["instance_count"])
            self.assertEqual(3, aggregate["common_count"])
            self.assertEqual(1, aggregate["improved_count"])
            self.assertEqual(1, aggregate["tied_count"])
            self.assertEqual(1, aggregate["worsened_count"])
            self.assertEqual(1, aggregate["baseline_only_count"])
            self.assertEqual(1, aggregate["candidate_only_count"])
            self.assertAlmostEqual(-1.3, aggregate["delta_avg_gap_pct"])
            self.assertTrue((output_dir / "awls_compare_summary.json").exists())
            report = (output_dir / "awls_compare_report.md").read_text(encoding="utf-8")
            self.assertIn("AWLS Benchmark Comparison Report", report)
            self.assertIn("improved.txt", report)


if __name__ == "__main__":
    unittest.main()
