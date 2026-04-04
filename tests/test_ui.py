"""Tests for Duckln banner rendering."""

from __future__ import annotations

import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from duckln.ui import (
    COMPACT_BANNER,
    DUCKLN_COMMAND,
    DUCKLN_GOLD,
    DUCKLN_SYSTEM,
    build_terminal_display,
    build_terminal_input,
    load_banner_asset,
    render_banner,
    render_session_header,
)


class BannerRenderingTest(unittest.TestCase):
    def test_full_banner_is_loaded_from_asset(self) -> None:
        banner = load_banner_asset()

        self.assertIn("Duckln", banner)
        self.assertIn("AI Terminal Mentor", banner)

    def test_full_banner_is_used_when_width_is_sufficient(self) -> None:
        rendered = render_banner(width=120)

        self.assertIn("██████╗", rendered)
        self.assertTrue(DUCKLN_GOLD in rendered or "██████╗" in rendered)
        self.assertNotIn(COMPACT_BANNER, rendered)

    def test_compact_banner_is_used_for_narrow_terminals(self) -> None:
        rendered = render_banner(width=20)

        self.assertIn(COMPACT_BANNER, rendered)
        self.assertNotIn("██████╗", rendered)

    def test_session_header_uses_command_and_brand_styles(self) -> None:
        rendered = render_session_header(
            provider="OpenAI",
            model="gpt-4o-mini",
            mode="HOTL",
            user_name="Shreyas",
            memory_state="ready",
            width=80,
        )

        plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
        self.assertIn("provider : OpenAI", plain)
        self.assertIn("/provider to change", plain)
        self.assertTrue(DUCKLN_GOLD in rendered or "◆ Duckln" in rendered)
        self.assertTrue(DUCKLN_COMMAND in rendered or "/provider to change" in rendered)

    def test_terminal_input_keeps_prompt_and_entered_text_in_user_input_color(self) -> None:
        prompt_input = build_terminal_input()
        captured_stdout = io.StringIO()

        with patch("builtins.input", return_value="/help") as mocked_input, redirect_stdout(captured_stdout):
            entered = prompt_input("duckln> ")

        self.assertEqual("/help", entered)
        self.assertEqual("\x1b[38;2;240;234;214mduckln> ", mocked_input.call_args.args[0])
        self.assertEqual("\x1b[0m", captured_stdout.getvalue())

    def test_terminal_display_renders_system_text_dim_grey_and_commands_cyan(self) -> None:
        captured_stdout = io.StringIO()

        with patch("duckln.ui.Console", None), redirect_stdout(captured_stdout):
            display = build_terminal_display()
            display("/help — Show the available slash commands.")

        rendered = captured_stdout.getvalue()
        self.assertIn("\x1b[38;2;0;255;255m/help\x1b[0m", rendered)
        self.assertIn("\x1b[38;2;169;169;169m — Show the available slash commands.\x1b[0m", rendered)
        self.assertTrue(DUCKLN_COMMAND in rendered or "/help" in rendered)
        self.assertTrue(DUCKLN_SYSTEM in rendered or "Show the available slash commands." in rendered)


if __name__ == "__main__":
    unittest.main()
