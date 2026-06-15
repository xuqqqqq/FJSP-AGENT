from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    instance = json.loads(args.input.read_text(encoding="utf-8"))
    solution = {
        "instance": instance["name"],
        "seed": args.seed,
        "schedule": [
            {
                "job_id": "J1",
                "operation_id": "J1-O1",
                "machine_id": "M1",
                "start": 0,
                "end": 10 + args.seed
            }
        ]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(solution, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

