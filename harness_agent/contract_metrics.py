"""Contract-facing Full/None comparison derived from fixed Core manifests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any


@dataclass(frozen=True)
class ContractThresholds:
    feasibility_points: float = 8.0
    quality_percent: float = 5.0
    efficiency_percent: float = 10.0
    bonus_feasibility_points: float = 16.0
    bonus_quality_percent: float = 10.0
    bonus_efficiency_percent: float = 30.0
    quality_equivalence_percent: float = 1.0


def build_contract_comparison(
    *,
    full_manifest_paths: list[Path],
    none_manifest_paths: list[Path],
    output_dir: Path,
    thresholds: ContractThresholds | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or ContractThresholds()
    full = [_load_manifest(path) for path in full_manifest_paths]
    none = [_load_manifest(path) for path in none_manifest_paths]
    checks = comparison_protocol_checks(full=full, none=none)
    metrics = comparison_metrics(full=full, none=none, thresholds=thresholds)
    protocol_valid = all(item["passed"] for item in checks)
    verdicts = contract_verdicts(metrics=metrics, thresholds=thresholds, protocol_valid=protocol_valid)
    payload = {
        "schema_version": 1,
        "status": "comparable" if protocol_valid else "protocol_invalid",
        "scope": "standard_fjsp_guidance_ablation",
        "full_manifests": [str(path.resolve()) for path in full_manifest_paths],
        "none_manifests": [str(path.resolve()) for path in none_manifest_paths],
        "protocol_checks": checks,
        "metrics": metrics,
        "thresholds": thresholds.__dict__,
        "verdicts": verdicts,
        "risk_notes": comparison_risk_notes(full=full, none=none, metrics=metrics),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "contract_comparison.json"
    report_path = output_dir / "contract_comparison.md"
    payload["artifacts"] = {"json": str(json_path.resolve()), "report": str(report_path.resolve())}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_contract_comparison(payload), encoding="utf-8")
    return payload


def comparison_protocol_checks(*, full: list[dict[str, Any]], none: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(check: str, passed: bool, detail: str) -> None:
        checks.append({"check": check, "passed": bool(passed), "detail": detail})

    add("non_empty_lanes", bool(full and none), f"full={len(full)}, none={len(none)}")
    add("equal_run_count", len(full) == len(none), f"full={len(full)}, none={len(none)}")
    add(
        "guidance_modes",
        all(_request(item).get("guidance_mode") == "full" for item in full)
        and all(_request(item).get("guidance_mode") == "none" for item in none),
        "Full must use full and baseline must use none.",
    )
    add(
        "all_runs_completed",
        all(item.get("status") == "ok" for item in [*full, *none]),
        "Every manifest must have status=ok.",
    )
    paired = list(zip(full, none)) if len(full) == len(none) else []
    add(
        "same_inputs_and_evaluator",
        bool(paired) and all(_input_signature(a) == _input_signature(b) for a, b in paired),
        "Document/instance hashes, evaluator command, and objectives must match per pair.",
    )
    add(
        "same_budgets",
        bool(paired) and all(_budget_signature(a) == _budget_signature(b) for a, b in paired),
        "Seeds, iterations, solver timeout, controller budget, worker count, and lane count must match.",
    )
    declared_models = [
        _agent_model_signature(item)
        for item in [*full, *none]
        if any(_agent_model_signature(item))
    ]
    add(
        "same_agent_models",
        not declared_models
        or (
            bool(paired)
            and all(
                _agent_model_signature(a) == _agent_model_signature(b)
                for a, b in paired
            )
        ),
        (
            "Main and Coding Worker model identifiers must match per pair. "
            "Legacy manifests without model metadata require command-artifact audit."
        ),
    )
    add(
        "actual_lane_counts_match",
        bool(paired)
        and all(
            _actual_lane_counts(a) == _actual_lane_counts(b)
            and len(_actual_lane_counts(a)) == int(_request(a).get("iterations", 0) or 0)
            and all(
                count == int(_request(a).get("max_competing_workers", 1) or 1)
                for count in _actual_lane_counts(a)
            )
            for a, b in paired
        ),
        "Every direction round must start the configured number of lanes in both modes.",
    )
    add(
        "lanes_are_distinct_methods",
        all(_method_lane_contract_passes(item) for item in [*full, *none]),
        "Lane declarations must name distinct algorithm methods, not generic worker roles.",
    )
    shared_baseline = bool(paired) and all(
        a.get("baseline_source") == "provided_project"
        and b.get("baseline_source") == "provided_project"
        and a.get("baseline_key") == b.get("baseline_key")
        for a, b in paired
    )
    add(
        "frozen_shared_baseline",
        shared_baseline,
        "Each pair must start from the same Core-valid provided-project baseline and objective key.",
    )
    add(
        "controller_timing_available",
        all(_controller_seconds(item) is not None for item in [*full, *none]),
        "New manifests must separate controller wall time from fixed Core evaluation intervals.",
    )
    return checks


def comparison_metrics(
    *, full: list[dict[str, Any]], none: list[dict[str, Any]], thresholds: ContractThresholds
) -> dict[str, Any]:
    full_total, full_valid = _feasibility_counts(full)
    none_total, none_valid = _feasibility_counts(none)
    full_rate = _ratio(full_valid, full_total)
    none_rate = _ratio(none_valid, none_total)
    full_quality = _mean_quality(full)
    none_quality = _mean_quality(none)
    quality_gain = _improvement_percent(lower=full_quality, reference=none_quality)
    full_runtime = _mean_solver_wall(full)
    none_runtime = _mean_solver_wall(none)
    efficiency_gain = _improvement_percent(lower=full_runtime, reference=none_runtime)
    quality_difference = _absolute_percent_difference(full_quality, none_quality)
    return {
        "feasibility": {
            "full_valid": full_valid,
            "full_total": full_total,
            "full_rate": full_rate,
            "none_valid": none_valid,
            "none_total": none_total,
            "none_rate": none_rate,
            "gain_percentage_points": None
            if full_rate is None or none_rate is None
            else (full_rate - none_rate) * 100.0,
        },
        "quality": {
            "metric": "avg_makespan",
            "full_mean": full_quality,
            "none_mean": none_quality,
            "improvement_percent": quality_gain,
            "quality_difference_percent": quality_difference,
        },
        "efficiency": {
            "metric": "fixed_core_observed_solver_wall_seconds",
            "full_mean_seconds": full_runtime,
            "none_mean_seconds": none_runtime,
            "improvement_percent": efficiency_gain,
            "quality_equivalent": quality_difference is not None
            and quality_difference <= thresholds.quality_equivalence_percent,
        },
        "effective_iteration_rate": {
            "definition": "promoted_rounds / attempted_direction_rounds",
            "full": _effective_iteration_rate(full),
            "none": _effective_iteration_rate(none),
        },
        "controller": {
            "full_mean_seconds_excluding_core": _mean_optional([_controller_seconds(item) for item in full]),
            "none_mean_seconds_excluding_core": _mean_optional([_controller_seconds(item) for item in none]),
            "full_within_30_minutes": _within_controller_budget(full, 1800.0),
            "none_within_30_minutes": _within_controller_budget(none, 1800.0),
        },
        "lane_methods": {
            "full": [_declared_lane_methods(item) for item in full],
            "none": [_declared_lane_methods(item) for item in none],
        },
    }


def contract_verdicts(
    *, metrics: dict[str, Any], thresholds: ContractThresholds, protocol_valid: bool
) -> dict[str, Any]:
    feasibility = metrics["feasibility"]["gain_percentage_points"]
    quality = metrics["quality"]["improvement_percent"]
    efficiency = metrics["efficiency"]["improvement_percent"]
    quality_equivalent = metrics["efficiency"]["quality_equivalent"]

    def passed(value: float | None, threshold: float) -> bool:
        return protocol_valid and value is not None and value >= threshold

    contractual = {
        "feasibility_8_points": passed(feasibility, thresholds.feasibility_points),
        "quality_5_percent": passed(quality, thresholds.quality_percent),
        "efficiency_10_percent_at_equivalent_quality": quality_equivalent
        and passed(efficiency, thresholds.efficiency_percent),
    }
    bonus = {
        "feasibility_16_points": passed(feasibility, thresholds.bonus_feasibility_points),
        "quality_10_percent": passed(quality, thresholds.bonus_quality_percent),
        "efficiency_30_percent_at_equivalent_quality": quality_equivalent
        and passed(efficiency, thresholds.bonus_efficiency_percent),
    }
    return {
        "protocol_valid": protocol_valid,
        "contractual": contractual,
        "contractual_any_passed": any(contractual.values()),
        "bonus": bonus,
        "bonus_any_passed": any(bonus.values()),
    }


def comparison_risk_notes(
    *, full: list[dict[str, Any]], none: list[dict[str, Any]], metrics: dict[str, Any]
) -> list[str]:
    notes: list[str] = []
    instance_hashes = {
        item.get("sha256")
        for manifest in [*full, *none]
        for item in ((manifest.get("input_fingerprints") or {}).get("files") or [])
        if isinstance(item, dict) and item.get("sha256") not in {None, "unavailable"}
    }
    if len(instance_hashes) < 3:
        notes.append("Evidence covers fewer than three distinct input documents/instances; do not claim generalization.")
    if len(full) < 3 or len(none) < 3:
        notes.append("Fewer than three independent paired runs per lane; Agent-generation variance remains under-sampled.")
    if metrics["efficiency"]["full_mean_seconds"] is None:
        notes.append("Solver wall timing is unavailable; efficiency thresholds cannot be evaluated.")
    return notes


def render_contract_comparison(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    lines = [
        "# Standard FJSP Contract Comparison",
        "",
        f"- Status: `{payload['status']}`",
        f"- Protocol valid: `{payload['verdicts']['protocol_valid']}`",
        f"- Feasibility gain (percentage points): `{metrics['feasibility']['gain_percentage_points']}`",
        f"- Mean makespan improvement: `{metrics['quality']['improvement_percent']}`%",
        f"- Efficiency improvement at equivalent quality: `{metrics['efficiency']['improvement_percent']}`%",
        f"- Full controller time excluding Core: `{metrics['controller']['full_mean_seconds_excluding_core']}` s",
        f"- None controller time excluding Core: `{metrics['controller']['none_mean_seconds_excluding_core']}` s",
        "",
        "## Protocol Checks",
        "",
        "| Check | Passed | Detail |",
        "| --- | --- | --- |",
    ]
    for item in payload["protocol_checks"]:
        lines.append(f"| {item['check']} | {item['passed']} | {item['detail']} |")
    lines.extend(["", "## Threshold Verdicts", "", "```json", json.dumps(payload["verdicts"], ensure_ascii=False, indent=2), "```"])
    if payload["risk_notes"]:
        lines.extend(["", "## Risks", ""])
        lines.extend(f"- {note}" for note in payload["risk_notes"])
    return "\n".join(lines) + "\n"


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return payload


def _request(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("request")
    return value if isinstance(value, dict) else {}


def _input_signature(manifest: dict[str, Any]) -> str:
    value = manifest.get("input_fingerprints") or {}
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _budget_signature(manifest: dict[str, Any]) -> tuple[Any, ...]:
    request = _request(manifest)
    keys = (
        "seeds", "iterations", "timeout_seconds", "max_workers", "max_steps",
        "max_runtime_seconds", "promotion_repeats", "in_round_repair_attempts", "max_competing_workers",
    )
    return tuple(json.dumps(request.get(key), sort_keys=True) for key in keys)


def _agent_model_signature(manifest: dict[str, Any]) -> tuple[str, str]:
    request = _request(manifest)
    return (
        str(request.get("worker_model") or ""),
        str(request.get("main_agent_model") or ""),
    )


def _actual_lane_counts(manifest: dict[str, Any]) -> list[int]:
    counts: list[int] = []
    for item in manifest.get("rounds") or []:
        if not isinstance(item, dict):
            continue
        direction = item.get("direction_plan") if isinstance(item.get("direction_plan"), dict) else {}
        competition = (
            direction.get("competition_result")
            if isinstance(direction.get("competition_result"), dict)
            else {}
        )
        value = competition.get("candidate_count")
        if isinstance(value, int):
            counts.append(value)
    return counts


def _declared_lane_methods(manifest: dict[str, Any]) -> list[list[str]]:
    result: list[list[str]] = []
    for item in manifest.get("rounds") or []:
        if not isinstance(item, dict):
            continue
        direction = item.get("direction_plan") if isinstance(item.get("direction_plan"), dict) else {}
        methods = []
        for variant in direction.get("candidate_variants") or []:
            if not isinstance(variant, dict):
                continue
            name = str(variant.get("method_name") or variant.get("method_family") or "").strip()
            if name:
                methods.append(name)
        result.append(methods)
    return result


def _method_lane_contract_passes(manifest: dict[str, Any]) -> bool:
    declared = _declared_lane_methods(manifest)
    actual = _actual_lane_counts(manifest)
    if len(declared) != len(actual):
        return False
    return all(
        len(methods) == count
        and len({re.sub(r"[^a-z0-9]+", "", name.lower()) for name in methods}) == count
        for methods, count in zip(declared, actual)
    )


def _final_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("final_summary")
    return value if isinstance(value, dict) else {}


def _feasibility_counts(manifests: list[dict[str, Any]]) -> tuple[int, int]:
    total = sum(int(_final_summary(item).get("total", 0) or 0) for item in manifests)
    valid = sum(int(_final_summary(item).get("valid", 0) or 0) for item in manifests)
    return total, valid


def _quality(manifest: dict[str, Any]) -> float | None:
    summary = _final_summary(manifest)
    candidate = summary.get("best_candidate_metrics") or {}
    value = candidate.get("avg_makespan") if isinstance(candidate, dict) else None
    if value is None:
        best = summary.get("best_metrics") or {}
        value = best.get("makespan") if isinstance(best, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _solver_wall(manifest: dict[str, Any]) -> float | None:
    summary = _final_summary(manifest)
    candidate = summary.get("best_candidate_metrics") or {}
    value = candidate.get("avg_solver_wall_seconds") if isinstance(candidate, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _controller_seconds(manifest: dict[str, Any]) -> float | None:
    timing = manifest.get("execution_timing") or {}
    value = timing.get("controller_wall_seconds_excluding_core") if isinstance(timing, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def _mean_quality(manifests: list[dict[str, Any]]) -> float | None:
    return _mean_optional([_quality(item) for item in manifests])


def _mean_solver_wall(manifests: list[dict[str, Any]]) -> float | None:
    return _mean_optional([_solver_wall(item) for item in manifests])


def _mean_optional(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return fmean(present) if len(present) == len(values) and present else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _improvement_percent(*, lower: float | None, reference: float | None) -> float | None:
    if lower is None or reference is None or reference <= 0:
        return None
    return (reference - lower) / reference * 100.0


def _absolute_percent_difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or right <= 0:
        return None
    return abs(left - right) / right * 100.0


def _effective_iteration_rate(manifests: list[dict[str, Any]]) -> float | None:
    rounds = sum(int(item.get("round_count", 0) or 0) for item in manifests)
    promoted = sum(int(item.get("promoted_rounds", 0) or 0) for item in manifests)
    return _ratio(promoted, rounds)


def _within_controller_budget(manifests: list[dict[str, Any]], limit: float) -> bool | None:
    values = [_controller_seconds(item) for item in manifests]
    if not values or any(value is None for value in values):
        return None
    return all(value <= limit for value in values if value is not None)
