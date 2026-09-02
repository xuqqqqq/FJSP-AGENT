from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="校验并评价工作负荷多目标 FJSP 排程。")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()

    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_workload_objectives:
            raise ValueError("工作负荷多目标 evaluator 需要 fjsp_multiobjective_workload 实例")
        declared = json.loads(args.solution.read_text(encoding="utf-8"))
        errors, metrics = validate_standard_schedule(instance, load_solution(args.solution))
        for metric_name in ("max_machine_workload", "total_workload"):
            if metric_name not in declared:
                errors.append(f"solution must declare {metric_name}")
                continue
            declared_value = float(declared[metric_name])
            if declared_value != metrics[metric_name]:
                errors.append(
                    f"declared {metric_name} mismatch: "
                    f"declared={declared_value}, computed={metrics[metric_name]}"
                )
    except Exception as exc:  # noqa: BLE001 - 固定 evaluator 将错误结构化输出。
        errors, metrics = [str(exc)], {}

    payload = {"valid": not errors, "error_count": len(errors), "errors": errors, "metrics": metrics}
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
