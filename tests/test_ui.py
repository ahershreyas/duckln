"""Tests for Duckln banner rendering."""

from __future__ import annotations

import unittest

from duckln.ui import COMPACT_BANNER, load_banner_asset, render_banner


class BannerRenderingTest(unittest.TestCase):
    def test_full_banner_is_loaded_from_asset(self) -> None:
        banner = load_banner_asset()

        self.assertIn("Duckln", banner)
        self.assertIn("AI Terminal Mentor", banner)

    def test_full_banner_is_used_when_width_is_sufficient(self) -> None:
        rendered = render_banner(width=120)

        self.assertIn("██████╗", rendered)
        self.assertNotIn(COMPACT_BANNER, rendered)

    def test_compact_banner_is_used_for_narrow_terminals(self) -> None:
        rendered = render_banner(width=20)

        self.assertIn(COMPACT_BANNER, rendered)
        self.assertNotIn("██████╗", rendered)


if __name__ == "__main__":
    unittest.main()
