"""Plan 65 Phase 5 — Observability.

Every harness turn, tool call, and message becomes a structured trace event
written to ``~/.duckln/logs/sessions/<session_id>/agents/<agent_name>.jsonl``.

Slash commands surface this data for debugging:
- ``/agents`` — list active agents in this session
- ``/agents trace [session]`` — render a session's agent timeline
- ``/agents costs [session]`` — token + wall-clock totals

Sensitive payloads (API keys, full secrets) are redacted via the existing
``redact_sensitive_data`` helper from ``repair_intake.py`` before being
written to disk.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# --- Event schema ------------------------------------------------------------


@dataclass(frozen=True)
class TraceEvent:
    """One structured trace event."""

    ts: str               # ISO 8601 UTC
    session_id: str
    agent: str
    turn: int
    kind: str             # "turn_start" | "tool_call" | "tool_result" | "turn_end" | "give_up" | "message"
    tool: str | None = None
    args: dict | None = None
    result_summary: str | None = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error_code: str | None = None
    reason: str | None = None
    extras: dict | None = None


# --- Session id allocation ---------------------------------------------------


def new_session_id() -> str:
    """Allocate a new session id (UUID4 short form)."""
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- Logging paths -----------------------------------------------------------


def logs_root_for_config_dir(config_dir: Path) -> Path:
    return Path(config_dir) / "logs" / "sessions"


def session_dir(config_dir: Path, session_id: str) -> Path:
    return logs_root_for_config_dir(config_dir) / session_id


def agent_log_path(config_dir: Path, session_id: str, agent_name: str) -> Path:
    return session_dir(config_dir, session_id) / "agents" / f"{agent_name}.jsonl"


# --- Redaction wrapper -------------------------------------------------------


def _redact(payload: Any) -> Any:
    """Best-effort redaction for trace payloads.

    Wraps the existing ``redact_sensitive_data`` helper from
    ``repair_intake.py``. Falls back to identity when the import fails (e.g.
    in early unit-test environments).
    """
    try:
        from duckln.diagnostics import redact_sensitive_data
    except Exception:
        return payload
    if isinstance(payload, str):
        return redact_sensitive_data(payload)
    if isinstance(payload, dict):
        return {k: _redact(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return type(payload)(_redact(v) for v in payload)
    return payload


# --- TraceLogger -------------------------------------------------------------


class TraceLogger:
    """Per-session, append-only trace logger.

    Writes one JSON-lines file per agent. Token accounting is maintained in
    memory (per agent, per session) so observability slash commands can
    render totals without re-parsing the whole log.
    """

    def __init__(self, *, config_dir: Path, session_id: str | None = None) -> None:
        self.config_dir = Path(config_dir)
        self.session_id = session_id or new_session_id()
        self._token_totals: dict[str, dict[str, int]] = {}  # agent -> {"in": x, "out": y}
        self._event_counts: dict[str, int] = {}
        self._t0 = time.monotonic()

    # --- Recording ---------------------------------------------------------

    def emit(self, event: TraceEvent) -> None:
        """Write one event to the agent's jsonl file. Path creation is lazy."""
        path = agent_log_path(self.config_dir, self.session_id, event.agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Redact known-sensitive fields before serializing.
        redacted = asdict(event)
        if redacted.get("args"):
            redacted["args"] = _redact(redacted["args"])
        if redacted.get("result_summary"):
            redacted["result_summary"] = _redact(redacted["result_summary"])
        if redacted.get("reason"):
            redacted["reason"] = _redact(redacted["reason"])
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(redacted, default=str) + "\n")
        # Token accounting.
        if event.tokens_in or event.tokens_out:
            totals = self._token_totals.setdefault(event.agent, {"in": 0, "out": 0})
            totals["in"] += int(event.tokens_in or 0)
            totals["out"] += int(event.tokens_out or 0)
        self._event_counts[event.agent] = self._event_counts.get(event.agent, 0) + 1

    def record_turn(
        self,
        agent: str,
        turn: int,
        *,
        tool: str | None,
        args: dict | None,
        ok: bool,
        result_summary: str | None,
        latency_ms: float | None,
        error_code: str | None,
        reason: str | None,
    ) -> None:
        self.emit(TraceEvent(
            ts=_now_iso(),
            session_id=self.session_id,
            agent=agent,
            turn=turn,
            kind="tool_call" if tool else "turn_end",
            tool=tool,
            args=args,
            result_summary=result_summary,
            latency_ms=latency_ms,
            error_code=error_code if not ok else None,
            reason=reason,
        ))

    # --- Reading -----------------------------------------------------------

    def read_events(self, agent: str | None = None) -> tuple[TraceEvent, ...]:
        """Read all events for the session. If `agent` is given, only that
        agent's events are returned. Otherwise all agents merged in time order."""
        directory = session_dir(self.config_dir, self.session_id) / "agents"
        if not directory.exists():
            return ()
        events: list[TraceEvent] = []
        files = [agent_log_path(self.config_dir, self.session_id, agent)] if agent else sorted(directory.glob("*.jsonl"))
        for fp in files:
            if not fp.exists():
                continue
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    events.append(TraceEvent(
                        ts=obj.get("ts", ""),
                        session_id=obj.get("session_id", ""),
                        agent=obj.get("agent", ""),
                        turn=int(obj.get("turn", 0) or 0),
                        kind=obj.get("kind", ""),
                        tool=obj.get("tool"),
                        args=obj.get("args"),
                        result_summary=obj.get("result_summary"),
                        latency_ms=obj.get("latency_ms"),
                        tokens_in=obj.get("tokens_in"),
                        tokens_out=obj.get("tokens_out"),
                        error_code=obj.get("error_code"),
                        reason=obj.get("reason"),
                        extras=obj.get("extras"),
                    ))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
        events.sort(key=lambda e: e.ts)
        return tuple(events)

    def token_totals(self) -> dict[str, dict[str, int]]:
        return {agent: dict(totals) for agent, totals in self._token_totals.items()}

    def event_counts(self) -> dict[str, int]:
        return dict(self._event_counts)


# --- Slash command renderers -------------------------------------------------


def render_agents_list(*, config_dir: Path) -> str:
    """Render the output of ``/agents``: which agents are defined in this build."""
    try:
        from duckln.harness import builtin_agents_directory, AgentRegistry
    except Exception:
        return "Duckln harness not available."
    registry = AgentRegistry.from_directory(builtin_agents_directory())
    if not registry.names():
        return "No agents defined."
    lines = ["Defined harness agents:"]
    for name in registry.names():
        ad = registry.lookup(name)
        flag = " [coordinator]" if ad.is_coordinator else ""
        lines.append(f"  • {name}{flag} — {ad.role}")
    return "\n".join(lines)


def render_trace_timeline(
    *,
    config_dir: Path,
    session_id: str,
    agent: str | None = None,
    limit: int = 50,
) -> str:
    """Render a session's events as a human-readable timeline."""
    logger = TraceLogger(config_dir=config_dir, session_id=session_id)
    events = logger.read_events(agent=agent)
    if not events:
        return f"No trace events found for session {session_id}."
    lines = [f"Trace for session {session_id}:"]
    for ev in events[-limit:]:
        latency = f" ({ev.latency_ms:.0f}ms)" if ev.latency_ms else ""
        tool = ev.tool or "—"
        outcome = ev.error_code or "ok"
        lines.append(
            f"  [{ev.ts}] {ev.agent} t{ev.turn} {ev.kind} {tool}{latency} → {outcome}"
        )
    return "\n".join(lines)


def render_costs(
    *,
    config_dir: Path,
    session_id: str,
) -> str:
    """Render token + wall-clock totals for a session."""
    logger = TraceLogger(config_dir=config_dir, session_id=session_id)
    events = logger.read_events()
    if not events:
        return f"No trace events found for session {session_id}."
    by_agent: dict[str, dict[str, int]] = {}
    for ev in events:
        agg = by_agent.setdefault(ev.agent, {"events": 0, "tokens_in": 0, "tokens_out": 0})
        agg["events"] += 1
        agg["tokens_in"] += int(ev.tokens_in or 0)
        agg["tokens_out"] += int(ev.tokens_out or 0)
    lines = [f"Costs for session {session_id}:"]
    total_in = total_out = total_ev = 0
    for agent, agg in sorted(by_agent.items()):
        lines.append(
            f"  {agent}: {agg['events']} events, "
            f"{agg['tokens_in']} tokens in, {agg['tokens_out']} tokens out"
        )
        total_in += agg["tokens_in"]
        total_out += agg["tokens_out"]
        total_ev += agg["events"]
    lines.append(f"  ── total: {total_ev} events, {total_in} tokens in, {total_out} tokens out")
    return "\n".join(lines)
