"""本地 Web 服务：把浏览器操作映射到同一套 Agent/Core 闭环。

Web 层只负责输入落盘、后台任务生命周期、状态轮询和产物摘要。它不选择
具体算法，也不参与 promotion/rollback。一次任务的正式判断仍由
`orchestration.standard -> orchestration.loop -> core` 完成。

数据目录约定：`outputs/web_runs/<job_id>/inputs` 保存用户输入，`run` 保存
不可变实验产物，`web_job_status.json` 是浏览器和重启恢复共同读取的快照。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness_agent.deepseek_client import is_deepseek_configured, load_local_env, local_env_candidates, normalize_deepseek_model
from harness_agent.orchestration.loop import DEFAULT_IN_ROUND_REPAIR_ATTEMPTS
from harness_agent.context.knowledge import method_package_catalog
from harness_agent.agents.main import DeepSeekMainAgent, EvidenceDrivenMainAgent
from harness_agent.agents.semantic import DeepSeekAlgorithmSemanticReviewer
from harness_agent.domains.io import parse_standard_fjsp
from harness_agent.orchestration.standard import StandardWorkerLoopRequest, run_standard_worker_loop
from harness_agent.workers.opencode_worker import OpenCodeWorker


# 路径与显示上限集中在这里，避免 HTTP handler 和任务线程各自推导目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "web_runs"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_CHARS = 240_000
DEFAULT_STANDARD_SEEDS_TEXT = "0,1,2,3,4,5,6,7,8,9"
DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads"
DEFAULT_SDST_HUDATA_LA20_INSTANCE = (
    DEFAULT_DOWNLOADS_DIR
    / "FJSP_SDST_HUdata_instances_package"
    / "FJSP_SDST_HUdata_instances_package"
    / "instances"
    / "oddla20.txt"
)
DEFAULT_SDST_HUDATA_BOUNDS_CANDIDATES = [
    DEFAULT_DOWNLOADS_DIR
    / "FJSP_SDST_HUdata_instances_package"
    / "FJSP_SDST_HUdata_instances_package"
    / "bounds"
    / "SDST_HUdata_bounds_LB_UB.csv",
    DEFAULT_DOWNLOADS_DIR / "FJSP_SDST_benchmark_bounds_package" / "SDST_HUdata_bounds_LB_UB.csv",
]
DEFAULT_SDST_LA20_BOUNDS_CSV = (
    "Instance,Lower bound (LB),Best-known upper bound (UB/BKS),Note\n"
    "la20,857,997,SDST-HUdata LA20 fallback row\n"
)

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_ACTIVE_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT


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


def append_event(job: dict[str, Any], message: str, *, level: str = "info") -> None:
    """向前端事件流追加一条面向用户的阶段消息。"""

    job.setdefault("events", []).append(
        {
            "time": utc_timestamp(),
            "level": level,
            "message": message,
        }
    )


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

    if str(payload.get("status") or "") not in {"queued", "running"}:
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
    """返回可直接运行的 SDST 示例；配置只包含平台资源预算。"""

    requirement_name = "fjsp_sdst_fattahi_requirement.md"
    io_name = "fjsp_sdst_fattahi_io.md"
    requirement = (PROJECT_ROOT / "examples" / requirement_name).read_text(encoding="utf-8")
    io_doc = (PROJECT_ROOT / "examples" / io_name).read_text(encoding="utf-8")
    instance_name, instance = read_default_sdst_la20_instance()
    best_known_name, best_known = read_default_sdst_bounds_csv()
    return {
        "requirement": {"name": requirement_name, "text": requirement},
        "io": {"name": io_name, "text": io_doc},
        "instance": {"name": instance_name, "text": instance},
        "best_known_csv": {"name": best_known_name, "text": best_known},
        "config": {
            "title": "SDST-HUdata LA20（oddla20）Agent 自写闭环测试",
            "max_rounds": 10,
            "seeds": DEFAULT_STANDARD_SEEDS_TEXT,
            "timeout_seconds": 60,
            "max_workers": 2,
            "worker_max_steps": 4,
            "worker_max_runtime_seconds": 120,
            "in_round_repair_attempts": DEFAULT_IN_ROUND_REPAIR_ATTEMPTS,
            "promotion_repeats": 1,
        },
    }


def read_default_sdst_la20_instance() -> tuple[str, str]:
    if DEFAULT_SDST_HUDATA_LA20_INSTANCE.is_file():
        return DEFAULT_SDST_HUDATA_LA20_INSTANCE.name, DEFAULT_SDST_HUDATA_LA20_INSTANCE.read_text(encoding="utf-8")
    fallback = PROJECT_ROOT / "examples" / "fjsp_sdst_hudata_tiny.txt"
    return fallback.name, fallback.read_text(encoding="utf-8")


def read_default_sdst_bounds_csv() -> tuple[str, str]:
    for path in DEFAULT_SDST_HUDATA_BOUNDS_CANDIDATES:
        if path.is_file():
            return path.name, path.read_text(encoding="utf-8-sig")
    return "SDST_HUdata_bounds_LB_UB_la20_fallback.csv", DEFAULT_SDST_LA20_BOUNDS_CSV


def deepseek_status_payload() -> dict[str, Any]:
    """Return non-secret DeepSeek runtime status for the local UI."""

    load_local_env()
    api_key_present = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    key_file_value = os.environ.get("DEEPSEEK_API_KEY_FILE", "").strip()
    key_file_status = inspect_secret_file(key_file_value)
    configured = api_key_present or bool(key_file_status.get("has_content"))
    env_files = [env_file_status(path) for path in local_env_candidates()]
    env_example = PROJECT_ROOT / ".env.example"
    return {
        "configured": configured,
        "model": normalize_deepseek_model(os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
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
                "FJSP_AGENT_ENV_FILE 指向的 env 文件",
                "仓库根目录或当前工作目录下的 .env / .env.local",
            ],
            "examples": [
                "DEEPSEEK_API_KEY=sk-你的本地密钥",
                r"DEEPSEEK_API_KEY_FILE=C:\Users\ASUS\.secrets\deepseek_api_key.txt",
                "DEEPSEEK_MODEL=deepseek-v4-pro",
                "DEEPSEEK_BASE_URL=https://api.deepseek.com",
            ],
            "safe_note": ".env 和 .env.local 已被 .gitignore 忽略；不要把真实密钥写进 .env.example 或提交到 git。",
        },
        "note": "只返回配置诊断，不返回密钥内容。",
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

    # Web 层只接受资源预算，不接受具体算法参数。任何求解方法都必须由
    # Main Agent 从需求/IO/知识库中选择，并由 Coding Agent 实际写出。
    config = {
        "max_rounds": coerce_int(payload.get("max_rounds"), 2, minimum=1, maximum=20),
        "seeds": parse_seeds(payload.get("seeds", "0")),
        "timeout_seconds": coerce_int(payload.get("timeout_seconds"), 60, minimum=5, maximum=3600),
        "max_workers": coerce_int(payload.get("max_workers"), 1, minimum=1, maximum=8),
        "deepseek_model": str(payload.get("deepseek_model") or "deepseek-v4-pro"),
        "coding_backend": "opencode",
        "opencode_executable": str(payload.get("opencode_executable") or os.environ.get("OPENCODE_EXECUTABLE") or "opencode"),
        "opencode_model": str(payload.get("opencode_model") or os.environ.get("OPENCODE_MODEL") or ""),
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
        },
        "events": [],
        "summary": {},
        "artifacts": {},
    }
    append_event(job, "任务材料已保存，等待进入 Agent 自写 solver 闭环。")
    append_instance_profile_events(job)
    append_event(
        job,
        "平台不调用内置求解算法：Main Agent 规划方向，OpenCode Coding Agent 写代码，固定 Core 决定晋升或回滚。",
    )
    write_job_status(job)
    with _LOCK:
        _JOBS[job_id] = job
    return job


def start_job(job_id: str) -> None:
    """用守护线程启动长任务，使 HTTP 请求可以立即返回 202。"""

    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()


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
    return profile


def latest_compatible_experience_memory(job: dict[str, Any]) -> Path | None:
    """召回同变种、同方法包且经过语义验证的最近一次经验。"""

    config = job.get("config") if isinstance(job.get("config"), dict) else {}
    profile = config.get("instance_profile") if isinstance(config.get("instance_profile"), dict) else {}
    expected_format = str(profile.get("format") or "").strip()
    if not expected_format:
        return None
    expected_sdst = bool(profile.get("has_sequence_dependent_setup"))
    expected_features = ["fjsp_sdst", "sequence_dependent_setup", "setup_time"] if expected_sdst else []
    expected_package_id = str(
        method_package_catalog(problem_family="FJSP", active_features=expected_features).get(
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
        if bool(previous_profile.get("has_sequence_dependent_setup")) != expected_sdst:
            continue
        if str(previous_profile.get("format") or "").strip() != expected_format:
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
        validated_package_ids = {
            str(item.get("method_package_id") or "").strip()
            for item in validated
            if str(item.get("method_package_id") or "").strip()
        }
        if not validated or not expected_package_id or expected_package_id not in validated_package_ids:
            continue
        candidates.append((memory_path.stat().st_mtime, memory_path.resolve()))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


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

    这里完成角色装配：OpenCode 是唯一默认 Coding Agent；DeepSeek Main
    Agent 负责方向规划；DeepSeek Semantic Reviewer 负责方法语义复核。
    `run_standard_worker_loop` 返回后，本函数只整理前端摘要，不二次裁决。
    """

    with _LOCK:
        job = _JOBS[job_id]
        job["status"] = "running"
        append_event(job, "开始执行文档到 evaluator 的循环迭代。")
        write_job_status(job)

    try:
        config = job["config"]
        input_paths = job["inputs"]
        output_dir = Path(job["job_dir"]) / "run"
        append_event(
            job,
            (
                f"启动 Agent 自写 solver 闭环：rounds={config['max_rounds']}，"
                f"seeds={config['seeds']}，Core 并行数={config['max_workers']}。"
            ),
        )
        write_job_status(job)
        # OpenCode 是代码编辑运行时，model/provider 通过其配置注入；它和
        # DeepSeek 不是两个并列修改代码的 Agent。
        coding_worker = OpenCodeWorker(
            executable=config["opencode_executable"],
            model=config["opencode_model"] or None,
        )
        if not coding_worker.capabilities().supports_code_generation:
            raise RuntimeError("OpenCode 不可用，请先安装或构建 OpenCode worker。")
        direction_planner = (
            DeepSeekMainAgent(model=config["deepseek_model"])
            if is_deepseek_configured()
            else EvidenceDrivenMainAgent()
        )
        semantic_reviewer = (
            DeepSeekAlgorithmSemanticReviewer(model=config["deepseek_model"])
            if is_deepseek_configured()
            else None
        )
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
                    semantic_reviewer=semantic_reviewer,
                    previous_pipeline_memory=previous_memory_path,
                    max_instances=1,
                    iterations=config["max_rounds"],
                    seeds=config["seeds"],
                    timeout_seconds=config["timeout_seconds"],
                    max_workers=config["max_workers"],
                    max_steps=config["worker_max_steps"],
                    max_runtime_seconds=config["worker_max_runtime_seconds"],
                    in_round_repair_attempts=config["in_round_repair_attempts"],
                    apply_worker_changes=True,
                    promotion_repeats=config["promotion_repeats"],
                    agent_generated_solver_path=config["agent_generated_solver_path"],
                    experiment_id="web_agent_generated_loop",
                    hypothesis=(
                        "Read the requirement, IO documents, instance diagnostics, domain-pack metadata, and "
                        "retrieved knowledge first. Create a runnable solver from those materials; never rely "
                        "on a repository-embedded solver. State the scheduling idea before editing, preserve "
                        "the fixed parser/evaluator contract, and accept claims only after Core measurement."
                    ),
                )
            )
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
            append_event(
                job,
                (
                    f"循环结束，状态：{job['status']}；实际完成 {round_summary['completed_round_count']} 轮；"
                    f"终止原因：{manifest.get('terminal_reason') or '正常结束'}。"
                ),
                level="error" if job["status"] == "failed" else "info",
            )
            write_job_status(job)
    except Exception as exc:  # noqa: BLE001 - web jobs should preserve failures as inspectable artifacts.
        trace_path = Path(job["job_dir"]) / "web_job_exception.txt"
        trace_path.write_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
        with _LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["artifacts"]["exception"] = str(trace_path.resolve())
            append_event(job, f"执行失败：{exc}", level="error")
            write_job_status(job)


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
    return {
        "round_index": round_index,
        "title": str(item.get("title") or f"round_{round_index:03d}"),
        "status": str(item.get("status") or "unknown"),
        "decision": str(item.get("decision") or "unknown"),
        "strategy_type": str(item.get("strategy_type") or "-"),
        "strategy_intent": str(item.get("strategy_intent") or ""),
        "attempt_count": int(item.get("attempt_count", len(attempts)) or 0),
        "score_relation": str(item.get("score_relation") or ""),
        "makespan": metrics.get("makespan"),
        "gap_pct": metrics.get("gap_pct"),
        "valid": int(candidate_summary.get("valid", 0) or 0),
        "total": int(candidate_summary.get("total", 0) or 0),
        "failure_signatures": failures[:4],
        "evidence_used": evidence,
        "semantic_review_status": semantic_review.get("status"),
        "semantic_finding_count": len(semantic_review.get("findings") or []),
    }


def compact_loop_round(item: dict[str, Any]) -> dict[str, Any]:
    candidate_summary = item.get("candidate_summary") if isinstance(item.get("candidate_summary"), dict) else {}
    metrics = summary_metrics(candidate_summary)
    diagnostics = item.get("proposal_diagnostics") if isinstance(item.get("proposal_diagnostics"), dict) else {}
    summary = str(diagnostics.get("summary") or "")
    round_index = json_int(item.get("round_index"), 0)
    semantic_review = item.get("semantic_review") if isinstance(item.get("semantic_review"), dict) else {}
    return {
        "round_index": round_index,
        "title": summary[:80] or f"round_{round_index:03d}",
        "status": "validated_success" if item.get("decision") == "promoted" else "rolled_back",
        "decision": str(item.get("decision") or "unknown"),
        "strategy_type": "-",
        "strategy_intent": str(diagnostics.get("strategy_intent") or summary),
        "attempt_count": 1,
        "score_relation": "",
        "makespan": metrics.get("makespan"),
        "gap_pct": metrics.get("gap_pct"),
        "valid": int(candidate_summary.get("valid", 0) or 0),
        "total": int(candidate_summary.get("total", 0) or 0),
        "failure_signatures": [],
        "semantic_review_status": semantic_review.get("status"),
        "semantic_finding_count": len(semantic_review.get("findings") or []),
        "evidence_used": [],
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
    return {
        "lesson_type": str(item.get("lesson_type") or "-"),
        "strategy": str(item.get("strategy") or "-"),
        "outcome": str(item.get("outcome") or "-"),
        "confidence": str(item.get("confidence") or "-"),
        "recommended_skill_update": str(item.get("recommended_skill_update") or ""),
    }


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
    record_code_evolution_progress_summary(job, worker_root)


def scan_code_attempt_progress(job: dict[str, Any], seen: set[str], attempt_dir: Path, label: str) -> None:
        if (attempt_dir / "context_packet.json").exists():
            record_progress_event(job, seen, f"{label}:context", f"{label} 已生成上下文包，等待 DeepSeek CodingWorker 返回方案。")
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
            if accepted:
                message = f"{label} JA 代码判断通过，进入固定 evaluator。"
                level = "info"
            else:
                message = f"{label} JA 代码判断未通过，已阻止 evaluator：{summarize_list(issues)}"
                level = "error"
            record_progress_event(job, seen, f"{label}:agentic-judgment", message, level=level)
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
                    f"{label} JA 未放行正式 evaluator；诊断 evaluator 已证明当前输出合法："
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
        semantic_review = attempt_dir / "semantic_review" / "algorithm_semantic_review.json"
        if semantic_review.exists():
            review_payload = read_json_file(semantic_review)
            review_status = str(review_payload.get("status") or "unknown")
            findings = review_payload.get("findings") if isinstance(review_payload.get("findings"), list) else []
            record_progress_event(
                job,
                seen,
                f"{label}:semantic-review",
                (
                    f"{label} 算法语义审查={review_status}，"
                    f"证据项={len(findings)}。"
                ),
                level="error" if review_status == "repair_required" else "info",
            )
        patch = attempt_dir / "worker_changes.patch"
        if patch.exists() and patch.stat().st_size > 0:
            record_progress_event(job, seen, f"{label}:patch", f"{label} 产生候选代码 patch，等待提升判定。")


def worker_attempt_dirs(round_dir: Path) -> list[tuple[Path, str]]:
    attempts: list[tuple[Path, str]] = [(round_dir, round_dir.name)]
    for repair_dir in sorted(path for path in round_dir.glob("repair_*") if path.is_dir()):
        suffix = repair_dir.name.replace("repair_", "修补 ")
        attempts.append((repair_dir, f"{round_dir.name} {suffix}"))
    return attempts


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
    for round_dir in round_dirs:
        final_dir = final_worker_attempt_dir(round_dir)
        cycle_result = read_json_file(final_dir / "cycle_result.json")
        summary = cycle_result.get("harness") if isinstance(cycle_result.get("harness"), dict) else {}
        if summary:
            evaluated_rounds.append(summary)
    latest_summary = evaluated_rounds[-1] if evaluated_rounds else {}
    latest_metrics = summary_metrics(latest_summary)
    best_summary = best_progress_summary(evaluated_rounds)
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
        accepted = bool(judgment.get("accepted"))
        total = int(summary.get("total", 0) or 0)
        valid = int(summary.get("valid", 0) or 0)
        if accepted and (total == 0 or valid == total):
            recovered_round_count += 1
        elif total == 0 and not accepted:
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

    GET 提供静态页面、任务历史、洞察和产物；POST `/api/jobs` 是唯一创建
    任务的写入口。长任务由后台线程执行，不阻塞 HTTP 请求线程。
    """

    server_version = "AlgoForgeWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
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
        if parsed.path != "/api/jobs":
            self._json(404, {"error": "not found"})
            return
        try:
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
