from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.standard_fjsp_evaluator import load_best_known
from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate FJSP schedules with machine maintenance windows.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()
    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_machine_availability:
            raise ValueError("machine-availability evaluator requires an fjsp_machine_availability instance")
        errors, metrics = validate_standard_schedule(instance, load_solution(args.solution))
        best_known = load_best_known(args.best_known_csv, instance.name)
        if best_known and best_known > 0:
            metrics["best_known_makespan"] = float(best_known)
            metrics["gap_pct"] = (metrics["makespan"] - best_known) / best_known * 100.0
    except Exception as exc:  # noqa: BLE001
        errors, metrics = [str(exc)], {}
    payload = {"valid": not errors, "error_count": len(errors), "errors": errors, "metrics": metrics}
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
