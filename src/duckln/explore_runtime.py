"""Glue layer between the /explore UI surfaces and the existing repo bring-up flow."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Callable

from duckln.explore_cache import (
    DEFAULT_TTL_SECONDS,
    cache_age_seconds,
    cache_clear,
    cache_get,
    cache_put,
)
from duckln.explore_trending import (
    HttpClientProtocol,
    TrendingFetchError,
    TrendingPeriod,
    TrendingRepo,
    fetch_trending,
)


CONNECTION_ERROR_MESSAGE = "Cannot reach GitHub trending — check your connection."
EMPTY_PARSE_MESSAGE = (
    "Trending data unavailable — GitHub may have updated their page structure."
)
EXPLORE_DEBUG_FILENAME = "explore_debug.html"


@dataclass(frozen=True)
class ExploreView:
    """Resolved view of the trending list for the explore overlay."""

    period: TrendingPeriod
    language: str | None
    repos: tuple[TrendingRepo, ...]
    served_from_cache: bool
    cache_age_seconds: int | None


def _config_logs_dir(config_dir: str | PathLike[str] | Path) -> Path:
    path = Path(config_dir) / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def _write_debug_snippet(config_dir: str | PathLike[str] | Path, html: str) -> None:
    if not html:
        return
    try:
        target = _config_logs_dir(config_dir) / EXPLORE_DEBUG_FILENAME
        target.write_text(html[:8192], encoding="utf-8")
    except Exception:
        pass


def load_explore_view(
    *,
    config_dir: str | PathLike[str] | Path,
    period: TrendingPeriod,
    language: str | None = None,
    http_client: HttpClientProtocol | None = None,
    force_refresh: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    clock: Callable[[], float] | None = None,
    fetcher: Callable[..., tuple[TrendingRepo, ...]] | None = None,
) -> ExploreView | str:
    """Return an ExploreView, a freshly fetched payload, or a user-facing error string."""

    if not force_refresh:
        cached = cache_get(
            config_dir,
            period=period,
            language=language,
            ttl_seconds=ttl_seconds,
            clock=clock,
        )
        if cached:
            age = cache_age_seconds(config_dir, period=period, language=language, clock=clock)
            return ExploreView(
                period=period,
                language=language,
                repos=cached,
                served_from_cache=True,
                cache_age_seconds=age,
            )
    fetch = fetcher or fetch_trending
    try:
        repos = fetch(period=period, language=language, client=http_client)
    except TrendingFetchError:
        return CONNECTION_ERROR_MESSAGE
    except Exception:
        return CONNECTION_ERROR_MESSAGE
    if not repos:
        # Parser returned 0 — likely an HTML structure change.
        return EMPTY_PARSE_MESSAGE
    cache_put(config_dir, period=period, language=language, repos=repos, clock=clock)
    return ExploreView(
        period=period,
        language=language,
        repos=repos,
        served_from_cache=False,
        cache_age_seconds=0,
    )


def refresh_explore_view(
    *,
    config_dir: str | PathLike[str] | Path,
    period: TrendingPeriod,
    language: str | None = None,
    http_client: HttpClientProtocol | None = None,
    clock: Callable[[], float] | None = None,
) -> ExploreView | str:
    """Force a refresh of one (period, language) cell."""

    cache_clear(config_dir, period=period, language=language)
    return load_explore_view(
        config_dir=config_dir,
        period=period,
        language=language,
        http_client=http_client,
        force_refresh=True,
        clock=clock,
    )


def write_parse_failure_debug_snippet(
    config_dir: str | PathLike[str] | Path,
    html: str,
) -> None:
    """Persist the raw HTML body of the latest failed parse for diagnostics."""

    _write_debug_snippet(config_dir, html)


def filter_repos_by_language(
    repos: tuple[TrendingRepo, ...],
    needle: str,
) -> tuple[TrendingRepo, ...]:
    """Filter the rendered list by language substring; empty needle keeps everything."""

    clean = (needle or "").strip().lower()
    if not clean:
        return repos
    return tuple(repo for repo in repos if clean in repo.language.lower())


_PERIOD_LABELS = {
    TrendingPeriod.TODAY: "Today",
    TrendingPeriod.WEEK: "This Week",
    TrendingPeriod.MONTH: "This Month",
}


def _format_stars(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return str(value)


def render_repo_label(repo: TrendingRepo, period: TrendingPeriod) -> str:
    """Picker row for one trending repo."""

    period_token = {
        TrendingPeriod.TODAY: "today",
        TrendingPeriod.WEEK: "this week",
        TrendingPeriod.MONTH: "this month",
    }[period]
    parts = [
        repo.full_name,
        repo.language or "—",
        f"★ {_format_stars(repo.total_stars)}",
        f"+{_format_stars(repo.period_stars)} {period_token}",
    ]
    if repo.is_ai_relevant:
        parts.append("[AI]")
    label = "  ".join(parts)
    if repo.description:
        description = repo.description
        if len(description) > 80:
            description = description[:77] + "…"
        label = f"{label}  — {description}"
    return label


def render_explore_header(view: ExploreView) -> str:
    """Plain-text header line for the explore browser."""

    period_label = _PERIOD_LABELS[view.period]
    parts = [f"GitHub Trending — {period_label}"]
    if view.language:
        parts.append(f"language: {view.language}")
    if view.served_from_cache and view.cache_age_seconds is not None:
        minutes = view.cache_age_seconds // 60
        cache_hint = f"cached {minutes}m ago" if minutes else "just cached"
        parts.append(f"[{cache_hint}]")
    parts.append(f"({len(view.repos)} repos)")
    parts.append("Press Q or Esc to cancel; pick a repo to launch setup immediately.")
    return " · ".join(parts)


SWITCH_TODAY_LABEL = "[Filter] Period — Today"
SWITCH_WEEK_LABEL = "[Filter] Period — This Week"
SWITCH_MONTH_LABEL = "[Filter] Period — This Month"
EDIT_FILTER_LABEL = "[Filter] Language — type to filter…"
CLEAR_FILTER_LABEL = "[Filter] Language — clear"
REFRESH_LABEL = "[Filter] Refresh now"


_PERIOD_SWITCH = {
    SWITCH_TODAY_LABEL: TrendingPeriod.TODAY,
    SWITCH_WEEK_LABEL: TrendingPeriod.WEEK,
    SWITCH_MONTH_LABEL: TrendingPeriod.MONTH,
}


def _control_options(view: ExploreView) -> tuple[str, ...]:
    options: list[str] = []
    if view.period != TrendingPeriod.TODAY:
        options.append(SWITCH_TODAY_LABEL)
    if view.period != TrendingPeriod.WEEK:
        options.append(SWITCH_WEEK_LABEL)
    if view.period != TrendingPeriod.MONTH:
        options.append(SWITCH_MONTH_LABEL)
    options.append(EDIT_FILTER_LABEL)
    if view.language:
        options.append(CLEAR_FILTER_LABEL)
    options.append(REFRESH_LABEL)
    return tuple(options)


def run_explore_loop(
    *,
    config_dir: str | PathLike[str] | Path,
    select_prompt: Callable[[str, tuple[str, ...]], str | None],
    text_prompt: Callable[[str, str], str | None] | None,
    display_output: Callable[[str], None],
    initial_period: TrendingPeriod = TrendingPeriod.TODAY,
    initial_language: str | None = None,
    http_client: HttpClientProtocol | None = None,
    max_rows: int = 25,
    cancel_hint: str = "(Press Q or Esc to cancel)",
) -> str | None:
    """Interactive loop: period switch, language filter, repo pick. Returns selected repo URL or None."""

    period = initial_period
    language: str | None = initial_language
    while True:
        outcome = load_explore_view(
            config_dir=config_dir,
            period=period,
            language=language,
            http_client=http_client,
        )
        if isinstance(outcome, str):
            display_output(outcome)
            return None
        view = outcome
        repos = view.repos[:max_rows]
        if not repos:
            display_output("No trending repos to show — try a different language filter.")
        repo_labels = tuple(render_repo_label(repo, period) for repo in repos)
        label_to_url = {label: repo.repo_url for label, repo in zip(repo_labels, repos)}
        options = _control_options(view) + repo_labels + ("Cancel",)
        prompt = f"{render_explore_header(view)}\n{cancel_hint}"
        chosen = select_prompt(prompt, options)
        if chosen in {None, "Cancel"}:
            return None
        if chosen in label_to_url:
            return label_to_url[chosen]
        if chosen in _PERIOD_SWITCH:
            period = _PERIOD_SWITCH[chosen]
            continue
        if chosen == EDIT_FILTER_LABEL:
            if text_prompt is None:
                display_output("Language filter requires text input; not available in this mode.")
                continue
            response = text_prompt("Filter by language (blank to clear):", language or "")
            language = (response or "").strip() or None
            continue
        if chosen == CLEAR_FILTER_LABEL:
            language = None
            continue
        if chosen == REFRESH_LABEL:
            refresh_explore_view(
                config_dir=config_dir,
                period=period,
                language=language,
                http_client=http_client,
            )
            continue
        # Unknown option label — exit safely.
        return None
