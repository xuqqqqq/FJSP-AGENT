"""命令行入口：把各层能力暴露为可脚本化命令。

本文件只做参数解析和依赖组装，不负责算法决策。真实业务逻辑分别位于
`context/`、`orchestration/`、`core/` 和 `agents/`；因此 CLI 与 Web 可以
共享同一套闭环，而不会形成两套评价口径。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.context.contract import (
    DraftContractRequest,
    draft_review_report_path,
    write_confirmed_contract,
    write_draft_contract,
)
from .deepseek_client import is_deepseek_configured
from harness_agent.core.evidence import EvidenceIndexRequest, build_evidence_index
from harness_agent.core.graph import GraphHarnessRunner
from harness_agent.core.health import HealthCheckRequest, run_health_check
from harness_agent.core.intent import IntentAlignmentRequest, write_intent_alignment
from harness_agent.orchestration.loop import DEFAULT_IN_ROUND_REPAIR_ATTEMPTS, run_worker_loop
from harness_agent.agents.main import DeepSeekMainAgent, EvidenceDrivenMainAgent
from harness_agent.core.models import TaskContract
from harness_agent.domains.families import write_problem_family_card
from harness_agent.context.intake import ProjectIntakeRequest, write_project_intake
from harness_agent.core.runner import HarnessRunner
from harness_agent.agents.semantic import DeepSeekAlgorithmSemanticReviewer
from harness_agent.slots.manifest import write_default_slot_manifest, write_selected_slot_manifest
from harness_agent.orchestration.standard import StandardWorkerLoopRequest, run_standard_worker_loop
from harness_agent.web.server import DEFAULT_OUTPUT_ROOT, run_web_server
from .worker import ExperimentSpec, NullWorker, WorkerResult
from harness_agent.orchestration.cycle import run_worker_cycle


DEFAULT_STANDARD_SEEDS = "0,1,2,3,4,5,6,7,8,9"


# ---------------------------------------------------------------------------
# 参数定义：这里只描述平台资源、路径和契约，不暴露具体邻域或求解算法参数。
# ---------------------------------------------------------------------------

def add_worker_options(parser: argparse.ArgumentParser, *, default_worker: str = "opencode") -> None:
    """为直接调用 Coding Agent 的命令注册统一参数。"""

    parser.add_argument("--worker", choices=["null", "deepseek", "opencode"], default=default_worker)
    parser.add_argument("--deepseek-model", default="deepseek-v4-pro")
    parser.add_argument("--opencode-model", help="可选的 OpenCode provider/model 覆盖")


def build_parser() -> argparse.ArgumentParser:
    """注册从准备、单次 Worker 到完整闭环的全部命令。

    命令大致分为四组：契约/上下文准备、Worker 调试、固定 Core 执行、
    Agent 自写 solver 闭环。每个 handler 只把 argparse 值转换成领域对象。
    """

    parser = argparse.ArgumentParser(description="FJSP Harness Agent CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-contract", help="校验任务契约")
    validate.add_argument("--contract", required=True, type=Path)
    validate.add_argument("--project-root", type=Path, default=Path.cwd())

    confirm = commands.add_parser("confirm-contract", help="确认人工复核后的契约")
    confirm.add_argument("--contract", required=True, type=Path)
    confirm.add_argument("--output", required=True, type=Path)
    confirm.add_argument("--confirmed-by", required=True)
    confirm.add_argument("--note", default="")

    draft = commands.add_parser("draft-contract", help="从需求/IO 文档生成待确认契约")
    draft.add_argument("--doc", action="append", type=Path, default=[])
    draft.add_argument("--instance", action="append", type=Path, default=[])
    draft.add_argument("--output", required=True, type=Path)
    draft.add_argument("--project-root", type=Path, default=Path.cwd())
    draft.add_argument("--task-id", default="draft_task")
    draft.add_argument("--problem-family")
    draft.add_argument("--objective", action="append", default=[])
    draft.add_argument("--solver-cmd")
    draft.add_argument("--evaluator-cmd")
    draft.add_argument("--quick-test")
    draft.add_argument("--rounds", type=int, default=1)
    draft.add_argument("--seeds", default="0")
    draft.add_argument("--timeout-seconds", type=int, default=300)
    draft.add_argument("--max-workers", type=int, default=1)
    draft.add_argument("--allowed-path", action="append", default=[])
    draft.add_argument("--forbidden-path", action="append", default=[".git", "outputs"])
    draft.add_argument("--resource", action="append", default=[])

    context = commands.add_parser("build-context-packet", help="构建有界 Coding Agent 上下文")
    context.add_argument("--contract", required=True, type=Path)
    context.add_argument("--output", required=True, type=Path)
    context.add_argument("--doc", action="append", type=Path, default=[])
    context.add_argument("--knowledge-card", action="append", type=Path, default=[])
    context.add_argument("--hypothesis", default="")
    context.add_argument("--previous-report", type=Path)
    context.add_argument("--previous-memory", type=Path)
    context.add_argument("--project-intake-manifest", type=Path)
    context.add_argument("--slot-manifest", type=Path)
    context.add_argument("--project-root", type=Path, default=Path.cwd())
    context.add_argument("--max-chars-per-source", type=int, default=12000)

    family = commands.add_parser("problem-family-card", help="生成问题族能力卡")
    family.add_argument("--problem-family", default="standard_fjsp")
    family.add_argument("--output", required=True, type=Path)

    slots = commands.add_parser("build-slot-manifest", help="生成可插拔代码槽契约")
    slots.add_argument("--problem-family", default="standard_fjsp")
    slots.add_argument("--output", required=True, type=Path)
    slots.add_argument("--confirmed", action="store_true")
    slots.add_argument("--selected-slot-id", action="append", default=[])

    run_worker = commands.add_parser("run-worker", help="让 Coding Agent 读取一个上下文包")
    add_worker_options(run_worker, default_worker="opencode")
    run_worker.add_argument("--context-packet", required=True, type=Path)
    run_worker.add_argument("--worktree", type=Path, default=Path.cwd())
    run_worker.add_argument("--output-dir", required=True, type=Path)
    run_worker.add_argument("--task-id", default="worker_task")
    run_worker.add_argument("--experiment-id", default="worker_experiment")
    run_worker.add_argument("--max-steps", type=int, default=8)
    run_worker.add_argument("--max-runtime-seconds", type=int, default=300)
    run_worker.add_argument("--apply", action="store_true")

    cycle = commands.add_parser("run-worker-cycle", help="运行一次候选生成与 Core 评测")
    add_worker_options(cycle, default_worker="opencode")
    cycle.add_argument("--contract", required=True, type=Path)
    cycle.add_argument("--context-packet", required=True, type=Path)
    cycle.add_argument("--output-dir", required=True, type=Path)
    cycle.add_argument("--project-root", type=Path, default=Path.cwd())
    cycle.add_argument("--experiment-id", default="worker_cycle")
    cycle.add_argument("--max-steps", type=int, default=8)
    cycle.add_argument("--max-runtime-seconds", type=int, default=300)
    cycle.add_argument("--apply-worker", action="store_true")
    cycle.add_argument("--allow-draft", action="store_true")

    loop = commands.add_parser("run-worker-loop", help="运行 promotion/rollback 多轮闭环")
    add_worker_options(loop, default_worker="opencode")
    loop.add_argument("--contract", required=True, type=Path)
    loop.add_argument("--context-packet", required=True, type=Path)
    loop.add_argument("--output-dir", required=True, type=Path)
    loop.add_argument("--project-root", type=Path, default=Path.cwd())
    loop.add_argument("--experiment-id", default="worker_loop")
    loop.add_argument("--iterations", type=int, default=3)
    loop.add_argument("--max-steps", type=int, default=8)
    loop.add_argument("--max-runtime-seconds", type=int, default=300)
    loop.add_argument("--in-round-repair-attempts", type=int, default=DEFAULT_IN_ROUND_REPAIR_ATTEMPTS)
    loop.add_argument("--promotion-repeats", type=int, default=1)
    loop.add_argument("--apply-worker", action="store_true")
    loop.add_argument("--allow-draft", action="store_true")

    run = commands.add_parser("run", help="按已确认契约运行固定 Core")
    run.add_argument("--contract", required=True, type=Path)
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--runner", choices=["langgraph", "linear"], default="langgraph")
    run.add_argument("--allow-draft", action="store_true")

    intake = commands.add_parser("project-intake", help="扫描待优化项目")
    intake.add_argument("--project-root", type=Path, default=Path.cwd())
    intake.add_argument("--output-dir", required=True, type=Path)
    intake.add_argument("--contract", type=Path)
    intake.add_argument("--max-files", type=int, default=200)
    intake.add_argument("--max-symbols-per-file", type=int, default=20)

    health = commands.add_parser("health-check", help="运行契约快速测试和稳定性探针")
    health.add_argument("--contract", required=True, type=Path)
    health.add_argument("--output-dir", required=True, type=Path)
    health.add_argument("--project-root", type=Path, default=Path.cwd())
    health.add_argument("--repeats", type=int, default=2)
    health.add_argument("--max-instances", type=int, default=1)
    health.add_argument("--max-seeds", type=int, default=1)
    health.add_argument("--allow-draft", action="store_true")

    intent = commands.add_parser("intent-alignment", help="生成可复核的优化目标摘要")
    intent.add_argument("--contract", required=True, type=Path)
    intent.add_argument("--output-dir", required=True, type=Path)
    intent.add_argument("--project-root", type=Path, default=Path.cwd())
    intent.add_argument("--health-manifest", type=Path)
    intent.add_argument("--benchmark-source", default="user_provided")
    intent.add_argument("--allow-draft", action="store_true")
    intent.add_argument("--no-require-health", action="store_true")

    commands.add_parser("worker-status", help="查看 Coding Agent 后端状态")

    standard = commands.add_parser("run-standard-worker-loop", help="运行 Agent 自写 FJSP solver 闭环")
    add_worker_options(standard, default_worker="opencode")
    standard.add_argument("--doc", action="append", type=Path, default=[])
    standard.add_argument("--knowledge-card", action="append", type=Path, default=[])
    standard.add_argument("--slot-manifest", type=Path)
    standard.add_argument("--instance-dir", required=True, type=Path)
    standard.add_argument("--pattern", default="*.txt")
    standard.add_argument("--best-known-csv", type=Path)
    standard.add_argument("--previous-memory", type=Path)
    standard.add_argument("--output-dir", required=True, type=Path)
    standard.add_argument("--project-root", type=Path, default=Path.cwd())
    standard.add_argument("--max-instances", type=int)
    standard.add_argument("--seeds", default=DEFAULT_STANDARD_SEEDS)
    standard.add_argument("--timeout-seconds", type=int, default=60)
    standard.add_argument("--max-workers", type=int, default=1)
    standard.add_argument("--iterations", type=int, default=1)
    standard.add_argument("--max-steps", type=int, default=4)
    standard.add_argument("--max-runtime-seconds", type=int, default=120)
    standard.add_argument("--in-round-repair-attempts", type=int, default=DEFAULT_IN_ROUND_REPAIR_ATTEMPTS)
    standard.add_argument("--promotion-repeats", type=int, default=1)
    standard.add_argument("--apply-worker", action="store_true")
    standard.add_argument("--agent-generated-solver-path", default="examples/agent_generated_fjsp_solver.py")
    standard.add_argument("--experiment-id", default="standard_worker_loop")
    standard.add_argument("--hypothesis", default="")

    evidence = commands.add_parser("build-evidence-index", help="汇总闭环运行证据")
    evidence.add_argument("--input-dir", action="append", required=True, type=Path)
    evidence.add_argument("--output-dir", required=True, type=Path)
    evidence.add_argument("--title", default="Loop Engineering Evidence Index")

    web = commands.add_parser("serve-web", help="启动本地 Web 平台")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=7860)
    web.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


# ---------------------------------------------------------------------------
# 公共参数转换与依赖装配
# ---------------------------------------------------------------------------

def print_json(payload: dict[str, Any]) -> None:
    """统一 CLI 的 UTF-8 友好 JSON 输出，便于脚本和 Web 外部工具读取。"""

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_seed_list(value: str) -> list[int]:
    """把逗号分隔 seed 转为列表；空输入仍保证至少有 seed=0。"""

    seeds = [int(item.strip()) for item in str(value).split(",") if item.strip()]
    return seeds or [0]


def load_runnable_contract(args: argparse.Namespace) -> TaskContract | None:
    """加载并执行运行前门禁；返回 None 表示命令不应进入 Core。"""

    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    if errors:
        print_json({"status": "invalid_contract", "errors": errors})
        return None
    if contract.requires_human_confirmation and not args.allow_draft:
        print_json({"status": "contract_requires_human_confirmation", "review_status": contract.review_status})
        return None
    return contract


def make_worker(name: str, *, deepseek_model: str, opencode_model: str | None = None):
    """创建通用 Coding Agent 适配器，不根据问题类型注入算法实现。"""

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
    """把 Worker 过程结果序列化；该结果本身不代表候选已被 Core 接受。"""

    return {
        "status": result.status,
        "changed_files": result.changed_files,
        "summary": result.summary,
        "raw_log_path": result.raw_log_path,
        "artifacts": result.artifacts or {},
    }


# ---------------------------------------------------------------------------
# Handler：以下函数保持“解析参数 -> 调用一个业务入口 -> 输出摘要”的薄结构。
# ---------------------------------------------------------------------------

def validate_contract(args: argparse.Namespace) -> int:
    contract = TaskContract.load(args.contract)
    errors = contract.validate(args.project_root)
    print_json(
        {
            "task_id": contract.task_id,
            "problem_family": contract.problem_family,
            "instances": len(contract.instances),
            "review_status": contract.review_status,
            "errors": errors,
        }
    )
    return 1 if errors else 0


def draft_contract(args: argparse.Namespace) -> int:
    output = write_draft_contract(
        DraftContractRequest(
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
            seeds=parse_seed_list(args.seeds),
            timeout_seconds=args.timeout_seconds,
            max_workers=max(1, args.max_workers),
            allowed_paths=args.allowed_path,
            forbidden_paths=args.forbidden_path,
            resources=args.resource,
        )
    )
    contract = TaskContract.load(output)
    print_json(
        {
            "status": "draft_created",
            "output": str(output.resolve()),
            "review_report": str(draft_review_report_path(output).resolve()),
            "validation_errors": contract.validate(args.project_root),
        }
    )
    return 0


def confirm_contract(args: argparse.Namespace) -> int:
    output = write_confirmed_contract(args.contract, args.output, confirmed_by=args.confirmed_by, note=args.note)
    print_json({"status": "confirmed", "output": str(output.resolve())})
    return 0


def build_context_packet_cmd(args: argparse.Namespace) -> int:
    output = write_context_packet(
        ContextPacketRequest(
            contract_path=args.contract,
            output_path=args.output,
            docs=args.doc,
            knowledge_cards=args.knowledge_card,
            project_root=args.project_root,
            hypothesis=args.hypothesis,
            previous_report=args.previous_report,
            previous_pipeline_memory=args.previous_memory,
            project_intake_manifest=args.project_intake_manifest,
            slot_manifest=args.slot_manifest,
            max_chars_per_source=max(1000, args.max_chars_per_source),
        )
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    print_json({"status": "context_packet_created", "output": str(output.resolve()), "packet_hash": payload["packet_hash"]})
    return 0


def problem_family_card_cmd(args: argparse.Namespace) -> int:
    output = write_problem_family_card(family_id=args.problem_family, output=args.output)
    print_json({"status": "problem_family_card_created", "output": str(output.resolve())})
    return 0


def build_slot_manifest_cmd(args: argparse.Namespace) -> int:
    output = (
        write_selected_slot_manifest(args.problem_family, args.output, selected_slot_ids=args.selected_slot_id)
        if args.selected_slot_id
        else write_default_slot_manifest(args.problem_family, args.output, confirmed=bool(args.confirmed))
    )
    print_json({"status": "slot_manifest_created", "output": str(output.resolve())})
    return 0


def run_worker_cmd(args: argparse.Namespace) -> int:
    """只调用一次 Coding Worker，适合检查 prompt、provider 和代码应用。"""

    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = worker.run_experiment(
        ExperimentSpec(
            task_id=args.task_id,
            experiment_id=args.experiment_id,
            context_packet_path=str(args.context_packet),
            worktree_path=str(args.worktree),
            max_steps=max(1, args.max_steps),
            max_runtime_seconds=max(1, args.max_runtime_seconds),
            output_dir=str(args.output_dir),
            apply_changes=bool(args.apply),
        )
    )
    payload = worker_result_payload(result)
    (args.output_dir / "worker_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print_json(payload)
    return 1 if result.status in {"failed", "unavailable"} else 0


def run_worker_cycle_cmd(args: argparse.Namespace) -> int:
    """执行一次隔离候选周期：Worker、JA、smoke 和固定 evaluator。"""

    contract = load_runnable_contract(args)
    if contract is None:
        return 1
    result = run_worker_cycle(
        contract=contract,
        project_root=args.project_root,
        output_dir=args.output_dir,
        context_packet_path=args.context_packet,
        worker=make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model),
        experiment_id=args.experiment_id,
        max_steps=max(1, args.max_steps),
        max_runtime_seconds=max(1, args.max_runtime_seconds),
        apply_worker_changes=bool(args.apply_worker),
    )
    print_json({"status": "ok", "worker_status": result.worker_result.status, "best_metrics": result.summary.best_metrics})
    return 0


def run_worker_loop_cmd(args: argparse.Namespace) -> int:
    """在已有契约和 Context Packet 上运行通用多轮闭环。"""

    contract = load_runnable_contract(args)
    if contract is None:
        return 1
    result = run_worker_loop(
        contract=contract,
        project_root=args.project_root,
        output_dir=args.output_dir,
        context_packet_path=args.context_packet,
        worker=make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model),
        experiment_id=args.experiment_id,
        iterations=max(0, args.iterations),
        max_steps=max(1, args.max_steps),
        max_runtime_seconds=max(1, args.max_runtime_seconds),
        in_round_repair_attempts=max(0, args.in_round_repair_attempts),
        apply_worker_changes=bool(args.apply_worker),
        promotion_repeats=max(1, args.promotion_repeats),
    )
    print_json({"status": "ok", "baseline_key": list(result.baseline_key), "final_key": list(result.final_key)})
    return 0


def run_contract(args: argparse.Namespace) -> int:
    contract = load_runnable_contract(args)
    if contract is None:
        return 1
    runner_cls = GraphHarnessRunner if args.runner == "langgraph" else HarnessRunner
    runner = runner_cls(contract=contract, project_root=args.project_root, output_dir=args.output_dir)
    try:
        summary = runner.run()
    finally:
        runner.close()
    print_json({"status": "ok", "total": summary.total, "valid": summary.valid, "best_metrics": summary.best_metrics})
    return 0


def worker_status(args: argparse.Namespace) -> int:  # noqa: ARG001 - argparse handler signature
    workers = [NullWorker().capabilities()]
    try:
        from .workers.deepseek_worker import DeepSeekWorker
        from .workers.opencode_worker import OpenCodeWorker

        workers.extend([DeepSeekWorker().capabilities(), OpenCodeWorker().capabilities()])
    except Exception as exc:  # noqa: BLE001 - 状态命令需要把适配器导入失败显示出来。
        print_json({"status": "error", "error": str(exc)})
        return 1
    print_json({"workers": [worker.__dict__ for worker in workers]})
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
    print_json({"status": manifest["status"], "artifacts": manifest["artifacts"]})
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
    print_json({"status": manifest["status"], "artifacts": manifest["artifacts"]})
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
    print_json({"status": manifest["status"], "ready_for_optimization": manifest["ready_for_optimization"]})
    return 0 if manifest["status"] == "ready" else 1


def run_standard_worker_loop_cmd(args: argparse.Namespace) -> int:
    """运行当前 Web 同款的“文档驱动、Agent 自写 FJSP solver”闭环。"""

    worker = make_worker(args.worker, deepseek_model=args.deepseek_model, opencode_model=args.opencode_model)
    main_agent = (
        DeepSeekMainAgent(model=args.deepseek_model) if is_deepseek_configured() else EvidenceDrivenMainAgent()
    )
    semantic_reviewer = (
        DeepSeekAlgorithmSemanticReviewer(model=args.deepseek_model) if is_deepseek_configured() else None
    )
    manifest = run_standard_worker_loop(
        StandardWorkerLoopRequest(
            docs=args.doc,
            knowledge_cards=args.knowledge_card,
            instance_dir=args.instance_dir,
            pattern=args.pattern,
            output_dir=args.output_dir,
            project_root=args.project_root,
            worker=worker,
            main_agent=main_agent,
            semantic_reviewer=semantic_reviewer,
            best_known_csv=args.best_known_csv,
            slot_manifest=args.slot_manifest,
            previous_pipeline_memory=args.previous_memory,
            max_instances=args.max_instances,
            seeds=parse_seed_list(args.seeds),
            timeout_seconds=args.timeout_seconds,
            max_workers=max(1, args.max_workers),
            iterations=args.iterations,
            max_steps=args.max_steps,
            max_runtime_seconds=args.max_runtime_seconds,
            in_round_repair_attempts=max(0, args.in_round_repair_attempts),
            apply_worker_changes=bool(args.apply_worker),
            promotion_repeats=max(1, args.promotion_repeats),
            agent_generated_solver_path=args.agent_generated_solver_path,
            experiment_id=args.experiment_id,
            hypothesis=args.hypothesis
            or "根据需求、IO 和检索知识生成求解器；固定 Core 是唯一验收依据。",
        )
    )
    print_json(
        {
            "status": manifest["status"],
            "baseline_key": manifest["baseline_key"],
            "final_key": manifest["final_key"],
            "promoted_rounds": manifest["promoted_rounds"],
            "artifacts": manifest["artifacts"],
        }
    )
    return 0 if manifest["status"] == "ok" else 1


def build_evidence_index_cmd(args: argparse.Namespace) -> int:
    index = build_evidence_index(
        EvidenceIndexRequest(input_dirs=args.input_dir, output_dir=args.output_dir, title=args.title)
    )
    print_json({"status": "ok", "entry_count": index["entry_count"], "artifacts": index["artifacts"]})
    return 0


def serve_web_cmd(args: argparse.Namespace) -> int:
    run_web_server(host=args.host, port=args.port, output_root=args.output_root)
    return 0


# 命令名到薄 handler 的唯一分发表，避免在 main 中堆积业务分支。
HANDLERS = {
    "validate-contract": validate_contract,
    "confirm-contract": confirm_contract,
    "draft-contract": draft_contract,
    "build-context-packet": build_context_packet_cmd,
    "problem-family-card": problem_family_card_cmd,
    "build-slot-manifest": build_slot_manifest_cmd,
    "run-worker": run_worker_cmd,
    "run-worker-cycle": run_worker_cycle_cmd,
    "run-worker-loop": run_worker_loop_cmd,
    "run": run_contract,
    "project-intake": project_intake_cmd,
    "health-check": health_check_cmd,
    "intent-alignment": intent_alignment_cmd,
    "worker-status": worker_status,
    "run-standard-worker-loop": run_standard_worker_loop_cmd,
    "build-evidence-index": build_evidence_index_cmd,
    "serve-web": serve_web_cmd,
}


def main(argv: list[str] | None = None) -> int:
    """CLI 进程入口；可预期的输入/路径错误转为结构化错误而非堆栈。"""

    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except (ValueError, FileNotFoundError) as exc:
        print_json({"status": "error", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
