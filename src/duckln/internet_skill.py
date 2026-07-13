"""Internet-on-demand skill for Duckln.

Item D: gives local models (Ollama, llama.cpp) a bounded `web_search` capability via
the `duckduckgo-search` package, gated by `/internet on|off`. When the user is on a
cloud provider but Internet is disabled in Duckln, the orchestrator surfaces an explicit
in-chat ask before any external call is made.

Network-bounded by design:
  • Result count capped at 8.
  • Per-call timeout 8 seconds.
  • Returns plaintext snippets only (no HTML / no auto-fetch of result pages).
  • All calls go through `internet_search_summary` so a single off-switch silences them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from state.access import read_config_snapshot, write_config_snapshot


_INTERNET_ENABLED_KEY = "internet.enabled"
_DEFAULT_RESULT_COUNT = 5
_MAX_RESULT_COUNT = 8
_DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class InternetSearchResult:
    """A single bounded DuckDuckGo result."""

    title: str
    url: str
    snippet: str


def is_internet_enabled(config_dir) -> bool:
    """Whether the /internet toggle is currently set to ON. Default OFF."""

    snapshot = read_config_snapshot(config_dir)
    raw = str(snapshot.get(_INTERNET_ENABLED_KEY) or "").strip().lower()
    return raw in {"1", "true", "on", "yes"}


def ensure_internet_for_cloud_target(
    config_dir,
    *,
    execution_target: str,
    display: object | None = None,
) -> bool:
    """Auto-enable internet when the user is on a cloud target (AWS/GCP).

    Cloud users are already paying for outbound network — Duckln treats internet as
    on-by-default in that case. Returns the post-call enabled state. Surfaces the change
    via `display` (callable) when it actually flips, so the user is never surprised.
    """

    target = (execution_target or "").strip().lower()
    if target not in {"aws", "gcp"}:
        return is_internet_enabled(config_dir)
    if is_internet_enabled(config_dir):
        return True
    set_internet_enabled(config_dir, True)
    if display is not None and callable(display):
        try:
            display(
                f"Cloud target {target.upper()} is active — Duckln enabled the internet "
                f"skill automatically. Toggle off any time with /internet off."
            )
        except Exception:
            pass
    return True


def set_internet_enabled(config_dir, enabled: bool) -> None:
    """Persist the /internet toggle. Materializes a skill doc on first enable so the
    agent layer (and the user reading ~/.duckln/memory/skills/) can see what's available.
    """

    write_config_snapshot(config_dir, {_INTERNET_ENABLED_KEY: "true" if enabled else "false"})
    if enabled:
        try:
            from state.access import write_skill_memory_state

            write_skill_memory_state(
                config_dir,
                slug="internet-search",
                title="Internet search via DuckDuckGo",
                summary=(
                    "Toggle: /internet on | /internet off | /internet status\n"
                    "Backend: duckduckgo-search (PyPI) — text mode, no JS / no scraping.\n"
                    "Caps: 8 results max, 8 s timeout per call, plaintext snippets only.\n"
                    "Used by: orchestrator failure recovery (when no curated recipe matches),\n"
                    "and local models that lack built-in browsing — search results are\n"
                    "injected into the next prompt as plaintext context.\n"
                    "Privacy: no telemetry, no API key, no result caching beyond a session.\n"
                    "Cloud (AWS/GCP) targets auto-enable this on connect."
                ),
            )
        except Exception:
            pass


def render_internet_status(config_dir) -> str:
    """One-line status string for /internet status."""

    enabled = is_internet_enabled(config_dir)
    if enabled:
        return "Internet skill: ON. Local models can search the web via DuckDuckGo."
    return "Internet skill: OFF. Toggle with /internet on."


def internet_search_summary(
    query: str,
    *,
    config_dir,
    max_results: int = _DEFAULT_RESULT_COUNT,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[InternetSearchResult, ...]:
    """Run a DuckDuckGo search subject to the /internet toggle. Returns () when OFF.

    Caller is responsible for surfacing the toggle's state to the user; this function
    silently no-ops when disabled so no accidental network call ever fires.
    """

    if not query.strip():
        return ()
    if not is_internet_enabled(config_dir):
        return ()
    capped = max(1, min(_MAX_RESULT_COUNT, int(max_results)))
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]
    except ImportError:
        return ()
    try:
        with DDGS(timeout=timeout_seconds) as ddgs:
            raw_results = list(ddgs.text(query, max_results=capped))
    except Exception:
        return ()
    return tuple(_normalize_search_result(item) for item in raw_results if item)


def render_search_summary(results: Iterable[InternetSearchResult]) -> str:
    """Compact multi-line summary suitable for a chat message."""

    lines = []
    for index, result in enumerate(results, start=1):
        title = result.title.strip() or "(untitled)"
        url = result.url.strip()
        snippet = result.snippet.strip()
        head = f"{index}. {title}"
        if url:
            head += f" — {url}"
        lines.append(head)
        if snippet:
            lines.append(f"   {snippet}")
    if not lines:
        return "No internet results returned."
    return "\n".join(lines)


def cloud_internet_consent_prompt(provider: str | None) -> str:
    """When a cloud target is active and internet is off, ask the user explicitly."""

    label = (provider or "cloud").upper() if provider else "cloud"
    return (
        f"Internet is currently OFF in Duckln, but the active provider is {label}. "
        "Do you want me to enable web search via DuckDuckGo so the model can ground "
        "answers in real-time results? Reply: yes / no."
    )


def _normalize_search_result(payload: object) -> InternetSearchResult:
    if isinstance(payload, dict):
        return InternetSearchResult(
            title=str(payload.get("title") or "").strip(),
            url=str(payload.get("href") or payload.get("url") or "").strip(),
            snippet=str(payload.get("body") or payload.get("description") or "").strip(),
        )
    return InternetSearchResult(title=str(payload), url="", snippet="")
