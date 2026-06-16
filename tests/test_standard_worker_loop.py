from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from harness_agent.project_intake import ProjectIntakeRequest, write_project_intake
from harness_agent.standard_worker_loop import StandardWorkerLoopRequest, run_standard_worker_loop
from harness_agent.worker import NullWorker


ROOT = Path(__file__).resolve().parents[1]


class StandardWorkerLoopTests(unittest.TestCase):
    def test_standard_worker_loop_runs_baseline_and_candidate_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake = write_project_intake(
                ProjectIntakeRequest(
                    project_root=ROOT,
                    output_dir=tmp_path / "project_intake",
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    max_files=40,
                )
            )
            output_dir = Path(tmp) / "standard_worker"

            manifest = run_standard_worker_loop(
                StandardWorkerLoopRequest(
                    docs=[ROOT / "README.md"],
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    output_dir=output_dir,
                    project_root=ROOT,
                    worker=NullWorker(),
                    project_intake_manifest=Path(intake["artifacts"]["manifest"]),
                    max_instances=1,
                    seeds=[0],
                    timeout_seconds=30,
                    max_workers=1,
                    solver="portfolio",
                    portfolio_size=4,
                    iterations=1,
                    max_steps=1,
                    max_runtime_seconds=30,
                    apply_worker_changes=False,
                    experiment_id="test_standard_worker_loop",
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual(manifest["baseline_key"], manifest["final_key"])
            self.assertFalse(manifest["improved"])
            self.assertEqual(1, manifest["round_count"])
            self.assertEqual(0, manifest["promoted_rounds"])
            self.assertEqual(1, manifest["baseline_summary"]["valid"])
            self.assertEqual("rolled_back", manifest["rounds"][0]["decision"])
            self.assertEqual("missing", manifest["rounds"][0]["proposal_diagnostics"]["status"])
            self.assertEqual(str(Path(intake["artifacts"]["manifest"])), manifest["request"]["project_intake_manifest"])
            self.assertTrue((output_dir / "standard_worker_contract.json").exists())
            self.assertTrue((output_dir / "context_packet.json").exists())
            context_packet = json.loads((output_dir / "context_packet.json").read_text(encoding="utf-8"))
            self.assertTrue(context_packet["project_intake"]["exists"])
            self.assertTrue((output_dir / "standard_worker_loop_manifest.json").exists())
            self.assertTrue((output_dir / "standard_worker_loop_report.md").exists())
            self.assertTrue((output_dir / "worker_loop" / "loop_result.json").exists())


if __name__ == "__main__":
    unittest.main()
