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
