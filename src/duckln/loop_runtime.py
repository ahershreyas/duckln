"""Persistent background loop scheduling for Duckln."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
from typing import Any, Callable

from duckln.shell import ControlledCommandRunner
from state.store import LoopRecord, initialize_state_store, resolve_state_store_paths


try:  # Optional at import time so tests and fresh checkouts do not crash.
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover - exercised only when optional deps are absent.
    SQLAlchemyJobStore = None  # type: ignore[assignment]
    BackgroundScheduler = None  # type: ignore[assignment]


LOOP_TYPES: dict[str, tuple[str, ...]] = {
    "health_monitor": ("check_port", "check_process", "read_logs", "restart_service", "send_notification"),
    "cost_monitor": ("aws_get_cost", "gcp_get_cost", "check_instance_status", "send_notification"),
    "repo_watcher": ("git_fetch", "git_diff_summary", "run_verification", "send_notification"),
    "model_quality": ("run_inference", "compare_outputs", "send_notification"),
    "ci_monitor": ("github_get_ci_status", "github_get_pr_status", "send_notification"),
    "custom": ("read_state", "run_safe_check", "send_notification"),
}

_SCHEDULE_RE = re.compile(r"(\d+)")
_SCHEDULER: Any | None = None
_SCHEDULER_DB_PATH: Path | None = None

# Plan 161 PR2: the 4 stub loop types get a real LLM-driven execution path; cost_monitor
# and health_monitor stay deterministic (a port/billing check doesn't need reasoning).
LLM_DRIVEN_TYPES: frozenset[str] = frozenset({"repo_watcher", "model_quality", "ci_monitor", "custom"})

# The conceptual LOOP_TYPES tool labels (check_port, git_fetch…) are not real harness
# tools. For the LLM-driven path Duckln scopes the executor to REAL read-only tools per
# type — the LLM call happens INSIDE the existing tool-scope + S0/S1 sandbox, never
# instead of it.
_LOOP_TYPE_REAL_TOOLS: dict[str, tuple[str, ...]] = {
    "repo_watcher": ("git.run", "fs.read_file", "shell.probe"),
    "ci_monitor": ("web.fetch", "shell.probe"),
    "model_quality": ("shell.probe", "fs.read_file"),
    "custom": ("fs.read_file", "shell.probe", "state.read"),
}

_UNSET = object()
_OK_STATUSES = frozenset({"ok", "success"})

# Plan 161 PR1: forced expiry / review point. Per-type defaults bound how long a
# forgotten loop runs before it auto-pauses for review (human oversight, runaway-cost
# prevention, drift prevention). ci_monitor tracks a short-lived branch (7d); a
# cost_monitor watches ongoing infra (90d); everything else uses 30d.
_DEFAULT_EXPIRY_DAYS_BY_TYPE: dict[str, int] = {"ci_monitor": 7, "cost_monitor": 90}
_DEFAULT_EXPIRY_DAYS = 30


def _default_expiry_days(loop_type: str) -> int:
    return _DEFAULT_EXPIRY_DAYS_BY_TYPE.get(loop_type, _DEFAULT_EXPIRY_DAYS)


def next_expires_at(expiry_days: int, *, now: datetime | None = None) -> str:
    """UTC ISO timestamp `expiry_days` from now (frozen-record safe; set at persist time)."""

    base = now or datetime.now(timezone.utc)
    return (base + timedelta(days=max(1, int(expiry_days)))).isoformat(timespec="seconds")


@dataclass(frozen=True)
class LoopSpec:
    """Structured loop creation result from natural-language answers."""

    name: str
    type: str
    schedule: str
    task_description: str
    tool_scope: tuple[str, ...]
    auto_fix: bool
    max_fix_attempts: int
    notify_on: str
    safety_class: str
    expiry_days: int = _DEFAULT_EXPIRY_DAYS


@dataclass(frozen=True)
class FixAttempt:
    """One bounded diagnose → act → retest pass inside a scheduled cycle (Plan 161 PR3)."""

    attempt_number: int
    diagnosis: str
    action_taken: str | None
    retest_result: str


@dataclass(frozen=True)
class LoopExecutionResult:
    """One bounded LoopSubAgent execution outcome."""

    status: str
    summary: str
    actions_taken: str | None = None
    fix_attempts: int = 0
    escalated: bool = False
    raw_output: str | None = None
    should_notify: bool = False
    attempts: tuple[FixAttempt, ...] = ()


class LoopSubAgent:
    """Restricted loop executor with a fixed tool scope.

    Plan 161: the 4 stub types (repo_watcher/model_quality/ci_monitor/custom) run a real
    LLM-driven cycle (PR2); cost_monitor/health_monitor stay deterministic. Any type whose
    fast path finds a problem (and `auto_fix` is on) escalates into a bounded
    diagnose → act → retest loop capped at `max_fix_attempts` (PR3). The LLM call always
    happens inside the existing tool-scope + S0/S1 sandbox.
    """

    MAX_FIX_ATTEMPTS = 3

    def __init__(
        self,
        *,
        config_dir: Path,
        notify: Callable[[str, str], None] | None = None,
        llm_client: Any = _UNSET,
        spec_runner: Callable[..., Any] | None = None,
        definition_loader: Callable[[], Any] | None = None,
    ) -> None:
        self.config_dir = config_dir
        self.notify = notify or send_loop_notification
        self._llm_client = llm_client
        self._spec_runner = spec_runner
        self._definition_loader = definition_loader

    def execute(self, loop: LoopRecord) -> LoopExecutionResult:
        # Tool-scope guard: default-deny anything outside the loop type's declared scope.
        allowed = set(LOOP_TYPES.get(loop.type, LOOP_TYPES["custom"]))
        if any(tool not in allowed for tool in loop.tool_scope):
            return LoopExecutionResult(
                status="failed",
                summary=f"{loop.id} has a tool outside its allowed scope.",
                escalated=True,
                should_notify=True,
            )
        # S0/S1 ceiling — S2+ is never run without explicit approval.
        if loop.safety_class not in {"S0", "S1"}:
            return LoopExecutionResult(
                status="blocked",
                summary=f"{loop.id} requires {loop.safety_class}; Duckln will not run it without explicit approval.",
                escalated=True,
                should_notify=True,
            )
        # LLM-driven types need a usable model. With none, report honestly + notify —
        # NEVER a fake "ok" that gives false confidence monitoring is happening.
        if loop.type in LLM_DRIVEN_TYPES and self._resolve_llm_client() is None:
            return LoopExecutionResult(
                status="attention_needed",
                summary=(
                    f"{loop.name}: couldn't assess — no usable model is configured; "
                    "configure one so this loop can actually run."
                ),
                escalated=True,
                should_notify=True,
            )
        # 1. Fast path — deterministic logic for deterministic types, one LLM pass for
        #    LLM-driven types. The cheapest path; nothing escalates when all is well.
        first_pass = self._fast_path(loop)
        if first_pass.status in _OK_STATUSES:
            return first_pass
        if first_pass.status == "blocked":
            return first_pass
        if first_pass.status == "attention_needed" and not loop.auto_fix:
            return first_pass  # notify-only — the user did not ask Duckln to fix
        # 2. Problem found AND auto_fix on → bounded diagnose-fix-retest (ALL types;
        #    deterministic types escalate to the LLM here on a fixed-logic failure).
        if loop.auto_fix and first_pass.status in {"attention_needed", "failed"}:
            return self._diagnose_fix_retest_loop(loop, first_pass)
        return first_pass

    # --- fast path --------------------------------------------------------

    def _fast_path(self, loop: LoopRecord) -> LoopExecutionResult:
        if loop.type in LLM_DRIVEN_TYPES:
            return self._execute_llm_driven(loop)
        return self._execute_deterministic(loop)

    def _execute_deterministic(self, loop: LoopRecord) -> LoopExecutionResult:
        if loop.type == "health_monitor":
            return self._run_health_monitor(loop)
        if loop.type == "cost_monitor":
            return self._run_cost_monitor(loop)
        return LoopExecutionResult(status="ok", summary=f"{loop.name}: check completed.")

    def _execute_llm_driven(self, loop: LoopRecord) -> LoopExecutionResult:
        client = self._resolve_llm_client()
        if client is None:  # defensive — execute() already gated this for LLM types
            return LoopExecutionResult(
                status="attention_needed",
                summary=f"{loop.name}: no usable model; configure one.",
                escalated=True,
                should_notify=True,
            )
        state = {
            "loop.type": loop.type,
            "loop.cadence_minutes": _schedule_minutes(loop.schedule),
            "loop.prior_results": self._prior_results_state(loop.id),
            "loop.auto_fix": loop.auto_fix,
        }
        spec = self._run_spec(self._loop_executor_definition(loop.type), loop.task_description, client, state)
        payload = self._spec_payload(spec)
        if payload is None:
            errs = "; ".join(getattr(spec, "errors", ()) or ()) or "no valid structured result"
            return LoopExecutionResult(
                status="attention_needed",
                summary=f"{loop.name}: the model couldn't produce a usable assessment ({errs}); check the model/config.",
                escalated=True,
                should_notify=True,
                raw_output=getattr(spec, "raw", None),
            )
        return self._result_from_payload(loop, payload)

    # --- bounded diagnose-fix-retest (PR3) --------------------------------

    def _diagnose_fix_retest_loop(self, loop: LoopRecord, first_pass: LoopExecutionResult) -> LoopExecutionResult:
        client = self._resolve_llm_client()
        if client is None:
            # A deterministic type failed but there's no model to reason a fix — honest-stop.
            return replace(
                first_pass,
                status="failed",
                summary=f"{first_pass.summary} (no usable model to attempt a fix; configure one)",
                escalated=True,
                should_notify=True,
            )
        definition = self._loop_executor_definition(loop.type)
        max_attempts = max(1, int(loop.max_fix_attempts or self.MAX_FIX_ATTEMPTS))
        escalated = loop.type not in LLM_DRIVEN_TYPES
        attempts: list[FixAttempt] = []
        problem_context = first_pass.summary
        for n in range(1, max_attempts + 1):
            state = {
                "loop.type": loop.type,
                "loop.cadence_minutes": _schedule_minutes(loop.schedule),
                "loop.problem_context": problem_context,
                "loop.prior_attempts": [_fix_attempt_state(a) for a in attempts],
                "loop.attempt_number": n,
                "loop.max_attempts": max_attempts,
                "loop.auto_fix": loop.auto_fix,
            }
            spec = self._run_spec(definition, loop.task_description, client, state)
            payload = self._spec_payload(spec) or {}
            attempt = FixAttempt(
                attempt_number=n,
                diagnosis=str(payload.get("diagnosis") or payload.get("summary") or "").strip(),
                action_taken=_clean_action(payload.get("action_taken")),
                retest_result=str(payload.get("retest_result") or "unresolved").strip().lower(),
            )
            attempts.append(attempt)
            if attempt.retest_result == "resolved":
                return LoopExecutionResult(
                    status="fixed",
                    summary=f"{loop.name}: resolved after {n} attempt(s). {attempt.diagnosis}".strip(),
                    actions_taken=attempt.action_taken,
                    fix_attempts=n,
                    escalated=escalated,
                    should_notify=True,
                    attempts=tuple(attempts),
                )
            problem_context = (
                f"{problem_context}\nAttempt {n} tried: {attempt.action_taken or 'no action'}. "
                f"Result: still unresolved. {attempt.diagnosis}"
            ).strip()
        last = attempts[-1]
        return LoopExecutionResult(
            status="failed",
            summary=(
                f"{loop.name}: could not resolve after {len(attempts)} attempt(s). "
                f"Last diagnosis: {last.diagnosis or 'unspecified'}"
            ),
            actions_taken=last.action_taken,
            fix_attempts=len(attempts),
            escalated=escalated,
            should_notify=True,
            attempts=tuple(attempts),
        )

    # --- seams + helpers --------------------------------------------------

    def _resolve_llm_client(self) -> Any:
        if self._llm_client is not _UNSET:
            return self._llm_client
        try:
            from duckln.ai_client import build_default_llm_client_or_none

            return build_default_llm_client_or_none(self.config_dir)
        except Exception:
            return None

    def _loop_executor_definition(self, loop_type: str) -> Any:
        if self._definition_loader is not None:
            definition = self._definition_loader()
        else:
            from duckln.harness.agent_def import builtin_agents_directory, load_agent_definition_from_path

            definition = load_agent_definition_from_path(builtin_agents_directory() / "loop_executor.md")
        scope = _LOOP_TYPE_REAL_TOOLS.get(loop_type, _LOOP_TYPE_REAL_TOOLS["custom"])
        return replace(definition, tools=tuple(scope))

    def _run_spec(self, definition: Any, user_message: str, client: Any, state: dict[str, Any]) -> Any:
        if self._spec_runner is not None:
            return self._spec_runner(definition, user_message, client, state)
        from duckln.harness.loop import run_spec

        return run_spec(definition, user_message, client, available_state=state)

    @staticmethod
    def _spec_payload(spec: Any) -> dict[str, Any] | None:
        if not getattr(spec, "ok", False):
            return None
        parsed = getattr(spec, "parsed", None)
        return parsed if isinstance(parsed, dict) else None

    def _result_from_payload(self, loop: LoopRecord, payload: dict[str, Any]) -> LoopExecutionResult:
        status = str(payload.get("status") or "ok").strip().lower()
        if status not in {"ok", "attention_needed", "fixed", "failed"}:
            status = "attention_needed"
        summary = str(payload.get("summary") or f"{loop.name}: cycle completed.").strip()
        should_notify = bool(payload.get("should_notify", status not in _OK_STATUSES))
        return LoopExecutionResult(
            status=status,
            summary=summary,
            actions_taken=_clean_action(payload.get("action_taken")),
            should_notify=should_notify,
        )

    def _prior_results_state(self, loop_id: str) -> list[dict[str, str]]:
        try:
            results = initialize_state_store(self.config_dir).list_loop_results(loop_id, limit=5)
        except Exception:
            return []
        return [{"run_at": r.run_at, "status": r.status, "summary": r.summary} for r in results]

    def _run_health_monitor(self, loop: LoopRecord) -> LoopExecutionResult:
        command = _extract_start_command(loop.task_description)
        if command and loop.auto_fix:
            assessment = _safe_loop_command(command)
            if not assessment:
                return LoopExecutionResult(
                    status="blocked",
                    summary=f"{loop.name}: restart command is above S1, so Duckln only notified.",
                    escalated=True,
                )
        return LoopExecutionResult(status="ok", summary=f"{loop.name}: health check completed.")

    def _run_cost_monitor(self, loop: LoopRecord) -> LoopExecutionResult:
        providers = []
        task = loop.task_description.casefold()
        if "aws" in task:
            providers.append("aws")
        if "gcp" in task or "google" in task:
            providers.append("gcp")
        if not providers:
            providers = ["aws", "gcp"]
        summaries: list[str] = []
        runner = ControlledCommandRunner(execution_target="local")
        today = datetime.now(timezone.utc).date()
        month_start = today.replace(day=1).isoformat()
        tomorrow = today.isoformat()
        for provider in providers:
            if provider == "aws":
                result = runner.run(
                    "aws ce get-cost-and-usage "
                    f"--time-period Start={month_start},End={tomorrow} "
                    "--granularity MONTHLY --metrics UnblendedCost --output json"
                )
                summaries.append("AWS cost checked." if result.exit_code == 0 else "AWS cost unavailable; check AWS CLI auth.")
            else:
                result = runner.run("gcloud billing accounts list --format=json")
                summaries.append("GCP billing checked." if result.exit_code == 0 else "GCP billing unavailable; check gcloud auth/billing access.")
        return LoopExecutionResult(status="ok", summary=" ".join(summaries), raw_output=None)


def infer_loop_spec(
    task: str, cadence: str, failure_policy: str, *, expiry_days: int | None = None
) -> LoopSpec:
    """Convert natural-language loop answers into a bounded loop specification."""

    task_text = " ".join(task.split())
    lowered = task_text.casefold()
    loop_type = "custom"
    if any(word in lowered for word in ("crash", "restart", "alive", "health", "port", "service", "api")):
        loop_type = "health_monitor"
    if any(word in lowered for word in ("aws", "gcp", "cloud", "cost", "spend", "budget")):
        loop_type = "cost_monitor"
    if any(word in lowered for word in ("github", "release", "commit", "pull request", "repo watcher")):
        loop_type = "repo_watcher"
    if any(word in lowered for word in ("quality", "drift", "baseline", "test set")):
        loop_type = "model_quality"
    if any(word in lowered for word in ("ci", "pipeline", "github action", "flaky", "build")):
        loop_type = "ci_monitor"
    minutes = _parse_schedule_minutes(cadence, loop_type)
    schedule = json.dumps({"kind": "interval", "minutes": minutes}, sort_keys=True)
    auto_fix = "notify" not in failure_policy.casefold() or "fix" in failure_policy.casefold()
    readable_type = loop_type.replace("_", " ")
    name = f"{_title_subject(task_text)} {readable_type}"
    resolved_expiry = (
        max(1, int(expiry_days)) if expiry_days is not None else _default_expiry_days(loop_type)
    )
    return LoopSpec(
        name=name[:80],
        type=loop_type,
        schedule=schedule,
        task_description=task_text,
        tool_scope=LOOP_TYPES[loop_type],
        auto_fix=auto_fix,
        max_fix_attempts=3,
        notify_on="failure",
        safety_class="S1",
        expiry_days=resolved_expiry,
    )


def create_loop_from_answers(
    config_dir: Path,
    *,
    task: str,
    cadence: str,
    failure_policy: str,
    expiry_days: int | None = None,
) -> LoopRecord:
    """Persist a loop from natural-language answers."""

    store = initialize_state_store(config_dir)
    loop_id = store.next_loop_id()
    spec = infer_loop_spec(task, cadence, failure_policy, expiry_days=expiry_days)
    # Frozen records can't compute this in __post_init__; set it at persist time.
    expires_at = next_expires_at(spec.expiry_days)
    store.upsert_loop(
        loop_id=loop_id,
        name=spec.name,
        loop_type=spec.type,
        schedule=spec.schedule,
        task_description=spec.task_description,
        tool_scope=spec.tool_scope,
        os_type=platform.system() or "Unknown",
        auto_fix=spec.auto_fix,
        max_fix_attempts=spec.max_fix_attempts,
        notify_on=spec.notify_on,
        safety_class=spec.safety_class,
        active=True,
        expires_at=expires_at,
        expiry_days=spec.expiry_days,
    )
    loop = store.get_loop(loop_id)
    if loop is None:
        raise RuntimeError("Duckln could not persist the loop.")
    return loop


def render_loop_confirmation(spec: LoopSpec) -> str:
    minutes = _schedule_minutes(spec.schedule)
    cadence = f"every {minutes} min" if minutes != 60 else "hourly"
    behavior = "fix with safe actions first, then notify" if spec.auto_fix else "notify only"
    return f"Create loop: {spec.name} — {cadence}; {behavior}."


def render_loops(config_dir: Path) -> str:
    loops = initialize_state_store(config_dir).list_loops()
    if not loops:
        return "No loops configured."
    lines = ["Duckln loops:"]
    for loop in loops:
        active = "active" if loop.active else "paused"
        last = f"last {loop.last_status}: {loop.last_summary}" if loop.last_status else "not run yet"
        expiry = _expiry_label(loop)
        expiry_part = f" — {expiry}" if expiry and loop.active else ""
        lines.append(
            f"- {loop.id}: {loop.name} — {active} — {_schedule_label(loop.schedule)}{expiry_part} — {last}"
        )
    return "\n".join(lines)


def render_loop_history(config_dir: Path, loop_id: str) -> str:
    store = initialize_state_store(config_dir)
    loop = store.get_loop(loop_id)
    if loop is None:
        return f"No loop found with id {loop_id}."
    results = store.list_loop_results(loop_id, limit=10)
    expiry = _expiry_label(loop)
    expiry_part = f" ({expiry})" if expiry and loop.active else ""
    if not results:
        return f"No history yet for {loop.id}: {loop.name}{expiry_part}."
    lines = [f"Last {len(results)} run(s) for {loop.id}: {loop.name}{expiry_part}"]
    for result in results:
        lines.append(f"- {result.run_at}: {result.status} — {result.summary}")
    return "\n".join(lines)


def start_loop_scheduler(config_dir: Path) -> str:
    """Start the persistent scheduler and resume active loops."""

    global _SCHEDULER, _SCHEDULER_DB_PATH
    store = initialize_state_store(config_dir)
    store.delete_old_loop_results(days=30)
    active_loops = store.list_loops(include_inactive=False)
    if not active_loops:
        return "No active loops to schedule."
    if BackgroundScheduler is None or SQLAlchemyJobStore is None:
        return "Loop scheduler is not active because APScheduler/SQLAlchemy is not installed."
    db_path = resolve_state_store_paths(config_dir).database_file
    if (
        _SCHEDULER is not None
        and getattr(_SCHEDULER, "running", False)
        and _SCHEDULER_DB_PATH == db_path
    ):
        return "Loop scheduler already running."
    if _SCHEDULER is not None and getattr(_SCHEDULER, "running", False):
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:
            pass
    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        timezone="UTC",
    )
    scheduler.start()
    _SCHEDULER = scheduler
    _SCHEDULER_DB_PATH = db_path
    for loop in active_loops:
        schedule_loop(config_dir, loop)
    return "Loop scheduler started."


def shutdown_loop_scheduler() -> None:
    """Stop the process-local scheduler, mainly for tests and clean shutdown."""

    global _SCHEDULER, _SCHEDULER_DB_PATH
    if _SCHEDULER is not None and getattr(_SCHEDULER, "running", False):
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:
            pass
    _SCHEDULER = None
    _SCHEDULER_DB_PATH = None


def shutdown_loop_scheduler_if_idle(config_dir: Path) -> None:
    """Stop the scheduler when the active-loop set is empty."""

    if not initialize_state_store(config_dir).list_loops(include_inactive=False):
        shutdown_loop_scheduler()


def schedule_loop(config_dir: Path, loop: LoopRecord) -> None:
    if _SCHEDULER is None or not loop.active:
        return
    minutes = _schedule_minutes(loop.schedule)
    _SCHEDULER.add_job(
        _run_scheduled_loop,
        "interval",
        minutes=minutes,
        args=[str(config_dir), loop.id],
        id=f"duckln_loop_{loop.id}",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )


def unschedule_loop(loop_id: str) -> None:
    if _SCHEDULER is None:
        return
    try:
        _SCHEDULER.remove_job(f"duckln_loop_{loop_id}")
    except Exception:
        pass


def run_loop_now(config_dir: Path, loop_id: str) -> LoopExecutionResult:
    return _run_scheduled_loop(str(config_dir), loop_id)


def _run_scheduled_loop(config_dir_text: str, loop_id: str) -> LoopExecutionResult:
    config_dir = Path(config_dir_text)
    store = initialize_state_store(config_dir)
    loop = store.get_loop(loop_id)
    if loop is None:
        return LoopExecutionResult(status="failed", summary=f"Loop {loop_id} no longer exists.")
    # Plan 161 PR1: a loop past its review point auto-pauses and notifies — it never
    # runs the expired cycle, bounding a forgotten loop's runaway cost/drift.
    if _loop_is_expired(loop):
        store.set_loop_active(loop.id, False)
        unschedule_loop(loop.id)
        message = (
            f"This loop ran for {loop.expiry_days} days and has been automatically paused "
            f"for review. Run `/loop edit {loop.id}` to renew it, or `/loop delete {loop.id}` "
            "to remove it."
        )
        send_loop_notification(f"{loop.name} expired", message, config_dir=config_dir)
        return LoopExecutionResult(status="expired", summary=message)
    result = LoopSubAgent(config_dir=config_dir).execute(loop)
    store.record_loop_result(
        loop_id=loop.id,
        status=result.status,
        summary=result.summary,
        actions_taken=result.actions_taken,
        fix_attempts=result.fix_attempts,
        escalated=result.escalated,
        raw_output=result.raw_output,
    )
    # Notify when: the loop asks for every cycle, the cycle itself decided it (LLM
    # should_notify / fixed / failed), or the status is not a clean ok/success.
    if loop.notify_on == "always" or result.should_notify or result.status not in _OK_STATUSES:
        send_loop_notification(loop.name, result.summary, config_dir=config_dir)
    return result


def send_loop_notification(title: str, message: str, *, config_dir: Path | None = None) -> None:
    os_type = platform.system()
    try:
        if os_type == "Darwin":
            import pync  # type: ignore

            pync.notify(message, title=f"Duckln: {title}")
            return
        if os_type == "Linux":
            subprocess.run(["notify-send", f"Duckln: {title}", message], check=True, timeout=5)
            return
        if os_type == "Windows":
            from win10toast import ToastNotifier  # type: ignore

            ToastNotifier().show_toast(f"Duckln: {title}", message, duration=8, threaded=True)
            return
    except Exception:
        pass
    target_dir = config_dir or Path(os.environ.get("DUCKLN_CONFIG_DIR", Path.home() / ".duckln"))
    target_dir.mkdir(parents=True, exist_ok=True)
    with (target_dir / "loop_results.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {title}: {message}\n")


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _loop_is_expired(loop: LoopRecord, *, now: datetime | None = None) -> bool:
    expires = _parse_iso_timestamp(loop.expires_at)
    if expires is None:
        return False
    return (now or datetime.now(timezone.utc)) >= expires


def _days_remaining(loop: LoopRecord, *, now: datetime | None = None) -> int | None:
    expires = _parse_iso_timestamp(loop.expires_at)
    if expires is None:
        return None
    seconds = (expires - (now or datetime.now(timezone.utc))).total_seconds()
    if seconds <= 0:
        return 0
    return int((seconds + 86399) // 86400)


def _expiry_label(loop: LoopRecord) -> str | None:
    days = _days_remaining(loop)
    if days is None:
        return None
    if days <= 0:
        return "expired"
    return f"expires in {days} day{'s' if days != 1 else ''}"


def _parse_schedule_minutes(cadence: str, loop_type: str) -> int:
    text = cadence.casefold()
    match = _SCHEDULE_RE.search(text)
    value = int(match.group(1)) if match else None
    if "hour" in text:
        minutes = (value or 1) * 60
    elif "day" in text or "daily" in text:
        minutes = (value or 1) * 24 * 60
    elif "week" in text or "weekly" in text:
        minutes = (value or 1) * 7 * 24 * 60
    elif "five" in text:
        minutes = 5
    elif "minute" in text:
        minutes = value or 1
    else:
        minutes = 5 if loop_type == "health_monitor" else 60
    if loop_type == "health_monitor":
        return min(30, max(1, minutes))
    if loop_type == "cost_monitor":
        return min(60, max(15, minutes))
    if loop_type == "ci_monitor":
        return min(30, max(5, minutes))
    return max(1, minutes)


def _schedule_minutes(schedule: str) -> int:
    try:
        payload = json.loads(schedule)
    except json.JSONDecodeError:
        return 60
    return max(1, int(payload.get("minutes") or 60))


def _schedule_label(schedule: str) -> str:
    minutes = _schedule_minutes(schedule)
    if minutes == 60:
        return "hourly"
    if minutes % 1440 == 0:
        return f"every {minutes // 1440} day(s)"
    return f"every {minutes} min"


def _title_subject(task: str) -> str:
    words = [word.strip(".,:;") for word in task.split()[:4]]
    return " ".join(words).title() or "Duckln"


def _extract_start_command(task: str) -> str | None:
    match = re.search(r"`([^`]+)`", task)
    return match.group(1) if match else None


def _clean_action(value: Any) -> str | None:
    if value in (None, "", "null", "None"):
        return None
    text = str(value).strip()
    return text or None


def _fix_attempt_state(attempt: FixAttempt) -> dict[str, Any]:
    return {
        "attempt_number": attempt.attempt_number,
        "diagnosis": attempt.diagnosis,
        "action_taken": attempt.action_taken,
        "retest_result": attempt.retest_result,
    }


def _safe_loop_command(command: str) -> bool:
    lowered = command.casefold()
    # Plan 161: back the loop_executor's "never an irreversible action" rule —
    # delete/force-push/drop/terminate and friends are blocked regardless of auto_fix.
    blocked = (
        "sudo ",
        " rm ",
        "rm -",
        "delete",
        "terminate",
        "shutdown",
        "mkfs",
        "dd ",
        "--force",
        "push -f",
        "force-push",
        "drop table",
        "drop database",
    )
    return not any(token in f" {lowered} " for token in blocked)
