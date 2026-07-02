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
    generic_slot_repair_guidance,
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

    def test_selected_confirmed_slot_accepts_sdst_move_selection_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_move_selection", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_move_selection"))

    def test_selected_confirmed_slot_accepts_sdst_zi_feature_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_zi_features")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_zi_features", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_zi_features"))

    def test_selected_confirmed_slot_accepts_sdst_weight_update_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_weight_update")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_weight_update", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_weight_update"))

    def test_selected_confirmed_slot_accepts_sdst_search_transition_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_search_transition", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_search_transition"))

    def test_selected_confirmed_slot_accepts_sdst_tabu_memory_slot(self) -> None:
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")

        slot, error = selected_confirmed_slot(context)

        self.assertEqual("", error)
        self.assertEqual("awls_sdst_tabu_memory", slot["slot_id"])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_tabu_memory"))

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

    def test_initialization_slot_warns_on_append_only_setup_completion_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_append_completion",
                        "type": "construction_rule",
                        "novelty": "Avoids prior failure by using setup in completion.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    setup_ready = machine_ready[machine_id] + setup_time_between(index.instance, machine_id, prev_op, cur_op, index)\n"
                            "    completion = max(job_ready[job_id], setup_ready) + duration\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_append_only_setup_completion",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_warns_on_append_only_low_setup_tiebreak_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "low_setup_append_tie",
                        "type": "construction_rule",
                        "novelty": "Uses setup as a tie-breaker.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    setup_cost = setup_time_between(index.instance, machine_id, prev_op, cur_op, index)\n"
                            "    best_setup = min(best_setup, setup_cost)\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_low_setup_tiebreak",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_warns_on_regret_label_without_second_best(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_regret_label_only",
                        "type": "construction_rule",
                        "novelty": "Avoids failed append-only setup completion by using a regret label.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    setup_cost = setup_time_between(index.instance, machine_id, prev_op, cur_op, index)\n"
                            "    regret = setup_cost + index.duration(node, machine_id)\n"
                            "    choices.append((regret, node, machine_id))\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_regret_label_without_second_best",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_allows_true_second_best_regret_append(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "second_best_machine_regret",
                        "type": "construction_rule",
                        "novelty": "Materially changes failed append-only setup completion by ranking operations with second-best machine regret.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    candidate_costs = []\n"
                            "    for machine_id, duration in index.candidates[node].items():\n"
                            "        setup_cost = setup_time_between(index.instance, machine_id, prev_op, cur_op, index)\n"
                            "        completion = max(job_ready[job_id], machine_ready[machine_id] + setup_cost) + duration\n"
                            "        candidate_costs.append((completion, machine_id))\n"
                            "    candidate_costs.sort()\n"
                            "    best_machine_cost = candidate_costs[0][0]\n"
                            "    second_best_machine_cost = candidate_costs[1][0] if len(candidate_costs) > 1 else best_machine_cost\n"
                            "    regret = second_best_machine_cost - best_machine_cost\n"
                            "    machine_id = candidate_costs[0][1]\n"
                            "    choices.append((-regret, best_machine_cost, machine_id, node))\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        warnings = normalized["proposal_audit"]["warnings"]
        self.assertNotIn("initialization_regret_label_without_second_best", warnings)
        self.assertNotIn("initialization_retries_append_only_setup_completion", warnings)
        self.assertNotIn("initialization_retries_low_setup_tiebreak", warnings)

    def test_initialization_slot_allows_second_best_cost_regret_variable(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "second_best_cost_regret",
                        "type": "construction_rule",
                        "novelty": "Materially changes failed append-only setup completion by computing second_best_cost - best_cost before append.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    costs = []\n"
                            "    for machine_id, duration in index.candidates[node].items():\n"
                            "        setup = setup_time_between(index.instance, machine_id, prev_op, cur_op, index)\n"
                            "        costs.append((max(job_ready[job_id], machine_ready[machine_id] + setup) + duration, machine_id))\n"
                            "    costs.sort()\n"
                            "    best_cost, best_machine = costs[0]\n"
                            "    second_best_cost = costs[1][0] if len(costs) > 1 else best_cost\n"
                            "    regret = second_best_cost - best_cost\n"
                            "    sequences[best_machine].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        warnings = normalized["proposal_audit"]["warnings"]
        self.assertNotIn("initialization_regret_label_without_second_best", warnings)
        self.assertNotIn("initialization_retries_append_only_setup_completion", warnings)
        self.assertNotIn("initialization_retries_low_setup_tiebreak", warnings)

    def test_initialization_slot_allows_sorted_completion_pair_regret(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "sorted_completion_regret",
                        "type": "construction_rule",
                        "novelty": "Materially changes failed append-only setup completion by sorting candidate machine completions and subtracting second-best minus best.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    completions = []\n"
                            "    for machine_id, duration in index.candidates[node].items():\n"
                            "        setup = setup_time_between(index.instance, machine_id, prev_op, (job_id, op_id), index)\n"
                            "        completion = max(job_ready[job_id], machine_ready[machine_id]) + setup + duration\n"
                            "        completions.append((machine_id, completion))\n"
                            "    completions.sort(key=lambda item: item[1])\n"
                            "    regret = completions[1][1] - completions[0][1] if len(completions) >= 2 else 0\n"
                            "    machine_id = completions[0][0]\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        warnings = normalized["proposal_audit"]["warnings"]
        self.assertNotIn("initialization_regret_label_without_second_best", warnings)
        self.assertNotIn("initialization_retries_append_only_setup_completion", warnings)
        self.assertNotIn("initialization_retries_low_setup_tiebreak", warnings)

    def test_initialization_slot_warns_on_repeated_max_regret_append_dispatch(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "max_regret_append_retry",
                        "type": "construction_rule",
                        "novelty": "Retries true regret by choosing the maximum regret ready operation.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    ready_ops = []\n"
                            "    second_best_comp = best_comp\n"
                            "    regret = second_best_comp - best_comp\n"
                            "    ready_ops.append((node, best_mach, regret, best_comp))\n"
                            "    max_regret = max(r for (_, _, r, _) in ready_ops)\n"
                            "    node, machine_id = rng.choice([(node, mach) for (node, mach, regret, best_comp) in ready_ops if regret == max_regret])\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_max_regret_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_warns_on_op_priorities_max_regret_append_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "op_priorities_max_regret_append_retry",
                        "type": "construction_rule",
                        "novelty": "Retries classic max regret dispatch with op_priorities.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    op_priorities = []\n"
                            "    machine_costs = []\n"
                            "    machine_costs.sort(key=lambda item: item[0])\n"
                            "    best_cost, best_machine = machine_costs[0]\n"
                            "    second_cost = machine_costs[1][0]\n"
                            "    regret = second_cost - best_cost\n"
                            "    op_priorities.append((regret, node, best_machine, best_cost))\n"
                            "    max_regret = max(item[0] for item in op_priorities)\n"
                            "    regret, node, best_machine, best_cost = rng.choice([item for item in op_priorities if item[0] == max_regret])\n"
                            "    sequences[best_machine].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_max_regret_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_requires_topology_when_round_hypothesis_demands_it(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        context["hypothesis"] = (
            "This round must not be another append-only ready-operation priority formula. "
            "Use topology-guarded non-append insertion or post-construction repair."
        )
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "plain_priority_formula_despite_topology_request",
                        "type": "construction_rule",
                        "novelty": "Claims to address topology request with a new priority.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    ready_ops = []\n"
                            "    priority = completion + setup_pressure\n"
                            "    ready_ops.append((priority, node, machine_id))\n"
                            "    ready_ops.sort()\n"
                            "    _, node, machine_id = ready_ops[0]\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_missing_required_topology_or_repair",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_satisfies_topology_hypothesis_with_real_guard(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        context["hypothesis"] = (
            "This round must not be another append-only formula; use topology-guarded insertion."
        )
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "topology_guarded_insertion",
                        "type": "construction_rule",
                        "novelty": "Uses a real AwlsSchedule topological guard before accepting insertion.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    candidate_sequences[machine_id].insert(best_pos, node)\n"
                            "    candidate = AwlsSchedule(index, candidate_sequences, candidate_on_machine, rng)\n"
                            "    candidate.topological_sort()\n"
                            "    sequences = candidate_sequences\n"
                            "    on_machine = candidate_on_machine\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertNotIn(
            "initialization_missing_required_topology_or_repair",
            normalized["proposal_audit"]["warnings"],
        )

    def test_initialization_slot_allows_regret_with_critical_tail_pressure(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "regret_tail_pressure",
                        "type": "construction_rule",
                        "novelty": "Materially changes failed max-regret append dispatch by combining second-best regret with critical tail pressure.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    ready_ops = []\n"
                            "    second_best_comp = best_comp\n"
                            "    regret = second_best_comp - best_comp\n"
                            "    critical_tail = remaining_tail_by_job[job_id]\n"
                            "    ready_ops.append((regret + 0.25 * critical_tail, node, best_mach, best_comp))\n"
                            "    max_regret = max(score for (score, _, _, _) in ready_ops)\n"
                            "    node, machine_id = rng.choice([(node, mach) for (score, node, mach, best_comp) in ready_ops if score == max_regret])\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertNotIn(
            "initialization_retries_max_regret_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )

    def test_initialization_slot_warns_on_regret_roulette_append_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "regret_roulette_append_retry",
                        "type": "construction_rule",
                        "novelty": "Retries true second-best regret with roulette selection.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    candidates = []\n"
                            "    machine_costs = []\n"
                            "    machine_costs.sort()\n"
                            "    best_cost = machine_costs[0][0]\n"
                            "    second_best_cost = machine_costs[1][0] if len(machine_costs) > 1 else best_cost\n"
                            "    regret = second_best_cost - best_cost\n"
                            "    candidates.append((node, machine_id, completion, regret))\n"
                            "    filtered = candidates\n"
                            "    weights = [regret + 1 for (_, _, _, regret) in filtered]\n"
                            "    total_weight = sum(weights)\n"
                            "    node, machine_id, _, _ = rng.choices(filtered, weights=weights, k=1)[0]\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_regret_roulette_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_allows_regret_roulette_with_tail_pressure(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "regret_roulette_tail_pressure",
                        "type": "construction_rule",
                        "novelty": "Materially changes failed regret roulette by adding critical tail pressure.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    candidates = []\n"
                            "    machine_costs = []\n"
                            "    machine_costs.sort()\n"
                            "    best_cost = machine_costs[0][0]\n"
                            "    second_best_cost = machine_costs[1][0] if len(machine_costs) > 1 else best_cost\n"
                            "    regret = second_best_cost - best_cost\n"
                            "    critical_tail = remaining_tail_by_job[job_id]\n"
                            "    candidates.append((node, machine_id, completion, regret, critical_tail))\n"
                            "    filtered = candidates\n"
                            "    weights = [regret + 0.25 * critical_tail + 1 for (_, _, _, regret, critical_tail) in filtered]\n"
                            "    node, machine_id, _, _, _ = rng.choices(filtered, weights=weights, k=1)[0]\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertNotIn(
            "initialization_retries_regret_roulette_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )

    def test_initialization_slot_warns_on_tail_ratio_regret_append_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "tail_ratio_regret_append_retry",
                        "type": "construction_rule",
                        "novelty": "Retries append-only remaining-work tail ratio with regret tie-breaks.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    ready_ops = []\n"
                            "    machine_costs = []\n"
                            "    machine_costs.sort()\n"
                            "    best_cost = machine_costs[0][0]\n"
                            "    second_best_cost = machine_costs[1][0] if len(machine_costs) > 1 else best_cost\n"
                            "    regret = second_best_cost - best_cost\n"
                            "    remaining = sum(min(index.candidates[n].values()) for n in nodes[current_pos[job_id]:])\n"
                            "    priority = remaining / best_cost\n"
                            "    ready_ops.append((priority, regret, node, machine_id))\n"
                            "    ready_ops.sort(key=lambda item: (item[0], item[1]), reverse=True)\n"
                            "    _, _, node, machine_id = ready_ops[0]\n"
                            "    sequences[machine_id].append(node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_tail_ratio_regret_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_allows_tail_ratio_when_topology_guarded(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "tail_ratio_topology_guarded_insert",
                        "type": "construction_rule",
                        "novelty": "Uses tail ratio only to rank topology-guarded insertion candidates.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    ready_ops = []\n"
                            "    machine_costs = []\n"
                            "    machine_costs.sort()\n"
                            "    best_cost = machine_costs[0][0]\n"
                            "    second_best_cost = machine_costs[1][0] if len(machine_costs) > 1 else best_cost\n"
                            "    regret = second_best_cost - best_cost\n"
                            "    remaining = remaining_tail_by_job[job_id]\n"
                            "    priority = remaining / best_cost\n"
                            "    ready_ops.sort(key=lambda item: (item[0], item[1]), reverse=True)\n"
                            "    candidate = AwlsSchedule(index, candidate_sequences, candidate_on_machine, rng)\n"
                            "    candidate.topological_sort()\n"
                            "    sequences[machine_id].insert(best_pos, node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertNotIn(
            "initialization_retries_tail_ratio_regret_append_dispatch",
            normalized["proposal_audit"]["warnings"],
        )

    def test_initialization_slot_warns_on_non_append_without_cycle_guard(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "unguarded_committed_insert",
                        "type": "construction_rule",
                        "novelty": "Avoids failed append-only ideas by using non-append insertion.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    insert_pos = best_pos\n"
                            "    sequences[machine_id].insert(insert_pos, node)\n"
                            "    on_machine[node] = machine_id\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_non_append_without_acyclic_guard",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_warns_on_seq_insert_with_textual_acyclic_claim_only(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "claimed_acyclic_forward_insert",
                        "type": "construction_rule",
                        "novelty": "Avoids failed insertion by claiming acyclic forward propagation.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    sequences = [[] for _ in range(index.instance.machine_count)]\n"
                            "    on_machine = [-1] * index.node_count\n"
                            "    seq = sequences[0]\n"
                            "    # acyclic forward propagation avoids cycle_detected states\n"
                            "    seq.insert(0, index.job_to_nodes[0][0])\n"
                            "    return sequences, on_machine\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_non_append_without_acyclic_guard",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_allows_non_append_with_real_topology_guard(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "guarded_committed_insert",
                        "type": "construction_rule",
                        "novelty": "Avoids failed insertion by checking topological_sort before accepting.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    sequences = [[] for _ in range(index.instance.machine_count)]\n"
                            "    on_machine = [-1] * index.node_count\n"
                            "    node = index.job_to_nodes[0][0]\n"
                            "    on_machine[node] = 0\n"
                            "    sequences[0].insert(0, node)\n"
                            "    candidate = AwlsSchedule(index, sequences, on_machine, rng)\n"
                            "    candidate.topological_sort()\n"
                            "    return sequences, on_machine\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertNotIn(
            "initialization_non_append_without_acyclic_guard",
            normalized["proposal_audit"]["warnings"],
        )

    def test_initialization_slot_warns_when_insert_rebuilds_global_ready_state(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "insert_replay_machine",
                        "type": "construction_rule",
                        "novelty": "Avoids append-only construction and claims to check topological_sort.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    sequences[machine_id].insert(insert_pos, node)\n"
                            "    topological_sort = True\n"
                            "    for op_node in sequences[machine_id]:\n"
                            "        completion = machine_ready[machine_id] + index.duration(op_node, machine_id)\n"
                            "        job_ready[index.node_to_job[op_node]] = completion\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_rebuilds_ready_after_committed_insert",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_initialization_slot_warns_on_static_bottleneck_without_setup_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_initialization")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "static_bottleneck_priority",
                        "type": "construction_rule",
                        "novelty": "Avoids setup-aware append and unsafe insertion by focusing on bottleneck load.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_initialization",
                        "content": (
                            "    machine_loads = [0.0] * index.instance.machine_count\n"
                            "    bottleneck_machine = max(range(index.instance.machine_count), key=lambda m: machine_loads[m])\n"
                            "    def bottleneck_priority(node):\n"
                            "        return index.candidates[node].get(bottleneck_machine, 0)\n"
                            "    chosen = max(ready_nodes, key=bottleneck_priority)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "initialization_retries_static_bottleneck_ignores_setup",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

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

    def test_neighborhood_slot_warns_on_repeated_near_critical_window_pattern(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "near_critical_window_retry",
                        "type": "local_search_operator",
                        "novelty": "Avoids prior failed memory but still explores near-critical windows.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    near_critical_cutoff = 0.99 * schedule.makespan\n"
                            "    same_machine_window = 10\n"
                            "    for node in schedule.index.real_nodes:\n"
                            "        near_critical = schedule.end_time[node] >= near_critical_cutoff\n"
                            "        if near_critical:\n"
                            "            consider_same(FRONT, node, node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "neighborhood_retries_failed_near_critical_threshold",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertIn(
            "neighborhood_retries_failed_same_machine_window",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_repair_guidance_names_material_alternatives(self) -> None:
        slot = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")["slot_manifest"]["slots"][0]

        guidance = generic_slot_repair_guidance(slot)

        self.assertIn("boundary-biased N7", guidance)
        self.assertIn("consider_same / consider_change", guidance)
        self.assertIn("slot_id", guidance)
        self.assertIn("awls_sdst_neighborhood_selection", guidance)
        self.assertIn("schedule.index.duration", guidance)

    def test_move_selection_slot_normalizes_exact_recheck_replacement(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bounded_exact_tiebreak",
                        "type": "local_search_operator",
                        "novelty": "Different from failed setup-only tie-breakers by preserving exact makespan first.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    if not all_moves:\n"
                            "        return None\n"
                            "    if exact_select_top_k > 0 and ranked_moves:\n"
                            "        exact_best = None\n"
                            "        for approx_value, move_key in sorted(ranked_moves, key=lambda item: item[0])[:exact_select_top_k]:\n"
                            "            try:\n"
                            "                trial = schedule.clone()\n"
                            "                move = Move(*move_key)\n"
                            "                trial.apply_move(move)\n"
                            "            except (ValueError, KeyError):\n"
                            "                continue\n"
                            "            key = (trial.makespan, approx_value, move_key)\n"
                            "            if exact_best is None or key[:2] < exact_best[:2]:\n"
                            "                exact_best = key\n"
                            "        if exact_best is not None:\n"
                            "            return Move(*exact_best[2])\n"
                            "    if not best_moves:\n"
                            "        return Move(*schedule.rng.choice(all_moves))\n"
                            "    if best_value > schedule.makespan and schedule.rng.randrange(100) < 3:\n"
                            "        return Move(*schedule.rng.choice(all_moves))\n"
                            "    return Move(*schedule.rng.choice(best_moves))\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertEqual(["awls_sdst_move_selection"], [item["slot_id"] for item in normalized["changes"]])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_move_selection"))
        self.assertNotIn("slot_content_python_syntax_error", normalized["proposal_audit"]["warnings"])

    def test_move_selection_slot_warns_on_candidate_generation(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "late_candidate_generation",
                        "type": "local_search_operator",
                        "novelty": "Different from failed selectors by adding missing setup candidates.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    if not all_moves:\n"
                            "        consider_same(BACK, block[0], block[-1])\n"
                            "    return Move(*schedule.rng.choice(all_moves))\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("move_selection_generates_candidates", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_candidate_list_mutation(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "synthetic_best_move",
                        "type": "local_search_operator",
                        "novelty": "Different from failed selectors by inserting a synthetic best move.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    if ranked_moves:\n"
                            "        best_moves.append(ranked_moves[0][1])\n"
                            "    return Move(*schedule.rng.choice(best_moves or all_moves))\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("move_selection_mutates_candidate_lists", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_direct_schedule_mutation(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "direct_apply_probe",
                        "type": "local_search_operator",
                        "novelty": "Different from failed exact selectors by applying candidates in-place.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    move = Move(*all_moves[0])\n"
                            "    schedule.apply_move(move)\n"
                            "    return move\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("move_selection_mutates_schedule_directly", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_trial_apply_without_clone(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "shared_trial_probe",
                        "type": "local_search_operator",
                        "novelty": "Different from failed exact selectors by reusing a prepared trial.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    trial.apply_move(Move(*all_moves[0]))\n"
                            "    return Move(*all_moves[0])\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("move_selection_trial_apply_without_clone", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_repeated_small_best_moves_exact_recheck(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "small_best_moves_exact_again",
                        "type": "local_search_operator",
                        "novelty": "Different from failed selectors by exact checking a small best_moves sample.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    if not all_moves:\n"
                            "        return None\n"
                            "    subset_size = min(3, len(best_moves))\n"
                            "    for move_key in schedule.rng.sample(best_moves, subset_size):\n"
                            "        trial = schedule.clone()\n"
                            "        trial.apply_move(Move(*move_key))\n"
                            "        if trial.makespan <= schedule.makespan:\n"
                            "            return Move(*move_key)\n"
                            "    return Move(*schedule.rng.choice(best_moves or all_moves))\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_retries_small_best_moves_exact_recheck",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_global_setup_sum_tiebreak_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "exact_setup_tie_breaker",
                        "type": "local_search_operator",
                        "novelty": "Different from random escapes by summing setup after exact recheck.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    def _total_setup(sched):\n"
                            "        setup_sum = 0\n"
                            "        for machine_id, sequence in enumerate(sched.machine_sequences):\n"
                            "            for idx in range(1, len(sequence)):\n"
                            "                setup_sum += setup_time_between(sched.index.instance, machine_id, operation_key(sched, sequence[idx - 1]), operation_key(sched, sequence[idx]), sched.index)\n"
                            "        return setup_sum\n"
                            "    exact_best = None\n"
                            "    for approx_value, move_key in sorted(ranked_moves, key=lambda item: item[0])[:exact_select_top_k]:\n"
                            "        trial = schedule.clone()\n"
                            "        trial.apply_move(Move(*move_key))\n"
                            "        trial_setup = _total_setup(trial)\n"
                            "        key = (trial.makespan, trial_setup, approx_value, move_key)\n"
                            "        if exact_best is None or key[:3] < exact_best[:3]:\n"
                            "            exact_best = key\n"
                            "    return Move(*exact_best[3])\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_retries_global_setup_sum_tiebreak",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_random_noise_escape_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "random_noise_escape",
                        "type": "local_search_operator",
                        "novelty": "Different from failed selectors by perturbing ranked moves and escaping randomly.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    ranked_with_noise = sorted(ranked_moves, key=lambda item: item[0] + schedule.rng.uniform(-0.001, 0.001))\n"
                            "    if schedule.rng.randrange(100) < 5:\n"
                            "        return Move(*schedule.rng.choice(all_moves))\n"
                            "    return Move(*ranked_with_noise[0][1])\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_retries_random_noise_escape",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_invalid_setup_time_between_signature(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "global_setup_scan",
                        "type": "local_search_operator",
                        "novelty": "Different from failed selectors by using setup tie-breaking.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    from harness_agent.standard_fjsp import setup_time_between\n"
                            "    total = setup_time_between(schedule.index, op1, op2)\n"
                            "    return Move(*best_moves[0]) if total >= 0 else None\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_uses_invalid_setup_time_between_signature",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_nonexistent_operations_api(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bottleneck_operations_scan",
                        "type": "local_search_operator",
                        "novelty": "Different from setup tie-breakers by counting bottleneck records.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    loads = {}\n"
                            "    for op in schedule.operations:\n"
                            "        loads[op.machine] = max(loads.get(op.machine, 0), op.end)\n"
                            "    return Move(*best_moves[0])\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_uses_nonexistent_operations_api",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_nonexistent_node_to_operation_key(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "global_setup_sum",
                        "type": "local_search_operator",
                        "novelty": "Different from local setup tie-breakers by scanning full machine sequences.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    idx = schedule.index\n"
                            "    prev_op = idx.node_to_operation_key[move.which]\n"
                            "    return Move(*best_moves[0]) if prev_op else None\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_uses_nonexistent_node_to_operation_key",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_slot_warns_on_move_key_shape_misread(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_move_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bottleneck_move_key_shape",
                        "type": "local_search_operator",
                        "novelty": "Different from random escapes by classifying move keys.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_selection",
                        "content": (
                            "    def score(move_key):\n"
                            "        move_type, op_key, target_m = move_key\n"
                            "        if move_type == 'change_machine':\n"
                            "            return 1\n"
                            "        return 0\n"
                            "    return Move(*sorted(best_moves or all_moves, key=score)[0])\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "move_selection_misinterprets_move_key_shape",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_move_selection_repair_guidance_names_select_only_contract(self) -> None:
        slot = _generic_slot_context(slot_id="awls_sdst_move_selection")["slot_manifest"]["slots"][0]

        guidance = generic_slot_repair_guidance(slot)

        self.assertIn("already collected move keys", guidance)
        self.assertIn("trial = schedule.clone()", guidance)
        self.assertIn("makespan", guidance)
        self.assertIn("which_node", guidance)
        self.assertIn("no `operations`", guidance)
        self.assertIn("operation_key(schedule, node)", guidance)
        self.assertIn("min(3, len(best_moves))", guidance)
        self.assertIn("global setup-sum", guidance)
        self.assertIn("random-noise", guidance)
        self.assertIn("setup_time_between(sched.index, op1, op2)", guidance)

    def test_weight_update_slot_allows_bounded_weight_cooldown_replacement(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_weight_update")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bounded_sdst_cooldown_pressure",
                        "type": "adaptive_weight_rule",
                        "novelty": "Different from failed setup-ratio formulas by changing cooldown pressure only.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_weight_update",
                        "content": (
                            "    critical = {node for node in schedule.index.real_nodes if schedule.is_critical_operation(node)}\n"
                            "    if current_makespan >= previous_makespan:\n"
                            "        increment = 2 if moved_node in critical else 1\n"
                            "        schedule.op_weight[moved_node] += increment\n"
                            "        schedule.op_cooldown[moved_node] = max(schedule.op_cooldown[moved_node] - theta, 0)\n"
                            "        for node in schedule.index.real_nodes:\n"
                            "            if node not in critical and node != moved_node:\n"
                            "                schedule.op_cooldown[node] += 1\n"
                            "    else:\n"
                            "        for node in schedule.index.real_nodes:\n"
                            "            schedule.op_cooldown[node] += 1\n"
                            "    if current_makespan < best_makespan_before:\n"
                            "        for node in schedule.index.real_nodes:\n"
                            "            schedule.op_cooldown[node] = 10**9\n"
                            "            schedule.op_weight[node] = 0\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertEqual(["awls_sdst_weight_update"], [item["slot_id"] for item in normalized["changes"]])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_weight_update"))
        self.assertFalse(generic_slot_needs_repair(normalized))

    def test_weight_update_slot_warns_on_runtime_api_calls(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_weight_update")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "self_evaluating_weight_update",
                        "type": "adaptive_weight_rule",
                        "novelty": "Different from failed formulas by evaluating a trial move.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_weight_update",
                        "content": (
                            "    trial = schedule.clone()\n"
                            "    trial.apply_move(move)\n"
                            "    if validate_standard_schedule(schedule.index.instance, records):\n"
                            "        schedule.op_weight[moved_node] += 1\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("weight_update_calls_forbidden_runtime_api", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_weight_update_slot_warns_on_schedule_structure_mutation(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_weight_update")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "sequence_repair_weight_update",
                        "type": "adaptive_weight_rule",
                        "novelty": "Different from failed cooldown rules by repairing machine order.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_weight_update",
                        "content": (
                            "    schedule.machine_sequences[0].sort()\n"
                            "    schedule.makespan = current_makespan\n"
                            "    schedule.op_weight[moved_node] += 1\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("weight_update_mutates_schedule_structure", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_weight_update_slot_warns_on_random_or_io(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_weight_update")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "randomized_weight_update",
                        "type": "adaptive_weight_rule",
                        "novelty": "Different from failed formulas by adding random pressure.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_weight_update",
                        "content": (
                            "    if schedule.rng.random() < 0.5:\n"
                            "        schedule.op_weight[moved_node] += 1\n"
                            "    Path('debug.txt').write_text(str(current_makespan))\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("weight_update_uses_random_or_io", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_weight_update_repair_guidance_names_weight_only_contract(self) -> None:
        slot = _generic_slot_context(slot_id="awls_sdst_weight_update")["slot_manifest"]["slots"][0]

        guidance = generic_slot_repair_guidance(slot)

        self.assertIn("op_weight", guidance)
        self.assertIn("op_cooldown", guidance)
        self.assertIn("apply_move", guidance)

    def test_search_transition_slot_allows_bounded_best_update_and_backtrack(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bounded_best_backtrack",
                        "type": "search_transition_rule",
                        "novelty": "Different from cooldown and portfolio ties by changing post-move plateau state.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        if current.makespan < best.makespan:\n"
                            "            best = current.clone()\n"
                            "            if stats is not None:\n"
                            "                stats['best_updates'] = stats.get('best_updates', 0) + 1\n"
                            "        elif current.index.instance.has_sequence_dependent_setup and iteration > 0 and iteration % 50 == 0:\n"
                            "            current = best.clone()\n"
                            "            if stats is not None:\n"
                            "                stats['sdst_best_backtracks'] = stats.get('sdst_best_backtracks', 0) + 1\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertEqual(["awls_sdst_search_transition"], [item["slot_id"] for item in normalized["changes"]])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_search_transition"))
        self.assertFalse(generic_slot_needs_repair(normalized))

    def test_search_transition_slot_warns_on_forbidden_runtime_api_calls(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "recursive_transition_search",
                        "type": "search_transition_rule",
                        "novelty": "Different from plateau ties by calling another search.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        trial = current.clone()\n"
                            "        trial.apply_move(move)\n"
                            "        current = tabu_search(trial, 10, 1.0, beta, gamma, theta, 0, 'stable', 75, zi_policy, '', 1)\n"
                            "        if current.makespan < best.makespan:\n"
                            "            best = current.clone()\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("search_transition_calls_forbidden_runtime_api", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_search_transition_slot_warns_on_schedule_structure_mutation(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "direct_topology_repair_transition",
                        "type": "search_transition_rule",
                        "novelty": "Different from cooldown by repairing machine sequence.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        current.machine_sequences[0].sort()\n"
                            "        current.makespan = best.makespan\n"
                            "        if current.makespan < best.makespan:\n"
                            "            best = current.clone()\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("search_transition_mutates_schedule_structure", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_search_transition_slot_warns_on_promoting_worse_best(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "accept_worse_best_for_setup",
                        "type": "search_transition_rule",
                        "novelty": "Different from setup tie-breaks by changing best promotion.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        if current.makespan >= best.makespan:\n"
                            "            best = current.clone()\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("search_transition_promotes_worse_best", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_search_transition_slot_warns_on_io_or_unseeded_random(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "file_driven_transition",
                        "type": "search_transition_rule",
                        "novelty": "Different from portfolio ties by reading an external threshold.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        threshold = random.random()\n"
                            "        Path('debug.txt').write_text(str(threshold))\n"
                            "        if current.makespan < best.makespan:\n"
                            "            best = current.clone()\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("search_transition_uses_io_or_unseeded_random", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_search_transition_slot_warns_on_stats_without_none_guard(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "unguarded_plateau_counter",
                        "type": "search_transition_rule",
                        "novelty": "Different from portfolio ties by counting plateau steps.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        stats.setdefault('plateau_steps', 0)\n"
                            "        if current.makespan < best.makespan:\n"
                            "            best = current.clone()\n"
                            "            stats['plateau_steps'] = 0\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("search_transition_stats_without_none_guard", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_search_transition_slot_warns_on_relative_degradation_best_reset_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_search_transition")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "degradation_threshold_reset",
                        "type": "search_transition_rule",
                        "novelty": "Different from fixed resets by using a relative makespan gap.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_search_transition",
                        "content": (
                            "        if current.makespan < best.makespan:\n"
                            "            best = current.clone()\n"
                            "        if current.makespan > int(best.makespan * 1.01):\n"
                            "            current = best.clone()\n"
                            "            if stats is not None:\n"
                            "                stats['degradation_resets'] = stats.get('degradation_resets', 0) + 1\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "search_transition_retries_relative_degradation_best_reset",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_search_transition_repair_guidance_names_best_invariant(self) -> None:
        slot = _generic_slot_context(slot_id="awls_sdst_search_transition")["slot_manifest"]["slots"][0]

        guidance = generic_slot_repair_guidance(slot)

        self.assertIn("best", guidance)
        self.assertIn("lowest makespan", guidance)
        self.assertIn("apply_move", guidance)
        self.assertIn("stats is not None", guidance)

    def test_tabu_memory_slot_allows_bounded_single_tabu_add(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "sdst_move_type_tenure",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from reset and cooldown failures by changing only tabu tenure.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    machine_id, sequence = candidate_tabu_sequence(schedule, move)\n"
                            "    tenure = schedule.rng.randint(tenure_min, tenure_max)\n"
                            "    if schedule.index.instance.has_sequence_dependent_setup and move.method in (CHANGE_MACHINE_FRONT, CHANGE_MACHINE_BACK):\n"
                            "        tenure = min(tenure_max + 3, tenure + 2)\n"
                            "    tabu.add(machine_id, sequence, iteration + tenure)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertEqual(["awls_sdst_tabu_memory"], [item["slot_id"] for item in normalized["changes"]])
        self.assertEqual([], validate_generic_slot_contract(context, "awls_sdst_tabu_memory"))
        self.assertFalse(generic_slot_needs_repair(normalized))

    def test_tabu_memory_slot_warns_on_missing_or_multiple_tabu_adds(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "double_tabu_memory",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from reset by storing two reverse arcs.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    machine_id, sequence = candidate_tabu_sequence(schedule, move)\n"
                            "    tabu.add(machine_id, sequence, iteration + tenure_min)\n"
                            "    tabu.add(machine_id, list(reversed(sequence)), iteration + tenure_max)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("tabu_memory_missing_or_multiple_tabu_add", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_tabu_memory_slot_warns_on_runtime_api_calls(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "trial_based_tabu_memory",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from tenure-only by evaluating a trial.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    trial = schedule.clone()\n"
                            "    trial.apply_move(move)\n"
                            "    if validate_standard_schedule(schedule.index.instance, trial.to_records()):\n"
                            "        tabu.add(0, [move.which], iteration + tenure_min)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("tabu_memory_calls_forbidden_runtime_api", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_tabu_memory_slot_warns_on_direct_schedule_or_tabu_mutation(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "direct_tabu_table_write",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from tenure-only by writing the table.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    schedule.machine_sequences[0].sort()\n"
                            "    tabu.items[0][(move.which,)] = iteration + tenure_max\n"
                            "    tabu.add(0, [move.which], iteration + tenure_min)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("tabu_memory_mutates_schedule_or_tabu_directly", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_tabu_memory_slot_warns_on_io_or_unseeded_random(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "file_tuned_tabu_memory",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from tenure-only by reading a threshold.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    tenure = int(random.random() * tenure_max)\n"
                            "    Path('tabu.txt').write_text(str(tenure))\n"
                            "    tabu.add(0, [move.which], iteration + tenure)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("tabu_memory_uses_io_or_unseeded_random", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_tabu_memory_slot_warns_on_nonexistent_sdst_helpers(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "fake_setup_helper_tabu",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from criticality-only by using setup helpers.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    has_sdst = schedule.index.instance.has_sequence_dependent_setup()\n"
                            "    op_key = schedule.index.operation_key(move.which)\n"
                            "    tenure = tenure_max if has_sdst and op_key else tenure_min\n"
                            "    tabu.add(schedule.on_machine[move.which], [move.which], iteration + tenure)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("tabu_memory_uses_nonexistent_api", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_tabu_memory_slot_warns_on_short_front_back_sequence_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_tabu_memory")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "move_type_sequence_and_tenure_bias",
                        "type": "tabu_memory_rule",
                        "novelty": "Different from criticality-only tenure by shortening local move memory.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_tabu_memory",
                        "content": (
                            "    machine_id = schedule.on_machine[move.which]\n"
                            "    if move.method == FRONT:\n"
                            "        sequence = [move.which, move.where]\n"
                            "        tenure = schedule.rng.randint(tenure_min, (tenure_min + tenure_max) // 2)\n"
                            "    elif move.method == BACK:\n"
                            "        sequence = [move.where, move.which]\n"
                            "        tenure = schedule.rng.randint(tenure_min, (tenure_min + tenure_max) // 2)\n"
                            "    else:\n"
                            "        sequence = [move.which]\n"
                            "        tenure = tenure_max\n"
                            "    tabu.add(machine_id, sequence, iteration + tenure)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "tabu_memory_retries_short_front_back_sequence",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_tabu_memory_repair_guidance_names_single_tabu_add_contract(self) -> None:
        slot = _generic_slot_context(slot_id="awls_sdst_tabu_memory")["slot_manifest"]["slots"][0]

        guidance = generic_slot_repair_guidance(slot)

        self.assertIn("tabu.add", guidance)
        self.assertIn("exactly once", guidance)
        self.assertIn("apply_move", guidance)
        self.assertIn("operation_key(schedule, node)", guidance)
        self.assertIn("[move.which, move.where]", guidance)

    def test_generic_slot_audit_repairs_all_rejected_wrong_slot_changes(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Filter setup-heavy moves.",
                "strategy_intent": "Change neighborhood selection.",
                "rule_operator_hypotheses": [
                    {
                        "name": "wrong_slot_edit",
                        "type": "local_search_operator",
                        "novelty": "Different from failed near-critical filters.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_move_evaluation",
                        "content": "    consider_same(FRONT, block[0], block[-1])\n",
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertEqual([], normalized["changes"])
        self.assertIn("all_slot_changes_rejected", normalized["proposal_audit"]["warnings"])
        self.assertIn("slot_change_rejected_wrong_slot_id", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_generic_slot_warns_on_nonexistent_operation_index_durations(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "duration_sorted_blocks",
                        "type": "local_search_operator",
                        "novelty": "Different from failed near-critical filters by sorting blocks.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    blocks = critical_blocks(schedule, schedule.rng, exhaustive=True)\n"
                            "    sorted(blocks, key=lambda block: sum(schedule.index.durations[node] for node in block))\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "slot_uses_nonexistent_operation_index_durations",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_generic_slot_warns_on_nonexistent_setup_time_api(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_api_lookup",
                        "type": "local_search_operator",
                        "novelty": "Different from failed near-critical filters by using setup arcs.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    for node in schedule.index.real_nodes:\n"
                            "        setup = schedule.setup_time[schedule.on_machine[node]].get(node, {})\n"
                            "        if setup:\n"
                            "            consider_same(BACK, node, node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "slot_uses_nonexistent_setup_time_api",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_generic_slot_warns_on_slot_content_syntax_error(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bad_indent_slot",
                        "type": "local_search_operator",
                        "novelty": "Different from failed filters but malformed.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    exhaustive_first = schedule.rng.randrange(100) < 75\n"
                            "        exhaustive_modes = (True, False)\n"
                            "    for exhaustive in exhaustive_modes:\n"
                            "        pass\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "slot_content_python_syntax_error",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_random_empty_move_fallback(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "fallback_random_shake",
                        "type": "local_search_operator",
                        "novelty": "Different from failed near-critical filters by adding a random fallback.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    if not all_moves:\n"
                            "        operations = list(schedule.index.real_nodes)\n"
                            "        schedule.rng.shuffle(operations)\n"
                            "        target = schedule.rng.choice(operations)\n"
                            "        consider_same(BACK, operations[0], target)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "neighborhood_adds_random_no_move_fallback",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_setup_empty_move_fallback(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_fallback_candidates",
                        "type": "local_search_operator",
                        "novelty": "Different from failed random fallback by using setup-heavy arcs.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    if not all_moves:\n"
                            "        setup_heavy_nodes = list(schedule.index.real_nodes)\n"
                            "        for node in setup_heavy_nodes:\n"
                            "            consider_same(BACK, node, node)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "neighborhood_adds_setup_no_move_fallback",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_when_change_machine_is_only_empty_fallback(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "same_machine_first_fallback_change",
                        "type": "local_search_operator",
                        "novelty": "Different from failed random fallback by prioritizing same-machine moves.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    blocks = critical_blocks(schedule, schedule.rng, exhaustive=False)\n"
                            "    for block in blocks:\n"
                            "        consider_same(BACK, block[0], block[-1])\n"
                            "    if not all_moves:\n"
                            "        for node in schedule.index.real_nodes:\n"
                            "            sequence, rk_start, lk_end = change_machine_window(schedule, node, schedule.on_machine[node])\n"
                            "            consider_change(CHANGE_MACHINE_BACK, node, sequence[0], -1, -1)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "neighborhood_gates_change_machine_on_empty_same_moves",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_latest_block_topk_overpruning(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "latest_block_topk",
                        "type": "local_search_operator",
                        "novelty": "Avoids failed window filters by using latest critical blocks.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    blocks = critical_blocks(schedule, schedule.rng, exhaustive=False)\n"
                            "    blocks.sort(key=lambda block: schedule.end_time[block[-1]], reverse=True)\n"
                            "    top_k = 3\n"
                            "    for block in blocks[:top_k]:\n"
                            "        consider_same(BACK, block[0], block[-1])\n"
                            "    for node in schedule.index.real_nodes:\n"
                            "        if schedule.is_critical_operation(node):\n"
                            "            sequence, rk_start, lk_end = change_machine_window(schedule, node, schedule.on_machine[node])\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "neighborhood_retries_latest_block_topk_overpruning",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_global_move_count_cap_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "BoundedNeighborhood",
                        "type": "local_search_operator",
                        "novelty": "Different from exhaustive search by using a global cap.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    MAX_MOVES = 200\n"
                            "    for node in schedule.index.real_nodes:\n"
                            "        for candidate_machine in schedule.index.candidates[node]:\n"
                            "            sequence, rk_start, lk_end = change_machine_window(schedule, node, candidate_machine)\n"
                            "            if len(all_moves) >= MAX_MOVES:\n"
                            "                break\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("neighborhood_retries_global_move_count_cap", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_unordered_candidate_machine_cap_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "bounded_nk_alternate_machine",
                        "type": "local_search_operator",
                        "novelty": "Different from global caps by limiting machines per operation.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    max_candidate_machines = 3\n"
                            "    for node in schedule.index.real_nodes:\n"
                            "        candidate_list = list(schedule.index.candidates[node])\n"
                            "        bounded_candidates = [m for m in candidate_list if m != schedule.on_machine[node]]\n"
                            "        for candidate_machine in bounded_candidates[:max_candidate_machines]:\n"
                            "            sequence, rk_start, lk_end = change_machine_window(schedule, node, candidate_machine)\n"
                            "            if sequence:\n"
                            "                consider_change(CHANGE_MACHINE_BACK, node, sequence[0], -1, -1)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "neighborhood_retries_unordered_candidate_machine_cap",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_allows_ordered_candidate_machine_cap(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "setup_ordered_bounded_nk",
                        "type": "local_search_operator",
                        "novelty": "Different from unordered caps by sorting alternate machines before slicing.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    max_candidate_machines = 3\n"
                            "    for node in schedule.index.real_nodes:\n"
                            "        candidate_list = list(schedule.index.candidates[node])\n"
                            "        bounded_candidates = sorted(candidate_list, key=lambda m: schedule.index.duration(node, m))\n"
                            "        for candidate_machine in bounded_candidates[:max_candidate_machines]:\n"
                            "            sequence, rk_start, lk_end = change_machine_window(schedule, node, candidate_machine)\n"
                            "            if sequence:\n"
                            "                consider_change(CHANGE_MACHINE_BACK, node, sequence[0], -1, -1)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertNotIn(
            "neighborhood_retries_unordered_candidate_machine_cap",
            normalized["proposal_audit"]["warnings"],
        )

    def test_neighborhood_slot_warns_on_random_diversity_sampling_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "BoundedDiversitySampling",
                        "type": "local_search_operator",
                        "novelty": "Different from global cap by sampling blocks.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    max_blocks = 10\n"
                            "    max_same_per_block = 3\n"
                            "    total_move_limit = 50\n"
                            "    blocks = critical_blocks(schedule, schedule.rng, exhaustive=False)\n"
                            "    schedule.rng.shuffle(blocks)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("neighborhood_retries_random_diversity_sampling", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_random_change_only_lane_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "randomized_change_only_lane",
                        "type": "local_search_operator",
                        "novelty": "Different from global caps by randomly selecting a change-only lane.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    change_only_prob = 50\n"
                            "    use_change_only = schedule.rng.randrange(100) < change_only_prob\n"
                            "    for block in critical_blocks(schedule, schedule.rng, exhaustive=False):\n"
                            "        if not use_change_only:\n"
                            "            consider_same(BACK, block[0], block[-1])\n"
                            "    for node in schedule.index.real_nodes:\n"
                            "        sequence, rk_start, lk_end = change_machine_window(schedule, node, schedule.on_machine[node])\n"
                            "        if sequence:\n"
                            "            consider_change(CHANGE_MACHINE_BACK, node, sequence[0], -1, -1)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("neighborhood_retries_random_change_only_lane", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_neighborhood_slot_warns_on_shuffling_candidate_machine_dict(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_neighborhood_selection")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "shuffle_candidate_machine_dict",
                        "type": "local_search_operator",
                        "novelty": "Different from setup fallback by shuffling candidate machines.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_neighborhood_selection",
                        "content": (
                            "    candidate_machines = schedule.index.candidates[node]\n"
                            "    schedule.rng.shuffle(candidate_machines)\n"
                            "    for candidate_machine in candidate_machines:\n"
                            "        consider_change(CHANGE_MACHINE_FRONT, node, candidate_machine, -1, -1)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("neighborhood_shuffles_candidate_machine_dict", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_portfolio_slot_warns_on_seed_mapping_only_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_portfolio_search_control")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "perturbed_seed_mapping",
                        "type": "search_control",
                        "novelty": "Avoids failed lane deepening by changing the deterministic seed mapping.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_portfolio_search_control",
                        "content": (
                            "    lane_budgets = allocate_lane_budgets(portfolio_lanes, time_limit_sec)\n"
                            "    for idx, (lane, lane_budget) in enumerate(zip(portfolio_lanes, lane_budgets, strict=True)):\n"
                            "        effective_lane_seed = (lane.seed + seed * PORTFOLIO_OUTER_SEED_STRIDE + idx * 7919) % 10000\n"
                            "        candidate = solve_awls_single(index, seed=effective_lane_seed, restarts=lane.restarts, cycles_per_restart=cycles_per_restart, iterations=iterations, time_limit_sec=lane_budget, init_mode=lane.init_mode, beta=beta, gamma=gamma, theta=theta, exact_select_top_k=exact_select_top_k, same_machine_eval=same_machine_eval, critical_block_exhaustive_pct=critical_block_exhaustive_pct, zi_policy=zi_policy, zi_formula=zi_formula, initial_state=initial_state, time_check_interval=time_check_interval, cycle_trace=cycle_trace)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn("portfolio_retries_seed_mapping_only", normalized["proposal_audit"]["warnings"])
        self.assertTrue(generic_slot_needs_repair(normalized))

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

    def test_generic_slot_audit_rejects_empty_proposal_with_algorithm_risk_note(self) -> None:
        worker = DeepSeekSlotWorker()
        slot = _generic_slot_context(slot_id="awls_sdst_weight_update")["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "summary": "Describe a setup-aware weight update without code.",
                "strategy_intent": "Skip editing.",
                "changes": [],
                "risk_notes": [
                    "The setup ratio may be noisy on instances with few operations, but the fallback handles missing neighbors."
                ],
            },
            slot,
        )

        self.assertEqual([], normalized["changes"])
        self.assertIn(
            "empty_slot_proposal_without_concrete_blocker",
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

    def test_same_machine_slot_warns_when_pure_exact_trial_repeats(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "exact_trial_same_machine_eval",
                        "type": "local_search_operator",
                        "novelty": "Avoids setup propagation by using exact trial.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    legacy = 1.0\n"
                            "    if not schedule.index.instance.has_sequence_dependent_setup:\n"
                            "        return legacy\n"
                            "    try:\n"
                            "        trial = schedule.clone()\n"
                            "        trial.apply_move(move)\n"
                            "        return float(trial.makespan) + 0.001 * float(legacy)\n"
                            "    except (ValueError, KeyError, IndexError):\n"
                            "        return legacy\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_retries_pure_exact_trial",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_same_machine_slot_warns_when_legacy_ratio_exact_gate_repeats(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "exact_trial_with_legacy_ratio_gate",
                        "type": "local_search_operator",
                        "novelty": "Avoids setup propagation by gating exact trial with legacy score.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    legacy = 1.0\n"
                            "    if not schedule.index.instance.has_sequence_dependent_setup:\n"
                            "        return legacy\n"
                            "    if legacy <= schedule.makespan * 1.1:\n"
                            "        try:\n"
                            "            trial = schedule.clone()\n"
                            "            trial.apply_move(move)\n"
                            "            return float(trial.makespan) + 0.001 * float(legacy)\n"
                            "        except (ValueError, KeyError, IndexError):\n"
                            "            return legacy\n"
                            "    return legacy\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_retries_legacy_ratio_exact_gate",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_same_machine_slot_warns_when_exact_setup_tiebreak_repeats(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "exact_trial_setup_tiebreak",
                        "type": "local_search_operator",
                        "novelty": "Avoids pure exact trial by adding setup tie-breaking.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    legacy = 1.0\n"
                            "    if not schedule.index.instance.has_sequence_dependent_setup:\n"
                            "        return legacy\n"
                            "    try:\n"
                            "        trial = schedule.clone()\n"
                            "        trial.apply_move(move)\n"
                            "        from harness_agent.standard_fjsp import setup_time_between\n"
                            "        total_setup = 0.0\n"
                            "        total_setup += setup_time_between(schedule.index.instance, schedule.on_machine[move.which], (0, 0), (0, 1), schedule.index)\n"
                            "        return float(trial.makespan) + 0.001 * total_setup\n"
                            "    except (ValueError, KeyError, IndexError):\n"
                            "        return legacy\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_retries_exact_setup_tiebreak",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_same_machine_slot_warns_on_noncritical_worsening_exact_gate_retry(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "exact_trial_criticality_gate",
                        "type": "local_search_operator",
                        "novelty": "Avoids pure exact trial with a non-critical worsening gate.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    legacy = 1.0\n"
                            "    if not schedule.index.instance.has_sequence_dependent_setup:\n"
                            "        return legacy\n"
                            "    try:\n"
                            "        trial = schedule.clone()\n"
                            "        trial.apply_move(move)\n"
                            "        trial_makespan = float(trial.makespan)\n"
                            "        op = move.which\n"
                            "        if schedule.end_time[op] + schedule.backward_path_length[op] < schedule.makespan:\n"
                            "            if trial_makespan > schedule.makespan:\n"
                            "                trial_makespan += 0.1 * (trial_makespan - schedule.makespan)\n"
                            "        return trial_makespan\n"
                            "    except (ValueError, KeyError, IndexError):\n"
                            "        return legacy\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_retries_noncritical_worsening_exact_gate",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_same_machine_slot_warns_on_nonexistent_move_node_api(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "critical_gate_move_node",
                        "type": "local_search_operator",
                        "novelty": "Uses a critical-path gate and flow-time tie-breaker.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    legacy = 1.0\n"
                            "    node = move.node\n"
                            "    if schedule.forward_path_length[node] < schedule.makespan:\n"
                            "        return legacy\n"
                            "    trial = schedule.clone()\n"
                            "    trial.apply_move(move)\n"
                            "    return float(trial.makespan)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_uses_nonexistent_move_node",
            normalized["proposal_audit"]["warnings"],
        )
        self.assertTrue(generic_slot_needs_repair(normalized))

    def test_same_machine_slot_warns_on_end_node_end_time_as_makespan(self) -> None:
        worker = DeepSeekSlotWorker()
        context = _generic_slot_context(slot_id="awls_sdst_same_machine_evaluation")
        slot = context["slot_manifest"]["slots"][0]

        normalized = worker._normalize_generic_slot_proposal(  # noqa: SLF001 - regression-tests worker normalization.
            {
                "rule_operator_hypotheses": [
                    {
                        "name": "end_node_makespan_gate",
                        "type": "local_search_operator",
                        "novelty": "Uses a makespan gate before exact trial.",
                        "target_files": ["examples/standard_fjsp_awls_solver.py"],
                    }
                ],
                "changes": [
                    {
                        "action": "replace_slot_block",
                        "slot_id": "awls_sdst_same_machine_evaluation",
                        "content": (
                            "    legacy = 1.0\n"
                            "    makespan = schedule.end_time[schedule.index.end_node]\n"
                            "    if makespan <= legacy:\n"
                            "        return legacy\n"
                            "    trial = schedule.clone()\n"
                            "    trial.apply_move(move)\n"
                            "    return float(trial.makespan)\n"
                        ),
                    }
                ],
            },
            slot,
            context=context,
        )

        self.assertIn(
            "same_machine_uses_end_node_end_time_as_makespan",
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
        self.assertEqual(0, rejected["proposal_audit"]["accepted_change_count"])
        self.assertEqual([], rejected["proposal_audit"]["accepted_change_paths"])
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
    elif slot_id == "awls_sdst_weight_update":
        title = "AWLS-SDST adaptive operation weight update"
        purpose = "Update op_weight and op_cooldown after accepted moves."
        inputs = ["schedule", "moved_node", "previous_makespan", "current_makespan", "beta", "gamma", "theta"]
        outputs = ["Mutate schedule.op_weight and schedule.op_cooldown only."]
        tags = ["awls", "sdst", "zi", "adaptive_weight", "weight_update"]
    elif slot_id == "awls_sdst_search_transition":
        title = "AWLS-SDST tabu search state transition"
        purpose = "Control post-move tabu_search state transitions without changing parser or evaluator."
        inputs = ["current", "best", "move", "iteration", "previous_makespan", "best_before", "stats"]
        outputs = ["May assign best/current AwlsSchedule clones and update stats counters."]
        tags = ["awls", "sdst", "search_control", "tabu_search", "search_transition"]
    elif slot_id == "awls_sdst_tabu_memory":
        title = "AWLS-SDST tabu memory update"
        purpose = "Control the local sequence and tenure inserted into the tabu list."
        inputs = ["tabu", "schedule", "move", "iteration", "tenure_min", "tenure_max"]
        outputs = ["Call tabu.add exactly once with machine_id, sequence, and expires_at."]
        tags = ["awls", "sdst", "search_control", "tabu_search", "tabu_memory"]
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
