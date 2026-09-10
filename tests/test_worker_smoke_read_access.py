from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harness_agent.worker import ExperimentSpec, WorkerAssignment
from harness_agent.workers.opencode_worker import (
    OPENCODE_WORKER_AGENT,
    WORKER_RUNTIME_POLICY_MAX_CHARS,
    OpenCodeSessionLaunch,
    OpenCodeWorker,
)
from tests.test_opencode_worker import _write_assignment


class WorkerSmokeReadAccessTests(unittest.TestCase):
    def test_smoke_outputs_are_readable_without_widening_write_or_shell_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            session = root / "session"
            worktree.mkdir()
            session.mkdir()
            runtime = worktree / ".algoforge_worker_runtime"
            runtime.mkdir()
            (runtime / "run_smoke.py").touch()
            assignment = WorkerAssignment.load(_write_assignment(root, worktree))
            spec = ExperimentSpec("test", "smoke-access", "context.json", str(worktree), 4, 300)
            config = OpenCodeWorker()._runtime_config(
                spec, assignment=assignment, attachment_paths=[],
                workspace_roots=[worktree, session],
            )
            permissions = config["agent"][OPENCODE_WORKER_AGENT]["permission"]
            for name in ("smoke_solution.json", "smoke.used"):
                relative = f".algoforge_worker_runtime/{name}"
                for path in (relative, (worktree / relative).as_posix(), (session / relative).as_posix()):
                    self.assertEqual("allow", permissions["read"].get(path))
                    self.assertNotIn(path, permissions["edit"])
                self.assertNotIn((root / "outside" / relative).as_posix(), permissions["read"])
            for path in (".algoforge_worker_runtime/**", ".algoforge_worker_runtime/other.json"):
                self.assertNotIn(path, permissions["read"])
            self.assertEqual("deny", permissions["read"]["*"])
            self.assertEqual("deny", permissions["edit"]["*"])
            self.assertEqual("deny", permissions["external_directory"]["*"])
            self.assertEqual("deny", permissions["bash"]["*"])
            self.assertNotIn("python -c *", permissions["bash"])
            self.assertNotIn("python .algoforge_worker_runtime/run_smoke.py && echo OK", permissions["bash"])
            (runtime / "run_smoke.py").unlink()
            without_smoke = OpenCodeWorker()._runtime_config(
                spec, assignment=assignment, attachment_paths=[],
            )["agent"][OPENCODE_WORKER_AGENT]["permission"]
            self.assertNotIn(".algoforge_worker_runtime/smoke_solution.json", without_smoke["read"])
            self.assertNotIn("python .algoforge_worker_runtime/run_smoke.py", without_smoke["bash"])

    def test_improvement_prompt_exposes_exact_checks_and_early_patch_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assignment = replace(WorkerAssignment.load(_write_assignment(root, root)), mode="improvement")
            spec = ExperimentSpec("test", "smoke-access", "context.json", str(root), 4, 300)
            launch = OpenCodeSessionLaunch(root, None, None, "fresh", None, "test", root / "state.json", None, root, None)
            prompt = OpenCodeWorker()._prompt(spec, assignment=assignment, session_launch=launch)
            self.assertIn(f"python -m py_compile {assignment.target_file}", prompt)
            self.assertIn("python .algoforge_worker_runtime/run_smoke.py", prompt)
            self.assertIn("smoke_solution.json", prompt)
            self.assertIn("smoke.used", prompt)
            self.assertIn("diagnostics", prompt)
            self.assertIn("small runnable patch", prompt)
            self.assertIn("Do not append", prompt)
            self.assertLessEqual(len(prompt), WORKER_RUNTIME_POLICY_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
