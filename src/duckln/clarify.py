"""Plan 191 — an INTELLIGENT, adaptive clarification engine.

One reusable module every ambiguous surface calls instead of asking a fixed number of
questions. The LLM DECIDES how many questions to ask (1, up to a hard cap of 4) based on how
much clarity it actually needs, and generates CLEVER, grounded options; the deterministic layer
is the FLOOR — the grounded facts each surface supplies, the max-4 cap, the radio/Other/Esc
primitive, a single-question fallback when there's no model, and a strong record of the reasoning
to `logical-thinking.md`. Depth scales with the model; safety/graceful-degradation is guaranteed.

Rendered through the upgraded choice overlay (Plan 191 F6): NUMBERED options + an inline
"Other — type your own" free-text row + an "Esc to cancel" footer, with long options that wrap.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

MAX_ROUNDS = 4  # hard cap — Duckln NEVER asks more than four clarifying questions.


@dataclass(frozen=True)
class ClarifyFacts:
    """The deterministic grounding a surface supplies: the topic, the candidate option labels
    (grounded in real data — catalog domains, routes, repo names), and any short context."""

    topic: str
    options: tuple[str, ...] = ()
    context: str = ""


@dataclass(frozen=True)
class ClarifyResult:
    resolved: bool
    value: str = ""
    cancelled: bool = False
    rounds_asked: int = 0
    qa: tuple[tuple[str, str], ...] = ()
    # For the non-overlay (fallback) path: the caller displays this as a text question and
    # persists a pending clarify so the NEXT typed message is reasoned continue-vs-new (F2).
    pending_question: tuple[str, tuple[str, ...]] | None = None


def _skill_excerpt() -> str:
    try:
        p = Path(__file__).resolve().parents[1] / "agent" / "playbooks" / "aware_interaction.md"
        return p.read_text(encoding="utf-8").strip()[:4000]
    except Exception:
        return ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")[:48] or "clarify"


def _parse_json_object(raw: str) -> dict:
    m = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


_PLANNER_SYSTEM = (
    "You help Duckln (a terminal repo manager) ask the FEWEST, CLEVEREST clarifying questions to "
    "pin down what the user wants. Given the user's ASK, the known FACTS (candidate options grounded "
    "in Duckln's real data), and the Q&A SO FAR, decide whether you now have ENOUGH to act.\n"
    "Reply with ONLY a JSON object, no prose:\n"
    '{"enough": true|false, "resolution": "<the single best answer/value when enough>", '
    '"question": {"text": "<one short, clever question>", "options": ["<3-4 grounded, distinct labels>"]}}\n'
    "Rules: ask AT MOST one question at a time; ask as FEW as possible and STOP as soon as you can "
    "act (often zero or one — set enough:true the moment the ask already names the answer). Options "
    "MUST be grounded in the FACTS (real domains/repos/routes), concise, and mutually distinct; do "
    "NOT add an 'Other' option (the UI adds it). Never ask more than four questions total."
)


def _plan_next(*, ask: str, facts: ClarifyFacts, qa: list, llm_client, skill: str) -> dict:
    """Return the LLM's structured plan for the next step (or {} on any failure)."""
    if llm_client is None:
        return {}
    qa_txt = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa) or "(none yet)"
    user = (
        f"ASK: {ask}\n"
        f"TOPIC: {facts.topic}\n"
        f"FACTS — candidate options: {', '.join(facts.options) if facts.options else '(none provided)'}\n"
        + (f"CONTEXT: {facts.context}\n" if facts.context else "")
        + f"Q&A SO FAR:\n{qa_txt}\n"
        "Decide enough vs the next single clever question."
    )
    try:
        raw = llm_client(system_prompt=skill + "\n\n" + _PLANNER_SYSTEM, user_message=user)
    except Exception:
        return {}
    return _parse_json_object(raw)


def _floor_plan(*, facts: ClarifyFacts, qa: list) -> dict:
    """Deterministic FLOOR (no model): ONE grounded question from the facts, then done."""
    if qa:  # already asked once → resolve with that answer
        return {"enough": True, "resolution": qa[-1][1]}
    if not facts.options:
        return {"enough": True, "resolution": ""}
    text = f"To point you right — {facts.topic}?" if facts.topic else "Which of these did you mean?"
    return {"enough": False, "question": {"text": text, "options": list(facts.options[:4])}}


def _record(config_dir, *, ask: str, lines: list, display=None) -> None:
    """F3: strongly record the clarification reasoning to `logical-thinking.md` + surface a link."""
    try:
        from duckln.recovery import append_thinking_log, surface_thinking_link

        path = append_thinking_log(
            config_dir, repo_slug=_slug(ask), title=f"Clarifying: {ask}",
            surface="clarify", lines=[ln for ln in lines if str(ln).strip()],
        )
        if display is not None and path is not None:
            surface_thinking_link(path, display)
    except Exception:
        pass


def run_clarification(
    *,
    ask: str,
    facts: ClarifyFacts,
    channel,
    config_dir,
    llm_client=None,
    display: Callable[[str], None] | None = None,
    max_rounds: int = MAX_ROUNDS,
) -> ClarifyResult:
    """Adaptively clarify the user's `ask`. Returns a ClarifyResult; the caller acts on `.value`."""
    cap = max(1, min(int(max_rounds), MAX_ROUNDS))
    skill = _skill_excerpt()
    overlay = bool(channel is not None and getattr(channel, "supports_overlay", lambda: True)()
                   and hasattr(channel, "select_choice"))
    qa: list = []
    think: list = [f"Ask: {ask!r} · topic={facts.topic!r} · grounded options={list(facts.options)}"]

    rounds = 0
    while rounds < cap:
        plan = _plan_next(ask=ask, facts=facts, qa=qa, llm_client=llm_client, skill=skill) if llm_client else {}
        if not plan:
            plan = _floor_plan(facts=facts, qa=qa)

        if plan.get("enough"):
            resolution = str(plan.get("resolution") or (qa[-1][1] if qa else "")).strip()
            think.append(f"Enough after {rounds} question(s) → resolving with {resolution!r}.")
            _record(config_dir, ask=ask, lines=think, display=display)
            return ClarifyResult(resolved=True, value=resolution, rounds_asked=rounds, qa=tuple(qa))

        q = plan.get("question") or {}
        text = str(q.get("text") or facts.topic or "Which did you mean?").strip()
        opts = tuple(str(o).strip() for o in (q.get("options") or facts.options) if str(o).strip())[:4]
        if not opts:  # nothing to ask → resolve best-effort
            think.append("No grounded options to ask → resolving from context.")
            _record(config_dir, ask=ask, lines=think, display=display)
            return ClarifyResult(resolved=True, value=(qa[-1][1] if qa else ""), rounds_asked=rounds, qa=tuple(qa))

        if not overlay:
            think.append(f"No interactive overlay → asking as text + persisting for continue-vs-new: {text!r} {list(opts)}")
            # Record the reasoning, but don't surface a link yet — the clarify isn't resolved.
            _record(config_dir, ask=ask, lines=think, display=None)
            return ClarifyResult(resolved=False, pending_question=(text, opts), rounds_asked=rounds, qa=tuple(qa))

        think.append(f"Q{rounds + 1}: {text!r} — options {list(opts)} (+ Other), because clarity still needed.")
        answer = channel.select_choice(text, opts, allow_other=True)
        if answer is None:
            think.append("User pressed Esc → cancelled the clarification.")
            _record(config_dir, ask=ask, lines=think, display=display)
            return ClarifyResult(resolved=False, cancelled=True, rounds_asked=rounds, qa=tuple(qa))
        kind = "picked option" if answer in opts else "typed Other"
        think.append(f"A{rounds + 1}: {answer!r} ({kind}).")
        qa.append((text, answer))
        rounds += 1

    # Hit the hard cap — resolve best-effort from what we gathered.
    final = _plan_next(ask=ask, facts=facts, qa=qa, llm_client=llm_client, skill=skill) if llm_client else {}
    resolution = str((final.get("resolution") if final.get("enough") else "") or (qa[-1][1] if qa else "")).strip()
    think.append(f"Reached the {cap}-question cap → resolving with {resolution!r}.")
    _record(config_dir, ask=ask, lines=think, display=display)
    return ClarifyResult(resolved=True, value=resolution, rounds_asked=rounds, qa=tuple(qa))


_CONTINUE_SYSTEM = (
    "Duckln asked the user a clarifying question and is waiting. Decide whether the user's NEW message "
    "ANSWERS that pending question (CONTINUE) or starts a DIFFERENT request (NEW). Reply with ONLY "
    'a JSON object: {"decision": "continue"|"new"}. A short reply that names or picks one of the '
    "pending options is CONTINUE; a new command, a repo/URL, a `/slash`, or an unrelated topic is NEW."
)


def decide_continue_or_new(*, message: str, pending_question: str, options: Sequence[str], llm_client=None) -> str:
    """F2: is a typed `message` continuing the pending clarify, or a NEW request? → 'continue' | 'new'."""
    m = str(message or "").strip()
    low = m.lower()
    if not m:
        return "new"
    # Deterministic floor (model-independent, high precision).
    if low.startswith("/") or re.search(r"https?://|github\.com/", low):
        return "new"
    for opt in options:
        o = str(opt or "").strip().lower()
        if o and (low == o or o in low or low in o):
            return "continue"
    if re.match(r"^(the\s+)?(first|second|third|fourth|1|2|3|4|one|two|three|four|option\s*\d)\b", low):
        return "continue"
    _NEW_VERBS = ("set up", "setup", "run ", "install", "clone", "deploy", "open ", "fix ", "build ", "stop ")
    if any(v in low for v in _NEW_VERBS):
        return "new"
    if llm_client is not None:
        try:
            raw = llm_client(
                system_prompt=_CONTINUE_SYSTEM,
                user_message=f"Pending question: {pending_question}\nPending options: {', '.join(options)}\nUser message: {m}",
            )
            dec = str(_parse_json_object(raw).get("decision") or "").strip().lower()
            if dec in ("continue", "new"):
                return dec
        except Exception:
            pass
    # No model: a short bare reply is likely an answer; a longer/sentence-like message is likely new.
    return "continue" if len(m.split()) <= 4 else "new"
