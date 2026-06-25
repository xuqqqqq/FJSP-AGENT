from __future__ import annotations

import unittest
from pathlib import Path

from harness_agent.standard_worker_loop import StandardWorkerLoopRequest, standard_solver_command
from harness_agent.worker import NullWorker
from harness_agent.workers.deepseek_slot_worker import replace_evolve_block, validate_awls_slot_contract, validate_slot_function


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


if __name__ == "__main__":
    unittest.main()
