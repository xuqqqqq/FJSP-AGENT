from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import parse_standard_fjsp, validate_standard_schedule, ScheduleRecord


def solve(instance_path: Path, *, time_limit: float, workers: int) -> tuple[str, list[ScheduleRecord], float]:
    from ortools.sat.python import cp_model

    instance = parse_standard_fjsp(instance_path)
    if instance.variant != "fjsp_jpc_tst":
        raise ValueError("benchmark requires an fjsp_jpc_tst instance")
    setup = {
        (job_id, op_id, machine_id): value
        for job_id, op_id, machine_id, value in instance.operation_setup_times
    }
    horizon = sum(
        max(candidate.duration + setup[(op.job_id, op.op_id, candidate.machine_id)] for candidate in op.candidates)
        for job in instance.jobs
        for op in job.operations
    ) + instance.operation_count * max(max(row) for row in instance.transport_times)
    model = cp_model.CpModel()
    chosen: dict[tuple[int, int, int], object] = {}
    starts: dict[tuple[int, int], object] = {}
    ends: dict[tuple[int, int], object] = {}
    machine_intervals: dict[int, list[object]] = {m: [] for m in range(instance.machine_count)}
    for job in instance.jobs:
        for op in job.operations:
            key = (job.job_id, op.op_id)
            starts[key] = model.new_int_var(0, horizon, f"start_{key[0]}_{key[1]}")
            ends[key] = model.new_int_var(0, horizon, f"end_{key[0]}_{key[1]}")
            presences = []
            for candidate in op.candidates:
                m = candidate.machine_id
                present = model.new_bool_var(f"x_{key[0]}_{key[1]}_{m}")
                chosen[(key[0], key[1], m)] = present
                occupied_start = model.new_int_var(0, horizon, f"occ_{key[0]}_{key[1]}_{m}")
                occupied_duration = setup[(key[0], key[1], m)] + candidate.duration
                interval = model.new_optional_fixed_size_interval_var(
                    occupied_start, occupied_duration, present, f"i_{key[0]}_{key[1]}_{m}"
                )
                machine_intervals[m].append(interval)
                model.add(starts[key] == occupied_start + setup[(key[0], key[1], m)]).only_enforce_if(present)
                model.add(ends[key] == starts[key] + candidate.duration).only_enforce_if(present)
                presences.append(present)
            model.add_exactly_one(presences)
    for intervals in machine_intervals.values():
        model.add_no_overlap(intervals)

    def add_transport_arc(left: tuple[int, int], right: tuple[int, int]) -> None:
        left_op = instance.jobs[left[0]].operations[left[1]]
        right_op = instance.jobs[right[0]].operations[right[1]]
        for left_candidate in left_op.candidates:
            for right_candidate in right_op.candidates:
                model.add(
                    starts[right]
                    >= ends[left]
                    + instance.transport_times[left_candidate.machine_id][right_candidate.machine_id]
                ).only_enforce_if(
                    chosen[(left[0], left[1], left_candidate.machine_id)],
                    chosen[(right[0], right[1], right_candidate.machine_id)],
                )

    for job in instance.jobs:
        for op_id in range(len(job.operations) - 1):
            add_transport_arc((job.job_id, op_id), (job.job_id, op_id + 1))
    for predecessor, successor in instance.job_precedences:
        add_transport_arc(
            (predecessor, len(instance.jobs[predecessor].operations) - 1),
            (successor, 0),
        )

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, [ends[(job.job_id, len(job.operations) - 1)] for job in instance.jobs])
    model.minimize(makespan)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.log_search_progress = False
    status_code = solver.solve(model)
    status = solver.status_name(status_code)
    if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return status, [], float("inf")
    schedule: list[ScheduleRecord] = []
    for job in instance.jobs:
        for op in job.operations:
            machine_id = next(
                candidate.machine_id
                for candidate in op.candidates
                if solver.boolean_value(chosen[(job.job_id, op.op_id, candidate.machine_id)])
            )
            schedule.append(
                ScheduleRecord(
                    job_id=job.job_id,
                    op_id=op.op_id,
                    machine_id=machine_id,
                    start=solver.value(starts[(job.job_id, op.op_id)]),
                    end=solver.value(ends[(job.job_id, op.op_id)]),
                )
            )
    errors, metrics = validate_standard_schedule(instance, schedule)
    if errors:
        raise RuntimeError("CP-SAT extraction failed evaluator: " + "; ".join(errors[:5]))
    return status, schedule, metrics["makespan"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", type=Path, default=Path("examples/fjsp_jpc_tst_T01.jpctst.json"))
    parser.add_argument("--time-limit", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--solution", type=Path)
    args = parser.parse_args()
    status, schedule, makespan = solve(args.instance, time_limit=args.time_limit, workers=args.workers)
    payload = {
        "status": status,
        "makespan": makespan,
        "schedule": [record.__dict__ for record in schedule],
        "ortools_ran": True,
        "num_search_workers": args.workers,
    }
    if args.solution:
        args.solution.parent.mkdir(parents=True, exist_ok=True)
        args.solution.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "schedule"}, indent=2))
    return 0 if schedule else 1


if __name__ == "__main__":
    raise SystemExit(main())
