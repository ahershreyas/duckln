"""Follow-up resolution helpers for conversation threads."""

from __future__ import annotations

from duckln.conversation_routes.intent_schema import parse_structured_intent
from duckln.conversation_routes.repo import looks_like_repo_lifecycle_turn
from duckln.conversation_routes.social import looks_like_next_step_question, looks_like_social_or_identity_turn


def _looks_like_continue_or_approval_request(normalized_compact: str) -> bool:
    tokens = set(normalized_compact.replace("?", " ").replace(".", " ").split())
    if normalized_compact in {
        "yes", "ye", "yep", "yes please", "sure", "okay", "ok",
        "please do", "go ahead", "alright", "all right",
        "tryagain", "try again", "try",
    }:
        return True
    if {"please", "ask"} <= tokens or {"ask", "me"} <= tokens:
        return True
    if "approval" in tokens or "approve" in tokens or "prompt" in tokens:
        return True
    if "continue" in tokens or "proceed" in tokens or "retry" in tokens or "rerun" in tokens:
        return True
    if "try" in tokens and "again" in tokens:
        return True
    return False


def _should_ignore_followup_context(normalized_compact: str) -> bool:
    structured = parse_structured_intent(normalized_compact)
    if looks_like_repo_lifecycle_turn(normalized_compact):
        return True
    if looks_like_social_or_identity_turn(normalized_compact):
        return True
    if looks_like_next_step_question(normalized_compact):
        return True
    if structured.subject == "vm":
        return True
    if structured.subject == "repo" and (
        structured.action in {"run", "restart", "remove", "access", "setup", "verify"}
        or structured.scope in {"path", "path_inventory", "source", "source_inventory", "inventory", "inventory_local", "inventory_vm", "specific_status", "active", "top_n"}
        or structured.repo_name_hint is not None
    ):
        return True
    protected_phrases = (
        "do i have an active repo in my system",
        "do i have an active repo",
        "is there an active repo",
        "which repo is active",
        "what repo is active",
        "what repo are you currently tracking",
        "which repo do you currently have active",
        "show me all repos",
        "repos installed",
        "repos active",
        "worked previously",
        "do you understand what i am asking",
        "that's not what i asked",
        "that is not what i asked",
        "you are not understanding me",
        "sorry i did not understand",
        "i did not understand",
        "didn't understand",
        "can you elaborate",
        "can you explain that",
        "explain what you mean",
        "say that again simply",
        "restate that",
        "say it plainly",
    )
    return any(phrase in normalized_compact for phrase in protected_phrases)


def resolve_followup_intent(
    normalized_compact: str,
    *,
    followup_state,
    acceptance_phrase,
    thread_ids_match,
) -> str | None:
    if _should_ignore_followup_context(normalized_compact):
        return None
    if not any(
        (
            getattr(followup_state, "pending_next_action", None),
            getattr(followup_state, "pending_offer_kind", None),
            getattr(followup_state, "pending_offer_id", None),
            getattr(followup_state, "pending_repo_key", None),
            getattr(followup_state, "pending_repo_name", None),
            getattr(followup_state, "last_supervisor_decision", None),
        )
    ):
        return None
    if getattr(followup_state, "pending_offer_id", None) and not thread_ids_match(
        pending_offer_thread_id=getattr(followup_state, "pending_offer_thread_id", None),
        active_thread_id=getattr(followup_state, "active_thread_id", None),
    ):
        return None
    if acceptance_phrase(normalized_compact):
        if getattr(followup_state, "pending_offer_kind", None) == "recommend_best_fit":
            return "repo_recommendation_single"
        if getattr(followup_state, "pending_offer_kind", None) == "inspect_requirements":
            return "repo_requirements"
        if getattr(followup_state, "pending_offer_kind", None) == "set_up_repo":
            return "setup_request"
        if getattr(followup_state, "pending_offer_kind", None) == "run_repo":
            return "repo_run"
        if getattr(followup_state, "pending_offer_kind", None) == "verify_repo":
            return "repo_verify"
        if getattr(followup_state, "pending_offer_kind", None) == "stop_repo":
            return "repo_stop"
        if getattr(followup_state, "pending_offer_kind", None) == "explain_rationale":
            return "repo_recommendation_rationale"
        if any(token in normalized_compact for token in ("suggest", "recommend", "best fit", "best for my machine", "best for my system")):
            return "repo_recommendation_single"
        if any(token in normalized_compact for token in ("inspect", "requirement")):
            return "repo_requirements"
    if _looks_like_continue_or_approval_request(normalized_compact):
        if getattr(followup_state, "pending_next_action", None) == "recommend_best_fit":
            return "repo_recommendation_single"
        if getattr(followup_state, "pending_next_action", None) == "inspect_requirements":
            return "repo_requirements"
        if getattr(followup_state, "pending_next_action", None) == "run_repo":
            return "repo_run"
        if getattr(followup_state, "pending_next_action", None) == "verify_repo":
            return "repo_verify"
        if getattr(followup_state, "pending_next_action", None) == "stop_repo":
            return "repo_stop"
        if getattr(followup_state, "pending_next_action", None) == "set_up_repo":
            return "setup_request"
        if getattr(followup_state, "pending_next_action", None) == "explain_rationale":
            return "repo_recommendation_rationale"
    if any(phrase in normalized_compact for phrase in ("inspect its requirement", "inspect its requirements", "inspect requirements", "its requirement", "its requirements")):
        return "repo_requirements"
    if any(phrase in normalized_compact for phrase in ("could inspect", "please inspect", "inspect that", "inspect it")):
        return "repo_requirements"
    if any(
        phrase in normalized_compact
        for phrase in (
            "second choice",
            "second best",
            "second recommendation",
            "other option",
            "other repo",
            "what else would you pick",
            "what else do you recommend",
            "so no second choice",
        )
    ):
        return "repo_alternatives"
    if any(phrase in normalized_compact for phrase in ("in your memory", "in your memeory")) and (
        getattr(followup_state, "pending_repo_name", None) or getattr(followup_state, "last_discussed_repo_name", None)
    ):
        return "repo_memory_meta"
    if any(
        phrase in normalized_compact
        for phrase in (
            "what is this repo",
            "what does this repo do",
            "give me an idea what",
            "tell me about this repo",
            "what is ",
            "what does ",
        )
    ):
        return "repo_overview"
    if getattr(followup_state, "last_answer_style", None) == "slash_runtime" and any(
        phrase in normalized_compact
        for phrase in ("set it up", "run it", "inspect", "requirements", "why that repo", "what is it", "does duckln remember it")
    ):
        return "slash_runtime_followup"
    if normalized_compact in {"why", "tell me more"} or normalized_compact.startswith("why "):
        return "repo_recommendation_rationale"
    if any(phrase in normalized_compact for phrase in ("set it up", "setup it", "can you set it up")):
        return "setup_request"
    if any(phrase in normalized_compact for phrase in ("restart it", "rerun it", "re-run it", "start it again", "run it again")):
        return "repo_restart"
    if any(phrase in normalized_compact for phrase in ("run it", "launch it", "start it")):
        return "repo_run"
    if any(phrase in normalized_compact for phrase in ("stop it", "kill it", "terminate it")):
        return "repo_stop"
    if any(phrase in normalized_compact for phrase in ("verify it", "check it", "check if it is ready")):
        return "repo_verify"
    if any(phrase in normalized_compact for phrase in ("show logs", "tail logs", "show me logs")):
        return "repo_logs"
    if any(phrase in normalized_compact for phrase in ("already installed", "already setup", "already set up", "is it installed", "is it setup", "is it set up")):
        return "repo_status"
    if any(phrase in normalized_compact for phrase in ("run comfortably", "okay on my machine", "too heavy")):
        return "repo_fit_judgment"
    return None
