from __future__ import annotations

import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch

from harness_agent.slot_manifest import write_selected_slot_manifest
from harness_agent.standard_worker_loop import SDST_ZI_FEATURES_CONSUMER_FORMULA
from harness_agent.web_app import (
    _JOBS,
    browser_safe_json,
    create_job,
    deepseek_status_payload,
    make_demo_examples,
    run_job,
    scan_awls_zi_progress,
    scan_code_evolution_progress,
    slot_manifest_catalog_payload,
    summarize_code_evolution_progress,
    summarize_worker_manifest,
)


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = {}
        for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_FILE", "FJSP_AGENT_ENV_FILE"):
            self._saved_env[key] = os.environ.get(key)
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_browser_safe_json_replaces_non_finite_numbers(self) -> None:
        payload = {
            "baseline_key": [float("-inf"), 1.0],
            "nested": {"bad": float("inf"), "ok": 2},
        }

        safe = browser_safe_json(payload)

        self.assertEqual({"baseline_key": [None, 1.0], "nested": {"bad": None, "ok": 2}}, safe)
        json.dumps(safe, allow_nan=False)

    def test_deepseek_status_reports_template_env_is_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
            with patch("harness_agent.web_app.PROJECT_ROOT", project_root), patch(
                "harness_agent.deepseek_client.local_env_candidates", return_value=[]
            ), patch(
                "harness_agent.web_app.local_env_candidates",
                return_value=[],
            ):
                status = deepseek_status_payload()

        self.assertFalse(status["configured"])
        self.assertIn(".env.example", status["diagnosis"])
        self.assertFalse(status["env_example"]["loaded"])
        self.assertIn("不会被自动加载", status["env_example"]["note"])

    def test_demo_examples_default_to_sdst_la20_agent_generated_settings(self) -> None:
        demo = make_demo_examples()

        self.assertEqual("fjsp_sdst_fattahi_requirement.md", demo["requirement"]["name"])
        self.assertEqual("fjsp_sdst_fattahi_io.md", demo["io"]["name"])
        self.assertIn("FJSP-SDST", demo["requirement"]["text"])
        self.assertIn("SDST-HUdata", demo["io"]["text"])
        self.assertIn("la20", demo["best_known_csv"]["text"].lower())

        config = demo["config"]
        self.assertEqual("standard_loop", config["run_mode"])
        self.assertEqual("agent_generated", config["baseline_source"])
        self.assertEqual("agent-generated", config["solver"])
        self.assertEqual("code", config["evolution_mode"])
        self.assertEqual("deepseek", config["profile_mode"])
        self.assertEqual(10, config["max_rounds"])
        self.assertEqual("0,1,2,3,4,5,6,7,8,9", config["seeds"])
        self.assertEqual("fixed", config["awls_time_policy"])
        self.assertEqual(1, config["awls_restarts"])
        self.assertEqual(1000, config["awls_cycles_per_restart"])
        self.assertEqual(1000000, config["awls_iterations"])
        self.assertEqual("random", config["awls_init"])
        self.assertEqual(400, config["awls_beta"])
        self.assertEqual(40, config["awls_gamma"])
        self.assertEqual(5, config["awls_theta"])
        self.assertEqual("critical", config["awls_zi_policy"])
        self.assertEqual(75, config["awls_critical_block_exhaustive_pct"])
        self.assertEqual("", config["awls_portfolio_lanes"])

    def test_worker_manifest_summary_uses_final_metrics(self) -> None:
        summary = summarize_worker_manifest(
            {
                "baseline_key": [-1366.0],
                "final_key": [-1277.0],
                "round_count": 2,
                "promoted_rounds": 1,
                "improved": True,
                "baseline_summary": {"total": 1, "valid": 1, "failed": 0},
                "final_summary": {
                    "total": 10,
                    "valid": 10,
                    "failed": 0,
                    "best_metrics": {"makespan": 1277.0, "gap_pct": 28.08},
                },
                "latest_candidate_summary": {
                    "total": 10,
                    "valid": 10,
                    "failed": 0,
                    "best_metrics": {"makespan": 1300.0, "gap_pct": 30.39},
                },
                "rounds": [],
            }
        )

        self.assertEqual(1277.0, summary["final_makespan"])
        self.assertEqual(28.08, summary["final_gap_pct"])
        self.assertEqual(10, summary["final_valid"])
        self.assertEqual(1300.0, summary["latest_makespan"])

    def test_worker_manifest_summary_reports_in_round_repair_stats(self) -> None:
        summary = summarize_worker_manifest(
            {
                "baseline_key": [-1366.0],
                "final_key": [-1277.0],
                "round_count": 2,
                "promoted_rounds": 1,
                "improved": True,
                "baseline_summary": {"total": 1, "valid": 1, "failed": 0},
                "final_summary": {
                    "total": 10,
                    "valid": 10,
                    "failed": 0,
                    "best_metrics": {"makespan": 1277.0, "gap_pct": 28.08},
                },
                "in_round_repair": {
                    "repair_round_count": 1,
                    "repair_attempt_count": 2,
                    "recovered_round_count": 1,
                    "final_rejected_after_repair": 0,
                },
                "rounds": [],
            }
        )

        self.assertEqual(2, summary["in_round_repair"]["repair_attempt_count"])
        self.assertEqual(1, summary["in_round_repair"]["recovered_round_count"])

    def test_code_evolution_progress_uses_final_repair_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            round_dir = root / "round_000"
            repair_dir = round_dir / "repair_001"
            round_dir.mkdir(parents=True)
            repair_dir.mkdir(parents=True)
            (round_dir / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "agentic_judgment": {"accepted": False},
                        "harness": {"total": 0, "valid": 0, "failed": 0, "best_metrics": {}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (repair_dir / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "agentic_judgment": {"accepted": True},
                        "harness": {
                            "total": 10,
                            "valid": 10,
                            "failed": 0,
                            "best_metrics": {"makespan": 1277.0, "gap_pct": 28.08},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = summarize_code_evolution_progress(root)

        self.assertEqual(10, summary["latest_valid"])
        self.assertEqual(1277.0, summary["latest_makespan"])
        self.assertEqual(1, summary["in_round_repair"]["repair_attempt_count"])
        self.assertEqual(1, summary["in_round_repair"]["recovered_round_count"])

    def test_deepseek_status_loads_explicit_env_file_without_returning_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "agent.env"
            env_file.write_text("DEEPSEEK_API_KEY=secret-for-test\n", encoding="utf-8")
            os.environ["FJSP_AGENT_ENV_FILE"] = str(env_file)

            with patch("harness_agent.deepseek_client.local_env_candidates", return_value=[env_file]), patch(
                "harness_agent.web_app.local_env_candidates",
                return_value=[env_file],
            ):
                status = deepseek_status_payload()

        self.assertTrue(status["configured"])
        self.assertTrue(status["api_key_env_present"])
        self.assertNotIn("secret-for-test", json.dumps(status, ensure_ascii=False))

    def test_deepseek_status_explains_missing_key_file(self) -> None:
        os.environ["DEEPSEEK_API_KEY_FILE"] = str(Path(tempfile.gettempdir()) / "missing_deepseek_key_for_test.txt")

        with patch("harness_agent.deepseek_client.local_env_candidates", return_value=[]), patch(
            "harness_agent.web_app.local_env_candidates",
            return_value=[],
        ):
            status = deepseek_status_payload()

        self.assertFalse(status["configured"])
        self.assertIn("文件不存在", status["diagnosis"])

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

    def test_slot_mode_accepts_sdst_neighborhood_slot(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "sdst neighborhood slot smoke",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "evolution_mode": "slot",
            "selected_slot_id": "awls_sdst_neighborhood_selection",
            "slot_user_confirmed": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(payload, output_root=Path(tmp))

        self.assertEqual("slot", job["config"]["evolution_mode"])
        self.assertEqual("awls_sdst_neighborhood_selection", job["config"]["selected_slot_id"])
        self.assertEqual("awls", job["config"]["solver"])

    def test_slot_mode_auto_selects_sdst_slot_and_awls_policy(self) -> None:
        demo = make_demo_examples()
        sdst_instance = "1 1 1\n1 1 0 1\n0\n"
        payload = {
            "title": "auto sdst slot smoke",
            "requirement": {"name": "req.md", "text": "FJSP-SDST with setup times; improve makespan."},
            "io": demo["io"],
            "instance": {"name": "oddtiny.txt", "text": sdst_instance},
            "evolution_mode": "slot",
            "selected_slot_id": "agent_auto",
            "slot_user_confirmed": True,
            "solver": "awls",
            "awls_zi_policy": "auto",
        }
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(payload, output_root=Path(tmp))

        self.assertEqual("slot", job["config"]["evolution_mode"])
        self.assertEqual("awls_sdst_move_selection", job["config"]["selected_slot_id"])
        self.assertEqual("agent_auto", job["config"]["slot_selection"]["mode"])
        self.assertEqual("critical", job["config"]["awls_zi_policy"])
        self.assertEqual(75, job["config"]["awls_critical_block_exhaustive_pct"])
        messages = "\n".join(event["message"] for event in job["events"])
        self.assertIn("代码槽选择", messages)
        self.assertIn("awls_sdst_move_selection", messages)

    def test_run_job_routes_sdst_slot_without_enabling_zi_slot_policy(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "sdst neighborhood slot route",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "evolution_mode": "slot",
            "selected_slot_id": "awls_sdst_neighborhood_selection",
            "slot_user_confirmed": True,
            "max_rounds": 1,
            "seeds": "0",
            "awls_zi_policy": "cpp",
        }
        captured = {}

        def fake_worker_loop(request):
            captured["request"] = request
            return {
                "status": "ok",
                "baseline_key": [-1010.0],
                "final_key": [-1010.0],
                "baseline_summary": {"valid": 1},
                "rounds": [],
                "round_count": 0,
                "promoted_rounds": 0,
                "improved": False,
                "artifacts": {"manifest": str(Path(request.output_dir) / "manifest.json")},
            }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("harness_agent.web_app.is_deepseek_configured", return_value=True), patch(
                "harness_agent.web_app.run_standard_worker_loop",
                side_effect=fake_worker_loop,
            ):
                job = create_job(payload, output_root=Path(tmp))
                run_job(job["id"])

            request = captured["request"]
            manifest = json.loads(Path(request.slot_manifest).read_text(encoding="utf-8"))

        self.assertEqual("cpp", request.awls_zi_policy)
        self.assertEqual("awls", request.solver)
        self.assertEqual("web_deepseek_slot_loop", request.experiment_id)
        self.assertIsNotNone(request.slot_manifest)
        confirmed = {slot["slot_id"]: slot["user_confirmed"] for slot in manifest["slots"]}
        self.assertTrue(confirmed["awls_sdst_neighborhood_selection"])
        self.assertFalse(confirmed["awls_zi_policy"])
        self.assertIn("awls_sdst_neighborhood_selection", request.hypothesis)

    def test_run_job_routes_auto_selected_sdst_slot_with_critical_policy(self) -> None:
        demo = make_demo_examples()
        sdst_instance = "1 1 1\n1 1 0 1\n0\n"
        payload = {
            "title": "auto sdst slot route",
            "requirement": {"name": "req.md", "text": "FJSP-SDST setup-aware neighborhood improvement."},
            "io": demo["io"],
            "instance": {"name": "oddtiny.txt", "text": sdst_instance},
            "evolution_mode": "slot",
            "selected_slot_id": "agent_auto",
            "slot_user_confirmed": True,
            "max_rounds": 1,
            "seeds": "0",
            "awls_zi_policy": "auto",
        }
        captured = {}

        def fake_worker_loop(request):
            captured["request"] = request
            return {
                "status": "ok",
                "baseline_key": [-10.0],
                "final_key": [-10.0],
                "baseline_summary": {"valid": 1},
                "rounds": [],
                "round_count": 0,
                "promoted_rounds": 0,
                "improved": False,
                "artifacts": {"manifest": str(Path(request.output_dir) / "manifest.json")},
            }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("harness_agent.web_app.is_deepseek_configured", return_value=True), patch(
                "harness_agent.web_app.run_standard_worker_loop",
                side_effect=fake_worker_loop,
            ):
                job = create_job(payload, output_root=Path(tmp))
                run_job(job["id"])

            request = captured["request"]
            manifest = json.loads(Path(request.slot_manifest).read_text(encoding="utf-8"))

        self.assertEqual("critical", request.awls_zi_policy)
        self.assertEqual(75, request.awls_critical_block_exhaustive_pct)
        confirmed = {slot["slot_id"]: slot["user_confirmed"] for slot in manifest["slots"]}
        self.assertTrue(confirmed["awls_sdst_move_selection"])
        self.assertFalse(confirmed["awls_zi_policy"])

    def test_run_job_routes_sdst_zi_features_slot_with_formula_consumer(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "sdst zi feature slot route",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "evolution_mode": "slot",
            "selected_slot_id": "awls_sdst_zi_features",
            "slot_user_confirmed": True,
            "max_rounds": 1,
            "seeds": "0",
        }
        captured = {}

        def fake_worker_loop(request):
            captured["request"] = request
            return {
                "status": "ok",
                "baseline_key": [-1010.0],
                "final_key": [-1010.0],
                "baseline_summary": {"valid": 1},
                "rounds": [],
                "round_count": 0,
                "promoted_rounds": 0,
                "improved": False,
                "artifacts": {"manifest": str(Path(request.output_dir) / "manifest.json")},
            }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("harness_agent.web_app.is_deepseek_configured", return_value=True), patch(
                "harness_agent.web_app.run_standard_worker_loop",
                side_effect=fake_worker_loop,
            ):
                job = create_job(payload, output_root=Path(tmp))
                run_job(job["id"])

            request = captured["request"]
            manifest = json.loads(Path(request.slot_manifest).read_text(encoding="utf-8"))

        self.assertEqual("formula", request.awls_zi_policy)
        self.assertEqual(SDST_ZI_FEATURES_CONSUMER_FORMULA, request.awls_zi_formula)
        self.assertIn("formula zi policy", request.hypothesis)
        confirmed = {slot["slot_id"]: slot["user_confirmed"] for slot in manifest["slots"]}
        self.assertTrue(confirmed["awls_sdst_zi_features"])
        self.assertFalse(confirmed["awls_zi_policy"])

    def test_run_job_routes_agent_generated_baseline_to_code_worker(self) -> None:
        demo = make_demo_examples()
        payload = {
            "title": "agent baseline route",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": demo["instance"],
            "evolution_mode": "code",
            "baseline_source": "agent_generated",
            "max_rounds": 1,
            "seeds": "0",
            "solver": "portfolio",
        }
        captured = {}

        def fake_worker_loop(request):
            captured["request"] = request
            return {
                "status": "ok",
                "baseline_source": "agent_generated",
                "baseline_key": [-20.0],
                "final_key": [-20.0],
                "baseline_summary": {"valid": 1},
                "baseline_generation": {"status": "ok", "worker_changed_files": ["examples/agent_generated_fjsp_solver.py"]},
                "rounds": [],
                "round_count": 0,
                "promoted_rounds": 0,
                "improved": False,
                "artifacts": {"manifest": str(Path(request.output_dir) / "manifest.json")},
            }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("harness_agent.web_app.is_deepseek_configured", return_value=True), patch(
                "harness_agent.web_app.run_standard_worker_loop",
                side_effect=fake_worker_loop,
            ):
                job = create_job(payload, output_root=Path(tmp))
                run_job(job["id"])

            request = captured["request"]
            finished = _JOBS[job["id"]]

        self.assertEqual("agent_generated", request.baseline_source)
        self.assertEqual("examples/agent_generated_fjsp_solver.py", request.agent_generated_solver_path)
        self.assertEqual("portfolio", request.solver)
        self.assertIn("baseline_source is agent_generated", request.hypothesis)
        self.assertEqual("completed", finished["status"])

    def test_create_job_times_budget_from_actual_instance_content(self) -> None:
        demo = make_demo_examples()
        fake_dp15 = "20 10 10\n" + "\n".join("15 " + " ".join(["1 1 3"] * 15) for _ in range(20)) + "\n"
        payload = {
            "title": "misnamed dp15",
            "requirement": demo["requirement"],
            "io": demo["io"],
            "instance": {"name": "fjsp.brandimarte.Mk01.m6j10c3.txt", "text": fake_dp15},
            "run_mode": "awls_zi",
            "awls_time_policy": "scaled",
            "awls_time_limit_sec": 30,
            "awls_zi_candidates": 2,
            "seeds": "0,1",
        }
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(payload, output_root=Path(tmp))

        profile = job["config"]["instance_profile"]
        self.assertEqual(20, profile["job_count"])
        self.assertEqual(10, profile["machine_count"])
        self.assertEqual(300, profile["operation_count"])
        self.assertTrue(profile["filename_shape_mismatch"])
        self.assertEqual(600.0, job["config"]["effective_awls_time_limit_sec"])
        self.assertEqual(2400.0, job["config"]["estimated_awls_zi_eval_sec_per_round"])
        messages = "\n".join(event["message"] for event in job["events"])
        self.assertIn("按实际算例内容解析规模", messages)
        self.assertIn("文件名形状与实际内容不一致", messages)
        self.assertIn("每个算例/seed/候选=600s", messages)

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
        self.assertFalse(confirmed["awls_sdst_neighborhood_selection"])

    def test_selected_slot_manifest_can_confirm_sdst_neighborhood_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slot_manifest.json"
            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=path,
                selected_slot_ids=["awls_sdst_neighborhood_selection"],
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        confirmed = {slot["slot_id"]: slot["user_confirmed"] for slot in payload["slots"]}
        self.assertFalse(confirmed["awls_zi_policy"])
        self.assertTrue(confirmed["awls_sdst_neighborhood_selection"])

    def test_slot_manifest_catalog_includes_resolved_block_advice(self) -> None:
        payload = slot_manifest_catalog_payload()

        slots = {slot["slot_id"]: slot for slot in payload["slots"]}
        self.assertEqual("draft_requires_user_confirmation", payload["status"])
        self.assertGreater(slots["awls_zi_policy"]["line_start"], 0)
        self.assertGreater(slots["local_search_neighborhood_actions"]["line_end"], slots["local_search_neighborhood_actions"]["line_start"])
        self.assertIn("def evolved_zi", slots["awls_zi_policy"]["original_content"])
        self.assertEqual("available", slots["awls_zi_policy"]["advisor"]["worker_support"])
        self.assertEqual("available", slots["local_search_neighborhood_actions"]["advisor"]["worker_support"])
        self.assertGreater(slots["awls_sdst_neighborhood_selection"]["line_start"], 0)
        self.assertIn("consider_same", slots["awls_sdst_neighborhood_selection"]["original_content"])
        self.assertEqual("available", slots["awls_sdst_neighborhood_selection"]["advisor"]["worker_support"])

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

    def test_code_progress_summary_tracks_best_so_far_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worker_root = Path(tmp) / "worker_loop"
            first = worker_root / "round_000"
            second = worker_root / "round_001"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "harness": {
                            "total": 10,
                            "valid": 10,
                            "failed": 0,
                            "best_metrics": {"makespan": 1200.0, "gap_pct": 20.36},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (second / "cycle_result.json").write_text(
                json.dumps(
                    {
                        "harness": {
                            "total": 10,
                            "valid": 10,
                            "failed": 0,
                            "best_metrics": {"makespan": 1138.0, "gap_pct": 14.14},
                        }
                    }
                ),
                encoding="utf-8",
            )

            progress = summarize_code_evolution_progress(worker_root)

        self.assertEqual(2, progress["completed_round_count"])
        self.assertEqual(1138.0, progress["best_makespan_so_far"])
        self.assertEqual(14.14, progress["best_gap_pct_so_far"])
        self.assertEqual(1138.0, progress["latest_makespan"])

    def test_awls_zi_progress_scanner_writes_visible_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            job_dir = tmp_path / "job"
            job_dir.mkdir()
            evolution_root = tmp_path / "awls_zi_evolution"
            round_dir = evolution_root / "round_00"
            candidate_dir = round_dir / "candidates" / "r00_formula_load"
            (evolution_root / "baseline_cpp").mkdir(parents=True)
            round_dir.mkdir(parents=True)
            candidate_dir.mkdir(parents=True)
            (evolution_root / "baseline_cpp" / "summary.json").write_text(
                json.dumps({"aggregate": {"valid_instance_count": 1, "avg_makespan": 2209.0}}),
                encoding="utf-8",
            )
            (round_dir / "deepseek_prompt.md").write_text("# prompt\n", encoding="utf-8")
            (round_dir / "deepseek_raw_response.json").write_text("{}", encoding="utf-8")
            (round_dir / "normalized_candidates.json").write_text(
                json.dumps([{"name": "r00_formula_load"}]),
                encoding="utf-8",
            )
            (candidate_dir / "summary.json").write_text(
                json.dumps({"aggregate": {"valid_instance_count": 1, "invalid_run_count": 0, "avg_makespan": 2204.0}}),
                encoding="utf-8",
            )
            (evolution_root / "zi_evolution_summary.json").write_text(
                json.dumps(
                    {
                        "rounds": [{"round_index": 0}],
                        "best": {"name": "r00_formula_load", "avg_makespan": 2204.0},
                    }
                ),
                encoding="utf-8",
            )
            (evolution_root / "zi_evolution_report.md").write_text("# report\n", encoding="utf-8")

            job = {
                "id": "job",
                "title": "awls zi progress smoke",
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
            scan_awls_zi_progress(job, evolution_root, set())

            messages = "\n".join(event["message"] for event in job["events"])
            self.assertIn("AWLS-ZI 基线已完成", messages)
            self.assertIn("round_00 DeepSeek 已返回候选参数/规则", messages)
            self.assertIn("round_00 候选已归一化", messages)
            self.assertIn("round_00 候选评测完成", messages)
            self.assertIn("AWLS-ZI 摘要已更新", messages)
            self.assertTrue((job_dir / "web_job_status.json").exists())


if __name__ == "__main__":
    unittest.main()
