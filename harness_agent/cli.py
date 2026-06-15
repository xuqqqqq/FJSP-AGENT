from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import TaskContract
from .runner import HarnessRunner


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
    runner = HarnessRunner(contract=contract, project_root=args.project_root, output_dir=args.output_dir)
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
                "report": str((args.output_dir / "report.md").resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-contract":
        return validate_contract(args)
    if args.command == "run":
        return run_contract(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

