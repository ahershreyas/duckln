"""Bounded trusted-source helpers for runtime error repair paths."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import platform
import re
from urllib.parse import quote_plus, unquote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Callable

from duckln.diagnostics import redact_sensitive_data
from duckln.execution_trace import render_tool_invocation_trace
from duckln.repair_intake import (
    is_duckln_synthetic_line,
    is_shell_prompt_line,
)


# --- Plan 181: the Web Reader — distill a fetched page into a structured digest -------------
_WEB_READER_MIN_CHARS = 4096  # only distill a page large enough that the token win pays for the call


@lru_cache(maxsize=None)
def _web_reader_spec_prompt() -> str:
    from duckln.harness.agent_def import (
        builtin_agents_directory,
        load_agent_definition_from_path,
        render_output_contract_text,
    )

    definition = load_agent_definition_from_path(builtin_agents_directory() / "web_reader.md")
    contract = render_output_contract_text(definition)
    return definition.system_prompt + (f"\n\n{contract}" if contract else "")


def read_web_page(*, excerpt: str, url: str = "", config_dir=None, context: str = "", llm_client=None) -> dict | None:
    """Plan 181: distill ONE fetched authoritative page into a STRUCTURED digest via the
    WEB_READER model — ``{relevant, fix_commands, key_excerpt, confidence}``.

    Strictly ADDITIVE + guarded: returns None when there's no reader model, the page is too
    small to be worth a call, or the read fails / can't parse / is low-confidence — so the
    caller FALLS BACK to the raw excerpt and the fix can never be summarized away. Never raises."""
    text = (excerpt or "").strip()
    if len(text) < _WEB_READER_MIN_CHARS:
        return None
    try:
        if llm_client is None:
            from duckln.ai_client import build_llm_client_for_role

            llm_client = build_llm_client_for_role(config_dir, "WEB_READER")
        if llm_client is None:
            return None
        from duckln.harness.agent_def import _extract_json

        user = (f"Failing step context: {context}\n\n" if context else "") + f"URL: {url}\n\nPAGE:\n{text[:12000]}"
        raw = llm_client(
            system_prompt=_web_reader_spec_prompt(), user_message=user, json_mode=True, max_tokens=600
        )
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return None
        return {
            "relevant": bool(parsed.get("relevant")),
            "fix_commands": [str(c) for c in (parsed.get("fix_commands") or []) if str(c).strip()][:10],
            "key_excerpt": str(parsed.get("key_excerpt") or "").strip()[:1500],
            "confidence": str(parsed.get("confidence") or "low").strip().lower(),
        }
    except Exception:
        return None


@dataclass(frozen=True)
class WebReference:
    """A single official documentation reference Duckln can surface."""

    label: str
    url: str


@dataclass(frozen=True)
class WebReferenceSummary:
    """Small fetched summary for one remote documentation page."""

    reference: WebReference
    title: str
    excerpt: str
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebSearchResult:
    """Single bounded fallback result from a general web search."""

    title: str
    url: str
    excerpt: str


@dataclass(frozen=True)
class RuntimeRepairEvidence:
    """Structured evidence Duckln can show before suggesting or applying a repair."""

    note: str | None
    source_urls: tuple[str, ...]
    search_query: str | None


_REFERENCE_MAP: tuple[tuple[tuple[str, ...], WebReference], ...] = (
    (("whisper",), WebReference("OpenAI Whisper repo", "https://github.com/openai/whisper")),
    (("pip", "no module named", "modulenotfounderror"), WebReference("Python packaging", "https://packaging.python.org/en/latest/tutorials/installing-packages/")),
    (("venv", "virtual environment"), WebReference("Python venv", "https://docs.python.org/3/library/venv.html")),
    (("python", "python3"), WebReference("Python docs", "https://docs.python.org/3/")),
    (("npm", "node", "nodejs"), WebReference("npm CLI docs", "https://docs.npmjs.com/")),
    (("yarn",), WebReference("Yarn docs", "https://yarnpkg.com/getting-started")),
    (("pnpm",), WebReference("pnpm docs", "https://pnpm.io/installation")),
    (("docker", "container"), WebReference("Docker docs", "https://docs.docker.com/")),
    (("multipass",), WebReference("Multipass docs", "https://documentation.ubuntu.com/multipass/latest/")),
    (("uvicorn",), WebReference("Uvicorn docs", "https://www.uvicorn.org/")),
    (("fastapi",), WebReference("FastAPI docs", "https://fastapi.tiangolo.com/")),
    (("streamlit",), WebReference("Streamlit docs", "https://docs.streamlit.io/")),
    (("gradio",), WebReference("Gradio docs", "https://www.gradio.app/guides")),
    (("torch", "pytorch"), WebReference("PyTorch docs", "https://pytorch.org/docs/stable/index.html")),
    (("ffmpeg",), WebReference("FFmpeg docs", "https://ffmpeg.org/documentation.html")),
    (("ollama",), WebReference("Ollama docs", "https://github.com/ollama/ollama/tree/main/docs")),
)

REMOTE_FETCH_TIMEOUT_SECONDS = 4.0
_SEARCH_TIMEOUT_SECONDS = 8.0
MAX_REMOTE_SUMMARY_CHARS = 240
MAX_SEARCH_QUERY_CHARS = 160
_BROWSER_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_PATTERN = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_RESULT_LINK_PATTERN = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_SNIPPET_PATTERN = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*>.*?</a>(?P<tail>.*?)(?:<a[^>]+class="[^"]*result__a[^"]*"|</body>)',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_SNIPPET_INNER_PATTERN = re.compile(
    r'<(?:div|a)[^>]+class="[^"]*(?:result__snippet|result__extras__url)[^"]*"[^>]*>(?P<snippet>.*?)</(?:div|a)>',
    re.IGNORECASE | re.DOTALL,
)
_BING_RESULT_PATTERN = re.compile(
    r'<li class="b_algo"[^>]*>.*?<a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>(?P<title>[^<]+)</a>.*?<p[^>]*>(?P<snippet>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_search_result_cache: dict[str, "WebSearchResult"] = {}
_MAX_SEARCH_CACHE = 32
_TRUSTED_PUBLIC_DOMAINS = (
    "stackoverflow.com",
    "serverfault.com",
    "superuser.com",
    "github.com",
    "github.io",
    "medium.com",
    "dev.to",
    "discuss.",
    "forum.",
    "community.",
    "docs.",
    "readthedocs.io",
)
_QUERY_TOKEN_SPLIT_PATTERN = re.compile(r"[^a-z0-9.+#/_-]+", re.IGNORECASE)
_GENERIC_STATUS_PREFIXES = (
    "duckln classified this blocker as",
    "duckln searched",
    "duckln found",
    "duckln checked",
)
_NOISE_LINE_TOKENS = (
    "duckln trace:",
    "objective:",
    "attempt ",
    "supervisor agent",
    "repair runtime issue",
)
_SHELL_COMMAND_HINT_PATTERN = re.compile(
    r"\b(?:sudo\s+)?(?:apt(?:-get)?|snap|brew|npm|pnpm|yarn|pip(?:3)?|python(?:3)?|node|docker|git|curl|wget|which|export|openclaw)\b[^\n]{0,120}",
    re.IGNORECASE,
)
_MARKETING_DOMAIN_HINTS = ("hosting", "guide", "roadmap", "tips", "tutorial")


def references_for_runtime_issue(*, command: str | None = None, error_text: str | None = None) -> tuple[WebReference, ...]:
    """Return a deduplicated ordered list of likely-official web references for a runtime blocker."""

    searchable = " ".join(part for part in (command or "", error_text or "") if part).lower()
    if not searchable.strip():
        return ()
    matches: list[WebReference] = []
    for keywords, reference in _REFERENCE_MAP:
        if any(keyword in searchable for keyword in keywords):
            if reference.url not in {item.url for item in matches}:
                matches.append(reference)
    return tuple(matches[:3])


def fetch_web_reference_summary(
    reference: WebReference,
    *,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
) -> WebReferenceSummary | None:
    """Fetch a compact human-readable summary from a remote documentation page."""

    if trace is not None:
        trace(
            render_tool_invocation_trace(
                title="web documentation lookup",
                tool_id="web.documentation_lookup",
                action=f"Fetch an official documentation summary for {reference.label}",
                detail_lines=(f"Remote source: {reference.url}",),
                execution_target=execution_target,
                source_urls=(reference.url,),
            )
        )
    return _fetch_web_reference_summary(reference.label, reference.url)


@lru_cache(maxsize=32)
def _fetch_web_reference_summary(label: str, url: str) -> WebReferenceSummary | None:
    """Fetch and cache a compact human-readable summary for one documentation URL."""

    request = Request(url, headers=_BROWSER_HEADERS)
    try:
        with urlopen(request, timeout=REMOTE_FETCH_TIMEOUT_SECONDS) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            body = response.read(32000).decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if "html" in content_type or "<html" in body.lower():
        title = _extract_html_title(body) or label
        excerpt = _extract_html_excerpt(body)
        steps = extract_steps_from_html(body)
    else:
        title = label
        excerpt = _extract_text_excerpt(body)
        steps = ()
    if not excerpt:
        return None
    return WebReferenceSummary(reference=WebReference(label=label, url=url), title=title, excerpt=excerpt, steps=steps)


def build_runtime_web_reference_note(
    *,
    command: str | None = None,
    error_text: str | None = None,
    fetch_live: bool = False,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
) -> str | None:
    """Render a compact note Duckln can attach to a runtime failure summary."""

    references = references_for_runtime_issue(command=command, error_text=error_text)
    if not references:
        if fetch_live:
            search_result = search_runtime_issue(
                command=command,
                error_text=error_text,
                trace=trace,
                execution_target=execution_target,
            )
            if search_result is not None:
                return (
                    "Duckln broadened the search on the web for this blocker: "
                    f"{search_result.title} — {search_result.excerpt} "
                    f"Source: [1] {search_result.url}"
                )
        return None
    if fetch_live:
        summary = fetch_web_reference_summary(
            references[0],
            trace=trace,
            execution_target=execution_target,
        )
        if summary is not None:
            steps_text = _render_steps(summary.steps)
            return (
                "Duckln checked current official docs for this blocker: "
                f"{summary.title} — {summary.excerpt}"
                f"{steps_text} "
                f"Source: [1] {summary.reference.url}"
            )
        search_result = search_runtime_issue(
            command=command,
            error_text=error_text,
            trace=trace,
            execution_target=execution_target,
        )
        if search_result is not None:
            return (
                "Duckln broadened the search on the web after the official docs were not enough: "
                f"{search_result.title} — {search_result.excerpt} "
                f"Source: [1] {search_result.url}"
            )
    rendered = "; ".join(f"[{index}] {reference.label}: {reference.url}" for index, reference in enumerate(references[:2], start=1))
    return f"Duckln can cross-check current docs for this blocker: {rendered}."


def gather_runtime_repair_evidence(
    *,
    command: str | None = None,
    error_text: str | None = None,
    fetch_live: bool = True,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
    os_hint: str | None = None,
) -> RuntimeRepairEvidence:
    """Collect compact web evidence Duckln can surface before a bounded repair step."""

    references = references_for_runtime_issue(command=command, error_text=error_text)
    search_queries = _runtime_search_queries(command=command, error_text=error_text, os_hint=os_hint)
    search_query = search_queries[0] if search_queries else None
    source_urls: list[str] = []
    note = None
    if fetch_live and search_query:
        search_results = search_runtime_issue_results(
            command=command,
            error_text=error_text,
            trace=trace,
            execution_target=execution_target,
            os_hint=os_hint,
        )
        if search_results:
            search_result = search_results[0]
            source_urls.append(search_result.url)
            source_urls.extend(item.url for item in search_results[1:5])
            search_excerpt = search_result.excerpt
            search_steps = ""
            # Only deep-fetch likely documentation pages; random pages often produce noisy excerpts.
            if _trusted_source_rank(search_result.url) <= 4:
                search_summary = _fetch_web_reference_summary(search_result.title, search_result.url)
                if search_summary is not None:
                    search_excerpt = search_summary.excerpt or search_excerpt
                    search_steps = _render_steps(search_summary.steps)
            suggested_commands = _extract_candidate_commands(
                search_results=search_results[:5],
                execution_target=execution_target,
                os_hint=os_hint,
            )
            quick_causes = "; ".join(item.title for item in search_results[:3])
            commands_text = (
                " Suggested commands: "
                + " | ".join(f"`{command_text}`" for command_text in suggested_commands[:3])
                if suggested_commands
                else ""
            )
            note = (
                "Duckln searched the exact terminal error in DuckDuckGo/trusted sources: "
                f"{search_result.title} — {search_excerpt}"
                f"{search_steps} "
                f"Source: [1] {search_result.url}. "
                f"Top web results reviewed: {', '.join(url for url in source_urls[:5])}. "
                f"Likely causes from top pages: {quick_causes}."
                f"{commands_text}"
            )
    if fetch_live and references and note is None:
        summary = fetch_web_reference_summary(
            references[0],
            trace=trace,
            execution_target=execution_target,
        )
        if summary is not None:
            source_urls.append(summary.reference.url)
            steps_text = _render_steps(summary.steps)
            note = (
                "Duckln checked current official docs for this blocker: "
                f"{summary.title} — {summary.excerpt}"
                f"{steps_text} "
                f"Source: [1] {summary.reference.url}"
            )
    if note is None and references:
        source_urls.extend(reference.url for reference in references[:2])
        rendered = "; ".join(
            f"[{index}] {reference.label}: {reference.url}"
            for index, reference in enumerate(references[:2], start=1)
        )
        note = f"Duckln can cross-check current docs for this blocker: {rendered}."
    return RuntimeRepairEvidence(
        note=note,
        source_urls=tuple(dict.fromkeys(url for url in source_urls if url)),
        search_query=search_query,
    )


def search_runtime_issue(
    *,
    command: str | None = None,
    error_text: str | None = None,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
    os_hint: str | None = None,
) -> WebSearchResult | None:
    """Run a small bounded trusted-source search when local evidence is insufficient."""

    queries = _runtime_search_queries(command=command, error_text=error_text, os_hint=os_hint)
    if not queries:
        return None
    query = queries[0]
    if trace is not None:
        trace(
            render_tool_invocation_trace(
                title="web search lookup",
                tool_id="web.documentation_lookup",
                action="Search the exact captured terminal error in DuckDuckGo/trusted public sources",
                detail_lines=(
                    "Duckln searches the exact error text first, then checks well-known public sources such as GitHub issues, blogs, and Stack Overflow.",
                    "Duckln still requires approval before running any repair command.",
                ),
                execution_target=execution_target,
                search_query=query,
            )
        )
    results = _search_runtime_issue_results(queries)
    return None if not results else results[0]


def search_runtime_issue_results(
    *,
    command: str | None = None,
    error_text: str | None = None,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
    os_hint: str | None = None,
) -> tuple[WebSearchResult, ...]:
    """Return top bounded web results (3-5) for the exact runtime blocker."""

    queries = _runtime_search_queries(command=command, error_text=error_text, os_hint=os_hint)
    if not queries:
        return ()
    query = queries[0]
    if trace is not None:
        trace(
            render_tool_invocation_trace(
                title="web search lookup",
                tool_id="web.documentation_lookup",
                action="Search exact error + failing command + OS context in DuckDuckGo/trusted public sources",
                detail_lines=(
                    "Duckln searches the exact captured error first, then broadens with command and OS context.",
                    "Duckln reviews top results and extracts likely root causes/commands before any mutation.",
                ),
                execution_target=execution_target,
                search_query=query,
            )
        )
    return _search_runtime_issue_results(queries)


def build_runtime_search_query(*, command: str | None = None, error_text: str | None = None, os_hint: str | None = None) -> str:
    """Return the bounded public search query Duckln would use for a runtime blocker."""

    return _build_search_query(
        command=redact_sensitive_data(command or ""),
        error_text=redact_sensitive_data(error_text or ""),
        os_hint=os_hint,
    )


def _runtime_search_queries(*, command: str | None, error_text: str | None, os_hint: str | None = None) -> tuple[str, ...]:
    """Return exact-error-first DuckDuckGo queries, then a broader trusted-source query."""

    safe_command = redact_sensitive_data(command or "")
    safe_error_text = redact_sensitive_data(error_text or "")
    broad_query = _build_search_query(command=safe_command, error_text=safe_error_text, os_hint=os_hint)
    exact_error = _exact_error_query_fragment(safe_error_text)
    queries: list[str] = []
    if exact_error:
        exact_query = f'"{exact_error}"'
        command_hint = re.sub(r"\s+", " ", safe_command).strip()
        if command_hint:
            quoted_command = f'"{command_hint}"'
            if len(exact_query) + 1 + len(quoted_command) <= MAX_SEARCH_QUERY_CHARS:
                exact_query = f"{exact_query} {quoted_command}"
        os_text = _default_os_hint(os_hint)
        if os_text:
            quoted_os = f'"{os_text}"'
            if len(exact_query) + 1 + len(quoted_os) <= MAX_SEARCH_QUERY_CHARS:
                exact_query = f"{exact_query} {quoted_os}"
        queries.append(exact_query.strip())
    if broad_query:
        queries.append(broad_query)
    return tuple(dict.fromkeys(query for query in queries if query))


def _search_runtime_issue(query: str) -> WebSearchResult | None:
    """Backward-compatible single-result helper."""

    results = _search_runtime_issue_candidates(query)
    return None if not results else results[0]


def _search_runtime_issue_results(queries: tuple[str, ...]) -> tuple[WebSearchResult, ...]:
    """Search across query fallbacks and return up to 5 deduplicated ranked results."""

    aggregated: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for query in queries:
        candidates = _search_runtime_issue_candidates(query)
        for candidate in candidates:
            if candidate.url in seen_urls:
                continue
            aggregated.append(candidate)
            seen_urls.add(candidate.url)
            if len(aggregated) >= 5:
                return tuple(aggregated)
    return tuple(aggregated)


def gather_repair_fix(
    *,
    command: str | None = None,
    error_text: str | None = None,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
    os_hint: str | None = None,
    max_pages: int = 3,
) -> str | None:
    """Plan 80 Fix 7: actually FIND a fix by browsing live. Ranks candidates, then
    for the top `max_pages` it surfaces `Browsing <url>…` to the user AND fetches the
    real page body (not just the search snippet), extracting commands/steps. Returns
    the richest extracted evidence (title + url + body + steps) for attribution, or
    None. Stops early once a content-rich page is found. Never raises."""
    try:
        candidates = search_runtime_issue_results(
            command=command, error_text=error_text, trace=None,
            execution_target=execution_target, os_hint=os_hint,
        )
    except Exception:
        candidates = ()
    if not candidates:
        return None
    best_evidence = ""
    best_score = 0
    for candidate in candidates[:max_pages]:
        url = getattr(candidate, "url", "") or ""
        if not url:
            continue
        if trace is not None:
            try:
                trace(f"Browsing {url} …")
            except Exception:
                pass
        summary = None
        try:
            summary = _fetch_web_reference_summary(getattr(candidate, "title", "") or url, url)
        except Exception:
            summary = None
        # Prefer the fetched page body; fall back to the search snippet.
        body = ""
        steps: tuple[str, ...] = ()
        title = getattr(candidate, "title", "") or url
        if summary is not None:
            title = getattr(summary, "title", title) or title
            body = getattr(summary, "excerpt", "") or ""
            steps = tuple(getattr(summary, "steps", ()) or ())
        if not body:
            body = getattr(candidate, "excerpt", "") or ""
        score = len(body) + 200 * len(steps)
        if score > best_score and body:
            best_score = score
            steps_text = ("\nSteps:\n- " + "\n- ".join(steps)) if steps else ""
            best_evidence = f"{title}\n{url}\n{body}{steps_text}"
            if trace is not None:
                try:
                    trace(f"Found a usable answer at {url}")
                except Exception:
                    pass
            if len(body) > 400 or steps:
                break  # content-rich enough — stop browsing
    return best_evidence.strip() or None


def _search_runtime_issue_candidates(query: str) -> tuple[WebSearchResult, ...]:
    """Search for a runtime issue and return ranked candidates."""

    cached = _search_result_cache.get(query)
    if cached is not None:
        return (cached,)
    ddg_candidates = _search_ddg_candidates(query)
    # Fall back to Bing on empty results AND on hard connection failures so we don't
    # silently report "could not reach DuckDuckGo" when Bing is reachable.
    if ddg_candidates:
        candidates = ddg_candidates
    else:
        candidates = _search_bing_candidates(query)
    if not candidates:
        return ()
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            _result_relevance_rank(candidate=candidate, query_keywords=_query_keywords(query)),
            _trusted_source_rank(candidate.url),
        ),
    )
    best = ranked[0]
    if len(_search_result_cache) >= _MAX_SEARCH_CACHE:
        try:
            del _search_result_cache[next(iter(_search_result_cache))]
        except (StopIteration, KeyError):
            pass
    _search_result_cache[query] = best
    return tuple(ranked[:5])


_last_search_diagnostic: dict[str, str] = {}


def _record_search_error(provider: str, reason: str) -> None:
    _last_search_diagnostic[provider] = reason


def last_search_diagnostic() -> dict[str, str]:
    """Return a copy of the last network-error reason per search provider (ddg/bing)."""
    return dict(_last_search_diagnostic)


def describe_search_failure() -> str:
    """Human-readable summary of why the most recent search round failed.

    Falls back to a generic message only when no diagnostic was recorded.
    """
    if not _last_search_diagnostic:
        return "Search did not reach a result; no network diagnostic was captured."
    parts: list[str] = []
    for provider, reason in _last_search_diagnostic.items():
        parts.append(f"{provider}: {reason}")
    return "; ".join(parts)


def _search_ddg_candidates(query: str) -> tuple[WebSearchResult, ...]:
    """Try DuckDuckGo HTML search and return candidates.

    Records a per-provider diagnostic on failure (DNS, timeout, HTTP code, OS error)
    so callers can render a specific reason instead of the generic 'could not reach
    DuckDuckGo' message.
    """
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    request = Request(search_url, headers=_BROWSER_HEADERS)
    body: str | None = None
    last_reason: str | None = None
    # One retry on transient timeouts/connection errors so a single dropped packet
    # doesn't silently fail the whole repair flow.
    for attempt in range(2):
        try:
            with urlopen(request, timeout=_SEARCH_TIMEOUT_SECONDS) as response:
                body = response.read(48000).decode("utf-8", errors="ignore")
            last_reason = None
            break
        except HTTPError as exc:
            last_reason = f"HTTP {exc.code}"
            break  # Retrying an HTTP status code rarely helps.
        except URLError as exc:
            reason_obj = getattr(exc, "reason", exc)
            last_reason = f"network: {reason_obj}"
        except TimeoutError as exc:
            last_reason = f"timeout: {exc}"
        except OSError as exc:
            last_reason = f"os: {exc}"
        if attempt == 0:
            continue
    if body is None:
        if last_reason is not None:
            _record_search_error("duckduckgo", last_reason)
        return ()
    _last_search_diagnostic.pop("duckduckgo", None)
    candidates: list[WebSearchResult] = []
    for match in _RESULT_LINK_PATTERN.finditer(body):
        title = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(match.group("title"))))
        url = _resolve_ddg_redirect(unescape(match.group("url")).strip())
        if not title or not url or not url.startswith("http"):
            continue
        snippet = _extract_ddg_snippet_near_match(body, match.end())
        excerpt = snippet or "Duckln found a likely public fix path for this blocker."
        candidates.append(WebSearchResult(title=title, url=url, excerpt=excerpt))
    if not candidates:
        return ()
    deduped: list[WebSearchResult] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        deduped.append(candidate)
        if len(deduped) >= 8:
            break
    return tuple(deduped)


def _search_bing_candidates(query: str) -> tuple[WebSearchResult, ...]:
    """Try Bing HTML search as a fallback when DuckDuckGo is unreachable."""
    search_url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en"
    request = Request(search_url, headers=_BROWSER_HEADERS)
    body: str | None = None
    last_reason: str | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=_SEARCH_TIMEOUT_SECONDS) as response:
                body = response.read(48000).decode("utf-8", errors="ignore")
            last_reason = None
            break
        except HTTPError as exc:
            last_reason = f"HTTP {exc.code}"
            break
        except URLError as exc:
            reason_obj = getattr(exc, "reason", exc)
            last_reason = f"network: {reason_obj}"
        except TimeoutError as exc:
            last_reason = f"timeout: {exc}"
        except OSError as exc:
            last_reason = f"os: {exc}"
        if attempt == 0:
            continue
    if body is None:
        if last_reason is not None:
            _record_search_error("bing", last_reason)
        return ()
    _last_search_diagnostic.pop("bing", None)
    candidates: list[WebSearchResult] = []
    for match in _BING_RESULT_PATTERN.finditer(body):
        title = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(match.group("title"))))
        url = unescape(match.group("url")).strip()
        if not title or not url or not url.startswith("http"):
            continue
        excerpt = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(match.group("snippet"))))
        excerpt = excerpt[:MAX_REMOTE_SUMMARY_CHARS].rstrip(" .,;:") or "Duckln found a likely public fix path for this blocker."
        candidates.append(WebSearchResult(title=title, url=url, excerpt=excerpt))
    if not candidates:
        return ()
    deduped: list[WebSearchResult] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        deduped.append(candidate)
        if len(deduped) >= 8:
            break
    return tuple(deduped)


def _extract_ddg_snippet_near_match(body: str, offset: int) -> str:
    """Extract a nearby DDG snippet for one result link match."""

    window = body[offset : offset + 2500]
    inner = _RESULT_SNIPPET_INNER_PATTERN.search(window)
    if inner is None:
        return ""
    snippet = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(inner.group("snippet"))))
    return snippet[:MAX_REMOTE_SUMMARY_CHARS].rstrip(" .,;:")


def _query_keywords(query: str) -> tuple[str, ...]:
    """Build meaningful tokens for result relevance scoring."""

    cleaned = query.replace('"', " ").casefold()
    raw_tokens = [token for token in _QUERY_TOKEN_SPLIT_PATTERN.split(cleaned) if token]
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "then",
        "check",
        "error",
        "failed",
        "issue",
        "github",
        "stackoverflow",
        "blog",
        "forum",
        "run",
    }
    tokens: list[str] = []
    for token in raw_tokens:
        if len(token) < 3:
            continue
        if token in stopwords:
            continue
        if token not in tokens:
            tokens.append(token)
    return tuple(tokens[:14])


def _result_relevance_rank(*, candidate: WebSearchResult, query_keywords: tuple[str, ...]) -> int:
    """Lower rank is better; rewards overlap with the exact captured error text."""

    if not query_keywords:
        return 1000
    haystack = f"{candidate.title} {candidate.excerpt} {candidate.url}".casefold()
    matched = 0
    for token in query_keywords:
        if token in haystack:
            matched += 1
    if matched == 0:
        return 1000
    coverage = int((matched * 100) / max(1, len(query_keywords)))
    return 100 - coverage


def _extract_candidate_commands(
    *,
    search_results: tuple[WebSearchResult, ...] | list[WebSearchResult],
    execution_target: str,
    os_hint: str | None,
) -> tuple[str, ...]:
    """Extract concrete command suggestions from search snippets with OS/layer guards."""

    commands: list[str] = []
    for result in search_results:
        text = f"{result.title}\n{result.excerpt}"
        for match in _SHELL_COMMAND_HINT_PATTERN.finditer(text):
            command_text = _normalize_excerpt(match.group(0)).strip("`'\" ")
            if not command_text:
                continue
            if not _looks_like_shell_command(command_text):
                continue
            if not _command_matches_runtime_layer(
                command_text=command_text,
                execution_target=execution_target,
                os_hint=os_hint,
            ):
                continue
            if command_text not in commands:
                commands.append(command_text)
            if len(commands) >= 5:
                return tuple(commands)
    return tuple(commands)


_TITLE_LIKE_PATTERN = re.compile(r"^(?:[A-Z][A-Za-z0-9]*\s+){2,}")
_SHELL_SYNTAX_TOKENS = ("--", " -", "/", "=", "|", ">", "&&", "$(", "${")


def _looks_like_shell_command(text: str) -> bool:
    """Reject prose/title strings that merely mention a tool name.

    A real command typically contains shell syntax (flags, paths, redirects)
    or follows a verb + arg shape; blog post titles rarely do."""
    stripped = text.strip()
    if not stripped:
        return False
    if "..." in stripped or "…" in stripped:
        return False
    if _TITLE_LIKE_PATTERN.match(stripped):
        return False
    if any(token in stripped for token in _SHELL_SYNTAX_TOKENS):
        return True
    parts = stripped.split()
    if len(parts) <= 3:
        return True
    if all(part[:1].isupper() for part in parts if part and part[0].isalpha()):
        return False
    return True


def _command_matches_runtime_layer(*, command_text: str, execution_target: str, os_hint: str | None) -> bool:
    lowered = command_text.casefold()
    os_text = (os_hint or platform.system()).casefold()
    if execution_target == "vm" and "multipass " in lowered:
        return False
    if "ubuntu" in os_text or "linux" in os_text or execution_target == "vm":
        if "brew " in lowered:
            return False
    if "darwin" in os_text or "mac" in os_text:
        if "apt " in lowered or "apt-get " in lowered or "snap " in lowered:
            return False
    return True


def _trusted_source_rank(url: str) -> int:
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    path = parsed.path.casefold()
    if "stackoverflow.com" in host or "serverfault.com" in host or "superuser.com" in host:
        return 0
    if "github.com" in host and any(segment in path for segment in ("/issues", "/discussions")):
        return 1
    if any(domain in host for domain in ("medium.com", "dev.to", "github.io", "discuss.", "forum.", "community.")):
        return 2
    if "github.com" in host:
        return 3
    if "docs." in host or "readthedocs.io" in host:
        return 4
    if any(token in host for token in _MARKETING_DOMAIN_HINTS):
        return 60
    for index, domain in enumerate(_TRUSTED_PUBLIC_DOMAINS, start=5):
        if domain in host:
            return index
    return 50


def _extract_html_title(body: str) -> str:
    match = _TITLE_PATTERN.search(body)
    if match is None:
        return ""
    return _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(match.group(1))))


_LIST_ITEM_PATTERN = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_CODE_BLOCK_PATTERN = re.compile(r"<code[^>]*>(.*?)</code>", re.IGNORECASE | re.DOTALL)
MAX_STEPS_EXTRACTED = 5


class _MarkdownExtractor(HTMLParser):
    """Plan 196 F10: convert an HTML page to clean Markdown so a local 9B parses code/commands
    without drowning in markup — `<pre>`/`<code>` → fenced ```` ``` ```` blocks (with a language
    hint from `class="language-*"`), `<h1>`-`<h6>` → `#`…, `<ul>`/`<ol>`/`<li>` → `- `/`1. `,
    `<p>`/`<br>` → line breaks; `<script>`/`<style>`/`<nav>`/`<footer>` dropped. Robust (stdlib,
    no dependency); never raises — the caller falls back to the plain-text excerpt on any error."""

    _SKIP = {"script", "style", "nav", "footer", "head", "noscript", "svg", "aside"}
    _HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        self._code_inline = 0
        self._fence_idx: int | None = None
        self._list_stack: list[list] = []  # each: [kind, counter]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "pre":
            self._out.append("\n```\n")
            self._fence_idx = len(self._out) - 1
            self._pre_depth += 1
        elif tag == "code":
            if self._pre_depth:
                lang = self._lang_from_attrs(attrs)
                if lang and self._fence_idx is not None:
                    self._out[self._fence_idx] = "\n```" + lang + "\n"
            else:
                self._out.append("`")
                self._code_inline += 1
        elif tag in self._HEADINGS:
            self._out.append("\n\n" + self._HEADINGS[tag] + " ")
        elif tag in ("p", "div", "section", "article", "br", "tr", "blockquote"):
            self._out.append("\n")
        elif tag == "ul":
            self._list_stack.append(["ul", 0])
        elif tag == "ol":
            self._list_stack.append(["ol", 0])
        elif tag == "li":
            self._out.append("\n")
            if self._list_stack:
                entry = self._list_stack[-1]
                if entry[0] == "ol":
                    entry[1] += 1
                    self._out.append(f"{entry[1]}. ")
                else:
                    self._out.append("- ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "pre":
            if self._pre_depth:
                self._pre_depth -= 1
                self._out.append("\n```\n")
                self._fence_idx = None
        elif tag == "code" and not self._pre_depth and self._code_inline:
            self._out.append("`")
            self._code_inline -= 1
        elif tag in self._HEADINGS:
            self._out.append("\n")
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
        elif tag == "p":
            self._out.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._out.append(data)

    @staticmethod
    def _lang_from_attrs(attrs) -> str:
        for key, value in attrs:
            if key and key.lower() == "class" and value:
                for token in value.split():
                    low = token.lower()
                    if low.startswith("language-"):
                        return low[len("language-"):]
                    if low.startswith("lang-"):
                        return low[len("lang-"):]
        return ""

    def get_markdown(self) -> str:
        text = "".join(self._out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_markdown(body: str) -> str:
    """F10: HTML → clean Markdown via the stdlib parser. Empty string on failure/empty."""
    parser = _MarkdownExtractor()
    parser.feed(body)
    parser.close()
    return parser.get_markdown()


def _extract_html_excerpt(body: str) -> str:
    # Plan 196 F10: prefer a structured Markdown rendering (fenced code blocks / headings /
    # lists) so the model reads commands cleanly; fall back to the plain tag-strip on any
    # parse error. Capped to MAX_REMOTE_SUMMARY_CHARS so the context window is protected.
    try:
        markdown = _html_to_markdown(body)
        if markdown:
            return markdown[:MAX_REMOTE_SUMMARY_CHARS].rstrip()
    except Exception:
        pass
    cleaned = _SCRIPT_PATTERN.sub(" ", body)
    text = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(cleaned)))
    return text[:MAX_REMOTE_SUMMARY_CHARS].rstrip(" .,;:")


def extract_steps_from_html(body: str, *, max_steps: int = MAX_STEPS_EXTRACTED) -> tuple[str, ...]:
    """Extract ordered steps or key commands from an HTML documentation page.

    Prefers <ol><li> numbered steps, falls back to <ul><li> items that look
    like install/run instructions, then to short <code> blocks."""
    cleaned = _SCRIPT_PATTERN.sub(" ", body)
    steps: list[str] = []
    for match in _LIST_ITEM_PATTERN.finditer(cleaned):
        text = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(match.group(1))))
        if text and len(text) > 5 and len(text) < 300:
            steps.append(text)
        if len(steps) >= max_steps:
            break
    if not steps:
        for match in _CODE_BLOCK_PATTERN.finditer(cleaned):
            text = _normalize_excerpt(_TAG_PATTERN.sub(" ", unescape(match.group(1)))).strip()
            if text and len(text) > 3 and len(text) < 200 and (" " in text or text.startswith(("pip", "npm", "python", "node", "docker", "git", "sudo", "apt", "brew"))):
                steps.append(text)
            if len(steps) >= max_steps:
                break
    return tuple(steps[:max_steps])


def _extract_text_excerpt(body: str) -> str:
    lines = [_normalize_excerpt(line) for line in body.splitlines()]
    compact = next((line for line in lines if line and not line.startswith(("#", "//", "/*"))), "")
    return compact[:MAX_REMOTE_SUMMARY_CHARS].rstrip(" .,;:")


def _normalize_excerpt(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _render_steps(steps: tuple[str, ...]) -> str:
    """Format extracted documentation steps as a compact inline string."""
    if not steps:
        return ""
    numbered = " ".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))
    return f" Steps: {numbered}"


def _resolve_ddg_redirect(url: str) -> str:
    """Extract the actual destination URL from a DuckDuckGo tracking redirect.

    DDG HTML search results use `//duckduckgo.com/l/?uddg=<url-encoded-target>&rut=...`
    as the href. This function decodes the real URL so users see the actual source."""
    if "duckduckgo.com/l/" not in url and not url.startswith("//duckduckgo.com"):
        return url
    idx = url.find("uddg=")
    if idx < 0:
        return url
    encoded = url[idx + 5:]
    end = encoded.find("&")
    if end >= 0:
        encoded = encoded[:end]
    decoded = unquote(encoded).strip()
    return decoded if decoded.startswith("http") else url


def _sanitize_error_lines(text: str | None) -> tuple[str, ...]:
    """Return error lines with shell prompts, Duckln narrative, and generic status noise removed."""

    if not text:
        return ()
    cleaned: list[str] = []
    for raw_line in str(text).splitlines():
        if not raw_line or not raw_line.strip():
            continue
        if is_shell_prompt_line(raw_line):
            continue
        if is_duckln_synthetic_line(raw_line):
            continue
        line = re.sub(r"\s+", " ", raw_line).strip(" .")
        if not line:
            continue
        lowered = line.casefold()
        if any(prefix in lowered for prefix in _GENERIC_STATUS_PREFIXES):
            continue
        if any(token in lowered for token in _NOISE_LINE_TOKENS):
            continue
        cleaned.append(line[:140])
    return tuple(cleaned)


def _build_search_query(*, command: str | None, error_text: str | None, os_hint: str | None = None) -> str:
    tokens: list[str] = []
    exact_error = _exact_error_query_fragment(error_text)
    if exact_error:
        tokens.append(f'"{exact_error}"')
    # Use sanitized lines for the fallback so shell prompts ("ubuntu@host:~$ …") and
    # Duckln-own narrative ("Specialist route: rust …") never leak into the query.
    sanitized_lines = _sanitize_error_lines(error_text) if not exact_error else ()
    fallback_error_text = " ".join(sanitized_lines) if sanitized_lines else ""
    sources = (command or "",) if exact_error else (fallback_error_text, command or "")
    for source in sources:
        cleaned = re.sub(r"\s+", " ", source).strip()
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    if not tokens:
        return ""
    resolved_os_hint = _default_os_hint(os_hint)
    if resolved_os_hint:
        tokens.append(f'"{resolved_os_hint}"')
    query = " ".join(tokens)
    if "error" not in query.lower() and "failed" not in query.lower():
        query = f"{query} error"
    suffix = " github issues stackoverflow blog forum"
    suffix_tokens = ("github", "stackoverflow", "blog", "forum")
    if not any(token in query.casefold() for token in suffix_tokens):
        query = f"{query[: max(24, MAX_SEARCH_QUERY_CHARS - len(suffix))].strip()} {suffix.strip()}"
    query = query[:MAX_SEARCH_QUERY_CHARS].strip()
    return query


def _default_os_hint(value: str | None) -> str:
    cleaned = str(value or "").strip()
    if cleaned:
        return cleaned
    system = platform.system().strip()
    if not system:
        return ""
    if system.lower() == "darwin":
        return "macOS"
    return system


def _exact_error_query_fragment(error_text: str | None) -> str:
    """Extract the most searchable exact terminal error line."""

    if not error_text:
        return ""
    sanitized = _sanitize_error_lines(error_text)
    lines = list(sanitized)
    if not lines:
        # Last resort: keep at least one collapsed line so callers get something — but
        # only from text that hasn't been entirely filtered (shell prompts, narrative).
        return ""
    priority_tokens = (
        "command not found",
        "no such file or directory",
        "no module named",
        "modulenotfounderror",
        "permission denied",
        "ebadengine",
        "unsupported engine",
        "requires node",
        "failed",
        "error",
        "exception",
        "traceback",
    )
    for line in lines:
        lowered = line.casefold()
        if any(token in lowered for token in priority_tokens):
            return line[:140].strip()
    return lines[0][:140].strip()


def _is_duckln_synthetic_search_line(line: str) -> bool:
    lowered = str(line or "").casefold()
    synthetic_markers = (
        "duckln classified",
        "specialist route:",
        "toolchain:",
        "supervisor agent does not have",
        "does not have a reliable run command",
        "duckln checked",
        "run openclaw",
    )
    return any(marker in lowered for marker in synthetic_markers)


def search_for_run_command_hint(
    repo_name: str,
    *,
    repo_url: str | None = None,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
) -> RuntimeRepairEvidence:
    """Search the web for how to run a specific repo when Duckln has no stored command.

    Uses a focused 'how to run/start/launch' query rather than the generic error-repair
    query so the results are about the entry-point, not a random dependency problem.
    Returns evidence with the result title, a ≤5-line excerpt, and the source URL."""
    slug = re.sub(r"[^A-Za-z0-9_.-]", " ", repo_name).strip()
    query = f"{slug} how to run start launch README github docs"
    if repo_url and "github.com" in repo_url:
        query = f"{slug} github README start command issues"
    query = query[:MAX_SEARCH_QUERY_CHARS].strip()
    if trace is not None:
        trace(
            render_tool_invocation_trace(
                title="run command discovery search",
                tool_id="web.documentation_lookup",
                action=f"Search for how to run {repo_name} — no stored entry-point found",
                detail_lines=(
                    f"Repo: {repo_name}",
                    f"Search query: {query}",
                    "Duckln is looking for the canonical start/run command across README, docs, GitHub issues, and other trusted public sources.",
                ),
                execution_target=execution_target,
                search_query=query,
            )
        )
    result = _search_runtime_issue(query)
    if result is None:
        return RuntimeRepairEvidence(note=None, source_urls=(), search_query=query)
    note = (
        f"Duckln searched for how to run {repo_name}: "
        f"{result.title} — {result.excerpt} "
        f"Source: [1] {result.url}"
    )
    return RuntimeRepairEvidence(
        note=note,
        source_urls=(result.url,),
        search_query=query,
    )


def build_repo_install_search_query(
    repo_name: str,
    *,
    repo_url: str | None = None,
    os_hint: str | None = None,
) -> str:
    """Build the direct fourth-pass query for repo installation on the target OS."""

    slug = re.sub(r"[^A-Za-z0-9_.-]", " ", repo_name).strip()
    parts = ["how to install", slug]
    if repo_url:
        parts.append(repo_url)
    if os_hint:
        parts.append(os_hint)
    query = " ".join(part for part in parts if part).strip()
    return query[:MAX_SEARCH_QUERY_CHARS].strip()


def search_for_repo_install_hint(
    repo_name: str,
    *,
    repo_url: str | None = None,
    trace: Callable[[str], None] | None = None,
    execution_target: str = "local",
    os_hint: str | None = None,
) -> RuntimeRepairEvidence:
    """Search directly for how to install a repo on the current VM/cloud/local OS.

    This is the fourth fallback after README, exact-error search, and run-command
    discovery. It is intentionally broader than documentation lookup so Duckln can
    find practical install notes while still surfacing sources before execution.
    """

    query = build_repo_install_search_query(repo_name, repo_url=repo_url, os_hint=os_hint or platform.system())
    if trace is not None:
        trace(
            render_tool_invocation_trace(
                title="direct repo install search",
                tool_id="web.documentation_lookup",
                action=f"Search how to install {repo_name} on the target OS",
                detail_lines=(
                    f"Repo: {repo_name}",
                    f"Repo URL: {repo_url or 'unknown'}",
                    f"Target OS: {os_hint or platform.system()}",
                    f"Search query: {query}",
                ),
                execution_target=execution_target,
                search_query=query,
            )
        )
    results = _search_runtime_issue_results((query,))
    if not results:
        return RuntimeRepairEvidence(note=None, source_urls=(), search_query=query)
    commands = _extract_candidate_commands(
        search_results=results,
        execution_target=execution_target,
        os_hint=os_hint,
    )
    command_text = (
        " Suggested commands: " + " | ".join(f"`{command}`" for command in commands[:3])
        if commands
        else ""
    )
    top = results[0]
    urls = tuple(result.url for result in results[:5])
    note = (
        f"Duckln searched directly for how to install {repo_name} on {os_hint or platform.system()}: "
        f"{top.title} — {top.excerpt} Source: [1] {top.url}.{command_text}"
    )
    return RuntimeRepairEvidence(note=note, source_urls=urls, search_query=query)
