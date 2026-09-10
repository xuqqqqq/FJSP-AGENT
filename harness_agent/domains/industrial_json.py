"""Industrial JSON instance facts and frozen IO metadata, without scheduling code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness_agent.core.models import TaskContract, resolve_project_path


def inspect_industrial_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("time", "config", "eqp", "task", "transition", "setup"):
        if not isinstance(data.get(key), dict):
            raise ValueError(f"industrial input requires object {key}")
    if not data["task"] or not data["eqp"]:
        raise ValueError("industrial input requires tasks and machines")
    current = float(data["time"]["current_time"])
    horizon = float(data["config"]["max_output_horizon"])
    counts = dict(all_route_operation_count=0, minimum_selected_operation_count=0,
                  maximum_selected_operation_count=0, alternative_path_job_count=0,
                  batch_operation_count=0, minimum_time_lag_count=0,
                  maximum_time_lag_count=0, future_release_job_count=0,
                  repeated_candidate_machine_visits=0)
    machine_types: dict[str, set[bool]] = {}
    qtime_types: set[str] = set()
    for task in data["task"].values():
        routes = task["process_path"]
        if not routes:
            raise ValueError("task has no process path")
        counts["alternative_path_job_count"] += int(len(routes) > 1)
        counts["future_release_job_count"] += int(float(task["earliest_ava_time"]) > current)
        sizes = []
        for route in routes.values():
            operations = route["process_list"]
            if not operations:
                raise ValueError("route has no operations")
            sizes.append(len(operations))
            seen: set[str] = set()
            for operation in operations.values():
                batch = bool(operation["is_batch_type"])
                counts["batch_operation_count"] += int(batch)
                if not operation["eqp_list"]:
                    raise ValueError("operation has no eligible machine")
                for machine in operation["eqp_list"]:
                    if machine not in data["eqp"]:
                        raise ValueError("operation references unknown machine")
                    machine_types.setdefault(machine, set()).add(batch)
                    counts["repeated_candidate_machine_visits"] += int(machine in seen)
                    seen.add(machine)
            for lag in route.get("qtime_info", {}).values():
                if lag["start_process_seq"] not in operations or lag["end_process_seq"] not in operations:
                    raise ValueError("qtime references unknown route operation")
                qtime_types.add(f"{lag['start_process_type']}-{lag['end_process_type']}")
                counts["minimum_time_lag_count"] += int(lag.get("min_process_interval") is not None)
                counts["maximum_time_lag_count"] += int(lag.get("max_process_interval") is not None)
        counts["all_route_operation_count"] += sum(sizes)
        counts["minimum_selected_operation_count"] += min(sizes)
        counts["maximum_selected_operation_count"] += max(sizes)
    mixed = [machine for machine, kinds in machine_types.items() if len(kinds) > 1]
    if mixed:
        raise ValueError("fixed validator does not cover mixed ordinary/batch use of one machine")
    calendar = [window for machine in machine_types for window in data["eqp"][machine].get("eqp_down_interval", [])]
    positive_setup = sum(float(value) > 0 for row in data["setup"].values() for value in row.values())
    positive_transport = sum(float(value) > 0 for source, row in data["transition"].items()
                             if source in machine_types for target, value in row.items() if target in machine_types)
    features = {"industrial_json", "alternative_machines", "operation_precedence", "machine_capacity", "release_dates"}
    if counts["alternative_path_job_count"]:
        features.add("alternative_path")
    if counts["batch_operation_count"]:
        features.add("batching")
    if counts["minimum_time_lag_count"] or counts["maximum_time_lag_count"]:
        features.update(("time_lag", "industrial_qtime"))
    if calendar:
        features.add("machine_calendar")
    if positive_setup:
        features.add("sequence_dependent_setup")
    if positive_transport:
        features.add("transportation")
    return {
        "id": path.stem, "path": str(path.resolve()), "parsed": True, "variant": "fjsp_industrial_json",
        "job_count": len(data["task"]), "machine_count": len(data["eqp"]),
        "used_machine_count": len(machine_types), **counts,
        "current_time": current, "horizon": horizon, "qtime_anchor_types": sorted(qtime_types),
        "calendar_interval_count": len(calendar),
        "calendar_intervals_in_output_window": sum(start < horizon and end >= current for start, end in calendar),
        "positive_setup_arc_count": positive_setup, "positive_transport_arc_count": positive_transport,
        "active_features": sorted(features),
        "coverage_limits": ["Repeated machine candidates are not a forced reentrant loop.",
                            "Due dates and task priorities are not hard constraints in the fixed validator.",
                            "Alternative-route operation counts are not all mandatory scheduled operations."],
    }


class IndustrialJsonContextProvider:
    def inspect_instances(self, contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
        base = project_root or contract.source_path.parent
        instances = [inspect_industrial_json(resolve_project_path(base, item.path)) for item in contract.instances]
        features = {feature for item in instances for feature in item["active_features"]}
        objectives = {item.name for item in contract.objectives}
        if "completed_weight_within_horizon" in objectives:
            features.add("window_weight_objective")
        if "setup_count_positive" in objectives:
            features.add("setup_count_objective")
        if len(objectives) > 1:
            features.add("multi_objective")
        return {"status": "available", "active_features": sorted(features), "instances": instances,
                "summary": {"instance_count": len(instances), "profiled_count": len(instances),
                            "max_operation_count": max(item["all_route_operation_count"] for item in instances),
                            "max_job_count": max(item["job_count"] for item in instances),
                            "max_machine_count": max(item["machine_count"] for item in instances)},
                "direction_hints": []}

    def active_features(self, *, contract: TaskContract, instance_diagnostics: dict[str, Any],
                        contract_review_evidence: dict[str, Any]) -> list[str]:
        return list(instance_diagnostics["active_features"])

    def solution_contract(self) -> dict[str, Any]:
        return {"required_top_level_fields": ["task"], "required_object_fields": ["task"],
                "worker_smoke_validation": "schema_only",
                "operation_fields": ["temp_machine_id", "path_id", "process_start_time", "process_finish_time"],
                "notes": ["Use task.TASK.process_path.SEQ objects, not the standard schedule array.",
                          "Every task selects one route and outputs every operation of that route, even after horizon.",
                          "Core's frozen finite-batch validator is the only legality authority."]}
