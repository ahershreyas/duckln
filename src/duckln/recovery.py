"""Plan 129 — reasoning-based failure recovery.

When a bring-up step fails and the cheap deterministic fast-path
(`diagnostics.match_deterministic_fix`) finds nothing, Duckln should REASON its way
out — investigate with tools, try a bounded-safe fix, re-run, verify, iterate — the
way a real agent does, instead of guessing one command blind. This module holds the
target-agnostic pieces:

- `RecoveryReport` / `RecoveryDecision` — the structured outcome.
- `classify_blast_radius` / `recovery_autonomy` — the safety policy that decides whether
  a proposed fix may auto-apply (keyed on the fix's blast radius + target), so recovery
  is autonomous on disposable sandboxes (VM/container/cloud) AND on the user's Mac for
  repo-scoped reversible fixes, while destructive/secret ops always stop for a human.
- `append_thinking_log` — a persistent, human-readable Claude-style reasoning log.
- `recover_failed_step_with_agent` — drives the existing tool-using agent loop in a
  recovery role and returns a `RecoveryReport` (the agent runner is injectable for tests
  and degrades gracefully when no LLM is configured).

Runs identically on every execution target — local (Mac), multipass VM, container, AWS,
GCP — because the agent's tools already wrap commands per target.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

# Plan 155 F7: a quiet module logger so a recovery-time failure that we deliberately
# swallow (to fall back) is still OBSERVABLE at DEBUG, not silently lost.
_LOG = logging.getLogger("duckln.recovery")


# Plan 157 P4: recovery prompts are SPEC-DRIVEN — sourced from agent .md spec bodies
# (recovery_agent.md / recovery_supervisor.md / planning_investigator.md) rather than the
# retired `_RECOVERY_SYSTEM`/`_SUPERVISOR_SYSTEM`/`_PLANNING_SYSTEM` constants. Edit behavior
# by editing the spec.
from functools import lru_cache as _lru_cache


@_lru_cache(maxsize=None)
def _recovery_spec_prompt(agent_name: str) -> str:
    """The composed system prompt (spec body + output_contract text) for a recovery-side
    agent, loaded once from its `.md` spec."""
    from duckln.harness.agent_def import (
        builtin_agents_directory,
        load_agent_definition_from_path,
        render_output_contract_text,
    )

    definition = load_agent_definition_from_path(builtin_agents_directory() / f"{agent_name}.md")
    contract = render_output_contract_text(definition)
    return definition.system_prompt + (f"\n\n{contract}" if contract else "")

# Plan 155 F1/F6: canonical set + normalization (maps the `docker` alias → `container`).
from duckln.execution_targets import SANDBOX_TARGETS as _SANDBOX_TARGETS, is_sandbox_target

# --- Plan 132 shared reasoning infrastructure --------------------------------

# Hard per-pass bounds (the harness enforces turns/calls between turns; the
# wall-clock timeout is the backstop for a single blocking tool call).
# Plan 142 F3: a REAL investigation (read several files, probe OS/permissions, read the
# error log, search the web) needs more than 6 tool calls — raise the budget so the agent
# can actually dig in, while staying hard-capped (wall-clock daemon + per-bring-up pass cap)
# so cost can't run away.
REASONING_MAX_TURNS = 12
REASONING_BUDGET_SECONDS = 220.0
REASONING_BUDGET_LLM_CALLS = 12
REASONING_WALLCLOCK_SECONDS = 240.0
# Global cap per bring-up so cost can't run away across many failed steps.
REASONING_MAX_PASSES_PER_BRINGUP = 6


_REASONING_CAPABLE_MARKERS = (
    "gpt-4", "gpt-5", "gpt-4o", "o1", "o3", "o4", "claude", "sonnet", "opus", "haiku",
    "gemini-1.5", "gemini-2", "mistral-large", "command-r-plus", "nemotron",
    "70b", "120b", "405b", "8x7b", "8x22b", "deepseek", "qwen2.5-coder", "qwen3",
)


def _name_heuristic_capable(model_id: str) -> bool:
    """Plan 156 P1: the FALLBACK name heuristic (used only when no behavior probe verdict
    is cached). Conservative: assume capable unless the id is clearly small."""
    low = (model_id or "").lower()
    if any(m in low for m in _REASONING_CAPABLE_MARKERS):
        return True
    # Explicit small param sizes (…:1.5b, -3b, _2b) → too small to reason reliably.
    m = re.search(r"[:\-_ ]([0-9]+(?:\.[0-9]+)?)\s*b\b", low)
    if m:
        try:
            return float(m.group(1)) >= 7.0
        except ValueError:
            return True
    return True


# --- Plan 156 P1: behavior-based capability probe (provider-agnostic) --------------------
_CAPABILITY_PROBE_SYSTEM = "You are a setup engineer. Reply with ONLY a JSON object — no markdown, no prose."
_CAPABILITY_PROBE_USER = (
    "A Python step failed: ModuleNotFoundError: No module named 'requests'.\n"
    'Return ONLY this JSON: {"cause": "<one line>", "fix": "<one shell command>"}'
)


def _capability_key(model_id: str) -> str:
    return str(model_id or "").strip().lower()


def read_cached_capability(config_dir, model_id: str) -> str | None:
    """Plan 156 P1: the cached behavior verdict ('capable'/'weak') for a model id, or None."""
    try:
        import json as _json
        from state.access import read_config_snapshot
        raw = read_config_snapshot(config_dir).get("model_capability_verdicts") or "{}"
        return _json.loads(raw).get(_capability_key(model_id))
    except Exception:
        return None


def write_cached_capability(config_dir, model_id: str, verdict: str) -> None:
    try:
        import json as _json
        from state.access import read_config_snapshot, write_config_snapshot
        raw = read_config_snapshot(config_dir).get("model_capability_verdicts") or "{}"
        try:
            verdicts = _json.loads(raw)
        except Exception:
            verdicts = {}
        verdicts[_capability_key(model_id)] = verdict
        write_config_snapshot(config_dir, {"model_capability_verdicts": _json.dumps(verdicts)})
    except Exception:
        pass


def probe_model_reasoning_capability(llm_client, *, model_id: str = "", config_dir=None,
                                     use_cache: bool = True) -> str:
    """Plan 156 P1: BEHAVIOR-test the connected model — can it follow a structured protocol
    (return valid JSON with the expected keys)? Returns 'capable' | 'weak' | 'unknown'.
    Cached per model id, so it runs once. Provider-agnostic — judges Ollama / OpenRouter-free
    models by what they DO, not by name (the name heuristic is only the unprobed fallback)."""
    if use_cache and config_dir is not None and model_id:
        cached = read_cached_capability(config_dir, model_id)
        if cached in ("capable", "weak"):
            return cached
    if llm_client is None:
        return "unknown"
    text = run_bounded_agent(
        lambda: llm_client(system_prompt=_CAPABILITY_PROBE_SYSTEM, user_message=_CAPABILITY_PROBE_USER),
        timeout_seconds=30.0,
    )
    payload = _extract_json_object(text) if text else None
    verdict = "capable" if (isinstance(payload, dict) and str(payload.get("cause") or "").strip()
                            and str(payload.get("fix") or "").strip()) else "weak"
    if config_dir is not None and model_id:
        write_cached_capability(config_dir, model_id, verdict)
        try:
            append_thinking_log(
                config_dir, repo_slug="model-capability", surface="probe",
                title=f"Capability probe — {model_id}",
                lines=[f"Verdict: {verdict} "
                       f"({'follows the structured JSON/tool protocol' if verdict == 'capable' else 'did not return valid structured JSON'})."],
            )
        except Exception:
            pass
    return verdict


def _extract_json_object(text: str):
    """Lenient JSON-object extraction: handles a bare object or one inside ```json fences."""
    import json as _json
    s = str(text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    try:
        return _json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(0))
            except Exception:
                return None
    return None


# --- Plan 160 D1/D2: the probe verdict does OPERATIONAL work ----------------------------
def effective_contract_retries(definition, *, model_id: str = "", config_dir=None) -> int:
    """Plan 160 D1: the spec's `max_contract_retries` for a probe-CAPABLE (or unprobed) model,
    but a REDUCED budget for a probe-WEAK one — so a weak model honest-stops FAST instead of
    burning the full re-ask budget. The probe verdict thus routes behavior, not just a UI note."""
    base = max(0, int(getattr(definition, "max_contract_retries", 2)))
    if config_dir is None or not model_id:
        return base
    if read_cached_capability(config_dir, model_id) == "weak":
        return min(base, 1)  # weak → at most one corrective re-ask before honest-stop
    return base


def resolve_agent_budget(definition, *, model_id: str = "", config_dir=None):
    """Plan 187 F4: select the agent's budget TIER from the OBSERVED capability verdict
    (Plan-156 probe: 'capable'/'weak'), NOT the model name — a surprisingly-strong small
    model earns the generous tier; a flaky one gets the tight leash ("judge by output, not
    label"). ONLY turns/seconds/calls/retries flex; the safety floor (tools, scope, S0 probe
    ceiling, output_contract) is IDENTICAL across tiers.

    For a LOCAL model the tight tier is about CONVERGENCE + LATENCY + answer QUALITY — a local
    model has no billing, but small models loop, re-read, and fail to stop on their own; the
    tight ceiling stops a 9B from churning 15 turns into a noisier answer than it gives in 6.
    Returns the definition UNCHANGED when there are no profiles, no model, or an unknown/absent
    verdict (so the flat default block stands)."""
    profiles = getattr(definition, "budget_profiles", None) or {}
    if not profiles or config_dir is None or not model_id:
        return definition
    verdict = read_cached_capability(config_dir, model_id)
    profile = profiles.get(verdict) if verdict in ("capable", "weak") else None
    if not isinstance(profile, dict) or not profile:
        return definition
    from dataclasses import replace as _replace

    def _pick(key, current, cast):
        try:
            return cast(profile[key]) if key in profile else current
        except Exception:
            return current

    try:
        return _replace(
            definition,
            max_turns=_pick("max_turns", definition.max_turns, int),
            budget_seconds=_pick("budget_seconds", definition.budget_seconds, float),
            budget_llm_calls=_pick("budget_llm_calls", definition.budget_llm_calls, int),
            max_contract_retries=_pick("max_contract_retries", definition.max_contract_retries, int),
        )
    except Exception:
        return definition


_CAPABILITY_PROMOTE_THRESHOLD = 3  # consecutive valid decisions to upgrade weak → capable


def record_capability_observation(config_dir, model_id: str, *, delivered_valid: bool) -> None:
    """Plan 160 D2: record whether the model delivered a VALID contract-compliant decision.
    A probe-WEAK model is NOT promoted on a single lucky pass — it upgrades to `capable` only
    after `_CAPABILITY_PROMOTE_THRESHOLD` consecutive valid deliveries (an N-of-M check); any
    failure resets the streak. A `capable` verdict is already trusted and left as-is."""
    if config_dir is None or not model_id:
        return
    try:
        import json as _json
        from state.access import read_config_snapshot, write_config_snapshot

        if read_cached_capability(config_dir, model_id) == "capable":
            return  # already trusted — nothing to promote
        raw = read_config_snapshot(config_dir).get("model_capability_streaks") or "{}"
        try:
            streaks = _json.loads(raw)
        except Exception:
            streaks = {}
        key = _capability_key(model_id)
        streak = (int(streaks.get(key, 0)) + 1) if delivered_valid else 0
        streaks[key] = streak
        write_config_snapshot(config_dir, {"model_capability_streaks": _json.dumps(streaks)})
        if delivered_valid and streak >= _CAPABILITY_PROMOTE_THRESHOLD:
            write_cached_capability(config_dir, model_id, "capable")
    except Exception:
        pass


def model_is_reasoning_capable(model_id: str, *, config_dir=None) -> bool:
    """Plan 156 P1: prefer the cached BEHAVIOR probe verdict; fall back to the name heuristic
    only when the model hasn't been probed. Used to WARN (not block) — a weak model still
    reasons best-effort with the evidence/supervisor gate; a completely-unreachable model is
    a hard honest-stop (handled at the planning seam, not here)."""
    if config_dir is not None and model_id:
        cached = read_cached_capability(config_dir, model_id)
        if cached == "capable":
            return True
        if cached == "weak":
            return False
    return _name_heuristic_capable(model_id)


_WEAK_MODEL_REASONING_NOTE = "Note — For depth reasoning, configure a stronger model."


def weak_model_reasoning_note(model_id: str, *, config_dir=None) -> str | None:
    """Plan 147 F4 / 156 P1: a SINGLE brief heads-up shown ONLY when the configured model is
    NOT strong enough (by the cached behavior probe, else the name heuristic) — exactly
    `Note — For depth reasoning, configure a stronger model.` A capable model returns None."""
    return None if model_is_reasoning_capable(model_id, config_dir=config_dir) else _WEAK_MODEL_REASONING_NOTE


def _distilled_error_block(stderr: str, stdout: str, *, capable: bool) -> str:
    """Plan 199 F7: build a DISTILLED error block for the LLM — the key error line(s) first, then a
    bounded raw tail. Model-aware: a weak model gets the distilled error + a SHORT tail (a big dump
    dilutes the signal); a capable model gets the distilled lead + more raw context. Reuses the
    web-search error extractors so shell prompts / Duckln narrative / noise are stripped."""
    key = ""
    lines: tuple[str, ...] = ()
    try:
        from duckln.web_runtime import _sanitize_error_lines, _exact_error_query_fragment
        src = stderr or stdout or ""
        lines = _sanitize_error_lines(src)
        key = _exact_error_query_fragment(src)
    except Exception:
        lines = ()
    block = ""
    if key:
        block += f"KEY ERROR: {key}\n"
    if lines:
        block += "ERROR LINES:\n" + "\n".join(lines[-8:]) + "\n"
    err_cap = 6000 if capable else 1200
    out_cap = 3000 if capable else 600
    block += f"STDERR (tail):\n{(stderr or '')[-err_cap:]}\n"
    if stdout and (capable or not lines):
        block += f"STDOUT (tail):\n{(stdout or '')[-out_cap:]}\n"
    return block


def reasoning_enabled() -> bool:
    """Plan 132: reasoning is ON BY DEFAULT (no flag). Disabled only when explicitly
    turned off (`DUCKLN_AGENT_RECOVERY=0`) or when running under the unit test suite
    (so the 1900+ tests stay fast and never spin a live agent loop) — unless a test
    explicitly opts in with `=1`."""
    import os
    import sys

    flag = os.environ.get("DUCKLN_AGENT_RECOVERY")
    if flag == "0":
        return False
    if flag == "1":
        return True
    return not ("unittest" in sys.modules or "pytest" in sys.modules)


@dataclass
class ReasoningBudget:
    """Global per-bring-up reasoning cap — bounds total cost across many failed steps."""
    max_passes: int = REASONING_MAX_PASSES_PER_BRINGUP
    used: int = 0

    def available(self) -> bool:
        return self.used < self.max_passes

    def consume(self) -> None:
        self.used += 1


def run_bounded_agent(runner: Callable[[], Any], *, timeout_seconds: float = REASONING_WALLCLOCK_SECONDS) -> Any | None:
    """Run `runner()` under a hard WALL-CLOCK timeout (daemon thread + join). Returns
    the result, or None if it overran/raised — so a reasoning pass can NEVER hang a
    bring-up even if a single tool call blocks. This is the anti-hang backstop."""
    import threading

    holder: dict[str, Any] = {}

    def _work() -> None:
        try:
            holder["result"] = runner()
        except Exception:  # noqa: BLE001 — abandon → caller falls back
            holder["result"] = None

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout_seconds)
    if t.is_alive():
        return None
    return holder.get("result")


class RecoveryDecision(str, Enum):
    FIXED = "fixed"          # a verified fix was applied (or is proposed for approval)
    SKIP = "skip"            # the failed step is optional/absent → continue
    BLOCK = "block"          # genuinely needs a human (secret/hardware/license) → honest ask
    EXHAUSTED = "exhausted"  # bounded budget hit without a fix → honest stop


@dataclass(frozen=True)
class RecoveryReport:
    decision: RecoveryDecision
    cause: str
    command: str = ""        # the fix command when decision == FIXED
    reason: str = ""
    safety_class: str = "S2"
    # Plan 145 F2: the concrete evidence the conclusion rests on (files read + key lines,
    # probes run + results, the exact error line). A conclusion with no cited evidence is
    # low-confidence and cannot pass the supervisor (F3) without scrutiny.
    evidence: str = ""

    @property
    def has_evidence(self) -> bool:
        return bool((self.evidence or "").strip())


# --- blast-radius autonomy policy --------------------------------------------

# Destructive / outside-the-repo / secret — NEVER auto-apply, on any target.
_DESTRUCTIVE_RE = re.compile(
    r"(rm\s+-rf\s+(/|~|\$HOME|/etc|/usr|/var|/bin)"
    r"|mkfs|dd\s+if=|dd\s+of=/dev|>\s*/dev/sd|:\(\)\s*\{|shutdown|reboot|halt|poweroff"
    r"|chmod\s+-R\s+777\s+/|chown\s+-R\s+\S+\s+/(?!home)"
    r"|id_rsa|\.ssh/|\.aws/credentials|\.config/gcloud|/etc/passwd|/etc/shadow"
    r"|curl[^|]*\|\s*sudo|wget[^|]*\|\s*sudo)",
    re.IGNORECASE,
)
# System-wide installs / privilege — auto on a disposable sandbox, ask on local.
_SYSTEM_RE = re.compile(
    r"(\bsudo\b|\bapt(-get)?\s+install|\bbrew\s+install|\byum\s+install|\bdnf\s+install"
    r"|npm\s+install\s+-g|npm\s+i\s+-g|pnpm\s+add\s+-g|yarn\s+global\s+add"
    r"|rustup\b|nvm\s+install|pyenv\s+install|/etc/|/usr/local/)",
    re.IGNORECASE,
)


def classify_blast_radius(command: str) -> str:
    """Return 'destructive' | 'system' | 'repo' for a proposed fix command."""
    c = (command or "").strip()
    if not c:
        return "repo"
    if _DESTRUCTIVE_RE.search(c):
        return "destructive"
    if _SYSTEM_RE.search(c):
        return "system"
    return "repo"


def recovery_autonomy(command: str, *, execution_target: str) -> str:
    """Return 'auto' | 'ask' | 'block' for whether a fix may apply without a prompt.

    - destructive/secret/outside-repo → 'block' (always — a human must decide).
    - repo/project-scoped + reversible → 'auto' on EVERY target incl. the user's Mac.
    - system-wide → 'auto' on a disposable sandbox (VM/container/cloud), 'ask' on local.
    """
    blast = classify_blast_radius(command)
    if blast == "destructive":
        return "block"
    if blast == "repo":
        return "auto"
    # system-wide
    return "auto" if is_sandbox_target(execution_target) else "ask"


# --- thinking log (Claude-style, persisted, redacted) ------------------------


def thinking_log_path(config_dir: Path | str) -> Path:
    """Plan 133 F6: the maintained, human-readable reasoning log path."""
    return Path(config_dir) / "memory" / "logical-thinking.md"


def surface_thinking_link(path: Path | None, display: Callable[[str], None] | None) -> None:
    """Plan 133 F6: after a MAJOR reasoning conclusion, show a one-line CLICKABLE link
    to logical-thinking.md so the user can open it (markdown link for the VSCode panel;
    the `file://` URI is clickable in most terminals)."""
    if not path or display is None:
        return
    try:
        # The markdown link is clickable in the VSCode panel; the Textual TUI detects this
        # exact prefix + the `file://` URI and renders a clickable OSC-8 hyperlink (Plan 138 F3).
        display(f"🧠 Reasoning captured → [logical-thinking.md](file://{path}) (click to open)")
    except Exception:
        pass


def append_thinking_log(
    config_dir: Path | str,
    *,
    repo_slug: str,
    title: str,
    lines: list[str],
    surface: str = "recover",
) -> Path | None:
    """Plan 133 F6: append a Claude-style reasoning EPISODE to a MAINTAINED, human-
    readable `logical-thinking.md` — created on the first episode, appended (never
    overwritten) thereafter — so the user can open and read the chain of thought.
    Redacted (reasoning + command names + outcomes; never raw secrets). Best-effort;
    returns the file path (for surfacing a clickable link) or None."""
    try:
        from datetime import datetime, timezone

        from duckln.diagnostics import redact_sensitive_data

        path = thinking_log_path(config_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                "# Duckln — logical thinking log\n\n"
                "Duckln's reasoning across planning and recovery (newest at the bottom).\n",
                encoding="utf-8",
            )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        body = "\n".join(f"- {redact_sensitive_data(str(ln))}" for ln in lines if str(ln).strip())
        block = (
            f"\n## {ts} · {redact_sensitive_data(str(repo_slug))} · {surface}\n"
            f"**{redact_sensitive_data(str(title))}**\n{body}\n"
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
        return path
    except Exception:
        return None


# --- act → verify (apply a fix on the target, then re-run the failed step) ----


def apply_fix_and_verify(
    *,
    fix_command: str,
    failed_command: str,
    run_cmd: Callable[[str], tuple[int, str]],
    retries: int = 1,
    next_fix: Callable[[str], str | None] | None = None,
    max_stages: int = 4,
) -> bool:
    """Apply `fix_command` on the target, then RE-RUN `failed_command` to VERIFY the
    fix actually worked. `run_cmd(command) -> (exit_code, output)`. Returns True only
    when the failed step now exits 0 — so the plan only advances on a verified pass,
    never on a hopeful guess.

    Plan 144 F1 — PROGRESS-AWARE, multi-stage. A multi-stage build (e.g. a script that
    first checks for a venv, THEN runs PyInstaller) reveals its NEXT error only after the
    prior one is fixed. So when a fix applies cleanly but the re-run STILL fails, ask
    `next_fix(new_output)` for a fix for the NEW error: if it returns a DIFFERENT command,
    the prior fix made real PROGRESS (a new stage surfaced) → chain that fix and re-verify,
    up to `max_stages`. Only return False when the error is UNCHANGED (next_fix returns the
    same/no fix, or it was already tried) or the stage budget is exhausted — never treat
    'the error changed' as 'my fix failed'."""
    if not fix_command or not failed_command:
        return False
    current_fix = fix_command
    applied: set[str] = set()
    for _stage in range(max(1, max_stages)):
        ok = False
        for _ in range(max(1, retries)):
            fix_code, _out = run_cmd(current_fix)
            if fix_code == 0:
                ok = True
                break
        if not ok:
            return False
        applied.add(current_fix.strip())
        verify_code, vout = run_cmd(failed_command)
        if verify_code == 0:
            return True
        # Applied cleanly, but the step still fails. If a DIFFERENT known fix matches the
        # NEW error, the prior fix advanced the build to a later stage (real progress) —
        # chain it. A same/empty/already-tried fix means no progress → stop honestly.
        if next_fix is None:
            return False
        nxt = next_fix(vout)
        if not nxt or nxt.strip() in applied:
            return False
        current_fix = nxt
    return False


# --- the reasoning recovery loop (agent-driven) ------------------------------

# Plan 157 P4: _RECOVERY_SYSTEM retired → agents/recovery_agent.md
#   (sourced via `_recovery_spec_prompt("recovery_agent")`).


_DECISION_MAP = {
    "fixed": RecoveryDecision.FIXED, "fix": RecoveryDecision.FIXED,
    "skip": RecoveryDecision.SKIP, "block": RecoveryDecision.BLOCK,
}


def _report_from_payload(payload: dict) -> RecoveryReport | None:
    raw = str(payload.get("decision") or "").strip().lower()
    decision = _DECISION_MAP.get(raw)
    if decision is None:
        return None
    # Plan 136 F1: validate/repair the model's fix command (e.g. a stray-slash `/cd …`).
    from duckln.shell import sanitize_shell_command
    _cmd = "" if decision is not RecoveryDecision.FIXED else sanitize_shell_command(str(payload.get("command") or "").strip())
    return RecoveryReport(
        decision=decision,
        cause=str(payload.get("cause") or "").strip() or "Recovery agent could not determine a clear cause.",
        command=_cmd,
        reason=str(payload.get("reason") or "").strip(),
        evidence=str(payload.get("evidence") or "").strip(),
    )


def _report_from_prose(answer: str) -> RecoveryReport | None:
    """Plan 142 F4: when the agent reasoned in PROSE (no clean JSON), don't throw it away —
    infer a CONFIDENT decision from the text. Returns None when there's no actionable signal
    (so the deterministic/web fallback still runs); the prose is persisted regardless."""
    import re as _re
    from duckln.shell import sanitize_shell_command

    text = (answer or "").strip()
    if not text:
        return None
    low = text.lower()
    # Extract a fix command from a fenced block or an explicit `command:`/`fix:` line.
    cmd = ""
    m = _re.search(r"```(?:[a-zA-Z]*\n)?(.+?)```", text, _re.DOTALL)
    if m and m.group(1).strip():
        cmd = m.group(1).strip().splitlines()[0].strip()
    if not cmd:
        m2 = _re.search(r"(?im)^\s*(?:fix\s*command|command|run)\s*[:=]\s*(.+)$", text)
        if m2:
            cmd = m2.group(1).strip().strip("`")
    cmd = sanitize_shell_command(cmd) if cmd else ""
    # Cause line, else a concise lead.
    cause = ""
    mc = _re.search(r"(?im)^\s*(?:cause|root\s*cause)\s*[:=]\s*(.+)$", text)
    cause = mc.group(1).strip() if mc else text[:300].strip()
    # Plan 145 F2: pull an explicit evidence line if the model gave one.
    me = _re.search(r"(?im)^\s*(?:evidence|i\s+(?:inspected|checked|found|observed))\s*[:=]\s*(.+)$", text)
    evidence = me.group(1).strip() if me else ""
    # CONFIDENT decision only.
    if cmd:
        decision = RecoveryDecision.FIXED
    elif any(k in low for k in ("skip", "optional", "not present", "absent", "no-op")):
        decision = RecoveryDecision.SKIP
    elif any(k in low for k in ("needs a human", "missing hardware", "license", "paid secret", "credential", "no such hardware")):
        decision = RecoveryDecision.BLOCK
    else:
        return None  # vague — let the deterministic/web fallback run
    return RecoveryReport(
        decision=decision, cause=cause,
        command=(cmd if decision is RecoveryDecision.FIXED else ""), reason=text[:1500],
        evidence=evidence,
    )


def _parse_recovery_report(answer: str) -> RecoveryReport | None:
    """Extract the structured decision from the agent's final answer. Plan 142 F4: prefer
    clean JSON, but fall back to inferring it from PROSE so good reasoning isn't discarded."""
    try:
        from duckln.plan_mode import parse_plan_json
        payload = parse_plan_json(answer)
    except Exception:
        payload = None
    if isinstance(payload, dict) and payload.get("decision"):
        rep = _report_from_payload(payload)
        if rep is not None:
            return rep
    return _report_from_prose(answer)


# --- the supervisor: a skeptical reviewer that CHALLENGES a conclusion (Plan 145 F3) -----

# Plan 157 P4: _SUPERVISOR_SYSTEM retired → agents/recovery_supervisor.md
#   (sourced via `_recovery_spec_prompt("recovery_supervisor")`).


_CAPABILITY_GAP_RE = re.compile(
    r"\bneed(?:s|ed|ing)?\s+(?:an?\s+|the\s+)?(?:tool|mcp(?:\s+server)?|connector|capability)\b[^`'\"\w]*[`'\"]?([a-zA-Z0-9_.\-/]+)",
    re.IGNORECASE,
)


def capability_gap_from_text(text: str) -> str | None:
    """Plan 146 B4: when a recovery/planning conclusion is blocked for lack of a CAPABILITY
    (not a fixable error) — e.g. "to do X I need the MCP server `github`" — extract the
    needed tool/MCP name so the user can connect it via `/mcp`//`/tools add`. Returns the
    name or None. (The supervisor F3 confirms it's a genuine gap, not an early give-up.)"""
    m = _CAPABILITY_GAP_RE.search(text or "")
    if not m:
        return None
    name = (m.group(1) or "").strip("`'\".,)")
    # ignore noise words that follow "need a tool"
    return name if name and name.lower() not in ("to", "that", "for", "which", "named", "called") else None


def supervise_recovery_conclusion(
    report: RecoveryReport | None,
    *,
    empirically_verified: bool = False,
    review: Callable[[RecoveryReport], tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    """Plan 145 F3: challenge a recovery conclusion before it's trusted. Returns
    (accepted, critique). Order:
    1. `empirically_verified` (the fix's re-run passed) → ACCEPT — proof beats debate.
    2. an injected `review` (the model supervisor) → its verdict.
    3. else a deterministic trust gate: a conclusion that CAN'T be empirically verified
       (block/skip, or a fix with no cited evidence) must carry EVIDENCE — without it the
       supervisor challenges, so an unproven claim is never rubber-stamped."""
    if empirically_verified:
        return (True, "")
    if report is None:
        return (False, "No conclusion was produced — investigate the failure and gather evidence.")
    if review is not None:
        try:
            return review(report)
        except Exception:  # noqa: BLE001 — a reviewer crash must not abort recovery
            # Plan 155 F7: don't silently lose a model-reviewer failure — log it (redacted,
            # DEBUG) so it's observable, then fall through to the deterministic gate.
            _LOG.debug("supervisor reviewer raised; using the deterministic gate", exc_info=True)
    # Deterministic gate (used when no model reviewer is available).
    if report.decision in (RecoveryDecision.BLOCK, RecoveryDecision.SKIP):
        if report.has_evidence:
            return (True, "")
        verb = "block" if report.decision is RecoveryDecision.BLOCK else "skip"
        return (False, f"A '{verb}' must be proven — cite the exact evidence (error line / listing) that justifies it.")
    if report.decision is RecoveryDecision.FIXED:
        if not (report.command or "").strip():
            return (False, "A 'fixed' decision must include a concrete fix command.")
        if not report.has_evidence:
            return (False, "Cite the evidence (files/probes/the error line) showing this fix addresses the real cause.")
        return (True, "")
    return (True, "")  # EXHAUSTED and other terminal decisions pass through


def _llm_supervisor_review(llm_client, failed_command: str) -> Callable[[RecoveryReport], tuple[bool, str]] | None:
    """Build a model-backed supervisor reviewer (one-shot JSON) when an LLM is available;
    returns None when it isn't (caller falls back to the deterministic gate)."""
    if llm_client is None:
        return None

    def _review(report: RecoveryReport) -> tuple[bool, str]:
        prompt = (
            f"Failed step: {failed_command}\n"
            f"Proposed decision: {report.decision.value}\n"
            f"Cause: {report.cause}\n"
            f"Fix command: {report.command or '(none)'}\n"
            f"Evidence cited: {report.evidence or '(none)'}\n"
            f"Reasoning: {report.reason[:1200] or '(none)'}\n\n"
            "Review per your instructions and return the JSON verdict."
        )
        try:
            text = llm_client(system_prompt=_recovery_spec_prompt("recovery_supervisor"), user_message=prompt)
        except Exception:
            # Any LLM/transport failure → defer to the deterministic gate.
            return supervise_recovery_conclusion(report)
        from duckln.plan_mode import parse_plan_json
        try:
            payload = parse_plan_json(text or "")
        except Exception:
            payload = None
        if isinstance(payload, dict):
            verdict = str(payload.get("verdict") or "").strip().lower()
            if verdict == "accept":
                return (True, "")
            if verdict == "challenge":
                return (False, str(payload.get("critique") or "Insufficient evidence — investigate further.").strip())
        # Unparseable supervisor output → deterministic gate.
        return supervise_recovery_conclusion(report)

    return _review


# Plan 157 P4: _PLANNING_SYSTEM retired → agents/planning_investigator.md
#   (sourced via `_recovery_spec_prompt("planning_investigator")`).


def reason_about_plan(
    *,
    config_dir: Path,
    repo_name: str,
    project_dir: Path | None,
    execution_target: str,
    vm_name: str | None,
    objective: str,
    mode,
    approve: Callable[[str], bool] | None,
    llm_client,
    emit_thought: Callable[[str], None] | None = None,
    display: Callable[[str], None] | None = None,
    agent_runner: Callable | None = None,
    budget: "ReasoningBudget | None" = None,
) -> str | None:
    """Plan 132 A2: for a COMPLEX/uncertain repo, investigate with read-only tools and
    return a grounded PLANNING BRIEF that enriches the deterministic plan. Returns None
    when reasoning is off / no LLM / budget spent / harness unavailable (→ fast path).
    Bounded by a wall-clock timeout so it can never hang planning."""
    if llm_client is None:
        return None
    if budget is not None and not budget.available():
        return None
    runner = agent_runner
    if runner is None:
        if not reasoning_enabled():
            return None
        try:
            from duckln.repo_agent import run_repo_agent as runner  # type: ignore
        except Exception:
            return None
    if budget is not None:
        budget.consume()
    # Plan 135 F4: CAPTURE the live "Duckln's thinking" stream so the persisted
    # logical-thinking.md mirrors what the user sees on screen — not just the final LLM
    # brief (which is empty on a weak/free model). The link is then always worth showing.
    _captured: list[str] = []

    def _cap(line: str) -> None:
        try:
            _captured.append(str(line))
        except Exception:
            pass
        if emit_thought:
            try:
                emit_thought(line)
            except Exception:
                pass

    _cap("Investigating the repo to plan it (reading manifests/scripts, probing the toolchain)…")

    def _invoke():
        return runner(
            question=f"Objective: {objective}\nInvestigate this repo and produce the planning brief.",
            config_dir=config_dir, repo_name=repo_name, project_dir=project_dir,
            execution_target=execution_target, mode=mode, display=display,
            emit_thought=_cap, approve=approve, llm_client=llm_client,
            task_kind="answer", vm_name=vm_name, extra_hints=(_recovery_spec_prompt("planning_investigator"),),
        )

    outcome = run_bounded_agent(_invoke)
    brief = (getattr(outcome, "answer", "") or "").strip() if outcome is not None else ""
    _lines = list(_captured)
    if brief:
        _lines += ["", "Conclusion / planning brief:", brief[:1200]]
    if not any(s.strip() for s in _lines):
        _lines = ["(reasoning produced no captured output)"]
    _path = append_thinking_log(
        config_dir, repo_slug=str(project_dir or repo_name),
        title=f"Planning brief: {repo_name}", lines=_lines, surface="plan",
    )
    # Plan 135 F4: a planning pass is a MAJOR reasoning conclusion → ALWAYS surface the
    # clickable reasoning file (was gated on a non-empty brief, so a weak model = no link).
    surface_thinking_link(_path, display)
    return brief or None


def _format_investigation_chain(observations) -> list[str]:
    """Plan 142 F4: render the agent's tool-use chain (what it inspected + what it found)
    into readable, redacted bullet lines for logical-thinking.md."""
    lines: list[str] = []
    for obs in observations[:20]:
        tool = getattr(obs, "tool", "") or "?"
        args = getattr(obs, "args", {}) or {}
        target = ""
        if isinstance(args, dict):
            target = str(args.get("path") or args.get("command") or args.get("query") or args.get("pattern") or "")
        ok = getattr(obs, "ok", False)
        if ok:
            payload = getattr(obs, "payload", None)
            snippet = ""
            if isinstance(payload, dict):
                snippet = str(payload.get("summary") or payload.get("content") or payload.get("entries") or payload.get("stdout") or "")
            elif payload is not None:
                snippet = str(payload)
            snippet = " ".join(snippet.split())[:140]
            detail = f" → {snippet}" if snippet else " → ok"
        else:
            detail = f" → ERROR {getattr(obs, 'error_code', '') or ''}: {getattr(obs, 'error_message', '') or ''}".rstrip()
        try:
            from duckln.diagnostics import redact_sensitive_data as _redact
            line = _redact(f"- `{tool} {target}`{detail}")
        except Exception:
            line = f"- `{tool} {target}`{detail}"
        lines.append(line[:200])
    return lines


def recover_failed_step_with_agent(
    *,
    config_dir: Path,
    repo_name: str,
    project_dir: Path | None,
    execution_target: str,
    vm_name: str | None,
    failed_command: str,
    stderr: str,
    stdout: str,
    mode,
    approve: Callable[[str], bool] | None,
    llm_client,
    display: Callable[[str], None] | None = None,
    emit_thought: Callable[[str], None] | None = None,
    agent_runner: Callable | None = None,
    budget: "ReasoningBudget | None" = None,
    hint: str = "",
) -> RecoveryReport | None:
    """Investigate→decide via the tool-using agent loop; return a RecoveryReport or
    None when recovery can't run (disabled / no LLM / budget spent / harness
    unavailable) so the caller falls back to the existing path. Default-ON in
    production; bounded by a wall-clock timeout + global budget. `agent_runner` is
    injectable for tests (always runs, bypassing the default-off-under-unittest gate).
    Plan 145 F1: `hint` is a non-authoritative known-pattern suggestion the model may use
    or reject against the real evidence — never a decider."""
    if llm_client is None:
        return None
    if budget is not None and not budget.available():
        return None
    runner = agent_runner
    if runner is None:
        # Plan 132: default-ON (reasoning_enabled) — off only under the unit suite or
        # explicit DUCKLN_AGENT_RECOVERY=0. The wall-clock backstop below guarantees
        # it can never hang a bring-up.
        if not reasoning_enabled():
            return None
        try:
            from duckln.repo_agent import run_repo_agent as runner  # type: ignore
        except Exception:
            return None

    # Plan 183 F8b: the multi-agent pre-pass (investigate+search+memory) is the biggest TOKEN
    # sink. A WEAK (non-reasoning-capable) model can't synthesize its findings anyway (it just
    # burned ~100k tokens looping in the live run), so SKIP it for a weak model — go straight to
    # the single bounded pass → honest-stop. A capable model still gets the full coordinator.
    try:
        from state.access import read_config_snapshot as _rcs_cap
        _cap_model = str(_rcs_cap(config_dir).get("model") or "")
    except Exception:
        _cap_model = ""
    _cap_capable = model_is_reasoning_capable(_cap_model, config_dir=config_dir) if _cap_model else True
    # Plan 156 P5: run the multi-agent RECOVERY coordinator (investigate + search + memory in
    # parallel under recovery_coordinator) LIVE — its consolidated findings are fed as an extra
    # HINT into the structured single-agent recovery below (which produces the RecoveryReport).
    # The agents run live + emit trace + are captured to logical-thinking.md. Bounded + best-
    # effort: a failure is a no-op. Skipped when a test injects `agent_runner`.
    if agent_runner is None and reasoning_enabled() and _cap_capable:
        try:
            from duckln.harness.recovery_flow import RecoveryRequest, run_multi_agent_recovery

            _req = RecoveryRequest(
                failed_command=failed_command, step_purpose=repo_name or "",
                stderr=stderr or "", stdout=stdout or "", exit_code=None,
                repo_slug=str(repo_name or project_dir),
                project_dir=Path(project_dir) if project_dir else Path("."),
                execution_target=execution_target, config_dir=Path(config_dir), mode=mode,
            )
            _outcome = run_bounded_agent(
                lambda: run_multi_agent_recovery(_req, llm_client=llm_client, display=display, approve=approve),
                timeout_seconds=REASONING_WALLCLOCK_SECONDS,
            )
            if _outcome is not None and str(getattr(_outcome, "summary", "") or "").strip():
                _findings = str(_outcome.summary)[:1500]
                hint = (hint + "\n\n" if hint else "") + "Multi-agent recovery findings (investigate/search/memory): " + _findings
                try:
                    append_thinking_log(
                        config_dir, repo_slug=str(repo_name or project_dir), surface="recover",
                        title="Multi-agent recovery (coordinator→investigate/search/memory)",
                        lines=[f"Coordinator ran live (session={getattr(_outcome, 'session_id', '')}, "
                               f"children={len(getattr(_outcome, 'children', ()) or ())}, "
                               f"succeeded={getattr(_outcome, 'succeeded', False)}).",
                               f"Findings: {_findings[:800]}"],
                    )
                except Exception:
                    pass
        except Exception:
            pass

    # Plan 199 F7: DISTILL the error before the LLM — lead with the KEY error line(s) so the model
    # reasons on the specific error, not a wall of logs. Model-aware: a weak/small model gets the
    # distilled error + a SHORT raw tail (a big dump dilutes the 9B's signal); a capable model gets
    # the distilled lead + more raw context. The FULL log stays available — the agent can
    # `fs.read_file` the real log when it needs more (Plan 142's read-the-real-log path is kept).
    question = (
        f"A bring-up step failed.\nCOMMAND: {failed_command}\n"
        + _distilled_error_block(stderr or "", stdout or "", capable=_cap_capable)
        + (f"{hint}\n" if hint else "")
        + "Investigate (read the manifest/scripts/log, probe the OS + permissions), find the "
        "ROOT CAUSE, then return the decision JSON."
    )
    if budget is not None:
        budget.consume()

    # Plan 145 F3: a skeptical SUPERVISOR challenges the conclusion before it's trusted.
    # Build the model-backed reviewer (deterministic gate when no model). On a CHALLENGE we
    # re-investigate (bounded) with the critique fed back, so the sub-agent gathers more
    # evidence instead of the conclusion being rubber-stamped.
    review = _llm_supervisor_review(llm_client, failed_command)
    # Plan 173 F3: a WEAK model won't improve across re-investigation rounds — do a single pass
    # then honest-stop (don't burn 3 rounds / ~127k tokens, as the qwen2.5:1.5b run did). A
    # reasoning-capable model still gets the full supervisor rounds.
    try:
        from state.access import read_config_snapshot as _rcs173
        _model_id173 = str(_rcs173(config_dir).get("model") or "")
    except Exception:
        _model_id173 = ""
    _capable173 = model_is_reasoning_capable(_model_id173, config_dir=config_dir) if _model_id173 else True
    _MAX_SUPERVISOR_ROUNDS = 2 if _capable173 else 0
    # Plan 179 B2: the Runtime Capability Adapter appends the strict `<thought>` + don't-guess
    # directive ONLY for a LOCAL model class; a cloud-reasoning model gets the spec body as-is.
    from duckln.ai_client import adapt_reasoning_prompt as _adapt_reasoning_prompt
    _recovery_extra_hint = _adapt_reasoning_prompt(
        _recovery_spec_prompt("recovery_agent"), _model_id173, config_dir=config_dir
    )
    answer = ""
    report = None
    _last_thought = ""  # Plan 178 F2: the captured internal monologue (robust to truncation).
    _chain: list[str] = []
    _prev_chain: list[str] = []
    _supervisor_log: list[str] = []
    _critique = ""
    _accepted = False
    # Plan 147 F2: log under the repo NAME (consistent identifier), not the raw dir path —
    # so the thinking log doesn't show a local-looking path here and the clone URL elsewhere
    # for the same repo (which looked like Duckln was "going to the URL again").
    repo_slug = str(repo_name or project_dir)
    for _round in range(1 + _MAX_SUPERVISOR_ROUNDS):
        _q = question if not _critique else (
            f"{question}\n\nThe SUPERVISOR CHALLENGED your previous conclusion — address this "
            f"with concrete evidence, do NOT repeat an unproven claim:\n{_critique}"
        )

        def _invoke(_q=_q):
            return runner(
                question=_q, config_dir=config_dir, repo_name=repo_name, project_dir=project_dir,
                execution_target=execution_target, mode=mode, display=display, emit_thought=emit_thought,
                approve=approve, llm_client=llm_client, task_kind="recover", vm_name=vm_name,
                extra_hints=(_recovery_extra_hint,),
            )

        # Hard wall-clock backstop so a blocking tool call can never wedge the bring-up.
        outcome = run_bounded_agent(_invoke)
        if outcome is None:
            return None
        answer = getattr(outcome, "answer", "") or ""
        # Plan 178 F2: split the `<thought>` monologue from the action payload — robust to a
        # truncated/unclosed/missing tag (a weak model that ran out of tokens mid-reasoning).
        # The captured thought is persisted to logical-thinking.md even when the JSON is malformed.
        from duckln.reasoning import (
            MISSING_THOUGHT as _MISSING_THOUGHT,
            TRUNCATED_PAYLOAD as _TRUNCATED_PAYLOAD,
            extract_thinking_and_content as _extract_thought,
        )
        _thought, _payload = _extract_thought(answer)
        if _thought and _thought != _MISSING_THOUGHT:
            _last_thought = _thought
        # Parse the action payload (post-</thought>); fall back to the full answer when the tag
        # was skipped or truncated, so a prose decision in the body isn't lost.
        _to_parse = answer if (_thought == _MISSING_THOUGHT or _payload == _TRUNCATED_PAYLOAD or not _payload) else _payload
        report = _parse_recovery_report(_to_parse)
        _chain = _format_investigation_chain(getattr(outcome, "observations", ()) or ())
        # Plan 145 F2: if the model didn't cite evidence in JSON, fall back to the actual
        # tool-use chain (what it inspected) as the evidence — so a conclusion always carries
        # the facts it rests on for the supervisor to check.
        if report is not None and not report.has_evidence and _chain:
            from dataclasses import replace as _dc_replace_ev
            report = _dc_replace_ev(report, evidence=" | ".join(_chain[:8]))
        _accepted, _critique = supervise_recovery_conclusion(report, review=review)
        if _accepted:
            if _round > 0:
                _supervisor_log.append(f"Round {_round + 1}: supervisor ACCEPTED the revised conclusion.")
            break
        # Plan 173 F3: a CHALLENGED round that gathered NO NEW evidence (empty, or the same
        # chain as last round — e.g. the same rejected probes) won't improve by looping — stop.
        if _round > 0 and (not _chain or _chain == _prev_chain):
            _supervisor_log.append(f"Round {_round + 1}: no new evidence gathered — stopping re-investigation.")
            break
        _prev_chain = list(_chain)
        _supervisor_log.append(f"Round {_round + 1}: supervisor CHALLENGED — {_critique}")
        if budget is not None:
            if not budget.available():
                break
            budget.consume()

    # The supervisor never accepted. A FIXED proposal may still PROVE itself via the caller's
    # apply→verify (proof beats debate) — let it through. But an un-verifiable BLOCK/SKIP that
    # couldn't be justified is NOT trusted → honest EXHAUSTED stop naming what's unproven.
    if not _accepted and report is not None and report.decision in (RecoveryDecision.BLOCK, RecoveryDecision.SKIP):
        from dataclasses import replace as _dc_replace_ex
        report = _dc_replace_ex(
            report, decision=RecoveryDecision.EXHAUSTED,
            cause=f"{report.cause} — supervisor not satisfied: {_critique}",
        )
    _lines = [f"Failed step: {failed_command}", ""]
    if _last_thought:
        # Plan 178 F2: the model's internal monologue, captured robustly (survives a truncated tag).
        _lines += ["### Internal monologue (<thought>)", _last_thought[:1500], ""]
    if _chain:
        _lines += ["### What I inspected", *_chain, ""]
    if report and report.evidence:
        _lines += ["### Evidence", f"- {report.evidence[:600]}", ""]
    if _supervisor_log:
        _lines += ["### Supervisor review", *[f"- {ln}" for ln in _supervisor_log], ""]
    _lines += [
        "### Conclusion",
        f"- root cause: {report.cause if report else (answer[:400] or 'no conclusion reached')}",
        f"- decision: {report.decision.value if report else 'none (no confident decision)'}",
        f"- fix: {report.command if (report and report.command) else '(none)'}",
    ]
    if report and report.reason:
        _lines += ["", "### Reasoning", report.reason[:1500]]
    _path = append_thinking_log(
        config_dir, repo_slug=repo_slug,
        title=f"Recovering: {failed_command}",
        lines=_lines,
        surface="recover",
    )
    # Plan 135 F4: a recovery episode is a MAJOR reasoning conclusion → always surface
    # the clickable reasoning file (the agent ran; the file has the cause/decision).
    surface_thinking_link(_path, display)
    return report
