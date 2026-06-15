from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.standard_fjsp import ScheduleRecord, parse_standard_fjsp, write_solution


def build_ect_schedule(instance_path: Path, seed: int) -> tuple[object, list[ScheduleRecord]]:
    instance = parse_standard_fjsp(instance_path)
    rng = random.Random(seed)
    next_op = [0 for _ in instance.jobs]
    job_ready = [0 for _ in instance.jobs]
    machine_ready = [0 for _ in range(instance.machine_count)]
    schedule: list[ScheduleRecord] = []

    for _ in range(instance.operation_count):
        best_key: tuple[int, int, int, float] | None = None
        best_record: ScheduleRecord | None = None
        for job in instance.jobs:
            op_id = next_op[job.job_id]
            if op_id >= len(job.operations):
                continue
            operation = job.operations[op_id]
            for candidate in operation.candidates:
                start = max(job_ready[job.job_id], machine_ready[candidate.machine_id])
                end = start + candidate.duration
                # Earliest completion first, then shorter processing time, with a tiny seed tie breaker.
                key = (end, candidate.duration, start, rng.random())
                if best_key is None or key < best_key:
                    best_key = key
                    best_record = ScheduleRecord(
                        job_id=job.job_id,
                        op_id=op_id,
                        machine_id=candidate.machine_id,
                        start=start,
                        end=end,
                    )
        if best_record is None:
            raise RuntimeError("no schedulable operation found")
        schedule.append(best_record)
        next_op[best_record.job_id] += 1
        job_ready[best_record.job_id] = best_record.end
        machine_ready[best_record.machine_id] = best_record.end

    return instance, schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Simple ECT baseline solver for standard FJSP instances.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    instance, schedule = build_ect_schedule(args.input, args.seed)
    write_solution(args.output, instance, schedule, strategy="ect_baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
