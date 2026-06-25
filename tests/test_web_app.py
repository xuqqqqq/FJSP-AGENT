from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from harness_agent.slot_manifest import write_selected_slot_manifest
from harness_agent.web_app import (
    _JOBS,
    create_job,
    make_demo_examples,
    run_job,
    scan_code_evolution_progress,
    slot_manifest_catalog_payload,
)


class WebAppTests(unittest.TestCase):
    def test_slot_mode_requires_explicit_slot_confirmation(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "slot confirmation smoke",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "evolution_mode": "slot",
            "selected_slot_id": "awls_zi_policy",
            "slot_user_confirmed": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "explicit user confirmation"):
                create_job(payload, output_root=Path(tmp))

    def test_slot_mode_records_user_confirmed_slot(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "slot confirmation smoke",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "evolution_mode": "slot",
            "selected_slot_id": "awls_zi_policy",
            "slot_user_confirmed": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(payload, output_root=Path(tmp))

        self.assertEqual("slot", job["config"]["evolution_mode"])
        self.assertEqual("awls_zi_policy", job["config"]["selected_slot_id"])
        self.assertTrue(job["config"]["slot_user_confirmed"])

    def test_selected_slot_manifest_confirms_only_requested_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slot_manifest.json"
            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=path,
                selected_slot_ids=["awls_zi_policy"],
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        confirmed = {slot["slot_id"]: slot["user_confirmed"] for slot in payload["slots"]}
        self.assertEqual("confirmed", payload["status"])
        self.assertFalse(payload["confirmation_required"])
        self.assertTrue(confirmed["awls_zi_policy"])
        self.assertFalse(confirmed["local_search_neighborhood_actions"])

    def test_slot_manifest_catalog_includes_resolved_block_advice(self) -> None:
        payload = slot_manifest_catalog_payload()

        slots = {slot["slot_id"]: slot for slot in payload["slots"]}
        self.assertEqual("draft_requires_user_confirmation", payload["status"])
        self.assertGreater(slots["awls_zi_policy"]["line_start"], 0)
        self.assertGreater(slots["local_search_neighborhood_actions"]["line_end"], slots["local_search_neighborhood_actions"]["line_start"])
        self.assertIn("def evolved_zi", slots["awls_zi_policy"]["original_content"])
        self.assertEqual("available", slots["awls_zi_policy"]["advisor"]["worker_support"])
        self.assertEqual("planned", slots["local_search_neighborhood_actions"]["advisor"]["worker_support"])

    def test_web_job_runs_demo_loop_from_submitted_documents(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "web smoke",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "best_known_csv": demo["best_known_csv"],
            "max_rounds": 2,
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
            self.assertEqual(2, finished["summary"]["round_summary"]["completed_round_count"])
            self.assertEqual(2, finished["summary"]["round_summary"]["reflection_count"])

    def test_code_progress_scanner_writes_visible_worker_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            job_dir = tmp_path / "job"
            job_dir.mkdir()
            worker_root = tmp_path / "worker_loop"
            round_dir = worker_root / "round_000"
            (worker_root / "baseline_harness").mkdir(parents=True)
            (worker_root / "baseline_harness" / "report.md").write_text("# report\n", encoding="utf-8")
            (round_dir / "worker").mkdir(parents=True)
            (round_dir / "context_packet.json").write_text("{}", encoding="utf-8")
            (round_dir / "worker" / "deepseek_code_edit_raw.json").write_text("{}", encoding="utf-8")
            (round_dir / "cycle_exception.txt").write_text(
                "Traceback\njson.decoder.JSONDecodeError: bad json\n",
                encoding="utf-8",
            )

            job = {
                "id": "job",
                "title": "code progress smoke",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "job_dir": str(job_dir),
                "config": {},
                "inputs": {},
                "events": [],
                "summary": {},
                "artifacts": {},
            }
            scan_code_evolution_progress(job, worker_root, set())

            messages = "\n".join(event["message"] for event in job["events"])
            self.assertIn("基线 evaluator 已完成", messages)
            self.assertIn("round_000 已生成上下文包", messages)
            self.assertIn("round_000 DeepSeek 已返回原始代码修改响应", messages)
            self.assertIn("round_000 执行异常", messages)
            self.assertTrue((job_dir / "web_job_status.json").exists())


if __name__ == "__main__":
    unittest.main()
