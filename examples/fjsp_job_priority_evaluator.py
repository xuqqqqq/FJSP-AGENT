from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and score FJSP with job priority schedule JSON.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_job_priority:
            raise ValueError("instance does not contain FJSP job-priority tail")
        schedule = load_solution(args.solution)
        errors, metrics = validate_standard_schedule(instance, schedule)
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
