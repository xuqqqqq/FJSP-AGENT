from __future__ import annotations

import difflib
import hashlib
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
    delta_path: Path
    patch_path: Path


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
    before_snapshot = collect_worktree_snapshot(worktree_path)
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
    after_snapshot = collect_worktree_snapshot(worktree_path)
    delta = compute_worktree_delta(before_snapshot, after_snapshot)
    delta_path, patch_path = write_worktree_delta_artifacts(
        root=worktree_path,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        delta=delta,
        output_dir=output_dir,
    )
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
        delta=delta,
        delta_path=delta_path,
        patch_path=patch_path,
    )
    return WorkerCycleResult(
        worker_result=worker_result,
        summary=summary,
        worktree_path=worktree_path,
        harness_output_dir=harness_output_dir,
        delta_path=delta_path,
        patch_path=patch_path,
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
    delta: dict[str, Any],
    delta_path: Path,
    patch_path: Path,
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
            "worktree_delta": str(delta_path),
            "worktree_patch": str(patch_path),
        },
        "worktree_delta": delta,
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
        f"- Worktree delta: `{json.dumps(delta.get('counts', {}), ensure_ascii=False)}`",
        f"- Delta artifact: `{delta_path}`",
        f"- Patch artifact: `{patch_path}`",
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


def collect_worktree_snapshot(root: Path, max_text_bytes: int = 200_000) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _is_generated_cache_path(path):
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        entry: dict[str, Any] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        if len(data) <= max_text_bytes and b"\x00" not in data:
            entry["_text"] = data.decode("utf-8", errors="replace")
        snapshot[relative] = entry
    return snapshot


def compute_worktree_delta(
    before_snapshot: dict[str, dict[str, Any]],
    after_snapshot: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    before_paths = set(before_snapshot)
    after_paths = set(after_snapshot)
    added = sorted(after_paths - before_paths)
    deleted = sorted(before_paths - after_paths)
    modified = sorted(
        path
        for path in before_paths & after_paths
        if before_snapshot[path].get("sha256") != after_snapshot[path].get("sha256")
    )
    return {
        "counts": {
            "added": len(added),
            "modified": len(modified),
            "deleted": len(deleted),
            "total_changed": len(added) + len(modified) + len(deleted),
        },
        "added": [{"path": path, **_public_snapshot_entry(after_snapshot[path])} for path in added],
        "modified": [
            {
                "path": path,
                "before": _public_snapshot_entry(before_snapshot[path]),
                "after": _public_snapshot_entry(after_snapshot[path]),
            }
            for path in modified
        ],
        "deleted": [{"path": path, **_public_snapshot_entry(before_snapshot[path])} for path in deleted],
    }


def write_worktree_delta_artifacts(
    *,
    root: Path,
    before_snapshot: dict[str, dict[str, Any]],
    after_snapshot: dict[str, dict[str, Any]],
    delta: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    delta_path = output_dir / "worker_worktree_delta.json"
    patch_path = output_dir / "worker_changes.patch"
    delta_path.write_text(json.dumps(delta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    patch_path.write_text(
        render_worktree_patch(
            root=root,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            delta=delta,
        ),
        encoding="utf-8",
    )
    return delta_path, patch_path


def render_worktree_patch(
    *,
    root: Path,
    before_snapshot: dict[str, dict[str, Any]],
    after_snapshot: dict[str, dict[str, Any]],
    delta: dict[str, Any],
    max_file_bytes: int = 200_000,
) -> str:
    paths = [item["path"] for item in delta.get("added", [])]
    paths.extend(item["path"] for item in delta.get("modified", []))
    paths.extend(item["path"] for item in delta.get("deleted", []))
    lines: list[str] = []
    for relative in sorted(paths):
        before_text = _snapshot_text(root=root, relative=relative, snapshot=before_snapshot, max_file_bytes=max_file_bytes)
        after_text = _snapshot_text(root=root, relative=relative, snapshot=after_snapshot, max_file_bytes=max_file_bytes)
        if before_text is None or after_text is None:
            lines.extend(
                [
                    f"diff -- {relative}",
                    f"# binary or oversized file omitted from text patch: {relative}",
                    "",
                ]
            )
            continue
        before_lines = before_text.splitlines(keepends=True)
        after_lines = after_text.splitlines(keepends=True)
        lines.extend(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                lineterm="",
            )
        )
        if lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _snapshot_text(
    *,
    root: Path,
    relative: str,
    snapshot: dict[str, dict[str, Any]],
    max_file_bytes: int,
) -> str | None:
    if relative not in snapshot:
        return ""
    if int(snapshot[relative].get("size", 0)) > max_file_bytes:
        return None
    text = snapshot[relative].get("_text")
    return text if isinstance(text, str) else None


def _public_snapshot_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def _is_generated_cache_path(path: Path) -> bool:
    generated_dirs = {"__pycache__", ".pytest_cache", ".mypy_cache"}
    return path.suffix == ".pyc" or any(part in generated_dirs for part in path.parts)


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
