from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.standard_pipeline import (
    StandardPipelineLoopRequest,
    StandardPipelineRequest,
    run_standard_pipeline,
    run_standard_pipeline_loop,
    summarize_operator_lineage,
)
from harness_agent.worker import NullWorker


ROOT = Path(__file__).resolve().parents[1]


class StandardPipelineTests(unittest.TestCase):
    def test_operator_lineage_summary_tracks_promoted_and_rolled_back_rules(self) -> None:
        summary = summarize_operator_lineage(
            [
                {
                    "round_index": 0,
                    "decision": "rolled_back",
                    "duplicate_proposal": False,
                    "proposal_diagnostics": {
                        "rule_operator_hypotheses": [
                            {
                                "name": "critical_block_bias",
                                "type": "local_search_operator",
                                "target_files": ["examples/standard_fjsp_solver.py"],
                                "expected_effect": "Reduce makespan.",
                                "novelty": "Moves inside critical blocks instead of dispatch-only sorting.",
                            }
                        ],
                    },
                },
                {
                    "round_index": 1,
                    "decision": "promoted",
                    "duplicate_proposal": False,
                    "proposal_diagnostics": {
                        "rule_operator_hypotheses": [
                            {
                                "name": "machine_load_insert",
                                "type": "constructive_rule",
                                "target_files": ["examples/standard_fjsp_solver.py"],
                                "expected_effect": "Reduce machine idle time.",
                                "novelty": "Changes insertion timing while preserving evaluator semantics.",
                            }
                        ],
                    },
                },
                {
                    "round_index": 2,
                    "decision": "rolled_back",
                    "duplicate_proposal": True,
                    "proposal_diagnostics": {"rule_operator_hypotheses": []},
                },
            ]
        )

        self.assertEqual(2, summary["hypothesis_count"])
        self.assertEqual(1, summary["missing_hypothesis_rounds"])
        self.assertEqual(1, summary["type_counts"]["constructive_rule"])
        self.assertEqual(1, summary["type_counts"]["local_search_operator"])
        self.assertEqual(1, summary["decision_counts"]["promoted"])
        self.assertEqual(1, summary["decision_counts"]["rolled_back"])
        self.assertEqual("machine_load_insert", summary["promoted_hypotheses"][0]["name"])
        self.assertEqual("critical_block_bias", summary["rolled_back_hypotheses"][0]["name"])

    def test_standard_pipeline_runs_all_core_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_dir = tmp_path / "standard_pipeline"
            previous_memory = _write_previous_memory(tmp_path)

            manifest = run_standard_pipeline(
                StandardPipelineRequest(
                    suite_config=ROOT / "configs" / "standard_fjsp_suite.example.json",
                    output_dir=output_dir,
                    project_root=ROOT,
                    worker=NullWorker(),
                    worker_docs=[ROOT / "README.md"],
                    worker_instance_dir=ROOT / "examples",
                    previous_pipeline_memory=previous_memory,
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
            self.assertEqual(1, memory["operator_lineage_signal"]["missing_hypothesis_rounds"])
            self.assertEqual(0, memory["operator_lineage_signal"]["hypothesis_count"])
            self.assertTrue(any("No worker round was promoted" in item for item in memory["recommendations"]))
            self.assertTrue(any("rule/operator hypotheses" in item for item in memory["recommendations"]))
            self.assertEqual(
                "ok",
                json.loads((output_dir / "standard_worker_loop" / "context_packet.json").read_text(encoding="utf-8"))[
                    "previous_pipeline_memory"
                ]["pipeline_status"],
            )
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

    def test_standard_pipeline_loop_chains_memory_between_iterations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "pipeline_loop"
            manifest = run_standard_pipeline_loop(
                StandardPipelineLoopRequest(
                    base_request=StandardPipelineRequest(
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
                    ),
                    rounds=2,
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual(2, manifest["round_count"])
            self.assertEqual(2, len(manifest["iterations"]))
            first_memory = Path(str(manifest["iterations"][0]["memory_path"]))
            self.assertTrue(first_memory.exists())
            self.assertEqual(str(first_memory), manifest["iterations"][1]["input_previous_memory"])
            second_context = json.loads(
                (output_dir / "iteration_001" / "standard_worker_loop" / "context_packet.json").read_text(encoding="utf-8")
            )
            self.assertEqual(str(first_memory), second_context["previous_pipeline_memory"]["path"])
            self.assertEqual("ok", second_context["previous_pipeline_memory"]["pipeline_status"])
            self.assertEqual("available", second_context["previous_pipeline_memory"]["operator_guidance"]["status"])
            self.assertIn(
                "include 1 to 3 concrete hypotheses",
                " ".join(second_context["previous_pipeline_memory"]["operator_guidance"]["must_do"]),
            )
            self.assertTrue(manifest["request"]["adapt_worker_hypothesis"])
            self.assertIn("Use the previous pipeline memory as measured evidence", second_context["hypothesis"])
            self.assertIn("target_best_known_gap_quality", second_context["hypothesis"])
            self.assertEqual(second_context["hypothesis"], manifest["iterations"][1]["worker_hypothesis"])
            self.assertEqual(manifest["iterations"][1]["memory_path"], manifest["artifacts"]["final_memory"])
            self.assertTrue((output_dir / "standard_pipeline_next_action_brief.json").exists())
            self.assertTrue((output_dir / "standard_pipeline_next_action_brief.md").exists())
            brief = json.loads((output_dir / "standard_pipeline_next_action_brief.json").read_text(encoding="utf-8"))
            self.assertEqual("ready", brief["status"])
            self.assertEqual(manifest["artifacts"]["final_memory"], brief["next_previous_memory"])
            self.assertIn("target_best_known_gap_quality", brief["focus_areas"])
            self.assertIn("require_rule_operator_hypotheses", brief["focus_areas"])
            self.assertEqual(1, brief["operator_lineage_signal"]["missing_hypothesis_rounds"])
            self.assertTrue(any("evaluator" in item.lower() for item in brief["proposal_requirements"]))
            self.assertTrue((output_dir / "standard_pipeline_loop_manifest.json").exists())
            self.assertTrue((output_dir / "standard_pipeline_loop_report.md").exists())


def _write_previous_memory(tmp_path: Path) -> Path:
    memory_path = tmp_path / "previous_standard_pipeline_memory.json"
    memory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pipeline_status": "ok",
                "stage_status": {"admission_gate": "passed"},
                "admission": {"gate": "passed"},
                "benchmark_signal": {"avg_reported_gap_pct": 13.0},
                "worker_signal": {"round_count": 1, "promoted_rounds": 0, "rounds": []},
                "recommendations": ["Use benchmark gap evidence in the next rule proposal."],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return memory_path


if __name__ == "__main__":
    unittest.main()
