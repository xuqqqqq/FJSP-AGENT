from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
        },
    }


def write_draft_contract(request: DraftContractRequest) -> Path:
    payload = build_draft_contract(request)
    request.output.parent.mkdir(parents=True, exist_ok=True)
    request.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request.output


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
    candidates = [
        ("completed_weight", "maximize", ["产量", "完成重量", "completed_weight", "throughput"]),
        ("makespan", "minimize", ["makespan", "最大完工", "完工时间", "最大完成时间"]),
        ("setup_count", "minimize", ["setup", "切换次数", "换型"]),
        ("tardiness", "minimize", ["延期", "迟交", "tardiness"]),
        ("runtime_seconds", "minimize", ["运行时间", "runtime"]),
    ]
    objectives: list[DraftObjective] = []
    for name, direction, patterns in candidates:
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


def _short_evidence(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 80)
    return re.sub(r"\s+", " ", text[start:end]).strip()
