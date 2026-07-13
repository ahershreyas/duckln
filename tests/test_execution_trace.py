"""Tests for shared Duckln execution-trace helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from duckln.execution_trace import build_failure_message, render_execution_trace, render_tool_invocation_trace


class ExecutionTraceTest(unittest.TestCase):
    def test_render_execution_trace_formats_title_and_steps(self) -> None:
        rendered = render_execution_trace("repo runtime review", ("Step one", "Step two"))

        self.assertIn("Duckln trace: repo runtime review.", rendered)
        self.assertIn("1. Step one", rendered)
        self.assertIn("2. Step two", rendered)

    def test_render_tool_invocation_trace_grounds_output_in_tool_registry(self) -> None:
        rendered = render_tool_invocation_trace(
            title="dependency install review",
            tool_id="shell.command_runner",
            action="Review a dependency install",
            detail_lines=("Planned command: python -m pip install -r requirements.txt",),
            source_urls=("https://packaging.python.org/en/latest/tutorials/installing-packages/",),
        )

        self.assertIn("Controlled shell runner", rendered)
        self.assertIn("shell.command_runner", rendered)
        self.assertIn("safety class: s1", rendered.lower())
        self.assertIn("Sources: [1] https://packaging.python.org/en/latest/tutorials/installing-packages/", rendered)

    def test_build_failure_message_includes_query_when_web_search_broadens(self) -> None:
        with patch(
            "duckln.web_runtime.build_runtime_web_reference_note",
            return_value="Duckln broadened the search on the web for this blocker: Fix guide — Try a clean environment. Source: https://example.com/fix",
        ), patch(
            "duckln.web_runtime.build_runtime_search_query",
            return_value="whisper ModuleNotFoundError fix",
        ):
            rendered = build_failure_message(
                "Run failed",
                "ModuleNotFoundError: No module named whisper",
                command=".venv/bin/python -m whisper --help",
            )

        self.assertIn("Duckln search query: `whisper ModuleNotFoundError fix`.", rendered)
        self.assertIn("https://example.com/fix", rendered)


if __name__ == "__main__":
    unittest.main()
