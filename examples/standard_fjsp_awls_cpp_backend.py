from __future__ import annotations

"""Python wrapper for the verified C++ GREEDY_INIT AWLS executable.

该脚本用于建立“效果对齐基线”：标准 FJSP 实例仍由 Python harness 读取和校验，
搜索过程交给已复现论文水平的 C++ AWLS 可执行文件。C++ 输出的是机器工序序列，
本脚本再按作业前序约束和机器前序约束拓扑重建每道工序的开始/结束时间。
"""

import argparse
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.standard_fjsp import (
    ScheduleRecord,
    StandardFjspInstance,
    parse_standard_fjsp,
    validate_standard_schedule,
    write_solution,
)


DEFAULT_CPP_EXE = Path(__file__).resolve().parents[1] / "outputs" / "cpp_awls_builds_20260618" / "AWLS_greedy_msvc.exe"


def parse_cpp_machine_sequences(stdout: str, machine_count: int) -> list[list[tuple[int, int]]]:
    """Parse C++ machine-row output into zero-based ``(job_id, op_id)`` sequences."""

    sequences: list[list[tuple[int, int]]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tokens = line.split()
        try:
            count = int(tokens[0])
        except ValueError:
            continue
        if len(tokens) != 1 + count * 2:
            raise ValueError(f"malformed C++ machine row: {raw_line!r}")
        row: list[tuple[int, int]] = []
        for pos in range(count):
            job_id = int(tokens[1 + pos * 2])
            op_id = int(tokens[2 + pos * 2])
            row.append((job_id, op_id))
        sequences.append(row)

    if len(sequences) != machine_count:
        raise ValueError(f"C++ output machine count mismatch: expected={machine_count}, got={len(sequences)}")
    return sequences


def duration_lookup(instance: StandardFjspInstance) -> dict[tuple[int, int, int], int]:
    """Build ``(job, op, machine) -> processing_time`` map used by reconstruction."""

    durations: dict[tuple[int, int, int], int] = {}
    for job in instance.jobs:
        for operation in job.operations:
            for candidate in operation.candidates:
                durations[(job.job_id, operation.op_id, candidate.machine_id)] = candidate.duration
    return durations


def reconstruct_schedule(
    instance: StandardFjspInstance,
    machine_sequences: list[list[tuple[int, int]]],
) -> list[ScheduleRecord]:
    """Rebuild earliest-start schedule from fixed machine sequences.

    The C++ executable prints only machine order.  For a standard FJSP active
    schedule, start times are determined by the longest path over two edge sets:
    job precedence edges and machine-sequence edges.
    """

    durations = duration_lookup(instance)
    op_machine: dict[tuple[int, int], int] = {}
    successors: dict[tuple[int, int], list[tuple[int, int]]] = {}
    indegree: dict[tuple[int, int], int] = {}

    for job in instance.jobs:
        for operation in job.operations:
            key = (job.job_id, operation.op_id)
            successors[key] = []
            indegree[key] = 0

    for job in instance.jobs:
        for op_id in range(len(job.operations) - 1):
            left = (job.job_id, op_id)
            right = (job.job_id, op_id + 1)
            successors[left].append(right)
            indegree[right] += 1

    for machine_id, sequence in enumerate(machine_sequences):
        for key in sequence:
            if key not in indegree:
                raise ValueError(f"C++ output contains unknown operation {key}")
            if key in op_machine:
                raise ValueError(f"C++ output duplicates operation {key}")
            if (key[0], key[1], machine_id) not in durations:
                raise ValueError(f"C++ output assigns operation {key} to non-candidate machine {machine_id}")
            op_machine[key] = machine_id
        for left, right in zip(sequence, sequence[1:]):
            successors[left].append(right)
            indegree[right] += 1

    expected_count = instance.operation_count
    if len(op_machine) != expected_count:
        raise ValueError(f"C++ output operation count mismatch: expected={expected_count}, got={len(op_machine)}")

    ready = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    start_time = {key: 0 for key in indegree}
    end_time: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []

    while ready:
        key = ready.popleft()
        order.append(key)
        machine_id = op_machine[key]
        end_time[key] = start_time[key] + durations[(key[0], key[1], machine_id)]
        for successor in successors[key]:
            start_time[successor] = max(start_time[successor], end_time[key])
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)

    if len(order) != expected_count:
        raise ValueError("C++ machine sequence creates a cycle with job precedence constraints")

    records = [
        ScheduleRecord(
            job_id=job_id,
            op_id=op_id,
            machine_id=op_machine[(job_id, op_id)],
            start=start_time[(job_id, op_id)],
            end=end_time[(job_id, op_id)],
        )
        for job_id, op_id in sorted(op_machine)
    ]
    return sorted(records, key=lambda item: (item.start, item.end, item.machine_id, item.job_id, item.op_id))


def run_cpp_awls(
    exe: Path,
    input_path: Path,
    time_limit_sec: float,
    seed: int,
    best_known: int,
) -> tuple[str, str]:
    """Run the C++ AWLS executable and return ``stdout, stderr``."""

    if not exe.exists():
        raise FileNotFoundError(f"C++ AWLS executable not found: {exe}")
    proc = subprocess.run(
        [str(exe), str(max(1, int(round(time_limit_sec)))), str(seed), str(best_known)],
        input=input_path.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
        timeout=max(5.0, time_limit_sec + 30.0),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"C++ AWLS exited with code {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout, proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description="Run standard FJSP through the verified C++ AWLS backend.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--exe", type=Path, default=DEFAULT_CPP_EXE)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--time-limit-sec", type=float, default=90.0)
    parser.add_argument("--best-known", type=int, default=0)
    args = parser.parse_args()

    start = time.perf_counter()
    instance = parse_standard_fjsp(args.input)
    stdout, stderr = run_cpp_awls(args.exe, args.input, args.time_limit_sec, args.seed, args.best_known)
    machine_sequences = parse_cpp_machine_sequences(stdout, instance.machine_count)
    schedule = reconstruct_schedule(instance, machine_sequences)
    errors, metrics = validate_standard_schedule(instance, schedule)
    if errors:
        for error in errors[:20]:
            print(f"[cpp-backend][error] {error}", file=sys.stderr)
        return 2

    runtime_sec = time.perf_counter() - start
    strategy = (
        "cpp-awls-greedy-backend:"
        f"exe={args.exe}:seed={args.seed}:time_limit={args.time_limit_sec}:"
        f"best_known={args.best_known}:stderr={stderr.strip()!r}"
    )
    write_solution(args.output, instance, schedule, strategy)
    print(
        {
            "instance": instance.name,
            "makespan": int(metrics["makespan"]),
            "scheduled_operations": int(metrics["scheduled_operations"]),
            "runtime_sec": round(runtime_sec, 3),
            "strategy": strategy,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
