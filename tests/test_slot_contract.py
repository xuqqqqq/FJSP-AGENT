from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.slots.contract import (
    extract_block_name,
    extract_marked_block,
    find_confirmed_slot,
    language_for_path,
    locate_marked_block,
    replace_marked_block,
    validate_slot_manifest_gate,
)


class SlotContractTests(unittest.TestCase):
    def test_generic_gate_accepts_confirmed_slot(self) -> None:
        errors = validate_slot_manifest_gate(
            _context(user_confirmed=True),
            "neighborhood",
            expected_target_file="solver.py",
            expected_marker_start="# SLOT neighborhood START",
            expected_marker_end="# SLOT neighborhood END",
        )

        self.assertEqual([], errors)

    def test_generic_gate_rejects_unconfirmed_manifest_and_slot(self) -> None:
        errors = validate_slot_manifest_gate(
            _context(status="draft_requires_user_confirmation", confirmation_required=True, user_confirmed=False),
            "neighborhood",
        )

        self.assertIn("slot_manifest.status must be confirmed", errors)
        self.assertIn("slot_manifest.confirmation_required must be false", errors)
        self.assertIn("slot 'neighborhood' must be user_confirmed", errors)

    def test_generic_gate_rejects_target_or_marker_mismatch(self) -> None:
        errors = validate_slot_manifest_gate(
            _context(user_confirmed=True),
            "neighborhood",
            expected_target_file="other.py",
            expected_marker_start="# WRONG START",
            expected_marker_end="# WRONG END",
        )

        self.assertIn("slot target_file must be 'other.py'", errors)
        self.assertIn("slot marker_start must be '# WRONG START'", errors)
        self.assertIn("slot marker_end must be '# WRONG END'", errors)

    def test_marked_block_extract_replace_and_metadata(self) -> None:
        text = (
            "def solve():\n"
            "    before()\n"
            "    # SLOT neighborhood START\n"
            "    old()\n"
            "    # SLOT neighborhood END\n"
            "    after()\n"
        )

        block = locate_marked_block(text, "# SLOT neighborhood START", "# SLOT neighborhood END")
        updated = replace_marked_block(text, "# SLOT neighborhood START", "# SLOT neighborhood END", "    new()")

        self.assertEqual("neighborhood", block.block_name)
        self.assertEqual(3, block.line_start)
        self.assertEqual(5, block.line_end)
        self.assertEqual("old()", extract_marked_block(text, "# SLOT neighborhood START", "# SLOT neighborhood END").strip())
        self.assertIn("    # SLOT neighborhood START\n    new()\n    # SLOT neighborhood END", updated)

    def test_find_confirmed_slot_resolves_llm4ad_style_block_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "solver.py"
            target.write_text(
                "def solve():\n"
                "    before()\n"
                "    # SLOT neighborhood START\n"
                "    old()\n"
                "    # SLOT neighborhood END\n"
                "    after()\n",
                encoding="utf-8",
            )

            slot = find_confirmed_slot(_context(user_confirmed=True), "neighborhood", worktree_path=root)

        payload = slot.to_block_payload()
        self.assertEqual("solver.py", payload["file_path"])
        self.assertEqual("python", payload["language"])
        self.assertEqual("neighborhood", payload["block_name"])
        self.assertEqual(3, payload["line_start"])
        self.assertEqual(5, payload["line_end"])
        self.assertIn("old()", payload["original_content"])

    def test_language_for_path_matches_common_code_files(self) -> None:
        self.assertEqual("python", language_for_path("solver.py"))
        self.assertEqual("cpp", language_for_path("solver.cpp"))
        self.assertEqual("plaintext", language_for_path("solver.unknown"))

    def test_extract_block_name_preserves_slot_identifier(self) -> None:
        self.assertEqual("neighborhood_actions", extract_block_name("# SLOT neighborhood_actions START"))
        self.assertEqual("", extract_block_name("# EVOLVE_START"))


def _context(
    *,
    status: str = "confirmed",
    confirmation_required: bool = False,
    user_confirmed: bool = True,
) -> dict[str, object]:
    return {
        "slot_manifest": {
            "exists": True,
            "status": status,
            "confirmation_required": confirmation_required,
            "slots": [
                {
                    "slot_id": "neighborhood",
                    "title": "Neighborhood moves",
                    "target_file": "solver.py",
                    "marker_start": "# SLOT neighborhood START",
                    "marker_end": "# SLOT neighborhood END",
                    "purpose": "Generate candidate moves.",
                    "inputs": ["state"],
                    "outputs": ["moves"],
                    "invariants": ["Keep IO stable."],
                    "allowed_edits": ["Edit only marked block."],
                    "forbidden_edits": ["Parser edits."],
                    "validation_commands": ["python -m compileall solver.py"],
                    "knowledge_tags": ["neighborhood"],
                    "user_confirmed": user_confirmed,
                }
            ],
        }
    }


if __name__ == "__main__":
    unittest.main()
