"""Plan 61 Fix E: multi-choice clarification prompt adapters.

Wraps the existing `duckln_select` (CLI) and `show_choice_overlay` (Textual UI)
into a unified callable type so the runtime repair flow can ask the user to
pick from a list when LLM confidence is low or when system probes leave
ambiguity. Returns the 0-based index of the chosen option, or None for cancel.

Repo-agnostic; only depends on the existing selection primitives.
"""

from __future__ import annotations

from typing import Callable, Sequence


ClarifyPrompt = Callable[[str, Sequence[str]], int | None]


def make_cli_clarify_prompt() -> ClarifyPrompt:
    """Build a ClarifyPrompt that uses `duckln_select` for terminal sessions."""

    def _prompt(question: str, choices: Sequence[str]) -> int | None:
        if not choices:
            return None
        choice_tuple = tuple(choices)
        # Import inside the call so unit tests can patch
        # `duckln.selections.duckln_select` and have the patch take effect.
        try:
            from duckln.selections import duckln_select
            chosen = duckln_select(question, choice_tuple)
        except Exception:
            return None
        if chosen is None:
            return None
        try:
            return choice_tuple.index(chosen)
        except ValueError:
            return None

    return _prompt


def make_textual_clarify_prompt(
    *,
    chat: object,
) -> ClarifyPrompt | None:
    """Build a ClarifyPrompt that uses the Textual UI's choice overlay.

    Returns None when the chat object lacks the overlay capability — callers
    fall back to the CLI variant in that case.
    """

    if chat is None:
        return None
    if not hasattr(chat, "show_choice_overlay"):
        return None

    def _prompt(question: str, choices: Sequence[str]) -> int | None:
        if not choices:
            return None
        choice_tuple = tuple(choices)
        try:
            # The textual overlay is interactive; in test/non-textual contexts
            # this will short-circuit to None.
            chosen = chat.show_choice_overlay(question, choice_tuple)
        except Exception:
            return None
        if chosen is None:
            return None
        try:
            return choice_tuple.index(chosen)
        except (ValueError, TypeError):
            return None

    return _prompt


def resolve_clarify_prompt(*, chat: object | None) -> ClarifyPrompt:
    """Pick the best available clarify prompt for the active session.

    Textual UI when available, CLI fallback otherwise. Always returns a
    ClarifyPrompt — never None — so callers can use it unconditionally.
    """
    textual = make_textual_clarify_prompt(chat=chat)
    if textual is not None:
        return textual
    return make_cli_clarify_prompt()
