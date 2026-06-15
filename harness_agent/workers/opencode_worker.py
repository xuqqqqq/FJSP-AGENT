from __future__ import annotations

import shutil

from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult


class OpenCodeWorker(CodingWorker):
    """Adapter boundary for using OpenCode as a future coding agent backend."""

    def __init__(self, executable: str = "opencode") -> None:
        self.executable = executable
        self.executable_path = shutil.which(executable)

    def capabilities(self) -> WorkerCapabilities:
        available = self.executable_path is not None
        return WorkerCapabilities(
            name="opencode" if available else "opencode_unavailable",
            supports_code_generation=available,
            supports_repair=available,
            supports_structured_output=False,
        )

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        if self.executable_path is None:
            return WorkerResult(
                status="unavailable",
                changed_files=[],
                summary=f"OpenCode executable {self.executable!r} was not found on PATH.",
            )
        return WorkerResult(
            status="not_implemented",
            changed_files=[],
            summary="OpenCode adapter is detected but candidate-generation commands are not wired yet.",
        )
