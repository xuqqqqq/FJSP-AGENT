"""结构化上下文压缩；保留最新证据和产物引用，不直接截断 JSON 字节。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


ROUND_CONTEXT_MAX_CHARS = 180_000
ROUND_FEEDBACK_MAX_CHARS = 44_000
STABLE_WORKER_CONTEXT_MAX_CHARS = 32_000


_TAIL_HISTORY_KEYS = {
    "attempts",
    "candidate_lessons",
    "directions",
    "previous_attempts",
    "previous_rounds",
    "raw_notes",
    "recent_directions",
    "rounds",
}


@dataclass(frozen=True)
class CompactedJson:
    """结构化压缩结果。

    `payload` 仍保持 JSON 可解析结构，`text` 是最终写入/发送给 worker 的文本，
    其余字段用于审计压缩前后规模和采用的压缩档位。
    """

    payload: Any
    text: str
    original_chars: int
    compacted: bool
    profile: str


def compact_json(
    value: Any,
    *,
    max_chars: int,
    ensure_ascii: bool = False,
) -> CompactedJson:
    """按字段语义压缩 JSON，保证输出仍可解析且关键事实可追溯。"""

    original_text = json.dumps(value, ensure_ascii=ensure_ascii, indent=2)
    if len(original_text) <= max_chars:
        return CompactedJson(
            payload=value,
            text=original_text,
            original_chars=len(original_text),
            compacted=False,
            profile="none",
        )

    # 按“逐步收紧”的档位尝试压缩，而不是一次性粗暴截断。这样更容易保留
    # 关键键名、最近历史和证据摘要，便于后续 round 做增量推理。
    profiles = [
        ("light", 2400, 16, 80, 10),
        ("medium", 1400, 10, 60, 8),
        ("strong", 800, 7, 45, 7),
        ("minimal", 420, 5, 32, 6),
        ("emergency", 220, 3, 24, 5),
    ]
    for name, max_string, max_list, max_dict, max_depth in profiles:
        payload = _compact_value(
            value,
            path=(),
            max_string=max_string,
            max_list=max_list,
            max_dict=max_dict,
            max_depth=max_depth,
        )
        text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=2)
        if len(text) <= max_chars:
            return CompactedJson(
                payload=payload,
                text=text,
                original_chars=len(original_text),
                compacted=True,
                profile=name,
            )

    payload = _root_fallback(value)
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=2)
    return CompactedJson(
        payload=payload,
        text=text,
        original_chars=len(original_text),
        compacted=True,
        profile="root_fallback",
    )


def stable_worker_context(context: dict[str, Any]) -> dict[str, Any]:
    """提取稳定区上下文。

    这里故意只保留任务事实、领域能力、审阅证据、项目扫描、实例诊断等
    “跨轮次大概率不变”的内容，用作 worker 提示词的缓存友好前缀。
    与之对应，`loop_feedback`、`hypothesis` 等会留在动态区。
    """

    worker_instruction = context.get("worker_instruction")
    if not isinstance(worker_instruction, dict):
        worker_instruction = {}
    stable_instruction = {
        key: worker_instruction.get(key)
        for key in ("role", "success_rule", "baseline_generation_rule")
        if worker_instruction.get(key) is not None
    }
    return {
        "task": context.get("task") or {},
        "problem_family_capability": context.get("problem_family_capability") or {},
        "evaluator_protocol": context.get("evaluator_protocol") or {},
        "edit_policy": context.get("edit_policy") or {},
        "worker_instruction": stable_instruction,
        "contract_review_evidence": context.get("contract_review_evidence") or {},
        "project_intake": context.get("project_intake") or {},
        "instance_diagnostics": context.get("instance_diagnostics") or {},
        "documents": context.get("documents") or [],
        "knowledge_selection": context.get("knowledge_selection") or {},
        "auto_knowledge_cards": context.get("auto_knowledge_cards") or [],
        "method_package_catalog": context.get("method_package_catalog") or {},
        "active_method_package": context.get("active_method_package") or {},
        "previous_pipeline_memory": context.get("previous_pipeline_memory") or {},
    }


def stable_worker_context_json(context: dict[str, Any]) -> CompactedJson:
    return compact_json(stable_worker_context(context), max_chars=STABLE_WORKER_CONTEXT_MAX_CHARS)


def compact_source_records(
    records: Any,
    *,
    max_items: int,
    max_snippet_chars: int,
) -> list[dict[str, Any]]:
    """压缩文档/知识卡记录。

    轮次刷新时不需要重复携带整份大文档，只保留路径、哈希、长度和压缩后的
    snippet，既能追溯原始来源，又能控制 Context Packet 的动态区大小。
    """

    if not isinstance(records, list):
        return []
    compacted: list[dict[str, Any]] = []
    for item in records[:max_items]:
        if not isinstance(item, dict):
            continue
        snippet = str(item.get("snippet") or "")
        compacted.append(
            {
                key: item.get(key)
                for key in ("path", "exists", "sha256", "chars", "truncated", "error")
                if key in item
            }
        )
        compacted[-1]["snippet"] = _compact_string(snippet, max_snippet_chars)
        compacted[-1]["snippet_compacted_for_round"] = len(snippet) > max_snippet_chars
    return compacted


def _compact_value(
    value: Any,
    *,
    path: tuple[str, ...],
    max_string: int,
    max_list: int,
    max_dict: int,
    max_depth: int,
) -> Any:
    if len(path) >= max_depth:
        evidence = _critical_evidence_depth_summary(value, path=path, max_string=max_string)
        if evidence is not None:
            return evidence
        return _depth_summary(value)
    if isinstance(value, str):
        return _compact_string(value, max_string)
    if isinstance(value, list):
        items = value[-max_list:] if path and path[-1] in _TAIL_HISTORY_KEYS else value[:max_list]
        return [
            _compact_value(
                item,
                path=path + (str(index),),
                max_string=max_string,
                max_list=max_list,
                max_dict=max_dict,
                max_depth=max_depth,
            )
            for index, item in enumerate(items)
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_dict:
                result["_omitted_key_count"] = len(value) - max_dict
                break
            text_key = str(key)
            result[text_key] = _compact_value(
                item,
                path=path + (text_key,),
                max_string=max_string,
                max_list=max_list,
                max_dict=max_dict,
                max_depth=max_depth,
            )
        return result
    return value


def _critical_evidence_depth_summary(
    value: Any,
    *,
    path: tuple[str, ...],
    max_string: int,
) -> Any | None:
    """Preserve causal gate fields when generic depth compaction is unavoidable."""

    if isinstance(value, list) and path and path[-1] in {
        "changed_core_algorithm_files",
        "changed_validator_files",
        "target_files",
        "evidence_used",
    }:
        return [
            _compact_string(item, min(max_string, 300)) if isinstance(item, str) else item
            for item in value[:12]
            if isinstance(item, (str, int, float, bool, type(None)))
        ]
    if not isinstance(value, dict):
        return None
    if "rule_operator_hypotheses" in path:
        return {
            key: (
                _compact_string(item, min(max_string, 240))
                if isinstance(item, str)
                else item
            )
            for key, item in value.items()
            if key in {"name", "type", "novelty", "expected_effect", "ablation_plan"}
            and isinstance(item, (str, int, float, bool, type(None)))
        }
    if "activation_checks" in path or "mechanism_activation" in path:
        return {
            key: (
                _compact_string(item, min(max_string, 240))
                if isinstance(item, str)
                else item
            )
            for key, item in value.items()
            if key
            in {
                "id",
                "path",
                "operator",
                "expected",
                "required",
                "found",
                "observed",
                "passed",
                "status",
                "required_failure_count",
            }
            and isinstance(item, (str, int, float, bool, type(None)))
        }
    if "operator_lineage" in path:
        return {
            str(key): item
            for key, item in value.items()
            if isinstance(item, (str, int, float, bool, type(None)))
        }
    return None


def _compact_string(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = f"\n... <{len(value) - limit} chars compacted> ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining * 2 // 3
    tail = remaining - head
    return value[:head] + marker + (value[-tail:] if tail else "")


def _depth_summary(value: Any) -> Any:
    if isinstance(value, dict):
        return {"_compacted": "dict", "key_count": len(value), "keys": [str(key) for key in list(value)[:12]]}
    if isinstance(value, list):
        return {"_compacted": "list", "item_count": len(value)}
    if isinstance(value, str):
        return _compact_string(value, 180)
    return value


def _root_fallback(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"_compacted": True, "value_type": type(value).__name__}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {
            "round_index",
            "round_semantics",
            "current_direction",
            "baseline_key",
            "incumbent_key_before",
            "protected_promoted_facts",
            "failure_memory",
            "next_round_guidance",
            "instructions",
            "current_round_repair",
        }:
            result[key] = _compact_value(
                item,
                path=(str(key),),
                max_string=180,
                max_list=3,
                max_dict=20,
                max_depth=4,
            )
    result["_compacted"] = {
        "mode": "root_fallback",
        "source_key_count": len(value),
    }
    return result
