# Standard FJSP Agent-Generated Reference Skeleton

Use this card when a coding agent must create or repair a standalone
`examples/agent_generated_fjsp_solver.py` for standard FJSP from IO documents.

This is a portable code-level reference, not a hidden platform solver.  The
agent may copy and adapt the structure, but it must parse the active input file,
respect the active output schema, and cite the functions it actually submits in
`solver_contract_self_check`.

Do not hardcode instance sizes, machine choices, operation orders, makespan
values, LB/UB values, or previous solution files.

Machine ids in public standard-FJSP text files may be 0-based or 1-based.  Do
not unconditionally subtract 1 inside the candidate-token loop.  First collect
all raw ids, infer the base from `machine_count`, and normalize exactly once
when building `eligible`.

## Complete Baseline Skeleton

```python
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

OpKey = tuple[int, int]


def parse_instance(path: str | Path) -> dict:
    """Parse standard FJSP packed job lines into 0-based machine ids."""
    numbers = [int(token) for token in Path(path).read_text(encoding="utf-8").split()]
    if len(numbers) < 3:
        raise ValueError("instance is too short")
    idx = 0
    job_count = numbers[idx]
    machine_count = numbers[idx + 1]
    max_candidate_count = numbers[idx + 2]
    idx += 3

    raw_jobs: list[list[list[tuple[int, int]]]] = []
    machine_ids: list[int] = []
    for job_id in range(job_count):
        op_count = numbers[idx]
        idx += 1
        ops: list[list[tuple[int, int]]] = []
        for op_id in range(op_count):
            candidate_count = numbers[idx]
            idx += 1
            if candidate_count <= 0:
                raise ValueError(f"job {job_id} op {op_id} has no candidates")
            candidates: list[tuple[int, int]] = []
            for _ in range(candidate_count):
                machine_id = numbers[idx]
                duration = numbers[idx + 1]
                idx += 2
                if duration < 0:
                    raise ValueError("negative processing time")
                candidates.append((machine_id, duration))
                machine_ids.append(machine_id)
            ops.append(candidates)
        raw_jobs.append(ops)

    if not machine_ids:
        raise ValueError("no machine candidates")
    # Infer the machine-id base from the full raw id range before normalizing.
    min_machine = min(machine_ids)
    max_machine = max(machine_ids)
    if 0 <= min_machine and max_machine < machine_count:
        machine_base = 0
    elif 1 <= min_machine and max_machine <= machine_count:
        machine_base = 1
    else:
        raise ValueError("machine ids are outside machine_count")

    op_info: dict[OpKey, dict] = {}
    job_op_counts: dict[int, int] = {}
    for job_id, raw_ops in enumerate(raw_jobs):
        job_op_counts[job_id] = len(raw_ops)
        for op_id, raw_candidates in enumerate(raw_ops):
            eligible = {machine_id - machine_base: duration for machine_id, duration in raw_candidates}
            op_info[(job_id, op_id)] = {"eligible": eligible}

    return {
        "name": Path(path).stem,
        "job_count": job_count,
        "machine_count": machine_count,
        "max_candidate_count": max_candidate_count,
        "job_op_counts": job_op_counts,
        "op_info": op_info,
        "operation_count": len(op_info),
    }


def initial_ready_list_state(instance: dict) -> tuple[dict[OpKey, int], dict[int, list[OpKey]]] | None:
    """Build assignment + machine_sequences with an operation-level ready list."""
    op_info = instance["op_info"]
    machine_count = instance["machine_count"]
    job_op_counts = instance["job_op_counts"]

    assignment: dict[OpKey, int] = {}
    machine_sequences: dict[int, list[OpKey]] = {machine_id: [] for machine_id in range(machine_count)}
    job_next_op = {job_id: 0 for job_id in job_op_counts}
    job_ready = {job_id: 0 for job_id in job_op_counts}
    machine_ready = {machine_id: 0 for machine_id in range(machine_count)}

    while len(assignment) < instance["operation_count"]:
        ready_ops = [
            (job_id, op_id)
            for job_id, op_id in job_next_op.items()
            if op_id < job_op_counts[job_id]
        ]
        if not ready_ops:
            return None
        best_finish = None
        best_choices: list[tuple[OpKey, int, int]] = []
        for op_key in ready_ops:
            job_id, _op_id = op_key
            for machine_id, duration in op_info[op_key]["eligible"].items():
                start = max(job_ready[job_id], machine_ready[machine_id])
                finish = start + duration
                if best_finish is None or finish < best_finish:
                    best_finish = finish
                    best_choices = [(op_key, machine_id, finish)]
                elif finish == best_finish:
                    best_choices.append((op_key, machine_id, finish))
        if not best_choices:
            return None
        op_key, machine_id, finish = min(best_choices, key=lambda item: (item[2], item[1], item[0]))
        assignment[op_key] = machine_id
        machine_sequences[machine_id].append(op_key)
        job_next_op[op_key[0]] += 1
        job_ready[op_key[0]] = finish
        machine_ready[machine_id] = finish

    return assignment, machine_sequences


def decode_state(
    instance: dict,
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
) -> list[dict] | None:
    """Progress decoder: schedule only machine-head ops whose predecessors are done."""
    op_info = instance["op_info"]
    expected_ops = set(op_info)
    if set(assignment) != expected_ops:
        return None
    flattened = [op for seq in machine_sequences.values() for op in seq]
    if len(flattened) != len(expected_ops) or set(flattened) != expected_ops:
        return None
    for op_key, machine_id in assignment.items():
        if machine_id not in op_info[op_key]["eligible"]:
            return None

    machine_pos = {machine_id: 0 for machine_id in range(instance["machine_count"])}
    job_ready = {job_id: 0 for job_id in instance["job_op_counts"]}
    machine_ready = {machine_id: 0 for machine_id in range(instance["machine_count"])}
    scheduled: dict[OpKey, dict] = {}
    schedule: list[dict] = []

    while len(schedule) < instance["operation_count"]:
        progressed = False
        for machine_id in range(instance["machine_count"]):
            sequence = machine_sequences.get(machine_id, [])
            pos = machine_pos[machine_id]
            if pos >= len(sequence):
                continue
            op_key = sequence[pos]
            if assignment.get(op_key) != machine_id:
                return None
            job_id, op_id = op_key
            if op_id > 0 and (job_id, op_id - 1) not in scheduled:
                continue
            duration = op_info[op_key]["eligible"].get(machine_id)
            if duration is None:
                return None
            start = max(job_ready[job_id], machine_ready[machine_id])
            end = start + duration
            record = {
                "job_id": job_id,
                "op_id": op_id,
                "machine_id": machine_id,
                "start": start,
                "end": end,
            }
            scheduled[op_key] = record
            schedule.append(record)
            job_ready[job_id] = end
            machine_ready[machine_id] = end
            machine_pos[machine_id] += 1
            progressed = True
        if not progressed:
            return None

    return schedule if coverage_ok(instance, schedule) else None


def coverage_ok(instance: dict, schedule: list[dict]) -> bool:
    seen = {(item["job_id"], item["op_id"]) for item in schedule}
    return len(schedule) == instance["operation_count"] and seen == set(instance["op_info"])


def validate_schedule(instance: dict, schedule: list[dict]) -> bool:
    if not coverage_ok(instance, schedule):
        return False
    op_info = instance["op_info"]
    by_machine: dict[int, list[tuple[int, int]]] = {}
    job_end: dict[OpKey, int] = {}
    for item in schedule:
        op_key = (item["job_id"], item["op_id"])
        machine_id = item["machine_id"]
        duration = op_info.get(op_key, {}).get("eligible", {}).get(machine_id)
        if duration is None:
            return False
        if item["end"] - item["start"] != duration:
            return False
        if op_key[1] > 0 and job_end.get((op_key[0], op_key[1] - 1), 0) > item["start"]:
            return False
        job_end[op_key] = item["end"]
        by_machine.setdefault(machine_id, []).append((item["start"], item["end"]))
    for intervals in by_machine.values():
        intervals.sort()
        for left, right in zip(intervals, intervals[1:]):
            if left[1] > right[0]:
                return False
    return True


def makespan(schedule: list[dict]) -> int:
    return max(item["end"] for item in schedule)


def clone_state(
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
) -> tuple[dict[OpKey, int], dict[int, list[OpKey]]]:
    return dict(assignment), {machine_id: list(seq) for machine_id, seq in machine_sequences.items()}


def apply_reassignment_move(
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
    op_key: OpKey,
    new_machine: int,
    new_pos: int,
) -> tuple[dict[OpKey, int], dict[int, list[OpKey]]] | None:
    old_machine = assignment.get(op_key)
    if old_machine is None:
        return None
    next_assignment, next_sequences = clone_state(assignment, machine_sequences)
    if op_key not in next_sequences.get(old_machine, []):
        return None
    next_sequences[old_machine].remove(op_key)
    seq = next_sequences.setdefault(new_machine, [])
    new_pos = max(0, min(new_pos, len(seq)))
    seq.insert(new_pos, op_key)
    next_assignment[op_key] = new_machine
    return next_assignment, next_sequences


def improve_by_alternative_machine(
    instance: dict,
    assignment: dict[OpKey, int],
    machine_sequences: dict[int, list[OpKey]],
    seed: int,
    deadline: float,
) -> tuple[dict[OpKey, int], dict[int, list[OpKey]], list[dict]]:
    """Small legal local search. Replace later with stronger cards if needed."""
    rng = random.Random(seed)
    best_assignment, best_sequences = clone_state(assignment, machine_sequences)
    best_schedule = decode_state(instance, best_assignment, best_sequences)
    if best_schedule is None or not validate_schedule(instance, best_schedule):
        raise RuntimeError("initial state does not decode")
    best_value = makespan(best_schedule)

    iteration = 0
    while time.perf_counter() < deadline and iteration < 2000:
        iteration += 1
        improved = False
        ops = list(instance["op_info"].keys())
        rng.shuffle(ops)
        for op_key in ops:
            if time.perf_counter() >= deadline:
                break
            current_machine = best_assignment[op_key]
            eligible = list(instance["op_info"][op_key]["eligible"].keys())
            rng.shuffle(eligible)
            for new_machine in eligible:
                if time.perf_counter() >= deadline:
                    break
                if new_machine == current_machine:
                    continue
                max_pos = len(best_sequences.get(new_machine, []))
                positions = list(range(max_pos + 1))
                rng.shuffle(positions)
                for new_pos in positions[:8]:
                    if time.perf_counter() >= deadline:
                        break
                    trial = apply_reassignment_move(best_assignment, best_sequences, op_key, new_machine, new_pos)
                    if trial is None:
                        continue
                    trial_assignment, trial_sequences = trial
                    trial_schedule = decode_state(instance, trial_assignment, trial_sequences)
                    if trial_schedule is None or not validate_schedule(instance, trial_schedule):
                        continue
                    trial_value = makespan(trial_schedule)
                    if trial_value < best_value:
                        best_assignment = trial_assignment
                        best_sequences = trial_sequences
                        best_schedule = trial_schedule
                        best_value = trial_value
                        improved = True
                        break
                if improved:
                    break
            if improved or time.perf_counter() >= deadline:
                break
        if not improved:
            break

    return best_assignment, best_sequences, best_schedule


def solve(input_path: str, output_path: str, seed: int, time_limit_sec: float) -> None:
    instance = parse_instance(input_path)
    state = initial_ready_list_state(instance)
    if state is None:
        raise RuntimeError("failed to construct initial state")
    assignment, machine_sequences = state
    deadline = time.perf_counter() + max(0.05, time_limit_sec)
    assignment, machine_sequences, schedule = improve_by_alternative_machine(
        instance, assignment, machine_sequences, seed, deadline
    )
    if not validate_schedule(instance, schedule):
        raise RuntimeError("internal validation failed")
    output = {
        "format": "standard_fjsp_schedule_v1",
        "strategy": "agent_generated_ready_list_sequence_decoder",
        "makespan": makespan(schedule),
        "schedule": sorted(schedule, key=lambda item: (item["job_id"], item["op_id"])),
    }
    Path(output_path).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--time-limit-sec", type=float, required=True)
    args = parser.parse_args()
    solve(args.input, args.output, args.seed, args.time_limit_sec)


if __name__ == "__main__":
    main()
```

## Required Evidence Names

When using this skeleton, cite concrete submitted symbols:

- `parse_instance` for `active_io_parser`
- `initial_ready_list_state` for `operation_level_ready_list_constructor`
- `assignment` and `machine_sequences` for `stable_operation_identity` and sequence state
- `decode_state` for progress/topological decoder
- `coverage_ok` for complete schedule coverage
- `validate_schedule` for eligibility, duration, precedence, and non-overlap
- `apply_reassignment_move` and `improve_by_alternative_machine` for guarded local search
