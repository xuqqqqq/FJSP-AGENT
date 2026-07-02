from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harness_agent.cli import make_worker
from harness_agent.standard_worker_loop import StandardWorkerLoopRequest, standard_solver_command
from harness_agent.worker import NullWorker
from harness_agent.workers.deepseek_worker import apply_code_edit_proposal
from harness_agent.workers.deepseek_slot_worker import (
    DeepSeekSlotWorker,
    compact_context,
    extract_negative_memory_lines,
    extract_generic_slot_proposal,
    generic_slot_needs_repair,
    replace_evolve_block,
    selected_confirmed_slot,
    selected_slot_failure_memory,
    should_accept_generic_slot_repair,
    reject_unrepaired_generic_slot,
    strip_marker_lines,
    validate_awls_slot_contract,
    validate_generic_slot_contract,
    validate_slot_function,
)


class AwlsSlotModeTests(unittest.TestCase):
    def test_replace_evolve_block_keeps_surrounding_file(self) -> None:
        original = "before\n# EVOLVE_START\nold\n# EVOLVE_END\nafter\n"
        replacement = "def evolved_zi(values: dict[str, float]) -> float:\n    return float(values.get(\"base\", 0.0))\n"

        updated = replace_evolve_block(original, replacement)

        self.assertIn("before\n# EVOLVE_START\n", updated)
        self.assertIn(replacement.rstrip(), updated)
        self.assertTrue(updated.endswith("# EVOLVE_END\nafter\n"))

    def test_validate_slot_function_allows_values_get_only(self) -> None:
        validate_slot_function(
            "def evolved_zi(values: dict[str, float]) -> float:\n"
            "    base = float(values.get(\"base\", 0.0))\n"
            "    critical = float(values.get(\"is_critical\", 0.0))\n"
            "    return max(0.0, base * (1.0 + 0.2 * critical))\n"
        )
        with self.assertRaises(ValueError):
            validate_slot_function(
                "def evolved_zi(values: dict[str, float]) -> float:\n"
                "    import os\n"
                "    return 0.0\n"
            )

    def test_standard_solver_command_can_enable_slot_policy(self) -> None:
        request = StandardWorkerLoopRequest(
            docs=[],
            instance_dir=Path("."),
            pattern="*.txt",
            output_dir=Path("."),
            project_root=Path("."),
            worker=NullWorker(),
            solver="awls",
            awls_zi_policy="slot",
        )

        command = standard_solver_command(request)

        self.assertIn("--zi-policy slot", command)

    def test_standard_solver_command_preserves_sdst_incumbent_awls_controls(self) -> None:
        request = StandardWorkerLoopRequest(
            docs=[],
            instance_dir=Path("."),
            pattern="*.txt",
            output_dir=Path("."),
            project_root=Path("."),
            worker=NullWorker(),
            solver="awls",
            awls_zi_policy="critical",
            awls_critical_block_exhaustive_pct=75,
            awls_same_machine_eval="stable",
            awls_beta=400,
            awls_gamma=40,
            awls_theta=5,
        )

        command = standard_solver_command(request)

        self.assertIn("--zi-policy critical", command)
        self.assertIn("--critical-block-exhaustive-pct 75", command)
        self.assertIn("--same-machine-eval stable", command)
        self.assertIn("--beta 400", command)

    def test_cli_deepseek_worker_uses_slot_worker_when_slot_manifest_is_present(self) -> None:
        worker = make_worker(
            "deepseek",
            deepseek_model="deepseek-v4-pro",
            slot_manifest=Path("slot_manifest.json"),
        )

        self.assertIsInstance(worker, DeepSeekSlotWorker)

    def test_validate_awls_slot_contract_accepts_confirmed_manifest(self) -> None:
        context = _slot_context(user_confirmed=True)

        errors = validate_awls_slot_contract(context)

        self.assertEqual([], errors)

    def test_validate_awls_slot_contract_requires_readable_manifest(self) -> None:
        self.assertIn(
            "missing a readable slot_manifest",
            validate_awls_slot_contract({})[0],
        )

    def test_validate_awls_slot_contract_rejects_unconfirmed_slot(self) -> None:
        context = _slot_context(status="draft_requires_user_confirmation", confirmation_required=True, user_confirmed=False)

        errors = validate_awls_slot_contract(context)

        self.assertIn("slot_manifest.status must be confirmed", errors)
        self.assertIn("slot_manifest.confirmation_required must be false", errors)
        self.assertIn("slot 'awls_zi_policy' must be user_confirmed", errors)

    def test_validate_awls_slot_contract_rejects_target_or_marker_mismatch(self) -> None:
        context = _slot_context(user_confirmed=True, target_file="examples/standard_fjsp_evaluator.py", marker_start="# WRONG")

        errors = validate_awls_slot_contract(context)

        self.assertIn("slot target_file must be 'examples/awls_evolved_slots.py'", errors)
        self.assertIn("slot marker_start must be '# EVOLVE_START'", errors)

    def test_selected_confirmed_slot_accepts_generic_sdst_neighborhood_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_neighborhood_selection", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_neighborhood_selection"))

    def test_selected_confirmed_slot_accepts_sdst_move_evaluation_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_move_evaluation")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_move_evaluation", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_move_evaluation"))

    def test_selected_confirmed_slot_accepts_sdst_zi_feature_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_zi_features")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_zi_features", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_zi_features"))

    def test_selected_confirmed_slot_rejects_multiple_confirmed_slots(self) -> None:
        context = _slot_context(user_confirmed=True)
        context["slot_manifest"]["slots"].append(_generic_slot_context()["slot_manifest"]["slots"][0])

        slot, error = selected_confirmed_slot(context)

        self.assertIsNone(slot)
        self.assertIn("exactly one", error)

    def test_generic_slot_proposal_normalizes_to_single_replace_slot_block(self) -> None:
        worker = DeepSeekSlotWorker()
        slot = _generic_slot_context()["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Try a bounded critical-block ordering tweak.",
                "strategy_intent": "Keep IO fixed and alter only candidate ordering.",
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_arc_rank",
                        "type": "local_search_operator",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "```python\n"
                            "# SLOT awls_sdst_neighborhood_selection START\n"
                            "    consider_same(FRONT, block[0], block[-1])\n"
                            "# SLOT awls_sdst_neighborhood_selection END\n"
                            "```"
                        ),
                        "rationale": "Small slot replacement.",
                    },
                    {
                        "action": "text_replace",
                        "path": "examples/standard_fjsp_awls_solver.py",
                        "old": "x",
                        "new": "y",
                    },
                ],
                "risk_notes": "Keep evaluator fixed.",
            },
            slot,
        )

        self.assertEqual(1, len(normalized["changes"]))
        self.assertEqual("replace_slot_block", normalized["changes"][0]["action"])
        self.assertEqual("awls_sdst_neighborhood_selection", normalized["changes"][0]["slot_id"])
        self.assertNotIn("SLOT awls_sdst_neighborhood_selection", normalized["changes"][0]["content"])
        self.assertIn("consider_same", normalized["changes"][0]["content"])
        self.assertEqual(1, len(normalized["rejected_changes"]))
        self.assertEqual(["Keep evaluator fixed."], normalized["risk_notes"])

    def test_generic_slot_replacement_applies_only_marker_block(self) -> None:
        context = _generic_slot_context()
        slot = context["slot_manifest"]["slots"][0]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "examples" / "standard_fjsp_awls_solver.py"
            target.parent.mkdir(parents=True)
            target.write_text(
                "def find_move():\n"
                "    before()\n"
                "    # SLOT awls_sdst_neighborhood_selection START\n"
                "    old_moves()\n"
                "    # SLOT awls_sdst_neighborhood_selection END\n"
                "    after()\n",
                encoding="utf-8",
            )
            proposal = {
                "changes": [
                    {
                        "path": slot["target_file"],
                        "action": "replace_slot_block",
                        "slot_id": slot["slot_id"],
                        "content": "    new_moves()\n",
                    }
                ]
            }

            changed = apply_code_edit_proposal(proposal=proposal, worktree_path=root, context=context)

            self.assertEqual(["examples/standard_fjsp_awls_solver.py"], changed)
            self.assertEqual(
                "def find_move():\n"
                "    before()\n"
                "    # SLOT awls_sdst_neighborhood_selection START\n"
                "    new_moves()\n"
                "    # SLOT awls_sdst_neighborhood_selection END\n"
                "    after()\n",
                target.read_text(encoding="utf-8"),
            )

    def test_generic_slot_normalization_aligns_function_body_indent(self) -> None:
        worker = DeepSeekSlotWorker()
        slot = _generic_slot_context(slot_id="awls_sdst_initialization")["slot_manifest"]["slots"][0]
        slot["original_content"] = "    old_body()\n"

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": "new_body()\nif ready:\n    choose()\n",
                    }
                ]
            },
            slot,
        )

        self.assertEqual("    new_body()\n    if ready:\n        choose()\n", normalized["changes"][0]["content"])

    def test_strip_marker_lines_removes_fences_and_markers(self) -> None:
        slot = _generic_slot_context()["slot_manifest"]["slots"][0]

        stripped = strip_marker_lines(
            "```python\n# SLOT awls_sdst_neighborhood_selection START\n    body()\n# SLOT awls_sdst_neighborhood_selection END\n```",
            slot,
        )

        self.assertEqual("    body()\n", stripped)

    def test_compact_context_prioritizes_selected_slot_knowledge(self) -> None:
        context = _generic_slot_context()
        context["knowledge_cards"] = [
            {"path": "knowledge/benchmarks/standard_fjsp_format.md", "snippet": "generic"},
            {
                "path": "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
                "snippet": "awls_sdst_neighborhood_selection incumbent 1010 failure memory",
            },
            {"path": "knowledge/operators/critical_path_machine_block_neighborhood.md", "snippet": "critical_block"},
        ]

        compact = compact_context(context)

        self.assertTrue(compact["knowledge_cards"][0]["path"].endswith("awls_sdst_neighborhood_selection_notes.md"))

    def test_selected_slot_failure_memory_extracts_negative_evidence(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/awls_sdst_initialization_notes.md",
                "snippet": (
                    "- Legal setup-aware greedy dispatch worsened makespan from 1010 to 1046.\n"
                    "- Do not retry plain setup-aware append scoring unchanged.\n"
                    "- Positive background note without a failure cue.\n"
                ),
            }
        ]

        memory = selected_slot_failure_memory(context, slot, max_items=4)

        self.assertEqual("available", memory["status"])
        evidence = " ".join(item["evidence"] for item in memory["avoid_patterns"])
        self.assertIn("worsened makespan", evidence)
        self.assertIn("Do not retry plain setup-aware append", evidence)

    def test_extract_negative_memory_lines_merges_multiline_bullets(self) -> None:
        lines = extract_negative_memory_lines(
            "- A setup-aware attempt failed because it used the wrong API.\n"
            "  Always pass operation-key tuples.\n"
            "- This positive line should not appear.\n",
            limit=3,
        )

        self.assertEqual(1, len(lines))
        self.assertIn("Always pass operation-key tuples", lines[0])

    def test_generic_slot_audit_warns_when_failure_memory_is_ignored(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]
        context["knowledge_cards"] = [
            {
                "path": "knowledge/papers/awls_sdst_neighborhood_selection_notes.md",
                "snippet": "- Do not retry near-critical/window-only slot replacements; they tied or worsened la20.",
            }
        ]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "near_critical_tweak",
                        "type": "local_search_operator",
                        "novelty": "Uses a small candidate ordering tweak.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": "    consider_same(FRONT, block[0], block[-1])\n",
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "novelty_does_not_reference_failure_memory",
            normalized["proposal_audit"]["warnings"],
        )

    def test_generic_slot_audit_warns_on_empty_proposal_without_risk_note(self) -> None:
        worker = DeepSeekSlotWorker()
        slot = _generic_slot_context(slot_id="awls_sdst_move_evaluation")["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "No safe change selected.",
                "strategy_intent": "Skip editing.",
                "changes": [],
                "risk_notes": [],
            },
            slot,
        )

        self.assertEqual([], normalized["changes"])
        self.assertIn(
            "empty_slot_proposal_without_risk_note",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_generic_slot_audit_repairs_empty_proposal_with_non_blocking_risk_note(self) -> None:
        worker = DeepSeekSlotWorker()
        slot = _generic_slot_context(slot_id="awls_sdst_initialization")["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Revert to the original baseline initializer.",
                "strategy_intent": "Use the original setup-blind greedy baseline.",
                "changes": [],
                "risk_notes": ["This does not exploit setup structure, but prior attempts worsened quality."],
            },
            slot,
        )

        self.assertEqual([], normalized["changes"])
        self.assertIn(
            "empty_slot_proposal_without_concrete_blocker",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertIn(
            "empty_slot_proposal_reverts_to_baseline",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_generic_slot_audit_allows_empty_proposal_with_concrete_blocker(self) -> None:
        worker = DeepSeekSlotWorker()
        slot = _generic_slot_context(slot_id="awls_sdst_zi_features")["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "No safe change selected.",
                "strategy_intent": "Skip editing.",
                "changes": [],
                "risk_notes": [
                    "No safe edit is possible inside this slot because the required feature is outside the slot contract."
                ],
            },
            slot,
        )

        self.assertEqual([], normalized["changes"])
        self.assertNotIn(
            "empty_slot_proposal_without_concrete_blocker",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertFalse(generic_slot_needs_repair(normalized))

    def test_extract_generic_slot_proposal_rejects_nested_hypothesis_object(self) -> None:
        with self.assertRaises(Exception):
            extract_generic_slot_proposal(
                '{"name":"nested_hypothesis","type":"local_search_operator"} trailing truncated outer proposal'
            )

    def test_generic_slot_semantic_repair_cannot_discard_existing_change(self) -> None:
        original = {
            "changes": [{"action": "replace_slot_block", "slot_id": "awls_sdst_initialization", "content": "body()"}],
            "proposal_audit": {"warnings": ["novelty_does_not_reference_failure_memory"]},
        }
        repaired = {
            "changes": [],
            "proposal_audit": {"warnings": []},
        }

        self.assertFalse(should_accept_generic_slot_repair(original, repaired))

    def test_same_machine_slot_warns_when_setup_propagation_repeats_without_exact_trial(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_local_propagation",
                        "type": "local_search_operator",
                        "novelty": "Avoids failed setup-only scoring by being different.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    new_r = []\n"
                            "    new_q = []\n"
                            "    return setup_time_between\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_setup_propagation_without_exact_trial",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_unrepaired_must_repair_slot_warning_drops_changes(self) -> None:
        original = {
            "changes": [{"action": "replace_slot_block", "slot_id": "awls_sdst_same_machine_evaluation", "content": "bad"}],
            "risk_notes": [],
            "proposal_audit": {"warnings": ["same_machine_setup_propagation_without_exact_trial"]},
        }
        repaired = {
            "changes": [],
            "proposal_audit": {"warnings": []},
        }

        self.assertFalse(should_accept_generic_slot_repair(original, repaired))
        rejected = reject_unrepaired_generic_slot(original)
        self.assertEqual([], rejected["changes"])
        self.assertIn("unrepaired_must_repair_warning", rejected["proposal_audit"]["warnings"])


def _slot_context(
    *,
    status: str = "confirmed",
    confirmation_required: bool = False,
    user_confirmed: bool = True,
    target_file: str = "examples/awls_evolved_slots.py",
    marker_start: str = "# EVOLVE_START",
    marker_end: str = "# EVOLVE_END",
) -> dict[str, object]:
    return {
        "slot_manifest": {
            "exists": True,
            "status": status,
            "confirmation_required": confirmation_required,
            "slots": [
                {
                    "slot_id": "awls_zi_policy",
                    "target_file": target_file,
                    "marker_start": marker_start,
                    "marker_end": marker_end,
                    "user_confirmed": user_confirmed,
                }
            ],
        }
    }


def _generic_slot_context(*, slot_id: str = "awls_sdst_neighborhood_selection") -> dict[str, object]:
    marker = f"# SLOT {slot_id}"
    title = "AWLS-SDST critical-block neighborhood candidate selection"
    purpose = "Generate bounded candidate moves."
    inputs = ["schedule", "consider_same", "consider_change"]
    outputs = ["Populate candidate move containers through closures."]
    tags = ["awls", "sdst", "neighborhood"]
    if slot_id == "awls_sdst_move_evaluation":
        title = "AWLS-SDST setup-aware change-machine NK scoring"
        purpose = "Rank change-machine moves."
        inputs = ["schedule", "method", "which", "where", "intersection_first", "intersection_last", "gamma"]
        outputs = ["Return numeric change-machine move score."]
        tags = ["awls", "sdst", "move_scoring", "nk_neighborhood", "change_machine"]
    elif slot_id == "awls_sdst_zi_features":
        title = "AWLS-SDST setup-aware zi feature extraction"
        purpose = "Add setup-aware numeric features to zi values."
        inputs = ["schedule", "node", "values", "operation_key", "setup_time_between"]
        outputs = ["Mutate values with finite setup feature entries."]
        tags = ["awls", "sdst", "zi", "zi_features", "setup_time"]
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
                    "slot_id": slot_id,
                    "title": title,
                    "target_file": "examples/standard_fjsp_awls_solver.py",
                    "marker_start": f"{marker} START",
                    "marker_end": f"{marker} END",
                    "slot_kind": "marked_block",
                    "language": "python",
                    "purpose": purpose,
                    "inputs": inputs,
                    "outputs": outputs,
                    "invariants": ["Keep parser/evaluator/IO fixed."],
                    "allowed_edits": ["Only rewrite code between markers."],
                    "forbidden_edits": ["Do not edit evaluator semantics."],
                    "validation_commands": ["python -m compileall examples/standard_fjsp_awls_solver.py"],
                    "knowledge_tags": tags,
                    "user_confirmed": True,
                }
            ],
        },
    }


if __name__ == "__main__":
    unittest.main()
