from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.worker import ExperimentSpec
from harness_agent.workers.opencode_worker import OpenCodeWorker


class OpenCodeWorkerTests(unittest.TestCase):
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
            self.assertIn("operation_level_ready_list_constructor", prompt)
            self.assertIn("sequence_dependent_setup", prompt)


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


if __name__ == "__main__":
    unittest.main()
