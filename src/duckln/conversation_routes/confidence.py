"""Confidence and tie-breaking helpers for pre-generation routing."""

from __future__ import annotations

from dataclasses import dataclass

from duckln.conversation_policy import RouteCandidate


_BASE_EXPLICIT_FAMILIES = {
    "small_talk",
    "conversation_repair",
    "conversation_restate",
    "repo_active_explanation",
    "next_step_guidance",
    "user_identity_meta",
    "user_alias",
    "capability",
    "repo_overview",
    "repo_status",
    "repo_verify",
    "repo_inventory",
    "repo_live_sessions",
    "repo_path",
    "repo_run",
    "repo_restart",
    "repo_stop",
    "repo_logs",
    "repo_remove",
    "system_verify",
}


@dataclass(frozen=True)
class ConfidenceSnapshot:
    """Ordered routing snapshot used before the final route decision."""

    ordered: tuple[RouteCandidate, ...]
    top: RouteCandidate | None
    second: RouteCandidate | None
    margin: float
    top_score: float


def build_confidence_snapshot(candidates: list[RouteCandidate]) -> ConfidenceSnapshot:
    ordered = tuple(sorted(candidates, key=lambda item: item.score, reverse=True))
    top = ordered[0] if ordered else None
    second = ordered[1] if len(ordered) > 1 else None
    top_score = top.score if top is not None else 0.0
    second_score = second.score if second is not None else 0.0
    return ConfidenceSnapshot(
        ordered=ordered,
        top=top,
        second=second,
        margin=max(0.0, top_score - second_score),
        top_score=top_score,
    )


def confidence_band(score: float) -> str:
    if score >= 0.9:
        return "high"
    if score >= 0.78:
        return "medium"
    if score >= 0.55:
        return "low"
    return "uncertain"


def explicit_base_candidate(
    snapshot: ConfidenceSnapshot,
    *,
    base_intent: str,
    base_route_family: str,
) -> RouteCandidate | None:
    if base_intent == "generic" or base_route_family not in _BASE_EXPLICIT_FAMILIES:
        return None
    candidate = next(
        (
            item
            for item in snapshot.ordered
            if item.route_family == base_route_family and item.intent == base_intent
        ),
        None,
    )
    if candidate is None or candidate.score < 0.88:
        return None
    return candidate


def should_answer_without_clarify(
    snapshot: ConfidenceSnapshot,
    *,
    followup_intent: str | None,
    subject_name: str | None,
) -> bool:
    top = snapshot.top
    if top is None:
        return False
    if top.score < 0.78:
        return False
    if snapshot.margin >= 0.12:
        return True
    if (
        top.route_family.startswith("repo_recommendation")
        and snapshot.second is not None
        and snapshot.second.route_family == "repo_capability_coverage"
    ):
        return True
    if followup_intent is not None and top.intent == followup_intent:
        return True
    if top.route_family == "workflow_action" and subject_name is not None:
        return True
    return False


def should_clarify_route(
    snapshot: ConfidenceSnapshot,
    *,
    clarification_option_count: int,
    subject_name: str | None,
    top_route_family: str | None,
    looks_like_repo_pronoun_followup: bool,
) -> bool:
    if clarification_option_count <= 0:
        return False
    if top_route_family is None:
        return True
    if confidence_band(snapshot.top_score) in {"low", "uncertain"}:
        return True
    if snapshot.margin < 0.12:
        return True
    if looks_like_repo_pronoun_followup and subject_name is None:
        return True
    if top_route_family == "workflow_action" and subject_name is None:
        return True
    if top_route_family == "utility_fallback":
        return True
    return False
