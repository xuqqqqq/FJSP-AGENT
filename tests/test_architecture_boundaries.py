"""平台层与算法知识层的架构边界回归测试。"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "harness_agent"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_runtime_does_not_embed_named_solver_methods(self) -> None:
        """具体算法名和组合参数只能存在于知识、Skill 或领域资料中。"""

        forbidden = re.compile(
            r"\bawls\b|\bn7\b|\bn8\b|\bnk\b|k[-_ ]insertion|"
            r"portfolio_size|neighborhood_profile",
            re.IGNORECASE,
        )
        violations: list[str] = []
        for path in RUNTIME_ROOT.rglob("*"):
            if path.suffix.lower() not in {".py", ".js", ".html"}:
                continue
            text = path.read_text(encoding="utf-8")
            if forbidden.search(text):
                violations.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual([], violations)

    def test_web_has_no_retired_algorithm_or_slot_controls(self) -> None:
        html = (RUNTIME_ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
        script = (RUNTIME_ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
        combined = f"{html}\n{script}"
        for retired_label in ("策略层", "自由代码", "AWLS-ZI", "代码槽"):
            self.assertNotIn(retired_label, combined)

    def test_method_implementation_assets_live_under_knowledge(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "domain_packs" / "standard_fjsp" / "domain_pack.json").read_text(
                encoding="utf-8"
            )
        )
        for package in manifest.get("method_packages") or []:
            implementation = str(package.get("implementation_asset") or "").replace("\\", "/")
            self.assertTrue(implementation.startswith("knowledge/"), implementation)

    def test_domain_pack_declared_project_assets_exist(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "domain_packs" / "standard_fjsp" / "domain_pack.json").read_text(
                encoding="utf-8"
            )
        )
        declared: list[str] = []

        def collect(value: object) -> None:
            if isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
            elif isinstance(value, str):
                normalized = value.replace("\\", "/")
                if normalized.startswith(("knowledge/", ".codex/skills/")):
                    declared.append(normalized)

        collect(manifest)
        missing = sorted({path for path in declared if not (PROJECT_ROOT / path).exists()})
        self.assertEqual([], missing)

    def test_worker_implementation_skills_are_project_local_and_id_aligned(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "domain_packs" / "standard_fjsp" / "domain_pack.json").read_text(
                encoding="utf-8"
            )
        )
        skill_ids: set[str] = set()
        for item in manifest.get("worker_implementation_skills") or []:
            skill_id = str(item.get("skill_id") or "")
            source = str(item.get("source_path") or "").replace("\\", "/")
            self.assertRegex(skill_id, r"^[a-z0-9-]+$")
            self.assertNotIn(skill_id, skill_ids)
            skill_ids.add(skill_id)
            self.assertEqual(f".codex/skills/{skill_id}", source)
            skill_text = (PROJECT_ROOT / source / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"name: {skill_id}", skill_text)

    def test_skill_markdown_project_references_exist(self) -> None:
        missing: list[str] = []
        for skill_path in sorted((PROJECT_ROOT / ".codex" / "skills").glob("*/SKILL.md")):
            text = skill_path.read_text(encoding="utf-8")
            references = [
                *re.findall(r"`([^`\r\n]+)`", text),
                *re.findall(r"\]\(([^)\r\n]+)\)", text),
            ]
            for value in references:
                normalized = value.replace("\\", "/")
                if normalized.startswith("references/"):
                    target = skill_path.parent / normalized
                elif normalized.startswith(("knowledge/", "domain_packs/", ".codex/skills/")):
                    target = PROJECT_ROOT / normalized
                else:
                    continue
                if not target.exists():
                    missing.append(f"{skill_path.relative_to(PROJECT_ROOT)} -> {normalized}")
        self.assertEqual([], missing)

    def test_reusable_references_do_not_contain_run_specific_scores(self) -> None:
        forbidden = re.compile(
            r"outputs[/\\]|\boddla\d+\b|\bdp\d{2}[a-z]?\b|\bbarnes\b|"
            r"avg_gap_pct|makespan\s*[=:]\s*`?\d+",
            re.IGNORECASE,
        )
        violations: list[str] = []
        for path in sorted((PROJECT_ROOT / "knowledge" / "references").rglob("*.md")):
            if forbidden.search(path.read_text(encoding="utf-8")):
                violations.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual([], violations)

    def test_reference_method_has_no_deleted_runtime_imports(self) -> None:
        source = (
            PROJECT_ROOT
            / "knowledge"
            / "method_packages"
            / "standard_fjsp_awls_hgtsa"
            / "reference_solver.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("harness_agent.standard_fjsp", source)
        self.assertNotIn("examples.awls_evolved_slots", source)
        compile(source, "reference_solver.py", "exec")


if __name__ == "__main__":
    unittest.main()
