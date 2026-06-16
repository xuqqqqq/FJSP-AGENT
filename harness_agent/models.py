from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Direction = Literal["maximize", "minimize"]


@dataclass(frozen=True)
class ObjectiveSpec:
    name: str
    direction: Direction
    priority: int = 1
    invalid_if_missing: bool = True
    threshold: float | None = None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ObjectiveSpec":
        direction = data.get("direction")
        if direction not in {"maximize", "minimize"}:
            raise ValueError(f"objective {data.get('name')!r} has invalid direction: {direction!r}")
        return ObjectiveSpec(
            name=str(data["name"]),
            direction=direction,
            priority=int(data.get("priority", 1)),
            invalid_if_missing=bool(data.get("invalid_if_missing", True)),
            threshold=data.get("threshold"),
        )


@dataclass(frozen=True)
class InstanceSpec:
    id: str
    path: Path

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "InstanceSpec":
        return InstanceSpec(id=str(data["id"]), path=Path(str(data["path"])))


@dataclass(frozen=True)
class CommandSpec:
    solver: str
    evaluator: str
    quick_test: str | None = None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CommandSpec":
        return CommandSpec(
            solver=str(data["solver"]),
            evaluator=str(data["evaluator"]),
            quick_test=str(data["quick_test"]) if data.get("quick_test") else None,
        )


@dataclass(frozen=True)
class BudgetSpec:
    rounds: int = 1
    seeds: list[int] = field(default_factory=lambda: [0])
    timeout_seconds: int = 300
    max_workers: int = 1

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "BudgetSpec":
        return BudgetSpec(
            rounds=int(data.get("rounds", 1)),
            seeds=[int(seed) for seed in data.get("seeds", [0])],
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            max_workers=max(1, int(data.get("max_workers", 1))),
        )


@dataclass(frozen=True)
class PathPolicy:
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PathPolicy":
        return PathPolicy(
            allowed_paths=[str(path) for path in data.get("allowed_paths", [])],
            forbidden_paths=[str(path) for path in data.get("forbidden_paths", [])],
        )


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    problem_family: str
    description: str
    instances: list[InstanceSpec]
    objectives: list[ObjectiveSpec]
    commands: CommandSpec
    budget: BudgetSpec
    paths: PathPolicy
    resources: dict[str, Path]
    source_path: Path
    review: dict[str, Any]

    @staticmethod
    def load(path: Path) -> "TaskContract":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return TaskContract(
            task_id=str(raw["task_id"]),
            problem_family=str(raw.get("problem_family", "FJSP")),
            description=str(raw.get("description", "")),
            instances=[InstanceSpec.from_dict(item) for item in raw.get("instances", [])],
            objectives=[ObjectiveSpec.from_dict(item) for item in raw.get("objectives", [])],
            commands=CommandSpec.from_dict(raw["commands"]),
            budget=BudgetSpec.from_dict(raw.get("budget", {})),
            paths=PathPolicy.from_dict(raw.get("paths", {})),
            resources={str(key): Path(str(value)) for key, value in raw.get("resources", {}).items()},
            source_path=path,
            review=dict(raw.get("review", {})),
        )

    @property
    def review_status(self) -> str:
        return str(self.review.get("status", "confirmed"))

    @property
    def requires_human_confirmation(self) -> bool:
        return self.review_status == "draft_requires_human_confirmation"

    def validate(self, project_root: Path) -> list[str]:
        errors: list[str] = []
        if not self.instances:
            errors.append("contract.instances must not be empty")
        if not self.objectives:
            errors.append("contract.objectives must not be empty")
        if self.budget.rounds <= 0:
            errors.append("contract.budget.rounds must be positive")
        if not self.budget.seeds:
            errors.append("contract.budget.seeds must not be empty")
        for instance in self.instances:
            instance_path = resolve_project_path(project_root, instance.path)
            if not instance_path.exists():
                errors.append(f"instance {instance.id!r} path does not exist: {instance.path}")
        for name, resource_path in self.resources.items():
            resolved_resource = resolve_project_path(project_root, resource_path)
            if not resolved_resource.exists():
                errors.append(f"resource {name!r} path does not exist: {resource_path}")
        return errors


def resolve_project_path(project_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return (project_root / path).resolve()
