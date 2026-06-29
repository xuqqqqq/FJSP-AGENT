from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context_packet import ContextPacketRequest, build_context_packet
from harness_agent.problem_families import get_problem_family
from harness_agent.slot_manifest import write_default_slot_manifest


class SlotManifestPlatformTests(unittest.TestCase):
    def test_standard_fjsp_family_card_names_variants_and_invariants(self) -> None:
        family = get_problem_family("FJSP")

        payload = family.to_payload()

        self.assertEqual(payload["family_id"], "standard_fjsp")
        self.assertIn("standard_fjsp", payload["supported_variants"])
        self.assertTrue(any("evaluator" in item.lower() for item in payload["evaluator_invariants"]))
        self.assertIn("slot_manifest_guided_edits", payload["specialization_hooks"])

    def test_default_slot_manifest_contains_confirmable_neighborhood_and_zi_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "slot_manifest.json"

            write_default_slot_manifest(problem_family="standard_fjsp", output=output, confirmed=False)
            payload = json.loads(output.read_text(encoding="utf-8"))

        slot_ids = {item["slot_id"] for item in payload["slots"]}
        self.assertEqual(payload["status"], "draft_requires_user_confirmation")
        self.assertIn("awls_zi_policy", slot_ids)
        self.assertIn("local_search_neighborhood_actions", slot_ids)
        self.assertTrue(payload["confirmation_required"])

    def test_context_packet_includes_problem_family_and_slot_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "slot_context_smoke",
                        "problem_family": "standard_fjsp",
                        "description": "smoke",
                        "instances": [{"id": "tiny", "path": str(instance)}],
                        "objectives": [{"name": "makespan", "direction": "minimize"}],
                        "commands": {
                            "solver": "python solver.py",
                            "evaluator": "python evaluator.py",
                            "quick_test": "python -m compileall .",
                        },
                        "budget": {"rounds": 1, "seeds": [0]},
                        "paths": {"allowed_paths": ["examples"], "forbidden_paths": [".git"]},
                        "review": {"status": "confirmed"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            manifest = root / "slot_manifest.json"
            write_default_slot_manifest(problem_family="standard_fjsp", output=manifest, confirmed=True)

            packet = build_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=root / "context.json",
                    slot_manifest=manifest,
                )
            )

        self.assertEqual(packet["problem_family_capability"]["family_id"], "standard_fjsp")
        self.assertEqual(packet["slot_manifest"]["status"], "confirmed")
        self.assertGreaterEqual(len(packet["slot_manifest"]["slots"]), 2)
        awls_slot = next(item for item in packet["slot_manifest"]["slots"] if item["slot_id"] == "awls_zi_policy")
        self.assertEqual("# EVOLVE_START", awls_slot["marker_start"])
        self.assertEqual("# EVOLVE_END", awls_slot["marker_end"])
        neighborhood_slot = next(
            item for item in packet["slot_manifest"]["slots"] if item["slot_id"] == "local_search_neighborhood_actions"
        )
        self.assertIsInstance(neighborhood_slot["line_start"], int)
        self.assertIsInstance(neighborhood_slot["line_end"], int)
        self.assertIn("critical_machine_blocks", neighborhood_slot["original_content"])
        self.assertIn("    specs = operation_specs(instance)", neighborhood_slot["original_content"])
        self.assertEqual("# SLOT neighborhood_actions START", neighborhood_slot["marker_start"])
        self.assertIn("neighbor_limit: int", neighborhood_slot["context_before"])
        self.assertTrue(packet["auto_knowledge_cards"])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("standard_fjsp_format.md", knowledge_paths)
        self.assertIn("fjsp_scene_survey_2025_10_17.md", knowledge_paths)
        self.assertIn("xiejin_hgtsa_n8_k_insertion_tabu_spec.md", knowledge_paths)
        self.assertIn("Review slot_manifest", " ".join(packet["worker_instruction"]["required_order"]))


if __name__ == "__main__":
    unittest.main()
