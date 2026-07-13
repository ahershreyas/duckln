from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.ai_client import Provider
from duckln.config import (
    AppConfig,
    ConfigPaths,
    _parse_duckln_ui,
    load_app_config,
    save_app_config,
)
from duckln.modes import ControlMode
from duckln.ui import (
    build_chat_interface,
    format_thought_for_seconds,
    render_main_task_header,
    render_step_line,
)


class TestUiModeConfig(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(_parse_duckln_ui("inline"), "inline")
        self.assertEqual(_parse_duckln_ui("full"), "full")
        self.assertEqual(_parse_duckln_ui("bogus"), "auto")
        self.assertEqual(_parse_duckln_ui(None), "auto")

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = ConfigPaths(config_dir=Path(tmp), config_file=Path(tmp) / "config.json")
            cfg = AppConfig(provider=Provider.OPENAI, model="m", api_key="k", mode=ControlMode.HOTL, duckln_ui="inline")
            save_app_config(cfg, paths)
            self.assertEqual(load_app_config(paths).duckln_ui, "inline")


class TestInlineBuild(unittest.TestCase):
    def test_inline_mode_returns_non_split_interface(self):
        chat = build_chat_interface(session_header="h", user_name="u", ui_mode="inline")
        # Inline mode must NOT be the fullscreen split-pane app.
        self.assertNotEqual(type(chat).__name__, "SplitPaneChatInterface")


class TestRenderHelpers(unittest.TestCase):
    def test_thought_for_seconds(self):
        self.assertEqual(format_thought_for_seconds(28), "Thought for 28s")
        self.assertEqual(format_thought_for_seconds(64), "Thought for 1m 4s")

    def test_main_task_header(self):
        self.assertIn("Main task", render_main_task_header("Set up X"))

    def test_step_indent(self):
        self.assertIn("├─", render_step_line("sub", indent=1))
        self.assertNotIn("├─", render_step_line("top"))


if __name__ == "__main__":
    unittest.main()
