from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context_packet import ContextPacketRequest, write_context_packet
from .main_agent import DirectionPlanningAgent
from .loop_runner import (
    DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
    WorkerLoopResult,
    compact_promotion_check,
    compact_proposal_audit,
    round_record_payload,
    run_worker_loop,
    summary_payload,
)
from .loop_runner import normalize_baseline_source
from .models import TaskContract
from .semantic_review import AlgorithmSemanticReviewer
from .slot_manifest import load_slot_manifest
from .worker import CodingWorker


SDST_ZI_FEATURES_CONSUMER_FORMULA = "base * (1 + 0.10 * setup_adjacent_ratio * is_critical)"


@dataclass(frozen=True)
class StandardWorkerLoopRequest:
    """High-level request for a standard-FJSP code-evolution loop."""

    docs: list[Path]
    instance_dir: Path
    pattern: str
    output_dir: Path
    project_root: Path
    worker: CodingWorker
    main_agent: DirectionPlanningAgent | None = None
    semantic_reviewer: AlgorithmSemanticReviewer | None = None
    best_known_csv: Path | None = None
    knowledge_cards: list[Path] | None = None
    slot_manifest: Path | None = None
    project_intake_manifest: Path | None = None
    previous_pipeline_memory: Path | None = None
    max_instances: int | None = None
    seeds: list[int] | None = None
    timeout_seconds: int = 60
    max_workers: int = 1
    solver: str = "portfolio"
    portfolio_size: int = 16
    local_search_restarts: int = 1
    local_search_initial_pool_size: int = 1
    local_search_iterations: int = 40
    local_search_neighbor_limit: int = 100
    local_search_time_limit_sec: float = 2.0
    local_search_neighborhood_profile: str = "combined"
    awls_restarts: int = 2
    awls_cycles_per_restart: int = 1000
    awls_iterations: int = 10000
    awls_time_limit_sec: float = 10.0
    awls_init: str = "random"
    awls_exact_select_top_k: int = 0
    awls_beta: int = 500
    awls_gamma: int = 40
    awls_theta: int = 5
    awls_zi_policy: str = "cpp"
    awls_zi_formula: str = ""
    awls_critical_block_exhaustive_pct: int = 0
    awls_same_machine_eval: str = "stable"
    awls_portfolio_lanes: str = ""
    iterations: int = 1
    max_steps: int = 4
    max_runtime_seconds: int = 120
    apply_worker_changes: bool = False
    promotion_repeats: int = 1
    in_round_repair_attempts: int = DEFAULT_IN_ROUND_REPAIR_ATTEMPTS
    baseline_source: str = "current_project"
    agent_generated_solver_path: str = "examples/agent_generated_fjsp_solver.py"
    experiment_id: str = "standard_worker_loop"
    hypothesis: str = (
        "Improve the standard FJSP solver under the fixed evaluator. "
        "State the rule-level idea before editing code."
    )


def run_standard_worker_loop(request: StandardWorkerLoopRequest) -> dict[str, Any]:
    """Build a standard-FJSP contract, context packet, and coding-worker loop."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contract_path = output_dir / "standard_worker_contract.json"
    context_path = output_dir / "context_packet.json"
    contract_payload = build_standard_worker_contract_payload(request)
    contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contract = TaskContract.load(contract_path)
    errors = contract.validate(request.project_root)
    if errors:
        raise ValueError(f"generated standard worker contract is invalid: {errors}")

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
    loop_result = run_worker_loop(
        contract=contract,
        project_root=request.project_root,
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
        baseline_source=request.baseline_source,
        in_round_repair_attempts=max(0, request.in_round_repair_attempts),
    )
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
    instance_dir = resolve_input_path(request.project_root, request.instance_dir)
    paths = sorted(instance_dir.glob(request.pattern))
    if request.max_instances is not None:
        paths = paths[: request.max_instances]
    if not paths:
        raise FileNotFoundError(f"no standard FJSP instances matched {instance_dir / request.pattern}")

    resources: dict[str, str] = {}
    solver = standard_solver_command(request)
    quick_test = "python -m compileall harness_agent examples"
    if normalize_baseline_source(request.baseline_source) == "agent_generated":
        solver_path = str(request.agent_generated_solver_path or "examples/agent_generated_fjsp_solver.py").replace(
            "\\", "/"
        )
        quick_test = f"python -m py_compile {solver_path}"
    evaluator = "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"
    if request.best_known_csv:
        best_known_csv = resolve_input_path(request.project_root, request.best_known_csv)
        resources["best_known_csv"] = str(best_known_csv)
        evaluator += " --best-known-csv {best_known_csv}"

    return {
        "task_id": request.experiment_id,
        "problem_family": "FJSP",
        "description": "Standard-FJSP coding-worker loop contract generated by the harness.",
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
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "resources": resources,
        "review": {
            "status": "confirmed",
            "note": (
                "Generated from standard FJSP harness parameters; fixed evaluator remains authoritative. "
                f"baseline_source={normalize_baseline_source(request.baseline_source)}."
            ),
            "baseline_source": normalize_baseline_source(request.baseline_source),
            "agent_generated_solver_path": request.agent_generated_solver_path,
        },
    }


def standard_solver_command(request: StandardWorkerLoopRequest) -> str:
    if normalize_baseline_source(request.baseline_source) == "agent_generated":
        solver_path = str(request.agent_generated_solver_path or "examples/agent_generated_fjsp_solver.py").replace("\\", "/")
        return (
            f"python {solver_path} --input {{instance}} --output {{solution}} --seed {{seed}} "
            "--time-limit-sec {solver_time_limit_seconds}"
        )
    awls_zi_policy, awls_zi_formula = effective_awls_zi_settings(request)
    if request.solver == "portfolio":
        return (
            "python examples/standard_fjsp_portfolio_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {max(1, request.portfolio_size)}"
        )
    if request.solver == "local-search":
        return (
            "python examples/standard_fjsp_local_search_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--portfolio-size {max(1, request.portfolio_size)} "
            f"--restarts {max(1, request.local_search_restarts)} "
            f"--initial-pool-size {max(1, request.local_search_initial_pool_size)} "
            f"--iterations {max(0, request.local_search_iterations)} "
            f"--neighbor-limit {max(1, request.local_search_neighbor_limit)} "
            f"--time-limit-sec {max(0.1, request.local_search_time_limit_sec)} "
            f"--neighborhood-profile {request.local_search_neighborhood_profile}"
        )
    if request.solver == "awls":
        command = (
            "python examples/standard_fjsp_awls_solver.py "
            "--input {instance} --output {solution} --seed {seed} "
            f"--restarts {max(1, request.awls_restarts)} "
            f"--cycles-per-restart {max(1, request.awls_cycles_per_restart)} "
            f"--iterations {max(0, request.awls_iterations)} "
            f"--time-limit-sec {max(0.1, request.awls_time_limit_sec)} "
            f"--init {request.awls_init} "
            f"--exact-select-top-k {max(0, request.awls_exact_select_top_k)} "
            f"--beta {max(1, request.awls_beta)} "
            f"--gamma {max(1, request.awls_gamma)} "
            f"--theta {max(0, request.awls_theta)}"
        )
        command += f" --critical-block-exhaustive-pct {max(0, min(100, request.awls_critical_block_exhaustive_pct))}"
        command += f" --same-machine-eval {request.awls_same_machine_eval}"
        if awls_zi_policy != "cpp":
            command += f" --zi-policy {awls_zi_policy}"
            if awls_zi_policy == "formula" and awls_zi_formula:
                escaped_formula = awls_zi_formula.replace('"', '\\"')
                command += f' --zi-formula "{escaped_formula}"'
        if request.awls_portfolio_lanes:
            command += f' --portfolio-lanes "{request.awls_portfolio_lanes}"'
        return command
    raise ValueError(f"unknown standard worker solver: {request.solver}")


def effective_awls_zi_settings(request: StandardWorkerLoopRequest) -> tuple[str, str]:
    """Return zi settings that make the selected AWLS slot executable.

    The SDST zi-feature slot only enriches the formula/slot feature map.  If a
    worker edits it while the solver remains on cpp/critical zi, Core evaluation
    cannot observe that edit.  Use a conservative formula consumer by default
    only when this exact slot is user-confirmed.
    """

    if confirmed_slot_ids(request.slot_manifest) == {"awls_sdst_zi_features"} and request.awls_zi_policy == "cpp":
        return "formula", request.awls_zi_formula or SDST_ZI_FEATURES_CONSUMER_FORMULA
    return request.awls_zi_policy, request.awls_zi_formula


def confirmed_slot_ids(slot_manifest: Path | None) -> set[str]:
    if slot_manifest is None:
        return set()
    try:
        payload = load_slot_manifest(slot_manifest)
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        str(slot.get("slot_id"))
        for slot in payload.get("slots") or []
        if isinstance(slot, dict) and slot.get("user_confirmed")
    }


def standard_worker_manifest(
    *,
    request: StandardWorkerLoopRequest,
    contract_path: Path,
    context_path: Path,
    loop_result: WorkerLoopResult,
    output_dir: Path,
) -> dict[str, Any]:
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
        "status": "ok",
        "evaluation_mode": (
            "agent_capability"
            if loop_result.baseline_source == "agent_generated"
            else "legacy_solver_tuning"
        ),
        "request": {
            "docs": [str(path) for path in request.docs],
            "instance_dir": str(request.instance_dir),
            "pattern": request.pattern,
            "best_known_csv": str(request.best_known_csv) if request.best_known_csv else None,
            "slot_manifest": str(request.slot_manifest) if request.slot_manifest else None,
            "project_intake_manifest": str(request.project_intake_manifest) if request.project_intake_manifest else None,
            "previous_pipeline_memory": str(request.previous_pipeline_memory) if request.previous_pipeline_memory else None,
            "seeds": request.seeds or [0],
            "solver": request.solver,
            "baseline_source": normalize_baseline_source(request.baseline_source),
            "agent_generated_solver_path": request.agent_generated_solver_path,
            "iterations": max(0, request.iterations),
            "apply_worker_changes": bool(request.apply_worker_changes),
            "promotion_repeats": max(1, request.promotion_repeats),
            "in_round_repair_attempts": max(0, request.in_round_repair_attempts),
            "semantic_reviewer": (
                type(request.semantic_reviewer).__name__ if request.semantic_reviewer is not None else None
            ),
            "awls_zi_policy": request.awls_zi_policy,
            "awls_critical_block_exhaustive_pct": max(0, min(100, request.awls_critical_block_exhaustive_pct)),
            "awls_same_machine_eval": request.awls_same_machine_eval,
        },
        "contract_path": str(contract_path),
        "context_packet_path": str(context_path),
        "baseline_key": list(loop_result.baseline_key),
        "baseline_source": loop_result.baseline_source,
        "baseline_generation": loop_result.baseline_generation,
        "final_key": list(loop_result.final_key),
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
