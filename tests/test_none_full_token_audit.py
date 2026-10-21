"""Focused, offline regression tests for historical usage accounting."""

import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.audit_none_full_tokens import Audit, normalized, refresh_export, sums, total_relation, unique_pair_id, unique_pair_rows, verify_export


class NoneFullTokenAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.audit = Audit(self.root, self.root, self.root)
        self.audit.runs["U1"] = {
            "gaps": [], "duplicate_usage_records": 0, "request": {}, "job_id": None,
        }

    def add(self, mid, tokens, source="events.jsonl#L1"):
        return self.audit.add_call("U1", "session", mid, tokens, source, "coding_worker")

    def test_event_mirrors_with_same_session_and_message_count_once(self):
        event = {
            "type": "step_finish", "sessionID": "session",
            "part": {"id": "part", "messageID": "message", "tokens": {"input": 7, "output": 3, "total": 10}},
        }
        for name in ("events.jsonl", "semantic_mirror_events.jsonl"):
            p = self.root / name
            p.write_text(json.dumps(event) + "\n", encoding="utf-8")
            self.audit.events("U1", p)
        self.assertEqual(len(self.audit.calls), 1)
        self.assertEqual(sums(list(self.audit.calls.values()))["total_tokens"], 10)

    def test_distinct_retry_messages_are_both_counted(self):
        for mid in ("primary", "retry"):
            self.add(mid, {"input": 7, "output": 3, "total": 10})
        self.assertEqual(len(self.audit.calls), 2)
        self.assertEqual(sums(list(self.audit.calls.values()))["total_tokens"], 20)

    def test_opencode_total_does_not_add_cache_or_reasoning_twice(self):
        tokens = {"input": 100, "output": 20, "reasoning": 10, "cache": {"read": 50, "write": 0}, "total": 180}
        self.add("message", tokens)
        self.assertEqual(sums(list(self.audit.calls.values()))["total_tokens"], 180)
        self.assertEqual(total_relation(normalized(tokens)), "input+output+cache+reasoning")

    def test_api_total_already_contains_cached_prompt_and_reasoning_output(self):
        api = normalized({"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                          "prompt_cache_hit_tokens": 70, "reasoning_tokens": 20}, api=True)
        self.assertEqual(api["total_tokens"], 130)
        self.assertEqual(total_relation(api, api=True), "input+output")

    def test_missing_total_keeps_measured_subtotal_but_blocks_complete_total(self):
        self.add("known", {"input": 7, "output": 3, "total": 10})
        self.add("missing", {"input": 8, "output": 2})
        self.audit.complete()
        run = self.audit.runs["U1"]
        self.assertEqual(run["observed"]["total_tokens"], 10)
        self.assertIsNone(run["recorded_calls_exact_total_tokens"])
        self.assertIsNone(run["exact_job_total_tokens"])

    def test_empty_usage_artifact_is_not_a_zero_cost_call(self):
        self.audit.artifact_checks = [{"audit_run_id": "U1", "path": "main_agent_usage.json",
                                       "role": "main", "data": {"usage": {}, "event_count": 0}}]
        self.audit.finalize_usage_artifacts()
        self.audit.complete()
        self.assertEqual(len(self.audit.calls), 0)
        self.assertIsNone(self.audit.runs["U1"]["exact_job_total_tokens"])

    def test_semantic_retry_and_aggregate_total_are_not_added_together(self):
        primary = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
        retry = {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22}
        self.audit.artifact_checks = [{
            "audit_run_id": "U1", "path": "algorithm_semantic_review_usage.json", "role": "semantic_review",
            "data": {"primary_review": primary, "json_retry": retry,
                     "total": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35}},
        }]
        self.audit.finalize_usage_artifacts()
        self.assertEqual(len(self.audit.calls), 2)
        self.assertEqual(sums(list(self.audit.calls.values()))["total_tokens"], 35)

    def test_unique_pair_removes_report_aliases_and_preserves_other_experiments(self):
        first = unique_pair_id("full/v7/manifest.json", "none/v7/manifest.json")
        second = unique_pair_id("full/v8/manifest.json", "none/v8/manifest.json")
        aliases = [
            {"pair_id": "C001-01", "unique_pair_id": first},
            {"pair_id": "C002-01", "unique_pair_id": first},
            {"pair_id": "C003-01", "unique_pair_id": second},
        ]
        self.assertEqual(len(unique_pair_rows(aliases)), 2)
        self.assertNotEqual(first, second)
        self.assertEqual(first, unique_pair_id("full\\v7\\manifest.json", "none/v7/manifest.json"))

    def test_snapshot_refresh_preserves_totals_without_reading_databases(self):
        a = Audit(self.root, self.root, self.root / "export")
        for uid, mode in (("U1", "full"), ("U2", "none")):
            a.runs[uid] = {
                "audit_run_id": uid, "gaps": [], "duplicate_usage_records": 0, "request": {},
                "job_id": None, "comparisons": ["C001"], "family": "family", "mode": mode,
                "status": "ok", "instance": "instance", "manifest": f"runs/{mode}/manifest.json",
                "db_recovered_calls": 0, "run_purpose": "none_full_run",
            }
            a.add_call(uid, "session" + uid, "message", {"input": 7, "output": 3, "total": 10},
                       f"runs/{mode}/events.jsonl#L1", "main")
        a.pairs = [{
            "pair_id": "C001-01", "comparison_id": "C001", "family": "family", "status": "comparable",
            "protocol_valid": True, "source_comparison": "outputs/batch/report.json", "full_run": "U1", "none_run": "U2",
            "unique_pair_id": unique_pair_id("runs/full/manifest.json", "runs/none/manifest.json"),
        }]
        a.complete()
        with contextlib.redirect_stdout(io.StringIO()):
            a.write()
            before = (a.output / "calls.csv").read_bytes()
            with patch.object(Audit, "read_db", side_effect=AssertionError("Snapshot refresh must not read databases")):
                refresh_export(a.output)
            verify_export(a.output)
        self.assertEqual(before, (a.output / "calls.csv").read_bytes())


if __name__ == "__main__":
    unittest.main()
