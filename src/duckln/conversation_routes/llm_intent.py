"""Plan 174 F2: the cascade CATCH-ALL — an LLM intent classifier for the open-ended tail.

Deterministic routing handles the clear-cut cases (precision). When it's ambiguous — a bare
question, social chatter, or anything novel — we don't enumerate keywords; we ask the LLM ONE
cheap structured question: is this about the user's active repo, or general/off-topic? Only a
CONFIDENT repo verdict routes to the repo agent; everything else falls through to conversation
(the safe default), so a random message never lands in repo_qa + a web.search loop.
"""

from __future__ import annotations

import json
import re
from typing import Callable

_SYSTEM_PROMPT = (
    "You are the intent router for a developer tool ('Duckln') that sets up, runs, and fixes code "
    "repositories AND answers technical questions. Classify the user's message into EXACTLY ONE "
    "route and reply with ONLY a JSON object: {\"route\": \"<route>\"}. No prose.\n"
    "Routes:\n"
    "- repo_question: a question about the user's ACTIVE code repository — its code, files, setup, "
    "build, run, tests, dependencies, or an error IN IT.\n"
    "- repo_action: a request to CHANGE that repository — edit, fix, add, refactor, or run it.\n"
    "- technical: a general technical / programming / software / cloud / GPU / systems / "
    "reverse-engineering question NOT tied to the active repo (e.g. 'how does recursion work', "
    "'explain how CUDA works', 'AWS vs GCP', 'what is a hash map'), OR a question about what DUCKLN "
    "can do ('can you use a GPU', 'do you support docker', 'are you able to ...').\n"
    "- social: greetings, small talk, jokes, weather, personal or non-technical chatter, a question "
    "about DUCKLN ITSELF (what YOU can do, do YOU support X, YOUR repos/catalog/capabilities, who are "
    "you, what model are you), a request to RECOMMEND / SUGGEST / DISCOVER a repo ('recommend a repo', "
    "'which repo should I try', 'best repo for X', 'top repos'), or anything unclear that isn't a "
    "general technical concept.\n"
    "Rules: a question about the current SESSION/STATE ('what's the status', 'which VM', 'what mode') "
    "is NOT repo_question. A question about DUCKLN itself, or a repo RECOMMENDATION request, is "
    "'social' — NEVER 'repo_action' (do not act on the repo) and NEVER 'repo_question' (do not read "
    "it). 'technical' is only an IMPERSONAL general concept (recursion, CUDA, 'how does docker work'); "
    "use 'social' for genuinely non-technical chatter and for anything about Duckln itself."
)

_VALID = ("repo_question", "repo_action", "technical", "social")


def classify_repo_relevance(message: str, *, llm_client: Callable[..., str] | None) -> str:
    """Return 'repo_question' | 'repo_action' | 'general', or '' when there's no model / the
    reply can't be parsed (caller treats '' as 'not a confident repo route' → conversation)."""
    if llm_client is None or not str(message or "").strip():
        return ""
    try:
        raw = str(llm_client(system_prompt=_SYSTEM_PROMPT, user_message=message) or "")
    except Exception:
        return ""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return ""
    try:
        route = str(json.loads(m.group(0)).get("route") or "").strip().lower()
    except Exception:
        return ""
    return route if route in _VALID else ""


# ---------------------------------------------------------------------------
# Plan 192 — Tier 3: context-aware intent classifier with a CONFIDENCE.
# The SYSTEM + USER prompts below are used VERBATIM from the user's design brief
# (docs/knowledge/intent_routing_design.md §5), only adapted to name Duckln's surfaces.
# ---------------------------------------------------------------------------

_TIER3_INTENTS = ("repo_task", "status", "conversation", "ambiguous")
TIER3_CONFIDENCE_THRESHOLD = 0.6  # doc §7: act at/above; clarify below; "ambiguous" always clarifies.

_TIER3_SYSTEM_PROMPT = (
    "You are Duckln's intent classifier. Duckln is a terminal-first repository manager: it sets up, "
    "runs, explains, and fixes code repositories, and it can run recurring monitoring loops.\n"
    "\n"
    "You are given the recent conversation and the user's latest message. Decide what the user's "
    "latest message intends, USING the conversation context — a short message like \"when will this "
    "be done?\" or \"is it ready?\" usually refers to work already in progress earlier in the "
    "conversation, not a new request.\n"
    "\n"
    "Classify into EXACTLY ONE intent:\n"
    "- \"repo_task\": the user wants Duckln to act on a repository (set up, run, build, clone, "
    "install, fix, deploy, test, or similar), OR is answering/continuing such a request.\n"
    "- \"status\": the user is asking about the state, progress, or timing of work already underway "
    "in this conversation (\"when will this be done?\", \"is it ready?\", \"how long?\").\n"
    "- \"conversation\": small talk, greetings, thanks, questions about you, or anything not about a "
    "repository or ongoing work (\"how are you?\", \"r you ok?\", \"tell me a joke\").\n"
    "- \"ambiguous\": you genuinely cannot tell which of the above it is, even with the context.\n"
    "\n"
    "Rules:\n"
    "- Judge the LATEST message, using earlier turns only as context.\n"
    "- Do not assume \"repo_task\" just because Duckln is a repo tool — most greetings are "
    "\"conversation\".\n"
    "- If the message has no repository reference and no tie to ongoing work, it is \"conversation\", "
    "not \"ambiguous\".\n"
    "- Use \"ambiguous\" only when two or more intents are genuinely plausible and you cannot choose.\n"
    "- Output ONLY one JSON object, no prose, no markdown fences:\n"
    "  {\"intent\": \"repo_task|status|conversation|ambiguous\", \"confidence\": 0.0-1.0, \"rationale\": \"one short line\"}\n"
    "- \"confidence\" is your genuine certainty. Use < 0.6 when unsure — a low score sends the "
    "message to a clarifying question, which is safer than guessing wrong."
)


def _tier3_user_prompt(conversation_context: str, latest_user_message: str) -> str:
    return (
        "Recent conversation (oldest to newest):\n"
        f"{conversation_context or '(none)'}\n"
        "\n"
        "User's latest message:\n"
        f"{latest_user_message}\n"
        "\n"
        "Classify the latest message. Output only the JSON object."
    )


def classify_intent_with_context(
    latest_user_message: str,
    conversation_context: str = "",
    *,
    llm_client: Callable[..., str] | None,
) -> tuple[str, float, str]:
    """Tier 3 (the uncertain middle): classify with conversation context, returning
    (intent, confidence, rationale). intent ∈ repo_task|status|conversation|ambiguous.
    Returns ("ambiguous", 0.0, ...) when there's no model / the reply can't be parsed
    (the caller then clarifies — never guesses)."""
    if llm_client is None or not str(latest_user_message or "").strip():
        return ("ambiguous", 0.0, "no model or empty message")
    raw = ""
    for _attempt in range(2):  # doc §5: allow one extra retry for shaky small-model JSON
        try:
            raw = str(llm_client(
                system_prompt=_TIER3_SYSTEM_PROMPT,
                user_message=_tier3_user_prompt(conversation_context, str(latest_user_message)),
            ) or "")
        except Exception:
            return ("ambiguous", 0.0, "classifier call failed")
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        intent = str(obj.get("intent") or "").strip().lower()
        if intent not in _TIER3_INTENTS:
            continue
        try:
            confidence = float(obj.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        rationale = str(obj.get("rationale") or "").strip()[:200]
        return (intent, confidence, rationale)
    return ("ambiguous", 0.0, "unparseable classifier reply")


# ---------------------------------------------------------------------------
# Plan 196 F14 — the paraphrase tail for STATE/INVENTORY questions. The deterministic
# regexes (main._INV_*_RE / _PREV_REPO_RE) are the precision fast-path; this LLM classifier
# handles ANY other phrasing ("which machines do I have and what's on them", "are any
# containers up", "what were we building yesterday") so the capability is defined by INTENT,
# not an enumerated phrase list. It only classifies — the ANSWER is always from real state.
# ---------------------------------------------------------------------------

_INVENTORY_INTENTS = ("inventory_vm", "inventory_docker", "inventory_repo", "last_project", "none")
INVENTORY_CONFIDENCE_THRESHOLD = 0.6

_INVENTORY_SYSTEM_PROMPT = (
    "You classify a user's message about their local infrastructure/work state for a terminal "
    "tool named Duckln that sets up repos and manages VMs and Docker containers. Decide which ONE "
    "of these the user is asking to see, or 'none' if the message is not about listing/counting "
    "their infrastructure or recalling past work:\n"
    "- \"inventory_vm\": list/count their VMs (Multipass virtual machines), or which VMs exist and "
    "what repos run on them (\"which machines do I have\", \"list my vms and their projects\").\n"
    "- \"inventory_docker\": list/count their Docker images or containers, how many are running/in "
    "use, or which project each maps to (\"are any containers up\", \"what docker images do I have\").\n"
    "- \"inventory_repo\": list/count the repos/projects they have set up or worked on in this tool "
    "(\"what repos have we set up\", \"show my projects\").\n"
    "- \"last_project\": which repo/project/app they most recently worked on (\"what were we building "
    "yesterday\", \"our last project\").\n"
    "- \"none\": anything else (greetings, a request to set up/run/fix a repo, general questions).\n"
    "\n"
    "Respond with ONLY JSON: {\"intent\": \"inventory_vm|inventory_docker|inventory_repo|last_project|"
    "none\", \"confidence\": 0.0-1.0}\n"
)


def classify_inventory_intent(
    latest_user_message: str,
    *,
    llm_client: Callable[..., str] | None,
) -> tuple[str, float]:
    """Plan 196 F14: classify a state/inventory paraphrase into one of _INVENTORY_INTENTS.
    Returns (intent, confidence). ("none", 0.0) when no model / unparseable — the caller
    then falls through to the normal cascade (never a wrong answer)."""
    if llm_client is None or not str(latest_user_message or "").strip():
        return ("none", 0.0)
    for _attempt in range(2):
        try:
            raw = str(llm_client(
                system_prompt=_INVENTORY_SYSTEM_PROMPT,
                user_message=f"Message: {str(latest_user_message)[:400]}\nJSON:",
            ) or "")
        except Exception:
            return ("none", 0.0)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        intent = str(obj.get("intent") or "").strip().lower()
        if intent not in _INVENTORY_INTENTS:
            continue
        try:
            confidence = max(0.0, min(1.0, float(obj.get("confidence"))))
        except (TypeError, ValueError):
            confidence = 0.0
        return (intent, confidence)
    return ("none", 0.0)


# ---------------------------------------------------------------------------
# Plan 192 — Tier 4: clarify on the RIGHT axis (repo job vs just chatting).
# The SYSTEM + USER prompts below are VERBATIM from the design brief (§6).
# ---------------------------------------------------------------------------

_TIER4_SYSTEM_PROMPT = (
    "You are Duckln, a terminal-first repo manager. The user's last message was ambiguous — it "
    "might be a request to work on a repository, or it might just be conversation, and you could not "
    "tell which.\n"
    "\n"
    "Ask ONE short, friendly clarifying question that lets the user resolve it in a word or two. Do "
    "NOT assume it is about a repository. Do NOT list repo-specific options (like \"status or path\") "
    "unless you have already established the user is talking about a repo — you have not.\n"
    "\n"
    "Keep it to one line, in Duckln's dry, practical terminal voice. Offer the two plausible "
    "directions plainly. Do not lecture, do not over-explain.\n"
    "\n"
    "Output ONLY the clarifying question text — no JSON, no preamble."
)

# Deterministic FLOOR (doc-faithful shape) used when no model is reachable — never a dead-end.
_TIER4_FALLBACK = "Did you mean a repo task, or are we just chatting? Tell me in a word and I'll pick it up."


def phrase_axis_clarification(
    latest_user_message: str,
    conversation_context: str = "",
    *,
    llm_client: Callable[..., str] | None,
) -> str:
    """Tier 4: one short 'repo job or just chatting?' question — never the repo-only clarifier.
    Falls back to a neutral deterministic line when there's no model (never a dead-end)."""
    if llm_client is None:
        return _TIER4_FALLBACK
    user = (
        "Recent conversation (oldest to newest):\n"
        f"{conversation_context or '(none)'}\n"
        "\n"
        "User's latest (ambiguous) message:\n"
        f"{latest_user_message}\n"
        "\n"
        "Ask one clarifying question."
    )
    try:
        text = str(llm_client(system_prompt=_TIER4_SYSTEM_PROMPT, user_message=user) or "").strip()
    except Exception:
        text = ""
    # Strip any accidental JSON/preamble; keep it to one clean line.
    text = text.splitlines()[0].strip() if text else ""
    return text or _TIER4_FALLBACK
