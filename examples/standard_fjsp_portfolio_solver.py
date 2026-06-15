from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.standard_fjsp import (
    ScheduleRecord,
    StandardFjspInstance,
    parse_standard_fjsp,
    validate_standard_schedule,
    write_solution,
)


@dataclass(frozen=True)
class Strategy:
    name: str
    weights: dict[str, float]
    noise: float = 0.0


def remaining_min_work(instance: StandardFjspInstance) -> list[list[float]]:
    remaining_by_job: list[list[float]] = []
    for job in instance.jobs:
        suffix = [0.0] * (len(job.operations) + 1)
        for op_idx in range(len(job.operations) - 1, -1, -1):
            op = job.operations[op_idx]
            suffix[op_idx] = suffix[op_idx + 1] + min(candidate.duration for candidate in op.candidates)
        remaining_by_job.append(suffix)
    return remaining_by_job


def score_action(strategy: Strategy, rng: random.Random, features: dict[str, float]) -> float:
    score = sum(strategy.weights.get(name, 0.0) * value for name, value in features.items())
    if strategy.noise:
        score += strategy.noise * rng.uniform(-1.0, 1.0)
    return score


def build_schedule(instance: StandardFjspInstance, strategy: Strategy, seed: int) -> list[ScheduleRecord]:
    rng = random.Random(seed)
    next_op = [0 for _ in instance.jobs]
    job_ready = [0 for _ in instance.jobs]
    machine_ready = [0 for _ in range(instance.machine_count)]
    machine_load = [0 for _ in range(instance.machine_count)]
    remaining_work = remaining_min_work(instance)
    schedule: list[ScheduleRecord] = []

    max_duration = max(
        candidate.duration
        for job in instance.jobs
        for op in job.operations
        for candidate in op.candidates
    )
    max_remaining = max(row[0] for row in remaining_work) or 1.0
    max_ops = max(len(job.operations) for job in instance.jobs) or 1

    for _ in range(instance.operation_count):
        best_key: tuple[float, int, int, int, int] | None = None
        best_record: ScheduleRecord | None = None
        horizon_scale = max(1.0, max(max(job_ready), max(machine_ready)))

        for job in instance.jobs:
            op_idx = next_op[job.job_id]
            if op_idx >= len(job.operations):
                continue
            op = job.operations[op_idx]
            remaining_ops = len(job.operations) - op_idx
            min_duration = min(candidate.duration for candidate in op.candidates)
            flexibility = len(op.candidates) / max(1, instance.max_candidate_count)

            for candidate in op.candidates:
                start = max(job_ready[job.job_id], machine_ready[candidate.machine_id])
                finish = start + candidate.duration
                candidate_horizon = max(horizon_scale, finish)
                avg_load = max(1.0, sum(machine_load) / max(1, instance.machine_count))
                features = {
                    "early_finish": -finish / candidate_horizon,
                    "early_start": -start / candidate_horizon,
                    "short_processing": -candidate.duration / max_duration,
                    "long_processing": candidate.duration / max_duration,
                    "min_option": -min_duration / max_duration,
                    "remaining_work": remaining_work[job.job_id][op_idx] / max_remaining,
                    "remaining_after": remaining_work[job.job_id][op_idx + 1] / max_remaining,
                    "remaining_ops": remaining_ops / max_ops,
                    "machine_ready": -machine_ready[candidate.machine_id] / candidate_horizon,
                    "job_ready": -job_ready[job.job_id] / candidate_horizon,
                    "machine_load": -machine_load[candidate.machine_id] / (avg_load + max_duration),
                    "flexibility": -flexibility,
                    "machine_slack": -(start - machine_ready[candidate.machine_id]) / candidate_horizon,
                    "job_slack": -(start - job_ready[job.job_id]) / candidate_horizon,
                }
                value = score_action(strategy, rng, features)
                key = (value, -finish, -candidate.duration, -job.job_id, -candidate.machine_id)
                if best_key is None or key > best_key:
                    best_key = key
                    best_record = ScheduleRecord(
                        job_id=job.job_id,
                        op_id=op.op_id,
                        machine_id=candidate.machine_id,
                        start=start,
                        end=finish,
                    )

        if best_record is None:
            raise RuntimeError(f"no schedulable action in {instance.name}")
        schedule.append(best_record)
        next_op[best_record.job_id] += 1
        job_ready[best_record.job_id] = best_record.end
        machine_ready[best_record.machine_id] = best_record.end
        machine_load[best_record.machine_id] += best_record.duration

    return schedule


def baseline_strategies() -> list[Strategy]:
    return [
        Strategy("ECT", {"early_finish": 10.0}),
        Strategy("SPT", {"short_processing": 10.0, "early_finish": 1.0}),
        Strategy("LPT", {"long_processing": 8.0, "early_finish": 1.0}),
        Strategy("MWKR", {"remaining_work": 8.0, "early_finish": 2.0}),
        Strategy("MOR", {"remaining_ops": 8.0, "early_finish": 2.0}),
        Strategy("LOAD_BALANCE", {"machine_load": 7.0, "machine_ready": 2.0, "early_finish": 2.0}),
        Strategy(
            "BALANCED",
            {
                "early_finish": 5.0,
                "short_processing": 2.0,
                "remaining_work": 3.0,
                "machine_load": 2.0,
                "flexibility": 1.0,
            },
        ),
        Strategy(
            "BOTTLENECK_AWARE",
            {
                "early_finish": 4.0,
                "machine_load": 4.0,
                "machine_ready": 2.5,
                "remaining_after": 2.0,
                "short_processing": 1.0,
            },
        ),
    ]


def random_strategy(rng: random.Random, name: str) -> Strategy:
    keys = [
        "early_finish",
        "early_start",
        "short_processing",
        "long_processing",
        "min_option",
        "remaining_work",
        "remaining_after",
        "remaining_ops",
        "machine_ready",
        "job_ready",
        "machine_load",
        "flexibility",
        "machine_slack",
        "job_slack",
    ]
    weights = {key: rng.uniform(-3.0, 6.0) for key in keys}
    weights["early_finish"] = rng.uniform(0.0, 10.0)
    return Strategy(name=name, weights=weights, noise=rng.uniform(0.0, 0.06))


def mutate_strategy(parent: Strategy, rng: random.Random, name: str) -> Strategy:
    weights = {key: value + rng.gauss(0.0, 1.0) for key, value in parent.weights.items()}
    for key in ("early_finish", "short_processing", "remaining_work", "machine_load"):
        weights.setdefault(key, rng.uniform(-1.0, 5.0))
    return Strategy(name=name, weights=weights, noise=max(0.0, min(0.10, parent.noise + rng.gauss(0.0, 0.02))))


def crossover_strategy(left: Strategy, right: Strategy, rng: random.Random, name: str) -> Strategy:
    keys = set(left.weights) | set(right.weights)
    weights: dict[str, float] = {}
    for key in keys:
        alpha = rng.uniform(0.25, 0.75)
        weights[key] = alpha * left.weights.get(key, 0.0) + (1.0 - alpha) * right.weights.get(key, 0.0)
    return Strategy(name=name, weights=weights, noise=(left.noise + right.noise) / 2.0)


def load_strategy_profile(path: Path | None) -> list[Strategy]:
    if path is None:
        return []
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    items = raw.get("strategies", raw if isinstance(raw, list) else [])
    strategies: list[Strategy] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        weights = item.get("weights", {})
        if not isinstance(weights, dict):
            continue
        strategies.append(
            Strategy(
                name=str(item.get("name", f"profile_{index:03d}")),
                weights={str(key): float(value) for key, value in weights.items()},
                noise=float(item.get("noise", 0.0)),
            )
        )
    return strategies


def build_portfolio(seed: int, portfolio_size: int, profile_path: Path | None) -> list[Strategy]:
    rng = random.Random(seed)
    strategies = baseline_strategies() + load_strategy_profile(profile_path)
    while len(strategies) < portfolio_size:
        if len(strategies) >= 2 and rng.random() < 0.30:
            left, right = rng.sample(strategies, 2)
            strategies.append(crossover_strategy(left, right, rng, f"cross_{len(strategies):03d}"))
        elif len(strategies) > len(baseline_strategies()) and rng.random() < 0.65:
            parent = rng.choice(strategies)
            strategies.append(mutate_strategy(parent, rng, f"mut_{len(strategies):03d}"))
        else:
            strategies.append(random_strategy(rng, f"rand_{len(strategies):03d}"))
    return strategies[:portfolio_size]


def choose_best(instance: StandardFjspInstance, strategies: list[Strategy], seed: int) -> tuple[Strategy, list[ScheduleRecord]]:
    best: tuple[int, int, Strategy, list[ScheduleRecord]] | None = None
    for index, strategy in enumerate(strategies):
        schedule = build_schedule(instance, strategy, seed + index * 100003)
        errors, metrics = validate_standard_schedule(instance, schedule)
        if errors:
            continue
        makespan = int(metrics["makespan"])
        key = (makespan, index)
        if best is None or key < (best[0], best[1]):
            best = (makespan, index, strategy, schedule)
    if best is None:
        raise RuntimeError("no legal schedule generated by the portfolio")
    return best[2], best[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Portfolio dispatch solver for standard FJSP instances.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--portfolio-size", type=int, default=64)
    parser.add_argument("--strategy-profile", type=Path)
    args = parser.parse_args()

    start = time.perf_counter()
    instance = parse_standard_fjsp(args.input)
    strategies = build_portfolio(args.seed, max(1, args.portfolio_size), args.strategy_profile)
    winner, schedule = choose_best(instance, strategies, args.seed)
    runtime_sec = time.perf_counter() - start
    write_solution(
        args.output,
        instance,
        schedule,
        strategy=f"portfolio:{winner.name}:size={len(strategies)}:runtime={runtime_sec:.6f}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
