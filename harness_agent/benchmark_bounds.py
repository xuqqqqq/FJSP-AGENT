from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NAME_COLUMNS = ("instance", "instance_name", "name", "file", "filename", "id", "problem", "case", "author")
LOWER_COLUMNS = ("lb", "lower", "lower_bound", "lower bound", "lower bound (lb)")
UPPER_COLUMNS = (
    "ub",
    "upper",
    "upper_bound",
    "upper bound",
    "best",
    "best_known",
    "best_known_makespan",
    "best-known upper bound (ub/bks)",
    "ub/bks",
    "bks",
    "makespan",
)
OPTIMUM_COLUMNS = ("optimum", "optimal", "opt")


@dataclass(frozen=True)
class BenchmarkBounds:
    instance: str
    lower_bound: float | None = None
    upper_bound: float | None = None
    source: str | None = None
    note: str | None = None


def load_bounds_table(path: Path | None) -> dict[str, BenchmarkBounds]:
    """Load optional LB/UB benchmark references from a CSV table.

    Bounds files are intentionally treated as diagnostic metadata.  Missing or
    partial rows do not make an instance invalid; reports simply mark the
    unknown fields as N/A.
    """

    if path is None or not path.exists():
        return {}

    csv_text = read_csv_text(path)
    bounds: dict[str, BenchmarkBounds] = {}
    with io.StringIO(csv_text) as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames:
            for row in reader:
                values = {normalize_column(key): value for key, value in row.items() if key}
                raw_name = first_value(values, NAME_COLUMNS)
                if not raw_name:
                    continue
                entry = bounds_from_values(str(raw_name), values, path)
                if entry.lower_bound is None and entry.upper_bound is None:
                    continue
                for key in instance_name_keys(entry.instance):
                    bounds[key] = entry
            if bounds:
                return bounds

    with io.StringIO(csv_text) as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            name = str(row[0]).strip()
            if not name or name.lower() in {"instance", "name", "file"}:
                continue
            lower = parse_float(row[1])
            upper = parse_float(row[2]) if len(row) > 2 else None
            if lower is None and upper is None:
                continue
            entry = BenchmarkBounds(instance=name, lower_bound=lower, upper_bound=upper, source=str(path))
            for key in instance_name_keys(name):
                bounds[key] = entry
    return bounds


def find_bounds(bounds: dict[str, BenchmarkBounds], instance_name: str) -> BenchmarkBounds | None:
    for key in instance_name_keys(instance_name):
        entry = bounds.get(key)
        if entry is not None:
            return entry
    return None


def benchmark_family_label(instance_name: str) -> str:
    normalized = normalize_instance_name(instance_name)
    parts = normalized.split(".")
    if len(parts) > 2 and parts[0] == "fjsp":
        family = parts[1]
        if family == "barnes":
            return "BA"
        if family == "brandimarte":
            return "BR"
        if family == "dauzere":
            return "DP"
        if family == "hurink":
            return "HU"
        return family.upper()
    if normalized.startswith("oddla") or normalized.startswith("la"):
        return "HUdata"
    return instance_family_name(instance_name).upper()


def instance_family_name(instance_name: str) -> str:
    normalized = normalize_instance_name(instance_name)
    parts = normalized.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "fjsp" else parts[0]


def normalize_instance_name(name: str) -> str:
    normalized = str(name).strip().replace("\\", "/").split("/")[-1].lower()
    for suffix in (".txt", ".fjs", ".json"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def instance_name_keys(name: str) -> set[str]:
    normalized = normalize_instance_name(name)
    keys = {normalized}

    parts = normalized.split(".")
    if len(parts) > 2 and parts[0] == "fjsp":
        family = parts[1]
        case = parts[2]
        keys.add(case)
        if family == "dauzere" and case.endswith("a") and case[:-1].isdigit():
            index = int(case[:-1])
            keys.add(f"dpp{index:02d}")
            keys.add(f"dp{index:02d}")
        if family == "brandimarte":
            keys.add(case.lower())
        if family == "hurink" and "-" in case:
            prefix, base = case.split("-", 1)
            prefix_key = prefix[0] if prefix else ""
            if prefix_key:
                keys.add(f"{prefix_key}-{base}")
            if prefix == "sdata":
                keys.add(base)
        if family == "barnes":
            keys.add(case)

    if normalized.startswith("oddla") and normalized[5:].isdigit():
        keys.add("la" + normalized[5:])
    if normalized.startswith("la") and normalized[2:].isdigit():
        keys.add("oddla" + normalized[2:])
    return keys


def read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def bounds_from_values(raw_name: str, values: dict[str, str], path: Path) -> BenchmarkBounds:
    lower = first_number(values, LOWER_COLUMNS)
    upper = first_number(values, UPPER_COLUMNS)
    optimum = first_number(values, OPTIMUM_COLUMNS)
    if optimum is not None:
        lower = lower if lower is not None else optimum
        upper = upper if upper is not None else optimum
    return BenchmarkBounds(
        instance=raw_name,
        lower_bound=lower,
        upper_bound=upper,
        source=first_value(values, ("source", "source_url", "url")) or str(path),
        note=first_value(values, ("note", "notes", "remark")),
    )


def first_value(values: dict[str, str], columns: tuple[str, ...]) -> str | None:
    for column in columns:
        value = values.get(normalize_column(column))
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def first_number(values: dict[str, str], columns: tuple[str, ...]) -> float | None:
    for column in columns:
        parsed = parse_float(values.get(normalize_column(column)))
        if parsed is not None:
            return parsed
    return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def normalize_column(column: str) -> str:
    return str(column).strip().lower().replace("_", " ").replace("-", " ")
