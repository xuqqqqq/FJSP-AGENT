#!/usr/bin/env python3
"""Independent CP-SAT benchmark for alternative-process-path FJSP instances."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ortools.sat.python import cp_model

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import ScheduleRecord, parse_standard_fjsp, validate_standard_schedule


STRATEGY = "independent_cp_sat_alternative_path_benchmark"


def _build_horizon(instance) -> int:
    return sum(
        max(candidate.duration for candidate in operation.candidates)
        for job in instance.jobs
        for operation in job.operations
    )


def solve(instance, time_limit: float, workers: int, upper_bound: int | None) -> tuple[dict, dict]:
    if not instance.has_alternative_routes:
        raise ValueError(f"expected fjsp_alternative_path instance, got {instance.variant!r}")

    model = cp_model.CpModel()
    horizon = _build_horizon(instance)

    starts: dict[tuple[int, int], cp_model.IntVar] = {}
    ends: dict[tuple[int, int], cp_model.IntVar] = {}
    operation_present: dict[tuple[int, int], cp_model.IntVar] = {}
    route_literals: dict[tuple[int, int], cp_model.IntVar] = {}
    machine_literals: dict[tuple[int, int, int], cp_model.IntVar] = {}
    job_completion: dict[int, cp_model.IntVar] = {}
    machine_intervals: list[list[cp_model.IntervalVar]] = [[] for _ in range(instance.machine_count)]

    optional_interval_count = 0
    conditional_precedence_count = 0
    route_choice_count = 0

    for job in instance.jobs:
        job_id = job.job_id
        route_options = instance.route_options(job_id)
        job_route_literals = []
        for route_id, _ in enumerate(route_options):
            route_lit = model.new_bool_var(f"route_{job_id}_{route_id}")
            route_literals[(job_id, route_id)] = route_lit
            job_route_literals.append(route_lit)
        model.add_exactly_one(job_route_literals)
        route_choice_count += 1

        job_completion[job_id] = model.new_int_var(0, horizon, f"job_completion_{job_id}")

        for operation in job.operations:
            op_key = (job_id, operation.op_id)
            starts[op_key] = model.new_int_var(0, horizon, f"start_{job_id}_{operation.op_id}")
            ends[op_key] = model.new_int_var(0, horizon, f"end_{job_id}_{operation.op_id}")
            present = model.new_bool_var(f"present_{job_id}_{operation.op_id}")
            containing_routes = [
                route_literals[(job_id, route_id)]
                for route_id, route in enumerate(route_options)
                if operation.op_id in route
            ]
            model.add(present == sum(containing_routes))
            operation_present[op_key] = present

            option_literals = []
            for option_id, candidate in enumerate(operation.candidates):
                selected = model.new_bool_var(f"machine_{job_id}_{operation.op_id}_{option_id}")
                machine_literals[(job_id, operation.op_id, option_id)] = selected
                option_literals.append(selected)
                interval = model.new_optional_interval_var(
                    starts[op_key],
                    candidate.duration,
                    ends[op_key],
                    selected,
                    f"interval_{job_id}_{operation.op_id}_{option_id}",
                )
                machine_intervals[candidate.machine_id].append(interval)
                optional_interval_count += 1
            model.add(sum(option_literals) == present)

        for route_id, route in enumerate(route_options):
            route_lit = route_literals[(job_id, route_id)]
            model.add(job_completion[job_id] == ends[(job_id, route[-1])]).only_enforce_if(route_lit)
            for from_op, to_op in zip(route, route[1:]):
                model.add(starts[(job_id, to_op)] >= ends[(job_id, from_op)]).only_enforce_if(route_lit)
                conditional_precedence_count += 1

    for intervals in machine_intervals:
        if intervals:
            model.add_no_overlap(intervals)

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(job_completion.values()))
    if upper_bound is not None:
        model.add(makespan <= upper_bound)
    model.minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.cp_model_presolve = True

    started = time.monotonic()
    status = solver.solve(model)
    elapsed = time.monotonic() - started
    status_name = solver.status_name(status)

    diagnostics = {
        "status": status_name,
        "runtime_seconds": elapsed,
        "objective": int(solver.value(makespan)) if status in (cp_model.FEASIBLE, cp_model.OPTIMAL) else None,
        "best_bound": solver.best_objective_bound,
        "job_count": instance.job_count,
        "machine_count": instance.machine_count,
        "operation_pool_count": instance.operation_count,
        "workers": workers,
        "time_limit_seconds": time_limit,
        "input_upper_bound": upper_bound,
        "solver_evidence": {
            "route_one_hot_constraints_posted": route_choice_count,
            "route_optional_intervals_posted": optional_interval_count,
            "route_conditional_precedences_posted": conditional_precedence_count,
        },
        "branches": solver.num_branches,
        "conflicts": solver.num_conflicts,
        "boolean_variables": solver.num_booleans,
    }
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return {}, diagnostics

    selected_routes: dict[int, int] = {}
    for job in instance.jobs:
        for route_id, _ in enumerate(instance.route_options(job.job_id)):
            if solver.boolean_value(route_literals[(job.job_id, route_id)]):
                selected_routes[job.job_id] = route_id
                break

    schedule: list[ScheduleRecord] = []
    for job in instance.jobs:
        for operation in job.operations:
            op_key = (job.job_id, operation.op_id)
            if not solver.boolean_value(operation_present[op_key]):
                continue
            selected_machine = None
            for option_id, candidate in enumerate(operation.candidates):
                if solver.boolean_value(machine_literals[(job.job_id, operation.op_id, option_id)]):
                    selected_machine = candidate.machine_id
                    break
            if selected_machine is None:
                raise RuntimeError(f"missing machine assignment for {op_key}")
            schedule.append(
                ScheduleRecord(
                    job_id=job.job_id,
                    op_id=operation.op_id,
                    machine_id=selected_machine,
                    start=solver.value(starts[op_key]),
                    end=solver.value(ends[op_key]),
                )
            )

    errors, metrics = validate_standard_schedule(instance, schedule, selected_routes=selected_routes)
    if errors:
        raise RuntimeError(f"generated invalid schedule: {errors}")
    diagnostics["validation_metrics"] = metrics

    solution = {
        "format": "standard_fjsp_schedule_v1",
        "variant": instance.variant,
        "instance": instance.name,
        "strategy": STRATEGY,
        "makespan": int(solver.value(makespan)),
        "alternative_path_policy": "selected_route_checked_by_evaluator",
        "selected_routes": {str(job_id): route_id for job_id, route_id in sorted(selected_routes.items())},
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
        "diagnostics": {
            "cp_sat_called": True,
            "solver_evidence": diagnostics["solver_evidence"],
            "independent_cp_sat_benchmark": diagnostics,
        },
    }
    return solution, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", "--instance", dest="input_path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--time-limit-sec", type=float, default=300.0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--upper-bound", type=int)
    args = parser.parse_args()

    instance = parse_standard_fjsp(args.input_path)
    solution, diagnostics = solve(instance, args.time_limit_sec, args.workers, args.upper_bound)

    if solution:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(solution, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.diagnostics:
        args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False))
    return 0 if solution else 2


if __name__ == "__main__":
    raise SystemExit(main())
