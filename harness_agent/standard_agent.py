from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .graph_runner import GraphHarnessRunner
from .hypothesis import HypothesisLedger, HypothesisRecord, extract_score, improvement_note, make_hypothesis_id
from .models import TaskContract
from .runner import RunSummary
from .strategy_variants import build_strategy_candidates
from .workers.deepseek_worker import generate_profile_auto, normalize_local_search_profiles


class StandardAgentState(TypedDict, total=False):
    docs_text: str
    previous_report: str
    round_index: int
    profile_path: str
    strategy_path: str
    profile_source: str
    profile_candidates: list[dict[str, str]]
    contract_specs: list[dict[str, str]]
    contract_path: str
    selected_candidate_id: str
    candidate_results: list[dict[str, Any]]
    harness_output_dir: str
    summary: RunSummary
    reports: list[str]
    last_hypothesis_id: str | None
    last_score_value: float | None
    best_hypothesis_id: str | None
    best_score_value: float | None


class StandardFjspAgentRunner:
    """LangGraph workflow for document-driven standard-FJSP strategy iteration."""

    def __init__(
        self,
        *,
        docs: list[Path],
        instance_dir: Path,
        pattern: str,
        output_dir: Path,
        best_known_csv: Path | None,
        max_instances: int | None,
        max_rounds: int,
        seeds: list[int],
        timeout_seconds: int,
        max_workers: int,
        solver: str,
        portfolio_size: int,
        local_search_restarts: int,
        local_search_initial_pool_size: int,
        local_search_iterations: int,
        local_search_neighbor_limit: int,
        local_search_time_limit_sec: float,
        local_search_neighborhood_profiles: list[str],
        local_search_run_profiles: list[dict[str, Any]] | None,
        strategy_candidates: int,
        profile_mode: str,
        deepseek_model: str,
        project_root: Path,
    ) -> None:
        self.docs = docs
        self.instance_dir = instance_dir
        self.pattern = pattern
        self.output_dir = output_dir.resolve()
        self.best_known_csv = best_known_csv
        self.max_instances = max_instances
        self.max_rounds = max_rounds
        self.seeds = seeds
        self.timeout_seconds = timeout_seconds
        self.max_workers = max(1, max_workers)
        self.solver = solver
        self.portfolio_size = portfolio_size
        self.local_search_restarts = local_search_restarts
        self.local_search_initial_pool_size = local_search_initial_pool_size
        self.local_search_iterations = local_search_iterations
        self.local_search_neighbor_limit = local_search_neighbor_limit
        self.local_search_time_limit_sec = local_search_time_limit_sec
        self.local_search_neighborhood_profiles = local_search_neighborhood_profiles or ["random"]
        self.local_search_run_profiles = local_search_run_profiles
        self.strategy_candidates = max(1, strategy_candidates)
        self.profile_mode = profile_mode
        self.deepseek_model = deepseek_model
        self.project_root = project_root.resolve()
        self.hypothesis_ledger = HypothesisLedger(self.output_dir / "hypotheses.jsonl")

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        graph = self._build_graph()
        final_state = graph.invoke({"round_index": 0, "reports": [], "previous_report": ""})
        self._write_agent_report(final_state)
        return {
            "rounds": self.max_rounds,
            "report": str((self.output_dir / "agent_report.md").resolve()),
            "last_summary": self._summary_payload(final_state.get("summary")),
            "profile_source": final_state.get("profile_source"),
        }

    def _build_graph(self):
        workflow = StateGraph(StandardAgentState)
        workflow.add_node("ingest_documents", self._ingest_documents)
        workflow.add_node("propose_strategy", self._propose_strategy)
        workflow.add_node("build_contract", self._build_contract)
        workflow.add_node("run_harness", self._run_harness)
        workflow.add_node("reflect", self._reflect)

        workflow.add_edge(START, "ingest_documents")
        workflow.add_edge("ingest_documents", "propose_strategy")
        workflow.add_edge("propose_strategy", "build_contract")
        workflow.add_edge("build_contract", "run_harness")
        workflow.add_edge("run_harness", "reflect")
        workflow.add_conditional_edges(
            "reflect",
            self._next_after_reflect,
            {
                "continue": "propose_strategy",
                "end": END,
            },
        )
        return workflow.compile()

    def _ingest_documents(self, state: StandardAgentState) -> StandardAgentState:
        parts: list[str] = []
        for path in self.docs:
            if not path.exists():
                parts.append(f"\n## Missing document\n{path}\n")
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            parts.append(f"\n## {path.name}\n{text[:12000]}\n")
        return {"docs_text": "\n".join(parts)}

    def _propose_strategy(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        round_dir = self.output_dir / f"round_{round_index:02d}"
        profile_path, strategy_path, source = generate_profile_auto(
            docs=state.get("docs_text", ""),
            previous_report=state.get("previous_report", ""),
            output_dir=round_dir,
            round_index=round_index,
            mode=self.profile_mode,
            model=self.deepseek_model,
        )
        candidates = build_strategy_candidates(
            profile_path=profile_path,
            output_dir=round_dir,
            max_candidates=self.strategy_candidates,
            source=source,
        )
        return {
            "profile_path": str(profile_path),
            "strategy_path": str(strategy_path),
            "profile_source": source,
            "profile_candidates": candidates,
        }

    def _build_contract(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        round_dir = self.output_dir / f"round_{round_index:02d}"
        paths = sorted(self.instance_dir.glob(self.pattern))
        if self.max_instances is not None:
            paths = paths[: self.max_instances]
        if not paths:
            raise FileNotFoundError(f"no instance files matched {self.instance_dir / self.pattern}")

        contract_specs: list[dict[str, str]] = []
        profile_candidates = state.get("profile_candidates") or [
            {
                "candidate_id": "candidate_00_all",
                "profile_path": str(Path(state["profile_path"])),
                "strategy_path": str(Path(state["strategy_path"])),
            }
        ]

        evaluator = "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
        evaluator_resources: dict[str, str] = {}
        if self.best_known_csv:
            evaluator_resources["best_known_csv"] = str(self.best_known_csv)
            evaluator += " --best-known-csv {best_known_csv}"

        default_run_profiles = self._local_search_profiles()
        for candidate in profile_candidates:
            run_profiles = (
                default_run_profiles
                if self.local_search_run_profiles
                else self._local_search_profiles_for_candidate(Path(candidate["profile_path"]), default_run_profiles)
            )
            for run_profile in run_profiles:
                candidate_id = candidate["candidate_id"]
                expanded_candidate_id = (
                    candidate_id
                    if len(run_profiles) == 1
                    else f"{candidate_id}__ls_{run_profile['name']}"
                )
                candidate_dir = round_dir / "candidates" / expanded_candidate_id
                candidate_dir.mkdir(parents=True, exist_ok=True)
                resources = {
                    "strategy_profile": candidate["profile_path"],
                    **evaluator_resources,
                }
                payload = self._contract_payload(
                    round_index=round_index,
                    candidate_id=expanded_candidate_id,
                    paths=paths,
                    evaluator=evaluator,
                    resources=resources,
                    run_profile=run_profile,
                )
                contract_path = candidate_dir / "contract.json"
                contract_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                contract_specs.append(
                    {
                        "candidate_id": expanded_candidate_id,
                        "profile_path": candidate["profile_path"],
                        "strategy_path": candidate["strategy_path"],
                        "contract_path": str(contract_path),
                        "local_search_profile": str(run_profile["name"]),
                    }
                )
        return {"contract_specs": contract_specs, "contract_path": contract_specs[0]["contract_path"]}

    def _contract_payload(
        self,
        *,
        round_index: int,
        candidate_id: str,
        paths: list[Path],
        evaluator: str,
        resources: dict[str, str],
        run_profile: dict[str, Any],
    ) -> dict[str, Any]:
        if self.solver == "portfolio":
            solver_cmd = (
                "python examples/standard_fjsp_portfolio_solver.py "
                "--input {instance} --output {solution} --seed {seed} "
                f"--portfolio-size {self.portfolio_size} "
                "--strategy-profile {strategy_profile}"
            )
        elif self.solver == "local-search":
            solver_cmd = (
                "python examples/standard_fjsp_local_search_solver.py "
                "--input {instance} --output {solution} --seed {seed} "
                f"--portfolio-size {int(run_profile['portfolio_size'])} "
                f"--restarts {int(run_profile['restarts'])} "
                f"--initial-pool-size {int(run_profile.get('initial_pool_size', 1))} "
                f"--iterations {int(run_profile['iterations'])} "
                f"--neighbor-limit {int(run_profile['neighbor_limit'])} "
                f"--time-limit-sec {float(run_profile['time_limit_sec'])} "
                f"--neighborhood-profile {run_profile['neighborhood_profile']} "
                "--strategy-profile {strategy_profile}"
            )
        else:
            raise ValueError(f"unknown standard solver: {self.solver}")

        return {
            "task_id": f"standard_fjsp_agent_round_{round_index:02d}_{candidate_id}",
            "problem_family": "FJSP",
            "description": "Document-driven standard FJSP agent round.",
            "instances": [{"id": path.stem, "path": str(path)} for path in paths],
            "objectives": [
                {
                    "name": "makespan",
                    "direction": "minimize",
                    "priority": 1,
                    "invalid_if_missing": True,
                }
            ],
            "commands": {
                "solver": solver_cmd,
                "evaluator": evaluator,
                "quick_test": "python -m compileall harness_agent examples",
            },
            "budget": {
                "rounds": 1,
                "seeds": self.seeds,
                "timeout_seconds": self.timeout_seconds,
                "max_workers": self.max_workers,
            },
            "paths": {
                "allowed_paths": ["examples", "harness_agent", "configs"],
                "forbidden_paths": [".git", "outputs"],
            },
            "resources": resources,
        }

    def _run_harness(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        candidate_results: list[dict[str, Any]] = []
        best_result: dict[str, Any] | None = None
        for spec in state.get("contract_specs", []):
            contract = TaskContract.load(Path(spec["contract_path"]))
            errors = contract.validate(self.project_root)
            if errors:
                raise RuntimeError(f"generated contract is invalid for {spec['candidate_id']}: {errors}")
            harness_output_dir = self.output_dir / f"round_{round_index:02d}" / "candidates" / spec["candidate_id"] / "harness"
            runner = GraphHarnessRunner(contract=contract, project_root=self.project_root, output_dir=harness_output_dir)
            try:
                summary = runner.run()
            finally:
                runner.close()
            summary_payload = self._summary_payload(summary)
            metric_name, score_value = extract_score(summary_payload)
            result = {
                "candidate_id": spec["candidate_id"],
                "profile_path": spec["profile_path"],
                "strategy_path": spec["strategy_path"],
                "contract_path": spec["contract_path"],
                "harness_output_dir": str(harness_output_dir),
                "score_metric": metric_name,
                "score_value": score_value,
                "summary": summary_payload,
            }
            candidate_results.append(result)
            if best_result is None or self._candidate_sort_key(result) > self._candidate_sort_key(best_result):
                best_result = result
        if best_result is None:
            raise RuntimeError("no strategy candidates were evaluated")
        return {
            "summary": self._summary_from_payload(best_result["summary"]),
            "harness_output_dir": str(best_result["harness_output_dir"]),
            "selected_candidate_id": str(best_result["candidate_id"]),
            "profile_path": str(best_result["profile_path"]),
            "strategy_path": str(best_result["strategy_path"]),
            "contract_path": str(best_result["contract_path"]),
            "candidate_results": candidate_results,
        }

    def _reflect(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        report_path = Path(state["harness_output_dir"]) / "report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        reports = list(state.get("reports", []))
        reports.append(report)
        hypothesis = self._record_hypothesis(round_index, state)
        reflection = self._local_reflection(round_index, state, report, hypothesis)
        reflection_path = self.output_dir / f"round_{round_index:02d}" / "reflection.md"
        reflection_path.write_text(reflection, encoding="utf-8")
        best_hypothesis_id = state.get("best_hypothesis_id")
        best_score_value = state.get("best_score_value")
        if hypothesis.score_value is not None and (
            best_score_value is None or hypothesis.score_value > float(best_score_value)
        ):
            best_hypothesis_id = hypothesis.hypothesis_id
            best_score_value = hypothesis.score_value
        return {
            "previous_report": self._next_round_context(report, hypothesis),
            "reports": reports,
            "round_index": round_index + 1,
            "last_hypothesis_id": hypothesis.hypothesis_id,
            "last_score_value": hypothesis.score_value,
            "best_hypothesis_id": best_hypothesis_id,
            "best_score_value": best_score_value,
        }

    def _next_after_reflect(self, state: StandardAgentState) -> str:
        return "continue" if int(state.get("round_index", 0)) < self.max_rounds else "end"

    def _record_hypothesis(self, round_index: int, state: StandardAgentState) -> HypothesisRecord:
        summary = self._summary_payload(state.get("summary"))
        score_metric, score_value = extract_score(summary)
        parent_score = state.get("last_score_value")
        delta = None
        if score_value is not None and parent_score is not None:
            delta = score_value - float(parent_score)
        artifacts = {
            "strategy": str(state.get("strategy_path", "")),
            "profile": str(state.get("profile_path", "")),
            "contract": str(state.get("contract_path", "")),
            "harness": str(state.get("harness_output_dir", "")),
        }
        hypothesis = HypothesisRecord(
            hypothesis_id=make_hypothesis_id(round_index, summary, artifacts),
            parent_id=state.get("last_hypothesis_id"),
            round_index=round_index,
            source=str(state.get("profile_source", "")),
            solver=self.solver,
            status="evaluated" if summary else "missing_summary",
            score_metric=score_metric,
            score_value=score_value,
            delta_from_parent=delta,
            summary=summary,
            artifacts=artifacts,
            note=improvement_note(score_metric, score_value, delta),
            candidate_id=state.get("selected_candidate_id"),
            candidate_results=state.get("candidate_results"),
        )
        self.hypothesis_ledger.append(hypothesis)
        return hypothesis

    def _next_round_context(self, report: str, hypothesis: HypothesisRecord) -> str:
        return (
            report
            + "\n\n## Structured Hypothesis Feedback\n\n"
            + json.dumps(hypothesis.__dict__, ensure_ascii=False, indent=2)
        )

    def _local_reflection(
        self,
        round_index: int,
        state: StandardAgentState,
        report: str,
        hypothesis: HypothesisRecord,
    ) -> str:
        summary = self._summary_payload(state.get("summary"))
        return (
            f"# Round {round_index:02d} Reflection\n\n"
            f"- Strategy source: `{state.get('profile_source')}`\n"
            f"- Strategy file: `{state.get('strategy_path')}`\n"
            f"- Harness output: `{state.get('harness_output_dir')}`\n"
            f"- Selected candidate: `{state.get('selected_candidate_id') or 'N/A'}`\n"
            f"- Hypothesis id: `{hypothesis.hypothesis_id}`\n"
            f"- Parent hypothesis: `{hypothesis.parent_id or 'N/A'}`\n"
            f"- Score metric: `{hypothesis.score_metric or 'N/A'}`\n"
            f"- Score value: `{hypothesis.score_value if hypothesis.score_value is not None else 'N/A'}`\n"
            f"- Delta from parent: `{hypothesis.delta_from_parent if hypothesis.delta_from_parent is not None else 'N/A'}`\n"
            f"- Hypothesis note: {hypothesis.note}\n"
            f"- Summary: `{json.dumps(summary, ensure_ascii=False)}`\n\n"
            f"{self._candidate_table(state.get('candidate_results', []))}\n\n"
            "The evaluator remains the source of truth. The next round may use this report "
            "as feedback for a new strategy profile, but it must not reuse solution files as warm starts.\n\n"
            "## Report Excerpt\n\n"
            f"{report[:4000]}\n"
        )

    def _write_agent_report(self, state: StandardAgentState) -> None:
        local_search_profile_label = (
            ", ".join(profile["name"] for profile in self._local_search_profiles())
            if self.local_search_run_profiles
            else "profile-driven local_search_profiles; fallback="
            + ", ".join(profile["name"] for profile in self._local_search_profiles())
        )
        lines = [
            "# Standard FJSP Agent Report",
            "",
            f"- Rounds requested: {self.max_rounds}",
            f"- Profile mode: `{self.profile_mode}`",
            f"- Solver: `{self.solver}`",
            f"- Local-search run profiles: `{local_search_profile_label}`",
            f"- DeepSeek model: `{self.deepseek_model}`",
            f"- Pattern: `{self.pattern}`",
            f"- Strategy candidates per round: `{self.strategy_candidates}`",
            f"- Last summary: `{json.dumps(self._summary_payload(state.get('summary')), ensure_ascii=False)}`",
            f"- Selected candidate: `{state.get('selected_candidate_id') or 'N/A'}`",
            f"- Best hypothesis: `{state.get('best_hypothesis_id') or 'N/A'}`",
            f"- Hypothesis ledger: `{self.hypothesis_ledger.path}`",
            "",
            "## Rounds",
            "",
        ]
        for index in range(self.max_rounds):
            round_dir = self.output_dir / f"round_{index:02d}"
            lines.append(f"- Round {index:02d}: [{round_dir.name}]({round_dir / 'reflection.md'})")
        (self.output_dir / "agent_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _summary_payload(self, summary: RunSummary | None) -> dict[str, Any]:
        if summary is None:
            return {}
        return {
            "total": summary.total,
            "valid": summary.valid,
            "failed": summary.failed,
            "best_experiment_id": summary.best_experiment_id,
            "best_metrics": summary.best_metrics,
            "best_candidate_id": summary.best_candidate_id,
            "best_candidate_metrics": summary.best_candidate_metrics,
        }

    def _local_search_profiles(self) -> list[dict[str, Any]]:
        if self.local_search_run_profiles:
            return self.local_search_run_profiles
        return [
            {
                "name": profile,
                "portfolio_size": self.portfolio_size,
                "restarts": self.local_search_restarts,
                "initial_pool_size": self.local_search_initial_pool_size,
                "iterations": self.local_search_iterations,
                "neighbor_limit": self.local_search_neighbor_limit,
                "time_limit_sec": self.local_search_time_limit_sec,
                "neighborhood_profile": profile,
            }
            for profile in self.local_search_neighborhood_profiles
        ]

    def _local_search_profiles_for_candidate(
        self,
        profile_path: Path,
        fallback: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return fallback
        generated_profiles = normalize_local_search_profiles(profile)
        if not generated_profiles:
            return fallback
        return generated_profiles

    def _candidate_sort_key(self, result: dict[str, Any]) -> tuple[float, str]:
        score = result.get("score_value")
        return (float(score) if isinstance(score, (int, float)) else float("-inf"), str(result.get("candidate_id", "")))

    def _summary_from_payload(self, payload: dict[str, Any]) -> RunSummary:
        return RunSummary(
            total=int(payload.get("total", 0)),
            valid=int(payload.get("valid", 0)),
            failed=int(payload.get("failed", 0)),
            best_experiment_id=payload.get("best_experiment_id"),
            best_metrics=dict(payload.get("best_metrics") or {}),
            best_candidate_id=payload.get("best_candidate_id"),
            best_candidate_metrics=dict(payload.get("best_candidate_metrics") or {}),
        )

    def _candidate_table(self, candidate_results: list[dict[str, Any]]) -> str:
        if not candidate_results:
            return "## Strategy Candidates\n\nNo strategy-candidate comparison was recorded."
        lines = [
            "## Strategy Candidates",
            "",
            "| Candidate | Score Metric | Score Value | Summary |",
            "| --- | --- | ---: | --- |",
        ]
        for result in candidate_results:
            lines.append(
                f"| {result.get('candidate_id')} | {result.get('score_metric') or 'N/A'} | "
                f"`{result.get('score_value')}` | "
                f"`{json.dumps(result.get('summary') or {}, ensure_ascii=False)}` |"
            )
        return "\n".join(lines)
