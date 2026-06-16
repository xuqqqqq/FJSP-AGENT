from __future__ import annotations

import json
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
            self.assertEqual("ok", manifest["stage_status"]["project_intake"])
            self.assertEqual("ok", manifest["stage_status"]["health_check"])
            self.assertEqual("ready", manifest["stage_status"]["intent_alignment"])
            self.assertEqual("ok", manifest["stage_status"]["benchmark_suite"])
            self.assertEqual("ok", manifest["stage_status"]["standard_worker_loop"])
            self.assertGreaterEqual(manifest["stage_status"]["evidence_index_entries"], 6)
            self.assertEqual(0, manifest["stage_status"]["missing_artifact_count"])
            self.assertEqual("Python", manifest["project_intake"]["language_summary"]["primary_language"])
            self.assertEqual("ok", manifest["health_check"]["status"])
            self.assertTrue(manifest["intent_alignment"]["ready_for_optimization"])
            self.assertTrue(manifest["health_check"]["stability_probe"]["stable"])
            self.assertEqual(1, manifest["benchmark_suite"]["aggregate"]["valid_experiments"])
            self.assertEqual(1, manifest["standard_worker_loop"]["round_count"])
            self.assertEqual(1, len(manifest["standard_worker_loop"]["rounds"]))
            self.assertEqual(
                "missing",
                manifest["standard_worker_loop"]["rounds"][0]["proposal_diagnostics"]["status"],
            )
            self.assertTrue((output_dir / "standard_pipeline_manifest.json").exists())
            self.assertTrue((output_dir / "standard_pipeline_report.md").exists())
            self.assertTrue((output_dir / "standard_pipeline_memory.json").exists())
            self.assertTrue((output_dir / "standard_pipeline_memory.md").exists())
            self.assertTrue((output_dir / "project_intake" / "project_intake_manifest.json").exists())
            self.assertTrue((output_dir / "health_check" / "health_check_manifest.json").exists())
            self.assertTrue((output_dir / "intent_alignment" / "intent_alignment_manifest.json").exists())
            self.assertTrue((output_dir / "benchmark_suite" / "suite_manifest.json").exists())
            self.assertTrue((output_dir / "standard_worker_loop" / "standard_worker_loop_manifest.json").exists())
            context_packet = json.loads((output_dir / "standard_worker_loop" / "context_packet.json").read_text(encoding="utf-8"))
            self.assertTrue(context_packet["project_intake"]["exists"])
            self.assertEqual("ok", context_packet["project_intake"]["status"])
            memory = json.loads((output_dir / "standard_pipeline_memory.json").read_text(encoding="utf-8"))
            self.assertEqual("ok", memory["pipeline_status"])
            self.assertEqual(1, len(memory["worker_signal"]["rounds"]))
            self.assertEqual("missing", memory["worker_signal"]["rounds"][0]["proposal_diagnostics"]["status"])
            self.assertTrue(any("No worker round was promoted" in item for item in memory["recommendations"]))
            self.assertTrue((output_dir / "evidence_index" / "evidence_index.json").exists())

    def test_standard_pipeline_skips_optimization_when_admission_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_contract = root / "draft_standard_contract.json"
            payload = json.loads((ROOT / "configs" / "standard_fjsp_tiny.example.json").read_text(encoding="utf-8"))
            payload["review"] = {"status": "draft_requires_human_confirmation"}
            draft_contract.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            output_dir = root / "blocked_pipeline"
            manifest = run_standard_pipeline(
                StandardPipelineRequest(
                    suite_config=ROOT / "configs" / "standard_fjsp_suite.example.json",
                    output_dir=output_dir,
                    project_root=ROOT,
                    worker=NullWorker(),
                    worker_docs=[ROOT / "README.md"],
                    worker_instance_dir=ROOT / "examples",
                    health_contract=draft_contract,
                    worker_pattern="standard_fjsp_tiny.fjs",
                    worker_best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    worker_iterations=1,
                    worker_max_steps=1,
                    worker_max_runtime_seconds=30,
                )
            )

            self.assertEqual("partial_failed", manifest["status"])
            self.assertEqual("blocked", manifest["stage_status"]["admission_gate"])
            self.assertEqual("ok", manifest["stage_status"]["project_intake"])
            self.assertEqual("requires_confirmation", manifest["stage_status"]["health_check"])
            self.assertEqual("blocked", manifest["stage_status"]["intent_alignment"])
            self.assertEqual("skipped_admission_gate", manifest["stage_status"]["benchmark_suite"])
            self.assertEqual("skipped_admission_gate", manifest["stage_status"]["standard_worker_loop"])
            self.assertFalse((output_dir / "benchmark_suite" / "suite_manifest.json").exists())
            self.assertFalse((output_dir / "standard_worker_loop" / "standard_worker_loop_manifest.json").exists())
            self.assertTrue((output_dir / "standard_pipeline_memory.json").exists())
            self.assertTrue((output_dir / "project_intake" / "project_intake_manifest.json").exists())
            self.assertTrue((output_dir / "intent_alignment" / "intent_alignment_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
