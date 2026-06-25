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
from standard_fjsp_portfolio_solver import build_portfolio, build_schedule


OpKey = tuple[int, int]
NEIGHBORHOOD_PROFILES = ("random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid")
PROFILE_SEED_OFFSETS = {
    "random": 0,
    "critical-block": 3_058_763,
    "combined": 0,
    "hgtsa-lite": 6_117_529,
    "hybrid": 9_176_291,
    "awls-hybrid": 18_352_582,
}


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
        if self.kind.startswith("hgtsa") and self.from_machine == self.to_machine:
            return (self.kind, self.op, self.from_machine, self.from_index, self.to_index)
        return (self.kind, self.op, self.from_machine, self.to_machine)

    @property
    def reverse_tabu_key(self) -> tuple[object, ...]:
        if self.kind.startswith("hgtsa") and self.from_machine == self.to_machine:
            return (self.kind, self.op, self.from_machine, self.to_index, self.from_index)
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


def decoded_timing(decoded: DecodedState) -> tuple[dict[OpKey, ScheduleRecord], dict[OpKey, int], dict[OpKey, int]]:
    record_by_op = {(record.job_id, record.op_id): record for record in decoded.schedule}
    duration = {op: record.duration for op, record in record_by_op.items()}
    tail_after = {op: 0 for op in decoded.topological_order}
    for op in reversed(decoded.topological_order):
        tail_after[op] = max((duration[successor] + tail_after[successor] for successor in decoded.successors[op]), default=0)
    return record_by_op, duration, tail_after


def job_successor_tail(duration: dict[OpKey, int], tail_after: dict[OpKey, int], op: OpKey) -> int:
    successor = (op[0], op[1] + 1)
    if successor not in duration:
        return 0
    return duration[successor] + tail_after[successor]


def critical_machine_blocks_all(decoded: DecodedState, state: SearchState) -> list[tuple[int, list[int]]]:
    """Find every same-machine critical block, not only one recovered path."""

    record_by_op, _, _ = decoded_timing(decoded)
    critical = critical_operations(decoded)
    blocks: list[tuple[int, list[int]]] = []
    for machine_id, sequence in enumerate(state.machine_sequences):
        current: list[int] = []
        for index, op in enumerate(sequence):
            if op in critical:
                if not current:
                    current = [index]
                else:
                    prev_op = sequence[current[-1]]
                    if record_by_op[prev_op].end == record_by_op[op].start:
                        current.append(index)
                    else:
                        if len(current) >= 2:
                            blocks.append((machine_id, current))
                        current = [index]
            else:
                if len(current) >= 2:
                    blocks.append((machine_id, current))
                current = []
        if len(current) >= 2:
            blocks.append((machine_id, current))
    return blocks


def machine_sequence_proxy_score(
    *,
    record_by_op: dict[OpKey, ScheduleRecord],
    tail_after: dict[OpKey, int],
    specs: dict[OpKey, dict[int, int]],
    sequence: list[OpKey],
    machine_id: int,
    assignment: dict[OpKey, int],
    tail_override: dict[OpKey, int] | None = None,
) -> float:
    """Approximate local longest-path impact on one machine sequence.

    The exact schedule is still decoded before acceptance.  This score only
    ranks a larger AWLS/HGTSA candidate set cheaply enough to keep top-k
    evaluation useful.
    """

    machine_ready = 0
    score = 0.0
    tail_override = tail_override or {}
    for op in sequence:
        duration = specs[op][assignment.get(op, machine_id)]
        start = max(machine_ready, job_predecessor_ready(record_by_op, op))
        end = start + duration
        score = max(score, end + tail_override.get(op, tail_after.get(op, 0)))
        machine_ready = end
    return score


def awls_insert_positions(
    *,
    state: SearchState,
    record_by_op: dict[OpKey, ScheduleRecord],
    duration: dict[OpKey, int],
    tail_after: dict[OpKey, int],
    specs: dict[OpKey, dict[int, int]],
    op: OpKey,
    target_machine: int,
) -> list[int]:
    """Generate k-insertion target positions with the AWLS RK/LK idea."""

    target_sequence = list(state.machine_sequences[target_machine])
    if not target_sequence:
        return [0]

    remove_machine_r = job_predecessor_ready(record_by_op, op)
    remove_machine_q = job_successor_tail(duration, tail_after, op)
    rk = [
        target_op
        for target_op in target_sequence
        if record_by_op[target_op].end > remove_machine_r
    ]
    lk = [
        target_op
        for target_op in target_sequence
        if tail_after.get(target_op, 0) + specs[target_op][target_machine] > remove_machine_q
    ]

    position_by_op = {target_op: index for index, target_op in enumerate(target_sequence)}
    positions: set[int] = set()
    intersection = [target_op for target_op in target_sequence if target_op in set(rk) and target_op in set(lk)]
    if intersection:
        positions.add(position_by_op[intersection[0]])
        for target_op in intersection:
            positions.add(position_by_op[target_op] + 1)
    elif lk and rk:
        left = position_by_op[lk[-1]]
        right = position_by_op[rk[0]]
        if left <= right:
            for index in range(left, right + 1):
                positions.add(index + 1)
        else:
            positions.update({right, left + 1})
    elif rk:
        positions.add(position_by_op[rk[0]])
    elif lk:
        positions.add(position_by_op[lk[-1]] + 1)

    start_pivot = len(target_sequence)
    ready_pivot = len(target_sequence)
    job_ready = job_predecessor_ready(record_by_op, op)
    op_start = record_by_op[op].start
    for index, target_op in enumerate(target_sequence):
        target_record = record_by_op[target_op]
        if target_record.start >= op_start and start_pivot == len(target_sequence):
            start_pivot = index
        if target_record.end >= job_ready and ready_pivot == len(target_sequence):
            ready_pivot = index
    for pivot in (start_pivot, ready_pivot):
        positions.update({pivot - 2, pivot - 1, pivot, pivot + 1, pivot + 2})

    # Keep head/tail as safe exploratory fallbacks when the RK/LK cut is empty
    # or overly narrow on small instances.
    positions.update({0, len(target_sequence)})
    return sorted(position for position in positions if 0 <= position <= len(target_sequence))


def job_predecessor_ready(record_by_op: dict[OpKey, ScheduleRecord], op: OpKey) -> int:
    if op[1] <= 0:
        return 0
    predecessor = record_by_op.get((op[0], op[1] - 1))
    return predecessor.end if predecessor else 0


def proxy_insert_score(
    *,
    decoded: DecodedState,
    state: SearchState,
    record_by_op: dict[OpKey, ScheduleRecord],
    tail_after: dict[OpKey, int],
    specs: dict[OpKey, dict[int, int]],
    op: OpKey,
    target_machine: int,
    insert_pos: int,
    source_machine: int,
    source_index: int,
) -> float:
    """Cheaply rank candidates before full active decoding.

    The proxy is not used as proof of quality.  It estimates the local head
    created by a move, adds the operation's current tail, and lightly penalizes
    machine-load imbalance.  The tabu loop still accepts moves only after full
    decoding and evaluator-compatible schedule validation.
    """

    target_sequence = list(state.machine_sequences[target_machine])
    source_sequence = list(state.machine_sequences[source_machine])
    legacy_target_sequence = list(target_sequence)
    legacy_insert_pos = insert_pos
    if source_machine == target_machine and 0 <= source_index < len(legacy_target_sequence):
        legacy_target_sequence.pop(source_index)
        if legacy_insert_pos > source_index:
            legacy_insert_pos -= 1
    legacy_insert_pos = max(0, min(legacy_insert_pos, len(legacy_target_sequence)))
    if source_machine == target_machine and 0 <= source_index < len(target_sequence):
        target_sequence.pop(source_index)
        if insert_pos > source_index:
            insert_pos -= 1
        insert_pos = max(0, min(insert_pos, len(target_sequence)))
        target_sequence.insert(insert_pos, op)
        proxy = machine_sequence_proxy_score(
            record_by_op=record_by_op,
            tail_after=tail_after,
            specs=specs,
            sequence=target_sequence,
            machine_id=target_machine,
            assignment=state.assignment,
        )
    else:
        if 0 <= source_index < len(source_sequence):
            source_sequence.pop(source_index)
        insert_pos = max(0, min(insert_pos, len(target_sequence)))
        target_sequence.insert(insert_pos, op)
        assignment = dict(state.assignment)
        assignment[op] = target_machine
        tail_override = {op: job_successor_tail({key: value.duration for key, value in record_by_op.items()}, tail_after, op)}
        proxy = max(
            machine_sequence_proxy_score(
                record_by_op=record_by_op,
                tail_after=tail_after,
                specs=specs,
                sequence=source_sequence,
                machine_id=source_machine,
                assignment=assignment,
                tail_override=tail_override,
            ),
            machine_sequence_proxy_score(
                record_by_op=record_by_op,
                tail_after=tail_after,
                specs=specs,
                sequence=target_sequence,
                machine_id=target_machine,
                assignment=assignment,
                tail_override=tail_override,
            ),
        )
    predecessor = legacy_target_sequence[legacy_insert_pos - 1] if legacy_insert_pos > 0 else None
    successor = legacy_target_sequence[legacy_insert_pos] if legacy_insert_pos < len(legacy_target_sequence) else None
    machine_ready = record_by_op[predecessor].end if predecessor is not None else 0
    job_ready = job_predecessor_ready(record_by_op, op)
    duration = specs[op][target_machine]
    estimated_start = max(machine_ready, job_ready)
    estimated_completion = estimated_start + duration + tail_after.get(op, 0)
    successor_slack = max(0, estimated_start + duration - record_by_op[successor].start) if successor is not None else 0
    machine_load = sum(specs[item][state.assignment[item]] for item in legacy_target_sequence)
    mean_load = sum(record.duration for record in decoded.schedule) / max(1, len(state.machine_sequences))
    load_penalty = abs(machine_load + duration - mean_load) / max(1.0, mean_load)
    locality_penalty = abs(estimated_start - record_by_op[op].start) / max(1, decoded.makespan)
    legacy_proxy = estimated_completion + successor_slack + 0.25 * load_penalty + 0.10 * locality_penalty
    return 0.75 * proxy + 0.25 * legacy_proxy


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


def schedule_signature(instance: StandardFjspInstance, schedule: list[ScheduleRecord]) -> tuple[object, ...]:
    by_machine: list[list[ScheduleRecord]] = [[] for _ in range(instance.machine_count)]
    assignments: list[tuple[int, int, int]] = []
    for record in schedule:
        by_machine[record.machine_id].append(record)
        assignments.append((record.job_id, record.op_id, record.machine_id))
    machine_sequences = []
    for records in by_machine:
        records.sort(key=lambda item: (item.start, item.end, item.job_id, item.op_id))
        machine_sequences.append(tuple((item.job_id, item.op_id) for item in records))
    return (tuple(sorted(assignments)), tuple(machine_sequences))


def choose_initial_schedules(
    instance: StandardFjspInstance,
    strategies: list[object],
    seed: int,
    count: int,
) -> list[tuple[str, int, list[ScheduleRecord]]]:
    """Return diverse high-quality constructive schedules for local search.

    The earlier solver kept only the single best dispatch schedule from a
    portfolio.  That is fast, but it can over-commit to one machine assignment
    before tabu search starts.  This selector keeps a small, duplicate-filtered
    elite set built only from the current instance and strategy portfolio.
    """

    candidates: list[tuple[int, int, str, list[ScheduleRecord]]] = []
    seen: set[tuple[object, ...]] = set()
    for index, strategy in enumerate(strategies):
        schedule = build_schedule(instance, strategy, seed + index * 100003)
        errors, metrics = validate_standard_schedule(instance, schedule)
        if errors:
            continue
        signature = schedule_signature(instance, schedule)
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append((int(metrics["makespan"]), index, getattr(strategy, "name", f"strategy_{index:03d}"), schedule))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(name, makespan, schedule) for makespan, _, name, schedule in candidates[: max(1, count)]]


def generate_structured_neighbors(
    instance: StandardFjspInstance,
    state: SearchState,
    decoded: DecodedState,
    rng: random.Random,
    neighbor_limit: int,
) -> list[tuple[Move, SearchState]]:
    # SLOT neighborhood_actions START
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
    # SLOT neighborhood_actions END


def generate_hgtsa_lite_neighbors(
    instance: StandardFjspInstance,
    state: SearchState,
    decoded: DecodedState,
    rng: random.Random,
    neighbor_limit: int,
) -> list[tuple[Move, SearchState]]:
    """N8/k-insertion-inspired neighborhood with proxy candidate ranking.

    This is a lightweight implementation of the HGTSA ideas captured in the
    knowledge base: N8 expands same-machine critical-block moves outside the
    block, while k-insertion changes the machine of critical operations.  The
    generator intentionally over-produces candidates, ranks them with a cheap
    proxy, and leaves exact feasibility/objective judgement to active decoding.
    """

    specs = operation_specs(instance)
    path = critical_path(decoded)
    path_set = set(path)
    critical_set = critical_operations(decoded)
    critical_tail = [op for op in critical_set if op not in path_set]
    rng.shuffle(critical_tail)
    critical = list(path) + critical_tail
    record_by_op, duration, tail_after = decoded_timing(decoded)
    machine_load = [
        sum(specs[op][state.assignment[op]] for op in sequence)
        for sequence in state.machine_sequences
    ]
    scored: list[tuple[float, float, Move, SearchState]] = []
    seen_moves: set[tuple[object, ...]] = set()

    def remember(move: Move, next_state: SearchState | None, score: float) -> None:
        if next_state is None:
            return
        key = (move.kind, move.op, move.from_machine, move.to_machine, move.from_index, move.to_index)
        if key in seen_moves:
            return
        seen_moves.add(key)
        scored.append((score, rng.random(), move, next_state))

    blocks = critical_machine_blocks_all(decoded, state) or critical_machine_blocks(decoded, state)
    last_block_index = len(blocks) - 1
    for block_index, (machine_id, positions) in enumerate(blocks):
        sequence = state.machine_sequences[machine_id]
        first_index = positions[0]
        last_index = positions[-1]
        block_size = len(positions)

        for old_index in positions:
            op = sequence[old_index]

            # N8 extension: move critical-block operations before any earlier
            # same-machine operation or after any later same-machine operation.
            for target_index in range(0, first_index):
                score = proxy_insert_score(
                    decoded=decoded,
                    state=state,
                    record_by_op=record_by_op,
                    tail_after=tail_after,
                    specs=specs,
                    op=op,
                    target_machine=machine_id,
                    insert_pos=target_index,
                    source_machine=machine_id,
                    source_index=old_index,
                )
                remember(
                    Move("hgtsa_n8_front", op, machine_id, machine_id, old_index, target_index),
                    relocate_same_machine(state, machine_id, old_index, target_index),
                    score,
                )
            for target_index in range(last_index + 1, len(sequence)):
                insert_pos = target_index + 1
                score = proxy_insert_score(
                    decoded=decoded,
                    state=state,
                    record_by_op=record_by_op,
                    tail_after=tail_after,
                    specs=specs,
                    op=op,
                    target_machine=machine_id,
                    insert_pos=insert_pos,
                    source_machine=machine_id,
                    source_index=old_index,
                )
                remember(
                    Move("hgtsa_n8_back", op, machine_id, machine_id, old_index, insert_pos),
                    relocate_same_machine(state, machine_id, old_index, insert_pos),
                    score,
                )

        # N7/N8 internal moves with the standard first/last-block pruning
        # rules.  These are separate from the outside-block moves above.
        internal_moves: list[tuple[int, int, str]] = []
        if block_size == 2:
            internal_moves.append((positions[0], positions[1] + 1, "hgtsa_n8_pair_back"))
        else:
            for j in range(2, block_size):
                internal_moves.append((positions[0], positions[j] + 1, "hgtsa_n8_first_back"))
            for j in range(block_size - 2, -1, -1):
                internal_moves.append((positions[-1], positions[j], "hgtsa_n8_last_front"))
            for j in range(1, block_size - 1):
                internal_moves.append((positions[j], positions[0], "hgtsa_n8_mid_front"))
                internal_moves.append((positions[j], positions[-1] + 1, "hgtsa_n8_mid_back"))

        for old_index, insert_pos, kind in internal_moves:
            if old_index == insert_pos:
                continue
            if block_index == 0 and old_index == first_index and insert_pos > old_index:
                continue
            if block_index == 0 and old_index > first_index and insert_pos <= first_index:
                continue
            if block_index == last_block_index and old_index == last_index and insert_pos < old_index:
                continue
            if block_index == last_block_index and old_index < last_index and insert_pos > last_index:
                continue
            op = sequence[old_index]
            score = proxy_insert_score(
                decoded=decoded,
                state=state,
                record_by_op=record_by_op,
                tail_after=tail_after,
                specs=specs,
                op=op,
                target_machine=machine_id,
                insert_pos=insert_pos,
                source_machine=machine_id,
                source_index=old_index,
            )
            remember(
                Move(kind, op, machine_id, machine_id, old_index, insert_pos),
                relocate_same_machine(state, machine_id, old_index, insert_pos),
                score,
            )

        if len(scored) >= neighbor_limit * 10:
            break

    critical_limit = max(12, min(len(critical), max(len(path), neighbor_limit // 2)))
    for op in critical[:critical_limit]:
        from_machine = state.assignment[op]
        source_sequence = state.machine_sequences[from_machine]
        try:
            old_index = source_sequence.index(op)
        except ValueError:
            continue
        candidate_machines = sorted(
            (machine_id for machine_id in specs[op] if machine_id != from_machine),
            key=lambda machine_id: (specs[op][machine_id], machine_load[machine_id], machine_id),
        )
        for to_machine in candidate_machines:
            for insert_pos in awls_insert_positions(
                state=state,
                record_by_op=record_by_op,
                duration=duration,
                tail_after=tail_after,
                specs=specs,
                op=op,
                target_machine=to_machine,
            ):
                score = proxy_insert_score(
                    decoded=decoded,
                    state=state,
                    record_by_op=record_by_op,
                    tail_after=tail_after,
                    specs=specs,
                    op=op,
                    target_machine=to_machine,
                    insert_pos=insert_pos,
                    source_machine=from_machine,
                    source_index=old_index,
                )
                remember(
                    Move("hgtsa_k_insertion", op, from_machine, to_machine, old_index, insert_pos),
                    change_machine_insert(state, op, from_machine, to_machine, insert_pos),
                    score,
                )
        if len(scored) >= neighbor_limit * 8:
            break

    if len(scored) < neighbor_limit:
        fallback_moves = generate_structured_neighbors(instance, state, decoded, rng, neighbor_limit)
        fallback_score = decoded.makespan + 100.0
        for move, next_state in fallback_moves:
            remember(
                Move(
                    f"hgtsa_fallback_{move.kind}",
                    move.op,
                    move.from_machine,
                    move.to_machine,
                    move.from_index,
                    move.to_index,
                ),
                next_state,
                fallback_score + rng.random(),
            )
            if len(scored) >= neighbor_limit:
                break

    if not scored:
        return []
    scored.sort(key=lambda item: (item[0], item[1]))
    # The proxy is intentionally only a coarse filter.  Keep a substantial
    # random tail so a wrong local estimate does not make the search tunnel on
    # one critical-path interpretation.
    priority_count = min(len(scored), max(1, int(neighbor_limit * 0.55)))
    selected = scored[:priority_count]
    remainder = scored[priority_count:]
    rng.shuffle(remainder)
    selected.extend(remainder[: max(0, neighbor_limit - len(selected))])
    return [(move, next_state) for _, _, move, next_state in selected[:neighbor_limit]]


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
    if neighborhood_profile == "hgtsa-lite":
        return generate_hgtsa_lite_neighbors(instance, state, decoded, rng, neighbor_limit)

    structured = generate_structured_neighbors(instance, state, decoded, rng, neighbor_limit)
    random_moves = generate_random_neighbors(instance, state, decoded, rng, neighbor_limit)
    hgtsa_moves = generate_hgtsa_lite_neighbors(instance, state, decoded, rng, neighbor_limit) if neighborhood_profile in {"hybrid", "awls-hybrid"} else []

    structured_quota = min(len(structured), max(1, neighbor_limit // 4))
    combined = structured[:structured_quota]
    seen = {
        (move.kind, move.op, move.from_machine, move.to_machine, move.from_index, move.to_index)
        for move, _ in combined
    }

    if neighborhood_profile == "hybrid":
        random_quota = min(len(random_moves), max(1, neighbor_limit // 2))
        hgtsa_quota = min(len(hgtsa_moves), max(1, neighbor_limit // 5))
        ordered_candidates = (
            random_moves[:random_quota]
            + hgtsa_moves[:hgtsa_quota]
            + structured[structured_quota:]
            + random_moves[random_quota:]
            + hgtsa_moves[hgtsa_quota:]
        )
    elif neighborhood_profile == "awls-hybrid":
        hgtsa_quota = min(len(hgtsa_moves), max(1, neighbor_limit // 2))
        structured_extra_quota = min(len(structured) - structured_quota, max(1, neighbor_limit // 5))
        random_quota = min(len(random_moves), max(1, neighbor_limit // 4))
        ordered_candidates = (
            hgtsa_moves[:hgtsa_quota]
            + structured[structured_quota : structured_quota + structured_extra_quota]
            + random_moves[:random_quota]
            + hgtsa_moves[hgtsa_quota:]
            + structured[structured_quota + structured_extra_quota :]
            + random_moves[random_quota:]
        )
    else:
        ordered_candidates = random_moves + structured[structured_quota:]

    for move, next_state in ordered_candidates:
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
    hgtsa_tenure = max(7, int(15 + instance.job_count / max(1, instance.machine_count)))

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
        elif move.kind.startswith("hgtsa"):
            tabu_until[move.reverse_tabu_key] = iteration + hgtsa_tenure + rng.randint(0, max(3, hgtsa_tenure // 3))
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
    initial_pool_size: int,
    iterations: int,
    neighbor_limit: int,
    time_limit_sec: float,
    neighborhood_profile: str,
) -> tuple[list[ScheduleRecord], str]:
    best: tuple[int, int, DecodedState, str] | None = None
    restart_count = max(1, restarts)
    pool_size = max(1, initial_pool_size)
    total_searches = restart_count * pool_size
    per_search_time = time_limit_sec / total_searches if time_limit_sec > 0 else 0.0

    for restart in range(restart_count):
        restart_seed = seed + restart * 1_000_003
        strategies = build_portfolio(restart_seed, portfolio_size, strategy_profile)
        initial_candidates = choose_initial_schedules(instance, strategies, restart_seed, pool_size)
        for initial_index, (strategy_name, initial_makespan, initial_schedule) in enumerate(initial_candidates):
            decoded = tabu_search(
                instance,
                initial_schedule,
                seed=restart_seed + initial_index * 17_171,
                iterations=iterations,
                neighbor_limit=neighbor_limit,
                time_limit_sec=per_search_time,
                neighborhood_profile=neighborhood_profile,
            )
            key = (decoded.makespan, initial_makespan)
            if best is None or key < best[:2]:
                label = (
                    f"local_search:{neighborhood_profile}:{strategy_name}:"
                    f"restart={restart}:initial_index={initial_index}:"
                    f"initial_pool={pool_size}:initial={initial_makespan}:best={decoded.makespan}"
                )
                best = (decoded.makespan, initial_makespan, decoded, label)

    if best is None:
        raise RuntimeError("local search failed to produce a candidate")
    return list(best[2].schedule), best[3]


def parse_neighborhood_profile_list(value: str | None, fallback: str) -> list[str]:
    raw_items = [fallback] if not value else [item.strip() for item in value.split(",") if item.strip()]
    profiles: list[str] = []
    for item in raw_items:
        if item not in NEIGHBORHOOD_PROFILES:
            raise ValueError(f"unknown neighborhood profile: {item}")
        if item not in profiles:
            profiles.append(item)
    return profiles or [fallback]


def solve_profile_portfolio(
    instance: StandardFjspInstance,
    *,
    seed: int,
    portfolio_size: int,
    strategy_profile: Path | None,
    restarts: int,
    initial_pool_size: int,
    iterations: int,
    neighbor_limit: int,
    time_limit_sec: float,
    neighborhood_profiles: list[str],
) -> tuple[list[ScheduleRecord], str]:
    """Run several neighborhood profiles and keep the best validated schedule.

    Different FJSP instances respond differently to broad random moves,
    critical-block moves, and AWLS/HGTSA-biased moves.  This portfolio keeps the
    solver deterministic for a seed while letting the evaluator-backed makespan
    decide which neighborhood family is most useful for the current instance.
    """

    if len(neighborhood_profiles) == 1:
        return solve_with_restarts(
            instance,
            seed=seed,
            portfolio_size=portfolio_size,
            strategy_profile=strategy_profile,
            restarts=restarts,
            initial_pool_size=initial_pool_size,
            iterations=iterations,
            neighbor_limit=neighbor_limit,
            time_limit_sec=time_limit_sec,
            neighborhood_profile=neighborhood_profiles[0],
        )

    best: tuple[int, int, str, list[ScheduleRecord], str] | None = None
    for profile_index, profile in enumerate(neighborhood_profiles):
        profile_schedule, profile_label = solve_with_restarts(
            instance,
            seed=seed + PROFILE_SEED_OFFSETS[profile],
            portfolio_size=portfolio_size,
            strategy_profile=strategy_profile,
            restarts=restarts,
            initial_pool_size=initial_pool_size,
            iterations=iterations,
            neighbor_limit=neighbor_limit,
            time_limit_sec=time_limit_sec,
            neighborhood_profile=profile,
        )
        errors, metrics = validate_standard_schedule(instance, profile_schedule)
        if errors:
            continue
        key = (int(metrics["makespan"]), profile_index, profile, profile_schedule, profile_label)
        if best is None or key[:3] < best[:3]:
            best = key

    if best is None:
        raise RuntimeError("profile portfolio failed to produce a valid candidate")
    profile_names = ",".join(neighborhood_profiles)
    label = f"profile_portfolio:{profile_names}:winner={best[2]}:{best[4]}"
    return best[3], label


def main() -> int:
    parser = argparse.ArgumentParser(description="Critical-path local-search solver for standard FJSP instances.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--portfolio-size", type=int, default=128)
    parser.add_argument("--strategy-profile", type=Path)
    parser.add_argument("--restarts", type=int, default=2)
    parser.add_argument("--initial-pool-size", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--neighbor-limit", type=int, default=180)
    parser.add_argument("--time-limit-sec", type=float, default=4.0)
    parser.add_argument(
        "--neighborhood-profile",
        choices=list(NEIGHBORHOOD_PROFILES),
        default="random",
    )
    parser.add_argument(
        "--neighborhood-profiles",
        help="Comma-separated neighborhood-profile portfolio. Each profile uses the normal per-profile search budget.",
    )
    args = parser.parse_args()

    start_time = time.perf_counter()
    instance = parse_standard_fjsp(args.input)
    neighborhood_profiles = parse_neighborhood_profile_list(args.neighborhood_profiles, args.neighborhood_profile)
    schedule, label = solve_profile_portfolio(
        instance,
        seed=args.seed,
        portfolio_size=max(1, args.portfolio_size),
        strategy_profile=args.strategy_profile,
        restarts=max(1, args.restarts),
        initial_pool_size=max(1, args.initial_pool_size),
        iterations=max(0, args.iterations),
        neighbor_limit=max(1, args.neighbor_limit),
        time_limit_sec=max(0.0, args.time_limit_sec),
        neighborhood_profiles=neighborhood_profiles,
    )
    runtime_sec = time.perf_counter() - start_time
    write_solution(args.output, instance, schedule, strategy=f"{label}:runtime={runtime_sec:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
