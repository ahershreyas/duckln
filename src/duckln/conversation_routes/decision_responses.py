"""Decision-response helpers for clarification and bounded utility fallback."""

from __future__ import annotations

from typing import Any, Callable


def build_clarification_reply(
    *,
    decision,
    recent_replies: tuple[Any, ...],
    reply_factory: Callable[..., Any],
    pick_reply: Callable[[tuple[Any, ...], str, tuple[Any, ...]], Any],
    clarification_reply_texts: Callable[[tuple[str, ...] | list[str]], tuple[str, ...]],
) -> Any:
    labels = [option.label for option in decision.clarification_options[:3]]
    options = tuple(
        reply_factory(text, intent="clarify", specialist_name="conversation-clarify")
        for text in clarification_reply_texts(labels)
    )
    return pick_reply(options, "clarify " + " ".join(labels), recent_replies)


def build_utility_fallback_reply(
    *,
    context,
    recent_replies: tuple[Any, ...],
    reply_factory: Callable[..., Any],
    pick_reply: Callable[[tuple[Any, ...], str, tuple[Any, ...]], Any],
    utility_fallback_reply_texts: Callable[..., tuple[str, ...]],
) -> Any:
    workflow = getattr(context, "workflow_state", None)
    workflow_repo_name = str(getattr(workflow, "repo_name", "") or "").strip() or None
    repo = context.mentioned_repo or context.active_repo
    repo_name = workflow_repo_name or (repo.name if repo is not None else None)
    allow_recommendation_shortlist = repo_name is None and workflow_repo_name is None and context.pending_offer.kind == "recommend_best_fit"
    recommendation = context.durable_recommendation
    options = tuple(
        reply_factory(text, intent="generic", specialist_name="conversation-fallback")
        for text in utility_fallback_reply_texts(
            repo_name=repo_name,
            shortlist_primary=recommendation.primary_repo if allow_recommendation_shortlist else None,
            shortlist_secondary=recommendation.secondary_repo if allow_recommendation_shortlist else None,
            pending_offer_kind=context.pending_offer.kind,
        )
    )
    return pick_reply(options, "utility fallback", recent_replies)
