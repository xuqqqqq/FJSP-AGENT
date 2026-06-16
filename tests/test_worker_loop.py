from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context_packet import ContextPacketRequest, write_context_packet
from harness_agent.loop_runner import run_worker_loop
from harness_agent.models import TaskContract
from harness_agent.worker import NullWorker, WorkerCapabilities, WorkerResult


ROOT = Path(__file__).resolve().parents[1]


class ImproveOnceWorker:
    """Test worker that improves the dummy solver once inside the candidate tree."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="improve-once",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        solver_path = Path(spec.worktree_path) / "examples" / "dummy_solver.py"
        text = solver_path.read_text(encoding="utf-8")
        changed_files: list[str] = []
        if "10 + args.seed" in text:
            solver_path.write_text(text.replace("10 + args.seed", "8 + args.seed"), encoding="utf-8")
            changed_files = ["examples/dummy_solver.py"]
        return WorkerResult(
            status="ok",
            changed_files=changed_files,
            summary="Improve the dummy end time if the baseline expression is still present.",
        )


class WorkerLoopTests(unittest.TestCase):
    def test_refreshed_context_records_previous_round_and_duplicate_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=NullWorker(),
                experiment_id="test_null_loop",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertEqual((990.0, -0.01), result.baseline_key)
            self.assertEqual((990.0, -0.01), result.final_key)
            self.assertEqual(["rolled_back", "rolled_back"], [item.decision for item in result.rounds])
            self.assertEqual([False, True], [item.duplicate_proposal for item in result.rounds])

            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            self.assertEqual("worker_loop_round_feedback", round_001_context["refresh_reason"])
            self.assertEqual(1, round_001_context["loop_feedback"]["round_index"])
            self.assertEqual("rolled_back", round_001_context["loop_feedback"]["previous_rounds"][0]["decision"])
            self.assertTrue(round_001_context["worker_instruction"]["round_feedback_rule"])

    def test_loop_promotes_only_strict_objective_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=ImproveOnceWorker(),
                experiment_id="test_improve_once",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            self.assertEqual((990.0, -0.01), result.baseline_key)
            self.assertEqual((992.0, -0.01), result.final_key)
            self.assertEqual(["promoted", "rolled_back"], [item.decision for item in result.rounds])
            self.assertEqual([False, False], [item.duplicate_proposal for item in result.rounds])


def _write_test_context(tmp_path: Path) -> Path:
    output_path = tmp_path / "context_packet.json"
    return write_context_packet(
        ContextPacketRequest(
            contract_path=ROOT / "configs" / "task_contract.example.json",
            output_path=output_path,
            docs=[ROOT / "README.md"],
            hypothesis="Worker-loop regression test context.",
        )
    )


if __name__ == "__main__":
    unittest.main()
