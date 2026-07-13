"""Tests for persistent Duckln loop scheduling helpers."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from duckln.loop_runtime import (
    create_loop_from_answers,
    infer_loop_spec,
    render_loop_history,
    render_loops,
    run_loop_now,
    shutdown_loop_scheduler,
)
from state.store import initialize_state_store


class LoopRuntimeTest(unittest.TestCase):
    def tearDown(self) -> None:
        shutdown_loop_scheduler()

    def test_infer_health_monitor_from_plain_english(self) -> None:
        spec = infer_loop_spec(
            "restart whisper if it crashes",
            "every five minutes",
            "fix first then notify if you cannot",
        )

        self.assertEqual("health_monitor", spec.type)
        self.assertIn("restart_service", spec.tool_scope)
        self.assertTrue(spec.auto_fix)
        self.assertEqual(5, json.loads(spec.schedule)["minutes"])

    def test_create_run_and_history_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            loop = create_loop_from_answers(
                Path(temp_dir),
                task="restart whisper if it crashes",
                cadence="every five minutes",
                failure_policy="fix first then notify",
            )

            self.assertEqual("loop_001", loop.id)
            self.assertIn("Whisper", loop.name)
            result = run_loop_now(Path(temp_dir), loop.id)
            self.assertEqual("ok", result.status)
            history = render_loop_history(Path(temp_dir), loop.id)
            self.assertIn("health check completed", history)

    def test_loop_pause_state_is_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = __import__("pathlib").Path(temp_dir)
            loop = create_loop_from_answers(
                config_dir,
                task="check AWS spend",
                cadence="every 15 minutes",
                failure_policy="notify only",
            )
            store = initialize_state_store(config_dir)
            self.assertTrue(store.set_loop_active(loop.id, False))

            summary = render_loops(config_dir)

            self.assertIn("paused", summary)
            self.assertIn(loop.id, summary)


if __name__ == "__main__":
    unittest.main()
