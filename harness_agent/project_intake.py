from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TaskContract, resolve_project_path


EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".omx",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "outputs",
    "venv",
}

DEPENDENCY_FILE_NAMES = {
    "environment.yml",
    "environment.yaml",
    "package-lock.json",
    "package.json",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}

TEXT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".csv",
    ".fjs",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

DATA_DIR_NAMES = {
    "benchmark",
    "benchmarks",
    "config",
    "configs",
    "data",
    "dataset",
    "datasets",
    "example",
    "examples",
    "instance",
    "instances",
    "testdata",
}


@dataclass(frozen=True)
class ProjectIntakeRequest:
    """Request for a bounded, read-only project intake scan."""

    project_root: Path
    output_dir: Path
    contract_path: Path | None = None
    max_files: int = 200
    max_symbols_per_file: int = 20


def write_project_intake(request: ProjectIntakeRequest) -> dict[str, Any]:
    """Scan a project and write a manifest/report for downstream coding agents."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "project_intake_manifest.json"
    report_path = output_dir / "project_intake_report.md"

    project_root = request.project_root.resolve()
    if not project_root.exists():
        manifest = {
            "schema_version": 1,
            "status": "failed",
            "project_root": str(project_root),
            "errors": [f"project root does not exist: {project_root}"],
            "artifacts": {
                "manifest": str(manifest_path.resolve()),
                "report": str(report_path.resolve()),
            },
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.write_text(render_project_intake_report(manifest), encoding="utf-8")
        return manifest

    contract = load_contract(request.contract_path)
    files, truncated = collect_project_files(project_root, scan_limit=max(1000, request.max_files * 20))
    directories = collect_project_dirs(project_root, scan_limit=max(1000, request.max_files * 10))
    language_summary = summarize_languages(files)
    git_summary = summarize_git(project_root)
    dependency_files = match_dependency_files(files)
    validator_files = match_keyword_files(
        project_root,
        files,
        {"eval", "evaluator", "valid", "validate", "validator", "check"},
    )
    benchmark_files = match_keyword_files(project_root, files, {"benchmark", "suite", "runner", "demo"})
    command_files = command_referenced_files(project_root, contract)
    entry_files = rank_entry_files(files, command_files)
    core_algorithm_files = rank_core_algorithm_files(project_root, files)
    test_commands = infer_test_commands(project_root, files, contract)
    data_dirs = match_data_dirs(project_root, directories)
    output_hints = output_format_hints(contract)
    edit_policy = edit_policy_summary(contract)
    selected_files = select_context_files(
        command_files=command_files,
        entry_files=entry_files,
        core_files=core_algorithm_files,
        dependency_files=dependency_files,
        benchmark_files=benchmark_files,
        validator_files=validator_files,
        files=files,
        max_files=max(1, request.max_files),
    )
    context_index = [
        summarize_file(project_root, path, max_symbols=request.max_symbols_per_file)
        for path in selected_files
    ]
    risks = intake_risks(
        contract=contract,
        git_summary=git_summary,
        files=files,
        dependency_files=dependency_files,
        benchmark_files=benchmark_files,
        validator_files=validator_files,
        test_commands=test_commands,
        edit_policy=edit_policy,
        truncated=truncated,
    )

    manifest = {
        "schema_version": 1,
        "status": "ok",
        "project_root": str(project_root),
        "contract_path": str(request.contract_path.resolve()) if request.contract_path else None,
        "scan_limits": {
            "max_files": max(1, request.max_files),
            "max_symbols_per_file": max(1, request.max_symbols_per_file),
        },
        "git": git_summary,
        "language_summary": language_summary,
        "file_tree_summary": summarize_tree(project_root, files, directories, truncated=truncated),
        "entry_files": [relative_path(project_root, path) for path in entry_files],
        "core_algorithm_files": [relative_path(project_root, path) for path in core_algorithm_files],
        "dependency_files": [relative_path(project_root, path) for path in dependency_files],
        "benchmark_files": [relative_path(project_root, path) for path in benchmark_files],
        "validator_files": [relative_path(project_root, path) for path in validator_files],
        "test_commands": test_commands,
        "data_dirs": [relative_path(project_root, path) for path in data_dirs],
        "output_format_hints": output_hints,
        "edit_policy": edit_policy,
        "context_index": context_index,
        "risk_flags": risks,
        "artifacts": {
            "manifest": str(manifest_path.resolve()),
            "report": str(report_path.resolve()),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_project_intake_report(manifest), encoding="utf-8")
    return manifest


def load_contract(contract_path: Path | None) -> TaskContract | None:
    if not contract_path:
        return None
    try:
        return TaskContract.load(contract_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def collect_project_files(project_root: Path, *, scan_limit: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    for root, dir_names, file_names in os.walk(project_root):
        dir_names[:] = sorted(name for name in dir_names if name.lower() not in EXCLUDED_DIR_NAMES)
        root_path = Path(root)
        for file_name in sorted(file_names):
            path = root_path / file_name
            if should_skip_path(project_root, path):
                continue
            files.append(path.resolve())
            if len(files) >= scan_limit:
                truncated = True
                return sorted(files, key=lambda item: relative_path(project_root, item)), truncated
    return sorted(files, key=lambda item: relative_path(project_root, item)), truncated


def collect_project_dirs(project_root: Path, *, scan_limit: int) -> list[Path]:
    directories: list[Path] = []
    for root, dir_names, _ in os.walk(project_root):
        dir_names[:] = sorted(name for name in dir_names if name.lower() not in EXCLUDED_DIR_NAMES)
        for dir_name in dir_names:
            path = (Path(root) / dir_name).resolve()
            if should_skip_path(project_root, path):
                continue
            directories.append(path)
            if len(directories) >= scan_limit:
                return sorted(directories, key=lambda item: relative_path(project_root, item))
    return sorted(directories, key=lambda item: relative_path(project_root, item))


def should_skip_path(project_root: Path, path: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(project_root.resolve()).parts
    except ValueError:
        return True
    return any(part.lower() in EXCLUDED_DIR_NAMES for part in rel_parts)


def summarize_git(project_root: Path) -> dict[str, Any]:
    inside = git_output(project_root, ["rev-parse", "--is-inside-work-tree"])
    is_repo = inside.strip().lower() == "true"
    if not is_repo:
        return {
            "is_repo": False,
            "branch": None,
            "commit": None,
            "dirty": None,
            "status_lines": [],
            "recent_hotspots": [],
        }
    status_lines = git_output(project_root, ["status", "--short"]).splitlines()
    return {
        "is_repo": True,
        "branch": git_output(project_root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip() or None,
        "commit": git_output(project_root, ["rev-parse", "HEAD"]).strip() or None,
        "dirty": bool(status_lines),
        "status_lines": status_lines[:50],
        "recent_hotspots": recent_git_hotspots(project_root),
    }


def git_output(project_root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def recent_git_hotspots(project_root: Path) -> list[dict[str, Any]]:
    raw = git_output(project_root, ["log", "--name-only", "--pretty=format:", "-n", "20"])
    counts: dict[str, int] = {}
    for line in raw.splitlines():
        item = line.strip()
        if not item or should_skip_relative(item):
            continue
        counts[item.replace("\\", "/")] = counts.get(item.replace("\\", "/"), 0) + 1
    return [
        {"path": path, "recent_commit_touches": count}
        for path, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]
    ]


def should_skip_relative(path: str) -> bool:
    return any(part.lower() in EXCLUDED_DIR_NAMES for part in Path(path).parts)


def summarize_languages(files: list[Path]) -> dict[str, Any]:
    by_extension: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for path in files:
        ext = path.suffix.lower() or "<none>"
        by_extension[ext] = by_extension.get(ext, 0) + 1
        category = language_category(ext)
        by_category[category] = by_category.get(category, 0) + 1
    primary = None
    if by_category:
        primary = sorted(by_category.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return {
        "primary_language": primary,
        "by_category": dict(sorted(by_category.items())),
        "by_extension": dict(sorted(by_extension.items())),
        "total_files": len(files),
    }


def language_category(ext: str) -> str:
    if ext == ".py":
        return "Python"
    if ext in {".md", ".txt"}:
        return "Documentation"
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini"}:
        return "Configuration"
    if ext in {".fjs", ".csv"}:
        return "BenchmarkData"
    if ext in {".ps1", ".sh", ".bat", ".cmd"}:
        return "Script"
    return "Other"


def summarize_tree(project_root: Path, files: list[Path], directories: list[Path], *, truncated: bool) -> dict[str, Any]:
    top_level: dict[str, dict[str, int]] = {}
    total_bytes = 0
    for path in files:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue
        rel = path.relative_to(project_root)
        top = rel.parts[0] if rel.parts else rel.name
        record = top_level.setdefault(top, {"files": 0, "dirs": 0})
        record["files"] += 1
    for path in directories:
        rel = path.relative_to(project_root)
        top = rel.parts[0] if rel.parts else rel.name
        record = top_level.setdefault(top, {"files": 0, "dirs": 0})
        record["dirs"] += 1
    return {
        "total_files": len(files),
        "total_directories": len(directories),
        "total_bytes": total_bytes,
        "truncated": truncated,
        "top_level": dict(sorted(top_level.items())),
    }


def match_dependency_files(files: list[Path]) -> list[Path]:
    return sorted(
        [
            path
            for path in files
            if path.name.lower() in DEPENDENCY_FILE_NAMES or path.name.lower().startswith("requirements")
        ],
        key=lambda item: item.as_posix(),
    )


def match_keyword_files(project_root: Path, files: list[Path], keywords: set[str]) -> list[Path]:
    matches = []
    for path in files:
        lowered = relative_path(project_root, path).lower()
        if any(keyword in lowered for keyword in keywords):
            matches.append(path)
    return sorted(matches, key=lambda item: item.as_posix())[:50]


def command_referenced_files(project_root: Path, contract: TaskContract | None) -> list[Path]:
    if not contract:
        return []
    candidates: list[Path] = []
    for command in [contract.commands.solver, contract.commands.evaluator, contract.commands.quick_test or ""]:
        for token in re.findall(r"[\w./\\-]+\.(?:py|ps1|sh|bat|cmd|json|toml|yaml|yml|md)", command, flags=re.I):
            if "{" in token or "}" in token:
                continue
            resolved = resolve_project_path(project_root, Path(token))
            if resolved.exists() and not should_skip_path(project_root, resolved):
                candidates.append(resolved)
    return unique_paths(candidates)


def rank_entry_files(files: list[Path], command_files: list[Path]) -> list[Path]:
    ranked: list[tuple[int, str, Path]] = []
    command_set = set(command_files)
    for path in files:
        name = path.name.lower()
        score = 0
        if path in command_set:
            score += 100
        if path.suffix.lower() == ".py":
            score += 10
        if any(keyword in name for keyword in ["main", "cli", "run", "solver", "agent"]):
            score += 8
        if name in {"__main__.py", "cli.py"}:
            score += 5
        if score > 0:
            ranked.append((-score, path.as_posix(), path))
    return [path for _, _, path in sorted(ranked)[:30]]


def rank_core_algorithm_files(project_root: Path, files: list[Path]) -> list[Path]:
    keywords = {
        "algorithm",
        "agent",
        "dispatch",
        "fjsp",
        "heuristic",
        "local_search",
        "portfolio",
        "schedule",
        "scheduler",
        "search",
        "solver",
        "strategy",
        "tabu",
        "worker_loop",
    }
    ranked: list[tuple[int, str, Path]] = []
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        lowered = relative_path(project_root, path).lower()
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score:
            ranked.append((-score, path.as_posix(), path))
    return [path for _, _, path in sorted(ranked)[:50]]


def infer_test_commands(project_root: Path, files: list[Path], contract: TaskContract | None) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if contract and contract.commands.quick_test:
        commands.append({"source": "contract.quick_test", "command": contract.commands.quick_test})
    if (project_root / "tests").exists():
        commands.append({"source": "inferred.unittest", "command": "python -m unittest discover -s tests -v"})
    if any(path.name.startswith("test_") and path.suffix == ".py" for path in files):
        commands.append({"source": "inferred.pytest", "command": "python -m pytest"})
    if (project_root / "pyproject.toml").exists():
        commands.append({"source": "inferred.compile", "command": "python -m compileall harness_agent examples tests"})
    return unique_dicts(commands, key="command")


def match_data_dirs(project_root: Path, directories: list[Path]) -> list[Path]:
    matches = []
    for path in directories:
        if path.name.lower() in DATA_DIR_NAMES:
            matches.append(path)
    return sorted(matches, key=lambda item: relative_path(project_root, item))[:50]


def output_format_hints(contract: TaskContract | None) -> dict[str, Any]:
    if not contract:
        return {
            "source": "none",
            "objectives": [],
            "solver_placeholders": [],
            "evaluator_placeholders": [],
            "expected_metrics": {},
        }
    return {
        "source": "task_contract",
        "objectives": [
            {
                "name": objective.name,
                "direction": objective.direction,
                "priority": objective.priority,
                "invalid_if_missing": objective.invalid_if_missing,
                "threshold": objective.threshold,
            }
            for objective in contract.objectives
        ],
        "solver_placeholders": sorted(set(re.findall(r"{([^{}]+)}", contract.commands.solver))),
        "evaluator_placeholders": sorted(set(re.findall(r"{([^{}]+)}", contract.commands.evaluator))),
        "expected_metrics": {
            "valid": "boolean evaluator verdict",
            "error_count": "integer count of evaluator-detected errors",
            "metrics": [objective.name for objective in contract.objectives],
        },
    }


def edit_policy_summary(contract: TaskContract | None) -> dict[str, Any]:
    if not contract:
        return {
            "source": "default",
            "allowed_paths": [],
            "forbidden_paths": sorted(EXCLUDED_DIR_NAMES),
        }
    return {
        "source": "task_contract",
        "allowed_paths": contract.paths.allowed_paths,
        "forbidden_paths": contract.paths.forbidden_paths,
    }


def select_context_files(
    *,
    command_files: list[Path],
    entry_files: list[Path],
    core_files: list[Path],
    dependency_files: list[Path],
    benchmark_files: list[Path],
    validator_files: list[Path],
    files: list[Path],
    max_files: int,
) -> list[Path]:
    selected: list[Path] = []
    for group in [command_files, entry_files, core_files, validator_files, benchmark_files, dependency_files]:
        selected.extend(group)
    selected.extend([path for path in files if path.name.lower() in {"readme.md", "agents.md"}])
    return unique_paths(selected)[:max_files]


def summarize_file(project_root: Path, path: Path, *, max_symbols: int) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": relative_path(project_root, path),
        "size_bytes": None,
        "line_count": None,
        "sha256": None,
        "symbols": [],
        "imports": [],
        "skipped_content": False,
    }
    try:
        data = path.read_bytes()
        record["size_bytes"] = len(data)
        record["sha256"] = hashlib.sha256(data).hexdigest()
    except OSError:
        record["skipped_content"] = True
        return record
    if path.suffix.lower() not in TEXT_EXTENSIONS or len(data) > 1_000_000:
        record["skipped_content"] = True
        return record
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        record["skipped_content"] = True
        return record
    lines = text.splitlines()
    record["line_count"] = len(lines)
    record["symbols"] = extract_python_symbols(lines, max_symbols=max(1, max_symbols)) if path.suffix.lower() == ".py" else []
    record["imports"] = extract_python_imports(lines) if path.suffix.lower() == ".py" else []
    return record


def extract_python_symbols(lines: list[str], *, max_symbols: int) -> list[dict[str, Any]]:
    symbols = []
    pattern = re.compile(r"^\s*(class|def)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for index, line in enumerate(lines, start=1):
        match = pattern.match(line)
        if not match:
            continue
        symbols.append({"kind": match.group(1), "name": match.group(2), "line": index})
        if len(symbols) >= max_symbols:
            break
    return symbols


def extract_python_imports(lines: list[str]) -> list[str]:
    imports = []
    pattern = re.compile(r"^\s*(?:from\s+([A-Za-z0-9_.]+)\s+import|import\s+([A-Za-z0-9_.]+))")
    for line in lines:
        match = pattern.match(line)
        if not match:
            continue
        imports.append(match.group(1) or match.group(2))
        if len(imports) >= 20:
            break
    return sorted(set(imports))


def intake_risks(
    *,
    contract: TaskContract | None,
    git_summary: dict[str, Any],
    files: list[Path],
    dependency_files: list[Path],
    benchmark_files: list[Path],
    validator_files: list[Path],
    test_commands: list[dict[str, Any]],
    edit_policy: dict[str, Any],
    truncated: bool,
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    if contract is None:
        risks.append({"code": "missing_contract", "message": "No task contract was loaded for this intake scan."})
    if git_summary.get("is_repo") and git_summary.get("dirty"):
        risks.append({"code": "dirty_worktree", "message": "Git status is not clean; candidate diffs may mix with existing changes."})
    if not dependency_files:
        risks.append({"code": "missing_dependency_file", "message": "No common dependency manifest was found."})
    if not validator_files:
        risks.append({"code": "missing_validator_candidate", "message": "No validator/evaluator candidate file was detected."})
    if not benchmark_files:
        risks.append({"code": "missing_benchmark_candidate", "message": "No benchmark/suite candidate file was detected."})
    if not test_commands:
        risks.append({"code": "missing_test_command", "message": "No quick-test or inferred test command was detected."})
    forbidden = {str(path).replace("\\", "/").lower() for path in edit_policy.get("forbidden_paths") or []}
    if "outputs" not in forbidden:
        risks.append({"code": "outputs_not_forbidden", "message": "The edit policy does not explicitly forbid outputs."})
    if truncated:
        risks.append({"code": "scan_truncated", "message": f"Project scan reached the configured file cap after {len(files)} files."})
    return risks


def render_project_intake_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# Project Intake Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Project root: `{manifest.get('project_root')}`",
        f"- Contract: `{manifest.get('contract_path')}`",
    ]
    if manifest.get("status") != "ok":
        lines.extend(["", "## Errors", ""])
        for error in manifest.get("errors") or []:
            lines.append(f"- {error}")
        return "\n".join(lines).strip() + "\n"

    language = manifest.get("language_summary") or {}
    tree = manifest.get("file_tree_summary") or {}
    git_summary = manifest.get("git") or {}
    lines.extend(
        [
            f"- Git branch: `{git_summary.get('branch')}`",
            f"- Git dirty: `{git_summary.get('dirty')}`",
            f"- Primary language: `{language.get('primary_language')}`",
            f"- Files scanned: `{tree.get('total_files')}`",
            f"- Context-index files: `{len(manifest.get('context_index') or [])}`",
            f"- Risk flags: `{len(manifest.get('risk_flags') or [])}`",
            "",
            "## Key Files",
            "",
            "| Category | Count | Examples |",
            "| --- | ---: | --- |",
        ]
    )
    for label, key in [
        ("Entry files", "entry_files"),
        ("Core algorithm files", "core_algorithm_files"),
        ("Dependency files", "dependency_files"),
        ("Benchmark files", "benchmark_files"),
        ("Validator files", "validator_files"),
        ("Data directories", "data_dirs"),
    ]:
        values = manifest.get(key) or []
        examples = ", ".join(f"`{item}`" for item in values[:5]) if values else "N/A"
        lines.append(f"| {label} | {len(values)} | {examples} |")
    lines.extend(
        [
            "",
            "## Test Commands",
            "",
        ]
    )
    for command in manifest.get("test_commands") or []:
        lines.append(f"- `{command.get('command')}` ({command.get('source')})")
    if not manifest.get("test_commands"):
        lines.append("- N/A")
    lines.extend(
        [
            "",
            "## Risk Flags",
            "",
        ]
    )
    for risk in manifest.get("risk_flags") or []:
        lines.append(f"- `{risk.get('code')}`: {risk.get('message')}")
    if not manifest.get("risk_flags"):
        lines.append("- None")
    lines.extend(
        [
            "",
            "Project intake is a read-only context scan. It does not run solvers, evaluate candidates, or make promotion decisions.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def unique_dicts(items: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        value = str(item.get(key))
        if value in seen:
            continue
        seen.add(value)
        result.append(item)
    return result


def relative_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
