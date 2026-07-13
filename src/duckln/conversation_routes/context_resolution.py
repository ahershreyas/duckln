"""Conversation subject and repo-state resolution helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from state.repo_catalog import RepoCatalogRecord


def resolve_conversation_subject(
    normalized_compact: str,
    records: tuple[RepoCatalogRecord, ...],
    recent_turns,
    *,
    active_repo: RepoCatalogRecord | None,
    workflow_state,
    followup_state,
    recommendation_context: dict[str, object] | None,
    resolved_subject_factory: Callable[[RepoCatalogRecord | None, str | None], Any],
    looks_like_social_or_identity_turn: Callable[[str], bool],
    resolve_mentioned_repo: Callable[[str, tuple[RepoCatalogRecord, ...], Any], RepoCatalogRecord | None],
    parse_structured_intent: Callable[[str], Any],
    resolve_repo_name_hint: Callable[..., str | None],
    acceptance_phrase: Callable[[str], str | None],
    thread_ids_match: Callable[..., bool],
    repo_from_followup_state: Callable[[tuple[RepoCatalogRecord, ...], Any], RepoCatalogRecord | None],
    repo_from_workflow_state: Callable[[tuple[RepoCatalogRecord, ...], Any], RepoCatalogRecord | None],
    looks_like_repo_pronoun_followup: Callable[[str], bool],
    repo_from_recommendation_context: Callable[[tuple[RepoCatalogRecord, ...], dict[str, object] | None], RepoCatalogRecord | None],
) -> Any:
    if looks_like_social_or_identity_turn(normalized_compact):
        return resolved_subject_factory(None, None)
    structured = parse_structured_intent(normalized_compact)
    explicit = resolve_mentioned_repo(normalized_compact, records, recent_turns)
    if explicit is not None:
        return resolved_subject_factory(explicit, "explicit")
    workflow_repo = repo_from_workflow_state(records, workflow_state)
    if workflow_repo is not None:
        workflow_hint = resolve_repo_name_hint(
            repo_name_hint=structured.repo_name_hint,
            path_hint=structured.path_hint,
            candidate_names=(workflow_repo.name,),
            active_repo_name=workflow_repo.name,
        )
        if workflow_hint and workflow_hint.lower() == workflow_repo.name.lower():
            return resolved_subject_factory(workflow_repo, "workflow-fuzzy")
    if active_repo is not None:
        active_hint = resolve_repo_name_hint(
            repo_name_hint=structured.repo_name_hint,
            path_hint=structured.path_hint,
            candidate_names=(active_repo.name,),
            active_repo_name=active_repo.name,
        )
        if active_hint and active_hint.lower() == active_repo.name.lower():
            return resolved_subject_factory(active_repo, "active-fuzzy")
    if acceptance_phrase(normalized_compact):
        if thread_ids_match(
            pending_offer_thread_id=followup_state.pending_offer_thread_id,
            active_thread_id=followup_state.active_thread_id,
        ):
            pending = repo_from_followup_state(records, followup_state)
            if pending is not None:
                return resolved_subject_factory(pending, "pending")
    if looks_like_repo_pronoun_followup(normalized_compact):
        if workflow_repo is not None:
            return resolved_subject_factory(workflow_repo, "workflow")
        pending = repo_from_followup_state(records, followup_state)
        if pending is not None:
            return resolved_subject_factory(pending, "pending")
        if active_repo is not None:
            return resolved_subject_factory(active_repo, "active")
        recommended = repo_from_recommendation_context(records, recommendation_context)
        if recommended is not None:
            return resolved_subject_factory(recommended, "recommendation")
    workflow_action = structured.action in {"run", "access", "remove", "setup", "verify", "inspect"}
    if workflow_repo is not None and workflow_action and structured.subject == "repo":
        return resolved_subject_factory(workflow_repo, "workflow")
    if (
        active_repo is not None
        and structured.subject == "repo"
        and structured.repo_name_hint is None
        and structured.scope in {"active", "specific_status", "path", "source"}
        and not any(token in normalized_compact for token in ("all repos", "repos", "repositories", "tracked repos"))
    ):
        return resolved_subject_factory(active_repo, "active")
    recommended = repo_from_recommendation_context(records, recommendation_context)
    if recommended is not None and any(token in normalized_compact for token in ("that repo", "that one", "recommended")):
        return resolved_subject_factory(recommended, "recommendation")
    return resolved_subject_factory(None, None)


def resolve_active_repo(
    config_dir: Path | None,
    records: tuple[RepoCatalogRecord, ...],
    *,
    initialize_state_store: Callable[[Path], Any],
) -> RepoCatalogRecord | None:
    if config_dir is None:
        return None
    latest = initialize_state_store(config_dir).get_latest_repo_state()
    if latest is None:
        return None
    latest_key = (latest.repo_url or latest.repo_key or "").lower()
    for record in records:
        if record.repo_url.lower() == latest_key or record.name.lower() == latest_key:
            return record
    repo_name = Path(latest.repo_path).name if latest.repo_path else latest_key.rsplit("/", 1)[-1]
    if not repo_name:
        return None
    return RepoCatalogRecord(
        name=repo_name,
        repo_url=latest.repo_url or latest.repo_key,
        stars=0,
        description=latest.summary,
        category="Unknown",
        framework="Unknown",
        last_updated="",
    )


def repo_status_record(
    config_dir: Path | None,
    repo: RepoCatalogRecord | None,
    *,
    initialize_state_store: Callable[[Path], Any],
    repo_name_from_state: Callable[[Any], str],
) -> dict[str, object] | None:
    if config_dir is None or repo is None:
        return None
    rows = initialize_state_store(config_dir).list_repo_states()
    for row in rows:
        repo_name = str(row.metadata.get("repo_name") or repo_name_from_state(row)).strip()
        repo_url = str(row.repo_url or row.repo_key or "").strip()
        if repo_name.lower() == repo.name.lower() or (repo.repo_url and repo.repo_url.lower() == repo_url.lower()):
            return {
                "repo_key": row.repo_key,
                "name": repo_name,
                "status": row.status,
                "install_location": row.metadata.get("install_location") or row.repo_path,
                "access_hint": row.metadata.get("access_hint"),
                "run_command": row.metadata.get("run_command"),
            }
    return None
