from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.core.health import HealthCheckRequest, run_health_check
from harness_agent.core.intent import IntentAlignmentRequest, write_intent_alignment


ROOT = Path(__file__).resolve().parents[1]


class IntentAlignmentTests(unittest.TestCase):
    def test_intent_alignment_summarizes_contract_and_health_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health = run_health_check(
                HealthCheckRequest(
                    contract_path=ROOT / "configs" / "task_contract.example.json",
                    output_dir=root / "health",
                    project_root=ROOT,
                    repeats=2,
                )
            )

            manifest = write_intent_alignment(
                IntentAlignmentRequest(
                    contract_path=ROOT / "configs" / "task_contract.example.json",
                    output_dir=root / "intent",
                    project_root=ROOT,
                    health_manifest_path=Path(health["artifacts"]["manifest"]),
                )
            )

            self.assertEqual("ready", manifest["status"])
            self.assertTrue(manifest["ready_for_optimization"])
            self.assertEqual([], manifest["blockers"])
            self.assertEqual("demo_dummy_fjsp_contract", manifest["task"]["task_id"])
            self.assertEqual("primary_score", manifest["objectives"][0]["name"])
            self.assertEqual("maximize", manifest["objectives"][0]["direction"])
            self.assertEqual(4, manifest["budget"]["planned_evaluator_runs"])
            self.assertEqual("stable", manifest["risk"]["benchmark_stability"])
            self.assertEqual("high", manifest["risk"]["overfitting_risk"])
            self.assertTrue((root / "intent" / "intent_alignment_manifest.json").exists())
            self.assertTrue((root / "intent" / "intent_alignment_report.md").exists())


if __name__ == "__main__":
    unittest.main()
