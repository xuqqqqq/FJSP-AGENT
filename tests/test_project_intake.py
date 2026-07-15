from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.context.intake import ProjectIntakeRequest, write_project_intake


ROOT = Path(__file__).resolve().parents[1]


class ProjectIntakeTests(unittest.TestCase):
    def test_project_intake_writes_context_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_project_intake(
                ProjectIntakeRequest(
                    project_root=ROOT,
                    output_dir=Path(tmp) / "project_intake",
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    max_files=60,
                    max_symbols_per_file=8,
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertIn(manifest["language_summary"]["primary_language"], {"Python", "Documentation"})
            self.assertIn("examples/standard_fjsp_evaluator.py", manifest["entry_files"])
            self.assertIn("examples/standard_fjsp_evaluator.py", manifest["validator_files"])
            self.assertTrue(manifest["test_commands"])
            self.assertTrue(manifest["context_index"])
            self.assertEqual([], [risk for risk in manifest["risk_flags"] if risk["code"] == "outputs_not_forbidden"])
            self.assertTrue(Path(manifest["artifacts"]["manifest"]).exists())
            self.assertTrue(Path(manifest["artifacts"]["report"]).exists())


if __name__ == "__main__":
    unittest.main()
