from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.industrial_json_evaluator import _sha256, evaluate


def report_for(paths):
    return {
        "input": str(paths["instance"]),
        "solution": str(paths["solution"]),
        "horizon": 3840,
        "validator": "validate_batch_solution",
        "error_count": 0,
        "errors": [],
        "metrics": {
            "total_tasks": 2,
            "fully_scheduled_tasks": 2,
            "valid_full_tasks": 2,
            "completed_tasks_within_horizon": 1,
            "completed_weight_within_horizon": 12.3,
            "setup_count_positive": 4,
            "scheduled_ops": 5,
            "batch_group_count": 2,
            "error_count": 0,
        },
    }


def fake_validator(report, *, returncode=0, commands=None):
    def run(command, **kwargs):
        if commands is not None:
            commands.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        path = Path(command[command.index("--report") + 1])
        assert not path.exists()
        if report is not None:
            path.write_text(report if isinstance(report, str) else json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, b"not echoed", b"")
    return run


class IndustrialJsonEvaluatorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.paths = {
            "instance": root / "instance.json",
            "solution": root / "solution.json",
            "output": root / "metrics.json",
            "validator_python": root / "python.exe",
            "validator_script": root / "validate_batch_solution.py",
        }
        self.paths["instance"].write_text(json.dumps({"task": {"a": {}, "b": {}}, "config": {"max_output_horizon": 3840}}))
        self.paths["solution"].write_text('{"task": {}}')
        self.paths["validator_script"].write_text("# fixed validator fixture\n")
        self.paths["validator_script"].with_name("rl_relaxed_solver.py").write_text("# parser dependency fixture\n")

    def run_mocked(self, report, *, returncode=0):
        with patch("tools.industrial_json_evaluator.subprocess.run", side_effect=fake_validator(report, returncode=returncode)):
            return evaluate(**self.paths)

    def test_valid_report_preserves_metrics_and_audit_copy(self):
        report = report_for(self.paths)
        commands = []
        with patch("tools.industrial_json_evaluator.subprocess.run", side_effect=fake_validator(report, commands=commands)):
            result = evaluate(**self.paths)
        self.assertTrue(result["valid"])
        self.assertEqual(result["metrics"], {**report["metrics"], "grouped_batch_count": 0})
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(json.loads(Path(result["evidence"]["validator_report"]).read_text()), report)
        self.assertEqual(result["evidence"]["validator_sha256"], _sha256(self.paths["validator_script"]))
        self.assertEqual(json.loads(self.paths["output"].read_text()), result)
        command = commands[0]
        self.assertEqual(command[command.index("--max-errors") + 1], "30")
        self.assertNotIn("--force-path", command)
        self.assertFalse(any("horizon" in item or "current-time" in item or "maintenance" in item for item in command))

    def write_batch_fixture(self):
        source = {"task": {}, "config": {"max_output_horizon": 3840}}
        scheduled = {"task": {}, "diagnostics": {"grouped_batch_count": 999}}
        for task_id in ("a", "b"):
            source["task"][task_id] = {"process_path": {
                "chosen": {"process_list": {str(seq): {"is_batch_type": seq != 4} for seq in range(1, 5)}},
                "unused": {"process_list": {"4": {"is_batch_type": True}}},
            }}
            scheduled["task"][task_id] = {"process_path": {str(seq): {
                "path_id": "chosen", "temp_machine_id": f"M{seq}",
                "process_start_time": "2025/04/28 08:00:00",
                "process_finish_time": "2025/04/28 08:20:00",
            } for seq in range(1, 5)}}
        self.paths["instance"].write_text(json.dumps(source))
        self.paths["solution"].write_text(json.dumps(scheduled))
        return scheduled

    def test_grouped_batches_aggregate_actual_members_not_reported_counts(self):
        self.write_batch_fixture()
        report = report_for(self.paths)
        report["metrics"]["batch_group_count"] = 100
        report["metrics"]["grouped_batch_count"] = 888
        result = self.run_mocked(report)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["metrics"]["grouped_batch_count"], 3)
        self.assertEqual(result["metrics"]["batch_group_count"], 100)

    def test_grouped_batches_normalize_dates_and_exclude_singletons_and_ordinary_operations(self):
        scheduled = self.write_batch_fixture()
        operations = scheduled["task"]["b"]["process_path"]
        operations["1"]["process_start_time"] = "2025-04-28 08:00:00"
        operations["1"]["process_finish_time"] = "2025-04-28 08:20:00"
        operations["2"]["temp_machine_id"] = "another-machine"
        operations["3"]["process_finish_time"] = "2025/04/28 08:21:00"
        self.paths["solution"].write_text(json.dumps(scheduled))
        result = self.run_mocked(report_for(self.paths))
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["metrics"]["grouped_batch_count"], 1)

    def test_invalid_schedule_does_not_expose_grouped_batch_evidence(self):
        self.write_batch_fixture()
        report = report_for(self.paths)
        report["metrics"]["grouped_batch_count"] = 888
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                report["metrics"]["valid_full_tasks"] = 1
                result = self.run_mocked(report, returncode=returncode)
                self.assertFalse(result["valid"])
                self.assertNotIn("grouped_batch_count", result["metrics"])

    def test_singleton_batches_do_not_activate_grouping(self):
        scheduled = self.write_batch_fixture()
        for operation in scheduled["task"]["b"]["process_path"].values():
            operation["temp_machine_id"] += "-b"
        self.paths["solution"].write_text(json.dumps(scheduled))
        result = self.run_mocked(report_for(self.paths))
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["metrics"]["grouped_batch_count"], 0)

    def test_unreadable_batch_evidence_fails_closed(self):
        for field, value in (("path_id", "unknown"), ("process_start_time", "invalid")):
            with self.subTest(field=field):
                scheduled = self.write_batch_fixture()
                scheduled["task"]["a"]["process_path"]["1"][field] = value
                self.paths["solution"].write_text(json.dumps(scheduled))
                result = self.run_mocked(report_for(self.paths))
                self.assertFalse(result["valid"])
                self.assertNotIn("grouped_batch_count", result["metrics"])

    def test_nonzero_exit_invalid_even_with_clean_report(self):
        for code in (1, 2, -1):
            with self.subTest(code=code):
                self.assertFalse(self.run_mocked(report_for(self.paths), returncode=code)["valid"])

    def test_constraint_errors_are_preserved(self):
        report = report_for(self.paths)
        report.update(error_count=42, errors=["capacity exceeded"])
        report["metrics"]["error_count"] = 42
        result = self.run_mocked(report, returncode=1)
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_count"], 42)
        self.assertIn("capacity exceeded", result["errors"])
        self.assertEqual(result["metrics"], report["metrics"])

    def test_process_exception_fails_closed_without_echoing_output(self):
        for exception in (OSError("private process detail"), subprocess.TimeoutExpired("validator", 1)):
            with self.subTest(exception=type(exception).__name__):
                with patch("tools.industrial_json_evaluator.subprocess.run", side_effect=exception):
                    result = evaluate(**self.paths)
                self.assertFalse(result["valid"])
                self.assertNotIn("private process detail", json.dumps(result))

    def test_missing_or_malformed_report_rejected(self):
        self.paths["output"].with_suffix(".validator-report.json").write_text(json.dumps(report_for(self.paths)))
        for report in (None, "{broken", [], {"valid": True}):
            with self.subTest(report=report):
                self.assertFalse(self.run_mocked(report)["valid"])

    def test_incomplete_or_malformed_metrics_rejected(self):
        for field, value in (
            ("fully_scheduled_tasks", 1), ("valid_full_tasks", 1), ("total_tasks", 1),
            ("setup_count_positive", -1), ("completed_weight_within_horizon", float("nan")),
            ("completed_tasks_within_horizon", 3), ("error_count", None),
        ):
            with self.subTest(field=field):
                report = report_for(self.paths)
                report["metrics"][field] = value
                self.assertFalse(self.run_mocked(report)["valid"])

    def test_wrong_report_provenance_rejected(self):
        for field, value in (("input", "other.json"), ("solution", "other.json"), ("horizon", 4320), ("error_count", False)):
            with self.subTest(field=field):
                report = report_for(self.paths)
                report[field] = value
                self.assertFalse(self.run_mocked(report)["valid"])

    def test_each_call_uses_unique_caches_and_keeps_separate_report(self):
        commands = []
        with patch("tools.industrial_json_evaluator.subprocess.run", side_effect=fake_validator(report_for(self.paths), commands=commands)):
            first = evaluate(**self.paths)
            second = evaluate(**self.paths)
        for flag in ("--instance-cache", "--setup-db", "--report"):
            paths = [Path(command[command.index(flag) + 1]) for command in commands]
            self.assertNotEqual(paths[0], paths[1])
            self.assertFalse(any(path.exists() for path in paths))
        self.assertNotEqual(first["evidence"]["validator_report"], second["evidence"]["validator_report"])
        self.assertTrue(Path(first["evidence"]["validator_report"]).is_file())

    def test_hash_mismatch_rejects_before_launch(self):
        for field in ("expected_validator_sha256", "expected_validator_dependency_sha256"):
            with self.subTest(field=field):
                with patch("tools.industrial_json_evaluator.subprocess.run") as run:
                    result = evaluate(**self.paths, **{field: "0" * 64})
                run.assert_not_called()
                self.assertFalse(result["valid"])

    def test_input_mutation_during_validation_rejected(self):
        fake = fake_validator(report_for(self.paths))
        def run(command, **kwargs):
            result = fake(command, **kwargs)
            self.paths["solution"].write_text("changed")
            return result
        with patch("tools.industrial_json_evaluator.subprocess.run", side_effect=run):
            result = evaluate(**self.paths)
        self.assertFalse(result["valid"])
        self.assertIn("changed during validation", " ".join(result["errors"]))
        self.assertNotIn("grouped_batch_count", result["metrics"])

    def test_output_cannot_overwrite_instance(self):
        original = self.paths["instance"].read_bytes()
        self.paths["output"] = self.paths["instance"]
        with patch("tools.industrial_json_evaluator.subprocess.run") as run:
            result = evaluate(**self.paths)
        run.assert_not_called()
        self.assertFalse(result["valid"])
        self.assertEqual(self.paths["instance"].read_bytes(), original)

    def test_output_cannot_overwrite_validator_dependency(self):
        dependency = self.paths["validator_script"].with_name("rl_relaxed_solver.py")
        original = dependency.read_bytes()
        self.paths["output"] = dependency
        with patch("tools.industrial_json_evaluator.subprocess.run") as run:
            result = evaluate(**self.paths)
        run.assert_not_called()
        self.assertFalse(result["valid"])
        self.assertEqual(dependency.read_bytes(), original)

    def test_matching_pinned_hashes_allow_validation(self):
        script = self.paths["validator_script"]
        with patch("tools.industrial_json_evaluator.subprocess.run", side_effect=fake_validator(report_for(self.paths))):
            result = evaluate(
                **self.paths,
                expected_validator_sha256=_sha256(script),
                expected_validator_dependency_sha256=_sha256(script.with_name("rl_relaxed_solver.py")),
            )
        self.assertTrue(result["valid"])


VALIDATOR_PYTHON = Path("C:/Users/ASUS/AppData/Local/Programs/Python/Python312/python.exe")
VALIDATOR_SCRIPT = Path("F:/huawei_fjsp_llm/huawei_fjsp_llm/scripts/validate_batch_solution.py")


@unittest.skipUnless(
    VALIDATOR_PYTHON.is_file() and VALIDATOR_SCRIPT.is_file(),
    "local fixed industrial validator and its Python runtime are unavailable",
)
class IndustrialJsonEvaluatorIntegrationTests(unittest.TestCase):
    def test_real_validator_grouped_batch_evidence_requires_legal_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instance, solution = root / "instance.json", root / "solution.json"
            source = {
                "time": {"current_time": 480, "current_date_time": "2025-04-28 08:00:00"},
                "config": {"cal_threads": 1, "max_output_horizon": 600},
                "eqp": {"M": {"eqp_id": "M", "factory_info": "F", "eqp_down_interval": []}},
                "task": {}, "transition": {}, "setup": {},
            }
            scheduled = {"task": {}, "diagnostics": {"grouped_batch_count": 123}}
            for task_id in ("A", "B"):
                source["task"][task_id] = {
                    "earliest_ava_time": 480, "task_delivery_time": 600,
                    "task_priority": 1.0, "final_product_weight": 7.5,
                    "process_path": {"0": {"process_list": {"1": {
                        "is_batch_type": True, "batch_family": ["F"], "diff_factory_info": [],
                        "eqp_list": {"M": {"priority": 1, "process_time": 20, "curr_batch_size": 2}},
                    }}, "qtime_info": {}}},
                }
                separator = "/" if task_id == "A" else "-"
                date = separator.join(("2025", "04", "28"))
                scheduled["task"][task_id] = {"process_path": {"1": {
                    "temp_machine_id": "M", "path_id": "0",
                    "process_start_time": f"{date} 08:00:00",
                    "process_finish_time": f"{date} 08:20:00",
                }}}
            instance.write_text(json.dumps(source), encoding="utf-8")
            solution.write_text(json.dumps(scheduled), encoding="utf-8")
            arguments = dict(
                instance=instance, solution=solution, output=root / "metrics.json",
                validator_python=VALIDATOR_PYTHON, validator_script=VALIDATOR_SCRIPT,
                expected_validator_sha256=_sha256(VALIDATOR_SCRIPT),
                expected_validator_dependency_sha256=_sha256(VALIDATOR_SCRIPT.with_name("rl_relaxed_solver.py")),
            )
            result = evaluate(**arguments)
            self.assertTrue(result["valid"], result)
            self.assertEqual(result["metrics"]["grouped_batch_count"], 1)
            self.assertEqual(result["metrics"]["batch_group_count"], 1)

            source["task"]["A"]["process_path"]["0"]["process_list"]["1"]["eqp_list"]["M"]["curr_batch_size"] = 1
            instance.write_text(json.dumps(source), encoding="utf-8")
            invalid = evaluate(**arguments)
            self.assertFalse(invalid["valid"], invalid)
            self.assertNotIn("grouped_batch_count", invalid["metrics"])

    def test_real_validator_accepts_valid_schedule_and_rejects_wrong_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instance = root / "instance.json"
            solution = root / "solution.json"
            output = root / "metrics.json"
            instance.write_text(json.dumps({
                "time": {"current_time": 480, "current_date_time": "2025-04-28 08:00:00"},
                "config": {"cal_threads": 1, "max_output_horizon": 600},
                "eqp": {"M": {"eqp_id": "M", "factory_info": "F", "eqp_down_interval": []}},
                "task": {"J": {
                    "earliest_ava_time": 480,
                    "task_delivery_time": 600,
                    "task_priority": 1.0,
                    "final_product_weight": 7.5,
                    "process_path": {"0": {
                        "process_list": {"1": {
                            "is_batch_type": False,
                            "diff_factory_info": [],
                            "eqp_list": {"M": {"priority": 1, "process_time": 20, "curr_batch_size": 0}},
                        }},
                        "qtime_info": {},
                    }},
                }},
                "transition": {},
                "setup": {"('J', '0', '1')": {}},
            }), encoding="utf-8")
            payload = {"task": {"J": {"process_path": {"1": {
                "temp_machine_id": "M",
                "path_id": "0",
                "process_start_time": "2025/04/28 08:00:00",
                "process_finish_time": "2025/04/28 08:20:00",
            }}}}}
            solution.write_text(json.dumps(payload), encoding="utf-8")
            arguments = dict(
                instance=instance, solution=solution, output=output,
                validator_python=VALIDATOR_PYTHON, validator_script=VALIDATOR_SCRIPT,
                expected_validator_sha256=_sha256(VALIDATOR_SCRIPT),
                expected_validator_dependency_sha256=_sha256(VALIDATOR_SCRIPT.with_name("rl_relaxed_solver.py")),
            )

            valid = evaluate(**arguments)
            self.assertTrue(valid["valid"], valid)
            self.assertEqual(valid["evidence"]["validator_returncode"], 0)
            self.assertEqual(valid["metrics"]["completed_weight_within_horizon"], 7.5)
            self.assertEqual(valid["metrics"]["setup_count_positive"], 0)
            self.assertEqual(valid["metrics"]["fully_scheduled_tasks"], 1)
            self.assertEqual(valid["metrics"]["grouped_batch_count"], 0)
            valid_report = Path(valid["evidence"]["validator_report"])
            self.assertEqual(json.loads(valid_report.read_text(encoding="utf-8"))["error_count"], 0)

            payload["task"]["J"]["process_path"]["1"]["process_finish_time"] = "2025/04/28 08:19:00"
            solution.write_text(json.dumps(payload), encoding="utf-8")
            invalid = evaluate(**arguments)
            self.assertFalse(invalid["valid"])
            self.assertNotIn("grouped_batch_count", invalid["metrics"])
            self.assertEqual(invalid["evidence"]["validator_returncode"], 1)
            self.assertIn("duration mismatch", " ".join(invalid["errors"]))
            self.assertNotEqual(valid["evidence"]["validator_report"], invalid["evidence"]["validator_report"])
            self.assertTrue(valid_report.is_file())
            self.assertEqual(list(root.rglob("*.pkl")), [])
            self.assertEqual(list(root.rglob("*.sqlite")), [])


if __name__ == "__main__":
    unittest.main()
