"""Tests: Plan 54/55 narrative lines are stripped before they leak into search queries."""

from __future__ import annotations

import unittest

from duckln.repair_intake import (
    DUCKLN_SYNTHETIC_LINE_MARKERS,
    is_duckln_synthetic_line,
    strip_synthetic_and_prompt_lines,
)


class SyntheticMarkerCoverageTest(unittest.TestCase):
    def test_plan54_55_narrative_markers_are_recognised(self) -> None:
        narrative_lines = (
            "Duckln tried `sudo apt install -y nodejs` twice and got the same error",
            "Stopping the repair loop. Manual fix needed.",
            "Duckln using the OS install hint: sudo apt install -y nodejs",
            "Duckln spent 92s on runtime repair without resolving — escalating to the user.",
            "Duckln noticed a stale runtime target (gcp); using your current session target (local).",
            "Duckln running: node -e \"console.log(1)\"",
            "Duckln installing prerequisite: sudo apt install -y nodejs",
            "Duckln wants to run: `sudo apt install -y nodejs`",
        )
        for line in narrative_lines:
            with self.subTest(line=line):
                self.assertTrue(
                    is_duckln_synthetic_line(line),
                    f"expected line to be flagged as synthetic: {line!r}",
                )

    def test_strip_synthetic_lines_removes_plan55_messages(self) -> None:
        stderr = "\n".join(
            (
                "Duckln tried `sudo apt install -y nodejs` twice and got the same error",
                "Command 'node' not found",
            )
        )
        cleaned = strip_synthetic_and_prompt_lines(stderr)
        self.assertNotIn("duckln tried", cleaned.casefold())
        self.assertIn("Command 'node' not found", cleaned)

    def test_required_markers_present(self) -> None:
        for marker in (
            "duckln tried",
            "stopping the repair loop",
            "duckln using the os install hint",
            "duckln spent",
            "duckln noticed a stale",
            "duckln running:",
            "duckln installing prerequisite:",
            "duckln wants to run:",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, DUCKLN_SYNTHETIC_LINE_MARKERS)


if __name__ == "__main__":
    unittest.main()
