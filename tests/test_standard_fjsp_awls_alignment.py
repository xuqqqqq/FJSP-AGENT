from __future__ import annotations

import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from examples.standard_fjsp_awls_solver import (
    BACK,
    CHANGE_MACHINE_BACK,
    CHANGE_MACHINE_FRONT,
    FRONT,
    AwlsSchedule,
    Move,
    OperationIndex,
    all_path_critical_blocks,
    candidate_tabu_sequence,
    change_machine_intersection,
    machine_scan_critical_blocks,
    solve_awls,
)
from harness_agent.standard_fjsp import (
    Job,
    MachineOption,
    Operation,
    StandardFjspInstance,
    validate_standard_schedule,
)


def make_instance(raw_jobs: list[list[list[tuple[int, int]]]], machine_count: int = 2) -> StandardFjspInstance:
    """Build a compact in-memory FJSP instance for AWLS operator tests."""

    jobs: list[Job] = []
    max_candidate_count = 0
    for job_id, raw_ops in enumerate(raw_jobs):
        operations: list[Operation] = []
        for op_id, raw_candidates in enumerate(raw_ops):
            max_candidate_count = max(max_candidate_count, len(raw_candidates))
            operations.append(
                Operation(
                    job_id=job_id,
                    op_id=op_id,
                    candidates=tuple(MachineOption(machine_id, duration) for machine_id, duration in raw_candidates),
                )
            )
        jobs.append(Job(job_id=job_id, operations=tuple(operations)))
    return StandardFjspInstance(
        name="unit",
        job_count=len(jobs),
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
    )


def make_schedule(instance: StandardFjspInstance, machine_sequences: list[list[int]]) -> AwlsSchedule:
    """Create an AWLS schedule from explicit machine sequences."""

    index = OperationIndex.from_instance(instance)
    on_machine = [-1] * index.node_count
    for machine_id, sequence in enumerate(machine_sequences):
        for node in sequence:
            on_machine[node] = machine_id
    return AwlsSchedule(index, machine_sequences, on_machine, random.Random(0))


def reference_change_machine_intersection(
    schedule: AwlsSchedule,
    node: int,
    candidate_machine: int,
) -> tuple[list[int], list[int], list[int]]:
    """Literal RK/LK list matching from the C++ AWLS formulation.

    The production implementation uses a suffix/prefix window shortcut.  This
    reference keeps the slower list-construction shape so the unit test catches
    mistakes in that shortcut without depending on a C++ binary.
    """

    job_predecessor = schedule.job_predecessor[node]
    job_successor = schedule.job_successor[node]
    remove_r = schedule.end_time[job_predecessor]
    remove_q = 0
    if job_successor != schedule.index.end_node:
        remove_q = (
            schedule.backward_path_length[job_successor]
            + schedule.index.duration(job_successor, schedule.on_machine[job_successor])
        )

    rk: list[int] = []
    lk: list[int] = []
    for other in schedule.machine_sequences[candidate_machine]:
        if schedule.end_time[other] > remove_r:
            rk.append(other)
        if schedule.backward_path_length[other] + schedule.index.duration(other, candidate_machine) > remove_q:
            lk.append(other)

    intersection: list[int] = []
    if lk and rk:
        if len(lk) > len(rk):
            index = 0
            while index < len(rk) and rk[index] != lk[-1]:
                index += 1
            if index < len(rk):
                for offset in range(index + 1):
                    lk_node = lk[len(lk) - 1 - index + offset]
                    if rk[offset] == lk_node:
                        intersection.append(rk[offset])
        else:
            index = len(lk) - 1
            while index >= 0 and lk[index] != rk[0]:
                index -= 1
            if index >= 0:
                for offset in range(index, len(lk)):
                    rk_pos = offset - index
                    if rk_pos >= len(rk) or lk[offset] != rk[rk_pos]:
                        break
                    intersection.append(lk[offset])
    return rk, lk, intersection


class StandardFjspAwlsAlignmentTests(unittest.TestCase):
    def test_candidate_tabu_sequence_matches_cpp_local_sequence_cases(self) -> None:
        instance = make_instance(
            [
                [[(0, 3), (1, 3)]],
                [[(0, 3), (1, 3)]],
                [[(0, 3), (1, 3)]],
                [[(0, 3), (1, 3)]],
            ]
        )
        schedule = make_schedule(instance, [[1, 2], [3, 4]])

        self.assertEqual((0, [2, 1]), candidate_tabu_sequence(schedule, Move(FRONT, 2, 1)))
        self.assertEqual((0, [2, 1]), candidate_tabu_sequence(schedule, Move(BACK, 1, 2)))
        self.assertEqual((1, [3, 2, 4]), candidate_tabu_sequence(schedule, Move(CHANGE_MACHINE_FRONT, 2, 4)))
        self.assertEqual((1, [3, 2, 4]), candidate_tabu_sequence(schedule, Move(CHANGE_MACHINE_BACK, 2, 3)))

    def test_change_machine_intersection_matches_reference_list_algorithm(self) -> None:
        instance = make_instance(
            [
                [[(0, 3), (1, 4)], [(0, 5), (1, 2)]],
                [[(0, 2), (1, 3)], [(0, 4), (1, 6)]],
                [[(0, 6), (1, 2)], [(0, 1), (1, 5)]],
            ]
        )
        schedule = make_schedule(instance, [[1, 4, 6], [3, 5, 2]])

        checked = 0
        for node in schedule.index.real_nodes:
            old_machine = schedule.on_machine[node]
            for candidate_machine in schedule.index.candidates[node]:
                if candidate_machine == old_machine:
                    continue
                expected = reference_change_machine_intersection(schedule, node, candidate_machine)
                actual = change_machine_intersection(schedule, node, candidate_machine)
                self.assertEqual(expected, actual, f"node={node}, candidate_machine={candidate_machine}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_all_path_critical_blocks_matches_machine_scan_on_single_machine_chain(self) -> None:
        instance = make_instance(
            [
                [[(0, 2)]],
                [[(0, 3)]],
                [[(0, 4)]],
                [[(0, 5)]],
            ],
            machine_count=1,
        )
        schedule = make_schedule(instance, [[1, 2, 3, 4]])

        self.assertEqual([[1, 2, 3, 4]], all_path_critical_blocks(schedule))
        self.assertEqual(machine_scan_critical_blocks(schedule), all_path_critical_blocks(schedule))

    def test_awls_solver_returns_valid_complete_schedule_on_small_instance(self) -> None:
        instance = make_instance(
            [
                [[(0, 3), (1, 4)], [(0, 2), (1, 3)]],
                [[(0, 2), (1, 5)], [(0, 4), (1, 1)]],
                [[(0, 4), (1, 2)], [(0, 3), (1, 2)]],
            ]
        )

        schedule, label = solve_awls(
            instance,
            seed=7,
            restarts=1,
            cycles_per_restart=1,
            iterations=40,
            time_limit_sec=1.0,
            init_mode="greedy",
            beta=500,
            gamma=40,
            theta=5,
            exact_select_top_k=0,
            same_machine_eval="cpp-fast",
            critical_block_exhaustive_pct=0,
            zi_policy="cpp",
            initial_state="reset",
        )

        errors, metrics = validate_standard_schedule(instance, schedule)
        self.assertEqual([], errors)
        self.assertEqual(float(instance.operation_count), metrics["scheduled_operations"])
        self.assertIn("awls:init=greedy", label)

    def test_incremental_machine_link_rebuild_matches_full_rebuild_after_moves(self) -> None:
        instance = make_instance(
            [
                [[(0, 3), (1, 4)]],
                [[(0, 2), (1, 3)]],
                [[(0, 5), (1, 2)]],
                [[(0, 4), (1, 6)]],
            ]
        )

        for move in (
            Move(BACK, 1, 2),
            Move(CHANGE_MACHINE_FRONT, 2, 3),
            Move(CHANGE_MACHINE_BACK, 1, 4),
        ):
            with self.subTest(move=move):
                schedule = make_schedule(instance, [[1, 2], [3, 4]])
                schedule.apply_move(move)
                rebuilt = make_schedule(instance, schedule.machine_sequences)

                self.assertEqual(rebuilt.on_machine, schedule.on_machine)
                self.assertEqual(rebuilt.on_machine_pos, schedule.on_machine_pos)
                self.assertEqual(rebuilt.machine_predecessor, schedule.machine_predecessor)
                self.assertEqual(rebuilt.machine_successor, schedule.machine_successor)
                self.assertEqual(rebuilt.first_machine_operation, schedule.first_machine_operation)
                self.assertEqual(rebuilt.last_machine_operation, schedule.last_machine_operation)
                self.assertEqual(rebuilt.machine_operation_count, schedule.machine_operation_count)
                self.assertEqual(rebuilt.forward_path_length, schedule.forward_path_length)
                self.assertEqual(rebuilt.end_time, schedule.end_time)
                self.assertEqual(rebuilt.backward_path_length, schedule.backward_path_length)
                self.assertEqual(rebuilt.makespan, schedule.makespan)

    def test_paper_profile_uses_single_cpp_style_greedy_restart(self) -> None:
        raw_instance = "2 2 2\n2 2 1 3 2 4 2 1 2 2 3\n2 2 1 2 2 5 2 1 4 2 1\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance_path = root / "tiny.fjs"
            output_path = root / "solution.json"
            instance_path.write_text(raw_instance, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "examples/standard_fjsp_awls_solver.py",
                    "--input",
                    str(instance_path),
                    "--output",
                    str(output_path),
                    "--seed",
                    "3",
                    "--time-limit-sec",
                    "0.05",
                    "--paper-profile",
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            self.assertEqual("", proc.stderr)
            self.assertEqual(0, proc.returncode)
            self.assertIn("awls:init=greedy:restarts=1:", proc.stdout)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
