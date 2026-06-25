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

from .awls_zi_evolution import AwlsZiEvolutionRequest, run_awls_zi_evolution
from .deepseek_client import is_deepseek_configured, load_local_env, local_env_candidates, normalize_deepseek_model
from .demo import StandardDemoRequest, run_standard_demo
from .slot_contract import ResolvedCodeSlot
from .slot_manifest import default_standard_fjsp_slot_manifest, write_selected_slot_manifest
from .standard_worker_loop import StandardWorkerLoopRequest, run_standard_worker_loop
from .workers.deepseek_slot_worker import DeepSeekSlotWorker
from .workers.deepseek_worker import DeepSeekWorker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "web_static"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "web_runs"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_CHARS = 240_000
DEFAULT_STANDARD_SEEDS_TEXT = "0,1,2,3,4,5,6,7,8,9"

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_ACTIVE_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT


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


def coerce_float(value: Any, default: float, *, minimum: float = 0.1, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def append_event(job: dict[str, Any], message: str, *, level: str = "info") -> None:
    job.setdefault("events", []).append(
        {
            "time": utc_timestamp(),
            "level": level,
            "message": message,
        }
    )


def public_job(job: dict[str, Any]) -> dict[str, Any]:
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


def write_job_status(job: dict[str, Any]) -> None:
    job["updated_at"] = utc_timestamp()
    status_path = Path(job["job_dir"]) / "web_job_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(public_job(job), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_demo_examples() -> dict[str, Any]:
    requirement = (PROJECT_ROOT / "examples" / "web_demo_requirement.md").read_text(encoding="utf-8")
    io_doc = (PROJECT_ROOT / "examples" / "web_demo_io.md").read_text(encoding="utf-8")
    instance_name = "fjsp.brandimarte.Mk01.m6j10c3.txt"
    best_known_name = "brandimarte_mk01_best.csv"
    instance = (PROJECT_ROOT / "examples" / instance_name).read_text(encoding="utf-8")
    best_known = (PROJECT_ROOT / "examples" / best_known_name).read_text(encoding="utf-8")
    return {
        "requirement": {"name": "web_demo_requirement.md", "text": requirement},
        "io": {"name": "web_demo_io.md", "text": io_doc},
        "instance": {"name": instance_name, "text": instance},
        "best_known_csv": {"name": best_known_name, "text": best_known},
        "config": {
            "title": "Brandimarte Mk01 标准 FJSP 闭环演示",
            "run_mode": "standard_loop",
            "max_rounds": 2,
            "seeds": DEFAULT_STANDARD_SEEDS_TEXT,
            "solver": "portfolio",
            "evolution_mode": "strategy",
            "profile_mode": "deepseek",
            "strategy_candidates": 2,
            "portfolio_size": 8,
            "timeout_seconds": 60,
            "max_workers": 1,
            "awls_zi_candidates": 2,
            "awls_same_machine_eval": "stable",
            "apply_worker_changes": True,
            "worker_max_steps": 4,
        },
    }


def slot_manifest_catalog_payload() -> dict[str, Any]:
    payload = default_standard_fjsp_slot_manifest(confirmed=False).to_payload()
    enriched_slots: list[dict[str, Any]] = []
    for slot in payload["slots"]:
        enriched_slots.append(enrich_slot_payload(slot))
    payload["slots"] = enriched_slots
    return payload


def enrich_slot_payload(slot: dict[str, Any]) -> dict[str, Any]:
    target_file = str(slot.get("target_file") or "")
    source_path = PROJECT_ROOT / target_file
    enriched = dict(slot)
    try:
        resolved = ResolvedCodeSlot.from_manifest_slot(slot, source_text=source_path.read_text(encoding="utf-8"))
        block_payload = resolved.to_block_payload()
        enriched.update(
            {
                "line_start": block_payload["line_start"],
                "line_end": block_payload["line_end"],
                "block_name": block_payload["block_name"],
                "context_before": block_payload["context_before"],
                "context_after": block_payload["context_after"],
                "original_content": block_payload["original_content"],
                "source_exists": True,
                "source_error": None,
            }
        )
    except Exception as exc:  # noqa: BLE001 - UI should show broken slot contracts instead of hiding them.
        enriched.update(
            {
                "line_start": None,
                "line_end": None,
                "block_name": "",
                "context_before": "",
                "context_after": "",
                "original_content": "",
                "source_exists": source_path.exists(),
                "source_error": str(exc),
            }
        )
    enriched["advisor"] = slot_advice_payload(enriched)
    return enriched


def slot_advice_payload(slot: dict[str, Any]) -> dict[str, Any]:
    slot_id = str(slot.get("slot_id") or "")
    executable = slot_id == "awls_zi_policy"
    if executable:
        feasibility = "yes"
        feasibility_label = "可执行"
        feasibility_reason = "当前受控 DeepSeekSlotWorker 已接入该 AWLS zi 函数槽，可以在契约内改写并验证。"
    else:
        feasibility = "partial"
        feasibility_label = "待接入"
        feasibility_reason = "该代码槽的边界、输入输出和不变量已经建模，但专用执行 worker 还未接入。"
    significance = "high" if "neighborhood" in slot_id else "medium"
    significance_label = "高" if significance == "high" else "中"
    concerns = list(slot.get("forbidden_edits") or [])[:3]
    suggestions = [
        "只有当输入、输出和不变量都符合本轮目标时，才确认该代码槽。",
        "每次接受代码槽修改后，都必须运行列出的验证命令。",
    ]
    if not executable:
        suggestions.append("在允许自动改写该区域前，需要先接入对应的代码槽 worker。")
    return {
        "block_summary": f"{slot.get('title') or slot_id}：{slot.get('purpose') or ''}",
        "feasibility": feasibility,
        "feasibility_label": feasibility_label,
        "feasibility_reason": feasibility_reason,
        "significance": significance,
        "significance_label": significance_label,
        "significance_reason": str(slot.get("purpose") or ""),
        "worker_support": "available" if executable else "planned",
        "worker_support_label": "已接入" if executable else "待接入",
        "advisor_mode": "本地顾问初筛",
        "rationale": "先让用户确认功能分区和 IO 契约，再把有限代码片段交给 worker 演化。",
        "concerns": concerns,
        "suggestions": suggestions,
    }


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


def create_job(payload: dict[str, Any], *, output_root: Path | None = None) -> dict[str, Any]:
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
    best_known_text = str(best_known.get("text") or "").strip()
    if best_known_text:
        best_known_path.write_text(best_known_text + "\n", encoding="utf-8")
    else:
        best_known_path = None

    run_mode = str(payload.get("run_mode") or "standard_loop")
    if run_mode not in {"standard_loop", "awls_zi"}:
        run_mode = "standard_loop"
    solver = str(payload.get("solver") or "portfolio")
    if solver not in {"portfolio", "local-search", "awls"}:
        solver = "portfolio"
    evolution_mode = str(payload.get("evolution_mode") or "strategy")
    if evolution_mode not in {"strategy", "code", "slot"}:
        evolution_mode = "strategy"
    if evolution_mode == "slot":
        solver = "awls"
    selected_slot_id = str(payload.get("selected_slot_id") or "awls_zi_policy")
    slot_user_confirmed = bool(payload.get("slot_user_confirmed", False))
    if evolution_mode == "slot" and not slot_user_confirmed:
        raise ValueError("slot mode requires explicit user confirmation of the selected code slot")
    if evolution_mode == "slot" and selected_slot_id != "awls_zi_policy":
        raise ValueError("current DeepSeek slot worker supports only selected_slot_id='awls_zi_policy'")
    profile_mode = str(payload.get("profile_mode") or "template")
    if profile_mode not in {"template", "auto", "deepseek"}:
        profile_mode = "template"
    awls_same_machine_eval = str(payload.get("awls_same_machine_eval") or "stable")
    if awls_same_machine_eval not in {"stable", "cpp-fast"}:
        awls_same_machine_eval = "stable"
    awls_time_policy = str(payload.get("awls_time_policy") or "fixed")
    if awls_time_policy not in {"fixed", "scaled"}:
        awls_time_policy = "fixed"

    config = {
        "run_mode": run_mode,
        "max_rounds": coerce_int(payload.get("max_rounds"), 2, minimum=1, maximum=20),
        "seeds": parse_seeds(payload.get("seeds", "0")),
        "solver": solver,
        "evolution_mode": evolution_mode,
        "selected_slot_id": selected_slot_id,
        "slot_user_confirmed": slot_user_confirmed,
        "profile_mode": profile_mode,
        "strategy_candidates": coerce_int(payload.get("strategy_candidates"), 2, minimum=1, maximum=16),
        "portfolio_size": coerce_int(payload.get("portfolio_size"), 16, minimum=1, maximum=512),
        "timeout_seconds": coerce_int(payload.get("timeout_seconds"), 60, minimum=5, maximum=3600),
        "max_workers": coerce_int(payload.get("max_workers"), 1, minimum=1, maximum=8),
        "local_search_iterations": coerce_int(payload.get("local_search_iterations"), 30, minimum=0, maximum=1000),
        "local_search_neighbor_limit": coerce_int(payload.get("local_search_neighbor_limit"), 80, minimum=1, maximum=5000),
        "local_search_time_limit_sec": coerce_float(
            payload.get("local_search_time_limit_sec"), 2.0, minimum=0.1, maximum=120.0
        ),
        "local_search_neighborhood_profile": str(payload.get("local_search_neighborhood_profile") or "random"),
        "awls_restarts": coerce_int(payload.get("awls_restarts"), 2, minimum=1, maximum=128),
        "awls_cycles_per_restart": coerce_int(payload.get("awls_cycles_per_restart"), 1000, minimum=1, maximum=100000),
        "awls_iterations": coerce_int(payload.get("awls_iterations"), 10000, minimum=0, maximum=1000000),
        "awls_time_limit_sec": coerce_float(payload.get("awls_time_limit_sec"), 5.0, minimum=0.1, maximum=1800.0),
        "awls_init": str(payload.get("awls_init") or "random"),
        "awls_exact_select_top_k": coerce_int(payload.get("awls_exact_select_top_k"), 0, minimum=0, maximum=256),
        "awls_beta": coerce_int(payload.get("awls_beta"), 500, minimum=1, maximum=100000),
        "awls_gamma": coerce_int(payload.get("awls_gamma"), 40, minimum=1, maximum=100000),
        "awls_theta": coerce_int(payload.get("awls_theta"), 5, minimum=0, maximum=100000),
        "awls_portfolio_lanes": str(payload.get("awls_portfolio_lanes") or ""),
        "awls_zi_candidates": coerce_int(payload.get("awls_zi_candidates"), 2, minimum=1, maximum=8),
        "awls_same_machine_eval": awls_same_machine_eval,
        "awls_time_policy": awls_time_policy,
        "deepseek_model": str(payload.get("deepseek_model") or "deepseek-v4-pro"),
        "apply_worker_changes": bool(payload.get("apply_worker_changes", True)),
        "worker_max_steps": coerce_int(payload.get("worker_max_steps"), 4, minimum=1, maximum=20),
        "worker_max_runtime_seconds": coerce_int(
            payload.get("worker_max_runtime_seconds"),
            120,
            minimum=10,
            maximum=1800,
        ),
    }
    if config["local_search_neighborhood_profile"] not in {"random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"}:
        config["local_search_neighborhood_profile"] = "random"
    if config["awls_init"] not in {"random", "greedy", "mixed"}:
        config["awls_init"] = "random"

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
    append_event(job, "任务材料已保存，等待进入循环。")
    write_job_status(job)
    with _LOCK:
        _JOBS[job_id] = job
    return job


def start_job(job_id: str) -> None:
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()


def run_job(job_id: str) -> None:
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
                f"调用运行模式={config['run_mode']}，演进层级={config['evolution_mode']}：rounds={config['max_rounds']}，"
                f"solver={config['solver']}。"
            ),
        )
        write_job_status(job)
        if config["run_mode"] == "awls_zi":
            load_local_env()
            if not is_deepseek_configured():
                raise RuntimeError(
                    "DeepSeek API is not configured. Set DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE before AWLS zi evolution."
                )
            append_event(
                job,
                (
                    "进入 AWLS-ZI 受控演化：DeepSeek 只提出候选参数/规则，"
                    "固定 AWLS evaluator 负责验证 gap 与合法性。"
                ),
            )
            write_job_status(job)
            manifest = run_awls_zi_evolution(
                AwlsZiEvolutionRequest(
                    instance_dir=Path(input_paths["instance"]).parent,
                    pattern=Path(input_paths["instance"]).name,
                    best_known_csv=Path(input_paths["best_known_csv"]) if input_paths.get("best_known_csv") else None,
                    output_dir=output_dir / "awls_zi_evolution",
                    rounds=config["max_rounds"],
                    candidates_per_round=config["awls_zi_candidates"],
                    deepseek_model=config["deepseek_model"],
                    seeds=config["seeds"],
                    max_workers=config["max_workers"],
                    restarts=config["awls_restarts"],
                    cycles_per_restart=config["awls_cycles_per_restart"],
                    iterations=config["awls_iterations"],
                    time_limit_sec=config["awls_time_limit_sec"],
                    init_mode=config["awls_init"],
                    exact_select_top_k=config["awls_exact_select_top_k"],
                    beta=config["awls_beta"],
                    gamma=config["awls_gamma"],
                    theta=config["awls_theta"],
                    portfolio_lanes=config["awls_portfolio_lanes"],
                    same_machine_eval=config["awls_same_machine_eval"],
                    time_policy=config["awls_time_policy"],
                )
            )
            round_summary = summarize_zi_manifest(manifest)
            summary_payload = {
                "manifest_status": manifest.get("status"),
                "zi_summary": round_summary,
                "round_summary": {
                    "completed_round_count": round_summary["round_count"],
                    "reflection_count": round_summary["round_count"],
                    "harness_report_count": round_summary["candidate_count"],
                    "round_dirs": round_summary["round_dirs"],
                },
            }
            raw_artifacts = manifest.get("artifacts", {})
            artifacts = {
                "zi_evolution_summary": raw_artifacts.get("summary"),
                "zi_evolution_report": raw_artifacts.get("report"),
            }
            artifacts = {key: value for key, value in artifacts.items() if value}
        elif config["evolution_mode"] in {"code", "slot"}:
            if not is_deepseek_configured():
                raise RuntimeError(
                    "DeepSeek API is not configured. Set DEEPSEEK_API_KEY or DEEPSEEK_API_KEY_FILE before code evolution."
                )
            is_slot_mode = config["evolution_mode"] == "slot"
            worker_loop_root = output_dir / "standard_worker_loop" / "worker_loop"
            progress_stop = threading.Event()
            progress_thread = threading.Thread(
                target=monitor_code_evolution_progress,
                args=(job, worker_loop_root, progress_stop),
                daemon=True,
            )
            progress_thread.start()
            try:
                slot_manifest_path = None
                if is_slot_mode:
                    slot_manifest_path = output_dir / "standard_worker_loop" / "slot_manifest.json"
                    write_selected_slot_manifest(
                        problem_family="standard_fjsp",
                        output=slot_manifest_path,
                        selected_slot_ids=[str(config["selected_slot_id"])],
                    )
                    append_event(
                        job,
                        f"已按用户确认生成代码槽契约：{config['selected_slot_id']}。DeepSeek 只能修改该槽内实现。",
                    )
                    write_job_status(job)
                manifest = run_standard_worker_loop(
                    StandardWorkerLoopRequest(
                        docs=[Path(input_paths["requirement"]), Path(input_paths["io"])],
                        instance_dir=Path(input_paths["instance"]).parent,
                        pattern=Path(input_paths["instance"]).name,
                        best_known_csv=Path(input_paths["best_known_csv"]) if input_paths.get("best_known_csv") else None,
                        output_dir=output_dir / "standard_worker_loop",
                        project_root=PROJECT_ROOT,
                        worker=(
                            DeepSeekSlotWorker(model=config["deepseek_model"])
                            if is_slot_mode
                            else DeepSeekWorker(model=config["deepseek_model"])
                        ),
                        slot_manifest=slot_manifest_path,
                        max_instances=1,
                        iterations=config["max_rounds"],
                        seeds=config["seeds"],
                        timeout_seconds=config["timeout_seconds"],
                        max_workers=config["max_workers"],
                        solver="awls" if is_slot_mode else config["solver"],
                        portfolio_size=config["portfolio_size"],
                        local_search_iterations=config["local_search_iterations"],
                        local_search_neighbor_limit=config["local_search_neighbor_limit"],
                        local_search_time_limit_sec=config["local_search_time_limit_sec"],
                        local_search_neighborhood_profile=config["local_search_neighborhood_profile"],
                        awls_restarts=config["awls_restarts"],
                        awls_cycles_per_restart=config["awls_cycles_per_restart"],
                        awls_iterations=config["awls_iterations"],
                        awls_time_limit_sec=config["awls_time_limit_sec"],
                        awls_init=config["awls_init"],
                        awls_exact_select_top_k=config["awls_exact_select_top_k"],
                        awls_beta=config["awls_beta"],
                        awls_gamma=config["awls_gamma"],
                        awls_theta=config["awls_theta"],
                        awls_zi_policy="slot" if is_slot_mode else "cpp",
                        awls_portfolio_lanes=config["awls_portfolio_lanes"],
                        max_steps=config["worker_max_steps"],
                        max_runtime_seconds=config["worker_max_runtime_seconds"],
                        apply_worker_changes=config["apply_worker_changes"],
                        experiment_id="web_deepseek_slot_loop" if is_slot_mode else "web_deepseek_code_loop",
                        hypothesis=(
                            "Read the requirement and IO documents first. Propose a natural-language AWLS zi policy idea, "
                            "then modify only the EVOLVE-marked zi code slot. Preserve evaluator correctness; do not claim "
                            "success without measured improvement."
                            if is_slot_mode
                            else (
                                "Read the requirement and IO documents first. Propose the rule-level scheduling idea in natural "
                                "language, then edit only allowed solver code. Preserve evaluator correctness; do not claim "
                                "success without measured improvement."
                            )
                        ),
                    )
                )
            finally:
                progress_stop.set()
                progress_thread.join(timeout=2.0)
            round_summary = summarize_worker_manifest(manifest)
            summary_payload = {
                "manifest_status": manifest.get("status"),
                "worker_summary": round_summary,
                "last_summary": manifest.get("baseline_summary", {}),
                "artifact_checks": {},
                "round_summary": {
                    "completed_round_count": round_summary["round_count"],
                    "reflection_count": round_summary["round_count"],
                    "harness_report_count": round_summary["round_count"],
                    "round_dirs": round_summary["round_dirs"],
                },
            }
            artifacts = manifest.get("artifacts", {})
            if is_slot_mode and slot_manifest_path:
                artifacts = {**artifacts, "slot_manifest": str(slot_manifest_path.resolve())}
        else:
            manifest = run_standard_demo(
                StandardDemoRequest(
                    docs=[Path(input_paths["requirement"]), Path(input_paths["io"])],
                    instance_dir=Path(input_paths["instance"]).parent,
                    pattern=Path(input_paths["instance"]).name,
                    best_known_csv=Path(input_paths["best_known_csv"]) if input_paths.get("best_known_csv") else None,
                    output_dir=output_dir,
                    project_root=PROJECT_ROOT,
                    max_instances=1,
                    max_rounds=config["max_rounds"],
                    seeds=config["seeds"],
                    timeout_seconds=config["timeout_seconds"],
                    max_workers=config["max_workers"],
                    solver=config["solver"],
                    portfolio_size=config["portfolio_size"],
                    local_search_iterations=config["local_search_iterations"],
                    local_search_neighbor_limit=config["local_search_neighbor_limit"],
                    local_search_time_limit_sec=config["local_search_time_limit_sec"],
                    local_search_neighborhood_profiles=[config["local_search_neighborhood_profile"]],
                    awls_restarts=config["awls_restarts"],
                    awls_cycles_per_restart=config["awls_cycles_per_restart"],
                    awls_iterations=config["awls_iterations"],
                    awls_time_limit_sec=config["awls_time_limit_sec"],
                    awls_init=config["awls_init"],
                    awls_exact_select_top_k=config["awls_exact_select_top_k"],
                    awls_beta=config["awls_beta"],
                    awls_gamma=config["awls_gamma"],
                    awls_theta=config["awls_theta"],
                    awls_portfolio_lanes=config["awls_portfolio_lanes"],
                    strategy_candidates=config["strategy_candidates"],
                    profile_mode=config["profile_mode"],
                    deepseek_model=config["deepseek_model"],
                )
            )
            round_summary = summarize_round_artifacts(output_dir)
            summary_payload = {
                "manifest_status": manifest.get("status"),
                "benchmark_summary": manifest.get("benchmark_summary", {}),
                "last_summary": (manifest.get("agent_result") or {}).get("last_summary", {}),
                "artifact_checks": manifest.get("artifact_checks", {}),
                "round_summary": round_summary,
            }
            artifacts = manifest.get("artifacts", {})
        with _LOCK:
            job["status"] = "completed" if manifest.get("status") == "ok" else "completed_with_warnings"
            job["summary"] = summary_payload
            job["artifacts"] = artifacts
            append_event(
                job,
                f"循环结束，状态：{job['status']}；实际完成 {round_summary['completed_round_count']} 轮。",
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


def read_artifact(job: dict[str, Any], name: str) -> dict[str, Any]:
    allowed: dict[str, str] = {
        "status": str((Path(job["job_dir"]) / "web_job_status.json").resolve()),
        **{key: str(value) for key, value in (job.get("artifacts") or {}).items() if value},
    }
    if name not in allowed:
        raise KeyError(f"unknown artifact: {name}")
    path = Path(allowed[name])
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > MAX_ARTIFACT_CHARS
    return {
        "name": name,
        "path": str(path.resolve()),
        "text": text[:MAX_ARTIFACT_CHARS],
        "truncated": truncated,
    }


def summarize_round_artifacts(output_dir: Path) -> dict[str, Any]:
    agent_dir = output_dir / "standard_agent"
    round_dirs = sorted(path for path in agent_dir.glob("round_*") if path.is_dir())
    reflection_paths = [path / "reflection.md" for path in round_dirs if (path / "reflection.md").exists()]
    harness_reports = sorted(agent_dir.glob("round_*/candidates/*/harness/report.md"))
    return {
        "completed_round_count": len(round_dirs),
        "reflection_count": len(reflection_paths),
        "harness_report_count": len(harness_reports),
        "round_dirs": [str(path.resolve()) for path in round_dirs],
    }


def summarize_worker_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    baseline_key = list(manifest.get("baseline_key") or [])
    final_key = list(manifest.get("final_key") or [])
    baseline_summary = manifest.get("baseline_summary") or {}
    rounds = manifest.get("rounds") or []
    round_dirs = [str(Path(item.get("cycle_dir", "")).resolve()) for item in manifest.get("rounds", []) if item.get("cycle_dir")]
    decision_counts = status_counts([str(item.get("decision") or "unknown") for item in rounds])
    worker_status_counts = status_counts([str(item.get("worker_status") or "unknown") for item in rounds])
    rejected_before_eval = sum(1 for item in rounds if list(item.get("candidate_key") or []) and all(value == float("-inf") for value in item.get("candidate_key") or []))
    return {
        "round_count": int(manifest.get("round_count", 0) or 0),
        "completed_round_count": int(manifest.get("round_count", 0) or 0),
        "promoted_rounds": int(manifest.get("promoted_rounds", 0) or 0),
        "improved": bool(manifest.get("improved")),
        "baseline_key": baseline_key,
        "final_key": final_key,
        "baseline_makespan": objective_key_to_makespan(baseline_key),
        "final_makespan": objective_key_to_makespan(final_key),
        "baseline_total": int(baseline_summary.get("total", 0) or 0),
        "baseline_valid": int(baseline_summary.get("valid", 0) or 0),
        "baseline_failed": int(baseline_summary.get("failed", 0) or 0),
        "decision_counts": decision_counts,
        "worker_status_counts": worker_status_counts,
        "rejected_before_eval": rejected_before_eval,
        "round_dirs": round_dirs,
    }


def status_counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def summarize_zi_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract the small metric set the web cockpit needs for AWLS-ZI runs."""

    rounds = manifest.get("rounds") or []
    candidates = [
        candidate
        for round_record in rounds
        for candidate in (round_record.get("candidates") or [])
        if isinstance(candidate, dict)
    ]
    best = manifest.get("best") or {}
    baseline = manifest.get("baseline") or {}
    round_dirs = [
        str(Path(candidate.get("report", "")).resolve().parent)
        for candidate in candidates
        if candidate.get("report")
    ]
    return {
        "round_count": len(rounds),
        "completed_round_count": len(rounds),
        "candidate_count": len(candidates),
        "best_name": best.get("name"),
        "best_avg_gap_pct": best.get("avg_gap_pct"),
        "best_median_gap_pct": best.get("median_gap_pct"),
        "best_max_gap_pct": best.get("max_gap_pct"),
        "best_valid_instance_count": best.get("valid_instance_count"),
        "best_invalid_run_count": best.get("invalid_run_count"),
        "baseline_avg_gap_pct": baseline.get("avg_gap_pct"),
        "selected_instance_count": len(manifest.get("selected_instance_names") or []),
        "round_dirs": round_dirs,
    }


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


def monitor_code_evolution_progress(job: dict[str, Any], worker_root: Path, stop_event: threading.Event) -> None:
    """Mirror coding-worker filesystem progress into the web event stream."""

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

    for round_dir in sorted(path for path in worker_root.glob("round_*") if path.is_dir()):
        label = round_dir.name
        if (round_dir / "context_packet.json").exists():
            record_progress_event(job, seen, f"{label}:context", f"{label} 已生成上下文包，等待 DeepSeek CodingWorker 返回方案。")
        raw_response = round_dir / "worker" / "deepseek_code_edit_raw.json"
        if raw_response.exists():
            record_progress_event(job, seen, f"{label}:raw", f"{label} DeepSeek 已返回原始代码修改响应。")
        proposal = round_dir / "worker" / "proposal.md"
        if proposal.exists():
            record_progress_event(job, seen, f"{label}:proposal", f"{label} 已生成结构化代码修改 proposal。")
        judgment = round_dir / "agentic_judgment.json"
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
        error_analysis = round_dir / "agentic_error_analysis.json"
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
        exception = round_dir / "cycle_exception.txt"
        if exception.exists():
            record_progress_event(
                job,
                seen,
                f"{label}:exception",
                f"{label} 执行异常，已作为下一轮反馈：{summarize_exception(exception)}",
                level="error",
            )
        cycle_result = round_dir / "cycle_result.json"
        if cycle_result.exists():
            payload = read_json_file(cycle_result)
            worker = payload.get("worker", {}) if isinstance(payload, dict) else {}
            summary = payload.get("harness", {}) if isinstance(payload, dict) else {}
            best_metrics = summary.get("best_metrics") or summary.get("best_candidate_metrics") or {}
            makespan = best_metrics.get("makespan", best_metrics.get("avg_makespan"))
            record_progress_event(
                job,
                seen,
                f"{label}:cycle-result",
                (
                    f"{label} evaluator 已完成：worker={worker.get('status', 'unknown')}，"
                    f"valid={summary.get('valid', '-')}，makespan={format_progress_value(makespan)}。"
                ),
            )
        patch = round_dir / "worker_changes.patch"
        if patch.exists() and patch.stat().st_size > 0:
            record_progress_event(job, seen, f"{label}:patch", f"{label} 产生候选代码 patch，等待提升判定。")


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


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
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
        if parsed.path == "/api/slot-manifest":
            self._json(200, slot_manifest_catalog_payload())
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
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_web_server(host: str = "127.0.0.1", port: int = 7860, *, output_root: Path = DEFAULT_OUTPUT_ROOT) -> None:
    global _ACTIVE_OUTPUT_ROOT
    _ACTIVE_OUTPUT_ROOT = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
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
