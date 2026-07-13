"""Plan 174 F6: conversation safety guardrail.

Duckln must NOT answer inappropriate / harmful / abusive / NSFW / vulgar requests, and must
NEVER emit vulgar or inappropriate content itself. This is a deterministic SAFETY FLOOR (like
the S0–S4 command gate) so it does not depend on a weak/jailbroken model:
- `is_inappropriate_request()` — a HIGH-PRECISION check that flags clearly inappropriate input
  (the obvious cases) so it's declined WITHOUT being sent to be answered. It is intentionally
  NOT exhaustive — the persona prompt handles the nuanced tail; this catches the clear cases.
- `scrub_reply()` — an OUTPUT guard: if Duckln's own generated reply contains strong profanity,
  suppress it and return a clean decline, so Duckln never emits off-color text even if the model
  slips.
- `clean_british_decline()` — a short, CLEAN, dry-British refusal that steers back to repo work.
"""

from __future__ import annotations

import re

# Clearly-harmful / inappropriate REQUEST patterns (word-boundary, high-precision — avoid false
# positives on normal dev vocabulary like "kill the process" or "abort").
_HARMFUL_REQUEST_PATTERNS = (
    r"\bhow (to|do i|can i) (make|build|create|synthesi[sz]e) (a |an )?(bomb|explosive|nerve agent|meth|methamphetamine|nuclear|biological weapon)\b",
    r"\b(kill|murder|harm|hurt|poison) (myself|yourself|him|her|them|someone|a person|people|my )\b",
    r"\b(commit|how to) suicide\b",
    r"\bchild (porn|sexual|sex)\b",
    r"\b(rape|molest)\b",
    r"\bhow (to|do i) (hack|ddos|steal|launder)\b",
    r"\bwrite (me )?(a )?(sexually explicit|erotic|porn)\b",
    r"\b(racial|ethnic) slur\b",
)

# Strong profanity tokens — used ONLY to scrub DUCKLN'S OWN output (so it never emits them). Kept
# minimal + word-boundary matched. (Not used to judge the user's input — people swear casually.)
_PROFANITY_TOKENS = (
    r"f+u+c+k", r"\bshit\b", r"\bcunt\b", r"\bbitch\b", r"\basshole\b", r"\bbastard\b",
    r"\bdick\b", r"\bslut\b", r"\bwhore\b", r"\bn[i1]gg", r"\bfaggot\b", r"\bretard\b",
)

_HARMFUL_RE = re.compile("|".join(_HARMFUL_REQUEST_PATTERNS), re.IGNORECASE)
_PROFANITY_RE = re.compile("|".join(_PROFANITY_TOKENS), re.IGNORECASE)

_DECLINES = (
    "Not happening, I'm afraid — I'm a repo manager, not that sort of bot. Point me at a repo and let's crack on.",
    "Hard pass on that one. I do repos and setups, not whatever that was — what are we getting running?",
    "I'll politely decline, ta. Bring me a repo to set up or a blocker to fix and I'm all yours.",
    "Steady on — that's not something I'll do. Shall we get back to a repo instead?",
)


def is_inappropriate_request(text: str) -> bool:
    """High-precision: True for a clearly inappropriate / harmful / explicit REQUEST."""
    return bool(_HARMFUL_RE.search(str(text or "")))


def clean_british_decline(seed: int = 0) -> str:
    """A short, CLEAN, dry-British decline that steers back to repo work (the safety-floor reply —
    deterministic so it never depends on the model). Rotates a little so it isn't identical."""
    return _DECLINES[seed % len(_DECLINES)]


def scrub_reply(text: str, *, seed: int = 0) -> str:
    """Output guard: if Duckln's OWN reply contains strong profanity, suppress it and return a
    clean decline instead — so Duckln never emits off-color text even if the model slips."""
    if text and _PROFANITY_RE.search(text):
        return clean_british_decline(seed)
    return text
