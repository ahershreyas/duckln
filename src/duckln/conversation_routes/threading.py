"""Thread and offer helpers for scoped conversation state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


def thread_id_for_reply(
    *,
    has_repo_thread: bool,
    intent: str,
    route_family: str | None,
    repo_token: str | None,
    prior_thread_id: str | None,
) -> str | None:
    if not has_repo_thread:
        return None
    if prior_thread_id and (intent.startswith("repo_") or intent in {"recommendation_followup", "slash_runtime_followup"}):
        return prior_thread_id
    token = (repo_token or "thread").lower()
    return f"{route_family or intent}:{token}:{uuid4().hex[:8]}"


def offer_id_for_reply(*, offer_kind: str | None, thread_id: str | None) -> str | None:
    if not offer_kind or not thread_id:
        return None
    return f"{offer_kind}:{uuid4().hex[:8]}"


def thread_ids_match(*, pending_offer_thread_id: str | None, active_thread_id: str | None) -> bool:
    if not pending_offer_thread_id or not active_thread_id:
        return True
    return pending_offer_thread_id == active_thread_id


def thread_expiry_for_reply(*, thread_id: str | None, now: datetime | None = None) -> str | None:
    if thread_id is None:
        return None
    anchor = now or datetime.now(timezone.utc)
    return (anchor + timedelta(minutes=45)).isoformat(timespec="seconds")


def followup_expiry_for_reply(*, offer_id: str | None, now: datetime | None = None) -> str | None:
    if offer_id is None:
        return None
    anchor = now or datetime.now(timezone.utc)
    return (anchor + timedelta(minutes=20)).isoformat(timespec="seconds")


def state_is_expired(raw_timestamp: str | None, *, now: datetime | None = None) -> bool:
    if not raw_timestamp:
        return False
    try:
        parsed = datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    anchor = now or datetime.now(timezone.utc)
    return parsed <= anchor
