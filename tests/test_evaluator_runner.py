from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness_agent.core.evaluator import EvaluationResult
from harness_agent.core.ledger import ExperimentRecord
from harness_agent.core.models import ObjectiveSpec
from harness_agent.core.runner import (
    command_failure_message,
    load_solver_evidence,
    pareto_frontier,
    run_shell_command,
    validation_summary,
)


class EvaluatorRunnerTests(unittest.TestCase):
    def test_solver_evidence_keeps_bounded_runtime_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            solution = Path(tmp) / "solution.json"
            solution.write_text(
                json.dumps(
                    {
                        "source": "beam:finish",
                        "makespan": 2211,
                        "schedule": [{"operation": 0}],
                        "diagnostics": {
                            "candidate_runs": [{"source": "beam", "makespan": 2211}],
                            "search_counters": {"expanded_states": 300, "pruned_states": 297},
                        },
                    }
                ),
                encoding="utf-8",
            )

            evidence = load_solver_evidence(solution)

        self.assertEqual("beam:finish", evidence["selected_source"])
        self.assertEqual(2211, evidence["reported_makespan"])
        self.assertEqual(300, evidence["diagnostics"]["search_counters"]["expanded_states"])

    def test_oversized_diagnostics_keep_scalar_activation_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            solution = Path(tmp) / "solution.json"
            solution.write_text(
                json.dumps(
                    {
                        "source": "rule-table",
                        "diagnostics": {
                            "telemetry": {
                                "rule_table_status": "ok",
                                "rule_table_decisions": 387,
                                "fingerprints": ["f" * 2_000 for _ in range(200)],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            evidence = load_solver_evidence(solution)

        self.assertTrue(evidence["diagnostics_truncated"])
        telemetry = evidence["diagnostics"]["telemetry"]
        self.assertEqual("ok", telemetry["rule_table_status"])
        self.assertEqual(387, telemetry["rule_table_decisions"])
        self.assertLessEqual(len(json.dumps(evidence, ensure_ascii=False)), 32_000)

    def test_successful_shell_command_reaps_descendants(self) -> None:
        process = MagicMock()
        process.communicate.return_value = ("ok", "")
        process.returncode = 0
        with (
            patch("harness_agent.core.runner.subprocess.Popen", return_value=process),
            patch("harness_agent.core.runner.cleanup_process_descendants") as cleanup,
        ):
            result = run_shell_command("solver", cwd=Path.cwd(), timeout=5, check=False)

        self.assertEqual(0, result.returncode)
        cleanup.assert_called_once_with(process)

    def test_required_objective_metric_must_be_numeric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = Path(tmp) / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "valid": True,
                        "error_count": 0,
                        "errors": [],
                        "metrics": {"primary_score": "not-a-number"},
                    }
                ),
                encoding="utf-8",
            )
            result = EvaluationResult.from_metrics_file(
                metrics_path,
                [ObjectiveSpec(name="primary_score", direction="maximize")],
            )

            self.assertFalse(result.valid)
            self.assertEqual("failed_validation", result.status)
            self.assertIn("non-numeric objective metric: primary_score", result.errors)

    def test_pareto_frontier_filters_dominated_candidates(self) -> None:
        candidates = [
            {"candidate_id": "high_score", "complete": True, "objective_key": (10.0, -5.0), "metrics": {}},
            {"candidate_id": "fast", "complete": True, "objective_key": (9.0, -2.0), "metrics": {}},
            {"candidate_id": "dominated", "complete": True, "objective_key": (8.0, -6.0), "metrics": {}},
            {"candidate_id": "incomplete", "complete": False, "objective_key": (100.0, 100.0), "metrics": {}},
        ]

        frontier_ids = {item["candidate_id"] for item in pareto_frontier(candidates)}

        self.assertEqual({"high_score", "fast"}, frontier_ids)

    def test_validation_summary_counts_status_and_errors(self) -> None:
        records = [
            _record("ok", "success", True, None),
            _record("bad_a", "failed_validation", False, "missing required metric: score"),
            _record("bad_b", "failed_validation", False, "missing required metric: score"),
        ]

        summary = validation_summary(records)

        self.assertEqual({"failed_validation": 2, "success": 1}, summary["status_counts"])
        self.assertEqual(
            [{"error": "missing required metric: score", "count": 2}],
            summary["top_errors"],
        )

    def test_command_failure_message_includes_stderr_tail(self) -> None:
        stderr = "\n".join(
            [
                "Traceback (most recent call last):",
                '  File "solver.py", line 10, in <module>',
                "    main()",
                "TypeError: tuple indices must be integers or slices, not str",
            ]
        )

        message = command_failure_message("solver", 1, stderr)

        self.assertIn("solver command failed with exit code 1", message)
        self.assertIn("stderr_excerpt", message)
        self.assertIn("TypeError: tuple indices must be integers or slices, not str", message)

    def test_shell_timeout_kills_child_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            child_script = tmp_path / "child.py"
            parent_script = tmp_path / "parent.py"
            pid_path = tmp_path / "child.pid"

            child_script.write_text("import time\nwhile True:\n    time.sleep(1)\n", encoding="utf-8")
            parent_script.write_text(
                "\n".join(
                    [
                        "import subprocess",
                        "import sys",
                        "import time",
                        "from pathlib import Path",
                        "child = subprocess.Popen([sys.executable, sys.argv[1]])",
                        "Path(sys.argv[2]).write_text(str(child.pid), encoding='utf-8')",
                        "while True:",
                        "    time.sleep(1)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            command = f'"{sys.executable}" "{parent_script}" "{child_script}" "{pid_path}"'
            with self.assertRaises(subprocess.TimeoutExpired):
                run_shell_command(command, cwd=tmp_path, timeout=1, check=False)

            deadline = time.time() + 5
            while not pid_path.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(pid_path.exists(), "parent script did not record child pid before timeout")
            child_pid = int(pid_path.read_text(encoding="utf-8"))

            deadline = time.time() + 5
            while _process_exists(child_pid) and time.time() < deadline:
                time.sleep(0.1)
            self.assertFalse(_process_exists(child_pid), f"child process {child_pid} survived command timeout")


def _record(experiment_id: str, status: str, valid: bool, error: str | None) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        task_id="test",
        round_index=0,
        instance_id="instance",
        seed=0,
        status=status,
        valid=valid,
        objective_key=(1.0,) if valid else (float("-inf"),),
        metrics={"score": 1.0} if valid else {},
        paths={},
        error=error,
    )


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            capture_output=True,
            check=False,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    unittest.main()
