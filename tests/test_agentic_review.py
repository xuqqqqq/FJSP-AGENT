from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agentic_review import (
    _detect_agent_generated_source_self_check_risks,
    _detect_agent_generated_output_schema_mismatch_risks,
    _has_machine_non_overlap_guard,
    _has_operation_coverage_guard,
    _has_operation_level_ready_list_constructor,
    analyze_rejected_judgment,
    judge_worker_result,
)
from harness_agent.solver_quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.worker import WorkerResult


class AgenticReviewQualityContractTests(unittest.TestCase):
    def test_timeout_with_compilable_diff_continues_to_core_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text("VALUE = 1\n", encoding="utf-8")
            context_path = root / "context_packet.json"
            context_path.write_text(
                json.dumps(
                    {
                        "task": {"problem_family": "FJSP", "description": "incremental solver edit"},
                        "evaluator_protocol": {"solver_command_template": "python examples/solver.py"},
                        "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": [".git", "outputs"]},
                    }
                ),
                encoding="utf-8",
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="timeout",
                    changed_files=["examples/solver.py"],
                    summary="Timed out after writing and compiling the edit.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=True,
            )

        self.assertTrue(judgment.accepted, judgment.issues)
        self.assertIn("worker_timeout_after_code_change", judgment.checks["proposal_audit_warnings"])

    def test_timeout_without_diff_remains_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context_path = root / "context_packet.json"
            context_path.write_text("{}", encoding="utf-8")

            judgment = judge_worker_result(
                worker_result=WorkerResult(status="timeout", changed_files=[], summary="No edit."),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=True,
            )

        self.assertFalse(judgment.accepted)
        self.assertIn("worker_status_not_usable: timeout", judgment.issues)

    def test_agent_generated_sdst_solver_without_decoder_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(
                "\n".join(
                    [
                        "def solve(jobs):",
                        "    schedule = []",
                        "    machine_ready = {}",
                        "    for job_id, job in enumerate(jobs):",
                        "        job_ready = 0",
                        "        for op_id, op in enumerate(job):",
                        "            machine_id, duration = op['candidates'][0]",
                        "            start = max(job_ready, machine_ready.get(machine_id, 0))",
                        "            end = start + duration",
                        "            schedule.append({'job_id': job_id, 'op_id': op_id, 'machine_id': machine_id, 'start': start, 'end': end})",
                        "            job_ready = end",
                        "            machine_ready[machine_id] = end",
                        "    return schedule",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            context_path = _write_context(root, sdst=True)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Weak generated solver.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("setup-aware" in item for item in risks))
            contract = judgment.checks["agent_generated_solver_quality_contract"]
            self.assertIn("sequence_dependent_setup", contract["active_features"])

    def test_agent_generated_sdst_solver_with_decoder_contract_passes_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_strong_agent_generated_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver skeleton.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.issues)
            self.assertEqual([], judgment.checks["agent_generated_solver_quality_risks"])

    def test_agent_generated_proposal_without_self_check_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_strong_agent_generated_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            proposal_path = _write_proposal(root, solver_contract_self_check=None)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver skeleton without proposal self-check.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            self.assertTrue(judgment.checks["agent_generated_solver_self_check_risks"])
            analysis = analyze_rejected_judgment(judgment=judgment, output_dir=root / "analysis")
            self.assertTrue(
                any("Agent-generated solver self-check risks" in item for item in analysis.diagnosis)
            )
            self.assertTrue(
                any("Expected agent-generated solver contract" in item for item in analysis.diagnosis)
            )

    def test_agent_generated_proposal_with_self_check_passes_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_strong_agent_generated_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            proposal_path = _write_proposal(root, solver_contract_self_check=_complete_solver_self_check())

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver skeleton with proposal self-check.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.issues)
            self.assertEqual([], judgment.checks["agent_generated_solver_self_check_risks"])

    def test_agent_generated_bare_schedule_writer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace(
                "Path(args.output).write_text(json.dumps(solution), encoding='utf-8')",
                "with Path(args.output).open('w', encoding='utf-8') as handle:\n        json.dump(solution['schedule'], handle)",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(root, solver_contract_self_check=_complete_solver_self_check(), content=source)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated solver writes a bare schedule list.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("declared_output_schema_mismatch" in item for item in risks))

    def test_agent_generated_result_object_writer_is_not_bare_schedule(self) -> None:
        source = """
def write_solution(output_path, schedule):
    result = {
        "format": "standard_fjsp_schedule_v1",
        "schedule": schedule,
        "makespan": max(row["end"] for row in schedule),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
"""
        self.assertEqual([], _detect_agent_generated_output_schema_mismatch_risks(source))

    def test_agent_generated_machine_major_decoder_precedence_claim_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _machine_major_decoder_solver_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(root, solver_contract_self_check=_complete_solver_self_check(), content=source)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated solver replays machine sequences in machine-major order.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("job_precedence_guard_mismatch" in item for item in risks))

    def test_agent_generated_standard_solver_variable_names_pass_quality_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_solver_with_compact_variable_names_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver uses compact variable names but has the required structure.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.checks["agent_generated_solver_quality_risks"])
            self.assertEqual([], judgment.checks["agent_generated_solver_quality_risks"])

    def test_agent_generated_standard_solver_iterator_parser_ready_list_is_not_missing_base_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_solver_with_iterator_parser_ready_list_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver uses token iterator parser and ready-list construction.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            risks = judgment.checks["agent_generated_solver_quality_risks"]
            missing_base = [item for item in risks if "missing base capabilities" in item]
            self.assertEqual([], missing_base)

    def test_agent_generated_random_machine_ready_list_gets_specific_repair_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_random_machine_ready_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver randomly chooses an eligible machine for a ready operation.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertFalse(judgment.accepted)
            self.assertTrue(
                any("random_machine_choice_without_ready_machine_evaluation" in item for item in risks),
                risks,
            )
            missing_base = [item for item in risks if "missing base capabilities" in item]
            self.assertTrue(any("operation_level_ready_list_constructor" in item for item in missing_base), risks)
            self.assertFalse(any("processing_duration_guard" in item for item in missing_base), risks)
            repair_plan = judgment.checks["agent_generated_solver_repair_plan"]
            self.assertTrue(any("ready-choice loop" in item for item in repair_plan["must_add"]))
            self.assertTrue(any("rng.choice" in item for item in repair_plan["must_not"]))

    def test_ready_list_detector_accepts_m_id_candidate_loop(self) -> None:
        source = """
def initial_ready_list_state(instance, rng):
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < instance["operation_count"]:
        best_candidate = None
        best_finish = float("inf")
        for job_id, next_op in job_next_op.items():
            if next_op >= job_op_counts[job_id]:
                continue
            op_key = (job_id, next_op)
            for m_id in op_info[op_key]["eligible"]:
                proc = op_info[op_key]["eligible"][m_id]["proc"]
                start = max(job_ready[job_id], machine_ready[m_id])
                finish = start + proc
                if finish < best_finish:
                    best_candidate = (op_key, m_id)
                    best_finish = finish
        op_key, m_id = best_candidate
        assignment[op_key] = m_id
        machine_sequences[m_id].append(op_key)
        job_next_op[op_key[0]] += 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_op_info_eligible_items_loop(self) -> None:
        source = """
def initial_ready_list_state(instance):
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < instance["operation_count"]:
        ready_ops = [(j, op) for j, op in job_next_op.items() if op < job_op_counts[j]]
        best_choices = []
        best_finish = None
        for op_key in ready_ops:
            job_id, _ = op_key
            for machine_id, duration in op_info[op_key]["eligible"].items():
                finish = max(job_ready[job_id], machine_ready[machine_id]) + duration
                if best_finish is None or finish < best_finish:
                    best_finish = finish
                    best_choices = [(op_key, machine_id, finish)]
        op_key, machine_id, finish = min(best_choices, key=lambda item: item[2])
        assignment[op_key] = machine_id
        machine_sequences[machine_id].append(op_key)
        job_next_op[op_key[0]] += 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_source_self_check_follows_coverage_helper_and_sorted_interval_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(
                """
def coverage_ok(instance, schedule):
    seen = {(item["job_id"], item["op_id"]) for item in schedule}
    return len(schedule) == instance["operation_count"] and seen == set(instance["op_info"])

def validate_schedule(instance, schedule):
    if not coverage_ok(instance, schedule):
        return False
    by_machine = {}
    for item in schedule:
        op_key = (item["job_id"], item["op_id"])
        machine_id = item["machine_id"]
        duration = instance["op_info"].get(op_key, {}).get("eligible", {}).get(machine_id)
        if duration is None or item["end"] - item["start"] != duration:
            return False
        by_machine.setdefault(machine_id, []).append((item["start"], item["end"]))
    for intervals in by_machine.values():
        intervals.sort()
        for left, right in zip(intervals, intervals[1:]):
            if left[1] > right[0]:
                return False
    return True

def solve(instance, schedule):
    if not validate_schedule(instance, schedule):
        raise RuntimeError("invalid")
""",
                encoding="utf-8",
            )
            risks = _detect_agent_generated_source_self_check_risks(
                worktree_path=root,
                changed_files=["examples/agent_generated_fjsp_solver.py"],
                quality_contract={
                    "required_code_capabilities": [
                        "complete_schedule_coverage_guard",
                        "machine_eligibility_guard",
                        "processing_duration_guard",
                        "machine_non_overlap_guard",
                    ],
                    "variant_required_code_capabilities": [],
                },
            )

        self.assertEqual([], risks)

    def test_coverage_detector_accepts_seen_expected_return(self) -> None:
        source = """
def validate_schedule(instance, schedule):
    op_info = instance["op_info"]
    expected = set(op_info.keys())
    seen = set()
    for rec in schedule:
        key = (rec["job_id"], rec["op_id"])
        if key in seen or key not in expected:
            return False
        seen.add(key)
    return seen == expected
"""
        self.assertTrue(_has_operation_coverage_guard(source))

    def test_coverage_detector_accepts_unscheduled_and_total_ops_guard(self) -> None:
        source = """
def decode_schedule(assignment, machine_sequences, instance):
    total_ops = instance["operation_count"]
    unscheduled = set(op for op in assignment.keys())
    scheduled = set()
    schedule = []
    while unscheduled:
        progress = False
        for op in list(unscheduled):
            schedule.append(op)
            scheduled.add(op)
            unscheduled.remove(op)
            progress = True
        if not progress:
            break
    if unscheduled:
        return None
    if len(schedule) != total_ops:
        return None
    return schedule
"""
        self.assertTrue(_has_operation_coverage_guard(source))

    def test_coverage_detector_accepts_scheduled_expected_set_guard(self) -> None:
        source = """
def validate_schedule(instance, schedule):
    expected = set(instance["op_info"].keys())
    scheduled = set()
    for op in schedule:
        key = (op["job_id"], op["op_id"])
        if key in scheduled:
            return False
        scheduled.add(key)
    if scheduled != expected:
        return False
    return True
"""
        self.assertTrue(_has_operation_coverage_guard(source))

    def test_ready_list_detector_accepts_generic_candidates_finish_scoring(self) -> None:
        source = """
def build_initial_schedule(op_info, job_op_counts, seed):
    rng = random.Random(seed)
    machine_ready = [0] * n_machines
    job_ready = [0] * len(job_op_counts)
    next_op = [0] * len(job_op_counts)
    assignment = {}
    machine_sequences = defaultdict(list)
    for step in range(sum(job_op_counts)):
        candidates = []
        for job_id, ops_count in enumerate(job_op_counts):
            if next_op[job_id] < ops_count:
                op_id = next_op[job_id]
                op_key = (job_id, op_id)
                for m, dur in op_info[op_key]:
                    start = max(job_ready[job_id], machine_ready[m])
                    finish = start + dur
                    candidates.append((finish, job_id, op_id, m, dur, start))
        min_f = min(c[0] for c in candidates)
        best_candidates = [c for c in candidates if c[0] == min_f]
        rng.shuffle(best_candidates)
        chosen = best_candidates[0]
        assignment[(chosen[1], chosen[2])] = chosen[3]
        machine_sequences[chosen[3]].append((chosen[1], chosen[2]))
        next_op[chosen[1]] += 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_candidates_machine_duration_map(self) -> None:
        source = """
def initial_ready_list_state(instance, seed):
    rng = random.Random(seed)
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < instance["operation_count"]:
        candidates = []
        for job_id, next_op_id in list(job_next_op.items()):
            if next_op_id >= job_op_counts[job_id]:
                continue
            op_key = (job_id, next_op_id)
            for mach_id in op_info[op_key]["candidates"]:
                proc = op_info[op_key]["machines"][mach_id]
                start = max(job_ready[job_id], machine_ready[mach_id])
                finish = start + proc
                candidates.append((finish, job_id, next_op_id, mach_id, start))
        cand_seeded = [
            (finish, job_id, op_id, mach_id, start, rng.random())
            for (finish, job_id, op_id, mach_id, start) in candidates
        ]
        cand_seeded.sort(key=lambda item: (item[0], item[5]))
        finish, job_id, op_id, mach_id, start, _ = cand_seeded[0]
        assignment[(job_id, op_id)] = mach_id
        machine_sequences[mach_id].append((job_id, op_id))
        job_next_op[job_id] = op_id + 1
        job_ready[job_id] = finish
        machine_ready[mach_id] = finish
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_local_candidates_items_loop(self) -> None:
        source = """
def build_initial_state(instance, seed):
    rng = random.Random(seed)
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < instance["operation_count"]:
        ready_ops = []
        for job_id in list(job_op_counts.keys()):
            if job_next_op[job_id] < job_op_counts[job_id]:
                ready_ops.append((job_id, job_next_op[job_id]))
        best_candidates = []
        best_value = None
        for op_key in ready_ops:
            job_id, op_id = op_key
            candidates = op_info[op_key]["candidates"]
            for m_id, proc in candidates.items():
                start = max(job_ready[job_id], machine_ready[m_id])
                finish = start + proc
                if best_value is None or finish < best_value:
                    best_value = finish
                    best_candidates = [(op_key, m_id, finish)]
                elif finish == best_value:
                    best_candidates.append((op_key, m_id, finish))
        chosen = rng.choice(best_candidates)
        op_key, m_id, finish = chosen
        assignment[op_key] = m_id
        machine_sequences[m_id].append(op_key)
        job_next_op[op_key[0]] += 1
        job_ready[op_key[0]] = finish
        machine_ready[m_id] = finish
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_key_eligible_items_loop(self) -> None:
        source = """
def initial_ready_list_state(instance, rng):
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < instance["operation_count"]:
        candidates = []
        for j in job_op_counts:
            op_id = job_next_op[j]
            if op_id < job_op_counts[j]:
                key = (j, op_id)
                for m, dur in op_info[key]["eligible"].items():
                    start = max(job_ready[j], machine_ready[m])
                    finish = start + dur
                    candidates.append((finish, start, m, dur, key, j, op_id))
        best_finish = min(c[0] for c in candidates)
        best_cands = [c for c in candidates if c[0] == best_finish]
        _, start, machine, dur, key, j, op_id = rng.choice(best_cands)
        assignment[key] = machine
        machine_sequences[machine].append(key)
        job_next_op[j] = op_id + 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_processing_times_items_loop(self) -> None:
        source = """
def construct_schedule(instance, rng):
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    total_ops = instance["total_ops"]
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < total_ops:
        ready_ops = []
        for job_id, next_op_id in job_next_op.items():
            if next_op_id < job_op_counts[job_id]:
                ready_ops.append((job_id, next_op_id))
        candidates = []
        for job_id, op_id in ready_ops:
            op_key = (job_id, op_id)
            for machine_id, duration in op_info[op_key]["processing_times"].items():
                start = max(job_ready[job_id], machine_ready[machine_id])
                end = start + duration
                candidates.append((end, machine_id, op_key, start))
        candidates.sort(key=lambda x: (x[0], x[1], x[2][0], x[2][1]))
        min_end = candidates[0][0]
        best_candidates = [c for c in candidates if c[0] == min_end]
        end, machine_id, op_key, start = rng.choice(best_candidates)
        assignment[op_key] = machine_id
        machine_sequences[machine_id].append(op_key)
        job_next_op[op_key[0]] += 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_local_eligible_machine_loop(self) -> None:
        source = """
def construct_assignment(instance, rng):
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    assignment = {}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    schedule = []
    while len(assignment) < instance["operation_count"]:
        ready = [(j, job_next_op[j]) for j in job_op_counts if job_next_op[j] < job_op_counts[j]]
        candidates = []
        for op_key in ready:
            eligible = op_info[op_key]
            jr = job_ready[op_key[0]]
            for m in eligible:
                start = max(jr, machine_ready[m])
                finish = start + eligible[m]
                candidates.append((finish, start, m, op_key))
        rng.shuffle(candidates)
        best = min(candidates, key=lambda x: (x[0], x[1], x[2], x[3]))
        _, _, chosen_m, op_key = best
        assignment[op_key] = chosen_m
        job_next_op[op_key[0]] += 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_ready_list_detector_accepts_info_eligible_duration_map(self) -> None:
        source = """
def construct_solution(instance, seed):
    op_info = instance["op_info"]
    job_op_counts = instance["job_op_counts"]
    rng = random.Random(seed)
    assignment = {}
    machine_sequences = {m: [] for m in range(instance["machine_count"])}
    job_next_op = {j: 0 for j in job_op_counts}
    job_ready = {j: 0 for j in job_op_counts}
    machine_ready = {m: 0 for m in range(instance["machine_count"])}
    while len(assignment) < instance["operation_count"]:
        ready_ops = []
        for job_id, next_op_idx in job_next_op.items():
            if next_op_idx < job_op_counts[job_id]:
                ready_ops.append((job_id, next_op_idx))
        candidates = []
        for op_key in ready_ops:
            info = op_info[op_key]
            job_id, op_id = op_key
            ready_time = job_ready[job_id]
            for m_id in info["eligible"]:
                proc_time = info["durations"][m_id]
                start = max(ready_time, machine_ready[m_id])
                finish = start + proc_time
                candidates.append((finish, op_key, m_id))
        best_finish = min(item[0] for item in candidates)
        best_candidates = [item for item in candidates if item[0] == best_finish]
        _, op_key, m_id = rng.choice(best_candidates)
        assignment[op_key] = m_id
        machine_sequences[m_id].append(op_key)
        job_next_op[op_key[0]] += 1
"""
        self.assertTrue(_has_operation_level_ready_list_constructor(source))

    def test_non_overlap_detector_accepts_machine_sequence_end_start_guard(self) -> None:
        source = """
def decode_schedule(assignment, machine_sequences, instance):
    start_times = {}
    end_times = {}
    schedule = []
    for m, seq in machine_sequences.items():
        for i in range(1, len(seq)):
            prev_op = seq[i - 1]
            cur_op = seq[i]
            if end_times[prev_op] > start_times[cur_op]:
                return None
    return schedule
"""
        self.assertTrue(_has_machine_non_overlap_guard(source))

    def test_negated_strong_neighborhood_terms_do_not_count_as_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_solver_with_iterator_parser_ready_list_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal = {
                "summary": "Repair base capabilities without unstructured neighborhood claims.",
                "strategy_intent": "Use a ready-list constructor and omit AWLS/N8/tabu claims until implemented.",
                "rule_operator_hypotheses": [
                    {
                        "name": "ready_list_baseline",
                        "type": "dispatch_rule",
                        "novelty": "No critical-block or N8 claim is made here.",
                        "expected_effect": "Legal baseline only.",
                        "target_files": ["examples/agent_generated_fjsp_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "path": "examples/agent_generated_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": source,
                        "rationale": "Full replacement without unsupported structured-neighborhood claims.",
                    }
                ],
                "context_usage": {
                    "notes": "The repair plan drove the decision to omit AWLS/N8/tabu claims.",
                },
                "proposal_audit": {
                    "priority_knowledge_paths": [
                        "knowledge/imported_huawei_fjsp_knowledge/operators/standard_fjsp_awls_hgtsa_execution_skeleton.md"
                    ]
                },
                "solver_contract_self_check": _standard_solver_self_check(),
            }
            proposal_path = root / "proposal.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver explicitly avoids strong-neighborhood claims.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertFalse(any("structured_neighborhood_claim_unimplemented" in item for item in risks), risks)

    def test_agent_generated_standard_solver_helper_guards_pass_quality_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_solver_with_helper_guards_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver uses helper guards for ready ops, coverage, and eligibility.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.checks["agent_generated_solver_quality_risks"])
            self.assertEqual([], judgment.checks["agent_generated_solver_quality_risks"])

    def test_agent_generated_standard_solver_append_move_guards_pass_quality_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_solver_with_append_move_guards_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver uses append-based move repair with decoded coverage rejection.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertFalse(any("missing base capabilities" in item for item in risks), risks)
            self.assertFalse(any("post_move_coverage_guard" in item for item in risks), risks)

    def test_agent_generated_standard_direct_schedule_with_coverage_guard_passes_quality_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_direct_schedule_restarts_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_direct_schedule_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver builds schedules directly with scheduled_ops coverage guard.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.checks)
            self.assertEqual([], judgment.checks["agent_generated_solver_quality_risks"])
            self.assertEqual([], judgment.checks["incomplete_solution_acceptance_risks"])
            self.assertEqual(
                "stage_1_legal_constructor_without_sequence_state",
                judgment.checks["agent_generated_solver_method_stage"]["stage_name"],
            )

    def test_standard_fjsp_random_reassignment_hill_climber_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_random_reassignment_hill_climber_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver adds only random local search reassignment.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("shallow_local_search_operator" in item for item in risks))
            self.assertTrue(any("random operation-to-machine reassignment" in item for item in judgment.suggestions))

    def test_standard_fjsp_baseline_random_reassignment_is_not_rejected_as_shallow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_random_reassignment_hill_climber_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated baseline includes a weak random local-search pass.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.checks["agent_generated_solver_quality_risks"])
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertFalse(any("shallow_local_search_operator" in item for item in risks))

    def test_standard_fjsp_structured_neighborhood_skeleton_is_not_rejected_as_shallow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_structured_neighborhood_skeleton_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver includes critical-block N8/k-insertion and tabu scaffolding.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.checks["agent_generated_solver_quality_risks"])
            self.assertFalse(
                any(
                    "shallow_local_search_operator" in item
                    for item in judgment.checks["agent_generated_solver_quality_risks"]
                )
            )

    def test_standard_fjsp_claimed_structured_neighborhood_requires_executable_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_random_reassignment_hill_climber_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
            proposal["summary"] = (
                "Add a critical-block N8/k-insertion tabu neighborhood around the generated standard FJSP solver."
            )
            proposal["strategy_intent"] = (
                "Claim critical-block extraction plus N8 and k-insertion moves, but this fixture intentionally "
                "does not add those executable structures."
            )
            proposal["rule_operator_hypotheses"] = [
                {
                    "name": "critical_block_n8_k_insertion_tabu",
                    "type": "local_search_operator",
                    "target_files": ["examples/agent_generated_fjsp_solver.py"],
                }
            ]
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated solver claims a structured FJSP neighborhood without implementing it.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("structured_neighborhood_claim_unimplemented" in item for item in risks))
            self.assertTrue(any("critical_block_extraction" in item for item in risks))
            self.assertTrue(any("n8_or_k_insertion_neighbor_generation" in item for item in risks))
            self.assertTrue(
                any("do not repeat the AWLS/critical-block" in item for item in judgment.suggestions),
                judgment.suggestions,
            )
            stage = judgment.checks["agent_generated_solver_method_stage"]
            self.assertEqual("stage_4_basic_sequence_moves_without_structured_neighborhood", stage["stage_name"])
            repair_plan = judgment.checks["agent_generated_solver_repair_plan"]
            self.assertEqual("method_stage_migration", repair_plan["repair_mode"])
            self.assertEqual("structured_neighborhood_claim_unimplemented", repair_plan["reason"])
            self.assertIn("critical_block_extraction", repair_plan["missing_components"])
            self.assertIn("n8_or_k_insertion_neighbor_generation", repair_plan["missing_components"])
            self.assertEqual("stage_5_structured_neighborhood", repair_plan["target_stage"])

    def test_standard_fjsp_structured_neighborhood_claim_with_executable_skeleton_passes_claim_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_structured_neighborhood_skeleton_source()
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
            proposal["summary"] = "Add critical-block N8 and k-insertion tabu scaffolding."
            proposal["strategy_intent"] = "Use machine_sequences, critical_blocks, N8/k-insertion generators, and tabu search."
            proposal["rule_operator_hypotheses"] = [
                {
                    "name": "critical_block_n8_k_insertion_tabu",
                    "type": "local_search_operator",
                    "target_files": ["examples/agent_generated_fjsp_solver.py"],
                }
            ]
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated solver claims a structured FJSP neighborhood with executable scaffolding.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertFalse(any("structured_neighborhood_claim_unimplemented" in item for item in risks))

    def test_agent_generated_standard_solver_empty_fallback_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standard_solver_with_helper_guards_source().replace(
                "    if best_schedule is None:\n"
                "        raise RuntimeError('no feasible schedule')\n",
                "    if best_schedule is None:\n"
                "        best_schedule = []\n"
                "        best_makespan = 0\n",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_standard_solver_self_check(),
                content=source,
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Generated standard solver emits an empty fallback schedule.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("incomplete_solution_acceptance_risk", judgment.issues)
            self.assertTrue(
                any(
                    "empty_schedule_fallback_emitted" in item
                    for item in judgment.checks["incomplete_solution_acceptance_risks"]
                )
            )

    def test_agent_generated_structured_proposal_rejects_uncalled_source_self_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace(
                "    validate_schedule(instance, schedule)\n",
                "",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            proposal_path = _write_proposal(root, solver_contract_self_check=_complete_solver_self_check())

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver with an unwired validator.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("source-level self-check `validate_schedule` is defined but not reachable" in item for item in risks))

    def test_agent_generated_structured_proposal_rejects_uncalled_decoder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace(
                "decode_schedule(instance, dict(assignment), {m: list(v) for m, v in machine_sequences.items()})",
                "[]",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            proposal_path = _write_proposal(root, solver_contract_self_check=_complete_solver_self_check())

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver with an unwired decoder.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("decoder `decode_schedule` is defined but not reachable" in item for item in risks))

    def test_agent_generated_structured_proposal_rejects_decoder_dead_function_island(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace(
                "decode_schedule(instance, dict(assignment), {m: list(v) for m, v in machine_sequences.items()})",
                "[]",
            )
            source = source.replace(
                "\ndef validate_schedule(instance, schedule):\n",
                "\ndef evidence_only_decoder(instance, assignment, machine_sequences):\n"
                "    return decode_schedule(instance, assignment, machine_sequences)\n"
                "\n"
                "def validate_schedule(instance, schedule):\n",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            proposal_path = _write_proposal(root, solver_contract_self_check=_complete_solver_self_check())

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver with a decoder called only by unreachable helper code.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("decoder `decode_schedule` is defined but not reachable" in item for item in risks))

    def test_agent_generated_direct_edit_requires_source_level_self_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace(
                "    validate_schedule(instance, schedule)\n",
                "",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=True)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Direct generated solver edit without proposal self-check or source-level validation call.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            risks = judgment.checks["agent_generated_solver_self_check_risks"]
            self.assertTrue(any("defined but not called" in item for item in risks))

    def test_agent_generated_direct_edit_with_source_level_self_check_passes_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_strong_agent_generated_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Direct generated solver edit with source-level validation.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.issues)
            self.assertEqual([], judgment.checks["agent_generated_solver_self_check_risks"])

    def test_agent_generated_direct_sdst_edit_requires_setup_self_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace(
                "\n".join(
                    [
                        "    for machine_id, intervals in by_machine.items():",
                        "        intervals.sort()",
                        "        prev_key = None",
                        "        prev_end = 0",
                        "        for start, end, op_key in intervals:",
                        "            setup = setup_time(instance, machine_id, prev_key, op_key)",
                        "            if prev_key is not None and start < prev_end + setup:",
                        "                raise ValueError('setup arc violation')",
                        "            prev_key = op_key",
                        "            prev_end = end",
                    ]
                )
                + "\n",
                "",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=True)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Direct generated SDST solver edit without source-level setup validation.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            risks = judgment.checks["agent_generated_solver_self_check_risks"]
            self.assertTrue(any("setup_aware_machine_arc_timing" in item for item in risks))

    def test_agent_generated_self_check_evidence_must_match_source_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_strong_agent_generated_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            self_check = json.loads(json.dumps(_complete_solver_self_check()))
            self_check["capabilities"][0]["evidence"] = "imaginary_decoder_hook proves this capability."
            proposal_path = _write_proposal(root, solver_contract_self_check=self_check)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver skeleton with unsupported self-check evidence.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            risks = judgment.checks["agent_generated_solver_self_check_risks"]
            self.assertTrue(any("does not match generated source symbols" in item for item in risks))

    def test_agent_generated_self_check_narrative_must_match_source_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_strong_agent_generated_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            self_check = json.loads(json.dumps(_complete_solver_self_check()))
            self_check["decoder"] = "`phantom_evidence_anchor` proves decoder behavior."
            proposal_path = _write_proposal(root, solver_contract_self_check=self_check)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated solver skeleton with unsupported narrative evidence.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            risks = judgment.checks["agent_generated_solver_self_check_risks"]
            self.assertTrue(any("narrative evidence for decoder does not match" in item for item in risks))

    def test_agent_generated_solver_with_hardcoded_toy_parser_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_hardcoded_toy_parser_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=True)
            proposal_path = _write_proposal(
                root,
                solver_contract_self_check=_complete_solver_self_check(),
                content=_hardcoded_toy_parser_source(),
            )

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Hardcoded toy parser disguised as a generated solver.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("hardcode toy operation metadata" in item for item in risks))
            self.assertTrue(any("active_io_parser" in item for item in risks))

    def test_standard_fjsp_packed_job_line_parser_antipattern_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_standard_fjsp_physical_operation_line_parser_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=False)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Standard FJSP parser that incorrectly reads one physical line per operation.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_quality_contract_missing", judgment.issues)
            risks = judgment.checks["agent_generated_solver_quality_risks"]
            self.assertTrue(any("one physical operation line" in item for item in risks))

    def test_standard_fjsp_quality_contract_does_not_require_setup_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _strong_agent_generated_solver_source().replace("setup_time", "transition_gap")
            source = source.replace("setup = transition_gap(machine_id, prev_key, (job_id, op_id))", "setup = 0")
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured standard generated solver skeleton.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.issues)
            contract = judgment.checks["agent_generated_solver_quality_contract"]
            self.assertNotIn("sequence_dependent_setup", contract["active_features"])

    def test_agent_generated_direct_variant_edit_requires_active_variant_self_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            solver.write_text(_standardized_strong_solver_source(), encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["task"]["description"] += " Active variant: no-wait with release dates."
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Direct generated no-wait/release-date solver edit without active variant self-checks.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            risks = judgment.checks["agent_generated_solver_self_check_risks"]
            self.assertTrue(any("no_wait_start_time_guard" in item for item in risks))
            self.assertTrue(any("release_date_guard" in item for item in risks))

    def test_agent_generated_direct_variant_edit_with_active_variant_self_checks_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standardized_strong_solver_source().replace(
                "        if start < job_ready.get(job_id, 0):\n"
                "            raise ValueError('job precedence violation')\n",
                "        release_time = instance.get('release_dates', {}).get(op_key, 0)\n"
                "        if start < release_time:\n"
                "            raise ValueError('release date violation')\n"
                "        no_wait = instance.get('no_wait')\n"
                "        if no_wait and op_id > 0 and start != job_ready.get(job_id, 0):\n"
                "            raise ValueError('no_wait violation')\n"
                "        if start < job_ready.get(job_id, 0):\n"
                "            raise ValueError('job precedence violation')\n",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["task"]["description"] += " Active variant: no-wait with release dates."
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Direct generated no-wait/release-date solver edit with active variant self-checks.",
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertTrue(judgment.accepted, judgment.issues)
            contract = judgment.checks["agent_generated_solver_quality_contract"]
            self.assertIn("no_wait", contract["active_features"])
            self.assertIn("release_dates", contract["active_features"])
            playbook = {item["name"]: item for item in contract["capability_playbook"]}
            self.assertIn("successor", playbook["no_wait_start_time_guard"]["evidence"])
            self.assertIn("release", playbook["release_date_guard"]["evidence"])

    def test_agent_generated_structured_variant_self_check_requires_variant_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "examples" / "agent_generated_fjsp_solver.py"
            solver.parent.mkdir(parents=True)
            source = _standardized_strong_solver_source().replace(
                "        if start < job_ready.get(job_id, 0):\n"
                "            raise ValueError('job precedence violation')\n",
                "        release_time = instance.get('release_dates', {}).get(op_key, 0)\n"
                "        if start < release_time:\n"
                "            raise ValueError('release date violation')\n"
                "        no_wait = instance.get('no_wait')\n"
                "        if no_wait and op_id > 0 and start != job_ready.get(job_id, 0):\n"
                "            raise ValueError('no_wait violation')\n"
                "        if start < job_ready.get(job_id, 0):\n"
                "            raise ValueError('job precedence violation')\n",
            )
            solver.write_text(source, encoding="utf-8")
            context_path = _write_context(root, sdst=False)
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["task"]["description"] += " Active variant: no-wait with release dates."
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self_check = _complete_no_wait_release_self_check()
            self_check.pop("variant_handling", None)
            proposal_path = _write_proposal(root, solver_contract_self_check=self_check, content=source)

            judgment = judge_worker_result(
                worker_result=WorkerResult(
                    status="ok",
                    changed_files=["examples/agent_generated_fjsp_solver.py"],
                    summary="Structured generated no-wait/release-date solver edit without variant handling narrative.",
                    artifacts={"proposal": str(proposal_path)},
                ),
                worktree_path=root,
                context_packet_path=context_path,
                output_dir=root / "review",
                apply_worker_changes=False,
            )

            self.assertFalse(judgment.accepted)
            self.assertIn("agent_generated_solver_self_check_incomplete", judgment.issues)
            risks = judgment.checks["agent_generated_solver_self_check_risks"]
            self.assertTrue(any("missing variant_handling for active variant capabilities" in item for item in risks))

    def test_instance_diagnostics_prevent_generic_variant_false_positives(self) -> None:
        context = {
            "task": {
                "problem_family": "FJSP",
                "description": "Generate a standard FJSP solver from the active IO document.",
            },
            "evaluator_protocol": {
                "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
                "evaluator_command_template": "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}",
            },
            "problem_family_capability": {
                "supported_variants": ["standard_fjsp", "fjsp_sdst", "batching"],
                "knowledge_tags": ["fjsp", "sequence_dependent_setup", "batching"],
            },
            "documents": [
                {
                    "path": "README.md",
                    "snippet": "This platform can support FJSP-SDST, batching, transport, and other variants.",
                }
            ],
            "knowledge_cards": [
                {
                    "path": "knowledge/all_variants.md",
                    "snippet": "sequence-dependent setup and batch capacity are known variant skills.",
                }
            ],
            "instance_diagnostics": {
                "status": "available",
                "summary": {
                    "instance_count": 1,
                    "profiled_count": 1,
                    "sdst_instance_count": 0,
                    "setup_time_kinds": ["none"],
                },
                "instances": [
                    {
                        "variant": "standard_fjsp",
                        "setup_time_kind": "none",
                    }
                ],
            },
        }

        contract = build_agent_generated_solver_quality_contract(context)

        self.assertTrue(contract["enabled"])
        self.assertNotIn("sequence_dependent_setup", contract["active_features"])
        self.assertNotIn("batching", contract["active_features"])


def _write_context(root: Path, *, sdst: bool) -> Path:
    diagnostics = {
        "status": "available",
        "summary": {
            "instance_count": 1,
            "profiled_count": 1,
            "sdst_instance_count": 1 if sdst else 0,
            "setup_time_kinds": ["job_pair"] if sdst else [],
        },
        "instances": [
            {
                "id": "case",
                "variant": "fjsp_sdst" if sdst else "standard_fjsp",
                "setup_time_kind": "job_pair" if sdst else None,
            }
        ],
    }
    context = {
        "task": {
            "problem_family": "FJSP",
            "description": "Generate a standalone FJSP solver from IO docs.",
            "objectives": [{"name": "makespan", "direction": "minimize"}],
        },
        "evaluator_protocol": {
            "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
            "evaluator_command_template": "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}",
        },
        "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": [".git", "outputs"]},
        "instance_diagnostics": diagnostics,
    }
    path = root / "context_packet.json"
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_proposal(
    root: Path,
    *,
    solver_contract_self_check: dict[str, object] | None,
    content: str | None = None,
) -> Path:
    proposal = {
        "summary": "Create a structured generated solver.",
        "strategy_intent": "Use a standalone parser, stable operation representation, and complete decoder.",
        "rule_operator_hypotheses": [
            {
                "name": "complete_decoder_baseline",
                "type": "dispatch_rule",
                "target_files": ["examples/agent_generated_fjsp_solver.py"],
            }
        ],
        "changes": [
            {
                "path": "examples/agent_generated_fjsp_solver.py",
                "action": "create_or_replace",
                "content": content if content is not None else _strong_agent_generated_solver_source(),
            }
        ],
    }
    if solver_contract_self_check is not None:
        proposal["solver_contract_self_check"] = solver_contract_self_check
    path = root / "proposal.json"
    path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _complete_solver_self_check() -> dict[str, object]:
    capabilities = [
        "standalone_cli_interface",
        "active_io_parser",
        "declared_output_schema",
        "stable_operation_identity",
        "operation_level_ready_list_constructor",
        "complete_schedule_coverage_guard",
        "machine_eligibility_guard",
        "processing_duration_guard",
        "job_precedence_guard",
        "machine_non_overlap_guard",
        "bounded_runtime_or_iteration_guard",
        "incumbent_preservation_on_failed_candidate",
        "setup_aware_machine_arc_timing",
        "setup_aware_full_decoder_for_sequence_moves",
    ]
    return {
        "present": True,
        "active_features": [
            "alternative_machines",
            "operation_precedence",
            "machine_capacity",
            "makespan_objective",
            "sequence_dependent_setup",
        ],
        "capabilities": [
            {
                "name": name,
                "status": "implemented",
                "evidence": f"{name} is implemented in op_info/decode_schedule/improve.",
            }
            for name in capabilities
        ],
        "representation": "op_info uses (job_id, op_id), assignment maps op keys to machines, machine_sequences maps machines to op keys.",
        "decoder": "decode_schedule rebuilds all starts/ends and returns None on duplicates, missing ops, deadlocks, or ineligible machines.",
        "variant_handling": ["setup_time is checked in decode_schedule and validate_schedule for adjacent machine arcs."],
        "runtime_bounds": "time_limit/deadline and max_iterations bound decoding and improvement.",
        "incumbent_preservation": "candidate None is skipped and best_schedule changes only when candidate_makespan < best_makespan.",
        "remaining_gaps": [],
    }


def _standard_solver_self_check() -> dict[str, object]:
    capabilities = [
        "standalone_cli_interface",
        "active_io_parser",
        "declared_output_schema",
        "stable_operation_identity",
        "operation_level_ready_list_constructor",
        "complete_schedule_coverage_guard",
        "machine_eligibility_guard",
        "processing_duration_guard",
        "job_precedence_guard",
        "machine_non_overlap_guard",
        "bounded_runtime_or_iteration_guard",
        "incumbent_preservation_on_failed_candidate",
    ]
    return {
        "present": True,
        "active_features": [
            "alternative_machines",
            "machine_capacity",
            "makespan_objective",
            "operation_precedence",
        ],
        "capabilities": [
            {
                "name": name,
                "status": "implemented",
                "evidence": f"{name} is implemented by parse_instance, validate_schedule, solve, best_schedule, and deadline.",
            }
            for name in capabilities
        ],
        "representation": "parse_instance builds op_info keyed by (job, op), and solve/validate_schedule preserve that key.",
        "decoder": "validate_schedule rebuilds checked records and rejects missing, duplicate, ineligible, mistimed, precedence, or overlap errors.",
        "variant_handling": [],
        "runtime_bounds": "solve uses deadline and max_restarts to bound search.",
        "incumbent_preservation": "best_schedule and best_makespan change only after valid makespan improvement.",
        "remaining_gaps": [],
    }


def _direct_schedule_solver_self_check() -> dict[str, object]:
    payload = _standard_solver_self_check()
    payload["representation"] = "op_info uses (job, op_id) keys; build_schedule stores scheduled_ops and output schedule records with job_id/op_id."
    payload["decoder"] = "build_schedule constructs start/end records directly and returns None unless scheduled_ops covers sum(job_ops.values())."
    payload["runtime_bounds"] = "main uses max_restarts to bound repeated construction."
    payload["incumbent_preservation"] = "best_schedule and best_makespan update only when sched is not None and ms improves."
    for capability in payload["capabilities"]:
        name = capability["name"]
        if name == "operation_level_ready_list_constructor":
            capability["evidence"] = "build_schedule uses ready_ops over each job_next_op[job] and all op_info[op_key] eligible machines."
        elif name == "complete_schedule_coverage_guard":
            capability["evidence"] = "build_schedule checks len(scheduled_ops) != sum(job_ops.values()) before returning a schedule."
        elif name == "processing_duration_guard":
            capability["evidence"] = "build_schedule sets end = start + duration from op_info[op_key]."
        else:
            capability["evidence"] = f"{name} is implemented by parse_instance, build_schedule, main, best_schedule, and max_restarts."
    return payload


def _complete_no_wait_release_self_check() -> dict[str, object]:
    capabilities = [
        "standalone_cli_interface",
        "active_io_parser",
        "declared_output_schema",
        "stable_operation_identity",
        "operation_level_ready_list_constructor",
        "complete_schedule_coverage_guard",
        "machine_eligibility_guard",
        "processing_duration_guard",
        "job_precedence_guard",
        "machine_non_overlap_guard",
        "bounded_runtime_or_iteration_guard",
        "incumbent_preservation_on_failed_candidate",
        "no_wait_start_time_guard",
        "release_date_guard",
    ]
    return {
        "present": True,
        "active_features": [
            "alternative_machines",
            "machine_capacity",
            "makespan_objective",
            "no_wait",
            "operation_precedence",
            "release_dates",
        ],
        "capabilities": [
            {
                "name": name,
                "status": "implemented",
                "evidence": f"{name} is implemented in op_info/decode_schedule/improve/validate_schedule/release_time/no_wait.",
            }
            for name in capabilities
        ],
        "representation": "op_info uses (job_id, op_id), assignment maps op keys to machines, machine_sequences maps machines to op keys.",
        "decoder": "decode_schedule rebuilds all starts/ends and returns None on duplicates, missing ops, deadlocks, or ineligible machines.",
        "variant_handling": ["release_time and no_wait guards are checked in validate_schedule."],
        "runtime_bounds": "time_limit/deadline and max_iterations bound decoding and improvement.",
        "incumbent_preservation": "candidate None is skipped and best_schedule changes only when candidate_makespan < best_makespan.",
        "remaining_gaps": [],
    }


def _strong_agent_generated_solver_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "import random",
            "import time",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    numbers = [int(token) for token in Path(path).read_text(encoding='utf-8').split()]",
            "    idx = 0",
            "    job_count, machine_count, _max_candidates = numbers[idx:idx + 3]",
            "    idx += 3",
            "    raw_ops = []",
            "    raw_machine_ids = []",
            "    for job_id in range(job_count):",
            "        op_count = numbers[idx]",
            "        idx += 1",
            "        for op_id in range(op_count):",
            "            candidate_count = numbers[idx]",
            "            idx += 1",
            "            candidates = []",
            "            for _candidate_index in range(candidate_count):",
            "                machine_id = numbers[idx]",
            "                duration = numbers[idx + 1]",
            "                idx += 2",
            "                raw_machine_ids.append(machine_id)",
            "                candidates.append((machine_id, duration))",
            "            raw_ops.append((job_id, op_id, candidates))",
            "    machine_base = 0 if raw_machine_ids and min(raw_machine_ids) == 0 else 1",
            "    op_info = {}",
            "    for job_id, op_id, candidates in raw_ops:",
            "        eligible = {machine_id - machine_base: duration for machine_id, duration in candidates}",
            "        op_key = (job_id, op_id)",
            "        selected_machine = min(eligible, key=lambda machine_id: (eligible[machine_id], machine_id))",
            "        op_info[op_key] = {'eligible': eligible, 'processing_time': eligible[selected_machine]}",
            "    setup_times = []",
            "    setup_dimension = job_count",
            "    if len(numbers) - idx == machine_count * job_count * job_count:",
            "        for machine_id in range(machine_count):",
            "            matrix = []",
            "            for _row in range(setup_dimension):",
            "                matrix.append(numbers[idx:idx + setup_dimension])",
            "                idx += setup_dimension",
            "            setup_times.append(matrix)",
            "    total_ops = len(op_info)",
            "    return {'name': Path(path).name, 'op_info': op_info, 'machine_count': machine_count, 'setup_times': setup_times, 'total_ops': total_ops}",
            "",
            "def setup_time(instance, machine_id, prev_key, cur_key):",
            "    if prev_key is None or not instance.get('setup_times'):",
            "        return 0",
            "    return instance['setup_times'][machine_id][prev_key[0]][cur_key[0]]",
            "",
            "def construct_initial_solution(instance, seed=0, restart_count=2):",
            "    rng = random.Random(seed)",
            "    best_assignment = None",
            "    best_machine_sequences = None",
            "    best_makespan = None",
            "    job_ids = sorted({job_id for job_id, _op_id in instance['op_info']})",
            "    for _restart in range(max(1, restart_count)):",
            "        next_op_by_job = {job_id: 0 for job_id in job_ids}",
            "        job_ready = {job_id: 0 for job_id in job_ids}",
            "        machine_ready = {machine_id: 0 for machine_id in range(instance['machine_count'])}",
            "        machine_prev = {}",
            "        assignment = {}",
            "        machine_sequences = {machine_id: [] for machine_id in range(instance['machine_count'])}",
            "        while len(assignment) < instance['total_ops']:",
            "            ready_ops = []",
            "            for job_id in job_ids:",
            "                op_id = next_op_by_job[job_id]",
            "                op_key = (job_id, op_id)",
            "                if op_key not in instance['op_info']:",
            "                    continue",
            "                eligible = instance['op_info'][op_key]['eligible']",
            "                for machine_id, duration in eligible.items():",
            "                    setup = setup_time(instance, machine_id, machine_prev.get(machine_id), op_key)",
            "                    start = max(job_ready[job_id], machine_ready[machine_id] + setup)",
            "                    finish = start + duration",
            "                    ready_ops.append((finish, start, duration, rng.random(), op_key, machine_id))",
            "            if not ready_ops:",
            "                return None, None",
            "            best_choice = min(ready_ops)",
            "            finish, _start, _duration, _tie, op_key, machine_id = best_choice",
            "            assignment[op_key] = machine_id",
            "            machine_sequences[machine_id].append(op_key)",
            "            job_id, op_id = op_key",
            "            next_op_by_job[job_id] = op_id + 1",
            "            job_ready[job_id] = finish",
            "            machine_ready[machine_id] = finish",
            "            machine_prev[machine_id] = op_key",
            "        candidate = decode_schedule(instance, dict(assignment), {m: list(v) for m, v in machine_sequences.items()})",
            "        if candidate is None:",
            "            continue",
            "        candidate_makespan = max(item['end'] for item in candidate)",
            "        if best_makespan is None or candidate_makespan < best_makespan:",
            "            best_assignment = assignment",
            "            best_machine_sequences = machine_sequences",
            "            best_makespan = candidate_makespan",
            "    return best_assignment, best_machine_sequences",
            "",
            "def decode_schedule(instance, assignment, machine_sequences, time_limit=1.0):",
            "    deadline = time.perf_counter() + time_limit",
            "    op_info = instance['op_info']",
            "    total_ops = instance['total_ops']",
            "    expected_ops = set(op_info)",
            "    seen_ops = set()",
            "    schedule = []",
            "    job_ready = {}",
            "    machine_ready = {}",
            "    machine_prev = {}",
            "    if set(assignment) != expected_ops:",
            "        return None",
            "    queues = {machine_id: list(sequence) for machine_id, sequence in machine_sequences.items()}",
            "    while len(schedule) < total_ops:",
            "        if time.perf_counter() > deadline:",
            "            return None",
            "        progressed = False",
            "        for machine_id, sequence in queues.items():",
            "            if not sequence:",
            "                continue",
            "            job_id, op_id = sequence[0]",
            "            op_key = (job_id, op_id)",
            "            if op_key in seen_ops:",
            "                return None  # duplicate operation",
            "            info = op_info[op_key]",
            "            eligible = info['eligible']",
            "            if machine_id not in eligible:",
            "                return None",
            "            if op_id > 0 and (job_id, op_id - 1) not in seen_ops:",
            "                continue",
            "            duration = eligible[machine_id]",
            "            prev_key = machine_prev.get(machine_id)",
            "            setup = setup_time(instance, machine_id, prev_key, op_key)",
            "            prev_end = machine_ready.get(machine_id, 0)",
            "            start = max(job_ready.get(job_id, 0), prev_end + setup)",
            "            end = start + duration",
            "            if end - start != duration:",
            "                return None",
            "            schedule.append({'job_id': job_id, 'op_id': op_id, 'machine_id': machine_id, 'start': start, 'end': end})",
            "            seen_ops.add(op_key)",
            "            sequence.pop(0)",
            "            job_ready[job_id] = end",
            "            machine_ready[machine_id] = end",
            "            machine_prev[machine_id] = op_key",
            "            progressed = True",
            "        if not progressed:",
            "            return None",
            "    if len(schedule) != total_ops or seen_ops != expected_ops:",
            "        return None",
            "    return schedule",
            "",
            "def improve(instance, max_iterations=10):",
            "    assignment, machine_sequences = construct_initial_solution(instance, seed=0)",
            "    if assignment is None or machine_sequences is None:",
            "        return None",
            "    best_schedule = decode_schedule(instance, dict(assignment), {m: list(v) for m, v in machine_sequences.items()})",
            "    if best_schedule is None:",
            "        return None",
            "    best_makespan = max(item['end'] for item in best_schedule)",
            "    for _iteration in range(max_iterations):",
            "        candidate = decode_schedule(instance, dict(assignment), {m: list(v) for m, v in machine_sequences.items()})",
            "        if candidate is None:",
            "            continue",
            "        candidate_makespan = max(item['end'] for item in candidate)",
            "        if candidate_makespan < best_makespan:",
            "            best_schedule = candidate",
            "            best_makespan = candidate_makespan",
            "    return best_schedule",
            "",
            "def validate_schedule(instance, schedule):",
            "    op_info = instance['op_info']",
            "    expected_ops = set(op_info)",
            "    total_ops = instance['total_ops']",
            "    seen_ops = set()",
            "    job_ready = {}",
            "    machine_ready = {}",
            "    machine_intervals = {}",
            "    by_machine = {}",
            "    for item in schedule:",
            "        job_id = item['job_id']",
            "        op_id = item['op_id']",
            "        machine_id = item['machine_id']",
            "        start = item['start']",
            "        end = item['end']",
            "        op_key = (job_id, op_id)",
            "        if op_key in seen_ops:",
            "            raise ValueError('duplicate operation')",
            "        if op_key not in expected_ops:",
            "            raise ValueError('unexpected operation')",
            "        seen_ops.add(op_key)",
            "        eligible = op_info[op_key]['eligible']",
            "        if machine_id not in eligible:",
            "            raise ValueError('ineligible machine')",
            "        duration = eligible[machine_id]",
            "        if end - start != duration:",
            "            raise ValueError('processing duration mismatch')",
            "        if start < job_ready.get(job_id, 0):",
            "            raise ValueError('job precedence violation')",
            "        for prev_start, prev_end in machine_intervals.get(machine_id, []):",
            "            if not (end <= prev_start or start >= prev_end):",
            "                raise ValueError('machine overlap')",
            "        job_ready[job_id] = max(job_ready.get(job_id, 0), end)",
            "        machine_ready[machine_id] = max(machine_ready.get(machine_id, 0), end)",
            "        machine_intervals.setdefault(machine_id, []).append((start, end))",
            "        by_machine.setdefault(machine_id, []).append((start, end, op_key))",
            "    for machine_id, intervals in by_machine.items():",
            "        intervals.sort()",
            "        prev_key = None",
            "        prev_end = 0",
            "        for start, end, op_key in intervals:",
            "            setup = setup_time(instance, machine_id, prev_key, op_key)",
            "            if prev_key is not None and start < prev_end + setup:",
            "                raise ValueError('setup arc violation')",
            "            prev_key = op_key",
            "            prev_end = end",
            "    missing_ops = expected_ops - seen_ops",
            "    if missing_ops or len(schedule) != total_ops:",
            "        raise ValueError('missing_ops')",
            "    return True",
            "",
            "def solve(input_path, seed=0):",
            "    instance = parse_instance(input_path)",
            "    schedule = improve(instance)",
            "    if schedule is None:",
            "        raise ValueError('infeasible generated schedule')",
            "    validate_schedule(instance, schedule)",
            "    return {'format': 'standard_fjsp_schedule_v1', 'variant': 'fjsp_sdst', 'instance': instance['name'], 'seed': seed, 'schedule': schedule, 'makespan': max(item['end'] for item in schedule)}",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    solution = solve(args.input, seed=args.seed)",
            "    Path(args.output).write_text(json.dumps(solution), encoding='utf-8')",
            "",
            "if __name__ == '__main__':",
            "    raise SystemExit(main())",
        ]
    ) + "\n"


def _machine_major_decoder_solver_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    numbers = [int(token) for token in Path(path).read_text(encoding='utf-8').split()]",
            "    job_count, machine_count, _max_candidates = numbers[:3]",
            "    op_info = {(0, 0): {'eligible': {0: 1}}, (0, 1): {'eligible': {1: 1}}}",
            "    return {'name': Path(path).name, 'op_info': op_info, 'machine_count': machine_count, 'total_ops': len(op_info)}",
            "",
            "def decode_schedule(instance, assignment, machine_sequences):",
            "    schedule = []",
            "    job_end = {}",
            "    expected_ops = set(instance['op_info'])",
            "    seen_ops = set()",
            "    for machine_id, sequence in machine_sequences.items():",
            "        machine_end = 0",
            "        for op_key in sequence:",
            "            job_id, op_id = op_key",
            "            if op_key in seen_ops or op_key not in expected_ops:",
            "                return None",
            "            eligible = instance['op_info'][op_key]['eligible']",
            "            if machine_id not in eligible:",
            "                return None",
            "            start = max(job_end.get(job_id, 0), machine_end)",
            "            duration = eligible[machine_id]",
            "            end = start + duration",
            "            schedule.append({'job_id': job_id, 'op_id': op_id, 'machine_id': machine_id, 'start': start, 'end': end})",
            "            seen_ops.add(op_key)",
            "            job_end[job_id] = end",
            "            machine_end = end",
            "    if seen_ops != expected_ops or len(schedule) != instance['total_ops']:",
            "        return None",
            "    return schedule",
            "",
            "def solve(input_path, seed=0):",
            "    instance = parse_instance(input_path)",
            "    assignment = {(0, 0): 0, (0, 1): 1}",
            "    machine_sequences = {1: [(0, 1)], 0: [(0, 0)]}",
            "    best_schedule = decode_schedule(instance, assignment, machine_sequences)",
            "    best_makespan = max(item['end'] for item in best_schedule) if best_schedule else 999",
            "    return {'format': 'standard_fjsp_schedule_v1', 'schedule': best_schedule, 'makespan': best_makespan}",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    Path(args.output).write_text(json.dumps(solve(args.input, seed=args.seed)), encoding='utf-8')",
            "",
            "if __name__ == '__main__':",
            "    raise SystemExit(main())",
        ]
    ) + "\n"


def _standard_solver_with_compact_variable_names_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "import random",
            "import time",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    tokens = Path(path).read_text().split()",
            "    it = iter(tokens)",
            "    num_jobs = int(next(it))",
            "    num_machines = int(next(it))",
            "    _max_candidates = int(next(it))",
            "    raw_ops = []",
            "    for job in range(num_jobs):",
            "        op_count = int(next(it))",
            "        for op in range(op_count):",
            "            cand_count = int(next(it))",
            "            candidates = []",
            "            for _ in range(cand_count):",
            "                m = int(next(it))",
            "                pt = int(next(it))",
            "                candidates.append((m, pt))",
            "            raw_ops.append((job, op, candidates))",
            "    offset = 0 if any(m == 0 for _, _, cands in raw_ops for m, _ in cands) else 1",
            "    op_info = {}",
            "    job_op_counts = {}",
            "    for job, op, cands in raw_ops:",
            "        op_info[(job, op)] = [(m - offset, pt) for m, pt in cands]",
            "        job_op_counts[job] = max(job_op_counts.get(job, 0), op + 1)",
            "    return op_info, num_jobs, num_machines, len(raw_ops), job_op_counts",
            "",
            "def validate_schedule(scheduled, op_info, total_ops, num_machines, op_durations):",
            "    expected_ops = set(op_info.keys())",
            "    seen = set()",
            "    validated = []",
            "    job_end = {}",
            "    machine_intervals = {m: [] for m in range(num_machines)}",
            "    for (j, o, m, s, e) in scheduled:",
            "        if (j, o) in seen or (j, o) not in expected_ops:",
            "            return False, []",
            "        seen.add((j, o))",
            "        if m not in [mc for mc, _ in op_info[(j, o)]]:",
            "            return False, []",
            "        dur = op_durations[(j, o)][m]",
            "        if e - s != dur:",
            "            return False, []",
            "        if o > 0 and ((j, o - 1) not in job_end or job_end[(j, o - 1)] > s):",
            "            return False, []",
            "        job_end[(j, o)] = e",
            "        machine_intervals[m].append((s, e))",
            "        validated.append({'job_id': j, 'op_id': o, 'machine_id': m, 'start': s, 'end': e})",
            "    if seen != expected_ops or len(scheduled) != total_ops:",
            "        return False, []",
            "    for intervals in machine_intervals.values():",
            "        intervals.sort()",
            "        for i in range(len(intervals) - 1):",
            "            if intervals[i][1] > intervals[i + 1][0]:",
            "                return False, []",
            "    return True, validated",
            "",
            "def solve(input_path, output_path, seed):",
            "    op_info, num_jobs, num_machines, total_ops, job_op_counts = parse_instance(input_path)",
            "    op_durations = {key: {m: pt for m, pt in cands} for key, cands in op_info.items()}",
            "    rng = random.Random(seed)",
            "    best_schedule = None",
            "    best_makespan = float('inf')",
            "    deadline = time.time() + 30",
            "    max_restarts = 100",
            "    for _restart in range(max_restarts):",
            "        if time.time() > deadline:",
            "            break",
            "        job_next_op = {j: 0 for j in range(num_jobs)}",
            "        job_ready_time = {j: 0 for j in range(num_jobs)}",
            "        machine_ready_time = {m: 0 for m in range(num_machines)}",
            "        scheduled = []",
            "        while len(scheduled) < total_ops:",
            "            ready = []",
            "            for j in range(num_jobs):",
            "                nxt = job_next_op[j]",
            "                if nxt is not None and nxt < job_op_counts[j]:",
            "                    ready.append((j, nxt, op_info[(j, nxt)]))",
            "            best_score = float('inf')",
            "            best_assignment = None",
            "            for (j, op_id, cands) in ready:",
            "                for (m, pt) in cands:",
            "                    start = max(job_ready_time[j], machine_ready_time[m])",
            "                    end = start + pt",
            "                    score = end + rng.random() * 0.01",
            "                    if score < best_score:",
            "                        best_score = score",
            "                        best_assignment = (j, op_id, m, start, end)",
            "            if best_assignment is None:",
            "                break",
            "            j, op_id, m, start, end = best_assignment",
            "            scheduled.append((j, op_id, m, start, end))",
            "            job_ready_time[j] = end",
            "            machine_ready_time[m] = end",
            "            job_next_op[j] = op_id + 1 if op_id + 1 < job_op_counts[j] else None",
            "        valid, validated = validate_schedule(scheduled, op_info, total_ops, num_machines, op_durations)",
            "        if valid:",
            "            makespan = max(e for (_, _, _, _, e) in scheduled)",
            "            if makespan < best_makespan:",
            "                best_makespan = makespan",
            "                best_schedule = validated",
            "    if best_schedule is None:",
            "        raise RuntimeError('No feasible schedule found within budget')",
            "    output = {'format': 'standard_fjsp_schedule_v1', 'schedule': best_schedule, 'makespan': best_makespan}",
            "    with open(output_path, 'w') as f:",
            "        json.dump(output, f, indent=2)",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, required=True)",
            "    args = parser.parse_args()",
            "    solve(args.input, args.output, args.seed)",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
    ) + "\n"


def _standard_solver_with_iterator_parser_ready_list_source() -> str:
    return "\n".join(
        [
            "import argparse, json, random, time",
            "",
            "def parse_instance(path):",
            "    with open(path) as f:",
            "        tokens = f.read().split()",
            "    it = iter(tokens)",
            "    job_count = int(next(it))",
            "    machine_count = int(next(it))",
            "    _ = next(it)",
            "    op_info = {}",
            "    job_ops = {}",
            "    expected_ops = 0",
            "    for j in range(job_count):",
            "        ops_in_job = int(next(it))",
            "        job_ops[j] = ops_in_job",
            "        for op_id in range(ops_in_job):",
            "            candidate_count = int(next(it))",
            "            candidates = {}",
            "            for _ in range(candidate_count):",
            "                m = int(next(it))",
            "                dur = int(next(it))",
            "                candidates[m] = dur",
            "            op_info[(j, op_id)] = candidates",
            "            expected_ops += 1",
            "    return op_info, job_ops, expected_ops, machine_count",
            "",
            "def construct_initial(op_info, job_ops, total_ops, machine_count, rng):",
            "    job_next_op = {j: 0 for j in job_ops}",
            "    machine_available = [0.0] * machine_count",
            "    job_ready = {j: 0.0 for j in job_ops}",
            "    assignment = {}",
            "    machine_sequences = {m: [] for m in range(machine_count)}",
            "    scheduled = 0",
            "    while scheduled < total_ops:",
            "        candidates = []",
            "        for j, next_op in list(job_next_op.items()):",
            "            if next_op < job_ops.get(j, 0):",
            "                op = (j, next_op)",
            "                for m, dur in op_info[op].items():",
            "                    start = max(machine_available[m], job_ready[j])",
            "                    candidates.append((start + dur, op, m, dur))",
            "        candidates.sort(key=lambda item: item[0])",
            "        best = rng.choice([item for item in candidates if item[0] == candidates[0][0]])",
            "        finish, op, m, dur = best",
            "        j, _op_idx = op",
            "        assignment[op] = m",
            "        machine_sequences[m].append(op)",
            "        machine_available[m] = finish",
            "        job_ready[j] = finish",
            "        job_next_op[j] += 1",
            "        scheduled += 1",
            "    return assignment, machine_sequences",
            "",
            "def decode_schedule(assignment, machine_sequences, op_info):",
            "    job_ready = {j: 0.0 for j, _ in assignment}",
            "    machine_ready = [0.0] * len(machine_sequences)",
            "    job_next = {j: 0 for j in job_ready}",
            "    machine_pos = {m: 0 for m in machine_sequences}",
            "    schedule = []",
            "    scheduled_ops = set()",
            "    total_ops = len(assignment)",
            "    scheduled = 0",
            "    while scheduled < total_ops:",
            "        progress = False",
            "        for m, seq in machine_sequences.items():",
            "            pos = machine_pos[m]",
            "            if pos >= len(seq):",
            "                continue",
            "            op = seq[pos]",
            "            j, op_idx = op",
            "            if job_next[j] != op_idx:",
            "                continue",
            "            dur = None",
            "            for cand_m, cand_dur in op_info[op].items():",
            "                if cand_m == m:",
            "                    dur = cand_dur",
            "                    break",
            "            if dur is None:",
            "                return None",
            "            start = max(machine_ready[m], job_ready[j])",
            "            end = start + dur",
            "            schedule.append({'job_id': j, 'op_id': op_idx, 'machine_id': m, 'start': start, 'end': end})",
            "            machine_ready[m] = end",
            "            job_ready[j] = end",
            "            job_next[j] += 1",
            "            machine_pos[m] += 1",
            "            scheduled += 1",
            "            scheduled_ops.add(op)",
            "            progress = True",
            "        if not progress:",
            "            return None",
            "    if len(scheduled_ops) != len(op_info):",
            "        return None",
            "    return schedule, max(row['end'] for row in schedule)",
            "",
            "def solve(input_path, output_path, seed):",
            "    rng = random.Random(seed)",
            "    op_info, job_ops, total_ops, machine_count = parse_instance(input_path)",
            "    assignment, machine_sequences = construct_initial(op_info, job_ops, total_ops, machine_count, rng)",
            "    best_schedule = None",
            "    best_makespan = float('inf')",
            "    result = decode_schedule(assignment, machine_sequences, op_info)",
            "    if result is None:",
            "        raise RuntimeError('infeasible')",
            "    schedule, mk = result",
            "    if len(schedule) != total_ops:",
            "        raise RuntimeError('coverage mismatch')",
            "    if mk < best_makespan:",
            "        best_schedule = schedule",
            "        best_makespan = mk",
            "    deadline = time.time() + 1.0",
            "    max_iter = 1",
            "    while max_iter > 0 and time.time() < deadline:",
            "        max_iter -= 1",
            "    with open(output_path, 'w') as f:",
            "        json.dump({'format': 'standard_fjsp_schedule_v1', 'schedule': best_schedule, 'makespan': best_makespan}, f)",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, required=True)",
            "    args = parser.parse_args()",
            "    solve(args.input, args.output, args.seed)",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
    ) + "\n"


def _standard_solver_with_helper_guards_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "import random",
            "import time",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    tokens = Path(path).read_text(encoding='utf-8').split()",
            "    idx = 0",
            "    job_count = int(tokens[idx]); idx += 1",
            "    machine_count = int(tokens[idx]); idx += 1",
            "    _max_candidates = int(tokens[idx]); idx += 1",
            "    op_info = {}",
            "    total_ops = 0",
            "    for job_id in range(job_count):",
            "        op_count = int(tokens[idx]); idx += 1",
            "        for op_id in range(op_count):",
            "            cand_count = int(tokens[idx]); idx += 1",
            "            candidates = []",
            "            for _ in range(cand_count):",
            "                mach = int(tokens[idx]); idx += 1",
            "                dur = int(tokens[idx]); idx += 1",
            "                candidates.append((mach, dur))",
            "            op_info[(job_id, op_id)] = candidates",
            "            total_ops += 1",
            "    return op_info, machine_count, total_ops",
            "",
            "def get_ready_ops(jobs, job_next_op, unscheduled):",
            "    ready_ops = []",
            "    for j in jobs:",
            "        if job_next_op[j] is not None:",
            "            op_key = (j, job_next_op[j])",
            "            if op_key in unscheduled:",
            "                ready_ops.append(op_key)",
            "    return ready_ops",
            "",
            "def construct_schedule_one_restart(op_info, num_machines, rng):",
            "    jobs = sorted({job_id for job_id, _op_id in op_info})",
            "    job_next_op = {job_id: 0 for job_id in jobs}",
            "    job_ready = {job_id: 0 for job_id in jobs}",
            "    machine_ready = {machine_id: 0 for machine_id in range(num_machines)}",
            "    unscheduled = set(op_info)",
            "    schedule = {}",
            "    while unscheduled:",
            "        ready_ops = get_ready_ops(jobs, job_next_op, unscheduled)",
            "        if not ready_ops:",
            "            return None",
            "        best_finish = float('inf')",
            "        best_choices = []",
            "        for op_key in ready_ops:",
            "            for mach, dur in op_info[op_key]:",
            "                start = max(job_ready[op_key[0]], machine_ready[mach])",
            "                finish = start + dur",
            "                if finish < best_finish:",
            "                    best_finish = finish",
            "                    best_choices = [(op_key, mach, start, finish)]",
            "                elif finish == best_finish:",
            "                    best_choices.append((op_key, mach, start, finish))",
            "        op_key, mach, start, finish = rng.choice(best_choices)",
            "        schedule[op_key] = {'machine': mach, 'start': start, 'end': finish}",
            "        job_ready[op_key[0]] = finish",
            "        machine_ready[mach] = finish",
            "        job_next_op[op_key[0]] += 1",
            "        unscheduled.remove(op_key)",
            "    return schedule",
            "",
            "def check_schedule_coverage(schedule, expected_ops):",
            "    if set(schedule) != expected_ops:",
            "        return False, 'missing or extra operations'",
            "    return True, 'ok'",
            "",
            "def check_machine_eligibility(op_key, machine_id, op_info):",
            "    return any(candidate_machine == machine_id for candidate_machine, _duration in op_info[op_key])",
            "",
            "def validate_schedule(schedule, op_info):",
            "    ok, message = check_schedule_coverage(schedule, set(op_info))",
            "    if not ok:",
            "        return False, message",
            "    job_end = {}",
            "    machine_intervals = {}",
            "    for op_key, assignment in schedule.items():",
            "        machine_id = assignment['machine']",
            "        start = assignment['start']",
            "        end = assignment['end']",
            "        if not check_machine_eligibility(op_key, machine_id, op_info):",
            "            return False, 'ineligible machine'",
            "        duration = next(dur for mach, dur in op_info[op_key] if mach == machine_id)",
            "        if end - start != duration:",
            "            return False, 'duration mismatch'",
            "        job_id, op_id = op_key",
            "        if op_id > 0 and job_end.get((job_id, op_id - 1), 0) > start:",
            "            return False, 'precedence violation'",
            "        job_end[op_key] = end",
            "        machine_intervals.setdefault(machine_id, []).append((start, end))",
            "    for intervals in machine_intervals.values():",
            "        intervals.sort()",
            "        for i in range(len(intervals) - 1):",
            "            if intervals[i][1] > intervals[i + 1][0]:",
            "                return False, 'machine overlap'",
            "    return True, 'ok'",
            "",
            "def solve(input_path, output_path, seed):",
            "    op_info, num_machines, _total_ops = parse_instance(input_path)",
            "    best_schedule = None",
            "    best_makespan = float('inf')",
            "    deadline = time.time() + 30",
            "    restart = 0",
            "    while time.time() < deadline and restart < 100:",
            "        candidate = construct_schedule_one_restart(op_info, num_machines, random.Random(seed + restart))",
            "        restart += 1",
            "        if candidate is None:",
            "            continue",
            "        valid, _message = validate_schedule(candidate, op_info)",
            "        if not valid:",
            "            continue",
            "        makespan = max(item['end'] for item in candidate.values())",
            "        if makespan < best_makespan:",
            "            best_makespan = makespan",
            "            best_schedule = candidate",
            "    if best_schedule is None:",
            "        raise RuntimeError('no feasible schedule')",
            "    output_schedule = []",
            "    for (job_id, op_id), item in sorted(best_schedule.items()):",
            "        output_schedule.append({'job_id': job_id, 'op_id': op_id, 'machine_id': item['machine'], 'start': item['start'], 'end': item['end']})",
            "    Path(output_path).write_text(json.dumps({'format': 'standard_fjsp_schedule_v1', 'schedule': output_schedule, 'makespan': best_makespan}), encoding='utf-8')",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    solve(args.input, args.output, args.seed)",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
    ) + "\n"


def _standard_solver_with_append_move_guards_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "import random",
            "import time",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    lines = [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]",
            "    job_count, machine_count, _max_candidates = [int(x) for x in lines[0].split()[:3]]",
            "    op_info = {}",
            "    raw_machine_ids = []",
            "    for job_id, line in enumerate(lines[1:1 + job_count]):",
            "        tokens = [int(x) for x in line.split()]",
            "        op_count = tokens[0]",
            "        pos = 1",
            "        for op_id in range(op_count):",
            "            candidate_count = tokens[pos]",
            "            pos += 1",
            "            candidates = {}",
            "            for _ in range(candidate_count):",
            "                machine_raw = tokens[pos]",
            "                duration = tokens[pos + 1]",
            "                pos += 2",
            "                raw_machine_ids.append(machine_raw)",
            "                candidates[machine_raw] = duration",
            "            op_info[(job_id, op_id)] = {'eligible': candidates}",
            "    machine_base = 0 if min(raw_machine_ids) >= 0 and max(raw_machine_ids) < machine_count else 1",
            "    for op_key, data in op_info.items():",
            "        data['eligible'] = {machine_id - machine_base: duration for machine_id, duration in data['eligible'].items()}",
            "    return {'op_info': op_info, 'machine_count': machine_count, 'total_ops': len(op_info)}",
            "",
            "def build_feasible_schedule(instance, rng):",
            "    op_info = instance['op_info']",
            "    job_ids = sorted({job_id for job_id, _op_id in op_info})",
            "    job_next_op = {job_id: 0 for job_id in job_ids}",
            "    job_ready_time = {job_id: 0 for job_id in job_ids}",
            "    machine_ready_time = {machine_id: 0 for machine_id in range(instance['machine_count'])}",
            "    unfinished_jobs = set(job_ids)",
            "    assignment = {}",
            "    machine_sequences = {machine_id: [] for machine_id in range(instance['machine_count'])}",
            "    while unfinished_jobs:",
            "        ready_ops = []",
            "        for j in list(unfinished_jobs):",
            "            op_id = job_next_op[j]",
            "            op_key = (j, op_id)",
            "            if op_key not in op_info:",
            "                unfinished_jobs.remove(j)",
            "                continue",
            "            ready_ops.append(op_key)",
            "        best_candidates = []",
            "        best_time = float('inf')",
            "        for op_key in ready_ops:",
            "            j, _o = op_key",
            "            op_data = op_info[op_key]",
            "            for m in op_data['eligible']:",
            "                duration = op_data['eligible'][m]",
            "                start_time = max(job_ready_time[j], machine_ready_time[m])",
            "                finish_time = start_time + duration",
            "                if finish_time < best_time:",
            "                    best_time = finish_time",
            "                    best_candidates = [(op_key, m, finish_time)]",
            "                elif finish_time == best_time:",
            "                    best_candidates.append((op_key, m, finish_time))",
            "        op_key, m, finish_time = rng.choice(best_candidates)",
            "        assignment[op_key] = m",
            "        machine_sequences[m].append(op_key)",
            "        job_next_op[op_key[0]] += 1",
            "        job_ready_time[op_key[0]] = finish_time",
            "        machine_ready_time[m] = finish_time",
            "    return assignment, machine_sequences",
            "",
            "def decode_schedule(assignment, machine_sequences, op_info):",
            "    all_ops = set(op_info.keys())",
            "    decoded_ops = set()",
            "    schedule = []",
            "    job_pred_end = {}",
            "    machine_next_start = {m: 0 for m in machine_sequences}",
            "    while len(decoded_ops) < len(all_ops):",
            "        progressed = False",
            "        for m, seq in machine_sequences.items():",
            "            for op_key in seq:",
            "                if op_key in decoded_ops:",
            "                    continue",
            "                j, o = op_key",
            "                if o > 0 and (j, o - 1) not in decoded_ops:",
            "                    break",
            "                duration = op_info[op_key]['eligible'][assignment[op_key]]",
            "                start_time = max(machine_next_start[m], job_pred_end.get((j, o - 1), 0))",
            "                end_time = start_time + duration",
            "                schedule.append({'job_id': j, 'op_id': o, 'machine_id': m, 'start': start_time, 'end': end_time})",
            "                decoded_ops.add(op_key)",
            "                job_pred_end[op_key] = end_time",
            "                machine_next_start[m] = end_time",
            "                progressed = True",
            "                break",
            "        if not progressed:",
            "            return None",
            "    if decoded_ops != all_ops or len(decoded_ops) != len(op_info):",
            "        return None",
            "    return schedule, max(row['end'] for row in schedule)",
            "",
            "def apply_random_machine_move(assignment, machine_sequences, op_info, rng):",
            "    op_key = rng.choice(list(assignment.keys()))",
            "    eligible = list(op_info[op_key]['eligible'].keys())",
            "    other_machines = [m for m in eligible if m != assignment[op_key]]",
            "    if not other_machines:",
            "        return None",
            "    new_m = rng.choice(other_machines)",
            "    new_assignment = dict(assignment)",
            "    new_machine_sequences = {m: list(seq) for m, seq in machine_sequences.items()}",
            "    current_m = assignment[op_key]",
            "    new_machine_sequences[current_m] = [op for op in new_machine_sequences[current_m] if op != op_key]",
            "    new_machine_sequences[new_m].append(op_key)",
            "    new_assignment[op_key] = new_m",
            "    return new_assignment, new_machine_sequences",
            "",
            "def local_search(assignment, machine_sequences, op_info, seed):",
            "    rng = random.Random(seed)",
            "    best_schedule, best_makespan = decode_schedule(assignment, machine_sequences, op_info)",
            "    deadline = time.time() + 1.0",
            "    iteration = 0",
            "    while time.time() < deadline and iteration < 10:",
            "        iteration += 1",
            "        move = apply_random_machine_move(assignment, machine_sequences, op_info, rng)",
            "        if move is None:",
            "            continue",
            "        cand_assignment, cand_seqs = move",
            "        res = decode_schedule(cand_assignment, cand_seqs, op_info)",
            "        if res is None:",
            "            continue",
            "        cand_schedule, cand_makespan = res",
            "        if cand_makespan < best_makespan:",
            "            best_schedule = cand_schedule",
            "            best_makespan = cand_makespan",
            "    return best_schedule, best_makespan",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    instance = parse_instance(args.input)",
            "    assignment, machine_sequences = build_feasible_schedule(instance, random.Random(args.seed))",
            "    schedule, makespan = local_search(assignment, machine_sequences, instance['op_info'], args.seed)",
            "    Path(args.output).write_text(json.dumps({'format': 'standard_fjsp_schedule_v1', 'schedule': schedule, 'makespan': makespan}), encoding='utf-8')",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
    ) + "\n"


def _standard_direct_schedule_restarts_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "import random",
            "from pathlib import Path",
            "",
            "def parse_instance(path):",
            "    lines = [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]",
            "    job_count, machine_count, _max_candidates = [int(item) for item in lines[0].split()[:3]]",
            "    job_ops = {}",
            "    op_info = {}",
            "    machine_ids = set()",
            "    for job, line in enumerate(lines[1:1 + job_count], start=1):",
            "        tokens = [int(item) for item in line.split()]",
            "        pos = 0",
            "        op_count = tokens[pos]",
            "        pos += 1",
            "        job_ops[job] = op_count",
            "        for op_id in range(op_count):",
            "            cand_count = tokens[pos]",
            "            pos += 1",
            "            candidates = []",
            "            for _ in range(cand_count):",
            "                machine = tokens[pos]",
            "                duration = tokens[pos + 1]",
            "                pos += 2",
            "                machine_ids.add(machine)",
            "                candidates.append((machine, duration))",
            "            op_info[(job, op_id)] = candidates",
            "    machine_base = 0 if min(machine_ids) >= 0 and max(machine_ids) < machine_count else 1",
            "    op_info = {op_key: [(machine - machine_base, duration) for machine, duration in candidates] for op_key, candidates in op_info.items()}",
            "    return job_count, machine_count, job_ops, op_info",
            "",
            "def build_schedule(num_jobs, num_machines, job_ops, op_info, seed):",
            "    rng = random.Random(seed)",
            "    job_next_op = {job: 0 for job in range(1, num_jobs + 1)}",
            "    job_ready_time = {job: 0 for job in range(1, num_jobs + 1)}",
            "    machine_ready_time = {machine: 0 for machine in range(num_machines)}",
            "    schedule = []",
            "    scheduled_ops = set()",
            "    while len(scheduled_ops) < sum(job_ops.values()):",
            "        ready_ops = []",
            "        for job in range(1, num_jobs + 1):",
            "            op_idx = job_next_op[job]",
            "            if op_idx < job_ops[job]:",
            "                op_key = (job, op_idx)",
            "                for machine, duration in op_info[op_key]:",
            "                    start = max(job_ready_time[job], machine_ready_time[machine])",
            "                    end = start + duration",
            "                    ready_ops.append((end, start, machine, op_key, duration))",
            "        if not ready_ops:",
            "            break",
            "        min_end = min(item[0] for item in ready_ops)",
            "        best = [item for item in ready_ops if item[0] == min_end]",
            "        end, start, machine, (job, op_id), duration = rng.choice(best)",
            "        schedule.append({'job_id': job - 1, 'op_id': op_id, 'machine_id': machine, 'start': start, 'end': end})",
            "        job_next_op[job] = op_id + 1",
            "        job_ready_time[job] = end",
            "        machine_ready_time[machine] = end",
            "        scheduled_ops.add((job, op_id))",
            "    if len(scheduled_ops) != sum(job_ops.values()):",
            "        return None, None",
            "    makespan = max(item['end'] for item in schedule) if schedule else 0",
            "    return schedule, makespan",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    num_jobs, num_machines, job_ops, op_info = parse_instance(args.input)",
            "    best_schedule = None",
            "    best_makespan = float('inf')",
            "    max_restarts = 20",
            "    for trial in range(max_restarts):",
            "        sched, ms = build_schedule(num_jobs, num_machines, job_ops, op_info, args.seed + trial)",
            "        if sched is not None and ms < best_makespan:",
            "            best_schedule = sched",
            "            best_makespan = ms",
            "    if best_schedule is None:",
            "        raise RuntimeError('no feasible schedule')",
            "    Path(args.output).write_text(json.dumps({'format': 'standard_fjsp_schedule_v1', 'schedule': best_schedule, 'makespan': best_makespan}), encoding='utf-8')",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
    ) + "\n"


def _standard_random_machine_ready_source() -> str:
    return "\n".join(
        [
            "import argparse",
            "import json",
            "import random",
            "import time",
            "from pathlib import Path",
            "",
            "OpKey = tuple[int, int]",
            "",
            "def parse_instance(path):",
            "    tokens = [int(token) for token in Path(path).read_text(encoding='utf-8').split()]",
            "    idx = 0",
            "    job_count = tokens[idx]; idx += 1",
            "    machine_count = tokens[idx]; idx += 1",
            "    _max_candidates = tokens[idx]; idx += 1",
            "    op_info = {}",
            "    job_op_counts = {}",
            "    raw_machine_ids = []",
            "    for job_id in range(job_count):",
            "        op_count = tokens[idx]; idx += 1",
            "        job_op_counts[job_id] = op_count",
            "        for op_id in range(op_count):",
            "            cand_count = tokens[idx]; idx += 1",
            "            cands = {}",
            "            for _candidate in range(cand_count):",
            "                raw_machine = tokens[idx]",
            "                proc = tokens[idx + 1]",
            "                idx += 2",
            "                raw_machine_ids.append(raw_machine)",
            "                cands[raw_machine] = proc",
            "            op_info[(job_id, op_id)] = cands",
            "    machine_base = 0 if min(raw_machine_ids) >= 0 and max(raw_machine_ids) < machine_count else 1",
            "    op_info = {op_key: {machine - machine_base: proc for machine, proc in cands.items()} for op_key, cands in op_info.items()}",
            "    return {'job_count': job_count, 'machine_count': machine_count, 'job_op_counts': job_op_counts, 'op_info': op_info, 'operation_count': sum(job_op_counts.values())}",
            "",
            "def build_schedule(instance, seed):",
            "    rng = random.Random(seed)",
            "    job_next_op = {job_id: 0 for job_id in instance['job_op_counts']}",
            "    job_ready = {job_id: 0 for job_id in instance['job_op_counts']}",
            "    machine_ready = {machine: 0 for machine in range(instance['machine_count'])}",
            "    assignment = {}",
            "    scheduled_ops = set()",
            "    schedule = []",
            "    total_ops = instance['operation_count']",
            "    while len(scheduled_ops) < total_ops:",
            "        ready = []",
            "        for job_id, next_op in job_next_op.items():",
            "            if next_op < instance['job_op_counts'][job_id]:",
            "                ready.append((job_id, next_op))",
            "        if not ready:",
            "            return None",
            "        op_key = rng.choice(ready)",
            "        cands = instance['op_info'][op_key]",
            "        eligible = list(cands.keys())",
            "        chosen_machine = rng.choice(eligible)",
            "        if chosen_machine not in cands:",
            "            return None",
            "        proc = cands[chosen_machine]",
            "        start = max(job_ready[op_key[0]], machine_ready[chosen_machine])",
            "        end = start + proc",
            "        assignment[op_key] = chosen_machine",
            "        schedule.append({'job_id': op_key[0], 'op_id': op_key[1], 'machine_id': chosen_machine, 'start': start, 'end': end})",
            "        scheduled_ops.add(op_key)",
            "        job_next_op[op_key[0]] += 1",
            "        job_ready[op_key[0]] = end",
            "        machine_ready[chosen_machine] = end",
            "    if len(scheduled_ops) != total_ops:",
            "        return None",
            "    return schedule",
            "",
            "def validate_schedule(instance, schedule):",
            "    if schedule is None or len(schedule) != instance['operation_count']:",
            "        return False",
            "    seen_ops = set()",
            "    for row in schedule:",
            "        op_key = (row['job_id'], row['op_id'])",
            "        if op_key in seen_ops:",
            "            return False",
            "        seen_ops.add(op_key)",
            "        machine = row['machine_id']",
            "        proc = instance['op_info'][op_key][machine]",
            "        if row['end'] - row['start'] != proc:",
            "            return False",
            "    return len(seen_ops) == instance['operation_count']",
            "",
            "def solve(input_path, output_path, seed):",
            "    instance = parse_instance(input_path)",
            "    deadline = time.time() + 1.0",
            "    best_schedule = None",
            "    best_makespan = float('inf')",
            "    max_restarts = 3",
            "    for restart in range(max_restarts):",
            "        if time.time() > deadline:",
            "            break",
            "        candidate = build_schedule(instance, seed + restart)",
            "        if candidate is None or not validate_schedule(instance, candidate):",
            "            continue",
            "        candidate_makespan = max(item['end'] for item in candidate)",
            "        if candidate_makespan < best_makespan:",
            "            best_schedule = candidate",
            "            best_makespan = candidate_makespan",
            "    if best_schedule is None:",
            "        raise RuntimeError('no feasible schedule')",
            "    Path(output_path).write_text(json.dumps({'format': 'standard_fjsp_schedule_v1', 'schedule': best_schedule, 'makespan': best_makespan}), encoding='utf-8')",
            "",
            "def main():",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--input', required=True)",
            "    parser.add_argument('--output', required=True)",
            "    parser.add_argument('--seed', type=int, default=0)",
            "    args = parser.parse_args()",
            "    solve(args.input, args.output, args.seed)",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
    ) + "\n"


def _standardized_strong_solver_source() -> str:
    source = _strong_agent_generated_solver_source().replace("setup_time", "transition_gap")
    return source.replace("setup = transition_gap(machine_id, prev_key, op_key)", "setup = 0")


def _standard_random_reassignment_hill_climber_source() -> str:
    source = _standardized_strong_solver_source()
    start = source.index("def improve(instance, max_iterations=10):")
    end = source.index("\ndef validate_schedule")
    replacement = "\n".join(
        [
            "def apply_local_search(instance, assignment, machine_sequences, best_schedule, best_makespan, seed=0, time_limit=1.0):",
            "    rng = random.Random(seed)",
            "    start_time = time.perf_counter()",
            "    iteration = 0",
            "    while iteration < 1000 and time.perf_counter() - start_time < time_limit:",
            "        ops = list(assignment.keys())",
            "        if not ops:",
            "            break",
            "        op = rng.choice(ops)",
            "        current_machine = assignment[op]",
            "        eligible = instance['op_info'][op]['eligible']",
            "        alt_machines = [machine_id for machine_id in eligible if machine_id != current_machine]",
            "        if not alt_machines:",
            "            iteration += 1",
            "            continue",
            "        new_machine = rng.choice(alt_machines)",
            "        new_assignment = assignment.copy()",
            "        new_assignment[op] = new_machine",
            "        new_machine_sequences = {machine_id: list(sequence) for machine_id, sequence in machine_sequences.items()}",
            "        new_machine_sequences[current_machine].remove(op)",
            "        new_sequence = new_machine_sequences.setdefault(new_machine, [])",
            "        pos = rng.randint(0, len(new_sequence))",
            "        new_sequence.insert(pos, op)",
            "        candidate_schedule = decode_schedule(instance, new_assignment, new_machine_sequences)",
            "        if candidate_schedule is None:",
            "            iteration += 1",
            "            continue",
            "        candidate_makespan = max(item['end'] for item in candidate_schedule)",
            "        if candidate_makespan < best_makespan:",
            "            best_makespan = candidate_makespan",
            "            best_schedule = candidate_schedule",
            "            assignment = new_assignment",
            "            machine_sequences = new_machine_sequences",
            "        iteration += 1",
            "    return best_schedule",
            "",
            "def improve(instance, max_iterations=10):",
            "    assignment, machine_sequences = construct_initial_solution(instance, seed=0)",
            "    if assignment is None or machine_sequences is None:",
            "        return None",
            "    best_schedule = decode_schedule(instance, dict(assignment), {m: list(v) for m, v in machine_sequences.items()})",
            "    if best_schedule is None:",
            "        return None",
            "    best_makespan = max(item['end'] for item in best_schedule)",
            "    return apply_local_search(",
            "        instance, assignment, machine_sequences, best_schedule, best_makespan, seed=0, time_limit=1.0",
            "    )",
        ]
    )
    return source[:start] + replacement + source[end:]


def _standard_structured_neighborhood_skeleton_source() -> str:
    source = _standard_random_reassignment_hill_climber_source()
    insertion_point = source.index("def apply_local_search")
    helpers = "\n".join(
        [
            "def critical_blocks(schedule):",
            "    by_machine = {}",
            "    for item in schedule:",
            "        by_machine.setdefault(item['machine_id'], []).append((item['start'], item['end'], (item['job_id'], item['op_id'])))",
            "    blocks = []",
            "    for machine_id, intervals in by_machine.items():",
            "        intervals.sort()",
            "        if len(intervals) >= 2:",
            "            blocks.append((machine_id, [op_key for _start, _end, op_key in intervals]))",
            "    return blocks",
            "",
            "def generate_n8_like_neighbors(assignment, machine_sequences, blocks):",
            "    moves = []",
            "    for machine_id, block in blocks:",
            "        sequence = machine_sequences.get(machine_id, [])",
            "        for left, right in zip(block, block[1:]):",
            "            if left in sequence and right in sequence:",
            "                moves.append({'kind': 'n8_swap', 'machine': machine_id, 'left': left, 'right': right})",
            "    return moves",
            "",
            "def generate_k_insertion_neighbors(assignment, machine_sequences, blocks):",
            "    moves = []",
            "    for machine_id, block in blocks:",
            "        sequence = machine_sequences.get(machine_id, [])",
            "        for op_key in block:",
            "            if op_key not in sequence:",
            "                continue",
            "            old_pos = sequence.index(op_key)",
            "            for insertion_pos in range(max(0, old_pos - 3), min(len(sequence), old_pos + 4)):",
            "                if insertion_pos != old_pos:",
            "                    moves.append({'kind': 'k_insertion', 'machine': machine_id, 'op': op_key, 'pos': insertion_pos})",
            "    return moves",
            "",
            "def tabu_search(instance, assignment, machine_sequences, best_schedule, best_makespan, seed=0, time_limit=1.0):",
            "    tabu_until = {}",
            "    aspiration = best_makespan",
            "    blocks = critical_blocks(best_schedule)",
            "    candidate_moves = generate_n8_like_neighbors(assignment, machine_sequences, blocks)",
            "    candidate_moves += generate_k_insertion_neighbors(assignment, machine_sequences, blocks)",
            "    if not candidate_moves:",
            "        assignment, machine_sequences = perturb_state(assignment, machine_sequences, random.Random(seed))",
            "        return best_schedule",
            "    for iteration, move in enumerate(candidate_moves[:64]):",
            "        signature = tuple(sorted(move.items(), key=lambda item: str(item[0])))",
            "        if tabu_until.get(signature, -1) > iteration and best_makespan >= aspiration:",
            "            continue",
            "        tabu_until[signature] = iteration + 7",
            "    return best_schedule",
            "",
            "def perturb_state(assignment, machine_sequences, rng):",
            "    diversified_sequences = {machine_id: list(sequence) for machine_id, sequence in machine_sequences.items()}",
            "    for sequence in diversified_sequences.values():",
            "        if len(sequence) >= 2:",
            "            i = rng.randrange(len(sequence))",
            "            j = rng.randrange(len(sequence))",
            "            sequence[i], sequence[j] = sequence[j], sequence[i]",
            "            break",
            "    return dict(assignment), diversified_sequences",
            "",
        ]
    )
    source = source[:insertion_point] + helpers + source[insertion_point:]
    return source.replace(
        "    return best_schedule\n\n"
        "def improve(instance, max_iterations=10):",
        "    return tabu_search(instance, assignment, machine_sequences, best_schedule, best_makespan, seed=seed, time_limit=time_limit)\n\n"
        "def improve(instance, max_iterations=10):",
    )


def _hardcoded_toy_parser_source() -> str:
    source = _strong_agent_generated_solver_source()
    start = source.index("def parse_instance(path):")
    end = source.index("\ndef setup_time")
    replacement = "\n".join(
        [
            "def parse_instance(path):",
            "    raw_text = Path(path).read_text(encoding='utf-8')",
            "    tokens = raw_text.split()",
            "    _ = tokens",
            "    op_info = {(0, 0): {'eligible': {0: 3}, 'processing_time': 3}}",
            "    assignment = {(0, 0): 0}",
            "    machine_sequences = {0: [(0, 0)]}",
            "    total_ops = len(op_info)",
            "    return {'name': Path(path).name, 'op_info': op_info, 'assignment': assignment, 'machine_sequences': machine_sequences, 'setup_times': [], 'total_ops': total_ops}",
        ]
    )
    return source[:start] + replacement + source[end:]


def _standard_fjsp_physical_operation_line_parser_source() -> str:
    source = _standardized_strong_solver_source()
    start = source.index("def parse_instance(path):")
    end = source.index("\ndef transition_gap")
    replacement = "\n".join(
        [
            "def parse_instance(path):",
            "    lines = [line.split() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]",
            "    job_count, machine_count, _max_candidates = [int(token) for token in lines[0]]",
            "    idx = 1",
            "    op_info = {}",
            "    for job_id in range(job_count):",
            "        header = [int(token) for token in lines[idx]]",
            "        idx += 1",
            "        op_count = header[0]",
            "        for op_id in range(op_count):",
            "            op_line = [int(token) for token in lines[idx]]",
            "            idx += 1",
            "            candidate_count = op_line[0]",
            "            pos = 1",
            "            eligible = {}",
            "            for _candidate_index in range(candidate_count):",
            "                machine_id = op_line[pos] - 1",
            "                duration = op_line[pos + 1]",
            "                pos += 2",
            "                eligible[machine_id] = duration",
            "            op_info[(job_id, op_id)] = {'eligible': eligible, 'processing_time': min(eligible.values())}",
            "    total_ops = len(op_info)",
            "    return {'name': Path(path).name, 'op_info': op_info, 'machine_count': machine_count, 'setup_times': [], 'total_ops': total_ops}",
        ]
    )
    return source[:start] + replacement + source[end:]


if __name__ == "__main__":
    unittest.main()
