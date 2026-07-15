from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.core.evidence import EvidenceIndexRequest, build_evidence_index


class EvidenceIndexTests(unittest.TestCase):
    def test_builds_index_across_demo_suite_and_worker_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.md"
            report.write_text("# report\n", encoding="utf-8")
            _write_json(
                root / "intake" / "project_intake_manifest.json",
                {
                    "status": "ok",
                    "artifacts": {"report": str(report)},
                    "language_summary": {"primary_language": "Python"},
                    "entry_files": ["examples/standard_fjsp_solver.py"],
                    "core_algorithm_files": ["examples/standard_fjsp_solver.py"],
                    "benchmark_files": ["harness_agent/benchmark_suite.py"],
                    "validator_files": ["examples/standard_fjsp_evaluator.py"],
                    "risk_flags": [],
                },
            )
            _write_json(
                root / "health" / "health_check_manifest.json",
                {
                    "status": "ok",
                    "artifacts": {"report": str(report)},
                    "quick_test": {"status": "ok"},
                    "stability_probe": {
                        "status": "ok",
                        "stable": True,
                        "valid": 2,
                        "total": 2,
                    },
                },
            )
            _write_json(
                root / "intent" / "intent_alignment_manifest.json",
                {
                    "status": "ready",
                    "artifacts": {"report": str(report)},
                    "ready_for_optimization": True,
                    "blockers": [],
                    "warnings": ["only one instance is configured; overfitting risk is high"],
                    "budget": {"planned_evaluator_runs": 2},
                },
            )
            _write_json(
                root / "demo" / "demo_manifest.json",
                {
                    "status": "ok",
                    "artifacts": {"report": str(report)},
                    "benchmark_summary": {
                        "valid_experiments": 1,
                        "total_experiments": 1,
                        "gap_metrics": {"avg_gap_pct": 10.0},
                    },
                    "artifact_checks": {"missing": []},
                },
            )
            _write_json(
                root / "suite" / "suite_manifest.json",
                {
                    "status": "ok",
                    "suite_count": 1,
                    "artifacts": {"report": str(report)},
                    "aggregate": {
                        "valid_experiments": 2,
                        "total_experiments": 2,
                        "avg_reported_gap_pct": 12.0,
                    },
                },
            )
            _write_json(
                root / "worker" / "standard_worker_loop_manifest.json",
                {
                    "status": "ok",
                    "artifacts": {"report": str(report), "missing": str(root / "missing.md")},
                    "baseline_key": [-7.0],
                    "final_key": [-6.0],
                    "improved": True,
                    "round_count": 1,
                    "promoted_rounds": 1,
                    "baseline_summary": {"valid": 1, "total": 1},
                },
            )

            index = build_evidence_index(
                EvidenceIndexRequest(
                    input_dirs=[root],
                    output_dir=root / "index",
                    title="Test Evidence",
                )
            )

            self.assertEqual(6, index["entry_count"])
            self.assertEqual(
                {
                    "benchmark_suite": 1,
                    "health_check": 1,
                    "intent_alignment": 1,
                    "project_intake": 1,
                    "standard_demo": 1,
                    "standard_worker_loop": 1,
                },
                index["summary"]["type_counts"],
            )
            self.assertEqual(6, index["summary"]["valid_experiments"])
            self.assertEqual(6, index["summary"]["total_experiments"])
            self.assertEqual(1, index["summary"]["missing_artifact_count"])
            self.assertEqual(1, index["summary"]["improved_worker_loops"])
            self.assertAlmostEqual(11.0, index["summary"]["avg_gap_metric"])
            self.assertTrue((root / "index" / "evidence_index.json").exists())
            self.assertIn("standard_worker_loop", (root / "index" / "evidence_index.md").read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
