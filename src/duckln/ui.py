"""Terminal UI helpers for branding, prompts, and output formatting."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import readline
import shutil
import sys
import textwrap
from typing import Callable

from duckln import __version__
from duckln.browser_runtime import ManagedBrowserWindow
from duckln.constants import (
    COLOUR_COMMAND,
    COLOUR_ERROR,
    COLOUR_GOLD,
    COLOUR_GREY,
    COLOUR_SUCCESS,
    COLOUR_SYSTEM,
    COLOUR_USER_INPUT,
    COLOUR_WARNING,
    SLASH_COMMANDS,
)
from duckln.render_blocks import render_block_text
from duckln.selections import ConversationChoicePrompt
from duckln.textual_ui import (
    SplitPaneChatInterface,
    _is_repair_adjacent_trace_title,
    _is_repair_trace_title,
    _parse_trace_message,
    _repair_session_message_payload,
    split_pane_dependencies_available,
)
from duckln.transcript_styles import (
    transcript_body_colour,
    transcript_prefix,
    transcript_prefix_colour,
)

try:
    from rich import box
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ModuleNotFoundError:
    Console = None
    Group = None
    Layout = None
    Live = None
    Panel = None
    Table = None
    Text = None
    box = None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
except ModuleNotFoundError:
    FormattedText = None
    HTML = None
    KeyBindings = None
    PromptSession = None
    Style = None


DUCKLN_GOLD = COLOUR_GOLD
DUCKLN_COMMAND = COLOUR_COMMAND
DUCKLN_USER_INPUT = COLOUR_USER_INPUT
DUCKLN_SYSTEM = COLOUR_SYSTEM
DUCKLN_GREEN = COLOUR_SUCCESS
DUCKLN_RED = COLOUR_ERROR
DUCKLN_ORANGE = COLOUR_WARNING
DUCKLN_GREY = COLOUR_GREY
_ANSI_BY_COLOR = {
    DUCKLN_GOLD: "\033[38;2;245;166;35m",
    DUCKLN_COMMAND: "\033[38;2;0;255;255m",
    DUCKLN_USER_INPUT: "\033[38;2;255;255;255m",
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
_HEADER_LABEL_WIDTH = 8
_SORTED_SLASH_COMMANDS = tuple(sorted(SLASH_COMMANDS, key=len, reverse=True))
_COMMAND_PATTERN = re.compile("(" + "|".join(re.escape(command) for command in _SORTED_SLASH_COMMANDS) + ")")


@dataclass(frozen=True)
class ChatMessage:
    """A rendered transcript entry."""

    role: str
    content: object


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
    target: str = "Local",
    width: int = 80,
) -> str:
    """Render the compact startup header."""

    return _fallback_session_header(
        provider=provider,
        model=model,
        mode=mode,
        user_name=user_name,
        memory_state=memory_state,
        target=target,
        width=width,
    )


def render_first_run_safety_panel() -> str:
    """Render the first-run safety panel with Duckln branding."""

    rows = (
        _style_text("┌────────────────────────────────────────────────────────────┐", DUCKLN_GREY),
        f"{_style_text('│', DUCKLN_GREY)} {_style_text('◆ Duckln', DUCKLN_GOLD)}{_style_text(' Safety & Permissions', DUCKLN_SYSTEM)}{' ' * 32}{_style_text('│', DUCKLN_GREY)}",
        f"{_style_text('│', DUCKLN_GREY)} {_style_text('Duckln can run terminal commands, read project files,', DUCKLN_SYSTEM)}{' ' * 8}{_style_text('│', DUCKLN_GREY)}",
        f"{_style_text('│', DUCKLN_GREY)} {_style_text('and install dependencies on your machine.', DUCKLN_SYSTEM)}{' ' * 18}{_style_text('│', DUCKLN_GREY)}",
        f"{_style_text('│', DUCKLN_GREY)} {_style_text('HOTL asks before execution; HOOTLWO runs safe commands.', DUCKLN_SYSTEM)}{' ' * 3}{_style_text('│', DUCKLN_GREY)}",
        f"{_style_text('│', DUCKLN_GREY)} {_style_text('Destructive commands are blocked.', DUCKLN_SYSTEM)}{' ' * 28}{_style_text('│', DUCKLN_GREY)}",
        _style_text("└────────────────────────────────────────────────────────────┘", DUCKLN_GREY),
    )
    return "\n".join(rows)


def build_terminal_display() -> Callable[[str], None]:
    """Return the default Rich-backed terminal printer."""

    if Console is None:
        def display(message: object) -> None:
            print(_style_terminal_message(render_block_text(message)))

        return display

    console = Console(highlight=False, force_terminal=True, color_system="truecolor")

    def display(message: object) -> None:
        rendered = render_block_text(message)
        if "\033[" in rendered and Text is not None:
            console.print(Text.from_ansi(rendered))
            return
        console.print(_style_terminal_message(rendered), markup=True)

    return display


def build_terminal_input() -> Callable[[str], str]:
    """Return the default Rich-backed input prompt."""

    def prompt(message: str) -> str:
        try:
            return input(render_input_prompt(message))
        finally:
            print(_ANSI_RESET, end="")

    return prompt


def build_chat_input() -> Callable[[str], str]:
    """Return a no-prompt input callable for the fixed chat composer."""

    def prompt(_: str) -> str:
        try:
            try:
                readline.parse_and_bind("set horizontal-scroll-mode on")
            except Exception:
                pass
            return input(f"{_ANSI_BY_COLOR[DUCKLN_USER_INPUT]}> ")
        finally:
            print(_ANSI_RESET, end="", flush=True)

    return prompt


def render_input_prompt(message: str) -> str:
    """Render the shell prompt with a gold prompt marker and eggshell input color."""

    if message == "duckln> ":
        return (
            f"{_ANSI_BY_COLOR[DUCKLN_GOLD]}duckln>"
            f"{_ANSI_BY_COLOR[DUCKLN_USER_INPUT]} "
        )
    return f"{_ANSI_BY_COLOR[DUCKLN_USER_INPUT]}{message}"


def render_input_footer(
    *,
    execution_label: str | None = None,
    usage_summary: str | None = None,
) -> str:
    """Render the compact input-area hint line."""

    parts = [
        f"{_style_text('/help', DUCKLN_COMMAND)}"
        f"{_style_text(' for commands', DUCKLN_SYSTEM)}",
        f"{_style_text('Enter', DUCKLN_USER_INPUT)}"
        f"{_style_text(' to send', DUCKLN_SYSTEM)}",
        f"{_style_text('Ctrl+C', DUCKLN_USER_INPUT)}"
        f"{_style_text(' to exit', DUCKLN_SYSTEM)}",
    ]
    if execution_label:
        parts.append(f"{_style_text('target', DUCKLN_SYSTEM)} {_style_text(execution_label, DUCKLN_COMMAND)}")
    if usage_summary:
        parts.append(f"{_style_text('LLM', DUCKLN_SYSTEM)} {_style_text(usage_summary, DUCKLN_USER_INPUT)}")
    return _style_text(" • ", DUCKLN_GREY).join(parts)


_STEP_GLYPHS = {
    "running": "◐",
    "done": "✓",
    "failed": "✗",
    "skipped": "·",
}


def render_main_task_header(text: str) -> str:
    """Plan 80 Fix 8: a prominent header marking the MAIN task, so the user can tell
    it apart from the small sub-steps nested under it."""
    return _style_text(f"▌ Main task: {text.strip()}", DUCKLN_GOLD)


def format_thought_for_seconds(seconds: float) -> str:
    """Plan 80 Fix 8: 'Thought for Ns' / 'Thought for Nm Ms' (Claude/Codex-style)."""
    total = int(max(0, round(seconds)))
    if total < 60:
        return f"Thought for {total}s"
    return f"Thought for {total // 60}m {total % 60}s"


def render_step_line(
    label: str,
    *,
    status: str = "running",
    duration_seconds: float | None = None,
    detail: str | None = None,
    indent: int = 0,
) -> str:
    """Render a single step line: `<glyph> <label>` with optional `(Ns)` or `— detail`.

    Item A2: completed steps render with a strikethrough on the label so finished work
    visually recedes (Claude-style checklist UX). Failed steps stay bold so the user's
    eye lands on the broken step. Plan 80 Fix 8: `indent` nests sub-steps under the
    main task with a tree prefix.
    """

    normalized = (status or "running").strip().lower()
    glyph = _STEP_GLYPHS.get(normalized, "◐")
    glyph_color = {
        "running": DUCKLN_GOLD,
        "done": DUCKLN_GREEN,
        "failed": DUCKLN_RED,
        "skipped": DUCKLN_GREY,
    }.get(normalized, DUCKLN_GOLD)
    label_color = {
        "running": DUCKLN_USER_INPUT,
        "done": DUCKLN_GREY,
        "failed": DUCKLN_RED,
        "skipped": DUCKLN_GREY,
    }.get(normalized, DUCKLN_USER_INPUT)
    label_text = label.strip()
    if normalized == "done":
        # ANSI strikethrough = ESC[9m … ESC[29m. Most modern terminals respect it.
        styled_label = f"\033[9m{_style_text(label_text, label_color)}\033[29m"
    elif normalized == "skipped":
        styled_label = f"\033[9m{_style_text(label_text, label_color)}\033[29m"
    else:
        styled_label = _style_text(label_text, label_color)
    prefix = _style_text("  ├─ ", DUCKLN_SYSTEM) if indent else ""
    parts = [prefix, _style_text(glyph, glyph_color), _style_text(" ", DUCKLN_SYSTEM), styled_label]
    if duration_seconds is not None and duration_seconds >= 0:
        if duration_seconds < 60:
            human = f"{duration_seconds:.1f}s"
        else:
            minutes = int(duration_seconds // 60)
            seconds = int(duration_seconds % 60)
            human = f"{minutes}m{seconds:02d}s"
        parts.append(_style_text(f" ({human})", DUCKLN_SYSTEM))
    if detail:
        parts.append(_style_text(f" — {detail.strip()}", DUCKLN_SYSTEM))
    return "".join(parts)


def render_edit_card(
    path: str,
    *,
    added: int,
    removed: int,
    created: bool = False,
    diff: str | None = None,
) -> str:
    """Plan 133 F7b: a Claude-style edit card — `✎ Edit <path>  +N -M` with an optional
    indented, colorized diff body (green +adds / red -removes) shown when expanded.

    The header line alone is the collapsed card; passing `diff` appends the expandable
    body. Mirrors render_step_line's glyph+label styling so the timeline stays consistent.
    """
    verb = "Create" if created else "Edit"
    glyph = _style_text("✎", DUCKLN_GOLD)
    label = _style_text(f"{verb} {path.strip()}", DUCKLN_USER_INPUT)
    counts = []
    if added:
        counts.append(_style_text(f"+{added}", DUCKLN_GREEN))
    if removed:
        counts.append(_style_text(f"-{removed}", DUCKLN_RED))
    count_text = (" " + " ".join(counts)) if counts else ""
    header = f"{glyph} {label}{count_text}"
    if not diff:
        return header
    body_lines = []
    for raw in diff.splitlines():
        if raw.startswith("+"):
            body_lines.append(_style_text(f"  {raw}", DUCKLN_GREEN))
        elif raw.startswith("-"):
            body_lines.append(_style_text(f"  {raw}", DUCKLN_RED))
        elif raw.startswith("@@"):
            body_lines.append(_style_text(f"  {raw}", DUCKLN_SYSTEM))
        else:
            body_lines.append(_style_text(f"  {raw}", DUCKLN_GREY))
    return header + "\n" + "\n".join(body_lines)


def render_status_box(message: str, *, title: str = "Thinking…", width: int = 72) -> str:
    """Render a compact boxed status update for non-trivial work."""

    content_width = max(28, min(width, 72))
    return _fallback_status_box(message=message, title=title, width=content_width)


def render_conversation_choice_prompt(
    prompt: ConversationChoicePrompt,
    *,
    width: int = 72,
) -> str:
    """Render a readable bounded choice prompt for the transcript."""

    content_width = max(32, min(width, 72))
    wrapped_prompt = textwrap.wrap(prompt.message, width=content_width) or [prompt.message]
    lines = [wrapped_prompt[0]]
    lines.extend(wrapped_prompt[1:])
    for option in prompt.options:
        marker = "◆" if option.recommended else "○"
        label = f"{marker} {option.label}"
        wrapped = textwrap.wrap(label, width=content_width) or [label]
        lines.append(wrapped[0])
        lines.extend(f"  {line}" for line in wrapped[1:])
        if option.description:
            detail_lines = textwrap.wrap(option.description, width=max(20, content_width - 2)) or [option.description]
            lines.extend(f"  {line}" for line in detail_lines)
    return "\n".join(lines)


class TerminalChatInterface:
    """Persistent header/transcript/input terminal layout."""

    def __init__(self, *, session_header: str, user_name: str = "user") -> None:
        self._session_header = session_header
        self._user_name = user_name or "user"
        self._messages: list[ChatMessage] = []
        self._console = (
            None
            if Console is None
            else Console(highlight=False, force_terminal=True, color_system="truecolor")
        )
        self._live = None
        self._started = False
        self._prompt_message = ""
        self._input_active = False
        self._header_shown = False
        self._fallback_header_rendered = False
        self._browser_window = ManagedBrowserWindow(emit=self._browser_event)
        self._repair_surface_index: int | None = None
        self._repair_surface_fingerprints: set[tuple[str, str]] = set()
        self._repair_surface_labels: list[str] = []
        self._active_objective_key: str | None = None
        self._repair_surface_objective_key: str | None = None
        # Item 2: inline step messages keyed by step_id so they can update in place.
        self._step_message_index: dict[str, int] = {}

    @property
    def supports_live(self) -> bool:
        return False

    @property
    def messages(self) -> tuple[ChatMessage, ...]:
        return tuple(self._messages)

    def mark_header_rendered(self) -> None:
        """Tell the fallback renderer the session header is already on screen."""

        self._fallback_header_rendered = True

    def update_identity(self, *, session_header: str, user_name: str) -> None:
        """Refresh the visible header and stored user label for the current session."""

        self._session_header = session_header
        self._user_name = user_name or self._user_name
        if self.supports_live:
            self.refresh()
        else:
            self._fallback_header_rendered = False
            self._fallback_redraw()

    def clear_transcript(self) -> None:
        """Clear only the visible conversation surface, not Duckln memory."""

        self._messages.clear()
        self._repair_surface_index = None
        self._repair_surface_fingerprints.clear()
        self._repair_surface_labels.clear()
        self._repair_surface_objective_key = None
        self._active_objective_key = None
        self._step_message_index.clear()
        self._fallback_header_rendered = False
        self._fallback_redraw()

    def start(self) -> None:
        if self._started:
            return
        if self.supports_live:
            self._live = Live(
                self._build_layout(),
                console=self._console,
                screen=True,
                refresh_per_second=8,
                transient=False,
            )
            self._live.start()
            self.refresh()
        self._started = True

    def stop(self) -> None:
        self._browser_window.stop()
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._started = False

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._build_layout(), refresh=True)

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
        """Fallback chat interface: record the connection hint so future header reprints reflect it."""

        self._connection_hint = {
            "local": "Local",
            "vm": "Ubuntu VM",
            "aws": "AWS Cloud",
            "gcp": "Google Cloud",
            "docker": "Docker",
        }.get((connection_type or "").strip().lower(), "Local")

    def display_step(
        self,
        step_id: str,
        label: str,
        *,
        status: str = "running",
        duration_seconds: float | None = None,
        detail: str | None = None,
    ) -> None:
        """Append or update an inline step entry. status ∈ {running, done, failed}.

        Steps render as `◐ Label`, `✓ Label (1.2s)`, or `✗ Label — detail`. Updating an
        existing step_id replaces the rendered line in place rather than appending a new
        message, so a sequence reads as a checklist.
        """

        rendered = render_step_line(label, status=status, duration_seconds=duration_seconds, detail=detail)
        existing_index = self._step_message_index.get(step_id)
        if existing_index is not None and 0 <= existing_index < len(self._messages):
            self._messages[existing_index] = ChatMessage(role="assistant", content=rendered)
        else:
            self._messages.append(ChatMessage(role="assistant", content=rendered))
            self._step_message_index[step_id] = len(self._messages) - 1
        if self.supports_live:
            self.refresh()
        else:
            self._fallback_redraw()

    def display(self, message: object, *, role: str | None = None) -> None:
        resolved_role = role or ("status" if _looks_like_status_box(message) else "assistant")
        if self._try_update_repair_surface(message, role=resolved_role):
            if self.supports_live:
                self.refresh()
            else:
                self._fallback_redraw()
            return
        if resolved_role in {"assistant", "status", "proactive"} and self._active_objective_key is None:
            self._reset_repair_surface_state()
        self._messages.append(ChatMessage(role=resolved_role, content=message))
        if self.supports_live:
            self.refresh()
        else:
            self._fallback_redraw()

    def open_web_preview(self, *, url: str, title_hint: str | None = None) -> bool:
        return self._browser_window.open(url=url, title_hint=title_hint)

    def _browser_event(self, message: str, role: str) -> None:
        self.display(message, role=role)

    def _try_update_repair_surface(self, message: object, *, role: str) -> bool:
        rendered = render_block_text(message)
        plain = _strip_markup(rendered).strip()
        objective_key = self._active_objective_key
        trace_payload = _parse_trace_message(rendered) if role in {"assistant", "status"} else None
        if trace_payload is not None:
            title, summary, details = trace_payload
            if _is_repair_trace_title(title) or (
                self._repair_surface_index is not None
                and self._repair_surface_objective_key == objective_key
                and _is_repair_adjacent_trace_title(title)
            ):
                detail_block = f"{title}\n{details or summary}".strip()
                self._update_repair_surface(
                    summary=summary.strip() or title.strip(),
                    details=detail_block,
                    label=title.strip(),
                    objective_key=objective_key,
                )
                return True
        if role in {"assistant", "status", "proactive"}:
            repair_payload = _repair_session_message_payload(plain)
            if repair_payload is not None:
                summary, details = repair_payload
                if (
                    self._repair_surface_index is not None
                    or objective_key is not None
                    or not summary.startswith("Official docs checked")
                ):
                    self._update_repair_surface(
                        summary=summary.strip(),
                        details=details.strip(),
                        label=summary.strip(),
                        objective_key=objective_key,
                    )
                    return True
        if (
            role == "status"
            and self._repair_surface_index is not None
            and self._repair_surface_objective_key == objective_key
            and _looks_like_status_box(rendered)
        ):
            status_title, status_text = _status_payload_from_box(rendered)
            if status_title == "Supervisor agent":
                normalized = status_text.strip()
                self._update_repair_surface(
                    summary=normalized or "Supervisor agent update",
                    details=f"{status_title}\n{normalized}".strip(),
                    label=normalized or status_title,
                    objective_key=objective_key,
                )
                return True
        return False

    def _update_repair_surface(self, *, summary: str, details: str, label: str, objective_key: str | None) -> None:
        fingerprint = (summary.strip(), details.strip())
        if objective_key != self._repair_surface_objective_key:
            self._reset_repair_surface_state()
        if self._repair_surface_index is None:
            self._messages.append(ChatMessage(role="assistant", content=""))
            self._repair_surface_index = len(self._messages) - 1
            self._repair_surface_fingerprints = set()
            self._repair_surface_labels = []
            self._repair_surface_objective_key = objective_key
        if fingerprint not in self._repair_surface_fingerprints:
            self._repair_surface_fingerprints.add(fingerprint)
            normalized_label = label.strip()
            if normalized_label and normalized_label not in self._repair_surface_labels:
                self._repair_surface_labels.append(normalized_label)
        self._messages[self._repair_surface_index] = ChatMessage(
            role="assistant",
            content=_render_repair_surface_message(
                summary=summary,
                labels=tuple(self._repair_surface_labels),
            ),
        )

    def _reset_repair_surface_state(self) -> None:
        self._repair_surface_index = None
        self._repair_surface_fingerprints = set()
        self._repair_surface_labels = []
        self._repair_surface_objective_key = None

    def update_objective_status(
        self,
        message: str,
        *,
        activity_message: str | None = None,
        activity_spinner: bool = False,
        objective_key: str | None = None,
    ) -> None:
        del message, activity_message, activity_spinner
        normalized_key = str(objective_key or "").strip() or None
        if self._active_objective_key and normalized_key != self._active_objective_key:
            self._reset_repair_surface_state()
        self._active_objective_key = normalized_key

    def clear_objective_status(self) -> None:
        self._active_objective_key = None
        self._reset_repair_surface_state()

    def prompt(
        self,
        prompt_message: str,
        *,
        input_func: Callable[[str], str],
        record_input: bool = True,
    ) -> str:
        self._prompt_message = prompt_message
        self._input_active = True
        if self._live is not None:
            self.refresh()
            self._live.stop()
            self._clear_live_input_area()
        try:
            if self.supports_live:
                value = input_func(prompt_message)
            else:
                self._fallback_redraw(input_active=True)
                value = input_func("")
        finally:
            if self.supports_live:
                if self._live is None:
                    self._live = Live(
                        self._build_layout(),
                        console=self._console,
                        screen=True,
                        refresh_per_second=8,
                        transient=False,
                    )
                    self._live.start()
                self.refresh()
        self._input_active = False
        if record_input and value.strip():
            self._messages.append(ChatMessage(role=self._user_name, content=value.strip()))
            self.refresh()
        elif not self.supports_live:
            self._fallback_redraw()
        return value

    def _build_layout(self):
        layout = Layout(name="root")
        header_height = max(3, len(_strip_markup(self._session_header).splitlines()))
        input_height = 4
        layout.split_column(
            Layout(self._render_header(), name="header", size=header_height),
            Layout(self._render_transcript_panel(), name="transcript", ratio=1),
            Layout(self._render_input_panel(), name="input", size=input_height),
        )
        return layout

    def _render_header(self):
        self._header_shown = True
        return _ansi_to_rich(self._session_header)

    def _render_transcript_panel(self):
        transcript_messages = self._visible_transcript_messages()
        if not transcript_messages:
            body = Text("", style=DUCKLN_SYSTEM)
        else:
            body = Group(*[_render_chat_message(message) for message in transcript_messages])
        return body

    def _render_input_panel(self):
        input_box = Panel(
            Text(" ", style=DUCKLN_USER_INPUT),
            title=f"[{DUCKLN_SYSTEM}]Send a message[/]",
            title_align="left",
            box=box.SQUARE,
            border_style=DUCKLN_GREY,
            padding=(0, 1),
        )
        footer_line = _ansi_to_rich(render_input_footer())
        return Group(input_box, footer_line)

    def _visible_transcript_messages(self) -> tuple[ChatMessage, ...]:
        if self._console is None:
            return tuple(self._messages)
        transcript_height = max(self._console.size.height - 12, 6)
        selected: list[ChatMessage] = []
        line_budget = 0
        for message in reversed(self._messages):
            line_count = max(1, len(_strip_markup(render_block_text(message.content)).splitlines()))
            if selected and line_budget + line_count > transcript_height:
                break
            selected.append(message)
            line_budget += line_count
        return tuple(reversed(selected))

    def _fallback_redraw(self, *, input_active: bool = False) -> None:
        width, height = shutil.get_terminal_size((80, 24))
        header_height = len(self._session_header.splitlines())
        input_section_height = 4
        transcript_height = max(6, height - header_height - input_section_height - 2)
        transcript_lines = _fallback_transcript_lines(self._messages, width=width, height=transcript_height)
        padding_lines = [""] * max(0, transcript_height - len(transcript_lines))
        input_panel = _fallback_input_panel(input_active=input_active)
        lower_frame = "\n".join([*transcript_lines, *padding_lines, *input_panel])
        if self._fallback_header_rendered:
            sys.stdout.write(f"\033[{header_height + 1};1H\033[J")
        else:
            sys.stdout.write("\033[2J\033[H")
            if not self._header_shown:
                sys.stdout.write(self._session_header)
                sys.stdout.write("\n")
                self._header_shown = True
            self._fallback_header_rendered = True
        sys.stdout.write(lower_frame)
        if input_active:
            prompt_row = header_height + transcript_height + 2
            sys.stdout.write(f"\033[{prompt_row};3H")
        sys.stdout.flush()

    def _clear_live_input_area(self) -> None:
        """Clear the existing Rich input section before opening the live composer."""

        sys.stdout.write("\033[6A\033[J")
        sys.stdout.flush()

    def prepare_selection_overlay(self) -> None:
        """Clear the lower screen region so an interactive selector can render there."""

        if self.supports_live:
            return
        width, height = shutil.get_terminal_size((80, 24))
        header_height = len(self._session_header.splitlines())
        input_section_height = 4
        transcript_height = max(6, height - header_height - input_section_height - 2)
        transcript_lines = _fallback_transcript_lines(self._messages, width=width, height=transcript_height)
        overlay_row = header_height + max(1, len(transcript_lines))
        sys.stdout.write(f"\033[{overlay_row};1H\033[J")
        sys.stdout.flush()

    def restore_after_selection(self) -> None:
        """Restore the standard chat layout after an interactive selector closes."""

        if not self.supports_live:
            self._fallback_redraw()


def build_chat_interface(
    *,
    session_header: str,
    user_name: str = "user",
    initial_connection_type: str = "local",
    config_dir=None,
    ui_mode: str = "auto",
) -> TerminalChatInterface:
    """Create a persistent chat-style terminal renderer.

    Plan 80 Fix 4: `ui_mode="inline"` forces the inline (non-fullscreen) renderer so
    Duckln flows in the terminal instead of taking the whole screen; `full`/`auto`
    use the split-pane TUI (which hosts the live VM terminal). Inline is the
    "doesn't fill the terminal" path the user can opt into."""
    if str(ui_mode or "").strip().lower() == "inline":
        fallback = TerminalChatInterface(session_header=session_header, user_name=user_name)
        try:
            fallback.update_connection(connection_type=initial_connection_type)
        except Exception:
            pass
        return fallback

    if SplitPaneChatInterface is not None and split_pane_dependencies_available() and sys.stdin.isatty() and sys.stdout.isatty():
        chat = SplitPaneChatInterface(
            session_header=session_header,
            user_name=user_name,
            initial_connection_type=initial_connection_type,
            config_dir=config_dir,
        )
        if hasattr(chat, "set_footer_text"):
            chat.set_footer_text(render_input_footer())
        return chat
    fallback = TerminalChatInterface(session_header=session_header, user_name=user_name)
    try:
        fallback.update_connection(connection_type=initial_connection_type)
    except Exception:
        pass
    return fallback


def _style_terminal_message(message: str) -> str:
    if not message:
        return ""
    if "\033[" in message:
        return message
    if "\n" in message or "┌" in message or message.startswith("["):
        return render_with_commands(message)
    if message.startswith("Retryable error:") or message.startswith("✗"):
        return render_with_commands(message, base_color=DUCKLN_RED)
    if message.startswith("Warning:") or message.startswith("⚠"):
        return render_with_commands(message, base_color=DUCKLN_ORANGE)
    if message.startswith("✓") or " is ready" in message or " completed" in message or " connection ready" in message:
        return render_with_commands(message, base_color=DUCKLN_GREEN)
    if message.endswith("...") or message.startswith("Checking ") or message.startswith("Starting ") or message.startswith("Reading ") or message.startswith("Cloning ") or message.startswith("Working on:"):
        return render_with_commands(message, base_color=DUCKLN_SYSTEM)
    return render_with_commands(message, base_color=DUCKLN_SYSTEM)


def _looks_like_status_box(message: str) -> bool:
    plain = _strip_markup(message).strip()
    return plain.startswith("┌") and "┐" in plain and "\n" in plain


def _ansi_to_rich(message: str):
    if Text is None:
        return message
    if "\033[" in message:
        return Text.from_ansi(message)
    if "[" in message and "]" in message:
        return Text.from_markup(message)
    return Text(message, style=transcript_body_colour("assistant"))


def _render_chat_message(message: ChatMessage):
    content = render_block_text(message.content)
    if message.role not in {"assistant", "status"}:
        return Text.assemble(
            Text(transcript_prefix(message.role), style=transcript_prefix_colour(message.role)),
            Text(content, style=transcript_body_colour(message.role)),
        )
    if message.role == "status":
        status_title, status_text = _status_payload_from_box(content)
        return Text.assemble(
            Text("● ", style=DUCKLN_GOLD),
            Text(f"{status_title} ", style=DUCKLN_GOLD),
            Text(status_text, style=DUCKLN_SYSTEM),
        )
    return Text.assemble(
        Text(transcript_prefix(message.role), style=transcript_prefix_colour(message.role)),
        _ansi_to_rich(content),
    )


def _fallback_transcript_lines(messages: list[ChatMessage], *, width: int, height: int) -> list[str]:
    flattened: list[str] = []
    for message in messages:
        rendered = _fallback_render_chat_message(message, width=width)
        flattened.extend(rendered)
        if message.role in {"assistant", "status"} and rendered and rendered[-1] != "":
            flattened.append("")
    if len(flattened) > height:
        flattened = flattened[-height:]
    if not flattened:
        return [""]
    return flattened


def _fallback_render_chat_message(message: ChatMessage, *, width: int) -> list[str]:
    content = render_block_text(message.content)
    if message.role not in {"assistant", "status"}:
        return _wrap_prefixed_message(
            transcript_prefix(message.role),
            content,
            width=width,
            prefix_color=transcript_prefix_colour(message.role),
            content_color=transcript_body_colour(message.role),
        )
    if message.role == "status":
        status_title, status_text = _status_payload_from_box(content)
        return _wrap_styled_text(
            f"{_style_text(f'● {status_title} ', DUCKLN_GOLD)}{_style_text(status_text, DUCKLN_SYSTEM)}",
            plain_text=f"● {status_title} {status_text}",
            width=width,
            base_color=DUCKLN_SYSTEM,
        )
    return _wrap_prefixed_message(
        transcript_prefix(message.role),
        _strip_markup(content),
        width=width,
        prefix_color=transcript_prefix_colour(message.role),
        content_color=transcript_body_colour(message.role),
    )


def _rich_header_renderables(
    *,
    provider: str,
    model: str,
    mode: str,
    user_name: str,
    memory_state: str,
    narrow: bool,
) -> tuple[object, ...]:
    rows: list[object]
    if narrow:
        rows = [
            _rich_brand_row(narrow=True),
            Text(" "),
        ]
    else:
        rows = [_rich_brand_row(narrow=False), Text(" ")]
    for label, value, command in (
        ("provider", provider, "/provider"),
        ("model", model, "/model"),
        ("mode", mode, "/mode"),
        ("user", user_name, None),
        ("memory", memory_state, None),
    ):
        rows.extend(_rich_header_row(label=label, value=value, command=command, narrow=narrow))
    return tuple(rows)


def _rich_header_layout(
    *,
    provider: str,
    model: str,
    mode: str,
    user_name: str,
    memory_state: str,
    narrow: bool,
):
    grid = Table.grid(expand=True, padding=(0, 0))
    grid.add_column(ratio=1)
    for renderable in _rich_header_renderables(
        provider=provider,
        model=model,
        mode=mode,
        user_name=user_name,
        memory_state=memory_state,
        narrow=narrow,
    ):
        grid.add_row(renderable)
    return grid


def _rich_brand_row(*, narrow: bool):
    return Text.assemble(
        Text("◆ Duckln", style=DUCKLN_GOLD),
        Text(f" (v{__version__})", style=DUCKLN_SYSTEM),
    )


def _rich_header_row(*, label: str, value: str, command: str | None, narrow: bool):
    left = Text.assemble(
        Text(f"{label:<{_HEADER_LABEL_WIDTH}}", style=DUCKLN_SYSTEM),
        Text(" : ", style=DUCKLN_SYSTEM),
        Text(value, style=DUCKLN_SYSTEM),
    )
    if command is None:
        return (left,)
    hint = Text.assemble(
        Text(command, style=DUCKLN_COMMAND),
        Text(" to change", style=DUCKLN_SYSTEM),
    )
    if narrow:
        return (left, Text.assemble(Text("  ", style=DUCKLN_SYSTEM), hint))
    row = Table.grid(expand=True)
    row.add_column(ratio=1)
    row.add_column(justify="right")
    row.add_row(left, hint)
    return (row,)


def _style_text(text: str, color: str) -> str:
    return f"{_ANSI_BY_COLOR[color]}{text}{_ANSI_RESET}"


def render_with_commands(text: str, *, base_color: str | None = None) -> str:
    """Render slash commands in cyan without bleeding into surrounding text."""

    styled_chunks: list[str] = []
    cursor = 0
    for match in _COMMAND_PATTERN.finditer(text):
        if match.start() > cursor:
            prefix = text[cursor : match.start()]
            styled_chunks.append(_style_text(prefix, base_color) if base_color else prefix)
        styled_chunks.append(_style_text(match.group(1), DUCKLN_COMMAND))
        cursor = match.end()
    if cursor < len(text):
        suffix = text[cursor:]
        styled_chunks.append(_style_text(suffix, base_color) if base_color else suffix)
    return "".join(styled_chunks)


def _style_commands(message: str, *, base_color: str | None = None) -> str:
    return render_with_commands(message, base_color=base_color)


def _strip_markup(text: str) -> str:
    return re.sub(r"\[[#A-Za-z0-9/]+\]|\033\[[0-9;]*m", "", text)


def _truncate_styled_row(styled_text: str, plain_text: str, width: int, *, label_color: str = DUCKLN_SYSTEM) -> str:
    if len(plain_text) <= width:
        return styled_text
    trimmed_plain = plain_text[:width]
    if plain_text.startswith(trimmed_plain):
        return _style_text(trimmed_plain[:_HEADER_LABEL_WIDTH], label_color) + trimmed_plain[_HEADER_LABEL_WIDTH:]
    return trimmed_plain


def _pad_styled_row(styled_text: str, width: int) -> str:
    plain_length = len(_strip_markup(styled_text))
    return styled_text + (" " * max(0, width - plain_length))


def _fallback_status_box(*, message: str, title: str, width: int) -> str:
    content_width = max(len(title) + 2, min(width, max(len(message) + 2, 28)))
    title_plain = f" {title} "
    border_width = max(content_width + 2, len(title_plain) + 2)
    title_prefix = f"┌{title_plain}"
    top = _style_text(title_prefix + ("─" * max(0, border_width - len(title_prefix) - 1)) + "┐", DUCKLN_GREY)
    body = (
        f"{_style_text('│', DUCKLN_GREY)} "
        f"{_pad_styled_row(_style_text(message, DUCKLN_SYSTEM), border_width - 2)} "
        f"{_style_text('│', DUCKLN_GREY)}"
    )
    bottom = _style_text(f"└{'─' * border_width}┘", DUCKLN_GREY)
    return "\n".join((top, body, bottom))


def _status_line_from_box(message: str) -> str:
    return _status_payload_from_box(message)[1]


def _status_payload_from_box(message: str) -> tuple[str, str]:
    plain_lines = [_strip_markup(line).strip() for line in message.splitlines()]
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
    return title, _strip_markup(message).strip()


def _render_repair_surface_message(*, summary: str, labels: tuple[str, ...]) -> str:
    normalized_summary = summary.strip() or "Duckln is working on the current repair step."
    recent_labels = tuple(label for label in labels if label.strip())
    lines = ["Active repair", "", f"Current step: {normalized_summary}"]
    if recent_labels:
        lines.extend(("", "Recent repair updates:"))
        visible = recent_labels[-3:]
        hidden_count = max(0, len(recent_labels) - len(visible))
        if hidden_count:
            lines.append(f"- {hidden_count} earlier update(s) compacted")
        lines.extend(f"- {label}" for label in visible)
    return "\n".join(lines)


def _wrap_labeled_message(
    label: str,
    message: str,
    *,
    width: int,
    label_color: str,
    content_color: str,
) -> list[str]:
    prefix = f"{label}: "
    content_width = max(8, width - len(prefix))
    message_lines = message.splitlines() or [""]
    first_segment = textwrap.wrap(message_lines[0], width=content_width) or [""]
    lines = [
        f"{_style_text(label, label_color)}{_style_text(': ', DUCKLN_SYSTEM)}{_style_text(first_segment[0], content_color)}"
    ]
    indent = " " * len(prefix)
    for continuation in first_segment[1:]:
        lines.append(f"{indent}{_style_text(continuation, content_color)}")
    for raw_line in message_lines[1:]:
        wrapped = textwrap.wrap(raw_line, width=content_width) or [""]
        lines.append(f"{indent}{_style_text(wrapped[0], content_color)}")
        for continuation in wrapped[1:]:
            lines.append(f"{indent}{_style_text(continuation, content_color)}")
    return lines


def _wrap_prefixed_message(
    prefix: str,
    message: str,
    *,
    width: int,
    prefix_color: str,
    content_color: str,
) -> list[str]:
    content_width = max(8, width - len(prefix))
    message_lines = message.splitlines() or [""]
    lines: list[str] = []
    indent = " " * len(prefix)
    numbered_pattern = re.compile(r"^(\d+\.\s+)(.+)$")
    first_content_line = True
    for raw_line in message_lines:
        if not raw_line:
            lines.append("")
            continue
        stripped = raw_line.lstrip()
        line_prefix = prefix if first_content_line else indent
        first_content_line = False
        if stripped.startswith(("- ", "• ")):
            bullet = stripped[:2]
            body = stripped[2:].strip()
            wrapped = textwrap.wrap(body, width=max(8, content_width - len(bullet))) or [""]
            lines.append(
                f"{_style_text(line_prefix, prefix_color if line_prefix == prefix else content_color)}"
                f"{_style_text(bullet, content_color)}{_style_text(wrapped[0], content_color)}"
            )
            bullet_indent = indent + (" " * len(bullet))
            for continuation in wrapped[1:]:
                lines.append(f"{bullet_indent}{_style_text(continuation, content_color)}")
            continue
        numbered_match = numbered_pattern.match(stripped)
        if numbered_match:
            marker, body = numbered_match.groups()
            wrapped = textwrap.wrap(body, width=max(8, content_width - len(marker))) or [""]
            lines.append(
                f"{_style_text(line_prefix, prefix_color if line_prefix == prefix else content_color)}"
                f"{_style_text(marker, content_color)}{_style_text(wrapped[0], content_color)}"
            )
            number_indent = indent + (" " * len(marker))
            for continuation in wrapped[1:]:
                lines.append(f"{number_indent}{_style_text(continuation, content_color)}")
            continue
        wrapped = textwrap.wrap(raw_line, width=content_width) or [""]
        lines.append(
            f"{_style_text(line_prefix, prefix_color if line_prefix == prefix else content_color)}"
            f"{_style_text(wrapped[0], content_color)}"
        )
        for continuation in wrapped[1:]:
            lines.append(f"{indent}{_style_text(continuation, content_color)}")
    return lines


def _wrap_assistant_message(message: str, *, width: int) -> list[str]:
    plain = _strip_markup(message)
    wrapped = textwrap.wrap(plain, width=max(20, width)) or [plain]
    return [_style_terminal_message(line) for line in wrapped]


def _wrap_styled_text(styled_text: str, *, plain_text: str, width: int, base_color: str) -> list[str]:
    wrapped = textwrap.wrap(plain_text, width=max(20, width)) or [plain_text]
    if not wrapped:
        return [styled_text]
    lines: list[str] = []
    for index, line in enumerate(wrapped):
        if index == 0:
            lines.append(styled_text if len(wrapped) == 1 else _style_commands(line, base_color=base_color))
        else:
            lines.append(_style_commands(line, base_color=base_color))
    return lines


def _fallback_input_panel(*, input_active: bool) -> list[str]:
    width, _ = shutil.get_terminal_size((80, 24))
    content_width = max(20, width - 2)
    separator = _style_text("─" * content_width, DUCKLN_GREY)
    if input_active:
        return [separator, f"{_style_text('◆ ', DUCKLN_GOLD)}", separator, render_input_footer()]
    input_line = (
        f"{_style_text('◆ ', DUCKLN_GOLD)}"
        f"{_style_text('Send a message', DUCKLN_GREY)}"
    )
    return [separator, input_line, separator, render_input_footer()]


def _fallback_session_header(
    *,
    provider: str,
    model: str,
    mode: str,
    user_name: str,
    memory_state: str,
    target: str = "Local",
    width: int,
) -> str:
    content_width = max(32, min(width, 80) - 4)
    narrow = content_width < 64
    rows = [
        _fallback_brand_row(content_width, narrow=narrow),
        "",
        _fallback_header_row(label="provider", value=provider, command="/provider", width=content_width, narrow=narrow),
        _fallback_header_row(label="model", value=model, command="/model", width=content_width, narrow=narrow),
        _fallback_header_row(label="mode", value=mode, command="/mode", width=content_width, narrow=narrow),
        _fallback_header_row(label="target", value=target, command="/vm", width=content_width, narrow=narrow),
        _fallback_header_row(label="user", value=user_name, command=None, width=content_width, narrow=narrow),
        _fallback_header_row(label="memory", value=memory_state, command=None, width=content_width, narrow=narrow),
    ]
    flat_rows = [line for row in rows for line in (row.splitlines() or [""])]
    top = _style_text(f"┌{'─' * (content_width + 2)}┐", DUCKLN_GREY)
    bottom = _style_text(f"└{'─' * (content_width + 2)}┘", DUCKLN_GREY)
    body = "\n".join(
        f"{_style_text('│', DUCKLN_GREY)} {_pad_styled_row(row, content_width)} {_style_text('│', DUCKLN_GREY)}"
        for row in flat_rows
    )
    return "\n".join((top, body, bottom))


def _fallback_brand_row(width: int, *, narrow: bool) -> str:
    return (
        f"{_style_text('◆ Duckln', DUCKLN_GOLD)}"
        f"{_style_text(f' (v{__version__})', DUCKLN_SYSTEM)}"
    )


def _fallback_header_row(
    *,
    label: str,
    value: str,
    command: str | None,
    width: int,
    narrow: bool,
) -> str:
    left_plain = f"{label:<{_HEADER_LABEL_WIDTH}} : {value}"
    left = (
        f"{_style_text(f'{label:<{_HEADER_LABEL_WIDTH}}', DUCKLN_SYSTEM)}"
        f"{_style_text(' : ', DUCKLN_SYSTEM)}"
        f"{_style_text(value, DUCKLN_SYSTEM)}"
    )
    if command is None:
        return _truncate_styled_row(left, left_plain, width, label_color=DUCKLN_SYSTEM)
    hint_plain = f"{command} to change"
    hint = f"{_style_text(command, DUCKLN_COMMAND)}{_style_text(' to change', DUCKLN_SYSTEM)}"
    if narrow:
        return (
            f"{_truncate_styled_row(left, left_plain, width, label_color=DUCKLN_SYSTEM)}\n"
            f"{_truncate_styled_row(f'  {hint}', f'  {hint_plain}', width)}"
        )
    spacing = max(2, width - min(len(left_plain), width) - len(hint_plain))
    return f"{_truncate_styled_row(left, left_plain, width, label_color=DUCKLN_SYSTEM)}{' ' * spacing}{hint}"


# --- Plan 67: Plan Mode rendering --------------------------------------------


_PLAN_CONCRETE_SOURCES = {"duckln-deterministic", "duckln-known-fix"}


def _plan_step_is_speculative(step) -> bool:
    """Plan 148 F4: a step is SPECULATIVE (not evidence-backed) only when it has NO cited
    evidence AND no concrete origin AND low confidence — conservative, so a real deterministic
    or amendment step (which carries a rationale + a known source + high confidence) is never
    false-flagged. 'Evidence' = an evidence_excerpt, a concrete `source` (a file/README/web),
    or a deterministic/known-fix origin."""
    if (getattr(step, "evidence_excerpt", "") or "").strip():
        return False
    src = (getattr(step, "source", "") or "").strip()
    if src and src not in {"", "planner"}:   # a concrete origin (a file, README, web, known-fix)
        return False
    if getattr(step, "origin", "") == "amendment":   # recovery-derived, justified elsewhere
        return False
    try:
        conf = float(getattr(step, "confidence", 1.0) or 1.0)
    except (TypeError, ValueError):
        conf = 1.0
    return conf < 0.5


def render_plan_panel(plan, *, width: int | None = None) -> str:
    """Render a PlanRecord as a multi-line plain-text panel.

    Designed to be passed to ``display(...)`` callables that the rest of
    Duckln uses — does not call print() directly. When `rich` is available,
    a Rich Panel is rendered to a string with ANSI markers; otherwise we
    fall back to a hand-drawn box.
    """
    from duckln.glyphs import (
        DOT_SOLID,
        PLAN_APPROVED,
        PLAN_EDIT,
        PLAN_NOTE,
        PLAN_PENDING,
        PLAN_REJECTED,
    )

    cols = width or shutil.get_terminal_size((100, 24)).columns
    panel_w = max(52, min(100, cols - 2))
    inner = panel_w - 2  # content width after the "│ " left border

    lines: list[str] = []  # each is a fully-styled content line (no border yet)

    def field(label: str, value: str, *, value_color: str | None = None) -> None:
        lines.extend(_plan_field_lines(label, value, inner=inner, label_w=12, value_color=value_color))

    # Header block.
    field("Objective:", plan.objective)
    if plan.repo_slug:
        field("Repo:", plan.repo_slug, value_color=DUCKLN_COMMAND)
    # Plan 83 Fix 2: show WHERE the repo installs (target machine / VM name).
    target_label = getattr(plan, "target_label", "") or ""
    if target_label:
        field("Target:", target_label)
    eta_min = max(1, plan.estimated_seconds // 60)
    # Plan 76: plain summary — count steps that will ask for approval instead of
    # surfacing S0/S1/S2 jargon.
    approval_steps = sum(1 for s in plan.steps if s.safety_class in ("S2", "S3"))
    approval_note = f"   {approval_steps} need approval" if approval_steps else ""
    field("ETA:", f"~{eta_min} min   Steps: {len(plan.steps)}{approval_note}")
    field("Context:", plan.context_summary)
    # Plan 83 Fix 2: what Duckln already found on the target, under its own heading.
    precheck_summary = getattr(plan, "precheck_summary", "") or ""
    if precheck_summary:
        field("Pre-check:", precheck_summary)
    lines.append("")

    # Steps.
    lines.append(_style_text("Steps", DUCKLN_GOLD))
    if not plan.steps:
        lines.append(_style_text("  (no steps)", DUCKLN_SYSTEM))
    for step in plan.steps:
        # The COMMAND is the headline — that's what actually gets the repo
        # running. The safety class + ETA are a small trailing annotation.
        head_plain = f"{DOT_SOLID} Step {step.index}: {step.title}"
        lines.extend(_plan_wrap_line(head_plain, inner=inner, indent=0, color=None))
        if step.command is not None:
            # Prominent, copy-pasteable command line.
            lines.extend(_plan_wrap_line(f"$ {step.command}", inner=inner, indent=4, color=DUCKLN_COMMAND))
        else:
            lines.extend(_plan_wrap_line("(explanation step — no shell command)", inner=inner, indent=4, color=DUCKLN_SYSTEM))
        if step.rationale:
            lines.extend(_plan_field_lines("Why:", step.rationale, inner=inner, label_w=8, indent=4))
        # Plan 78 Fix B: show WHERE the command runs and WHERE it came from.
        where = getattr(step, "target", "") or ""
        src = getattr(step, "source", "") or ""
        if where or src:
            on_part = f"on {where}" if where else ""
            from_part = f"from {src}" if src and src != "duckln-deterministic" else ""
            detail = " · ".join(p for p in (on_part, from_part) if p)
            if detail:
                lines.extend(_plan_field_lines("Where:", detail, inner=inner, label_w=8, indent=4))
        # Plan 79 L5: show the README line the command was grounded in, when known.
        evidence = getattr(step, "evidence_excerpt", "") or ""
        if evidence:
            lines.extend(_plan_field_lines("Source:", evidence, inner=inner, label_w=8, indent=4))
        if step.verification:
            lines.extend(_plan_field_lines("Verify:", step.verification, inner=inner, label_w=8, indent=4))
        if step.depends_on:
            dep = ", ".join(str(d) for d in step.depends_on)
            lines.extend(_plan_field_lines("Needs:", f"steps {dep}", inner=inner, label_w=8, indent=4))
        if step.origin == "amendment":
            lines.append(_style_text("    (amendment — inserted to recover from a failure)", DUCKLN_ORANGE))
        # Plan 148 F4: a step Duckln can't tie to any cited source (no evidence, no concrete
        # origin, and low confidence) is SPECULATIVE — flag it so Duckln never silently
        # asserts a step it can't back. (Evidence-backed steps show Why/Where/Source above.)
        if _plan_step_is_speculative(step):
            lines.append(_style_text("    ⚠ speculative — no cited source (Duckln couldn't back this from the repo)", DUCKLN_ORANGE))
        # Plan 76: keep the plan simple — no S0/S1/S2 jargon in the panel. Show
        # only a plain time estimate; approval gating still happens at run time.
        if step.command is not None:
            lines.append(_style_text(f"    ~{step.estimated_seconds}s", DUCKLN_SYSTEM))
        lines.append("")

    # Risks.
    lines.append(_style_text("Risks", DUCKLN_GOLD))
    if plan.risks:
        for r in plan.risks:
            lines.extend(_plan_wrap_line(f"• {r}", inner=inner, indent=2, color=DUCKLN_SYSTEM))
    else:
        lines.append(_style_text("  (none identified)", DUCKLN_SYSTEM))
    lines.append("")

    # Rollback.
    lines.append(_style_text("Rollback", DUCKLN_GOLD))
    lines.extend(_plan_wrap_line(plan.rollback or "(none)", inner=inner, indent=2, color=DUCKLN_SYSTEM))

    if plan.critic_reasoning:
        lines.append("")
        lines.append(_style_text("Supervisor", DUCKLN_GOLD))
        lines.extend(_plan_wrap_line(plan.critic_reasoning, inner=inner, indent=2, color=DUCKLN_SYSTEM))

    lines.append("")
    action_line = (
        f"{_style_text(PLAN_APPROVED + ' /plan approve', DUCKLN_GREEN)}    "
        f"{_style_text(PLAN_REJECTED + ' /plan reject', DUCKLN_RED)}    "
        f"{_style_text(PLAN_EDIT + ' /plan edit', DUCKLN_COMMAND)}"
    )
    lines.append(action_line)

    title = f"{PLAN_NOTE}  Plan Mode — {plan.status}"
    return _draw_plan_panel(title=title, body_lines=lines, panel_w=panel_w)


def render_plan_oneline(plan) -> str:
    """Compact one-line plan summary for header-row display."""
    from duckln.glyphs import PLAN_NOTE

    return (
        f"{PLAN_NOTE} Plan Mode — {plan.status}: "
        f"{len(plan.steps)} step(s), ~{max(1, plan.estimated_seconds // 60)} min"
    )


def render_clarification_prompt(questions) -> str:
    """Render the clarification panel that asks the user up to 3 questions."""
    from duckln.glyphs import PLAN_PENDING

    if not questions:
        return ""
    lines = [
        f"{PLAN_PENDING} Plan Mode — clarification needed before finalizing the plan:",
        "",
    ]
    for i, q in enumerate(questions, start=1):
        lines.append(f"  {i}. {q.text}")
        if q.options:
            lines.append(f"     options: {', '.join(q.options)}")
        if q.default:
            lines.append(f"     default: {q.default}")
        if q.applies_to_step_indices:
            lines.append(
                f"     affects step(s): {', '.join(str(x) for x in q.applies_to_step_indices)}"
            )
    lines.append("")
    lines.append("Answer in chat. Duckln will refine the plan once it has your input.")
    return "\n".join(lines)


def render_error_attribution(*, failed_step, cause: str, fix_step, amendment_count: int) -> str:
    """Render the cause + fix panel shown when an approved step fails."""
    from duckln.glyphs import PLAN_REJECTED, PLAN_PENDING

    lines = [
        f"{PLAN_REJECTED} Step {failed_step.index} failed — {failed_step.title}",
        f"  Cause:  {cause}",
    ]
    if fix_step is not None:
        lines.append(f"  Fix:    {fix_step.title} ({fix_step.safety_class})")
        if fix_step.command:
            lines.append(f"          {fix_step.command}")
        if fix_step.rationale:
            lines.append(f"  Why:    {fix_step.rationale}")
        lines.append(
            f"  {PLAN_PENDING} amendment #{amendment_count} inserted before step "
            f"{failed_step.index}. Run `/plan continue` to apply + resume, `/plan reject` to abort."
        )
    else:
        lines.append("  Fix:    no automatic fix available — please intervene manually.")
        lines.append("  Run `/plan reject` to abort, or fix the issue and run `/plan continue`.")
    return "\n".join(lines)


def _highest_safety(steps) -> str:
    order = {"S0": 0, "S1": 1, "S2": 2, "S3": 3}
    best = "S0"
    for s in steps:
        if order.get(s.safety_class, 0) > order.get(best, 0):
            best = s.safety_class
    return best


_SAFETY_CHIP_COLORS = {
    "S0": COLOUR_SUCCESS,
    "S1": COLOUR_SUCCESS,
    "S2": COLOUR_GOLD,
    "S3": COLOUR_WARNING,
    "S4": COLOUR_ERROR,
}


def _safety_chip(safety_class: str) -> str:
    return _style_text(safety_class, _SAFETY_CHIP_COLORS.get(safety_class, COLOUR_SYSTEM))


def _plan_wrap_line(
    text: str,
    *,
    inner: int,
    indent: int = 0,
    color: str | None = None,
    suffix: str = "",
) -> list[str]:
    """Word-wrap one plain line to the inner width with a hanging indent, then
    colour each visual line. ``suffix`` is appended (uncoloured) to the first
    line only — used for the right-aligned-ish safety chip on a step head."""
    pad = " " * indent
    avail = max(8, inner - indent)
    wrapped = textwrap.wrap(
        text, width=avail, break_long_words=True, break_on_hyphens=False
    ) or [""]
    out: list[str] = []
    for i, chunk in enumerate(wrapped):
        body = pad + chunk
        styled = _style_text(body, color) if color else body
        if i == 0 and suffix:
            styled = styled + suffix
        out.append(styled)
    return out


def _plan_field_lines(
    label: str,
    value: str,
    *,
    inner: int,
    label_w: int,
    indent: int = 0,
    value_color: str | None = None,
) -> list[str]:
    """Render `Label:   value` with the value wrapped and continuation lines
    hanging-indented under the value column. Label is grey; value optionally
    coloured."""
    pad = " " * indent
    label_field = f"{label:<{label_w}}"
    value_col = indent + label_w
    avail = max(8, inner - value_col)
    wrapped = textwrap.wrap(
        value or "", width=avail, break_long_words=True, break_on_hyphens=False
    ) or [""]
    out: list[str] = []
    first_value = _style_text(wrapped[0], value_color) if value_color else wrapped[0]
    out.append(f"{pad}{_style_text(label_field, COLOUR_SYSTEM)}{first_value}")
    cont_pad = " " * value_col
    for chunk in wrapped[1:]:
        styled = _style_text(chunk, value_color) if value_color else chunk
        out.append(f"{cont_pad}{styled}")
    return out


def _draw_plan_panel(*, title: str, body_lines: list[str], panel_w: int) -> str:
    """Frame pre-styled, pre-wrapped body lines with a left border + top/bottom
    rule. No right border (avoids ANSI-width padding bugs); every visual line
    keeps its left border so terminal soft-wrap can't corrupt the frame."""
    styled_title = _style_text(title, DUCKLN_GOLD)
    rule_len = max(4, panel_w - _visible_len(title) - 3)
    out: list[str] = [f"╭─ {styled_title} {'─' * rule_len}"]
    for line in body_lines:
        out.append(f"│ {line}" if line else "│")
    out.append(f"╰{'─' * (panel_w - 1)}")
    return "\n".join(out)


def _visible_len(text: str) -> int:
    """Length of text ignoring ANSI escape sequences."""
    return len(re.sub(r"\x1b\[[0-9;]*m", "", text))
