from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harness_agent.awls_zi_evolution import AwlsZiEvolutionRequest
from harness_agent.awls_zi_evolution import (
    build_deepseek_prompt,
    candidate_record,
    candidate_row,
    candidate_signature,
    collect_candidate_signatures,
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

    def test_normalize_candidates_rejects_prior_duplicate_configuration(self) -> None:
        prior = {
            candidate_signature(
                {
                    "beta": 400,
                    "gamma": 40,
                    "theta": 5,
                    "zi_policy": "critical",
                    "zi_formula": "",
                    "critical_block_exhaustive_pct": 75,
                    "same_machine_eval": "stable",
                    "portfolio_lanes": "",
                }
            )
        }

        with self.assertRaisesRegex(ValueError, "repeats prior measured configuration"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "incumbent_repeat",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "critical",
                            "critical_block_exhaustive_pct": 75,
                            "same_machine_eval": "stable",
                        }
                    ]
                },
                1,
                0,
                prior_signatures=prior,
            )

    def test_normalize_candidates_requires_portfolio_candidate_for_multi_candidate_round(self) -> None:
        with self.assertRaisesRegex(ValueError, "portfolio_lanes"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "formula_a",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "formula",
                            "zi_formula": "base * (1 + 0.2 * is_critical)",
                            "critical_block_exhaustive_pct": 75,
                        },
                        {
                            "name": "formula_b",
                            "beta": 500,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "formula",
                            "zi_formula": "max(0, base + 0.02 * backward * is_critical)",
                            "critical_block_exhaustive_pct": 75,
                        },
                    ]
                },
                2,
                0,
                require_portfolio_candidate=True,
            )

    def test_normalize_candidates_requires_setup_feature_formula_when_formula_round_has_portfolio(self) -> None:
        with self.assertRaisesRegex(ValueError, "setup_"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "old_formula_with_portfolio",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "formula",
                            "zi_formula": "base * (1 + 0.2 * is_critical)",
                            "critical_block_exhaustive_pct": 75,
                            "portfolio_lanes": "1:mixed:1:5,4:mixed:1:5",
                        },
                        {
                            "name": "critical",
                            "beta": 500,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "critical",
                            "critical_block_exhaustive_pct": 75,
                        },
                    ]
                },
                2,
                0,
                require_portfolio_candidate=True,
            )

    def test_normalize_candidates_accepts_setup_feature_formula_round(self) -> None:
        candidates = normalize_candidates(
            {
                "candidates": [
                    {
                        "name": "setup_formula_with_portfolio",
                        "beta": 400,
                        "gamma": 40,
                        "theta": 5,
                        "zi_policy": "formula",
                        "zi_formula": "base * (1 + 0.2 * setup_next_ratio * is_critical)",
                        "critical_block_exhaustive_pct": 75,
                        "portfolio_lanes": "1:mixed:1:5,4:mixed:1:5",
                    },
                    {
                        "name": "critical",
                        "beta": 500,
                        "gamma": 40,
                        "theta": 5,
                        "zi_policy": "critical",
                        "critical_block_exhaustive_pct": 75,
                    },
                ]
            },
            2,
            0,
            require_portfolio_candidate=True,
        )

        self.assertIn("setup_next_ratio", candidates[0]["zi_formula"])

    def test_normalize_candidates_rejects_known_failed_portfolio_lanes(self) -> None:
        with self.assertRaisesRegex(ValueError, "repeats failed portfolio_lanes"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "failed_lane_repeat",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "critical",
                            "critical_block_exhaustive_pct": 75,
                            "portfolio_lanes": "0:mixed:1:6,6:mixed:1:6,7:greedy:1:6",
                        }
                    ]
                },
                1,
                0,
            )

    def test_normalize_candidates_rejects_failed_pct80_diverse_lanes(self) -> None:
        with self.assertRaisesRegex(ValueError, "repeats failed portfolio_lanes"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "failed_pct80_lanes",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "critical",
                            "critical_block_exhaustive_pct": 80,
                            "portfolio_lanes": "6:mixed:1:6.0, 7:greedy:1:6, 3:random:1:6",
                        }
                    ]
                },
                1,
                0,
            )

    def test_normalize_candidates_rejects_failed_setup_formula_portfolios(self) -> None:
        for lane_string in (
            "2:random:1:6,5:greedy:1:6,8:mixed:1:6",
            "1:greedy:1:6,3:random:1:6,7:mixed:1:6",
        ):
            with self.subTest(lane_string=lane_string):
                with self.assertRaisesRegex(ValueError, "repeats failed portfolio_lanes"):
                    normalize_candidates(
                        {
                            "candidates": [
                                {
                                    "name": "failed_setup_formula_lanes",
                                    "beta": 400,
                                    "gamma": 40,
                                    "theta": 5,
                                    "zi_policy": "formula",
                                    "zi_formula": "base * (1 + 0.2 * is_critical * setup_next_ratio)",
                                    "critical_block_exhaustive_pct": 75,
                                    "portfolio_lanes": lane_string,
                                }
                            ]
                        },
                        1,
                        0,
                    )

    def test_normalize_candidates_rejects_failed_aggressive_179_portfolio(self) -> None:
        with self.assertRaisesRegex(ValueError, "repeats failed portfolio_lanes"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "failed_aggressive_179",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "aggressive",
                            "critical_block_exhaustive_pct": 75,
                            "portfolio_lanes": "1:random:1:6,7:greedy:1:6,9:mixed:1:6",
                        }
                    ]
                },
                1,
                0,
            )

    def test_normalize_candidates_rejects_failed_sqrt_stable_pct75(self) -> None:
        with self.assertRaisesRegex(ValueError, "sqrt stable pct75"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "failed_sqrt_pct75",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "sqrt",
                            "critical_block_exhaustive_pct": 75,
                            "same_machine_eval": "stable",
                            "portfolio_lanes": "",
                        }
                    ]
                },
                1,
                0,
            )

    def test_normalize_candidates_rejects_known_flat_direct_pct_probe(self) -> None:
        with self.assertRaisesRegex(ValueError, "flat/worse direct pct probe"):
            normalize_candidates(
                {
                    "candidates": [
                        {
                            "name": "critical_pct60_repeat",
                            "beta": 400,
                            "gamma": 40,
                            "theta": 5,
                            "zi_policy": "critical",
                            "critical_block_exhaustive_pct": 60,
                            "same_machine_eval": "stable",
                            "portfolio_lanes": "",
                        }
                    ]
                },
                1,
                0,
            )

    def test_normalize_candidates_allows_known_pct_with_material_lane_change(self) -> None:
        candidates = normalize_candidates(
            {
                "candidates": [
                    {
                        "name": "critical_pct60_with_lanes",
                        "beta": 400,
                        "gamma": 40,
                        "theta": 5,
                        "zi_policy": "critical",
                        "critical_block_exhaustive_pct": 60,
                        "same_machine_eval": "stable",
                        "portfolio_lanes": "4:mixed:1:5,8:random:1:5",
                    }
                ]
            },
            1,
            0,
        )

        self.assertEqual("4:mixed:1:5,8:random:1:5", candidates[0]["portfolio_lanes"])

    def test_collect_candidate_signatures_includes_baseline_and_history(self) -> None:
        signatures = collect_candidate_signatures(
            {
                "request": {
                    "beta": 400,
                    "gamma": 40,
                    "theta": 5,
                    "zi_policy": "critical",
                    "critical_block_exhaustive_pct": 75,
                    "same_machine_eval": "stable",
                }
            },
            [
                {
                    "candidates": [
                        {
                            "candidate": {
                                "beta": 500,
                                "gamma": 40,
                                "theta": 5,
                                "zi_policy": "formula",
                                "zi_formula": "base * 1.1",
                                "critical_block_exhaustive_pct": 75,
                                "same_machine_eval": "stable",
                                "portfolio_lanes": "1:mixed:1:5",
                            }
                        }
                    ]
                }
            ],
        )

        self.assertTrue(any("zi_policy=critical" in item for item in signatures))
        self.assertTrue(any("portfolio_lanes=1:mixed:1:5" in item for item in signatures))

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
                "same_machine_eval": "stable",
            },
        }

        row = candidate_row("round_00", candidate, baseline)

        self.assertIn("|  | 1154 | -23 |", row)
        self.assertIn("| 15.7472 | -2.307 |", row)
        self.assertIn("| 50 |", row)
        self.assertIn("| stable |", row)

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
        self.assertIn("AWLS-SDST Portfolio Search-Control Notes", prompt)
        self.assertIn("AWLS-SDST zi Feature Notes", prompt)
        self.assertIn("setup_adjacent_ratio", prompt)
        self.assertIn("non-empty `portfolio_lanes`", prompt)
        self.assertIn("same_machine_eval=cpp-fast", prompt)


if __name__ == "__main__":
    unittest.main()
