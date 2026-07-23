from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_agent.cli import build_parser, run_standard_worker_loop_cmd, run_worker_loop_cmd
from harness_agent.core.runner import RunSummary, solver_time_limit_seconds
from harness_agent.orchestration.loop import (
    WorkerLoopResult,
    collect_current_round_repair_targets,
    current_round_repair_feedback,
)
from harness_agent.orchestration.standard import (
    StandardWorkerLoopRequest,
    build_standard_worker_contract_payload,
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
        self.assertFalse(hasattr(args, "solver"))
        self.assertFalse(hasattr(args, "baseline_source"))
        self.assertFalse(any(name.startswith("awls_") for name in vars(args)))

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

    def test_standard_loop_runtime_forces_semantic_reviewer_none(self) -> None:
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

        self.assertIsNone(run_loop.call_args.kwargs["semantic_reviewer"])

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

    def test_cli_standard_loop_passes_no_semantic_reviewer(self) -> None:
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
