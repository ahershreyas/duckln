"""Shared transcript styling rules used by both terminal renderers."""

from __future__ import annotations

from duckln.constants import COLOUR_COMMAND, COLOUR_GOLD, COLOUR_SYSTEM, COLOUR_USER_INPUT


TRANSCRIPT_USER_BODY = COLOUR_USER_INPUT
TRANSCRIPT_ASSISTANT_BODY = COLOUR_USER_INPUT
TRANSCRIPT_STATUS_BODY = COLOUR_SYSTEM
TRANSCRIPT_PROACTIVE_BODY = COLOUR_SYSTEM

TRANSCRIPT_USER_PREFIX = "> "
TRANSCRIPT_ASSISTANT_PREFIX = "● "
TRANSCRIPT_PROACTIVE_PREFIX = "→ "

TRANSCRIPT_USER_PREFIX_COLOUR = COLOUR_USER_INPUT
TRANSCRIPT_ASSISTANT_PREFIX_COLOUR = COLOUR_GOLD
TRANSCRIPT_PROACTIVE_PREFIX_COLOUR = COLOUR_COMMAND
TRANSCRIPT_STATUS_PREFIX_COLOUR = COLOUR_GOLD


def transcript_body_colour(role: str) -> str:
    """Return the shared transcript body colour for a role."""

    if role == "status":
        return TRANSCRIPT_STATUS_BODY
    if role == "proactive":
        return TRANSCRIPT_PROACTIVE_BODY
    if role == "user":
        return TRANSCRIPT_USER_BODY
    return TRANSCRIPT_ASSISTANT_BODY


def transcript_prefix(role: str) -> str:
    """Return the stable transcript prefix for a role."""

    if role == "user":
        return TRANSCRIPT_USER_PREFIX
    if role == "proactive":
        return TRANSCRIPT_PROACTIVE_PREFIX
    return TRANSCRIPT_ASSISTANT_PREFIX


def transcript_prefix_colour(role: str) -> str:
    """Return the shared prefix colour for a role."""

    if role == "user":
        return TRANSCRIPT_USER_PREFIX_COLOUR
    if role == "proactive":
        return TRANSCRIPT_PROACTIVE_PREFIX_COLOUR
    if role == "status":
        return TRANSCRIPT_STATUS_PREFIX_COLOUR
    return TRANSCRIPT_ASSISTANT_PREFIX_COLOUR


def transcript_highlight_inline(role: str) -> bool:
    """User turns keep literal text; assistant-like turns may highlight inline tokens."""

    return role != "user"
