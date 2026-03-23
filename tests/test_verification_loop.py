"""Phase 7 integration coverage for the suggestion and verification loop.

Req: R7, R8, R9
Plan: 8, 9, 10
Tasks:
- Integration tests for verification loop
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from duckln.diagnostics import ErrorCategory, gather_minimal_context
from duckln.modes import ControlMode
from duckln.prompts import (
    SuggestedCommand,
    build_task_prompt,
    build_verification_checks,
    get_teaching_snippet,
    normalize_suggested_commands,
    render_suggestions_for_mode,
)
from duckln.shell import ControlledCommandRunner


class SuggestionVerificationLoopReqR7R8R9Plan8Plan9Plan10Test(unittest.TestCase):
    """Covers input -> suggestion -> execution gate -> verification result."""

    def test_r7_r8_r9_plan8_plan9_plan10_hotl_file_fix_verification_flow_succeeds(self) -> None:
        runner = ControlledCommandRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing.txt"
            original_command = f"cat {missing_path}"
            stderr = f"cat: {missing_path}: No such file or directory"

            context = gather_minimal_context(
                command=original_command,
                stderr=stderr,
            )
            prompt = build_task_prompt(
                command=context.command,
                error_category=context.category,
                redacted_stderr=context.redacted_stderr,
                mode=ControlMode.HOTL,
            )
            suggestions = normalize_suggested_commands(
                [
                    SuggestedCommand("Create the missing file", f"touch {missing_path}"),
                    SuggestedCommand("Retry the original command", original_command),
                ]
            )
            rendered = render_suggestions_for_mode(
                ControlMode.HOTL,
                suggestions,
                approved_commands={f"touch {missing_path}"},
            )

            fix_result = runner.run(rendered[0].suggestion.command, timeout_seconds=1)
            verification_checks = build_verification_checks(
                original_command=original_command,
                suggested_fix=rendered[0].suggestion,
                error_category=ErrorCategory.FILE_NOT_FOUND,
            )
            targeted_result = runner.run(verification_checks[0].command, timeout_seconds=1)
            rerun_result = runner.run(verification_checks[1].command, timeout_seconds=1)

            self.assertEqual(ErrorCategory.FILE_NOT_FOUND, context.category)
            self.assertIn("file_not_found", prompt)
            self.assertEqual("Create the missing file", rendered[0].suggestion.purpose)
            self.assertTrue(rendered[0].can_execute)
            self.assertFalse(rendered[0].auto_run)
            self.assertEqual("Check expected path", verification_checks[0].purpose)
            self.assertEqual("Rerun original command", verification_checks[1].purpose)
            self.assertEqual(0, fix_result.exit_code)
            self.assertEqual(0, targeted_result.exit_code)
            self.assertEqual(0, rerun_result.exit_code)

    def test_r7_r8_r9_plan8_plan9_plan10_hitl_keeps_suggestion_manual_and_teaches_briefly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "manual.txt"
            original_command = f"cat {missing_path}"
            stderr = f"cat: {missing_path}: No such file or directory"

            context = gather_minimal_context(
                command=original_command,
                stderr=stderr,
            )
            suggestions = normalize_suggested_commands(
                [SuggestedCommand("Create the missing file", f"touch {missing_path}")]
            )
            rendered = render_suggestions_for_mode(ControlMode.HITL, suggestions)
            teaching = get_teaching_snippet("venv")

            self.assertEqual(ErrorCategory.FILE_NOT_FOUND, context.category)
            self.assertFalse(rendered[0].can_execute)
            self.assertFalse(rendered[0].auto_run)
            self.assertIn("virtual environment", teaching)


if __name__ == "__main__":
    unittest.main()
