from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.agentic_review import analyze_rejected_judgment, judge_worker_result
from harness_agent.solver_quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.worker import WorkerResult


class AgenticReviewQualityContractTests(unittest.TestCase):
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
        "variant_handling": ["sequence_dependent_setup is applied between adjacent operations on each machine."],
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


if __name__ == "__main__":
    unittest.main()
