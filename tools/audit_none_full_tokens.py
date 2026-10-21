"""Read historical ablation evidence without importing or running the harness.

Only the requested export directory is written. SQLite is opened read-only and
queries select usage metadata, never credentials, prompts, or response bodies.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
          "cache_write_tokens", "total_tokens")
PRUNE = {"node_modules", ".git", ".opencode", "__pycache__", "inputs", "project",
         "baseline_project", "provided_project", "baseline_generation"}
ROLES = ("main", "coding_worker", "semantic_review", "reflection", "subagent", "other")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def usage_stage(path):
    return "baseline_generation" if "agent_generated_baseline" in str(path) else "evolution"


def run_purpose(manifest):
    name = Path(manifest).parent.name
    return "baseline_preparation" if name.startswith("baseline_") or name == "shared_baseline_generation" else "none_full_run"


def number(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def normalized(tokens, api=False):
    if not isinstance(tokens, dict):
        tokens = {}
    if api:
        details = tokens.get("completion_tokens_details") or {}
        prompt_details = tokens.get("prompt_tokens_details") or {}
        return dict(zip(FIELDS, (
            number(tokens.get("prompt_tokens")), number(tokens.get("completion_tokens")),
            number(tokens.get("reasoning_tokens", details.get("reasoning_tokens"))),
            number(tokens.get("prompt_cache_hit_tokens", tokens.get("cached_tokens", prompt_details.get("cached_tokens")))),
            number(tokens.get("cache_creation_input_tokens")), number(tokens.get("total_tokens")),
        )))
    cache = tokens.get("cache") or {}
    return dict(zip(FIELDS, (number(tokens.get("input")), number(tokens.get("output")),
                            number(tokens.get("reasoning")), number(cache.get("read")),
                            number(cache.get("write")), number(tokens.get("total")))))


def total_relation(values, api=False):
    i, o, r, cr, cw, total = (values[k] for k in FIELDS)
    if total is None or i is None or o is None:
        return "unverifiable_missing_fields"
    matches = []
    if total == i + o:
        matches.append("input+output")
    if not api and cr is not None and cw is not None:
        if total == i + o + cr + cw:
            matches.append("input+output+cache")
        if r is not None and r > 0 and total == i + o + cr + cw + r:
            matches.append("input+output+cache+reasoning")
    return "=".join(matches) if matches else "other_provider_total_preserved"


def sums(calls):
    result = {}
    for key in FIELDS:
        values = [c[key] for c in calls if c[key] is not None]
        result[key] = sum(values) if values else None
        result[key + "_missing_calls"] = sum(c[key] is None for c in calls)
    return result


def unique_pair_id(full_manifest, none_manifest):
    value = str(full_manifest).replace("\\", "/") + "\n" + str(none_manifest).replace("\\", "/")
    return "P" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def unique_pair_rows(pairs):
    result = {}
    for pair in pairs:
        result.setdefault(pair["unique_pair_id"], pair)
    return list(result.values())


def role_for(path):
    text = str(path).replace("\\", "/").lower()
    if "reflection" in text:
        return "reflection"
    if "semantic_review" in text:
        return "semantic_review"
    if "/main_agent" in text or "opencode_main" in Path(path).name:
        return "main"
    if "/worker/" in text or Path(path).name.startswith("opencode"):
        return "coding_worker"
    return "other"


class Audit:
    def __init__(self, root, package, output, use_db=True):
        self.root, self.package, self.output, self.use_db = root, package, output, use_db
        self.runs = {}
        self.comparisons = []
        self.pairs = []
        self.calls = {}
        self.evidence = {}
        self.invocations = []
        self.artifact_checks = []
        self.databases = {}
        self.db_audit = []
        self.session_owners = collections.defaultdict(set)
        self.versions = collections.Counter()
        self.scan = collections.Counter()
        self.unmetered = {}
        self.session_links = {}
        self.expected_children = []
        self.session_stages = {}

    def path(self, value):
        p = Path(value)
        return p.resolve() if p.is_absolute() else (self.root / p).resolve()

    def short(self, p):
        try:
            return Path(p).relative_to(self.root).as_posix()
        except ValueError:
            return Path(p).as_posix()

    def read(self, p):
        data = Path(p).read_bytes()
        self.evidence[self.short(p)] = {"path": self.short(p), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()}
        self.scan["json_files"] += 1
        return json.loads(data.decode("utf-8-sig"))

    def gap(self, run_id, kind, evidence):
        item = {"kind": kind, "evidence": self.short(evidence)}
        if item not in self.runs[run_id]["gaps"]:
            self.runs[run_id]["gaps"].append(item)

    def load_inventory(self):
        metadata = {}
        for p in sorted((self.package / "experiments").glob("*/run_summary.csv")):
            self.read_csv_evidence(p)
            for row in read_csv(p):
                if row["guidance_mode"].lower() in {"none", "full"}:
                    metadata[str(self.path(row["source_run"]))] = (p.parent.name, row)
        paths = set(metadata)
        for p in sorted((self.package / "experiments").glob("*/comparisons.csv")):
            self.read_csv_evidence(p)
            for row in read_csv(p):
                source = self.path(row["source_comparison"])
                d = self.read(source)
                cid = f"C{len(self.comparisons) + 1:03d}"
                comp = {"comparison_id": cid, "family": p.parent.name,
                        "source_comparison": self.short(source), "status": d.get("status"),
                        "protocol_valid": bool(d.get("protocol_checks")) and all(x.get("passed") is True for x in d.get("protocol_checks", [])),
                        "full_manifests": [str(self.path(s)) for s in d.get("full_manifests", [])],
                        "none_manifests": [str(self.path(s)) for s in d.get("none_manifests", [])]}
                self.comparisons.append(comp)
                paths.update(comp["full_manifests"] + comp["none_manifests"])
        for index, path in enumerate(sorted(paths), 1):
            p = Path(path)
            family, row = metadata.get(path, ("unknown", {}))
            d = self.read(p) if p.exists() else {}
            req = d.get("request") or {}
            uid = f"U{index:04d}"
            job_id = None
            for parent in [p.parent, *list(p.parents)[:4]]:
                status = parent / "web_job_status.json"
                if status.exists():
                    job_id = self.read(status).get("id")
                    break
            self.runs[uid] = {
                "audit_run_id": uid, "job_id": job_id, "export_run_id": row.get("run_id"),
                "run_purpose": run_purpose(p),
                "family": family, "instance": row.get("instance", req.get("pattern")),
                "mode": req.get("guidance_mode", row.get("guidance_mode")),
                "status": d.get("status"), "terminal_reason": d.get("terminal_reason"),
                "completed_rounds": d.get("round_count", d.get("completed_rounds")), "manifest": self.short(p),
                "main_model": req.get("main_agent_model"), "worker_model": req.get("worker_model"),
                "semantic_model": req.get("semantic_reviewer_model"),
                "main_planning_mode": req.get("main_planning_mode"),
                "comparisons": [], "gaps": [], "notes": [], "duplicate_usage_records": 0,
                "db_recovered_calls": 0, "db_matched_calls": 0,
                "worker_cli_attempts": 0, "provider_retries": 0,
                "resume_launches": 0, "deterministic_reflection_rounds": 0,
                "llm_reflection_event_files": 0, "request": req,
                "worker_status_counts": dict(collections.Counter(x.get("worker_status", x.get("status", "unknown"))
                                                                   for x in d.get("rounds", []))),
                "baseline_generation_scope": "not_generated_inside_this_run",
            }
            if not d:
                self.gap(uid, "manifest_missing", p)
            if req.get("resumed_round_count") or req.get("resume_loop_result"):
                self.gap(uid, "resumed_history_may_live_outside_this_run", p)
            if d.get("baseline_generation"):
                cycle = d["baseline_generation"].get("cycle_dir")
                if cycle and self.path(cycle).is_relative_to(p.parent):
                    self.runs[uid]["baseline_generation_scope"] = "included_internal_generation"
                else:
                    self.runs[uid]["baseline_generation_scope"] = "external_generation_not_in_this_run"
                    self.gap(uid, "baseline_generation_not_in_run_directory", p)
            for stage in d.get("rounds", []):
                status = stage.get("worker_status", stage.get("status"))
                if status in {"worker_exception", "failed_runtime", "authorization_required", "orchestration_error", "interrupted"}:
                    self.gap(uid, "aborted_stage_without_proven_usage_coverage", p)
            if "request" not in d:
                self.runs[uid]["notes"].append("legacy_outer_manifest_models_and_round_statuses_require_nested_evidence")
        lookup = {str(self.path(r["manifest"])): uid for uid, r in self.runs.items()}
        for comp in self.comparisons:
            full, none = comp.pop("full_manifests"), comp.pop("none_manifests")
            comp["full_runs"] = [lookup[s] for s in full]
            comp["none_runs"] = [lookup[s] for s in none]
            for uid in comp["full_runs"] + comp["none_runs"]:
                self.runs[uid]["comparisons"].append(comp["comparison_id"])
            for index, (f, n) in enumerate(zip(comp["full_runs"], comp["none_runs"]), 1):
                self.pairs.append({"pair_id": f"{comp['comparison_id']}-{index:02d}",
                                   "unique_pair_id": unique_pair_id(self.runs[f]["manifest"], self.runs[n]["manifest"]),
                                   **{k: comp[k] for k in ("comparison_id", "family", "status", "protocol_valid", "source_comparison")},
                                   "full_run": f, "none_run": n})
            if len(full) != len(none):
                comp["pairing_gap"] = "unequal_manifest_counts"

    def read_csv_evidence(self, p):
        data = p.read_bytes()
        self.evidence[self.short(p)] = {"path": self.short(p), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()}

    def add_call(self, uid, sid, mid, tokens, source, role, *, api=False, finished=True, model=None, group=False):
        key = f"{uid}|{sid}|{mid}"
        values = normalized(tokens, api)
        if key in self.calls:
            call = self.calls[key]
            conflict = any(call[k] is not None and values[k] is not None and call[k] != values[k] for k in FIELDS)
            if conflict:
                self.gap(uid, "usage_conflict_for_same_message", source.split("#", 1)[0])
                call["conflicting_usage"] = True
            for k in FIELDS:
                if call[k] is None:
                    call[k] = values[k]
            if source not in call["sources"]:
                call["sources"].append(source)
            self.runs[uid]["duplicate_usage_records"] += 1
            return call
        call = {"audit_run_id": uid, "session_id": sid, "message_id": mid, "role": role,
                "model": model, "usage_convention": "openai_compatible_api" if api else "opencode_normalized",
                "aggregate_without_call_ids": group, "finished": finished,
                "stage": usage_stage(source),
                **values, "sources": [source], "total_relation": total_relation(values, api)}
        self.calls[key] = call
        if sid and (uid, sid) in self.session_stages:
            call["stage"] = self.session_stages[(uid, sid)]
        if not finished:
            self.gap(uid, "assistant_message_incomplete", source.split("#", 1)[0])
        if values["total_tokens"] is None:
            self.gap(uid, "provider_total_not_recorded", source.split("#", 1)[0])
        if sid:
            self.session_owners[sid].add(uid)
        return call

    def register_db(self, p, uid, role):
        if p.is_file():
            self.databases.setdefault(str(p.resolve()), set()).add((uid, role))

    def collect_files(self, uid):
        base = self.path(self.runs[uid]["manifest"]).parent
        files = []
        for current, dirs, names in os.walk(base):
            self.scan["directories"] += 1
            here = Path(current)
            if ".opencode_runtime" in dirs:
                self.register_db(here / ".opencode_runtime/data/opencode/opencode.db", uid, role_for(here / "placeholder"))
            if ".algoforge_opencode_session" in dirs:
                session_root = here / ".algoforge_opencode_session"
                for d in session_root.iterdir():
                    if d.is_dir():
                        self.register_db(d / ".opencode_runtime/data/opencode/opencode.db", uid, "coding_worker")
            dirs[:] = sorted(d for d in dirs if d not in PRUNE and d not in {".opencode_runtime", ".algoforge_opencode_session"}
                             and not d.endswith("worktree") and not d.endswith("worktrees"))
            for name in sorted(names):
                if ((name.endswith(".jsonl") and "events" in name and "reasoning" not in name)
                        or name in {"main_agent_usage.json", "algorithm_semantic_review_usage.json", "deepseek_usage.json",
                                    "opencode_session.json", "opencode_provider_retries.json", "round_reflection.json"}
                        or name.startswith("opencode_main_command") and name.endswith(".json")
                        or name in {"opencode.stdout.txt", "competition_result.json"}):
                    files.append(here / name)
        return sorted(files)

    def events(self, uid, p):
        role = role_for(p)
        sessions, started, finished = set(), set(), set()
        errors, lines, malformed, usage_count = 0, 0, 0, 0
        digest = hashlib.sha256()
        with p.open("rb") as stream:
            for index, line in enumerate(stream, 1):
                digest.update(line)
                lines += 1
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    malformed += 1
                    continue
                if not isinstance(event, dict):
                    continue
                part = event.get("part") or {}
                sid = event.get("sessionID") or part.get("sessionID")
                if sid:
                    sessions.add(sid)
                    self.session_owners[sid].add(uid)
                    self.session_stages[(uid, sid)] = usage_stage(p)
                if part.get("tool") == "task":
                    state = part.get("state") or {}
                    metadata = state.get("metadata") or {}
                    child = metadata.get("sessionId") or metadata.get("sessionID") or metadata.get("session_id")
                    if child:
                        self.session_owners[child].add(uid)
                        self.expected_children.append((uid, child, self.short(p)))
                mid = part.get("messageID")
                typ = event.get("type")
                if typ == "step_start" and mid:
                    started.add((sid, mid))
                if typ == "error":
                    errors += 1
                if typ != "step_finish" or not isinstance(part.get("tokens"), dict):
                    continue
                if not mid:
                    self.gap(uid, "usage_without_message_id", p)
                    mid = part.get("id") or f"{self.short(p)}:{index}"
                finished.add((sid, mid))
                self.add_call(uid, sid, mid, part["tokens"], f"{self.short(p)}#L{index}", role)
                usage_count += 1
        self.evidence[self.short(p)] = {"path": self.short(p), "bytes": p.stat().st_size,
                                       "sha256": digest.hexdigest()}
        invocation = {"audit_run_id": uid, "role": role, "event_file": self.short(p),
                      "sessions": sorted(sessions), "usage_records": usage_count,
                      "event_lines": lines, "error_events": errors, "malformed_lines": malformed,
                      "unfinished_message_ids": sorted(mid for _, mid in started - finished)}
        self.invocations.append(invocation)
        if role == "reflection":
            self.runs[uid]["llm_reflection_event_files"] += 1
        self.scan["event_files"] += 1

    def scan_runs(self):
        for index, (uid, run) in enumerate(self.runs.items(), 1):
            if index % 25 == 1:
                print(f"Scanning bounded run {index}/{len(self.runs)}", flush=True)
            files = self.collect_files(uid)
            for p in files:
                if p.suffix == ".jsonl":
                    self.events(uid, p)
                elif p.name == "opencode.stdout.txt" and not (p.parent / "opencode_events.jsonl").exists():
                    self.events(uid, p)
                elif p.name == "opencode_session.json":
                    d = self.read(p)
                    for k in ("observed_session_id", "command_session_id", "requested_session_id"):
                        if d.get(k):
                            self.session_owners[d[k]].add(uid)
                            self.session_stages[(uid, d[k])] = usage_stage(p)
                    run["resume_launches"] += bool(d.get("command_session_id"))
                    if d.get("worker_status") in {"timeout", "failed_runtime", "worker_exception", "authorization_required"}:
                        self.gap(uid, "worker_session_not_completed", p)
                elif p.name == "competition_result.json":
                    d = self.read(p)
                    for candidate in d.get("candidates", []):
                        if candidate.get("worker_status") in {"worker_exception", "failed_runtime", "authorization_required", "interrupted"}:
                            self.gap(uid, "candidate_aborted_without_proven_usage_coverage", p)
                elif p.name == "opencode_provider_retries.json":
                    d = self.read(p)
                    attempts = d.get("attempts") or []
                    run["worker_cli_attempts"] += len(attempts)
                    run["provider_retries"] += d.get("retry_count", 0)
                    for a in attempts:
                        if a.get("observed_session_id"):
                            self.session_owners[a["observed_session_id"]].add(uid)
                        if a.get("timed_out") or a.get("returncode") != 0 or a.get("event_stream_bytes") == 0:
                            self.gap(uid, "worker_attempt_without_proven_complete_usage", p)
                elif p.name == "round_reflection.json":
                    self.read(p)
                    if not list(p.parent.glob("*events*.jsonl")):
                        if run["main_planning_mode"] == "fast":
                            run["deterministic_reflection_rounds"] += 1
                        else:
                            self.gap(uid, "reflection_origin_not_proven", p)
                elif p.name.startswith("opencode_main_command"):
                    self.read(p)
                    suffix = p.stem.removeprefix("opencode_main_command")
                    expected = p.with_name("opencode_main_events" + suffix + ".jsonl")
                    if not expected.exists():
                        self.gap(uid, "command_without_event_file", p)
                elif "usage" in p.name:
                    self.artifact_checks.append({"audit_run_id": uid, "path": self.short(p),
                                                 "role": role_for(p), "data": self.read(p)})
            if not files:
                self.gap(uid, "no_llm_evidence_files", self.path(run["manifest"]))

    def read_db(self, p, owners=None, global_db=False):
        before = len(self.calls)
        info = {"path": self.short(p), "mode": "read_only", "global": global_db,
                "bytes": p.stat().st_size, "wal_bytes": Path(str(p) + "-wal").stat().st_size if Path(str(p) + "-wal").exists() else 0}
        connection = None
        try:
            connection = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            connection.execute("PRAGMA query_only=ON")
            sessions = connection.execute("SELECT id,parent_id,version FROM session").fetchall()
            chosen = {}
            if global_db:
                for sid, parent, version in sessions:
                    if sid in self.session_owners:
                        chosen[sid] = {(uid, self.role_from_session(uid, sid)) for uid in self.session_owners[sid]}
            else:
                chosen = {sid: set(owners) for sid, _, _ in sessions}
            # Include child/subagent and compaction messages even when CLI JSONL
            # only contains the parent task-tool call.
            while True:
                additions = {sid: chosen[parent] for sid, parent, _ in sessions if parent in chosen and sid not in chosen}
                if not additions:
                    break
                chosen.update(additions)
            info["selected_sessions"] = len(chosen)
            for sid, parent, version in sessions:
                if sid not in chosen:
                    continue
                self.session_links[sid] = parent
                for uid, _ in chosen[sid]:
                    if parent and (uid, parent) in self.session_stages:
                        self.session_stages.setdefault((uid, sid), self.session_stages[(uid, parent)])
                self.versions[version] += 1
                for mid, created, raw_tokens, finish, completed, model, error in connection.execute(
                    "SELECT id,time_created,json_extract(data,'$.tokens'),json_extract(data,'$.finish'),"
                    "json_extract(data,'$.time.completed'),json_extract(data,'$.modelID'),"
                    "json_type(data,'$.error') FROM message WHERE session_id=? AND json_extract(data,'$.role')='assistant'",
                    (sid,),
                ):
                    tokens = json.loads(raw_tokens) if raw_tokens else {}
                    source = f"{self.short(p)}#message/{mid}"
                    has_usage = any(number(tokens.get(k)) not in (None, 0) for k in ("total", "input", "output", "reasoning"))
                    has_usage = has_usage or any(number(v) not in (None, 0) for v in (tokens.get("cache") or {}).values())
                    for uid, role in chosen[sid]:
                        if not has_usage:
                            self.gap(uid, "db_assistant_message_without_usage", p)
                            key = f"{uid}|{sid}|{mid}"
                            if key not in self.unmetered:
                                self.unmetered[key] = {"audit_run_id": uid, "session_id": sid, "message_id": mid,
                                                       "role": role, "finished": bool(finish or completed),
                                                       "error_recorded": bool(error), "sources": []}
                            self.unmetered[key]["sources"].append(source)
                            continue
                        key = f"{uid}|{sid}|{mid}"
                        if key in self.calls:
                            self.runs[uid]["db_matched_calls"] += 1
                            if not self.calls[key].get("model"):
                                self.calls[key]["model"] = model
                        else:
                            self.runs[uid]["db_recovered_calls"] += 1
                        self.add_call(uid, sid, mid, tokens, source, role, finished=bool(finish or completed), model=model)
                        if error and not finish:
                            self.gap(uid, "db_error_without_successful_finish", p)
            info["new_usage_calls"] = len(self.calls) - before
        except (sqlite3.Error, ValueError, OSError) as exc:
            info["error"] = type(exc).__name__ + ": " + str(exc)[:160]
            for uid, _ in owners or []:
                self.gap(uid, "cache_db_unreadable", p)
        finally:
            if connection:
                connection.close()
        self.db_audit.append(info)

    def role_from_session(self, uid, sid):
        for c in self.calls.values():
            if c["audit_run_id"] == uid and c["session_id"] == sid:
                return c["role"]
        for invocation in self.invocations:
            if invocation["audit_run_id"] == uid and sid in invocation["sessions"]:
                return invocation["role"]
        return "coding_worker"

    def recover_databases(self):
        if not self.use_db:
            for uid in self.runs:
                self.gap(uid, "cache_database_recovery_disabled", self.path(self.runs[uid]["manifest"]))
            return
        for index, (path, owners) in enumerate(sorted(self.databases.items()), 1):
            if index % 100 == 1:
                print(f"Reading isolated usage DB {index}/{len(self.databases)}", flush=True)
            self.read_db(Path(path), owners)
        global_path = Path.home() / ".local/share/opencode/opencode.db"
        if global_path.exists():
            self.read_db(global_path, global_db=True)

    def finalize_usage_artifacts(self):
        for a in self.artifact_checks:
            uid, p, d = a["audit_run_id"], self.path(a["path"]), a.pop("data")
            nearby = [c for c in self.calls.values() if c["audit_run_id"] == uid
                      and any(s.split("#", 1)[0].startswith(self.short(p.parent) + "/") for s in c["sources"])]
            raw = d.get("usage") if "usage" in d else d.get("total", d)
            api = isinstance(raw, dict) and any(k in raw for k in ("prompt_tokens", "completion_tokens"))
            if p.name == "algorithm_semantic_review_usage.json":
                components = {k: v for k, v in d.items() if k != "total" and isinstance(v, dict)}
                if components and not nearby:
                    for k, v in sorted(components.items()):
                        self.add_call(uid, None, f"{a['path']}:{k}", v, a["path"] + "#" + k, a["role"], api=True)
                    a["handling"] = "primary_and_json_retry_only_aggregate_total_excluded"
                    continue
            if not isinstance(raw, dict):
                a["handling"] = "not_a_usage_mapping"
                continue
            if nearby:
                totals = sums(nearby)
                value = number(raw.get("total_tokens"))
                a["artifact_total_tokens"] = value
                a["deduplicated_nearby_total_tokens"] = totals["total_tokens"]
                a["handling"] = "validation_only_never_added"
                if value is not None and totals["total_tokens"] != value:
                    a["difference"] = (totals["total_tokens"] or 0) - value
                continue
            if any(k in raw for k in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")):
                if api:
                    tokens = raw
                else:
                    tokens = {k: raw.get(k + "_tokens") for k in ("input", "output", "reasoning", "total")}
                    tokens["cache"] = {"read": raw.get("cache_read_tokens"), "write": raw.get("cache_write_tokens")}
                self.add_call(uid, None, a["path"], tokens, a["path"], a["role"], api=api, group=True)
                self.gap(uid, "aggregate_usage_without_individual_call_ids", p)
                a["handling"] = "aggregate_fallback_count_unknown"
            else:
                a["handling"] = "empty_usage_no_zero_imputation"
                self.gap(uid, "empty_usage_artifact", p)

    def complete(self):
        for uid, sid, source in self.expected_children:
            if not any(c["audit_run_id"] == uid and c["session_id"] == sid for c in self.calls.values()):
                self.gap(uid, "task_child_session_usage_not_recovered", self.path(source))
        for call in self.calls.values():
            call["is_subagent_session"] = bool(self.session_links.get(call["session_id"]))
            if call["is_subagent_session"]:
                call["role"] = "subagent"
            # Fields added from a DB may complete the original event record.
            call["total_relation"] = total_relation(call, call["usage_convention"] == "openai_compatible_api")
        for invocation in self.invocations:
            uid = invocation["audit_run_id"]
            db_prefix = self.short(self.path(invocation["event_file"]).parent) + "/.opencode_runtime/"
            known = [c for c in self.calls.values() if c["audit_run_id"] == uid
                     and (c["session_id"] in invocation["sessions"]
                          or any(s.startswith(db_prefix) for s in c["sources"]))]
            mids = {c["message_id"] for c in known if c["finished"]}
            if any(mid not in mids for mid in invocation["unfinished_message_ids"]):
                self.gap(uid, "step_start_without_metered_finish", self.path(invocation["event_file"]))
            if invocation["error_events"]:
                self.gap(uid, "provider_error_may_have_unreported_usage", self.path(invocation["event_file"]))
            if invocation["malformed_lines"]:
                self.gap(uid, "malformed_event_lines", self.path(invocation["event_file"]))
            if not invocation["usage_records"] and not known:
                self.gap(uid, "empty_or_unmetered_invocation", self.path(invocation["event_file"]))
        for uid, run in self.runs.items():
            calls = [c for c in self.calls.values() if c["audit_run_id"] == uid]
            run["llm_call_count_observed"] = sum(not c["aggregate_without_call_ids"] for c in calls)
            run["aggregate_usage_groups"] = sum(c["aggregate_without_call_ids"] for c in calls)
            run["unmetered_assistant_messages"] = sum(c["audit_run_id"] == uid for c in self.unmetered.values())
            run["subagent_calls"] = sum(c["is_subagent_session"] for c in calls)
            run["observed"] = sums(calls)
            complete_totals = bool(calls) and all(c["total_tokens"] is not None for c in calls)
            run["recorded_calls_exact_total_tokens"] = run["observed"]["total_tokens"] if complete_totals else None
            run["exact_job_total_tokens"] = run["observed"]["total_tokens"] if complete_totals and not run["gaps"] else None
            run["completeness"] = "complete_recorded_scope" if run["exact_job_total_tokens"] is not None else "partial_usage_only"
            run["job_id_completeness"] = "historical_web_job_id" if run["job_id"] else "not_retained_in_export_or_run_ancestors"
            run["role_usage"] = {role: {"calls": sum(c["role"] == role and not c["aggregate_without_call_ids"] for c in calls),
                                         **sums([c for c in calls if c["role"] == role])} for role in ROLES}
            run["stage_usage"] = {stage: {"calls": sum(c["stage"] == stage for c in calls),
                                           **sums([c for c in calls if c["stage"] == stage])}
                                  for stage in ("baseline_generation", "evolution")}
            del run["request"]
        for pair in self.pairs:
            f, n = (self.runs[pair[k]] for k in ("full_run", "none_run"))
            pair.update({"instance": f["instance"], "full_status": f["status"], "none_status": n["status"],
                         "full_recorded_total_tokens": f["observed"]["total_tokens"],
                         "none_recorded_total_tokens": n["observed"]["total_tokens"],
                         "full_exact_job_total_tokens": f["exact_job_total_tokens"],
                         "none_exact_job_total_tokens": n["exact_job_total_tokens"],
                         "exact_token_comparison": pair["status"] == "comparable" and pair["protocol_valid"]
                         and f["exact_job_total_tokens"] is not None and n["exact_job_total_tokens"] is not None})

    def csv(self, name, rows):
        if not rows:
            rows = [{"status": "no_rows"}]
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (self.output / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
                                 for k, v in row.items()})

    def write(self):
        self.output.mkdir(parents=True, exist_ok=True)
        runs = list(self.runs.values())
        unique_pairs = []
        for row in unique_pair_rows(self.pairs):
            aliases = [p for p in self.pairs if p["unique_pair_id"] == row["unique_pair_id"]]
            selected = next((p for p in aliases if p["exact_token_comparison"]), row)
            item = {**selected, "source_pair_ids": [p["pair_id"] for p in aliases], "source_report_count": len(aliases)}
            if selected["exact_token_comparison"]:
                item["full_minus_none_tokens"] = selected["full_exact_job_total_tokens"] - selected["none_exact_job_total_tokens"]
            else:
                item["full_minus_none_tokens"] = None
            unique_pairs.append(item)
        summary = {"runs": len(runs), "paired_unique_runs": sum(bool(r["comparisons"]) for r in runs),
                   "comparison_reports": len(self.comparisons), "comparison_pair_rows": len(self.pairs),
                   "unique_pairs": len({(p["full_run"], p["none_run"]) for p in self.pairs}),
                   "exact_job_runs": sum(r["exact_job_total_tokens"] is not None for r in runs),
                   "exact_run_purposes": dict(collections.Counter(r["run_purpose"] for r in runs if r["exact_job_total_tokens"] is not None)),
                   "exact_pair_rows": sum(p["exact_token_comparison"] for p in self.pairs),
                   "exact_unique_pairs": len({(p["full_run"], p["none_run"]) for p in self.pairs if p["exact_token_comparison"]}),
                   "usage_calls": sum(r["llm_call_count_observed"] for r in runs),
                   "db_recovered_calls": sum(r["db_recovered_calls"] for r in runs),
                   "duplicate_usage_records": sum(r["duplicate_usage_records"] for r in runs),
                   "db_count": len(self.db_audit), "scan": dict(self.scan),
                   "session_versions": dict(self.versions),
                   "unmetered_assistant_messages": len(self.unmetered),
                   "role_calls": dict(collections.Counter(c["role"] for c in self.calls.values())),
                   "subagent_calls": sum(c["is_subagent_session"] for c in self.calls.values()),
                   "calls_with_nonzero_reasoning": sum((c["reasoning_tokens"] or 0) > 0 for c in self.calls.values()),
                   "calls_with_nonzero_cache_read": sum((c["cache_read_tokens"] or 0) > 0 for c in self.calls.values()),
                   "total_relations": dict(collections.Counter(c["total_relation"] for c in self.calls.values())),
                   "gaps_by_kind": dict(collections.Counter(g["kind"] for r in runs for g in r["gaps"]))}
        aggregates = []
        for family in sorted({r["family"] for r in runs}):
            for purpose, mode in sorted({(r["run_purpose"], r["mode"]) for r in runs if r["family"] == family}):
                group = [r for r in runs if r["family"] == family and r["mode"] == mode and r["run_purpose"] == purpose]
                if not group:
                    continue
                known = [r["observed"]["total_tokens"] for r in group if r["observed"]["total_tokens"] is not None]
                aggregates.append({"family": family, "mode": mode, "unique_runs": len(group),
                                   "run_purpose": purpose,
                                   "paired_runs": sum(bool(r["comparisons"]) for r in group),
                                   "exact_runs": sum(r["exact_job_total_tokens"] is not None for r in group),
                                   "observed_calls": sum(r["llm_call_count_observed"] for r in group),
                                   "recorded_total_tokens_subtotal": sum(known) if known else None,
                                   "exact_all_runs_total_tokens": sum(known) if known and all(r["exact_job_total_tokens"] is not None for r in group) else None})
        flat = []
        for r in runs:
            flat.append({**{k: v for k, v in r.items() if k not in {"observed", "role_usage", "stage_usage", "gaps", "notes"}},
                         **{("recorded_" + k): v for k, v in r["observed"].items()},
                         **{f"{role}_calls": r["role_usage"][role]["calls"] for role in ROLES},
                         **{f"{role}_recorded_total_tokens": r["role_usage"][role]["total_tokens"] for role in ROLES},
                         "gap_count": len(r["gaps"]), "gaps": r["gaps"]})
        self.csv("runs.csv", flat)
        self.csv("pairs.csv", self.pairs)
        self.csv("unique_pairs.csv", unique_pairs)
        self.csv("family_mode_totals.csv", aggregates)
        self.csv("calls.csv", list(self.calls.values()))
        self.csv("invocations.csv", self.invocations)
        self.csv("usage_artifact_checks.csv", self.artifact_checks)
        self.csv("unmetered_messages.csv", list(self.unmetered.values()))
        payload = {"schema_version": 1, "audit_date": "2026-10-21", "root": self.root.as_posix(),
                   "package": self.short(self.package), "summary": summary, "runs": runs,
                   "comparisons": self.comparisons, "pairs": self.pairs, "unique_pairs": unique_pairs, "aggregates": aggregates,
                   "databases": self.db_audit, "evidence_files": list(self.evidence.values()),
                   "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        (self.output / "audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.write_report(summary, aggregates)
        print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)

    def write_report(self, summary, aggregates):
        # Chinese report text is kept here so regeneration produces the same
        # evidence-backed explanation as the CSV and JSON, without manual edits.
        exact = unique_pair_rows([p for p in self.pairs if p["exact_token_comparison"]])
        exact.sort(key=lambda p: p["pair_id"])
        lines = ["# 历史 none/full Token 核查（2026-10-21）", "",
                 f"从旧 v2 包的 13 类实验清单定位 {summary['comparison_reports']} 份比较报告、"
                 f"{summary['paired_unique_runs']} 个配对运行；并补充清单中的未配对运行，共 {summary['runs']} 个。",
                 f"精确恢复完整已记录范围的运行有 {summary['exact_job_runs']} 个；"
                 f"满足原协议且双方总量完整的不同运行对有 {summary['exact_unique_pairs']} 对"
                 f"（pairs.csv 中共 {summary['exact_pair_rows']} 行，聚合报告会重复引用同一运行）。",
                 f"完整运行包括 {summary['exact_run_purposes'].get('none_full_run', 0)} 个 none/full 运行"
                 f"及 {summary['exact_run_purposes'].get('baseline_preparation', 0)} 个单独的 baseline 准备运行。"
                 "首轮 31 个为复核前口径；最终已计入证据完整的内部生成阶段，并剔除阶段异常误报。",
                 "", "## 口径", "",
                 "- total 直接采用 OpenCode tokens.total 或 API total_tokens；不从分项推算缺失 total。",
                 "- OpenCode 的 input 是其归一化输入字段，缓存另列。多数实测 total 已覆盖 cache，不能再次相加。"
                 "reasoning 是否已含在 total/output 中按 calls.csv 的 total_relation 核对；原值始终保留。",
                 "- API prompt_tokens 通常包含缓存，completion_tokens 可能包含 reasoning。API 与 OpenCode 的 input 分项语义不同，"
                 "不可把所有分项直接加成总量；缺失分项留空。仅数值恒等关系不能证明 provider 未返回的明细语义。",
                 "- 同一运行内按 session_id + message_id 去重，事件镜像、DB message 与 usage 汇总不重复相加。"
                 "同一消息的字段冲突会阻止完整性认定。不同消息的 retry、repair、resume 新调用照计。",
                 "- calls.csv 的调用数是有 usage 的 assistant 消息数；CLI 尝试次数另列。"
                 "只有聚合 usage 而无逐次 ID 时，保留该小计，调用数不猜测。语义审查 primary_review/json_retry 与 total 不重复计。",
                 "- recorded_total_tokens 是已留存 total 的小计，不是缺失值为零；"
                 "recorded_calls_exact_total_tokens 表示所有已恢复 usage 记录均有 total；"
                 "exact_job_total_tokens 还要求没有已识别覆盖缺口。",
                 "- Main、全部候选 coding Workers（包含未晋升者）、语义审查、模型反思、子会话及压缩消息均在扫描范围。"
                 "子会话调用单列为 subagent，不重复并入父角色。Main Fast 且无模型反思事件的轮次按确定性复盘记录，不能把复盘 JSON 数当模型调用数。",
                 "- 共同 baseline 生成与正式演进是不同阶段；本表按 none/full 原始 manifest 所属运行计，"
                 "不把外部共享 baseline 重复分摊。运行目录内部的 baseline 生成照计，stage_usage 分别列出；"
                 "跨目录 resume 历史或外部生成阶段显式标记。",
                 "- job_id 只抄历史 web_job_status.id；原 CLI 运行未保留该 ID 时留空，"
                 "用 audit_run_id、export_run_id 和 manifest 路径定位，不虚构 job id。",
                 "", "## 证据与恢复", "",
                 f"读取 {summary['scan'].get('event_files', 0)} 个定向事件文件、{summary['db_count']} 个只读数据库，"
                 f"恢复 {summary['usage_calls']} 个带 usage 的消息；DB 补回 {summary['db_recovered_calls']} 个。"
                 f"合并 {summary['duplicate_usage_records']} 条同消息重复 usage 记录（含事件镜像与 DB 交叉验证）。",
                 f"有 {summary['calls_with_nonzero_cache_read']} 个调用记录非零 cache_read，"
                 f"{summary['calls_with_nonzero_reasoning']} 个记录非零 reasoning；"
                 f"其中 {summary['total_relations'].get('input+output+cache+reasoning', 0)} 个实测 total="
                 "input+output+cache+reasoning。另有 provider total 与分项不能吻合的记录，保留原值，未强行修正。",
                 "SQLite 使用 mode=ro/query_only，只查询会话关系和消息 usage 元数据，不导出凭据、提示词或响应正文。"
                 "独立 DB 只在已定位运行目录中寻找；全局 DB 只读取匹配 session 及其子会话消息。",
                 "证据文件大小及 SHA-256 见 audit.json；数据库保留来源路径、WAL 大小和查询结果，"
                 "可变 DB 的全库内容未复制或哈希。calls.csv 保留逐消息分项与来源，可脱离 DB 复核本次小计。",
                 "", "## 可精确比较的实验", "",
                 "| 唯一配对 ID | 实例 / 批次 | None total | Full total |", "|---|---|---:|---:|"]
        for p in exact:
            batch = Path(p["source_comparison"]).parent.name
            lines.append(f"| {p['unique_pair_id']} | {p['instance']} / {batch} | {p['none_exact_job_total_tokens']} | {p['full_exact_job_total_tokens']} |")
        if not exact:
            lines += ["| 无 | 现有记录不足以宣称任一完整配对成本精确恢复 | | |"]
        lines += ["", "各特性/模式的历史已记录小计见 family_mode_totals.csv。该表按不同 manifest 去重，"
                  "共享 baseline 准备运行按 run_purpose 单独列组。批次、模型和预算可能不同，不能合并为领域增强的因果结论。"]
        lines += ["", "## 覆盖缺口", "",
                  "超时、零事件、未完成 assistant 消息、provider 错误和总量缺失均保留为缺口；"
                  "数据库补回的已完成消息并不能证明失败请求从未消耗 token。传输层内部重试若无 usage，历史记录无法完整恢复，"
                  "不以估算或当前价格倒推。当前 runtime 源码只用于解释留存方式，不作为每次历史执行的版本证明。", ""]
        for kind, count in sorted(summary["gaps_by_kind"].items()):
            lines.append(f"- `{kind}`：{count} 处（具体运行及证据见 runs.csv / audit.json）。")
        lines += ["", "## 复现", "", "在工作区根目录运行（无新依赖、不调用模型）：", "", "```powershell",
                  ".venv/Scripts/python.exe tools/audit_none_full_tokens.py", "```", "",
                  "`--no-db` 可仅核本地事件与 usage 文件，但会明确降级完整性。"
                  "复跑只覆盖本导出目录；旧运行产物、.env、runtime/UI 均不修改。",
                  "输出：runs.csv、pairs.csv、calls.csv、invocations.csv、family_mode_totals.csv、"
                  "unique_pairs.csv、usage_artifact_checks.csv、unmetered_messages.csv、audit.json、README.md。",
                  "`--verify-export` 从已导出的逐调用 CSV 独立复核运行总量和配对数字，无须读取历史 DB。"]
        lines += ["`--refresh-export` 仅用本导出目录中的 JSON/CSV 重新生成表格与说明，无须重扫原日志或 DB。"]
        (self.output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        a = Audit(root, root, root)
        a.runs["U1"] = {"gaps": [], "duplicate_usage_records": 0, "db_matched_calls": 0, "db_recovered_calls": 0}
        t = {"total": 180, "input": 100, "output": 20, "reasoning": 10, "cache": {"read": 50, "write": 0}}
        a.add_call("U1", "ses1", "msg1", t, "events.jsonl#L1", "main")
        a.add_call("U1", "ses1", "msg1", t, "mirror.jsonl#L1", "main")
        assert len(a.calls) == 1 and sums(list(a.calls.values()))["total_tokens"] == 180
        a.add_call("U1", "ses1", "msg2", {"input": 3, "output": 4}, "retry.jsonl#L1", "main")
        assert len(a.calls) == 2 and list(a.calls.values())[1]["total_tokens"] is None
        assert "provider_total_not_recorded" in {g["kind"] for g in a.runs["U1"]["gaps"]}
        assert total_relation(normalized(t)) == "input+output+cache+reasoning"
        api = normalized({"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                          "prompt_cache_hit_tokens": 70, "reasoning_tokens": 20}, api=True)
        assert api["total_tokens"] == 130 and total_relation(api, True) == "input+output"
        assert normalized({}, True)["total_tokens"] is None
        db = root / "cache.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE session (id TEXT,parent_id TEXT,version TEXT)")
        con.execute("CREATE TABLE message (id TEXT,session_id TEXT,time_created INTEGER,data TEXT)")
        con.executemany("INSERT INTO session VALUES (?,?,?)", [("ses1", None, "fixture"), ("child", "ses1", "fixture")])
        for mid, sid, tokens in [("msg1", "ses1", t), ("child1", "child", {"input": 2, "output": 3, "total": 5}), ("unknown", "child", {})]:
            con.execute("INSERT INTO message VALUES (?,?,?,?)", (mid, sid, 0, json.dumps({"role": "assistant", "tokens": tokens, "finish": "stop"})))
        con.commit()
        con.close()
        a.read_db(db, global_db=True)
        assert len(a.calls) == 3 and a.runs["U1"]["db_recovered_calls"] == 1
        assert len(a.unmetered) == 1 and a.calls["U1|child|child1"]["total_tokens"] == 5
        assert sums(list(a.calls.values()))["total_tokens"] == 185
    print("Self-test passed: deduplication, distinct retries, missing totals, cache/reasoning, read-only DB child recovery, unmetered messages.")


def verify_export(output):
    payload = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    calls = read_csv(output / "calls.csv")
    identities = [(c["audit_run_id"], c["session_id"], c["message_id"]) for c in calls]
    assert len(identities) == len(set(identities)), "Duplicate call identities in export"
    for r in payload["runs"]:
        selected = [c for c in calls if c["audit_run_id"] == r["audit_run_id"]]
        for k in FIELDS:
            values = [int(c[k]) for c in selected if c[k] != ""]
            assert (sum(values) if values else None) == r["observed"][k], (r["audit_run_id"], k)
        if r["exact_job_total_tokens"] is not None:
            assert not r["gaps"] and r["observed"]["total_tokens_missing_calls"] == 0
    runs = {r["audit_run_id"]: r for r in payload["runs"]}
    for p in payload["pairs"]:
        assert p["unique_pair_id"] == unique_pair_id(runs[p["full_run"]]["manifest"], runs[p["none_run"]]["manifest"])
        for mode in ("full", "none"):
            r = runs[p[mode + "_run"]]
            assert p[mode + "_recorded_total_tokens"] == r["observed"]["total_tokens"]
            assert p[mode + "_exact_job_total_tokens"] == r["exact_job_total_tokens"]
    print(f"Export verification passed: {len(calls)} usage records, {len(runs)} runs, {len(payload['pairs'])} pair rows.")


def refresh_export(output):
    payload = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    root = Path(payload["root"])
    a = Audit(root, root / payload["package"], output)
    a.runs = {r["audit_run_id"]: r for r in payload["runs"]}
    for r in a.runs.values():
        r["run_purpose"] = run_purpose(r["manifest"])
    a.pairs, a.comparisons = payload["pairs"], payload["comparisons"]
    for p in a.pairs:
        p["unique_pair_id"] = unique_pair_id(a.runs[p["full_run"]]["manifest"], a.runs[p["none_run"]]["manifest"])

    def load_rows(name):
        rows = []
        for row in read_csv(output / name):
            if row == {"status": "no_rows"}:
                continue
            parsed = {}
            for k, v in row.items():
                if v == "":
                    parsed[k] = None
                elif v in {"True", "False"}:
                    parsed[k] = v == "True"
                else:
                    try:
                        parsed[k] = json.loads(v)
                    except ValueError:
                        parsed[k] = v
            rows.append(parsed)
        return rows

    for c in load_rows("calls.csv"):
        if c.get("is_subagent_session"):
            c["role"] = "subagent"
        a.calls[f"{c['audit_run_id']}|{c['session_id']}|{c['message_id']}"] = c
    for uid, run in a.runs.items():
        selected = [c for c in a.calls.values() if c["audit_run_id"] == uid]
        run["role_usage"] = {role: {"calls": sum(c["role"] == role and not c["aggregate_without_call_ids"] for c in selected),
                                     **sums([c for c in selected if c["role"] == role])} for role in ROLES}
    a.invocations = load_rows("invocations.csv")
    a.artifact_checks = load_rows("usage_artifact_checks.csv")
    for c in load_rows("unmetered_messages.csv"):
        a.unmetered[f"{c['audit_run_id']}|{c['session_id']}|{c['message_id']}"] = c
    a.db_audit = payload["databases"]
    a.evidence = {e["path"]: e for e in payload["evidence_files"]}
    a.scan = collections.Counter(payload["summary"]["scan"])
    a.versions = collections.Counter(payload["summary"]["session_versions"])
    a.write()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--package", type=Path, default=Path("exports/FJSP_Experiment_Package_20261015_v2"))
    parser.add_argument("--output", type=Path, default=Path("exports/none_full_token_usage_20261021"))
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify-export", action="store_true")
    parser.add_argument("--refresh-export", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    root = args.root.resolve()
    package = args.package.resolve() if args.package.is_absolute() else root / args.package
    output = args.output.resolve() if args.output.is_absolute() else root / args.output
    if output.resolve() == package.resolve() or not output.resolve().is_relative_to(root / "exports"):
        parser.error("output must be a separate directory under workspace exports")
    if args.verify_export:
        verify_export(output)
        return 0
    if args.refresh_export:
        refresh_export(output)
        return 0
    a = Audit(root, package, output, not args.no_db)
    a.load_inventory()
    a.scan_runs()
    a.recover_databases()
    a.finalize_usage_artifacts()
    a.complete()
    a.write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
