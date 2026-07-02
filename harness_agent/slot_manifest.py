from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodeSlotSpec:
    slot_id: str
    title: str
    target_file: str
    marker_start: str
    marker_end: str
    slot_kind: str
    language: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    invariants: list[str]
    allowed_edits: list[str]
    forbidden_edits: list[str]
    validation_commands: list[str] = field(default_factory=list)
    knowledge_tags: list[str] = field(default_factory=list)
    user_confirmed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SlotManifest:
    schema_version: int
    problem_family: str
    status: str
    slots: list[CodeSlotSpec]
    confirmation_required: bool = True
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "problem_family": self.problem_family,
            "status": self.status,
            "confirmation_required": self.confirmation_required,
            "notes": self.notes,
            "slots": [slot.to_payload() for slot in self.slots],
        }


def default_standard_fjsp_slot_manifest(*, confirmed: bool = False) -> SlotManifest:
    slots = [
        CodeSlotSpec(
            slot_id="awls_zi_policy",
            title="AWLS 自适应 zi 权重策略",
            target_file="examples/awls_evolved_slots.py",
            marker_start="# EVOLVE_START",
            marker_end="# EVOLVE_END",
            slot_kind="function_body",
            language="python",
            purpose="控制 AWLS 邻域动作打分中的 zi 数值扰动策略。",
            inputs=[
                "values['base']：固定 AWLS 外壳传入的基础 zi 分数",
                "values['weight']：操作级自适应权重",
                "values['cooldown']：操作冷却/时间信号",
                "values['rr'], values['gamma'], values['cooling']",
                "values['is_critical'], values['forward'], values['backward']",
                "values['duration'], values['machine_load'], values['position']",
                "SDST-aware values may include values['setup_prev'], values['setup_next'], values['setup_adjacent'], and setup ratio/critical-neighbor flags",
            ],
            outputs=["返回有限的非负 float；外层 wrapper 会裁剪不安全数值。"],
            invariants=[
                "函数名必须保持 evolved_zi(values)。",
                "禁止 import、subprocess、文件 IO、随机数、网络访问或读取评测器。",
                "不得改变 solver 的输入/输出 schema。",
            ],
            allowed_edits=[
                "只改写 EVOLVE 标记内部的 evolved_zi 函数体。",
                "允许使用算术、本地变量、values.get(...)、if/else 和白名单数值函数。",
            ],
            forbidden_edits=[
                "禁止修改 parser、evaluator 或 benchmark 文件。",
                "禁止修改解 JSON schema。",
                "禁止修改 AWLS 图结构/状态数据结构。",
            ],
            validation_commands=[
                "python -m compileall examples/awls_evolved_slots.py examples/standard_fjsp_awls_solver.py",
                "python examples/standard_fjsp_awls_solver.py --input examples/fjsp.brandimarte.Mk01.m6j10c3.txt --output outputs/slot_smoke.json --zi-policy slot --time-limit-sec 1",
            ],
            knowledge_tags=["awls", "zi", "adaptive_weight", "move_scoring", "zi_features"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="local_search_neighborhood_actions",
            title="局部搜索邻域动作生成",
            target_file="examples/standard_fjsp_local_search_solver.py",
            marker_start="# SLOT neighborhood_actions START",
            marker_end="# SLOT neighborhood_actions END",
            slot_kind="marked_block",
            language="python",
            purpose="为已解码的标准 FJSP 排程生成可验证的候选改进动作。",
            inputs=[
                "instance：固定 StandardFjspInstance",
                "state：当前机器分配和机器序列",
                "decoded：当前排程、makespan、前驱后继和拓扑顺序",
                "rng：带 seed 的随机源",
                "neighbor_limit：候选动作数量上限",
            ],
            outputs=["返回有界的 Move 对象列表，必须兼容 apply_move/decode_state。"],
            invariants=[
                "不得改变 Move 字段或 SearchState/DecodedState schema。",
                "所有动作必须仍可被 decode_state 和 validate_standard_schedule 检查。",
                "不得修改 evaluator、parser 或 IO 契约。",
            ],
            allowed_edits=[
                "允许在标记槽内新增或调整 move generator。",
                "允许使用上下文中已有的关键路径/关键块、机器负载、空闲间隙和候选机器信号。",
            ],
            forbidden_edits=[
                "禁止改变 benchmark/evaluator 语义。",
                "禁止改变命令行参数或解输出 schema。",
                "禁止创建无界候选列表或非确定性外部副作用。",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_local_search_solver.py",
                "python examples/standard_fjsp_local_search_solver.py --input examples/fjsp.brandimarte.Mk01.m6j10c3.txt --output outputs/neighborhood_slot_smoke.json --time-limit-sec 1",
            ],
            knowledge_tags=["critical_path", "critical_block", "neighborhood", "machine_reassignment"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_initialization",
            title="AWLS-SDST setup-aware greedy initialization",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_initialization START",
            marker_end="# SLOT awls_sdst_initialization END",
            slot_kind="marked_block",
            language="python",
            purpose="Build better initial AWLS machine sequences for SDST instances while preserving the fixed parser/evaluator contract.",
            inputs=[
                "index: OperationIndex with instance, candidates, node_to_job/node_to_op, and job_to_nodes",
                "rng: random.Random used for deterministic seeded tie-breaking",
                "random_factor and idle_bonus diversification parameters",
                "index.instance.has_sequence_dependent_setup and setup_time_between when setup-aware scoring is needed",
                "setup_time_between must be imported from harness_agent.standard_fjsp inside the slot if used",
                "Operation keys are (index.node_to_job[node], index.node_to_op[node]); pass index as the op_index mapping",
            ],
            outputs=[
                "sequences: list[list[int]] assigning every operation node exactly once to a machine sequence",
                "on_machine: list[int] mapping every real node to its selected machine",
                "Result must be accepted by AwlsSchedule and validate_standard_schedule after timing propagation",
            ],
            invariants=[
                "Keep greedy_gt_init signature unchanged.",
                "Schedule each operation exactly once and respect job operation order in candidate release.",
                "If attempting non-append insertion, preserve a valid permutation per machine and keep on_machine consistent for every real node.",
                "If non-append insertion commits into an existing machine sequence, guard against disjunctive-graph cycles before returning an AwlsSchedule.",
                "Keep standard FJSP behavior close to the current greedy initializer when no SDST data exists.",
                "Keep all replacement lines indented inside greedy_gt_init; the slot is a function body.",
                "If setup_time_between is used, call setup_time_between(index.instance, machine_id, previous_op, current_op, index).",
                "Do not modify random_init, build_initial_schedule, parser, evaluator, solution schema, CLI arguments, or benchmark semantics.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_initialization markers.",
                "May add local helper functions or lambdas inside the slot.",
                "May use setup-aware completion, setup load, projected load, and seeded tie-breaking.",
                "May use bounded non-append insertion into a candidate machine sequence if the local ready-state bookkeeping remains deterministic and every operation is scheduled once.",
                "May test candidate sequences with AwlsSchedule/topological feasibility locally before accepting a non-append insertion.",
                "May use bottleneck-machine, critical-tail, or remaining-work estimates as bounded construction pressure.",
                "May import setup_time_between locally inside the slot.",
            ],
            forbidden_edits=[
                "Do not create helper files for setup parsing or initialization.",
                "Do not import setup_time_between from examples.standard_fjsp_awls_solver; use harness_agent.standard_fjsp.",
                "Do not call setup_time_between with separate job/op integer arguments.",
                "Do not retry plain append-only setup-aware earliest-completion, low-setup tie-breaking, fixed small RCL, or tail-aware append scoring unchanged.",
                "Do not retry static single-bottleneck priority that ignores setup, tail pressure, and dynamic readiness.",
                "Do not directly commit sequences[machine].insert(...) and then rebuild global job_ready for already scheduled operations without a cycle/topology guard.",
                "Do not emit unindented top-level code in this function-body slot.",
                "Do not change AWLS timing propagation, N7/NK move scoring, zi policy, parser, evaluator, or benchmark semantics.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_standard_fjsp_awls_alignment tests.test_awls_slot_mode -v",
            ],
            knowledge_tags=["awls", "sdst", "setup_time", "initialization", "dispatching", "quality"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_same_machine_evaluation",
            title="AWLS-SDST setup-aware same-machine N7 scoring",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_same_machine_evaluation START",
            marker_end="# SLOT awls_sdst_same_machine_evaluation END",
            slot_kind="marked_block",
            language="python",
            purpose="Rank AWLS same-machine critical-block moves with setup-aware cost information while leaving candidate generation and validation fixed.",
            inputs=[
                "schedule: AwlsSchedule with setup-aware forward/end/backward path lengths",
                "move: Move whose method is FRONT or BACK on the same machine",
                "gamma: adaptive-weight perturbation scale",
                "local_sequence_after_same_machine_move(schedule, move)",
                "setup_time_between from harness_agent.standard_fjsp if setup-aware local estimates are needed",
                "Operation keys are (schedule.index.node_to_job[node], schedule.index.node_to_op[node]); pass schedule.index as the op_index mapping",
            ],
            outputs=[
                "A numeric same-machine move score where smaller is preferred by find_move",
                "No mutation of schedule or global state",
            ],
            invariants=[
                "Keep same_machine_evaluate_stable signature unchanged.",
                "Move method values are string constants FRONT and BACK.",
                "AwlsSchedule has no setup_time(...) method and OperationIndex has no setup_time(...) method.",
                "If setup_time_between is used, call setup_time_between(schedule.index.instance, machine_id, previous_op, current_op, schedule.index).",
                "Never pass node ids directly to setup_time_between; convert nodes to operation keys first.",
                "Do not change change_machine_evaluate_parts, same_machine_evaluate_cpp_fast, zi policy, parser, evaluator, or benchmark semantics.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_same_machine_evaluation markers.",
                "May add local helper functions or setup-aware propagation inside the local scoring block.",
                "May clone and apply a move for exact local makespan if errors are handled locally.",
                "May import setup_time_between locally inside this slot.",
            ],
            forbidden_edits=[
                "Do not create helper files for setup parsing or move evaluation.",
                "Do not call schedule.setup_time(...) or schedule.index.setup_time(...); these APIs do not exist.",
                "Do not change AWLS timing propagation, change-machine scoring, zi policy, parser, evaluator, or benchmark semantics.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_standard_fjsp_awls_alignment tests.test_awls_slot_mode -v",
            ],
            knowledge_tags=["awls", "sdst", "setup_time", "same_machine", "n7_neighborhood", "quality"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_move_evaluation",
            title="AWLS-SDST setup-aware change-machine NK scoring",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_move_evaluation START",
            marker_end="# SLOT awls_sdst_move_evaluation END",
            slot_kind="marked_block",
            language="python",
            purpose=(
                "Rank AWLS change-machine NK moves with setup-aware cost information "
                "while leaving candidate generation, move application, and validation fixed."
            ),
            inputs=[
                "schedule: AwlsSchedule with setup-aware forward/end/backward path lengths",
                "method: string constant CHANGE_MACHINE_FRONT or CHANGE_MACHINE_BACK",
                "which: operation node being reassigned to another machine",
                "where: destination-machine anchor node used by the current insertion method",
                "intersection_first/intersection_last from change_machine_intersection/window",
                "gamma: adaptive-weight perturbation scale",
                "weight_perturbation(schedule, which, gamma)",
                "Move(method, which, where) is available if exact scoring clones are needed",
                "setup_time_between from harness_agent.standard_fjsp if setup-aware arc deltas are needed",
                "Operation keys are (schedule.index.node_to_job[node], schedule.index.node_to_op[node]); pass schedule.index as the op_index mapping",
            ],
            outputs=[
                "A numeric change-machine move score where smaller is preferred by find_move",
                "For zi_policy cpp/cpp-exact, preserve cpp_int_score(...) wrapping of the final score",
                "No mutation of schedule, tabu state, machine sequences, or evaluator state",
            ],
            invariants=[
                "Keep change_machine_evaluate_parts signature unchanged.",
                "Move method values are string constants, not integers.",
                "AwlsSchedule has no setup_time(...) method and OperationIndex has no setup_time(...) method.",
                "If setup_time_between is used, call setup_time_between(schedule.index.instance, machine_id, previous_op, current_op, schedule.index).",
                "Never call setup_time_between with current_op=None; missing predecessor/successor edges contribute zero setup.",
                "Use trial.makespan after trial.apply_move(Move(method, which, where)) if exact scoring is attempted.",
                "Do not change same-machine scoring, neighborhood selection, zi policy, parser, evaluator, solution schema, or benchmark semantics.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_move_evaluation markers.",
                "May preserve the legacy AWLS proxy for standard FJSP and add SDST-only setup-aware penalties or exact scoring.",
                "May add local helper functions inside the slot.",
                "May import setup_time_between locally inside this slot.",
                "May clone schedule and apply a single Move for exact SDST scoring if errors are caught locally.",
            ],
            forbidden_edits=[
                "Do not create helper files for setup parsing or move evaluation.",
                "Do not call schedule.setup_time(...) or schedule.index.setup_time(...); these APIs do not exist.",
                "Do not compare method constants with integer values.",
                "Do not use LB/UB or setup_time alone as the objective; fixed evaluator score remains makespan.",
                "Do not modify AWLS timing propagation, same-machine scoring, neighborhood selection, zi policy, parser, evaluator, or benchmark semantics.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_standard_fjsp_awls_alignment tests.test_awls_slot_mode -v",
            ],
            knowledge_tags=[
                "awls",
                "sdst",
                "setup_time",
                "move_scoring",
                "nk_neighborhood",
                "change_machine",
                "quality",
            ],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_zi_features",
            title="AWLS-SDST setup-aware zi feature extraction",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_zi_features START",
            marker_end="# SLOT awls_sdst_zi_features END",
            slot_kind="marked_block",
            language="python",
            purpose=(
                "Expose bounded setup-aware numeric features to AWLS zi formula/slot policies "
                "without changing the fixed evaluator, parser, or default AWLS policies."
            ),
            inputs=[
                "schedule: AwlsSchedule with machine links, criticality, forward/backward times, and OperationIndex",
                "node: real operation node currently being scored by weight_perturbation",
                "values: dict[str, float] already containing base, weight, cooldown, rr, gamma, cooling, is_critical, forward, backward, duration, machine_load, and position",
                "operation_key(schedule, node) converts AWLS node ids to (job_id, op_id) tuples",
                "setup_time_between from harness_agent.standard_fjsp if setup-aware features are used",
                "schedule.index.instance.has_sequence_dependent_setup tells whether SDST setup data exists",
            ],
            outputs=[
                "Mutate values only by adding finite numeric feature entries",
                "Expected setup feature keys: setup_prev, setup_next, setup_adjacent, setup_prev_ratio, setup_next_ratio, setup_adjacent_ratio, setup_is_sdst, setup_predecessor_critical, setup_successor_critical",
                "Return flow must continue to build_zi_feature_values and then formula/slot evaluation",
            ],
            invariants=[
                "Keep build_zi_feature_values and weight_perturbation signatures unchanged.",
                "Do not change cpp, aggressive, critical, sqrt, or none zi-policy behavior; this slot only enriches formula/slot values.",
                "If setup_time_between is used, call setup_time_between(schedule.index.instance, machine_id, previous_op, current_op, schedule.index).",
                "Never pass node ids directly to setup_time_between; convert nodes with operation_key.",
                "Never call setup_time_between with current_op=None; missing predecessor/successor contributes zero setup.",
                "Do not read LB/UB, evaluator output, instance files, environment variables, network, or filesystem state.",
                "Do not mutate schedule, machine sequences, tabu state, parser, evaluator, solution schema, or benchmark semantics.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_zi_features markers.",
                "May add local bounded setup feature calculations and numeric ratios inside the slot.",
                "May use schedule.index.instance.has_sequence_dependent_setup to keep standard FJSP values at zero.",
                "May catch local lookup errors and fall back to zero setup features.",
            ],
            forbidden_edits=[
                "Do not create helper files or parallel setup parsers.",
                "Do not change formula validation outside the documented setup feature names unless a separate platform change is made.",
                "Do not make makespan, LB/UB, or setup_time alone the objective.",
                "Do not call trial.apply_move or run local search inside this feature slot.",
                "Do not add randomness, subprocesses, multiprocessing, network calls, or file IO.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_standard_fjsp_awls_alignment tests.test_awls_slot_mode tests.test_slot_manifest_platform -v",
            ],
            knowledge_tags=["awls", "sdst", "zi", "zi_features", "setup_time", "quality"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_portfolio_search_control",
            title="AWLS-SDST portfolio lane search control",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_portfolio_search_control START",
            marker_end="# SLOT awls_sdst_portfolio_search_control END",
            slot_kind="marked_block",
            language="python",
            purpose=(
                "Control how explicit AWLS portfolio lanes are budgeted, ordered, "
                "and selected for SDST instances without changing schedule semantics."
            ),
            inputs=[
                "portfolio_lanes: list[PortfolioLane] parsed from CLI/benchmark as seed:init:restarts[:seconds]",
                "time_limit_sec: global AWLS wall-clock cap for this solver call",
                "seed: outer benchmark seed used to offset lane seeds with PORTFOLIO_OUTER_SEED_STRIDE",
                "index: fixed OperationIndex built from parse_standard_fjsp output",
                "solve_awls_single(...) and format_awls_stats(...) from this module",
                "All AWLS controls already passed to solve_awls: cycles_per_restart, iterations, beta/gamma/theta, exact_select_top_k, same_machine_eval, critical_block_exhaustive_pct, zi_policy, zi_formula, initial_state, time_check_interval",
            ],
            outputs=[
                "best: AwlsSchedule clone selected from legal lane runs by lowest makespan",
                "best_lane: PortfolioLane describing the selected effective lane",
                "lane_summaries: list[str] preserving per-lane diagnostics for the strategy label",
                "No change to returned ScheduleRecord schema or benchmark score semantics",
            ],
            invariants=[
                "Keep solve_awls signature unchanged.",
                "Only execute when portfolio_lanes is non-empty; non-portfolio AWLS path must remain unchanged.",
                "Keep score objective as makespan; do not use LB/UB or setup_time as the objective.",
                "Every lane must still call solve_awls_single or an equivalent existing AWLS path that returns AwlsSchedule.",
                "Preserve deterministic effective_lane_seed = lane.seed + seed * PORTFOLIO_OUTER_SEED_STRIDE unless the replacement explicitly documents an equivalent deterministic mapping.",
                "Do not mutate parser, evaluator, solution JSON schema, CLI argument names, or benchmark semantics.",
                "Keep lane_summaries informative enough to audit selected seed/init/restarts/time/makespan.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_portfolio_search_control markers.",
                "May change lane ordering, per-lane budget allocation, early-stop policy, or tie-breaking among equal makespans.",
                "May add local bounded diagnostics or helper lists inside the slot.",
                "May adapt search-control decisions for instance.has_sequence_dependent_setup while preserving standard FJSP legality.",
            ],
            forbidden_edits=[
                "Do not parse instance files, setup matrices, LB/UB tables, or evaluator output in this slot.",
                "Do not change parse_portfolio_lanes format or allocate_lane_budgets unless a separate slot is confirmed.",
                "Do not change solve_awls_single, AWLS move scoring, zi formula validation, parser, evaluator, or output schema.",
                "Do not skip validation by returning records directly from the slot.",
                "Do not retry seed-mapping-only perturbations without a real lane budget, order, early-stop, or tie-breaking change.",
                "Do not add unbounded loops, multiprocessing, subprocesses, network access, file IO, or randomness outside existing seeded AWLS calls.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_benchmark_suite tests.test_awls_slot_mode tests.test_slot_manifest_platform -v",
            ],
            knowledge_tags=["awls", "sdst", "portfolio", "search_control", "quality"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_neighborhood_selection",
            title="AWLS-SDST critical-block neighborhood candidate selection",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_neighborhood_selection START",
            marker_end="# SLOT awls_sdst_neighborhood_selection END",
            slot_kind="marked_block",
            language="python",
            purpose="Generate a bounded legal set of same-machine and change-machine AWLS moves from critical blocks and critical operations.",
            inputs=[
                "schedule: AwlsSchedule with machine_sequences, on_machine, end_time, backward_path_length, and makespan",
                "best_makespan, tabu, iteration, gamma, exact_select_top_k, critical_block_exhaustive_pct from find_move",
                "Local closures consider_same(...) and consider_change(...)",
                "critical_blocks(...), change_machine_window(...), and schedule.index.candidates",
                "Processing time lookup uses schedule.index.duration(node, machine_id); OperationIndex has no durations attribute.",
            ],
            outputs=[
                "Populate all_moves, ranked_moves, best_moves, and best_value only through consider_same and consider_change",
                "Leave final move selection, exact top-k recheck, fallback, and Move construction to unchanged code after the marker",
                "No direct mutation of schedule, tabu, machine sequences, or evaluator state",
            ],
            invariants=[
                "Keep find_move signature unchanged.",
                "Do not return from inside this slot except through existing all_moves flow after the marker.",
                "Do not directly append to move containers; call consider_same or consider_change.",
                "Move method values are string constants FRONT, BACK, CHANGE_MACHINE_FRONT, and CHANGE_MACHINE_BACK.",
                "Only pass real operation nodes; never pass START_NODE or schedule.index.end_node.",
                "Do not access nonexistent OperationIndex fields such as schedule.index.durations.",
                "Do not modify parser, evaluator, solution schema, CLI arguments, or benchmark semantics.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_neighborhood_selection markers.",
                "May change the order and subset of critical blocks explored.",
                "May add near-critical operation filters using end_time + backward_path_length close to makespan.",
                "May add bounded same-machine or alternate-machine insertion candidates through existing closures.",
                "May bias exploration for SDST instances by setup-heavy arcs while preserving closure-based legality.",
            ],
            forbidden_edits=[
                "Do not create helper files for setup parsing or neighborhood selection.",
                "Do not call trial.apply_move directly in this slot.",
                "Do not change the objective from makespan or use LB/UB as a score.",
                "Do not touch harness_agent.standard_fjsp or examples/standard_fjsp_evaluator.py.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_standard_fjsp_awls_alignment tests.test_awls_slot_mode -v",
            ],
            knowledge_tags=["awls", "sdst", "critical_block", "n7_neighborhood", "nk_neighborhood", "candidate_generation", "quality"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="awls_sdst_move_selection",
            title="AWLS-SDST final move selection and exact recheck",
            target_file="examples/standard_fjsp_awls_solver.py",
            marker_start="# SLOT awls_sdst_move_selection START",
            marker_end="# SLOT awls_sdst_move_selection END",
            slot_kind="marked_block",
            language="python",
            purpose=(
                "Select the final AWLS Move after same-machine/change-machine candidates "
                "have been generated, including exact top-k rechecks and bounded tie-breaking."
            ),
            inputs=[
                "schedule: AwlsSchedule with current machine/job arcs, makespan, rng, and setup-aware timing",
                "all_moves: list of candidate move keys collected only through consider_same/consider_change",
                "ranked_moves: optional list[(approx_value, move_key)] when exact_select_top_k > 0",
                "best_moves and best_value: approximate best candidate set from remember_candidate",
                "exact_select_top_k, best_makespan, Move, and schedule.rng from find_move scope",
                "setup_time_between from harness_agent.standard_fjsp if SDST tie-breaking needs setup lookup",
            ],
            outputs=[
                "Return Move or None exactly as find_move expects",
                "No direct mutation of schedule, tabu, machine sequences, parser, evaluator, or output schema",
                "Any exact check must use schedule.clone(), trial.apply_move(Move(...)), and trial.makespan",
            ],
            invariants=[
                "Keep find_move signature unchanged.",
                "If all_moves is empty, return None.",
                "Use makespan as the primary exact objective; setup_time may only be a bounded tie-breaker.",
                "Do not bypass already-applied tabu filtering from remember_candidate.",
                "Do not change candidate generation; this slot runs after candidate collection.",
                "Do not access nonexistent schedule.setup_time, schedule.index.setup_time, or schedule.index.durations APIs.",
                "Do not modify parser, evaluator, solution schema, CLI arguments, or benchmark semantics.",
            ],
            allowed_edits=[
                "Only rewrite code between awls_sdst_move_selection markers.",
                "May change exact top-k candidate ordering, tie-breaking, and bounded random diversification.",
                "May adjust how best_moves/all_moves are sampled when approximate scores tie.",
                "May add SDST-only local helper functions inside the slot.",
                "May clone schedule and apply candidate moves for bounded exact rechecks with local exception handling.",
                "May import setup_time_between locally inside the slot for setup-aware tie-breakers.",
            ],
            forbidden_edits=[
                "Do not append new moves or call consider_same/consider_change in this slot.",
                "Do not mutate schedule directly or call trial.apply_move without cloning first.",
                "Do not use LB/UB, evaluator reports, files, subprocesses, multiprocessing, network, or environment variables.",
                "Do not optimize setup_time instead of makespan.",
                "Do not add unbounded loops over repeated local search; keep rechecks bounded by existing candidate lists.",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py",
                "python -m unittest tests.test_standard_fjsp_awls_alignment tests.test_awls_slot_mode tests.test_slot_manifest_platform -v",
            ],
            knowledge_tags=[
                "awls",
                "sdst",
                "move_selection",
                "search_control",
                "move_scoring",
                "setup_time",
                "quality",
            ],
            user_confirmed=confirmed,
        ),
    ]
    return SlotManifest(
        schema_version=1,
        problem_family="standard_fjsp",
        status="confirmed" if confirmed else "draft_requires_user_confirmation",
        confirmation_required=not confirmed,
        notes=[
            "代码槽是带明确输入、输出和不变量的功能编辑区域。",
            "LLM 获准修改代码槽之前，必须先经过用户确认。",
            "除非确认新的 IO 契约，否则 evaluator/parser/metric 语义保持固定。",
        ],
        slots=slots,
    )


def load_slot_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def selected_standard_fjsp_slot_manifest(*, selected_slot_ids: list[str]) -> SlotManifest:
    selected = {str(slot_id) for slot_id in selected_slot_ids if str(slot_id).strip()}
    if not selected:
        raise ValueError("at least one selected slot_id is required")
    manifest = default_standard_fjsp_slot_manifest(confirmed=False)
    known = {slot.slot_id for slot in manifest.slots}
    unknown = sorted(selected - known)
    if unknown:
        raise ValueError(f"unknown standard_fjsp slot_id(s): {', '.join(unknown)}")
    slots = [
        CodeSlotSpec(
            **{
                **slot.to_payload(),
                "user_confirmed": slot.slot_id in selected,
            }
        )
        for slot in manifest.slots
    ]
    return SlotManifest(
        schema_version=manifest.schema_version,
        problem_family=manifest.problem_family,
        status="confirmed",
        confirmation_required=False,
        notes=manifest.notes
        + [
            "Only selected slots have user_confirmed=true; unselected slots remain locked.",
        ],
        slots=slots,
    )


def write_default_slot_manifest(*, problem_family: str, output: Path, confirmed: bool = False) -> Path:
    normalized_family = str(problem_family).strip().lower()
    if normalized_family not in {"fjsp", "standard_fjsp"}:
        raise ValueError(f"no default slot manifest is available for problem family: {problem_family}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = default_standard_fjsp_slot_manifest(confirmed=confirmed).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def write_selected_slot_manifest(*, problem_family: str, output: Path, selected_slot_ids: list[str]) -> Path:
    normalized_family = str(problem_family).strip().lower()
    if normalized_family not in {"fjsp", "standard_fjsp"}:
        raise ValueError(f"no default slot manifest is available for problem family: {problem_family}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = selected_standard_fjsp_slot_manifest(selected_slot_ids=selected_slot_ids).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
