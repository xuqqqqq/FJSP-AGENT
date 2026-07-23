from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness_agent.deepseek_client import DeepSeekChatResult
from harness_agent.agents.semantic import (
    AlgorithmSemanticReviewRequest,
    DeepSeekAlgorithmSemanticReviewer,
    EvidenceOnlySemanticReviewer,
    load_review_knowledge,
    load_review_sources,
    normalize_semantic_review,
    semantic_review_json_repair_prompt,
    semantic_review_prompt,
)


class SemanticReviewTests(unittest.TestCase):
    def test_complete_package_review_excludes_reference_implementation_asset(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract = root / "knowledge" / "method_packages" / "standard_fjsp_awls_hgtsa" / "implementation_contract.json"
        behavior = root / "knowledge" / "method_packages" / "standard_fjsp_awls_hgtsa" / "behavior_contract.md"
        reference = root / "knowledge" / "method_packages" / "standard_fjsp_awls_hgtsa" / "reference_solver.py"
        context = {
            "task": {"problem_family": "FJSP"},
            "knowledge_cards": [
                {"path": str(contract)},
                {"path": str(behavior)},
                {"path": str(reference)},
            ],
            "active_method_package": {
                "implementation_contract_assets": [str(contract)],
                "semantic_assets": [str(behavior)],
                "implementation_asset": str(reference),
            },
        }

        loaded = load_review_knowledge(
            context=context,
            direction_plan={
                "implementation_bundle": {"contract_id": "standard_complete"},
                "knowledge_paths": [str(reference), str(behavior), str(contract)],
            },
        )

        loaded_paths = list(loaded)
        self.assertTrue(any(path.endswith("implementation_contract.json") for path in loaded_paths))
        self.assertTrue(any(path.endswith("behavior_contract.md") for path in loaded_paths))
        self.assertFalse(any(path.endswith("reference_solver.py") for path in loaded_paths))

    def test_semantic_json_retry_does_not_resend_source_and_knowledge_bodies(self) -> None:
        prompt = semantic_review_json_repair_prompt(
            '{"summary":"draft"}',
            sources={"solver.py": "SECRET_SOURCE_BODY"},
            knowledge={"contract.md": "SECRET_KNOWLEDGE_BODY"},
        )

        self.assertIn("solver.py", prompt)
        self.assertIn("contract.md", prompt)
        self.assertNotIn("SECRET_SOURCE_BODY", prompt)
        self.assertNotIn("SECRET_KNOWLEDGE_BODY", prompt)

    def test_semantic_primary_prompt_numbers_source_once(self) -> None:
        prompt = semantic_review_prompt(
            direction_plan={},
            candidate_summary={},
            sources={"solver.py": "def solve():\n    return 1\n"},
            knowledge={"contract.md": "The solver must return a result."},
        )

        self.assertIn("1: def solve():", prompt)
        self.assertIn("2:     return 1", prompt)
        self.assertLess(prompt.index("Knowledge contracts"), prompt.index("Direction plan"))
        self.assertLess(
            prompt.index("Knowledge contracts"),
            prompt.index("Candidate source with authoritative"),
        )

    def test_verified_source_and_exact_knowledge_quote_can_block(self) -> None:
        result = normalize_semantic_review(
            {
                "summary": "Forward tabu attribute does not forbid reversal.",
                "findings": [
                    {
                        "finding_id": "reverse_move",
                        "category": "reverse_move_memory",
                        "severity": "blocking",
                        "confidence": 0.95,
                        "claim": "The loop implements tabu search.",
                        "source_path": "solver.py",
                        "line_start": 2,
                        "line_end": 2,
                        "knowledge_path": "contract.md",
                        "knowledge_quote": "tabu memory must record the attribute that would undo that move",
                        "explanation": "The accepted forward signature is stored unchanged.",
                        "repair": "Store the inverse move attribute.",
                        "required_test": "Accept one move and prove its inverse remains tabu.",
                    }
                ],
            },
            sources={"solver.py": "def search():\n    tabu[move_signature(move)] = expiry\n"},
            knowledge={
                "contract.md": "After accepting a move, tabu memory must record the attribute that would undo that move."
            },
            reviewer="test",
        )

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)
        self.assertTrue(result.findings[0]["blocking"])
        self.assertIn("tabu[move_signature", result.findings[0]["source_excerpt"])

    def test_unique_relative_knowledge_path_resolves_to_loaded_absolute_path(self) -> None:
        absolute_contract = (
            "F:/workspace/knowledge/references/standard_fjsp/"
            "standard_fjsp_algorithm_semantic_review_contract.md"
        )
        result = normalize_semantic_review(
            {
                "summary": "Critical blocks merge operations separated by idle time.",
                "findings": [
                    {
                        "finding_id": "critical_block_tight_arc",
                        "category": "operator_fidelity",
                        "severity": "blocking",
                        "confidence": 0.95,
                        "claim": "Critical blocks are built without checking tight machine arcs.",
                        "source_path": "examples/solver.py",
                        "line_start": 2,
                        "line_end": 2,
                        "knowledge_path": (
                            "knowledge/references/standard_fjsp/"
                            "standard_fjsp_algorithm_semantic_review_contract.md"
                        ),
                        "knowledge_quote": (
                            "adjacent operations with idle time between them must not be merged"
                        ),
                        "explanation": "The grouping checks only machine identity.",
                        "repair": "Traverse each machine sequence and require a tight arc.",
                        "required_test": "Keep two critical operations in separate blocks when idle time exists.",
                    }
                ],
            },
            sources={"examples/solver.py": "def blocks():\n    return group_by_machine(critical_ops)\n"},
            knowledge={
                absolute_contract: (
                    "Adjacent operations with idle time between them must not be merged into the same critical block."
                )
            },
            reviewer="test",
        )

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)
        self.assertEqual(absolute_contract, result.findings[0]["knowledge_path"])

    def test_markdown_list_wrapping_does_not_invalidate_verbatim_content(self) -> None:
        result = normalize_semantic_review(
            {
                "findings": [
                    {
                        "severity": "blocking",
                        "confidence": 0.95,
                        "source_path": "solver.py",
                        "line_start": 1,
                        "line_end": 1,
                        "knowledge_path": "contract.md",
                        "knowledge_quote": (
                            "The inverse attribute uses the old machine and old insertion context, "
                            "not the new machine sequence."
                        ),
                        "repair": "Store the old machine context as the inverse attribute.",
                        "required_test": "Apply a move and prove its immediate inverse remains tabu.",
                    }
                ]
            },
            sources={"solver.py": "tabu[new_machine][forward_context] = expiry"},
            knowledge={
                "contract.md": (
                    "- The inverse attribute uses the old machine and old\n"
                    "  insertion context, not the new machine sequence."
                )
            },
            reviewer="test",
        )

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)

    def test_fabricated_quote_or_low_confidence_cannot_block(self) -> None:
        sources = {"solver.py": "def search():\n    return current\n"}
        knowledge = {"contract.md": "The search must return the global best state."}
        fabricated = normalize_semantic_review(
            {
                "findings": [
                    {
                        "severity": "blocking",
                        "confidence": 0.99,
                        "source_path": "solver.py",
                        "line_start": 2,
                        "line_end": 2,
                        "knowledge_path": "contract.md",
                        "knowledge_quote": "This quote is not in the contract.",
                    }
                ]
            },
            sources=sources,
            knowledge=knowledge,
            reviewer="test",
        )
        low_confidence = normalize_semantic_review(
            {
                "findings": [
                    {
                        "severity": "blocking",
                        "confidence": 0.6,
                        "source_path": "solver.py",
                        "line_start": 2,
                        "line_end": 2,
                        "knowledge_path": "contract.md",
                        "knowledge_quote": "The search must return the global best state.",
                    }
                ]
            },
            sources=sources,
            knowledge=knowledge,
            reviewer="test",
        )

        self.assertEqual("warning", fabricated.status)
        self.assertTrue(fabricated.accepted)
        self.assertIn("Rejected 1 proposed semantic finding", fabricated.summary)
        self.assertIn("No verified semantic mismatch remains", fabricated.summary)
        self.assertNotIn("This quote is not in the contract", fabricated.summary)
        self.assertEqual("warning", low_confidence.status)
        self.assertTrue(low_confidence.accepted)
        self.assertFalse(low_confidence.findings[0]["blocking"])

    def test_full_solver_source_is_loaded_beyond_incumbent_snippet_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solver = root / "solver.py"
            solver.write_text("# padding\n" + ("x = 1\n" * 4000) + "# semantic-tail-marker\n", encoding="utf-8")

            sources = load_review_sources(
                context={
                    "evaluator_protocol": {
                        "solver_command_template": "python solver.py --input {instance} --output {solution}"
                    }
                },
                worktree_path=root,
                changed_files=[],
            )

            self.assertGreater(len(sources["solver.py"]), 16_000)
            self.assertIn("semantic-tail-marker", sources["solver.py"])

    def test_unavailable_model_reviewer_is_not_accepted(self) -> None:
        reviewer = DeepSeekAlgorithmSemanticReviewer()
        with tempfile.TemporaryDirectory() as tmp:
            request = AlgorithmSemanticReviewRequest(
                round_index=0,
                attempt_index=0,
                context_packet_path=Path(tmp) / "context.json",
                worktree_path=Path(tmp),
                changed_files=[],
                direction_plan={},
                candidate_summary={},
                output_dir=Path(tmp) / "review",
            )
            with (
                patch("harness_agent.agents.semantic.is_deepseek_configured", return_value=True),
                patch("harness_agent.agents.semantic.load_context_dict", side_effect=RuntimeError("offline")),
            ):
                result = reviewer.review(request)

        self.assertEqual("unavailable", result.status)
        self.assertFalse(result.accepted)

    def test_non_json_review_gets_one_structured_retry(self) -> None:
        reviewer = DeepSeekAlgorithmSemanticReviewer()
        client = Mock()
        client.chat_with_usage.side_effect = [
            DeepSeekChatResult(
                content="The reverse tabu signature is wrong and should be blocking.",
                usage={"prompt_tokens": 100, "completion_tokens": 20},
            ),
            DeepSeekChatResult(
                content=json.dumps(
                    {
                        "summary": "Reverse move memory is inconsistent.",
                        "findings": [
                            {
                                "finding_id": "reverse_move",
                                "category": "move_memory",
                                "severity": "blocking",
                                "confidence": 0.95,
                                "claim": "The loop implements reverse-move tabu memory.",
                                "source_path": "solver.py",
                                "line_start": 2,
                                "line_end": 2,
                                "knowledge_path": "contract.md",
                                "knowledge_quote": "tabu memory must record the attribute that would undo that move",
                                "explanation": "The forward attribute is stored instead.",
                                "repair": "Store the inverse move attribute after acceptance.",
                                "required_test": "Accept one move and prove its inverse remains tabu.",
                            }
                        ],
                    }
                ),
                usage={"prompt_tokens": 30, "completion_tokens": 40},
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = AlgorithmSemanticReviewRequest(
                round_index=0,
                attempt_index=0,
                context_packet_path=root / "context.json",
                worktree_path=root,
                changed_files=["solver.py"],
                direction_plan={},
                candidate_summary={},
                output_dir=root / "review",
            )
            with (
                patch("harness_agent.agents.semantic.is_deepseek_configured", return_value=True),
                patch("harness_agent.agents.semantic.load_context_dict", return_value={}),
                patch(
                    "harness_agent.agents.semantic.load_review_sources",
                    return_value={"solver.py": "def search():\n    tabu[forward] = expiry\n"},
                ),
                patch(
                    "harness_agent.agents.semantic.load_review_knowledge",
                    return_value={
                        "contract.md": "Tabu memory must record the attribute that would undo that move."
                    },
                ),
                patch(
                    "harness_agent.agents.semantic.DeepSeekClient.from_env",
                    return_value=client,
                ) as client_factory,
            ):
                result = reviewer.review(request)

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)
        self.assertEqual(2, client.chat_with_usage.call_count)
        client_factory.assert_called_once_with(model="deepseek-v4-pro", timeout_seconds=300)
        self.assertEqual(130, result.usage["prompt_tokens"])
        self.assertIn("json_retry_response", result.artifacts)
        self.assertIn("usage", result.artifacts)

    def test_missing_or_partial_component_coverage_blocks_without_findings(self) -> None:
        result = normalize_semantic_review(
            {
                "summary": "No semantic mismatch found.",
                "component_coverage": [
                    {
                        "component_id": "decoder",
                        "status": "partial",
                        "source_path": "solver.py",
                        "line_start": 1,
                        "line_end": 1,
                        "evidence": "Only the parse step is reachable.",
                        "behavior_coverage": [
                            {
                                "behavior_index": 1,
                                "status": "partial",
                                "source_path": "solver.py",
                                "line_start": 1,
                                "line_end": 1,
                                "evidence": "Only parsing is implemented; no schedule is built.",
                            }
                        ],
                        "missing_behaviors": ["Decode never schedules all operations."],
                    }
                ],
                "findings": [],
            },
            sources={"solver.py": "def parse():\n    return []\n"},
            knowledge={"contract.md": "Generic contract."},
            required_components=[
                {
                    "component_id": "decoder",
                    "title": "Decoder",
                    "required_behaviors": ["Decode every operation."],
                },
                {
                    "component_id": "search",
                    "title": "Search",
                    "required_behaviors": ["Search legal neighbors."],
                },
            ],
            reviewer="test",
        )

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)
        self.assertFalse(result.coverage_complete)
        self.assertEqual(
            ["partial", "missing"],
            [item["status"] for item in result.component_coverage],
        )
        self.assertIn("Complete-method coverage is missing or partial", result.summary)

    def test_full_component_coverage_passes_without_findings(self) -> None:
        source = (
            "def decode_state(state):\n"
            "    return state\n\n"
            "def tabu_search(state):\n"
            "    return decode_state(state)\n"
        )
        result = normalize_semantic_review(
            {
                "summary": "Full bundle coverage verified.",
                "component_coverage": [
                    {
                        "component_id": "decoder",
                        "status": "implemented",
                        "source_path": "solver.py",
                        "line_start": 1,
                        "line_end": 2,
                        "evidence": "decode_state is reachable.",
                        "behavior_coverage": [
                            {
                                "behavior_index": 1,
                                "status": "implemented",
                                "source_path": "solver.py",
                                "line_start": 1,
                                "line_end": 2,
                                "evidence": "decode_state returns the decoded state on the reachable path.",
                            }
                        ],
                    },
                    {
                        "component_id": "search",
                        "status": "implemented",
                        "source_path": "solver.py",
                        "line_start": 4,
                        "line_end": 5,
                        "evidence": "tabu_search calls decode_state.",
                        "behavior_coverage": [
                            {
                                "behavior_index": 1,
                                "status": "implemented",
                                "source_path": "solver.py",
                                "line_start": 4,
                                "line_end": 5,
                                "evidence": "tabu_search reaches decode_state during its search path.",
                            }
                        ],
                    },
                ],
                "findings": [],
            },
            sources={"solver.py": source},
            knowledge={"contract.md": "Generic contract."},
            required_components=[
                {
                    "component_id": "decoder",
                    "title": "Decoder",
                    "required_behaviors": ["Decode the state."],
                },
                {
                    "component_id": "search",
                    "title": "Search",
                    "required_behaviors": ["Invoke decoding from search."],
                },
            ],
            reviewer="test",
        )

        self.assertEqual("pass", result.status)
        self.assertTrue(result.accepted)
        self.assertTrue(result.coverage_complete)
        self.assertEqual(
            ["implemented", "implemented"],
            [item["status"] for item in result.component_coverage],
        )

    def test_partial_coupled_group_blocks_complete_components(self) -> None:
        source = "def generate():\n    return {'move': 1}\n\ndef apply(move):\n    return move\n"
        result = normalize_semantic_review(
            {
                "summary": "Helpers exist but the selected move is not passed to apply.",
                "component_coverage": [
                    {
                        "component_id": "generator",
                        "status": "implemented",
                        "source_path": "solver.py",
                        "line_start": 1,
                        "line_end": 2,
                        "behavior_coverage": [
                            {
                                "behavior_index": 1,
                                "status": "implemented",
                                "source_path": "solver.py",
                                "line_start": 1,
                                "line_end": 2,
                                "evidence": "generate returns a stable move object to the caller.",
                            }
                        ],
                    },
                    {
                        "component_id": "application",
                        "status": "implemented",
                        "source_path": "solver.py",
                        "line_start": 4,
                        "line_end": 5,
                        "behavior_coverage": [
                            {
                                "behavior_index": 1,
                                "status": "implemented",
                                "source_path": "solver.py",
                                "line_start": 4,
                                "line_end": 5,
                                "evidence": "apply consumes the supplied move object on its reachable path.",
                            }
                        ],
                    },
                ],
                "coupled_group_coverage": [
                    {
                        "group_id": "move_lifecycle",
                        "status": "partial",
                        "source_path": "solver.py",
                        "line_start": 1,
                        "line_end": 5,
                        "missing_behavior": "The generated move is not consumed by apply.",
                    }
                ],
                "findings": [],
            },
            sources={"solver.py": source},
            knowledge={"contract.md": "Generic contract."},
            required_components=[
                {"component_id": "generator", "required_behaviors": ["Generate a move."]},
                {"component_id": "application", "required_behaviors": ["Apply that move."]},
            ],
            required_coupled_groups=[
                {
                    "group_id": "move_lifecycle",
                    "component_ids": ["generator", "application"],
                    "rule": "The exact generated move must be applied.",
                }
            ],
            reviewer="test",
        )

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)
        self.assertTrue(all(item["status"] == "implemented" for item in result.component_coverage))
        self.assertEqual("partial", result.coupled_group_coverage[0]["status"])
        self.assertIn("move_lifecycle", result.summary)

    def test_component_claim_without_behavior_level_evidence_cannot_pass(self) -> None:
        result = normalize_semantic_review(
            {
                "component_coverage": [
                    {
                        "component_id": "decoder",
                        "status": "implemented",
                        "source_path": "solver.py",
                        "line_start": 1,
                        "line_end": 1,
                        "evidence": "This unrelated line is claimed as a decoder.",
                    }
                ],
                "findings": [],
            },
            sources={"solver.py": "VALUE = 1\n"},
            knowledge={"contract.md": "Generic contract."},
            required_components=[
                {
                    "component_id": "decoder",
                    "required_behaviors": ["Decode every operation through a reachable path."],
                }
            ],
            reviewer="test",
        )

        self.assertEqual("repair_required", result.status)
        self.assertFalse(result.accepted)
        self.assertEqual("missing", result.component_coverage[0]["status"])

    def test_evidence_only_fallback_is_non_blocking(self) -> None:
        result = EvidenceOnlySemanticReviewer().review(
            AlgorithmSemanticReviewRequest(
                round_index=0,
                attempt_index=0,
                context_packet_path=Path("context.json"),
                worktree_path=Path("."),
                changed_files=[],
                direction_plan={},
                candidate_summary={},
                output_dir=Path("."),
            )
        )

        self.assertEqual("skipped", result.status)
        self.assertTrue(result.accepted)


if __name__ == "__main__":
    unittest.main()
