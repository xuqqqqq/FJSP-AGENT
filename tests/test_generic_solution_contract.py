from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.agents.quality_contract import (
    build_solver_runtime_feature_contract,
    extract_variant_features,
)
from harness_agent.orchestration.cycle import WORKER_SMOKE_RUNNER_SOURCE


ROOT = Path(__file__).resolve().parents[1]


class GenericSolutionContractTests(unittest.TestCase):
    def _run_smoke(self, payload, *, schema_only=True, repeat=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / ".algoforge_worker_runtime"
            runtime.mkdir()
            (root / "instance.json").write_text("{}", encoding="utf-8")
            contract = {
                "required_top_level_fields": ["result", "diagnostics"],
                "required_object_fields": ["result", "diagnostics"],
            }
            if schema_only:
                contract["worker_smoke_validation"] = "schema_only"
            else:
                for relative in (
                    "harness_agent/__init__.py",
                    "harness_agent/domains/__init__.py",
                    "harness_agent/domains/io.py",
                ):
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / relative, target)
                (root / "instance.json").write_text("1 1 1\n1 1 1 3\n", encoding="utf-8")
                contract = {
                    "format": "standard_fjsp_schedule_v1",
                    "required_top_level_fields": ["format", "makespan", "schedule"],
                }
            (root / "solver.py").write_text(
                "import argparse, json\n"
                "p=argparse.ArgumentParser()\n"
                "for name in ['--input','--output','--seed','--time-limit-sec']: p.add_argument(name)\n"
                "args=p.parse_args()\n"
                f"payload=json.loads({json.dumps(payload)!r})\n"
                "with open(args.output,'w',encoding='utf-8') as out: json.dump(payload,out)\n",
                encoding="utf-8",
            )
            (runtime / "smoke_config.json").write_text(
                json.dumps({
                    "target_file": "solver.py",
                    "instance_path": "instance.json",
                    "output_path": ".algoforge_worker_runtime/smoke_solution.json",
                    "time_limit_seconds": 2,
                    "problem_family": "generic_json" if schema_only else "standard_fjsp",
                    "solution_contract": contract,
                }),
                encoding="utf-8",
            )
            wrapper = runtime / "run_smoke.py"
            wrapper.write_text(WORKER_SMOKE_RUNNER_SOURCE, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(wrapper)], cwd=root, capture_output=True, text=True, check=False,
            )
            if repeat:
                result = subprocess.run(
                    [sys.executable, str(wrapper)], cwd=root, capture_output=True, text=True, check=False,
                )
            return result

    def test_schema_only_accepts_objects_without_standard_parser_or_makespan(self):
        result = self._run_smoke({"result": {}, "diagnostics": {}})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("schema check only", result.stdout)
        self.assertIn("Core has not validated legality or objectives", result.stdout)

    def test_schema_only_rejects_missing_required_field(self):
        result = self._run_smoke({"result": {}})
        self.assertEqual(4, result.returncode)
        self.assertIn("missing required field: diagnostics", result.stderr)

    def test_schema_only_rejects_non_object_field(self):
        for value in ([], None, 2, "data"):
            with self.subTest(value=value):
                result = self._run_smoke({"result": value, "diagnostics": {}})
                self.assertEqual(4, result.returncode)
                self.assertIn("field must be a JSON object: result", result.stderr)

    def test_schema_only_rejects_non_object_output(self):
        result = self._run_smoke([])
        self.assertEqual(4, result.returncode)
        self.assertIn("solution must be a JSON object", result.stderr)

    def test_schema_only_retains_single_run_limit(self):
        result = self._run_smoke({"result": {}, "diagnostics": {}}, repeat=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("already used", result.stderr)

    def test_standard_mode_still_validates_schedule_and_makespan(self):
        payload = {
            "format": "standard_fjsp_schedule_v1", "makespan": 3,
            "schedule": [{"job_id": 0, "op_id": 0, "machine_id": 0, "start": 0, "end": 3}],
        }
        accepted = self._run_smoke(payload, schema_only=False)
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        payload["makespan"] = 2
        mismatch = self._run_smoke(payload, schema_only=False)
        self.assertEqual(4, mismatch.returncode)
        self.assertIn("declared makespan mismatch", mismatch.stderr)
        payload["makespan"] = 3
        payload["schedule"][0]["end"] = 2
        invalid = self._run_smoke(payload, schema_only=False)
        self.assertEqual(4, invalid.returncode)

    def test_explicit_provider_features_do_not_invent_makespan(self):
        features = ["alternative_machines", "window_weight_objective", "multi_objective"]
        context = {
            "instance_diagnostics": {"active_features": features},
            "task": {"description": "makespan no-wait"},
        }
        self.assertEqual(set(features), extract_variant_features(context))
        runtime = build_solver_runtime_feature_contract(context)
        self.assertEqual(sorted(features), runtime["active_features"])
        self.assertNotIn("makespan_objective", runtime["active_features"])
        self.assertIn("declared_objective_priority_guard", runtime["variant_required_code_capabilities"])

    def test_invalid_or_absent_explicit_features_keep_old_fallback(self):
        expected = {"alternative_machines", "operation_precedence", "machine_capacity", "makespan_objective"}
        for features in (None, [], "batching", [""], ["batching", 4]):
            with self.subTest(features=features):
                context = {"instance_diagnostics": {"active_features": features}}
                self.assertEqual(expected, extract_variant_features(context))

    def test_legacy_document_feature_detection_is_unchanged(self):
        context = {"task": {"description": "minimum time lag"}}
        features = extract_variant_features(context)
        self.assertIn("minimum_time_lag", features)
        self.assertIn("makespan_objective", features)


if __name__ == "__main__":
    unittest.main()
