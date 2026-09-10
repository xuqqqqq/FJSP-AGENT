from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _grouped_batch_count(source: dict[str, Any], scheduled: dict[str, Any]) -> int:
    def timestamp(value: str) -> datetime:
        for pattern in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, pattern)
            except ValueError:
                continue
        raise ValueError("unsupported schedule timestamp")

    groups: Counter[tuple[str, datetime, datetime]] = Counter()
    for task_id, task in scheduled["task"].items():
        routes = source["task"][task_id]["process_path"]
        for seq, operation in task["process_path"].items():
            process = routes[str(operation["path_id"])]["process_list"][seq]
            if not bool(process.get("is_batch_type")):
                continue
            groups[(
                str(operation["temp_machine_id"]),
                timestamp(operation["process_start_time"]),
                timestamp(operation["process_finish_time"]),
            )] += 1
    return sum(size > 1 for size in groups.values())


def _report_errors(
    report: Any, *, instance: Path, solution: Path, total_tasks: int, horizon: int
) -> list[str]:
    if not isinstance(report, dict):
        return ["validator report must be a JSON object"]
    errors = []
    if report.get("validator") != "validate_batch_solution":
        errors.append("unexpected validator identity")
    for field, expected in (("input", instance), ("solution", solution)):
        value = report.get(field)
        if not isinstance(value, str) or Path(value).resolve() != expected:
            errors.append(f"validator report {field} does not match this evaluation")
    if type(report.get("horizon")) is not int or report["horizon"] != horizon:
        errors.append("validator report horizon does not match input")
    if not _count(report.get("error_count")):
        errors.append("validator report has no valid error_count")
    elif report["error_count"]:
        errors.append(f"validator reported {report['error_count']} constraint errors")
    details = report.get("errors")
    if not isinstance(details, list) or any(not isinstance(item, str) for item in details):
        errors.append("validator report errors must be a list of strings")
    elif details:
        errors.extend(details)
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        return [*errors, "validator report metrics must be a JSON object"]
    for field in ("total_tasks", "fully_scheduled_tasks", "valid_full_tasks"):
        if not _count(metrics.get(field)) or metrics[field] != total_tasks:
            errors.append(f"validator {field} does not cover all input tasks")
    if not _count(metrics.get("error_count")) or metrics["error_count"] != 0:
        errors.append("validator metrics contain errors or lack a valid error_count")
    for field in ("setup_count_positive", "completed_tasks_within_horizon", "scheduled_ops"):
        if not _count(metrics.get(field)):
            errors.append(f"validator metric {field} must be a nonnegative integer")
    completed = metrics.get("completed_tasks_within_horizon")
    if _count(completed) and completed > total_tasks:
        errors.append("completed task count exceeds input task count")
    if not _number(metrics.get("completed_weight_within_horizon")):
        errors.append("validator metric completed_weight_within_horizon must be finite and nonnegative")
    return errors


def evaluate(
    *,
    instance: Path,
    solution: Path,
    output: Path,
    validator_python: Path,
    validator_script: Path,
    expected_validator_sha256: str | None = None,
    expected_validator_dependency_sha256: str | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    instance, solution, output = instance.resolve(), solution.resolve(), output.resolve()
    validator_python, validator_script = validator_python.resolve(), validator_script.resolve()
    dependency = validator_script.with_name("rl_relaxed_solver.py")
    protected = (instance, solution, validator_script, validator_python, dependency)
    output.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    metrics: dict[str, Any] = {}
    evidence: dict[str, Any] = {"validator_script": str(validator_script)}
    raw_error_count = 0
    try:
        if output in protected:
            raise ValueError("output must not overwrite an evaluation input")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        source = json.loads(instance.read_text(encoding="utf-8-sig"), parse_constant=_reject_json_constant)
        tasks = source.get("task")
        if not isinstance(tasks, dict) or not tasks:
            raise ValueError("instance must contain nonempty task mapping")
        horizon = source.get("config", {}).get("max_output_horizon")
        if type(horizon) is not int:
            raise ValueError("instance must contain an integer max_output_horizon")
        hashes = {str(path): _sha256(path) for path in (instance, solution, validator_script, dependency)}
        evidence["input_sha256"] = hashes[str(instance)]
        evidence["solution_sha256"] = hashes[str(solution)]
        evidence["validator_sha256"] = hashes[str(validator_script)]
        evidence["validator_dependency_sha256"] = hashes[str(dependency)]
        for expected, path in (
            (expected_validator_sha256, validator_script),
            (expected_validator_dependency_sha256, dependency),
        ):
            if expected is not None and expected.lower() != hashes[str(path)]:
                raise ValueError("fixed validator hash mismatch")

        # A fresh report and separate caches prevent stale evidence and cross-lane cache races.
        with tempfile.TemporaryDirectory(prefix="industrial-validation-", dir=output.parent) as cache_dir:
            cache = Path(cache_dir)
            report_path = cache / "report.json"
            command = [
                str(validator_python), str(validator_script),
                "--input", str(instance), "--solution", str(solution),
                "--instance-cache", str(cache / "instance.pkl"),
                "--setup-db", str(cache / "setup.sqlite"),
                "--report", str(report_path), "--max-errors", "30",
            ]
            result = subprocess.run(
                command,
                cwd=validator_script.parent,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            evidence["validator_returncode"] = result.returncode
            if result.returncode != 0:
                errors.append(f"validator exited with code {result.returncode}")
            if not report_path.is_file():
                errors.append("validator did not produce a fresh report")
            else:
                artifact = output.with_name(f"{output.stem}.{cache.name}.validator-report.json")
                shutil.copyfile(report_path, artifact)
                evidence["validator_report"] = str(artifact)
                evidence["validator_report_sha256"] = _sha256(artifact)
                report = json.loads(report_path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
                errors.extend(_report_errors(
                    report, instance=instance, solution=solution, total_tasks=len(tasks), horizon=horizon
                ))
                if isinstance(report, dict):
                    raw_metrics = report.get("metrics")
                    if isinstance(raw_metrics, dict):
                        metrics = dict(raw_metrics)
                        metrics.pop("grouped_batch_count", None)
                    if _count(report.get("error_count")):
                        raw_error_count = report["error_count"]
            if not errors:
                scheduled_text = solution.read_text(encoding="utf-8-sig")
            if any(_sha256(Path(path)) != value for path, value in hashes.items()):
                errors.append("evaluation input or fixed validator changed during validation")
            if not errors:
                # Derive activation evidence only from a fully validated schedule, never solver diagnostics.
                scheduled = json.loads(scheduled_text, parse_constant=_reject_json_constant)
                metrics["grouped_batch_count"] = _grouped_batch_count(source, scheduled)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, subprocess.SubprocessError) as exc:
        # External process output is intentionally not echoed or copied into worker feedback.
        errors.append(f"industrial validation failed ({type(exc).__name__})")

    payload = {
        "valid": not errors,
        "error_count": max(raw_error_count, len(errors)),
        "errors": errors,
        "metrics": metrics,
        "evidence": evidence,
    }
    if output in protected:
        return payload
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge the fixed industrial JSON validator to Core.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--output", "--metrics", dest="output", required=True, type=Path)
    parser.add_argument("--validator-python", required=True, type=Path)
    parser.add_argument("--validator-script", required=True, type=Path)
    parser.add_argument("--expected-validator-sha256")
    parser.add_argument("--expected-validator-dependency-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()
    evaluate(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
