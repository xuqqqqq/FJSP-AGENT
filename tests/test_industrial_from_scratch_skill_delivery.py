from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.context.knowledge import resolve_worker_implementation_skills
from harness_agent.domains.pack import get_domain_pack
from harness_agent.orchestration.cycle import _stage_worker_implementation_skills
from harness_agent.worker import ExperimentSpec
from harness_agent.workers.opencode_worker import OPENCODE_WORKER_AGENT, OpenCodeWorker
from tests.test_aluminum_skill_delivery import METHOD_SKILLS, ROOT, _assignment


SKILL_ID = "industrial-fjsp-from-scratch"
STAGES = ("baseline", "baseline_retry", "improvement", "focused_improvement", "repair")
REFERENCE_NAMES = {
    "sources.md",
    "parsing-state.md",
    "feasible-construction.md",
    "conflict-repair.md",
    "micro-validation.md",
    "optimization.md",
}


class IndustrialFromScratchSkillDeliveryTests(unittest.TestCase):
    def test_registration_remains_an_industrial_adapter_with_existing_methods(self):
        pack = get_domain_pack("fjsp_industrial_json", fallback_to_standard=False)
        self.assertIsNotNone(pack)
        self.assertEqual(set(METHOD_SKILLS), {item.family_id for item in pack.method_families})
        skill = pack.worker_implementation_skill(SKILL_ID)
        self.assertIsNotNone(skill)
        self.assertEqual(["industrial_json"], skill.required_features)
        self.assertTrue(skill.always_include)
        self.assertEqual([], skill.method_families)
        self.assertEqual((ROOT / ".codex/skills" / SKILL_ID).resolve(), skill.source_path.resolve())
        self.assertIsNotNone(pack.worker_implementation_skill("huawei-aluminum-fjsp"))

    def test_full_industrial_authorizes_new_skill_across_methods_and_stages(self):
        for family, method_skill in METHOD_SKILLS.items():
            for stage in STAGES:
                with self.subTest(family=family, stage=stage):
                    assignment = _assignment(family=family, stage=stage, mode="full")
                    skills = {item["skill_id"]: item for item in assignment.implementation_skills}
                    self.assertIn(SKILL_ID, skills)
                    self.assertTrue(skills[SKILL_ID]["required"])
                    self.assertEqual(f".opencode/skills/{SKILL_ID}", skills[SKILL_ID]["sandbox_path"])
                    self.assertIn("huawei-aluminum-fjsp", skills)
                    if assignment.mode != "baseline":
                        self.assertIn(method_skill, skills)

    def test_none_and_standard_assignments_do_not_authorize_new_skill(self):
        for family in METHOD_SKILLS:
            for stage in STAGES:
                for mode, industrial in (("none", True), ("full", False)):
                    with self.subTest(family=family, stage=stage, mode=mode, industrial=industrial):
                        assignment = _assignment(family=family, stage=stage, mode=mode, industrial=industrial)
                        self.assertNotIn(SKILL_ID, [item["skill_id"] for item in assignment.implementation_skills])
                        if mode == "none":
                            self.assertEqual([], assignment.implementation_skills)

    def test_both_industrial_pack_and_feature_are_required(self):
        for problem_family, features in (
            ("fjsp_industrial_json", []),
            ("fjsp_industrial_json", ["batching", "window_weight_objective"]),
            ("FJSP", ["industrial_json", "batching"]),
        ):
            with self.subTest(problem_family=problem_family, features=features):
                selection = resolve_worker_implementation_skills(
                    problem_family=problem_family,
                    method_families=["constructive_search"],
                    active_features=features,
                )
                self.assertNotIn(SKILL_ID, [item["skill_id"] for item in selection["skills"]])

    def test_materialization_copies_reference_text_and_runtime_gates_nested_reads(self):
        source = ROOT / ".codex/skills" / SKILL_ID
        expected_files = {"SKILL.md", *(f"references/{name}" for name in REFERENCE_NAMES)}
        self.assertEqual(expected_files, {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()})
        # This package supplies guidance, never a baseline solver or executable helper.
        self.assertTrue(all(Path(name).suffix == ".md" for name in expected_files))
        for mode, industrial in (("full", True), ("none", True), ("full", False)):
            with self.subTest(mode=mode, industrial=industrial), tempfile.TemporaryDirectory() as tmp:
                worktree = Path(tmp)
                assignment = _assignment(family="constructive_search", stage="baseline", mode=mode, industrial=industrial)
                _stage_worker_implementation_skills(assignment=assignment, source_root=ROOT, worktree_path=worktree)
                spec = ExperimentSpec(
                    task_id="skill-delivery-test",
                    experiment_id="industrial-from-scratch",
                    context_packet_path=str(worktree / "unused-context.json"),
                    worktree_path=str(worktree),
                    max_steps=4,
                    max_runtime_seconds=900,
                )
                # Build permissions directly: no subprocess, model, or evaluator is launched.
                runtime = OpenCodeWorker()._runtime_config(spec, assignment=assignment, attachment_paths=[])
                permission = runtime["agent"][OPENCODE_WORKER_AGENT]["permission"]
                target = worktree / ".opencode/skills" / SKILL_ID
                read_pattern = f".opencode/skills/{SKILL_ID}/**"
                self.assertEqual("deny", permission["read"]["*"])
                if mode == "full" and industrial:
                    self.assertEqual("allow", permission["skill"][SKILL_ID])
                    self.assertEqual("deny", permission["skill"]["*"])
                    self.assertEqual("allow", permission["read"][read_pattern])
                    self.assertEqual(expected_files, {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()})
                    for name in expected_files:
                        self.assertEqual((source / name).read_bytes(), (target / name).read_bytes(), name)
                else:
                    self.assertFalse(target.exists())
                    self.assertNotIn(read_pattern, permission["read"])
                    if isinstance(permission["skill"], dict):
                        self.assertNotIn(SKILL_ID, permission["skill"])
                    else:
                        self.assertEqual("deny", permission["skill"])


if __name__ == "__main__":
    unittest.main()
