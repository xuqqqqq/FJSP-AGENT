from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.context.intake import ProjectIntakeRequest, write_project_intake


ROOT = Path(__file__).resolve().parents[1]


class ProjectIntakeTests(unittest.TestCase):
    def test_project_intake_writes_context_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            examples_dir = project_root / "examples"
            examples_dir.mkdir(parents=True)
            (project_root / "README.md").write_text("# Intake fixture\n", encoding="utf-8")
            (examples_dir / "agent_generated_fjsp_solver.py").write_text(
                "def main():\n    return 0\n",
                encoding="utf-8",
            )
            (examples_dir / "standard_fjsp_evaluator.py").write_text(
                "def main():\n    return 0\n",
                encoding="utf-8",
            )
            manifest = write_project_intake(
                ProjectIntakeRequest(
                    project_root=project_root,
                    output_dir=tmp_path / "project_intake",
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
