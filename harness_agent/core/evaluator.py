"""统一 evaluator 输出、合法性状态和目标值比较。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_agent.core.models import ObjectiveSpec


@dataclass(frozen=True)
class EvaluationResult:
    """从 evaluator metrics 文件读取的单次正式判卷结果。"""

    valid: bool
    error_count: int
    errors: list[str]
    metrics: dict[str, Any]
    status: str

    @staticmethod
    def from_metrics_file(path: Path, objectives: list[ObjectiveSpec]) -> "EvaluationResult":
        raw = json.loads(path.read_text(encoding="utf-8"))
        valid = bool(raw.get("valid", False))
        errors = [str(item) for item in raw.get("errors", [])]
        error_count = int(raw.get("error_count", len(errors)))
        metrics = dict(raw.get("metrics", {}))
        missing = [
            objective.name
            for objective in objectives
            if objective.invalid_if_missing and objective.name not in metrics
        ]
        if missing:
            valid = False
            errors.extend(f"missing required metric: {name}" for name in missing)
            error_count = max(error_count, len(errors))
        non_numeric = [
            objective.name
            for objective in objectives
            if objective.name in metrics and not isinstance(metrics[objective.name], (int, float))
        ]
        if non_numeric:
            valid = False
            errors.extend(f"non-numeric objective metric: {name}" for name in non_numeric)
            error_count = max(error_count, len(errors))
        return EvaluationResult(
            valid=valid,
            error_count=error_count,
            errors=errors,
            metrics=metrics,
            status="success" if valid else "failed_validation",
        )


def objective_key(result: EvaluationResult, objectives: list[ObjectiveSpec]) -> tuple[float, ...]:
    """统一为越大越好的目标 key；无效结果直接返回全 `-inf`。"""

    if not result.valid:
        return tuple(float("-inf") for _ in objectives)
    ordered = sorted(objectives, key=lambda item: item.priority)
    key: list[float] = []
    for objective in ordered:
        value = result.metrics.get(objective.name)
        if value is None:
            key.append(float("-inf"))
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            key.append(float("-inf"))
            continue
        if objective.threshold is not None:
            if objective.direction == "maximize" and numeric < objective.threshold:
                key.append(float("-inf"))
                continue
            if objective.direction == "minimize" and numeric > objective.threshold:
                key.append(float("-inf"))
                continue
        key.append(numeric if objective.direction == "maximize" else -numeric)
    return tuple(key)
