"""Interactive cached repo catalog selection helpers."""

from __future__ import annotations

from typing import Callable

from duckln.config import ConfigPaths
from state.repo_catalog import RepoCatalogRecord, load_sorted_local_repo_catalog


MAX_DESCRIPTION_LENGTH = 60


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

    choices = tuple(format_repo_catalog_choice(record) for record in records)
    selected = select_prompt("Select a repository:", choices)
    if selected is None:
        return None

    for record in records:
        if selected == format_repo_catalog_choice(record):
            return record

    raise ValueError(f"Unknown repo selection: {selected}")


def format_repo_catalog_choice(record: RepoCatalogRecord) -> str:
    """Render a repo catalog record into a readable dropdown label."""

    description = _shorten_description(record.description)
    return f"{record.name} | {record.stars} stars | {description} | {record.category} | {record.framework}"


def _shorten_description(description: str) -> str:
    normalized = " ".join(description.split())
    if len(normalized) <= MAX_DESCRIPTION_LENGTH:
        return normalized
    return normalized[: MAX_DESCRIPTION_LENGTH - 3].rstrip() + "..."
