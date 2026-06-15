from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.standard_fjsp import load_solution, parse_standard_fjsp, validate_standard_schedule


def load_best_known(path: Path | None, instance_name: str) -> float | None:
    if path is None or not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            values = {key.lower(): value for key, value in row.items() if key}
            name = values.get("instance") or values.get("name") or values.get("file") or values.get("id")
            best = values.get("best") or values.get("best_known") or values.get("ub") or values.get("makespan")
            if name and Path(name).stem == instance_name and best:
                return float(best)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and score standard FJSP schedule JSON.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        instance = parse_standard_fjsp(args.instance)
        schedule = load_solution(args.solution)
        errors, metrics = validate_standard_schedule(instance, schedule)
        best_known = load_best_known(args.best_known_csv, instance.name)
        if best_known and best_known > 0:
            metrics["best_known_makespan"] = float(best_known)
            metrics["gap_pct"] = (metrics["makespan"] - best_known) / best_known * 100.0
    except Exception as exc:  # noqa: BLE001 - evaluator converts all issues into structured errors.
        errors = [str(exc)]
        metrics = {}

    payload = {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "metrics": metrics,
    }
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
