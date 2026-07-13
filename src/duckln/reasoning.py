"""Plan 178 F1 — fault-tolerant `<thought>` extraction.

A reasoning agent is asked to emit a leading `<thought>…</thought>` monologue before
its JSON/action payload. A weak or rate-limited model can TRUNCATE that block (no closing
tag) or skip it entirely. A naive `str.split` would then crash the agent loop or silently
drop the reasoning. This module separates the inner monologue from the action payload while
surviving truncated, unclosed, or missing tags — so the chain-of-thought is preserved (and
appended to `logical-thinking.md`) even when the model's output is malformed.

Mirrors the spec's `extract_reasoning_sandbox`, adapted to Duckln's naming + logging.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Sentinels the caller can recognise (distinct from any real payload).
TRUNCATED_PAYLOAD = "SYSTEM_ERROR: compute budget exceeded during the internal reasoning phase."
MISSING_THOUGHT = "CRITICAL: the agent skipped the structural reasoning step."

_THOUGHT_RE = re.compile(r"<thought>(.*?)</thought>", re.DOTALL | re.IGNORECASE)
_THOUGHT_OPEN_RE = re.compile(r"<thought>", re.IGNORECASE)
_STRIP_THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)


def extract_thinking_and_content(raw_output: str) -> tuple[str, str]:
    """Separate the inner monologue (`<thought>`) from the action/execution payload.

    Returns ``(thought, payload)``:

    - **Matched tags** → the captured thought + the payload with the block stripped.
    - **Unclosed / truncated** (`<thought>` present, no `</thought>`) → the best-effort
      partial thought + a ``TRUNCATED_PAYLOAD`` sentinel (the model ran out of tokens
      mid-reasoning; there is no usable action payload).
    - **Missing** (no `<thought>` at all) → a ``MISSING_THOUGHT`` marker as the thought +
      the whole output as the payload (a model that skipped the protocol still produced
      *something* the caller can try to parse).

    Never raises — a malformed response degrades to a best-effort split, never a crash.
    """
    sanitized = (raw_output or "").strip()
    if not sanitized:
        return (MISSING_THOUGHT, "")

    match = _THOUGHT_RE.search(sanitized)
    if match:
        thought = match.group(1).strip()
        payload = _STRIP_THOUGHT_RE.sub("", sanitized).strip()
        return (thought, payload)

    # Opened but never closed → truncated mid-thought.
    if _THOUGHT_OPEN_RE.search(sanitized):
        logger.warning("reasoning: <thought> was opened but never closed (model output truncated).")
        partial = _THOUGHT_OPEN_RE.split(sanitized, 1)[1].strip()
        return (partial, TRUNCATED_PAYLOAD)

    # No structural reasoning at all.
    logger.debug("reasoning: no <thought> boundaries in the model output.")
    return (MISSING_THOUGHT, sanitized)


def has_thought(raw_output: str) -> bool:
    """True when the output opened a `<thought>` block (closed or truncated)."""
    return bool(_THOUGHT_OPEN_RE.search(raw_output or ""))
