from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .graph_runner import GraphHarnessRunner
from .models import TaskContract
from .runner import RunSummary
from .workers.deepseek_worker import generate_profile_auto


class StandardAgentState(TypedDict, total=False):
    docs_text: str
    previous_report: str
    round_index: int
    profile_path: str
    strategy_path: str
    profile_source: str
    contract_path: str
    harness_output_dir: str
    summary: RunSummary
    reports: list[str]


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
        portfolio_size: int,
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
        self.portfolio_size = portfolio_size
        self.profile_mode = profile_mode
        self.deepseek_model = deepseek_model
        self.project_root = project_root.resolve()

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
        return {
            "profile_path": str(profile_path),
            "strategy_path": str(strategy_path),
            "profile_source": source,
        }

    def _build_contract(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        round_dir = self.output_dir / f"round_{round_index:02d}"
        paths = sorted(self.instance_dir.glob(self.pattern))
        if self.max_instances is not None:
            paths = paths[: self.max_instances]
        if not paths:
            raise FileNotFoundError(f"no instance files matched {self.instance_dir / self.pattern}")

        resources = {"strategy_profile": str(Path(state["profile_path"]))}
        evaluator = "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
        if self.best_known_csv:
            resources["best_known_csv"] = str(self.best_known_csv)
            evaluator += " --best-known-csv {best_known_csv}"

        payload = {
            "task_id": f"standard_fjsp_agent_round_{round_index:02d}",
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
                "solver": (
                    "python examples/standard_fjsp_portfolio_solver.py "
                    "--input {instance} --output {solution} --seed {seed} "
                    f"--portfolio-size {self.portfolio_size} "
                    "--strategy-profile {strategy_profile}"
                ),
                "evaluator": evaluator,
                "quick_test": "python -m compileall harness_agent examples",
            },
            "budget": {
                "rounds": 1,
                "seeds": self.seeds,
                "timeout_seconds": self.timeout_seconds,
            },
            "paths": {
                "allowed_paths": ["examples", "harness_agent", "configs"],
                "forbidden_paths": [".git", "outputs"],
            },
            "resources": resources,
        }
        contract_path = round_dir / "contract.json"
        contract_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"contract_path": str(contract_path)}

    def _run_harness(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        contract = TaskContract.load(Path(state["contract_path"]))
        errors = contract.validate(self.project_root)
        if errors:
            raise RuntimeError(f"generated contract is invalid: {errors}")
        harness_output_dir = self.output_dir / f"round_{round_index:02d}" / "harness"
        runner = GraphHarnessRunner(contract=contract, project_root=self.project_root, output_dir=harness_output_dir)
        try:
            summary = runner.run()
        finally:
            runner.close()
        return {
            "summary": summary,
            "harness_output_dir": str(harness_output_dir),
        }

    def _reflect(self, state: StandardAgentState) -> StandardAgentState:
        round_index = int(state.get("round_index", 0))
        report_path = Path(state["harness_output_dir"]) / "report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        reports = list(state.get("reports", []))
        reports.append(report)
        reflection = self._local_reflection(round_index, state, report)
        reflection_path = self.output_dir / f"round_{round_index:02d}" / "reflection.md"
        reflection_path.write_text(reflection, encoding="utf-8")
        return {
            "previous_report": report,
            "reports": reports,
            "round_index": round_index + 1,
        }

    def _next_after_reflect(self, state: StandardAgentState) -> str:
        return "continue" if int(state.get("round_index", 0)) < self.max_rounds else "end"

    def _local_reflection(self, round_index: int, state: StandardAgentState, report: str) -> str:
        summary = self._summary_payload(state.get("summary"))
        return (
            f"# Round {round_index:02d} Reflection\n\n"
            f"- Strategy source: `{state.get('profile_source')}`\n"
            f"- Strategy file: `{state.get('strategy_path')}`\n"
            f"- Harness output: `{state.get('harness_output_dir')}`\n"
            f"- Summary: `{json.dumps(summary, ensure_ascii=False)}`\n\n"
            "The evaluator remains the source of truth. The next round may use this report "
            "as feedback for a new strategy profile, but it must not reuse solution files as warm starts.\n\n"
            "## Report Excerpt\n\n"
            f"{report[:4000]}\n"
        )

    def _write_agent_report(self, state: StandardAgentState) -> None:
        lines = [
            "# Standard FJSP Agent Report",
            "",
            f"- Rounds requested: {self.max_rounds}",
            f"- Profile mode: `{self.profile_mode}`",
            f"- DeepSeek model: `{self.deepseek_model}`",
            f"- Pattern: `{self.pattern}`",
            f"- Last summary: `{json.dumps(self._summary_payload(state.get('summary')), ensure_ascii=False)}`",
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
