"""Social and identity detection helpers for the conversation supervisor."""

from __future__ import annotations

import re


def extract_user_alias(message: str) -> str | None:
    normalized = " ".join(message.strip().split())
    patterns = (
        r"^(?:i am|i'm)\s+(.+)$",
        r"^(?:my name is|call me|you can call me|please call me|can you call me|change my alias to|change my name to|use)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip().strip(".!?")
        candidate = re.sub(r"^(?:to\s+|as\s+)", "", candidate, flags=re.IGNORECASE)
        if 1 <= len(candidate) <= 40 and not any(token in candidate.lower() for token in ("repo", "setup", "help me", "run this")):
            return candidate
    return None


def looks_like_social_or_identity_turn(normalized_compact: str) -> bool:
    return any(
        phrase in normalized_compact
        for phrase in (
            "who am i",
            "remember me",
            "call me",
            "my name is",
            "i am ",
            "i'm ",
            "how are you",
            "what can you help me with",
            "give me 5 points",
            "five points",
            "what should i call you",
            "who are you",
        )
    )


def looks_like_next_step_question(normalized_compact: str) -> bool:
    return any(
        phrase in normalized_compact
        for phrase in (
            "what should i do next",
            "what do i do next",
            "tell me what to do next",
            "what next",
            "next step",
            "what's next",
            "whats next",
        )
    )


def acceptance_phrase(normalized_compact: str) -> str | None:
    compact = normalized_compact.strip()
    if compact in {"yes", "yes please", "sure", "okay", "ok", "please do", "go ahead", "do it"}:
        return compact
    if compact.startswith(("yes p", "yes pl", "yep", "yeah")):
        return compact
    return None
