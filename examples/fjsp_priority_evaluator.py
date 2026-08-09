from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and score Job-Priority FJSP schedules.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()
    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_job_priorities:
            raise ValueError("priority evaluator requires an fjsp_priority instance")
        declared = json.loads(args.solution.read_text(encoding="utf-8"))
        errors, metrics = validate_standard_schedule(instance, load_solution(args.solution))
        if "priority_completion_time" not in declared:
            errors.append("solution must declare priority_completion_time")
        else:
            declared_priority = float(declared["priority_completion_time"])
            if declared_priority != metrics["priority_completion_time"]:
                errors.append(
                    "declared priority_completion_time mismatch: "
                    f"declared={declared_priority}, computed={metrics['priority_completion_time']}"
                )
    except Exception as exc:  # noqa: BLE001
        errors, metrics = [str(exc)], {}
    payload = {"valid": not errors, "error_count": len(errors), "errors": errors, "metrics": metrics}
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
