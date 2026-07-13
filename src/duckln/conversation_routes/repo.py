"""Repo-scoped helpers for conversation routing."""

from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path
import re

from duckln.conversation_routes.entity_resolution import normalize_repo_token, path_matches_repo_name, resolve_repo_name_hint
from duckln.conversation_routes.intent_schema import parse_structured_intent
from state.repo_catalog import RepoCatalogRecord


def _phrase_present(text: str, phrase: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _contains_activeish_token(text: str) -> bool:
    if re.search(r"\ba+ctive\b", text) is not None:
        return True
    tokens = re.findall(r"[a-z]+", text)
    for token in tokens:
        if token == "active":
            return True
        if len(token) >= 5 and token.startswith("a") and "ctive" in token:
            return True
    return False


def looks_like_repo_lifecycle_inventory_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    structured = parse_structured_intent(cleaned)
    if structured.subject == "repo" and structured.scope == "inventory":
        return True
    if (
        any(_phrase_present(cleaned, marker) for marker in ("repos", "repositories"))
        and any(_phrase_present(cleaned, marker) for marker in ("active", "running"))
        and any(_phrase_present(cleaned, marker) for marker in ("show", "list", "tell me", "which", "what", "can you show me", "can you tell me", "do we have", "do i have"))
        and not any(_phrase_present(cleaned, marker) for marker in ("installed", "set up", "setup", "ready", "prepared", "tracked"))
    ):
        return False
    inventory_scope_markers = (
        "all repos",
        "show me all repos",
        "show me repos",
        "repos that we have",
        "repos we have",
        "a repo that we have setup",
        "a repo we have setup",
        "a repo that is setup",
        "a repo that we have set up",
        "which repos do i have",
        "what repos do i have",
        "do i have any repos",
        "do we have any repos",
        "can you tell me active repos",
        "can you tell me running repos",
        "active repos",
        "running repos",
        "repos in duckln",
        "repos in system",
        "already have an repo",
        "already have a repo",
    )
    lifecycle_markers = (
        "installed",
        "active",
        "running",
        "set up",
        "setup",
        "ready",
        "in process",
        "working previously",
        "worked previously",
        "worked on previously",
    )
    if any(_phrase_present(cleaned, marker) for marker in inventory_scope_markers) and any(
        _phrase_present(cleaned, marker) for marker in lifecycle_markers
    ):
        return True
    return False


def looks_like_repo_lifecycle_active_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    structured = parse_structured_intent(cleaned)
    if structured.subject == "repo" and structured.scope == "active":
        return True
    explicit_markers = (
        "which repo do you currently have active",
        "repo which we worked previously",
    )
    if any(_phrase_present(cleaned, marker) for marker in explicit_markers):
        return True
    if (
        any(_phrase_present(cleaned, marker) for marker in ("repos", "repositories"))
        and any(_phrase_present(cleaned, marker) for marker in ("active", "running"))
        and any(_phrase_present(cleaned, marker) for marker in ("show", "list", "tell me", "which", "what", "can you show me", "can you tell me", "do we have", "do i have"))
        and not any(_phrase_present(cleaned, marker) for marker in ("installed", "set up", "setup", "ready", "prepared", "tracked"))
    ):
        return False
    if (_phrase_present(cleaned, "worked previously") or _phrase_present(cleaned, "worked on previously")) and _phrase_present(cleaned, "repo"):
        return True
    if _phrase_present(cleaned, "repo") and _contains_activeish_token(cleaned):
        if any(
            _phrase_present(cleaned, marker)
            for marker in ("is there", "what", "which", "do i have", "do we have", "currently", "tracking", "worked previously", "worked on previously")
        ):
            return True
    return False


def looks_like_repo_path_inventory_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    structured = parse_structured_intent(cleaned)
    if structured.subject == "repo" and structured.scope == "path_inventory":
        return True
    path_markers = (
        "location of all repos",
        "location of all the repos",
        "locations of all repos",
        "locations of all the repos",
        "show me all repo paths",
        "show me all repos path",
        "show me the locations of all repos",
        "show me the locations of all the repos",
        "repo paths",
        "repos path",
        "paths of all repos",
        "where are all repos installed",
        "where did you install all repos",
        "location of all tracked repos",
    )
    plural_markers = ("all repos", "repos", "repositories", "tracked repos", "setup repos", "installed repos")
    path_terms = ("path", "paths", "location", "locations", "where")
    return (
        any(_phrase_present(cleaned, marker) for marker in path_markers)
        or (
            any(_phrase_present(cleaned, marker) for marker in plural_markers)
            and any(_phrase_present(cleaned, marker) for marker in path_terms)
            and not parse_structured_intent(cleaned).repo_name_hint
        )
    )


def looks_like_repo_source_inventory_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    structured = parse_structured_intent(cleaned)
    if structured.subject == "repo" and structured.scope == "source_inventory":
        return True
    source_markers = (
        "github paths of all repos",
        "github paths for all repos",
        "github links of all repos",
        "github links for all repos",
        "source links of all repos",
        "source urls of all repos",
        "repo urls of all repos",
        "show me all repo urls",
        "show me all repo links",
        "show me all github links",
    )
    plural_markers = ("all repos", "repos", "repositories", "tracked repos")
    source_terms = ("github", "source", "repo url", "repo link", "repository url", "repository link", "remote url", "origin url")
    return (
        any(_phrase_present(cleaned, marker) for marker in source_markers)
        or (
            any(_phrase_present(cleaned, marker) for marker in plural_markers)
            and any(_phrase_present(cleaned, marker) for marker in source_terms)
            and not parse_structured_intent(cleaned).repo_name_hint
        )
    )


def looks_like_repo_lifecycle_specific_status_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    structured = parse_structured_intent(cleaned)
    if structured.subject == "repo" and structured.scope == "specific_status":
        return True
    lifecycle_markers = (
        "installed",
        "active",
        "running",
        "ready",
        "set up",
        "setup",
    )
    question_markers = (
        "do we have",
        "do wehave",
        "do i have",
        "is",
        "already have",
        "is there",
        "tell me if",
        "check if",
        "whether",
    )
    if any(_phrase_present(cleaned, phrase) for phrase in ("help me set up", "set up", "setup", "install", "run")):
        return False
    return structured.repo_name_hint is not None and (
        any(_phrase_present(cleaned, marker) for marker in lifecycle_markers)
        or any(_phrase_present(cleaned, marker) for marker in question_markers)
    )


def looks_like_repo_lifecycle_previous_work_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if _phrase_present(cleaned, "worked previously") or _phrase_present(cleaned, "worked on previously"):
        return True
    if _phrase_present(cleaned, "already have") and _phrase_present(cleaned, "repo") and _phrase_present(cleaned, "previously"):
        return True
    return False


def looks_like_repo_lifecycle_turn(normalized_compact: str) -> bool:
    return any(
        (
            looks_like_repo_lifecycle_inventory_query(normalized_compact),
            looks_like_repo_lifecycle_active_query(normalized_compact),
            looks_like_repo_lifecycle_specific_status_query(normalized_compact),
            looks_like_repo_lifecycle_previous_work_query(normalized_compact),
        )
    )


def looks_like_repo_live_sessions_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    session_markers = (
        "live repo sessions",
        "live repos",
        "running repo sessions",
        "repo sessions",
        "live sessions",
        "actually running repos",
        "actually running right now",
        "repos actually running",
        "running right now",
        "currently running sessions",
    )
    repo_markers = ("repo", "repos", "repository", "repositories")
    session_terms = ("live", "running", "session", "sessions")
    question_markers = ("show", "list", "which", "what", "tell me", "do we have", "do i have", "can you show me", "can you tell me")
    return any(_phrase_present(cleaned, marker) for marker in session_markers) or (
        any(_phrase_present(cleaned, marker) for marker in repo_markers)
        and any(_phrase_present(cleaned, marker) for marker in session_terms)
        and any(_phrase_present(cleaned, marker) for marker in question_markers)
        and any(_phrase_present(cleaned, marker) for marker in ("actually", "live", "session", "sessions"))
    )


def looks_like_repo_live_sessions_local_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    return looks_like_repo_live_sessions_query(cleaned) and _phrase_present(cleaned, "local")


def looks_like_repo_live_sessions_vm_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    return looks_like_repo_live_sessions_query(cleaned) and any(_phrase_present(cleaned, marker) for marker in ("vm", "virtual machine"))


def looks_like_repo_live_sessions_cloud_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    return looks_like_repo_live_sessions_query(cleaned) and any(_phrase_present(cleaned, marker) for marker in ("cloud", "aws", "gcp"))


def looks_like_repo_live_sessions_docker_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    return looks_like_repo_live_sessions_query(cleaned) and any(_phrase_present(cleaned, marker) for marker in ("docker", "container"))


def looks_like_repo_source_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    structured = parse_structured_intent(cleaned)
    if structured.subject == "repo" and structured.scope == "source":
        return True
    source_markers = (
        "github path",
        "github repo",
        "github url",
        "github link",
        "repo url",
        "repository url",
        "repo link",
        "repository link",
        "source repo",
        "source url",
        "source link",
        "origin url",
        "remote url",
        "github location",
    )
    repo_reference_markers = ("repo", "repository", " it ", " its ", "that repo", "this repo")
    return (
        any(_phrase_present(cleaned, marker) for marker in source_markers)
        and (
            structured.repo_name_hint is not None
            or any(marker in f" {cleaned} " for marker in repo_reference_markers)
            or structured.subject == "repo"
        )
    )


def repo_name_from_state(row) -> str:
    if getattr(row, "repo_path", None):
        return Path(row.repo_path).name
    repo_url = getattr(row, "repo_url", None) or getattr(row, "repo_key", "")
    if repo_url:
        return str(repo_url).rstrip("/").rsplit("/", 1)[-1]
    return "repo"


def repo_alias_map(records: tuple[RepoCatalogRecord, ...]) -> dict[str, RepoCatalogRecord]:
    alias_map: dict[str, RepoCatalogRecord] = {}
    for record in records:
        aliases = {
            record.name.lower(),
            record.name.lower().replace("-", " "),
            record.name.lower().replace(".", " "),
        }
        if record.name.lower() == "open-webui":
            aliases.add("open webui")
        for alias in aliases:
            alias_map[alias] = record
    fallback_names = {
        "whisper": RepoCatalogRecord("whisper", "", 0, "Speech recognition.", "Audio", "Python", ""),
        "speechbrain": RepoCatalogRecord("speechbrain", "", 0, "Speech toolkit.", "Audio", "Python", ""),
        "open webui": RepoCatalogRecord("open-webui", "", 0, "Local model UI.", "LLM", "Python", ""),
    }
    for alias, record in fallback_names.items():
        alias_map.setdefault(alias, record)
    return alias_map


def resolve_mentioned_repo(
    normalized_compact: str,
    records: tuple[RepoCatalogRecord, ...],
    recent_turns,
) -> RepoCatalogRecord | None:
    structured = parse_structured_intent(normalized_compact)
    search_space = [normalized_compact]
    search_space.extend(" ".join(turn.content.strip().lower().split()) for turn in recent_turns[-4:])
    aliases = repo_alias_map(records)
    candidate_names = tuple(record.name for record in records)
    resolved_hint = resolve_repo_name_hint(
        repo_name_hint=structured.repo_name_hint,
        path_hint=structured.path_hint,
        candidate_names=candidate_names,
    )
    if resolved_hint is not None:
        normalized_hint = normalize_repo_token(resolved_hint)
        for alias, record in aliases.items():
            if normalize_repo_token(alias) == normalized_hint:
                return record
    for text in search_space:
        structured_text = parse_structured_intent(text)
        resolved_hint = resolve_repo_name_hint(
            repo_name_hint=structured_text.repo_name_hint,
            path_hint=structured_text.path_hint,
            candidate_names=candidate_names,
        )
        if resolved_hint is not None:
            normalized_hint = normalize_repo_token(resolved_hint)
            for alias, record in aliases.items():
                if normalize_repo_token(alias) == normalized_hint:
                    return record
        for alias, record in aliases.items():
            if alias in text:
                return record
        fuzzy = get_close_matches(
            normalize_repo_token(text),
            tuple(normalize_repo_token(record.name) for record in records),
            n=1,
            cutoff=0.84,
        )
        if fuzzy:
            wanted = fuzzy[0]
            for record in records:
                if normalize_repo_token(record.name) == wanted:
                    return record
    return None


def repo_from_followup_state(
    records: tuple[RepoCatalogRecord, ...],
    followup_state,
) -> RepoCatalogRecord | None:
    wanted_key = (
        getattr(followup_state, "pending_offer_repo_key", None)
        or getattr(followup_state, "pending_repo_key", None)
        or getattr(followup_state, "last_discussed_repo_key", None)
        or ""
    ).lower()
    wanted_name = (
        getattr(followup_state, "pending_offer_repo_name", None)
        or getattr(followup_state, "pending_repo_name", None)
        or getattr(followup_state, "last_discussed_repo_name", None)
        or ""
    ).lower()
    for record in records:
        if wanted_name and record.name.lower() == wanted_name:
            return record
        if wanted_key and (record.name.lower() == wanted_key or record.repo_url.lower() == wanted_key):
            return record
    if wanted_name:
        return RepoCatalogRecord(wanted_name, wanted_key, 0, "", "Unknown", "Unknown", "")
    return None


def repo_from_recommendation_context(
    records: tuple[RepoCatalogRecord, ...],
    recommendation_context: dict[str, object] | None,
) -> RepoCatalogRecord | None:
    if recommendation_context is None:
        return None
    wanted_key = str(recommendation_context.get("repo_key") or "").strip().lower()
    wanted_name = str(recommendation_context.get("repo_name") or "").strip().lower()
    for record in records:
        if wanted_name and record.name.lower() == wanted_name:
            return record
        if wanted_key and (record.name.lower() == wanted_key or record.repo_url.lower() == wanted_key):
            return record
    if wanted_name:
        return RepoCatalogRecord(wanted_name, wanted_key, 0, "", "Unknown", "Unknown", "")
    return None


def repo_from_workflow_state(
    records: tuple[RepoCatalogRecord, ...],
    workflow_state,
) -> RepoCatalogRecord | None:
    if workflow_state is None:
        return None
    wanted_key = str(getattr(workflow_state, "repo_key", None) or getattr(workflow_state, "pending_destructive_repo_key", None) or "").strip().lower()
    wanted_name = str(getattr(workflow_state, "repo_name", None) or getattr(workflow_state, "pending_destructive_repo_name", None) or "").strip().lower()
    wanted_path = str(getattr(workflow_state, "install_location", None) or getattr(workflow_state, "pending_destructive_path", None) or "").strip() or None
    for record in records:
        if wanted_name and record.name.lower() == wanted_name:
            return record
        if wanted_key and (record.name.lower() == wanted_key or record.repo_url.lower() == wanted_key):
            return record
        if wanted_path and path_matches_repo_name(wanted_path, record.name):
            return record
    if wanted_name:
        return RepoCatalogRecord(wanted_name, wanted_key, 0, "", "Unknown", "Unknown", "")
    return None
