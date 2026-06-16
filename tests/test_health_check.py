from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.health_check import HealthCheckRequest, run_health_check


ROOT = Path(__file__).resolve().parents[1]


class HealthCheckTests(unittest.TestCase):
    def test_health_check_runs_quick_test_and_stability_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "health"

            manifest = run_health_check(
                HealthCheckRequest(
                    contract_path=ROOT / "configs" / "standard_fjsp_tiny.example.json",
                    output_dir=output_dir,
                    project_root=ROOT,
                    repeats=2,
                    max_instances=1,
                    max_seeds=1,
                )
            )

            self.assertEqual("ok", manifest["status"])
            self.assertEqual("ok", manifest["quick_test"]["status"])
            self.assertEqual("ok", manifest["stability_probe"]["status"])
            self.assertTrue(manifest["stability_probe"]["stable"])
            self.assertEqual(2, manifest["stability_probe"]["total"])
            self.assertEqual(2, manifest["stability_probe"]["valid"])
            self.assertEqual(1, len(manifest["stability_probe"]["groups"]))
            self.assertTrue((output_dir / "health_check_manifest.json").exists())
            self.assertTrue((output_dir / "health_check_report.md").exists())
            self.assertTrue((output_dir / "stability_probe" / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
