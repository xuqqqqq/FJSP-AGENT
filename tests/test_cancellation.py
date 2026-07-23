from __future__ import annotations

import unittest

from harness_agent.core.cancellation import CancellationToken, TaskCancelled


class CancellationTokenTests(unittest.TestCase):
    def test_cancel_runs_registered_and_late_terminators(self) -> None:
        token = CancellationToken()
        calls: list[str] = []
        registration = token.register_terminator(lambda: calls.append("active"))

        token.cancel()
        token.register_terminator(lambda: calls.append("late"))
        token.unregister_terminator(registration)

        self.assertEqual(["active", "late"], calls)
        with self.assertRaises(TaskCancelled):
            token.raise_if_cancelled()


if __name__ == "__main__":
    unittest.main()
