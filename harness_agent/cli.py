from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph_runner import GraphHarnessRunner
from .models import TaskContract
from .runner import HarnessRunner
from .standard_agent import StandardFjspAgentRunner
from .worker import NullWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FJSP Harness Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-contract", help="validate a task contract")
    validate.add_argument("--contract", required=True, type=Path)
    validate.add_argument("--project-root", type=Path, default=Path.cwd())

    run = subparsers.add_parser("run", help="run solver/evaluator experiments from a task contract")
    run.add_argument("--contract", required=True, type=Path)
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--runner", choices=["langgraph", "linear"], default="langgraph")

    build_standard = subparsers.add_parser("build-standard-contract", help="create a standard FJSP task contract")
    build_standard.add_argument("--instance-dir", required=True, type=Path)
    build_standard.add_argument("--pattern", default="*.txt")
    build_standard.add_argument("--output", required=True, type=Path)
    build_standard.add_argument("--best-known-csv", type=Path)
    build_standard.add_argument("--task-id", default="standard_fjsp_batch")
    build_standard.add_argument("--rounds", type=int, default=1)
    build_standard.add_argument("--seeds", default="0,1,2")
    build_standard.add_argument("--timeout-seconds", type=int, default=60)
    build_standard.add_argument("--max-workers", type=int, default=1)
    build_standard.add_argument("--max-instances", type=int)
    build_standard.add_argument("--solver", choices=["local-search", "portfolio", "ect"], default="portfolio")
    build_standard.add_argument("--portfolio-size", type=int, default=64)
    build_standard.add_argument("--strategy-profile", type=Path)
    build_standard.add_argument("--local-search-restarts", type=int, default=2)
    build_standard.add_argument("--local-search-initial-pool-size", type=int, default=1)
    build_standard.add_argument("--local-search-iterations", type=int, default=80)
    build_standard.add_argument("--local-search-neighbor-limit", type=int, default=180)
    build_standard.add_argument("--local-search-time-limit-sec", type=float, default=4.0)
    build_standard.add_argument(
        "--local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid"],
        default="random",
    )

    subparsers.add_parser("worker-status", help="show available coding worker backends")

    standard_agent = subparsers.add_parser("run-standard-agent", help="run the document-driven standard FJSP agent loop")
    standard_agent.add_argument("--doc", action="append", type=Path, default=[])
    standard_agent.add_argument("--instance-dir", required=True, type=Path)
    standard_agent.add_argument("--pattern", default="*.txt")
    standard_agent.add_argument("--best-known-csv", type=Path)
    standard_agent.add_argument("--output-dir", required=True, type=Path)
    standard_agent.add_argument("--project-root", type=Path, default=Path.cwd())
    standard_agent.add_argument("--max-instances", type=int)
    standard_agent.add_argument("--max-rounds", type=int, default=1)
    standard_agent.add_argument("--seeds", default="0,1,2")
    standard_agent.add_argument("--timeout-seconds", type=int, default=120)
    standard_agent.add_argument("--max-workers", type=int, default=1)
    standard_agent.add_argument("--solver", choices=["local-search", "portfolio"], default="local-search")
    standard_agent.add_argument("--portfolio-size", type=int, default=96)
    standard_agent.add_argument("--local-search-restarts", type=int, default=2)
    standard_agent.add_argument("--local-search-initial-pool-size", type=int, default=1)
    standard_agent.add_argument("--local-search-iterations", type=int, default=80)
    standard_agent.add_argument("--local-search-neighbor-limit", type=int, default=180)
    standard_agent.add_argument("--local-search-time-limit-sec", type=float, default=4.0)
    standard_agent.add_argument(
        "--local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid"],
        default="random",
    )
    standard_agent.add_argument(
        "--local-search-neighborhood-profiles",
        help="comma-separated neighborhood profiles to cross-evaluate in each agent round",
    )
    standard_agent.add_argument(
        "--local-search-run-profiles",
        help=(
            "comma-separated local-search run presets to cross-evaluate. "
            "Built-ins: current, balanced-random, balanced-combined, balanced-hgtsa, deep-combined, deep-hgtsa"
        ),
    )
    standard_agent.add_argument("--strategy-candidates", type=int, default=1)
    standard_agent.add_argument("--profile-mode", choices=["auto", "deepseek", "template"], default="auto")
    standard_agent.add_argument("--deepseek-model", default="deepseek-v4-pro")
    return parser


def validate_contract(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    result = {
        "task_id": contract.task_id,
        "problem_family": contract.problem_family,
        "instances": len(contract.instances),
        "objectives": [objective.name for objective in contract.objectives],
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def run_contract(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    if errors:
        print(json.dumps({"status": "invalid_contract", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    runner_cls = GraphHarnessRunner if args.runner == "langgraph" else HarnessRunner
    runner = runner_cls(contract=contract, project_root=args.project_root, output_dir=args.output_dir)
    try:
        summary = runner.run()
    finally:
        runner.close()
    print(
        json.dumps(
            {
                "status": "ok",
                "total": summary.total,
                "valid": summary.valid,
                "failed": summary.failed,
                "best_experiment_id": summary.best_experiment_id,
                "best_metrics": summary.best_metrics,
                "best_candidate_id": summary.best_candidate_id,
                "best_candidate_metrics": summary.best_candidate_metrics,
                "report": str((args.output_dir / "report.md").resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_standard_contract(args: argparse.Namespace) -> int:
    instances = sorted(args.instance_dir.glob(args.pattern))
    if args.max_instances is not None:
        instances = instances[: args.max_instances]
    if not instances:
        print(json.dumps({"status": "no_instances", "instance_dir": str(args.instance_dir), "pattern": args.pattern}, ensure_ascii=False))
        return 1

    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    resources: dict[str, str] = {}
    solver = "python examples/standard_fjsp_solver.py --input {instance} --output {solution} --seed {seed}"
    if args.solver == "portfolio":
        solver = (
            "python examples/standard_fjsp_portfolio_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {args.portfolio_size}"
        )
        if args.strategy_profile:
            resources["strategy_profile"] = str(args.strategy_profile)
            solver += " --strategy-profile {strategy_profile}"
    elif args.solver == "local-search":
        solver = (
            "python examples/standard_fjsp_local_search_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {args.portfolio_size} "
            f"--restarts {args.local_search_restarts} "
            f"--initial-pool-size {args.local_search_initial_pool_size} "
            f"--iterations {args.local_search_iterations} "
            f"--neighbor-limit {args.local_search_neighbor_limit} "
            f"--time-limit-sec {args.local_search_time_limit_sec} "
            f"--neighborhood-profile {args.local_search_neighborhood_profile}"
        )
        if args.strategy_profile:
            resources["strategy_profile"] = str(args.strategy_profile)
            solver += " --strategy-profile {strategy_profile}"
    evaluator = "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
    if args.best_known_csv:
        resources["best_known_csv"] = str(args.best_known_csv)
        evaluator += " --best-known-csv {best_known_csv}"

    payload = {
        "task_id": args.task_id,
        "problem_family": "FJSP",
        "description": "Generated standard-FJSP benchmark contract with optional best-known gap reporting.",
        "instances": [{"id": path.stem, "path": str(path)} for path in instances],
        "objectives": [
            {
                "name": "makespan",
                "direction": "minimize",
                "priority": 1,
                "invalid_if_missing": True,
            }
        ],
        "commands": {
            "solver": solver,
            "evaluator": evaluator,
            "quick_test": "python -m compileall harness_agent examples",
        },
        "budget": {
            "rounds": args.rounds,
            "seeds": seeds,
            "timeout_seconds": args.timeout_seconds,
            "max_workers": max(1, args.max_workers),
        },
        "paths": {
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "resources": resources,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(args.output.resolve()), "instances": len(instances)}, ensure_ascii=False, indent=2))
    return 0


def worker_status(args: argparse.Namespace) -> int:
    workers = [NullWorker().capabilities()]
    try:
        from .workers.deepseek_worker import DeepSeekWorker
        from .workers.opencode_worker import OpenCodeWorker

        workers.append(DeepSeekWorker().capabilities())
        workers.append(OpenCodeWorker().capabilities())
    except Exception as exc:  # noqa: BLE001 - status command should report adapter import failures.
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"workers": [worker.__dict__ for worker in workers]}, ensure_ascii=False, indent=2))
    return 0


def run_standard_agent(args: argparse.Namespace) -> int:
    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    neighborhood_profiles = parse_neighborhood_profiles(
        args.local_search_neighborhood_profiles,
        fallback=args.local_search_neighborhood_profile,
    )
    run_profiles = build_local_search_run_profiles(args, neighborhood_profiles)
    runner = StandardFjspAgentRunner(
        docs=args.doc,
        instance_dir=args.instance_dir,
        pattern=args.pattern,
        output_dir=args.output_dir,
        best_known_csv=args.best_known_csv,
        max_instances=args.max_instances,
        max_rounds=args.max_rounds,
        seeds=seeds,
        timeout_seconds=args.timeout_seconds,
        max_workers=max(1, args.max_workers),
        solver=args.solver,
        portfolio_size=args.portfolio_size,
        local_search_restarts=args.local_search_restarts,
        local_search_initial_pool_size=args.local_search_initial_pool_size,
        local_search_iterations=args.local_search_iterations,
        local_search_neighbor_limit=args.local_search_neighbor_limit,
        local_search_time_limit_sec=args.local_search_time_limit_sec,
        local_search_neighborhood_profiles=neighborhood_profiles,
        local_search_run_profiles=run_profiles,
        strategy_candidates=args.strategy_candidates,
        profile_mode=args.profile_mode,
        deepseek_model=args.deepseek_model,
        project_root=args.project_root,
    )
    result = runner.run()
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


def parse_neighborhood_profiles(value: str | None, *, fallback: str) -> list[str]:
    allowed = {"random", "critical-block", "combined", "hgtsa-lite", "hybrid"}
    raw_items = [fallback] if not value else [item.strip() for item in value.split(",") if item.strip()]
    profiles: list[str] = []
    for item in raw_items:
        if item not in allowed:
            raise ValueError(f"unknown local-search neighborhood profile: {item}")
        if item not in profiles:
            profiles.append(item)
    return profiles or [fallback]


def build_local_search_run_profiles(args: argparse.Namespace, neighborhood_profiles: list[str]) -> list[dict[str, object]] | None:
    if not args.local_search_run_profiles:
        return None

    custom_by_neighborhood = {
        profile: {
            "name": f"current-{profile}",
            "portfolio_size": args.portfolio_size,
            "restarts": args.local_search_restarts,
            "initial_pool_size": args.local_search_initial_pool_size,
            "iterations": args.local_search_iterations,
            "neighbor_limit": args.local_search_neighbor_limit,
            "time_limit_sec": args.local_search_time_limit_sec,
            "neighborhood_profile": profile,
        }
        for profile in neighborhood_profiles
    }
    presets: dict[str, dict[str, object]] = {
        "balanced-random": {
            "name": "balanced-random",
            "portfolio_size": max(args.portfolio_size, 192),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 100),
            "neighbor_limit": max(args.local_search_neighbor_limit, 220),
            "time_limit_sec": max(args.local_search_time_limit_sec, 4.0),
            "neighborhood_profile": "random",
        },
        "balanced-combined": {
            "name": "balanced-combined",
            "portfolio_size": max(args.portfolio_size, 192),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 100),
            "neighbor_limit": max(args.local_search_neighbor_limit, 220),
            "time_limit_sec": max(args.local_search_time_limit_sec, 4.0),
            "neighborhood_profile": "combined",
        },
        "deep-combined": {
            "name": "deep-combined",
            "portfolio_size": max(args.portfolio_size, 256),
            "restarts": max(args.local_search_restarts, 3),
            "initial_pool_size": max(args.local_search_initial_pool_size, 2),
            "iterations": max(args.local_search_iterations, 180),
            "neighbor_limit": max(args.local_search_neighbor_limit, 320),
            "time_limit_sec": max(args.local_search_time_limit_sec, 8.0),
            "neighborhood_profile": "combined",
        },
        "balanced-hgtsa": {
            "name": "balanced-hgtsa",
            "portfolio_size": max(args.portfolio_size, 192),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 100),
            "neighbor_limit": max(args.local_search_neighbor_limit, 220),
            "time_limit_sec": max(args.local_search_time_limit_sec, 4.0),
            "neighborhood_profile": "hgtsa-lite",
        },
        "deep-hgtsa": {
            "name": "deep-hgtsa",
            "portfolio_size": max(args.portfolio_size, 256),
            "restarts": max(args.local_search_restarts, 3),
            "initial_pool_size": max(args.local_search_initial_pool_size, 2),
            "iterations": max(args.local_search_iterations, 180),
            "neighbor_limit": max(args.local_search_neighbor_limit, 320),
            "time_limit_sec": max(args.local_search_time_limit_sec, 8.0),
            "neighborhood_profile": "hybrid",
        },
    }
    for profile, payload in custom_by_neighborhood.items():
        presets[f"current-{profile}"] = payload
    if len(custom_by_neighborhood) == 1:
        presets["current"] = next(iter(custom_by_neighborhood.values()))

    requested = [item.strip() for item in args.local_search_run_profiles.split(",") if item.strip()]
    run_profiles: list[dict[str, object]] = []
    for name in requested:
        if name not in presets:
            raise ValueError(f"unknown local-search run profile: {name}")
        profile = dict(presets[name])
        if profile not in run_profiles:
            run_profiles.append(profile)
    return run_profiles


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-contract":
        return validate_contract(args)
    if args.command == "run":
        return run_contract(args)
    if args.command == "build-standard-contract":
        return build_standard_contract(args)
    if args.command == "worker-status":
        return worker_status(args)
    if args.command == "run-standard-agent":
        return run_standard_agent(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
