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

    def test_context_packet_embeds_previous_pipeline_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            memory_path = tmp_path / "standard_pipeline_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pipeline_status": "ok",
                        "stage_status": {"admission_gate": "passed"},
                        "admission": {"gate": "passed"},
                        "benchmark_signal": {"avg_reported_gap_pct": 16.67},
                        "worker_signal": {
                            "baseline_key": [-7.0],
                            "final_key": [-7.0],
                            "improved": False,
                            "round_count": 1,
                            "promoted_rounds": 0,
                            "rounds": [
                                {
                                    "round_index": 0,
                                    "decision": "rolled_back",
                                    "worker_status": "skipped",
                                    "proposal_diagnostics": {"status": "missing"},
                                }
                            ],
                        },
                        "recommendations": ["Require a materially different rule."],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context_packet.json",
                    previous_pipeline_memory=memory_path,
                    hypothesis="Context-packet previous-memory regression test.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

            memory = packet["previous_pipeline_memory"]
            self.assertTrue(memory["exists"])
            self.assertEqual("ok", memory["pipeline_status"])
            self.assertEqual(16.67, memory["benchmark_signal"]["avg_reported_gap_pct"])
            self.assertEqual("rolled_back", memory["worker_signal"]["rounds"][0]["decision"])
            self.assertIn("Review previous_pipeline_memory", " ".join(packet["worker_instruction"]["required_order"]))


if __name__ == "__main__":
    unittest.main()
