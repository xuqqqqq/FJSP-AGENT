from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    args = parser.parse_args()

    instance = json.loads(args.instance.read_text(encoding="utf-8"))
    solution = json.loads(args.solution.read_text(encoding="utf-8"))
    errors: list[str] = []
    if solution.get("instance") != instance.get("name"):
        errors.append("solution instance name mismatch")
    schedule = solution.get("schedule", [])
    if len(schedule) != 1:
        errors.append("dummy evaluator expects exactly one operation")

    end_time = schedule[0].get("end", 999999) if schedule else 999999
    valid = not errors
    payload = {
        "valid": valid,
        "error_count": len(errors),
        "errors": errors,
        "metrics": {
            "primary_score": 1000 - float(end_time),
            "makespan": float(end_time),
            "runtime_seconds": 0.01
        }
    }
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

