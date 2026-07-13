"""Prompt builders for provider-neutral Duckln requests."""

from __future__ import annotations

from dataclasses import dataclass
import re

from duckln.diagnostics import ErrorCategory
from duckln.modes import ControlMode, evaluate_mode_action
from duckln.safety import assess_command
from duckln.tool_registry import tool_policy_summary


MAX_NEXT_COMMANDS = 3

TEACHING_SNIPPETS: dict[str, str] = {
    "venv": "A virtual environment keeps project packages isolated so one project does not break another.",
    "pip": "pip installs packages into the Python environment it is attached to, so matching pip to python matters.",
    "cuda": "CUDA support depends on your GPU drivers, PyTorch build, and local toolkit lining up.",
    "torch_mismatch": "Torch CUDA errors usually mean your installed PyTorch build does not match the machine GPU setup.",
}


@dataclass(frozen=True)
class SuggestedCommand:
    """A single exact next command with a short purpose label."""

    purpose: str
    command: str


@dataclass(frozen=True)
class SuggestionExecutionView:
    """Mode-specific execution state for a suggested command."""

    suggestion: SuggestedCommand
    can_execute: bool
    requires_approval: bool
    auto_run: bool
    reason: str


@dataclass(frozen=True)
class VerificationCheck:
    """A verification step after a fix is suggested or executed."""

    purpose: str
    command: str


def build_system_prompt(
    mode: ControlMode,
    *,
    workspace_sections: dict[str, str] | None = None,
    execution_target: str = "local",
) -> str:
    """Build a bounded provider-neutral system prompt."""

    sections = workspace_sections or {}
    prompt_parts = [
        build_core_system_prompt(mode),
        build_tool_policy_prompt(execution_target=execution_target),
        build_planning_prompt(),
    ]
    prompt_parts.extend(_workspace_prompt_blocks(sections))
    return "\n".join(
        (
            *prompt_parts,
        )
    )


def build_core_system_prompt(mode: ControlMode) -> str:
    """Build the core Duckln system prompt shared by provider-backed flows."""

    mode_line = {
        ControlMode.HITL: "HITL: explain briefly and suggest commands only.",
        ControlMode.HOTL: "HOTL: suggest commands that require approval before execution.",
        ControlMode.HOOTLWO: "HOOTLWO: only safe whitelisted commands may auto-run.",
    }[mode]
    return (
        "You are Duckln, an AI terminal mentor. "
        "Keep explanations concise, actionable, and privacy-first. "
        "Return at most 1-3 exact next commands. "
        "Each command must have a short purpose label. "
        "Do not invent fixes or include full logs. "
        "Understand whether the user is asking for conversation, repo analysis, setup planning, or execution. "
        "Answer directly when intent is clear and avoid robotic fallback. "
        "Prefer the next useful delta over repeating the prior answer. "
        f"{mode_line}"
    )


def build_tool_policy_prompt(*, execution_target: str) -> str:
    """Build the tool-policy layer for prompt composition."""

    return tool_policy_summary(execution_target=execution_target)


def build_planning_prompt() -> str:
    """Build the planning-discipline layer for prompt composition."""

    return (
        "Before repo-changing work, create or update TODO.md with understanding, inspection steps, planned changes, "
        "and what completion looks like. Keep active execution-target continuity unless the user changes it."
    )


def build_subagent_prompt_template(
    *,
    subagent_name: str,
    execution_target: str,
    workspace_sections: dict[str, str] | None = None,
    allowed_tool_labels: tuple[str, ...] = (),
) -> str:
    """Build a compact specialist/subagent prompt scaffold."""

    prompt = (
        f"You are the {subagent_name} specialist. Read before acting, plan before changing, stay grounded in repo evidence, "
        f"use only tools from tools.json for the active {execution_target} target, follow Duckln's shared trace contract by naming the tool and action you are taking, "
        "show web sources or bounded search queries when you use them, and escalate risky or unclear work to the supervisor."
    )
    if allowed_tool_labels:
        prompt += f" Allowed tools for this specialist: {', '.join(allowed_tool_labels)}."
    sections = workspace_sections or {}
    if not sections:
        return prompt
    return prompt + "\n" + "\n".join(_workspace_prompt_blocks(sections))


def build_task_prompt(
    *,
    command: str,
    error_category: ErrorCategory,
    redacted_stderr: str,
    mode: ControlMode,
) -> str:
    """Build a bounded task prompt for provider calls."""

    return (
        f"Command: {command}\n"
        f"Mode: {mode.value}\n"
        f"Classified error: {error_category.value}\n"
        f"Redacted stderr: {redacted_stderr}\n"
        "Respond with a concise explanation, 1-3 exact next commands, and only the minimum needed context."
    )


def build_repair_skill_prompt(
    *,
    failed_command: str,
    error_output: str,
    recovery_command: str,
    repo_family: str,
    repo_name: str,
    framework: str,
) -> str:
    """Build an LLM prompt that extracts a reusable repair skill from a successful recovery."""
    safe_error = error_output.strip()[:800] if error_output else "(no output)"
    return (
        "A repo setup repair just succeeded. Extract a reusable skill note so future agents "
        "know what to do when they see the same error.\n\n"
        f"Repo: {repo_name} ({framework}), family: {repo_family}\n"
        f"Failed command: {failed_command}\n"
        f"Error output (trimmed):\n{safe_error}\n"
        f"Recovery command that worked: {recovery_command}\n\n"
        "Respond with ONLY a JSON object — no markdown, no explanation. Schema:\n"
        "{\n"
        '  "slug": "repair-<repo_family>-<short-error-keyword>",\n'
        '  "title": "Repair: <one short phrase>",\n'
        '  "trigger_pattern": "<one-line pattern a future agent should match in stderr>",\n'
        '  "fix_sequence": ["<command1>", "<command2>"],\n'
        '  "verification": "<command to confirm fix worked>",\n'
        '  "notes": "<optional caveat or empty string>"\n'
        "}\n"
        "Keep each field concise. slug must be lowercase with hyphens only."
    )


def normalize_suggested_commands(commands: list[SuggestedCommand]) -> tuple[SuggestedCommand, ...]:
    """Validate and bound next-step commands to the required 1-3 items."""

    cleaned = [
        SuggestedCommand(purpose=item.purpose.strip(), command=item.command.strip())
        for item in commands
        if item.purpose.strip() and item.command.strip()
    ]
    return tuple(cleaned[:MAX_NEXT_COMMANDS])


def render_suggestions_for_mode(
    mode: ControlMode,
    suggestions: tuple[SuggestedCommand, ...],
    *,
    approved_commands: set[str] | None = None,
) -> tuple[SuggestionExecutionView, ...]:
    """Apply mode policy to suggested commands without executing them."""

    approved = approved_commands or set()
    rendered: list[SuggestionExecutionView] = []
    for suggestion in suggestions:
        decision = evaluate_mode_action(
            mode,
            assess_command(suggestion.command),
            is_ai_suggested=True,
            user_approved=suggestion.command in approved,
        )
        rendered.append(
            SuggestionExecutionView(
                suggestion=suggestion,
                can_execute=decision.allowed,
                requires_approval=decision.requires_approval,
                auto_run=decision.auto_run,
                reason=decision.reason,
            )
        )
    return tuple(rendered)


def build_verification_checks(
    *,
    original_command: str,
    suggested_fix: SuggestedCommand,
    error_category: ErrorCategory,
) -> tuple[VerificationCheck, ...]:
    """Create a targeted verification plan followed by rerun verification when useful."""

    targeted: VerificationCheck | None = None
    package = _extract_package_name(suggested_fix.command)

    if error_category in {ErrorCategory.MISSING_MODULE, ErrorCategory.PIP_PYTHON_MISMATCH} and package:
        targeted = VerificationCheck(
            purpose="Check installed package",
            command=f"python -m pip show {package}",
        )
    elif error_category is ErrorCategory.FILE_NOT_FOUND:
        path = _extract_missing_path(original_command)
        if path:
            targeted = VerificationCheck(
                purpose="Check expected path",
                command=f"ls {path}",
            )
    elif error_category is ErrorCategory.PERMISSION_DENIED:
        targeted = VerificationCheck(
            purpose="Check current permissions",
            command="pwd",
        )
    elif error_category is ErrorCategory.CUDA_TORCH_MISMATCH:
        targeted = VerificationCheck(
            purpose="Check torch CUDA availability",
            command="python -c \"import torch; print(torch.cuda.is_available())\"",
        )

    checks: list[VerificationCheck] = []
    if targeted is not None:
        checks.append(targeted)
    checks.append(VerificationCheck(purpose="Rerun original command", command=original_command))
    return tuple(checks)


def get_teaching_snippet(topic: str) -> str:
    """Return a concise teaching snippet for a known troubleshooting topic."""

    return TEACHING_SNIPPETS.get(topic, "")


def _extract_package_name(command: str) -> str | None:
    match = re.search(r"pip\s+install\s+([A-Za-z0-9._-]+)", command)
    if match:
        return match.group(1)
    return None


def _extract_missing_path(command: str) -> str | None:
    parts = command.strip().split()
    if len(parts) > 1:
        return parts[-1]
    return None


def _workspace_prompt_blocks(sections: dict[str, str]) -> tuple[str, ...]:
    ordered = (
        "IDENTITY.md",
        "SOUL.md",
        "USER.md",
        "TOOLS.md",
    )
    blocks: list[str] = []
    for name in ordered:
        content = sections.get(name)
        if content:
            blocks.append(f"{name}\n{content}")
    return tuple(blocks)


# --- Plan 67/157: Plan Mode prompts ------------------------------------------
# Plan 157 P3: the five SYSTEM_PROMPT_PLAN_* constants that used to live here are RETIRED.
# Each planning agent's prompt is now its `.md` spec body (the single source of truth):
#   PROPOSE → agents/planner.md   CRITIQUE → agents/critic.md   CLARIFY → agents/clarifier.md
#   VERDICT → agents/verdict.md   ATTRIBUTE → agents/attributor.md
# plan_mode.py sources them via `_spec_prompt(<name>)`. Edit behavior by editing the spec.
