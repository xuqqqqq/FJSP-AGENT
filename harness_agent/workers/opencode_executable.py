"""Resolve OpenCode without executing npm shims or parsing shell commands."""

from __future__ import annotations

import platform
import re
import shutil
import warnings
from pathlib import Path


def resolve_opencode_executable(executable: str = "opencode") -> str | None:
    """Return an existing path or PATH match, preferring native Windows defaults.

    Callers resolve configuration/environment overrides before calling this helper.
    Explicit paths and custom command names retain their original selection.
    """
    path = Path(executable)
    if path.is_file():
        return str(path.resolve())
    resolved = shutil.which(executable)
    if resolved is None or executable != "opencode" or platform.system() != "Windows":
        return resolved
    shim = Path(resolved)
    if shim.name.lower() not in {"opencode", "opencode.cmd", "opencode.bat", "opencode.ps1"}:
        return resolved

    packages = [shim.parent / "node_modules" / "opencode-ai"]
    if shim.parent.name.lower() == ".bin":
        packages.append(shim.parent.parent / "opencode-ai")
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        # Baseline is safe without probing CPU instruction support via a shell.
        platform_packages = ("opencode-windows-x64-baseline", "opencode-windows-x64")
    elif machine in {"arm64", "aarch64"}:
        platform_packages = ("opencode-windows-arm64",)
    else:
        platform_packages = ()
    for package in packages:
        candidates = [package / "bin" / "opencode.exe"]
        for dependencies in (package / "node_modules", package.parent):
            candidates.extend(dependencies / name / "bin" / "opencode.exe" for name in platform_packages)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
    return resolved


def normalize_opencode_prompt(prompt: str, executable_path: str | None) -> str:
    """Keep a Windows batch fallback from dropping arguments after a newline."""
    if (
        platform.system() != "Windows"
        or executable_path is None
        or Path(executable_path).suffix.lower() not in {".cmd", ".bat"}
        or not any(char in prompt for char in "\r\n")
    ):
        return prompt
    warnings.warn(
        "OpenCode is using a Windows batch wrapper; replacing prompt CR/LF with "
        "spaces to prevent prompt and attachment argument truncation. "
        "Use the native opencode.exe to preserve multiline prompts.",
        RuntimeWarning,
        stacklevel=2,
    )
    return re.sub(r"[\r\n]+", " ", prompt)
