"""标准 FJSP 及已确认变体的固定 IO、数据模型和合法性验证。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


OpKey = tuple[int, int]
SetupTimes = tuple[tuple[tuple[int, ...], ...], ...]
MinimumTimeLags = tuple["MinimumTimeLag", ...]
MaximumTimeLags = tuple["MaximumTimeLag", ...]
ReentrantLoops = tuple["ReentrantLoop", ...]
AlternativeRoutes = tuple[tuple[tuple[int, ...], ...], ...]
OperationSetupTimes = tuple[tuple[int, int, int, int], ...]
TransportTimes = tuple[tuple[int, ...], ...]
JobPrecedences = tuple[tuple[int, int], ...]
BatchMachineCapacities = tuple[tuple[int, int], ...]


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
class MaximumTimeLag:
    """Upper bound on waiting from one job operation's end to a later start."""

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
class ReentrantLoop:
    """One contiguous operation segment repeated within a job route."""

    job_id: int
    loop_start: int
    loop_end: int
    repeat: int
    original_operation_count: int

    @property
    def loop_body_size(self) -> int:
        return self.loop_end - self.loop_start + 1

    @property
    def expanded_operation_count(self) -> int:
        return self.original_operation_count + self.loop_body_size * (self.repeat - 1)


@dataclass(frozen=True)
class StandardFjspSolution:
    """Parsed schedule plus variant metadata from the solution document."""

    schedule: list[ScheduleRecord]
    selected_routes: dict[int, int] | None = None


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
    maximum_time_lags: MaximumTimeLags = ()
    job_release_times: tuple[int, ...] = ()
    machine_available_times: tuple[int, ...] = ()
    unavailability_intervals: tuple[MachineUnavailability, ...] = ()
    priority_job_ids: tuple[int, ...] = ()
    reentrant_loops: ReentrantLoops = ()
    alternative_routes: AlternativeRoutes = ()
    operation_setup_times: OperationSetupTimes = ()
    transport_times: TransportTimes = ()
    job_precedences: JobPrecedences = ()
    batch_machine_capacities: BatchMachineCapacities = ()
    job_family_ids: tuple[int, ...] = ()
    job_due_dates: tuple[int, ...] = ()
    source_job_ids: tuple[int, ...] = ()
    machine_cell_ids: tuple[int, ...] = ()
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
    def has_maximum_time_lags(self) -> bool:
        return self.variant == "fjsp_max_time_lag"

    @property
    def has_release_times(self) -> bool:
        return self.variant in {"fjsp_release_time", "fjsp_calendar_reentrant"}

    @property
    def has_machine_availability(self) -> bool:
        return self.variant in {"fjsp_machine_availability", "fjsp_calendar_reentrant"}

    @property
    def has_job_priorities(self) -> bool:
        return self.variant == "fjsp_priority"

    @property
    def has_workload_objectives(self) -> bool:
        return self.variant == "fjsp_multiobjective_workload"

    @property
    def has_reentrant_routes(self) -> bool:
        return self.variant in {
            "fjsp_reentrant",
            "fjsp_calendar_reentrant",
            "fjsp_cell_sdst_transport_tardiness",
        }

    @property
    def has_alternative_routes(self) -> bool:
        return self.variant == "fjsp_alternative_path"

    @property
    def has_operation_setup_times(self) -> bool:
        return bool(self.operation_setup_times)

    @property
    def has_transport_times(self) -> bool:
        return bool(self.transport_times)

    @property
    def has_job_precedences(self) -> bool:
        return bool(self.job_precedences)

    @property
    def has_batch_processing(self) -> bool:
        return self.variant == "fjsp_pbpm"

    @property
    def has_cell_sdst_transport_tardiness(self) -> bool:
        return self.variant == "fjsp_cell_sdst_transport_tardiness"

    def route_options(self, job_id: int) -> tuple[tuple[int, ...], ...]:
        original = tuple(range(len(self.jobs[job_id].operations)))
        alternatives = self.alternative_routes[job_id] if self.alternative_routes else ()
        return (original, *alternatives)

    @property
    def original_operation_count(self) -> int:
        if not self.reentrant_loops:
            return self.operation_count
        return sum(loop.original_operation_count for loop in self.reentrant_loops)


@dataclass(frozen=True)
class ScheduleRecord:
    """调度解中的单条工序排产记录。"""

    job_id: int
    op_id: int
    machine_id: int
    start: int
    end: int
    batch_id: int | None = None

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

    if path.name.casefold().endswith(".calendar_reentrant.json"):
        return _parse_calendar_reentrant_json(path)
    if path.name.casefold().endswith(".jpctst.json"):
        return _parse_jpc_tst_json(path)
    if path.name.casefold().endswith(".fjcs.json"):
        return _parse_cell_sdst_transport_tardiness_json(path)
    if ".pbpm." in path.name.casefold() or path.name.casefold().endswith(".pbpm"):
        return _parse_pbpm_fjsp(path)

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
    alternative_routes: AlternativeRoutes = ()
    if ".apfjsp" in path.name.casefold():
        alternative_routes = _parse_alternative_routes(
            path=path,
            tail=numbers[idx:],
            jobs=tuple(jobs),
        )
        setup_times = ()
        setup_time_kind = "none"
        minimum_time_lags = ()
        maximum_time_lags = ()
        job_release_times = ()
        machine_available_times = ()
        unavailability_intervals = ()
        priority_job_ids = ()
        reentrant_loops = ()
        variant = "fjsp_alternative_path"
    else:
        (
            setup_times,
            setup_time_kind,
            minimum_time_lags,
            maximum_time_lags,
            job_release_times,
            machine_available_times,
            unavailability_intervals,
            priority_job_ids,
            reentrant_loops,
            variant,
        ) = _parse_optional_variant_tail(
            path=path,
            tail=numbers[idx:],
            jobs=tuple(jobs),
            machine_count=machine_count,
            operation_count=operation_count,
        )
    if reentrant_loops:
        jobs = list(_expand_reentrant_jobs(tuple(jobs), reentrant_loops))

    return StandardFjspInstance(
        name=path.stem,
        job_count=job_count,
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
        setup_times=setup_times,
        setup_time_kind=setup_time_kind,
        minimum_time_lags=minimum_time_lags,
        maximum_time_lags=maximum_time_lags,
        job_release_times=job_release_times,
        machine_available_times=machine_available_times,
        unavailability_intervals=unavailability_intervals,
        priority_job_ids=priority_job_ids,
        reentrant_loops=reentrant_loops,
        alternative_routes=alternative_routes,
        variant=variant,
    )


def _parse_calendar_reentrant_json(path: Path) -> StandardFjspInstance:
    """Parse the frozen release/calendar/reentrant compatibility subset."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "fjsp_calendar_reentrant_instance_v1":
        raise ValueError(f"{path} has an unsupported calendar-reentrant format")
    expected_features = {
        "release_time",
        "machine_initial_availability",
        "machine_availability",
        "reentrant_route",
    }
    raw_features = payload.get("active_features")
    if not isinstance(raw_features, list) or set(map(str, raw_features)) != expected_features:
        raise ValueError(
            f"{path} active_features must be exactly {sorted(expected_features)}"
        )

    machine_count = int(payload.get("machine_count", 0))
    raw_jobs = payload.get("jobs")
    if machine_count <= 0 or not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError(f"{path} must declare positive machines and a non-empty jobs array")

    jobs: list[Job] = []
    loops: list[ReentrantLoop] = []
    releases: list[int] = []
    max_candidate_count = 0
    for job_id, raw_job in enumerate(raw_jobs):
        if not isinstance(raw_job, dict):
            raise ValueError(f"{path} job {job_id} must be an object")
        release_time = int(raw_job.get("release_time", -1))
        if release_time < 0:
            raise ValueError(f"{path} job {job_id} has a negative release_time")
        releases.append(release_time)
        raw_operations = raw_job.get("operations")
        if not isinstance(raw_operations, list) or len(raw_operations) < 3:
            raise ValueError(f"{path} job {job_id} must contain at least three operations")
        operations: list[Operation] = []
        for op_id, raw_candidates in enumerate(raw_operations):
            if not isinstance(raw_candidates, list) or not raw_candidates:
                raise ValueError(f"{path} job {job_id} operation {op_id} has no candidates")
            candidates: list[MachineOption] = []
            seen_machines: set[int] = set()
            max_candidate_count = max(max_candidate_count, len(raw_candidates))
            for raw_candidate in raw_candidates:
                if not isinstance(raw_candidate, list) or len(raw_candidate) != 2:
                    raise ValueError(f"{path} has a malformed candidate at job {job_id} op {op_id}")
                machine_id, duration = map(int, raw_candidate)
                if not 0 <= machine_id < machine_count or duration < 0:
                    raise ValueError(f"{path} has an invalid candidate at job {job_id} op {op_id}")
                if machine_id in seen_machines:
                    raise ValueError(f"{path} repeats machine {machine_id} at job {job_id} op {op_id}")
                seen_machines.add(machine_id)
                candidates.append(MachineOption(machine_id=machine_id, duration=duration))
            operations.append(Operation(job_id=job_id, op_id=op_id, candidates=tuple(candidates)))
        jobs.append(Job(job_id=job_id, operations=tuple(operations)))

        raw_loop = raw_job.get("reentrant_loop")
        if not isinstance(raw_loop, dict):
            raise ValueError(f"{path} job {job_id} must declare one reentrant_loop")
        loop_start = int(raw_loop.get("loop_start", -1))
        loop_end = int(raw_loop.get("loop_end", -1))
        repeat = int(raw_loop.get("repeat", 0))
        op_count = len(operations)
        if not (0 < loop_start <= loop_end < op_count - 1) or repeat < 2:
            raise ValueError(f"{path} job {job_id} has an invalid reentrant_loop")
        loops.append(ReentrantLoop(job_id, loop_start, loop_end, repeat, op_count))

    raw_machine_ready = payload.get("machine_initial_availability")
    if not isinstance(raw_machine_ready, list) or len(raw_machine_ready) != machine_count:
        raise ValueError(
            f"{path} machine_initial_availability must contain {machine_count} values"
        )
    machine_ready = tuple(map(int, raw_machine_ready))
    if any(value < 0 for value in machine_ready):
        raise ValueError(f"{path} has a negative machine initial availability")
    if not any(releases) and not any(machine_ready):
        raise ValueError(f"{path} must activate at least one release or initial-availability bound")

    intervals: list[MachineUnavailability] = []
    for index, raw_interval in enumerate(payload.get("unavailability_intervals") or []):
        if not isinstance(raw_interval, list) or len(raw_interval) != 3:
            raise ValueError(f"{path} unavailability interval {index} is malformed")
        machine_id, start, end = map(int, raw_interval)
        if not 0 <= machine_id < machine_count or start < 0 or end <= start:
            raise ValueError(f"{path} unavailability interval {index} is invalid")
        intervals.append(MachineUnavailability(machine_id, start, end))
    if not intervals:
        raise ValueError(f"{path} must declare at least one fixed unavailability interval")

    expanded_jobs = _expand_reentrant_jobs(tuple(jobs), tuple(loops))
    return StandardFjspInstance(
        name=path.name.removesuffix(".calendar_reentrant.json"),
        job_count=len(jobs),
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=expanded_jobs,
        job_release_times=tuple(releases),
        machine_available_times=machine_ready,
        unavailability_intervals=tuple(intervals),
        reentrant_loops=tuple(loops),
        variant="fjsp_calendar_reentrant",
    )


def _parse_jpc_tst_json(path: Path) -> StandardFjspInstance:
    """解析论文 FJSP-JPC-TST 的规范化公开算例。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "fjsp_jpc_tst_instance_v1":
        raise ValueError(f"{path} has an unsupported FJSP-JPC-TST format")
    job_rows = payload.get("jobs")
    if not isinstance(job_rows, list) or not job_rows:
        raise ValueError(f"{path} must contain a non-empty jobs array")
    machine_count = int(payload["machine_count"])
    jobs: list[Job] = []
    setup_rows: list[tuple[int, int, int, int]] = []
    setup_rule = payload.get("operation_setup_rule") or {}
    setup_ratio = float(setup_rule.get("processing_time_ratio", 0))
    setup_minimum = int(setup_rule.get("minimum", 0))
    max_candidate_count = 0
    for job_id, raw_job in enumerate(job_rows):
        raw_ops = raw_job.get("operations") if isinstance(raw_job, dict) else None
        if not isinstance(raw_ops, list) or not raw_ops:
            raise ValueError(f"{path} job {job_id} must contain operations")
        operations: list[Operation] = []
        for op_id, raw_candidates in enumerate(raw_ops):
            if not isinstance(raw_candidates, list) or not raw_candidates:
                raise ValueError(f"{path} job {job_id} operation {op_id} has no candidates")
            candidates: list[MachineOption] = []
            max_candidate_count = max(max_candidate_count, len(raw_candidates))
            for raw_candidate in raw_candidates:
                machine_id = int(raw_candidate[0])
                duration = int(raw_candidate[1])
                if not 0 <= machine_id < machine_count or duration < 0:
                    raise ValueError(
                        f"{path} invalid candidate for job {job_id} operation {op_id}"
                    )
                candidates.append(MachineOption(machine_id=machine_id, duration=duration))
                setup = max(setup_minimum, round(duration * setup_ratio))
                setup_rows.append((job_id, op_id, machine_id, setup))
            operations.append(Operation(job_id=job_id, op_id=op_id, candidates=tuple(candidates)))
        jobs.append(Job(job_id=job_id, operations=tuple(operations)))

    raw_transport = payload.get("transport_times")
    if not isinstance(raw_transport, list) or len(raw_transport) != machine_count:
        raise ValueError(f"{path} transport_times must be a square machine matrix")
    transport_times = tuple(tuple(int(value) for value in row) for row in raw_transport)
    if any(len(row) != machine_count or any(value < 0 for value in row) for row in transport_times):
        raise ValueError(f"{path} transport_times must be square and non-negative")

    precedences: list[tuple[int, int]] = []
    for raw_edge in payload.get("job_precedences") or []:
        predecessor, successor = int(raw_edge[0]), int(raw_edge[1])
        if not 0 <= predecessor < len(jobs) or not 0 <= successor < len(jobs):
            raise ValueError(f"{path} has an out-of-range job precedence edge")
        if predecessor == successor:
            raise ValueError(f"{path} has a self job precedence edge")
        precedences.append((predecessor, successor))

    return StandardFjspInstance(
        name=path.name.removesuffix(".jpctst.json"),
        job_count=len(jobs),
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
        operation_setup_times=tuple(setup_rows),
        transport_times=transport_times,
        job_precedences=tuple(precedences),
        variant="fjsp_jpc_tst",
    )


def _parse_cell_sdst_transport_tardiness_json(path: Path) -> StandardFjspInstance:
    """解析公开 FJCS-SDFSTs-ITTs 算例的规范化整数子集。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "fjsp_cell_sdst_transport_tardiness_instance_v1":
        raise ValueError(f"{path} has an unsupported FJCS-SDFSTs-ITTs format")
    expected_features = {
        "cell_transport",
        "family_sequence_dependent_setup",
        "reentrant_route",
        "due_date",
        "total_tardiness",
    }
    raw_features = payload.get("active_features")
    if not isinstance(raw_features, list) or set(map(str, raw_features)) != expected_features:
        raise ValueError(f"{path} active_features must be exactly {sorted(expected_features)}")

    machine_count = int(payload.get("machine_count", 0))
    machine_cells_raw = payload.get("machine_cell_ids")
    if (
        machine_count <= 0
        or not isinstance(machine_cells_raw, list)
        or len(machine_cells_raw) != machine_count
    ):
        raise ValueError(f"{path} must declare one cell id for every physical machine")
    machine_cell_ids = tuple(map(int, machine_cells_raw))
    cell_count = int(payload.get("cell_count", 0))
    if cell_count <= 0 or any(not 0 <= cell < cell_count for cell in machine_cell_ids):
        raise ValueError(f"{path} has an invalid cell count or machine cell id")

    raw_transport = payload.get("cell_transport_times")
    if not isinstance(raw_transport, list) or len(raw_transport) != cell_count:
        raise ValueError(f"{path} cell_transport_times must be a square cell matrix")
    cell_transport = tuple(tuple(map(int, row)) for row in raw_transport)
    if any(len(row) != cell_count or any(value < 0 for value in row) for row in cell_transport):
        raise ValueError(f"{path} cell_transport_times must be square and non-negative")
    transport_times = tuple(
        tuple(cell_transport[left_cell][right_cell] for right_cell in machine_cell_ids)
        for left_cell in machine_cell_ids
    )

    raw_family_setup = payload.get("family_setup_times")
    family_count = int(payload.get("family_count", 0))
    if not isinstance(raw_family_setup, list) or len(raw_family_setup) != family_count:
        raise ValueError(f"{path} family_setup_times must be a square family matrix")
    family_setup = tuple(tuple(map(int, row)) for row in raw_family_setup)
    if any(len(row) != family_count or any(value < 0 for value in row) for row in family_setup):
        raise ValueError(f"{path} family_setup_times must be square and non-negative")

    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError(f"{path} must contain a non-empty jobs array")
    jobs: list[Job] = []
    family_ids: list[int] = []
    due_dates: list[int] = []
    source_job_ids: list[int] = []
    max_candidate_count = 0
    for job_id, raw_job in enumerate(raw_jobs):
        if not isinstance(raw_job, dict):
            raise ValueError(f"{path} job {job_id} must be an object")
        family_id = int(raw_job.get("family_id", -1))
        due_date = int(raw_job.get("due_date", -1))
        source_job_id = int(raw_job.get("source_job_id", -1))
        if not 0 <= family_id < family_count or due_date < 0 or source_job_id < 0:
            raise ValueError(f"{path} job {job_id} has invalid family, due date, or source id")
        raw_operations = raw_job.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ValueError(f"{path} job {job_id} must contain operations")
        operations: list[Operation] = []
        for op_id, raw_candidates in enumerate(raw_operations):
            if not isinstance(raw_candidates, list) or not raw_candidates:
                raise ValueError(f"{path} job {job_id} operation {op_id} has no candidates")
            candidates: list[MachineOption] = []
            seen_machines: set[int] = set()
            for raw_candidate in raw_candidates:
                if not isinstance(raw_candidate, list) or len(raw_candidate) != 2:
                    raise ValueError(f"{path} malformed candidate at job {job_id} op {op_id}")
                machine_id, duration = map(int, raw_candidate)
                if not 0 <= machine_id < machine_count or duration <= 0:
                    raise ValueError(f"{path} invalid candidate at job {job_id} op {op_id}")
                if machine_id in seen_machines:
                    raise ValueError(f"{path} repeats machine {machine_id} at job {job_id} op {op_id}")
                seen_machines.add(machine_id)
                candidates.append(MachineOption(machine_id=machine_id, duration=duration))
            max_candidate_count = max(max_candidate_count, len(candidates))
            operations.append(Operation(job_id=job_id, op_id=op_id, candidates=tuple(candidates)))
        jobs.append(Job(job_id=job_id, operations=tuple(operations)))
        family_ids.append(family_id)
        due_dates.append(due_date)
        source_job_ids.append(source_job_id)
    if len(set(source_job_ids)) != len(source_job_ids):
        raise ValueError(f"{path} source_job_ids must be unique")

    job_pair_setup = tuple(
        tuple(family_setup[family_ids[left]][family_ids[right]] for right in range(len(jobs)))
        for left in range(len(jobs))
    )
    setup_times = tuple(job_pair_setup for _ in range(machine_count))
    return StandardFjspInstance(
        name=str(payload.get("name") or path.name.removesuffix(".fjcs.json")),
        job_count=len(jobs),
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
        setup_times=setup_times,
        setup_time_kind="job_pair",
        transport_times=transport_times,
        job_family_ids=tuple(family_ids),
        job_due_dates=tuple(due_dates),
        source_job_ids=tuple(source_job_ids),
        machine_cell_ids=machine_cell_ids,
        variant="fjsp_cell_sdst_transport_tardiness",
    )


def _parse_pbpm_fjsp(path: Path) -> StandardFjspInstance:
    """Parse the frozen Fattahi-prefix PBPM-FJSP text contract."""

    numbers = [int(token) for token in path.read_text(encoding="utf-8").split()]
    if len(numbers) < 2:
        raise ValueError(f"{path} is too short to be a PBPM-FJSP instance")
    idx = 0
    job_count, machine_count = numbers[idx : idx + 2]
    idx += 2
    if job_count <= 0 or machine_count <= 0:
        raise ValueError(f"{path} must declare positive job and machine counts")

    jobs: list[Job] = []
    max_candidate_count = 0
    for job_id in range(job_count):
        if idx >= len(numbers):
            raise ValueError(f"{path} ended before job {job_id}")
        operation_count = numbers[idx]
        idx += 1
        if operation_count <= 0:
            raise ValueError(f"{path} job {job_id} has no operations")
        operations: list[Operation] = []
        for op_id in range(operation_count):
            if idx >= len(numbers):
                raise ValueError(f"{path} ended before job {job_id} operation {op_id}")
            candidate_count = numbers[idx]
            idx += 1
            if candidate_count <= 0:
                raise ValueError(f"{path} job {job_id} operation {op_id} has no candidates")
            max_candidate_count = max(max_candidate_count, candidate_count)
            candidates: list[MachineOption] = []
            for _ in range(candidate_count):
                if idx + 1 >= len(numbers):
                    raise ValueError(f"{path} ended inside a candidate list")
                machine_id, duration = numbers[idx : idx + 2]
                idx += 2
                if not 0 <= machine_id < machine_count:
                    raise ValueError(f"{path} has out-of-range machine id {machine_id}")
                if duration < 0:
                    raise ValueError(f"{path} has negative duration {duration}")
                candidates.append(MachineOption(machine_id=machine_id, duration=duration))
            operations.append(Operation(job_id=job_id, op_id=op_id, candidates=tuple(candidates)))
        jobs.append(Job(job_id=job_id, operations=tuple(operations)))

    if idx >= len(numbers):
        raise ValueError(f"{path} is missing the batch-machine tail")
    batch_machine_count = numbers[idx]
    idx += 1
    if batch_machine_count <= 0:
        raise ValueError(f"{path} must declare at least one batch-processing machine")
    batch_capacities: list[tuple[int, int]] = []
    seen_machines: set[int] = set()
    for _ in range(batch_machine_count):
        if idx + 1 >= len(numbers):
            raise ValueError(f"{path} ended inside the batch-machine tail")
        machine_id, capacity = numbers[idx : idx + 2]
        idx += 2
        if not 0 <= machine_id < machine_count:
            raise ValueError(f"{path} has out-of-range batch machine {machine_id}")
        if machine_id in seen_machines:
            raise ValueError(f"{path} repeats batch machine {machine_id}")
        if capacity < 2:
            raise ValueError(f"{path} batch machine {machine_id} has capacity below 2")
        seen_machines.add(machine_id)
        batch_capacities.append((machine_id, capacity))

    if idx >= len(numbers):
        raise ValueError(f"{path} is missing the job-family tail")
    family_count = numbers[idx]
    idx += 1
    if family_count <= 0:
        raise ValueError(f"{path} must declare at least one job family")
    if idx + job_count != len(numbers):
        raise ValueError(
            f"{path} PBPM tail size mismatch: expected {job_count} family ids, "
            f"got {len(numbers) - idx}"
        )
    job_family_ids = tuple(numbers[idx : idx + job_count])
    if any(not 0 <= family_id < family_count for family_id in job_family_ids):
        raise ValueError(f"{path} has an out-of-range job family id")

    return StandardFjspInstance(
        name=path.name.split(".pbpm", 1)[0],
        job_count=job_count,
        machine_count=machine_count,
        max_candidate_count=max_candidate_count,
        jobs=tuple(jobs),
        batch_machine_capacities=tuple(batch_capacities),
        job_family_ids=job_family_ids,
        variant="fjsp_pbpm",
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
    MaximumTimeLags,
    tuple[int, ...],
    tuple[int, ...],
    tuple[MachineUnavailability, ...],
    tuple[int, ...],
    ReentrantLoops,
    str,
]:
    """严格解析已注册的标准 FJSP 变体尾部。

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
    reentrant_name = ".rjsp." in name or name.endswith(".rjsp.txt") or name.endswith(".rjsp")
    workload_objective_name = (
        ".mofjsp." in name or name.endswith(".mofjsp.txt") or name.endswith(".mofjsp")
    )
    if not tail:
        if priority_name:
            raise ValueError(f"{path} priority tail is missing")
        if reentrant_name:
            raise ValueError(f"{path} reentrant loop tail is missing")
        variant = "fjsp_multiobjective_workload" if workload_objective_name else "standard_fjsp"
        return (), "none", (), (), (), (), (), (), (), variant
    machine_availability_name = (
        name.startswith(("ffcr", "nfa", "fjsp_nfa"))
        or ".nfafjsp" in name
        or ".nfa." in name
    )
    if priority_name:
        priority_job_ids = _parse_priority_jobs(path=path, tail=tail, job_count=job_count)
        return (), "none", (), (), (), (), (), priority_job_ids, (), "fjsp_priority"
    if reentrant_name:
        loops = _parse_reentrant_loops(path=path, tail=tail, jobs=jobs)
        return (), "none", (), (), (), (), (), (), loops, "fjsp_reentrant"
    if ".mitfjsp" in name:
        return (), "none", _parse_minimum_time_lags(path=path, tail=tail, jobs=jobs), (), (), (), (), (), (), "fjsp_min_time_lag"
    if ".tlfjsp" in name:
        return (), "none", (), _parse_maximum_time_lags(path=path, tail=tail, jobs=jobs), (), (), (), (), (), "fjsp_max_time_lag"
    if ".rtfjsp" in name:
        job_release, machine_available = _parse_release_times(
            path=path,
            tail=tail,
            job_count=job_count,
            machine_count=machine_count,
        )
        return (), "none", (), (), job_release, machine_available, (), (), (), "fjsp_release_time"
    if machine_availability_name:
        intervals = _parse_machine_unavailability(path=path, tail=tail, machine_count=machine_count)
        return (), "none", (), (), (), (), intervals, (), (), "fjsp_machine_availability"
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
            return (), "none", constraints, (), (), (), (), (), (), "fjsp_min_time_lag"
        if availability_match:
            intervals = _parse_machine_unavailability(path=path, tail=tail, machine_count=machine_count)
            return (), "none", (), (), (), (), intervals, (), (), "fjsp_machine_availability"
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
    return tuple(setup_by_machine), kind, (), (), (), (), (), (), (), "fjsp_sdst"


def _parse_reentrant_loops(
    *, path: Path, tail: list[int], jobs: tuple[Job, ...]
) -> ReentrantLoops:
    expected = 3 * len(jobs)
    if len(tail) != expected:
        raise ValueError(
            f"{path} reentrant tail must contain exactly {expected} integers, got {len(tail)}"
        )
    loops: list[ReentrantLoop] = []
    for job_id, job in enumerate(jobs):
        loop_start, loop_end, repeat = tail[3 * job_id : 3 * job_id + 3]
        op_count = len(job.operations)
        if not 0 < loop_start < op_count - 1:
            raise ValueError(
                f"{path} job {job_id} loop_start must satisfy 0 < start < {op_count - 1}, "
                f"got {loop_start}"
            )
        if not loop_start <= loop_end < op_count - 1:
            raise ValueError(
                f"{path} job {job_id} loop_end must satisfy {loop_start} <= end < "
                f"{op_count - 1}, got {loop_end}"
            )
        if repeat < 2:
            raise ValueError(f"{path} job {job_id} repeat must be at least 2, got {repeat}")
        loops.append(
            ReentrantLoop(
                job_id=job_id,
                loop_start=loop_start,
                loop_end=loop_end,
                repeat=repeat,
                original_operation_count=op_count,
            )
        )
    return tuple(loops)


def _parse_alternative_routes(
    *,
    path: Path,
    tail: list[int],
    jobs: tuple[Job, ...],
) -> AlternativeRoutes:
    """Parse per-job alternative operation sequences after the standard body."""

    if not tail:
        raise ValueError(f"{path} alternative-route tail is missing")
    cursor = 0
    all_alternatives: list[tuple[tuple[int, ...], ...]] = []
    for job in jobs:
        if cursor >= len(tail):
            raise ValueError(f"{path} is missing alternative-route count for job {job.job_id}")
        alternative_count = tail[cursor]
        cursor += 1
        if alternative_count < 0:
            raise ValueError(f"{path} job {job.job_id} has negative alternative-route count")
        routes: list[tuple[int, ...]] = []
        seen_routes = {tuple(range(len(job.operations)))}
        for route_index in range(alternative_count):
            if cursor >= len(tail):
                raise ValueError(f"{path} job {job.job_id} route {route_index + 1} is missing")
            operation_count = tail[cursor]
            cursor += 1
            if not 2 <= operation_count <= len(job.operations):
                raise ValueError(
                    f"{path} job {job.job_id} route {route_index + 1} has invalid operation_count={operation_count}"
                )
            if cursor + operation_count > len(tail):
                raise ValueError(f"{path} ended inside job {job.job_id} route {route_index + 1}")
            route = tuple(tail[cursor : cursor + operation_count])
            cursor += operation_count
            if len(set(route)) != len(route):
                raise ValueError(f"{path} job {job.job_id} route {route_index + 1} repeats an operation")
            invalid = [op_id for op_id in route if not 0 <= op_id < len(job.operations)]
            if invalid:
                raise ValueError(
                    f"{path} job {job.job_id} route {route_index + 1} has out-of-range operation ids {invalid}"
                )
            if route in seen_routes:
                raise ValueError(f"{path} job {job.job_id} has duplicate route {route}")
            seen_routes.add(route)
            routes.append(route)
        all_alternatives.append(tuple(routes))
    if cursor != len(tail):
        raise ValueError(f"{path} alternative-route tail has {len(tail) - cursor} trailing tokens")
    return tuple(all_alternatives)


def _expand_reentrant_jobs(jobs: tuple[Job, ...], loops: ReentrantLoops) -> tuple[Job, ...]:
    loop_by_job = {loop.job_id: loop for loop in loops}
    expanded_jobs: list[Job] = []
    for job in jobs:
        loop = loop_by_job[job.job_id]
        body = job.operations[loop.loop_start : loop.loop_end + 1]
        route = (
            list(job.operations[: loop.loop_start])
            + list(body) * loop.repeat
            + list(job.operations[loop.loop_end + 1 :])
        )
        operations = tuple(
            Operation(job_id=job.job_id, op_id=op_id, candidates=source.candidates)
            for op_id, source in enumerate(route)
        )
        expanded_jobs.append(Job(job_id=job.job_id, operations=operations))
    return tuple(expanded_jobs)


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


def _parse_maximum_time_lags(
    *,
    path: Path,
    tail: list[int],
    jobs: tuple[Job, ...],
) -> MaximumTimeLags:
    """Parse sparse same-job upper bounds, including non-adjacent operation pairs."""

    constraint_count = tail[0]
    if constraint_count < 0 or len(tail) != 1 + 4 * constraint_count:
        raise ValueError(f"{path} has an invalid maximum-time-lag tail")
    constraints: list[MaximumTimeLag] = []
    seen: set[tuple[int, int, int]] = set()
    for index in range(constraint_count):
        offset = 1 + index * 4
        job_id, from_op, to_op, lag = tail[offset : offset + 4]
        if not 0 <= job_id < len(jobs):
            raise ValueError(f"{path} maximum-time-lag {index} has out-of-range job_id={job_id}")
        operation_count = len(jobs[job_id].operations)
        if not (0 <= from_op < to_op < operation_count):
            raise ValueError(
                f"{path} maximum-time-lag {index} has invalid ordered operation pair "
                f"job={job_id}, from_op={from_op}, to_op={to_op}"
            )
        if lag < 0:
            raise ValueError(f"{path} maximum-time-lag {index} has negative lag={lag}")
        key = (job_id, from_op, to_op)
        if key in seen:
            raise ValueError(f"{path} has duplicate maximum-time-lag constraint for {key}")
        seen.add(key)
        constraints.append(MaximumTimeLag(job_id, from_op, to_op, lag))
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

    return load_solution_document(path).schedule


def load_solution_document(path: Path) -> StandardFjspSolution:
    """Load the schedule and optional route-choice metadata."""

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
                    batch_id=int(item["batch_id"]) if item.get("batch_id") is not None else None,
                )
            )
        except Exception as exc:  # noqa: BLE001 - convert malformed records into validation errors.
            raise ValueError(f"schedule record {index} is malformed: {item!r}") from exc
    raw_routes = raw.get("selected_routes")
    selected_routes: dict[int, int] | None = None
    if raw_routes is not None:
        if not isinstance(raw_routes, dict):
            raise ValueError("solution field 'selected_routes' must be an object")
        selected_routes = {}
        for raw_job_id, raw_route_id in raw_routes.items():
            if isinstance(raw_job_id, bool) or isinstance(raw_route_id, bool):
                raise ValueError("selected_routes job and route ids must be integers")
            if not isinstance(raw_route_id, int):
                raise ValueError("selected_routes route ids must be JSON integers")
            try:
                job_id = int(raw_job_id)
                route_id = int(raw_route_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("selected_routes job and route ids must be integers") from exc
            if str(job_id) != str(raw_job_id).strip() and not isinstance(raw_job_id, int):
                raise ValueError(f"selected_routes has non-canonical job id {raw_job_id!r}")
            if job_id in selected_routes:
                raise ValueError(f"selected_routes repeats job id {job_id}")
            selected_routes[job_id] = route_id
    return StandardFjspSolution(schedule=parsed, selected_routes=selected_routes)


def _solution_field(item: dict[str, Any], canonical: str, alias: str) -> Any:
    """Accept the fixed schema and the audited FJSPSolutionV1 field names."""

    if canonical in item:
        return item[canonical]
    return item[alias]


def write_solution(
    path: Path,
    instance: StandardFjspInstance,
    schedule: list[ScheduleRecord],
    strategy: str,
    *,
    selected_routes: dict[int, int] | None = None,
) -> None:
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
                **({"batch_id": record.batch_id} if record.batch_id is not None else {}),
            }
            for record in schedule
        ],
    }
    if instance.has_minimum_time_lags:
        payload["min_time_lag_policy"] = "checked_by_evaluator"
    if instance.has_maximum_time_lags:
        payload["max_time_lag_policy"] = "checked_by_evaluator"
    if instance.has_alternative_routes:
        choices = selected_routes if selected_routes is not None else {job.job_id: 0 for job in instance.jobs}
        payload["selected_routes"] = {str(job_id): route_id for job_id, route_id in sorted(choices.items())}
        payload["alternative_path_policy"] = "selected_route_checked_by_evaluator"
    if instance.has_release_times:
        payload["release_time_policy"] = "checked_by_evaluator"
    if instance.has_machine_availability:
        payload["machine_availability_policy"] = "checked_by_evaluator"
    if instance.has_reentrant_routes:
        payload["reentrant_policy"] = "expanded_route_checked_by_evaluator"
    if instance.has_batch_processing:
        batch_machine_ids = {machine_id for machine_id, _ in instance.batch_machine_capacities}
        payload["batch_count"] = len(
            {
                (record.machine_id, record.batch_id)
                for record in schedule
                if record.machine_id in batch_machine_ids and record.batch_id is not None
            }
        )
        payload["batch_processing_policy"] = "capacity_family_and_max_duration_checked_by_evaluator"
    if instance.has_job_priorities:
        completion_times = [
            record.end
            for record in schedule
            if record.job_id in instance.priority_job_ids
            and record.op_id == len(instance.jobs[record.job_id].operations) - 1
        ]
        payload["priority_completion_time"] = max(completion_times, default=0)
        payload["priority_policy"] = "lexicographic_after_makespan"
    if instance.has_workload_objectives:
        machine_workloads = [0 for _ in range(instance.machine_count)]
        for record in schedule:
            machine_workloads[record.machine_id] += record.duration
        payload["max_machine_workload"] = max(machine_workloads, default=0)
        payload["total_workload"] = sum(machine_workloads)
        payload["workload_objective_policy"] = "lexicographic_after_makespan"
    if instance.has_cell_sdst_transport_tardiness:
        completion_times = [
            max((record.end for record in schedule if record.job_id == job_id), default=0)
            for job_id in range(instance.job_count)
        ]
        payload["total_tardiness"] = sum(
            max(0, completion - instance.job_due_dates[job_id])
            for job_id, completion in enumerate(completion_times)
        )
        payload["cell_objective_policy"] = "lexicographic_after_makespan"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_standard_schedule(
    instance: StandardFjspInstance,
    schedule: list[ScheduleRecord],
    *,
    selected_routes: dict[int, int] | None = None,
) -> tuple[list[str], dict[str, float]]:
    """验证标准 FJSP 及共用 schedule schema 的已注册变体解。

    这是 parser/validator 层的关键边界：它只判断“这份 schedule 是否满足实例 IO
    语义”，并返回基础指标；不负责比较算法优劣，也不决定是否 promotion。
    """

    errors: list[str] = []
    seen: dict[tuple[int, int], ScheduleRecord] = {}

    candidate_duration: dict[tuple[int, int, int], int] = {}
    batch_capacities = dict(instance.batch_machine_capacities)
    all_ops: set[tuple[int, int]] = set()
    for job in instance.jobs:
        for op in job.operations:
            all_ops.add((job.job_id, op.op_id))
            for candidate in op.candidates:
                candidate_duration[(job.job_id, op.op_id, candidate.machine_id)] = candidate.duration

    selected_sequences: dict[int, tuple[int, ...]] = {
        job.job_id: tuple(range(len(job.operations))) for job in instance.jobs
    }
    if instance.has_alternative_routes:
        choices = selected_routes
        if choices is None:
            errors.append("solution must contain selected_routes for every job")
            choices = {}
        unknown_jobs = sorted(set(choices) - set(range(instance.job_count)))
        for job_id in unknown_jobs:
            errors.append(f"selected_routes contains unknown job={job_id}")
        for job in instance.jobs:
            if job.job_id not in choices:
                errors.append(f"selected_routes is missing job={job.job_id}")
                continue
            route_id = choices[job.job_id]
            options = instance.route_options(job.job_id)
            if not 0 <= route_id < len(options):
                errors.append(
                    f"selected route is out of range: job={job.job_id}, route={route_id}, "
                    f"available=0..{len(options) - 1}"
                )
                continue
            selected_sequences[job.job_id] = options[route_id]

    expected_ops = {
        (job_id, op_id)
        for job_id, sequence in selected_sequences.items()
        for op_id in sequence
    }
    if len(schedule) != len(expected_ops):
        errors.append(f"operation count mismatch: expected={len(expected_ops)}, got={len(schedule)}")

    for record in schedule:
        key = (record.job_id, record.op_id)
        if key in seen:
            errors.append(f"duplicate operation: job={record.job_id}, op={record.op_id}")
        seen[key] = record
        if key not in all_ops:
            errors.append(f"unknown operation: job={record.job_id}, op={record.op_id}")
        elif key not in expected_ops:
            errors.append(f"operation is not on selected route: job={record.job_id}, op={record.op_id}")
        if record.start < 0:
            errors.append(f"negative start: job={record.job_id}, op={record.op_id}, start={record.start}")
        if record.end < record.start:
            errors.append(f"negative interval: job={record.job_id}, op={record.op_id}")
        duration = candidate_duration.get((record.job_id, record.op_id, record.machine_id))
        if duration is None:
            errors.append(
                f"machine is not a candidate: job={record.job_id}, op={record.op_id}, machine={record.machine_id}"
            )
        elif record.machine_id not in batch_capacities and record.duration != duration:
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

    transport_violations = 0
    for job in instance.jobs:
        route = selected_sequences[job.job_id]
        for from_op, to_op in zip(route, route[1:]):
            current = seen.get((job.job_id, from_op))
            nxt = seen.get((job.job_id, to_op))
            transport = (
                instance.transport_times[current.machine_id][nxt.machine_id]
                if current and nxt and instance.has_transport_times
                else 0
            )
            if current and nxt and nxt.start < current.end + transport:
                if transport:
                    transport_violations += 1
                errors.append(
                    f"precedence violation: job={job.job_id}, op={from_op} ends at {current.end}, "
                    f"op={to_op} starts at {nxt.start}, transport={transport}"
                )

    job_precedence_violations = 0
    for predecessor, successor in instance.job_precedences:
        predecessor_record = seen.get((predecessor, len(instance.jobs[predecessor].operations) - 1))
        successor_record = seen.get((successor, 0))
        if predecessor_record is None or successor_record is None:
            continue
        transport = instance.transport_times[predecessor_record.machine_id][successor_record.machine_id]
        required_start = predecessor_record.end + transport
        if successor_record.start < required_start:
            job_precedence_violations += 1
            if transport:
                transport_violations += 1
            errors.append(
                f"job precedence/transport violation: predecessor={predecessor}, "
                f"successor={successor}, required_start={required_start}, "
                f"actual_start={successor_record.start}, transport={transport}"
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

    max_time_lag_violations = 0
    for constraint in instance.maximum_time_lags:
        previous = seen.get((constraint.job_id, constraint.from_op))
        successor = seen.get((constraint.job_id, constraint.to_op))
        if previous is None or successor is None:
            continue
        actual_gap = successor.start - previous.end
        if actual_gap > constraint.lag:
            max_time_lag_violations += 1
            errors.append(
                f"maximum time-lag violation: job={constraint.job_id}, "
                f"from_op={constraint.from_op}, to_op={constraint.to_op}, "
                f"maximum_gap={constraint.lag}, actual_gap={actual_gap}"
            )

    by_machine: dict[int, list[ScheduleRecord]] = {}
    for record in schedule:
        by_machine.setdefault(record.machine_id, []).append(record)
    batch_count = 0
    grouped_batch_count = 0
    family_violations = 0
    batch_capacity_violations = 0
    batch_synchronization_violations = 0
    batch_duration_violations = 0
    machine_activities = {machine_id: list(records) for machine_id, records in by_machine.items()}
    if instance.has_batch_processing:
        for machine_id, capacity in batch_capacities.items():
            groups: dict[int, list[ScheduleRecord]] = {}
            ungrouped: list[ScheduleRecord] = []
            for record in by_machine.get(machine_id, []):
                if record.batch_id is None:
                    errors.append(
                        f"batch id is required on batch machine: machine={machine_id}, "
                        f"job={record.job_id}, op={record.op_id}"
                    )
                    ungrouped.append(record)
                    continue
                if record.batch_id < 0:
                    errors.append(
                        f"negative batch id: machine={machine_id}, batch={record.batch_id}"
                    )
                groups.setdefault(record.batch_id, []).append(record)

            activities = list(ungrouped)
            for batch_id, members in groups.items():
                batch_count += 1
                grouped_batch_count += int(len(members) > 1)
                starts = {record.start for record in members}
                ends = {record.end for record in members}
                if len(starts) != 1 or len(ends) != 1:
                    batch_synchronization_violations += 1
                    errors.append(
                        f"batch synchronization violation: machine={machine_id}, batch={batch_id}"
                    )
                if len(members) > capacity:
                    batch_capacity_violations += 1
                    errors.append(
                        f"batch capacity violation: machine={machine_id}, batch={batch_id}, "
                        f"capacity={capacity}, members={len(members)}"
                    )
                families = {
                    instance.job_family_ids[record.job_id]
                    for record in members
                    if 0 <= record.job_id < len(instance.job_family_ids)
                }
                if len(families) > 1:
                    family_violations += 1
                    errors.append(
                        f"batch family violation: machine={machine_id}, batch={batch_id}, "
                        f"families={sorted(families)}"
                    )
                if len({record.job_id for record in members}) != len(members):
                    errors.append(
                        f"batch repeats a job: machine={machine_id}, batch={batch_id}"
                    )
                processing_times = [
                    candidate_duration[(record.job_id, record.op_id, machine_id)]
                    for record in members
                    if (record.job_id, record.op_id, machine_id) in candidate_duration
                ]
                expected_duration = max(processing_times, default=0)
                actual_durations = {record.duration for record in members}
                if actual_durations != {expected_duration}:
                    batch_duration_violations += 1
                    errors.append(
                        f"batch duration violation: machine={machine_id}, batch={batch_id}, "
                        f"expected={expected_duration}, got={sorted(actual_durations)}"
                    )
                representative = members[0]
                activities.append(
                    ScheduleRecord(
                        job_id=representative.job_id,
                        op_id=representative.op_id,
                        machine_id=machine_id,
                        start=min(record.start for record in members),
                        end=max(record.end for record in members),
                        batch_id=batch_id,
                    )
                )
            machine_activities[machine_id] = activities
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
    operation_setup = {
        (job_id, op_id, machine_id): setup
        for job_id, op_id, machine_id, setup in instance.operation_setup_times
    }
    total_setup_time = 0
    setup_count = 0
    for machine_id, records in machine_activities.items():
        sorted_records = sorted(records, key=lambda item: (item.start, item.end, item.job_id, item.op_id))
        if sorted_records and instance.has_operation_setup_times:
            first = sorted_records[0]
            first_setup = operation_setup.get((first.job_id, first.op_id, machine_id), 0)
            total_setup_time += first_setup
            setup_count += int(first_setup > 0)
            if first.start < first_setup:
                errors.append(
                    f"operation setup violation: machine={machine_id}, job={first.job_id}, "
                    f"op={first.op_id}, setup={first_setup}, start={first.start}"
                )
        for left, right in zip(sorted_records, sorted_records[1:]):
            setup_time = (
                operation_setup.get((right.job_id, right.op_id, machine_id), 0)
                if instance.has_operation_setup_times
                else setup_time_between(
                    instance,
                    machine_id,
                    (left.job_id, left.op_id),
                    (right.job_id, right.op_id),
                    op_index,
                )
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
        "operation_count": float(len(expected_ops)),
    }
    if instance.has_alternative_routes:
        metrics["operation_pool_count"] = float(instance.operation_count)
        metrics["selected_alternative_route_count"] = float(
            sum(1 for route_id in (selected_routes or {}).values() if route_id > 0)
        )
    if instance.has_reentrant_routes:
        metrics["original_operation_count"] = float(instance.original_operation_count)
        metrics["reentrant_added_operation_count"] = float(
            instance.operation_count - instance.original_operation_count
        )
    if instance.has_sequence_dependent_setup:
        metrics["setup_time"] = float(total_setup_time)
        metrics["setup_count"] = float(setup_count)
    if instance.has_operation_setup_times:
        metrics["operation_setup_time"] = float(total_setup_time)
        metrics["operation_setup_count"] = float(setup_count)
    if instance.has_transport_times:
        metrics["transport_violations"] = float(transport_violations)
    if instance.has_job_precedences:
        metrics["job_precedence_constraints"] = float(len(instance.job_precedences))
        metrics["job_precedence_violations"] = float(job_precedence_violations)
    if instance.has_minimum_time_lags:
        metrics["min_time_lag_constraints"] = float(len(instance.minimum_time_lags))
        metrics["min_time_lag_violations"] = float(min_time_lag_violations)
    if instance.has_maximum_time_lags:
        metrics["max_time_lag_constraints"] = float(len(instance.maximum_time_lags))
        metrics["max_time_lag_violations"] = float(max_time_lag_violations)
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
    if instance.has_workload_objectives:
        machine_workloads = [0 for _ in range(instance.machine_count)]
        for record in schedule:
            if 0 <= record.machine_id < instance.machine_count:
                machine_workloads[record.machine_id] += record.duration
        metrics["max_machine_workload"] = float(max(machine_workloads, default=0))
        metrics["total_workload"] = float(sum(machine_workloads))
    if instance.has_cell_sdst_transport_tardiness:
        completion_times = [
            max((record.end for record in schedule if record.job_id == job_id), default=0)
            for job_id in range(instance.job_count)
        ]
        total_tardiness = sum(
            max(0, completion - instance.job_due_dates[job_id])
            for job_id, completion in enumerate(completion_times)
        )
        metrics["total_tardiness"] = float(total_tardiness)
        metrics["tardy_job_count"] = float(
            sum(
                completion > instance.job_due_dates[job_id]
                for job_id, completion in enumerate(completion_times)
            )
        )
        metrics["cell_count"] = float(len(set(instance.machine_cell_ids)))
    if instance.has_batch_processing:
        metrics["batch_count"] = float(batch_count)
        metrics["grouped_batch_count"] = float(grouped_batch_count)
        metrics["batch_machine_count"] = float(len(batch_capacities))
        metrics["family_violations"] = float(family_violations)
        metrics["batch_capacity_violations"] = float(batch_capacity_violations)
        metrics["batch_synchronization_violations"] = float(batch_synchronization_violations)
        metrics["batch_duration_violations"] = float(batch_duration_violations)
    return errors, metrics
