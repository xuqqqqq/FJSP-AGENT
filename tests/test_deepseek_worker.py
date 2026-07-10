from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from harness_agent.workers.deepseek_worker import (
    DeepSeekWorker,
    apply_code_edit_proposal,
    compact_priority_knowledge_cards,
    extract_json_object,
    insert_after_anchor,
    insert_before_anchor,
    priority_context_max_chars,
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

    def test_normalization_rejects_top_level_helper_inserted_after_def_line(self) -> None:
        worker = DeepSeekWorker()
        context = _context_packet_with_intake()

        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Add a helper in the wrong place.",
                "strategy_intent": "Simulate the dangling def syntax failure pattern.",
                "rule_operator_hypotheses": [
                    {
                        "name": "unsafe_helper_insert",
                        "type": "repair_rule",
                        "target_files": ["examples/standard_fjsp_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "path": "examples/standard_fjsp_solver.py",
                        "action": "insert_after",
                        "anchor": "def main():",
                        "content": "def new_top_level_helper():\n    return 1\n",
                    }
                ],
                "quick_test_plan": "python -m compileall examples",
            },
            context,
        )

        self.assertEqual([], normalized["changes"])
        self.assertEqual("examples/standard_fjsp_solver.py", normalized["rejected_changes"][0]["path"])
        self.assertIn("insert_after a def/class line", normalized["rejected_changes"][0]["reason"])

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

    def test_priority_worker_context_keeps_full_generated_solver_edit_site(self) -> None:
        context = _context_packet_with_intake()
        context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
        context["loop_feedback"] = {"incumbent_key_before": [-1267.0], "previous_rounds": []}
        source = (
            "def parse_instance(path):\n"
            "    return path\n\n"
            + ("# generated solver body\n" * 360)
            + "def local_search_insertion(schedule):\n"
            "    return schedule\n\n"
            "def main():\n"
            "    best_schedule = build_schedule()\n"
            + ("    # main refinement setup\n" * 80)
            + "    improved_schedule, improved_makespan = local_search_insertion(\n"
            "        best_schedule\n"
            "    )\n"
            "    return improved_schedule, improved_makespan\n"
        )
        context["incumbent_code_context"] = {
            "source": "promoted_incumbent_worktree",
            "purpose": "test full generated solver edit site",
            "files": [
                {
                    "relative_path": "examples/agent_generated_fjsp_solver.py",
                    "chars": len(source),
                    "truncated": False,
                    "snippet": source,
                }
            ],
        }

        prompt_context = priority_worker_context(context)

        self.assertIn("improved_schedule, improved_makespan = local_search_insertion(", prompt_context)
        self.assertIn("return improved_schedule, improved_makespan", prompt_context)

    def test_priority_context_max_chars_is_configurable_and_clamped(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(48000, priority_context_max_chars())
        with patch.dict("os.environ", {"ALGOFORGE_PRIORITY_CONTEXT_MAX_CHARS": "48000"}, clear=True):
            self.assertEqual(48000, priority_context_max_chars())
        with patch.dict("os.environ", {"ALGOFORGE_PRIORITY_CONTEXT_MAX_CHARS": "1000"}, clear=True):
            self.assertEqual(12000, priority_context_max_chars())
        with patch.dict("os.environ", {"ALGOFORGE_PRIORITY_CONTEXT_MAX_CHARS": "999999"}, clear=True):
            self.assertEqual(60000, priority_context_max_chars())
        with patch.dict("os.environ", {"ALGOFORGE_PRIORITY_CONTEXT_MAX_CHARS": "wide"}, clear=True):
            self.assertEqual(48000, priority_context_max_chars())

    def test_priority_worker_context_keeps_exact_rejected_edit_for_repair(self) -> None:
        context = _context_packet_with_intake()
        context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
        context["loop_feedback"] = {
            "incumbent_key_before": [-1267.0],
            "previous_rounds": [],
            "current_round_repair": {
                "status": "repair_required",
                "attempt_index": 1,
                "max_repair_attempts": 2,
                "repair_targets": {
                    "agent_generated_solver_quality_risks": [
                        "agent_generated_solver: missing base capabilities: stable_operation_identity"
                    ],
                    "agent_generated_solver_expected_contract": {
                        "active_features": ["alternative_machines"],
                        "capabilities": ["stable_operation_identity"],
                    },
                },
                "previous_attempts": [
                    {
                        "attempt_index": 0,
                        "failure_signatures": ["proposal_apply_rejections"],
                        "proposal_diagnostics": {
                            "summary": "Guessed a stale local-search call.",
                            "apply_rejections": [
                                {
                                    "path": "examples/agent_generated_fjsp_solver.py",
                                    "reason": "old text not found",
                                }
                            ],
                            "rejected_edits": [
                                {
                                    "path": "examples/agent_generated_fjsp_solver.py",
                                    "reason": "old text not found",
                                    "action": "text_replace",
                                    "old": "best_schedule, best_makespan = local_search_insertion(best_schedule)",
                                }
                            ],
                        },
                    }
                ],
                "must_do": ["Repair the rejected edit."],
                "avoid": ["proposal_apply_rejections"],
            },
        }

        prompt_context = priority_worker_context(context)

        self.assertIn("old text not found", prompt_context)
        self.assertIn("best_schedule, best_makespan = local_search_insertion(best_schedule)", prompt_context)
        self.assertIn("repair_targets", prompt_context)
        self.assertIn("stable_operation_identity", prompt_context)

    def test_priority_worker_context_keeps_incumbent_before_large_rag_cards(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "fjsp_sdst",
            "description": "Improve an agent_generated FJSP-SDST solver on oddla20.",
            "instances": [{"id": "oddla20", "path": "instances/oddla20.txt"}],
        }
        context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
        context["loop_feedback"] = {
            "incumbent_key_before": [-1277.0],
            "previous_rounds": [
                {
                    "round_index": 1,
                    "decision": "rolled_back",
                    "proposal_diagnostics": {
                        "summary": "Patch failed because the worker guessed an anchor.",
                        "proposal_audit": {"rejected_change_count": 2},
                    },
                    "candidate_summary": {
                        "validation_summary": {
                            "top_errors": ["anchor text not found", "old text not found"],
                        }
                    },
                }
            ],
        }
        context["incumbent_code_context"] = {
            "source": "promoted_incumbent_worktree",
            "purpose": "Current promoted solver source for surgical patches.",
            "files": [
                {
                    "relative_path": "examples/agent_generated_fjsp_solver.py",
                    "chars": 4800,
                    "truncated": False,
                    "snippet": (
                        "def parse_instance(path):\n"
                        "    return load_hudata(path)\n\n"
                        "def main():\n"
                        "    schedule = build_current_incumbent_schedule()\n"
                        "    print(schedule)\n"
                    ),
                }
            ],
        }
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 10000,
                "truncated": False,
                "snippet": (
                    ("RAG_FILLER " * 700)
                    + "Recover operation-level list scheduler and keep setup-aware multi-start. "
                    + ("RAG_FILLER " * 500)
                ),
            }
        ] + [
            {
                "path": f"knowledge/papers/large_card_{index}.md",
                "chars": 10000,
                "truncated": False,
                "snippet": "FJSP-SDST local search background. " + ("MORE_FILLER " * 1200),
            }
            for index in range(4)
        ]

        prompt_context = priority_worker_context(context)

        self.assertIn("examples/agent_generated_fjsp_solver.py", prompt_context)
        self.assertIn("def parse_instance(path):", prompt_context)
        self.assertIn("def main():", prompt_context)
        self.assertIn("anchor text not found", prompt_context)
        self.assertIn("priority_knowledge_cards", prompt_context)
        self.assertIn("operation-level list scheduler", prompt_context)
        self.assertLess(prompt_context.index("incumbent_code_context"), prompt_context.index("priority_knowledge_cards"))

    def test_priority_worker_context_keeps_rag_after_incumbent_source_grows(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "fjsp_sdst",
            "description": "Improve an agent_generated FJSP-SDST solver on oddla20 with local_search_operator.",
            "instances": [{"id": "oddla20", "path": "instances/oddla20.txt"}],
        }
        context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
        context["loop_feedback"] = {
            "incumbent_key_before": [-1133.0],
            "previous_rounds": [{"round_index": 7, "decision": "promoted", "candidate_key": [-1133.0]}],
        }
        long_source = (
            "#!/usr/bin/env python3\n"
            "def parse_instance(path):\n"
            "    return {}\n\n"
            + ("# solve filler\n" * 180)
            + "def solve(instance_path, seed=0):\n"
            "    best_schedule = []\n"
            "    return best_schedule\n\n"
            + ("# decode filler\n" * 180)
            + "def decode_schedule(machine_orders):\n"
            "    return []\n\n"
            + ("# local search filler\n" * 230)
            + "def local_search_improve(schedule, seed):\n"
            "    return schedule\n\n"
            + ("# relocate filler\n" * 230)
            + "def relocate_improve(schedule):\n"
            "    return schedule\n\n"
            + ("# tail filler\n" * 160)
            + "def main():\n"
            "    solve('instance')\n\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n"
        )
        self.assertGreater(len(long_source), 12000)
        context["incumbent_code_context"] = {
            "source": "promoted_incumbent_worktree",
            "purpose": "Current promoted solver source for surgical patches.",
            "files": [
                {
                    "relative_path": "examples/agent_generated_fjsp_solver.py",
                    "chars": len(long_source),
                    "truncated": False,
                    "snippet": long_source,
                }
            ],
        }
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 10000,
                "truncated": False,
                "snippet": (
                    ("RAG_FILLER " * 600)
                    + "What To Preserve Or Recover First: Recover operation-level list scheduler and setup-aware multi-start. "
                    + ("RAG_FILLER " * 600)
                ),
            }
        ]

        prompt_context = priority_worker_context(context)

        self.assertIn("snippet_compacted_for_priority", prompt_context)
        self.assertIn("def parse_instance(path):", prompt_context)
        self.assertIn("def solve(instance_path, seed=0):", prompt_context)
        self.assertIn("def main():", prompt_context)
        self.assertIn("priority_knowledge_cards", prompt_context)
        self.assertIn("operation-level list scheduler", prompt_context)

    def test_priority_worker_context_prioritizes_local_search_safety_card(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "fjsp_sdst",
            "description": "Improve an agent_generated FJSP-SDST solver with local_search_operator.",
            "instances": [{"id": "oddla20", "path": "instances/oddla20.txt"}],
        }
        context["iteration_edit_contract"] = {"mode": "incremental_after_baseline"}
        context["loop_feedback"] = {
            "incumbent_key_before": [-1000.0],
            "previous_rounds": [
                {
                    "round_index": 7,
                    "decision": "rolled_back",
                    "smoke_gate": {
                        "passed": False,
                        "summary": {
                            "validation_summary": {
                                "top_errors": [
                                    {
                                        "error": (
                                            "solver command failed with exit code 1 | stderr_excerpt: "
                                            "TypeError: tuple indices must be integers or slices, not str"
                                        ),
                                        "count": 1,
                                    }
                                ]
                            }
                        },
                    },
                }
            ],
        }
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 10000,
                "truncated": False,
                "snippet": (
                    ("RAG_FILLER " * 500)
                    + "## What To Preserve Or Recover First\nRecover operation-level list scheduler.\n"
                    + ("RAG_FILLER " * 300)
                    + "## Local Search Quality Contract\n"
                    "Candidate schedules must contain exactly the same operation set. "
                    "Decode fixed machine sequences with operation coverage checks.\n"
                    "Risk patterns: partial schedule, deadlock, and mixed machine sequences.\n"
                    + ("RAG_FILLER " * 300)
                ),
            }
        ]

        prompt_context = priority_worker_context(context)

        self.assertIn("Local Search Quality Contract", prompt_context)
        self.assertIn("Risk patterns", prompt_context)
        self.assertIn("TypeError: tuple indices", prompt_context)

    def test_priority_worker_context_keeps_agent_generated_local_method_evidence(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "fjsp_sdst",
            "description": "Improve an agent_generated FJSP-SDST solver on oddla20 with local_search_operator.",
            "instances": [{"id": "oddla20", "path": "instances/oddla20.txt"}],
        }
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 12000,
                "truncated": False,
                "snippet": (
                    "# FJSP-SDST Agent-Generated Solver Search Memory\n"
                    "Purpose text.\n"
                    + ("INTRO_FILLER " * 160)
                    + "## Local Method Evidence\n"
                    "- Operation-level setup-aware dispatch is stronger than job-order greedy construction.\n"
                    "- Insertion/all-pair local search crashed by mixing operation representations.\n"
                    + ("EVIDENCE_FILLER " * 220)
                    + "## What To Preserve Or Recover First\n"
                    "Recover operation-level list scheduler and setup-aware multi-start.\n"
                    + ("PRESERVE_FILLER " * 120)
                    + "## Local Search Quality Contract\n"
                    "Candidate schedules must contain exactly the same operation set.\n"
                    "Risk patterns already observed: Mixing operation representations inside local-search decoders.\n"
                    + ("TAIL_FILLER " * 160)
                ),
            }
        ]

        prompt_context = priority_worker_context(context)

        self.assertIn("Local Method Evidence", prompt_context)
        self.assertIn("Operation-level setup-aware dispatch", prompt_context)
        self.assertIn("mixing operation representations", prompt_context)
        self.assertIn("operation-level list scheduler", prompt_context)
        self.assertIn("Risk patterns already observed", prompt_context)

    def test_agent_generated_memory_card_uses_method_level_evidence(self) -> None:
        text = Path("knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("## Local Method Evidence", text)
        self.assertIn("Do not copy any previous", text)
        for forbidden_value in ("1096", "1102", "1138", "1131", "3817"):
            self.assertNotIn(forbidden_value, text)

    def test_priority_worker_context_includes_variant_quality_contract(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "FJSP",
            "description": "Generate an agent-generated FJSP-SDST solver from IO documents.",
        }
        context["evaluator_protocol"] = {
            "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
            "evaluator_command_template": "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}",
        }
        context["instance_diagnostics"] = {
            "status": "available",
            "summary": {
                "sdst_instance_count": 1,
                "setup_time_kinds": ["job_pair"],
            },
        }

        prompt_context = priority_worker_context(context)

        self.assertIn("agent_generated_solver_quality_contract", prompt_context)
        self.assertIn('"enabled": true', prompt_context)
        self.assertIn("sequence_dependent_setup", prompt_context)
        self.assertIn("complete_schedule_coverage_guard", prompt_context)
        self.assertIn("capability_playbook", prompt_context)
        self.assertIn("Cite the parser function", prompt_context)
        self.assertIn("active_io_parser_rule", prompt_context)
        self.assertIn("hardcoding", prompt_context)
        self.assertIn("constructive_baseline_rule", prompt_context)
        self.assertIn("operation-level ready list", prompt_context)
        self.assertIn("solver_quality_playbook_rule", prompt_context)

    def test_priority_worker_context_includes_agent_quality_memory(self) -> None:
        context = _agent_generated_sdst_context()
        context["loop_feedback"] = {
            "agent_generated_baseline_memory": {
                "status": "ok",
                "accepted_as_incumbent": True,
                "baseline_key": [-120.0],
                "worker_status": "ok",
                "worker_changed_files": ["examples/agent_generated_fjsp_solver.py"],
                "repair_attempt_count": 1,
                "repair_recovered": True,
                "agentic_accepted": True,
                "proposal_summary": "Repair generated baseline parser and constructor.",
                "strategy_intent": "Preserve parser/decoder skeleton before adding local search.",
                "rule_operator_hypotheses": [
                    {
                        "name": "repair_contract_complete_constructor",
                        "type": "baseline_constructor_repair",
                        "target_files": ["examples/agent_generated_fjsp_solver.py"],
                    }
                ],
                "protection_rule": "Preserve generated baseline parser, constructor, and decoder.",
            },
            "experience_memory": {
                "schema_version": 1,
                "memory_tiers": {"candidate_lessons": []},
                "agent_generated_quality_memory": {
                    "attempt_count": 2,
                    "rejected_attempt_count": 1,
                    "recovered_direction_count": 1,
                    "recurring_quality_risks": [
                        {
                            "text": "agent_generated_solver: missing base capabilities: active_io_parser, operation_level_ready_list_constructor",
                            "count": 1,
                        }
                    ],
                    "recurring_self_check_risks": [
                        {
                            "text": "solver_contract_self_check missing implemented capabilities: active_io_parser",
                            "count": 1,
                        }
                    ],
                    "next_prompt_rule": "Resolve recurring agent-generated quality gaps before objective tuning.",
                },
            }
        }

        prompt_context = priority_worker_context(context)

        self.assertIn("agent_generated_quality_memory", prompt_context)
        self.assertIn("agent_generated_baseline_memory", prompt_context)
        self.assertIn("repair_contract_complete_constructor", prompt_context)
        self.assertIn("Preserve generated baseline parser", prompt_context)
        self.assertIn("operation_level_ready_list_constructor", prompt_context)
        self.assertIn("experience_quality_memory_rule", prompt_context)
        self.assertIn("before objective tuning", prompt_context)

    def test_proposal_audit_requires_solver_contract_self_check_for_generated_solver(self) -> None:
        worker = DeepSeekWorker()
        context = _agent_generated_sdst_context()

        missing = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Create a generated solver without a self-check.",
                "strategy_intent": "Write a standalone solver.",
                "rule_operator_hypotheses": [
                    {
                        "name": "operation_level_dispatch",
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
            },
            context,
        )

        self.assertIn(
            "agent_generated_solver_self_check_missing",
            missing["proposal_audit"]["warnings"],
        )

        complete = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Create a generated solver with a self-check.",
                "strategy_intent": "Write a standalone solver from the IO contract.",
                "rule_operator_hypotheses": [
                    {
                        "name": "operation_level_dispatch",
                        "type": "dispatch_rule",
                        "target_files": ["examples/agent_generated_fjsp_solver.py"],
                    }
                ],
                "solver_contract_self_check": _complete_solver_self_check(),
                "changes": [
                    {
                        "path": "examples/agent_generated_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": "print('solver')\n",
                    }
                ],
            },
            context,
        )

        audit = complete["proposal_audit"]["solver_contract_self_check"]
        self.assertTrue(audit["required"])
        self.assertTrue(audit["present"])
        self.assertEqual([], audit["missing_capabilities"])
        self.assertNotIn("agent_generated_solver_self_check_missing", complete["proposal_audit"]["warnings"])

    def test_proposal_audit_warns_on_vague_solver_contract_evidence(self) -> None:
        worker = DeepSeekWorker()
        context = _agent_generated_sdst_context()
        self_check = _complete_solver_self_check()
        self_check["capabilities"][0]["evidence"] = "implemented"

        normalized = worker._normalize_code_edit_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Create a generated solver with vague self-check evidence.",
                "strategy_intent": "Write a standalone solver from the IO contract.",
                "rule_operator_hypotheses": [
                    {
                        "name": "operation_level_dispatch",
                        "type": "dispatch_rule",
                        "target_files": ["examples/agent_generated_fjsp_solver.py"],
                    }
                ],
                "solver_contract_self_check": self_check,
                "changes": [
                    {
                        "path": "examples/agent_generated_fjsp_solver.py",
                        "action": "create_or_replace",
                        "content": "print('solver')\n",
                    }
                ],
            },
            context,
        )

        audit = normalized["proposal_audit"]["solver_contract_self_check"]
        self.assertIn("agent_generated_solver_self_check_vague_evidence", audit["warnings"])
        self.assertIn("standalone_cli_interface", audit["capabilities_with_vague_evidence"])

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

    def test_agent_generated_priority_cards_deprioritize_awls_slot_notes(self) -> None:
        context = _context_packet_with_intake()
        context["task"] = {
            "problem_family": "fjsp_sdst",
            "description": "Improve an agent_generated FJSP-SDST solver on oddla20.",
        }
        context["evaluator_protocol"] = {
            "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance}",
        }
        context["problem_family_capability"] = {
            "knowledge_tags": ["fjsp", "sdst", "awls", "sequence_dependent_setup"],
            "specialization_hooks": ["setup_aware_dispatch_or_insertion"],
        }
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/awls_sdst_initialization_notes.md",
                "chars": 12000,
                "truncated": False,
                "snippet": "AWLS-SDST initialization setup-aware oddla20 local search critical block " * 80,
            },
            {
                "path": "knowledge/papers/awls_sdst_tabu_memory_notes.md",
                "chars": 12000,
                "truncated": False,
                "snippet": "AWLS-SDST tabu memory setup-aware oddla20 local search critical block " * 80,
            },
            {
                "path": "knowledge/papers/fjsp_sdst_agent_generated_search_memory_20260707.md",
                "chars": 5400,
                "truncated": False,
                "snippet": (
                    "Use this card for agent-generated FJSP-SDST solver improvement. "
                    "Recover operation-level list scheduler, setup-aware multi-start, and decoder coverage checks."
                ),
            },
            {
                "path": "knowledge/principles/fjsp_variant_domain_pack_rag.md",
                "chars": 4000,
                "truncated": False,
                "snippet": "Variant domain pack RAG contract for FJSP-SDST.",
            },
        ]

        selected = compact_priority_knowledge_cards(context, limit=3, max_chars_per_card=400)
        selected_names = [Path(card["path"]).name for card in selected]

        self.assertEqual("fjsp_sdst_agent_generated_search_memory_20260707.md", selected_names[0])
        self.assertNotIn("awls_sdst_initialization_notes.md", selected_names)
        self.assertNotIn("awls_sdst_tabu_memory_notes.md", selected_names)

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


def _agent_generated_sdst_context() -> dict[str, object]:
    context = _context_packet_with_intake()
    context["task"] = {
        "problem_family": "FJSP",
        "description": "Generate an agent-generated FJSP-SDST solver from IO documents.",
    }
    context["evaluator_protocol"] = {
        "solver_command_template": "python examples/agent_generated_fjsp_solver.py --input {instance} --output {solution} --seed {seed}",
        "evaluator_command_template": "python examples/standard_fjsp_evaluator.py --instance {instance} --solution {solution} --metrics {metrics}",
    }
    context["instance_diagnostics"] = {
        "status": "available",
        "summary": {
            "sdst_instance_count": 1,
            "setup_time_kinds": ["job_pair"],
        },
    }
    return context


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
                "evidence": f"{name} is implemented in parse_instance/decode_schedule/improve.",
            }
            for name in capabilities
        ],
        "representation": "op_info uses (job_id, op_id), assignment maps op keys to machines, machine_sequences maps machines to op keys.",
        "decoder": "decode_schedule rebuilds all starts/ends and returns None on duplicates, missing ops, deadlocks, or ineligible machines.",
        "variant_handling": ["sequence_dependent_setup is applied between adjacent operations on each machine."],
        "runtime_bounds": "max_restarts, max_iterations, and deadline bound all loops.",
        "incumbent_preservation": "failed decode returns None and improve keeps best_schedule unless candidate_makespan is lower.",
        "remaining_gaps": [],
    }


if __name__ == "__main__":
    unittest.main()
