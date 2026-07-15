"""按任务图执行依赖节点，并把节点结果交给固定 Core 汇总。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from harness_agent.core.runner import HarnessRunner, RunSummary


class HarnessGraphState(TypedDict, total=False):
    planned_runs: list[dict[str, Any]]
    cursor: int
    summary: RunSummary


class GraphHarnessRunner(HarnessRunner):
    """LangGraph-based orchestration layer for harness experiments.

    The graph keeps orchestration explicit while reusing the deterministic
    execution, evaluator, ledger, and report logic from HarnessRunner.
    """

    def run(self) -> RunSummary:
        app = self._build_graph()
        final_state = app.invoke({})
        return final_state["summary"]

    def _build_graph(self):
        workflow = StateGraph(HarnessGraphState)
        workflow.add_node("prepare_experiments", self._graph_prepare_experiments)
        workflow.add_node("run_experiment", self._graph_run_experiment)
        workflow.add_node("summarize_results", self._graph_summarize_results)

        workflow.add_edge(START, "prepare_experiments")
        workflow.add_conditional_edges(
            "prepare_experiments",
            self._graph_next_after_prepare,
            {
                "run": "run_experiment",
                "summarize": "summarize_results",
            },
        )
        workflow.add_conditional_edges(
            "run_experiment",
            self._graph_next_after_experiment,
            {
                "run": "run_experiment",
                "summarize": "summarize_results",
            },
        )
        workflow.add_edge("summarize_results", END)
        return workflow.compile()

    def _graph_prepare_experiments(self, state: HarnessGraphState) -> HarnessGraphState:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.experiment_root.mkdir(parents=True, exist_ok=True)
        self._run_quick_test()

        planned_runs: list[dict[str, Any]] = []
        for round_index in range(self.contract.budget.rounds):
            for instance in self.contract.instances:
                for seed in self.contract.budget.seeds:
                    planned_runs.append(
                        {
                            "round_index": round_index,
                            "instance_id": instance.id,
                            "instance_path": str(instance.path),
                            "seed": seed,
                        }
                    )
        return {"planned_runs": planned_runs, "cursor": 0}

    def _graph_run_experiment(self, state: HarnessGraphState) -> HarnessGraphState:
        planned_runs = state.get("planned_runs", [])
        cursor = int(state.get("cursor", 0))
        worker_count = max(1, self.contract.budget.max_workers)
        batch = planned_runs[cursor : cursor + worker_count]
        self._run_many(
            [
                {
                    "round_index": int(run_spec["round_index"]),
                    "instance_id": str(run_spec["instance_id"]),
                    "instance_path": Path(str(run_spec["instance_path"])),
                    "seed": int(run_spec["seed"]),
                }
                for run_spec in batch
            ]
        )
        return {"cursor": cursor + len(batch)}

    def _graph_summarize_results(self, state: HarnessGraphState) -> HarnessGraphState:
        summary = self._summarize()
        self._write_report(summary)
        return {"summary": summary}

    def _graph_next_after_prepare(self, state: HarnessGraphState) -> str:
        return "run" if state.get("planned_runs") else "summarize"

    def _graph_next_after_experiment(self, state: HarnessGraphState) -> str:
        planned_runs = state.get("planned_runs", [])
        cursor = int(state.get("cursor", 0))
        return "run" if cursor < len(planned_runs) else "summarize"
