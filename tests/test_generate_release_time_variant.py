from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness_agent.domains.io import parse_standard_fjsp
from tools.generate_release_time_variant import generate_release_time_variant


ROOT = Path(__file__).resolve().parents[1]


class GenerateReleaseTimeVariantTests(unittest.TestCase):
    def test_generation_is_deterministic_and_preserves_standard_body(self) -> None:
        source = ROOT / "examples" / "standard_fjsp_tiny.fjs"
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.rtfjsp.txt"
            second = Path(tmp) / "second.rtfjsp.txt"
            metadata = generate_release_time_variant(source, first, seed=17)
            generate_release_time_variant(source, second, seed=17)

            generated = parse_standard_fjsp(first)
            original = parse_standard_fjsp(source)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual("fjsp_release_time", generated.variant)
            self.assertEqual(original.jobs, generated.jobs)
            self.assertEqual(original.job_count, generated.job_count)
            self.assertEqual(original.machine_count, generated.machine_count)
            self.assertGreater(max(generated.job_release_times), 0)
            self.assertGreater(max(generated.machine_available_times), 0)
            self.assertEqual(
                sum(len(job.operations) for job in original.jobs),
                metadata["operation_count"],
            )


if __name__ == "__main__":
    unittest.main()
