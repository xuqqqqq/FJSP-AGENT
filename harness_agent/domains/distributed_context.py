"""Context diagnostics for distributed FJSP with transfers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_agent.core.models import TaskContract
from harness_agent.domains.distributed_fjsp import parse_distributed_fjsp


@dataclass(frozen=True)
class DistributedFjspContextProvider:
    def inspect_instances(self, contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
        details: list[dict[str, Any]] = []
        for spec in contract.instances:
            path = spec.path if spec.path.is_absolute() else (project_root or contract.source_path.parent) / spec.path
            try:
                instance = parse_distributed_fjsp(path)
                candidates = [len(op.candidates) for job in instance.jobs for op in job.operations]
                details.append(
                    {
                        "id": spec.id,
                        "path": str(path.resolve()),
                        "parsed": True,
                        "variant": "fjsp_distributed_transfer",
                        "job_count": instance.job_count,
                        "factory_count": instance.factory_count,
                        "machines_per_factory": instance.machines_per_factory,
                        "machine_count": instance.machine_count,
                        "operation_count": instance.operation_count,
                        "max_candidate_count": instance.max_candidate_count,
                        "avg_candidate_count": round(sum(candidates) / max(1, len(candidates)), 6),
                        "same_factory_transfer_time": instance.same_factory_transfer_time,
                        "cross_factory_transfer_time": instance.cross_factory_transfer_time,
                        "transfer_unit_energy": instance.transfer_unit_energy,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                details.append({"id": spec.id, "path": str(path), "parsed": False, "error": str(exc)})
        parsed = [item for item in details if item.get("parsed")]
        status = "available" if len(parsed) == len(details) and parsed else "partial" if parsed else "unavailable"
        return {
            "status": status,
            "summary": {
                "instance_count": len(details),
                "profiled_count": len(parsed),
                "distributed_transfer_instance_count": len(parsed),
                "max_factory_count": max((int(item["factory_count"]) for item in parsed), default=0),
                "max_operation_count": max((int(item["operation_count"]) for item in parsed), default=0),
            },
            "direction_hints": [
                "Factory assignment, machine assignment, operation order, transfer delay, "
                "and energy must be evaluated together.",
                "Promotion uses the fixed lexicographic makespan, max-factory-workload, and total-energy objectives.",
            ],
            "instances": details[:12],
            "truncated": len(details) > 12,
        }

    def active_features(
        self,
        *,
        contract: TaskContract,
        instance_diagnostics: dict[str, Any],
        contract_review_evidence: dict[str, Any],
    ) -> list[str]:
        return [
            "fjsp_distributed_transfer",
            "distributed_transfer",
            "distributed_factories",
            "transfer_time",
            "energy_objective",
        ]

    def solution_contract(self) -> dict[str, Any]:
        return {
            "format": "standard_fjsp_schedule_v1",
            "required_top_level_fields": [
                "format",
                "makespan",
                "max_factory_workload",
                "total_energy_consumption",
                "schedule",
            ],
            "schedule_record_fields": ["job_id", "op_id", "factory_id", "machine_id", "start", "end"],
            "indexing": "all identifiers in solution records are 0-based integers",
            "legality_owner": "AlgoForge Core evaluator",
        }
