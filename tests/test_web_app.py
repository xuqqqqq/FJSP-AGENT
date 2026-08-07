from __future__ import annotations

import base64
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.web.server import (
    _JOB_CANCELLATIONS,
    _JOBS,
    _ROUND_GATES,
    agent_status_snapshot,
    browser_safe_json,
    compact_lane_development_states,
    create_project_resource,
    create_job,
    deepseek_status_payload,
    frontend_document,
    inspect_instance_profile,
    is_frontend_route,
    latest_compatible_experience_memory,
    make_demo_examples,
    mark_stale_persisted_job_interrupted,
    preview_starter_project,
    read_resource,
    resource_catalog,
    resume_job,
    run_job,
    scan_opencode_main_trace,
    scan_opencode_worker_trace,
    scan_code_attempt_progress,
    scan_round_reflection_progress,
    service_health_payload,
    summarize_code_evolution_progress,
    summarize_worker_manifest,
    stop_job,
    submit_round_intervention,
    worker_attempt_dirs,
    WebRoundInterventionGate,
)


ROOT = Path(__file__).resolve().parents[1]


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_jobs = dict(_JOBS)
        self.saved_cancellations = dict(_JOB_CANCELLATIONS)
        self.saved_round_gates = dict(_ROUND_GATES)
        _JOBS.clear()
        _JOB_CANCELLATIONS.clear()
        _ROUND_GATES.clear()
        self.saved_env = {
            key: os.environ.get(key)
            for key in (
                "DEEPSEEK_API_KEY",
                "DEEPSEEK_API_KEY_FILE",
                "DEEPSEEK_BASE_URL",
                "OPENAI_API_KEY",
                "OPENAI_API_KEY_FILE",
                "OPENCODE_MODEL",
                "OPENCODE_OPENAI_COMPAT_FROM_DEEPSEEK",
            )
        }
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY_FILE", None)
        os.environ.pop("DEEPSEEK_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY_FILE", None)
        os.environ.pop("OPENCODE_MODEL", None)
        os.environ.pop("OPENCODE_OPENAI_COMPAT_FROM_DEEPSEEK", None)

    def tearDown(self) -> None:
        _JOBS.clear()
        _JOBS.update(self.saved_jobs)
        _JOB_CANCELLATIONS.clear()
        _JOB_CANCELLATIONS.update(self.saved_cancellations)
        _ROUND_GATES.clear()
        _ROUND_GATES.update(self.saved_round_gates)
        for key, value in self.saved_env.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_compact_lane_development_states_exposes_checkpoint_progress(self) -> None:
        compacted = compact_lane_development_states(
            {
                "lane-01": {
                    "candidate_id": "lane-01",
                    "method_family": "coupled_local_search",
                    "method_package_id": "standard_fjsp_awls_hgtsa",
                    "checkpoint_worktree": "C:/run/lane-01",
                    "objective_key": [-2241.0],
                    "track": "direct_evidence",
                    "stage": 2,
                    "verified_components": ["progress_decoder", "alternative_machine_neighborhood"],
                    "session_id": "ses-01",
                    "session_status": "continued",
                    "event_stream_status": "nonzero",
                    "last_failure": None,
                    "last_update_round": 3,
                }
            }
        )

        self.assertEqual("direct_evidence", compacted["lane-01"]["track"])
        self.assertEqual(2, compacted["lane-01"]["stage"])
        self.assertEqual("ses-01", compacted["lane-01"]["session_id"])

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

    def test_service_health_does_not_require_provider_credentials(self) -> None:
        runtime = {
            "python_executable": r"C:\runtime\python.exe",
            "python_version": "3.12.9",
            "ortools_available": True,
            "ortools_version": "9.15.6755",
            "ortools_error": None,
        }
        with patch("harness_agent.web.server.OpenCodeWorker") as worker_cls, patch(
            "harness_agent.web.server.is_deepseek_configured", return_value=False
        ), patch("harness_agent.web.server.solver_runtime_status", return_value=runtime):
            worker_cls.return_value.capabilities.return_value.supports_code_generation = True
            payload = service_health_payload()

        self.assertEqual("ok", payload["status"])
        self.assertEqual("algoforge-web", payload["service"])
        self.assertTrue(payload["opencode_available"])
        self.assertFalse(payload["provider_configured"])
        self.assertEqual(runtime, payload["solver_runtime"])

    def test_create_job_extracts_provided_project_and_keeps_archive_out_of_status(self) -> None:
        archive = self.zip_payload(
            {
                "fjsp-project/solver.py": "print('entry')\n",
                "fjsp-project/fjsp/solver.py": "def improve():\n    return 1\n",
                "fjsp-project/evaluate.py": "raise RuntimeError('untrusted')\n",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    starter_project=archive,
                    starter_solver_entrypoint="solver.py",
                    starter_solver_command=(
                        "python solver.py {instance} --output {solution} --seed {seed}"
                    ),
                    starter_target_file="fjsp/solver.py",
                ),
                output_root=Path(tmp),
            )
            project_root = Path(job["inputs"]["starter_project"])

            self.assertTrue((project_root / "solver.py").is_file())
            self.assertTrue((project_root / "fjsp" / "solver.py").is_file())
            self.assertFalse((project_root / "fjsp-project").exists())
            self.assertEqual("provided_project", job["config"]["baseline_mode"])
            self.assertEqual("fjsp/solver.py", job["config"]["starter_target_file"])
            self.assertEqual("fjsp-project", job["config"]["starter_project"]["stripped_root"])
            self.assertNotIn(archive["base64"], json.dumps(job))

    def test_create_job_defaults_main_planning_mode_to_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(self.job_payload(), output_root=Path(tmp))

        self.assertEqual("fast", job["config"]["main_planning_mode"])

    def test_create_job_rejects_zip_path_traversal_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                create_job(
                    self.job_payload(
                        starter_project=self.zip_payload({"../outside.py": "bad"}),
                    ),
                    output_root=Path(tmp),
                )

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            info = zipfile.ZipInfo("project/link.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "solver.py")
            archive.writestr("project/solver.py", "print('ok')")
        symlink_payload = {
            "name": "symlink.zip",
            "base64": base64.b64encode(stream.getvalue()).decode("ascii"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "symlink"):
                create_job(
                    self.job_payload(starter_project=symlink_payload),
                    output_root=Path(tmp),
                )

    def test_create_job_rejects_excessive_zip_entries_and_expanded_size(self) -> None:
        archive = self.zip_payload({"project/solver.py": "print('ok')", "project/model.py": "x = 1"})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "harness_agent.web.server.MAX_STARTER_ARCHIVE_ENTRIES", 1
        ):
            with self.assertRaisesRegex(ValueError, "more than 1 entries"):
                create_job(
                    self.job_payload(starter_project=archive),
                    output_root=Path(tmp),
                )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "harness_agent.web.server.MAX_STARTER_EXPANDED_BYTES", 4
        ):
            with self.assertRaisesRegex(ValueError, "expands beyond 4 bytes"):
                create_job(
                    self.job_payload(
                        starter_project=self.zip_payload({"project/solver.py": "12345"}),
                    ),
                    output_root=Path(tmp),
                )

    def test_preview_starter_project_lists_files_and_validates_contract(self) -> None:
        archive = self.zip_payload(
            {
                "fjsp-project/solver.py": "print('entry')\n",
                "fjsp-project/fjsp/search.py": "def improve():\n    return 1\n",
                "fjsp-project/README.md": "# Solver\n",
            }
        )

        preview = preview_starter_project(
            {
                "starter_project": archive,
                "starter_solver_entrypoint": "solver.py",
                "starter_target_file": "fjsp/search.py",
                "starter_solver_command": "python solver.py {instance} --output {solution} --seed {seed}",
            }
        )

        self.assertTrue(preview["can_continue"])
        self.assertEqual("fjsp-project", preview["stripped_root"])
        self.assertEqual(
            ["fjsp/search.py", "README.md", "solver.py"],
            [item["path"] for item in preview["files"]],
        )
        self.assertTrue(preview["contract"]["entrypoint_exists"])
        self.assertTrue(preview["contract"]["target_exists"])
        self.assertTrue(preview["contract"]["syntax_checks"]["entrypoint"]["valid"])

        invalid = preview_starter_project(
            {
                "starter_project": archive,
                "starter_solver_entrypoint": "missing.py",
                "starter_target_file": "fjsp/search.py",
                "starter_solver_command": "python missing.py {instance} --output {solution} --seed {seed}",
            }
        )
        self.assertFalse(invalid["can_continue"])
        self.assertTrue(any("Solver 入口不存在" in item for item in invalid["errors"]))

    def test_preview_detects_wrapper_algorithm_target_and_real_cli(self) -> None:
        archive = self.zip_payload(
            {
                "project/solver.py": (
                    "import argparse\n"
                    "from fjsp import solve\n"
                    "p = argparse.ArgumentParser()\n"
                    "p.add_argument('instance')\n"
                    "p.add_argument('--output', required=True)\n"
                    "p.add_argument('--seed', type=int, default=0)\n"
                ),
                "project/fjsp/__init__.py": "from .solver import solve\n",
                "project/fjsp/solver.py": "def solve(problem, seed=0):\n    return problem\n",
                "project/instances/dp/07a.fjs": "1 1 1\n1 1 1 1\n",
                "project/instances/dp/18a.fjs": "1 1 1\n1 1 1 1\n",
            }
        )

        preview = preview_starter_project(
            {
                "starter_project": archive,
                "starter_solver_entrypoint": "solver.py",
                "starter_target_file": "solver.py",
                "starter_solver_command": (
                    "python solver.py --input {instance} --output {solution} --seed {seed} "
                    "--time-limit-sec {solver_time_limit_seconds}"
                ),
                "auto_detect_contract": True,
            }
        )

        self.assertTrue(preview["can_continue"])
        self.assertEqual("fjsp/solver.py", preview["contract"]["target_file"])
        self.assertEqual(
            "python solver.py {instance} --output {solution} --seed {seed}",
            preview["contract"]["solver_command"],
        )
        detection = preview["contract"]["detection"]
        self.assertTrue(detection["entrypoint_is_wrapper"])
        self.assertEqual("fjsp/solver.py", detection["recommended_target_file"])
        self.assertEqual(
            ["instances/dp/07a.fjs", "instances/dp/18a.fjs"],
            [item["path"] for item in preview["project_instances"]],
        )

        rejected = preview_starter_project(
            {
                "starter_project": archive,
                "starter_solver_entrypoint": "solver.py",
                "starter_target_file": "solver.py",
                "starter_solver_command": (
                    "python solver.py --input {instance} --output {solution} --seed {seed} "
                    "--time-limit-sec {solver_time_limit_seconds}"
                ),
            }
        )
        self.assertFalse(rejected["can_continue"])
        self.assertTrue(any("CLI 包装器" in item for item in rejected["errors"]))
        self.assertTrue(any("argparse 未声明" in item for item in rejected["errors"]))

    def test_preview_and_profile_recognize_compound_min_time_lag_txt_instance(self) -> None:
        instance_name = "fjsp.barnes.mt10c1.m11j10c2.mitfjsp.seed20260714.txt"
        archive = self.zip_payload(
            {
                "project/solver.py": "def solve(problem, seed=0):\n    return problem\n",
                f"project/instances/{instance_name}": (
                    ROOT / "examples" / "fjsp_min_time_lag_tiny.mitfjsp"
                ).read_text(encoding="utf-8"),
            }
        )

        preview = preview_starter_project(
            {
                "starter_project": archive,
                "starter_solver_entrypoint": "solver.py",
                "starter_target_file": "solver.py",
                "starter_solver_command": "python solver.py {instance} --output {solution} --seed {seed}",
            }
        )
        profile = inspect_instance_profile(ROOT / "examples" / "fjsp_min_time_lag_tiny.mitfjsp")

        self.assertEqual(
            [f"instances/{instance_name}"],
            [item["path"] for item in preview["project_instances"]],
        )
        self.assertTrue(profile["valid"])
        self.assertEqual("fjsp_min_time_lag", profile["variant"])
        self.assertTrue(profile["has_minimum_time_lags"])
        self.assertEqual(1, profile["min_time_lag_constraint_count"])
        self.assertEqual(["minimum_time_lag"], profile["variant_features"])

    def test_create_job_rejects_wrapper_as_algorithm_target(self) -> None:
        archive = self.zip_payload(
            {
                "project/solver.py": "from fjsp import solve\n",
                "project/fjsp/__init__.py": "from .solver import solve\n",
                "project/fjsp/solver.py": "def solve(problem, seed=0):\n    return problem\n",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "CLI wrapper"):
                create_job(
                    self.job_payload(
                        starter_project=archive,
                        starter_solver_entrypoint="solver.py",
                        starter_target_file="solver.py",
                        starter_solver_command="python solver.py {instance} --output {solution} --seed {seed}",
                    ),
                    output_root=Path(tmp),
                )

    def test_create_job_copies_project_instances_into_fixed_core_inputs(self) -> None:
        archive = self.zip_payload(
            {
                "project/solver.py": "from fjsp.solver import solve\n",
                "project/fjsp/solver.py": "def solve(problem, seed=0):\n    return problem\n",
                "project/instances/dp/07a.fjs": "1 1 1\n1 1 1 1\n",
                "project/instances/dp/18a.fjs": "1 1 1\n1 1 1 2\n",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    starter_project=archive,
                    starter_solver_entrypoint="solver.py",
                    starter_target_file="fjsp/solver.py",
                    starter_solver_command="python solver.py {instance} --output {solution} --seed {seed}",
                    starter_use_project_instances=True,
                ),
                output_root=Path(tmp),
            )

            copied = [Path(path) for path in job["inputs"]["starter_instances"]]
            self.assertEqual(2, len(copied))
            self.assertTrue(all(path.is_file() for path in copied))
            self.assertTrue(all("provided_instances" in path.parts for path in copied))
            self.assertTrue(job["config"]["starter_use_project_instances"])
            self.assertEqual(
                ["instances/dp/07a.fjs", "instances/dp/18a.fjs"],
                job["config"]["starter_project_instances"],
            )
            self.assertEqual(2, job["config"]["instance_profile"]["instance_count"])

    def test_frontend_exposes_provided_project_contract(self) -> None:
        static = ROOT / "harness_agent" / "web" / "static"
        index = (static / "index.html").read_text(encoding="utf-8")
        import_page = (static / "project_import.html").read_text(encoding="utf-8")
        review_page = (static / "project_review.html").read_text(encoding="utf-8")
        setup_page = (static / "project_setup.html").read_text(encoding="utf-8")
        project_script = (static / "project.js").read_text(encoding="utf-8")

        for element_id in (
            "starter-project-file",
            "starter-solver-entrypoint",
            "starter-solver-command",
            "starter-target-file",
            "starter-use-project-instances",
        ):
            self.assertIn(f'id="{element_id}"', import_page)
            self.assertNotIn(f'id="{element_id}"', index)
        self.assertIn('href="/projects/import"', index)
        self.assertIn('id="review-file-tree"', review_page)
        self.assertIn('id="review-entrypoint"', review_page)
        self.assertIn('id="review-target-file"', review_page)
        self.assertIn('id="review-detected-target"', review_page)
        self.assertIn('id="review-project-instances"', review_page)
        self.assertIn('id="review-command"', review_page)
        self.assertIn('id="confirm-starter-project-review"', review_page)
        self.assertNotIn('id="job-form"', review_page)
        self.assertIn('id="project-job-form"', setup_page)
        self.assertNotIn('id="job-form"', setup_page)
        self.assertNotIn('id="requirement-text"', setup_page)
        self.assertNotIn('id="io-text"', setup_page)
        self.assertNotIn('id="provided-task-form-host"', setup_page)
        self.assertEqual(1, index.count('id="job-form"'))
        instance_field_start = index.index('id="task-instance-field"')
        instance_field_end = index.index("</label>", instance_field_start)
        instance_field = index[instance_field_start:instance_field_end]
        self.assertIn('id="instance-file"', instance_field)
        self.assertIn('id="instance-text"', instance_field)
        self.assertNotIn('id="title"', instance_field)
        self.assertIn("file.arrayBuffer()", project_script)
        self.assertIn('fetch("/api/starter-projects/preview"', project_script)
        self.assertIn("starter_project: draft.project", project_script)
        self.assertIn("starter_use_project_instances", project_script)
        self.assertIn("indexedDB.open", project_script)
        self.assertIn('window.location.assign("/projects/import/review")', project_script)
        self.assertIn('window.location.assign("/projects/import/setup")', project_script)

    def test_project_intake_uses_standalone_browser_routes(self) -> None:
        static = ROOT / "harness_agent" / "web" / "static"
        index = (static / "index.html").read_text(encoding="utf-8")
        pages = {
            "project_import.html": (static / "project_import.html").read_text(encoding="utf-8"),
            "project_review.html": (static / "project_review.html").read_text(encoding="utf-8"),
            "project_setup.html": (static / "project_setup.html").read_text(encoding="utf-8"),
        }

        self.assertNotIn('id="view-import-project"', index)
        self.assertNotIn('id="view-import-project-review"', index)
        self.assertNotIn('id="view-import-project-setup"', index)
        for filename, page in pages.items():
            with self.subTest(filename=filename):
                self.assertIn('class="project-document"', page)
                self.assertNotIn('class="app-shell"', page)
                self.assertNotIn('class="side-rail"', page)
                self.assertNotIn('class="inspector"', page)

    def test_workbench_scopes_audit_and_artifact_components_to_their_views(self) -> None:
        static = ROOT / "harness_agent" / "web" / "static"
        index = (static / "index.html").read_text(encoding="utf-8")
        script = (static / "app.js").read_text(encoding="utf-8")
        style = (static / "style.css").read_text(encoding="utf-8")

        worker_start = index.index('id="view-worker"')
        worker_end = index.index('id="view-experiments"', worker_start)
        worker_view = index[worker_start:worker_end]
        self.assertIn('aria-label="回合检查器"', worker_view)
        self.assertIn('id="inspector-title"', worker_view)

        artifacts_start = index.index('id="view-artifacts"')
        artifacts_end = index.index('id="view-models"', artifacts_start)
        artifacts_view = index[artifacts_start:artifacts_end]
        self.assertIn('id="artifact-list"', artifacts_view)
        self.assertIn('id="artifact-preview"', artifacts_view)
        self.assertIn('id="artifact-path"', artifacts_view)
        self.assertEqual(1, index.count('id="artifact-preview"'))
        self.assertEqual(1, index.count('aria-label="回合检查器"'))
        self.assertIn('id="mobile-view-select"', index)
        self.assertIn('artifacts: "报告与产物"', script)
        self.assertIn('setActiveView("artifacts")', script)
        self.assertIn("function viewFromUrl()", script)
        self.assertIn("window.history[method]", script)
        self.assertIn('window.addEventListener("popstate"', script)
        self.assertIn("loadReport: initialView === \"artifacts\"", script)
        self.assertIn("window.scrollTo(0, 0)", script)
        self.assertIn('$("mobile-view-select").addEventListener("change"', script)
        self.assertGreaterEqual(style.count("#view-worker .split-view"), 2)

    def test_server_maps_project_routes_to_distinct_html_documents(self) -> None:
        for path in (
            "/",
            "/projects/import",
            "/projects/import/",
            "/projects/import/review",
            "/projects/import/setup",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_frontend_route(path))
        for path in ("/api/jobs", "/static/app.js", "/projects/unknown"):
            with self.subTest(path=path):
                self.assertFalse(is_frontend_route(path))
        self.assertEqual("index.html", frontend_document("/"))
        self.assertEqual("project_import.html", frontend_document("/projects/import"))
        self.assertEqual("project_review.html", frontend_document("/projects/import/review"))
        self.assertEqual("project_setup.html", frontend_document("/projects/import/setup"))
        self.assertIsNone(frontend_document("/api/jobs"))

    def test_stop_job_cancels_active_task_and_preserves_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(self.job_payload(), output_root=Path(tmp))
            job["status"] = "running"
            job["artifacts"]["incumbent"] = str(Path(tmp) / "incumbent.py")
            from harness_agent.core.cancellation import CancellationToken

            cancellation = CancellationToken()
            _JOB_CANCELLATIONS[job["id"]] = cancellation

            result = stop_job(job["id"])

        self.assertTrue(result["accepted"])
        self.assertEqual("stopping", job["status"])
        self.assertTrue(cancellation.cancelled)
        self.assertIn("incumbent", job["artifacts"])
        self.assertTrue(any("用户请求停止任务" in item["message"] for item in job["events"]))

    def test_frontend_exposes_real_stop_endpoint_control(self) -> None:
        html = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="stop-job"', html)
        self.assertIn('/stop`', script)
        self.assertIn('method: "POST"', script)
        self.assertIn('className = "history-stop-button"', script)
        self.assertIn("async function stopJob(jobId", script)

    def test_frontend_exposes_completed_job_resume_control(self) -> None:
        html = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="resume-job"', html)
        self.assertIn('id="resume-additional-rounds"', html)
        self.assertIn('id="resume-dialog"', html)
        self.assertIn('/resume`', script)
        self.assertIn("async function resumeCurrentJob()", script)
        self.assertIn("function openResumeDialog(job)", script)
        self.assertIn('className = "history-resume-button"', script)

    def test_resume_job_appends_round_budget_and_preserves_same_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(self.job_payload(max_rounds=3), output_root=Path(tmp))
            job["status"] = "completed"
            loop_result_path = (
                Path(job["job_dir"])
                / "run"
                / "standard_worker_loop"
                / "worker_loop"
                / "loop_result.json"
            )
            incumbent = loop_result_path.parent / "round_002" / "candidate_worktree"
            incumbent.mkdir(parents=True)
            loop_result_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "baseline_key": [-2257.0],
                        "final_key": [-2195.0],
                        "final_worktree": str(incumbent),
                        "baseline_source": "agent_generated",
                        "baseline_generation": {"status": "ok", "source": "agent_generated"},
                        "baseline_summary": {
                            "total": 1,
                            "valid": 1,
                            "failed": 0,
                            "best_experiment_id": "baseline",
                            "best_metrics": {"makespan": 2257.0},
                        },
                        "rounds": [
                            {
                                "round_index": index,
                                "decision": "promoted" if index == 2 else "rolled_back",
                                "candidate_key": [-2195.0],
                                "incumbent_key_after": [-2195.0],
                            }
                            for index in range(3)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            job["artifacts"]["loop_result"] = str(loop_result_path)

            with patch("harness_agent.web.server.start_job") as start:
                result = resume_job(job["id"], {"additional_rounds": 4})

        self.assertTrue(result["accepted"])
        self.assertEqual("queued", job["status"])
        self.assertEqual(7, job["config"]["max_rounds"])
        self.assertEqual(3, job["continuation"]["starting_round_index"])
        self.assertEqual(4, job["continuation"]["additional_rounds"])
        self.assertEqual(job["id"], result["job"]["id"])
        start.assert_called_once_with(job["id"])

    def test_resource_catalog_exposes_only_project_skills_and_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            skill = project_root / ".codex" / "skills" / "demo" / "SKILL.md"
            knowledge = project_root / "knowledge" / "references" / "beam.md"
            outside = project_root / "private.txt"
            skill.parent.mkdir(parents=True)
            knowledge.parent.mkdir(parents=True)
            skill.write_text("---\nname: demo-worker\ndescription: Demo skill.\n---\n# Demo\n", encoding="utf-8")
            knowledge.write_text("# Beam Search\n\nBounded constructive search.\n", encoding="utf-8")
            outside.write_text("must not be exposed", encoding="utf-8")

            with patch("harness_agent.web.server.PROJECT_ROOT", project_root):
                catalog = resource_catalog()
                skill_content = read_resource("skill:demo/SKILL.md")
                knowledge_content = read_resource("knowledge:references/beam.md")
                with self.assertRaisesRegex(ValueError, "invalid resource id"):
                    read_resource("knowledge:../private.txt")

        self.assertEqual({"skill": 1, "knowledge": 1}, catalog["counts"])
        self.assertEqual("demo-worker", skill_content["title"])
        self.assertIn("# Beam Search", knowledge_content["content"])
        self.assertFalse(any(item["path"] == "private.txt" for item in catalog["resources"]))

    def test_frontend_exposes_skill_and_knowledge_browser(self) -> None:
        html = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        for resource_id in (
            'id="resource-kind-skill"',
            'id="resource-kind-knowledge"',
            'id="resource-search"',
            'id="resource-list"',
            'id="resource-preview-content"',
            'id="create-skill"',
            'id="create-knowledge"',
            'id="resource-dialog"',
            'id="resource-dialog-form"',
        ):
            self.assertIn(resource_id, html)
        self.assertIn('fetch("/api/resources")', script)
        self.assertIn('method: "POST"', script)
        self.assertIn("async function submitResourceDialog", script)
        self.assertIn("/api/resources/content?id=", script)
        self.assertIn("async function selectResource", script)

    def test_create_project_skill_is_validated_and_registered_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            manifest_path = project_root / "domain_packs" / "standard_fjsp" / "domain_pack.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "family_id": "standard_fjsp",
                        "method_families": [{"family_id": "constructive_search"}],
                        "worker_implementation_skills": [],
                        "knowledge": {"tagged_cards": {}, "knowledge_query": {"tag_descriptions": {}}},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "category": "skill",
                "name": "fjsp-demo-worker",
                "title": "FJSP 演示执行器",
                "description": "实现受控演示搜索。用于 Main 选择 constructive_search 时。",
                "body": "## 工作流\n\n1. 读取 assignment。\n2. 实现并验证。",
                "default_prompt": "实现当前 assignment。",
                "method_families": ["constructive_search"],
                "activation_tags": ["construction"],
                "register": True,
            }

            with patch("harness_agent.web.server.PROJECT_ROOT", project_root):
                created = create_project_resource(payload)
                with self.assertRaisesRegex(ValueError, "Skill 已存在"):
                    create_project_resource(payload)
                with self.assertRaisesRegex(ValueError, "Skill 名称"):
                    create_project_resource({**payload, "name": "../Bad Skill"})

            skill_path = project_root / ".codex" / "skills" / "fjsp-demo-worker" / "SKILL.md"
            agent_path = skill_path.parent / "agents" / "openai.yaml"
            self.assertTrue(skill_path.is_file())
            self.assertTrue(agent_path.is_file())
            self.assertEqual("skill:fjsp-demo-worker/SKILL.md", created["id"])
            self.assertTrue(created["registered"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("fjsp-demo-worker", manifest["worker_implementation_skills"][0]["skill_id"])
            self.assertEqual(["constructive_search"], manifest["worker_implementation_skills"][0]["method_families"])

    def test_create_knowledge_card_registers_only_reviewed_stable_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            manifest_path = project_root / "domain_packs" / "standard_fjsp" / "domain_pack.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "family_id": "standard_fjsp",
                        "method_families": [],
                        "worker_implementation_skills": [],
                        "knowledge": {"tagged_cards": {}, "knowledge_query": {"tag_descriptions": {}}},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "category": "knowledge",
                "title": "Beam 宽度预算",
                "slug": "beam-width-budget",
                "destination": "reference-standard",
                "summary": "根据层展开成本和剩余 deadline 决定 Beam 宽度。",
                "source": "人工审核的算法说明与可复现实验。",
                "body": "## 适用条件\n\n高柔性构造搜索。\n\n## 验证方式\n\n记录 expanded 和 retained。",
                "tags": ["beam_search", "construction"],
                "register": True,
            }
            with patch("harness_agent.web.server.PROJECT_ROOT", project_root):
                created = create_project_resource(payload)
                with self.assertRaisesRegex(ValueError, "只有稳定方法参考"):
                    create_project_resource(
                        {
                            **payload,
                            "slug": "unreviewed-run",
                            "destination": "experiment-memory",
                        }
                    )

            card_path = project_root / "knowledge" / "references" / "standard_fjsp" / "beam-width-budget.md"
            self.assertTrue(card_path.is_file())
            self.assertIn('status: "reviewed"', card_path.read_text(encoding="utf-8"))
            self.assertTrue(created["registered"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            registered_path = "knowledge/references/standard_fjsp/beam-width-budget.md"
            self.assertIn(registered_path, manifest["knowledge"]["tagged_cards"]["beam_search"])
            self.assertIn(registered_path, manifest["knowledge"]["tagged_cards"]["construction"])

    def test_overview_does_not_render_nonfunctional_round_tabs(self) -> None:
        html = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        style = (ROOT / "harness_agent" / "web" / "static" / "style.css").read_text(encoding="utf-8")

        self.assertNotIn('class="round-tabs"', html)
        self.assertNotIn(".round-tabs", style)

    def test_resources_and_model_allocation_are_mutually_exclusive_views(self) -> None:
        html = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        resources_start = html.index('id="view-resources"')
        models_start = html.index('id="view-models"')
        setup_start = html.index('id="view-setup"')
        resources_view = html[resources_start:models_start]
        models_view = html[models_start:setup_start]

        self.assertIn('data-view-target="resources">Skills / 知识库', html)
        self.assertIn('data-view-target="models">模型分配', html)
        self.assertIn('class="resource-browser"', resources_view)
        self.assertNotIn('class="model-allocation"', resources_view)
        self.assertIn('class="model-allocation"', models_view)

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

        self.assertEqual("standard_fjsp_requirement.md", demo["requirement"]["name"])
        self.assertEqual("standard_fjsp_io.md", demo["io"]["name"])
        self.assertEqual("fjsp.dauzere.18a.m10j20c10.txt", demo["instance"]["name"])
        self.assertIn("标准 FJSP", demo["requirement"]["text"])
        self.assertIn("standard_fjsp_schedule_v1", demo["io"]["text"])
        self.assertIn('"2057","2127"', demo["best_known_csv"]["text"])
        self.assertEqual(10, config["max_rounds"])
        self.assertEqual(120, config["worker_max_runtime_seconds"])
        self.assertEqual(1, config["promotion_repeats"])
        self.assertFalse(any(key.startswith("awls_") for key in config))
        for removed in ("solver", "run_mode", "evolution_mode", "baseline_source", "profile_mode"):
            self.assertNotIn(removed, config)

    def test_provider_status_exposes_model_and_key_presence_without_secret(self) -> None:
        os.environ["OPENAI_API_KEY"] = "test-secret-must-not-leak"
        os.environ["OPENCODE_MODEL"] = "openai/gpt-5.4"
        with patch("harness_agent.web.server.load_local_env"):
            status = deepseek_status_payload()

        self.assertEqual("openai/gpt-5.4", status["opencode_model"])
        self.assertTrue(status["provider_keys"]["openai"])
        self.assertNotIn("test-secret-must-not-leak", json.dumps(status))

    def test_provider_status_accepts_explicit_openai_compatible_gateway(self) -> None:
        os.environ["DEEPSEEK_API_KEY"] = "compatible-secret-must-not-leak"
        os.environ["DEEPSEEK_BASE_URL"] = "https://gateway.example/v1"
        os.environ["OPENCODE_OPENAI_COMPAT_FROM_DEEPSEEK"] = "true"
        with patch("harness_agent.web.server.load_local_env"), patch(
            "harness_agent.workers.opencode_worker.load_local_env"
        ):
            status = deepseek_status_payload()

        self.assertTrue(status["provider_keys"]["openai"])
        self.assertEqual("deepseek_compatible_gateway", status["provider_key_sources"]["openai"])
        self.assertNotIn("compatible-secret-must-not-leak", json.dumps(status))

    def test_create_job_preserves_frontend_opencode_model_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    main_agent_model="openai/gpt-5.4",
                    main_agent_variant="high",
                    coding_worker_model="deepseek/deepseek-v4-pro",
                    coding_worker_variant="low",
                    main_max_subagents=3,
                    max_competing_workers=4,
                ),
                output_root=Path(tmp),
            )

        self.assertEqual("openai/gpt-5.4", job["config"]["main_agent_model"])
        self.assertEqual("high", job["config"]["main_agent_variant"])
        self.assertEqual("deepseek/deepseek-v4-pro", job["config"]["coding_worker_model"])
        self.assertEqual("low", job["config"]["coding_worker_variant"])
        self.assertEqual(3, job["config"]["main_max_subagents"])
        self.assertEqual(4, job["config"]["max_competing_workers"])

    def test_frontend_submits_model_without_accepting_api_keys(self) -> None:
        index = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="main-agent-model-setup"', index)
        self.assertIn('id="main-agent-variant-setup"', index)
        self.assertIn('id="coding-worker-model-setup"', index)
        self.assertIn('id="coding-worker-variant-setup"', index)
        self.assertIn('id="main-max-subagents"', index)
        self.assertIn('id="max-competing-workers"', index)
        self.assertIn("main_agent_model: selectedAgentModel(\"main-agent\")", app)
        self.assertIn("coding_worker_model: selectedAgentModel(\"coding-worker\")", app)
        self.assertNotIn('id="openai-api-key"', index)

    def test_main_agent_visible_trace_is_collected_without_patch_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attempt_dir = Path(tmp) / "round_000"
            main_dir = attempt_dir / "main_agent"
            main_dir.mkdir(parents=True)
            events = [
                {
                    "type": "text",
                    "timestamp": 1000,
                    "part": {
                        "type": "text",
                        "text": "I am comparing assignment and sequence pressure.",
                        "metadata": {"openai": {"phase": "commentary"}},
                    },
                },
                {
                    "type": "text",
                    "timestamp": 1001,
                    "part": {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "direction_plan": {"direction_id": "d000"},
                                "worker_assignment": {"objective": "执行有界变异。"},
                            },
                            ensure_ascii=False,
                        ),
                        "metadata": {"openai": {"phase": "final_answer"}},
                    },
                },
                {
                    "type": "tool_use",
                    "timestamp": 1002,
                    "part": {
                        "tool": "task",
                        "state": {
                            "status": "completed",
                            "input": {
                                "subagent_type": "plan-critic",
                                "patchText": "must not appear in the browser trace",
                            },
                            "title": "Plan critic completed",
                        },
                    },
                },
                {
                    "type": "step_finish",
                    "timestamp": 1003,
                    "part": {
                        "reason": "stop",
                        "tokens": {
                            "input": 100,
                            "output": 20,
                            "reasoning": 30,
                            "cache": {"read": 50},
                        },
                    },
                },
            ]
            (main_dir / "opencode_main_events.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8",
            )
            (main_dir / "main_reasoning_trace.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "stage": "结构观察",
                                "summary": "Beam 已存在，但宽度只有 3。",
                                "evidence": ["examples/solver.py:712 beam_width=3"],
                                "inference": "状态多样性可能过早坍缩。",
                                "decision": "不重写 Beam，只扩大现有搜索覆盖。",
                                "next_check": "记录扩展状态数、耗时和 makespan。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            job = {"status": "running", "main_agent_trace": []}

            with patch("harness_agent.web.server.write_job_status"):
                scan_opencode_main_trace(job, set(), attempt_dir, "round_000")

        self.assertEqual(
            ["commentary", "tool", "usage"],
            [item["kind"] for item in job["main_agent_trace"]],
        )
        serialized = json.dumps(job["main_agent_trace"], ensure_ascii=False)
        self.assertIn("plan-critic", serialized)
        self.assertIn("cache=50", serialized)
        self.assertNotIn("状态多样性可能过早坍缩", serialized)
        self.assertNotIn("direction_plan", serialized)
        self.assertNotIn("must not appear", serialized)

    def test_main_agent_uses_structured_reasoning_only_without_native_commentary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attempt_dir = Path(tmp) / "round_000"
            main_dir = attempt_dir / "main_agent"
            main_dir.mkdir(parents=True)
            (main_dir / "main_reasoning_trace.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "stage": "结构观察",
                                "summary": "模型未发出原生 commentary，使用结构化记录兜底。",
                                "evidence": ["beam_width=3"],
                                "inference": "搜索覆盖可能不足。",
                                "decision": "验证扩大覆盖。",
                                "next_check": "比较 makespan 与耗时。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            job = {"status": "running", "main_agent_trace": []}

            with patch("harness_agent.web.server.write_job_status"):
                scan_opencode_main_trace(job, set(), attempt_dir, "round_000")

        self.assertEqual(["analysis"], [item["kind"] for item in job["main_agent_trace"]])
        self.assertIn("结构化记录兜底", job["main_agent_trace"][0]["text"])

    def test_main_agent_trace_includes_between_round_user_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attempt_dir = Path(tmp) / "round_001"
            revision_dir = attempt_dir / "main_agent_user_revision"
            revision_dir.mkdir(parents=True)
            (revision_dir / "opencode_main_events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "text",
                        "timestamp": 2000,
                        "part": {
                            "text": "正在根据用户介入重签任务书。",
                            "metadata": {"openai": {"phase": "commentary"}},
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            job = {"status": "running", "main_agent_trace": []}

            with patch("harness_agent.web.server.write_job_status"):
                scan_opencode_main_trace(job, set(), attempt_dir, "round_001")

        self.assertEqual(1, len(job["main_agent_trace"]))
        self.assertEqual("round_001 · 用户修订", job["main_agent_trace"][0]["attempt"])
        self.assertIn("重签任务书", job["main_agent_trace"][0]["text"])

    def test_frontend_renders_main_agent_trace_in_chat(self) -> None:
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function renderUnifiedTimeline(job)", app)
        self.assertIn("job.main_agent_trace", app)
        self.assertIn("main-agent-run", app)
        self.assertIn("main-trace-item", app)
        self.assertIn("renderUnifiedTimeline(job);", app)
        self.assertIn("showUnifiedConversation({scrollThread: true});", app)
        self.assertIn('renderAnalysisList("实现限制", assessment.implementation_limits)', app)
        self.assertIn('renderAnalysisList("证伪指标", mutation.falsification_metrics)', app)
        self.assertIn("function renderReasoningTrace(values)", app)
        self.assertIn('commentary: "Main Agent 思考过程"', app)
        self.assertIn('analysis: "Main Agent 思考摘要（兜底）"', app)
        self.assertNotIn('message.includes("rollback") || message.includes("回滚")', app)

    def test_coding_agent_trace_is_public_safe_and_distinguishes_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_001" / "candidates"
            attempts = []
            for candidate, timestamp in (("v1_beam", 2000), ("v2_rules", 2001)):
                attempt_dir = round_dir / candidate
                worker_dir = attempt_dir / "worker"
                worker_dir.mkdir(parents=True)
                (worker_dir / "opencode_command.json").write_text(
                    json.dumps(["opencode", "run", "--model", "openai/gpt-5.4", "--variant", "high"]),
                    encoding="utf-8",
                )
                events = [
                    {
                        "type": "text",
                        "timestamp": timestamp,
                        "part": {
                            "text": f"正在检查 {candidate} 的实现证据。",
                            "metadata": {"openai": {"phase": "commentary"}},
                        },
                    },
                    {
                        "type": "tool_use",
                        "timestamp": timestamp + 10,
                        "part": {
                            "tool": "apply_patch",
                            "state": {
                                "status": "completed",
                                "title": "Updated solver.py",
                                "input": {"patchText": "secret patch body"},
                                "output": "secret source body",
                            },
                        },
                    },
                ]
                (worker_dir / "opencode_events.jsonl").write_text(
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n",
                    encoding="utf-8",
                )
                attempts.append((attempt_dir, f"round_001 候选 {candidate}"))
            job = {"status": "running", "coding_agent_trace": []}
            with patch("harness_agent.web.server.write_job_status"):
                for attempt_dir, label in attempts:
                    scan_opencode_worker_trace(job, set(), attempt_dir, label)

        trace = job["coding_agent_trace"]
        self.assertEqual({"v1_beam", "v2_rules"}, {item["candidate_id"] for item in trace})
        self.assertEqual({"openai/gpt-5.4"}, {item["model"] for item in trace})
        self.assertEqual({"high"}, {item["variant"] for item in trace})
        serialized = json.dumps(trace, ensure_ascii=False)
        self.assertIn("Updated solver.py", serialized)
        self.assertNotIn("secret patch body", serialized)
        self.assertNotIn("secret source body", serialized)

    def test_frontend_merges_distinct_coding_agents_into_conversation(self) -> None:
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("job.coding_agent_trace", app)
        self.assertIn("appendCodingAgentRunHeader", app)
        self.assertIn("appendCodingAgentTraceItem", app)
        self.assertIn("state.codingTimelineHeaders", app)
        self.assertIn("Coding Agent ·", app)

    def test_agent_status_snapshot_separates_started_agents_from_configured_capacity(self) -> None:
        job = {
            "status": "running",
            "updated_at": "2026-07-25T00:00:00Z",
            "config": {
                "main_agent_model": "openai/gpt-5.4",
                "main_agent_variant": "high",
                "coding_worker_model": "openai/gpt-5.4",
                "coding_worker_variant": "high",
                "main_max_subagents": 2,
                "max_competing_workers": 4,
            },
            "main_agent_trace": [
                {
                    "id": "main-1",
                    "attempt": "round_000",
                    "timestamp": 100,
                    "kind": "commentary",
                    "text": "正在确定本轮最小变异。",
                },
                {
                    "id": "task-1",
                    "attempt": "round_000",
                    "timestamp": 110,
                    "kind": "tool",
                    "tool": "task",
                    "text": "task / plan-critic / completed / 评审变异方向",
                },
            ],
            "coding_agent_trace": [
                {
                    "id": "c1-1",
                    "agent_key": "round_000:c01:primary",
                    "display_name": "c01",
                    "round": "round_000",
                    "timestamp": 120,
                    "kind": "commentary",
                    "text": "正在实现候选一。",
                    "model": "openai/gpt-5.4",
                    "variant": "high",
                },
                {
                    "id": "c1-2",
                    "agent_key": "round_000:c01:primary",
                    "display_name": "c01",
                    "round": "round_000",
                    "timestamp": 130,
                    "kind": "final",
                    "text": "候选一完成。",
                    "model": "openai/gpt-5.4",
                    "variant": "high",
                },
                {
                    "id": "c2-1",
                    "agent_key": "round_000:c02:primary",
                    "display_name": "c02",
                    "round": "round_000",
                    "timestamp": 140,
                    "kind": "tool",
                    "tool": "apply_patch",
                    "status": "running",
                    "title": "更新 solver.py",
                    "text": "apply_patch / running / 更新 solver.py",
                    "model": "openai/gpt-5.4",
                    "variant": "high",
                },
            ],
        }

        snapshot = agent_status_snapshot(job)

        self.assertEqual(2, snapshot["summary"]["configured_subagents"])
        self.assertEqual(4, snapshot["summary"]["configured_workers"])
        self.assertEqual(1, snapshot["summary"]["started_subagents"])
        self.assertEqual(2, snapshot["summary"]["started_workers"])
        self.assertEqual(
            {"Main Agent": "waiting", "Main Subagent · plan-critic": "completed", "Coding Agent · c01": "completed", "Coding Agent · c02": "running"},
            {agent["name"]: agent["status"] for agent in snapshot["agents"]},
        )

    def test_frontend_renders_structured_agent_status_bar(self) -> None:
        html = (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="agent-status-bar"', html)
        self.assertIn('id="agent-status-summary"', html)
        self.assertIn('id="agent-status-track"', html)
        self.assertIn("function renderAgentStatusBar(job)", app)
        self.assertIn("function agentQuietSeconds(agent, status)", app)
        self.assertIn("function agentStatusDetail(job, agent)", app)
        self.assertIn("可能停滞", app)
        self.assertIn("job.agent_status", app)
        self.assertIn("renderAgentStatusBar(job);", app)

    def test_agent_status_snapshot_exposes_worker_attempts_without_public_trace(self) -> None:
        snapshot = agent_status_snapshot(
            {
                "status": "completed",
                "updated_at": "2026-07-25T00:00:00Z",
                "config": {
                    "coding_worker_model": "openai/gpt-5.4",
                    "coding_worker_variant": "high",
                    "max_competing_workers": 2,
                },
                "summary": {
                    "worker_summary": {
                        "completed_round_count": 3,
                        "attempt_count": 3,
                        "worker_status_counts": {"failed_runtime": 3},
                    }
                },
                "main_agent_trace": [],
                "coding_agent_trace": [],
            }
        )

        self.assertEqual(3, snapshot["summary"]["reported_worker_attempts"])
        self.assertEqual(3, snapshot["summary"]["untraced_worker_attempts"])
        self.assertEqual("3 轮完成", snapshot["agents"][0]["stage"])
        self.assertEqual("failed", snapshot["agents"][1]["status"])
        self.assertIn("failed_runtime=3", snapshot["agents"][1]["detail"])

    def test_frontend_resets_conversation_when_job_changes(self) -> None:
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("conversationJobId: null", app)
        self.assertIn("function resetConversationState(jobId)", app)
        self.assertIn("state.conversationJobId = jobId", app)
        self.assertIn("if (state.conversationJobId !== job.id)", app)
        self.assertIn("resetConversationState(job.id);", app)

    def test_frontend_starts_with_empty_conversation_and_does_not_restore_latest_job(self) -> None:
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        initialize_start = app.index("function initializeChat()")
        initialize_end = app.index("function resetConversationState", initialize_start)
        initialize_body = app[initialize_start:initialize_end]

        self.assertNotIn("appendChatMessage", initialize_body)
        self.assertNotIn("restoreLatest", app)
        self.assertIn("if (!state.currentJobId) return;", app)
        self.assertIn("loadJobHistory().catch", app)

    def test_web_round_gate_persists_analysis_and_returns_user_direction(self) -> None:
        job = {
            "id": "gate-job",
            "status": "running",
            "events": [],
            "intervention_history": [],
        }
        gate = WebRoundInterventionGate(job)
        previous_round = SimpleNamespace(
            round_index=0,
            decision="rolled_back",
            candidate_key=(-2300.0,),
            incumbent_key_after=(-2200.0,),
        )
        proposed = {
            "title": "Widen reassignment",
            "diagnosis": "The prior move set was too narrow.",
            "observed_shortcomings": ["Only one insertion position was tested."],
            "reasoning_trace": [
                {
                    "stage": "结构观察",
                    "summary": "Beam exists but remains narrow.",
                    "evidence": ["beam_width=3"],
                    "inference": "Coverage may collapse early.",
                    "decision": "Preserve Beam semantics.",
                    "next_check": "Measure expanded states.",
                }
            ],
            "incumbent_assessment": {
                "verified_capabilities": ["Beam is reachable."],
                "implementation_limits": ["beam_width=3"],
                "bottleneck_hypotheses": ["State diversity collapses early."],
                "evidence_refs": ["examples/solver.py:712"],
                "unknowns": ["Expanded-state count is unmeasured."],
            },
            "evidence_summary": ["Candidate was legal but rolled back."],
            "direction_judgment": "Preserve decoding and widen reassignment only.",
            "next_mutation": {
                "target_symbols": ["solve.beam_width"],
                "change": "Scale the existing Beam under the deadline.",
                "expected_effect": "Retain more distinct partial schedules.",
                "falsification_metrics": ["expanded states", "makespan"],
            },
        }

        with patch("harness_agent.web.server.write_job_status"):
            gate.publish(next_round_index=1, previous_round=previous_round, proposed_direction=proposed)
            gate.submit("Try critical reassignment with more insertion positions.")
            result = gate.wait_for_submission()

        self.assertEqual("waiting_for_user", job["status"])
        self.assertEqual("Widen reassignment", job["pending_intervention"]["main_analysis"]["title"])
        self.assertEqual(
            ["beam_width=3"],
            job["pending_intervention"]["main_analysis"]["incumbent_assessment"]["implementation_limits"],
        )
        self.assertEqual(
            ["solve.beam_width"],
            job["pending_intervention"]["main_analysis"]["next_mutation"]["target_symbols"],
        )
        self.assertEqual(
            "结构观察",
            job["pending_intervention"]["main_analysis"]["reasoning_trace"][0]["stage"],
        )
        self.assertEqual("user_rejected_direction_change", result["source"])
        self.assertEqual(
            "Try critical reassignment with more insertion positions.",
            result["direction"],
        )

    def test_web_round_gate_returns_typed_pivot_for_method_reselection(self) -> None:
        job = {"id": "pivot-job", "status": "running", "events": [], "intervention_history": []}
        gate = WebRoundInterventionGate(job)

        gate.submit("Switch to a coupled critical-block neighborhood.", action="pivot")
        result = gate.wait_for_submission()

        self.assertEqual("pivot", result["direction_patch"]["action"])
        self.assertEqual(
            "Switch to a coupled critical-block neighborhood.",
            result["direction_patch"]["instructions"],
        )

    def test_web_round_gate_defaults_to_current_direction_after_timeout(self) -> None:
        job = {"id": "timeout-job", "status": "running", "events": [], "intervention_history": []}
        gate = WebRoundInterventionGate(job, timeout_seconds=0.02)
        previous_round = SimpleNamespace(
            round_index=0,
            decision="rolled_back",
            candidate_key=(-2300.0,),
            incumbent_key_after=(-2200.0,),
            direction_plan={"method_family": "constructive_search"},
        )

        with patch("harness_agent.web.server.write_job_status"):
            result = gate(
                next_round_index=1,
                previous_round=previous_round,
                proposed_direction={
                    "experiment_stage": "pivot",
                    "method_family": "coupled_local_search",
                    "title": "Switch search family",
                },
            )

        self.assertEqual("continue", result["direction_patch"]["action"])
        self.assertEqual("direction_change_timeout_default_continue", result["source"])
        self.assertIn("Continue the previously active method family", result["direction_patch"]["instructions"])
        self.assertEqual("running", job["status"])
        self.assertIsNone(job["pending_intervention"])
        self.assertEqual("timeout_continue", job["intervention_history"][0]["resolution"])

    def test_web_round_gate_does_not_pause_same_direction_refinement(self) -> None:
        job = {"id": "continue-job", "status": "running", "events": [], "intervention_history": []}
        gate = WebRoundInterventionGate(job, timeout_seconds=0.02)
        previous_round = SimpleNamespace(
            round_index=0,
            direction_plan={"method_family": "constructive_search"},
        )

        result = gate(
            next_round_index=1,
            previous_round=previous_round,
            proposed_direction={
                "experiment_stage": "probe",
                "method_family": "constructive_search",
            },
        )

        self.assertIsNone(result)
        self.assertEqual("running", job["status"])
        self.assertNotIn("pending_intervention", job)

    def test_frontend_exposes_continue_vs_method_family_pivot_actions(self) -> None:
        app = (ROOT / "harness_agent" / "web" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("细化当前方向", app)
        self.assertIn("继续当前方向", app)
        self.assertIn(
            "20 秒",
            (ROOT / "harness_agent" / "web" / "static" / "index.html").read_text(encoding="utf-8"),
        )
        self.assertIn("切换方法族", app)
        self.assertIn('state.interventionAction = "pivot"', app)
        self.assertIn("action: state.interventionAction", app)

    def test_round_intervention_submission_resumes_blocked_loop(self) -> None:
        job = {
            "id": "gate-job",
            "status": "running",
            "events": [],
            "intervention_history": [],
        }
        gate = WebRoundInterventionGate(job)
        _JOBS[job["id"]] = job
        _ROUND_GATES[job["id"]] = gate
        previous_round = SimpleNamespace(
            round_index=0,
            decision="rolled_back",
            candidate_key=(-2300.0,),
            incumbent_key_after=(-2200.0,),
        )
        result: dict[str, str | None] = {}

        def wait_at_gate() -> None:
            result["direction"] = gate(
                next_round_index=1,
                previous_round=previous_round,
                proposed_direction={
                    "title": "Widen reassignment",
                    "experiment_stage": "pivot",
                    "observed_shortcomings": ["Only one insertion position was tested."],
                    "evidence_summary": ["The legal candidate was rolled back."],
                    "direction_judgment": "Preserve decoding and widen the move set.",
                },
            )

        with patch("harness_agent.web.server.write_job_status"):
            thread = threading.Thread(target=wait_at_gate, daemon=True)
            thread.start()
            deadline = time.monotonic() + 1.0
            while job["status"] != "waiting_for_user" and time.monotonic() < deadline:
                time.sleep(0.01)
            response = submit_round_intervention(
                job["id"],
                {"direction": "Try critical reassignment with more insertion positions."},
            )
            thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(response["accepted"])
        self.assertEqual("running", job["status"])
        self.assertIsNone(job["pending_intervention"])
        self.assertEqual(1, len(job["intervention_history"]))
        self.assertEqual("user_rejected_direction_change", result["direction"]["source"])
        self.assertEqual(
            "Try critical reassignment with more insertion positions.",
            result["direction"]["direction"],
        )

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
            job = create_job(
                self.job_payload(
                    max_rounds=3,
                    pause_between_rounds=False,
                    main_agent_model="openai/gpt-5.4",
                    main_agent_variant="high",
                    coding_worker_model="deepseek/deepseek-v4-pro",
                    coding_worker_variant="low",
                ),
                output_root=Path(tmp),
            )
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
            fake_main = SimpleNamespace()
            fake_semantic_reviewer = SimpleNamespace()
            with patch(
                "harness_agent.web.server.OpenCodeWorker", return_value=fake_worker
            ) as worker_factory, patch(
                "harness_agent.web.server.OpenCodeMainAgent", return_value=fake_main
            ) as main_factory, patch(
                "harness_agent.web.server.DeepSeekAlgorithmSemanticReviewer",
                return_value=fake_semantic_reviewer,
            ) as semantic_reviewer_factory, patch(
                "harness_agent.web.server.is_deepseek_configured", return_value=True
            ) as deepseek_status, patch(
                "harness_agent.web.server.run_standard_worker_loop", return_value=manifest
            ) as run_loop:
                run_job(job["id"])

        self.assertEqual("completed", job["status"])
        request = run_loop.call_args.args[0]
        self.assertEqual(3, request.iterations)
        self.assertEqual("examples/agent_generated_fjsp_solver.py", request.agent_generated_solver_path)
        self.assertTrue(request.apply_worker_changes)
        self.assertIsInstance(request.round_intervention, WebRoundInterventionGate)
        self.assertIs(fake_worker, request.worker)
        self.assertIs(fake_main, request.main_agent)
        self.assertIs(fake_semantic_reviewer, request.semantic_reviewer)
        self.assertEqual(
            job["config"]["deepseek_model"],
            semantic_reviewer_factory.call_args.kwargs["model"],
        )
        self.assertEqual("deepseek/deepseek-v4-pro", worker_factory.call_args.kwargs["model"])
        self.assertEqual("low", worker_factory.call_args.kwargs["variant"])
        self.assertEqual(
            job["config"]["worker_max_runtime_seconds"],
            worker_factory.call_args.kwargs["timeout_seconds"],
        )
        self.assertEqual("openai/gpt-5.4", main_factory.call_args.kwargs["model"])
        self.assertEqual("high", main_factory.call_args.kwargs["variant"])
        self.assertEqual(4, main_factory.call_args.kwargs["max_subagents"])
        self.assertEqual(4, request.max_competing_workers)
        deepseek_status.assert_not_called()

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

            enabled_catalog = {
                "recommended_package_id": "standard_fjsp_awls_hgtsa",
                "packages": [{"package_id": "standard_fjsp_awls_hgtsa"}],
            }
            with patch("harness_agent.web.server.method_package_catalog", return_value=enabled_catalog):
                self.assertEqual(memory_path.resolve(), latest_compatible_experience_memory(current))
                current["config"]["instance_profile"]["has_sequence_dependent_setup"] = True
                self.assertIsNone(latest_compatible_experience_memory(current))

    def test_latest_memory_allows_empty_package_and_uses_lightweight_instance_portrait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_dir = Path(tmp) / "previous"
            memory_path = previous_dir / "run" / "standard_worker_loop" / "worker_loop" / "experience_memory.json"
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text(
                json.dumps(
                    {
                        "memory_tiers": {
                            "validated_lessons": [
                                {"lesson_id": "validated", "method_package_id": "", "strategy": "portable"}
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
                "config": {
                    "instance_profile": {
                        "format": "standard_fjsp",
                        "has_sequence_dependent_setup": False,
                        "operation_count": 72,
                        "machine_count": 6,
                        "max_candidate_count": 4,
                    }
                },
            }
            current = {
                "id": "current",
                "config": {
                    "instance_profile": {
                        "format": "standard_fjsp",
                        "has_sequence_dependent_setup": False,
                        "operation_count": 88,
                        "machine_count": 8,
                        "max_candidate_count": 3,
                    }
                },
            }

            with patch("harness_agent.web.server.method_package_catalog", return_value={"recommended_package_id": ""}):
                self.assertEqual(memory_path.resolve(), latest_compatible_experience_memory(current))
                current["config"]["instance_profile"]["operation_count"] = 720
                self.assertIsNone(latest_compatible_experience_memory(current))

    def test_latest_memory_ignores_candidate_only_experience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_dir = Path(tmp) / "previous"
            memory_path = previous_dir / "run" / "standard_worker_loop" / "worker_loop" / "experience_memory.json"
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text(
                json.dumps(
                    {
                        "memory_tiers": {
                            "candidate_lessons": [
                                {"lesson_id": "unvalidated", "method_package_id": "standard_fjsp_awls_hgtsa"}
                            ],
                            "validated_lessons": [],
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

            self.assertIsNone(latest_compatible_experience_memory(current))

    def test_latest_memory_accepts_legacy_profiles_without_variant_or_portrait_fields(self) -> None:
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
                "config": {
                    "instance_profile": {
                        "format": "standard_fjsp",
                        "has_sequence_dependent_setup": False,
                        "variant_features": [],
                        "instance_portrait": {
                            "operation_bucket": "small",
                            "machine_bucket": "medium",
                            "flex_bucket": "medium_flex",
                        },
                    }
                },
            }

            enabled_catalog = {"recommended_package_id": "standard_fjsp_awls_hgtsa"}
            with patch("harness_agent.web.server.method_package_catalog", return_value=enabled_catalog):
                self.assertEqual(memory_path.resolve(), latest_compatible_experience_memory(current))

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

    def test_progress_summary_keeps_best_legal_result_after_failed_trials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_dir = root / "agent_generated_baseline"
            primary_dir = root / "round_000"
            failed_repair_dir = primary_dir / "repair_001"
            failed_round_dir = root / "round_001"
            for path in (baseline_dir, primary_dir, failed_repair_dir, failed_round_dir):
                path.mkdir(parents=True, exist_ok=True)
            valid = {"total": 1, "valid": 1, "failed": 0, "best_metrics": {"makespan": 2228}}
            failed = {"total": 0, "valid": 0, "failed": 0, "best_metrics": {}}
            (baseline_dir / "cycle_result.json").write_text(
                json.dumps({"harness": valid}), encoding="utf-8"
            )
            (primary_dir / "cycle_result.json").write_text(
                json.dumps({"harness": valid}), encoding="utf-8"
            )
            (failed_repair_dir / "cycle_result.json").write_text(
                json.dumps({"harness": failed}), encoding="utf-8"
            )
            (failed_round_dir / "cycle_result.json").write_text(
                json.dumps({"harness": failed}), encoding="utf-8"
            )

            progress = summarize_code_evolution_progress(root)

        self.assertEqual(2, progress["completed_round_count"])
        self.assertEqual(2228, progress["best_makespan_so_far"])
        self.assertIsNone(progress["latest_makespan"])

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

    def test_attempt_progress_reports_legacy_soft_acceptance_after_preflight_rejection(self) -> None:
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
        self.assertTrue(any("JA 记录了诊断项（仅供审计，不阻止 Core）" in message for message in messages))
        self.assertTrue(any("历史软门禁被降级并放行正式评估" in message for message in messages))
        self.assertTrue(any("diagnostic_makespan=2352" in message for message in messages))

    def test_worker_attempt_dirs_include_parallel_candidates_and_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = Path(tmp) / "round_000"
            (round_dir / "candidates" / "c0" / "repair_001").mkdir(parents=True)
            (round_dir / "candidates" / "c1").mkdir(parents=True)

            attempts = worker_attempt_dirs(round_dir)

        paths = [path.relative_to(round_dir).as_posix() for path, _label in attempts]
        self.assertIn("candidates/c0", paths)
        self.assertIn("candidates/c0/repair_001", paths)
        self.assertIn("candidates/c1", paths)

    def test_round_reflection_is_published_to_web_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            round_dir = root / "round_000"
            reflection_dir = round_dir / "main_agent_reflection"
            reflection_dir.mkdir(parents=True)
            (reflection_dir / "round_reflection.json").write_text(
                json.dumps(
                    {
                        "hypothesis_outcome": "inconclusive_not_exercised",
                        "next_action": {"action": "probe"},
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "id": "reflection-job",
                "status": "running",
                "job_dir": str(root),
                "events": [],
                "main_agent_trace": [],
            }

            with patch("harness_agent.web.server.write_job_status"):
                scan_round_reflection_progress(job, set(), round_dir)

        self.assertIn("inconclusive_not_exercised", job["events"][-1]["message"])
        self.assertIn("probe", job["events"][-1]["message"])

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

    @staticmethod
    def zip_payload(files: dict[str, str]) -> dict[str, str]:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return {
            "name": "starter.zip",
            "base64": base64.b64encode(stream.getvalue()).decode("ascii"),
        }


if __name__ == "__main__":
    unittest.main()
