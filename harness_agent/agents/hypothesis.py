"""实验假设图、轮间反馈和分层经验沉淀。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HypothesisRecord:
    """One evaluated strategy hypothesis in the agent evolution loop."""

    hypothesis_id: str
    parent_id: str | None
    round_index: int
    source: str
    solver: str
    status: str
    score_metric: str | None
    score_value: float | None
    delta_from_parent: float | None
    summary: dict[str, Any]
    artifacts: dict[str, str]
    note: str
    candidate_id: str | None = None
    candidate_results: list[dict[str, Any]] | None = None


class HypothesisLedger:
    """Append-only JSONL ledger for strategy evolution records."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: HypothesisRecord) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True) + "\n")

    def list_records(self) -> list[HypothesisRecord]:
        if not self.path.exists():
            return []
        records: list[HypothesisRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            records.append(HypothesisRecord(**payload))
        return records


@dataclass(frozen=True)
class HypothesisDecision:
    hypothesis_id: str
    decision: str
    reason: str
    round_index: int
    score_metric: str | None
    score_value: float | None
    delta_from_parent: float | None
    parent_id: str | None
    candidate_id: str | None


def summarize_hypothesis_graph(
    records: list[HypothesisRecord],
    *,
    max_promoted: int = 3,
    max_pruned: int = 8,
) -> dict[str, Any]:
    """Summarize evaluated hypotheses into promote/prune/mutate guidance.

    The graph summary is deliberately advisory.  It helps the next strategy or
    coding worker decide what to preserve or vary, while the evaluator remains
    the only source of candidate acceptance.
    """

    if not records:
        return {
            "schema_version": 1,
            "record_count": 0,
            "comparable_count": 0,
            "best_hypothesis_id": None,
            "best_score_value": None,
            "decisions": [],
            "decision_counts": {},
            "mutation_guidance": [
                "No evaluated hypotheses are available yet; explore diverse baseline rules and keep edits reversible."
            ],
        }

    comparable = [
        record
        for record in records
        if record.status == "evaluated" and isinstance(record.score_value, (int, float))
    ]
    ordered = sorted(comparable, key=lambda item: (float(item.score_value), -item.round_index), reverse=True)
    promoted_ids = {record.hypothesis_id for record in ordered[: max(1, max_promoted)]}
    ancestor_ids = promoted_ancestor_ids(records, promoted_ids)
    best = ordered[0] if ordered else None

    decisions: list[HypothesisDecision] = []
    prune_count = 0
    for record in records:
        decision = "mutate"
        reason = "Comparable but not elite; use as a mutation parent rather than copying it unchanged."
        if record.status != "evaluated" or record.score_value is None:
            decision = "prune"
            reason = "Missing evaluator-backed comparable score."
        elif record.hypothesis_id in promoted_ids:
            decision = "promote"
            reason = "Elite evaluator-backed comparable score."
        elif record.hypothesis_id in ancestor_ids:
            decision = "mutate"
            reason = "Ancestor of a promoted hypothesis; preserve as a mutation parent."
        elif record.delta_from_parent is not None and record.delta_from_parent < -1e-9 and prune_count < max_pruned:
            decision = "prune"
            reason = "Worse than its parent according to the comparable score."
        elif best and float(record.score_value) < float(best.score_value) and prune_count < max_pruned:
            decision = "prune"
            reason = "Dominated by stronger historical hypotheses under the comparable score."
        if decision == "prune":
            prune_count += 1
        decisions.append(
            HypothesisDecision(
                hypothesis_id=record.hypothesis_id,
                decision=decision,
                reason=reason,
                round_index=record.round_index,
                score_metric=record.score_metric,
                score_value=float(record.score_value) if isinstance(record.score_value, (int, float)) else None,
                delta_from_parent=(
                    float(record.delta_from_parent)
                    if isinstance(record.delta_from_parent, (int, float))
                    else None
                ),
                parent_id=record.parent_id,
                candidate_id=record.candidate_id,
            )
        )

    decision_payloads = [asdict(item) for item in decisions]
    return {
        "schema_version": 1,
        "record_count": len(records),
        "comparable_count": len(comparable),
        "best_hypothesis_id": best.hypothesis_id if best else None,
        "best_score_value": float(best.score_value) if best and best.score_value is not None else None,
        "decisions": decision_payloads,
        "decision_counts": _decision_counts(decision_payloads),
        "mutation_guidance": mutation_guidance(decision_payloads),
    }


def mutation_guidance(decisions: list[dict[str, Any]]) -> list[str]:
    promoted = [item for item in decisions if item["decision"] == "promote"]
    pruned = [item for item in decisions if item["decision"] == "prune"]
    mutable = [item for item in decisions if item["decision"] == "mutate"]
    guidance: list[str] = []
    if promoted:
        ids = ", ".join(item["hypothesis_id"] for item in promoted[:3])
        guidance.append(f"Preserve and lightly vary mechanisms from promoted hypotheses: {ids}.")
    if pruned:
        ids = ", ".join(item["hypothesis_id"] for item in pruned[:4])
        guidance.append(f"Avoid copying pruned hypotheses unchanged: {ids}.")
    if mutable:
        ids = ", ".join(item["hypothesis_id"] for item in mutable[:3])
        guidance.append(f"Use mutate hypotheses as parents for targeted perturbations: {ids}.")
    if not guidance:
        guidance.append("No stable graph decision is available; explore diverse, evaluator-safe variants.")
    guidance.append("Do not reuse solution files as warm starts; only mutate rules, parameters, or code under the evaluator.")
    return guidance


def render_hypothesis_graph_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Hypothesis Graph Summary",
        "",
        f"- Records: `{summary.get('record_count', 0)}`",
        f"- Comparable records: `{summary.get('comparable_count', 0)}`",
        f"- Best hypothesis: `{summary.get('best_hypothesis_id') or 'N/A'}`",
        f"- Best score: `{summary.get('best_score_value') if summary.get('best_score_value') is not None else 'N/A'}`",
        f"- Decision counts: `{json.dumps(summary.get('decision_counts', {}), ensure_ascii=False)}`",
        "",
        "## Mutation Guidance",
        "",
    ]
    for item in summary.get("mutation_guidance", []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Decisions",
            "",
            "| Hypothesis | Decision | Round | Score | Delta | Reason |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for item in summary.get("decisions", []):
        lines.append(
            f"| {item.get('hypothesis_id')} | {item.get('decision')} | "
            f"{item.get('round_index')} | {item.get('score_value')} | "
            f"{item.get('delta_from_parent')} | {item.get('reason')} |"
        )
    return "\n".join(lines).strip() + "\n"


def promoted_ancestor_ids(records: list[HypothesisRecord], promoted_ids: set[str]) -> set[str]:
    by_id = {record.hypothesis_id: record for record in records}
    ancestors: set[str] = set()
    for hypothesis_id in promoted_ids:
        current = by_id.get(hypothesis_id)
        while current and current.parent_id:
            parent_id = current.parent_id
            if parent_id in ancestors:
                break
            ancestors.add(parent_id)
            current = by_id.get(parent_id)
    return ancestors


def make_hypothesis_id(round_index: int, summary: dict[str, Any], artifacts: dict[str, str]) -> str:
    digest = hashlib.sha1(
        json.dumps(
            {
                "round_index": round_index,
                "summary": summary,
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"h{round_index:03d}_{digest}"


def extract_score(summary: dict[str, Any]) -> tuple[str | None, float | None]:
    """Return a maximization score from the standard-FJSP summary payload.

    The standard evaluator reports makespan and optionally best-known gaps.  A
    smaller gap or makespan is better, so the score is negated to keep the
    higher-is-better convention used by the harness.
    """

    candidate_metrics = summary.get("best_candidate_metrics") or {}
    best_metrics = summary.get("best_metrics") or {}
    for metric_name in ("avg_gap_pct", "gap_pct", "avg_makespan", "makespan"):
        container = candidate_metrics if metric_name.startswith("avg_") else best_metrics
        value = container.get(metric_name)
        if isinstance(value, (int, float)):
            return metric_name, -float(value)
    return None, None


def improvement_note(metric_name: str | None, score: float | None, delta: float | None) -> str:
    if metric_name is None or score is None:
        return "No comparable numeric score was available for this hypothesis."
    if delta is None:
        return f"Initial comparable score for `{metric_name}`."
    if delta > 1e-9:
        return f"Improved `{metric_name}` versus the parent hypothesis."
    if delta < -1e-9:
        return f"Worse `{metric_name}` than the parent hypothesis."
    return f"Matched the parent hypothesis on `{metric_name}`."


def _decision_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in decisions:
        decision = str(item.get("decision", "unknown"))
        counts[decision] = counts.get(decision, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# 当前闭环使用的“方向图”：一个方向包含首次候选和全部同轮修补 attempt。
# ---------------------------------------------------------------------------

def summarize_direction_graph(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize worker-loop records as user-facing improvement directions.

    A direction is the user-visible "round": one rule/operator idea plus all
    same-direction attempts needed to repair or refine it.  The original round
    records remain the evaluator-backed atomic facts.
    """

    directions: list[dict[str, Any]] = []
    plan_id_to_graph_id: dict[str, str] = {}
    latest_promoted_id: str | None = None
    previous_direction_id: str | None = None
    for item in rounds:
        if not isinstance(item, dict):
            continue
        diagnostics = _dict(item.get("proposal_diagnostics"))
        hypotheses = _list(diagnostics.get("rule_operator_hypotheses"))
        primary = _dict(hypotheses[0]) if hypotheses else {}
        direction_plan = _dict(item.get("direction_plan"))
        round_index = _int(item.get("round_index"), default=len(directions))
        direction_id = make_direction_id(round_index, diagnostics)
        decision = str(item.get("decision") or "unknown")
        status = direction_status(item)
        attempts = direction_attempts(item)
        parent_direction_id = explicit_parent_direction_id(item, direction_plan)
        resolved_parent_id = plan_id_to_graph_id.get(parent_direction_id or "", parent_direction_id)
        mechanism_activation = compact_mechanism_activation(
            item.get("mechanism_activation")
            if isinstance(item.get("mechanism_activation"), dict)
            else direction_plan.get("mechanism_activation")
        )
        round_reflection = compact_round_reflection(item.get("round_reflection"))
        # 图节点保留方向语义和产物引用，不复制 solver 源码或实例具体解。
        direction = {
            "direction_id": direction_id,
            "parent_id": resolved_parent_id or previous_direction_id,
            "round_index": round_index,
            "title": direction_title(item, primary),
            "status": status,
            "decision": decision,
            "strategy_type": str(direction_plan.get("strategy_type") or primary.get("type") or "unknown"),
            "method_package_id": direction_plan.get("method_package_id"),
            "direction_plan": direction_plan,
            "target_files": _bounded_strings(primary.get("target_files"), limit=12),
            "strategy_intent": _bounded_text(diagnostics.get("strategy_intent"), limit=500),
            "hypotheses": [compact_hypothesis_payload(value) for value in hypotheses[:6] if isinstance(value, dict)],
            "attempt_count": len(attempts),
            "attempts": attempts,
            "score_relation": objective_relation(item),
            "mechanism_activation": mechanism_activation,
            "round_reflection": round_reflection,
            "hypothesis_outcome": inferred_hypothesis_outcome(
                decision=decision,
                status=status,
                mechanism_activation=mechanism_activation,
                round_reflection=round_reflection,
            ),
            "artifact_refs": {
                "context_packet_path": item.get("context_packet_path"),
                "cycle_dir": item.get("cycle_dir"),
                "patch_path": item.get("patch_path"),
                "delta_path": item.get("delta_path"),
            },
        }
        directions.append(direction)
        plan_direction_id = str(direction_plan.get("direction_id") or "").strip()
        if plan_direction_id:
            plan_id_to_graph_id[plan_direction_id] = direction_id
        previous_direction_id = direction_id
        if decision == "promoted":
            latest_promoted_id = direction_id

    return {
        "schema_version": 2,
        "round_semantics": "direction",
        "attempt_semantics": "worker proposal attempts inside one direction",
        "direction_count": len(directions),
        "attempt_count": sum(int(item.get("attempt_count", 0) or 0) for item in directions),
        "status_counts": _counts(str(item.get("status") or "unknown") for item in directions),
        "decision_counts": _counts(str(item.get("decision") or "unknown") for item in directions),
        "promoted_direction_ids": [item["direction_id"] for item in directions if item.get("decision") == "promoted"],
        "active_parent_id": latest_promoted_id or previous_direction_id,
        "directions": directions,
        "guidance": direction_graph_guidance(directions),
    }


def explicit_parent_direction_id(
    round_record: dict[str, Any],
    direction_plan: dict[str, Any],
) -> str | None:
    for container in (direction_plan, round_record):
        parent_direction_id = str(container.get("parent_direction_id") or "").strip()
        if parent_direction_id:
            return parent_direction_id
    return None


def build_experience_memory(
    rounds: list[dict[str, Any]],
    *,
    problem_family: str | None = None,
) -> dict[str, Any]:
    """Build candidate lessons and usage signals from evaluator-backed rounds.

    This is a run artifact, not a curated knowledge-card writer.  It preserves
    method-level lessons with artifact references, while avoiding instance
    score values as reusable knowledge.
    """

    # 第一步：每个方向先生成候选经验。成功、失败、未提升和修补恢复都会
    # 保留，但它们的可信等级不同。
    graph = summarize_direction_graph(rounds)
    candidate_lessons: list[dict[str, Any]] = []
    for direction in graph.get("directions") or []:
        if not isinstance(direction, dict):
            continue
        lesson = candidate_lesson_from_direction(direction, problem_family=problem_family)
        if lesson:
            candidate_lessons.append(lesson)
        repair_lesson = repair_lesson_from_direction(direction, problem_family=problem_family)
        if repair_lesson:
            candidate_lessons.append(repair_lesson)
        quality_lesson = agent_generated_quality_lesson_from_direction(
            direction,
            problem_family=problem_family,
        )
        if quality_lesson:
            candidate_lessons.append(quality_lesson)
        semantic_lesson = algorithm_semantic_lesson_from_direction(
            direction,
            problem_family=problem_family,
        )
        if semantic_lesson:
            candidate_lessons.append(semantic_lesson)

    usage_records = skill_usage_records_from_directions(graph.get("directions") or [])
    quality_memory = agent_generated_quality_memory_from_directions(graph.get("directions") or [])
    semantic_memory = algorithm_semantic_memory_from_directions(graph.get("directions") or [])
    # 第二步：只有 Core promoted、机制激活未失败且不存在已验证语义阻塞项，
    # 才会进入 validated_lessons。审查 skipped/unavailable 不阻止经验沉淀。
    directions_by_id = {
        str(direction.get("direction_id") or ""): direction
        for direction in graph.get("directions") or []
        if isinstance(direction, dict) and str(direction.get("direction_id") or "")
    }
    validated_lessons = []
    for lesson in candidate_lessons:
        if lesson.get("lesson_type") != "successful_strategy":
            continue
        direction_id = str((lesson.get("evidence") or {}).get("direction_id") or "")
        direction = directions_by_id.get(direction_id)
        if direction is None or not direction_validated_lesson_eligible(direction):
            continue
        validated_lessons.append(
            {**lesson, "confidence": validated_lesson_confidence(direction)}
        )
    return {
        "schema_version": 1,
        "purpose": "Run-local learning memory for future context selection; not a curated long-term knowledge write.",
        "write_policy": {
            "raw_notes": "preserve as artifacts only",
            "candidate_lessons": "may be recalled with source evidence",
            "validated_lessons": (
                "requires Core promotion, mechanism activation not failed, and no verified blocking semantic finding; "
                "reviewer skipped/unavailable does not block"
            ),
            "curated_skills": "requires explicit promotion outside the worker loop",
            "no_instance_score_as_method": True,
        },
        "memory_tiers": {
            "raw_notes": {
                "status": "artifact_only",
                "sources": [
                    direction.get("artifact_refs", {})
                    for direction in graph.get("directions") or []
                    if isinstance(direction, dict)
                ],
            },
            "candidate_lessons": candidate_lessons,
            "validated_lessons": validated_lessons,
            "curated_skills": [],
        },
        "agent_generated_quality_memory": quality_memory,
        "algorithm_semantic_memory": semantic_memory,
        "skill_usage_records": usage_records,
        "skill_usage_summary": summarize_skill_usage_records(usage_records),
        "self_evolution_metrics": {
            "direction_count": graph.get("direction_count", 0),
            "attempt_count": graph.get("attempt_count", 0),
            "candidate_lesson_count": len(candidate_lessons),
            "validated_lesson_count": len(validated_lessons),
            "skill_usage_record_count": len(usage_records),
            "promoted_direction_count": len(graph.get("promoted_direction_ids") or []),
            "same_direction_recovery_count": sum(
                1
                for direction in graph.get("directions") or []
                if isinstance(direction, dict) and direction_recovered(direction)
            ),
            "agent_quality_rejected_attempt_count": quality_memory.get("rejected_attempt_count", 0),
            "agent_quality_recovered_direction_count": quality_memory.get("recovered_direction_count", 0),
            "semantic_repair_required_attempt_count": semantic_memory.get(
                "repair_required_attempt_count",
                0,
            ),
            "semantic_recovered_direction_count": semantic_memory.get("recovered_direction_count", 0),
        },
        "next_context_guidance": experience_guidance(
            candidate_lessons,
            usage_records,
            quality_memory,
            semantic_memory,
        ),
    }


def render_direction_graph_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Improvement Direction Graph",
        "",
        f"- Schema: `{summary.get('schema_version')}`",
        f"- Direction count: `{summary.get('direction_count', 0)}`",
        f"- Attempt count: `{summary.get('attempt_count', 0)}`",
        f"- Decision counts: `{json.dumps(summary.get('decision_counts') or {}, ensure_ascii=False)}`",
        f"- Status counts: `{json.dumps(summary.get('status_counts') or {}, ensure_ascii=False)}`",
        "",
        "## Directions",
        "",
        "| Direction | Parent | Round | Status | Decision | Attempts | Type | Title |",
        "| --- | --- | ---: | --- | --- | ---: | --- | --- |",
    ]
    for item in summary.get("directions") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| `{item.get('direction_id')}` | `{item.get('parent_id') or ''}` | "
            f"`{item.get('round_index')}` | `{item.get('status')}` | `{item.get('decision')}` | "
            f"`{item.get('attempt_count')}` | `{item.get('strategy_type')}` | "
            f"{_md_cell(str(item.get('title') or ''))} |"
        )
    lines.extend(["", "## Guidance", ""])
    for item in summary.get("guidance") or []:
        lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def render_experience_memory_markdown(memory: dict[str, Any]) -> str:
    tiers = _dict(memory.get("memory_tiers"))
    lessons = _list(tiers.get("candidate_lessons"))
    quality = _dict(memory.get("agent_generated_quality_memory"))
    semantic = _dict(memory.get("algorithm_semantic_memory"))
    lines = [
        "# Experience Memory",
        "",
        f"- Candidate lessons: `{len(lessons)}`",
        f"- Skill usage records: `{len(_list(memory.get('skill_usage_records')))}`",
        f"- Self-evolution metrics: `{json.dumps(memory.get('self_evolution_metrics') or {}, ensure_ascii=False)}`",
        "",
        "## Candidate Lessons",
        "",
        "| Lesson | Type | Confidence | Strategy | Outcome | Applicability |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in lessons:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| `{item.get('lesson_id')}` | `{item.get('lesson_type')}` | `{item.get('confidence')}` | "
            f"{_md_cell(str(item.get('strategy') or ''))} | `{item.get('outcome')}` | "
            f"{_md_cell('; '.join(str(value) for value in _list(item.get('applicability'))[:3]))} |"
        )
    if quality:
        lines.extend(
            [
                "",
                "## Agent-Generated Quality Memory",
                "",
                f"- Rejected attempts: `{quality.get('rejected_attempt_count', 0)}`",
                f"- Recovered directions: `{quality.get('recovered_direction_count', 0)}`",
                f"- Recurring quality risks: `{json.dumps(quality.get('recurring_quality_risks') or [], ensure_ascii=False)}`",
                f"- Recurring self-check risks: `{json.dumps(quality.get('recurring_self_check_risks') or [], ensure_ascii=False)}`",
            ]
        )
    if semantic:
        lines.extend(
            [
                "",
                "## Algorithm Semantic Memory",
                "",
                f"- Repair-required attempts: `{semantic.get('repair_required_attempt_count', 0)}`",
                f"- Recovered directions: `{semantic.get('recovered_direction_count', 0)}`",
                f"- Recurring categories: `{json.dumps(semantic.get('recurring_categories') or [], ensure_ascii=False)}`",
                f"- Required behavioral tests: `{json.dumps(semantic.get('required_behavioral_tests') or [], ensure_ascii=False)}`",
            ]
        )
    lines.extend(["", "## Next Context Guidance", ""])
    for item in _list(memory.get("next_context_guidance")):
        lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def make_direction_id(round_index: int, diagnostics: dict[str, Any]) -> str:
    digest = hashlib.sha1(
        json.dumps(
            {
                "round_index": round_index,
                "strategy_intent": diagnostics.get("strategy_intent"),
                "rule_operator_hypotheses": diagnostics.get("rule_operator_hypotheses") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:10]
    return f"d{round_index:03d}_{digest}"


def direction_title(round_record: dict[str, Any], primary: dict[str, Any]) -> str:
    direction_plan = _dict(round_record.get("direction_plan"))
    planned_title = str(direction_plan.get("title") or "").strip()
    if planned_title:
        return planned_title[:160]
    name = str(primary.get("name") or "").strip()
    if name:
        return name[:160]
    diagnostics = _dict(round_record.get("proposal_diagnostics"))
    summary = _bounded_text(diagnostics.get("summary"), limit=160)
    if summary:
        return summary
    return f"direction_{_int(round_record.get('round_index'), default=0):03d}"


def direction_status(round_record: dict[str, Any]) -> str:
    if round_record.get("decision") == "baseline_incumbent":
        candidate_key = _number_list(round_record.get("candidate_key"))
        if candidate_key and not all(value == float("-inf") for value in candidate_key):
            if round_record.get("semantic_review_degraded"):
                return "degraded_baseline"
            return "validated_baseline"
        return "strategy_infeasible"
    if round_record.get("decision") == "promoted":
        return "validated_success"
    candidate_key = _number_list(round_record.get("candidate_key"))
    if candidate_key and all(value == float("-inf") for value in candidate_key):
        return "strategy_infeasible"
    semantic_review = _dict(round_record.get("semantic_review"))
    if semantic_review.get("status") == "unavailable":
        return "semantic_review_unavailable"
    if semantic_review.get("status") == "repair_required" or semantic_review.get("accepted") is False:
        return "semantic_repair_required"
    smoke = _dict(round_record.get("smoke_gate"))
    if smoke and smoke.get("passed") is False:
        return "strategy_infeasible"
    promotion = _dict(round_record.get("promotion_check"))
    if promotion.get("reason") == "algorithm_semantic_review_unavailable":
        return "semantic_review_unavailable"
    if promotion.get("reason") == "candidate_not_strictly_better":
        return "no_improvement"
    if promotion.get("reason") == "repeat_objective_not_strictly_better":
        return "unstable_or_noisy_improvement"
    if str(round_record.get("worker_status") or "").endswith("exception"):
        return "worker_failed"
    return "rolled_back"


def direction_attempts(round_record: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = _dict(round_record.get("proposal_diagnostics"))
    repair = _dict(diagnostics.get("in_round_repair"))
    attempts = _list(repair.get("attempts"))
    if attempts:
        return [compact_attempt_payload(item) for item in attempts if isinstance(item, dict)]
    return [
        {
            "attempt_index": 0,
            "kind": "initial",
            "worker_status": round_record.get("worker_status"),
            "changed_files": _bounded_strings(round_record.get("worker_changed_files"), limit=12),
            "candidate_key_relation": objective_relation(round_record),
            "failure_signatures": [],
            "algorithm_semantic_review": compact_algorithm_semantic_review(
                round_record.get("semantic_review")
            ),
            "context_packet_path": round_record.get("context_packet_path"),
            "patch_path": round_record.get("patch_path"),
        }
    ]


def compact_attempt_payload(attempt: dict[str, Any]) -> dict[str, Any]:
    judgment = _dict(attempt.get("agentic_judgment"))
    checks = _dict(judgment.get("checks"))
    return {
        "attempt_index": attempt.get("attempt_index"),
        "kind": attempt_kind(attempt),
        "worker_status": attempt.get("worker_status"),
        "changed_files": _bounded_strings(attempt.get("changed_files"), limit=12),
        "candidate_key_relation": attempt_key_relation(attempt),
        "failure_signatures": _bounded_strings(attempt.get("failure_signatures"), limit=16),
        "agentic_accepted": judgment.get("accepted"),
        "agent_generated_quality": compact_agent_generated_quality_gate(judgment, checks),
        "algorithm_semantic_review": compact_algorithm_semantic_review(attempt.get("semantic_review")),
        "assignment_id": attempt.get("assignment_id"),
        "worker_assignment_path": attempt.get("worker_assignment_path"),
        "context_packet_path": attempt.get("context_packet_path"),
        "patch_path": attempt.get("patch_path"),
        "delta_path": attempt.get("delta_path"),
    }


def compact_mechanism_activation(value: Any) -> dict[str, Any]:
    activation = _dict(value)
    if not activation:
        return {}
    checks: list[dict[str, Any]] = []
    for item in _list(activation.get("checks")):
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                "id": item.get("id"),
                "path": item.get("path"),
                "required": bool(item.get("required", True)),
                "passed": item.get("passed"),
                "description": _bounded_text(item.get("description"), limit=300),
            }
        )
        if len(checks) >= 8:
            break
    return {
        "status": activation.get("status"),
        "passed": activation.get("passed"),
        "declared_check_count": activation.get("declared_check_count"),
        "required_check_count": activation.get("required_check_count"),
        "required_failure_count": activation.get("required_failure_count"),
        "checks": checks,
    }


def compact_algorithm_semantic_review(value: Any) -> dict[str, Any]:
    review = _dict(value)
    if not review:
        return {}
    findings: list[dict[str, Any]] = []
    for item in _list(review.get("findings")):
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "finding_id": item.get("finding_id"),
                "category": item.get("category"),
                "blocking": bool(item.get("blocking")),
                "confidence": item.get("confidence"),
                "source_path": item.get("source_path"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "knowledge_path": item.get("knowledge_path"),
                "repair": _bounded_text(item.get("repair"), limit=700),
                "required_test": _bounded_text(item.get("required_test"), limit=500),
            }
        )
        if len(findings) >= 8:
            break
    return {
        "status": review.get("status"),
        "accepted": review.get("accepted"),
        "summary": _bounded_text(review.get("summary"), limit=700),
        "findings": findings,
        "knowledge_paths": _bounded_strings(review.get("knowledge_paths"), limit=12),
        "artifacts": _dict(review.get("artifacts")),
    }


def compact_round_reflection(value: Any) -> dict[str, Any]:
    reflection = _dict(value)
    if not reflection:
        return {}
    findings: list[dict[str, Any]] = []
    for item in _list(reflection.get("candidate_findings")):
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "candidate_id": _bounded_text(item.get("candidate_id"), limit=80),
                "outcome": normalize_hypothesis_outcome(item.get("outcome")),
                "causal_interpretation": _bounded_text(item.get("causal_interpretation"), limit=900),
            }
        )
        if len(findings) >= 4:
            break
    next_action = _dict(reflection.get("next_action"))
    return {
        "schema_version": reflection.get("schema_version"),
        "round_index": reflection.get("round_index"),
        "hypothesis_outcome": normalize_hypothesis_outcome(
            reflection.get("hypothesis_outcome") or reflection.get("status")
        ),
        "summary": _bounded_text(reflection.get("summary"), limit=1200),
        "candidate_findings": findings,
        "next_action": {
            "action": _bounded_text(next_action.get("action"), limit=80),
            "rationale": _bounded_text(next_action.get("rationale"), limit=1200),
            "required_activation_checks": _bounded_strings(next_action.get("required_activation_checks"), limit=12),
        },
    }


def compact_agent_generated_quality_gate(
    judgment: dict[str, Any],
    checks: dict[str, Any],
) -> dict[str, Any]:
    """Keep quality-gate facts visible to later rounds without copying code."""

    quality_risks = _bounded_strings(
        checks.get("agent_generated_solver_blocking_quality_risks")
        if "agent_generated_solver_blocking_quality_risks" in checks
        else checks.get("agent_generated_solver_quality_risks"),
        limit=8,
    )
    self_check_risks = _bounded_strings(checks.get("agent_generated_solver_self_check_risks"), limit=8)
    runtime_import_risks = _bounded_strings(checks.get("agent_generated_runtime_import_risks"), limit=8)
    if not (quality_risks or self_check_risks or runtime_import_risks):
        issues = _bounded_strings(judgment.get("issues"), limit=8)
        if not any(str(item).startswith("agent_generated") for item in issues):
            return {}
    contract = _dict(checks.get("agent_generated_solver_quality_contract"))
    return {
        "accepted": judgment.get("accepted"),
        "issues": [
            item
            for item in _bounded_strings(judgment.get("issues"), limit=8)
            if item.startswith("agent_generated")
        ],
        "quality_risks": quality_risks,
        "self_check_risks": self_check_risks,
        "runtime_import_risks": runtime_import_risks,
        "expected_active_features": _bounded_strings(contract.get("active_features"), limit=12),
        "expected_capabilities": _bounded_strings(
            (contract.get("required_code_capabilities") or [])
            + (contract.get("variant_required_code_capabilities") or []),
            limit=18,
        ),
    }


def attempt_kind(attempt: dict[str, Any]) -> str:
    if _int(attempt.get("attempt_index"), default=0) == 0:
        return "initial"
    signatures = " ".join(_bounded_strings(attempt.get("failure_signatures"), limit=16))
    if "algorithm_semantic_review_repair_required" in signatures:
        return "semantic_repair"
    if "legal_but_not_strictly_better" in signatures:
        return "same_direction_refinement"
    return "repair"


def attempt_key_relation(attempt: dict[str, Any]) -> str:
    key = _number_list(attempt.get("candidate_key"))
    if not key:
        return "not_measured"
    if all(value == float("-inf") for value in key):
        return "invalid_or_rejected"
    if "legal_but_not_strictly_better" in _bounded_strings(attempt.get("failure_signatures"), limit=16):
        return "legal_but_not_strictly_better"
    return "measured"


def objective_relation(round_record: dict[str, Any]) -> str:
    candidate = _number_list(round_record.get("candidate_key"))
    incumbent_after = _number_list(round_record.get("incumbent_key_after"))
    if not candidate:
        return "not_measured"
    if all(value == float("-inf") for value in candidate):
        return "invalid_or_rejected"
    if round_record.get("decision") == "baseline_incumbent":
        return "accepted_as_agent_generated_baseline"
    if round_record.get("decision") == "promoted":
        return "improved_and_promoted"
    semantic_review = _dict(round_record.get("semantic_review"))
    if semantic_review.get("status") == "repair_required" or semantic_review.get("accepted") is False:
        return "semantic_review_blocked"
    promotion = _dict(round_record.get("promotion_check"))
    if promotion.get("reason") == "repeat_objective_not_strictly_better":
        return "initially_better_but_unstable"
    if incumbent_after and candidate <= incumbent_after:
        return "legal_but_not_strictly_better"
    return "measured_but_rolled_back"


def compact_hypothesis_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _bounded_text(item.get("name"), limit=120),
        "type": _bounded_text(item.get("type"), limit=80),
        "novelty": _bounded_text(item.get("novelty"), limit=240),
        "expected_effect": _bounded_text(item.get("expected_effect"), limit=240),
        "target_files": _bounded_strings(item.get("target_files"), limit=12),
        "evidence_used": _bounded_strings(item.get("evidence_used"), limit=12),
        "ablation_plan": _bounded_text(item.get("ablation_plan"), limit=240),
    }


def normalize_hypothesis_outcome(value: Any) -> str:
    outcome = str(value or "").strip().lower()
    if outcome == "supported":
        return "supported"
    if outcome == "refuted":
        return "refuted"
    if outcome in {"mixed", "inconclusive_not_exercised", "inconclusive"}:
        return "inconclusive"
    return ""


def inferred_hypothesis_outcome(
    *,
    decision: str,
    status: str,
    mechanism_activation: dict[str, Any],
    round_reflection: dict[str, Any],
) -> str:
    recorded = normalize_hypothesis_outcome(round_reflection.get("hypothesis_outcome"))
    if recorded:
        return recorded
    if mechanism_activation.get("passed") is False:
        return "inconclusive"
    if decision in {"promoted", "baseline_incumbent"} or status in {"validated_success", "validated_baseline"}:
        return "supported"
    return "refuted"


# ---------------------------------------------------------------------------
# 经验条目构造：把不同方向结局转成有适用条件和禁忌的可召回记录。
# ---------------------------------------------------------------------------

def candidate_lesson_from_direction(
    direction: dict[str, Any],
    *,
    problem_family: str | None,
) -> dict[str, Any] | None:
    hypotheses = _list(direction.get("hypotheses"))
    primary = _dict(hypotheses[0]) if hypotheses else {}
    strategy = str(primary.get("name") or direction.get("title") or "").strip()
    if not strategy:
        return None
    status = str(direction.get("status") or "")
    if status in {"validated_success", "validated_baseline"}:
        lesson_type = "successful_strategy"
        outcome = (
            "accepted_as_agent_generated_baseline"
            if status == "validated_baseline"
            else "promoted_by_core_evaluator"
        )
        applicability = [
            "when the same problem-family features and edit contract are present",
            "when the incumbent mechanism can be preserved and mutated incrementally",
        ]
        contraindications = ["do not copy instance-specific outputs or scores", "do not bypass validator/evaluator gates"]
    elif status == "no_improvement":
        lesson_type = "no_improvement_pattern"
        outcome = "legal_but_not_promoted"
        applicability = ["when a similar idea repeats without strict improvement"]
        contraindications = ["avoid repeating the same tie-break or cosmetic change unchanged"]
    elif status in {
        "strategy_infeasible",
        "worker_failed",
        "semantic_repair_required",
        "semantic_review_unavailable",
    }:
        lesson_type = "failure_pattern"
        outcome = status
        applicability = ["when similar files, warnings, or failure signatures appear"]
        contraindications = ["repair legality and patch shape before objective tuning"]
    else:
        lesson_type = "candidate_observation"
        outcome = status or str(direction.get("decision") or "unknown")
        applicability = ["use only as weak evidence until repeated"]
        contraindications = ["do not promote to a skill without more evidence"]

    return {
        "lesson_id": make_lesson_id(direction, lesson_type),
        "lesson_type": lesson_type,
        "problem_family": problem_family,
        "strategy": strategy[:160],
        "strategy_type": direction.get("strategy_type"),
        "method_package_id": direction.get("method_package_id"),
        "outcome": outcome,
        "hypothesis_outcome": direction.get("hypothesis_outcome"),
        "applicability": applicability,
        "contraindications": contraindications,
        "evidence": {
            "direction_id": direction.get("direction_id"),
            "round_index": direction.get("round_index"),
            "decision": direction.get("decision"),
            "status": direction.get("status"),
            "score_relation": direction.get("score_relation"),
            "hypothesis_outcome": direction.get("hypothesis_outcome"),
            "mechanism_activation": _dict(direction.get("mechanism_activation")),
            "round_reflection": _dict(direction.get("round_reflection")),
            "artifact_refs": direction.get("artifact_refs") or {},
        },
        "confidence": "candidate",
        "recommended_skill_update": recommended_skill_update(lesson_type, direction),
    }


def repair_lesson_from_direction(
    direction: dict[str, Any],
    *,
    problem_family: str | None,
) -> dict[str, Any] | None:
    """仅在同一方向从失败恢复时生成修补经验。"""

    attempts = _list(direction.get("attempts"))
    if len(attempts) < 2 or not direction_recovered(direction):
        return None
    return {
        "lesson_id": make_lesson_id(direction, "repair_recovery"),
        "lesson_type": "repair_recovery",
        "problem_family": problem_family,
        "strategy": str(direction.get("title") or "")[:160],
        "strategy_type": direction.get("strategy_type"),
        "method_package_id": direction.get("method_package_id"),
        "outcome": "same_direction_attempt_recovered",
        "hypothesis_outcome": direction.get("hypothesis_outcome"),
        "applicability": [
            "when the first attempt is illegal or legal-but-not-better but carries useful patch evidence",
            "when repair feedback includes exact rejected edits, failure signatures, and incumbent context",
        ],
        "contraindications": ["do not spend a new direction before consuming same-direction feedback"],
        "evidence": {
            "direction_id": direction.get("direction_id"),
            "round_index": direction.get("round_index"),
            "attempt_count": len(attempts),
            "mechanism_activation": _dict(direction.get("mechanism_activation")),
            "round_reflection": _dict(direction.get("round_reflection")),
            "artifact_refs": direction.get("artifact_refs") or {},
        },
        "confidence": "candidate",
        "recommended_skill_update": "Keep same-direction repair/refinement prompts focused on exact failed edits and measured failure signatures.",
    }


def agent_generated_quality_lesson_from_direction(
    direction: dict[str, Any],
    *,
    problem_family: str | None,
) -> dict[str, Any] | None:
    """总结 parser/decoder/self-check 等工程质量门禁及其恢复情况。"""

    gates = [
        _dict(attempt.get("agent_generated_quality"))
        for attempt in _list(direction.get("attempts"))
        if isinstance(attempt, dict) and _dict(attempt.get("agent_generated_quality"))
    ]
    if not gates:
        return None
    quality_risks = _dedupe_strings(
        risk
        for gate in gates
        for risk in _bounded_strings(gate.get("quality_risks"), limit=8)
    )
    self_check_risks = _dedupe_strings(
        risk
        for gate in gates
        for risk in _bounded_strings(gate.get("self_check_risks"), limit=8)
    )
    runtime_import_risks = _dedupe_strings(
        risk
        for gate in gates
        for risk in _bounded_strings(gate.get("runtime_import_risks"), limit=8)
    )
    if not (quality_risks or self_check_risks or runtime_import_risks):
        return None
    recovered = direction_recovered(direction)
    return {
        "lesson_id": make_lesson_id(direction, "agent_generated_quality_gap"),
        "lesson_type": "agent_generated_quality_gap",
        "problem_family": problem_family,
        "strategy": str(direction.get("title") or "")[:160],
        "strategy_type": direction.get("strategy_type"),
        "method_package_id": direction.get("method_package_id"),
        "outcome": "recovered_after_quality_repair" if recovered else "blocked_by_quality_gate",
        "hypothesis_outcome": direction.get("hypothesis_outcome"),
        "applicability": [
            "when an agent-generated solver is created or evolved from IO and requirement documents",
            "when JA rejects a proposal before evaluator scoring",
            "when the next prompt must repair structure before objective tuning",
        ],
        "contraindications": [
            "do not switch to a new heuristic while parser/decoder/constructor/self-check gates are still missing",
            "do not treat solver_contract_self_check text as a substitute for matching code evidence",
        ],
        "evidence": {
            "direction_id": direction.get("direction_id"),
            "round_index": direction.get("round_index"),
            "quality_risks": quality_risks[:8],
            "self_check_risks": self_check_risks[:8],
            "runtime_import_risks": runtime_import_risks[:8],
            "mechanism_activation": _dict(direction.get("mechanism_activation")),
            "round_reflection": _dict(direction.get("round_reflection")),
            "artifact_refs": direction.get("artifact_refs") or {},
        },
        "confidence": "candidate",
        "recommended_skill_update": quality_gap_recommendation(
            quality_risks=quality_risks,
            self_check_risks=self_check_risks,
            runtime_import_risks=runtime_import_risks,
        ),
    }


def algorithm_semantic_lesson_from_direction(
    direction: dict[str, Any],
    *,
    problem_family: str | None,
) -> dict[str, Any] | None:
    """沉淀有源码与知识双重证据的方法语义缺口，不保存实例分数。"""

    reviews = algorithm_semantic_reviews_from_direction(direction)
    findings = [
        finding
        for review in reviews
        for finding in _list(review.get("findings"))
        if isinstance(finding, dict)
    ]
    if not findings:
        return None
    categories = _dedupe_strings(
        str(finding.get("category") or "method_semantics")
        for finding in findings
    )
    repairs = _dedupe_strings(
        str(finding.get("repair") or "")
        for finding in findings
        if str(finding.get("repair") or "").strip()
    )
    required_tests = _dedupe_strings(
        str(finding.get("required_test") or "")
        for finding in findings
        if str(finding.get("required_test") or "").strip()
    )
    blocking = any(bool(finding.get("blocking")) for finding in findings)
    recovered = algorithm_semantic_direction_recovered(direction)
    return {
        "lesson_id": make_lesson_id(direction, "algorithm_semantic_gap"),
        "lesson_type": "algorithm_semantic_gap",
        "problem_family": problem_family,
        "strategy": str(direction.get("title") or "")[:160],
        "strategy_type": direction.get("strategy_type"),
        "method_package_id": direction.get("method_package_id"),
        "outcome": (
            "recovered_after_semantic_repair"
            if recovered
            else ("blocked_by_semantic_review" if blocking else "semantic_warning_observed")
        ),
        "hypothesis_outcome": direction.get("hypothesis_outcome"),
        "applicability": [
            "when generated code claims the same named method or invariant",
            "when the cited knowledge contract is active for the current problem family",
        ],
        "contraindications": [
            "do not generalize benchmark scores or schedules into reusable method knowledge",
            "do not block on a semantic claim without source lines and an exact knowledge quote",
        ],
        "evidence": {
            "direction_id": direction.get("direction_id"),
            "round_index": direction.get("round_index"),
            "categories": categories[:8],
            "repairs": repairs[:8],
            "required_tests": required_tests[:8],
            "knowledge_paths": _dedupe_strings(
                str(finding.get("knowledge_path") or "")
                for finding in findings
                if str(finding.get("knowledge_path") or "").strip()
            )[:12],
            "mechanism_activation": _dict(direction.get("mechanism_activation")),
            "round_reflection": _dict(direction.get("round_reflection")),
            "artifact_refs": direction.get("artifact_refs") or {},
        },
        "confidence": "candidate",
        "recommended_skill_update": (
            "Preserve the reviewed invariant and its behavioral test in the domain knowledge card; "
            "keep instance scores out of the reusable rule."
        ),
    }


def algorithm_semantic_reviews_from_direction(direction: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        review
        for attempt in _list(direction.get("attempts"))
        if isinstance(attempt, dict)
        for review in [_dict(attempt.get("algorithm_semantic_review"))]
        if review
    ]


def _semantic_review_has_blocking_finding(review: dict[str, Any]) -> bool:
    return any(
        isinstance(finding, dict) and bool(finding.get("blocking"))
        for finding in _list(review.get("findings"))
    )


def _semantic_review_is_explicit_repair_attempt(attempt: dict[str, Any]) -> bool:
    kind = str(attempt.get("kind") or attempt_kind(attempt) or "").strip().lower()
    return kind in {"repair", "semantic_repair"}


def _semantic_review_clears_blocker(attempt: dict[str, Any], review: dict[str, Any]) -> bool:
    return (
        _semantic_review_is_explicit_repair_attempt(attempt)
        and str(review.get("status") or "").strip().lower() in {"pass", "warning"}
        and review.get("accepted") is True
        and not _semantic_review_has_blocking_finding(review)
    )


def _direction_semantic_blocker_state(direction: dict[str, Any]) -> tuple[bool, bool]:
    seen_blocker = False
    active_blocker = False
    for attempt in _list(direction.get("attempts")):
        if not isinstance(attempt, dict):
            continue
        review = _dict(attempt.get("algorithm_semantic_review"))
        if not review:
            continue
        if _semantic_review_has_blocking_finding(review):
            seen_blocker = True
            active_blocker = True
            continue
        if active_blocker and _semantic_review_clears_blocker(attempt, review):
            active_blocker = False
    return seen_blocker, active_blocker


def direction_has_verified_blocking_semantic_finding(direction: dict[str, Any]) -> bool:
    _, active_blocker = _direction_semantic_blocker_state(direction)
    return active_blocker


def direction_semantically_validated(direction: dict[str, Any]) -> bool:
    return not direction_has_verified_blocking_semantic_finding(direction)


def direction_validated_lesson_eligible(direction: dict[str, Any]) -> bool:
    if direction.get("decision") != "promoted":
        return False
    activation = _dict(direction.get("mechanism_activation"))
    if str(activation.get("status") or "").strip().lower() != "passed":
        return False
    if activation.get("passed") is not True:
        return False
    return not direction_has_verified_blocking_semantic_finding(direction)


def validated_lesson_confidence(direction: dict[str, Any]) -> str:
    activation = _dict(direction.get("mechanism_activation"))
    if str(activation.get("status") or "").strip().lower() != "passed" or activation.get("passed") is not True:
        return "candidate"
    reviews = algorithm_semantic_reviews_from_direction(direction)
    if any(
        str(review.get("status") or "").strip().lower() in {"pass", "warning"}
        and review.get("accepted") is True
        for review in reviews
    ):
        return "core_activation_and_semantic_validated"
    return "core_and_activation_validated"


def algorithm_semantic_direction_recovered(direction: dict[str, Any]) -> bool:
    seen_blocker, active_blocker = _direction_semantic_blocker_state(direction)
    return seen_blocker and not active_blocker


# ---------------------------------------------------------------------------
# 聚合记忆：为下一轮提供高频质量缺口、语义修复和行为测试，而非原始长日志。
# ---------------------------------------------------------------------------

def algorithm_semantic_memory_from_directions(directions: list[Any]) -> dict[str, Any]:
    reviews: list[dict[str, Any]] = []
    recovered_direction_count = 0
    for direction in directions:
        if not isinstance(direction, dict):
            continue
        direction_reviews = algorithm_semantic_reviews_from_direction(direction)
        if not direction_reviews:
            continue
        reviews.extend(direction_reviews)
        if algorithm_semantic_direction_recovered(direction):
            recovered_direction_count += 1
    if not reviews:
        return {}

    findings = [
        finding
        for review in reviews
        for finding in _list(review.get("findings"))
        if isinstance(finding, dict)
    ]
    categories = [str(item.get("category") or "method_semantics") for item in findings]
    repairs = [str(item.get("repair") or "") for item in findings if str(item.get("repair") or "").strip()]
    required_tests = _dedupe_strings(
        str(item.get("required_test") or "")
        for item in findings
        if str(item.get("required_test") or "").strip()
    )
    knowledge_paths = _dedupe_strings(
        str(item.get("knowledge_path") or "")
        for item in findings
        if str(item.get("knowledge_path") or "").strip()
    )
    repair_required_count = sum(
        1
        for review in reviews
        if review.get("status") == "repair_required"
        or (review.get("accepted") is False and review.get("status") != "unavailable")
    )
    warning_count = sum(1 for review in reviews if review.get("status") == "warning")
    return {
        "schema_version": 1,
        "purpose": (
            "Run-local method-semantic memory backed by candidate source and domain knowledge evidence; "
            "contains no benchmark-specific solution or target score."
        ),
        "attempt_count": len(reviews),
        "repair_required_attempt_count": repair_required_count,
        "warning_attempt_count": warning_count,
        "recovered_direction_count": recovered_direction_count,
        "recurring_categories": counted_items(categories, limit=8),
        "recurring_repairs": counted_items(repairs, limit=8),
        "required_behavioral_tests": required_tests[:10],
        "knowledge_paths": knowledge_paths[:12],
        "next_prompt_rule": (
            "Preserve repaired algorithm invariants in later directions. Before repeating a named method claim, "
            "run the remembered behavioral tests and cite matching source evidence against the active knowledge contract."
            if findings
            else ""
        ),
    }


def agent_generated_quality_memory_from_directions(directions: list[Any]) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    recovered_direction_count = 0
    for direction in directions:
        if not isinstance(direction, dict):
            continue
        direction_gates = [
            _dict(attempt.get("agent_generated_quality"))
            for attempt in _list(direction.get("attempts"))
            if isinstance(attempt, dict) and _dict(attempt.get("agent_generated_quality"))
        ]
        if not direction_gates:
            continue
        gates.extend(direction_gates)
        if direction_recovered(direction):
            recovered_direction_count += 1

    if not gates:
        return {}

    quality_risks = [
        risk
        for gate in gates
        for risk in _bounded_strings(gate.get("quality_risks"), limit=12)
    ]
    self_check_risks = [
        risk
        for gate in gates
        for risk in _bounded_strings(gate.get("self_check_risks"), limit=12)
    ]
    runtime_import_risks = [
        risk
        for gate in gates
        for risk in _bounded_strings(gate.get("runtime_import_risks"), limit=12)
    ]
    rejected_attempt_count = sum(1 for gate in gates if gate.get("accepted") is False)
    return {
        "schema_version": 1,
        "purpose": (
            "Agent-generated solver quality signals for the next prompt; "
            "method-level gaps only, no solver implementation."
        ),
        "attempt_count": len(gates),
        "rejected_attempt_count": rejected_attempt_count,
        "recovered_direction_count": recovered_direction_count,
        "recurring_quality_risks": counted_items(quality_risks, limit=8),
        "recurring_self_check_risks": counted_items(self_check_risks, limit=8),
        "recurring_runtime_import_risks": counted_items(runtime_import_risks, limit=8),
        "next_prompt_rule": quality_memory_next_prompt_rule(
            quality_risks=quality_risks,
            self_check_risks=self_check_risks,
            runtime_import_risks=runtime_import_risks,
        ),
    }


def quality_gap_recommendation(
    *,
    quality_risks: list[str],
    self_check_risks: list[str],
    runtime_import_risks: list[str],
) -> str:
    if runtime_import_risks:
        return "Strengthen standalone-runtime guidance; generated example solvers must not import backend harness modules."
    joined_quality = " ".join(quality_risks).lower()
    if "operation_level_ready_list_constructor" in joined_quality or "active_io_parser" in joined_quality:
        return (
            "Before local search, require active IO parsing and operation-level ready-list construction "
            "with code evidence in solver_contract_self_check."
        )
    if self_check_risks:
        return "Require solver_contract_self_check to map every expected capability to concrete code evidence."
    return "Keep this as negative memory and repair the listed structural gaps before objective tuning."


def quality_memory_next_prompt_rule(
    *,
    quality_risks: list[str],
    self_check_risks: list[str],
    runtime_import_risks: list[str],
) -> str:
    if not (quality_risks or self_check_risks or runtime_import_risks):
        return ""
    return (
        "Before proposing another objective-improvement operator, explicitly resolve recurring "
        "agent-generated quality gaps from this memory. Preserve any recovered parser/representation/"
        "constructor/decoder mechanism and cite matching code evidence."
    )


def direction_recovered(direction: dict[str, Any]) -> bool:
    attempts = _list(direction.get("attempts"))
    if len(attempts) < 2:
        return False
    return direction.get("decision") == "promoted" or direction.get("status") in {
        "validated_success",
        "validated_baseline",
        "no_improvement",
        "unstable_or_noisy_improvement",
    }


def make_lesson_id(direction: dict[str, Any], lesson_type: str) -> str:
    raw = f"{direction.get('direction_id')}:{lesson_type}:{direction.get('title')}"
    return "lesson_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def recommended_skill_update(lesson_type: str, direction: dict[str, Any]) -> str:
    if lesson_type == "successful_strategy":
        return (
            "Consider a reusable workflow only after this method succeeds again under similar contracts; "
            "capture method structure, not instance score values."
        )
    if lesson_type == "failure_pattern":
        return "Add or strengthen a negative-memory guard if the same failure signature repeats."
    if lesson_type == "no_improvement_pattern":
        return "Down-rank unchanged repeats; require a materially different neighborhood, decoder, or rule mechanism."
    return "Keep as candidate memory until more evaluator-backed evidence exists."


# ---------------------------------------------------------------------------
# 知识/Skill 使用追踪：记录“被引用并与什么结果关联”，不宣称严格因果。
# ---------------------------------------------------------------------------

def skill_usage_records_from_directions(directions: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for direction in directions:
        if not isinstance(direction, dict):
            continue
        hypotheses = _list(direction.get("hypotheses"))
        sources: set[str] = set()
        for hypothesis in hypotheses:
            if isinstance(hypothesis, dict):
                sources.update(_bounded_strings(hypothesis.get("evidence_used"), limit=20))
        intent = str(direction.get("strategy_intent") or "").lower()
        if "knowledge_cards" in intent or "rag" in intent:
            sources.add("knowledge_cards")
        if "loop_feedback" in intent or _list(direction.get("attempts")):
            sources.add("loop_feedback")
        method_package_id = str(direction.get("method_package_id") or "").strip()
        if method_package_id:
            sources.add(f"method_package:{method_package_id}")
        if not sources:
            sources.add("unattributed")
        for source in sorted(sources):
            records.append(
                {
                    "usage_id": make_usage_id(direction, source),
                    "direction_id": direction.get("direction_id"),
                    "round_index": direction.get("round_index"),
                    "source": source,
                    "source_kind": classify_usage_source(source),
                    "strategy_type": direction.get("strategy_type"),
                    "method_package_id": method_package_id or None,
                    "outcome": direction.get("status"),
                    "decision": direction.get("decision"),
                    "effect": usage_effect(direction),
                }
            )
    return records


def make_usage_id(direction: dict[str, Any], source: str) -> str:
    raw = f"{direction.get('direction_id')}:{source}"
    return "usage_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def classify_usage_source(source: str) -> str:
    lowered = source.lower()
    if "knowledge" in lowered or "rag" in lowered or lowered.endswith(".md"):
        return "knowledge_card"
    if lowered.startswith("method_package:"):
        return "method_package"
    if "skill" in lowered:
        return "skill"
    if "loop_feedback" in lowered or "failure_memory" in lowered or "previous" in lowered:
        return "within_run_memory"
    if "project_intake" in lowered or "instance_diagnostics" in lowered or "slot_manifest" in lowered:
        return "context_source"
    return "declared_evidence"


def usage_effect(direction: dict[str, Any]) -> str:
    """按方向结局标注关联效果，供后续审计知识是否被有效使用。"""

    if direction.get("decision") == "promoted":
        return "associated_with_promotion"
    if direction.get("status") == "strategy_infeasible":
        return "associated_with_invalid_or_rejected_candidate"
    if direction.get("status") == "no_improvement":
        return "associated_with_legal_no_improvement"
    return "associated_with_observation"


def summarize_skill_usage_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for record in records:
        kind = str(record.get("source_kind") or "unknown")
        effect = str(record.get("effect") or "unknown")
        by_kind.setdefault(kind, {})
        by_kind[kind][effect] = by_kind[kind].get(effect, 0) + 1
    return {
        "record_count": len(records),
        "by_source_kind": by_kind,
        "promoted_usage_count": sum(1 for item in records if item.get("effect") == "associated_with_promotion"),
    }


def direction_graph_guidance(directions: list[dict[str, Any]]) -> list[str]:
    if not directions:
        return ["Start with a small, auditable direction and require explicit rule/operator hypotheses."]
    guidance = [
        "Treat each direction as a hypothesis lifecycle: repair/refine attempts first, then promote, prune, or mutate.",
        "Preserve promoted directions as parents; do not replace them without an explicit ablation fallback.",
    ]
    if any(item.get("status") == "no_improvement" for item in directions[-3:]):
        guidance.append("Recent legal no-improvement directions should be mutated materially before spending another direction.")
    if any(item.get("status") == "strategy_infeasible" for item in directions[-3:]):
        guidance.append("Recent infeasible directions should trigger legality or patch-shape repair before objective tuning.")
    return guidance


def experience_guidance(
    lessons: list[dict[str, Any]],
    usage_records: list[dict[str, Any]],
    quality_memory: dict[str, Any] | None = None,
    semantic_memory: dict[str, Any] | None = None,
) -> list[str]:
    guidance = [
        "Inject only candidate lessons whose applicability matches the current contract and problem family.",
        "Keep candidate lessons separate from curated skills until repeated success or human review.",
    ]
    if any(item.get("lesson_type") == "successful_strategy" for item in lessons):
        guidance.append("Preserve method-level structure from promoted lessons, but never use prior solution files or scores as solver inputs.")
    if any(item.get("lesson_type") == "failure_pattern" for item in lessons):
        guidance.append("Recall repeated failure patterns as negative memory and require a material implementation difference.")
    if usage_records and not any(item.get("source_kind") == "knowledge_card" for item in usage_records):
        guidance.append("Future directions should explicitly cite relevant knowledge cards or explain why local evidence overrides them.")
    quality_memory = quality_memory or {}
    if int(quality_memory.get("rejected_attempt_count", 0) or 0) > 0:
        guidance.append(
            "Repair recurring agent-generated parser/representation/constructor/decoder/self-check gaps before spending another direction on objective tuning."
        )
    semantic_memory = semantic_memory or {}
    if int(semantic_memory.get("repair_required_attempt_count", 0) or 0) > 0:
        guidance.append(
            "Preserve recovered algorithm semantics and rerun remembered behavioral tests before repeating named-method claims."
        )
    return guidance


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    result: list[float] = []
    for item in value:
        if isinstance(item, (int, float)):
            result.append(float(item))
    return result


def _bounded_text(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value)
    return text[:limit]


def _bounded_strings(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:500] for item in value[:limit] if item is not None]


def _dedupe_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def counted_items(values: list[str], *, limit: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    first_text: dict[str, str] = {}
    for value in values:
        key = value.strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        first_text.setdefault(key, value)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [{"text": first_text[key], "count": count} for key, count in ordered]


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")[:500]
