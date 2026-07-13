"""GitHub trending scraper used by /explore."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Protocol


GITHUB_TRENDING_URL = "https://github.com/trending"
AI_RELEVANT_LANGUAGES = frozenset(
    {"python", "jupyter notebook", "cuda", "rust", "c++"}
)


class TrendingPeriod(str, Enum):
    """Period selector matching GitHub trending's `since` query param."""

    TODAY = "daily"
    WEEK = "weekly"
    MONTH = "monthly"


@dataclass(frozen=True)
class TrendingRepo:
    """One trending repository parsed from github.com/trending."""

    full_name: str
    repo_url: str
    description: str
    language: str
    total_stars: int
    period_stars: int
    is_ai_relevant: bool


class HttpClientProtocol(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> "HttpResponseProtocol": ...


class HttpResponseProtocol(Protocol):
    @property
    def status_code(self) -> int: ...
    @property
    def text(self) -> str: ...


class TrendingFetchError(RuntimeError):
    """Raised when GitHub trending cannot be reached or returns a non-200 status."""


def build_trending_url(period: TrendingPeriod, language: str | None) -> str:
    """Return the public GitHub trending URL for a period + optional language filter."""

    base = GITHUB_TRENDING_URL
    clean_language = (language or "").strip().lower()
    if clean_language:
        base = f"{base}/{clean_language.replace(' ', '%20')}"
    return f"{base}?since={period.value}"


def _strip(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_int_with_commas(raw: str) -> int:
    digits = re.sub(r"[^0-9]", "", raw or "")
    return int(digits) if digits else 0


def parse_trending_html(html: str, *, period: TrendingPeriod) -> tuple[TrendingRepo, ...]:
    """Parse the HTML for github.com/trending into TrendingRepo records."""

    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - dependency missing at runtime only
        raise TrendingFetchError(
            "beautifulsoup4 is required for /explore. Install it with `pip install beautifulsoup4`."
        ) from exc

    soup = BeautifulSoup(html or "", "html.parser")
    articles = soup.select("article.Box-row")
    repos: list[TrendingRepo] = []
    for article in articles:
        name_anchor = article.select_one("h2 a") or article.select_one("h1 a")
        if name_anchor is None:
            continue
        href = (name_anchor.get("href") or "").strip()
        if not href.startswith("/"):
            continue
        full_name = href.lstrip("/")
        # GitHub renders the name across two spans with whitespace; collapse here.
        if not full_name or "/" not in full_name:
            continue
        repo_url = f"https://github.com{href}"
        description_node = article.select_one("p")
        description = _strip(description_node.get_text() if description_node else "")
        language_node = article.select_one('[itemprop="programmingLanguage"]')
        language = _strip(language_node.get_text() if language_node else "")
        stargazers_anchor = article.select_one('a[href$="/stargazers"]')
        total_stars = _parse_int_with_commas(
            stargazers_anchor.get_text() if stargazers_anchor else ""
        )
        period_node = article.select_one("span.d-inline-block.float-sm-right")
        period_stars = _parse_int_with_commas(
            period_node.get_text() if period_node else ""
        )
        repos.append(
            TrendingRepo(
                full_name=full_name,
                repo_url=repo_url,
                description=description,
                language=language,
                total_stars=total_stars,
                period_stars=period_stars,
                is_ai_relevant=language.lower() in AI_RELEVANT_LANGUAGES,
            )
        )
    return tuple(repos)


def sort_for_display(repos: tuple[TrendingRepo, ...]) -> tuple[TrendingRepo, ...]:
    """Push no-description repos to the bottom; within buckets keep period-star order."""

    def key(repo: TrendingRepo) -> tuple[int, int, int]:
        no_desc = 0 if repo.description else 1
        # Negative for descending order on period_stars then total_stars.
        return (no_desc, -repo.period_stars, -repo.total_stars)

    return tuple(sorted(repos, key=key))


def fetch_trending(
    *,
    period: TrendingPeriod,
    language: str | None = None,
    client: HttpClientProtocol | None = None,
    user_agent: str = "Duckln/0.1 (+https://duckln.dev)",
    timeout_seconds: float = 8.0,
) -> tuple[TrendingRepo, ...]:
    """Fetch and parse GitHub trending. Raises TrendingFetchError on network failure."""

    if client is None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise TrendingFetchError("httpx is required for /explore.") from exc
        client = httpx.Client(follow_redirects=True)
        owns_client = True
    else:
        owns_client = False
    try:
        url = build_trending_url(period, language)
        try:
            response = client.get(
                url,
                headers={"User-Agent": user_agent, "Accept": "text/html"},
                timeout=timeout_seconds,
            )
        except Exception as exc:
            raise TrendingFetchError(
                "Cannot reach GitHub trending — check your connection."
            ) from exc
        if int(getattr(response, "status_code", 0)) != 200:
            raise TrendingFetchError(
                f"GitHub trending returned status {getattr(response, 'status_code', 'unknown')}."
            )
        parsed = parse_trending_html(getattr(response, "text", ""), period=period)
        return sort_for_display(parsed)
    finally:
        if owns_client and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass
