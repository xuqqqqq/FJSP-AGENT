from __future__ import annotations

import unittest

from harness_agent.awls_zi_evolution import is_better


class AwlsZiEvolutionTests(unittest.TestCase):
    def test_is_better_falls_back_to_makespan_without_gap(self) -> None:
        incumbent = {"avg_gap_pct": None, "avg_makespan": 2209.0, "invalid_run_count": 0}
        candidate = {"avg_gap_pct": None, "avg_makespan": 2195.0, "invalid_run_count": 0}

        self.assertTrue(is_better(candidate, incumbent))

    def test_is_better_rejects_invalid_makespan_improvement(self) -> None:
        incumbent = {"avg_gap_pct": None, "avg_makespan": 2209.0, "invalid_run_count": 0}
        candidate = {"avg_gap_pct": None, "avg_makespan": 2195.0, "invalid_run_count": 1}

        self.assertFalse(is_better(candidate, incumbent))


if __name__ == "__main__":
    unittest.main()
