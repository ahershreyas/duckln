"""Interactive cached repo catalog selection helpers."""

from __future__ import annotations

from typing import Callable

from duckln.config import ConfigPaths
from state.repo_catalog import RepoCatalogRecord, load_sorted_local_repo_catalog


MAX_DESCRIPTION_LENGTH = 60
CANCEL_REPO_SELECTION = "Cancel and return"

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
) -> RepoCatalogRecord | None:
    """Open the cached repo catalog and return the selected repo record."""

    if select is None:
        from duckln.config import _default_select_prompt

        select_prompt = _default_select_prompt
    else:
        select_prompt = select

    records = load_sorted_local_repo_catalog(paths.config_dir)
    if not records:
        raise ValueError("Cached repo catalog is empty.")

    choices = (CANCEL_REPO_SELECTION,) + tuple(format_repo_catalog_choice(record) for record in records)
    selected = select_prompt("Select a repository:", choices)
    if selected is None:
        return None
    if selected == CANCEL_REPO_SELECTION:
        return None

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
