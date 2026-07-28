"""本地 Web 服务：把浏览器操作映射到同一套 Agent/Core 闭环。

Web 层只负责输入落盘、后台任务生命周期、状态轮询和产物摘要。它不选择
具体算法，也不参与 promotion/rollback。一次任务的正式判断仍由
`orchestration.standard -> orchestration.loop -> core` 完成。

数据目录约定：`outputs/web_runs/<job_id>/inputs` 保存用户输入，`run` 保存
不可变实验产物，`web_job_status.json` 是浏览器和重启恢复共同读取的快照。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import math
import os
import re
import stat
import threading
import time
import traceback
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness_agent.deepseek_client import is_deepseek_configured, load_local_env, local_env_candidates, normalize_deepseek_model
from harness_agent.orchestration.loop import DEFAULT_IN_ROUND_REPAIR_ATTEMPTS, load_worker_loop_result
from harness_agent.context.knowledge import knowledge_query_catalog, method_family_catalog, method_package_catalog
from harness_agent.core.cancellation import CancellationToken, TaskCancelled
from harness_agent.agents.opencode_main import OpenCodeMainAgent
from harness_agent.domains.io import parse_standard_fjsp
from harness_agent.orchestration.standard import StandardWorkerLoopRequest, run_standard_worker_loop
from harness_agent.workers.opencode_worker import (
    DEFAULT_OPENCODE_MODEL,
    OpenCodeWorker,
    opencode_openai_key_available,
    opencode_openai_key_source,
)


# 路径与显示上限集中在这里，避免 HTTP handler 和任务线程各自推导目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "web_runs"
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_STARTER_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_STARTER_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_STARTER_FILE_BYTES = 16 * 1024 * 1024
MAX_STARTER_ARCHIVE_ENTRIES = 2_000
MAX_ARTIFACT_CHARS = 240_000
MAX_RESOURCE_CHARS = 240_000
RESOURCE_TEXT_SUFFIXES = frozenset({".md", ".json", ".py", ".yaml", ".yml", ".csv", ".txt"})
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KNOWLEDGE_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
KNOWLEDGE_DESTINATIONS = {
    "reference-general": Path("references") / "general_fjsp",
    "reference-standard": Path("references") / "standard_fjsp",
    "reference-sdst": Path("references") / "sdst",
    "principle": Path("principles"),
    "benchmark": Path("benchmarks"),
    "experiment-memory": Path("experiment_memory") / "current_week",
    "imported-note": Path("imported") / "user_notes",
}
DEFAULT_STANDARD_SEEDS_TEXT = "0,1,2,3,4,5,6,7,8,9"
DEFAULT_DIRECTION_CHANGE_CONFIRMATION_SECONDS = 20.0
DIRECTION_CHANGE_REJECTION_INSTRUCTION = (
    "Reject the proposed method-family switch. Continue the previously active method family and preserve its "
    "verified mechanisms. Plan one materially different bounded mutation inside that direction from the latest "
    "Core, activation, legality, and rollback evidence."
)
DEFAULT_STANDARD_FJSP_DP18A_INSTANCE = PROJECT_ROOT / "examples" / "fjsp.dauzere.18a.m10j20c10.txt"
DEFAULT_STANDARD_FJSP_DP18A_BOUNDS_CSV = (
    '"Instance","Family","Lower bound (LB)","Best-known upper bound (UB/BKS)","Note","Source URL"\n'
    '"fjsp.dauzere.18a.m10j20c10.txt","DP","2057","2127","",'
    '"https://raw.githubusercontent.com/SchedulingLab/fjsp-instances/main/instances.json"\n'
)

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_ROUND_GATES: dict[str, "WebRoundInterventionGate"] = {}
_JOB_CANCELLATIONS: dict[str, CancellationToken] = {}
_ACTIVE_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT
_RESOURCE_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Web 状态模型：所有写操作最终落到 web_job_status.json，内存字典只是缓存。
# ---------------------------------------------------------------------------

def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sanitize_filename(name: str | None, default: str) -> str:
    candidate = (name or default).strip().replace("\\", "/").split("/")[-1]
    candidate = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", candidate)
    candidate = candidate.strip("._")
    return candidate or default


def normalize_starter_project_path(value: Any, *, field: str, default: str) -> str:
    """Return one bounded POSIX-relative path inside an uploaded project."""

    text = str(value or default).strip().replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in text
    ):
        raise ValueError(f"{field} must be a relative project path")
    normalized = path.as_posix()
    if len(normalized) > 240:
        raise ValueError(f"{field} is too long")
    return normalized


def validate_starter_solver_command(value: Any, *, entrypoint: str) -> str:
    command = str(value or "").strip() or (
        f"python {entrypoint} --input {{instance}} --output {{solution}} --seed {{seed}} "
        "--time-limit-sec {solver_time_limit_seconds}"
    )
    if len(command) > 2_000:
        raise ValueError("starter solver command is too long")
    missing = [name for name in ("instance", "solution", "seed") if f"{{{name}}}" not in command]
    if missing:
        raise ValueError("starter solver command is missing placeholders: " + ", ".join(missing))
    try:
        command.format(
            instance="INSTANCE",
            solution="SOLUTION",
            seed=0,
            solver_time_limit_seconds=1,
            timeout_seconds=1,
            workdir="WORKDIR",
            instance_id="INSTANCE_ID",
            task_id="TASK",
            round=0,
            round_id="round_000",
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"starter solver command has an invalid placeholder: {exc}") from exc
    return command


def extract_starter_project(archive: dict[str, Any], *, destination: Path) -> dict[str, Any]:
    """Decode and safely extract one browser-uploaded ZIP into a private input snapshot."""

    encoded = str(archive.get("base64") or "").strip()
    if not encoded:
        raise ValueError("starter project ZIP is empty")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("starter project is not valid base64") from exc
    if len(raw) > MAX_STARTER_ARCHIVE_BYTES:
        raise ValueError(f"starter project ZIP exceeds {MAX_STARTER_ARCHIVE_BYTES} bytes")

    try:
        archive_file = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("starter project is not a valid ZIP archive") from exc

    with archive_file:
        infos = [item for item in archive_file.infolist() if not _ignored_zip_metadata(item.filename)]
        if not infos:
            raise ValueError("starter project ZIP contains no project files")
        if len(infos) > MAX_STARTER_ARCHIVE_ENTRIES:
            raise ValueError(f"starter project ZIP has more than {MAX_STARTER_ARCHIVE_ENTRIES} entries")

        normalized: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        expanded_bytes = 0
        for info in infos:
            parts = _safe_zip_member_parts(info)
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if file_type == stat.S_IFLNK:
                raise ValueError(f"starter project ZIP contains a symlink: {info.filename}")
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError(f"starter project ZIP contains a special file: {info.filename}")
            if info.file_size > MAX_STARTER_FILE_BYTES:
                raise ValueError(f"starter project file is too large: {info.filename}")
            expanded_bytes += info.file_size
            if expanded_bytes > MAX_STARTER_EXPANDED_BYTES:
                raise ValueError(f"starter project expands beyond {MAX_STARTER_EXPANDED_BYTES} bytes")
            normalized.append((info, parts))

        file_parts = [parts for info, parts in normalized if not info.is_dir()]
        common_root = (
            file_parts[0][0]
            if file_parts
            and all(len(parts) > 1 and parts[0].casefold() == file_parts[0][0].casefold() for parts in file_parts)
            else None
        )
        destination.mkdir(parents=True, exist_ok=False)
        seen: set[str] = set()
        file_count = 0
        for info, original_parts in normalized:
            parts = original_parts[1:] if common_root and original_parts[0].casefold() == common_root.casefold() else original_parts
            if not parts:
                continue
            collision_key = "/".join(parts).casefold()
            if collision_key in seen:
                raise ValueError(f"starter project ZIP contains duplicate paths: {'/'.join(parts)}")
            seen.add(collision_key)
            target = destination.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive_file.open(info, "r") as source, target.open("xb") as output:
                remaining = info.file_size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError(f"starter project ZIP ended early: {info.filename}")
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise ValueError(f"starter project ZIP size mismatch: {info.filename}")
            file_count += 1

    return {
        "name": sanitize_filename(archive.get("name"), "starter_project.zip"),
        "archive_bytes": len(raw),
        "expanded_bytes": expanded_bytes,
        "file_count": file_count,
        "stripped_root": common_root,
        "project_root": str(destination.resolve()),
    }


def _ignored_zip_metadata(name: str) -> bool:
    normalized = str(name or "").replace("\\", "/").lstrip("/")
    return normalized.startswith("__MACOSX/") or normalized.endswith("/.DS_Store") or normalized == ".DS_Store"


def _safe_zip_member_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = str(info.filename or "")
    normalized = name.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or ":" in normalized
    ):
        raise ValueError(f"starter project ZIP has an unsafe path: {name!r}")
    parts = PurePosixPath(normalized.rstrip("/")).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"starter project ZIP has an unsafe path: {name!r}")
    return tuple(parts)


def resource_catalog() -> dict[str, Any]:
    """列出前端可浏览的项目 Skill 和知识资产，不暴露任意路径。"""

    resources: list[dict[str, Any]] = []
    for resource_kind, category, relative_root in (
        ("skill", "skill", Path(".codex") / "skills"),
        ("opencode-skill", "skill", Path(".opencode") / "skills"),
        ("knowledge", "knowledge", Path("knowledge")),
    ):
        root = (PROJECT_ROOT / relative_root).resolve()
        if not root.is_dir():
            continue
        paths = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in RESOURCE_TEXT_SUFFIXES
        ]
        if category == "skill":
            paths.sort(
                key=lambda path: (
                    path.relative_to(root).parts[0],
                    path.name != "SKILL.md",
                    path.relative_to(root).as_posix().lower(),
                )
            )
        else:
            paths.sort(key=lambda path: path.relative_to(root).as_posix().lower())
        for path in paths:
            relative_path = path.relative_to(root).as_posix()
            resources.append(
                resource_metadata(
                    category=category,
                    resource_kind=resource_kind,
                    relative_root=relative_root,
                    relative_path=relative_path,
                    path=path,
                )
            )
    return {
        "resources": resources,
        "counts": {
            "skill": sum(item["category"] == "skill" for item in resources),
            "knowledge": sum(item["category"] == "knowledge" for item in resources),
        },
        "authoring": resource_authoring_schema(),
    }


def read_resource(resource_id: str) -> dict[str, Any]:
    """读取白名单资源内容；resource_id 不能旁路到项目中的其他文件。"""

    category, separator, relative_text = str(resource_id or "").partition(":")
    roots = {
        "skill": Path(".codex") / "skills",
        "opencode-skill": Path(".opencode") / "skills",
        "knowledge": Path("knowledge"),
    }
    relative_root = roots.get(category)
    relative_path = Path(relative_text.replace("\\", "/"))
    if not separator or relative_root is None or not relative_text or relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("invalid resource id")
    root = (PROJECT_ROOT / relative_root).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in RESOURCE_TEXT_SUFFIXES:
        raise ValueError("resource is outside the allowed catalog")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    metadata = resource_metadata(
        category="skill" if category in {"skill", "opencode-skill"} else category,
        resource_kind=category,
        relative_root=relative_root,
        relative_path=relative_path.as_posix(),
        path=path,
        text=text,
    )
    return {
        **metadata,
        "content": text[:MAX_RESOURCE_CHARS],
        "truncated": len(text) > MAX_RESOURCE_CHARS,
    }


def resource_metadata(
    *,
    category: str,
    resource_kind: str | None = None,
    relative_root: Path,
    relative_path: str,
    path: Path,
    text: str | None = None,
) -> dict[str, Any]:
    preview = text
    if preview is None:
        try:
            preview = path.read_text(encoding="utf-8-sig", errors="replace")[:8_000]
        except OSError:
            preview = ""
    title, description = resource_title_and_description(preview, path=path)
    kind = resource_kind or category
    group, group_label = resource_group(category=category, resource_kind=kind, relative_path=relative_path)
    return {
        "id": f"{kind}:{relative_path}",
        "category": category,
        "resource_kind": kind,
        "group": group,
        "group_label": group_label,
        "title": title,
        "description": description,
        "path": (relative_root / relative_path).as_posix(),
        "format": path.suffix.lower().lstrip(".") or "text",
        "size": path.stat().st_size,
    }


def resource_title_and_description(text: str, *, path: Path) -> tuple[str, str]:
    title = ""
    description = ""
    in_frontmatter = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            key, separator, value = line.partition(":")
            if separator and key.strip() == "name" and not title:
                title = value.strip().strip("'\"")
            elif separator and key.strip() == "description" and not description:
                description = value.strip().strip("'\"")
            continue
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue
        if line and not line.startswith(("#", "```", "<!--")) and not description:
            description = line
        if title and description:
            break
    return title or path.stem.replace("_", " "), description[:220]


def resource_group(*, category: str, resource_kind: str, relative_path: str) -> tuple[str, str]:
    parts = Path(relative_path).parts
    if category == "skill":
        if resource_kind == "opencode-skill":
            return "opencode-internal", "OpenCode 内部执行 Skill"
        skill_id = parts[0] if parts else "other"
        if skill_id in {"fjsp-solver-foundation-worker", "fjsp-experiment-design-worker"}:
            return "worker-foundation", "Worker 基础与实验"
        if skill_id in {
            "fjsp-constructive-search-worker",
            "fjsp-coupled-local-search-worker",
            "fjsp-exact-hybrid-worker",
            "fjsp-population-memetic-worker",
            "fjsp-sdst-adapter-worker",
        }:
            return "worker-method", "Worker 方法实现"
        return "planning-domain", "规划、诊断与领域扩展"
    top = parts[0] if parts else "other"
    second = parts[1] if len(parts) > 1 else ""
    if len(parts) == 1:
        return "knowledge-governance", "知识库规范与总清单"
    labels = {
        "principles": "原则与架构契约",
        "benchmarks": "Benchmark、IO 与边界事实",
        "capabilities": "当前能力快照",
        "method_packages": "完整 Method Package",
        "experiment_memory": "本周实验记忆",
        "imported": "导入材料与原始来源",
    }
    if top == "references":
        reference_labels = {
            "general_fjsp": "通用 FJSP 方法知识",
            "standard_fjsp": "标准 FJSP 实现知识",
            "sdst": "FJSP-SDST 变体知识",
        }
        return f"references/{second or 'other'}", reference_labels.get(second, "其他稳定参考")
    return top, labels.get(top, "其他知识资产")


def resource_authoring_schema() -> dict[str, Any]:
    families = method_family_catalog(problem_family="FJSP").get("families") or []
    query = knowledge_query_catalog(problem_family="FJSP")
    return {
        "skill": {
            "name_pattern": SKILL_NAME_PATTERN.pattern,
            "method_families": families,
            "tags": query.get("tags") or [],
        },
        "knowledge": {
            "destinations": [
                {"id": key, "path": f"knowledge/{value.as_posix()}"}
                for key, value in KNOWLEDGE_DESTINATIONS.items()
            ],
            "tags": query.get("tags") or [],
        },
    }


def create_project_resource(payload: dict[str, Any]) -> dict[str, Any]:
    """创建一个项目 Skill 或知识卡；只允许新建，禁止覆盖现有资产。"""

    category = str(payload.get("category") or "").strip().lower()
    with _RESOURCE_WRITE_LOCK:
        if category == "skill":
            return _create_project_skill(payload)
        if category == "knowledge":
            return _create_knowledge_card(payload)
    raise ValueError("category must be skill or knowledge")


def _create_project_skill(payload: dict[str, Any]) -> dict[str, Any]:
    name = _resource_text(payload.get("name"), "Skill 名称", maximum=64)
    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError("Skill 名称必须使用小写字母、数字和单连字符，且不能以连字符开头或结尾")
    description = _resource_text(payload.get("description"), "触发描述", maximum=1024)
    title = _resource_text(payload.get("title") or name, "显示名称", maximum=100)
    body = _resource_text(payload.get("body"), "Skill 指令", maximum=120_000)
    default_prompt = _resource_text(
        payload.get("default_prompt") or f"使用 ${name} 完成当前任务。",
        "默认提示词",
        maximum=500,
    )
    method_families = _normalized_resource_terms(payload.get("method_families"), maximum=8)
    activation_tags = _normalized_resource_terms(payload.get("activation_tags"), maximum=24)
    register = coerce_bool(payload.get("register"), default=True)
    if register and not method_families:
        raise ValueError("加入 Worker 自动匹配时至少选择一个方法族")

    skill_root = (PROJECT_ROOT / ".codex" / "skills").resolve()
    skill_dir = (skill_root / name).resolve()
    if not skill_dir.is_relative_to(skill_root):
        raise ValueError("Skill 路径超出允许目录")
    for root in (PROJECT_ROOT / ".codex" / "skills", PROJECT_ROOT / ".opencode" / "skills"):
        if (root / name).exists():
            raise ValueError(f"Skill 已存在：{name}")

    instructions = body.strip()
    if instructions.startswith("---"):
        raise ValueError("Skill 指令不应包含 YAML frontmatter，表单会自动生成")
    if not instructions.startswith("# "):
        instructions = f"# {title}\n\n{instructions}"
    skill_text = (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n\n"
        f"{instructions.rstrip()}\n"
    )
    agent_text = (
        "interface:\n"
        f"  display_name: {json.dumps(title, ensure_ascii=False)}\n"
        f"  short_description: {json.dumps(description[:80], ensure_ascii=False)}\n"
        f"  default_prompt: {json.dumps(default_prompt, ensure_ascii=False)}\n"
    )

    manifest_path = PROJECT_ROOT / "domain_packs" / "standard_fjsp" / "domain_pack.json"
    manifest = _read_resource_manifest(manifest_path) if register else None
    if manifest is not None:
        _append_worker_skill_registration(
            manifest,
            name=name,
            title=title,
            description=description,
            method_families=method_families,
            activation_tags=activation_tags,
        )

    skill_dir.mkdir(parents=True)
    try:
        (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "openai.yaml").write_text(agent_text, encoding="utf-8")
        if manifest is not None:
            _write_resource_manifest(manifest_path, manifest)
    except Exception:
        for path in (skill_dir / "agents" / "openai.yaml", skill_dir / "SKILL.md"):
            if path.exists():
                path.unlink()
        for path in (skill_dir / "agents", skill_dir):
            if path.exists():
                path.rmdir()
        raise
    result = read_resource(f"skill:{name}/SKILL.md")
    result["registered"] = register
    return result


def _create_knowledge_card(payload: dict[str, Any]) -> dict[str, Any]:
    title = _resource_text(payload.get("title"), "知识卡标题", maximum=160)
    slug = _resource_text(payload.get("slug"), "知识卡标识", maximum=80).lower()
    if not KNOWLEDGE_SLUG_PATTERN.fullmatch(slug):
        raise ValueError("知识卡标识必须使用小写字母、数字、单连字符或下划线")
    destination_id = str(payload.get("destination") or "").strip().lower()
    relative_dir = KNOWLEDGE_DESTINATIONS.get(destination_id)
    if relative_dir is None:
        raise ValueError("未知知识分类")
    summary = _resource_text(payload.get("summary"), "摘要", maximum=1024)
    source = _resource_text(payload.get("source"), "证据或来源", maximum=2000)
    body = _resource_text(payload.get("body"), "知识卡正文", maximum=160_000)
    tags = _normalized_resource_terms(payload.get("tags"), maximum=24)
    register = coerce_bool(payload.get("register"), default=False)
    if register and not destination_id.startswith("reference-"):
        raise ValueError("只有稳定方法参考可以加入自动检索；原则、Benchmark、实验记忆和导入材料需单独审核")
    if register and not tags:
        raise ValueError("加入自动检索时至少填写一个标签")

    knowledge_root = (PROJECT_ROOT / "knowledge").resolve()
    path = (knowledge_root / relative_dir / f"{slug}.md").resolve()
    if not path.is_relative_to(knowledge_root):
        raise ValueError("知识卡路径超出允许目录")
    if path.exists():
        raise ValueError(f"知识卡已存在：{path.relative_to(PROJECT_ROOT).as_posix()}")
    content = body.strip()
    if content.startswith("---"):
        raise ValueError("知识卡正文不应包含 YAML frontmatter，表单会自动生成")
    if not content.startswith("# "):
        content = f"# {title}\n\n{content}"
    metadata = (
        "---\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"description: {json.dumps(summary, ensure_ascii=False)}\n"
        f"knowledge_type: {json.dumps(destination_id, ensure_ascii=False)}\n"
        f"problem_family: \"standard_fjsp\"\n"
        f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
        f"status: {json.dumps('reviewed' if register else 'draft', ensure_ascii=False)}\n"
        f"source: {json.dumps(source, ensure_ascii=False)}\n"
        f"created_at: {json.dumps(utc_timestamp(), ensure_ascii=False)}\n"
        "---\n\n"
    )

    manifest_path = PROJECT_ROOT / "domain_packs" / "standard_fjsp" / "domain_pack.json"
    manifest = _read_resource_manifest(manifest_path) if register else None
    project_relative = path.relative_to(PROJECT_ROOT).as_posix()
    if manifest is not None:
        _append_knowledge_registration(manifest, path=project_relative, tags=tags, summary=summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(metadata + content.rstrip() + "\n", encoding="utf-8")
        if manifest is not None:
            _write_resource_manifest(manifest_path, manifest)
    except Exception:
        if path.exists():
            path.unlink()
        raise
    result = read_resource(f"knowledge:{path.relative_to(knowledge_root).as_posix()}")
    result["registered"] = register
    return result


def _resource_text(value: Any, label: str, *, maximum: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > maximum:
        raise ValueError(f"{label}超过长度上限 {maximum}")
    return text


def _normalized_resource_terms(value: Any, *, maximum: int) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,，\s]+", str(value or ""))
    result: list[str] = []
    for item in raw:
        term = str(item).strip().lower()
        if not term or term in result:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", term):
            raise ValueError(f"标签或方法族格式非法：{term}")
        result.append(term)
        if len(result) > maximum:
            raise ValueError(f"标签数量不能超过 {maximum}")
    return result


def _read_resource_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 Domain Pack：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Domain Pack 必须是 JSON object")
    return payload


def _write_resource_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _append_worker_skill_registration(
    manifest: dict[str, Any],
    *,
    name: str,
    title: str,
    description: str,
    method_families: list[str],
    activation_tags: list[str],
) -> None:
    known_families = {
        str(item.get("family_id") or "").strip().lower()
        for item in manifest.get("method_families") or []
        if isinstance(item, dict)
    }
    unknown = sorted(set(method_families) - known_families)
    if unknown:
        raise ValueError("未知方法族：" + ", ".join(unknown))
    skills = manifest.setdefault("worker_implementation_skills", [])
    if any(isinstance(item, dict) and item.get("skill_id") == name for item in skills):
        raise ValueError(f"Domain Pack 已注册 Skill：{name}")
    skills.append(
        {
            "skill_id": name,
            "title": title,
            "description": description,
            "source_path": f".codex/skills/{name}",
            "method_families": method_families,
            "activation_tags": activation_tags,
            "required_features": [],
            "excluded_features": [],
            "default_priority": 100,
            "always_include": False,
        }
    )


def _append_knowledge_registration(
    manifest: dict[str, Any], *, path: str, tags: list[str], summary: str
) -> None:
    knowledge = manifest.setdefault("knowledge", {})
    tagged_cards = knowledge.setdefault("tagged_cards", {})
    query = knowledge.setdefault("knowledge_query", {})
    descriptions = query.setdefault("tag_descriptions", {})
    for tag in tags:
        paths = tagged_cards.setdefault(tag, [])
        if path not in paths:
            paths.append(path)
        descriptions.setdefault(tag, summary[:220])


def parse_seeds(value: Any) -> list[int]:
    if isinstance(value, list):
        parsed = [int(item) for item in value if str(item).strip()]
    else:
        parsed = [int(item.strip()) for item in str(value or "0").split(",") if item.strip()]
    return parsed or [0]


def coerce_int(value: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_opencode_variant(value: Any) -> str:
    """Accept only OpenCode reasoning presets exposed by the local UI."""

    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"low", "medium", "high"} else ""


def normalize_main_planning_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"fast", "research"} else "fast"


def append_event(job: dict[str, Any], message: str, *, level: str = "info") -> None:
    """向前端事件流追加一条面向用户的阶段消息。"""

    job.setdefault("events", []).append(
        {
            "time": utc_timestamp(),
            "level": level,
            "message": message,
        }
    )


def _trace_timestamp(record: dict[str, Any]) -> float:
    value = record.get("timestamp")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _agent_status(value: Any, *, default: str = "queued") -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "pending": "queued",
        "unknown": default,
        "in_progress": "running",
        "success": "completed",
        "complete": "completed",
        "done": "completed",
        "error": "failed",
        "cancelled": "stopped",
        "canceled": "stopped",
        "interrupted": "stopped",
        "stopping": "stopped",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"queued", "running", "waiting", "completed", "failed", "stopped"} else default


def _tool_trace_fields(record: dict[str, Any]) -> tuple[str, str, str, str]:
    """Read structured tool fields, with a fallback for persisted legacy traces."""

    tool = str(record.get("tool") or "").strip()
    subagent = str(record.get("subagent") or "").strip()
    status = str(record.get("status") or "").strip()
    title = str(record.get("title") or "").strip()
    parts = [part.strip() for part in str(record.get("text") or "").split(" / ")]
    if not tool and parts:
        tool = parts[0]
    if tool == "task":
        if not subagent and len(parts) > 1:
            subagent = parts[1]
        if not status and len(parts) > 2:
            status = parts[2]
        if not title and len(parts) > 3:
            title = " / ".join(parts[3:])
    else:
        if not status and len(parts) > 1:
            status = parts[1]
        if not title and len(parts) > 2:
            title = " / ".join(parts[2:])
    return tool, subagent, status, title


def _agent_detail(record: dict[str, Any] | None, fallback: str) -> str:
    if not record:
        return fallback
    text = str(record.get("text") or "").strip()
    if not text:
        return fallback
    return text if len(text) <= 180 else f"{text[:177]}..."


def agent_status_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    """Aggregate public traces into a truthful, refresh-safe Agent status bar."""

    config = job.get("config") if isinstance(job.get("config"), dict) else {}
    job_status = str(job.get("status") or "queued")
    main_trace = [item for item in job.get("main_agent_trace") or [] if isinstance(item, dict)]
    coding_trace = [item for item in job.get("coding_agent_trace") or [] if isinstance(item, dict)]
    latest_main = max(main_trace, key=_trace_timestamp, default=None)
    latest_coding = max(coding_trace, key=_trace_timestamp, default=None)

    terminal_main_status = {
        "completed": "completed",
        "failed": "failed",
        "stopped": "stopped",
        "interrupted": "stopped",
    }.get(job_status)
    if terminal_main_status:
        main_status = terminal_main_status
    elif job_status == "waiting_for_user":
        main_status = "waiting"
    elif job_status == "stopping":
        main_status = "stopped"
    elif job_status == "queued":
        main_status = "queued"
    elif latest_coding and _trace_timestamp(latest_coding) > _trace_timestamp(latest_main or {}):
        main_status = "waiting"
    else:
        main_status = "running"

    main_detail = _agent_detail(latest_main, "等待任务启动")
    if main_status == "waiting" and latest_coding and _trace_timestamp(latest_coding) > _trace_timestamp(latest_main or {}):
        main_detail = "等待 Coding Agent 返回"
    elif job_status == "waiting_for_user":
        main_detail = "等待用户指定下一轮方向"
    agents: list[dict[str, Any]] = [
        {
            "key": "main",
            "role": "main",
            "name": "Main Agent",
            "status": main_status,
            "stage": str((latest_main or {}).get("stage") or (latest_main or {}).get("attempt") or job_status),
            "detail": main_detail,
            "model": str(config.get("main_agent_model") or config.get("opencode_model") or ""),
            "variant": str(config.get("main_agent_variant") or ""),
            "updated_at": (latest_main or {}).get("timestamp") or job.get("updated_at"),
        }
    ]
    worker_summary = (
        job.get("summary", {}).get("worker_summary", {})
        if isinstance(job.get("summary"), dict)
        else {}
    )
    if terminal_main_status and isinstance(worker_summary, dict):
        completed_rounds = coerce_int(worker_summary.get("completed_round_count"), 0, minimum=0)
        if completed_rounds:
            agents[0]["stage"] = f"{completed_rounds} 轮完成"

    task_records: list[tuple[dict[str, Any], str, str, str]] = []
    for record in main_trace:
        tool, subagent, status, title = _tool_trace_fields(record)
        if tool == "task" and subagent:
            task_records.append((record, subagent, status, title))
    current_main_attempt = str((latest_main or {}).get("attempt") or "")
    visible_task_records = [item for item in task_records if str(item[0].get("attempt") or "") == current_main_attempt]
    if not visible_task_records and task_records:
        latest_task_attempt = str(max(task_records, key=lambda item: _trace_timestamp(item[0]))[0].get("attempt") or "")
        visible_task_records = [item for item in task_records if str(item[0].get("attempt") or "") == latest_task_attempt]
    task_groups: dict[str, list[tuple[dict[str, Any], str, str, str]]] = {}
    for item in visible_task_records:
        record, subagent, _status, title = item
        key = f"{record.get('attempt') or 'current'}:{subagent}:{title or 'task'}"
        task_groups.setdefault(key, []).append(item)
    for key, records in task_groups.items():
        record, subagent, raw_status, title = max(records, key=lambda item: _trace_timestamp(item[0]))
        status = _agent_status(raw_status, default="running")
        if job_status in {"failed", "stopped", "interrupted"} and status in {"queued", "running", "waiting"}:
            status = "stopped" if job_status in {"stopped", "interrupted"} else "failed"
        elif job_status == "completed" and status in {"queued", "running", "waiting"}:
            status = "failed"
        agents.append(
            {
                "key": f"subagent:{key}",
                "role": "subagent",
                "name": f"Main Subagent · {subagent}",
                "status": status,
                "stage": str(record.get("attempt") or "当前轮"),
                "detail": title or _agent_detail(record, "执行 Main 委派任务"),
                "model": str(config.get("main_agent_model") or config.get("opencode_model") or ""),
                "variant": str(config.get("main_agent_variant") or ""),
                "updated_at": record.get("timestamp") or job.get("updated_at"),
            }
        )

    coding_groups: dict[str, list[dict[str, Any]]] = {}
    for record in coding_trace:
        key = str(record.get("agent_key") or record.get("display_name") or record.get("attempt") or "worker")
        coding_groups.setdefault(key, []).append(record)
    if coding_groups:
        latest_group = max(coding_groups.values(), key=lambda records: max(_trace_timestamp(item) for item in records))
        current_round = str(max(latest_group, key=_trace_timestamp).get("round") or "")
        if current_round:
            coding_groups = {
                key: records
                for key, records in coding_groups.items()
                if str(max(records, key=_trace_timestamp).get("round") or "") == current_round
            }
    for key, records in sorted(coding_groups.items(), key=lambda item: max(_trace_timestamp(record) for record in item[1])):
        latest = max(records, key=_trace_timestamp)
        has_final = any(record.get("kind") == "final" for record in records)
        tool_statuses = [_tool_trace_fields(record)[2] for record in records if record.get("kind") == "tool"]
        latest_tool_status = next((value for value in reversed(tool_statuses) if value), "")
        if has_final:
            status = "completed"
        elif _agent_status(latest_tool_status, default="running") in {"failed", "stopped"}:
            status = _agent_status(latest_tool_status, default="failed")
        elif job_status in {"completed", "failed"}:
            status = "failed"
        elif job_status in {"stopped", "interrupted", "stopping"}:
            status = "stopped"
        elif job_status == "queued":
            status = "queued"
        else:
            status = "running"
        latest_non_usage = max(
            (record for record in records if record.get("kind") != "usage"),
            key=_trace_timestamp,
            default=latest,
        )
        agents.append(
            {
                "key": f"coding:{key}",
                "role": "coding",
                "name": f"Coding Agent · {latest.get('display_name') or latest.get('candidate_id') or 'worker'}",
                "status": status,
                "stage": str(latest.get("round") or latest.get("attempt") or "当前轮"),
                "detail": _agent_detail(latest_non_usage, "等待 Coding Agent 输出"),
                "model": str(latest.get("model") or config.get("coding_worker_model") or config.get("opencode_model") or ""),
                "variant": str(latest.get("variant") or config.get("coding_worker_variant") or ""),
                "updated_at": latest.get("timestamp") or job.get("updated_at"),
            }
        )

    reported_worker_attempts = (
        coerce_int(worker_summary.get("attempt_count"), 0, minimum=0)
        if isinstance(worker_summary, dict)
        else 0
    )
    observed_candidate_workers = len(
        {
            str(record.get("agent_key") or record.get("display_name") or record.get("attempt") or "worker")
            for record in coding_trace
            if str(record.get("round") or "") != "baseline"
        }
    )
    untraced_worker_attempts = max(0, reported_worker_attempts - observed_candidate_workers)
    if untraced_worker_attempts:
        worker_status_counts = worker_summary.get("worker_status_counts") if isinstance(worker_summary, dict) else {}
        worker_status_counts = worker_status_counts if isinstance(worker_status_counts, dict) else {}
        failed_count = sum(
            coerce_int(count, 0, minimum=0)
            for status, count in worker_status_counts.items()
            if "failed" in str(status).lower() or "error" in str(status).lower()
        )
        aggregate_status = "failed" if failed_count else ("running" if job_status == "running" else "completed")
        status_text = "，".join(f"{key}={value}" for key, value in worker_status_counts.items()) or "未产生公开 trace"
        agents.append(
            {
                "key": "coding:untraced-attempts",
                "role": "coding",
                "name": "Coding Agent · 无公开 trace 的候选",
                "status": aggregate_status,
                "stage": f"{untraced_worker_attempts} 次候选尝试",
                "detail": status_text,
                "model": str(config.get("coding_worker_model") or config.get("opencode_model") or ""),
                "variant": str(config.get("coding_worker_variant") or ""),
                "updated_at": job.get("updated_at"),
            }
        )

    status_counts = {status: 0 for status in ("queued", "running", "waiting", "completed", "failed", "stopped")}
    for agent in agents:
        status_counts[agent["status"]] += 1
    all_task_keys = {
        f"{record.get('attempt') or 'current'}:{subagent}:{title or 'task'}"
        for record, subagent, _status, title in task_records
    }
    all_coding_keys = {
        str(record.get("agent_key") or record.get("display_name") or record.get("attempt") or "worker")
        for record in coding_trace
    }
    return {
        "summary": {
            **status_counts,
            "configured_subagents": coerce_int(config.get("main_max_subagents"), 0, minimum=0),
            "configured_workers": coerce_int(config.get("max_competing_workers"), 0, minimum=0),
            "started_subagents": len(all_task_keys),
            "started_workers": len(all_coding_keys),
            "reported_worker_attempts": reported_worker_attempts,
            "untraced_worker_attempts": untraced_worker_attempts,
        },
        "agents": agents,
    }


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """过滤内部字段，并限制事件数量，形成浏览器可轮询的任务快照。"""

    return {
        "id": job["id"],
        "title": job["title"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "config": job.get("config", {}),
        "inputs": job.get("inputs", {}),
        "events": job.get("events", [])[-80:],
        "main_agent_trace": job.get("main_agent_trace", []),
        "coding_agent_trace": job.get("coding_agent_trace", []),
        "agent_status": agent_status_snapshot(job),
        "pending_intervention": job.get("pending_intervention"),
        "intervention_history": job.get("intervention_history", []),
        "continuation": job.get("continuation"),
        "continuation_history": job.get("continuation_history", []),
        "summary": job.get("summary", {}),
        "artifacts": job.get("artifacts", {}),
        "error": job.get("error"),
    }


def browser_safe_json(value: Any) -> Any:
    """Return a standards-compliant JSON value for browser `response.json()`."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): browser_safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [browser_safe_json(item) for item in value]
    return value


def write_job_status(job: dict[str, Any]) -> None:
    """同步更新时间并覆盖当前任务的持久化状态快照。"""

    job["updated_at"] = utc_timestamp()
    status_path = Path(job["job_dir"]) / "web_job_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(public_job(job), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_persisted_jobs(output_root: Path, *, limit: int = 30) -> None:
    """服务重启时恢复最近任务；残留 running 状态会标记为已中断。"""

    if not output_root.exists():
        return
    status_paths = sorted(output_root.glob("*/web_job_status.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    with _LOCK:
        for status_path in status_paths[:limit]:
            payload = read_json_file(status_path)
            job_id = str(payload.get("id") or "").strip()
            if not job_id or job_id in _JOBS:
                continue
            if mark_stale_persisted_job_interrupted(payload):
                status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            payload["job_dir"] = str(status_path.parent.resolve())
            payload.setdefault("summary", {})
            payload.setdefault("artifacts", {})
            refresh_persisted_worker_summary(payload)
            _JOBS[job_id] = payload


def mark_stale_persisted_job_interrupted(payload: dict[str, Any]) -> bool:
    """A prior-process queued/running job cannot still execute after server restart."""

    if str(payload.get("status") or "") not in {"queued", "running", "waiting_for_user"}:
        return False
    payload["status"] = "interrupted"
    payload["updated_at"] = utc_timestamp()
    payload["error"] = "后端停止或重启前任务未完成，已标记为中断；不会自动续跑。"
    events = payload.setdefault("events", [])
    if isinstance(events, list):
        events.append(
            {
                "time": payload["updated_at"],
                "level": "warning",
                "message": "检测到上一次后端进程遗留的未完成任务，已标记为中断并保留现有报告。",
            }
        )
    return True


def refresh_persisted_worker_summary(job: dict[str, Any]) -> None:
    artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
    manifest_value = str(artifacts.get("manifest") or "").strip()
    manifest_path = Path(manifest_value) if manifest_value else None
    if manifest_path is None or not manifest_path.exists():
        worker_root = Path(str(job.get("job_dir") or "")) / "run" / "standard_worker_loop" / "worker_loop"
        progress = summarize_code_evolution_progress(worker_root)
        if progress:
            summary = dict(job.get("summary") or {})
            worker_summary = dict(summary.get("worker_summary") or {})
            worker_summary.update(progress)
            summary["worker_summary"] = worker_summary
            summary["round_summary"] = {
                "completed_round_count": progress.get("completed_round_count", 0),
                "reflection_count": progress.get("completed_round_count", 0),
                "harness_report_count": progress.get("evaluated_round_count", 0),
                "round_dirs": progress.get("round_dirs", []),
            }
            job["summary"] = summary
        return
    manifest = read_json_file(manifest_path)
    if not manifest:
        return
    manifest = enrich_worker_manifest_from_loop_result(manifest)
    worker_summary = summarize_worker_manifest(manifest)
    summary = dict(job.get("summary") or {})
    summary["manifest_status"] = manifest.get("status")
    summary["worker_summary"] = worker_summary
    summary["last_summary"] = manifest.get("final_summary") or manifest.get("baseline_summary", {})
    summary["round_summary"] = {
        "completed_round_count": worker_summary["completed_round_count"],
        "reflection_count": worker_summary["completed_round_count"],
        "harness_report_count": worker_summary["completed_round_count"],
        "round_dirs": worker_summary["round_dirs"],
    }
    job["summary"] = summary


def enrich_worker_manifest_from_loop_result(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("final_summary"):
        return manifest
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    loop_result_path = Path(str(artifacts.get("loop_result") or ""))
    if not loop_result_path.exists():
        return manifest
    loop_result = read_json_file(loop_result_path)
    rounds = loop_result.get("rounds") if isinstance(loop_result.get("rounds"), list) else []
    final_key = list(manifest.get("final_key") or loop_result.get("final_key") or [])
    final_summary = manifest.get("baseline_summary") or loop_result.get("baseline_summary") or {}
    final_round_index = None
    latest_candidate_summary = final_summary
    for item in rounds:
        if not isinstance(item, dict):
            continue
        candidate_summary = item.get("candidate_summary") if isinstance(item.get("candidate_summary"), dict) else {}
        if candidate_summary:
            latest_candidate_summary = candidate_summary
        if item.get("decision") == "promoted" and list(item.get("incumbent_key_after") or []) == final_key:
            final_summary = candidate_summary or final_summary
            final_round_index = item.get("round_index")
    enriched = dict(manifest)
    enriched["final_summary"] = final_summary
    enriched["final_round_index"] = final_round_index
    enriched["latest_candidate_summary"] = latest_candidate_summary
    return enriched


# ---------------------------------------------------------------------------
# 示例与 provider 配置：只向 UI 暴露非敏感诊断，不返回任何密钥内容。
# ---------------------------------------------------------------------------

def make_demo_examples() -> dict[str, Any]:
    """返回可直接运行的标准 FJSP DP18a 示例。"""

    requirement_name = "standard_fjsp_requirement.md"
    io_name = "standard_fjsp_io.md"
    requirement = (PROJECT_ROOT / "examples" / requirement_name).read_text(encoding="utf-8")
    io_doc = (PROJECT_ROOT / "examples" / io_name).read_text(encoding="utf-8")
    instance_name, instance = read_default_standard_fjsp_dp18a_instance()
    return {
        "requirement": {"name": requirement_name, "text": requirement},
        "io": {"name": io_name, "text": io_doc},
        "instance": {"name": instance_name, "text": instance},
        "best_known_csv": {
            "name": "standard_fjsp_dp18a_bounds.csv",
            "text": DEFAULT_STANDARD_FJSP_DP18A_BOUNDS_CSV,
        },
        "config": {
            "title": "标准 FJSP DP18a Agent 自写闭环测试",
            "max_rounds": 10,
            "seeds": DEFAULT_STANDARD_SEEDS_TEXT,
            "timeout_seconds": 60,
            "max_workers": 2,
            "worker_max_steps": 4,
            "worker_max_runtime_seconds": 120,
            "in_round_repair_attempts": DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
            "main_max_subagents": 4,
            "main_planning_mode": "fast",
            "max_competing_workers": 4,
            "promotion_repeats": 1,
            "pause_between_rounds": True,
        },
    }


def read_default_standard_fjsp_dp18a_instance() -> tuple[str, str]:
    return (
        DEFAULT_STANDARD_FJSP_DP18A_INSTANCE.name,
        DEFAULT_STANDARD_FJSP_DP18A_INSTANCE.read_text(encoding="utf-8"),
    )


def deepseek_status_payload() -> dict[str, Any]:
    """Return non-secret provider and OpenCode model status for the local UI."""

    load_local_env()
    api_key_present = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    key_file_value = os.environ.get("DEEPSEEK_API_KEY_FILE", "").strip()
    key_file_status = inspect_secret_file(key_file_value)
    configured = api_key_present or bool(key_file_status.get("has_content"))
    openai_configured = opencode_openai_key_available()
    openai_key_source = opencode_openai_key_source()
    env_files = [env_file_status(path) for path in local_env_candidates()]
    env_example = PROJECT_ROOT / ".env.example"
    default_opencode_model = os.environ.get("OPENCODE_MODEL", DEFAULT_OPENCODE_MODEL)
    return {
        "configured": configured,
        "model": normalize_deepseek_model(os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "opencode_model": default_opencode_model,
        "main_agent_model": os.environ.get("OPENCODE_MAIN_MODEL", default_opencode_model),
        "main_agent_variant": normalize_opencode_variant(os.environ.get("OPENCODE_MAIN_VARIANT")),
        "main_planning_mode": normalize_main_planning_mode(
            os.environ.get("OPENCODE_MAIN_PLANNING_MODE")
        ),
        "coding_worker_model": os.environ.get("OPENCODE_WORKER_MODEL", default_opencode_model),
        "coding_worker_variant": normalize_opencode_variant(os.environ.get("OPENCODE_WORKER_VARIANT")),
        "main_max_subagents": coerce_int(
            os.environ.get("OPENCODE_MAIN_MAX_SUBAGENTS"), 4, minimum=0, maximum=4
        ),
        "provider_keys": {
            "deepseek": configured,
            "openai": openai_configured,
        },
        "provider_key_sources": {
            "deepseek": "deepseek" if configured else None,
            "openai": openai_key_source,
        },
        "diagnosis": deepseek_config_diagnosis(
            configured=configured,
            api_key_present=api_key_present,
            key_file_status=key_file_status,
            env_files=env_files,
            env_example=env_example,
        ),
        "api_key_env_present": api_key_present,
        "key_file": key_file_status,
        "checked_env_files": env_files,
        "env_example": {
            "path": str(env_example.resolve()),
            "exists": env_example.is_file(),
            "loaded": False,
            "note": ".env.example 只是模板，不会被自动加载；请复制为 .env 或 .env.local。",
        },
        "help": {
            "accepted_sources": [
                "进程环境变量 DEEPSEEK_API_KEY",
                "进程环境变量 DEEPSEEK_API_KEY_FILE 指向的私有文本文件",
                "OpenAI provider 使用 OPENAI_API_KEY 或 OPENAI_API_KEY_FILE",
                "OpenAI 兼容网关可显式启用 OPENCODE_OPENAI_COMPAT_FROM_DEEPSEEK",
                "FJSP_AGENT_ENV_FILE 指向的 env 文件",
                "仓库根目录或当前工作目录下的 .env / .env.local",
            ],
            "examples": [
                "DEEPSEEK_API_KEY=sk-你的本地密钥",
                r"DEEPSEEK_API_KEY_FILE=C:\Users\ASUS\.secrets\deepseek_api_key.txt",
                "DEEPSEEK_MODEL=deepseek-v4-pro",
                "DEEPSEEK_BASE_URL=https://api.deepseek.com",
                "OPENAI_API_KEY=sk-你的 OpenAI 密钥",
                "OPENCODE_MODEL=openai/gpt-5.4",
            ],
            "safe_note": ".env 和 .env.local 已被 .gitignore 忽略；不要把真实密钥写进 .env.example 或提交到 git。",
        },
        "note": "只返回配置诊断，不返回密钥内容。",
    }


def service_health_payload() -> dict[str, Any]:
    """Return liveness separately from optional model-provider readiness."""

    worker_capabilities = OpenCodeWorker().capabilities()
    return {
        "status": "ok",
        "service": "algoforge-web",
        "opencode_available": worker_capabilities.supports_code_generation,
        "provider_configured": is_deepseek_configured(),
    }


def env_file_status(path: Path) -> dict[str, Any]:
    exists = path.is_file()
    return {
        "path": str(path.resolve()),
        "exists": exists,
        "loaded_if_exists": exists,
    }


def inspect_secret_file(file_path: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "configured": bool(file_path),
        "path": file_path or None,
        "exists": False,
        "readable": False,
        "has_content": False,
        "problem": None,
    }
    if not file_path:
        return status
    path = Path(file_path)
    try:
        status["exists"] = path.is_file()
    except OSError as exc:
        status["problem"] = f"无法检查密钥文件：{exc}"
        return status
    if not status["exists"]:
        status["problem"] = "DEEPSEEK_API_KEY_FILE 指向的文件不存在。"
        return status
    try:
        status["has_content"] = bool(path.read_text(encoding="utf-8").strip())
        status["readable"] = True
    except OSError as exc:
        status["problem"] = f"无法读取密钥文件：{exc}"
    if status["readable"] and not status["has_content"]:
        status["problem"] = "DEEPSEEK_API_KEY_FILE 指向的文件是空的。"
    return status


def deepseek_config_diagnosis(
    *,
    configured: bool,
    api_key_present: bool,
    key_file_status: dict[str, Any],
    env_files: list[dict[str, Any]],
    env_example: Path,
) -> str:
    if configured and api_key_present:
        return "已从 DEEPSEEK_API_KEY 检测到密钥；界面不会显示密钥内容。"
    if configured:
        return "已从 DEEPSEEK_API_KEY_FILE 指向的私有文件检测到密钥；界面不会显示密钥内容。"
    problem = key_file_status.get("problem")
    if problem:
        return str(problem)
    if env_example.is_file() and not any(item["exists"] for item in env_files):
        return "没有检测到 .env/.env.local 或进程环境变量。注意：.env.example 是模板，不会被自动加载。"
    return "没有检测到 DEEPSEEK_API_KEY，也没有可读取的 DEEPSEEK_API_KEY_FILE。"


# ---------------------------------------------------------------------------
# 任务生命周期：create_job 负责固化输入，run_job 负责组装并执行正式闭环。
# ---------------------------------------------------------------------------

def create_job(payload: dict[str, Any], *, output_root: Path | None = None) -> dict[str, Any]:
    """验证并固化一次 Web 提交，返回尚未开始执行的任务记录。

    上传文本会先复制到任务私有目录，后续 Context Packet 和 Core 始终引用
    这份快照，避免用户再次修改表单后影响正在运行的实验。
    """

    output_root = output_root or _ACTIVE_OUTPUT_ROOT
    job_id = time.strftime("%Y%m%d_%H%M%S", time.localtime()) + "_" + uuid.uuid4().hex[:8]
    title = str(payload.get("title") or "AlgoForge Web Run").strip() or "AlgoForge Web Run"
    job_dir = output_root.resolve() / job_id
    input_dir = job_dir / "inputs"
    docs_dir = input_dir / "docs"
    instance_dir = input_dir / "instances"
    docs_dir.mkdir(parents=True, exist_ok=True)
    instance_dir.mkdir(parents=True, exist_ok=True)

    requirement = payload.get("requirement") or {}
    io_doc = payload.get("io") or {}
    instance = payload.get("instance") or {}
    best_known = payload.get("best_known_csv") or {}
    starter_archive = payload.get("starter_project") or {}

    req_path = docs_dir / sanitize_filename(requirement.get("name"), "requirement.md")
    io_path = docs_dir / sanitize_filename(io_doc.get("name"), "io_spec.md")
    instance_path = instance_dir / sanitize_filename(instance.get("name"), "instance.fjs")
    best_known_path = input_dir / sanitize_filename(best_known.get("name"), "best_known.csv")

    req_path.write_text(str(requirement.get("text") or ""), encoding="utf-8")
    io_path.write_text(str(io_doc.get("text") or ""), encoding="utf-8")
    instance_path.write_text(str(instance.get("text") or ""), encoding="utf-8")
    instance_profile = inspect_instance_profile(instance_path)
    best_known_text = str(best_known.get("text") or "").strip()
    if best_known_text:
        best_known_path.write_text(best_known_text + "\n", encoding="utf-8")
    else:
        best_known_path = None

    starter_project: dict[str, Any] | None = None
    starter_project_root: Path | None = None
    starter_entrypoint: str | None = None
    starter_target_file: str | None = None
    starter_solver_command: str | None = None
    if str(starter_archive.get("base64") or "").strip():
        starter_project_root = input_dir / "starter_project"
        starter_project = extract_starter_project(starter_archive, destination=starter_project_root)
        starter_entrypoint = normalize_starter_project_path(
            payload.get("starter_solver_entrypoint"),
            field="starter_solver_entrypoint",
            default="solver.py",
        )
        starter_target_file = normalize_starter_project_path(
            payload.get("starter_target_file"),
            field="starter_target_file",
            default=starter_entrypoint,
        )
        if not (starter_project_root / starter_entrypoint).is_file():
            raise ValueError(f"starter solver entrypoint does not exist: {starter_entrypoint}")
        if not (starter_project_root / starter_target_file).is_file():
            raise ValueError(f"starter target file does not exist: {starter_target_file}")
        starter_solver_command = validate_starter_solver_command(
            payload.get("starter_solver_command"),
            entrypoint=starter_entrypoint,
        )

    # Web 层只接受资源预算，不接受具体算法参数。任何求解方法都必须由
    # Main Agent 从需求/IO/知识库中选择，并由 Coding Agent 实际写出。
    legacy_opencode_model = str(
        payload.get("opencode_model") or os.environ.get("OPENCODE_MODEL") or DEFAULT_OPENCODE_MODEL
    )
    main_agent_model = str(
        payload.get("main_agent_model")
        or os.environ.get("OPENCODE_MAIN_MODEL")
        or legacy_opencode_model
    )
    coding_worker_model = str(
        payload.get("coding_worker_model")
        or os.environ.get("OPENCODE_WORKER_MODEL")
        or legacy_opencode_model
    )
    config = {
        "max_rounds": coerce_int(payload.get("max_rounds"), 2, minimum=1, maximum=20),
        "seeds": parse_seeds(payload.get("seeds", "0")),
        "timeout_seconds": coerce_int(payload.get("timeout_seconds"), 60, minimum=5, maximum=3600),
        "max_workers": coerce_int(payload.get("max_workers"), 1, minimum=1, maximum=8),
        "deepseek_model": str(payload.get("deepseek_model") or "deepseek-v4-pro"),
        "coding_backend": "opencode",
        "opencode_executable": str(payload.get("opencode_executable") or os.environ.get("OPENCODE_EXECUTABLE") or "opencode"),
        # Keep the legacy field as the Worker model for old reports and API clients.
        "opencode_model": coding_worker_model,
        "main_agent_model": main_agent_model,
        "main_agent_variant": normalize_opencode_variant(payload.get("main_agent_variant")),
        "main_planning_mode": normalize_main_planning_mode(
            payload.get("main_planning_mode") or os.environ.get("OPENCODE_MAIN_PLANNING_MODE")
        ),
        "coding_worker_model": coding_worker_model,
        "coding_worker_variant": normalize_opencode_variant(payload.get("coding_worker_variant")),
        "main_max_subagents": coerce_int(
            payload.get("main_max_subagents"), 4, minimum=0, maximum=4
        ),
        "max_competing_workers": coerce_int(
            payload.get("max_competing_workers"), 4, minimum=1, maximum=4
        ),
        "pause_between_rounds": coerce_bool(payload.get("pause_between_rounds"), True),
        "agent_generated_solver_path": str(
            payload.get("agent_generated_solver_path") or "examples/agent_generated_fjsp_solver.py"
        ),
        "worker_max_steps": coerce_int(payload.get("worker_max_steps"), 4, minimum=1, maximum=20),
        "worker_max_runtime_seconds": coerce_int(
            payload.get("worker_max_runtime_seconds"),
            120,
            minimum=10,
            maximum=1800,
        ),
        "in_round_repair_attempts": coerce_int(
            payload.get("in_round_repair_attempts"),
            DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
            minimum=0,
            maximum=8,
        ),
        "promotion_repeats": coerce_int(payload.get("promotion_repeats"), 1, minimum=1, maximum=5),
        "instance_profile": instance_profile,
        "baseline_mode": "provided_project" if starter_project else "agent_generated",
        "starter_solver_entrypoint": starter_entrypoint,
        "starter_target_file": starter_target_file,
        "starter_solver_command": starter_solver_command,
        "starter_project": starter_project,
    }

    job = {
        "id": job_id,
        "title": title,
        "status": "queued",
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "job_dir": str(job_dir),
        "config": config,
        "inputs": {
            "requirement": str(req_path.resolve()),
            "io": str(io_path.resolve()),
            "instance": str(instance_path.resolve()),
            "best_known_csv": str(best_known_path.resolve()) if best_known_path else None,
            "starter_project": str(starter_project_root.resolve()) if starter_project_root else None,
        },
        "events": [],
        "main_agent_trace": [],
        "coding_agent_trace": [],
        "intervention_history": [],
        "pending_intervention": None,
        "summary": {},
        "artifacts": {},
    }
    append_event(
        job,
        (
            "任务材料与现有项目 ZIP 已保存，将先由固定 Core 原样评测 baseline。"
            if starter_project
            else "任务材料已保存，等待进入 Agent 自写 solver 闭环。"
        ),
    )
    append_instance_profile_events(job)
    append_event(
        job,
        (
            "平台不信任 ZIP 内 evaluator、历史实例或解答：只使用当前任务实例和固定 Core；"
            "Main Agent 规划方向，OpenCode Coding Agent 增量修改指定主文件。"
            if starter_project
            else "平台不调用内置求解算法：Main Agent 规划方向，OpenCode Coding Agent 写代码，固定 Core 决定晋升或回滚。"
        ),
    )
    write_job_status(job)
    with _LOCK:
        _JOBS[job_id] = job
    return job


def start_job(job_id: str) -> None:
    """用守护线程启动长任务，使 HTTP 请求可以立即返回 202。"""

    with _LOCK:
        _JOB_CANCELLATIONS.setdefault(job_id, CancellationToken())
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()


def stop_job(job_id: str) -> dict[str, Any]:
    """Request cancellation and terminate active subprocess trees for one job."""

    with _LOCK:
        job = _JOBS.get(job_id)
        cancellation = _JOB_CANCELLATIONS.get(job_id)
        gate = _ROUND_GATES.get(job_id)
        if job is None:
            raise KeyError("job not found")
        status = str(job.get("status") or "")
        if status not in {"queued", "running", "waiting_for_user", "stopping"}:
            return {"accepted": False, "status": status, "reason": "job is already terminal"}
        if status != "stopping":
            job["status"] = "stopping"
            job["stop_requested_at"] = utc_timestamp()
            append_event(job, "用户请求停止任务，正在终止 Main、Coding Worker 和 Core 子进程。", level="warning")
            write_job_status(job)
    if cancellation is not None:
        cancellation.cancel()
    if gate is not None:
        gate.cancel()
    return {"accepted": True, "status": "stopping"}


def resume_job(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Append improvement rounds to a terminal job without regenerating baseline."""

    additional_rounds = coerce_int(payload.get("additional_rounds"), 3, minimum=1, maximum=20)
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError("job not found")
        status = str(job.get("status") or "")
        if status not in {"completed", "completed_with_warnings"}:
            raise ValueError(f"job cannot continue from status: {status}")
        loop_result_path = (
            Path(str(job.get("job_dir") or ""))
            / "run"
            / "standard_worker_loop"
            / "worker_loop"
            / "loop_result.json"
        )
        restored = load_worker_loop_result(loop_result_path)
        completed_rounds = len(restored.rounds)
        target_rounds = completed_rounds + additional_rounds
        requested_at = utc_timestamp()
        continuation = {
            "active": True,
            "requested_at": requested_at,
            "source_status": status,
            "loop_result": str(loop_result_path.resolve()),
            "starting_round_index": max((item.round_index for item in restored.rounds), default=-1) + 1,
            "completed_rounds_before": completed_rounds,
            "additional_rounds": additional_rounds,
            "target_rounds": target_rounds,
        }
        job["continuation"] = continuation
        job.setdefault("continuation_history", []).append(dict(continuation))
        job["config"]["max_rounds"] = target_rounds
        job["status"] = "queued"
        job["error"] = None
        job["pending_intervention"] = None
        append_event(
            job,
            (
                f"用户请求继续迭代：保留原 baseline、{completed_rounds} 轮历史和当前 incumbent，"
                f"从第 {continuation['starting_round_index'] + 1} 轮起追加 {additional_rounds} 轮。"
            ),
        )
        write_job_status(job)
    start_job(job_id)
    return {"accepted": True, "status": "queued", "job": public_job(job)}


def inspect_instance_profile(instance_path: Path) -> dict[str, Any]:
    """用正式 parser 提取算例规模和变种特征，失败时保留可展示错误。"""

    profile: dict[str, Any] = {
        "path": str(instance_path.resolve()),
        "file_name": instance_path.name,
        "format": "unknown",
        "valid": False,
    }
    try:
        parsed = parse_standard_fjsp(instance_path)
    except Exception as exc:  # noqa: BLE001 - keep web job creation inspectable.
        profile["error"] = str(exc)
        return profile
    profile.update(
        {
            "format": "standard_fjsp",
            "valid": True,
            "job_count": parsed.job_count,
            "machine_count": parsed.machine_count,
            "operation_count": parsed.operation_count,
            "max_candidate_count": parsed.max_candidate_count,
            "has_sequence_dependent_setup": parsed.has_sequence_dependent_setup,
            "setup_time_kind": parsed.setup_time_kind,
            "scale": parsed.job_count * parsed.machine_count * parsed.operation_count,
        }
    )
    profile["variant_features"] = profile_variant_features(profile)
    portrait = instance_portrait(profile)
    if portrait:
        profile["instance_portrait"] = portrait
    return profile


def latest_compatible_experience_memory(job: dict[str, Any]) -> Path | None:
    """召回标准格式兼容且含 validated lessons 的最近一次经验。"""

    config = job.get("config") if isinstance(job.get("config"), dict) else {}
    profile = config.get("instance_profile") if isinstance(config.get("instance_profile"), dict) else {}
    expected_format = str(profile.get("format") or "").strip()
    if not expected_format:
        return None
    expected_package_features = method_package_features(profile)
    expected_package_id = str(
        method_package_catalog(problem_family="FJSP", active_features=expected_package_features).get(
            "recommended_package_id"
        )
        or ""
    )
    candidates: list[tuple[float, Path]] = []
    with _LOCK:
        jobs = [dict(item) for item in _JOBS.values()]
    for previous in jobs:
        if previous.get("id") == job.get("id"):
            continue
        if str(previous.get("status") or "") not in {"completed", "completed_with_warnings"}:
            continue
        previous_config = previous.get("config") if isinstance(previous.get("config"), dict) else {}
        previous_profile = (
            previous_config.get("instance_profile")
            if isinstance(previous_config.get("instance_profile"), dict)
            else {}
        )
        if str(previous_profile.get("format") or "").strip() != expected_format:
            continue
        if not variant_features_compatible(profile, previous_profile):
            continue
        if not instance_portrait_compatible(profile, previous_profile):
            continue
        memory_path = (
            Path(str(previous.get("job_dir") or ""))
            / "run"
            / "standard_worker_loop"
            / "worker_loop"
            / "experience_memory.json"
        )
        if not memory_path.is_file():
            continue
        memory = read_json_file(memory_path)
        tiers = memory.get("memory_tiers") if isinstance(memory.get("memory_tiers"), dict) else {}
        validated = [item for item in tiers.get("validated_lessons") or [] if isinstance(item, dict)]
        if not validated:
            continue
        validated_package_ids = {
            str(item.get("method_package_id") or "").strip()
            for item in validated
            if str(item.get("method_package_id") or "").strip()
        }
        if expected_package_id and validated_package_ids and expected_package_id not in validated_package_ids:
            continue
        candidates.append((memory_path.stat().st_mtime, memory_path.resolve()))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def profile_variant_features(profile: dict[str, Any]) -> list[str]:
    features = canonical_variant_feature_set(profile)
    return sorted(features)


def method_package_features(profile: dict[str, Any]) -> list[str]:
    raw = [str(item).strip() for item in (profile.get("variant_features") or []) if str(item).strip()]
    if raw:
        return raw
    if bool(profile.get("has_sequence_dependent_setup")):
        return ["fjsp_sdst", "sequence_dependent_setup", "setup_time"]
    return []


def canonical_variant_feature_set(profile: dict[str, Any]) -> set[str]:
    canonical: set[str] = set()
    aliases = {
        "fjsp_sdst",
        "sdst",
        "sequence_dependent_setup",
        "setup_time",
        "setup_times",
        "setup_matrix",
    }
    for item in profile.get("variant_features") or []:
        text = str(item or "").strip().lower()
        if not text:
            continue
        canonical.add("sequence_dependent_setup" if text in aliases else text)
    if bool(profile.get("has_sequence_dependent_setup")):
        canonical.add("sequence_dependent_setup")
    return canonical


def variant_features_compatible(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    return canonical_variant_feature_set(current) == canonical_variant_feature_set(previous)


def instance_portrait(profile: dict[str, Any]) -> dict[str, str]:
    operation_bucket = numeric_bucket(
        profile.get("operation_count"),
        thresholds=((30, "tiny"), (100, "small"), (250, "medium"), (600, "large")),
        fallback="xlarge",
    )
    machine_bucket = numeric_bucket(
        profile.get("machine_count"),
        thresholds=((5, "small"), (10, "medium"), (20, "large")),
        fallback="xlarge",
    )
    flex_bucket = numeric_bucket(
        profile.get("max_candidate_count"),
        thresholds=((1, "rigid"), (2, "low_flex"), (4, "medium_flex")),
        fallback="high_flex",
    )
    portrait = {
        "operation_bucket": operation_bucket,
        "machine_bucket": machine_bucket,
        "flex_bucket": flex_bucket,
    }
    return {key: value for key, value in portrait.items() if value}


def numeric_bucket(
    value: Any,
    *,
    thresholds: tuple[tuple[int, str], ...],
    fallback: str,
) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    for upper, label in thresholds:
        if number <= upper:
            return label
    return fallback


def instance_portrait_compatible(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    current_portrait = normalized_instance_portrait(current)
    previous_portrait = normalized_instance_portrait(previous)
    if not current_portrait or not previous_portrait:
        return True
    for key in ("operation_bucket", "machine_bucket", "flex_bucket"):
        current_value = str(current_portrait.get(key) or "").strip()
        previous_value = str(previous_portrait.get(key) or "").strip()
        if current_value and previous_value and current_value != previous_value:
            return False
    return True


def normalized_instance_portrait(profile: dict[str, Any]) -> dict[str, str]:
    stored = profile.get("instance_portrait")
    if isinstance(stored, dict) and stored:
        return {
            key: str(stored.get(key) or "").strip()
            for key in ("operation_bucket", "machine_bucket", "flex_bucket")
            if str(stored.get(key) or "").strip()
        }
    return instance_portrait(profile)


def append_instance_profile_events(job: dict[str, Any]) -> None:
    profile = job.get("config", {}).get("instance_profile") or {}
    if not profile.get("valid"):
        append_event(job, f"算例解析失败，时间预算只能使用用户输入：{profile.get('error', '未知错误')}", level="warning")
        return
    append_event(
        job,
        (
            "已按实际算例内容解析规模："
            f"jobs={profile.get('job_count')}，machines={profile.get('machine_count')}，"
            f"operations={profile.get('operation_count')}，max_candidates={profile.get('max_candidate_count')}。"
        ),
    )
    append_event(
        job,
        (
            "Core 评测预算："
            f"每个算例/seed 最多 {format_progress_value(job['config'].get('timeout_seconds'))}s，"
            f"并行数 {job['config'].get('max_workers')}。"
        ),
    )


def run_job(job_id: str) -> None:
    """执行一次 Web 任务并持续写入可恢复状态。

    这里完成角色装配：OpenCode Main 负责只读规划和任务书签发；OpenCode
    Worker 只执行任务书；固定 Core 负责结果合法性和目标复验。
    `run_standard_worker_loop` 返回后，本函数只整理前端摘要，不二次裁决。
    """

    with _LOCK:
        job = _JOBS[job_id]
        cancellation = _JOB_CANCELLATIONS.setdefault(job_id, CancellationToken())
        if cancellation.cancelled:
            job["status"] = "stopped"
            job["stopped_at"] = utc_timestamp()
            append_event(job, "任务在进入执行前已按用户请求停止。", level="warning")
            write_job_status(job)
            _JOB_CANCELLATIONS.pop(job_id, None)
            return
        job["status"] = "running"
        append_event(job, "开始执行文档到 evaluator 的循环迭代。")
        write_job_status(job)

    round_gate: WebRoundInterventionGate | None = None
    try:
        config = job["config"]
        input_paths = job["inputs"]
        output_dir = Path(job["job_dir"]) / "run"
        continuation = job.get("continuation") if isinstance(job.get("continuation"), dict) else {}
        resume_loop_result = (
            Path(str(continuation.get("loop_result"))).resolve()
            if continuation.get("active") and continuation.get("loop_result")
            else None
        )
        run_iterations = (
            int(continuation.get("additional_rounds", 0) or 0)
            if resume_loop_result is not None
            else int(config["max_rounds"])
        )
        append_event(
            job,
            (
                f"启动 {'现有项目增量演进' if config.get('baseline_mode') == 'provided_project' else 'Agent 自写 solver'}闭环："
                f"本次 rounds={run_iterations}，"
                f"seeds={config['seeds']}，Core 并行数={config['max_workers']}。"
            ),
        )
        write_job_status(job)
        # OpenCode 是代码编辑运行时，model/provider 通过其配置注入；它和
        # DeepSeek 不是两个并列修改代码的 Agent。
        coding_worker = OpenCodeWorker(
            executable=config["opencode_executable"],
            model=config["coding_worker_model"] or None,
            variant=config["coding_worker_variant"] or None,
            timeout_seconds=config["worker_max_runtime_seconds"],
            cancellation=cancellation,
        )
        if not coding_worker.capabilities().supports_code_generation:
            raise RuntimeError("OpenCode 不可用，请先安装或构建 OpenCode worker。")
        direction_planner = OpenCodeMainAgent(
            executable=config["opencode_executable"],
            model=config["main_agent_model"] or None,
            variant=config["main_agent_variant"] or None,
            planning_mode=config["main_planning_mode"],
            project_root=PROJECT_ROOT,
            max_subagents=config["main_max_subagents"],
            cancellation=cancellation,
        )
        if config.get("pause_between_rounds") and (
            run_iterations > 1 or resume_loop_result is not None
        ):
            round_gate = WebRoundInterventionGate(job, cancellation=cancellation)
            with _LOCK:
                _ROUND_GATES[job_id] = round_gate
        # 跨任务记忆只召回同变种、同方法包且已经过 Core/语义验证的经验。
        previous_memory_path = latest_compatible_experience_memory(job)
        if previous_memory_path:
            append_event(job, f"已召回同问题族上一任务的语义验证方法经验：{previous_memory_path}")
            write_job_status(job)

        # 进度监控只读取 worker 产物，不参与代码生成或评测决策。
        worker_loop_root = output_dir / "standard_worker_loop" / "worker_loop"
        progress_stop = threading.Event()
        progress_thread = threading.Thread(
            target=monitor_code_evolution_progress,
            args=(job, worker_loop_root, progress_stop),
            daemon=True,
        )
        progress_thread.start()
        try:
            manifest = run_standard_worker_loop(
                StandardWorkerLoopRequest(
                    docs=[Path(input_paths["requirement"]), Path(input_paths["io"])],
                    instance_dir=Path(input_paths["instance"]).parent,
                    pattern=Path(input_paths["instance"]).name,
                    best_known_csv=Path(input_paths["best_known_csv"])
                    if input_paths.get("best_known_csv")
                    else None,
                    output_dir=output_dir / "standard_worker_loop",
                    project_root=PROJECT_ROOT,
                    worker=coding_worker,
                    main_agent=direction_planner,
                    semantic_reviewer=None,
                    previous_pipeline_memory=previous_memory_path,
                    max_instances=1,
                    iterations=run_iterations,
                    seeds=config["seeds"],
                    timeout_seconds=config["timeout_seconds"],
                    max_workers=config["max_workers"],
                    max_steps=config["worker_max_steps"],
                    max_runtime_seconds=config["worker_max_runtime_seconds"],
                    in_round_repair_attempts=config["in_round_repair_attempts"],
                    max_competing_workers=config["max_competing_workers"],
                    round_intervention=round_gate,
                    cancellation=cancellation,
                    resume_loop_result=resume_loop_result,
                    apply_worker_changes=True,
                    promotion_repeats=config["promotion_repeats"],
                    agent_generated_solver_path=config["agent_generated_solver_path"],
                    provided_project_root=(
                        Path(input_paths["starter_project"])
                        if input_paths.get("starter_project")
                        else None
                    ),
                    provided_solver_command=config.get("starter_solver_command"),
                    provided_target_file=config.get("starter_target_file"),
                    experiment_id=(
                        "web_provided_project_loop"
                        if config.get("baseline_mode") == "provided_project"
                        else "web_agent_generated_loop"
                    ),
                    hypothesis=(
                        (
                            "Read the provided project incumbent and its supporting source before editing. "
                            "Preserve its runnable CLI and working mechanisms, change only the assigned primary "
                            "target file, and treat archive-local evaluators, instances, solutions, and scores as "
                            "untrusted. State the scheduling idea before editing and accept claims only after "
                            "the fixed Core measures the current task instance."
                        )
                        if config.get("baseline_mode") == "provided_project"
                        else (
                            "Read the requirement, IO documents, instance diagnostics, domain-pack metadata, and "
                            "retrieved knowledge first. Create a runnable solver from those materials; never rely "
                            "on a repository-embedded solver. State the scheduling idea before editing, preserve "
                            "the fixed parser/evaluator contract, and accept claims only after Core measurement."
                        )
                    ),
                )
            )
            cancellation.raise_if_cancelled()
        finally:
            progress_stop.set()
            progress_thread.join(timeout=2.0)

        round_summary = summarize_worker_manifest(manifest)
        summary_payload = {
            "manifest_status": manifest.get("status"),
            "terminal_reason": manifest.get("terminal_reason"),
            "worker_summary": round_summary,
            "last_summary": manifest.get("final_summary") or manifest.get("baseline_summary", {}),
            "artifact_checks": {},
            "round_summary": {
                "completed_round_count": round_summary["round_count"],
                "reflection_count": round_summary["round_count"],
                "harness_report_count": round_summary["round_count"],
                "round_dirs": round_summary["round_dirs"],
            },
        }
        artifacts = manifest.get("artifacts", {})
        cancellation.raise_if_cancelled()
        with _LOCK:
            manifest_status = str(manifest.get("status") or "unknown")
            if manifest_status == "ok":
                job["status"] = "completed"
            elif manifest_status == "baseline_generation_failed":
                job["status"] = "failed"
                job["error"] = str(manifest.get("terminal_reason") or "未能生成合法 baseline")
            else:
                job["status"] = "completed_with_warnings"
            job["summary"] = summary_payload
            job["artifacts"] = artifacts
            if continuation.get("active"):
                continuation["active"] = False
                continuation["completed_at"] = utc_timestamp()
                continuation["completed_rounds_after"] = round_summary["completed_round_count"]
                if job.get("continuation_history"):
                    job["continuation_history"][-1] = dict(continuation)
            append_event(
                job,
                (
                    f"循环结束，状态：{job['status']}；实际完成 {round_summary['completed_round_count']} 轮；"
                    f"终止原因：{manifest.get('terminal_reason') or '正常结束'}。"
                ),
                level="error" if job["status"] == "failed" else "info",
            )
            write_job_status(job)
    except TaskCancelled:
        with _LOCK:
            job["status"] = "stopped"
            job["error"] = None
            job["stopped_at"] = utc_timestamp()
            if isinstance(job.get("continuation"), dict):
                job["continuation"]["active"] = False
                job["continuation"]["stopped_at"] = job["stopped_at"]
                if job.get("continuation_history"):
                    job["continuation_history"][-1] = dict(job["continuation"])
            append_event(job, "任务已按用户请求停止；已完成产物和 incumbent 已保留。", level="warning")
            write_job_status(job)
    except Exception as exc:  # noqa: BLE001 - web jobs should preserve failures as inspectable artifacts.
        trace_path = Path(job["job_dir"]) / "web_job_exception.txt"
        trace_path.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
        with _LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)
            if isinstance(job.get("continuation"), dict):
                job["continuation"]["active"] = False
                job["continuation"]["failed_at"] = utc_timestamp()
                if job.get("continuation_history"):
                    job["continuation_history"][-1] = dict(job["continuation"])
            job["artifacts"]["exception"] = str(trace_path.resolve())
            append_event(job, f"执行失败：{exc}", level="error")
            write_job_status(job)
    finally:
        with _LOCK:
            _ROUND_GATES.pop(job_id, None)
            _JOB_CANCELLATIONS.pop(job_id, None)


class WebRoundInterventionGate:
    """Require explicit opt-in before Main changes the active method family."""

    def __init__(
        self,
        job: dict[str, Any],
        cancellation: CancellationToken | None = None,
        *,
        timeout_seconds: float = DEFAULT_DIRECTION_CHANGE_CONFIRMATION_SECONDS,
    ) -> None:
        self.job = job
        self.cancellation = cancellation
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self._condition = threading.Condition()
        self._submitted = False
        self._direction: Any = None
        self._resolution = ""
        self._deadline_monotonic: float | None = None

    def __call__(self, next_round_index: int, previous_round: Any, proposed_direction: dict[str, Any]) -> Any:
        if not direction_change_proposed(previous_round, proposed_direction):
            return None
        self.publish(
            next_round_index=next_round_index,
            previous_round=previous_round,
            proposed_direction=proposed_direction,
        )
        direction = self.wait_for_submission()
        with _LOCK:
            pending = dict(self.job.get("pending_intervention") or {})
            pending["status"] = "resolved"
            pending["submitted_direction"] = direction
            pending["resolved_at"] = utc_timestamp()
            pending["resolution"] = self._resolution
            self.job.setdefault("intervention_history", []).append(pending)
            self.job["pending_intervention"] = None
            self.job["status"] = "running"
            if self._resolution == "timeout_continue":
                event_message = (
                    f"换向建议等待 {self.timeout_seconds:g} 秒未获用户同意；"
                    f"第 {next_round_index + 1} 轮默认保持当前方向。"
                )
            elif self._resolution == "user_continue":
                event_message = f"用户不同意换向；第 {next_round_index + 1} 轮保持当前方向。"
            elif direction:
                event_message = (
                    f"用户已指定第 {next_round_index + 1} 轮方向：{intervention_display_text(direction)}"
                )
            else:
                event_message = f"用户同意 Main Agent 换向建议，继续第 {next_round_index + 1} 轮。"
            append_event(self.job, event_message)
            write_job_status(self.job)
        return direction

    def publish(self, *, next_round_index: int, previous_round: Any, proposed_direction: dict[str, Any]) -> None:
        with self._condition:
            self._submitted = False
            self._direction = None
            self._resolution = ""
            self._deadline_monotonic = time.monotonic() + self.timeout_seconds
        analysis = {
            key: proposed_direction.get(key)
            for key in (
                "title",
                "hypothesis",
                "diagnosis",
                "observed_shortcomings",
                "reasoning_trace",
                "incumbent_assessment",
                "evidence_summary",
                "direction_judgment",
                "alternatives_considered",
                "selection_rationale",
                "change_scope",
                "next_mutation",
                "acceptance_checks",
            )
        }
        previous_plan = getattr(previous_round, "direction_plan", None)
        previous_plan = previous_plan if isinstance(previous_plan, dict) else {}
        pending = {
            "status": "waiting",
            "completed_round_index": int(getattr(previous_round, "round_index", next_round_index - 1)),
            "completed_round_decision": str(getattr(previous_round, "decision", "unknown")),
            "candidate_key": list(getattr(previous_round, "candidate_key", ()) or ()),
            "incumbent_key_after": list(getattr(previous_round, "incumbent_key_after", ()) or ()),
            "next_round_index": next_round_index,
            "main_analysis": analysis,
            "requested_at": utc_timestamp(),
            "recommendation_kind": "direction_change",
            "timeout_seconds": self.timeout_seconds,
            "default_action": "continue_current_direction",
            "current_method_family": str(previous_plan.get("method_family") or ""),
            "proposed_method_family": str(proposed_direction.get("method_family") or ""),
        }
        with _LOCK:
            self.job["status"] = "waiting_for_user"
            self.job["pending_intervention"] = pending
            append_event(
                self.job,
                (
                    f"第 {pending['completed_round_index'] + 1} 轮已完成；Main Agent 建议换方向。"
                    f"等待用户同意，{self.timeout_seconds:g} 秒无响应将保持当前方向。"
                ),
                level="warning",
            )
            write_job_status(self.job)

    def submit(
        self,
        direction: str | None,
        *,
        action: str = "revise",
        resolution: str = "user_submission",
    ) -> None:
        with self._condition:
            if self._submitted:
                raise ValueError("direction-change decision is already resolved")
            normalized_direction = str(direction or "").strip()[:4_000]
            normalized_action = str(action or "revise").strip().lower().replace("-", "_")
            if normalized_direction and normalized_action in {"pivot", "research_tournament"}:
                self._direction = {
                    "direction": normalized_direction,
                    "direction_patch": {
                        "action": normalized_action,
                        "instructions": normalized_direction,
                        "preserve_unspecified": True,
                    },
                }
            elif normalized_direction:
                self._direction = {
                    "source": "user_rejected_direction_change",
                    "direction": normalized_direction,
                    "direction_patch": {
                        "action": "revise",
                        "instructions": normalized_direction,
                        "preserve_unspecified": True,
                    },
                }
            else:
                self._direction = None
            self._resolution = resolution
            self._submitted = True
            self._condition.notify_all()

    def continue_current_direction(self, *, resolution: str = "user_continue") -> None:
        with self._condition:
            if self._submitted:
                raise ValueError("direction-change decision is already resolved")
            self._set_continue_current_direction(resolution=resolution)
            self._condition.notify_all()

    def _set_continue_current_direction(self, *, resolution: str) -> None:
        self._direction = {
            "source": (
                "direction_change_timeout_default_continue"
                if resolution == "timeout_continue"
                else "user_rejected_direction_change"
            ),
            "direction": DIRECTION_CHANGE_REJECTION_INSTRUCTION,
            "direction_patch": {
                "action": "continue",
                "instructions": DIRECTION_CHANGE_REJECTION_INSTRUCTION,
                "preserve_unspecified": True,
            },
        }
        self._resolution = resolution
        self._submitted = True

    def cancel(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def wait_for_submission(self) -> Any:
        with self._condition:
            while not self._submitted:
                if self.cancellation is not None:
                    self.cancellation.raise_if_cancelled()
                remaining = (self._deadline_monotonic or time.monotonic()) - time.monotonic()
                if remaining <= 0:
                    self._set_continue_current_direction(resolution="timeout_continue")
                    break
                self._condition.wait(timeout=min(0.5, remaining))
            if self.cancellation is not None:
                self.cancellation.raise_if_cancelled()
            return self._direction


def direction_change_proposed(previous_round: Any, proposed_direction: dict[str, Any]) -> bool:
    stage = str(proposed_direction.get("experiment_stage") or "").strip().lower().replace("-", "_")
    if stage in {"pivot", "research_tournament"}:
        return True
    selection = (
        proposed_direction.get("direction_selection")
        if isinstance(proposed_direction.get("direction_selection"), dict)
        else {}
    )
    selection_reason = str(selection.get("selection_reason") or "").strip().lower()
    if "pivot" in selection_reason or "research_tournament" in selection_reason:
        return True
    previous_plan = getattr(previous_round, "direction_plan", None)
    previous_plan = previous_plan if isinstance(previous_plan, dict) else {}
    previous_family = str(previous_plan.get("method_family") or "").strip().lower()
    proposed_family = str(proposed_direction.get("method_family") or "").strip().lower()
    return bool(previous_family and proposed_family and previous_family != proposed_family)


def intervention_display_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("direction") or value.get("direction_patch") or "")[:4_000]
    return str(value or "")[:4_000]


def submit_round_intervention(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS.get(job_id)
        gate = _ROUND_GATES.get(job_id)
    if job is None:
        raise KeyError("job not found")
    if gate is None or job.get("status") != "waiting_for_user":
        raise ValueError("job is not waiting for a between-round intervention")
    use_main = coerce_bool(payload.get("use_main_recommendation"), False)
    continue_current = coerce_bool(payload.get("continue_current_direction"), False)
    direction = None if use_main else str(payload.get("direction") or "").strip()
    action = str(payload.get("action") or "revise").strip().lower().replace("-", "_")
    if action not in {"revise", "pivot", "research_tournament"}:
        raise ValueError("action must be revise, pivot, or research_tournament")
    if use_main and continue_current:
        raise ValueError("choose either the Main recommendation or the current direction")
    if not use_main and not continue_current and not direction:
        raise ValueError("direction is required unless use_main_recommendation=true")
    if continue_current:
        gate.continue_current_direction()
    else:
        gate.submit(
            direction,
            action=action,
            resolution="user_accept_main" if use_main else "user_submission",
        )
    return {
        "accepted": True,
        "use_main_recommendation": use_main,
        "continue_current_direction": continue_current,
        "direction": direction or None,
        "action": "accept" if use_main else "continue" if continue_current else action,
    }


# ---------------------------------------------------------------------------
# 产物与洞察视图：把原始 JSON/Markdown 压缩成前端可扫描摘要，不改变证据。
# ---------------------------------------------------------------------------

def read_artifact(job: dict[str, Any], name: str) -> dict[str, Any]:
    """读取白名单中的任务产物，并限制浏览器预览体积。"""

    path = job_artifact_path(job, name)
    if path is None:
        raise KeyError(f"unknown artifact: {name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > MAX_ARTIFACT_CHARS
    return {
        "name": name,
        "path": str(path.resolve()),
        "text": text[:MAX_ARTIFACT_CHARS],
        "truncated": truncated,
    }


def job_artifact_path(job: dict[str, Any], name: str) -> Path | None:
    """只允许读取任务清单登记过的产物，避免任意路径读取。"""

    allowed: dict[str, str] = {
        "status": str((Path(job["job_dir"]) / "web_job_status.json").resolve()),
        **{key: str(value) for key, value in (job.get("artifacts") or {}).items() if value},
    }
    if name not in allowed:
        return None
    return Path(allowed[name])


def read_json_artifact(job: dict[str, Any], name: str) -> Any:
    path = job_artifact_path(job, name)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def job_insights(job: dict[str, Any]) -> dict[str, Any]:
    """汇总上下文、Worker、实验和知识四个前端面板的数据。"""

    context_packet = read_json_artifact(job, "context_packet")
    if not isinstance(context_packet, dict):
        context_packet = {}
    loop_result = read_json_artifact(job, "loop_result")
    if not isinstance(loop_result, dict):
        loop_result = {}
    hypothesis_graph = read_json_artifact(job, "hypothesis_graph")
    if not isinstance(hypothesis_graph, dict):
        hypothesis_graph = loop_result.get("hypothesis_graph") if isinstance(loop_result.get("hypothesis_graph"), dict) else {}
    experience_memory = read_json_artifact(job, "experience_memory")
    if not isinstance(experience_memory, dict):
        experience_memory = loop_result.get("experience_memory") if isinstance(loop_result.get("experience_memory"), dict) else {}
    skill_usage_records = read_json_artifact(job, "skill_usage_records")
    if not isinstance(skill_usage_records, list):
        skill_usage_records = loop_result.get("skill_usage_records") if isinstance(loop_result.get("skill_usage_records"), list) else []
    return {
        "job_id": job.get("id"),
        "context": summarize_context_insight(context_packet),
        "worker": summarize_worker_insight(job, loop_result, hypothesis_graph),
        "experiments": summarize_experiment_insight(job, loop_result),
        "knowledge": summarize_knowledge_insight(experience_memory, skill_usage_records),
    }


def summarize_context_insight(packet: dict[str, Any]) -> dict[str, Any]:
    documents = packet.get("documents") if isinstance(packet.get("documents"), list) else []
    knowledge_cards = packet.get("knowledge_cards") if isinstance(packet.get("knowledge_cards"), list) else []
    auto_cards = packet.get("auto_knowledge_cards") if isinstance(packet.get("auto_knowledge_cards"), list) else []
    diagnostics = packet.get("instance_diagnostics") if isinstance(packet.get("instance_diagnostics"), dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    hints = diagnostics.get("direction_hints") if isinstance(diagnostics.get("direction_hints"), list) else []
    worker_instruction = packet.get("worker_instruction")
    instruction_order = []
    if isinstance(worker_instruction, dict):
        instruction_order = worker_instruction.get("required_order") if isinstance(worker_instruction.get("required_order"), list) else []
    task = packet.get("task") if isinstance(packet.get("task"), dict) else {}
    document_chars = sum(json_int(item.get("chars"), 0) for item in documents if isinstance(item, dict))
    sources = [
        {
            "state": "selected",
            "title": "Task Contract",
            "detail": f"{task.get('problem_family', 'FJSP')} · {task.get('review_status', 'unknown')}",
        },
        {
            "state": "selected",
            "title": "Requirement / IO Docs",
            "detail": f"{len(documents)} 个文档，{document_chars} chars",
        },
        {
            "state": "selected",
            "title": "Instance Diagnostics",
            "detail": context_instance_detail(summary),
        },
        {
            "state": "selected",
            "title": "Knowledge / Skills",
            "detail": f"{len(knowledge_cards)} 张固定知识卡，{len(auto_cards)} 个自动检索候选",
        },
    ]
    if instruction_order:
        sources.append(
            {
                "state": "selected",
                "title": "Worker Instruction",
                "detail": summarize_list(instruction_order, limit=2),
            }
        )
    return {
        "packet_hash": str(packet.get("packet_hash") or "-"),
        "contract_hash": str(packet.get("contract_hash") or "-"),
        "document_count": len(documents),
        "knowledge_card_count": len(knowledge_cards),
        "auto_knowledge_card_count": len(auto_cards),
        "selected_source_count": len(sources),
        "excluded_source_count": 0,
        "sources": sources,
        "documents": [compact_document(item) for item in documents if isinstance(item, dict)],
        "diagnostics": {
            "status": diagnostics.get("status"),
            "summary": summary,
            "direction_hints": [str(item) for item in hints[:5]],
        },
    }


def context_instance_detail(summary: dict[str, Any]) -> str:
    parts = [
        f"{summary.get('profiled_count', summary.get('instance_count', '-'))} 个算例",
        f"SDST {summary.get('sdst_instance_count', '-')}",
        f"最大工序 {summary.get('max_operation_count', '-')}",
    ]
    setup_kinds = summary.get("setup_time_kinds")
    if isinstance(setup_kinds, list) and setup_kinds:
        parts.append(f"setup={','.join(str(item) for item in setup_kinds[:3])}")
    return " · ".join(parts)


def compact_document(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": Path(str(item.get("path") or "")).name or "document",
        "path": str(item.get("path") or ""),
        "exists": bool(item.get("exists")),
        "chars": int(item.get("chars", 0) or 0),
        "sha256": str(item.get("sha256") or "")[:12],
    }


def json_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def summarize_worker_insight(
    job: dict[str, Any],
    loop_result: dict[str, Any],
    hypothesis_graph: dict[str, Any],
) -> dict[str, Any]:
    directions = hypothesis_graph.get("directions") if isinstance(hypothesis_graph.get("directions"), list) else []
    rounds = loop_result.get("rounds") if isinstance(loop_result.get("rounds"), list) else []
    if directions:
        compact_rounds = [compact_direction(item, rounds) for item in directions if isinstance(item, dict)]
    else:
        compact_rounds = [compact_loop_round(item) for item in rounds if isinstance(item, dict)]
    summary = (job.get("summary") or {}).get("worker_summary")
    if not isinstance(summary, dict):
        summary = {}
    return {
        "rounds": compact_rounds,
        "direction_count": int(hypothesis_graph.get("direction_count", summary.get("direction_count", len(compact_rounds))) or 0),
        "attempt_count": int(hypothesis_graph.get("attempt_count", summary.get("attempt_count", len(compact_rounds))) or 0),
        "status_counts": hypothesis_graph.get("status_counts") or summary.get("direction_status_counts") or {},
        "decision_counts": hypothesis_graph.get("decision_counts") or summary.get("decision_counts") or {},
        "active_parent_id": hypothesis_graph.get("active_parent_id"),
    }


def compact_direction(item: dict[str, Any], rounds: list[Any]) -> dict[str, Any]:
    round_index = json_int(item.get("round_index"), 0)
    matching_round = next(
        (
            round_item
            for round_item in rounds
            if isinstance(round_item, dict) and json_int(round_item.get("round_index"), -1) == round_index
        ),
        {},
    )
    candidate_summary = matching_round.get("candidate_summary") if isinstance(matching_round, dict) else {}
    if not isinstance(candidate_summary, dict):
        candidate_summary = {}
    metrics = summary_metrics(candidate_summary)
    semantic_review = (
        matching_round.get("semantic_review")
        if isinstance(matching_round.get("semantic_review"), dict)
        else {}
    )
    matching_direction_plan = (
        matching_round.get("direction_plan")
        if isinstance(matching_round.get("direction_plan"), dict)
        else item.get("direction_plan") or {}
    )
    attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
    failures: list[str] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        for signature in attempt.get("failure_signatures") or []:
            text = str(signature)
            if text and text not in failures:
                failures.append(text)
    hypotheses = item.get("hypotheses") if isinstance(item.get("hypotheses"), list) else []
    evidence: list[str] = []
    if hypotheses and isinstance(hypotheses[0], dict):
        evidence = [str(value) for value in (hypotheses[0].get("evidence_used") or [])[:4]]
    mechanism_activation = public_mechanism_activation(item.get("mechanism_activation"))
    round_reflection = public_round_reflection(item.get("round_reflection"))
    return {
        "round_index": round_index,
        "title": str(item.get("title") or f"round_{round_index:03d}"),
        "status": str(item.get("status") or "unknown"),
        "decision": str(item.get("decision") or "unknown"),
        "strategy_type": str(item.get("strategy_type") or "-"),
        "strategy_intent": str(item.get("strategy_intent") or ""),
        "diagnosis": str((item.get("direction_plan") or {}).get("diagnosis") or ""),
        "reasoning_trace": list((item.get("direction_plan") or {}).get("reasoning_trace") or [])[:12],
        "incumbent_assessment": dict((item.get("direction_plan") or {}).get("incumbent_assessment") or {}),
        "next_mutation": dict((item.get("direction_plan") or {}).get("next_mutation") or {}),
        "selection_rationale": str((item.get("direction_plan") or {}).get("selection_rationale") or ""),
        "implementation_order": list((item.get("direction_plan") or {}).get("implementation_order") or [])[:12],
        "planner": str((item.get("direction_plan") or {}).get("planner") or ""),
        "worker_assignments": [
            {
                "assignment_id": attempt.get("assignment_id"),
                "artifact_path": attempt.get("worker_assignment_path"),
            }
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("worker_assignment_path")
        ],
        "attempt_count": int(item.get("attempt_count", len(attempts)) or 0),
        "score_relation": str(item.get("score_relation") or ""),
        "makespan": metrics.get("makespan"),
        "gap_pct": metrics.get("gap_pct"),
        "valid": int(candidate_summary.get("valid", 0) or 0),
        "total": int(candidate_summary.get("total", 0) or 0),
        "failure_signatures": failures[:4],
        "evidence_used": evidence,
        "hypothesis_outcome": str(
            item.get("hypothesis_outcome") or round_reflection.get("hypothesis_outcome") or "-"
        ),
        "mechanism_activation": mechanism_activation,
        "round_reflection": round_reflection,
        "semantic_review_status": semantic_review.get("status"),
        "semantic_finding_count": len(semantic_review.get("findings") or []),
        "competition": compact_competition_result(matching_direction_plan.get("competition_result")),
    }


def compact_loop_round(item: dict[str, Any]) -> dict[str, Any]:
    candidate_summary = item.get("candidate_summary") if isinstance(item.get("candidate_summary"), dict) else {}
    metrics = summary_metrics(candidate_summary)
    diagnostics = item.get("proposal_diagnostics") if isinstance(item.get("proposal_diagnostics"), dict) else {}
    summary = str(diagnostics.get("summary") or "")
    round_index = json_int(item.get("round_index"), 0)
    semantic_review = item.get("semantic_review") if isinstance(item.get("semantic_review"), dict) else {}
    direction_plan = item.get("direction_plan") if isinstance(item.get("direction_plan"), dict) else {}
    repair = diagnostics.get("in_round_repair") if isinstance(diagnostics.get("in_round_repair"), dict) else {}
    attempts = repair.get("attempts") if isinstance(repair.get("attempts"), list) else []
    mechanism_activation = public_mechanism_activation(item.get("mechanism_activation"))
    round_reflection = public_round_reflection(item.get("round_reflection"))
    return {
        "round_index": round_index,
        "title": summary[:80] or f"round_{round_index:03d}",
        "status": "validated_success" if item.get("decision") == "promoted" else "rolled_back",
        "decision": str(item.get("decision") or "unknown"),
        "strategy_type": str(direction_plan.get("strategy_type") or "-"),
        "strategy_intent": str(direction_plan.get("hypothesis") or diagnostics.get("strategy_intent") or summary),
        "diagnosis": str(direction_plan.get("diagnosis") or ""),
        "reasoning_trace": list(direction_plan.get("reasoning_trace") or [])[:12],
        "incumbent_assessment": dict(direction_plan.get("incumbent_assessment") or {}),
        "next_mutation": dict(direction_plan.get("next_mutation") or {}),
        "selection_rationale": str(direction_plan.get("selection_rationale") or ""),
        "implementation_order": list(direction_plan.get("implementation_order") or [])[:12],
        "planner": str(direction_plan.get("planner") or ""),
        "worker_assignments": [
            {
                "assignment_id": attempt.get("assignment_id"),
                "artifact_path": attempt.get("worker_assignment_path"),
            }
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("worker_assignment_path")
        ],
        "attempt_count": max(1, len(attempts)),
        "score_relation": "",
        "makespan": metrics.get("makespan"),
        "gap_pct": metrics.get("gap_pct"),
        "valid": int(candidate_summary.get("valid", 0) or 0),
        "total": int(candidate_summary.get("total", 0) or 0),
        "failure_signatures": [],
        "hypothesis_outcome": str(
            round_reflection.get("hypothesis_outcome")
            or ("supported" if item.get("decision") == "promoted" else "-")
        ),
        "mechanism_activation": mechanism_activation,
        "round_reflection": round_reflection,
        "semantic_review_status": semantic_review.get("status"),
        "semantic_finding_count": len(semantic_review.get("findings") or []),
        "evidence_used": [],
        "competition": compact_competition_result(direction_plan.get("competition_result")),
    }


def compact_competition_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = []
    for item in value.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "candidate_id": item.get("candidate_id"),
                "status": item.get("status"),
                "eligible": bool(item.get("eligible")),
                "objective_key": list(item.get("objective_key") or [])[:4],
                "ja_accepted": item.get("ja_accepted"),
                "core_eligible": item.get("core_eligible"),
                "semantic_eligible": item.get("semantic_eligible"),
                "worker_status": item.get("worker_status"),
            }
        )
    return {
        "status": value.get("status"),
        "candidate_count": int(value.get("candidate_count", len(candidates)) or 0),
        "eligible_candidate_count": int(value.get("eligible_candidate_count", 0) or 0),
        "selected_candidate_id": value.get("selected_candidate_id"),
        "selected_for_promotion": bool(value.get("selected_for_promotion")),
        "candidates": candidates[:4],
    }


def summarize_experiment_insight(job: dict[str, Any], loop_result: dict[str, Any]) -> dict[str, Any]:
    summary = (job.get("summary") or {}).get("worker_summary")
    if not isinstance(summary, dict):
        summary = {}
    rounds = loop_result.get("rounds") if isinstance(loop_result.get("rounds"), list) else []
    trend = []
    baseline_makespan = first_number(summary.get("baseline_makespan"))
    if baseline_makespan is not None:
        trend.append(
            {
                "label": "baseline",
                "makespan": baseline_makespan,
                "decision": "baseline",
                "valid": summary.get("baseline_valid"),
                "total": summary.get("baseline_total"),
                "gap_pct": None,
            }
        )
    for item in rounds:
        if not isinstance(item, dict):
            continue
        candidate_summary = item.get("candidate_summary") if isinstance(item.get("candidate_summary"), dict) else {}
        metrics = summary_metrics(candidate_summary)
        trend.append(
            {
                "label": f"R{int(item.get('round_index', 0) or 0) + 1}",
                "makespan": metrics.get("makespan"),
                "decision": item.get("decision"),
                "valid": candidate_summary.get("valid"),
                "total": candidate_summary.get("total"),
                "gap_pct": metrics.get("gap_pct"),
            }
        )
    return {
        "baseline_makespan": baseline_makespan,
        "final_makespan": first_number(summary.get("final_makespan")),
        "best_makespan": first_number(summary.get("best_makespan_so_far"), summary.get("final_makespan")),
        "final_gap_pct": first_number(summary.get("final_gap_pct"), summary.get("best_gap_pct_so_far")),
        "promoted_rounds": int(summary.get("promoted_rounds", 0) or 0),
        "round_count": int(summary.get("round_count", len(rounds)) or 0),
        "final_valid": int(summary.get("final_valid", 0) or 0),
        "final_total": int(summary.get("final_total", 0) or 0),
        "trend": trend,
    }


def summarize_knowledge_insight(experience_memory: dict[str, Any], skill_usage_records: list[Any]) -> dict[str, Any]:
    tiers = experience_memory.get("memory_tiers") if isinstance(experience_memory.get("memory_tiers"), dict) else {}
    candidate_lessons = tiers.get("candidate_lessons") if isinstance(tiers.get("candidate_lessons"), list) else []
    validated_lessons = tiers.get("validated_lessons") if isinstance(tiers.get("validated_lessons"), list) else []
    skill_summary = experience_memory.get("skill_usage_summary") if isinstance(experience_memory.get("skill_usage_summary"), dict) else {}
    semantic_memory = (
        experience_memory.get("algorithm_semantic_memory")
        if isinstance(experience_memory.get("algorithm_semantic_memory"), dict)
        else {}
    )
    return {
        "purpose": experience_memory.get("purpose") or "运行后按层沉淀经验",
        "lesson_count": len(candidate_lessons),
        "validated_lesson_count": len(validated_lessons),
        "skill_usage_record_count": len(skill_usage_records),
        "skill_usage_summary": skill_summary,
        "algorithm_semantic_memory": semantic_memory,
        "lessons": [compact_lesson(item) for item in candidate_lessons[:8] if isinstance(item, dict)],
        "skill_usage_records": [compact_usage_record(item) for item in skill_usage_records[:12] if isinstance(item, dict)],
    }


def compact_lesson(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    mechanism_activation = (
        evidence.get("mechanism_activation")
        if isinstance(evidence.get("mechanism_activation"), dict)
        else {}
    )
    return {
        "lesson_type": str(item.get("lesson_type") or "-"),
        "strategy": str(item.get("strategy") or "-"),
        "outcome": str(item.get("outcome") or "-"),
        "hypothesis_outcome": str(item.get("hypothesis_outcome") or evidence.get("hypothesis_outcome") or "-"),
        "confidence": str(item.get("confidence") or "-"),
        "mechanism_activation_status": str(mechanism_activation.get("status") or "-"),
        "recommended_skill_update": str(item.get("recommended_skill_update") or ""),
    }


def public_mechanism_activation(value: Any) -> dict[str, Any]:
    activation = value if isinstance(value, dict) else {}
    if not activation:
        return {}
    checks: list[dict[str, Any]] = []
    for item in activation.get("checks") or []:
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                "id": item.get("id"),
                "path": item.get("path"),
                "required": bool(item.get("required", True)),
                "passed": item.get("passed"),
                "description": str(item.get("description") or "")[:300],
            }
        )
        if len(checks) >= 8:
            break
    return {
        "status": activation.get("status"),
        "passed": activation.get("passed"),
        "declared_check_count": activation.get("declared_check_count"),
        "required_check_count": activation.get("required_check_count"),
        "required_failure_count": activation.get("required_failure_count"),
        "checks": checks,
    }


def public_round_reflection(value: Any) -> dict[str, Any]:
    reflection = value if isinstance(value, dict) else {}
    if not reflection:
        return {}
    findings: list[dict[str, Any]] = []
    for item in reflection.get("candidate_findings") or []:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "candidate_id": str(item.get("candidate_id") or "")[:80],
                "outcome": normalize_public_hypothesis_outcome(item.get("outcome")),
                "causal_interpretation": str(item.get("causal_interpretation") or "")[:900],
            }
        )
        if len(findings) >= 4:
            break
    next_action = reflection.get("next_action") if isinstance(reflection.get("next_action"), dict) else {}
    return {
        "hypothesis_outcome": normalize_public_hypothesis_outcome(
            reflection.get("hypothesis_outcome") or reflection.get("status")
        ),
        "summary": str(reflection.get("summary") or "")[:1200],
        "candidate_findings": findings,
        "next_action": {
            "action": str(next_action.get("action") or "")[:80],
            "rationale": str(next_action.get("rationale") or "")[:1200],
            "required_activation_checks": [str(item)[:300] for item in (next_action.get("required_activation_checks") or [])[:12]],
        },
    }


def normalize_public_hypothesis_outcome(value: Any) -> str:
    outcome = str(value or "").strip().lower()
    if outcome == "supported":
        return "supported"
    if outcome == "refuted":
        return "refuted"
    if outcome in {"mixed", "inconclusive", "inconclusive_not_exercised"}:
        return "inconclusive"
    return ""


def compact_usage_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "round_index": item.get("round_index"),
        "source": str(item.get("source") or "-"),
        "source_kind": str(item.get("source_kind") or "-"),
        "effect": str(item.get("effect") or "-"),
        "decision": str(item.get("decision") or "-"),
    }


def summarize_worker_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    baseline_key = list(manifest.get("baseline_key") or [])
    final_key = list(manifest.get("final_key") or [])
    baseline_summary = manifest.get("baseline_summary") or {}
    final_summary = manifest.get("final_summary") or {}
    latest_summary = manifest.get("latest_candidate_summary") or final_summary
    rounds = manifest.get("rounds") or []
    round_dirs = [str(Path(item.get("cycle_dir", "")).resolve()) for item in manifest.get("rounds", []) if item.get("cycle_dir")]
    decision_counts = status_counts([str(item.get("decision") or "unknown") for item in rounds])
    worker_status_counts = status_counts([str(item.get("worker_status") or "unknown") for item in rounds])
    rejected_before_eval = sum(1 for item in rounds if list(item.get("candidate_key") or []) and all(value == float("-inf") for value in item.get("candidate_key") or []))
    in_round_repair = manifest.get("in_round_repair")
    if not isinstance(in_round_repair, dict):
        in_round_repair = summarize_in_round_repair(rounds)
    hypothesis_graph = manifest.get("hypothesis_graph") if isinstance(manifest.get("hypothesis_graph"), dict) else {}
    experience_memory = (
        manifest.get("experience_memory") if isinstance(manifest.get("experience_memory"), dict) else {}
    )
    memory_tiers = (
        experience_memory.get("memory_tiers") if isinstance(experience_memory.get("memory_tiers"), dict) else {}
    )
    candidate_lessons = memory_tiers.get("candidate_lessons") if isinstance(memory_tiers.get("candidate_lessons"), list) else []
    skill_usage_records = manifest.get("skill_usage_records")
    if not isinstance(skill_usage_records, list):
        skill_usage_records = experience_memory.get("skill_usage_records")
    if not isinstance(skill_usage_records, list):
        skill_usage_records = []
    final_metrics = summary_metrics(final_summary)
    latest_metrics = summary_metrics(latest_summary)
    diagnostic_fields = summarize_diagnostic_summaries(collect_valid_diagnostic_summaries(manifest))
    return {
        "round_count": int(manifest.get("round_count", 0) or 0),
        "completed_round_count": int(manifest.get("round_count", 0) or 0),
        "direction_count": int(hypothesis_graph.get("direction_count", manifest.get("round_count", 0)) or 0),
        "attempt_count": int(hypothesis_graph.get("attempt_count", manifest.get("round_count", 0)) or 0),
        "candidate_lesson_count": len(candidate_lessons),
        "skill_usage_record_count": len(skill_usage_records),
        "semantic_review": manifest.get("algorithm_semantic_review") or {},
        "direction_status_counts": hypothesis_graph.get("status_counts") or {},
        "direction_decision_counts": hypothesis_graph.get("decision_counts") or {},
        "promoted_rounds": int(manifest.get("promoted_rounds", 0) or 0),
        "improved": bool(manifest.get("improved")),
        "baseline_key": baseline_key,
        "final_key": final_key,
        "baseline_makespan": objective_key_to_makespan(baseline_key),
        "final_makespan": final_metrics.get("makespan") or objective_key_to_makespan(final_key),
        "final_gap_pct": final_metrics.get("gap_pct"),
        "final_total": int(final_summary.get("total", 0) or 0),
        "final_valid": int(final_summary.get("valid", 0) or 0),
        "final_failed": int(final_summary.get("failed", 0) or 0),
        "latest_makespan": latest_metrics.get("makespan"),
        "latest_gap_pct": latest_metrics.get("gap_pct"),
        "latest_total": int(latest_summary.get("total", 0) or 0),
        "latest_valid": int(latest_summary.get("valid", 0) or 0),
        "latest_failed": int(latest_summary.get("failed", 0) or 0),
        "baseline_total": int(baseline_summary.get("total", 0) or 0),
        "baseline_valid": int(baseline_summary.get("valid", 0) or 0),
        "baseline_failed": int(baseline_summary.get("failed", 0) or 0),
        "decision_counts": decision_counts,
        "worker_status_counts": worker_status_counts,
        "rejected_before_eval": rejected_before_eval,
        "in_round_repair": in_round_repair,
        "round_dirs": round_dirs,
        **diagnostic_fields,
    }


def collect_valid_diagnostic_summaries(value: Any) -> list[dict[str, Any]]:
    """Collect evaluator-proven diagnostic summaries from nested loop artifacts."""

    summaries: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            diagnostic = node.get("diagnostic_smoke")
            if isinstance(diagnostic, dict):
                summary = diagnostic.get("summary") if isinstance(diagnostic.get("summary"), dict) else {}
                total = int(summary.get("total", 0) or 0)
                valid = int(summary.get("valid", 0) or 0)
                if total > 0 and valid == total:
                    summaries.append(summary)
            for key, child in node.items():
                if key != "diagnostic_smoke":
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return summaries


def summarize_diagnostic_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose diagnostic metrics separately from promotable Core metrics."""

    if not summaries:
        return {}
    latest = summaries[-1]
    best = best_progress_summary(summaries)
    latest_metrics = summary_metrics(latest)
    best_metrics = summary_metrics(best)
    return {
        "has_valid_diagnostic": True,
        "diagnostic_makespan": best_metrics.get("makespan"),
        "latest_diagnostic_makespan": latest_metrics.get("makespan"),
        "diagnostic_total": int(latest.get("total", 0) or 0),
        "diagnostic_valid": int(latest.get("valid", 0) or 0),
        "diagnostic_failed": int(latest.get("failed", 0) or 0),
        "diagnostic_promotable": False,
    }


def summarize_in_round_repair(rounds: list[Any]) -> dict[str, Any]:
    repair_attempt_count = 0
    repair_round_count = 0
    recovered_round_count = 0
    final_rejected_after_repair = 0
    for item in rounds:
        diagnostics = item.get("proposal_diagnostics") if isinstance(item, dict) else {}
        repair = diagnostics.get("in_round_repair") if isinstance(diagnostics, dict) else None
        if not isinstance(repair, dict):
            continue
        attempts = int(repair.get("repair_attempt_count", 0) or 0)
        if attempts <= 0:
            continue
        repair_round_count += 1
        repair_attempt_count += attempts
        if repair.get("recovered"):
            recovered_round_count += 1
        elif list(item.get("candidate_key") or []) and all(
            isinstance(value, (int, float)) and float(value) == float("-inf")
            for value in item.get("candidate_key") or []
        ):
            final_rejected_after_repair += 1
    return {
        "repair_round_count": repair_round_count,
        "repair_attempt_count": repair_attempt_count,
        "recovered_round_count": recovered_round_count,
        "final_rejected_after_repair": final_rejected_after_repair,
    }


def summary_metrics(summary: dict[str, Any]) -> dict[str, float | None]:
    best_metrics = summary.get("best_metrics") if isinstance(summary.get("best_metrics"), dict) else {}
    candidate_metrics = (
        summary.get("best_candidate_metrics") if isinstance(summary.get("best_candidate_metrics"), dict) else {}
    )
    return {
        "makespan": first_number(best_metrics.get("makespan"), candidate_metrics.get("avg_makespan")),
        "gap_pct": first_number(
            best_metrics.get("gap_pct"),
            best_metrics.get("gap_to_ub_pct"),
            candidate_metrics.get("avg_gap_pct"),
            candidate_metrics.get("avg_gap_to_ub_pct"),
        ),
    }


def first_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def status_counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def objective_key_to_makespan(key: list[Any]) -> float | None:
    if not key:
        return None
    try:
        value = float(key[0])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return -value


# ---------------------------------------------------------------------------
# 进度扫描：仅观察正在生成的文件并写事件，不参与任何实验或晋升决策。
# ---------------------------------------------------------------------------

def monitor_code_evolution_progress(job: dict[str, Any], worker_root: Path, stop_event: threading.Event) -> None:
    """轮询 Worker 产物目录，把新阶段转换为 Web 事件，直到任务线程结束。"""

    seen: set[str] = set()
    while True:
        scan_code_evolution_progress(job, worker_root, seen)
        if stop_event.wait(1.5):
            scan_code_evolution_progress(job, worker_root, seen)
            return


def scan_code_evolution_progress(job: dict[str, Any], worker_root: Path, seen: set[str]) -> None:
    if not worker_root.exists():
        return
    record_progress_event(
        job,
        seen,
        "baseline-started",
        "代码层已创建基线 worktree，正在运行 evaluator 基线。",
    )
    if (worker_root / "baseline_harness" / "report.md").exists():
        record_progress_event(job, seen, "baseline-report", "基线 evaluator 已完成，开始进入 DeepSeek 代码演进轮次。")

    generated_baseline_dir = worker_root / "agent_generated_baseline"
    if generated_baseline_dir.exists():
        record_progress_event(
            job,
            seen,
            "agent-generated-baseline-started",
            "Agent 正在从需求、IO 与知识卡生成初始 solver，并执行基线审查。",
        )
        for attempt_dir, label in worker_attempt_dirs(generated_baseline_dir):
            scan_code_attempt_progress(job, seen, attempt_dir, label)

    for round_dir in sorted(path for path in worker_root.glob("round_*") if path.is_dir()):
        for attempt_dir, label in worker_attempt_dirs(round_dir):
            scan_code_attempt_progress(job, seen, attempt_dir, label)
        scan_round_reflection_progress(job, seen, round_dir)
    record_code_evolution_progress_summary(job, worker_root)


def scan_code_attempt_progress(job: dict[str, Any], seen: set[str], attempt_dir: Path, label: str) -> None:
        if (attempt_dir / "context_packet.json").exists():
            record_progress_event(job, seen, f"{label}:context", f"{label} 已生成 Main/审查侧上下文包。")
        planning_packet = attempt_dir / "main_agent" / "planning_packet.json"
        if planning_packet.exists():
            record_progress_event(job, seen, f"{label}:planning-packet", f"{label} Main Agent 进入阶段一：根据问题与算例画像选择方法族。")
        incumbent_audit = attempt_dir / "main_agent" / "incumbent_capability_audit.json"
        if incumbent_audit.exists():
            audit = read_json_file(incumbent_audit)
            summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
            record_progress_event(
                job,
                seen,
                f"{label}:incumbent-capability-audit",
                (
                    f"{label} 已完成 incumbent 结构审计："
                    f"functions={summary.get('function_count', 0)}，"
                    f"configurations={summary.get('configuration_count', 0)}，"
                    f"loops={summary.get('loop_count', 0)}。"
                ),
            )
        direction_selection = attempt_dir / "main_agent" / "direction_selection.json"
        if direction_selection.exists():
            selection = read_json_file(direction_selection)
            record_progress_event(
                job,
                seen,
                f"{label}:direction-selection",
                (
                    f"{label} Main Agent 阶段一完成：方法族={selection.get('method_family') or '-'}；"
                    f"检索标签={summarize_list(selection.get('knowledge_query') or [])}。"
                ),
            )
        implementation_packet = attempt_dir / "main_agent" / "implementation_planning_packet.json"
        if implementation_packet.exists():
            record_progress_event(
                job,
                seen,
                f"{label}:implementation-planning-packet",
                f"{label} Main Agent 进入阶段二：读取定向实现知识并签发完整任务书。",
            )
        scan_opencode_main_trace(job, seen, attempt_dir, label)
        scan_opencode_worker_trace(job, seen, attempt_dir, label)
        direction_plan = attempt_dir / "main_agent" / "direction_plan.json"
        if direction_plan.exists():
            plan = read_json_file(direction_plan)
            record_progress_event(
                job,
                seen,
                f"{label}:direction-plan",
                (
                    f"{label} Main Agent 已选方向：{plan.get('title') or '-'}；"
                    f"方法包={plan.get('method_package_id') or '-'}；"
                    f"理由={str(plan.get('selection_rationale') or '-')[:180]}"
                ),
            )
        assignment_paths = [attempt_dir / "worker_assignment.json", *sorted(attempt_dir.glob("assignment_revision_*.json"))]
        for assignment_path in assignment_paths:
            if not assignment_path.exists():
                continue
            assignment = read_json_file(assignment_path)
            record_progress_event(
                job,
                seen,
                f"{label}:assignment:{assignment_path.name}",
                (
                    f"{label} Main Agent 已签发 {assignment.get('assignment_id') or assignment_path.name}："
                    f"deliverables={len(assignment.get('deliverables') or [])}，"
                    f"read_set={len(assignment.get('read_set') or [])}。"
                ),
            )
        opencode_budget = attempt_dir / "worker" / "opencode_context_budget.json"
        if opencode_budget.exists():
            budget = read_json_file(opencode_budget)
            record_progress_event(
                job,
                seen,
                f"{label}:opencode-budget",
                (
                    f"{label} OpenCode Worker 输入：policy={budget.get('stable_policy_chars', '-')} chars，"
                    f"assignment={budget.get('assignment_chars', '-')} chars，"
                    f"full_context_visible={budget.get('full_context_packet_visible', '-')}。"
                ),
            )
        raw_response = attempt_dir / "worker" / "deepseek_code_edit_raw.json"
        if raw_response.exists():
            record_progress_event(job, seen, f"{label}:raw", f"{label} DeepSeek 已返回原始代码修改响应。")
        usage_path = attempt_dir / "worker" / "deepseek_usage.json"
        if usage_path.exists():
            usage_payload = read_json_file(usage_path)
            usage = usage_payload.get("usage") if isinstance(usage_payload.get("usage"), dict) else {}
            cache_ratio = usage_payload.get("cache_hit_ratio")
            cache_text = "-" if cache_ratio is None else f"{float(cache_ratio) * 100:.1f}%"
            record_progress_event(
                job,
                seen,
                f"{label}:usage",
                (
                    f"{label} 模型用量：prompt={usage.get('prompt_tokens', '-')}，"
                    f"completion={usage.get('completion_tokens', '-')}，缓存命中={cache_text}。"
                ),
            )
        proposal = attempt_dir / "worker" / "proposal.md"
        if proposal.exists():
            record_progress_event(job, seen, f"{label}:proposal", f"{label} 已生成结构化代码修改 proposal。")
        judgment = attempt_dir / "agentic_judgment.json"
        if judgment.exists():
            judgment_payload = read_json_file(judgment)
            accepted = judgment_payload.get("accepted")
            issues = judgment_payload.get("issues") or []
            checks = judgment_payload.get("checks") if isinstance(judgment_payload.get("checks"), dict) else {}
            soft_acceptance = (
                checks.get("soft_accepted_by_diagnostic_smoke")
                if isinstance(checks.get("soft_accepted_by_diagnostic_smoke"), dict)
                else {}
            )
            if accepted:
                message = f"{label} JA 诊断无异常；该诊断不参与候选淘汰，结果仍由 Core evaluator 决定。"
            else:
                message = (
                    f"{label} JA 记录了诊断项（仅供审计，不阻止 Core）："
                    f"{summarize_list(issues)}"
                )
            level = "info"
            record_progress_event(job, seen, f"{label}:agentic-judgment", message, level=level)
            if accepted and soft_acceptance:
                original_issues = soft_acceptance.get("original_issues") or []
                diagnostic_metrics = (
                    soft_acceptance.get("diagnostic_metrics")
                    if isinstance(soft_acceptance.get("diagnostic_metrics"), dict)
                    else {}
                )
                record_progress_event(
                    job,
                    seen,
                    f"{label}:agentic-judgment-soft-accepted",
                    (
                        f"{label} 兼容诊断已证明输出合法，历史软门禁被降级并放行正式评估："
                        f"原始问题={summarize_list(original_issues)}，"
                        f"diagnostic_makespan={format_progress_value(first_number(diagnostic_metrics.get('makespan'), diagnostic_metrics.get('avg_makespan')))}。"
                    ),
                    level="warning",
                )
        error_analysis = attempt_dir / "agentic_error_analysis.json"
        if error_analysis.exists():
            analysis_payload = read_json_file(error_analysis)
            diagnosis = analysis_payload.get("diagnosis") or []
            record_progress_event(
                job,
                seen,
                f"{label}:agentic-error-analysis",
                f"{label} EAA 已生成错误分析：{summarize_list(diagnosis)}",
                level="error" if diagnosis else "info",
            )
        exception = attempt_dir / "cycle_exception.txt"
        if exception.exists():
            record_progress_event(
                job,
                seen,
                f"{label}:exception",
                f"{label} 执行异常，已作为下一轮反馈：{summarize_exception(exception)}",
                level="error",
            )
        cycle_result = attempt_dir / "cycle_result.json"
        if cycle_result.exists():
            payload = read_json_file(cycle_result)
            worker = payload.get("worker", {}) if isinstance(payload, dict) else {}
            summary = payload.get("harness", {}) if isinstance(payload, dict) else {}
            best_metrics = summary.get("best_metrics") or summary.get("best_candidate_metrics") or {}
            makespan = best_metrics.get("makespan", best_metrics.get("avg_makespan"))
            diagnostic_summaries = collect_valid_diagnostic_summaries(payload)
            diagnostic_summary = diagnostic_summaries[-1] if diagnostic_summaries else {}
            diagnostic_metrics = summary_metrics(diagnostic_summary)
            formal_total = int(summary.get("total", 0) or 0)
            if formal_total <= 0 and diagnostic_summary:
                message = (
                    f"{label} 候选预检未放行正式 Core evaluator；兼容诊断已证明当前输出合法："
                    f"valid={diagnostic_summary.get('valid', '-')}，"
                    f"diagnostic_makespan={format_progress_value(diagnostic_metrics.get('makespan'))}。"
                    "该结果仅用于诊断，不参与 promotion。"
                )
                level = "warning"
            else:
                message = (
                    f"{label} evaluator 已完成：worker={worker.get('status', 'unknown')}，"
                    f"valid={summary.get('valid', '-')}，makespan={format_progress_value(makespan)}。"
                )
                level = "info"
            record_progress_event(
                job,
                seen,
                f"{label}:cycle-result",
                message,
                level=level,
            )
        patch = attempt_dir / "worker_changes.patch"
        if patch.exists() and patch.stat().st_size > 0:
            record_progress_event(job, seen, f"{label}:patch", f"{label} 产生候选代码 patch，等待提升判定。")


def worker_attempt_dirs(round_dir: Path) -> list[tuple[Path, str]]:
    attempts: list[tuple[Path, str]] = [(round_dir, round_dir.name)]
    for repair_dir in sorted(path for path in round_dir.glob("repair_*") if path.is_dir()):
        suffix = repair_dir.name.replace("repair_", "Local Trial ")
        attempts.append((repair_dir, f"{round_dir.name} {suffix}"))
    candidates_dir = round_dir / "candidates"
    for candidate_dir in sorted(path for path in candidates_dir.glob("*") if path.is_dir()):
        candidate_label = f"{round_dir.name} 候选 {candidate_dir.name}"
        attempts.append((candidate_dir, candidate_label))
        for repair_dir in sorted(path for path in candidate_dir.glob("repair_*") if path.is_dir()):
            suffix = repair_dir.name.replace("repair_", "Local Trial ")
            attempts.append((repair_dir, f"{candidate_label} {suffix}"))
    return attempts


def scan_round_reflection_progress(
    job: dict[str, Any],
    seen: set[str],
    round_dir: Path,
) -> None:
    reflection_dir = round_dir / "main_agent_reflection"
    if not reflection_dir.is_dir():
        return
    label = f"{round_dir.name} 轮后反思"
    records: list[dict[str, Any]] = []
    for path in sorted(reflection_dir.glob("opencode_main_events*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines):
            key = f"{round_dir.name}:reflection-trace:{path.name}:{index}"
            if key in seen:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(key)
            record = opencode_main_trace_record(payload, label=label, record_id=key)
            if record is not None:
                records.append(record)
    reflection_path = reflection_dir / "round_reflection.json"
    if reflection_path.exists():
        reflection = read_json_file(reflection_path)
        action = (
            reflection.get("next_action")
            if isinstance(reflection.get("next_action"), dict)
            else {}
        )
        record_progress_event(
            job,
            seen,
            f"{round_dir.name}:round-reflection",
            (
                f"{round_dir.name} Main Agent 已完成轮后因果复盘："
                f"结论={reflection.get('hypothesis_outcome') or '-'}；"
                f"下一动作={action.get('action') or '-'}。"
            ),
        )
    if records:
        with _LOCK:
            if job.get("status") != "running":
                return
            trace = [item for item in job.get("main_agent_trace") or [] if isinstance(item, dict)]
            trace.extend(records)
            job["main_agent_trace"] = trace[-240:]
            write_job_status(job)


def scan_opencode_main_trace(
    job: dict[str, Any],
    seen: set[str],
    attempt_dir: Path,
    label: str,
) -> None:
    """Collect the Main Agent's model-visible trace without exposing hidden reasoning.

    OpenCode JSONL can contain very large tool inputs, including source patches.
    The browser trace intentionally keeps only public commentary, tool identity,
    completion state, final answers, and usage counters.
    """

    records: list[dict[str, Any]] = []
    for directory_name, trace_label in (
        ("main_agent", label),
        ("main_agent_user_revision", f"{label} · 用户修订"),
    ):
        main_dir = attempt_dir / directory_name
        has_native_commentary = any(
            item.get("attempt") == trace_label and item.get("kind") == "commentary"
            for item in job.get("main_agent_trace") or []
            if isinstance(item, dict)
        )
        for path in sorted(main_dir.glob("opencode_main_events*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines):
                key = f"{label}:{directory_name}:main-trace:{path.name}:{index}"
                if key in seen:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = opencode_main_trace_record(payload, label=trace_label, record_id=key)
                if record is None:
                    seen.add(key)
                    continue
                seen.add(key)
                records.append(record)
                if record.get("kind") == "commentary":
                    has_native_commentary = True
        public_trace_path = main_dir / "main_reasoning_trace.json"
        # The structured trace is generated from the final plan. It is useful when a
        # provider emits no commentary, but must not masquerade as live thinking.
        if public_trace_path.exists() and not has_native_commentary:
            public_trace = read_json_file(public_trace_path)
            timestamp_base = int(public_trace_path.stat().st_mtime * 1000)
            for index, entry in enumerate(public_trace.get("entries") or []):
                if not isinstance(entry, dict):
                    continue
                key = f"{label}:{directory_name}:main-public-reasoning:{index}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    {
                        "id": key,
                        "attempt": trace_label,
                        "timestamp": timestamp_base + index,
                        "kind": "analysis",
                        "stage": str(entry.get("stage") or "分析"),
                        "text": format_public_reasoning_entry(entry),
                    }
                )
    if not records:
        return
    with _LOCK:
        if job.get("status") != "running":
            return
        job.setdefault("main_agent_trace", []).extend(records)
        write_job_status(job)


def scan_opencode_worker_trace(
    job: dict[str, Any],
    seen: set[str],
    attempt_dir: Path,
    label: str,
) -> None:
    """Collect one Coding Agent's public progress without exposing tool payloads."""

    worker_dir = attempt_dir / "worker"
    events_path = worker_dir / "opencode_events.jsonl"
    if not events_path.is_file():
        return
    identity = coding_agent_identity(attempt_dir, label, worker_dir / "opencode_command.json")
    try:
        # The monitor can read while OpenCode is midway through a UTF-8 line.
        # Replacement decoding keeps that partial line retryable on the next poll.
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        key = f"{label}:coding-trace:{index}"
        if key in seen:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        seen.add(key)
        record = opencode_worker_trace_record(
            payload,
            label=label,
            record_id=key,
            identity=identity,
        )
        if record is not None:
            records.append(record)
    if not records:
        return
    with _LOCK:
        if job.get("status") != "running":
            return
        trace = [item for item in job.get("coding_agent_trace") or [] if isinstance(item, dict)]
        trace.extend(records)
        job["coding_agent_trace"] = trace[-800:]
        write_job_status(job)


def coding_agent_identity(attempt_dir: Path, label: str, command_path: Path) -> dict[str, str]:
    """Derive stable display identity and actual model settings for a Worker run."""

    parts = attempt_dir.parts
    candidate_id = ""
    if "candidates" in parts:
        index = parts.index("candidates")
        if index + 1 < len(parts):
            candidate_id = parts[index + 1]
    elif attempt_dir.name == "agent_generated_baseline":
        candidate_id = "baseline"
    repair = attempt_dir.name if attempt_dir.name.startswith("repair_") else ""
    round_match = re.search(r"round_\d+", label)
    round_id = round_match.group(0) if round_match else "baseline"
    display_name = candidate_id or attempt_dir.name
    if repair:
        display_name = f"{display_name} / {repair.replace('_', ' ')}"

    command: list[Any] = []
    try:
        raw_command = json.loads(command_path.read_text(encoding="utf-8"))
        if isinstance(raw_command, list):
            command = raw_command
    except (OSError, json.JSONDecodeError):
        pass

    def flag_value(flag: str) -> str:
        try:
            flag_index = command.index(flag)
        except ValueError:
            return ""
        if flag_index + 1 >= len(command):
            return ""
        return str(command[flag_index + 1]).strip()

    agent_key = f"{round_id}:{candidate_id or attempt_dir.name}:{repair or 'primary'}"
    return {
        "agent_key": agent_key,
        "display_name": display_name,
        "candidate_id": candidate_id,
        "round": round_id,
        "repair": repair,
        "model": flag_value("--model"),
        "variant": flag_value("--variant"),
    }


def opencode_worker_trace_record(
    payload: dict[str, Any],
    *,
    label: str,
    record_id: str,
    identity: dict[str, str],
) -> dict[str, Any] | None:
    """Whitelist browser-safe Worker fields from an OpenCode JSONL event."""

    event_type = str(payload.get("type") or "")
    part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
    base = {
        "id": record_id,
        "attempt": label,
        "timestamp": payload.get("timestamp"),
        **identity,
    }
    if event_type == "text":
        text = str(part.get("text") or "").strip()
        if not text:
            return None
        metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
        openai_metadata = metadata.get("openai") if isinstance(metadata.get("openai"), dict) else {}
        phase = str(openai_metadata.get("phase") or "commentary")
        return {
            **base,
            "kind": "final" if phase == "final_answer" else "commentary",
            "phase": phase,
            "text": text[:8_000],
        }
    if event_type == "tool_use":
        tool = str(part.get("tool") or "tool")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = str(state.get("status") or "unknown")
        title = str(state.get("title") or "").strip()
        detail = f"{tool} / {status}"
        if title and title.lower() != tool.lower():
            detail += f" / {title[:300]}"
        return {
            **base,
            "kind": "tool",
            "tool": tool,
            "status": status,
            "title": title[:300],
            "text": detail,
        }
    if event_type == "step_finish":
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        if not tokens:
            return None
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        text = (
            f"input={tokens.get('input', 0)}，output={tokens.get('output', 0)}，"
            f"reasoning={tokens.get('reasoning', 0)}，cache={cache.get('read', 0)}"
        )
        return {**base, "kind": "usage", "text": text}
    return None


def opencode_main_trace_record(
    payload: dict[str, Any],
    *,
    label: str,
    record_id: str,
) -> dict[str, Any] | None:
    event_type = str(payload.get("type") or "")
    part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
    timestamp = payload.get("timestamp")
    base = {
        "id": record_id,
        "attempt": label,
        "timestamp": timestamp,
    }
    if event_type == "text":
        text = str(part.get("text") or "").strip()
        if not text:
            return None
        try:
            structured = json.loads(text)
        except json.JSONDecodeError:
            structured = None
        if isinstance(structured, dict) and (
            isinstance(structured.get("direction_selection"), dict)
            or isinstance(structured.get("direction_plan"), dict)
        ):
            # 最终 JSON 由结构化公开研究日志和方向卡展示，避免在对话区
            # 重复倾倒一整块机器可读 payload。
            return None
        metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
        openai_metadata = metadata.get("openai") if isinstance(metadata.get("openai"), dict) else {}
        phase = str(openai_metadata.get("phase") or "commentary")
        return {
            **base,
            "kind": "final" if phase == "final_answer" else "commentary",
            "phase": phase,
            "text": text[:8_000],
        }
    if event_type == "tool_use":
        tool = str(part.get("tool") or "tool")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
        subagent = first_nonempty_text(
            tool_input.get("subagent_type"),
            tool_input.get("agent"),
            tool_input.get("name"),
        )
        status = str(state.get("status") or "unknown")
        title = str(state.get("title") or "").strip()
        detail = f"{tool}"
        if subagent:
            detail += f" / {subagent}"
        detail += f" / {status}"
        if title and title.lower() != tool.lower():
            detail += f" / {title[:300]}"
        return {
            **base,
            "kind": "tool",
            "tool": tool,
            "subagent": subagent,
            "status": status,
            "title": title[:300],
            "text": detail,
        }
    if event_type == "step_finish":
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        if not tokens:
            return None
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        text = (
            f"input={tokens.get('input', 0)}，output={tokens.get('output', 0)}，"
            f"reasoning={tokens.get('reasoning', 0)}，cache={cache.get('read', 0)}"
        )
        return {**base, "kind": "usage", "text": text}
    return None


def format_public_reasoning_entry(entry: dict[str, Any]) -> str:
    """把公开研究日志变成接近工程工作记录的中文段落。"""

    parts = [str(entry.get("summary") or "").strip()]
    evidence = [str(value).strip() for value in entry.get("evidence") or [] if str(value).strip()]
    if evidence:
        parts.append("证据：" + "；".join(evidence))
    inference = str(entry.get("inference") or "").strip()
    if inference:
        parts.append("判断：" + inference)
    decision = str(entry.get("decision") or "").strip()
    if decision:
        parts.append("决定：" + decision)
    next_check = str(entry.get("next_check") or "").strip()
    if next_check:
        parts.append("下一项验证：" + next_check)
    return "\n".join(part for part in parts if part)[:8_000]


def first_nonempty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def final_worker_attempt_dir(round_dir: Path) -> Path:
    final_dir = round_dir
    for attempt_dir, _label in worker_attempt_dirs(round_dir):
        if (attempt_dir / "cycle_result.json").exists():
            final_dir = attempt_dir
    return final_dir


def record_progress_event(
    job: dict[str, Any],
    seen: set[str],
    key: str,
    message: str,
    *,
    level: str = "info",
) -> None:
    if key in seen:
        return
    seen.add(key)
    with _LOCK:
        if job.get("status") != "running":
            return
        append_event(job, message, level=level)
        write_job_status(job)


def record_code_evolution_progress_summary(job: dict[str, Any], worker_root: Path) -> None:
    progress = summarize_code_evolution_progress(worker_root)
    if not progress:
        return
    with _LOCK:
        if job.get("status") != "running":
            return
        summary = dict(job.get("summary") or {})
        worker_summary = dict(summary.get("worker_summary") or {})
        worker_summary.update(progress)
        summary["worker_summary"] = worker_summary
        summary["round_summary"] = {
            "completed_round_count": progress.get("completed_round_count", 0),
            "reflection_count": progress.get("completed_round_count", 0),
            "harness_report_count": progress.get("evaluated_round_count", 0),
            "round_dirs": progress.get("round_dirs", []),
        }
        job["summary"] = summary
        write_job_status(job)


def summarize_code_evolution_progress(worker_root: Path) -> dict[str, Any]:
    if not worker_root.exists():
        return {}
    round_dirs = sorted(path for path in worker_root.glob("round_*") if path.is_dir())
    stage_dirs = [path for path in [worker_root / "agent_generated_baseline", *round_dirs] if path.is_dir()]
    diagnostic_summaries: list[dict[str, Any]] = []
    for stage_dir in stage_dirs:
        for attempt_dir, _label in worker_attempt_dirs(stage_dir):
            cycle_payload = read_json_file(attempt_dir / "cycle_result.json")
            diagnostic_summaries.extend(collect_valid_diagnostic_summaries(cycle_payload))
    if not round_dirs and not diagnostic_summaries:
        return {}
    evaluated_rounds: list[dict[str, Any]] = []
    historical_summaries: list[dict[str, Any]] = []
    baseline_cycle = read_json_file(worker_root / "agent_generated_baseline" / "cycle_result.json")
    baseline_summary = (
        baseline_cycle.get("harness") if isinstance(baseline_cycle.get("harness"), dict) else {}
    )
    if baseline_summary:
        historical_summaries.append(baseline_summary)
    for round_dir in round_dirs:
        for attempt_dir, _label in worker_attempt_dirs(round_dir):
            attempt_cycle = read_json_file(attempt_dir / "cycle_result.json")
            attempt_summary = (
                attempt_cycle.get("harness")
                if isinstance(attempt_cycle.get("harness"), dict)
                else {}
            )
            if attempt_summary:
                historical_summaries.append(attempt_summary)
        final_dir = final_worker_attempt_dir(round_dir)
        cycle_result = read_json_file(final_dir / "cycle_result.json")
        summary = cycle_result.get("harness") if isinstance(cycle_result.get("harness"), dict) else {}
        if summary:
            evaluated_rounds.append(summary)
    latest_summary = evaluated_rounds[-1] if evaluated_rounds else {}
    latest_metrics = summary_metrics(latest_summary)
    best_summary = best_progress_summary(historical_summaries)
    best_metrics = summary_metrics(best_summary)
    return {
        "round_count": len(round_dirs),
        "completed_round_count": len(evaluated_rounds),
        "evaluated_round_count": len(evaluated_rounds),
        "best_makespan_so_far": best_metrics.get("makespan"),
        "best_gap_pct_so_far": best_metrics.get("gap_pct"),
        "best_total_so_far": int(best_summary.get("total", 0) or 0),
        "best_valid_so_far": int(best_summary.get("valid", 0) or 0),
        "best_failed_so_far": int(best_summary.get("failed", 0) or 0),
        "latest_makespan": latest_metrics.get("makespan"),
        "latest_gap_pct": latest_metrics.get("gap_pct"),
        "latest_total": int(latest_summary.get("total", 0) or 0),
        "latest_valid": int(latest_summary.get("valid", 0) or 0),
        "latest_failed": int(latest_summary.get("failed", 0) or 0),
        "round_dirs": [str(path.resolve()) for path in round_dirs],
        "in_round_repair": summarize_progress_repair_dirs(round_dirs),
        **summarize_diagnostic_summaries(diagnostic_summaries),
    }


def summarize_progress_repair_dirs(round_dirs: list[Path]) -> dict[str, Any]:
    repair_round_count = 0
    repair_attempt_count = 0
    recovered_round_count = 0
    final_rejected_after_repair = 0
    for round_dir in round_dirs:
        repair_dirs = [path for path in round_dir.glob("repair_*") if path.is_dir()]
        if not repair_dirs:
            continue
        repair_round_count += 1
        repair_attempt_count += len(repair_dirs)
        final_dir = final_worker_attempt_dir(round_dir)
        cycle_result = read_json_file(final_dir / "cycle_result.json")
        judgment = cycle_result.get("agentic_judgment") if isinstance(cycle_result.get("agentic_judgment"), dict) else {}
        summary = cycle_result.get("harness") if isinstance(cycle_result.get("harness"), dict) else {}
        total = int(summary.get("total", 0) or 0)
        valid = int(summary.get("valid", 0) or 0)
        if total > 0 and valid == total:
            recovered_round_count += 1
        elif total == 0 or valid != total:
            final_rejected_after_repair += 1
    return {
        "repair_round_count": repair_round_count,
        "repair_attempt_count": repair_attempt_count,
        "recovered_round_count": recovered_round_count,
        "final_rejected_after_repair": final_rejected_after_repair,
    }


def best_progress_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    best_summary: dict[str, Any] = {}
    best_makespan: float | None = None
    for summary in summaries:
        if int(summary.get("valid", 0) or 0) <= 0:
            continue
        metrics = summary_metrics(summary)
        makespan = first_number(metrics.get("makespan"))
        if makespan is None:
            continue
        if best_makespan is None or makespan < best_makespan:
            best_makespan = makespan
            best_summary = summary
    return best_summary


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def summarize_exception(path: Path) -> str:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return "无法读取异常详情"
    if not lines:
        return "异常文件为空"
    for line in reversed(lines):
        if "Error" in line or "Exception" in line:
            return line[:240]
    return lines[-1][:240]


def summarize_list(value: Any, *, limit: int = 2, max_chars: int = 220) -> str:
    if not isinstance(value, list) or not value:
        return "无详情"
    text = "；".join(str(item) for item in value[:limit])
    if len(value) > limit:
        text += f"；另有 {len(value) - limit} 项"
    return text[:max_chars]


def format_progress_value(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:.2f}"


class AlgoForgeWebHandler(BaseHTTPRequestHandler):
    """无框架本地 HTTP 路由；业务逻辑委托给上面的纯函数。

    GET 提供静态页面、任务历史、洞察和产物；POST `/api/jobs` 创建任务，
    POST `/api/jobs/<id>/continue` 提交轮间人工方向。长任务由后台线程执行。
    """

    server_version = "AlgoForgeWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(200, service_health_payload())
            return
        if parsed.path == "/":
            self._serve_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/api/examples":
            self._json(200, make_demo_examples())
            return
        if parsed.path == "/api/deepseek-status":
            self._json(200, deepseek_status_payload())
            return
        if parsed.path == "/api/resources":
            self._json(200, resource_catalog())
            return
        if parsed.path == "/api/resources/content":
            resource_id = parse_qs(parsed.query).get("id", [""])[0]
            try:
                self._json(200, read_resource(resource_id))
            except (OSError, ValueError) as exc:
                self._json(404, {"error": str(exc)})
            return
        if parsed.path == "/api/jobs":
            with _LOCK:
                jobs = [public_job(job) for job in sorted(_JOBS.values(), key=lambda item: item["created_at"], reverse=True)]
            self._json(200, {"jobs": jobs})
            return
        if parsed.path.startswith("/api/jobs/"):
            parts = [item for item in parsed.path.split("/") if item]
            if len(parts) >= 3:
                job_id = parts[2]
                with _LOCK:
                    job = _JOBS.get(job_id)
                if not job:
                    self._json(404, {"error": "job not found"})
                    return
                if len(parts) == 3:
                    self._json(200, public_job(job))
                    return
                if len(parts) == 4 and parts[3] == "insights":
                    self._json(200, job_insights(job))
                    return
                if len(parts) == 4 and parts[3] == "artifact":
                    name = parse_qs(parsed.query).get("name", ["demo_report"])[0]
                    try:
                        self._json(200, read_artifact(job, name))
                    except Exception as exc:  # noqa: BLE001
                        self._json(404, {"error": str(exc)})
                    return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/resources":
                self._json(201, create_project_resource(self._read_json()))
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/resume"):
                parts = [item for item in parsed.path.split("/") if item]
                if len(parts) != 4:
                    self._json(404, {"error": "not found"})
                    return
                self._json(202, resume_job(parts[2], self._read_json()))
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/continue"):
                parts = [item for item in parsed.path.split("/") if item]
                if len(parts) != 4:
                    self._json(404, {"error": "not found"})
                    return
                result = submit_round_intervention(parts[2], self._read_json())
                self._json(202, result)
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stop"):
                parts = [item for item in parsed.path.split("/") if item]
                if len(parts) != 4:
                    self._json(404, {"error": "not found"})
                    return
                self._json(202, stop_job(parts[2]))
                return
            if parsed.path != "/api/jobs":
                self._json(404, {"error": "not found"})
                return
            payload = self._read_json()
            job = create_job(payload, output_root=_ACTIVE_OUTPUT_ROOT)
            start_job(job["id"])
            self._json(202, public_job(job))
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        print(f"[web] {self.address_string()} - {format % args}")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BYTES:
            raise ValueError(f"request is too large: {length} bytes")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _serve_static(self, relative_path: str) -> None:
        safe_name = relative_path.replace("\\", "/").strip("/") or "index.html"
        if ".." in safe_name.split("/"):
            self._json(400, {"error": "invalid static path"})
            return
        path = STATIC_DIR / safe_name
        if not path.exists() or not path.is_file():
            self._json(404, {"error": "static file not found"})
            return
        content_type = "text/plain; charset=utf-8"
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(browser_safe_json(payload), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_web_server(host: str = "127.0.0.1", port: int = 7860, *, output_root: Path = DEFAULT_OUTPUT_ROOT) -> None:
    """恢复历史任务并启动多线程本地 HTTP 服务。"""

    global _ACTIVE_OUTPUT_ROOT
    _ACTIVE_OUTPUT_ROOT = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    load_persisted_jobs(output_root)
    server = ThreadingHTTPServer((host, port), AlgoForgeWebHandler)
    print(f"[web] AlgoForge demo UI: http://{host}:{port}")
    print(f"[web] Output root: {output_root.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] stopped")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local AlgoForge web demo UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_web_server(args.host, args.port, output_root=args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
