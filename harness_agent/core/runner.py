"""固定 Core 运行器：执行 solver/evaluator 契约并汇总可复验指标。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from harness_agent.core.cancellation import CancellationToken, TaskCancelled
from harness_agent.core.evaluator import EvaluationResult, objective_key
from harness_agent.core.ledger import ExperimentLedger, ExperimentRecord
from harness_agent.core.models import TaskContract, resolve_project_path


CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
SUBPROCESS_ERROR_EXCERPT_MAX_CHARS = 900
SOLVER_DIAGNOSTICS_MAX_CHARS = 32_000
SOLVER_DIAGNOSTICS_MAX_DEPTH = 6
SOLVER_DIAGNOSTICS_MAX_ITEMS = 200
ACTIVATION_DIAGNOSTICS_MAX_CHARS = 8_000


@dataclass(frozen=True)
class RunSummary:
    """一批 Core 实验的聚合结果；保留合法率、最佳候选和错误分类。"""

    total: int
    valid: int
    failed: int
    best_experiment_id: str | None
    best_metrics: dict[str, object]
    best_candidate_id: str | None = None
    best_candidate_metrics: dict[str, object] | None = None
    candidate_summaries: list[dict[str, object]] | None = None
    pareto_frontier: list[dict[str, object]] | None = None
    validation_summary: dict[str, object] | None = None
    activation_evidence: list[dict[str, object]] | None = None


class HarnessRunner:
    """并行执行契约实验；不包含任何 FJSP 搜索算法。"""

    def __init__(
        self,
        contract: TaskContract,
        project_root: Path,
        output_dir: Path,
        cancellation: CancellationToken | None = None,
    ) -> None:
        self.contract = contract
        self.project_root = project_root.resolve()
        self.output_dir = output_dir.resolve()
        self.experiment_root = self.output_dir / "experiments"
        self.ledger = ExperimentLedger(self.output_dir / "harness.sqlite3")
        self.cancellation = cancellation

    def close(self) -> None:
        self.ledger.close()

    def run(self) -> RunSummary:
        """执行 quick test、展开实验矩阵、汇总并写报告。"""

        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.experiment_root.mkdir(parents=True, exist_ok=True)
        self._run_quick_test()
        # 每个 instance/seed 都是独立可复验原子实验；rounds 用于稳定性或
        # promotion 重复探测，不等同于外层 Agent 改进方向。
        planned_runs = [
            {
                "round_index": round_index,
                "instance_id": instance.id,
                "instance_path": instance.path,
                "seed": seed,
            }
            for round_index in range(self.contract.budget.rounds)
            for instance in self.contract.instances
            for seed in self.contract.budget.seeds
        ]
        self._run_many(planned_runs)
        summary = self._summarize()
        self._write_report(summary)
        return summary

    def _run_quick_test(self) -> None:
        if not self.contract.commands.quick_test:
            return
        run_shell_command(
            self.contract.commands.quick_test,
            cwd=self.project_root,
            timeout=self.contract.budget.timeout_seconds,
            check=True,
            cancellation=self.cancellation,
        )

    def _run_one(self, round_index: int, instance_id: str, instance_path: Path, seed: int) -> None:
        self.ledger.record(self._run_one_to_record(round_index, instance_id, instance_path, seed))

    def _run_many(self, planned_runs: list[dict[str, object]]) -> None:
        """并行执行子进程，但让主线程串行写 SQLite，避免写锁竞争。"""

        if not planned_runs:
            return
        max_workers = max(1, self.contract.budget.max_workers)
        if max_workers == 1 or len(planned_runs) == 1:
            for spec in planned_runs:
                self._run_one(
                    round_index=int(spec["round_index"]),
                    instance_id=str(spec["instance_id"]),
                    instance_path=Path(str(spec["instance_path"])),
                    seed=int(spec["seed"]),
                )
            return

        # Subprocess execution is independent per instance/seed.  SQLite ledger
        # writes stay on the main thread to avoid concurrent write contention.
        with ThreadPoolExecutor(max_workers=min(max_workers, len(planned_runs))) as executor:
            futures = [
                executor.submit(
                    self._run_one_to_record,
                    int(spec["round_index"]),
                    str(spec["instance_id"]),
                    Path(str(spec["instance_path"])),
                    int(spec["seed"]),
                )
                for spec in planned_runs
            ]
            for future in as_completed(futures):
                self.ledger.record(future.result())

    def _run_one_to_record(self, round_index: int, instance_id: str, instance_path: Path, seed: int) -> ExperimentRecord:
        """运行一次 solver + evaluator，并把所有异常转换为失败记录。"""

        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        experiment_id = f"round_{round_index:03d}__{instance_id}__seed_{seed}"
        work_dir = self.experiment_root / experiment_id
        work_dir.mkdir(parents=True, exist_ok=True)

        solution_path = work_dir / "solution.json"
        metrics_path = work_dir / "metrics.json"
        solver_stdout = work_dir / "solver.stdout.txt"
        solver_stderr = work_dir / "solver.stderr.txt"
        evaluator_stdout = work_dir / "evaluator.stdout.txt"
        evaluator_stderr = work_dir / "evaluator.stderr.txt"
        resolved_instance = resolve_project_path(self.project_root, instance_path)

        placeholders = {
            "task_id": self.contract.task_id,
            "round": round_index,
            "round_id": f"round_{round_index:03d}",
            "instance": str(resolved_instance),
            "instance_id": instance_id,
            "solution": str(solution_path),
            "metrics": str(metrics_path),
            "seed": seed,
            "workdir": str(work_dir),
            "timeout_seconds": self.contract.budget.timeout_seconds,
            "solver_time_limit_seconds": solver_time_limit_seconds(self.contract.budget.timeout_seconds),
        }
        for name, resource_path in self.contract.resources.items():
            placeholders[name] = str(resolve_project_path(self.project_root, resource_path))

        # solver 只负责写 solution；evaluator 重新读取 solution 并写 metrics。
        # 两者 stderr/stdout 分开保存，便于判断是生成失败还是判卷失败。
        try:
            solver_cmd = self.contract.commands.solver.format(**placeholders)
            solver_started = time.perf_counter()
            solver_result = run_shell_command(
                solver_cmd,
                cwd=self.project_root,
                timeout=self.contract.budget.timeout_seconds,
                check=False,
                cancellation=self.cancellation,
            )
            solver_wall_seconds = time.perf_counter() - solver_started
            solver_stdout.write_text(solver_result.stdout, encoding="utf-8")
            solver_stderr.write_text(solver_result.stderr, encoding="utf-8")
            if solver_result.returncode != 0:
                raise RuntimeError(command_failure_message("solver", solver_result.returncode, solver_result.stderr))

            evaluator_cmd = self.contract.commands.evaluator.format(**placeholders)
            evaluator_started = time.perf_counter()
            evaluator_result = run_shell_command(
                evaluator_cmd,
                cwd=self.project_root,
                timeout=self.contract.budget.timeout_seconds,
                check=False,
                cancellation=self.cancellation,
            )
            evaluator_wall_seconds = time.perf_counter() - evaluator_started
            evaluator_stdout.write_text(evaluator_result.stdout, encoding="utf-8")
            evaluator_stderr.write_text(evaluator_result.stderr, encoding="utf-8")
            if evaluator_result.returncode != 0:
                raise RuntimeError(command_failure_message("evaluator", evaluator_result.returncode, evaluator_result.stderr))
            if not metrics_path.exists():
                raise RuntimeError("evaluator did not create metrics file")

            evaluation = EvaluationResult.from_metrics_file(metrics_path, self.contract.objectives)
            evaluation = replace(
                evaluation,
                metrics={
                    **evaluation.metrics,
                    "solver_wall_seconds": solver_wall_seconds,
                    "evaluator_wall_seconds": evaluator_wall_seconds,
                },
            )
            solver_evidence = load_solver_evidence(solution_path)
            if solver_evidence:
                consistency_errors = accepted_solver_output_errors(
                    evaluation.metrics,
                    solver_evidence,
                )
                evaluation = replace(
                    evaluation,
                    metrics={**evaluation.metrics, "solver_evidence": solver_evidence},
                    valid=evaluation.valid and not consistency_errors,
                    error_count=evaluation.error_count + len(consistency_errors),
                    errors=[*evaluation.errors, *consistency_errors],
                    status=(
                        "failed_validation"
                        if consistency_errors
                        else evaluation.status
                    ),
                )
            key = objective_key(evaluation, self.contract.objectives)
            return ExperimentRecord(
                experiment_id=experiment_id,
                task_id=self.contract.task_id,
                round_index=round_index,
                instance_id=instance_id,
                seed=seed,
                status=evaluation.status,
                valid=evaluation.valid,
                objective_key=key,
                metrics=evaluation.metrics,
                paths=self._paths(solution_path, metrics_path, solver_stdout, solver_stderr, evaluator_stdout, evaluator_stderr),
                error="; ".join(evaluation.errors) if evaluation.errors else None,
            )
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - runner must capture failures as experiment facts.
            return ExperimentRecord(
                experiment_id=experiment_id,
                task_id=self.contract.task_id,
                round_index=round_index,
                instance_id=instance_id,
                seed=seed,
                status="failed_runtime",
                valid=False,
                objective_key=tuple(float("-inf") for _ in self.contract.objectives),
                metrics={},
                paths=self._paths(solution_path, metrics_path, solver_stdout, solver_stderr, evaluator_stdout, evaluator_stderr),
                error=str(exc),
            )

    def _paths(self, *paths: Path) -> dict[str, str]:
        return {path.stem: str(path) for path in paths}

    def _summarize(self) -> RunSummary:
        """按实验和 candidate 两个粒度汇总，残缺算例覆盖不能成为最佳候选。"""

        records = self.ledger.list_records()
        valid_records = [record for record in records if record.valid]
        best = max(valid_records, key=lambda item: item.objective_key, default=None)
        candidate_summaries = self._candidate_summaries(records)
        best_candidate = max(candidate_summaries, key=lambda item: item["objective_key"], default=None)
        pareto = pareto_frontier(candidate_summaries)
        return RunSummary(
            total=len(records),
            valid=len(valid_records),
            failed=len(records) - len(valid_records),
            best_experiment_id=best.experiment_id if best else None,
            best_metrics=best.metrics if best else {},
            best_candidate_id=str(best_candidate["candidate_id"]) if best_candidate else None,
            best_candidate_metrics=dict(best_candidate["metrics"]) if best_candidate else None,
            candidate_summaries=candidate_summaries,
            pareto_frontier=pareto,
            validation_summary=validation_summary(records),
            activation_evidence=activation_evidence_records(valid_records),
        )

    def _candidate_summaries(self, records: list[ExperimentRecord]) -> list[dict[str, object]]:
        grouped: dict[str, list[ExperimentRecord]] = {}
        for record in records:
            candidate_id = f"round_{record.round_index:03d}__seed_{record.seed}"
            grouped.setdefault(candidate_id, []).append(record)

        summaries: list[dict[str, object]] = []
        expected_instance_count = len(self.contract.instances)
        for candidate_id, group in sorted(grouped.items()):
            valid_group = [record for record in group if record.valid]
            complete = len(valid_group) == expected_instance_count
            if complete:
                objective_key = tuple(
                    sum(record.objective_key[i] for record in valid_group) / len(valid_group)
                    for i in range(len(self.contract.objectives))
                )
            else:
                objective_key = tuple(float("-inf") for _ in self.contract.objectives)

            numeric_metrics: dict[str, list[float]] = {}
            for record in valid_group:
                for name, value in record.metrics.items():
                    if isinstance(value, (int, float)):
                        numeric_metrics.setdefault(name, []).append(float(value))
            metrics = {
                f"avg_{name}": sum(values) / len(values)
                for name, values in sorted(numeric_metrics.items())
                if values
            }
            metrics["valid_instances"] = len(valid_group)
            metrics["expected_instances"] = expected_instance_count
            summaries.append(
                {
                    "candidate_id": candidate_id,
                    "objective_key": objective_key,
                    "complete": complete,
                    "metrics": metrics,
                }
            )
        return summaries

    def _write_report(self, summary: RunSummary) -> None:
        records = self.ledger.list_records()
        candidate_summaries = summary.candidate_summaries or self._candidate_summaries(records)
        frontier = summary.pareto_frontier or pareto_frontier(candidate_summaries)
        validation = summary.validation_summary or validation_summary(records)
        lines = [
            f"# Harness Report: {self.contract.task_id}",
            "",
            f"- Total experiments: {summary.total}",
            f"- Valid experiments: {summary.valid}",
            f"- Failed experiments: {summary.failed}",
            f"- Best experiment: {summary.best_experiment_id or 'N/A'}",
            f"- Best metrics: `{json.dumps(summary.best_metrics, ensure_ascii=False)}`",
            f"- Best candidate: {summary.best_candidate_id or 'N/A'}",
            f"- Best candidate metrics: `{json.dumps(summary.best_candidate_metrics or {}, ensure_ascii=False)}`",
            f"- Validation summary: `{json.dumps(validation, ensure_ascii=False)}`",
            "",
            "## Candidate Aggregates",
            "",
            "| Candidate | Complete | Objective Key | Metrics |",
            "| --- | ---: | --- | --- |",
        ]
        for candidate in candidate_summaries:
            lines.append(
                f"| {candidate['candidate_id']} | {candidate['complete']} | "
                f"`{json.dumps(candidate['objective_key'], ensure_ascii=False)}` | "
                f"`{json.dumps(candidate['metrics'], ensure_ascii=False)}` |"
            )
        lines.extend(
            [
                "",
                "## Pareto Frontier",
                "",
                "| Candidate | Objective Key | Metrics |",
                "| --- | --- | --- |",
            ]
        )
        if frontier:
            for candidate in frontier:
                lines.append(
                    f"| {candidate['candidate_id']} | "
                    f"`{json.dumps(candidate['objective_key'], ensure_ascii=False)}` | "
                    f"`{json.dumps(candidate['metrics'], ensure_ascii=False)}` |"
                )
        else:
            lines.append("| N/A | `[]` | `{}` |")
        lines.extend(
            [
                "",
                "## Experiments",
                "",
                "| Experiment | Status | Valid | Objective Key | Error |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for record in records:
            error = (record.error or "").replace("|", "\\|")
            lines.append(
                f"| {record.experiment_id} | {record.status} | {record.valid} | "
                f"`{json.dumps(record.objective_key, ensure_ascii=False)}` | {error} |"
            )
        (self.output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_solver_evidence(solution_path: Path) -> dict[str, object]:
    """Read bounded, non-objective diagnostics emitted by a candidate solver.

    The fixed evaluator remains the only source of validity and objective
    metrics. This evidence only explains which internal strategy produced the
    submitted schedule and why a previous mutation improved or regressed.
    """

    try:
        raw = json.loads(solution_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    evidence: dict[str, object] = {}
    source = str(raw.get("source") or "").strip()
    if source:
        evidence["selected_source"] = source[:240]
    if isinstance(raw.get("makespan"), (int, float)):
        evidence["reported_makespan"] = raw["makespan"]
    schedule = raw.get("schedule")
    if isinstance(schedule, list):
        evidence["reported_operation_count"] = len(schedule)
    raw_diagnostics = raw.get("diagnostics")
    if raw_diagnostics in ({}, [], None, ""):
        misplaced = raw.get("best_metrics")
        misplaced = misplaced.get("solver_evidence") if isinstance(misplaced, dict) else None
        if isinstance(misplaced, dict):
            exact = misplaced.get("diagnostics")
            if isinstance(exact, dict):
                exact = dict(exact)
                exact.setdefault("accepted", misplaced.get("accepted") is True)
                raw_diagnostics = {"solver_evidence": exact}
                evidence["solver_evidence_path_repaired"] = True
    diagnostics = bounded_solver_diagnostics(raw_diagnostics)
    if diagnostics not in ({}, [], None, ""):
        fitted, truncated = fit_solver_diagnostics(
            diagnostics,
            max_chars=SOLVER_DIAGNOSTICS_MAX_CHARS - 1_000,
        )
        evidence["diagnostics"] = fitted
        if truncated:
            evidence["diagnostics_truncated"] = True
    encoded = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= SOLVER_DIAGNOSTICS_MAX_CHARS:
        return evidence
    fitted, _truncated = fit_solver_diagnostics(
        diagnostics,
        max_chars=SOLVER_DIAGNOSTICS_MAX_CHARS // 2,
    )
    evidence["diagnostics"] = fitted
    evidence["diagnostics_truncated"] = True
    return evidence


def accepted_solver_output_errors(
    evaluator_metrics: dict[str, object],
    solver_evidence: dict[str, object],
) -> list[str]:
    """Reject a serialized schedule that disagrees with an accepted exact incumbent."""

    diagnostics = solver_evidence.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return []
    exact = diagnostics.get("solver_evidence")
    if not isinstance(exact, dict) or exact.get("accepted") is not True:
        return []

    comparisons = {
        "makespan": exact.get(
            "makespan",
            exact.get("objective", exact.get("candidate_makespan")),
        ),
        "priority_completion_time": exact.get(
            "priority_completion_time",
            exact.get("candidate_priority_completion_time"),
        ),
        "max_machine_workload": exact.get(
            "max_machine_workload",
            exact.get("candidate_max_machine_workload"),
        ),
        "total_workload": exact.get(
            "total_workload",
            exact.get("candidate_total_workload"),
        ),
        "total_tardiness": exact.get(
            "total_tardiness",
            exact.get("candidate_total_tardiness"),
        ),
    }
    errors: list[str] = []
    for metric_name, accepted_value in comparisons.items():
        evaluator_value = evaluator_metrics.get(metric_name)
        if accepted_value is None or evaluator_value is None:
            continue
        if not _same_numeric_value(accepted_value, evaluator_value):
            errors.append(
                "accepted solver incumbent does not match serialized evaluator metric: "
                f"{metric_name} accepted={accepted_value!r} evaluated={evaluator_value!r}"
            )
    return errors


def _same_numeric_value(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return abs(float(left) - float(right)) <= 1e-9


def activation_evidence_records(records: list[ExperimentRecord]) -> list[dict[str, object]]:
    """Retain bounded per-run diagnostics so activation is not tied to the best seed."""

    result: list[dict[str, object]] = []
    for record in sorted(records, key=lambda item: (item.seed, item.instance_id, item.experiment_id)):
        solver_evidence = record.metrics.get("solver_evidence")
        if not isinstance(solver_evidence, dict):
            continue
        diagnostics = solver_evidence.get("diagnostics")
        fitted, truncated = fit_solver_diagnostics(
            diagnostics,
            max_chars=ACTIVATION_DIAGNOSTICS_MAX_CHARS,
        )
        bounded_evidence = {
            key: solver_evidence.get(key)
            for key in (
                "selected_source",
                "reported_makespan",
                "reported_operation_count",
            )
            if key in solver_evidence
        }
        if fitted not in ({}, [], None, ""):
            bounded_evidence["diagnostics"] = fitted
        if truncated or solver_evidence.get("diagnostics_truncated"):
            bounded_evidence["diagnostics_truncated"] = True
        result.append(
            {
                "experiment_id": record.experiment_id,
                "instance_id": record.instance_id,
                "seed": record.seed,
                "best_metrics": {"solver_evidence": bounded_evidence},
            }
        )
    return result


def fit_solver_diagnostics(value: object, *, max_chars: int) -> tuple[object, bool]:
    """Keep scalar telemetry paths within a hard budget, preferring counters over blobs."""

    leaves: list[tuple[tuple[str, ...], object]] = []
    _collect_diagnostic_leaves(value, (), leaves)
    leaves.sort(key=_diagnostic_leaf_priority)
    result: dict[str, object] = {}
    omitted = 0
    for path, item in leaves:
        candidate = json.loads(json.dumps(result, ensure_ascii=False))
        _set_diagnostic_path(candidate, path, item)
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > max_chars:
            omitted += 1
            continue
        result = candidate
    if omitted:
        result["_truncated"] = True
        result["_omitted_leaf_count"] = omitted
    return result, bool(omitted or result != value)


def _collect_diagnostic_leaves(
    value: object,
    path: tuple[str, ...],
    result: list[tuple[tuple[str, ...], object]],
) -> None:
    if value is None or isinstance(value, (bool, int, float)):
        result.append((path, value))
        return
    if isinstance(value, str):
        result.append((path, value[:256]))
        return
    if isinstance(value, list):
        if len(value) <= 16 and all(item is None or isinstance(item, (bool, int, float, str)) for item in value):
            result.append((path, [item[:256] if isinstance(item, str) else item for item in value]))
            return
        result.append(((*path, "_item_count"), len(value)))
        for index, item in enumerate(value[:8]):
            _collect_diagnostic_leaves(item, (*path, str(index)), result)
        return
    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            if index >= SOLVER_DIAGNOSTICS_MAX_ITEMS:
                result.append(((*path, "_omitted_item_count"), len(value) - index))
                break
            _collect_diagnostic_leaves(item, (*path, str(key)[:160]), result)
        return
    result.append((path, str(value)[:256]))


def _diagnostic_leaf_priority(item: tuple[tuple[str, ...], object]) -> tuple[int, int, str]:
    path, value = item
    joined = ".".join(path)
    preferred = 0 if "telemetry" in path else 1 if any(key in path for key in ("search_counters", "timings_ms")) else 2
    value_rank = 0 if value is None or isinstance(value, (bool, int, float)) else 1
    return preferred, value_rank, joined


def _set_diagnostic_path(target: dict[str, object], path: tuple[str, ...], value: object) -> None:
    if not path:
        target["value"] = value
        return
    current = target
    for segment in path[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[path[-1]] = value


def bounded_solver_diagnostics(value: object, *, depth: int = 0) -> object:
    """Sanitize untrusted solver diagnostics before storing them in the ledger."""

    if depth >= SOLVER_DIAGNOSTICS_MAX_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, list):
        return [
            bounded_solver_diagnostics(item, depth=depth + 1)
            for item in value[:SOLVER_DIAGNOSTICS_MAX_ITEMS]
        ]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= SOLVER_DIAGNOSTICS_MAX_ITEMS:
                break
            result[str(key)[:160]] = bounded_solver_diagnostics(item, depth=depth + 1)
        return result
    return str(value)[:2_000]


def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    check: bool,
    cancellation: CancellationToken | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command and kill the whole process tree on timeout."""

    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "shell": True,
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    if cancellation is not None:
        cancellation.raise_if_cancelled()
    proc = subprocess.Popen(command, **popen_kwargs)
    registration = (
        cancellation.register_terminator(lambda: kill_process_tree(proc))
        if cancellation is not None
        else None
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd=exc.cmd,
            timeout=exc.timeout,
            output=stdout,
            stderr=stderr,
        ) from exc
    finally:
        if cancellation is not None:
            cancellation.unregister_terminator(registration)
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    cleanup_process_descendants(proc)

    result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=stdout, stderr=stderr)
    return result


def cleanup_process_descendants(proc: subprocess.Popen[str]) -> None:
    """Reap descendants left behind after their direct parent exited normally.

    OpenCode and solver launchers can finish while detached Node/Python helpers
    remain in the process group.  The launcher PID/process group is unique to
    this invocation, so cleanup stays scoped to descendants of this run.
    """

    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return
    if os.name == "nt":
        _kill_windows_process_tree(pid)
        return
    try:
        # Callers use start_new_session=True, making the launcher PID the PGID.
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        return


def solver_time_limit_seconds(timeout_seconds: int | float) -> float:
    """Reserve process-exit headroom inside the Core wall-clock timeout."""

    # solver 获得大部分预算，但必须给 Python 退出、文件落盘和 Core 回收
    # 进程留出余量，否则内部“准时结束”仍可能触发外层硬超时。
    timeout = max(0.1, float(timeout_seconds))
    if timeout <= 2.0:
        return round(max(0.05, timeout * 0.5), 3)
    return round(max(0.1, min(timeout * 0.8, timeout - 1.0)), 3)


def kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        # .cmd、venv launcher 和真实 Python 进程可能形成多层父子链；外层
        # 已退出时 stdout 管道仍会被后代持有。先按原生进程快照结束后代，
        # 再用 taskkill 兜底，避免 communicate() 永久等待。
        _kill_windows_process_tree(proc.pid)
        try:
            proc.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
            if completed.returncode == 0 or proc.poll() is not None:
                return
        except Exception:  # noqa: BLE001 - fall through to direct kill.
            pass
    else:
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except Exception:  # noqa: BLE001 - fall through to direct kill.
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        return


def _kill_windows_process_tree(root_pid: int) -> None:
    """Terminate descendants from deepest to root using Toolhelp32."""

    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return
        children: dict[int, list[int]] = {}
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while has_entry:
                children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
                has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)

        ordered: list[int] = []
        stack = [root_pid]
        while stack:
            parent = stack.pop()
            ordered.append(parent)
            stack.extend(children.get(parent, []))
        for pid in reversed(ordered):
            handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
            if not handle:
                continue
            try:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 200)
            finally:
                kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - taskkill/direct kill remain as fallbacks.
        return


def pareto_frontier(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    complete_candidates = [
        candidate
        for candidate in candidates
        if bool(candidate.get("complete")) and _finite_key(candidate.get("objective_key"))
    ]
    frontier: list[dict[str, object]] = []
    for candidate in complete_candidates:
        candidate_key = tuple(float(item) for item in candidate.get("objective_key", ()))
        dominated = False
        for other in complete_candidates:
            if other is candidate:
                continue
            other_key = tuple(float(item) for item in other.get("objective_key", ()))
            if _dominates(other_key, candidate_key):
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    return sorted(frontier, key=lambda item: item.get("objective_key", ()), reverse=True)


def validation_summary(records: list[ExperimentRecord]) -> dict[str, object]:
    status_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    for record in records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
        if record.error:
            for error in record.error.split("; "):
                error_counts[error] = error_counts.get(error, 0) + 1
    return {
        "status_counts": dict(sorted(status_counts.items())),
        "top_errors": [
            {"error": error, "count": count}
            for error, count in sorted(error_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
    }


def _dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    if len(left) != len(right):
        return False
    return all(a >= b for a, b in zip(left, right)) and any(a > b for a, b in zip(left, right))


def _finite_key(value: object) -> bool:
    if not isinstance(value, tuple):
        return False
    return bool(value) and all(item != float("-inf") for item in value)


def command_failure_message(stage: str, returncode: int, stderr: str) -> str:
    message = f"{stage} command failed with exit code {returncode}"
    excerpt = subprocess_error_excerpt(stderr)
    if excerpt:
        message += f" | stderr_excerpt: {excerpt}"
    return message


def subprocess_error_excerpt(stderr: str) -> str:
    if not stderr:
        return ""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    tail = lines[-6:]
    excerpt = " | ".join(tail)
    if len(excerpt) > SUBPROCESS_ERROR_EXCERPT_MAX_CHARS:
        excerpt = excerpt[-SUBPROCESS_ERROR_EXCERPT_MAX_CHARS:]
    return excerpt
