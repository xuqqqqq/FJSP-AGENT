from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.opencode_main import OpenCodeMainAgent
from harness_agent.orchestration.standard import StandardWorkerLoopRequest, run_standard_worker_loop
from harness_agent.workers.opencode_worker import OpenCodeWorker
from tests.test_worker_loop import _standard_agent_generated_solver_source


ROOT = Path(__file__).resolve().parents[1]


class AssignmentPipelineIntegrationTests(unittest.TestCase):
    def test_fake_opencode_runs_baseline_and_one_round_through_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_opencode = _write_role_aware_fake_opencode(tmp_path)
            output_dir = tmp_path / "run"
            manifest = run_standard_worker_loop(
                StandardWorkerLoopRequest(
                    docs=[],
                    instance_dir=ROOT / "examples",
                    pattern="standard_fjsp_tiny.fjs",
                    output_dir=output_dir,
                    project_root=ROOT,
                    worker=OpenCodeWorker(executable=str(fake_opencode), model="fake/model"),
                    main_agent=OpenCodeMainAgent(
                        executable=str(fake_opencode),
                        model="fake/model",
                        project_root=ROOT,
                        timeout_seconds=30,
                    ),
                    semantic_reviewer=None,
                    max_instances=1,
                    seeds=[0],
                    timeout_seconds=5,
                    max_workers=1,
                    max_competing_workers=1,
                    iterations=1,
                    max_steps=2,
                    max_runtime_seconds=30,
                    in_round_repair_attempts=0,
                    apply_worker_changes=True,
                    promotion_repeats=1,
                    experiment_id="assignment_pipeline",
                )
            )

            self.assertEqual("ok", manifest["status"], manifest)
            self.assertEqual(1, manifest["baseline_summary"]["valid"])
            self.assertEqual(1, manifest["round_count"])

            loop_root = output_dir / "worker_loop"
            baseline_assignment = loop_root / "agent_generated_baseline" / "worker_assignment.json"
            round_assignment = loop_root / "round_000" / "worker_assignment.json"
            self.assertTrue(baseline_assignment.is_file())
            self.assertTrue(round_assignment.is_file())
            baseline_assignment_payload = json.loads(baseline_assignment.read_text(encoding="utf-8"))
            round_assignment_payload = json.loads(round_assignment.read_text(encoding="utf-8"))
            self.assertEqual(
                "Create the smallest complete standalone legal solver: parser, simple construction, CLI/output, and deterministic fallback only.",
                baseline_assignment_payload["objective"],
            )
            self.assertEqual(
                "Implement the complete selected package.",
                round_assignment_payload["objective"],
            )
            self.assertTrue(
                (loop_root / "agent_generated_baseline" / "main_agent" / "planning_packet.json").is_file()
            )
            direction = json.loads(
                (loop_root / "round_000" / "main_agent" / "direction_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual("opencode_main_agent", direction["planner"])

            for attempt_dir in (loop_root / "agent_generated_baseline", loop_root / "round_000"):
                budget = json.loads(
                    (attempt_dir / "worker" / "opencode_context_budget.json").read_text(encoding="utf-8")
                )
                self.assertFalse(budget["full_context_packet_visible"])
                self.assertLessEqual(budget["total_attached_chars"], 12_000)
                prompt = (attempt_dir / "worker" / "opencode_prompt.md").read_text(encoding="utf-8")
                self.assertNotIn("method_package_catalog", prompt)
                self.assertNotIn("experience_memory", prompt)
                runtime = json.loads(
                    (attempt_dir / "worker" / "opencode_runtime_config.json").read_text(encoding="utf-8")
                )
                permissions = runtime["agent"]["algoforge-worker"]["permission"]
                bash = permissions["bash"]
                self.assertEqual("deny", bash["*"])
                self.assertIn("python .algoforge_worker_runtime/run_smoke.py", bash)
                self.assertFalse(any("agent_generated_fjsp_solver.py *" in command for command in bash))
                self.assertEqual("deny", permissions["skill"]["*"])
                self.assertEqual("allow", permissions["skill"]["fjsp-solver-foundation-worker"])
                if attempt_dir.name == "agent_generated_baseline":
                    self.assertNotIn("fjsp-coupled-local-search-worker", permissions["skill"])
                else:
                    self.assertEqual("allow", permissions["skill"]["fjsp-coupled-local-search-worker"])

            baseline_worktree = Path(manifest["baseline_generation"]["worktree"])
            self.assertTrue(
                (baseline_worktree / ".opencode" / "skills" / "fjsp-solver-foundation-worker" / "SKILL.md").is_file()
            )
            self.assertFalse(
                (baseline_worktree / ".opencode" / "skills" / "fjsp-coupled-local-search-worker").exists()
            )
            self.assertFalse(
                (baseline_worktree / ".opencode" / "skills" / "fjsp-constructive-search-worker").exists()
            )
            self.assertFalse((baseline_worktree / ".opencode" / "package.json").exists())
            self.assertFalse(
                (baseline_worktree / ".codex" / "skills" / "fjsp-solver-optimizer" / "SKILL.md").exists()
            )
            wrapper = baseline_worktree / ".algoforge_worker_runtime" / "run_smoke.py"
            completed = subprocess.run(
                ["git", "-C", str(baseline_worktree), "rev-parse", "--show-toplevel"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                str(baseline_worktree.resolve()).replace("\\", "/"),
                completed.stdout.strip().replace("\\", "/"),
            )
            first = subprocess.run(
                [sys.executable, str(wrapper)],
                cwd=baseline_worktree,
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                [sys.executable, str(wrapper)],
                cwd=baseline_worktree,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, first.returncode)
            self.assertEqual(3, second.returncode)
            self.assertNotIn("ModuleNotFoundError", first.stderr + first.stdout)


def _write_role_aware_fake_opencode(tmp_path: Path) -> Path:
    script = tmp_path / "fake_role_opencode.py"
    solver_source = _standard_agent_generated_solver_source()
    solver_source = solver_source.replace(
        "def solve(input_path, seed=0, max_iterations=1):",
        "\n".join(
            [
                "def validate_schedule(schedule, instance):",
                "    expected_ops = set(instance['op_info'])",
                "    seen_ops = {(item['job_id'], item['op_id']) for item in schedule}",
                "    if seen_ops != expected_ops or len(schedule) != len(expected_ops):",
                "        return False",
                "    intervals = {}",
                "    for item in schedule:",
                "        op_key = (item['job_id'], item['op_id'])",
                "        eligible = instance['op_info'][op_key]['eligible']",
                "        if item['machine_id'] not in eligible:",
                "            return False",
                "        if item['end'] - item['start'] != eligible[item['machine_id']]:",
                "            return False",
                "        intervals.setdefault(item['machine_id'], []).append((item['start'], item['end']))",
                "    return all(left[1] <= right[0] for values in intervals.values() for left, right in zip(sorted(values), sorted(values)[1:]))",
                "",
                "def solve(input_path, seed=0, max_iterations=1):",
            ]
        ),
    )
    solver_source = solver_source.replace(
        "    return {\n        'format': 'standard_fjsp_schedule_v1',",
        "    if not validate_schedule(best_schedule, instance):\n"
        "        raise ValueError('generated schedule failed source self-check')\n"
        "    return {\n        'format': 'standard_fjsp_schedule_v1',",
    )
    solver_source = solver_source.replace(
        "    parser.add_argument('--seed', type=int, default=0)",
        "    parser.add_argument('--seed', type=int, default=0)\n"
        "    parser.add_argument('--time-limit-sec', type=float, default=1.0)",
    )
    script.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import json",
                "import sys",
                "args = sys.argv[1:]",
                "agent = args[args.index('--agent') + 1] if '--agent' in args else ''",
                "if agent == 'algoforge-main':",
                "    packet_paths = [Path(arg.split('=', 1)[1]) for arg in args if arg.startswith('--file=') and 'planning_packet' in arg]",
                "    packet = json.loads(packet_paths[0].read_text(encoding='utf-8')) if packet_paths else {}",
                "    planning_stage = packet.get('planning_stage')",
                "    phase = packet.get('phase', 'improvement')",
                "    direction_id = 'baseline-direction' if phase == 'baseline' else 'd000'",
                "    if planning_stage == 'direction_selection':",
                "        planned = {",
                "            'direction_selection': {",
                "                'direction_id': direction_id,",
                "                'method_family': 'coupled_local_search',",
                "                'method_families': [{'id': 'coupled_local_search', 'role': 'primary'}],",
                "                'primary_search_pressure': 'coupled',",
                "                'diagnosis': 'The instance exposes both machine-choice and sequencing pressure.',",
                "                'measured_evidence': ['flexible_operation_ratio=0.5'],",
                "                'uncertainties': ['No incumbent critical-block evidence is available yet.'],",
                "                'alternatives_considered': ['Construction alone cannot exercise deep local search.'],",
                "                'selection_rationale': 'Query implementation knowledge before assigning complete work.',",
                "                'knowledge_query': ['local_search', 'critical_path', 'assignment_aware_local_search'],",
                "            }",
                "        }",
                "    else:",
                "        planned = {",
                "            'direction_plan': {",
                "                'direction_id': direction_id,",
                "                'title': 'Adapt the selected complete method package',",
                "                'strategy_type': 'baseline_constructor' if phase == 'baseline' else 'local_search_operator',",
                "                'hypothesis': 'The selected complete package can produce an evaluator-checkable candidate.',",
                "                'diagnosis': 'Use the active contract and preserve any incumbent.',",
                "                'reasoning_trace': [",
                "                    {",
                "                        'stage': f'step-{index}',",
                "                        'summary': f'Public evidence analysis step {index}.',",
                "                        'evidence': ['The promoted solver and evaluator evidence are available.'],",
                "                        'inference': 'One bounded mutation is safer than a rewrite.',",
                "                        'decision': 'Preserve the incumbent structure.',",
                "                        'next_check': 'Compare validity, makespan, and runtime.',",
                "                    } for index in range(3)",
                "                ],",
                "                'incumbent_assessment': {",
                "                    'verified_capabilities': ['The promoted solver exposes a reachable solve entrypoint.'],",
                "                    'implementation_limits': ['The current bounded search controls remain narrow.'],",
                "                    'bottleneck_hypotheses': ['Narrow coverage may limit candidate diversity.'],",
                "                    'evidence_refs': ['examples/agent_generated_fjsp_solver.py:solve'],",
                "                    'unknowns': ['Runtime expansion counts are not measured.'],",
                "                },",
                "                'next_mutation': {",
                "                    'target_symbols': ['solve'],",
                "                    'change': 'Refine one existing bounded search control without replacing the solver.',",
                "                    'preserve': ['Preserve parser, decoder, and incumbent fallback.'],",
                "                    'expected_effect': 'Increase useful candidate coverage under the same deadline.',",
                "                    'falsification_metrics': ['validity', 'makespan', 'runtime'],",
                "                },",
                "                'alternatives_considered': ['A partial package would not satisfy completion.'],",
                "                'selection_rationale': 'The selected package matches the requested knowledge tags.',",
                "                'method_package_id': 'standard_fjsp_awls_hgtsa',",
                "                'activation_checks': [",
                "                    {",
                "                        'id': 'solver_self_check',",
                "                        'path': 'diagnostics.solver_contract_self_check.enabled',",
                "                        'operator': 'truthy',",
                "                        'expected': True,",
                "                    }",
                "                ],",
                "            },",
                "            'worker_assignment': {'objective': 'Implement the complete selected package.'},",
                "        }",
                "    print(json.dumps({'type': 'text', 'part': {'type': 'text', 'text': json.dumps(planned)}}))",
                "else:",
                "    target = Path('examples/agent_generated_fjsp_solver.py')",
                "    target.parent.mkdir(parents=True, exist_ok=True)",
                f"    target.write_text({solver_source!r}, encoding='utf-8')",
                "    print(json.dumps({'type': 'text', 'part': {'type': 'text', 'text': 'worker completed'}}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp_path / "fake_role_opencode.cmd"
        wrapper.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        wrapper = tmp_path / "fake_role_opencode"
        wrapper.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


if __name__ == "__main__":
    unittest.main()
