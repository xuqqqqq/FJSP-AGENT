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

from harness_agent.context.loader import load_context_dict
from ..deepseek_client import load_local_env, resolve_secret
from harness_agent.core.runner import CREATE_NEW_PROCESS_GROUP, kill_process_tree
from harness_agent.context.worker import worker_context_sections
from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult


class OpenCodeWorker(CodingWorker):
    """默认的直接执行型 Worker 运行时。

    与 DeepSeekWorker 返回结构化 proposal 再由 harness 落地不同，
    OpenCodeWorker 让外部 agent 直接在 `spec.worktree_path` 内编辑。
    因此这里的职责重点不是 JSON 规范化，而是：
    1. 固化本轮 prompt 和命令行参数；
    2. 约束子进程生命周期与超时；
    3. 保存可审计产物，便于主流程判断是否接受候选。
    """

    def __init__(self, executable: str = "opencode", model: str | None = None) -> None:
        self.executable = executable
        executable_path = Path(executable)
        self.executable_path = str(executable_path.resolve()) if executable_path.exists() else shutil.which(executable)
        self.model = model or os.environ.get("OPENCODE_MODEL")
        self.run_command = os.environ.get("OPENCODE_RUN_COMMAND", "run")

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

        `ExperimentSpec` 中最关键的三段数据流是：
        - `context_packet_path`：只读上下文，供 prompt 引导 agent；
        - `worktree_path`：唯一允许被 agent 直接修改的候选目录；
        - `output_dir`：worker 自己的提示词、命令、日志、stderr 归档位。

        返回值只描述本次 worker 进程是否正常结束，不直接声明候选算法已
        通过 evaluator。
        """
        output_dir = Path(spec.output_dir) if spec.output_dir else Path(spec.worktree_path) / ".algoforge_worker" / spec.experiment_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.executable_path is None:
            return WorkerResult(
                status="unavailable",
                changed_files=[],
                summary=f"OpenCode executable {self.executable!r} was not found on PATH.",
                artifacts={"output_dir": str(output_dir)},
            )

        prompt = self._prompt(spec)
        prompt_path = output_dir / "opencode_prompt.md"
        stdout_path = output_dir / "opencode.stdout.txt"
        stderr_path = output_dir / "opencode.stderr.txt"
        command_path = output_dir / "opencode_command.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        command = self._command(prompt_path)
        command_path.write_text(json_dumps(command), encoding="utf-8")

        # OpenCode 直接改 worktree，所以 stdout/stderr 不是装饰性日志，
        # 而是回溯本轮行为、定位超时/鉴权失败的第一手证据。
        popen_kwargs: dict[str, object] = {
            "cwd": spec.worktree_path,
            "env": opencode_subprocess_environment(),
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = process.communicate(timeout=max(1, spec.max_runtime_seconds))
        except subprocess.TimeoutExpired as exc:
            # 超时后必须显式结束整棵进程树，避免子进程继续占用 worktree、
            # 文件句柄或外部 provider 连接。
            kill_process_tree(process)
            stdout, stderr = process.communicate()
            stdout_path.write_text(stdout or exc.stdout or "", encoding="utf-8")
            stderr_path.write_text(stderr or exc.stderr or "", encoding="utf-8")
            return WorkerResult(
                status="timeout",
                changed_files=[],
                summary=f"OpenCode exceeded {spec.max_runtime_seconds} seconds.",
                raw_log_path=str(stdout_path),
                artifacts={
                    "output_dir": str(output_dir),
                    "prompt": str(prompt_path),
                    "stderr": str(stderr_path),
                    "command": str(command_path),
                },
            )

        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
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
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "command": str(command_path),
            },
        )

    def _command(self, prompt_path: Path) -> list[str]:
        """构造最终命令行。

        这里不把 prompt 内容直接塞进 argv，而是只传一个“读取某个文件”的
        指令，确保完整 worker 指令文本能原样保存在产物目录，便于审计。
        """
        command = [str(self.executable_path)]
        if self.run_command:
            command.extend(shlex.split(self.run_command, posix=False))
        if self.model:
            command.extend(["--model", self.model])
        prompt = f"Read and follow the worker instructions in this file: {prompt_path}"
        command.append(prompt)
        return command

    def _prompt(self, spec: ExperimentSpec) -> str:
        """生成给 OpenCode 的完整 worker 提示词。

        stable/dynamic context 在这里以预裁剪后的 JSON 文本注入。该函数
        只负责编排 worker 可见上下文，不参与任何结果验收逻辑。
        """
        context_sections = self._context_sections(spec)
        local_inputs = self._local_input_hint(spec)
        return f"""
You are running inside an AlgoForge worker cycle.

Read the context packet at:
{spec.context_packet_path}

Worktree:
{spec.worktree_path}

Task:
- State a concise natural-language strategy in your own working notes if useful.
- Make at most {max(1, spec.max_steps)} small code-edit steps.
- Modify only files allowed by the context packet edit_policy.
- Do not edit forbidden paths such as .git or outputs.
- Do not claim benchmark success. The harness will snapshot the worktree, run
  the fixed evaluator, and decide whether this candidate is promoted.
- Worker execution is intentionally narrow. You may run one compile check and
  at most one fixed-seed short smoke using the first active instance. Do not run
  the formal evaluator command, any benchmark command, multiple seeds, repeated
  solver trials, the full test suite, or parameter sweeps. Core owns all formal
  and multi-seed evaluation.
- The first active instance is mirrored inside the worktree for read-only use:
  {local_inputs}
  Use this local mirror for inspection and the single worker smoke. Do not ask
  for access to the original instance path when it is outside the worktree.
- Agent-generated solver entrypoints must accept `--time-limit-sec`. Treat it
  as one shared wall-clock budget and return comfortably before it expires.
  Check the deadline inside nested candidate scans, not only between restarts
  or outer iterations. Bound every graph/sequence traversal by a visited set or
  the parsed operation count.
- Apply neighborhood moves transactionally: mutate a clone/snapshot, fully
  decode and validate it, then commit it. A failed move must not leave the
  current state, machine links, assignment, or sequences partially mutated.
- Run at most one solver smoke, with `--time-limit-sec` no greater than 3
  seconds. If it fails or times out, stop testing and leave the concrete error
  for Core repair feedback; do not retry with inline loops or a longer budget.
- Prefer a complete, reversible solver improvement over broad rewrites.
- Treat `loop_feedback.current_direction_plan` as the Main Agent experiment
  contract. Implement that direction and keep same-direction repairs inside its
  change scope instead of switching to an unrelated method.
- When `active_method_package` is present, read its assets and adapt that one
  package to the active IO/CLI. Preserve its executable decoder, neighborhood,
  tabu/aspiration, adaptive-search, and diversification structure. Do not blend
  a second method family into the same direction or reduce the package to a
  random hill climber while retaining the stronger method name.
- When `loop_feedback.current_direction_plan.implementation_bundle` is present,
  treat it as one complete method implementation contract. Before editing,
  enumerate every required component and coupled group in your working plan.
  Implement the whole bundle coherently in this direction; do not stop after
  fixing one convenient finding while other required components remain absent.
- During same-direction repair, use the latest
  `repair_targets.algorithm_semantic_review.implementation_coverage` matrix as
  the component remaining-work list and `coupled_group_coverage` as the
  integration remaining-work list. Preserve entries marked implemented and
  repair every missing or partial entry before claiming completion.
- If the priority context says `agent_generated_solver_quality_contract.enabled`
  is true, create or edit the standalone solver entrypoint referenced by the
  context packet's `evaluator_protocol.solver_command_template`. The code must
  satisfy the listed parser, representation, decoder, coverage, eligibility,
  precedence, non-overlap, runtime-bound, and incumbent-preservation
  capabilities before optimizing objective value.
- If priority context `round_type` is `baseline_or_single_round`, create or
  replace the complete generated solver entrypoint when needed; do not preserve
  a nonexistent incumbent. If `round_type` is `improvement_round`, preserve the
  promoted incumbent skeleton unless the context says legality repair is
  required.
- For agent-generated baselines, stdout may include a short self-check, but the
  decisive evidence is the actual code diff. Cite real function/variable/guard
  names that exist in the file you changed. If you mention representation,
  decoder, variant handling, runtime bounds, or incumbent preservation in notes
  or self-check output, anchor each claim to source symbols in the changed file.
- Because this worker edits files directly instead of returning structured
  proposal JSON, include a source-level validation helper such as
  `validate_schedule(...)` or `self_check_solution(...)` in the generated solver
  and call it before writing output. It must reject missing/duplicate
  operations, ineligible machines, duration mismatches, precedence violations,
  and machine overlaps. It must also verify every active variant capability
  listed under `agent_generated_solver_quality_contract.variant_required_code_capabilities`.
  For example, sequence-dependent setup requires setup-aware same-machine arcs
  such as `start >= prev_end + setup_time(...)`; no-wait requires successor
  starts to match predecessor completion; release dates require starts no
  earlier than the parsed release time.
- Any parser, decoder, schedule builder, or validation/self-check helper you
  define must be called by the runnable flow before the solution file is
  written. Do not leave helper functions unused as evidence-only scaffolding.
- Keep every quick-test solution, metrics file, and scratch artifact inside the
  current worktree, for example under `.algoforge_worker_tmp/`. Never use
  `%TEMP%`, `$env:TEMP`, `/tmp`, or any external directory that would require a
  permission prompt. Finish after the bounded quick test instead of continuing
  open-ended exploration.
- Before finishing, verify that the solver entrypoint named by
  `evaluator_protocol.solver_command_template` exists and is non-empty. If it
  does not, continue implementing unless a concrete blocker makes editing
  impossible.

Stable task context:
```json
{context_sections["stable"]}
```

Priority context (dynamic tail):
```json
{context_sections["dynamic"]}
```

If no safe edit is possible, leave the worktree unchanged and explain why in
stdout.  The harness will record your stdout/stderr and the worktree delta.
""".strip()

    def _local_input_hint(self, spec: ExperimentSpec) -> str:
        """把本地镜像实例路径压缩成简短提示，避免 agent 请求越界原始路径。"""
        manifest_path = Path(spec.worktree_path) / ".algoforge_worker_inputs" / "manifest.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return ".algoforge_worker_inputs/instances/ (when present)"
        paths = [
            str(item.get("local_path") or "")
            for item in payload.get("instances") or []
            if isinstance(item, dict) and item.get("local_path")
        ]
        return ", ".join(paths) if paths else ".algoforge_worker_inputs/instances/ (when present)"

    def _context_sections(self, spec: ExperimentSpec) -> dict[str, str]:
        """把上下文包切成 stable/dynamic 两段，供 prompt 明确区分。

        stable 部分偏任务常量，dynamic 部分偏当前轮反馈和修复尾部。即使
        裁剪失败，也返回兜底 JSON，保证默认运行时仍可继续读取原始包。
        """
        try:
            context = load_context_dict(Path(spec.context_packet_path))
        except (OSError, json.JSONDecodeError, ValueError):
            return {"stable": "{}", "dynamic": "{}"}
        try:
            return worker_context_sections(context)
        except Exception as exc:  # noqa: BLE001 - OpenCode can still read the raw context packet.
            fallback = json_dumps({"status": "unavailable", "reason": str(exc)}).strip()
            return {"stable": "{}", "dynamic": fallback}


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def opencode_subprocess_environment() -> dict[str, str]:
    """为子进程注入 provider 环境，但不把密钥文件复制进 worktree。

    这样 agent 进程能读取所需授权信息，同时隔离目录里只保留代码与日志，
    不留下额外秘密副本。
    """

    load_local_env()
    environment = os.environ.copy()
    deepseek_key = resolve_secret("DEEPSEEK_API_KEY", file_env="DEEPSEEK_API_KEY_FILE")
    if deepseek_key:
        environment["DEEPSEEK_API_KEY"] = deepseek_key
    return environment


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
