"""Context Packet：把稳定任务事实与逐轮动态反馈组织成有界上下文。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_agent.agents.incumbent_audit import build_incumbent_capability_audit

from harness_agent.context.compaction import (
    ROUND_CONTEXT_MAX_CHARS,
    ROUND_FEEDBACK_MAX_CHARS,
    compact_json,
    compact_source_records,
)
from harness_agent.context.loader import load_context_packet
from harness_agent.domains.context import get_domain_context_provider
from harness_agent.context.knowledge import (
    knowledge_query_catalog,
    method_family_catalog,
    method_package_catalog,
    resolve_worker_implementation_skills,
    resolve_method_package,
    select_knowledge_cards,
    select_tagged_knowledge_cards,
    selection_cards,
)
from harness_agent.core.models import TaskContract
from harness_agent.domains.families import get_problem_family
from harness_agent.slots.contract import ResolvedCodeSlot
from harness_agent.slots.manifest import load_slot_manifest


SECTION_ROLE_PRIORITY = {
    "objectives": 0,
    "constraints": 1,
    "input_output": 2,
    "acceptance": 3,
    "algorithm_guidance": 4,
    "instance_data": 5,
    "general": 9,
}


@dataclass(frozen=True)
class ContextPacketRequest:
    """构建 Context Packet 所需的所有外部输入。

    这里汇总的是稳定任务材料：确认后的 Task Contract、补充文档、知识卡、项目
    intake、slot manifest、上一轮报告/经验等。真正的求解结果与回滚信息则在
    `write_refreshed_context_packet()` 里以动态区方式追加。
    """

    contract_path: Path
    output_path: Path
    docs: list[Path] = field(default_factory=list)
    knowledge_cards: list[Path] = field(default_factory=list)
    project_root: Path | None = None
    hypothesis: str = ""
    previous_report: Path | None = None
    previous_pipeline_memory: Path | None = None
    project_intake_manifest: Path | None = None
    slot_manifest: Path | None = None
    max_chars_per_source: int = 12000


def build_context_packet(request: ContextPacketRequest) -> dict[str, Any]:
    """构建首轮完整 Context Packet。

    这个函数是多路上下文的汇合点：Task Contract 文档抽取证据、问题族能力、
    实例特征诊断、知识卡选择、Method Package 候选、项目地图和 slot 编辑边界
    都会在这里合并成同一份首轮上下文。
    """

    contract = TaskContract.load(request.contract_path)
    contract_raw = json.loads(request.contract_path.read_text(encoding="utf-8-sig"))
    domain_context_provider = get_domain_context_provider(contract.problem_family)
    docs = [_source_payload(path, request.max_chars_per_source) for path in request.docs]
    previous_report = (
        _source_payload(request.previous_report, request.max_chars_per_source)
        if request.previous_report
        else None
    )
    previous_pipeline_memory = (
        _pipeline_memory_payload(request.previous_pipeline_memory)
        if request.previous_pipeline_memory
        else None
    )
    instance_diagnostics = domain_context_provider.inspect_instances(
        contract,
        project_root=request.project_root,
    )
    project_intake = (
        _project_intake_payload(request.project_intake_manifest, request.max_chars_per_source)
        if request.project_intake_manifest
        else None
    )
    slot_manifest = (
        _slot_manifest_payload(request.slot_manifest, project_root=request.project_root)
        if request.slot_manifest
        else None
    )
    contract_review_evidence = _contract_review_payload(contract.review)
    problem_family_capability = get_problem_family(contract.problem_family).to_payload()
    agent_generated_solver = _uses_agent_generated_solver(contract)
    problem_family_tags = (
        [] if agent_generated_solver else list(problem_family_capability.get("knowledge_tags") or [])
    )
    if agent_generated_solver:
        problem_family_tags.append("agent_generated_solver")
    active_features = domain_context_provider.active_features(
        contract=contract,
        instance_diagnostics=instance_diagnostics,
        contract_review_evidence=contract_review_evidence,
    )
    # 知识卡选择和 Method Package 推荐都建立在“领域能力 + 当前实例特征”之上。
    # Domain Pack 提供边界与素材，实例诊断/slot 确认决定当前 round 实际可用什么。
    knowledge_selection = select_knowledge_cards(
        problem_family=contract.problem_family,
        problem_family_tags=problem_family_tags,
        slot_manifest=slot_manifest,
        instance_diagnostics=instance_diagnostics,
        active_features=active_features,
    )
    auto_cards = knowledge_selection.cards
    strategy_card_paths = selection_cards(
        problem_family=contract.problem_family,
        stage="strategy",
    )
    package_catalog = (
        method_package_catalog(
            problem_family=contract.problem_family,
            active_features=active_features,
            knowledge_query_tags=["__direction_selection_pending__"],
        )
        if agent_generated_solver
        else {
            "status": "not_applicable",
            "problem_family": contract.problem_family,
            "active_features": active_features,
            "packages": [],
            "recommended_package_id": None,
        }
    )
    query_catalog = knowledge_query_catalog(problem_family=contract.problem_family)
    family_catalog = method_family_catalog(
        problem_family=contract.problem_family,
        active_features=active_features,
    )
    knowledge_card_paths = _unique_paths([*request.knowledge_cards, *auto_cards])
    knowledge_cards = [_source_payload(path, request.max_chars_per_source) for path in knowledge_card_paths]
    strategy_selection_cards = [
        _source_payload(path, min(request.max_chars_per_source, 12_000))
        for path in strategy_card_paths
    ]
    # `required_order` 是 worker 的最小阅读顺序控制，用于把“先看契约/实例/slot，
    # 再改代码”这种流程固化在上下文里，而不是依赖模型自行猜顺序。
    required_order = [
        "Read this context packet.",
        "State a natural-language strategy before editing code.",
        "Modify only allowed files.",
        "Run the quick test before benchmark self-evaluation.",
        "Return structured changed files, test results, benchmark summary, and failure analysis.",
    ]
    if contract_review_evidence.get("has_document_schema"):
        required_order.insert(1, "Review contract_review_evidence before interpreting document snippets.")
    if contract_review_evidence.get("role_prioritized_sections"):
        required_order.insert(2, "Start document grounding from contract_review_evidence.role_prioritized_sections.")
    if project_intake:
        required_order.insert(1, "Review project_intake before proposing code changes.")
    if slot_manifest:
        required_order.insert(1, "Review slot_manifest and edit only user-confirmed selected slots.")
    if instance_diagnostics.get("status") in {"available", "partial"}:
        required_order.insert(
            1,
            "Review instance_diagnostics before choosing a slot strategy; best-known/LB/UB values are diagnostics only.",
        )
    if previous_pipeline_memory:
        required_order.insert(1, "Review previous_pipeline_memory before proposing the next loop change.")
        if previous_pipeline_memory.get("operator_guidance"):
            required_order.insert(
                2,
                "Apply previous_pipeline_memory.operator_guidance when choosing rule/operator hypotheses.",
            )
        if previous_pipeline_memory.get("direction_graph_signal"):
            required_order.insert(
                2,
                "Use previous_pipeline_memory.direction_graph_signal to preserve, mutate, or prune prior improvement directions.",
            )
        if previous_pipeline_memory.get("experience_memory_signal"):
            required_order.insert(
                2,
                "Use validated lessons from previous_pipeline_memory.experience_memory_signal as reusable method evidence; keep candidate lessons provisional.",
            )
    packet = {
        "packet_type": "algoforge_context_packet",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract_path": str(request.contract_path),
        "contract_hash": _hash_text(json.dumps(contract_raw, ensure_ascii=False, sort_keys=True)),
        "task": {
            "task_id": contract.task_id,
            "problem_family": contract.problem_family,
            "description": contract.description,
            "review_status": contract.review_status,
            "requires_human_confirmation": contract.requires_human_confirmation,
            "objectives": [
                {
                    "name": objective.name,
                    "direction": objective.direction,
                    "priority": objective.priority,
                    "invalid_if_missing": objective.invalid_if_missing,
                    "threshold": objective.threshold,
                }
                for objective in contract.objectives
            ],
            "instances": [{"id": instance.id, "path": str(instance.path)} for instance in contract.instances],
            "budget": {
                "rounds": contract.budget.rounds,
                "seeds": contract.budget.seeds,
                "timeout_seconds": contract.budget.timeout_seconds,
                "max_workers": contract.budget.max_workers,
            },
        },
        "problem_family_capability": problem_family_capability,
        "evaluator_protocol": {
            "solver_command_template": contract.commands.solver,
            "evaluator_command_template": contract.commands.evaluator,
            "quick_test_command": contract.commands.quick_test,
            "solution_contract": domain_context_provider.solution_contract(),
            "solution_format": domain_context_provider.solution_contract().get("format"),
            "resources": {key: str(value) for key, value in contract.resources.items()},
            "formal_verdict_owner": "AlgoForge Core",
            "worker_self_evaluation_policy": (
                "Worker may compile changed code and run one fixed-seed short smoke only; formal evaluator, "
                "multi-seed, repeated, and benchmark runs belong exclusively to Core."
            ),
            "worker_execution_budget": {
                "compile_runs": 1,
                "smoke_runs": 1,
                "smoke_seed": contract.budget.seeds[0] if contract.budget.seeds else 0,
                "smoke_timeout_seconds": min(10, max(5, contract.budget.timeout_seconds)),
                "forbidden": [
                    "multi-seed evaluation",
                    "formal benchmark command",
                    "full test suite",
                    "repeated evaluator runs",
                ],
            },
        },
        "edit_policy": {
            "allowed_paths": contract.paths.allowed_paths,
            "forbidden_paths": contract.paths.forbidden_paths,
            "must_not_modify": [".git", "outputs", "confirmed evaluator semantics unless explicitly requested"],
        },
        "worker_instruction": {
            "role": "Coding Agent / CodingWorker",
            "required_order": required_order,
            "success_rule": "Do not claim success unless AlgoForge Core reruns evaluator/validator and accepts the result.",
        },
        "hypothesis": request.hypothesis,
        "contract_review_evidence": contract_review_evidence,
        "project_intake": project_intake,
        "slot_manifest": slot_manifest,
        "instance_diagnostics": instance_diagnostics,
        "documents": docs,
        "knowledge_cards": knowledge_cards,
        "strategy_selection_cards": strategy_selection_cards,
        "auto_knowledge_cards": [str(path) for path in auto_cards],
        "knowledge_selection": knowledge_selection.audit,
        "knowledge_query_catalog": query_catalog,
        "method_family_catalog": family_catalog,
        "method_package_catalog": package_catalog,
        "previous_report": previous_report,
        "previous_pipeline_memory": previous_pipeline_memory,
    }
    packet["packet_hash"] = _hash_text(json.dumps(packet, ensure_ascii=False, sort_keys=True))
    return packet


def write_context_packet(request: ContextPacketRequest) -> Path:
    """生成首轮完整上下文；需求、IO、domain pack 和知识选择在这里汇合。"""

    payload = build_context_packet(request)
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    request.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request.output_path


def write_refreshed_context_packet(
    *,
    base_context_packet_path: Path,
    output_path: Path,
    loop_feedback: dict[str, Any],
    project_root: Path | None = None,
) -> Path:
    """追加 Core 反馈并生成逐轮上下文。

    首轮包保留稳定任务信息；动态 promotion/rollback、修补和失败证据追加在
    后部并按结构压缩，从而控制长度并尽量复用模型缓存前缀。
    """

    loaded_packet = load_context_packet(base_context_packet_path)
    packet = loaded_packet.effective_context
    parent_hash = loaded_packet.raw.get("packet_hash") or loaded_packet.integrity["actual_packet_hash"]

    refreshed = dict(packet)
    refreshed.pop("packet_hash", None)
    refreshed["created_at"] = datetime.now(timezone.utc).isoformat()
    refreshed["parent_packet_hash"] = parent_hash
    refreshed["refresh_reason"] = "worker_loop_round_feedback"
    refreshed["base_context_ref"] = {
        "path": str(base_context_packet_path.resolve()),
        "packet_hash": parent_hash,
        "immutable": True,
    }
    refreshed["documents"] = compact_source_records(
        refreshed.get("documents"),
        max_items=8,
        max_snippet_chars=6000,
    )
    refreshed["knowledge_cards"] = compact_source_records(
        refreshed.get("knowledge_cards"),
        max_items=40,
        max_snippet_chars=2400,
    )
    refreshed["iteration_edit_contract"] = {
        "mode": "incremental_after_baseline",
        "preserve_incumbent_rule": (
            "Start from the incumbent worktree and preserve the best promoted solver structure unless the proposal "
            "names a measured weakness and makes a smaller, evaluator-checkable mutation."
        ),
        "whole_file_rewrite_policy": (
            "Do not use create_or_replace on an existing solver file during improvement rounds. Use text_replace, "
            "insert_after, or a confirmed replace_slot_block for small changes; create_or_replace is reserved for "
            "new helper files or baseline-generation entrypoints."
        ),
        "required_pre_full_eval_gate": (
            "Core runs a one-seed evaluator smoke before the full benchmark; proposals should be small enough for "
            "that smoke to diagnose quickly."
        ),
    }
    incumbent_code_context = _incumbent_code_context(refreshed, project_root=project_root)
    if incumbent_code_context:
        refreshed["incumbent_code_context"] = incumbent_code_context
        incumbent_capability_audit = build_incumbent_capability_audit(
            incumbent_code_context,
            project_root=project_root,
        )
        if incumbent_capability_audit:
            refreshed["incumbent_capability_audit"] = incumbent_capability_audit
    compacted_feedback = compact_json(loop_feedback, max_chars=ROUND_FEEDBACK_MAX_CHARS)
    refreshed["loop_feedback"] = compacted_feedback.payload
    activate_method_package_context(
        refreshed,
        direction_plan=(
            loop_feedback.get("current_direction_plan")
            if isinstance(loop_feedback.get("current_direction_plan"), dict)
            else None
        ),
    )
    activate_direction_knowledge_context(
        refreshed,
        direction_plan=(
            loop_feedback.get("current_direction_plan")
            if isinstance(loop_feedback.get("current_direction_plan"), dict)
            else None
        ),
    )
    refreshed["hypothesis"] = _improvement_round_hypothesis(str(refreshed.get("hypothesis") or ""))
    if project_root is not None:
        refreshed["slot_manifest"] = _refresh_slot_manifest_sources(
            refreshed.get("slot_manifest"),
            project_root=project_root,
        )

    worker_instruction = dict(refreshed.get("worker_instruction") or {})
    required_order = list(worker_instruction.get("required_order") or [])
    feedback_step = "Review loop_feedback and avoid repeating rolled-back changes unless the new proposal is materially different."
    if feedback_step not in required_order:
        required_order.insert(1, feedback_step)
    incumbent_step = "Preserve the current promoted incumbent; make a small incremental edit rather than rewriting the solver."
    if incumbent_step not in required_order:
        required_order.insert(2, incumbent_step)
    worker_instruction["required_order"] = required_order
    worker_instruction["round_feedback_rule"] = (
        "Treat loop_feedback as Core evaluator evidence.  Promoted rounds show "
        "directions worth preserving; rolled-back rounds show directions to avoid "
        "or modify.  Do not use worker self-claims as success evidence."
    )
    worker_instruction["incremental_edit_rule"] = (
        "After an incumbent exists, keep the promoted solver skeleton and mutate one bounded rule/operator at a time. "
        "A full-file rewrite of an existing solver is not an acceptable improvement-round edit."
    )
    refreshed["worker_instruction"] = worker_instruction
    refreshed["context_compaction"] = {
        "mode": "bounded_round_context",
        "max_context_chars": ROUND_CONTEXT_MAX_CHARS,
        "max_feedback_chars": ROUND_FEEDBACK_MAX_CHARS,
        "feedback_original_chars": compacted_feedback.original_chars,
        "feedback_stored_chars": len(compacted_feedback.text),
        "feedback_compacted": compacted_feedback.compacted,
        "feedback_profile": compacted_feedback.profile,
        "history_policy": "keep_recent_items_and_artifact_references",
    }

    bounded_packet = compact_json(refreshed, max_chars=ROUND_CONTEXT_MAX_CHARS - 256)
    refreshed = bounded_packet.payload
    compaction = refreshed.get("context_compaction")
    if isinstance(compaction, dict):
        compaction["packet_original_chars_before_final_bound"] = bounded_packet.original_chars
        compaction["packet_profile"] = bounded_packet.profile
        compaction["packet_compacted"] = bounded_packet.compacted
    refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_text = json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n"
    if len(output_text) > ROUND_CONTEXT_MAX_CHARS:
        final_packet = compact_json(refreshed, max_chars=ROUND_CONTEXT_MAX_CHARS - 256)
        refreshed = final_packet.payload
        refreshed["packet_hash"] = _hash_text(json.dumps(refreshed, ensure_ascii=False, sort_keys=True))
        output_text = json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n"
    output_path.write_text(output_text, encoding="utf-8")
    return output_path


def activate_method_package_context(
    context: dict[str, Any],
    *,
    direction_plan: dict[str, Any] | None,
    max_chars_per_asset: int = 16000,
) -> dict[str, Any] | None:
    """把一个选中的 Method Package 注入 worker 上下文。

    一轮最多激活一个完整 Method Package，避免两套完整参考实现互相覆盖。
    这不限制 Main 选择多个兼容方法族；互补实现知识由独立的 Worker Skills
    精确匹配并组合。
    """

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    if catalog.get("status") != "ok":
        context.pop("active_method_package", None)
        return None
    active_features = [str(item) for item in catalog.get("active_features") or []]
    requested_id = str((direction_plan or {}).get("method_package_id") or "").strip()
    if not requested_id:
        context.pop("active_method_package", None)
        return None
    package = resolve_method_package(
        problem_family=str(task.get("problem_family") or ""),
        package_id=requested_id,
        active_features=active_features,
        knowledge_query_tags=[
            str(item)
            for item in (direction_plan or {}).get("knowledge_query") or []
            if str(item).strip()
        ],
    )
    if not package:
        context.pop("active_method_package", None)
        return None

    contract_assets = [
        str(value)
        for value in package.get("implementation_contract_assets")
        or [package.get("implementation_contract_asset")]
        if str(value or "").strip()
    ]
    asset_paths = [
        Path(str(value))
        for value in [*contract_assets, *(package.get("assets") or [])]
        if str(value).strip()
    ]
    existing_cards = [item for item in context.get("knowledge_cards") or [] if isinstance(item, dict)]
    by_path = {str(item.get("path") or ""): item for item in existing_cards if str(item.get("path") or "")}
    package_cards: list[dict[str, Any]] = []
    for asset_path in asset_paths:
        key = str(asset_path)
        card = by_path.get(key) or _source_payload(asset_path, max_chars_per_asset)
        by_path[key] = card
        package_cards.append(card)
    context["knowledge_cards"] = list(by_path.values())
    context["auto_knowledge_cards"] = _unique_strings(
        [
            *(str(item) for item in context.get("auto_knowledge_cards") or []),
            *(str(path) for path in asset_paths),
        ]
    )
    context["active_method_package"] = {
        **package,
        "requested_package_id": requested_id or None,
        "selection": "requested" if requested_id == package.get("package_id") else "recommended_fallback",
        "asset_records": package_cards,
        "worker_rule": (
            "Adapt this one package to the active IO and solver contract. Implement every required component and "
            "coupled group in its implementation_contract before claiming the method is complete. It may be "
            "combined only with method families explicitly selected by Main and matched to Worker Skills. "
            "Same-direction repairs must close the latest missing/partial component matrix rather than switching "
            "methods or patching one symptom in isolation."
        ),
    }
    return context["active_method_package"]


def activate_direction_knowledge_context(
    context: dict[str, Any],
    *,
    direction_plan: dict[str, Any] | None,
    max_chars_per_asset: int = 10_000,
) -> dict[str, Any] | None:
    """Run second-stage retrieval after Main has selected a method direction."""

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    plan = direction_plan if isinstance(direction_plan, dict) else {}
    query = [str(item).strip().lower() for item in plan.get("knowledge_query") or [] if str(item).strip()]
    method_families = plan.get("method_families") or [plan.get("method_family")]
    skill_selection = resolve_worker_implementation_skills(
        problem_family=str(task.get("problem_family") or ""),
        method_families=method_families,
        active_features=[str(item) for item in catalog.get("active_features") or []],
        knowledge_query_tags=query,
    )
    context["active_worker_implementation_skills"] = skill_selection
    if not query:
        context.pop("active_direction_knowledge", None)
        return None
    selection = select_tagged_knowledge_cards(
        problem_family=str(task.get("problem_family") or ""),
        knowledge_query_tags=query,
        instance_diagnostics=(
            context.get("instance_diagnostics")
            if isinstance(context.get("instance_diagnostics"), dict)
            else None
        ),
        active_features=[str(item) for item in catalog.get("active_features") or []],
    )
    paths = [str(path) for path in selection.cards]
    existing_cards = [item for item in context.get("knowledge_cards") or [] if isinstance(item, dict)]
    by_path = {str(item.get("path") or ""): item for item in existing_cards if str(item.get("path") or "")}
    records: list[dict[str, Any]] = []
    for path in selection.cards:
        key = str(path)
        record = by_path.get(key) or _source_payload(path, max_chars_per_asset)
        by_path[key] = record
        records.append(record)
    context["knowledge_cards"] = list(by_path.values())
    context["active_direction_knowledge"] = {
        "method_family": str(plan.get("method_family") or ""),
        "method_families": skill_selection.get("method_families") or [],
        "query": query,
        "paths": paths,
        "asset_records": records,
        "audit": selection.audit,
        "worker_rule": (
            "Read only these second-stage cards and the matched Worker Implementation Skills. Combine only the "
            "method families selected by Main, and convert them into explicit behavioral deliverables."
        ),
    }
    return context["active_direction_knowledge"]


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _improvement_round_hypothesis(base_hypothesis: str) -> str:
    baseline_generation_pattern = (
        r"If baseline_source is agent_generated,\s*first create a runnable solver entrypoint at .*?"
        r"rather than relying on an incumbent solver\.\s*"
    )
    cleaned = re.sub(baseline_generation_pattern, "", base_hypothesis, flags=re.IGNORECASE | re.DOTALL).strip()
    prefix = (
        "This is an improvement round, not baseline generation. A measured incumbent solver already exists. "
        "Use incumbent_code_context and loop_feedback to make one small patch to the promoted solver; do not "
        "create the initial solver again and do not replace the whole existing solver file."
    )
    if not cleaned:
        return prefix
    return f"{prefix}\n\nOriginal task context, with baseline-generation instructions superseded:\n{cleaned}"


def _incumbent_code_context(packet: dict[str, Any], *, project_root: Path | None, max_chars: int = 16000) -> dict[str, Any] | None:
    if project_root is None:
        return None
    evaluator_protocol = packet.get("evaluator_protocol")
    if not isinstance(evaluator_protocol, dict):
        return None
    solver_template = str(evaluator_protocol.get("solver_command_template") or "")
    relative_paths = _python_paths_from_command(solver_template)
    files: list[dict[str, Any]] = []
    for relative in relative_paths:
        source = (project_root / relative).resolve()
        try:
            source.relative_to(project_root.resolve())
        except ValueError:
            continue
        if not source.is_file():
            continue
        payload = _source_payload(source, max_chars)
        payload["relative_path"] = relative.as_posix()
        files.append(payload)
    if not files:
        return None
    return {
        "source": "promoted_incumbent_worktree",
        "root": str(project_root),
        "purpose": (
            "Current solver source available for incremental text_replace or insert_after proposals. "
            "Preserve this structure unless loop_feedback identifies a measured weakness."
        ),
        "files": files,
    }


def _python_paths_from_command(command: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?P<quote>['\"])?(?P<path>[A-Za-z0-9_./\\-]+\.py)(?P=quote)?", command):
        raw_path = match.group("path").replace("\\", "/").strip()
        if not raw_path or "{" in raw_path or "}" in raw_path:
            continue
        path = Path(raw_path)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            continue
        normalized = path.as_posix()
        if normalized not in seen:
            seen.add(normalized)
            paths.append(Path(normalized))
    return paths


def _source_payload(path: Path, max_chars: int) -> dict[str, Any]:
    """把文档/知识卡文件变成可嵌入 packet 的源记录。

    统一保留路径、存在性、文本摘要和 sha256，方便后续压缩、去重和人工审计。
    """

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        exists = True
        error = None
    except OSError as exc:
        text = ""
        exists = False
        error = str(exc)
    truncated = len(text) > max_chars
    snippet = text[:max_chars]
    return {
        "path": str(path),
        "exists": exists,
        "sha256": _hash_text(text) if exists else None,
        "chars": len(text),
        "truncated": truncated,
        "snippet": snippet,
        "error": error,
    }


def _uses_agent_generated_solver(contract: TaskContract) -> bool:
    solver = contract.commands.solver.replace("\\", "/").lower()
    return "agent_generated" in solver or "generated_fjsp" in solver


def _project_intake_payload(path: Path, max_chars: int) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        manifest = json.loads(text)
        exists = True
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        text = ""
        manifest = {}
        exists = False
        error = str(exc)

    artifacts = manifest.get("artifacts") or {}
    report_path = Path(str(artifacts["report"])) if artifacts.get("report") else None
    report = _source_payload(report_path, max_chars) if report_path else None
    return {
        "path": str(path),
        "exists": exists,
        "sha256": _hash_text(text) if exists else None,
        "status": manifest.get("status"),
        "error": error,
        "summary": _compact_project_intake(manifest),
        "report": report,
    }


def _slot_manifest_payload(path: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    """把 slot manifest 转成上下文记录。

    slot 插件是可选能力，不是默认执行路径；因此这里既携带 manifest 元数据，也尽量
    补齐已确认 slot 的块位置信息，供需要 slot-based edit 的 worker 使用。
    """

    try:
        manifest = load_slot_manifest(path)
        exists = True
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}
        exists = False
        error = str(exc)
    slots = manifest.get("slots") if isinstance(manifest, dict) else []
    if not isinstance(slots, list):
        slots = []
    manifest_root = path.resolve().parent
    repo_root = Path(__file__).resolve().parents[2]
    resolved_project_root = project_root.resolve() if project_root else None
    selected_slots = []
    for item in slots:
        if not isinstance(item, dict):
            continue
        target_file = str(item.get("target_file", ""))
        source_text = None
        if target_file:
            target_path = Path(target_file)
            candidates = []
            if target_path.is_absolute():
                candidates.append(target_path)
            else:
                if resolved_project_root is not None:
                    candidates.append(resolved_project_root / target_path)
                candidates.extend([repo_root / target_path, manifest_root / target_path])
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        source_text = candidate.read_text(encoding="utf-8")
                        break
                except OSError:
                    continue
        resolved = ResolvedCodeSlot.from_manifest_slot(item, source_text=source_text)
        selected_slots.append(
            {
            "slot_id": str(item.get("slot_id", "")),
            "title": str(item.get("title", "")),
            "problem_family": str(manifest.get("problem_family") or ""),
            "target_file": target_file,
            "marker_start": str(item.get("marker_start", "")),
            "marker_end": str(item.get("marker_end", "")),
            "slot_kind": str(item.get("slot_kind", "")),
            "language": str(item.get("language", "")),
            "line_start": resolved.line_start,
            "line_end": resolved.line_end,
            "block_name": resolved.block_name,
            "context_before": resolved.context_before,
            "context_after": resolved.context_after,
            "original_content": resolved.original_content,
            "purpose": str(item.get("purpose", "")),
            "inputs": item.get("inputs", []),
            "outputs": item.get("outputs", []),
            "invariants": item.get("invariants", []),
            "allowed_edits": item.get("allowed_edits", []),
            "forbidden_edits": item.get("forbidden_edits", []),
            "validation_commands": item.get("validation_commands", []),
            "knowledge_tags": item.get("knowledge_tags", []),
            "user_confirmed": bool(item.get("user_confirmed", False)),
            }
        )
    return {
        "path": str(path),
        "exists": exists,
        "status": manifest.get("status") if isinstance(manifest, dict) else None,
        "problem_family": manifest.get("problem_family") if isinstance(manifest, dict) else None,
        "confirmation_required": bool(manifest.get("confirmation_required", True)) if isinstance(manifest, dict) else True,
        "slots": selected_slots,
        "error": error,
    }


def _refresh_slot_manifest_sources(slot_manifest: Any, *, project_root: Path) -> Any:
    if not isinstance(slot_manifest, dict):
        return slot_manifest
    slots = slot_manifest.get("slots")
    if not isinstance(slots, list):
        return slot_manifest

    refreshed = dict(slot_manifest)
    refreshed_slots: list[dict[str, Any]] = []
    resolved_project_root = project_root.resolve()
    for item in slots:
        if not isinstance(item, dict):
            continue
        refreshed_slot = dict(item)
        target_file = str(refreshed_slot.get("target_file", ""))
        source_text = None
        if target_file:
            target_path = Path(target_file)
            candidate = target_path if target_path.is_absolute() else resolved_project_root / target_path
            try:
                if candidate.is_file():
                    source_text = candidate.read_text(encoding="utf-8")
            except OSError:
                source_text = None
        resolved = ResolvedCodeSlot.from_manifest_slot(refreshed_slot, source_text=source_text)
        refreshed_slot.update(
            {
                "line_start": resolved.line_start,
                "line_end": resolved.line_end,
                "block_name": resolved.block_name,
                "context_before": resolved.context_before,
                "context_after": resolved.context_after,
                "original_content": resolved.original_content,
            }
        )
        refreshed_slots.append(refreshed_slot)
    refreshed["slots"] = refreshed_slots
    return refreshed


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _pipeline_memory_payload(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        memory = json.loads(text)
        exists = True
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        text = ""
        memory = {}
        exists = False
        error = str(exc)

    raw_experience_signal = _raw_experience_memory_signal(memory)
    experience_signal = memory.get("experience_memory_signal") or raw_experience_signal

    return {
        "path": str(path),
        "exists": exists,
        "sha256": _hash_text(text) if exists else None,
        "schema_version": memory.get("schema_version"),
        "pipeline_status": memory.get("pipeline_status"),
        "stage_status": memory.get("stage_status") or {},
        "admission": memory.get("admission") or {},
        "benchmark_signal": memory.get("benchmark_signal") or {},
        "worker_signal": _compact_worker_signal(memory.get("worker_signal") or {}),
        "operator_lineage_signal": _compact_operator_lineage_signal(memory.get("operator_lineage_signal") or {}),
        "direction_graph_signal": _compact_direction_graph_signal(memory.get("direction_graph_signal") or {}),
        "experience_memory_signal": _compact_experience_memory_signal(experience_signal),
        "skill_usage_signal": memory.get("skill_usage_signal") or {},
        "operator_guidance": _operator_guidance_from_memory(memory),
        "evidence_signal": memory.get("evidence_signal") or {},
        "recommendations": (memory.get("recommendations") or [])[:20],
        "artifacts": memory.get("artifacts") or {},
        "error": error,
    }


def _compact_direction_graph_signal(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal:
        return {}
    return {
        "schema_version": signal.get("schema_version"),
        "round_semantics": signal.get("round_semantics"),
        "direction_count": signal.get("direction_count", 0),
        "attempt_count": signal.get("attempt_count", 0),
        "status_counts": signal.get("status_counts") or {},
        "decision_counts": signal.get("decision_counts") or {},
        "promoted_direction_ids": (signal.get("promoted_direction_ids") or [])[:8],
        "recent_directions": _compact_direction_records(signal.get("recent_directions") or [], limit=8),
        "guidance": (signal.get("guidance") or [])[:8],
    }


def _compact_experience_memory_signal(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal:
        return {}
    return {
        "schema_version": signal.get("schema_version"),
        "write_policy": signal.get("write_policy") or {},
        "candidate_lesson_count": signal.get("candidate_lesson_count", 0),
        "candidate_lessons": _compact_lesson_records(signal.get("candidate_lessons") or [], limit=10),
        "candidate_lessons_withheld": bool(signal.get("candidate_lessons_withheld")),
        "validated_lesson_count": signal.get("validated_lesson_count", 0),
        "validated_lessons": _compact_lesson_records(signal.get("validated_lessons") or [], limit=10),
        "self_evolution_metrics": signal.get("self_evolution_metrics") or {},
        "algorithm_semantic_memory": signal.get("algorithm_semantic_memory") or {},
        "next_context_guidance": (signal.get("next_context_guidance") or [])[:8],
    }


def _raw_experience_memory_signal(memory: dict[str, Any]) -> dict[str, Any]:
    tiers = memory.get("memory_tiers") if isinstance(memory.get("memory_tiers"), dict) else {}
    if not tiers:
        return {}
    candidate = [item for item in tiers.get("candidate_lessons") or [] if isinstance(item, dict)]
    validated = [item for item in tiers.get("validated_lessons") or [] if isinstance(item, dict)]
    return {
        "schema_version": memory.get("schema_version"),
        "write_policy": memory.get("write_policy") or {},
        "candidate_lesson_count": len(candidate),
        "candidate_lessons": [],
        "candidate_lessons_withheld": bool(candidate),
        "validated_lesson_count": len(validated),
        "validated_lessons": validated,
        "self_evolution_metrics": memory.get("self_evolution_metrics") or {},
        "algorithm_semantic_memory": memory.get("algorithm_semantic_memory") or {},
        "next_context_guidance": memory.get("next_context_guidance") or [],
    }


def _compact_direction_records(records: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "direction_id": item.get("direction_id"),
                "round_index": item.get("round_index"),
                "title": str(item.get("title") or "")[:160],
                "status": item.get("status"),
                "decision": item.get("decision"),
                "strategy_type": item.get("strategy_type"),
                "method_package_id": item.get("method_package_id"),
                "attempt_count": item.get("attempt_count"),
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _compact_lesson_records(records: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "lesson_id": item.get("lesson_id"),
                "lesson_type": item.get("lesson_type"),
                "strategy": str(item.get("strategy") or "")[:160],
                "strategy_type": item.get("strategy_type"),
                "method_package_id": item.get("method_package_id"),
                "outcome": item.get("outcome"),
                "confidence": item.get("confidence"),
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _compact_operator_lineage_signal(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal:
        return {}
    return {
        "hypothesis_count": signal.get("hypothesis_count", 0),
        "missing_hypothesis_rounds": signal.get("missing_hypothesis_rounds", 0),
        "type_counts": signal.get("type_counts") or {},
        "decision_counts": signal.get("decision_counts") or {},
        "target_file_counts": signal.get("target_file_counts") or {},
        "promoted_hypotheses": _compact_lineage_records(signal.get("promoted_hypotheses") or [], limit=8),
        "rolled_back_hypotheses": _compact_lineage_records(signal.get("rolled_back_hypotheses") or [], limit=8),
        "duplicate_hypotheses": _compact_lineage_records(signal.get("duplicate_hypotheses") or [], limit=8),
    }


def _operator_guidance_from_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """Translate pipeline memory into worker-facing rule/operator instructions.

    The guidance is prompt material only.  It helps a coding worker produce more
    auditable and diverse hypotheses while leaving evaluator acceptance
    unchanged.
    """

    signal = _compact_operator_lineage_signal(memory.get("operator_lineage_signal") or {})
    if not signal:
        return {
            "status": "missing_lineage",
            "must_do": ["Declare explicit rule_operator_hypotheses before code changes."],
            "preserve": [],
            "mutate": [],
            "avoid": [],
            "evidence": [],
        }

    promoted = signal.get("promoted_hypotheses") or []
    rolled_back = signal.get("rolled_back_hypotheses") or []
    duplicate = signal.get("duplicate_hypotheses") or []
    missing_rounds = int(signal.get("missing_hypothesis_rounds", 0) or 0)
    must_do = [
        "Use Core evaluator metrics as the only success evidence.",
        "State the natural-language rule/operator idea before editing code.",
    ]
    if missing_rounds > 0:
        must_do.append(
            "Previous rounds lacked auditable rule/operator hypotheses; include 1 to 3 concrete hypotheses with target files."
        )

    return {
        "status": "available",
        "must_do": must_do,
        "preserve": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
                "reason": "Promoted in prior evaluator-backed loop; preserve or ablate before replacing.",
            }
            for item in promoted[:5]
        ],
        "mutate": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
                "reason": "Rolled back in prior evaluator-backed loop; do not repeat unchanged.",
            }
            for item in rolled_back[:5]
        ],
        "avoid": [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "target_files": item.get("target_files") or [],
                "reason": "Duplicate proposal lineage; novelty must explain a material difference.",
            }
            for item in duplicate[:5]
        ],
        "evidence": [
            f"hypothesis_count={signal.get('hypothesis_count', 0)}",
            f"missing_hypothesis_rounds={missing_rounds}",
            f"type_counts={json.dumps(signal.get('type_counts') or {}, ensure_ascii=False)}",
        ],
    }


def _compact_lineage_records(records: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "duplicate_proposal": item.get("duplicate_proposal"),
                "name": str(item.get("name") or "")[:120],
                "type": str(item.get("type") or "")[:80],
                "target_files": [str(value) for value in (item.get("target_files") or [])[:12]],
                "expected_effect": str(item.get("expected_effect") or "")[:240],
                "novelty": str(item.get("novelty") or "")[:240],
            }
        )
        if len(compact) >= limit:
            break
    return compact


def _contract_review_payload(review: dict[str, Any]) -> dict[str, Any]:
    """压缩 Task Contract review 证据。

    目标不是重复整份 review，而是保留 worker 最先该看的证据骨架：不确定字段、
    特征/指标提示、文档结构和推荐阅读 section。
    """

    document_schema = _compact_document_schema(review.get("document_schema") or {})
    role_prioritized_sections = _role_prioritized_sections(document_schema, limit=16)
    return {
        "status": review.get("status"),
        "uncertain_fields": (review.get("uncertain_fields") or [])[:30],
        "extracted_problem_features": _compact_feature_hints(review.get("extracted_problem_features") or [], limit=30),
        "metric_hints": _compact_metric_hints(review.get("metric_hints") or [], limit=30),
        "document_schema": document_schema,
        "role_prioritized_sections": role_prioritized_sections,
        "has_document_schema": bool(document_schema.get("section_count")),
        "extraction_method": review.get("extraction_method"),
    }


def _compact_document_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """压缩文档结构抽取结果。

    文档 section 数量可能远大于 worker 首轮可读预算，因此这里保留结构化骨架，
    再由 `_role_prioritized_sections()` 导出一个更短的优先阅读列表。
    """

    if not schema:
        return {}
    compact_documents = []
    section_budget = 40
    for document in schema.get("documents") or []:
        sections = []
        for section in document.get("sections") or []:
            if section_budget <= 0:
                break
            sections.append(
                {
                    "heading": section.get("heading"),
                    "level": section.get("level"),
                    "line_start": section.get("line_start"),
                    "line_end": section.get("line_end"),
                    "roles": section.get("roles") or [],
                    "feature_hints": _compact_feature_hints(section.get("feature_hints") or [], limit=8),
                    "metric_hints": _compact_metric_hints(section.get("metric_hints") or [], limit=8),
                    "evidence_excerpt": str(section.get("evidence_excerpt") or "")[:180],
                }
            )
            section_budget -= 1
        compact_documents.append(
            {
                "path": document.get("path"),
                "section_count": document.get("section_count", len(sections)),
                "sections": sections,
            }
        )
        if section_budget <= 0:
            break
    return {
        "schema_version": schema.get("schema_version"),
        "document_count": schema.get("document_count", len(compact_documents)),
        "section_count": schema.get("section_count", sum(len(item["sections"]) for item in compact_documents)),
        "role_counts": schema.get("role_counts") or {},
        "documents": compact_documents,
        "truncated": section_budget <= 0,
    }


def _role_prioritized_sections(schema: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    """Return the most useful Markdown sections for a worker's first read.

    The full schema is preserved for auditability.  This derived list is a
    bounded reading order for long documents, so workers inspect objective,
    constraint, IO, and acceptance evidence before generic prose.
    """

    candidates: list[dict[str, Any]] = []
    for document_index, document in enumerate(schema.get("documents") or []):
        for section_index, section in enumerate(document.get("sections") or []):
            roles = list(section.get("roles") or ["general"])
            best_role_rank = min(SECTION_ROLE_PRIORITY.get(str(role), 8) for role in roles)
            hint_bonus = len(section.get("feature_hints") or []) + len(section.get("metric_hints") or [])
            candidates.append(
                {
                    "sort_key": (best_role_rank, -hint_bonus, document_index, section_index),
                    "payload": {
                        "source": document.get("path"),
                        "heading": section.get("heading"),
                        "line_start": section.get("line_start"),
                        "line_end": section.get("line_end"),
                        "roles": roles,
                        "feature_hints": _compact_feature_hints(section.get("feature_hints") or [], limit=8),
                        "metric_hints": _compact_metric_hints(section.get("metric_hints") or [], limit=8),
                        "evidence_excerpt": str(section.get("evidence_excerpt") or "")[:220],
                        "priority_reason": _priority_reason(roles, hint_bonus),
                    },
                }
            )

    candidates.sort(key=lambda item: item["sort_key"])
    return [item["payload"] for item in candidates[:limit]]


def _priority_reason(roles: list[str], hint_bonus: int) -> str:
    role_text = ", ".join(roles) if roles else "general"
    if hint_bonus:
        return f"roles={role_text}; contains {hint_bonus} extracted feature/metric hints"
    return f"roles={role_text}"


def _compact_feature_hints(hints: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    compact = []
    for item in hints[:limit]:
        compact.append(
            {
                "name": item.get("name"),
                "category": item.get("category"),
                "matched_pattern": item.get("matched_pattern"),
            }
        )
    return compact


def _compact_metric_hints(hints: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    compact = []
    for item in hints[:limit]:
        compact.append(
            {
                "metric": item.get("metric"),
                "direction": item.get("direction"),
                "matched_pattern": item.get("matched_pattern"),
            }
        )
    return compact


def _compact_worker_signal(worker_signal: dict[str, Any]) -> dict[str, Any]:
    rounds = []
    for item in worker_signal.get("rounds") or []:
        if not isinstance(item, dict):
            continue
        rounds.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "worker_status": item.get("worker_status"),
                "duplicate_proposal": item.get("duplicate_proposal"),
                "candidate_key": item.get("candidate_key"),
                "incumbent_key_after": item.get("incumbent_key_after"),
                "changed_files": (item.get("changed_files") or [])[:20],
                "proposal_diagnostics": item.get("proposal_diagnostics") or {},
            }
        )
        if len(rounds) >= 20:
            break
    return {
        "baseline_key": worker_signal.get("baseline_key"),
        "final_key": worker_signal.get("final_key"),
        "improved": worker_signal.get("improved"),
        "round_count": worker_signal.get("round_count", 0),
        "promoted_rounds": worker_signal.get("promoted_rounds", 0),
        "rounds": rounds,
    }


def _compact_project_intake(manifest: dict[str, Any]) -> dict[str, Any]:
    if not manifest:
        return {}
    context_index = []
    for item in manifest.get("context_index") or []:
        context_index.append(
            {
                "path": item.get("path"),
                "line_count": item.get("line_count"),
                "symbols": item.get("symbols") or [],
                "imports": item.get("imports") or [],
            }
        )
        if len(context_index) >= 40:
            break
    return {
        "project_root": manifest.get("project_root"),
        "git": {
            "branch": (manifest.get("git") or {}).get("branch"),
            "commit": (manifest.get("git") or {}).get("commit"),
            "dirty": (manifest.get("git") or {}).get("dirty"),
            "recent_hotspots": (manifest.get("git") or {}).get("recent_hotspots") or [],
        },
        "language_summary": manifest.get("language_summary") or {},
        "file_tree_summary": manifest.get("file_tree_summary") or {},
        "entry_files": (manifest.get("entry_files") or [])[:20],
        "core_algorithm_files": (manifest.get("core_algorithm_files") or [])[:30],
        "dependency_files": manifest.get("dependency_files") or [],
        "benchmark_files": (manifest.get("benchmark_files") or [])[:20],
        "validator_files": (manifest.get("validator_files") or [])[:20],
        "test_commands": manifest.get("test_commands") or [],
        "data_dirs": manifest.get("data_dirs") or [],
        "output_format_hints": manifest.get("output_format_hints") or {},
        "edit_policy": manifest.get("edit_policy") or {},
        "risk_flags": manifest.get("risk_flags") or [],
        "context_index": context_index,
    }


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
