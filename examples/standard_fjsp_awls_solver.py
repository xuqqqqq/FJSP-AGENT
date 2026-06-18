from __future__ import annotations

"""AWLS-style solver for standard Flexible Job-Shop Scheduling instances.

This file is intentionally separate from ``standard_fjsp_local_search_solver``.
The older solver is a lightweight profile portfolio; this implementation keeps
the stronger AWLS mechanics explicit: a disjunctive graph state, critical-block
neighborhoods, R/Q move evaluation, sequence tabu, and adaptive operation
weights.  The implementation is based on the public FJSP instance/evaluator
contract in this repository, not on a copied C++ source file.
"""

import argparse
import math
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.standard_fjsp import (
    ScheduleRecord,
    StandardFjspInstance,
    parse_standard_fjsp,
    validate_standard_schedule,
    write_solution,
)


START_NODE = 0
FRONT = "FRONT"
BACK = "BACK"
CHANGE_MACHINE_FRONT = "CHANGE_MACHINE_FRONT"
CHANGE_MACHINE_BACK = "CHANGE_MACHINE_BACK"
ZI_POLICY_CHOICES = ("cpp", "none", "sqrt", "aggressive", "critical")
PORTFOLIO_OUTER_SEED_STRIDE = 1_000_003


@dataclass(frozen=True)
class Move:
    method: str
    which: int
    where: int


@dataclass(frozen=True)
class PortfolioLane:
    """One independent AWLS search lane.

    C++ AWLS is stochastic: different seeds and initialization policies can
    land in very different basins.  A portfolio keeps that behavior explicit
    instead of hiding it in repeated manual runs.
    """

    seed: int
    init_mode: str
    restarts: int
    time_limit_sec: float | None = None


@dataclass
class OperationIndex:
    instance: StandardFjspInstance
    end_node: int
    node_count: int
    node_to_job: list[int]
    node_to_op: list[int]
    job_to_nodes: list[list[int]]
    candidates: list[dict[int, int]]

    @classmethod
    def from_instance(cls, instance: StandardFjspInstance) -> "OperationIndex":
        real_count = instance.operation_count
        end_node = real_count + 1
        node_count = real_count + 2
        node_to_job = [-1] * node_count
        node_to_op = [-1] * node_count
        job_to_nodes: list[list[int]] = [[] for _ in instance.jobs]
        candidates: list[dict[int, int]] = [{} for _ in range(node_count)]

        node = 1
        for job in instance.jobs:
            for op in job.operations:
                node_to_job[node] = job.job_id
                node_to_op[node] = op.op_id
                job_to_nodes[job.job_id].append(node)
                candidates[node] = {candidate.machine_id: candidate.duration for candidate in op.candidates}
                node += 1

        return cls(
            instance=instance,
            end_node=end_node,
            node_count=node_count,
            node_to_job=node_to_job,
            node_to_op=node_to_op,
            job_to_nodes=job_to_nodes,
            candidates=candidates,
        )

    @property
    def real_nodes(self) -> range:
        return range(1, self.end_node)

    def duration(self, node: int, machine_id: int) -> int:
        if node <= START_NODE or node >= self.end_node:
            return 0
        return self.candidates[node][machine_id]

    def can_run_on(self, node: int, machine_id: int) -> bool:
        return machine_id in self.candidates[node]


class AwlsSchedule:
    """Mutable disjunctive graph schedule used by the AWLS search."""

    def __init__(
        self,
        index: OperationIndex,
        machine_sequences: list[list[int]],
        on_machine: list[int],
        rng: random.Random,
        initial_weight: int = 0,
    ) -> None:
        self.index = index
        self.rng = rng
        self.machine_sequences = [list(sequence) for sequence in machine_sequences]
        self.on_machine = list(on_machine)

        n = index.node_count
        self.job_successor = [-1] * n
        self.job_predecessor = [-1] * n
        self.first_job_operation = [-1] * index.instance.job_count
        self.last_job_operation = [-1] * index.instance.job_count
        for job_id, nodes in enumerate(index.job_to_nodes):
            if not nodes:
                continue
            self.first_job_operation[job_id] = nodes[0]
            self.last_job_operation[job_id] = nodes[-1]
            for pos, node in enumerate(nodes):
                self.job_predecessor[node] = START_NODE if pos == 0 else nodes[pos - 1]
                self.job_successor[node] = index.end_node if pos == len(nodes) - 1 else nodes[pos + 1]

        self.machine_successor = [-1] * n
        self.machine_predecessor = [-1] * n
        self.first_machine_operation = [-1] * index.instance.machine_count
        self.last_machine_operation = [-1] * index.instance.machine_count
        self.machine_operation_count = [0] * index.instance.machine_count
        self.on_machine_pos = [0] * n
        self.rebuild_machine_links()

        self.forward_path_length = [0] * n
        self.end_time = [0] * n
        self.backward_path_length = [0] * n
        self.topological_order: list[int] = []
        self.makespan = 0
        self.op_weight = [initial_weight] * n
        self.op_cooldown = [0] * n
        self.same_machine_eval = "stable"
        self.zi_policy = "cpp"
        self.update_time()

    def clone(self) -> "AwlsSchedule":
        cloned = AwlsSchedule.__new__(AwlsSchedule)
        cloned.index = self.index
        cloned.rng = self.rng
        cloned.machine_sequences = [list(sequence) for sequence in self.machine_sequences]
        cloned.on_machine = list(self.on_machine)
        cloned.job_successor = list(self.job_successor)
        cloned.job_predecessor = list(self.job_predecessor)
        cloned.first_job_operation = list(self.first_job_operation)
        cloned.last_job_operation = list(self.last_job_operation)
        cloned.machine_successor = list(self.machine_successor)
        cloned.machine_predecessor = list(self.machine_predecessor)
        cloned.first_machine_operation = list(self.first_machine_operation)
        cloned.last_machine_operation = list(self.last_machine_operation)
        cloned.machine_operation_count = list(self.machine_operation_count)
        cloned.on_machine_pos = list(self.on_machine_pos)
        cloned.forward_path_length = list(self.forward_path_length)
        cloned.end_time = list(self.end_time)
        cloned.backward_path_length = list(self.backward_path_length)
        cloned.topological_order = list(self.topological_order)
        cloned.makespan = self.makespan
        cloned.op_weight = list(self.op_weight)
        cloned.op_cooldown = list(self.op_cooldown)
        cloned.same_machine_eval = self.same_machine_eval
        cloned.zi_policy = self.zi_policy
        return cloned

    def rebuild_machine_links(self) -> None:
        n = self.index.node_count
        self.machine_successor = [-1] * n
        self.machine_predecessor = [-1] * n
        self.first_machine_operation = [-1] * self.index.instance.machine_count
        self.last_machine_operation = [-1] * self.index.instance.machine_count
        self.machine_operation_count = [0] * self.index.instance.machine_count
        self.on_machine_pos = [0] * n

        for machine_id, sequence in enumerate(self.machine_sequences):
            self.machine_operation_count[machine_id] = len(sequence)
            if sequence:
                self.first_machine_operation[machine_id] = sequence[0]
                self.last_machine_operation[machine_id] = sequence[-1]
            for pos, node in enumerate(sequence):
                self.on_machine[node] = machine_id
                self.on_machine_pos[node] = pos
                if pos > 0:
                    self.machine_predecessor[node] = sequence[pos - 1]
                if pos + 1 < len(sequence):
                    self.machine_successor[node] = sequence[pos + 1]

    def topological_sort(self) -> list[int]:
        n = self.index.node_count
        successors: list[list[int]] = [[] for _ in range(n)]
        indegree = [0] * n

        for node in self.index.real_nodes:
            job_successor = self.job_successor[node]
            if job_successor != -1:
                successors[node].append(job_successor)
                indegree[job_successor] += 1
            machine_successor = self.machine_successor[node]
            if machine_successor != -1:
                successors[node].append(machine_successor)
                indegree[machine_successor] += 1

        for first in self.first_job_operation:
            if first != -1:
                successors[START_NODE].append(first)
                indegree[first] += 1

        ready = deque([node for node in range(n) if indegree[node] == 0])
        order: list[int] = []
        while ready:
            node = ready.popleft()
            order.append(node)
            for successor in successors[node]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)

        if len(order) != n:
            raise ValueError("cycle detected in disjunctive graph")
        return order

    def update_time(self) -> None:
        order = self.topological_sort()
        self.topological_order = order
        self.forward_path_length = [0] * self.index.node_count
        self.end_time = [0] * self.index.node_count
        self.backward_path_length = [0] * self.index.node_count
        self.makespan = 0

        for node in order:
            if node <= START_NODE or node >= self.index.end_node:
                continue
            start = 0
            job_predecessor = self.job_predecessor[node]
            machine_predecessor = self.machine_predecessor[node]
            if job_predecessor != -1:
                start = max(start, self.end_time[job_predecessor])
            if machine_predecessor != -1:
                start = max(start, self.end_time[machine_predecessor])
            machine_id = self.on_machine[node]
            end = start + self.index.duration(node, machine_id)
            self.forward_path_length[node] = start
            self.end_time[node] = end
            self.makespan = max(self.makespan, end)

        for node in reversed(order):
            if node <= START_NODE or node >= self.index.end_node:
                continue
            q = 0
            job_successor = self.job_successor[node]
            machine_successor = self.machine_successor[node]
            if job_successor != self.index.end_node:
                machine_id = self.on_machine[job_successor]
                q = max(
                    q,
                    self.backward_path_length[job_successor]
                    + self.index.duration(job_successor, machine_id),
                )
            if machine_successor != -1:
                machine_id = self.on_machine[machine_successor]
                q = max(
                    q,
                    self.backward_path_length[machine_successor]
                    + self.index.duration(machine_successor, machine_id),
                )
            self.backward_path_length[node] = q

    def is_critical_operation(self, node: int) -> bool:
        return (
            node > START_NODE
            and node < self.index.end_node
            and self.end_time[node] + self.backward_path_length[node] == self.makespan
        )

    def to_records(self) -> list[ScheduleRecord]:
        records: list[ScheduleRecord] = []
        for node in self.index.real_nodes:
            records.append(
                ScheduleRecord(
                    job_id=self.index.node_to_job[node],
                    op_id=self.index.node_to_op[node],
                    machine_id=self.on_machine[node],
                    start=self.forward_path_length[node],
                    end=self.end_time[node],
                )
            )
        return sorted(records, key=lambda item: (item.start, item.end, item.machine_id, item.job_id, item.op_id))

    def apply_move(self, move: Move) -> None:
        old_machine = self.on_machine[move.which]
        target_machine = self.on_machine[move.where]

        if move.method in (FRONT, BACK):
            if old_machine != target_machine:
                raise ValueError("same-machine move targets a different machine")
            sequence = self.machine_sequences[old_machine]
            sequence.remove(move.which)
            where_pos = sequence.index(move.where)
            insert_pos = where_pos if move.method == FRONT else where_pos + 1
            sequence.insert(insert_pos, move.which)
        elif move.method in (CHANGE_MACHINE_FRONT, CHANGE_MACHINE_BACK):
            if target_machine not in self.index.candidates[move.which]:
                raise ValueError("target machine is not a candidate")
            self.machine_sequences[old_machine].remove(move.which)
            target_sequence = self.machine_sequences[target_machine]
            where_pos = target_sequence.index(move.where)
            insert_pos = where_pos if move.method == CHANGE_MACHINE_FRONT else where_pos + 1
            target_sequence.insert(insert_pos, move.which)
            self.on_machine[move.which] = target_machine
        else:
            raise ValueError(f"unknown move method: {move.method}")

        self.rebuild_machine_links()
        self.update_time()


class SequenceTabuList:
    def __init__(self, machine_count: int) -> None:
        self.items: list[dict[tuple[int, ...], int]] = [dict() for _ in range(machine_count)]

    def add(self, machine_id: int, sequence: list[int], expires_at: int) -> None:
        if sequence:
            self.items[machine_id][tuple(sequence)] = expires_at

    def is_tabu(self, machine_id: int, sequence: list[int], iteration: int) -> bool:
        if not sequence:
            return False
        return self.items[machine_id].get(tuple(sequence), -1) >= iteration


def random_init(index: OperationIndex, rng: random.Random) -> tuple[list[list[int]], list[int]]:
    sequences = [[] for _ in range(index.instance.machine_count)]
    on_machine = [-1] * index.node_count
    candidates = [nodes[0] for nodes in index.job_to_nodes if nodes]

    while candidates:
        candidate_pos = rng.randrange(len(candidates))
        node = candidates[candidate_pos]
        machine_id = rng.choice(list(index.candidates[node]))
        sequences[machine_id].append(node)
        on_machine[node] = machine_id
        job_id = index.node_to_job[node]
        op_pos = index.job_to_nodes[job_id].index(node)
        if op_pos + 1 < len(index.job_to_nodes[job_id]):
            candidates[candidate_pos] = index.job_to_nodes[job_id][op_pos + 1]
        else:
            candidates[candidate_pos] = candidates[-1]
            candidates.pop()
    return sequences, on_machine


def greedy_gt_init(index: OperationIndex, rng: random.Random, random_factor: float = 0.20, idle_bonus: float = 0.20) -> tuple[list[list[int]], list[int]]:
    sequences = [[] for _ in range(index.instance.machine_count)]
    on_machine = [-1] * index.node_count
    job_ready = [0] * index.instance.job_count
    machine_ready = [0] * index.instance.machine_count
    machine_load_count = [0] * index.instance.machine_count
    current_pos = [0] * index.instance.job_count
    scheduled = 0

    while scheduled < index.end_node - 1:
        choices: list[tuple[int, int, int, bool]] = []
        best_completion = math.inf
        for job_id, nodes in enumerate(index.job_to_nodes):
            if current_pos[job_id] >= len(nodes):
                continue
            node = nodes[current_pos[job_id]]
            for machine_id, duration in index.candidates[node].items():
                start = max(job_ready[job_id], machine_ready[machine_id])
                completion = start + duration
                best_completion = min(best_completion, completion)
                choices.append((node, machine_id, completion, machine_load_count[machine_id] == 0))

        if not choices:
            break

        has_unused = any(count == 0 for count in machine_load_count)
        filtered: list[tuple[int, int, int, bool]] = []
        if has_unused:
            threshold = best_completion * (1.0 + random_factor + idle_bonus)
            filtered = [choice for choice in choices if choice[3] and choice[2] <= threshold]
        if not filtered:
            threshold = best_completion * (1.0 + random_factor)
            filtered = [choice for choice in choices if choice[2] <= threshold]
        if not filtered:
            filtered = choices

        node, machine_id, _, _ = rng.choice(filtered)
        duration = index.duration(node, machine_id)
        job_id = index.node_to_job[node]
        start = max(job_ready[job_id], machine_ready[machine_id])
        completion = start + duration
        sequences[machine_id].append(node)
        on_machine[node] = machine_id
        job_ready[job_id] = completion
        machine_ready[machine_id] = completion
        machine_load_count[machine_id] += 1
        current_pos[job_id] += 1
        scheduled += 1

    return sequences, on_machine


def one_critical_path_from_start(schedule: AwlsSchedule, start: int) -> list[int]:
    """Follow one tight critical path from a selected start operation."""

    path = [start]
    seen = {start}
    while schedule.end_time[path[-1]] != schedule.makespan:
        node = path[-1]
        machine_successor = schedule.machine_successor[node]
        if (
            machine_successor != -1
            and machine_successor not in seen
            and schedule.is_critical_operation(machine_successor)
            and schedule.forward_path_length[machine_successor] == schedule.end_time[node]
        ):
            path.append(machine_successor)
            seen.add(machine_successor)
            continue
        job_successor = schedule.job_successor[node]
        if (
            job_successor != schedule.index.end_node
            and job_successor not in seen
            and schedule.is_critical_operation(job_successor)
            and schedule.forward_path_length[job_successor] == schedule.end_time[node]
        ):
            path.append(job_successor)
            seen.add(job_successor)
            continue
        break
    if path and schedule.end_time[path[-1]] == schedule.makespan:
        return path
    return []


def blocks_from_path(schedule: AwlsSchedule, path: list[int]) -> list[list[int]]:
    blocks: list[list[int]] = []
    block: list[int] = []
    previous_machine = -1
    for node in path:
        machine_id = schedule.on_machine[node]
        if machine_id != previous_machine:
            if len(block) > 1:
                blocks.append(block)
            block = [node]
            previous_machine = machine_id
        else:
            block.append(node)
    if len(block) > 1:
        blocks.append(block)
    return blocks


def machine_scan_critical_blocks(schedule: AwlsSchedule) -> list[list[int]]:
    blocks: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    for sequence in schedule.machine_sequences:
        block: list[int] = []
        for node in sequence:
            if schedule.is_critical_operation(node):
                if not block or schedule.end_time[block[-1]] == schedule.forward_path_length[node]:
                    block.append(node)
                else:
                    if len(block) > 1 and tuple(block) not in seen:
                        blocks.append(block)
                        seen.add(tuple(block))
                    block = [node]
            else:
                if len(block) > 1 and tuple(block) not in seen:
                    blocks.append(block)
                    seen.add(tuple(block))
                block = []
        if len(block) > 1 and tuple(block) not in seen:
            blocks.append(block)
            seen.add(tuple(block))
    return blocks


def critical_blocks(schedule: AwlsSchedule, rng: random.Random, exhaustive: bool = False) -> list[list[int]]:
    if exhaustive:
        return machine_scan_critical_blocks(schedule)

    start_candidates = [
        node
        for node in schedule.first_machine_operation
        if node != -1 and schedule.is_critical_operation(node) and schedule.forward_path_length[node] == 0
    ]
    rng.shuffle(start_candidates)
    for start in start_candidates:
        blocks = blocks_from_path(schedule, one_critical_path_from_start(schedule, start))
        if blocks:
            return blocks
    return machine_scan_critical_blocks(schedule)


def weight_perturbation(schedule: AwlsSchedule, node: int, gamma: int) -> float:
    weight = schedule.op_weight[node]
    policy = schedule.zi_policy
    if policy == "none" or weight <= 0:
        return 0.0
    rr = schedule.rng.uniform(1.0e-9, float(gamma))
    cooling_factor = max(0.0, 1.0 - schedule.op_cooldown[node] / rr)
    if policy == "sqrt":
        return cooling_factor * math.sqrt(weight)

    perturbation = cooling_factor * weight
    if policy == "aggressive":
        return 1.5 * perturbation
    if policy == "critical" and schedule.is_critical_operation(node):
        return 1.25 * perturbation
    return perturbation


def operation_tail(schedule: AwlsSchedule, node: int) -> int:
    """Return C++-style Q tail contribution for a successor operation."""

    if node <= START_NODE or node >= schedule.index.end_node:
        return 0
    return schedule.backward_path_length[node] + schedule.index.duration(node, schedule.on_machine[node])


def local_sequence_after_same_machine_move(schedule: AwlsSchedule, move: Move) -> tuple[list[int], int | None, int | None]:
    machine_id = schedule.on_machine[move.which]
    sequence = schedule.machine_sequences[machine_id]
    which_pos = sequence.index(move.which)
    where_pos = sequence.index(move.where)

    if move.method == FRONT:
        if where_pos >= which_pos:
            raise ValueError("FRONT expects where before which")
        new_sequence = [move.which] + sequence[where_pos:which_pos]
        predecessor = sequence[where_pos - 1] if where_pos > 0 else None
        successor = sequence[which_pos + 1] if which_pos + 1 < len(sequence) else None
    elif move.method == BACK:
        if where_pos <= which_pos:
            raise ValueError("BACK expects where after which")
        new_sequence = sequence[which_pos + 1 : where_pos + 1] + [move.which]
        predecessor = sequence[which_pos - 1] if which_pos > 0 else None
        successor = sequence[where_pos + 1] if where_pos + 1 < len(sequence) else None
    else:
        raise ValueError("not a same-machine move")
    return new_sequence, predecessor, successor


def same_machine_evaluate_stable(schedule: AwlsSchedule, move: Move, gamma: int) -> float:
    new_sequence, machine_predecessor, machine_successor = local_sequence_after_same_machine_move(schedule, move)
    n = len(new_sequence)
    new_r = [0] * n
    new_q = [0] * n

    for idx, node in enumerate(new_sequence):
        job_predecessor = schedule.job_predecessor[node]
        job_ready = schedule.end_time[job_predecessor] if job_predecessor != -1 else 0
        if idx == 0:
            machine_ready = schedule.end_time[machine_predecessor] if machine_predecessor is not None else 0
        else:
            prev = new_sequence[idx - 1]
            machine_ready = new_r[idx - 1] + schedule.index.duration(prev, schedule.on_machine[prev])
        new_r[idx] = max(job_ready, machine_ready)

    for rev_idx, node in enumerate(reversed(new_sequence)):
        idx = n - rev_idx - 1
        job_successor = schedule.job_successor[node]
        job_tail = 0
        if job_successor != schedule.index.end_node:
            job_tail = schedule.backward_path_length[job_successor] + schedule.index.duration(
                job_successor,
                schedule.on_machine[job_successor],
            )
        if idx == n - 1:
            machine_tail = 0
            if machine_successor is not None:
                machine_tail = schedule.backward_path_length[machine_successor] + schedule.index.duration(
                    machine_successor,
                    schedule.on_machine[machine_successor],
                )
        else:
            nxt = new_sequence[idx + 1]
            machine_tail = new_q[idx + 1] + schedule.index.duration(nxt, schedule.on_machine[nxt])
        new_q[idx] = max(job_tail, machine_tail)

    value = 0.0
    for idx, node in enumerate(new_sequence):
        machine_id = schedule.on_machine[node]
        value = max(
            value,
            new_r[idx] + schedule.index.duration(node, machine_id) + new_q[idx] + weight_perturbation(schedule, node, gamma),
        )
    return value


def same_machine_evaluate_cpp_fast(schedule: AwlsSchedule, move: Move, gamma: int) -> float:
    """Approximate same-machine move evaluation used by the C++ AWLS branch.

    This deliberately mirrors the active non-EVALUATE_1 branch in the reference
    C++ framework.  It is less symmetric than the stable local R/Q evaluator,
    but the rough ordering can steer tabu search through different basins.
    """

    if move.method == FRONT:
        new_sequence = [move.which]
        node = move.where
        while node != move.which:
            new_sequence.append(node)
            node = schedule.machine_successor[node]
        n = len(new_sequence)
        new_r = [0] * n
        new_q = [0] * n

        job_predecessor = schedule.job_predecessor[move.which]
        machine_predecessor = schedule.machine_predecessor[move.where]
        new_r[0] = schedule.end_time[job_predecessor]
        if machine_predecessor != -1:
            new_r[0] = max(new_r[0], schedule.end_time[machine_predecessor])
        for idx in range(1, n):
            op = new_sequence[idx]
            job_predecessor = schedule.job_predecessor[op]
            prev = new_sequence[idx - 1]
            new_r[idx] = max(
                schedule.end_time[job_predecessor],
                new_r[idx - 1] + schedule.index.duration(prev, schedule.on_machine[prev]),
            )

        last = new_sequence[-1]
        job_successor = schedule.job_successor[last]
        new_q[-1] = operation_tail(schedule, job_successor)
        machine_successor = schedule.machine_successor[move.which]
        if machine_successor != -1:
            new_q[-1] = max(new_q[-1], operation_tail(schedule, machine_successor))
        for idx in range(n - 2, -1, -1):
            op = new_sequence[idx]
            job_successor = schedule.job_successor[op]
            nxt = new_sequence[idx + 1]
            new_q[idx] = max(
                operation_tail(schedule, job_successor),
                new_q[idx + 1] + schedule.index.duration(nxt, schedule.on_machine[nxt]),
            )
    elif move.method == BACK:
        new_sequence = [move.which]
        node = move.where
        while node != move.which:
            new_sequence.append(node)
            node = schedule.machine_predecessor[node]
        n = len(new_sequence)
        new_r = [0] * n
        new_q = [0] * n

        job_successor = schedule.job_successor[move.which]
        machine_successor = schedule.machine_successor[move.where]
        new_q[0] = operation_tail(schedule, job_successor)
        if machine_successor != -1:
            new_q[0] = max(new_q[0], operation_tail(schedule, machine_successor))
        for idx in range(1, n):
            op = new_sequence[idx]
            job_successor = schedule.job_successor[op]
            prev = new_sequence[idx - 1]
            new_q[idx] = max(
                operation_tail(schedule, job_successor),
                new_q[idx - 1] + schedule.index.duration(prev, schedule.on_machine[prev]),
            )

        last = new_sequence[-1]
        job_predecessor = schedule.job_predecessor[last]
        machine_predecessor = schedule.machine_predecessor[move.which]
        new_r[-1] = schedule.end_time[job_predecessor]
        if machine_predecessor != -1:
            new_r[-1] = max(new_r[-1], schedule.end_time[machine_predecessor])
        for idx in range(n - 2, -1, -1):
            op = new_sequence[idx]
            job_predecessor = schedule.job_predecessor[op]
            nxt = new_sequence[idx + 1]
            new_r[idx] = max(
                schedule.end_time[job_predecessor],
                new_r[idx + 1] + schedule.index.duration(nxt, schedule.on_machine[nxt]),
            )
    else:
        raise ValueError("not a same-machine move")

    value = 0.0
    for idx, node in enumerate(new_sequence):
        value = max(
            value,
            new_r[idx]
            + schedule.index.duration(node, schedule.on_machine[node])
            + new_q[idx]
            + weight_perturbation(schedule, node, gamma),
        )
    return value


def same_machine_evaluate(schedule: AwlsSchedule, move: Move, gamma: int, eval_mode: str) -> float:
    if eval_mode == "cpp-fast":
        return same_machine_evaluate_cpp_fast(schedule, move, gamma)
    return same_machine_evaluate_stable(schedule, move, gamma)


def change_machine_intersection(schedule: AwlsSchedule, node: int, candidate_machine: int) -> tuple[list[int], list[int], list[int]]:
    job_predecessor = schedule.job_predecessor[node]
    job_successor = schedule.job_successor[node]
    remove_r = schedule.end_time[job_predecessor]
    remove_q = 0
    if job_successor != schedule.index.end_node:
        remove_q = schedule.backward_path_length[job_successor] + schedule.index.duration(
            job_successor,
            schedule.on_machine[job_successor],
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
            index = next((i for i, value in enumerate(rk) if value == lk[-1]), None)
            if index is not None:
                offset = len(lk) - 1 - index
                for i in range(index + 1):
                    if offset + i >= 0 and rk[i] == lk[offset + i]:
                        intersection.append(rk[i])
        else:
            index = next((i for i in range(len(lk) - 1, -1, -1) if lk[i] == rk[0]), None)
            if index is not None:
                for i in range(index, len(lk)):
                    rk_index = i - index
                    if rk_index < len(rk) and lk[i] == rk[rk_index]:
                        intersection.append(lk[i])
                    else:
                        break
    return rk, lk, intersection


def change_machine_evaluate(schedule: AwlsSchedule, move: Move, intersection: list[int], gamma: int) -> float:
    machine_id = schedule.on_machine[move.where]
    job_predecessor = schedule.job_predecessor[move.which]
    job_successor = schedule.job_successor[move.which]
    which_time = schedule.index.duration(move.which, machine_id)
    job_successor_time = 0
    if job_successor != schedule.index.end_node:
        job_successor_time = schedule.index.duration(job_successor, schedule.on_machine[job_successor])
    where_time = schedule.index.duration(move.where, machine_id)
    zi = weight_perturbation(schedule, move.which, gamma)

    if not intersection:
        return (
            which_time
            + schedule.end_time[job_predecessor]
            + schedule.backward_path_length[job_successor]
            + job_successor_time
            + zi
        )
    if move.method == CHANGE_MACHINE_FRONT and move.where == intersection[0]:
        return which_time + schedule.end_time[job_predecessor] + where_time + schedule.backward_path_length[move.where] + zi
    if move.method == CHANGE_MACHINE_BACK and move.where == intersection[-1]:
        return which_time + schedule.end_time[move.where] + schedule.backward_path_length[job_successor] + job_successor_time + zi
    machine_successor = schedule.machine_successor[move.where]
    if machine_successor == -1:
        return which_time + schedule.end_time[move.where] + zi
    return (
        which_time
        + schedule.end_time[move.where]
        + schedule.backward_path_length[machine_successor]
        + schedule.index.duration(machine_successor, machine_id)
        + zi
    )


def is_legal_same_machine_move(schedule: AwlsSchedule, move: Move) -> bool:
    if schedule.on_machine[move.which] != schedule.on_machine[move.where]:
        return False
    if move.which == move.where:
        return False
    if move.method == BACK:
        if schedule.on_machine_pos[move.where] <= schedule.on_machine_pos[move.which]:
            return False
        job_successor = schedule.job_successor[move.which]
        if job_successor == move.where:
            return False
        successor_tail = 0
        if job_successor != schedule.index.end_node:
            successor_tail = schedule.backward_path_length[job_successor] + schedule.index.duration(
                job_successor,
                schedule.on_machine[job_successor],
            )
        return (
            schedule.backward_path_length[move.where] + schedule.index.duration(move.where, schedule.on_machine[move.where])
            >= successor_tail
        )
    if move.method == FRONT:
        if schedule.on_machine_pos[move.where] >= schedule.on_machine_pos[move.which]:
            return False
        job_predecessor = schedule.job_predecessor[move.which]
        if move.where == job_predecessor:
            return False
        return schedule.end_time[move.where] >= schedule.end_time[job_predecessor]
    return False


def candidate_tabu_sequence(schedule: AwlsSchedule, move: Move) -> tuple[int, list[int]]:
    machine_id = schedule.on_machine[move.where]
    if move.method == FRONT:
        sequence = [move.which]
        node = move.where
        while node != move.which:
            sequence.append(node)
            node = schedule.machine_successor[node]
        return machine_id, sequence
    if move.method == BACK:
        sequence = []
        node = schedule.machine_successor[move.which]
        stop = schedule.machine_successor[move.where]
        while node != stop:
            sequence.append(node)
            node = schedule.machine_successor[node]
        sequence.append(move.which)
        return machine_id, sequence
    if move.method == CHANGE_MACHINE_BACK:
        successor = schedule.machine_successor[move.where]
        sequence = [move.where, move.which]
        if successor != -1:
            sequence.append(successor)
        return machine_id, sequence
    predecessor = schedule.machine_predecessor[move.where]
    sequence = []
    if predecessor != -1:
        sequence.append(predecessor)
    sequence.extend([move.which, move.where])
    return machine_id, sequence


def add_move_tabu(tabu: SequenceTabuList, schedule: AwlsSchedule, move: Move, iteration: int, tenure_min: int, tenure_max: int) -> None:
    machine_id = schedule.on_machine[move.which]
    if move.method == FRONT:
        sequence = []
        node = move.where
        stop = schedule.machine_successor[move.which]
        while node != stop:
            sequence.append(node)
            node = schedule.machine_successor[node]
    elif move.method == BACK:
        sequence = []
        node = move.which
        stop = schedule.machine_successor[move.where]
        while node != stop:
            sequence.append(node)
            node = schedule.machine_successor[node]
    else:
        sequence = []
        predecessor = schedule.machine_predecessor[move.which]
        successor = schedule.machine_successor[move.which]
        if predecessor != -1:
            sequence.append(predecessor)
        sequence.append(move.which)
        if successor != -1:
            sequence.append(successor)
    tabu.add(machine_id, sequence, iteration + schedule.rng.randint(tenure_min, tenure_max))


def is_change_move_acyclic(schedule: AwlsSchedule, move: Move) -> bool:
    try:
        trial = schedule.clone()
        trial.apply_move(move)
        return True
    except (ValueError, KeyError):
        return False


def evaluate_and_push(
    schedule: AwlsSchedule,
    best_makespan: int,
    tabu: SequenceTabuList,
    iteration: int,
    move: Move,
    all_moves: list[Move],
    ranked_moves: list[tuple[float, Move]],
    best_moves: list[Move],
    best_value: list[float],
    gamma: int,
    intersection: list[int] | None = None,
) -> None:
    try:
        if move.method in (FRONT, BACK):
            if not is_legal_same_machine_move(schedule, move):
                return
            value = same_machine_evaluate(schedule, move, gamma, schedule.same_machine_eval)
        else:
            value = change_machine_evaluate(schedule, move, intersection or [], gamma)
    except (ValueError, KeyError):
        return

    all_moves.append(move)
    machine_id, sequence = candidate_tabu_sequence(schedule, move)
    if value >= best_makespan and tabu.is_tabu(machine_id, sequence, iteration):
        return
    ranked_moves.append((value, move))
    if value < best_value[0] - 1.0e-9:
        best_value[0] = value
        best_moves.clear()
        best_moves.append(move)
    elif abs(value - best_value[0]) <= 1.0e-9:
        best_moves.append(move)


def find_move(
    schedule: AwlsSchedule,
    best_makespan: int,
    tabu: SequenceTabuList,
    iteration: int,
    gamma: int,
    exact_select_top_k: int,
    critical_block_exhaustive_pct: int,
) -> Move | None:
    all_moves: list[Move] = []
    ranked_moves: list[tuple[float, Move]] = []
    best_moves: list[Move] = []
    best_value = [math.inf]

    exhaustive_first = schedule.rng.randrange(100) < max(0, min(100, critical_block_exhaustive_pct))
    exhaustive_modes = (True, False) if exhaustive_first else (False, True)
    for exhaustive in exhaustive_modes:
        blocks = critical_blocks(schedule, schedule.rng, exhaustive=exhaustive)
        for block in blocks:
            machine_id = schedule.on_machine[block[0]]
            sequence = schedule.machine_sequences[machine_id]
            block_start = sequence.index(block[0])
            block_end = sequence.index(block[-1])

            for node in block:
                for target in sequence[:block_start]:
                    evaluate_and_push(
                        schedule,
                        best_makespan,
                        tabu,
                        iteration,
                        Move(FRONT, node, target),
                        all_moves,
                        ranked_moves,
                        best_moves,
                        best_value,
                        gamma,
                    )
                for target in sequence[block_end + 1 :]:
                    evaluate_and_push(
                        schedule,
                        best_makespan,
                        tabu,
                        iteration,
                        Move(BACK, node, target),
                        all_moves,
                        ranked_moves,
                        best_moves,
                        best_value,
                        gamma,
                    )

            n = len(block)
            if n == 2:
                evaluate_and_push(
                    schedule,
                    best_makespan,
                    tabu,
                    iteration,
                    Move(BACK, block[0], block[1]),
                    all_moves,
                    ranked_moves,
                    best_moves,
                    best_value,
                    gamma,
                )
            else:
                for j in range(2, n):
                    evaluate_and_push(
                        schedule,
                        best_makespan,
                        tabu,
                        iteration,
                        Move(BACK, block[0], block[j]),
                        all_moves,
                        ranked_moves,
                        best_moves,
                        best_value,
                        gamma,
                    )
                for j in range(n - 2, -1, -1):
                    evaluate_and_push(
                        schedule,
                        best_makespan,
                        tabu,
                        iteration,
                        Move(FRONT, block[-1], block[j]),
                        all_moves,
                        ranked_moves,
                        best_moves,
                        best_value,
                        gamma,
                    )
                for j in range(1, n - 1):
                    evaluate_and_push(
                        schedule,
                        best_makespan,
                        tabu,
                        iteration,
                        Move(FRONT, block[j], block[0]),
                        all_moves,
                        ranked_moves,
                        best_moves,
                        best_value,
                        gamma,
                    )
                for j in range(1, n - 1):
                    evaluate_and_push(
                        schedule,
                        best_makespan,
                        tabu,
                        iteration,
                        Move(BACK, block[j], block[-1]),
                        all_moves,
                        ranked_moves,
                        best_moves,
                        best_value,
                        gamma,
                    )

        if not exhaustive:
            for node in schedule.index.real_nodes:
                if not schedule.is_critical_operation(node):
                    continue
                old_machine = schedule.on_machine[node]
                if schedule.machine_operation_count[old_machine] <= 1:
                    continue
                for candidate_machine in schedule.index.candidates[node]:
                    if candidate_machine == old_machine or not schedule.machine_sequences[candidate_machine]:
                        continue
                    rk, lk, intersection = change_machine_intersection(schedule, node, candidate_machine)
                    if intersection:
                        evaluate_and_push(
                            schedule,
                            best_makespan,
                            tabu,
                            iteration,
                            Move(CHANGE_MACHINE_FRONT, node, intersection[0]),
                            all_moves,
                            ranked_moves,
                            best_moves,
                            best_value,
                            gamma,
                            intersection,
                        )
                        for target in intersection:
                            evaluate_and_push(
                                schedule,
                                best_makespan,
                                tabu,
                                iteration,
                                Move(CHANGE_MACHINE_BACK, node, target),
                                all_moves,
                                ranked_moves,
                                best_moves,
                                best_value,
                                gamma,
                                intersection,
                            )
                    elif lk and rk:
                        sequence = schedule.machine_sequences[candidate_machine]
                        start = sequence.index(lk[-1])
                        stop = sequence.index(rk[0])
                        if start < stop:
                            targets = sequence[start:stop]
                        else:
                            targets = []
                        for target in targets:
                            evaluate_and_push(
                                schedule,
                                best_makespan,
                                tabu,
                                iteration,
                                Move(CHANGE_MACHINE_BACK, node, target),
                                all_moves,
                                ranked_moves,
                                best_moves,
                                best_value,
                                gamma,
                                intersection,
                            )
                    elif not lk and rk:
                        evaluate_and_push(
                            schedule,
                            best_makespan,
                            tabu,
                            iteration,
                            Move(CHANGE_MACHINE_FRONT, node, rk[0]),
                            all_moves,
                            ranked_moves,
                            best_moves,
                            best_value,
                            gamma,
                            intersection,
                        )
                    elif lk and not rk:
                        evaluate_and_push(
                            schedule,
                            best_makespan,
                            tabu,
                            iteration,
                            Move(CHANGE_MACHINE_BACK, node, lk[-1]),
                            all_moves,
                            ranked_moves,
                            best_moves,
                            best_value,
                            gamma,
                            intersection,
                        )

        if all_moves:
            break

    if not all_moves:
        return None
    if exact_select_top_k > 0 and ranked_moves:
        exact_best: tuple[int, float, Move] | None = None
        for approx_value, move in sorted(ranked_moves, key=lambda item: item[0])[:exact_select_top_k]:
            try:
                trial = schedule.clone()
                trial.apply_move(move)
            except (ValueError, KeyError):
                continue
            key = (trial.makespan, approx_value, move)
            if exact_best is None or key[:2] < exact_best[:2]:
                exact_best = key
        if exact_best is not None:
            return exact_best[2]
    if not best_moves:
        return schedule.rng.choice(all_moves)
    if best_value[0] > schedule.makespan and schedule.rng.randrange(100) < 3:
        return schedule.rng.choice(all_moves)
    return schedule.rng.choice(best_moves)


def update_operation_weights(
    schedule: AwlsSchedule,
    moved_node: int,
    best_makespan_before: int,
    previous_makespan: int,
    current_makespan: int,
    beta: int,
    gamma: int,
    theta: int,
    zi_policy: str,
) -> None:
    critical = {node for node in schedule.index.real_nodes if schedule.is_critical_operation(node)}
    if current_makespan >= previous_makespan:
        if schedule.op_cooldown[moved_node] > beta:
            schedule.op_weight[moved_node] = 0
        else:
            increment = 1
            if zi_policy == "aggressive":
                increment = 2
            elif zi_policy == "critical" and moved_node in critical:
                increment = 2
            schedule.op_weight[moved_node] += increment

        if schedule.op_cooldown[moved_node] > gamma:
            schedule.op_cooldown[moved_node] = gamma
        else:
            cooldown_step = theta
            if zi_policy == "aggressive":
                cooldown_step = max(theta + 1, theta * 2)
            schedule.op_cooldown[moved_node] = max(schedule.op_cooldown[moved_node] - cooldown_step, 0)

        for node in schedule.index.real_nodes:
            if node not in critical and node != moved_node:
                schedule.op_cooldown[node] += 2 if zi_policy == "aggressive" else 1
    else:
        for node in schedule.index.real_nodes:
            schedule.op_cooldown[node] += 1

    if current_makespan < best_makespan_before:
        for node in schedule.index.real_nodes:
            schedule.op_cooldown[node] = 10**9
            schedule.op_weight[node] = 0


def tabu_search(
    initial: AwlsSchedule,
    iterations: int,
    time_limit_sec: float,
    beta: int,
    gamma: int,
    theta: int,
    exact_select_top_k: int,
    same_machine_eval: str,
    critical_block_exhaustive_pct: int,
    zi_policy: str,
) -> AwlsSchedule:
    current = initial
    current.same_machine_eval = same_machine_eval
    current.zi_policy = zi_policy
    best = initial.clone()
    tabu = SequenceTabuList(current.index.instance.machine_count)
    tenure_min = max(1, 10 + current.index.instance.job_count // max(1, current.index.instance.machine_count))
    tenure_max = max(tenure_min, int(math.ceil(tenure_min * 1.5)))
    deadline = time.perf_counter() + time_limit_sec if time_limit_sec > 0 else None

    for iteration in range(iterations):
        if deadline is not None and time.perf_counter() >= deadline:
            break
        move = find_move(
            current,
            best.makespan,
            tabu,
            iteration,
            gamma,
            exact_select_top_k,
            critical_block_exhaustive_pct,
        )
        if move is None:
            break
        previous_makespan = current.makespan
        best_before = best.makespan
        add_move_tabu(tabu, current, move, iteration, tenure_min, tenure_max)
        try:
            current.apply_move(move)
        except (ValueError, KeyError):
            continue
        update_operation_weights(
            current,
            move.which,
            best_before,
            previous_makespan,
            current.makespan,
            beta,
            gamma,
            theta,
            zi_policy,
        )
        if current.makespan < best.makespan:
            best = current.clone()
    return best


def build_initial_schedule(index: OperationIndex, rng: random.Random, restart: int, mode: str) -> AwlsSchedule:
    if mode == "random":
        sequences, on_machine = random_init(index, rng)
    elif mode == "greedy":
        sequences, on_machine = greedy_gt_init(index, rng)
    elif mode == "mixed":
        if restart % 3 == 0:
            sequences, on_machine = greedy_gt_init(index, rng, random_factor=0.08, idle_bonus=0.10)
        elif restart % 3 == 1:
            sequences, on_machine = greedy_gt_init(index, rng, random_factor=0.25, idle_bonus=0.25)
        else:
            sequences, on_machine = random_init(index, rng)
    else:
        raise ValueError(f"unknown init mode: {mode}")
    return AwlsSchedule(index, sequences, on_machine, rng)


def parse_portfolio_lanes(raw: str) -> list[PortfolioLane]:
    """Parse comma-separated portfolio lane specs.

    Format: ``seed:init:restarts[:seconds]``.  Example:
    ``17:random:2:20,5:random:4:60,23:mixed:2:20``.
    """

    lanes: list[PortfolioLane] = []
    for chunk in raw.split(","):
        text = chunk.strip()
        if not text:
            continue
        parts = text.split(":")
        if len(parts) not in (3, 4):
            raise ValueError(f"invalid portfolio lane {text!r}; expected seed:init:restarts[:seconds]")
        seed = int(parts[0])
        init_mode = parts[1]
        if init_mode not in {"random", "greedy", "mixed"}:
            raise ValueError(f"invalid lane init mode {init_mode!r}; expected random, greedy, or mixed")
        restarts = max(1, int(parts[2]))
        seconds = float(parts[3]) if len(parts) == 4 else None
        if seconds is not None and seconds <= 0:
            raise ValueError(f"invalid lane time limit {seconds!r}; expected a positive value")
        lanes.append(PortfolioLane(seed=seed, init_mode=init_mode, restarts=restarts, time_limit_sec=seconds))
    return lanes


def allocate_lane_budgets(lanes: list[PortfolioLane], time_limit_sec: float) -> list[float]:
    """Allocate per-lane wall-clock budgets while respecting a global cap when present."""

    if time_limit_sec <= 0:
        return [lane.time_limit_sec if lane.time_limit_sec is not None else 0.0 for lane in lanes]

    specified = sum(lane.time_limit_sec or 0.0 for lane in lanes)
    unspecified_count = sum(1 for lane in lanes if lane.time_limit_sec is None)
    if unspecified_count:
        remaining = max(0.1 * unspecified_count, time_limit_sec - specified)
        default_budget = remaining / unspecified_count
    else:
        default_budget = 0.0

    budgets = [lane.time_limit_sec if lane.time_limit_sec is not None else default_budget for lane in lanes]
    total = sum(budgets)
    if total > time_limit_sec:
        scale = time_limit_sec / total
        budgets = [max(0.1, budget * scale) for budget in budgets]
    return budgets


def solve_awls_single(
    index: OperationIndex,
    seed: int,
    restarts: int,
    cycles_per_restart: int,
    iterations: int,
    time_limit_sec: float,
    init_mode: str,
    beta: int,
    gamma: int,
    theta: int,
    exact_select_top_k: int,
    same_machine_eval: str,
    critical_block_exhaustive_pct: int = 0,
    zi_policy: str = "cpp",
) -> AwlsSchedule:
    rng = random.Random(seed)
    best: AwlsSchedule | None = None
    deadline = time.perf_counter() + time_limit_sec if time_limit_sec > 0 else None

    for restart in range(max(1, restarts)):
        remaining_time = max(0.0, deadline - time.perf_counter()) if deadline is not None else 0.0
        if deadline is not None and remaining_time <= 0:
            break
        remaining_restarts = max(1, restarts - restart)
        restart_budget = remaining_time / remaining_restarts if deadline is not None else 0.0
        restart_deadline = time.perf_counter() + restart_budget if deadline is not None else None
        initial = build_initial_schedule(index, rng, restart, init_mode)
        if best is None or initial.makespan < best.makespan:
            best = initial.clone()
        population = initial
        for _ in range(max(1, cycles_per_restart)):
            remaining_cycle_time = max(0.0, restart_deadline - time.perf_counter()) if restart_deadline is not None else 0.0
            if restart_deadline is not None and remaining_cycle_time <= 0:
                break
            per_cycle_limit = remaining_cycle_time if restart_deadline is not None else 0.0
            improved = tabu_search(
                population,
                iterations,
                per_cycle_limit,
                beta,
                gamma,
                theta,
                exact_select_top_k,
                same_machine_eval,
                critical_block_exhaustive_pct,
                zi_policy,
            )
            population = improved.clone()
            if best is None or improved.makespan < best.makespan:
                best = improved.clone()

    if best is None:
        raise RuntimeError("AWLS failed to build an initial schedule")
    return best


def solve_awls(
    instance: StandardFjspInstance,
    seed: int,
    restarts: int,
    cycles_per_restart: int,
    iterations: int,
    time_limit_sec: float,
    init_mode: str,
    beta: int,
    gamma: int,
    theta: int,
    exact_select_top_k: int,
    same_machine_eval: str = "stable",
    portfolio_lanes: list[PortfolioLane] | None = None,
    critical_block_exhaustive_pct: int = 0,
    zi_policy: str = "cpp",
) -> tuple[list[ScheduleRecord], str]:
    if zi_policy not in ZI_POLICY_CHOICES:
        raise ValueError(f"unknown zi_policy {zi_policy!r}; expected one of {', '.join(ZI_POLICY_CHOICES)}")
    index = OperationIndex.from_instance(instance)
    if portfolio_lanes:
        lane_budgets = allocate_lane_budgets(portfolio_lanes, time_limit_sec)
        best: AwlsSchedule | None = None
        best_lane: PortfolioLane | None = None
        lane_summaries: list[str] = []
        for lane, lane_budget in zip(portfolio_lanes, lane_budgets, strict=True):
            effective_lane_seed = lane.seed + seed * PORTFOLIO_OUTER_SEED_STRIDE
            candidate = solve_awls_single(
                index,
                seed=effective_lane_seed,
                restarts=lane.restarts,
                cycles_per_restart=cycles_per_restart,
                iterations=iterations,
                time_limit_sec=lane_budget,
                init_mode=lane.init_mode,
                beta=beta,
                gamma=gamma,
                theta=theta,
                exact_select_top_k=exact_select_top_k,
                same_machine_eval=same_machine_eval,
                critical_block_exhaustive_pct=critical_block_exhaustive_pct,
                zi_policy=zi_policy,
            )
            lane_summaries.append(
                f"{effective_lane_seed}/{lane.init_mode}/r{lane.restarts}/t{lane_budget:.1f}=m{candidate.makespan}"
            )
            if best is None or candidate.makespan < best.makespan:
                best = candidate.clone()
                best_lane = PortfolioLane(
                    seed=effective_lane_seed,
                    init_mode=lane.init_mode,
                    restarts=lane.restarts,
                    time_limit_sec=lane.time_limit_sec,
                )
        if best is None or best_lane is None:
            raise RuntimeError("AWLS portfolio did not run any lane")
        label = (
            "awls-portfolio:"
            f"outer_seed={seed}:selected={best_lane.seed}/{best_lane.init_mode}/r{best_lane.restarts}:"
            f"cycles={cycles_per_restart}:iterations={iterations}:eval={same_machine_eval}:"
            f"exhaustive_pct={critical_block_exhaustive_pct}:zi={zi_policy}:makespan={best.makespan}:"
            f"lanes={'|'.join(lane_summaries)}"
        )
        return best.to_records(), label

    best = solve_awls_single(
        index,
        seed=seed,
        restarts=restarts,
        cycles_per_restart=cycles_per_restart,
        iterations=iterations,
        time_limit_sec=time_limit_sec,
        init_mode=init_mode,
        beta=beta,
        gamma=gamma,
        theta=theta,
        exact_select_top_k=exact_select_top_k,
        same_machine_eval=same_machine_eval,
        critical_block_exhaustive_pct=critical_block_exhaustive_pct,
        zi_policy=zi_policy,
    )
    label = (
        f"awls:init={init_mode}:restarts={restarts}:cycles={cycles_per_restart}:"
        f"iterations={iterations}:seed={seed}:eval={same_machine_eval}:"
        f"exhaustive_pct={critical_block_exhaustive_pct}:zi={zi_policy}:makespan={best.makespan}"
    )
    return best.to_records(), label


def main() -> int:
    parser = argparse.ArgumentParser(description="AWLS-style solver for standard FJSP instances.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--restarts", type=int, default=2)
    parser.add_argument("--cycles-per-restart", type=int, default=1000)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--time-limit-sec", type=float, default=10.0)
    parser.add_argument("--init", choices=("random", "greedy", "mixed"), default="mixed")
    parser.add_argument("--beta", type=int, default=500)
    parser.add_argument("--gamma", type=int, default=40)
    parser.add_argument("--theta", type=int, default=5)
    parser.add_argument("--zi-policy", choices=ZI_POLICY_CHOICES, default="cpp")
    parser.add_argument("--exact-select-top-k", type=int, default=0)
    parser.add_argument(
        "--critical-block-exhaustive-pct",
        type=int,
        default=0,
        help="Percent chance to evaluate all critical blocks first; 5 approximates the C++ AWLS exploration branch.",
    )
    parser.add_argument("--same-machine-eval", choices=("stable", "cpp-fast"), default="stable")
    parser.add_argument(
        "--portfolio-lanes",
        default="",
        help="Comma-separated lanes: seed:init:restarts[:seconds], for example 17:random:2:20,5:mixed:2:20.",
    )
    args = parser.parse_args()

    start = time.perf_counter()
    instance = parse_standard_fjsp(args.input)
    portfolio_lanes = parse_portfolio_lanes(args.portfolio_lanes) if args.portfolio_lanes else None
    schedule, strategy = solve_awls(
        instance,
        seed=args.seed,
        restarts=args.restarts,
        cycles_per_restart=args.cycles_per_restart,
        iterations=args.iterations,
        time_limit_sec=args.time_limit_sec,
        init_mode=args.init,
        beta=args.beta,
        gamma=args.gamma,
        theta=args.theta,
        zi_policy=args.zi_policy,
        exact_select_top_k=args.exact_select_top_k,
        same_machine_eval=args.same_machine_eval,
        portfolio_lanes=portfolio_lanes,
        critical_block_exhaustive_pct=args.critical_block_exhaustive_pct,
    )
    errors, metrics = validate_standard_schedule(instance, schedule)
    if errors:
        for error in errors[:20]:
            print(f"[awls][error] {error}", file=sys.stderr)
        return 2

    runtime_sec = time.perf_counter() - start
    write_solution(args.output, instance, schedule, strategy)
    print(
        {
            "instance": instance.name,
            "makespan": int(metrics["makespan"]),
            "scheduled_operations": int(metrics["scheduled_operations"]),
            "runtime_sec": round(runtime_sec, 3),
            "strategy": strategy,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
