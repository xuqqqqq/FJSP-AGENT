from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from harness_agent.agents.main import DirectionPlanRequest
from harness_agent.agents.main import RoundReflectionRequest
from harness_agent.agents.opencode_main import (
    OPENCODE_MAIN_AGENT,
    OpenCodeMainAgent,
    build_planning_packet,
    compact_round_competition_result,
    extract_planned_direction,
    incumbent_planning_contract_errors,
    normalize_direction_selection,
    summarize_opencode_events,
)
from harness_agent.context.packet import ContextPacketRequest, write_context_packet


ROOT = Path(__file__).resolve().parents[1]


class OpenCodeMainAgentTests(unittest.TestCase):
    def test_competition_evidence_falls_back_to_compact_direction_plan(self) -> None:
        result = compact_round_competition_result(
            None,
            direction={
                "competition_result": {
                    "status": "selected",
                    "selected_candidate_id": "c1",
                    "candidates": [
                        {
                            "candidate_id": "c1",
                            "status": "completed",
                            "worker_model": "openai/gpt-5.4",
                            "objective_key": [-2200],
                            "mechanism_activation": {"status": "passed", "passed": True},
                        }
                    ],
                }
            },
        )

        self.assertEqual("selected", result["status"])
        self.assertEqual("openai/gpt-5.4", result["candidates"][0]["model"])

    def test_run_once_waits_indefinitely_when_no_main_timeout_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            executable = tmp_path / "opencode.exe"
            attachment = tmp_path / "planning_packet.json"
            output_dir = tmp_path / "main"
            executable.write_text("placeholder", encoding="utf-8")
            attachment.write_text("{}", encoding="utf-8")
            output_dir.mkdir()
            process = MagicMock()
            process.stdout = MagicMock()
            process.stderr = MagicMock()
            process.returncode = 0

            with (
                patch("harness_agent.agents.opencode_main.subprocess.Popen", return_value=process),
                patch("harness_agent.agents.opencode_main.threading.Thread") as thread_factory,
                patch("harness_agent.agents.opencode_main.opencode_subprocess_environment", return_value={}),
                patch("harness_agent.agents.opencode_main.cleanup_process_descendants"),
            ):
                thread_factory.return_value = SimpleNamespace(start=lambda: None, join=lambda timeout=0: None)
                agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT)

                result = agent._run_once(
                    output_dir=output_dir,
                    attachments=[attachment],
                    prompt="test prompt",
                    suffix="",
                )

        self.assertIsNone(agent.timeout_seconds)
        self.assertFalse(result["timed_out"])
        process.wait.assert_called_once_with()

    def test_run_once_honors_explicit_main_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            executable = tmp_path / "opencode.exe"
            attachment = tmp_path / "planning_packet.json"
            output_dir = tmp_path / "main"
            executable.write_text("placeholder", encoding="utf-8")
            attachment.write_text("{}", encoding="utf-8")
            output_dir.mkdir()
            process = MagicMock()
            process.stdout = MagicMock()
            process.stderr = MagicMock()
            process.returncode = 0

            with (
                patch("harness_agent.agents.opencode_main.subprocess.Popen", return_value=process),
                patch("harness_agent.agents.opencode_main.threading.Thread") as thread_factory,
                patch("harness_agent.agents.opencode_main.opencode_subprocess_environment", return_value={}),
                patch("harness_agent.agents.opencode_main.cleanup_process_descendants"),
            ):
                thread_factory.return_value = SimpleNamespace(start=lambda: None, join=lambda timeout=0: None)
                agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT, timeout_seconds=21)

                result = agent._run_once(
                    output_dir=output_dir,
                    attachments=[attachment],
                    prompt="test prompt",
                    suffix="",
                )

        self.assertEqual(21, agent.timeout_seconds)
        self.assertFalse(result["timed_out"])
        process.wait.assert_called_once_with(timeout=21)

    def test_specialist_attachment_permissions_include_project_relative_path(self) -> None:
        executable = ROOT / "opencode.exe"
        agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT)
        attachment = ROOT / "outputs" / "test-main" / "planning_packet.json"

        runtime = agent._runtime_config(
            attachment_paths=[attachment],
            allowed_specialist="evidence-analyst",
        )

        analyst = runtime["agent"]["evidence-analyst"]
        self.assertFalse(analyst["disable"])
        self.assertEqual("allow", analyst["permission"]["read"]["*"])
        self.assertEqual("deny", analyst["permission"]["read"][".env"])
        self.assertTrue(runtime["agent"]["requirements-method-analyst"]["disable"])

    def test_runtime_enables_up_to_four_distinct_read_only_specialists(self) -> None:
        executable = ROOT / "opencode.exe"
        agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT, max_subagents=4)
        attachment = ROOT / "outputs" / "test-main" / "planning_packet.json"
        specialists = [
            "requirements-method-analyst",
            "evidence-analyst",
            "plan-critic",
            "candidate-strategy-analyst",
        ]

        runtime = agent._runtime_config(
            attachment_paths=[attachment],
            allowed_specialists=specialists,
        )

        self.assertEqual(
            {name: "allow" for name in specialists},
            {
                name: runtime["agent"][OPENCODE_MAIN_AGENT]["permission"]["task"][name]
                for name in specialists
            },
        )
        self.assertTrue(all(not runtime["agent"][name]["disable"] for name in specialists))
        self.assertTrue(all(runtime["agent"][name]["permission"]["edit"] == "deny" for name in specialists))

    def test_summarizes_step_finish_usage_with_canonical_fields(self) -> None:
        events = "\n".join(
            [
                json.dumps(
                    {
                        "type": "step_finish",
                        "part": {
                            "tokens": {
                                "total": 23_362,
                                "input": 1_652,
                                "output": 2_244,
                                "reasoning": 1_034,
                                "cache": {"read": 18_432, "write": 0},
                            },
                            "cost": 0.1,
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "step_finish",
                        "part": {
                            "tokens": {
                                "total": 10,
                                "input": 3,
                                "output": 4,
                                "reasoning": 2,
                                "cache": {"read": 1, "write": 5},
                            },
                            "cost": 0.025,
                        },
                    }
                ),
            ]
        )

        summary = summarize_opencode_events(events)

        self.assertEqual(
            {
                "input_tokens": 1_655,
                "output_tokens": 2_248,
                "reasoning_tokens": 1_036,
                "total_tokens": 23_372,
                "cache_read_tokens": 18_433,
                "cache_write_tokens": 5,
                "cost": 0.125,
            },
            summary["usage"],
        )

    def test_extracts_planned_direction_from_json_event_text(self) -> None:
        planned = {
            "direction_plan": {"direction_id": "d000", "hypothesis": "Improve one operator."},
            "worker_assignment": {"objective": "Implement it."},
        }
        events = json.dumps(
            {
                "type": "text",
                "part": {"type": "text", "text": json.dumps(planned)},
            }
        )

        self.assertEqual(planned, extract_planned_direction(events))

    def test_extracts_final_plan_after_native_commentary(self) -> None:
        planned = {
            "direction_plan": {"direction_id": "d000", "hypothesis": "扩大现有搜索覆盖。"},
            "worker_assignment": {"objective": "修改已审计的搜索控制参数。"},
        }
        events = "\n".join(
            [
                json.dumps(
                    {
                        "type": "text",
                        "part": {
                            "type": "text",
                            "text": "我先核对 incumbent 的搜索控制参数。",
                            "metadata": {"openai": {"phase": "commentary"}},
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "text",
                        "part": {
                            "type": "text",
                            "text": "Beam 已存在但宽度较小，因此先验证覆盖不足假设。",
                            "metadata": {"openai": {"phase": "commentary"}},
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "text",
                        "part": {
                            "type": "text",
                            "text": json.dumps(planned, ensure_ascii=False),
                            "metadata": {"openai": {"phase": "final_answer"}},
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        self.assertEqual(planned, extract_planned_direction(events))

    def test_main_prompt_requires_live_thinking_before_final_json(self) -> None:
        prompt = (ROOT / ".opencode" / "agents" / "algoforge-main.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("live thinking process", prompt)
        self.assertIn("commentary messages", prompt)
        self.assertIn("final-answer message must contain exactly one JSON object", prompt)
        self.assertNotIn("Return exactly one JSON object", prompt)

    def test_planning_packet_excludes_incumbent_source_and_unbounded_history(self) -> None:
        packet = build_planning_packet(
            context={
                "task": {"task_id": "test", "problem_family": "FJSP"},
                "method_package_catalog": {
                    "recommended_package_id": "standard_fjsp_awls_hgtsa",
                    "packages": [
                        {
                            "package_id": "standard_fjsp_awls_hgtsa",
                            "description": "bounded package",
                            "implementation_contract": {
                                "required_components": [{"component_id": "decoder"}]
                            },
                            "asset_records": [{"snippet": "reference solver source"}],
                        }
                    ],
                },
                "strategy_selection_cards": [
                    {
                        "path": "knowledge/references/general_fjsp/fjsp_method_selection_zh.md",
                        "snippet": "低柔性实例优先比较顺序搜索、构造多样性和混合精确方法。",
                    }
                ],
                "incumbent_code_context": {
                    "source": "incumbent",
                    "files": [
                        {
                            "relative_path": "examples/solver.py",
                            "sha256": "abc",
                            "snippet": "secret incumbent source",
                        }
                    ],
                },
                "incumbent_capability_audit": {
                    "schema_version": 1,
                    "source": "promoted_incumbent_static_python_ast",
                    "files": [
                        {
                            "relative_path": "examples/solver.py",
                            "parse_status": "ok",
                            "configurations": [
                                {
                                    "scope": "solve",
                                    "name": "beam_width",
                                    "line": 712,
                                    "expression": "min(3, max(2, machine_count // 4 + 1))",
                                    "literal_values": [3, 2, 4, 1],
                                }
                            ],
                            "functions": [
                                {
                                    "name": f"helper_{index}",
                                    "qualified_name": f"helper_{index}",
                                    "line": index + 1,
                                    "end_line": index + 1,
                                    "args": ["instance", "deadline"],
                                    "loop_count": 1,
                                    "branch_count": 2,
                                    "calls": [f"helper_{index + 1}"],
                                }
                                for index in range(100)
                            ],
                            "classes": [],
                            "loops": [],
                            "call_edges": [],
                        }
                    ],
                },
            },
            loop_feedback={
                "previous_rounds": [
                    {
                        "round_index": index,
                        "decision": "rolled_back",
                        "direction_plan": {
                            "strategy_type": "local_search_operator",
                            "hypothesis": f"Improve component {index}",
                            "method_package_id": "standard_fjsp_awls_hgtsa",
                            "implementation_order": [f"component_{index}"],
                        },
                        "competition_result": {
                            "status": "selected",
                            "selected_candidate_id": f"c{index:02d}",
                            "selected_objective_key": [2000 - index],
                            "selected_for_promotion": False,
                            "candidates": [
                                {
                                    "candidate_id": f"c{index:02d}",
                                    "status": "completed",
                                    "model": f"variant-{index}",
                                    "objective_key": [2000 - index],
                                    "mechanism_activation": {
                                        "status": "passed",
                                        "passed": True,
                                    },
                                    "summary": {
                                        "validation_summary": {"valid": 1},
                                        "candidate_summaries": [{"candidate_id": f"c{index:02d}"}],
                                    },
                                    "semantic_review": {"status": "pass"},
                                    "patch_path": f"patches/round_{index}.diff",
                                }
                            ],
                        },
                        "round_reflection": {
                            "hypothesis_outcome": "supported" if index % 2 == 0 else "refuted",
                            "summary": f"round {index} reflection",
                            "next_action": {"action": "probe", "rationale": "collect activation evidence"},
                        },
                    }
                    for index in range(20)
                ],
                "experience_memory": {
                    "memory_tiers": {
                        "validated_lessons": [
                            {
                                "lesson_type": "successful_strategy",
                                "problem_family": "FJSP",
                                "strategy": "Joint assignment and sequencing search",
                                "strategy_type": "local_search_operator",
                                "method_package_id": "standard_fjsp_awls_hgtsa",
                                "outcome": "promoted_by_core_evaluator",
                                "applicability": ["same measured pressure"],
                                "contraindications": ["do not copy scores"],
                                "evidence": {
                                    "direction_id": "old",
                                    "artifact_refs": {"package": "standard_fjsp_awls_hgtsa"},
                                },
                                "confidence": "core_and_semantic_validated",
                            }
                        ]
                    }
                },
            },
            round_index=2,
        )

        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("reference solver source", rendered)
        self.assertNotIn("secret incumbent source", rendered)
        self.assertIn("beam_width", rendered)
        self.assertIn("min(3, max(2, machine_count // 4 + 1))", rendered)
        self.assertNotIn("required_components", rendered)
        self.assertNotIn("standard_fjsp_awls_hgtsa", rendered)
        self.assertEqual([], packet["method_package_catalog"]["packages"])
        self.assertIn("低柔性实例优先比较", rendered)
        self.assertLessEqual(len(packet["recent_round_evidence"]), 6)
        self.assertEqual(
            ["component_19"],
            packet["recent_round_evidence"][-1]["implementation_order"],
        )
        self.assertEqual(
            "variant-19",
            packet["recent_round_evidence"][-1]["competition_result"]["candidates"][0]["model"],
        )
        self.assertEqual(
            "passed",
            packet["recent_round_evidence"][-1]["competition_result"]["candidates"][0]["mechanism_activation"]["status"],
        )
        self.assertEqual(
            "pass",
            packet["recent_round_evidence"][-1]["competition_result"]["candidates"][0]["diagnostics"]["semantic_review"]["status"],
        )
        self.assertEqual(
            "refuted",
            packet["recent_round_evidence"][-1]["round_reflection"]["hypothesis_outcome"],
        )

    def test_direction_selection_filters_unknown_query_tags(self) -> None:
        normalized = normalize_direction_selection(
            {
                "direction_selection": {
                    "method_family": "多起点构造",
                    "knowledge_query": ["construction", "invented_method", "decoder", "construction"],
                }
            },
            planning_packet={
                "knowledge_query_catalog": {
                    "default_limit": 2,
                    "tags": [
                        {"tag": "construction"},
                        {"tag": "decoder"},
                    ],
                }
            },
            round_index=-1,
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(["construction", "decoder"], normalized["knowledge_query"])
        self.assertEqual("", normalized["method_package_id"])

    def test_direction_selection_accepts_direct_structured_selector_values(self) -> None:
        normalized = normalize_direction_selection(
            {
                "planning_stage": "direction_selection",
                "selected_pressure": {"id": "ordering_pressure", "label": "排序压力主导"},
                "selected_method_family": {"id": "local_search", "label": "局部搜索"},
                "knowledge_query": ["critical_path", "unknown"],
                "reasoning_trace": [{"kind": "decision", "content": "测试关键块邻域。"}],
            },
            planning_packet={
                "knowledge_query_catalog": {
                    "default_limit": 4,
                    "tags": [{"tag": "critical_path"}],
                }
            },
            round_index=0,
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual("ordering_pressure", normalized["primary_search_pressure"])
        self.assertEqual("local_search", normalized["method_family"])
        self.assertEqual(["critical_path"], normalized["knowledge_query"])

    def test_direction_selection_accepts_multiple_catalog_families_and_filters_cross_family_tags(self) -> None:
        normalized = normalize_direction_selection(
            {
                "direction_selection": {
                    "method_families": [
                        {"id": "constructive_search", "role": "primary"},
                        {"id": "coupled_local_search", "role": "complementary"},
                        {"id": "invented_family", "role": "complementary"},
                    ],
                    "knowledge_query": ["beam_search", "local_search", "cp_sat"],
                }
            },
            planning_packet={
                "method_family_catalog": {
                    "max_selected": 3,
                    "families": [
                        {"family_id": "constructive_search", "query_tags": ["beam_search"]},
                        {"family_id": "coupled_local_search", "query_tags": ["local_search"]},
                        {"family_id": "exact_hybrid", "query_tags": ["cp_sat"]},
                    ],
                },
                "knowledge_query_catalog": {
                    "default_limit": 6,
                    "tags": [
                        {"tag": "beam_search"},
                        {"tag": "local_search"},
                        {"tag": "cp_sat"},
                    ],
                },
            },
            round_index=0,
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual("constructive_search", normalized["method_family"])
        self.assertEqual(
            ["constructive_search", "coupled_local_search"],
            [item["id"] for item in normalized["method_families"]],
        )
        self.assertEqual(["beam_search", "local_search"], normalized["knowledge_query"])

    def test_improvement_planning_requires_incumbent_assessment_and_next_mutation(self) -> None:
        packet = {"incumbent_capability_audit": {"schema_version": 1, "files": [{}]}}
        errors = incumbent_planning_contract_errors(
            {"direction_plan": {"diagnosis": "Beam exists."}},
            planning_packet=packet,
            round_index=0,
        )

        self.assertIn("direction_plan.incumbent_assessment is missing", errors)
        self.assertIn("direction_plan.next_mutation is missing", errors)

        complete = {
            "direction_plan": {
                "incumbent_assessment": {
                    "verified_capabilities": ["Beam is reachable."],
                    "implementation_limits": ["beam_width=3"],
                    "bottleneck_hypotheses": ["State diversity may collapse early."],
                    "evidence_refs": ["examples/solver.py:712 beam_width"],
                },
                "next_mutation": {
                    "target_symbols": ["solve.beam_width"],
                    "change": "Scale the existing Beam under the deadline.",
                    "expected_effect": "Retain more distinct states.",
                    "falsification_metrics": ["expanded states", "makespan", "runtime"],
                },
                "reasoning_trace": [
                    {
                        "stage": f"step-{index}",
                        "summary": f"Public analysis step {index}.",
                        "evidence": ["beam_width=3"],
                        "inference": "The current coverage may be narrow.",
                        "decision": "Preserve the existing Beam.",
                        "next_check": "Measure expanded states.",
                    }
                    for index in range(3)
                ],
            }
        }
        self.assertEqual(
            [],
            incumbent_planning_contract_errors(
                complete,
                planning_packet=packet,
                round_index=0,
            ),
        )

    def test_opencode_main_normalizes_plan_and_enforces_read_only_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            executable = tmp_path / "opencode.exe"
            executable.write_text("placeholder", encoding="utf-8")
            selected = {
                "direction_selection": {
                    "direction_id": "d000",
                    "method_family": "coupled_local_search",
                    "method_families": [{"id": "coupled_local_search", "role": "primary"}],
                    "primary_search_pressure": "coupled",
                    "diagnosis": "实例同时存在机器选择和排序压力。",
                    "measured_evidence": ["flexible_operation_ratio=0.5"],
                    "uncertainties": ["尚无 incumbent 关键块证据"],
                    "alternatives_considered": ["纯构造只能建立基线，不能完成深搜索。"],
                    "selection_rationale": "需要读取联合局部搜索实现知识后再签发任务。",
                    "knowledge_query": ["local_search", "critical_path", "assignment_aware_local_search"],
                }
            }
            planned = {
                "direction_plan": {
                    "direction_id": "d000",
                    "title": "Complete selected package",
                    "strategy_type": "baseline_constructor",
                    "hypothesis": "A complete package adaptation creates a legal baseline.",
                    "diagnosis": "No generated solver exists.",
                    "alternatives_considered": ["A dispatch-only baseline omits required search behavior."],
                    "selection_rationale": "The recommended package matches active features.",
                    "method_package_id": "standard_fjsp_awls_hgtsa",
                },
                "worker_assignment": {
                    "objective": "Adapt every required package component.",
                    "completion_rule": "Every component must be reachable.",
                },
            }
            selection_events = json.dumps(
                {
                    "type": "text",
                    "part": {"type": "text", "text": json.dumps(selected, ensure_ascii=False)},
                }
            )
            implementation_events = json.dumps(
                {
                    "type": "text",
                    "part": {"type": "text", "text": json.dumps(planned, ensure_ascii=False)},
                }
            )
            selection_process = MagicMock()
            selection_process.stdout = io.StringIO(selection_events + "\n")
            selection_process.stderr = io.StringIO("")
            selection_process.wait.return_value = 0
            selection_process.returncode = 0
            implementation_process = MagicMock()
            implementation_process.stdout = io.StringIO(implementation_events + "\n")
            implementation_process.stderr = io.StringIO("")
            implementation_process.wait.return_value = 0
            implementation_process.returncode = 0

            with (
                patch(
                    "harness_agent.agents.opencode_main.subprocess.Popen",
                    side_effect=[selection_process, implementation_process],
                ) as popen,
                patch("harness_agent.agents.opencode_main.cleanup_process_descendants") as cleanup,
            ):
                plan = OpenCodeMainAgent(
                    executable=str(executable),
                    model="openai/gpt-5.4",
                    variant="high",
                    project_root=ROOT,
                    timeout_seconds=30,
                ).plan_direction(
                    DirectionPlanRequest(
                        round_index=-1,
                        context_packet_path=context_path,
                        loop_feedback={"round_type": "agent_generated_baseline"},
                        output_dir=tmp_path / "main",
                    )
                )

            self.assertEqual("opencode_main_agent", plan["planner"])
            self.assertEqual("standard_fjsp_awls_hgtsa", plan["method_package_id"])
            self.assertFalse(plan["method_package_selection"]["fallback_used"])
            self.assertEqual(selected["direction_selection"]["method_family"], plan["method_family"])
            self.assertEqual(
                selected["direction_selection"]["knowledge_query"],
                plan["knowledge_query"],
            )
            self.assertEqual(
                "Adapt every required package component.",
                plan["worker_objective"],
            )
            command = json.loads((tmp_path / "main" / "opencode_main_command.json").read_text())
            self.assertIn(OPENCODE_MAIN_AGENT, command)
            self.assertIn("json", command)
            self.assertEqual("openai/gpt-5.4", command[command.index("--model") + 1])
            self.assertEqual("high", command[command.index("--variant") + 1])
            session_title = command[command.index("--title") + 1]
            self.assertTrue(session_title.startswith("AlgoForge Main "))
            self.assertTrue(session_title.endswith(" direction-selection"))
            self.assertIn("最多调用一次", command[-2])
            self.assertIn("requirements-method-analyst", command[-2])
            self.assertEqual(2, popen.call_count)
            runtime = json.loads(popen.call_args_list[0].kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
            permissions = runtime["agent"][OPENCODE_MAIN_AGENT]["permission"]
            self.assertEqual("allow", permissions["read"]["*"])
            self.assertEqual(
                "allow",
                permissions["read"]["*"],
            )
            self.assertEqual("deny", permissions["read"][".env"])
            self.assertEqual("deny", permissions["read"]["**/.env.*"])
            self.assertEqual("deny", permissions["edit"])
            self.assertEqual("deny", permissions["bash"])
            self.assertEqual("deny", permissions["task"]["*"])
            self.assertEqual("allow", permissions["task"]["requirements-method-analyst"])
            self.assertEqual("deny", permissions["skill"]["*"])
            self.assertEqual("allow", permissions["skill"]["algoforge-assignment"])
            self.assertEqual("allow", permissions["skill"]["experiment-design"])
            self.assertNotIn("evidence-analyst", permissions["task"])
            self.assertNotIn("plan-critic", permissions["task"])
            specialist_permissions = runtime["agent"]["requirements-method-analyst"]["permission"]
            self.assertEqual("allow", specialist_permissions["read"]["*"])
            self.assertEqual("deny", specialist_permissions["read"][".env"])
            self.assertEqual(
                "allow",
                specialist_permissions["external_directory"][
                    str((tmp_path / "main" / "planning_packet.json").resolve())
                ],
            )
            self.assertFalse(runtime["agent"]["requirements-method-analyst"]["disable"])
            self.assertTrue(runtime["agent"]["evidence-analyst"]["disable"])
            self.assertEqual(2, cleanup.call_count)
            implementation_packet = json.loads(
                (tmp_path / "main" / "implementation_planning_packet.json").read_text(encoding="utf-8")
            )
            self.assertEqual("implementation_planning", implementation_packet["planning_stage"])
            self.assertIn(
                "standard_fjsp_awls_hgtsa",
                implementation_packet["method_package_catalog"]["eligible_package_ids"],
            )
            self.assertTrue(implementation_packet["active_direction_knowledge"]["cards"])

    def test_timeout_without_model_text_skips_format_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            executable = tmp_path / "opencode.exe"
            executable.write_text("placeholder", encoding="utf-8")
            agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT, timeout_seconds=30)
            timed_out = {
                "stdout": json.dumps({"type": "tool_use", "part": {"tool": "task"}}),
                "stderr": "planner exceeded its bound",
                "returncode": "1",
                "timed_out": True,
            }

            with patch.object(agent, "_run_once", return_value=timed_out) as run_once:
                plan = agent.plan_direction(
                    DirectionPlanRequest(
                        round_index=-1,
                        context_packet_path=context_path,
                        loop_feedback={"round_type": "agent_generated_baseline"},
                        output_dir=tmp_path / "main",
                    )
                )

            self.assertEqual(1, run_once.call_count)
            self.assertEqual("evidence_fallback", plan["planner"])
            usage = json.loads((tmp_path / "main" / "main_agent_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(1, usage["attempts"])

    def test_contract_retry_reformats_attached_invalid_plan_without_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["incumbent_capability_audit"] = {
                "schema_version": 1,
                "source": "test_static_audit",
                "files": [{"relative_path": "examples/solver.py", "parse_status": "ok"}],
            }
            context_path.write_text(json.dumps(context), encoding="utf-8")
            executable = tmp_path / "opencode.exe"
            executable.write_text("placeholder", encoding="utf-8")
            agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT, timeout_seconds=30)
            selected = {
                "direction_selection": {
                    "method_family": "coupled_local_search",
                    "method_families": [{"id": "coupled_local_search", "role": "primary"}],
                    "primary_search_pressure": "ordering_pressure",
                    "diagnosis": "当前排序搜索覆盖不足。",
                    "knowledge_query": ["local_search", "critical_path"],
                }
            }
            invalid_plan = {
                "direction_plan": {
                    "direction_id": "d000",
                    "title": "关键路径邻域",
                    "method_family": "coupled_local_search",
                    "knowledge_query": ["local_search", "critical_path"],
                },
                "candidate_variants": [{"candidate_id": "wrong-level"}],
                "worker_assignment": {"objective": "实现关键路径邻域。"},
            }

            def events(payload: dict[str, object]) -> dict[str, object]:
                return {
                    "stdout": json.dumps(
                        {
                            "type": "text",
                            "part": {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                        },
                        ensure_ascii=False,
                    ),
                    "stderr": "",
                    "returncode": 0,
                    "timed_out": False,
                }

            with patch.object(
                agent,
                "_run_once",
                side_effect=[events(selected), events(invalid_plan), events(invalid_plan)],
            ) as run_once:
                plan = agent.plan_direction(
                    DirectionPlanRequest(
                        round_index=0,
                        context_packet_path=context_path,
                        loop_feedback={"round_type": "improvement"},
                        output_dir=tmp_path / "main",
                    )
                )

            self.assertEqual("evidence_fallback", plan["planner"])
            self.assertEqual(3, run_once.call_count)
            implementation_call = run_once.call_args_list[1].kwargs
            self.assertIn("experiment-design Skill", run_once.call_args_list[0].kwargs["prompt"])
            self.assertIn("experiment-design Skill", implementation_call["prompt"])
            self.assertIn("英文 key", implementation_call["prompt"])
            self.assertIn("candidate_variants 必须放在 direction_plan 内", implementation_call["prompt"])
            self.assertIn("activation_checks", implementation_call["prompt"])
            self.assertIn("research_tournament", implementation_call["prompt"])
            self.assertIn("不是把质量结果本身当作执行证明", implementation_call["prompt"])
            self.assertIn("都是 advisory", implementation_call["prompt"])
            self.assertIn("不得把它们解释成禁止选择完整方法", implementation_call["prompt"])
            self.assertIn("不鼓励机械照抄参考源码", implementation_call["prompt"])

            retry_call = run_once.call_args_list[2].kwargs
            retry_attachments = {path.name for path in retry_call["attachments"]}
            self.assertEqual(
                {"incumbent_planning_invalid.json", "incumbent_planning_contract_errors.json"},
                retry_attachments,
            )
            self.assertIn("不要重新研究", retry_call["prompt"])
            self.assertIn("不要重新研究、读取其他文件或调用工具", retry_call["prompt"])
            self.assertIsNone(retry_call["allowed_specialist"])
            self.assertTrue((tmp_path / "main" / "incumbent_planning_invalid.json").exists())
            self.assertTrue(
                (tmp_path / "main" / "incumbent_planning_contract_errors.json").exists()
            )

    def test_unknown_method_family_stops_before_second_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            context_path = write_context_packet(
                ContextPacketRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_path=tmp_path / "context.json",
                )
            )
            executable = tmp_path / "opencode.exe"
            executable.write_text("placeholder", encoding="utf-8")
            agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT, timeout_seconds=30)
            selected = {
                "direction_selection": {
                    "method_family": "仅适用于 SDST 的方法",
                    "primary_search_pressure": "sequence",
                    "diagnosis": "测试不兼容变体查询。",
                    "knowledge_query": ["setup_time"],
                }
            }
            events = json.dumps(
                {
                    "type": "text",
                    "part": {"type": "text", "text": json.dumps(selected, ensure_ascii=False)},
                }
            )
            run_result = {
                "stdout": events,
                "stderr": "",
                "returncode": 0,
                "timed_out": False,
            }

            with patch.object(agent, "_run_once", return_value=run_result) as run_once:
                plan = agent.plan_direction(
                    DirectionPlanRequest(
                        round_index=-1,
                        context_packet_path=context_path,
                        loop_feedback={"round_type": "agent_generated_baseline"},
                        output_dir=tmp_path / "main",
                    )
                )

            self.assertEqual(1, run_once.call_count)
            self.assertEqual("evidence_fallback", plan["planner"])
            self.assertFalse((tmp_path / "main" / "implementation_planning_packet.json").exists())

    def test_reflect_on_round_writes_evidence_and_normalized_reflection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            executable = tmp_path / "opencode.exe"
            executable.write_text("placeholder", encoding="utf-8")
            agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT)
            reflection_payload = {
                "round_reflection": {
                    "hypothesis_outcome": "supported",
                    "summary": "候选机制已执行且目标严格提升。",
                    "candidate_findings": [
                        {
                            "candidate_id": "c00",
                            "outcome": "supported",
                            "evidence": [
                                "mechanism_activation=passed",
                                "objective_key=[95.0]",
                            ],
                            "causal_interpretation": "候选机制已激活，结果支持原假设。",
                        }
                    ],
                    "next_action": {
                        "action": "research_tournament",
                        "rationale": "已有正向证据，但仍需跨方法族做有界比较。",
                        "required_activation_checks": [
                            {
                                "id": "cp_hits",
                                "path": "solution.json#/diagnostics/search_counters/critical_path_hits",
                                "operator": "gt",
                                "expected": 0,
                                "description": "证明关键路径机制被真正执行。",
                            }
                        ],
                    },
                }
            }
            run_result = {
                "stdout": json.dumps(
                    {
                        "type": "text",
                        "part": {"type": "text", "text": json.dumps(reflection_payload, ensure_ascii=False)},
                    },
                    ensure_ascii=False,
                ),
                "stderr": "",
                "returncode": 0,
                "timed_out": False,
            }
            request = RoundReflectionRequest(
                round_index=3,
                direction_plan={
                    "direction_id": "d003",
                    "title": "关键路径扩展",
                    "hypothesis": "扩大关键路径重排可继续降低 makespan。",
                    "strategy_type": "local_search_operator",
                    "activation_checks": [
                        {
                            "id": "cp_hits",
                            "path": "solution.json#/diagnostics/search_counters/critical_path_hits",
                            "operator": "gt",
                            "expected": 0,
                        }
                    ],
                    "candidate_variants": [
                        {
                            "candidate_id": "c00",
                            "title": "关键路径扩展变体",
                            "experiment_stage": "research_tournament",
                        }
                    ],
                },
                competition_result={
                    "status": "selected",
                    "selected_candidate_id": "c00",
                    "selected_for_promotion": True,
                    "candidates": [
                        {
                            "candidate_id": "c00",
                            "status": "completed",
                            "objective_key": [95.0],
                            "mechanism_activation": {"status": "passed", "passed": True},
                            "summary": {"validation_summary": {"valid": 1}},
                            "semantic_review": {"status": "pass"},
                            "patch_path": "patches/c00.diff",
                        }
                    ],
                },
                promotion_check={"promoted": True},
                incumbent_key_before=(100.0,),
                incumbent_key_after=(95.0,),
                output_dir=tmp_path / "round",
            )

            with patch.object(agent, "_run_once", return_value=run_result) as run_once:
                reflection = agent.reflect_on_round(request)

            self.assertEqual("supported", reflection["hypothesis_outcome"])
            self.assertEqual("research_tournament", reflection["next_action"]["action"])
            self.assertEqual(
                "cp_hits",
                reflection["next_action"]["required_activation_checks"][0]["id"],
            )
            self.assertTrue((tmp_path / "round" / "round_evidence.json").exists())
            stored_evidence = json.loads((tmp_path / "round" / "round_evidence.json").read_text(encoding="utf-8"))
            self.assertEqual("selected", stored_evidence["competition_result"]["status"])
            self.assertEqual(
                "关键路径扩展变体",
                stored_evidence["competition_result"]["candidates"][0]["model"],
            )
            self.assertEqual(
                "passed",
                stored_evidence["competition_result"]["candidates"][0]["mechanism_activation"]["status"],
            )
            run_call = run_once.call_args.kwargs
            self.assertEqual(["round_evidence.json"], [path.name for path in run_call["attachments"]])
            self.assertIn("experiment-design Skill", run_call["prompt"])
            self.assertIn("证明机制确实执行", run_call["prompt"])
            self.assertIn("research_tournament", run_call["prompt"])
            stored_reflection = json.loads((tmp_path / "round" / "round_reflection.json").read_text(encoding="utf-8"))
            self.assertEqual("supported", stored_reflection["hypothesis_outcome"])

    def test_reflect_on_round_uses_deterministic_fallback_when_model_output_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            executable = tmp_path / "opencode.exe"
            executable.write_text("placeholder", encoding="utf-8")
            agent = OpenCodeMainAgent(executable=str(executable), project_root=ROOT)
            request = RoundReflectionRequest(
                round_index=1,
                direction_plan={"direction_id": "d001", "title": "Test reflection"},
                competition_result={
                    "status": "selected",
                    "selected_candidate_id": "c00",
                    "selected_for_promotion": False,
                    "candidates": [
                        {
                            "candidate_id": "c00",
                            "status": "completed",
                            "objective_key": [120.0],
                            "mechanism_activation": {"status": "missing", "passed": False},
                        }
                    ],
                },
                promotion_check={"promoted": False},
                incumbent_key_before=(100.0,),
                incumbent_key_after=(100.0,),
                output_dir=tmp_path / "round",
            )

            with patch.object(
                agent,
                "_run_once",
                return_value={
                    "stdout": json.dumps(
                        {
                            "type": "text",
                            "part": {
                                "type": "text",
                                "text": json.dumps({"hypothesis_outcome": "unsupported"}, ensure_ascii=False),
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "stderr": "",
                    "returncode": 0,
                    "timed_out": False,
                },
            ):
                reflection = agent.reflect_on_round(request)

            self.assertEqual("inconclusive_not_exercised", reflection["hypothesis_outcome"])
            self.assertEqual("probe", reflection["next_action"]["action"])
            self.assertEqual(
                "机制激活检查未通过，当前结果不能用于否定算法假设。",
                reflection["candidate_findings"][0]["causal_interpretation"],
            )


if __name__ == "__main__":
    unittest.main()
