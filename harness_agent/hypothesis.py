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
