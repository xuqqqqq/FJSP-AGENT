from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
from pathlib import Path

from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult


class OpenCodeWorker(CodingWorker):
    """Run OpenCode as a guarded non-interactive coding backend."""

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
        command = self._command(prompt)
        command_path.write_text(json_dumps(command), encoding="utf-8")

        try:
            completed = subprocess.run(
                command,
                cwd=spec.worktree_path,
                text=True,
                capture_output=True,
                timeout=max(1, spec.max_runtime_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text(exc.stderr or "", encoding="utf-8")
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

        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        status = "completed" if completed.returncode == 0 else "failed_runtime"
        summary = f"OpenCode exited with code {completed.returncode}. Harness diff/evaluator artifacts decide acceptance."
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

    def _command(self, prompt: str) -> list[str]:
        command = [str(self.executable_path)]
        if self.run_command:
            command.extend(shlex.split(self.run_command, posix=False))
        if self.model:
            command.extend(["--model", self.model])
        command.append(prompt)
        return command

    def _prompt(self, spec: ExperimentSpec) -> str:
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
- Prefer a complete, reversible solver improvement over broad rewrites.

If no safe edit is possible, leave the worktree unchanged and explain why in
stdout.  The harness will record your stdout/stderr and the worktree delta.
""".strip()


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
