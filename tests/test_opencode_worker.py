from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness_agent.worker import ExperimentSpec
from harness_agent.workers.opencode_worker import OpenCodeWorker, opencode_status, opencode_subprocess_environment


class OpenCodeWorkerTests(unittest.TestCase):
    def test_opencode_environment_resolves_deepseek_key_file_without_copying_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            key_file = tmp_path / "deepseek.key"
            env_file = tmp_path / ".env.test"
            key_file.write_text("test-opencode-key\n", encoding="utf-8")
            env_file.write_text(
                f'DEEPSEEK_API_KEY=\nDEEPSEEK_API_KEY_FILE="{key_file}"\n',
                encoding="utf-8",
            )
            previous = {
                key: os.environ.get(key)
                for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_FILE", "FJSP_AGENT_ENV_FILE")
            }
            try:
                os.environ.pop("DEEPSEEK_API_KEY", None)
                os.environ.pop("DEEPSEEK_API_KEY_FILE", None)
                os.environ["FJSP_AGENT_ENV_FILE"] = str(env_file)

                environment = opencode_subprocess_environment()
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual("test-opencode-key", environment["DEEPSEEK_API_KEY"])

    def test_opencode_status_classifies_authorization_failures(self) -> None:
        self.assertEqual(
            "authorization_required",
            opencode_status(1, "", "Error: Authorization Required"),
        )
        self.assertEqual("failed_runtime", opencode_status(1, "", "Error: command failed"))

    def test_fake_opencode_executable_runs_against_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            worktree = tmp_path / "worktree"
            output_dir = tmp_path / "worker"
            worktree.mkdir()
            context_packet = tmp_path / "context_packet.json"
            context_packet.write_text(
                '{"edit_policy":{"allowed_paths":["examples"],"forbidden_paths":[".git","outputs"]}}',
                encoding="utf-8",
            )
            fake_executable = _write_fake_opencode(tmp_path)

            result = OpenCodeWorker(executable=str(fake_executable), model="fake/model").run_experiment(
                ExperimentSpec(
                    task_id="test",
                    experiment_id="fake_opencode",
                    context_packet_path=str(context_packet),
                    worktree_path=str(worktree),
                    max_steps=2,
                    max_runtime_seconds=30,
                    output_dir=str(output_dir),
                    apply_changes=False,
                )
            )

            self.assertEqual("completed", result.status)
            self.assertTrue((worktree / "examples" / "opencode_marker.txt").exists())
            self.assertTrue((output_dir / "opencode_prompt.md").exists())
            self.assertIn("fake/model", (output_dir / "opencode_command.json").read_text(encoding="utf-8"))
            self.assertIn("fake opencode executed", (output_dir / "opencode.stdout.txt").read_text(encoding="utf-8"))

    def test_opencode_prompt_includes_agent_generated_priority_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            worktree = tmp_path / "worktree"
            output_dir = tmp_path / "worker"
            worktree.mkdir()
            context_packet = tmp_path / "context_packet.json"
            context_packet.write_text(
                "\n".join(
                    [
                        "{",
                        '  "task": {"problem_family": "FJSP", "description": "agent_generated FJSP-SDST run"},',
                        '  "evaluator_protocol": {',
                        '    "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",',
                        '    "evaluator_command_template": "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}"',
                        "  },",
                        '  "instance_diagnostics": {',
                        '    "status": "available",',
                        '    "summary": {"sdst_instance_count": 1, "setup_time_kinds": ["job_pair"]}',
                        "  },",
                        '  "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": [".git", "outputs"]}',
                        "}",
                    ]
                ),
                encoding="utf-8",
            )
            fake_executable = _write_fake_opencode(tmp_path)

            result = OpenCodeWorker(executable=str(fake_executable)).run_experiment(
                ExperimentSpec(
                    task_id="test",
                    experiment_id="fake_opencode_quality",
                    context_packet_path=str(context_packet),
                    worktree_path=str(worktree),
                    max_steps=2,
                    max_runtime_seconds=30,
                    output_dir=str(output_dir),
                    apply_changes=False,
                )
            )

            self.assertEqual("completed", result.status)
            prompt = (output_dir / "opencode_prompt.md").read_text(encoding="utf-8")
            self.assertIn("Priority context", prompt)
            self.assertIn("agent_generated_solver_quality_contract", prompt)
            self.assertIn("standalone solver entrypoint", prompt)
            self.assertIn("evaluator_protocol.solver_command_template", prompt)
            self.assertIn("actual code diff", prompt)
            self.assertIn("baseline_or_single_round", prompt)
            self.assertIn("do not preserve", prompt)
            self.assertIn("improvement_round", prompt)
            self.assertIn("source-level validation helper", prompt)
            self.assertIn("validate_schedule", prompt)
            self.assertIn("decoder, variant handling", prompt)
            self.assertIn("anchor each claim to source symbols", prompt)
            self.assertIn("before the solution file is", prompt)
            self.assertIn("evidence-only scaffolding", prompt)
            self.assertIn(".algoforge_worker_tmp", prompt)
            self.assertIn("Never use", prompt)
            self.assertIn("external directory", prompt)
            self.assertIn("variant_required_code_capabilities", prompt)
            self.assertIn("setup-aware same-machine arcs", prompt)
            self.assertIn("release dates require starts", prompt)
            self.assertIn("operation_level_ready_list_constructor", prompt)
            self.assertIn("sequence_dependent_setup", prompt)

    def test_timeout_kills_opencode_child_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            worktree = tmp_path / "worktree"
            output_dir = tmp_path / "worker"
            worktree.mkdir()
            context_packet = tmp_path / "context_packet.json"
            context_packet.write_text("{}", encoding="utf-8")
            executable, pid_path = _write_hanging_opencode(tmp_path)

            result = OpenCodeWorker(executable=str(executable)).run_experiment(
                ExperimentSpec(
                    task_id="test",
                    experiment_id="timeout_tree",
                    context_packet_path=str(context_packet),
                    worktree_path=str(worktree),
                    max_steps=1,
                    max_runtime_seconds=1,
                    output_dir=str(output_dir),
                    apply_changes=False,
                )
            )

            self.assertEqual("timeout", result.status)
            deadline = time.time() + 5
            while not pid_path.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(pid_path.exists(), "fake OpenCode did not record its child pid")
            child_pid = int(pid_path.read_text(encoding="utf-8"))
            deadline = time.time() + 5
            while _process_exists(child_pid) and time.time() < deadline:
                time.sleep(0.1)
            self.assertFalse(_process_exists(child_pid), f"OpenCode child process {child_pid} survived timeout")


def _write_fake_opencode(tmp_path: Path) -> Path:
    script = tmp_path / "fake_opencode.py"
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "Path('examples').mkdir(exist_ok=True)",
                "Path('examples/opencode_marker.txt').write_text('created by fake opencode\\n', encoding='utf-8')",
                "print('fake opencode executed')",
                "print('args=' + repr(sys.argv[1:]))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp_path / "fake_opencode.cmd"
        wrapper.write_text(f'@echo off\n"{sys.executable}" "{script}"\n', encoding="utf-8")
    else:
        wrapper = tmp_path / "fake_opencode"
        wrapper.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


def _write_hanging_opencode(tmp_path: Path) -> tuple[Path, Path]:
    child = tmp_path / "hanging_child.py"
    child.write_text("import time\nwhile True:\n    time.sleep(1)\n", encoding="utf-8")
    pid_path = tmp_path / "hanging_child.pid"
    parent = tmp_path / "hanging_opencode.py"
    parent.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import subprocess",
                "import sys",
                "import time",
                f"child = subprocess.Popen([sys.executable, {str(child)!r}])",
                f"Path({str(pid_path)!r}).write_text(str(child.pid), encoding='utf-8')",
                "while True:",
                "    time.sleep(1)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp_path / "hanging_opencode.cmd"
        wrapper.write_text(f'@echo off\n"{sys.executable}" "{parent}"\n', encoding="utf-8")
    else:
        wrapper = tmp_path / "hanging_opencode"
        wrapper.write_text(f'#!/bin/sh\n"{sys.executable}" "{parent}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper, pid_path


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            capture_output=True,
            check=False,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    unittest.main()
