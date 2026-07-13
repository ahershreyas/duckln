"""SQLite-backed cache for /explore trending payloads with a 15-minute TTL."""

from __future__ import annotations

from dataclasses import asdict
import json
import time
from os import PathLike
from pathlib import Path
from typing import Callable

from duckln.explore_trending import TrendingPeriod, TrendingRepo
from state.store import initialize_state_store


EXPLORE_CACHE_PREFIX = "conversation.explore_cache."
DEFAULT_TTL_SECONDS = 15 * 60


def _cache_key(period: TrendingPeriod, language: str | None) -> str:
    language_token = (language or "").strip().lower() or "_"
    return f"{EXPLORE_CACHE_PREFIX}{period.value}.{language_token}"


def _now_seconds(clock: Callable[[], float] | None = None) -> float:
    return float(clock() if clock is not None else time.time())


def cache_put(
    config_dir: str | PathLike[str] | Path,
    *,
    period: TrendingPeriod,
    language: str | None,
    repos: tuple[TrendingRepo, ...],
    clock: Callable[[], float] | None = None,
) -> None:
    """Persist a trending payload under the (period, language) cache key."""

    store = initialize_state_store(Path(config_dir))
    payload = {
        "stored_at": _now_seconds(clock),
        "repos": [asdict(repo) for repo in repos],
    }
    store.upsert_config_values({_cache_key(period, language): json.dumps(payload)})


def cache_get(
    config_dir: str | PathLike[str] | Path,
    *,
    period: TrendingPeriod,
    language: str | None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    clock: Callable[[], float] | None = None,
) -> tuple[TrendingRepo, ...] | None:
    """Return cached repos if present and not expired; otherwise None."""

    store = initialize_state_store(Path(config_dir))
    key = _cache_key(period, language)
    raw = store.read_config_values(prefix=EXPLORE_CACHE_PREFIX).get(key)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    stored_at = float(payload.get("stored_at") or 0.0)
    if _now_seconds(clock) - stored_at > ttl_seconds:
        return None
    raw_repos = payload.get("repos") or []
    if not isinstance(raw_repos, list):
        return None
    repos: list[TrendingRepo] = []
    for entry in raw_repos:
        if not isinstance(entry, dict):
            continue
        try:
            repos.append(
                TrendingRepo(
                    full_name=str(entry.get("full_name") or ""),
                    repo_url=str(entry.get("repo_url") or ""),
                    description=str(entry.get("description") or ""),
                    language=str(entry.get("language") or ""),
                    total_stars=int(entry.get("total_stars") or 0),
                    period_stars=int(entry.get("period_stars") or 0),
                    is_ai_relevant=bool(entry.get("is_ai_relevant", False)),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(repos)


def cache_age_seconds(
    config_dir: str | PathLike[str] | Path,
    *,
    period: TrendingPeriod,
    language: str | None,
    clock: Callable[[], float] | None = None,
) -> int | None:
    """Return the age in seconds of the cached payload, or None when absent."""

    store = initialize_state_store(Path(config_dir))
    raw = store.read_config_values(prefix=EXPLORE_CACHE_PREFIX).get(_cache_key(period, language))
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    stored_at = float(payload.get("stored_at") or 0.0)
    return max(0, int(_now_seconds(clock) - stored_at))


def cache_clear(
    config_dir: str | PathLike[str] | Path,
    *,
    period: TrendingPeriod,
    language: str | None,
) -> None:
    """Remove a single cached payload."""

    store = initialize_state_store(Path(config_dir))
    store.delete_config_values((_cache_key(period, language),))
