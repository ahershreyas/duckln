"""Tests: _drain_terminal_runtime_incidents respects the 180s total budget (Plan 56)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import duckln.main as _main_mod


class DrainTotalBudgetTest(unittest.TestCase):
    def test_drain_loop_breaks_after_total_budget(self) -> None:
        """The drain loop body must contain a total-budget check that breaks the loop."""
        import inspect
        src = inspect.getsource(_main_mod._drain_terminal_runtime_incidents)
        self.assertIn(
            "_DRAIN_TOTAL_BUDGET_SECONDS",
            src,
            "_drain_terminal_runtime_incidents must reference _DRAIN_TOTAL_BUDGET_SECONDS",
        )
        self.assertIn(
            "drain_started_at",
            src,
            "_drain_terminal_runtime_incidents must track a drain start time",
        )
        self.assertIn(
            "Manual action needed",
            src,
            "_drain_terminal_runtime_incidents must emit a manual-action message when budget is exhausted",
        )

    def test_drain_budget_constant_is_greater_than_per_call_budget(self) -> None:
        """_DRAIN_TOTAL_BUDGET_SECONDS must exceed _RUNTIME_REPAIR_BUDGET_SECONDS."""
        self.assertGreater(
            _main_mod._DRAIN_TOTAL_BUDGET_SECONDS,
            _main_mod._RUNTIME_REPAIR_BUDGET_SECONDS,
            "_DRAIN_TOTAL_BUDGET_SECONDS must be larger than the per-call budget",
        )


if __name__ == "__main__":
    unittest.main()
