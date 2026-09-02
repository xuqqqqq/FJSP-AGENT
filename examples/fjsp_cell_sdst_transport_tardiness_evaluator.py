from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="校验并评价 FJCS-SDFSTs-ITTs 排程。")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()
    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_cell_sdst_transport_tardiness:
            raise ValueError("evaluator requires an fjsp_cell_sdst_transport_tardiness instance")
        declared = json.loads(args.solution.read_text(encoding="utf-8"))
        errors, metrics = validate_standard_schedule(instance, load_solution(args.solution))
        if "total_tardiness" not in declared:
            errors.append("solution must declare total_tardiness")
        elif float(declared["total_tardiness"]) != metrics["total_tardiness"]:
            errors.append(
                "declared total_tardiness mismatch: "
                f"declared={float(declared['total_tardiness'])}, "
                f"computed={metrics['total_tardiness']}"
            )
    except Exception as exc:  # noqa: BLE001
        errors, metrics = [str(exc)], {}
    payload = {"valid": not errors, "error_count": len(errors), "errors": errors, "metrics": metrics}
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
