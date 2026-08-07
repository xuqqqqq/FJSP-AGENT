#!/usr/bin/env python3
"""Independent CP-SAT benchmark for minimum-time-lag FJSP instances."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ortools.sat.python import cp_model


def parse_instance(path: Path) -> dict:
    tokens = [int(token) for token in path.read_text(encoding="utf-8").split()]
    cursor = 0

    def take() -> int:
        nonlocal cursor
        value = tokens[cursor]
        cursor += 1
        return value

    num_jobs, num_machines, max_candidates = take(), take(), take()
    jobs = []
    for _ in range(num_jobs):
        operations = []
        for _ in range(take()):
            alternatives = []
            for _ in range(take()):
                alternatives.append((take(), take()))
            operations.append(alternatives)
        jobs.append(operations)

    lag_count = take() if cursor < len(tokens) else 0
    lags = {}
    for _ in range(lag_count):
        job, source, target, lag = take(), take(), take(), take()
        if target != source + 1:
            raise ValueError(f"non-adjacent lag: {(job, source, target, lag)}")
        lags[(job, source, target)] = lag
    if cursor != len(tokens):
        raise ValueError(f"unexpected trailing tokens: {len(tokens) - cursor}")

    return {
        "num_jobs": num_jobs,
        "num_machines": num_machines,
        "max_candidates": max_candidates,
        "jobs": jobs,
        "lags": lags,
    }


def solve(problem: dict, time_limit: float, workers: int, upper_bound: int | None) -> tuple[dict, dict]:
    model = cp_model.CpModel()
    jobs = problem["jobs"]
    lags = problem["lags"]
    operation_count = sum(len(job) for job in jobs)
    horizon = sum(max(duration for _, duration in op) for job in jobs for op in job)
    horizon += sum(lags.values())

    starts = {}
    ends = {}
    choices = {}
    machine_intervals = [[] for _ in range(problem["num_machines"])]

    for job_id, job in enumerate(jobs):
        for op_id, alternatives in enumerate(job):
            key = (job_id, op_id)
            start = model.new_int_var(0, horizon, f"s_{job_id}_{op_id}")
            end = model.new_int_var(0, horizon, f"e_{job_id}_{op_id}")
            starts[key], ends[key] = start, end
            literals = []
            for alt_id, (machine, duration) in enumerate(alternatives):
                selected = model.new_bool_var(f"x_{job_id}_{op_id}_{alt_id}")
                interval = model.new_optional_interval_var(
                    start, duration, end, selected, f"i_{job_id}_{op_id}_{alt_id}"
                )
                machine_intervals[machine].append(interval)
                choices[(job_id, op_id, alt_id)] = selected
                literals.append(selected)
            model.add_exactly_one(literals)

    for job_id, job in enumerate(jobs):
        for op_id in range(len(job) - 1):
            lag = lags.get((job_id, op_id, op_id + 1), 0)
            model.add(starts[(job_id, op_id + 1)] >= ends[(job_id, op_id)] + lag)

    for intervals in machine_intervals:
        model.add_no_overlap(intervals)

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, [ends[(j, len(job) - 1)] for j, job in enumerate(jobs)])
    if upper_bound is not None:
        model.add(makespan <= upper_bound)
    model.minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.log_search_progress = True
    solver.parameters.cp_model_presolve = True
    started = time.monotonic()
    status = solver.solve(model)
    elapsed = time.monotonic() - started
    status_name = solver.status_name(status)

    diagnostics = {
        "status": status_name,
        "runtime_seconds": elapsed,
        "objective": solver.objective_value if status in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None,
        "best_bound": solver.best_objective_bound,
        "operation_count": operation_count,
        "alternative_count": len(choices),
        "boolean_variables": solver.num_booleans,
        "branches": solver.num_branches,
        "conflicts": solver.num_conflicts,
        "workers": workers,
        "time_limit_seconds": time_limit,
        "input_upper_bound": upper_bound,
    }
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return {}, diagnostics

    schedule = []
    for job_id, job in enumerate(jobs):
        for op_id, alternatives in enumerate(job):
            selected_machine = None
            selected_duration = None
            for alt_id, (machine, duration) in enumerate(alternatives):
                if solver.boolean_value(choices[(job_id, op_id, alt_id)]):
                    selected_machine, selected_duration = machine, duration
                    break
            start = solver.value(starts[(job_id, op_id)])
            schedule.append(
                {
                    "job_id": job_id,
                    "op_id": op_id,
                    "machine_id": selected_machine,
                    "start": start,
                    "end": start + selected_duration,
                }
            )

    solution = {
        "format": "standard_fjsp_schedule_v1",
        "makespan": int(solver.value(makespan)),
        "schedule": schedule,
        "diagnostics": {"independent_cp_sat_benchmark": diagnostics},
    }
    return solution, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--time-limit-sec", type=float, default=300.0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--upper-bound", type=int)
    args = parser.parse_args()

    problem = parse_instance(args.input)
    solution, diagnostics = solve(problem, args.time_limit_sec, args.workers, args.upper_bound)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if solution:
        args.output.write_text(json.dumps(solution, indent=2), encoding="utf-8")
    if args.diagnostics:
        args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False))
    return 0 if solution else 2


if __name__ == "__main__":
    raise SystemExit(main())
