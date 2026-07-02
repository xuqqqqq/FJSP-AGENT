from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from examples.standard_fjsp_evaluator import load_best_known, names_match


class StandardFjspEvaluatorTests(unittest.TestCase):
    def test_hudata_oddla_names_match_published_la_bounds(self) -> None:
        self.assertTrue(names_match("la20", "oddla20.txt"))
        self.assertTrue(names_match("oddla20.txt", "la20"))
        self.assertFalse(names_match("la19", "oddla20.txt"))

    def test_load_best_known_accepts_hudata_la_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bounds = Path(tmp) / "bounds.csv"
            bounds.write_text("Instance,Lower bound (LB),Best-known upper bound (UB/BKS)\nla20,857,997\n", encoding="utf-8")

            best = load_best_known(bounds, "oddla20")

        self.assertEqual(997.0, best)


if __name__ == "__main__":
    unittest.main()
