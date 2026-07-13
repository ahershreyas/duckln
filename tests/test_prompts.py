"""Tests for prompt building, next-step suggestions, verification, and teaching snippets."""

from __future__ import annotations

import unittest

from duckln.diagnostics import ErrorCategory
from duckln.modes import ControlMode
from duckln.prompts import (
    SuggestedCommand,
    build_core_system_prompt,
    build_planning_prompt,
    build_subagent_prompt_template,
    build_system_prompt,
    build_task_prompt,
    build_tool_policy_prompt,
    build_verification_checks,
    get_teaching_snippet,
    normalize_suggested_commands,
    render_suggestions_for_mode,
)


class PromptAndSuggestionTest(unittest.TestCase):
    def test_system_prompt_is_bounded_and_mode_aware(self) -> None:
        prompt = build_system_prompt(
            ControlMode.HOTL,
            workspace_sections={"SOUL.md": "Stay calm.", "USER.md": "Preferred alias: Shreyas"},
            execution_target="vm",
        )

        self.assertIn("at most 1-3 exact next commands", prompt)
        self.assertIn("require approval", prompt)
        self.assertIn("tools.json", prompt)
        self.assertIn("TODO.md", prompt)
        self.assertIn("SOUL.md", prompt)
        self.assertIn("USER.md", prompt)
        self.assertIn("Multipass", prompt)

    def test_layered_prompt_builders_capture_tool_and_planning_rules(self) -> None:
        self.assertIn("Answer directly", build_core_system_prompt(ControlMode.HITL))
        self.assertIn("tools.json", build_tool_policy_prompt(execution_target="vm"))
        self.assertIn("announce", build_tool_policy_prompt(execution_target="vm").lower())
        self.assertIn("shared duckln trace contract", build_tool_policy_prompt(execution_target="vm").lower())
        self.assertIn("tool order starts with", build_tool_policy_prompt(execution_target="vm").lower())
        self.assertIn("fresh remote information", build_tool_policy_prompt(execution_target="vm").lower())
        self.assertIn("TODO.md", build_planning_prompt())
        self.assertIn(
            "use only tools from tools.json",
            build_subagent_prompt_template(
                subagent_name="python",
                execution_target="local",
                workspace_sections={"TOOLS.md": "Use the smallest useful tool."},
            ),
        )
        self.assertIn(
            "show web sources or bounded search queries",
            build_subagent_prompt_template(
                subagent_name="python",
                execution_target="local",
                workspace_sections={"TOOLS.md": "Use the smallest useful tool."},
            ).lower(),
        )

    def test_task_prompt_uses_redacted_context(self) -> None:
        prompt = build_task_prompt(
            command="python app.py",
            error_category=ErrorCategory.MISSING_MODULE,
            redacted_stderr="ModuleNotFoundError: [REDACTED_TOKEN]",
            mode=ControlMode.HITL,
        )

        self.assertIn("missing_module", prompt)
        self.assertIn("[REDACTED_TOKEN]", prompt)

    def test_normalize_suggested_commands_limits_to_three_with_labels(self) -> None:
        commands = normalize_suggested_commands(
            [
                SuggestedCommand("Check python", "python --version"),
                SuggestedCommand("Check pip", "pip --version"),
                SuggestedCommand("Show torch", "pip show torch"),
                SuggestedCommand("Extra", "pwd"),
            ]
        )

        self.assertEqual(3, len(commands))
        self.assertEqual("Check python", commands[0].purpose)

    def test_hitl_suggestions_never_execute(self) -> None:
        rendered = render_suggestions_for_mode(
            ControlMode.HITL,
            (
                SuggestedCommand("Check python", "python --version"),
            ),
        )

        self.assertFalse(rendered[0].can_execute)
        self.assertFalse(rendered[0].auto_run)

    def test_hotl_suggestions_require_approval_before_execution(self) -> None:
        suggestion = SuggestedCommand("Check python", "python --version")
        pending = render_suggestions_for_mode(ControlMode.HOTL, (suggestion,))
        approved = render_suggestions_for_mode(
            ControlMode.HOTL,
            (suggestion,),
            approved_commands={"python --version"},
        )

        self.assertTrue(pending[0].requires_approval)
        self.assertTrue(approved[0].can_execute)

    def test_verification_checks_put_targeted_check_before_rerun(self) -> None:
        checks = build_verification_checks(
            original_command="python app.py",
            suggested_fix=SuggestedCommand("Install torch", "python -m pip install torch"),
            error_category=ErrorCategory.MISSING_MODULE,
        )

        self.assertEqual("Check installed package", checks[0].purpose)
        self.assertEqual("Rerun original command", checks[1].purpose)

    def test_teaching_snippets_are_short_and_topic_specific(self) -> None:
        self.assertIn("virtual environment", get_teaching_snippet("venv"))
        self.assertIn("pip installs packages", get_teaching_snippet("pip"))
        self.assertIn("CUDA support", get_teaching_snippet("cuda"))
        self.assertIn("PyTorch build", get_teaching_snippet("torch_mismatch"))


if __name__ == "__main__":
    unittest.main()
