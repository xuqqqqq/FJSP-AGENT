"""Build a bounded customer delivery from existing inputs and experiment records.

Only --build writes files, exclusively to a new exports directory and its ZIP.
No solver, evaluator, model, or framework code is executed.
"""

from __future__ import annotations

import argparse
import ast
import collections
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "exports/FJSP_Experiment_Package_20261015_v2"
ASSETS = ROOT / "exports/FJSP实验材料_20261015"
TARGET = ROOT / "exports/FJSP_Experiment_Package_20261021_v2"
DOWNLOADS = Path("C:/Users/ASUS/Downloads")
PUBLIC = DOWNLOADS / "ALL-Input-Information/ALL-Input-Information"
PUBLIC_FEATURES = {
    "1-FJSP": "standard_fjsp",
    "2-release_time-FJSP": "fjsp_release_time",
    "3-max_interval-FJSP": "fjsp_max_time_lag",
    "4-min_interval-FJSP": "fjsp_min_time_lag",
    "6-re_entrant-FJSP": "fjsp_reentrant",
    "7-Alternative-Process-Path-FJSP": "fjsp_alternative_path",
    "8-nfa-FJSP": "fjsp_machine_availability",
    "10-distributed-FJSP": "fjsp_distributed_transfer",
    "11-priority-FJSP": "fjsp_priority",
    "12-PBPM-FJSP": "fjsp_pbpm",
}
PROJECT_DOCS = {
    "standard_fjsp": "examples/standard_fjsp",
    "fjsp_sdst": "examples/fjsp_sdst_fattahi",
    "fjsp_release_time": "docs/variants/release_time/fjsp_release_time",
    "fjsp_reentrant": "docs/variants/reentrant/fjsp_reentrant",
    "fjsp_machine_availability": "docs/variants/machine_availability/fjsp_machine_availability",
    "fjsp_distributed_transfer": "docs/variants/distributed_transfer/fjsp_distributed_transfer",
    "fjsp_priority": None,
    "fjsp_pbpm": "docs/variants/pbpm/fjsp_pbpm",
    "fjsp_multiobjective_workload": "docs/variants/multiobjective/fjsp_multiobjective_workload",
    "fjsp_calendar_reentrant": "docs/variants/multi_feature/fjsp_calendar_reentrant",
    "fjsp_cell_sdst_transport_tardiness": "docs/variants/multi_feature/fjsp_cell_sdst_transport_tardiness",
    "fjsp_jpc_tst": "docs/variants/multi_feature/fjsp_jpc_tst",
}
DATA_EXT = {".txt", ".json", ".fjs", ".fjsp", ".dat", ".csv", ".xls", ".xlsx",
            ".apfjsp", ".tlfjsp", ".mitfjsp"}
CODE_EXT = {".py", ".cpp", ".c", ".cc", ".h", ".hpp", ".rs"}
PRUNE = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
         ".codex", ".opencode", ".opencode_runtime", ".algoforge_opencode_session",
         "candidate_worktree", "baseline_worktree", "final_worktree", "provided_project",
         "project_snapshot", "git_probe", "agent_generated_baseline_failed_worktree"}
SECRET = re.compile(
    rb"\bsk-[A-Za-z0-9_-]{16,}|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"
    rb"|\bxox[baprs]-[A-Za-z0-9-]{20,}|\bAKIA[A-Z0-9]{16}"
    rb"|(?i:Bearer\s+)[A-Za-z0-9._-]{24,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
KEY_LITERAL = re.compile(
    rb'''(?i)["']?(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)["']?\s*[:=]\s*["']([^"'\r\n]{16,})["']'''
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({key: json.dumps(value, ensure_ascii=True) if isinstance(value, (list, dict))
                     else value for key, value in row.items()} for row in rows)
    return stream.getvalue().encode("utf-8-sig")


def source_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def safe_files(directory):
    if not directory.is_dir():
        return
    for folder, dirs, files in os.walk(directory, followlinks=False):
        parent = Path(folder)
        dirs[:] = sorted(name for name in dirs if name not in PRUNE
                         and "worktree" not in name and not name.startswith("source_project")
                         and not (parent / name).is_symlink()
                         and not getattr(parent / name, "is_junction", lambda: False)())
        for name in sorted(files):
            path = parent / name
            if not path.is_symlink() and not name.startswith(".env"):
                yield path


def family_for(path):
    name = path.name.lower()
    for family, markers in (
        ("fjsp_calendar_reentrant", ("calendar_reentrant",)),
        ("fjsp_cell_sdst_transport_tardiness", ("cell_sdst_transport", ".fjcs.")),
        ("fjsp_jpc_tst", ("jpc_tst", ".jpctst.")),
        ("fjsp_alternative_path", ("alternative_path", ".apfjsp")),
        ("fjsp_max_time_lag", ("max_time_lag", ".tlfjsp")),
        ("fjsp_min_time_lag", ("min_time_lag", ".mitfjsp")),
        ("fjsp_release_time", ("release_time", ".rtfjsp.")),
        ("fjsp_multiobjective_workload", ("multiobjective", ".mofjsp.")),
        ("fjsp_pbpm", ("pbpm",)), ("fjsp_priority", ("priority",)),
        ("fjsp_reentrant", ("reentrant", "re_entrant", ".rjsp.")),
        ("fjsp_machine_availability", ("ffcr", "availability", "downtime")),
        ("fjsp_distributed_transfer", ("distributed_transfer", "dfm")),
        ("fjsp_sdst", ("sdst", "fattahi_setup", "oddla")),
    ):
        if any(marker in name for marker in markers):
            return family
    if path.suffix.lower() == ".json":
        data = read_json(path)
        if isinstance(data, dict) and "task" in data and "eqp" in data:
            return "industrial_fjsp"
    return "standard_fjsp"


def document_role(name):
    name = name.lower()
    if "requirement" in name:
        return "requirement"
    if name == "io.md" or "_io." in name:
        return "io"
    return "format_note"


def document_path(family, group, filename):
    folder = {"Web_Demo": "Project/Web_Demo",
              "Source_Format_Notes": "Original/Source_Format_Notes"}.get(group, group)
    return f"Instances/{family}/Documents/{folder}/{filename}"


def solver_sources(entrypoint):
    base = entrypoint.parent.resolve()
    pending = [entrypoint]
    selected = set()
    while pending:
        source = pending.pop()
        if source in selected:
            continue
        if not source.resolve().is_relative_to(base):
            raise ValueError(f"Solver dependency outside selected source directory: {source}")
        selected.add(source)
        if source.suffix != ".py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules += [node.module or ""]
                modules += [".".join(filter(None, (node.module, alias.name))) for alias in node.names]
        for module in modules:
            candidate = base.joinpath(*module.split("."))
            for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
                if path.is_file() and path not in selected:
                    pending.append(path)
    selected.update(source for source in entrypoint.parent.iterdir()
                    if source.is_file() and source.suffix in {".cpp", ".c", ".cc", ".h", ".hpp"})
    return sorted(selected)


def check_bytes(relative, raw):
    parts = Path(relative).parts
    if (not relative.isascii() or Path(relative).is_absolute() or ".." in parts
            or any(part.lower() in {"skills", ".codex", ".opencode"} for part in parts)
            or any(part.lower().startswith(".env") for part in parts)):
        raise ValueError(f"Disallowed package path: {relative}")
    lowered = raw.lower()
    if any(marker in lowered for marker in (b"sk-", b"ghp_", b"gho_", b"ghu_", b"ghs_", b"ghr_",
                                            b"github_pat_", b"xox", b"akia", b"bearer", b"private key")) and SECRET.search(raw):
        raise ValueError(f"Credential marker in selected file: {relative}")
    matches = KEY_LITERAL.finditer(raw) if any(marker in lowered for marker in
        (b"api_key", b"api-key", b"apikey", b"access_token", b"access-token", b"secret_key", b"secret-key", b"password")) else ()
    for match in matches:
        value = match.group(1).lower()
        if not any(marker in value for marker in (b"example", b"placeholder", b"your_", b"your-",
                                                  b"redacted", b"getenv", b"environ", b"${", b"{api")):
            raise ValueError(f"Credential literal in selected file: {relative}")


class Delivery:
    def __init__(self):
        self.files = {}
        self.sources = {}
        self.instance_rows = []
        self.document_rows = []
        self.documents = collections.defaultdict(list)
        self.run_rows = collections.defaultdict(list)
        self.missing = []
        self.omitted = []
        self.new_instances = []
        self.instance_lookup = {}
        self.experiment_map = {}
        self.historical_docs = collections.defaultdict(list)
        self.old_rows = {}
        self.new_manifests = []
        self.scanned_roots = []
        self.industrial_evidence = collections.defaultdict(list)
        self.additional_runs = []
        self.reference_names = {}
        self.csv_io_document = ""

    def add(self, source, relative, category):
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(source)
        raw = source.read_bytes()
        self.sources[source] = digest(raw)
        return self.put(relative, raw, category, str(source))

    def put(self, relative, raw, category, source="generated"):
        relative = str(relative).replace("\\", "/")
        check_bytes(relative, raw)
        if relative in self.files:
            if self.files[relative]["sha256"] != digest(raw):
                raise ValueError(f"Package path collision: {relative}")
            return relative
        self.files[relative] = {"raw": raw, "sha256": digest(raw), "category": category,
                                "source": source}
        return relative

    def document(self, source, family, group, original_source=None):
        raw = source.read_bytes()
        role = document_role(source.name)
        relative = document_path(family, group, source.name)
        if relative in self.files and self.files[relative]["sha256"] != digest(raw):
            relative = document_path(family, group, f"{digest(raw)[:12]}/{source.name}")
        self.add(source, relative, "document")
        row = {"feature": family, "role": role, "version": group,
               "document_file": relative, "source_file": str(original_source or source),
               "copied_from": str(source), "sha256": digest(raw)}
        if row not in self.document_rows:
            self.document_rows.append(row)
        if relative not in self.documents[family]:
            self.documents[family].append(relative)
        return relative

    def instance(self, source, family, relative, collection):
        name = self.add(source, relative, "instance")
        row = {"feature": family, "collection": collection, "instance_file": name,
               "source_file": str(source), "sha256": self.files[name]["sha256"]}
        self.instance_rows.append(row)
        self.instance_lookup.setdefault((family, row["sha256"]), name)
        return name

    def base(self):
        self.old_rows = {row["run_id"]: row for path in sorted((OLD / "experiments").glob("*/run_summary.csv"))
                         for row in read_csv(path)}
        for source in safe_files(OLD / "instances"):
            parts = source.relative_to(OLD).parts
            family = parts[1]
            if "generated" in parts and (source.name.endswith("_best.csv") or source.name == "dummy_instance.json"):
                self.omitted.append(source.relative_to(OLD).as_posix())
                continue
            relative = "Instances/" + source.relative_to(OLD / "instances").as_posix()
            self.instance(source, family, relative, "previous_delivery")
        for source in safe_files(OLD / "experiments"):
            relative = "Experiments/" + source.relative_to(OLD / "experiments").as_posix()
            if source.suffix == ".csv":
                self.experiment_map[source] = relative
            else:
                self.add(source, relative, "solver_code")
        for name in list(self.files) + list(self.experiment_map.values()):
            self.reference_names[name.casefold()] = name
            for parent in Path(name).parents:
                self.reference_names[parent.as_posix().casefold()] = parent.as_posix()

    def public(self):
        actual = {path.name for path in PUBLIC.iterdir() if path.is_dir()}
        if actual != set(PUBLIC_FEATURES):
            raise ValueError(f"Unexpected original feature roots: {actual}")
        for feature, family in PUBLIC_FEATURES.items():
            roots = [path for path in (PUBLIC / feature).iterdir()
                     if path.is_dir() and "instance" in path.name.lower()]
            if not roots:
                raise FileNotFoundError(f"No instance directory: {feature}")
            for directory in roots:
                for source in safe_files(directory):
                    relative = f"Instances/{family}/original/{source.relative_to(directory).as_posix()}"
                    self.instance(source, family, relative, "original_all_input_information")
            for source in safe_files(PUBLIC / feature / "docs"):
                if document_role(source.name) in {"io", "requirement"}:
                    self.document(source, family, "Original")

    def local(self):
        sources = [(path, Path("examples") / path.name) for path in (ROOT / "examples").iterdir()
                   if path.is_file()]
        sources += [(path, Path("variant_instances") / path.relative_to(ROOT / "examples/variant_instances"))
                    for path in safe_files(ROOT / "examples/variant_instances")]
        sources += [(path, Path("derived_instances") / path.relative_to(ROOT / "outputs/derived_instances"))
                    for path in safe_files(ROOT / "outputs/derived_instances")]
        for source, local in sources:
            if source.suffix.lower() not in DATA_EXT or any(marker in source.name.lower() for marker in
                    ("_best.csv", "dummy_", "reference_", "solution", "metrics", "manifest", "report")):
                continue
            family = family_for(source)
            if family == "industrial_fjsp":
                continue
            relative = f"Instances/{family}/generated/{local.as_posix()}"
            if relative not in self.files:
                self.new_instances.append(relative)
            self.instance(source, family, relative, "project_generated")
        extras = [
            (DOWNLOADS / "FJSP_SDST_HUdata_instances_package/FJSP_SDST_HUdata_instances_package/instances",
             "fjsp_sdst", "hudata"),
            (DOWNLOADS / "FJSP_SDST_Fattahi_instances/FJSP_SDST_Fattahi_instances", "fjsp_sdst", "fattahi"),
            (ROOT / "tmp/fjcs_dataset/Instances", "fjsp_cell_sdst_transport_tardiness", "fjcs_source"),
        ]
        for directory, family, label in extras:
            if not directory.is_dir():
                self.missing.append(f"Dataset directory unavailable: {directory}")
                continue
            for source in safe_files(directory):
                if source.suffix.lower() not in DATA_EXT or source.name.lower().startswith("readme"):
                    continue
                relative = f"Instances/{family}/original/{label}/{source.relative_to(directory).as_posix()}"
                if relative not in self.files:
                    self.new_instances.append(relative)
                self.instance(source, family, relative, "additional_public_original")
        for family, stem in PROJECT_DOCS.items():
            if stem:
                for role in ("io", "requirement"):
                    self.document(ROOT / f"{stem}_{role}.md", family, "Project")
        for source in sorted((ROOT / "docs/variants/industrial_json").glob("*.md")):
            self.document(source, "industrial_fjsp", "Project")
        for role in ("io", "requirement"):
            self.document(ROOT / f"examples/web_demo_{role}.md", "standard_fjsp", "Web_Demo")
        for family, source in (
            ("fjsp_sdst", DOWNLOADS / "FJSP_SDST_HUdata_instances_package/FJSP_SDST_HUdata_instances_package/README.txt"),
            ("fjsp_sdst", DOWNLOADS / "FJSP_SDST_Fattahi_instances/FJSP_SDST_Fattahi_instances/README.txt"),
        ):
            if source.is_file():
                self.document(source, family, "Source_Format_Notes")

    def standard_sources(self):
        roots = [(DOWNLOADS / "FJSP-Instance-main/FJSP-Instance-main/instance", "fjsp_instance_collection"),
                 (Path("F:/MAE/FJSP/instance"), "mae_instance_collection")]
        for directory, label in roots:
            if not directory.is_dir():
                self.missing.append(f"Standard dataset unavailable: {directory}")
                continue
            for source in safe_files(directory):
                if source.suffix.lower() not in {".txt", ".fjs", ".fjsp", ".dat"}:
                    continue
                sha = digest(source.read_bytes())
                relative = self.instance_lookup.get(("standard_fjsp", sha))
                if not relative:
                    relative = f"Instances/standard_fjsp/original/{label}/{source.relative_to(directory).as_posix()}"
                    self.new_instances.append(relative)
                self.instance(source, "standard_fjsp", relative, label)
        readme = DOWNLOADS / "FJSP-Instance-main/FJSP-Instance-main/README.md"
        if readme.is_file():
            self.document(readme, "standard_fjsp", "Source_Format_Notes")

    def industrial_inputs(self):
        # Inspect only previously indexed contracts that have actual execution evidence.
        cache = read_json(ROOT / "tmp/assets_inventory_20261015.json")
        for value in cache[1]:
            if not value.startswith(("outputs/contract_industrial", "outputs/iz", "outputs/if")):
                continue
            contract_path = ROOT / value
            if contract_path.name != "contract.json":
                continue
            parent = contract_path.parent
            executed = ((parent / "worker_loop").is_dir() or (parent / "manifest.json").is_file()
                        or (parent / "progress.json").is_file()
                        or any(path.is_dir() and path.name.startswith("total_round_") for path in parent.iterdir()))
            if not executed:
                continue
            self.sources[contract_path] = digest(contract_path.read_bytes())
            for item in read_json(contract_path).get("instances", []):
                source = source_path(item["path"])
                sha = digest(source.read_bytes())
                relative = self.instance_lookup.get(("industrial_fjsp", sha))
                if not relative:
                    relative = f"Instances/industrial_fjsp/{sha[:12]}/{source.name}"
                    self.new_instances.append(relative)
                self.instance(source, "industrial_fjsp", relative, "industrial_executed_only")
                self.industrial_evidence[relative].append(value)
        packaged = {name for name, entry in self.files.items()
                    if entry["category"] == "instance" and name.startswith("Instances/industrial_fjsp/")}
        if packaged != set(self.industrial_evidence):
            raise ValueError(f"Industrial inputs without executed contract evidence: {packaged - set(self.industrial_evidence)}")

    def industrial_documents(self):
        for row in self.old_rows.values():
            if not row["run_id"].startswith("I"):
                continue
            manifest_path = source_path(row["source_run"])
            data = read_json(manifest_path)
            worktree = data.get("final_worktree")
            if not worktree and data.get("rounds"):
                worktree = data["rounds"][-1].get("final_worktree")
            if worktree:
                directory = Path(worktree) / ".algoforge_worker_inputs/docs"
                for source in sorted(directory.glob("*.md")):
                    role = document_role(source.name)
                    if role in {"io", "requirement"}:
                        relative = self.document(source, "industrial_fjsp", "Historical")
                        self.historical_docs[row["run_id"]].append(relative)

    def archived_documents(self):
        family_by_run = {row["run_id"]: path.parent.name
                         for path in (OLD / "experiments").glob("*/run_summary.csv") for row in read_csv(path)}
        for row in read_csv(ASSETS / "02_test_data/算例与文档索引.csv"):
            run_id = row["run_id"]
            if row["role"] != "contracts" or run_id not in family_by_run:
                continue
            path = ASSETS / row["package_path"]
            relative = self.document(path, family_by_run[run_id], "Historical", row["original_path"])
            if relative not in self.historical_docs[run_id]:
                self.historical_docs[run_id].append(relative)

    def source_csv_document(self):
        source = ROOT / "tmp/fjcs_normalize.py"
        self.sources[source] = digest(source.read_bytes())
        family = "fjsp_cell_sdst_transport_tardiness"
        relative = document_path(family, "Project", "fjsp_cell_sdst_transport_csv_io.md")
        text = """# FJCS Original CSV Input and Normalized JSON Output

This format description follows the existing project converter tmp/fjcs_normalize.py
and the supplied Small, Medium and Large CSV files. It does not change the data.

## Original Input

The files are UTF-8 CSV with semicolon separators and empty padding cells.
The initial named rows provide n (jobs), L (part families), C (cells), and m
(physical machine columns). The Cell and Machine header rows associate each
physical machine column with its cell and logical machine. The operation
table follows the Alternative(for machine) header.

Each operation row begins with part family, source job ID, and operation
number. The next m cells are processing times for the physical machines;
a positive integer creates an eligible machine/time pair. Empty cells are
not eligible assignments. Repeated visits remain separate operations in
their original per-job row order.

The remaining sections contain directed family setup times (L by L),
directed cell transportation times (C by C), and source job due dates.
Their headers contain Part Family/Setup Time, Cell/Transportation Time,
and Job/Due Date respectively.

## Normalized Output

The project's existing converted instances use
fjsp_cell_sdst_transport_tardiness_instance_v1 JSON. Physical machine columns
become zero-based machine IDs. Cell IDs and family IDs become zero-based;
the original job number is retained as source_job_id. Jobs are ordered by
source job ID, and every operation retains its eligible machine/time pairs.
The JSON stores machine_cell_ids, logical_machine_ids, cell_transport_times,
family_setup_times and jobs[].due_date. The original_file field records
the collection-relative CSV source path.

The corresponding scheduling output is described in
[the JSON input/output contract](fjsp_cell_sdst_transport_tardiness_io.md).
The solver consumes normalized JSON; the original CSV is preserved as source
data. instance_index.csv links the two representations through
related_instance_files. Existing converted files cover Ins01 through Ins42;
the original Ins43 CSV is included without an existing normalized counterpart.
"""
        self.put(relative, text.encode("utf-8"), "document", str(source))
        self.document_rows.append({"feature": family, "role": "io", "version": "Project_Derived_CSV_Format",
                                   "document_file": relative, "source_file": str(source), "copied_from": "generated_from_existing_converter",
                                   "sha256": self.files[relative]["sha256"]})
        self.documents[family].append(relative)
        self.csv_io_document = relative
        self.missing.append("FJCS Ins43: no existing normalized JSON was found; the original Large/Ins.#43.csv is included.")

    def preferred_documents(self, family, role, instance=""):
        group = "Original" if "/original/" in instance and family in PUBLIC_FEATURES.values() and family != "standard_fjsp" else "Project"
        rows = [row for row in self.document_rows if row["feature"] == family and row["role"] == role]
        chosen = [row["document_file"] for row in rows if row["version"] == group]
        if not chosen:
            chosen = [row["document_file"] for row in rows if row["version"] == "Original"]
        if not chosen:
            self.missing.append(f"No {role} document for {family}: {instance}")
        return list(dict.fromkeys(chosen))

    def convert_reference(self, value):
        renamed = value.replace("instances/", "Instances/", 1).replace("experiments/", "Experiments/", 1)
        return self.reference_names.get(renamed.casefold(), renamed)

    def run_documents(self, family, row):
        paths = self.historical_docs.get(row["run_id"], [])
        requirements = [path for path in paths if document_role(Path(path).name) == "requirement"]
        ios = [path for path in paths if document_role(Path(path).name) == "io"]
        row["requirement_files"] = requirements or self.preferred_documents(family, "requirement")
        row["io_files"] = ios or self.preferred_documents(family, "io")
        row["document_version"] = "historical_experiment_snapshot" if requirements and ios else "feature_contract"
        return row

    def old_experiments(self):
        for source, relative in self.experiment_map.items():
            rows = read_csv(source)
            family = source.parent.name if source.name in {"run_summary.csv", "comparisons.csv"} else source.parent.parent.name
            for row in rows:
                for field in ("instance_files", "full_runs", "none_runs"):
                    if field in row:
                        row[field] = [self.convert_reference(value) for value in json.loads(row[field])]
                if row.get("solver_file"):
                    row["solver_file"] = self.convert_reference(row["solver_file"])
                if row.get("run_id"):
                    self.run_documents(family, row)
                if source.name == "run_summary.csv":
                    self.run_rows[family].append(row)
            self.put(relative, csv_bytes(rows), "experiment_result", str(source))

    def scan_existing(self):
        output = ROOT / "outputs"
        roots = [path for path in output.iterdir() if path.is_dir() and
                 (path.name.startswith(("contract_", "ab_", "iz")) or re.fullmatch(r"if\d+[a-z]*", path.name))]
        roots += [output / name for name in ("web_runs", "variant_runs", "selfhosted_variant_tests")]
        known = {str(source_path(row["source_run"]).resolve()).lower() for row in self.old_rows.values()}
        for directory in sorted(roots):
            self.scanned_roots.append(directory.relative_to(ROOT).as_posix())
            for path in safe_files(directory):
                if path.name == "standard_worker_loop_manifest.json" and str(path.resolve()).lower() not in known:
                    self.new_manifests.append(path)

    def frozen_input(self, source, expected, worktree, role):
        candidates = [source]
        if worktree:
            folder = Path(worktree) / ".algoforge_worker_inputs" / role
            candidates += [path for path in sorted(folder.glob("*")) if path.name.endswith(source.name)]
        for candidate in candidates:
            if candidate.is_file():
                raw = candidate.read_bytes()
                alternatives = [raw]
                if candidate.suffix == ".md":
                    lf = raw.replace(b"\r\n", b"\n")
                    alternatives += [lf, lf.replace(b"\n", b"\r\n")]
                for content in alternatives:
                    if not expected or digest(content) == expected:
                        self.sources[candidate] = digest(raw)
                        return candidate, content
        raise FileNotFoundError(f"Frozen {role} not available: {source}")

    def new_experiments(self):
        for index, path in enumerate(self.new_manifests, 1):
            data = read_json(path)
            self.sources[path] = digest(path.read_bytes())
            request = data.get("request") or {}
            worktree = data.get("final_worktree") or (data.get("best_legal_incumbent") or {}).get("worktree")
            run_id = f"N{index:04d}"
            docs = [source_path(value) for value in request.get("docs", [])]
            fingerprints = (data.get("input_fingerprints") or {}).get("files") or []
            inputs = [source_path(value) for value in request.get("instance_paths", [])]
            if not inputs and request.get("instance_dir"):
                inputs = sorted(source_path(request["instance_dir"]).glob(request.get("pattern") or "*.fjs"))
            if fingerprints:
                inputs = [source_path(item["path"]) for item in fingerprints if Path(item["path"]).suffix != ".md"]
            if not inputs:
                raise ValueError(f"No recorded inputs for additional run: {path}")
            family = family_for(inputs[0])
            hashes = {str(source_path(item["path"])): item.get("sha256", "") for item in fingerprints}
            instance_files = []
            for source in inputs:
                copied, raw = self.frozen_input(source, hashes.get(str(source), ""), worktree, "instances")
                sha = digest(raw)
                relative = self.instance_lookup.get((family, sha))
                if relative:
                    self.instance_rows.append({"feature": family, "collection": "additional_experiment_input",
                                               "instance_file": relative, "source_file": str(source), "sha256": sha})
                else:
                    relative = f"Instances/{family}/experiment_inputs/{sha[:12]}/{source.name}"
                    self.put(relative, raw, "instance", str(copied))
                    self.instance_lookup[(family, sha)] = relative
                    self.instance_rows.append({"feature": family, "collection": "additional_experiment_input",
                                               "instance_file": relative, "source_file": str(source), "sha256": sha})
                    self.new_instances.append(relative)
                instance_files.append(relative)
            for source in docs:
                copied, raw = self.frozen_input(source, hashes.get(str(source), ""), worktree, "docs")
                relative = document_path(family, "Historical", f"{digest(raw)[:12]}/{source.name}")
                self.put(relative, raw, "document", str(copied))
                self.document_rows.append({"feature": family, "role": document_role(source.name), "version": "Historical",
                                          "document_file": relative, "source_file": str(source), "copied_from": str(copied),
                                          "sha256": digest(raw)})
                self.historical_docs[run_id].append(relative)
            date_match = re.search(r"20\d{6}", path.relative_to(ROOT).as_posix())
            date = date_match.group() if date_match else "historical"
            mode = request.get("guidance_mode") or "unspecified"
            folder = f"Experiments/{family}/{inputs[0].stem[:60].lower()}_{date}_{mode}_{run_id.lower()}"
            contract = path.with_name("standard_worker_contract.json")
            command = ((read_json(contract).get("commands") or {}).get("solver") if contract.is_file() else None)
            command = command or request.get("provided_solver_command") or ""
            target = request.get("provided_target_file") or request.get("agent_generated_solver_path")
            for token in shlex.split(command, posix=False):
                candidate = Path(token.strip("\"'"))
                if (candidate.suffix == ".py" and not candidate.is_absolute() and ".." not in candidate.parts
                        and worktree and (Path(worktree) / candidate).is_file()):
                    target = candidate.as_posix()
                    break
            solver_file = ""
            if target and worktree:
                entrypoint = Path(worktree) / target
                if entrypoint.is_file() and entrypoint.resolve().is_relative_to(Path(worktree).resolve()):
                    for source in solver_sources(entrypoint):
                        relative = self.add(source, folder + "/" + source.relative_to(entrypoint.parent).as_posix(), "solver_code")
                        if source == entrypoint:
                            solver_file = relative
            final = data.get("final_summary") or {}
            metrics = final.get("best_metrics") or {}
            row = {"run_id": run_id, "instance": "; ".join(source.name for source in inputs),
                   "guidance_mode": mode, "batch": path.parent.name, "status": data.get("status", ""),
                   "valid_instances": final.get("valid", ""), "total_instances": final.get("total", ""),
                   "completed_rounds": data.get("round_count", ""), "promoted_rounds": data.get("promoted_rounds", "")}
            row.update({key: value for key, value in metrics.items() if isinstance(value, (int, float, str)) and not key.startswith("_")})
            row.update({"objective_key": data.get("final_key"), "model": request.get("worker_model", ""),
                        "seed": request.get("seeds", []), "core_timeout_seconds": request.get("timeout_seconds", ""),
                        "instance_files": instance_files, "solver_file": solver_file,
                        "code_description": "retained_final_code" if solver_file else "not_available",
                        "original_command": command, "source_run": path.relative_to(ROOT).as_posix()})
            self.run_documents(family, row)
            self.run_rows[family].append(row)
            self.additional_runs.append(row)
            self.put(folder + "/results.csv", csv_bytes([row]), "experiment_result", str(path))
        for family, rows in self.run_rows.items():
            relative = f"Experiments/{family}/run_summary.csv"
            self.files.pop(relative, None)
            self.put(relative, csv_bytes(rows), "experiment_result")

    def indices(self):
        grouped = collections.defaultdict(list)
        for row in self.instance_rows:
            if row not in grouped[row["instance_file"]]:
                grouped[row["instance_file"]].append(row)
        usage = collections.defaultdict(list)
        for rows in self.run_rows.values():
            for row in rows:
                for name in row["instance_files"]:
                    usage[name].append(row["run_id"])
        instance_index = []
        related = collections.defaultdict(list)
        raw_csvs = {}
        for name in grouped:
            marker = "/original/fjcs_source/"
            if marker in name:
                raw_csvs[name.split(marker, 1)[1]] = name
        for name in grouped:
            if name.endswith(".fjcs.json"):
                original = json.loads(self.files[name]["raw"]).get("source", {}).get("original_file")
                if original in raw_csvs:
                    raw = raw_csvs[original]
                    related[name].append(raw)
                    related[raw].append(name)
        for name, rows in sorted(grouped.items()):
            family = rows[0]["feature"]
            ios = self.preferred_documents(family, "io", name)
            kind = "original_source_csv" if "/original/fjcs_source/" in name else "solver_input"
            if kind == "original_source_csv":
                ios = [self.csv_io_document] + ios
            instance_index.append({"feature": family, "instance_file": name,
                                   "representation": kind,
                                   "bytes": len(self.files[name]["raw"]), "sha256": self.files[name]["sha256"],
                                   "collections": sorted({row["collection"] for row in rows}),
                                   "source_files": sorted({row["source_file"] for row in rows}),
                                   "requirement_files": self.preferred_documents(family, "requirement", name),
                                   "io_files": ios,
                                   "related_instance_files": sorted(related[name]),
                                   "experiment_runs": sorted(set(usage[name])),
                                   "industrial_execution_contracts": sorted(set(self.industrial_evidence.get(name, [])))})
        self.put("Instances/instance_index.csv", csv_bytes(instance_index), "index")
        documents = collections.defaultdict(list)
        for row in self.document_rows:
            documents[row["document_file"]].append(row)
        document_index = []
        for name, rows in sorted(documents.items()):
            first = rows[0]
            document_index.append({"feature": first["feature"], "role": first["role"], "version": first["version"],
                                   "document_file": name, "bytes": len(self.files[name]["raw"]), "sha256": first["sha256"],
                                   "source_files": sorted({row["source_file"] for row in rows}),
                                   "copied_from": sorted({row["copied_from"] for row in rows})})
        self.put("Instances/document_index.csv", csv_bytes(document_index), "index")
        features = sorted({row["feature"] for row in instance_index})
        feature_rows = []
        for family in features:
            inputs = [row for row in instance_index if row["feature"] == family]
            feature_rows.append({"feature": family, "instance_files": len(inputs),
                                 "distinct_content": len({row["sha256"] for row in inputs}),
                                 "instance_bytes": sum(row["bytes"] for row in inputs),
                                 "runs": len(self.run_rows[family]),
                                 "requirement_files": self.preferred_documents(family, "requirement"),
                                 "io_files": self.preferred_documents(family, "io")})
        self.put("Instances/feature_map.csv", csv_bytes(feature_rows), "index")
        return instance_index, document_index, feature_rows

    def original_coverage(self):
        indexed = {source_path(row["source_file"]).resolve(): row for row in self.instance_rows
                   if row["collection"] == "original_all_input_information"}
        result = []
        for feature, family in PUBLIC_FEATURES.items():
            sources = [source for directory in (PUBLIC / feature).iterdir()
                       if directory.is_dir() and "instance" in directory.name.lower()
                       for source in safe_files(directory)]
            covered = 0
            for source in sources:
                row = indexed.get(source.resolve())
                if row and self.files[row["instance_file"]]["sha256"] == digest(source.read_bytes()):
                    covered += 1
            if len(sources) != covered:
                raise ValueError(f"Original collection coverage incomplete: {feature}, {covered}/{len(sources)}")
            result.append({"source_feature": feature, "package_feature": family,
                           "source_files": len(sources), "covered_files": covered})
        return result

    def readme(self, instances, documents, features):
        runs = [row for rows in self.run_rows.values() for row in rows]
        lines = ["# FJSP Experiment Package", "", "Prepared on 2026-10-21.", "",
                 "## Contents", "", "```text", TARGET.name + "/", "  README.md", "  Instances/",
                 "    instance_index.csv", "    document_index.csv", "    feature_map.csv",
                 "    <feature>/original/", "    <feature>/generated/",
                 "    <feature>/Documents/Original/", "    <feature>/Documents/Project/",
                 "    <feature>/Documents/Historical/",
                 "  Experiments/", "    <feature>/run_summary.csv", "    <feature>/comparisons.csv",
                 "    <feature>/<instance_date_mode_run>/results.csv",
                 "    <feature>/<instance_date_mode_run>/<solver source files>", "```", "",
                 "## Instance and Document Mapping", "",
                 "[Instances/instance_index.csv](Instances/instance_index.csv) maps every packaged input to its feature, "
                 "source file(s), requirement, input/output contract, and existing experiment run IDs. "
                 "SHA-256 and byte counts distinguish files with the same name or different historical contents.", "",
                 "[Instances/document_index.csv](Instances/document_index.csv) identifies each document's role, version, "
                 "original source, and copied snapshot. [Instances/feature_map.csv](Instances/feature_map.csv) provides a compact feature mapping. "
                 "Each feature's documents are stored inside its own Instances/<feature>/Documents directory.", "",
                 "`Original` contains the customer's supplied contracts. `Project` contains the project's maintained contracts. "
                 "`Historical` preserves contracts associated with existing experiments, including earlier versions. "
                 "Input index links use Original contracts for the customer's variant collections and Project contracts for project examples. "
                 "For a historical experiment, use the `requirement_files` and `io_files` in its own results.csv. "
                 "Those links take precedence over the current feature contract.", "",
                 "All paths in JSON-valued CSV cells are relative to this package root. CSV files use UTF-8 with BOM.", "",
                 "| Feature | Input Files | Distinct Contents | Runs | Requirement | Input/Output |",
                 "| --- | ---: | ---: | ---: | --- | --- |"]
        for row in features:
            links = lambda names: "<br>".join(f"[{Path(name).name}]({name})" for name in names)
            lines.append(f"| {row['feature']} | {row['instance_files']} | {row['distinct_content']} | {row['runs']} | "
                         f"{links(row['requirement_files'])} | {links(row['io_files'])} |")
        coverage = self.original_coverage()
        source_count = sum(row["source_files"] for row in coverage)
        lines += ["", "## Source Collections", "",
                  f"All {source_count} files in the ten Instance/Instances collections under the supplied ALL-Input-Information directory "
                  "are included, preserving their collection-relative paths. The input index records every source file. "
                  "Coverage counts below compare source file contents with their packaged SHA-256 hashes.", "",
                  "| Original Feature | Source Files | Covered Files |", "| --- | ---: | ---: |"]
        lines += [f"| {row['source_feature']} | {row['source_files']} | {row['covered_files']} |" for row in coverage]
        lines += ["",
                  "The complete SDST HUdata and Fattahi collections, the original cell/transport dataset, "
                  "project examples, examples/variant_instances, and the input in outputs/derived_instances are included. "
                  "The standard public collections from FJSP-Instance-main and the experiment-referenced F:/MAE/FJSP/instance "
                  "are compared by content; identical data reuse an existing package file and retain all source mappings. "
                  "The standard collection includes JSP cases with one eligible machine per operation. "
                  "Native .fjs and converted .txt inputs retain their original contents; the standard IO contract describes machine numbering.", "",
                  "Reference-score CSVs and the dummy framework fixture previously placed in generated inputs are excluded from the instance count. "
                  "The .apfjsp, .tlfjsp and .mitfjsp examples are included.", "",
                  "For cell/transport data, original CSVs and normalized JSONs are separate representations. "
                  "The instance index marks source CSVs and links their existing normalized counterparts; "
                  "the supplied CSV input/JSON output description documents the existing conversion. "
                  "The complete 43-file CSV collection and all 42 existing normalized examples are included.", "",
                  "Industrial inputs are limited to the existing experiment inputs. Their index rows include the executed contract locations; "
                  "unused industrial generation directories are excluded.", "",
                  "## Existing Experiments", "",
                  "Each feature's run_summary.csv lists its runs; each experiment directory keeps results.csv beside its retained solver code. "
                  "The original 408 run records and 95 comparison rows are preserved, including failed runs and runs absent from the reports. "
                  f"An additional {len(self.additional_runs)} already-existing runs from selfhosted_variant_tests, variant_runs and newer web_runs are included. "
                  "N-prefixed IDs identify these additional records. No new experiment was executed to prepare this package.", "",
                  "`full` and `none` identify the original guidance modes; `unspecified` means no mode was recorded. "
                  "`valid_instances` and `total_instances` retain the source evaluation counts, which can include multiple seeds. "
                  "`retained_final_code` identifies the saved final solver; `last_unaccepted_attempt` identifies a failed industrial run's last attempt. "
                  "A blank solver_file and `not_available` preserve a historical run without retained code. "
                  "A blank metric retains the absence of an evaluation result. These records are not presented as successful runs.", "",
                  f"Total: {len(instances)} input files, {len(documents)} requirement/IO/format documents, "
                  f"{len(runs)} existing run records, {len(features)} input features.", ""]
        missing = sorted(set(self.missing))
        if missing:
            lines += ["## Unavailable Source Material", ""] + ["- " + value for value in missing] + [""]
        self.put("README.md", "\n".join(lines).encode("utf-8"), "readme")

    def verify(self):
        if {Path(name).parts[0] for name in self.files} != {"README.md", "Instances", "Experiments"}:
            raise ValueError("Package root must contain README.md, Instances and Experiments only")
        for name, entry in self.files.items():
            if entry["category"] == "document":
                parts = Path(name).parts
                if (len(parts) < 5 or parts[0] != "Instances" or parts[2] != "Documents"
                        or parts[3] not in {"Original", "Project", "Historical"}):
                    raise ValueError(f"Document outside feature document groups: {name}")
        indexed_instances = {row["instance_file"] for row in self.instance_rows}
        actual_instances = {name for name, entry in self.files.items() if entry["category"] == "instance"}
        if indexed_instances != actual_instances:
            raise ValueError("Instance index does not cover all input files")
        for name in ("Instances/instance_index.csv", "Instances/document_index.csv", "Instances/feature_map.csv"):
            for row in csv.DictReader(io.StringIO(self.files[name]["raw"].decode("utf-8-sig"))):
                for field in ("requirement_files", "io_files", "related_instance_files"):
                    for reference in json.loads(row.get(field) or "[]"):
                        if reference not in self.files:
                            raise ValueError(f"Broken index reference: {name}, {reference}")
                for field in ("instance_file", "document_file"):
                    if field in row and self.files[row[field]]["sha256"] != row["sha256"]:
                        raise ValueError(f"Indexed content hash differs: {name}, {row[field]}")
        if "README.md" in self.files:
            for reference in re.findall(r"\]\(([^)]+)\)", self.files["README.md"]["raw"].decode("utf-8")):
                if reference not in self.files:
                    raise ValueError(f"Broken README reference: {reference}")
        new_rows = {row["run_id"]: row for rows in self.run_rows.values() for row in rows}
        for run_id, old in self.old_rows.items():
            new = new_rows[run_id]
            for key, value in old.items():
                if key == "instance_files":
                    expected = [self.convert_reference(name) for name in json.loads(value)]
                elif key == "solver_file":
                    expected = self.convert_reference(value) if value else value
                else:
                    expected = value
                if new[key] != expected:
                    raise ValueError(f"Historical run field changed: {run_id}, {key}")
        list_fields = {"instance_files", "full_runs", "none_runs", "requirement_files", "io_files"}
        for name, entry in self.files.items():
            if entry["category"] == "experiment_result":
                rows = csv.DictReader(io.StringIO(entry["raw"].decode("utf-8-sig")))
                for row in rows:
                    for field in list_fields:
                        for reference in json.loads(row.get(field) or "[]"):
                            if reference not in self.files and not any(key.startswith(reference + "/") for key in self.files):
                                raise ValueError(f"Broken {field} reference: {name}, {reference}")
                    if row.get("solver_file") and row["solver_file"] not in self.files:
                        raise ValueError(f"Broken solver reference: {name}")
                    if row.get("solver_file") and not Path(row["solver_file"]).is_relative_to(Path(name).parent) and Path(name).name == "results.csv":
                        raise ValueError(f"Solver and results in different experiment folders: {name}")
                    if row.get("run_id") and (not json.loads(row["requirement_files"]) or not json.loads(row["io_files"])):
                        raise ValueError(f"Run without corresponding documents: {name}")
        for source, relative in self.experiment_map.items():
            if source.name == "run_summary.csv":
                continue
            old = read_csv(source)
            new = list(csv.DictReader(io.StringIO(self.files[relative]["raw"].decode("utf-8-sig"))))
            if len(old) != len(new):
                raise ValueError(f"Historical result row count changed: {source}")
            for before, after in zip(old, new):
                for field, value in before.items():
                    if field in {"instance_files", "full_runs", "none_runs"}:
                        expected = [self.convert_reference(item) for item in json.loads(value)]
                        if json.loads(after[field]) != expected:
                            raise ValueError(f"Historical result link changed: {source}")
                    elif field == "solver_file":
                        if after[field] != self.convert_reference(value):
                            raise ValueError(f"Historical solver link changed: {source}")
                    elif after[field] != value:
                        raise ValueError(f"Historical result value changed: {source}, {field}")
        for source, expected in self.sources.items():
            if digest(source.read_bytes()) != expected:
                raise ValueError(f"Source changed during packaging: {source}")

    def build(self):
        if TARGET.exists() or TARGET.with_suffix(".zip").exists():
            raise FileExistsError(f"Refusing to overwrite delivery: {TARGET}")
        instances, documents, features = self.indices()
        self.readme(instances, documents, features)
        self.verify()
        TARGET.mkdir()
        for name, entry in sorted(self.files.items()):
            dest = TARGET / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(entry["raw"])
        archive = TARGET.with_suffix(".zip")
        with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for name in sorted(self.files):
                zipped.write(TARGET / name, TARGET.name + "/" + name)
        with zipfile.ZipFile(archive) as zipped:
            if zipped.testzip() is not None or len(zipped.namelist()) != len(set(zipped.namelist())):
                raise ValueError("ZIP integrity failure")
            if len(zipped.namelist()) != len(self.files):
                raise ValueError("ZIP file count mismatch")
            for name, entry in self.files.items():
                raw = zipped.read(TARGET.name + "/" + name)
                check_bytes(name, raw)
                if digest(raw) != entry["sha256"] or (TARGET / name).read_bytes() != raw:
                    raise ValueError(f"ZIP/directory/source byte mismatch: {name}")
        self.verify()
        if {path.name for path in TARGET.iterdir()} != {"README.md", "Instances", "Experiments"}:
            raise ValueError("Written delivery has unexpected root entries")
        categories = {}
        for category in sorted({entry["category"] for entry in self.files.values()}):
            entries = [entry for entry in self.files.values() if entry["category"] == category]
            categories[category] = {"files": len(entries), "bytes": sum(len(entry["raw"]) for entry in entries)}
        print(json.dumps({"directory": str(TARGET), "zip": str(archive), "zip_bytes": archive.stat().st_size,
                          "zip_sha256": digest(archive.read_bytes()), "categories": categories,
                          "features": features, "runs": sum(map(len, self.run_rows.values())),
                          "added_runs": len(self.additional_runs), "added_instance_paths": len(set(self.new_instances)),
                          "original_files_mapped": sum(row["collection"] == "original_all_input_information" for row in self.instance_rows),
                          "original_source_coverage": self.original_coverage(),
                          "industrial_inputs": len(self.industrial_evidence), "missing": sorted(set(self.missing)),
                          "code_unavailable_runs": [row["run_id"] for rows in self.run_rows.values() for row in rows if not row.get("solver_file")],
                          "all_source_hashes_unchanged": True, "all_package_references_resolve": True,
                          "historical_metrics_preserved": True, "zip_contents_verified": True,
                          "credential_scan_passed": True}, ensure_ascii=False, indent=2))

    def inspect(self):
        category = collections.Counter(entry["category"] for entry in self.files.values())
        old_counts = collections.Counter(path.parent.parent.name for path in (OLD / "experiments").glob("*/*/results.csv"))
        print(json.dumps({"old_runs": len(self.old_rows), "old_features": dict(old_counts),
                          "planned_files": dict(category), "original_source_files": sum(row["collection"] == "original_all_input_information" for row in self.instance_rows),
                          "project_source_files": sum(row["collection"] == "project_generated" for row in self.instance_rows),
                          "new_instance_path_count": len(set(self.new_instances)),
                          "new_instance_paths_by_feature": dict(collections.Counter(Path(name).parts[1] for name in set(self.new_instances))),
                          "omitted_non_instance_files": self.omitted,
                          "document_sources": len(self.document_rows), "historical_run_document_links": sum(map(len, self.historical_docs.values())),
                          "bounded_output_roots": len(self.scanned_roots), "additional_manifests": [str(path.relative_to(ROOT)) for path in self.new_manifests],
                          "code_unavailable_runs": [row["run_id"] for rows in self.run_rows.values() for row in rows if not row.get("solver_file")],
                          "missing": sorted(set(self.missing))}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    delivery = Delivery()
    print("Comparing previous delivery and original collections", flush=True)
    delivery.base()
    delivery.public()
    delivery.local()
    delivery.standard_sources()
    delivery.industrial_inputs()
    delivery.source_csv_document()
    print("Resolving historical requirement and IO snapshots", flush=True)
    delivery.archived_documents()
    delivery.industrial_documents()
    delivery.old_experiments()
    print("Checking bounded existing experiment roots", flush=True)
    delivery.scan_existing()
    delivery.new_experiments()
    if args.build:
        print("Writing and verifying the new delivery", flush=True)
        delivery.build()
    else:
        delivery.indices()
        delivery.verify()
        delivery.inspect()


if __name__ == "__main__":
    main()
