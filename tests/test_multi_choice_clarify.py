"""Tests: clarify_prompt multi-choice adapter (Plan 61 Fix E)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from duckln.clarify_prompts import (
    make_cli_clarify_prompt,
    make_textual_clarify_prompt,
    resolve_clarify_prompt,
)


class CliClarifyPromptTest(unittest.TestCase):
    def test_returns_index_of_chosen_option(self) -> None:
        with patch("duckln.selections.duckln_select", return_value="Option B"):
            prompt = make_cli_clarify_prompt()
            idx = prompt("Pick one", ("Option A", "Option B", "Option C"))
            self.assertEqual(idx, 1)

    def test_returns_none_when_select_returns_none(self) -> None:
        with patch("duckln.selections.duckln_select", return_value=None):
            prompt = make_cli_clarify_prompt()
            idx = prompt("Pick one", ("A", "B"))
            self.assertIsNone(idx)

    def test_returns_none_for_empty_choices(self) -> None:
        prompt = make_cli_clarify_prompt()
        idx = prompt("Pick one", ())
        self.assertIsNone(idx)


class TextualClarifyPromptTest(unittest.TestCase):
    def test_returns_none_for_nil_chat(self) -> None:
        self.assertIsNone(make_textual_clarify_prompt(chat=None))

    def test_returns_none_when_chat_lacks_overlay(self) -> None:
        class _DummyChat:
            pass
        self.assertIsNone(make_textual_clarify_prompt(chat=_DummyChat()))


class ResolveClarifyPromptTest(unittest.TestCase):
    def test_falls_back_to_cli_when_no_chat(self) -> None:
        prompt = resolve_clarify_prompt(chat=None)
        self.assertIsNotNone(prompt)
        # CLI adapter under the hood.
        with patch("duckln.selections.duckln_select", return_value="X"):
            idx = prompt("Q", ("X", "Y"))
            self.assertEqual(idx, 0)


if __name__ == "__main__":
    unittest.main()
