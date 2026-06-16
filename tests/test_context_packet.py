from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context_packet import ContextPacketRequest, write_context_packet
from harness_agent.project_intake import ProjectIntakeRequest, write_project_intake


ROOT = Path(__file__).resolve().parents[1]


class ContextPacketTests(unittest.TestCase):
    def test_context_packet_embeds_project_intake_summary(self) -> None:
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
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context_packet.json",
                    docs=[ROOT / "README.md"],
                    project_intake_manifest=Path(intake["artifacts"]["manifest"]),
                    hypothesis="Context-packet intake regression test.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

            self.assertTrue(packet["project_intake"]["exists"])
            self.assertEqual("ok", packet["project_intake"]["status"])
            self.assertEqual("Python", packet["project_intake"]["summary"]["language_summary"]["primary_language"])
            self.assertIn("examples/standard_fjsp_solver.py", packet["project_intake"]["summary"]["entry_files"])
            self.assertTrue(packet["project_intake"]["report"]["exists"])
            self.assertIn("Review project_intake", " ".join(packet["worker_instruction"]["required_order"]))


if __name__ == "__main__":
    unittest.main()
