from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .graph_runner import GraphHarnessRunner
from .models import TaskContract, resolve_project_path
from .runner import RunSummary
from .worker import CodingWorker, ExperimentSpec, WorkerResult


@dataclass(frozen=True)
class WorkerCycleResult:
    worker_result: WorkerResult
    summary: RunSummary
    worktree_path: Path
    harness_output_dir: Path


def run_worker_cycle(
    *,
    contract: TaskContract,
    project_root: Path,
    output_dir: Path,
    context_packet_path: Path,
    worker: CodingWorker,
    experiment_id: str,
    max_steps: int,
    max_runtime_seconds: int,
    apply_worker_changes: bool,
) -> WorkerCycleResult:
    """Run one proposal/apply/evaluate cycle in an isolated candidate tree."""

    output_dir = output_dir.resolve()
    worktree_path = output_dir / "candidate_worktree"
    worker_output_dir = output_dir / "worker"
    harness_output_dir = output_dir / "harness"
    prepare_candidate_worktree(
        project_root=project_root.resolve(),
        contract=contract,
        worktree_path=worktree_path,
    )
    spec = ExperimentSpec(
        task_id=contract.task_id,
        experiment_id=experiment_id,
        context_packet_path=str(context_packet_path),
        worktree_path=str(worktree_path),
        max_steps=max_steps,
        max_runtime_seconds=max_runtime_seconds,
        output_dir=str(worker_output_dir),
        apply_changes=apply_worker_changes,
    )
    worker_result = worker.run_experiment(spec)
    runner = GraphHarnessRunner(contract=contract, project_root=worktree_path, output_dir=harness_output_dir)
    try:
        summary = runner.run()
    finally:
        runner.close()
    write_cycle_report(
        output_dir=output_dir,
        worker_result=worker_result,
        summary=summary,
        worktree_path=worktree_path,
        harness_output_dir=harness_output_dir,
    )
    return WorkerCycleResult(
        worker_result=worker_result,
        summary=summary,
        worktree_path=worktree_path,
        harness_output_dir=harness_output_dir,
    )


def prepare_candidate_worktree(*, project_root: Path, contract: TaskContract, worktree_path: Path) -> None:
    if worktree_path.exists():
        shutil.rmtree(worktree_path)
    worktree_path.mkdir(parents=True, exist_ok=True)
    allowed_paths = contract.paths.allowed_paths or ["."]
    forbidden_paths = set(contract.paths.forbidden_paths or [])
    forbidden_paths.update({".git", "outputs", "__pycache__", ".pytest_cache", ".mypy_cache"})
    if "." in allowed_paths:
        _copy_directory_contents(project_root, worktree_path, forbidden_paths)
        return
    for relative in allowed_paths:
        if not relative or relative in forbidden_paths:
            continue
        source = resolve_project_path(project_root, Path(relative))
        target = worktree_path / relative
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, target, ignore=_ignore_names(forbidden_paths), dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def write_cycle_report(
    *,
    output_dir: Path,
    worker_result: WorkerResult,
    summary: RunSummary,
    worktree_path: Path,
    harness_output_dir: Path,
) -> None:
    payload: dict[str, Any] = {
        "worker": {
            "status": worker_result.status,
            "changed_files": worker_result.changed_files,
            "summary": worker_result.summary,
            "raw_log_path": worker_result.raw_log_path,
            "artifacts": worker_result.artifacts or {},
        },
        "harness": {
            "total": summary.total,
            "valid": summary.valid,
            "failed": summary.failed,
            "best_experiment_id": summary.best_experiment_id,
            "best_metrics": summary.best_metrics,
            "best_candidate_id": summary.best_candidate_id,
            "best_candidate_metrics": summary.best_candidate_metrics,
        },
        "paths": {
            "worktree": str(worktree_path),
            "harness_output": str(harness_output_dir),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cycle_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Worker Cycle Report",
        "",
        "## Worker",
        "",
        f"- Status: `{worker_result.status}`",
        f"- Changed files: `{json.dumps(worker_result.changed_files, ensure_ascii=False)}`",
        f"- Summary: {worker_result.summary}",
        "",
        "## Harness",
        "",
        f"- Total: {summary.total}",
        f"- Valid: {summary.valid}",
        f"- Failed: {summary.failed}",
        f"- Best experiment: {summary.best_experiment_id or 'N/A'}",
        f"- Best metrics: `{json.dumps(summary.best_metrics, ensure_ascii=False)}`",
        f"- Best candidate: {summary.best_candidate_id or 'N/A'}",
        f"- Best candidate metrics: `{json.dumps(summary.best_candidate_metrics or {}, ensure_ascii=False)}`",
        "",
        "The worker result is not a success verdict. The harness metrics above are the Core evaluation result for this cycle.",
    ]
    (output_dir / "cycle_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_directory_contents(source_root: Path, target_root: Path, forbidden_paths: set[str]) -> None:
    for child in source_root.iterdir():
        if child.name in forbidden_paths:
            continue
        target = target_root / child.name
        if child.is_dir():
            shutil.copytree(child, target, ignore=_ignore_names(forbidden_paths), dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def _ignore_names(forbidden_paths: set[str]):
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in forbidden_paths or name.endswith(".pyc")}

    return ignore
