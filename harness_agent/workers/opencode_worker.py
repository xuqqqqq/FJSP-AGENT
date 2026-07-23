"""OpenCode Coding Agent 适配器。

这里承载的是当前 harness 侧最直接的代码运行时：把 `ExperimentSpec`
翻译成一次受控的命令行调用，让外部 Coding Agent 在隔离 worktree 内
直接改文件，然后把 prompt、命令、stdout/stderr 和超时结果落盘，供
后续审计、diff、smoke gate 与 evaluator 复盘。
"""

from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
from pathlib import Path

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
OPENCODE_WORKER_AGENT = "algoforge-worker"
MIN_OPENCODE_AGENT_STEPS = 8
MAX_OPENCODE_AGENT_STEPS = 16
WORKER_RUNTIME_POLICY_MAX_CHARS = 4_000
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

    def capabilities(self) -> WorkerCapabilities:
        available = self.executable_path is not None
        return WorkerCapabilities(
            name="opencode" if available else "opencode_unavailable",
            supports_code_generation=available,
            supports_repair=available,
            supports_structured_output=False,
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

        prompt = self._prompt(spec, assignment=assignment)
        prompt_path = output_dir / "opencode_prompt.md"
        budget_path = output_dir / "opencode_context_budget.json"
        stdout_path = output_dir / "opencode.stdout.txt"
        events_path = output_dir / "opencode_events.jsonl"
        stderr_path = output_dir / "opencode.stderr.txt"
        command_path = output_dir / "opencode_command.json"
        runtime_config_path = output_dir / "opencode_runtime_config.json"
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
        )
        runtime_config_path.write_text(json_dumps(runtime_config), encoding="utf-8")
        command = self._command(
            prompt_path,
            assignment_path,
            worktree_path=Path(spec.worktree_path),
        )
        command_path.write_text(json_dumps(command), encoding="utf-8")

        # OpenCode 直接改 worktree，所以 stdout/stderr 不是装饰性日志，
        # 而是回溯本轮行为、定位超时/鉴权失败的第一手证据。
        timed_out = False
        # OpenCode emits one JSON object per line. Writing its streams directly
        # to disk lets the Web monitor expose public commentary while the model
        # is still working, instead of dumping the whole trace after exit.
        with (
            events_path.open("w", encoding="utf-8", buffering=1) as events_stream,
            stderr_path.open("w", encoding="utf-8", buffering=1) as stderr_stream,
        ):
            popen_kwargs: dict[str, object] = {
                "cwd": spec.worktree_path,
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
            process = subprocess.Popen(command, **popen_kwargs)
            registration = (
                self.cancellation.register_terminator(lambda: kill_process_tree(process))
                if self.cancellation is not None
                else None
            )
            try:
                process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                # 超时后必须显式结束整棵进程树，避免子进程继续占用 worktree、
                # 文件句柄或外部 provider 连接。
                kill_process_tree(process)
                try:
                    process.wait(timeout=5)
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
        stdout_path.write_text(stdout, encoding="utf-8")
        if timed_out:
            return WorkerResult(
                status="timeout",
                changed_files=[],
                summary=f"OpenCode exceeded {self.timeout_seconds} seconds.",
                raw_log_path=str(stdout_path),
                artifacts={
                    "output_dir": str(output_dir),
                    "prompt": str(prompt_path),
                    "context_budget": str(budget_path),
                    "stderr": str(stderr_path),
                    "command": str(command_path),
                    "runtime_config": str(runtime_config_path),
                    "worker_assignment": str(assignment_path),
                    "events": str(events_path),
                },
            )

        cleanup_process_descendants(process)
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()

        status = opencode_status(process.returncode, stdout, stderr)
        summary = f"OpenCode exited with code {process.returncode}. Harness diff/evaluator artifacts decide acceptance."
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
                "worker_assignment": str(assignment_path),
                "events": str(events_path),
            },
        )

    def _command(self, prompt_path: Path, assignment_path: Path, *, worktree_path: Path) -> list[str]:
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
        command.extend(
            [
                "--agent",
                OPENCODE_WORKER_AGENT,
                "--format",
                "json",
                f"--dir={worktree_path.resolve()}",
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
    ) -> dict[str, object]:
        """给 OpenCode 注入只对本次 worker 生效的硬执行边界。

        提示词中的 ``max_steps`` 只是自然语言约束，OpenCode 不会据此限制
        自己的工具轮次。这里用当前安装版本支持的 ``task: deny`` 真正禁止
        子 Agent，并为读取、编辑、编译和一次 smoke 留出有限的主 Agent 步数。
        """
        requested_steps = max(1, int(spec.max_steps)) * 3
        agent_steps = min(MAX_OPENCODE_AGENT_STEPS, max(MIN_OPENCODE_AGENT_STEPS, requested_steps))
        worktree_path = Path(spec.worktree_path).resolve()
        read_permissions: dict[str, str] = {"*": "deny"}
        self._allow_worktree_path(read_permissions, worktree_path, assignment.target_file)
        for item in assignment.read_set:
            self._allow_worktree_path(read_permissions, worktree_path, str(item.get("path") or ""))
        skill_permissions: str | dict[str, str] = "deny"
        if assignment.implementation_skills:
            skill_permissions = {"*": "deny"}
            for item in assignment.implementation_skills:
                skill_id = str(item.get("skill_id") or "").strip()
                sandbox_path = str(item.get("sandbox_path") or "").strip()
                skill_permissions[skill_id] = "allow"
                self._allow_worktree_tree(read_permissions, worktree_path, sandbox_path)
        edit_permissions: dict[str, str] = {"*": "deny"}
        self._allow_worktree_path(edit_permissions, worktree_path, assignment.target_file)
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
    def _allow_worktree_path(permissions: dict[str, str], worktree_path: Path, relative: str) -> None:
        """Allow one path in the forms emitted by OpenCode on Windows and POSIX."""

        normalized = relative.replace("\\", "/").strip()
        if not normalized:
            return
        absolute = (worktree_path / normalized).resolve()
        for pattern in (normalized, str(absolute), absolute.as_posix()):
            permissions[pattern] = "allow"

    @staticmethod
    def _allow_worktree_tree(permissions: dict[str, str], worktree_path: Path, relative: str) -> None:
        normalized = relative.replace("\\", "/").strip().rstrip("/")
        if not normalized:
            return
        absolute = (worktree_path / normalized).resolve()
        for pattern in (
            normalized,
            f"{normalized}/**",
            str(absolute),
            f"{absolute}{os.sep}**",
            absolute.as_posix(),
            f"{absolute.as_posix()}/**",
        ):
            permissions[pattern] = "allow"

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

    def _prompt(self, spec: ExperimentSpec, *, assignment: WorkerAssignment) -> str:
        """返回短而稳定的执行协议；动态规划内容只存在于 JSON 任务书。"""

        target_file_policy = (
            "This is baseline mode. `target_file` is explicitly authorized but may not exist yet; "
            "if it is absent, create it and continue instead of reporting a missing-input blocker."
            if assignment.mode == "baseline"
            else "This is improvement/repair mode. `target_file` is the required incumbent; read it before editing and preserve unrelated working behavior."
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
- assignment_id: {assignment.assignment_id}
- direction_id: {assignment.direction_id}
- mode: {assignment.mode}
- worktree: {spec.worktree_path}
""".strip()
        if len(prompt) > WORKER_RUNTIME_POLICY_MAX_CHARS:
            raise ValueError(
                f"worker runtime policy exceeds {WORKER_RUNTIME_POLICY_MAX_CHARS} chars: {len(prompt)}"
            )
        return prompt


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
    if runtime_config:
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
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            merge_nested_dicts(safe_existing, runtime_config),
            ensure_ascii=False,
        )
    return environment


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
