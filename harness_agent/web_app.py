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

from .awls_benchmark import AwlsBenchmarkRequest, effective_time_limit_sec, filename_shape
from .awls_zi_evolution import AwlsZiEvolutionRequest, run_awls_zi_evolution
from .deepseek_client import is_deepseek_configured, load_local_env, local_env_candidates, normalize_deepseek_model
from .demo import StandardDemoRequest, run_standard_demo
from .slot_contract import ResolvedCodeSlot
from .slot_manifest import default_standard_fjsp_slot_manifest, write_selected_slot_manifest
from .standard_fjsp import parse_standard_fjsp
from .standard_worker_loop import StandardWorkerLoopRequest, run_standard_worker_loop
from .standard_worker_loop import SDST_ZI_FEATURES_CONSUMER_FORMULA
from .workers.deepseek_slot_worker import DeepSeekSlotWorker
from .workers.deepseek_worker import DeepSeekWorker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "web_static"
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
    job["updated_at"] = utc_timestamp()
    status_path = Path(job["job_dir"]) / "web_job_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(public_job(job), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_persisted_jobs(output_root: Path, *, limit: int = 30) -> None:
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


def make_demo_examples() -> dict[str, Any]:
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
            "run_mode": "standard_loop",
            "baseline_source": "agent_generated",
            "max_rounds": 10,
            "seeds": DEFAULT_STANDARD_SEEDS_TEXT,
            "solver": "agent-generated",
            "evolution_mode": "code",
            "profile_mode": "deepseek",
            "strategy_candidates": 2,
            "portfolio_size": 8,
            "timeout_seconds": 60,
            "max_workers": 1,
            "awls_time_limit_sec": 30,
            "awls_time_policy": "fixed",
            "awls_restarts": 1,
            "awls_cycles_per_restart": 1000,
            "awls_iterations": 1000000,
            "awls_init": "random",
            "awls_beta": 400,
            "awls_gamma": 40,
            "awls_theta": 5,
            "awls_zi_policy": "critical",
            "awls_critical_block_exhaustive_pct": 75,
            "awls_portfolio_lanes": "",
            "awls_zi_candidates": 2,
            "awls_same_machine_eval": "stable",
            "apply_worker_changes": True,
            "worker_max_steps": 4,
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
    executable = bool(slot.get("source_exists", True)) and str(slot.get("language") or "python") == "python"
    if executable:
        feasibility = "yes"
        feasibility_label = "可执行"
        if slot_id == "awls_zi_policy":
            feasibility_reason = "当前受控 DeepSeekSlotWorker 已接入该 AWLS zi 函数槽，可以在契约内改写并验证。"
        else:
            feasibility_reason = "当前受控 DeepSeekSlotWorker 已支持该 Python 标记代码槽，只会替换用户确认的 marker 内代码。"
    else:
        feasibility = "partial"
        feasibility_label = "待接入"
        feasibility_reason = "该代码槽的边界、输入输出和不变量已经建模，但当前 worker 只支持可读取的 Python 标记槽。"
    significance = "high" if "neighborhood" in slot_id else "medium"
    significance_label = "高" if significance == "high" else "中"
    concerns = list(slot.get("forbidden_edits") or [])[:3]
    suggestions = [
        "只有当输入、输出和不变量都符合本轮目标时，才确认该代码槽。",
        "每次接受代码槽修改后，都必须运行列出的验证命令。",
    ]
    if slot_id != "awls_zi_policy":
        suggestions.append("该槽位将走通用 marker-block 替换路径，仍需固定 evaluator 严格验收。")
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


def slot_mode_hypothesis(slot_id: str) -> str:
    if slot_id in {"auto", "agent_auto", "agent_auto_slot"}:
        return (
            "Read the requirement, IO documents, resolved slot catalog, and benchmark feedback first. "
            "Choose the most relevant confirmed code slot, state the rule-level scheduling idea, then edit "
            "only that marker-bounded slot. Preserve the slot IO contract, parser, evaluator, solution schema, "
            "and benchmark semantics."
        )
    if slot_id == "awls_zi_policy":
        return (
            "Read the requirement and IO documents first. Propose a natural-language AWLS zi policy idea, "
            "then modify only the EVOLVE-marked zi code slot. Preserve evaluator correctness; do not claim "
            "success without measured improvement."
        )
    if slot_id == "awls_sdst_zi_features":
        return (
            "Read the requirement, IO documents, slot manifest, and SDST/AWLS knowledge first. Propose the "
            "rule-level setup-aware zi-feature idea, then modify only the awls_sdst_zi_features marker block. "
            "This slot is consumed by the AWLS formula zi policy during Core evaluation, so preserve finite "
            "numeric feature keys and do not change parser, evaluator, or benchmark semantics."
        )
    return (
        f"Read the requirement, IO documents, slot manifest, and SDST/AWLS knowledge first. Propose the rule-level "
        f"scheduling idea for selected code slot {slot_id!r}, then modify only that marker-bounded slot. Preserve "
        "the slot inputs, outputs, invariants, parser, evaluator, and benchmark semantics; do not claim success "
        "without Core evaluator improvement."
    )


AUTO_SLOT_IDS = {"", "auto", "agent_auto", "agent_auto_slot"}
AWLS_ZI_POLICY_CHOICES = {"auto", "cpp", "critical", "formula", "slot"}


def resolve_agent_selected_slot(
    *,
    requested_slot_id: str,
    requirement_text: str,
    io_text: str,
    instance_profile: dict[str, Any],
    solver: str,
) -> tuple[str, dict[str, Any]]:
    requested = requested_slot_id.strip()
    if requested not in AUTO_SLOT_IDS:
        return requested, {
            "mode": "manual",
            "requested_slot_id": requested,
            "selected_slot_id": requested,
            "candidate_slot_ids": [requested],
            "rationale": "用户已指定本轮代码槽；平台只负责锁定该槽的 IO 契约和评测器。",
        }

    corpus = f"{requirement_text}\n{io_text}\n{instance_profile.get('file_name', '')}".lower()
    is_sdst = bool(instance_profile.get("has_sequence_dependent_setup")) or any(
        token in corpus for token in ("sdst", "setup", "sequence-dependent", "换型", "准备时间")
    )
    if is_sdst and solver == "awls":
        if any(token in corpus for token in ("zi", "权重", "score", "评分")):
            candidates = ["awls_sdst_zi_features", "awls_sdst_move_selection", "awls_sdst_tabu_memory"]
            reason = "文档包含 zi/权重/评分信号，优先选择 SDST zi 特征槽。"
        elif any(token in corpus for token in ("init", "初始化", "初始解", "random", "greedy")):
            candidates = ["awls_sdst_initialization", "awls_sdst_move_selection", "awls_sdst_tabu_memory"]
            reason = "文档强调初始化或初始解质量，优先选择 SDST 初始化槽。"
        elif any(token in corpus for token in ("tabu", "禁忌", "memory", "记忆")):
            candidates = ["awls_sdst_tabu_memory", "awls_sdst_move_selection", "awls_sdst_weight_update"]
            reason = "文档强调禁忌或记忆机制，优先选择 SDST tabu memory 槽。"
        elif any(token in corpus for token in ("neighborhood", "邻域", "n7", "nk", "move", "动作", "同机")):
            candidates = ["awls_sdst_move_selection", "awls_sdst_move_evaluation", "awls_sdst_neighborhood_selection"]
            reason = "文档强调邻域/动作/N7/NK，优先选择 SDST move selection 槽。"
        else:
            candidates = ["awls_sdst_move_selection", "awls_sdst_tabu_memory", "awls_sdst_weight_update"]
            reason = "检测到 FJSP-SDST 算例，默认先让 agent 改影响搜索选择的 SDST move selection 槽。"
    elif solver == "local-search":
        candidates = ["local_search_neighborhood_actions"]
        reason = "非 AWLS 局部搜索流程默认选择邻域动作生成槽。"
    else:
        candidates = ["awls_zi_policy"]
        reason = "标准 AWLS 流程默认选择 zi 策略槽。"
    return candidates[0], {
        "mode": "agent_auto",
        "requested_slot_id": requested or "auto",
        "selected_slot_id": candidates[0],
        "candidate_slot_ids": candidates,
        "rationale": reason,
    }


def resolve_awls_zi_policy_for_slot(
    *,
    requested_policy: str,
    selected_slot_id: str,
    instance_profile: dict[str, Any],
) -> str:
    policy = requested_policy if requested_policy in AWLS_ZI_POLICY_CHOICES else "auto"
    if selected_slot_id == "awls_zi_policy":
        return "slot"
    if selected_slot_id == "awls_sdst_zi_features":
        return "formula"
    if policy != "auto":
        return policy
    if bool(instance_profile.get("has_sequence_dependent_setup")) and selected_slot_id.startswith("awls_sdst_"):
        return "critical"
    return "cpp"


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
    instance_profile = inspect_instance_profile(instance_path)
    best_known_text = str(best_known.get("text") or "").strip()
    if best_known_text:
        best_known_path.write_text(best_known_text + "\n", encoding="utf-8")
    else:
        best_known_path = None

    run_mode = str(payload.get("run_mode") or "standard_loop")
    if run_mode not in {"standard_loop", "awls_zi"}:
        run_mode = "standard_loop"
    solver = str(payload.get("solver") or "portfolio")
    if solver not in {"portfolio", "local-search", "awls", "agent-generated"}:
        solver = "portfolio"
    evolution_mode = str(payload.get("evolution_mode") or "strategy")
    if evolution_mode not in {"strategy", "code", "slot"}:
        evolution_mode = "strategy"
    baseline_source = str(payload.get("baseline_source") or "").strip().lower().replace("-", "_")
    if solver == "agent-generated":
        solver = "portfolio"
        baseline_source = "agent_generated"
        evolution_mode = "code"
    if baseline_source not in {"agent_generated", "current_project"}:
        baseline_source = "agent_generated" if evolution_mode == "code" else "current_project"
    if baseline_source == "agent_generated" and evolution_mode != "code":
        raise ValueError("agent-generated baseline currently requires free code layer; slot mode uses current_project incumbent markers")
    if evolution_mode == "slot":
        solver = "awls"
    selected_slot_id, slot_selection = resolve_agent_selected_slot(
        requested_slot_id=str(payload.get("selected_slot_id") or "agent_auto"),
        requirement_text=str(requirement.get("text") or ""),
        io_text=str(io_doc.get("text") or ""),
        instance_profile=instance_profile,
        solver=solver,
    )
    slot_user_confirmed = bool(payload.get("slot_user_confirmed", False))
    if evolution_mode == "slot" and not slot_user_confirmed:
        raise ValueError("slot mode requires explicit user confirmation of the selected code slot")
    profile_mode = str(payload.get("profile_mode") or "template")
    if profile_mode not in {"template", "auto", "deepseek"}:
        profile_mode = "template"
    awls_same_machine_eval = str(payload.get("awls_same_machine_eval") or "stable")
    if awls_same_machine_eval not in {"stable", "cpp-fast"}:
        awls_same_machine_eval = "stable"
    awls_time_policy = str(payload.get("awls_time_policy") or "scaled")
    if awls_time_policy not in {"fixed", "scaled", "mae2019", "mae2019-hour"}:
        awls_time_policy = "scaled"
    requested_awls_zi_policy = str(payload.get("awls_zi_policy") or "auto")
    awls_zi_policy = resolve_awls_zi_policy_for_slot(
        requested_policy=requested_awls_zi_policy,
        selected_slot_id=selected_slot_id,
        instance_profile=instance_profile,
    )
    default_exhaustive_pct = 75 if (
        evolution_mode == "slot"
        and selected_slot_id.startswith("awls_sdst_")
        and bool(instance_profile.get("has_sequence_dependent_setup"))
    ) else 0

    config = {
        "run_mode": run_mode,
        "max_rounds": coerce_int(payload.get("max_rounds"), 2, minimum=1, maximum=20),
        "seeds": parse_seeds(payload.get("seeds", "0")),
        "solver": solver,
        "baseline_source": baseline_source,
        "agent_generated_solver_path": str(payload.get("agent_generated_solver_path") or "examples/agent_generated_fjsp_solver.py"),
        "evolution_mode": evolution_mode,
        "selected_slot_id": selected_slot_id,
        "slot_selection": slot_selection,
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
        "awls_time_limit_sec": coerce_float(payload.get("awls_time_limit_sec"), 30.0, minimum=0.1, maximum=1800.0),
        "awls_init": str(payload.get("awls_init") or "random"),
        "awls_exact_select_top_k": coerce_int(payload.get("awls_exact_select_top_k"), 0, minimum=0, maximum=256),
        "awls_beta": coerce_int(payload.get("awls_beta"), 500, minimum=1, maximum=100000),
        "awls_gamma": coerce_int(payload.get("awls_gamma"), 40, minimum=1, maximum=100000),
        "awls_theta": coerce_int(payload.get("awls_theta"), 5, minimum=0, maximum=100000),
        "awls_zi_policy": awls_zi_policy,
        "requested_awls_zi_policy": requested_awls_zi_policy,
        "awls_critical_block_exhaustive_pct": coerce_int(
            payload.get("awls_critical_block_exhaustive_pct"),
            default_exhaustive_pct,
            minimum=0,
            maximum=100,
        ),
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
    config["promotion_repeats"] = coerce_int(
        payload.get("promotion_repeats"),
        2 if evolution_mode == "slot" else 1,
        minimum=1,
        maximum=5,
    )
    config["instance_profile"] = instance_profile
    config["effective_awls_time_limit_sec"] = effective_awls_time_limit_for_web(config, instance_path)
    config["estimated_awls_zi_eval_sec_per_round"] = estimate_awls_zi_round_seconds(config)
    if config["local_search_neighborhood_profile"] not in {
        "random",
        "critical-block",
        "combined",
        "hgtsa-lite",
        "hybrid",
        "awls-hybrid",
        "setup-guided",
    }:
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
    append_instance_profile_events(job)
    if config["baseline_source"] == "agent_generated":
        append_event(
            job,
            (
                "基线来源：agent_generated。平台会先让 coding worker 根据 IO/需求/知识卡生成初始 solver，"
                "再由 Core evaluator 测这个生成结果作为 baseline。"
            ),
        )
    else:
        append_event(
            job,
            "基线来源：current_project。平台会用当前仓库已有 solver 作为 incumbent；这不是纯 agent 自写 baseline。",
        )
    if config["evolution_mode"] == "slot":
        selection = config.get("slot_selection") or {}
        append_event(
            job,
            (
                "代码槽选择："
                f"mode={selection.get('mode')}，selected={config.get('selected_slot_id')}。"
                f"{selection.get('rationale', '')}"
            ),
        )
    write_job_status(job)
    with _LOCK:
        _JOBS[job_id] = job
    return job


def start_job(job_id: str) -> None:
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()


def inspect_instance_profile(instance_path: Path) -> dict[str, Any]:
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
    shape = filename_shape(instance_path.name)
    mismatch = bool(
        shape
        and (
            shape["job_count"] != parsed.job_count
            or shape["machine_count"] != parsed.machine_count
            or shape["max_candidate_count"] != parsed.max_candidate_count
        )
    )
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
            "filename_shape": shape,
            "filename_shape_mismatch": mismatch,
        }
    )
    return profile


def effective_awls_time_limit_for_web(config: dict[str, Any], instance_path: Path) -> float:
    request = AwlsBenchmarkRequest(
        instance_dir=instance_path.parent,
        pattern=instance_path.name,
        output_dir=Path("_unused_web_time_probe"),
        time_limit_sec=float(config.get("awls_time_limit_sec", 0.0) or 0.0),
        time_policy=str(config.get("awls_time_policy") or "fixed"),
    )
    return effective_time_limit_sec(request, instance_path)


def estimate_awls_zi_round_seconds(config: dict[str, Any]) -> float:
    seeds = config.get("seeds") or [0]
    seed_count = len(seeds) if isinstance(seeds, list) else 1
    candidate_count = int(config.get("awls_zi_candidates", 1) or 1)
    instance_count = 1
    return float(config.get("effective_awls_time_limit_sec", 0.0) or 0.0) * seed_count * candidate_count * instance_count


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
    if profile.get("filename_shape_mismatch"):
        append_event(
            job,
            (
                f"算例文件名形状与实际内容不一致：文件名={profile.get('file_name')}，"
                "后续时间预算按实际内容计算。"
            ),
            level="warning",
        )
    append_event(
        job,
        (
            "AWLS evaluate 预算："
            f"policy={job['config'].get('awls_time_policy')}，"
            f"每个算例/seed/候选={format_progress_value(job['config'].get('effective_awls_time_limit_sec'))}s，"
            f"AWLS-ZI 每轮约 {format_progress_value(job['config'].get('estimated_awls_zi_eval_sec_per_round'))}s。"
        ),
    )


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
                f"solver={config['solver']}，baseline={config.get('baseline_source')}。"
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
                    f" 本次按实际算例预算 {format_progress_value(config.get('effective_awls_time_limit_sec'))}s/seed/候选。"
                ),
            )
            write_job_status(job)
            awls_zi_root = output_dir / "awls_zi_evolution"
            progress_stop = threading.Event()
            progress_thread = threading.Thread(
                target=monitor_awls_zi_progress,
                args=(job, awls_zi_root, progress_stop),
                daemon=True,
            )
            progress_thread.start()
            try:
                manifest = run_awls_zi_evolution(
                    AwlsZiEvolutionRequest(
                        instance_dir=Path(input_paths["instance"]).parent,
                        pattern=Path(input_paths["instance"]).name,
                        best_known_csv=Path(input_paths["best_known_csv"]) if input_paths.get("best_known_csv") else None,
                        output_dir=awls_zi_root,
                        rounds=config["max_rounds"],
                        candidates_per_round=config["awls_zi_candidates"],
                        deepseek_model=config["deepseek_model"],
                        seeds=config["seeds"],
                        max_workers=config["max_workers"],
                        restarts=config["awls_restarts"],
                        cycles_per_restart=config["awls_cycles_per_restart"],
                        iterations=config["awls_iterations"],
                        time_limit_sec=config["effective_awls_time_limit_sec"],
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
            finally:
                progress_stop.set()
                progress_thread.join(timeout=5)
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
            is_zi_slot_mode = is_slot_mode and str(config["selected_slot_id"]) == "awls_zi_policy"
            is_zi_features_slot_mode = is_slot_mode and str(config["selected_slot_id"]) == "awls_sdst_zi_features"
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
                        awls_time_limit_sec=config["effective_awls_time_limit_sec"],
                        awls_init=config["awls_init"],
                        awls_exact_select_top_k=config["awls_exact_select_top_k"],
                        awls_beta=config["awls_beta"],
                        awls_gamma=config["awls_gamma"],
                        awls_theta=config["awls_theta"],
                        awls_zi_policy="slot" if is_zi_slot_mode else ("formula" if is_zi_features_slot_mode else config["awls_zi_policy"]),
                        awls_zi_formula=SDST_ZI_FEATURES_CONSUMER_FORMULA if is_zi_features_slot_mode else "",
                        awls_critical_block_exhaustive_pct=config["awls_critical_block_exhaustive_pct"],
                        awls_same_machine_eval=config["awls_same_machine_eval"],
                        awls_portfolio_lanes=config["awls_portfolio_lanes"],
                        max_steps=config["worker_max_steps"],
                        max_runtime_seconds=config["worker_max_runtime_seconds"],
                        apply_worker_changes=config["apply_worker_changes"],
                        promotion_repeats=config["promotion_repeats"],
                        baseline_source=config["baseline_source"],
                        agent_generated_solver_path=config["agent_generated_solver_path"],
                        experiment_id="web_deepseek_slot_loop" if is_slot_mode else "web_deepseek_code_loop",
                        hypothesis=slot_mode_hypothesis(str(config["selected_slot_id"]))
                        if is_slot_mode
                        else (
                            "Read the requirement, IO documents, instance diagnostics, domain-pack metadata, and knowledge cards first. "
                            "If baseline_source is agent_generated, first create a runnable solver entrypoint at "
                            f"{config['agent_generated_solver_path']} from those materials rather than relying on an incumbent solver. "
                            "Propose the rule-level scheduling idea in natural language, then edit only allowed solver code. "
                            "Preserve evaluator correctness; do not claim success without measured improvement."
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
                    awls_time_limit_sec=config["effective_awls_time_limit_sec"],
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
    final_metrics = summary_metrics(final_summary)
    latest_metrics = summary_metrics(latest_summary)
    return {
        "round_count": int(manifest.get("round_count", 0) or 0),
        "completed_round_count": int(manifest.get("round_count", 0) or 0),
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
        for attempt_dir, label in worker_attempt_dirs(round_dir):
            scan_code_attempt_progress(job, seen, attempt_dir, label)
    record_code_evolution_progress_summary(job, worker_root)


def scan_code_attempt_progress(job: dict[str, Any], seen: set[str], attempt_dir: Path, label: str) -> None:
        if (attempt_dir / "context_packet.json").exists():
            record_progress_event(job, seen, f"{label}:context", f"{label} 已生成上下文包，等待 DeepSeek CodingWorker 返回方案。")
        raw_response = attempt_dir / "worker" / "deepseek_code_edit_raw.json"
        if raw_response.exists():
            record_progress_event(job, seen, f"{label}:raw", f"{label} DeepSeek 已返回原始代码修改响应。")
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
            record_progress_event(
                job,
                seen,
                f"{label}:cycle-result",
                (
                    f"{label} evaluator 已完成：worker={worker.get('status', 'unknown')}，"
                    f"valid={summary.get('valid', '-')}，makespan={format_progress_value(makespan)}。"
                ),
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


def monitor_awls_zi_progress(job: dict[str, Any], evolution_root: Path, stop_event: threading.Event) -> None:
    """Mirror AWLS-ZI filesystem progress into the web event stream."""

    seen: set[str] = set()
    while True:
        scan_awls_zi_progress(job, evolution_root, seen)
        if stop_event.wait(1.5):
            scan_awls_zi_progress(job, evolution_root, seen)
            return


def scan_awls_zi_progress(job: dict[str, Any], evolution_root: Path, seen: set[str]) -> None:
    if not evolution_root.exists():
        return
    record_progress_event(job, seen, "awls-zi-root", "AWLS-ZI 输出目录已创建，正在执行固定 evaluator 基线。")

    baseline_summary = evolution_root / "baseline_cpp" / "summary.json"
    if baseline_summary.exists():
        summary = read_run_summary(baseline_summary)
        record_progress_event(
            job,
            seen,
            "awls-zi-baseline",
            (
                "AWLS-ZI 基线已完成："
                f"valid={format_progress_value(summary.get('valid_instance_count'))}，"
                f"avg_makespan={format_progress_value(summary.get('avg_makespan'))}。"
            ),
        )

    for round_dir in sorted(path for path in evolution_root.glob("round_*") if path.is_dir()):
        label = round_dir.name
        if (round_dir / "deepseek_prompt.md").exists():
            record_progress_event(job, seen, f"{label}:prompt", f"{label} 已生成 DeepSeek 提示词，等待候选参数/规则。")
        if (round_dir / "deepseek_raw_response.json").exists():
            record_progress_event(job, seen, f"{label}:raw", f"{label} DeepSeek 已返回候选参数/规则。")

        normalized = round_dir / "normalized_candidates.json"
        if normalized.exists():
            candidates = read_json_list(normalized)
            names = [str(item.get("name")) for item in candidates if isinstance(item, dict) and item.get("name")]
            suffix = f"：{summarize_list(names, limit=3)}" if names else "。"
            record_progress_event(
                job,
                seen,
                f"{label}:normalized",
                f"{label} 候选已归一化，共 {len(candidates)} 个{suffix}",
            )

        candidates_root = round_dir / "candidates"
        if candidates_root.exists():
            for candidate_dir in sorted(path for path in candidates_root.iterdir() if path.is_dir()):
                summary_path = candidate_dir / "summary.json"
                if not summary_path.exists():
                    continue
                summary = read_run_summary(summary_path)
                invalid_count = summary.get("invalid_run_count")
                record_progress_event(
                    job,
                    seen,
                    f"{label}:candidate:{candidate_dir.name}",
                    (
                        f"{label} 候选评测完成：{candidate_dir.name}，"
                        f"valid={format_progress_value(summary.get('valid_instance_count'))}，"
                        f"avg_makespan={format_progress_value(summary.get('avg_makespan'))}，"
                        f"invalid_runs={format_progress_value(invalid_count)}。"
                    ),
                    level="warning" if invalid_count else "info",
                )

    manifest_path = evolution_root / "zi_evolution_summary.json"
    if manifest_path.exists():
        manifest = read_json_file(manifest_path)
        rounds = manifest.get("rounds") or []
        best = manifest.get("best") or {}
        manifest_key = f"awls-zi-manifest:{len(rounds)}:{best.get('name')}:{best.get('avg_makespan')}"
        record_progress_event(
            job,
            seen,
            manifest_key,
            (
                f"AWLS-ZI 摘要已更新：已完成 {len(rounds)} 轮，"
                f"当前最佳={best.get('name', '-')}，"
                f"avg_makespan={format_progress_value(best.get('avg_makespan'))}。"
            ),
        )

    if (evolution_root / "zi_evolution_report.md").exists():
        record_progress_event(job, seen, "awls-zi-report", "AWLS-ZI 报告已生成，可在产物区查看。")


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
    if not round_dirs:
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


def read_json_list(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def read_run_summary(path: Path) -> dict[str, Any]:
    payload = read_json_file(path)
    aggregate = payload.get("aggregate")
    if isinstance(aggregate, dict):
        return aggregate
    return payload


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
        data = json.dumps(browser_safe_json(payload), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_web_server(host: str = "127.0.0.1", port: int = 7860, *, output_root: Path = DEFAULT_OUTPUT_ROOT) -> None:
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
