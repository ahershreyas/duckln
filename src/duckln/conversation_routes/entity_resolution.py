"""Fuzzy repo/entity resolution helpers for conversation turns."""

from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path


def normalize_repo_token(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-")
    return " ".join(lowered.replace("-", " ").split())


def repo_name_from_path(path_hint: str | None) -> str | None:
    if not path_hint:
        return None
    name = Path(path_hint.rstrip("/")).name.strip()
    return name or None


def path_matches_repo_name(path_hint: str | None, repo_name: str | None) -> bool:
    if not path_hint or not repo_name:
        return False
    path_name = repo_name_from_path(path_hint)
    if path_name is None:
        return False
    return normalize_repo_token(path_name) == normalize_repo_token(repo_name)


def resolve_repo_name_hint(
    *,
    repo_name_hint: str | None,
    path_hint: str | None,
    candidate_names: tuple[str, ...] | list[str],
    active_repo_name: str | None = None,
) -> str | None:
    pool = {name.strip() for name in candidate_names if name and name.strip()}
    if active_repo_name:
        pool.add(active_repo_name.strip())
    path_name = repo_name_from_path(path_hint)
    ordered_hints = [hint for hint in (repo_name_hint, path_name) if hint]
    if not ordered_hints or not pool:
        return path_name or repo_name_hint

    normalized_pool = {normalize_repo_token(name): name for name in pool}
    for hint in ordered_hints:
        normalized_hint = normalize_repo_token(hint)
        if normalized_hint in normalized_pool:
            return normalized_pool[normalized_hint]
        close = get_close_matches(normalized_hint, tuple(normalized_pool.keys()), n=1, cutoff=0.72)
        if close:
            return normalized_pool[close[0]]
    return path_name or repo_name_hint
