"""Helpers for repairing or restating a confusing prior reply."""

from __future__ import annotations


def repair_reply_options(
    *,
    last_route_family: str | None,
    repo_name: str | None,
) -> tuple[str, ...]:
    if last_route_family == "repo_capability_coverage":
        return (
            "I was talking about the repos Duckln can help with, then narrowing that list to what fits this machine.",
            "I meant Duckln can work across the repo catalog, and then pick the options that are realistic on this machine.",
        )
    if last_route_family in {
        "repo_recommendation_single",
        "repo_recommendation_compare",
        "repo_recommendation_rationale",
        "repo_alternatives",
        "recommendation_followup",
    }:
        target = repo_name or "the current repo thread"
        return (
            f"I was talking about repo recommendations for this machine, not a hidden background task. Right now that answer was centered on {target}.",
            f"I meant Duckln was still answering the repo recommendation question for this machine. If that is not what you want, I can reset and answer from scratch.",
        )
    if last_route_family == "repo_active_explanation" and repo_name:
        return (
            f"I meant Duckln is tracking {repo_name} as the current repo for follow-up phrases like 'run it' or 'what next'.",
            f"I was only saying {repo_name} is the repo Duckln would assume for short follow-up questions.",
        )
    return (
        "I was referring to the last topic Duckln was tracking. If that was unclear, I can restate it in plain English or start fresh.",
        "I meant the last active topic in this chat, not a hidden action. If you want, I can reset and answer the question again more plainly.",
    )


def restatement_reply_options(
    *,
    last_route_family: str | None,
    repo_name: str | None,
    recommendation_primary: str | None,
) -> tuple[str, ...]:
    """Return simpler restatements of the last answer without continuing the old thread."""

    if last_route_family == "repo_capability_coverage":
        return (
            "In simple terms: Duckln can help with several repos, then narrow them down to the ones that fit this machine.",
            "Short version: I was listing the repos Duckln can help with, then trimming that list to realistic fits for this machine.",
        )
    if last_route_family in {
        "repo_recommendation_single",
        "repo_recommendation_compare",
        "repo_recommendation_rationale",
        "repo_alternatives",
        "recommendation_followup",
    }:
        target = recommendation_primary or repo_name or "the current recommendation"
        return (
            f"Plain version: I was still talking about repo recommendations, and {target} was the current pick.",
            f"Short version: Duckln was still in recommendation mode, with {target} as the current answer.",
        )
    if last_route_family == "repo_active_explanation" and repo_name:
        return (
            f"Plain version: active repo just means Duckln is treating {repo_name} as the current follow-up repo.",
            f"Short version: {repo_name} is the repo Duckln assumes when you say something short like 'run it'.",
        )
    return (
        "Plain version: I was referring to the last topic Duckln had active in this chat.",
        "Short version: I was still answering from the current tracked topic.",
    )
