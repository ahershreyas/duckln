"""Optional Textual split-pane UI scaffolding for Duckln."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from importlib import util as importlib_util
import asyncio
import os
import platform
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import termios
import textwrap
import tty
import uuid
import webbrowser
from html import unescape
from pathlib import Path
from typing import Callable

import httpx

from duckln.browser_runtime import ManagedBrowserWindow
from duckln.constants import COLOUR_COMMAND, COLOUR_GREY, COLOUR_SYSTEM, COLOUR_USER_INPUT, SLASH_COMMANDS
from duckln.repair_intake import (
    DependencyApprovalDecision,
    DependencyApprovalRequest,
    render_dependency_approval_lines,
    summarize_failure_incident,
)
from duckln.transcript_styles import (
    transcript_body_colour,
    transcript_highlight_inline,
    transcript_prefix,
    transcript_prefix_colour,
)


DUCKLN_GOLD = "#F5A623"
LOCAL_GREEN = "#4CAF50"
VM_GOLD = "#F5A623"
AWS_ORANGE = "#FF9900"
GCP_BLUE = "#4285F4"
DOCKER_CYAN = "#00FFFF"
UNFOCUSED_BORDER = "#3a3a3a"
DUCKLN_EGGSHELL = COLOUR_USER_INPUT
DUCKLN_SYSTEM = COLOUR_SYSTEM
DUCKLN_GREY = COLOUR_GREY
DUCKLN_CYAN = COLOUR_COMMAND
_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_OLLAMA_PULL_PROGRESS_PATTERN = re.compile(
    r"pulling\s+([0-9a-f]+)\.\.\.\s+(\d+)%\s+.*?(\d+(?:\.\d+)?\s*[KMG]?B(?:/\d+(?:\.\d+)?\s*[KMG]?B)?)?$",
    re.IGNORECASE,
)
_INLINE_TOKEN_PATTERN = re.compile(
    r"`[^`]+`|https?://\S+|(?<!\w)(?:~\/|\.\/|\.\.\/|/)[^\s,;]+|(?<!\w)/(?:[a-z][\w-]*)(?:\s+[a-z][\w-]*)?|\[(?:[#-]{2,8}|\d{1,3})\]"
)
_TERMINAL_URL_PATTERN = re.compile(r"https?://[^\s<>'\")\]]+")
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_LONG_ACTIVITY_NOTICE_SECONDS = 120.0
_SORTED_SLASH_COMMANDS = tuple(sorted(SLASH_COMMANDS, key=lambda item: (len(item), item)))
_SLASH_COMMAND_HINTS = {
    "/": "Open the command palette",
    "/help": "Show Duckln commands",
    "/provider": "Change provider and key flow",
    "/model": "Pick the current provider model",
    "/mode": "Switch HITL, HOTL, or HOOTLWO",
    "/config": "Open configuration menu",
    "/repos": "Browse cached repos",
    "/repos tracked": "Show tracked repo installs",
    "/repos active": "Show the active repo context",
    "/repos status": "Show active repo setup status",
    "/repos history": "Show scoped repo history",
    "/repos live": "Show live repo sessions",
    "/repos path": "Show active repo path",
    "/repos link": "Track an existing repo path",
    "/repos remove": "Remove a tracked repo safely",
    "/repos refresh": "Refresh repo catalog",
    "/memory clear": "Clear Duckln memory safely",
    "/remember": "Save concise session context",
    "/healthcheck": "Run environment healthcheck",
    "/skills": "List Duckln skills",
    "/skill add": "Add a new skill proposal",
    "/tools": "List Duckln tools",
    "/tools add": "Add a new tool proposal",
    "/mcp": "List MCP servers and tools",
    "/internet": "Toggle DuckDuckGo web search",
    "/vm": "Create or configure a VM",
    "/cloud": "Manage cloud resources",
    "/cleanup": "Delete installed VMs / Docker images+containers / cloud",
    "/explore": "Browse GitHub trending repos and one-key launch setup",
    "/exit": "Exit Duckln",
}


def _wrap_chat_message(message: str, *, prefix: str, width: int) -> str:
    """Wrap chat transcript text to the visible pane width."""
    available = max(12, width - len(prefix))
    wrapped_lines: list[str] = []
    for raw_line in str(message).splitlines() or [""]:
        if _TERMINAL_URL_PATTERN.search(raw_line):
            wrapped_lines.append(f"{prefix if not wrapped_lines else ' ' * len(prefix)}{raw_line}")
            continue
        if not raw_line.strip():
            wrapped_lines.append(prefix if not wrapped_lines else " " * len(prefix))
            continue

        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)
        bullet_prefix = ""
        continuation_indent = indent
        bullet_match = re.match(r"((?:[-*]\s)|(?:\d+\.\s))", stripped)
        if bullet_match:
            bullet_prefix = bullet_match.group(1)
            continuation_indent = indent + len(bullet_prefix)
            stripped = stripped[len(bullet_prefix) :]

        initial_indent = " " * indent + bullet_prefix
        subsequent_indent = " " * continuation_indent
        chunks = textwrap.wrap(
            stripped or "",
            width=max(8, available),
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            break_long_words=True,
            break_on_hyphens=False,
            drop_whitespace=False,
            replace_whitespace=False,
        ) or [initial_indent.rstrip()]

        for index, chunk in enumerate(chunks):
            wrapped_lines.append(f"{prefix if not wrapped_lines else ' ' * len(prefix)}{chunk}")
    return "\n".join(wrapped_lines)


# Plan 86 Fix 3: package-mirror / docs / registry hosts that scroll past during installs
# and are NOT the app the user wants to open — never surface these as "captured links".
_INFRA_URL_HOSTS = (
    "ports.ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com", "deb.nodesource.com",
    "ubuntu.com", "launchpad.net", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org",
    "deb.debian.org", "packages.microsoft.com", "dl.google.com", "esm.ubuntu.com",
)


def _is_infra_url(url: str) -> bool:
    low = (url or "").lower()
    return any(host in low for host in _INFRA_URL_HOSTS)


def _extract_terminal_urls(chunk: str) -> tuple[str, ...]:
    """Extract complete URLs from raw terminal output before visual wrapping."""
    cleaned = _strip_ansi(str(chunk or "")).replace("\r", "")
    urls: list[str] = []
    for match in _TERMINAL_URL_PATTERN.finditer(cleaned):
        if match.end() == len(cleaned) and not cleaned.endswith((" ", "\t", "\n")):
            continue
        url = match.group(0).rstrip(".,;:")
        if url:
            urls.append(url)
    return tuple(urls)


def _read_os_clipboard_text() -> str:
    """Best-effort cross-platform clipboard read for terminal paste shortcuts."""
    system = platform.system()
    commands: tuple[tuple[str, ...], ...]
    if system == "Darwin":
        commands = (("pbpaste",),)
    elif system == "Windows":
        commands = (("powershell", "-NoProfile", "-Command", "Get-Clipboard"),)
    else:
        commands = (
            ("wl-paste", "--no-newline"),
            ("xclip", "-selection", "clipboard", "-out"),
            ("xsel", "--clipboard", "--output"),
        )
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=1.0)
        except Exception:
            continue
        if result.returncode == 0 and result.stdout:
            return result.stdout
    return ""


def _overlay_choice_label(choice: str, index: int | None = None) -> str:
    # Plan 191 F6: number the options (1/2/3…) to match the Accept-this-plan format;
    # callers that don't pass an index keep the legacy bullet.
    if index is None:
        return f"• {choice}"
    return f"{index + 1}  {choice}"


def _choice_activity_text(message: str, choices: tuple[str, ...]) -> str:
    normalized_choices = tuple(_strip_ansi(choice).strip().casefold() for choice in choices)
    is_yes_no = (
        len(normalized_choices) == 2
        and normalized_choices[0].startswith("yes")
        and normalized_choices[1].startswith("no")
    )
    first_line = next(
        (
            _strip_ansi(line).strip()
            for line in str(message or "").splitlines()
            if _strip_ansi(line).strip()
        ),
        "",
    )
    if not first_line:
        return "Awaiting your approval..." if is_yes_no else "Awaiting your choice..."
    if len(first_line) > 88:
        first_line = f"{first_line[:85].rstrip()}..."
    prefix = "Awaiting approval: " if is_yes_no else "Awaiting choice: "
    return f"{prefix}{first_line}"


def _approval_item_label(
    item,
    *,
    selected: bool,
) -> str:
    marker = "[x]" if selected else "[ ]"
    version = f" {item.version}" if getattr(item, "version", None) else ""
    url = getattr(item, "source_url", "") or "no source url"
    installer = getattr(item, "installer", "installer")
    return f"{marker} {item.dependency}{version} | {installer} | {url}"


def _resolve_overlay_choice(selected: str | None, *, choices: tuple[str, ...]) -> str | None:
    if selected is None:
        return None
    normalized = selected.strip()
    for choice in choices:
        if normalized == choice or normalized == _overlay_choice_label(choice):
            return choice
    if normalized.startswith("• "):
        return normalized[2:].strip()
    return normalized


def _strip_ansi(text: str) -> str:
    return _ANSI_PATTERN.sub("", str(text))


def _objective_chat_significance(key: str | None, text: str) -> str:
    """Return a key that only changes on major status transitions, not phase changes.

    Used to suppress repeated ▸ Objective: chat entries during repair sequences where
    the phase label updates frequently but the primary status stays the same."""
    t = (text or "").casefold()
    if "waiting on you" in t or "waiting approval" in t or "decide next step" in t:
        status = "decision"
    elif "failed" in t or "blocked" in t:
        status = "failed"
    elif "complete" in t or "verified" in t:
        status = "done"
    else:
        status = "active"
    return f"{key or ''}:{status}"


def _attachment_button_label(paths: tuple[str, ...]) -> str:
    count = len(paths)
    if count <= 0:
        return "📎"
    if count < 10:
        return f"📎{count}"
    return "📎+"


def _normalize_attachment_path(raw_value: str) -> str | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        resolved = Path(text).expanduser().resolve()
    except Exception:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return str(resolved)


_PRIMARY_SLASH_COMMANDS: tuple[str, ...] = (
    "/help",
    "/mode",
    "/provider",
    "/model",
    "/config",
    "/repos",
    "/explore",
    "/vm",
    "/cloud",
    "/internet",
    "/healthcheck",
    "/memory",
    "/remember",
    "/skills",
    "/tools",
    "/mcp",
    "/exit",
)


def _slash_command_suggestions(query: str) -> tuple[str, ...]:
    normalized = " ".join(str(query or "").strip().split()).casefold()
    if not normalized.startswith("/"):
        return ()
    if normalized == "/":
        # Bare slash — show the curated top-level palette directly, no sub-variants.
        return tuple(
            command for command in _PRIMARY_SLASH_COMMANDS if command in SLASH_COMMANDS
        )
    matches = [
        command
        for command in _SORTED_SLASH_COMMANDS
        if command.casefold().startswith(normalized)
    ]
    deduped: list[str] = []
    for match in matches:
        if match not in deduped:
            deduped.append(match)
    return tuple(deduped[:12])


def _slash_command_label(command: str) -> str:
    hint = _SLASH_COMMAND_HINTS.get(command, "Duckln command")
    return f"{command} — {hint}"


def _extract_slash_command_from_label(label: str) -> str:
    if " — " in label:
        return label.split(" — ", 1)[0].strip()
    return label.strip()


def _render_progress_bar(percent: int, *, width: int = 12) -> str:
    clamped = max(0, min(100, int(percent)))
    filled = int(round((clamped / 100) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


# Plan 119: bare progress "noise" from `ollama pull` — a standalone elapsed time
# ("24s"), a spinner glyph, or a bare size ("2.0 GB") — that the CLI re-prints on
# carriage returns. These must collapse INTO the single activity bar, never stack
# as separate chat lines.
_OLLAMA_NOISE_PATTERN = re.compile(
    r"^(?:[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏▕▏█▏▎▍▌▋▊▉\s]*"
    r"|\d+(?:\.\d+)?\s*(?:[kmgt]?b)(?:/\d+(?:\.\d+)?\s*[kmgt]?b)?"
    r"|\d+(?:\.\d+)?\s*[smh]"
    r")$",
    re.IGNORECASE,
)


def _is_ollama_pull_noise(message: str) -> bool:
    """True for a bare progress-refresh line (time/size/spinner) emitted by
    `ollama pull`, so the caller can swallow it instead of appending it."""
    plain = _strip_ansi(message).strip()
    if not plain:
        return False
    return bool(_OLLAMA_NOISE_PATTERN.match(plain))


def _transient_activity_update(message: str) -> tuple[str, bool] | None:
    plain = _strip_ansi(message).strip()
    lowered = plain.casefold()
    if not plain:
        return None
    if lowered.startswith("running `ollama pull "):
        return (plain, True)
    if lowered.startswith("checking local ollama runtime at "):
        return ("Checking local Ollama runtime…", True)
    # Match a percentage on ANY pull line first so we render the bar.
    progress_match = _OLLAMA_PULL_PROGRESS_PATTERN.match(plain)
    if progress_match is not None:
        _, percent_text, size_text = progress_match.groups()
        percent = int(percent_text)
        suffix = f" {size_text.strip()}" if size_text else ""
        return (f"Ollama pull {percent}% {_render_progress_bar(percent)}{suffix}", True)
    # Plan 119: broaden to STARTSWITH so trailing spinner/elapsed-time variants
    # ("pulling manifest ⠴ 24s") still collapse into the single activity line.
    if lowered.startswith("pulling manifest"):
        return ("Ollama pull — pulling manifest", True)
    # Plan 186 F1a: a bare `pulling` (no trailing content) matched none of the
    # startswith branches and leaked to chat — collapse it into the activity bar too.
    if lowered == "pulling" or lowered.startswith("pulling "):
        return ("Ollama pull — downloading…", True)
    if lowered.startswith("verifying"):
        return ("Ollama pull — verifying digest", True)
    if lowered.startswith("writing manifest"):
        return ("Ollama pull — writing manifest", True)
    if lowered.startswith("removing"):
        return ("Ollama pull — finalizing…", True)
    if lowered == "success":
        return ("Ollama pull complete", False)
    return None


def _extract_shell_command_outcome(details: str) -> tuple[str, str]:
    """Pull `Command:` and `Outcome:` out of a shell.command_runner trace details block.

    Trace renderer prefixes each detail with `<n>. ` (numbered list style), so strip
    that prefix before matching keys."""
    import re as _re

    cmd = ""
    outcome = ""
    for raw in str(details).splitlines():
        stripped = _re.sub(r"^\s*\d+\.\s*", "", raw.strip())
        if stripped.startswith("Command:") and not cmd:
            cmd = stripped[len("Command:"):].strip()
        elif stripped.startswith("Outcome:") and not outcome:
            outcome = stripped[len("Outcome:"):].strip()
    return cmd, outcome


def _parse_trace_message(message: str) -> tuple[str, str, str] | None:
    plain = _strip_ansi(message).strip()
    if not plain.startswith("Duckln trace: "):
        return None
    lines = [line.rstrip() for line in plain.splitlines()]
    if not lines:
        return None
    title_line = lines[0].strip()
    title = title_line.removeprefix("Duckln trace: ").rstrip(".").strip() or "trace"
    detail_lines = [line for line in lines[1:] if line.strip()]
    summary = detail_lines[0].strip() if detail_lines else "Details available."
    details = "\n".join(detail_lines).strip()
    return title, summary, details


def _trace_activity_update(title: str) -> tuple[str, bool]:
    normalized = _strip_ansi(title).strip().rstrip(".")
    lowered = normalized.casefold()
    spinner = not any(token in lowered for token in ("result", "failed", "ready", "verified", "complete"))
    prefix = "Thinking..." if any(token in lowered for token in ("lookup", "review", "classif", "planning")) else "Working..."
    return f"{prefix} {normalized}", spinner


def _is_repair_trace_title(title: str) -> bool:
    lowered = _strip_ansi(title).casefold()
    return any(
        token in lowered
        for token in (
            "terminal incident intake",
            "runtime repair planning",
            "runtime prerequisite review",
            "runtime dependency repair review",
            "runtime repair evidence",
            "runtime repair handoff",
            "post-prerequisite rerun",
            "post-dependency-repair rerun",
            "post-repair rerun",
        )
    )


def _is_repair_adjacent_trace_title(title: str) -> bool:
    lowered = _strip_ansi(title).casefold()
    return any(
        token in lowered
        for token in (
            "repo runtime review",
            "repo task materialization",
            "shell command execution",
            "shell command result",
            "web documentation lookup",
            "dependency install review",
            "docker env review",
        )
    )


def _repair_session_message_payload(message: str) -> tuple[str, str] | None:
    plain = _strip_ansi(message).strip()
    if not plain:
        return None
    lowered = plain.casefold()
    if lowered.startswith("duckln captured a live runtime blocker for "):
        return "Runtime blocker captured", plain
    if "duckln checked current official docs for this blocker" in lowered:
        return "Official docs checked", plain
    if lowered.startswith("duckln is continuing the bounded runtime repair path"):
        return "Repair resumed", plain
    if lowered.startswith("supervisor agent "):
        if any(
            token in lowered
            for token in (
                "paused before repairing",
                "paused before",
                "installed the missing prerequisite",
                "repaired the repo dependencies",
                "could not finish",
                "could not auto-run",
                "left ",
                "verified ",
                "starting ",
                "verifying ",
                "opened ",
            )
        ):
            summary = plain.split(". ", 1)[0].strip() or plain
            return summary, plain
    return None


def _repair_activity_update(summary: str, details: str) -> tuple[str, bool]:
    normalized_summary = _strip_ansi(summary).strip()
    normalized_details = _strip_ansi(details).strip()
    lowered = normalized_summary.casefold()
    if lowered.startswith("runtime blocker captured"):
        return "Working... triaging the runtime blocker", True
    if lowered.startswith("official docs checked") or "checked current official docs" in normalized_details.casefold():
        return "Thinking... checking official docs", True
    if lowered.startswith("repair resumed"):
        return "Working... continuing the repair", True
    if "re-check" in lowered or "recheck" in lowered:
        return "Working... re-checking after the repair step", True
    if any(token in lowered for token in ("could not", "failed", "paused", "blocked", "waiting")):
        return f"Paused... {normalized_summary}", False
    return f"Working... {normalized_summary}", True


def _terminal_incident_activity_update(incident: dict[str, object]) -> tuple[str, bool]:
    package_hint = str(incident.get("package_hint") or "").strip()
    tool_hint = str(incident.get("tool_hint") or "").strip()
    summary = str(incident.get("summary") or "").strip()
    lowered = _strip_ansi(summary).casefold()
    if any(token in lowered for token in ("permission denied", "eacces", "failed", "timed out", "blocked", "requires approval")):
        return "Paused... terminal blocker captured", False
    if package_hint:
        return f"Working... triaging the {package_hint} blocker", True
    if tool_hint:
        return f"Working... triaging the {tool_hint} blocker", True
    if summary:
        return "Working... triaging the runtime blocker", True
    return "Working... reviewing the terminal incident", True


def _format_activity_elapsed(started_at: float | None) -> str:
    if started_at is None:
        return ""
    elapsed = max(0, int(time.monotonic() - started_at))
    if elapsed < 60:
        return f"{elapsed}s"
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes}m {seconds}s"


# Plan 73 Phase B: the activity spinner must NOT animate forever. Past this
# ceiling we stop the spinner and show a static "still working or blocked"
# notice so the UI never appears hung.
_ACTIVITY_SLOW_SECONDS = 120
_ACTIVITY_HARD_CEILING_SECONDS = 600

# Plan 76 Fix A: lively, rotating processing words so an in-flight task never
# looks frozen. Cycled by the spinner index.
_PROCESSING_VERBS = ("Preparing", "Reasoning", "Drafting", "Reviewing", "Cooking", "Chanting", "Polishing")


def _processing_verb(spinner_index: int) -> str:
    return _PROCESSING_VERBS[spinner_index % len(_PROCESSING_VERBS)]


def _open_in_text_editor(path: str) -> bool:
    """Plan 183 F7: open a text/markdown file in the OS-native TEXT editor — TextEdit on macOS,
    a common GUI editor / xdg-open on Linux, Notepad on Windows — instead of whatever random app
    is registered for `.md`. Non-blocking; returns True when a launcher started."""
    import platform as _platform
    import shutil as _shutil
    import subprocess as _subproc

    sysname = _platform.system()
    candidates: list[list[str]] = []
    always: set[str] = set()
    if sysname == "Darwin":
        candidates = [["open", "-t", str(path)], ["open", "-a", "TextEdit", str(path)], ["open", str(path)]]
        always = {"open"}
    elif sysname == "Windows":
        candidates = [["notepad", str(path)]]
        always = {"notepad"}
    else:
        for editor in ("gnome-text-editor", "gedit", "kate", "mousepad", "xed"):
            if _shutil.which(editor):
                candidates.append([editor, str(path)])
                break
        candidates.append(["xdg-open", str(path)])
    for argv in candidates:
        if argv[0] not in always and _shutil.which(argv[0]) is None:
            continue
        try:
            _subproc.Popen(argv, stdout=_subproc.DEVNULL, stderr=_subproc.DEVNULL, stdin=_subproc.DEVNULL)
            return True
        except Exception:
            continue
    return False


# Plan 87 Fix 3: at the 0.1s spinner tick, advance the verb only every this many
# ticks so it dwells ~2.4s and stays readable instead of flickering 10×/second.
_VERB_DWELL_TICKS = 24

# Plan 87 Fix 4: minimum gap between revealed thoughts so a rapid burst stays
# readable; the rest queue and drain one at a time.
_THOUGHT_MIN_INTERVAL = 0.55

# Plan 89: after the thought stream goes idle this long, clear the generic
# "Duckln is working" keep-alive so the activity bar doesn't spin after a terminal state.
_THOUGHT_IDLE_CLEAR_SECONDS = 6.0


def _release_due_thoughts(
    pending: list[str],
    visible: list[str],
    last_release: float,
    now: float,
    *,
    min_interval: float = _THOUGHT_MIN_INTERVAL,
    max_visible: int = 40,
) -> tuple[list[str], list[str], float, bool]:
    """Plan 87 Fix 4: reveal at most one queued thought per `min_interval` so the
    "Duckln's thinking" box stays readable under a rapid burst. Pure — returns
    ``(pending, visible, last_release, changed)``; never drops a thought."""
    if not pending or (now - last_release) < min_interval:
        return pending, visible, last_release, False
    visible = [*visible, pending[0]]
    pending = pending[1:]
    if len(visible) > max_visible:
        visible = visible[-max_visible:]
    return pending, visible, now, True


def _format_token_count(value: int) -> str:
    """Plan 164 F4: compact token count — `7.4k` for ≥1000, the integer otherwise."""
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(int(value))


def _thoughts_token_suffix(total_tokens: int) -> str:
    """Plan 166/167: the live token suffix for the "Duckln's thinking" header —
    `" · 1.9k tokens (session)"` (cumulative session total, so it isn't misread as per-call;
    the per-call ↑sent/↓recv lives on the activity bar). "" when nothing's been counted yet."""
    if total_tokens <= 0:
        return ""
    return f" · {_format_token_count(total_tokens)} tokens (session)"


def _token_activity_segment(
    *, prompt_tokens: int = 0, completion_tokens: int = 0, context_window: int = 0,
    is_estimate: bool = False,
) -> str:
    """Plan 164 F4 / 167 F2: the compact live token readout appended to the activity bar —
    `↑7.4k ↓0.9k · ctx ~92%` (↑ sent, ↓ received, % of the model's context). A `⚠` is added
    at ≥90%. When `is_estimate` (no exact provider usage yet, e.g. a pending/failed call) the
    sent count is marked `↑~7.4k` so an estimate never reads as a measured value. Empty string
    when there's nothing to show yet."""
    parts: list[str] = []
    if prompt_tokens > 0:
        mark = "~" if is_estimate else ""
        parts.append(f"↑{mark}{_format_token_count(prompt_tokens)}")
    if completion_tokens > 0:
        parts.append(f"↓{_format_token_count(completion_tokens)}")
    if not parts:
        return ""
    segment = " ".join(parts)
    if context_window > 0 and prompt_tokens > 0:
        pct = int(round(100 * prompt_tokens / context_window))
        segment += f" · ctx ~{pct}%" + (" ⚠" if pct >= 90 else "")
    return segment


def _activity_bar_segments(
    *,
    activity_text: str,
    spinner_on: bool,
    elapsed_seconds: float | None,
    spinner_frame: str,
    spinner_index: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    context_window: int = 0,
    tokens_are_estimate: bool = False,
) -> tuple[str, str, bool]:
    """Pure decision for the activity bar: returns ``(prefix, body, spinner_on)``.

    While a task is actively spinning, a rotating processing word is appended
    (Preparing / Reasoning / Drafting …) so the UI feels alive. Past the hard
    ceiling the spinner is forced OFF and a static blocked-notice replaces the
    elapsed suffix, so a stalled task never spins indefinitely.

    Plan 164 F4: when token counts are known, a compact `↑sent ↓recv · ctx ~X%` readout is
    appended so the user sees how many tokens each call sends and how full the context is."""
    if not activity_text:
        return "", "", False
    token_segment = _token_activity_segment(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, context_window=context_window,
        is_estimate=tokens_are_estimate,
    )
    over_ceiling = (
        elapsed_seconds is not None
        and elapsed_seconds >= _ACTIVITY_HARD_CEILING_SECONDS
    )
    if over_ceiling:
        body = (
            f"{activity_text} • still working or blocked — check `/repos status`; "
            "this will report when it finishes or hits a blocker."
        )
        if token_segment:
            body += f" • {token_segment}"
        return "• ", body, False
    prefix = f"{spinner_frame} " if spinner_on else "• "
    suffix = ""
    if spinner_on:
        suffix += f" • {_processing_verb(spinner_index)}…"
    if elapsed_seconds is not None:
        mins, secs = divmod(int(max(0, elapsed_seconds)), 60)
        suffix += f" • {secs}s" if mins == 0 else f" • {mins}m {secs}s"
    if token_segment:
        suffix += f" • {token_segment}"
    if spinner_on and elapsed_seconds is not None and elapsed_seconds >= _ACTIVITY_SLOW_SECONDS:
        suffix += " • taking longer than expected"
    return prefix, f"{activity_text}{suffix}", spinner_on


def _status_payload_from_box(message: str) -> tuple[str, str]:
    plain_lines = [_strip_ansi(line).strip() for line in str(message).splitlines()]
    title = "Status"
    if plain_lines:
        top = plain_lines[0]
        if top.startswith("┌") and top.endswith("┐"):
            title_candidate = top.strip("┌┐─ ").strip()
            if title_candidate:
                title = title_candidate
    for line in plain_lines:
        if not line or line.startswith("┌") or line.startswith("└") or line == "│":
            continue
        if line.startswith("│") and line.endswith("│"):
            candidate = line.strip("│ ").strip()
            if candidate:
                return title, candidate
    return title, _strip_ansi(message).strip()


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("- ", "* ", "• ")) or re.match(r"^\d+\.\s+", stripped):
        return False
    if any(character in stripped for character in ".:;!?"):
        return False
    return len(stripped) <= 48 and len(stripped.split()) <= 5

TEXTUAL_SPLIT_PANE_CSS = """
Screen {
    layout: vertical;
}

#status-bar {
    height: 1;
    content-align: left middle;
    text-overflow: ellipsis;
}

#main-area {
    height: 1fr;
    layout: horizontal;
}

.pane {
    min-width: 30;
}

#terminal-pane {
    min-width: 40;
    overflow-y: auto;
}

.pane-header {
    height: 1;
    border-bottom: solid #3a3a3a;
    background: #181818;
    content-align: left middle;
}

.pane-header.-focused {
    border-bottom: solid #F5A623;
}

.pane-header-title {
    width: 1fr;
}

#terminal-collapse {
    width: 3;
    content-align: center middle;
}

#divider {
    width: 1;
    height: 1fr;
    background: #3a3a3a;
    color: #3a3a3a;
    content-align: center middle;
}

#divider.-hover {
    background: #F5A623;
}

#separator {
    height: 1;
}

#chat-scroll {
    height: 1fr;
}

#choice-overlay {
    display: none;
    height: auto;
    max-height: 22;
    border: tall #F5A623;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#approval-overlay {
    display: none;
    height: auto;
    max-height: 18;
    border: tall #F5A623;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#text-overlay {
    display: none;
    height: auto;
    max-height: 18;
    border: tall #F5A623;
    margin: 0 0 1 0;
    padding: 1 2;
    background: #181818;
}

#attach-overlay {
    display: none;
    height: auto;
    max-height: 10;
    border: tall #F5A623;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#attach-prompt {
    height: auto;
    color: #F5A623;
    padding: 0 0 1 0;
}

#attach-list {
    height: auto;
    max-height: 6;
}

#text-prompt {
    height: auto;
    color: #F5A623;
    padding: 0 0 1 0;
}

#text-help {
    height: auto;
    color: #C8C8C8;
    padding: 0 0 1 0;
}

#text-overlay-input {
    width: 1fr;
    height: 3;
    border: round #00FFFF;
    background: #111111;
    color: #F0EAD6;
    margin: 1 0 0 0;
    padding: 0 2;
}

#approval-prompt {
    height: auto;
    color: #F0EAD6;
    padding: 0 0 1 0;
}

#approval-help {
    height: auto;
    color: #616161;
    padding: 0 0 1 0;
}

#approval-list {
    height: auto;
    max-height: 7;
    border: none;
    padding: 0;
}

#approval-actions {
    height: auto;
    layout: horizontal;
    margin: 1 0 0 0;
}

.approval-button {
    width: auto;
    min-width: 16;
    margin: 0 1 0 0;
    padding: 0 1;
    border: round #3a3a3a;
    content-align: center middle;
}

.approval-button.-primary {
    border: round #F5A623;
    color: #F5A623;
}

#choice-prompt {
    height: auto;
    color: #F0EAD6;
    text-style: bold;
    padding: 0 0 1 0;
}

#choice-list {
    height: auto;
    max-height: 14;
    border: none;
    padding: 0;
}

/* Plan 191 F6: inline "Other — type your own" row + "Esc to cancel" footer. */
#choice-other {
    display: none;
    height: auto;
    margin: 1 0 0 0;
    border: round #3a3a3a;
    background: #101010;
}

#choice-hint {
    height: auto;
    color: #808080;
    padding: 1 0 0 0;
}

#slash-overlay {
    display: none;
    height: auto;
    max-height: 22;
    border: tall #3a3a3a;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#slash-prompt {
    height: auto;
    color: #A9A9A9;
    padding: 0 0 1 0;
}

#slash-list {
    height: auto;
    max-height: 18;
    border: none;
    padding: 0;
}

#mention-overlay {
    display: none;
    height: auto;
    max-height: 22;
    border: tall #3a3a3a;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#mention-prompt {
    height: auto;
    color: #A9A9A9;
    padding: 0 0 1 0;
}

#mention-list {
    height: auto;
    max-height: 18;
    border: none;
    padding: 0;
}

#activity-bar {
    height: 1;
    content-align: left middle;
    color: #A9A9A9;
    padding: 0 1;
}

#objective-bar {
    height: 1;
    content-align: left middle;
    color: #C8C8C8;
    padding: 0 1;
}

#trace-panel {
    display: none;
    height: auto;
    border: tall #3a3a3a;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#trace-title {
    height: auto;
    color: #A9A9A9;
    padding: 0 0 1 0;
}

#trace-summary {
    height: auto;
    color: #F0EAD6;
    padding: 0 0 1 0;
}

#trace-actions {
    height: auto;
    layout: horizontal;
    margin: 0 0 1 0;
}

.trace-button {
    width: auto;
    min-width: 14;
    margin: 0 1 0 0;
    padding: 0 1;
    border: round #3a3a3a;
    content-align: center middle;
}

.trace-button.-primary {
    border: round #F5A623;
    color: #F5A623;
}

#trace-body {
    display: none;
    height: auto;
    max-height: 8;
    color: #C8C8C8;
}

#thoughts-panel {
    display: none;
    height: auto;
    border: round #3a3a3a;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #161616;
}

#thoughts-toggle {
    height: auto;
    color: #8FB8DE;
}

#thoughts-body {
    display: none;
    height: auto;
    max-height: 10;
    color: #A9A9A9;
    padding: 1 0 0 0;
}

#repair-panel {
    display: none;
    height: auto;
    border: tall #F5A623;
    margin: 0 0 1 0;
    padding: 0 1;
    background: #181818;
}

#repair-title {
    height: auto;
    color: #F5A623;
    padding: 0 0 1 0;
}

#repair-summary {
    height: auto;
    color: #F0EAD6;
    padding: 0 0 1 0;
}

#repair-actions {
    height: auto;
    layout: horizontal;
    margin: 0 0 1 0;
}

.repair-button {
    width: auto;
    min-width: 14;
    margin: 0 1 0 0;
    padding: 0 1;
    border: round #3a3a3a;
    content-align: center middle;
}

.repair-button.-primary {
    border: round #F5A623;
    color: #F5A623;
}

#repair-body {
    display: none;
    height: auto;
    max-height: 10;
    color: #C8C8C8;
}

#terminal-shell {
    width: 1fr;
    height: 1fr;
    overflow-y: auto;
}

#terminal-frame {
    height: 1fr;
    layout: horizontal;
}

#terminal-scroll-indicator {
    width: 1;
    height: 1fr;
    color: #3a3a3a;
    background: #111111;
    content-align: center middle;
}

#terminal-scroll-indicator.-active {
    color: #00FFFF;
}

#preview-pane {
    display: none;
    height: 1fr;
    layout: vertical;
    border-top: solid #2a2a2a;
}

#preview-header {
    height: 1;
    background: #171717;
    content-align: left middle;
}

#preview-title {
    width: 1fr;
    color: #00FFFF;
}

.preview-button {
    width: 3;
    content-align: center middle;
}

#preview-body {
    height: 1fr;
    overflow-y: auto;
    padding: 0 1;
    color: #F0EAD6;
}

#input-bar {
    height: 3;
    layout: horizontal;
}

#attach-button {
    width: 4;
    content-align: center middle;
    color: #616161;
}

#attach-button.-active {
    color: #F5A623;
}

#input-prompt {
    width: 3;
    content-align: center middle;
}

#chat-input {
    width: 1fr;
}

#footer {
    height: 1;
}
"""


@dataclass(frozen=True)
class TerminalBackendSelection:
    """Detected PTY backend for the split-pane terminal widget."""

    os_name: str
    backend_package: str | None
    backend_available: bool
    error_message: str | None = None


@dataclass(frozen=True)
class ConnectionContextLabel:
    """Resolved terminal-pane header label and color."""

    label: str
    color: str


@dataclass(frozen=True)
class WebPreviewSnapshot:
    """Bounded preview data rendered inside Duckln's right pane."""

    requested_url: str
    final_url: str
    ok: bool
    status_code: int | None
    title: str | None
    summary: str
    excerpt_lines: tuple[str, ...] = ()
    content_type: str | None = None
    error_message: str | None = None


def fetch_web_preview_snapshot(url: str, *, timeout_seconds: float = 5.0) -> WebPreviewSnapshot:
    """Fetch a bounded preview for a URL Duckln wants to surface in-pane."""

    requested = url.strip()
    try:
        response = httpx.get(requested, follow_redirects=True, timeout=timeout_seconds)
    except Exception as exc:  # pragma: no cover - exercised through runtime integration
        return WebPreviewSnapshot(
            requested_url=requested,
            final_url=requested,
            ok=False,
            status_code=None,
            title=None,
            summary="Duckln could not load the web preview from this session.",
            excerpt_lines=(),
            content_type=None,
            error_message=str(exc),
        )

    content_type = response.headers.get("content-type", "")
    text = response.text if isinstance(response.text, str) else ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = _normalize_preview_text(title_match.group(1)) if title_match is not None else None
    body_text = _extract_html_preview_text(text) if "html" in content_type.lower() else _normalize_preview_text(text)
    excerpt = tuple(line for line in body_text.splitlines() if line.strip())[:12]
    summary = "Duckln loaded the page preview successfully." if response.is_success else "Duckln reached the page but the preview returned a non-success HTTP status."
    return WebPreviewSnapshot(
        requested_url=requested,
        final_url=str(response.url),
        ok=bool(response.is_success),
        status_code=response.status_code,
        title=title,
        summary=summary,
        excerpt_lines=excerpt,
        content_type=content_type or None,
        error_message=None if response.is_success else f"HTTP {response.status_code}",
    )


def _normalize_preview_text(value: str) -> str:
    plain = re.sub(r"\s+", " ", unescape(value or "")).strip()
    return plain


def _plan_mode_header_segment(config_dir):
    """Plan 67: return ``(glyph, style, label)`` for the Plan Mode header
    indicator, or None only when the config is unavailable.

    Reads the live AppConfig (no caching) so toggling `/plan on` is visible
    on the very next render. The style is:
    - green ``Plan: on`` when Plan Mode is on and there is no ACTIONABLE plan
      pending (this includes terminal states like failed/completed/rejected —
      they are not actionable, so the header does not get stuck showing them)
    - orange ``Plan: <status>`` only when a plan genuinely awaits the user
      (pending / amended / approved / edited)

    Plan 188: Plan Mode is always on, so the chip is always ``Plan: on`` (or a
    pending status) — there is no ``off`` state.
    """
    if config_dir is None:
        return None
    try:
        from duckln.config import resolve_config_paths, load_app_config

        cfg = load_app_config(resolve_config_paths({"DUCKLN_CONFIG_DIR": str(config_dir)}))
    except Exception:
        return None
    if cfg is None:
        return None
    try:
        from duckln.glyphs import PLAN_NOTE
    except Exception:
        return None
    try:
        from state.access import read_pending_plan

        pending = read_pending_plan(config_dir)
    except Exception:
        pending = None
    _actionable = {"pending", "amended", "approved", "edited"}
    if pending is None or str(pending.get("status") or "") not in _actionable:
        # No actionable plan — show the steady "on" state, never a stuck
        # "failed"/terminal status from a prior run.
        return PLAN_NOTE, "green", "Plan: on"
    status = str(pending.get("status"))
    return PLAN_NOTE, "orange1", f"Plan: {status}"


def _extract_html_preview_text(html: str) -> str:
    scrubbed = re.sub(r"<script\b.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    scrubbed = re.sub(r"<style\b.*?</style>", " ", scrubbed, flags=re.IGNORECASE | re.DOTALL)
    scrubbed = re.sub(r"<[^>]+>", "\n", scrubbed)
    plain = unescape(scrubbed)
    lines = [_normalize_preview_text(line) for line in plain.splitlines()]
    filtered = [line for line in lines if line and len(line) > 2]
    return "\n".join(filtered[:24])


_DUCKLN_SENTINEL_PREFIX = "DUCKLN-DONE-"

# Plan 198 F1: a message that is EXACTLY one of these interrupts a running operation (matched on
# the whole, stripped, lowercased message so it never hijacks "stop the server" etc.).
_STOP_WORDS = frozenset({"stop", "cancel", "abort", "halt", "stop it", "cancel it", "stop!", "quit that"})


class PaneSentinelWaiter:
    """Capture lines from a pane stream until a `DUCKLN-DONE-<id>:<rc>` marker arrives.

    Item 1: lets `ControlledCommandRunner` route `multipass exec` (and any other VM-target
    command) into the embedded terminal pane instead of a hidden subprocess. The runner
    appends the marker via `; printf 'DUCKLN-DONE-<id>:%d\\n' $?` so we can capture
    chronological output and exit code from the user's visible shell session.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waits: dict[str, threading.Event] = {}
        self._captures: dict[str, list[str]] = {}
        self._exit_codes: dict[str, int] = {}
        self._sentinel_pattern = re.compile(rf"{_DUCKLN_SENTINEL_PREFIX}([a-zA-Z0-9_-]+):(-?\d+)")
        self._ansi_pattern = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

    def begin(self, sentinel_id: str) -> None:
        with self._lock:
            self._waits[sentinel_id] = threading.Event()
            self._captures[sentinel_id] = []
            self._exit_codes.pop(sentinel_id, None)

    def submit_line(self, line: str) -> None:
        clean = self._ansi_pattern.sub("", line).rstrip()
        match = self._sentinel_pattern.search(clean)
        if match is not None:
            sentinel_id = match.group(1)
            try:
                exit_code = int(match.group(2))
            except ValueError:
                exit_code = -1
            with self._lock:
                event = self._waits.get(sentinel_id)
                if event is not None and not event.is_set():
                    self._exit_codes[sentinel_id] = exit_code
                    event.set()
            return
        with self._lock:
            for sentinel_id, event in self._waits.items():
                if event.is_set():
                    continue
                self._captures[sentinel_id].append(clean)

    def wait(self, sentinel_id: str, *, timeout: float) -> tuple[bool, list[str], int]:
        with self._lock:
            event = self._waits.get(sentinel_id)
        if event is None:
            return False, [], -1
        timed_out = not event.wait(timeout=timeout)
        with self._lock:
            captured = list(self._captures.pop(sentinel_id, []))
            exit_code = self._exit_codes.pop(sentinel_id, -1)
            self._waits.pop(sentinel_id, None)
        return (not timed_out), captured, exit_code


class MLTerminalWatcher:
    """Background watcher that turns interesting terminal output into bounded proactive observations."""

    def __init__(
        self,
        callback: Callable[[str], None],
        incident_callback: Callable[[dict[str, object]], None] | None = None,
        sentinel_waiter: "PaneSentinelWaiter | None" = None,
    ) -> None:
        self._callback = callback
        self._incident_callback = incident_callback
        self._sentinel_waiter = sentinel_waiter
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._stop = threading.Event()
        self._buffer = ""
        self._recent_observations: dict[str, float] = {}
        self._recent_incidents: dict[str, float] = {}
        self._recent_lines: deque[str] = deque(maxlen=16)
        self._thread.start()

    def submit(self, chunk: str) -> None:
        if chunk:
            self._queue.put(chunk)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        self._thread.join(timeout=1)

    def analyse_output(self, line: str) -> tuple[str | None, dict[str, object] | None]:
        lower = line.strip().lower()
        if not lower:
            return None, None
        prompt_window = "\n".join(tuple(self._recent_lines) + (line.strip(),)).lower()
        if (
            "continue?" in prompt_window
            and re.search(r"\b(?:yes|y)\b.*\b(?:no|n)\b", prompt_window, flags=re.DOTALL)
        ):
            summary = "The app running in the terminal is waiting for user input: Continue? Yes/No."
            return "Duckln noticed the app is waiting for input in the terminal pane.", {
                "category": "app_prompt_active",
                "summary": summary,
                "fatal_line": "Continue? Yes/No",
                "package_hint": None,
                "tool_hint": None,
                "relevant_lines": tuple(self._recent_lines)[-4:] + (line.strip(),),
            }
        if any(
            token in lower
            for token in (
                "traceback",
                "error:",
                "exception",
                "command not found",
                "not found, but can be installed",
                "no module named",
                "failed",
                "errno",
                "file not found",
                "no such file or directory",
            )
        ) or re.search(r"\bcommand\s+['\"][^'\"]+['\"]\s+not found\b", lower):
            incident = summarize_failure_incident(
                command=None,
                stderr="\n".join(tuple(self._recent_lines) + (line.strip(),)),
            )
            observation = "Duckln noticed an error in the terminal output and can help inspect the blocker."
            return observation, {
                "category": incident.category,
                "summary": incident.summary,
                "fatal_line": incident.fatal_line,
                "package_hint": incident.package_hint,
                "tool_hint": incident.tool_hint,
                "relevant_lines": incident.relevant_lines,
            }
        if "whisper" in lower and ("usage:" in lower or "--help" in lower or "transcrib" in lower):
            return "Duckln noticed Whisper output in the terminal pane.", None
        if "yolo" in lower or "ultralytics" in lower:
            return "Duckln noticed YOLO-style output in the terminal pane.", None
        return None, None

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:
                return
            self._buffer += chunk
            lines = self._buffer.splitlines(keepends=False)
            if self._buffer and not self._buffer.endswith(("\n", "\r")):
                self._buffer = lines.pop() if lines else self._buffer
            else:
                self._buffer = ""
            for line in lines:
                if self._sentinel_waiter is not None:
                    self._sentinel_waiter.submit_line(line)
                normalized = line.strip()
                if normalized:
                    self._recent_lines.append(normalized)
                observation, incident = self.analyse_output(line)
                now = time.monotonic()
                incident_is_duplicate = False
                if incident is not None:
                    incident_fingerprint = f"{incident.get('category')}|{incident.get('summary')}"
                    last_incident = self._recent_incidents.get(incident_fingerprint, 0.0)
                    if now - last_incident < 30.0:
                        incident_is_duplicate = True
                    else:
                        self._recent_incidents[incident_fingerprint] = now
                if observation is not None:
                    last_seen = self._recent_observations.get(observation, 0.0)
                    window_seconds = 30.0 if incident is not None else 8.0
                    if not incident_is_duplicate and now - last_seen >= window_seconds:
                        self._recent_observations[observation] = now
                        self._callback(observation)
                if incident is None or self._incident_callback is None or incident_is_duplicate:
                    continue
                self._incident_callback(incident)


def default_shell_command(system_name: str | None = None) -> str:
    """Return a platform-appropriate interactive shell command."""

    os_name = system_name or platform.system()
    if os_name == "Windows":
        return os.environ.get("COMSPEC", "cmd.exe")
    return f"{os.environ.get('SHELL', '/bin/zsh')} -l"


_TERMINAL_RESET_SEQUENCE = (
    "\x1b[?1006l"
    "\x1b[?1015l"
    "\x1b[?1003l"
    "\x1b[?1002l"
    "\x1b[?1000l"
    "\x1b[?1004l"
    "\x1b[?2004l"
    "\x1b[?1049l"
    "\x1b[?25h"
)


def _reset_terminal_state() -> None:
    """Disable any leftover mouse tracking, leave alt screen, and show the cursor.

    Defensive cleanup so a partially initialized Textual app cannot leave the
    outer terminal in a state where SGR mouse moves echo as visible text."""
    try:
        stream = sys.__stdout__
        if stream is None or not stream.isatty():
            return
        stream.write(_TERMINAL_RESET_SEQUENCE)
        stream.flush()
    except Exception:
        pass


def local_connection_label() -> str:
    """Return a compact local-machine label for the terminal pane."""

    system_name = platform.system()
    machine = platform.machine().strip() or "unknown"
    if system_name == "Darwin":
        family = "macOS"
    elif system_name == "Windows":
        family = "Windows"
    else:
        family = system_name
    return f"{family} {machine}"


def detect_terminal_backend(system_name: str | None = None) -> TerminalBackendSelection:
    """Choose the correct PTY backend for the current operating system."""

    os_name = system_name or platform.system()
    if os_name in {"Darwin", "Linux"}:
        backend = "ptyprocess"
    elif os_name == "Windows":
        backend = "pywinpty"
    else:
        return TerminalBackendSelection(
            os_name=os_name,
            backend_package=None,
            backend_available=False,
            error_message=f"Duckln does not yet have a supported embedded terminal backend for {os_name}.",
        )
    available = importlib_util.find_spec(backend) is not None
    return TerminalBackendSelection(
        os_name=os_name,
        backend_package=backend,
        backend_available=available,
        error_message=None if available else f"Duckln could not import the {backend} backend required for the split terminal pane.",
    )


def split_pane_dependencies_available(system_name: str | None = None) -> bool:
    """Return whether the optional split-pane dependency stack is importable."""

    required = ("textual", "textual_terminal", "pyte")
    if any(importlib_util.find_spec(name) is None for name in required):
        return False
    return detect_terminal_backend(system_name).backend_available


def split_pane_unavailability_message(system_name: str | None = None) -> str:
    """Return a concise user-facing explanation when split-pane support is unavailable."""

    backend = detect_terminal_backend(system_name)
    missing: list[str] = []
    for package_name in ("textual", "textual_terminal", "pyte"):
        if importlib_util.find_spec(package_name) is None:
            missing.append(package_name)
    if not backend.backend_available and backend.backend_package:
        missing.append(backend.backend_package)
    if not missing:
        return "Duckln split-pane support is available."
    package_text = ", ".join(dict.fromkeys(missing))
    return f"Duckln could not start the split-pane terminal UI because these dependencies are missing: {package_text}."


def build_connection_context_label(
    *,
    connection_type: str,
    local_label: str = "macOS M2",
    vm_name: str | None = None,
    cloud_vendor: str | None = None,
    cloud_region: str | None = None,
    cloud_shape: str | None = None,
    docker_name: str | None = None,
) -> ConnectionContextLabel:
    """Build the right-pane header label and color from the active execution target."""

    # Plan 110 Fix 4: composite `location-resource` heading so the user always knows
    # WHERE things run — a multipass VM is locally hosted (`local-<vm>`), Docker is
    # `local-<name>`, cloud is `<provider>-<name>`.
    normalized = connection_type.strip().lower()
    if normalized == "local":
        return ConnectionContextLabel(label="local", color=LOCAL_GREEN)
    if normalized == "vm":
        label = f"local-{vm_name}" if vm_name else "local-vm"
        return ConnectionContextLabel(label=label, color=VM_GOLD)
    if normalized == "aws":
        name = (cloud_shape or cloud_region or "").strip()
        return ConnectionContextLabel(label=f"aws-{name}" if name else "aws", color=AWS_ORANGE)
    if normalized == "gcp":
        name = (cloud_shape or cloud_region or "").strip()
        return ConnectionContextLabel(label=f"gcp-{name}" if name else "gcp", color=GCP_BLUE)
    if normalized == "docker" or normalized == "container":
        return ConnectionContextLabel(label=f"local-{docker_name}" if docker_name else "local-container", color=DOCKER_CYAN)
    return ConnectionContextLabel(label="Duckln Terminal", color=UNFOCUSED_BORDER)


_UBUNTU_VM_PROMPT_PATTERN = re.compile(r"\bubuntu@[A-Za-z0-9_.-]+(?::[^#$\r\n]*)?[#$]\s*$")

# Plan 135 F5: a shell PROMPT at the end of the output = the pane is idle (a command
# finished). Matches `user@host:~$ `, `(base) user@Mac %`, `… #`, plus a DUCKLN-DONE
# marker (Duckln-wrapped commands). Used to drain the terminal-busy queue.
_SHELL_PROMPT_PATTERN = re.compile(r"[A-Za-z0-9_.\-~/)\]]\s*[#$%]\s*$")


def _looks_like_shell_prompt(chunk: str) -> bool:
    cleaned = _strip_ansi(str(chunk or ""))
    if "DUCKLN-DONE-" in cleaned:
        return True
    for line in reversed(cleaned.splitlines()):
        if line.strip():
            return bool(_SHELL_PROMPT_PATTERN.search(line))
    return False


def infer_terminal_connection_from_output(chunk: str) -> str | None:
    """Infer the live terminal attachment from visible shell output."""

    cleaned = _strip_ansi(str(chunk or ""))
    if "Welcome to Ubuntu" in cleaned or _UBUNTU_VM_PROMPT_PATTERN.search(cleaned):
        return "vm"
    return None


try:  # pragma: no cover - exercised only when optional UI deps are installed
    from rich.text import Text
    import textual.app as textual_app_module
    from textual import driver as textual_driver_module
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.css.query import NoMatches
    from textual.design import ColorSystem
    from textual.events import Key, MouseDown, MouseMove, MouseUp, Resize
    from textual.geometry import Size
    from textual.reactive import reactive
    from textual.widgets import Input, OptionList, RichLog, Static

    if not hasattr(textual_app_module, "DEFAULT_COLORS"):
        textual_app_module.DEFAULT_COLORS = {
            "dark": ColorSystem(
                primary=DUCKLN_GOLD,
                foreground="#F0EAD6",
                background="#141414",
                surface="#1D1D1D",
                panel="#1D1D1D",
                dark=True,
            ),
            "light": ColorSystem(
                primary=DUCKLN_GOLD,
                foreground="#141414",
                background="#F7F4ED",
                surface="#FFFFFF",
                panel="#FFFFFF",
                dark=False,
            ),
        }

    from textual.drivers.linux_driver import LinuxDriver, WriterThread
    from textual_terminal import Terminal
    from textual_terminal._terminal import DECSET_PREFIX, TerminalDisplay, _re_ansi_sequence
except Exception:  # pragma: no cover - safe fallback when optional deps are absent
    App = None
    ComposeResult = object
    Horizontal = None
    Vertical = None
    Input = None
    MouseDown = None
    MouseMove = None
    MouseUp = None
    Resize = None
    RichLog = None
    Terminal = None
    LinuxDriver = None
    reactive = None
    Static = None
    SplitPaneChatInterface = None


if App is not None and Terminal is not None and RichLog is not None and reactive is not None:  # pragma: no cover - optional runtime path
    def _append_inline_styled(text: Text, content: str, *, base_color: str) -> None:
        cursor = 0
        for match in _INLINE_TOKEN_PATTERN.finditer(content):
            if match.start() > cursor:
                text.append(content[cursor : match.start()], style=base_color)
            token = match.group(0)
            if token.startswith("http://") or token.startswith("https://"):
                text.append(token, style=f"bold underline {DUCKLN_CYAN} link {token}")
            elif token.startswith("[") and token.endswith("]"):
                text.append(token, style=f"bold {DUCKLN_CYAN}")
            else:
                text.append(token, style=f"bold {DUCKLN_CYAN}")
            cursor = match.end()
        if cursor < len(content):
            text.append(content[cursor:], style=base_color)


    def _append_structured_line(text: Text, body: str, *, base_color: str, highlight_inline: bool = True) -> None:
        stripped = body.strip()
        if _looks_like_heading(body):
            text.append(body, style=f"bold {base_color}")
            return
        bullet_match = re.match(r"^(\s*)([-*]\s+)(.+)$", body)
        if bullet_match is not None:
            indent, marker, remainder = bullet_match.groups()
            if indent:
                text.append(indent, style=base_color)
            text.append(marker, style=f"bold {DUCKLN_CYAN}")
            if highlight_inline:
                _append_inline_styled(text, remainder, base_color=base_color)
            else:
                text.append(remainder, style=base_color)
            return
        numbered_match = re.match(r"^(\s*)(\d+\.\s+)(.+)$", body)
        if numbered_match is not None:
            indent, marker, remainder = numbered_match.groups()
            if indent:
                text.append(indent, style=base_color)
            text.append(marker, style=f"bold {DUCKLN_CYAN}")
            if highlight_inline:
                _append_inline_styled(text, remainder, base_color=base_color)
            else:
                text.append(remainder, style=base_color)
            return
        label_match = re.match(r"^(\s*)([A-Za-z][A-Za-z0-9 /_-]{1,28}:)\s+(.+)$", body)
        if label_match is not None and stripped.count(":") == 1:
            indent, label, remainder = label_match.groups()
            if indent:
                text.append(indent, style=base_color)
            text.append(label, style=f"bold {DUCKLN_SYSTEM}")
            text.append(" ", style=base_color)
            if highlight_inline:
                _append_inline_styled(text, remainder, base_color=base_color)
            else:
                text.append(remainder, style=base_color)
            return
        if highlight_inline:
            _append_inline_styled(text, body, base_color=base_color)
        else:
            text.append(body, style=base_color)


    def _render_wrapped_role_lines(
        message: str,
        *,
        prefix: str,
        prefix_color: str,
        body_color: str,
        width: int,
        highlight_inline: bool = True,
    ) -> list[Text]:
        wrapped = _wrap_chat_message(_strip_ansi(message), prefix=prefix, width=width)
        indent = " " * len(prefix)
        lines: list[Text] = []
        for raw_line in wrapped.splitlines():
            line = Text()
            if raw_line.startswith(prefix):
                lead = prefix
                body = raw_line[len(prefix) :]
                line.append(lead, style=f"bold {prefix_color}")
            elif raw_line.startswith(indent):
                lead = indent
                body = raw_line[len(indent) :]
                line.append(lead, style=body_color)
            else:
                body = raw_line
            _append_structured_line(line, body, base_color=body_color, highlight_inline=highlight_inline)
            lines.append(line)
        return lines or [Text("")]


    def _message_dot_color(text: str) -> str | None:
        """Plan 133 F5: recolor the EXISTING message dot — RED on a failure/interrupt
        line, GREEN on a done/✅ line — without changing the glyph. Returns a colour
        override or None (keep the role's normal colour)."""
        head = _strip_ansi(text or "").lstrip()
        low = head.casefold()
        if (
            head.startswith(("✗", "✖", "❌"))
            or "plan halted" in low or "plan paused" in low
            or "tool interrupted" in low or "interrupted" in low
            or "no usable fix" in low or "did not return a usable" in low
            or "failed (exit_code" in low
        ):
            return "red"
        if head.startswith(("✅", "☑", "✓")):
            return "green"
        return None


    def _reasoning_link_renderable(text: str):
        """Plan 138 F3: when the message is the `🧠 Reasoning captured → … file://…` line,
        build a Rich Text with a clickable hyperlink on the filename so clicking OPENS
        logical-thinking.md (OSC-8; works in the VSCode terminal + modern emulators).
        Returns a Text renderable, or None when the line isn't a reasoning link."""
        plain = _strip_ansi(text or "")
        if "Reasoning captured" not in plain:
            return None
        m = re.search(r"file://(\S+?\.md)", plain)
        if not m:
            return None
        url = "file://" + m.group(1)
        try:
            t = Text("🧠 Reasoning captured → ", style=DUCKLN_GOLD)
            t.append("logical-thinking.md", style=f"underline {DUCKLN_CYAN} link {url}")
            t.append(" (click to open)", style=DUCKLN_GREY)
            return t
        except Exception:
            return None

    def _is_activity_halting_message(text: str) -> bool:
        """Plan 135 F7: True when a message marks that Duckln has STOPPED working —
        a pause for the user, an honest halt, or a finished plan. The "Working… •
        taking longer than expected" spinner must be cleared on these so it never keeps
        spinning after Duckln is done/waiting."""
        low = _strip_ansi(text or "").casefold()
        return any(s in low for s in (
            "plan paused", "plan halted", "plan amended", "paused for an amendment",
            "run `/plan continue`", "run `/plan approve`", "run `/plan reject`",
            "your repo is ready", "plan executed", "plan completed", "plan failed",
        ))


    def _render_status_lines(message: str, *, width: int) -> list[Text]:
        title, body = _status_payload_from_box(message)
        lines = [
            Text.assemble(
                ("▍ ", f"bold {DUCKLN_GOLD}"),
                (title, f"bold {DUCKLN_GOLD}"),
            )
        ]
        lines.extend(
            _render_wrapped_role_lines(
                body,
                prefix="  ",
                prefix_color=DUCKLN_GREY,
                body_color=DUCKLN_EGGSHELL,
                width=width,
            )
        )
        return lines


    def _render_status_bar(status_text) -> Text:
        # Plan 63 Fix 1: callers can pass either a plain `str` (legacy) or a
        # pre-built Rich `Text` (used by _status_text now that the header
        # includes coloured status dots). Pass Text through unchanged so the
        # styles survive — splitting on "•" and rebuilding would strip them.
        if isinstance(status_text, Text):
            return status_text
        parts = [part.strip() for part in _strip_ansi(status_text).split("•") if part.strip()]
        if not parts:
            return Text("Duckln", style=f"bold {DUCKLN_GOLD}")
        rendered = Text()
        first = parts[0]
        if first.lower().startswith("duckln"):
            rendered.append("Duckln", style=f"bold {DUCKLN_GOLD}")
        else:
            rendered.append(first, style=f"bold {DUCKLN_GOLD}")
        for part in parts[1:]:
            rendered.append(" • ", style=DUCKLN_GREY)
            if " " in part:
                label, value = part.split(" ", 1)
                rendered.append(f"{label} ", style=DUCKLN_GREY)
                rendered.append(value, style=DUCKLN_EGGSHELL)
            else:
                rendered.append(part, style=DUCKLN_EGGSHELL)
        return rendered


    def _render_footer_text(footer_text: str) -> Text:
        footer = Text()
        _append_inline_styled(footer, _strip_ansi(footer_text), base_color=DUCKLN_SYSTEM)
        return footer


    def _render_web_preview_snapshot(snapshot: WebPreviewSnapshot) -> Text:
        preview = Text()
        header_lines = [
            f"URL: {snapshot.final_url}",
            f"Status: {snapshot.status_code if snapshot.status_code is not None else 'unavailable'}",
        ]
        if snapshot.content_type:
            header_lines.append(f"Content-Type: {snapshot.content_type}")
        if snapshot.title:
            header_lines.append(f"Title: {snapshot.title}")
        header_lines.append(snapshot.summary)
        for index, line in enumerate(header_lines):
            _append_structured_line(preview, line, base_color=DUCKLN_EGGSHELL)
            preview.append("\n")
            if index == len(header_lines) - 1:
                preview.append("\n")
        if snapshot.error_message:
            _append_structured_line(preview, f"Error: {snapshot.error_message}", base_color=DUCKLN_GREY)
            preview.append("\n\n")
        if snapshot.excerpt_lines:
            _append_structured_line(preview, "Preview excerpt:", base_color=DUCKLN_SYSTEM)
            preview.append("\n")
            for line in snapshot.excerpt_lines:
                _append_structured_line(preview, f"- {line}", base_color=DUCKLN_EGGSHELL)
                preview.append("\n")
        else:
            _append_structured_line(preview, "Duckln did not find visible page text to preview.", base_color=DUCKLN_GREY)
        return preview


    class ThreadFriendlyLinuxDriver(LinuxDriver):
        """Linux driver variant that skips signal registration outside the main thread."""

        def __init__(
            self,
            app,
            *,
            debug: bool = False,
            mouse: bool = True,
            size: tuple[int, int] | None = None,
        ) -> None:
            textual_driver_module.Driver.__init__(self, app, debug=debug, mouse=mouse, size=size)
            self._file = sys.__stderr__
            self.fileno = sys.__stdin__.fileno()
            self.input_tty = sys.__stdin__.isatty()
            self.attrs_before = None
            self.exit_event = threading.Event()
            self._key_thread = None
            self._writer_thread = None
            self._must_signal_resume = False
            self._in_band_window_resize = False
            self._mouse_pixels = False
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTSTP, self._sigtstp_application)
                signal.signal(signal.SIGCONT, self._sigcont_application)

        def start_application_mode(self):
            if threading.current_thread() is threading.main_thread():
                return super().start_application_mode()

            loop = asyncio.get_running_loop()

            def send_size_event() -> None:
                width, height = self._get_terminal_size()
                textual_size = Size(width, height)
                event = textual_app_module.events.Resize(textual_size, textual_size)
                asyncio.run_coroutine_threadsafe(
                    self._app._post_message(event),
                    loop=loop,
                )

            self._writer_thread = WriterThread(self._file)
            self._writer_thread.start()
            send_size_event()

            self.write("\x1b[?1049h")
            self._enable_mouse_support()
            try:
                self.attrs_before = termios.tcgetattr(self.fileno)
            except termios.error:
                self.attrs_before = None

            try:
                newattr = termios.tcgetattr(self.fileno)
            except termios.error:
                pass
            else:
                newattr[tty.LFLAG] = self._patch_lflag(newattr[tty.LFLAG])
                newattr[tty.IFLAG] = self._patch_iflag(newattr[tty.IFLAG])
                newattr[tty.CC][termios.VMIN] = 1
                try:
                    termios.tcsetattr(self.fileno, termios.TCSANOW, newattr)
                except termios.error:
                    pass

            self.write("\x1b[?25l")
            self.write("\x1b[?1004h")
            self.write("\x1b[>1u")
            self.flush()
            self._key_thread = threading.Thread(target=self._run_input_thread, name="textual-input")
            self._key_thread.start()
            self._request_terminal_sync_mode_support()
            self._query_in_band_window_resize()
            self._enable_bracketed_paste()
            self._disable_line_wrap()
            self._enable_mouse_support()

        def disable_input(self) -> None:
            try:
                if not self.exit_event.is_set():
                    if threading.current_thread() is threading.main_thread():
                        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
                    self._disable_mouse_support()
                    self.exit_event.set()
                    if self._key_thread is not None:
                        self._key_thread.join()
                    self.exit_event.clear()
                    try:
                        termios.tcflush(self.fileno, termios.TCIFLUSH)
                    except termios.error:
                        pass
            except Exception:
                pass

    _MOUSE_SGR_PATTERN = re.compile(r"\x1b\[<[\d;]*[Mm]")
    _MOUSE_X10_PATTERN = re.compile(r"\x1b\[M[\x00-\xff]{3}")
    _MOUSE_SGR_PARTIAL_TAIL = re.compile(r"\x1b\[<[\d;]*$")
    _MOUSE_X10_PARTIAL_TAIL = re.compile(r"\x1b\[M[\x00-\xff]{0,2}$")
    _MOUSE_BARE_ESC_TAIL = re.compile(r"\x1b\[?$")
    _MOUSE_ORPHAN_TAIL = re.compile(r"^[\d;]+[Mm]")

    class ObservableTerminal(Terminal):
        """Terminal widget with a bounded stdout hook for watcher integrations."""

        def __init__(
            self,
            *args,
            stdout_callback: Callable[[str], None] | None = None,
            scroll_callback: Callable[[int, int], None] | None = None,
            **kwargs,
        ) -> None:
            super().__init__(*args, **kwargs)
            self._stdout_callback = stdout_callback
            self._scroll_callback = scroll_callback
            self._mouse_carry = ""
            self._scrollback: deque[Text] = deque(maxlen=2000)
            self._screen_signature: tuple[str, ...] = ()
            self._current_screen_lines: list[Text] = []
            self._scroll_offset = 0

        def _line_plain(self, line: Text) -> str:
            return str(getattr(line, "plain", str(line))).rstrip()

        def _append_scrollback_snapshot(self, lines: list[Text]) -> None:
            signature = tuple(self._line_plain(line) for line in lines)
            if not signature or signature == self._screen_signature:
                return
            for line in lines:
                plain = self._line_plain(line)
                if not plain:
                    continue
                if self._scrollback and self._line_plain(self._scrollback[-1]) == plain:
                    continue
                self._scrollback.append(line.copy())
            self._screen_signature = signature

        def _set_terminal_display(self, lines: list[Text]) -> None:
            self._current_screen_lines = [line.copy() for line in lines]
            if self._scroll_offset <= 0:
                self._scroll_offset = 0
                self._notify_scroll_position(0, max(0, len(self._scrollback) - 1))
                self._display = TerminalDisplay(lines)
                self.refresh()
                return
            history = list(self._scrollback)
            if not history:
                self._scroll_offset = 0
                self._notify_scroll_position(0, 0)
                self._display = TerminalDisplay(lines)
                self.refresh()
                return
            visible_rows = max(1, getattr(self._screen, "lines", len(lines)) or len(lines) or 1)
            max_offset = max(0, len(history) - 1)
            self._scroll_offset = min(self._scroll_offset, max_offset)
            end = max(0, len(history) - self._scroll_offset)
            start = max(0, end - max(1, visible_rows - 1))
            indicator = Text(f"↑ scrollback {self._scroll_offset}/{max_offset}  PageDown to return", style=DUCKLN_CYAN)
            self._notify_scroll_position(self._scroll_offset, max_offset)
            self._display = TerminalDisplay([indicator, *[line.copy() for line in history[start:end]]])
            self.refresh()

        def _notify_scroll_position(self, offset: int, max_offset: int) -> None:
            if self._scroll_callback is None:
                return
            try:
                self._scroll_callback(offset, max_offset)
            except Exception:
                return

        def visible_plain_text(self) -> str:
            lines = [self._line_plain(line) for line in (self._current_screen_lines or [])]
            return "\n".join(line for line in lines if line).strip()

        def scroll_up(self, amount: int = 5) -> None:
            if not self._scrollback:
                return
            self._scroll_offset = min(len(self._scrollback) - 1, self._scroll_offset + max(1, amount))
            self._set_terminal_display(self._current_screen_lines)

        def scroll_down(self, amount: int = 5) -> None:
            if self._scroll_offset <= 0:
                return
            self._scroll_offset = max(0, self._scroll_offset - max(1, amount))
            self._set_terminal_display(self._current_screen_lines)

        def scroll_to_bottom(self) -> None:
            self._scroll_offset = 0
            self._set_terminal_display(self._current_screen_lines)

        def on_mouse_scroll_up(self, event) -> None:
            self.scroll_up(3)
            event.stop()

        def on_mouse_scroll_down(self, event) -> None:
            self.scroll_down(3)
            event.stop()

        def _strip_mouse_events(self, chars: str) -> str:
            """Remove SGR and X10 mouse event sequences before feeding to pyte.

            pyte does not recognize SGR mouse reports (ESC[<Cb;Cx;CyM/m): it parses
            '<' as the CSI final byte, then renders the trailing digits as visible
            text. We strip complete sequences and buffer any partial tail that
            spans a chunk boundary."""
            combined = self._mouse_carry + chars
            self._mouse_carry = ""
            if combined.startswith(("M", "m")) and not combined.startswith("\x1b"):
                combined = combined[1:]
            orphan = _MOUSE_ORPHAN_TAIL.match(combined)
            if orphan is not None:
                combined = combined[orphan.end():]
            combined = _MOUSE_SGR_PATTERN.sub("", combined)
            combined = _MOUSE_X10_PATTERN.sub("", combined)
            sgr_partial = _MOUSE_SGR_PARTIAL_TAIL.search(combined)
            if sgr_partial is not None:
                self._mouse_carry = combined[sgr_partial.start():]
                return combined[:sgr_partial.start()]
            x10_partial = _MOUSE_X10_PARTIAL_TAIL.search(combined)
            if x10_partial is not None:
                self._mouse_carry = combined[x10_partial.start():]
                return combined[:x10_partial.start()]
            bare = _MOUSE_BARE_ESC_TAIL.search(combined)
            if bare is not None:
                self._mouse_carry = combined[bare.start():]
                return combined[:bare.start()]
            return combined

        async def recv(self):
            try:
                while True:
                    message = await self.recv_queue.get()
                    cmd = message[0]
                    if cmd == "setup":
                        await self.send_queue.put(["set_size", self.nrow, self.ncol])
                    elif cmd == "stdout":
                        chars = message[1]
                        if self._stdout_callback is not None:
                            self._stdout_callback(chars)
                        chars = self._strip_mouse_events(chars)

                        for sep_match in re.finditer(_re_ansi_sequence, chars):
                            sequence = sep_match.group(0)
                            if sequence.startswith(DECSET_PREFIX):
                                parameters = sequence.removeprefix(DECSET_PREFIX).split(";")
                                if "1000h" in parameters:
                                    self.mouse_tracking = True
                                if "1000l" in parameters:
                                    self.mouse_tracking = False

                        try:
                            self.stream.feed(chars)
                        except TypeError:
                            pass

                        lines = []
                        for y in range(self._screen.lines):
                            line_text = Text()
                            line = self._screen.buffer[y]
                            style_change_pos = 0
                            for x in range(self._screen.columns):
                                char = line[x]
                                line_text.append(char.data)
                                if x > 0:
                                    last_char = line[x - 1]
                                    if not self.char_style_cmp(char, last_char) or x == self._screen.columns - 1:
                                        last_style = self.char_rich_style(last_char)
                                        line_text.stylize(last_style, style_change_pos, x + 1)
                                        style_change_pos = x
                                if self._screen.cursor.x == x and self._screen.cursor.y == y:
                                    line_text.stylize("reverse", x, x + 1)
                            lines.append(line_text)

                        if self._current_screen_lines:
                            self._append_scrollback_snapshot(self._current_screen_lines)
                        self._set_terminal_display(lines)
                    elif cmd == "disconnect":
                        self.stop()
            except asyncio.CancelledError:
                pass

    class DividerHandle(Static):
        """Single-character divider that supports drag-based resizing."""

        dragging = reactive(False)

        def on_mouse_down(self, event: MouseDown) -> None:
            self.dragging = True
            self.add_class("-hover")
            self.app.capture_mouse(self)
            event.stop()

        def on_mouse_up(self, event: MouseUp) -> None:
            self.dragging = False
            self.remove_class("-hover")
            self.app.capture_mouse(None)
            event.stop()

        def on_mouse_move(self, event: MouseMove) -> None:
            if self.dragging:
                self.app.adjust_split_from_x(event.screen_x)
                event.stop()

        def on_enter(self) -> None:
            self.add_class("-hover")

        def on_leave(self) -> None:
            if not self.dragging:
                self.remove_class("-hover")


    class DucklnSplitPaneApp(App):
        """Interactive Textual split-pane shell scaffold for Duckln."""

        CSS = TEXTUAL_SPLIT_PANE_CSS
        BINDINGS = [
            ("ctrl+b", "focus_chat", "Focus chat input"),
            ("ctrl+t", "toggle_terminal", "Toggle terminal pane"),
            ("ctrl+p", "attach_file", "Attach file"),
            ("ctrl+r", "toggle_repair_details", "Toggle repair details"),
            ("ctrl+shift+r", "dismiss_repair_panel", "Dismiss repair panel"),
            ("ctrl+e", "toggle_trace_details", "Toggle trace details"),
            ("ctrl+shift+e", "dismiss_trace_panel", "Dismiss trace panel"),
            ("ctrl+shift+c", "copy_terminal_text", "Copy terminal text"),
            ("ctrl+shift+v", "paste_to_terminal", "Paste into terminal"),
        ]

        def action_quit(self) -> None:
            """Kill any running subprocess and unblock the main thread before quitting."""
            try:
                from duckln.shell import request_global_abort
                request_global_abort()
            except Exception:
                pass
            try:
                self._input_queue.put(_EXIT_SENTINEL)
            except Exception:
                pass
            super().action_quit()

        def __init__(
            self,
            *,
            status_text,
            connection_label: ConnectionContextLabel,
            shell_command: str,
            input_queue: "queue.Queue[str]",
            footer_text: str,
            ready_event: threading.Event | None = None,
            terminal_connection_callback: Callable[[str], None] | None = None,
            config_dir=None,
        ) -> None:
            super().__init__()
            self._status_text = status_text
            self._connection_label = connection_label
            self._shell_command = shell_command
            self._input_queue = input_queue
            self._footer_text = footer_text
            self._ready_event = ready_event
            self._terminal_connection_callback = terminal_connection_callback
            # Plan 63 Fix 3: kept for the `+` dropdown's internet-skill toggle
            # (which needs the config_dir to read/write the skill state).
            self._app_config_dir = config_dir
            self._detected_terminal_connection: str | None = None
            self._split_percent = 50
            self._previous_split_percent = 50
            self._terminal_collapsed = False
            self._terminal_incident_queue: "queue.Queue[dict[str, object]]" = queue.Queue()
            # Plan 135 F5: terminal-busy queue — when a command is running in the pane and
            # the user submits another, accept + queue it and run it once the pane is free.
            self._terminal_busy = False
            self._pending_terminal_commands: list[tuple[str, str | None]] = []
            self._sentinel_waiter = PaneSentinelWaiter()
            self._watcher = MLTerminalWatcher(
                self._handle_proactive_observation,
                incident_callback=self._handle_terminal_incident,
                sentinel_waiter=self._sentinel_waiter,
            )
            self._choice_result_queue: "queue.Queue[str | None]" = queue.Queue()
            self._choice_active = False
            self._choice_option_lookup: dict[str, str] = {}
            self._choice_choices: tuple[str, ...] = ()
            self._choice_allow_other = False
            self._choice_objective_key: str | None = None
            # Plan 63 Fix 3: `+` dropdown state.
            self._attach_active = False
            self._attach_option_lookup: dict[str, str] = {}
            self._attach_config_dir = self._app_config_dir
            self._approval_result_queue: "queue.Queue[DependencyApprovalDecision]" = queue.Queue()
            self._approval_active = False
            self._approval_request: DependencyApprovalRequest | None = None
            self._approval_selected_ids: set[str] = set()
            self._approval_objective_key: str | None = None
            self._slash_active = False
            self._slash_suggestions: tuple[str, ...] = ()
            # Plan 155 F4: the `@`-mention target picker (delete @vm/@container/@repo, …).
            self._mention_active = False
            self._mention_targets: tuple = ()
            self._preview_url: str | None = None
            self._text_prompt_result_queue: "queue.Queue[str | None]" = queue.Queue()
            self._text_prompt_active = False
            self._text_prompt_mode = "text"
            self._text_prompt_default = ""
            self._text_prompt_objective_key: str | None = None
            self._attached_files: list[str] = []
            self._activity_text = ""
            self._activity_spinner = False
            self._activity_spinner_index = 0
            # Plan 87 Fix 3: a slow, unbounded tick so the rotating processing VERB
            # changes gently (~every few seconds) instead of flickering each 0.1s.
            self._activity_tick = 0
            self._activity_started_at: float | None = None
            self._objective_text = ""
            self._objective_activity_text = ""
            self._objective_activity_spinner = False
            self._objective_activity_started_at: float | None = None
            self._objective_key: str | None = None
            self._repair_panel_visible = False
            self._repair_panel_expanded = False
            self._repair_panel_details = ""
            self._repair_panel_fingerprints: set[tuple[str, str, str]] = set()
            self._repair_panel_objective_key: str | None = None
            # Item D: when True, _show_repair_panel emits inline step messages instead of
            # toggling the modal panel widget. Default on for the no-modal UX.
            self._repair_panel_inline_mode = True
            self._trace_panel_visible = False
            self._trace_panel_expanded = False
            self._trace_panel_objective_key: str | None = None
            self._last_trace_fingerprint: tuple[str, str, str] | None = None
            # Plan 76 Fix E: collapsible "Duckln's thinking" box in the chat pane.
            # Plan 87 Fix 4: pace rapid thoughts so each line is readable — new
            # thoughts queue in `_thoughts_pending` and are revealed at most one per
            # `_THOUGHT_MIN_INTERVAL` into the visible `_thoughts`.
            self._thoughts: list[str] = []
            self._thoughts_pending: list[str] = []
            self._thoughts_last_release = 0.0
            self._thoughts_expanded = False
            self._last_transcript_key: tuple[str, str] | None = None
            self._last_objective_chat_sig: str = ""
            self._terminal_url_carry = ""
            self._last_terminal_url: str | None = None
            self._last_reasoning_path: str | None = None  # Plan 141 F2: latest logical-thinking.md
            self._last_terminal_url_announced: str | None = None
            # Plan 86 Fix 3: announce each unique app URL at most once (no apt-install spam).
            self._announced_terminal_urls: set[str] = set()

        def get_driver_class(self):
            if platform.system() == "Windows":
                return super().get_driver_class()
            return ThreadFriendlyLinuxDriver

        def compose(self) -> ComposeResult:
            yield Static(_render_status_bar(self._status_text), id="status-bar")
            with Horizontal(id="main-area"):
                with Vertical(classes="pane", id="chat-pane"):
                    with Horizontal(classes="pane-header", id="chat-pane-header"):
                        yield Static("Duckln Agent", classes="pane-header-title", id="chat-pane-title")
                    # Plan 183 F1: wrap=True so long lines reflow to the pane width instead of
                    # being cut off (the user had to scroll right to read them).
                    yield RichLog(id="chat-scroll", wrap=True, markup=False, auto_scroll=True)
                    with Vertical(id="thoughts-panel"):
                        yield Static("▸ Duckln's thinking", id="thoughts-toggle")
                        yield Static("", id="thoughts-body")
                    with Vertical(id="choice-overlay"):
                        yield Static("", id="choice-prompt")
                        yield OptionList(id="choice-list")
                        # Plan 191 F6: an inline "Other — type your own" free-text row
                        # (shown only when allow_other), matching the Accept-this-plan format.
                        yield Input(placeholder="Other — type your own…", id="choice-other")
                        yield Static("Esc to cancel", id="choice-hint")
                    with Vertical(id="slash-overlay"):
                        yield Static("", id="slash-prompt")
                        yield OptionList(id="slash-list")
                    with Vertical(id="mention-overlay"):
                        yield Static("", id="mention-prompt")
                        yield OptionList(id="mention-list")
                    with Vertical(id="approval-overlay"):
                        yield Static("", id="approval-prompt")
                        yield Static("", id="approval-help")
                        yield OptionList(id="approval-list")
                        with Horizontal(id="approval-actions"):
                            yield Static("Approve selected", id="approval-approve-selected", classes="approval-button -primary")
                            yield Static("Reject selected", id="approval-reject-selected", classes="approval-button")
                            yield Static("Approve all", id="approval-approve-all", classes="approval-button")
                            yield Static("Cancel", id="approval-cancel", classes="approval-button")
                    with Vertical(id="text-overlay"):
                        yield Static("", id="text-prompt")
                        yield Static("", id="text-help")
                        yield Input(placeholder="Enter a value...", id="text-overlay-input")
                    # Plan 63 Fix 3: `+` button dropdown — Upload / Add context /
                    # Browse the web. Hidden until the user clicks `+`.
                    with Vertical(id="attach-overlay"):
                        yield Static("", id="attach-prompt")
                        yield OptionList(id="attach-list")
                    with Vertical(id="repair-panel"):
                        yield Static("", id="repair-title")
                        yield Static("", id="repair-summary")
                        with Horizontal(id="repair-actions"):
                            yield Static("Show details", id="repair-toggle", classes="repair-button -primary")
                            yield Static("Dismiss", id="repair-dismiss", classes="repair-button")
                        yield Static("", id="repair-body")
                    with Vertical(id="trace-panel"):
                        yield Static("", id="trace-title")
                        yield Static("", id="trace-summary")
                        with Horizontal(id="trace-actions"):
                            yield Static("Show details", id="trace-toggle", classes="trace-button -primary")
                            yield Static("Dismiss", id="trace-dismiss", classes="trace-button")
                        yield Static("", id="trace-body")
                yield DividerHandle("", id="divider")
                with Vertical(classes="pane", id="terminal-pane"):
                    with Horizontal(classes="pane-header", id="terminal-pane-header"):
                        yield Static(self._connection_label.label, classes="pane-header-title", id="terminal-pane-title")
                        yield Static("⊟", id="terminal-collapse")
                    with Vertical(id="preview-pane"):
                        with Horizontal(id="preview-header"):
                            yield Static("Web Preview", id="preview-title")
                            yield Static("↻", classes="preview-button", id="preview-refresh")
                            yield Static("✕", classes="preview-button", id="preview-close")
                        yield Static("", id="preview-body")
                    with Horizontal(id="terminal-frame"):
                        yield ObservableTerminal(
                            self._shell_command,
                            id="terminal-shell",
                            stdout_callback=self._handle_terminal_stdout,
                            scroll_callback=self._handle_terminal_scroll_position,
                        )
                        yield Static("┃", id="terminal-scroll-indicator")
            yield Static("", id="separator")
            yield Static("", id="objective-bar")
            yield Static("", id="activity-bar")
            with Horizontal(id="input-bar"):
                yield Static(_attachment_button_label(tuple(self._attached_files)), id="attach-button")
                # Plan 63 Fix 2: moved internet icon to the `+` dropdown
                # (under "Browse the web") so the input field stays visible.
                yield Static("◆", id="input-prompt")
                yield Input(placeholder="Send a message...", id="chat-input")
            yield Static(_render_footer_text(self._footer_text), id="footer")

        def on_mount(self) -> None:
            self.query_one("#chat-input", Input).focus()
            self.query_one("#chat-pane-header", Horizontal).add_class("-focused")
            self.query_one("#chat-pane-title", Static).styles.color = DUCKLN_GOLD
            self.query_one("#terminal-pane-title", Static).styles.color = self._connection_label.color
            self.query_one("#input-prompt", Static).styles.color = DUCKLN_GOLD
            self.query_one("#status-bar", Static).styles.color = DUCKLN_EGGSHELL
            self.query_one("#footer", Static).styles.color = DUCKLN_SYSTEM
            self.query_one("#terminal-shell", ObservableTerminal).start()
            self._refresh_attachment_button()
            self._update_objective_bar()
            self._update_activity_bar()
            self._apply_split_layout()
            self.set_interval(0.1, self._tick_activity_spinner)
            # Plan 87 Fix 4: drain queued thoughts at a readable cadence even when no
            # new thought arrives to trigger a release.
            self.set_interval(0.15, self._release_pending_thoughts)
            if self._ready_event is not None:
                self._ready_event.set()

        def on_resize(self, event: Resize) -> None:
            del event
            self._apply_split_layout()

        def on_unmount(self) -> None:
            self._watcher.stop()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "text-overlay-input":
                self._submit_text_overlay(event.value)
                event.input.value = ""
                event.stop()
                return
            # Plan 191 F6: Enter in the inline "Other" row resolves the choice with the typed text.
            if event.input.id == "choice-other":
                typed = str(event.value or "").strip()
                event.input.value = ""
                event.stop()
                if self._choice_active and typed:
                    self._hide_choice_overlay(typed)
                return
            # Plan 155 F4: Enter while still typing an `@`-mention inserts the highlighted
            # target instead of submitting a partial token.
            if event.input.id == "chat-input" and self._mention_active:
                if self._mention_query(event.value) is not None:
                    self._accept_mention_suggestion()
                    event.stop()
                    return
                self._hide_mention_overlay()
            if event.input.id == "chat-input" and self._slash_active:
                current_value = event.value.strip()
                if current_value not in self._slash_suggestions:
                    selected = self._current_slash_selection()
                    if selected is not None:
                        event.input.value = selected
                        self._refresh_slash_overlay(selected)
                        event.stop()
                        return
                self._hide_slash_overlay()
            value = event.value.strip()
            if not value:
                return
            # Plan 198 F1: a bare "stop"/"cancel"/"abort"/"halt" must interrupt a RUNNING operation
            # IMMEDIATELY. This handler runs on the UI event loop, but the worker is a single blocked
            # thread — so queueing the word would only be seen AFTER the op ends (the "Duckln won't
            # stop" bug). Instead, set the global abort flag + interrupt the pane out-of-band so the
            # long-running watch/poll loops (which now check the flag) bail promptly.
            if value.lower() in _STOP_WORDS:
                self.append_message(value, role="user")
                try:
                    from duckln.shell import request_global_abort
                    request_global_abort()
                except Exception:
                    pass
                try:
                    self.interrupt_terminal()  # send Ctrl-C to the embedded pane
                except Exception:
                    pass
                self.append_message("⏹ Stopping the current operation…", role="assistant")
                event.input.value = ""
                event.stop()
                return
            self.append_message(value, role="user")
            self._input_queue.put(value)
            event.input.value = ""

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "text-overlay-input":
                if self._text_prompt_mode == "attachment":
                    helper = self.query_one("#text-help", Static)
                    helper.update(
                        "Paste a full local file path. Example: /Users/you/Downloads/audio.mp3. Enter submits. Esc cancels."
                    )
                return
            if event.input.id != "chat-input":
                return
            if self._choice_active or self._approval_active or self._text_prompt_active:
                self._hide_slash_overlay()
                self._hide_mention_overlay()
                return
            # Plan 155 F4: an `@`-mention token shows the target picker; otherwise the slash menu.
            if self._mention_query(event.value) is not None:
                self._hide_slash_overlay()
                self._refresh_mention_overlay(event.value)
                return
            self._hide_mention_overlay()
            self._refresh_slash_overlay(event.value)

        def action_focus_chat(self) -> None:
            if self._text_prompt_active:
                self.query_one("#text-overlay-input", Input).focus()
                self._mark_focus(chat=True)
                return
            if self._approval_active:
                self.query_one("#approval-list", OptionList).focus()
                self._mark_focus(chat=True)
                return
            if self._choice_active:
                self.query_one("#choice-list", OptionList).focus()
                self._mark_focus(chat=True)
                return
            self.query_one("#chat-input", Input).focus()
            self._mark_focus(chat=True)

        def action_toggle_terminal(self) -> None:
            if self._terminal_collapsed:
                self._terminal_collapsed = False
                self._split_percent = self._previous_split_percent
            else:
                self._previous_split_percent = self._split_percent
                self._terminal_collapsed = True
            self._apply_split_layout()

        def action_attach_file(self) -> None:
            self.show_attachment_overlay()

        def action_toggle_repair_details(self) -> None:
            self._toggle_repair_panel()

        def action_dismiss_repair_panel(self) -> None:
            self._hide_repair_panel()

        def action_toggle_trace_details(self) -> None:
            self._toggle_trace_panel()

        def action_dismiss_trace_panel(self) -> None:
            self._hide_trace_panel()

        def action_copy_terminal_text(self) -> None:
            terminal_text = ""
            try:
                terminal_text = self.query_one("#terminal-shell", ObservableTerminal).visible_plain_text()
            except Exception:
                terminal_text = ""
            copy_text = self._last_terminal_url or terminal_text
            if not copy_text:
                self.update_activity_text("No terminal text to copy", spinner=False)
                return
            try:
                self.copy_to_clipboard(copy_text)
            except Exception:
                self.update_activity_text("Could not copy terminal text from here", spinner=False)
                return
            self.update_activity_text("Copied latest terminal link" if self._last_terminal_url else "Copied terminal text", spinner=False)

        def action_paste_to_terminal(self) -> None:
            text = _read_os_clipboard_text()
            if not text:
                self.update_activity_text("Clipboard is empty or unavailable", spinner=False)
                return
            asyncio.create_task(self._send_terminal_text(text))
            self._mark_focus(chat=False)

        def on_click(self, event) -> None:
            # Plan 141 F2: a terminal OSC-8 link in the RichLog isn't delivered to the OS
            # because Textual captures the mouse — but Textual DOES expose the Rich link
            # under the cursor on click. Open a clicked `file://…` reasoning link ourselves.
            _style = getattr(event, "style", None)
            _link = getattr(_style, "link", None) if _style is not None else None
            if _link and _link.startswith("file://"):
                try:
                    webbrowser.open(_link)
                except Exception:
                    pass
                return
            widget_id = getattr(getattr(event, "widget", None), "id", None)
            if widget_id == "terminal-collapse":
                self.action_toggle_terminal()
            elif widget_id == "attach-button":
                # Plan 63 Fix 3: `+` now opens a dropdown menu (Upload /
                # Add context / Browse the web). The legacy attachment-only
                # behaviour lives under "Upload from computer".
                self.show_plus_dropdown_overlay()
            elif widget_id == "repair-toggle":
                self._toggle_repair_panel()
            elif widget_id == "repair-dismiss":
                self._hide_repair_panel()
            elif widget_id == "trace-toggle":
                self._toggle_trace_panel()
            elif widget_id == "trace-dismiss":
                self._hide_trace_panel()
            elif widget_id in {"thoughts-toggle", "thoughts-body"}:
                self.toggle_thoughts()
            elif widget_id == "approval-approve-selected":
                self._finish_dependency_approval(approved=bool(self._approval_selected_ids), approve_all=False)
            elif widget_id == "approval-reject-selected":
                self._finish_dependency_approval(approved=False, approve_all=False)
            elif widget_id == "approval-approve-all":
                self._approve_all_dependencies()
            elif widget_id == "approval-cancel":
                self._finish_dependency_approval(approved=False, approve_all=False)
            elif widget_id == "preview-close":
                self.close_web_preview()
            elif widget_id == "preview-refresh":
                self.refresh_web_preview()
            elif widget_id in {"terminal-shell", "terminal-frame", "terminal-scroll-indicator", "terminal-pane-header", "terminal-pane-title"}:
                self.query_one("#terminal-shell", ObservableTerminal).focus()
                self._mark_focus(chat=False)
            elif widget_id in {"chat-input", "chat-scroll", "chat-pane", "chat-pane-header"}:
                self.action_focus_chat()

        def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
            if self._slash_active and event.option_list.id == "slash-list":
                self._accept_slash_suggestion()
                return
            if self._mention_active and event.option_list.id == "mention-list":
                self._accept_mention_suggestion()
                return
            if self._approval_active and event.option_list.id == "approval-list":
                self._toggle_highlighted_dependency()
                return
            # Plan 63 Fix 3: `+` dropdown selection.
            if self._attach_active and event.option_list.id == "attach-list":
                key = self._attach_option_lookup.get(str(event.option.prompt))
                if key is not None:
                    self._handle_attach_selection(key)
                return
            if not self._choice_active:
                return
            # Plan 191 F6: resolve by index (robust to numbered/wrapping renderables), with the
            # display-label lookup as a fallback.
            idx = getattr(event, "option_index", None)
            if isinstance(idx, int) and 0 <= idx < len(self._choice_choices):
                selected = self._choice_choices[idx]
            else:
                selected = self._choice_option_lookup.get(str(event.option.prompt), str(event.option.prompt))
            self._hide_choice_overlay(selected)

        def on_key(self, event: Key) -> None:
            if self._text_prompt_active:
                if event.key == "escape":
                    self._finish_text_prompt_overlay(None)
                    event.stop()
                return
            if self._choice_active:
                # Plan 191 F6: Esc always cancels; the q/Q shortcut only when the inline
                # "Other" field is NOT focused (so typing "q" there isn't a cancel).
                other_focused = getattr(self.focused, "id", None) == "choice-other"
                if event.key == "escape" or (event.key in {"q", "Q"} and not other_focused):
                    self._hide_choice_overlay(None)
                    event.stop()
                    return
            # Plan 63 Fix 3: Escape closes the `+` dropdown.
            if self._attach_active and event.key == "escape":
                self._hide_attach_overlay()
                event.stop()
                return
            if not (self._slash_active or self._choice_active or self._approval_active):
                try:
                    terminal = self.query_one("#terminal-shell", ObservableTerminal)
                except Exception:
                    terminal = None
                if terminal is not None and event.key in {"pageup", "shift+pageup"}:
                    terminal.scroll_up(10)
                    event.stop()
                    return
                if terminal is not None and event.key in {"pagedown", "shift+pagedown"}:
                    terminal.scroll_down(10)
                    event.stop()
                    return
                if terminal is not None and event.key == "end":
                    terminal.scroll_to_bottom()
                    event.stop()
                    return
            focused = self.focused
            if self._slash_active and isinstance(focused, Input) and focused.id == "chat-input":
                option_list = self.query_one("#slash-list", OptionList)
                if event.key == "down":
                    move = getattr(option_list, "action_cursor_down", None)
                    if callable(move):
                        move()
                    event.stop()
                    return
                if event.key == "up":
                    move = getattr(option_list, "action_cursor_up", None)
                    if callable(move):
                        move()
                    event.stop()
                    return
                if event.key == "tab":
                    self._accept_slash_suggestion()
                    event.stop()
                    return
                if event.key == "escape":
                    self._hide_slash_overlay()
                    event.stop()
                    return
            # Plan 155 F4: same navigation for the `@`-mention picker.
            if self._mention_active and isinstance(focused, Input) and focused.id == "chat-input":
                option_list = self.query_one("#mention-list", OptionList)
                if event.key == "down":
                    move = getattr(option_list, "action_cursor_down", None)
                    if callable(move):
                        move()
                    event.stop()
                    return
                if event.key == "up":
                    move = getattr(option_list, "action_cursor_up", None)
                    if callable(move):
                        move()
                    event.stop()
                    return
                if event.key == "tab":
                    self._accept_mention_suggestion()
                    event.stop()
                    return
                if event.key == "escape":
                    self._hide_mention_overlay()
                    event.stop()
                    return
            if not self._approval_active:
                return
            if event.key == "space":
                self._toggle_highlighted_dependency()
                event.stop()
            elif event.key in {"a", "A"}:
                self._approve_all_dependencies()
                event.stop()
            elif event.key in {"enter"} and self._approval_selected_ids:
                self._finish_dependency_approval(approved=True, approve_all=False)
                event.stop()
            elif event.key == "escape":
                self._finish_dependency_approval(approved=False, approve_all=False)
                event.stop()

        def adjust_split_from_x(self, screen_x: int) -> None:
            width = max(self.size.width, 1)
            min_chat = 30
            min_terminal = 40
            clamped = max(min_chat, min(screen_x, width - min_terminal - 1))
            self._split_percent = int((clamped / width) * 100)
            self._terminal_collapsed = False
            self._apply_split_layout()

        def append_message(self, message: str, *, role: str = "assistant") -> None:
            transient_activity = None
            if role in {"assistant", "status"}:
                transient_activity = _transient_activity_update(message)
            if transient_activity is not None:
                activity_text, spinner = transient_activity
                self.update_activity_text(activity_text, spinner=spinner)
                return
            # Plan 119: swallow bare `ollama pull` refresh noise (a lone "24s",
            # size, or spinner) while a pull is active so it doesn't stack in chat.
            if role in {"assistant", "status"} and self._activity_text.startswith("Ollama pull") and _is_ollama_pull_noise(message):
                return
            trace_payload = None
            if role in {"assistant", "status"}:
                trace_payload = _parse_trace_message(message)
            if trace_payload is not None:
                title, summary, details = trace_payload
                activity_text, spinner = _trace_activity_update(title)
                if _is_repair_trace_title(title) or (
                    self._repair_panel_visible
                    and self._repair_panel_objective_key == self._objective_key
                    and _is_repair_adjacent_trace_title(title)
                ):
                    self._hide_trace_panel()
                    self._show_repair_panel(
                        title=title,
                        summary=summary,
                        details=details,
                        activity_message=activity_text,
                        activity_spinner=spinner,
                    )
                    return
                self.update_activity_text(activity_text, spinner=spinner)
                self._show_trace_panel(title=title, summary=summary, details=details)
                trace_fingerprint = (title, summary, details)
                if self._last_trace_fingerprint == trace_fingerprint:
                    return
                self._last_trace_fingerprint = trace_fingerprint
                return
            else:
                self._last_trace_fingerprint = None
            plain_message = _strip_ansi(message).strip()
            repair_payload = None
            if role in {"assistant", "status", "proactive"}:
                repair_payload = _repair_session_message_payload(plain_message)
            if repair_payload is not None:
                summary, details = repair_payload
                self._show_repair_panel(title="Active repair", summary=summary, details=details)
                return
            if role in {"assistant", "status", "proactive"}:
                transcript_key = (role, plain_message)
                if plain_message and self._last_transcript_key == transcript_key:
                    return
                self._last_transcript_key = transcript_key
            else:
                self._last_transcript_key = None
            if role in {"assistant", "status"} and (
                "ollama connection ready" in plain_message.casefold()
                or "updated provider to ollama" in plain_message.casefold()
            ):
                self.clear_activity_text()
            # Plan 135 F7: stop the "Working… • taking longer than expected" spinner the
            # moment Duckln pauses/halts/finishes — it must not keep running after the
            # work has stopped (the stuck-timer red flag from the screenshots).
            if role in {"assistant", "status"} and _is_activity_halting_message(plain_message):
                self.clear_activity_text()
            log = self.query_one("#chat-scroll", RichLog)
            width = getattr(log.size, "width", 0) or 72
            # Plan 138 F3: the reasoning-log line must be CLICKABLE — render the file path
            # as a real terminal hyperlink (OSC-8) so clicking OPENS logical-thinking.md.
            _think_link = _reasoning_link_renderable(plain_message)
            if _think_link is not None:
                # Plan 141 F2: remember the file so `/reasoning` (and a click) can open it.
                _m = re.search(r"file://(\S+?\.md)", _strip_ansi(plain_message))
                if _m:
                    self._last_reasoning_path = _m.group(1)
                log.write(_think_link)
                log.write(Text(""))
                return
            if role == "status" or (plain_message.lstrip().startswith("┌") and "┐" in plain_message):
                lines = _render_status_lines(message, width=width)
            else:
                # Plan 133 F5: recolor the existing dot red on failure/interrupt,
                # green on a done line — same glyph, just the colour.
                _dot_override = _message_dot_color(plain_message)
                lines = _render_wrapped_role_lines(
                    message,
                    prefix=transcript_prefix(role),
                    prefix_color=_dot_override or transcript_prefix_colour(role),
                    body_color=transcript_body_colour(role),
                    width=width,
                    highlight_inline=transcript_highlight_inline(role),
                )
            for line in lines:
                log.write(line)
            log.write(Text(""))

        def clear_transcript(self) -> None:
            log = self.query_one("#chat-scroll", RichLog)
            log.clear()
            self._last_transcript_key = None
            self._last_trace_fingerprint = None
            self._hide_trace_panel()
            self._hide_repair_panel()
            self.clear_activity_text()
            self.clear_objective_text()

        def update_connection_label(self, label: ConnectionContextLabel) -> None:
            self._connection_label = label
            header = self.query_one("#terminal-pane-title", Static)
            header.update(label.label)
            header.styles.color = label.color

        def update_status_text(self, status_text: str) -> None:
            self._status_text = status_text
            self.query_one("#status-bar", Static).update(_render_status_bar(status_text))

        def update_footer_text(self, footer_text: str) -> None:
            self._footer_text = footer_text
            self.query_one("#footer", Static).update(_render_footer_text(footer_text))

        def attached_files(self) -> tuple[str, ...]:
            return tuple(self._attached_files)

        def consume_attached_file(self, path: str) -> None:
            normalized = str(path or "").strip()
            if not normalized:
                return
            for index, candidate in enumerate(tuple(self._attached_files)):
                if candidate == normalized:
                    del self._attached_files[index]
                    self._refresh_attachment_button()
                    break

        def run_command_in_terminal(self, command: str, *, cwd: str | None = None) -> None:
            self.close_web_preview()
            stripped = str(command or "").strip()
            if stripped.startswith("multipass shell ") and self._detected_terminal_connection == "vm":
                self.add_proactive_observation(
                    "Duckln detected the terminal is already inside a VM shell and skipped `multipass shell`."
                )
                return
            # Plan 151 F1: the pane is ALREADY inside the VM, so a host-side `multipass exec
            # <vm> -- bash -lc '<body>'` wrapper would run `multipass` INSIDE the VM (not found).
            # Strip the wrapper and send only the inner command (the existing helper does this).
            if self._detected_terminal_connection == "vm" and stripped.startswith("multipass exec "):
                from duckln.shell import _unwrap_vm_pane_command
                unwrapped = _unwrap_vm_pane_command(stripped)
                if unwrapped != stripped:
                    command = unwrapped
                    stripped = unwrapped.strip()
            # Plan 135 F5: if a command is already running in the pane, accept this one but
            # QUEUE it (don't interleave keystrokes mid-command) and tell the user; it runs
            # automatically when the terminal returns to a prompt.
            if self._terminal_busy:
                if len(self._pending_terminal_commands) < 25:
                    self._pending_terminal_commands.append((command, cwd))
                    self.add_proactive_observation(
                        f"Terminal busy — queued `{stripped}`. I'll run it as soon as the current command finishes."
                    )
                else:
                    self.add_proactive_observation(
                        "Terminal busy and the queue is full — please retry that command shortly."
                    )
                return
            self._terminal_busy = True
            self._dispatch_terminal_command(command, cwd)

        def _dispatch_terminal_command(self, command: str, cwd: str | None) -> None:
            composed = f"cd {shlex.quote(cwd)}\n{command}\n" if cwd else f"{command}\n"
            asyncio.create_task(self._send_terminal_text(composed))

        def _mark_terminal_idle(self) -> None:
            """Plan 135 F5: the pane returned to a prompt — run the next queued command, or
            clear the busy flag if the queue is empty."""
            if self._pending_terminal_commands:
                command, cwd = self._pending_terminal_commands.pop(0)
                self._terminal_busy = True
                self.add_proactive_observation(f"Terminal free — running queued `{str(command).strip()}` now.")
                self._dispatch_terminal_command(command, cwd)
            else:
                self._terminal_busy = False

        def connect_to_vm_shell(self, vm_name: str) -> None:
            """Send multipass shell <vm_name> to the terminal pane so the user lands in the VM."""
            safe_name = shlex.quote(vm_name.strip()) if vm_name.strip() else None
            if safe_name is None:
                return
            asyncio.create_task(self._send_terminal_text(f"multipass shell {safe_name}\n"))

        def begin_pane_capture(self, sentinel_id: str) -> None:
            """Item 1: register a sentinel waiter before dispatching a wrapped command."""

            self._sentinel_waiter.begin(sentinel_id)

        def dispatch_pane_capture_command(self, sentinel_id: str, command: str, *, cwd: str | None = None) -> None:
            """Item 1: send `<command>; printf 'DUCKLN-DONE-<id>:%d\\n' $?` to the pane."""

            self.close_web_preview()
            wrapped = (
                f"{command}; printf '{_DUCKLN_SENTINEL_PREFIX}{sentinel_id}:%d\\n' $?"
            )
            composed = f"cd {shlex.quote(cwd)}\n{wrapped}\n" if cwd else f"{wrapped}\n"
            asyncio.create_task(self._send_terminal_text(composed))

        def interrupt_terminal(self) -> None:
            asyncio.create_task(self._send_terminal_text("\x03"))

        def add_proactive_observation(self, message: str) -> None:
            self.append_message(message, role="proactive")

        def consume_terminal_incidents(self) -> tuple[dict[str, object], ...]:
            incidents: list[dict[str, object]] = []
            while True:
                try:
                    incidents.append(self._terminal_incident_queue.get_nowait())
                except queue.Empty:
                    break
            return tuple(incidents)

        def show_choice_overlay(
            self,
            message: str,
            choices: tuple[str, ...],
            *,
            objective_key: str | None = None,
            allow_other: bool = False,
            other_placeholder: str = "Other — type your own…",
        ) -> None:
            from rich.text import Text

            overlay = self.query_one("#choice-overlay", Vertical)
            prompt = self.query_one("#choice-prompt", Static)
            option_list = self.query_one("#choice-list", OptionList)
            self._choice_objective_key = _strip_ansi(objective_key or "").strip() or self._objective_key
            prompt.update(message)
            option_list.clear_options()
            # Plan 191 F6: NUMBERED options (1/2/3…) rendered as WRAPPING renderables so a
            # long option statement folds onto multiple lines instead of truncating.
            self._choice_choices = tuple(choices)
            display_choices = tuple(_overlay_choice_label(choice, index=i) for i, choice in enumerate(choices))
            self._choice_option_lookup = dict(zip(display_choices, choices))
            option_list.add_options(
                tuple(Text(label, overflow="fold", no_wrap=False) for label in display_choices)
            )
            option_list.action_first()
            # Plan 191 F6: the inline "Other — type your own" free-text row (opt-in).
            other = self.query_one("#choice-other", Input)
            self._choice_allow_other = bool(allow_other)
            other.value = ""
            other.placeholder = other_placeholder
            other.styles.display = "block" if allow_other else "none"
            self.query_one("#choice-hint", Static).update("Esc to cancel")
            overlay.styles.display = "block"
            self._choice_active = True
            self.update_activity_text(_choice_activity_text(message, choices), spinner=False)
            option_list.focus()
            self._mark_focus(chat=True)

        def update_activity_text(self, message: str, *, spinner: bool = False) -> None:
            normalized = _strip_ansi(message).strip()
            if normalized != self._activity_text:
                self._activity_started_at = time.monotonic() if normalized else None
            self._activity_text = normalized
            self._activity_spinner = bool(spinner)
            if not spinner:
                self._activity_spinner_index = 0
            self._update_activity_bar()

        def clear_activity_text(self) -> None:
            self._activity_text = ""
            self._activity_spinner = False
            self._activity_spinner_index = 0
            self._activity_started_at = None
            self._update_activity_bar()

        def update_objective_text(
            self,
            message: str,
            *,
            activity_message: str | None = None,
            activity_spinner: bool = False,
            objective_key: str | None = None,
        ) -> None:
            normalized_key = _strip_ansi(objective_key or "").strip() or None
            previous_key = self._objective_key
            if self._objective_key and normalized_key != self._objective_key:
                self._reset_objective_scoped_surfaces()
            self._objective_key = normalized_key
            self._objective_text = _strip_ansi(message).strip()
            normalized_activity = _strip_ansi(activity_message or "").strip()
            if normalized_activity != self._objective_activity_text:
                self._objective_activity_started_at = time.monotonic() if normalized_activity else None
            self._objective_activity_text = normalized_activity
            self._objective_activity_spinner = bool(activity_spinner and self._objective_activity_text)
            # Item B: in inline mode, post objective status to chat only on significant
            # transitions — status changes (active→decision, active→failed, key change)
            # not on phase-label changes within the same repair sequence. This prevents
            # the chat from flooding with repeated `▸ Objective:` lines during repair.
            new_sig = _objective_chat_significance(normalized_key, self._objective_text)
            if self._repair_panel_inline_mode and self._objective_text and new_sig != self._last_objective_chat_sig:
                # Plan 80 Fix 8: render the top-level objective as a prominent MAIN
                # task header so the user can tell it apart from the small sub-steps.
                try:
                    from duckln.ui import render_main_task_header
                    self.append_message(render_main_task_header(self._objective_text), role="assistant")
                except Exception:
                    self.append_message(f"▌ Main task: {self._objective_text}", role="assistant")
                self._last_objective_chat_sig = new_sig
            self._update_objective_bar()
            self._update_activity_bar()

        def clear_objective_text(self) -> None:
            self._objective_key = None
            self._objective_text = ""
            self._objective_activity_text = ""
            self._objective_activity_spinner = False
            self._objective_activity_started_at = None
            self._reset_objective_scoped_surfaces()
            self._update_objective_bar()
            self._update_activity_bar()

        def show_dependency_approval(self, request: DependencyApprovalRequest, *, objective_key: str | None = None) -> None:
            overlay = self.query_one("#approval-overlay", Vertical)
            prompt = self.query_one("#approval-prompt", Static)
            help_text = self.query_one("#approval-help", Static)
            option_list = self.query_one("#approval-list", OptionList)
            self._approval_request = request
            self._approval_active = True
            self._approval_selected_ids = {item.item_id for item in request.items}
            self._approval_objective_key = _strip_ansi(objective_key or "").strip() or self._objective_key
            prompt.update(request.prompt)
            help_text.update(
                "Review dependency, version, and source URL. Space toggles selection. Click or use Enter to approve selected."
            )
            self._show_repair_panel(
                title="Approval required",
                summary=request.prompt,
                details="\n".join(render_dependency_approval_lines(request)),
            )
            self._refresh_dependency_options(option_list)
            overlay.styles.display = "block"
            self.update_activity_text("Awaiting your approval...", spinner=False)
            option_list.focus()
            self._mark_focus(chat=True)

        def show_text_prompt_overlay(
            self,
            message: str,
            *,
            default: str = "",
            help_text: str | None = None,
            mode: str = "text",
            objective_key: str | None = None,
        ) -> None:
            overlay = self.query_one("#text-overlay", Vertical)
            prompt = self.query_one("#text-prompt", Static)
            helper = self.query_one("#text-help", Static)
            text_input = self.query_one("#text-overlay-input", Input)
            self._text_prompt_active = True
            self._text_prompt_mode = mode
            self._text_prompt_default = default
            self._text_prompt_objective_key = _strip_ansi(objective_key or "").strip() or self._objective_key
            prompt.update(message)
            helper.update(
                help_text
                or (
                    "Paste a full local file path. Example: /Users/you/Downloads/audio.mp3. Enter submits. Esc cancels."
                    if mode == "attachment"
                    else ""
                )
            )
            text_input.value = default
            # Plan 121: secret mode masks the field (and never echoes into the
            # transcript) so an API key is entered in a hidden pop-out, not the chat.
            text_input.password = (mode == "secret")
            text_input.placeholder = (
                "Type a local file path like /Users/.../audio.mp3"
                if mode == "attachment"
                else "API key (hidden) — Enter submits, Esc cancels"
                if mode == "secret"
                else "Enter the requested value"
            )
            if mode == "attachment":
                self._show_repair_panel(
                    title="Input required",
                    summary=message,
                    details=(help_text or "").strip(),
                )
            overlay.styles.display = "block"
            self.update_activity_text("Awaiting your input...", spinner=False)
            text_input.focus()
            self._mark_focus(chat=True)

        def show_attachment_overlay(self) -> None:
            attached_summary = ", ".join(Path(item).name for item in self._attached_files[-3:])
            help_text = (
                "Enter a local file path to attach. Enter 'clear' to remove all attachments."
                + (f" Current attachments: {attached_summary}" if attached_summary else "")
            )
            self.show_text_prompt_overlay(
                "Attach a local file for the next repo task:",
                default="",
                help_text=help_text,
                mode="attachment",
            )

        def show_plus_dropdown_overlay(self) -> None:
            """Plan 63 Fix 3: open the `+` dropdown with three options.
            Selecting an option routes to the corresponding action — the
            internet skill toggle, the legacy attachment flow, or a
            'coming soon' toast."""
            from duckln.glyphs import UPLOAD, DOCUMENT
            # Determine the current 'Browse the web' label (uses cached
            # internet status + the user's /internet toggle state).
            web_label = self._compose_browse_web_label()
            plan_label = self._compose_plan_mode_label()
            options: tuple[tuple[str, str], ...] = (
                (f"{UPLOAD}  Upload from computer", "upload"),
                (f"{DOCUMENT}  Add context", "context"),
                (web_label, "internet"),
                (plan_label, "plan_mode"),
            )
            overlay = self.query_one("#attach-overlay", Vertical)
            prompt = self.query_one("#attach-prompt", Static)
            option_list = self.query_one("#attach-list", OptionList)
            prompt.update("Attach or browse — arrows move, Enter selects, Escape cancels.")
            option_list.clear_options()
            self._attach_option_lookup = {}
            for label, key in options:
                option_list.add_option(label)
                self._attach_option_lookup[label] = key
            overlay.styles.display = "block"
            self._attach_active = True
            option_list.focus()

        def _compose_browse_web_label(self) -> str:
            """Build the 'Browse the web — currently ON/OFF' label using the
            nerdfont globe glyph and the live internet-status probe."""
            from duckln.glyphs import WEB_ONLINE, WEB_OFFLINE
            enabled = False
            if self._attach_config_dir is not None:
                try:
                    from duckln.internet_skill import is_internet_enabled
                    enabled = is_internet_enabled(self._attach_config_dir)
                except Exception:
                    enabled = False
            reachable = getattr(self, "_internet_status", None) == "green"
            glyph = WEB_ONLINE if (enabled and reachable) else WEB_OFFLINE
            state = "ON" if enabled else "OFF"
            extra = ""
            if enabled and not reachable:
                extra = " — unreachable"
            return f"{glyph}  Browse the web — currently {state}{extra}"

        def _compose_plan_mode_label(self) -> str:
            """Plan 68: build the 'Plan Mode — currently ON/OFF' dropdown label
            using the clipboard nerdfont glyph and the saved config flag."""
            from duckln.glyphs import PLAN_NOTE
            enabled = False
            if self._attach_config_dir is not None:
                try:
                    from duckln.config import load_app_config, resolve_config_paths

                    cfg = load_app_config(
                        resolve_config_paths({"DUCKLN_CONFIG_DIR": str(self._attach_config_dir)})
                    )
                    enabled = bool(cfg and cfg.plan_mode_enabled)
                except Exception:
                    enabled = False
            state = "ON" if enabled else "OFF"
            return f"{PLAN_NOTE}  Plan Mode — currently {state}"

        def _hide_attach_overlay(self) -> None:
            overlay = self.query_one("#attach-overlay", Vertical)
            overlay.styles.display = "none"
            self._attach_active = False
            self._attach_option_lookup = {}
            self.query_one("#chat-input", Input).focus()

        def _handle_attach_selection(self, key: str) -> None:
            """Dispatch a `+` dropdown option to its handler."""
            self._hide_attach_overlay()
            if key == "upload":
                self.show_attachment_overlay()
                return
            if key == "context":
                self.show_text_prompt_overlay(
                    "Add a context source (path or URL) for the next repo task:",
                    default="",
                    help_text="Coming soon. Press Escape to cancel.",
                    mode="context",
                )
                return
            if key == "internet":
                self._toggle_internet_skill()
                return
            if key == "plan_mode":
                self._toggle_plan_mode()
                return

        def _toggle_plan_mode(self) -> None:
            """Plan 68: flip the Plan Mode config flag and notify the user."""
            if self._attach_config_dir is None:
                self.display_message(
                    "Plan Mode toggle isn't available — config dir not set.",
                    role="system",
                )
                return
            try:
                from dataclasses import replace as _dc_replace

                from duckln.config import (
                    load_app_config,
                    resolve_config_paths,
                    save_app_config,
                )

                paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": str(self._attach_config_dir)})
                cfg = load_app_config(paths)
                if cfg is None:
                    self.display_message(
                        "Plan Mode toggle isn't available — no config saved yet.",
                        role="system",
                    )
                    return
                updated = _dc_replace(cfg, plan_mode_enabled=not cfg.plan_mode_enabled)
                save_app_config(updated, paths)
                state = "ON" if updated.plan_mode_enabled else "OFF"
                self.display_message(
                    f"Plan Mode is now {state}. "
                    + (
                        "Multi-step tasks will generate a reviewable plan first."
                        if updated.plan_mode_enabled
                        else "Tasks run directly as before."
                    ),
                    role="system",
                )
            except Exception as exc:
                self.display_message(
                    f"Could not toggle Plan Mode: {exc}",
                    role="system",
                )

        def _toggle_internet_skill(self) -> None:
            """Plan 63 Fix 3: flip the /internet toggle and notify the user."""
            try:
                from duckln.internet_skill import set_internet_enabled, is_internet_enabled
            except Exception:
                return
            if self._attach_config_dir is None:
                self.display_message(
                    "Internet skill toggle isn't available — config dir not set.",
                    role="system",
                )
                return
            try:
                current = is_internet_enabled(self._attach_config_dir)
                set_internet_enabled(self._attach_config_dir, not current)
                state = "ON" if not current else "OFF"
                self.display_message(
                    f"Internet skill is now {state}.",
                    role="system",
                )
            except Exception as exc:
                self.display_message(
                    f"Could not toggle internet skill: {exc}",
                    role="system",
                )

        def cancel_choice_overlay(self) -> None:
            self._hide_choice_overlay(None)

        def _apply_split_layout(self) -> None:
            chat_pane = self.query_one("#chat-pane", Vertical)
            terminal_pane = self.query_one("#terminal-pane", Vertical)
            divider = self.query_one("#divider", DividerHandle)
            width = max(self.size.width, 72)
            min_chat = 30
            min_terminal = 40
            if self._terminal_collapsed:
                chat_pane.styles.width = "1fr"
                terminal_pane.styles.display = "none"
                divider.styles.display = "none"
                return
            chat_width = max(min_chat, min(int(width * (self._split_percent / 100)), width - min_terminal - 1))
            terminal_width = max(min_terminal, width - chat_width - 1)
            chat_pane.styles.width = chat_width
            terminal_pane.styles.width = terminal_width
            terminal_pane.styles.display = "block"
            divider.styles.display = "block"

        def _mark_focus(self, *, chat: bool) -> None:
            chat_header = self.query_one("#chat-pane-header", Horizontal)
            terminal_header = self.query_one("#terminal-pane-header", Horizontal)
            if chat:
                chat_header.add_class("-focused")
                terminal_header.remove_class("-focused")
            else:
                terminal_header.add_class("-focused")
                chat_header.remove_class("-focused")

        async def _send_terminal_text(self, text: str) -> None:
            terminal = self.query_one("#terminal-shell", ObservableTerminal)
            if terminal.send_queue is None:
                return
            for char in text:
                await terminal.send_queue.put(["stdin", char])

        def _handle_proactive_observation(self, message: str) -> None:
            self.call_from_thread(self.add_proactive_observation, message)

        def _handle_terminal_incident(self, incident: dict[str, object]) -> None:
            payload = dict(incident)
            if threading.current_thread() is threading.main_thread():
                self._queue_terminal_incident_ui(payload)
                return
            try:
                self.call_from_thread(self._queue_terminal_incident_ui, payload)
            except RuntimeError:
                self._terminal_incident_queue.put(payload)

        def _handle_terminal_stdout(self, chunk: str) -> None:
            self._capture_terminal_urls(chunk)
            self._watcher.submit(chunk)
            # Plan 134 F1: the target CHIP is driven by the AUTHORITATIVE execution
            # target (config `execution_target` + resolved `vm_name`, via
            # `update_connection`/`_refresh_terminal_connection_label`), NOT by scraping
            # terminal stdout. Inference was wrong twice over — it only ever detected
            # "vm" (never reset to "local", so it stuck) and dropped `vm_name` (→ generic
            # "local-vm"). We still track `_detected_terminal_connection` (so the
            # "already inside a VM shell → skip `multipass shell`" optimization works),
            # but we no longer let scraped output set the chip label.
            inferred = infer_terminal_connection_from_output(chunk)
            if inferred is not None:
                self._detected_terminal_connection = inferred
            # Plan 135 F5: when the pane returns to a prompt (or a wrapped command's
            # DUCKLN-DONE marker appears) and we're waiting, drain the next queued command.
            if self._terminal_busy and _looks_like_shell_prompt(chunk):
                self._mark_terminal_idle()

        def _capture_terminal_urls(self, chunk: str) -> None:
            text = f"{self._terminal_url_carry}{chunk or ''}"
            urls = _extract_terminal_urls(text)
            if urls:
                self._last_terminal_url = urls[-1]
                # Plan 86 Fix 3: only surface APP/served URLs (not package-mirror/doc
                # hosts), and announce each unique URL once — kills apt-install spam.
                for url in urls:
                    if _is_infra_url(url) or url in self._announced_terminal_urls:
                        continue
                    self._announced_terminal_urls.add(url)
                    self._last_terminal_url_announced = url
                    self.append_message(f"Terminal link captured:\n{url}", role="status")
            tail = _strip_ansi(text)[-4096:]
            http_index = max(tail.rfind("http://"), tail.rfind("https://"))
            self._terminal_url_carry = tail[http_index:] if http_index >= 0 else ""

        def _handle_terminal_scroll_position(self, offset: int, max_offset: int) -> None:
            try:
                indicator = self.query_one("#terminal-scroll-indicator", Static)
            except Exception:
                return
            if max_offset <= 0:
                indicator.update("┃")
                indicator.remove_class("-active")
                return
            if offset > 0:
                indicator.update("█")
                indicator.add_class("-active")
            else:
                indicator.update("│")
                indicator.remove_class("-active")

        def last_terminal_url(self) -> str | None:
            return self._last_terminal_url

        def open_last_terminal_url(self) -> bool:
            if not self._last_terminal_url:
                return False
            return bool(webbrowser.open(self._last_terminal_url))

        def open_last_reasoning_file(self) -> bool:
            """Plan 141 F2 / 183 F7: open the latest logical-thinking.md (the `/reasoning` command +
            a clicked link both route here) in the OS-native TEXT editor (not a random `.md` app)."""
            path = getattr(self, "_last_reasoning_path", None)
            if not path:
                return False
            fs_path = str(path)[len("file://"):] if str(path).startswith("file://") else str(path)
            if _open_in_text_editor(fs_path):
                return True
            try:  # last-resort fallback
                return bool(webbrowser.open(f"file://{fs_path}"))
            except Exception:
                return False

        def _hide_choice_overlay(self, selected: str | None) -> None:
            overlay = self.query_one("#choice-overlay", Vertical)
            was_active = self._choice_active
            overlay.styles.display = "none"
            self._choice_active = False
            self._choice_option_lookup = {}
            self._choice_choices = ()
            self._choice_allow_other = False
            self._choice_objective_key = None
            try:
                other = self.query_one("#choice-other", Input)
                other.value = ""
                other.styles.display = "none"
            except Exception:
                pass
            self.clear_activity_text()
            self.query_one("#chat-input", Input).focus()
            if was_active:
                self._choice_result_queue.put(selected)

        def _refresh_slash_overlay(self, raw_value: str) -> None:
            suggestions = _slash_command_suggestions(raw_value)
            overlay = self.query_one("#slash-overlay", Vertical)
            prompt = self.query_one("#slash-prompt", Static)
            option_list = self.query_one("#slash-list", OptionList)
            if not suggestions:
                self._hide_slash_overlay()
                return
            self._slash_suggestions = suggestions
            self._slash_active = True
            bare_slash = " ".join(str(raw_value or "").strip().split()) == "/"
            header = (
                "Duckln commands — every main / command. Arrows move, Tab completes, Enter sends."
                if bare_slash
                else "Duckln commands — arrows move, Tab completes, Enter sends"
            )
            prompt.update(header)
            option_list.clear_options()
            option_list.add_options(tuple(_slash_command_label(item) for item in suggestions))
            option_list.action_first()
            overlay.styles.display = "block"

        def _hide_slash_overlay(self) -> None:
            overlay = self.query_one("#slash-overlay", Vertical)
            overlay.styles.display = "none"
            self._slash_active = False
            self._slash_suggestions = ()

        def _current_slash_selection(self) -> str | None:
            if not self._slash_active or not self._slash_suggestions:
                return None
            option_list = self.query_one("#slash-list", OptionList)
            highlighted = getattr(option_list, "highlighted", 0)
            if highlighted is None:
                highlighted = 0
            if not (0 <= highlighted < len(self._slash_suggestions)):
                return self._slash_suggestions[0]
            return self._slash_suggestions[highlighted]

        def _accept_slash_suggestion(self) -> None:
            selected = self._current_slash_selection()
            if selected is None:
                return
            chat_input = self.query_one("#chat-input", Input)
            chat_input.value = selected
            self._refresh_slash_overlay(selected)
            chat_input.focus()

        # --- Plan 155 F4: the `@`-mention target picker (mirrors the slash overlay) --------
        @staticmethod
        def _mention_query(raw_value: str | None) -> str | None:
            """The trailing `@token` the user is typing (e.g. '... delete @duck' → '@duck'),
            or None when the cursor isn't in an `@`-mention."""
            m = re.search(r"(?:^|\s)(@[A-Za-z0-9_.\-/]*)$", str(raw_value or ""))
            return m.group(1) if m else None

        def _refresh_mention_overlay(self, raw_value: str) -> None:
            token = self._mention_query(raw_value)
            if token is None or self._app_config_dir is None:
                self._hide_mention_overlay()
                return
            try:
                from duckln import delete_controls
                targets = delete_controls.mention_suggestions(self._app_config_dir, query=token)
            except Exception:
                targets = ()
            if not targets:
                self._hide_mention_overlay()
                return
            self._mention_targets = targets
            self._mention_active = True
            self.query_one("#mention-prompt", Static).update(
                "Pick a target — arrows move, Tab/Enter inserts @name; then type the action (e.g. delete)."
            )
            option_list = self.query_one("#mention-list", OptionList)
            option_list.clear_options()
            option_list.add_options(tuple(t.label for t in targets))
            option_list.action_first()
            self.query_one("#mention-overlay", Vertical).styles.display = "block"

        def _hide_mention_overlay(self) -> None:
            self.query_one("#mention-overlay", Vertical).styles.display = "none"
            self._mention_active = False
            self._mention_targets = ()

        def _current_mention_target(self):
            if not self._mention_active or not self._mention_targets:
                return None
            option_list = self.query_one("#mention-list", OptionList)
            highlighted = getattr(option_list, "highlighted", 0)
            if highlighted is None or not (0 <= highlighted < len(self._mention_targets)):
                return self._mention_targets[0]
            return self._mention_targets[highlighted]

        def _accept_mention_suggestion(self) -> None:
            target = self._current_mention_target()
            if target is None:
                return
            chat_input = self.query_one("#chat-input", Input)
            # Replace the trailing `@token` with the chosen `@name ` (keep the verb the user typed).
            chat_input.value = re.sub(r"(@[A-Za-z0-9_.\-/]*)$", "@" + target.name + " ", chat_input.value)
            self._hide_mention_overlay()
            chat_input.focus()

        def _tick_activity_spinner(self) -> None:
            activity_active = bool(self._activity_spinner and self._activity_text)
            objective_active = bool(self._objective_activity_spinner and self._objective_activity_text)
            if not activity_active and not objective_active:
                return
            self._activity_spinner_index = (self._activity_spinner_index + 1) % len(_SPINNER_FRAMES)
            self._activity_tick += 1  # Plan 87 Fix 3: slow, unbounded verb clock
            self._update_activity_bar()
            # Plan 183 F2: advance the header's provider-dot pulse while communicating.
            if activity_active:
                try:
                    self.update_status_text(self._status_text())
                except Exception:
                    pass

        def _update_activity_bar(self) -> None:
            try:
                bar = self.query_one("#activity-bar", Static)
            except NoMatches:
                return
            activity_text = self._activity_text or self._objective_activity_text
            activity_spinner = self._activity_spinner if self._activity_text else self._objective_activity_spinner
            started_at = self._activity_started_at if self._activity_text else self._objective_activity_started_at
            if not activity_text:
                bar.update("")
                return
            elapsed_seconds = (None if started_at is None else max(0.0, time.monotonic() - started_at))
            # Plan 164 F4: feed the latest per-call token usage so the bar shows ↑sent/↓recv
            # and how full the model's context is.
            try:
                from duckln.usage_meter import current_usage_snapshot as _usage

                _u = _usage()
                _prompt_tok = _u.last_prompt_tokens or _u.last_estimated_prompt_tokens
                _completion_tok = _u.last_completion_tokens
                _ctx = _u.context_window
                _tok_estimate = not _u.last_prompt_is_exact
            except Exception:
                _prompt_tok = _completion_tok = _ctx = 0
                _tok_estimate = False
            prefix, body, spinner_on = _activity_bar_segments(
                activity_text=activity_text,
                spinner_on=bool(activity_spinner),
                elapsed_seconds=elapsed_seconds,
                spinner_frame=_SPINNER_FRAMES[self._activity_spinner_index],
                # Plan 87 Fix 3: divide the tick so the verb dwells ~2.4s (readable),
                # while the spinner frame above still animates every 0.1s.
                spinner_index=self._activity_tick // _VERB_DWELL_TICKS,
                prompt_tokens=_prompt_tok,
                completion_tokens=_completion_tok,
                context_window=_ctx,
                tokens_are_estimate=_tok_estimate,
            )
            # Past the hard ceiling, halt the animation so it can't spin forever.
            if not spinner_on and self._activity_text:
                self._activity_spinner = False
            bar.update(Text.assemble((prefix, f"bold {DUCKLN_GOLD}"), (body, DUCKLN_SYSTEM)))
            # Plan 166: tick the "Duckln's thinking · N tokens" counter so it grows after
            # each LLM call even when no new thought line arrives (cheap header-only update).
            self._refresh_thoughts_token_header()

        def _update_objective_bar(self) -> None:
            # Item B: in inline mode, the persistent objective bar is hidden — every
            # objective transition is announced as a chat message instead. Keeps the
            # chat the single place to read live agent state (Claude-style).
            try:
                bar = self.query_one("#objective-bar", Static)
            except NoMatches:
                return
            if self._repair_panel_inline_mode:
                bar.update("")
                bar.styles.display = "none"
                return
            if not self._objective_text:
                bar.update("")
                return
            bar.update(Text.assemble(("Objective: ", f"bold {DUCKLN_GOLD}"), (self._objective_text, DUCKLN_SYSTEM)))

        def _show_repair_panel(
            self,
            *,
            title: str,
            summary: str,
            details: str,
            activity_message: str | None = None,
            activity_spinner: bool | None = None,
        ) -> None:
            # Item D: when inline mode is on, deduplicate this repair step against the
            # objective fingerprint set and append it as a chat line instead of opening
            # a modal panel. Activity-bar update keeps the spinner UX consistent.
            if self._repair_panel_objective_key != self._objective_key:
                self._repair_panel_details = ""
                self._repair_panel_fingerprints = set()
            self._repair_panel_objective_key = self._objective_key
            fingerprint = (title.strip(), summary.strip(), details.strip())
            resolved_summary = summary.strip() or title.strip() or "Duckln is working on the current repair step."
            if activity_message is None:
                activity_text, spinner = _repair_activity_update(resolved_summary, details)
            else:
                activity_text = activity_message
                spinner = True if activity_spinner is None else activity_spinner

            if self._repair_panel_inline_mode:
                if fingerprint not in self._repair_panel_fingerprints:
                    self._repair_panel_fingerprints.add(fingerprint)
                    from duckln.ui import render_step_line as _render_step_line

                    inline_line = _render_step_line(
                        f"{title.strip()} — {resolved_summary}" if title.strip() and title.strip() != resolved_summary else resolved_summary,
                        status="running",
                        detail=None,
                    )
                    self.append_message(inline_line, role="assistant")
                self.update_activity_text(activity_text, spinner=spinner)
                return

            panel = self.query_one("#repair-panel", Vertical)
            title_widget = self.query_one("#repair-title", Static)
            summary_widget = self.query_one("#repair-summary", Static)
            body_widget = self.query_one("#repair-body", Static)
            if not self._repair_panel_fingerprints:
                body_widget.update("")
            if fingerprint not in self._repair_panel_fingerprints:
                self._repair_panel_fingerprints.add(fingerprint)
                block_lines = [title.strip()]
                if details.strip():
                    block_lines.append(details.strip())
                elif summary.strip():
                    block_lines.append(summary.strip())
                block = "\n".join(line for line in block_lines if line)
                if block:
                    self._repair_panel_details = (
                        f"{self._repair_panel_details}\n\n{block}".strip()
                        if self._repair_panel_details
                        else block
                    )
                    body_widget.update(self._repair_panel_details)
            self._repair_panel_visible = True
            title_widget.update("Active repair")
            summary_widget.update(resolved_summary)
            body_widget.styles.display = "block" if self._repair_panel_expanded else "none"
            self._update_repair_toggle_label()
            panel.styles.display = "block"
            self.update_activity_text(activity_text, spinner=spinner)

        def _toggle_repair_panel(self) -> None:
            if not self._repair_panel_visible:
                return
            body_widget = self.query_one("#repair-body", Static)
            self._repair_panel_expanded = not self._repair_panel_expanded
            body_widget.styles.display = "block" if self._repair_panel_expanded else "none"
            self._update_repair_toggle_label()

        def _hide_repair_panel(self) -> None:
            panel = self.query_one("#repair-panel", Vertical)
            body_widget = self.query_one("#repair-body", Static)
            self._repair_panel_visible = False
            self._repair_panel_expanded = False
            self._repair_panel_details = ""
            self._repair_panel_fingerprints = set()
            self._repair_panel_objective_key = None
            body_widget.update("")
            body_widget.styles.display = "none"
            self._update_repair_toggle_label()
            panel.styles.display = "none"

        def _queue_terminal_incident_ui(self, incident: dict[str, object]) -> None:
            self._terminal_incident_queue.put(dict(incident))
            activity_text, spinner = _terminal_incident_activity_update(incident)
            self.update_activity_text(activity_text, spinner=spinner)

        def _update_repair_toggle_label(self) -> None:
            toggle_widget = self.query_one("#repair-toggle", Static)
            if self._repair_panel_expanded:
                toggle_widget.update("Hide details  Ctrl+R")
                return
            toggle_widget.update("Show details  Ctrl+R")

        def _show_trace_panel(self, *, title: str, summary: str, details: str) -> None:
            # Item A1: in inline mode the trace panel becomes a chat line + dropped details,
            # so the user no longer sees the modal "Show details / Dismiss" buttons.
            self._trace_panel_objective_key = self._objective_key
            if self._repair_panel_inline_mode:
                from duckln.ui import render_step_line as _render_step_line

                inline_title = title.strip().rstrip(".")
                inline_summary = summary.strip()
                # shell.command_runner emits two trace events per command (execution +
                # result). The "execution" line is redundant once the activity bar shows
                # the spinner — suppress it and let the "result" line carry the final
                # status (✓ done / ✗ failed) with the command itself as the label.
                lowered_title = inline_title.casefold()
                if lowered_title == "shell command execution":
                    try:
                        panel = self.query_one("#trace-panel", Vertical)
                        panel.styles.display = "none"
                    except Exception:
                        pass
                    self._trace_panel_visible = False
                    self._trace_panel_expanded = False
                    return
                if lowered_title == "shell command result":
                    cmd, outcome = _extract_shell_command_outcome(details)
                    label = cmd or inline_summary or inline_title
                    if outcome and "exit code 0" in outcome:
                        status = "done"
                    elif outcome and "timed out" in outcome:
                        status = "failed"
                    elif outcome:
                        status = "failed"
                    else:
                        status = "done"
                    rendered = _render_step_line(label, status=status)
                    self.clear_activity_text()
                    self.append_message(rendered, role="assistant")
                    self._trace_panel_visible = False
                    self._trace_panel_expanded = False
                    return
                line_label = f"{inline_title} — {inline_summary}" if inline_summary and inline_summary != inline_title else inline_title
                rendered = _render_step_line(line_label, status="running")
                self.append_message(rendered, role="assistant")
                # Hide the panel widget if it's still on screen from a previous session.
                try:
                    panel = self.query_one("#trace-panel", Vertical)
                    panel.styles.display = "none"
                except Exception:
                    pass
                self._trace_panel_visible = False
                self._trace_panel_expanded = False
                return

            panel = self.query_one("#trace-panel", Vertical)
            title_widget = self.query_one("#trace-title", Static)
            summary_widget = self.query_one("#trace-summary", Static)
            body_widget = self.query_one("#trace-body", Static)
            self._trace_panel_visible = True
            self._trace_panel_expanded = False
            title_widget.update(f"Duckln trace: {title}.")
            summary_widget.update(summary)
            body_widget.update(details)
            body_widget.styles.display = "none"
            self._update_trace_toggle_label()
            panel.styles.display = "block"

        def _toggle_trace_panel(self) -> None:
            if not self._trace_panel_visible:
                return
            body_widget = self.query_one("#trace-body", Static)
            self._trace_panel_expanded = not self._trace_panel_expanded
            body_widget.styles.display = "block" if self._trace_panel_expanded else "none"
            self._update_trace_toggle_label()

        def _hide_trace_panel(self) -> None:
            panel = self.query_one("#trace-panel", Vertical)
            body_widget = self.query_one("#trace-body", Static)
            self._trace_panel_visible = False
            self._trace_panel_expanded = False
            self._trace_panel_objective_key = None
            body_widget.update("")
            body_widget.styles.display = "none"
            self._update_trace_toggle_label()
            panel.styles.display = "none"

        def _reset_objective_scoped_surfaces(self) -> None:
            self._hide_choice_overlay(None)
            self._hide_repair_panel()
            self._hide_trace_panel()
            self._cancel_dependency_approval_overlay()
            self._cancel_text_prompt_overlay()

        def _update_trace_toggle_label(self) -> None:
            toggle_widget = self.query_one("#trace-toggle", Static)
            if self._trace_panel_expanded:
                toggle_widget.update("Hide details  Ctrl+E")
                return
            toggle_widget.update("Show details  Ctrl+E")

        # Plan 76 Fix E: collapsible "Duckln's thinking" box.
        # Plan 87 Fix 2: a larger window so the agent-to-agent narrative isn't truncated.
        _THOUGHTS_MAX = 40

        def add_thought(self, text: str) -> None:
            line = str(text).strip()
            if not line:
                return
            # Plan 87 Fix 4: queue, then reveal at a readable pace (drained on tick too).
            self._thoughts_pending.append(line)
            self._thoughts_expanded = True
            # Plan 87 Fix 3: keep the one-liner above the input alive while thinking.
            if not self._activity_text:
                self.update_activity_text("Duckln is working", spinner=True)
            self._release_pending_thoughts()

        def _release_pending_thoughts(self) -> None:
            now = time.monotonic()
            (
                self._thoughts_pending,
                self._thoughts,
                self._thoughts_last_release,
                changed,
            ) = _release_due_thoughts(
                self._thoughts_pending,
                self._thoughts,
                self._thoughts_last_release,
                now,
                max_visible=self._THOUGHTS_MAX,
            )
            if changed:
                self._render_thoughts_panel()
            # Plan 89: the generic "Duckln is working" keep-alive (set by add_thought)
            # must not spin forever after the work reaches a terminal state and the
            # thoughts stop. Auto-clear it once the thought stream has been idle a few
            # seconds. A real activity label set by other code paths is left alone.
            elif (
                not self._thoughts_pending
                and self._activity_text == "Duckln is working"
                and (now - self._thoughts_last_release) > _THOUGHT_IDLE_CLEAR_SECONDS
            ):
                self.clear_activity_text()

        def _refresh_thoughts_token_header(self) -> None:
            """Plan 166: update ONLY the thoughts-toggle header's token suffix (no body
            re-render → no flicker), so the live `· N tokens` counter ticks after each call."""
            if not self._thoughts:
                return
            try:
                toggle = self.query_one("#thoughts-toggle", Static)
                from duckln.usage_meter import current_usage_snapshot as _usage

                suffix = _thoughts_token_suffix(_usage().total_tokens)
            except Exception:
                return
            if self._thoughts_expanded:
                toggle.update(f"▾ Duckln's thinking{suffix}")
            else:
                toggle.update(f"▸ Duckln's thinking ({len(self._thoughts)}){suffix}")

        def clear_thoughts(self) -> None:
            self._thoughts = []
            self._thoughts_pending = []
            self._thoughts_last_release = 0.0
            self._thoughts_expanded = False
            self._render_thoughts_panel()

        def toggle_thoughts(self) -> None:
            if not self._thoughts:
                return
            self._thoughts_expanded = not self._thoughts_expanded
            self._render_thoughts_panel()

        def _render_thoughts_panel(self) -> None:
            try:
                panel = self.query_one("#thoughts-panel", Vertical)
                toggle = self.query_one("#thoughts-toggle", Static)
                body = self.query_one("#thoughts-body", Static)
            except Exception:
                return
            # Plan 166: Claude-style live token counter on the thinking line.
            try:
                from duckln.usage_meter import current_usage_snapshot as _usage

                _tok_suffix = _thoughts_token_suffix(_usage().total_tokens)
            except Exception:
                _tok_suffix = ""
            if not self._thoughts:
                panel.styles.display = "none"
                body.update("")
                toggle.update("▸ Duckln's thinking")
                return
            panel.styles.display = "block"
            if self._thoughts_expanded:
                toggle.update(f"▾ Duckln's thinking{_tok_suffix}")
                body.update("\n".join(f"· {t}" for t in self._thoughts))
                body.styles.display = "block"
            else:
                toggle.update(f"▸ Duckln's thinking ({len(self._thoughts)}){_tok_suffix}")
                body.update("")
                body.styles.display = "none"

        def _refresh_attachment_button(self) -> None:
            button = self.query_one("#attach-button", Static)
            button.update(_attachment_button_label(tuple(self._attached_files)))
            if self._attached_files:
                button.add_class("-active")
            else:
                button.remove_class("-active")

        def _refresh_dependency_options(self, option_list: OptionList | None = None) -> None:
            option_list = option_list or self.query_one("#approval-list", OptionList)
            option_list.clear_options()
            if self._approval_request is None:
                return
            rendered = [
                _approval_item_label(item, selected=item.item_id in self._approval_selected_ids)
                for item in self._approval_request.items
            ]
            option_list.add_options(rendered)
            option_list.action_first()

        def _toggle_highlighted_dependency(self) -> None:
            if self._approval_request is None:
                return
            option_list = self.query_one("#approval-list", OptionList)
            highlighted = getattr(option_list, "highlighted", 0)
            if highlighted is None:
                highlighted = 0
            if not (0 <= highlighted < len(self._approval_request.items)):
                return
            item = self._approval_request.items[highlighted]
            if item.item_id in self._approval_selected_ids:
                self._approval_selected_ids.remove(item.item_id)
            else:
                self._approval_selected_ids.add(item.item_id)
            self._refresh_dependency_options(option_list)

        def _approve_all_dependencies(self) -> None:
            if self._approval_request is None:
                return
            self._approval_selected_ids = {item.item_id for item in self._approval_request.items}
            self._finish_dependency_approval(approved=True, approve_all=True)

        def _finish_dependency_approval(self, *, approved: bool, approve_all: bool) -> None:
            overlay = self.query_one("#approval-overlay", Vertical)
            overlay.styles.display = "none"
            selected_ids = tuple(sorted(self._approval_selected_ids))
            self._approval_active = False
            self._approval_request = None
            self._approval_selected_ids = set()
            self._approval_objective_key = None
            self.clear_activity_text()
            self.query_one("#chat-input", Input).focus()
            self._approval_result_queue.put(
                DependencyApprovalDecision(
                    approved=bool(approved),
                    approve_all=bool(approved and approve_all),
                    selected_item_ids=selected_ids if approved else (),
                )
            )

        def _cancel_dependency_approval_overlay(self) -> None:
            if not self._approval_active:
                self._approval_objective_key = None
                return
            self._finish_dependency_approval(approved=False, approve_all=False)

        def _submit_text_overlay(self, raw_value: str) -> None:
            if self._text_prompt_mode == "attachment":
                submitted = str(raw_value or "").strip()
                if submitted.casefold() == "clear":
                    self._attached_files.clear()
                    self._refresh_attachment_button()
                    self.append_message("Duckln cleared the staged file attachments.", role="status")
                    self._finish_text_prompt_overlay("")
                    return
                normalized = _normalize_attachment_path(submitted)
                if normalized is None:
                    helper = self.query_one("#text-help", Static)
                    helper.update("Duckln could not find a readable file at that path. Enter a valid local file path, or Esc to cancel.")
                    return
                if normalized not in self._attached_files:
                    self._attached_files.append(normalized)
                self._refresh_attachment_button()
                self.append_message(f"Duckln attached {normalized} for the next repo task.", role="status")
                self._finish_text_prompt_overlay(normalized)
                return
            value = str(raw_value).strip() or self._text_prompt_default or None
            self._finish_text_prompt_overlay(value)

        def _finish_text_prompt_overlay(self, value: str | None) -> None:
            overlay = self.query_one("#text-overlay", Vertical)
            overlay.styles.display = "none"
            self._text_prompt_active = False
            self._text_prompt_mode = "text"
            self._text_prompt_default = ""
            self._text_prompt_objective_key = None
            # Plan 121: clear the secret value + un-mask so the next text prompt
            # isn't hidden and the key doesn't linger in the widget.
            try:
                secret_field = self.query_one("#text-overlay-input", Input)
                secret_field.password = False
                secret_field.value = ""
            except Exception:
                pass
            self.clear_activity_text()
            self.query_one("#chat-input", Input).focus()
            self._text_prompt_result_queue.put(value)

        def _cancel_text_prompt_overlay(self) -> None:
            if not self._text_prompt_active:
                self._text_prompt_objective_key = None
                return
            self._finish_text_prompt_overlay(None)

        def show_web_preview(self, url: str, *, title_hint: str | None = None) -> None:
            self._preview_url = url
            preview_pane = self.query_one("#preview-pane", Vertical)
            preview_body = self.query_one("#preview-body", Static)
            preview_title = self.query_one("#preview-title", Static)
            terminal = self.query_one("#terminal-shell", ObservableTerminal)
            preview_title.update(f"Web Preview — {title_hint or url}")
            preview_body.update("Loading preview...")
            preview_pane.styles.display = "block"
            terminal.styles.display = "none"
            asyncio.create_task(self._load_web_preview(url))

        def close_web_preview(self) -> None:
            preview_pane = self.query_one("#preview-pane", Vertical)
            terminal = self.query_one("#terminal-shell", ObservableTerminal)
            preview_pane.styles.display = "none"
            terminal.styles.display = "block"
            self._preview_url = None

        def refresh_web_preview(self) -> None:
            if not self._preview_url:
                return
            asyncio.create_task(self._load_web_preview(self._preview_url))

        async def _load_web_preview(self, url: str) -> None:
            snapshot = await asyncio.to_thread(fetch_web_preview_snapshot, url)
            preview_body = self.query_one("#preview-body", Static)
            preview_title = self.query_one("#preview-title", Static)
            preview_title.update(f"Web Preview — {snapshot.title or snapshot.final_url}")
            preview_body.update(_render_web_preview_snapshot(snapshot))


    _EXIT_SENTINEL = "\x00__DUCKLN_EXIT__\x00"

    class SplitPaneChatInterface:
        """Adapter that keeps Duckln's existing main loop while using a Textual split-pane shell."""

        def __init__(
            self,
            *,
            session_header: str,
            user_name: str = "user",
            initial_connection_type: str = "local",
            config_dir: Path | None = None,
        ) -> None:
            self._session_header = session_header
            self._user_name = user_name or "user"
            self._input_queue: "queue.Queue[str]" = queue.Queue()
            self._ready = threading.Event()
            self._app: DucklnSplitPaneApp | None = None
            self._thread: threading.Thread | None = None
            self._pending_messages: list[tuple[str, str]] = []
            self._footer_text = ""
            self._initial_connection_type = (initial_connection_type or "local").strip().lower() or "local"
            self._connection_hint = {
                "local": "Local",
                "vm": "Ubuntu VM",
                "aws": "AWS Cloud",
                "gcp": "Google Cloud",
                "docker": "Docker",
            }.get(self._initial_connection_type, "Local")
            # Connection-status indicators (orange = probing, red = offline,
            # green = healthy). Updated by the background probe thread.
            self._config_dir: Path | None = config_dir
            self._dot_provider: str = "orange"
            self._dot_target: str = "orange"
            self._dot_internet: str = "orange"
            self._probe_stop_event: threading.Event = threading.Event()
            self._probe_thread: threading.Thread | None = None
            self._startup_error: Exception | None = None
            self._fallback_chat = None
            self._browser_window = ManagedBrowserWindow(emit=self._browser_event)

        @property
        def supports_live(self) -> bool:
            return self._fallback_chat is None

        def supports_overlay(self) -> bool:
            """Plan 191 F6: True when an interactive choice overlay can render (live TUI,
            not the non-interactive fallback). The clarify engine uses this to pick radios
            vs a text-question + continue-vs-new fallback."""
            return self._fallback_chat is None and self._app_ready()

        def _start_connection_probe_thread(self) -> None:
            """Plan 62: start the background thread that refreshes connection
            dots every 30s. Safe to call multiple times — second call is no-op."""
            if self._probe_thread is not None:
                return
            self._probe_stop_event.clear()

            first_pass = [True]

            def _loop() -> None:
                from duckln.connection_status import (
                    probe_internet_status,
                    probe_provider_status,
                    probe_target_status,
                    startup_offline_notice,
                )
                from duckln.config import ConfigPaths, load_app_config
                # First pass: immediate probe so the UI shows the right colour
                # within ~2 seconds of starting (before the 30s interval fires).
                while not self._probe_stop_event.is_set():
                    try:
                        cfg = None
                        if self._config_dir is not None:
                            try:
                                cfg = load_app_config(
                                    ConfigPaths(
                                        config_dir=self._config_dir,
                                        config_file=self._config_dir / "config.json",
                                    )
                                )
                            except Exception:
                                cfg = None
                        # Plan 182 F4: pass config_dir so the header shows GREEN only when the
                        # model truly returned a live reply (verified marker), never fake-green.
                        provider_status = probe_provider_status(cfg, config_dir=self._config_dir) if cfg else "red"
                        target_status = probe_target_status(self._initial_connection_type, config_dir=self._config_dir)
                        internet_status = probe_internet_status()
                        self._dot_provider = provider_status
                        self._dot_target = target_status
                        self._dot_internet = internet_status
                        if self._app is not None and self._app.is_running:
                            try:
                                self._app.call_from_thread(
                                    self._app.update_status_text, self._status_text()
                                )
                            except Exception:
                                pass
                        # Plan 62: on the first probe iteration, if the provider
                        # is offline, surface a one-line user notice to the chat
                        # so the user knows replies will fail.
                        if first_pass[0]:
                            first_pass[0] = False
                            if cfg is not None and provider_status != "green":
                                # Plan 186 F2b: a previously-configured LOCAL Ollama is just
                                # not running yet at launch — auto-start it (safe S1, mode-gated)
                                # and re-probe so the user sees a friendly "Reconnected" instead
                                # of a scary "offline" every launch. Cloud providers: notice only.
                                reconnected = False
                                provider = getattr(cfg, "provider", None)
                                provider_value = provider.value if hasattr(provider, "value") else str(provider or "")
                                if provider_value.lower() == "ollama":
                                    try:
                                        from duckln.config import _start_ollama_runtime
                                        from duckln.ai_client import get_provider_adapter_for_base_url
                                        try:
                                            adapter = get_provider_adapter_for_base_url(
                                                provider=cfg.provider,
                                                base_url=getattr(cfg, "base_url", None),
                                            )
                                        except Exception:
                                            adapter = None
                                        # Plan 187 F1: reconnect SILENTLY — the green header dot is the
                                        # signal. Swallow the "Starting Ollama…/Ollama is up." chatter
                                        # (a no-op display); a genuine failure falls to the notice below.
                                        _start_ollama_runtime(
                                            mode=getattr(cfg, "mode", None),
                                            display=lambda _message: None,
                                            adapter=adapter,
                                        )
                                        from duckln.connection_status import clear_status_cache
                                        clear_status_cache()
                                        new_status = probe_provider_status(cfg, config_dir=self._config_dir)
                                        if new_status != "red":
                                            self._dot_provider = new_status
                                            reconnected = True
                                            # No chat line — the header dot going green says it all.
                                            if self._app is not None and self._app.is_running:
                                                try:
                                                    self._app.call_from_thread(
                                                        self._app.update_status_text, self._status_text()
                                                    )
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                if not reconnected:
                                    notice = startup_offline_notice(config=cfg)
                                    if notice:
                                        try:
                                            self.display(notice)
                                        except Exception:
                                            pass
                    except Exception:
                        pass
                    # Sleep with early-wake on stop signal.
                    self._probe_stop_event.wait(timeout=30.0)

            t = threading.Thread(target=_loop, name="duckln-connection-probe", daemon=True)
            self._probe_thread = t
            t.start()

        def _internet_dropdown_label(self) -> str:
            """Plan 63 Fix 3: build the 'Browse the web' option label for the
            `+` dropdown. Uses the nerdfont globe glyph plus an ON/OFF suffix
            that reflects both the user's `/internet` toggle and the live
            probe state."""
            from duckln.glyphs import WEB_ONLINE, WEB_OFFLINE
            from duckln.internet_skill import is_internet_enabled
            if self._config_dir is not None:
                try:
                    enabled = is_internet_enabled(self._config_dir)
                except Exception:
                    enabled = False
            else:
                enabled = False
            reachable = self._dot_internet == "green"
            glyph = WEB_ONLINE if (enabled and reachable) else WEB_OFFLINE
            state = "ON" if enabled else "OFF"
            reach_hint = ""
            if enabled and not reachable:
                reach_hint = " — unreachable"
            return f"{glyph}  Browse the web — currently {state}{reach_hint}"

        def start(self) -> None:
            if self._thread is not None:
                return

            def _run() -> None:
                try:
                    connection = build_connection_context_label(
                        connection_type=self._initial_connection_type,
                        local_label=local_connection_label(),
                    )
                    app = DucklnSplitPaneApp(
                        status_text=self._status_text(),
                        connection_label=connection,
                        shell_command=default_shell_command(),
                        input_queue=self._input_queue,
                        footer_text=self._footer_text,
                        ready_event=self._ready,
                        terminal_connection_callback=self._handle_detected_terminal_connection,
                        config_dir=self._config_dir,
                    )
                    self._app = app
                    app.run()
                except Exception as exc:  # pragma: no cover - runtime safety net
                    self._startup_error = exc
                    self._ready.set()
                finally:
                    _reset_terminal_state()

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
            self._ready.wait(timeout=10)
            # Plan 62: kick off the connection-status probe thread once the
            # Textual app is ready (or after the fallback decision is made).
            self._start_connection_probe_thread()
            if self._app is None or self._startup_error is not None or not self._ready.is_set():
                self._activate_fallback()
            if self._app is not None:
                for message, role in self._pending_messages:
                    self.display(message, role=role)
                self._pending_messages.clear()

        def _handle_detected_terminal_connection(self, connection_type: str) -> None:
            normalized_connection = str(connection_type or "").strip().lower()
            if not normalized_connection:
                return
            self._connection_hint = {
                "local": "Local",
                "vm": "Ubuntu VM",
                "aws": "AWS Cloud",
                "gcp": "Google Cloud",
                "docker": "Docker",
            }.get(normalized_connection, normalized_connection.title())
            if self._app is not None:
                try:
                    self._app.update_status_text(self._status_text())
                except Exception:
                    pass

        def stop(self) -> None:
            self._browser_window.stop()
            if self._fallback_chat is not None:
                self._fallback_chat.stop()
                self._fallback_chat = None
            if self._app is not None:
                self._app.call_from_thread(self._app.exit)
            if self._thread is not None:
                self._thread.join(timeout=2)
            # Plan 62: stop the connection-probe thread on shutdown.
            self._probe_stop_event.set()
            if self._probe_thread is not None:
                self._probe_thread.join(timeout=1)
                self._probe_thread = None
            self._thread = None
            self._app = None
            self._ready.clear()

        def display(self, message: object, *, role: str = "assistant") -> None:
            text = str(message)
            if self._fallback_chat is not None:
                self._fallback_chat.display(text, role=role)
                return
            if not self._app_ready():
                if self._app is None:
                    self._pending_messages.append((text, role))
                else:
                    self._activate_fallback()
                    self._fallback_chat.display(text, role=role)
                return
            try:
                self._app.call_from_thread(self._app.append_message, text, role=role)
            except RuntimeError:
                self._activate_fallback()
                self._fallback_chat.display(text, role=role)

        def display_step(
            self,
            step_id: str,
            label: str,
            *,
            status: str = "running",
            duration_seconds: float | None = None,
            detail: str | None = None,
        ) -> None:
            """Inline step entry. Delegates to TerminalChatInterface.display_step in fallback;
            in the live Textual app we render as a normal assistant line for v1."""

            if self._fallback_chat is not None:
                self._fallback_chat.display_step(
                    step_id,
                    label,
                    status=status,
                    duration_seconds=duration_seconds,
                    detail=detail,
                )
                return
            from duckln.ui import render_step_line as _render_step_line

            rendered = _render_step_line(label, status=status, duration_seconds=duration_seconds, detail=detail)
            self.display(rendered, role="assistant")

        def prompt(
            self,
            prompt_message: str,
            *,
            input_func: Callable[[str], str],
            record_input: bool = True,
        ) -> str:
            del prompt_message, input_func, record_input
            if self._fallback_chat is not None:
                return self._fallback_chat.prompt("", input_func=input_func, record_input=record_input)
            if not self._app_ready():
                self._activate_fallback()
                return self._fallback_chat.prompt("", input_func=input_func, record_input=record_input)
            value = self._input_queue.get()
            if value == _EXIT_SENTINEL:
                raise EOFError
            # Plan 198 F1: a NEW message means the previous operation finished (or was stopped) —
            # clear any pending abort so the next operation starts clean.
            try:
                from duckln.shell import clear_global_abort
                clear_global_abort()
            except Exception:
                pass
            return value

        def set_activity_text(self, message: str, *, spinner: bool = False) -> None:
            if self._fallback_chat is not None:
                return
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.update_activity_text, message, spinner=spinner)
            except RuntimeError:
                self._activate_fallback()

        def clear_activity_text(self) -> None:
            if self._fallback_chat is not None:
                return
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.clear_activity_text)
            except RuntimeError:
                self._activate_fallback()

        def add_thought(self, text: str) -> None:
            if self._fallback_chat is not None:
                return
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.add_thought, str(text))
            except RuntimeError:
                self._activate_fallback()

        def clear_thoughts(self) -> None:
            if self._fallback_chat is not None:
                return
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.clear_thoughts)
            except RuntimeError:
                self._activate_fallback()

        def clear_transcript(self) -> None:
            if self._fallback_chat is not None:
                self._fallback_chat.clear_transcript()
                return
            self._pending_messages.clear()
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.clear_transcript)
            except RuntimeError:
                self._activate_fallback()

        def update_objective_status(
            self,
            message: str,
            *,
            activity_message: str | None = None,
            activity_spinner: bool = False,
            objective_key: str | None = None,
        ) -> None:
            if self._fallback_chat is not None:
                self._fallback_chat.update_objective_status(
                    message,
                    activity_message=activity_message,
                    activity_spinner=activity_spinner,
                    objective_key=objective_key,
                )
                return
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(
                    self._app.update_objective_text,
                    message,
                    activity_message=activity_message,
                    activity_spinner=activity_spinner,
                    objective_key=objective_key,
                )
            except RuntimeError:
                self._activate_fallback()

        def clear_objective_status(self) -> None:
            if self._fallback_chat is not None:
                self._fallback_chat.clear_objective_status()
                return
            if not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.clear_objective_text)
            except RuntimeError:
                self._activate_fallback()

        def update_identity(self, *, session_header: str, user_name: str) -> None:
            self._session_header = session_header
            self._user_name = user_name or self._user_name
            if self._fallback_chat is not None:
                self._fallback_chat.update_identity(session_header=session_header, user_name=self._user_name)
                return
            if self._app_ready():
                try:
                    self._app.call_from_thread(self._app.update_status_text, self._status_text())
                except RuntimeError:
                    self._activate_fallback()

        def set_footer_text(self, footer_text: str) -> None:
            self._footer_text = footer_text
            if self._fallback_chat is not None:
                return
            if self._app_ready():
                try:
                    self._app.call_from_thread(self._app.update_footer_text, footer_text)
                except RuntimeError:
                    self._activate_fallback()

        def update_connection(
            self,
            *,
            connection_type: str,
            vm_name: str | None = None,
            cloud_vendor: str | None = None,
            cloud_region: str | None = None,
            cloud_shape: str | None = None,
            docker_name: str | None = None,
        ) -> None:
            normalized_connection = connection_type.strip().lower()
            # Plan 110 Fix 4: composite `location-resource` hint in the status bar.
            self._connection_hint = build_connection_context_label(
                connection_type=connection_type, local_label=local_connection_label(),
                vm_name=vm_name, cloud_vendor=cloud_vendor, cloud_region=cloud_region,
                cloud_shape=cloud_shape, docker_name=docker_name,
            ).label
            if self._fallback_chat is not None:
                return
            if not self._app_ready():
                if self._app is not None:
                    self._activate_fallback()
                return
            label = build_connection_context_label(
                connection_type=connection_type,
                local_label=local_connection_label(),
                vm_name=vm_name,
                cloud_vendor=cloud_vendor,
                cloud_region=cloud_region,
                cloud_shape=cloud_shape,
                docker_name=docker_name,
            )
            try:
                self._app.call_from_thread(self._app.update_connection_label, label)
                self._app.call_from_thread(self._app.update_status_text, self._status_text())
            except RuntimeError:
                self._activate_fallback()

        def select_choice(
            self,
            message: str,
            choices: tuple[str, ...],
            *,
            objective_key: str | None = None,
            allow_other: bool = False,
            other_placeholder: str = "Other — type your own…",
        ) -> str | None:
            if self._fallback_chat is not None or not self._app_ready():
                return None
            try:
                self._app.call_from_thread(
                    self._app.show_choice_overlay, message, choices,
                    objective_key=objective_key, allow_other=allow_other, other_placeholder=other_placeholder,
                )
            except RuntimeError:
                self._activate_fallback()
                return None
            return self._app._choice_result_queue.get()

        def confirm_choice(self, message: str, *, default: bool = True, objective_key: str | None = None) -> bool:
            yes_label = "Yes" if default else "Yes"
            no_label = "No" if default else "No"
            selected = self.select_choice(message, (yes_label, no_label), objective_key=objective_key)
            if selected is None:
                return default
            return selected == yes_label

        def approve_dependency_plan(
            self,
            request: DependencyApprovalRequest,
            *,
            objective_key: str | None = None,
        ) -> DependencyApprovalDecision:
            if self._fallback_chat is not None or not self._app_ready():
                return DependencyApprovalDecision(approved=False, approve_all=False, selected_item_ids=())
            try:
                self._app.call_from_thread(self._app.show_dependency_approval, request, objective_key=objective_key)
            except RuntimeError:
                self._activate_fallback()
                return DependencyApprovalDecision(approved=False, approve_all=False, selected_item_ids=())
            return self._app._approval_result_queue.get()

        def prompt_text(
            self,
            message: str,
            *,
            default: str = "",
            help_text: str | None = None,
            objective_key: str | None = None,
        ) -> str | None:
            if self._fallback_chat is not None or not self._app_ready():
                return None
            try:
                self._app.call_from_thread(
                    self._app.show_text_prompt_overlay,
                    message,
                    default=default,
                    help_text=help_text,
                    mode="text",
                    objective_key=objective_key,
                )
            except RuntimeError:
                self._activate_fallback()
                return None
            return self._app._text_prompt_result_queue.get()

        def prompt_secret(
            self,
            message: str,
            *,
            help_text: str | None = None,
            objective_key: str | None = None,
        ) -> str | None:
            """Plan 121: collect a secret (API key) in a MASKED pop-out overlay —
            the field is hidden and the value is never echoed into the transcript.
            Returns None in the non-live fallback so the caller uses its own path."""
            if self._fallback_chat is not None or not self._app_ready():
                return None
            try:
                self._app.call_from_thread(
                    self._app.show_text_prompt_overlay,
                    message,
                    default="",
                    help_text=help_text or "Your key is hidden and is never shown in the chat.",
                    mode="secret",
                    objective_key=objective_key,
                )
            except RuntimeError:
                self._activate_fallback()
                return None
            return self._app._text_prompt_result_queue.get()

        def list_attached_files(self) -> tuple[str, ...]:
            if self._fallback_chat is not None or not self._app_ready():
                return ()
            try:
                return self._app.attached_files()
            except RuntimeError:
                self._activate_fallback()
                return ()

        def consume_attached_file(self, path: str) -> None:
            if self._fallback_chat is not None or not self._app_ready():
                return
            try:
                self._app.call_from_thread(self._app.consume_attached_file, path)
            except RuntimeError:
                self._activate_fallback()

        def run_terminal_command(self, *, command: str, cwd: str | None = None) -> bool:
            if self._fallback_chat is not None:
                return False
            if not self._app_ready():
                return False
            try:
                self._app.call_from_thread(self._app.run_command_in_terminal, command, cwd=cwd)
            except RuntimeError:
                self._activate_fallback()
                return False
            return True

        def last_terminal_url(self) -> str | None:
            if self._fallback_chat is not None or not self._app_ready():
                return None
            try:
                return self._app.last_terminal_url()
            except RuntimeError:
                self._activate_fallback()
                return None

        def open_last_terminal_url(self) -> bool:
            if self._fallback_chat is not None or not self._app_ready():
                return False
            try:
                return bool(self._app.call_from_thread(self._app.open_last_terminal_url))
            except RuntimeError:
                self._activate_fallback()
                return False

        def open_last_reasoning_file(self) -> bool:
            """Plan 141 F2: open the latest logical-thinking.md from the `/reasoning` command."""
            if self._fallback_chat is not None or not self._app_ready():
                return False
            try:
                return bool(self._app.call_from_thread(self._app.open_last_reasoning_file))
            except RuntimeError:
                self._activate_fallback()
                return False

        def run_command_in_pane_with_capture(
            self,
            *,
            command: str,
            cwd: str | None = None,
            timeout_seconds: float = 60.0,
        ) -> tuple[bool, list[str], int]:
            """Item 1: run `command` in the pane and wait for its sentinel marker.

            Returns (completed, captured_lines, exit_code). Falls back to (False, [], -1)
            when the live Textual app isn't available — caller should use subprocess instead.
            """

            if self._fallback_chat is not None or not self._app_ready():
                return False, [], -1
            sentinel_id = uuid.uuid4().hex[:12]
            try:
                self._app.call_from_thread(self._app.begin_pane_capture, sentinel_id)
                self._app.call_from_thread(
                    self._app.dispatch_pane_capture_command, sentinel_id, command, cwd=cwd
                )
            except RuntimeError:
                self._activate_fallback()
                return False, [], -1
            return self._app._sentinel_waiter.wait(sentinel_id, timeout=timeout_seconds)

        def consume_terminal_incidents(self) -> tuple[dict[str, object], ...]:
            if self._fallback_chat is not None or not self._app_ready():
                return ()
            try:
                return self._app.consume_terminal_incidents()
            except RuntimeError:
                self._activate_fallback()
                return ()

        def open_web_preview(self, *, url: str, title_hint: str | None = None) -> bool:
            if self._browser_window.open(url=url, title_hint=title_hint):
                return True
            if self._fallback_chat is not None:
                return False
            if not self._app_ready():
                return False
            try:
                self._app.call_from_thread(self._app.show_web_preview, url, title_hint=title_hint)
            except RuntimeError:
                self._activate_fallback()
                return False
            return True

        def _browser_event(self, message: str, role: str) -> None:
            self.display(message, role=role)

        def interrupt_terminal(self) -> bool:
            if self._fallback_chat is not None:
                return False
            if not self._app_ready():
                return False
            try:
                self._app.call_from_thread(self._app.interrupt_terminal)
            except RuntimeError:
                self._activate_fallback()
                return False
            return True

        def prepare_selection_overlay(self) -> None:
            if self._fallback_chat is not None:
                self._fallback_chat.prepare_selection_overlay()
            return None

        def restore_after_selection(self) -> None:
            if self._fallback_chat is not None:
                self._fallback_chat.restore_after_selection()
            return None

        def _status_text(self):
            """Plan 63 Fix 1: build the header as a Rich ``Text`` object so the
            connection-status dots render as actual coloured glyphs.

            Returning bracket-markup (``[red]●[/red]``) printed literally
            because ``_render_status_bar`` strips ANSI then rebuilds segments
            without parsing markup. Building a Text directly bypasses that."""
            from rich.text import Text
            from duckln.connection_status import status_text_segment

            plain = re.sub(r"\x1b\[[0-9;]*m", "", self._session_header)
            def _extract(label: str) -> str | None:
                match = re.search(rf"{label}\s*:\s*([^\n│]+)", plain, re.IGNORECASE)
                if match is None:
                    return None
                value = " ".join(match.group(1).split())
                value = value.split(" /", 1)[0].split(" •", 1)[0].strip()
                return value or None

            provider = _extract("provider")
            model = _extract("model")
            mode = _extract("mode")
            user = _extract("user")

            provider_glyph, provider_style = status_text_segment(self._dot_provider)
            target_glyph, target_style = status_text_segment(self._dot_target)
            # Plan 183 F2: while a request is in-flight, PULSE the provider dot with the spinner
            # frame (in the connection colour) so the header visibly shows communication in
            # progress; it settles back to the steady dot when idle.
            if getattr(self, "_activity_spinner", False) and getattr(self, "_activity_text", ""):
                _idx = getattr(self, "_activity_spinner_index", 0)
                provider_glyph = _SPINNER_FRAMES[_idx % len(_SPINNER_FRAMES)]

            text = Text("Duckln", style="bold")

            def _sep() -> None:
                text.append(" • ", style="dim")

            if provider:
                _sep()
                text.append(provider_glyph, style=provider_style)
                text.append(f" provider {provider}")
            if model:
                _sep()
                text.append(f"model {model}")
            if mode:
                _sep()
                text.append(f"mode {mode}")
            if user:
                _sep()
                text.append(f"user {user}")
            if self._connection_hint:
                _sep()
                text.append(target_glyph, style=target_style)
                text.append(f" {self._connection_hint}")
            # Plan 67: Plan Mode header indicator.
            plan_info = _plan_mode_header_segment(self._config_dir)
            if plan_info is not None:
                plan_glyph, plan_style, plan_label = plan_info
                _sep()
                text.append(plan_glyph, style=plan_style)
                text.append(f" {plan_label}")
            text.truncate(260)
            return text

        def _app_ready(self) -> bool:
            return self._app is not None and self._app.is_running and self._ready.is_set()

        def _activate_fallback(self) -> None:
            if self._fallback_chat is not None:
                return
            _reset_terminal_state()
            from duckln.ui import TerminalChatInterface

            fallback = TerminalChatInterface(session_header=self._session_header, user_name=self._user_name)
            fallback.start()
            if self._startup_error is not None:
                fallback.display(
                    "Duckln could not start the split-pane terminal UI cleanly, so it fell back to the stable chat view."
                )
            self._fallback_chat = fallback
            pending = list(self._pending_messages)
            self._pending_messages.clear()
            for message, role in pending:
                fallback.display(message, role=role)
