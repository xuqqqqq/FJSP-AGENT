from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.web.server import (
    _JOBS,
    browser_safe_json,
    create_job,
    deepseek_status_payload,
    latest_compatible_experience_memory,
    make_demo_examples,
    mark_stale_persisted_job_interrupted,
    run_job,
    scan_code_attempt_progress,
    summarize_code_evolution_progress,
    summarize_worker_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_jobs = dict(_JOBS)
        _JOBS.clear()
        self.saved_env = {key: os.environ.get(key) for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_FILE")}
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY_FILE", None)

    def tearDown(self) -> None:
        _JOBS.clear()
        _JOBS.update(self.saved_jobs)
        for key, value in self.saved_env.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_stale_running_job_is_marked_interrupted_without_losing_history(self) -> None:
        payload = {
            "id": "stale-job",
            "status": "running",
            "events": [],
            "summary": {"worker_summary": {"completed_round_count": 4}},
        }

        self.assertTrue(mark_stale_persisted_job_interrupted(payload))
        self.assertEqual("interrupted", payload["status"])
        self.assertEqual(4, payload["summary"]["worker_summary"]["completed_round_count"])

    def test_browser_safe_json_replaces_non_finite_numbers(self) -> None:
        safe = browser_safe_json({"key": [float("-inf"), float("inf"), 1.0]})

        self.assertEqual({"key": [None, None, 1.0]}, safe)
        json.dumps(safe, allow_nan=False)

    def test_deepseek_status_does_not_load_env_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
            with patch("harness_agent.web.server.PROJECT_ROOT", project_root), patch(
                "harness_agent.web.server.local_env_candidates", return_value=[]
            ), patch(
                "harness_agent.deepseek_client.local_env_candidates", return_value=[]
            ):
                status = deepseek_status_payload()

        self.assertFalse(status["configured"])
        self.assertFalse(status["env_example"]["loaded"])

    def test_demo_contains_only_platform_runtime_controls(self) -> None:
        demo = make_demo_examples()
        config = demo["config"]

        self.assertIn("FJSP-SDST", demo["requirement"]["text"])
        self.assertEqual(10, config["max_rounds"])
        self.assertEqual(120, config["worker_max_runtime_seconds"])
        self.assertEqual(1, config["promotion_repeats"])
        self.assertFalse(any(key.startswith("awls_") for key in config))
        for removed in ("solver", "run_mode", "evolution_mode", "baseline_source", "profile_mode"):
            self.assertNotIn(removed, config)

    def test_create_job_ignores_legacy_algorithm_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    solver="awls",
                    run_mode="awls_zi",
                    baseline_source="current_project",
                    awls_beta=999,
                ),
                output_root=Path(tmp),
            )

        self.assertEqual("opencode", job["config"]["coding_backend"])
        self.assertFalse(any(key.startswith("awls_") for key in job["config"]))
        self.assertNotIn("solver", job["config"])
        self.assertTrue(job["config"]["instance_profile"]["valid"])
        self.assertTrue(any("不调用内置求解算法" in item["message"] for item in job["events"]))

    def test_run_job_routes_to_agent_generated_worker_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(self.job_payload(max_rounds=3), output_root=Path(tmp))
            artifacts_dir = Path(tmp) / "artifacts"
            artifacts_dir.mkdir()
            report = artifacts_dir / "report.md"
            report.write_text("ok", encoding="utf-8")
            manifest = {
                "status": "ok",
                "baseline_key": [-120.0],
                "final_key": [-100.0],
                "round_count": 3,
                "promoted_rounds": 1,
                "improved": True,
                "baseline_summary": {"total": 1, "valid": 1, "failed": 0},
                "final_summary": {"total": 1, "valid": 1, "failed": 0, "best_metrics": {"makespan": 100}},
                "rounds": [],
                "artifacts": {"report": str(report)},
            }
            fake_worker = SimpleNamespace(
                capabilities=lambda: SimpleNamespace(supports_code_generation=True)
            )
            with patch("harness_agent.web.server.OpenCodeWorker", return_value=fake_worker), patch(
                "harness_agent.web.server.is_deepseek_configured", return_value=False
            ), patch("harness_agent.web.server.run_standard_worker_loop", return_value=manifest) as run_loop:
                run_job(job["id"])

        self.assertEqual("completed", job["status"])
        request = run_loop.call_args.args[0]
        self.assertEqual(3, request.iterations)
        self.assertEqual("examples/agent_generated_fjsp_solver.py", request.agent_generated_solver_path)
        self.assertTrue(request.apply_worker_changes)

    def test_run_job_marks_missing_valid_baseline_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(self.job_payload(max_rounds=3), output_root=Path(tmp))
            report = Path(tmp) / "baseline_failure_report.md"
            report.write_text("baseline failed", encoding="utf-8")
            manifest = {
                "status": "baseline_generation_failed",
                "terminal_reason": "judgment_rejected",
                "baseline_key": [float("-inf")],
                "final_key": [float("-inf")],
                "round_count": 0,
                "promoted_rounds": 0,
                "improved": False,
                "baseline_summary": {"total": 0, "valid": 0, "failed": 0},
                "final_summary": {"total": 0, "valid": 0, "failed": 0},
                "rounds": [],
                "artifacts": {"report": str(report)},
            }
            fake_worker = SimpleNamespace(capabilities=lambda: SimpleNamespace(supports_code_generation=True))
            with patch("harness_agent.web.server.OpenCodeWorker", return_value=fake_worker), patch(
                "harness_agent.web.server.is_deepseek_configured", return_value=False
            ), patch("harness_agent.web.server.run_standard_worker_loop", return_value=manifest):
                run_job(job["id"])

        self.assertEqual("failed", job["status"])
        self.assertEqual("judgment_rejected", job["error"])
        self.assertEqual("judgment_rejected", job["summary"]["terminal_reason"])
        self.assertEqual(str(report), job["artifacts"]["report"])

    def test_latest_memory_requires_same_variant_and_validated_method_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_dir = Path(tmp) / "previous"
            memory_path = previous_dir / "run" / "standard_worker_loop" / "worker_loop" / "experience_memory.json"
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text(
                json.dumps(
                    {
                        "memory_tiers": {
                            "validated_lessons": [
                                {"lesson_id": "validated", "method_package_id": "standard_fjsp_awls_hgtsa"}
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            _JOBS["previous"] = {
                "id": "previous",
                "status": "completed",
                "job_dir": str(previous_dir),
                "config": {"instance_profile": {"format": "standard_fjsp", "has_sequence_dependent_setup": False}},
            }
            current = {
                "id": "current",
                "config": {"instance_profile": {"format": "standard_fjsp", "has_sequence_dependent_setup": False}},
            }

            self.assertEqual(memory_path.resolve(), latest_compatible_experience_memory(current))
            current["config"]["instance_profile"]["has_sequence_dependent_setup"] = True
            self.assertIsNone(latest_compatible_experience_memory(current))

    def test_worker_manifest_summary_uses_promoted_final_metrics(self) -> None:
        summary = summarize_worker_manifest(
            {
                "baseline_key": [-120.0],
                "final_key": [-100.0],
                "round_count": 2,
                "promoted_rounds": 1,
                "improved": True,
                "baseline_summary": {"total": 1, "valid": 1, "failed": 0},
                "final_summary": {"total": 1, "valid": 1, "failed": 0, "best_metrics": {"makespan": 100}},
                "latest_candidate_summary": {
                    "total": 1,
                    "valid": 1,
                    "failed": 0,
                    "best_metrics": {"makespan": 110},
                },
                "rounds": [],
            }
        )

        self.assertEqual(100, summary["final_makespan"])
        self.assertEqual(110, summary["latest_makespan"])

    def test_worker_manifest_summary_keeps_valid_diagnostic_makespan_separate(self) -> None:
        summary = summarize_worker_manifest(
            {
                "status": "baseline_generation_failed",
                "baseline_key": [float("-inf")],
                "final_key": [float("-inf")],
                "round_count": 0,
                "baseline_summary": {"total": 0, "valid": 0},
                "final_summary": {"total": 0, "valid": 0},
                "baseline_generation": {
                    "in_round_repair": {
                        "attempts": [
                            {
                                "diagnostic_smoke": {
                                    "diagnostic_only": True,
                                    "passed": True,
                                    "summary": {
                                        "total": 1,
                                        "valid": 1,
                                        "failed": 0,
                                        "best_metrics": {"makespan": 2230},
                                    },
                                }
                            }
                        ]
                    }
                },
                "rounds": [],
            }
        )

        self.assertIsNone(summary["final_makespan"])
        self.assertEqual(2230, summary["diagnostic_makespan"])
        self.assertEqual(1, summary["diagnostic_valid"])
        self.assertFalse(summary["diagnostic_promotable"])

    def test_progress_summary_prefers_final_repair_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            round_dir = root / "round_000"
            repair_dir = round_dir / "repair_01"
            repair_dir.mkdir(parents=True)
            (repair_dir / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "candidate_key": [-95.0],
                        "harness": {"total": 1, "valid": 1, "best_metrics": {"makespan": 95}},
                        "decision": "promoted",
                    }
                ),
                encoding="utf-8",
            )

            progress = summarize_code_evolution_progress(root)

        self.assertEqual(1, progress["completed_round_count"])
        self.assertEqual(95, progress["best_makespan_so_far"])

    def test_progress_summary_exposes_baseline_diagnostic_before_any_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt_dir = root / "agent_generated_baseline" / "repair_001"
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "harness": {"total": 0, "valid": 0, "best_metrics": {}},
                        "diagnostic_smoke": {
                            "passed": True,
                            "summary": {
                                "total": 1,
                                "valid": 1,
                                "failed": 0,
                                "best_metrics": {"makespan": 2230},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            progress = summarize_code_evolution_progress(root)

        self.assertEqual(0, progress["completed_round_count"])
        self.assertEqual(2230, progress["diagnostic_makespan"])
        self.assertFalse(progress["diagnostic_promotable"])

    def test_attempt_progress_reports_diagnostic_makespan_when_ja_blocks_formal_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt_dir = root / "attempt"
            attempt_dir.mkdir()
            (attempt_dir / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "worker": {"status": "completed"},
                        "harness": {"total": 0, "valid": 0, "best_metrics": {}},
                        "diagnostic_smoke": {
                            "passed": True,
                            "summary": {
                                "total": 1,
                                "valid": 1,
                                "failed": 0,
                                "best_metrics": {"makespan": 2230},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "id": "diagnostic-job",
                "title": "diagnostic job",
                "status": "running",
                "created_at": "2026-07-16T00:00:00Z",
                "updated_at": "2026-07-16T00:00:00Z",
                "job_dir": str(root),
                "events": [],
                "summary": {},
                "artifacts": {},
                "error": None,
            }

            scan_code_attempt_progress(job, set(), attempt_dir, "baseline")

        message = job["events"][-1]["message"]
        self.assertIn("diagnostic_makespan=2230", message)
        self.assertIn("不参与 promotion", message)
        self.assertEqual("warning", job["events"][-1]["level"])

    def test_attempt_progress_reports_soft_acceptance_after_initial_ja_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt_dir = root / "attempt"
            attempt_dir.mkdir()
            judgment_path = attempt_dir / "agentic_judgment.json"
            judgment_path.write_text(
                json.dumps(
                    {
                        "accepted": False,
                        "issues": ["agent_generated_solver_self_check_incomplete"],
                        "checks": {},
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "id": "soft-accept-job",
                "title": "soft accept job",
                "status": "running",
                "created_at": "2026-07-16T00:00:00Z",
                "updated_at": "2026-07-16T00:00:00Z",
                "job_dir": str(root),
                "events": [],
                "summary": {},
                "artifacts": {},
                "error": None,
            }
            seen: set[str] = set()

            scan_code_attempt_progress(job, seen, attempt_dir, "baseline")
            judgment_path.write_text(
                json.dumps(
                    {
                        "accepted": True,
                        "issues": [],
                        "checks": {
                            "soft_accepted_by_diagnostic_smoke": {
                                "original_issues": ["agent_generated_solver_self_check_incomplete"],
                                "diagnostic_metrics": {"avg_makespan": 2352},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            scan_code_attempt_progress(job, seen, attempt_dir, "baseline")

        messages = [event["message"] for event in job["events"]]
        self.assertTrue(any("JA 初审未通过" in message for message in messages))
        self.assertTrue(any("JA 已将软性静态证据缺口降级为警告" in message for message in messages))
        self.assertTrue(any("diagnostic_makespan=2352" in message for message in messages))

    @staticmethod
    def job_payload(**overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "title": "web test",
            "requirement": {"name": "requirement.md", "text": "Solve FJSP."},
            "io": {"name": "io.md", "text": "Use standard schedule JSON."},
            "instance": {
                "name": "tiny.fjs",
                "text": (ROOT / "examples" / "standard_fjsp_tiny.fjs").read_text(encoding="utf-8"),
            },
            "best_known_csv": {"name": "best.csv", "text": "instance,best\ntiny,100"},
            "max_rounds": 1,
            "seeds": "0",
        }
        payload.update(overrides)
        return payload


if __name__ == "__main__":
    unittest.main()
