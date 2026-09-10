from __future__ import annotations

import unittest
from pathlib import Path

from harness_agent.context.knowledge import resolve_worker_implementation_skills
from harness_agent.context.worker import build_worker_assignment
from harness_agent.domains.industrial_json import IndustrialJsonContextProvider
from harness_agent.domains.pack import get_domain_pack


ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "huawei-aluminum-fjsp"
METHOD_SKILLS = {
    "constructive_search": "fjsp-constructive-search-worker",
    "coupled_local_search": "fjsp-coupled-local-search-worker",
    "exact_hybrid": "fjsp-exact-hybrid-worker",
    "population_memetic": "fjsp-population-memetic-worker",
}


def _assignment(*, family: str, stage: str, mode: str, industrial: bool = True):
    target = "examples/agent_generated_industrial_solver.py" if industrial else "examples/agent_generated_fjsp_solver.py"
    context = {
        "task": {"problem_family": "fjsp_industrial_json" if industrial else "FJSP"},
        "guidance_ablation": {"mode": mode},
        "instance_diagnostics": {
            "active_features": ["industrial_json", "window_weight_objective"] if industrial else [],
        },
        "evaluator_protocol": {"solver_command_template": f"python {target} --input {{instance}}"},
        "edit_policy": {"allowed_paths": ["examples"], "forbidden_paths": ["outputs"]},
    }
    if industrial:
        context["evaluator_protocol"]["solution_contract"] = IndustrialJsonContextProvider().solution_contract()
    direction = {
        "direction_id": f"skill-delivery-{stage}-{family}",
        "hypothesis": "Improve the legal incumbent using the selected method under the frozen objective.",
        "method_family": family,
        "method_families": [{"id": family, "role": "primary"}],
    }
    if stage == "baseline":
        direction["strategy_type"] = "baseline_constructor"
    if stage == "focused_improvement":
        direction["worker_lane_policy"] = {"mechanism_selection": "delegated_to_worker"}
    feedback = {}
    if stage in {"repair", "baseline_retry"}:
        feedback = {"current_round_repair": {
            "status": "repair_required",
            "repair_targets": {"python_compile_errors": {target: "SyntaxError: invalid syntax"}},
        }}
        if stage == "baseline_retry":
            feedback["current_round_repair"].update({"baseline_trial": 1, "resume_incomplete_baseline": True})
    return build_worker_assignment(
        context=context,
        direction_plan=direction,
        loop_feedback=feedback,
        round_index=-1 if stage in {"baseline", "baseline_retry"} else 0,
        attempt_index=1 if stage in {"repair", "baseline_retry"} else 0,
        max_steps=4,
        max_runtime_seconds=900,
    )


class AluminumSkillDeliveryTests(unittest.TestCase):
    def test_registration_is_an_industrial_adapter_not_a_fifth_method_family(self):
        pack = get_domain_pack("fjsp_industrial_json", fallback_to_standard=False)
        self.assertIsNotNone(pack)
        self.assertEqual(set(METHOD_SKILLS), {item.family_id for item in pack.method_families})
        matches = [item for item in pack.worker_implementation_skills if item.skill_id == SKILL_ID]
        self.assertEqual(1, len(matches))
        skill = matches[0]
        self.assertEqual(["industrial_json"], skill.required_features)
        self.assertTrue(skill.always_include)
        self.assertEqual([], skill.method_families)
        self.assertEqual((ROOT / ".codex/skills" / SKILL_ID).resolve(), skill.source_path.resolve())
        self.assertTrue((skill.source_path / "SKILL.md").is_file())

    def test_full_industrial_assignments_authorize_skill_across_methods_and_stages(self):
        for family, method_skill in METHOD_SKILLS.items():
            for stage in ("baseline", "baseline_retry", "improvement", "focused_improvement", "repair"):
                with self.subTest(family=family, stage=stage):
                    assignment = _assignment(family=family, stage=stage, mode="full")
                    expected_mode = "baseline" if stage == "baseline_retry" else stage.removeprefix("focused_")
                    self.assertEqual(expected_mode, assignment.mode)
                    skills = {item["skill_id"]: item for item in assignment.implementation_skills}
                    self.assertIn(SKILL_ID, skills)
                    self.assertTrue(skills[SKILL_ID]["required"])
                    self.assertEqual(f".opencode/skills/{SKILL_ID}", skills[SKILL_ID]["sandbox_path"])
                    self.assertEqual([], skills[SKILL_ID]["method_families"])
                    self.assertLessEqual(len(assignment.implementation_skills), 8)
                    if assignment.mode != "baseline":
                        self.assertIn(method_skill, skills)
                    self.assertNotIn("complex-fjsp-stepwise-optimization", skills)

    def test_none_never_authorizes_skills_even_with_industrial_features(self):
        for family in METHOD_SKILLS:
            for stage in ("baseline", "baseline_retry", "improvement", "focused_improvement", "repair"):
                with self.subTest(family=family, stage=stage):
                    assignment = _assignment(family=family, stage=stage, mode="none")
                    self.assertIn("industrial_json", assignment.runtime_contract["active_features"])
                    self.assertEqual([], assignment.implementation_skills)

    def test_standard_fjsp_assignments_never_authorize_industrial_skill(self):
        for family in METHOD_SKILLS:
            for stage in ("baseline", "baseline_retry", "improvement", "focused_improvement", "repair"):
                with self.subTest(family=family, stage=stage):
                    assignment = _assignment(family=family, stage=stage, mode="full", industrial=False)
                    self.assertNotIn(SKILL_ID, [item["skill_id"] for item in assignment.implementation_skills])

    def test_industrial_feature_is_required_even_for_always_include_skill(self):
        for features in ([], ["batching", "window_weight_objective"]):
            with self.subTest(features=features):
                selection = resolve_worker_implementation_skills(
                    problem_family="fjsp_industrial_json",
                    method_families=["constructive_search"],
                    active_features=features,
                )
                self.assertNotIn(SKILL_ID, [item["skill_id"] for item in selection["skills"]])
                self.assertIn(
                    {"skill_id": SKILL_ID, "reason": "feature_incompatible"},
                    selection["audit"]["excluded_skills"],
                )

    def test_standard_pack_cannot_select_skill_from_industrial_feature_alone(self):
        selection = resolve_worker_implementation_skills(
            problem_family="FJSP",
            method_families=["constructive_search"],
            active_features=["industrial_json", "batching"],
        )
        self.assertNotIn(SKILL_ID, [item["skill_id"] for item in selection["skills"]])


if __name__ == "__main__":
    unittest.main()
