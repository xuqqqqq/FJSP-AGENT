from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.awls_benchmark import (
    AwlsBenchmarkRequest,
    effective_time_limit_sec,
    instance_family,
    load_resumed_result,
    run_awls_benchmark,
    scaled_time_limit_sec,
    selected_instances,
)
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
                    critical_block_exhaustive_pct=5,
                    zi_policy="sqrt",
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual(5, manifest["request"]["critical_block_exhaustive_pct"])
            self.assertEqual("sqrt", manifest["request"]["zi_policy"])
            self.assertEqual(1, manifest["aggregate"]["instance_count"])
            self.assertEqual(["standard_fjsp_tiny.fjs"], manifest["selected_instance_names"])
            self.assertEqual(1, manifest["aggregate"]["valid_instance_count"])
            self.assertEqual(0, manifest["aggregate"]["invalid_run_count"])
            self.assertIsNotNone(manifest["aggregate"]["avg_gap_pct"])
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "report.md").exists())
            first_runtime = manifest["runs"][0]["runtime_sec"]
            metrics_payload = next((output_dir / "runs").glob("*_metrics.json")).read_text(encoding="utf-8")
            self.assertIn('"runtime_sec"', metrics_payload)

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
            self.assertEqual(first_runtime, resumed["runs"][0]["runtime_sec"])

    def test_awls_portfolio_runs_each_outer_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "awls_portfolio"

            manifest = run_awls_benchmark(
                AwlsBenchmarkRequest(
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    output_dir=output_dir,
                    best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    seeds=[0, 1],
                    restarts=1,
                    cycles_per_restart=1,
                    iterations=3,
                    time_limit_sec=0.2,
                    init_mode="random",
                    portfolio_lanes="0:random:1,1:mixed:1",
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual(2, manifest["aggregate"]["seed_count"])
            self.assertEqual(2, manifest["aggregate"]["run_count"])
            strategies = [item["strategy"] for item in manifest["runs"]]
            self.assertTrue(any("outer_seed=0" in strategy for strategy in strategies))
            self.assertTrue(any("outer_seed=1" in strategy for strategy in strategies))

    def test_awls_benchmark_passes_alignment_profile_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "awls_alignment_profile"

            manifest = run_awls_benchmark(
                AwlsBenchmarkRequest(
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    output_dir=output_dir,
                    seeds=[0],
                    restarts=1,
                    cycles_per_restart=1,
                    iterations=3,
                    time_limit_sec=0.2,
                    init_mode="greedy",
                    zi_policy="cpp-exact",
                    initial_state="cpp",
                    time_check_interval=7,
                    same_machine_eval="cpp-fast",
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual("cpp", manifest["request"]["initial_state"])
            self.assertEqual(7, manifest["request"]["time_check_interval"])
            strategy = manifest["runs"][0]["strategy"]
            self.assertIn("initial=cpp", strategy)
            self.assertIn("time_check=7", strategy)
            self.assertIn("zi=cpp-exact", strategy)

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

    def test_scaled_time_policy_uses_instance_size_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "small.fjs"
            medium = root / "medium.fjs"
            large = root / "large.fjs"
            small.write_text("2 2 1\n1 1 1 3\n1 1 2 4\n", encoding="utf-8")
            medium.write_text(
                "5 5 1\n" + "\n".join("10 " + " ".join(["1 1 3"] * 10) for _ in range(5)) + "\n",
                encoding="utf-8",
            )
            large.write_text(
                "10 10 1\n" + "\n".join("10 " + " ".join(["1 1 3"] * 10) for _ in range(10)) + "\n",
                encoding="utf-8",
            )
            request = AwlsBenchmarkRequest(
                instance_dir=root,
                pattern="*.fjs",
                output_dir=root / "unused",
                time_limit_sec=45.0,
                time_policy="scaled",
            )

            self.assertEqual(30.0, scaled_time_limit_sec(small))
            self.assertEqual(90.0, scaled_time_limit_sec(medium))
            self.assertEqual(300.0, scaled_time_limit_sec(large))
            self.assertEqual(45.0, effective_time_limit_sec(request, small))
            self.assertEqual(90.0, effective_time_limit_sec(request, medium))

    def test_mae2019_time_policy_uses_content_when_filename_shape_is_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            misnamed = root / "fjsp.brandimarte.Mk01.m6j10c3.txt"
            misnamed.write_text(
                "20 10 10\n" + "\n".join("15 " + " ".join(["1 1 3"] * 15) for _ in range(20)) + "\n",
                encoding="utf-8",
            )
            request = AwlsBenchmarkRequest(
                instance_dir=root,
                pattern="*.txt",
                output_dir=root / "unused",
                time_limit_sec=12.0,
                time_policy="mae2019",
            )

            self.assertEqual(300.0, effective_time_limit_sec(request, misnamed))

    def test_awls_resume_legacy_metrics_do_not_fake_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metrics_path = root / "legacy_metrics.json"
            solution_path = root / "legacy_solution.json"
            metrics_path.write_text(
                """{
  "valid": true,
  "error_count": 0,
  "errors": [],
  "metrics": {
    "makespan": 10,
    "gap_pct": 0
  }
}
""",
                encoding="utf-8",
            )
            solution_path.write_text('{"strategy": "legacy"}\n', encoding="utf-8")

            result = load_resumed_result(Path("legacy.fjs"), 0, metrics_path, solution_path)

            self.assertIsNotNone(result)
            self.assertTrue(result["resumed"])
            self.assertIsNone(result["runtime_sec"])
            self.assertIsNone(result["time_limit_sec"])
            self.assertEqual("legacy", result["strategy"])

    def test_awls_benchmark_family_filter_selects_only_named_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp)
            for name in (
                "fjsp.barnes.mt10x.m11j10c2.txt",
                "fjsp.brandimarte.Mk01.m6j10c3.txt",
                "fjsp.dauzere.01a.m5j10c3.txt",
                "fjsp.hurink.edata-la01.m5j10c2.txt",
                "fjsp.LA01.m5j10c2.txt",
            ):
                (instance_dir / name).write_text("placeholder", encoding="utf-8")

            request = AwlsBenchmarkRequest(
                instance_dir=instance_dir,
                pattern="*.txt",
                output_dir=Path(tmp) / "out",
                include_families=["barnes", "brandimarte", "dauzere", "hurink"],
            )

            names = [path.name for path in selected_instances(request)]
            self.assertEqual(
                [
                    "fjsp.barnes.mt10x.m11j10c2.txt",
                    "fjsp.brandimarte.Mk01.m6j10c3.txt",
                    "fjsp.dauzere.01a.m5j10c3.txt",
                    "fjsp.hurink.edata-la01.m5j10c2.txt",
                ],
                names,
            )
            self.assertEqual("barnes", instance_family(Path("fjsp.barnes.mt10x.m11j10c2.txt")))

    def test_awls_benchmark_sample_count_is_family_balanced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp)
            for family in ("barnes", "brandimarte", "dauzere", "hurink"):
                for index in range(10):
                    (instance_dir / f"fjsp.{family}.case{index:02d}.m1j1c1.txt").write_text(
                        "placeholder",
                        encoding="utf-8",
                    )

            request = AwlsBenchmarkRequest(
                instance_dir=instance_dir,
                pattern="*.txt",
                output_dir=Path(tmp) / "out",
                include_families=["barnes", "brandimarte", "dauzere", "hurink"],
                sample_count=10,
                sample_seed=20260617,
            )

            names = [path.name for path in selected_instances(request)]
            counts: dict[str, int] = {}
            for name in names:
                family = instance_family(Path(name))
                counts[family] = counts.get(family, 0) + 1

            self.assertEqual(10, len(names))
            self.assertEqual({"barnes": 3, "brandimarte": 3, "dauzere": 2, "hurink": 2}, counts)
            self.assertNotEqual(
                [f"fjsp.barnes.case{index:02d}.m1j1c1.txt" for index in range(10)],
                names,
            )

    def test_awls_benchmark_instance_names_override_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp)
            for index in range(5):
                (instance_dir / f"fjsp.barnes.case{index:02d}.m1j1c1.txt").write_text(
                    "placeholder",
                    encoding="utf-8",
                )

            request = AwlsBenchmarkRequest(
                instance_dir=instance_dir,
                pattern="*.txt",
                output_dir=Path(tmp) / "out",
                instance_names=[
                    "fjsp.barnes.case03.m1j1c1.txt",
                    "fjsp.barnes.case01.m1j1c1.txt",
                    "fjsp.barnes.case03.m1j1c1.txt",
                ],
                sample_count=1,
                max_instances=1,
            )

            names = [path.name for path in selected_instances(request)]

            self.assertEqual(["fjsp.barnes.case03.m1j1c1.txt", "fjsp.barnes.case01.m1j1c1.txt"], names)

    def test_awls_benchmark_instance_names_fail_on_missing_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp)
            (instance_dir / "fjsp.barnes.case00.m1j1c1.txt").write_text("placeholder", encoding="utf-8")
            request = AwlsBenchmarkRequest(
                instance_dir=instance_dir,
                pattern="*.txt",
                output_dir=Path(tmp) / "out",
                instance_names=["missing.txt"],
            )

            with self.assertRaisesRegex(ValueError, "not found"):
                selected_instances(request)


if __name__ == "__main__":
    unittest.main()
