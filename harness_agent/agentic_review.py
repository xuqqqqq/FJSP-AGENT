from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runner import RunSummary
from .solver_quality_contract import build_agent_generated_solver_quality_contract
from .solver_quality_contract import is_agent_generated_solver_context as _contract_agent_generated_context
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
    iteration_contract = context.get("iteration_edit_contract") if isinstance(context, dict) else {}
    is_incremental_iteration = bool(
        isinstance(iteration_contract, dict)
        and iteration_contract.get("mode") == "incremental_after_baseline"
    )

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
            if is_incremental_iteration:
                issues.append("missing_rule_operator_hypotheses")
                suggestions.append(
                    "Every improvement-round code change must declare a concrete rule/operator hypothesis before editing."
                )
            else:
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

    agent_generated_import_risks = _detect_agent_generated_runtime_import_risks(
        context,
        worktree_path,
        worker_result.changed_files,
    )
    if agent_generated_import_risks:
        issues.append("agent_generated_solver_imports_backend_package")
        suggestions.append(
            "Agent-generated solver/helper files under examples must be runnable as standalone scripts; "
            "do not import harness_agent.* from them."
        )
        suggestions.append(
            "Keep small setup/decoder helpers self-contained or move the change back into the existing generated solver file."
        )

    agent_generated_quality_contract = build_agent_generated_solver_quality_contract(context)
    agent_generated_quality_risks = _detect_agent_generated_solver_quality_risks(
        context=context,
        worktree_path=worktree_path,
        changed_files=worker_result.changed_files,
        quality_contract=agent_generated_quality_contract,
    )
    if agent_generated_quality_risks:
        issues.append("agent_generated_solver_quality_contract_missing")
        suggestions.append(
            "Repair the generated solver structure before evaluator execution: derive active variant features from "
            "the IO/requirement context, keep one stable operation identity, and add complete coverage, eligibility, "
            "precedence, non-overlap, and bounded-runtime guards."
        )
        if "sequence_dependent_setup" in agent_generated_quality_contract.get("active_features", []):
            suggestions.append(
                "For setup-aware instances, include setup on same-machine arcs and full-decode sequence/neighborhood "
                "candidates before comparing makespan."
            )
        suggestions.append(
            "If this is an improvement round, preserve the incumbent parser/skeleton and patch the missing capability "
            "rather than replacing the solver with an unrelated implementation."
        )

    agent_generated_self_check_risks = _detect_agent_generated_solver_self_check_risks(
        proposal=proposal if isinstance(proposal, dict) else None,
        worktree_path=worktree_path,
        changed_files=worker_result.changed_files,
        quality_contract=agent_generated_quality_contract,
    )
    if agent_generated_self_check_risks:
        issues.append("agent_generated_solver_self_check_incomplete")
        suggestions.append(
            "Before editing an agent-generated FJSP solver, fill solver_contract_self_check with active features, "
            "implemented required capabilities, concrete code evidence, runtime bounds, decoder evidence, and "
            "incumbent-preservation evidence."
        )

    protected_fact_regressions = _detect_protected_promoted_fact_regressions(
        context,
        proposal if isinstance(proposal, dict) else None,
        worktree_path,
        worker_result.changed_files,
    )
    if protected_fact_regressions:
        issues.append("protected_promoted_fact_regression")
        suggestions.append(
            "Do not remove or disable Core-promoted mechanisms in the next round. "
            "Preserve them or explicitly ablate them with a legality-preserving fallback."
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
        "agent_generated_runtime_import_risks": agent_generated_import_risks,
        "agent_generated_solver_quality_contract": agent_generated_quality_contract,
        "agent_generated_solver_quality_risks": agent_generated_quality_risks,
        "agent_generated_solver_self_check_risks": agent_generated_self_check_risks,
        "protected_promoted_fact_regressions": protected_fact_regressions,
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
    detailed_diagnosis = _rejected_judgment_detail_lines(judgment)
    analysis = ErrorAnalysis(
        needed=True,
        source="code_judgment",
        diagnosis=[
            "The candidate was rejected before evaluator execution because the code judgment found blocking issues.",
            *judgment.issues,
            *detailed_diagnosis,
        ],
        suggestions=judgment.suggestions,
    )
    write_error_analysis_artifacts(output_dir=output_dir, analysis=analysis)
    return analysis


def _rejected_judgment_detail_lines(judgment: AgenticJudgment) -> list[str]:
    checks = judgment.checks or {}
    detail_specs = [
        ("agent_generated_solver_quality_risks", "Agent-generated solver quality risks"),
        ("agent_generated_solver_self_check_risks", "Agent-generated solver self-check risks"),
        ("incomplete_solution_acceptance_risks", "Incomplete solution acceptance risks"),
        ("python_compile_errors", "Python compile errors"),
        ("apply_rejections", "Proposal apply rejections"),
        ("protected_promoted_fact_regressions", "Protected promoted fact regressions"),
    ]
    details: list[str] = []
    for key, label in detail_specs:
        value = checks.get(key)
        if value:
            details.append(f"{label}: {_compact_json_for_diagnosis(value)}")
    quality_contract = checks.get("agent_generated_solver_quality_contract")
    if isinstance(quality_contract, dict) and quality_contract.get("enabled"):
        capabilities = []
        for key in ("required_code_capabilities", "variant_required_code_capabilities"):
            for item in quality_contract.get(key) or []:
                if isinstance(item, str) and item not in capabilities:
                    capabilities.append(item)
        details.append(
            "Expected agent-generated solver contract: "
            + _compact_json_for_diagnosis(
                {
                    "active_features": quality_contract.get("active_features") or [],
                    "capabilities": capabilities,
                }
            )
        )
    return details


def _compact_json_for_diagnosis(value: Any, *, limit: int = 1800) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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


def _detect_agent_generated_runtime_import_risks(
    context: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
) -> list[str]:
    if not _is_agent_generated_solver_context(context):
        return []
    risky: list[str] = []
    for relative in changed_files:
        normalized = relative.replace("\\", "/")
        if not (normalized.startswith("examples/") and normalized.endswith(".py")):
            continue
        path = worktree_path / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"^\s*(from\s+harness_agent(?:\.|\s+import)|import\s+harness_agent(?:\b|\.))", text, re.M):
            risky.append(f"{relative}: imports harness_agent from standalone agent-generated solver runtime")
    return risky


def _detect_agent_generated_solver_quality_risks(
    *,
    context: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
    quality_contract: dict[str, Any],
) -> list[str]:
    if not quality_contract.get("enabled"):
        return []
    changed = [item.replace("\\", "/") for item in changed_files]
    if not any(_is_agent_generated_example_path(path) for path in changed):
        return []

    sources = _agent_generated_solver_sources(worktree_path, changed)
    if not sources:
        return ["agent_generated_solver: no generated solver source was available for quality review"]

    combined_text = "\n\n".join(f"# FILE {path}\n{text}" for path, text in sources.items())
    combined_lower = combined_text.lower()
    risks: list[str] = []
    hardcoded_parser_risks = _detect_hardcoded_agent_generated_parser_risks(combined_text)
    risks.extend(hardcoded_parser_risks)
    missing = _missing_agent_generated_base_capabilities(combined_text)
    if missing:
        risks.append(f"agent_generated_solver: missing base capabilities: {', '.join(missing)}")

    active_features = set(quality_contract.get("active_features") or [])
    if "sequence_dependent_setup" in active_features:
        setup_missing = _missing_setup_aware_capabilities(combined_text)
        if setup_missing:
            risks.append(f"agent_generated_solver: missing setup-aware capabilities: {', '.join(setup_missing)}")

    sequence_move_terms = (
        "local_search",
        "neighborhood",
        "relocate",
        "relocation",
        "insert",
        "insertion",
        "swap",
        "destroy",
        "repair",
        "machine_sequence",
        "machine_sequences",
    )
    if any(term in combined_lower for term in sequence_move_terms):
        move_missing = _missing_sequence_move_capabilities(combined_text)
        if move_missing:
            risks.append(f"agent_generated_solver: sequence/neighborhood move lacks: {', '.join(move_missing)}")

    for feature, terms in _VARIANT_FEATURE_CODE_TERMS.items():
        if feature not in active_features:
            continue
        if not any(term in combined_lower for term in terms):
            risks.append(f"agent_generated_solver: active feature `{feature}` is not reflected in solver code")
    return risks


def _detect_agent_generated_solver_self_check_risks(
    *,
    proposal: dict[str, Any] | None,
    worktree_path: Path,
    changed_files: list[str],
    quality_contract: dict[str, Any],
) -> list[str]:
    if not proposal or not quality_contract.get("enabled"):
        return []
    changed = [item.replace("\\", "/") for item in changed_files]
    if not any(_is_agent_generated_example_path(path) for path in changed):
        return []

    self_check = proposal.get("solver_contract_self_check")
    if not isinstance(self_check, dict) or not self_check.get("present"):
        return ["solver_contract_self_check is missing for an agent-generated FJSP solver edit"]

    risks: list[str] = []
    expected_features = {str(item) for item in quality_contract.get("active_features") or []}
    declared_features = {str(item) for item in self_check.get("active_features") or []}
    missing_features = sorted(expected_features - declared_features)
    if missing_features:
        risks.append(f"solver_contract_self_check missing active_features: {', '.join(missing_features)}")

    expected_capabilities = set(_quality_contract_capabilities_for_review(quality_contract))
    implemented = {
        str(item.get("name"))
        for item in self_check.get("capabilities") or []
        if isinstance(item, dict) and item.get("status") == "implemented"
    }
    missing_capabilities = sorted(expected_capabilities - implemented)
    if missing_capabilities:
        risks.append(
            "solver_contract_self_check missing implemented capabilities: "
            + ", ".join(missing_capabilities)
        )

    evidence_missing = sorted(
        str(item.get("name"))
        for item in self_check.get("capabilities") or []
        if isinstance(item, dict)
        and item.get("status") == "implemented"
        and str(item.get("name")) in expected_capabilities
        and not str(item.get("evidence") or "").strip()
    )
    if evidence_missing:
        risks.append("solver_contract_self_check missing evidence for: " + ", ".join(evidence_missing))

    narrative_fields = [
        ("representation", "representation evidence is missing"),
        ("decoder", "decoder evidence is missing"),
        ("runtime_bounds", "runtime bound evidence is missing"),
        ("incumbent_preservation", "incumbent preservation evidence is missing"),
    ]
    for field, message in narrative_fields:
        if not str(self_check.get(field) or "").strip():
            risks.append(f"solver_contract_self_check {message}")

    if "sequence_dependent_setup" in expected_features and not self_check.get("variant_handling"):
        risks.append("solver_contract_self_check missing variant_handling for sequence_dependent_setup")
    source_evidence_risks = _detect_self_check_evidence_source_mismatches(
        self_check=self_check,
        worktree_path=worktree_path,
        changed_files=changed,
        expected_capabilities=expected_capabilities,
    )
    risks.extend(source_evidence_risks)
    return risks


def _detect_self_check_evidence_source_mismatches(
    *,
    self_check: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
    expected_capabilities: set[str],
) -> list[str]:
    sources = _agent_generated_solver_sources(worktree_path, changed_files)
    if not sources:
        return []
    source_text = "\n".join(sources.values()).lower()
    risks: list[str] = []
    for item in self_check.get("capabilities") or []:
        if not isinstance(item, dict) or item.get("status") != "implemented":
            continue
        capability = str(item.get("name") or "")
        if capability not in expected_capabilities:
            continue
        evidence = str(item.get("evidence") or "")
        code_tokens = [
            token
            for token in _self_check_code_evidence_tokens(evidence)
            if token != capability and token not in expected_capabilities
        ]
        if not code_tokens:
            risks.append(
                f"solver_contract_self_check evidence for {capability} does not cite a concrete code symbol"
            )
            continue
        missing_tokens = [token for token in code_tokens if token.lower() not in source_text]
        if len(missing_tokens) == len(code_tokens):
            risks.append(
                "solver_contract_self_check evidence for "
                f"{capability} does not match generated source symbols: {', '.join(code_tokens[:4])}"
            )
    return risks


def _self_check_code_evidence_tokens(evidence: str) -> list[str]:
    """Extract likely code anchors from self-check prose.

    The goal is not formal proof.  It prevents empty claims such as
    "implemented" or references to imaginary helpers while allowing natural
    evidence like `parse_instance/decode_schedule/improve`.
    """

    if not evidence.strip():
        return []
    raw_tokens = re.findall(r"`([^`]+)`|([A-Za-z_][A-Za-z0-9_]{2,})", evidence)
    tokens = [first or second for first, second in raw_tokens]
    generic = {
        "implemented",
        "implementation",
        "capability",
        "capabilities",
        "function",
        "functions",
        "guard",
        "guards",
        "logic",
        "solver",
        "schedule",
        "schedules",
        "source",
        "code",
        "uses",
        "used",
        "with",
        "where",
        "evidence",
        "active",
        "variant",
        "feature",
        "features",
        "this",
        "that",
        "every",
        "each",
        "path",
        "paths",
        "handled",
        "supported",
        "complete",
    }
    result: list[str] = []
    for token in tokens:
        for part in re.split(r"[/.,:;()\[\]\s]+", token):
            stripped = part.strip("_")
            if len(stripped) < 3:
                continue
            lowered = stripped.lower()
            if lowered in generic:
                continue
            if stripped not in result:
                result.append(stripped)
            if len(result) >= 8:
                return result
    return result


def _quality_contract_capabilities_for_review(quality_contract: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("required_code_capabilities", "variant_required_code_capabilities"):
        for item in quality_contract.get(key) or []:
            if isinstance(item, str) and item not in result:
                result.append(item)
    return result


def _is_agent_generated_example_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if not (normalized.startswith("examples/") and normalized.endswith(".py")):
        return False
    return "agent_generated" in normalized or "generated_fjsp" in normalized


def _agent_generated_solver_sources(worktree_path: Path, changed_files: list[str]) -> dict[str, str]:
    candidates: set[str] = {
        path
        for path in changed_files
        if path.replace("\\", "/").startswith("examples/") and path.endswith(".py")
    }
    examples_dir = worktree_path / "examples"
    if examples_dir.exists():
        for path in examples_dir.glob("agent_generated*.py"):
            candidates.add(path.relative_to(worktree_path).as_posix())
        for path in examples_dir.glob("*generated*fjsp*.py"):
            candidates.add(path.relative_to(worktree_path).as_posix())
    sources: dict[str, str] = {}
    for relative in sorted(candidates):
        path = worktree_path / relative
        try:
            sources[relative.replace("\\", "/")] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return sources


def _missing_agent_generated_base_capabilities(text: str) -> list[str]:
    return [
        name
        for name, detector in [
            ("standalone_cli_interface", _has_standalone_cli_interface),
            ("active_io_parser", _has_active_io_parser),
            ("declared_output_schema", _has_declared_output_schema),
            ("stable_operation_identity", _has_stable_operation_identity),
            ("operation_level_ready_list_constructor", _has_operation_level_ready_list_constructor),
            ("complete_schedule_coverage_guard", _has_operation_coverage_guard),
            ("machine_eligibility_guard", _has_machine_eligibility_guard),
            ("processing_duration_guard", _has_processing_duration_guard),
            ("job_precedence_guard", _has_job_precedence_guard),
            ("machine_non_overlap_guard", _has_machine_non_overlap_guard),
            ("bounded_runtime_or_iteration_guard", _has_bounded_runtime_or_iteration_guard),
            ("incumbent_preservation_on_failed_candidate", _has_incumbent_preservation_guard),
        ]
        if not detector(text)
    ]


def _has_standalone_cli_interface(text: str) -> bool:
    lowered = text.lower()
    return (
        "argparse" in lowered
        and "--input" in lowered
        and "--output" in lowered
        and "--seed" in lowered
        and ("if __name__" in lowered or "def main" in lowered)
    )


def _has_active_io_parser(text: str) -> bool:
    lowered = text.lower()
    parser_terms = ["def parse", "parse_instance", "read_instance", "load_instance"]
    read_terms = ["read_text", "open(", ".read(", "json.load", "split()"]
    if not (any(term in lowered for term in parser_terms) and any(term in lowered for term in read_terms)):
        return False
    if _detect_hardcoded_agent_generated_parser_risks(text):
        return False
    derived_operation_terms = [
        "for job_id in range(job_count",
        "for job_id, job in enumerate",
        "for job in jobs",
        "for op_id in range(op_count",
        "for op_id, op in enumerate",
        "candidate_count",
        "candidates.append",
        "eligible = {",
        "op_info[op_key]",
        "op_info[(job_id, op_id)]",
    ]
    return sum(1 for term in derived_operation_terms if term in lowered) >= 3


def _detect_hardcoded_agent_generated_parser_risks(text: str) -> list[str]:
    """Detect parser-shaped code that reads the file but ignores it for ops.

    Generated solvers sometimes satisfy shallow lexical checks by calling
    `read_text().split()` and then constructing a fixed one-operation
    `op_info`.  That is not an active IO parser; it will pass tiny smoke tests
    only by accident and cannot generalize to the actual instance.
    """

    risks: list[str] = []
    hardcoded_patterns = [
        r"\bop_info\s*=\s*\{\s*\(\s*\d+\s*,\s*\d+\s*\)\s*:",
        r"\bassignment\s*=\s*\{\s*\(\s*\d+\s*,\s*\d+\s*\)\s*:",
        r"\bmachine_sequences?\s*=\s*\{\s*\d+\s*:\s*\[\s*\(\s*\d+\s*,\s*\d+\s*\)",
        r"\bschedule\s*=\s*\[\s*\{\s*['\"]job_id['\"]\s*:\s*\d+",
    ]
    if any(re.search(pattern, text, re.I | re.S) for pattern in hardcoded_patterns):
        risks.append(
            "agent_generated_solver: parser appears to hardcode toy operation metadata instead of deriving all jobs/operations/candidates from active IO"
        )
    return risks


def _has_declared_output_schema(text: str) -> bool:
    lowered = text.lower()
    schema_terms = ["standard_fjsp_schedule_v1", '"schedule"', "'schedule'", "schedule:"]
    field_terms = ["job_id", "op_id", "machine_id", "start", "end"]
    write_terms = ["write_text", "json.dump", "json.dumps", "--output"]
    return (
        any(term in lowered for term in schema_terms)
        and all(term in lowered for term in field_terms)
        and any(term in lowered for term in write_terms)
    )


def _missing_setup_aware_capabilities(text: str) -> list[str]:
    missing: list[str] = []
    if not re.search(r"\bsetup(?:_time)?\b", text, re.I):
        missing.append("setup_time_logic")
    if not re.search(r"\bdecode\w*\s*\(", text, re.I):
        missing.append("setup_aware_decoder")
    setup_arc_terms = [
        r"prev(?:ious)?_.*setup",
        r"setup.*prev(?:ious)?_",
        r"last_.*setup",
        r"setup.*last_",
        r"machine_sequences?",
    ]
    if not any(re.search(pattern, text, re.I | re.S) for pattern in setup_arc_terms):
        missing.append("same_machine_setup_arc")
    return missing


def _missing_sequence_move_capabilities(text: str) -> list[str]:
    missing: list[str] = []
    if not re.search(r"\bassignment\b", text, re.I):
        missing.append("operation_to_machine_assignment")
    if not re.search(r"\bmachine_sequences?\b", text, re.I):
        missing.append("machine_sequences")
    if not re.search(r"\bdecode\w*\s*\(", text, re.I):
        missing.append("full_decoder")
    if not _has_operation_coverage_guard(text):
        missing.append("post_move_coverage_guard")
    if not _has_incumbent_preservation_guard(text):
        missing.append("keep_incumbent_on_failed_move")
    return missing


def _has_stable_operation_identity(text: str) -> bool:
    lowered = text.lower()
    pair_like = (
        "(job_id, op_id)" in lowered
        or "(job, op)" in lowered
        or "op_key" in lowered
        or "operation_key" in lowered
    )
    return pair_like and ("op_info" in lowered or "operations" in lowered or "all_ops" in lowered)


def _has_operation_level_ready_list_constructor(text: str) -> bool:
    lowered = text.lower()
    ready_terms = [
        "ready_ops",
        "ready_operations",
        "ready_candidates",
        "ready_choices",
        "next_op_by_job",
        "next_operation",
        "job_next",
        "remaining_jobs",
    ]
    next_operation_terms = [
        "next_op_by_job[job_id]",
        "job_next[job_id]",
        "next_operation[job_id]",
        "op_id = next",
        "op_id == next",
        "op_id > 0",
    ]
    eligible_machine_terms = [
        "for machine_id, duration in eligible.items()",
        "for machine_id, processing_time in eligible.items()",
        "for machine_id, proc_time in eligible.items()",
        "for machine_id in eligible",
        "for machine_id, duration in candidates",
        "for machine_id, processing_time in candidates",
    ]
    selection_terms = [
        "best_choice",
        "best_candidate",
        "min(",
        "sort(",
        ".sort(",
        "rng.choice",
        "random.choice",
        "shuffle(",
        "seed",
        "multi_start",
        "restarts",
    ]
    state_terms = ["job_ready", "machine_ready", "assignment", "machine_sequences", "schedule.append"]
    return (
        any(term in lowered for term in ready_terms)
        and any(term in lowered for term in next_operation_terms)
        and any(term in lowered for term in eligible_machine_terms)
        and any(term in lowered for term in selection_terms)
        and sum(1 for term in state_terms if term in lowered) >= 3
    )


def _has_operation_coverage_guard(text: str) -> bool:
    lowered = text.lower()
    coverage_terms = [
        "expected_ops",
        "total_ops",
        "operation_count",
        "all_ops",
        "required_ops",
        "seen_ops",
        "missing_ops",
    ]
    duplicate_terms = ["duplicate", "seen_ops", "seen.add", "set(schedule", "len(set("]
    schedule_terms = ["len(schedule)", "len(result)", "len(decoded)", "len(best_schedule)", "len(candidate_schedule)"]
    return (
        any(term in lowered for term in coverage_terms)
        and any(term in lowered for term in schedule_terms)
        and any(term in lowered for term in duplicate_terms)
    )


def _has_machine_eligibility_guard(text: str) -> bool:
    lowered = text.lower()
    eligibility_terms = ["eligible", "candidates", "machine_options", "options", "candidate_machines"]
    machine_terms = ["machine_id", "machine"]
    rejection_terms = ["not in", "continue", "return none", "raise valueerror", "infeasible"]
    return (
        any(term in lowered for term in eligibility_terms)
        and any(term in lowered for term in machine_terms)
        and any(term in lowered for term in rejection_terms)
    )


def _has_processing_duration_guard(text: str) -> bool:
    lowered = text.lower()
    duration_terms = ["duration", "processing_time", "proc_time", "eligible[machine_id]", "options[machine_id]"]
    interval_terms = ["end - start", "start + duration", "start + processing", "start + proc"]
    rejection_terms = ["return none", "raise valueerror", "continue", "assert", "!="]
    return (
        any(term in lowered for term in duration_terms)
        and any(term in lowered for term in interval_terms)
        and any(term in lowered for term in rejection_terms)
    )


def _has_job_precedence_guard(text: str) -> bool:
    lowered = text.lower()
    return (
        ("job_ready" in lowered or "job_end" in lowered or "predecessor" in lowered or "prev_op" in lowered)
        and ("start" in lowered and "end" in lowered)
    )


def _has_machine_non_overlap_guard(text: str) -> bool:
    lowered = text.lower()
    return (
        ("machine_ready" in lowered or "machine_end" in lowered or "machine_available" in lowered or "prev_end" in lowered)
        and ("start" in lowered and "end" in lowered)
    )


def _has_bounded_runtime_or_iteration_guard(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered
        for term in [
            "deadline",
            "time_limit",
            "perf_counter",
            "max_iterations",
            "max_iter",
            "max_trials",
            "max_restarts",
            "restart_count",
            "range(restarts",
        ]
    )


def _has_incumbent_preservation_guard(text: str) -> bool:
    lowered = text.lower()
    keep_terms = [
        "best_schedule",
        "incumbent",
        "current_best",
        "best_candidate",
    ]
    reject_terms = [
        "return none",
        "continue",
        "if candidate is none",
        "if decoded is none",
        "keep",
        "strictly improves",
        "< best_makespan",
        "candidate_makespan <",
    ]
    return any(term in lowered for term in keep_terms) and any(term in lowered for term in reject_terms)


_VARIANT_FEATURE_CODE_TERMS = {
    "no_wait": ["no_wait", "no-wait"],
    "time_lag": ["time_lag", "time lag", "lag_min", "lag_max"],
    "machine_calendar": ["calendar", "availability", "unavailable"],
    "batching": ["batch", "capacity"],
    "transportation": ["transport", "travel"],
    "release_dates": ["release", "release_time"],
    "due_dates": ["due", "tardiness", "lateness"],
    "multi_objective": ["objective", "priority", "weighted"],
}


def _detect_protected_promoted_fact_regressions(
    context: dict[str, Any],
    proposal: dict[str, Any] | None,
    worktree_path: Path,
    changed_files: list[str],
) -> list[str]:
    if not isinstance(proposal, dict):
        return []
    loop_feedback = context.get("loop_feedback") if isinstance(context.get("loop_feedback"), dict) else {}
    facts = loop_feedback.get("protected_promoted_facts") or []
    if not isinstance(facts, list) or not facts:
        return []
    changed = {item.replace("\\", "/") for item in changed_files}
    proposal_text = _proposal_text_for_guard(proposal)
    regressions: list[str] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        target_files = {
            str(path).replace("\\", "/")
            for path in (fact.get("target_files") or [])
            if isinstance(path, str) and path.strip()
        }
        if target_files and changed and not (target_files & changed):
            continue
        if _is_feasibility_protection_fact(fact):
            risks = _detect_incomplete_solution_acceptance_risks(worktree_path, list(changed))
            if risks:
                regressions.append(
                    f"{fact.get('name') or 'promoted_fact'}: proposal reintroduces incomplete-solution risk"
                )
            continue
        keywords = _protected_fact_keywords(fact)
        for keyword in keywords:
            if _proposal_removes_keyword(proposal_text, keyword):
                regressions.append(
                    f"{fact.get('name') or 'promoted_fact'}: proposal appears to remove protected mechanism `{keyword}`"
                )
                break
    return regressions


def _is_feasibility_protection_fact(fact: dict[str, Any]) -> bool:
    text = " ".join(
        str(fact.get(key) or "")
        for key in ("name", "novelty", "expected_effect", "protection_rule")
    ).replace("_", " ").lower()
    signals = ("incomplete", "partial", "empty", "coverage", "zero")
    return ("schedule" in text or "solution" in text or "operation" in text) and any(
        signal in text for signal in signals
    )


def _proposal_text_for_guard(proposal: dict[str, Any]) -> str:
    parts = [
        str(proposal.get("summary") or ""),
        str(proposal.get("strategy_intent") or ""),
    ]
    for hypothesis in proposal.get("rule_operator_hypotheses") or []:
        if not isinstance(hypothesis, dict):
            continue
        parts.extend(
            [
                str(hypothesis.get("name") or ""),
                str(hypothesis.get("type") or ""),
                str(hypothesis.get("novelty") or ""),
                str(hypothesis.get("expected_effect") or ""),
            ]
        )
    for change in proposal.get("changes") or []:
        if not isinstance(change, dict):
            continue
        parts.extend(
            [
                str(change.get("path") or ""),
                str(change.get("action") or ""),
                str(change.get("old") or ""),
                str(change.get("new") or ""),
                str(change.get("anchor") or ""),
                str(change.get("content") or ""),
                str(change.get("rationale") or ""),
            ]
        )
    return "\n".join(parts).replace("_", " ").lower()


def _protected_fact_keywords(fact: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(fact.get(key) or "")
        for key in ("name", "type", "novelty", "expected_effect", "protection_rule")
    ).replace("_", " ")
    stopwords = {
        "promoted",
        "mechanism",
        "proposal",
        "solver",
        "target",
        "files",
        "round",
        "effect",
        "repair",
        "rule",
        "dispatch",
        "policy",
        "schedule",
        "schedules",
        "greedy",
        "operation",
        "operations",
        "machine",
        "machines",
        "candidate",
        "conditional",
        "fallback",
        "previous",
        "attempt",
        "directly",
        "scored",
        "zero",
        "evaluation",
        "preserve",
    }
    keywords: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{5,}", text.lower()):
        normalized = token.strip("-")
        if normalized and normalized not in stopwords and normalized not in keywords:
            keywords.append(normalized)
    return keywords[:12]


def _proposal_removes_keyword(proposal_text: str, keyword: str) -> bool:
    escaped = re.escape(keyword)
    removal_verbs = r"(remove|removing|removed|delete|deleting|deleted|eliminate|eliminating|disable|disabling|discard|discarding|drop|dropping|strip|stripping|undo|revert)"
    return bool(
        re.search(rf"\b{removal_verbs}\b[\s\S]{{0,120}}\b{escaped}\b", proposal_text)
        or re.search(rf"\b{escaped}\b[\s\S]{{0,120}}\b{removal_verbs}\b", proposal_text)
    )


def _is_agent_generated_solver_context(context: dict[str, Any]) -> bool:
    return _contract_agent_generated_context(context)


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
