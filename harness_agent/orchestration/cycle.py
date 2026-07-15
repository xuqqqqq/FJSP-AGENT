"""单次候选周期：隔离 worktree、应用修改、JA 审查和固定 Core 评测。"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from harness_agent.agents.judgment import (
    AgenticJudgment,
    ErrorAnalysis,
    analyze_rejected_judgment,
    analyze_run_summary,
    judge_worker_result,
    write_judgment_artifacts,
)
from harness_agent.domains.pack import get_domain_pack
from harness_agent.core.graph import GraphHarnessRunner
from harness_agent.core.models import TaskContract, resolve_project_path
from harness_agent.core.runner import RunSummary
from harness_agent.worker import CodingWorker, ExperimentSpec, WorkerResult


@dataclass(frozen=True)
class WorkerCycleResult:
    """一次候选 attempt 的完整结果。

    同时保存 Worker 过程、真实 worktree 差异、JA、smoke/full Core 结果和
    错误分析。上层 loop 只根据这些证据决定是否修补或进入 promotion check。
    """

    worker_result: WorkerResult
    summary: RunSummary
    worktree_path: Path
    harness_output_dir: Path
    delta_path: Path
    patch_path: Path
    agentic_judgment: AgenticJudgment
    agentic_error_analysis: ErrorAnalysis | None
    smoke_summary: RunSummary | None = None
    smoke_output_dir: Path | None = None
    diagnostic_smoke_summary: RunSummary | None = None
    diagnostic_smoke_output_dir: Path | None = None
    full_evaluation_started: bool = False


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
    """在隔离候选树中执行一次“写代码、审查、评测”周期。

    候选目录从传入的 `project_root` 复制而来；在正式轮中它就是当前
    incumbent，在修补中则可能是上一 attempt。无论结果如何，本函数都不会
    反向覆盖源目录。
    """

    output_dir = output_dir.resolve()
    worktree_path = output_dir / "candidate_worktree"
    worker_output_dir = output_dir / "worker"
    harness_output_dir = output_dir / "harness"
    smoke_output_dir = output_dir / "harness_smoke"
    diagnostic_smoke_output_dir = output_dir / "harness_diagnostic_smoke"
    # 1. 建立候选沙箱并记录修改前快照。只读 Core 依赖会复制进来，但仍不
    # 属于允许修改范围。
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
    # 2. Coding Worker 只接触候选 worktree；其 changed_files 自报值稍后会
    # 与真实文件快照合并，防止漏报。
    worker_result = worker.run_experiment(spec)
    after_snapshot = collect_worktree_snapshot(worktree_path)
    delta = compute_worktree_delta(before_snapshot, after_snapshot)
    detected_changed_files = changed_files_from_worktree_delta(delta)
    if detected_changed_files:
        worker_result = replace(
            worker_result,
            changed_files=sorted(set(worker_result.changed_files or []) | set(detected_changed_files)),
        )
    delta_path, patch_path = write_worktree_delta_artifacts(
        root=worktree_path,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        delta=delta,
        output_dir=output_dir,
    )
    # 3. JA 在执行候选前拦截确定性风险。JA 拒绝时默认不进入正式 evaluator。
    agentic_judgment = judge_worker_result(
        worker_result=worker_result,
        worktree_path=worktree_path,
        context_packet_path=context_packet_path,
        output_dir=output_dir,
        apply_worker_changes=apply_worker_changes,
    )
    agentic_error_analysis: ErrorAnalysis | None = None
    diagnostic_smoke_summary: RunSummary | None = None
    if not agentic_judgment.accepted:
        summary = RunSummary(
            total=0,
            valid=0,
            failed=0,
            best_experiment_id=None,
            best_metrics={},
            best_candidate_id=None,
            best_candidate_metrics=None,
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={"agentic_judgment": agentic_judgment.to_payload()},
        )
        agentic_error_analysis = analyze_rejected_judgment(judgment=agentic_judgment, output_dir=output_dir)
        smoke_summary = None
        # 对 Agent 自写 solver 的部分“源码形态”拒绝，可做一次只诊断 smoke。
        # 它不能直接晋升，只用于判断静态门禁是否过度保守并生成修补事实。
        if should_run_agent_generated_smoke_diagnostic(
            contract=contract,
            worker_result=worker_result,
            agentic_judgment=agentic_judgment,
        ):
            diagnostic_smoke_summary = run_diagnostic_smoke(
                contract=contract,
                worktree_path=worktree_path,
                output_dir=diagnostic_smoke_output_dir,
            )
        if should_soft_accept_agent_generated_quality_rejection(
            agentic_judgment=agentic_judgment,
            diagnostic_smoke_summary=diagnostic_smoke_summary,
        ):
            agentic_judgment = soften_agent_generated_quality_judgment(
                agentic_judgment=agentic_judgment,
                diagnostic_smoke_summary=diagnostic_smoke_summary,
            )
            write_judgment_artifacts(output_dir=output_dir, judgment=agentic_judgment)
            agentic_error_analysis = None
            smoke_contract = smoke_gate_contract(contract)
            smoke_runner = GraphHarnessRunner(
                contract=smoke_contract,
                project_root=worktree_path,
                output_dir=smoke_output_dir,
            )
            try:
                smoke_summary = smoke_runner.run()
            finally:
                smoke_runner.close()
            smoke_passed = smoke_summary.total > 0 and smoke_summary.valid == smoke_summary.total
            full_evaluation_started = False
            if smoke_passed and full_evaluation_required(contract):
                runner = GraphHarnessRunner(contract=contract, project_root=worktree_path, output_dir=harness_output_dir)
                try:
                    summary = runner.run()
                finally:
                    runner.close()
                full_evaluation_started = True
            else:
                summary = smoke_summary
                harness_output_dir = smoke_output_dir
            agentic_error_analysis = analyze_run_summary(summary=summary, output_dir=output_dir)
        else:
            full_evaluation_started = False
    else:
        # 4. JA 接受后先用一个 seed 做 smoke；只有 smoke 全部合法才支付完整
        # 多 seed/多算例评测成本。
        smoke_contract = smoke_gate_contract(contract)
        smoke_runner = GraphHarnessRunner(contract=smoke_contract, project_root=worktree_path, output_dir=smoke_output_dir)
        try:
            smoke_summary = smoke_runner.run()
        finally:
            smoke_runner.close()
        smoke_passed = smoke_summary.total > 0 and smoke_summary.valid == smoke_summary.total
        full_evaluation_started = False
        if smoke_passed and full_evaluation_required(contract):
            runner = GraphHarnessRunner(contract=contract, project_root=worktree_path, output_dir=harness_output_dir)
            try:
                summary = runner.run()
            finally:
                runner.close()
            full_evaluation_started = True
        else:
            summary = smoke_summary
            harness_output_dir = smoke_output_dir
        agentic_error_analysis = analyze_run_summary(summary=summary, output_dir=output_dir)
    write_cycle_report(
        output_dir=output_dir,
        worker_result=worker_result,
        summary=summary,
        worktree_path=worktree_path,
        harness_output_dir=harness_output_dir,
        delta=delta,
        delta_path=delta_path,
        patch_path=patch_path,
        agentic_judgment=agentic_judgment,
        agentic_error_analysis=agentic_error_analysis,
        smoke_summary=smoke_summary,
        smoke_output_dir=smoke_output_dir if smoke_summary else None,
        diagnostic_smoke_summary=diagnostic_smoke_summary,
        diagnostic_smoke_output_dir=diagnostic_smoke_output_dir if diagnostic_smoke_summary else None,
        full_evaluation_started=full_evaluation_started,
    )
    return WorkerCycleResult(
        worker_result=worker_result,
        summary=summary,
        worktree_path=worktree_path,
        harness_output_dir=harness_output_dir,
        delta_path=delta_path,
        patch_path=patch_path,
        agentic_judgment=agentic_judgment,
        agentic_error_analysis=agentic_error_analysis,
        smoke_summary=smoke_summary,
        smoke_output_dir=smoke_output_dir if smoke_summary else None,
        diagnostic_smoke_summary=diagnostic_smoke_summary,
        diagnostic_smoke_output_dir=diagnostic_smoke_output_dir if diagnostic_smoke_summary else None,
        full_evaluation_started=full_evaluation_started,
    )


def smoke_gate_contract(contract: TaskContract) -> TaskContract:
    """Return the one-seed evaluator smoke used before full candidate scoring."""

    first_seed = contract.budget.seeds[0] if contract.budget.seeds else 0
    return replace(
        contract,
        task_id=f"{contract.task_id}_candidate_smoke",
        budget=replace(
            contract.budget,
            rounds=1,
            seeds=[first_seed],
            max_workers=1,
        ),
    )


def full_evaluation_required(contract: TaskContract) -> bool:
    return contract.budget.rounds > 1 or len(contract.budget.seeds) > 1


def should_run_agent_generated_smoke_diagnostic(
    *,
    contract: TaskContract,
    worker_result: WorkerResult,
    agentic_judgment: AgenticJudgment,
) -> bool:
    """Run one diagnostic evaluator pass for rejected generated solvers.

    This pass never promotes a candidate.  It exists to turn JA-rejected solver
    claims into concrete evaluator feedback for same-round repair.
    """

    if agentic_judgment.accepted:
        return False
    solver_command = str(contract.commands.solver or "").replace("\\", "/").lower()
    if "agent_generated" not in solver_command:
        return False
    changed = [str(path).replace("\\", "/").lower() for path in worker_result.changed_files or []]
    return any(path.startswith("examples/agent_generated") and path.endswith(".py") for path in changed)


def should_soft_accept_agent_generated_quality_rejection(
    *,
    agentic_judgment: AgenticJudgment,
    diagnostic_smoke_summary: RunSummary | None,
) -> bool:
    """Let evaluator-proven generated solvers pass soft source-shape JA gaps.

    Static quality checks are intentionally conservative, but they should not
    trap an agent in helper-renaming repair loops after the fixed evaluator has
    already proved complete coverage and legality on the active instance.
    Concrete safety failures such as empty-schedule fallback, syntax errors,
    parser hardcoding, backend imports, apply failures, or validator edits stay
    hard-blocking.
    """

    if agentic_judgment.accepted or diagnostic_smoke_summary is None:
        return False
    if diagnostic_smoke_summary.total <= 0 or diagnostic_smoke_summary.valid != diagnostic_smoke_summary.total:
        return False
    issues = set(agentic_judgment.issues or [])
    if issues != {"agent_generated_solver_quality_contract_missing"}:
        return False
    checks = agentic_judgment.checks or {}
    quality_risks = [str(item) for item in checks.get("agent_generated_solver_quality_risks") or []]
    if not quality_risks:
        return False
    # Source-shape and named-method gaps are hypotheses for the semantic
    # reviewer, not legality facts.  Once Core has validated the active
    # instance, only deterministic safety/generalization hazards stay blocking.
    hard_quality_fragments = (
        "hardcode",
        "job_precedence_guard_mismatch",
        "parser assumes one physical operation line",
        "active feature",
        "missing setup-aware capabilities",
        "failed_move_mutates_current_without_rollback",
    )
    return not any(
        any(fragment in risk for fragment in hard_quality_fragments)
        for risk in quality_risks
    )


def soften_agent_generated_quality_judgment(
    *,
    agentic_judgment: AgenticJudgment,
    diagnostic_smoke_summary: RunSummary,
) -> AgenticJudgment:
    checks = dict(agentic_judgment.checks or {})
    checks["soft_accepted_by_diagnostic_smoke"] = {
        "reason": "diagnostic_smoke_validated_soft_agent_generated_quality_gaps",
        "original_issues": list(agentic_judgment.issues or []),
        "original_quality_risks": list(checks.get("agent_generated_solver_quality_risks") or []),
        "diagnostic_metrics": diagnostic_smoke_summary.best_candidate_metrics or diagnostic_smoke_summary.best_metrics,
    }
    return replace(
        agentic_judgment,
        accepted=True,
        right=True,
        issues=[],
        suggestions=[
            "Diagnostic smoke validated the active generated solver on the fixed evaluator; static quality-contract gaps were downgraded to warnings.",
            "Continue with evaluator-backed comparison, and preserve the validated parser/coverage/eligibility behavior in later edits.",
        ],
        checks=checks,
    )


def run_diagnostic_smoke(*, contract: TaskContract, worktree_path: Path, output_dir: Path) -> RunSummary:
    """Run a diagnostic-only one-seed smoke and capture quick-test failures."""

    smoke_contract = smoke_gate_contract(contract)
    runner = GraphHarnessRunner(contract=smoke_contract, project_root=worktree_path, output_dir=output_dir)
    try:
        summary = runner.run()
    except Exception as exc:  # noqa: BLE001 - diagnostic feedback should not abort a repairable round.
        summary = RunSummary(
            total=1,
            valid=0,
            failed=1,
            best_experiment_id=None,
            best_metrics={},
            best_candidate_id=None,
            best_candidate_metrics=None,
            candidate_summaries=[],
            pareto_frontier=[],
            validation_summary={
                "diagnostic_only": True,
                "status_counts": {"failed_runtime": 1},
                "top_errors": [{"error": f"diagnostic smoke failed before evaluator records: {exc}", "count": 1}],
            },
        )
        write_diagnostic_smoke_report(output_dir=output_dir, summary=summary)
        return summary
    finally:
        runner.close()
    return summary


def write_diagnostic_smoke_report(*, output_dir: Path, summary: RunSummary) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Diagnostic Smoke Report",
        "",
        "This smoke run is diagnostic-only. It is used as repair feedback for a JA-rejected agent-generated solver and is not eligible for promotion.",
        "",
        f"- Total: {summary.total}",
        f"- Valid: {summary.valid}",
        f"- Failed: {summary.failed}",
        f"- Validation summary: `{json.dumps(summary.validation_summary or {}, ensure_ascii=False)}`",
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_candidate_worktree(*, project_root: Path, contract: TaskContract, worktree_path: Path) -> None:
    """构建候选工作区，并把可修改源码与只读 Core 依赖分开复制。"""

    # worktree 是一次性产物目录，可以安全重建；源 project_root 不会删除。
    if worktree_path.exists():
        shutil.rmtree(worktree_path)
    worktree_path.mkdir(parents=True, exist_ok=True)
    allowed_paths = contract.paths.allowed_paths or ["."]
    forbidden_paths = set(contract.paths.forbidden_paths or [])
    forbidden_paths.update(
        {".git", "outputs", "__pycache__", ".pytest_cache", ".mypy_cache", ".algoforge_worker_inputs"}
    )
    if "." in allowed_paths:
        _copy_directory_contents(project_root, worktree_path, forbidden_paths)
    else:
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
    copy_read_only_core_dependencies(
        project_root=project_root,
        contract=contract,
        worktree_path=worktree_path,
        forbidden_paths=forbidden_paths,
    )
    stage_worker_input_files(contract=contract, project_root=project_root, worktree_path=worktree_path)


def copy_read_only_core_dependencies(
    *,
    project_root: Path,
    contract: TaskContract,
    worktree_path: Path,
    forbidden_paths: set[str],
) -> None:
    """复制 evaluator 运行所需文件，但不把它们加入 Agent 可修改范围。

    `allowed_paths` 只描述候选改动边界。parser/evaluator 的运行时依赖由
    domain pack 单独声明，避免为了让 Core 可导入而开放整个后端目录。
    """

    domain_pack = get_domain_pack(contract.problem_family)
    if domain_pack is None:
        return
    for relative in domain_pack.agent_generated_baseline_preserve_paths:
        if not relative:
            continue
        source = project_root / relative
        if not source.exists():
            continue
        target = worktree_path / relative
        if source.is_dir():
            shutil.copytree(source, target, ignore=_ignore_names(forbidden_paths), dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def stage_worker_input_files(*, contract: TaskContract, project_root: Path, worktree_path: Path) -> None:
    """把首个算例镜像到沙箱，供 Coding Worker 做有界检查。

    正式 Core 仍使用契约中的权威路径；镜像只解决非交互工具访问工作区外
    文件时的授权问题，且通过 manifest 明确标记为只读检查输入。
    """

    if not contract.instances:
        return
    instance = contract.instances[0]
    source = resolve_project_path(project_root, instance.path)
    if not source.is_file():
        return

    input_root = worktree_path / ".algoforge_worker_inputs"
    instances_root = input_root / "instances"
    instances_root.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(instance.id)).strip("._") or "instance"
    suffix = source.suffix or ".dat"
    local_path = instances_root / f"000_{safe_id}{suffix}"
    shutil.copy2(source, local_path)
    manifest = {
        "read_only": True,
        "purpose": "coding-worker inspection and one bounded smoke only",
        "instances": [
            {
                "id": str(instance.id),
                "local_path": local_path.relative_to(worktree_path).as_posix(),
            }
        ],
    }
    (input_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 候选证据序列化与真实文件差异
# ---------------------------------------------------------------------------

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
    agentic_judgment: AgenticJudgment,
    agentic_error_analysis: ErrorAnalysis | None,
    smoke_summary: RunSummary | None = None,
    smoke_output_dir: Path | None = None,
    diagnostic_smoke_summary: RunSummary | None = None,
    diagnostic_smoke_output_dir: Path | None = None,
    full_evaluation_started: bool = False,
) -> None:
    """将 Worker、JA、smoke 和 Core 结果写成同一份 attempt 报告。"""

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
            "smoke_output": str(smoke_output_dir) if smoke_output_dir else None,
            "worktree_delta": str(delta_path),
            "worktree_patch": str(patch_path),
        },
        "smoke_gate": {
            "enabled": smoke_summary is not None,
            "passed": bool(smoke_summary and smoke_summary.total > 0 and smoke_summary.valid == smoke_summary.total),
            "full_evaluation_started": full_evaluation_started,
            "summary": run_summary_payload(smoke_summary) if smoke_summary else None,
        },
        "diagnostic_smoke": {
            "enabled": diagnostic_smoke_summary is not None,
            "diagnostic_only": True,
            "passed": bool(
                diagnostic_smoke_summary
                and diagnostic_smoke_summary.total > 0
                and diagnostic_smoke_summary.valid == diagnostic_smoke_summary.total
            ),
            "summary": run_summary_payload(diagnostic_smoke_summary) if diagnostic_smoke_summary else None,
            "output_dir": str(diagnostic_smoke_output_dir) if diagnostic_smoke_output_dir else None,
        },
        "worktree_delta": delta,
        "agentic_judgment": agentic_judgment.to_payload(),
        "agentic_error_analysis": agentic_error_analysis.to_payload() if agentic_error_analysis else None,
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
        "## Agentic Judgment",
        "",
        f"- Accepted: `{agentic_judgment.accepted}`",
        f"- Issues: `{json.dumps(agentic_judgment.issues, ensure_ascii=False)}`",
        f"- Suggestions: `{json.dumps(agentic_judgment.suggestions, ensure_ascii=False)}`",
    ]
    if agentic_error_analysis:
        lines.extend(
            [
                "",
                "## Agentic Error Analysis",
                "",
                f"- Source: `{agentic_error_analysis.source}`",
                f"- Diagnosis: `{json.dumps(agentic_error_analysis.diagnosis, ensure_ascii=False)}`",
                f"- Suggestions: `{json.dumps(agentic_error_analysis.suggestions, ensure_ascii=False)}`",
            ]
        )
    if smoke_summary:
        lines.extend(
            [
                "",
                "## Smoke Gate",
                "",
                f"- Passed: `{smoke_summary.total > 0 and smoke_summary.valid == smoke_summary.total}`",
                f"- Full evaluation started: `{full_evaluation_started}`",
                f"- Smoke output: `{smoke_output_dir}`",
                f"- Smoke summary: `{json.dumps(run_summary_payload(smoke_summary), ensure_ascii=False)}`",
            ]
        )
    if diagnostic_smoke_summary:
        lines.extend(
            [
                "",
                "## Diagnostic Smoke",
                "",
                "- Diagnostic only: `true`",
                f"- Passed: `{diagnostic_smoke_summary.total > 0 and diagnostic_smoke_summary.valid == diagnostic_smoke_summary.total}`",
                f"- Smoke output: `{diagnostic_smoke_output_dir}`",
                f"- Smoke summary: `{json.dumps(run_summary_payload(diagnostic_smoke_summary), ensure_ascii=False)}`",
            ]
        )
    lines.extend(
        [
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
    )
    (output_dir / "cycle_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_summary_payload(summary: RunSummary) -> dict[str, Any]:
    return {
        "total": summary.total,
        "valid": summary.valid,
        "failed": summary.failed,
        "best_experiment_id": summary.best_experiment_id,
        "best_metrics": summary.best_metrics,
        "best_candidate_id": summary.best_candidate_id,
        "best_candidate_metrics": summary.best_candidate_metrics,
        "candidate_summaries": summary.candidate_summaries or [],
        "pareto_frontier": summary.pareto_frontier or [],
        "validation_summary": summary.validation_summary or {},
    }


def collect_worktree_snapshot(root: Path, max_text_bytes: int = 200_000) -> dict[str, dict[str, Any]]:
    """以哈希记录候选树；小型文本同时保留内容以生成可审计 patch。"""

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
    """比较前后快照，得到 added/modified/deleted 的真实改动清单。"""

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


def changed_files_from_worktree_delta(delta: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for key in ("added", "modified", "deleted"):
        for item in delta.get(key, []) or []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.add(item["path"])
    return sorted(paths)


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
        before_lines = normalized_diff_lines(before_text)
        after_lines = normalized_diff_lines(after_text)
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


def normalized_diff_lines(text: str) -> list[str]:
    """Normalize line endings for review diffs without changing delta hashes."""

    return [f"{line}\n" for line in text.splitlines()]


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
