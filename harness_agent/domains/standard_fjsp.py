"""标准 FJSP/FJSP-SDST 算例诊断，只提取规模与约束特征。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_agent.core.models import TaskContract
from harness_agent.domains.io import parse_standard_fjsp


@dataclass(frozen=True)
class StandardFjspContextProvider:
    """标准 FJSP/FJSP-SDST 的实例诊断适配器。

    它只负责从算例中抽取规模、候选机稀疏度、setup 形态、best-known 诊断等
    与“选方法、控预算”相关的特征，不负责生成调度或评价调度优劣。
    """

    def inspect_instances(self, contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
        """读取 contract 中声明的实例并汇总诊断。

        返回结果会进入 Context Packet 的 `instance_diagnostics`，供知识卡选择、
        Method Package 推荐和 worker 的方向控制使用。
        """

        best_known_csv = _resolve_optional_context_path(
            contract.resources.get("best_known_csv"),
            project_root=project_root,
            base_dir=contract.source_path.parent,
        )
        detailed: list[dict[str, Any]] = []
        profiled: list[dict[str, Any]] = []
        for instance_spec in contract.instances:
            instance_path = _resolve_context_path(
                instance_spec.path,
                project_root=project_root,
                base_dir=contract.source_path.parent,
            )
            payload = _single_instance_diagnostics(
                instance_id=instance_spec.id,
                path=instance_path,
                best_known_csv=best_known_csv,
            )
            detailed.append(payload)
            if payload.get("parsed"):
                profiled.append(payload)

        status = (
            "available"
            if profiled and len(profiled) == len(contract.instances)
            else "partial"
            if profiled
            else "unavailable"
        )
        sdst_instances = [item for item in profiled if item.get("variant") == "fjsp_sdst"]
        nfa_instances = [item for item in profiled if item.get("variant") == "fjsp_machine_availability"]
        best_known_count = sum(1 for item in profiled if item.get("best_known_makespan") is not None)
        summary = {
            "instance_count": len(contract.instances),
            "profiled_count": len(profiled),
            "sdst_instance_count": len(sdst_instances),
            "nfa_instance_count": len(nfa_instances),
            "shape_group_count": len(_instance_shape_groups(profiled)),
            "setup_time_kinds": sorted(
                {str(item.get("setup_time_kind")) for item in profiled if item.get("setup_time_kind")}
            ),
            "max_operation_count": max((int(item.get("operation_count", 0) or 0) for item in profiled), default=0),
            "max_scale": max((int(item.get("scale", 0) or 0) for item in profiled), default=0),
            "avg_candidate_count": _rounded_average(
                float(item.get("avg_candidate_count", 0.0) or 0.0) for item in profiled
            ),
            "avg_flexible_operation_ratio": _rounded_average(
                float(item.get("flexible_operation_ratio", 0.0) or 0.0) for item in profiled
            ),
            "avg_duration_spread_ratio": _rounded_average(
                float(item.get("duration_spread_ratio_avg", 0.0) or 0.0) for item in profiled
            ),
            "max_duration_spread_ratio": max(
                (float(item.get("duration_spread_ratio_max", 0.0) or 0.0) for item in profiled),
                default=0.0,
            ),
            "max_machine_eligibility_cv": max(
                (float(item.get("machine_eligibility_cv", 0.0) or 0.0) for item in profiled),
                default=0.0,
            ),
            "max_fractional_min_load_cv": max(
                (float(item.get("fractional_min_load_cv", 0.0) or 0.0) for item in profiled),
                default=0.0,
            ),
            "max_setup_to_processing_avg_ratio": max(
                (float(item.get("setup_to_processing_avg_ratio", 0.0) or 0.0) for item in profiled),
                default=0.0,
            ),
            "best_known_available_count": best_known_count,
            "best_known_semantics": "diagnostic_only_score_remains_negative_makespan",
        }
        return {
            "status": status,
            "summary": summary,
            "direction_hints": _instance_direction_hints(summary, profiled),
            "best_known_csv": str(best_known_csv) if best_known_csv else None,
            "shape_groups": _instance_shape_group_summaries(profiled),
            "instances": _representative_instance_diagnostics(detailed, limit=12),
            "truncated": len(detailed) > 12,
        }

    def active_features(
        self,
        *,
        contract: TaskContract,
        instance_diagnostics: dict[str, Any],
        contract_review_evidence: dict[str, Any],
    ) -> list[str]:
        """根据实例诊断和文档证据判断当前激活特征。

        优先相信真实解析出来的实例形态；只有在实例尚未成功解析时，才回退到
        Task Contract 文本和 review 证据里的 SDST 关键词。
        """

        summary = instance_diagnostics.get("summary") if isinstance(instance_diagnostics.get("summary"), dict) else {}
        setup_kinds = [str(kind).strip().lower() for kind in summary.get("setup_time_kinds") or []]
        diagnostics_have_shape = (
            instance_diagnostics.get("status") in {"available", "partial"}
            and int(summary.get("profiled_count") or 0) > 0
        )
        diagnostics_show_sdst = (
            int(summary.get("sdst_instance_count") or 0) > 0
            or any(kind not in {"", "none", "null"} for kind in setup_kinds)
        )
        if diagnostics_show_sdst:
            return ["fjsp_sdst", "sequence_dependent_setup", "setup_time"]
        if diagnostics_have_shape:
            return []

        text = json.dumps(
            {
                "description": contract.description,
                "review": contract_review_evidence,
            },
            ensure_ascii=False,
        ).lower()
        if re.search(r"\bfjsp[-_]?sdst\b|\bsequence[-_\s]?dependent[-_\s]?setup\b|\bsetup[-_\s]?matrix\b", text):
            return ["fjsp_sdst", "sequence_dependent_setup", "setup_time"]
        return []

    def solution_contract(self) -> dict[str, Any]:
        """Expose the exact schema consumed by ``domains.io.load_solution``."""

        return {
            "format": "standard_fjsp_schedule_v1",
            "required_top_level_fields": ["format", "makespan", "schedule"],
            "schedule_record_fields": ["job_id", "op_id", "machine_id", "start", "end"],
            "indexing": "job_id, op_id, and machine_id are 0-based integers",
            "legality_owner": "AlgoForge Core evaluator",
        }


def _single_instance_diagnostics(
    *,
    instance_id: str,
    path: Path,
    best_known_csv: Path | None,
) -> dict[str, Any]:
    """对单个实例做只读体检。

    输出强调“可供上下文消费的特征”，例如规模、候选机数、setup 密度和
    best-known 参考值；不会把任何启发式搜索状态混进来。
    """

    payload: dict[str, Any] = {
        "id": instance_id,
        "path": str(path),
        "exists": path.exists(),
        "parsed": False,
    }
    try:
        instance = parse_standard_fjsp(path)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not fail context generation.
        payload["error"] = str(exc)
        return payload

    operations = [op for job in instance.jobs for op in job.operations]
    candidate_counts = [len(op.candidates) for op in operations]
    durations = [candidate.duration for op in operations for candidate in op.candidates]
    duration_spreads = [
        max(candidate.duration for candidate in op.candidates)
        - min(candidate.duration for candidate in op.candidates)
        for op in operations
    ]
    duration_spread_ratios = [
        _round_float(
            (max(candidate.duration for candidate in op.candidates) - min(candidate.duration for candidate in op.candidates))
            / max(1, min(candidate.duration for candidate in op.candidates))
        )
        for op in operations
    ]
    machine_eligibility_counts = [0.0 for _ in range(instance.machine_count)]
    fractional_min_loads = [0.0 for _ in range(instance.machine_count)]
    mandatory_loads = [0.0 for _ in range(instance.machine_count)]
    for op in operations:
        minimum_duration = min(candidate.duration for candidate in op.candidates)
        share = minimum_duration / max(1, len(op.candidates))
        for candidate in op.candidates:
            machine_eligibility_counts[candidate.machine_id] += 1.0
            fractional_min_loads[candidate.machine_id] += share
        if len(op.candidates) == 1:
            mandatory_loads[op.candidates[0].machine_id] += op.candidates[0].duration
    job_min_workloads = [
        sum(min(candidate.duration for candidate in op.candidates) for op in job.operations)
        for job in instance.jobs
    ]
    setup_stats = _setup_matrix_stats(instance.setup_times)
    processing_avg = _rounded_average(float(value) for value in durations)
    setup_ratio = (
        _round_float(float(setup_stats["avg_nonzero"]) / processing_avg)
        if processing_avg > 0 and setup_stats["avg_nonzero"] is not None
        else 0.0
    )
    best_known = _load_best_known_diagnostic(best_known_csv, instance.name)
    payload.update(
        {
            "parsed": True,
            "name": instance.name,
            "variant": "fjsp_sdst" if instance.has_sequence_dependent_setup else "standard_fjsp",
            "job_count": instance.job_count,
            "machine_count": instance.machine_count,
            "operation_count": instance.operation_count,
            "max_candidate_count": instance.max_candidate_count,
            "scale": instance.job_count * instance.machine_count * instance.operation_count,
            "jobs_per_machine": _round_float(instance.job_count / max(1, instance.machine_count)),
            "operations_per_machine": _round_float(instance.operation_count / max(1, instance.machine_count)),
            "avg_candidate_count": _rounded_average(float(value) for value in candidate_counts),
            "min_candidate_count": min(candidate_counts, default=0),
            "max_observed_candidate_count": max(candidate_counts, default=0),
            "candidate_count_cv": _coefficient_of_variation(candidate_counts),
            "flexible_operation_ratio": _round_float(
                sum(1 for value in candidate_counts if value > 1) / max(1, len(candidate_counts))
            ),
            "full_flexibility_ratio": _round_float(
                sum(1 for value in candidate_counts if value == instance.machine_count)
                / max(1, len(candidate_counts))
            ),
            "processing_time_min": min(durations, default=0),
            "processing_time_max": max(durations, default=0),
            "processing_time_avg": processing_avg,
            "processing_time_cv": _coefficient_of_variation(durations),
            "duration_spread_avg": _rounded_average(duration_spreads),
            "duration_spread_ratio_avg": _rounded_average(duration_spread_ratios),
            "duration_spread_ratio_max": max(duration_spread_ratios, default=0.0),
            "machine_eligibility_cv": _coefficient_of_variation(machine_eligibility_counts),
            "fractional_min_load_cv": _coefficient_of_variation(fractional_min_loads),
            "mandatory_load_max": max(mandatory_loads, default=0.0),
            "job_min_workload_max": max(job_min_workloads, default=0.0),
            "setup_time_kind": instance.setup_time_kind,
            "setup_entry_count": setup_stats["entry_count"],
            "setup_nonzero_count": setup_stats["nonzero_count"],
            "setup_density": setup_stats["density"],
            "setup_time_min_positive": setup_stats["min_positive"],
            "setup_time_max": setup_stats["max"],
            "setup_time_avg_nonzero": setup_stats["avg_nonzero"],
            "setup_time_avg_all": setup_stats["avg_all"],
            "setup_to_processing_avg_ratio": setup_ratio,
            "best_known_makespan": best_known,
            "best_known_diagnostic_only": best_known is not None,
        }
    )
    return payload


def _setup_matrix_stats(setup_times: Any) -> dict[str, Any]:
    entry_count = 0
    total = 0
    nonzero_count = 0
    nonzero_total = 0
    min_positive: int | None = None
    max_value = 0
    for machine_matrix in setup_times or ():
        for row in machine_matrix:
            for raw_value in row:
                value = int(raw_value)
                entry_count += 1
                total += value
                max_value = max(max_value, value)
                if value > 0:
                    nonzero_count += 1
                    nonzero_total += value
                    min_positive = value if min_positive is None else min(min_positive, value)
    return {
        "entry_count": entry_count,
        "nonzero_count": nonzero_count,
        "density": _round_float(nonzero_count / entry_count) if entry_count else 0.0,
        "min_positive": min_positive,
        "max": max_value,
        "avg_nonzero": _round_float(nonzero_total / nonzero_count) if nonzero_count else 0.0,
        "avg_all": _round_float(total / entry_count) if entry_count else 0.0,
    }


def _instance_shape_groups(profiled: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in profiled:
        groups.setdefault(_instance_shape_key(item), []).append(item)
    return groups


def _instance_shape_key(item: dict[str, Any]) -> str:
    return (
        f"j{int(item.get('job_count', 0) or 0)}_"
        f"m{int(item.get('machine_count', 0) or 0)}_"
        f"ops{int(item.get('operation_count', 0) or 0)}_"
        f"c{int(item.get('max_candidate_count', 0) or 0)}_"
        f"{item.get('setup_time_kind') or 'none'}"
    )


def _instance_shape_group_summaries(profiled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按实例形状聚类，帮助 worker 识别是否存在多模态 benchmark。"""

    summaries: list[dict[str, Any]] = []
    for key, items in _instance_shape_groups(profiled).items():
        first = items[0]
        best_known_values = [
            float(item["best_known_makespan"])
            for item in items
            if isinstance(item.get("best_known_makespan"), (int, float))
        ]
        setup_ratios = [float(item.get("setup_to_processing_avg_ratio", 0.0) or 0.0) for item in items]
        summaries.append(
            {
                "shape_key": key,
                "count": len(items),
                "instance_ids": [str(item.get("id") or item.get("name") or "") for item in items],
                "job_count": int(first.get("job_count", 0) or 0),
                "machine_count": int(first.get("machine_count", 0) or 0),
                "operation_count": int(first.get("operation_count", 0) or 0),
                "max_candidate_count": int(first.get("max_candidate_count", 0) or 0),
                "scale": int(first.get("scale", 0) or 0),
                "setup_time_kind": first.get("setup_time_kind"),
                "avg_candidate_count": _rounded_average(
                    float(item.get("avg_candidate_count", 0.0) or 0.0) for item in items
                ),
                "flexible_operation_ratio_avg": _rounded_average(
                    float(item.get("flexible_operation_ratio", 0.0) or 0.0) for item in items
                ),
                "duration_spread_ratio_avg": _rounded_average(
                    float(item.get("duration_spread_ratio_avg", 0.0) or 0.0) for item in items
                ),
                "machine_eligibility_cv_avg": _rounded_average(
                    float(item.get("machine_eligibility_cv", 0.0) or 0.0) for item in items
                ),
                "fractional_min_load_cv_avg": _rounded_average(
                    float(item.get("fractional_min_load_cv", 0.0) or 0.0) for item in items
                ),
                "setup_to_processing_avg_ratio_avg": _rounded_average(setup_ratios),
                "setup_to_processing_avg_ratio_max": max(setup_ratios, default=0.0),
                "best_known_min": min(best_known_values) if best_known_values else None,
                "best_known_max": max(best_known_values) if best_known_values else None,
            }
        )
    return sorted(
        summaries,
        key=lambda item: (
            -int(item.get("scale", 0) or 0),
            -int(item.get("count", 0) or 0),
            str(item.get("shape_key") or ""),
        ),
    )


def _representative_instance_diagnostics(detailed: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """从全部诊断中选一组代表样本。

    Context Packet 不会塞入每个实例的完整诊断，而是优先保留失败样本、每类形状
    的代表实例和极端 setup/规模样本，兼顾长度与可解释性。
    """

    if len(detailed) <= limit:
        return detailed

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def item_key(item: dict[str, Any]) -> str:
        return str(item.get("path") or item.get("id") or item.get("name") or len(seen))

    def add(item: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        key = item_key(item)
        if key in seen:
            return
        selected.append(item)
        seen.add(key)

    for item in [item for item in detailed if not item.get("parsed")][:2]:
        add(item)
    parsed = [item for item in detailed if item.get("parsed")]
    for group_items in _instance_shape_groups(parsed).values():
        ordered = sorted(group_items, key=lambda item: str(item.get("id") or item.get("name") or item.get("path") or ""))
        for index in sorted({0, len(ordered) // 2, len(ordered) - 1}):
            add(ordered[index])
    for item in sorted(
        parsed,
        key=lambda item: (
            float(item.get("setup_to_processing_avg_ratio", 0.0) or 0.0),
            int(item.get("scale", 0) or 0),
            float(item.get("best_known_makespan", 0.0) or 0.0),
        ),
        reverse=True,
    ):
        add(item)
    for item in detailed:
        add(item)
        if len(selected) >= limit:
            break
    return selected


def _instance_direction_hints(summary: dict[str, Any], profiled: list[dict[str, Any]]) -> list[str]:
    if not profiled:
        return ["Instance parsing failed or no instances were supplied; do not infer scale or setup from filenames."]

    hints = ["Use actual parsed instance content, not filename shape, when choosing budget-sensitive strategies."]
    if int(summary.get("shape_group_count", 0) or 0) > 1:
        hints.append(
            "Multiple instance shapes are present; inspect shape_groups and avoid overfitting a single instance/seed probe."
        )
    if int(summary.get("sdst_instance_count", 0) or 0) > 0:
        hints.append("Sequence-dependent setup is active; treat setup state and timing as required problem semantics.")
        setup_ratio = float(summary.get("max_setup_to_processing_avg_ratio", 0.0) or 0.0)
        if setup_ratio >= 0.75:
            hints.append("Setup is large relative to processing; record this ratio as first-stage selection evidence.")
        elif setup_ratio >= 0.25:
            hints.append("Setup is material; any later direction must preserve setup-aware timing semantics.")
    else:
        hints.append("No sequence-dependent setup matrix was detected in the parsed instances.")
    if int(summary.get("nfa_instance_count", 0) or 0) > 0:
        hints.append("Machine availability constraints are active; dispatch and insertion must check intervals before committing a start time.")
    hints.append(
        "Measured assignment structure: "
        f"avg_candidates={summary.get('avg_candidate_count', 0.0)}, "
        f"flexible_operation_ratio={summary.get('avg_flexible_operation_ratio', 0.0)}, "
        f"duration_spread_ratio={summary.get('avg_duration_spread_ratio', 0.0)}. "
        "Use the method-selection cards and current benchmark distribution before classifying flexibility."
    )
    hints.append(
        "Measured machine concentration: "
        f"eligibility_cv_max={summary.get('max_machine_eligibility_cv', 0.0)}, "
        f"fractional_min_load_cv_max={summary.get('max_fractional_min_load_cv', 0.0)}. "
        "These are evidence inputs, not a backend-selected algorithm direction."
    )
    if int(summary.get("best_known_available_count", 0) or 0) > 0:
        hints.append("Best-known/LB/UB values are diagnostics only; promotion remains fixed-evaluator objective improvement.")
    return hints


def _resolve_optional_context_path(
    path: Path | None,
    *,
    project_root: Path | None,
    base_dir: Path,
) -> Path | None:
    if path is None:
        return None
    resolved = _resolve_context_path(path, project_root=project_root, base_dir=base_dir)
    return resolved if resolved.exists() else None


def _resolve_context_path(path: Path, *, project_root: Path | None, base_dir: Path) -> Path:
    if path.is_absolute():
        return path
    repo_root = Path(__file__).resolve().parents[2]
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / path)
    candidates.extend([base_dir / path, repo_root / path])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _load_best_known_diagnostic(path: Path | None, instance_name: str) -> float | None:
    if path is None:
        return None
    try:
        from examples.standard_fjsp_evaluator import load_best_known

        return load_best_known(path, instance_name)
    except Exception:  # noqa: BLE001 - diagnostics must not fail context generation.
        return None


def _rounded_average(values: Any) -> float:
    values_list = [float(value) for value in values]
    if not values_list:
        return 0.0
    return _round_float(sum(values_list) / len(values_list))


def _coefficient_of_variation(values: Any) -> float:
    values_list = [float(value) for value in values]
    if not values_list:
        return 0.0
    average = sum(values_list) / len(values_list)
    if average == 0:
        return 0.0
    variance = sum((value - average) ** 2 for value in values_list) / len(values_list)
    return _round_float((variance**0.5) / average)


def _round_float(value: float) -> float:
    return round(float(value), 6)
