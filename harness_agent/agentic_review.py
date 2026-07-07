from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runner import RunSummary
from .worker import WorkerResult


@dataclass(frozen=True)
class AgenticJudgment:
    """Machine-readable JA result for one generated code proposal.

    The judgment is a pre-execution review.  It reduces avoidable evaluator
    runs, but it never promotes a candidate; promotion remains tied to the fixed
    evaluator metrics.
    """

    accepted: bool
    right: bool
    stage: str
    issues: list[str]
    suggestions: list[str]
    checks: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "right": self.right,
            "stage": self.stage,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "checks": self.checks,
        }


@dataclass(frozen=True)
class ErrorAnalysis:
    """EAA result that converts execution or evaluation failure into repair guidance."""

    needed: bool
    source: str
    diagnosis: list[str]
    suggestions: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "needed": self.needed,
            "source": self.source,
            "diagnosis": self.diagnosis,
            "suggestions": self.suggestions,
        }


def judge_worker_result(
    *,
    worker_result: WorkerResult,
    worktree_path: Path,
    context_packet_path: Path,
    output_dir: Path,
    apply_worker_changes: bool,
) -> AgenticJudgment:
    """Run a deterministic code JA pass over a worker proposal and changed files."""

    context = _load_json(context_packet_path)
    proposal = _load_proposal(worker_result)
    proposal_audit = proposal.get("proposal_audit") if isinstance(proposal, dict) else {}
    if not isinstance(proposal_audit, dict):
        proposal_audit = {}

    issues: list[str] = []
    suggestions: list[str] = []
    warnings: list[str] = []

    if worker_result.status in {"unavailable", "skipped", "failed"}:
        issues.append(f"worker_status_not_usable: {worker_result.status}")
        suggestions.append("Configure or repair the coding worker before running evaluator-backed evolution.")

    if apply_worker_changes and not worker_result.changed_files:
        issues.append("no_changed_files_after_apply")
        suggestions.append("Return at least one accepted code change or explicitly run in proposal-only mode.")

    if isinstance(proposal, dict):
        rejected_changes = proposal.get("rejected_changes") or []
        accepted_changes = proposal.get("changes") or []
        risk_notes = _proposal_risk_notes(proposal)
        if rejected_changes and not proposal.get("changes"):
            issues.append("all_proposed_changes_rejected")
            suggestions.append("Revise the proposal so edits use supported actions, include full content, and stay under allowed paths.")
        elif rejected_changes:
            warnings.append("some_proposed_changes_rejected")
        elif (
            apply_worker_changes
            and not worker_result.changed_files
            and not accepted_changes
            and not risk_notes
            and proposal_audit.get("slot_id")
        ):
            issues.append("empty_slot_proposal_without_risk_note")
            suggestions.append(
                "Return exactly one safe replace_slot_block edit, or include risk_notes explaining why no slot edit is safe."
            )

        hypotheses = proposal.get("rule_operator_hypotheses") or []
        if worker_result.changed_files and not hypotheses:
            warnings.append("changed_code_without_rule_operator_hypothesis")

        audit_warnings = proposal_audit.get("warnings") or []
        if isinstance(audit_warnings, list):
            warnings.extend(str(item) for item in audit_warnings)
        apply_rejections = proposal.get("apply_rejections") or []
        if isinstance(apply_rejections, list) and apply_rejections:
            issues.append("proposal_apply_rejections")
            suggestions.append(
                "Repair proposal anchors/text_replace blocks before evaluation; partial application can leave the solver entrypoint unchanged."
            )

        changed_validators = proposal_audit.get("changed_validator_files") or []
        changed_benchmarks = proposal_audit.get("changed_benchmark_files") or []
        if changed_validators:
            issues.append("proposal_touches_validator_candidates")
            suggestions.append("Keep validators and evaluator semantics fixed; move algorithm changes into solver files.")
        if changed_benchmarks:
            issues.append("proposal_touches_benchmark_candidates")
            suggestions.append("Do not edit benchmark or instance data during algorithm evolution.")

    elif worker_result.artifacts and worker_result.artifacts.get("proposal"):
        issues.append("proposal_artifact_unreadable")
        suggestions.append("Repair the worker output into parseable proposal JSON before applying code.")

    py_compile_errors = _compile_changed_python_files(worktree_path, worker_result.changed_files)
    for path, error in py_compile_errors.items():
        issues.append(f"python_syntax_error: {path}: {error}")
    if py_compile_errors:
        suggestions.append("Fix Python syntax errors in changed files before running the evaluator.")

    parser_rewrite_files = _detect_standard_parser_rewrites(worktree_path, worker_result.changed_files)
    if parser_rewrite_files:
        issues.append("standard_fjsp_parser_reimplementation_detected")
        suggestions.append(
            "For standard FJSP runs, reuse harness_agent.standard_fjsp parsing instead of reimplementing machine-index parsing."
        )

    incomplete_solution_risks = _detect_incomplete_solution_acceptance_risks(worktree_path, worker_result.changed_files)
    if incomplete_solution_risks:
        issues.append("incomplete_solution_acceptance_risk")
        suggestions.append(
            "Local search or neighborhood decoders must reject incomplete candidate solutions before comparing objective values."
        )
        suggestions.append(
            "Do not treat empty or partial schedules/routes as zero-cost improvements; require full operation coverage before acceptance."
        )

    checks = {
        "worker_status": worker_result.status,
        "apply_worker_changes": apply_worker_changes,
        "changed_files": worker_result.changed_files,
        "proposal_present": isinstance(proposal, dict),
        "proposal_audit_warnings": warnings,
        "apply_rejections": proposal.get("apply_rejections") if isinstance(proposal, dict) else None,
        "edit_policy": context.get("edit_policy") or {},
        "python_compile_errors": py_compile_errors,
        "parser_rewrite_files": parser_rewrite_files,
        "incomplete_solution_acceptance_risks": incomplete_solution_risks,
    }
    judgment = AgenticJudgment(
        accepted=not issues,
        right=not issues,
        stage="code_generation",
        issues=issues,
        suggestions=suggestions or ["Proceed to evaluator; no blocking pre-execution issue was found."],
        checks=checks,
    )
    write_judgment_artifacts(output_dir=output_dir, judgment=judgment)
    return judgment


def analyze_rejected_judgment(*, judgment: AgenticJudgment, output_dir: Path) -> ErrorAnalysis:
    analysis = ErrorAnalysis(
        needed=True,
        source="code_judgment",
        diagnosis=[
            "The candidate was rejected before evaluator execution because the code judgment found blocking issues.",
            *judgment.issues,
        ],
        suggestions=judgment.suggestions,
    )
    write_error_analysis_artifacts(output_dir=output_dir, analysis=analysis)
    return analysis


def analyze_run_summary(*, summary: RunSummary, output_dir: Path) -> ErrorAnalysis | None:
    if summary.total > 0 and summary.valid == summary.total:
        return None

    diagnosis: list[str] = []
    suggestions: list[str] = []
    if summary.total == 0:
        diagnosis.append("No evaluator experiments were executed.")
        suggestions.append("Check whether the candidate was rejected before execution or whether the task contract planned no runs.")
    if summary.failed:
        diagnosis.append(f"{summary.failed} of {summary.total} evaluator experiments failed or were invalid.")
        suggestions.append("Inspect solver/evaluator stderr artifacts and revise the solver around the first repeated failure class.")
    if summary.valid == 0 and summary.total > 0:
        diagnosis.append("No valid solution was produced by the candidate.")
        suggestions.append("Prioritize feasibility repair before objective improvement.")
    validation_summary = summary.validation_summary or {}
    if validation_summary:
        diagnosis.append(f"Validation summary: {json.dumps(validation_summary, ensure_ascii=False)}")
    if not summary.best_candidate_metrics:
        suggestions.append("Ensure the evaluator writes all objective metrics required by the task contract.")

    analysis = ErrorAnalysis(
        needed=True,
        source="harness_evaluator",
        diagnosis=diagnosis or ["Evaluator did not produce a fully successful run."],
        suggestions=suggestions or ["Use evaluator artifacts as ground truth for the next repair proposal."],
    )
    write_error_analysis_artifacts(output_dir=output_dir, analysis=analysis)
    return analysis


def write_judgment_artifacts(*, output_dir: Path, judgment: AgenticJudgment) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = output_dir / "agentic_judgment.json"
    markdown_path = output_dir / "agentic_judgment.md"
    payload_path.write_text(json.dumps(judgment.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_judgment_markdown(judgment), encoding="utf-8")
    return payload_path, markdown_path


def write_error_analysis_artifacts(*, output_dir: Path, analysis: ErrorAnalysis) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = output_dir / "agentic_error_analysis.json"
    markdown_path = output_dir / "agentic_error_analysis.md"
    payload_path.write_text(json.dumps(analysis.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_error_analysis_markdown(analysis), encoding="utf-8")
    return payload_path, markdown_path


def render_judgment_markdown(judgment: AgenticJudgment) -> str:
    lines = [
        "# Agentic Judgment",
        "",
        f"- Stage: `{judgment.stage}`",
        f"- Accepted: `{judgment.accepted}`",
        f"- Right: `{judgment.right}`",
        "",
        "## Issues",
        "",
    ]
    lines.extend(f"- {item}" for item in (judgment.issues or ["None"]))
    lines.extend(["", "## Suggestions", ""])
    lines.extend(f"- {item}" for item in judgment.suggestions)
    lines.extend(["", "## Checks", "", f"```json\n{json.dumps(judgment.checks, ensure_ascii=False, indent=2)}\n```"])
    return "\n".join(lines) + "\n"


def render_error_analysis_markdown(analysis: ErrorAnalysis) -> str:
    lines = [
        "# Agentic Error Analysis",
        "",
        f"- Needed: `{analysis.needed}`",
        f"- Source: `{analysis.source}`",
        "",
        "## Diagnosis",
        "",
    ]
    lines.extend(f"- {item}" for item in analysis.diagnosis)
    lines.extend(["", "## Repair Suggestions", ""])
    lines.extend(f"- {item}" for item in analysis.suggestions)
    return "\n".join(lines) + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_proposal(worker_result: WorkerResult) -> dict[str, Any] | None:
    proposal_path = (worker_result.artifacts or {}).get("proposal")
    if not proposal_path:
        return None
    try:
        payload = json.loads(Path(proposal_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _proposal_risk_notes(proposal: dict[str, Any]) -> list[str]:
    notes = proposal.get("risk_notes") or []
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        return []
    return [str(item).strip() for item in notes if str(item).strip()]


def _compile_changed_python_files(worktree_path: Path, changed_files: list[str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for relative in changed_files:
        if not relative.endswith(".py"):
            continue
        path = worktree_path / relative
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except Exception as exc:  # noqa: BLE001 - JA reports syntax/read errors as review facts.
            errors[relative] = str(exc)
    return errors


def _detect_standard_parser_rewrites(worktree_path: Path, changed_files: list[str]) -> list[str]:
    suspicious: list[str] = []
    for relative in changed_files:
        normalized = relative.replace("\\", "/")
        if not (normalized.startswith("examples/standard_fjsp") and normalized.endswith(".py")):
            continue
        path = worktree_path / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        has_custom_parser = "def parse" in text or "def read" in text and "instance" in text
        uses_standard_parser = "parse_standard_fjsp" in text or "harness_agent.standard_fjsp" in text
        if has_custom_parser and not uses_standard_parser:
            suspicious.append(relative)
    return suspicious


def _detect_incomplete_solution_acceptance_risks(worktree_path: Path, changed_files: list[str]) -> list[str]:
    risky: list[str] = []
    for relative in changed_files:
        normalized = relative.replace("\\", "/")
        if not normalized.endswith(".py"):
            continue
        path = worktree_path / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        detected = _incomplete_solution_risk_reasons(text)
        if detected:
            risky.append(f"{relative}: {', '.join(detected)}")
    return risky


def _incomplete_solution_risk_reasons(text: str) -> list[str]:
    compact = " ".join(text.split())
    reasons: list[str] = []
    if "_schedule" in text or "schedule" in text or "route" in text or "solution" in text:
        if " if new_schedule else 0" in compact or " if schedule else 0" in compact:
            reasons.append("empty_schedule_scored_as_zero")
        if " if best_schedule else 0" in compact or " if candidate_schedule else 0" in compact:
            reasons.append("empty_candidate_scored_as_zero")
        if "if best_machine is None:" in text and "break" in text and "return schedule" in text:
            if not _has_explicit_completion_guard(text):
                reasons.append("decoder_can_return_partial_schedule")
    return reasons


def _has_explicit_completion_guard(text: str) -> bool:
    coverage_terms = [
        "len(schedule) == total_ops",
        "len(schedule) != total_ops",
        "len(new_schedule) == total_ops",
        "len(new_schedule) != total_ops",
        "expected_ops",
        "operation_count",
    ]
    invalid_terms = ["return None", "raise ValueError", "continue", "float('inf')", "math.inf"]
    return any(term in text for term in coverage_terms) and any(term in text for term in invalid_terms)
