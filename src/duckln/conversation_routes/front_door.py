"""Front-door categorization for free-text conversation turns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrontDoorClassification:
    """High-level category chosen before detailed route resolution."""

    category: str
    reason: str
    workflow_priority_applied: bool = False


def _token_set(normalized_compact: str) -> set[str]:
    cleaned = normalized_compact
    for marker in ("?", ".", ",", "!", ":", ";", "(", ")"):
        cleaned = cleaned.replace(marker, " ")
    return {token for token in cleaned.split() if token}


def looks_like_continue_or_approval_request(normalized_compact: str) -> bool:
    """Return True when the user is trying to continue the live workflow."""

    tokens = _token_set(normalized_compact)
    if normalized_compact in {
        "yes",
        "ye",
        "yep",
        "yes please",
        "please do",
        "do it",
        "go ahead",
        "sure",
        "ok",
        "okay",
        "alright",
        "all right",
        "continue",
        "proceed",
        "tryagain",
        "try again",
        "try",
    }:
        return True
    if {"please", "ask"} <= tokens or {"ask", "me"} <= tokens:
        return True
    if "approval" in tokens or "approve" in tokens or "prompt" in tokens:
        return True
    if "continue" in tokens or "proceed" in tokens or "retry" in tokens or "rerun" in tokens:
        return True
    if "repair" in tokens or "fix" in tokens or "solve" in tokens:
        return True
    if "show" in tokens and ("approval" in tokens or "prompt" in tokens):
        return True
    if "try" in tokens and "again" in tokens:
        return True
    return False


def classify_front_door(
    *,
    normalized_compact: str,
    base_intent: str,
    structured,
    context,
    looks_like_social_or_identity_turn,
    looks_like_next_step_question,
) -> FrontDoorClassification:
    """Classify the top-level type of request before detailed route scoring."""

    workflow = getattr(context, "workflow_state", None)
    followup_state = getattr(context, "followup_state", None)
    active_issue_kind = str(getattr(workflow, "active_issue_kind", "") or "").strip().lower()
    active_repair_phase = str(getattr(workflow, "active_repair_phase", "") or "").strip().lower()
    pending_next_action = str(getattr(followup_state, "pending_next_action", "") or "").strip().lower()
    pending_offer_kind = str(getattr(followup_state, "pending_offer_kind", "") or "").strip().lower()

    if (
        active_issue_kind
        or active_repair_phase
        or pending_next_action in {"repair_repo_runtime", "run_repo", "verify_repo", "stop_repo", "set_up_repo"}
        or pending_offer_kind in {"repair_repo_runtime", "run_repo", "verify_repo", "stop_repo", "set_up_repo"}
    ) and (
        looks_like_continue_or_approval_request(normalized_compact)
        or looks_like_next_step_question(normalized_compact)
    ):
        return FrontDoorClassification(
            category="workflow_followup",
            reason="active workflow state is present and the user is trying to continue or approve the current path",
            workflow_priority_applied=True,
        )

    if base_intent in {"conversation_repair", "conversation_restate", "repo_active_explanation"}:
        return FrontDoorClassification(
            category="conversation_repair",
            reason="the user is explicitly asking Duckln to repair or restate the previous answer",
        )

    if looks_like_social_or_identity_turn(normalized_compact) or base_intent in {"greeting", "rapport", "capability", "user_identity_meta", "user_alias_set"}:
        return FrontDoorClassification(
            category="social_or_capability",
            reason="the user is asking a social, identity, or capability question",
        )

    if structured.subject == "repo" and structured.action in {"run", "restart", "stop", "verify", "logs", "remove"}:
        return FrontDoorClassification(
            category="repo_runtime",
            reason="the user is asking for a repo runtime action",
        )

    if structured.subject == "repo" and structured.scope in {"inventory", "specific_status", "path", "source"}:
        return FrontDoorClassification(
            category="repo_state",
            reason="the user is asking about tracked repo state",
        )

    if structured.subject == "vm":
        return FrontDoorClassification(
            category="vm",
            reason="the user is asking a VM-scoped question",
        )

    if structured.subject == "system" or base_intent in {"system_capacity", "system_followup", "system_verify"}:
        return FrontDoorClassification(
            category="system",
            reason="the user is asking about the machine or a system verification path",
        )

    if "repo" in normalized_compact or getattr(context.thread_state, "active_repo_name", None):
        return FrontDoorClassification(
            category="repo_general",
            reason="repo language is present even if the detailed route still needs clarification",
        )

    return FrontDoorClassification(
        category="ambiguous",
        reason="the user request is still broad, so Duckln should clarify from live context instead of using a generic fallback",
    )
