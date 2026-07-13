"""Plan 166 — Claude-style live token counter on the "Duckln's thinking" line."""

from __future__ import annotations

import unittest

import duckln.textual_ui as tui


class ThoughtsTokenSuffix(unittest.TestCase):
    def test_k_format_for_large(self):
        self.assertEqual(" · 1.9k tokens (session)", tui._thoughts_token_suffix(1900))

    def test_integer_below_1k(self):
        self.assertEqual(" · 850 tokens (session)", tui._thoughts_token_suffix(850))

    def test_empty_when_zero(self):
        self.assertEqual("", tui._thoughts_token_suffix(0))
        self.assertEqual("", tui._thoughts_token_suffix(-5))


if __name__ == "__main__":
    unittest.main()
