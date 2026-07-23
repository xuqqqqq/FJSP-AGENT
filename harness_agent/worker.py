"""Coding Worker 的算法无关协议和实验输入输出数据结构。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import Protocol


@dataclass(frozen=True)
class WorkerCapabilities:
    """Worker 向编排层声明的能力，不代表某次候选已经成功。

    编排层据此决定是否允许代码生成和同轮修补；真正的代码合法性与
    算法效果仍分别由 JA、Semantic Reviewer 和固定 Core 判定。
    """

    name: str
    supports_code_generation: bool
    supports_repair: bool
    supports_structured_output: bool


@dataclass(frozen=True)
class ExperimentSpec:
    """编排层传给一次 Coding Worker 调用的最小运行上下文。

    `worker_assignment_path` 是 Coding Worker 的唯一规划输入；
    `context_packet_path` 仅为兼容其他受信审查层保留，隔离型 Worker 不得
    读取。`worktree_path` 是本次候选唯一允许修改的隔离目录，`output_dir`
    只保存 Worker 自身日志和产物。
    `max_steps`/`max_runtime_seconds` 限制的是写代码阶段，不是 Core 的
    solver benchmark 时间。
    """

    task_id: str
    experiment_id: str
    context_packet_path: str
    worktree_path: str
    max_steps: int
    max_runtime_seconds: int
    output_dir: str | None = None
    apply_changes: bool = False
    worker_assignment_path: str | None = None


@dataclass(frozen=True)
class WorkerAssignment:
    """Main Agent 签发给 Coding Worker 的最小可执行任务书。

    WorkerAssignment 是 Main Agent 与 Coding Worker 之间唯一允许共享的
    规划产物。完整 Context Packet、方法目录、历史轮次和经验记忆不得通过
    这个结构透传；``target_file`` 是任务书单独显式授权的可读写路径，其他
    普通源码或知识必须逐项出现在 ``read_set`` 中。获准的 Worker
    Implementation Skill 只以受信 ID 出现在 ``implementation_skills``，由
    Harness 单独镜像并通过 OpenCode Skill 权限加载。
    """

    assignment_id: str
    direction_id: str
    mode: str
    target_file: str
    objective: str
    method_package: dict[str, Any]
    read_set: list[dict[str, Any]]
    deliverables: list[dict[str, Any]]
    implementation_order: list[str]
    preserve: list[str]
    forbidden: list[str]
    latest_feedback: dict[str, Any]
    checks: list[str]
    budgets: dict[str, Any]
    completion_rule: str
    lineage: dict[str, Any]
    runtime_contract: dict[str, Any]
    implementation_skills: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assignment_id": self.assignment_id,
            "direction_id": self.direction_id,
            "mode": self.mode,
            "target_file": self.target_file,
            "objective": self.objective,
            "method_package": self.method_package,
            "read_set": self.read_set,
            "deliverables": self.deliverables,
            "implementation_order": self.implementation_order,
            "preserve": self.preserve,
            "forbidden": self.forbidden,
            "latest_feedback": self.latest_feedback,
            "checks": self.checks,
            "budgets": self.budgets,
            "completion_rule": self.completion_rule,
            "lineage": self.lineage,
            "runtime_contract": self.runtime_contract,
            "implementation_skills": self.implementation_skills,
        }

    @classmethod
    def from_payload(cls, value: dict[str, Any]) -> "WorkerAssignment":
        if not isinstance(value, dict):
            raise ValueError("worker assignment must be a JSON object")
        assignment = cls(
            schema_version=int(value.get("schema_version") or 1),
            assignment_id=str(value.get("assignment_id") or "").strip(),
            direction_id=str(value.get("direction_id") or "").strip(),
            mode=str(value.get("mode") or "").strip(),
            target_file=str(value.get("target_file") or "").strip().replace("\\", "/"),
            objective=str(value.get("objective") or "").strip(),
            method_package=dict(value.get("method_package") or {}),
            read_set=[dict(item) for item in value.get("read_set") or [] if isinstance(item, dict)],
            deliverables=[dict(item) for item in value.get("deliverables") or [] if isinstance(item, dict)],
            implementation_order=[str(item) for item in value.get("implementation_order") or [] if str(item).strip()],
            preserve=[str(item) for item in value.get("preserve") or [] if str(item).strip()],
            forbidden=[str(item) for item in value.get("forbidden") or [] if str(item).strip()],
            latest_feedback=dict(value.get("latest_feedback") or {}),
            checks=[str(item) for item in value.get("checks") or [] if str(item).strip()],
            budgets=dict(value.get("budgets") or {}),
            completion_rule=str(value.get("completion_rule") or "").strip(),
            lineage=dict(value.get("lineage") or {}),
            runtime_contract=dict(value.get("runtime_contract") or {}),
            implementation_skills=[
                dict(item)
                for item in value.get("implementation_skills") or []
                if isinstance(item, dict)
            ],
        )
        errors = assignment.validate()
        if errors:
            raise ValueError("invalid worker assignment: " + "; ".join(errors))
        return assignment

    @classmethod
    def load(cls, path: Path) -> "WorkerAssignment":
        return cls.from_payload(json.loads(path.read_text(encoding="utf-8")))

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.schema_version != 1:
            errors.append(f"unsupported schema_version: {self.schema_version}")
        if not self.assignment_id:
            errors.append("assignment_id is required")
        if not self.direction_id:
            errors.append("direction_id is required")
        if self.mode not in {"baseline", "improvement", "repair"}:
            errors.append(f"unsupported mode: {self.mode or '(empty)'}")
        target = Path(self.target_file)
        if not self.target_file or target.is_absolute() or ".." in target.parts:
            errors.append("target_file must be a safe relative path")
        if not self.objective:
            errors.append("objective is required")
        if not self.read_set:
            errors.append("read_set must name the bounded worker inputs")
        seen_read_paths: set[str] = set()
        for index, item in enumerate(self.read_set):
            read_path = str(item.get("path") or "").strip().replace("\\", "/")
            parsed_read_path = Path(read_path)
            if not read_path or parsed_read_path.is_absolute() or ".." in parsed_read_path.parts:
                errors.append(f"read_set[{index}].path must be a safe relative path")
            if read_path in seen_read_paths:
                errors.append(f"read_set contains duplicate path: {read_path}")
            seen_read_paths.add(read_path)
            if not str(item.get("role") or "").strip():
                errors.append(f"read_set[{index}].role is required")
        if not self.deliverables:
            errors.append("deliverables must not be empty")
        seen_skill_ids: set[str] = set()
        for index, item in enumerate(self.implementation_skills):
            skill_id = str(item.get("skill_id") or "").strip()
            sandbox_path = str(item.get("sandbox_path") or "").strip().replace("\\", "/")
            if not skill_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in skill_id):
                errors.append(f"implementation_skills[{index}].skill_id is invalid")
            if skill_id in seen_skill_ids:
                errors.append(f"implementation_skills contains duplicate skill_id: {skill_id}")
            seen_skill_ids.add(skill_id)
            if sandbox_path != f".opencode/skills/{skill_id}":
                errors.append(f"implementation_skills[{index}].sandbox_path must match its skill_id")
            if "source_path" in item:
                errors.append(f"implementation_skills[{index}] must not expose source_path")
        if len(self.implementation_skills) > 8:
            errors.append("implementation_skills exceeds the bounded limit of 8")
        if not self.completion_rule:
            errors.append("completion_rule is required")
        try:
            max_steps = int(self.budgets.get("max_edit_steps") or 0)
            max_runtime = int(self.budgets.get("max_runtime_seconds") or 0)
        except (TypeError, ValueError):
            max_steps = 0
            max_runtime = 0
        if max_steps <= 0 or max_runtime <= 0:
            errors.append("budgets must contain positive max_edit_steps and max_runtime_seconds")
        return errors


@dataclass(frozen=True)
class WorkerResult:
    """Coding Worker 的过程结果，而不是候选算法的验收结论。

    `status` 只描述 Worker 是否正常完成；`changed_files` 还会由候选
    worktree 的真实快照差异补充。即使这里是 completed/applied，候选也
    必须继续通过 JA、evaluator 和 promotion check。
    """

    status: str
    changed_files: list[str]
    summary: str
    raw_log_path: str | None = None
    artifacts: dict[str, str] | None = None


class CodingWorker(Protocol):
    """所有 Coding Agent 运行时都必须实现的算法无关接口。"""

    def capabilities(self) -> WorkerCapabilities:
        ...

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        ...


class NullWorker:
    """不生成代码的占位实现，用于检查编排和报告链路。"""

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="null",
            supports_code_generation=False,
            supports_repair=False,
            supports_structured_output=True,
        )

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        return WorkerResult(
            status="skipped",
            changed_files=[],
            summary="NullWorker does not modify code.",
        )
