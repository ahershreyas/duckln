"""Terminal UI helpers for branding, prompts, and output formatting."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Callable

try:
    from rich.console import Console
except ModuleNotFoundError:
    Console = None


DUCKLN_GOLD = "#F5A623"
DUCKLN_COMMAND = "#00FFFF"
DUCKLN_USER_INPUT = "#F0EAD6"
DUCKLN_SYSTEM = "#A9A9A9"
DUCKLN_GREEN = "#4CAF50"
DUCKLN_RED = "#E53935"
DUCKLN_ORANGE = "#FF6D00"
DUCKLN_GREY = "#616161"
_ANSI_BY_COLOR = {
    DUCKLN_GOLD: "\033[38;2;245;166;35m",
    DUCKLN_COMMAND: "\033[38;2;0;255;255m",
    DUCKLN_USER_INPUT: "\033[38;2;240;234;214m",
    DUCKLN_SYSTEM: "\033[38;2;169;169;169m",
    DUCKLN_GREEN: "\033[38;2;76;175;80m",
    DUCKLN_RED: "\033[38;2;229;57;53m",
    DUCKLN_ORANGE: "\033[38;2;255;109;0m",
    DUCKLN_GREY: "\033[38;2;97;97;97m",
}
_ANSI_RESET = "\033[0m"
COMPACT_BANNER = "\n".join(
    (
        ">(.)__ <  Duckln",
        " (___/    AI Terminal Mentor",
    )
)
_COMMAND_PATTERN = re.compile(r"(?<!\S)(/[A-Za-z]+(?:\s+[A-Za-z]+)?)")


@lru_cache(maxsize=1)
def load_banner_asset() -> str:
    """Load the Duckln banner from the bundled documentation asset."""

    asset_path = Path(__file__).resolve().parents[2] / "docs" / "assets" / "duckln-banner.txt"
    return asset_path.read_text(encoding="utf-8").strip("\n")


def render_banner(width: int) -> str:
    """Render the full banner when it fits, otherwise fall back to the compact duck."""

    banner = load_banner_asset()
    widest_line = max(len(line) for line in banner.splitlines())
    body = banner if width >= widest_line else COMPACT_BANNER
    return _style_text(body, DUCKLN_GOLD)


def render_session_header(
    *,
    provider: str,
    model: str,
    mode: str,
    user_name: str,
    memory_state: str,
    width: int = 80,
) -> str:
    """Render the compact startup header with config and command hints."""

    content_width = max(46, min(width, 80) - 4)
    rows = (
        _style_text("◆ Duckln", DUCKLN_GOLD),
        "",
        _header_row("provider", provider, "/provider to change", content_width),
        _header_row("model", model, "/model to change", content_width),
        _header_row("mode", mode, "/mode to change", content_width),
        _header_row("user", user_name, "", content_width),
        _header_row("memory", memory_state, "", content_width),
    )
    top = _style_text(f"┌{'─' * (content_width + 2)}┐", DUCKLN_GREY)
    bottom = _style_text(f"└{'─' * (content_width + 2)}┘", DUCKLN_GREY)
    body = "\n".join(
        f"{_style_text('│', DUCKLN_GREY)} {_pad_styled_row(row, content_width)} {_style_text('│', DUCKLN_GREY)}"
        for row in rows
    )
    return "\n".join((top, body, bottom))


def _header_row(label: str, value: str, hint: str, width: int) -> str:
    styled_label = _style_text(f"{label:<8}", DUCKLN_GOLD)
    left_text = f"{label:<8} : {value}"
    left = f"{styled_label} : {value}"
    if not hint:
        return _truncate_styled_row(left, left_text, width)
    hint_width = max(0, width - min(len(left_text), width) - 2)
    trimmed_hint = hint[:hint_width]
    if not trimmed_hint:
        return _truncate_styled_row(left, left_text, width)
    spacing = max(2, width - min(len(left_text), width) - len(trimmed_hint))
    return f"{_truncate_styled_row(left, left_text, width)}{' ' * spacing}{_style_text(trimmed_hint, DUCKLN_COMMAND)}"


def build_terminal_display() -> Callable[[str], None]:
    """Return the default Rich-backed terminal printer."""

    if Console is None:
        def display(message: str) -> None:
            print(_style_terminal_message(message))

        return display

    console = Console(highlight=False, force_terminal=True, color_system="truecolor")

    def display(message: str) -> None:
        console.print(_style_terminal_message(message), markup=True)

    return display


def build_terminal_input() -> Callable[[str], str]:
    """Return the default Rich-backed input prompt."""

    user_input_prefix = _ANSI_BY_COLOR[DUCKLN_USER_INPUT]

    def prompt(message: str) -> str:
        try:
            return input(f"{user_input_prefix}{message}")
        finally:
            print(_ANSI_RESET, end="")

    return prompt


def _style_terminal_message(message: str) -> str:
    if not message:
        return ""
    if "\n" in message or "┌" in message or message.startswith("[") or "\033[" in message:
        return _style_commands(message)
    if message.startswith("Retryable error:") or message.startswith("✗"):
        return _style_commands(message, base_color=DUCKLN_RED)
    if message.startswith("Warning:") or message.startswith("⚠"):
        return _style_commands(message, base_color=DUCKLN_ORANGE)
    if message.startswith("✓") or " is ready" in message or " completed" in message or " connection ready" in message:
        return _style_commands(message, base_color=DUCKLN_GREEN)
    if message.endswith("...") or message.startswith("Checking ") or message.startswith("Starting ") or message.startswith("Reading ") or message.startswith("Cloning ") or message.startswith("Working on:"):
        return _style_commands(message, base_color=DUCKLN_SYSTEM)
    return _style_commands(message, base_color=DUCKLN_SYSTEM)


def _style_text(text: str, color: str) -> str:
    if Console is None:
        return f"{_ANSI_BY_COLOR[color]}{text}{_ANSI_RESET}"
    return f"[{color}]{text}[/]"


def _style_commands(message: str, *, base_color: str | None = None) -> str:
    styled_chunks: list[str] = []
    cursor = 0
    for match in _COMMAND_PATTERN.finditer(message):
        if match.start() > cursor:
            prefix = message[cursor : match.start()]
            styled_chunks.append(_style_text(prefix, base_color) if base_color else prefix)
        styled_chunks.append(_style_text(match.group(1), DUCKLN_COMMAND))
        cursor = match.end()
    if cursor < len(message):
        suffix = message[cursor:]
        styled_chunks.append(_style_text(suffix, base_color) if base_color else suffix)
    return "".join(styled_chunks)


def _strip_markup(text: str) -> str:
    return re.sub(r"\[[#A-Za-z0-9/]+\]|\033\[[0-9;]*m", "", text)


def _truncate_styled_row(styled_text: str, plain_text: str, width: int) -> str:
    if len(plain_text) <= width:
        return styled_text
    trimmed_plain = plain_text[:width]
    if plain_text.startswith(trimmed_plain):
        return _style_text(trimmed_plain[:8], DUCKLN_GOLD) + trimmed_plain[8:]
    return trimmed_plain


def _pad_styled_row(styled_text: str, width: int) -> str:
    plain_length = len(_strip_markup(styled_text))
    return styled_text + (" " * max(0, width - plain_length))
