from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from harness_agent.workers.deepseek_worker import (
    DeepSeekWorker,
    apply_code_edit_proposal,
    extract_json_object,
    insert_after_anchor,
    insert_before_anchor,
    priority_worker_context,
    render_code_edit_markdown,
)


class DeepSeekWorkerProposalAuditTests(unittest.TestCase):
    def test_proposal_audit_records_project_intake_usage(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_intake()
        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Adjust examples/standard_fjsp_solver.py using the project map.",
                "strategy_intent": "Use project_intake to locate the constructive solver and leave evaluator files unchanged.",
                "rule_operator_hypotheses": [
                    {
                        "name": "critical_block_bias",
                        "type": "local_search_operator",
                        "novelty": "Biases the existing search toward critical-block moves instead of another dispatch-only tweak.",
                        "expected_effect": "Reduce average makespan under the fixed evaluator.",
                        "evidence_used": ["project_intake.core_algorithm_files", "loop_feedback.previous_rounds"],
                        "target_files": ["examples/standard_fjsp_solver.py"],
                        "ablation_plan": "Run the same suite with and without the critical-block bias.",
                    }
                ],
                "changes": [
                    {
                        "path": "examples/standard_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": "print('solver')\n",
                        "rationale": "The intake marks this as an entry/core algorithm file.",
                    }
                ],
                "context_usage": {
                    "used_project_intake": True,
                    "referenced_files": ["examples/standard_fjsp_solver.py", "examples/standard_fjsp_evaluator.py"],
                    "notes": "The solver is editable; the evaluator is only a validation reference.",
                },
                "quick_test_plan": "python -m compileall harness_agent examples",
                "risk_notes": ["Do not edit evaluator semantics."],
            },
            context,
        )

        audit = normalized["proposal_audit"]
        self.assertTrue(audit["project_intake_present"])
        self.assertTrue(audit["declared_project_intake_used"])
        self.assertIn("examples/standard_fjsp_solver.py", audit["detected_referenced_intake_files"])
        self.assertEqual(1, audit["operator_lineage"]["hypothesis_count"])
        self.assertEqual(["local_search_operator"], audit["operator_lineage"]["hypothesis_types"])
        self.assertEqual(
            ["examples/standard_fjsp_solver.py"],
            audit["operator_lineage"]["target_files_overlap_changes"],
        )
        self.assertEqual(["examples/standard_fjsp_solver.py"], audit["changed_core_algorithm_files"])
        self.assertEqual([], audit["changed_validator_files"])
        self.assertIn("python -m compileall harness_agent examples", audit["referenced_test_commands"])
        self.assertNotIn("project_intake_present_but_not_referenced", audit["warnings"])

        markdown = render_code_edit_markdown(normalized)
        self.assertIn("## Context Usage", markdown)
        self.assertIn("## Rule / Operator Hypotheses", markdown)
        self.assertIn("critical_block_bias", markdown)
        self.assertIn("## Proposal Audit", markdown)
        self.assertIn("changed_core_algorithm_files", markdown)

    def test_proposal_audit_warns_when_intake_is_ignored(self) -> None:
        worker = DeepSeekWorker()
        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Make a small edit.",
                "strategy_intent": "Try a solver tweak.",
                "changes": [],
                "quick_test_plan": "",
                "risk_notes": [],
            },
            _context_packet_with_intake(),
        )

        self.assertIn("project_intake_present_but_not_referenced", normalized["proposal_audit"]["warnings"])

    def test_extract_json_object_accepts_trailing_model_text(self) -> None:
        payload = extract_json_object('{"summary":"ok","changes":[]} extra notes that should be ignored')

        self.assertEqual("ok", payload["summary"])
        self.assertEqual([], payload["changes"])

    def test_local_patch_actions_are_applied_without_replacing_full_file(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_intake()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "examples" / "standard_fjsp_solver.py"
            target.parent.mkdir(parents=True)
            target.write_text("alpha = 1\nbeta = 2\n", encoding="utf-8")

            normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
                {
                    "summary": "Use a local patch.",
                    "strategy_intent": "Avoid full-file replacement for large solver files.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "small_patch",
                            "type": "parameter_policy",
                            "novelty": "Uses a local text replacement instead of rewriting the parser.",
                            "expected_effect": "Keeps evaluator-visible behavior auditable.",
                            "evidence_used": ["project_intake.core_algorithm_files"],
                            "target_files": ["examples/standard_fjsp_solver.py"],
                            "ablation_plan": "Compare with the baseline value.",
                        }
                    ],
                    "changes": [
                        {
                            "path": "examples/standard_fjsp_solver.py",
                            "action": "insert_before",
                            "anchor": "beta = 2\n",
                            "content": "delta = 5\n",
                            "rationale": "Small controlled pre-insertion.",
                        },
                        {
                            "path": "examples/standard_fjsp_solver.py",
                            "action": "text_replace",
                            "old": "beta = 2\n",
                            "new": "beta = 3\n",
                            "rationale": "Small controlled replacement.",
                        },
                        {
                            "path": "examples/standard_fjsp_solver.py",
                            "action": "insert_after",
                            "anchor": "alpha = 1\n",
                            "content": "gamma = 4\n",
                            "rationale": "Small controlled insertion.",
                        },
                    ],
                    "context_usage": {
                        "used_project_intake": True,
                        "referenced_files": ["examples/standard_fjsp_solver.py"],
                        "notes": "Patch the editable solver only.",
                    },
                    "quick_test_plan": "python -m compileall harness_agent examples",
                    "risk_notes": "Single string risk note should stay one note.",
                },
                context,
            )

            changed = apply_code_edit_proposal(proposal=normalized, worktree_path=root, context=context)

            self.assertEqual(
                [
                    "examples/standard_fjsp_solver.py",
                    "examples/standard_fjsp_solver.py",
                    "examples/standard_fjsp_solver.py",
                ],
                changed,
            )
            self.assertEqual("alpha = 1\ngamma = 4\ndelta = 5\nbeta = 3\n", target.read_text(encoding="utf-8"))
            self.assertEqual(["Single string risk note should stay one note."], normalized["risk_notes"])

    def test_insert_after_adds_line_boundaries_when_anchor_is_bare_line(self) -> None:
        text = "machine_last_job = [-1] * n_machines\nschedule = []\n"
        updated = insert_after_anchor(
            text,
            "machine_last_job = [-1] * n_machines",
            "remaining_work = []\nfor job in jobs:\n    remaining_work.append(0)",
        )

        self.assertEqual(
            "machine_last_job = [-1] * n_machines\n"
            "remaining_work = []\n"
            "for job in jobs:\n"
            "    remaining_work.append(0)\n"
            "schedule = []\n",
            updated,
        )

    def test_insert_after_adds_trailing_line_boundary_when_anchor_contains_newline(self) -> None:
        text = "alpha = 1\nbeta = 2\n"
        updated = insert_after_anchor(text, "alpha = 1\n", "gamma = 4")

        self.assertEqual("alpha = 1\ngamma = 4\nbeta = 2\n", updated)

    def test_insert_before_adds_line_boundaries_for_top_level_helper(self) -> None:
        text = "import json\n\ndef main():\n    run()\n"
        updated = insert_before_anchor(
            text,
            "def main():",
            "def helper():\n    return 1",
        )

        self.assertEqual(
            "import json\n\n"
            "def helper():\n"
            "    return 1\n"
            "def main():\n"
            "    run()\n",
            updated,
        )

    def test_replace_slot_block_action_rewrites_only_confirmed_slot(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_slot_manifest()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "examples" / "standard_fjsp_local_search_solver.py"
            target.parent.mkdir(parents=True)
            target.write_text(
                "def generate_structured_neighbors():\n"
                "    before()\n"
                "    # SLOT neighborhood_actions START\n"
                "    old_move()\n"
                "    # SLOT neighborhood_actions END\n"
                "    after()\n",
                encoding="utf-8",
            )

            normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
                {
                    "summary": "Replace the selected neighborhood slot.",
                    "strategy_intent": "Use the manifest-confirmed code slot instead of a long text_replace old block.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "slot_guided_move",
                            "type": "local_search_operator",
                            "novelty": "Edits only the confirmed slot.",
                            "expected_effect": "Keeps IO and markers stable.",
                            "target_files": ["examples/standard_fjsp_local_search_solver.py"],
                        }
                    ],
                    "changes": [
                        {
                            "action": "replace_slot_block",
                            "slot_id": "local_search_neighborhood_actions",
                            "content": (
                                "```python\n"
                                "    new_move()\n"
                                "```\n"
                            ),
                            "rationale": "Small slot replacement.",
                        }
                    ],
                    "quick_test_plan": "python -m compileall examples/standard_fjsp_local_search_solver.py",
                },
                context,
            )

            changed = apply_code_edit_proposal(proposal=normalized, worktree_path=root, context=context)

            self.assertEqual(["examples/standard_fjsp_local_search_solver.py"], changed)
            self.assertEqual([], normalized["rejected_changes"])
            self.assertEqual(
                "def generate_structured_neighbors():\n"
                "    before()\n"
                "    # SLOT neighborhood_actions START\n"
                "    new_move()\n"
                "    # SLOT neighborhood_actions END\n"
                "    after()\n",
                target.read_text(encoding="utf-8"),
            )

    def test_replace_slot_block_rejects_unconfirmed_slot(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_slot_manifest(user_confirmed=False)

        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "changes": [
                    {
                        "path": "examples/standard_fjsp_local_search_solver.py",
                        "action": "replace_slot_block",
                        "slot_id": "local_search_neighborhood_actions",
                        "content": "    new_move()\n",
                    }
                ],
            },
            context,
        )

        self.assertEqual([], normalized["changes"])
        self.assertIn("must be user_confirmed", normalized["rejected_changes"][0]["reason"])

    def test_replace_slot_block_uses_manifest_target_over_worker_path(self) -> None:
        worker = DeepSeekWorker()
        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "changes": [
                    {
                        "path": "examples/standard_fjsp_local_search_sdst.py",
                        "action": "replace_slot_block",
                        "slot_id": "local_search_neighborhood_actions",
                        "content": "    new_move()\n",
                    }
                ],
            },
            _context_packet_with_slot_manifest(),
        )

        self.assertEqual([], normalized["rejected_changes"])
        self.assertEqual("examples/standard_fjsp_local_search_solver.py", normalized["changes"][0]["path"])

    def test_iteration_contract_rejects_full_solver_rewrite(self) -> None:
        worker = DeepSeekWorker()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "examples" / "agent_generated_fjsp_solver.py"
            target.parent.mkdir(parents=True)
            target.write_text("print('incumbent')\n", encoding="utf-8")
            context = _context_packet_with_iteration_contract(root)

            normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
                {
                    "summary": "Rewrite the solver.",
                    "strategy_intent": "Replace the incumbent with a fresh solver.",
                    "rule_operator_hypotheses": [
                        {
                            "name": "fresh_solver_rewrite",
                            "type": "dispatch_rule",
                            "novelty": "Claims to differ from rollback history.",
                            "expected_effect": "Lower makespan.",
                            "target_files": ["examples/agent_generated_fjsp_solver.py"],
                        }
                    ],
                    "changes": [
                        {
                            "path": "examples/agent_generated_fjsp_solver.py",
                            "action": "create_or_replace",
                            "content": "print('new solver')\n",
                            "rationale": "Full rewrite.",
                        },
                        {
                            "path": "examples/new_helper.py",
                            "action": "create_or_replace",
                            "content": "VALUE = 1\n",
                            "rationale": "New helper files remain allowed.",
                        },
                    ],
                    "quick_test_plan": "python -m compileall harness_agent examples",
                },
                context,
            )

            self.assertEqual(["examples/new_helper.py"], [item["path"] for item in normalized["changes"]])
            self.assertEqual("examples/agent_generated_fjsp_solver.py", normalized["rejected_changes"][0]["path"])
            self.assertIn("forbids create_or_replace", normalized["rejected_changes"][0]["reason"])

    def test_iteration_contract_allows_full_solver_rewrite_when_incumbent_is_invalid(self) -> None:
        worker = DeepSeekWorker()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "examples" / "agent_generated_fjsp_solver.py"
            target.parent.mkdir(parents=True)
            target.write_text("broken = True\n", encoding="utf-8")
            context = _context_packet_with_iteration_contract(root, incumbent_key=[float("-inf")], valid=0)

            normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
                {
                    "summary": "Repair an invalid incumbent.",
                    "strategy_intent": "Replace the invalid generated solver so legality can be restored.",
                    "changes": [
                        {
                            "path": "examples/agent_generated_fjsp_solver.py",
                            "action": "create_or_replace",
                            "content": "print('repair')\n",
                            "rationale": "Invalid incumbents need full-entrypoint repair.",
                        }
                    ],
                    "quick_test_plan": "python -m compileall harness_agent examples",
                },
                context,
            )

            self.assertEqual(["examples/agent_generated_fjsp_solver.py"], [item["path"] for item in normalized["changes"]])
            self.assertEqual([], normalized["rejected_changes"])

            prompt_context = priority_worker_context(context)
            self.assertIn('"incumbent_requires_legality_repair": true', prompt_context)
            self.assertIn("create_or_replace of the solver entrypoint is allowed", prompt_context)

    def test_priority_worker_context_frontloads_incumbent_source(self) -> None:
        context = _context_packet_with_intake()
        context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
        context["worker_instruction"] = {"incremental_edit_rule": "Patch only."}
        context["loop_feedback"] = {"incumbent_key_before": [-1160.0], "previous_rounds": []}
        context["incumbent_code_context"] = {
            "source": "promoted_incumbent_worktree",
            "purpose": "test",
            "files": [
                {
                    "relative_path": "examples/agent_generated_fjsp_solver.py",
                    "chars": 21,
                    "truncated": False,
                    "snippet": "def schedule(): pass\n",
                }
            ],
        }

        prompt_context = priority_worker_context(context)

        self.assertIn("improvement_round", prompt_context)
        self.assertIn("examples/agent_generated_fjsp_solver.py", prompt_context)
        self.assertIn("def schedule(): pass", prompt_context)

    def test_priority_worker_context_frontloads_relevant_knowledge_cards(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "fjsp_sdst",
            "description": "Improve an agent_generated FJSP-SDST solver on oddla20.",
            "instances": [{"id": "oddla20", "path": "instances/oddla20.txt"}],
        }
        context["evaluator_protocol"] = {
            "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
            "quick_test_command": "python -m compileall examples/agent_generated_fjsp_solver.py",
        }
        context["problem_family_capability"] = {
            "knowledge_tags": ["fjsp", "sdst", "sequence_dependent_setup"],
            "specialization_hooks": ["setup_aware_dispatch_or_insertion"],
        }
        context["knowledge_cards"] = [
            {
                "path": "knowledge/benchmarks/fjsp_benchmark_scope.md",
                "chars": 4000,
                "truncated": False,
                "snippet": "Generic benchmark scope." * 200,
            },
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 5400,
                "truncated": False,
                "snippet": (
                    "Use this card for agent-generated FJSP-SDST solver improvement. "
                    "Recover operation-level list scheduler and setup-aware operation-level dispatch."
                ),
            },
        ]

        prompt_context = priority_worker_context(context)

        self.assertIn("priority_knowledge_cards", prompt_context)
        self.assertIn("fjsp_sdst_agent_generated_search_memory_20260707.md", prompt_context)
        self.assertIn("operation-level list scheduler", prompt_context)

    def test_proposal_audit_warns_when_priority_knowledge_is_ignored(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_intake()
        context["task"] = {"problem_family": "fjsp_sdst", "description": "agent_generated SDST run"}
        context["evaluator_protocol"] = {
            "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}"
        }
        context["problem_family_capability"] = {"knowledge_tags": ["sdst", "sequence_dependent_setup"]}
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 5400,
                "truncated": False,
                "snippet": "Preserve operation-level setup-aware dispatch and multi-start.",
            }
        ]

        ignored = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Change a tie-break.",
                "strategy_intent": "Try a small dispatch change.",
                "rule_operator_hypotheses": [
                    {
                        "name": "tie_break",
                        "type": "dispatch_rule",
                        "target_files": ["examples/agent_generated_fjsp_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "path": "examples/agent_generated_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": "print('solver')\n",
                    }
                ],
                "quick_test_plan": "python -m compileall harness_agent examples",
            },
            context,
        )

        self.assertIn("priority_knowledge_cards_not_referenced", ignored["proposal_audit"]["warnings"])

        referenced = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Follow the RAG brief.",
                "strategy_intent": "Use knowledge_cards to preserve the setup-aware constructive skeleton.",
                "rule_operator_hypotheses": [
                    {
                        "name": "knowledge_guided_dispatch",
                        "type": "dispatch_rule",
                        "evidence_used": ["knowledge_cards", "loop_feedback.previous_rounds"],
                        "target_files": ["examples/agent_generated_fjsp_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "path": "examples/agent_generated_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": "print('solver')\n",
                    }
                ],
                "quick_test_plan": "python -m compileall harness_agent examples",
            },
            context,
        )

        self.assertNotIn("priority_knowledge_cards_not_referenced", referenced["proposal_audit"]["warnings"])


def _context_packet_with_intake() -> dict[str, object]:
    return {
        "edit_policy": {
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "project_intake": {
            "status": "ok",
            "summary": {
                "entry_files": ["examples/standard_fjsp_solver.py", "examples/standard_fjsp_evaluator.py"],
                "core_algorithm_files": ["examples/standard_fjsp_solver.py"],
                "validator_files": ["examples/standard_fjsp_evaluator.py"],
                "benchmark_files": ["harness_agent/benchmark_suite.py"],
                "dependency_files": ["pyproject.toml"],
                "test_commands": [
                    {
                        "source": "contract.quick_test",
                        "command": "python -m compileall harness_agent examples",
                    }
                ],
                "risk_flags": [{"code": "dirty_worktree", "message": "test risk"}],
            },
        },
    }


def _context_packet_with_slot_manifest(*, user_confirmed: bool = True) -> dict[str, object]:
    return {
        "edit_policy": {
            "allowed_paths": ["examples", "harness_agent", "configs"],
            "forbidden_paths": [".git", "outputs"],
        },
        "slot_manifest": {
            "exists": True,
            "status": "confirmed",
            "confirmation_required": False,
            "slots": [
                {
                    "slot_id": "local_search_neighborhood_actions",
                    "title": "局部搜索邻域动作生成",
                    "target_file": "examples/standard_fjsp_local_search_solver.py",
                    "marker_start": "# SLOT neighborhood_actions START",
                    "marker_end": "# SLOT neighborhood_actions END",
                    "purpose": "Generate candidate moves.",
                    "inputs": ["instance", "state", "decoded", "rng", "neighbor_limit"],
                    "outputs": ["list[tuple[Move, SearchState]]"],
                    "invariants": ["Keep parser/evaluator/IO fixed."],
                    "allowed_edits": ["Edit only the marked block."],
                    "forbidden_edits": ["Do not edit evaluator semantics."],
                    "validation_commands": ["python -m compileall examples/standard_fjsp_local_search_solver.py"],
                    "knowledge_tags": ["neighborhood"],
                    "user_confirmed": user_confirmed,
                }
            ],
        },
    }


def _context_packet_with_iteration_contract(
    incumbent_root: Path,
    *,
    incumbent_key: list[float] | None = None,
    valid: int = 1,
) -> dict[str, object]:
    context = _context_packet_with_intake()
    context["loop_feedback"] = {
        "incumbent_worktree": str(incumbent_root),
        "incumbent_key_before": incumbent_key or [-100.0],
        "baseline_summary": {"total": 1, "valid": valid},
    }
    context["iteration_edit_contract"] = {
        "mode": "incremental_after_baseline",
        "whole_file_rewrite_policy": "Do not rewrite existing solver files during improvement rounds.",
    }
    return context


if __name__ == "__main__":
    unittest.main()
