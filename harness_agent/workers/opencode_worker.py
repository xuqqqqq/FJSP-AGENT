"""OpenCode Coding Agent 适配器。

这里承载的是当前 harness 侧最直接的代码运行时：把 `ExperimentSpec`
翻译成一次受控的命令行调用，让外部 Coding Agent 在隔离 worktree 内
直接改文件，然后把 prompt、命令、stdout/stderr 和超时结果落盘，供
后续审计、diff、smoke gate 与 evaluator 复盘。
"""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import re
import shutil
import shlex
import subprocess
import time
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..deepseek_client import load_local_env, resolve_secret
from harness_agent.context.worker import (
    WORKER_ASSIGNMENT_MAX_CHARS,
    WORKER_ASSIGNMENT_SOFT_CHARS,
)
from harness_agent.core.cancellation import CancellationToken
from harness_agent.core.runner import (
    CREATE_NEW_PROCESS_GROUP,
    cleanup_process_descendants,
    kill_process_tree,
)
from ..worker import CodingWorker, ExperimentSpec, WorkerAssignment, WorkerCapabilities, WorkerResult


DEFAULT_OPENCODE_MODEL = "deepseek/deepseek-v4-pro"
OPENCODE_OPENAI_COMPAT_ENV = "OPENCODE_OPENAI_COMPAT_FROM_DEEPSEEK"
OPENCODE_WORKER_AGENT = "algoforge-worker"
MIN_OPENCODE_AGENT_STEPS = 8
MAX_OPENCODE_AGENT_STEPS = 16
WORKER_RUNTIME_POLICY_MAX_CHARS = 4_000
OPENCODE_COMPACTION_CONFIG = {
    "auto": True,
    "prune": True,
    "tail_turns": 2,
    "preserve_recent_tokens": 8_000,
}
SESSION_WORKSPACE_ROOT = ".algoforge_opencode_session"
SESSION_WORKSPACE_NAME = "workspace"
SESSION_STATE_FILE = "session_state.json"
SESSION_LANE_SEGMENT_MAX_CHARS = 32
ZERO_EVENT_STARTUP_RETRY_BUDGET_SECONDS = 120.0
OPENCODE_WORKER_ROLE_PROMPT = """You are `algoforge-worker`.

Execute the attached validated WorkerAssignment as the sole planning input.
Do not re-diagnose or replace the assigned direction. Load every selected
`implementation_skills` entry, study its implementation guidance, and decide
how to combine only the method families selected by Main. Skill references and
code examples are advisory: adopt, adapt, combine, or reject them based on the
incumbent and task evidence while preserving the assignment's hard contracts.
Read only `target_file`, those Skills, and `read_set`; edit only `target_file`,
and keep unrelated behavior unchanged. In baseline mode, a missing `target_file`
is expected: create it instead of treating its absence as a blocker. In
improvement or repair mode, read the existing `target_file` before editing it.
Do not use subagents, questions, or network access. If the assignment is
incomplete or contradictory, report the concrete blocker instead of expanding
scope. During execution, publish concise Simplified Chinese commentary before
each meaningful phase: what evidence you are checking, what implementation
decision follows from it, what you changed, and what the latest check proves.
Keep code, commands, paths, and symbol names unchanged. Do not expose hidden
chain-of-thought; report only concise engineering reasoning grounded in visible
evidence. Return a concise Simplified Chinese report of changed files and checks run.
"""


class OpenCodeWorker(CodingWorker):
    """默认的直接执行型 Worker 运行时。

    与 DeepSeekWorker 返回结构化 proposal 再由 harness 落地不同，
    OpenCodeWorker 让外部 agent 直接在 `spec.worktree_path` 内编辑。
    因此这里的职责重点不是 JSON 规范化，而是：
    1. 固化本轮 prompt 和命令行参数；
    2. 约束子进程生命周期与超时；
    3. 保存可审计产物，便于主流程判断是否接受候选。
    """

    def __init__(
        self,
        executable: str = "opencode",
        model: str | None = None,
        variant: str | None = None,
        timeout_seconds: int | None = None,
        cancellation: CancellationToken | None = None,
        provider_stream_retries: int | None = None,
        provider_retry_backoff_seconds: float | None = None,
        zero_event_timeout_seconds: int | None = None,
    ) -> None:
        configured_executable = (
            os.environ.get("OPENCODE_EXECUTABLE") or executable
            if executable == "opencode"
            else executable
        )
        self.executable = configured_executable
        executable_path = Path(configured_executable)
        self.executable_path = (
            str(executable_path.resolve()) if executable_path.exists() else shutil.which(configured_executable)
        )
        # Fresh candidate worktrees have no project-local model history.  An
        # explicit default keeps `opencode run` non-interactive in services.
        self.model = model or os.environ.get("OPENCODE_MODEL") or DEFAULT_OPENCODE_MODEL
        self.variant = (variant or os.environ.get("OPENCODE_WORKER_VARIANT") or "").strip()
        self.run_command = os.environ.get("OPENCODE_RUN_COMMAND", "run")
        self.timeout_seconds = resolve_optional_timeout_seconds(
            timeout_seconds,
            env_var="OPENCODE_WORKER_TIMEOUT_SECONDS",
        )
        self.cancellation = cancellation
        self.provider_stream_retries = max(
            0,
            int(
                provider_stream_retries
                if provider_stream_retries is not None
                else os.environ.get("OPENCODE_PROVIDER_STREAM_RETRIES", "2")
            ),
        )
        self.provider_retry_backoff_seconds = max(
            0.0,
            float(
                provider_retry_backoff_seconds
                if provider_retry_backoff_seconds is not None
                else os.environ.get("OPENCODE_PROVIDER_RETRY_BACKOFF_SECONDS", "1")
            ),
        )
        raw_zero_event_timeout = (
            zero_event_timeout_seconds
            if zero_event_timeout_seconds is not None
            else os.environ.get("OPENCODE_ZERO_EVENT_TIMEOUT_SECONDS", "45")
        )
        parsed_zero_event_timeout = int(raw_zero_event_timeout)
        self.zero_event_timeout_seconds = (
            max(1, parsed_zero_event_timeout) if parsed_zero_event_timeout > 0 else None
        )

    def capabilities(self) -> WorkerCapabilities:
        available = self.executable_path is not None
        return WorkerCapabilities(
            name="opencode" if available else "opencode_unavailable",
            supports_code_generation=available,
            supports_repair=available,
            supports_structured_output=False,
            supports_session_reuse=available,
        )

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        """执行一次 OpenCode worker 周期并收集审计产物。

        `worker_assignment_path` 是唯一规划输入；完整 Context Packet 仍由
        Main Agent、JA、Semantic Reviewer 和 Core 使用，不会传给 Worker。
        `worktree_path` 是唯一允许直接修改的候选目录，`output_dir` 只保存
        Worker 的协议、命令、事件和 stderr 证据。

        返回值只描述本次 worker 进程是否正常结束，不直接声明候选算法已
        通过 evaluator。
        """
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()
        output_dir = (
            Path(spec.output_dir)
            if spec.output_dir
            else Path(spec.worktree_path) / ".algoforge_worker" / spec.experiment_id
        ).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            assignment_path, assignment = self._load_assignment(spec)
            self._validate_required_inputs(assignment=assignment, worktree_path=Path(spec.worktree_path))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            error_path = output_dir / "worker_assignment_error.json"
            error_path.write_text(
                json_dumps({"status": "invalid_assignment", "reason": str(exc)}),
                encoding="utf-8",
            )
            return WorkerResult(
                status="invalid_assignment",
                changed_files=[],
                summary=f"OpenCode was not started because the Main Agent assignment is invalid: {exc}",
                artifacts={"output_dir": str(output_dir), "assignment_error": str(error_path)},
            )
        if self.executable_path is None:
            return WorkerResult(
                status="unavailable",
                changed_files=[],
                summary=f"OpenCode executable {self.executable!r} was not found on PATH.",
                artifacts={"output_dir": str(output_dir)},
            )

        worktree_path = Path(spec.worktree_path).resolve()
        session_launch = self._resolve_session_launch(
            spec,
            assignment=assignment,
            worktree_path=worktree_path,
        )
        prompt = self._prompt(spec, assignment=assignment, session_launch=session_launch)
        prompt_path = output_dir / "opencode_prompt.md"
        budget_path = output_dir / "opencode_context_budget.json"
        stdout_path = output_dir / "opencode.stdout.txt"
        events_path = output_dir / "opencode_events.jsonl"
        compaction_path = output_dir / "opencode_compaction.json"
        stderr_path = output_dir / "opencode.stderr.txt"
        command_path = output_dir / "opencode_command.json"
        runtime_config_path = output_dir / "opencode_runtime_config.json"
        session_path = output_dir / "opencode_session.json"
        provider_retries_path = output_dir / "opencode_provider_retries.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        assignment_chars = len(assignment_path.read_text(encoding="utf-8"))
        context_budget = worker_context_budget_payload(
            prompt_chars=len(prompt),
            assignment_chars=assignment_chars,
        )
        budget_path.write_text(
            json_dumps(context_budget),
            encoding="utf-8",
        )
        runtime_config = self._runtime_config(
            spec,
            assignment=assignment,
            attachment_paths=[prompt_path, assignment_path],
            workspace_roots=[worktree_path, session_launch.launch_dir],
        )
        runtime_config_path.write_text(json_dumps(runtime_config), encoding="utf-8")
        command = self._command(
            prompt_path,
            assignment_path,
            worktree_path=session_launch.launch_dir,
            session_id=session_launch.command_session_id,
        )
        command_path.write_text(json_dumps(command), encoding="utf-8")
        self._write_session_launch_record(session_path, session_launch, observed_session_id=None)

        # Provider stream failures are infrastructure faults, not algorithmic
        # Local Trials. Retry them inside this Worker call and reuse the session
        # that already contains completed reads/reasoning.
        worker_deadline = (
            time.monotonic() + float(self.timeout_seconds)
            if self.timeout_seconds is not None
            else None
        )
        zero_event_retry_deadline: float | None = None
        attempt_index = 0
        attempt_command = command
        stdout_chars = 0
        stderr_chars = 0
        provider_attempts: list[dict[str, Any]] = []
        retry_reason: str | None = None
        timed_out = False
        process: subprocess.Popen[str]
        while True:
            timed_out = False
            zero_event_stream_timeout = False
            if zero_event_retry_deadline is None or not _is_zero_event_startup_retry_reason(retry_reason):
                zero_event_retry_deadline = time.monotonic() + ZERO_EVENT_STARTUP_RETRY_BUDGET_SECONDS
                if worker_deadline is not None:
                    zero_event_retry_deadline = min(zero_event_retry_deadline, worker_deadline)
            stream_mode = "w" if attempt_index == 0 else "a"
            attempt_started_at = time.monotonic()
            attempt_event_start_bytes = events_path.stat().st_size if events_path.exists() else 0
            # OpenCode emits one JSON object per line. Appending retries to the
            # same file keeps Web monitoring and audit consumers continuous.
            with (
                events_path.open(stream_mode, encoding="utf-8", buffering=1) as events_stream,
                stderr_path.open(stream_mode, encoding="utf-8", buffering=1) as stderr_stream,
            ):
                popen_kwargs: dict[str, object] = {
                    "cwd": str(session_launch.launch_dir),
                    "env": opencode_subprocess_environment(runtime_config=runtime_config),
                    "stdin": subprocess.DEVNULL,
                    "text": True,
                    "stdout": events_stream,
                    "stderr": stderr_stream,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
                else:
                    popen_kwargs["start_new_session"] = True
                process = subprocess.Popen(attempt_command, **popen_kwargs)
                registration = (
                    self.cancellation.register_terminator(lambda: kill_process_tree(process))
                    if self.cancellation is not None
                    else None
                )
                remaining_timeout = (
                    max(0.001, worker_deadline - time.monotonic())
                    if worker_deadline is not None
                    else None
                )
                attempt_timeout = remaining_timeout
                if self.zero_event_timeout_seconds is not None:
                    zero_event_retry_remaining = max(0.001, zero_event_retry_deadline - time.monotonic())
                    attempt_timeout = (
                        min(
                            float(self.zero_event_timeout_seconds),
                            zero_event_retry_remaining,
                            remaining_timeout,
                        )
                        if remaining_timeout is not None
                        else min(
                            float(self.zero_event_timeout_seconds),
                            zero_event_retry_remaining,
                        )
                    )
                try:
                    process.wait(timeout=attempt_timeout)
                except subprocess.TimeoutExpired:
                    events_stream.flush()
                    current_event_bytes = events_path.stat().st_size if events_path.exists() else 0
                    zero_event_stream_timeout = current_event_bytes <= attempt_event_start_bytes
                    if not zero_event_stream_timeout:
                        remaining_timeout = (
                            max(0.001, worker_deadline - time.monotonic())
                            if worker_deadline is not None
                            else None
                        )
                        try:
                            process.wait(timeout=remaining_timeout)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                    else:
                        timed_out = True
                    if timed_out:
                        kill_process_tree(process)
                        try:
                            process.wait(timeout=self._timeout_cleanup_grace_seconds())
                        except subprocess.TimeoutExpired:
                            kill_process_tree(process)
                            try:
                                process.kill()
                            except OSError:
                                pass
                finally:
                    events_stream.flush()
                    stderr_stream.flush()
                    if self.cancellation is not None:
                        self.cancellation.unregister_terminator(registration)

            stdout = events_path.read_text(encoding="utf-8", errors="replace")
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
            attempt_stdout = stdout[stdout_chars:]
            attempt_stderr = stderr[stderr_chars:]
            stdout_chars = len(stdout)
            stderr_chars = len(stderr)
            cleanup_process_descendants(process)
            attempt_event_end_bytes = events_path.stat().st_size if events_path.exists() else 0
            attempt_event_stream_bytes = max(0, attempt_event_end_bytes - attempt_event_start_bytes)
            attempt_duration_seconds = max(0.0, time.monotonic() - attempt_started_at)
            observed_attempt_session = extract_opencode_session_id(attempt_stdout)
            if timed_out and zero_event_stream_timeout:
                retry_reason = "zero_event_stream_timeout"
            elif not timed_out and process.returncode != 0 and attempt_event_stream_bytes == 0:
                retry_reason = "zero_event_stream_exit"
            else:
                retry_reason = (
                    retryable_opencode_provider_error(attempt_stdout, attempt_stderr)
                    if not timed_out and process.returncode != 0
                    else None
                )
            attempt_reason = retry_reason
            if attempt_reason is None:
                if timed_out:
                    attempt_reason = "worker_timeout"
                elif process.returncode == 0:
                    attempt_reason = (
                        "completed_without_events" if attempt_event_stream_bytes == 0 else "completed"
                    )
                else:
                    attempt_reason = "non_retryable_nonzero_exit"
            provider_attempts.append(
                {
                    "attempt_index": attempt_index,
                    "returncode": process.returncode,
                    "timed_out": timed_out,
                    "reason": attempt_reason,
                    "observed_session_id": observed_attempt_session,
                    "event_stream_bytes": attempt_event_stream_bytes,
                    "duration_seconds": round(attempt_duration_seconds, 6),
                    "command": attempt_command,
                }
            )
            if retry_reason is None or attempt_index >= self.provider_stream_retries:
                break
            if worker_deadline is not None and time.monotonic() >= worker_deadline:
                break
            if _is_zero_event_startup_retry_reason(retry_reason) and time.monotonic() >= zero_event_retry_deadline:
                break
            attempt_index += 1
            retry_session_id = observed_attempt_session or session_launch.command_session_id
            attempt_command = self._command(
                prompt_path,
                assignment_path,
                worktree_path=session_launch.launch_dir,
                session_id=retry_session_id,
            )
            if self.provider_retry_backoff_seconds > 0:
                delay = self.provider_retry_backoff_seconds * (2 ** (attempt_index - 1))
                if worker_deadline is not None:
                    delay = min(delay, max(0.0, worker_deadline - time.monotonic()))
                if _is_zero_event_startup_retry_reason(retry_reason):
                    delay = min(delay, max(0.0, zero_event_retry_deadline - time.monotonic()))
                if delay > 0:
                    time.sleep(delay)

        provider_retry_count = max(0, len(provider_attempts) - 1)
        provider_retry_recovered = provider_retry_count > 0 and not timed_out and process.returncode == 0
        provider_retry_exhausted = bool(retry_reason and not provider_retry_recovered)
        provider_retries_path.write_text(
            json_dumps(
                {
                    "schema_version": 1,
                    "max_retries": self.provider_stream_retries,
                    "retry_count": provider_retry_count,
                    "recovered": provider_retry_recovered,
                    "exhausted": provider_retry_exhausted,
                    "attempts": provider_attempts,
                }
            ),
            encoding="utf-8",
        )
        timed_out_target_sync = TargetSyncResult(synced=False, reason=None, quarantine_path=None)
        if not timed_out and process.returncode == 0 and stdout.strip():
            _sync_worker_target_from_session(
                session_launch.launch_dir,
                worktree_path,
                assignment.target_file,
            )
        elif timed_out:
            timed_out_target_sync = _sync_worker_target_from_session(
                session_launch.launch_dir,
                worktree_path,
                assignment.target_file,
                validate_python=True,
                quarantine_invalid_source=True,
            )
        observed_session_id = extract_opencode_session_id(stdout)
        self._write_session_launch_record(
            session_path,
            session_launch,
            observed_session_id=observed_session_id,
        )
        # The per-attempt record is audit evidence; the lane state is what lets
        # the next Local Trial locate and safely resume the observed session.
        self._write_session_launch_record(
            session_launch.state_path,
            session_launch,
            observed_session_id=observed_session_id,
        )
        stdout_path.write_text(stdout, encoding="utf-8")
        compaction_path.write_text(
            json_dumps(summarize_opencode_compaction_events(stdout)),
            encoding="utf-8",
        )
        session_artifacts = self._session_result_artifacts(
            session_launch,
            observed_session_id=observed_session_id,
            event_stream_bytes=len(stdout.encode("utf-8")),
        )
        provider_retry_artifacts = {
            "provider_retries": str(provider_retries_path),
            "provider_retry_count": str(provider_retry_count),
            "provider_retry_recovered": str(provider_retry_recovered).lower(),
            "provider_retry_exhausted": str(provider_retry_exhausted).lower(),
        }
        if timed_out:
            timeout_summary = (
                f"OpenCode produced no event stream within {self.zero_event_timeout_seconds} seconds "
                f"after {len(provider_attempts)} provider attempts."
                if retry_reason == "zero_event_stream_timeout"
                else f"OpenCode exceeded {self.timeout_seconds} seconds."
            )
            return WorkerResult(
                status="timeout",
                changed_files=[assignment.target_file] if timed_out_target_sync.synced else [],
                summary=timeout_summary,
                raw_log_path=str(stdout_path),
                artifacts={
                    "output_dir": str(output_dir),
                    "prompt": str(prompt_path),
                    "context_budget": str(budget_path),
                    "stderr": str(stderr_path),
                    "command": str(command_path),
                    "runtime_config": str(runtime_config_path),
                    "session": str(session_path),
                    "worker_assignment": str(assignment_path),
                    "events": str(events_path),
                    "compaction": str(compaction_path),
                    "target_sync_reason": timed_out_target_sync.reason or "",
                    "target_sync_quarantine": timed_out_target_sync.quarantine_path or "",
                    **provider_retry_artifacts,
                    **session_artifacts,
                },
            )

        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()

        status = opencode_status(process.returncode, stdout, stderr)
        if session_launch.command_session_id and not stdout.strip():
            status = "failed_runtime"
            summary = (
                "OpenCode continuation exited without a JSON event stream; the requested session "
                "was not counted as resumed."
            )
        else:
            summary = (
                f"OpenCode exited with code {process.returncode}. "
                "Harness diff/evaluator artifacts decide acceptance."
            )
        if provider_retry_recovered:
            summary = f"Recovered after {provider_retry_count} provider stream retry; {summary}"
        elif provider_retry_exhausted:
            summary = f"Provider stream retries exhausted after {provider_retry_count} retries; {summary}"
        return WorkerResult(
            status=status,
            changed_files=[],
            summary=summary,
            raw_log_path=str(stdout_path),
            artifacts={
                "output_dir": str(output_dir),
                "prompt": str(prompt_path),
                "context_budget": str(budget_path),
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "command": str(command_path),
                "runtime_config": str(runtime_config_path),
                "session": str(session_path),
                "worker_assignment": str(assignment_path),
                "events": str(events_path),
                "compaction": str(compaction_path),
                **provider_retry_artifacts,
                **session_artifacts,
            },
        )

    @staticmethod
    def _session_result_artifacts(
        session_launch: "OpenCodeSessionLaunch",
        *,
        observed_session_id: str | None,
        event_stream_bytes: int,
    ) -> dict[str, str]:
        artifacts = {
            "resume_strategy": session_launch.strategy,
            "event_stream_bytes": str(max(0, int(event_stream_bytes))),
        }
        if session_launch.requested_session_id:
            artifacts["requested_session_id"] = session_launch.requested_session_id
        if session_launch.command_session_id:
            artifacts["command_session_id"] = session_launch.command_session_id
        if observed_session_id:
            artifacts["observed_session_id"] = observed_session_id
            # Backward-compatible continuity handle for non-OpenCode orchestration.
            artifacts["session_id"] = observed_session_id
        return artifacts

    def _command(
        self,
        prompt_path: Path,
        assignment_path: Path,
        *,
        worktree_path: Path,
        session_id: str | None = None,
    ) -> list[str]:
        """构造最终命令行。

        使用 OpenCode 原生 file attachment 让说明在首个模型请求中直接可见，
        避免 agent 先后两次 Read 长 prompt，再把这些工具输出累计进会话。
        """
        command = [str(self.executable_path)]
        if self.run_command:
            command.extend(shlex.split(self.run_command, posix=False))
        if self.model:
            command.extend(["--model", self.model])
        if self.variant:
            command.extend(["--variant", self.variant])
        session_title = f"AlgoForge Worker {worktree_path.parent.name}"[:120]
        # Explicit titles suppress OpenCode's auxiliary title-generation call.
        command.extend(["--title", session_title])
        if session_id:
            command.extend(["--session", session_id])
        command.extend(
            [
                "--agent",
                OPENCODE_WORKER_AGENT,
                "--format",
                "json",
                f"--dir={_absolute_path_no_resolve(worktree_path)}",
            ]
        )
        command.append("Execute the attached Main Agent assignment under the attached worker runtime policy.")
        # `--file` 是数组参数；使用等号形式可避免把后续位置参数误吞成第二个文件。
        command.append(f"--file={prompt_path}")
        command.append(f"--file={assignment_path}")
        return command

    def _runtime_config(
        self,
        spec: ExperimentSpec,
        *,
        assignment: WorkerAssignment,
        attachment_paths: list[Path],
        workspace_roots: list[Path] | None = None,
    ) -> dict[str, object]:
        """给 OpenCode 注入只对本次 worker 生效的硬执行边界。

        提示词中的 ``max_steps`` 只是自然语言约束，OpenCode 不会据此限制
        自己的工具轮次。这里用当前安装版本支持的 ``task: deny`` 真正禁止
        子 Agent，并为读取、编辑、编译和一次 smoke 留出有限的主 Agent 步数。
        """
        requested_steps = max(1, int(spec.max_steps)) * 3
        agent_steps = min(MAX_OPENCODE_AGENT_STEPS, max(MIN_OPENCODE_AGENT_STEPS, requested_steps))
        actual_worktree_path = Path(spec.worktree_path).resolve()
        permitted_roots = _unique_workspace_roots(workspace_roots or [actual_worktree_path])
        read_permissions: dict[str, str] = {"*": "deny"}
        self._allow_worktree_path(read_permissions, permitted_roots, assignment.target_file)
        for item in assignment.read_set:
            self._allow_worktree_path(read_permissions, permitted_roots, str(item.get("path") or ""))
        skill_permissions: str | dict[str, str] = "deny"
        if assignment.implementation_skills:
            skill_permissions = {"*": "deny"}
            for item in assignment.implementation_skills:
                skill_id = str(item.get("skill_id") or "").strip()
                sandbox_path = str(item.get("sandbox_path") or "").strip()
                skill_permissions[skill_id] = "allow"
                self._allow_worktree_tree(read_permissions, permitted_roots, sandbox_path)
        edit_permissions: dict[str, str] = {"*": "deny"}
        self._allow_worktree_path(edit_permissions, permitted_roots, assignment.target_file)
        bash_permissions = {
            "*": "deny",
            f"python -m py_compile {assignment.target_file}": "allow",
            f'python -m py_compile "{assignment.target_file}"': "allow",
        }
        smoke_wrapper = Path(spec.worktree_path) / ".algoforge_worker_runtime" / "run_smoke.py"
        if smoke_wrapper.is_file():
            bash_permissions["python .algoforge_worker_runtime/run_smoke.py"] = "allow"
        return {
            "$schema": "https://opencode.ai/config.json",
            "snapshot": False,
            "compaction": dict(OPENCODE_COMPACTION_CONFIG),
            "agent": {
                OPENCODE_WORKER_AGENT: {
                    "description": "Bounded AlgoForge implementation worker",
                    "mode": "primary",
                    "prompt": OPENCODE_WORKER_ROLE_PROMPT,
                    "steps": agent_steps,
                    "permission": {
                        "*": "deny",
                        "read": read_permissions,
                        "glob": "deny",
                        "grep": "deny",
                        "edit": edit_permissions,
                        "bash": bash_permissions,
                        "external_directory": {
                            "*": "deny",
                            **{str(path.resolve()): "allow" for path in attachment_paths},
                        },
                        "task": "deny",
                        "question": "deny",
                        "webfetch": "deny",
                        "websearch": "deny",
                        "list": "deny",
                        "lsp": "deny",
                        "todowrite": "deny",
                        "doom_loop": "deny",
                        "skill": skill_permissions,
                    },
                }
            },
        }

    @staticmethod
    def _allow_worktree_path(permissions: dict[str, str], worktree_paths: list[Path], relative: str) -> None:
        """Allow one path in the forms emitted by OpenCode on Windows and POSIX."""

        normalized = relative.replace("\\", "/").strip()
        if not normalized:
            return
        permissions[normalized] = "allow"
        for worktree_path in worktree_paths:
            absolute = _absolute_path_no_resolve(worktree_path / normalized)
            resolved = absolute.resolve()
            for pattern in (str(absolute), absolute.as_posix(), str(resolved), resolved.as_posix()):
                permissions[pattern] = "allow"

    @staticmethod
    def _allow_worktree_tree(permissions: dict[str, str], worktree_paths: list[Path], relative: str) -> None:
        normalized = relative.replace("\\", "/").strip().rstrip("/")
        if not normalized:
            return
        for pattern in (normalized, f"{normalized}/**"):
            permissions[pattern] = "allow"
        for worktree_path in worktree_paths:
            absolute = _absolute_path_no_resolve(worktree_path / normalized)
            resolved = absolute.resolve()
            for path_variant in (absolute, resolved):
                permissions[str(path_variant)] = "allow"
                permissions[f"{path_variant}{os.sep}**"] = "allow"
                permissions[path_variant.as_posix()] = "allow"
                permissions[f"{path_variant.as_posix()}/**"] = "allow"

    def _load_assignment(self, spec: ExperimentSpec) -> tuple[Path, WorkerAssignment]:
        """缺少 Main Agent 任务书时 fail closed，不启动外部进程。"""

        if not spec.worker_assignment_path:
            raise ValueError("worker_assignment_path is required")
        assignment_path = Path(spec.worker_assignment_path).resolve()
        if not assignment_path.is_file():
            raise ValueError(f"worker assignment does not exist: {assignment_path}")
        return assignment_path, WorkerAssignment.load(assignment_path)

    def _validate_required_inputs(self, *, assignment: WorkerAssignment, worktree_path: Path) -> None:
        """确认 Main 声明的必读输入已被 Harness 精确镜像到沙箱。"""

        missing = [
            str(item.get("path") or "")
            for item in assignment.read_set
            if item.get("required", True)
            and not (worktree_path / str(item.get("path") or "")).exists()
        ]
        if missing:
            raise ValueError("required assignment inputs are missing from worktree: " + ", ".join(missing))
        missing_skills = [
            str(item.get("skill_id") or "")
            for item in assignment.implementation_skills
            if item.get("required", True)
            and not (
                worktree_path
                / str(item.get("sandbox_path") or "")
                / "SKILL.md"
            ).is_file()
        ]
        if missing_skills:
            raise ValueError("required Worker Implementation Skills are missing: " + ", ".join(missing_skills))

    def _prompt(
        self,
        spec: ExperimentSpec,
        *,
        assignment: WorkerAssignment,
        session_launch: "OpenCodeSessionLaunch",
    ) -> str:
        """返回短而稳定的执行协议；动态规划内容只存在于 JSON 任务书。"""

        target_file_policy = (
            "This is baseline mode. `target_file` is explicitly authorized but may not exist yet; "
            "if it is absent, create it and continue instead of reporting a missing-input blocker."
            if assignment.mode == "baseline"
            else "This is improvement/repair mode. `target_file` is the required incumbent; read it before editing and preserve unrelated working behavior."
        )
        try:
            baseline_trial = int(assignment.lineage.get("baseline_trial") or 1)
        except (TypeError, ValueError):
            baseline_trial = 1
        write_first_policy = (
            "- Trial 1 write-first checkpoint: after loading the required Skill, read the required `read_set` "
            "in one batch and immediately create `target_file`. Do not open optional Skill reference files "
            "or spend a separate turn narrating a plan before the first target checkpoint. Prioritize a complete "
            "minimal legal solver over optional reading, explanation, or checks."
            if assignment.mode == "baseline" and baseline_trial <= 1
            else ""
        )
        prompt = f"""
# AlgoForge Coding Worker Runtime Policy

Execute exactly the attached `WorkerAssignment` issued by the Main Agent.
The assignment is the sole planning input for this worker.

- Do not read or request the full Context Packet, method-package catalog,
  history, experience memory, or unselected cards.
- Load every listed `implementation_skills` entry and no unselected Skill.
  Its examples are advisory implementation material: combine or adapt them
  inside Main's selected method families and report material departures.
- Read only `target_file`, paths listed in `read_set`, and files under selected Skill folders;
  do not list, glob, recursively scan, or broadly explore the repository.
- {target_file_policy}
{write_first_policy}
- Edit only `target_file`. Do not change contracts, knowledge assets, evaluator,
  Harness code, runtime configuration, or any other file.
- Work alone. Task/subagent, question, and network tools are disabled.
- Implement every deliverable as reachable behavior in `implementation_order`;
  preserve confirmed mechanisms and obey the completion rule.
- A repair may close listed gaps but may not switch method package or rewrite
  unrelated working behavior.
- Apply stateful changes transactionally. A failed candidate action must not
  leave partially mutated state.
- Run at most one compile and one optional bounded smoke, exactly via
  `python .algoforge_worker_runtime/run_smoke.py`. Never run the evaluator,
  full tests, benchmarks, parameter sweeps, or ad-hoc commands.
- Finish after the bounded edit and checks; Harness gates decide acceptance.

Runtime identifiers:
- mode: {assignment.mode}
- local_trial: {max(1, spec.local_trial_index + 1)}/{max(1, spec.local_trial_count)}
- session_mode: {session_launch.prompt_mode}
""".strip()
        if session_launch.requested_session_id or spec.local_trial_index > 0:
            prompt += (
                "\n\nFor a continued Local Trial, the Harness may restore an earlier best valid parent "
                "into a new isolated worktree. Treat the current assignment, worktree, and runtime feedback "
                "as authoritative; use session memory only to avoid repeated failed edits in this direction."
            )
        if session_launch.prompt_note:
            prompt += f"\n\nSession continuity note:\n- {session_launch.prompt_note}"
        if len(prompt) > WORKER_RUNTIME_POLICY_MAX_CHARS:
            raise ValueError(
                f"worker runtime policy exceeds {WORKER_RUNTIME_POLICY_MAX_CHARS} chars: {len(prompt)}"
            )
        return prompt

    def _resolve_session_launch(
        self,
        spec: ExperimentSpec,
        *,
        assignment: WorkerAssignment,
        worktree_path: Path,
    ) -> "OpenCodeSessionLaunch":
        requested_session_id = str(spec.session_id or "").strip() or None
        session_root = _session_scope_root(worktree_path) / SESSION_WORKSPACE_ROOT
        existing_state_path = _find_session_state(session_root, requested_session_id)
        lane_key = _session_lane_key(spec.experiment_id, assignment.direction_id)
        state_dir = (
            existing_state_path.parent
            if existing_state_path is not None
            else session_root / _safe_session_segment(lane_key)
        )
        alias_path = state_dir / SESSION_WORKSPACE_NAME
        state_path = state_dir / SESSION_STATE_FILE
        previous_state = _read_session_state(state_path)
        launch_dir = worktree_path
        prompt_mode = "new_direction_session"
        prompt_note: str | None = None
        command_session_id = requested_session_id
        strategy = "direct_worktree"

        alias_error: str | None = None
        try:
            launch_dir = _ensure_session_workspace_alias(
                alias_path,
                worktree_path,
                preserved_target_file=(
                    assignment.target_file
                    if requested_session_id and existing_state_path is not None
                    else None
                ),
            )
            strategy = "materialized_session_workspace"
            prompt_mode = (
                "continued_same_direction_via_materialized_workspace"
                if requested_session_id
                else "new_direction_session_via_materialized_workspace"
            )
            if requested_session_id and existing_state_path is None:
                command_session_id = None
                strategy = "restart_equivalent_context_missing_workspace_state"
                prompt_mode = "restarted_equivalent_context_due_to_missing_workspace_state"
                prompt_note = (
                    f"Do not resume OpenCode session {requested_session_id} because this Harness run has no "
                    "persisted workspace binding for it. Continue from the attached assignment, refreshed "
                    "worktree, and current runtime feedback instead."
                )
        except OSError as exc:
            alias_error = str(exc)
            previous_launch_dir = str(
                previous_state.get("actual_worktree_path")
                or previous_state.get("launch_dir")
                or previous_state.get("last_launch_dir")
                or ""
            ).strip()
            previous_dir_changed = previous_launch_dir and previous_launch_dir != str(worktree_path)
            likely_new_trial_worktree = spec.local_trial_index > 0 or worktree_path.parent.name.startswith("repair_")
            if requested_session_id and (previous_dir_changed or likely_new_trial_worktree):
                command_session_id = None
                strategy = "restart_equivalent_context"
                prompt_mode = "restarted_equivalent_context_due_to_workspace_binding"
                prior_dir = previous_launch_dir or "the previous worktree"
                prompt_note = (
                    f"Do not resume OpenCode session {requested_session_id} because its prior workspace "
                    f"directory was {prior_dir} while this attempt runs in {worktree_path}. Continue from "
                    "the attached assignment, refreshed worktree, and current runtime feedback instead."
                )
            elif requested_session_id:
                prompt_mode = "continued_same_direction_without_stable_workspace"
            elif alias_error:
                prompt_mode = "new_direction_session_without_stable_workspace"

        session_launch = OpenCodeSessionLaunch(
            launch_dir=launch_dir,
            command_session_id=command_session_id,
            requested_session_id=requested_session_id,
            prompt_mode=prompt_mode,
            prompt_note=prompt_note,
            strategy=strategy,
            state_path=state_path,
            alias_path=alias_path if strategy == "materialized_session_workspace" else None,
            actual_worktree_path=worktree_path,
            alias_error=alias_error,
        )
        self._write_session_launch_record(state_path, session_launch, observed_session_id=None)
        return session_launch

    def _write_session_launch_record(
        self,
        path: Path,
        session_launch: "OpenCodeSessionLaunch",
        *,
        observed_session_id: str | None,
    ) -> None:
        payload = {
            "schema_version": 1,
            "requested_session_id": session_launch.requested_session_id,
            "command_session_id": session_launch.command_session_id,
            "observed_session_id": observed_session_id,
            "resume_strategy": session_launch.strategy,
            "prompt_mode": session_launch.prompt_mode,
            "actual_worktree_path": str(session_launch.actual_worktree_path),
            "launch_dir": str(session_launch.launch_dir),
            "stable_workspace_alias": (
                str(session_launch.alias_path) if session_launch.alias_path is not None else None
            ),
            "materialized_workspace": (
                str(session_launch.alias_path)
                if session_launch.strategy == "materialized_session_workspace"
                else None
            ),
            "alias_error": session_launch.alias_error,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_dumps(payload), encoding="utf-8")

    def _timeout_cleanup_grace_seconds(self) -> float:
        timeout_seconds = self.timeout_seconds
        if timeout_seconds is None:
            return 1.0
        return max(0.2, min(1.0, float(timeout_seconds)))


@dataclass(frozen=True)
class OpenCodeSessionLaunch:
    launch_dir: Path
    command_session_id: str | None
    requested_session_id: str | None
    prompt_mode: str
    prompt_note: str | None
    strategy: str
    state_path: Path
    alias_path: Path | None
    actual_worktree_path: Path
    alias_error: str | None


@dataclass(frozen=True)
class TargetSyncResult:
    synced: bool
    reason: str | None
    quarantine_path: str | None


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def worker_context_budget_payload(*, prompt_chars: int, assignment_chars: int) -> dict[str, object]:
    assignment_budget_status = (
        "soft_target_exceeded"
        if assignment_chars > WORKER_ASSIGNMENT_SOFT_CHARS
        else "within_soft_target"
    )
    total_attached_chars = prompt_chars + assignment_chars
    return {
        "stable_policy_chars": prompt_chars,
        "assignment_chars": assignment_chars,
        "assignment_soft_limit_chars": WORKER_ASSIGNMENT_SOFT_CHARS,
        "assignment_hard_limit_chars": WORKER_ASSIGNMENT_MAX_CHARS,
        "assignment_budget_status": assignment_budget_status,
        "assignment_budget_warning": (
            "WorkerAssignment exceeds the preferred 12000-character target but remains within the "
            "validated hard limit."
            if assignment_budget_status == "soft_target_exceeded"
            else None
        ),
        "prompt_chars": prompt_chars,
        "total_attached_chars": total_attached_chars,
        "approx_attached_tokens_at_4_chars": (total_attached_chars + 3) // 4,
        "full_context_packet_visible": False,
        "note": "Provider tokenization and OpenCode internal tool turns determine billed tokens.",
    }


def resolve_optional_timeout_seconds(explicit_value: int | None, *, env_var: str) -> int | None:
    if explicit_value is not None:
        parsed_value = int(explicit_value)
        return max(1, parsed_value) if parsed_value > 0 else None
    raw_value = os.environ.get(env_var)
    if raw_value is None or not str(raw_value).strip():
        return None
    parsed_value = int(raw_value)
    return max(1, parsed_value) if parsed_value > 0 else None


def _absolute_path_no_resolve(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _unique_workspace_roots(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        raw = str(_absolute_path_no_resolve(path))
        if raw in seen:
            continue
        seen.add(raw)
        unique.append(_absolute_path_no_resolve(path))
    return unique


def _safe_session_segment(raw_value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw_value or "").strip()).strip("._")
    cleaned = cleaned or "direction"
    if len(cleaned) <= SESSION_LANE_SEGMENT_MAX_CHARS:
        return cleaned
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:20]
    prefix_length = SESSION_LANE_SEGMENT_MAX_CHARS - len(digest) - 1
    return f"{cleaned[:prefix_length]}-{digest}"


def _session_direction_root(worktree_path: Path) -> Path:
    attempt_root = worktree_path.parent.resolve()
    if re.fullmatch(r"repair_\d+", attempt_root.name) and attempt_root.parent.exists():
        return attempt_root.parent.resolve()
    return attempt_root


def _session_scope_root(worktree_path: Path) -> Path:
    attempt_root = worktree_path.parent.resolve()
    for candidate in (attempt_root, *attempt_root.parents):
        if candidate.name == "worker_loop":
            return candidate
    return _session_direction_root(worktree_path)


def _session_lane_key(experiment_id: str, direction_id: str) -> str:
    experiment_lane = re.sub(r"_attempt_\d+$", "", str(experiment_id or "").strip())
    return f"{direction_id}-{experiment_lane or 'lane'}"


def _find_session_state(session_root: Path, session_id: str | None) -> Path | None:
    if not session_id or not session_root.is_dir():
        return None
    for state_path in sorted(session_root.glob(f"*/{SESSION_STATE_FILE}")):
        state = _read_session_state(state_path)
        observed_session_id = str(state.get("observed_session_id") or "").strip()
        if session_id == observed_session_id:
            return state_path
    return None


def _read_session_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _ensure_session_workspace_alias(
    alias_path: Path,
    worktree_path: Path,
    *,
    preserved_target_file: str | None = None,
) -> Path:
    """Refresh a real, path-stable lane workspace from the current parent worktree.

    OpenCode may canonicalize junction targets before binding a session to a
    project. Retargeting a junction therefore leaves the visible ``--dir``
    unchanged while still moving the session to a different internal project.
    Keeping the workspace directory itself stable avoids that mismatch.
    """

    preserved_target_bytes: bytes | None = None
    if preserved_target_file:
        source_target = worktree_path / preserved_target_file
        existing_target = alias_path / preserved_target_file
        if not source_target.is_file() and existing_target.is_file():
            preserved_target_bytes = existing_target.read_bytes()

    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.is_symlink() or bool(
        getattr(os.path, "isjunction", lambda _: False)(os.fspath(alias_path))
    ):
        _remove_directory_link(alias_path)
    elif alias_path.exists() and not alias_path.is_dir():
        raise OSError(f"session workspace path is not a directory: {alias_path}")
    alias_path.mkdir(parents=True, exist_ok=True)

    for child in list(alias_path.iterdir()):
        if child.name == ".git" and child.is_dir():
            continue
        if child.is_symlink() or bool(
            getattr(os.path, "isjunction", lambda _: False)(os.fspath(child))
        ):
            _remove_directory_link(child)
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    source_root = worktree_path.resolve()
    for source in source_root.iterdir():
        # Never copy a changing worktree pointer or repository database. The
        # lane owns an independent Git boundary initialized below.
        if source.name == ".git":
            continue
        destination = alias_path / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    if preserved_target_bytes is not None:
        destination = alias_path / preserved_target_file
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(preserved_target_bytes)
    if not (alias_path / ".git").is_dir():
        completed = subprocess.run(
            ["git", "init", "--quiet", str(alias_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0 or not (alias_path / ".git").is_dir():
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise OSError(stderr or f"failed to initialize session workspace {alias_path}")
    return _absolute_path_no_resolve(alias_path)


def _remove_directory_link(alias_path: Path) -> None:
    if not alias_path.exists() and not alias_path.is_symlink():
        return
    if alias_path.is_symlink():
        alias_path.unlink()
        return
    is_junction = bool(getattr(os.path, "isjunction", lambda _: False)(os.fspath(alias_path)))
    if is_junction:
        os.rmdir(alias_path)
        return
    raise OSError(f"session workspace path is not a removable link: {alias_path}")


def _sync_worker_target_from_session(
    session_workspace: Path,
    worktree_path: Path,
    target_file: str,
    *,
    validate_python: bool = False,
    quarantine_invalid_source: bool = False,
) -> TargetSyncResult:
    if _absolute_path_no_resolve(session_workspace) == _absolute_path_no_resolve(worktree_path):
        return TargetSyncResult(synced=False, reason="same_workspace", quarantine_path=None)
    source = session_workspace / target_file
    destination = worktree_path / target_file
    if not source.is_file():
        return TargetSyncResult(synced=False, reason="source_missing", quarantine_path=None)
    source_bytes = source.read_bytes()
    if destination.is_file() and source_bytes == destination.read_bytes():
        return TargetSyncResult(synced=False, reason="unchanged", quarantine_path=None)
    if validate_python and source.suffix.lower() == ".py":
        validation_reason = _invalid_python_sync_reason(source)
        if validation_reason is not None:
            quarantine_path = (
                str(_quarantine_invalid_worker_target(source, session_workspace, target_file, validation_reason))
                if quarantine_invalid_source
                else None
            )
            return TargetSyncResult(
                synced=False,
                reason=validation_reason,
                quarantine_path=quarantine_path,
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.opencode-sync-{os.getpid()}-{time.time_ns()}.tmp"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except OSError:
        return TargetSyncResult(synced=False, reason="atomic_sync_failed", quarantine_path=None)
    finally:
        if temporary.exists():
            temporary.unlink()
    return TargetSyncResult(synced=True, reason="synced", quarantine_path=None)


def _is_zero_event_startup_retry_reason(reason: str | None) -> bool:
    return reason in {"zero_event_stream_timeout", "zero_event_stream_exit"}


def _invalid_python_sync_reason(source: Path) -> str | None:
    try:
        with tokenize.open(source) as source_stream:
            source_text = source_stream.read()
    except (LookupError, SyntaxError, UnicodeDecodeError):
        return "invalid_python_encoding"
    if _contains_diff_markers(source_text):
        return "diff_marker_pollution"
    try:
        py_compile.compile(str(source), doraise=True)
    except py_compile.PyCompileError:
        return "invalid_python"
    return None


def _contains_diff_markers(source_text: str) -> bool:
    for line in source_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("<<<<<<<") or stripped == "=======" or stripped.startswith(">>>>>>>"):
            return True
    return False


def _quarantine_invalid_worker_target(
    source: Path,
    session_workspace: Path,
    target_file: str,
    reason: str,
) -> Path:
    quarantine_root = (
        session_workspace.parent
        / "quarantine"
        / f"{time.time_ns()}-{_safe_session_segment(reason)}"
    )
    quarantine_path = quarantine_root / Path(target_file)
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, quarantine_path)
    return quarantine_path


def opencode_subprocess_environment(
    *, runtime_config: dict[str, object] | None = None
) -> dict[str, str]:
    """为子进程注入 provider 环境，但不把密钥文件复制进 worktree。

    这样 agent 进程能读取所需授权信息，同时隔离目录里只保留代码与日志，
    不留下额外秘密副本。
    """

    load_local_env()
    environment = os.environ.copy()
    deepseek_key = resolve_secret("DEEPSEEK_API_KEY", file_env="DEEPSEEK_API_KEY_FILE")
    if deepseek_key:
        environment["DEEPSEEK_API_KEY"] = deepseek_key
    openai_key = resolve_secret("OPENAI_API_KEY", file_env="OPENAI_API_KEY_FILE")
    if openai_key:
        environment["OPENAI_API_KEY"] = openai_key

    compatibility_config: dict[str, object] = {}
    if opencode_openai_compat_enabled():
        if deepseek_key and not openai_key:
            environment["OPENAI_API_KEY"] = deepseek_key
        compatible_base_url = environment.get("DEEPSEEK_BASE_URL", "").strip()
        if compatible_base_url:
            compatibility_config = {
                "provider": {
                    "openai": {
                        "options": {"baseURL": compatible_base_url},
                    }
                }
            }

    if runtime_config or compatibility_config:
        existing_config: dict[str, object] = {}
        existing_raw = environment.get("OPENCODE_CONFIG_CONTENT")
        if existing_raw:
            try:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict):
                    existing_config = parsed
            except json.JSONDecodeError:
                pass
        # Provider 连接参数可以继承；agent、permission、instructions、plugin、
        # MCP 和 Skill 必须从本轮干净配置重建，防止用户级配置重新开放能力。
        connection_keys = {
            "$schema",
            "provider",
            "enabled_providers",
            "disabled_providers",
            "logLevel",
        }
        safe_existing = {
            key: value for key, value in existing_config.items() if key in connection_keys
        }
        connection_config = merge_nested_dicts(compatibility_config, safe_existing)
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            merge_nested_dicts(connection_config, runtime_config or {}),
            ensure_ascii=False,
        )
    return environment


def opencode_openai_compat_enabled() -> bool:
    """Return whether a DeepSeek-named OpenAI-compatible gateway is explicit."""

    raw_value = os.environ.get(OPENCODE_OPENAI_COMPAT_ENV, "").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def opencode_openai_key_available() -> bool:
    """Check OpenAI credentials, including the explicitly enabled gateway alias."""

    return opencode_openai_key_source() is not None


def opencode_openai_key_source() -> str | None:
    """Identify the non-secret source used for the OpenAI provider."""

    load_local_env()
    if resolve_secret("OPENAI_API_KEY", file_env="OPENAI_API_KEY_FILE"):
        return "openai"
    compatible_key = resolve_secret("DEEPSEEK_API_KEY", file_env="DEEPSEEK_API_KEY_FILE")
    compatible_base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
    if opencode_openai_compat_enabled() and compatible_key and compatible_base_url:
        return "deepseek_compatible_gateway"
    return None


def merge_nested_dicts(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    """递归合并 OpenCode 配置，并让本轮安全边界覆盖同名用户设置。"""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_nested_dicts(current, value)
        else:
            merged[key] = value
    return merged


def summarize_opencode_compaction_events(events_text: str) -> dict[str, Any]:
    """Summarize only explicit top-level OpenCode compaction lifecycle events."""

    status_counts = {"started": 0, "completed": 0, "failed": 0}
    observed: list[dict[str, Any]] = []
    unknown_status_count = 0
    for line_number, line in enumerate(events_text.splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip().lower().replace("-", ".").replace("_", ".")
        status = _top_level_compaction_status(event_type, event.get("status"))
        if status is None and not _is_top_level_compaction_type(event_type):
            continue
        if status in status_counts:
            status_counts[status] += 1
        else:
            unknown_status_count += 1
        observed.append(
            {
                "line": line_number,
                "type": str(event.get("type") or "")[:120],
                "status": status or str(event.get("status") or "unknown")[:80],
            }
        )
    return {
        "schema_version": 1,
        "source": "opencode_jsonl_top_level_events",
        "event_count": len(observed),
        "started": status_counts["started"],
        "completed": status_counts["completed"],
        "failed": status_counts["failed"],
        "unknown_status_count": unknown_status_count,
        "status_counts": status_counts,
        "events": observed[:32],
        "events_truncated": len(observed) > 32,
    }


def extract_opencode_session_id(events_text: str) -> str | None:
    """Return the first explicit OpenCode session id from JSONL events."""

    for line in events_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for source in (event, event.get("part")):
            if not isinstance(source, dict):
                continue
            for key in ("sessionID", "sessionId", "session_id"):
                value = str(source.get(key) or "").strip()
                if value:
                    return value[:200]
    return None


def _is_top_level_compaction_type(event_type: str) -> bool:
    if event_type in {"compaction", "session.compacted"}:
        return True
    prefixes = ("compaction.", "session.compaction.", "session.next.compaction.")
    lifecycle_suffixes = {"start", "started", "end", "ended", "complete", "completed", "failed", "error"}
    return event_type.startswith(prefixes) and event_type.rsplit(".", 1)[-1] in lifecycle_suffixes


def _top_level_compaction_status(event_type: str, raw_status: Any) -> str | None:
    if not _is_top_level_compaction_type(event_type):
        return None
    suffix = event_type.rsplit(".", 1)[-1]
    status_value = raw_status or (suffix if event_type != "compaction" else "")
    raw = str(status_value).strip().lower()
    if raw in {"start", "started", "starting", "running"}:
        return "started"
    if raw in {"end", "ended", "complete", "completed", "compacted", "success", "succeeded"}:
        return "completed"
    if raw in {"error", "fail", "failed", "failure"}:
        return "failed"
    return None


def opencode_status(returncode: int, stdout: str, stderr: str) -> str:
    """把底层退出结果归一成 harness 可消费的 worker 状态。

    这里的 `authorization_required` 是编排层关心的特殊状态：它表示问题更
    可能出在 provider 登录/API key/余额，而不是候选代码逻辑本身。
    """
    if returncode == 0:
        return "completed"
    combined = f"{stdout}\n{stderr}".lower()
    auth_terms = [
        "authorization required",
        "authentication required",
        "not authenticated",
        "unauthorized",
        "api key",
        "insufficient balance",
    ]
    if any(term in combined for term in auth_terms):
        return "authorization_required"
    return "failed_runtime"


def retryable_opencode_provider_error(stdout: str, stderr: str = "") -> str | None:
    """Return a narrowly allowlisted transient provider-stream failure."""

    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or str(event.get("type") or "").lower() != "error":
            continue
        error = event.get("error") if isinstance(event.get("error"), dict) else {}
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        nested = data.get("message")
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except json.JSONDecodeError:
                nested = {}
        nested_error = nested.get("error") if isinstance(nested, dict) else {}
        if (
            isinstance(nested_error, dict)
            and str(nested_error.get("type") or "").lower() == "upstream_error"
            and str(nested_error.get("code") or "").lower() == "stream_read_error"
        ):
            return "stream_read_error"
    combined = f"{stdout}\n{stderr}".lower()
    if "upstream_error" in combined and "stream_read_error" in combined:
        return "stream_read_error"
    return None
