from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.incumbent_audit import build_incumbent_capability_audit


class IncumbentCapabilityAuditTests(unittest.TestCase):
    def test_extracts_search_controls_collections_loops_and_calls_without_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(
                """
def run_beam_construction(problem, beam_width):
    return problem


def solve(instance):
    beam_width = min(3, max(2, instance[\"machine_count\"] // 4 + 1))
    max_restarts = 4
    modes = [\"critical\", \"finish\", \"balance\", \"randomized\"]
    for mode in modes[:max_restarts]:
        run_beam_construction(instance, beam_width)


if __name__ == \"__main__\":
    solve({\"machine_count\": 10})
""".strip()
                + "\n",
                encoding="utf-8",
            )
            report = build_incumbent_capability_audit(
                {
                    "files": [
                        {
                            "relative_path": "examples/solver.py",
                            "sha256": "abc",
                            "snippet": "this field must not be copied into the audit",
                        }
                    ]
                },
                project_root=root,
            )

        self.assertIsNotNone(report)
        assert report is not None
        file_report = report["files"][0]
        configurations = {item["name"]: item for item in file_report["configurations"]}
        self.assertEqual(
            'min(3, max(2, instance["machine_count"] // 4 + 1))',
            configurations["beam_width"]["expression"],
        )
        self.assertEqual(4, configurations["modes"]["collection_size"])
        self.assertTrue(
            any(
                item["control"] == "modes[:max_restarts]"
                and "run_beam_construction" in item["calls"]
                for item in file_report["loops"]
            )
        )
        self.assertTrue(
            any(
                item["caller"] == "solve" and item["callee"] == "run_beam_construction"
                for item in file_report["call_edges"]
            )
        )
        self.assertTrue(file_report["has_main_guard"])
        self.assertNotIn("this field must not be copied", str(report))


if __name__ == "__main__":
    unittest.main()
