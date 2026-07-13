"""Plan 104 (Plan 103 Phase 1) — context engineering for the harness loop.

Real agents fail when the context window is managed by blunt truncation. This
module provides:

- a token ESTIMATOR (heuristic ≈ chars/4) and a model→context-window map, so
  budgets are token/model-aware rather than a fixed char cap;
- observation COMPACTION: keep the most recent observations full-fidelity (the
  "live window") and fold the older ones into a single rolling summary that
  preserves the decisions/paths that matter — so the agent keeps long-horizon
  context without blowing the window.

Pure + dependency-free; an optional ``summarizer`` callable (wrapping the LLM)
can produce a higher-quality rolling summary, with a deterministic fallback so
everything is testable without a model.
"""

from __future__ import annotations

from typing import Callable


def estimate_tokens(text: str) -> int:
    """Cheap, provider-agnostic token estimate (≈ 4 chars/token). Good enough for
    budgeting without a tokenizer dependency."""
    return max(1, (len(text or "") + 3) // 4)


import re as _re

_PATHY_RUN = _re.compile(r"[/._\-]")


def budget_tokens(text: str) -> int:
    """Plan 194 F2 (§5 guardrail): a code/path-AWARE token estimate for budget-critical trims.
    The plain chars/4 heuristic under-counts code and file paths (each `/`, `.`, `_`, `-` tends to
    start its own token). This adds one token per such separator on top of the chars/4 baseline, so
    a path-heavy trajectory isn't silently under-budgeted. Still tokenizer-free (model-agnostic)."""
    s = str(text or "")
    if not s:
        return 1
    base = (len(s) + 3) // 4
    separators = len(_PATHY_RUN.findall(s))
    return max(1, base + separators)


# Conservative context windows by model-id substring (tokens). Output headroom is
# reserved by the caller via `usable_context_tokens`.
_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude", 200_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("o1", 128_000),
    ("gpt-4", 128_000),
    ("gpt-3.5", 16_000),
    ("llama3", 8_192),
    ("llama", 8_192),
    ("qwen", 32_000),
    ("mistral", 32_000),
    ("gemma2", 8_192),
    ("gemma", 8_192),
    ("phi", 16_000),
)
_DEFAULT_WINDOW = 8_192


def context_window_for(model_id: str | None) -> int:
    low = str(model_id or "").lower()
    for needle, window in _MODEL_CONTEXT_WINDOWS:
        if needle in low:
            return window
    return _DEFAULT_WINDOW


def usable_context_tokens(model_id: str | None, *, output_headroom: int = 1024, safety: float = 0.5) -> int:
    """Tokens available for the prompt: a fraction of the window minus output
    headroom. Conservative (0.5) so we never crowd the model."""
    window = context_window_for(model_id)
    return max(1024, int(window * safety) - output_headroom)


def _gist(body: str, *, limit: int = 90) -> str:
    """Plan 194 F3: a one-line compact reference for a tool result (first meaningful line + size),
    used to MASK a result once it's no longer the most-recently-relevant one (Anthropic tool-result
    clearing) — so verbose payloads don't crowd the live context."""
    text = str(body or "").strip()
    if not text:
        return "(empty)"
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), text)
    gist = first[:limit]
    if len(text) > limit:
        gist += f" …(+{len(text) - limit}B)"
    return gist


def _observation_line(obs, *, full: bool = True) -> str:
    import json

    args = json.dumps(getattr(obs, "args", {}) or {}, default=str)[:120]
    if getattr(obs, "ok", False):
        raw = json.dumps(getattr(obs, "payload", None), default=str)
        # Plan 194 F3: keep the newest result verbatim (bounded); MASK older ones to a compact ref.
        body = raw[:300] if full else _gist(raw)
    else:
        body = f"ERROR {getattr(obs, 'error_code', '')}: {getattr(obs, 'error_message', '')}"
        if not full:
            body = _gist(body)
    return f"- turn {getattr(obs, 'turn', '?')} {getattr(obs, 'tool', '?')}({args}): {body}"


def _deterministic_summary(observations) -> str:
    """A compact, model-free rolling summary of older observations: one line each,
    preserving tool + target + ok/err so decisions and file paths survive."""
    lines = []
    for obs in observations:
        args = getattr(obs, "args", {}) or {}
        target = args.get("path") or args.get("command") or args.get("query") or ""
        status = "ok" if getattr(obs, "ok", False) else f"err:{getattr(obs, 'error_code', '')}"
        lines.append(f"  · {getattr(obs, 'tool', '?')} {str(target)[:60]} → {status}")
    return "Earlier findings (compacted):\n" + "\n".join(lines)


def map_reduce_summarize(
    text: str,
    *,
    query: str = "",
    chunk_chars: int = 6000,
    summarizer: Callable[[str], str] | None = None,
    max_chunks: int = 12,
) -> str:
    """Plan 106: reason over a LARGE file/result by chunking it, summarizing each
    chunk (map), then combining (reduce) — so the agent isn't limited to the first
    window. With no `summarizer`, falls back to a deterministic head/tail-per-chunk
    extract (keyword-biased toward `query`)."""
    text = text or ""
    if len(text) <= chunk_chars:
        return text
    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)][:max_chunks]

    def _map(chunk: str) -> str:
        if summarizer is not None:
            try:
                s = summarizer(chunk)
                if s and s.strip():
                    return s.strip()
            except Exception:
                pass
        # deterministic fallback: keep lines mentioning the query + head/tail.
        lines = chunk.splitlines()
        q_terms = [w for w in (query or "").lower().split() if len(w) > 2]
        hits = [ln for ln in lines if any(t in ln.lower() for t in q_terms)][:20]
        head = "\n".join(lines[:8])
        return (("\n".join(hits) + "\n") if hits else "") + head

    mapped = [f"[chunk {i + 1}/{len(chunks)}]\n{_map(c)}" for i, c in enumerate(chunks)]
    reduced = "\n\n".join(mapped)
    if summarizer is not None and len(reduced) > chunk_chars:
        try:
            final = summarizer(reduced)
            if final and final.strip():
                return final.strip()
        except Exception:
            pass
    return reduced


def _dedup_observations(observations):
    """Plan 105: drop consecutive near-duplicate observations (same tool + args +
    ok), which waste tokens when an agent repeats a probe. Order preserved."""
    out = []
    seen_last = None
    for obs in observations:
        import json as _json
        key = (
            getattr(obs, "tool", ""),
            _json.dumps(getattr(obs, "args", {}) or {}, sort_keys=True, default=str),
            bool(getattr(obs, "ok", False)),
        )
        if key == seen_last:
            continue
        seen_last = key
        out.append(obs)
    return out


def assemble_observation_block(
    observations,
    *,
    live_window: int = 6,
    summarizer: Callable[[str], str] | None = None,
) -> str:
    """Build the observation section: the most recent `live_window` observations
    verbatim (newest first) + a rolling summary of everything older. Returns an
    empty string when there are no observations."""
    obs = _dedup_observations(list(observations or ()))
    if not obs:
        return ""
    recent = obs[-live_window:]
    older = obs[:-live_window]
    parts: list[str] = []
    if older:
        raw = _deterministic_summary(older)
        if summarizer is not None:
            try:
                better = summarizer(raw)
                if better and better.strip():
                    raw = "Earlier findings (summary): " + better.strip()
            except Exception:
                pass
        parts.append(raw)
    parts.append("Recent observations (newest first):")
    # Plan 194 F3: only the MOST-RECENT observation stays verbatim; the rest of the live window is
    # masked to compact references (tool-result clearing) — the reasoning agent asks again if it
    # needs the full body of an older result.
    for idx, o in enumerate(reversed(recent)):
        parts.append(_observation_line(o, full=(idx == 0)))
    return "\n".join(parts)
