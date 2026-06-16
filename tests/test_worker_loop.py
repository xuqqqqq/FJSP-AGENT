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


class ProposalAuditWorker:
    """Test worker that writes a structured proposal artifact without changing files."""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="proposal-audit",
            supports_code_generation=True,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec) -> WorkerResult:  # noqa: ANN001 - follows the worker protocol surface.
        output_dir = Path(spec.output_dir or spec.worktree_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(
            json.dumps(
                {
                    "summary": "Try a solver-rule change based on the project intake.",
                    "strategy_intent": "Prefer solver-side changes and leave validators untouched.",
                    "context_usage": {
                        "used_project_intake": True,
                        "referenced_files": ["examples/dummy_solver.py", "configs/task_contract.example.json"],
                        "notes": "Used intake to identify the dummy solver entry point.",
                    },
                    "proposal_audit": {
                        "project_intake_present": True,
                        "project_intake_status": "ok",
                        "declared_project_intake_used": True,
                        "detected_referenced_intake_files": ["examples/dummy_solver.py"],
                        "changed_core_algorithm_files": ["examples/dummy_solver.py"],
                        "changed_validator_files": [],
                        "changed_benchmark_files": [],
                        "referenced_test_commands": ["python -m compileall harness_agent examples"],
                        "warnings": [],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return WorkerResult(
            status="proposal_created",
            changed_files=[],
            summary="Proposal artifact was written for diagnostics.",
            artifacts={"proposal": str(proposal_path)},
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
            self.assertEqual("missing", result.rounds[0].proposal_diagnostics["status"])

            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            self.assertEqual("worker_loop_round_feedback", round_001_context["refresh_reason"])
            self.assertEqual(1, round_001_context["loop_feedback"]["round_index"])
            self.assertEqual("rolled_back", round_001_context["loop_feedback"]["previous_rounds"][0]["decision"])
            self.assertTrue(round_001_context["worker_instruction"]["round_feedback_rule"])

            round_001_delta = json.loads((tmp_path / "loop" / "round_001" / "worker_worktree_delta.json").read_text(encoding="utf-8"))
            self.assertEqual(0, round_001_delta["counts"]["total_changed"])

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

            round_000_delta = json.loads((tmp_path / "loop" / "round_000" / "worker_worktree_delta.json").read_text(encoding="utf-8"))
            self.assertEqual(1, round_000_delta["counts"]["modified"])
            self.assertEqual("examples/dummy_solver.py", round_000_delta["modified"][0]["path"])
            round_000_patch = (tmp_path / "loop" / "round_000" / "worker_changes.patch").read_text(encoding="utf-8")
            self.assertIn("examples/dummy_solver.py", round_000_patch)
            self.assertIn("8 + args.seed", round_000_patch)

    def test_proposal_diagnostics_feed_next_round_context_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = TaskContract.load(ROOT / "configs" / "task_contract.example.json")
            context_path = _write_test_context(tmp_path)

            result = run_worker_loop(
                contract=contract,
                project_root=ROOT,
                output_dir=tmp_path / "loop",
                context_packet_path=context_path,
                worker=ProposalAuditWorker(),
                experiment_id="test_proposal_diagnostics",
                iterations=2,
                max_steps=1,
                max_runtime_seconds=30,
                apply_worker_changes=False,
            )

            diagnostics = result.rounds[0].proposal_diagnostics
            self.assertEqual("ok", diagnostics["status"])
            self.assertTrue(diagnostics["context_usage"]["used_project_intake"])
            self.assertEqual(["examples/dummy_solver.py"], diagnostics["proposal_audit"]["changed_core_algorithm_files"])

            round_001_context = json.loads((tmp_path / "loop" / "round_001" / "context_packet.json").read_text(encoding="utf-8"))
            previous = round_001_context["loop_feedback"]["previous_rounds"][0]
            self.assertEqual("ok", previous["proposal_diagnostics"]["status"])
            self.assertTrue(previous["proposal_diagnostics"]["context_usage"]["used_project_intake"])
            self.assertEqual(
                ["examples/dummy_solver.py"],
                previous["proposal_diagnostics"]["proposal_audit"]["changed_core_algorithm_files"],
            )

            loop_result = json.loads((tmp_path / "loop" / "loop_result.json").read_text(encoding="utf-8"))
            self.assertEqual("ok", loop_result["rounds"][0]["proposal_diagnostics"]["status"])


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
