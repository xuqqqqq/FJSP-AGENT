from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from harness_agent.project_intake import ProjectIntakeRequest, write_project_intake
from harness_agent.slot_manifest import write_default_slot_manifest
from harness_agent.standard_worker_loop import StandardWorkerLoopRequest, run_standard_worker_loop, standard_solver_command
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
            previous_memory = _write_previous_memory(tmp_path)
            slot_manifest = tmp_path / "slot_manifest.json"
            write_default_slot_manifest(problem_family="standard_fjsp", output=slot_manifest, confirmed=True)

            manifest = run_standard_worker_loop(
                StandardWorkerLoopRequest(
                    docs=[ROOT / "README.md"],
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    best_known_csv=ROOT / "configs" / "standard_fjsp_tiny_best.csv",
                    output_dir=output_dir,
                    project_root=ROOT,
                    worker=NullWorker(),
                    slot_manifest=slot_manifest,
                    project_intake_manifest=Path(intake["artifacts"]["manifest"]),
                    previous_pipeline_memory=previous_memory,
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
            self.assertIn("final_summary", manifest)
            self.assertEqual(manifest["baseline_summary"], manifest["final_summary"])
            self.assertIn("latest_candidate_summary", manifest)
            self.assertEqual("rolled_back", manifest["rounds"][0]["decision"])
            self.assertEqual("missing", manifest["rounds"][0]["proposal_diagnostics"]["status"])
            self.assertEqual(str(slot_manifest), manifest["request"]["slot_manifest"])
            self.assertEqual(str(Path(intake["artifacts"]["manifest"])), manifest["request"]["project_intake_manifest"])
            self.assertEqual(str(previous_memory), manifest["request"]["previous_pipeline_memory"])
            self.assertTrue((output_dir / "standard_worker_contract.json").exists())
            self.assertTrue((output_dir / "context_packet.json").exists())
            context_packet = json.loads((output_dir / "context_packet.json").read_text(encoding="utf-8"))
            self.assertTrue(context_packet["project_intake"]["exists"])
            self.assertEqual("confirmed", context_packet["slot_manifest"]["status"])
            self.assertEqual("available", context_packet["instance_diagnostics"]["status"])
            self.assertEqual(1, context_packet["instance_diagnostics"]["summary"]["profiled_count"])
            self.assertEqual("standard_fjsp", context_packet["instance_diagnostics"]["instances"][0]["variant"])
            self.assertIn(
                "Review slot_manifest",
                " ".join(context_packet["worker_instruction"]["required_order"]),
            )
            self.assertEqual("ok", context_packet["previous_pipeline_memory"]["pipeline_status"])
            self.assertTrue((output_dir / "standard_worker_loop_manifest.json").exists())
            self.assertTrue((output_dir / "standard_worker_loop_report.md").exists())
            self.assertTrue((output_dir / "worker_loop" / "loop_result.json").exists())

    def test_agent_generated_baseline_command_points_to_generated_entrypoint(self) -> None:
        request = StandardWorkerLoopRequest(
            docs=[ROOT / "README.md"],
            instance_dir=ROOT / "examples",
            pattern="standard_fjsp_tiny.fjs",
            output_dir=Path("unused"),
            project_root=ROOT,
            worker=NullWorker(),
            baseline_source="agent_generated",
            agent_generated_solver_path="examples/generated_solver_for_test.py",
        )

        command = standard_solver_command(request)

        self.assertIn("examples/generated_solver_for_test.py", command)
        self.assertIn("--input {instance}", command)
        self.assertIn("--output {solution}", command)
        self.assertIn("--seed {seed}", command)


def _write_previous_memory(tmp_path: Path) -> Path:
    memory_path = tmp_path / "previous_standard_pipeline_memory.json"
    memory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pipeline_status": "ok",
                "stage_status": {"admission_gate": "passed"},
                "admission": {"gate": "passed"},
                "benchmark_signal": {"avg_reported_gap_pct": 12.5},
                "worker_signal": {"round_count": 1, "promoted_rounds": 0, "rounds": []},
                "recommendations": ["Try a materially different solver rule."],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return memory_path


if __name__ == "__main__":
    unittest.main()
