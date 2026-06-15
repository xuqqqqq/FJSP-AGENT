from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness_agent.standard_fjsp import load_solution, parse_standard_fjsp, validate_standard_schedule


NAME_COLUMNS = ("instance", "instance_name", "name", "file", "filename", "id", "problem", "case", "author")
BEST_COLUMNS = ("best", "best_known", "best_known_makespan", "ub", "upper_bound", "makespan", "optimum", "value")


def normalize_instance_name(name: str) -> str:
    normalized = str(name).strip().replace("\\", "/").split("/")[-1].lower()
    for suffix in (".txt", ".fjs", ".json"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def names_match(candidate: str, target: str) -> bool:
    normalized_candidate = normalize_instance_name(candidate)
    normalized_target = normalize_instance_name(target)
    return normalized_candidate == normalized_target


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def load_best_known(path: Path | None, instance_name: str) -> float | None:
    if path is None or not path.exists():
        return None
    csv_text = read_csv_text(path)
    with io.StringIO(csv_text) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            values = {str(key).strip().lower(): value for key, value in row.items() if key}
            name = next((values[column] for column in NAME_COLUMNS if values.get(column)), None)
            best = next((parse_float(values[column]) for column in BEST_COLUMNS if parse_float(values.get(column)) is not None), None)
            if name and best is not None and names_match(str(name), instance_name):
                return best
    with io.StringIO(csv_text) as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            name = row[0]
            best = parse_float(row[1])
            if best is not None and names_match(name, instance_name):
                return best
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and score standard FJSP schedule JSON.")
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--solution", required=True, type=Path)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--best-known-csv", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        instance = parse_standard_fjsp(args.instance)
        schedule = load_solution(args.solution)
        errors, metrics = validate_standard_schedule(instance, schedule)
        best_known = load_best_known(args.best_known_csv, instance.name)
        if best_known and best_known > 0:
            metrics["best_known_makespan"] = float(best_known)
            metrics["gap_pct"] = (metrics["makespan"] - best_known) / best_known * 100.0
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
