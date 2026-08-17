"""FJSP 文档任务的薄入口：组装通用闭环，不选择具体算法。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from harness_agent.context.packet import ContextPacketRequest, write_context_packet
from harness_agent.core.cancellation import CancellationToken
from harness_agent.agents.main import DirectionPlanningAgent
from harness_agent.orchestration.loop import (
    DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
    WorkerLoopResult,
    candidate_incumbent_payload,
    compact_promotion_check,
    compact_proposal_audit,
    load_worker_loop_result,
    round_record_payload,
    run_worker_loop,
    summary_payload,
)
from harness_agent.orchestration.loop import normalize_baseline_source
from harness_agent.core.models import TaskContract
from harness_agent.domains.pack import get_domain_pack
from harness_agent.domains.distributed_fjsp import looks_like_distributed_fjsp, parse_distributed_fjsp
from harness_agent.domains.io import parse_standard_fjsp
from harness_agent.agents.semantic import AlgorithmSemanticReviewer
from harness_agent.worker import CodingWorker


@dataclass(frozen=True)
class StandardWorkerLoopRequest:
    """由文档驱动的 FJSP 求解器生成与演进请求。

    这里故意不暴露任何具体算法参数。求解方法来自知识库和 Skill，并由
    Coding Agent 写入独立 solver；平台只负责组织上下文和调用固定 Core。
    """

    docs: list[Path]
    instance_dir: Path
    pattern: str
    output_dir: Path
    project_root: Path
    worker: CodingWorker
    instance_paths: list[Path] | None = None
    main_agent: DirectionPlanningAgent | None = None
    semantic_reviewer: AlgorithmSemanticReviewer | None = None
    best_known_csv: Path | None = None
    knowledge_cards: list[Path] | None = None
    slot_manifest: Path | None = None
    project_intake_manifest: Path | None = None
    previous_pipeline_memory: Path | None = None
    resume_loop_result: Path | None = None
    max_instances: int | None = None
    seeds: list[int] | None = None
    timeout_seconds: int = 60
    max_workers: int = 1
    iterations: int = 1
    max_steps: int = 4
    max_runtime_seconds: int = 120
    apply_worker_changes: bool = False
    promotion_repeats: int = 1
    in_round_repair_attempts: int = DEFAULT_IN_ROUND_REPAIR_ATTEMPTS
    max_competing_workers: int = 4
    round_intervention: Callable[[int, Any, dict[str, Any]], str | None] | None = None
    cancellation: CancellationToken | None = None
    agent_generated_solver_path: str = "examples/agent_generated_fjsp_solver.py"
    provided_project_root: Path | None = None
    provided_solver_command: str | None = None
    provided_target_file: str | None = None
    provided_project_read_paths: list[str] | None = None
    experiment_id: str = "standard_worker_loop"
    hypothesis: str = (
        "Improve the standard FJSP solver under the fixed evaluator. "
        "State the rule-level idea before editing code."
    )


def run_standard_worker_loop(request: StandardWorkerLoopRequest) -> dict[str, Any]:
    """构建任务契约和上下文，并运行“生成、审查、评测、晋升”闭环。"""

    # 1. 将 Web/CLI 参数固化为本次运行唯一的 Task Contract。后续所有
    # Worker、Core 和报告都引用这份文件，避免调用过程中口径漂移。
    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    execution_project_root = request.project_root.resolve()
    if request.provided_project_root is not None:
        execution_project_root = prepare_provided_project_source(
            uploaded_root=request.provided_project_root.resolve(),
            trusted_project_root=request.project_root.resolve(),
            output_path=output_dir / "provided_project_source",
        )
        request = replace(
            request,
            provided_project_read_paths=provided_project_read_paths(
                execution_project_root,
                target_file=request.provided_target_file,
            ),
        )
    contract_path = output_dir / "standard_worker_contract.json"
    context_path = output_dir / "context_packet.json"
    contract_payload = build_standard_worker_contract_payload(request)
    contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contract = TaskContract.load(contract_path)
    errors = contract.validate(execution_project_root)
    if errors:
        raise ValueError(f"generated standard worker contract is invalid: {errors}")

    # 2. 在首次调用 Agent 前构建稳定 Context Packet：任务、文档、算例
    # 诊断、知识选择和可选历史经验在这里形成同一份输入。
    write_context_packet(
        ContextPacketRequest(
            contract_path=contract_path,
            output_path=context_path,
            docs=request.docs,
            knowledge_cards=request.knowledge_cards or [],
            project_root=request.project_root,
            slot_manifest=request.slot_manifest,
            project_intake_manifest=request.project_intake_manifest,
            previous_pipeline_memory=request.previous_pipeline_memory,
            hypothesis=request.hypothesis,
        )
    )
    # 3. 无上传项目时由 Agent 从零生成 baseline；有上传项目时先原样评测，
    # 后续候选始终从 Core 已晋升的 incumbent 继续演进。
    resume_result = (
        load_worker_loop_result(request.resume_loop_result)
        if request.resume_loop_result is not None
        else None
    )
    loop_result = run_worker_loop(
        contract=contract,
        project_root=execution_project_root,
        output_dir=output_dir / "worker_loop",
        context_packet_path=context_path,
        worker=request.worker,
        main_agent=request.main_agent,
        semantic_reviewer=request.semantic_reviewer,
        experiment_id=request.experiment_id,
        iterations=max(0, request.iterations),
        max_steps=max(1, request.max_steps),
        max_runtime_seconds=max(1, request.max_runtime_seconds),
        apply_worker_changes=bool(request.apply_worker_changes),
        promotion_repeats=max(1, request.promotion_repeats),
        baseline_source="provided_project" if request.provided_project_root is not None else "agent_generated",
        worker_input_root=request.project_root,
        in_round_repair_attempts=max(0, request.in_round_repair_attempts),
        max_competing_workers=max(1, min(4, request.max_competing_workers)),
        round_intervention=request.round_intervention,
        cancellation=request.cancellation,
        resume_from=resume_result,
    )
    # 4. 闭环结束后只做报告汇总，不重新解释或改写 Core 的 promotion 结论。
    manifest = standard_worker_manifest(
        request=request,
        contract_path=contract_path,
        context_path=context_path,
        loop_result=loop_result,
        output_dir=output_dir,
    )
    manifest_path = output_dir / "standard_worker_loop_manifest.json"
    report_path = output_dir / "standard_worker_loop_report.md"
    manifest["artifacts"] = {
        "manifest": str(manifest_path.resolve()),
        "report": str(report_path.resolve()),
        "contract": str(contract_path.resolve()),
        "context_packet": str(context_path.resolve()),
        "loop_report": str((output_dir / "worker_loop" / "loop_report.md").resolve()),
        "loop_result": str((output_dir / "worker_loop" / "loop_result.json").resolve()),
        "hypothesis_graph": str((output_dir / "worker_loop" / "hypothesis_graph.json").resolve()),
        "hypothesis_graph_report": str((output_dir / "worker_loop" / "hypothesis_graph.md").resolve()),
        "experience_memory": str((output_dir / "worker_loop" / "experience_memory.json").resolve()),
        "experience_memory_report": str((output_dir / "worker_loop" / "experience_memory.md").resolve()),
        "skill_usage_records": str((output_dir / "worker_loop" / "skill_usage_records.json").resolve()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_standard_worker_report(manifest), encoding="utf-8")
    return manifest


def build_standard_worker_contract_payload(request: StandardWorkerLoopRequest) -> dict[str, Any]:
    """把文档式 FJSP 请求转换为固定 Core 可执行的契约 JSON。

    此处根据实际解析结果固定 CLI/evaluator 和目标，不固定任何构造规则、
    邻域或搜索算法；这些方法内容只能来自知识层并由 Coding Agent 写出。
    """

    instance_dir = resolve_input_path(request.project_root, request.instance_dir)
    paths = (
        sorted(resolve_input_path(request.project_root, path) for path in request.instance_paths)
        if request.instance_paths
        else sorted(instance_dir.glob(request.pattern))
    )
    if request.max_instances is not None:
        paths = paths[: request.max_instances]
    if not paths:
        raise FileNotFoundError(f"no standard FJSP instances matched {instance_dir / request.pattern}")

    resources: dict[str, str] = {}
    provided = request.provided_project_root is not None
    solver_path = str(
        request.provided_target_file
        if provided
        else request.agent_generated_solver_path or "examples/agent_generated_fjsp_solver.py"
    ).replace("\\", "/")
    solver = standard_solver_command(request)
    quick_test = f"python -m py_compile {solver_path}"
    problem_family, evaluator_path, objectives = fixed_problem_contract(paths)
    evaluator = f"python {evaluator_path} --instance {{instance}} --solution {{solution}} --metrics {{metrics}}"
    if request.best_known_csv:
        best_known_csv = resolve_input_path(request.project_root, request.best_known_csv)
        resources["best_known_csv"] = str(best_known_csv)
        if problem_family != "fjsp_distributed_transfer":
            evaluator += " --best-known-csv {best_known_csv}"

    return {
        "task_id": request.experiment_id,
        "problem_family": problem_family,
        "description": "由需求和 IO 文档驱动的 FJSP Coding Agent 闭环任务。",
        "instances": [{"id": path.stem, "path": str(path)} for path in paths],
        "objectives": objectives,
        "commands": {
            "solver": solver,
            "evaluator": evaluator,
            "quick_test": quick_test,
        },
        "budget": {
            "rounds": 1,
            "seeds": request.seeds or [0],
            "timeout_seconds": max(1, request.timeout_seconds),
            "max_workers": max(1, request.max_workers),
        },
        "paths": {
            "allowed_paths": ["."] if provided else ["examples"],
            # evaluator/parser 属于固定 Core。即使 solver 与 evaluator 同在 examples，
            # Coding Agent 也不得通过改评测规则来制造“提升”。
            "forbidden_paths": [
                ".git",
                "outputs",
                evaluator_path,
                "harness_agent",
                ".algoforge_worker_inputs",
                ".algoforge_worker_runtime",
            ],
        },
        "resources": resources,
        "review": {
            "status": "confirmed",
            "note": (
                "先原样评测用户提供项目，再由 Coding Agent 只修改指定主文件；固定 evaluator 是唯一验收依据。"
                if provided
                else "Solver 必须由 Coding Agent 生成；固定 evaluator 是唯一验收依据。"
            ),
            "baseline_source": "provided_project" if provided else "agent_generated",
            "agent_generated_solver_path": request.agent_generated_solver_path,
            "worker_target_file": solver_path,
            "provided_project_read_paths": request.provided_project_read_paths or [],
        },
    }


def standard_solver_command(request: StandardWorkerLoopRequest) -> str:
    """返回 Agent 生成 solver 的唯一运行命令。"""

    if request.provided_project_root is not None:
        command = str(request.provided_solver_command or "").strip()
        if not command:
            raise ValueError("provided project requires provided_solver_command")
        return command

    solver_path = str(request.agent_generated_solver_path or "examples/agent_generated_fjsp_solver.py").replace(
        "\\", "/"
    )
    return (
        f"python {solver_path} --input {{instance}} --output {{solution}} --seed {{seed}} "
        "--time-limit-sec {solver_time_limit_seconds}"
    )


PROVIDED_PROJECT_QUARANTINE_ROOTS = frozenset(
    {
        ".codex",
        ".git",
        ".opencode",
        "domain_packs",
        "instances",
        "knowledge",
        "outputs",
        "solutions",
        "tests",
        "trusted",
    }
)


def fixed_problem_contract(paths: list[Path]) -> tuple[str, str, list[dict[str, Any]]]:
    """Select the evaluator and objectives from parsed instances, never from Web input."""

    identities: set[str] = set()
    for path in paths:
        if looks_like_distributed_fjsp(path):
            parse_distributed_fjsp(path)
            identities.add("fjsp_distributed_transfer")
        else:
            identities.add(parse_standard_fjsp(path).variant)
    if len(identities) != 1:
        raise ValueError(f"all task instances must share one parsed variant, got {sorted(identities)}")
    identity = next(iter(identities))
    evaluator_by_variant = {
        "standard_fjsp": "examples/standard_fjsp_evaluator.py",
        "fjsp_sdst": "examples/standard_fjsp_evaluator.py",
        "fjsp_min_time_lag": "examples/standard_fjsp_evaluator.py",
        "fjsp_max_time_lag": "examples/standard_fjsp_evaluator.py",
        "fjsp_alternative_path": "examples/fjsp_alternative_path_evaluator.py",
        "fjsp_release_time": "examples/fjsp_release_time_evaluator.py",
        "fjsp_machine_availability": "examples/fjsp_machine_availability_evaluator.py",
        "fjsp_priority": "examples/fjsp_priority_evaluator.py",
        "fjsp_reentrant": "examples/fjsp_reentrant_evaluator.py",
        "fjsp_jpc_tst": "examples/fjsp_jpc_tst_evaluator.py",
        "fjsp_pbpm": "examples/fjsp_pbpm_evaluator.py",
        "fjsp_distributed_transfer": "examples/fjsp_distributed_transfer_evaluator.py",
    }
    if identity not in evaluator_by_variant:
        raise ValueError(f"no fixed evaluator is registered for parsed variant {identity!r}")
    objectives = [
        {"name": "makespan", "direction": "minimize", "priority": 1, "invalid_if_missing": True}
    ]
    problem_family = "FJSP"
    if identity == "fjsp_distributed_transfer":
        problem_family = identity
        objectives.extend(
            [
                {
                    "name": "max_factory_workload",
                    "direction": "minimize",
                    "priority": 2,
                    "invalid_if_missing": True,
                },
                {
                    "name": "total_energy_consumption",
                    "direction": "minimize",
                    "priority": 3,
                    "invalid_if_missing": True,
                },
            ]
        )
    elif identity == "fjsp_priority":
        objectives.append(
            {
                "name": "priority_completion_time",
                "direction": "minimize",
                "priority": 2,
                "invalid_if_missing": True,
            }
        )
    return problem_family, evaluator_by_variant[identity], objectives


PROVIDED_PROJECT_QUARANTINE_FILES = frozenset(
    {
        "evaluate.py",
        "evaluator.py",
        "standard_fjsp_evaluator.py",
        "fjsp_release_time_evaluator.py",
        "fjsp_machine_availability_evaluator.py",
        "fjsp_priority_evaluator.py",
        "fjsp_reentrant_evaluator.py",
        "fjsp_jpc_tst_evaluator.py",
        "fjsp_pbpm_evaluator.py",
        "fjsp_alternative_path_evaluator.py",
        "fjsp_distributed_transfer_evaluator.py",
    }
)
PROVIDED_PROJECT_READ_SUFFIXES = frozenset(
    {".cfg", ".ini", ".json", ".md", ".py", ".toml", ".yaml", ".yml"}
)


def prepare_provided_project_source(
    *,
    uploaded_root: Path,
    trusted_project_root: Path,
    output_path: Path,
) -> Path:
    """Compose an executable baseline without trusting archive-local Core assets."""

    if not uploaded_root.is_dir():
        raise ValueError(f"provided project root does not exist: {uploaded_root}")
    if output_path.exists():
        shutil.rmtree(output_path)

    source_root = uploaded_root.resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory).resolve()
        relative = current.relative_to(source_root)
        blocked = {name for name in names if name in {"__pycache__", ".pytest_cache", ".mypy_cache"}}
        if not relative.parts:
            blocked.update(name for name in names if name.casefold() in PROVIDED_PROJECT_QUARANTINE_ROOTS)
            blocked.update(name for name in names if name.casefold() in PROVIDED_PROJECT_QUARANTINE_FILES)
        return blocked

    shutil.copytree(source_root, output_path, ignore=ignore)
    packs = [get_domain_pack("FJSP"), get_domain_pack("fjsp_distributed_transfer")]
    if any(pack is None for pack in packs):
        raise ValueError("required FJSP Domain Packs are unavailable")
    preserve_paths = list(
        dict.fromkeys(
            relative_text
            for pack in packs
            if pack is not None
            for relative_text in pack.agent_generated_baseline_preserve_paths
        )
    )
    for relative_text in preserve_paths:
        relative = Path(relative_text)
        source = trusted_project_root / relative
        if not source.exists():
            raise ValueError(f"fixed Core dependency is missing: {relative_text}")
        target = output_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    return output_path.resolve()


def provided_project_read_paths(project_root: Path, *, target_file: str | None) -> list[str]:
    """Expose bounded supporting project text to Worker as read-only context."""

    target = str(target_file or "").replace("\\", "/")
    protected_prefixes = (
        "harness_agent/",
        "examples/standard_fjsp_evaluator.py",
        "examples/fjsp_release_time_evaluator.py",
        "examples/fjsp_machine_availability_evaluator.py",
        "examples/fjsp_priority_evaluator.py",
        "examples/fjsp_reentrant_evaluator.py",
        "examples/fjsp_jpc_tst_evaluator.py",
        "examples/fjsp_pbpm_evaluator.py",
        "examples/fjsp_alternative_path_evaluator.py",
        "examples/fjsp_distributed_transfer_evaluator.py",
    )
    result: list[str] = []
    for path in sorted(project_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file() or path.stat().st_size > 512_000:
            continue
        relative = path.relative_to(project_root).as_posix()
        if relative == target or relative.startswith(protected_prefixes):
            continue
        if path.suffix.lower() not in PROVIDED_PROJECT_READ_SUFFIXES:
            continue
        result.append(relative)
        if len(result) >= 200:
            break
    return result


def standard_worker_manifest(
    *,
    request: StandardWorkerLoopRequest,
    contract_path: Path,
    context_path: Path,
    loop_result: WorkerLoopResult,
    output_dir: Path,
) -> dict[str, Any]:
    """从 WorkerLoopResult 派生 Web/CLI 共享的运行摘要和产物索引。"""

    promoted_rounds = sum(1 for item in loop_result.rounds if item.decision == "promoted")
    round_payloads = [round_record_payload(item) for item in loop_result.rounds]
    loop_result_path = output_dir / "worker_loop" / "loop_result.json"
    loop_payload = {}
    if loop_result_path.exists():
        try:
            loop_payload = json.loads(loop_result_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            loop_payload = {}
    hypothesis_graph = loop_payload.get("hypothesis_graph") or {}
    experience_memory = loop_payload.get("experience_memory") or {}
    skill_usage_records = loop_payload.get("skill_usage_records") or []
    final_summary = summary_payload(loop_result.baseline_summary)
    final_round_index: int | None = None
    for item in loop_result.rounds:
        if item.decision == "promoted" and tuple(item.incumbent_key_after) == tuple(loop_result.final_key):
            final_summary = item.candidate_summary
            final_round_index = item.round_index
    latest_candidate_summary = (
        loop_result.rounds[-1].candidate_summary
        if loop_result.rounds
        else summary_payload(loop_result.baseline_summary)
    )
    repair_stats = worker_loop_repair_stats(loop_result.rounds)
    agent_quality = worker_loop_agent_quality_summary(loop_result)
    semantic_review = worker_loop_semantic_review_summary(loop_result)
    return {
        "status": loop_result.status,
        "terminal_reason": loop_result.stop_reason,
        "evaluation_mode": "agent_capability",
        "request": {
            "docs": [str(path) for path in request.docs],
            "instance_dir": str(request.instance_dir),
            "pattern": request.pattern,
            "instance_paths": [str(path) for path in request.instance_paths or []],
            "best_known_csv": str(request.best_known_csv) if request.best_known_csv else None,
            "slot_manifest": str(request.slot_manifest) if request.slot_manifest else None,
            "project_intake_manifest": str(request.project_intake_manifest) if request.project_intake_manifest else None,
            "previous_pipeline_memory": str(request.previous_pipeline_memory) if request.previous_pipeline_memory else None,
            "resume_loop_result": str(request.resume_loop_result) if request.resume_loop_result else None,
            "resumed_round_count": len(loop_result.rounds) - max(0, request.iterations)
            if request.resume_loop_result
            else 0,
            "additional_iterations": max(0, request.iterations)
            if request.resume_loop_result
            else 0,
            "seeds": request.seeds or [0],
            "baseline_source": loop_result.baseline_source,
            "agent_generated_solver_path": request.agent_generated_solver_path,
            "provided_solver_command": request.provided_solver_command,
            "provided_target_file": request.provided_target_file,
            "iterations": max(0, request.iterations),
            "apply_worker_changes": bool(request.apply_worker_changes),
            "promotion_repeats": max(1, request.promotion_repeats),
            "in_round_repair_attempts": max(0, request.in_round_repair_attempts),
            "max_competing_workers": max(1, min(4, request.max_competing_workers)),
            "semantic_reviewer": (
                type(request.semantic_reviewer).__name__ if request.semantic_reviewer is not None else None
            ),
        },
        "contract_path": str(contract_path),
        "context_packet_path": str(context_path),
        "baseline_key": list(loop_result.baseline_key),
        "baseline_source": loop_result.baseline_source,
        "baseline_generation": loop_result.baseline_generation,
        "final_key": list(loop_result.final_key),
        "best_legal_incumbent": candidate_incumbent_payload(loop_result.best_legal_incumbent),
        "best_activated_incumbent": candidate_incumbent_payload(loop_result.best_activated_incumbent),
        "improved": loop_result.final_key > loop_result.baseline_key,
        "round_count": len(loop_result.rounds),
        "promoted_rounds": promoted_rounds,
        "round_semantics": {
            "user_visible_round": "improvement_direction",
            "core_atomic_unit": "worker_attempt",
        },
        "hypothesis_graph": hypothesis_graph,
        "experience_memory": experience_memory,
        "skill_usage_records": skill_usage_records,
        "in_round_repair": repair_stats,
        "local_trials": repair_stats,
        "agent_generated_quality": agent_quality,
        "algorithm_semantic_review": semantic_review,
        "final_worktree": str(loop_result.final_worktree),
        "baseline_summary": summary_payload(loop_result.baseline_summary),
        "final_summary": final_summary,
        "final_round_index": final_round_index,
        "latest_candidate_summary": latest_candidate_summary,
        "rounds": round_payloads,
    }


def worker_loop_repair_stats(rounds: list[Any]) -> dict[str, Any]:
    repair_attempt_count = 0
    repair_round_count = 0
    recovered_round_count = 0
    final_rejected_after_repair = 0
    for item in rounds:
        diagnostics = item.proposal_diagnostics if hasattr(item, "proposal_diagnostics") else {}
        repair = diagnostics.get("in_round_repair") if isinstance(diagnostics, dict) else None
        if not isinstance(repair, dict):
            continue
        attempts = int(repair.get("repair_attempt_count", 0) or 0)
        if attempts <= 0:
            continue
        repair_round_count += 1
        repair_attempt_count += attempts
        if repair.get("recovered"):
            recovered_round_count += 1
        elif tuple(getattr(item, "candidate_key", ())) and all(
            isinstance(value, (int, float)) and float(value) == float("-inf")
            for value in getattr(item, "candidate_key", ())
        ):
            final_rejected_after_repair += 1
    return {
        "repair_round_count": repair_round_count,
        "repair_attempt_count": repair_attempt_count,
        "recovered_round_count": recovered_round_count,
        "final_rejected_after_repair": final_rejected_after_repair,
    }


def worker_loop_agent_quality_summary(loop_result: WorkerLoopResult) -> dict[str, Any]:
    """Summarize generated-solver quality gates for operator-facing reports."""

    baseline = loop_result.baseline_generation or {}
    baseline_judgment = baseline.get("agentic_judgment") if isinstance(baseline, dict) else {}
    baseline_checks = baseline_judgment.get("checks") if isinstance(baseline_judgment, dict) else {}
    baseline_repair = baseline.get("in_round_repair") if isinstance(baseline, dict) else {}
    if not isinstance(baseline_repair, dict):
        baseline_repair = {}
    round_summaries: list[dict[str, Any]] = []
    ja_rejected_rounds = 0
    quality_rejected_rounds = 0
    self_check_rejected_rounds = 0
    evaluator_valid_rounds = 0
    for item in loop_result.rounds:
        candidate_summary = item.candidate_summary or {}
        validation = candidate_summary.get("validation_summary") if isinstance(candidate_summary, dict) else {}
        judgment = validation.get("agentic_judgment") if isinstance(validation, dict) else {}
        issues = judgment.get("issues") if isinstance(judgment, dict) else []
        accepted = bool(judgment.get("accepted")) if isinstance(judgment, dict) else None
        if accepted is False:
            ja_rejected_rounds += 1
        if isinstance(issues, list) and "agent_generated_solver_quality_contract_missing" in issues:
            quality_rejected_rounds += 1
        if isinstance(issues, list) and "agent_generated_solver_self_check_incomplete" in issues:
            self_check_rejected_rounds += 1
        total = int(candidate_summary.get("total", 0) or 0) if isinstance(candidate_summary, dict) else 0
        valid = int(candidate_summary.get("valid", 0) or 0) if isinstance(candidate_summary, dict) else 0
        if total > 0 and valid == total:
            evaluator_valid_rounds += 1
        round_summaries.append(
            {
                "round_index": item.round_index,
                "decision": item.decision,
                "ja_accepted": accepted,
                "issues": issues if isinstance(issues, list) else [],
                "quality_risk_count": _quality_risk_count_from_judgment(judgment, "agent_generated_solver_quality_risks"),
                "self_check_risk_count": _quality_risk_count_from_judgment(
                    judgment,
                    "agent_generated_solver_self_check_risks",
                ),
                "evaluator_valid": bool(total > 0 and valid == total),
                "candidate_key": list(item.candidate_key),
            }
        )
    baseline_summary = {
        "enabled": loop_result.baseline_source == "agent_generated",
        "status": baseline.get("status") if isinstance(baseline, dict) else None,
        "ja_accepted": bool(baseline_judgment.get("accepted")) if isinstance(baseline_judgment, dict) else None,
        "quality_risk_count": _quality_risk_count_from_checks(baseline_checks, "agent_generated_solver_quality_risks"),
        "self_check_risk_count": _quality_risk_count_from_checks(
            baseline_checks,
            "agent_generated_solver_self_check_risks",
        ),
        "repair_attempt_count": int((baseline_repair or {}).get("repair_attempt_count", 0) or 0)
        if isinstance(baseline_repair, dict)
        else 0,
        "repair_recovered": bool((baseline_repair or {}).get("recovered")) if isinstance(baseline_repair, dict) else False,
    }
    return {
        "baseline": baseline_summary,
        "round_count": len(loop_result.rounds),
        "ja_rejected_rounds": ja_rejected_rounds,
        "quality_rejected_rounds": quality_rejected_rounds,
        "self_check_rejected_rounds": self_check_rejected_rounds,
        "evaluator_valid_rounds": evaluator_valid_rounds,
        "promoted_rounds": sum(1 for item in loop_result.rounds if item.decision == "promoted"),
        "rounds": round_summaries,
    }


def worker_loop_semantic_review_summary(loop_result: WorkerLoopResult) -> dict[str, Any]:
    baseline = loop_result.baseline_generation or {}
    baseline_review = baseline.get("semantic_review") if isinstance(baseline, dict) else {}
    baseline_repair = baseline.get("in_round_repair") if isinstance(baseline, dict) else {}
    if not isinstance(baseline_repair, dict):
        baseline_repair = {}
    baseline_reviews = [
        attempt.get("semantic_review")
        for attempt in (baseline_repair.get("attempts") or [])
        if isinstance(attempt, dict)
        and isinstance(attempt.get("semantic_review"), dict)
        and attempt.get("semantic_review")
    ]
    if not baseline_reviews and isinstance(baseline_review, dict) and baseline_review:
        baseline_reviews = [baseline_review]
    round_reviews: list[dict[str, Any]] = []
    for item in loop_result.rounds:
        repair = (
            item.proposal_diagnostics.get("in_round_repair")
            if isinstance(item.proposal_diagnostics, dict)
            else {}
        )
        attempt_reviews = [
            attempt.get("semantic_review")
            for attempt in ((repair or {}).get("attempts") or [])
            if isinstance(attempt, dict)
            and isinstance(attempt.get("semantic_review"), dict)
            and attempt.get("semantic_review")
        ]
        if attempt_reviews:
            round_reviews.extend(attempt_reviews)
        elif isinstance(item.semantic_review, dict) and item.semantic_review:
            round_reviews.append(item.semantic_review)
    reviews = baseline_reviews + round_reviews
    statuses: dict[str, int] = {}
    blocking_finding_count = 0
    warning_finding_count = 0
    for review in reviews:
        status = str(review.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        for finding in review.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            if finding.get("blocking"):
                blocking_finding_count += 1
            else:
                warning_finding_count += 1
    return {
        "configured": any(str(review.get("reviewer") or "") != "none" for review in reviews)
        or (
            isinstance(baseline_review, dict)
            and bool(baseline_review)
            and str(baseline_review.get("reviewer") or "") != "none"
        ),
        "baseline": baseline_review if isinstance(baseline_review, dict) else {},
        "review_attempt_count": len(reviews),
        "baseline_review_attempt_count": len(baseline_reviews),
        "round_review_attempt_count": len(round_reviews),
        "reviewed_attempt_count": sum(
            1
            for review in reviews
            if review.get("status") in {"pass", "warning", "repair_required"}
        ),
        "reviewed_round_count": sum(
            1
            for review in round_reviews
            if review.get("status") in {"pass", "warning", "repair_required"}
        ),
        "status_counts": statuses,
        "blocking_finding_count": blocking_finding_count,
        "warning_finding_count": warning_finding_count,
        "repair_required_attempt_count": statuses.get("repair_required", 0),
        "repair_required_round_count": sum(
            1 for review in round_reviews if review.get("status") == "repair_required"
        ),
    }


def _quality_risk_count_from_judgment(judgment: Any, key: str) -> int:
    if not isinstance(judgment, dict):
        return 0
    checks = judgment.get("checks")
    return _quality_risk_count_from_checks(checks, key)


def _quality_risk_count_from_checks(checks: Any, key: str) -> int:
    if not isinstance(checks, dict):
        return 0
    value = checks.get(key)
    return len(value) if isinstance(value, list) else 0


def render_standard_worker_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# Standard FJSP Worker Loop Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Terminal reason: `{manifest.get('terminal_reason')}`",
        f"- Evaluation mode: `{manifest.get('evaluation_mode')}`",
        f"- Baseline source: `{manifest.get('baseline_source')}`",
        f"- Baseline key: `{json.dumps(manifest.get('baseline_key'), ensure_ascii=False)}`",
        f"- Final key: `{json.dumps(manifest.get('final_key'), ensure_ascii=False)}`",
        f"- Improved: `{manifest.get('improved')}`",
        f"- Rounds: `{manifest.get('round_count')}`",
        f"- Promoted rounds: `{manifest.get('promoted_rounds')}`",
        f"- Direction graph: `{json.dumps((manifest.get('hypothesis_graph') or {}).get('status_counts') or {}, ensure_ascii=False)}`",
        f"- Candidate lessons: `{len(((manifest.get('experience_memory') or {}).get('memory_tiers') or {}).get('candidate_lessons') or [])}`",
        f"- In-round repair: `{json.dumps(manifest.get('in_round_repair') or {}, ensure_ascii=False)}`",
        f"- Agent quality: `{json.dumps(manifest.get('agent_generated_quality') or {}, ensure_ascii=False)}`",
        f"- Algorithm semantic review: `{json.dumps(manifest.get('algorithm_semantic_review') or {}, ensure_ascii=False)}`",
        f"- Final worktree: `{manifest.get('final_worktree')}`",
        "",
        (
            "> This run measures Agent-written solver capability."
            if manifest.get("evaluation_mode") == "agent_capability"
            else "> Legacy/reference solver tuning run. Do not report this result as Agent-written solver capability."
        ),
        "",
        "## Artifacts",
        "",
    ]
    for name, path in (manifest.get("artifacts") or {}).items():
        lines.append(f"- {name}: `{path}`")
    if manifest.get("baseline_generation"):
        lines.extend(
            [
                "",
                "## Agent-Generated Baseline",
                "",
                f"```json\n{json.dumps(manifest.get('baseline_generation'), ensure_ascii=False, indent=2)}\n```",
            ]
        )
    lines.extend(
        [
            "",
            "## Baseline Summary",
            "",
            f"```json\n{json.dumps(manifest.get('baseline_summary') or {}, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Rounds",
            "",
            "| Round | Decision | Worker | Duplicate | Semantic Review | Promotion Check | Proposal Audit | Candidate Key | Changed Files | Patch |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in manifest.get("rounds", []):
        diagnostics = item.get("proposal_diagnostics") or {}
        proposal_audit = compact_proposal_audit(diagnostics) if isinstance(diagnostics, dict) else {}
        promotion_check = item.get("promotion_check") or {}
        lines.append(
            f"| {item.get('round_index')} | {item.get('decision')} | {item.get('worker_status')} | "
            f"{item.get('duplicate_proposal')} | "
            f"`{json.dumps(item.get('semantic_review') or {}, ensure_ascii=False)}` | "
            f"`{json.dumps(compact_promotion_check(promotion_check), ensure_ascii=False)}` | "
            f"`{json.dumps(proposal_audit, ensure_ascii=False)}` | "
            f"`{json.dumps(item.get('candidate_key'), ensure_ascii=False)}` | "
            f"`{json.dumps(item.get('worker_changed_files') or [], ensure_ascii=False)}` | "
            f"`{item.get('patch_path')}` |"
        )
    lines.extend(
        [
            "",
            "Promotion is allowed only when the Core evaluator-backed objective key is strictly better than the incumbent key.",
            "When `promotion_repeats` is greater than 1, promotion also requires a repeated Core evaluator probe to remain strictly better.",
            "Proposal-audit diagnostics are carried forward as reflection context and do not change promotion semantics.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def resolve_input_path(project_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return (project_root / path).resolve()
