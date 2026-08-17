from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import load_solution, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="校验并评价 FJSP-JPC-TST 排程。")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()
    try:
        instance = parse_standard_fjsp(args.instance)
        if instance.variant != "fjsp_jpc_tst":
            raise ValueError("FJSP-JPC-TST evaluator requires an fjsp_jpc_tst instance")
        errors, metrics = validate_standard_schedule(instance, load_solution(args.solution))
    except Exception as exc:  # noqa: BLE001
        errors, metrics = [str(exc)], {}
    payload = {"valid": not errors, "error_count": len(errors), "errors": errors, "metrics": metrics}
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
