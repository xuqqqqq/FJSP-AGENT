from __future__ import annotations

import unittest
import tempfile
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.cli import build_parser, run_standard_worker_loop_cmd, run_worker_loop_cmd
from harness_agent.agents.semantic import OpenCodeAlgorithmSemanticReviewer
from harness_agent.core.runner import RunSummary, solver_time_limit_seconds
from harness_agent.orchestration.loop import (
    WorkerLoopResult,
    collect_current_round_repair_targets,
    current_round_repair_feedback,
)
from harness_agent.orchestration.standard import (
    StandardWorkerLoopRequest,
    build_standard_worker_contract_payload,
    prepare_provided_project_source,
    provided_project_read_paths,
    run_standard_worker_loop,
    standard_solver_command,
    standard_worker_manifest,
)
from harness_agent.worker import NullWorker


ROOT = Path(__file__).resolve().parents[1]


class StandardWorkerLoopTests(unittest.TestCase):
    def make_request(self, **overrides: object) -> StandardWorkerLoopRequest:
        values: dict[str, object] = {
            "docs": [ROOT / "README.md"],
            "instance_dir": ROOT / "examples",
            "pattern": "standard_fjsp_tiny.fjs",
            "output_dir": ROOT / "outputs" / "unused_test",
            "project_root": ROOT,
            "worker": NullWorker(),
            "seeds": [0],
            "agent_generated_solver_path": "examples/generated_solver_for_test.py",
        }
        values.update(overrides)
        return StandardWorkerLoopRequest(**values)

    def test_contract_only_runs_agent_generated_solver(self) -> None:
        payload = build_standard_worker_contract_payload(self.make_request())

        self.assertEqual("agent_generated", payload["review"]["baseline_source"])
        self.assertIn("examples/generated_solver_for_test.py", payload["commands"]["solver"])
        self.assertIn("--time-limit-sec {solver_time_limit_seconds}", payload["commands"]["solver"])
        self.assertEqual(
            "python -m py_compile examples/generated_solver_for_test.py",
            payload["commands"]["quick_test"],
        )
        self.assertNotIn("harness_agent", payload["paths"]["allowed_paths"])
        self.assertIn("examples/standard_fjsp_evaluator.py", payload["paths"]["forbidden_paths"])

    def test_request_has_no_embedded_solver_algorithm_parameters(self) -> None:
        field_names = {item.name for item in fields(StandardWorkerLoopRequest)}

        self.assertNotIn("solver", field_names)
        self.assertNotIn("baseline_source", field_names)
        self.assertFalse(any(name.startswith("awls_") for name in field_names))
        self.assertFalse(any(name.startswith("local_search_") for name in field_names))
        self.assertNotIn("portfolio_size", field_names)

    def test_solver_command_points_to_generated_entrypoint(self) -> None:
        command = standard_solver_command(self.make_request())

        self.assertIn("examples/generated_solver_for_test.py", command)
        self.assertIn("--input {instance}", command)
        self.assertIn("--output {solution}", command)
        self.assertIn("--seed {seed}", command)

    def test_provided_project_contract_uses_existing_cli_and_primary_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "solver.py").write_text("print('entry')\n", encoding="utf-8")
            package = project / "fjsp"
            package.mkdir()
            (package / "solver.py").write_text("def solve(): return 1\n", encoding="utf-8")
            request = self.make_request(
                provided_project_root=project,
                provided_solver_command="python solver.py {instance} --output {solution} --seed {seed}",
                provided_target_file="fjsp/solver.py",
                provided_project_read_paths=["solver.py"],
            )

            payload = build_standard_worker_contract_payload(request)

        self.assertEqual("provided_project", payload["review"]["baseline_source"])
        self.assertEqual("fjsp/solver.py", payload["review"]["worker_target_file"])
        self.assertEqual(["solver.py"], payload["review"]["provided_project_read_paths"])
        self.assertEqual(["."], payload["paths"]["allowed_paths"])
        self.assertEqual(
            "python solver.py {instance} --output {solution} --seed {seed}",
            payload["commands"]["solver"],
        )
        self.assertEqual("python -m py_compile fjsp/solver.py", payload["commands"]["quick_test"])

    def test_explicit_instance_paths_replace_single_form_instance(self) -> None:
        request = self.make_request(
            instance_paths=[
                ROOT / "examples" / "standard_fjsp_tiny.fjs",
                ROOT / "examples" / "web_demo_instance.fjs",
            ],
            max_instances=None,
        )

        payload = build_standard_worker_contract_payload(request)

        self.assertEqual(
            ["standard_fjsp_tiny", "web_demo_instance"],
            [item["id"] for item in payload["instances"]],
        )

    def test_provided_project_source_quarantines_local_scores_and_overlays_fixed_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uploaded = root / "uploaded"
            uploaded.mkdir()
            (uploaded / "solver.py").write_text("print('entry')\n", encoding="utf-8")
            (uploaded / "evaluate.py").write_text("raise RuntimeError('untrusted')\n", encoding="utf-8")
            for name in ("trusted", "instances", "solutions"):
                directory = uploaded / name
                directory.mkdir()
                (directory / "history.json").write_text("{}\n", encoding="utf-8")

            composed = prepare_provided_project_source(
                uploaded_root=uploaded,
                trusted_project_root=ROOT,
                output_path=root / "composed",
            )

            self.assertTrue((composed / "solver.py").is_file())
            self.assertFalse((composed / "evaluate.py").exists())
            self.assertFalse((composed / "trusted").exists())
            self.assertFalse((composed / "instances").exists())
            self.assertFalse((composed / "solutions").exists())
            fixed_evaluator = composed / "examples" / "standard_fjsp_evaluator.py"
            self.assertEqual(
                (ROOT / "examples" / "standard_fjsp_evaluator.py").read_text(encoding="utf-8"),
                fixed_evaluator.read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "examples/standard_fjsp_evaluator.py",
                provided_project_read_paths(composed, target_file="solver.py"),
            )

    def test_standard_worker_cli_exposes_only_platform_controls(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
                "--agent-generated-solver-path",
                "examples/custom_agent_generated.py",
            ]
        )

        self.assertEqual("opencode", args.worker)
        self.assertEqual("examples/custom_agent_generated.py", args.agent_generated_solver_path)
        self.assertEqual(4, args.main_max_subagents)
        self.assertEqual(4, args.max_competing_workers)
        self.assertEqual("full", args.guidance_mode)
        self.assertFalse(hasattr(args, "solver"))
        self.assertFalse(hasattr(args, "baseline_source"))
        self.assertFalse(any(name.startswith("awls_") for name in vars(args)))

    def test_contract_comparison_cli_accepts_paired_manifests(self) -> None:
        args = build_parser().parse_args(
            [
                "compare-contract-guidance",
                "--full-manifest",
                "outputs/full/standard_worker_loop_manifest.json",
                "--none-manifest",
                "outputs/none/standard_worker_loop_manifest.json",
                "--output-dir",
                "outputs/comparison",
            ]
        )

        self.assertEqual(1, len(args.full_manifest))
        self.assertEqual(1, len(args.none_manifest))

    def test_standard_worker_cli_accepts_frozen_shared_baseline(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
                "--provided-project-root",
                "outputs/shared_baseline",
                "--provided-solver-command",
                "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
                "--provided-target-file",
                "examples/agent_generated_fjsp_solver.py",
            ]
        )

        self.assertEqual(Path("outputs/shared_baseline"), args.provided_project_root)
        self.assertEqual(
            "examples/agent_generated_fjsp_solver.py",
            args.provided_target_file,
        )

    def test_standard_worker_cli_accepts_guidance_ablation(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
                "--guidance-mode",
                "none",
            ]
        )

        self.assertEqual("none", args.guidance_mode)

    def test_solver_time_limit_reserves_core_exit_headroom(self) -> None:
        self.assertEqual(48.0, solver_time_limit_seconds(60))
        self.assertLess(solver_time_limit_seconds(30), 30)

    def test_manifest_propagates_baseline_generation_failure(self) -> None:
        summary = RunSummary(
            total=0,
            valid=0,
            failed=0,
            best_experiment_id=None,
            best_metrics={},
            best_candidate_id=None,
            best_candidate_metrics=None,
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={},
        )
        result = WorkerLoopResult(
            baseline_key=(float("-inf"),),
            final_key=(float("-inf"),),
            final_worktree=ROOT,
            rounds=[],
            baseline_summary=summary,
            baseline_source="agent_generated",
            baseline_generation={"status": "rejected"},
            status="baseline_generation_failed",
            stop_reason="judgment_rejected",
        )

        manifest = standard_worker_manifest(
            request=self.make_request(),
            contract_path=ROOT / "unused-contract.json",
            context_path=ROOT / "unused-context.json",
            loop_result=result,
            output_dir=ROOT / "outputs" / "unused_test",
        )

        self.assertEqual("baseline_generation_failed", manifest["status"])
        self.assertEqual("judgment_rejected", manifest["terminal_reason"])
        self.assertEqual(0, manifest["round_count"])

    def test_standard_loop_runtime_passes_configured_semantic_reviewer(self) -> None:
        summary = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id=None,
            best_metrics={"makespan": 100},
            best_candidate_id=None,
            best_candidate_metrics=None,
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={},
        )
        loop_result = WorkerLoopResult(
            baseline_key=(-100.0,),
            final_key=(-100.0,),
            final_worktree=ROOT,
            rounds=[],
            baseline_summary=summary,
        )
        request = self.make_request(output_dir=ROOT / "outputs" / "standard_loop_runtime", semantic_reviewer=object())

        with patch(
            "harness_agent.orchestration.standard.build_standard_worker_contract_payload",
            return_value={"task_id": "test"},
        ), patch(
            "harness_agent.orchestration.standard.TaskContract.load",
            return_value=SimpleNamespace(validate=lambda project_root: []),
        ), patch(
            "harness_agent.orchestration.standard.write_context_packet",
            return_value=request.output_dir / "context_packet.json",
        ), patch(
            "harness_agent.orchestration.standard.run_worker_loop",
            return_value=loop_result,
        ) as run_loop, patch(
            "harness_agent.orchestration.standard.standard_worker_manifest",
            return_value={
                "status": "ok",
                "terminal_reason": None,
                "baseline_key": [-100.0],
                "final_key": [-100.0],
                "promoted_rounds": 0,
                "artifacts": {},
            },
        ), patch(
            "harness_agent.orchestration.standard.render_standard_worker_report",
            return_value="",
        ):
            run_standard_worker_loop(request)

        self.assertIs(request.semantic_reviewer, run_loop.call_args.kwargs["semantic_reviewer"])

    def test_cli_run_worker_loop_passes_no_semantic_reviewer(self) -> None:
        args = build_parser().parse_args(
            [
                "run-worker-loop",
                "--contract",
                "configs/standard_fjsp_tiny.example.json",
                "--context-packet",
                "outputs/context.json",
                "--output-dir",
                "outputs/test",
            ]
        )
        summary = RunSummary(
            total=1,
            valid=1,
            failed=0,
            best_experiment_id=None,
            best_metrics={"makespan": 100},
            best_candidate_id=None,
            best_candidate_metrics=None,
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={},
        )
        loop_result = WorkerLoopResult(
            baseline_key=(-100.0,),
            final_key=(-90.0,),
            final_worktree=ROOT,
            rounds=[],
            baseline_summary=summary,
        )
        with patch("harness_agent.cli.load_runnable_contract", return_value=SimpleNamespace()), patch(
            "harness_agent.cli.make_worker", return_value=NullWorker()
        ), patch(
            "harness_agent.cli.make_main_agent", return_value=SimpleNamespace()
        ), patch(
            "harness_agent.cli.run_worker_loop", return_value=loop_result
        ) as run_loop, patch("harness_agent.cli.print_json"), patch(
            "harness_agent.cli.is_deepseek_configured"
        ) as deepseek_status:
            exit_code = run_worker_loop_cmd(args)

        self.assertEqual(0, exit_code)
        self.assertIsNone(run_loop.call_args.kwargs["semantic_reviewer"])
        deepseek_status.assert_not_called()

    def test_cli_standard_loop_configures_semantic_reviewer_for_full_guidance(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
                "--opencode-model",
                "qiming/deepseek-v4-flash",
            ]
        )
        manifest = {
            "status": "ok",
            "terminal_reason": None,
            "baseline_key": [-100.0],
            "final_key": [-90.0],
            "promoted_rounds": 1,
            "artifacts": {},
        }
        with patch("harness_agent.cli.make_worker", return_value=NullWorker()), patch(
            "harness_agent.cli.make_main_agent", return_value=SimpleNamespace()
        ), patch(
            "harness_agent.cli.run_standard_worker_loop", return_value=manifest
        ) as run_loop, patch("harness_agent.cli.print_json"), patch(
            "harness_agent.cli.is_deepseek_configured"
        ) as deepseek_status:
            exit_code = run_standard_worker_loop_cmd(args)

        self.assertEqual(0, exit_code)
        request = run_loop.call_args.args[0]
        self.assertIsInstance(request.semantic_reviewer, OpenCodeAlgorithmSemanticReviewer)
        self.assertEqual("qiming/deepseek-v4-flash", request.semantic_reviewer.model)
        self.assertEqual("qiming/deepseek-v4-flash", request.semantic_reviewer.model)
        deepseek_status.assert_not_called()

    def test_cli_standard_loop_passes_no_semantic_reviewer_for_none_guidance(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
                "--guidance-mode",
                "none",
            ]
        )
        manifest = {
            "status": "ok",
            "terminal_reason": None,
            "baseline_key": [-100.0],
            "final_key": [-90.0],
            "promoted_rounds": 1,
            "artifacts": {},
        }
        with patch("harness_agent.cli.make_worker", return_value=NullWorker()), patch(
            "harness_agent.cli.make_main_agent", return_value=SimpleNamespace()
        ), patch(
            "harness_agent.cli.run_standard_worker_loop", return_value=manifest
        ) as run_loop, patch("harness_agent.cli.print_json"), patch(
            "harness_agent.cli.is_deepseek_configured"
        ) as deepseek_status:
            exit_code = run_standard_worker_loop_cmd(args)

        self.assertEqual(0, exit_code)
        request = run_loop.call_args.args[0]
        self.assertIsNone(request.semantic_reviewer)
        deepseek_status.assert_not_called()

    def test_cli_standard_loop_bounds_worker_runtime_and_caps_main_planning(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
                "--max-runtime-seconds",
                "321",
            ]
        )
        manifest = {
            "status": "ok",
            "terminal_reason": None,
            "baseline_key": [-100.0],
            "final_key": [-90.0],
            "promoted_rounds": 1,
            "artifacts": {},
        }
        with patch("harness_agent.cli.make_worker", return_value=NullWorker()) as make_worker, patch(
            "harness_agent.cli.make_main_agent", return_value=SimpleNamespace()
        ) as make_main_agent, patch(
            "harness_agent.cli.run_standard_worker_loop", return_value=manifest
        ), patch("harness_agent.cli.print_json"):
            exit_code = run_standard_worker_loop_cmd(args)

        self.assertEqual(0, exit_code)
        self.assertEqual(321, make_worker.call_args.kwargs["timeout_seconds"])
        self.assertEqual(120, make_main_agent.call_args.kwargs["timeout_seconds"])

    def test_repair_targets_prefer_result_revalidation_top_errors_and_drop_semantic_blocks(self) -> None:
        attempt = {
            "agentic_judgment": {
                "accepted": False,
                "issues": ["candidate_result_revalidation_failed"],
                "suggestions": ["Repair the validator failure."],
                "checks": {
                    "result_revalidation": {
                        "passed": False,
                        "top_errors": ["missing makespan field", "invalid operation count"],
                    }
                },
            },
            "semantic_review": {
                "status": "repair_required",
                "accepted": False,
                "findings": [{"blocking": True, "category": "method_semantics"}],
            },
            "failure_signatures": ["candidate_result_revalidation_failed"],
        }

        targets = collect_current_round_repair_targets([attempt])
        feedback = current_round_repair_feedback(
            attempt_index=1,
            max_repair_attempts=3,
            previous_attempts=[attempt],
        )

        self.assertEqual(
            ["missing makespan field", "invalid operation count"],
            targets["result_revalidation_top_errors"],
        )
        self.assertNotIn("algorithm_semantic_review", targets)
        self.assertIn("result_revalidation_top_errors", feedback["repair_targets"])
        self.assertNotIn("algorithm_semantic_review", feedback["repair_targets"])
        self.assertTrue(
            any("result_revalidation_top_errors" in item for item in feedback["must_do"])
        )

    def test_cli_returns_failure_for_missing_valid_baseline(self) -> None:
        args = build_parser().parse_args(
            [
                "run-standard-worker-loop",
                "--instance-dir",
                "examples",
                "--output-dir",
                "outputs/test",
            ]
        )
        manifest = {
            "status": "baseline_generation_failed",
            "terminal_reason": "judgment_rejected",
            "baseline_key": [float("-inf")],
            "final_key": [float("-inf")],
            "promoted_rounds": 0,
            "artifacts": {},
        }
        with patch("harness_agent.cli.make_worker", return_value=NullWorker()), patch(
            "harness_agent.cli.is_deepseek_configured", return_value=False
        ), patch("harness_agent.cli.run_standard_worker_loop", return_value=manifest), patch(
            "harness_agent.cli.print_json"
        ) as print_json:
            exit_code = run_standard_worker_loop_cmd(args)

        self.assertEqual(1, exit_code)
        self.assertEqual("judgment_rejected", print_json.call_args.args[0]["terminal_reason"])


if __name__ == "__main__":
    unittest.main()
