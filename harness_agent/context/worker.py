"""面向 Coding Worker 的上下文视图与优先级整理。"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from harness_agent.agents.quality_contract import (
    build_agent_generated_solver_quality_contract,
    build_solver_runtime_feature_contract,
)
from harness_agent.context.knowledge import (
    resolve_method_package,
    resolve_worker_implementation_skills,
    select_tagged_knowledge_cards,
)
from harness_agent.worker import WorkerAssignment


# Keep ordinary assignments concise, but do not reject evidence-heavy work only
# because it crosses the preferred size by a small amount. The hard ceiling
# remains bounded so a malformed Main response cannot flood Worker context.
WORKER_ASSIGNMENT_SOFT_CHARS = 12_000
WORKER_ASSIGNMENT_MAX_CHARS = 24_000
PROJECT_ROOT = Path(__file__).resolve().parents[2]
HIGH_FLEX_BASELINE_REDUNDANT_CARDS = {
    "constructive_multistart_blueprint.md",
    "optimization_playbook.md",
    "idle_critical_beam_implementation_template.md",
    "core_pseudocode.md",
}


def build_worker_assignment(
    *,
    context: dict[str, Any],
    direction_plan: dict[str, Any],
    loop_feedback: dict[str, Any],
    round_index: int,
    attempt_index: int,
    max_steps: int,
    max_runtime_seconds: int,
    parent_assignment_id: str | None = None,
) -> WorkerAssignment:
    """把 Main Agent 方向编译成 Worker 唯一可见的最小任务书。

    这里做的是通用 handoff 约束，不选择或实现任何 FJSP 算法。算法行为
    仍来自 Main Agent 选中的 Method Package；Worker 只得到精确路径和
    有序交付物，不得到完整 Context Packet 或未选中的知识目录。
    """

    direction_id = str(direction_plan.get("direction_id") or f"d{round_index:03d}").strip()
    latest_feedback = _assignment_feedback(loop_feedback, attempt_index=attempt_index)
    baseline_trial = attempt_index + 1 if round_index < 0 else None
    if baseline_trial is not None:
        try:
            requested_baseline_trial = int(latest_feedback.get("baseline_trial") or baseline_trial)
        except (TypeError, ValueError):
            requested_baseline_trial = baseline_trial
        baseline_trial = max(1, min(3, requested_baseline_trial))
    baseline_exact_rescue = bool(
        round_index < 0
        and attempt_index > 0
        and isinstance(latest_feedback.get("baseline_feasibility_rescue"), dict)
        and str(direction_plan.get("method_family") or "").strip() == "exact_hybrid"
    )
    baseline_exact_rescue_repair = bool(
        baseline_exact_rescue
        and isinstance(
            (
                latest_feedback.get("repair_targets")
                if isinstance(latest_feedback.get("repair_targets"), dict)
                else {}
            ).get("exact_execution_failure"),
            dict,
        )
    )
    is_objective_refinement = (
        attempt_index > 0
        and str(latest_feedback.get("status") or "") == "refinement_required"
        and latest_feedback.get("allow_objective_refinement") is True
    )
    mode = (
        "repair"
        if baseline_exact_rescue_repair
        else
        "baseline"
        if baseline_exact_rescue
        else
        "baseline"
        if baseline_trial == 1
        else "improvement"
        if is_objective_refinement
        else "repair"
        if attempt_index > 0
        else "baseline"
        if round_index < 0
        else "improvement"
    )
    assignment_id = f"{_safe_identifier(direction_id)}-a{attempt_index:02d}"
    high_flex_baseline = bool(
        baseline_trial
        and "high_flexibility"
        in {
            str(item).strip().lower()
            for item in direction_plan.get("knowledge_query") or []
            if str(item).strip()
        }
    )
    target_file = _solver_target(context)
    runtime_feature_contract = build_solver_runtime_feature_contract(context)
    active_package = _selected_method_package(
        context,
        direction_plan,
        active_features=runtime_feature_contract.get("active_features") or [],
    )
    implementation_bundle = (
        direction_plan.get("implementation_bundle")
        if isinstance(direction_plan.get("implementation_bundle"), dict)
        else {}
    )
    variant_baseline_package = bool(
        baseline_trial == 1
        and active_package
        and active_package.get("required_features")
        and implementation_bundle.get("required_components")
    )
    component_rows = [
        item for item in implementation_bundle.get("required_components") or [] if isinstance(item, dict)
    ]
    requested_order = _strings(direction_plan.get("implementation_order"), limit=32)
    implementation_order = requested_order or [
        str(item.get("component_id") or "").strip()
        for item in component_rows
        if str(item.get("component_id") or "").strip()
    ]
    quality_contract = build_agent_generated_solver_quality_contract(context)
    evaluator_protocol = context.get("evaluator_protocol") if isinstance(context.get("evaluator_protocol"), dict) else {}
    edit_policy = context.get("edit_policy") if isinstance(context.get("edit_policy"), dict) else {}
    incumbent_assessment = (
        direction_plan.get("incumbent_assessment")
        if isinstance(direction_plan.get("incumbent_assessment"), dict)
        else {}
    )
    next_mutation = (
        direction_plan.get("next_mutation")
        if isinstance(direction_plan.get("next_mutation"), dict)
        else {}
    )
    # Baseline plans have no incumbent to inspect. Main's baseline assessment and
    # mutation narrative is already compiled into objective/deliverables/checks;
    # forwarding it again wastes the Worker's bounded context and can push an
    # otherwise valid assignment over the hard size ceiling.
    if mode != "baseline":
        if any(incumbent_assessment.values()):
            latest_feedback["main_agent_incumbent_assessment"] = incumbent_assessment
        if any(next_mutation.values()):
            latest_feedback["main_agent_next_mutation"] = next_mutation
    repair_targets = (
        latest_feedback.get("repair_targets")
        if isinstance(latest_feedback.get("repair_targets"), dict)
        else {}
    )
    if mode == "repair" and not repair_targets:
        raise ValueError(
            "repair assignment requires concrete repair_targets; a legal no-improvement result must end the "
            "attempt instead of generating an unconstrained objective-refinement patch"
        )
    if mode == "repair":
        latest_feedback["repair_contract"] = _build_repair_contract(
            target_file=target_file,
            repair_targets=repair_targets,
            evaluator_protocol=evaluator_protocol,
        )
    remaining_components = _remaining_component_ids(latest_feedback)
    repair_deliverables = _repair_deliverables(latest_feedback) if mode == "repair" else []
    if mode == "repair" and not repair_deliverables:
        raise ValueError(
            "repair assignment requires concrete compile/runtime/validator/mechanism-activation failures; "
            "semantic-review-only feedback cannot trigger a Coding Agent repair"
        )
    refinement_deliverables = (
        _objective_refinement_deliverables(latest_feedback)
        if is_objective_refinement
        else []
    )
    staged_baseline_deliverables = (
        _variant_agent_generated_baseline_deliverables(
            component_rows=component_rows,
            implementation_bundle=implementation_bundle,
        )
        if variant_baseline_package
        else _agent_generated_baseline_deliverables(
            baseline_trial=baseline_trial,
            high_flexibility=high_flex_baseline,
            active_features=runtime_feature_contract.get("active_features") or [],
        )
    )
    if repair_deliverables:
        implementation_order = [item["id"] for item in repair_deliverables]
    elif staged_baseline_deliverables:
        implementation_order = [item["id"] for item in staged_baseline_deliverables]
    elif remaining_components:
        implementation_order = [
            component_id for component_id in implementation_order if component_id in remaining_components
        ] or remaining_components
    elif refinement_deliverables:
        implementation_order = [item["id"] for item in refinement_deliverables]

    deliverables = repair_deliverables or staged_baseline_deliverables or refinement_deliverables or _assignment_deliverables(
        direction_plan=direction_plan,
        component_rows=component_rows,
        implementation_order=implementation_order,
    )
    read_set = _assignment_read_set(
        context=context,
        target_file=target_file,
        mode=mode,
        # 完整参考实现只用于从零构建，或语义审查明确发现组件缺失时的修补。
        # 普通改进轮必须围绕 incumbent 做单组件增量修改，避免重新抄写整套方法。
        include_implementation_asset=(mode == "baseline" or bool(remaining_components)),
        active_package=active_package,
        direction_plan=direction_plan,
        baseline_trial=baseline_trial,
        high_flexibility=high_flex_baseline,
    )
    implementation_skills = _assignment_implementation_skills(
        context,
        direction_plan=direction_plan,
        baseline_trial=baseline_trial,
        quality_active_features=runtime_feature_contract.get("active_features") or [],
    )
    if repair_deliverables:
        completion_rule = (
            "Eliminate every listed repair deliverable, compile the target, and pass JA plus the bounded smoke "
            "without rewriting unrelated working behavior."
        )
    elif staged_baseline_deliverables:
        completion_rule = (
            _variant_agent_generated_baseline_completion_rule(exact_rescue=baseline_exact_rescue)
            if variant_baseline_package
            else _agent_generated_baseline_completion_rule(baseline_trial or 1)
        )
    elif refinement_deliverables:
        completion_rule = (
            "Make one bounded same-direction objective refinement, compile, pass JA and the bounded smoke, "
            "and preserve the complete incumbent method. Core decides whether the result strictly improves."
        )
    else:
        completion_rule = str(
            implementation_bundle.get("completion_rule")
            or direction_plan.get("completion_rule")
            or "Complete every deliverable through reachable code, then pass the bounded checks."
        )[:1200]
    objective = str(
        direction_plan.get("worker_objective")
        or next_mutation.get("change")
        or direction_plan.get("hypothesis")
        or ""
    ).strip()[:1200]
    if repair_deliverables:
        objective = (
            "Repair only the blocking items in latest_feedback.repair_targets while preserving all unrelated "
            "working behavior in the current target file."
        )
    elif staged_baseline_deliverables:
        objective = (
            _variant_agent_generated_baseline_objective(exact_rescue=baseline_exact_rescue)
            if variant_baseline_package
            else _agent_generated_baseline_objective(baseline_trial or 1)
        )
    elif refinement_deliverables:
        objective = (
            "Refine only the current direction with one bounded objective-improvement edit. Preserve the legal "
            "incumbent and do not reimplement the complete Method Package."
        )
    assignment = WorkerAssignment(
        assignment_id=assignment_id,
        direction_id=direction_id,
        mode=mode,
        target_file=target_file,
        objective=objective,
        method_package={
            "package_id": str(active_package.get("package_id") or direction_plan.get("method_package_id") or ""),
            "implementation_asset": (
                active_package.get("implementation_asset")
                if (mode == "baseline" and baseline_trial != 1) or remaining_components
                else None
            ),
            "contract_paths": _unique_strings(
                _safe_read_path(path)
                for path in (
                    active_package.get("implementation_contract_assets")
                    or implementation_bundle.get("contract_paths")
                    or [active_package.get("implementation_contract_asset")]
                )
            ),
        },
        read_set=read_set,
        deliverables=deliverables,
        implementation_order=implementation_order,
        preserve=_unique_strings(
            [
                *(_strings(direction_plan.get("preserve"), limit=12)),
                *(_strings(next_mutation.get("preserve"), limit=12)),
                *(
                    ["Preserve all code unrelated to the listed repair or refinement deliverables."]
                    if repair_deliverables or refinement_deliverables
                    else []
                ),
            ]
        )[:12],
        forbidden=_unique_strings(
            [
                *(_strings(direction_plan.get("avoid"), limit=12)),
                *(str(item) for item in edit_policy.get("forbidden_paths") or []),
                "Do not choose a different method package or broaden this assignment.",
                "The standalone target must not import harness_agent, evaluator modules, or knowledge assets at runtime.",
                "Do not use previous solution files, fixed schedules, or target scores.",
                *(
                    [
                        "Never emit a schedule that the solver's complete local validator marked invalid; "
                        "an invalid or partial fallback must fail without writing candidate output."
                    ]
                    if baseline_trial == 1
                    else []
                ),
            ]
        )[:20],
        latest_feedback=latest_feedback,
        checks=_unique_strings(
            [
                "Compile the target solver once.",
                "Run at most one fixed-seed solver smoke with a time limit no greater than 3 seconds.",
                *(
                    [
                        "The CLI entrypoint must invoke the bounded exact solver before any failed constructive "
                        "path can exit. Defining a CP-SAT helper without a reachable call from main is incomplete.",
                        "The allowed smoke must exercise that exact entrypoint and emit diagnostics.cp_sat_called=true, "
                        "a JSON-native solver status, and positive observed variable, constraint, and interval counts."
                    ]
                    if baseline_exact_rescue
                    else []
                ),
                *(
                    [
                        "Eligible-machine choices are structured (machine_id, processing_time) pairs. Unpack the pair "
                        "before using the machine_id as a dictionary key or the processing_time as a duration."
                    ]
                    if baseline_trial is not None
                    else []
                ),
                *(
                    [
                        "Before every output write, require one complete schedule with exact coverage, eligibility, "
                        "durations, precedence, resource feasibility, and every active variant constraint validated."
                    ]
                    if baseline_trial == 1
                    else []
                ),
                *(
                    ["Every latest_feedback.repair_targets blocking item must be absent from the next JA result."]
                    if repair_deliverables
                    else []
                ),
                *(_strings(direction_plan.get("acceptance_checks"), limit=8)),
            ]
        )[:10],
        budgets={
            "max_edit_steps": max(1, int(max_steps)),
            "max_runtime_seconds": max(1, int(max_runtime_seconds)),
            "max_solver_smokes": 1,
            "max_solver_smoke_seconds": 3,
        },
        completion_rule=completion_rule,
        lineage={
            "parent_assignment_id": parent_assignment_id,
            "attempt_index": attempt_index,
            "round_index": round_index,
            "baseline_trial": baseline_trial,
            "track": str(
                (
                    direction_plan.get("worker_lane")
                    if isinstance(direction_plan.get("worker_lane"), dict)
                    else {}
                ).get("track_id")
                or (
                    direction_plan.get("worker_lane")
                    if isinstance(direction_plan.get("worker_lane"), dict)
                    else {}
                ).get("lane_role")
                or ""
            ),
            "stage": (
                direction_plan.get("worker_lane")
                if isinstance(direction_plan.get("worker_lane"), dict)
                else {}
            ).get("stage"),
            "parent_checkpoint": (
                direction_plan.get("worker_lane")
                if isinstance(direction_plan.get("worker_lane"), dict)
                else {}
            ).get("parent_checkpoint"),
            "verified_components": list(
                (
                    direction_plan.get("worker_lane")
                    if isinstance(direction_plan.get("worker_lane"), dict)
                    else {}
                ).get("verified_components")
                or []
            )[:32],
        },
        runtime_contract={
            "problem_family": (context.get("task") or {}).get("problem_family"),
            "solver_command_template": evaluator_protocol.get("solver_command_template"),
            "solution_format": evaluator_protocol.get("solution_format"),
            "solution_contract": evaluator_protocol.get("solution_contract") or {},
            "active_features": runtime_feature_contract.get("active_features") or [],
            "required_code_capabilities": quality_contract.get("required_code_capabilities") or [],
            "variant_required_code_capabilities": runtime_feature_contract.get("variant_required_code_capabilities") or [],
            "allowed_paths": edit_policy.get("allowed_paths") or [],
            "forbidden_paths": edit_policy.get("forbidden_paths") or [],
            "experiment_contract": {
                "stage": direction_plan.get("experiment_stage") or "probe",
                "activation_checks": (
                    []
                    if baseline_trial is not None and baseline_trial < 3
                    else _compact_activation_checks(direction_plan.get("activation_checks"))
                ),
                "falsification_metrics": _bounded_strings(
                    next_mutation.get("falsification_metrics"),
                    limit=8,
                    max_chars=240,
                ),
                "activation_path_root": "best_metrics.solver_evidence.diagnostics",
                "rule": (
                    "Core owns legality/objectives; required activation gates lineage/promotion."
                ),
            },
            **(
                {
                    "diagnostics_json_contract": {
                        "json_native_values_only": True,
                        "solver_status": (
                            "Convert solver status to solver.status_name(status), str, or int before JSON output."
                        ),
                        "forbidden_runtime_objects": [
                            "CpSolverStatus",
                            "IntVar",
                            "IntervalVar",
                            "numpy scalar",
                        ],
                    }
                }
                if str(direction_plan.get("method_family") or "") == "exact_hybrid"
                else {}
            ),
            **(
                {
                    "optional_solver_diagnostics": {
                        "location": "solution.json#/diagnostics",
                            "purpose": (
                                "Bounded Main evidence; never changes Core score, but declared required counters "
                                "gate lineage and promotion."
                            ),
                        "bounded_schema": {
                            "selected_source": "label",
                            "candidate_runs": "[{source,makespan,elapsed_ms}]",
                            "search_counters": "{expanded_states,retained_states,pruned_states,incumbent_path_survival}",
                            "timings_ms": "per rule/phase",
                            "distributions": "{profile_collisions,machine_shortlist}",
                        },
                        "limits": "64 runs, 256 layers, 128 distribution items; no raw schedules/states/traces.",
                    }
                }
                if baseline_trial is not None and baseline_trial >= 3
                else {}
            ),
        },
        implementation_skills=implementation_skills,
    )
    errors = assignment.validate()
    if errors:
        raise ValueError("invalid generated worker assignment: " + "; ".join(errors))
    serialized = json.dumps(assignment.to_payload(), ensure_ascii=False, indent=2)
    if len(serialized) > WORKER_ASSIGNMENT_MAX_CHARS:
        raise ValueError(
            f"worker assignment exceeds {WORKER_ASSIGNMENT_MAX_CHARS} chars: {len(serialized)}"
        )
    return assignment


def write_worker_assignment(path: Path, assignment: WorkerAssignment) -> Path:
    """校验并持久化任务书，作为 Worker 唯一规划输入。"""

    errors = assignment.validate()
    if errors:
        raise ValueError("invalid worker assignment: " + "; ".join(errors))
    text = json.dumps(assignment.to_payload(), ensure_ascii=False, indent=2) + "\n"
    if len(text) > WORKER_ASSIGNMENT_MAX_CHARS:
        raise ValueError(f"worker assignment exceeds {WORKER_ASSIGNMENT_MAX_CHARS} chars: {len(text)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _solver_target(context: dict[str, Any]) -> str:
    protocol = context.get("evaluator_protocol") if isinstance(context.get("evaluator_protocol"), dict) else {}
    explicit_target = _safe_read_path(protocol.get("worker_target_file"))
    if explicit_target:
        return explicit_target
    template = str(protocol.get("solver_command_template") or "")
    try:
        tokens = shlex.split(template, posix=False)
    except ValueError:
        tokens = template.split()
    for token in tokens:
        candidate = token.strip('"\'').replace("\\", "/")
        if candidate.lower().endswith(".py"):
            return candidate
    return "examples/agent_generated_fjsp_solver.py"


def _selected_method_package(
    context: dict[str, Any],
    direction_plan: dict[str, Any],
    *,
    active_features: list[str] | None = None,
) -> dict[str, Any]:
    active = context.get("active_method_package") if isinstance(context.get("active_method_package"), dict) else {}
    requested = str(direction_plan.get("method_package_id") or "").strip()
    if not requested:
        return active
    if active and str(active.get("package_id") or "") == requested:
        return active
    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    for item in catalog.get("packages") or []:
        if isinstance(item, dict) and str(item.get("package_id") or "") == requested:
            return item
    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    resolved = resolve_method_package(
        problem_family=str(task.get("problem_family") or ""),
        package_id=requested,
        active_features=(
            [str(item) for item in catalog.get("active_features") or []]
            or [str(item) for item in active_features or []]
        ),
        knowledge_query_tags=[
            str(item)
            for item in direction_plan.get("knowledge_query") or []
            if str(item).strip()
        ],
    )
    if resolved:
        return resolved
    raise ValueError(
        f"requested method package is not resolvable for this lane: {requested}"
    )


def _assignment_implementation_skills(
    context: dict[str, Any],
    *,
    direction_plan: dict[str, Any],
    baseline_trial: int | None = None,
    quality_active_features: list[str] | None = None,
) -> list[dict[str, Any]]:
    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    declared_families = direction_plan.get("method_families") or [direction_plan.get("method_family")]
    active_features = _unique_strings(
        list(catalog.get("active_features") or []) + list(quality_active_features or [])
    )
    selection = resolve_worker_implementation_skills(
        problem_family=str(task.get("problem_family") or ""),
        method_families=declared_families,
        active_features=active_features,
        knowledge_query_tags=[
            str(item)
            for item in direction_plan.get("knowledge_query") or []
            if str(item).strip()
        ],
    )
    audit = selection.get("audit") if isinstance(selection.get("audit"), dict) else {}
    requested = [str(item) for item in audit.get("requested_method_families") or [] if str(item).strip()]
    selected = [item for item in selection.get("method_families") or [] if isinstance(item, dict)]
    if requested and not selected:
        raise ValueError("no canonical method family was accepted for Worker Skill matching")
    uncovered = [str(item) for item in audit.get("uncovered_method_families") or [] if str(item).strip()]
    if uncovered:
        raise ValueError(
            "selected method families have no Worker Implementation Skill: " + ", ".join(uncovered)
        )
    result: list[dict[str, Any]] = []
    lane_policy = (
        direction_plan.get("worker_lane_policy")
        if isinstance(direction_plan.get("worker_lane_policy"), dict)
        else {}
    )
    delegated_improvement = (
        baseline_trial is None
        and lane_policy.get("mechanism_selection") == "delegated_to_worker"
    )
    specialized_baseline = bool(
        baseline_trial == 1 and str(direction_plan.get("method_package_id") or "").strip()
    )
    for item in selection.get("skills") or []:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip().lower()
        if not skill_id or any(row["skill_id"] == skill_id for row in result):
            continue
        if (
            baseline_trial == 1
            and skill_id != "fjsp-solver-foundation-worker"
            and not (item.get("always_include") and item.get("required_features"))
            and not (specialized_baseline and item.get("matched_method_families"))
        ):
            continue
        if baseline_trial == 2 and skill_id == "fjsp-experiment-design-worker":
            continue
        if delegated_improvement and skill_id in {
            "fjsp-solver-foundation-worker",
            "fjsp-experiment-design-worker",
        }:
            # The promoted incumbent already carries the foundation contract,
            # while Core owns evaluation. Fast lanes need only the selected
            # family Skill and any matching implementation playbook.
            continue
        result.append(
            {
                "skill_id": skill_id,
                "title": str(item.get("title") or skill_id)[:160],
                "method_families": _unique_strings(item.get("matched_method_families") or []),
                "sandbox_path": f".opencode/skills/{skill_id}",
                "required": True,
            }
        )
    return result[:8]


def _assignment_read_set(
    *,
    context: dict[str, Any],
    target_file: str,
    mode: str,
    include_implementation_asset: bool,
    active_package: dict[str, Any],
    direction_plan: dict[str, Any],
    baseline_trial: int | None = None,
    high_flexibility: bool = False,
) -> list[dict[str, Any]]:
    # ``target_file`` is always an explicitly authorized worker input.  During
    # baseline generation it may not exist yet, so the worker may create it;
    # later modes must inspect the promoted incumbent before editing it.
    rows: list[dict[str, Any]] = [
        {
            "path": target_file,
            "role": "target_file" if mode == "baseline" else "incumbent",
            "required": mode != "baseline",
        }
    ]
    evaluator_protocol = (
        context.get("evaluator_protocol")
        if isinstance(context.get("evaluator_protocol"), dict)
        else {}
    )
    lane_policy = (
        direction_plan.get("worker_lane_policy")
        if isinstance(direction_plan.get("worker_lane_policy"), dict)
        else {}
    )
    delegated_improvement = (
        mode == "improvement"
        and lane_policy.get("mechanism_selection") == "delegated_to_worker"
    )
    focused_retry = mode == "repair" or (
        baseline_trial is not None and baseline_trial > 1
    )
    for path in _unique_strings(evaluator_protocol.get("provided_project_read_paths") or [])[:200]:
        safe_path = _safe_read_path(path)
        if (
            (delegated_improvement or focused_retry)
            and safe_path
            and safe_path
            not in {
                ".algoforge_worker_runtime/run_smoke.py",
                ".algoforge_worker_runtime/smoke_config.json",
            }
        ):
            continue
        if safe_path and safe_path != target_file:
            rows.append(
                {
                    "path": safe_path,
                    "role": "provided_project_source",
                    "required": True,
                }
            )
    active_direction_knowledge = (
        context.get("active_direction_knowledge")
        if isinstance(context.get("active_direction_knowledge"), dict)
        else {}
    )
    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    package_catalog = (
        context.get("method_package_catalog")
        if isinstance(context.get("method_package_catalog"), dict)
        else {}
    )
    query = [str(item).strip().lower() for item in direction_plan.get("knowledge_query") or [] if str(item).strip()]
    direction_paths = list(active_direction_knowledge.get("paths") or [])
    if query:
        direction_paths = [
            str(path)
            for path in select_tagged_knowledge_cards(
                problem_family=str(task.get("problem_family") or ""),
                knowledge_query_tags=query,
                instance_diagnostics=(
                    context.get("instance_diagnostics")
                    if isinstance(context.get("instance_diagnostics"), dict)
                    else None
                ),
                active_features=[str(item) for item in package_catalog.get("active_features") or []],
            ).cards
        ]
    if include_implementation_asset and baseline_trial != 1:
        implementation_asset = _safe_read_path(active_package.get("implementation_asset"))
        if implementation_asset:
            rows.append({"path": implementation_asset, "role": "implementation", "required": True})
    contract_paths = _unique_strings(
        active_package.get("implementation_contract_assets")
        or [active_package.get("implementation_contract_asset")]
    )
    for path in contract_paths:
        safe_path = _safe_read_path(path)
        if safe_path:
            rows.append({"path": safe_path, "role": "contract", "required": True})
    if baseline_trial == 1:
        supporting_paths = []
    elif mode == "baseline":
        supporting_paths = [
            *direction_paths,
            *(active_package.get("assets") or []),
        ]
    else:
        # Worker needs the behavior contract and executable skeleton, not the
        # reviewer-only semantic rubric or package README.  Keep direction
        # cards because they carry the lane's concrete operator guidance.
        worker_semantic_assets = [
            path
            for path in active_package.get("semantic_assets") or []
            if not Path(str(path)).name.endswith("algorithm_semantic_review_contract.md")
        ]
        execution_skeletons = [
            path
            for path in active_package.get("assets") or []
            if Path(str(path)).name.endswith("_execution_skeleton.md")
        ]
        supporting_paths = [
            *worker_semantic_assets,
            *execution_skeletons,
            *direction_paths,
        ]
    implementation_asset = _safe_read_path(active_package.get("implementation_asset"))
    if delegated_improvement and str(active_package.get("package_id") or "").strip():
        authorized_direction_paths = {
            path
            for value in direction_paths
            if (path := _safe_read_path(value))
        }
        supporting_paths = [
            path
            for path in supporting_paths
            if Path(str(path)).name.endswith("_execution_skeleton.md")
            or _safe_read_path(path) in authorized_direction_paths
        ]
    for path in _unique_strings(supporting_paths or [])[:6]:
        safe_path = _safe_read_path(path)
        if (
            not safe_path
            or Path(safe_path).name.endswith("algorithm_semantic_review_contract.md")
            or (
                high_flexibility
                and baseline_trial is not None
                and Path(safe_path).name in HIGH_FLEX_BASELINE_REDUNDANT_CARDS
            )
            or (not include_implementation_asset and safe_path == implementation_asset)
            or any(item["path"] == safe_path for item in rows)
        ):
            continue
        rows.append({"path": safe_path, "role": "supporting_knowledge", "required": False})
    rows.append(
        {
            "path": ".algoforge_worker_inputs/manifest.json",
            "role": "instance_manifest",
            "required": True,
        }
    )
    if not focused_retry:
        rows.extend(_staged_instance_read_set(context))
    if not focused_retry and (
        mode == "baseline" or lane_policy.get("mechanism_selection") != "delegated_to_worker"
    ):
        rows.extend(_staged_document_read_set(context))
    if high_flexibility and baseline_trial is not None:
        rows = [
            item
            for item in rows
            if Path(str(item.get("path") or "")).name not in HIGH_FLEX_BASELINE_REDUNDANT_CARDS
        ]
    return _dedupe_read_set(rows)


def _dedupe_read_set(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge repeated staged/provided paths while preserving the stronger role."""

    result: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for raw in rows:
        path = str(raw.get("path") or "").strip().replace("\\", "/")
        if not path:
            continue
        row = {**raw, "path": path}
        position = positions.get(path)
        if position is None:
            positions[path] = len(result)
            result.append(row)
            continue
        existing = result[position]
        existing["required"] = bool(existing.get("required") or row.get("required"))
        if (
            existing.get("role") == "provided_project_source"
            and row.get("role") != "provided_project_source"
        ):
            existing["role"] = row.get("role")
    return result


def _staged_instance_read_set(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose the same first-instance mirror created by ``stage_worker_input_files``.

    The authoritative instance may live outside the repository and OpenCode's
    non-interactive permission policy cannot ask to read it.  The cycle stages
    one read-only copy under this deterministic path; the assignment must name
    that file explicitly or the worker can see the manifest but not its input.
    """

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    instances = [item for item in task.get("instances") or [] if isinstance(item, dict)]
    if not instances:
        return []
    first = instances[0]
    instance_id = str(first.get("id") or "instance")
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_id).strip("._") or "instance"
    suffix = Path(str(first.get("path") or "")).suffix or ".dat"
    return [
        {
            "path": f".algoforge_worker_inputs/instances/000_{safe_id}{suffix}",
            "role": "instance_sample",
            "required": True,
        }
    ]


def _staged_document_read_set(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Name the deterministic requirement/IO mirrors available to the Worker."""

    rows: list[dict[str, Any]] = []
    for index, document in enumerate(context.get("documents") or []):
        if not isinstance(document, dict):
            continue
        source_name = Path(str(document.get("path") or f"document_{index}.md")).name
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_name).strip("._") or f"document_{index}.md"
        rows.append(
            {
                "path": f".algoforge_worker_inputs/docs/{index:03d}_{safe_name}",
                "role": "requirement_or_io_contract",
                "required": True,
            }
        )
    return rows


def _safe_read_path(value: Any) -> str:
    """把仓库内绝对知识路径收敛为 Worker 沙箱中的相对路径。"""

    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(PROJECT_ROOT)
        except ValueError:
            return ""
    if ".." in path.parts:
        return ""
    return path.as_posix()


def _agent_generated_baseline_deliverables(
    *,
    baseline_trial: int | None,
    high_flexibility: bool,
    active_features: list[Any] | tuple[Any, ...] = (),
) -> list[dict[str, str]]:
    if baseline_trial == 1:
        features = {str(item).strip() for item in active_features if str(item).strip()}
        reentrant = bool(features.intersection({"reentrant_route", "loop_expansion"}))
        return [
            {
                "id": "parser_and_model",
                "behavior": (
                    "Use a two-phase parser: consume the complete standard FJSP body for all jobs first, "
                    "then consume exactly job_count trailing (loop_start, loop_end, repeat) triples. "
                    "Reject missing or trailing tokens and expand each route only after both phases complete."
                    if reentrant
                    else "Parse the assigned instance format into jobs, ordered operations, eligible machines, and durations."
                ),
                "evidence_required": (
                    "Before other optional work, run the one allowed smoke on the staged sample and prove that "
                    "all job rows remain aligned, exactly job_count loop triples are consumed, and expanded "
                    "operation coverage matches the contract."
                    if reentrant
                    else "The target parses the staged sample without importing harness or evaluator code."
                ),
            },
            {
                "id": "simple_legal_constructor",
                "behavior": "Build one complete deterministic legal schedule with a simple eligible-machine rule and precedence-safe decoding.",
                "evidence_required": "Every operation is scheduled once with eligible duration, precedence, and machine non-overlap.",
            },
            {
                "id": "cli_and_output",
                "behavior": "Implement the required CLI flags and emit the exact standalone solution JSON contract.",
                "evidence_required": "The fixed command writes a Core-readable complete solution.",
            },
            {
                "id": "deterministic_fallback",
                "behavior": "Always retain and emit the complete simple schedule when optional work reaches the deadline or fails.",
                "evidence_required": "A bounded run cannot exit without the best complete legal incumbent already constructed.",
            },
        ]
    if baseline_trial == 2 and high_flexibility:
        return [
            {
                "id": "earliest_gap",
                "behavior": "Upgrade decoding to earliest feasible machine-gap insertion; do not append blindly at the machine tail.",
                "evidence_required": "The reachable decoder tests internal gaps before tail placement.",
            },
            {
                "id": "operation_pressure",
                "behavior": "Use operation pressure exactly as (eligible_machine_count - 1) * duration_span.",
                "evidence_required": "The reachable priority computation uses the exact formula on each unscheduled operation.",
            },
            {
                "id": "exact_assignment_regret",
                "behavior": "After start-first scoring, use assignment_regret = current assignment cost - theoretical fastest processing time.",
                "evidence_required": "Regret is not the difference between the best and second-best complete score tuples.",
            },
            {
                "id": "low_pressure_order",
                "behavior": "Keep remaining-chain or equivalent sequence pressure for low-pressure operations.",
                "evidence_required": "Low-flexibility operations retain an explicit order-pressure term instead of being flattened by assignment scoring.",
            },
        ]
    if baseline_trial is not None and baseline_trial >= 3 and high_flexibility:
        return [
            {
                "id": "activation_telemetry",
                "behavior": "Add bounded diagnostics for pressure, exact regret, gap insertion, and fallback use without changing Core ranking.",
                "evidence_required": "Diagnostics prove reachable mechanism counts and remain optional, bounded output metadata.",
            },
            {
                "id": "mechanism_refinement",
                "behavior": "Refine only measured edge cases, tie breaks, deadline handling, and incumbent preservation around the Trial 2 mechanisms.",
                "evidence_required": "The legal Trial 2 solver remains the fallback and every refinement is reachable under the shared deadline.",
            },
        ]
    return []


def _variant_agent_generated_baseline_deliverables(
    *,
    component_rows: list[dict[str, Any]],
    implementation_bundle: dict[str, Any],
) -> list[dict[str, str]]:
    """Compile a feasibility-first baseline from a variant Method Package.

    Baseline generation establishes a legal anchor. Search components remain
    assigned to the formal family lanes, where the incumbent already exists.
    """

    dependencies = _component_dependency_map(implementation_bundle)
    component_ids = [
        str(item.get("component_id") or "").strip()
        for item in component_rows
        if str(item.get("component_id") or "").strip()
    ]
    root_ids = [component_id for component_id in component_ids if not dependencies.get(component_id)]
    by_id = {str(item.get("component_id") or ""): item for item in component_rows}
    variant_roots = []
    for component_id in root_ids:
        item = by_id.get(component_id, {})
        required_behaviors = _strings(item.get("required_behaviors"), limit=8)
        variant_roots.append(
            {
                "id": component_id,
                "behavior": " ".join(required_behaviors)[:800]
                or str(item.get("title") or component_id)[:800],
                "evidence_required": str(
                    item.get("evidence_required") or "Reachable variant parser, complete decoder, and legality guard."
                )[:800],
            }
        )
    return [
        {
            "id": "parser_cli_and_output",
            "behavior": (
                "Parse the complete active instance contract, implement the required CLI, and emit exactly the "
                "standalone solution JSON schema without importing Harness or evaluator code."
            ),
            "evidence_required": "The staged sample parses completely and the fixed command writes a Core-readable solution.",
        },
        *variant_roots,
        {
            "id": "verified_legal_incumbent",
            "behavior": (
                "Construct and retain one complete deterministic legal schedule before optional search. Validate exact "
                "coverage, eligibility, durations, precedence, machine resources, and every active variant constraint."
            ),
            "evidence_required": "The bounded Core smoke returns valid=true for one complete schedule.",
        },
        {
            "id": "fail_closed_output_guard",
            "behavior": (
                "Write output only from a complete locally validated incumbent. If construction, decoding, or fallback "
                "is invalid, partial, or times out before a legal incumbent exists, exit nonzero without writing it."
            ),
            "evidence_required": "No output path can serialize a locally invalid or partial schedule.",
        },
    ]


def _variant_agent_generated_baseline_objective(*, exact_rescue: bool = False) -> str:
    if exact_rescue:
        return (
            "Replace the failed constructive feasibility path with the smallest standalone exact-hybrid feasibility "
            "rescue. Post every active variant constraint in the real exact model, extract one complete Core-valid "
            "incumbent, emit JSON-native solver evidence, and fail closed when no feasible incumbent exists."
        )
    return (
        "Create the smallest standalone variant-aware solver that first produces one Core-valid deterministic "
        "incumbent. Implement only the Method Package dependency roots needed for legality; leave Beam, multistart, "
        "regret, local search, population search, and exact optimization to formal lanes."
    )


def _variant_agent_generated_baseline_completion_rule(*, exact_rescue: bool = False) -> str:
    if exact_rescue:
        return (
            "Stop after a bounded exact solve produces one complete locally validated incumbent and Core accepts it. "
            "The CLI entrypoint must actually call the exact solver; a defined but unreachable CP-SAT helper is not "
            "complete. The exact solver status and model counters must be JSON-native; no heuristic or partial "
            "fallback may be serialized as a valid solution."
        )
    return (
        "Stop as soon as the target compiles and Core validates one complete legal variant-aware incumbent. Never "
        "serialize a schedule after local validation fails, and do not add downstream Method Package search components."
    )


def _component_dependency_map(bundle: dict[str, Any]) -> dict[str, list[str]]:
    raw = bundle.get("component_dependencies")
    result: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for component_id, dependencies in raw.items():
            if str(component_id).strip() and isinstance(dependencies, (list, tuple)):
                result[str(component_id)] = _unique_strings(dependencies)
        return result
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or item.get("id") or "").strip()
        if component_id:
            result[component_id] = _unique_strings(
                item.get("depends_on") or item.get("dependencies") or []
            )
    return result


def _agent_generated_baseline_objective(baseline_trial: int) -> str:
    if baseline_trial <= 1:
        return "Create the smallest complete standalone legal solver: parser, simple construction, CLI/output, and deterministic fallback only."
    if baseline_trial == 2:
        return "Preserve the legal Trial 1 solver and add earliest-gap decoding, exact operation pressure, exact assignment regret, and low-pressure order priority."
    return "Preserve the best legal Trial 2 solver and add bounded activation telemetry plus measured, local mechanism refinements."


def _agent_generated_baseline_completion_rule(baseline_trial: int) -> str:
    if baseline_trial <= 1:
        return "Stop once the minimal solver compiles and Core can validate one complete legal fallback; do not add Beam, multi-start, telemetry, or local search."
    if baseline_trial == 2:
        return "Keep Trial 1 as fallback, compile, and expose the four exact high-flexibility construction mechanisms through reachable code."
    return "Keep the best legal parent as fallback, compile, and add only bounded telemetry and evidence-driven refinements before Core evaluation."


def _assignment_deliverables(
    *,
    direction_plan: dict[str, Any],
    component_rows: list[dict[str, Any]],
    implementation_order: list[str],
) -> list[dict[str, Any]]:
    declared = [item for item in direction_plan.get("deliverables") or [] if isinstance(item, dict)]
    if declared:
        remaining = set(implementation_order)
        declared_ids = {
            str(item.get("id") or item.get("component_id") or f"deliverable_{index}")
            for index, item in enumerate(declared[:20])
        }
        selected_ids = declared_ids.intersection(remaining)
        return [
            {
                "id": str(item.get("id") or item.get("component_id") or f"deliverable_{index}"),
                "behavior": str(item.get("behavior") or item.get("title") or "")[:800],
                "evidence_required": str(item.get("evidence_required") or "Reachable source and bounded check.")[:800],
            }
            for index, item in enumerate(declared[:20])
            if not selected_ids
            or str(item.get("id") or item.get("component_id") or f"deliverable_{index}") in selected_ids
        ]
    by_id = {str(item.get("component_id") or ""): item for item in component_rows}
    result = []
    for component_id in implementation_order:
        item = by_id.get(component_id, {})
        result.append(
            {
                "id": component_id,
                "behavior": str(item.get("title") or component_id)[:800],
                "evidence_required": str(item.get("evidence_required") or "Reachable source evidence.")[:800],
            }
        )
    if result:
        return result
    scoped = [
        {
            "id": f"scope_{index}",
            "behavior": value,
            "evidence_required": "Reachable source and fixed evaluator evidence.",
        }
        for index, value in enumerate(_strings(direction_plan.get("change_scope"), limit=8))
    ]
    if scoped:
        return scoped
    return [
        {
            "id": "assigned_objective",
            "behavior": str(
                direction_plan.get("worker_objective")
                or direction_plan.get("hypothesis")
                or "Implement the bounded direction under the active solver contract."
            )[:800],
            "evidence_required": "Reachable source and fixed evaluator evidence.",
        }
    ]


def _repair_deliverables(feedback: dict[str, Any]) -> list[dict[str, str]]:
    """Compile concrete runtime/contract failures into a minimal repair."""

    targets = feedback.get("repair_targets") if isinstance(feedback.get("repair_targets"), dict) else {}
    issues = _strings(targets.get("agentic_judgment_issues"), limit=8)
    suggestions = _strings(targets.get("agentic_judgment_suggestions"), limit=4)
    compile_errors = targets.get("python_compile_errors") if isinstance(targets.get("python_compile_errors"), dict) else {}
    rows: list[dict[str, str]] = []
    exact_failure = (
        targets.get("exact_execution_failure")
        if isinstance(targets.get("exact_execution_failure"), dict)
        else {}
    )
    if exact_failure:
        rows.append(
            {
                "id": "repair_exact_execution_not_exercised",
                "behavior": str(
                    exact_failure.get("required_evidence")
                    or "Wire the assigned bounded exact solver into the CLI entrypoint and run it."
                )[:800],
                "evidence_required": (
                    "The CLI cannot exit through the failed legacy constructor before calling the exact path, and the "
                    "bounded smoke reports diagnostics.cp_sat_called=true plus exact solver status/model/runtime evidence."
                ),
            }
        )
    mechanism_failure = (
        targets.get("mechanism_activation_failure")
        if isinstance(targets.get("mechanism_activation_failure"), dict)
        else {}
    )
    if mechanism_failure:
        failed_checks = [
            item
            for item in mechanism_failure.get("failed_checks") or []
            if isinstance(item, dict)
        ]
        check_summary = "; ".join(
            f"{item.get('id') or item.get('path')}: observed={item.get('observed')!r}, "
            f"required {item.get('operator')} {item.get('expected')!r}"
            for item in failed_checks[:8]
        )
        required_evidence = str(
            mechanism_failure.get("required_evidence")
            or "Execute the selected method family and emit its declared runtime activation counters."
        ).strip()
        rows.append(
            {
                "id": "repair_method_mechanism_activation",
                "behavior": (
                    f"Wire and execute the assigned method family from the real CLI path. {required_evidence} "
                    f"Failed checks: {check_summary or 'the declared required activation checks were not observed.'}"
                )[:800],
                "evidence_required": (
                    "Every required failed activation check must pass using newly observed runtime telemetry from "
                    "this candidate. Code presence, configuration, inherited incumbent quality, and another "
                    "method family's counters are not activation evidence."
                )[:800],
            }
        )
    concrete_errors = _error_strings(
        targets.get("result_revalidation_top_errors")
        or targets.get("diagnostic_smoke_top_errors"),
        limit=6,
    )
    for index, error in enumerate(concrete_errors):
        rows.append(
            {
                "id": f"repair_result_revalidation_{index:02d}",
                "behavior": f"Repair only this fixed parser/validator failure: {error}"[:800],
                "evidence_required": "The bounded result revalidation no longer reports this error."[:800],
            }
        )
    if compile_errors and not any("syntax_error" in item["id"] or "compile" in item["id"] for item in rows):
        rows.insert(
            0,
            {
                "id": "repair_python_compile_errors",
                "behavior": "Fix every reported Python compile error before making any semantic or objective change.",
                "evidence_required": "The assignment's single py_compile check exits successfully.",
            },
        )
    generic_issues = [
        issue
        for issue in issues
        if not (concrete_errors and issue == "candidate_result_revalidation_failed")
    ]
    for issue in generic_issues:
        repair_id = f"repair_{_safe_identifier(issue)[:80]}"
        guidance = f" Suggested evidence: {'; '.join(suggestions[:2])}" if suggestions else ""
        rows.append(
            {
                "id": repair_id,
                "behavior": f"Eliminate the deterministic JA issue `{issue}` with the smallest coherent edit.{guidance}"[:800],
                "evidence_required": f"The target compiles and JA no longer reports `{issue}`."[:800],
            }
        )
    return rows[:10]


def _error_strings(value: Any, *, limit: int) -> list[str]:
    rows = value if isinstance(value, list) else [value] if value else []
    errors: list[str] = []
    for item in rows:
        text = str(item.get("error") or "").strip() if isinstance(item, dict) else str(item or "").strip()
        if text and text not in errors:
            errors.append(text)
    return errors[: max(0, limit)]


def _build_repair_contract(
    *,
    target_file: str,
    repair_targets: dict[str, Any],
    evaluator_protocol: dict[str, Any],
) -> dict[str, Any]:
    """Lock a repair to concrete compile/runtime/validator evidence."""

    defect_ids = _unique_strings(
        [
            *(repair_targets.get("agentic_judgment_issues") or []),
            *("python_compile_errors" for _ in [0] if repair_targets.get("python_compile_errors")),
            *("result_revalidation_failure" for _ in [0] if repair_targets.get("result_revalidation_top_errors")),
            *("diagnostic_smoke_failure" for _ in [0] if repair_targets.get("diagnostic_smoke_top_errors")),
            *("exact_execution_failure" for _ in [0] if repair_targets.get("exact_execution_failure")),
            *("mechanism_activation_failure" for _ in [0] if repair_targets.get("mechanism_activation_failure")),
        ]
    )
    return {
        "allowed_paths": [target_file],
        "allowed_regions": [],
        "defect_ids": defect_ids,
        "input_contract": {
            "solver_command_template": evaluator_protocol.get("solver_command_template"),
            "rule": "Keep CLI flags and existing function signatures stable unless a listed source finding explicitly requires an interface change.",
        },
        "output_contract": {
            "solution_format": evaluator_protocol.get("solution_format"),
            "solution_contract": evaluator_protocol.get("solution_contract") or {},
            "rule": "Preserve complete legal output, operation identity, eligibility, duration, precedence, and non-overlap behavior.",
        },
        "scope_rule": (
            "Modify only allowed_paths and, when allowed_regions is non-empty, only the listed defect regions plus "
            "the minimum directly coupled caller/test lines. Do not tune unrelated parameters, rename interfaces, "
            "or rewrite the solver. Every changed line must be causally tied to one defect_id."
        ),
    }


def _objective_refinement_deliverables(feedback: dict[str, Any]) -> list[dict[str, str]]:
    """Keep legal-no-improvement retries to one edit instead of replaying a package."""

    if str(feedback.get("status") or "") != "refinement_required":
        return []
    return [
        {
            "id": "same_direction_objective_refinement",
            "behavior": (
                "Preserve the complete legal incumbent and make one bounded change inside the assigned direction "
                "that addresses the previous legal-but-not-better outcome. Do not rebuild unrelated components."
            ),
            "evidence_required": (
                "The target compiles, passes the bounded smoke and JA, and Core receives one legal candidate for "
                "strict objective comparison."
            ),
        }
    ]


def _assignment_feedback(loop_feedback: dict[str, Any], *, attempt_index: int) -> dict[str, Any]:
    if attempt_index <= 0:
        return {}
    current = loop_feedback.get("current_round_repair")
    if not isinstance(current, dict):
        return {}
    feedback = compact_current_round_repair(current)
    # Worker 只需要最新失败与剩余工作。旧 attempt 已由 Main 汇总，完整质量
    # 合同也已存在于 runtime_contract/read_set，重复携带会让 repair 任务书
    # 从数 KB 膨胀到数万字符。
    feedback.pop("previous_attempts", None)
    targets = feedback.get("repair_targets") if isinstance(feedback.get("repair_targets"), dict) else {}
    targets = dict(targets)
    if targets.pop("agent_generated_solver_expected_contract", None) is not None:
        targets["expected_contract_reference"] = (
            "Use runtime_contract.required_code_capabilities and variant_required_code_capabilities."
        )
    feedback["repair_targets"] = targets
    return feedback


def _remaining_component_ids(feedback: dict[str, Any]) -> list[str]:
    targets = feedback.get("repair_targets") if isinstance(feedback.get("repair_targets"), dict) else {}
    semantic = targets.get("algorithm_semantic_review") if isinstance(targets.get("algorithm_semantic_review"), dict) else {}
    return _unique_strings(
        [
            item.get("component_id")
            for item in semantic.get("implementation_coverage") or []
            if isinstance(item, dict) and item.get("status") != "implemented"
        ]
    )


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "direction"


def _strings(value: Any, *, limit: int) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return _unique_strings(values)[: max(0, limit)]


def _bounded_strings(value: Any, *, limit: int, max_chars: int) -> list[str]:
    return [item[:max_chars] for item in _strings(value, limit=limit)]


def _compact_activation_checks(value: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()[:300]
        if not path:
            continue
        expected = item.get("expected", item.get("value"))
        if isinstance(expected, str):
            expected = expected[:240]
        elif isinstance(expected, list):
            expected = [
                value[:120] if isinstance(value, str) else value
                for value in expected[:16]
                if isinstance(value, (str, int, float, bool, type(None)))
            ]
        elif isinstance(expected, dict):
            expected = {
                str(key)[:80]: value[:120] if isinstance(value, str) else value
                for key, value in list(expected.items())[:16]
                if isinstance(value, (str, int, float, bool, type(None)))
            }
        elif not isinstance(expected, (int, float, bool, type(None))):
            expected = str(expected)[:240]
        checks.append(
            {
                "id": str(item.get("id") or f"activation_{len(checks) + 1}")[:80],
                "path": path,
                "operator": str(item.get("operator") or "exists")[:16],
                "expected": expected,
                "required": item.get("required") is not False,
                "description": str(item.get("description") or "")[:300],
            }
        )
        if len(checks) >= 8:
            break
    return checks


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def compact_current_round_repair(value: dict[str, Any]) -> dict[str, Any]:
    """把修补证据压成完整剩余清单，避免通用字符压缩截断组件尾部。"""

    result = {
        key: value.get(key)
        for key in (
            "status",
            "allow_objective_refinement",
            "attempt_index",
            "max_repair_attempts",
            "baseline_trial",
            "resume_incomplete_baseline",
            "baseline_feasibility_rescue",
            "must_do",
            "avoid",
        )
        if value.get(key) not in (None, "", [], {})
    }
    targets = dict(value.get("repair_targets") or {}) if isinstance(value.get("repair_targets"), dict) else {}
    semantic = (
        targets.get("algorithm_semantic_review")
        if isinstance(targets.get("algorithm_semantic_review"), dict)
        else {}
    )
    if semantic:
        targets["algorithm_semantic_review"] = {
            "status": semantic.get("status"),
            "summaries": (semantic.get("summaries") or [])[:4],
            "blocking_findings": [
                {
                    key: item.get(key)
                    for key in (
                        "finding_id",
                        "category",
                        "source_path",
                        "line_start",
                        "line_end",
                        "knowledge_path",
                        "repair",
                        "required_test",
                    )
                    if item.get(key) not in (None, "")
                }
                for item in semantic.get("blocking_findings") or []
                if isinstance(item, dict)
            ],
            "implementation_coverage": [
                compact_component_repair_coverage(item)
                for item in semantic.get("implementation_coverage") or []
                if isinstance(item, dict)
            ],
            "coupled_group_coverage": [
                {
                    "group_id": item.get("group_id"),
                    "status": item.get("status"),
                    "missing_behavior": str(item.get("missing_behavior") or "")[:500],
                }
                for item in semantic.get("coupled_group_coverage") or []
                if isinstance(item, dict)
            ],
            "knowledge_paths": semantic.get("knowledge_paths") or [],
        }
    anchor = (
        targets.get("baseline_core_valid_anchor")
        if isinstance(targets.get("baseline_core_valid_anchor"), dict)
        else {}
    )
    if anchor:
        anchor_semantic = (
            anchor.get("semantic_review")
            if isinstance(anchor.get("semantic_review"), dict)
            else {}
        )
        targets["baseline_core_valid_anchor"] = {
            "attempt_index": anchor.get("attempt_index"),
            "candidate_key": anchor.get("candidate_key") or [],
            "semantic_review": {
                "status": anchor_semantic.get("status"),
                "accepted": anchor_semantic.get("accepted"),
            },
            "rule": str(anchor.get("rule") or "")[:500],
        }
    result["repair_targets"] = targets
    result["previous_attempts"] = [
        compact_previous_repair_attempt(item)
        for item in value.get("previous_attempts") or []
        if isinstance(item, dict)
    ]
    return result


def compact_component_repair_coverage(value: dict[str, Any]) -> dict[str, Any]:
    behavior_rows = [
        item
        for item in value.get("behavior_coverage") or []
        if isinstance(item, dict) and item.get("status") != "implemented"
    ]
    return {
        "component_id": value.get("component_id"),
        "status": value.get("status"),
        "missing_behavior_indexes": [item.get("behavior_index") for item in behavior_rows],
        "missing_behaviors": [str(item)[:500] for item in value.get("missing_behaviors") or []],
    }


def compact_previous_repair_attempt(value: dict[str, Any]) -> dict[str, Any]:
    semantic = value.get("semantic_review") if isinstance(value.get("semantic_review"), dict) else {}
    return {
        key: value.get(key)
        for key in (
            "attempt_index",
            "worker_status",
            "changed_files",
            "failure_signatures",
            "candidate_key",
            "summary",
        )
        if value.get(key) not in (None, "", [], {})
    } | (
        {
            "semantic_review": {
                "status": semantic.get("status"),
                "accepted": semantic.get("accepted"),
                "summary": str(semantic.get("summary") or "")[:500],
            }
        }
        if semantic
        else {}
    )
