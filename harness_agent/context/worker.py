"""面向 Coding Worker 的上下文视图与优先级整理。"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.context.knowledge import (
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
    mode = "repair" if attempt_index > 0 else "baseline" if round_index < 0 else "improvement"
    assignment_id = f"{_safe_identifier(direction_id)}-a{attempt_index:02d}"
    target_file = _solver_target(context)
    active_package = _selected_method_package(context, direction_plan)
    implementation_bundle = (
        direction_plan.get("implementation_bundle")
        if isinstance(direction_plan.get("implementation_bundle"), dict)
        else {}
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
    latest_feedback = _assignment_feedback(loop_feedback, attempt_index=attempt_index)
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
            "repair assignment requires concrete compile/runtime/validator failures; semantic-review-only "
            "feedback cannot trigger a Coding Agent repair"
        )
    refinement_deliverables: list[dict[str, str]] = []
    if repair_deliverables:
        implementation_order = [item["id"] for item in repair_deliverables]
    elif remaining_components:
        implementation_order = [
            component_id for component_id in implementation_order if component_id in remaining_components
        ] or remaining_components
    elif refinement_deliverables:
        implementation_order = [item["id"] for item in refinement_deliverables]

    deliverables = repair_deliverables or refinement_deliverables or _assignment_deliverables(
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
    )
    implementation_skills = _assignment_implementation_skills(context, direction_plan=direction_plan)
    if repair_deliverables:
        completion_rule = (
            "Eliminate every listed repair deliverable, compile the target, and pass JA plus the bounded smoke "
            "without rewriting unrelated working behavior."
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
                if mode == "baseline" or remaining_components
                else None
            ),
            "contract_paths": _unique_strings(
                active_package.get("implementation_contract_assets")
                or implementation_bundle.get("contract_paths")
                or [active_package.get("implementation_contract_asset")]
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
            ]
        )[:20],
        latest_feedback=latest_feedback,
        checks=_unique_strings(
            [
                "Compile the target solver once.",
                "Run at most one fixed-seed solver smoke with a time limit no greater than 3 seconds.",
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
        },
        runtime_contract={
            "problem_family": (context.get("task") or {}).get("problem_family"),
            "solver_command_template": evaluator_protocol.get("solver_command_template"),
            "solution_format": evaluator_protocol.get("solution_format"),
            "solution_contract": evaluator_protocol.get("solution_contract") or {},
            "active_features": quality_contract.get("active_features") or [],
            "required_code_capabilities": quality_contract.get("required_code_capabilities") or [],
            "variant_required_code_capabilities": quality_contract.get("variant_required_code_capabilities") or [],
            "allowed_paths": edit_policy.get("allowed_paths") or [],
            "forbidden_paths": edit_policy.get("forbidden_paths") or [],
            "experiment_contract": {
                "stage": direction_plan.get("experiment_stage") or "probe",
                "activation_checks": _compact_activation_checks(direction_plan.get("activation_checks")),
                "falsification_metrics": _bounded_strings(
                    next_mutation.get("falsification_metrics"),
                    limit=8,
                    max_chars=240,
                ),
                "activation_path_root": "best_metrics.solver_evidence.diagnostics",
                "rule": (
                    "The candidate is inconclusive unless required activation_checks are observable in solution diagnostics."
                ),
            },
            **(
                {
                    "optional_solver_diagnostics": {
                        "location": "solution.json#/diagnostics",
                        "purpose": "Bounded Main evidence only; never affects JA/Core score, ranking, or promotion.",
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
                if attempt_index == 0
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


def _selected_method_package(context: dict[str, Any], direction_plan: dict[str, Any]) -> dict[str, Any]:
    active = context.get("active_method_package") if isinstance(context.get("active_method_package"), dict) else {}
    requested = str(direction_plan.get("method_package_id") or "").strip()
    if active and (not requested or str(active.get("package_id") or "") == requested):
        return active
    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    for item in catalog.get("packages") or []:
        if isinstance(item, dict) and str(item.get("package_id") or "") == requested:
            return item
    return active


def _assignment_implementation_skills(
    context: dict[str, Any],
    *,
    direction_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    catalog = context.get("method_package_catalog") if isinstance(context.get("method_package_catalog"), dict) else {}
    declared_families = direction_plan.get("method_families") or [direction_plan.get("method_family")]
    selection = resolve_worker_implementation_skills(
        problem_family=str(task.get("problem_family") or ""),
        method_families=declared_families,
        active_features=[str(item) for item in catalog.get("active_features") or []],
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
    for item in selection.get("skills") or []:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip().lower()
        if not skill_id or any(row["skill_id"] == skill_id for row in result):
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
    if include_implementation_asset:
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
    if mode == "baseline":
        supporting_paths = [
            *direction_paths,
            *(active_package.get("assets") or []),
        ]
    else:
        # 改进和修补只需要行为契约及本轮选中的知识。package.assets 往往包含
        # 完整 reference_solver；即使 implementation_asset 未显式加入，也不能
        # 通过 supporting_knowledge 旁路重新进入 Worker 上下文。
        supporting_paths = [
            *(active_package.get("semantic_assets") or []),
            *direction_paths,
        ]
    implementation_asset = _safe_read_path(active_package.get("implementation_asset"))
    for path in _unique_strings(supporting_paths or [])[:6]:
        safe_path = _safe_read_path(path)
        if (
            not safe_path
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
    rows.extend(_staged_instance_read_set(context))
    rows.extend(_staged_document_read_set(context))
    return rows


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


def _assignment_deliverables(
    *,
    direction_plan: dict[str, Any],
    component_rows: list[dict[str, Any]],
    implementation_order: list[str],
) -> list[dict[str, Any]]:
    declared = [item for item in direction_plan.get("deliverables") or [] if isinstance(item, dict)]
    if declared:
        remaining = set(implementation_order)
        return [
            {
                "id": str(item.get("id") or item.get("component_id") or f"deliverable_{index}"),
                "behavior": str(item.get("behavior") or item.get("title") or "")[:800],
                "evidence_required": str(item.get("evidence_required") or "Reachable source and bounded check.")[:800],
            }
            for index, item in enumerate(declared[:20])
            if not remaining
            or str(item.get("id") or item.get("component_id") or f"deliverable_{index}") in remaining
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
        for key in ("status", "attempt_index", "max_repair_attempts", "must_do", "avoid")
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
