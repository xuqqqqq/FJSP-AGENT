from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph_runner import GraphHarnessRunner
from .models import TaskContract
from .runner import HarnessRunner
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
    build_standard.add_argument("--max-instances", type=int)

    subparsers.add_parser("worker-status", help="show available coding worker backends")
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
            "solver": "python examples/standard_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
            "evaluator": evaluator,
            "quick_test": "python -m compileall harness_agent examples",
        },
        "budget": {
            "rounds": args.rounds,
            "seeds": seeds,
            "timeout_seconds": args.timeout_seconds,
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
        from .workers.opencode_worker import OpenCodeWorker

        workers.append(OpenCodeWorker().capabilities())
    except Exception as exc:  # noqa: BLE001 - status command should report adapter import failures.
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"workers": [worker.__dict__ for worker in workers]}, ensure_ascii=False, indent=2))
    return 0


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
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
