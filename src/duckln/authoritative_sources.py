"""Plan 61 Fix G: authoritative-source consultation for unknown tools.

When the code-block README scanner registers an unknown tool (e.g. bazel,
deno, bun) — one the heuristic + LLM allow-list can't auto-install — Duckln
does ONE bounded lookup against a strict allow-list of authoritative
publishers, then surfaces an actionable guidance message to the user
("install from <official-url>; retry after install").

Repo-agnostic; never executes the install itself.
"""

from __future__ import annotations

from pathlib import Path
import re


# Allow-list of authoritative publishers. Curated; strict matching. Subdomains
# of these hosts are accepted (e.g. `learn.microsoft.com` matches the entry
# `microsoft.com`). github.com is included but only when the path is `<org>/<tool>`.
_AUTHORITATIVE_PUBLISHERS: tuple[str, ...] = (
    "docs.python.org",
    "nodejs.org",
    "developer.mozilla.org",
    "docs.rust-lang.org",
    "doc.rust-lang.org",
    "rust-lang.org",
    "go.dev",
    "docs.docker.com",
    "kubernetes.io",
    "bazel.build",
    "deno.land",
    "deno.com",
    "bun.sh",
    "rubyonrails.org",
    "developer.apple.com",
    "learn.microsoft.com",
    "microsoft.com",
    "cloud.google.com",
    "aws.amazon.com",
    # Plan 196 F11: vendor DevOps / cloud / container DOCS domains, so recovery can read
    # official Multipass / Ubuntu / AWS / GCP / Docker / GitHub reference material. These
    # are the DOCS/vendor hosts (the stated intent) — the bare retail/marketing roots
    # amazon.com / google.com / docker.com are DELIBERATELY excluded (they serve retail /
    # search / marketing, not engineering reference). `is_authoritative_url` matches
    # subdomains via endswith, so `canonical.com` covers `multipass.run`-style doc paths
    # under canonical.com, `ubuntu.com` covers `documentation.ubuntu.com`, `aws.amazon.com`
    # already covers `docs.aws.amazon.com`, and `cloud.google.com` covers `docs.cloud.google.com`.
    "canonical.com",
    "ubuntu.com",
    "github.com",
    "docs.astral.sh",
    "astral.sh",
    "python-poetry.org",
    "yarnpkg.com",
    "pnpm.io",
    "cmake.org",
    "ffmpeg.org",
    "ollama.com",
    "huggingface.co",
    "pytorch.org",
    "tensorflow.org",
)


def is_authoritative_url(url: str) -> bool:
    """Return True when the URL's host is in the allow-list."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for trusted in _AUTHORITATIVE_PUBLISHERS:
        if host == trusted or host.endswith("." + trusted):
            return True
    return False


def consult_authoritative_source(
    *,
    tool_name: str,
    config_dir: Path | None,
    timeout_seconds: float = 5.0,
) -> str | None:
    """Look up the official install URL for an unknown tool. Returns the URL
    string, or None when no allow-listed result is found (or no internet).

    Bounded: single search, 5s timeout. Results cached in the skill memory
    so re-asking within 24h skips the network call.
    """
    if not tool_name or not tool_name.strip():
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", tool_name).strip("-").lower()[:60] or "unknown"

    cached = _read_authoritative_cache(config_dir, slug=slug) if config_dir else None
    if cached is not None:
        return cached or None

    found = _search_for_authoritative_url(tool_name=tool_name, timeout=timeout_seconds)
    if config_dir is not None:
        _write_authoritative_cache(config_dir, slug=slug, url=found or "")
    return found


def _search_for_authoritative_url(*, tool_name: str, timeout: float) -> str | None:
    """Do a single internet search and return the first URL whose host is in
    _AUTHORITATIVE_PUBLISHERS. None when nothing matches.
    """
    try:
        from duckln.internet_skill import internet_search_summary
    except Exception:
        return None
    query = f"{tool_name} install official documentation"
    try:
        summary = internet_search_summary(
            query=query,
            max_results=5,
            timeout_seconds=timeout,
        )
    except Exception:
        return None
    if not summary:
        return None
    # internet_search_summary returns a text blob with URLs interleaved.
    # Extract candidate URLs and pick the first authoritative one.
    urls = re.findall(r"https?://[^\s)\]\"'>,]+", summary)
    for url in urls:
        if is_authoritative_url(url):
            # Strip trailing punctuation noise.
            cleaned = url.rstrip(".,;:!?")
            return cleaned
    return None


def format_user_guidance(*, tool_name: str, url: str | None) -> str:
    """Build the user-facing one-liner guidance message."""
    if url:
        return (
            f"Duckln doesn't auto-install `{tool_name}` yet. Official source: {url}. "
            f"Install it manually, then retry the setup — Duckln will detect "
            f"`{tool_name}` on PATH and continue."
        )
    return (
        f"Duckln doesn't auto-install `{tool_name}` and couldn't find an "
        f"official source via authoritative search. Look up the install "
        f"instructions for `{tool_name}` manually, then retry the setup."
    )


# --- Cache helpers (24h freshness) ---------------------------------------------


def _read_authoritative_cache(config_dir: Path, *, slug: str) -> str | None:
    """Return the cached authoritative URL for `slug` if fresher than 24h.
    Returns "" (empty string) when previous lookup found nothing — still
    cached so we don't re-search within 24h. Returns None when no cache."""
    import json
    import time as _time
    try:
        from agent.memory import AGENT_MEMORY_DIR_NAME, SKILLS_DIR_NAME
    except Exception:
        return None
    cache_file = (
        Path(config_dir)
        / AGENT_MEMORY_DIR_NAME
        / SKILLS_DIR_NAME
        / f"authoritative-{slug}.md"
    )
    if not cache_file.exists():
        return None
    try:
        text = cache_file.read_text(encoding="utf-8")
    except OSError:
        return None
    start = text.find("```json")
    after = text.find("\n", start) if start >= 0 else -1
    end = text.find("```", after + 1) if after > 0 else -1
    if start < 0 or after < 0 or end < 0:
        return None
    try:
        payload = json.loads(text[after + 1:end].strip())
    except (json.JSONDecodeError, ValueError):
        return None
    cached_at = str(payload.get("cached_at", "")).strip()
    if not cached_at:
        return None
    try:
        struct = _time.strptime(cached_at, "%Y-%m-%dT%H:%M:%SZ")
        epoch = _time.mktime(struct) - _time.timezone
    except (ValueError, OverflowError):
        return None
    if (_time.time() - epoch) > (24 * 3600):
        return None
    url = payload.get("url")
    if isinstance(url, str):
        return url
    return None


def _write_authoritative_cache(config_dir: Path, *, slug: str, url: str) -> None:
    """Cache the authoritative URL lookup result for `slug`. `url` can be ""
    (negative cache — we already searched and found nothing)."""
    import json
    import time as _time
    try:
        from agent.memory import AGENT_MEMORY_DIR_NAME, SKILLS_DIR_NAME
    except Exception:
        return
    try:
        skills_dir = Path(config_dir) / AGENT_MEMORY_DIR_NAME / SKILLS_DIR_NAME
        skills_dir.mkdir(parents=True, exist_ok=True)
        cache_file = skills_dir / f"authoritative-{slug}.md"
        payload = {
            "tool": slug,
            "url": url,
            "cached_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        }
        content = (
            f"# Authoritative-source cache for {slug}\n\n"
            "Auto-generated. Delete to force re-lookup.\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```\n"
        )
        cache_file.write_text(content, encoding="utf-8")
    except Exception:
        pass
