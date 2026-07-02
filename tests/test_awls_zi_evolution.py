from __future__ import annotations

import unittest

from harness_agent.awls_zi_evolution import is_better, normalize_candidates, normalize_portfolio_lanes


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


if __name__ == "__main__":
    unittest.main()
