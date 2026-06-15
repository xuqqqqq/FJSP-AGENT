from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MachineOption:
    machine_id: int
    duration: int


@dataclass(frozen=True)
class Operation:
    job_id: int
    op_id: int
    candidates: tuple[MachineOption, ...]


@dataclass(frozen=True)
class Job:
    job_id: int
    operations: tuple[Operation, ...]


@dataclass(frozen=True)
class StandardFjspInstance:
    name: str
    job_count: int
    machine_count: int
    max_candidate_count: int
    jobs: tuple[Job, ...]

    @property
    def operation_count(self) -> int:
        return sum(len(job.operations) for job in self.jobs)


@dataclass(frozen=True)
class ScheduleRecord:
    job_id: int
    op_id: int
    machine_id: int
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def parse_standard_fjsp(path: Path) -> StandardFjspInstance:
    """Parse the common qimingme/FJSP-Instance text format.

    The format starts with three integers:

    `job_count machine_count max_candidate_count`

    Each job then gives its operation count.  Each operation gives a candidate
    count followed by `(machine_id, processing_time)` pairs.
    """

    numbers = [int(token) for token in path.read_text(encoding="utf-8").split()]
    if len(numbers) < 3:
        raise ValueError(f"{path} is too short to be a standard FJSP instance")

    idx = 0
    job_count, machine_count, max_candidate_count = numbers[idx : idx + 3]
    idx += 3
    raw_jobs: list[list[list[tuple[int, int]]]] = []
    machine_ids: list[int] = []

    for job_id in range(job_count):
        if idx >= len(numbers):
            raise ValueError(f"{path} ended before job {job_id}")
        op_count = numbers[idx]
        idx += 1
        raw_ops: list[list[tuple[int, int]]] = []
        for op_id in range(op_count):
            if idx >= len(numbers):
                raise ValueError(f"{path} ended before job {job_id} operation {op_id}")
            candidate_count = numbers[idx]
            idx += 1
            if candidate_count <= 0:
                raise ValueError(f"{path} job {job_id} operation {op_id} has no candidates")
            candidates: list[tuple[int, int]] = []
            for _ in range(candidate_count):
                if idx + 1 >= len(numbers):
                    raise ValueError(f"{path} ended inside candidate list")
                machine_id = numbers[idx]
                duration = numbers[idx + 1]
                idx += 2
                if duration <= 0:
                    raise ValueError(f"{path} has non-positive duration {duration}")
                machine_ids.append(machine_id)
                candidates.append((machine_id, duration))
            raw_ops.append(candidates)
        raw_jobs.append(raw_ops)

    if idx != len(numbers):
        raise ValueError(f"{path} has trailing tokens: parsed={idx}, total={len(numbers)}")
    if not machine_ids:
        raise ValueError(f"{path} has no machine candidates")

    # Public FJSP datasets may number machines from 0 or 1.  Normalize to 0-based.
    min_machine = min(machine_ids)
    max_machine = max(machine_ids)
    if 0 <= min_machine and max_machine < machine_count:
        machine_base = 0
    elif 1 <= min_machine and max_machine <= machine_count:
        machine_base = 1
    else:
        raise ValueError(
            f"{path} machine ids out of range: min={min_machine}, max={max_machine}, "
            f"machine_count={machine_count}"
        )

    jobs: list[Job] = []
    for job_id, raw_ops in enumerate(raw_jobs):
        ops: list[Operation] = []
        for op_id, raw_candidates in enumerate(raw_ops):
            candidates = tuple(
                MachineOption(machine_id=machine_id - machine_base, duration=duration)
                for machine_id, duration in raw_candidates
            )
            ops.append(Operation(job_id=job_id, op_id=op_id, candidates=candidates))
        jobs.append(Job(job_id=job_id, operations=tuple(ops)))

    return StandardFjspInstance(
        name=path.stem,
        job_count=job_count,
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
    )


def load_solution(path: Path) -> list[ScheduleRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("schedule")
    if not isinstance(records, list):
        raise ValueError("solution must contain a list field named 'schedule'")
    parsed: list[ScheduleRecord] = []
    for index, item in enumerate(records):
        try:
            parsed.append(
                ScheduleRecord(
                    job_id=int(item["job_id"]),
                    op_id=int(item["op_id"]),
                    machine_id=int(item["machine_id"]),
                    start=int(item["start"]),
                    end=int(item["end"]),
                )
            )
        except Exception as exc:  # noqa: BLE001 - convert malformed records into validation errors.
            raise ValueError(f"schedule record {index} is malformed: {item!r}") from exc
    return parsed


def write_solution(path: Path, instance: StandardFjspInstance, schedule: list[ScheduleRecord], strategy: str) -> None:
    payload: dict[str, Any] = {
        "format": "standard_fjsp_schedule_v1",
        "instance": instance.name,
        "strategy": strategy,
        "makespan": max((record.end for record in schedule), default=0),
        "schedule": [
            {
                "job_id": record.job_id,
                "op_id": record.op_id,
                "machine_id": record.machine_id,
                "start": record.start,
                "end": record.end,
            }
            for record in schedule
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_standard_schedule(
    instance: StandardFjspInstance,
    schedule: list[ScheduleRecord],
) -> tuple[list[str], dict[str, float]]:
    errors: list[str] = []
    seen: dict[tuple[int, int], ScheduleRecord] = {}

    if len(schedule) != instance.operation_count:
        errors.append(f"operation count mismatch: expected={instance.operation_count}, got={len(schedule)}")

    candidate_duration: dict[tuple[int, int, int], int] = {}
    expected_ops: set[tuple[int, int]] = set()
    for job in instance.jobs:
        for op in job.operations:
            expected_ops.add((job.job_id, op.op_id))
            for candidate in op.candidates:
                candidate_duration[(job.job_id, op.op_id, candidate.machine_id)] = candidate.duration

    for record in schedule:
        key = (record.job_id, record.op_id)
        if key in seen:
            errors.append(f"duplicate operation: job={record.job_id}, op={record.op_id}")
        seen[key] = record
        if key not in expected_ops:
            errors.append(f"unknown operation: job={record.job_id}, op={record.op_id}")
        if record.start < 0:
            errors.append(f"negative start: job={record.job_id}, op={record.op_id}, start={record.start}")
        if record.end <= record.start:
            errors.append(f"non-positive interval: job={record.job_id}, op={record.op_id}")
        duration = candidate_duration.get((record.job_id, record.op_id, record.machine_id))
        if duration is None:
            errors.append(
                f"machine is not a candidate: job={record.job_id}, op={record.op_id}, machine={record.machine_id}"
            )
        elif record.duration != duration:
            errors.append(
                f"duration mismatch: job={record.job_id}, op={record.op_id}, "
                f"machine={record.machine_id}, expected={duration}, got={record.duration}"
            )

    missing = sorted(expected_ops - set(seen))
    for job_id, op_id in missing:
        errors.append(f"missing operation: job={job_id}, op={op_id}")

    for job in instance.jobs:
        for op_idx in range(len(job.operations) - 1):
            current = seen.get((job.job_id, op_idx))
            nxt = seen.get((job.job_id, op_idx + 1))
            if current and nxt and nxt.start < current.end:
                errors.append(
                    f"precedence violation: job={job.job_id}, op={op_idx} ends at {current.end}, "
                    f"op={op_idx + 1} starts at {nxt.start}"
                )

    by_machine: dict[int, list[ScheduleRecord]] = {}
    for record in schedule:
        by_machine.setdefault(record.machine_id, []).append(record)
    for machine_id, records in by_machine.items():
        sorted_records = sorted(records, key=lambda item: (item.start, item.end, item.job_id, item.op_id))
        for left, right in zip(sorted_records, sorted_records[1:]):
            if right.start < left.end:
                errors.append(
                    f"machine overlap: machine={machine_id}, "
                    f"left=({left.job_id},{left.op_id},{left.start},{left.end}), "
                    f"right=({right.job_id},{right.op_id},{right.start},{right.end})"
                )

    makespan = max((record.end for record in schedule), default=0)
    metrics = {
        "makespan": float(makespan),
        "scheduled_operations": float(len(schedule)),
        "operation_count": float(instance.operation_count),
    }
    return errors, metrics

