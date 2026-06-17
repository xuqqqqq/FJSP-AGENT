from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.web_app import _JOBS, create_job, make_demo_examples, run_job


class WebAppTests(unittest.TestCase):
    def test_web_job_runs_demo_loop_from_submitted_documents(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "web smoke",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "best_known_csv": demo["best_known_csv"],
            "max_rounds": 1,
            "seeds": "0",
            "solver": "portfolio",
            "profile_mode": "template",
            "strategy_candidates": 1,
            "portfolio_size": 4,
            "timeout_seconds": 30,
        }
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(payload, output_root=Path(tmp))
            run_job(job["id"])

            finished = _JOBS[job["id"]]
            self.assertEqual("completed", finished["status"])
            self.assertTrue(Path(finished["artifacts"]["report"]).exists())
            self.assertTrue(Path(finished["artifacts"]["standard_agent_report"]).exists())
            self.assertEqual(1, finished["summary"]["last_summary"]["valid"])


if __name__ == "__main__":
    unittest.main()
