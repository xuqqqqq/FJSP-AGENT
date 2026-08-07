from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.judgment import (
    _detect_agent_generated_solver_quality_risks,
    _detect_agent_generated_source_self_check_risks,
    judge_worker_result,
)
from harness_agent.worker import WorkerResult


class AgenticResultVerificationTests(unittest.TestCase):
    def _judge(
        self,
        *,
        source: str,
        changed_file: str = "examples/agent_generated_fjsp_solver.py",
        status: str = "completed",
        allowed_paths: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = root / changed_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        context_path = root / "context_packet.json"
        context_path.write_text(
            json.dumps(
                {
                    "task": {"problem_family": "FJSP"},
                    "evaluator_protocol": {
                        "solver_command_template": (
                            "python examples/agent_generated_fjsp_solver.py --input {instance} "
                            "--output {solution} --seed {seed}"
                        )
                    },
                    "edit_policy": {
                        "allowed_paths": allowed_paths if allowed_paths is not None else ["examples"],
                        "forbidden_paths": forbidden_paths if forbidden_paths is not None else ["outputs", ".git"],
                    },
                }
            ),
            encoding="utf-8",
        )
        return judge_worker_result(
            worker_result=WorkerResult(status=status, changed_files=[changed_file], summary="test candidate"),
            worktree_path=root,
            context_packet_path=context_path,
            output_dir=root / "review",
            apply_worker_changes=True,
        )

    def _quality_context(self, *, distributed: bool = False, priority: bool = False) -> dict[str, object]:
        active_features = [
            "alternative_machines",
            "operation_precedence",
            "machine_capacity",
            "makespan_objective",
        ]
        variant_required: list[str] = []
        if distributed:
            active_features.extend(
                [
                    "fjsp_distributed_transfer",
                    "factory_assignment",
                    "transfer_time",
                    "energy_consumption",
                    "factory_workload",
                ]
            )
            variant_required.extend(
                [
                    "factory_assignment_guard",
                    "transfer_time_precedence_guard",
                    "factory_machine_eligibility_guard",
                    "distributed_machine_non_overlap_guard",
                    "energy_and_workload_metric_guard",
                ]
            )
        if priority:
            active_features.extend(
                [
                    "fjsp_job_priority",
                    "fjsp_priority",
                    "job_priority",
                    "priority_jobs",
                    "priority_completion_time",
                    "multi_objective",
                    "lexicographic_objective",
                ]
            )
            variant_required.extend(
                [
                    "priority_tail_parser_guard",
                    "priority_job_identity_guard",
                    "priority_completion_metric_guard",
                    "lexicographic_priority_objective_guard",
                    "priority_aware_dispatch_guard",
                ]
            )
        return {
            "task": {
                "problem_family": (
                    "fjsp_distributed_transfer"
                    if distributed
                    else "fjsp_job_priority" if priority else "FJSP"
                ),
            },
            "instance_diagnostics": {
                "status": "available",
                "summary": {
                    "profiled_count": 1,
                    "distributed_transfer_instance_count": 1 if distributed else 0,
                    "priority_job_instance_count": 1 if priority else 0,
                    "priority_job_count_max": 3 if priority else 0,
                },
                "instances": [
                    {
                        "variant": (
                            "fjsp_distributed_transfer"
                            if distributed
                            else "fjsp_priority" if priority else "standard_fjsp"
                        ),
                        "has_distributed_transfer": distributed,
                        "has_job_priority": priority,
                        "priority_job_count": 3 if priority else 0,
                    }
                ],
            },
            "agent_generated_solver_quality_contract": {
                "enabled": True,
                "active_features": active_features,
                "required_code_capabilities": [],
                "variant_required_code_capabilities": variant_required,
            },
        }

    def test_accepts_compilable_candidate_without_semantic_source_shape(self) -> None:
        judgment = self._judge(source="def solve():\n    return []\n")

        self.assertTrue(judgment.accepted)
        self.assertNotIn("agent_generated_solver_self_check_incomplete", judgment.issues)
        self.assertNotIn("agent_generated_solver_quality_contract_missing", judgment.issues)

    def test_rejects_python_syntax_error(self) -> None:
        judgment = self._judge(source="def solve(:\n    pass\n")

        self.assertFalse(judgment.accepted)
        self.assertTrue(any(issue.startswith("python_syntax_error") for issue in judgment.issues))

    def test_rejects_change_outside_allowed_paths(self) -> None:
        judgment = self._judge(source="VALUE = 1\n", changed_file="src/solver.py")

        self.assertFalse(judgment.accepted)
        self.assertIn("changed_files_outside_edit_policy", judgment.issues)
        self.assertEqual(["src/solver.py"], judgment.checks["path_policy_violations"])

    def test_rejects_forbidden_path_even_when_parent_is_allowed(self) -> None:
        judgment = self._judge(
            source="VALUE = 1\n",
            changed_file="examples/standard_fjsp_evaluator.py",
            allowed_paths=["examples"],
            forbidden_paths=["examples/standard_fjsp_evaluator.py"],
        )

        self.assertFalse(judgment.accepted)
        self.assertIn("changed_files_outside_edit_policy", judgment.issues)

    def test_rejects_agent_generated_solver_importing_backend(self) -> None:
        judgment = self._judge(source="from harness_agent.domains.io import load_solution\n")

        self.assertFalse(judgment.accepted)
        self.assertIn("agent_generated_solver_imports_backend_package", judgment.issues)

    def test_rejects_obvious_hardcoded_instance_metadata(self) -> None:
        judgment = self._judge(
            source=(
                "def parse_instance(path):\n"
                "    op_info = {(0, 0): {'eligible': {0: 3}}}\n"
                "    return {'op_info': op_info}\n"
            )
        )

        self.assertFalse(judgment.accepted)
        self.assertIn("agent_generated_solver_hardcodes_instance_data", judgment.issues)

    def test_timeout_with_compilable_diff_is_not_rejected_by_timeout_alone(self) -> None:
        judgment = self._judge(source="VALUE = 1\n", status="timeout")

        self.assertTrue(judgment.accepted)
        self.assertIn("worker_timeout_after_code_change", judgment.checks["proposal_audit_warnings"])

    def test_distributed_quality_risks_detect_missing_factory_transfer_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(
                (
                    "def validate_schedule(schedule):\n"
                    "    seen_ops = set()\n"
                    "    machine_intervals = {}\n"
                    "    for rec in schedule:\n"
                    "        seen_ops.add((rec['job_id'], rec['op_id']))\n"
                    "        machine_intervals.setdefault(rec['machine_id'], []).append((rec['start'], rec['end']))\n"
                    "    return True\n"
                ),
                encoding="utf-8",
            )
            quality_contract = self._quality_context(distributed=True)["agent_generated_solver_quality_contract"]

            risks = _detect_agent_generated_solver_quality_risks(
                context=self._quality_context(distributed=True),
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract=quality_contract,
            )
            self_check_risks = _detect_agent_generated_source_self_check_risks(
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract=quality_contract,
            )

        self.assertTrue(
            any("missing distributed-transfer capabilities" in risk for risk in risks),
            risks,
        )
        self.assertTrue(
            any("factory_assignment_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )
        self.assertTrue(
            any("transfer_time_precedence_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )

    def test_distributed_quality_risks_accept_source_with_factory_transfer_energy_guards(self) -> None:
        source = (
            "SAME_FACTORY_TRANSFER = 30\n"
            "CROSS_FACTORY_TRANSFER = 60\n"
            "TRANSFER_UNIT_ENERGY = 6\n"
            "def transfer_time(prev, rec):\n"
            "    if prev['factory_id'] != rec['factory_id']:\n"
            "        return CROSS_FACTORY_TRANSFER\n"
            "    if prev['machine_id'] != rec['machine_id']:\n"
            "        return SAME_FACTORY_TRANSFER\n"
            "    return 0\n"
            "def validate_schedule(schedule, candidates):\n"
            "    seen_ops = set()\n"
            "    by_job = {}\n"
            "    machine_intervals = {}\n"
            "    factory_workload = {}\n"
            "    total_energy = 0\n"
            "    for rec in schedule:\n"
            "        op_key = (rec['job_id'], rec['op_id'])\n"
            "        if op_key in seen_ops:\n"
            "            return False\n"
            "        seen_ops.add(op_key)\n"
            "        factory_id = rec['factory_id']\n"
            "        machine_id = rec['machine_id']\n"
            "        if (factory_id, machine_id) not in candidates[op_key]:\n"
            "            return False\n"
            "        duration, unit_energy = candidates[op_key][(factory_id, machine_id)]\n"
            "        if rec['end'] - rec['start'] != duration:\n"
            "            return False\n"
            "        by_job.setdefault(rec['job_id'], []).append(rec)\n"
            "        factory_machine = (factory_id, machine_id)\n"
            "        machine_intervals.setdefault(factory_machine, []).append((rec['start'], rec['end']))\n"
            "        factory_workload[factory_id] = factory_workload.get(factory_id, 0) + duration\n"
            "        total_energy += duration * unit_energy\n"
            "    for job_id, records in by_job.items():\n"
            "        records.sort(key=lambda item: item['op_id'])\n"
            "        for prev, rec in zip(records, records[1:]):\n"
            "            if rec['start'] < prev['end'] + transfer_time(prev, rec):\n"
            "                return False\n"
            "            total_energy += transfer_time(prev, rec) * TRANSFER_UNIT_ENERGY\n"
            "    for factory_machine, intervals in machine_intervals.items():\n"
            "        intervals.sort()\n"
            "        for prev, curr in zip(intervals, intervals[1:]):\n"
            "            if prev[1] > curr[0]:\n"
            "                return False\n"
            "    max_factory_workload = max(factory_workload.values()) if factory_workload else 0\n"
            "    total_energy_consumption = total_energy\n"
            "    return max_factory_workload >= 0 and total_energy_consumption >= 0\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(source, encoding="utf-8")
            quality_contract = self._quality_context(distributed=True)["agent_generated_solver_quality_contract"]

            self_check_risks = _detect_agent_generated_source_self_check_risks(
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract=quality_contract,
            )

        self.assertFalse(
            any("factory_assignment_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )
        self.assertFalse(
            any("energy_and_workload_metric_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )

    def test_priority_quality_risks_detect_missing_priority_tail_and_objective_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(
                (
                    "def validate_schedule(schedule):\n"
                    "    seen_ops = set()\n"
                    "    for rec in schedule:\n"
                    "        seen_ops.add((rec['job_id'], rec['op_id']))\n"
                    "    return bool(seen_ops)\n"
                ),
                encoding="utf-8",
            )
            quality_contract = self._quality_context(priority=True)["agent_generated_solver_quality_contract"]

            risks = _detect_agent_generated_solver_quality_risks(
                context=self._quality_context(priority=True),
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract=quality_contract,
            )
            self_check_risks = _detect_agent_generated_source_self_check_risks(
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract=quality_contract,
            )

        self.assertTrue(any("missing job-priority capabilities" in risk for risk in risks), risks)
        self.assertTrue(any("priority_tail_parser_guard" in risk for risk in self_check_risks), self_check_risks)
        self.assertTrue(
            any("priority_completion_metric_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )

    def test_priority_quality_risks_accept_source_with_priority_tail_metric_and_dispatch(self) -> None:
        source = (
            "import math\n"
            "def parse_instance(path):\n"
            "    tokens = [int(x) for x in open(path).read().split()]\n"
            "    job_count = tokens[0]\n"
            "    remaining_tokens = tokens[10:]\n"
            "    priority_count = math.ceil(job_count / 4)\n"
            "    priority_tail = remaining_tokens[-(priority_count + 1):]\n"
            "    k = priority_tail[0]\n"
            "    priority_job_ids = [raw - 1 for raw in priority_tail[1:]]\n"
            "    for job_id in priority_job_ids:\n"
            "        if job_id < 0 or job_id >= job_count:\n"
            "            raise ValueError('priority job id out of range')\n"
            "    return job_count, set(priority_job_ids)\n"
            "def priority_completion_time(schedule, priority_jobs):\n"
            "    completion = [rec['end'] for rec in schedule if rec['job_id'] in priority_jobs]\n"
            "    return max(completion) if completion else 0\n"
            "def objective_tuple(schedule, priority_jobs):\n"
            "    makespan = max(rec['end'] for rec in schedule)\n"
            "    return (makespan, priority_completion_time(schedule, priority_jobs))\n"
            "def dispatch_score(ready, priority_jobs):\n"
            "    priority_score = -1000 if ready['job_id'] in priority_jobs else 0\n"
            "    return priority_score + ready['duration']\n"
            "def validate_schedule(schedule, path):\n"
            "    job_count, priority_jobs = parse_instance(path)\n"
            "    ready = {'job_id': next(iter(priority_jobs)), 'duration': 1}\n"
            "    return objective_tuple(schedule, priority_jobs) >= (0, 0) and dispatch_score(ready, priority_jobs) <= 1\n"
            "def main():\n"
            "    validate_schedule([{'job_id': 0, 'op_id': 0, 'end': 1}], 'instance.txt')\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(source, encoding="utf-8")
            quality_contract = self._quality_context(priority=True)["agent_generated_solver_quality_contract"]

            self_check_risks = _detect_agent_generated_source_self_check_risks(
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract=quality_contract,
            )

        self.assertFalse(
            any("priority_tail_parser_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )
        self.assertFalse(
            any("priority_completion_metric_guard" in risk for risk in self_check_risks),
            self_check_risks,
        )


if __name__ == "__main__":
    unittest.main()
