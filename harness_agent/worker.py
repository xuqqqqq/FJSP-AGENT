"""Coding Worker 的算法无关协议和实验输入输出数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WorkerCapabilities:
    name: str
    supports_code_generation: bool
    supports_repair: bool
    supports_structured_output: bool


@dataclass(frozen=True)
class ExperimentSpec:
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
    status: str
    changed_files: list[str]
    summary: str
    raw_log_path: str | None = None
    artifacts: dict[str, str] | None = None


class CodingWorker(Protocol):
    def capabilities(self) -> WorkerCapabilities:
        ...

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        ...


class NullWorker:
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
