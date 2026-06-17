from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AwlsCompareRequest:
    """Compare two AWLS benchmark summaries instance by instance."""

    baseline_summary: Path
    candidate_summary: Path
    output_dir: Path


def compare_awls_benchmarks(request: AwlsCompareRequest) -> dict[str, Any]:
    """Write JSON and Markdown evidence comparing two AWLS benchmark runs."""

    baseline = read_manifest(request.baseline_summary)
    candidate = read_manifest(request.candidate_summary)
    baseline_rows = rows_by_instance(baseline)
    candidate_rows = rows_by_instance(candidate)
    all_names = sorted(set(baseline_rows) | set(candidate_rows))

    rows: list[dict[str, Any]] = []
    for name in all_names:
        base = baseline_rows.get(name)
        cand = candidate_rows.get(name)
        rows.append(compare_instance(name, base, cand))

    aggregate = aggregate_comparison(rows)
    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "awls_compare_summary.json"
    report_path = output_dir / "awls_compare_report.md"
    manifest = {
        "status": "ok",
        "baseline_summary": str(request.baseline_summary.resolve()),
        "candidate_summary": str(request.candidate_summary.resolve()),
        "aggregate": aggregate,
        "instances": rows,
        "artifacts": {
            "summary": str(summary_path),
            "report": str(report_path),
        },
    }
    summary_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_compare_report(manifest), encoding="utf-8")
    return manifest


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows_by_instance(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("instances") or []
    return {str(row.get("instance")): dict(row) for row in rows if row.get("instance")}


def compare_instance(name: str, base: dict[str, Any] | None, cand: dict[str, Any] | None) -> dict[str, Any]:
    baseline_makespan = numeric(base, "makespan")
    candidate_makespan = numeric(cand, "makespan")
    baseline_gap = numeric(base, "gap_pct")
    candidate_gap = numeric(cand, "gap_pct")
    delta_makespan = None
    delta_gap = None
    outcome = "missing"
    if base is None:
        outcome = "candidate_only"
    elif cand is None:
        outcome = "baseline_only"
    elif not base.get("valid") or not cand.get("valid"):
        outcome = "invalid"
    elif baseline_makespan is not None and candidate_makespan is not None:
        delta_makespan = candidate_makespan - baseline_makespan
        if candidate_makespan < baseline_makespan:
            outcome = "improved"
        elif candidate_makespan > baseline_makespan:
            outcome = "worsened"
        else:
            outcome = "tied"
    if baseline_gap is not None and candidate_gap is not None:
        delta_gap = candidate_gap - baseline_gap

    return {
        "instance": name,
        "outcome": outcome,
        "baseline_valid": bool(base.get("valid")) if base is not None else None,
        "candidate_valid": bool(cand.get("valid")) if cand is not None else None,
        "baseline_makespan": baseline_makespan,
        "candidate_makespan": candidate_makespan,
        "delta_makespan": delta_makespan,
        "baseline_gap_pct": baseline_gap,
        "candidate_gap_pct": candidate_gap,
        "delta_gap_pct": delta_gap,
    }


def aggregate_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    common = [row for row in rows if row["outcome"] not in {"baseline_only", "candidate_only", "missing"}]
    comparable = [row for row in common if row.get("delta_makespan") is not None]
    baseline_gaps = [row["baseline_gap_pct"] for row in common if isinstance(row.get("baseline_gap_pct"), (int, float))]
    candidate_gaps = [row["candidate_gap_pct"] for row in common if isinstance(row.get("candidate_gap_pct"), (int, float))]
    delta_gaps = [row["delta_gap_pct"] for row in common if isinstance(row.get("delta_gap_pct"), (int, float))]
    return {
        "instance_count": len(rows),
        "common_count": len(common),
        "comparable_count": len(comparable),
        "improved_count": sum(1 for row in rows if row["outcome"] == "improved"),
        "worsened_count": sum(1 for row in rows if row["outcome"] == "worsened"),
        "tied_count": sum(1 for row in rows if row["outcome"] == "tied"),
        "invalid_count": sum(1 for row in rows if row["outcome"] == "invalid"),
        "baseline_only_count": sum(1 for row in rows if row["outcome"] == "baseline_only"),
        "candidate_only_count": sum(1 for row in rows if row["outcome"] == "candidate_only"),
        "baseline_avg_gap_pct": sum(baseline_gaps) / len(baseline_gaps) if baseline_gaps else None,
        "candidate_avg_gap_pct": sum(candidate_gaps) / len(candidate_gaps) if candidate_gaps else None,
        "delta_avg_gap_pct": sum(delta_gaps) / len(delta_gaps) if delta_gaps else None,
        "best_delta_makespan": min((row["delta_makespan"] for row in comparable), default=None),
        "worst_delta_makespan": max((row["delta_makespan"] for row in comparable), default=None),
    }


def render_compare_report(manifest: dict[str, Any]) -> str:
    aggregate = manifest["aggregate"]
    lines = [
        "# AWLS Benchmark Comparison Report",
        "",
        f"- Common instances: `{aggregate['common_count']}`",
        f"- Improved / tied / worsened: `{aggregate['improved_count']}` / `{aggregate['tied_count']}` / `{aggregate['worsened_count']}`",
        f"- Baseline avg gap pct: `{format_cell(aggregate['baseline_avg_gap_pct'])}`",
        f"- Candidate avg gap pct: `{format_cell(aggregate['candidate_avg_gap_pct'])}`",
        f"- Candidate minus baseline avg gap pct: `{format_cell(aggregate['delta_avg_gap_pct'])}`",
        f"- Baseline-only / candidate-only: `{aggregate['baseline_only_count']}` / `{aggregate['candidate_only_count']}`",
        "",
        "## Instance Delta",
        "",
        "| Instance | Outcome | Baseline | Candidate | Delta | Baseline Gap % | Candidate Gap % | Delta Gap % |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    sorted_rows = sorted(
        manifest["instances"],
        key=lambda row: (
            row.get("delta_gap_pct") if isinstance(row.get("delta_gap_pct"), (int, float)) else 0.0,
            row["instance"],
        ),
        reverse=True,
    )
    for row in sorted_rows:
        lines.append(
            f"| {row['instance']} | `{row['outcome']}` | {format_cell(row['baseline_makespan'])} | "
            f"{format_cell(row['candidate_makespan'])} | {format_cell(row['delta_makespan'])} | "
            f"{format_cell(row['baseline_gap_pct'])} | {format_cell(row['candidate_gap_pct'])} | "
            f"{format_cell(row['delta_gap_pct'])} |"
        )
    lines.extend(["", "## Aggregate JSON", "", f"```json\n{json.dumps(aggregate, ensure_ascii=False, indent=2)}\n```"])
    return "\n".join(lines).strip() + "\n"


def numeric(row: dict[str, Any] | None, key: str) -> float | None:
    if row is None:
        return None
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def format_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value is None:
        return "N/A"
    return str(value)
