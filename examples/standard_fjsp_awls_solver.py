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
import ast
import json
import math
import random
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
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

try:
    from examples.awls_evolved_slots import safe_evolved_zi
except Exception:  # pragma: no cover - compile checks surface malformed slots.
    safe_evolved_zi = None


START_NODE = 0
FRONT = "FRONT"
BACK = "BACK"
CHANGE_MACHINE_FRONT = "CHANGE_MACHINE_FRONT"
CHANGE_MACHINE_BACK = "CHANGE_MACHINE_BACK"
COOLDOWN_INFINITY = 10**9
CPP_INT_MIN = -(2**31)
CPP_INT_MAX = 2**31 - 1
ZI_POLICY_CHOICES = ("cpp", "cpp-exact", "none", "sqrt", "aggressive", "critical", "formula", "slot")
PORTFOLIO_OUTER_SEED_STRIDE = 1_000_003
ZI_FORMULA_MAX_LEN = 240
ZI_FORMULA_MAX_ABS = 1.0e9
ZI_FORMULA_ALLOWED_NAMES = {
    "weight",
    "cooldown",
    "rr",
    "gamma",
    "cooling",
    "base",
    "sqrt_weight",
    "log_weight",
    "is_critical",
    "forward",
    "backward",
    "duration",
    "machine_load",
    "position",
}
ZI_FORMULA_FUNCTIONS = {
    "abs": abs,
    "max": max,
    "min": min,
    "sqrt": math.sqrt,
    "log1p": math.log1p,
}
ZI_FORMULA_ALLOWED_AST = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)


@dataclass(frozen=True, slots=True)
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


def validate_zi_formula(expression: str) -> str:
    """Validate the small arithmetic DSL used for LLM-evolved zi formulas."""

    formula = (expression or "").strip()
    if not formula:
        raise ValueError("zi_formula is required when zi_policy='formula'")
    if len(formula) > ZI_FORMULA_MAX_LEN:
        raise ValueError(f"zi_formula is too long; max length is {ZI_FORMULA_MAX_LEN}")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid zi_formula syntax: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, ZI_FORMULA_ALLOWED_AST):
            raise ValueError(f"unsupported zi_formula syntax: {type(node).__name__}")
        if isinstance(node, ast.Name):
            allowed = ZI_FORMULA_ALLOWED_NAMES | set(ZI_FORMULA_FUNCTIONS)
            if node.id not in allowed:
                raise ValueError(f"unknown zi_formula symbol: {node.id}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ZI_FORMULA_FUNCTIONS:
                raise ValueError("zi_formula only supports whitelisted math functions")
            if node.keywords:
                raise ValueError("zi_formula function calls cannot use keyword arguments")
            if len(node.args) > 4:
                raise ValueError("zi_formula function calls support at most four arguments")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("zi_formula constants must be numeric")
            if abs(float(node.value)) > ZI_FORMULA_MAX_ABS:
                raise ValueError("zi_formula numeric constants are too large")
        elif isinstance(node, ast.Pow):
            # Keep exponent-heavy expressions from creating extremely slow or
            # numerically unstable candidates.
            parent_nodes = list(ast.walk(tree))
            for parent in parent_nodes:
                if isinstance(parent, ast.BinOp) and parent.op is node:
                    if isinstance(parent.right, ast.Constant) and abs(float(parent.right.value)) <= 4:
                        continue
                    raise ValueError("zi_formula exponent must be a numeric constant with abs <= 4")
    return formula


@lru_cache(maxsize=256)
def compile_zi_formula(expression: str) -> object:
    """Compile a validated zi formula once and reuse it during move scoring."""

    formula = validate_zi_formula(expression)
    return compile(ast.parse(formula, mode="eval"), "<zi_formula>", "eval")


def evaluate_zi_formula(expression: str, values: dict[str, float]) -> float:
    """Evaluate a validated zi formula with bounded numeric output."""

    namespace = dict(ZI_FORMULA_FUNCTIONS)
    namespace.update(values)
    result = eval(compile_zi_formula(expression), {"__builtins__": {}}, namespace)
    value = float(result)
    if not math.isfinite(value):
        return 0.0
    return min(ZI_FORMULA_MAX_ABS, max(0.0, value))


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

    def __getitem__(self, op_key: tuple[int, int]) -> int:
        job_id, op_id = op_key
        return self.job_to_nodes[job_id][op_id] - 1

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
        initial_cooldown: int = COOLDOWN_INFINITY,
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
        self.op_cooldown = [initial_cooldown] * n
        self.same_machine_eval = "stable"
        self.zi_policy = "cpp"
        self.zi_formula = ""
        self.awls_stats: dict[str, int] = {}
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
        cloned.zi_formula = self.zi_formula
        cloned.awls_stats = dict(self.awls_stats)
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

    def rebuild_machine_links_for(self, machine_ids: list[int]) -> None:
        """Refresh machine arcs only for machines affected by one local move."""

        for machine_id in dict.fromkeys(machine_ids):
            sequence = self.machine_sequences[machine_id]
            self.machine_operation_count[machine_id] = len(sequence)
            self.first_machine_operation[machine_id] = sequence[0] if sequence else -1
            self.last_machine_operation[machine_id] = sequence[-1] if sequence else -1
            for node in sequence:
                self.machine_predecessor[node] = -1
                self.machine_successor[node] = -1

        for machine_id in dict.fromkeys(machine_ids):
            sequence = self.machine_sequences[machine_id]
            for pos, node in enumerate(sequence):
                self.on_machine[node] = machine_id
                self.on_machine_pos[node] = pos
                if pos > 0:
                    self.machine_predecessor[node] = sequence[pos - 1]
                if pos + 1 < len(sequence):
                    self.machine_successor[node] = sequence[pos + 1]

    def topological_sort(self) -> list[int]:
        n = self.index.node_count
        indegree = [0] * n

        for node in self.index.real_nodes:
            job_successor = self.job_successor[node]
            if job_successor != -1:
                indegree[job_successor] += 1
            machine_successor = self.machine_successor[node]
            if machine_successor != -1:
                indegree[machine_successor] += 1

        for first in self.first_job_operation:
            if first != -1:
                indegree[first] += 1

        ready = deque([node for node in range(n) if indegree[node] == 0])
        order: list[int] = []
        while ready:
            node = ready.popleft()
            order.append(node)
            if node == START_NODE:
                successors = self.first_job_operation
            elif START_NODE < node < self.index.end_node:
                successors = (self.job_successor[node], self.machine_successor[node])
            else:
                successors = ()
            for successor in successors:
                if successor == -1:
                    continue
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)

        if len(order) != n:
            raise ValueError("cycle detected in disjunctive graph")
        return order

    def update_time(self) -> None:
        # SLOT awls_sdst_time_propagation START
        order = self.topological_sort()
        self.topological_order = order
        node_count = self.index.node_count
        end_node = self.index.end_node
        durations = self.index.candidates
        on_machine = self.on_machine
        job_predecessor = self.job_predecessor
        machine_predecessor = self.machine_predecessor
        job_successor = self.job_successor
        machine_successor = self.machine_successor
        self.forward_path_length = [0] * node_count
        self.end_time = [0] * node_count
        self.backward_path_length = [0] * node_count
        self.makespan = 0
        forward_path_length = self.forward_path_length
        end_time = self.end_time
        backward_path_length = self.backward_path_length

        from harness_agent.standard_fjsp import setup_time_between
        instance = self.index.instance
        has_sdst = instance.has_sequence_dependent_setup
        node_to_job = self.index.node_to_job
        node_to_op = self.index.node_to_op

        for node in order:
            if node <= START_NODE or node >= end_node:
                continue
            start = 0
            job_prev = job_predecessor[node]
            machine_prev = machine_predecessor[node]
            if job_prev != -1:
                start = max(start, end_time[job_prev])
            if machine_prev != -1:
                machine_id = on_machine[node]
                if has_sdst:
                    prev_op = (node_to_job[machine_prev], node_to_op[machine_prev])
                    cur_op = (node_to_job[node], node_to_op[node])
                    setup = setup_time_between(instance, machine_id, prev_op, cur_op, self.index)
                else:
                    setup = 0
                start = max(start, end_time[machine_prev] + setup)
            end = start + durations[node][on_machine[node]]
            forward_path_length[node] = start
            end_time[node] = end
            self.makespan = max(self.makespan, end)

        for node in reversed(order):
            if node <= START_NODE or node >= end_node:
                continue
            q = 0
            job_next = job_successor[node]
            machine_next = machine_successor[node]
            if job_next != end_node:
                machine_id = on_machine[job_next]
                q = max(
                    q,
                    backward_path_length[job_next]
                    + durations[job_next][machine_id],
                )
            if machine_next != -1:
                machine_id = on_machine[machine_next]
                if has_sdst:
                    cur_op = (node_to_job[node], node_to_op[node])
                    next_op = (node_to_job[machine_next], node_to_op[machine_next])
                    setup = setup_time_between(instance, machine_id, cur_op, next_op, self.index)
                else:
                    setup = 0
                q = max(
                    q,
                    setup + backward_path_length[machine_next]
                    + durations[machine_next][machine_id],
                )
            backward_path_length[node] = q
        # SLOT awls_sdst_time_propagation END

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
            which_pos = self.on_machine_pos[move.which]
            where_pos = self.on_machine_pos[move.where]
            sequence.pop(which_pos)
            if which_pos < where_pos:
                where_pos -= 1
            insert_pos = where_pos if move.method == FRONT else where_pos + 1
            sequence.insert(insert_pos, move.which)
            affected_machines = [old_machine]
        elif move.method in (CHANGE_MACHINE_FRONT, CHANGE_MACHINE_BACK):
            if target_machine not in self.index.candidates[move.which]:
                raise ValueError("target machine is not a candidate")
            old_sequence = self.machine_sequences[old_machine]
            old_sequence.pop(self.on_machine_pos[move.which])
            target_sequence = self.machine_sequences[target_machine]
            where_pos = self.on_machine_pos[move.where]
            insert_pos = where_pos if move.method == CHANGE_MACHINE_FRONT else where_pos + 1
            target_sequence.insert(insert_pos, move.which)
            self.on_machine[move.which] = target_machine
            affected_machines = [old_machine, target_machine]
        else:
            raise ValueError(f"unknown move method: {move.method}")

        self.rebuild_machine_links_for(affected_machines)
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
            threshold = int(best_completion * (1.0 + random_factor + idle_bonus))
            filtered = [choice for choice in choices if choice[3] and choice[2] <= threshold]
        if not filtered:
            threshold = int(best_completion * (1.0 + random_factor))
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
    """Follow the C++ AWLS single-path rule from a selected start operation."""

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
        if job_successor == schedule.index.end_node or job_successor in seen:
            break
        # C++ 参考实现会直接沿工件后继继续走，而不再额外检查紧路径条件。
        # 这里对齐该单路径规则，避免过早缩小 N7 的候选范围。
        path.append(job_successor)
        seen.add(job_successor)
        continue
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


def all_path_critical_blocks(schedule: AwlsSchedule) -> list[list[int]]:
    """Return C++ ``update_all_critical_block`` style fallback blocks.

    The normal AWLS step follows one randomly selected critical path.  When
    that path does not expose a legal move, the C++ reference enumerates all
    branchable critical paths and extracts same-machine blocks from those
    paths.  A plain machine scan can include similar-looking blocks, but it is
    not exactly the same neighborhood definition.
    """

    all_paths: list[list[int]] = [
        [node]
        for node in schedule.first_machine_operation
        if node != -1 and schedule.is_critical_operation(node) and schedule.forward_path_length[node] == 0
    ]

    path_index = 0
    while path_index < len(all_paths):
        current_path = all_paths[path_index]
        node = current_path[-1]
        while schedule.end_time[node] < schedule.makespan:
            machine_successor = schedule.machine_successor[node]
            job_successor = schedule.job_successor[node]
            is_machine_critical = schedule.is_critical_operation(machine_successor)
            is_job_critical = (
                machine_successor != job_successor
                and job_successor != schedule.index.end_node
                and schedule.is_critical_operation(job_successor)
            )
            if (
                is_machine_critical
                and is_job_critical
                and schedule.end_time[node] == schedule.forward_path_length[job_successor]
            ):
                all_paths.append(list(current_path))
                current_path.append(machine_successor)
                all_paths[-1].append(job_successor)
            elif is_machine_critical:
                current_path.append(machine_successor)
            elif is_job_critical and schedule.end_time[node] == schedule.forward_path_length[job_successor]:
                current_path.append(job_successor)
            else:
                break
            node = current_path[-1]
        path_index += 1

    blocks: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    for path in all_paths:
        for block in blocks_from_path(schedule, path):
            key = tuple(block)
            if key not in seen:
                blocks.append(block)
                seen.add(key)
    return blocks


def critical_blocks(schedule: AwlsSchedule, rng: random.Random, exhaustive: bool = False) -> list[list[int]]:
    if exhaustive:
        blocks = all_path_critical_blocks(schedule)
        return blocks if blocks else machine_scan_critical_blocks(schedule)

    start_candidates = [
        node
        for node in schedule.first_machine_operation
        if node != -1 and schedule.is_critical_operation(node) and schedule.forward_path_length[node] == 0
    ]
    while start_candidates:
        start_index = rng.randrange(len(start_candidates))
        start = start_candidates[start_index]
        start_candidates[start_index] = start_candidates[-1]
        start_candidates.pop()
        blocks = blocks_from_path(schedule, one_critical_path_from_start(schedule, start))
        if blocks:
            return blocks
    return machine_scan_critical_blocks(schedule)


def weight_perturbation(schedule: AwlsSchedule, node: int, gamma: int) -> float:
    weight = schedule.op_weight[node]
    policy = schedule.zi_policy
    if policy == "cpp-exact":
        # C++ AWLS draws rr inside every candidate evaluation even when w=0.
        # Keeping that random-number consumption makes the Python trajectory
        # closer to the reference executable while preserving the same zi value.
        rr = schedule.rng.random() * float(gamma)
        if weight <= 0:
            return 0.0
        rr = max(rr, 1.0e-12)
        return max(0.0, 1.0 - schedule.op_cooldown[node] / rr) * weight
    if policy == "none" or weight <= 0:
        return 0.0
    rr = schedule.rng.uniform(1.0e-9, float(gamma))
    cooling_factor = max(0.0, 1.0 - schedule.op_cooldown[node] / rr)
    if policy == "sqrt":
        return cooling_factor * math.sqrt(weight)

    perturbation = cooling_factor * weight
    if policy in {"formula", "slot"}:
        machine_id = schedule.on_machine[node]
        values = {
            "weight": float(weight),
            "cooldown": float(schedule.op_cooldown[node]),
            "rr": float(rr),
            "gamma": float(gamma),
            "cooling": float(cooling_factor),
            "base": float(perturbation),
            "sqrt_weight": math.sqrt(max(0.0, float(weight))),
            "log_weight": math.log1p(max(0.0, float(weight))),
            "is_critical": 1.0 if schedule.is_critical_operation(node) else 0.0,
            "forward": float(schedule.forward_path_length[node]),
            "backward": float(schedule.backward_path_length[node]),
            "duration": float(schedule.index.duration(node, machine_id)),
            "machine_load": float(schedule.machine_operation_count[machine_id]),
            "position": float(schedule.on_machine_pos[node]),
        }
        if policy == "slot":
            if safe_evolved_zi is None:
                return perturbation
            return safe_evolved_zi(values)
        try:
            return evaluate_zi_formula(schedule.zi_formula, values)
        except (ArithmeticError, OverflowError, ValueError):
            return 0.0
    if policy == "aggressive":
        return 1.5 * perturbation
    if policy == "critical" and schedule.is_critical_operation(node):
        return 1.25 * perturbation
    return perturbation


def cpp_int_score(value: float) -> float:
    """按 C++ `int` 返回值口径截断近似评分。"""

    if value < CPP_INT_MIN or value > CPP_INT_MAX:
        # MSVC/x64 converts an out-of-range floating value to 0x80000000 when
        # using the cvttsd2si instruction.  The reference executable used for
        # paper reproduction was built with MSVC, so this preserves the odd
        # first-iteration behavior caused by initial w=INT_MAX.
        return float(CPP_INT_MIN)
    return float(int(value))


def operation_tail(schedule: AwlsSchedule, node: int) -> int:
    """Return C++-style Q tail contribution for a successor operation."""

    if node <= START_NODE or node >= schedule.index.end_node:
        return 0
    return schedule.backward_path_length[node] + schedule.index.duration(node, schedule.on_machine[node])


def local_sequence_after_same_machine_move(schedule: AwlsSchedule, move: Move) -> tuple[list[int], int | None, int | None]:
    machine_id = schedule.on_machine[move.which]
    sequence = schedule.machine_sequences[machine_id]
    which_pos = schedule.on_machine_pos[move.which]
    where_pos = schedule.on_machine_pos[move.where]

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
    return cpp_int_score(value)


def same_machine_evaluate(schedule: AwlsSchedule, move: Move, gamma: int, eval_mode: str) -> float:
    if eval_mode == "cpp-fast":
        return same_machine_evaluate_cpp_fast(schedule, move, gamma)
    return same_machine_evaluate_stable(schedule, move, gamma)


def change_machine_intersection(schedule: AwlsSchedule, node: int, candidate_machine: int) -> tuple[list[int], list[int], list[int]]:
    sequence, rk_start, lk_end = change_machine_window(schedule, node, candidate_machine)
    rk = sequence[rk_start:] if rk_start < len(sequence) else []
    lk = sequence[: lk_end + 1] if lk_end >= 0 else []
    intersection = sequence[rk_start : lk_end + 1] if rk and lk and rk_start <= lk_end else []
    return rk, lk, intersection


def change_machine_window(schedule: AwlsSchedule, node: int, candidate_machine: int) -> tuple[list[int], int, int]:
    job_predecessor = schedule.job_predecessor[node]
    job_successor = schedule.job_successor[node]
    end_time = schedule.end_time
    backward_path_length = schedule.backward_path_length
    on_machine = schedule.on_machine
    durations = schedule.index.candidates
    remove_r = end_time[job_predecessor]
    remove_q = 0
    if job_successor != schedule.index.end_node:
        remove_q = backward_path_length[job_successor] + durations[job_successor][on_machine[job_successor]]

    sequence = schedule.machine_sequences[candidate_machine]

    # On a fixed machine sequence, end times are increasing and tail lengths are
    # decreasing.  Therefore RK is a suffix, LK is a prefix, and their overlap is
    # a contiguous window.  This is equivalent to the C++ list construction but
    # avoids building and matching two temporary lists in the hottest loop.
    rk_start = len(sequence)
    for pos, other in enumerate(sequence):
        if end_time[other] > remove_r:
            rk_start = pos
            break

    lk_end = -1
    for pos in range(len(sequence) - 1, -1, -1):
        other = sequence[pos]
        if backward_path_length[other] + durations[other][candidate_machine] > remove_q:
            lk_end = pos
            break

    return sequence, rk_start, lk_end


def change_machine_evaluate(schedule: AwlsSchedule, move: Move, intersection: list[int], gamma: int) -> float:
    intersection_first = intersection[0] if intersection else -1
    intersection_last = intersection[-1] if intersection else -1
    return change_machine_evaluate_parts(
        schedule,
        move.method,
        move.which,
        move.where,
        intersection_first,
        intersection_last,
        gamma,
    )


def change_machine_evaluate_parts(
    schedule: AwlsSchedule,
    method: str,
    which: int,
    where: int,
    intersection_first: int,
    intersection_last: int,
    gamma: int,
) -> float:
    # SLOT awls_sdst_move_evaluation START
    on_machine = schedule.on_machine
    end_time = schedule.end_time
    backward_path_length = schedule.backward_path_length
    durations = schedule.index.candidates
    machine_id = on_machine[where]
    job_predecessor = schedule.job_predecessor[which]
    job_successor = schedule.job_successor[which]
    which_time = durations[which][machine_id]
    job_successor_time = 0
    if job_successor != schedule.index.end_node:
        job_successor_time = durations[job_successor][on_machine[job_successor]]
    where_time = durations[where][machine_id]
    zi = weight_perturbation(schedule, which, gamma)

    if intersection_first == -1:
        value = (
            which_time
            + end_time[job_predecessor]
            + backward_path_length[job_successor]
            + job_successor_time
            + zi
        )
    elif method == CHANGE_MACHINE_FRONT and where == intersection_first:
        value = which_time + end_time[job_predecessor] + where_time + backward_path_length[where] + zi
    elif method == CHANGE_MACHINE_BACK and where == intersection_last:
        value = which_time + end_time[where] + backward_path_length[job_successor] + job_successor_time + zi
    else:
        machine_successor = schedule.machine_successor[where]
        if machine_successor == -1:
            value = which_time + end_time[where] + zi
        else:
            value = (
                which_time
                + end_time[where]
                + backward_path_length[machine_successor]
                + durations[machine_successor][machine_id]
                + zi
            )
    if schedule.zi_policy in {"cpp", "cpp-exact"}:
        return cpp_int_score(value)
    return value
    # SLOT awls_sdst_move_evaluation END


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
    return candidate_tabu_sequence_parts(schedule, move.method, move.which, move.where)


def candidate_tabu_sequence_parts(schedule: AwlsSchedule, method: str, which: int, where: int) -> tuple[int, list[int]]:
    machine_id = schedule.on_machine[where]
    if method == FRONT:
        sequence = [which]
        node = where
        while node != which:
            sequence.append(node)
            node = schedule.machine_successor[node]
        return machine_id, sequence
    if method == BACK:
        sequence = []
        node = schedule.machine_successor[which]
        stop = schedule.machine_successor[where]
        while node != stop:
            sequence.append(node)
            node = schedule.machine_successor[node]
        sequence.append(which)
        return machine_id, sequence
    if method == CHANGE_MACHINE_BACK:
        successor = schedule.machine_successor[where]
        sequence = [where, which]
        if successor != -1:
            sequence.append(successor)
        return machine_id, sequence
    predecessor = schedule.machine_predecessor[where]
    sequence = []
    if predecessor != -1:
        sequence.append(predecessor)
    sequence.extend([which, where])
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


def find_move(
    schedule: AwlsSchedule,
    best_makespan: int,
    tabu: SequenceTabuList,
    iteration: int,
    gamma: int,
    exact_select_top_k: int,
    critical_block_exhaustive_pct: int,
) -> Move | None:
    all_moves: list[tuple[str, int, int]] = []
    ranked_moves: list[tuple[float, tuple[str, int, int]]] | None = [] if exact_select_top_k > 0 else None
    best_moves: list[tuple[str, int, int]] = []
    best_value = math.inf
    same_machine_eval_func = (
        same_machine_evaluate_cpp_fast if schedule.same_machine_eval == "cpp-fast" else same_machine_evaluate_stable
    )

    def remember_candidate(method: str, which: int, where: int, value: float) -> None:
        nonlocal best_value
        move_key = (method, which, where)
        all_moves.append(move_key)
        if value >= best_makespan:
            machine_id, sequence = candidate_tabu_sequence_parts(schedule, method, which, where)
            if tabu.is_tabu(machine_id, sequence, iteration):
                return
        if ranked_moves is not None:
            ranked_moves.append((value, move_key))
        if value < best_value - 1.0e-9:
            best_value = value
            best_moves.clear()
            best_moves.append(move_key)
        elif abs(value - best_value) <= 1.0e-9:
            best_moves.append(move_key)

    def consider_same(method: str, which: int, where: int) -> None:
        if schedule.on_machine[which] != schedule.on_machine[where] or which == where:
            return
        if method == BACK:
            if schedule.on_machine_pos[where] <= schedule.on_machine_pos[which]:
                return
            job_successor = schedule.job_successor[which]
            if job_successor == where:
                return
            successor_tail = 0
            if job_successor != schedule.index.end_node:
                successor_tail = schedule.backward_path_length[job_successor] + schedule.index.duration(
                    job_successor,
                    schedule.on_machine[job_successor],
                )
            if (
                schedule.backward_path_length[where] + schedule.index.duration(where, schedule.on_machine[where])
                < successor_tail
            ):
                return
        elif method == FRONT:
            if schedule.on_machine_pos[where] >= schedule.on_machine_pos[which]:
                return
            job_predecessor = schedule.job_predecessor[which]
            if where == job_predecessor or schedule.end_time[where] < schedule.end_time[job_predecessor]:
                return
        else:
            return
        move = Move(method, which, where)
        try:
            value = same_machine_eval_func(schedule, move, gamma)
        except (ValueError, KeyError):
            return
        remember_candidate(method, which, where, value)

    def consider_change(method: str, which: int, where: int, intersection_first: int, intersection_last: int) -> None:
        try:
            value = change_machine_evaluate_parts(schedule, method, which, where, intersection_first, intersection_last, gamma)
        except (ValueError, KeyError):
            return
        remember_candidate(method, which, where, value)

    exhaustive_first = schedule.rng.randrange(100) < max(0, min(100, critical_block_exhaustive_pct))
    exhaustive_modes = (True, False) if exhaustive_first else (False, True)
    for exhaustive in exhaustive_modes:
        blocks = critical_blocks(schedule, schedule.rng, exhaustive=exhaustive)
        for block in blocks:
            machine_id = schedule.on_machine[block[0]]
            sequence = schedule.machine_sequences[machine_id]
            block_start = schedule.on_machine_pos[block[0]]
            block_end = schedule.on_machine_pos[block[-1]]

            for node in block:
                for target in sequence[:block_start]:
                    consider_same(FRONT, node, target)
                for target in sequence[block_end + 1 :]:
                    consider_same(BACK, node, target)

            n = len(block)
            if n == 2:
                consider_same(BACK, block[0], block[1])
            else:
                for j in range(2, n):
                    consider_same(BACK, block[0], block[j])
                for j in range(n - 2, -1, -1):
                    consider_same(FRONT, block[-1], block[j])
                for j in range(1, n - 1):
                    consider_same(FRONT, block[j], block[0])
                for j in range(1, n - 1):
                    consider_same(BACK, block[j], block[-1])

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
                    sequence, rk_start, lk_end = change_machine_window(schedule, node, candidate_machine)
                    has_rk = rk_start < len(sequence)
                    has_lk = lk_end >= 0
                    if has_rk and has_lk and rk_start <= lk_end:
                        intersection_first = sequence[rk_start]
                        intersection_last = sequence[lk_end]
                        consider_change(CHANGE_MACHINE_FRONT, node, intersection_first, intersection_first, intersection_last)
                        for target in sequence[rk_start : lk_end + 1]:
                            consider_change(CHANGE_MACHINE_BACK, node, target, intersection_first, intersection_last)
                    elif has_lk and has_rk:
                        for target in sequence[lk_end:rk_start]:
                            consider_change(CHANGE_MACHINE_BACK, node, target, -1, -1)
                    elif not has_lk and has_rk:
                        consider_change(CHANGE_MACHINE_FRONT, node, sequence[rk_start], -1, -1)
                    elif has_lk and not has_rk:
                        consider_change(CHANGE_MACHINE_BACK, node, sequence[lk_end], -1, -1)

        if all_moves:
            break

    if not all_moves:
        return None
    if exact_select_top_k > 0 and ranked_moves:
        exact_best: tuple[int, float, tuple[str, int, int]] | None = None
        for approx_value, move_key in sorted(ranked_moves, key=lambda item: item[0])[:exact_select_top_k]:
            try:
                trial = schedule.clone()
                move = Move(*move_key)
                trial.apply_move(move)
            except (ValueError, KeyError):
                continue
            key = (trial.makespan, approx_value, move_key)
            if exact_best is None or key[:2] < exact_best[:2]:
                exact_best = key
        if exact_best is not None:
            return Move(*exact_best[2])
    if not best_moves:
        return Move(*schedule.rng.choice(all_moves))
    if best_value > schedule.makespan and schedule.rng.randrange(100) < 3:
        return Move(*schedule.rng.choice(all_moves))
    return Move(*schedule.rng.choice(best_moves))


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
    zi_formula: str,
    time_check_interval: int,
    stats: dict[str, int] | None = None,
) -> AwlsSchedule:
    current = initial
    current.same_machine_eval = same_machine_eval
    current.zi_policy = zi_policy
    current.zi_formula = zi_formula
    best = initial.clone()
    tabu = SequenceTabuList(current.index.instance.machine_count)
    tenure_min, tenure_max = cpp_tabu_tenure_bounds(
        current.index.instance.job_count,
        current.index.instance.machine_count,
    )
    deadline = time.perf_counter() + time_limit_sec if time_limit_sec > 0 else None
    if stats is not None:
        stats["cycles"] = stats.get("cycles", 0) + 1

    check_interval = max(1, time_check_interval)
    for iteration in range(iterations):
        if deadline is not None and iteration % check_interval == 0 and time.perf_counter() >= deadline:
            if stats is not None:
                stats["deadline_breaks"] = stats.get("deadline_breaks", 0) + 1
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
            if stats is not None:
                stats["no_move_breaks"] = stats.get("no_move_breaks", 0) + 1
            break
        if stats is not None:
            stats["selected_moves"] = stats.get("selected_moves", 0) + 1
        previous_makespan = current.makespan
        best_before = best.makespan
        add_move_tabu(tabu, current, move, iteration, tenure_min, tenure_max)
        try:
            current.apply_move(move)
        except (ValueError, KeyError):
            if stats is not None:
                stats["invalid_moves"] = stats.get("invalid_moves", 0) + 1
            continue
        if stats is not None:
            stats["applied_moves"] = stats.get("applied_moves", 0) + 1
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
    # C++ 参考实现中 Schedule 复制的是共享 OperationList，best 图结构会继续携带
    # 当前搜索轨迹末端积累出的 w/t 记忆。这里显式保留这份长期记忆，避免外层
    # 多轮 AWLS 只继承“发现 best 那一刻”的权重状态。
    best.op_weight = list(current.op_weight)
    best.op_cooldown = list(current.op_cooldown)
    if stats is not None:
        best.awls_stats = dict(stats)
    return best


def build_initial_schedule(
    index: OperationIndex,
    rng: random.Random,
    restart: int,
    mode: str,
    initial_state: str = "reset",
) -> AwlsSchedule:
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

    if initial_state == "reset":
        return AwlsSchedule(index, sequences, on_machine, rng)
    if initial_state == "cpp":
        return AwlsSchedule(index, sequences, on_machine, rng, initial_weight=CPP_INT_MAX, initial_cooldown=0)
    raise ValueError(f"unknown initial_state: {initial_state!r}")


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


def cpp_tabu_tenure_bounds(job_count: int, machine_count: int) -> tuple[int, int]:
    """Return the C++ AWLS dynamic tabu-tenure bounds for an instance."""

    tenure_min = max(1, 10 + job_count // max(1, machine_count))
    if job_count <= 2 * machine_count:
        tenure_max = int(tenure_min * 1.4)
    else:
        tenure_max = int(tenure_min * 1.5)
    return tenure_min, max(tenure_min, tenure_max)


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
    zi_formula: str = "",
    initial_state: str = "reset",
    time_check_interval: int = 1,
    cycle_trace: list[dict[str, int | float | str]] | None = None,
) -> AwlsSchedule:
    rng = random.Random(seed)
    best: AwlsSchedule | None = None
    deadline = time.perf_counter() + time_limit_sec if time_limit_sec > 0 else None
    run_stats: dict[str, int] = {}

    for restart in range(max(1, restarts)):
        remaining_time = max(0.0, deadline - time.perf_counter()) if deadline is not None else 0.0
        if deadline is not None and remaining_time <= 0:
            break
        run_stats["restarts"] = run_stats.get("restarts", 0) + 1
        remaining_restarts = max(1, restarts - restart)
        restart_budget = remaining_time / remaining_restarts if deadline is not None else 0.0
        restart_deadline = time.perf_counter() + restart_budget if deadline is not None else None
        initial = build_initial_schedule(index, rng, restart, init_mode, initial_state)
        if best is None or initial.makespan < best.makespan:
            best = initial.clone()
        population = initial
        if cycle_trace is not None:
            cycle_trace.append(
                {
                    "event": "restart_initial",
                    "restart": restart,
                    "cycle": -1,
                    "makespan": initial.makespan,
                    "global_best": best.makespan,
                    "applied_moves": run_stats.get("applied_moves", 0),
                }
            )
        for cycle in range(max(1, cycles_per_restart)):
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
                zi_formula,
                time_check_interval,
                run_stats,
            )
            population = improved.clone()
            if best is None or improved.makespan < best.makespan:
                best = improved.clone()
            if cycle_trace is not None:
                cycle_trace.append(
                    {
                        "event": "cycle_done",
                        "restart": restart,
                        "cycle": cycle,
                        "makespan": improved.makespan,
                        "global_best": best.makespan,
                        "applied_moves": run_stats.get("applied_moves", 0),
                    }
                )

    if best is None:
        raise RuntimeError("AWLS failed to build an initial schedule")
    best.awls_stats = dict(run_stats)
    return best


def format_awls_stats(schedule: AwlsSchedule) -> str:
    """把实际搜索量写入策略标签，便于比较 Python 与 C++ 的单位时间搜索深度。"""

    stats = schedule.awls_stats
    return (
        f"restarts_done={stats.get('restarts', 0)}:"
        f"cycles_done={stats.get('cycles', 0)}:"
        f"moves={stats.get('applied_moves', 0)}:"
        f"selected={stats.get('selected_moves', 0)}"
    )


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
    zi_formula: str = "",
    initial_state: str = "reset",
    time_check_interval: int = 1,
    cycle_trace: list[dict[str, int | float | str]] | None = None,
) -> tuple[list[ScheduleRecord], str]:
    if zi_policy not in ZI_POLICY_CHOICES:
        raise ValueError(f"unknown zi_policy {zi_policy!r}; expected one of {', '.join(ZI_POLICY_CHOICES)}")
    if zi_policy == "formula":
        zi_formula = validate_zi_formula(zi_formula)
    else:
        zi_formula = ""
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
                zi_formula=zi_formula,
                initial_state=initial_state,
                time_check_interval=time_check_interval,
                cycle_trace=cycle_trace,
            )
            lane_summaries.append(
                f"{effective_lane_seed}/{lane.init_mode}/r{lane.restarts}/t{lane_budget:.1f}"
                f"=m{candidate.makespan}/{format_awls_stats(candidate)}"
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
            f"exhaustive_pct={critical_block_exhaustive_pct}:zi={zi_policy}:initial={initial_state}:"
            f"formula={zi_formula or 'none'}:"
            f"time_check={time_check_interval}:makespan={best.makespan}:"
            f"{format_awls_stats(best)}:"
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
        zi_formula=zi_formula,
        initial_state=initial_state,
        time_check_interval=time_check_interval,
        cycle_trace=cycle_trace,
    )
    label = (
        f"awls:init={init_mode}:restarts={restarts}:cycles={cycles_per_restart}:"
        f"iterations={iterations}:seed={seed}:eval={same_machine_eval}:"
        f"exhaustive_pct={critical_block_exhaustive_pct}:zi={zi_policy}:initial={initial_state}:"
        f"formula={zi_formula or 'none'}:"
        f"time_check={time_check_interval}:makespan={best.makespan}:"
        f"{format_awls_stats(best)}"
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
    parser.add_argument(
        "--paper-profile",
        action="store_true",
        help="Use the C++ AWLS paper-reproduction profile: greedy init, cpp-fast evaluation, beta/gamma/theta=500/40/5.",
    )
    parser.add_argument(
        "--strict-paper-profile",
        action="store_true",
        help=(
            "Use a stricter C++ diagnostic profile. This implies --paper-profile, "
            "keeps C++ initial Operation.w/t state, and consumes zi randomness even when w=0."
        ),
    )
    parser.add_argument("--beta", type=int, default=500)
    parser.add_argument("--gamma", type=int, default=40)
    parser.add_argument("--theta", type=int, default=5)
    parser.add_argument("--zi-policy", choices=ZI_POLICY_CHOICES, default="cpp")
    parser.add_argument(
        "--zi-formula",
        default="",
        help=(
            "Safe arithmetic formula used when --zi-policy formula. "
            "Variables include base, weight, cooldown, rr, gamma, is_critical, "
            "forward, backward, duration, machine_load, and position."
        ),
    )
    parser.add_argument(
        "--initial-state",
        choices=("reset", "cpp"),
        default="reset",
        help="Initial AWLS weight/cooldown state. 'cpp' mirrors Operation.w=INT_MAX and t=0 in the reference code.",
    )
    parser.add_argument("--exact-select-top-k", type=int, default=0)
    parser.add_argument(
        "--time-check-interval",
        type=int,
        default=1,
        help="Check wall-clock deadline every N tabu iterations; paper profile uses 1000 to mirror C++ stop checks.",
    )
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
    parser.add_argument(
        "--trace-cycles",
        type=Path,
        default=None,
        help="Optional JSONL path for per-cycle AWLS diagnostics; disabled by default and does not affect search.",
    )
    args = parser.parse_args()

    if args.strict_paper_profile:
        args.paper_profile = True

    if args.paper_profile:
        args.init = "greedy"
        args.restarts = 1
        args.beta = 500
        args.gamma = 40
        args.theta = 5
        args.zi_policy = "cpp"
        args.initial_state = "reset"
        args.exact_select_top_k = 0
        args.time_check_interval = 1000
        args.critical_block_exhaustive_pct = 0
        args.same_machine_eval = "cpp-fast"
        if args.strict_paper_profile:
            args.zi_policy = "cpp-exact"
            args.initial_state = "cpp"

    start = time.perf_counter()
    instance = parse_standard_fjsp(args.input)
    portfolio_lanes = parse_portfolio_lanes(args.portfolio_lanes) if args.portfolio_lanes else None
    cycle_trace: list[dict[str, int | float | str]] | None = [] if args.trace_cycles is not None else None
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
        zi_formula=args.zi_formula,
        initial_state=args.initial_state,
        exact_select_top_k=args.exact_select_top_k,
        same_machine_eval=args.same_machine_eval,
        portfolio_lanes=portfolio_lanes,
        critical_block_exhaustive_pct=args.critical_block_exhaustive_pct,
        time_check_interval=args.time_check_interval,
        cycle_trace=cycle_trace,
    )
    errors, metrics = validate_standard_schedule(instance, schedule)
    if errors:
        for error in errors[:20]:
            print(f"[awls][error] {error}", file=sys.stderr)
        return 2

    runtime_sec = time.perf_counter() - start
    write_solution(args.output, instance, schedule, strategy)
    if args.trace_cycles is not None and cycle_trace is not None:
        args.trace_cycles.parent.mkdir(parents=True, exist_ok=True)
        with args.trace_cycles.open("w", encoding="utf-8") as handle:
            for item in cycle_trace:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
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
