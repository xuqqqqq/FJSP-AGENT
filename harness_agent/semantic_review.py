from __future__ import annotations

import json
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .context_loader import load_context_dict
from .deepseek_client import DeepSeekClient, is_deepseek_configured
from .domain_pack import get_domain_pack


@dataclass(frozen=True)
class AlgorithmSemanticReviewRequest:
    round_index: int
    attempt_index: int
    context_packet_path: Path
    worktree_path: Path
    changed_files: list[str]
    direction_plan: dict[str, Any]
    candidate_summary: dict[str, Any]
    output_dir: Path


@dataclass(frozen=True)
class AlgorithmSemanticReviewResult:
    status: str
    accepted: bool
    summary: str
    findings: list[dict[str, Any]]
    reviewed_files: list[str]
    knowledge_paths: list[str]
    reviewer: str
    artifacts: dict[str, str]
    usage: dict[str, int] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "accepted": self.accepted,
            "summary": self.summary,
            "findings": self.findings,
            "reviewed_files": self.reviewed_files,
            "knowledge_paths": self.knowledge_paths,
            "reviewer": self.reviewer,
            "artifacts": self.artifacts,
            "usage": self.usage or {},
        }


class AlgorithmSemanticReviewer(Protocol):
    def review(self, request: AlgorithmSemanticReviewRequest) -> AlgorithmSemanticReviewResult:
        ...


class EvidenceOnlySemanticReviewer:
    """Non-blocking fallback when no model-backed semantic reviewer is configured."""

    def review(self, request: AlgorithmSemanticReviewRequest) -> AlgorithmSemanticReviewResult:
        return AlgorithmSemanticReviewResult(
            status="skipped",
            accepted=True,
            summary="No model-backed algorithm semantic reviewer was configured.",
            findings=[],
            reviewed_files=[],
            knowledge_paths=[],
            reviewer="evidence_only_fallback",
            artifacts={},
        )


class DeepSeekAlgorithmSemanticReviewer:
    """Review method claims against full candidate source and retrieved knowledge."""

    def __init__(self, model: str = "deepseek-v4-pro") -> None:
        self.model = model
        self.fallback = EvidenceOnlySemanticReviewer()

    def review(self, request: AlgorithmSemanticReviewRequest) -> AlgorithmSemanticReviewResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        if not is_deepseek_configured():
            return self.fallback.review(request)

        artifacts: dict[str, str] = {}
        try:
            context = load_context_dict(request.context_packet_path)
            sources = load_review_sources(
                context=context,
                worktree_path=request.worktree_path,
                changed_files=request.changed_files,
            )
            knowledge = load_review_knowledge(
                context=context,
                direction_plan=request.direction_plan,
            )
            if not sources or not knowledge:
                return AlgorithmSemanticReviewResult(
                    status="skipped",
                    accepted=True,
                    summary="Semantic review needs both candidate source and knowledge contracts.",
                    findings=[],
                    reviewed_files=sorted(sources),
                    knowledge_paths=sorted(knowledge),
                    reviewer="deepseek_algorithm_semantic_reviewer",
                    artifacts={},
                )

            prompt = semantic_review_prompt(
                direction_plan=request.direction_plan,
                candidate_summary=request.candidate_summary,
                sources=sources,
                knowledge=knowledge,
            )
            prompt_path = request.output_dir / "algorithm_semantic_review_prompt.md"
            raw_path = request.output_dir / "algorithm_semantic_review_raw.json"
            prompt_path.write_text(prompt, encoding="utf-8")
            artifacts.update(
                {
                    "prompt": str(prompt_path.resolve()),
                    "raw_response": str(raw_path.resolve()),
                }
            )
            client = DeepSeekClient.from_env(model=self.model)
            response = client.chat_with_usage(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are an algorithm implementation reviewer. Compare method claims with the full "
                            "candidate source and cited knowledge contracts. Return valid JSON only. Never write code."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
                max_tokens=6000,
                json_mode=True,
            )
            raw_path.write_text(response.content + "\n", encoding="utf-8")
            usage = response.usage
            try:
                raw = parse_json_object_response(response.content)
            except json.JSONDecodeError:
                retry_path = request.output_dir / "algorithm_semantic_review_json_retry.json"
                retry = client.chat_with_usage(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are a JSON formatter. Do not analyze, explain, or use markdown. "
                                "Return one valid JSON object only."
                            ),
                        },
                        {
                            "role": "user",
                            "content": semantic_review_json_repair_prompt(
                                response.content,
                                sources=sources,
                                knowledge=knowledge,
                            ),
                        },
                    ],
                    temperature=0.0,
                    max_tokens=3500,
                    json_mode=True,
                )
                retry_path.write_text(retry.content + "\n", encoding="utf-8")
                artifacts["json_retry_response"] = str(retry_path.resolve())
                raw = parse_json_object_response(retry.content)
                usage = merge_usage(response.usage, retry.usage)
            result = normalize_semantic_review(
                raw,
                sources=sources,
                knowledge=knowledge,
                reviewer="deepseek_algorithm_semantic_reviewer",
                usage=usage,
                artifacts=artifacts,
            )
            return write_semantic_review_artifacts(request.output_dir, result)
        except Exception as exc:  # noqa: BLE001 - reviewer failure must not replace Core authority.
            exception_path = request.output_dir / "algorithm_semantic_review_exception.txt"
            exception_path.write_text(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                encoding="utf-8",
            )
            return AlgorithmSemanticReviewResult(
                status="unavailable",
                accepted=False,
                summary=f"Semantic reviewer unavailable: {exc}",
                findings=[],
                reviewed_files=[],
                knowledge_paths=[],
                reviewer="deepseek_algorithm_semantic_reviewer",
                artifacts={**artifacts, "exception": str(exception_path.resolve())},
            )


def normalize_semantic_review(
    raw: Any,
    *,
    sources: dict[str, str],
    knowledge: dict[str, str],
    reviewer: str,
    usage: dict[str, int] | None = None,
    artifacts: dict[str, str] | None = None,
) -> AlgorithmSemanticReviewResult:
    payload = raw if isinstance(raw, dict) else {}
    requested_findings = [item for item in payload.get("findings") or [] if isinstance(item, dict)]
    findings: list[dict[str, Any]] = []
    for value in requested_findings:
        finding = verified_semantic_finding(value, sources=sources, knowledge=knowledge)
        if finding:
            findings.append(finding)
        if len(findings) >= 12:
            break
    blocking = [item for item in findings if item.get("blocking")]
    warning_count = sum(1 for item in findings if not item.get("blocking"))
    rejected_count = len(requested_findings) - len(findings)
    status = "repair_required" if blocking else ("warning" if warning_count or rejected_count else "pass")
    summary = str(payload.get("summary") or "").strip()
    if rejected_count:
        summary = (
            f"Rejected {rejected_count} semantic finding(s) whose source or knowledge evidence did not verify. "
            f"{summary}"
        ).strip()
    if not summary:
        summary = (
            f"Verified {len(blocking)} blocking and {warning_count} warning semantic findings."
            if findings
            else "No evidence-backed semantic mismatch was found."
        )
    return AlgorithmSemanticReviewResult(
        status=status,
        accepted=not blocking,
        summary=summary[:1200],
        findings=findings,
        reviewed_files=sorted(sources),
        knowledge_paths=sorted(knowledge),
        reviewer=reviewer,
        artifacts=dict(artifacts or {}),
        usage=usage,
    )


def verified_semantic_finding(
    value: Any,
    *,
    sources: dict[str, str],
    knowledge: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source_path = resolve_review_path(value.get("source_path"), sources)
    knowledge_path = resolve_review_path(value.get("knowledge_path"), knowledge)
    if source_path is None or knowledge_path is None:
        return None
    line_start = _bounded_int(value.get("line_start"), lower=1, upper=1_000_000)
    line_end = _bounded_int(value.get("line_end"), lower=line_start, upper=line_start + 80)
    source_lines = sources[source_path].splitlines()
    if line_start > len(source_lines):
        return None
    line_end = min(line_end, len(source_lines))
    source_excerpt = "\n".join(source_lines[line_start - 1 : line_end]).strip()
    if not source_excerpt:
        return None
    knowledge_quote = str(value.get("knowledge_quote") or "").strip()
    if len(knowledge_quote) < 12 or normalize_quote(knowledge_quote) not in normalize_quote(knowledge[knowledge_path]):
        return None
    confidence = _bounded_float(value.get("confidence"), lower=0.0, upper=1.0)
    severity = str(value.get("severity") or "warning").strip().lower()
    repair = str(value.get("repair") or "").strip()
    required_test = str(value.get("required_test") or "").strip()
    requested_blocking = severity in {"blocking", "high", "critical", "repair_required"}
    blocking = bool(
        requested_blocking
        and confidence >= 0.8
        and len(repair) >= 12
        and len(required_test) >= 12
    )
    return {
        "finding_id": str(value.get("finding_id") or f"semantic_{len(source_excerpt)}_{line_start}")[:120],
        "category": str(value.get("category") or "method_semantics")[:120],
        "severity": "blocking" if blocking else "warning",
        "blocking": blocking,
        "confidence": confidence,
        "claim": str(value.get("claim") or "")[:500],
        "source_path": source_path,
        "line_start": line_start,
        "line_end": line_end,
        "source_excerpt": source_excerpt[:4000],
        "knowledge_path": knowledge_path,
        "knowledge_quote": knowledge_quote[:1600],
        "explanation": str(value.get("explanation") or "")[:1600],
        "repair": repair[:1600],
        "required_test": required_test[:1200],
    }


def semantic_review_prompt(
    *,
    direction_plan: dict[str, Any],
    candidate_summary: dict[str, Any],
    sources: dict[str, str],
    knowledge: dict[str, str],
) -> str:
    return f"""
Review the candidate algorithm implementation after Core legality evaluation and before promotion.

Return JSON only:
{{
  "summary": "short evidence-based verdict",
  "findings": [
    {{
      "finding_id": "stable id",
      "category": "state_invariant | move_memory | structural_exactness | operator_fidelity | runtime_scaling | other",
      "severity": "blocking | warning",
      "confidence": 0.0,
      "claim": "the method claim being checked",
      "source_path": "exact path key from Candidate source",
      "line_start": 1,
      "line_end": 1,
      "knowledge_path": "exact path key from Knowledge contracts",
      "knowledge_quote": "exact quote copied from that knowledge contract",
      "explanation": "why the cited source violates or weakens the cited contract",
      "repair": "bounded method-level repair, not replacement solver code",
      "required_test": "behavioral test that proves the repair"
    }}
  ]
}}

Rules:
- Review algorithm semantics, not formatting, score magnitude, or evaluator legality.
- A blocking finding requires confidence >= 0.8, exact source lines, an exact knowledge quote, a bounded repair, and a behavioral test.
- Derive every semantic requirement from the supplied knowledge contracts; the generic reviewer has no built-in problem-family algorithm rules.
- Check that implementation behavior matches its claimed method, not merely that named functions exist.
- When a supplied contract distinguishes two states, attributes, graph properties, bounds, or acceptance rules, verify that the reachable implementation preserves that distinction.
- Do not use benchmark target values or previous solution files as method knowledge.
- Do not invent missing requirements. If evidence is incomplete, emit a warning or no finding.

Direction plan:
{json.dumps(direction_plan, ensure_ascii=False, indent=2)}

Core candidate summary:
{json.dumps(candidate_summary, ensure_ascii=False, indent=2)}

Candidate source (complete bounded files):
{json.dumps(sources, ensure_ascii=False, indent=2)}

Knowledge contracts:
{json.dumps(knowledge, ensure_ascii=False, indent=2)}
""".strip()


def semantic_review_json_repair_prompt(
    draft: str,
    *,
    sources: dict[str, str] | None = None,
    knowledge: dict[str, str] | None = None,
) -> str:
    numbered_sources = {
        path: "\n".join(f"{line_number}: {line}" for line_number, line in enumerate(text.splitlines(), start=1))
        for path, text in (sources or {}).items()
    }
    return f"""
Convert the draft review below into the exact JSON schema shown here. Preserve only findings already identified in
the draft. Do not add new findings, commentary, markdown, or code fences.

Every finding must use an exact source_path and knowledge_path key supplied below. Derive line_start and line_end from
the numbered source, and copy knowledge_quote exactly from the supplied knowledge text.

{{
  "summary": "short evidence-based verdict",
  "findings": [
    {{
      "finding_id": "stable id",
      "category": "state_invariant | move_memory | structural_exactness | operator_fidelity | runtime_scaling | other",
      "severity": "blocking | warning",
      "confidence": 0.0,
      "claim": "the method claim being checked",
      "source_path": "source path from the draft",
      "line_start": 1,
      "line_end": 1,
      "knowledge_path": "knowledge path from the draft",
      "knowledge_quote": "exact contract quote from the draft",
      "explanation": "evidence-based explanation from the draft",
      "repair": "bounded repair from the draft",
      "required_test": "behavioral test from the draft"
    }}
  ]
}}

Draft review:
{draft[:80_000]}

Numbered candidate source:
{json.dumps(numbered_sources, ensure_ascii=False, indent=2)}

Allowed knowledge contracts:
{json.dumps(knowledge or {}, ensure_ascii=False, indent=2)}
""".strip()


def load_review_sources(
    *,
    context: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
    max_total_chars: int = 600_000,
) -> dict[str, str]:
    protocol = context.get("evaluator_protocol") if isinstance(context.get("evaluator_protocol"), dict) else {}
    solver_command = str(protocol.get("solver_command_template") or "")
    candidates = {path.as_posix() for path in relative_python_paths(solver_command)}
    candidates.update(
        str(path).replace("\\", "/")
        for path in changed_files
        if str(path).replace("\\", "/").endswith(".py")
    )
    result: dict[str, str] = {}
    total = 0
    root = worktree_path.resolve()
    for relative in sorted(candidates):
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if total + len(text) > max_total_chars:
            raise ValueError(
                "algorithm semantic review source budget exceeded; refusing a partial-source review"
            )
        result[relative] = text
        total += len(text)
    return result


def load_review_knowledge(
    *,
    context: dict[str, Any],
    direction_plan: dict[str, Any],
    max_total_chars: int = 300_000,
) -> dict[str, str]:
    allowed: dict[str, Path] = {}
    for record in context.get("knowledge_cards") or []:
        if not isinstance(record, dict):
            continue
        raw_path = str(record.get("path") or "").strip()
        if raw_path:
            path = Path(raw_path).resolve()
            allowed[normalize_review_path(raw_path)] = path
            allowed[normalize_review_path(path)] = path
    family = str((context.get("task") or {}).get("problem_family") or "")
    pack = get_domain_pack(family)
    if pack is not None:
        for path in pack.semantic_review_cards:
            allowed[normalize_review_path(path)] = path

    selected: list[Path] = []
    for raw_path in direction_plan.get("knowledge_paths") or []:
        normalized = normalize_review_path(raw_path)
        path = allowed.get(normalized)
        if path is not None and path not in selected:
            selected.append(path)
    if pack is not None:
        for path in pack.semantic_review_cards:
            if path not in selected:
                selected.append(path)

    result: dict[str, str] = {}
    total = 0
    for path in selected:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if total + len(text) > max_total_chars:
            raise ValueError(
                "algorithm semantic review knowledge budget exceeded; refusing a partial-contract review"
            )
        result[normalize_review_path(path)] = text
        total += len(text)
    return result


def write_semantic_review_artifacts(
    output_dir: Path,
    result: AlgorithmSemanticReviewResult,
) -> AlgorithmSemanticReviewResult:
    json_path = output_dir / "algorithm_semantic_review.json"
    report_path = output_dir / "algorithm_semantic_review.md"
    artifacts = {
        **result.artifacts,
        "review_json": str(json_path.resolve()),
        "review_report": str(report_path.resolve()),
    }
    result = AlgorithmSemanticReviewResult(
        status=result.status,
        accepted=result.accepted,
        summary=result.summary,
        findings=result.findings,
        reviewed_files=result.reviewed_files,
        knowledge_paths=result.knowledge_paths,
        reviewer=result.reviewer,
        artifacts=artifacts,
        usage=result.usage,
    )
    json_path.write_text(json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Algorithm Semantic Review",
        "",
        f"- Status: `{result.status}`",
        f"- Accepted: `{result.accepted}`",
        f"- Reviewer: `{result.reviewer}`",
        f"- Summary: {result.summary}",
        "",
        "## Findings",
        "",
    ]
    if not result.findings:
        lines.append("- No evidence-backed semantic mismatch was found.")
    for item in result.findings:
        lines.extend(
            [
                f"### {item.get('severity')} - {item.get('category')}",
                "",
                f"- Claim: {item.get('claim')}",
                f"- Source: `{item.get('source_path')}:{item.get('line_start')}`",
                f"- Knowledge: `{item.get('knowledge_path')}`",
                f"- Explanation: {item.get('explanation')}",
                f"- Repair: {item.get('repair')}",
                f"- Required test: {item.get('required_test')}",
                "",
            ]
        )
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return result


def relative_python_paths(command: str) -> list[Path]:
    result: list[Path] = []
    for match in re.finditer(r"(?P<path>[A-Za-z0-9_./\\-]+\.py)", command):
        path = Path(match.group("path").replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or path in result:
            continue
        result.append(path)
    return result


def normalize_review_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def parse_json_object_response(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as original:
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced is None:
            raise original
        payload = json.loads(fenced.group(1))
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("semantic review response is not a JSON object", text, 0)
    return payload


def merge_usage(*values: dict[str, int] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        for key, amount in (value or {}).items():
            result[str(key)] = result.get(str(key), 0) + int(amount)
    return result


def resolve_review_path(value: Any, available: dict[str, str]) -> str | None:
    requested = normalize_review_path(value).strip("/")
    if not requested:
        return None
    exact = [
        key
        for key in available
        if normalize_review_path(key).strip("/").casefold() == requested.casefold()
    ]
    if len(exact) == 1:
        return exact[0]

    suffix = f"/{requested.casefold()}"
    matches = [
        key
        for key in available
        if normalize_review_path(key).strip("/").casefold().endswith(suffix)
    ]
    return matches[0] if len(matches) == 1 else None


def normalize_quote(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _bounded_int(value: Any, *, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = lower
    return max(lower, min(upper, parsed))


def _bounded_float(value: Any, *, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = lower
    return max(lower, min(upper, parsed))
