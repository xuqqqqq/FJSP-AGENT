from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.web.server import (
    _JOB_CANCELLATIONS,
    _JOBS,
    _ROUND_GATES,
    browser_safe_json,
    create_project_resource,
    create_job,
    deepseek_status_payload,
    latest_compatible_experience_memory,
    make_demo_examples,
    mark_stale_persisted_job_interrupted,
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
        with patch("harness_agent.web.server.OpenCodeWorker") as worker_cls, patch(
            "harness_agent.web.server.is_deepseek_configured", return_value=False
        ):
            worker_cls.return_value.capabilities.return_value.supports_code_generation = True
            payload = service_health_payload()

        self.assertEqual("ok", payload["status"])
        self.assertEqual("algoforge-web", payload["service"])
        self.assertTrue(payload["opencode_available"])
        self.assertFalse(payload["provider_configured"])

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

    def test_create_job_selects_nfa_evaluator_from_instance_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    instance={
                        "name": "FFCR01.txt",
                        "text": (ROOT / "examples" / "nfa_ffcr01.txt").read_text(encoding="utf-8"),
                    },
                ),
                output_root=Path(tmp),
            )

        self.assertTrue(job["config"]["instance_profile"]["has_machine_availability"])
        self.assertEqual(
            "examples/nfa_machine_availability_evaluator.py",
            job["config"]["evaluator_path"],
        )

    def test_create_job_selects_distributed_transfer_evaluator_from_instance_profile(self) -> None:
        instance_path = (
            ROOT
            / "ALL-Input-Information"
            / "10-distributed-FJSP"
            / "10-Instance"
            / "small size"
            / "DFM01_10x2x6.txt"
        )
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    requirement={
                        "name": "requirement.md",
                        "text": "Solve distributed FJSP with transfer time and energy.",
                    },
                    io={
                        "name": "io.md",
                        "text": "Output distributed_fjsp_schedule_v1 records with factory_id.",
                    },
                    instance={
                        "name": "DFM01_10x2x6.txt",
                        "text": instance_path.read_text(encoding="utf-8"),
                    },
                ),
                output_root=Path(tmp),
            )

        profile = job["config"]["instance_profile"]
        self.assertTrue(profile["valid"])
        self.assertEqual("distributed_fjsp", profile["format"])
        self.assertEqual("fjsp_distributed_transfer", profile["variant"])
        self.assertTrue(profile["has_distributed_transfer"])
        self.assertEqual(2, profile["factory_count"])
        self.assertEqual(6, profile["machines_per_factory"])
        self.assertEqual(10, profile["job_count"])
        self.assertEqual(50, profile["operation_count"])
        self.assertEqual(30, profile["transfer_time_model"]["same_factory_different_machine"])
        self.assertEqual(60, profile["transfer_time_model"]["cross_factory"])
        self.assertTrue(profile["energy_enabled"])
        self.assertIn("factory_assignment", profile["variant_features"])
        self.assertEqual(
            "examples/fjsp_distributed_transfer_evaluator.py",
            job["config"]["evaluator_path"],
        )

    def test_create_job_selects_priority_evaluator_from_instance_profile(self) -> None:
        root = ROOT / "ALL-Input-Information" / "11-priority-FJSP"
        instance_path = root / "11-Instances" / "fjsp.barnes.mt10c1.m11j10c2.priority.seed20260722.txt"
        with tempfile.TemporaryDirectory() as tmp:
            job = create_job(
                self.job_payload(
                    requirement={
                        "name": "fjsp_priority_requirement.md",
                        "text": (root / "docs" / "fjsp_priority_requirement.md").read_text(encoding="utf-8"),
                    },
                    io={
                        "name": "fjsp_priority_io.md",
                        "text": (root / "docs" / "fjsp_priority_io.md").read_text(encoding="utf-8"),
                    },
                    instance={
                        "name": instance_path.name,
                        "text": instance_path.read_text(encoding="utf-8"),
                    },
                ),
                output_root=Path(tmp),
            )

        profile = job["config"]["instance_profile"]
        self.assertTrue(profile["valid"])
        self.assertEqual("standard_fjsp", profile["format"])
        self.assertEqual("fjsp_priority", profile["variant"])
        self.assertTrue(profile["has_job_priority"])
        self.assertEqual(3, profile["priority_job_count"])
        self.assertEqual([1, 6, 8], profile["priority_job_ids"])
        self.assertAlmostEqual(0.3, profile["priority_job_ratio"])
        self.assertFalse(profile["has_sequence_dependent_setup"])
        self.assertFalse(profile["has_machine_availability"])
        self.assertFalse(profile["has_distributed_transfer"])
        self.assertIn("priority_completion_time", profile["variant_features"])
        self.assertIn("lexicographic_objective", profile["variant_features"])
        self.assertEqual(
            "examples/fjsp_job_priority_evaluator.py",
            job["config"]["evaluator_path"],
        )

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
        self.assertEqual("Try critical reassignment with more insertion positions.", result)

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
        self.assertEqual(
            "Try critical reassignment with more insertion positions.",
            result["direction"],
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
            with patch(
                "harness_agent.web.server.OpenCodeWorker", return_value=fake_worker
            ) as worker_factory, patch(
                "harness_agent.web.server.OpenCodeMainAgent", return_value=fake_main
            ) as main_factory, patch(
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
        self.assertIsNone(request.semantic_reviewer)
        self.assertEqual("deepseek/deepseek-v4-pro", worker_factory.call_args.kwargs["model"])
        self.assertEqual("low", worker_factory.call_args.kwargs["variant"])
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
        self.assertTrue(any("候选预检或结果复验未通过" in message for message in messages))
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


if __name__ == "__main__":
    unittest.main()
