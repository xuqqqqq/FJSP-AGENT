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
class StandardFjspInstance:
    """标准 FJSP/FJSP-SDST 算例的只读结构。

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
    setup_times, setup_time_kind, minimum_time_lags, variant = _parse_optional_variant_tail(
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
        variant=variant,
    )


def _parse_optional_variant_tail(
    *,
    path: Path,
    tail: list[int],
    jobs: tuple[Job, ...],
    machine_count: int,
    operation_count: int,
) -> tuple[SetupTimes, str, MinimumTimeLags, str]:
    """严格解析可选的 SDST matrix 或 min-time-lag constraint list。

    当前支持三类已确认格式：
    1. operation-pair 矩阵；
    2. HUdata 风格的 job-pair 矩阵。
    3. `K` 后跟 K 条 `(job_id, from_op, to_op, L_min)`。
    如果尾部结构不匹配，宁可抛错，也不忽略或猜测其语义。
    """

    if not tail:
        return (), "none", (), "standard_fjsp"
    job_count = len(jobs)
    if ".mitfjsp" in path.name.casefold():
        return (), "none", _parse_minimum_time_lags(path=path, tail=tail, jobs=jobs), "fjsp_min_time_lag"
    operation_pair_expected = machine_count * operation_count * operation_count
    job_pair_expected = machine_count * job_count * job_count
    if len(tail) == operation_pair_expected:
        dimension = operation_count
        kind = "operation_pair"
    elif len(tail) == job_pair_expected:
        dimension = job_count
        kind = "job_pair"
    else:
        return (), "none", _parse_minimum_time_lags(path=path, tail=tail, jobs=jobs), "fjsp_min_time_lag"
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
    return tuple(setup_by_machine), kind, (), "fjsp_sdst"


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
    return errors, metrics
