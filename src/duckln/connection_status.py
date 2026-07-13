"""Connection-status probes for the live header indicators.

Three independent dots:
- **Provider/AI**:  green when the configured LLM provider is reachable AND the
  selected model is available; red when offline or auth fails.
- **Execution target**: green when the active target (Local / VM / AWS / GCP)
  is reachable; orange when a check is in-flight or auth is partial; red when
  unreachable.
- **Internet**: green when DuckDuckGo's HTTP endpoint responds; red otherwise.

All probes are bounded (≤2s default), cached with a 30s TTL, and safe to call
on every UI render — the underlying network call only happens when the cache
expires.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ConnectionStatus = Literal["green", "orange", "red"]


# Markup constants for the three dots. The textual UI renders rich markup, so
# the colored bullet shows up directly in the header string.
_DOT_GREEN = "[green]●[/green]"
_DOT_ORANGE = "[orange1]●[/orange1]"
_DOT_RED = "[red]●[/red]"

# Plain-text fallback (used in CLI/non-textual contexts).
_DOT_GREEN_PLAIN = "●"
_DOT_ORANGE_PLAIN = "●"
_DOT_RED_PLAIN = "●"


def dot_for(status: ConnectionStatus, *, plain: bool = False) -> str:
    """Return the rich-markup (or plain) dot for a status."""
    if plain:
        return {
            "green": _DOT_GREEN_PLAIN,
            "orange": _DOT_ORANGE_PLAIN,
            "red": _DOT_RED_PLAIN,
        }.get(status, _DOT_RED_PLAIN)
    return {
        "green": _DOT_GREEN,
        "orange": _DOT_ORANGE,
        "red": _DOT_RED,
    }.get(status, _DOT_RED)


def status_text_segment(status: ConnectionStatus) -> tuple[str, str]:
    """Plan 63 Fix 1: return ``(glyph, rich-style)`` for a status dot.

    Callers build a Rich ``Text`` object via ``text.append(glyph, style)``
    instead of emitting bracket-markup, which avoids the literal-text bug
    where ``[red]●[/red]`` printed as-is in the header.

    Uses the bundled Nerd Font solid-circle codepoint (DOT_SOLID, U+F111).
    """
    # Lazy import keeps connection_status free of nerdfonts dependency at
    # import time (helpful in tests that pre-mock the glyph module).
    from duckln.glyphs import DOT_SOLID
    style = {
        "green": "green",
        "orange": "orange1",  # 256-color amber; Rich falls back to yellow on basic ANSI
        "red": "red",
    }.get(status, "red")
    return DOT_SOLID, style


@dataclass
class _CachedStatus:
    value: ConnectionStatus
    cached_at: float


_CACHE_TTL_SECONDS = 30.0
_PROBE_TIMEOUT_SECONDS = 2.0

_status_cache: dict[str, _CachedStatus] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> ConnectionStatus | None:
    with _cache_lock:
        rec = _status_cache.get(key)
        if rec is None:
            return None
        if (time.monotonic() - rec.cached_at) > _CACHE_TTL_SECONDS:
            return None
        return rec.value


def _cache_put(key: str, value: ConnectionStatus) -> None:
    with _cache_lock:
        _status_cache[key] = _CachedStatus(value=value, cached_at=time.monotonic())


def clear_status_cache() -> None:
    """Plan 186 F2b: drop cached probe verdicts so a fresh probe re-checks connectivity
    immediately (e.g. right after auto-starting the local Ollama runtime at startup)."""
    with _cache_lock:
        _status_cache.clear()


def clear_cache() -> None:
    """Reset the in-memory probe cache. Test-only helper."""
    with _cache_lock:
        _status_cache.clear()


# --- Internet ----------------------------------------------------------------


def probe_internet_status(*, timeout: float = _PROBE_TIMEOUT_SECONDS) -> ConnectionStatus:
    """Quick TCP probe to a well-known public host. Green when reachable,
    red on timeout/exception. Cached for 30s."""
    cached = _cache_get("internet")
    if cached is not None:
        return cached
    result: ConnectionStatus = "red"
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 443)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                result = "green"
                break
        except (socket.timeout, OSError):
            continue
    _cache_put("internet", result)
    return result


# --- Provider / AI -----------------------------------------------------------


def _provider_model_key(provider, model) -> str:
    return f"{getattr(provider, 'value', provider) or '?'}:{model or '?'}"


def record_live_verified(config_dir, *, provider, model) -> None:
    """Plan 182 F4: persist that THIS exact (provider, model) actually returned a live reply
    (a real test-message round-trip succeeded). The header reads this so it shows GREEN only when
    the model truly answered — never on mere reachability/listing (no fake green)."""
    try:
        from state.access import write_config_snapshot

        write_config_snapshot(config_dir, {"live_verified": _provider_model_key(provider, model)})
    except Exception:
        pass


def read_live_verified(config_dir) -> str:
    try:
        from state.access import read_config_snapshot

        return str(read_config_snapshot(config_dir).get("live_verified") or "")
    except Exception:
        return ""


def probe_provider_status(
    config,
    *,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
    config_dir: Path | None = None,
) -> ConnectionStatus:
    """Probe whether the configured LLM provider responds. Green when the
    provider's base URL accepts a TCP connection AND the configured model
    is in the provider's listed models. Red on any failure. Cached 30s.

    `config` is an AppConfig-shaped object with provider/api_key/base_url/model.
    """
    if config is None:
        return "red"
    # Plan 121: include api-key presence + base_url in the cache key so a credential
    # change (e.g. onboarding entering the key) busts a stale RED cached before it
    # landed, instead of waiting out the 30s TTL.
    key = (
        f"provider:{getattr(config, 'provider', '?')}:{getattr(config, 'model', '?')}"
        f":{bool(getattr(config, 'api_key', None))}:{getattr(config, 'base_url', None) or ''}"
        f":{'cd' if config_dir is not None else ''}"
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached
    status: ConnectionStatus = "red"
    try:
        from duckln.ai_client import get_provider_adapter_for_base_url
        adapter = None
        try:
            adapter = get_provider_adapter_for_base_url(
                provider=config.provider,
                base_url=getattr(config, "base_url", None),
            )
        except Exception:
            adapter = None
        if adapter is None:
            _cache_put(key, "red")
            return "red"
        # Fast pre-check: is the provider's host reachable on its port? This
        # avoids waiting for a full HTTPS handshake when the network is down.
        # Plan 121: non-Ollama providers store base_url=None (they use the
        # adapter's built-in URL), so fall back to the adapter's base_url — else
        # `_host_reachable("")` returned False and the dot was a permanent false-RED
        # for OpenAI/OpenRouter/Anthropic even when connected.
        base = getattr(config, "base_url", None) or getattr(adapter, "base_url", "") or ""
        if not _host_reachable(base, timeout=timeout):
            _cache_put(key, "red")
            return "red"
        # Plan 119: judge the dot by TRUE provider connectivity — the same signal
        # /healthcheck uses (validate_api_key → list_models reachable) — NOT model
        # membership. validate_model required the exact model id to appear in the
        # provider's listed set, which produced false-RED for connected providers
        # whose newest/free model ids aren't enumerated (e.g. gpt-5-mini). Whether
        # a chosen model is valid surfaces at call time / in /healthcheck, not as a
        # permanent red header.
        try:
            result = adapter.validate_api_key(getattr(config, "api_key", None))
            reachable_ok = bool(getattr(result, "ok", False))
        except Exception:
            reachable_ok = False
        if not reachable_ok:
            status = "red"
        elif config_dir is not None:
            # Plan 182 F4: the HEADER (which passes config_dir) shows GREEN only when this exact
            # (provider, model) actually returned a live reply — recorded by verify_live_reply on a
            # real test message (onboarding / /provider / /model). Reachable-but-unverified (e.g.
            # listed-but-can't-generate, or a model changed without a live check) → orange, not a
            # fake green. Callers without config_dir keep the reachability signal (unchanged).
            verified = read_live_verified(config_dir) == _provider_model_key(
                getattr(config, "provider", None), getattr(config, "model", "")
            )
            status = "green" if verified else "orange"
        else:
            status = "green"
    except Exception:
        status = "red"
    _cache_put(key, status)
    return status


@dataclass(frozen=True)
class ProviderReadiness:
    """Plan 119: the result of the mandatory pre-flight LLM connectivity check."""
    connected: bool
    message: str


def ensure_provider_connected(config, *, timeout: float = _PROBE_TIMEOUT_SECONDS, config_dir: Path | None = None) -> ProviderReadiness:
    """Plan 119: mandatory pre-flight before any LLM-dependent action.

    Reuses the cached truthful probe (``probe_provider_status``) so a genuinely
    connected provider is NEVER blocked, and a real disconnect stops work with a
    helpful, actionable message instead of silently planning/executing offline.
    """
    # Plan 182 F5: a clear "select a provider and model" message with the exact slash hints,
    # so trying to run a repo without a working model isn't a vague offline stop.
    _SELECT = (
        "No working model is connected. Pick a provider and model first — run `/provider` to "
        "choose a provider and enter a key, then `/model` to pick the model."
    )
    if config is None or not getattr(config, "model", None):
        return ProviderReadiness(connected=False, message=_SELECT)
    # Gate actions on REACHABILITY (not the header's strict live-verified green): a reachable
    # but not-yet-live-verified provider (orange) is allowed to proceed — the bring-up's own model
    # checks honest-stop downstream. Only a genuinely unreachable/unconfigured provider (red) blocks.
    status = probe_provider_status(config, timeout=timeout)
    if status != "red":
        return ProviderReadiness(connected=True, message="")
    provider = getattr(getattr(config, "provider", None), "value", None) or getattr(config, "provider", None) or "your provider"
    return ProviderReadiness(
        connected=False,
        message=(
            f"{provider} isn't responding with a live reply. {_SELECT} "
            "(Run `/healthcheck` to diagnose, or `/provider` to re-enter the key / base URL.)"
        ),
    )


def _host_reachable(base_url: str, *, timeout: float) -> bool:
    """Quick TCP probe to the host extracted from base_url. Returns False on
    malformed URLs (assume unreachable rather than raising)."""
    if not base_url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
    except Exception:
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


# --- Execution target (Local / VM / AWS / GCP) -------------------------------


def probe_target_status(
    target: str,
    *,
    config_dir: Path | None = None,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> ConnectionStatus:
    """Probe whether the active execution target is healthy.

    - `local` — always green (we're running locally).
    - `vm` — green when `multipass info` lists the active VM; orange if multipass
      is present but VM is starting; red when multipass missing/unreachable.
    - `aws` / `gcp` — green when `aws sts get-caller-identity` / `gcloud auth list`
      returns success quickly; orange when CLI is present but auth incomplete;
      red when CLI missing.
    - `docker` / `ssh` — green when the binary is on PATH (best-effort).

    Cached 30s.
    """
    normalized = (target or "local").strip().lower() or "local"
    if normalized == "local":
        return "green"
    key = f"target:{normalized}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    status: ConnectionStatus = "red"
    if normalized == "vm":
        status = _probe_multipass(timeout=timeout)
    elif normalized == "aws":
        status = _probe_aws(timeout=timeout)
    elif normalized == "gcp":
        status = _probe_gcp(timeout=timeout)
    elif normalized in {"docker", "ssh"}:
        status = "green" if _binary_on_path(normalized) else "red"
    _cache_put(key, status)
    return status


def _probe_multipass(*, timeout: float) -> ConnectionStatus:
    import shutil
    import subprocess
    if shutil.which("multipass") is None:
        return "red"
    try:
        result = subprocess.run(
            ["multipass", "list", "--format", "csv"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "orange"
    if result.returncode != 0:
        return "red"
    out = (result.stdout or "").lower()
    if "running" in out:
        return "green"
    if "starting" in out or "restarting" in out:
        return "orange"
    return "green"  # CLI works, no VMs yet — still considered reachable


def _probe_aws(*, timeout: float) -> ConnectionStatus:
    import shutil
    import subprocess
    if shutil.which("aws") is None:
        return "red"
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "orange"
    return "green" if result.returncode == 0 else "orange"


def _probe_gcp(*, timeout: float) -> ConnectionStatus:
    import shutil
    import subprocess
    if shutil.which("gcloud") is None:
        return "red"
    try:
        result = subprocess.run(
            ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "orange"
    if result.returncode != 0:
        return "orange"
    return "green" if (result.stdout or "").strip() else "orange"


def _binary_on_path(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


# --- Startup summary ---------------------------------------------------------


def startup_offline_notice(*, config) -> str | None:
    """If the configured provider isn't reachable, return a one-line user notice
    suitable for display in the activity feed at startup. None when everything
    looks healthy or when the check is inconclusive.
    """
    if config is None:
        return None
    status = probe_provider_status(config)
    if status == "green":
        return None
    provider = getattr(config, "provider", None)
    provider_value = provider.value if hasattr(provider, "value") else str(provider or "")
    provider_name = provider_value or "your provider"
    model = getattr(config, "model", "") or "the model"
    base = getattr(config, "base_url", "") or ""
    if status == "red":
        # Plan 186 F2a: plain, guiding language — no "replies that need the model will fail" jargon.
        if provider_value.lower() == "ollama":
            return (
                f"Ollama isn't running yet, so Duckln can't reach {model}. "
                f"I'll try to start it for you — or run `/provider` to reconnect."
            )
        return (
            f"Duckln can't reach {provider_name} right now"
            + (f" at {base}" if base else "")
            + ". Check your network and API key, then I'll reconnect — "
            "run `/healthcheck` if it keeps failing."
        )
    return None
