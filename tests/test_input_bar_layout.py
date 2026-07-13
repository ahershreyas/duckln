"""Tests: input bar no longer has #internet-icon; layout preserves chat-input (Plan 63 Fix 2)."""

from __future__ import annotations

import inspect as _inspect
import unittest


class InputBarLayoutTest(unittest.TestCase):
    def test_internet_icon_is_not_in_input_bar(self) -> None:
        try:
            import duckln.textual_ui as tui
        except Exception:
            self.skipTest("textual_ui not importable in this environment")
            return
        src = _inspect.getsource(tui)
        # Locate the input-bar Horizontal block and verify it doesn't
        # yield a Static with id="internet-icon".
        idx = src.find('with Horizontal(id="input-bar")')
        self.assertGreater(idx, 0, "input-bar Horizontal not found")
        block = src[idx:idx + 800]
        self.assertNotIn('id="internet-icon"', block)

    def test_input_bar_still_yields_chat_input(self) -> None:
        try:
            import duckln.textual_ui as tui
        except Exception:
            self.skipTest("textual_ui not importable in this environment")
            return
        src = _inspect.getsource(tui)
        idx = src.find('with Horizontal(id="input-bar")')
        block = src[idx:idx + 800]
        self.assertIn('id="chat-input"', block)
        self.assertIn('id="input-prompt"', block)
        self.assertIn('id="attach-button"', block)


if __name__ == "__main__":
    unittest.main()
