"""算法语义审查：核对代码行为是否实现了 Main Agent 声明的方法。"""

from __future__ import annotations

import json
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from harness_agent.context.loader import load_context_dict
from harness_agent.deepseek_client import DeepSeekClient, is_deepseek_configured
from harness_agent.domains.pack import get_domain_pack


@dataclass(frozen=True)
class AlgorithmSemanticReviewRequest:
    """一次候选方法语义复核所需的源码、方向、Core 结果和上下文。"""

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
    """证据化语义结论；只有满足严格证据条件的 finding 才能 blocking。"""

    status: str
    accepted: bool
    summary: str
    findings: list[dict[str, Any]]
    reviewed_files: list[str]
    knowledge_paths: list[str]
    reviewer: str
    artifacts: dict[str, str]
    usage: dict[str, int] | None = None
    component_coverage: list[dict[str, Any]] = field(default_factory=list)
    coupled_group_coverage: list[dict[str, Any]] = field(default_factory=list)
    coverage_complete: bool = True

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
            "component_coverage": self.component_coverage,
            "coupled_group_coverage": self.coupled_group_coverage,
            "coverage_complete": self.coverage_complete,
        }


class AlgorithmSemanticReviewer(Protocol):
    """语义审查接口；审查者没有运行代码或修改候选的权限。"""

    def review(self, request: AlgorithmSemanticReviewRequest) -> AlgorithmSemanticReviewResult:
        ...


class EvidenceOnlySemanticReviewer:
    """Non-blocking fallback when no model-backed semantic reviewer is configured."""

    def review(self, request: AlgorithmSemanticReviewRequest) -> AlgorithmSemanticReviewResult:
        components, groups = required_method_contract(request.direction_plan)
        if components:
            return normalize_semantic_review(
                {"summary": "A complete method package was declared but no model-backed reviewer is configured."},
                sources={},
                knowledge={},
                required_components=components,
                required_coupled_groups=groups,
                reviewer="evidence_only_fallback",
            )
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

    def __init__(self, model: str = "deepseek-v4-pro", timeout_seconds: int = 300) -> None:
        self.model = model
        self.timeout_seconds = max(120, int(timeout_seconds))
        self.fallback = EvidenceOnlySemanticReviewer()

    def review(self, request: AlgorithmSemanticReviewRequest) -> AlgorithmSemanticReviewResult:
        """加载完整候选源码和当前知识契约，调用模型并验证其每条 finding。"""

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
            required_components, required_groups = required_method_contract(request.direction_plan)
            if not sources or not knowledge:
                if required_components:
                    unavailable = normalize_semantic_review(
                        {"summary": "Complete-method review lacks candidate source or its declared knowledge contract."},
                        sources=sources,
                        knowledge=knowledge,
                        required_components=required_components,
                        required_coupled_groups=required_groups,
                        reviewer="deepseek_algorithm_semantic_reviewer",
                    )
                    return write_semantic_review_artifacts(request.output_dir, unavailable)
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
            client = DeepSeekClient.from_env(
                model=self.model,
                timeout_seconds=self.timeout_seconds,
            )
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
                max_tokens=9000,
                json_mode=True,
                stream=True,
            )
            raw_path.write_text(response.content + "\n", encoding="utf-8")
            usage = response.usage
            usage_breakdown: dict[str, Any] = {"primary_review": response.usage}
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
                    max_tokens=9000,
                    json_mode=True,
                    stream=True,
                )
                retry_path.write_text(retry.content + "\n", encoding="utf-8")
                artifacts["json_retry_response"] = str(retry_path.resolve())
                raw = parse_json_object_response(retry.content)
                usage = merge_usage(response.usage, retry.usage)
                usage_breakdown["json_retry"] = retry.usage
            usage_breakdown["total"] = usage
            usage_path = request.output_dir / "algorithm_semantic_review_usage.json"
            usage_path.write_text(
                json.dumps(usage_breakdown, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            artifacts["usage"] = str(usage_path.resolve())
            # 模型返回的 finding 不能直接生效；下面会核对路径、源码行、
            # 知识原文、置信度、修复方案和行为测试是否全部真实存在。
            result = normalize_semantic_review(
                raw,
                sources=sources,
                knowledge=knowledge,
                required_components=required_components,
                required_coupled_groups=required_groups,
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


# ---------------------------------------------------------------------------
# Finding 证据归一化：把模型意见转换为可审计、可阻塞的严格事实。
# ---------------------------------------------------------------------------

def normalize_semantic_review(
    raw: Any,
    *,
    sources: dict[str, str],
    knowledge: dict[str, str],
    required_components: list[dict[str, Any]] | None = None,
    required_coupled_groups: list[dict[str, Any]] | None = None,
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
    component_coverage = normalize_component_coverage(
        payload.get("component_coverage"),
        required_components=required_components or [],
        sources=sources,
    )
    coupled_group_coverage = normalize_coupled_group_coverage(
        payload.get("coupled_group_coverage"),
        required_groups=required_coupled_groups or [],
        sources=sources,
    )
    coverage_complete = all(item.get("status") == "implemented" for item in component_coverage) and all(
        item.get("status") == "implemented" for item in coupled_group_coverage
    )
    status = (
        "repair_required"
        if blocking or not coverage_complete
        else "warning"
        if warning_count or rejected_count
        else "pass"
    )
    summary = str(payload.get("summary") or "").strip()
    if rejected_count:
        rejection_note = (
            f"Rejected {rejected_count} proposed semantic finding(s) because its source or knowledge evidence "
            "did not verify."
        )
        # A draft summary can repeat an allegation whose finding was discarded.
        # Keep only conclusions backed by findings that survived normalization.
        summary = (
            f"{rejection_note} Retained {len(blocking)} blocking and {warning_count} warning verified finding(s)."
            if findings
            else f"{rejection_note} No verified semantic mismatch remains."
        )
    if not summary:
        summary = (
            f"Verified {len(blocking)} blocking and {warning_count} warning semantic findings."
            if findings
            else "No evidence-backed semantic mismatch was found."
        )
    incomplete_components = [
        item.get("component_id") for item in component_coverage if item.get("status") != "implemented"
    ]
    incomplete_groups = [
        item.get("group_id") for item in coupled_group_coverage if item.get("status") != "implemented"
    ]
    if incomplete_components:
        summary = (
            f"Complete-method coverage is missing or partial for: {', '.join(str(item) for item in incomplete_components)}. "
            f"{summary}"
        )
    if incomplete_groups:
        summary = (
            f"Coupled method behavior is missing or partial for: {', '.join(str(item) for item in incomplete_groups)}. "
            f"{summary}"
        )
    return AlgorithmSemanticReviewResult(
        status=status,
        accepted=not blocking and coverage_complete,
        summary=summary[:1200],
        findings=findings,
        reviewed_files=sorted(sources),
        knowledge_paths=sorted(knowledge),
        reviewer=reviewer,
        artifacts=dict(artifacts or {}),
        usage=usage,
        component_coverage=component_coverage,
        coupled_group_coverage=coupled_group_coverage,
        coverage_complete=coverage_complete,
    )


def required_method_contract(
    direction_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回当前方法包声明的完整组件和耦合组，不解释任何具体算法。"""

    bundle = (
        direction_plan.get("implementation_bundle")
        if isinstance(direction_plan.get("implementation_bundle"), dict)
        else {}
    )
    components = [item for item in bundle.get("required_components") or [] if isinstance(item, dict)]
    groups = [item for item in bundle.get("coupled_groups") or [] if isinstance(item, dict)]
    return components, groups


def required_method_components(direction_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容已有调用方；新代码应使用 required_method_contract。"""

    return required_method_contract(direction_plan)[0]


def normalize_component_coverage(
    raw: Any,
    *,
    required_components: list[dict[str, Any]],
    sources: dict[str, str],
) -> list[dict[str, Any]]:
    """把 Reviewer 的正向覆盖证据对齐到知识包要求的完整组件集合。"""

    provided = {
        str(item.get("component_id") or "").strip(): item
        for item in raw or []
        if isinstance(item, dict) and str(item.get("component_id") or "").strip()
    }
    coverage: list[dict[str, Any]] = []
    for component in required_components:
        component_id = str(component.get("component_id") or "").strip()
        if not component_id:
            continue
        value = provided.get(component_id, {})
        required_behaviors = [
            str(item)[:1200] for item in component.get("required_behaviors") or [] if str(item).strip()
        ]
        raw_behavior_coverage = [
            item for item in value.get("behavior_coverage") or [] if isinstance(item, dict)
        ]
        by_behavior_index = {
            _bounded_int(item.get("behavior_index"), lower=1, upper=max(1, len(required_behaviors))): item
            for item in raw_behavior_coverage
        }
        behavior_coverage: list[dict[str, Any]] = []
        for behavior_index, behavior in enumerate(required_behaviors, start=1):
            behavior_value = by_behavior_index.get(behavior_index, {})
            requested_status = str(behavior_value.get("status") or "missing").strip().lower()
            status = requested_status if requested_status in {"implemented", "partial", "missing"} else "missing"
            source_path, line_start, line_end, source_excerpt = normalize_coverage_location(
                behavior_value,
                sources=sources,
            )
            evidence = str(behavior_value.get("evidence") or "").strip()
            if status == "implemented" and (not source_excerpt or len(evidence) < 12):
                status = "partial" if source_excerpt else "missing"
            elif status == "partial" and not source_excerpt:
                status = "missing"
            behavior_coverage.append(
                {
                    "behavior_index": behavior_index,
                    "behavior": behavior,
                    "status": status,
                    "source_path": source_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source_excerpt": source_excerpt[:4000],
                    "evidence": evidence[:1200],
                }
            )

        behavior_statuses = [item["status"] for item in behavior_coverage]
        if behavior_statuses and all(item == "implemented" for item in behavior_statuses):
            status = "implemented"
        elif any(item in {"implemented", "partial"} for item in behavior_statuses):
            status = "partial"
        else:
            status = "missing"
        first_evidence = next(
            (item for item in behavior_coverage if item.get("source_excerpt")),
            {},
        )
        reported_missing = [
            str(item)[:1200] for item in value.get("missing_behaviors") or [] if str(item).strip()
        ]
        uncovered_behaviors = [
            str(item.get("behavior") or "")
            for item in behavior_coverage
            if item.get("status") != "implemented"
        ]
        missing_behaviors = [] if status == "implemented" else list(
            dict.fromkeys([*uncovered_behaviors, *reported_missing])
        )
        coverage.append(
            {
                "component_id": component_id,
                "title": str(component.get("title") or component_id)[:240],
                "status": status,
                "required_behaviors": required_behaviors,
                "evidence_required": str(component.get("evidence_required") or "")[:1600],
                "behavior_coverage": behavior_coverage,
                "source_path": first_evidence.get("source_path"),
                "line_start": first_evidence.get("line_start"),
                "line_end": first_evidence.get("line_end"),
                "source_excerpt": str(first_evidence.get("source_excerpt") or "")[:4000],
                "evidence": str(value.get("evidence") or "")[:1200],
                "missing_behaviors": missing_behaviors,
            }
        )
    return coverage


def normalize_coverage_location(
    value: dict[str, Any],
    *,
    sources: dict[str, str],
) -> tuple[str | None, int | None, int | None, str]:
    """解析一条覆盖证据的位置；任意越界或空白片段都不算源码证据。"""

    source_path = resolve_review_path(value.get("source_path"), sources)
    line_start = _bounded_int(value.get("line_start"), lower=1, upper=1_000_000)
    line_end = _bounded_int(value.get("line_end"), lower=line_start, upper=line_start + 80)
    source_excerpt = ""
    if source_path is not None:
        source_lines = sources[source_path].splitlines()
        if line_start <= len(source_lines):
            line_end = min(line_end, len(source_lines))
            source_excerpt = "\n".join(source_lines[line_start - 1 : line_end]).strip()
    if not source_excerpt:
        return source_path, None, None, ""
    return source_path, line_start, line_end, source_excerpt


def normalize_coupled_group_coverage(
    raw: Any,
    *,
    required_groups: list[dict[str, Any]],
    sources: dict[str, str],
) -> list[dict[str, Any]]:
    """验证组件间的行为闭环，避免“零件都有、调用链没接上”。"""

    provided = {
        str(item.get("group_id") or "").strip(): item
        for item in raw or []
        if isinstance(item, dict) and str(item.get("group_id") or "").strip()
    }
    coverage: list[dict[str, Any]] = []
    for group in required_groups:
        group_id = str(group.get("group_id") or "").strip()
        if not group_id:
            continue
        value = provided.get(group_id, {})
        requested_status = str(value.get("status") or "missing").strip().lower()
        status = requested_status if requested_status in {"implemented", "partial", "missing"} else "missing"
        source_path, line_start, line_end, source_excerpt = normalize_coverage_location(value, sources=sources)
        evidence = str(value.get("evidence") or "").strip()
        missing_behavior = str(value.get("missing_behavior") or "").strip()
        if status == "implemented" and (not source_excerpt or len(evidence) < 12 or missing_behavior):
            status = "partial" if source_excerpt else "missing"
        elif status == "partial" and not source_excerpt:
            status = "missing"
        coverage.append(
            {
                "group_id": group_id,
                "component_ids": [
                    str(item)[:160] for item in group.get("component_ids") or [] if str(item).strip()
                ],
                "rule": str(group.get("rule") or "")[:1600],
                "status": status,
                "source_path": source_path,
                "line_start": line_start,
                "line_end": line_end,
                "source_excerpt": source_excerpt[:4000],
                "evidence": evidence[:1600],
                "missing_behavior": (
                    "" if status == "implemented" else str(missing_behavior or group.get("rule") or "")[:1600]
                ),
            }
        )
    return coverage


def verified_semantic_finding(
    value: Any,
    *,
    sources: dict[str, str],
    knowledge: dict[str, str],
) -> dict[str, Any] | None:
    """验证单条 finding；任一关键证据缺失时整条丢弃。"""

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


# ---------------------------------------------------------------------------
# Prompt 与有界材料加载
# ---------------------------------------------------------------------------

def semantic_review_prompt(
    *,
    direction_plan: dict[str, Any],
    candidate_summary: dict[str, Any],
    sources: dict[str, str],
    knowledge: dict[str, str],
) -> str:
    numbered_sources = {
        path: "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(text.splitlines(), start=1)
        )
        for path, text in sources.items()
    }
    return f"""
Review the candidate algorithm implementation after Core legality evaluation and before promotion.

Return JSON only:
{{
  "summary": "short evidence-based verdict",
  "component_coverage": [
    {{
      "component_id": "exact id from direction_plan.implementation_bundle.required_components",
      "status": "implemented | partial | missing",
      "source_path": "exact path key from Candidate source, required when implemented",
      "line_start": 1,
      "line_end": 1,
      "evidence": "how reachable code implements this complete component",
      "behavior_coverage": [
        {{
          "behavior_index": 1,
          "status": "implemented | partial | missing",
          "source_path": "exact path key",
          "line_start": 1,
          "line_end": 1,
          "evidence": "how these lines implement this exact required_behaviors entry"
        }}
      ],
      "missing_behaviors": ["remaining behavior when partial or missing"]
    }}
  ],
  "coupled_group_coverage": [
    {{
      "group_id": "exact id from direction_plan.implementation_bundle.coupled_groups",
      "status": "implemented | partial | missing",
      "source_path": "exact path key proving the connected runtime path",
      "line_start": 1,
      "line_end": 1,
      "evidence": "how the required components form one reachable closed loop",
      "missing_behavior": "remaining broken connection when partial or missing"
    }}
  ],
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
- Treat function, class, and variable names only as source-navigation labels. Names are never positive or negative evidence.
- A retrieved method package is reference material, not proof by itself. When direction_plan.implementation_bundle is absent,
  derive required behavior from explicit direction claims. When that bundle is present with mode=complete_method_package,
  it is an explicit complete-method claim: audit every required component exactly once and check every coupled group.
- Emit one behavior_coverage row for every required_behaviors entry, using its 1-based behavior_index. Mark the component
  implemented only when every behavior row has exact reachable source lines and a concrete explanation. Mark it partial when
  only part is reachable, and missing when no implementation path exists. Do not omit components or behavior rows.
- Audit every coupled_group exactly once. A group is implemented only when the required components are connected in the same
  reachable runtime path; separately defined helpers or mismatched generation/scoring/application identities are partial.
- When no complete implementation_bundle was declared and source labels overclaim a stronger method than the direction
  requests, prefer a bounded correction to the label. A declared complete bundle cannot pass by renaming or narrowing its claim.
- Check that implementation behavior matches its claimed method through reachable call paths, values consumed by decisions,
  before/after state transitions, acceptance and rollback behavior, and observable tests. A method-named helper that is dead,
  returns a constant, or computes a value that is never consumed does not implement the method. An arbitrarily named helper
  that performs the required behavior does implement it.
- When a supplied contract distinguishes two states, attributes, graph properties, bounds, or acceptance rules, verify that the reachable implementation preserves that distinction.
- Do not use benchmark target values or previous solution files as method knowledge.
- Do not invent missing requirements. For free-form findings, incomplete evidence cannot block. For a declared component or
  coupled group, incomplete implementation evidence must be recorded as partial or missing in the coverage matrix.

Knowledge contracts (stable across same-direction repairs):
{json.dumps(knowledge, ensure_ascii=False, indent=2)}

Direction plan:
{json.dumps(direction_plan, ensure_ascii=False, indent=2)}

Core candidate summary:
{json.dumps(candidate_summary, ensure_ascii=False, indent=2)}

Candidate source with authoritative line numbers (complete bounded files):
{json.dumps(numbered_sources, ensure_ascii=False, indent=2)}
""".strip()


def semantic_review_json_repair_prompt(
    draft: str,
    *,
    sources: dict[str, str] | None = None,
    knowledge: dict[str, str] | None = None,
) -> str:
    return f"""
Convert the draft review below into the exact JSON schema shown here. Preserve the draft component and coupled-group coverage,
and only findings already identified in the draft. Do not add new findings, commentary, markdown, or code fences.

Every finding must preserve an exact source_path and knowledge_path key already present in the draft. Do not re-analyze
the source or knowledge; deterministic normalization will reject missing paths, invalid lines, and inexact quotes.

{{
  "summary": "short evidence-based verdict",
  "component_coverage": [
    {{
      "component_id": "exact required component id from the draft",
      "status": "implemented | partial | missing",
      "source_path": "exact source path when implemented",
      "line_start": 1,
      "line_end": 1,
      "evidence": "reachable behavior evidence",
      "behavior_coverage": [
        {{
          "behavior_index": 1,
          "status": "implemented | partial | missing",
          "source_path": "exact source path when implemented",
          "line_start": 1,
          "line_end": 1,
          "evidence": "evidence for this exact behavior"
        }}
      ],
      "missing_behaviors": []
    }}
  ],
  "coupled_group_coverage": [
    {{
      "group_id": "exact required coupled group id from the draft",
      "status": "implemented | partial | missing",
      "source_path": "exact source path when implemented",
      "line_start": 1,
      "line_end": 1,
      "evidence": "reachable coupling evidence",
      "missing_behavior": ""
    }}
  ],
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
{draft[:40_000]}

Allowed source path keys:
{json.dumps(sorted((sources or {}).keys()), ensure_ascii=False, indent=2)}

Allowed knowledge path keys:
{json.dumps(sorted((knowledge or {}).keys()), ensure_ascii=False, indent=2)}
""".strip()


def load_review_sources(
    *,
    context: dict[str, Any],
    worktree_path: Path,
    changed_files: list[str],
    max_total_chars: int = 600_000,
) -> dict[str, str]:
    """加载 solver 入口和本轮 Python 改动；超预算时拒绝做残缺源码审查。"""

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
    """只加载 Context Packet/方向计划已允许的知识契约。"""

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
    active_package = (
        context.get("active_method_package")
        if isinstance(context.get("active_method_package"), dict)
        else {}
    )
    complete_bundle = isinstance(direction_plan.get("implementation_bundle"), dict)
    if complete_bundle and active_package:
        # 参考实现供 Coding Agent 学习，不应再作为 Reviewer 的要求全文重传。
        # Reviewer 只读取方法包显式声明的语义资产和完整契约继承链。
        requested_paths = [
            *(active_package.get("implementation_contract_assets") or []),
            *(active_package.get("semantic_assets") or []),
        ]
        if not requested_paths:
            requested_paths = list(direction_plan.get("knowledge_paths") or [])
    else:
        requested_paths = list(direction_plan.get("knowledge_paths") or [])
    for raw_path in requested_paths:
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
        component_coverage=result.component_coverage,
        coupled_group_coverage=result.coupled_group_coverage,
        coverage_complete=result.coverage_complete,
    )
    json_path.write_text(json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Algorithm Semantic Review",
        "",
        f"- Status: `{result.status}`",
        f"- Accepted: `{result.accepted}`",
        f"- Reviewer: `{result.reviewer}`",
        f"- Complete method coverage: `{result.coverage_complete}`",
        f"- Summary: {result.summary}",
        "",
        "## Component Coverage",
        "",
    ]
    if not result.component_coverage:
        lines.append("- No complete-method implementation bundle was declared.")
    for item in result.component_coverage:
        source = item.get("source_path")
        line = item.get("line_start")
        source_text = f"{source}:{line}" if source and line else "no source evidence"
        lines.append(f"- `{item.get('component_id')}`: **{item.get('status')}** ({source_text})")
    lines.extend(
        [
        "",
        "## Coupled Group Coverage",
        "",
        ]
    )
    if not result.coupled_group_coverage:
        lines.append("- No coupled groups were declared.")
    for item in result.coupled_group_coverage:
        source = item.get("source_path")
        line = item.get("line_start")
        source_text = f"{source}:{line}" if source and line else "no source evidence"
        lines.append(f"- `{item.get('group_id')}`: **{item.get('status')}** ({source_text})")
    lines.extend(
        [
        "",
        "## Findings",
        "",
        ]
    )
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
    # Markdown bullets and hard-wrapped lines are presentation, not evidence
    # content.  Keep wording and punctuation strict while ignoring that layout.
    without_list_markers = re.sub(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)", "", value)
    return re.sub(r"\s+", " ", without_list_markers).strip().lower()


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
