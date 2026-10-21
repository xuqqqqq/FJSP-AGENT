from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent import cli
from harness_agent.web import server


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = {
    "status": "ok", "rounds": [], "round_count": 0, "artifacts": {},
    "baseline_key": [], "final_key": [], "promoted_rounds": 0,
}


class MainTimeoutCallerTests(unittest.TestCase):
    def test_cli_main_factory_resolves_explicit_timeout(self) -> None:
        for configured, resolved in ((None, 600), (0, None), (47, 47), (900, 900)):
            with self.subTest(configured=configured), patch(
                "harness_agent.cli.resolve_main_planning_timeout", return_value=resolved
            ) as resolve, patch("harness_agent.cli.OpenCodeMainAgent") as factory:
                cli.make_main_agent(
                    "opencode", deepseek_model="unused", opencode_model="qiming/dsv41-1017",
                    project_root=ROOT, timeout_seconds=configured,
                )
                resolve.assert_called_once_with("qiming/dsv41-1017", configured)
                self.assertEqual(resolved or 0, factory.call_args.kwargs["timeout_seconds"])

    def test_cli_real_main_constructor_preserves_timeout_precedence(self) -> None:
        cases = (
            ("qiming/dsv41-1017", None, None, 600),
            ("deepseek/deepseek-v4-pro", None, None, 120),
            ("qiming/dsv41-1017", "47", None, 47),
            ("qiming/dsv41-1017", "0", None, None),
            ("qiming/dsv41-1017", "47", 0, None),
            ("qiming/dsv41-1017", "0", 90, 90),
            ("qiming/dsv41-1017", "47", 900, 900),
        )
        for model, env_timeout, explicit, expected in cases:
            with self.subTest(model=model, env=env_timeout, explicit=explicit), patch.dict(
                os.environ, {}, clear=True
            ):
                if env_timeout is not None:
                    os.environ["OPENCODE_MAIN_TIMEOUT_SECONDS"] = env_timeout
                agent = cli.make_main_agent(
                    "opencode", deepseek_model="unused", opencode_model=model,
                    project_root=ROOT, timeout_seconds=explicit,
                )
                self.assertEqual(expected, agent.timeout_seconds)

    def test_cli_real_main_and_reviewer_accept_environment_unlimited(self) -> None:
        args = cli.build_parser().parse_args([
            "run-standard-worker-loop", "--instance-dir", "examples",
            "--output-dir", "outputs/test", "--opencode-model", "qiming/dsv41-1017",
            "--max-runtime-seconds", "31",
        ])
        for env_timeout, expected in (("0", None), ("47", 47)):
            with self.subTest(env=env_timeout), patch.dict(
                os.environ, {"OPENCODE_MAIN_TIMEOUT_SECONDS": env_timeout}, clear=True
            ), patch("harness_agent.cli.make_worker", return_value=SimpleNamespace()), patch(
                "harness_agent.cli.run_standard_worker_loop", return_value=MANIFEST
            ) as run, patch("harness_agent.cli.print_json"):
                self.assertEqual(0, cli.run_standard_worker_loop_cmd(args))
                request = run.call_args.args[0]
                self.assertEqual(expected, request.main_agent.timeout_seconds)
                self.assertEqual(expected, request.semantic_reviewer.timeout_seconds)

    def test_cli_main_without_model_uses_environment_model_timeout(self) -> None:
        with patch.dict(os.environ, {"OPENCODE_MODEL": "qiming/dsv41-1017"}, clear=True):
            agent = cli.make_main_agent(
                "opencode", deepseek_model="unused", opencode_model=None, project_root=ROOT,
            )
        self.assertEqual("qiming/dsv41-1017", agent.model)
        self.assertEqual(600, agent.timeout_seconds)

    def test_cli_standard_keeps_main_review_worker_and_controller_budgets_independent(self) -> None:
        for main_timeout in (600, 120, 47, None):
            with self.subTest(main_timeout=main_timeout):
                args = cli.build_parser().parse_args([
                    "run-standard-worker-loop", "--instance-dir", "examples",
                    "--output-dir", "outputs/test", "--opencode-model", "qiming/dsv41-1017",
                    "--max-runtime-seconds", "31", "--controller-budget-seconds", "75",
                ])
                with patch("harness_agent.cli.make_worker", return_value=SimpleNamespace(
                    model="deepseek/deepseek-v4-pro"
                )) as worker, patch(
                    "harness_agent.cli.resolve_main_planning_timeout", return_value=main_timeout
                ) as resolve, patch("harness_agent.cli.OpenCodeMainAgent") as main, patch(
                    "harness_agent.cli.OpenCodeAlgorithmSemanticReviewer"
                ) as reviewer, patch(
                    "harness_agent.cli.run_standard_worker_loop", return_value=MANIFEST
                ) as run, patch("harness_agent.cli.print_json"):
                    self.assertEqual(0, cli.run_standard_worker_loop_cmd(args))

                self.assertEqual(31, worker.call_args.kwargs["timeout_seconds"])
                self.assertEqual(main_timeout or 0, main.call_args.kwargs["timeout_seconds"])
                self.assertEqual(main_timeout or 0, reviewer.call_args.kwargs["timeout_seconds"])
                self.assertEqual([
                    (("qiming/dsv41-1017", None), {}),
                    (("deepseek/deepseek-v4-pro",), {}),
                ], resolve.call_args_list)
                request = run.call_args.args[0]
                self.assertEqual(31, request.max_runtime_seconds)
                self.assertEqual(75, request.controller_budget_seconds)

    def test_cli_worker_loop_does_not_pass_worker_budget_to_main(self) -> None:
        args = cli.build_parser().parse_args([
            "run-worker-loop", "--contract", "configs/standard_fjsp_tiny.example.json",
            "--context-packet", "outputs/context.json", "--output-dir", "outputs/test",
            "--max-runtime-seconds", "31", "--opencode-model", "qiming/dsv41-1017",
        ])
        with patch("harness_agent.cli.load_runnable_contract", return_value=object()), patch(
            "harness_agent.cli.make_worker"
        ) as worker, patch(
            "harness_agent.cli.resolve_main_planning_timeout", return_value=600
        ) as resolve, patch("harness_agent.cli.OpenCodeMainAgent") as main, patch(
            "harness_agent.cli.run_worker_loop",
            return_value=SimpleNamespace(baseline_key=[], final_key=[]),
        ) as run, patch("harness_agent.cli.print_json"):
            self.assertEqual(0, cli.run_worker_loop_cmd(args))
        resolve.assert_called_once_with("qiming/dsv41-1017", None)
        self.assertEqual(600, main.call_args.kwargs["timeout_seconds"])
        self.assertEqual(31, worker.call_args.kwargs["timeout_seconds"])
        self.assertEqual(31, run.call_args.kwargs["max_runtime_seconds"])

    def test_web_main_uses_shared_timeout_and_preserves_budgets(self) -> None:
        payload = {
            "requirement": {"name": "requirement.md", "text": "Solve FJSP."},
            "io": {"name": "io.md", "text": "Use standard schedule JSON."},
            "instance": {"name": "tiny.fjs", "text": (
                ROOT / "examples" / "standard_fjsp_tiny.fjs"
            ).read_text(encoding="utf-8")},
            "main_agent_model": "qiming/dsv41-1017",
            "coding_worker_model": "deepseek/deepseek-v4-pro",
            "worker_max_runtime_seconds": 321,
            "controller_budget_seconds": 75,
            "max_rounds": 1,
            "guidance_mode": "none",
        }
        for timeout in (600, 47, None):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as tmp, patch.dict(
                server._JOBS, {}, clear=True
            ), patch.dict(server._JOB_CANCELLATIONS, {}, clear=True), patch(
                "harness_agent.web.server.discover_model_catalog", return_value={"models": [], "error": None}
            ), patch("harness_agent.web.server.load_local_env"), patch.dict(os.environ, {}, clear=True):
                os.environ["OPENCODE_MAIN_TIMEOUT_SECONDS"] = str(timeout or 0)
                job = server.create_job(payload, output_root=Path(tmp))
                worker = SimpleNamespace(capabilities=lambda: SimpleNamespace(supports_code_generation=True))
                with patch("harness_agent.web.server.OpenCodeWorker", return_value=worker) as worker_factory, patch(
                    "harness_agent.web.server.resolve_main_planning_timeout",
                    wraps=server.resolve_main_planning_timeout,
                ) as resolve, patch("harness_agent.web.server.OpenCodeMainAgent") as main, patch(
                    "harness_agent.web.server.run_standard_worker_loop", return_value=MANIFEST
                ) as run:
                    server.run_job(job["id"])
                self.assertEqual("completed", job["status"], job.get("error"))
                resolve.assert_called_once_with("qiming/dsv41-1017")
                self.assertEqual(timeout or 0, main.call_args.kwargs["timeout_seconds"])
                self.assertEqual(321, worker_factory.call_args.kwargs["timeout_seconds"])
                request = run.call_args.args[0]
                self.assertEqual(321, request.max_runtime_seconds)
                self.assertEqual(75, request.controller_budget_seconds)


if __name__ == "__main__":
    unittest.main()
