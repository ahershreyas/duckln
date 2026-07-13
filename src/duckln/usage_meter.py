"""Session-scoped LLM usage tracking for Duckln."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import threading
from typing import Any

from state.store import initialize_state_store


@dataclass(frozen=True)
class UsageSnapshot:
    """Current in-process token and cost totals.

    `prompt/completion/total_tokens` are CUMULATIVE for the session. The `last_*` fields and
    `context_window` (Plan 164) describe the MOST RECENT call so the UI can show how many
    tokens each call sends and how full the model's context is — `last_estimated_prompt_tokens`
    is set BEFORE the call (chars/4 estimate, survives a failed/overflowed call); the exact
    `last_*` counts come from the provider's response and supersede the estimate."""

    provider: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    turn_count: int = 0
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    last_estimated_prompt_tokens: int = 0
    context_window: int = 0
    # Plan 167 F2: True once the last call's prompt count is the provider's EXACT usage;
    # False while it's only the pre-call chars/4 estimate (e.g. a failed/429 call).
    last_prompt_is_exact: bool = False


_LOCK = threading.Lock()
_CURRENT = UsageSnapshot()


def reset_usage_snapshot(*, config_dir: Path | None = None) -> None:
    """Reset session usage counters."""

    global _CURRENT
    with _LOCK:
        _CURRENT = UsageSnapshot()
    if config_dir is not None:
        initialize_state_store(config_dir).clear_usage_snapshot()


def current_usage_snapshot() -> UsageSnapshot:
    """Return the current in-memory usage totals."""

    with _LOCK:
        return _CURRENT


def record_call_estimate(*, estimated_prompt_tokens: int, context_window: int = 0) -> UsageSnapshot:
    """Plan 164 F1: record the ESTIMATED prompt size + the model's context window just
    BEFORE an LLM call, so the UI can show "↑~N tokens · ctx ~X%" even if the call then
    fails/overflows (the exact counts from `record_provider_usage` supersede this)."""

    global _CURRENT
    with _LOCK:
        # Plan 167 F1: a NEW call starts — clear the previous call's exact per-call counts so a
        # failed/429 call shows THIS attempt's estimate (↑~N · ↓0), never the prior call's values.
        _CURRENT = replace(
            _CURRENT,
            last_estimated_prompt_tokens=max(0, int(estimated_prompt_tokens)),
            context_window=max(0, int(context_window)),
            last_prompt_tokens=0,
            last_completion_tokens=0,
            last_total_tokens=0,
            last_prompt_is_exact=False,
        )
        return _CURRENT


def near_context_limit(snapshot: UsageSnapshot, *, threshold: float = 0.9) -> bool:
    """Plan 164 F5: True when the most recent prompt (exact if known, else the estimate)
    is at/over `threshold` of the model's context window. False when the window is unknown."""

    window = snapshot.context_window
    if window <= 0:
        return False
    used = snapshot.last_prompt_tokens or snapshot.last_estimated_prompt_tokens
    return used >= threshold * window


def context_overflow_hint(snapshot: UsageSnapshot | None = None, *, threshold: float = 0.9) -> str:
    """Plan 164 F5: an honest one-line hint when the last prompt is near/over the model's
    context — so a failure reads as a SIZE problem, not a false "can't connect". Empty string
    when the context window is unknown or the prompt is comfortably within it."""
    snap = snapshot or current_usage_snapshot()
    if not near_context_limit(snap, threshold=threshold):
        return ""
    used = snap.last_prompt_tokens or snap.last_estimated_prompt_tokens
    return (
        f"The prompt (~{used} tokens) is at/over this model's ~{snap.context_window}-token "
        "context, so it likely overflowed — use a larger-context model or reduce scope."
    )


def record_provider_usage(
    *,
    provider: str,
    model: str,
    payload: dict[str, Any],
    config_dir: Path | None = None,
) -> UsageSnapshot:
    """Extract provider usage fields and accumulate them into the current session."""

    usage = _extract_usage(provider=provider, payload=payload)
    if usage.total_tokens <= 0 and usage.estimated_cost_usd is None:
        return current_usage_snapshot()

    global _CURRENT
    with _LOCK:
        accumulated_cost = _combine_costs(_CURRENT.estimated_cost_usd, usage.estimated_cost_usd)
        _CURRENT = UsageSnapshot(
            provider=provider,
            model=model,
            prompt_tokens=_CURRENT.prompt_tokens + usage.prompt_tokens,
            completion_tokens=_CURRENT.completion_tokens + usage.completion_tokens,
            total_tokens=_CURRENT.total_tokens + usage.total_tokens,
            estimated_cost_usd=accumulated_cost,
            turn_count=_CURRENT.turn_count + 1,
            # Plan 164/167: exact per-call counts from the response supersede the estimate;
            # mark them exact and keep the context window set by record_call_estimate.
            last_prompt_tokens=usage.prompt_tokens or _CURRENT.last_estimated_prompt_tokens,
            last_completion_tokens=usage.completion_tokens,
            last_total_tokens=usage.total_tokens,
            last_estimated_prompt_tokens=_CURRENT.last_estimated_prompt_tokens,
            context_window=_CURRENT.context_window,
            last_prompt_is_exact=bool(usage.prompt_tokens > 0),
        )
        snapshot = _CURRENT

    if config_dir is not None:
        initialize_state_store(config_dir).upsert_usage_snapshot(
            scope_key="session.current",
            provider=provider,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
            turn_count_increment=1,
            metadata={"provider_usage": True},
        )
    return snapshot


def _combine_costs(left: float | None, right: float | None) -> float | None:
    if left is None and right is None:
        return None
    if left is None:
        return float(right or 0.0)
    if right is None:
        return float(left)
    return float(left) + float(right)


def _extract_usage(*, provider: str, payload: dict[str, Any]) -> UsageSnapshot:
    usage = payload.get("usage")
    lowered_provider = provider.lower()
    if isinstance(usage, dict):
        if lowered_provider == "anthropic":
            prompt_tokens = _safe_int(usage.get("input_tokens"))
            completion_tokens = _safe_int(usage.get("output_tokens"))
            total_tokens = prompt_tokens + completion_tokens
            return UsageSnapshot(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=_safe_float(usage.get("estimated_cost_usd") or usage.get("cost_usd")),
            )
        prompt_tokens = _safe_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion_tokens = _safe_int(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("generated_tokens")
        )
        total_tokens = _safe_int(usage.get("total_tokens")) or (prompt_tokens + completion_tokens)
        return UsageSnapshot(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=_safe_float(usage.get("estimated_cost_usd") or usage.get("cost_usd")),
        )

    if lowered_provider == "ollama":
        prompt_tokens = _safe_int(payload.get("prompt_eval_count"))
        completion_tokens = _safe_int(payload.get("eval_count"))
        total_tokens = prompt_tokens + completion_tokens
        return UsageSnapshot(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    return UsageSnapshot()


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
