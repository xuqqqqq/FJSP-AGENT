from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.distributed_fjsp import (
    load_distributed_solution,
    parse_distributed_fjsp,
    validate_distributed_schedule,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate distributed FJSP schedules with transfers.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    args = parser.parse_args()
    try:
        instance = parse_distributed_fjsp(args.instance)
        declared = json.loads(args.solution.read_text(encoding="utf-8"))
        if declared.get("format") != "standard_fjsp_schedule_v1":
            raise ValueError("distributed solution format must be standard_fjsp_schedule_v1")
        for field in ("makespan", "max_factory_workload", "total_energy_consumption"):
            if field not in declared:
                raise ValueError(f"distributed solution is missing required field: {field}")
        errors, metrics = validate_distributed_schedule(instance, load_distributed_solution(args.solution))
        for field in ("makespan", "max_factory_workload", "total_energy_consumption"):
            try:
                actual = float(declared[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"distributed solution field {field} must be numeric") from exc
            if actual != float(metrics[field]):
                errors.append(
                    f"declared {field} mismatch: declared={actual}, computed={metrics[field]}"
                )
    except Exception as exc:  # noqa: BLE001
        errors, metrics = [str(exc)], {}
    payload = {"valid": not errors, "error_count": len(errors), "errors": errors, "metrics": metrics}
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
