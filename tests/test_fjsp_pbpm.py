from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harness_agent.agents.quality_contract import build_solver_runtime_feature_contract
from harness_agent.domains.families import get_domain_pack
from harness_agent.domains.io import ScheduleRecord, StandardFjspInstance, parse_standard_fjsp, validate_standard_schedule
from harness_agent.orchestration.standard import fixed_problem_contract
from harness_agent.web.server import inspect_instance_profile


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / "examples" / "fjsp_pbpm_sfjs10.pbpm.txt"


def sequential_schedule(
    instance: StandardFjspInstance,
    *,
    grouped_jobs: tuple[int, ...] = (),
) -> list[ScheduleRecord]:
    schedule: list[ScheduleRecord] = []
    batch_machines = dict(instance.batch_machine_capacities)
    next_batch_id = 1
    current = 0

    if grouped_jobs:
        durations = []
        for job_id in grouped_jobs:
            candidate = next(
                item for item in instance.jobs[job_id].operations[0].candidates if item.machine_id == 0
            )
            durations.append(candidate.duration)
        current = max(durations)
        schedule.extend(
            ScheduleRecord(job_id=job_id, op_id=0, machine_id=0, start=0, end=current, batch_id=0)
            for job_id in grouped_jobs
        )

    for job in instance.jobs:
        for operation in job.operations:
            if operation.op_id == 0 and job.job_id in grouped_jobs:
                continue
            candidate = operation.candidates[0]
            end = current + candidate.duration
            batch_id = next_batch_id if candidate.machine_id in batch_machines else None
            schedule.append(
                ScheduleRecord(
                    job_id=job.job_id,
                    op_id=operation.op_id,
                    machine_id=candidate.machine_id,
                    start=current,
                    end=end,
                    batch_id=batch_id,
                )
            )
            current = end
            if batch_id is not None:
                next_batch_id += 1
    return schedule


class FjspPbpmTests(unittest.TestCase):
    def test_parser_reads_fattahi_prefix_and_pbpm_tail(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)

        self.assertEqual("fjsp_pbpm", instance.variant)
        self.assertEqual((4, 5, 12), (instance.job_count, instance.machine_count, instance.operation_count))
        self.assertEqual(((0, 2), (2, 2), (4, 2)), instance.batch_machine_capacities)
        self.assertEqual((1, 0, 1, 0), instance.job_family_ids)

    def test_parser_rejects_missing_and_trailing_family_tokens(self) -> None:
        text = INSTANCE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            malformed = {
                "missing": "\n".join(text.splitlines()[:-1]),
                "trailing": text + "\n99\n",
            }
            for name, payload in malformed.items():
                with self.subTest(name=name):
                    path = tmp_path / f"{name}.pbpm.txt"
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        parse_standard_fjsp(path)

    def test_web_core_and_domain_pack_route_pbpm_contract(self) -> None:
        profile = inspect_instance_profile(INSTANCE)
        self.assertEqual("examples/fjsp_pbpm_evaluator.py", profile["fixed_evaluator"])
        self.assertEqual(3, profile["batch_machine_count"])
        self.assertTrue(
            {"batching", "parallel_batch_machine", "batch_capacity", "incompatible_job_families"}
            .issubset(profile["variant_features"])
        )

        _, evaluator, objectives = fixed_problem_contract([INSTANCE])
        self.assertEqual("examples/fjsp_pbpm_evaluator.py", evaluator)
        self.assertEqual(["makespan"], [item["name"] for item in objectives])

        pack = get_domain_pack("fjsp_pbpm")
        self.assertIsNotNone(pack)
        self.assertIsNotNone(pack.worker_implementation_skill("fjsp-pbpm-adapter-worker"))
        self.assertIsNotNone(pack.method_package("fjsp_pbpm_adaptation"))

    def test_pbpm_method_contract_rejects_degenerate_activation_and_wrong_cp_api(self) -> None:
        pack = get_domain_pack("fjsp_pbpm")
        package = pack.method_package("fjsp_pbpm_adaptation")
        contract = package.implementation_contract

        self.assertIn("only_enforce_if", contract["exact_api_rule"])
        self.assertIn("only_if/OnlyIf 不存在", contract["exact_api_rule"])
        self.assertIn("candidate duration 等式必须由对应 presence 控制", contract["optional_candidate_rule"])
        self.assertIn("presence == sum(members)", contract["batch_slot_rule"])
        self.assertIn("最长单个 job 时长和不是安全上界", contract["horizon_rule"])
        self.assertIn("合法单件批可作为 baseline 进入正式竞争", contract["completion_rule"])
        self.assertIn("IntervalVar", contract["no_overlap_interval_rule"])
        self.assertIn("WhichOneof", contract["interval_count_rule"])
        self.assertIn("至少一个批槽含两个或更多同族成员", contract["exact_grouping_rule"])
        self.assertIn("先过滤合法且 Core grouped_batch_count > 0", contract["grouping_acceptance_rule"])
        self.assertEqual(
            "truthy",
            contract["activation_evidence"]["required_fields"]["diagnostics.cp_sat_called"],
        )
        shortcuts = "\n".join(contract["forbidden_shortcuts"])
        self.assertIn("grouped_batch_count=0", shortcuts)
        self.assertIn("CpSolver.solve/Solve", shortcuts)
        self.assertIn("单件批数写入 grouped_batch_count", shortcuts)
        self.assertEqual([], contract["checkpoint_checks"])
        self.assertIn("可作为 baseline 进入正式竞争", contract["completion_rule"])
        self.assertEqual(
            "fjsp-exact-hybrid-worker",
            contract["method_family_rules"]["exact_hybrid"]["skill_owner"],
        )

    def test_quality_contract_requires_all_batch_guards(self) -> None:
        runtime = build_solver_runtime_feature_contract(
            {
                "instance_diagnostics": {
                    "status": "available",
                    "summary": {"profiled_count": 1, "pbpm_instance_count": 1},
                    "instances": [{"variant": "fjsp_pbpm"}],
                }
            }
        )
        self.assertTrue(
            {"fjsp_pbpm", "batching", "parallel_batch_machine", "batch_capacity"}.issubset(
                runtime["active_features"]
            )
        )
        self.assertTrue(
            {
                "batch_capacity_guard",
                "batch_family_compatibility_guard",
                "parallel_batch_timing_guard",
            }.issubset(runtime["variant_required_code_capabilities"])
        )

    def test_chinese_requirement_activates_batch_guards_without_diagnostics(self) -> None:
        runtime = build_solver_runtime_feature_contract(
            {
                "task": {"description": "PBPM-FJSP 并行组批：同族工件共享批处理机的起止时间。"},
                "documents": [
                    {
                        "path": "fjsp_pbpm_requirement.md",
                        "snippet": "每个批次受容量约束，批时长取成员加工时长最大值。",
                    }
                ],
            }
        )

        self.assertTrue(
            {
                "fjsp_pbpm",
                "batching",
                "parallel_batch_machine",
                "batch_capacity",
                "incompatible_job_families",
            }.issubset(runtime["active_features"])
        )
        self.assertTrue(
            {
                "batch_capacity_guard",
                "batch_family_compatibility_guard",
                "parallel_batch_timing_guard",
            }.issubset(runtime["variant_required_code_capabilities"])
        )

    def test_singleton_batches_form_a_valid_complete_schedule(self) -> None:
        errors, metrics = validate_standard_schedule(
            parse_standard_fjsp(INSTANCE),
            sequential_schedule(parse_standard_fjsp(INSTANCE)),
        )

        self.assertEqual([], errors)
        self.assertGreater(metrics["batch_count"], 0)
        self.assertEqual(0.0, metrics["grouped_batch_count"])

    def test_same_family_operations_can_share_one_real_batch(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        errors, metrics = validate_standard_schedule(
            instance,
            sequential_schedule(instance, grouped_jobs=(1, 3)),
        )

        self.assertEqual([], errors)
        self.assertEqual(1.0, metrics["grouped_batch_count"])
        self.assertEqual(0.0, metrics["family_violations"])
        self.assertEqual(0.0, metrics["batch_capacity_violations"])
        self.assertEqual(0.0, metrics["batch_synchronization_violations"])
        self.assertEqual(0.0, metrics["batch_duration_violations"])

    def test_mixed_family_batch_is_rejected(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        errors, metrics = validate_standard_schedule(
            instance,
            sequential_schedule(instance, grouped_jobs=(1, 2)),
        )

        self.assertTrue(any("batch family violation" in error for error in errors))
        self.assertEqual(1.0, metrics["family_violations"])

    def test_over_capacity_batch_is_rejected(self) -> None:
        instance = replace(parse_standard_fjsp(INSTANCE), job_family_ids=(0, 0, 0, 0))
        errors, metrics = validate_standard_schedule(
            instance,
            sequential_schedule(instance, grouped_jobs=(0, 1, 2)),
        )

        self.assertTrue(any("batch capacity violation" in error for error in errors))
        self.assertEqual(1.0, metrics["batch_capacity_violations"])

    def test_unsynchronized_and_wrong_duration_batches_are_rejected(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        schedule = sequential_schedule(instance, grouped_jobs=(1, 3))
        unsynchronized = [
            replace(record, start=record.start + 1, end=record.end + 1)
            if (record.job_id, record.op_id) == (3, 0)
            else record
            for record in schedule
        ]
        wrong_duration = [
            replace(record, end=record.end - 1)
            if record.batch_id == 0
            else record
            for record in schedule
        ]

        sync_errors, sync_metrics = validate_standard_schedule(instance, unsynchronized)
        duration_errors, duration_metrics = validate_standard_schedule(instance, wrong_duration)

        self.assertTrue(any("batch synchronization violation" in error for error in sync_errors))
        self.assertEqual(1.0, sync_metrics["batch_synchronization_violations"])
        self.assertTrue(any("batch duration violation" in error for error in duration_errors))
        self.assertEqual(1.0, duration_metrics["batch_duration_violations"])

    def test_batch_id_is_required_and_distinct_batches_cannot_overlap(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        schedule = sequential_schedule(instance)
        first_index = next(index for index, record in enumerate(schedule) if record.batch_id is not None)
        missing_id = list(schedule)
        missing_id[first_index] = replace(missing_id[first_index], batch_id=None)

        same_machine = [
            (index, record)
            for index, record in enumerate(schedule)
            if record.machine_id == schedule[first_index].machine_id and record.batch_id is not None
        ]
        second_index, second = same_machine[1]
        overlap = list(schedule)
        overlap[second_index] = replace(
            second,
            start=schedule[first_index].start,
            end=schedule[first_index].start + second.duration,
        )

        missing_errors, _ = validate_standard_schedule(instance, missing_id)
        overlap_errors, _ = validate_standard_schedule(instance, overlap)

        self.assertTrue(any("batch id is required" in error for error in missing_errors))
        self.assertTrue(any("machine overlap/setup violation" in error for error in overlap_errors))

    def test_fixed_evaluator_accepts_a_real_grouped_batch(self) -> None:
        instance = parse_standard_fjsp(INSTANCE)
        schedule = sequential_schedule(instance, grouped_jobs=(1, 3))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            solution = tmp_path / "solution.json"
            metrics = tmp_path / "metrics.json"
            solution.write_text(
                json.dumps({"schedule": [record.__dict__ for record in schedule]}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_pbpm_evaluator.py"),
                    "--instance",
                    str(INSTANCE),
                    "--solution",
                    str(solution),
                    "--metrics",
                    str(metrics),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(metrics.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(payload["valid"], payload["errors"])
        self.assertEqual(1.0, payload["metrics"]["grouped_batch_count"])


if __name__ == "__main__":
    unittest.main()
