"""OpenCode-backed Main Agent with a bounded, read-only planning view."""

from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any

from harness_agent.agents.incumbent_audit import compact_incumbent_capability_audit
from harness_agent.agents.main import (
    DirectionPlanRequest,
    EvidenceDrivenMainAgent,
    RoundReflectionRequest,
    WorkerAssignmentIssue,
    WorkerAssignmentRequest,
    bind_direction_plan_to_method_catalog,
    deterministic_round_reflection,
    enforce_improvement_direction_contract,
    merge_public_reasoning_traces,
    normalize_direction_plan,
    normalize_incumbent_assessment,
    normalize_public_reasoning_trace,
    normalize_round_reflection,
    write_direction_plan,
    write_round_reflection,
)
from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.context.compaction import compact_json, compact_source_records
from harness_agent.context.knowledge import method_package_catalog
from harness_agent.context.loader import load_context_dict
from harness_agent.context.packet import activate_direction_knowledge_context
from harness_agent.core.cancellation import CancellationToken
from harness_agent.core.runner import (
    CREATE_NEW_PROCESS_GROUP,
    cleanup_process_descendants,
    kill_process_tree,
)
from harness_agent.workers.opencode_worker import (
    DEFAULT_OPENCODE_MODEL,
    json_dumps,
    opencode_subprocess_environment,
)


OPENCODE_MAIN_AGENT = "algoforge-main"
PLANNING_PACKET_MAX_CHARS = 48_000
MAIN_FORMAT_RETRY_TIMEOUT_SECONDS = 90


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
        max_subagents: int | None = None,
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
        configured_subagents = (
            max_subagents
            if max_subagents is not None
            else int(os.environ.get("OPENCODE_MAIN_MAX_SUBAGENTS", "4"))
        )
        self.max_subagents = max(0, min(4, int(configured_subagents)))
        self.cancellation = cancellation
        self.fallback = EvidenceDrivenMainAgent()

    def plan_direction(self, request: DirectionPlanRequest) -> dict[str, Any]:
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        if self.executable_path is None:
            return self._fallback(request, reason="OpenCode executable is unavailable")

        request.output_dir.mkdir(parents=True, exist_ok=True)
        context = load_context_dict(request.context_packet_path)
        incumbent_source_attachments = incumbent_source_files(context)
        planning_packet = build_planning_packet(
            context=context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
        )
        packet_path = request.output_dir / "planning_packet.json"
        packet_path.write_text(json_dumps(planning_packet), encoding="utf-8")
        audit_path: Path | None = None
        if planning_packet.get("incumbent_capability_audit"):
            audit_path = request.output_dir / "incumbent_capability_audit.json"
            audit_path.write_text(
                json_dumps(planning_packet["incumbent_capability_audit"]),
                encoding="utf-8",
            )

        selection_run = self._run_once(
            output_dir=request.output_dir,
            attachments=[packet_path, *incumbent_source_attachments],
            prompt=(
                "这是方向选择阶段。先加载并遵循 experiment-design Skill，用可证伪实验而不是只看最终分数来"
                "组织方向判断。阅读附件 PlanningPacket，只根据任务合同、实例画像、incumbent 证据和"
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
            knowledge_query_tags=selection["knowledge_query"],
        )
        implementation_packet = build_implementation_planning_packet(
            context=implementation_context,
            loop_feedback=request.loop_feedback,
            round_index=request.round_index,
            direction_selection=selection,
        )
        implementation_packet_path = request.output_dir / "implementation_planning_packet.json"
        implementation_packet_path.write_text(json_dumps(implementation_packet), encoding="utf-8")

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
            )

        implementation_run = self._run_once(
            output_dir=request.output_dir,
            attachments=[implementation_packet_path, *incumbent_source_attachments],
            prompt=(
                "这是实现规划阶段。方向已经确定。先加载并遵循 experiment-design Skill，把方向编译成最小、"
                "可证伪且能改变下一步决策的候选实验。阅读附件中的 direction_selection、"
                "active_direction_knowledge 和 eligible_method_packages，选择零个或一个真正匹配的方法包，"
                "知识卡、参考源码、推荐构建顺序和小步实现建议都是 advisory，不得把它们解释成禁止选择完整方法。"
                "证据与预算支持时可以选择完整方法包，也可以裁剪或组合参考机制；选择完整包要求独立适配且满足"
                "完整行为语义，不要求也不鼓励机械照抄参考源码。"
                "分析过程中必须在 commentary 中分步输出中文思考：指出 incumbent 的具体不足和证据，比较"
                "实现方案与保留项，再给出有界变异和证伪计划；不要等到最终 JSON 才一次性复述。"
                "然后输出完整 direction_plan 与 worker_assignment。必须基于 incumbent_capability_audit 指定"
                "现有目标符号、实现限制、下一次有界变异和证伪指标，不得重复实现审计已确认存在的机制。"
                "必须检查 incumbent 源码、获胜 source、规则级 diagnostics 和上轮 patch；证据缺失时应把"
                "补采 telemetry 作为候选变体，而不是用静态猜测替代运行事实。"
                "若 runtime_limits.max_competing_workers 大于 1，必须输出 candidate_variants，数量不超过该上限；"
                "各变体必须采用可区分、可证伪的实现机制，并显式保留 incumbent fallback；只有在"
                " experiment_stage=research_tournament 时才允许跨方法族比较，其余阶段必须保持当前主方向。"
                "主 direction_plan 和每个 candidate_variants 都必须声明 activation_checks，用"
                " telemetry/diagnostics 证明机制已经执行，而不是把质量结果本身当作执行证明。"
                "交付物必须覆盖所选知识要求的耦合组件，"
                "并输出至少三步 reasoning_trace；最终回答只能包含 JSON，不得直接写代码。"
                "所有 JSON 字段名必须严格使用附件 schema 的英文 key，不得翻译字段名；中文只用于字段值。"
                "candidate_variants 必须放在 direction_plan 内，顶层只能包含 direction_plan 和 worker_assignment。"
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
        usage_payload["attempts"] = 2
        if raw is None and not implementation_run["timed_out"] and has_model_text(implementation_run["stdout"]):
            invalid_path = request.output_dir / "main_agent_invalid_response.txt"
            invalid_path.write_text(
                bounded_invalid_response(implementation_run["stdout"]),
                encoding="utf-8",
            )
            retry = self._run_once(
                output_dir=request.output_dir,
                attachments=[implementation_packet_path, invalid_path, *incumbent_source_attachments],
                prompt=(
                    "把上一份实现规划修复为唯一一个合法 JSON 对象，顶层只能包含 direction_plan 和 "
                    "worker_assignment；保持已选方向和 knowledge_query 不变，并补齐 activation_checks。"
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
            usage_payload["attempts"] = 3
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
                    "reasoning_trace，每步使用 stage、summary、evidence、inference、decision、next_check。"
                    "所有 JSON 字段名必须使用英文 schema key，中文只能出现在字段值中；candidate_variants 放在 "
                    "direction_plan 内。顶层只能包含 direction_plan 和 worker_assignment。返回唯一合法 JSON。"
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
            usage_payload["attempts"] = 3
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
            return self._fallback(request, reason="OpenCode Main Agent did not return valid implementation planning JSON")

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
        plan["direction_selection"] = selection
        plan["reasoning_trace"] = merge_public_reasoning_traces(
            selection.get("reasoning_trace"),
            plan.get("reasoning_trace"),
        )
        plan = enforce_improvement_direction_contract(
            plan,
            round_index=request.round_index,
            loop_feedback=request.loop_feedback,
        )
        plan["planner"] = "opencode_main_agent"
        plan["planning_evidence"] = {
            "planning_packet_path": str(packet_path.resolve()),
            "direction_selection_path": str(selection_path.resolve()),
            "implementation_planning_packet_path": str(implementation_packet_path.resolve()),
            "incumbent_capability_audit_path": str(audit_path.resolve()) if audit_path else None,
            "called_subagents": usage_payload.get("called_subagents") or [],
            "event_count": usage_payload.get("event_count", 0),
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
        if self.executable_path is None:
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
        )
        runtime_path.write_text(json_dumps(runtime_config), encoding="utf-8")

        popen_kwargs: dict[str, object] = {
            "cwd": str(self.project_root),
            "env": opencode_subprocess_environment(runtime_config=runtime_config),
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
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        if timed_out and not stderr and timeout is not None:
            stderr = f"OpenCode Main exceeded {timeout} seconds."
            stderr_path.write_text(stderr, encoding="utf-8")
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": str(process.returncode),
            "timed_out": timed_out,
        }

    def _runtime_config(
        self,
        *,
        attachment_paths: list[Path],
        allowed_specialists: list[str] | None = None,
        allowed_specialist: str | None = None,
    ) -> dict[str, Any]:
        allowed_specialists = normalize_allowed_specialists(
            allowed_specialists or allowed_specialist,
            limit=self.max_subagents,
        )
        attachment_permissions = self._attachment_permissions(attachment_paths)
        main_read_permissions = self._main_read_permissions()
        task_permissions = {"*": "deny"}
        for specialist in allowed_specialists:
            task_permissions[specialist] = "allow"
        agent_configs: dict[str, Any] = {
            OPENCODE_MAIN_AGENT: {
                "steps": 12,
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
                    "skill": {
                        "*": "deny",
                        "algoforge-assignment": "allow",
                        "experiment-design": "allow",
                    },
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

    def _fallback(self, request: DirectionPlanRequest, *, reason: str) -> dict[str, Any]:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        (request.output_dir / "opencode_main_fallback.json").write_text(
            json_dumps({"reason": reason}),
            encoding="utf-8",
        )
        return self.fallback.plan_direction(request)


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
        },
        "reflection_contract": {
            "allowed_hypothesis_outcomes": ["supported", "refuted", "inconclusive"],
            "allowed_next_actions": ["probe", "scale", "pivot", "research_tournament"],
            "activation_checks_rule": "Use activation checks to prove execution, not result quality.",
            "research_tournament_scope": "May cross method families when local continuation is no longer evidence-backed.",
        },
    }
    compacted = compact_json(payload, max_chars=PLANNING_PACKET_MAX_CHARS)
    return json.loads(compacted.text)


def compact_round_competition_result(
    value: Any,
    *,
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    direction = direction if isinstance(direction, dict) else {}
    competition = value if isinstance(value, dict) else {}
    if not competition and isinstance(direction.get("competition_result"), dict):
        competition = direction["competition_result"]
    if not competition:
        return {}
    candidates = [
        compact_round_candidate_evidence(item, direction=direction)
        for item in competition.get("candidates") or []
        if isinstance(item, dict)
    ]
    return {
        "status": competition.get("status"),
        "candidate_count": int(competition.get("candidate_count", len(candidates)) or 0),
        "eligible_candidate_count": int(competition.get("eligible_candidate_count", 0) or 0),
        "selected_candidate_id": competition.get("selected_candidate_id"),
        "selected_objective_key": competition.get("selected_objective_key") or [],
        "selected_for_promotion": competition.get("selected_for_promotion"),
        "selection_rule": str(competition.get("selection_rule") or "")[:500],
        "candidates": candidates,
    }


def compact_round_candidate_evidence(
    candidate: dict[str, Any],
    *,
    direction: dict[str, Any],
) -> dict[str, Any]:
    summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    diagnostics_payload = {
        "eligible": candidate.get("eligible"),
        "core_eligible": candidate.get("core_eligible"),
        "semantic_eligible": candidate.get("semantic_eligible"),
        "activation_eligible": candidate.get("activation_eligible"),
        "worker_status": candidate.get("worker_status"),
        "ja_stage": candidate.get("ja_stage"),
        "ja_issues": candidate.get("ja_issues") or [],
        "semantic_review": candidate.get("semantic_review") or {},
        "validation_summary": summary.get("validation_summary") or {},
        "candidate_summaries": summary.get("candidate_summaries") or [],
    }
    return {
        "candidate_id": candidate.get("candidate_id"),
        "status": candidate.get("status"),
        "model": candidate_model_hint(candidate, direction=direction),
        "objective": candidate.get("objective_key") or [],
        "mechanism_activation": compact_json(
            candidate.get("mechanism_activation") or {},
            max_chars=3_000,
        ).payload,
        "summary": compact_json(summary, max_chars=3_500).payload,
        "diagnostics": compact_json(diagnostics_payload, max_chars=3_500).payload,
        "patch_path": candidate.get("patch_path"),
    }


def candidate_model_hint(candidate: dict[str, Any], *, direction: dict[str, Any]) -> str:
    for key in ("model", "worker_model", "candidate_model"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value[:200]
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    selected_variant = (
        direction.get("selected_candidate_variant")
        if isinstance(direction.get("selected_candidate_variant"), dict)
        else {}
    )
    if candidate_id and candidate_id == str(selected_variant.get("candidate_id") or "").strip():
        for key in ("title", "hypothesis", "method_family", "strategy_type", "experiment_stage"):
            value = str(selected_variant.get(key) or "").strip()
            if value:
                return value[:200]
    for item in direction.get("candidate_variants") or []:
        if not isinstance(item, dict):
            continue
        if candidate_id and candidate_id != str(item.get("candidate_id") or "").strip():
            continue
        for key in ("title", "hypothesis", "method_family", "strategy_type", "experiment_stage"):
            value = str(item.get(key) or "").strip()
            if value:
                return value[:200]
    for key in ("title", "hypothesis", "method_family", "strategy_type", "experiment_stage"):
        value = str(direction.get(key) or "").strip()
        if value:
            return value[:200]
    return ""


def build_planning_packet(
    *,
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    """Build the bounded Main view; promoted source is attached separately read-only."""

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    previous_rounds = []
    for item in (loop_feedback.get("previous_rounds") or [])[-6:]:
        if not isinstance(item, dict):
            continue
        direction = item.get("direction_plan") if isinstance(item.get("direction_plan"), dict) else {}
        previous_rounds.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "candidate_key": item.get("candidate_key"),
                "incumbent_key_after": item.get("incumbent_key_after"),
                "direction_id": direction.get("direction_id"),
                "title": direction.get("title"),
                "strategy_type": direction.get("strategy_type"),
                "hypothesis": str(direction.get("hypothesis") or "")[:600],
                "implementation_order": [
                    str(value)[:160]
                    for value in (direction.get("implementation_order") or [])[:8]
                    if str(value).strip()
                ],
                "failure_signatures": (item.get("failure_signatures") or [])[:8],
                "candidate_summary": compact_json(
                    item.get("candidate_summary") or {},
                    max_chars=8_000,
                ).payload,
                "competition_result": compact_round_competition_result(
                    item.get("competition_result"),
                    direction=direction,
                ),
                "promotion_check": compact_json(
                    item.get("promotion_check") or {},
                    max_chars=3_000,
                ).payload,
                "round_reflection": compact_json(
                    item.get("round_reflection") or {},
                    max_chars=6_000,
                ).payload,
                "patch_path": item.get("patch_path"),
                "patch_excerpt": bounded_artifact_text(item.get("patch_path"), max_chars=12_000),
            }
        )
    experience = (
        loop_feedback.get("experience_memory")
        if isinstance(loop_feedback.get("experience_memory"), dict)
        else {}
    )
    tiers = experience.get("memory_tiers") if isinstance(experience.get("memory_tiers"), dict) else {}
    incumbent = (
        context.get("incumbent_code_context")
        if isinstance(context.get("incumbent_code_context"), dict)
        else {}
    )
    incumbent_audit = (
        context.get("incumbent_capability_audit")
        if isinstance(context.get("incumbent_capability_audit"), dict)
        else {}
    )
    payload = {
        "schema_version": 1,
        "planning_stage": "direction_selection",
        "phase": "baseline" if round_index < 0 else "improvement",
        "direction_id": f"d{round_index:03d}",
        "task_digest": {
            "task_id": task.get("task_id") or context.get("task_id"),
            "problem_family": task.get("problem_family") or context.get("problem_family"),
            "description": task.get("description"),
            "objectives": context.get("objectives") or [],
            "hypothesis": context.get("hypothesis") or "",
        },
        "io_digest": {
            "evaluator_protocol": context.get("evaluator_protocol") or {},
            "edit_policy": context.get("edit_policy") or {},
            "quality_contract": build_agent_generated_solver_quality_contract(context),
        },
        "instance_diagnostics": context.get("instance_diagnostics") or {},
        "strategy_selection_cards": compact_source_records(
            context.get("strategy_selection_cards"),
            max_items=4,
            max_snippet_chars=6_000,
        ),
        "knowledge_query_catalog": context.get("knowledge_query_catalog") or {"tags": []},
        "method_family_catalog": context.get("method_family_catalog") or {"families": []},
        "method_package_catalog": {
            "active_features": catalog.get("active_features") or [],
            "available_after_direction_selection": True,
            "packages": [],
        },
        "incumbent_evidence": {
            "objective_key": loop_feedback.get("incumbent_key_before"),
            "source": incumbent.get("source"),
            "evaluation": compact_json(
                loop_feedback.get("incumbent_summary") or {},
                max_chars=10_000,
            ).payload,
            "files": [
                {
                    "relative_path": item.get("relative_path"),
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                    "chars": item.get("chars"),
                    "truncated": item.get("truncated"),
                }
                for item in incumbent.get("files") or []
                if isinstance(item, dict)
            ][:6],
        },
        # AST 报告用于快速定位符号；完整 incumbent 源码另以只读附件提供，
        # Main 必须把两者与运行证据、上轮 patch 一起审查。
        "incumbent_capability_audit": (
            compact_json(
                compact_incumbent_capability_audit(incumbent_audit),
                max_chars=18_000,
            ).payload
            if incumbent_audit
            else {}
        ),
        "recent_round_evidence": previous_rounds,
        "latest_attempt_evidence": loop_feedback.get("current_round_repair") or {},
        "next_round_guidance": loop_feedback.get("next_round_guidance") or {},
        "user_intervention": loop_feedback.get("user_intervention") or {},
        # 第一阶段可使用历史成败，但不能从旧包 ID、资产路径或实现合同反推具体方法包。
        "validated_memory": compact_direction_selection_memory(
            (tiers.get("validated_lessons") or [])[-6:]
        ),
        "runtime_limits": {
            "one_direction": True,
            "backend_algorithm_agnostic": True,
            "worker_full_context_visible": False,
            "main_reads_full_incumbent_source": True,
            "main_receives_structured_incumbent_audit": bool(incumbent_audit),
            "max_competing_workers": max(
                1,
                min(
                    4,
                    int((loop_feedback.get("competition") or {}).get("max_competing_workers") or 1),
                ),
            ),
        },
        "planner_output_contract": {
            "candidate_variants_must_declare_activation_checks": True,
            "activation_checks_purpose": "prove mechanism execution rather than result quality",
            "experiment_stage_options": [
                "baseline",
                "probe",
                "scale",
                "pivot",
                "research_tournament",
            ],
            "research_tournament_scope": "may compare across method families when round evidence invalidates the current family-level assumption",
        },
    }
    compacted = compact_json(payload, max_chars=PLANNING_PACKET_MAX_CHARS)
    return json.loads(compacted.text)


def compact_direction_selection_memory(values: Any) -> list[dict[str, Any]]:
    """Keep evaluator-backed lessons while hiding concrete implementation packages."""

    result: list[dict[str, Any]] = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        result.append(
            {
                "lesson_type": item.get("lesson_type"),
                "problem_family": item.get("problem_family"),
                "strategy": str(item.get("strategy") or "")[:160],
                "strategy_type": item.get("strategy_type"),
                "outcome": item.get("outcome"),
                "applicability": _bounded_string_list(item.get("applicability"), limit=4, chars=300),
                "contraindications": _bounded_string_list(
                    item.get("contraindications"),
                    limit=4,
                    chars=300,
                ),
                "evidence": {
                    "direction_id": evidence.get("direction_id"),
                    "round_index": evidence.get("round_index"),
                    "decision": evidence.get("decision"),
                    "status": evidence.get("status"),
                    "score_relation": evidence.get("score_relation"),
                },
                "confidence": item.get("confidence"),
            }
        )
    return result


def build_implementation_planning_packet(
    *,
    context: dict[str, Any],
    loop_feedback: dict[str, Any],
    round_index: int,
    direction_selection: dict[str, Any],
) -> dict[str, Any]:
    """Build the second Main view after direction-scoped retrieval has completed."""

    packet = build_planning_packet(
        context=context,
        loop_feedback=loop_feedback,
        round_index=round_index,
    )
    packet["planning_stage"] = "implementation_planning"
    packet["direction_selection"] = direction_selection
    packet.pop("strategy_selection_cards", None)
    packet.pop("knowledge_query_catalog", None)
    packet.pop("method_family_catalog", None)
    active = (
        context.get("active_direction_knowledge")
        if isinstance(context.get("active_direction_knowledge"), dict)
        else {}
    )
    packet["active_direction_knowledge"] = {
        "method_family": active.get("method_family"),
        "method_families": active.get("method_families") or [],
        "query": active.get("query") or [],
        "paths": active.get("paths") or [],
        "cards": compact_source_records(
            active.get("asset_records"),
            max_items=6,
            max_snippet_chars=5_000,
        ),
        "audit": active.get("audit") or {},
    }
    catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    packet["eligible_method_packages"] = compact_method_package_candidates(catalog)
    packet["method_package_catalog"] = {
        "active_features": catalog.get("active_features") or [],
        "knowledge_query_tags": catalog.get("knowledge_query_tags") or [],
        "eligible_package_ids": [
            item.get("package_id")
            for item in catalog.get("packages") or []
            if isinstance(item, dict) and item.get("package_id")
        ],
    }
    compacted = compact_json(packet, max_chars=PLANNING_PACKET_MAX_CHARS)
    return json.loads(compacted.text)


def compact_method_package_candidates(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose package contracts and explanatory assets without full reference source."""

    result: list[dict[str, Any]] = []
    for item in catalog.get("packages") or []:
        if not isinstance(item, dict):
            continue
        implementation_asset = str(item.get("implementation_asset") or "")
        asset_records: list[dict[str, Any]] = []
        for raw_path in item.get("assets") or []:
            path = Path(str(raw_path))
            if not path.is_file() or str(path) == implementation_asset:
                continue
            try:
                snippet = path.read_text(encoding="utf-8")[:5_000]
            except OSError:
                continue
            asset_records.append({"path": str(path), "snippet": snippet})
            if len(asset_records) >= 3:
                break
        contract = item.get("implementation_contract") if isinstance(item.get("implementation_contract"), dict) else {}
        bounded_contract = compact_json(contract, max_chars=12_000).payload if contract else {}
        result.append(
            {
                "package_id": item.get("package_id"),
                "title": item.get("title"),
                "description": item.get("description"),
                "activation_tags": item.get("activation_tags") or [],
                "strategy_types": item.get("strategy_types") or [],
                "implementation_contract": bounded_contract,
                "planning_assets": asset_records,
            }
        )
    return result[:3]


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
        if isinstance(event, dict) and "direction_plan" in event:
            candidates.append(json.dumps(event, ensure_ascii=False))
    for candidate in reversed(candidates):
        parsed = _json_object_from_text(candidate)
        if isinstance(parsed, dict) and isinstance(parsed.get("direction_plan"), dict):
            return parsed
    return None


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
        return None
    return min(timeout_seconds, upper_bound)


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
            for name in ("requirements-method-analyst", "evidence-analyst", "plan-critic"):
                if name in rendered and name not in called_subagents:
                    called_subagents.append(name)
        _sum_numeric_usage(event, usage)
    return {
        "event_count": len(events),
        "called_subagents": called_subagents,
        "usage": usage,
    }


def merge_event_summaries(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, int | float] = dict(first.get("usage") or {})
    for key, value in (second.get("usage") or {}).items():
        usage[key] = usage.get(key, 0) + value
    return {
        "event_count": int(first.get("event_count") or 0) + int(second.get("event_count") or 0),
        "called_subagents": list(
            dict.fromkeys([*(first.get("called_subagents") or []), *(second.get("called_subagents") or [])])
        ),
        "usage": usage,
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


def bounded_artifact_text(path_value: Any, *, max_chars: int) -> str:
    """Read a bounded internal artifact excerpt for next-round causal review."""

    path_text = str(path_value or "").strip()
    if not path_text:
        return ""
    path = Path(path_text)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    head_chars = max_chars * 2 // 3
    tail_chars = max_chars - head_chars
    return f"{text[:head_chars]}\n...<artifact excerpt truncated>...\n{text[-tail_chars:]}"


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
