"""Tests: glyph constants resolve to Nerd Font codepoints (Plan 63 Fix 4a)."""

from __future__ import annotations

import unittest

from duckln import glyphs


class GlyphConstantsTest(unittest.TestCase):
    def test_all_constants_are_non_empty(self) -> None:
        for name in (
            "DOT_SOLID",
            "WEB_ONLINE",
            "WEB_OFFLINE",
            "SLASH",
            "PLUS",
            "UPLOAD",
            "DOCUMENT",
        ):
            value = getattr(glyphs, name)
            self.assertTrue(value, f"{name} resolved to an empty string")
            self.assertIsInstance(value, str)

    def test_dot_solid_is_fa_circle_codepoint(self) -> None:
        # Sanity-check the specific codepoint so we notice if the nerdfonts
        # package's dictionary shifts.
        self.assertEqual(glyphs.DOT_SOLID, "")

    def test_web_online_and_offline_differ(self) -> None:
        self.assertNotEqual(glyphs.WEB_ONLINE, glyphs.WEB_OFFLINE)


if __name__ == "__main__":
    unittest.main()
