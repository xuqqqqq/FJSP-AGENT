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
