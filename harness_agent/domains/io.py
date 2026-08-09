"""标准 FJSP 及已确认变体的固定 IO、数据模型和合法性验证。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


OpKey = tuple[int, int]
SetupTimes = tuple[tuple[tuple[int, ...], ...], ...]
MinimumTimeLags = tuple["MinimumTimeLag", ...]


@dataclass(frozen=True)
class MachineOption:
    """一次工序在某台候选机器上的加工时长。"""

    machine_id: int
    duration: int


@dataclass(frozen=True)
class Operation:
    """标准 FJSP 工序：只包含候选机集合，不包含任何调度决策。"""

    job_id: int
    op_id: int
    candidates: tuple[MachineOption, ...]


@dataclass(frozen=True)
class Job:
    """作业定义，内部按工序先后顺序排列。"""

    job_id: int
    operations: tuple[Operation, ...]


@dataclass(frozen=True)
class MinimumTimeLag:
    """同一作业内相邻工序之间的最小等待时间。"""

    job_id: int
    from_op: int
    to_op: int
    lag: int


@dataclass(frozen=True)
class MachineUnavailability:
    """A fixed half-open interval during which a machine cannot process."""

    machine_id: int
    start: int
    end: int


@dataclass(frozen=True)
class StandardFjspInstance:
    """标准 FJSP 及兼容标准 schedule schema 变体的只读结构。

    这里是 parser/validator 共用的数据模型，强调“实例语义固定”。任何启发式、
    邻域、优先规则都不应塞进这个层次。
    """

    name: str
    job_count: int
    machine_count: int
    max_candidate_count: int
    jobs: tuple[Job, ...]
    setup_times: SetupTimes = ()
    setup_time_kind: str = "none"
    minimum_time_lags: MinimumTimeLags = ()
    job_release_times: tuple[int, ...] = ()
    machine_available_times: tuple[int, ...] = ()
    unavailability_intervals: tuple[MachineUnavailability, ...] = ()
    priority_job_ids: tuple[int, ...] = ()
    variant: str = "standard_fjsp"

    @property
    def operation_count(self) -> int:
        return sum(len(job.operations) for job in self.jobs)

    @property
    def has_sequence_dependent_setup(self) -> bool:
        return bool(self.setup_times)

    @property
    def has_minimum_time_lags(self) -> bool:
        return self.variant == "fjsp_min_time_lag"

    @property
    def has_release_times(self) -> bool:
        return self.variant == "fjsp_release_time"

    @property
    def has_machine_availability(self) -> bool:
        return self.variant == "fjsp_machine_availability"

    @property
    def has_job_priorities(self) -> bool:
        return self.variant == "fjsp_priority"


@dataclass(frozen=True)
class ScheduleRecord:
    """调度解中的单条工序排产记录。"""

    job_id: int
    op_id: int
    machine_id: int
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def parse_standard_fjsp(path: Path) -> StandardFjspInstance:
    """解析标准 FJSP 以及已确认的 SDST/min-time-lag 尾部。

    本模块只实现 IO 和合法性语义，不实现构造、邻域或搜索策略。

    The format starts with three integers:

    `job_count machine_count max_candidate_count`

    Each job then gives its operation count.  Each operation gives a candidate
    count followed by `(machine_id, processing_time)` pairs.  FJSP-SDST files
    may append either an operation-pair setup matrix
    (`machine_count * operation_count * operation_count`) or a HUdata job-pair
    setup matrix (`machine_count * job_count * job_count`).
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
                if duration < 0:
                    raise ValueError(f"{path} has negative duration {duration}")
                machine_ids.append(machine_id)
                candidates.append((machine_id, duration))
            raw_ops.append(candidates)
        raw_jobs.append(raw_ops)

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

    operation_count = sum(len(job.operations) for job in jobs)
    (
        setup_times,
        setup_time_kind,
        minimum_time_lags,
        job_release_times,
        machine_available_times,
        unavailability_intervals,
        priority_job_ids,
        variant,
    ) = _parse_optional_variant_tail(
        path=path,
        tail=numbers[idx:],
        jobs=tuple(jobs),
        machine_count=machine_count,
        operation_count=operation_count,
    )

    return StandardFjspInstance(
        name=path.stem,
        job_count=job_count,
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
        setup_times=setup_times,
        setup_time_kind=setup_time_kind,
        minimum_time_lags=minimum_time_lags,
        job_release_times=job_release_times,
        machine_available_times=machine_available_times,
        unavailability_intervals=unavailability_intervals,
        priority_job_ids=priority_job_ids,
        variant=variant,
    )


def _parse_optional_variant_tail(
    *,
    path: Path,
    tail: list[int],
    jobs: tuple[Job, ...],
    machine_count: int,
    operation_count: int,
) -> tuple[
    SetupTimes,
    str,
    MinimumTimeLags,
    tuple[int, ...],
    tuple[int, ...],
    tuple[MachineUnavailability, ...],
    tuple[int, ...],
    str,
]:
    """严格解析可选的 SDST matrix 或 min-time-lag constraint list。

    当前支持已确认的 setup matrix、minimum-lag list、release rows 和
    machine-unavailability list。文件标记优先用于消除长度碰撞；如果尾部结构
    仍有歧义则拒绝解析。

    具体编码包括：
    1. operation-pair 矩阵；
    2. HUdata 风格的 job-pair 矩阵；
    3. `K` 后跟 K 条 `(job_id, from_op, to_op, L_min)`。
    如果尾部结构不匹配，宁可抛错，也不忽略或猜测其语义。
    """

    job_count = len(jobs)
    name = path.name.casefold()
    priority_name = ".priority." in name or name.endswith(".priority.txt") or name.endswith(".priority")
    if not tail:
        if priority_name:
            raise ValueError(f"{path} priority tail is missing")
        return (), "none", (), (), (), (), (), "standard_fjsp"
    machine_availability_name = (
        name.startswith(("ffcr", "nfa", "fjsp_nfa"))
        or ".nfafjsp" in name
        or ".nfa." in name
    )
    if priority_name:
        priority_job_ids = _parse_priority_jobs(path=path, tail=tail, job_count=job_count)
        return (), "none", (), (), (), (), priority_job_ids, "fjsp_priority"
    if ".mitfjsp" in name:
        return (), "none", _parse_minimum_time_lags(path=path, tail=tail, jobs=jobs), (), (), (), (), "fjsp_min_time_lag"
    if ".rtfjsp" in name:
        job_release, machine_available = _parse_release_times(
            path=path,
            tail=tail,
            job_count=job_count,
            machine_count=machine_count,
        )
        return (), "none", (), job_release, machine_available, (), (), "fjsp_release_time"
    if machine_availability_name:
        intervals = _parse_machine_unavailability(path=path, tail=tail, machine_count=machine_count)
        return (), "none", (), (), (), intervals, (), "fjsp_machine_availability"
    operation_pair_expected = machine_count * operation_count * operation_count
    job_pair_expected = machine_count * job_count * job_count
    if len(tail) == operation_pair_expected:
        dimension = operation_count
        kind = "operation_pair"
    elif len(tail) == job_pair_expected:
        dimension = job_count
        kind = "job_pair"
    else:
        min_lag_match = len(tail) == 1 + 4 * tail[0] if tail and tail[0] >= 0 else False
        availability_match = len(tail) == 1 + 3 * tail[0] if tail and tail[0] >= 0 else False
        if min_lag_match and availability_match:
            raise ValueError(f"{path} has an ambiguous zero-count variant tail; use a recognized variant filename")
        if min_lag_match:
            constraints = _parse_minimum_time_lags(path=path, tail=tail, jobs=jobs)
            return (), "none", constraints, (), (), (), (), "fjsp_min_time_lag"
        if availability_match:
            intervals = _parse_machine_unavailability(path=path, tail=tail, machine_count=machine_count)
            return (), "none", (), (), (), intervals, (), "fjsp_machine_availability"
        raise ValueError(
            f"{path} has trailing tokens that match no supported setup, minimum-lag, "
            f"release-time, or machine-availability encoding: trailing={len(tail)}"
        )
    cursor = 0
    setup_by_machine: list[tuple[tuple[int, ...], ...]] = []
    for machine_id in range(machine_count):
        rows: list[tuple[int, ...]] = []
        for _ in range(dimension):
            row = tuple(tail[cursor : cursor + dimension])
            cursor += dimension
            if any(value < 0 for value in row):
                raise ValueError(f"{path} has negative setup time for machine {machine_id}")
            rows.append(row)
        setup_by_machine.append(tuple(rows))
    return tuple(setup_by_machine), kind, (), (), (), (), (), "fjsp_sdst"


def _parse_priority_jobs(*, path: Path, tail: list[int], job_count: int) -> tuple[int, ...]:
    expected_count = (job_count + 3) // 4
    if not tail:
        raise ValueError(f"{path} priority tail is missing")
    declared_count = tail[0]
    if declared_count != expected_count:
        raise ValueError(
            f"{path} priority count must equal ceil(job_count/4)={expected_count}, "
            f"got {declared_count}"
        )
    if len(tail) != 1 + declared_count:
        raise ValueError(
            f"{path} priority tail must contain exactly {1 + declared_count} integers, "
            f"got {len(tail)}"
        )
    priority_job_ids = tuple(tail[1:])
    if any(not 0 <= job_id < job_count for job_id in priority_job_ids):
        raise ValueError(f"{path} priority job id is out of range [0, {job_count})")
    if any(left >= right for left, right in zip(priority_job_ids, priority_job_ids[1:])):
        raise ValueError(f"{path} priority job ids must be strictly ascending and unique")
    return priority_job_ids


def _parse_release_times(
    *, path: Path, tail: list[int], job_count: int, machine_count: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(job_count, machine_count)
    if len(tail) != 2 * width:
        raise ValueError(f"{path} release-time tail must contain {2 * width} integers, got {len(tail)}")
    job_row = tail[:width]
    machine_row = tail[width:]
    if any(value < 0 for value in job_row[:job_count]):
        raise ValueError(f"{path} has a negative real job release time")
    if any(value != -1 for value in job_row[job_count:]):
        raise ValueError(f"{path} job release-time padding must be -1")
    if any(value < 0 for value in machine_row[:machine_count]):
        raise ValueError(f"{path} has a negative real machine available time")
    if any(value != -1 for value in machine_row[machine_count:]):
        raise ValueError(f"{path} machine available-time padding must be -1")
    return tuple(job_row[:job_count]), tuple(machine_row[:machine_count])


def _parse_machine_unavailability(
    *, path: Path, tail: list[int], machine_count: int
) -> tuple[MachineUnavailability, ...]:
    count = tail[0]
    if count < 0 or len(tail) != 1 + 3 * count:
        raise ValueError(f"{path} has an invalid machine-availability tail")
    intervals: list[MachineUnavailability] = []
    for index in range(count):
        machine_id, start, end = tail[1 + 3 * index : 4 + 3 * index]
        if not 0 <= machine_id < machine_count:
            raise ValueError(f"{path} availability machine id {machine_id} is out of range")
        if start < 0 or end <= start:
            raise ValueError(f"{path} has invalid availability interval [{start}, {end})")
        intervals.append(MachineUnavailability(machine_id, start, end))
    return tuple(intervals)


def _parse_minimum_time_lags(
    *,
    path: Path,
    tail: list[int],
    jobs: tuple[Job, ...],
) -> MinimumTimeLags:
    """Parse the confirmed adjacent-pair minimum time-lag list."""

    constraint_count = tail[0]
    if constraint_count < 0 or len(tail) != 1 + 4 * constraint_count:
        raise ValueError(
            f"{path} has trailing tokens that match neither a supported setup matrix nor "
            f"a min-time-lag list: trailing={len(tail)}, declared_constraints={constraint_count}"
        )
    constraints: list[MinimumTimeLag] = []
    seen: set[tuple[int, int, int]] = set()
    for index in range(constraint_count):
        offset = 1 + index * 4
        job_id, from_op, to_op, lag = tail[offset : offset + 4]
        if not 0 <= job_id < len(jobs):
            raise ValueError(f"{path} min-time-lag {index} has out-of-range job_id={job_id}")
        if to_op != from_op + 1:
            raise ValueError(
                f"{path} min-time-lag {index} must target adjacent operations: "
                f"from_op={from_op}, to_op={to_op}"
            )
        if not 0 <= from_op < len(jobs[job_id].operations) - 1:
            raise ValueError(
                f"{path} min-time-lag {index} has out-of-range operation pair "
                f"job={job_id}, from_op={from_op}, to_op={to_op}"
            )
        if lag < 0:
            raise ValueError(f"{path} min-time-lag {index} has negative lag={lag}")
        key = (job_id, from_op, to_op)
        if key in seen:
            raise ValueError(f"{path} has duplicate min-time-lag constraint for {key}")
        seen.add(key)
        constraints.append(MinimumTimeLag(job_id=job_id, from_op=from_op, to_op=to_op, lag=lag))
    return tuple(constraints)


def operation_index_lookup(instance: StandardFjspInstance) -> dict[OpKey, int]:
    """建立 `(job_id, op_id)` 到全局工序索引的映射。

    operation-pair setup 矩阵按“全局工序顺序”索引，因此 validator 需要这张查表。
    """

    return {
        (job.job_id, op.op_id): index
        for index, (job, op) in enumerate((job, op) for job in instance.jobs for op in job.operations)
    }


def setup_time_between(
    instance: StandardFjspInstance,
    machine_id: int,
    previous_op: OpKey | None,
    current_op: OpKey,
    op_index: dict[OpKey, int] | None = None,
) -> int:
    """读取两道工序在同一机器上的 setup 时间。"""

    if previous_op is None or not instance.setup_times:
        return 0
    if not 0 <= machine_id < len(instance.setup_times):
        return 0
    if instance.setup_time_kind == "job_pair":
        return instance.setup_times[machine_id][previous_op[0]][current_op[0]]
    index = op_index or operation_index_lookup(instance)
    return instance.setup_times[machine_id][index[previous_op]][index[current_op]]


def load_solution(path: Path) -> list[ScheduleRecord]:
    """读取 solver 输出的标准解格式。

    parser 的职责仅限于结构和类型校验；调度可行性仍由 `validate_standard_schedule()`
    统一判断。
    """

    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("schedule")
    if not isinstance(records, list):
        raise ValueError("solution must contain a list field named 'schedule'")
    parsed: list[ScheduleRecord] = []
    for index, item in enumerate(records):
        try:
            parsed.append(
                ScheduleRecord(
                    job_id=int(_solution_field(item, "job_id", "job")),
                    op_id=int(_solution_field(item, "op_id", "operation")),
                    machine_id=int(_solution_field(item, "machine_id", "machine")),
                    start=int(item["start"]),
                    end=int(item["end"]),
                )
            )
        except Exception as exc:  # noqa: BLE001 - convert malformed records into validation errors.
            raise ValueError(f"schedule record {index} is malformed: {item!r}") from exc
    return parsed


def _solution_field(item: dict[str, Any], canonical: str, alias: str) -> Any:
    """Accept the fixed schema and the audited FJSPSolutionV1 field names."""

    if canonical in item:
        return item[canonical]
    return item[alias]


def write_solution(path: Path, instance: StandardFjspInstance, schedule: list[ScheduleRecord], strategy: str) -> None:
    """按固定 JSON 协议输出解。

    输出里只记录 evaluator 需要的排产事实和少量来源元数据，不嵌入任何“自证最优”
    之类的求解侧结论。
    """

    payload: dict[str, Any] = {
        "format": "standard_fjsp_schedule_v1",
        "variant": instance.variant,
        "instance": instance.name,
        "strategy": strategy,
        "makespan": max((record.end for record in schedule), default=0),
        "setup_time_policy": "implicit_by_evaluator",
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
    if instance.has_minimum_time_lags:
        payload["min_time_lag_policy"] = "checked_by_evaluator"
    if instance.has_release_times:
        payload["release_time_policy"] = "checked_by_evaluator"
    if instance.has_machine_availability:
        payload["machine_availability_policy"] = "checked_by_evaluator"
    if instance.has_job_priorities:
        completion_times = [
            record.end
            for record in schedule
            if record.job_id in instance.priority_job_ids
            and record.op_id == len(instance.jobs[record.job_id].operations) - 1
        ]
        payload["priority_completion_time"] = max(completion_times, default=0)
        payload["priority_policy"] = "lexicographic_after_makespan"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_standard_schedule(
    instance: StandardFjspInstance,
    schedule: list[ScheduleRecord],
) -> tuple[list[str], dict[str, float]]:
    """验证标准 FJSP、FJSP-SDST 和 Min Time-Lag FJSP 解。

    这是 parser/validator 层的关键边界：它只判断“这份 schedule 是否满足实例 IO
    语义”，并返回基础指标；不负责比较算法优劣，也不决定是否 promotion。
    """

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
        if record.end < record.start:
            errors.append(f"negative interval: job={record.job_id}, op={record.op_id}")
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

    if instance.has_release_times:
        for job_id, release_time in enumerate(instance.job_release_times):
            first = seen.get((job_id, 0))
            if first is not None and first.start < release_time:
                errors.append(
                    f"job release-time violation: job={job_id}, start={first.start}, "
                    f"release_time={release_time}"
                )
        for record in schedule:
            if 0 <= record.machine_id < len(instance.machine_available_times):
                available_time = instance.machine_available_times[record.machine_id]
                if record.start < available_time:
                    errors.append(
                        f"machine available-time violation: machine={record.machine_id}, "
                        f"job={record.job_id}, op={record.op_id}, start={record.start}, "
                        f"available_time={available_time}"
                    )

    for job in instance.jobs:
        for op_idx in range(len(job.operations) - 1):
            current = seen.get((job.job_id, op_idx))
            nxt = seen.get((job.job_id, op_idx + 1))
            if current and nxt and nxt.start < current.end:
                errors.append(
                    f"precedence violation: job={job.job_id}, op={op_idx} ends at {current.end}, "
                    f"op={op_idx + 1} starts at {nxt.start}"
                )

    min_time_lag_violations = 0
    for constraint in instance.minimum_time_lags:
        previous = seen.get((constraint.job_id, constraint.from_op))
        successor = seen.get((constraint.job_id, constraint.to_op))
        if previous is None or successor is None:
            continue
        actual_gap = successor.start - previous.end
        if actual_gap < constraint.lag:
            min_time_lag_violations += 1
            errors.append(
                f"minimum time-lag violation: job={constraint.job_id}, "
                f"from_op={constraint.from_op}, to_op={constraint.to_op}, "
                f"required_gap={constraint.lag}, actual_gap={actual_gap}"
            )

    by_machine: dict[int, list[ScheduleRecord]] = {}
    for record in schedule:
        by_machine.setdefault(record.machine_id, []).append(record)
    machine_availability_violations = 0
    for interval in instance.unavailability_intervals:
        for record in by_machine.get(interval.machine_id, []):
            if record.start < interval.end and interval.start < record.end:
                machine_availability_violations += 1
                errors.append(
                    f"machine availability violation: machine={interval.machine_id}, "
                    f"job={record.job_id}, op={record.op_id}, operation=[{record.start},{record.end}), "
                    f"unavailable=[{interval.start},{interval.end})"
                )
    op_index = operation_index_lookup(instance)
    total_setup_time = 0
    setup_count = 0
    for machine_id, records in by_machine.items():
        sorted_records = sorted(records, key=lambda item: (item.start, item.end, item.job_id, item.op_id))
        for left, right in zip(sorted_records, sorted_records[1:]):
            setup_time = setup_time_between(
                instance,
                machine_id,
                (left.job_id, left.op_id),
                (right.job_id, right.op_id),
                op_index,
            )
            total_setup_time += setup_time
            if setup_time:
                setup_count += 1
            required_start = left.end + setup_time
            if right.start < required_start:
                errors.append(
                    f"machine overlap/setup violation: machine={machine_id}, "
                    f"left=({left.job_id},{left.op_id},{left.start},{left.end}), "
                    f"right=({right.job_id},{right.op_id},{right.start},{right.end}), "
                    f"setup={setup_time}, required_start={required_start}"
                )

    makespan = max((record.end for record in schedule), default=0)
    metrics = {
        "makespan": float(makespan),
        "scheduled_operations": float(len(schedule)),
        "operation_count": float(instance.operation_count),
    }
    if instance.has_sequence_dependent_setup:
        metrics["setup_time"] = float(total_setup_time)
        metrics["setup_count"] = float(setup_count)
    if instance.has_minimum_time_lags:
        metrics["min_time_lag_constraints"] = float(len(instance.minimum_time_lags))
        metrics["min_time_lag_violations"] = float(min_time_lag_violations)
    if instance.has_release_times:
        metrics["max_job_release_time"] = float(max(instance.job_release_times, default=0))
        metrics["max_machine_available_time"] = float(max(instance.machine_available_times, default=0))
    if instance.has_machine_availability:
        metrics["machine_availability_violations"] = float(machine_availability_violations)
        metrics["total_unavailable_duration"] = float(
            sum(interval.end - interval.start for interval in instance.unavailability_intervals)
        )
    if instance.has_job_priorities:
        priority_completion_time = max(
            (
                seen[(job_id, len(instance.jobs[job_id].operations) - 1)].end
                for job_id in instance.priority_job_ids
                if (job_id, len(instance.jobs[job_id].operations) - 1) in seen
            ),
            default=0,
        )
        metrics["priority_completion_time"] = float(priority_completion_time)
        metrics["priority_job_count"] = float(len(instance.priority_job_ids))
    return errors, metrics
