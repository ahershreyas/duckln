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

# Plan 142 F1: a "safe arguments" fragment for read-only diagnostics — any chars EXCEPT
# shell metacharacters that could chain / redirect / substitute a command (`;`, `&`, `|`,
# `$`, backtick, `<`, `>`, `(`, `)`, `{`, `}`, newline). With `^…$` anchoring this means a
# read command like `cat foo; rm -rf /` can NEVER match as S0 (the `;…` breaks the match),
# and `match_blocked_command` runs first regardless. Widens the S0 set so the reasoning
# agent can actually INVESTIGATE (ls/cat/grep/find/uname/id/os-release/versions/git status).
_RO_ARGS = r"[^;&|$`<>(){}\n]*"
_RO_ARG1 = r"[^;&|$`<>(){}\n]+"

HOOTLWO_DIAGNOSTIC_PATTERNS: tuple[tuple[str, SafetyClass, str], ...] = (
    # Plan 142 F1 — generic read-only diagnostics (never mutate the filesystem).
    (rf"^\s*(ls|cat|head|tail|wc|file|stat|realpath|readlink|basename|dirname|tree|grep|egrep|fgrep|nl|env|printenv|uname|hostname|whoami|id|groups|df|du|free|nproc|lscpu|lsb_release|ps|date|uptime|cat\s+/etc/os-release){_RO_ARGS}$", SafetyClass.S0, "Read-only diagnostic command."),
    # `find` is read-only ONLY without its mutating/exec actions.
    (rf"^\s*find\b(?!{_RO_ARGS}-(?:delete|exec|execdir|ok|okdir|fprint|fprintf|fls)\b){_RO_ARGS}$", SafetyClass.S0, "Read-only file search."),
    (rf"^\s*(command\s+-v|type)\s+{_RO_ARG1}$", SafetyClass.S0, "Tool availability check."),
    (r"^\s*(node|npm|pnpm|yarn|bun|deno|cargo|rustc|rustup|go|java|ruby|php|dotnet|gcc|g\+\+|clang|make|cmake)\s+(--version|-v|-V|version)\s*$", SafetyClass.S0, "Tool version check."),
    (rf"^\s*npm\s+(ls|list|view|root|why|outdated|config\s+get){_RO_ARGS}$", SafetyClass.S0, "npm read command."),
    (rf"^\s*cargo\s+(metadata|tree){_RO_ARGS}$", SafetyClass.S0, "cargo read command."),
    (rf"^\s*go\s+(version|env|list){_RO_ARGS}$", SafetyClass.S0, "go read command."),
    (rf"^\s*(dpkg|dpkg-query)\s+(-l|-s|-L|-W|--audit|--get-selections|--status|--list|--listfiles){_RO_ARGS}$", SafetyClass.S0, "dpkg read command."),
    (rf"^\s*apt-cache\s+(policy|show|showpkg|search|madison|depends|rdepends){_RO_ARGS}$", SafetyClass.S0, "apt-cache read command."),
    (rf"^\s*git\s+(status|log|remote|rev-parse|branch|diff|show|ls-files|describe|symbolic-ref|cat-file|config|for-each-ref|tag|shortlog){_RO_ARGS}$", SafetyClass.S0, "git read command."),
    (r"^\s*sudo\s+-n\s+true\s*$", SafetyClass.S0, "Sudo capability check."),
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
    (
        r"^\s*git\s+clone\s+--depth\s+1\s+https://[A-Za-z0-9./:_-]+\s+.+$",
        SafetyClass.S1,
        "Shallow repository clone into Duckln-managed workspace.",
    ),
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


def assess_command(command: str, *, execution_target: str | None = None) -> SafetyAssessment:
    """Classify a command for blocking, approval, or whitelisted auto-run.

    Plan 58 Bug C: when execution_target is a remote Linux target
    (vm/aws/gcp/ssh), reject commands that invoke `brew` — brew is macOS-only,
    so the command will fail on the remote. This is defense-in-depth against
    any code path that derives an install command from the local host's OS
    instead of the target's OS.
    """

    normalized = command.strip()
    if not normalized:
        return SafetyAssessment(
            safety_class=SafetyClass.S4,
            blocked=True,
            reason="Empty commands are blocked.",
        )

    if execution_target in {"vm", "aws", "gcp", "ssh"}:
        if re.match(r"^\s*(sudo\s+)?brew\b", normalized):
            return SafetyAssessment(
                safety_class=SafetyClass.S3,
                blocked=True,
                whitelisted=False,
                reason=(
                    "brew is macOS-only; this target is a Linux VM. Use apt-based "
                    "install commands instead."
                ),
            )
        # Plan 61 Fix A: winget / choco are Windows-only; block on Linux VM/cloud.
        if re.match(r"^\s*winget\b", normalized) or re.match(r"^\s*choco\b", normalized):
            return SafetyAssessment(
                safety_class=SafetyClass.S3,
                blocked=True,
                whitelisted=False,
                reason=(
                    "winget/choco are Windows-only; this target is a Linux VM. "
                    "Use apt-based install commands instead."
                ),
            )
    # Plan 61 Fix A: block apt/sudo apt when local target is Windows.
    if execution_target == "windows_local":
        if re.match(r"^\s*(sudo\s+)?apt(-get)?\b", normalized):
            return SafetyAssessment(
                safety_class=SafetyClass.S3,
                blocked=True,
                whitelisted=False,
                reason=(
                    "apt is Linux-only; this host is Windows. Use winget install "
                    "commands instead."
                ),
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
