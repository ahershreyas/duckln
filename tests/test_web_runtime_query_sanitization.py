"""Regression tests: search queries must strip shell prompts and Duckln narrative.

Before the fix, a stderr blob containing a shell prompt
(`ubuntu@duckln-vm:~/path$ command`) or Duckln's own log narrative
("Specialist route: rust …") leaked into the DuckDuckGo query string, producing
unsearchable junk like the one in the user's screenshot.
"""

from __future__ import annotations

import unittest

from duckln.web_runtime import _build_search_query, _sanitize_error_lines


class SanitizeErrorLinesTest(unittest.TestCase):
    def test_drops_shell_prompt_lines(self) -> None:
        text = (
            "ubuntu@duckln-vm:~/.duckln/projects/JustHireMe$ npm install\n"
            "npm error code EACCES\n"
        )
        cleaned = _sanitize_error_lines(text)
        joined = " | ".join(cleaned)
        self.assertNotIn("ubuntu@duckln-vm", joined)
        self.assertIn("npm error code EACCES", joined)

    def test_drops_duckln_narrative_lines(self) -> None:
        text = (
            "Duckln classified this blocker as rust dependency failure.\n"
            "Specialist route: rust. Toolchain: cargo.\n"
            "bash: line 1: multipass: command not found\n"
        )
        cleaned = _sanitize_error_lines(text)
        joined = " | ".join(cleaned)
        self.assertNotIn("Duckln classified", joined)
        self.assertNotIn("Specialist route", joined)
        self.assertIn("multipass: command not found", joined)

    def test_returns_empty_when_only_synthetic_input(self) -> None:
        text = (
            "Duckln classified this blocker as missing command.\n"
            "Specialist route: rust. Toolchain: cargo.\n"
        )
        self.assertEqual((), _sanitize_error_lines(text))


class BuildSearchQuerySanitizationTest(unittest.TestCase):
    def test_query_does_not_contain_shell_prompt(self) -> None:
        query = _build_search_query(
            command="npm install",
            error_text=(
                "ubuntu@duckln-vm:~/.duckln/projects/JustHireMe$ npm install\n"
                "npm error code EACCES\n"
            ),
        )
        self.assertNotIn("ubuntu@duckln-vm", query)
        self.assertIn("EACCES", query)

    def test_query_does_not_contain_duckln_narrative(self) -> None:
        query = _build_search_query(
            command="npm ci",
            error_text=(
                "Duckln classified this blocker as rust dependency failure.\n"
                "Specialist route: rust. Toolchain: cargo.\n"
                "npm error: missing peer dependency\n"
            ),
        )
        self.assertNotIn("Duckln classified", query)
        self.assertNotIn("Specialist route", query)
        self.assertIn("npm error", query)

    def test_query_empty_when_error_text_is_only_synthetic(self) -> None:
        query = _build_search_query(
            command=None,
            error_text=(
                "Duckln classified this blocker as missing command.\n"
                "Specialist route: rust. Toolchain: cargo.\n"
            ),
        )
        # Without the command or a real error, there's nothing searchable.
        self.assertNotIn("Duckln classified", query)
        self.assertNotIn("Specialist route", query)
        self.assertNotIn("Toolchain", query)


if __name__ == "__main__":
    unittest.main()
