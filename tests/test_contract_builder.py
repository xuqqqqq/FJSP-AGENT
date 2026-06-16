from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.contract_builder import DraftContractRequest, build_draft_contract


class ContractBuilderTests(unittest.TestCase):
    def test_draft_contract_extracts_source_grounded_features_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            doc = tmp_path / "requirements.md"
            instance = tmp_path / "case.json"
            doc.write_text(
                """
# 柔性车间调度需求

本问题属于 FJSP 场景，每道工序有候选机器。目标包括提升产量，并降低 setup 切换次数。
约束包括释放时间、最大生产间隔、最小生产间隔、设备维修窗口、跨厂转运时间、
工件优先级、可重入工序、替代加工路径以及 p-batch 组批加工。
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
            self.assertIn("rule_based_source_grounding_v1", review["extraction_method"])


if __name__ == "__main__":
    unittest.main()
