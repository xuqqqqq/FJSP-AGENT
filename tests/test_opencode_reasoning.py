from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_agent.web.opencode_reasoning import (
    MAX_REASONING_CHARS,
    merge_trace_records,
    read_reasoning_events,
    read_session_reasoning,
    reasoning_trace_record,
)
from harness_agent.web.server import (
    scan_opencode_main_trace,
    scan_opencode_worker_trace,
    scan_round_reflection_progress,
)


class OpenCodeReasoningTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "worker_loop"
        self.root.mkdir()
        self.started_at = int(time.time() * 1000) - 5000

    def database(self, runtime_root: Path) -> tuple[Path, sqlite3.Connection]:
        path = runtime_root / "data" / "opencode" / "opencode.db"
        path.parent.mkdir(parents=True)
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, session_id TEXT, "
            "time_created INTEGER, time_updated INTEGER, data TEXT)"
        )
        self.addCleanup(connection.close)
        return path, connection

    def add_part(
        self, connection: sqlite3.Connection, part_id: str = "part-live", *,
        session: str = "session-current", kind: str = "reasoning", age: int = 0,
        text: str = "Compare machine choices before editing.", ended: bool = False,
    ) -> None:
        timestamp = self.started_at + 1000 + age
        part = {"id": part_id, "type": kind, "text": text, "time": {"start": timestamp}}
        if ended:
            part["time"]["end"] = timestamp + 100
        connection.execute(
            "INSERT OR REPLACE INTO part VALUES (?, ?, ?, ?, ?)",
            (part_id, session, timestamp, timestamp + 100, json.dumps(part)),
        )
        connection.commit()

    def command_and_events(self, directory: Path, *, main: bool = False) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stem = "opencode_main" if main else "opencode"
        command = directory / f"{stem}_command.json"
        command.write_text(json.dumps(["opencode", "run", "--model", "qiming/test"]), encoding="utf-8")
        os.utime(command, (self.started_at / 1000, self.started_at / 1000))
        (directory / f"{stem}_events.jsonl").write_text(
            json.dumps({"type": "step_start", "sessionID": "session-current"}) + "\n",
            encoding="utf-8",
        )

    def test_reads_only_current_attempt_reasoning_in_current_session(self) -> None:
        path, connection = self.database(self.root / ".runtime")
        self.add_part(connection)
        self.add_part(connection, "other-session", session="session-other", text="Private other lane")
        self.add_part(connection, "old-attempt", age=-3000, text="Previous repair")
        self.add_part(connection, "tool", kind="tool", text="Private tool input")
        self.add_part(connection, "prompt", kind="text", text="Private prompt")

        records = read_session_reasoning(
            path, session_id="session-current", started_at=self.started_at, label="lane",
        )

        self.assertEqual(1, len(records))
        self.assertEqual("Compare machine choices before editing.", records[0]["text"])
        self.assertTrue(records[0]["streaming"])
        self.assertEqual("lane:reasoning:part-live", records[0]["id"])

    def test_unavailable_or_incompatible_storage_has_no_effect_on_monitoring(self) -> None:
        missing = self.root / "missing.db"
        self.assertEqual([], read_session_reasoning(missing, session_id="s", started_at=0, label="lane"))
        self.assertFalse(missing.exists())
        invalid = self.root / "invalid.db"
        invalid.write_text("not a database", encoding="utf-8")
        self.assertEqual([], read_session_reasoning(invalid, session_id="s", started_at=0, label="lane"))

    def test_completed_attempt_does_not_include_later_repair_in_reused_session(self) -> None:
        path, connection = self.database(self.root / ".runtime")
        self.add_part(connection)
        self.add_part(connection, "next-repair", age=2000, text="Later repair text")
        records = read_session_reasoning(
            path, session_id="session-current", started_at=self.started_at,
            finished_at=self.started_at + 1500, label="baseline",
        )
        self.assertEqual(["baseline:reasoning:part-live"], [record["id"] for record in records])

    def test_delta_stream_is_visible_before_end_and_final_snapshot_does_not_duplicate_text(self) -> None:
        path = self.root / "reasoning.jsonl"
        events = [
            {"type": "reasoning", "sessionID": "s", "timestamp": 100, "part": {
                "id": "p", "type": "reasoning", "text": "", "time": {"start": 100},
            }},
            {"type": "reasoning_delta", "sessionID": "s", "timestamp": 110, "partID": "p", "delta": "First "},
            {"type": "reasoning_delta", "sessionID": "other", "timestamp": 111, "partID": "p", "delta": "wrong lane"},
            {"type": "reasoning_delta", "sessionID": "s", "timestamp": 120, "partID": "p", "delta": "check"},
            {"type": "reasoning_delta", "sessionID": "s", "timestamp": 121, "partID": "missing", "delta": "orphan"},
        ]
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n{", encoding="utf-8")
        records = read_reasoning_events(path, session_id="s", started_at=100, label="lane")
        self.assertEqual("First check", records[0]["text"])
        self.assertEqual(120, records[0]["updated_at"])
        self.assertTrue(records[0]["streaming"])
        events.append({"type": "reasoning", "sessionID": "s", "timestamp": 130, "part": {
            "id": "p", "type": "reasoning", "text": "First check", "time": {"start": 100, "end": 130},
        }})
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        records = read_reasoning_events(path, session_id="s", started_at=100, label="lane")
        self.assertEqual(1, len(records))
        self.assertEqual("First check", records[0]["text"])
        self.assertFalse(records[0]["streaming"])

    def test_bundled_reasoning_plugin_does_not_inherit_external_plugins(self) -> None:
        from harness_agent.workers.opencode_worker import opencode_subprocess_environment

        path = self.root / "reasoning.jsonl"
        with patch("harness_agent.workers.opencode_worker.load_local_env"), patch.dict(
            os.environ, {"OPENCODE_CONFIG_CONTENT": json.dumps({"plugin": ["untrusted-plugin"]})},
        ):
            environment = opencode_subprocess_environment(reasoning_path=path, runtime_config={"snapshot": False})
        config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(str(path.resolve()), environment["ALGOFORGE_REASONING_EVENTS_PATH"])
        self.assertEqual(1, len(config["plugin"]))
        self.assertTrue(config["plugin"][0].endswith("/opencode_reasoning_plugin.mjs"))
        self.assertNotIn("untrusted-plugin", config["plugin"])

    def test_reasoning_updates_one_record_and_keeps_final_text(self) -> None:
        initial = reasoning_trace_record(
            {"id": "p", "text": "First", "time": {"start": 1}},
            label="lane", record_id="ignored", timestamp=2,
        )
        final = reasoning_trace_record(
            {"id": "p", "text": "First, then verify.", "time": {"start": 1, "end": 4}},
            label="lane", record_id="also-ignored", timestamp=5,
        )
        merged = merge_trace_records([initial], [final, initial], limit=800)
        self.assertEqual([final], merged)

    def test_large_reasoning_keeps_growing_tail_and_actual_length(self) -> None:
        text = "a" * MAX_REASONING_CHARS + "latest"
        record = reasoning_trace_record({"text": text}, label="lane", record_id="p", timestamp=2)
        self.assertEqual(MAX_REASONING_CHARS, len(record["text"]))
        self.assertEqual(len(text), record["text_length"])
        self.assertTrue(record["text"].endswith("latest"))

    def test_worker_observes_stream_before_cli_finishes_and_updates_in_place(self) -> None:
        attempt = self.root / "round_000" / "candidates" / "local"
        worker = attempt / "worker"
        self.command_and_events(worker)
        workspace = self.root / ".algoforge_opencode_session" / "local" / "workspace"
        workspace.mkdir(parents=True)
        (worker / "opencode_session.json").write_text(
            json.dumps({"materialized_workspace": str(workspace)}), encoding="utf-8",
        )
        _, connection = self.database(workspace.parent / ".opencode_runtime")
        self.add_part(connection, text="First live fragment")
        job = {"status": "running", "coding_agent_trace": []}
        seen: set[str] = set()
        with patch("harness_agent.web.server.write_job_status") as persist:
            scan_opencode_worker_trace(job, seen, attempt, "round_000 local")
            self.assertEqual("First live fragment", job["coding_agent_trace"][0]["text"])
            self.add_part(connection, text="First live fragment, now complete", ended=True)
            scan_opencode_worker_trace(job, seen, attempt, "round_000 local")
            scan_opencode_worker_trace(job, seen, attempt, "round_000 local")
            self.assertEqual(2, persist.call_count)

        self.assertEqual(1, len(job["coding_agent_trace"]))
        self.assertEqual("local", job["coding_agent_trace"][0]["candidate_id"])
        self.assertFalse(job["coding_agent_trace"][0]["streaming"])

    def test_main_observes_in_progress_reasoning(self) -> None:
        attempt = self.root / "round_000"
        main = attempt / "main_agent"
        self.command_and_events(main, main=True)
        _, connection = self.database(main / ".opencode_runtime")
        self.add_part(connection)
        job = {"status": "running", "main_agent_trace": []}
        with patch("harness_agent.web.server.write_job_status"):
            scan_opencode_main_trace(job, set(), attempt, "round_000")
        self.assertEqual("reasoning", job["main_agent_trace"][0]["kind"])
        self.assertTrue(job["main_agent_trace"][0]["streaming"])

    def test_reflection_updates_reasoning_from_plugin_before_cli_finishes(self) -> None:
        round_dir = self.root / "round_000"
        main = round_dir / "main_agent_reflection"
        self.command_and_events(main, main=True)
        stream_path = main / "opencode_main_reasoning.jsonl"
        initial = {"type": "reasoning", "sessionID": "session-current", "timestamp": self.started_at + 1000,
                   "part": {"id": "p", "type": "reasoning", "text": "", "time": {"start": self.started_at + 1000}}}
        delta = {"type": "reasoning_delta", "sessionID": "session-current", "timestamp": self.started_at + 1100,
                 "partID": "p", "delta": "Compare the candidates"}
        stream_path.write_text(json.dumps(initial) + "\n" + json.dumps(delta) + "\n", encoding="utf-8")
        job = {"status": "running", "main_agent_trace": []}
        seen: set[str] = set()
        with patch("harness_agent.web.server.write_job_status") as persist:
            scan_round_reflection_progress(job, seen, round_dir)
            self.assertTrue(job["main_agent_trace"][0]["streaming"])
            with stream_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**delta, "delta": ", then choose the next step.", "timestamp": self.started_at + 1200}) + "\n")
            scan_round_reflection_progress(job, seen, round_dir)
            scan_round_reflection_progress(job, seen, round_dir)
        self.assertEqual(2, persist.call_count)
        self.assertEqual(1, len(job["main_agent_trace"]))
        self.assertEqual("Compare the candidates, then choose the next step.", job["main_agent_trace"][0]["text"])

    def test_finished_attempt_marks_unfinished_reasoning_interrupted(self) -> None:
        attempt = self.root / "round_000"
        main = attempt / "main_agent"
        self.command_and_events(main, main=True)
        _, connection = self.database(main / ".opencode_runtime")
        self.add_part(connection)
        (attempt / "cycle_result.json").write_text("{}", encoding="utf-8")
        job = {"status": "running", "main_agent_trace": []}
        with patch("harness_agent.web.server.write_job_status"):
            scan_opencode_main_trace(job, set(), attempt, "round_000")
        self.assertFalse(job["main_agent_trace"][0]["streaming"])
        self.assertTrue(job["main_agent_trace"][0]["interrupted"])

    def test_cli_reasoning_event_and_storage_share_one_record(self) -> None:
        attempt = self.root / "round_000"
        main = attempt / "main_agent"
        self.command_and_events(main, main=True)
        _, connection = self.database(main / ".opencode_runtime")
        self.add_part(connection, ended=True)
        row = connection.execute("SELECT data FROM part WHERE id = 'part-live'").fetchone()
        with (main / "opencode_main_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "reasoning", "timestamp": self.started_at + 1200, "part": json.loads(row[0])}) + "\n")
        job = {"status": "running", "main_agent_trace": []}
        with patch("harness_agent.web.server.write_job_status"):
            scan_opencode_main_trace(job, set(), attempt, "round_000")
        self.assertEqual(1, len(job["main_agent_trace"]))
        self.assertFalse(job["main_agent_trace"][0]["streaming"])

    def test_worker_does_not_read_session_outside_task_root(self) -> None:
        attempt = self.root / "round_000" / "candidates" / "local"
        worker = attempt / "worker"
        self.command_and_events(worker)
        outside = self.root.parent / ".algoforge_opencode_session" / "outside" / "workspace"
        outside.mkdir(parents=True)
        (worker / "opencode_session.json").write_text(json.dumps({"materialized_workspace": str(outside)}), encoding="utf-8")
        job = {"status": "running", "coding_agent_trace": []}
        with patch("harness_agent.web.server.read_session_reasoning") as read:
            scan_opencode_worker_trace(job, set(), attempt, "round_000 local")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
