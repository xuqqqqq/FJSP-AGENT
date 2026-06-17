from __future__ import annotations

import argparse
import json
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .demo import StandardDemoRequest, run_standard_demo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "web_static"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "web_runs"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_CHARS = 240_000

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
            "max_rounds": 2,
            "seeds": "0",
            "solver": "portfolio",
            "profile_mode": "template",
            "strategy_candidates": 2,
            "portfolio_size": 8,
            "timeout_seconds": 60,
            "max_workers": 1,
        },
    }


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

    solver = str(payload.get("solver") or "portfolio")
    if solver not in {"portfolio", "local-search"}:
        solver = "portfolio"
    profile_mode = str(payload.get("profile_mode") or "template")
    if profile_mode not in {"template", "auto", "deepseek"}:
        profile_mode = "template"

    config = {
        "max_rounds": coerce_int(payload.get("max_rounds"), 2, minimum=1, maximum=20),
        "seeds": parse_seeds(payload.get("seeds", "0")),
        "solver": solver,
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
        "deepseek_model": str(payload.get("deepseek_model") or "deepseek-v4-pro"),
    }
    if config["local_search_neighborhood_profile"] not in {"random", "critical-block", "combined", "hgtsa-lite", "hybrid"}:
        config["local_search_neighborhood_profile"] = "random"

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
        append_event(job, f"调用 StandardDemo：rounds={config['max_rounds']}，solver={config['solver']}。")
        write_job_status(job)
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
                strategy_candidates=config["strategy_candidates"],
                profile_mode=config["profile_mode"],
                deepseek_model=config["deepseek_model"],
            )
        )
        with _LOCK:
            job["status"] = "completed" if manifest.get("status") == "ok" else "completed_with_warnings"
            job["summary"] = {
                "manifest_status": manifest.get("status"),
                "benchmark_summary": manifest.get("benchmark_summary", {}),
                "last_summary": (manifest.get("agent_result") or {}).get("last_summary", {}),
                "artifact_checks": manifest.get("artifact_checks", {}),
            }
            job["artifacts"] = manifest.get("artifacts", {})
            append_event(job, f"循环结束，状态：{job['status']}。")
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
