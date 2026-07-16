"""面向 Coding Worker 的上下文视图与优先级整理。"""

from __future__ import annotations

from typing import Any

from harness_agent.context.compaction import (
    STABLE_WORKER_CONTEXT_MAX_CHARS,
    compact_json,
    stable_worker_context,
)
from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract


WORKER_DYNAMIC_CONTEXT_MAX_CHARS = 32_000


def worker_context_sections(context: dict[str, Any]) -> dict[str, str]:
    """拆分 worker 视角的稳定区和动态区。

    稳定区复用 `stable_worker_context()` 的缓存友好前缀；动态区只放本轮假设、
    loop feedback、激活的方法包和增量编辑规则，便于在多轮迭代中缩小重传范围。
    """

    stable_payload = stable_worker_context(context)
    active_package = (
        context.get("active_method_package")
        if isinstance(context.get("active_method_package"), dict)
        else {}
    )
    if active_package:
        # OpenCode 可以按 assets 路径直接读取知识文件；重复注入 asset_records
        # 会把参考实现和契约文本再次塞进每个内部工具回合。
        stable_payload["active_method_package"] = {
            key: value
            for key, value in active_package.items()
            if key != "asset_records"
        }
        catalog = (
            context.get("method_package_catalog")
            if isinstance(context.get("method_package_catalog"), dict)
            else {}
        )
        stable_payload["method_package_catalog"] = {
            "status": catalog.get("status"),
            "problem_family": catalog.get("problem_family"),
            "active_features": catalog.get("active_features") or [],
            "recommended_package_id": catalog.get("recommended_package_id"),
            "available_package_ids": [
                str(item.get("package_id") or "")
                for item in catalog.get("packages") or []
                if isinstance(item, dict) and str(item.get("package_id") or "").strip()
            ],
        }
    stable = compact_json(stable_payload, max_chars=STABLE_WORKER_CONTEXT_MAX_CHARS).text
    dynamic_payload = {
        # 质量契约由需求/IO 派生，同一任务内稳定，放在动态区最前以延长缓存前缀。
        "agent_generated_solver_quality_contract": build_agent_generated_solver_quality_contract(context),
        "round_type": (
            "improvement_round"
            if (context.get("iteration_edit_contract") or {}).get("mode") == "incremental_after_baseline"
            else "baseline_or_single_round"
        ),
        "iteration_edit_contract": context.get("iteration_edit_contract") or {},
        "hypothesis": context.get("hypothesis") or "",
        # OpenCode 在候选 worktree 中读取真实源码，这里只保留路径和哈希。
        "incumbent_code_context": incumbent_code_metadata(context.get("incumbent_code_context")),
        "loop_feedback": compact_worker_loop_feedback(context.get("loop_feedback")),
        "worker_instruction_delta": {
            key: (context.get("worker_instruction") or {}).get(key)
            for key in ("required_order", "round_feedback_rule", "incremental_edit_rule")
            if (context.get("worker_instruction") or {}).get(key) is not None
        },
        "context_compaction": context.get("context_compaction") or {},
    }
    dynamic = compact_json(dynamic_payload, max_chars=WORKER_DYNAMIC_CONTEXT_MAX_CHARS).text
    return {"stable": stable, "dynamic": dynamic}


def incumbent_code_metadata(value: Any) -> dict[str, Any]:
    """去掉会由 OpenCode 再次 Read 的 incumbent 源码正文。"""

    if not isinstance(value, dict):
        return {}
    files = []
    for item in value.get("files") or []:
        if not isinstance(item, dict):
            continue
        files.append(
            {
                key: item.get(key)
                for key in ("relative_path", "path", "exists", "sha256", "chars", "error")
                if item.get(key) is not None
            }
        )
    return {
        "source": value.get("source"),
        "root": value.get("root"),
        "purpose": value.get("purpose"),
        "files": files[:8],
        "worker_rule": "Read the listed relative_path from the current worktree when source is needed.",
    }


def compact_worker_loop_feedback(value: Any) -> dict[str, Any]:
    """保留方向与最新修补证据，同时把完整契约改成稳定区引用。"""

    if not isinstance(value, dict):
        return {}
    feedback = dict(value)
    direction = (
        dict(feedback.get("current_direction_plan"))
        if isinstance(feedback.get("current_direction_plan"), dict)
        else {}
    )
    bundle = (
        direction.get("implementation_bundle")
        if isinstance(direction.get("implementation_bundle"), dict)
        else {}
    )
    if bundle:
        direction["implementation_bundle"] = {
            "contract_id": bundle.get("contract_id"),
            "mode": bundle.get("mode"),
            "contract_paths": bundle.get("contract_paths") or [],
            "required_component_ids": [
                str(item.get("component_id") or "")
                for item in bundle.get("required_components") or []
                if isinstance(item, dict) and str(item.get("component_id") or "").strip()
            ],
            "coupled_group_ids": [
                str(item.get("group_id") or "")
                for item in bundle.get("coupled_groups") or []
                if isinstance(item, dict) and str(item.get("group_id") or "").strip()
            ],
            "full_contract_location": "stable.active_method_package.implementation_contract",
        }
        feedback["current_direction_plan"] = direction
    current_repair = feedback.get("current_round_repair")
    if isinstance(current_repair, dict):
        feedback["current_round_repair"] = compact_current_round_repair(current_repair)
    return feedback


def compact_current_round_repair(value: dict[str, Any]) -> dict[str, Any]:
    """把修补证据压成完整剩余清单，避免通用字符压缩截断组件尾部。"""

    result = {
        key: value.get(key)
        for key in ("status", "attempt_index", "max_repair_attempts", "must_do", "avoid")
        if value.get(key) not in (None, "", [], {})
    }
    targets = dict(value.get("repair_targets") or {}) if isinstance(value.get("repair_targets"), dict) else {}
    semantic = (
        targets.get("algorithm_semantic_review")
        if isinstance(targets.get("algorithm_semantic_review"), dict)
        else {}
    )
    if semantic:
        targets["algorithm_semantic_review"] = {
            "status": semantic.get("status"),
            "summaries": (semantic.get("summaries") or [])[:4],
            "blocking_findings": [
                {
                    key: item.get(key)
                    for key in (
                        "finding_id",
                        "category",
                        "source_path",
                        "line_start",
                        "line_end",
                        "knowledge_path",
                        "repair",
                        "required_test",
                    )
                    if item.get(key) not in (None, "")
                }
                for item in semantic.get("blocking_findings") or []
                if isinstance(item, dict)
            ],
            "implementation_coverage": [
                compact_component_repair_coverage(item)
                for item in semantic.get("implementation_coverage") or []
                if isinstance(item, dict)
            ],
            "coupled_group_coverage": [
                {
                    "group_id": item.get("group_id"),
                    "status": item.get("status"),
                    "missing_behavior": str(item.get("missing_behavior") or "")[:500],
                }
                for item in semantic.get("coupled_group_coverage") or []
                if isinstance(item, dict)
            ],
            "knowledge_paths": semantic.get("knowledge_paths") or [],
        }
    result["repair_targets"] = targets
    result["previous_attempts"] = [
        compact_previous_repair_attempt(item)
        for item in value.get("previous_attempts") or []
        if isinstance(item, dict)
    ]
    return result


def compact_component_repair_coverage(value: dict[str, Any]) -> dict[str, Any]:
    behavior_rows = [
        item
        for item in value.get("behavior_coverage") or []
        if isinstance(item, dict) and item.get("status") != "implemented"
    ]
    return {
        "component_id": value.get("component_id"),
        "status": value.get("status"),
        "missing_behavior_indexes": [item.get("behavior_index") for item in behavior_rows],
        "missing_behaviors": [str(item)[:500] for item in value.get("missing_behaviors") or []],
    }


def compact_previous_repair_attempt(value: dict[str, Any]) -> dict[str, Any]:
    semantic = value.get("semantic_review") if isinstance(value.get("semantic_review"), dict) else {}
    return {
        key: value.get(key)
        for key in (
            "attempt_index",
            "worker_status",
            "changed_files",
            "failure_signatures",
            "candidate_key",
            "summary",
        )
        if value.get(key) not in (None, "", [], {})
    } | (
        {
            "semantic_review": {
                "status": semantic.get("status"),
                "accepted": semantic.get("accepted"),
                "summary": str(semantic.get("summary") or "")[:500],
            }
        }
        if semantic
        else {}
    )
