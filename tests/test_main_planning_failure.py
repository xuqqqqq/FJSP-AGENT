from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_agent.agents.main import MainPlanningError
from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.core.cancellation import TaskCancelled
from harness_agent.core.models import TaskContract
from harness_agent.core.runner import RunSummary
from harness_agent.orchestration.loop import (
    ControllerBudgetExhausted,
    LoopRoundRecord,
    WorkerLoopResult,
    plan_agent_generated_baseline_direction,
    plan_direction_with_fallback,
    run_worker_loop,
)
from harness_agent.orchestration.standard import StandardWorkerLoopRequest, run_standard_worker_loop
from harness_agent.worker import NullWorker


ROOT = Path(__file__).resolve().parents[1]


class FailingPlanner:
    def __init__(self) -> None:
        self.calls = []

    def plan_direction(self, request):
        self.calls.append(request)
        raise RuntimeError("planner provider unavailable")


class MainPlanningFailureTests(unittest.TestCase):
    def call_planner(self, phase, *, planner, context_path, output_dir):
        if phase == "baseline":
            return plan_agent_generated_baseline_direction(
                planner=planner, context_packet_path=context_path, output_dir=output_dir,
            )
        return plan_direction_with_fallback(
            planner=planner, round_index=0, context_packet_path=context_path,
            loop_feedback={}, output_dir=output_dir,
        )

    def test_none_without_history_wraps_fallback_failure_and_preserves_cause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "context.json"
            context.write_text(json.dumps({"guidance_ablation": {"mode": "none"}}), encoding="utf-8")
            for phase in ("round", "baseline"):
                with self.subTest(phase=phase):
                    planner = FailingPlanner()
                    output_dir = root / phase
                    with self.assertRaises(MainPlanningError) as raised:
                        self.call_planner(
                            phase, planner=planner, context_path=context, output_dir=output_dir,
                        )
                    error = raised.exception
                    self.assertIsInstance(error.__cause__, RuntimeError)
                    self.assertIn("historical", str(error.__cause__))
                    self.assertIn("planner provider unavailable", (output_dir / "planner_exception.txt").read_text())
                    self.assertEqual(1, len(planner.calls))
                    self.assertFalse((output_dir / "direction_plan.json").exists())

    def test_planning_control_stops_propagate_from_primary_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for phase in ("round", "baseline"):
                for stop_type in (TaskCancelled, ControllerBudgetExhausted):
                    for failing_stage in ("primary", "fallback"):
                        with self.subTest(phase=phase, stop=stop_type, stage=failing_stage):
                            stop = stop_type("stop planning")
                            planner = SimpleNamespace(plan_direction=Mock(side_effect=(
                                stop if failing_stage == "primary" else RuntimeError("provider unavailable")
                            )))
                            with patch("harness_agent.orchestration.loop.EvidenceDrivenMainAgent") as fallback:
                                fallback.return_value.plan_direction.side_effect = stop
                                with self.assertRaises(stop_type) as raised:
                                    self.call_planner(
                                        phase, planner=planner, context_path=Path(tmp) / "unused.json",
                                        output_dir=Path(tmp) / phase / stop_type.__name__ / failing_stage,
                                    )
                                self.assertIs(stop, raised.exception)
                                self.assertEqual(failing_stage == "fallback", fallback.called)

    def test_typed_fallback_reason_and_details_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failure = MainPlanningError("missing_method_history", "No historical methods.", details={"required": 3})
            with patch("harness_agent.orchestration.loop.EvidenceDrivenMainAgent") as fallback:
                fallback.return_value.plan_direction.side_effect = failure
                with self.assertRaises(MainPlanningError) as raised:
                    self.call_planner(
                        "round", planner=FailingPlanner(), context_path=root / "unused.json", output_dir=root,
                    )
            self.assertIs(failure, raised.exception.__cause__)
            self.assertEqual(failure.reason, raised.exception.reason)
            self.assertEqual(3, raised.exception.details["required"])
            self.assertIn("provider unavailable", raised.exception.details["planner_error"])

    def test_original_typed_failure_survives_unavailable_history_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "context.json"
            context.write_text(json.dumps({"guidance_ablation": {"mode": "none"}}), encoding="utf-8")
            for phase in ("round", "baseline"):
                for reason in ("timeout", "zero_event_stream"):
                    with self.subTest(phase=phase, reason=reason):
                        details = {"timeout_seconds": 47, "event_stream_bytes": 0}
                        failure = MainPlanningError(reason, "Original planner failed.", details=details)
                        planner = SimpleNamespace(plan_direction=Mock(side_effect=failure))
                        with self.assertRaises(MainPlanningError) as raised:
                            self.call_planner(
                                phase, planner=planner, context_path=context, output_dir=root / phase / reason,
                            )
                        error = raised.exception
                        self.assertEqual(reason, error.reason)
                        for key, value in details.items():
                            self.assertEqual(value, error.details[key])
                        self.assertIn(str(failure), str(error))
                        self.assertIn("historical", error.details["fallback_failure"]["message"])
                        self.assertIsInstance(error.__cause__, RuntimeError)
                        self.assertEqual(str(error.__cause__), error.details["fallback_failure"]["message"])
                        self.assertEqual(details, failure.details)

    def test_fallback_artifact_errors_propagate_without_planning_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("harness_agent.orchestration.loop.EvidenceDrivenMainAgent") as fallback, patch(
                "harness_agent.orchestration.loop.write_direction_plan", side_effect=OSError("artifact write failed"),
            ):
                fallback.return_value.plan_direction.return_value = {"direction_id": "d000"}
                with self.assertRaisesRegex(OSError, "artifact write failed"):
                    self.call_planner(
                        "round", planner=FailingPlanner(), context_path=root / "unused.json", output_dir=root,
                    )

    def test_real_loop_planning_failure_keeps_legal_incumbent_and_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            contract = replace(contract, budget=replace(contract.budget, rounds=1, seeds=[0]))
            context = write_context_packet(ContextPacketRequest(
                contract_path=ROOT / "configs" / "task_contract.example.json",
                output_path=root / "context.json", project_root=ROOT, guidance_mode="none",
            ))
            planner = FailingPlanner()
            worker = Mock(wraps=NullWorker())
            result = run_worker_loop(
                contract=contract, project_root=ROOT, output_dir=root / "loop",
                context_packet_path=context, worker=worker, main_agent=planner,
                experiment_id="planning_failure", iterations=3, max_steps=1,
                max_runtime_seconds=30, apply_worker_changes=False,
            )

            self.assertEqual("failed", result.status)
            self.assertEqual("main_planning_failed", result.stop_reason)
            self.assertEqual([], result.rounds)
            self.assertEqual(1, result.baseline_summary.valid)
            self.assertEqual(result.baseline_key, result.final_key)
            self.assertIsNotNone(result.best_legal_incumbent)
            self.assertEqual(result.final_key, result.best_legal_incumbent.objective_key)
            self.assertEqual(result.final_worktree, result.best_legal_incumbent.worktree)
            self.assertEqual(
                (ROOT / "examples" / "dummy_solver.py").read_bytes(),
                (result.final_worktree / "examples" / "dummy_solver.py").read_bytes(),
            )
            self.assertEqual(1, len(planner.calls))
            worker.run_experiment.assert_not_called()
            stored = json.loads((root / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", stored["status"])
            self.assertEqual("main_planning_failed", stored["stop_reason"])
            self.assertEqual(list(result.final_key), stored["final_key"])
            for name in ("loop_report.md", "candidate_pool.json", "best_history.json", "hypothesis_graph.json"):
                self.assertTrue((root / "loop" / name).is_file(), name)
            failure = json.loads((root / "loop" / "round_000" / "main_agent" / "planning_failure.json").read_text())
            self.assertEqual("main_planning_failed", failure["stop_reason"])
            self.assertTrue(Path(failure["exception_path"]).is_file())

    def test_resumed_planning_and_revision_failures_keep_history_and_promoted_incumbent(self) -> None:
        for phase in ("round", "revision"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                incumbent = root / "promoted_incumbent"
                incumbent.mkdir()
                context = root / "context.json"
                context.write_text(json.dumps({"guidance_ablation": {"mode": "none"}}), encoding="utf-8")
                prior_round = LoopRoundRecord(
                    round_index=0, decision="promoted", candidate_key=(-110.0,), incumbent_key_after=(-110.0,),
                    worker_status="ok", worker_changed_files=[], proposal_fingerprint="saved", duplicate_proposal=False,
                    proposal_diagnostics={}, candidate_summary={}, smoke_gate={}, promotion_check={},
                    cycle_dir="", context_packet_path="", delta_path="", patch_path="",
                    promoted_worktree=str(incumbent), direction_plan={"direction_id": "d000"},
                )
                resume = WorkerLoopResult(
                    baseline_key=(-120.0,), final_key=(-110.0,), final_worktree=incumbent,
                    rounds=[prior_round], baseline_source="agent_generated",
                    baseline_summary=RunSummary(
                        total=1, valid=1, failed=0, best_experiment_id="baseline", best_metrics={"makespan": 120},
                    ),
                    candidate_pool=[{"candidate_record_id": "saved_candidate"}],
                    best_history=[{"revision": 1, "objective_key": [-110.0]}],
                )
                responses = ([{"direction_id": "d001"}] if phase == "revision" else []) + [
                    RuntimeError("planner provider unavailable"),
                ]
                planner = SimpleNamespace(plan_direction=Mock(side_effect=responses))
                with patch("harness_agent.orchestration.loop.run_agent_generated_baseline") as generate, patch(
                    "harness_agent.orchestration.loop.run_competing_worker_cycles"
                ) as compete:
                    result = run_worker_loop(
                        contract=TaskContract.load(ROOT / "configs" / "standard_fjsp_tiny.example.json"),
                        project_root=ROOT, output_dir=root / "loop", context_packet_path=context,
                        worker=NullWorker(), main_agent=planner, resume_from=resume,
                        round_intervention=lambda *_: "Revise the experiment.", experiment_id="resumed_failure",
                        iterations=2, max_steps=1, max_runtime_seconds=30, apply_worker_changes=False,
                    )
                generate.assert_not_called()
                compete.assert_not_called()
                self.assertEqual("failed", result.status)
                self.assertEqual("main_planning_failed", result.stop_reason)
                self.assertEqual(resume.rounds, result.rounds)
                self.assertEqual(resume.final_key, result.final_key)
                self.assertEqual(resume.final_worktree.resolve(), result.final_worktree)
                self.assertEqual(resume.final_key, result.best_legal_incumbent.objective_key)
                self.assertEqual(resume.candidate_pool, result.candidate_pool)
                self.assertEqual(resume.best_history, result.best_history)
                failure_dir = "main_agent_user_revision" if phase == "revision" else "main_agent"
                self.assertTrue((root / "loop" / "round_001" / failure_dir / "planning_failure.json").is_file())

    def test_standard_baseline_planning_failure_completes_manifest_without_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planner = FailingPlanner()
            worker = Mock(wraps=NullWorker())
            manifest = run_standard_worker_loop(StandardWorkerLoopRequest(
                docs=[], instance_dir=ROOT / "examples", pattern="standard_fjsp_tiny.fjs",
                output_dir=root, project_root=ROOT, worker=worker, main_agent=planner,
                guidance_mode="none", seeds=[0], iterations=3, in_round_repair_attempts=0,
            ))

            self.assertEqual("failed", manifest["status"])
            self.assertEqual("main_planning_failed", manifest["terminal_reason"])
            self.assertEqual(0, manifest["round_count"])
            self.assertIsNone(manifest["best_legal_incumbent"])
            self.assertEqual(1, len(planner.calls))
            worker.run_experiment.assert_not_called()
            for name, path in manifest["artifacts"].items():
                self.assertTrue(Path(path).is_file(), name)
            stored = json.loads(Path(manifest["artifacts"]["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual("failed", stored["status"])
            loop_result = json.loads(Path(manifest["artifacts"]["loop_result"]).read_text(encoding="utf-8"))
            self.assertEqual("main_planning_failed", loop_result["stop_reason"])
            self.assertFalse(loop_result["baseline_generation"].get("direction_plan"))
            self.assertFalse(loop_result["baseline_generation"]["accepted_as_incumbent"])

    def test_loop_does_not_swallow_unrelated_planning_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "context.json"
            context.write_text("{}", encoding="utf-8")
            with patch("harness_agent.orchestration.loop._run_harness") as evaluate, patch(
                "harness_agent.orchestration.loop.plan_direction_with_fallback",
                side_effect=OSError("cannot write planning artifacts"),
            ):
                evaluate.return_value = RunSummary(
                    total=1, valid=1, failed=0, best_experiment_id="baseline", best_metrics={"primary_score": 1},
                )
                with self.assertRaisesRegex(OSError, "cannot write planning artifacts"):
                    run_worker_loop(
                        contract=TaskContract.load(ROOT / "configs" / "task_contract.example.json"),
                        project_root=ROOT, output_dir=root / "loop", context_packet_path=context,
                        worker=NullWorker(), experiment_id="unrelated_error", iterations=1,
                        max_steps=1, max_runtime_seconds=30, apply_worker_changes=False,
                    )


if __name__ == "__main__":
    unittest.main()
