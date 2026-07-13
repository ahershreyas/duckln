"""Tests: header is a Rich Text (not bracket markup string) (Plan 63 Fix 1)."""

from __future__ import annotations

import unittest

from rich.text import Text


class HeaderRendersTextNotMarkupTest(unittest.TestCase):
    """We can't construct a real ``SplitPaneChatInterface`` cheaply in unit
    tests (it depends on Textual/Pyte/pywebview), so we verify the contract
    at the helper level: ``status_text_segment`` returns parts a Text builds
    cleanly, and the result never contains literal '[red]' markup that would
    otherwise leak into the visible header."""

    def test_text_from_segments_renders_without_bracket_markup(self) -> None:
        from duckln.connection_status import status_text_segment
        text = Text("Duckln", style="bold")
        glyph, style = status_text_segment("red")
        text.append(" • ", style="dim")
        text.append(glyph, style=style)
        text.append(" provider Ollama")
        rendered = str(text)
        # The Rich Text str() representation is plain text — no bracket markup.
        self.assertNotIn("[red]", rendered)
        self.assertNotIn("[green]", rendered)
        self.assertNotIn("[/red]", rendered)
        self.assertIn(glyph, rendered)

    def test_text_keeps_styling_when_passed_through_render_status_bar(self) -> None:
        """When _render_status_bar receives a Text, it should pass it through
        unchanged (preserving styles)."""
        try:
            from duckln.textual_ui import _render_status_bar
        except Exception:
            self.skipTest("textual_ui not importable in this environment")
            return
        text = Text("Duckln", style="bold")
        text.append(" • foo")
        result = _render_status_bar(text)
        self.assertIs(result, text)

    def test_render_status_bar_still_accepts_plain_str(self) -> None:
        """Backward compat: passing a string still produces a styled Text."""
        try:
            from duckln.textual_ui import _render_status_bar
        except Exception:
            self.skipTest("textual_ui not importable in this environment")
            return
        result = _render_status_bar("Duckln • provider Ollama • Local")
        self.assertIsInstance(result, Text)
        self.assertIn("Duckln", str(result))


if __name__ == "__main__":
    unittest.main()
