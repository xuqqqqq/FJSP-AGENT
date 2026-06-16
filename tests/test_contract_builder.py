from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.contract_builder import (
    DraftContractRequest,
    build_draft_contract,
    draft_review_report_path,
    write_draft_contract,
)


class ContractBuilderTests(unittest.TestCase):
    def test_draft_contract_extracts_source_grounded_features_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "requirements.md"
            instance = tmp_path / "case.json"
            doc.write_text(
                """
# 柔性车间调度需求

## 目标与评价指标

本问题属于 FJSP 场景，每道工序有候选机器。目标包括提升产量，并降低 setup 切换次数。

## 约束清单

约束包括释放时间、最大生产间隔、最小生产间隔、设备维修窗口、跨厂转运时间、
工件优先级、可重入工序、替代加工路径以及 p-batch 组批加工。

## 输入输出结构

输入文件包含任务、工序、候选机器和维修窗口；输出文件包含每道工序的机器与时间。

## 算法提示

可以使用启发式规则、局部搜索或者强化学习 PPO 思想生成候选解。
                """.strip(),
                encoding="utf-8",
            )
            instance.write_text("{}", encoding="utf-8")

            payload = build_draft_contract(
                DraftContractRequest(
                    task_id="draft_case",
                    docs=[doc],
                    instances=[instance],
                    output=tmp_path / "draft.json",
                    solver_cmd="python solver.py --input {instance}",
                    evaluator_cmd="python evaluator.py --solution {solution}",
                )
            )

            self.assertEqual("FJSP", payload["problem_family"])
            objective_names = [item["name"] for item in payload["objectives"]]
            self.assertIn("completed_weight", objective_names)
            self.assertIn("setup_count", objective_names)

            review = payload["review"]
            feature_names = {item["name"] for item in review["extracted_problem_features"]}
            self.assertIn("batch_processing", feature_names)
            self.assertIn("maintenance_windows", feature_names)
            self.assertIn("cross_factory_transfer", feature_names)
            self.assertIn("alternative_routes", feature_names)

            metric_names = {item["metric"] for item in review["metric_hints"]}
            self.assertIn("completed_weight", metric_names)
            self.assertIn("setup_count", metric_names)

            missing_checks = [
                item
                for item in review["command_template_checks"]
                if item["status"] == "missing_placeholder"
            ]
            self.assertTrue(any(item["placeholder"] == "{solution}" and item["field"] == "commands.solver" for item in missing_checks))
            self.assertTrue(any(item["placeholder"] == "{metrics}" and item["field"] == "commands.evaluator" for item in missing_checks))
            self.assertIn("commands.solver", review["uncertain_fields"])
            self.assertIn("commands.evaluator", review["uncertain_fields"])
            self.assertTrue(review["confirmation_checklist"])
            self.assertIn("rule_based_source_grounding_v2", review["extraction_method"])

            schema = review["document_schema"]
            self.assertEqual(1, schema["document_count"])
            self.assertGreaterEqual(schema["section_count"], 4)
            self.assertGreaterEqual(schema["role_counts"]["objectives"], 1)
            self.assertGreaterEqual(schema["role_counts"]["constraints"], 1)
            self.assertGreaterEqual(schema["role_counts"]["input_output"], 1)
            self.assertGreaterEqual(schema["role_counts"]["algorithm_guidance"], 1)
            sections = schema["documents"][0]["sections"]
            objective_section = next(item for item in sections if item["heading"] == "目标与评价指标")
            constraint_section = next(item for item in sections if item["heading"] == "约束清单")
            self.assertIn("objectives", objective_section["roles"])
            self.assertTrue(any(item["metric"] == "completed_weight" for item in objective_section["metric_hints"]))
            self.assertIn("constraints", constraint_section["roles"])
            self.assertTrue(any(item["name"] == "maintenance_windows" for item in constraint_section["feature_hints"]))

    def test_write_draft_contract_writes_review_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "requirements.md"
            instance = tmp_path / "case.json"
            output = tmp_path / "draft_contract.json"
            doc.write_text(
                """
# FJSP 需求

## 目标指标

优化产量并减少 setup 切换次数。

## 输入输出

输入包含任务和候选机器；输出包含每道工序的开始结束时间。
                """.strip(),
                encoding="utf-8",
            )
            instance.write_text("{}", encoding="utf-8")

            written = write_draft_contract(
                DraftContractRequest(
                    task_id="draft_review_case",
                    docs=[doc],
                    instances=[instance],
                    output=output,
                    solver_cmd="python solver.py --input {instance} --output {solution}",
                    evaluator_cmd=(
                        "python evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
                    ),
                )
            )

            report_path = draft_review_report_path(written)
            self.assertEqual(output, written)
            self.assertTrue(output.exists())
            self.assertTrue(report_path.exists())

            payload = json.loads(output.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("document_schema", payload["review"])
            self.assertIn("# Draft Contract Review", report)
            self.assertIn("Markdown Document Schema", report)
            self.assertIn("目标指标", report)
            self.assertIn("Confirmation Checklist", report)
            self.assertIn("setup_count", report)


if __name__ == "__main__":
    unittest.main()
