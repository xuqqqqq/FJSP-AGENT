from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.standard_pipeline import StandardPipelineRequest, run_standard_pipeline
from harness_agent.worker import NullWorker


ROOT = Path(__file__).resolve().parents[1]


class StandardPipelineTests(unittest.TestCase):
    def test_standard_pipeline_runs_all_core_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "standard_pipeline"

            manifest = run_standard_pipeline(
                StandardPipelineRequest(
                    suite_config=ROOT / "configs" / "standard_fjsp_suite.example.json",
                    output_dir=output_dir,
                    project_root=ROOT,
                    worker=NullWorker(),
                    worker_docs=[ROOT / "README.md"],
                    worker_instance_dir=ROOT / "examples",
                    health_contract=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    health_repeats=2,
                    worker_pattern="standard_fjsp_tiny.fjs",
                    worker_best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    worker_max_instances=1,
                    worker_seeds=[0],
                    worker_timeout_seconds=30,
                    worker_solver="portfolio",
                    worker_portfolio_size=4,
                    worker_iterations=1,
                    worker_max_steps=1,
                    worker_max_runtime_seconds=30,
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual("ok", manifest["stage_status"]["health_check"])
            self.assertEqual("ok", manifest["stage_status"]["benchmark_suite"])
            self.assertEqual("ok", manifest["stage_status"]["standard_worker_loop"])
            self.assertGreaterEqual(manifest["stage_status"]["evidence_index_entries"], 4)
            self.assertEqual(0, manifest["stage_status"]["missing_artifact_count"])
            self.assertEqual("ok", manifest["health_check"]["status"])
            self.assertTrue(manifest["health_check"]["stability_probe"]["stable"])
            self.assertEqual(1, manifest["benchmark_suite"]["aggregate"]["valid_experiments"])
            self.assertEqual(1, manifest["standard_worker_loop"]["round_count"])
            self.assertTrue((output_dir / "standard_pipeline_manifest.json").exists())
            self.assertTrue((output_dir / "standard_pipeline_report.md").exists())
            self.assertTrue((output_dir / "health_check" / "health_check_manifest.json").exists())
            self.assertTrue((output_dir / "benchmark_suite" / "suite_manifest.json").exists())
            self.assertTrue((output_dir / "standard_worker_loop" / "standard_worker_loop_manifest.json").exists())
            self.assertTrue((output_dir / "evidence_index" / "evidence_index.json").exists())


if __name__ == "__main__":
    unittest.main()
