"""Fixed IO and legality semantics for distributed FJSP with transfers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SAME_FACTORY_TRANSFER_TIME = 30
CROSS_FACTORY_TRANSFER_TIME = 60
TRANSFER_UNIT_ENERGY = 6


@dataclass(frozen=True)
class DistributedMachineOption:
    factory_id: int
    machine_id: int
    duration: int
    unit_energy: int


@dataclass(frozen=True)
class DistributedOperation:
    job_id: int
    op_id: int
    candidates: tuple[DistributedMachineOption, ...]


@dataclass(frozen=True)
class DistributedJob:
    job_id: int
    operations: tuple[DistributedOperation, ...]


@dataclass(frozen=True)
class DistributedFjspInstance:
    name: str
    source_id: str
    job_count: int
    factory_count: int
    machines_per_factory: int
    min_machines_per_operation_per_factory: int
    max_machines_per_operation_per_factory: int
    jobs: tuple[DistributedJob, ...]
    same_factory_transfer_time: int = SAME_FACTORY_TRANSFER_TIME
    cross_factory_transfer_time: int = CROSS_FACTORY_TRANSFER_TIME
    transfer_unit_energy: int = TRANSFER_UNIT_ENERGY

    @property
    def operation_count(self) -> int:
        return sum(len(job.operations) for job in self.jobs)

    @property
    def max_candidate_count(self) -> int:
        return max((len(op.candidates) for job in self.jobs for op in job.operations), default=0)

    @property
    def machine_count(self) -> int:
        return self.factory_count * self.machines_per_factory


@dataclass(frozen=True)
class DistributedScheduleRecord:
    job_id: int
    op_id: int
    factory_id: int
    machine_id: int
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def looks_like_distributed_fjsp(path: Path) -> bool:
    try:
        first = path.read_text(encoding="utf-8-sig").splitlines()[0].strip().casefold()
    except (OSError, UnicodeError, IndexError):
        return False
    return first.startswith("the source of initial data:")


def parse_distributed_fjsp(path: Path) -> DistributedFjspInstance:
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(lines) < 6:
        raise ValueError(f"{path} is too short to be a distributed FJSP instance")
    source_id = _header_value(lines[0])
    job_count = _header_int(lines[1], path, "job count")
    factory_count = _header_int(lines[2], path, "factory count")
    machines_per_factory = _header_int(lines[3], path, "machines per factory")
    minimum, maximum = _header_range(lines[4], path, "available machine range")
    if min(job_count, factory_count, machines_per_factory, minimum) <= 0 or maximum < minimum:
        raise ValueError(f"{path} has invalid distributed FJSP header values")
    rows = lines[5:]
    if len(rows) != job_count:
        raise ValueError(f"{path} declares {job_count} jobs but contains {len(rows)} job rows")
    jobs = tuple(
        _parse_job(
            [int(token) for token in row.split()],
            job_id,
            factory_count,
            machines_per_factory,
            minimum,
            maximum,
            path,
        )
        for job_id, row in enumerate(rows)
    )
    return DistributedFjspInstance(
        name=path.stem,
        source_id=source_id,
        job_count=job_count,
        factory_count=factory_count,
        machines_per_factory=machines_per_factory,
        min_machines_per_operation_per_factory=minimum,
        max_machines_per_operation_per_factory=maximum,
        jobs=jobs,
    )


def _parse_job(
    tokens: list[int],
    job_id: int,
    factory_count: int,
    machines_per_factory: int,
    minimum: int,
    maximum: int,
    path: Path,
) -> DistributedJob:
    if not tokens or tokens[0] <= 0:
        raise ValueError(f"{path} job {job_id} has invalid operation count")
    parsed = _parse_operations(
        tokens, 1, job_id, 0, tokens[0], factory_count, machines_per_factory, minimum, maximum
    )
    if parsed is None:
        raise ValueError(f"{path} job {job_id} does not match grouped distributed candidate encoding")
    operations, position = parsed
    if position != len(tokens):
        raise ValueError(f"{path} job {job_id} has trailing tokens")
    return DistributedJob(job_id, tuple(operations))


def _parse_operations(
    tokens: list[int],
    position: int,
    job_id: int,
    op_id: int,
    op_count: int,
    factory_count: int,
    machines_per_factory: int,
    minimum: int,
    maximum: int,
) -> tuple[list[DistributedOperation], int] | None:
    if op_id == op_count:
        return ([], position) if position == len(tokens) else None
    if position >= len(tokens):
        return None
    candidate_count = tokens[position]
    for group_sizes in _group_size_options(candidate_count, factory_count, minimum, maximum):
        parsed = _parse_candidate_groups(
            tokens, position + 1, group_sizes, machines_per_factory, factory_count
        )
        if parsed is None:
            continue
        candidates, next_position = parsed
        suffix = _parse_operations(
            tokens,
            next_position,
            job_id,
            op_id + 1,
            op_count,
            factory_count,
            machines_per_factory,
            minimum,
            maximum,
        )
        if suffix is not None:
            rest, final_position = suffix
            return [DistributedOperation(job_id, op_id, tuple(candidates)), *rest], final_position
    return None


def _group_size_options(total: int, count: int, minimum: int, maximum: int) -> Iterator[tuple[int, ...]]:
    def visit(index: int, remaining: int, values: list[int]) -> Iterator[tuple[int, ...]]:
        left = count - index
        if left == 0:
            if remaining == 0:
                yield tuple(values)
            return
        low = max(minimum, remaining - (left - 1) * maximum)
        high = min(maximum, remaining - (left - 1) * minimum)
        for size in range(low, high + 1):
            yield from visit(index + 1, remaining - size, [*values, size])

    if minimum * count <= total <= maximum * count:
        yield from visit(0, total, [])


def _parse_candidate_groups(
    tokens: list[int],
    position: int,
    group_sizes: tuple[int, ...],
    machines_per_factory: int,
    factory_count: int,
) -> tuple[list[DistributedMachineOption], int] | None:
    result: list[DistributedMachineOption] = []
    cursor = position
    max_machine_id = factory_count * machines_per_factory
    for factory_index, group_size in enumerate(group_sizes):
        raw_factory = factory_index + 1
        if cursor + 3 >= len(tokens) or tokens[cursor] != raw_factory:
            return None
        raw_options = [(tokens[cursor + 1], tokens[cursor + 2], tokens[cursor + 3])]
        cursor += 4
        for _ in range(group_size - 1):
            if cursor + 2 >= len(tokens):
                return None
            raw_options.append((tokens[cursor], tokens[cursor + 1], tokens[cursor + 2]))
            cursor += 3
        for machine_id, duration, energy in raw_options:
            if not 1 <= machine_id <= max_machine_id or duration < 0 or energy < 0:
                return None
            result.append(DistributedMachineOption(raw_factory - 1, machine_id - 1, duration, energy))
    return result, cursor


def _header_value(line: str) -> str:
    return line.split(":", 1)[1].strip() if ":" in line else line.strip()


def _header_int(line: str, path: Path, field: str) -> int:
    try:
        return int(_header_value(line))
    except ValueError as exc:
        raise ValueError(f"{path} header {field} is not an integer") from exc


def _header_range(line: str, path: Path, field: str) -> tuple[int, int]:
    value = _header_value(line)
    parts = value.split(":", 1)
    try:
        if len(parts) != 2:
            raise ValueError
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{path} header {field} is not a range") from exc


def load_distributed_solution(path: Path) -> list[DistributedScheduleRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("schedule")
    if not isinstance(records, list):
        raise ValueError("solution must contain a list field named 'schedule'")
    result: list[DistributedScheduleRecord] = []
    for index, item in enumerate(records):
        try:
            result.append(
                DistributedScheduleRecord(
                    int(item["job_id"]),
                    int(item["op_id"]),
                    int(item["factory_id"]),
                    int(item["machine_id"]),
                    int(item["start"]),
                    int(item["end"]),
                )
            )
        except Exception as exc:
            raise ValueError(f"distributed schedule record {index} is malformed: {item!r}") from exc
    return result


def transfer_time_between(
    instance: DistributedFjspInstance,
    previous: DistributedScheduleRecord,
    current: DistributedScheduleRecord,
) -> int:
    if previous.factory_id != current.factory_id:
        return instance.cross_factory_transfer_time
    if previous.machine_id != current.machine_id:
        return instance.same_factory_transfer_time
    return 0


def validate_distributed_schedule(
    instance: DistributedFjspInstance, schedule: list[DistributedScheduleRecord]
) -> tuple[list[str], dict[str, float]]:
    errors: list[str] = []
    seen: dict[tuple[int, int], DistributedScheduleRecord] = {}
    expected: set[tuple[int, int]] = set()
    options: dict[tuple[int, int, int, int], list[DistributedMachineOption]] = {}
    for job in instance.jobs:
        for operation in job.operations:
            expected.add((job.job_id, operation.op_id))
            for option in operation.candidates:
                options.setdefault(
                    (job.job_id, operation.op_id, option.factory_id, option.machine_id), []
                ).append(option)
    if len(schedule) != instance.operation_count:
        errors.append(f"operation count mismatch: expected={instance.operation_count}, got={len(schedule)}")
    selected: dict[tuple[int, int], DistributedMachineOption] = {}
    for record in schedule:
        key = (record.job_id, record.op_id)
        if key in seen:
            errors.append(f"duplicate operation: job={record.job_id}, op={record.op_id}")
        seen[key] = record
        if key not in expected:
            errors.append(f"unknown operation: job={record.job_id}, op={record.op_id}")
        if record.start < 0 or record.end < record.start:
            errors.append(f"invalid interval: job={record.job_id}, op={record.op_id}")
        resource_options = options.get(
            (record.job_id, record.op_id, record.factory_id, record.machine_id)
        )
        if not resource_options:
            errors.append(
                f"factory-machine pair is not a candidate: job={record.job_id}, op={record.op_id}, "
                f"factory={record.factory_id}, machine={record.machine_id}"
            )
        else:
            duration_matches = [
                option for option in resource_options if record.duration == option.duration
            ]
            if not duration_matches:
                expected_durations = sorted({option.duration for option in resource_options})
                errors.append(
                    f"duration mismatch: job={record.job_id}, op={record.op_id}, "
                    f"expected_one_of={expected_durations}, got={record.duration}"
                )
            else:
                # Duration disambiguates repeated resource entries in the DFM data.
                selected[key] = duration_matches[-1]
    for job_id, op_id in sorted(expected - set(seen)):
        errors.append(f"missing operation: job={job_id}, op={op_id}")
    transfer_total = 0
    transfer_count = 0
    for job in instance.jobs:
        for op_id in range(len(job.operations) - 1):
            left, right = seen.get((job.job_id, op_id)), seen.get((job.job_id, op_id + 1))
            if left is None or right is None:
                continue
            transfer = transfer_time_between(instance, left, right)
            transfer_total += transfer
            transfer_count += int(transfer > 0)
            if right.start < left.end + transfer:
                errors.append(
                    f"transfer/precedence violation: job={job.job_id}, op={op_id}, "
                    f"transfer={transfer}, required_start={left.end + transfer}"
                )
    by_resource: dict[tuple[int, int], list[DistributedScheduleRecord]] = {}
    for record in schedule:
        by_resource.setdefault((record.factory_id, record.machine_id), []).append(record)
    for resource, records in by_resource.items():
        ordered = sorted(records, key=lambda item: (item.start, item.end, item.job_id, item.op_id))
        for left, right in zip(ordered, ordered[1:]):
            if right.start < left.end:
                errors.append(f"machine overlap violation: factory={resource[0]}, machine={resource[1]}")
    workloads = [0.0] * instance.factory_count
    processing_energy = 0.0
    for key, option in selected.items():
        record = seen[key]
        if record.duration == option.duration:
            workloads[option.factory_id] += option.duration
            processing_energy += option.duration * option.unit_energy
    metrics = {
        "makespan": float(max((record.end for record in schedule), default=0)),
        "max_factory_workload": float(max(workloads, default=0.0)),
        "total_energy_consumption": float(processing_energy + transfer_total * instance.transfer_unit_energy),
        "scheduled_operations": float(len(schedule)),
        "operation_count": float(instance.operation_count),
        "transfer_time_total": float(transfer_total),
        "transfer_count": float(transfer_count),
    }
    return errors, metrics
