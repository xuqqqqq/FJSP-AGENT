from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .evaluator import EvaluationResult, objective_key
from .ledger import ExperimentLedger, ExperimentRecord
from .models import TaskContract, resolve_project_path


@dataclass(frozen=True)
class RunSummary:
    total: int
    valid: int
    failed: int
    best_experiment_id: str | None
    best_metrics: dict[str, object]
    best_candidate_id: str | None = None
    best_candidate_metrics: dict[str, object] | None = None


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
        for round_index in range(self.contract.budget.rounds):
            for instance in self.contract.instances:
                for seed in self.contract.budget.seeds:
                    self._run_one(round_index, instance.id, instance.path, seed)
        summary = self._summarize()
        self._write_report(summary)
        return summary

    def _run_quick_test(self) -> None:
        if not self.contract.commands.quick_test:
            return
        subprocess.run(
            self.contract.commands.quick_test,
            cwd=self.project_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=self.contract.budget.timeout_seconds,
            check=True,
        )

    def _run_one(self, round_index: int, instance_id: str, instance_path: Path, seed: int) -> None:
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

        try:
            solver_cmd = self.contract.commands.solver.format(**placeholders)
            solver_result = subprocess.run(
                solver_cmd,
                cwd=self.project_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=self.contract.budget.timeout_seconds,
                check=False,
            )
            solver_stdout.write_text(solver_result.stdout, encoding="utf-8")
            solver_stderr.write_text(solver_result.stderr, encoding="utf-8")
            if solver_result.returncode != 0:
                raise RuntimeError(f"solver command failed with exit code {solver_result.returncode}")

            evaluator_cmd = self.contract.commands.evaluator.format(**placeholders)
            evaluator_result = subprocess.run(
                evaluator_cmd,
                cwd=self.project_root,
                shell=True,
                text=True,
                capture_output=True,
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
            self.ledger.record(
                ExperimentRecord(
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
            )
        except Exception as exc:  # noqa: BLE001 - runner must capture failures as experiment facts.
            self.ledger.record(
                ExperimentRecord(
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
            )

    def _paths(self, *paths: Path) -> dict[str, str]:
        return {path.stem: str(path) for path in paths}

    def _summarize(self) -> RunSummary:
        records = self.ledger.list_records()
        valid_records = [record for record in records if record.valid]
        best = max(valid_records, key=lambda item: item.objective_key, default=None)
        candidate_summaries = self._candidate_summaries(records)
        best_candidate = max(candidate_summaries, key=lambda item: item["objective_key"], default=None)
        return RunSummary(
            total=len(records),
            valid=len(valid_records),
            failed=len(records) - len(valid_records),
            best_experiment_id=best.experiment_id if best else None,
            best_metrics=best.metrics if best else {},
            best_candidate_id=str(best_candidate["candidate_id"]) if best_candidate else None,
            best_candidate_metrics=dict(best_candidate["metrics"]) if best_candidate else None,
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
            "",
            "## Candidate Aggregates",
            "",
            "| Candidate | Complete | Objective Key | Metrics |",
            "| --- | ---: | --- | --- |",
        ]
        for candidate in self._candidate_summaries(records):
            lines.append(
                f"| {candidate['candidate_id']} | {candidate['complete']} | "
                f"`{json.dumps(candidate['objective_key'], ensure_ascii=False)}` | "
                f"`{json.dumps(candidate['metrics'], ensure_ascii=False)}` |"
            )
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
