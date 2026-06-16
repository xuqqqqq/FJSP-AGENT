from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.contract_builder import DraftContractRequest, build_draft_contract
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

    def test_context_packet_embeds_compact_contract_review_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "requirements.md"
            instance = tmp_path / "case.json"
            contract_path = tmp_path / "draft_contract.json"
            doc.write_text(
                """
# FJSP 需求

## 目标指标

目标包括产量和 setup 切换次数。

## 约束清单

需要满足释放时间、维修窗口和组批约束。

## 输入输出结构

输入包含工序、候选机器和维修窗口。

## 算法提示

优先考虑局部搜索和强化学习风格的策略迭代。
                """.strip(),
                encoding="utf-8",
            )
            instance.write_text("{}", encoding="utf-8")
            contract_payload = build_draft_contract(
                DraftContractRequest(
                    task_id="draft_context_case",
                    docs=[doc],
                    instances=[instance],
                    output=contract_path,
                )
            )
            contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract_path,
                    output_path=tmp_path / "context_packet.json",
                    hypothesis="Use document schema grounding.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

            evidence = packet["contract_review_evidence"]
            self.assertTrue(evidence["has_document_schema"])
            self.assertEqual("draft_requires_human_confirmation", evidence["status"])
            self.assertGreaterEqual(evidence["document_schema"]["section_count"], 2)
            role_counts = evidence["document_schema"]["role_counts"]
            self.assertGreaterEqual(role_counts["objectives"], 1)
            self.assertGreaterEqual(role_counts["input_output"], 1)
            headings = [
                section["heading"]
                for document in evidence["document_schema"]["documents"]
                for section in document["sections"]
            ]
            self.assertIn("目标指标", headings)
            self.assertTrue(any(item["metric"] == "completed_weight" for item in evidence["metric_hints"]))
            self.assertIn("Review contract_review_evidence", " ".join(packet["worker_instruction"]["required_order"]))
            self.assertIn(
                "role_prioritized_sections",
                " ".join(packet["worker_instruction"]["required_order"]),
            )

            prioritized = evidence["role_prioritized_sections"]
            self.assertGreaterEqual(len(prioritized), 3)
            self.assertEqual("目标指标", prioritized[0]["heading"])
            prioritized_headings = [item["heading"] for item in prioritized]
            self.assertIn("约束清单", prioritized_headings)
            self.assertIn("输入输出结构", prioritized_headings)
            self.assertTrue(prioritized[0]["priority_reason"].startswith("roles=objectives"))


if __name__ == "__main__":
    unittest.main()
