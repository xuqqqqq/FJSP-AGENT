from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness_agent.context.loader import load_context_packet
from harness_agent.context.packet import ContextPacketRequest, write_context_packet


ROOT = Path(__file__).resolve().parents[1]


class ContextLoaderTests(unittest.TestCase):
    def test_v1_packet_is_split_into_stable_and_dynamic_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=Path(tmp) / "context.json",
                    hypothesis="Keep this in the dynamic tail.",
                )
            )

            loaded = load_context_packet(packet_path)

        self.assertEqual(1, loaded.schema_version)
        self.assertEqual("valid", loaded.integrity["status"])
        self.assertIn("task", loaded.stable_context)
        self.assertNotIn("hypothesis", loaded.stable_context)
        self.assertEqual("Keep this in the dynamic tail.", loaded.dynamic_context["hypothesis"])

    def test_hash_mismatch_is_reported_without_hiding_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            payload = self._base_payload()
            payload["packet_hash"] = "not-the-real-hash"
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_context_packet(path)

        self.assertEqual("mismatch", loaded.integrity["status"])
        self.assertTrue(loaded.diagnostics)
        self.assertEqual("loader_test", loaded.effective_context["task"]["task_id"])

    def test_v2_delta_resolves_relative_base_inside_trusted_artifact_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path = root / "base.json"
            base = self._base_payload()
            base["packet_hash"] = self._hash(base)
            base_path.write_text(json.dumps(base), encoding="utf-8")
            round_dir = root / "round_000"
            round_dir.mkdir()
            round_path = round_dir / "context.json"
            round_payload = {
                "packet_type": "algoforge_context_packet",
                "schema_version": 2,
                "base_context_ref": {
                    "path": "../base.json",
                    "packet_hash": base["packet_hash"],
                    "immutable": True,
                },
                "context_delta": {
                    "hypothesis": "delta hypothesis",
                    "worker_instruction": {"required_order": ["read delta"]},
                },
            }
            round_payload["packet_hash"] = self._hash(round_payload)
            round_path.write_text(json.dumps(round_payload), encoding="utf-8")

            loaded = load_context_packet(round_path, artifact_root=root)

        self.assertEqual(2, loaded.schema_version)
        self.assertEqual("loader_test", loaded.effective_context["task"]["task_id"])
        self.assertEqual("delta hypothesis", loaded.effective_context["hypothesis"])
        self.assertEqual("Coding Agent", loaded.effective_context["worker_instruction"]["role"])
        self.assertEqual(["read delta"], loaded.effective_context["worker_instruction"]["required_order"])

    def test_v2_dual_write_uses_flat_context_without_following_advisory_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            payload = self._base_payload()
            payload.update(
                {
                    "schema_version": 2,
                    "base_context_ref": {"path": "C:/outside/base.json", "immutable": True},
                    "context_delta": {"hypothesis": "dual-write delta"},
                }
            )
            payload["packet_hash"] = self._hash(payload)
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_context_packet(path)

        self.assertEqual("dual-write delta", loaded.effective_context["hypothesis"])
        self.assertEqual("loader_test", loaded.effective_context["task"]["task_id"])
        self.assertFalse(any("absolute base_context_ref" in item for item in loaded.diagnostics))

    def _base_payload(self) -> dict[str, object]:
        return {
            "packet_type": "algoforge_context_packet",
            "schema_version": 1,
            "task": {"task_id": "loader_test", "objectives": [], "instances": []},
            "evaluator_protocol": {"solver_command_template": "python solver.py"},
            "edit_policy": {"allowed_paths": ["solver.py"]},
            "worker_instruction": {"role": "Coding Agent", "success_rule": "Core decides."},
            "hypothesis": "base hypothesis",
        }

    def _hash(self, payload: dict[str, object]) -> str:
        canonical = dict(payload)
        canonical.pop("packet_hash", None)
        text = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
