from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_agent.semantic_review import (
    DeepSeekAlgorithmSemanticReviewer,
    EvidenceOnlySemanticReviewer,
    load_review_sources,
    normalize_semantic_review,
)


class SemanticReviewTests(unittest.TestCase):
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
            "F:/workspace/knowledge/imported_huawei_fjsp_knowledge/operators/"
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
                            "knowledge/imported_huawei_fjsp_knowledge/operators/"
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

        self.assertEqual("pass", fabricated.status)
        self.assertTrue(fabricated.accepted)
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

    def test_unavailable_model_reviewer_is_non_blocking(self) -> None:
        reviewer = DeepSeekAlgorithmSemanticReviewer()
        with tempfile.TemporaryDirectory() as tmp:
            from harness_agent.semantic_review import AlgorithmSemanticReviewRequest

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
                patch("harness_agent.semantic_review.is_deepseek_configured", return_value=True),
                patch("harness_agent.semantic_review.load_context_dict", side_effect=RuntimeError("offline")),
            ):
                result = reviewer.review(request)

        self.assertEqual("unavailable", result.status)
        self.assertTrue(result.accepted)

    def test_evidence_only_fallback_is_non_blocking(self) -> None:
        from harness_agent.semantic_review import AlgorithmSemanticReviewRequest

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
