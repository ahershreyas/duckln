"""High-signal state access helpers built on top of the SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
import json

from agent.memory import (
    AGENTS_FILE_NAME,
    BOOTSTRAP_FILE_NAME,
    DEFAULT_AGENTS_STUB,
    FAILURES_DIR_NAME,
    IDENTITY_FILE_NAME,
    KNOWLEDGE_DIR_NAME,
    SESSIONS_DIR_NAME,
    SKILLS_DIR_NAME,
    SOUL_FILE_NAME,
    SUBAGENTS_DIR_NAME,
    TOOLS_DOC_FILE_NAME,
    TOOLS_FILE_NAME,
    USER_FILE_NAME,
    initialize_agent_memory,
    materialize_memory_view,
    resolve_agent_memory_paths,
)
from agent.probe import SystemProbe
from duckln.subagents import default_subagent_contracts
from duckln.tool_registry import ToolVisibilityPolicy, active_tool_order, render_tools_manifest
from state.store import initialize_state_store


FOLLOWUP_STATE_PREFIX = "conversation.followup."
WORKFLOW_STATE_PREFIX = "conversation.workflow."
FOLLOWUP_STATE_KEYS = (
    "last_supervisor_decision",
    "last_route_family",
    "last_route_confidence",
    "last_response_contract",
    "active_topic",
    "pending_objective_id",
    "pending_repo_key",
    "pending_repo_name",
    "pending_next_action",
    "pending_offer_kind",
    "pending_offer_label",
    "pending_offer_objective_id",
    "pending_offer_repo_key",
    "pending_offer_repo_name",
    "pending_offer_id",
    "pending_offer_thread_id",
    "pending_offer_expires_at",
    "pending_question_type",
    "pending_subject_type",
    "pending_clarification_options",
    "last_detected_system_summary",
    "last_recommendation_fit",
    "last_verified_fact_summary",
    "last_answer_style",
    "last_discussed_repo_key",
    "last_discussed_repo_name",
    "last_choice_prompt",
    "clarify_count",  # Plan 195 (§6): per-thread clarify counter — cap at 2, then act, no loop.
    "last_chosen_option",
    "last_next_step_offered",
    "last_completed_step",
    "last_recommendation_alternatives",
    "shortlist_repo_names",
    "shortlist_primary_repo",
    "shortlist_secondary_repo",
    "last_render_fingerprint",
    "last_slash_runtime_action",
    "active_thread_id",
    "active_thread_expires_at",
    "active_session_id",
    "active_execution_target",
    "active_vm_name",
    "last_plan_path",
    "pending_cloud_remediation",
)
WORKFLOW_STATE_KEYS = (
    "active_repo_key",
    "active_repo_name",
    "active_issue_kind",
    "active_issue_summary",
    "active_objective_id",
    "active_objective_kind",
    "active_objective_repo_key",
    "active_objective_repo_name",
    "active_objective_status",
    "active_objective_goal",
    "active_objective_execution_target",
    "active_objective_runtime_command",
    "active_objective_last_blocker",
    "active_objective_attempt_count",
    "active_objective_max_attempts",
    "active_objective_requires_user_decision",
    "active_objective_resume_hint",
    "active_objective_started_at",
    "active_objective_updated_at",
    "active_incident_category",
    "active_incident_summary",
    # Plan 193 F1 (WRITE): the fuller incident scratchpad + a bounded recent-events ring, so a
    # follow-up like "what is the issue?" resolves against what just happened.
    "active_incident_command",
    "active_incident_excerpt",
    "active_incident_resolved",
    "recent_events",
    "active_repair_phase",
    "active_runtime_status",
    "active_runtime_command",
    "active_runtime_command_kind",
    "active_runtime_repo_key",
    "active_runtime_repo_name",
    "active_runtime_cwd",
    "active_runtime_pid",
    "active_runtime_log_path",
    "active_runtime_execution_target",
    "active_runtime_vm_name",
    "active_runtime_attach_hint",
    "active_runtime_logs_hint",
    "active_runtime_stop_hint",
    "active_runtime_stop_command",
    "active_runtime_docker_name",
    "active_runtime_cloud_resource_key",
    "active_runtime_cloud_vendor",
    "active_runtime_cloud_region",
    "active_runtime_cloud_shape",
    "active_runtime_run_approved",
    "pending_destructive_action",
    "pending_destructive_repo_key",
    "pending_destructive_repo_name",
    "pending_destructive_path",
    "pending_destructive_target",
    "last_confirmation_outcome",
    # Plan 171 F8: the deterministic resource-crunch FACTS (JSON: used/free/needed/reclaimed +
    # options) so the conversation layer can hand them to the LLM as a HINT — the LLM owns the
    # voice/recommendation over these facts; the sensing/reclaim/resize-math stay the floor.
    "active_resource_crunch",
)


@dataclass(frozen=True)
class MemoryClearResult:
    """Outcome of a bounded memory-clear operation."""

    cleared: bool
    scope: str
    summary: str


def _as_path(config_dir: str | PathLike[str] | Path) -> Path:
    return Path(config_dir)


def write_config_snapshot(config_dir: str | PathLike[str] | Path, values: dict[str, str]) -> None:
    """Persist a non-secret config snapshot to SQLite."""

    initialize_state_store(_as_path(config_dir)).upsert_config_values(values)


def read_config_snapshot(
    config_dir: str | PathLike[str] | Path,
    *,
    prefix: str | None = None,
) -> dict[str, str]:
    """Load a config snapshot from SQLite."""

    return initialize_state_store(_as_path(config_dir)).read_config_values(prefix=prefix)


def write_followup_state(
    config_dir: str | PathLike[str] | Path,
    values: dict[str, object | None],
) -> None:
    """Persist concise supervisor follow-up state in SQLite-backed config."""

    store = initialize_state_store(_as_path(config_dir))
    payload = {
        f"{FOLLOWUP_STATE_PREFIX}{key}": json.dumps(value)
        for key, value in values.items()
        if key in FOLLOWUP_STATE_KEYS and value is not None
    }
    if payload:
        store.upsert_config_values(payload)
    stale_keys = tuple(
        f"{FOLLOWUP_STATE_PREFIX}{key}"
        for key, value in values.items()
        if key in FOLLOWUP_STATE_KEYS and value is None
    )
    if stale_keys:
        store.delete_config_values(stale_keys)


def read_followup_state(config_dir: str | PathLike[str] | Path) -> dict[str, object]:
    """Load normalized supervisor follow-up state from SQLite-backed config."""

    raw = read_config_snapshot(config_dir, prefix=FOLLOWUP_STATE_PREFIX)
    result: dict[str, object] = {}
    for key, value in raw.items():
        short_key = key.removeprefix(FOLLOWUP_STATE_PREFIX)
        if short_key not in FOLLOWUP_STATE_KEYS:
            continue
        try:
            result[short_key] = json.loads(value)
        except json.JSONDecodeError:
            result[short_key] = value
    return result


def clear_followup_state(config_dir: str | PathLike[str] | Path) -> int:
    """Delete persisted supervisor follow-up state."""

    keys = tuple(f"{FOLLOWUP_STATE_PREFIX}{key}" for key in FOLLOWUP_STATE_KEYS)
    return initialize_state_store(_as_path(config_dir)).delete_config_values(keys)


REPO_FACTS_PREFIX = "repo_fact::"
_REPO_FACT_KEYS = frozenset(
    # Plan 87: also remember the understood archetype + declared services so a
    # future plan for the SAME repo recalls "this is a Tauri desktop app needing Postgres".
    {"run_command", "node_version", "package_manager", "env_file", "archetype", "services"}
)


def _repo_fact_slug(repo_slug: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(repo_slug or "")).strip("_") or "repo"


def write_repo_facts(
    config_dir: str | PathLike[str] | Path, repo_slug: str, facts: dict[str, str]
) -> None:
    """Plan 79 L8: persist per-repo learned facts (e.g. the working run command,
    the required Node version) so a future plan for the SAME repo starts smarter.
    Only known keys with truthy values are stored."""
    safe = _repo_fact_slug(repo_slug)
    payload = {
        f"{REPO_FACTS_PREFIX}{safe}::{key}": str(value)
        for key, value in facts.items()
        if key in _REPO_FACT_KEYS and value
    }
    if payload:
        initialize_state_store(_as_path(config_dir)).upsert_config_values(payload)


def read_repo_facts(config_dir: str | PathLike[str] | Path, repo_slug: str) -> dict[str, str]:
    """Plan 79 L8: load per-repo learned facts recorded after a prior verified run."""
    safe = _repo_fact_slug(repo_slug)
    prefix = f"{REPO_FACTS_PREFIX}{safe}::"
    raw = read_config_snapshot(config_dir, prefix=prefix)
    return {key.removeprefix(prefix): value for key, value in raw.items()}


# --- Plan 95: configurable per-tool HITL policy + user preferences -----------

TOOL_POLICY_PREFIX = "tool_policy::"
USER_PREF_PREFIX = "user_pref::"


def write_tool_policy(config_dir: str | PathLike[str] | Path, *, tool: str, decision: str) -> None:
    """Persist a per-tool HITL decision: 'allow' | 'deny' | 'default' (clears).
    Honored by the harness dispatcher before the mode gate."""
    decision = str(decision or "default").strip().lower()
    if decision not in ("allow", "deny", "default"):
        decision = "default"
    initialize_state_store(_as_path(config_dir)).upsert_config_values(
        {f"{TOOL_POLICY_PREFIX}{tool}": decision}
    )


def read_tool_policy(config_dir: str | PathLike[str] | Path) -> dict[str, str]:
    """Load the user's per-tool policy as {tool: 'allow'|'deny'} (omits 'default')."""
    raw = read_config_snapshot(config_dir, prefix=TOOL_POLICY_PREFIX)
    out: dict[str, str] = {}
    for key, value in raw.items():
        decision = str(value or "").strip().lower()
        if decision in ("allow", "deny"):
            out[key.removeprefix(TOOL_POLICY_PREFIX)] = decision
    return out


def write_user_preference(config_dir: str | PathLike[str] | Path, *, key: str, value: str) -> None:
    """Persist a single user preference (e.g. answer verbosity, auto-commit)."""
    initialize_state_store(_as_path(config_dir)).upsert_config_values(
        {f"{USER_PREF_PREFIX}{key}": str(value)}
    )


def read_user_preferences(config_dir: str | PathLike[str] | Path) -> dict[str, str]:
    """Load all user preferences as {key: value}."""
    raw = read_config_snapshot(config_dir, prefix=USER_PREF_PREFIX)
    return {key.removeprefix(USER_PREF_PREFIX): value for key, value in raw.items()}


REPO_MEMORY_PREFIX = "repo_memory::"
_REPO_MEMORY_KEEP = 12


def write_repo_memory(
    config_dir: str | PathLike[str] | Path, *, repo_slug: str, question: str, answer: str
) -> None:
    """Plan 95: remember a Q&A about a repo so a later conversation can recall what
    Duckln already learned about THIS repo. Redact before calling. Bounded ring."""
    safe = _repo_fact_slug(repo_slug)
    prefix = f"{REPO_MEMORY_PREFIX}{safe}::"
    import time as _t

    entry = json.dumps({"q": str(question)[:300], "a": str(answer)[:1200], "t": int(_t.time())})
    store = initialize_state_store(_as_path(config_dir))
    existing = read_config_snapshot(config_dir, prefix=prefix)
    store.upsert_config_values({f"{prefix}{int(_t.time() * 1000)}": entry})
    # Trim oldest beyond the keep window (set to empty; read skips empties).
    keys = sorted(existing.keys())
    overflow = keys[: max(0, len(keys) + 1 - _REPO_MEMORY_KEEP)]
    if overflow:
        store.upsert_config_values({k: "" for k in overflow})


def read_repo_memory(config_dir: str | PathLike[str] | Path, repo_slug: str) -> tuple[dict, ...]:
    """Plan 95: load recent Q&A memory for a repo (newest last)."""
    safe = _repo_fact_slug(repo_slug)
    prefix = f"{REPO_MEMORY_PREFIX}{safe}::"
    raw = read_config_snapshot(config_dir, prefix=prefix)
    out: list[dict] = []
    for key in sorted(raw.keys()):
        value = raw[key]
        if not value:
            continue
        try:
            out.append(json.loads(value))
        except (ValueError, TypeError):
            continue
    return tuple(out[-_REPO_MEMORY_KEEP:])


_MEMORY_STOPWORDS = frozenset({
    "the", "and", "for", "how", "does", "what", "with", "this", "that", "your", "you",
    "are", "was", "where", "which", "why", "when", "from", "into", "its", "explain",
    "show", "tell", "use", "using", "code", "file", "files", "function", "work", "works",
})


def _tokenize(text: str) -> set[str]:
    import re as _re
    return {
        w for w in _re.split(r"[^a-z0-9]+", str(text or "").lower())
        if len(w) > 2 and w not in _MEMORY_STOPWORDS
    }


def read_repo_memory_ranked(
    config_dir: str | PathLike[str] | Path, repo_slug: str, *, query: str, top_k: int = 3
) -> tuple[dict, ...]:
    """Plan 101: return the prior Q&A entries most RELEVANT to `query` (keyword-overlap
    scoring with a recency tiebreak), not just the most recent. Falls back to recency
    when nothing overlaps."""
    entries = list(read_repo_memory(config_dir, repo_slug))
    if not entries:
        return ()
    q_tokens = _tokenize(query)
    if not q_tokens:
        return tuple(entries[-top_k:])
    scored = []
    for i, e in enumerate(entries):
        e_tokens = _tokenize(e.get("q", "")) | _tokenize(e.get("a", ""))
        overlap = len(q_tokens & e_tokens)
        scored.append((overlap, i, e))  # i = recency tiebreak (higher = newer)
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    top = [e for score, _i, e in scored[:top_k] if score > 0]
    return tuple(top) if top else tuple(entries[-top_k:])


RESOURCE_LOG_PREFIX = "resource_log::"
_RESOURCE_LOG_KEEP = 50


def write_resource_event(config_dir: str | PathLike[str] | Path, *, target: str, event: str) -> None:
    """Plan 111: append a per-target resource event (crunch seen / resize / approval)
    so the user can refer back. `target` is the composite label (e.g. local-duckln-vm)."""
    safe = _repo_fact_slug(target)
    prefix = f"{RESOURCE_LOG_PREFIX}{safe}::"
    import time as _t

    entry = json.dumps({"event": str(event)[:500], "t": int(_t.time())})
    store = initialize_state_store(_as_path(config_dir))
    existing = read_config_snapshot(config_dir, prefix=prefix)
    store.upsert_config_values({f"{prefix}{int(_t.time() * 1000)}": entry})
    overflow = sorted(existing.keys())[: max(0, len(existing) + 1 - _RESOURCE_LOG_KEEP)]
    if overflow:
        store.upsert_config_values({k: "" for k in overflow})


def read_resource_log(config_dir: str | PathLike[str] | Path, target: str) -> tuple[dict, ...]:
    """Plan 111: the resource event history for a target (oldest→newest)."""
    safe = _repo_fact_slug(target)
    prefix = f"{RESOURCE_LOG_PREFIX}{safe}::"
    raw = read_config_snapshot(config_dir, prefix=prefix)
    out: list[dict] = []
    for key in sorted(raw.keys()):
        if not raw[key]:
            continue
        try:
            out.append(json.loads(raw[key]))
        except (ValueError, TypeError):
            continue
    return tuple(out[-_RESOURCE_LOG_KEEP:])


REPO_SKILL_PREFIX = "repo_skill::"
_REPO_SKILL_KEEP = 20


def write_repo_skill(config_dir: str | PathLike[str] | Path, *, repo_slug: str, task: str, lesson: str) -> None:
    """Plan 109: persist a reusable skill distilled from a verified agent action on
    this repo (what task → what worked). Redact before calling; bounded ring."""
    safe = _repo_fact_slug(repo_slug)
    prefix = f"{REPO_SKILL_PREFIX}{safe}::"
    import time as _t

    entry = json.dumps({"task": str(task)[:200], "lesson": str(lesson)[:1000], "t": int(_t.time())})
    store = initialize_state_store(_as_path(config_dir))
    existing = read_config_snapshot(config_dir, prefix=prefix)
    store.upsert_config_values({f"{prefix}{int(_t.time() * 1000)}": entry})
    overflow = sorted(existing.keys())[: max(0, len(existing) + 1 - _REPO_SKILL_KEEP)]
    if overflow:
        store.upsert_config_values({k: "" for k in overflow})


def read_repo_skills_ranked(
    config_dir: str | PathLike[str] | Path, repo_slug: str, *, query: str, top_k: int = 1
) -> tuple[str, ...]:
    """Plan 109: recall the distilled skills for this repo most relevant to `query`
    (keyword-overlap ranked). Returns lesson strings."""
    safe = _repo_fact_slug(repo_slug)
    prefix = f"{REPO_SKILL_PREFIX}{safe}::"
    raw = read_config_snapshot(config_dir, prefix=prefix)
    entries = []
    for key in sorted(raw.keys()):
        if not raw[key]:
            continue
        try:
            entries.append(json.loads(raw[key]))
        except (ValueError, TypeError):
            continue
    if not entries:
        return ()
    q = _tokenize(query)
    scored = []
    for i, e in enumerate(entries):
        overlap = len(q & (_tokenize(e.get("task", "")) | _tokenize(e.get("lesson", ""))))
        scored.append((overlap, i, e))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    top = [e["lesson"] for score, _i, e in scored[:top_k] if score > 0]
    return tuple(top)


COMMON_LESSON_PREFIX = "common_lesson::"


def _lesson_sig_slug(signature: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(signature or "")).strip("_") or "lesson"


def write_common_lesson(
    config_dir: str | PathLike[str] | Path, *, signature: str, lesson: str, fix_command: str = ""
) -> None:
    """Plan 80 Fix 9: persist a generalized, cross-repo lesson (a pitfall + its fix)
    keyed by a framework/error SIGNATURE so EVERY future repo benefits. Redacted by
    the caller; never repo-specific secrets."""
    sig = _lesson_sig_slug(signature)
    payload = {
        f"{COMMON_LESSON_PREFIX}{sig}::lesson": str(lesson or ""),
        f"{COMMON_LESSON_PREFIX}{sig}::fix": str(fix_command or ""),
    }
    initialize_state_store(_as_path(config_dir)).upsert_config_values(
        {k: v for k, v in payload.items() if v}
    )


def read_common_lessons(config_dir: str | PathLike[str] | Path) -> dict[str, dict[str, str]]:
    """Plan 80 Fix 9: load all cross-repo lessons as {signature: {lesson, fix}}."""
    raw = read_config_snapshot(config_dir, prefix=COMMON_LESSON_PREFIX)
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        rest = key.removeprefix(COMMON_LESSON_PREFIX)
        if "::" not in rest:
            continue
        sig, field = rest.rsplit("::", 1)
        out.setdefault(sig, {})[field] = value
    return out


def write_pending_cloud_remediation(
    config_dir: str | PathLike[str] | Path,
    *,
    provider: str,
    kind: str,
    label: str,
    project: str | None = None,
    source_url: str | None = None,
) -> None:
    """Persist a pending cloud remediation surfaced on the next /cloud or login turn."""

    payload = {
        "provider": provider,
        "kind": kind,
        "label": label,
        "project": project,
        "source_url": source_url,
    }
    write_followup_state(config_dir, {"pending_cloud_remediation": payload})


def read_pending_cloud_remediation(
    config_dir: str | PathLike[str] | Path,
) -> dict[str, object] | None:
    """Return the persisted pending cloud remediation payload, or None when absent."""

    state = read_followup_state(config_dir)
    payload = state.get("pending_cloud_remediation")
    if isinstance(payload, dict):
        return payload
    return None


def clear_pending_cloud_remediation(config_dir: str | PathLike[str] | Path) -> None:
    """Remove any persisted pending cloud remediation."""

    write_followup_state(config_dir, {"pending_cloud_remediation": None})


def write_workflow_state(
    config_dir: str | PathLike[str] | Path,
    values: dict[str, object | None],
) -> None:
    """Persist concise workflow state for active repo issues and confirmations."""

    store = initialize_state_store(_as_path(config_dir))
    payload = {
        f"{WORKFLOW_STATE_PREFIX}{key}": json.dumps(value)
        for key, value in values.items()
        if key in WORKFLOW_STATE_KEYS and value is not None
    }
    if payload:
        store.upsert_config_values(payload)
    stale_keys = tuple(
        f"{WORKFLOW_STATE_PREFIX}{key}"
        for key, value in values.items()
        if key in WORKFLOW_STATE_KEYS and value is None
    )
    if stale_keys:
        store.delete_config_values(stale_keys)


def read_workflow_state(config_dir: str | PathLike[str] | Path) -> dict[str, object]:
    """Load normalized workflow state from SQLite-backed config."""

    raw = read_config_snapshot(config_dir, prefix=WORKFLOW_STATE_PREFIX)
    result: dict[str, object] = {}
    for key, value in raw.items():
        short_key = key.removeprefix(WORKFLOW_STATE_PREFIX)
        if short_key not in WORKFLOW_STATE_KEYS:
            continue
        try:
            result[short_key] = json.loads(value)
        except json.JSONDecodeError:
            result[short_key] = value
    return result


def clear_workflow_state(config_dir: str | PathLike[str] | Path) -> int:
    """Delete persisted workflow state."""

    keys = tuple(f"{WORKFLOW_STATE_PREFIX}{key}" for key in WORKFLOW_STATE_KEYS)
    return initialize_state_store(_as_path(config_dir)).delete_config_values(keys)


_RECENT_EVENTS_MAX = 5


def write_active_incident(
    config_dir: str | PathLike[str] | Path,
    *,
    summary: str,
    category: str,
    command: str | None = None,
    excerpt: str | None = None,
    resolved: bool = False,
    also_event: bool = True,
) -> None:
    """Plan 193 F1 (WRITE): persist the current blocker to the session scratchpad (bounded,
    redacted), so "what is the issue?" can SELECT and answer from it. Also appends a recent-event."""
    from duckln.diagnostics import redact_sensitive_data

    def _bound(text: str | None, limit: int) -> str | None:
        if not text:
            return None
        red = redact_sensitive_data(str(text))
        lines = red.strip().splitlines()
        tail = "\n".join(lines[-8:]) if len(lines) > 8 else red.strip()  # COMPRESS: last-N-lines
        return tail[:limit]

    write_workflow_state(config_dir, {
        "active_incident_summary": _bound(summary, 400),
        "active_incident_category": str(category)[:60] if category else None,
        "active_incident_command": _bound(command, 200),
        "active_incident_excerpt": _bound(excerpt, 800),
        "active_incident_resolved": bool(resolved),
        "active_issue_summary": _bound(summary, 400),
    })
    if also_event and summary:
        append_recent_event(config_dir, str(summary))


def append_recent_event(config_dir: str | PathLike[str] | Path, event: str, *, max_events: int = _RECENT_EVENTS_MAX) -> None:
    """Plan 193 F1: append a compact, redacted event to a bounded recent-events ring."""
    from duckln.diagnostics import redact_sensitive_data

    text = redact_sensitive_data(str(event or "")).strip()
    if not text:
        return
    text = " ".join(text.splitlines())[:200]
    current = read_workflow_state(config_dir).get("recent_events")
    events = list(current) if isinstance(current, list) else []
    if events and events[-1] == text:
        return  # de-dupe consecutive repeats
    events.append(text)
    write_workflow_state(config_dir, {"recent_events": events[-max_events:]})


def resolve_active_incident(config_dir: str | PathLike[str] | Path) -> None:
    """Plan 193 F1: mark the active incident resolved (the blocker cleared)."""
    write_workflow_state(config_dir, {"active_incident_resolved": True})


def record_system_probe(config_dir: str | PathLike[str] | Path, probe: SystemProbe) -> None:
    """Persist a concise system probe summary and normalized fields."""

    store = initialize_state_store(_as_path(config_dir))
    store.upsert_config_values(probe.to_state_values())
    store.record_run(
        run_id="system-probe",
        command_name="system_probe",
        status="ready",
        summary=probe.summary(),
        metadata={
            "os": probe.operating_system,
            "architecture": probe.architecture,
            "gpu_backend": probe.gpu.backend,
        },
    )


def initialize_managed_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    contract_source: Path | None = None,
) -> None:
    """Ensure SQLite-managed memory exists and materialize the agent-facing view."""

    resolved_config_dir = _as_path(config_dir)
    initialize_agent_memory(resolved_config_dir)
    store = initialize_state_store(resolved_config_dir)
    if store.get_managed_memory_record("agents:root") is None:
        store.upsert_managed_memory(
            memory_key="agents:root",
            memory_kind="agents",
            relative_path=AGENTS_FILE_NAME,
            title="Duckln Agent Contract",
            content=_load_contract_content(contract_source),
        )
    _ensure_core_memory_contracts(resolved_config_dir)
    materialize_managed_memory_state(resolved_config_dir)


def materialize_managed_memory_state(config_dir: str | PathLike[str] | Path) -> tuple[Path, ...]:
    """Materialize the SQLite-backed managed memory view to the filesystem."""

    resolved_config_dir = _as_path(config_dir)
    initialize_agent_memory(resolved_config_dir)
    _ensure_core_memory_contracts(resolved_config_dir)
    records = initialize_state_store(resolved_config_dir).list_managed_memory_records()
    paths = resolve_agent_memory_paths(resolved_config_dir)
    return materialize_memory_view(paths, ((record.relative_path, record.content) for record in records))


def write_skill_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    slug: str,
    title: str,
    summary: str,
) -> Path:
    """Persist a managed skill note in SQLite, then materialize it."""

    relative_path = f"{SKILLS_DIR_NAME}/{_slugify(slug)}.md"
    content = f"# {title.strip()}\n\n{_normalize_summary_text(summary)}"
    return _upsert_and_materialize_memory(
        config_dir,
        memory_key=f"skill:{_slugify(slug)}",
        memory_kind="skill",
        relative_path=relative_path,
        title=title,
        content=content,
    )


# Plan 59 Fix 1: shared cross-repo failure log slug.
_GLOBAL_FAILURE_SLUG = "_global"

# Plan 59 Fix 1: substrings that mark a failure as environmental (host/target
# problem, not repo-specific). Environmental failures dual-write to the global
# failure log so a lesson learned in one repo helps every subsequent repo.
_ENVIRONMENTAL_FAILURE_MARKERS: tuple[str, ...] = (
    "command not found",
    "no such file or directory",
    "unable to locate package",
    "no installation candidate",
    "unsupported architecture",
    "unsupported python",
    "not supported on this platform",
    "executable file not found",
    "not recognized as",
)


def _is_environmental_failure(stderr_fingerprint: str) -> bool:
    """Return True if the fingerprint indicates a host/target-level failure
    (not a repo-specific code error like ModuleNotFoundError: my_pkg)."""
    if not stderr_fingerprint:
        return False
    fp_lower = stderr_fingerprint.lower()
    if any(marker in fp_lower for marker in _ENVIRONMENTAL_FAILURE_MARKERS):
        return True
    # Permission denied is environmental ONLY when paired with sudo / EACCES
    # tokens — otherwise it could be a repo-specific file permission issue.
    if "permission denied" in fp_lower and any(t in fp_lower for t in ("sudo", "eacces", "root")):
        return True
    return False


def write_failure_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    repo_slug: str,
    command: str,
    execution_target: str,
    exit_code: int,
    stderr_fingerprint: str,
) -> Path:
    """Plan 58 Bug D: persistently record an install command failure so that
    on a future Duckln run, the planner can consult this log and avoid
    re-proposing the same failing command.

    Plan 59 Fix 1: dual-writes to the global failure log when the stderr
    fingerprint indicates an environmental (host/target) failure. This lets
    lessons transfer across repos — "brew install node failed on vm" learned
    in repo A also blocks the same command in repo B.

    Records are stored as JSON inside a markdown file at
    ~/.duckln/memory/failures/<repo_slug>.md. One file per repo. The JSON is
    a list of records; duplicate (command, execution_target) entries are
    merged by incrementing retry_count and refreshing last_seen.
    """

    file_path = _write_single_failure_file(
        config_dir=config_dir,
        slug=repo_slug,
        command=command,
        execution_target=execution_target,
        exit_code=exit_code,
        stderr_fingerprint=stderr_fingerprint,
    )
    # Plan 59 Fix 1: if the failure is environmental (host/target-level), also
    # record it in the global log so other repos benefit from the lesson.
    if _is_environmental_failure(stderr_fingerprint) and repo_slug != _GLOBAL_FAILURE_SLUG:
        try:
            _write_single_failure_file(
                config_dir=config_dir,
                slug=_GLOBAL_FAILURE_SLUG,
                command=command,
                execution_target=execution_target,
                exit_code=exit_code,
                stderr_fingerprint=stderr_fingerprint,
            )
        except Exception:
            # Global dual-write is best-effort; primary record already exists.
            pass
    return file_path


def _write_single_failure_file(
    *,
    config_dir: str | PathLike[str] | Path,
    slug: str,
    command: str,
    execution_target: str,
    exit_code: int,
    stderr_fingerprint: str,
) -> Path:
    """Internal: write/merge one failure record into a single failure file."""

    import json
    import time as _time

    resolved_config_dir = _as_path(config_dir)
    paths = resolve_agent_memory_paths(resolved_config_dir)
    failures_dir = paths.memory_root / FAILURES_DIR_NAME
    failures_dir.mkdir(parents=True, exist_ok=True)
    file_path = failures_dir / f"{_slugify(slug)}.md"

    existing: list[dict] = []
    if file_path.exists():
        try:
            text = file_path.read_text(encoding="utf-8")
            existing = _parse_failure_records(text)
        except (OSError, ValueError):
            existing = []

    now_iso = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    key = (command.strip(), execution_target.strip())
    merged: list[dict] = []
    upserted = False
    for record in existing:
        rec_key = (str(record.get("command", "")).strip(), str(record.get("execution_target", "")).strip())
        if rec_key == key:
            new_record = {
                "command": command,
                "execution_target": execution_target,
                "exit_code": exit_code,
                "stderr_fingerprint": stderr_fingerprint,
                "last_seen": now_iso,
                "retry_count": int(record.get("retry_count", 0)) + 1,
            }
            merged.append(new_record)
            upserted = True
        else:
            merged.append(record)
    if not upserted:
        merged.append({
            "command": command,
            "execution_target": execution_target,
            "exit_code": exit_code,
            "stderr_fingerprint": stderr_fingerprint,
            "last_seen": now_iso,
            "retry_count": 1,
        })

    payload = json.dumps(merged, indent=2)
    title = f"Failure log for {slug}"
    if slug == _GLOBAL_FAILURE_SLUG:
        body = (
            "Duckln records ENVIRONMENTAL install command failures here (host/target\n"
            "level — brew not found, package not in repo, unsupported arch, etc.) so\n"
            "that future runs across ALL repos can avoid re-proposing the same failing\n"
            "command. Records older than 24h are ignored. Delete to reset."
        )
    else:
        body = (
            "Duckln records install command failures here so that future runs can\n"
            "avoid re-proposing the same failing command. Records older than 24h\n"
            "are ignored by the planner. Delete this file to reset."
        )
    content = f"# {title}\n\n{body}\n\n```json\n{payload}\n```\n"
    file_path.write_text(content, encoding="utf-8")
    return file_path


def read_failure_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    repo_slug: str,
) -> tuple[dict, ...]:
    """Plan 58 Bug D: read the persisted failure records for a repo."""

    resolved_config_dir = _as_path(config_dir)
    paths = resolve_agent_memory_paths(resolved_config_dir)
    file_path = paths.memory_root / FAILURES_DIR_NAME / f"{_slugify(repo_slug)}.md"
    if not file_path.exists():
        return ()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    records = _parse_failure_records(text)
    return tuple(records)


def _parse_failure_records(markdown_text: str) -> list[dict]:
    """Extract the JSON payload from the failure-log markdown file."""

    import json
    if not markdown_text:
        return []
    # Find the ```json ... ``` block.
    start = markdown_text.find("```json")
    if start < 0:
        return []
    after_open = markdown_text.find("\n", start)
    if after_open < 0:
        return []
    end = markdown_text.find("```", after_open + 1)
    if end < 0:
        return []
    payload = markdown_text[after_open + 1:end].strip()
    try:
        records = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _failure_window_hours_from_config(config_dir: str | PathLike[str] | Path) -> float:
    """Plan 61 Fix C: read the user's configured failure_window_hours.
    Returns 24.0 when no config exists or the field is missing."""
    try:
        from duckln.config import load_app_config, ConfigPaths
        resolved = _as_path(config_dir)
        paths = ConfigPaths(config_dir=resolved, config_file=resolved / "config.json")
        cfg = load_app_config(paths)
        if cfg is None:
            return 24.0
        return float(getattr(cfg, "failure_window_hours", 24.0))
    except Exception:
        return 24.0


def _failure_record_is_recent(record: dict, *, hours: float = 24.0) -> bool:
    """Return True if the failure's last_seen timestamp is within `hours` of now."""

    import time as _time
    last_seen = str(record.get("last_seen", "")).strip()
    if not last_seen:
        return False
    try:
        seen_struct = _time.strptime(last_seen, "%Y-%m-%dT%H:%M:%SZ")
        seen_epoch = _time.mktime(seen_struct) - _time.timezone
    except (ValueError, OverflowError):
        return False
    now_epoch = _time.time()
    return (now_epoch - seen_epoch) < (hours * 3600)


def lookup_recent_failure(
    config_dir: str | PathLike[str] | Path,
    *,
    repo_slug: str,
    command: str,
    execution_target: str,
    window_hours: float | None = None,
) -> dict | None:
    """Plan 58 Bug D + Plan 59 Fix 1 + Plan 61 Fix C: return a failure record
    matching (command, target) if it was last seen within `window_hours`.

    When `window_hours` is None (default), the value is read from the user's
    AppConfig (`failure_window_hours`), defaulting to 24h.

    Lookup precedence: per-repo log first (most specific), then fall back to
    the global log (cross-repo environmental failures). Returns None when no
    recent match in either tier.
    """

    if window_hours is None:
        window_hours = _failure_window_hours_from_config(config_dir)
    cmd_norm = command.strip()
    target_norm = execution_target.strip()

    def _scan(slug: str) -> dict | None:
        records = read_failure_memory_state(config_dir, repo_slug=slug)
        for record in records:
            if (
                str(record.get("command", "")).strip() == cmd_norm
                and str(record.get("execution_target", "")).strip() == target_norm
                and _failure_record_is_recent(record, hours=window_hours)
            ):
                return record
        return None

    primary = _scan(repo_slug)
    if primary is not None:
        return primary
    if repo_slug != _GLOBAL_FAILURE_SLUG:
        return _scan(_GLOBAL_FAILURE_SLUG)
    return None


def write_knowledge_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    slug: str,
    title: str,
    summary: str,
) -> Path:
    """Persist a managed knowledge note in SQLite, then materialize it."""

    relative_path = f"{KNOWLEDGE_DIR_NAME}/{_slugify(slug)}.md"
    content = f"# {title.strip()}\n\n{_normalize_summary_text(summary)}"
    return _upsert_and_materialize_memory(
        config_dir,
        memory_key=f"knowledge:{_slugify(slug)}",
        memory_kind="knowledge",
        relative_path=relative_path,
        title=title,
        content=content,
    )


def write_session_summary_state(
    config_dir: str | PathLike[str] | Path,
    *,
    session_id: str,
    summary: str,
) -> Path:
    """Persist a concise session summary to filesystem memory and SQLite."""

    resolved_config_dir = _as_path(config_dir)
    destination = _upsert_and_materialize_memory(
        resolved_config_dir,
        memory_key=f"session:{_slugify(session_id)}",
        memory_kind="session",
        relative_path=f"{SESSIONS_DIR_NAME}/{_slugify(session_id)}.md",
        title=session_id,
        content=_normalize_summary_text(summary),
    )
    initialize_state_store(resolved_config_dir).record_run(
        run_id=f"session-summary:{session_id}",
        command_name="session_summary",
        status="saved",
        summary=summary,
        metadata={"session_id": session_id},
    )
    return destination


def read_session_summary_state(
    config_dir: str | PathLike[str] | Path,
    *,
    session_id: str,
) -> str | None:
    """Load a concise session summary from SQLite-backed managed memory."""

    resolved_config_dir = _as_path(config_dir)
    store = initialize_state_store(resolved_config_dir)
    record = store.get_managed_memory_record(f"session:{_slugify(session_id)}")
    if record is not None:
        return record.content.strip()
    return None


def clear_memory_scope(
    config_dir: str | PathLike[str] | Path,
    *,
    scope: str,
    contract_source: Path | None = None,
    config_file: Path | None = None,
) -> MemoryClearResult:
    """Clear a bounded memory scope, resync files, and reclaim SQLite space."""

    resolved_config_dir = _as_path(config_dir)
    store = initialize_state_store(resolved_config_dir)

    if scope == "session":
        deleted_rows = store.clear_session_history()
        deleted_rows += clear_followup_state(resolved_config_dir)
        materialize_managed_memory_state(resolved_config_dir)
        store.vacuum()
        return MemoryClearResult(
            cleared=True,
            scope=scope,
            summary="Cleared session history and session summaries." if deleted_rows else "Session history was already clear.",
        )

    if scope == "project":
        repo_state = store.get_latest_repo_state()
        if repo_state is None:
            return MemoryClearResult(
                cleared=False,
                scope=scope,
                summary="No tracked project memory was found.",
            )
        deleted_rows = store.clear_project_state(repo_key=repo_state.repo_key)
        materialize_managed_memory_state(resolved_config_dir)
        store.vacuum()
        return MemoryClearResult(
            cleared=True,
            scope=scope,
            summary="Cleared tracked memory for the current project." if deleted_rows else "Current project memory was already clear.",
        )

    if scope == "factory":
        store.clear_factory_state()
        resolved_config_file = config_file or (resolved_config_dir / "config.json")
        if resolved_config_file.exists():
            resolved_config_file.unlink()
        initialize_managed_memory_state(resolved_config_dir, contract_source=contract_source)
        store.vacuum()
        return MemoryClearResult(
            cleared=True,
            scope=scope,
            summary="Cleared Duckln state and reset managed memory.",
        )

    raise ValueError(f"Unsupported memory clear scope: {scope}")


def _upsert_and_materialize_memory(
    config_dir: str | PathLike[str] | Path,
    *,
    memory_key: str,
    memory_kind: str,
    relative_path: str,
    title: str | None,
    content: str,
) -> Path:
    resolved_config_dir = _as_path(config_dir)
    initialize_managed_memory_state(resolved_config_dir)
    store = initialize_state_store(resolved_config_dir)
    store.upsert_managed_memory(
        memory_key=memory_key,
        memory_kind=memory_kind,
        relative_path=relative_path,
        title=title,
        content=content,
    )
    materialize_managed_memory_state(resolved_config_dir)
    paths = resolve_agent_memory_paths(resolved_config_dir)
    if relative_path == AGENTS_FILE_NAME:
        return paths.agents_file
    if relative_path == SOUL_FILE_NAME:
        return paths.soul_file
    if relative_path == USER_FILE_NAME:
        return paths.user_file
    if relative_path == IDENTITY_FILE_NAME:
        return paths.identity_file
    if relative_path == TOOLS_DOC_FILE_NAME:
        return paths.tools_doc_file
    if relative_path == BOOTSTRAP_FILE_NAME:
        return paths.bootstrap_file
    if relative_path == TOOLS_FILE_NAME:
        return paths.tools_file
    if relative_path.startswith(f"{SKILLS_DIR_NAME}/"):
        return paths.skills_dir / relative_path.removeprefix(f"{SKILLS_DIR_NAME}/")
    if relative_path.startswith(f"{KNOWLEDGE_DIR_NAME}/"):
        return paths.knowledge_dir / relative_path.removeprefix(f"{KNOWLEDGE_DIR_NAME}/")
    if relative_path.startswith(f"{SESSIONS_DIR_NAME}/"):
        return paths.sessions_dir / relative_path.removeprefix(f"{SESSIONS_DIR_NAME}/")
    if relative_path.startswith(f"{SUBAGENTS_DIR_NAME}/"):
        return paths.subagents_dir / relative_path.removeprefix(f"{SUBAGENTS_DIR_NAME}/")
    raise ValueError(f"Unsupported managed memory path: {relative_path}")


def _load_contract_content(contract_source: Path | None) -> str:
    if contract_source is not None and contract_source.exists():
        return contract_source.read_text(encoding="utf-8")
    return DEFAULT_AGENTS_STUB


def _ensure_core_memory_contracts(config_dir: Path) -> None:
    store = initialize_state_store(config_dir)
    config_snapshot = read_config_snapshot(config_dir)
    user_alias = str(config_snapshot.get("user_name") or "there").strip() or "there"
    execution_target = str(config_snapshot.get("execution_target") or "local").strip() or "local"
    # Plan 155 F8: include user-registered tool/MCP extensions so the agent can see + call them.
    from duckln.tool_registry import tool_registry_entries
    tools_manifest = render_tools_manifest(
        entries=tool_registry_entries(config_dir),
        policy=ToolVisibilityPolicy(execution_target=execution_target),
    )
    store.upsert_managed_memory(
        memory_key="tools:root",
        memory_kind="tools",
        relative_path=TOOLS_FILE_NAME,
        title="Duckln Tool Policy",
        content=tools_manifest.content,
        metadata={"managed_by": "duckln-runtime"},
    )
    store.upsert_managed_memory(
        memory_key="soul:root",
        memory_kind="soul",
        relative_path=SOUL_FILE_NAME,
        title="Duckln Soul",
        content=_render_soul_contract(),
        metadata={"managed_by": "duckln-runtime"},
    )
    store.upsert_managed_memory(
        memory_key="identity:root",
        memory_kind="identity",
        relative_path=IDENTITY_FILE_NAME,
        title="Duckln Identity",
        content=_render_identity_contract(),
        metadata={"managed_by": "duckln-runtime"},
    )
    store.upsert_managed_memory(
        memory_key="user:root",
        memory_kind="user",
        relative_path=USER_FILE_NAME,
        title="Duckln User Context",
        content=_render_user_contract(config_snapshot=config_snapshot, user_alias=user_alias),
        metadata={"managed_by": "duckln-runtime"},
    )
    store.upsert_managed_memory(
        memory_key="tools-doc:root",
        memory_kind="tools_doc",
        relative_path=TOOLS_DOC_FILE_NAME,
        title="Duckln Tool Guidance",
        content=_render_tools_doc(execution_target=execution_target),
        metadata={"managed_by": "duckln-runtime"},
    )
    store.upsert_managed_memory(
        memory_key="bootstrap:root",
        memory_kind="bootstrap",
        relative_path=BOOTSTRAP_FILE_NAME,
        title="Duckln Bootstrap",
        content=_render_bootstrap_contract(config_snapshot=config_snapshot, user_alias=user_alias),
        metadata={"managed_by": "duckln-runtime"},
    )
    for contract in default_subagent_contracts():
        store.upsert_managed_memory(
            memory_key=f"subagent:{contract.slug}:{contract.relative_path}",
            memory_kind="subagent",
            relative_path=contract.relative_path,
            title=contract.title,
            content=contract.content,
            metadata={"managed_by": "duckln-runtime"},
        )


def read_workspace_prompt_sections(
    config_dir: str | PathLike[str] | Path | None,
    *,
    relative_paths: tuple[str, ...] = (
        IDENTITY_FILE_NAME,
        SOUL_FILE_NAME,
        USER_FILE_NAME,
        TOOLS_DOC_FILE_NAME,
    ),
) -> dict[str, str]:
    """Read concise agent-workspace sections for prompt layering."""

    if config_dir is None:
        return {}
    resolved_config_dir = _as_path(config_dir)
    materialize_managed_memory_state(resolved_config_dir)
    store = initialize_state_store(resolved_config_dir)
    sections: dict[str, str] = {}
    for record in store.list_managed_memory_records():
        if record.relative_path in relative_paths:
            sections[record.relative_path] = record.content.strip()
    return sections


def read_subagent_workspace_sections(
    config_dir: str | PathLike[str] | Path | None,
    *,
    slug: str,
) -> dict[str, str]:
    """Read one subagent's scoped workspace contract."""

    return read_workspace_prompt_sections(
        config_dir,
        relative_paths=(
            f"{SUBAGENTS_DIR_NAME}/{slug}/AGENTS.md",
            f"{SUBAGENTS_DIR_NAME}/{slug}/SOUL.md",
            f"{SUBAGENTS_DIR_NAME}/{slug}/TOOLS.md",
        ),
    )


def read_supervisor_workspace_sections(
    config_dir: str | PathLike[str] | Path | None,
    *,
    route_family: str | None,
) -> dict[str, str]:
    """Return only the workspace files needed for the current supervisor turn."""

    relative_paths = [SOUL_FILE_NAME]
    social_families = {
        None,
        "social",
        "rapport",
        "help_request",
        "capability",
        "confidence",
        "user_identity_meta",
        "user_alias",
    }
    repair_families = {
        "conversation_repair",
        "conversation_restate",
        "clarify",
        "utility_fallback",
    }
    repo_context_families = {
        "repo_overview",
        "repo_memory_meta",
        "repo_inventory",
        "repo_status",
        "repo_verify",
        "repo_active_explanation",
        "repo_logs",
        "recommendation_followup",
        "repo_recommendation_rationale",
        "repo_alternatives",
    }
    action_families = {
        "repo_capability_coverage",
        "repo_recommendation_single",
        "repo_recommendation_compare",
        "repo_recommendation_rationale",
        "repo_alternatives",
        "repo_requirements",
        "repo_fit",
        "repo_status",
        "repo_verify",
        "repo_access",
        "repo_run",
        "repo_stop",
        "repo_logs",
        "repo_remove",
        "system_verify",
        "workflow_action",
        "slash_runtime_followup",
        "next_step_guidance",
    }
    if route_family in social_families:
        relative_paths.insert(0, IDENTITY_FILE_NAME)
        relative_paths.append(USER_FILE_NAME)
    elif route_family in repair_families:
        relative_paths.append(USER_FILE_NAME)
    elif route_family in repo_context_families or route_family in action_families:
        relative_paths.append(USER_FILE_NAME)
    if route_family in action_families:
        relative_paths.append(TOOLS_DOC_FILE_NAME)
    snapshot = read_config_snapshot(config_dir) if config_dir is not None else {}
    onboarding_complete = str(snapshot.get("onboarding_complete") or "").lower() == "true"
    if not onboarding_complete and route_family in social_families:
        relative_paths.append(BOOTSTRAP_FILE_NAME)
    return read_workspace_prompt_sections(config_dir, relative_paths=tuple(relative_paths))


def _render_soul_contract() -> str:
    return (
        "# Duckln Soul\n\n"
        "Duckln should sound calm, capable, warm, and terminal-native.\n\n"
        "- Be direct without being cold.\n"
        "- Prefer short readable blocks over dense paragraphs.\n"
        "- Keep greetings alive and varied.\n"
        "- Do not sound like a router or a status feed.\n"
        "- Carry the right context, not just the latest context.\n"
    )


def _render_identity_contract() -> str:
    return (
        "# Duckln Identity\n\n"
        "Duckln is a terminal companion for running AI and ML projects locally or in an Ubuntu VM.\n\n"
        "- Duckln helps with repo setup, debugging, verification, and bounded execution.\n"
        "- Duckln keeps the user in control.\n"
        "- Duckln is not a generic chatbot and should not sound like one.\n"
    )


def _render_user_contract(*, config_snapshot: dict[str, str], user_alias: str) -> str:
    last_alias_change = str(config_snapshot.get("user.alias_changed_at") or "").strip()
    last_repo = str(config_snapshot.get("session.last_repo_name") or "").strip()
    lines = [
        "# Duckln User Context",
        "",
        f"- Preferred alias: {user_alias}",
        "- This alias was explicitly provided by the user and should be used naturally in greetings and direct replies." if user_alias != "there" else "- The user has not picked a preferred alias yet.",
    ]
    if last_alias_change:
        lines.append(f"- Last alias update: {last_alias_change}")
    if last_repo:
        lines.append(f"- Last repo Duckln worked on with this user: {last_repo}")
    return "\n".join(lines)


def _render_tools_doc(*, execution_target: str) -> str:
    target_line = "local machine" if execution_target != "vm" else "active Multipass Ubuntu VM"
    ordered_tools = ", ".join(active_tool_order(policy=ToolVisibilityPolicy(execution_target=execution_target)))
    return (
        "# Duckln Tool Guidance\n\n"
        f"Duckln should reason about tools for the current target: {target_line}.\n\n"
        "- Shell commands are for bounded inspection, setup, verification, runtime control, and controlled fixes.\n"
        "- Filesystem read/search tools should be used before broad guessing.\n"
        "- Filesystem write/patch tools should be used only for the smallest necessary change.\n"
        "- Process/session tools should track live repo runs, stop actions, and runtime logs.\n"
        "- Web lookup should be used only when fresh remote information is actually needed.\n"
        "- Duckln should tell the user what tool or runtime action it is taking before it acts.\n"
        "- If specialists or agents hand work to each other, Duckln should surface that handoff clearly.\n"
        "- Multipass is the approved Ubuntu VM path.\n"
        "- tools.json is the machine-readable source of truth for tool availability.\n"
        f"- Active tool order for this target: {ordered_tools}.\n"
        "- Prefer the smallest useful tool for the current task.\n"
        "- Do not assume host GPU or MPS acceleration exists inside a VM.\n"
    )


def _render_bootstrap_contract(*, config_snapshot: dict[str, str], user_alias: str) -> str:
    onboarding_complete = str(config_snapshot.get("onboarding_complete") or "").lower() == "true"
    if onboarding_complete and user_alias != "there":
        return (
            "# Duckln Bootstrap\n\n"
            "Bootstrap is complete.\n\n"
            f"- Preferred alias is {user_alias}.\n"
            "- Keep greetings brief, varied, and grounded in the latest useful context.\n"
        )
    return (
        "# Duckln Bootstrap\n\n"
        "First-run ritual:\n\n"
        "- Introduce Duckln briefly.\n"
        "- Explain that Duckln helps run AI/ML repos locally or in an Ubuntu VM.\n"
        "- Ask what alias Duckln should use.\n"
        "- Keep the opening warm, short, and terminal-native.\n"
    )


def _normalize_summary_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Managed memory summaries cannot be empty.")
    return normalized


def _slugify(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value.strip()).strip(
        "-._"
    ) or "memory-note"


# --- Plan 67: Plan Mode persistence ------------------------------------------

# SQLite config-store keys used by Plan Mode. The pending plan is stored as a
# single JSON blob under a versioned key. History is appended to a separate
# JSON array. The on-disk markdown mirror lives at memory/plans/<plan_id>.md
# but the SQLite store remains the source of truth.

PLAN_MODE_STATE_PREFIX = "plan_mode."
PLAN_MODE_PENDING_KEY = "plan_mode.pending"
PLAN_MODE_HISTORY_KEY = "plan_mode.history"
PLAN_MODE_HISTORY_LIMIT = 50


def write_pending_plan(
    config_dir: str | PathLike[str] | Path,
    plan_payload: dict,
) -> None:
    """Persist the currently-pending plan as a JSON blob.

    `plan_payload` is the dict form of a PlanRecord (caller passes
    ``plan.to_dict()``). Overwrites any previously-pending plan.
    """

    store = initialize_state_store(_as_path(config_dir))
    store.upsert_config_values({PLAN_MODE_PENDING_KEY: json.dumps(plan_payload)})


def read_pending_plan(
    config_dir: str | PathLike[str] | Path,
) -> dict | None:
    """Return the pending plan payload dict, or None when no plan is pending."""

    snapshot = read_config_snapshot(config_dir)
    raw = snapshot.get(PLAN_MODE_PENDING_KEY)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def clear_pending_plan(config_dir: str | PathLike[str] | Path) -> None:
    """Remove any pending plan from the store."""

    initialize_state_store(_as_path(config_dir)).delete_config_values((PLAN_MODE_PENDING_KEY,))


def append_plan_history(
    config_dir: str | PathLike[str] | Path,
    plan_payload: dict,
    decision: str,
) -> None:
    """Append a one-line history record for an approved/rejected/completed plan.

    History is bounded to PLAN_MODE_HISTORY_LIMIT entries; oldest fall off.
    """

    store = initialize_state_store(_as_path(config_dir))
    snapshot = read_config_snapshot(config_dir)
    raw = snapshot.get(PLAN_MODE_HISTORY_KEY) or "[]"
    try:
        history = json.loads(raw)
    except json.JSONDecodeError:
        history = []
    if not isinstance(history, list):
        history = []
    entry = {
        "plan_id": plan_payload.get("plan_id"),
        "objective": plan_payload.get("objective"),
        "repo_slug": plan_payload.get("repo_slug"),
        "decision": decision,
        "step_count": len(plan_payload.get("steps") or []),
        "amendment_count": plan_payload.get("amendment_count", 0),
        "recorded_at": plan_payload.get("created_at"),
    }
    history.append(entry)
    if len(history) > PLAN_MODE_HISTORY_LIMIT:
        history = history[-PLAN_MODE_HISTORY_LIMIT:]
    store.upsert_config_values({PLAN_MODE_HISTORY_KEY: json.dumps(history)})


def list_plan_history(
    config_dir: str | PathLike[str] | Path,
    *,
    limit: int = 20,
) -> tuple[dict, ...]:
    """Return up to `limit` most recent plan-history entries (newest first)."""

    snapshot = read_config_snapshot(config_dir)
    raw = snapshot.get(PLAN_MODE_HISTORY_KEY) or "[]"
    try:
        history = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(history, list):
        return ()
    return tuple(reversed(history[-max(0, limit):]))


def clear_plan_history(config_dir: str | PathLike[str] | Path) -> None:
    """Wipe the plan history record."""

    initialize_state_store(_as_path(config_dir)).delete_config_values((PLAN_MODE_HISTORY_KEY,))
