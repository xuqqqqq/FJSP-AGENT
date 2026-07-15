"""任务契约构建：把需求、IO 与评测命令转换为可确认结构。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OBJECTIVE_CANDIDATES = [
    ("completed_weight", "maximize", ["产量", "完成重量", "completed_weight", "throughput"]),
    ("makespan", "minimize", ["makespan", "最大完工", "完工时间", "最大完成时间"]),
    ("setup_count", "minimize", ["setup", "切换次数", "换型"]),
    ("tardiness", "minimize", ["延期", "迟交", "tardiness"]),
    ("runtime_seconds", "minimize", ["运行时间", "runtime"]),
]

FEATURE_CANDIDATES = [
    ("flexible_job_shop", "core", ["FJSP", "柔性作业", "柔性车间", "候选机器", "多台机器可选"]),
    ("release_time", "constraint", ["释放时间", "release time", "最早到达", "最早可用"]),
    ("maximum_time_lag", "constraint", ["最大生产间隔", "最大间隔", "max lag", "maximum time lag"]),
    ("minimum_time_lag", "constraint", ["最小生产间隔", "最小间隔", "min lag", "minimum time lag"]),
    ("sequence_dependent_setup", "constraint", ["顺序相关切换", "切换矩阵", "sequence-dependent", "setup"]),
    ("reentrant_operations", "constraint", ["可重入", "reentrant", "多次重入"]),
    ("alternative_routes", "constraint", ["替代加工路径", "多条加工路径", "路径可选", "route choice"]),
    ("maintenance_windows", "constraint", ["维修", "维护", "maintenance", "不可用时间"]),
    ("cross_factory_transfer", "extension", ["跨厂", "转运", "transfer", "运输时间"]),
    ("job_priority", "extension", ["优先级", "priority"]),
    ("batch_processing", "extension", ["组批", "并行组批", "p-batch", "batch"]),
    ("decomposition_required", "challenge", ["大规模", "分解", "decomposition", "large-scale"]),
]

SECTION_ROLE_CANDIDATES = [
    ("objectives", ["目标", "objective", "指标", "metric", "评价"]),
    ("constraints", ["约束", "constraint", "限制", "合法", "可行"]),
    ("input_output", ["输入", "输出", "IO", "字段", "schema", "format"]),
    ("algorithm_guidance", ["算法", "强化学习", "heuristic", "prompt", "RL", "PPO", "DQN"]),
    ("instance_data", ["算例", "数据", "instance", "benchmark", "测试集"]),
    ("acceptance", ["验收", "交付", "acceptance", "标准", "对比"]),
]

REQUIRED_SOLVER_PLACEHOLDERS = ["{instance}", "{solution}"]
REQUIRED_EVALUATOR_PLACEHOLDERS = ["{instance}", "{solution}", "{metrics}"]


@dataclass(frozen=True)
class DraftSource:
    path: Path
    text: str


@dataclass(frozen=True)
class DraftObjective:
    name: str
    direction: str
    priority: int = 1
    invalid_if_missing: bool = True


@dataclass(frozen=True)
class DraftContractRequest:
    task_id: str
    docs: list[Path]
    instances: list[Path]
    output: Path
    problem_family: str | None = None
    objectives: list[str] = field(default_factory=list)
    solver_cmd: str | None = None
    evaluator_cmd: str | None = None
    quick_test_cmd: str | None = None
    rounds: int = 1
    seeds: list[int] = field(default_factory=lambda: [0])
    timeout_seconds: int = 300
    max_workers: int = 1
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=lambda: [".git", "outputs"])
    resources: list[str] = field(default_factory=list)


def build_draft_contract(request: DraftContractRequest) -> dict[str, Any]:
    """Build a review-required task contract draft from documents and CLI hints.

    This builder is intentionally conservative: it may infer obvious fields, but
    it records every heuristic choice and marks the result as requiring human
    confirmation before the harness can treat it as a formal evaluator contract.
    """

    sources = _read_sources(request.docs)
    joined_text = "\n\n".join(source.text for source in sources)
    source_references: list[dict[str, Any]] = []
    uncertain_fields: list[str] = []

    problem_family = request.problem_family or _infer_problem_family(joined_text, source_references)
    if not request.problem_family:
        uncertain_fields.append("problem_family")

    objectives = _parse_objective_overrides(request.objectives)
    if not objectives:
        objectives = _infer_objectives(joined_text, source_references)
    if not objectives:
        objectives = [DraftObjective(name="primary_score", direction="maximize", priority=1)]
        uncertain_fields.append("objectives")

    solver_cmd = request.solver_cmd or "python solver.py --input {instance} --output {solution} --seed {seed}"
    evaluator_cmd = request.evaluator_cmd or "python evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
    if request.solver_cmd is None:
        uncertain_fields.append("commands.solver")
    if request.evaluator_cmd is None:
        uncertain_fields.append("commands.evaluator")

    instance_items = _build_instances(request.instances)
    if not instance_items:
        uncertain_fields.append("instances")

    resources = _parse_resources(request.resources, uncertain_fields)
    description = _build_description(sources, joined_text)
    source_references.extend(_document_references(sources))
    feature_hints = _extract_problem_features(joined_text)
    metric_hints = _extract_metric_hints(joined_text)
    document_schema = _extract_document_schema(sources)
    command_checks = _command_template_checks(solver_cmd=solver_cmd, evaluator_cmd=evaluator_cmd)
    uncertain_fields.extend(check["field"] for check in command_checks if check["status"] == "missing_placeholder")

    return {
        "task_id": request.task_id,
        "problem_family": problem_family,
        "description": description,
        "instances": instance_items,
        "objectives": [
            {
                "name": objective.name,
                "direction": objective.direction,
                "priority": objective.priority,
                "invalid_if_missing": objective.invalid_if_missing,
            }
            for objective in objectives
        ],
        "commands": {
            "solver": solver_cmd,
            "evaluator": evaluator_cmd,
            "quick_test": request.quick_test_cmd or "python -m compileall .",
        },
        "budget": {
            "rounds": max(1, request.rounds),
            "seeds": request.seeds or [0],
            "timeout_seconds": max(1, request.timeout_seconds),
            "max_workers": max(1, request.max_workers),
        },
        "paths": {
            "allowed_paths": request.allowed_paths or ["."],
            "forbidden_paths": request.forbidden_paths or [".git", "outputs"],
        },
        "resources": resources,
        "review": {
            "status": "draft_requires_human_confirmation",
            "reason": (
                "This contract was generated from documents and CLI hints. "
                "Evaluator, validator, objectives, and command semantics must be confirmed before formal optimization."
            ),
            "uncertain_fields": sorted(set(uncertain_fields)),
            "source_documents": [str(path) for path in request.docs],
            "source_references": source_references,
            "document_statistics": _document_statistics(sources),
            "document_schema": document_schema,
            "extracted_problem_features": feature_hints,
            "metric_hints": metric_hints,
            "command_template_checks": command_checks,
            "confirmation_checklist": _confirmation_checklist(
                uncertain_fields=sorted(set(uncertain_fields)),
                feature_hints=feature_hints,
                metric_hints=metric_hints,
            ),
            "extraction_method": (
                "rule_based_source_grounding_v2: keyword and markdown-section evidence "
                "is used only to draft fields for review, not to create a formal evaluator contract."
            ),
        },
    }


def write_draft_contract(request: DraftContractRequest) -> Path:
    payload = build_draft_contract(request)
    request.output.parent.mkdir(parents=True, exist_ok=True)
    request.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    draft_review_report_path(request.output).write_text(render_draft_contract_review(payload), encoding="utf-8")
    return request.output


def draft_review_report_path(contract_path: Path) -> Path:
    """Return the deterministic Markdown review-card path for a draft contract."""

    return contract_path.with_suffix(".review.md")


def render_draft_contract_review(payload: dict[str, Any]) -> str:
    """Render draft-contract evidence as a human-reviewable Markdown card.

    The JSON contract remains the machine-readable source of truth.  This card
    is a reviewer convenience layer generated from the same payload so that
    source extraction, inferred metrics, and uncertain fields are easy to audit.
    """

    review = dict(payload.get("review") or {})
    objectives = list(payload.get("objectives") or [])
    instances = list(payload.get("instances") or [])
    command_checks = list(review.get("command_template_checks") or [])
    features = list(review.get("extracted_problem_features") or [])
    metrics = list(review.get("metric_hints") or [])
    checklist = list(review.get("confirmation_checklist") or [])
    schema = dict(review.get("document_schema") or {})

    lines = [
        "# Draft Contract Review",
        "",
        "本审核卡由 `draft-contract` 根据同一份 JSON 草稿自动生成，用于人工确认问题语义、",
        "评价指标、命令模板和文档抽取证据。它不是正式评价器结论；只有经过 `confirm-contract`",
        "确认后的契约才应作为正式优化依据。",
        "",
        "## Summary",
        "",
        f"- Task ID: `{payload.get('task_id', '')}`",
        f"- Problem family: `{payload.get('problem_family', '')}`",
        f"- Review status: `{review.get('status', '')}`",
        f"- Instance count: `{len(instances)}`",
        f"- Uncertain fields: {_format_inline_list(review.get('uncertain_fields') or [])}",
        f"- Extraction method: `{review.get('extraction_method', '')}`",
        "",
        "## Objectives",
        "",
        "| Name | Direction | Priority | Required |",
        "| --- | --- | ---: | --- |",
    ]
    if objectives:
        for objective in objectives:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(objective.get("name", "")),
                        _md_cell(objective.get("direction", "")),
                        _md_cell(objective.get("priority", "")),
                        _md_cell(objective.get("invalid_if_missing", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - |")

    lines.extend(
        [
            "",
            "## Command Template Checks",
            "",
            "| Field | Placeholder | Status |",
            "| --- | --- | --- |",
        ]
    )
    if command_checks:
        for check in command_checks:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(check.get("field", "")),
                        _md_cell(check.get("placeholder", "")),
                        _md_cell(check.get("status", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - |")

    lines.extend(
        [
            "",
            "## Extracted Problem Features",
            "",
            "| Feature | Category | Matched Pattern | Evidence |",
            "| --- | --- | --- | --- |",
        ]
    )
    if features:
        for feature in features:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(feature.get("name", "")),
                        _md_cell(feature.get("category", "")),
                        _md_cell(feature.get("matched_pattern", "")),
                        _md_cell(feature.get("evidence", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - |")

    lines.extend(
        [
            "",
            "## Metric Hints",
            "",
            "| Metric | Direction | Matched Pattern | Evidence |",
            "| --- | --- | --- | --- |",
        ]
    )
    if metrics:
        for metric in metrics:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(metric.get("metric", "")),
                        _md_cell(metric.get("direction", "")),
                        _md_cell(metric.get("matched_pattern", "")),
                        _md_cell(metric.get("evidence", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| - | - | - | - |")

    lines.extend(
        [
            "",
            "## Markdown Document Schema",
            "",
            "| Document | Lines | Heading | Roles | Feature Hints | Metric Hints | Evidence Excerpt |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    schema_rows = _document_schema_rows(schema)
    lines.extend(schema_rows or ["| - | - | - | - | - | - | - |"])

    lines.extend(["", "## Confirmation Checklist", ""])
    if checklist:
        for index, item in enumerate(checklist, start=1):
            lines.append(f"{index}. {item}")
    else:
        lines.append("1. Confirm evaluator, objective, and validity semantics before formal optimization.")

    lines.append("")
    return "\n".join(lines)


def write_confirmed_contract(
    *,
    contract_path: Path,
    output_path: Path,
    confirmed_by: str,
    note: str = "",
) -> Path:
    payload = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    payload["review"] = {
        **dict(payload.get("review", {})),
        "status": "human_confirmed",
        "confirmed_by": confirmed_by,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "confirmation_note": note,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def _read_sources(paths: list[Path]) -> list[DraftSource]:
    sources: list[DraftSource] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            text = ""
        sources.append(DraftSource(path=path, text=text))
    return sources


def _infer_problem_family(text: str, source_references: list[dict[str, Any]]) -> str:
    checks = [
        ("FJSP", [r"\bFJSP\b", "柔性作业", "柔性车间", "多台机器可选", "候选机器"]),
        ("JSP", [r"\bJSP\b", "作业车间"]),
        ("VRP", [r"\bVRP\b", "车辆路径"]),
    ]
    for family, patterns in checks:
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                source_references.append(
                    {
                        "field": "problem_family",
                        "value": family,
                        "evidence": _short_evidence(text, pattern),
                    }
                )
                return family
    return "GenericAlgorithm"


def _parse_objective_overrides(values: list[str]) -> list[DraftObjective]:
    objectives: list[DraftObjective] = []
    for index, raw in enumerate(values, start=1):
        parts = [part.strip() for part in raw.split(":")]
        if len(parts) < 2:
            raise ValueError(f"objective must use name:direction[:priority], got {raw!r}")
        name, direction = parts[0], parts[1].lower()
        if direction not in {"maximize", "minimize"}:
            raise ValueError(f"objective direction must be maximize/minimize, got {direction!r}")
        priority = int(parts[2]) if len(parts) >= 3 and parts[2] else index
        objectives.append(DraftObjective(name=name, direction=direction, priority=priority))
    return objectives


def _infer_objectives(text: str, source_references: list[dict[str, Any]]) -> list[DraftObjective]:
    objectives: list[DraftObjective] = []
    for name, direction, patterns in OBJECTIVE_CANDIDATES:
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                priority = len(objectives) + 1
                objectives.append(DraftObjective(name=name, direction=direction, priority=priority))
                source_references.append(
                    {
                        "field": "objectives",
                        "value": name,
                        "evidence": _short_evidence(text, pattern),
                    }
                )
                break
    return objectives


def _extract_metric_hints(text: str) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for name, direction, patterns in OBJECTIVE_CANDIDATES:
        for pattern in patterns:
            evidence = _short_evidence(text, pattern)
            if evidence:
                hints.append(
                    {
                        "metric": name,
                        "direction": direction,
                        "matched_pattern": pattern,
                        "evidence": evidence,
                    }
                )
                break
    return hints


def _extract_problem_features(text: str) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for name, category, patterns in FEATURE_CANDIDATES:
        for pattern in patterns:
            evidence = _short_evidence(text, pattern)
            if evidence:
                features.append(
                    {
                        "name": name,
                        "category": category,
                        "matched_pattern": pattern,
                        "evidence": evidence,
                    }
                )
                break
    return features


def _extract_document_schema(sources: list[DraftSource]) -> dict[str, Any]:
    """Parse lightweight Markdown structure for review and worker grounding."""

    documents = []
    total_sections = 0
    role_counts: dict[str, int] = {}
    for source in sources:
        sections = _markdown_sections(source)
        total_sections += len(sections)
        for section in sections:
            for role in section["roles"]:
                role_counts[role] = role_counts.get(role, 0) + 1
        documents.append(
            {
                "path": str(source.path),
                "section_count": len(sections),
                "sections": sections,
            }
        )
    return {
        "schema_version": 1,
        "document_count": len(sources),
        "section_count": total_sections,
        "role_counts": role_counts,
        "documents": documents,
    }


def _markdown_sections(source: DraftSource) -> list[dict[str, Any]]:
    lines = source.text.splitlines()
    headings: list[tuple[int, int, str]] = []
    in_fenced_code = False
    fence_marker = ""
    for line_number, line in enumerate(lines, start=1):
        fence_match = re.match(r"^\s*(```+|~~~+)", line)
        if fence_match:
            marker = fence_match.group(1)[:3]
            if not in_fenced_code:
                in_fenced_code = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fenced_code = False
                fence_marker = ""
            continue
        if in_fenced_code:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((line_number, len(match.group(1)), match.group(2).strip()))

    if not lines:
        return [
            _section_payload(
                source=source,
                heading="document",
                level=0,
                line_start=1,
                line_end=0,
                text="",
            )
        ]
    if not headings:
        return [
            _section_payload(
                source=source,
                heading="document",
                level=0,
                line_start=1,
                line_end=len(lines),
                text="\n".join(lines),
            )
        ]

    sections: list[dict[str, Any]] = []
    if headings[0][0] > 1:
        preamble_end = headings[0][0] - 1
        sections.append(
            _section_payload(
                source=source,
                heading="preamble",
                level=0,
                line_start=1,
                line_end=preamble_end,
                text="\n".join(lines[:preamble_end]),
            )
        )

    for index, (line_start, level, heading) in enumerate(headings):
        next_start = headings[index + 1][0] if index + 1 < len(headings) else len(lines) + 1
        line_end = max(line_start, next_start - 1)
        section_text = "\n".join(lines[line_start - 1 : line_end])
        sections.append(
            _section_payload(
                source=source,
                heading=heading,
                level=level,
                line_start=line_start,
                line_end=line_end,
                text=section_text,
            )
        )
    return sections


def _section_payload(
    *,
    source: DraftSource,
    heading: str,
    level: int,
    line_start: int,
    line_end: int,
    text: str,
) -> dict[str, Any]:
    return {
        "heading": heading,
        "level": level,
        "line_start": line_start,
        "line_end": line_end,
        "chars": len(text),
        "roles": _section_roles(heading, text),
        "feature_hints": [
            {
                "name": item["name"],
                "category": item["category"],
                "matched_pattern": item["matched_pattern"],
            }
            for item in _extract_problem_features(text)
        ],
        "metric_hints": [
            {
                "metric": item["metric"],
                "direction": item["direction"],
                "matched_pattern": item["matched_pattern"],
            }
            for item in _extract_metric_hints(text)
        ],
        "evidence_excerpt": re.sub(r"\s+", " ", text).strip()[:240],
        "source": str(source.path),
    }


def _section_roles(heading: str, text: str) -> list[str]:
    combined = f"{heading}\n{text}"
    roles = []
    for role, patterns in SECTION_ROLE_CANDIDATES:
        if any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in patterns):
            roles.append(role)
    return roles or ["general"]


def _command_template_checks(*, solver_cmd: str, evaluator_cmd: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for field, command, placeholders in [
        ("commands.solver", solver_cmd, REQUIRED_SOLVER_PLACEHOLDERS),
        ("commands.evaluator", evaluator_cmd, REQUIRED_EVALUATOR_PLACEHOLDERS),
    ]:
        for placeholder in placeholders:
            status = "ok" if placeholder in command else "missing_placeholder"
            checks.append(
                {
                    "field": field,
                    "placeholder": placeholder,
                    "status": status,
                    "command": command,
                }
            )
    return checks


def _confirmation_checklist(
    *,
    uncertain_fields: list[str],
    feature_hints: list[dict[str, Any]],
    metric_hints: list[dict[str, Any]],
) -> list[str]:
    checklist = [
        "Confirm that solver/evaluator command templates use the expected placeholders and produce the declared metrics.",
        "Confirm that every objective metric name matches the evaluator output JSON exactly.",
        "Confirm that validity semantics are owned by the evaluator/validator, not by the coding worker.",
    ]
    if uncertain_fields:
        checklist.append(f"Review uncertain fields: {', '.join(uncertain_fields)}.")
    if feature_hints:
        feature_names = ", ".join(item["name"] for item in feature_hints[:8])
        checklist.append(f"Confirm extracted problem features and required constraint switches: {feature_names}.")
    if metric_hints:
        metric_names = ", ".join(item["metric"] for item in metric_hints)
        checklist.append(f"Confirm objective priority and directions for inferred metrics: {metric_names}.")
    return checklist


def _document_schema_rows(schema: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for document in schema.get("documents") or []:
        document_path = str(document.get("path", ""))
        for section in document.get("sections") or []:
            line_start = section.get("line_start", "")
            line_end = section.get("line_end", "")
            feature_names = [str(item.get("name", "")) for item in section.get("feature_hints") or []]
            metric_names = [str(item.get("metric", "")) for item in section.get("metric_hints") or []]
            rows.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(document_path),
                        _md_cell(f"{line_start}-{line_end}"),
                        _md_cell(section.get("heading", "")),
                        _md_cell(", ".join(section.get("roles") or [])),
                        _md_cell(", ".join(name for name in feature_names if name) or "-"),
                        _md_cell(", ".join(name for name in metric_names if name) or "-"),
                        _md_cell(section.get("evidence_excerpt", "")),
                    ]
                )
                + " |"
            )
    return rows


def _format_inline_list(items: list[Any]) -> str:
    values = [str(item) for item in items if str(item)]
    if not values:
        return "-"
    return ", ".join(f"`{item}`" for item in values)


def _md_cell(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text.replace("|", "\\|") or "-"


def _build_instances(paths: list[Path]) -> list[dict[str, str]]:
    instances: list[dict[str, str]] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(item for item in path.iterdir() if item.is_file()):
                instances.append({"id": child.stem, "path": str(child)})
        else:
            instances.append({"id": path.stem, "path": str(path)})
    return instances


def _parse_resources(values: list[str], uncertain_fields: list[str]) -> dict[str, str]:
    resources: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            uncertain_fields.append(f"resources.{raw}")
            continue
        key, value = raw.split("=", 1)
        resources[key.strip()] = value.strip()
    return resources


def _build_description(sources: list[DraftSource], joined_text: str) -> str:
    heading = None
    for line in joined_text.splitlines():
        stripped = line.strip(" #\t")
        if stripped:
            heading = stripped
            break
    doc_names = ", ".join(source.path.name for source in sources) or "no source documents"
    if heading:
        return f"Draft contract generated from {doc_names}. First observed heading: {heading[:160]}"
    return f"Draft contract generated from {doc_names}."


def _document_references(sources: list[DraftSource]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for source in sources:
        non_empty = [line.strip() for line in source.text.splitlines() if line.strip()]
        references.append(
            {
                "field": "source_document",
                "value": str(source.path),
                "evidence": " ".join(non_empty[:3])[:240],
            }
        )
    return references


def _document_statistics(sources: list[DraftSource]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(source.path),
            "chars": len(source.text),
            "non_empty_lines": sum(1 for line in source.text.splitlines() if line.strip()),
        }
        for source in sources
    ]


def _short_evidence(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 80)
    return re.sub(r"\s+", " ", text[start:end]).strip()
