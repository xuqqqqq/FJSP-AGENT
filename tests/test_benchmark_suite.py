from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.awls_benchmark import AwlsBenchmarkRequest, effective_time_limit_sec, run_awls_benchmark
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
            self.assertAlmostEqual(16.666666666666664, manifest["aggregate"]["max_reported_gap_pct"])
            self.assertTrue((output_dir / "suite_manifest.json").exists())
            self.assertTrue((output_dir / "suite_report.md").exists())
            self.assertIn("tiny-standard-fjsp", (output_dir / "suite_report.md").read_text(encoding="utf-8"))

    def test_awls_benchmark_writes_valid_tiny_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "awls"

            manifest = run_awls_benchmark(
                AwlsBenchmarkRequest(
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    output_dir=output_dir,
                    best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    seeds=[0],
                    restarts=1,
                    cycles_per_restart=1,
                    iterations=5,
                    time_limit_sec=1.0,
                    init_mode="mixed",
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual(1, manifest["aggregate"]["instance_count"])
            self.assertEqual(1, manifest["aggregate"]["valid_instance_count"])
            self.assertEqual(0, manifest["aggregate"]["invalid_run_count"])
            self.assertIsNotNone(manifest["aggregate"]["avg_gap_pct"])
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "report.md").exists())

            resumed = run_awls_benchmark(
                AwlsBenchmarkRequest(
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    output_dir=output_dir,
                    best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    seeds=[0],
                    restarts=1,
                    cycles_per_restart=1,
                    iterations=5,
                    time_limit_sec=1.0,
                    init_mode="mixed",
                    resume=True,
                )
            )
            self.assertTrue(resumed["runs"][0]["resumed"])

    def test_mae2019_time_policy_matches_paper_families(self) -> None:
        request = AwlsBenchmarkRequest(
            instance_dir=ROOT / "examples",
            pattern="*.fjs",
            output_dir=ROOT / "outputs" / "_unused",
            time_limit_sec=12.0,
            time_policy="mae2019",
        )

        self.assertEqual(90.0, effective_time_limit_sec(request, Path("fjsp.barnes.mt10x.m11j10c2.txt")))
        self.assertEqual(90.0, effective_time_limit_sec(request, Path("fjsp.brandimarte.Mk01.m6j10c3.txt")))
        self.assertEqual(300.0, effective_time_limit_sec(request, Path("fjsp.dauzere.01a.m5j10c3.txt")))
        self.assertEqual(300.0, effective_time_limit_sec(request, Path("fjsp.hurink.edata-la01.m5j10c2.txt")))
        self.assertEqual(12.0, effective_time_limit_sec(request, Path("other_family_case.txt")))


if __name__ == "__main__":
    unittest.main()
