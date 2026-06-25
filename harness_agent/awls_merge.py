from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .awls_benchmark import aggregate_awls_results, format_cell
from .awls_compare import read_manifest, rows_by_instance


@dataclass(frozen=True)
class AwlsMergeRequest:
    """Merge evaluated AWLS benchmark summaries into a best-of evidence artifact."""

    summaries: list[Path]
    output_dir: Path


def merge_awls_benchmarks(request: AwlsMergeRequest) -> dict[str, Any]:
    """Select the best valid per-instance result from multiple summary artifacts."""

    if len(request.summaries) < 2:
        raise ValueError("merge-awls-benchmarks requires at least two summary files")

    loaded = [(path, read_manifest(path)) for path in request.summaries]
    universe_names = primary_universe(loaded[0][1])
    source_rows = [(path, rows_by_instance(manifest)) for path, manifest in loaded]

    merged_rows: list[dict[str, Any]] = []
    selection_counts: dict[str, int] = {}
    for name in universe_names:
        candidates = [(index, path, rows.get(name)) for index, (path, rows) in enumerate(source_rows)]
        selected = select_best_candidate(name, candidates)
        source_label = str(selected["source_summary"])
        selection_counts[source_label] = selection_counts.get(source_label, 0) + 1
        merged_rows.append(selected)

    aggregate = aggregate_awls_results(merged_rows, merged_rows)
    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "awls_merged_summary.json"
    report_path = output_dir / "awls_merged_report.md"
    manifest = {
        "status": "ok" if aggregate["invalid_instance_count"] == 0 else "partial_failed",
        "merge_policy": "best_valid_makespan_on_primary_universe",
        "source_summaries": [str(path.resolve()) for path, _manifest in loaded],
        "selected_instance_names": universe_names,
        "instance_count": len(universe_names),
        "selection_counts": selection_counts,
        "aggregate": aggregate,
        "instances": merged_rows,
        "runs": merged_rows,
        "artifacts": {
            "summary": str(summary_path),
            "report": str(report_path),
        },
    }
    summary_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_merge_report(manifest), encoding="utf-8")
    return manifest


def primary_universe(manifest: dict[str, Any]) -> list[str]:
    selected = [str(name) for name in manifest.get("selected_instance_names") or [] if name]
    if selected:
        return selected
    return sorted(rows_by_instance(manifest))


def select_best_candidate(name: str, candidates: list[tuple[int, Path, dict[str, Any] | None]]) -> dict[str, Any]:
    valid_candidates: list[tuple[int, Path, dict[str, Any]]] = []
    fallback: tuple[int, Path, dict[str, Any]] | None = None
    for index, path, row in candidates:
        if row is None:
            continue
        copied = dict(row)
        if fallback is None:
            fallback = (index, path, copied)
        if copied.get("valid") and isinstance(copied.get("makespan"), (int, float)):
            valid_candidates.append((index, path, copied))

    if valid_candidates:
        index, path, row = min(valid_candidates, key=lambda item: (float(item[2]["makespan"]), item[0]))
    elif fallback is not None:
        index, path, row = fallback
    else:
        index = -1
        path = Path("<missing>")
        row = {
            "instance": name,
            "status": "missing",
            "valid": False,
            "error_count": 1,
            "errors": ["instance missing from all summaries"],
            "makespan": None,
            "gap_pct": None,
        }

    row = dict(row)
    row["instance"] = name
    row["source_index"] = index
    row["source_summary"] = str(path.resolve()) if index >= 0 else str(path)
    return row


def render_merge_report(manifest: dict[str, Any]) -> str:
    aggregate = manifest["aggregate"]
    lines = [
        "# AWLS Merged Benchmark Report",
        "",
        f"- Status: `{manifest['status']}`",
        f"- Merge policy: `{manifest['merge_policy']}`",
        f"- Instances: `{aggregate['instance_count']}`",
        f"- Valid instances: `{aggregate['valid_instance_count']}`",
        f"- Average gap pct: `{format_cell(aggregate['avg_gap_pct'])}`",
        f"- Median gap pct: `{format_cell(aggregate['median_gap_pct'])}`",
        f"- Max gap pct: `{format_cell(aggregate['max_gap_pct'])}`",
        f"- Best reached: `{aggregate['best_reached_count']}/{aggregate['gap_count']}`",
        "",
        "## Source Selection Counts",
        "",
    ]
    for source, count in sorted(manifest["selection_counts"].items()):
        lines.append(f"- `{source}`: `{count}`")
    lines.extend(
        [
            "",
            "## Selected Instance Results",
            "",
            "| Instance | Source | Valid | Makespan | Best Known | Gap % | Strategy |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in manifest["instances"]:
        lines.append(
            f"| {row.get('instance')} | `{Path(str(row.get('source_summary'))).name}` | `{row.get('valid')}` | "
            f"{format_cell(row.get('makespan'))} | {format_cell(row.get('best_known_makespan'))} | "
            f"{format_cell(row.get('gap_pct'))} | {format_strategy(row.get('strategy'))} |"
        )
    lines.extend(["", "## Aggregate JSON", "", f"```json\n{json.dumps(aggregate, ensure_ascii=False, indent=2)}\n```"])
    return "\n".join(lines).strip() + "\n"


def format_strategy(value: Any) -> str:
    if value is None:
        return "N/A"
    text = str(value).replace("|", "\\|")
    return f"`{text[:160]}`" if len(text) > 160 else f"`{text}`"
