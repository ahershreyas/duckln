"""Supervisor follow-up state helpers extracted from conversation_agent."""

from __future__ import annotations

from dataclasses import replace
from typing import Any


def prune_expired_offer_state(values: dict[str, object]) -> dict[str, object]:
    pruned = dict(values)
    for key in (
        "pending_repo_key",
        "pending_repo_name",
        "pending_next_action",
        "pending_offer_kind",
        "pending_offer_label",
        "pending_offer_repo_key",
        "pending_offer_repo_name",
        "pending_offer_id",
        "pending_offer_thread_id",
        "pending_offer_expires_at",
        "pending_question_type",
        "pending_subject_type",
        "pending_clarification_options",
        "last_next_step_offered",
    ):
        pruned.pop(key, None)
    return pruned


def prune_expired_thread_state(values: dict[str, object]) -> dict[str, object]:
    pruned = prune_expired_offer_state(values)
    for key in (
        "active_topic",
        "last_route_family",
        "last_render_fingerprint",
        "last_discussed_repo_key",
        "last_discussed_repo_name",
        "shortlist_repo_names",
        "shortlist_primary_repo",
        "shortlist_secondary_repo",
        "active_thread_id",
        "active_thread_expires_at",
    ):
        pruned.pop(key, None)
    return pruned


def prune_followup_state_for_new_session(values: dict[str, object]) -> dict[str, object]:
    pruned = prune_expired_thread_state(values)
    pruned.pop("active_session_id", None)
    return pruned


def read_supervisor_followup_state(
    config_dir,
    *,
    followup_state_type,
    read_followup_state,
    read_config_snapshot,
    state_is_expired,
):
    if config_dir is None:
        return followup_state_type()
    values = read_followup_state(config_dir)
    session_snapshot = read_config_snapshot(config_dir, prefix="session.")
    active_session_id = str(session_snapshot.get("session.current_id") or "").strip() or None
    if values.get("active_session_id") and active_session_id and values.get("active_session_id") != active_session_id:
        values = prune_followup_state_for_new_session(values)
    if state_is_expired(str(values.get("pending_offer_expires_at") or "") or None):
        values = prune_expired_offer_state(values)
    if state_is_expired(str(values.get("active_thread_expires_at") or "") or None):
        values = prune_expired_thread_state(values)
    normalized: dict[str, object | None] = {}
    for field_name in followup_state_type.__dataclass_fields__:
        value = values.get(field_name)
        if field_name in {"pending_clarification_options", "last_recommendation_alternatives", "shortlist_repo_names"} and isinstance(value, list):
            value = tuple(str(item) for item in value)
        normalized[field_name] = value
    return followup_state_type(**normalized)


def reconcile_fit_with_followup_state(reply, *, followup_state):
    """Prevent contradictory fit labels for the same ongoing recommendation thread."""

    prior_fit = (followup_state.last_recommendation_fit or "").strip().lower()
    current_fit = (reply.recommendation_fit_label or "").strip().lower()
    if not prior_fit or not current_fit:
        return reply
    allowed = {"comfortable", "workable_but_tight", "not_recommended"}
    if prior_fit not in allowed or current_fit not in allowed or prior_fit == current_fit:
        return reply
    revised_text = (
        f"{reply.text} I’m keeping the prior fit judgment as {prior_fit.replace('_', ' ')} for consistency across chat and /repos."
    )
    return replace(reply, text=revised_text, recommendation_fit_label=prior_fit)


def verified_fact_summary(config_dir, repo, *, repo_status_record) -> str | None:
    record = repo_status_record(config_dir, repo)
    if record is None:
        return None
    status = str(record.get("status") or "").strip()
    install = str(record.get("install_location") or "").strip()
    if install:
        return f"Duckln verified or tracked {repo.name if repo is not None else 'the repo'} as {status} at {install}."
    return f"Duckln tracked {repo.name if repo is not None else 'the repo'} as {status}."
