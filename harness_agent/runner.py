from __future__ import annotations

import json
import os
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .evaluator import EvaluationResult, objective_key
from .ledger import ExperimentLedger, ExperimentRecord
from .models import TaskContract, resolve_project_path


CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


@dataclass(frozen=True)
class RunSummary:
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


class HarnessRunner:
    def __init__(self, contract: TaskContract, project_root: Path, output_dir: Path) -> None:
        self.contract = contract
        self.project_root = project_root.resolve()
        self.output_dir = output_dir.resolve()
        self.experiment_root = self.output_dir / "experiments"
        self.ledger = ExperimentLedger(self.output_dir / "harness.sqlite3")

    def close(self) -> None:
        self.ledger.close()

    def run(self) -> RunSummary:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.experiment_root.mkdir(parents=True, exist_ok=True)
        self._run_quick_test()
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
        )

    def _run_one(self, round_index: int, instance_id: str, instance_path: Path, seed: int) -> None:
        self.ledger.record(self._run_one_to_record(round_index, instance_id, instance_path, seed))

    def _run_many(self, planned_runs: list[dict[str, object]]) -> None:
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
        }
        for name, resource_path in self.contract.resources.items():
            placeholders[name] = str(resolve_project_path(self.project_root, resource_path))

        try:
            solver_cmd = self.contract.commands.solver.format(**placeholders)
            solver_result = run_shell_command(
                solver_cmd,
                cwd=self.project_root,
                timeout=self.contract.budget.timeout_seconds,
                check=False,
            )
            solver_stdout.write_text(solver_result.stdout, encoding="utf-8")
            solver_stderr.write_text(solver_result.stderr, encoding="utf-8")
            if solver_result.returncode != 0:
                raise RuntimeError(f"solver command failed with exit code {solver_result.returncode}")

            evaluator_cmd = self.contract.commands.evaluator.format(**placeholders)
            evaluator_result = run_shell_command(
                evaluator_cmd,
                cwd=self.project_root,
                timeout=self.contract.budget.timeout_seconds,
                check=False,
            )
            evaluator_stdout.write_text(evaluator_result.stdout, encoding="utf-8")
            evaluator_stderr.write_text(evaluator_result.stderr, encoding="utf-8")
            if evaluator_result.returncode != 0:
                raise RuntimeError(f"evaluator command failed with exit code {evaluator_result.returncode}")
            if not metrics_path.exists():
                raise RuntimeError("evaluator did not create metrics file")

            evaluation = EvaluationResult.from_metrics_file(metrics_path, self.contract.objectives)
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


def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    check: bool,
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

    proc = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd=exc.cmd,
            timeout=exc.timeout,
            output=stdout,
            stderr=stderr,
        ) from exc

    result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=stdout, stderr=stderr)
    return result


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
            )
            return
        except Exception:  # noqa: BLE001 - fall through to direct kill.
            pass
    else:
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
