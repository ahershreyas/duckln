"""Plan 67: state persistence tests for pending plan + history."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from state.access import (
    append_plan_history,
    clear_pending_plan,
    clear_plan_history,
    list_plan_history,
    read_pending_plan,
    write_pending_plan,
)


class PlanStateRoundTripTest(unittest.TestCase):
    def _config_dir(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name)

    def test_write_read_clear_pending(self) -> None:
        config_dir = self._config_dir()
        self.assertIsNone(read_pending_plan(config_dir))
        payload = {
            "plan_id": "abc123",
            "objective": "Set up x",
            "context_summary": "family=node",
            "steps": [{"index": 1, "title": "install", "command": "npm install"}],
            "risks": [],
            "rollback": "",
            "estimated_seconds": 60,
            "created_at": "2026-05-19T00:00:00Z",
            "status": "pending",
            "repo_slug": "owner/x",
            "mode_at_creation": "hotl",
            "clarifications": [],
            "dropped_candidates": [],
            "critic_reasoning": "",
            "amendment_count": 0,
            "history": [],
        }
        write_pending_plan(config_dir, payload)
        loaded = read_pending_plan(config_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["plan_id"], "abc123")
        self.assertEqual(loaded["objective"], "Set up x")
        clear_pending_plan(config_dir)
        self.assertIsNone(read_pending_plan(config_dir))

    def test_history_appends_and_lists(self) -> None:
        config_dir = self._config_dir()
        for i in range(3):
            append_plan_history(
                config_dir,
                {
                    "plan_id": f"p{i}",
                    "objective": f"obj{i}",
                    "repo_slug": None,
                    "steps": [],
                    "amendment_count": 0,
                    "created_at": f"2026-05-19T0{i}:00:00Z",
                },
                decision="completed",
            )
        history = list_plan_history(config_dir, limit=10)
        self.assertEqual(len(history), 3)
        # newest first
        self.assertEqual(history[0]["plan_id"], "p2")
        self.assertEqual(history[2]["plan_id"], "p0")

    def test_history_capped(self) -> None:
        from state.access import PLAN_MODE_HISTORY_LIMIT

        config_dir = self._config_dir()
        for i in range(PLAN_MODE_HISTORY_LIMIT + 10):
            append_plan_history(
                config_dir,
                {"plan_id": f"p{i}", "objective": "x", "steps": [], "amendment_count": 0},
                decision="completed",
            )
        history = list_plan_history(config_dir, limit=200)
        self.assertEqual(len(history), PLAN_MODE_HISTORY_LIMIT)

    def test_clear_history(self) -> None:
        config_dir = self._config_dir()
        append_plan_history(
            config_dir,
            {"plan_id": "p1", "objective": "x", "steps": [], "amendment_count": 0},
            decision="completed",
        )
        self.assertEqual(len(list_plan_history(config_dir)), 1)
        clear_plan_history(config_dir)
        self.assertEqual(list_plan_history(config_dir), ())


if __name__ == "__main__":
    unittest.main()
