"""实验产物索引与可追溯证据序列化。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_NAMES = {
    "project_intake_manifest.json": "project_intake",
    "health_check_manifest.json": "health_check",
    "intent_alignment_manifest.json": "intent_alignment",
    "demo_manifest.json": "standard_demo",
    "suite_manifest.json": "benchmark_suite",
    "standard_worker_loop_manifest.json": "standard_worker_loop",
}


@dataclass(frozen=True)
class EvidenceIndexRequest:
    """Request for indexing previously generated loop-engineering evidence."""

    input_dirs: list[Path]
    output_dir: Path
    title: str = "Loop Engineering Evidence Index"


def build_evidence_index(request: EvidenceIndexRequest) -> dict[str, Any]:
    """发现多个输出目录中的正式 manifest，生成统一证据索引。"""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = discover_evidence_entries(request.input_dirs)
    index = {
        "schema_version": 1,
        "title": request.title,
        "input_dirs": [str(path) for path in request.input_dirs],
        "entry_count": len(entries),
        "summary": summarize_entries(entries),
        "entries": entries,
    }
    json_path = output_dir / "evidence_index.json"
    markdown_path = output_dir / "evidence_index.md"
    index["artifacts"] = {
        "json": str(json_path.resolve()),
        "markdown": str(markdown_path.resolve()),
    }
    json_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_evidence_index_markdown(index), encoding="utf-8")
    return index


def discover_evidence_entries(input_dirs: list[Path]) -> list[dict[str, Any]]:
    """按已知 manifest 文件名递归发现并去重实验产物。"""

    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for input_dir in input_dirs:
        root = input_dir.resolve()
        for name, entry_type in MANIFEST_NAMES.items():
            for manifest_path in sorted(root.rglob(name)):
                resolved = manifest_path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                entries.append(evidence_entry(manifest_path, entry_type))
    return sorted(entries, key=lambda item: (item["type"], item["manifest_path"]))


def evidence_entry(manifest_path: Path, entry_type: str) -> dict[str, Any]:
    """把一种 manifest 归一成索引公共字段，并检查引用产物是否缺失。"""

    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    artifacts = dict(payload.get("artifacts") or {})
    missing_artifacts = [
        {"name": name, "path": path}
        for name, path in sorted(artifacts.items())
        if path and not Path(str(path)).exists()
    ]
    entry = {
        "type": entry_type,
        "status": str(payload.get("status", "unknown")),
        "manifest_path": str(manifest_path.resolve()),
        "report_path": artifacts.get("report"),
        "missing_artifacts": missing_artifacts,
    }
    if entry_type == "standard_demo":
        entry.update(standard_demo_fields(payload))
    elif entry_type == "benchmark_suite":
        entry.update(benchmark_suite_fields(payload))
    elif entry_type == "standard_worker_loop":
        entry.update(standard_worker_loop_fields(payload))
    elif entry_type == "health_check":
        entry.update(health_check_fields(payload))
    elif entry_type == "intent_alignment":
        entry.update(intent_alignment_fields(payload))
    elif entry_type == "project_intake":
        entry.update(project_intake_fields(payload))
    return entry


def project_intake_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """提取项目扫描条目的索引字段。"""

    return {
        "primary_language": (payload.get("language_summary") or {}).get("primary_language"),
        "entry_file_count": len(payload.get("entry_files") or []),
        "core_file_count": len(payload.get("core_algorithm_files") or []),
        "benchmark_file_count": len(payload.get("benchmark_files") or []),
        "validator_file_count": len(payload.get("validator_files") or []),
        "risk_count": len(payload.get("risk_flags") or []),
        "valid_experiments": 0,
        "total_experiments": 0,
        "gap_metrics": {},
    }


def intent_alignment_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """提取目标一致性条目的索引字段。"""

    return {
        "ready_for_optimization": payload.get("ready_for_optimization"),
        "blocker_count": len(payload.get("blockers") or []),
        "warning_count": len(payload.get("warnings") or []),
        "valid_experiments": 0,
        "total_experiments": 0,
        "gap_metrics": {},
    }


def health_check_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """提取健康检查和稳定性条目的索引字段。"""

    quick_test = dict(payload.get("quick_test") or {})
    probe = dict(payload.get("stability_probe") or {})
    return {
        "quick_test_status": quick_test.get("status"),
        "stability_status": probe.get("status"),
        "stable": probe.get("stable"),
        "valid_experiments": probe.get("valid"),
        "total_experiments": probe.get("total"),
        "gap_metrics": {},
    }


def standard_demo_fields(payload: dict[str, Any]) -> dict[str, Any]:
    benchmark = dict(payload.get("benchmark_summary") or {})
    artifact_checks = dict(payload.get("artifact_checks") or {})
    return {
        "benchmark_summary": benchmark,
        "artifact_missing_count": len(artifact_checks.get("missing") or []),
        "valid_experiments": benchmark.get("valid_experiments"),
        "total_experiments": benchmark.get("total_experiments"),
        "gap_metrics": benchmark.get("gap_metrics") or {},
    }


def benchmark_suite_fields(payload: dict[str, Any]) -> dict[str, Any]:
    aggregate = dict(payload.get("aggregate") or {})
    return {
        "suite_count": payload.get("suite_count"),
        "aggregate": aggregate,
        "valid_experiments": aggregate.get("valid_experiments"),
        "total_experiments": aggregate.get("total_experiments"),
        "gap_metrics": {"avg_reported_gap_pct": aggregate.get("avg_reported_gap_pct")}
        if aggregate.get("avg_reported_gap_pct") is not None
        else {},
    }


def standard_worker_loop_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """提取闭环轮数、晋升和最终 key 等可比较字段。"""

    return {
        "round_count": payload.get("round_count"),
        "promoted_rounds": payload.get("promoted_rounds"),
        "improved": bool(payload.get("improved")),
        "baseline_key": payload.get("baseline_key"),
        "final_key": payload.get("final_key"),
        "valid_experiments": (payload.get("baseline_summary") or {}).get("valid"),
        "total_experiments": (payload.get("baseline_summary") or {}).get("total"),
        "gap_metrics": {},
    }


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """跨条目聚合状态、实验合法数、gap 和有效提升次数。"""

    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    total_experiments = 0
    valid_experiments = 0
    missing_artifact_count = 0
    gap_values: list[float] = []
    improved_worker_loops = 0
    for entry in entries:
        type_counts[entry["type"]] = type_counts.get(entry["type"], 0) + 1
        status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1
        total_experiments += int(entry.get("total_experiments") or 0)
        valid_experiments += int(entry.get("valid_experiments") or 0)
        missing_artifact_count += len(entry.get("missing_artifacts") or [])
        if entry["type"] == "standard_worker_loop" and entry.get("improved"):
            improved_worker_loops += 1
        for value in (entry.get("gap_metrics") or {}).values():
            if isinstance(value, (int, float)):
                gap_values.append(float(value))
    return {
        "type_counts": dict(sorted(type_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "total_experiments": total_experiments,
        "valid_experiments": valid_experiments,
        "missing_artifact_count": missing_artifact_count,
        "gap_metric_count": len(gap_values),
        "avg_gap_metric": sum(gap_values) / len(gap_values) if gap_values else None,
        "improved_worker_loops": improved_worker_loops,
    }


def render_evidence_index_markdown(index: dict[str, Any]) -> str:
    """将证据索引渲染为可点击的 Markdown 总表。"""

    summary = index.get("summary") or {}
    lines = [
        f"# {index.get('title') or 'Loop Engineering Evidence Index'}",
        "",
        f"- Entries: `{index.get('entry_count', 0)}`",
        f"- Type counts: `{json.dumps(summary.get('type_counts') or {}, ensure_ascii=False)}`",
        f"- Status counts: `{json.dumps(summary.get('status_counts') or {}, ensure_ascii=False)}`",
        f"- Total experiments: `{summary.get('total_experiments', 0)}`",
        f"- Valid experiments: `{summary.get('valid_experiments', 0)}`",
        f"- Missing referenced artifacts: `{summary.get('missing_artifact_count', 0)}`",
        f"- Average gap metric: `{summary.get('avg_gap_metric')}`",
        f"- Improved worker loops: `{summary.get('improved_worker_loops', 0)}`",
        "",
        "## Entries",
        "",
        "| Type | Status | Valid/Total | Gap Metrics | Worker Improvement | Report | Manifest |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for entry in index.get("entries", []):
        valid = entry.get("valid_experiments")
        total = entry.get("total_experiments")
        valid_total = f"{valid}/{total}" if valid is not None or total is not None else "N/A"
        report = entry.get("report_path")
        report_cell = f"[report]({report})" if report else "N/A"
        manifest = entry.get("manifest_path")
        manifest_cell = f"[manifest]({manifest})" if manifest else "N/A"
        improvement = entry.get("improved")
        improvement_cell = str(improvement) if improvement is not None else "N/A"
        lines.append(
            f"| {entry.get('type')} | {entry.get('status')} | {valid_total} | "
            f"`{json.dumps(entry.get('gap_metrics') or {}, ensure_ascii=False)}` | "
            f"{improvement_cell} | {report_cell} | {manifest_cell} |"
        )
    return "\n".join(lines).strip() + "\n"
