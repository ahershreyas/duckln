"""Plan 175 G1: the aware-not-dumb interaction primitive.

At a decision point Duckln EXPLAINS what's going on + the best approach + why, then asks the
user Yes / No / write-your-own — so it behaves like a situationally-aware collaborator, not a
mechanical agent. This UNIFIES the existing `select` (3-way chooser) / `approve` (binary) /
`text_prompt` (custom) callables already threaded through bring-up and recovery; it is the same
human-in-the-loop "propose → explain → approve / reject / edit" pattern used by mature agent
frameworks (AWS Bedrock, OpenAI Agents, LangChain).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

ACCEPT = "accept"
REJECT = "reject"
CUSTOM = "custom"
CANCEL = "cancel"

_REJECT_LABEL = "No — skip this"
_CUSTOM_LABEL = "Let me tell you what to do instead"
_CANCEL_LABEL = "Cancel"


@dataclass(frozen=True)
class ProposalDecision:
    """The user's answer to a proposal: accept it, reject it, supply a custom instruction, or cancel."""

    outcome: str            # ACCEPT | REJECT | CUSTOM | CANCEL
    custom_text: str = ""

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPT

    @property
    def rejected(self) -> bool:
        return self.outcome == REJECT

    @property
    def is_custom(self) -> bool:
        return self.outcome == CUSTOM and bool(self.custom_text)

    @property
    def cancelled(self) -> bool:
        return self.outcome == CANCEL


def propose_and_confirm(
    *,
    situation: str,
    recommendation: str,
    why: str = "",
    display: Callable[[str], None],
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    approve: Callable[[str], bool] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    accept_label: str = "Yes — go ahead",
    allow_cancel: bool = False,
) -> ProposalDecision:
    """Explain the SITUATION + the recommended APPROACH (+ why), then ask Yes / No / write-your-own
    (and, with ``allow_cancel``, an explicit Cancel — Plan 186 F3, the always-guide popup).

    Returns a `ProposalDecision`. Prefers the multi-way `select`; falls back to `approve` (binary)
    with a `text_prompt` follow-up for the custom path; with no interactive channel returns REJECT
    (safe).
    """
    if situation:
        display(str(situation))
    rec = str(recommendation or "").strip()
    question = f"Best approach: {rec}" + (f" — {why}" if why else "")

    if select is not None:
        options = (accept_label, _REJECT_LABEL, _CUSTOM_LABEL)
        if allow_cancel:
            options = options + (_CANCEL_LABEL,)
        label = str(select(question, options) or "").strip()
        low = label.lower()
        if label == accept_label or low.startswith("yes") or low in ("y", "ok", "go ahead"):
            return ProposalDecision(ACCEPT)
        if allow_cancel and (label == _CANCEL_LABEL or low == "cancel"):
            return ProposalDecision(CANCEL)
        if label == _CUSTOM_LABEL or low.startswith("let me") or "instead" in low or "custom" in low:
            custom = ""
            if text_prompt is not None:
                custom = str(text_prompt("What would you like me to do instead?", "") or "").strip()
            return ProposalDecision(CUSTOM, custom) if custom else ProposalDecision(REJECT)
        return ProposalDecision(REJECT)

    if approve is not None:
        if approve(f"{question}  Do this? [y/n]"):
            return ProposalDecision(ACCEPT)
        # On 'no', still let the user redirect rather than just stopping.
        if text_prompt is not None:
            custom = str(text_prompt("No problem — tell me what to do instead (blank to skip):", "") or "").strip()
            if custom:
                return ProposalDecision(CUSTOM, custom)
        return ProposalDecision(REJECT)

    return ProposalDecision(REJECT)


def _aware_interaction_skill() -> str:
    """Plan 176 F1: load the `aware_interaction` SKILL (procedural knowledge) so the LLM voices the
    proposal in the right SHAPE — comprehend → explain → recommend → offer. Best-effort."""
    try:
        from pathlib import Path as _P
        p = _P(__file__).resolve().parents[1] / "agent" / "playbooks" / "aware_interaction.md"
        txt = p.read_text(encoding="utf-8").strip()
        return txt[:4000]  # the discipline + the one-shot examples (bounded)
    except Exception:
        return ""


def build_failure_proposal(
    *,
    failed_command: str,
    error_text: str,
    repo_name: str = "",
    det_fix=None,
    llm_client: Callable[..., str] | None = None,
) -> tuple[str, str, str]:
    """Plan 175 G2 / 176 F2: COMPREHEND a failed step → (situation, recommendation, why).

    DETERMINISTIC FLOOR (always works, even with no model): the factual situation + the
    `match_deterministic_fix` candidate as the recommendation. LLM VOICE (when a model is present):
    loaded with the `aware_interaction` SKILL + these deterministic FACTS, the LLM voices the
    situation/recommendation/why in persona (and proposes a fix when there's no deterministic one).
    The LLM owns the voice over the facts; deterministic is the floor — so it's never a dumb dead-end.
    """
    cmd = (failed_command or "").strip()
    err = " ".join((error_text or "").split())[:300]
    where = f" while setting up {repo_name}" if repo_name else ""
    # --- deterministic FLOOR ---
    situation = f"The step `{cmd}` failed{where}." + (f" The error: {err}" if err else "")
    det_cmd = getattr(det_fix, "fix_command", "") if det_fix is not None else ""
    if det_cmd:
        floor_rec = f"run `{det_cmd}`"
        floor_why = (getattr(det_fix, "cause", "") or getattr(det_fix, "fix_title", "")
                     or "this addresses the failure").strip()
    else:
        floor_rec = "I don't have a confident automatic fix for this"
        floor_why = "tell me how you'd like to proceed, or configure a stronger model so I can diagnose it deeper"

    # --- LLM VOICE over the facts (loaded with the skill) ---
    if llm_client is not None and (cmd or err):
        try:
            import json
            import re
            skill = _aware_interaction_skill()
            sys = (
                (skill + "\n\n" if skill else "")
                + "A build/setup step failed. Using the discipline above, reply with ONLY JSON: "
                "{\"situation\": \"<plain one-liner of what failed + why>\", "
                "\"recommendation\": \"<the single best action — prefer the deterministic candidate "
                "if given>\", \"why\": \"<short reason>\"}. Ground every claim in the facts. No prose."
            )
            facts = (
                f"Objective: {('set up ' + repo_name) if repo_name else 'a setup step'}\n"
                f"Failed command: {cmd}\nError: {err}\n"
                f"Deterministic candidate fix: {det_cmd or '(none — propose the best fix)'}"
            )
            raw = str(llm_client(system_prompt=sys, user_message=facts) or "")
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                d = json.loads(m.group(0))
                rec = str(d.get("recommendation") or "").strip()
                if rec:
                    return (
                        str(d.get("situation") or "").strip() or situation,
                        rec,
                        str(d.get("why") or "").strip() or floor_why,
                    )
        except Exception:
            pass

    return situation, floor_rec, floor_why
