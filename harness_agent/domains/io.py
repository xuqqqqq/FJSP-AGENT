"""标准 FJSP、FJSP-SDST、FJSP-NFA、FJSPJP 与 DFJSPT 的固定 IO、数据模型和合法性验证。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any


OpKey = tuple[int, int]
SetupTimes = tuple[tuple[tuple[int, ...], ...], ...]
SAME_FACTORY_TRANSFER_TIME = 30
CROSS_FACTORY_TRANSFER_TIME = 60
TRANSFER_UNIT_ENERGY = 6


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
class DistributedMachineOption:
    """DFJSPT 中一次工序在某工厂某机器上的加工与能耗候选。"""

    factory_id: int
    machine_id: int
    duration: int
    unit_energy: int


@dataclass(frozen=True)
class DistributedOperation:
    """分布式可转移 FJSP 工序：候选资源由工厂和机器共同确定。"""

    job_id: int
    op_id: int
    candidates: tuple[DistributedMachineOption, ...]


@dataclass(frozen=True)
class DistributedJob:
    """DFJSPT 作业定义，内部按工序先后顺序排列。"""

    job_id: int
    operations: tuple[DistributedOperation, ...]


@dataclass(frozen=True)
class DistributedFjspInstance:
    """分布式可转移 FJSP 算例的只读结构。"""

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
        return max(
            (len(operation.candidates) for job in self.jobs for operation in job.operations),
            default=0,
        )

    @property
    def machine_count(self) -> int:
        return self.factory_count * self.machines_per_factory


@dataclass(frozen=True)
class MachineUnavailability:
    """一道机器不可用区间。"""

    machine_id: int
    start: int
    end: int


@dataclass(frozen=True)
class StandardFjspInstance:
    """标准 FJSP/FJSP-SDST/FJSP-NFA 算例的只读结构。

    这里是 parser/validator 共用的数据模型，强调"实例语义固定"。任何启发式、
    邻域、优先规则都不应塞进这个层次。
    """

    name: str
    job_count: int
    machine_count: int
    max_candidate_count: int
    jobs: tuple[Job, ...]
    setup_times: SetupTimes = ()
    setup_time_kind: str = "none"
    unavailability_intervals: tuple[MachineUnavailability, ...] = ()
    unavailability_count: int = 0
    priority_job_ids: tuple[int, ...] = ()

    @property
    def operation_count(self) -> int:
        return sum(len(job.operations) for job in self.jobs)

    @property
    def has_sequence_dependent_setup(self) -> bool:
        return bool(self.setup_times)

    @property
    def has_machine_availability(self) -> bool:
        return bool(self.unavailability_intervals)

    @property
    def has_job_priority(self) -> bool:
        return bool(self.priority_job_ids)


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


@dataclass(frozen=True)
class DistributedScheduleRecord:
    """DFJSPT 调度解中的单条工序排产记录。"""

    job_id: int
    op_id: int
    factory_id: int
    machine_id: int
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def parse_standard_fjsp(path: Path) -> StandardFjspInstance:
    """解析标准 FJSP 以及已确认的 FJSP-SDST / FJSP-NFA 尾部。

    本模块只实现 IO 和合法性语义，不实现构造、邻域或搜索策略。
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
    tail = numbers[idx:]
    if not tail:
        setup_times: SetupTimes = ()
        setup_time_kind = "none"
        unavailability_intervals: tuple[MachineUnavailability, ...] = ()
        unavailability_count = 0
        priority_job_ids: tuple[int, ...] = ()
    else:
        sdst_result = _try_parse_setup_times(
            tail=tail,
            job_count=job_count,
            machine_count=machine_count,
            operation_count=operation_count,
        )
        if sdst_result is not None:
            setup_times, setup_time_kind = sdst_result
            unavailability_intervals = ()
            unavailability_count = 0
            priority_job_ids = ()
        else:
            priority_result = _try_parse_job_priority_tail(tail=tail, job_count=job_count)
            if priority_result is not None:
                setup_times = ()
                setup_time_kind = "none"
                unavailability_intervals = ()
                unavailability_count = 0
                priority_job_ids = priority_result
            else:
                nfa_result = _try_parse_machine_availability(
                    tail=tail,
                    machine_count=machine_count,
                )
                if nfa_result is not None:
                    setup_times = ()
                    setup_time_kind = "none"
                    unavailability_intervals = tuple(nfa_result)
                    unavailability_count = len(nfa_result)
                    priority_job_ids = ()
                else:
                    raise ValueError(
                        f"{path} has {len(tail)} trailing tokens that do not match "
                        f"FJSP-SDST setup matrix, FJSPJP priority jobs, or NFA machine availability intervals"
                    )

    return StandardFjspInstance(
        name=path.stem,
        job_count=job_count,
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
        setup_times=setup_times,
        setup_time_kind=setup_time_kind,
        unavailability_intervals=unavailability_intervals,
        unavailability_count=unavailability_count,
        priority_job_ids=priority_job_ids,
    )


def parse_distributed_fjsp(path: Path) -> DistributedFjspInstance:
    """解析 Distributed FJSP with Transfers / DFJSPT 算例。

    DFM 算例的工序候选不是简单定长元组：每道工序按 factory 分组，
    每个 factory 组第一台候选显式写出 factory_id，后续同 factory 候选省略
    factory_id。因此这里按 header 中的工厂数和每工厂候选范围恢复分组。
    """

    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(lines) < 6:
        raise ValueError(f"{path} is too short to be a distributed FJSP instance")

    source_id = _header_value(lines[0])
    job_count = _header_int(lines[1], path=path, field="job count")
    factory_count = _header_int(lines[2], path=path, field="factory count")
    machines_per_factory = _header_int(lines[3], path=path, field="machines per factory")
    min_per_factory, max_per_factory = _header_range(
        lines[4],
        path=path,
        field="available machine range",
    )
    if job_count <= 0:
        raise ValueError(f"{path} has non-positive job count {job_count}")
    if factory_count <= 0:
        raise ValueError(f"{path} has non-positive factory count {factory_count}")
    if machines_per_factory <= 0:
        raise ValueError(f"{path} has non-positive machines-per-factory {machines_per_factory}")
    if min_per_factory <= 0 or max_per_factory < min_per_factory:
        raise ValueError(
            f"{path} has invalid available machine range "
            f"{min_per_factory}:{max_per_factory}"
        )

    job_lines = lines[5:]
    if len(job_lines) != job_count:
        raise ValueError(
            f"{path} declares {job_count} jobs but contains {len(job_lines)} job rows"
        )

    jobs: list[DistributedJob] = []
    for job_id, line in enumerate(job_lines):
        tokens = [int(token) for token in line.split()]
        jobs.append(
            _parse_distributed_job_line(
                tokens=tokens,
                job_id=job_id,
                factory_count=factory_count,
                machines_per_factory=machines_per_factory,
                min_per_factory=min_per_factory,
                max_per_factory=max_per_factory,
                path=path,
            )
        )

    return DistributedFjspInstance(
        name=path.stem,
        source_id=source_id,
        job_count=job_count,
        factory_count=factory_count,
        machines_per_factory=machines_per_factory,
        min_machines_per_operation_per_factory=min_per_factory,
        max_machines_per_operation_per_factory=max_per_factory,
        jobs=tuple(jobs),
    )


def _try_parse_setup_times(
    *,
    tail: list[int],
    job_count: int,
    machine_count: int,
    operation_count: int,
) -> tuple[SetupTimes, str] | None:
    """尝试解析 FJSP-SDST setup 尾部。

    返回 ``(matrix, kind)`` 若尾部长度匹配 SDST 矩阵尺寸；
    返回 ``None`` 表示尾部不是 SDST 格式。
    """

    operation_pair_expected = machine_count * operation_count * operation_count
    job_pair_expected = machine_count * job_count * job_count
    if len(tail) == operation_pair_expected:
        dimension = operation_count
        kind = "operation_pair"
    elif len(tail) == job_pair_expected:
        dimension = job_count
        kind = "job_pair"
    else:
        return None
    cursor = 0
    setup_by_machine: list[tuple[tuple[int, ...], ...]] = []
    for mid in range(machine_count):
        rows: list[tuple[int, ...]] = []
        for _ in range(dimension):
            row = tuple(tail[cursor : cursor + dimension])
            cursor += dimension
            if any(value < 0 for value in row):
                raise ValueError(f"negative setup time for machine {mid}")
            rows.append(row)
        setup_by_machine.append(tuple(rows))
    return tuple(setup_by_machine), kind


def _parse_distributed_job_line(
    *,
    tokens: list[int],
    job_id: int,
    factory_count: int,
    machines_per_factory: int,
    min_per_factory: int,
    max_per_factory: int,
    path: Path,
) -> DistributedJob:
    if not tokens:
        raise ValueError(f"{path} job {job_id} row is empty")
    op_count = tokens[0]
    if op_count <= 0:
        raise ValueError(f"{path} job {job_id} has non-positive operation count {op_count}")
    parsed = _parse_distributed_operations_from(
        tokens=tokens,
        pos=1,
        job_id=job_id,
        op_id=0,
        op_count=op_count,
        factory_count=factory_count,
        machines_per_factory=machines_per_factory,
        min_per_factory=min_per_factory,
        max_per_factory=max_per_factory,
        path=path,
    )
    if parsed is None:
        raise ValueError(
            f"{path} job {job_id} row does not match DFJSPT grouped candidate format"
        )
    operations, pos = parsed
    if pos != len(tokens):
        raise ValueError(f"{path} job {job_id} has {len(tokens) - pos} trailing token(s)")
    return DistributedJob(job_id=job_id, operations=tuple(operations))


def _parse_distributed_operations_from(
    *,
    tokens: list[int],
    pos: int,
    job_id: int,
    op_id: int,
    op_count: int,
    factory_count: int,
    machines_per_factory: int,
    min_per_factory: int,
    max_per_factory: int,
    path: Path,
) -> tuple[list[DistributedOperation], int] | None:
    if op_id == op_count:
        return ([], pos) if pos == len(tokens) else None
    if pos >= len(tokens):
        return None
    candidate_count = tokens[pos]
    if candidate_count <= 0:
        return None
    for candidates, next_pos in _parse_distributed_operation_candidate_options(
        tokens=tokens,
        pos=pos + 1,
        job_id=job_id,
        op_id=op_id,
        candidate_count=candidate_count,
        factory_count=factory_count,
        machines_per_factory=machines_per_factory,
        min_per_factory=min_per_factory,
        max_per_factory=max_per_factory,
        path=path,
    ):
        suffix = _parse_distributed_operations_from(
            tokens=tokens,
            pos=next_pos,
            job_id=job_id,
            op_id=op_id + 1,
            op_count=op_count,
            factory_count=factory_count,
            machines_per_factory=machines_per_factory,
            min_per_factory=min_per_factory,
            max_per_factory=max_per_factory,
            path=path,
        )
        if suffix is None:
            continue
        rest_operations, final_pos = suffix
        operation = DistributedOperation(
            job_id=job_id,
            op_id=op_id,
            candidates=tuple(candidates),
        )
        return [operation, *rest_operations], final_pos
    return None


def _parse_distributed_operation_candidate_options(
    *,
    tokens: list[int],
    pos: int,
    job_id: int,
    op_id: int,
    candidate_count: int,
    factory_count: int,
    machines_per_factory: int,
    min_per_factory: int,
    max_per_factory: int,
    path: Path,
) -> list[tuple[list[DistributedMachineOption], int]]:
    min_total = factory_count * min_per_factory
    max_total = factory_count * max_per_factory
    if not min_total <= candidate_count <= max_total:
        return []
    options: list[tuple[list[DistributedMachineOption], int]] = []
    for group_sizes in _distributed_factory_group_size_options(
        total=candidate_count,
        group_count=factory_count,
        min_size=min_per_factory,
        max_size=max_per_factory,
    ):
        parsed = _try_parse_distributed_candidate_groups(
            tokens=tokens,
            pos=pos,
            job_id=job_id,
            op_id=op_id,
            group_sizes=group_sizes,
            max_machine_id=factory_count * machines_per_factory,
            path=path,
        )
        if parsed is not None:
            options.append(parsed)
    return options


def _try_parse_distributed_candidate_groups(
    *,
    tokens: list[int],
    pos: int,
    job_id: int,
    op_id: int,
    group_sizes: tuple[int, ...],
    max_machine_id: int,
    path: Path,
) -> tuple[list[DistributedMachineOption], int] | None:
    candidates: list[DistributedMachineOption] = []
    cursor = pos
    for factory_index, group_size in enumerate(group_sizes):
        raw_factory_id = factory_index + 1
        if cursor + 3 >= len(tokens) or tokens[cursor] != raw_factory_id:
            return None
        try:
            candidates.append(
                _distributed_machine_option(
                    factory_id=raw_factory_id,
                    machine_id=tokens[cursor + 1],
                    duration=tokens[cursor + 2],
                    unit_energy=tokens[cursor + 3],
                    max_machine_id=max_machine_id,
                    path=path,
                    job_id=job_id,
                    op_id=op_id,
                )
            )
        except ValueError:
            return None
        cursor += 4
        for _ in range(group_size - 1):
            if cursor + 2 >= len(tokens):
                return None
            try:
                candidates.append(
                    _distributed_machine_option(
                        factory_id=raw_factory_id,
                        machine_id=tokens[cursor],
                        duration=tokens[cursor + 1],
                        unit_energy=tokens[cursor + 2],
                        max_machine_id=max_machine_id,
                        path=path,
                        job_id=job_id,
                        op_id=op_id,
                    )
                )
            except ValueError:
                return None
            cursor += 3
    return candidates, cursor


def _distributed_factory_group_size_options(
    *,
    total: int,
    group_count: int,
    min_size: int,
    max_size: int,
) -> list[tuple[int, ...]]:
    """还原 DFM 每个工厂组的候选数。

    DFM 文件只写 operation 的总候选数，并在每个工厂组开始处写 factory_id；
    每工厂候选数由总数、header 中的范围和后续显式 factory marker 共同决定。
    """

    result: list[tuple[int, ...]] = []

    def visit(group_index: int, remaining: int, prefix: list[int]) -> None:
        groups_left = group_count - group_index
        if groups_left == 0:
            if remaining == 0:
                result.append(tuple(prefix))
            return
        low = max(min_size, remaining - (groups_left - 1) * max_size)
        high = min(max_size, remaining - (groups_left - 1) * min_size)
        for size in range(low, high + 1):
            prefix.append(size)
            visit(group_index + 1, remaining - size, prefix)
            prefix.pop()

    visit(0, total, [])
    return result


def _distributed_machine_option(
    *,
    factory_id: int,
    machine_id: int,
    duration: int,
    unit_energy: int,
    max_machine_id: int,
    path: Path,
    job_id: int,
    op_id: int,
) -> DistributedMachineOption:
    if machine_id <= 0:
        raise ValueError(
            f"{path} job {job_id} operation {op_id} has non-positive machine_id {machine_id}"
        )
    if machine_id > max_machine_id:
        raise ValueError(
            f"{path} job {job_id} operation {op_id} machine_id {machine_id} "
            f"exceeds global machine upper bound {max_machine_id}"
        )
    if duration < 0:
        raise ValueError(
            f"{path} job {job_id} operation {op_id} has negative duration {duration}"
        )
    if unit_energy < 0:
        raise ValueError(
            f"{path} job {job_id} operation {op_id} has negative unit_energy {unit_energy}"
        )
    return DistributedMachineOption(
        factory_id=factory_id - 1,
        machine_id=machine_id - 1,
        duration=duration,
        unit_energy=unit_energy,
    )


def _header_value(line: str) -> str:
    if ":" not in line:
        return line.strip()
    return line.split(":", 1)[1].strip()


def _header_int(line: str, *, path: Path, field: str) -> int:
    value = _header_value(line)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{path} header {field} is not an integer: {value!r}") from exc


def _header_range(line: str, *, path: Path, field: str) -> tuple[int, int]:
    value = _header_value(line)
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"{path} header {field} is not a range: {value!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{path} header {field} is not an integer range: {value!r}") from exc


def _try_parse_machine_availability(
    *,
    tail: list[int],
    machine_count: int,
) -> list[MachineUnavailability] | None:
    """尝试解析 NFA 机器不可用区间尾部。

    格式: K + Kx3 (machine_id start end)
    返回区间列表若格式匹配；返回 None 否则。
    """

    if not tail or len(tail) < 1:
        return None
    K = tail[0]
    expected = 1 + K * 3
    if len(tail) != expected:
        return None
    intervals: list[MachineUnavailability] = []
    pos = 1
    for _ in range(K):
        mid = tail[pos]
        s = tail[pos + 1]
        e = tail[pos + 2]
        pos += 3
        if not (0 <= mid < machine_count):
            raise ValueError(
                f"machine id {mid} out of range [0, {machine_count}) "
                f"in availability interval [{s}, {e})"
            )
        if e <= s:
            raise ValueError(
                f"invalid availability interval [{s}, {e}): "
                f"end must be greater than start"
            )
        intervals.append(MachineUnavailability(machine_id=mid, start=s, end=e))
    return intervals


def _try_parse_job_priority_tail(
    *,
    tail: list[int],
    job_count: int,
) -> tuple[int, ...] | None:
    """尝试解析 FJSPJP 优先级工件尾部。

    格式: K + K 个 0-based job_id。本数据集约定 K=ceil(job_count/4)。
    返回 priority job ids 若格式匹配；返回 None 表示尾部不是 priority 格式。
    """

    if not tail:
        return None
    priority_count = tail[0]
    if priority_count <= 0:
        return None
    if len(tail) != 1 + priority_count:
        return None
    expected = ceil(job_count / 4)
    if priority_count != expected:
        raise ValueError(
            f"invalid priority job count: expected ceil({job_count}/4)={expected}, "
            f"got={priority_count}"
        )
    priority_job_ids = tuple(tail[1:])
    if any(job_id < 0 or job_id >= job_count for job_id in priority_job_ids):
        raise ValueError(
            f"priority job id out of range [0, {job_count}): {priority_job_ids}"
        )
    if tuple(sorted(priority_job_ids)) != priority_job_ids:
        raise ValueError("priority job ids must be sorted in ascending order")
    if len(set(priority_job_ids)) != len(priority_job_ids):
        raise ValueError("priority job ids must be unique")
    return priority_job_ids


def operation_index_lookup(instance: StandardFjspInstance) -> dict[OpKey, int]:
    """建立 ``(job_id, op_id)`` 到全局工序索引的映射。"""

    return {
        (job.job_id, op.op_id): index
        for index, (job, op) in enumerate(
            (job, op) for job in instance.jobs for op in job.operations
        )
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
    """读取 solver 输出的标准解格式。"""

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
        except Exception as exc:
            raise ValueError(
                f"schedule record {index} is malformed: {item!r}"
            ) from exc
    return parsed


def load_distributed_solution(path: Path) -> list[DistributedScheduleRecord]:
    """读取 DFJSPT solver 输出的扩展解格式。"""

    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("schedule")
    if not isinstance(records, list):
        raise ValueError("solution must contain a list field named 'schedule'")
    parsed: list[DistributedScheduleRecord] = []
    for index, item in enumerate(records):
        try:
            parsed.append(
                DistributedScheduleRecord(
                    job_id=int(item["job_id"]),
                    op_id=int(item["op_id"]),
                    factory_id=int(item["factory_id"]),
                    machine_id=int(item["machine_id"]),
                    start=int(item["start"]),
                    end=int(item["end"]),
                )
            )
        except Exception as exc:
            raise ValueError(
                f"distributed schedule record {index} is malformed: {item!r}"
            ) from exc
    return parsed


def write_solution(
    path: Path,
    instance: StandardFjspInstance,
    schedule: list[ScheduleRecord],
    strategy: str,
) -> None:
    """按固定 JSON 协议输出解。"""

    completion_by_job: dict[int, int] = {}
    for record in schedule:
        completion_by_job[record.job_id] = max(
            completion_by_job.get(record.job_id, 0),
            record.end,
        )
    payload: dict[str, Any] = {
        "format": "standard_fjsp_schedule_v1",
        "variant": (
            "fjsp_machine_availability"
            if instance.has_machine_availability
            else "fjsp_sdst"
            if instance.has_sequence_dependent_setup
            else "fjsp_priority"
            if instance.has_job_priority
            else "standard_fjsp"
        ),
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
    if instance.has_job_priority:
        payload["priority_completion_time"] = max(
            (completion_by_job.get(job_id, 0) for job_id in instance.priority_job_ids),
            default=0,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_standard_schedule(
    instance: StandardFjspInstance,
    schedule: list[ScheduleRecord],
) -> tuple[list[str], dict[str, float]]:
    """验证标准 FJSP/FJSP-SDST/FJSP-NFA 解的结构与时序合法性。

    这是 parser/validator 层的关键边界：它只判断"这份 schedule 是否满足实例 IO
    语义"，并返回基础指标；不负责比较算法优劣，也不决定是否 promotion。
    """

    errors: list[str] = []
    seen: dict[tuple[int, int], ScheduleRecord] = {}

    if len(schedule) != instance.operation_count:
        errors.append(
            f"operation count mismatch: expected={instance.operation_count}, "
            f"got={len(schedule)}"
        )

    candidate_duration: dict[tuple[int, int, int], int] = {}
    expected_ops: set[tuple[int, int]] = set()
    for job in instance.jobs:
        for op in job.operations:
            expected_ops.add((job.job_id, op.op_id))
            for candidate in op.candidates:
                candidate_duration[
                    (job.job_id, op.op_id, candidate.machine_id)
                ] = candidate.duration

    for record in schedule:
        key = (record.job_id, record.op_id)
        if key in seen:
            errors.append(
                f"duplicate operation: job={record.job_id}, op={record.op_id}"
            )
        seen[key] = record
        if key not in expected_ops:
            errors.append(
                f"unknown operation: job={record.job_id}, op={record.op_id}"
            )
        if record.start < 0:
            errors.append(
                f"negative start: job={record.job_id}, op={record.op_id}, "
                f"start={record.start}"
            )
        if record.end < record.start:
            errors.append(
                f"negative interval: job={record.job_id}, op={record.op_id}"
            )
        duration = candidate_duration.get(
            (record.job_id, record.op_id, record.machine_id)
        )
        if duration is None:
            errors.append(
                f"machine is not a candidate: job={record.job_id}, "
                f"op={record.op_id}, machine={record.machine_id}"
            )
        elif record.duration != duration:
            errors.append(
                f"duration mismatch: job={record.job_id}, op={record.op_id}, "
                f"machine={record.machine_id}, expected={duration}, "
                f"got={record.duration}"
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
                    f"precedence violation: job={job.job_id}, "
                    f"op={op_idx} ends at {current.end}, "
                    f"op={op_idx + 1} starts at {nxt.start}"
                )

    by_machine: dict[int, list[ScheduleRecord]] = {}
    for record in schedule:
        by_machine.setdefault(record.machine_id, []).append(record)
    op_index = operation_index_lookup(instance)
    total_setup_time = 0
    setup_count = 0
    for machine_id, records in by_machine.items():
        sorted_records = sorted(
            records,
            key=lambda item: (item.start, item.end, item.job_id, item.op_id),
        )
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
    if instance.has_machine_availability:
        availability_violations = 0
        for interval in instance.unavailability_intervals:
            for record in by_machine.get(interval.machine_id, []):
                overlaps = not (
                    record.start >= interval.end
                    or record.end <= interval.start
                )
                if overlaps:
                    errors.append(
                        f"machine availability violation: "
                        f"machine={interval.machine_id}, "
                        f"op=({record.job_id},{record.op_id}) "
                        f"[{record.start},{record.end}) "
                        f"overlaps unavailable [{interval.start},{interval.end})"
                    )
                    availability_violations += 1
        metrics["machine_availability_violations"] = float(
            availability_violations
        )
        metrics["total_unavailable_duration"] = float(
            sum(
                interval.end - interval.start
                for interval in instance.unavailability_intervals
            )
        )
    if instance.has_job_priority:
        completion_by_job: dict[int, int] = {}
        for record in schedule:
            completion_by_job[record.job_id] = max(
                completion_by_job.get(record.job_id, 0),
                record.end,
            )
        priority_completion_time = max(
            (completion_by_job.get(job_id, 0) for job_id in instance.priority_job_ids),
            default=0,
        )
        metrics["priority_completion_time"] = float(priority_completion_time)
        metrics["priority_job_count"] = float(len(instance.priority_job_ids))
    return errors, metrics


def validate_distributed_schedule(
    instance: DistributedFjspInstance,
    schedule: list[DistributedScheduleRecord],
) -> tuple[list[str], dict[str, float]]:
    """验证 DFJSPT 解的结构、资源选择、机器容量和转移时间合法性。"""

    errors: list[str] = []
    seen: dict[tuple[int, int], DistributedScheduleRecord] = {}

    if len(schedule) != instance.operation_count:
        errors.append(
            f"operation count mismatch: expected={instance.operation_count}, "
            f"got={len(schedule)}"
        )

    expected_ops: set[tuple[int, int]] = set()
    candidate_options: dict[tuple[int, int, int, int], DistributedMachineOption] = {}
    for job in instance.jobs:
        for operation in job.operations:
            expected_ops.add((job.job_id, operation.op_id))
            for candidate in operation.candidates:
                candidate_options[
                    (
                        job.job_id,
                        operation.op_id,
                        candidate.factory_id,
                        candidate.machine_id,
                    )
                ] = candidate

    selected_options: dict[tuple[int, int], DistributedMachineOption] = {}
    for record in schedule:
        key = (record.job_id, record.op_id)
        if key in seen:
            errors.append(
                f"duplicate operation: job={record.job_id}, op={record.op_id}"
            )
        seen[key] = record
        if key not in expected_ops:
            errors.append(
                f"unknown operation: job={record.job_id}, op={record.op_id}"
            )
        if not 0 <= record.factory_id < instance.factory_count:
            errors.append(
                f"factory id out of range: job={record.job_id}, "
                f"op={record.op_id}, factory={record.factory_id}"
            )
        if record.start < 0:
            errors.append(
                f"negative start: job={record.job_id}, op={record.op_id}, "
                f"start={record.start}"
            )
        if record.end < record.start:
            errors.append(
                f"negative interval: job={record.job_id}, op={record.op_id}"
            )

        candidate = candidate_options.get(
            (record.job_id, record.op_id, record.factory_id, record.machine_id)
        )
        if candidate is None:
            errors.append(
                f"factory-machine pair is not a candidate: job={record.job_id}, "
                f"op={record.op_id}, factory={record.factory_id}, "
                f"machine={record.machine_id}"
            )
            continue
        selected_options[key] = candidate
        if record.duration != candidate.duration:
            errors.append(
                f"duration mismatch: job={record.job_id}, op={record.op_id}, "
                f"factory={record.factory_id}, machine={record.machine_id}, "
                f"expected={candidate.duration}, got={record.duration}"
            )

    missing = sorted(expected_ops - set(seen))
    for job_id, op_id in missing:
        errors.append(f"missing operation: job={job_id}, op={op_id}")

    transfer_time_total = 0
    transfer_count = 0
    for job in instance.jobs:
        for op_idx in range(len(job.operations) - 1):
            current = seen.get((job.job_id, op_idx))
            nxt = seen.get((job.job_id, op_idx + 1))
            if current is None or nxt is None:
                continue
            transfer_time = distributed_transfer_time_between(instance, current, nxt)
            transfer_time_total += transfer_time
            if transfer_time:
                transfer_count += 1
            required_start = current.end + transfer_time
            if nxt.start < required_start:
                errors.append(
                    f"transfer/precedence violation: job={job.job_id}, "
                    f"op={op_idx} ends at {current.end}, "
                    f"op={op_idx + 1} starts at {nxt.start}, "
                    f"transfer={transfer_time}, required_start={required_start}"
                )

    by_resource: dict[tuple[int, int], list[DistributedScheduleRecord]] = {}
    for record in schedule:
        by_resource.setdefault((record.factory_id, record.machine_id), []).append(record)
    for (factory_id, machine_id), records in by_resource.items():
        sorted_records = sorted(
            records,
            key=lambda item: (item.start, item.end, item.job_id, item.op_id),
        )
        for left, right in zip(sorted_records, sorted_records[1:]):
            if right.start < left.end:
                errors.append(
                    f"machine overlap violation: factory={factory_id}, "
                    f"machine={machine_id}, "
                    f"left=({left.job_id},{left.op_id},{left.start},{left.end}), "
                    f"right=({right.job_id},{right.op_id},{right.start},{right.end})"
                )

    factory_workloads = [0.0 for _ in range(instance.factory_count)]
    processing_energy = 0.0
    for key, candidate in selected_options.items():
        record = seen.get(key)
        if record is None or record.duration != candidate.duration:
            continue
        factory_workloads[candidate.factory_id] += float(candidate.duration)
        processing_energy += float(candidate.duration * candidate.unit_energy)
    transfer_energy = float(transfer_time_total * instance.transfer_unit_energy)
    makespan = max((record.end for record in schedule), default=0)
    metrics = {
        "makespan": float(makespan),
        "max_factory_workload": float(max(factory_workloads, default=0.0)),
        "total_energy_consumption": processing_energy + transfer_energy,
        "scheduled_operations": float(len(schedule)),
        "operation_count": float(instance.operation_count),
        "transfer_time_total": float(transfer_time_total),
        "transfer_count": float(transfer_count),
    }
    return errors, metrics


def distributed_transfer_time_between(
    instance: DistributedFjspInstance,
    previous: DistributedScheduleRecord,
    current: DistributedScheduleRecord,
) -> int:
    """读取同一 job 相邻工序之间由工厂/机器变化引起的转移时间。"""

    if previous.factory_id != current.factory_id:
        return instance.cross_factory_transfer_time
    if previous.machine_id != current.machine_id:
        return instance.same_factory_transfer_time
    return 0
