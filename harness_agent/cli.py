from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .awls_benchmark import AwlsBenchmarkRequest, run_awls_benchmark
from .awls_compare import AwlsCompareRequest, compare_awls_benchmarks
from .awls_zi_evolution import AwlsZiEvolutionRequest, run_awls_zi_evolution
from .benchmark_suite import BenchmarkSuiteRequest, run_benchmark_suite
from .context_packet import ContextPacketRequest, write_context_packet
from .contract_builder import (
    DraftContractRequest,
    draft_review_report_path,
    write_confirmed_contract,
    write_draft_contract,
)
from .demo import StandardDemoRequest, run_standard_demo
from .evidence import EvidenceIndexRequest, build_evidence_index
from .graph_runner import GraphHarnessRunner
from .health_check import HealthCheckRequest, run_health_check
from .intent_alignment import IntentAlignmentRequest, write_intent_alignment
from .loop_runner import run_worker_loop
from .models import TaskContract
from .project_intake import ProjectIntakeRequest, write_project_intake
from .runner import HarnessRunner
from .standard_agent import StandardFjspAgentRunner
from .standard_pipeline import (
    StandardPipelineAblationRequest,
    StandardPipelineLoopRequest,
    StandardPipelineRequest,
    run_standard_pipeline_ablation,
    run_standard_pipeline,
    run_standard_pipeline_loop,
)
from .standard_worker_loop import StandardWorkerLoopRequest, run_standard_worker_loop
from .web_app import DEFAULT_OUTPUT_ROOT, run_web_server
from .worker import ExperimentSpec, NullWorker, WorkerResult
from .worker_cycle import run_worker_cycle


def add_awls_arguments(parser: argparse.ArgumentParser, *, prefix: str = "", default_time_limit: float = 10.0) -> None:
    """Register AWLS solver-template options on a CLI subcommand."""

    option_prefix = f"--{prefix}-" if prefix else "--"
    dest_prefix = f"{prefix}_" if prefix else ""
    parser.add_argument(f"{option_prefix}awls-restarts", dest=f"{dest_prefix}awls_restarts", type=int, default=2)
    parser.add_argument(
        f"{option_prefix}awls-cycles-per-restart",
        dest=f"{dest_prefix}awls_cycles_per_restart",
        type=int,
        default=1000,
    )
    parser.add_argument(f"{option_prefix}awls-iterations", dest=f"{dest_prefix}awls_iterations", type=int, default=10000)
    parser.add_argument(
        f"{option_prefix}awls-time-limit-sec",
        dest=f"{dest_prefix}awls_time_limit_sec",
        type=float,
        default=default_time_limit,
    )
    parser.add_argument(
        f"{option_prefix}awls-init",
        dest=f"{dest_prefix}awls_init",
        choices=["random", "greedy", "mixed"],
        default="random",
    )
    parser.add_argument(
        f"{option_prefix}awls-exact-select-top-k",
        dest=f"{dest_prefix}awls_exact_select_top_k",
        type=int,
        default=0,
    )
    parser.add_argument(f"{option_prefix}awls-beta", dest=f"{dest_prefix}awls_beta", type=int, default=500)
    parser.add_argument(f"{option_prefix}awls-gamma", dest=f"{dest_prefix}awls_gamma", type=int, default=40)
    parser.add_argument(f"{option_prefix}awls-theta", dest=f"{dest_prefix}awls_theta", type=int, default=5)
    parser.add_argument(
        f"{option_prefix}awls-portfolio-lanes",
        dest=f"{dest_prefix}awls_portfolio_lanes",
        default="",
        help="Optional AWLS lane portfolio: seed:init:restarts[:seconds], comma-separated.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FJSP Harness Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-contract", help="validate a task contract")
    validate.add_argument("--contract", required=True, type=Path)
    validate.add_argument("--project-root", type=Path, default=Path.cwd())

    confirm = subparsers.add_parser("confirm-contract", help="mark a reviewed draft contract as human-confirmed")
    confirm.add_argument("--contract", required=True, type=Path)
    confirm.add_argument("--output", required=True, type=Path)
    confirm.add_argument("--confirmed-by", required=True)
    confirm.add_argument("--note", default="")

    context = subparsers.add_parser("build-context-packet", help="package a bounded context packet for a coding worker")
    context.add_argument("--contract", required=True, type=Path)
    context.add_argument("--output", required=True, type=Path)
    context.add_argument("--doc", action="append", type=Path, default=[])
    context.add_argument("--knowledge-card", action="append", type=Path, default=[])
    context.add_argument("--hypothesis", default="")
    context.add_argument("--previous-report", type=Path)
    context.add_argument("--previous-memory", type=Path, help="previous standard_pipeline_memory.json handoff")
    context.add_argument("--project-intake-manifest", type=Path)
    context.add_argument("--max-chars-per-source", type=int, default=12000)

    run_worker = subparsers.add_parser("run-worker", help="run a coding worker against a context packet")
    run_worker.add_argument("--worker", choices=["null", "deepseek", "opencode"], default="null")
    run_worker.add_argument("--context-packet", required=True, type=Path)
    run_worker.add_argument("--worktree", type=Path, default=Path.cwd())
    run_worker.add_argument("--output-dir", required=True, type=Path)
    run_worker.add_argument("--task-id", default="worker_task")
    run_worker.add_argument("--experiment-id", default="worker_experiment")
    run_worker.add_argument("--max-steps", type=int, default=8)
    run_worker.add_argument("--max-runtime-seconds", type=int, default=300)
    run_worker.add_argument("--apply", action="store_true", help="apply accepted file replacements inside --worktree")
    run_worker.add_argument("--deepseek-model", default="deepseek-v4-pro")
    run_worker.add_argument("--opencode-model", help="optional OpenCode model override, for example provider/model")

    cycle = subparsers.add_parser("run-worker-cycle", help="run worker proposal/apply/evaluate cycle in an isolated worktree")
    cycle.add_argument("--contract", required=True, type=Path)
    cycle.add_argument("--context-packet", required=True, type=Path)
    cycle.add_argument("--output-dir", required=True, type=Path)
    cycle.add_argument("--project-root", type=Path, default=Path.cwd())
    cycle.add_argument("--worker", choices=["null", "deepseek", "opencode"], default="null")
    cycle.add_argument("--experiment-id", default="worker_cycle")
    cycle.add_argument("--max-steps", type=int, default=8)
    cycle.add_argument("--max-runtime-seconds", type=int, default=300)
    cycle.add_argument("--apply-worker", action="store_true", help="apply accepted worker edits before Core evaluation")
    cycle.add_argument("--allow-draft", action="store_true", help="allow exploratory cycles on unconfirmed draft contracts")
    cycle.add_argument("--deepseek-model", default="deepseek-v4-pro")
    cycle.add_argument("--opencode-model", help="optional OpenCode model override, for example provider/model")

    loop = subparsers.add_parser("run-worker-loop", help="run multiple worker cycles with promotion/rollback decisions")
    loop.add_argument("--contract", required=True, type=Path)
    loop.add_argument("--context-packet", required=True, type=Path)
    loop.add_argument("--output-dir", required=True, type=Path)
    loop.add_argument("--project-root", type=Path, default=Path.cwd())
    loop.add_argument("--worker", choices=["null", "deepseek", "opencode"], default="null")
    loop.add_argument("--experiment-id", default="worker_loop")
    loop.add_argument("--iterations", type=int, default=3)
    loop.add_argument("--max-steps", type=int, default=8)
    loop.add_argument("--max-runtime-seconds", type=int, default=300)
    loop.add_argument("--apply-worker", action="store_true", help="apply accepted worker edits before each Core evaluation")
    loop.add_argument("--allow-draft", action="store_true", help="allow exploratory loops on unconfirmed draft contracts")
    loop.add_argument("--deepseek-model", default="deepseek-v4-pro")
    loop.add_argument("--opencode-model", help="optional OpenCode model override, for example provider/model")

    draft = subparsers.add_parser("draft-contract", help="build a human-review draft task contract from documents")
    draft.add_argument("--doc", action="append", type=Path, default=[], help="requirement/IO/metric document path")
    draft.add_argument("--instance", action="append", type=Path, default=[], help="instance file or directory path")
    draft.add_argument("--output", required=True, type=Path)
    draft.add_argument("--project-root", type=Path, default=Path.cwd())
    draft.add_argument("--task-id", default="draft_task")
    draft.add_argument("--problem-family", help="override inferred problem family, for example FJSP")
    draft.add_argument(
        "--objective",
        action="append",
        default=[],
        help="objective override in name:direction[:priority] format, e.g. makespan:minimize:1",
    )
    draft.add_argument("--solver-cmd", help="solver command template")
    draft.add_argument("--evaluator-cmd", help="evaluator command template")
    draft.add_argument("--quick-test", help="quick correctness command")
    draft.add_argument("--rounds", type=int, default=1)
    draft.add_argument("--seeds", default="0")
    draft.add_argument("--timeout-seconds", type=int, default=300)
    draft.add_argument("--max-workers", type=int, default=1)
    draft.add_argument("--allowed-path", action="append", default=[])
    draft.add_argument("--forbidden-path", action="append", default=[".git", "outputs"])
    draft.add_argument("--resource", action="append", default=[], help="resource mapping in key=path format")

    run = subparsers.add_parser("run", help="run solver/evaluator experiments from a task contract")
    run.add_argument("--contract", required=True, type=Path)
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--runner", choices=["langgraph", "linear"], default="langgraph")
    run.add_argument(
        "--allow-draft",
        action="store_true",
        help="allow exploratory runs on a draft contract that still requires human confirmation",
    )

    build_standard = subparsers.add_parser("build-standard-contract", help="create a standard FJSP task contract")
    build_standard.add_argument("--instance-dir", required=True, type=Path)
    build_standard.add_argument("--pattern", default="*.txt")
    build_standard.add_argument("--output", required=True, type=Path)
    build_standard.add_argument("--best-known-csv", type=Path)
    build_standard.add_argument("--task-id", default="standard_fjsp_batch")
    build_standard.add_argument("--rounds", type=int, default=1)
    build_standard.add_argument("--seeds", default="0,1,2")
    build_standard.add_argument("--timeout-seconds", type=int, default=60)
    build_standard.add_argument("--max-workers", type=int, default=1)
    build_standard.add_argument("--max-instances", type=int)
    build_standard.add_argument("--solver", choices=["local-search", "portfolio", "awls", "ect"], default="portfolio")
    build_standard.add_argument("--portfolio-size", type=int, default=64)
    build_standard.add_argument("--strategy-profile", type=Path)
    build_standard.add_argument("--local-search-restarts", type=int, default=2)
    build_standard.add_argument("--local-search-initial-pool-size", type=int, default=1)
    build_standard.add_argument("--local-search-iterations", type=int, default=80)
    build_standard.add_argument("--local-search-neighbor-limit", type=int, default=180)
    build_standard.add_argument("--local-search-time-limit-sec", type=float, default=4.0)
    build_standard.add_argument(
        "--local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"],
        default="random",
    )
    add_awls_arguments(build_standard, default_time_limit=10.0)

    project_intake = subparsers.add_parser("project-intake", help="scan a project and write a bounded context manifest")
    project_intake.add_argument("--project-root", type=Path, default=Path.cwd())
    project_intake.add_argument("--output-dir", required=True, type=Path)
    project_intake.add_argument("--contract", type=Path)
    project_intake.add_argument("--max-files", type=int, default=200)
    project_intake.add_argument("--max-symbols-per-file", type=int, default=20)

    health = subparsers.add_parser("health-check", help="run contract quick-test and benchmark stability probes")
    health.add_argument("--contract", required=True, type=Path)
    health.add_argument("--output-dir", required=True, type=Path)
    health.add_argument("--project-root", type=Path, default=Path.cwd())
    health.add_argument("--repeats", type=int, default=2)
    health.add_argument("--max-instances", type=int, default=1)
    health.add_argument("--max-seeds", type=int, default=1)
    health.add_argument("--allow-draft", action="store_true")

    intent = subparsers.add_parser("intent-alignment", help="write a reviewable optimization intent summary")
    intent.add_argument("--contract", required=True, type=Path)
    intent.add_argument("--output-dir", required=True, type=Path)
    intent.add_argument("--project-root", type=Path, default=Path.cwd())
    intent.add_argument("--health-manifest", type=Path)
    intent.add_argument("--benchmark-source", default="user_provided")
    intent.add_argument("--allow-draft", action="store_true")
    intent.add_argument("--no-require-health", action="store_true")

    subparsers.add_parser("worker-status", help="show available coding worker backends")

    standard_agent = subparsers.add_parser("run-standard-agent", help="run the document-driven standard FJSP agent loop")
    standard_agent.add_argument("--doc", action="append", type=Path, default=[])
    standard_agent.add_argument("--instance-dir", required=True, type=Path)
    standard_agent.add_argument("--pattern", default="*.txt")
    standard_agent.add_argument("--best-known-csv", type=Path)
    standard_agent.add_argument("--output-dir", required=True, type=Path)
    standard_agent.add_argument("--project-root", type=Path, default=Path.cwd())
    standard_agent.add_argument("--max-instances", type=int)
    standard_agent.add_argument("--max-rounds", type=int, default=1)
    standard_agent.add_argument("--seeds", default="0,1,2")
    standard_agent.add_argument("--timeout-seconds", type=int, default=120)
    standard_agent.add_argument("--max-workers", type=int, default=1)
    standard_agent.add_argument("--solver", choices=["local-search", "portfolio", "awls"], default="local-search")
    standard_agent.add_argument("--portfolio-size", type=int, default=96)
    standard_agent.add_argument("--local-search-restarts", type=int, default=2)
    standard_agent.add_argument("--local-search-initial-pool-size", type=int, default=1)
    standard_agent.add_argument("--local-search-iterations", type=int, default=80)
    standard_agent.add_argument("--local-search-neighbor-limit", type=int, default=180)
    standard_agent.add_argument("--local-search-time-limit-sec", type=float, default=4.0)
    standard_agent.add_argument(
        "--local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"],
        default="random",
    )
    standard_agent.add_argument(
        "--local-search-neighborhood-profiles",
        help="comma-separated neighborhood profiles to cross-evaluate in each agent round",
    )
    standard_agent.add_argument(
        "--local-search-run-profiles",
        help=(
            "comma-separated local-search run presets to cross-evaluate. "
            "Built-ins: current, balanced-random, balanced-combined, balanced-hgtsa, balanced-awls, deep-combined, deep-hgtsa"
        ),
    )
    add_awls_arguments(standard_agent, default_time_limit=10.0)
    standard_agent.add_argument("--strategy-candidates", type=int, default=1)
    standard_agent.add_argument("--profile-mode", choices=["auto", "deepseek", "template"], default="auto")
    standard_agent.add_argument("--deepseek-model", default="deepseek-v4-pro")

    demo = subparsers.add_parser("run-demo", help="run a compact document-to-evaluator loop demo")
    demo.add_argument("--doc", action="append", type=Path, default=[])
    demo.add_argument("--instance-dir", required=True, type=Path)
    demo.add_argument("--pattern", default="*.txt")
    demo.add_argument("--best-known-csv", type=Path)
    demo.add_argument("--output-dir", required=True, type=Path)
    demo.add_argument("--project-root", type=Path, default=Path.cwd())
    demo.add_argument("--max-instances", type=int)
    demo.add_argument("--max-rounds", type=int, default=2)
    demo.add_argument("--seeds", default="0")
    demo.add_argument("--timeout-seconds", type=int, default=60)
    demo.add_argument("--max-workers", type=int, default=1)
    demo.add_argument("--solver", choices=["local-search", "portfolio", "awls"], default="portfolio")
    demo.add_argument("--portfolio-size", type=int, default=16)
    demo.add_argument("--local-search-restarts", type=int, default=1)
    demo.add_argument("--local-search-initial-pool-size", type=int, default=1)
    demo.add_argument("--local-search-iterations", type=int, default=20)
    demo.add_argument("--local-search-neighbor-limit", type=int, default=60)
    demo.add_argument("--local-search-time-limit-sec", type=float, default=2.0)
    demo.add_argument(
        "--local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"],
        default="random",
    )
    demo.add_argument("--local-search-neighborhood-profiles")
    demo.add_argument("--local-search-run-profiles")
    add_awls_arguments(demo, default_time_limit=5.0)
    demo.add_argument("--strategy-candidates", type=int, default=2)
    demo.add_argument("--profile-mode", choices=["auto", "deepseek", "template"], default="template")
    demo.add_argument("--deepseek-model", default="deepseek-v4-pro")

    suite = subparsers.add_parser("run-benchmark-suite", help="run configured standard FJSP benchmark demo suites")
    suite.add_argument("--config", required=True, type=Path)
    suite.add_argument("--output-dir", required=True, type=Path)
    suite.add_argument("--project-root", type=Path, default=Path.cwd())
    suite.add_argument("--max-suites", type=int)

    awls_benchmark = subparsers.add_parser("run-awls-benchmark", help="run direct AWLS standard-FJSP benchmark")
    awls_benchmark.add_argument("--instance-dir", required=True, type=Path)
    awls_benchmark.add_argument("--pattern", default="*.txt")
    awls_benchmark.add_argument("--output-dir", required=True, type=Path)
    awls_benchmark.add_argument("--best-known-csv", type=Path)
    awls_benchmark.add_argument("--max-instances", type=int)
    awls_benchmark.add_argument(
        "--instance-name",
        action="append",
        default=[],
        help="Exact instance file name to run; can be repeated. Overrides sample-count/max-instances.",
    )
    awls_benchmark.add_argument(
        "--instance-list",
        type=Path,
        help="Text file with one exact instance file name per line. Overrides sample-count/max-instances.",
    )
    awls_benchmark.add_argument("--sample-count", type=int, help="Optional family-balanced benchmark sample size.")
    awls_benchmark.add_argument("--sample-seed", type=int, default=0, help="Seed for reproducible family-balanced sampling.")
    awls_benchmark.add_argument(
        "--include-families",
        default="",
        help="Optional comma-separated FJSP family filter, for example barnes,brandimarte,dauzere,hurink.",
    )
    awls_benchmark.add_argument("--seeds", default="0")
    awls_benchmark.add_argument("--max-workers", type=int, default=1)
    add_awls_arguments(awls_benchmark, default_time_limit=10.0)
    awls_benchmark.add_argument(
        "--awls-critical-block-exhaustive-pct",
        type=int,
        default=0,
        help="Percent chance to evaluate all critical blocks first; 5 approximates the C++ AWLS exploration branch.",
    )
    awls_benchmark.add_argument("--same-machine-eval", choices=("stable", "cpp-fast"), default="stable")
    awls_benchmark.add_argument(
        "--awls-zi-policy",
        choices=("cpp", "none", "sqrt", "aggressive", "critical"),
        default="cpp",
        help="Adaptive zi perturbation policy used by the AWLS move evaluator.",
    )
    awls_benchmark.add_argument(
        "--awls-time-policy",
        choices=("fixed", "mae2019", "mae2019-hour"),
        default="fixed",
        help="Instance time budget policy. mae2019 uses 90s for Barnes/Brandimarte and 300s for Dauzere/Hurink.",
    )
    awls_benchmark.add_argument("--resume", action="store_true", help="reuse completed metrics/solutions in output-dir")

    awls_compare = subparsers.add_parser(
        "compare-awls-benchmarks",
        help="compare two direct AWLS benchmark summary.json files instance by instance",
    )
    awls_compare.add_argument("--baseline-summary", required=True, type=Path)
    awls_compare.add_argument("--candidate-summary", required=True, type=Path)
    awls_compare.add_argument("--output-dir", required=True, type=Path)

    awls_zi = subparsers.add_parser(
        "run-awls-zi-evolution",
        help="let DeepSeek propose AWLS zi-policy candidates and evaluate them",
    )
    awls_zi.add_argument("--instance-dir", required=True, type=Path)
    awls_zi.add_argument("--pattern", default="*.txt")
    awls_zi.add_argument("--output-dir", required=True, type=Path)
    awls_zi.add_argument("--best-known-csv", type=Path)
    awls_zi.add_argument("--max-instances", type=int)
    awls_zi.add_argument("--instance-name", action="append", default=[])
    awls_zi.add_argument("--instance-list", type=Path)
    awls_zi.add_argument("--sample-count", type=int)
    awls_zi.add_argument("--sample-seed", type=int, default=0)
    awls_zi.add_argument("--include-families", default="")
    awls_zi.add_argument("--rounds", type=int, default=3)
    awls_zi.add_argument("--candidates-per-round", type=int, default=2)
    awls_zi.add_argument("--deepseek-model", default="deepseek-v4-pro")
    awls_zi.add_argument("--seeds", default="0")
    awls_zi.add_argument("--max-workers", type=int, default=1)
    add_awls_arguments(awls_zi, default_time_limit=10.0)
    awls_zi.add_argument("--same-machine-eval", choices=("stable", "cpp-fast"), default="stable")
    awls_zi.add_argument("--awls-time-policy", choices=("fixed", "mae2019", "mae2019-hour"), default="fixed")
    awls_zi.add_argument(
        "--baseline-summary",
        type=Path,
        help="Optional prior AWLS summary to include as baseline evidence without rerunning it.",
    )

    web = subparsers.add_parser("serve-web", help="serve the local document-to-loop web demo UI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=7860)
    web.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)

    standard_worker = subparsers.add_parser("run-standard-worker-loop", help="run a standard FJSP coding-worker evolution loop")
    standard_worker.add_argument("--doc", action="append", type=Path, default=[])
    standard_worker.add_argument("--knowledge-card", action="append", type=Path, default=[])
    standard_worker.add_argument("--instance-dir", required=True, type=Path)
    standard_worker.add_argument("--pattern", default="*.txt")
    standard_worker.add_argument("--best-known-csv", type=Path)
    standard_worker.add_argument("--previous-memory", type=Path, help="previous standard_pipeline_memory.json handoff")
    standard_worker.add_argument("--output-dir", required=True, type=Path)
    standard_worker.add_argument("--project-root", type=Path, default=Path.cwd())
    standard_worker.add_argument("--max-instances", type=int)
    standard_worker.add_argument("--seeds", default="0")
    standard_worker.add_argument("--timeout-seconds", type=int, default=60)
    standard_worker.add_argument("--max-workers", type=int, default=1)
    standard_worker.add_argument("--solver", choices=["local-search", "portfolio", "awls"], default="portfolio")
    standard_worker.add_argument("--portfolio-size", type=int, default=16)
    standard_worker.add_argument("--local-search-restarts", type=int, default=1)
    standard_worker.add_argument("--local-search-initial-pool-size", type=int, default=1)
    standard_worker.add_argument("--local-search-iterations", type=int, default=40)
    standard_worker.add_argument("--local-search-neighbor-limit", type=int, default=100)
    standard_worker.add_argument("--local-search-time-limit-sec", type=float, default=2.0)
    standard_worker.add_argument(
        "--local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"],
        default="combined",
    )
    add_awls_arguments(standard_worker, default_time_limit=10.0)
    standard_worker.add_argument("--worker", choices=["null", "deepseek", "opencode"], default="null")
    standard_worker.add_argument("--iterations", type=int, default=1)
    standard_worker.add_argument("--max-steps", type=int, default=4)
    standard_worker.add_argument("--max-runtime-seconds", type=int, default=120)
    standard_worker.add_argument("--apply-worker", action="store_true")
    standard_worker.add_argument("--experiment-id", default="standard_worker_loop")
    standard_worker.add_argument("--hypothesis", default="")
    standard_worker.add_argument("--deepseek-model", default="deepseek-v4-pro")
    standard_worker.add_argument("--opencode-model")

    pipeline = subparsers.add_parser(
        "run-standard-pipeline",
        help="run benchmark suite, coding-worker loop, and evidence index as one standard FJSP pipeline",
    )
    pipeline.add_argument("--suite-config", required=True, type=Path)
    pipeline.add_argument("--output-dir", required=True, type=Path)
    pipeline.add_argument("--project-root", type=Path, default=Path.cwd())
    pipeline.add_argument("--loop-rounds", type=int, default=1, help="run multiple pipeline iterations and chain memory")
    pipeline.add_argument(
        "--ablation",
        choices=["none", "memory-vs-fixed"],
        default="none",
        help="run a paired pipeline-loop ablation instead of a single pipeline",
    )
    pipeline.add_argument(
        "--no-adapt-worker-hypothesis",
        action="store_true",
        help="keep the same worker hypothesis across loop iterations instead of deriving it from prior memory",
    )
    pipeline.add_argument("--max-suites", type=int)
    pipeline.add_argument("--skip-project-intake", action="store_true")
    pipeline.add_argument("--project-intake-max-files", type=int, default=200)
    pipeline.add_argument("--health-contract", type=Path)
    pipeline.add_argument("--health-repeats", type=int, default=2)
    pipeline.add_argument("--health-max-instances", type=int, default=1)
    pipeline.add_argument("--health-max-seeds", type=int, default=1)
    pipeline.add_argument("--health-allow-draft", action="store_true")
    pipeline.add_argument("--benchmark-source", default="user_provided")
    pipeline.add_argument("--worker", choices=["null", "deepseek", "opencode"], default="null")
    pipeline.add_argument("--worker-doc", action="append", type=Path, default=[])
    pipeline.add_argument("--worker-knowledge-card", action="append", type=Path, default=[])
    pipeline.add_argument("--previous-memory", type=Path, help="previous standard_pipeline_memory.json handoff")
    pipeline.add_argument("--worker-instance-dir", required=True, type=Path)
    pipeline.add_argument("--worker-pattern", default="*.txt")
    pipeline.add_argument("--worker-best-known-csv", type=Path)
    pipeline.add_argument("--worker-max-instances", type=int)
    pipeline.add_argument("--worker-seeds", default="0")
    pipeline.add_argument("--worker-timeout-seconds", type=int, default=60)
    pipeline.add_argument("--worker-max-workers", type=int, default=1)
    pipeline.add_argument("--worker-solver", choices=["local-search", "portfolio", "awls"], default="portfolio")
    pipeline.add_argument("--worker-portfolio-size", type=int, default=16)
    pipeline.add_argument("--worker-local-search-restarts", type=int, default=1)
    pipeline.add_argument("--worker-local-search-initial-pool-size", type=int, default=1)
    pipeline.add_argument("--worker-local-search-iterations", type=int, default=40)
    pipeline.add_argument("--worker-local-search-neighbor-limit", type=int, default=100)
    pipeline.add_argument("--worker-local-search-time-limit-sec", type=float, default=2.0)
    pipeline.add_argument(
        "--worker-local-search-neighborhood-profile",
        choices=["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"],
        default="combined",
    )
    add_awls_arguments(pipeline, prefix="worker", default_time_limit=10.0)
    pipeline.add_argument("--worker-iterations", type=int, default=1)
    pipeline.add_argument("--worker-max-steps", type=int, default=4)
    pipeline.add_argument("--worker-max-runtime-seconds", type=int, default=120)
    pipeline.add_argument("--worker-apply", action="store_true")
    pipeline.add_argument("--worker-experiment-id", default="standard_pipeline_worker_loop")
    pipeline.add_argument("--worker-hypothesis", default="")
    pipeline.add_argument("--deepseek-model", default="deepseek-v4-pro")
    pipeline.add_argument("--opencode-model")
    pipeline.add_argument("--title", default="Standard FJSP Loop Pipeline Evidence")

    evidence = subparsers.add_parser("build-evidence-index", help="index generated loop-engineering manifests")
    evidence.add_argument("--input-dir", action="append", required=True, type=Path)
    evidence.add_argument("--output-dir", required=True, type=Path)
    evidence.add_argument("--title", default="Loop Engineering Evidence Index")
    return parser


def validate_contract(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    result = {
        "task_id": contract.task_id,
        "problem_family": contract.problem_family,
        "instances": len(contract.instances),
        "objectives": [objective.name for objective in contract.objectives],
        "review_status": contract.review_status,
        "requires_human_confirmation": contract.requires_human_confirmation,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def draft_contract(args: argparse.Namespace) -> int:
    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    request = DraftContractRequest(
        task_id=args.task_id,
        docs=args.doc,
        instances=args.instance,
        output=args.output,
        problem_family=args.problem_family,
        objectives=args.objective,
        solver_cmd=args.solver_cmd,
        evaluator_cmd=args.evaluator_cmd,
        quick_test_cmd=args.quick_test,
        rounds=args.rounds,
        seeds=seeds or [0],
        timeout_seconds=args.timeout_seconds,
        max_workers=max(1, args.max_workers),
        allowed_paths=args.allowed_path,
        forbidden_paths=args.forbidden_path,
        resources=args.resource,
    )
    output = write_draft_contract(request)
    contract = TaskContract.load(output)
    errors = contract.validate(args.project_root)
    payload = {
        "status": "draft_created",
        "output": str(output.resolve()),
        "review_report": str(draft_review_report_path(output).resolve()),
        "task_id": contract.task_id,
        "problem_family": contract.problem_family,
        "instances": len(contract.instances),
        "objectives": [objective.name for objective in contract.objectives],
        "validation_errors": errors,
        "review_required": True,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def confirm_contract(args: argparse.Namespace) -> int:
    output = write_confirmed_contract(
        contract_path=args.contract,
        output_path=args.output,
        confirmed_by=args.confirmed_by,
        note=args.note,
    )
    contract = TaskContract.load(output)
    payload = {
        "status": "confirmed",
        "output": str(output.resolve()),
        "task_id": contract.task_id,
        "review_status": contract.review_status,
        "requires_human_confirmation": contract.requires_human_confirmation,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_context_packet_cmd(args: argparse.Namespace) -> int:
    request = ContextPacketRequest(
        contract_path=args.contract,
        output_path=args.output,
        docs=args.doc,
        knowledge_cards=args.knowledge_card,
        hypothesis=args.hypothesis,
        previous_report=args.previous_report,
        previous_pipeline_memory=args.previous_memory,
        project_intake_manifest=args.project_intake_manifest,
        max_chars_per_source=max(1000, args.max_chars_per_source),
    )
    output = write_context_packet(request)
    payload = json.loads(output.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "context_packet_created",
                "output": str(output.resolve()),
                "task_id": payload["task"]["task_id"],
                "review_status": payload["task"]["review_status"],
                "documents": len(payload["documents"]),
                "knowledge_cards": len(payload["knowledge_cards"]),
                "project_intake": bool(payload.get("project_intake")),
                "previous_pipeline_memory": bool(payload.get("previous_pipeline_memory")),
                "packet_hash": payload["packet_hash"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_worker_cmd(args: argparse.Namespace) -> int:
    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    spec = ExperimentSpec(
        task_id=args.task_id,
        experiment_id=args.experiment_id,
        context_packet_path=str(args.context_packet),
        worktree_path=str(args.worktree),
        max_steps=max(1, args.max_steps),
        max_runtime_seconds=max(1, args.max_runtime_seconds),
        output_dir=str(args.output_dir),
        apply_changes=bool(args.apply),
    )
    result = worker.run_experiment(spec)
    result_path = args.output_dir / "worker_result.json"
    result_path.write_text(json.dumps(worker_result_payload(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result.status,
                "changed_files": result.changed_files,
                "summary": result.summary,
                "result": str(result_path.resolve()),
                "artifacts": result.artifacts or {},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status not in {"failed", "unavailable"} else 1


def run_worker_cycle_cmd(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    if errors:
        print(json.dumps({"status": "invalid_contract", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    if contract.requires_human_confirmation and not args.allow_draft:
        print(
            json.dumps(
                {
                    "status": "contract_requires_human_confirmation",
                    "review_status": contract.review_status,
                    "message": "Confirm this contract or pass --allow-draft for exploratory cycles.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    result = run_worker_cycle(
        contract=contract,
        project_root=args.project_root,
        output_dir=args.output_dir,
        context_packet_path=args.context_packet,
        worker=worker,
        experiment_id=args.experiment_id,
        max_steps=max(1, args.max_steps),
        max_runtime_seconds=max(1, args.max_runtime_seconds),
        apply_worker_changes=bool(args.apply_worker),
    )
    payload = {
        "status": "ok",
        "worker_status": result.worker_result.status,
        "worker_changed_files": result.worker_result.changed_files,
        "harness_total": result.summary.total,
        "harness_valid": result.summary.valid,
        "harness_failed": result.summary.failed,
        "best_metrics": result.summary.best_metrics,
        "cycle_report": str((args.output_dir / "cycle_report.md").resolve()),
        "cycle_result": str((args.output_dir / "cycle_result.json").resolve()),
        "worktree_delta": str(result.delta_path),
        "worktree_patch": str(result.patch_path),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_worker_loop_cmd(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    if errors:
        print(json.dumps({"status": "invalid_contract", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    if contract.requires_human_confirmation and not args.allow_draft:
        print(
            json.dumps(
                {
                    "status": "contract_requires_human_confirmation",
                    "review_status": contract.review_status,
                    "message": "Confirm this contract or pass --allow-draft for exploratory loops.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    result = run_worker_loop(
        contract=contract,
        project_root=args.project_root,
        output_dir=args.output_dir,
        context_packet_path=args.context_packet,
        worker=worker,
        experiment_id=args.experiment_id,
        iterations=max(0, args.iterations),
        max_steps=max(1, args.max_steps),
        max_runtime_seconds=max(1, args.max_runtime_seconds),
        apply_worker_changes=bool(args.apply_worker),
    )
    payload = {
        "status": "ok",
        "baseline_key": list(result.baseline_key),
        "final_key": list(result.final_key),
        "final_worktree": str(result.final_worktree),
        "rounds": len(result.rounds),
        "promoted_rounds": sum(1 for item in result.rounds if item.decision == "promoted"),
        "loop_report": str((args.output_dir / "loop_report.md").resolve()),
        "loop_result": str((args.output_dir / "loop_result.json").resolve()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def make_worker(name: str, *, deepseek_model: str, opencode_model: str | None = None):
    if name == "null":
        return NullWorker()
    if name == "deepseek":
        from .workers.deepseek_worker import DeepSeekWorker

        return DeepSeekWorker(model=deepseek_model)
    if name == "opencode":
        from .workers.opencode_worker import OpenCodeWorker

        return OpenCodeWorker(model=opencode_model)
    raise ValueError(f"unknown worker: {name}")


def worker_result_payload(result: WorkerResult) -> dict[str, object]:
    return {
        "status": result.status,
        "changed_files": result.changed_files,
        "summary": result.summary,
        "raw_log_path": result.raw_log_path,
        "artifacts": result.artifacts or {},
    }


def run_contract(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    if errors:
        print(json.dumps({"status": "invalid_contract", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    if contract.requires_human_confirmation and not args.allow_draft:
        print(
            json.dumps(
                {
                    "status": "contract_requires_human_confirmation",
                    "review_status": contract.review_status,
                    "message": (
                        "This contract is a generated draft. Run confirm-contract after human review, "
                        "or pass --allow-draft for exploratory runs that must not be treated as formal evidence."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    runner_cls = GraphHarnessRunner if args.runner == "langgraph" else HarnessRunner
    runner = runner_cls(contract=contract, project_root=args.project_root, output_dir=args.output_dir)
    try:
        summary = runner.run()
    finally:
        runner.close()
    print(
        json.dumps(
            {
                "status": "ok",
                "total": summary.total,
                "valid": summary.valid,
                "failed": summary.failed,
                "best_experiment_id": summary.best_experiment_id,
                "best_metrics": summary.best_metrics,
                "best_candidate_id": summary.best_candidate_id,
                "best_candidate_metrics": summary.best_candidate_metrics,
                "pareto_frontier": summary.pareto_frontier or [],
                "validation_summary": summary.validation_summary or {},
                "report": str((args.output_dir / "report.md").resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_standard_contract(args: argparse.Namespace) -> int:
    instances = sorted(args.instance_dir.glob(args.pattern))
    if args.max_instances is not None:
        instances = instances[: args.max_instances]
    if not instances:
        print(json.dumps({"status": "no_instances", "instance_dir": str(args.instance_dir), "pattern": args.pattern}, ensure_ascii=False))
        return 1

    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    resources: dict[str, str] = {}
    solver = "python examples/standard_fjsp_solver.py --input {instance} --output {solution} --seed {seed}"
    if args.solver == "portfolio":
        solver = (
            "python examples/standard_fjsp_portfolio_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {args.portfolio_size}"
        )
        if args.strategy_profile:
            resources["strategy_profile"] = str(args.strategy_profile)
            solver += " --strategy-profile {strategy_profile}"
    elif args.solver == "local-search":
        solver = (
            "python examples/standard_fjsp_local_search_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {args.portfolio_size} "
            f"--restarts {args.local_search_restarts} "
            f"--initial-pool-size {args.local_search_initial_pool_size} "
            f"--iterations {args.local_search_iterations} "
            f"--neighbor-limit {args.local_search_neighbor_limit} "
            f"--time-limit-sec {args.local_search_time_limit_sec} "
            f"--neighborhood-profile {args.local_search_neighborhood_profile}"
        )
        if args.strategy_profile:
            resources["strategy_profile"] = str(args.strategy_profile)
            solver += " --strategy-profile {strategy_profile}"
    elif args.solver == "awls":
        solver = (
            "python examples/standard_fjsp_awls_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--restarts {args.awls_restarts} "
            f"--cycles-per-restart {args.awls_cycles_per_restart} "
            f"--iterations {args.awls_iterations} "
            f"--time-limit-sec {args.awls_time_limit_sec} "
            f"--init {args.awls_init} "
            f"--exact-select-top-k {args.awls_exact_select_top_k} "
            f"--beta {args.awls_beta} "
            f"--gamma {args.awls_gamma} "
            f"--theta {args.awls_theta}"
        )
        if args.awls_portfolio_lanes:
            solver += f' --portfolio-lanes "{args.awls_portfolio_lanes}"'
    evaluator = "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
    if args.best_known_csv:
        resources["best_known_csv"] = str(args.best_known_csv)
        evaluator += " --best-known-csv {best_known_csv}"

    payload = {
        "task_id": args.task_id,
        "problem_family": "FJSP",
        "description": "Generated standard-FJSP benchmark contract with optional best-known gap reporting.",
        "instances": [{"id": path.stem, "path": str(path)} for path in instances],
        "objectives": [
            {
                "name": "makespan",
                "direction": "minimize",
                "priority": 1,
                "invalid_if_missing": True,
            }
        ],
        "commands": {
            "solver": solver,
            "evaluator": evaluator,
            "quick_test": "python -m compileall harness_agent examples",
        },
        "budget": {
            "rounds": args.rounds,
            "seeds": seeds,
            "timeout_seconds": args.timeout_seconds,
            "max_workers": max(1, args.max_workers),
        },
        "paths": {
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "resources": resources,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(args.output.resolve()), "instances": len(instances)}, ensure_ascii=False, indent=2))
    return 0


def worker_status(args: argparse.Namespace) -> int:
    workers = [NullWorker().capabilities()]
    try:
        from .workers.deepseek_worker import DeepSeekWorker
        from .workers.opencode_worker import OpenCodeWorker

        workers.append(DeepSeekWorker().capabilities())
        workers.append(OpenCodeWorker().capabilities())
    except Exception as exc:  # noqa: BLE001 - status command should report adapter import failures.
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"workers": [worker.__dict__ for worker in workers]}, ensure_ascii=False, indent=2))
    return 0


def project_intake_cmd(args: argparse.Namespace) -> int:
    manifest = write_project_intake(
        ProjectIntakeRequest(
            project_root=args.project_root,
            output_dir=args.output_dir,
            contract_path=args.contract,
            max_files=max(1, args.max_files),
            max_symbols_per_file=max(1, args.max_symbols_per_file),
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "primary_language": (manifest.get("language_summary") or {}).get("primary_language"),
                "entry_files": len(manifest.get("entry_files") or []),
                "core_algorithm_files": len(manifest.get("core_algorithm_files") or []),
                "risk_flags": len(manifest.get("risk_flags") or []),
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def health_check_cmd(args: argparse.Namespace) -> int:
    manifest = run_health_check(
        HealthCheckRequest(
            contract_path=args.contract,
            output_dir=args.output_dir,
            project_root=args.project_root,
            repeats=max(1, args.repeats),
            max_instances=max(1, args.max_instances),
            max_seeds=max(1, args.max_seeds),
            allow_draft=bool(args.allow_draft),
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "quick_test": (manifest["quick_test"] or {}).get("status"),
                "stability_probe": (manifest.get("stability_probe") or {}).get("status"),
                "stable": (manifest.get("stability_probe") or {}).get("stable"),
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def intent_alignment_cmd(args: argparse.Namespace) -> int:
    manifest = write_intent_alignment(
        IntentAlignmentRequest(
            contract_path=args.contract,
            output_dir=args.output_dir,
            project_root=args.project_root,
            health_manifest_path=args.health_manifest,
            benchmark_source=args.benchmark_source,
            allow_draft=bool(args.allow_draft),
            require_health=not bool(args.no_require_health),
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "ready_for_optimization": manifest["ready_for_optimization"],
                "blockers": manifest["blockers"],
                "warnings": manifest["warnings"],
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ready" else 1


def run_standard_agent(args: argparse.Namespace) -> int:
    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    neighborhood_profiles = parse_neighborhood_profiles(
        args.local_search_neighborhood_profiles,
        fallback=args.local_search_neighborhood_profile,
    )
    run_profiles = build_local_search_run_profiles(args, neighborhood_profiles)
    runner = StandardFjspAgentRunner(
        docs=args.doc,
        instance_dir=args.instance_dir,
        pattern=args.pattern,
        output_dir=args.output_dir,
        best_known_csv=args.best_known_csv,
        max_instances=args.max_instances,
        max_rounds=args.max_rounds,
        seeds=seeds,
        timeout_seconds=args.timeout_seconds,
        max_workers=max(1, args.max_workers),
        solver=args.solver,
        portfolio_size=args.portfolio_size,
        local_search_restarts=args.local_search_restarts,
        local_search_initial_pool_size=args.local_search_initial_pool_size,
        local_search_iterations=args.local_search_iterations,
        local_search_neighbor_limit=args.local_search_neighbor_limit,
        local_search_time_limit_sec=args.local_search_time_limit_sec,
        local_search_neighborhood_profiles=neighborhood_profiles,
        local_search_run_profiles=run_profiles,
        awls_restarts=args.awls_restarts,
        awls_cycles_per_restart=args.awls_cycles_per_restart,
        awls_iterations=args.awls_iterations,
        awls_time_limit_sec=args.awls_time_limit_sec,
        awls_init=args.awls_init,
        awls_exact_select_top_k=args.awls_exact_select_top_k,
        awls_beta=args.awls_beta,
        awls_gamma=args.awls_gamma,
        awls_theta=args.awls_theta,
        awls_portfolio_lanes=args.awls_portfolio_lanes,
        strategy_candidates=args.strategy_candidates,
        profile_mode=args.profile_mode,
        deepseek_model=args.deepseek_model,
        project_root=args.project_root,
    )
    result = runner.run()
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


def run_demo(args: argparse.Namespace) -> int:
    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    neighborhood_profiles = parse_neighborhood_profiles(
        args.local_search_neighborhood_profiles,
        fallback=args.local_search_neighborhood_profile,
    )
    run_profiles = build_local_search_run_profiles(args, neighborhood_profiles)
    manifest = run_standard_demo(
        StandardDemoRequest(
            docs=args.doc,
            instance_dir=args.instance_dir,
            pattern=args.pattern,
            output_dir=args.output_dir,
            project_root=args.project_root,
            best_known_csv=args.best_known_csv,
            max_instances=args.max_instances,
            max_rounds=args.max_rounds,
            seeds=seeds or [0],
            timeout_seconds=args.timeout_seconds,
            max_workers=max(1, args.max_workers),
            solver=args.solver,
            portfolio_size=args.portfolio_size,
            local_search_restarts=args.local_search_restarts,
            local_search_initial_pool_size=args.local_search_initial_pool_size,
            local_search_iterations=args.local_search_iterations,
            local_search_neighbor_limit=args.local_search_neighbor_limit,
            local_search_time_limit_sec=args.local_search_time_limit_sec,
            local_search_neighborhood_profiles=neighborhood_profiles,
            local_search_run_profiles=run_profiles,
            awls_restarts=args.awls_restarts,
            awls_cycles_per_restart=args.awls_cycles_per_restart,
            awls_iterations=args.awls_iterations,
            awls_time_limit_sec=args.awls_time_limit_sec,
            awls_init=args.awls_init,
            awls_exact_select_top_k=args.awls_exact_select_top_k,
            awls_beta=args.awls_beta,
            awls_gamma=args.awls_gamma,
            awls_theta=args.awls_theta,
            awls_portfolio_lanes=args.awls_portfolio_lanes,
            strategy_candidates=args.strategy_candidates,
            profile_mode=args.profile_mode,
            deepseek_model=args.deepseek_model,
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
                "standard_agent_report": manifest["artifacts"]["standard_agent_report"],
                "hypothesis_graph": manifest["artifacts"]["hypothesis_graph"],
                "last_summary": manifest["agent_result"].get("last_summary"),
                "artifact_checks": manifest["artifact_checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def run_benchmark_suite_cmd(args: argparse.Namespace) -> int:
    manifest = run_benchmark_suite(
        BenchmarkSuiteRequest(
            config_path=args.config,
            output_dir=args.output_dir,
            project_root=args.project_root,
            max_suites=args.max_suites,
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "suite_count": manifest["suite_count"],
                "aggregate": manifest["aggregate"],
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def run_standard_worker_loop_cmd(args: argparse.Namespace) -> int:
    seeds = [int(item.strip()) for item in str(args.seeds).split(",") if item.strip()]
    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    manifest = run_standard_worker_loop(
        StandardWorkerLoopRequest(
            docs=args.doc,
            knowledge_cards=args.knowledge_card,
            instance_dir=args.instance_dir,
            pattern=args.pattern,
            output_dir=args.output_dir,
            project_root=args.project_root,
            worker=worker,
            best_known_csv=args.best_known_csv,
            previous_pipeline_memory=args.previous_memory,
            max_instances=args.max_instances,
            seeds=seeds or [0],
            timeout_seconds=args.timeout_seconds,
            max_workers=max(1, args.max_workers),
            solver=args.solver,
            portfolio_size=args.portfolio_size,
            local_search_restarts=args.local_search_restarts,
            local_search_initial_pool_size=args.local_search_initial_pool_size,
            local_search_iterations=args.local_search_iterations,
            local_search_neighbor_limit=args.local_search_neighbor_limit,
            local_search_time_limit_sec=args.local_search_time_limit_sec,
            local_search_neighborhood_profile=args.local_search_neighborhood_profile,
            awls_restarts=args.awls_restarts,
            awls_cycles_per_restart=args.awls_cycles_per_restart,
            awls_iterations=args.awls_iterations,
            awls_time_limit_sec=args.awls_time_limit_sec,
            awls_init=args.awls_init,
            awls_exact_select_top_k=args.awls_exact_select_top_k,
            awls_beta=args.awls_beta,
            awls_gamma=args.awls_gamma,
            awls_theta=args.awls_theta,
            awls_portfolio_lanes=args.awls_portfolio_lanes,
            iterations=args.iterations,
            max_steps=args.max_steps,
            max_runtime_seconds=args.max_runtime_seconds,
            apply_worker_changes=bool(args.apply_worker),
            experiment_id=args.experiment_id,
            hypothesis=args.hypothesis
            or "Improve the standard FJSP solver under the fixed evaluator. State the rule-level idea before editing code.",
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "baseline_key": manifest["baseline_key"],
                "final_key": manifest["final_key"],
                "improved": manifest["improved"],
                "round_count": manifest["round_count"],
                "promoted_rounds": manifest["promoted_rounds"],
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
                "loop_report": manifest["artifacts"]["loop_report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def run_standard_pipeline_cmd(args: argparse.Namespace) -> int:
    seeds = [int(item.strip()) for item in str(args.worker_seeds).split(",") if item.strip()]
    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    request = StandardPipelineRequest(
        suite_config=args.suite_config,
        output_dir=args.output_dir,
        project_root=args.project_root,
        worker=worker,
        worker_docs=args.worker_doc,
        worker_knowledge_cards=args.worker_knowledge_card,
        previous_pipeline_memory=args.previous_memory,
        worker_instance_dir=args.worker_instance_dir,
        run_project_intake=not bool(args.skip_project_intake),
        project_intake_max_files=max(1, args.project_intake_max_files),
        health_contract=args.health_contract,
        health_repeats=max(1, args.health_repeats),
        health_max_instances=max(1, args.health_max_instances),
        health_max_seeds=max(1, args.health_max_seeds),
        health_allow_draft=bool(args.health_allow_draft),
        benchmark_source=args.benchmark_source,
        worker_pattern=args.worker_pattern,
        worker_best_known_csv=args.worker_best_known_csv,
        max_suites=args.max_suites,
        worker_max_instances=args.worker_max_instances,
        worker_seeds=seeds or [0],
        worker_timeout_seconds=args.worker_timeout_seconds,
        worker_max_workers=max(1, args.worker_max_workers),
        worker_solver=args.worker_solver,
        worker_portfolio_size=args.worker_portfolio_size,
        worker_local_search_restarts=args.worker_local_search_restarts,
        worker_local_search_initial_pool_size=args.worker_local_search_initial_pool_size,
        worker_local_search_iterations=args.worker_local_search_iterations,
        worker_local_search_neighbor_limit=args.worker_local_search_neighbor_limit,
        worker_local_search_time_limit_sec=args.worker_local_search_time_limit_sec,
        worker_local_search_neighborhood_profile=args.worker_local_search_neighborhood_profile,
        worker_awls_restarts=args.worker_awls_restarts,
        worker_awls_cycles_per_restart=args.worker_awls_cycles_per_restart,
        worker_awls_iterations=args.worker_awls_iterations,
        worker_awls_time_limit_sec=args.worker_awls_time_limit_sec,
        worker_awls_init=args.worker_awls_init,
        worker_awls_exact_select_top_k=args.worker_awls_exact_select_top_k,
        worker_awls_beta=args.worker_awls_beta,
        worker_awls_gamma=args.worker_awls_gamma,
        worker_awls_theta=args.worker_awls_theta,
        worker_awls_portfolio_lanes=args.worker_awls_portfolio_lanes,
        worker_iterations=args.worker_iterations,
        worker_max_steps=args.worker_max_steps,
        worker_max_runtime_seconds=args.worker_max_runtime_seconds,
        worker_apply_changes=bool(args.worker_apply),
        worker_experiment_id=args.worker_experiment_id,
        worker_hypothesis=args.worker_hypothesis
        or "Improve the standard FJSP solver under the fixed evaluator. State the rule-level idea before editing code.",
        title=args.title,
    )
    if args.ablation == "memory-vs-fixed":
        manifest = run_standard_pipeline_ablation(
            StandardPipelineAblationRequest(
                base_request=request,
                rounds=max(2, args.loop_rounds),
                ablation_name=args.ablation,
            )
        )
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "ablation_name": manifest["ablation_name"],
                    "round_count": manifest["round_count"],
                    "comparison": manifest["comparison"],
                    "manifest": manifest["artifacts"]["manifest"],
                    "report": manifest["artifacts"]["report"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if manifest["status"] == "ok" else 1

    if max(1, args.loop_rounds) > 1:
        manifest = run_standard_pipeline_loop(
            StandardPipelineLoopRequest(
                base_request=request,
                rounds=args.loop_rounds,
                adapt_worker_hypothesis=not bool(args.no_adapt_worker_hypothesis),
                chain_previous_memory=True,
            )
        )
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "round_count": manifest["round_count"],
                    "final": manifest["final"],
                    "manifest": manifest["artifacts"]["manifest"],
                    "report": manifest["artifacts"]["report"],
                    "final_memory": manifest["artifacts"].get("final_memory"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if manifest["status"] == "ok" else 1

    manifest = run_standard_pipeline(request)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "stage_status": manifest["stage_status"],
                "project_intake": manifest["artifacts"].get("project_intake_report"),
                "intent_alignment": manifest["artifacts"].get("intent_alignment_report"),
                "benchmark_suite": manifest["artifacts"]["benchmark_suite_report"],
                "standard_worker_loop": manifest["artifacts"]["standard_worker_loop_report"],
                "evidence_index": manifest["artifacts"]["evidence_index_markdown"],
                "manifest": manifest["artifacts"]["manifest"],
                "report": manifest["artifacts"]["report"],
                "memory": manifest["artifacts"].get("standard_pipeline_memory_json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def build_evidence_index_cmd(args: argparse.Namespace) -> int:
    index = build_evidence_index(
        EvidenceIndexRequest(
            input_dirs=args.input_dir,
            output_dir=args.output_dir,
            title=args.title,
        )
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "entry_count": index["entry_count"],
                "summary": index["summary"],
                "json": index["artifacts"]["json"],
                "markdown": index["artifacts"]["markdown"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def serve_web_cmd(args: argparse.Namespace) -> int:
    run_web_server(host=args.host, port=args.port, output_root=args.output_root)
    return 0


def run_awls_benchmark_cmd(args: argparse.Namespace) -> int:
    manifest = run_awls_benchmark(
        AwlsBenchmarkRequest(
            instance_dir=args.instance_dir,
            pattern=args.pattern,
            output_dir=args.output_dir,
            best_known_csv=args.best_known_csv,
            max_instances=args.max_instances,
            include_families=parse_csv_list(args.include_families),
            instance_names=parse_instance_names(args.instance_name, args.instance_list),
            sample_count=args.sample_count,
            sample_seed=args.sample_seed,
            seeds=parse_seed_list(args.seeds),
            max_workers=max(1, args.max_workers),
            restarts=args.awls_restarts,
            cycles_per_restart=args.awls_cycles_per_restart,
            iterations=args.awls_iterations,
            time_limit_sec=args.awls_time_limit_sec,
            init_mode=args.awls_init,
            exact_select_top_k=args.awls_exact_select_top_k,
            beta=args.awls_beta,
            gamma=args.awls_gamma,
            theta=args.awls_theta,
            zi_policy=args.awls_zi_policy,
            portfolio_lanes=args.awls_portfolio_lanes,
            critical_block_exhaustive_pct=args.awls_critical_block_exhaustive_pct,
            same_machine_eval=args.same_machine_eval,
            time_policy=args.awls_time_policy,
            resume=args.resume,
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "aggregate": manifest["aggregate"],
                "summary": manifest["artifacts"]["summary"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def compare_awls_benchmarks_cmd(args: argparse.Namespace) -> int:
    manifest = compare_awls_benchmarks(
        AwlsCompareRequest(
            baseline_summary=args.baseline_summary,
            candidate_summary=args.candidate_summary,
            output_dir=args.output_dir,
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "aggregate": manifest["aggregate"],
                "summary": manifest["artifacts"]["summary"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def run_awls_zi_evolution_cmd(args: argparse.Namespace) -> int:
    manifest = run_awls_zi_evolution(
        AwlsZiEvolutionRequest(
            instance_dir=args.instance_dir,
            pattern=args.pattern,
            output_dir=args.output_dir,
            best_known_csv=args.best_known_csv,
            max_instances=args.max_instances,
            include_families=parse_csv_list(args.include_families),
            instance_names=parse_instance_names(args.instance_name, args.instance_list),
            sample_count=args.sample_count,
            sample_seed=args.sample_seed,
            rounds=max(1, args.rounds),
            candidates_per_round=max(1, args.candidates_per_round),
            deepseek_model=args.deepseek_model,
            seeds=parse_seed_list(args.seeds),
            max_workers=max(1, args.max_workers),
            restarts=args.awls_restarts,
            cycles_per_restart=args.awls_cycles_per_restart,
            iterations=args.awls_iterations,
            time_limit_sec=args.awls_time_limit_sec,
            init_mode=args.awls_init,
            exact_select_top_k=args.awls_exact_select_top_k,
            beta=args.awls_beta,
            gamma=args.awls_gamma,
            theta=args.awls_theta,
            portfolio_lanes=args.awls_portfolio_lanes,
            same_machine_eval=args.same_machine_eval,
            time_policy=args.awls_time_policy,
            baseline_summary=args.baseline_summary,
        )
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "best": manifest["best"],
                "summary": manifest["artifacts"]["summary"],
                "report": manifest["artifacts"]["report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["status"] == "ok" else 1


def parse_seed_list(value: str) -> list[int]:
    seeds = [int(item.strip()) for item in str(value).split(",") if item.strip()]
    return seeds or [0]


def parse_csv_list(value: str) -> list[str] | None:
    items = [item.strip() for item in str(value).split(",") if item.strip()]
    return items or None


def parse_instance_names(values: list[str], list_path: Path | None) -> list[str] | None:
    names: list[str] = []
    for value in values:
        names.extend(item.strip() for item in str(value).split(",") if item.strip())
    if list_path is not None:
        for line in list_path.read_text(encoding="utf-8-sig").splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                names.append(name)
    return names or None


def parse_neighborhood_profiles(value: str | None, *, fallback: str) -> list[str]:
    allowed = {"random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"}
    raw_items = [fallback] if not value else [item.strip() for item in value.split(",") if item.strip()]
    profiles: list[str] = []
    for item in raw_items:
        if item not in allowed:
            raise ValueError(f"unknown local-search neighborhood profile: {item}")
        if item not in profiles:
            profiles.append(item)
    return profiles or [fallback]


def build_local_search_run_profiles(args: argparse.Namespace, neighborhood_profiles: list[str]) -> list[dict[str, object]] | None:
    if not args.local_search_run_profiles:
        return None

    custom_by_neighborhood = {
        profile: {
            "name": f"current-{profile}",
            "portfolio_size": args.portfolio_size,
            "restarts": args.local_search_restarts,
            "initial_pool_size": args.local_search_initial_pool_size,
            "iterations": args.local_search_iterations,
            "neighbor_limit": args.local_search_neighbor_limit,
            "time_limit_sec": args.local_search_time_limit_sec,
            "neighborhood_profile": profile,
        }
        for profile in neighborhood_profiles
    }
    presets: dict[str, dict[str, object]] = {
        "balanced-random": {
            "name": "balanced-random",
            "portfolio_size": max(args.portfolio_size, 192),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 100),
            "neighbor_limit": max(args.local_search_neighbor_limit, 220),
            "time_limit_sec": max(args.local_search_time_limit_sec, 4.0),
            "neighborhood_profile": "random",
        },
        "balanced-combined": {
            "name": "balanced-combined",
            "portfolio_size": max(args.portfolio_size, 192),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 100),
            "neighbor_limit": max(args.local_search_neighbor_limit, 220),
            "time_limit_sec": max(args.local_search_time_limit_sec, 4.0),
            "neighborhood_profile": "combined",
        },
        "deep-combined": {
            "name": "deep-combined",
            "portfolio_size": max(args.portfolio_size, 256),
            "restarts": max(args.local_search_restarts, 3),
            "initial_pool_size": max(args.local_search_initial_pool_size, 2),
            "iterations": max(args.local_search_iterations, 180),
            "neighbor_limit": max(args.local_search_neighbor_limit, 320),
            "time_limit_sec": max(args.local_search_time_limit_sec, 8.0),
            "neighborhood_profile": "combined",
        },
        "balanced-hgtsa": {
            "name": "balanced-hgtsa",
            "portfolio_size": max(args.portfolio_size, 192),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 100),
            "neighbor_limit": max(args.local_search_neighbor_limit, 220),
            "time_limit_sec": max(args.local_search_time_limit_sec, 4.0),
            "neighborhood_profile": "hgtsa-lite",
        },
        "balanced-awls": {
            "name": "balanced-awls",
            "portfolio_size": max(args.portfolio_size, 224),
            "restarts": max(args.local_search_restarts, 2),
            "initial_pool_size": max(args.local_search_initial_pool_size, 1),
            "iterations": max(args.local_search_iterations, 120),
            "neighbor_limit": max(args.local_search_neighbor_limit, 240),
            "time_limit_sec": max(args.local_search_time_limit_sec, 5.0),
            "neighborhood_profile": "awls-hybrid",
        },
        "deep-hgtsa": {
            "name": "deep-hgtsa",
            "portfolio_size": max(args.portfolio_size, 256),
            "restarts": max(args.local_search_restarts, 3),
            "initial_pool_size": max(args.local_search_initial_pool_size, 2),
            "iterations": max(args.local_search_iterations, 180),
            "neighbor_limit": max(args.local_search_neighbor_limit, 320),
            "time_limit_sec": max(args.local_search_time_limit_sec, 8.0),
            "neighborhood_profile": "hybrid",
        },
    }
    for profile, payload in custom_by_neighborhood.items():
        presets[f"current-{profile}"] = payload
    if len(custom_by_neighborhood) == 1:
        presets["current"] = next(iter(custom_by_neighborhood.values()))

    requested = [item.strip() for item in args.local_search_run_profiles.split(",") if item.strip()]
    run_profiles: list[dict[str, object]] = []
    for name in requested:
        if name not in presets:
            raise ValueError(f"unknown local-search run profile: {name}")
        profile = dict(presets[name])
        if profile not in run_profiles:
            run_profiles.append(profile)
    return run_profiles


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-contract":
        return validate_contract(args)
    if args.command == "confirm-contract":
        return confirm_contract(args)
    if args.command == "build-context-packet":
        return build_context_packet_cmd(args)
    if args.command == "run-worker":
        return run_worker_cmd(args)
    if args.command == "run-worker-cycle":
        return run_worker_cycle_cmd(args)
    if args.command == "run-worker-loop":
        return run_worker_loop_cmd(args)
    if args.command == "draft-contract":
        return draft_contract(args)
    if args.command == "run":
        return run_contract(args)
    if args.command == "build-standard-contract":
        return build_standard_contract(args)
    if args.command == "project-intake":
        return project_intake_cmd(args)
    if args.command == "health-check":
        return health_check_cmd(args)
    if args.command == "intent-alignment":
        return intent_alignment_cmd(args)
    if args.command == "worker-status":
        return worker_status(args)
    if args.command == "run-standard-agent":
        return run_standard_agent(args)
    if args.command == "run-demo":
        return run_demo(args)
    if args.command == "run-benchmark-suite":
        return run_benchmark_suite_cmd(args)
    if args.command == "run-awls-benchmark":
        return run_awls_benchmark_cmd(args)
    if args.command == "compare-awls-benchmarks":
        return compare_awls_benchmarks_cmd(args)
    if args.command == "run-awls-zi-evolution":
        return run_awls_zi_evolution_cmd(args)
    if args.command == "serve-web":
        return serve_web_cmd(args)
    if args.command == "run-standard-worker-loop":
        return run_standard_worker_loop_cmd(args)
    if args.command == "run-standard-pipeline":
        return run_standard_pipeline_cmd(args)
    if args.command == "build-evidence-index":
        return build_evidence_index_cmd(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
