from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TaskContract


@dataclass(frozen=True)
class IntentAlignmentRequest:
    """Request for producing a reviewable optimization intent card."""

    contract_path: Path
    output_dir: Path
    project_root: Path
    health_manifest_path: Path | None = None
    benchmark_source: str = "user_provided"
    allow_draft: bool = False
    require_health: bool = True


def write_intent_alignment(request: IntentAlignmentRequest) -> dict[str, Any]:
    """Write an intent-alignment card from the contract and preflight evidence."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = TaskContract.load(request.contract_path)
    contract_errors = contract.validate(request.project_root)
    health_manifest = load_optional_json(request.health_manifest_path)
    readiness = readiness_summary(
        contract=contract,
        contract_errors=contract_errors,
        health_manifest=health_manifest,
        allow_draft=request.allow_draft,
        require_health=request.require_health,
    )
    manifest_path = output_dir / "intent_alignment_manifest.json"
    report_path = output_dir / "intent_alignment_report.md"
    manifest = {
        "status": readiness["status"],
        "ready_for_optimization": readiness["ready_for_optimization"],
        "blockers": readiness["blockers"],
        "warnings": readiness["warnings"],
        "contract_path": str(request.contract_path.resolve()),
        "health_manifest_path": str(request.health_manifest_path.resolve()) if request.health_manifest_path else None,
        "benchmark_source": request.benchmark_source,
        "task": task_summary(contract),
        "objectives": objective_summary(contract),
        "constraints": constraint_summary(contract),
        "commands": command_summary(contract),
        "budget": budget_summary(contract),
        "risk": risk_summary(contract, benchmark_source=request.benchmark_source, health_manifest=health_manifest),
        "review": review_summary(contract),
        "health": health_summary(health_manifest),
        "artifacts": {
            "manifest": str(manifest_path.resolve()),
            "report": str(report_path.resolve()),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_intent_alignment_report(manifest), encoding="utf-8")
    return manifest


def load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def readiness_summary(
    *,
    contract: TaskContract,
    contract_errors: list[str],
    health_manifest: dict[str, Any] | None,
    allow_draft: bool,
    require_health: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if contract_errors:
        blockers.extend(f"contract: {error}" for error in contract_errors)
    if contract.requires_human_confirmation and not allow_draft:
        blockers.append("contract review status requires human confirmation")
    if require_health and not health_manifest:
        blockers.append("health-check manifest is required before formal optimization")
    if health_manifest and health_manifest.get("status") != "ok":
        blockers.append(f"health-check status is {health_manifest.get('status')}")
    if not require_health and not health_manifest:
        warnings.append("health-check evidence is missing; intent card is provisional")
    if len(contract.instances) <= 1:
        warnings.append("only one instance is configured; overfitting risk is high")
    status = "ready" if not blockers else "blocked"
    return {
        "status": status,
        "ready_for_optimization": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def task_summary(contract: TaskContract) -> dict[str, Any]:
    return {
        "task_id": contract.task_id,
        "problem_family": contract.problem_family,
        "description": contract.description,
        "instance_count": len(contract.instances),
        "instances": [{"id": item.id, "path": str(item.path)} for item in contract.instances],
    }


def objective_summary(contract: TaskContract) -> list[dict[str, Any]]:
    return [
        {
            "name": objective.name,
            "direction": objective.direction,
            "priority": objective.priority,
            "invalid_if_missing": objective.invalid_if_missing,
            "threshold": objective.threshold,
            "role": "primary" if index == 0 else "secondary",
        }
        for index, objective in enumerate(sorted(contract.objectives, key=lambda item: item.priority))
    ]


def constraint_summary(contract: TaskContract) -> dict[str, Any]:
    hard_constraints = [
        "solver command must create a solution artifact",
        "evaluator command must create a metrics artifact",
        "all required objective metrics must be present and numeric",
        "invalid evaluator output cannot be promoted",
    ]
    if contract.commands.quick_test:
        hard_constraints.append("quick test must pass before optimization")
    return {
        "hard": hard_constraints,
        "path_policy": {
            "allowed_paths": contract.paths.allowed_paths,
            "forbidden_paths": contract.paths.forbidden_paths,
        },
        "resources": {name: str(path) for name, path in contract.resources.items()},
    }


def command_summary(contract: TaskContract) -> dict[str, Any]:
    return {
        "solver": contract.commands.solver,
        "evaluator": contract.commands.evaluator,
        "quick_test": contract.commands.quick_test,
    }


def budget_summary(contract: TaskContract) -> dict[str, Any]:
    planned_runs = contract.budget.rounds * len(contract.instances) * len(contract.budget.seeds)
    return {
        "rounds": contract.budget.rounds,
        "seeds": contract.budget.seeds,
        "timeout_seconds": contract.budget.timeout_seconds,
        "max_workers": contract.budget.max_workers,
        "planned_evaluator_runs": planned_runs,
        "worst_case_timeout_seconds": planned_runs * contract.budget.timeout_seconds,
    }


def risk_summary(
    contract: TaskContract,
    *,
    benchmark_source: str,
    health_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    overfitting_risk = "high" if len(contract.instances) <= 1 else "medium" if len(contract.instances) < 5 else "lower"
    stability = "unknown"
    if health_manifest:
        probe = health_manifest.get("stability_probe") or {}
        stability = "stable" if probe.get("stable") else "unstable"
    return {
        "benchmark_source": benchmark_source,
        "overfitting_risk": overfitting_risk,
        "benchmark_stability": stability,
        "notes": risk_notes(overfitting_risk=overfitting_risk, benchmark_source=benchmark_source, stability=stability),
    }


def risk_notes(*, overfitting_risk: str, benchmark_source: str, stability: str) -> list[str]:
    notes: list[str] = []
    if benchmark_source != "user_provided":
        notes.append("generated or inferred benchmark evidence must be confirmed before formal use")
    if overfitting_risk == "high":
        notes.append("quality claims should be limited to the configured benchmark until more instances are added")
    if stability != "stable":
        notes.append("benchmark stability is not proven; improvement thresholds may be unreliable")
    return notes


def review_summary(contract: TaskContract) -> dict[str, Any]:
    return {
        "status": contract.review_status,
        "requires_human_confirmation": contract.requires_human_confirmation,
        "uncertain_fields": list(contract.review.get("uncertain_fields") or []),
        "confirmation_checklist": list(contract.review.get("confirmation_checklist") or []),
    }


def health_summary(health_manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not health_manifest:
        return {"available": False}
    probe = health_manifest.get("stability_probe") or {}
    quick_test = health_manifest.get("quick_test") or {}
    return {
        "available": True,
        "status": health_manifest.get("status"),
        "quick_test_status": quick_test.get("status"),
        "stability_status": probe.get("status"),
        "stable": probe.get("stable"),
        "valid": probe.get("valid"),
        "total": probe.get("total"),
        "groups": probe.get("groups") or [],
    }


def render_intent_alignment_report(manifest: dict[str, Any]) -> str:
    task = manifest.get("task") or {}
    budget = manifest.get("budget") or {}
    risk = manifest.get("risk") or {}
    lines = [
        "# Intent Alignment Summary",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Ready for optimization: `{manifest.get('ready_for_optimization')}`",
        f"- Task: `{task.get('task_id')}`",
        f"- Problem family: `{task.get('problem_family')}`",
        f"- Benchmark source: `{manifest.get('benchmark_source')}`",
        f"- Instance count: `{task.get('instance_count')}`",
        f"- Planned evaluator runs: `{budget.get('planned_evaluator_runs')}`",
        f"- Overfitting risk: `{risk.get('overfitting_risk')}`",
        f"- Benchmark stability: `{risk.get('benchmark_stability')}`",
        "",
        "## Blockers And Warnings",
        "",
        f"- Blockers: `{json.dumps(manifest.get('blockers') or [], ensure_ascii=False)}`",
        f"- Warnings: `{json.dumps(manifest.get('warnings') or [], ensure_ascii=False)}`",
        f"- Risk notes: `{json.dumps(risk.get('notes') or [], ensure_ascii=False)}`",
        "",
        "## Objectives",
        "",
        "| Role | Name | Direction | Priority | Required | Threshold |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for objective in manifest.get("objectives") or []:
        lines.append(
            f"| {objective.get('role')} | {objective.get('name')} | {objective.get('direction')} | "
            f"{objective.get('priority')} | {objective.get('invalid_if_missing')} | {objective.get('threshold')} |"
        )
    lines.extend(
        [
            "",
            "## Constraints",
            "",
            f"```json\n{json.dumps(manifest.get('constraints') or {}, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Commands",
            "",
            f"```json\n{json.dumps(manifest.get('commands') or {}, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Health Evidence",
            "",
            f"```json\n{json.dumps(manifest.get('health') or {}, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Review",
            "",
            f"```json\n{json.dumps(manifest.get('review') or {}, ensure_ascii=False, indent=2)}\n```",
        ]
    )
    return "\n".join(lines).strip() + "\n"
