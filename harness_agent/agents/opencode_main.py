"""OpenCode-backed Main Agent with a bounded, read-only planning view."""

from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from harness_agent.agents.main import (
    DirectionPlanRequest,
    EvidenceDrivenMainAgent,
    RoundReflectionRequest,
    WorkerAssignmentIssue,
    WorkerAssignmentRequest,
    activation_check_schema_errors,
    bind_direction_plan_to_method_catalog,
    candidate_method_names,
    configure_exact_probe_tournament,
    deterministic_round_reflection,
    ensure_direction_activation_contracts,
    ensure_method_family_activation_contract,
    enforce_improvement_direction_contract,
    fallback_planning_contract_status,
    high_flexibility_query_tags,
    merge_public_reasoning_traces,
    normalize_activation_checks,
    normalize_direction_plan,
    normalize_incumbent_assessment,
    normalize_public_reasoning_trace,
    normalize_round_reflection,
    write_direction_plan,
    write_round_reflection,
)
from harness_agent.context.compaction import compact_json
from harness_agent.context.knowledge import method_package_catalog, method_package_query_tags
from harness_agent.context.loader import load_context_dict
from harness_agent.context.packet import activate_direction_knowledge_context
from harness_agent.context import planning_packet as planning_packets
from harness_agent.core.cancellation import CancellationToken
from harness_agent.core.runner import (
    CREATE_NEW_PROCESS_GROUP,
    cleanup_process_descendants,
    kill_process_tree,
)
from harness_agent.workers.opencode_worker import (
    DEFAULT_OPENCODE_MODEL,
    OPENCODE_COMPACTION_CONFIG,
    json_dumps,
    opencode_subprocess_environment,
    summarize_opencode_compaction_events,
)


OPENCODE_MAIN_AGENT = "algoforge-main"
PLANNING_PACKET_MAX_CHARS = planning_packets.PLANNING_PACKET_MAX_CHARS
MAIN_FORMAT_RETRY_TIMEOUT_SECONDS = 90
FAST_MAIN_TIMEOUT_SECONDS = 120
FAST_PLANNING_PACKET_MAX_CHARS = 20_000
DEFAULT_MAIN_STALL_TIMEOUT_SECONDS = 15 * 60
MAIN_STALL_POLL_SECONDS = 1.0
DEFAULT_MAIN_AGENT_STEPS = 36
MAX_MAIN_AGENT_STEPS = 48


class OpenCodeMainAgent:
    """Use OpenCode's native agent/subagent runtime for direction planning only."""

    def __init__(
        self,
        *,
        executable: str = "opencode",
        model: str | None = None,
        variant: str | None = None,
        project_root: Path | None = None,
        timeout_seconds: int | None = None,
        stall_timeout_seconds: int | None = None,
        max_subagents: int | None = None,
        planning_mode: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        configured_executable = (
            os.environ.get("OPENCODE_EXECUTABLE") or executable
            if executable == "opencode"
            else executable
        )
        executable_path = Path(configured_executable)
        self.executable_path = (
            str(executable_path.resolve()) if executable_path.exists() else shutil.which(configured_executable)
        )
        self.model = model or os.environ.get("OPENCODE_MODEL") or DEFAULT_OPENCODE_MODEL
        self.variant = (variant or os.environ.get("OPENCODE_MAIN_VARIANT") or "").strip()
        self.run_command = os.environ.get("OPENCODE_RUN_COMMAND", "run")
        self.project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        self.timeout_seconds = resolve_optional_timeout_seconds(
            timeout_seconds,
            env_var="OPENCODE_MAIN_TIMEOUT_SECONDS",
            minimum=15,
        )
        configured_stall_timeout = stall_timeout_seconds
        if configured_stall_timeout is None:
            configured_stall_timeout = int(
                os.environ.get("OPENCODE_MAIN_STALL_TIMEOUT_SECONDS", DEFAULT_MAIN_STALL_TIMEOUT_SECONDS)
            )
        self.stall_timeout_seconds = (
            max(60, int(configured_stall_timeout)) if int(configured_stall_timeout) > 0 else None
        )
        configured_subagents = (
            max_subagents
            if max_subagents is not None
            else int(os.environ.get("OPENCODE_MAIN_MAX_SUBAGENTS", "4"))
        )
        self.max_subagents = max(0, min(4, int(configured_subagents)))
        configured_planning_mode = str(
            planning_mode or os.environ.get("OPENCODE_MAIN_PLANNING_MODE") or "research"
        ).strip().lower()
        self.planning_mode = (
            configured_planning_mode
            if configured_planning_mode in {"fast", "research"}
            else "research"
        )
        configured_steps = int(os.environ.get("OPENCODE_MAIN_MAX_STEPS", DEFAULT_MAIN_AGENT_STEPS))
        self.max_steps = max(12, min(MAX_MAIN_AGENT_STEPS, configured_steps))
        self.cancellation = cancellation
        self.fallback = EvidenceDrivenMainAgent()

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        if self.executable_path is None:
            return self._fallback(request, reason="OpenCode executable is unavailable")

        request.output_dir.mkdir(parents=True, exist_ok=True)
        context = load_context_dict(request.context_packet_path)
        guidance_ablation = (
            context.get("guidance_ablation")
            if isinstance(context.get("guidance_ablation"), dict)
            else {}
        )
        # None is a deliberate domain-guidance ablation, not a retrieval
        # failure. It receives a normal generic evidence plan with multiple
        # Worker lanes, but no Skills, cards, packages, or activation contract.
        if guidance_ablation.get("mode") == "none":
            return self._plan_direction_none(request)
        if self.planning_mode == "fast":
            return self._plan_direction_fast(request)

        incumbent_source_attachments = incumbent_source_files(context)
        planning_packet = build_planning_packet(
            context=context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        packet_path = request.output_dir / "planning_packet.json"
        planning_attachments = _write_packet_bundle(
            output_dir=request.output_dir,
            filename="planning_packet.json",
            packet=planning_packet,
        )
        planning_index_path = (request.output_dir / "planning_packet.index.json").resolve()
        audit_path: Path | None = None
        if planning_packet.get("incumbent_capability_audit"):
            audit_path = request.output_dir / "incumbent_capability_audit.json"
            audit_path.write_text(
                json_dumps(planning_packet["incumbent_capability_audit"]),
                encoding="utf-8",
            )

        research_state = (
            planning_packet.get("research_state")
            if isinstance(planning_packet.get("research_state"), dict)
            else {}
        )
        selection = inherited_direction_selection(
            planning_packet=planning_packet,
            round_index=request.round_index,
        )
        usage_payload = summarize_opencode_events("")
        usage_payload["attempts"] = 0
        if selection is None:
            selection_run = self._run_once(
                output_dir=request.output_dir,
                attachments=[*planning_attachments, *incumbent_source_attachments],
                prompt=(
                "这是方向选择阶段。先加载并遵循 experiment-design Skill，用可证伪实验而不是只看最终分数来"
                f"组织方向判断。先阅读当前任务索引 `{planning_index_path}`，index 中的 section 路径均相对其所在目录；"
                "再按需读取对应 section 文件；"
                "不要只依赖整包 JSON 的单次通读。阅读附件 PlanningPacket，只根据任务合同、实例画像、incumbent 证据和"
                "incumbent_capability_audit、strategy_selection_cards 选择一个主要搜索压力与方法族。"
                "分析过程中必须在 commentary 中分步输出中文思考：先说正在检查的证据，再说明由证据形成的"
                "假设和备选方向比较，最后说明选择及下一项验证；这些消息必须在最终决定之前真实发出，不能"
                "只在最终 JSON 中事后复述。"
                "先确认 incumbent 已有机制和具体控制参数，再判断是缺失、规模不足还是运行效果未知。"
                "必须阅读随 PlanningPacket 附带的 incumbent 源码，并把源码行为与 Core solver_evidence、"
                "上一轮 patch、competition_result 候选证据以及 round_reflection 对照后再做判断。"
                "不得选择具体 Method Package，"
                "不得给 Coding Worker 写实现任务。最终回答只返回一个 direction_selection JSON，并且 "
                "结构包含 method_families: [{id, role}]、兼容字段 method_family（等于第一项 id）、"
                "primary_search_pressure、diagnosis、measured_evidence、alternatives_considered、"
                "selection_rationale、knowledge_query 和 reasoning_trace。"
                "method_families 必须从 method_family_catalog 选择一到三个兼容项，第一项为 primary，其余为 "
                "complementary；只有证据支持组合时才多选。knowledge_query 只能使用所选方法族覆盖且存在于 "
                "knowledge_query_catalog 中的标签。reasoning_trace 要记录证据、"
                "推断、决定和下一项验证，不得编造未执行的命令。所有面向用户的文字使用简体中文。"
                "必须把共享 solver foundation 与正式优化方法族分开：无 incumbent 只要求先产出合法 warm start，"
                "不能据此把 constructive_search 设为正式赢家。低柔性/候选机稀疏表示 sequence 压力上升，"
                "应比较 coupled_local_search、真实 CP-SAT/CP-LNS 和预算允许的 population_memetic；"
                "constructive 只有在构造覆盖本身有实测缺口时才继续作为优化主族。"
                ),
                suffix="",
                allowed_specialist=(
                    ["requirements-method-analyst"]
                    if request.round_index < 0
                    else ["evidence-analyst", "requirements-method-analyst"]
                )[: self.max_subagents],
            )
            selection_raw = extract_direction_selection(selection_run["stdout"])
            usage_payload = summarize_opencode_events(selection_run["stdout"])
            usage_payload["attempts"] = 1
            selection = normalize_direction_selection(
                selection_raw,
                planning_packet=planning_packet,
                round_index=request.round_index,
            )
            if selection is None:
                self._write_usage(request.output_dir, usage_payload)
                return self._fallback(request, reason="OpenCode Main Agent did not return a valid direction selection")
        else:
            selection["selection_source"] = "research_state_inheritance"
            selection["selection_reason"] = research_state.get("selection_reason")
        selection_path = request.output_dir / "direction_selection.json"
        selection_path.write_text(json_dumps(selection), encoding="utf-8")

        implementation_context = dict(context)
        activate_direction_knowledge_context(
            implementation_context,
            direction_plan=selection,
        )
        task = context.get("task") if isinstance(context.get("task"), dict) else {}
        original_catalog = (
            context.get("method_package_catalog")
            if isinstance(context.get("method_package_catalog"), dict)
            else {}
        )
        implementation_context["method_package_catalog"] = method_package_catalog(
            problem_family=str(task.get("problem_family") or ""),
            active_features=[str(item) for item in original_catalog.get("active_features") or []],
            knowledge_query_tags=method_package_query_tags(
                knowledge_query=selection["knowledge_query"],
                method_family=selection["method_family"],
                active_features=original_catalog.get("active_features"),
            ),
        )
        implementation_packet = build_implementation_planning_packet(
            context=implementation_context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
            direction_selection=selection,
        )
        implementation_packet_path = request.output_dir / "implementation_planning_packet.json"
        implementation_attachments = _write_packet_bundle(
            output_dir=request.output_dir,
            filename="implementation_planning_packet.json",
            packet=implementation_packet,
        )
        implementation_index_path = (
            request.output_dir / "implementation_planning_packet.index.json"
        ).resolve()
        activation_schema_json = json_dumps(
            (
                (implementation_packet.get("planner_output_contract") or {}).get("activation_check_schema")
                if isinstance(implementation_packet.get("planner_output_contract"), dict)
                else {}
            )
        )

        active_cards = (
            (implementation_packet.get("active_direction_knowledge") or {}).get("cards") or []
        )
        eligible_packages = implementation_packet.get("eligible_method_packages") or []
        if not active_cards and not eligible_packages:
            rejection = {
                "status": "rejected_before_implementation_planning",
                "reason": "direction query matched neither implementation cards nor a compatible Method Package",
                "direction_selection": selection,
            }
            (request.output_dir / "direction_selection_rejection.json").write_text(
                json_dumps(rejection),
                encoding="utf-8",
            )
            self._write_usage(request.output_dir, usage_payload)
            return self._fallback(
                request,
                reason="Direction selection did not retrieve implementation knowledge",
                selection=selection,
            )

        implementation_run = self._run_once(
            output_dir=request.output_dir,
            attachments=[*implementation_attachments, *incumbent_source_attachments],
            prompt=(
                "这是实现规划阶段。方向已经确定。先加载并遵循 experiment-design Skill，把方向编译成最小、"
                f"可证伪且能改变下一步决策的候选实验。先阅读当前任务索引 `{implementation_index_path}`，"
                "index 中的 section 路径均相对其所在目录；"
                "再按需读取对应 section 文件；不要只依赖整包 JSON 的单次通读。阅读附件中的 direction_selection、"
                "active_direction_knowledge 和 eligible_method_packages，选择零个或一个真正匹配的方法包，"
                "知识卡、参考源码、推荐构建顺序和小步实现建议都是 advisory，不得把它们解释成禁止选择完整方法。"
                "证据与预算支持时可以选择完整方法包，也可以裁剪或组合参考机制；选择完整包要求独立适配且满足"
                "完整行为语义，不要求也不鼓励机械照抄参考源码。"
                "分析过程中必须在 commentary 中分步输出中文思考：指出 incumbent 的具体不足和证据，比较"
                "实现方案与保留项，再给出有界变异和证伪计划；不要等到最终 JSON 才一次性复述。"
                "然后输出完整 direction_plan 与 worker_assignment。若附件包含 user_intervention，还必须输出"
                " direction_patch，声明 action、set_fields 与 clear_fields；未列入 set_fields 的原计划字段必须继承。"
                "必须基于 incumbent_capability_audit 指定"
                "现有目标符号、实现限制、下一次有界变异和证伪指标，不得重复实现审计已确认存在的机制。"
                "必须检查 incumbent 源码、获胜 source、规则级 diagnostics 和上轮 patch；证据缺失时应把"
                "补采 telemetry 作为候选变体，而不是用静态猜测替代运行事实。"
                "严格按 planner_output_contract.competition_policy 输出 candidate_variants：达到"
                " minimum_candidate_variants 且不超过 maximum_candidate_variants；runtime_limits 中的 max 只表示"
                "容量上限，不得把它报告成实际启动数。"
                "各变体必须采用可区分、可证伪的实现机制，并显式保留 incumbent fallback；只有在"
                " experiment_stage=research_tournament 时才允许跨方法族比较，其余阶段必须保持当前主方向。"
                "主 direction_plan 和每个 candidate_variants 都必须声明 activation_checks，用"
                " telemetry/diagnostics 证明机制已经执行，而不是把质量结果本身当作执行证明。"
                f"activation_checks 与 next_action.required_activation_checks 必须严格满足这个 machine-checkable schema：{activation_schema_json}。"
                "其中 exists/truthy 可以省略 expected；eq/ne/gt/gte/lt/lte/contains 必须提供 expected；"
                "aggregation=min_passes 时必须提供正整数 min_passes。"
                "交付物必须覆盖所选知识要求的耦合组件，"
                "并输出至少三步 reasoning_trace；最终回答只能包含 JSON，不得直接写代码。"
                "所有 JSON 字段名必须严格使用附件 schema 的英文 key，不得翻译字段名；中文只用于字段值。"
                "candidate_variants 必须放在 direction_plan 内；顶层只能包含 direction_plan、worker_assignment，"
                "以及用户修订时的 direction_patch。"
                "所有自然语言值使用简体中文。"
            ),
            suffix="_implementation",
            allowed_specialist=["plan-critic", "candidate-strategy-analyst"][: self.max_subagents],
        )
        raw = extract_planned_direction(implementation_run["stdout"])
        usage_payload = merge_event_summaries(
            usage_payload,
            summarize_opencode_events(implementation_run["stdout"]),
        )
        usage_payload["attempts"] = int(usage_payload.get("attempts") or 0) + 1
        if (
            raw is None
            and not implementation_run["timed_out"]
            and not implementation_run.get("stalled")
            and has_model_text(implementation_run["stdout"])
        ):
            invalid_path = request.output_dir / "main_agent_invalid_response.txt"
            invalid_path.write_text(
                bounded_invalid_response(implementation_run["stdout"]),
                encoding="utf-8",
            )
            retry = self._run_once(
                output_dir=request.output_dir,
                attachments=[*implementation_attachments, invalid_path, *incumbent_source_attachments],
                prompt=(
                    "把上一份实现规划修复为唯一一个合法 JSON 对象，顶层只能包含 direction_plan 和 "
                    "worker_assignment；保持已选方向和 knowledge_query 不变，并补齐 activation_checks。"
                    f"activation_checks 必须继续满足这个 schema：{activation_schema_json}。"
                ),
                suffix="_implementation_retry",
                timeout_seconds=bounded_timeout_seconds(
                    self.timeout_seconds,
                    MAIN_FORMAT_RETRY_TIMEOUT_SECONDS,
                ),
                allowed_specialist=None,
            )
            raw = extract_planned_direction(retry["stdout"])
            usage_payload = merge_event_summaries(
                usage_payload,
                summarize_opencode_events(retry["stdout"]),
            )
            usage_payload["attempts"] = int(usage_payload.get("attempts") or 0) + 1
        contract_errors = incumbent_planning_contract_errors(
            raw,
            planning_packet=implementation_packet,
            round_index=request.round_index,
        )
        if raw is not None and contract_errors and usage_payload.get("attempts", 0) < 3:
            contract_error_path = request.output_dir / "incumbent_planning_contract_errors.json"
            contract_error_path.write_text(
                json_dumps({"errors": contract_errors}),
                encoding="utf-8",
            )
            invalid_plan_path = request.output_dir / "incumbent_planning_invalid.json"
            invalid_plan_path.write_text(json_dumps(raw), encoding="utf-8")
            retry = self._run_once(
                output_dir=request.output_dir,
                attachments=[invalid_plan_path, contract_error_path],
                prompt=(
                    "只重排并补全附件 incumbent_planning_invalid.json，不要重新研究、读取其他文件或调用工具。"
                    "根据错误附件补齐已验证能力、具体实现限制、瓶颈假设、审计证据引用，以及指向现有符号的"
                    "下一次变异和证伪指标。不得改变已选方法族与 knowledge_query。至少保留三步公开研究日志 "
                    "并为主 direction_plan 及每个 candidate_variants 补齐 activation_checks，且这些检查只用于"
                    "证明机制执行，不得拿质量结果充当 activation_checks。"
                    f"合法 schema 如下：{activation_schema_json}。exists/truthy 可以省略 expected；"
                    "比较类算子必须提供 expected；aggregation=min_passes 时必须提供正整数 min_passes。"
                    "reasoning_trace，每步使用 stage、summary、evidence、inference、decision、next_check。"
                    "所有 JSON 字段名必须使用英文 schema key，中文只能出现在字段值中；candidate_variants 放在 "
                    "direction_plan 内。顶层只能包含 direction_plan、worker_assignment，以及用户修订时的"
                    " direction_patch。返回唯一合法 JSON。"
                ),
                suffix="_incumbent_contract_retry",
                timeout_seconds=bounded_timeout_seconds(
                    self.timeout_seconds,
                    MAIN_FORMAT_RETRY_TIMEOUT_SECONDS,
                ),
                allowed_specialist=None,
            )
            raw = extract_planned_direction(retry["stdout"])
            usage_payload = merge_event_summaries(
                usage_payload,
                summarize_opencode_events(retry["stdout"]),
            )
            usage_payload["attempts"] = int(usage_payload.get("attempts") or 0) + 1
            contract_errors = incumbent_planning_contract_errors(
                raw,
                planning_packet=implementation_packet,
                round_index=request.round_index,
            )
        if contract_errors:
            (request.output_dir / "incumbent_planning_contract_rejection.json").write_text(
                json_dumps({"errors": contract_errors}),
                encoding="utf-8",
            )
            raw = None
        self._write_usage(request.output_dir, usage_payload)
        if raw is None:
            return self._fallback(
                request,
                reason="OpenCode Main Agent did not return valid implementation planning JSON",
                selection=selection,
            )

        (request.output_dir / "planned_direction_raw.json").write_text(
            json_dumps(raw),
            encoding="utf-8",
        )
        direction_payload = merge_worker_handoff_into_direction(raw)
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(direction_payload, round_index=request.round_index),
            context=implementation_context,
        )
        plan["method_family"] = selection["method_family"]
        plan["method_families"] = selection["method_families"]
        plan["knowledge_query"] = selection["knowledge_query"]
        state_stage = str(research_state.get("experiment_stage") or "").strip()
        if request.round_index >= 0 and state_stage in {
            "probe",
            "scale",
            "pivot",
            "research_tournament",
        }:
            plan["experiment_stage"] = state_stage
        plan["direction_selection"] = selection
        if isinstance(raw.get("direction_patch"), dict):
            plan["user_revision_patch"] = dict(raw["direction_patch"])
        plan["reasoning_trace"] = merge_public_reasoning_traces(
            selection.get("reasoning_trace"),
            plan.get("reasoning_trace"),
        )
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        plan = ensure_direction_activation_contracts(plan)
        plan["planner"] = "opencode_main_agent"
        plan["planning_evidence"] = {
            "planning_packet_path": str(packet_path.resolve()),
            "direction_selection_path": str(selection_path.resolve()),
            "implementation_planning_packet_path": str(implementation_packet_path.resolve()),
            "incumbent_capability_audit_path": str(audit_path.resolve()) if audit_path else None,
            "called_subagents": usage_payload.get("called_subagents") or [],
            "event_count": usage_payload.get("event_count", 0),
            "compaction": usage_payload.get("compaction") or {},
        }
        return write_direction_plan(request.output_dir, plan)

    def _plan_direction_none(self, request: DirectionPlanRequest) -> dict[str, Any]:
        """Ask generic Main to propose distinct methods without domain catalogs."""

        request.output_dir.mkdir(parents=True, exist_ok=True)
        context = load_context_dict(request.context_packet_path)
        planning_packet = build_planning_packet(
            context=context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        competition = (
            request.loop_feedback.get("competition")
            if isinstance(request.loop_feedback.get("competition"), dict)
            else {}
        )
        max_workers = max(1, min(4, int(competition.get("max_competing_workers") or 1)))
        incumbent = (
            planning_packet.get("incumbent_evidence")
            if isinstance(planning_packet.get("incumbent_evidence"), dict)
            else {}
        )
        generic_packet = {
            "schema_version": 1,
            "planning_stage": "generic_guidance_ablation",
            "task_digest": _bounded_fast_value(planning_packet.get("task_digest")),
            "instance_diagnostics": _bounded_fast_value(planning_packet.get("instance_diagnostics")),
            "incumbent_evidence": _bounded_fast_value(
                {
                    "objective_key": incumbent.get("objective_key"),
                    "evaluation": incumbent.get("evaluation"),
                }
            ),
            "recent_round_evidence": _bounded_fast_value(
                planning_packet.get("recent_round_evidence"), max_list=3
            ),
            "runtime_limits": _bounded_fast_value(planning_packet.get("runtime_limits")),
            "output_contract": {
                "candidate_method_count": max_workers,
                "required_candidate_fields": [
                    "candidate_id",
                    "method_name",
                    "hypothesis",
                    "worker_objective",
                    "strategy_type",
                ],
                "methods_must_be_distinct": True,
            },
        }
        packet_path = request.output_dir / "generic_ablation_planning_packet.json"
        packet_path.write_text(json_dumps(generic_packet), encoding="utf-8")
        source_attachments = incumbent_source_files(context)
        planning_attachments = [packet_path, *source_attachments]
        base_prompt = (
                "这是无 FJSP 领域增强的通用算法规划对照。只阅读附件中的任务、实例事实、incumbent 与 Core 反馈；"
                "可以读取随附的当前 incumbent solver 源码，但不要读取附件之外的任何文件。"
                "不要加载 Skill，不要调用子 Agent，不要读取知识库、方法包、候选算子库或历史经验。"
                f"请独立提出恰好 {max_workers} 种算法方法；每条 lane 必须是明确且互不相同的方法方案，"
                "不能用 direct_evidence、minimal_risk、orthogonal 等角色名代替方法名。"
                "只返回 JSON，顶层为 direction_plan；其中 candidate_variants 为数组，每项严格包含 "
                "candidate_id、method_name、hypothesis、worker_objective、strategy_type、change_scope、preserve、avoid。"
                "worker_objective 要让 Coding Worker 实现该方法并保留合法 incumbent fallback。"
        )
        variants: list[dict[str, Any]] = []
        usage_payload = summarize_opencode_events("")
        for attempt_index in range(2):
            run = self._run_once(
                output_dir=request.output_dir,
                attachments=planning_attachments,
                prompt=(
                    base_prompt
                    if attempt_index == 0
                    else base_prompt
                    + " 上一次没有返回满足契约的 JSON；本次不要解释、不要请求更多证据，立即只输出 JSON。"
                ),
                suffix="_generic_ablation" if attempt_index == 0 else "_generic_ablation_retry",
                timeout_seconds=bounded_timeout_seconds(self.timeout_seconds, FAST_MAIN_TIMEOUT_SECONDS),
                allowed_specialist=None,
                allowed_skills=[],
                attachments_only=True,
                isolated_cwd=True,
            )
            usage_payload = merge_event_summaries(
                usage_payload,
                summarize_opencode_events(run["stdout"]),
            )
            raw = extract_planned_direction(run["stdout"])
            raw_plan = raw.get("direction_plan") if isinstance(raw, dict) else None
            variants = normalize_generic_method_variants(
                raw_plan.get("candidate_variants") if isinstance(raw_plan, dict) else None,
                expected=max_workers,
            )
            if len(variants) == max_workers:
                break
        usage_payload.update({"attempts": attempt_index + 1, "planning_mode": "generic_guidance_ablation"})
        self._write_usage(request.output_dir, usage_payload)
        if len(variants) != max_workers:
            raise ValueError(
                "generic ablation Main must propose exactly "
                f"{max_workers} distinct algorithm method lanes"
            )
        plan = normalize_direction_plan(
            {
                "direction_id": f"d{request.round_index:03d}",
                "title": "Generic algorithm-method tournament",
                "strategy_type": "generic_method_tournament",
                "hypothesis": (
                    "Distinct generic algorithm methods can be tested independently against the current incumbent."
                ),
                "worker_objective": (
                    "Implement each proposed method as a bounded incumbent-preserving candidate."
                ),
                "diagnosis": (
                    "Use the model-proposed methods directly without domain retrieval or fallback planning."
                ),
                "experiment_stage": "research_tournament",
                "preserve": [
                    "Preserve the complete legal incumbent and return it when the method does not improve it."
                ],
                "avoid": [
                    "Do not load domain Skills, knowledge cards, method packages, candidate operator libraries, "
                    "or historical scores."
                ],
                "acceptance_checks": [
                    "Candidate passes deterministic checks and the fixed evaluator.",
                    "Candidate remains complete and legal under the active contract.",
                    "Candidate is promoted only when it strictly improves the incumbent.",
                ],
                "completion_rule": (
                    "Implement only the assigned generic method while preserving the complete legal incumbent."
                ),
            },
            round_index=request.round_index,
        )
        plan.update(
            {
                "method_family": "",
                "method_families": [],
                "method_package_id": "",
                "knowledge_query": [],
                "knowledge_paths": [],
                "implementation_bundle": {},
                "activation_contract_version": 0,
                "activation_checks": [],
                "candidate_variants": variants,
                "worker_lane_policy": {
                    "schema_version": 1,
                    "mechanism_selection": "generic_method_tournament",
                    "lane_count": max_workers,
                    "method_names": [item["method_name"] for item in variants],
                },
                "planning_contract_status": {
                    "schema_version": 1,
                    "status": "satisfied",
                    "source": "generic_guidance_ablation",
                    "maximum_worker_lanes": max_workers,
                    "planned_worker_lanes": max_workers,
                    "actual_started_candidates_source": "competition_result.candidates",
                    "activation_mode": "not_applicable",
                    "promotion_policy": "core_and_semantic_gates",
                },
                "planner": "generic_ablation_main_agent",
                "planning_evidence": {
                    "planning_mode": "generic_guidance_ablation",
                    "called_subagents": [],
                    "domain_knowledge_enabled": False,
                    "planning_packet_path": str(packet_path.resolve()),
                },
            }
        )
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        return write_direction_plan(request.output_dir, plan)

    def _plan_direction_fast(self, request: DirectionPlanRequest) -> dict[str, Any]:
        """Select one method direction and delegate mechanism design to Workers."""

        context = load_context_dict(request.context_packet_path)
        planning_packet = build_planning_packet(
            context=context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        fast_packet = build_fast_planning_packet(planning_packet)
        packet_path = request.output_dir / "fast_planning_packet.json"
        packet_path.write_text(json_dumps(fast_packet), encoding="utf-8")

        run = self._run_once(
            output_dir=request.output_dir,
            attachments=[packet_path],
            prompt=(
                "这是 AlgoForge 快速规划。附件 FastPlanningPacket 已包含完成决策所需的任务、实例画像、"
                "incumbent 摘要、最近 Core 证据、方法族目录和输出约束。你只负责判断方法族和粗粒度方向，"
                "不负责实现规划。不要加载 Skill，不要调用子 Agent，不要读取附件之外的文件，也不要输出"
                "分步 commentary。只返回一个 JSON 对象，顶层严格包含 direction_selection 和 direction_brief。"
                "direction_brief 只能描述假设、关注范围、影响、风险以及 preserve/avoid 边界；不要输出"
                "candidate_variants、worker_assignment、activation_checks、deliverables、implementation_order、"
                "next_mutation 或任何具体算法机制。并行 Worker 会在读取获准 Skill 和 incumbent 后各自选择机制。"
                "高柔性画像可以作为证据，但必须先独立判断方法族，不能直接硬路由到某个 Skill。"
                "若是同方向续跑，遵守 research_state 的继承策略，只更新粗粒度假设和关注范围。"
                "所有字段名使用 schema 的英文 key，自然语言值使用简体中文。"
            ),
            suffix="_fast",
            timeout_seconds=bounded_timeout_seconds(self.timeout_seconds, FAST_MAIN_TIMEOUT_SECONDS),
            allowed_specialist=None,
        )
        usage_payload = summarize_opencode_events(run["stdout"])
        usage_payload["attempts"] = 1
        usage_payload["planning_mode"] = "fast"
        self._write_usage(request.output_dir, usage_payload)

        raw = extract_planned_direction(run["stdout"])
        if raw is None:
            return self._fallback(request, reason="Fast Main Agent did not return a valid direction plan")

        raw_selection = raw.get("direction_selection")
        if not isinstance(raw_selection, dict):
            raw_selection = raw.get("direction_plan")
        selection = inherited_direction_selection(
            planning_packet=planning_packet,
            round_index=request.round_index,
        )
        if selection is not None:
            selection["selection_source"] = "research_state_inheritance"
        else:
            selection = normalize_direction_selection(
                {"direction_selection": raw_selection} if isinstance(raw_selection, dict) else {},
                planning_packet=planning_packet,
                round_index=request.round_index,
            )
        if selection is None:
            return self._fallback(
                request,
                reason="Fast Main Agent did not select a compatible method family",
            )

        implementation_context = dict(context)
        activate_direction_knowledge_context(implementation_context, direction_plan=selection)
        task = context.get("task") if isinstance(context.get("task"), dict) else {}
        original_catalog = (
            context.get("method_package_catalog")
            if isinstance(context.get("method_package_catalog"), dict)
            else {}
        )
        implementation_context["method_package_catalog"] = method_package_catalog(
            problem_family=str(task.get("problem_family") or ""),
            active_features=[str(item) for item in original_catalog.get("active_features") or []],
            knowledge_query_tags=method_package_query_tags(
                knowledge_query=selection["knowledge_query"],
                method_family=selection["method_family"],
                active_features=original_catalog.get("active_features"),
            ),
        )
        method_package_id, package_selection_source = resolve_fast_method_package(
            catalog=implementation_context["method_package_catalog"],
            method_family=selection["method_family"],
            loop_feedback=request.loop_feedback,
        )
        selection["method_package_id"] = method_package_id
        selection["method_package_resolution"] = {
            "policy": "fast_harness_resolution",
            "source": package_selection_source,
        }
        selection_path = request.output_dir / "direction_selection.json"
        selection_path.write_text(json_dumps(selection), encoding="utf-8")

        raw_brief = raw.get("direction_brief")
        direction_brief = normalize_fast_direction_brief(
            raw_brief if isinstance(raw_brief, dict) else {},
            selection=selection,
            round_index=request.round_index,
        )
        ignored_output_fields = sorted(
            key
            for key in (
                "direction_plan",
                "worker_assignment",
                "candidate_variants",
                "activation_checks",
                "deliverables",
                "implementation_order",
                "next_mutation",
            )
            if key in raw
        )
        sanitized_raw = {
            "direction_selection": raw_selection,
            "direction_brief": direction_brief,
        }
        if ignored_output_fields:
            sanitized_raw["ignored_output_fields"] = ignored_output_fields
        (request.output_dir / "planned_direction_raw.json").write_text(
            json_dumps(sanitized_raw),
            encoding="utf-8",
        )

        direction_payload = {
            **direction_brief,
            "direction_id": selection["direction_id"],
            "diagnosis": selection.get("diagnosis") or direction_brief.get("rationale_summary"),
            "direction_judgment": direction_brief.get("rationale_summary"),
            "selection_rationale": selection.get("selection_rationale")
            or direction_brief.get("rationale_summary"),
            "evidence_summary": selection.get("measured_evidence") or [],
            "reasoning_trace": selection.get("reasoning_trace") or [],
            "method_family": selection["method_family"],
            "method_families": selection["method_families"],
            "knowledge_query": selection["knowledge_query"],
            "method_package_id": method_package_id,
        }
        plan = bind_direction_plan_to_method_catalog(
            normalize_direction_plan(direction_payload, round_index=request.round_index),
            context=implementation_context,
        )
        plan["method_family"] = selection["method_family"]
        plan["method_families"] = selection["method_families"]
        plan["knowledge_query"] = selection["knowledge_query"]
        plan["method_package_selection"]["selection_source"] = package_selection_source
        plan["method_package_selection"]["selection_policy"] = "fast_harness_resolution"
        research_state = (
            planning_packet.get("research_state")
            if isinstance(planning_packet.get("research_state"), dict)
            else {}
        )
        state_stage = str(research_state.get("experiment_stage") or "").strip()
        if request.round_index >= 0 and state_stage in {
            "probe",
            "scale",
            "pivot",
            "research_tournament",
        }:
            plan["experiment_stage"] = state_stage
        plan["direction_selection"] = selection
        plan["direction_brief"] = direction_brief
        max_workers = max(
            1,
            min(
                4,
                int((fast_packet.get("runtime_limits") or {}).get("max_competing_workers") or 1),
            ),
        )
        plan = configure_exact_probe_tournament(
            plan,
            context=context,
            max_workers=max_workers,
        )
        exact_probe = (
            isinstance(plan.get("exact_probe_policy"), dict)
            and bool(plan["exact_probe_policy"].get("reserved"))
        )
        family_tournament = len(
            {
                str(item.get("method_family") or "").strip()
                for item in plan.get("candidate_variants") or []
                if isinstance(item, dict) and str(item.get("method_family") or "").strip()
            }
        ) >= 2
        plan["worker_lane_policy"] = {
            "schema_version": 1,
            "mechanism_selection": (
                "exact_probe_tournament"
                if exact_probe
                else "family_hypothesis_tournament"
                if family_tournament
                else "delegated_to_worker"
            ),
            "lane_count": max_workers,
        }
        if not family_tournament:
            plan["candidate_variants"] = []
        plan["worker_lane_policy"]["method_names"] = candidate_method_names(
            plan,
            limit=max_workers,
        )
        plan = ensure_direction_activation_contracts(plan)
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        plan["planning_contract_status"] = {
            "schema_version": 1,
            "status": "satisfied",
            "source": "fast_direction_delegation",
            "mechanism_selection": plan["worker_lane_policy"]["mechanism_selection"],
            "maximum_worker_lanes": max_workers,
            "planned_worker_lanes": max_workers,
            "actual_started_candidates_source": "competition_result.candidates",
            "activation_mode": "worker_owned_advisory",
            "promotion_policy": "core_and_semantic_gates",
        }
        plan["planner"] = "opencode_main_agent_fast"
        plan["planning_evidence"] = {
            "planning_mode": "fast",
            "fast_planning_packet_path": str(packet_path.resolve()),
            "direction_selection_path": str(selection_path.resolve()),
            "called_subagents": [],
            "event_count": usage_payload.get("event_count", 0),
            "compaction": usage_payload.get("compaction") or {},
        }
        return write_direction_plan(request.output_dir, plan)

    @staticmethod
    def _write_usage(output_dir: Path, usage_payload: dict[str, Any]) -> None:
        (output_dir / "main_agent_usage.json").write_text(
            json_dumps(usage_payload),
            encoding="utf-8",
        )

    def issue_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        # The model selects and decomposes the direction. Harness compilation
        # enforces paths, budgets, and permission boundaries deterministically.
        return self.fallback.issue_worker_assignment(request)

    def revise_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        # Repair revisions deliberately avoid another expensive planning call:
        # latest evaluator evidence is compiled into the same locked direction.
        return self.fallback.revise_worker_assignment(request)

    def reflect_on_round(self, request: RoundReflectionRequest) -> dict[str, Any]:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        evidence = build_round_reflection_evidence(request)
        evidence_path = request.output_dir / "round_evidence.json"
        evidence_path.write_text(json_dumps(evidence), encoding="utf-8")
        if self.executable_path is None or self.planning_mode == "fast":
            return write_round_reflection(request.output_dir, deterministic_round_reflection(request))

        reflection_run = self._run_once(
            output_dir=request.output_dir,
            attachments=[evidence_path],
            prompt=(
                "这是轮后因果复盘阶段。先加载并遵循 experiment-design Skill，严格区分机制激活证据、"
                "诊断性 smoke 和正式 Core 结果。阅读附件 round_evidence.json，只根据固定 evaluator、机制激活检查、"
                "promotion_check 和候选对比结果判断本轮假设。最终只返回一个 JSON 对象。"
                "hypothesis_outcome 只能是 supported、refuted、inconclusive。"
                "candidate_findings 必须逐个候选说明该候选机制是否激活、证据是什么、这些证据如何支持或削弱原假设，"
                "以及为什么不能把未激活候选的失败直接解释为方向无效。"
                "next_action.action 只能是 probe、scale、pivot、research_tournament 之一。"
                "若建议 scale，必须说明已有机制已执行且值得扩大范围；若建议 probe，必须说明下一轮需要补哪些执行证据；"
                "若建议 pivot，必须说明当前主假设为何已被充分反驳；若建议 research_tournament，可以跨方法族，但仍需"
                "给出有界比较理由。"
                "required_activation_checks 必须是下一轮用来证明机制确实执行的 telemetry/diagnostics 检查，"
                "不是 makespan、排名、promotion 之类质量结果。"
                "只有当候选未真正执行声明机制、或现有证据相互冲突不足以归因时，才能返回 inconclusive。"
                "reasoning_trace 只记录可公开审计的证据、推断、决定和下一检查。所有自然语言值使用简体中文。"
            ),
            suffix="_round_reflection",
            timeout_seconds=bounded_timeout_seconds(
                self.timeout_seconds,
                MAIN_FORMAT_RETRY_TIMEOUT_SECONDS,
            ),
            allowed_specialist=None,
        )
        raw = extract_round_reflection(reflection_run["stdout"])
        if raw is None and not reflection_run["timed_out"] and has_model_text(reflection_run["stdout"]):
            invalid_path = request.output_dir / "round_reflection_invalid_response.txt"
            invalid_path.write_text(
                bounded_invalid_response(reflection_run["stdout"]),
                encoding="utf-8",
            )
            retry = self._run_once(
                output_dir=request.output_dir,
                attachments=[evidence_path, invalid_path],
                prompt=(
                    "把上一份轮后复盘修复为唯一一个合法 JSON 对象。保持原有证据结论，不要新增实验，"
                    "不要输出 JSON 之外的文字。"
                ),
                suffix="_round_reflection_retry",
                timeout_seconds=bounded_timeout_seconds(
                    self.timeout_seconds,
                    MAIN_FORMAT_RETRY_TIMEOUT_SECONDS,
                ),
                allowed_specialist=None,
            )
            raw = extract_round_reflection(retry["stdout"])
        reflection = (
            normalize_round_reflection(
                prepare_round_reflection_payload(raw),
                request=request,
            )
            if raw is not None
            else deterministic_round_reflection(request)
        )
        return write_round_reflection(request.output_dir, reflection)

    def _run_once(
        self,
        *,
        output_dir: Path,
        attachments: list[Path],
        prompt: str,
        suffix: str,
        timeout_seconds: int | None = None,
        allowed_specialist: str | list[str] | None = None,
        allowed_skills: list[str] | None = None,
        attachments_only: bool = False,
        isolated_cwd: bool = False,
    ) -> dict[str, Any]:
        allowed_specialists = normalize_allowed_specialists(
            allowed_specialist,
            limit=self.max_subagents,
        )
        if allowed_specialists and attachments:
            attachment = attachments[0].resolve()
            try:
                specialist_path = attachment.relative_to(self.project_root).as_posix()
            except ValueError:
                specialist_path = attachment.as_posix()
            prompt += (
                f" 本阶段可调用的 specialist 为 {allowed_specialists}，总调用次数最多"
                f" {len(allowed_specialists)} 次，每个 specialist 最多调用一次。"
                f"调用时必须要求它读取 `{specialist_path}`，并按需要读取同轮附带的 incumbent 源码。"
            )
        command = [str(self.executable_path)]
        if self.run_command:
            command.extend(shlex.split(self.run_command, posix=False))
        command.extend(["--model", self.model])
        if self.variant:
            command.extend(["--variant", self.variant])
        stage = suffix.strip("_").replace("_", "-") or "direction-selection"
        session_title = f"AlgoForge Main {output_dir.parent.name} {stage}"[:120]
        # These are disposable harness sessions.  Supplying a title prevents
        # OpenCode from spending a separate provider request on auto-naming.
        command.extend(["--title", session_title])
        command.extend(["--agent", OPENCODE_MAIN_AGENT, "--format", "json", prompt])
        command.extend(f"--file={path.resolve()}" for path in attachments)
        command_path = output_dir / f"opencode_main_command{suffix}.json"
        events_path = output_dir / f"opencode_main_events{suffix}.jsonl"
        stderr_path = output_dir / f"opencode_main_stderr{suffix}.txt"
        runtime_path = output_dir / f"opencode_main_runtime_config{suffix}.json"
        command_path.write_text(json_dumps(command), encoding="utf-8")
        runtime_config = self._runtime_config(
            attachment_paths=attachments,
            allowed_specialists=allowed_specialists,
            allowed_skills=allowed_skills,
            attachments_only=attachments_only,
        )
        runtime_path.write_text(json_dumps(runtime_config), encoding="utf-8")

        isolated_workspace = (
            tempfile.TemporaryDirectory(prefix="algoforge-main-ablation-")
            if isolated_cwd
            else None
        )
        popen_kwargs: dict[str, object] = {
            "cwd": isolated_workspace.name if isolated_workspace is not None else str(self.project_root),
            "env": opencode_subprocess_environment(
                runtime_config=runtime_config,
                isolation_root=output_dir / ".opencode_runtime",
            ),
            "stdin": subprocess.DEVNULL,
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        registration = (
            self.cancellation.register_terminator(lambda: kill_process_tree(process))
            if self.cancellation is not None
            else None
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_thread = threading.Thread(
            target=stream_process_pipe,
            args=(process.stdout, events_path, stdout_chunks),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stream_process_pipe,
            args=(process.stderr, stderr_path, stderr_chunks),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        stall_state = {"stalled": False}
        stall_stop = threading.Event()
        stall_thread: threading.Thread | None = None
        if self.stall_timeout_seconds is not None:
            stall_thread = threading.Thread(
                target=monitor_process_stall,
                args=(
                    process,
                    stdout_chunks,
                    stderr_chunks,
                    self.stall_timeout_seconds,
                    stall_stop,
                    stall_state,
                ),
                daemon=True,
            )
            stall_thread.start()
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        try:
            if timeout is None:
                process.wait()
            else:
                process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        finally:
            stall_stop.set()
            if stall_thread is not None:
                stall_thread.join(timeout=2)
            if self.cancellation is not None:
                self.cancellation.unregister_terminator(registration)
        if not timed_out:
            cleanup_process_descendants(process)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (AttributeError, OSError):
                pass
        if isolated_workspace is not None:
            isolated_workspace.cleanup()
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        stalled = bool(stall_state.get("stalled"))
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        if timed_out and not stderr and timeout is not None:
            stderr = f"OpenCode Main exceeded {timeout} seconds."
            stderr_path.write_text(stderr, encoding="utf-8")
        elif stalled and not stderr and self.stall_timeout_seconds is not None:
            stderr = f"OpenCode Main produced no output for {self.stall_timeout_seconds} seconds."
            stderr_path.write_text(stderr, encoding="utf-8")
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": str(process.returncode),
            "timed_out": timed_out,
            "stalled": stalled,
            **opencode_event_stream_health(stdout),
        }

    def _runtime_config(
        self,
        *,
        attachment_paths: list[Path],
        allowed_specialists: list[str] | None = None,
        allowed_specialist: str | None = None,
        allowed_skills: list[str] | None = None,
        attachments_only: bool = False,
    ) -> dict[str, Any]:
        allowed_specialists = normalize_allowed_specialists(
            allowed_specialists or allowed_specialist,
            limit=self.max_subagents,
        )
        attachment_permissions = self._attachment_permissions(attachment_paths)
        main_read_permissions = {"*": "deny"} if attachments_only else self._main_read_permissions()
        task_permissions = {"*": "deny"}
        for specialist in allowed_specialists:
            task_permissions[specialist] = "allow"
        permitted_skills = (
            ["algoforge-assignment", "experiment-design"]
            if allowed_skills is None
            else [str(item) for item in allowed_skills if str(item).strip()]
        )
        skill_permissions = {"*": "deny"}
        for skill_id in permitted_skills:
            skill_permissions[skill_id] = "allow"
        agent_configs: dict[str, Any] = {
            OPENCODE_MAIN_AGENT: {
                "steps": self.max_steps,
                "permission": {
                    "*": "deny",
                    "read": main_read_permissions,
                    "glob": "deny",
                    "grep": "deny",
                    "bash": "deny",
                    "edit": "deny",
                    "question": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "list": "deny",
                    "lsp": "deny",
                    "todowrite": "deny",
                    "doom_loop": "deny",
                    "external_directory": {
                        **attachment_permissions,
                    },
                    "task": task_permissions,
                    "skill": skill_permissions,
                },
            }
        }
        for name in (
            "requirements-method-analyst",
            "evidence-analyst",
            "plan-critic",
            "candidate-strategy-analyst",
        ):
            agent_configs[name] = {
                "disable": name not in allowed_specialists,
                "permission": {
                    "*": "deny",
                    "read": main_read_permissions,
                    "glob": "deny",
                    "grep": "deny",
                    "bash": "deny",
                    "edit": "deny",
                    "task": "deny",
                    "question": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "list": "deny",
                    "lsp": "deny",
                    "todowrite": "deny",
                    "doom_loop": "deny",
                    "skill": "deny",
                    "external_directory": attachment_permissions,
                }
            }
        return {
            "$schema": "https://opencode.ai/config.json",
            "snapshot": False,
            "compaction": dict(OPENCODE_COMPACTION_CONFIG),
            "agent": agent_configs,
        }

    def _main_read_permissions(self) -> dict[str, str]:
        """Give Main a complete read-only project view without exposing secrets."""

        permissions = {"*": "allow"}
        secret_patterns = (
            ".env",
            ".env.*",
            "**/.env",
            "**/.env.*",
            "*.pem",
            "*.key",
            "**/*.pem",
            "**/*.key",
        )
        for pattern in secret_patterns:
            permissions[pattern] = "deny"
        for name in (".env", ".env.local"):
            absolute = (self.project_root / name).resolve()
            permissions[str(absolute)] = "deny"
            permissions[absolute.as_posix()] = "deny"
        return permissions

    def _attachment_permissions(self, attachment_paths: list[Path]) -> dict[str, str]:
        permissions = {"*": "deny"}
        for path in attachment_paths:
            absolute = path.resolve()
            patterns = [str(absolute), absolute.as_posix()]
            try:
                relative = absolute.relative_to(self.project_root)
            except ValueError:
                pass
            else:
                patterns.extend([str(relative), relative.as_posix()])
            for pattern in patterns:
                permissions[pattern] = "allow"
        return permissions

    def _fallback(
        self,
        request: DirectionPlanRequest,
        *,
        reason: str,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        fallback_path = request.output_dir / "opencode_main_fallback.json"
        fallback_path.write_text(
            json_dumps({"reason": reason}),
            encoding="utf-8",
        )
        plan = self.fallback.plan_direction(request)
        plan = {key: value for key, value in plan.items() if key != "artifact_path"}
        if selection:
            for key in (
                "direction_id",
                "method_family",
                "method_families",
                "primary_search_pressure",
                "knowledge_query",
            ):
                if selection.get(key) not in (None, "", []):
                    plan[key] = selection[key]
            plan["selection_preserved_after_planning_fallback"] = True
        context = load_context_dict(request.context_packet_path)
        task = context.get("task") if isinstance(context.get("task"), dict) else {}
        original_catalog = (
            context.get("method_package_catalog")
            if isinstance(context.get("method_package_catalog"), dict)
            else {}
        )
        tournament = (
            request.round_index >= 0
            and str(plan.get("experiment_stage") or "") == "research_tournament"
        )
        if tournament:
            plan = bind_fallback_tournament_variants(
                context=context,
                plan=plan,
                loop_feedback=request.loop_feedback,
            )
            # The round-level plan represents multiple independently bound packages.
            plan["method_package_id"] = ""
        else:
            package_catalog = method_package_catalog(
                problem_family=str(task.get("problem_family") or ""),
                active_features=[str(item) for item in original_catalog.get("active_features") or []],
                knowledge_query_tags=method_package_query_tags(
                    knowledge_query=plan.get("knowledge_query"),
                    method_family=str(plan.get("method_family") or ""),
                    active_features=original_catalog.get("active_features"),
                ),
            )
            package_id, package_selection_source = resolve_fast_method_package(
                catalog=package_catalog,
                method_family=str(plan.get("method_family") or ""),
                loop_feedback=request.loop_feedback,
            )
            plan["method_package_id"] = package_id
            package_context = dict(context)
            package_context["method_package_catalog"] = package_catalog
            plan = bind_direction_plan_to_method_catalog(plan, context=package_context)
            plan["method_package_selection"]["selection_source"] = package_selection_source
            plan["method_package_selection"]["selection_policy"] = "fallback_harness_resolution"
        if request.round_index >= 0:
            competition = (
                request.loop_feedback.get("competition")
                if isinstance(request.loop_feedback.get("competition"), dict)
                else {}
            )
            max_workers = max(1, min(4, int(competition.get("max_competing_workers") or 1)))
            plan = configure_exact_probe_tournament(
                plan,
                context=context,
                max_workers=max_workers,
            )
            exact_probe = (
                isinstance(plan.get("exact_probe_policy"), dict)
                and bool(plan["exact_probe_policy"].get("reserved"))
            )
            family_tournament = len(
                {
                    str(item.get("method_family") or "").strip()
                    for item in plan.get("candidate_variants") or []
                    if isinstance(item, dict) and str(item.get("method_family") or "").strip()
                }
            ) >= 2
            plan["worker_lane_policy"] = {
                "schema_version": 1,
                "mechanism_selection": (
                    "family_hypothesis_tournament"
                    if tournament
                    else "exact_probe_tournament"
                    if exact_probe
                    else "family_hypothesis_tournament"
                    if family_tournament
                    else "delegated_to_worker"
                ),
                "lane_count": max_workers,
            }
            if not tournament and not family_tournament:
                plan["candidate_variants"] = []
            plan["worker_lane_policy"]["method_names"] = candidate_method_names(
                plan,
                limit=max_workers,
            )
        plan = ensure_direction_activation_contracts(plan)
        plan["planning_contract_status"] = fallback_planning_contract_status(
            plan,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        plan["planner_fallback"] = {
            "source": type(self).__name__,
            "fallback": type(self.fallback).__name__,
            "reason": reason[:1_000],
            "fallback_path": str(fallback_path.resolve()),
        }
        return write_direction_plan(request.output_dir, plan)


def bind_fallback_tournament_variants(
    *,
    context: dict[str, Any],
    plan: dict[str, Any],
    loop_feedback: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one independent Method Package contract per fallback family hypothesis."""

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    original_catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    variants = []
    for raw in plan.get("candidate_variants") or []:
        if not isinstance(raw, dict):
            continue
        variant = dict(raw)
        family = str(variant.get("method_family") or "")
        catalog = method_package_catalog(
            problem_family=str(task.get("problem_family") or ""),
            active_features=[str(item) for item in original_catalog.get("active_features") or []],
            knowledge_query_tags=method_package_query_tags(
                knowledge_query=variant.get("knowledge_query"),
                method_family=family,
                active_features=original_catalog.get("active_features"),
            ),
        )
        package_id, source = resolve_fast_method_package(
            catalog=catalog,
            method_family=family,
            loop_feedback=loop_feedback,
        )
        variant["method_package_id"] = package_id
        package_context = dict(context)
        package_context["method_package_catalog"] = catalog
        bound = bind_direction_plan_to_method_catalog(
            {**plan, **variant, "candidate_variants": []},
            context=package_context,
        )
        for field in (
            "method_package_id",
            "implementation_order",
            "deliverables",
            "knowledge_paths",
            "implementation_bundle",
            "method_package_selection",
            "acceptance_checks",
            "checkpoint_checks",
        ):
            if bound.get(field) not in (None, "", [], {}):
                variant[field] = bound[field]
        variant["method_package_selection"] = {
            **(variant.get("method_package_selection") or {}),
            "selection_source": source,
            "selection_policy": "fallback_family_tournament_resolution",
        }
        variants.append(
            ensure_method_family_activation_contract(
                variant,
                active_features=original_catalog.get("active_features") or [],
            )
        )
    result = dict(plan)
    result["candidate_variants"] = variants
    result["tournament_contract"] = {
        "selection_status": "pending_core_evidence",
        "winner_selected_by_harness": False,
        "family_hypothesis_count": len(variants),
    }
    return result


def stream_process_pipe(stream: Any, path: Path, chunks: list[str]) -> None:
    """Tee one OpenCode pipe to disk so the Web monitor can render it live."""

    with path.open("w", encoding="utf-8") as handle:
        while True:
            chunk = stream.readline()
            if not chunk:
                return
            chunks.append(chunk)
            handle.write(chunk)
            handle.flush()


def monitor_process_stall(
    process: subprocess.Popen[str],
    stdout_chunks: list[str],
    stderr_chunks: list[str],
    stall_timeout_seconds: int,
    stop_event: threading.Event,
    state: dict[str, bool],
) -> None:
    """Terminate only a silent OpenCode call; active research has no total deadline."""

    observed_size = (len(stdout_chunks), len(stderr_chunks))
    last_activity = time.monotonic()
    while not stop_event.wait(MAIN_STALL_POLL_SECONDS):
        if process.poll() is not None:
            return
        current_size = (len(stdout_chunks), len(stderr_chunks))
        if current_size != observed_size:
            observed_size = current_size
            last_activity = time.monotonic()
            continue
        if time.monotonic() - last_activity < stall_timeout_seconds:
            continue
        state["stalled"] = True
        kill_process_tree(process)
        return


def build_round_reflection_evidence(request: RoundReflectionRequest) -> dict[str, Any]:
    """Keep post-round causal evidence compact but explicit for Main reflection."""

    direction = request.direction_plan if isinstance(request.direction_plan, dict) else {}
    competition = request.competition_result if isinstance(request.competition_result, dict) else {}
    payload = {
        "schema_version": 1,
        "round_index": request.round_index,
        "direction_plan": compact_json(
            {
                "direction_id": direction.get("direction_id"),
                "title": direction.get("title"),
                "hypothesis": direction.get("hypothesis"),
                "strategy_type": direction.get("strategy_type"),
                "method_family": direction.get("method_family"),
                "method_families": direction.get("method_families") or [],
                "experiment_stage": direction.get("experiment_stage"),
                "selection_rationale": direction.get("selection_rationale"),
                "change_scope": direction.get("change_scope") or [],
                "next_mutation": direction.get("next_mutation") or {},
                "activation_checks": direction.get("activation_checks") or [],
                "candidate_variants": direction.get("candidate_variants") or [],
                "selected_candidate_variant": direction.get("selected_candidate_variant") or {},
            },
            max_chars=18_000,
        ).payload,
        "competition_result": compact_round_competition_result(
            competition,
            direction=direction,
        ),
        "promotion_check": compact_json(
            request.promotion_check if isinstance(request.promotion_check, dict) else {},
            max_chars=8_000,
        ).payload,
        "incumbent_transition": {
            "incumbent_key_before": list(request.incumbent_key_before),
            "incumbent_key_after": list(request.incumbent_key_after),
            "promoted": bool((request.promotion_check or {}).get("promoted")),
            "selected_candidate_id": competition.get("selected_candidate_id"),
            "measured_candidate_id": competition.get("measured_candidate_id"),
            "best_legal_candidate": planning_packets.project_observed_candidate(
                competition.get("best_legal_candidate")
            ),
            "best_activated_candidate": planning_packets.project_observed_candidate(
                competition.get("best_activated_candidate")
            ),
        },
        "reflection_contract": {
            "allowed_hypothesis_outcomes": [
                "supported",
                "refuted",
                "mixed",
                "inconclusive",
                "inconclusive_not_exercised",
            ],
            "allowed_next_actions": ["probe", "scale", "pivot", "research_tournament"],
            "activation_checks_rule": "Use activation checks to prove execution, not result quality.",
            "research_tournament_scope": "May cross method families when local continuation is no longer evidence-backed.",
        },
    }
    compacted = compact_json(payload, max_chars=PLANNING_PACKET_MAX_CHARS)
    return json.loads(compacted.text)

# Keep these public names stable while the packet compiler lives in the context
# layer. Existing integrations and tests import them from this adapter.
build_planning_packet = planning_packets.build_planning_packet
build_implementation_planning_packet = planning_packets.build_implementation_planning_packet
compact_round_competition_result = planning_packets.compact_round_competition_result


def build_fast_planning_packet(planning_packet: dict[str, Any]) -> dict[str, Any]:
    """Keep only decision-critical evidence for the single-call planner."""

    payload = {
        "schema_version": 1,
        "planning_stage": "fast_direction_plan",
        "direction_id": planning_packet.get("direction_id"),
        "task_digest": _bounded_fast_value(planning_packet.get("task_digest")),
        "instance_diagnostics": _bounded_fast_value(planning_packet.get("instance_diagnostics")),
        "research_state": _bounded_fast_value(planning_packet.get("research_state")),
        "incumbent_evidence": _bounded_fast_value(planning_packet.get("incumbent_evidence")),
        "incumbent_capability_audit": _fast_incumbent_audit(
            planning_packet.get("incumbent_capability_audit")
        ),
        "recent_round_evidence": _bounded_fast_value(
            planning_packet.get("recent_round_evidence"), max_list=3
        ),
        "latest_evidence": _bounded_fast_value(planning_packet.get("latest_evidence")),
        "latest_attempt_evidence": _bounded_fast_value(
            planning_packet.get("latest_attempt_evidence")
        ),
        "historical_aggregates": _bounded_fast_value(planning_packet.get("historical_aggregates")),
        "next_round_guidance": _bounded_fast_value(planning_packet.get("next_round_guidance")),
        "user_intervention": _bounded_fast_value(planning_packet.get("user_intervention")),
        "strategy_selection_cards": _bounded_fast_value(
            planning_packet.get("strategy_selection_cards"), max_list=4, max_string=300
        ),
        "knowledge_query_catalog": _bounded_fast_value(
            planning_packet.get("knowledge_query_catalog"), max_list=12
        ),
        "method_family_catalog": _bounded_fast_value(
            planning_packet.get("method_family_catalog"), max_list=8
        ),
        "runtime_limits": _bounded_fast_value(planning_packet.get("runtime_limits")),
        "planner_output_contract": {
            "top_level_keys": ["direction_selection", "direction_brief"],
            "direction_selection_required_fields": [
                "method_family",
                "knowledge_query",
                "diagnosis",
                "selection_rationale",
            ],
            "direction_brief_required_fields": [
                "title",
                "strategy_type",
                "hypothesis",
                "rationale_summary",
                "focus_paths",
                "focus_symbols",
                "effort",
                "expected_impact",
                "risk",
                "change_scope",
                "preserve",
                "avoid",
            ],
            "forbidden_fields": [
                "worker_assignment",
                "candidate_variants",
                "activation_checks",
                "deliverables",
                "implementation_order",
                "next_mutation",
            ],
            "mechanism_selection": "delegated_to_worker_after_skill_loading",
            "high_flexibility_policy": (
                "Instance flexibility is evidence for Main's family judgment, not a hard route to a Skill."
            ),
        },
    }
    compacted = compact_json(payload, max_chars=FAST_PLANNING_PACKET_MAX_CHARS)
    result = compacted.payload if isinstance(compacted.payload, dict) else payload
    if compacted.profile == "root_fallback":
        result = _fast_packet_fallback(payload)
        compacted = compact_json(result, max_chars=FAST_PLANNING_PACKET_MAX_CHARS)
        result = compacted.payload if isinstance(compacted.payload, dict) else result
    result["packet_budget"] = {
        "max_chars": FAST_PLANNING_PACKET_MAX_CHARS,
        "original_chars": compacted.original_chars,
        "compacted": compacted.compacted,
        "profile": compacted.profile,
    }
    return result


def _bounded_fast_value(
    value: Any,
    *,
    max_string: int = 500,
    max_list: int = 6,
    max_dict: int = 30,
    max_depth: int = 5,
    _depth: int = 0,
) -> Any:
    """Project planner evidence before generic compaction can discard root contracts."""

    if _depth >= max_depth:
        if isinstance(value, dict):
            return {"_summary": "dict", "keys": [str(key) for key in list(value)[:8]]}
        if isinstance(value, list):
            return {"_summary": "list", "item_count": len(value)}
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "..."
    if isinstance(value, list):
        return [
            _bounded_fast_value(
                item,
                max_string=max_string,
                max_list=max_list,
                max_dict=max_dict,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for item in value[:max_list]
        ]
    if isinstance(value, dict):
        return {
            str(key): _bounded_fast_value(
                item,
                max_string=max_string,
                max_list=max_list,
                max_dict=max_dict,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for key, item in list(value.items())[:max_dict]
        }
    return value


def _fast_incumbent_audit(value: Any) -> dict[str, Any]:
    """Keep search controls and limits; omit full symbol and call-graph inventories."""

    audit = value if isinstance(value, dict) else {}
    files: list[dict[str, Any]] = []
    for raw in audit.get("files") or []:
        if not isinstance(raw, dict):
            continue
        files.append(
            {
                key: raw.get(key)
                for key in (
                    "relative_path",
                    "path",
                    "sha256",
                    "line_count",
                    "parse_status",
                    "entrypoints",
                    "has_main_guard",
                )
                if key in raw
            }
            | {
                "configurations": _bounded_fast_value(raw.get("configurations"), max_list=12),
                "loops": _bounded_fast_value(raw.get("loops"), max_list=8),
                "function_names": [
                    str(item.get("name") or item.get("qualified_name") or "")[:160]
                    for item in raw.get("functions") or []
                    if isinstance(item, dict) and (item.get("name") or item.get("qualified_name"))
                ][:12],
            }
        )
    return {
        key: _bounded_fast_value(audit.get(key), max_list=8)
        for key in (
            "schema_version",
            "source",
            "summary",
            "capabilities",
            "limits",
            "limitations",
            "interpretation_rules",
        )
        if key in audit
    } | {"files": files[:4]}


def _fast_packet_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve every Fast planning contract even under the hard packet limit."""

    protected = (
        "schema_version",
        "planning_stage",
        "direction_id",
        "task_digest",
        "instance_diagnostics",
        "research_state",
        "incumbent_evidence",
        "incumbent_capability_audit",
        "recent_round_evidence",
        "latest_evidence",
        "latest_attempt_evidence",
        "historical_aggregates",
        "next_round_guidance",
        "user_intervention",
        "strategy_selection_cards",
        "knowledge_query_catalog",
        "method_family_catalog",
        "runtime_limits",
        "planner_output_contract",
    )
    return {
        key: _bounded_fast_value(
            payload.get(key),
            max_string=220,
            max_list=3,
            max_dict=16,
            max_depth=4,
        )
        for key in protected
    } | {"_compacted": {"mode": "fast_contract_fallback"}}


def normalize_fast_direction_brief(
    value: dict[str, Any],
    *,
    selection: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    """Keep fast Main output coarse and discard candidate-level implementation detail."""

    hypothesis = str(value.get("hypothesis") or selection.get("diagnosis") or "")[:1200]
    rationale = str(
        value.get("rationale_summary")
        or selection.get("selection_rationale")
        or selection.get("diagnosis")
        or ""
    )[:1600]
    return {
        "title": str(value.get("title") or hypothesis or f"Direction {round_index}")[:200],
        "strategy_type": str(value.get("strategy_type") or "worker_selected_mechanism")[:80],
        "hypothesis": hypothesis or "在所选方法族内由 Worker 选择一个有界机制并交由 Core 验证。",
        "rationale_summary": rationale,
        "focus_paths": _bounded_string_list(value.get("focus_paths"), limit=8, chars=300),
        "focus_symbols": _bounded_string_list(value.get("focus_symbols"), limit=12, chars=200),
        "effort": str(value.get("effort") or "standard")[:40],
        "expected_impact": str(value.get("expected_impact") or "")[:800],
        "risk": str(value.get("risk") or "")[:800],
        "change_scope": _bounded_string_list(value.get("change_scope"), limit=8, chars=500),
        "preserve": _bounded_string_list(value.get("preserve"), limit=10, chars=500),
        "avoid": _bounded_string_list(value.get("avoid"), limit=10, chars=500),
    }


def has_model_text(events_text: str) -> bool:
    """Distinguish malformed model output from a timeout containing only tool events."""

    for line in events_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return True
        if isinstance(event, dict) and event.get("type") == "text" and _text_values(event):
            return True
    return False


def bounded_invalid_response(events_text: str, max_chars: int = 12_000) -> str:
    """Attach only malformed model text to the format retry, not the full event trace."""

    texts: list[str] = []
    for line in events_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                texts.append(line.strip())
            continue
        if isinstance(event, dict) and event.get("type") == "text":
            texts.extend(_text_values(event))
    rendered = "\n".join(texts).strip() or events_text[-max_chars:]
    return rendered[-max_chars:]


def extract_planned_direction(events_text: str) -> dict[str, Any] | None:
    """Extract the last valid PlannedDirection object from OpenCode JSON events."""

    candidates: list[str] = []
    for line in events_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            candidates.append(line)
            continue
        candidates.extend(_text_values(event))
        if isinstance(event, dict) and (
            "direction_plan" in event or "direction_brief" in event
        ):
            candidates.append(json.dumps(event, ensure_ascii=False))
    for candidate in reversed(candidates):
        parsed = _json_object_from_text(candidate)
        if isinstance(parsed, dict) and (
            isinstance(parsed.get("direction_plan"), dict)
            or (
                isinstance(parsed.get("direction_selection"), dict)
                and isinstance(parsed.get("direction_brief"), dict)
            )
        ):
            return parsed
    return None


def normalize_generic_method_variants(value: Any, *, expected: int) -> list[dict[str, Any]]:
    """Validate generic Main method lanes without mapping them to domain catalogs."""

    result: list[dict[str, Any]] = []
    seen_methods: set[str] = set()
    for index, item in enumerate(value or []):
        if not isinstance(item, dict):
            continue
        method_name = str(item.get("method_name") or "").strip()[:120]
        method_key = re.sub(r"[^a-z0-9]+", "", method_name.lower())
        hypothesis = str(item.get("hypothesis") or "").strip()[:1_200]
        objective = str(item.get("worker_objective") or "").strip()[:1_200]
        strategy_type = str(item.get("strategy_type") or "").strip()[:120]
        if not method_key or method_key in seen_methods or not hypothesis or not objective or not strategy_type:
            continue
        seen_methods.add(method_key)
        candidate_id = re.sub(
            r"[^a-z0-9_-]+",
            "-",
            str(item.get("candidate_id") or f"method-{index + 1:02d}").strip().lower(),
        ).strip("-")[:48] or f"method-{index + 1:02d}"
        result.append(
            {
                "candidate_id": candidate_id,
                "title": method_name,
                "method_name": method_name,
                "hypothesis": hypothesis,
                "worker_objective": objective,
                "strategy_type": strategy_type,
                "change_scope": _bounded_string_list(item.get("change_scope"), limit=8, chars=500),
                "preserve": _bounded_string_list(item.get("preserve"), limit=8, chars=500),
                "avoid": _bounded_string_list(item.get("avoid"), limit=8, chars=500),
                "activation_contract_version": 0,
                "activation_checks": [],
            }
        )
        if len(result) >= max(1, min(4, expected)):
            break
    return result


def extract_direction_selection(events_text: str) -> dict[str, Any] | None:
    """Extract the first-stage direction selection from OpenCode JSON events."""

    candidates: list[str] = []
    for line in events_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            candidates.append(line)
            continue
        candidates.extend(_text_values(event))
        if isinstance(event, dict) and "direction_selection" in event:
            candidates.append(json.dumps(event, ensure_ascii=False))
    for candidate in reversed(candidates):
        parsed = _json_object_from_text(candidate)
        if isinstance(parsed, dict) and isinstance(parsed.get("direction_selection"), dict):
            return parsed
        if isinstance(parsed, dict) and str(parsed.get("planning_stage") or "") == "direction_selection":
            return parsed
    return None


def extract_round_reflection(events_text: str) -> dict[str, Any] | None:
    """Extract the post-round reflection JSON from OpenCode events."""

    candidates: list[str] = []
    for line in events_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            candidates.append(line)
            continue
        candidates.extend(_text_values(event))
        if isinstance(event, dict) and (
            "hypothesis_outcome" in event
            or "round_reflection" in event
            or "next_action" in event
        ):
            candidates.append(json.dumps(event, ensure_ascii=False))
    for candidate in reversed(candidates):
        parsed = _json_object_from_text(candidate)
        if not isinstance(parsed, dict):
            continue
        nested = parsed.get("round_reflection") if isinstance(parsed.get("round_reflection"), dict) else None
        payload = nested or parsed
        if isinstance(payload, dict) and (
            payload.get("hypothesis_outcome") is not None
            or isinstance(payload.get("next_action"), dict)
        ):
            return payload
    return None


def resolve_optional_timeout_seconds(
    explicit_value: int | None,
    *,
    env_var: str,
    minimum: int,
) -> int | None:
    if explicit_value is not None:
        parsed_value = int(explicit_value)
        return max(minimum, parsed_value) if parsed_value > 0 else None
    raw_value = os.environ.get(env_var)
    if raw_value is None or not str(raw_value).strip():
        return None
    parsed_value = int(raw_value)
    return max(minimum, parsed_value) if parsed_value > 0 else None


def bounded_timeout_seconds(timeout_seconds: int | None, upper_bound: int) -> int | None:
    if timeout_seconds is None:
        return upper_bound
    return min(timeout_seconds, upper_bound)


def resolve_fast_method_package(
    *,
    catalog: dict[str, Any],
    method_family: str,
    loop_feedback: dict[str, Any],
) -> tuple[str, str]:
    """Bind Fast planning to one compatible package before Worker context is built."""

    available = {
        str(item.get("package_id") or "").strip()
        for item in catalog.get("packages") or []
        if isinstance(item, dict) and str(item.get("package_id") or "").strip()
    }
    if not available:
        return "", "no_compatible_package"

    prior_plans: list[dict[str, Any]] = []
    current = loop_feedback.get("current_direction_plan")
    if isinstance(current, dict):
        prior_plans.append(current)
    for record in reversed(loop_feedback.get("previous_rounds") or []):
        if not isinstance(record, dict):
            continue
        direction = record.get("direction_plan")
        if isinstance(direction, dict):
            prior_plans.append(direction)

    for prior in prior_plans:
        prior_families = prior.get("method_families") or [prior.get("method_family")]
        family_ids = {
            str(item.get("id") or "").strip() if isinstance(item, dict) else str(item or "").strip()
            for item in prior_families
        }
        package_id = str(prior.get("method_package_id") or "").strip()
        if method_family in family_ids and package_id in available:
            return package_id, "same_direction_inheritance"

    recommended = str(catalog.get("recommended_package_id") or "").strip()
    if recommended in available:
        return recommended, "catalog_recommendation"
    if len(available) == 1:
        return next(iter(available)), "sole_compatible_package"
    return "", "ambiguous_compatible_packages"


def inherited_direction_selection(
    *,
    planning_packet: dict[str, Any],
    round_index: int,
) -> dict[str, Any] | None:
    """Reuse the active family for probe/scale transitions without another tournament."""

    state = (
        planning_packet.get("research_state")
        if isinstance(planning_packet.get("research_state"), dict)
        else {}
    )
    if round_index < 0 or state.get("selection_required") is not False:
        return None
    raw = {
        "planning_stage": "direction_selection",
        "direction_id": f"d{round_index:03d}",
        "method_family": state.get("active_method_family"),
        "method_families": state.get("active_method_families") or [],
        "primary_search_pressure": state.get("active_primary_search_pressure") or "",
        "diagnosis": state.get("next_action_rationale") or state.get("active_hypothesis") or "",
        "measured_evidence": [
            f"last_decision={state.get('last_decision')}",
            f"last_hypothesis_outcome={state.get('last_hypothesis_outcome')}",
            f"next_action={state.get('next_action')}",
        ],
        "selection_rationale": (
            "研究状态机要求 probe/scale 继承当前方法族；本轮只重新规划该方向内的下一次可证伪变异。"
        ),
        "knowledge_query": state.get("active_knowledge_query") or [],
    }
    return normalize_direction_selection(
        raw,
        planning_packet=planning_packet,
        round_index=round_index,
    )


def normalize_direction_selection(
    value: dict[str, Any] | None,
    *,
    planning_packet: dict[str, Any],
    round_index: int,
) -> dict[str, Any] | None:
    """Validate first-stage output against the Domain Pack query vocabulary."""

    raw = value.get("direction_selection") if isinstance(value, dict) else None
    if not isinstance(raw, dict) and isinstance(value, dict):
        if str(value.get("planning_stage") or "") == "direction_selection":
            raw = value
    if not isinstance(raw, dict):
        return None
    query_catalog = (
        planning_packet.get("knowledge_query_catalog")
        if isinstance(planning_packet.get("knowledge_query_catalog"), dict)
        else {}
    )
    allowed = {
        str(item.get("tag") or "").strip().lower()
        for item in query_catalog.get("tags") or []
        if isinstance(item, dict) and str(item.get("tag") or "").strip()
    }
    family_catalog = (
        planning_packet.get("method_family_catalog")
        if isinstance(planning_packet.get("method_family_catalog"), dict)
        else {}
    )
    allowed_families = {
        str(item.get("family_id") or "").strip().lower(): item
        for item in family_catalog.get("families") or []
        if isinstance(item, dict) and str(item.get("family_id") or "").strip()
    }
    raw_families = raw.get("method_families")
    if not isinstance(raw_families, list):
        raw_families = [raw.get("method_family") or raw.get("selected_method_family")]
    method_families: list[dict[str, str]] = []
    for item in raw_families:
        family_id = _selection_value(item).strip().lower()[:80]
        if not family_id or any(row["id"] == family_id for row in method_families):
            continue
        if allowed_families and family_id not in allowed_families:
            continue
        family = allowed_families.get(family_id) or {}
        incompatible = {
            str(value).strip().lower()
            for value in family.get("incompatible_with") or []
            if str(value).strip()
        }
        if any(
            row["id"] in incompatible
            or family_id
            in {
                str(value).strip().lower()
                for value in (allowed_families.get(row["id"]) or {}).get("incompatible_with") or []
                if str(value).strip()
            }
            for row in method_families
        ):
            continue
        method_families.append(
            {
                "id": family_id,
                "role": "primary" if not method_families else "complementary",
            }
        )
        if len(method_families) >= max(1, min(4, int(family_catalog.get("max_selected") or 4))):
            break
    family_query_tags = {
        str(tag).strip().lower()
        for row in method_families
        for tag in (allowed_families.get(row["id"]) or {}).get("query_tags") or []
        if str(tag).strip()
    }
    query: list[str] = []
    for item in raw.get("knowledge_query") or []:
        tag = str(item).strip().lower()
        if (
            tag
            and tag in allowed
            and (not family_query_tags or tag in family_query_tags)
            and tag not in query
        ):
            query.append(tag)
    max_query = max(1, int(query_catalog.get("default_limit") or 6))
    query = query[:max_query]
    normalization_repairs: list[dict[str, str]] = []
    if method_families:
        preferred_query = high_flexibility_query_tags(
            planning_packet.get("instance_diagnostics"),
            compatible_tags=[tag for tag in family_query_tags if tag in allowed],
            limit=max_query,
        )
        merged_query = list(dict.fromkeys([*preferred_query, *query]))[:max_query]
        if preferred_query and merged_query != query:
            query = merged_query
            normalization_repairs.append(
                {
                    "path": "/knowledge_query",
                    "reason": "the parsed instance profile indicates a high-flexibility route",
                    "repair": "prioritized canonical high-flexibility query tags",
                }
            )
        for row in method_families:
            if query:
                break
            family = allowed_families.get(row["id"]) or {}
            fallback_tag = next(
                (
                    str(item).strip().lower()
                    for item in family.get("query_tags") or []
                    if str(item).strip().lower() in allowed
                ),
                "",
            )
            if fallback_tag:
                query = [fallback_tag]
                normalization_repairs.append(
                    {
                        "path": "/knowledge_query",
                        "reason": "no selected query tag was compatible with the preserved method families",
                        "repair": f"selected canonical family tag {fallback_tag}",
                    }
                )
                break
    if not method_families or not query:
        return None
    method_family = method_families[0]["id"]
    return {
        "schema_version": 1,
        "direction_id": str(raw.get("direction_id") or f"d{round_index:03d}")[:80],
        "method_family": method_family,
        "method_families": method_families,
        "primary_search_pressure": _selection_value(
            raw.get("primary_search_pressure") or raw.get("selected_pressure")
        )[:80],
        "diagnosis": str(raw.get("diagnosis") or "")[:1600],
        "measured_evidence": _bounded_string_list(raw.get("measured_evidence"), limit=8, chars=500),
        "reasoning_trace": normalize_public_reasoning_trace(raw.get("reasoning_trace"), limit=6),
        "incumbent_assessment": normalize_incumbent_assessment(raw.get("incumbent_assessment")),
        "uncertainties": _bounded_string_list(raw.get("uncertainties"), limit=6, chars=500),
        "alternatives_considered": _bounded_string_list(
            raw.get("alternatives_considered"),
            limit=6,
            chars=500,
        ),
        "selection_rationale": str(raw.get("selection_rationale") or "")[:1600],
        "knowledge_query": query,
        "method_package_id": "",
        "normalization_repairs": normalization_repairs,
    }


def _selection_value(value: Any) -> str:
    """Normalize Main's string or ``{id, label}`` direction selector."""

    if isinstance(value, dict):
        value = value.get("id") or value.get("label") or ""
    return str(value or "").strip()


def incumbent_planning_contract_errors(
    value: dict[str, Any] | None,
    *,
    planning_packet: dict[str, Any],
    round_index: int,
) -> list[str]:
    """Require evidence-backed incumbent diagnosis when a static audit is available."""

    if round_index < 0 or not planning_packet.get("incumbent_capability_audit"):
        return []
    direction = value.get("direction_plan") if isinstance(value, dict) else None
    if not isinstance(direction, dict):
        return ["direction_plan is missing"]
    assessment = direction.get("incumbent_assessment")
    mutation = direction.get("next_mutation")
    reasoning_trace = normalize_public_reasoning_trace(direction.get("reasoning_trace"))
    errors: list[str] = []
    if not isinstance(assessment, dict):
        errors.append("direction_plan.incumbent_assessment is missing")
    else:
        for key in (
            "verified_capabilities",
            "implementation_limits",
            "bottleneck_hypotheses",
            "evidence_refs",
        ):
            if not _bounded_string_list(assessment.get(key), limit=1, chars=500):
                errors.append(f"direction_plan.incumbent_assessment.{key} is empty")
    if not isinstance(mutation, dict):
        errors.append("direction_plan.next_mutation is missing")
    else:
        if not _bounded_string_list(mutation.get("target_symbols"), limit=1, chars=500):
            errors.append("direction_plan.next_mutation.target_symbols is empty")
        if not str(mutation.get("change") or "").strip():
            errors.append("direction_plan.next_mutation.change is empty")
        if not str(mutation.get("expected_effect") or "").strip():
            errors.append("direction_plan.next_mutation.expected_effect is empty")
        if not _bounded_string_list(mutation.get("falsification_metrics"), limit=1, chars=500):
            errors.append("direction_plan.next_mutation.falsification_metrics is empty")
    if len(reasoning_trace) < 3:
        errors.append("direction_plan.reasoning_trace must contain at least three public evidence-reasoning steps")
    for index, row in enumerate(reasoning_trace):
        if not row.get("evidence"):
            errors.append(f"direction_plan.reasoning_trace[{index}].evidence is empty")
        if not row.get("inference"):
            errors.append(f"direction_plan.reasoning_trace[{index}].inference is empty")
        if not row.get("decision") and not row.get("next_check"):
            errors.append(f"direction_plan.reasoning_trace[{index}] lacks decision and next_check")
    if round_index >= 0:
        if not normalize_activation_checks(direction.get("activation_checks")):
            errors.append("direction_plan.activation_checks is empty or not machine-checkable")
        else:
            errors.extend(
                activation_check_schema_errors(
                    direction.get("activation_checks"),
                    field_name="direction_plan.activation_checks",
                )
            )
        output_contract = (
            planning_packet.get("planner_output_contract")
            if isinstance(planning_packet.get("planner_output_contract"), dict)
            else {}
        )
        competition_policy = (
            output_contract.get("competition_policy")
            if isinstance(output_contract.get("competition_policy"), dict)
            else {}
        )
        try:
            minimum_candidates = int(competition_policy.get("minimum_candidate_variants") or 0)
            maximum_candidates = int(competition_policy.get("maximum_candidate_variants") or 4)
        except (TypeError, ValueError):
            minimum_candidates, maximum_candidates = 0, 4
        minimum_candidates = max(0, min(4, minimum_candidates))
        maximum_candidates = max(1, min(4, maximum_candidates))
        raw_variants = [
            item for item in direction.get("candidate_variants") or [] if isinstance(item, dict)
        ]
        if len(raw_variants) > maximum_candidates:
            errors.append(
                "direction_plan.candidate_variants exceeds "
                "planner_output_contract.competition_policy.maximum_candidate_variants"
            )
        variants = raw_variants[:maximum_candidates]
        if len(variants) < minimum_candidates:
            errors.append(
                "direction_plan.candidate_variants contains fewer experiments than "
                "planner_output_contract.competition_policy.minimum_candidate_variants"
            )
        signatures: set[str] = set()
        for index, variant in enumerate(variants):
            if not str(variant.get("hypothesis") or "").strip():
                errors.append(f"direction_plan.candidate_variants[{index}].hypothesis is empty")
            if not str(variant.get("strategy_type") or "").strip():
                errors.append(f"direction_plan.candidate_variants[{index}].strategy_type is empty")
            if not normalize_activation_checks(variant.get("activation_checks")):
                errors.append(
                    f"direction_plan.candidate_variants[{index}].activation_checks is empty or not machine-checkable"
                )
            else:
                errors.extend(
                    activation_check_schema_errors(
                        variant.get("activation_checks"),
                        field_name=f"direction_plan.candidate_variants[{index}].activation_checks",
                    )
                )
            mutation = variant.get("next_mutation") if isinstance(variant.get("next_mutation"), dict) else {}
            target_symbols = sorted(
                str(item).strip().lower()
                for item in mutation.get("target_symbols") or []
                if str(item).strip()
            )
            change = str(mutation.get("change") or "").strip().lower()
            if not target_symbols:
                errors.append(
                    f"direction_plan.candidate_variants[{index}].next_mutation.target_symbols is empty"
                )
            if not change:
                errors.append(
                    f"direction_plan.candidate_variants[{index}].next_mutation.change is empty"
                )
            signature = json.dumps(
                {
                    "method_family": str(variant.get("method_family") or direction.get("method_family") or "")
                    .strip()
                    .lower(),
                    "strategy_type": str(variant.get("strategy_type") or "").strip().lower(),
                    "target_symbols": target_symbols,
                    "change": change,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if signature in signatures:
                errors.append(
                    f"direction_plan.candidate_variants[{index}] duplicates an earlier mechanism"
                )
            signatures.add(signature)
    return errors


def _bounded_string_list(value: Any, *, limit: int, chars: int) -> list[str]:
    values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    result: list[str] = []
    for item in values:
        text = str(item).strip()[:chars]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def merge_worker_handoff_into_direction(value: dict[str, Any]) -> dict[str, Any]:
    direction = dict(value.get("direction_plan") or {})
    handoff = value.get("worker_assignment") if isinstance(value.get("worker_assignment"), dict) else {}
    if handoff.get("objective"):
        direction["worker_objective"] = handoff["objective"]
    for key in ("implementation_order", "deliverables", "preserve", "completion_rule"):
        if handoff.get(key) and not direction.get(key):
            direction[key] = handoff[key]
    if handoff.get("forbidden"):
        direction["avoid"] = [*(direction.get("avoid") or []), *(handoff.get("forbidden") or [])]
    return direction


def summarize_opencode_events(events_text: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line in events_text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    called_subagents: list[str] = []
    usage: dict[str, int | float] = {}
    for event in events:
        rendered = json.dumps(event, ensure_ascii=False)
        if "task" in rendered.lower():
            for name in (
                "requirements-method-analyst",
                "evidence-analyst",
                "plan-critic",
                "candidate-strategy-analyst",
            ):
                if name in rendered and name not in called_subagents:
                    called_subagents.append(name)
        _sum_numeric_usage(event, usage)
    return {
        "event_count": len(events),
        **opencode_event_stream_health(events_text),
        "called_subagents": called_subagents,
        "usage": usage,
        "compaction": summarize_opencode_compaction_events(events_text),
    }


def merge_event_summaries(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, int | float] = dict(first.get("usage") or {})
    for key, value in (second.get("usage") or {}).items():
        usage[key] = usage.get(key, 0) + value
    return {
        "attempts": int(first.get("attempts") or 0) + int(second.get("attempts") or 0),
        "event_count": int(first.get("event_count") or 0) + int(second.get("event_count") or 0),
        "meaningful_event_count": int(first.get("meaningful_event_count") or 0)
        + int(second.get("meaningful_event_count") or 0),
        "event_stream_status": merged_event_stream_status(first, second),
        "called_subagents": list(
            dict.fromkeys([*(first.get("called_subagents") or []), *(second.get("called_subagents") or [])])
        ),
        "usage": usage,
        "compaction": merge_compaction_summaries(
            first.get("compaction"),
            second.get("compaction"),
        ),
    }


def opencode_event_stream_health(events_text: str) -> dict[str, Any]:
    """Separate process startup bytes from evidence that Main actually progressed."""

    event_types: list[str] = []
    meaningful_count = 0
    for line in events_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                meaningful_count += 1
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip().lower()
        if event_type:
            event_types.append(event_type)
        if event_type not in {"", "step_start"}:
            meaningful_count += 1
    if meaningful_count > 0:
        status = "meaningful"
    elif event_types:
        status = "startup_only"
    else:
        status = "zero"
    return {
        "event_stream_status": status,
        "meaningful_event_count": meaningful_count,
    }


def merged_event_stream_status(first: dict[str, Any], second: dict[str, Any]) -> str:
    statuses = {
        str(first.get("event_stream_status") or "zero"),
        str(second.get("event_stream_status") or "zero"),
    }
    if "meaningful" in statuses:
        return "meaningful"
    if "startup_only" in statuses:
        return "startup_only"
    return "zero"


def _write_packet_bundle(
    *,
    output_dir: Path,
    filename: str,
    packet: dict[str, Any],
) -> list[Path]:
    """Persist the bounded root packet plus a pageable section/index bundle."""

    stem = Path(filename).stem
    bundle_paths: list[Path] = []
    for relative_path, text in planning_packets.planning_packet_bundle_files(packet, stem=stem).items():
        path = output_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        bundle_paths.append(path)
    return bundle_paths


def merge_compaction_summaries(first: Any, second: Any) -> dict[str, Any]:
    first = first if isinstance(first, dict) else {}
    second = second if isinstance(second, dict) else {}
    first_counts = first.get("status_counts") if isinstance(first.get("status_counts"), dict) else {}
    second_counts = second.get("status_counts") if isinstance(second.get("status_counts"), dict) else {}
    status_counts = {
        status: int(first_counts.get(status) or first.get(status) or 0)
        + int(second_counts.get(status) or second.get(status) or 0)
        for status in ("started", "completed", "failed")
    }
    events = [
        item
        for item in [*(first.get("events") or []), *(second.get("events") or [])]
        if isinstance(item, dict)
    ]
    return {
        "schema_version": 1,
        "source": "opencode_jsonl_top_level_events",
        "event_count": int(first.get("event_count") or 0) + int(second.get("event_count") or 0),
        **status_counts,
        "unknown_status_count": int(first.get("unknown_status_count") or 0)
        + int(second.get("unknown_status_count") or 0),
        "status_counts": status_counts,
        "events": events[:32],
        "events_truncated": bool(first.get("events_truncated"))
        or bool(second.get("events_truncated"))
        or len(events) > 32,
    }


def prepare_round_reflection_payload(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    findings: list[dict[str, Any]] = []
    for item in payload.get("candidate_findings") or []:
        if not isinstance(item, dict):
            continue
        finding = dict(item)
        evidence = _bounded_string_list(finding.get("evidence"), limit=8, chars=500)
        mechanism_activated = finding.get("mechanism_activated")
        if mechanism_activated is not None and not any(
            text.startswith("mechanism_activated=") for text in evidence
        ):
            evidence.append(f"mechanism_activated={bool(mechanism_activated)}")
        activation_status = str(finding.get("activation_status") or "").strip()
        if activation_status and not any(text.startswith("activation_status=") for text in evidence):
            evidence.append(f"activation_status={activation_status[:120]}")
        finding["evidence"] = evidence
        findings.append(finding)
    if findings:
        payload["candidate_findings"] = findings
    return payload


def _text_values(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "content"} and isinstance(item, str):
                result.append(item)
            else:
                result.extend(_text_values(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_text_values(item))
    return result


def incumbent_source_files(context: dict[str, Any], *, max_files: int = 2) -> list[Path]:
    """Resolve promoted solver files for read-only Main Agent attachments."""

    code_context = (
        context.get("incumbent_code_context")
        if isinstance(context.get("incumbent_code_context"), dict)
        else {}
    )
    result: list[Path] = []
    seen: set[str] = set()
    for item in code_context.get("files") or []:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            root = str(code_context.get("root") or "").strip()
            relative = str(item.get("relative_path") or "").strip()
            if root and relative:
                raw_path = str(Path(root) / relative)
        if not raw_path:
            continue
        path = Path(raw_path).resolve()
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        result.append(path)
        if len(result) >= max_files:
            break
    return result


def normalize_allowed_specialists(
    value: str | list[str] | None,
    *,
    limit: int,
) -> list[str]:
    known = {
        "requirements-method-analyst",
        "evidence-analyst",
        "plan-critic",
        "candidate-strategy-analyst",
    }
    values = [value] if isinstance(value, str) else list(value or [])
    result: list[str] = []
    for item in values:
        name = str(item or "").strip()
        if not name or name not in known or name in result:
            continue
        result.append(name)
        if len(result) >= max(0, min(4, limit)):
            break
    return result


def _json_object_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]) if len(lines) >= 3 else stripped
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _sum_numeric_usage(value: Any, usage: dict[str, int | float]) -> None:
    if isinstance(value, dict):
        tokens = value.get("tokens")
        if isinstance(tokens, dict):
            for source, target in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"),
                ("total", "total_tokens"),
            ):
                _add_usage_number(usage, target, tokens.get(source))
            cache = tokens.get("cache")
            if isinstance(cache, dict):
                _add_usage_number(usage, "cache_read_tokens", cache.get("read"))
                _add_usage_number(usage, "cache_write_tokens", cache.get("write"))
        _add_usage_number(usage, "cost", value.get("cost"))
        for key, item in value.items():
            if key in {"tokens", "cost"}:
                continue
            _sum_numeric_usage(item, usage)
    elif isinstance(value, list):
        for item in value:
            _sum_numeric_usage(item, usage)


def _add_usage_number(
    usage: dict[str, int | float],
    key: str,
    value: Any,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    usage[key] = usage.get(key, 0) + value
