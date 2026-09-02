from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.contract_metrics import build_contract_comparison
from harness_agent.orchestration.standard import merged_interval_seconds, standard_execution_timing


class ContractMetricsTests(unittest.TestCase):
    def make_manifest(
        self,
        *,
        mode: str,
        makespan: float,
        valid: int = 2,
        total: int = 2,
        solver_seconds: float = 10.0,
        baseline_key: list[float] | None = None,
    ) -> dict[str, object]:
        return {
            "status": "ok",
            "request": {
                "guidance_mode": mode,
                "worker_model": "opencode/test-model",
                "main_agent_model": "opencode/test-model",
                "semantic_reviewer_model": "opencode/test-model" if mode == "full" else "",
                "seeds": [0, 1],
                "iterations": 2,
                "timeout_seconds": 60,
                "max_workers": 1,
                "max_steps": 4,
                "max_runtime_seconds": 120,
                "promotion_repeats": 1,
                "in_round_repair_attempts": 1,
                "max_competing_workers": 3,
            },
            "input_fingerprints": {
                "files": [{"path": "instance.fjs", "sha256": "abc"}],
                "evaluator_command": "python evaluator.py",
                "objectives": [{"name": "makespan", "direction": "min"}],
            },
            "baseline_source": "provided_project",
            "baseline_key": baseline_key or [-120.0],
            "final_summary": {
                "total": total,
                "valid": valid,
                "best_candidate_metrics": {
                    "avg_makespan": makespan,
                    "avg_solver_wall_seconds": solver_seconds,
                },
            },
            "round_count": 2,
            "promoted_rounds": 1,
            "rounds": [
                {
                    "direction_plan": {
                        "candidate_variants": [
                            {"method_name": "method a"},
                            {"method_name": "method b"},
                            {"method_name": "method c"},
                        ],
                        "competition_result": {"candidate_count": 3},
                    }
                },
                {
                    "direction_plan": {
                        "candidate_variants": [
                            {"method_name": "method d"},
                            {"method_name": "method e"},
                            {"method_name": "method f"},
                        ],
                        "competition_result": {"candidate_count": 3},
                    }
                },
            ],
            "execution_timing": {"controller_wall_seconds_excluding_core": 100.0},
        }

    def write_manifest(self, root: Path, name: str, payload: dict[str, object]) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_comparison_reports_contract_quality_gain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = self.write_manifest(root, "full.json", self.make_manifest(mode="full", makespan=90.0))
            none = self.write_manifest(root, "none.json", self.make_manifest(mode="none", makespan=100.0))

            result = build_contract_comparison(
                full_manifest_paths=[full],
                none_manifest_paths=[none],
                output_dir=root / "report",
            )

            self.assertEqual("comparable", result["status"])
            self.assertAlmostEqual(10.0, result["metrics"]["quality"]["improvement_percent"])
            self.assertTrue(result["verdicts"]["contractual"]["quality_5_percent"])
            self.assertTrue((root / "report" / "contract_comparison.json").is_file())

    def test_comparison_rejects_different_or_generated_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full_payload = self.make_manifest(mode="full", makespan=90.0)
            full_payload["baseline_source"] = "agent_generated"
            full = self.write_manifest(root, "full.json", full_payload)
            none = self.write_manifest(root, "none.json", self.make_manifest(mode="none", makespan=100.0))

            result = build_contract_comparison(
                full_manifest_paths=[full],
                none_manifest_paths=[none],
                output_dir=root / "report",
            )

            self.assertEqual("protocol_invalid", result["status"])
            self.assertFalse(result["verdicts"]["contractual_any_passed"])
            frozen = next(item for item in result["protocol_checks"] if item["check"] == "frozen_shared_baseline")
            self.assertFalse(frozen["passed"])

    def test_overlapping_core_intervals_are_counted_once(self) -> None:
        self.assertEqual(8.0, merged_interval_seconds([(1.0, 5.0), (3.0, 7.0), (9.0, 11.0)]))

    def test_standard_timing_subtracts_union_of_core_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, interval in enumerate(((102.0, 106.0), (104.0, 108.0))):
                directory = root / str(index)
                directory.mkdir()
                (directory / "core_evaluation_timing.json").write_text(
                    json.dumps({"started_at_epoch": interval[0], "finished_at_epoch": interval[1]}),
                    encoding="utf-8",
                )

            timing = standard_execution_timing(
                output_dir=root,
                run_started_at_epoch=100.0,
                run_finished_at_epoch=110.0,
            )

            self.assertEqual(6.0, timing["fixed_core_evaluation_wall_seconds"])
            self.assertEqual(4.0, timing["controller_wall_seconds_excluding_core"])


if __name__ == "__main__":
    unittest.main()
