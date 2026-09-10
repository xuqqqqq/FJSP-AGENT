from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.core.ledger import ExperimentLedger, ExperimentRecord
from harness_agent.core.models import ObjectiveSpec
from harness_agent.core.runner import (
    CORE_VALIDATION_ERRORS_KEY,
    VALIDATION_ERROR_EXCERPT_MAX_CHARS,
    HarnessRunner,
    RunSummary,
    validation_error_evidence,
    validation_summary,
)


NUMERIC_ERROR = "job:2: violates lower time bound; start=80 (09:20), lower=200 (11:20)"


def record(*, errors=None, error=None, paths=None, status="failed_validation"):
    return ExperimentRecord(
        experiment_id="test", task_id="test", round_index=0, instance_id="one", seed=0,
        status=status, valid=status == "success", objective_key=(float("-inf"),),
        metrics={} if errors is None else {CORE_VALIDATION_ERRORS_KEY: errors},
        paths=paths or {}, error=error,
    )


class ValidationErrorEvidenceTests(unittest.TestCase):
    def test_numeric_evidence_reaches_worker_through_full_feedback_chain(self):
        from harness_agent.agents.judgment import AgenticJudgment
        from harness_agent.context.packet import _project_current_round_repair
        from harness_agent.context.worker import _assignment_feedback, _repair_deliverables
        from harness_agent.orchestration.cycle import judgment_with_result_revalidation
        from harness_agent.orchestration.loop import current_round_repair_feedback, round_attempt_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metrics_path = root / "metrics.json"
            metrics_path.write_text(json.dumps({"errors": [NUMERIC_ERROR]}), encoding="utf-8")
            for stored in (None, [NUMERIC_ERROR]):
                with self.subTest(structured=stored is not None):
                    original = record(errors=stored, error=NUMERIC_ERROR, paths={"metrics": str(metrics_path)})
                    summary = RunSummary(total=1, valid=0, failed=1, best_experiment_id=None,
                                         best_metrics={}, validation_summary=validation_summary([original]))
                    judgment = judgment_with_result_revalidation(
                        judgment=AgenticJudgment(accepted=True, right=True, stage="test", issues=[], suggestions=[], checks={}),
                        smoke_summary=summary,
                    )
                    cycle = SimpleNamespace(summary=summary, agentic_judgment=judgment, worker_result=None)
                    attempt = round_attempt_payload(cycle, attempt_index=0, context_packet_path=root / "context.json")
                    current = current_round_repair_feedback(attempt_index=1, max_repair_attempts=19, previous_attempts=[attempt])
                    feedback = _assignment_feedback({"current_round_repair": _project_current_round_repair(current)}, attempt_index=1)
                    self.assertEqual([NUMERIC_ERROR], feedback["repair_targets"]["result_revalidation_top_errors"])
                    behaviors = [item["behavior"] for item in _repair_deliverables(feedback)]
                    self.assertTrue(any(NUMERIC_ERROR in behavior for behavior in behaviors))

    def test_structured_errors_preserve_numeric_counterexamples_and_exact_counts(self):
        first = record(errors=[NUMERIC_ERROR, "another error"], error="; ".join([NUMERIC_ERROR, "another error"]))
        second = replace(first, experiment_id="test2")
        summary = validation_summary([first, second])
        self.assertEqual({NUMERIC_ERROR: 2, "another error": 2},
                         {item["error"]: item["count"] for item in summary["top_errors"]})
        self.assertEqual({"failed_validation": 2}, summary["status_counts"])

    def test_legacy_artifact_recovers_errors_and_keeps_extra_consistency_evidence_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.json"
            errors = [NUMERIC_ERROR, "second original error"]
            path.write_text(json.dumps({"errors": errors}), encoding="utf-8")
            old = record(error="; ".join(errors), paths={"metrics": str(path)})
            self.assertEqual(errors, validation_error_evidence(old))
            suffix = "consistency mismatch; observed=9; expected=10"
            self.assertEqual([*errors, suffix], validation_error_evidence(replace(old, error=old.error + "; " + suffix)))

    def test_legacy_missing_changed_or_malformed_artifact_never_splits_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.json"
            old = record(error=NUMERIC_ERROR, paths={"metrics": str(path)})
            self.assertEqual([NUMERIC_ERROR], validation_error_evidence(old))
            for content in ("not json", json.dumps({"errors": ["different run"]}), "[]"):
                path.write_text(content, encoding="utf-8")
                self.assertEqual([NUMERIC_ERROR], validation_error_evidence(old))

    def test_runtime_failure_does_not_consume_stale_evaluator_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.json"
            path.write_text(json.dumps({"errors": ["old", "runtime detail"]}), encoding="utf-8")
            old = record(error="old; runtime detail", paths={"metrics": str(path)}, status="failed_runtime")
            self.assertEqual(["old; runtime detail"], validation_error_evidence(old))

    def test_reserved_evidence_survives_existing_sqlite_schema_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ExperimentLedger(Path(tmp) / "ledger.sqlite3")
            try:
                original = record(errors=[NUMERIC_ERROR], error=NUMERIC_ERROR)
                ledger.record(original)
                restored = ledger.list_records()[0]
                self.assertEqual([NUMERIC_ERROR], validation_error_evidence(restored))
                self.assertEqual(original.objective_key, restored.objective_key)
                self.assertEqual(original.valid, restored.valid)
            finally:
                ledger.close()

    def test_top_ten_is_bounded_without_turning_semicolons_into_error_categories(self):
        errors = [f"job{i}: lower violation; start={i}, lower=200" for i in range(12)]
        long_error = "prefix " + "x" * 4000 + "; start=80, lower=200"
        rows = [record(errors=errors)] + [record(errors=[long_error]) for _ in range(3)]
        summary = validation_summary(rows)
        self.assertEqual(10, len(summary["top_errors"]))
        highest = summary["top_errors"][0]
        self.assertEqual(3, highest["count"])
        self.assertLessEqual(len(highest["error"]), VALIDATION_ERROR_EXCERPT_MAX_CHARS)
        self.assertIn("start=80, lower=200", highest["error"])

    def test_runner_owns_reserved_errors_without_changing_validity_or_objective(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = HarnessRunner.__new__(HarnessRunner)
            runner.project_root = root
            runner.experiment_root = root / "experiments"
            runner.cancellation = None
            runner.contract = SimpleNamespace(
                task_id="test", resources={}, budget=SimpleNamespace(timeout_seconds=60),
                commands=SimpleNamespace(solver="stub solver", evaluator="stub evaluator"),
                objectives=[ObjectiveSpec(name="score", direction="maximize")],
            )
            work = runner.experiment_root / "round_000__one__seed_0"
            work.mkdir(parents=True)
            for valid, errors in ((True, []), (False, [NUMERIC_ERROR])):
                (work / "metrics.json").write_text(json.dumps({
                    "valid": valid, "errors": errors, "error_count": len(errors),
                    "metrics": {"score": 11, CORE_VALIDATION_ERRORS_KEY: ["forged evaluator metadata"]},
                }), encoding="utf-8")
                with patch("harness_agent.core.runner.run_shell_command", return_value=subprocess.CompletedProcess("stub", 0, "", "")), \
                     patch("harness_agent.core.runner.load_solver_evidence", return_value={}):
                    result = runner._run_one_to_record(0, "one", Path("instance.json"), 0)
                self.assertEqual(valid, result.valid)
                self.assertEqual((11.0,) if valid else (float("-inf"),), result.objective_key)
                self.assertEqual(errors, result.metrics[CORE_VALIDATION_ERRORS_KEY])
                self.assertEqual(errors, validation_error_evidence(result))
                self.assertEqual(11, result.metrics["score"])


if __name__ == "__main__":
    unittest.main()
