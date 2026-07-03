from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context_packet import ContextPacketRequest, write_context_packet
from harness_agent.domain_pack import get_domain_pack, load_domain_pack, load_domain_packs
from harness_agent.knowledge_registry import auto_knowledge_cards
from harness_agent.problem_families import get_problem_family
from harness_agent.slot_manifest import write_selected_slot_manifest


ROOT = Path(__file__).resolve().parents[1]


class DomainPackTests(unittest.TestCase):
    def test_standard_fjsp_capability_loads_from_external_domain_pack(self) -> None:
        pack = get_domain_pack("FJSP")

        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertEqual("standard_fjsp", pack.family_id)
        self.assertTrue(pack.source_path)
        self.assertTrue(str(pack.source_path).endswith("domain_packs\\standard_fjsp\\domain_pack.json") or str(pack.source_path).endswith("domain_packs/standard_fjsp/domain_pack.json"))
        self.assertIn("fjsp_sdst", pack.aliases)
        self.assertIn("sdst", pack.capability.knowledge_tags)

        capability = get_problem_family("fjsp_sdst")
        self.assertEqual("standard_fjsp", capability.family_id)
        self.assertIn("FJSP-SDST", " ".join(capability.io_contract_notes))

    def test_domain_pack_declares_knowledge_retrieval_without_backend_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pack_dir = tmp_path / "domain_packs" / "toy"
            pack_dir.mkdir(parents=True)
            card = tmp_path / "toy_card.md"
            card.write_text("# toy\n", encoding="utf-8")
            manifest = pack_dir / "domain_pack.json"
            manifest.write_text(
                json.dumps(
                    {
                        "family_id": "toy",
                        "aliases": ["toy_alias"],
                        "capability": {
                            "display_name": "Toy",
                            "description": "A toy external pack.",
                            "supported_variants": ["toy"],
                            "canonical_objectives": [{"name": "cost", "direction": "minimize"}],
                            "io_contract_notes": ["toy IO"],
                            "evaluator_invariants": ["toy evaluator"],
                            "solver_entrypoints": ["solver.py"],
                            "knowledge_tags": ["toy_tag"],
                        },
                        "knowledge": {
                            "base_cards": [str(card)],
                            "tagged_cards": {"toy_tag": [str(card)]},
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            pack = load_domain_pack(manifest, project_root=tmp_path)
            packs = load_domain_packs(domain_pack_root=tmp_path / "domain_packs", project_root=tmp_path)

        self.assertEqual("toy", pack.family_id)
        self.assertIn("toy", packs)
        self.assertIn("toy_alias", packs)
        self.assertEqual([card.resolve()], pack.base_cards)
        self.assertEqual([card.resolve()], pack.tagged_cards["toy_tag"])

    def test_auto_knowledge_cards_uses_domain_pack_tags_and_selected_slot_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            slot_manifest = tmp_path / "slot_manifest.json"
            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=slot_manifest,
                selected_slot_ids=["awls_sdst_weight_update"],
            )
            slot_payload = json.loads(slot_manifest.read_text(encoding="utf-8"))

        cards = auto_knowledge_cards(
            problem_family="standard_fjsp",
            problem_family_tags=["sdst"],
            slot_manifest=slot_payload,
        )
        card_paths = {str(path.relative_to(ROOT)).replace("\\", "/") for path in cards if path.is_relative_to(ROOT)}

        self.assertIn("knowledge/benchmarks/standard_fjsp_format.md", card_paths)
        self.assertIn("knowledge/papers/awls_sdst_hudata20_baseline_notes.md", card_paths)
        self.assertIn("knowledge/papers/awls_sdst_weight_update_notes.md", card_paths)

    def test_context_packet_embeds_domain_pack_capability_and_auto_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contract = tmp_path / "contract.json"
            instance = tmp_path / "tiny.fjs"
            instance.write_text((ROOT / "examples" / "standard_fjsp_tiny.fjs").read_text(encoding="utf-8"), encoding="utf-8")
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "domain_pack_context",
                        "problem_family": "fjsp_sdst",
                        "description": "domain pack context smoke",
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

            output = write_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=tmp_path / "context_packet.json",
                    hypothesis="Domain pack context smoke.",
                )
            )
            packet = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("standard_fjsp", packet["problem_family_capability"]["family_id"])
        auto_cards = {Path(path).name for path in packet["auto_knowledge_cards"]}
        self.assertIn("standard_fjsp_format.md", auto_cards)
        self.assertIn("awls_sdst_hudata20_baseline_notes.md", auto_cards)


if __name__ == "__main__":
    unittest.main()
