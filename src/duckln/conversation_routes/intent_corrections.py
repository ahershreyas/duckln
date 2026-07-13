"""Conversation intent correction helpers extracted from the supervisor."""

from __future__ import annotations

from duckln.conversation_routes.intent_schema import parse_structured_intent


def resolve_corrected_intent(
    normalized_compact: str,
    *,
    base_intent: str,
    recent_turns,
    recent_replies,
    config_dir,
    has_promoted_phrase_bucket,
) -> str:
    if base_intent not in {"generic", "help_request", "setup_request"}:
        return base_intent
    bucket = phrase_learning_bucket(normalized_compact)
    if bucket == "recommendation":
        if has_promoted_phrase_bucket(config_dir, bucket) or recent_replies_were_command_deflections(recent_replies):
            return "repo_recommendation_single"
    if bucket == "fit":
        if has_promoted_phrase_bucket(config_dir, bucket) or recent_replies_were_command_deflections(recent_replies):
            return "repo_fit_judgment"
    if bucket == "inventory":
        return "repo_inventory"
    if bucket == "status":
        return "repo_status"
    if any("recommend" in turn.content.lower() or "best" in turn.content.lower() for turn in recent_turns[-2:]) and bucket == "recommendation":
        return "repo_recommendation_single"
    return base_intent


def phrase_learning_bucket(normalized_or_raw_message: str) -> str | None:
    normalized = " ".join(normalized_or_raw_message.strip().lower().split())
    structured = parse_structured_intent(normalized)
    if structured.subject == "repo" and structured.scope in {"inventory", "inventory_local", "inventory_vm"}:
        return "inventory"
    if structured.subject == "repo" and structured.scope in {"specific_status", "active"}:
        return "status"
    if structured.subject == "repo" and structured.action in {"recommend", "compare"}:
        return "recommendation"
    if any(token in normalized for token in ("recommend", "best", "suit my system", "suit my machine", "work best", "run best")):
        return "recommendation"
    if any(token in normalized for token in ("comfortably", "too heavy", "okay on my machine", "okay on this machine")):
        return "fit"
    if any(token in normalized for token in ("which repos do i have", "what repos do i have", "already setup", "already set up", "repos setup")):
        return "inventory"
    if any(token in normalized for token in ("is whisper setup", "is it setup", "is it installed", "is it running", "is it ready")):
        return "status"
    if any(token in normalized for token in ("system capacity", "my machine", "my system specs", "ram cpu gpu")):
        return "system"
    return None


def recent_replies_were_command_deflections(recent_replies) -> bool:
    if not recent_replies:
        return False
    recent_steers = [reply.steer for reply in recent_replies[-2:] if reply.steer]
    return bool(recent_steers) and all(steer in {"/repos", "/help"} for steer in recent_steers)


def looks_like_repeated_deflection_breakout(intent: str, recent_replies) -> bool:
    if not intent.startswith("repo_"):
        return False
    return recent_replies_were_command_deflections(recent_replies)
