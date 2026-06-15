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


def critical_path(decoded: DecodedState) -> list[OpKey]:
    """Recover one critical path from the decoded disjunctive graph."""

    by_op = {(record.job_id, record.op_id): record for record in decoded.schedule}
    duration = {op: record.duration for op, record in by_op.items()}
    start = {op: record.start for op, record in by_op.items()}
    tail_after = {op: 0 for op in decoded.topological_order}
    for op in reversed(decoded.topological_order):
        tail_after[op] = max((duration[successor] + tail_after[successor] for successor in decoded.successors[op]), default=0)

    candidates = [
        op
        for op in decoded.topological_order
        if start[op] + duration[op] == decoded.makespan and start[op] + duration[op] + tail_after[op] == decoded.makespan
    ]
    if not candidates:
        return []

    path = [max(candidates, key=lambda op: (start[op], op[0], op[1]))]
    while True:
        op = path[-1]
        predecessors = [
            predecessor
            for predecessor in decoded.predecessors[op]
            if start[predecessor] + duration[predecessor] == start[op]
            and start[predecessor] + duration[predecessor] + tail_after[predecessor] == decoded.makespan
        ]
        if not predecessors:
            break
        path.append(max(predecessors, key=lambda item: (start[item], item[0], item[1])))
    path.reverse()
    return path


def critical_machine_blocks(decoded: DecodedState, state: SearchState) -> list[tuple[int, list[int]]]:
    """Find consecutive same-machine blocks on one critical path.

    Mature job-shop tabu searches usually work on critical blocks instead of
    moving only isolated operations.  This keeps the neighborhood focused on
    arcs that can actually reduce makespan while still preserving feasibility
    through the decoder and evaluator.
    """

    path = critical_path(decoded)
    if len(path) < 2:
        return []

    blocks: list[tuple[int, list[int]]] = []
    index_by_machine = {
        machine_id: {op: index for index, op in enumerate(sequence)}
        for machine_id, sequence in enumerate(state.machine_sequences)
    }
    current_machine = state.assignment[path[0]]
    current_positions = [index_by_machine[current_machine][path[0]]]
    last_position = current_positions[-1]

    for op in path[1:]:
        machine_id = state.assignment[op]
        position = index_by_machine[machine_id][op]
        if machine_id == current_machine and position == last_position + 1:
            current_positions.append(position)
        else:
            if len(current_positions) >= 2:
                blocks.append((current_machine, current_positions))
            current_machine = machine_id
            current_positions = [position]
        last_position = position

    if len(current_positions) >= 2:
        blocks.append((current_machine, current_positions))
    return blocks


def clone_sequences(state: SearchState) -> list[list[OpKey]]:
    return [list(sequence) for sequence in state.machine_sequences]


def swap_adjacent_same_machine(state: SearchState, machine_id: int, left_index: int) -> SearchState | None:
    sequences = clone_sequences(state)
    sequence = sequences[machine_id]
    right_index = left_index + 1
    if left_index < 0 or right_index >= len(sequence):
        return None
    sequence[left_index], sequence[right_index] = sequence[right_index], sequence[left_index]
    return SearchState(assignment=dict(state.assignment), machine_sequences=tuple(tuple(seq) for seq in sequences))


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


def generate_structured_neighbors(
    instance: StandardFjspInstance,
    state: SearchState,
    decoded: DecodedState,
    rng: random.Random,
    neighbor_limit: int,
) -> list[tuple[Move, SearchState]]:
    specs = operation_specs(instance)
    path = critical_path(decoded)
    path_set = set(path)
    critical_set = critical_operations(decoded)
    critical_tail = [op for op in critical_set if op not in path_set]
    rng.shuffle(critical_tail)
    critical = list(path) + critical_tail
    record_by_op = {(record.job_id, record.op_id): record for record in decoded.schedule}
    moves: list[tuple[Move, SearchState]] = []
    seen_moves: set[tuple[object, ...]] = set()

    def remember(move: Move, next_state: SearchState | None) -> None:
        if next_state is None:
            return
        key = (move.kind, move.op, move.from_machine, move.to_machine, move.from_index, move.to_index)
        if key in seen_moves:
            return
        seen_moves.add(key)
        moves.append((move, next_state))

    for machine_id, positions in critical_machine_blocks(decoded, state):
        sequence = state.machine_sequences[machine_id]
        first_index = positions[0]
        last_index = positions[-1]
        first_op = sequence[first_index]
        last_op = sequence[last_index]
        remember(
            Move("critical_block_first_after_last", first_op, machine_id, machine_id, first_index, last_index + 1),
            relocate_same_machine(state, machine_id, first_index, last_index + 1),
        )
        remember(
            Move("critical_block_last_before_first", last_op, machine_id, machine_id, last_index, first_index),
            relocate_same_machine(state, machine_id, last_index, first_index),
        )
        for left_index in positions[:-1]:
            remember(
                Move("critical_block_adjacent_swap", sequence[left_index], machine_id, machine_id, left_index, left_index + 1),
                swap_adjacent_same_machine(state, machine_id, left_index),
            )

    for op in critical:
        from_machine = state.assignment[op]
        sequence = state.machine_sequences[from_machine]
        try:
            old_index = sequence.index(op)
        except ValueError:
            continue

        local_positions = {
            0,
            len(sequence),
            old_index - 4,
            old_index - 3,
            old_index - 2,
            old_index - 1,
            old_index + 1,
            old_index + 2,
            old_index + 3,
            old_index + 4,
            old_index + 5,
        }
        random_positions = list(range(len(sequence) + 1))
        rng.shuffle(random_positions)
        positions = [
            position
            for position in list(local_positions) + random_positions[: max(8, neighbor_limit // 10)]
            if 0 <= position <= len(sequence)
        ]
        for insert_pos in positions:
            next_state = relocate_same_machine(state, from_machine, old_index, insert_pos)
            remember(Move("same_machine_relocate", op, from_machine, from_machine, old_index, insert_pos), next_state)

        record = record_by_op[op]
        candidate_machines = sorted(specs[op], key=lambda machine_id: (specs[op][machine_id], machine_id))
        for to_machine in candidate_machines:
            if to_machine == from_machine:
                continue
            target_sequence = state.machine_sequences[to_machine]
            pivot = len(target_sequence)
            for index, target_op in enumerate(target_sequence):
                if record_by_op[target_op].start >= record.start:
                    pivot = index
                    break
            guided_positions = {
                0,
                len(target_sequence),
                pivot - 2,
                pivot - 1,
                pivot,
                pivot + 1,
                pivot + 2,
            }
            random_positions = list(range(len(target_sequence) + 1))
            rng.shuffle(random_positions)
            positions = [
                position
                for position in list(guided_positions) + random_positions[: max(6, neighbor_limit // 18)]
                if 0 <= position <= len(target_sequence)
            ]
            for insert_pos in positions:
                next_state = change_machine_insert(state, op, from_machine, to_machine, insert_pos)
                remember(Move("machine_change_insert", op, from_machine, to_machine, old_index, insert_pos), next_state)

        if len(moves) >= neighbor_limit * 6:
            break

    if len(moves) < neighbor_limit:
        by_machine_end: list[tuple[int, int]] = []
        for machine_id, sequence in enumerate(state.machine_sequences):
            if not sequence:
                continue
            machine_end = max(record_by_op[op].end for op in sequence)
            by_machine_end.append((machine_end, machine_id))
        for _, machine_id in sorted(by_machine_end, reverse=True)[: max(2, instance.machine_count // 4)]:
            sequence = state.machine_sequences[machine_id]
            tail_indices = list(range(max(0, len(sequence) - 8), len(sequence)))
            rng.shuffle(tail_indices)
            for old_index in tail_indices:
                op = sequence[old_index]
                for insert_pos in (old_index - 3, old_index - 2, old_index - 1, old_index + 2, old_index + 3, old_index + 4):
                    if 0 <= insert_pos <= len(sequence):
                        remember(
                            Move("late_machine_tail_relocate", op, machine_id, machine_id, old_index, insert_pos),
                            relocate_same_machine(state, machine_id, old_index, insert_pos),
                        )
                if len(moves) >= neighbor_limit:
                    break
            if len(moves) >= neighbor_limit:
                break

    if len(moves) <= neighbor_limit:
        return moves
    priority_count = min(len(moves), max(1, neighbor_limit // 3))
    priority = moves[:priority_count]
    remainder = moves[priority_count:]
    rng.shuffle(remainder)
    return (priority + remainder[: neighbor_limit - priority_count])[:neighbor_limit]


def generate_random_neighbors(
    instance: StandardFjspInstance,
    state: SearchState,
    decoded: DecodedState,
    rng: random.Random,
    neighbor_limit: int,
) -> list[tuple[Move, SearchState]]:
    """Legacy broad critical-operation neighborhood.

    This retains the earlier solver's strength: noisy coverage across all
    critical operations and insertion positions.  The structured neighborhood
    is stronger on some instances, but this broader sampler keeps the search
    from overfitting one recovered critical path.
    """

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


def generate_neighbors(
    instance: StandardFjspInstance,
    state: SearchState,
    decoded: DecodedState,
    rng: random.Random,
    neighbor_limit: int,
    neighborhood_profile: str,
) -> list[tuple[Move, SearchState]]:
    if neighborhood_profile == "random":
        return generate_random_neighbors(instance, state, decoded, rng, neighbor_limit)
    if neighborhood_profile == "critical-block":
        return generate_structured_neighbors(instance, state, decoded, rng, neighbor_limit)

    structured = generate_structured_neighbors(instance, state, decoded, rng, neighbor_limit)
    random_moves = generate_random_neighbors(instance, state, decoded, rng, neighbor_limit)

    structured_quota = min(len(structured), max(1, neighbor_limit // 4))
    combined = structured[:structured_quota]
    seen = {
        (move.kind, move.op, move.from_machine, move.to_machine, move.from_index, move.to_index)
        for move, _ in combined
    }
    for move, next_state in random_moves + structured[structured_quota:]:
        key = (move.kind, move.op, move.from_machine, move.to_machine, move.from_index, move.to_index)
        if key in seen:
            continue
        seen.add(key)
        combined.append((move, next_state))
        if len(combined) >= neighbor_limit:
            break
    return combined


def tabu_search(
    instance: StandardFjspInstance,
    initial_schedule: list[ScheduleRecord],
    seed: int,
    iterations: int,
    neighbor_limit: int,
    time_limit_sec: float,
    neighborhood_profile: str,
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
    base_tenure = max(7, int((instance.operation_count ** 0.5) * 1.5))

    for iteration in range(iterations):
        if time_limit_sec > 0 and time.perf_counter() - start_time >= time_limit_sec:
            break

        best_candidate: tuple[int, float, Move, SearchState, DecodedState] | None = None
        for move, next_state in generate_neighbors(instance, current_state, current_decoded, rng, neighbor_limit, neighborhood_profile):
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
        if neighborhood_profile == "random":
            tabu_until[move.reverse_tabu_key] = iteration + 7 + (iteration % 5)
        else:
            tabu_until[move.reverse_tabu_key] = iteration + base_tenure + rng.randint(0, max(3, base_tenure // 2))
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
    neighborhood_profile: str,
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
            neighborhood_profile=neighborhood_profile,
        )
        key = (decoded.makespan, initial_makespan)
        if best is None or key < best[:2]:
            label = (
                f"local_search:{neighborhood_profile}:{winner.name}:"
                f"restart={restart}:initial={initial_makespan}:best={decoded.makespan}"
            )
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
    parser.add_argument("--neighborhood-profile", choices=["random", "critical-block", "combined"], default="random")
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
        neighborhood_profile=args.neighborhood_profile,
    )
    runtime_sec = time.perf_counter() - start_time
    write_solution(args.output, instance, schedule, strategy=f"{label}:runtime={runtime_sec:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
