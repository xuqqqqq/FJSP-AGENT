from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harness_agent.awls_zi_evolution import AwlsZiEvolutionRequest
from harness_agent.awls_zi_evolution import (
    build_deepseek_prompt,
    candidate_record,
    candidate_row,
    is_better,
    normalize_candidates,
    normalize_portfolio_lanes,
    write_candidate_failure_manifest,
)


class AwlsZiEvolutionTests(unittest.TestCase):
    def test_is_better_falls_back_to_makespan_without_gap(self) -> None:
        incumbent = {"avg_gap_pct": None, "avg_makespan": 2209.0, "invalid_run_count": 0}
        candidate = {"avg_gap_pct": None, "avg_makespan": 2195.0, "invalid_run_count": 0}

        self.assertTrue(is_better(candidate, incumbent))

    def test_is_better_rejects_invalid_makespan_improvement(self) -> None:
        incumbent = {"avg_gap_pct": None, "avg_makespan": 2209.0, "invalid_run_count": 0}
        candidate = {"avg_gap_pct": None, "avg_makespan": 2195.0, "invalid_run_count": 1}

        self.assertFalse(is_better(candidate, incumbent))

    def test_normalize_portfolio_lanes_canonicalizes_safe_lanes(self) -> None:
        self.assertEqual(
            "2:mixed:1:8,5:random:2",
            normalize_portfolio_lanes(" 2:mixed:1:8.0, 5:random:2 "),
        )

    def test_normalize_portfolio_lanes_rejects_unbounded_lanes(self) -> None:
        with self.assertRaises(ValueError):
            normalize_portfolio_lanes("1:mixed:5")
        with self.assertRaises(ValueError):
            normalize_portfolio_lanes("1:mixed:1:301")

    def test_normalize_candidates_keeps_valid_portfolio_lanes(self) -> None:
        candidates = normalize_candidates(
            {
                "candidates": [
                    {
                        "name": "seed_portfolio",
                        "beta": 500,
                        "gamma": 40,
                        "theta": 5,
                        "zi_policy": "cpp",
                        "portfolio_lanes": "2:mixed:1:8.0,0:mixed:1:8.0",
                    }
                ]
            },
            1,
            0,
        )

        self.assertEqual("2:mixed:1:8,0:mixed:1:8", candidates[0]["portfolio_lanes"])

    def test_normalize_candidates_preserves_large_critical_exhaustive_pct(self) -> None:
        candidates = normalize_candidates(
            {
                "candidates": [
                    {
                        "name": "deep_critical_scan",
                        "beta": 500,
                        "gamma": 40,
                        "theta": 5,
                        "zi_policy": "aggressive",
                        "critical_block_exhaustive_pct": 50,
                    }
                ]
            },
            1,
            0,
        )

        self.assertEqual(50, candidates[0]["critical_block_exhaustive_pct"])

    def test_normalize_candidates_clamps_critical_exhaustive_pct_to_solver_limit(self) -> None:
        candidates = normalize_candidates(
            {
                "candidates": [
                    {
                        "name": "too_deep_scan",
                        "beta": 500,
                        "gamma": 40,
                        "theta": 5,
                        "zi_policy": "aggressive",
                        "critical_block_exhaustive_pct": 150,
                    }
                ]
            },
            1,
            0,
        )

        self.assertEqual(100, candidates[0]["critical_block_exhaustive_pct"])

    def test_candidate_row_reports_delta_against_baseline(self) -> None:
        baseline = {"avg_makespan": 1177.0, "avg_gap_pct": 18.0542, "candidate": {}}
        candidate = {
            "name": "aggressive",
            "avg_makespan": 1154.0,
            "avg_gap_pct": 15.7472,
            "median_gap_pct": 15.7472,
            "max_gap_pct": 15.7472,
            "invalid_run_count": 0,
            "candidate": {
                "zi_policy": "aggressive",
                "beta": 500,
                "gamma": 40,
                "theta": 5,
                "critical_block_exhaustive_pct": 50,
            },
        }

        row = candidate_row("round_00", candidate, baseline)

        self.assertIn("|  | 1154 | -23 |", row)
        self.assertIn("| 15.7472 | -2.307 |", row)
        self.assertIn("| 50 |", row)

    def test_candidate_record_recovers_config_from_benchmark_manifest(self) -> None:
        record = candidate_record(
            "baseline",
            "baseline",
            {
                "request": {
                    "init_mode": "mixed",
                    "beta": 500,
                    "gamma": 40,
                    "theta": 5,
                    "zi_policy": "aggressive",
                    "critical_block_exhaustive_pct": 50,
                    "same_machine_eval": "stable",
                },
                "aggregate": {"avg_makespan": 1023.0, "avg_gap_pct": 2.6078, "invalid_run_count": 0},
                "artifacts": {},
            },
            {},
        )

        self.assertEqual("aggressive", record["candidate"]["zi_policy"])
        self.assertEqual(50, record["candidate"]["critical_block_exhaustive_pct"])

    def test_request_can_represent_sdst_incumbent_baseline(self) -> None:
        request = AwlsZiEvolutionRequest(
            instance_dir=Path("."),
            pattern="oddla20.txt",
            output_dir=Path("out"),
            beta=400,
            gamma=40,
            theta=5,
            zi_policy="critical",
            critical_block_exhaustive_pct=75,
            same_machine_eval="stable",
        )
        payload = request.__dict__

        self.assertEqual("critical", payload["zi_policy"])
        self.assertEqual(75, payload["critical_block_exhaustive_pct"])

    def test_candidate_failure_manifest_records_invalid_candidate_without_score(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest = write_candidate_failure_manifest(
                AwlsZiEvolutionRequest(
                    instance_dir=Path(tmp),
                    pattern="oddla20.txt",
                    output_dir=Path(tmp) / "out",
                    seeds=[0],
                ),
                ["oddla20.txt"],
                Path(tmp) / "candidate",
                {"name": "bad_formula", "zi_policy": "formula"},
                ValueError("unknown zi_formula symbol: foo"),
            )

        record = candidate_record("bad_formula", "round_00", manifest, {"name": "bad_formula"})

        self.assertEqual("candidate_failed", record["status"])
        self.assertEqual(1, record["invalid_run_count"])
        self.assertIsNone(record["avg_makespan"])
        self.assertIn("unknown zi_formula symbol", record["errors"][0])

    def test_deepseek_prompt_includes_sdst_memory_card(self) -> None:
        prompt = build_deepseek_prompt(
            AwlsZiEvolutionRequest(instance_dir=Path("."), pattern="oddla20.txt", output_dir=Path("out")),
            ["oddla20.txt"],
            {"aggregate": {}, "instances": [], "runs": []},
            [],
            {},
            0,
        )

        self.assertIn("Local SDST-HUdata measured memory and cautions", prompt)
        self.assertIn("AWLS-SDST Neighborhood Selection Notes", prompt)
        self.assertIn("AWLS-SDST Move Evaluation Notes", prompt)
        self.assertIn("AWLS-SDST Initialization Notes", prompt)
        self.assertIn("AWLS-SDST Same-Machine N7 Notes", prompt)
        self.assertIn("non-empty `portfolio_lanes`", prompt)
        self.assertIn("same_machine_eval=cpp-fast", prompt)


if __name__ == "__main__":
    unittest.main()
