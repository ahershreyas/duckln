"""Tests: status_text_segment returns (glyph, rich-style) (Plan 63 Fix 1)."""

from __future__ import annotations

import unittest

from duckln.connection_status import status_text_segment
from duckln.glyphs import DOT_SOLID


class StatusTextSegmentTest(unittest.TestCase):
    def test_green_returns_green_style(self) -> None:
        glyph, style = status_text_segment("green")
        self.assertEqual(glyph, DOT_SOLID)
        self.assertEqual(style, "green")

    def test_orange_returns_orange1_or_yellow(self) -> None:
        glyph, style = status_text_segment("orange")
        self.assertEqual(glyph, DOT_SOLID)
        self.assertIn(style, ("orange1", "yellow"))

    def test_red_returns_red_style(self) -> None:
        glyph, style = status_text_segment("red")
        self.assertEqual(glyph, DOT_SOLID)
        self.assertEqual(style, "red")

    def test_unknown_status_falls_back_to_red(self) -> None:
        # Defensive: any unrecognised status (e.g. typo) is treated as red.
        glyph, style = status_text_segment("totally_unknown")  # type: ignore[arg-type]
        self.assertEqual(glyph, DOT_SOLID)
        self.assertEqual(style, "red")


if __name__ == "__main__":
    unittest.main()
