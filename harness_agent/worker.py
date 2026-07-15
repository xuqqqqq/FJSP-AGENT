"""Coding Worker 的算法无关协议和实验输入输出数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
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

    `context_packet_path` 提供只读任务知识，`worktree_path` 是本次候选
    唯一允许修改的隔离目录，`output_dir` 只保存 Worker 自身日志和产物。
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
