from __future__ import annotations

import json
import shutil
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
        self.assertIn("awls_sdst_initialization", slot_ids)
        self.assertIn("awls_sdst_same_machine_evaluation", slot_ids)
        self.assertIn("awls_sdst_move_evaluation", slot_ids)
        self.assertIn("awls_sdst_portfolio_search_control", slot_ids)
        self.assertIn("awls_sdst_zi_features", slot_ids)
        self.assertIn("awls_sdst_neighborhood_selection", slot_ids)
        same_machine_slot = next(slot for slot in payload["slots"] if slot["slot_id"] == "awls_sdst_same_machine_evaluation")
        same_machine_text = "\n".join(
            same_machine_slot["inputs"] + same_machine_slot["invariants"] + same_machine_slot["forbidden_edits"]
        )
        self.assertIn("setup_time_between(schedule.index.instance", same_machine_text)
        self.assertIn("schedule.setup_time", same_machine_text)
        move_eval_slot = next(slot for slot in payload["slots"] if slot["slot_id"] == "awls_sdst_move_evaluation")
        move_eval_text = "\n".join(move_eval_slot["inputs"] + move_eval_slot["invariants"] + move_eval_slot["forbidden_edits"])
        self.assertIn("change_machine_evaluate_parts", move_eval_text)
        self.assertIn("trial.makespan", move_eval_text)
        self.assertIn("CHANGE_MACHINE_FRONT", move_eval_text)
        portfolio_slot = next(slot for slot in payload["slots"] if slot["slot_id"] == "awls_sdst_portfolio_search_control")
        portfolio_text = "\n".join(
            portfolio_slot["inputs"]
            + portfolio_slot["outputs"]
            + portfolio_slot["invariants"]
            + portfolio_slot["forbidden_edits"]
        )
        self.assertIn("portfolio_lanes", portfolio_text)
        self.assertIn("PORTFOLIO_OUTER_SEED_STRIDE", portfolio_text)
        self.assertIn("Do not parse instance files", portfolio_text)
        zi_features_slot = next(slot for slot in payload["slots"] if slot["slot_id"] == "awls_sdst_zi_features")
        zi_features_text = "\n".join(
            zi_features_slot["inputs"] + zi_features_slot["outputs"] + zi_features_slot["invariants"]
        )
        self.assertIn("setup_prev", zi_features_text)
        self.assertIn("operation_key", zi_features_text)
        self.assertIn("build_zi_feature_values", zi_features_text)
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
        sdst_slot = next(
            item for item in packet["slot_manifest"]["slots"] if item["slot_id"] == "awls_sdst_neighborhood_selection"
        )
        self.assertIsInstance(sdst_slot["line_start"], int)
        self.assertIsInstance(sdst_slot["line_end"], int)
        self.assertIn("consider_same", sdst_slot["original_content"])
        self.assertEqual("# SLOT awls_sdst_neighborhood_selection START", sdst_slot["marker_start"])
        self.assertTrue(packet["auto_knowledge_cards"])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("standard_fjsp_format.md", knowledge_paths)
        self.assertIn("fjsp_scene_survey_2025_10_17.md", knowledge_paths)
        self.assertIn("xiejin_hgtsa_n8_k_insertion_tabu_spec.md", knowledge_paths)
        self.assertIn("Review slot_manifest", " ".join(packet["worker_instruction"]["required_order"]))

    def test_context_packet_includes_sdst_portfolio_slot_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_portfolio_slot_context",
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
            from harness_agent.slot_manifest import write_selected_slot_manifest

            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=manifest,
                selected_slot_ids=["awls_sdst_portfolio_search_control"],
            )

            packet = build_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=root / "context.json",
                    slot_manifest=manifest,
                    project_root=Path.cwd(),
                )
            )

        portfolio_slot = next(
            item for item in packet["slot_manifest"]["slots"] if item["slot_id"] == "awls_sdst_portfolio_search_control"
        )
        self.assertTrue(portfolio_slot["user_confirmed"])
        self.assertIn("lane_budgets", portfolio_slot["original_content"])
        self.assertIn("solve_awls_single", portfolio_slot["original_content"])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("awls_sdst_portfolio_search_control_notes.md", knowledge_paths)

    def test_context_packet_includes_sdst_zi_feature_slot_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_zi_feature_slot_context",
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
            from harness_agent.slot_manifest import write_selected_slot_manifest

            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=manifest,
                selected_slot_ids=["awls_sdst_zi_features"],
            )

            packet = build_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=root / "context.json",
                    slot_manifest=manifest,
                    project_root=Path.cwd(),
                )
            )

        confirmed = [slot for slot in packet["slot_manifest"]["slots"] if slot["user_confirmed"]]
        self.assertEqual(["awls_sdst_zi_features"], [slot["slot_id"] for slot in confirmed])
        zi_slot = confirmed[0]
        self.assertIn("setup_prev", zi_slot["original_content"])
        self.assertIn("setup_time_between", zi_slot["original_content"])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("awls_sdst_zi_feature_notes.md", knowledge_paths)

    def test_context_packet_includes_sdst_move_evaluation_slot_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_move_eval_slot_context",
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
            from harness_agent.slot_manifest import write_selected_slot_manifest

            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=manifest,
                selected_slot_ids=["awls_sdst_move_evaluation"],
            )

            packet = build_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=root / "context.json",
                    slot_manifest=manifest,
                )
            )

        confirmed = [slot for slot in packet["slot_manifest"]["slots"] if slot["user_confirmed"]]
        self.assertEqual(["awls_sdst_move_evaluation"], [slot["slot_id"] for slot in confirmed])
        move_eval_slot = confirmed[0]
        self.assertIsInstance(move_eval_slot["line_start"], int)
        self.assertEqual("awls_sdst_move_evaluation", move_eval_slot["block_name"])
        self.assertIn("weight_perturbation", move_eval_slot["original_content"])
        self.assertIn("cpp_int_score", move_eval_slot["original_content"])
        self.assertEqual("# SLOT awls_sdst_move_evaluation START", move_eval_slot["marker_start"])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("awls_sdst_move_evaluation_notes.md", knowledge_paths)

    def test_context_packet_includes_sdst_move_selection_slot_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_move_selection_slot_context",
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
            from harness_agent.slot_manifest import write_selected_slot_manifest

            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=manifest,
                selected_slot_ids=["awls_sdst_move_selection"],
            )

            packet = build_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=root / "context.json",
                    slot_manifest=manifest,
                    project_root=Path.cwd(),
                )
            )

        confirmed = [slot for slot in packet["slot_manifest"]["slots"] if slot["user_confirmed"]]
        self.assertEqual(["awls_sdst_move_selection"], [slot["slot_id"] for slot in confirmed])
        move_selection_slot = confirmed[0]
        self.assertIsInstance(move_selection_slot["line_start"], int)
        self.assertEqual("awls_sdst_move_selection", move_selection_slot["block_name"])
        self.assertIn("exact_select_top_k", move_selection_slot["original_content"])
        self.assertIn("ranked_moves", move_selection_slot["original_content"])
        self.assertEqual("# SLOT awls_sdst_move_selection START", move_selection_slot["marker_start"])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("awls_sdst_move_selection_notes.md", knowledge_paths)

    def test_context_packet_includes_sdst_memory_for_sdst_neighborhood_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "sdst_slot_context",
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
            from harness_agent.slot_manifest import write_selected_slot_manifest

            write_selected_slot_manifest(
                problem_family="standard_fjsp",
                output=manifest,
                selected_slot_ids=["awls_sdst_neighborhood_selection"],
            )

            packet = build_context_packet(
                ContextPacketRequest(
                    contract_path=contract,
                    output_path=root / "context.json",
                    slot_manifest=manifest,
                )
            )

        confirmed = [slot for slot in packet["slot_manifest"]["slots"] if slot["user_confirmed"]]
        self.assertEqual(["awls_sdst_neighborhood_selection"], [slot["slot_id"] for slot in confirmed])
        knowledge_paths = {Path(item["path"]).name for item in packet["knowledge_cards"]}
        self.assertIn("awls_sdst_neighborhood_selection_notes.md", knowledge_paths)

    def test_context_packet_reads_slot_source_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "candidate"
            examples_dir = project_root / "examples"
            examples_dir.mkdir(parents=True)
            solver = examples_dir / "standard_fjsp_local_search_solver.py"
            shutil.copy2(Path(__file__).resolve().parents[1] / "examples" / "standard_fjsp_local_search_solver.py", solver)
            text = solver.read_text(encoding="utf-8")
            sentinel = "    project_root_specific_slot_sentinel = 12345\n"
            start = text.index("    # SLOT neighborhood_actions START")
            end = text.index("    # SLOT neighborhood_actions END", start)
            replacement = text[: text.find("\n", start) + 1] + sentinel + text[end:]
            solver.write_text(replacement, encoding="utf-8")

            instance = root / "instance.fjs"
            instance.write_text("1 1 1\n1 1 0 1\n", encoding="utf-8")
            contract = root / "contract.json"
            contract.write_text(
                json.dumps(
                    {
                        "task_id": "slot_project_root_case",
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
                    project_root=project_root,
                    slot_manifest=manifest,
                )
            )

        neighborhood_slot = next(
            item for item in packet["slot_manifest"]["slots"] if item["slot_id"] == "local_search_neighborhood_actions"
        )
        self.assertIn("project_root_specific_slot_sentinel", neighborhood_slot["original_content"])


if __name__ == "__main__":
    unittest.main()
