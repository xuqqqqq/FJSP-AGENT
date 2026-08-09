from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.pack import get_domain_pack
from harness_agent.domains.distributed_context import DistributedFjspContextProvider
from harness_agent.domains.io import parse_standard_fjsp
from harness_agent.orchestration.standard import fixed_problem_contract
from harness_agent.orchestration.cycle import WORKER_SMOKE_RUNNER_SOURCE, contract_evaluator_python_paths
from harness_agent.web.server import inspect_instance_profile, is_supported_starter_instance


ROOT = Path(__file__).resolve().parents[1]


class VariantIntegrationTests(unittest.TestCase):
    def test_fixed_variant_evaluator_is_discovered_as_read_only_core_dependency(self) -> None:
        paths = contract_evaluator_python_paths(
            "python examples/fjsp_release_time_evaluator.py --instance {instance} --solution {solution}"
        )

        self.assertEqual(["examples/fjsp_release_time_evaluator.py"], paths)

    def test_starter_project_discovers_confirmed_text_variant_names(self) -> None:
        for name in [
            "case.rtfjsp.seed1.txt",
            "FFCR01.txt",
            "NFA01.txt",
            "DFM01_10x2x6.txt",
            "case.priority.seed1.txt",
        ]:
            self.assertTrue(is_supported_starter_instance(Path(name)), name)
        self.assertFalse(is_supported_starter_instance(Path("read me.txt")))

    def test_nfa_named_zero_window_instance_is_not_guessed_as_min_lag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "NFA_zero.txt"
            path.write_text("1 1 1\n1 1 0 2\n0\n", encoding="utf-8")
            instance = parse_standard_fjsp(path)
        self.assertEqual("fjsp_machine_availability", instance.variant)
        self.assertEqual((), instance.unavailability_intervals)

    def test_distributed_context_contract_includes_factory_and_objective_fields(self) -> None:
        contract = DistributedFjspContextProvider().solution_contract()
        self.assertEqual(
            ["job_id", "op_id", "factory_id", "machine_id", "start", "end"],
            contract["schedule_record_fields"],
        )
        self.assertIn("max_factory_workload", contract["required_top_level_fields"])
        self.assertIn("total_energy_consumption", contract["required_top_level_fields"])

    def test_fixed_contract_routes_each_parsed_variant(self) -> None:
        cases = {
            "fjsp_release_time_tiny.rtfjsp.txt": "examples/fjsp_release_time_evaluator.py",
            "FFCR_tiny.txt": "examples/fjsp_machine_availability_evaluator.py",
            "fjsp_distributed_transfer_tiny.txt": "examples/fjsp_distributed_transfer_evaluator.py",
            "fjsp_priority_tiny.priority.txt": "examples/fjsp_priority_evaluator.py",
        }
        for name, expected_evaluator in cases.items():
            family, evaluator, objectives = fixed_problem_contract([ROOT / "examples" / name])
            self.assertEqual(expected_evaluator, evaluator)
            expected_count = 3 if family == "fjsp_distributed_transfer" else 2 if "priority" in name else 1
            self.assertEqual(expected_count, len(objectives))

    def test_web_profile_reports_distributed_shape_and_fixed_objectives(self) -> None:
        profile = inspect_instance_profile(ROOT / "examples" / "fjsp_distributed_transfer_tiny.txt")
        self.assertTrue(profile["valid"])
        self.assertEqual("fjsp_distributed_transfer", profile["problem_family"])
        self.assertEqual(2, profile["factory_count"])
        self.assertEqual(
            ["makespan", "max_factory_workload", "total_energy_consumption"],
            profile["objective_names"],
        )

    def test_web_profile_reports_priority_shape_and_lexicographic_objectives(self) -> None:
        profile = inspect_instance_profile(ROOT / "examples" / "fjsp_priority_tiny.priority.txt")
        self.assertTrue(profile["valid"])
        self.assertEqual("fjsp_priority", profile["variant"])
        self.assertTrue(profile["has_job_priorities"])
        self.assertEqual(1, profile["priority_job_count"])
        self.assertEqual(["makespan", "priority_completion_time"], profile["objective_names"])
        self.assertEqual("examples/fjsp_priority_evaluator.py", profile["fixed_evaluator"])

    def test_domain_packs_load_variant_skills_and_packages(self) -> None:
        standard = get_domain_pack("FJSP")
        distributed = get_domain_pack("fjsp_distributed_transfer")
        self.assertIsNotNone(standard)
        self.assertIsNotNone(distributed)
        assert standard is not None and distributed is not None
        self.assertIsNotNone(standard.worker_implementation_skill("fjsp-release-time-adapter-worker"))
        self.assertIsNotNone(
            standard.worker_implementation_skill("fjsp-machine-availability-adapter-worker")
        )
        self.assertIsNotNone(standard.method_package("fjsp_machine_availability_adaptation"))
        self.assertIsNotNone(standard.worker_implementation_skill("fjsp-priority-adapter-worker"))
        self.assertIsNotNone(standard.method_package("fjsp_priority_adaptation"))
        self.assertIsNotNone(distributed.method_package("fjsp_distributed_transfer_adaptation"))

    def test_distributed_adapter_forbids_numeric_factory_marker_guessing(self) -> None:
        text = (
            ROOT / ".codex" / "skills" / "fjsp-distributed-transfer-adapter-worker" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Enumerate compositions", text)
        self.assertIn("Never guess whether a token is a factory marker", text)
        self.assertIn("Machine IDs remain global across factory groups", text)
        self.assertIn("paper uses Pareto ranking", text)

    def test_foundation_skill_pins_decimal_time_limit_cli(self) -> None:
        text = (
            ROOT / ".codex" / "skills" / "fjsp-solver-foundation-worker" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`--time-limit-sec` must use `type=float`".replace(" must use ", " 必须使用 "), text)
        self.assertIn("`args.time_limit_sec`", text)

    def test_distributed_evaluator_executes_fixed_schema(self) -> None:
        solution = {
            "format": "standard_fjsp_schedule_v1",
            "makespan": 68,
            "max_factory_workload": 5,
            "total_energy_consumption": 386,
            "schedule": [
                {"job_id": 0, "op_id": 0, "factory_id": 0, "machine_id": 0, "start": 0, "end": 3},
                {"job_id": 0, "op_id": 1, "factory_id": 1, "machine_id": 1, "start": 63, "end": 68},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            solution_path = Path(temp) / "solution.json"
            metrics_path = Path(temp) / "metrics.json"
            solution_path.write_text(json.dumps(solution), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_distributed_transfer_evaluator.py"),
                    "--instance",
                    str(ROOT / "examples" / "fjsp_distributed_transfer_tiny.txt"),
                    "--solution",
                    str(solution_path),
                    "--metrics",
                    str(metrics_path),
                ],
                cwd=ROOT,
                check=True,
            )
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
            solution["total_energy_consumption"] = 385
            solution_path.write_text(json.dumps(solution), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples" / "fjsp_distributed_transfer_evaluator.py"),
                    "--instance",
                    str(ROOT / "examples" / "fjsp_distributed_transfer_tiny.txt"),
                    "--solution",
                    str(solution_path),
                    "--metrics",
                    str(metrics_path),
                ],
                cwd=ROOT,
                check=True,
            )
            rejected_payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["valid"])
        self.assertEqual(68.0, payload["metrics"]["makespan"])
        self.assertFalse(rejected_payload["valid"])
        self.assertTrue(
            any("declared total_energy_consumption mismatch" in error for error in rejected_payload["errors"])
        )

    def test_standard_schema_variant_evaluators_execute(self) -> None:
        cases = [
            (
                "fjsp_release_time_evaluator.py",
                "fjsp_release_time_tiny.rtfjsp.txt",
                [
                    {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 5, "end": 8},
                    {"job_id": 1, "op_id": 0, "machine_id": 1, "start": 7, "end": 11},
                ],
            ),
            (
                "fjsp_machine_availability_evaluator.py",
                "FFCR_tiny.txt",
                [
                    {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 5, "end": 8},
                    {"job_id": 1, "op_id": 0, "machine_id": 1, "start": 0, "end": 4},
                ],
            ),
        ]
        for evaluator, instance, schedule in cases:
            with tempfile.TemporaryDirectory() as temp:
                solution_path = Path(temp) / "solution.json"
                metrics_path = Path(temp) / "metrics.json"
                solution_path.write_text(json.dumps({"schedule": schedule}), encoding="utf-8")
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "examples" / evaluator),
                        "--instance",
                        str(ROOT / "examples" / instance),
                        "--solution",
                        str(solution_path),
                        "--metrics",
                        str(metrics_path),
                    ],
                    cwd=ROOT,
                    check=True,
                )
                payload = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["valid"], payload)

    def test_priority_evaluator_recomputes_declared_secondary_objective(self) -> None:
        solution = {
            "format": "standard_fjsp_schedule_v1",
            "makespan": 4,
            "priority_completion_time": 3,
            "schedule": [
                {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 0, "end": 3},
                {"job_id": 1, "op_id": 0, "machine_id": 1, "start": 0, "end": 4},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            solution_path = Path(temp) / "solution.json"
            metrics_path = Path(temp) / "metrics.json"
            solution_path.write_text(json.dumps(solution), encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "examples" / "fjsp_priority_evaluator.py"),
                "--instance",
                str(ROOT / "examples" / "fjsp_priority_tiny.priority.txt"),
                "--solution",
                str(solution_path),
                "--metrics",
                str(metrics_path),
            ]
            subprocess.run(command, cwd=ROOT, check=True)
            accepted = json.loads(metrics_path.read_text(encoding="utf-8"))
            solution["priority_completion_time"] = 2
            solution_path.write_text(json.dumps(solution), encoding="utf-8")
            subprocess.run(command, cwd=ROOT, check=True)
            rejected = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertTrue(accepted["valid"])
        self.assertEqual(3.0, accepted["metrics"]["priority_completion_time"])
        self.assertFalse(rejected["valid"])
        self.assertTrue(any("declared priority_completion_time mismatch" in error for error in rejected["errors"]))

    def test_worker_smoke_uses_distributed_parser_for_distributed_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / ".algoforge_worker_runtime"
            runtime.mkdir()
            (root / "harness_agent" / "domains").mkdir(parents=True)
            for relative in [
                "harness_agent/__init__.py",
                "harness_agent/domains/__init__.py",
                "harness_agent/domains/distributed_fjsp.py",
            ]:
                target = root / relative
                shutil.copy2(ROOT / relative, target)
            instance = root / "instance.txt"
            shutil.copy2(ROOT / "examples" / "fjsp_distributed_transfer_tiny.txt", instance)
            solver = root / "solver.py"
            solver.write_text(
                "import argparse, json\n"
                "p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); "
                "p.add_argument('--seed'); p.add_argument('--time-limit-sec'); a=p.parse_args()\n"
                "json.dump({'format':'standard_fjsp_schedule_v1','makespan':68,"
                "'max_factory_workload':5,'total_energy_consumption':386,'schedule':["
                "{'job_id':0,'op_id':0,'factory_id':0,'machine_id':0,'start':0,'end':3},"
                "{'job_id':0,'op_id':1,'factory_id':1,'machine_id':1,'start':63,'end':68}]},open(a.output,'w'))\n",
                encoding="utf-8",
            )
            (runtime / "smoke_config.json").write_text(
                json.dumps(
                    {
                        "target_file": "solver.py",
                        "instance_path": "instance.txt",
                        "output_path": ".algoforge_worker_runtime/smoke_solution.json",
                        "time_limit_seconds": 2,
                        "problem_family": "fjsp_distributed_transfer",
                        "solution_contract": {
                            "format": "standard_fjsp_schedule_v1",
                            "required_top_level_fields": ["format", "makespan", "schedule"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "run_smoke.py").write_text(WORKER_SMOKE_RUNNER_SOURCE, encoding="utf-8")
            completed = subprocess.run([sys.executable, str(runtime / "run_smoke.py")], cwd=root, check=False)
            solver.write_text(
                solver.read_text(encoding="utf-8").replace(
                    "'total_energy_consumption':386",
                    "'total_energy_consumption':385",
                ),
                encoding="utf-8",
            )
            (runtime / "smoke.used").unlink()
            rejected = subprocess.run(
                [sys.executable, str(runtime / "run_smoke.py")], cwd=root, check=False
            )
        self.assertEqual(0, completed.returncode)
        self.assertEqual(4, rejected.returncode)


if __name__ == "__main__":
    unittest.main()
