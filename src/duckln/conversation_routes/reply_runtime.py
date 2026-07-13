"""Small reply/runtime helpers extracted from conversation_agent."""

from __future__ import annotations

import re


def phrase_matches(normalized_compact: str, phrase: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])"
    return re.search(pattern, normalized_compact) is not None


def looks_like_command_surface_request(normalized_compact: str) -> bool:
    keywords = ("provider", "model", "mode", "vm", "config", "healthcheck", "health")
    verbs = ("change", "switch", "open", "check", "help", "configure")
    return any(keyword in normalized_compact for keyword in keywords) and any(verb in normalized_compact for verb in verbs)


def pick_free_text_reply(
    options,
    normalized_compact: str,
    recent_replies,
    *,
    reply_factory,
):
    if not options:
        return reply_factory("Tell me what you want to change.", intent="generic")
    seed = (sum(ord(char) for char in normalized_compact) + len(recent_replies)) % len(options)
    ordered = options[seed:] + options[:seed]
    recent_texts = {reply.text for reply in recent_replies[-2:]}
    recent_steers = [reply.steer for reply in recent_replies[-2:] if reply.steer]
    for option in ordered:
        if option.text in recent_texts:
            continue
        if option.steer is not None and len(recent_steers) >= 2 and all(steer == option.steer for steer in recent_steers[-2:]):
            continue
        return option
    return ordered[len(recent_replies) % len(ordered)]


def pick_text_option(options, recent_replies) -> str:
    recent_texts = {reply.text for reply in recent_replies[-2:]}
    for option in options:
        if option not in recent_texts:
            return option
    return options[0]


def free_text_reply_from_draft(draft, *, specialist_name: str, reply_factory):
    return reply_factory(
        draft.text,
        intent=draft.intent,
        steer=draft.steer,
        specialist_name=specialist_name,
        action=draft.action,
        action_repo_key=draft.action_repo_key,
    )


def with_route_metadata(reply, decision, *, reply_factory):
    return reply_factory(
        text=reply.text,
        intent=reply.intent,
        steer=reply.steer,
        specialist_name=reply.specialist_name,
        provider_backed=reply.provider_backed,
        action=reply.action,
        action_repo_key=reply.action_repo_key,
        recommendation_repo_key=reply.recommendation_repo_key,
        recommendation_repo_name=reply.recommendation_repo_name,
        recommendation_reason=reply.recommendation_reason,
        recommendation_alternatives=reply.recommendation_alternatives,
        recommendation_caveat=reply.recommendation_caveat,
        hardware_summary=reply.hardware_summary,
        recommendation_fit_label=reply.recommendation_fit_label,
        trust_evidence_summary=reply.trust_evidence_summary,
        route_family=decision.route_family,
        route_confidence=round(decision.confidence, 2),
        response_contract_name=decision.response_contract.route_family,
        answer_style=decision.response_contract.style_name,
        user_alias=reply.user_alias,
        pending_offer_kind=reply.pending_offer_kind,
        pending_offer_label=reply.pending_offer_label,
        pending_offer_repo_key=reply.pending_offer_repo_key,
        pending_offer_repo_name=reply.pending_offer_repo_name,
    )


def clarification_option_labels_for_reply(reply) -> tuple[str, ...] | None:
    if reply.intent != "clarify":
        return None
    lower = reply.text.lower()
    labels: list[str] = []
    for marker in ("repo recommendation", "repo status", "system capacity", "last recommended repo", "active repo status", "run the active repo", "inspect repo requirements", "recommend a repo", "repo setup issue"):
        if marker in lower:
            labels.append(marker)
    return tuple(labels) if labels else None
