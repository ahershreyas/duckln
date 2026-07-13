"""Tests for interactive Duckln selector styling."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from duckln.selections import (
    DUCKLN_SELECT_STYLE,
    _apply_duckln_fuzzy_renderer,
    _apply_duckln_single_select_renderer,
    duckln_search_select,
    duckln_select,
)


class SelectionStyleTest(unittest.TestCase):
    def test_duckln_select_uses_circle_and_brand_dot_style(self) -> None:
        fake_prompt = Mock()
        fake_prompt.execute.return_value = "OpenAI"
        fake_prompt.content_control = Mock()

        with patch("duckln.selections.inquirer.select", return_value=fake_prompt) as mocked_select:
            selected = duckln_select("Select provider:", ("OpenRouter", "OpenAI", "Anthropic"))

        self.assertEqual("OpenAI", selected)
        mocked_select.assert_called_once()
        kwargs = mocked_select.call_args.kwargs
        self.assertEqual("●", kwargs["pointer"])
        self.assertEqual("○", kwargs["marker"])
        self.assertEqual("◆", kwargs["qmark"])
        self.assertEqual(DUCKLN_SELECT_STYLE, kwargs["style"])

    def test_duckln_search_select_uses_circle_and_brand_dot_style(self) -> None:
        fake_prompt = Mock()
        fake_prompt.execute.return_value = "repo-a"
        fake_prompt.content_control = Mock()

        with patch("duckln.selections.inquirer.fuzzy", return_value=fake_prompt) as mocked_fuzzy:
            selected = duckln_search_select("Select a repository:", ("repo-a", "repo-b"))

        self.assertEqual("repo-a", selected)
        mocked_fuzzy.assert_called_once()
        kwargs = mocked_fuzzy.call_args.kwargs
        self.assertEqual("●", kwargs["pointer"])
        self.assertEqual("○", kwargs["marker"])
        self.assertEqual("◆", kwargs["qmark"])
        self.assertEqual(DUCKLN_SELECT_STYLE, kwargs["style"])
        self.assertFalse(kwargs["info"])

    def test_single_select_renderer_shows_selected_brand_dot_and_unselected_circle(self) -> None:
        content_control = Mock()
        prompt = Mock(content_control=content_control)

        _apply_duckln_single_select_renderer(prompt)

        selected_tokens = content_control._get_hover_text({"name": "tensorflow"})
        normal_tokens = content_control._get_normal_text({"name": "AutoGPT"})
        self.assertEqual(("class:marker-selected", "●"), selected_tokens[0])
        self.assertEqual(("class:marker", "○"), normal_tokens[0])
        self.assertEqual(("", "  "), selected_tokens[1])
        self.assertEqual(("", "  "), normal_tokens[1])

    def test_fuzzy_renderer_keeps_marker_column_and_spacing(self) -> None:
        content_control = Mock()
        prompt = Mock(content_control=content_control)

        _apply_duckln_fuzzy_renderer(prompt)

        selected_tokens = content_control._get_hover_text({"name": "tensorflow", "indices": []})
        normal_tokens = content_control._get_normal_text({"name": "AutoGPT", "indices": []})
        self.assertEqual(("class:marker-selected", "●"), selected_tokens[0])
        self.assertEqual(("class:marker", "○"), normal_tokens[0])
        self.assertEqual(("", "  "), selected_tokens[1])
        self.assertEqual(("", "  "), normal_tokens[1])


if __name__ == "__main__":
    unittest.main()
