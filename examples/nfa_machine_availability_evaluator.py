"""Machine Availability (NFA) FJSP evaluator.

Usage:
  python examples/nfa_machine_availability_evaluator.py \\
    --instance <nfa_instance.txt> --solution <schedule.json> --metrics <out.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.core.bounds import find_bounds, load_bounds_table
from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def load_best_known(path: Path | None, instance_name: str) -> float | None:
    if path is None or not path.exists():
        return None
    bounds_entry = find_bounds(load_bounds_table(path), instance_name)
    if bounds_entry is not None and bounds_entry.upper_bound is not None:
        return bounds_entry.upper_bound
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and score FJSP-NFA schedule JSON."
    )
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_machine_availability:
            errors.append("instance does not contain machine availability data")
        schedule = load_solution(args.solution)
        validation_errors, metrics = validate_standard_schedule(instance, schedule)
        errors.extend(validation_errors)
        best_known = load_best_known(args.best_known_csv, instance.name)
        if best_known and best_known > 0:
            metrics["best_known_makespan"] = float(best_known)
            metrics["gap_pct"] = (metrics["makespan"] - best_known) / best_known * 100.0
    except Exception as exc:
        errors = [str(exc)]
        metrics = {}

    payload = {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "metrics": metrics,
    }
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
