"""运行前健康检查：验证命令、路径、Worker 与契约是否可用。"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from harness_agent.core.ledger import ExperimentRecord
from harness_agent.core.models import BudgetSpec, TaskContract
from harness_agent.core.runner import HarnessRunner


@dataclass(frozen=True)
class HealthCheckRequest:
    """Request for contract, quick-test, and benchmark-stability checks."""

    contract_path: Path
    output_dir: Path
    project_root: Path
    repeats: int = 2
    max_instances: int = 1
    max_seeds: int = 1
    allow_draft: bool = False


def run_health_check(request: HealthCheckRequest) -> dict[str, Any]:
    """Verify that a task contract is runnable before an optimization loop."""

    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = TaskContract.load(request.contract_path)
    contract_errors = contract.validate(request.project_root)
    review_blocked = contract.requires_human_confirmation and not request.allow_draft

    quick_test = run_quick_test(contract, request.project_root, output_dir)
    probe_manifest: dict[str, Any] | None = None
    if not contract_errors and not review_blocked and quick_test["ok"]:
        probe_manifest = run_stability_probe(contract, request, output_dir / "stability_probe")

    manifest_path = output_dir / "health_check_manifest.json"
    report_path = output_dir / "health_check_report.md"
    manifest = {
        "status": health_status(
            contract_errors=contract_errors,
            review_blocked=review_blocked,
            quick_test=quick_test,
            probe_manifest=probe_manifest,
        ),
        "contract_path": str(request.contract_path.resolve()),
        "review_status": contract.review_status,
        "contract_errors": contract_errors,
        "review_blocked": review_blocked,
        "quick_test": quick_test,
        "stability_probe": probe_manifest,
        "artifacts": {
            "manifest": str(manifest_path.resolve()),
            "report": str(report_path.resolve()),
            "quick_stdout": quick_test.get("stdout_path"),
            "quick_stderr": quick_test.get("stderr_path"),
            "probe_report": str((output_dir / "stability_probe" / "report.md").resolve()) if probe_manifest else None,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_health_report(manifest), encoding="utf-8")
    return manifest


def run_quick_test(contract: TaskContract, project_root: Path, output_dir: Path) -> dict[str, Any]:
    """单独执行契约 quick test，并把 stdout/stderr 固化为健康检查证据。"""

    stdout_path = output_dir / "quick_test.stdout.txt"
    stderr_path = output_dir / "quick_test.stderr.txt"
    if not contract.commands.quick_test:
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("contract.commands.quick_test is missing\n", encoding="utf-8")
        return {
            "status": "missing",
            "ok": False,
            "command": None,
            "returncode": None,
            "elapsed_seconds": 0.0,
            "stdout_path": str(stdout_path.resolve()),
            "stderr_path": str(stderr_path.resolve()),
            "error": "contract.commands.quick_test is required for formal health checks",
        }

    start = time.perf_counter()
    try:
        result = subprocess.run(
            contract.commands.quick_test,
            cwd=project_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=contract.budget.timeout_seconds,
            check=False,
        )
        elapsed = time.perf_counter() - start
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        return {
            "status": "ok" if result.returncode == 0 else "failed",
            "ok": result.returncode == 0,
            "command": contract.commands.quick_test,
            "returncode": result.returncode,
            "elapsed_seconds": elapsed,
            "stdout_path": str(stdout_path.resolve()),
            "stderr_path": str(stderr_path.resolve()),
            "error": None if result.returncode == 0 else f"quick test failed with exit code {result.returncode}",
        }
    except Exception as exc:  # noqa: BLE001 - health reports must preserve probe failures as data.
        elapsed = time.perf_counter() - start
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(str(exc), encoding="utf-8")
        return {
            "status": "failed_runtime",
            "ok": False,
            "command": contract.commands.quick_test,
            "returncode": None,
            "elapsed_seconds": elapsed,
            "stdout_path": str(stdout_path.resolve()),
            "stderr_path": str(stderr_path.resolve()),
            "error": str(exc),
        }


def run_stability_probe(contract: TaskContract, request: HealthCheckRequest, output_dir: Path) -> dict[str, Any]:
    """用少量算例/seed 重复运行，验证相同输入是否产生稳定指标。"""

    selected_instances = contract.instances[: max(1, request.max_instances)]
    selected_seeds = contract.budget.seeds[: max(1, request.max_seeds)]
    probe_contract = replace(
        contract,
        task_id=f"{contract.task_id}_health_probe",
        instances=selected_instances,
        commands=replace(contract.commands, quick_test=None),
        budget=BudgetSpec(
            rounds=max(1, request.repeats),
            seeds=selected_seeds or [0],
            timeout_seconds=contract.budget.timeout_seconds,
            max_workers=1,
        ),
    )
    runner = HarnessRunner(contract=probe_contract, project_root=request.project_root, output_dir=output_dir)
    try:
        summary = runner.run()
        records = runner.ledger.list_records()
    finally:
        runner.close()

    stability = stability_summary(records)
    return {
        "status": "ok" if summary.failed == 0 and stability["stable"] else "failed",
        "total": summary.total,
        "valid": summary.valid,
        "failed": summary.failed,
        "selected_instances": [item.id for item in selected_instances],
        "selected_seeds": selected_seeds or [0],
        "repeats": max(1, request.repeats),
        "stable": stability["stable"],
        "groups": stability["groups"],
        "best_metrics": summary.best_metrics,
        "report": str((output_dir / "report.md").resolve()),
    }


def stability_summary(records: list[ExperimentRecord]) -> dict[str, Any]:
    """按 instance+seed 分组，检查重复记录的合法性、目标和 metrics 一致性。"""

    grouped: dict[tuple[str, int], list[ExperimentRecord]] = {}
    for record in records:
        grouped.setdefault((record.instance_id, record.seed), []).append(record)

    groups: list[dict[str, Any]] = []
    for (instance_id, seed), group in sorted(grouped.items()):
        valid_group = [record for record in group if record.valid]
        objective_keys = sorted({tuple(record.objective_key) for record in valid_group})
        metrics_fingerprints = sorted({json.dumps(record.metrics, sort_keys=True, ensure_ascii=False) for record in valid_group})
        groups.append(
            {
                "instance_id": instance_id,
                "seed": seed,
                "runs": len(group),
                "valid_runs": len(valid_group),
                "objective_keys": [list(key) for key in objective_keys],
                "distinct_metric_fingerprints": len(metrics_fingerprints),
                "stable": len(group) == len(valid_group) and len(objective_keys) == 1 and len(metrics_fingerprints) == 1,
            }
        )
    return {
        "stable": bool(groups) and all(item["stable"] for item in groups),
        "groups": groups,
    }


def health_status(
    *,
    contract_errors: list[str],
    review_blocked: bool,
    quick_test: dict[str, Any],
    probe_manifest: dict[str, Any] | None,
) -> str:
    """按契约、人工确认、quick test、稳定性顺序返回首个阻塞状态。"""

    if contract_errors:
        return "failed_contract"
    if review_blocked:
        return "requires_confirmation"
    if not quick_test.get("ok"):
        return "failed_quick_test"
    if not probe_manifest or probe_manifest.get("status") != "ok":
        return "failed_stability_probe"
    return "ok"


def render_health_report(manifest: dict[str, Any]) -> str:
    """将健康检查 manifest 渲染为面向人的 Markdown。"""

    probe = manifest.get("stability_probe") or {}
    lines = [
        "# Harness Health Check Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Contract: `{manifest.get('contract_path')}`",
        f"- Review status: `{manifest.get('review_status')}`",
        f"- Contract errors: `{json.dumps(manifest.get('contract_errors') or [], ensure_ascii=False)}`",
        f"- Quick test status: `{(manifest.get('quick_test') or {}).get('status')}`",
        f"- Stability status: `{probe.get('status', 'not_run')}`",
        f"- Stability valid/total: `{probe.get('valid', 0)}`/`{probe.get('total', 0)}`",
        f"- Stable repeated metrics: `{probe.get('stable', False)}`",
        "",
        "## Quick Test",
        "",
        f"```json\n{json.dumps(manifest.get('quick_test') or {}, ensure_ascii=False, indent=2)}\n```",
        "",
        "## Stability Probe",
        "",
        f"```json\n{json.dumps(probe, ensure_ascii=False, indent=2)}\n```",
    ]
    return "\n".join(lines).strip() + "\n"
