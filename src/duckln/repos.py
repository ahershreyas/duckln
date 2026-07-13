"""Interactive cached repo catalog selection helpers."""

from __future__ import annotations

from typing import Callable

from duckln.config import ConfigPaths
from duckln.selections import duckln_search_select
from state.repo_catalog import RepoCatalogRecord, RepoCatalogRefreshError, load_sorted_local_repo_catalog, resolve_public_github_repo_record
from state.store import initialize_state_store


MAX_DESCRIPTION_LENGTH = 60
CANCEL_REPO_SELECTION = "Cancel and return"
CUSTOM_GITHUB_REPO_CHOICE = "Paste a public GitHub repo URL"
RECENT_CUSTOM_REPOS_CHOICE = "Recent custom repos"

def _format_stars(stars: int) -> str:
    """Format star counts for compact display."""
    if stars >= 1_000_000:
        return f"{stars / 1_000_000:.1f}m".rstrip("0").rstrip(".")
    if stars >= 1_000:
        return f"{stars / 1_000:.1f}k".rstrip("0").rstrip(".")
    return str(stars)


def open_repo_catalog(
    paths: ConfigPaths,
    *,
    select: Callable[[str, tuple[str, ...]], str | None] | None = None,
    text_prompt: Callable[[str, str], str | None] | None = None,
    client=None,
    preselected_repo: RepoCatalogRecord | None = None,
) -> RepoCatalogRecord | None:
    """Open the cached repo catalog and return the selected repo record.

    Plan 184 F10: when `preselected_repo` is given (e.g. a GitHub URL pasted in chat that's
    already resolved to a record), skip the catalog UI and return it directly — so the rest of
    the setup flow (environment picker → bring-up) runs unchanged."""
    if preselected_repo is not None:
        return preselected_repo

    records = load_sorted_local_repo_catalog(paths.config_dir)
    if not records:
        raise ValueError("Cached repo catalog is empty.")
    store = initialize_state_store(paths.config_dir)
    recent_custom = tuple(
        RepoCatalogRecord(
            name=record.repo_name,
            repo_url=record.repo_url,
            stars=record.stars,
            description=record.description,
            category=record.category,
            framework=record.framework,
            last_updated=record.last_updated,
        )
        for record in store.list_recent_custom_repos()
    )

    choices = [CANCEL_REPO_SELECTION, CUSTOM_GITHUB_REPO_CHOICE]
    if recent_custom:
        choices.append(RECENT_CUSTOM_REPOS_CHOICE)
    choices.extend(format_repo_catalog_choice(record) for record in records)
    if select is None:
        selected = duckln_search_select("Select a repository:", tuple(choices))
    else:
        select_prompt = select
        selected = select_prompt("Select a repository:", tuple(choices))
    if selected is None:
        return None
    if selected == CANCEL_REPO_SELECTION:
        return None
    if selected == CUSTOM_GITHUB_REPO_CHOICE:
        if text_prompt is None:
            raise ValueError("Custom GitHub repo entry is unavailable in this prompt flow.")
        repo_url = text_prompt("Paste a public GitHub repository URL:", "")
        if repo_url is None or not repo_url.strip():
            return None
        try:
            record = resolve_public_github_repo_record(repo_url.strip(), client=client)
        except (RepoCatalogRefreshError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        store.upsert_recent_custom_repo(
            repo_url=record.repo_url,
            repo_name=record.name,
            stars=record.stars,
            description=record.description,
            category=record.category,
            framework=record.framework,
            last_updated=record.last_updated,
            metadata={"source": "custom_github"},
        )
        return record
    if selected == RECENT_CUSTOM_REPOS_CHOICE:
        if not recent_custom:
            return None
        if select is None:
            selected_recent = duckln_search_select(
                "Recent custom GitHub repos:",
                (CANCEL_REPO_SELECTION,) + tuple(format_repo_catalog_choice(record) for record in recent_custom),
            )
        else:
            selected_recent = select(
                "Recent custom GitHub repos:",
                (CANCEL_REPO_SELECTION,) + tuple(format_repo_catalog_choice(record) for record in recent_custom),
            )
        if selected_recent in {None, CANCEL_REPO_SELECTION}:
            return None
        for record in recent_custom:
            if selected_recent == format_repo_catalog_choice(record):
                return record
        raise ValueError(f"Unknown recent custom repo selection: {selected_recent}")

    for record in records:
        if selected == format_repo_catalog_choice(record):
            return record

    raise ValueError(f"Unknown repo selection: {selected}")


def format_repo_catalog_choice(record: RepoCatalogRecord) -> str:
    """Render a repo catalog record into a readable dropdown label."""

    description = _shorten_description(record.description or "No description available")
    stars = _format_stars(record.stars)
    return f"{record.name} ⭐{stars} — {description} ({record.category} | {record.framework})"


def _shorten_description(description: str) -> str:
    normalized = " ".join(description.split())
    if len(normalized) <= MAX_DESCRIPTION_LENGTH:
        return normalized
    return normalized[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"
