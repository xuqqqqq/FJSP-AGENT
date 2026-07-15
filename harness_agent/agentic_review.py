from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runner import RunSummary
from .solver_quality_contract import build_agent_generated_solver_quality_contract
from .solver_quality_contract import is_agent_generated_solver_context as _contract_agent_generated_context
from .context_loader import try_load_context_dict
from .source_reachability import function_call_count
from .source_reachability import function_is_reachable_from_entry
from .source_reachability import unreachable_defined_function_helpers
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

    context = try_load_context_dict(context_packet_path)
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

    unusable_worker_statuses = {
        "unavailable",
        "skipped",
        "failed",
        "failed_runtime",
        "authorization_required",
    }
    if worker_result.status in unusable_worker_statuses:
        issues.append(f"worker_status_not_usable: {worker_result.status}")
        suggestions.append("Configure or repair the coding worker before running evaluator-backed evolution.")
    elif worker_result.status == "timeout":
        if worker_result.changed_files:
            warnings.append("worker_timeout_after_code_change")
            suggestions.append(
                "The coding process timed out after producing a diff; rely on deterministic checks and the fixed evaluator, not the timeout alone."
            )
        else:
            issues.append("worker_status_not_usable: timeout")
            suggestions.append("Increase the worker budget or reduce the requested edit before retrying.")

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
        proposal=proposal if isinstance(proposal, dict) else None,
    )
    agent_generated_blocking_quality_risks = _blocking_agent_generated_quality_risks(
        agent_generated_quality_risks
    )
    agent_generated_method_stage = _agent_generated_solver_method_stage(
        context=context,
        worktree_path=worktree_path,
        changed_files=worker_result.changed_files,
        quality_contract=agent_generated_quality_contract,
        proposal=proposal if isinstance(proposal, dict) else None,
    )
    agent_generated_repair_plan = _agent_generated_solver_repair_plan(
        quality_risks=agent_generated_blocking_quality_risks,
        method_stage=agent_generated_method_stage,
    )
    if agent_generated_blocking_quality_risks:
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
        suggestions.append(
            "For standard-FJSP local-search claims such as AWLS, N7/N8, NK, k-insertion, or tabu search, "
            "the generated solver must expose an executable method skeleton: stable operation keys, "
            "assignment plus machine_sequences, a progress/topological decoder, bounded neighbor generation, "
            "decoded-candidate rejection, and best-incumbent preservation."
        )
        if any("structured_neighborhood_claim_unimplemented" in item for item in agent_generated_blocking_quality_risks):
            suggestions.append(
                "For the next same-round repair, do not repeat the AWLS/critical-block/N7/N8/NK/k-insertion/tabu "
                "claim unless the patch actually adds the missing state/decode/move guards named in the risk. "
                "First migrate the incumbent to assignment + machine_sequences + progress decoder, then add the "
                "claimed neighborhood; otherwise remove the strong-neighborhood wording and submit a smaller honest "
                "operator for evaluator comparison."
            )
        if any("shallow_local_search_operator" in item for item in agent_generated_blocking_quality_risks):
            suggestions.append(
                "Do not repair this by adding another random operation-to-machine reassignment loop. "
                "Patch the existing incumbent with critical-path or critical-block move selection, "
                "N8/k-insertion candidate generation, tabu or aspiration memory, and a bounded perturbation path."
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
        "agent_generated_solver_blocking_quality_risks": agent_generated_blocking_quality_risks,
        "agent_generated_solver_method_stage": {
            **agent_generated_method_stage,
            "authoritative": False,
            "evidence_basis": "lexical_static_hint",
        }
        if agent_generated_method_stage
        else {},
        "agent_generated_solver_repair_plan": agent_generated_repair_plan,
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


def _blocking_agent_generated_quality_risks(risks: list[str]) -> list[str]:
    """Keep only deterministic hazards in the pre-evaluator JA gate.

    Missing expected identifiers, source shapes, or named-method skeletons are
    not legality facts.  They remain visible as diagnostics and are resolved by
    the fixed evaluator plus the post-evaluator semantic reviewer.
    """

    hard_fragments = (
        "hardcode",
        "parser assumes one physical operation line",
        "job_precedence_guard_mismatch",
        "failed_move_mutates_current_without_rollback",
    )
    return [risk for risk in risks if any(fragment in risk for fragment in hard_fragments)]


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
    proposal: dict[str, Any] | None = None,
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
    proposal_text = _agent_generated_method_claim_text(proposal)
    combined_lower = combined_text.lower()
    risks: list[str] = []
    hardcoded_parser_risks = _detect_hardcoded_agent_generated_parser_risks(combined_text)
    risks.extend(hardcoded_parser_risks)
    if _is_standard_fjsp_without_setup_context(context, quality_contract):
        risks.extend(_detect_standard_fjsp_packed_line_parser_risks(combined_text))
    risks.extend(_detect_agent_generated_output_schema_mismatch_risks(combined_text))
    risks.extend(_detect_machine_major_decoder_precedence_risks(combined_text))
    risks.extend(_detect_agent_generated_dead_function_risks(combined_text))
    missing = _missing_agent_generated_base_capabilities(combined_text)
    if missing:
        risks.append(f"agent_generated_solver: missing base capabilities: {', '.join(missing)}")
        if "operation_level_ready_list_constructor" in missing:
            risks.extend(_detect_random_ready_machine_selection_risks(combined_text))

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
        if _has_failed_in_place_move_without_rollback(combined_lower):
            risks.append(
                "agent_generated_solver: failed_move_mutates_current_without_rollback: "
                "apply neighborhood moves to a clone/snapshot and commit only after full decode succeeds"
            )

    if _is_standard_fjsp_without_setup_context(context, quality_contract):
        if _is_incremental_after_baseline_context(context):
            risks.extend(
                _detect_shallow_standard_fjsp_local_search_risks(
                    combined_text,
                    proposal_text=proposal_text,
                )
            )
        risks.extend(
            _detect_structured_standard_fjsp_neighborhood_claim_risks(
                combined_text,
                proposal_text=proposal_text,
            )
        )

    for feature, terms in _VARIANT_FEATURE_CODE_TERMS.items():
        if feature not in active_features:
            continue
        if not any(term in combined_lower for term in terms):
            risks.append(f"agent_generated_solver: active feature `{feature}` is not reflected in solver code")
    return risks


def _agent_generated_solver_method_stage(
    *,
    context: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
    quality_contract: dict[str, Any],
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not quality_contract.get("enabled"):
        return {}
    changed = [item.replace("\\", "/") for item in changed_files]
    if not any(_is_agent_generated_example_path(path) for path in changed):
        return {}
    sources = _agent_generated_solver_sources(worktree_path, changed)
    if not sources:
        return {
            "schema_version": 1,
            "stage_index": 0,
            "stage_name": "stage_0_contract_repair_required",
            "missing_for_next_stage": ["agent_generated_solver_source"],
        }
    combined_text = "\n\n".join(f"# FILE {path}\n{text}" for path, text in sources.items())
    proposal_text = _agent_generated_method_claim_text(proposal)
    return _method_stage_snapshot(
        combined_text,
        quality_contract=quality_contract,
        proposal_text=proposal_text,
        standard_fjsp_without_setup=_is_standard_fjsp_without_setup_context(context, quality_contract),
    )


def _method_stage_snapshot(
    text: str,
    *,
    quality_contract: dict[str, Any],
    proposal_text: str,
    standard_fjsp_without_setup: bool,
) -> dict[str, Any]:
    lowered = text.lower()
    base_missing = _missing_agent_generated_base_capabilities(text)
    has_assignment = bool(re.search(r"\bassignment\b|\bon_machine\b", lowered))
    has_machine_sequences = _has_machine_sequence_state_shape(lowered)
    has_progress_decoder = _has_machine_sequence_decoder_shape(lowered)
    has_move_application = _has_move_application_shape(lowered)
    has_coverage_guard = _has_operation_coverage_guard(text)
    has_candidate_rejection = _has_decoded_candidate_rejection_shape(lowered)
    has_transactional_move = _has_transactional_candidate_application_shape(lowered)
    has_critical_blocks = _has_critical_block_extraction_shape(lowered)
    has_n8_or_k_insertion = _has_n8_or_k_insertion_generation_shape(lowered)
    has_tabu_or_perturbation = bool(re.search(r"\btabu(?:_until|_list|_tenure|_memory)?\b|\bperturb", lowered))
    claimed_terms = (
        _claimed_structured_neighborhood_terms(f"{lowered}\n{proposal_text.lower()}")
        if standard_fjsp_without_setup
        else []
    )

    evidence = {
        "base_missing": base_missing,
        "has_assignment": has_assignment,
        "has_machine_sequences": has_machine_sequences,
        "has_progress_decoder": has_progress_decoder,
        "has_move_application": has_move_application,
        "has_coverage_guard": has_coverage_guard,
        "has_decoded_candidate_rejection": has_candidate_rejection,
        "has_transactional_candidate_application": has_transactional_move,
        "has_critical_block_extraction": has_critical_blocks,
        "has_n8_or_k_insertion_generation": has_n8_or_k_insertion,
        "has_tabu_or_perturbation_memory": has_tabu_or_perturbation,
        "claimed_structured_neighborhood_terms": claimed_terms,
        "active_features": (quality_contract.get("active_features") or [])[:16],
    }
    if base_missing:
        stage_index = 0
        stage_name = "stage_0_contract_repair_required"
        missing_for_next = base_missing[:8]
    elif not (has_assignment and has_machine_sequences):
        stage_index = 1
        stage_name = "stage_1_legal_constructor_without_sequence_state"
        missing_for_next = [
            item
            for item, present in (
                ("operation_to_machine_assignment", has_assignment),
                ("machine_sequences", has_machine_sequences),
            )
            if not present
        ]
    elif not has_progress_decoder:
        stage_index = 2
        stage_name = "stage_2_sequence_state_without_progress_decoder"
        missing_for_next = ["progress_or_topological_decoder"]
    elif not (has_move_application and has_coverage_guard and has_candidate_rejection):
        stage_index = 3
        stage_name = "stage_3_progress_decoder_without_guarded_moves"
        missing_for_next = [
            item
            for item, present in (
                ("apply_move_on_assignment_and_machine_sequences", has_move_application),
                ("post_move_coverage_guard", has_coverage_guard),
                ("decoded_candidate_rejection", has_candidate_rejection),
            )
            if not present
        ]
    elif not (has_critical_blocks and has_n8_or_k_insertion):
        stage_index = 4
        stage_name = "stage_4_basic_sequence_moves_without_structured_neighborhood"
        missing_for_next = [
            item
            for item, present in (
                ("critical_block_extraction", has_critical_blocks),
                ("n8_or_k_insertion_neighbor_generation", has_n8_or_k_insertion),
            )
            if not present
        ]
    else:
        stage_index = 5
        stage_name = "stage_5_structured_neighborhood_ready"
        missing_for_next = []

    return {
        "schema_version": 1,
        "stage_index": stage_index,
        "stage_name": stage_name,
        "missing_for_next_stage": missing_for_next,
        "evidence": evidence,
    }


def _agent_generated_solver_repair_plan(
    *,
    quality_risks: list[str],
    method_stage: dict[str, Any],
) -> dict[str, Any]:
    if not quality_risks:
        return {}
    structured = [risk for risk in quality_risks if "structured_neighborhood_claim_unimplemented" in risk]
    missing_components = _structured_claim_missing_components(structured)
    claim_terms = _structured_claim_terms_from_risks(structured)
    if structured:
        target_stage = _target_stage_for_structured_missing(missing_components)
        must_add = _repair_must_add_for_missing_components(missing_components)
        return {
            "schema_version": 1,
            "repair_mode": "method_stage_migration",
            "reason": "structured_neighborhood_claim_unimplemented",
            "current_stage": method_stage,
            "target_stage": target_stage,
            "blocking_claim_terms": claim_terms,
            "missing_components": missing_components,
            "must_add": must_add,
            "must_not": [
                "Do not repeat AWLS/critical-block/N7/N8/NK/k-insertion/tabu claims unless the patch adds the named missing structures.",
                "Do not replace the whole promoted solver when a bounded representation or decoder migration can repair the stage.",
                "Do not submit strategy prose or solver_contract_self_check evidence without matching reachable source symbols.",
            ],
            "acceptance_checks": [
                "Submitted code contains the missing state/decode/move guard symbols named in missing_components.",
                "Every candidate move is decoded and rejected on None, partial coverage, ineligible machine, precedence violation, or overlap.",
                "The incumbent schedule is replaced only after a strict evaluator-side improvement candidate exists.",
            ],
        }
    must_add = [
        "Repair the earliest missing parser, representation, constructor, decoder, coverage, eligibility, precedence, non-overlap, runtime, or incumbent-preservation capability before adding another optimization idea."
    ]
    if any("random_machine_choice_without_ready_machine_evaluation" in risk for risk in quality_risks):
        must_add.insert(
            0,
            "Replace random eligible-machine selection with an operation-level ready-choice loop: for every ready operation, iterate every eligible machine, compute start/finish from job_ready and machine_ready, collect scored candidates, then commit one best or seeded tie-break candidate.",
        )
    return {
        "schema_version": 1,
        "repair_mode": "quality_contract_repair",
        "reason": "agent_generated_solver_quality_contract_missing",
        "current_stage": method_stage,
        "blocking_quality_risks": quality_risks[:8],
        "must_add": must_add,
        "must_not": [
            "Do not satisfy operation_level_ready_list_constructor by selecting one ready operation and then calling rng.choice/random.choice over eligible machines.",
            "Do not claim a higher-level local-search method until lower-stage legality and representation capabilities are present."
        ],
    }


def _structured_claim_missing_components(risks: list[str]) -> list[str]:
    missing: list[str] = []
    for risk in risks:
        match = re.search(r"\bmissing\s+(.+)$", risk)
        if not match:
            continue
        for item in match.group(1).split(","):
            normalized = item.strip()
            if normalized and normalized not in missing:
                missing.append(normalized)
    return missing


def _structured_claim_terms_from_risks(risks: list[str]) -> list[str]:
    terms: list[str] = []
    for risk in risks:
        match = re.search(r"\bclaims\s+(.+?)\s+but\s+missing\b", risk)
        if not match:
            continue
        for item in match.group(1).split(","):
            normalized = item.strip()
            if normalized and normalized not in terms:
                terms.append(normalized)
    return terms


def _target_stage_for_structured_missing(missing_components: list[str]) -> str:
    ordered_targets = [
        ("machine_sequence_state_and_progress_decoder", "stage_2_sequence_state_and_progress_decoder"),
        ("decoded_candidate_rejection", "stage_3_guarded_progress_decoder"),
        ("post_move_coverage_guard", "stage_3_guarded_progress_decoder"),
        ("apply_move_on_assignment_and_machine_sequences", "stage_4_executable_sequence_move"),
        ("critical_block_extraction", "stage_5_structured_neighborhood"),
        ("n8_or_k_insertion_neighbor_generation", "stage_5_structured_neighborhood"),
    ]
    for component, target in ordered_targets:
        if component in missing_components:
            return target
    return "stage_5_structured_neighborhood"


def _repair_must_add_for_missing_components(missing_components: list[str]) -> list[str]:
    playbook = {
        "machine_sequence_state_and_progress_decoder": (
            "Add reachable assignment plus machine_sequences state and a progress/topological decoder that schedules only predecessor-ready operations."
        ),
        "critical_block_extraction": (
            "Add reachable critical block/path extraction from a decoded schedule and machine sequence view before generating critical-block moves."
        ),
        "n8_or_k_insertion_neighbor_generation": (
            "Add bounded N8/NK/k-insertion-like neighbor generation that emits explicit move records and caps candidate count/runtime."
        ),
        "apply_move_on_assignment_and_machine_sequences": (
            "Add an apply_move path that copies assignment and machine_sequences, applies swap/remove/insert/reassignment consistently, and leaves the incumbent untouched on failure."
        ),
        "post_move_coverage_guard": (
            "Add a post-move guard that compares decoded operation coverage with the expected operation set before scoring makespan."
        ),
        "decoded_candidate_rejection": (
            "Add candidate rejection branches for decode failure, partial schedules, invalid durations, ineligible machines, precedence violations, and machine overlaps."
        ),
    }
    result: list[str] = []
    for component in missing_components:
        instruction = playbook.get(component)
        if instruction and instruction not in result:
            result.append(instruction)
    return result


def _detect_agent_generated_solver_self_check_risks(
    *,
    proposal: dict[str, Any] | None,
    worktree_path: Path,
    changed_files: list[str],
    quality_contract: dict[str, Any],
) -> list[str]:
    if not quality_contract.get("enabled"):
        return []
    changed = [item.replace("\\", "/") for item in changed_files]
    if not any(_is_agent_generated_example_path(path) for path in changed):
        return []
    if not proposal:
        return _detect_agent_generated_source_self_check_risks(
            worktree_path=worktree_path,
            changed_files=changed,
            quality_contract=quality_contract,
        )

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

    variant_required = [
        item
        for item in quality_contract.get("variant_required_code_capabilities") or []
        if isinstance(item, str)
    ]
    narrative_fields = [
        ("representation", "representation evidence is missing"),
        ("decoder", "decoder evidence is missing"),
        ("runtime_bounds", "runtime bound evidence is missing"),
        ("incumbent_preservation", "incumbent preservation evidence is missing"),
    ]
    for field, message in narrative_fields:
        if not str(self_check.get(field) or "").strip():
            risks.append(f"solver_contract_self_check {message}")

    if variant_required and not self_check.get("variant_handling"):
        risks.append(
            "solver_contract_self_check missing variant_handling for active variant capabilities: "
            + ", ".join(variant_required)
        )
    required_narrative_fields = [field for field, _message in narrative_fields]
    if variant_required:
        required_narrative_fields.append("variant_handling")
    source_evidence_risks = _detect_self_check_evidence_source_mismatches(
        self_check=self_check,
        worktree_path=worktree_path,
        changed_files=changed,
        expected_capabilities=expected_capabilities,
        required_narrative_fields=required_narrative_fields,
    )
    risks.extend(source_evidence_risks)
    return risks


def _detect_agent_generated_source_self_check_risks(
    *,
    worktree_path: Path,
    changed_files: list[str],
    quality_contract: dict[str, Any],
) -> list[str]:
    sources = _agent_generated_solver_sources(worktree_path, changed_files)
    if not sources:
        return []
    combined_text = "\n".join(sources.values())
    self_check = _source_self_check_block(combined_text)
    if self_check is None:
        return [
            "agent-generated solver edit has no solver_contract_self_check proposal and no source-level validate_schedule/self_check function"
        ]
    name, block = self_check
    evidence_text = _source_self_check_evidence_text(combined_text, root_name=name, fallback=block)
    risks: list[str] = []
    reachable = function_is_reachable_from_entry(combined_text, name)
    if reachable is False or (reachable is None and function_call_count(combined_text, name) < 2):
        risks.append(f"source-level self-check `{name}` is defined but not called before output")

    expected_capabilities = set(_quality_contract_capabilities_for_review(quality_contract))
    capability_detectors = [
        ("complete_schedule_coverage_guard", _has_operation_coverage_guard),
        ("machine_eligibility_guard", _has_machine_eligibility_guard),
        ("processing_duration_guard", _has_processing_duration_guard),
        ("job_precedence_guard", _has_job_precedence_guard),
        ("machine_non_overlap_guard", _has_machine_non_overlap_guard),
        ("setup_aware_machine_arc_timing", _has_setup_aware_source_self_check_guard),
        ("no_wait_start_time_guard", _has_no_wait_source_self_check_guard),
        ("time_lag_precedence_guard", _has_time_lag_source_self_check_guard),
        ("machine_calendar_availability_guard", _has_machine_calendar_source_self_check_guard),
        ("batch_capacity_guard", _has_batch_capacity_source_self_check_guard),
        ("transport_time_guard", _has_transport_time_source_self_check_guard),
        ("release_date_guard", _has_release_date_source_self_check_guard),
        ("due_date_or_tardiness_objective_guard", _has_due_date_source_self_check_guard),
        ("declared_objective_priority_guard", _has_multi_objective_source_self_check_guard),
    ]
    missing = [
        capability
        for capability, detector in capability_detectors
        if capability in expected_capabilities and not detector(evidence_text)
    ]
    if missing:
        risks.append(
            "source-level self-check missing capability evidence: "
            + ", ".join(missing)
        )
    return risks


def _source_self_check_block(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"^def\s+(_*(?:validate|self_check|check|assert)[A-Za-z0-9_]*(?:schedule|solution|feasible|valid)[A-Za-z0-9_]*)\s*\(",
        text,
        re.M,
    )
    if not match:
        return None
    next_def = re.search(r"^def\s+", text[match.end() :], re.M)
    end = match.end() + next_def.start() if next_def else len(text)
    return match.group(1), text[match.start() : end]


def _source_self_check_evidence_text(text: str, *, root_name: str, fallback: str) -> str:
    """Include directly reachable validation helpers in source-level evidence."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return fallback
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if root_name not in functions:
        return fallback
    lines = text.splitlines()
    selected: list[str] = []
    pending = [root_name]
    visited: set[str] = set()
    while pending and len(visited) < 12:
        name = pending.pop(0)
        if name in visited:
            continue
        node = functions.get(name)
        if node is None:
            continue
        visited.add(name)
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        selected.append("\n".join(lines[node.lineno - 1 : end_lineno]))
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                called_name = child.func.id
                if called_name in functions and called_name not in visited:
                    pending.append(called_name)
    return "\n\n".join(selected) or fallback


def _detect_agent_generated_dead_function_risks(text: str) -> list[str]:
    """Catch generated helpers that are cited/defined but not wired into flow.

    This is intentionally a call-flow contract, not an algorithm template.  A
    generated solver may choose any parser, decoder, or validator shape, but if
    it defines one of these critical helpers it must actually call it.
    """

    patterns = [
        (
            "parser",
            r"^def\s+((?:parse|read|load)[A-Za-z0-9_]*(?:instance|problem|input)[A-Za-z0-9_]*)\s*\(",
        ),
        (
            "decoder",
            r"^def\s+((?:decode|build|construct)[A-Za-z0-9_]*(?:schedule|solution)[A-Za-z0-9_]*)\s*\(",
        ),
        (
            "source-level self-check",
            r"^def\s+((?:validate|self_check|check|assert)[A-Za-z0-9_]*(?:schedule|solution|feasible|valid)[A-Za-z0-9_]*)\s*\(",
        ),
    ]
    return [
        f"agent_generated_solver: {label} `{name}` is defined but not reachable from generated solver entry flow"
        for label, name in unreachable_defined_function_helpers(text, patterns)
    ]


def _has_setup_aware_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    setup_terms = ["setup_time(", "setup_times", "setup ="]
    previous_terms = ["prev_key", "prev_op", "previous", "prev_end", "machine_prev"]
    timing_terms = ["+ setup", "setup +", "prev_end + setup", "start <"]
    return (
        any(term in lowered for term in setup_terms)
        and any(term in lowered for term in previous_terms)
        and any(term in lowered for term in timing_terms)
        and "machine" in lowered
    )


def _has_no_wait_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return (
        ("no_wait" in lowered or "no-wait" in lowered)
        and ("start !=" in lowered or "start ==" in lowered or "same start" in lowered)
        and ("job_ready" in lowered or "predecessor" in lowered or "prev_end" in lowered)
    )


def _has_time_lag_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    lag_terms = ["time_lag", "time lag", "lag_min", "lag_max", "min_lag", "max_lag"]
    return any(term in lowered for term in lag_terms) and "start" in lowered and (
        "predecessor" in lowered or "prev_end" in lowered or "job_ready" in lowered
    )


def _has_machine_calendar_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return (
        any(term in lowered for term in ["calendar", "availability", "unavailable"])
        and "machine" in lowered
        and "start" in lowered
        and "end" in lowered
    )


def _has_batch_capacity_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return "batch" in lowered and "capacity" in lowered


def _has_transport_time_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return (
        any(term in lowered for term in ["transport", "travel"])
        and any(term in lowered for term in ["+ transport", "+ travel", "transport_time", "travel_time"])
    )


def _has_release_date_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return (
        any(term in lowered for term in ["release_date", "release_time", "release"])
        and "start <" in lowered
    )


def _has_due_date_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ["due_date", "due time", "tardiness", "lateness"]) and (
        "objective" in lowered or "max(" in lowered or "end" in lowered
    )


def _has_multi_objective_source_self_check_guard(text: str) -> bool:
    lowered = text.lower()
    return "objective" in lowered and any(
        term in lowered for term in ["priority", "weight", "lexicographic", "pareto"]
    )


def _detect_self_check_evidence_source_mismatches(
    *,
    self_check: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
    expected_capabilities: set[str],
    required_narrative_fields: list[str],
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
    for field in required_narrative_fields:
        evidence = _self_check_narrative_text(self_check.get(field))
        if not evidence.strip():
            continue
        code_tokens = [
            token
            for token in _self_check_code_evidence_tokens(evidence)
            if token != field and token not in expected_capabilities
        ]
        if not code_tokens:
            risks.append(
                f"solver_contract_self_check narrative evidence for {field} does not cite a concrete code symbol"
            )
            continue
        missing_tokens = [token for token in code_tokens if token.lower() not in source_text]
        if len(missing_tokens) == len(code_tokens):
            risks.append(
                "solver_contract_self_check narrative evidence for "
                f"{field} does not match generated source symbols: {', '.join(code_tokens[:4])}"
            )
    return risks


def _self_check_narrative_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


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
        "for j in range(job_count",
        "for job in range(num_jobs",
        "for job in range(job_count",
        "for job_id, job in enumerate",
        "for job in jobs",
        "for op_id in range(op_count",
        "for op_id in range(ops_in_job",
        "for op in range(op_count",
        "for op_id, op in enumerate",
        "candidate_count",
        "cand_count",
        "candidates.append",
        "raw_ops.append",
        "eligible = {",
        "op_info[op_key]",
        "op_info[(job_id, op_id)]",
        "op_info[(job, op)]",
        "op_info[(j, op_id)]",
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


def _is_standard_fjsp_without_setup_context(context: dict[str, Any], quality_contract: dict[str, Any]) -> bool:
    active_features = {str(item).strip() for item in quality_contract.get("active_features") or [] if str(item).strip()}
    variant_features = {
        "sequence_dependent_setup",
        "no_wait",
        "time_lag",
        "machine_calendar",
        "batching",
        "transportation",
        "release_dates",
        "due_dates",
        "multi_objective",
    }
    if active_features & variant_features:
        return False

    diagnostics = context.get("instance_diagnostics") if isinstance(context.get("instance_diagnostics"), dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    instances = [item for item in diagnostics.get("instances") or [] if isinstance(item, dict)]

    setup_kinds = [str(item).strip().lower() for item in summary.get("setup_time_kinds") or []]
    if any(kind not in {"", "none", "null"} for kind in setup_kinds):
        return False
    if int(summary.get("sdst_instance_count") or 0) > 0:
        return False
    for item in instances:
        setup_kind = str(item.get("setup_time_kind") or "").strip().lower()
        if setup_kind not in {"", "none", "null"}:
            return False

    knowledge_selection = (
        context.get("knowledge_selection") if isinstance(context.get("knowledge_selection"), dict) else {}
    )
    active_variant = str(knowledge_selection.get("active_variant") or "").strip().lower()
    family = str((context.get("task") or {}).get("problem_family") or context.get("problem_family") or "").strip().lower()
    instance_variants = {str(item.get("variant") or "").strip().lower() for item in instances}
    return (
        active_variant == "standard_fjsp"
        or family in {"standard_fjsp", "fjsp"}
        or "standard_fjsp" in instance_variants
    )


def _is_incremental_after_baseline_context(context: dict[str, Any]) -> bool:
    contract = context.get("iteration_edit_contract") if isinstance(context, dict) else {}
    return bool(isinstance(contract, dict) and contract.get("mode") == "incremental_after_baseline")


def _detect_standard_fjsp_packed_line_parser_risks(text: str) -> list[str]:
    lowered = text.lower()
    if not any(term in lowered for term in ("splitlines(", ".readlines(", "readlines(")):
        return []

    loop_pattern = re.compile(
        r"for\s+\w+\s+in\s+range\(\s*(?:op_count|num_ops|operation_count|n_ops)[^)]*\)\s*:"
        r"(?P<body>(?:\n[ \t]+[^\n]*){1,40})",
        re.I,
    )
    cursor_names = ("idx", "line_idx", "line_index", "line_no", "pos")
    for match in loop_pattern.finditer(text):
        body = match.group("body")
        reads_physical_line = re.search(
            r"\blines\s*\[\s*(?:idx|line_idx|line_index|line_no|pos)\s*\]", body, re.I
        )
        advances_line_cursor = any(re.search(rf"\b{name}\s*\+=\s*1\b", body) for name in cursor_names)
        if reads_physical_line and advances_line_cursor:
            return [
                "agent_generated_solver: standard FJSP parser assumes one physical operation line; "
                "Dauzere/DP/BA/BR/HU instances pack all operations for a job on one line, so parse "
                "with a token cursor over the job line"
            ]
    return []


def _detect_agent_generated_output_schema_mismatch_risks(text: str) -> list[str]:
    """Detect solver files that write a bare schedule list instead of schema object."""

    bare_schedule_names = r"(?:best_schedule|candidate_schedule|decoded_schedule|decoded|schedule|result)"
    patterns = [
        rf"json\.dump\s*\(\s*{bare_schedule_names}\s*,",
        r"json\.dump\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\[\s*['\"]schedule['\"]\s*\]\s*,",
        rf"json\.dumps\s*\(\s*{bare_schedule_names}\s*\)",
        r"json\.dumps\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\[\s*['\"]schedule['\"]\s*\]\s*\)",
    ]
    if not any(re.search(pattern, text, re.I) for pattern in patterns):
        return []
    object_writer_patterns = [
        r"json\.dump\s*\(\s*\{[^{}]*(?:['\"]schedule['\"])\s*:",
        r"json\.dumps\s*\(\s*\{[^{}]*(?:['\"]schedule['\"])\s*:",
    ]
    if any(re.search(pattern, text, re.I | re.S) for pattern in object_writer_patterns):
        return []
    object_variable_patterns = [
        r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*dict[^=]*)?=\s*\{[^{}]*['\"]schedule['\"]\s*:"
        r".*?\}\s*.{0,800}?\bjson\.dump\s*\(\s*(?P=name)\s*,",
        r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*dict[^=]*)?=\s*\{[^{}]*['\"]schedule['\"]\s*:"
        r".*?\}\s*.{0,800}?\bjson\.dumps\s*\(\s*(?P=name)\s*\)",
    ]
    if any(re.search(pattern, text, re.I | re.S) for pattern in object_variable_patterns):
        return []
    return [
        "agent_generated_solver: declared_output_schema_mismatch: solver appears to write a bare schedule list; "
        "the standard evaluator expects a JSON object with a `schedule` array"
    ]


def _detect_machine_major_decoder_precedence_risks(text: str) -> list[str]:
    """Detect decoders that replay each machine sequence in machine order only."""

    machine_major_loop = re.search(
        r"for\s+[^:\n]*,\s*(?P<sequence>[A-Za-z_][A-Za-z0-9_]*)\s+in\s+machine_sequences?\.items\(\)\s*:"
        r"(?P<body>.*?)(?=\n\S|\Z)",
        text,
        re.I | re.S,
    )
    if not machine_major_loop:
        return []
    sequence_name = re.escape(machine_major_loop.group("sequence"))
    body = machine_major_loop.group("body")
    if not re.search(rf"\n[ \t]+for\s+[^:\n]+\s+in\s+{sequence_name}\s*:", body, re.I):
        return []
    updates_job_ready = re.search(r"\b(?:job_ready|job_end)\s*\[[^\]]+\]\s*=", body, re.I)
    if not updates_job_ready:
        return []
    has_progress_decoder = re.search(r"\bwhile\s+len\s*\(\s*schedule\s*\)\s*<", text, re.I) and re.search(
        r"\bprogressed\b", text, re.I
    )
    has_predecessor_skip = re.search(
        r"op_id\s*>\s*0.*(?:op_id\s*-\s*1|prev_op|predecessor).*continue",
        text,
        re.I | re.S,
    )
    if has_progress_decoder and has_predecessor_skip:
        return []
    return [
        "agent_generated_solver: job_precedence_guard_mismatch: decoder replays machine_sequences in machine-major order; "
        "decode machine sequences with a progress/topological loop so job successors cannot be scheduled before predecessors"
    ]


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
    if not re.search(r"\b(?:assignment|on_machine)\b", text, re.I):
        missing.append("operation_to_machine_assignment")
    if not _has_machine_sequence_state_shape(text.lower()):
        missing.append("machine_sequences")
    if not _has_machine_sequence_decoder_shape(text.lower()):
        missing.append("full_decoder")
    if not _has_operation_coverage_guard(text):
        missing.append("post_move_coverage_guard")
    if not _has_incumbent_preservation_guard(text):
        missing.append("keep_incumbent_on_failed_move")
    return missing


def _detect_shallow_standard_fjsp_local_search_risks(text: str, *, proposal_text: str = "") -> list[str]:
    """Reject local-search proposals that stop at random reassignment climbing.

    This is deliberately not a backend algorithm template.  It only blocks the
    repeated failure mode where an agent keeps an assignment/sequence decoder
    but implements the "neighborhood" as random op -> random alternative machine
    -> random insertion, with no critical-path/block selection, N8/k-insertion
    candidate generation, tabu/aspiration memory, or diversification path.
    """

    lowered = text.lower()
    evidence_lower = f"{lowered}\n{proposal_text.lower()}"
    if not _has_assignment_sequence_decoder_shape(lowered):
        return []
    if not _claims_standard_fjsp_local_search(evidence_lower):
        return []
    if not _has_random_reassignment_hill_climber_shape(lowered):
        return []
    if _has_structured_standard_fjsp_neighborhood_shape(lowered):
        return []
    return [
        "agent_generated_solver: shallow_local_search_operator: random hill-climbing reassignment lacks "
        "critical-path/critical-block candidate selection, N8 or k-insertion neighborhood generation, "
        "tabu/aspiration memory, and bounded perturbation/diversification"
    ]


def _detect_structured_standard_fjsp_neighborhood_claim_risks(text: str, *, proposal_text: str = "") -> list[str]:
    """Require executable structure when a proposal claims a strong FJSP neighborhood.

    The backend remains algorithm-agnostic: this does not prescribe the scoring
    formula or exact move set.  It only rejects the mismatch where a proposal
    claims critical-block, N7/N8/NK, k-insertion, tabu, or AWLS-style local
    search but edits only a global order or shallow reassignment loop.
    """

    lowered = text.lower()
    evidence_lower = f"{lowered}\n{proposal_text.lower()}"
    claim_terms = _claimed_structured_neighborhood_terms(evidence_lower)
    if not claim_terms:
        return []

    missing: list[str] = []
    if not _has_machine_sequence_decoder_shape(lowered):
        missing.append("machine_sequence_state_and_progress_decoder")
    if _claim_requires_critical_block_extraction(claim_terms) and not _has_critical_block_extraction_shape(lowered):
        missing.append("critical_block_extraction")
    if _claim_requires_n8_or_k_insertion_generation(claim_terms) and not _has_n8_or_k_insertion_generation_shape(lowered):
        missing.append("n8_or_k_insertion_neighbor_generation")
    if not _has_move_application_shape(lowered):
        missing.append("apply_move_on_assignment_and_machine_sequences")
    if not _has_operation_coverage_guard(text):
        missing.append("post_move_coverage_guard")
    if not _has_decoded_candidate_rejection_shape(lowered):
        missing.append("decoded_candidate_rejection")
    if not missing:
        return []
    return [
        "agent_generated_solver: structured_neighborhood_claim_unimplemented: claims "
        f"{', '.join(claim_terms)} but missing {', '.join(missing)}"
    ]


def _claimed_structured_neighborhood_terms(lowered: str) -> list[str]:
    terms: list[str] = []
    patterns = [
        ("awls", r"\bawls\b"),
        ("n7", r"\bn7\b"),
        ("n8", r"\bn8\b"),
        ("nk", r"\bnk\b"),
        ("k_insertion", r"\bk[_\s-]*insertion\b"),
        ("critical_block", r"\bcritical[_\s-]*(?:block|blocks)\b"),
        ("tabu", r"\btabu(?:[_\s-]*(?:list|until|tenure|search|memory))?\b"),
    ]
    for label, pattern in patterns:
        if any(not _structured_term_is_negated(lowered, match.start()) for match in re.finditer(pattern, lowered, re.S)):
            terms.append(label)
    return terms


def _structured_term_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 90) : start]
    return bool(
        re.search(
            r"\b(?:without|omit|omits|omitted|avoid|avoids|avoided|remove|removes|removed|no|not|never|do\s+not|don't|unsupported|unimplemented)\b.{0,80}$",
            prefix,
            re.S,
        )
    )


def _agent_generated_method_claim_text(proposal: dict[str, Any] | None) -> str:
    """Extract proposal text that can be a method claim, excluding evidence paths and full code."""

    if not isinstance(proposal, dict):
        return ""
    parts = [
        str(proposal.get("summary") or ""),
        str(proposal.get("strategy_intent") or ""),
        str(proposal.get("quick_test_plan") or ""),
    ]
    for item in proposal.get("rule_operator_hypotheses") or []:
        if not isinstance(item, dict):
            continue
        parts.extend(
            [
                str(item.get("name") or ""),
                str(item.get("type") or ""),
                str(item.get("novelty") or ""),
                str(item.get("expected_effect") or ""),
                str(item.get("ablation_plan") or ""),
            ]
        )
    for item in proposal.get("changes") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("rationale") or ""))
    for item in proposal.get("risk_notes") or []:
        parts.append(str(item))
    context_usage = proposal.get("context_usage") if isinstance(proposal.get("context_usage"), dict) else {}
    parts.append(str(context_usage.get("notes") or ""))
    self_check = proposal.get("solver_contract_self_check") if isinstance(proposal.get("solver_contract_self_check"), dict) else {}
    parts.extend(
        [
            str(self_check.get("representation") or ""),
            str(self_check.get("decoder") or ""),
            str(self_check.get("runtime_bounds") or ""),
            str(self_check.get("incumbent_preservation") or ""),
        ]
    )
    for item in self_check.get("variant_handling") or []:
        parts.append(str(item))
    for item in self_check.get("remaining_gaps") or []:
        parts.append(str(item))
    return "\n".join(part for part in parts if part).lower()


def _claim_requires_critical_block_extraction(claim_terms: list[str]) -> bool:
    return any(term in {"awls", "n7", "n8", "nk", "k_insertion", "critical_block"} for term in claim_terms)


def _claim_requires_n8_or_k_insertion_generation(claim_terms: list[str]) -> bool:
    return any(term in {"awls", "n7", "n8", "nk", "k_insertion"} for term in claim_terms)


def _has_machine_sequence_decoder_shape(lowered: str) -> bool:
    has_sequence_state = _has_machine_sequence_state_shape(lowered)
    return bool(
        re.search(r"\bdef\s+\w*decode\w*\s*\([^)]*machine_sequences?", lowered, re.S)
        or (
            has_sequence_state
            and re.search(r"\bdef\s+\w*decode\w*\s*\([^)]*\bsequences?\b", lowered, re.S)
        )
        or (
            has_sequence_state
            and re.search(r"\bdef\s+_*(?:topological_sort|update_time|recompute_times?)\s*\(", lowered)
            and re.search(r"\bindegree\b|\bcycle detected\b|\btopological_order\b", lowered)
        )
        or (
            re.search(r"\bfor\s+[^:\n]*,\s*\w+\s+in\s+machine_sequences?\.items\s*\(\s*\)\s*:", lowered)
            and re.search(r"\bprogress(?:ed)?\b|\bwhile\s+len\s*\(", lowered, re.S)
        )
    )


def _has_machine_sequence_state_shape(lowered: str) -> bool:
    if re.search(r"\bmachine_sequences?\b", lowered):
        return True
    return bool(
        re.search(r"\bclass\s+\w*(?:schedule|state)\w*\s*[:(]", lowered)
        and re.search(r"\bself\.sequences\b", lowered)
        and "machine_predecessor" in lowered
        and "machine_successor" in lowered
    )


def _has_critical_block_extraction_shape(lowered: str) -> bool:
    if re.search(r"\bdef\s+\w*critical\w*(?:block|path)\w*\s*\(", lowered):
        return True
    if re.search(r"\bcritical_blocks?\s*=", lowered) and "machine_sequences" in lowered:
        return True
    return bool(
        re.search(r"\bby_machine\b", lowered)
        and re.search(r"\bcritical\b", lowered)
        and re.search(r"\bmachine_sequences?\b", lowered)
    )


def _has_n8_or_k_insertion_generation_shape(lowered: str) -> bool:
    return bool(
        re.search(r"\bdef\s+\w*(?:n7|n8|nk)\w*(?:neighbor|neighbour|move|candidate)", lowered)
        or re.search(r"\bgenerate[_\s-]*(?:n7|n8|nk)", lowered)
        or re.search(r"\bdef\s+\w*k[_\s-]*insertion\w*", lowered)
        or re.search(r"\bgenerate[_\s-]*k[_\s-]*insertion", lowered)
        or re.search(r"\binsertion_positions?\b", lowered)
    )


def _has_move_application_shape(lowered: str) -> bool:
    return bool(
        "apply_move" in lowered
        or re.search(r"\bdef\s+\w*apply\w*move\w*\s*\(", lowered)
        or (
            "machine_sequences" in lowered
            and re.search(r"\.remove\s*\(", lowered)
            and re.search(r"\.insert\s*\(", lowered)
        )
        or (
            "machine_sequences" in lowered
            and re.search(r"\bnew_machine_sequences\b", lowered)
            and re.search(r"\bif\s+\w+\s*!=\s*op_key\b", lowered)
            and re.search(r"\.append\s*\(\s*op_key\s*\)", lowered)
        )
        or (
            "machine_sequences" in lowered
            and re.search(r"\bsequence\s*\[[^\]]+\]\s*,\s*sequence\s*\[[^\]]+\]\s*=", lowered)
        )
        or (
            "machine_sequences" in lowered
            and re.search(r"\bsequences?\s*=\s*\{[^}]*machine_sequences", lowered, re.S)
            and re.search(r"\bsearchstate\b|\bstate\b", lowered)
        )
    )


def _has_decoded_candidate_rejection_shape(lowered: str) -> bool:
    return bool(
        re.search(r"\bcandidate(?:_schedule|_decoded)?\s+is\s+none\b", lowered)
        or re.search(r"\b(?:res|result|trial|trial_schedule|cand_schedule)\s+is\s+none\s*:\s*continue\b", lowered)
        or re.search(r"\bif\s+not\s+(?:validate|is_valid|check)", lowered)
        or re.search(r"\bdecode\w*\([^)]*\).*?\bcontinue\b", lowered, re.S)
        or (
            _has_transactional_candidate_application_shape(lowered)
            and re.search(r"\bexcept\s*\([^)]*(?:valueerror|keyerror|runtimeerror)", lowered)
        )
    )


def _has_transactional_candidate_application_shape(lowered: str) -> bool:
    clone_names = re.findall(
        r"\b([a-z_][a-z0-9_]*)\s*=\s*(?:current|state|incumbent|best_state)\.clone\s*\(\s*\)",
        lowered,
    )
    for name in clone_names:
        if re.search(
            rf"(?:\b{re.escape(name)}\.apply_move\s*\(|(?<![a-z0-9_])[a-z0-9_]*apply_move[a-z0-9_]*\s*\(\s*{re.escape(name)}\b)",
            lowered,
        ):
            return True
        if (
            re.search(rf"\b{re.escape(name)}\.machine_sequences?\b", lowered)
            and re.search(rf"\b{re.escape(name)}\.(?:update_time|decode|recompute_times?)\s*\(", lowered)
            and re.search(rf"\breturn\s+{re.escape(name)}\b", lowered)
        ):
            return True
    snapshot_terms = ("snapshot", "rollback", "restore_state", "old_assignment", "old_machine_sequences")
    return any(term in lowered for term in snapshot_terms) and bool(
        re.search(r"\b(?:rollback|restore|old_assignment|old_machine_sequences)\b", lowered)
    )


def _has_failed_in_place_move_without_rollback(lowered: str) -> bool:
    mutates_sequences = bool(re.search(r"\.(?:pop|insert|remove)\s*\(", lowered)) and "machine_sequence" in lowered
    applies_to_current = bool(
        re.search(r"\b_?apply_move\s*\(\s*current\b", lowered)
        or re.search(r"\bcurrent\.apply_move\s*\(", lowered)
    )
    catches_failure = "except" in lowered and ("return false" in lowered or "continue" in lowered)
    return (
        mutates_sequences
        and applies_to_current
        and catches_failure
        and not _has_transactional_candidate_application_shape(lowered)
    )


def _has_assignment_sequence_decoder_shape(lowered: str) -> bool:
    return (
        ("assignment" in lowered or "on_machine" in lowered)
        and _has_machine_sequence_state_shape(lowered)
        and _has_machine_sequence_decoder_shape(lowered)
    )


def _claims_standard_fjsp_local_search(lowered: str) -> bool:
    claim_terms = [
        "local_search",
        "local search",
        "neighborhood",
        "neighbourhood",
        "hill climb",
        "hill-climb",
        "reassignment",
        "relocation",
        "awls",
        "n7",
        "n8",
        "nk",
        "k-insertion",
        "k_insertion",
        "tabu",
    ]
    return any(term in lowered for term in claim_terms)


def _has_random_reassignment_hill_climber_shape(lowered: str) -> bool:
    random_op = bool(
        re.search(
            r"\b(?:rng|random)\.(?:choice|sample)\s*\(\s*"
            r"(?:ops|all_ops|operations|op_keys|list\s*\(\s*assignment\.keys\s*\(\s*\)\s*\))",
            lowered,
        )
    )
    random_machine = bool(
        re.search(r"\b(?:rng|random)\.(?:choice|sample)\s*\(\s*(?:alt_machines|eligible_machines)", lowered)
    )
    random_insert = bool(re.search(r"\b(?:rng|random)\.(?:randint|randrange)\s*\(", lowered) and ".insert(" in lowered)
    mutates_assignment = bool(re.search(r"\b(?:new_)?assignment\s*\[[^\]]+\]\s*=", lowered))
    mutates_machine_sequence = "machine_sequences" in lowered and ".remove(" in lowered and ".insert(" in lowered
    has_alt_machine_loop = "alt_machines" in lowered or "alternative machine" in lowered
    return (
        (random_op or (has_alt_machine_loop and "choice(" in lowered))
        and (random_machine or random_insert or has_alt_machine_loop)
        and mutates_assignment
        and mutates_machine_sequence
    )


def _has_structured_standard_fjsp_neighborhood_shape(lowered: str) -> bool:
    critical_terms = [
        r"\bcritical[_\s-]*(?:path|block|blocks)\b",
        r"\bcritical\b.{0,80}\b(?:path|block|blocks|operation|operations)\b",
        r"\b(?:longest|makespan)[_\s-]*path\b",
        r"\bbottleneck[_\s-]*(?:machine|block|blocks)\b",
    ]
    n8_or_insertion_terms = [
        r"\bn[78]\b",
        r"\bgenerate[_\s-]*n[78]",
        r"\bn[78][_\s-]*(?:move|neighbor|neighbour|candidate|block)",
        r"\bk[_\s-]*insertion\b",
        r"\binsertion[_\s-]*positions\b",
        r"\bblock[_\s-]*(?:insert|insertion|relocate|relocation|swap)\b",
    ]
    memory_terms = [
        r"\btabu(?:[_\s-]*(?:list|until|tenure|search|memory))?\b",
        r"\baspiration\b",
    ]
    diversification_terms = [
        r"\bperturb(?:_state|ation)?\b",
        r"\bdiversif(?:y|ication)?\b",
        r"\bstagnation\b",
        r"\bno[_\s-]*improve\b",
        r"\brestart[_\s-]*after[_\s-]*no[_\s-]*improve\b",
    ]
    has_critical = any(re.search(pattern, lowered, re.S) for pattern in critical_terms)
    has_n8_or_insertion = any(re.search(pattern, lowered, re.S) for pattern in n8_or_insertion_terms)
    has_memory = any(re.search(pattern, lowered, re.S) for pattern in memory_terms)
    has_diversification = any(re.search(pattern, lowered, re.S) for pattern in diversification_terms)
    return (
        (has_critical and has_n8_or_insertion)
        or (has_n8_or_insertion and has_memory)
        or (has_critical and (has_memory or has_diversification))
        or (has_memory and has_diversification and "decode" in lowered)
    )


def _has_stable_operation_identity(text: str) -> bool:
    lowered = text.lower()
    pair_like = (
        "(job_id, op_id)" in lowered
        or "(job, op)" in lowered
        or "(j, op)" in lowered
        or "(j, op_id)" in lowered
        or "(j, next_op)" in lowered
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
        "ready_list",
        "ready =",
        "candidates:",
        "candidates =",
        "next_op_by_job",
        "job_next_op",
        "next_operation",
        "job_next",
        "remaining_jobs",
    ]
    next_operation_terms = [
        "next_op_by_job[job_id]",
        "next_op[job_id]",
        "job_next[job_id]",
        "job_next_op[j]",
        "job_next_op[job]",
        "job_next_op.items",
        "next_op < job_ops",
        "op = (j, next_op)",
        "nxt = job_next_op",
        "next_operation[job_id]",
        "op_id = next",
        "op_id == next",
        "op_id > 0",
        "nxt < job_op_counts",
    ]
    eligible_machine_terms = [
        "for machine_id, duration in eligible.items()",
        "for machine_id, processing_time in eligible.items()",
        "for machine_id, proc_time in eligible.items()",
        "for machine_id in eligible",
        "for machine in eligible",
        "for m in eligible",
        "for m_id in eligible",
        "for machine_id, duration in candidates",
        "for machine_id, duration in candidates.items()",
        "for machine_id, processing_time in candidates",
        "for machine_id, processing_time in candidates.items()",
        "for machine_id, proc in candidates.items()",
        "for m_id, proc in candidates.items()",
        "for m_id, duration in candidates.items()",
        "for mach_id, proc in candidates.items()",
        "for (m, pt) in cands",
        "for m, pt in cands",
        "for m, proc in candidates.items()",
        "for m, dur in candidates.items()",
        "for (machine_id, processing_time) in cands",
        "for mach, dur in op_info[op_key]",
        "for machine, duration in op_info[op_key]",
        "for machine, duration in op_info[op].items()",
        "for m, dur in op_info[key]",
        "for m, dur in op_info[key][\"eligible\"].items()",
        "for m, dur in op_info[key]['eligible'].items()",
        "for machine, duration in op_info[key][\"eligible\"].items()",
        "for machine, duration in op_info[key]['eligible'].items()",
        "for machine_id, duration in op_info[key][\"eligible\"].items()",
        "for machine_id, duration in op_info[key]['eligible'].items()",
        "for machine_id in op_info[op_key][\"eligible\"]",
        "for machine_id in op_info[op_key]['eligible']",
        "for machine_id, duration in op_info[op_key][\"eligible\"].items()",
        "for machine_id, duration in op_info[op_key]['eligible'].items()",
        "for machine_id, duration in op_info[op_key][\"processing_times\"].items()",
        "for machine_id, duration in op_info[op_key]['processing_times'].items()",
        "for machine_id, proc in op_info[op_key][\"processing_times\"].items()",
        "for machine_id, proc in op_info[op_key]['processing_times'].items()",
        "for machine_id in op_info[op_key][\"candidates\"]",
        "for machine_id in op_info[op_key]['candidates']",
        "for machine in op_info[op_key][\"eligible\"]",
        "for machine in op_info[op_key]['eligible']",
        "for machine, duration in op_info[op_key][\"processing_times\"].items()",
        "for machine, duration in op_info[op_key]['processing_times'].items()",
        "for machine in op_info[op_key][\"candidates\"]",
        "for machine in op_info[op_key]['candidates']",
        "for m in op_info[op_key][\"eligible\"]",
        "for m in op_info[op_key]['eligible']",
        "for m, dur in op_info[op_key][\"processing_times\"].items()",
        "for m, dur in op_info[op_key]['processing_times'].items()",
        "for m in op_info[op_key][\"candidates\"]",
        "for m in op_info[op_key]['candidates']",
        "for m_id in op_info[op_key][\"eligible\"]",
        "for m_id in op_info[op_key]['eligible']",
        "for m_id in op_info[op_key][\"candidates\"]",
        "for m_id in op_info[op_key]['candidates']",
        "for mach_id in op_info[op_key][\"candidates\"]",
        "for mach_id in op_info[op_key]['candidates']",
        "for m, d in op_info[op_key]",
        "for m, dur in op_info[op_key]",
        "for m, dur in op_info[op]",
        "for mach, dur in candidates",
        "for mach, dur in op_info.get",
        "for m_idx, machine in enumerate(info['machines'])",
        'for m_idx, machine in enumerate(info["machines"])',
        "for machine in info['machines']",
        'for machine in info["machines"]',
        "for m_id in info['eligible']",
        'for m_id in info["eligible"]',
        "for machine_id in info['eligible']",
        'for machine_id in info["eligible"]',
        "for machine in info['eligible']",
        'for machine in info["eligible"]',
        "for m in info['eligible']",
        'for m in info["eligible"]',
        "for m in op_data['eligible']",
        'for m in op_data["eligible"]',
        "for machine_id in op_data['eligible']",
        'for machine_id in op_data["eligible"]',
    ]
    selection_terms = [
        "best_choice",
        "best_candidate",
        "best_assignment",
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
    state_terms = [
        "job_ready",
        "job_ready_time",
        "machine_ready",
        "machine_ready_time",
        "assignment",
        "machine_sequences",
        "schedule[",
        "schedule [",
        "schedule.append",
        "scheduled.append",
    ]
    explicit_ready_list = (
        any(term in lowered for term in ready_terms)
        and any(term in lowered for term in next_operation_terms)
        and any(term in lowered for term in eligible_machine_terms)
        and any(term in lowered for term in selection_terms)
        and sum(1 for term in state_terms if term in lowered) >= 3
    )
    current_position_ready_list = bool(
        re.search(r"\bcurrent_(?:pos|position)\s*=\s*\[", lowered)
        and (
            re.search(r"\bfor\s+job_id\s*,\s*nodes\s+in\s+enumerate\s*\(", lowered)
            or re.search(r"\bfor\s+job_id\s+in\s+range\s*\(", lowered)
        )
        and (
            re.search(r"nodes\s*\[\s*current_(?:pos|position)\s*\[\s*job_id\s*\]\s*\]", lowered)
            or re.search(
                r"job_to_nodes\s*\[\s*job_id\s*\]\s*\[\s*current_(?:pos|position)\s*\[\s*job_id\s*\]\s*\]",
                lowered,
            )
        )
        and re.search(
            r"\bfor\s+\w+\s*,\s*\w+\s+in\s+[^\n:]{0,240}(?:eligible|candidates)[^\n:]{0,120}\.items\s*\(\s*\)",
            lowered,
        )
        and re.search(r"\b(?:choices|all_candidates|ready_choices|ready_candidates)\.append\s*\(", lowered)
        and any(term in lowered for term in ("rng.choice", "min(", ".sort(", "sorted("))
        and "job_ready" in lowered
        and "machine_ready" in lowered
        and ("machine_sequences" in lowered or "sequences" in lowered)
    )
    return explicit_ready_list or current_position_ready_list


def _detect_random_ready_machine_selection_risks(text: str) -> list[str]:
    lowered = text.lower()
    if not any(term in lowered for term in ("ready_ops", "ready =", "job_next_op", "next_op")):
        return []
    random_machine_choice = any(
        re.search(pattern, lowered, re.S)
        for pattern in [
            r"\b(?:chosen_machine|machine_id|machine|mid|m)\s*=\s*(?:rng|random)\.choice\(\s*(?:eligible|machines|eligible_machines)\s*\)",
            r"\b(?:chosen_machine|machine_id|machine|mid|m)\s*=\s*(?:rng|random)\.choice\(\s*list\(\s*(?:cands|candidates|op_info\[[^\]]+\])\.keys\(\)\s*\)\s*\)",
            r"\b(?:rng|random)\.choice\(\s*(?:eligible|machines|eligible_machines)\s*\)",
        ]
    )
    candidate_scoring = any(
        re.search(pattern, lowered, re.S)
        for pattern in [
            r"\bready_(?:ops|choices|candidates)\.append\(\s*\((?:finish|end|score|cost|start)",
            r"\bcandidates\.append\(\s*\((?:finish|end|score|cost|start)",
            r"\bbest_(?:choice|candidate|candidates|finish|score)\b",
            r"\b(?:min_f|min_finish)\s*=\s*min\(",
            r"\b(?:finish|end|score|cost)\s*=.+\n.+\bready_(?:ops|choices|candidates)\.append",
            r"\b(?:finish|end|score|cost)\s*=.+\n.+\bcandidates\.append",
        ]
    )
    if not random_machine_choice or candidate_scoring:
        return []
    return [
        "agent_generated_solver: random_machine_choice_without_ready_machine_evaluation: "
        "operation-level ready-list construction must score each ready operation on each eligible machine "
        "before committing one operation; selecting one ready op and then rng.choice(eligible) is not sufficient"
    ]


def _has_operation_coverage_guard(text: str) -> bool:
    lowered = text.lower()
    coverage_terms = [
        "expected_ops",
        "total_ops",
        "operation_count",
        "all_ops",
        "required_ops",
        "seen_ops",
        "scheduled_ops",
        "missing_ops",
        "covered",
        "expected =",
        "seen =",
    ]
    duplicate_terms = [
        "duplicate",
        "seen_ops",
        "decoded_ops",
        "scheduled_ops",
        "scheduled = set",
        "scheduled.add",
        "unscheduled",
        "seen =",
        "seen.add",
        "decoded_ops.add",
        "scheduled_ops.add",
        "covered.add",
        "set(schedule",
        "len(set(",
        "scheduled += 1",
        "len(trial_schedule) == total_ops",
    ]
    schedule_terms = [
        "len(schedule)",
        "len(scheduled)",
        "len(result)",
        "len(decoded)",
        "len(validated)",
        "len(best_schedule)",
        "len(candidate_schedule)",
        "len(trial_schedule)",
        "len(scheduled_ops)",
        "len(decoded_ops)",
    ]
    set_equality_guard = any(
        term in lowered
        for term in [
            "set(schedule) != expected_ops",
            "set(schedule) == expected_ops",
            "set(schedule.keys()) != expected_ops",
            "set(schedule.keys()) == expected_ops",
            "set(schedule) != set(op_info)",
            "set(schedule) == set(op_info)",
            "set(schedule.keys()) != set(op_info)",
            "set(schedule.keys()) == set(op_info)",
            "seen_ops != expected_ops",
            "seen_ops == expected_ops",
            "scheduled != expected",
            "scheduled == expected",
            "scheduled != expected_ops",
            "scheduled == expected_ops",
            "decoded_ops != all_ops",
            "decoded_ops == all_ops",
            "decoded_ops != set(op_info)",
            "decoded_ops == set(op_info)",
            "seen != expected",
            "seen == expected",
            "len(scheduled_ops) != sum(job_ops.values())",
            "len(scheduled_ops) == sum(job_ops.values())",
            "len(schedule) != total_ops",
            "len(schedule) == total_ops",
            "len(scheduled) != total_ops",
            "len(scheduled) == total_ops",
            "len(scheduled) < total_ops",
        ]
    ) or bool(
        re.search(
            r"set\(\s*[a-z_][a-z0-9_]*(?:\.keys\(\))?\s*\)\s*(?:==|!=)\s*"
            r"[a-z_][a-z0-9_]*",
            lowered,
        )
    )
    unscheduled_guard = (
        "unscheduled" in lowered
        and ("if unscheduled" in lowered or "while unscheduled" in lowered)
        and "return none" in lowered
    )
    return (
        any(term in lowered for term in coverage_terms)
        and (any(term in lowered for term in schedule_terms) or set_equality_guard)
        and (
            any(term in lowered for term in duplicate_terms)
            or ("scheduled < total_ops" in lowered and "if not progress" in lowered and "return none" in lowered)
            or unscheduled_guard
        )
    )


def _has_machine_eligibility_guard(text: str) -> bool:
    lowered = text.lower()
    eligibility_terms = ["eligible", "candidates", "machine_options", "options", "candidate_machines"]
    machine_terms = ["machine_id", "machine"]
    rejection_terms = [
        "not in",
        "continue",
        "return none",
        "return false",
        "raise valueerror",
        "infeasible",
        "return any(",
        "if not check_machine_eligibility",
        "if not is_machine_eligible",
        "ineligible machine",
    ]
    return (
        any(term in lowered for term in eligibility_terms)
        and any(term in lowered for term in machine_terms)
        and any(term in lowered for term in rejection_terms)
    )


def _has_processing_duration_guard(text: str) -> bool:
    lowered = text.lower()
    duration_terms = [
        "duration",
        "processing_time",
        "proc_time",
        "proc",
        "op_durations",
        "dur",
        "pt",
        "eligible[machine_id]",
        "options[machine_id]",
    ]
    interval_terms = [
        "end - start",
        "e - s",
        "start + duration",
        "start + processing",
        "start + proc",
        "start + pt",
        "start + dur",
        "start_time + duration",
        "start_time + processing",
        "start_time + proc",
        "start_time + dur",
        "end_time - start_time",
    ]
    rejection_terms = ["return none", "return false", "raise valueerror", "raise runtimeerror", "continue", "assert", "!="]
    direct_duration_construction = bool(
        re.search(
            r"\b(?:end|end_time)\s*=\s*(?:start|start_time)\s*\+\s*"
            r"(?:duration|processing_time|proc_time|proc|dur|pt)\b",
            lowered,
        )
        and ("eligible" in lowered or "op_info" in lowered or "processing_time" in lowered)
    )
    record_duration_guard = bool(
        re.search(
            r"\b([a-z_][a-z0-9_]*)\[['\"]end['\"]\]\s*-\s*\1\[['\"]start['\"]\]"
            r"\s*!=\s*[^\n:]+",
            lowered,
        )
    ) or _has_record_duration_dataflow_guard(text)
    return (
        any(term in lowered for term in duration_terms)
        and (any(term in lowered for term in interval_terms) or record_duration_guard)
        and (direct_duration_construction or record_duration_guard or any(term in lowered for term in rejection_terms))
    )


def _has_record_duration_dataflow_guard(text: str) -> bool:
    """Recognize `delta = record[end] - record[start]; delta != expected` by behavior."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    interval_values: set[str] = set()
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.BinOp) or not isinstance(value.op, ast.Sub):
            continue
        if _record_field_name(value.left) != "end" or _record_field_name(value.right) != "start":
            continue
        left_base = value.left.value if isinstance(value.left, ast.Subscript) else None
        right_base = value.right.value if isinstance(value.right, ast.Subscript) else None
        if left_base is not None and right_base is not None and ast.dump(left_base) == ast.dump(right_base):
            interval_values.add(target.id)
    if not interval_values:
        return False
    return any(
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id in interval_values
        and any(isinstance(operator, (ast.NotEq, ast.Eq)) for operator in node.ops)
        for node in ast.walk(tree)
    )


def _record_field_name(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    slice_value = node.slice
    if isinstance(slice_value, ast.Constant) and isinstance(slice_value.value, str):
        return slice_value.value.lower()
    return None


def _has_job_precedence_guard(text: str) -> bool:
    lowered = text.lower()
    record_precedence_guard = bool(
        _has_record_start_before_record_end_comparison(lowered)
        and ("precedence" in lowered or re.search(r"for\s+\w*job\w*[^\n]*\bin\b", lowered))
    )
    return (
        ("job_ready" in lowered or "job_end" in lowered or "predecessor" in lowered or "prev_op" in lowered)
        and ("start" in lowered and "end" in lowered)
    ) or record_precedence_guard


def _has_machine_non_overlap_guard(text: str) -> bool:
    lowered = text.lower()
    machine_clock_guard = (
        "machine_ready" in lowered
        or "mach_ready" in lowered
        or "machine_end" in lowered
        or "machine_available" in lowered
        or "prev_end" in lowered
    )
    sequence_pair_guard = (
        ("machine_sequences" in lowered or "machine_seqs" in lowered or "by_machine" in lowered)
        and ("prev_op" in lowered or "previous_op" in lowered or "cur_op" in lowered or "current_op" in lowered)
        and ("end_times" in lowered or "end_time" in lowered)
        and ("start_times" in lowered or "start_time" in lowered)
        and ("> start" in lowered or "> start_times" in lowered or "overlap" in lowered)
        and ("return none" in lowered or "return false" in lowered or "raise" in lowered)
    )
    sorted_interval_guard = (
        "by_machine" in lowered
        and ("intervals.sort" in lowered or "sorted(intervals" in lowered)
        and "zip(intervals" in lowered
        and any(term in lowered for term in ["left[1] > right[0]", "prev[1] > curr[0]", "overlap"])
        and ("return none" in lowered or "return false" in lowered or "raise" in lowered)
    )
    sorted_record_guard = bool(
        _has_record_start_before_record_end_comparison(lowered)
        and "machine" in lowered
        and "overlap" in lowered
        and "sorted(" in lowered
        and "zip(" in lowered
    )
    return (
        (machine_clock_guard or sequence_pair_guard or sorted_interval_guard or sorted_record_guard)
        and ("start" in lowered and "end" in lowered)
    )


def _has_record_start_before_record_end_comparison(text: str) -> bool:
    return bool(
        re.search(
            r"\b[a-z_][a-z0-9_]*\[['\"]start['\"]\]\s*<\s*"
            r"[a-z_][a-z0-9_]*\[['\"]end['\"]\]",
            text,
        )
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
        "best_state",
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
        "raise runtimeerror",
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
    patterns = (
        rf"\b{removal_verbs}\b[\s\S]{{0,120}}\b{escaped}\b",
        rf"\b{escaped}\b[\s\S]{{0,120}}\b{removal_verbs}\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, proposal_text):
            context = proposal_text[max(0, match.start() - 80) : min(len(proposal_text), match.end() + 160)]
            if _is_operation_reinsertion_context(context):
                continue
            if _is_negated_removal_context(context):
                continue
            return True
    return False


def _is_operation_reinsertion_context(text: str) -> bool:
    lowered = text.replace("_", " ").lower()
    if not re.search(r"\b(remove|removing|removed)\s+(?:an?\s+|the\s+)?operations?\b", lowered):
        return False
    move_terms = ("insert", "inserting", "reinsert", "reinserting", "sequence", "neighborhood", "move")
    if not any(term in lowered for term in move_terms):
        return False
    return True


def _is_negated_removal_context(text: str) -> bool:
    lowered = text.replace("_", " ").lower()
    removal_verbs = (
        "remove",
        "removing",
        "removed",
        "delete",
        "deleting",
        "deleted",
        "eliminate",
        "eliminating",
        "disable",
        "disabling",
        "discard",
        "discarding",
        "drop",
        "dropping",
        "strip",
        "stripping",
        "undo",
        "revert",
    )
    verb_pattern = "|".join(re.escape(verb) for verb in removal_verbs)
    negation_pattern = r"(without|not|no|never)"
    return bool(
        re.search(rf"\b{negation_pattern}\b[\s\S]{{0,80}}\b({verb_pattern})\b", lowered)
        or re.search(rf"\b({verb_pattern})\b[\s\S]{{0,80}}\b{negation_pattern}\b", lowered)
    )


def _is_agent_generated_solver_context(context: dict[str, Any]) -> bool:
    return _contract_agent_generated_context(context)


def _incomplete_solution_risk_reasons(text: str) -> list[str]:
    compact = " ".join(text.split())
    reasons: list[str] = []
    if "_schedule" in text or "schedule" in text or "route" in text or "solution" in text:
        has_completion_guard = _has_explicit_completion_guard(text)
        if (" if new_schedule else 0" in compact or " if schedule else 0" in compact) and not has_completion_guard:
            reasons.append("empty_schedule_scored_as_zero")
        if (" if best_schedule else 0" in compact or " if candidate_schedule else 0" in compact) and not has_completion_guard:
            reasons.append("empty_candidate_scored_as_zero")
        if re.search(
            r"if\s+best_schedule\s+is\s+none\s*:\s*"
            r"(?:(?!\n\S).)*?"
            r"best_schedule\s*=\s*\[\s*\]"
            r"(?:(?!\n\S).)*?"
            r"best_makespan\s*=\s*0\b",
            text,
            re.I | re.S,
        ):
            reasons.append("empty_schedule_fallback_emitted")
        if "if best_machine is None:" in text and "break" in text and "return schedule" in text:
            if not has_completion_guard:
                reasons.append("decoder_can_return_partial_schedule")
    return reasons


def _has_explicit_completion_guard(text: str) -> bool:
    coverage_terms = [
        "len(schedule) == total_ops",
        "len(schedule) != total_ops",
        "len(new_schedule) == total_ops",
        "len(new_schedule) != total_ops",
        "len(scheduled_ops) == sum(job_ops.values())",
        "len(scheduled_ops) != sum(job_ops.values())",
        "expected_ops",
        "operation_count",
    ]
    invalid_terms = ["return None", "raise ValueError", "continue", "float('inf')", "math.inf"]
    return any(term in text for term in coverage_terms) and any(term in text for term in invalid_terms)
