"""Repo recommendation reply construction extracted from the conversation specialist."""

from __future__ import annotations

from typing import Any, Callable


def build_repo_recommendation_reply(
    *,
    message: str,
    intent: str,
    context: Any,
    config_dir,
    system_probe,
    recent_replies: tuple[Any, ...],
    specialist_name: str,
    reply_factory: Callable[..., Any],
    pick_reply: Callable[[tuple[Any, ...], str, tuple[Any, ...]], Any],
    score_recommendation_candidates: Callable[..., tuple[Any, ...]],
    recommendation_system_hint: Callable[[Any], str],
    find_recommendation_candidate: Callable[..., Any | None],
    requested_repo_comparison_candidates: Callable[..., tuple[Any, ...]],
    recommendation_options: Callable[..., tuple[str, ...]],
    rationale_options: Callable[..., tuple[str, ...]],
    compare_options: Callable[..., tuple[str, ...]],
    alternative_options: Callable[..., tuple[str, ...]],
    join_blocks: Callable[..., str],
    bullet_block: Callable[[tuple[str, ...]], str],
    next_step_phrase: Callable[..., str],
    short_reason_text: Callable[[str], str],
) -> Any:
    records = context.records
    normalized_compact = " ".join(message.strip().lower().split())
    recommendations = score_recommendation_candidates(
        records,
        system_probe,
        config_dir=config_dir,
        normalized_compact=normalized_compact,
    )
    if not recommendations:
        return reply_factory(
            "I can recommend one once I can see the cached repo catalog. Run /repos and I’ll help you choose the smallest fit for this machine.",
            intent=intent,
            steer="/repos",
            specialist_name=specialist_name,
        )
    system_hint = recommendation_system_hint(system_probe)
    top = recommendations[:3]
    repeated_recommendation = any(reply.intent.startswith("repo_recommendation") for reply in recent_replies[-2:])
    recommendation_context = context.recommendation_memory or {}
    last_repo_name = str(recommendation_context.get("repo_name") or "").strip()

    if intent == "repo_alternatives":
        if len(recommendations) < 2:
            return reply_factory(
                "I only have one realistic option in view right now. If you want, I can still walk through its requirements or explain why the heavier repos fell away.",
                intent="repo_alternatives",
                specialist_name=specialist_name,
            )
        primary = find_recommendation_candidate(recommendations, last_repo_name) or recommendations[0]
        stored_alternatives = tuple(name for name in context.followup_state.last_recommendation_alternatives if name)
        alternative = None
        for alternative_name in stored_alternatives:
            alternative = find_recommendation_candidate(recommendations, alternative_name)
            if alternative is not None and alternative.repo.name.lower() != primary.repo.name.lower():
                break
        if alternative is None:
            alternative = next(
                (candidate for candidate in recommendations if candidate.repo.name.lower() != primary.repo.name.lower()),
                recommendations[1],
            )
        options = tuple(
            reply_factory(
                text,
                intent="repo_alternatives",
                steer="/repos",
                specialist_name=specialist_name,
                recommendation_repo_key=alternative.repo.repo_url or alternative.repo.name,
                recommendation_repo_name=alternative.repo.name,
                recommendation_reason=alternative.fit_reason,
                recommendation_alternatives=tuple(candidate.repo.name for candidate in recommendations[2:4]),
                recommendation_caveat=alternative.caveat,
                hardware_summary=alternative.hardware_summary,
                recommendation_fit_label=alternative.fit_label,
                trust_evidence_summary=alternative.trust_summary,
            )
            for text in alternative_options(
                alternative,
                system_hint=system_hint,
                better_name=primary.repo.name,
                thread_state=context.thread_state,
                recent_replies=recent_replies,
            )
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent in {"repo_recommendation_rationale", "repo_recommendation_followup"}:
        chosen = find_recommendation_candidate(top, last_repo_name) or top[0]
        referenced = context.mentioned_repo.name if context.mentioned_repo is not None else None
        if "why not " in normalized_compact and referenced and referenced.lower() != chosen.repo.name.lower():
            alt = find_recommendation_candidate(recommendations, referenced)
            if alt is not None:
                options = tuple(
                    reply_factory(
                        text,
                        intent=intent,
                        steer="/repos",
                        specialist_name=specialist_name,
                        recommendation_repo_key=chosen.repo.repo_url or chosen.repo.name,
                        recommendation_repo_name=chosen.repo.name,
                        recommendation_reason=chosen.fit_reason,
                        recommendation_alternatives=tuple(candidate.repo.name for candidate in top[1:3]),
                        recommendation_caveat=chosen.caveat,
                        recommendation_fit_label=chosen.fit_label,
                        trust_evidence_summary=chosen.trust_summary,
                    )
                    for text in rationale_options(
                        chosen,
                        alternative=alt,
                        thread_state=context.thread_state,
                        recent_replies=recent_replies,
                    )
                )
                return pick_reply(options, normalized_compact, recent_replies)
        options = tuple(
            reply_factory(
                text,
                intent=intent,
                steer="/repos",
                specialist_name=specialist_name,
                recommendation_repo_key=chosen.repo.repo_url or chosen.repo.name,
                recommendation_repo_name=chosen.repo.name,
                recommendation_reason=chosen.fit_reason,
                recommendation_alternatives=tuple(candidate.repo.name for candidate in top[1:3]),
                recommendation_caveat=chosen.caveat,
                hardware_summary=chosen.hardware_summary,
                recommendation_fit_label=chosen.fit_label,
                trust_evidence_summary=chosen.trust_summary,
            )
            for text in rationale_options(
                chosen,
                thread_state=context.thread_state,
                recent_replies=recent_replies,
            )
        )
        return pick_reply(options, normalized_compact, recent_replies)

    wants_single = intent == "repo_recommendation_single" or repeated_recommendation
    if wants_single:
        best = top[0]
        options = tuple(
            reply_factory(
                text,
                intent=intent,
                steer="/repos",
                specialist_name=specialist_name,
                recommendation_repo_key=best.repo.repo_url or best.repo.name,
                recommendation_repo_name=best.repo.name,
                recommendation_reason=best.fit_reason,
                recommendation_alternatives=tuple(candidate.repo.name for candidate in top[1:3]),
                recommendation_caveat=best.caveat,
                hardware_summary=best.hardware_summary,
                recommendation_fit_label=best.fit_label,
                trust_evidence_summary=best.trust_summary,
            )
            for text in recommendation_options(
                best,
                system_hint=system_hint,
                thread_state=context.thread_state,
                recent_replies=recent_replies,
            )
        )
        return pick_reply(options, normalized_compact, recent_replies)

    requested_compare = requested_repo_comparison_candidates(normalized_compact, recommendations)
    if len(requested_compare) >= 2:
        left, right = requested_compare[0], requested_compare[1]
        chosen = left if (left.score, -left.repo.stars) <= (right.score, -right.repo.stars) else right
        other = right if chosen is left else left
        options = tuple(
            reply_factory(
                text,
                intent=intent,
                steer="/repos",
                specialist_name=specialist_name,
                recommendation_repo_key=chosen.repo.repo_url or chosen.repo.name,
                recommendation_repo_name=chosen.repo.name,
                recommendation_reason=chosen.fit_reason,
                recommendation_alternatives=(other.repo.name,),
                recommendation_caveat=chosen.caveat,
                hardware_summary=chosen.hardware_summary,
                recommendation_fit_label=chosen.fit_label,
                trust_evidence_summary=chosen.trust_summary,
            )
            for text in compare_options(
                chosen,
                other,
                system_hint=system_hint,
                thread_state=context.thread_state,
                recent_replies=recent_replies,
            )
        )
        return pick_reply(options, normalized_compact, recent_replies)

    best = top[0]
    shortlist_lines = tuple(f"{candidate.repo.name}: {short_reason_text(candidate.fit_reason)}" for candidate in top[:3])
    options = (
        reply_factory(
            join_blocks(
                f"On this {system_hint}, these are the strongest starting points I see:",
                bullet_block(shortlist_lines),
                f"If you want one clear starting point, I'd still begin with {best.repo.name}.",
                next_step_phrase(recent_replies=recent_replies, thread_state=context.thread_state, inspect_repo_name=best.repo.name),
            ),
            intent=intent,
            steer="/repos",
            specialist_name=specialist_name,
            recommendation_repo_key=best.repo.repo_url or best.repo.name,
            recommendation_repo_name=best.repo.name,
            recommendation_reason=best.fit_reason,
            recommendation_alternatives=tuple(candidate.repo.name for candidate in top[1:3]),
            recommendation_caveat=best.caveat,
            hardware_summary=best.hardware_summary,
            recommendation_fit_label=best.fit_label,
            trust_evidence_summary=best.trust_summary,
        ),
        reply_factory(
            join_blocks(
                f"I'd keep the shortlist small on this {system_hint}:",
                bullet_block(shortlist_lines),
                f"If you want one repo instead of options, I'd still lean toward {best.repo.name}.",
                next_step_phrase(recent_replies=recent_replies, thread_state=context.thread_state, inspect_repo_name=best.repo.name),
            ),
            intent=intent,
            steer="/repos",
            specialist_name=specialist_name,
            recommendation_repo_key=best.repo.repo_url or best.repo.name,
            recommendation_repo_name=best.repo.name,
            recommendation_reason=best.fit_reason,
            recommendation_alternatives=tuple(candidate.repo.name for candidate in top[1:3]),
            recommendation_caveat=best.caveat,
            hardware_summary=best.hardware_summary,
            recommendation_fit_label=best.fit_label,
            trust_evidence_summary=best.trust_summary,
        ),
    )
    return pick_reply(options, normalized_compact, recent_replies)
