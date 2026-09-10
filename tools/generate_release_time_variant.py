from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.domains.io import parse_standard_fjsp


def _release_window(instance) -> int:
    minimum_processing_load = sum(
        min(option.duration for option in operation.candidates)
        for job in instance.jobs
        for operation in job.operations
    )
    return max(10, round(minimum_processing_load / instance.machine_count * 0.2))


def build_release_rows(instance, seed: int) -> tuple[list[int], list[int]]:
    window = _release_window(instance)
    job_releases = [
        0 if job_id % 5 == 0 else 1 + (seed * 11 + job_id * 37 + job_id * job_id * 7) % window
        for job_id in range(instance.job_count)
    ]
    machine_available = [
        0
        if machine_id % 4 == 0
        else 1 + (seed * 13 + machine_id * 23 + machine_id * machine_id * 5) % window
        for machine_id in range(instance.machine_count)
    ]
    return job_releases, machine_available


def generate_release_time_variant(source: Path, output: Path, seed: int) -> dict[str, object]:
    instance = parse_standard_fjsp(source)
    if instance.variant != "standard_fjsp":
        raise ValueError(f"source must be a standard FJSP instance, got {instance.variant}")

    job_releases, machine_available = build_release_rows(instance, seed)
    width = max(instance.job_count, instance.machine_count)
    job_row = job_releases + [-1] * (width - instance.job_count)
    machine_row = machine_available + [-1] * (width - instance.machine_count)

    source_bytes = source.read_bytes()
    body = source_bytes.decode("utf-8").rstrip()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        body + "\n" + " ".join(map(str, job_row)) + "\n" + " ".join(map(str, machine_row)) + "\n",
        encoding="utf-8",
    )

    generated = parse_standard_fjsp(output)
    if generated.variant != "fjsp_release_time":
        raise ValueError(f"generated instance was parsed as {generated.variant}")
    return {
        "source": str(source),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "output": str(output),
        "seed": seed,
        "job_count": instance.job_count,
        "machine_count": instance.machine_count,
        "operation_count": sum(len(job.operations) for job in instance.jobs),
        "release_window": _release_window(instance),
        "max_job_release_time": max(job_releases),
        "max_machine_available_time": max(machine_available),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Append deterministic release-time rows to a standard FJSP instance.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(generate_release_time_variant(args.source, args.output, args.seed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
