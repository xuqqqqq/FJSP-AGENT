from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import load_solution_document, parse_standard_fjsp, validate_standard_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and score alternative-process-path FJSP schedules.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()

    try:
        instance = parse_standard_fjsp(args.instance)
        if not instance.has_alternative_routes:
            raise ValueError(f"expected fjsp_alternative_path instance, got {instance.variant!r}")
        solution = load_solution_document(args.solution)
        errors, metrics = validate_standard_schedule(
            instance,
            solution.schedule,
            selected_routes=solution.selected_routes,
        )
    except Exception as exc:  # noqa: BLE001 - evaluator converts failures to structured output.
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
