"""Shared selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MethodType
from typing import Callable, Sequence

try:
    from InquirerPy import inquirer
    from InquirerPy.utils import InquirerPyStyle
except ModuleNotFoundError:
    inquirer = None
    InquirerPyStyle = None


if InquirerPyStyle is not None:
    DUCKLN_SELECT_STYLE = InquirerPyStyle(
        {
            "question": "#F5A623 bold",
            "pointer": "#F5A623 bold",
            "marker": "#616161",
            "marker-selected": "#F5A623 bold",
            "highlighted": "#F2F2F2",
            "text": "#A9A9A9",
            "instruction": "#616161 italic",
            "answer": "#F0EAD6 bold",
            "fuzzy_info": "#616161",
            "fuzzy_prompt": "#616161",
            "fuzzy_match": "#F5A623 bold",
        }
    )
else:
    DUCKLN_SELECT_STYLE = None


@dataclass(frozen=True)
class ConversationChoiceOption:
    """One bounded supervisor option shown in chat-like choice prompts."""

    label: str
    action_key: str
    description: str | None = None
    recommended: bool = False


@dataclass(frozen=True)
class ConversationChoicePrompt:
    """A bounded in-chat supervisor decision prompt."""

    message: str
    options: tuple[ConversationChoiceOption, ...]


def conversation_choice_labels(prompt: ConversationChoicePrompt) -> tuple[str, ...]:
    """Return the user-facing labels for a bounded supervisor choice prompt."""

    return tuple(option.label for option in prompt.options)


def resolve_conversation_choice(
    prompt: ConversationChoicePrompt,
    *,
    selected_label: str | None,
) -> ConversationChoiceOption | None:
    """Resolve a chosen label back to the corresponding bounded option."""

    if selected_label is None:
        return None
    for option in prompt.options:
        recommended_label = f"{option.label} (Recommended)"
        if option.label == selected_label or recommended_label == selected_label:
            return option
    return None


def _render_hover_tokens(choice: dict[str, object]) -> list[tuple[str, str]]:
    return [
        ("class:marker-selected", "●"),
        ("", "  "),
        ("[SetCursorPosition]", ""),
        ("class:pointer", str(choice["name"])),
    ]


def _render_normal_tokens(choice: dict[str, object]) -> list[tuple[str, str]]:
    return [
        ("class:marker", "○"),
        ("", "  "),
        ("class:text", str(choice["name"])),
    ]


def _render_fuzzy_normal_tokens(choice: dict[str, object]) -> list[tuple[str, str]]:
    display_choices: list[tuple[str, str]] = [
        ("class:marker", "○"),
        ("", "  "),
    ]
    indices = set(choice.get("indices", []))
    if not indices:
        display_choices.append(("class:text", str(choice["name"])))
        return display_choices
    for index, char in enumerate(str(choice["name"])):
        display_choices.append(("class:fuzzy_match" if index in indices else "class:text", char))
    return display_choices


def _apply_duckln_single_select_renderer(prompt: object) -> object:
    content_control = getattr(prompt, "content_control", None)
    if content_control is None:
        return prompt
    content_control._get_hover_text = MethodType(lambda self, choice: _render_hover_tokens(choice), content_control)
    content_control._get_normal_text = MethodType(lambda self, choice: _render_normal_tokens(choice), content_control)
    return prompt


def _apply_duckln_fuzzy_renderer(prompt: object) -> object:
    content_control = getattr(prompt, "content_control", None)
    if content_control is None:
        return prompt
    content_control._get_hover_text = MethodType(lambda self, choice: _render_hover_tokens(choice), content_control)
    content_control._get_normal_text = MethodType(
        lambda self, choice: _render_fuzzy_normal_tokens(choice),
        content_control,
    )
    return prompt


def inline_selection_available() -> bool:
    """Return whether interactive arrow-key selectors are available."""

    return inquirer is not None


def _fallback_select(
    question: str,
    choices: Sequence[str],
    *,
    default: str | None = None,
    include_cancel: bool = False,
    input_func: Callable[[str], str] | None = None,
    display: Callable[[str], None] | None = None,
) -> str | None:
    """Render a simple inline numbered selector."""

    read_input = input if input_func is None else input_func
    write_output = print if display is None else display
    choice_values = list(choices)
    if include_cancel and "Cancel" not in choice_values:
        choice_values.append("Cancel")

    while True:
        lines = [question]
        lines.extend(f"  {index}. {choice}" for index, choice in enumerate(choice_values, start=1))
        lines.append("  c. Cancel")
        if default:
            lines.append(f"Press Enter to keep: {default}")
        lines.append("Type a number:")
        write_output("\n".join(lines))

        raw = read_input("").strip()
        if not raw:
            if default is not None:
                return default
            return None
        if raw.lower() in {"c", "cancel"}:
            return "Cancel" if "Cancel" in choice_values else None
        if not raw.isdigit():
            write_output("Retryable error: Enter a number from the list.")
            continue

        selected_index = int(raw) - 1
        if 0 <= selected_index < len(choice_values):
            return choice_values[selected_index]
        write_output("Retryable error: Enter a number from the list.")


def duckln_select(
    question: str,
    choices: Sequence[str],
    *,
    default: str | None = None,
    include_cancel: bool = False,
    input_func: Callable[[str], str] | None = None,
    display: Callable[[str], None] | None = None,
) -> str | None:
    """Render an arrow-key selector when available, otherwise use a simple fallback."""

    choice_values = list(choices)
    if include_cancel and "Cancel" not in choice_values:
        choice_values.append("Cancel")
    if inquirer is None or input_func is not None or display is not None:
        return _fallback_select(
            question,
            tuple(choice_values),
            default=default,
            include_cancel=False,
            input_func=input_func,
            display=display,
        )

    try:
        prompt = inquirer.select(
            message=question,
            choices=choice_values,
            default=default if default in choice_values else None,
            style=DUCKLN_SELECT_STYLE,
            qmark="◆",
            amark="",
            pointer="●",
            marker="○",
            instruction="",
            long_instruction="",
            vi_mode=False,
            cycle=False,
            wrap_lines=False,
            raise_keyboard_interrupt=True,
            mandatory=False,
        )
        return _apply_duckln_single_select_renderer(prompt).execute()
    except KeyboardInterrupt:
        return None


def duckln_confirm(
    question: str,
    *,
    default: bool = True,
    input_func: Callable[[str], str] | None = None,
    display: Callable[[str], None] | None = None,
) -> bool:
    """Render a simple inline yes/no confirmation."""

    result = duckln_select(
        question,
        ("Yes", "No"),
        default="Yes" if default else "No",
        input_func=input_func,
        display=display,
    )
    return result == "Yes"


def duckln_search_select(
    question: str,
    choices: Sequence[str],
    *,
    default: str | None = None,
    input_func: Callable[[str], str] | None = None,
    display: Callable[[str], None] | None = None,
) -> str | None:
    """Render a searchable arrow-key selector when available."""

    active_choices = list(choices)
    if inquirer is None or input_func is not None or display is not None:
        read_input = input if input_func is None else input_func
        write_output = print if display is None else display
        filter_text = ""

        while True:
            visible_choices = [
                choice for choice in active_choices if filter_text.casefold() in choice.casefold()
            ] or active_choices
            lines = [question]
            if filter_text:
                lines.append(f"Filter: {filter_text}")
            lines.extend(f"  {index}. {choice}" for index, choice in enumerate(visible_choices, start=1))
            lines.append("  c. Cancel")
            lines.append("Type a number or enter search text:")
            write_output("\n".join(lines))

            raw = read_input("").strip()
            if not raw:
                if default is not None:
                    return default
                return None
            if raw.lower() in {"c", "cancel"}:
                return None
            if raw.isdigit():
                selected_index = int(raw) - 1
                if 0 <= selected_index < len(visible_choices):
                    return visible_choices[selected_index]
                write_output("Retryable error: Enter a number from the filtered list.")
                continue

            filter_text = raw

    try:
        prompt = inquirer.fuzzy(
            message=question,
            choices=active_choices,
            default=default if default in active_choices else "",
            style=DUCKLN_SELECT_STYLE,
            qmark="◆",
            amark="",
            pointer="●",
            marker="○",
            prompt="",
            instruction="",
            long_instruction="",
            vi_mode=False,
            cycle=False,
            wrap_lines=False,
            raise_keyboard_interrupt=True,
            mandatory=False,
            info=False,
            max_height="70%",
            height="70%",
        )
        return _apply_duckln_fuzzy_renderer(prompt).execute()
    except KeyboardInterrupt:
        return None
