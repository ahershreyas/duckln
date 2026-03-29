"""Safety policies and command classification for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from duckln.logging_utils import log_blocked_command


class SafetyClass(str, Enum):
    """Safety classes for Duckln command handling."""

    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"


@dataclass(frozen=True)
class SafetyAssessment:
    """Result of evaluating a shell command for safety."""

    safety_class: SafetyClass
    reason: str
    blocked: bool = False
    whitelisted: bool = False
    matched_pattern: str | None = None


BLOCKED_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(^|\s)rm\s+-rf\s+/", "Destructive root deletion is blocked."),
    (r"(^|\s)sudo\s+rm\b", "Privileged file deletion is blocked."),
    (r"(^|\s)mkfs\b", "Filesystem formatting is blocked."),
    (r"(^|\s)dd\s+if=", "Raw disk overwrite commands are blocked."),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "Fork bombs are blocked."),
    (r"(^|\s)(shutdown|reboot|halt)\b", "System power commands are blocked."),
)

HOOTLWO_DIAGNOSTIC_PATTERNS: tuple[tuple[str, SafetyClass, str], ...] = (
    (r"^\s*python(\d+(\.\d+)*)?\s+--version\s*$", SafetyClass.S0, "Python version check."),
    (r"^\s*[A-Za-z0-9._/-]+/python\s+--version\s*$", SafetyClass.S0, "Project Python version check."),
    (r"^\s*python(\d+(\.\d+)*)?\s+-m\s+pip\s+--version\s*$", SafetyClass.S0, "Pip version check."),
    (r"^\s*[A-Za-z0-9._/-]+/python\s+-m\s+pip\s+--version\s*$", SafetyClass.S0, "Project pip version check."),
    (r"^\s*pip\s+--version\s*$", SafetyClass.S0, "Pip version check."),
    (r"^\s*pip\s+list\s*$", SafetyClass.S0, "Installed package listing."),
    (r"^\s*pip\s+show\s+[A-Za-z0-9._-]+\s*$", SafetyClass.S0, "Package metadata check."),
    (r"^\s*python(\d+(\.\d+)*)?\s+-m\s+pip\s+show\s+[A-Za-z0-9._-]+\s*$", SafetyClass.S0, "Package metadata check."),
    (r"^\s*which\s+[A-Za-z0-9._/-]+\s*$", SafetyClass.S0, "Binary path check."),
    (r"^\s*pwd\s*$", SafetyClass.S0, "Working directory check."),
    (r"^\s*test\s+-(d|f|x)\s+.+$", SafetyClass.S0, "Filesystem existence check."),
)

HOOTLWO_INSTALL_PATTERNS: tuple[tuple[str, SafetyClass, str], ...] = (
    (r"^\s*pip\s+install\s+[A-Za-z0-9._\-\[\]=<>! ]+\s*$", SafetyClass.S1, "Package install command."),
    (
        r"^\s*python(\d+(\.\d+)*)?\s+-m\s+pip\s+install\s+[A-Za-z0-9._\-\[\]=<>! ]+\s*$",
        SafetyClass.S1,
        "Package install command.",
    ),
    (r"^\s*python(\d+(\.\d+)*)?\s+-m\s+venv\s+[A-Za-z0-9._/-]+\s*$", SafetyClass.S1, "Virtualenv creation command."),
    (
        r"^\s*[A-Za-z0-9._/-]+/python\s+-m\s+pip\s+install\s+-r\s+[A-Za-z0-9._/-]+\s*$",
        SafetyClass.S1,
        "Requirements install command.",
    ),
    (
        r"^\s*[A-Za-z0-9._/-]+/python\s+-m\s+pip\s+install\s+-e\s+\.\s*$",
        SafetyClass.S1,
        "Editable install command.",
    ),
)

ELEVATED_COMMAND_PATTERNS: tuple[tuple[str, SafetyClass, str], ...] = (
    (r"(^|\s)(apt|apt-get|brew|conda)\s+install\b", SafetyClass.S3, "System package install needs approval."),
    (r"(^|\s)(pip|python(\d+(\.\d+)*)?\s+-m\s+pip)\s+uninstall\b", SafetyClass.S3, "Package removal needs approval."),
    (r"(^|\s)(chmod|chown|mv|cp)\b", SafetyClass.S3, "Filesystem mutation needs approval."),
    (r"(^|\s)git\s+(checkout|reset|clean)\b", SafetyClass.S3, "Git state mutation needs approval."),
)


def assess_command(command: str) -> SafetyAssessment:
    """Classify a command for blocking, approval, or whitelisted auto-run."""

    normalized = command.strip()
    if not normalized:
        return SafetyAssessment(
            safety_class=SafetyClass.S4,
            blocked=True,
            reason="Empty commands are blocked.",
        )

    blocked = match_blocked_command(normalized)
    if blocked is not None:
        return blocked

    for pattern, safety_class, reason in HOOTLWO_DIAGNOSTIC_PATTERNS + HOOTLWO_INSTALL_PATTERNS:
        if re.search(pattern, normalized):
            return SafetyAssessment(
                safety_class=safety_class,
                blocked=False,
                whitelisted=True,
                reason=reason,
                matched_pattern=pattern,
            )

    for pattern, safety_class, reason in ELEVATED_COMMAND_PATTERNS:
        if re.search(pattern, normalized):
            return SafetyAssessment(
                safety_class=safety_class,
                blocked=False,
                whitelisted=False,
                reason=reason,
                matched_pattern=pattern,
            )

    return SafetyAssessment(
        safety_class=SafetyClass.S2,
        blocked=False,
        whitelisted=False,
        reason="Command is not blocked but is outside the HOOTLWO safe whitelist.",
    )


def match_blocked_command(command: str) -> SafetyAssessment | None:
    """Return a blocked assessment when a command matches an S4 pattern."""

    for pattern, reason in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, command):
            assessment = SafetyAssessment(
                safety_class=SafetyClass.S4,
                blocked=True,
                whitelisted=False,
                reason=reason,
                matched_pattern=pattern,
            )
            log_blocked_command(
                command=command,
                reason=assessment.reason,
                safety_class=assessment.safety_class.value,
            )
            return assessment
    return None
