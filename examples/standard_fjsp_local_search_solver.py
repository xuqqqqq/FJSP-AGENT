from __future__ import annotations

"""Standard-FJSP constructive-plus-local-search solver.

This solver is intentionally kept as a normal command-line candidate solver so
that the LangGraph harness can evaluate it through the same contract interface
as any future LLM-generated solver.  It builds a dispatch-rule portfolio first,
then applies a small critical-path tabu search over machine sequences and
machine assignments.  The fixed evaluator remains responsible for the final
validity and best-known-gap judgement.
"""

import argparse
import random
import time
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_agent.standard_fjsp import (
    ScheduleRecord,
    StandardFjspInstance,
    parse_standard_fjsp,
    validate_standard_schedule,
    write_solution,
)
from standard_fjsp_portfolio_solver import build_portfolio, choose_best


OpKey = tuple[int, int]


@dataclass(frozen=True)
class SearchState:
    assignment: dict[OpKey, int]
    machine_sequences: tuple[tuple[OpKey, ...], ...]


@dataclass(frozen=True)
class DecodedState:
    schedule: tuple[ScheduleRecord, ...]
    makespan: int
    predecessors: dict[OpKey, tuple[OpKey, ...]]
    successors: dict[OpKey, tuple[OpKey, ...]]
    topological_order: tuple[OpKey, ...]


@dataclass(frozen=True)
class Move:
    kind: str
    op: OpKey
    from_machine: int
    to_machine: int
    from_index: int
    to_index: int

    @property
    def tabu_key(self) -> tuple[object, ...]:
        return (self.kind, self.op, self.from_machine, self.to_machine)

    @property
    def reverse_tabu_key(self) -> tuple[object, ...]:
        return (self.kind, self.op, self.to_machine, self.from_machine)


def all_operations(instance: StandardFjspInstance) -> list[OpKey]:
    return [(job.job_id, op.op_id) for job in instance.jobs for op in job.operations]


def operation_specs(instance: StandardFjspInstance) -> dict[OpKey, dict[int, int]]:
    specs: dict[OpKey, dict[int, int]] = {}
    for job in instance.jobs:
        for op in job.operations:
            specs[(job.job_id, op.op_id)] = {candidate.machine_id: candidate.duration for candidate in op.candidates}
    return specs


def schedule_to_state(instance: StandardFjspInstance, schedule: list[ScheduleRecord]) -> SearchState:
    assignment: dict[OpKey, int] = {}
    by_machine: list[list[ScheduleRecord]] = [[] for _ in range(instance.machine_count)]
    for record in schedule:
        op = (record.job_id, record.op_id)
        assignment[op] = record.machine_id
        by_machine[record.machine_id].append(record)

    sequences: list[tuple[OpKey, ...]] = []
    for records in by_machine:
        records.sort(key=lambda item: (item.start, item.end, item.job_id, item.op_id))
        sequences.append(tuple((item.job_id, item.op_id) for item in records))
    return SearchState(assignment=assignment, machine_sequences=tuple(sequences))


def decode_state(instance: StandardFjspInstance, state: SearchState) -> DecodedState | None:
    specs = operation_specs(instance)
    ops = all_operations(instance)
    op_set = set(ops)
    seen: set[OpKey] = set()

    for machine_id, sequence in enumerate(state.machine_sequences):
        for op in sequence:
            if op in seen or op not in op_set:
                return None
            if state.assignment.get(op) != machine_id:
                return None
            if machine_id not in specs[op]:
                return None
            seen.add(op)
    if seen != op_set:
        return None

    predecessors: dict[OpKey, list[OpKey]] = {op: [] for op in ops}
    successors: dict[OpKey, list[OpKey]] = {op: [] for op in ops}

    def add_arc(left: OpKey, right: OpKey) -> None:
        successors[left].append(right)
        predecessors[right].append(left)

    for job in instance.jobs:
        for left, right in zip(job.operations, job.operations[1:]):
            add_arc((job.job_id, left.op_id), (job.job_id, right.op_id))
    for sequence in state.machine_sequences:
        for left, right in zip(sequence, sequence[1:]):
            add_arc(left, right)

    indegree = {op: len(predecessors[op]) for op in ops}
    earliest_start = {op: 0 for op in ops}
    ready = sorted([op for op in ops if indegree[op] == 0])
    topo: list[OpKey] = []
    records: dict[OpKey, ScheduleRecord] = {}

    while ready:
        ready.sort(key=lambda op: (earliest_start[op], op[0], op[1]))
        op = ready.pop(0)
        topo.append(op)
        machine = state.assignment[op]
        duration = specs[op][machine]
        start = earliest_start[op]
        end = start + duration
        records[op] = ScheduleRecord(job_id=op[0], op_id=op[1], machine_id=machine, start=start, end=end)
        for successor in successors[op]:
            if earliest_start[successor] < end:
                earliest_start[successor] = end
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)

    if len(topo) != len(ops):
        return None

    schedule = tuple(sorted(records.values(), key=lambda item: (item.start, item.end, item.machine_id, item.job_id, item.op_id)))
    makespan = max((record.end for record in schedule), default=0)
    return DecodedState(
        schedule=schedule,
        makespan=makespan,
        predecessors={key: tuple(value) for key, value in predecessors.items()},
        successors={key: tuple(value) for key, value in successors.items()},
        topological_order=tuple(topo),
    )


def critical_operations(decoded: DecodedState) -> set[OpKey]:
    duration = {(record.job_id, record.op_id): record.duration for record in decoded.schedule}
    start = {(record.job_id, record.op_id): record.start for record in decoded.schedule}
    tail_after = {op: 0 for op in decoded.topological_order}
    for op in reversed(decoded.topological_order):
        tail_after[op] = max((duration[successor] + tail_after[successor] for successor in decoded.successors[op]), default=0)
    return {
        op
        for op in decoded.topological_order
        if start[op] + duration[op] + tail_after[op] == decoded.makespan
    }


def clone_sequences(state: SearchState) -> list[list[OpKey]]:
    return [list(sequence) for sequence in state.machine_sequences]


def relocate_same_machine(state: SearchState, machine_id: int, old_index: int, insert_pos: int) -> SearchState | None:
    sequences = clone_sequences(state)
    sequence = sequences[machine_id]
    if old_index < 0 or old_index >= len(sequence):
        return None
    op = sequence.pop(old_index)
    if insert_pos > old_index:
        insert_pos -= 1
    if insert_pos < 0 or insert_pos > len(sequence):
        return None
    if insert_pos == old_index:
        return None
    sequence.insert(insert_pos, op)
    return SearchState(assignment=dict(state.assignment), machine_sequences=tuple(tuple(seq) for seq in sequences))


def change_machine_insert(
    state: SearchState,
    op: OpKey,
    from_machine: int,
    to_machine: int,
    insert_pos: int,
) -> SearchState | None:
    sequences = clone_sequences(state)
    try:
        old_index = sequences[from_machine].index(op)
    except ValueError:
        return None
    sequences[from_machine].pop(old_index)
    if insert_pos < 0 or insert_pos > len(sequences[to_machine]):
        return None
    sequences[to_machine].insert(insert_pos, op)
    assignment = dict(state.assignment)
    assignment[op] = to_machine
    return SearchState(assignment=assignment, machine_sequences=tuple(tuple(seq) for seq in sequences))


def generate_neighbors(
    instance: StandardFjspInstance,
    state: SearchState,
    decoded: DecodedState,
    rng: random.Random,
    neighbor_limit: int,
) -> list[tuple[Move, SearchState]]:
    specs = operation_specs(instance)
    critical = list(critical_operations(decoded))
    rng.shuffle(critical)
    moves: list[tuple[Move, SearchState]] = []

    for op in critical:
        from_machine = state.assignment[op]
        sequence = state.machine_sequences[from_machine]
        try:
            old_index = sequence.index(op)
        except ValueError:
            continue

        positions = list(range(len(sequence) + 1))
        rng.shuffle(positions)
        for insert_pos in positions:
            next_state = relocate_same_machine(state, from_machine, old_index, insert_pos)
            if next_state is None:
                continue
            moves.append((Move("same_machine_relocate", op, from_machine, from_machine, old_index, insert_pos), next_state))

        candidate_machines = list(specs[op])
        rng.shuffle(candidate_machines)
        for to_machine in candidate_machines:
            if to_machine == from_machine:
                continue
            positions = list(range(len(state.machine_sequences[to_machine]) + 1))
            rng.shuffle(positions)
            for insert_pos in positions:
                next_state = change_machine_insert(state, op, from_machine, to_machine, insert_pos)
                if next_state is None:
                    continue
                moves.append((Move("machine_change_insert", op, from_machine, to_machine, old_index, insert_pos), next_state))

        if len(moves) >= neighbor_limit * 4:
            break

    rng.shuffle(moves)
    return moves[:neighbor_limit]


def tabu_search(
    instance: StandardFjspInstance,
    initial_schedule: list[ScheduleRecord],
    seed: int,
    iterations: int,
    neighbor_limit: int,
    time_limit_sec: float,
) -> DecodedState:
    rng = random.Random(seed)
    start_time = time.perf_counter()
    current_state = schedule_to_state(instance, initial_schedule)
    current_decoded = decode_state(instance, current_state)
    if current_decoded is None:
        raise RuntimeError("initial schedule could not be decoded")
    best_state = current_state
    best_decoded = current_decoded
    tabu_until: dict[tuple[object, ...], int] = {}

    for iteration in range(iterations):
        if time_limit_sec > 0 and time.perf_counter() - start_time >= time_limit_sec:
            break

        best_candidate: tuple[int, float, Move, SearchState, DecodedState] | None = None
        for move, next_state in generate_neighbors(instance, current_state, current_decoded, rng, neighbor_limit):
            decoded = decode_state(instance, next_state)
            if decoded is None:
                continue
            errors, _ = validate_standard_schedule(instance, list(decoded.schedule))
            if errors:
                continue
            is_tabu = tabu_until.get(move.tabu_key, -1) > iteration
            aspiration = decoded.makespan < best_decoded.makespan
            if is_tabu and not aspiration:
                continue
            item = (decoded.makespan, rng.random(), move, next_state, decoded)
            if best_candidate is None or item[:2] < best_candidate[:2]:
                best_candidate = item

        if best_candidate is None:
            break

        _, _, move, current_state, current_decoded = best_candidate
        tabu_until[move.reverse_tabu_key] = iteration + 7 + (iteration % 5)
        if current_decoded.makespan < best_decoded.makespan:
            best_state = current_state
            best_decoded = current_decoded

    decoded_best = decode_state(instance, best_state)
    if decoded_best is None:
        return best_decoded
    return decoded_best


def solve_with_restarts(
    instance: StandardFjspInstance,
    seed: int,
    portfolio_size: int,
    strategy_profile: Path | None,
    restarts: int,
    iterations: int,
    neighbor_limit: int,
    time_limit_sec: float,
) -> tuple[list[ScheduleRecord], str]:
    best: tuple[int, int, DecodedState, str] | None = None
    restart_count = max(1, restarts)
    per_restart_time = time_limit_sec / restart_count if time_limit_sec > 0 else 0.0

    for restart in range(restart_count):
        restart_seed = seed + restart * 1_000_003
        strategies = build_portfolio(restart_seed, portfolio_size, strategy_profile)
        winner, initial_schedule = choose_best(instance, strategies, restart_seed)
        initial_makespan = max(record.end for record in initial_schedule)
        decoded = tabu_search(
            instance,
            initial_schedule,
            seed=restart_seed,
            iterations=iterations,
            neighbor_limit=neighbor_limit,
            time_limit_sec=per_restart_time,
        )
        key = (decoded.makespan, initial_makespan)
        if best is None or key < best[:2]:
            label = f"local_search:{winner.name}:restart={restart}:initial={initial_makespan}:best={decoded.makespan}"
            best = (decoded.makespan, initial_makespan, decoded, label)

    if best is None:
        raise RuntimeError("local search failed to produce a candidate")
    return list(best[2].schedule), best[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Critical-path local-search solver for standard FJSP instances.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--portfolio-size", type=int, default=128)
    parser.add_argument("--strategy-profile", type=Path)
    parser.add_argument("--restarts", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--neighbor-limit", type=int, default=180)
    parser.add_argument("--time-limit-sec", type=float, default=4.0)
    args = parser.parse_args()

    start_time = time.perf_counter()
    instance = parse_standard_fjsp(args.input)
    schedule, label = solve_with_restarts(
        instance,
        seed=args.seed,
        portfolio_size=max(1, args.portfolio_size),
        strategy_profile=args.strategy_profile,
        restarts=max(1, args.restarts),
        iterations=max(0, args.iterations),
        neighbor_limit=max(1, args.neighbor_limit),
        time_limit_sec=max(0.0, args.time_limit_sec),
    )
    runtime_sec = time.perf_counter() - start_time
    write_solution(args.output, instance, schedule, strategy=f"{label}:runtime={runtime_sec:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
