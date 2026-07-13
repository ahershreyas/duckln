# Duckln /loop — Alignment Fixes (vs. official Claude Code scheduling docs)

**Context for the engineer:** Duckln's `/loop` is a custom feature, not Anthropic's native
Claude Code `/loop` skill. There is no official spec for Duckln's exact feature — but
Anthropic's documented scheduling architecture (code.claude.com/docs/en/scheduled-tasks,
fetched live, current as of this writing) gives a three-tier model: Cloud (Routines),
Desktop, and session-scoped `/loop`. Duckln's `/loop` architecturally matches the
**Desktop tier**: runs on your machine, persists across restarts via local state, doesn't
require an open chat session. That placement is correct and should not change.

Two real gaps exist relative to the documented patterns. Both are described below with
the reasoning, the official precedent, and the exact fix to implement.

---

## Gap 1 — No forced expiry / review point

### What the docs say
Anthropic's official `/loop` auto-expires recurring tasks after 7 days, and is explicit
that this is a deliberate safety feature:

> "Recurring tasks automatically expire 7 days after creation. The task fires one final
> time, then deletes itself. This bounds how long a forgotten loop can run."

Three reasons are given in Anthropic's broader scheduling guidance for why this bound
exists, all of which apply equally to Duckln:
- **Human oversight** — a hard limit forces a natural review point.
- **Runaway cost prevention** — a misconfigured loop running frequent cycles can burn
  resources (API credits in Claude Code's case; CPU/API calls in Duckln's case — e.g. a
  cost_monitor loop calling `aws ce get-cost-and-usage` every minute for months
  unattended).
- **Error accumulation / drift prevention** — a loop that's slightly off on day one can
  be significantly off by day thirty if nobody re-examines it.

### What Duckln currently does
`LoopSpec` has no expiry field. Once created, a loop runs indefinitely — surviving
restarts forever — until a human manually runs `/loop delete <id>`. There is no forced
review point of any kind.

### The fix
Add an expiry field to `LoopSpec` and enforce it in the scheduler.

```python
# loop_runtime.py — LoopSpec

@dataclass
class LoopSpec:
    # ... existing fields ...
    expires_at: datetime | None = None   # NEW
    expiry_days: int = 30                # NEW — default bound, configurable per loop

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = datetime.utcnow() + timedelta(days=self.expiry_days)
```

```python
# loop_runtime.py — _run_scheduled_loop

def _run_scheduled_loop(config_dir, loop_id):
    loop = store.get_loop(config_dir, loop_id)
    if loop is None or not loop.active:
        return

    # NEW — expiry check before execution
    if loop.expires_at and datetime.utcnow() >= loop.expires_at:
        store.set_loop_active(config_dir, loop_id, active=False)
        send_loop_notification(
            config_dir,
            title=f"Loop '{loop.name}' expired",
            body=(
                f"This loop has run for {loop.expiry_days} days and has been "
                f"automatically paused for review. Run `/loop edit {loop_id}` to "
                f"renew it, or `/loop delete {loop_id}` to remove it."
            ),
        )
        return

    result = LoopSubAgent.execute(loop)
    store.record_loop_result(config_dir, loop_id, result)
    if result.should_notify:
        notify(config_dir, loop, result)
```

```python
# main.py — interactive /loop creation flow, add a 4th question

# Existing 3 questions: what to monitor → how often → fix-first-or-notify
# ADD a 4th question with a sane default so it doesn't add friction:

expiry_days = ask_user(
    "How long should this loop run before I check in with you for review? "
    "(default: 30 days, press enter to accept)",
    default=30,
)
```

```python
# main.py — /loop list and /loop history, surface time-to-expiry

# When listing loops, show days remaining so the user has visibility
# without needing to wait for the expiry notification:
# e.g. "health_monitor (id: 3) — active, expires in 12 days"
```

### Why 30 days, not 7
Claude Code's native `/loop` is session-scoped and ephemeral by nature — 7 days fits a
short-lived polling task ("babysit this PR"). Duckln's loops are closer to genuine
infrastructure monitoring (cost watching, health checks) where 7 days would be
disruptively short. 30 days mirrors the same safety principle (forced review, bounded
drift, bounded runaway cost) while fitting the actual use case. Make it configurable per
loop type if some types warrant shorter bounds (e.g. `ci_monitor` tracking a specific
release branch might reasonably default to 7 days; `cost_monitor` tracking ongoing infra
might reasonably default to 90).

### Test to add
```python
def test_loop_expires_and_deactivates(tmp_config_dir):
    loop = create_test_loop(expiry_days=1)
    # fast-forward the clock past expiry
    with freeze_time(datetime.utcnow() + timedelta(days=2)):
        _run_scheduled_loop(tmp_config_dir, loop.id)
    reloaded = store.get_loop(tmp_config_dir, loop.id)
    assert reloaded.active is False

def test_loop_notifies_on_expiry(tmp_config_dir, mock_notifier):
    loop = create_test_loop(expiry_days=1)
    with freeze_time(datetime.utcnow() + timedelta(days=2)):
        _run_scheduled_loop(tmp_config_dir, loop.id)
    assert mock_notifier.called
    assert "expired" in mock_notifier.call_args.kwargs["body"].lower()
```

---

## Gap 2 — Stub loop types give up the core value of agentic scheduling

### What the docs say
Anthropic's stated reason scheduled agentic tasks exist at all, as opposed to plain cron:

> "Because Claude has semantic understanding of code, it can handle the messy,
> context-dependent situations that break traditional automation... Traditional
> automation — CI/CD pipelines, scheduled scripts, GitHub Actions — is powerful but
> brittle. It executes exactly what you tell it to, no more. If something unexpected
> happens, it either fails loudly or silently produces wrong results."

The built-in maintenance prompt for bare `/loop` is itself LLM-driven — it reads PR
state, CI logs, and review comments, and *reasons* about what to do next ("if CI is red,
pull the failing job log, diagnose, and push a minimal fix"). That reasoning step is the
entire point.

### What Duckln currently does
`LoopSubAgent.execute` runs fixed per-type Python logic with **no LLM call anywhere in
the loop body**. Worse: `repo_watcher`, `model_quality`, `ci_monitor`, and `custom` — 4
of the 6 declared loop types — are literal stubs that return `ok` without doing any real
work (`loop_runtime.py:93-99`). Only `cost_monitor` and partially `health_monitor` have
real logic, and that logic is deterministic shell-outs (`aws ce get-cost-and-usage`,
`check_port`), not reasoning.

This means: a user who says "watch this repo and tell me if anything looks concerning"
(repo_watcher) gets a loop that does literally nothing every cycle. That's worse than no
feature — it gives false confidence that monitoring is happening.

### The fix
Two tracks, do both:

**Track A — Keep deterministic logic for types that don't need reasoning.**
`cost_monitor` and `health_monitor` are correctly deterministic. Checking if a port is
open, or shelling out to a billing API and comparing a number to a threshold, doesn't
need an LLM. Anthropic's own per-cycle pattern allows this — the maintenance prompt
itself, when nothing is pending, just says so in one line; it doesn't force reasoning
where none is needed. Leave these two as-is.

**Track B — Give the 4 stub types a real LLM-driven execution path, scoped exactly like
the official maintenance prompt is scoped.**

```python
# loop_runtime.py — LoopSubAgent.execute, for LLM-driven types

LLM_DRIVEN_TYPES = {"repo_watcher", "model_quality", "ci_monitor", "custom"}

class LoopSubAgent:
    def execute(self, loop: LoopRecord) -> LoopResult:
        if loop.type not in LOOP_TYPES:
            return LoopResult(status="failed", reason=f"unknown loop type {loop.type}")

        allowed_tools = LOOP_TYPES[loop.type]["tools"]

        if loop.type in LLM_DRIVEN_TYPES:
            return self._execute_llm_driven(loop, allowed_tools)
        else:
            return self._execute_deterministic(loop, allowed_tools)

    def _execute_llm_driven(self, loop: LoopRecord, allowed_tools: list[str]) -> LoopResult:
        """
        Mirrors the official /loop maintenance-prompt pattern:
        - LLM reasons about what it observes
        - tool access is hard-restricted to allowed_tools (default-deny outside scope)
        - irreversible actions only proceed if the loop's own prior cycles already
          established the need (no fresh destructive initiative)
        - safety_class S0/S1 only — same gate as the deterministic types
        """
        spec = get_agent_spec("loop_executor")   # new .md spec, see below
        result = run_spec_with_tool_scope(
            spec,
            user_message=loop.task_description,
            available_state={
                "loop.type": loop.type,
                "loop.cadence_minutes": loop.interval_minutes,
                "loop.prior_results": store.list_loop_results(loop.config_dir, loop.id, limit=5),
                "loop.auto_fix": loop.auto_fix,
            },
            allowed_tools=allowed_tools,         # hard tool-scope enforcement, same as today
            safety_ceiling="S1",                 # same S0/S1 ceiling as deterministic types
        )
        return LoopResult(
            status=result.get("status", "ok"),
            summary=result.get("summary", ""),
            should_notify=result.get("should_notify", False),
        )

    def _execute_deterministic(self, loop: LoopRecord, allowed_tools: list[str]) -> LoopResult:
        # existing cost_monitor / health_monitor logic, unchanged
        ...
```

```yaml
# harness/agents/loop_executor.md — NEW spec, follows the planner.md reference format

---
name: loop_executor
version: "1.0"
role: >
  Executes one cycle of an LLM-driven monitoring loop (repo_watcher, model_quality,
  ci_monitor, or custom). Observes current state via the tools it's been scoped to,
  reasons about whether anything needs attention, and reports back. Never takes
  irreversible action unless prior cycles already established the need.

tools:
  - state.read
  # actual tool set is injected per-call from LOOP_TYPES[loop.type]["tools"] —
  # this agent NEVER gets tools outside what the loop type declares

max_turns: 6
budget_seconds: 90
budget_llm_calls: 3
max_contract_retries: 2

input_contract:
  required_state_keys:
    - loop.type
    - loop.cadence_minutes
  optional_state_keys:
    - loop.prior_results
    - loop.auto_fix

output_contract:
  type: json_object
  description: >
    A single JSON object reporting what was observed and whether the user should
    be notified.
  item_schema:
    status:
      type: string
      enum: ["ok", "attention_needed", "failed"]
    summary:
      type: string
      description: One to two sentences describing what was observed this cycle.
    should_notify:
      type: boolean
      description: True only if this cycle found something the user should see now.
    action_taken:
      type: "string | null"
      description: >
        If auto_fix is true AND the situation is genuinely safe and reversible,
        describe what was done. Otherwise null — default to reporting, not acting.
---

# Loop Executor — System Prompt

You are Duckln's Loop Executor, running one cycle of a recurring monitoring loop.

## Hard rules

- You are scoped to exactly the tools provided to you this call. Do not attempt to use
  any tool not in your available set — there is no escalation path mid-cycle.
- Default to reporting, not acting. Only take action if `loop.auto_fix` is true AND the
  action is safe, reversible, and directly addresses what you observed this cycle.
- Never take an irreversible action (delete, force-push, drop, terminate) under any
  circumstances, regardless of `auto_fix`. The deterministic safety floor blocks these
  anyway, but you should never attempt them.
- If you find nothing notable, say so in one line. Do not invent concerns to seem useful.
- Compare against `loop.prior_results` when available — a single bad reading often isn't
  worth notifying about; a worsening trend across cycles usually is.
- You have a small turn and token budget. Be direct: observe, reason briefly, report.
  Do not narrate your process at length.

## Self-check before responding

- [ ] Did I only use tools from my allowed set?
- [ ] If I'm reporting `should_notify: true`, would a reasonable person actually want
      to be interrupted for this?
- [ ] If I took an action, was it safe, reversible, and explicitly authorized by
      `auto_fix` being true?
- [ ] Is my summary one to two sentences, not a transcript of my reasoning?
```

### Why this matches the docs, not just "adds an LLM somewhere"
The official maintenance prompt's exact pattern is: bounded tool scope, default-deny on
new initiatives, irreversible actions gated on prior authorization, concise one-line
reporting when nothing is wrong. `loop_executor.md` mirrors every one of those
constraints, not just "call an LLM instead of stubbing." This keeps Duckln's existing
safety model (S0/S1 ceiling, per-type tool scope, `_safe_loop_command` blocklist) fully
intact — the LLM call happens *inside* that same sandbox, it doesn't bypass it.

### Test to add
```python
def test_llm_driven_loop_respects_tool_scope(tmp_config_dir, mock_llm_client):
    loop = create_test_loop(type="repo_watcher")
    # mock_llm_client configured to attempt a tool outside repo_watcher's allowed set
    result = LoopSubAgent().execute(loop)
    assert result.status == "failed"
    assert "outside allowed scope" in result.summary.lower()

def test_llm_driven_loop_never_attempts_irreversible_action(tmp_config_dir, mock_llm_client):
    loop = create_test_loop(type="ci_monitor", auto_fix=True)
    # mock_llm_client returns action_taken describing a force-push
    result = LoopSubAgent().execute(loop)
    assert result.status == "failed"  # deterministic safety floor catches it regardless

def test_stub_types_no_longer_silently_return_ok(tmp_config_dir):
    for loop_type in ["repo_watcher", "model_quality", "ci_monitor", "custom"]:
        loop = create_test_loop(type=loop_type)
        result = LoopSubAgent().execute(loop)
        # must have gone through _execute_llm_driven, not the old stub path
        assert result.summary != ""
        assert result.status in {"ok", "attention_needed", "failed"}
```

---

## Gap 3 — No diagnose-fix-retest persistence; deterministic types can't escalate

### What's still missing after Gaps 1 and 2
Gap 2 gives the 4 stub types one reasoning pass per cycle: observe, decide, optionally
take one action, report. That's still short of what was asked for: a loop that, given a
goal, actually works the problem — tries a fix, checks if it worked, tries again if not,
and only stops when resolved or genuinely out of room to try. It also leaves
`cost_monitor` and `health_monitor` permanently deterministic with no path to reasoning
when their fixed logic hits something it doesn't know how to handle (e.g. health check
fails, but the failure mode isn't one of the pre-coded restart scenarios).

### Decisions locked in for this fix
- **Bounded retries per cycle** (not unbounded): try fix → retest → repeat, capped at
  `max_fix_attempts` (default 3) within one scheduled cycle. Unbounded was rejected
  because this is an unattended background loop with no one watching in real time —
  unlike Claude Code's `/goal`, which a developer actively started. Unbounded retries on
  a timer risks silent runaway cost and compounding a wrong fix, which is exactly the
  error-accumulation risk Anthropic's docs cite as the reason for bounding loops at all.
- **All loop types can escalate to LLM reasoning**, not just the 4 LLM-driven ones.
  Deterministic types (`cost_monitor`, `health_monitor`) keep their fast fixed-logic path
  for the common case, and only invoke the LLM when that fixed logic fails to resolve
  the problem — so the common case stays cheap and fast, and the LLM is reserved for
  exactly the "messy, context-dependent situations that break traditional automation"
  Anthropic's docs describe.
- **Honest-stop + notify** when attempts are exhausted: report what was tried, why each
  attempt didn't resolve it, and stop — never retry silently, never escalate to a
  human-approval queue (that's more process than a background loop needs; the
  notification itself is the escalation).

### The fix — diagnose-fix-retest cycle inside `LoopSubAgent.execute`

```python
# loop_runtime.py — add a bounded fix-retest loop inside execution,
# shared by deterministic types (on fixed-logic failure) and LLM-driven types

@dataclass
class LoopExecutionResult:
    status: str                      # "ok" | "attention_needed" | "fixed" | "failed"
    summary: str
    should_notify: bool
    attempts: list[FixAttempt] = field(default_factory=list)


@dataclass
class FixAttempt:
    attempt_number: int
    diagnosis: str
    action_taken: str | None
    retest_result: str               # "resolved" | "unresolved" | "action_skipped"


class LoopSubAgent:
    MAX_FIX_ATTEMPTS = 3             # default; can be overridden per loop type

    def execute(self, loop: LoopRecord) -> LoopExecutionResult:
        if loop.type not in LOOP_TYPES:
            return LoopExecutionResult(status="failed", summary=f"unknown loop type {loop.type}", should_notify=True)

        allowed_tools = LOOP_TYPES[loop.type]["tools"]

        # 1. Try the fast path first — deterministic logic for deterministic types,
        #    single LLM reasoning pass for LLM-driven types (Gap 2 behavior, unchanged).
        first_pass = self._fast_path(loop, allowed_tools)

        if first_pass.status == "ok":
            return first_pass   # nothing wrong, no escalation needed — cheapest path

        if first_pass.status == "attention_needed" and not loop.auto_fix:
            return first_pass   # user wants notification only, not auto-fix — don't escalate

        # 2. Something needs fixing AND auto_fix is on — escalate into the bounded
        #    diagnose-fix-retest loop, regardless of whether this is a deterministic
        #    or LLM-driven loop type.
        return self._diagnose_fix_retest_loop(loop, allowed_tools, first_pass)

    def _fast_path(self, loop: LoopRecord, allowed_tools: list[str]) -> LoopExecutionResult:
        if loop.type in LLM_DRIVEN_TYPES:
            return self._execute_llm_driven(loop, allowed_tools)   # Gap 2, single pass
        else:
            return self._execute_deterministic(loop, allowed_tools)   # existing fixed logic

    def _diagnose_fix_retest_loop(
        self, loop: LoopRecord, allowed_tools: list[str], first_pass: LoopExecutionResult
    ) -> LoopExecutionResult:
        """
        Bounded fix-retest cycle. Used by ALL loop types once the fast path has
        identified a problem and auto_fix is enabled. Each attempt is one
        diagnose → act → retest pass through the loop_executor spec, capped at
        MAX_FIX_ATTEMPTS. Honest-stops with a full attempt history if unresolved.
        """
        max_attempts = LOOP_TYPES[loop.type].get("max_fix_attempts", self.MAX_FIX_ATTEMPTS)
        attempts: list[FixAttempt] = []
        problem_context = first_pass.summary

        for n in range(1, max_attempts + 1):
            spec = get_agent_spec("loop_executor")
            result = run_spec_with_tool_scope(
                spec,
                user_message=loop.task_description,
                available_state={
                    "loop.type": loop.type,
                    "loop.problem_context": problem_context,
                    "loop.prior_attempts": [a.__dict__ for a in attempts],
                    "loop.attempt_number": n,
                    "loop.max_attempts": max_attempts,
                    "loop.auto_fix": loop.auto_fix,
                },
                allowed_tools=allowed_tools,
                safety_ceiling="S1",
            )

            attempt = FixAttempt(
                attempt_number=n,
                diagnosis=result.get("diagnosis", ""),
                action_taken=result.get("action_taken"),
                retest_result=result.get("retest_result", "unresolved"),
            )
            attempts.append(attempt)

            if attempt.retest_result == "resolved":
                return LoopExecutionResult(
                    status="fixed",
                    summary=f"Resolved after {n} attempt(s). {attempt.diagnosis}",
                    should_notify=True,   # always tell the user something was auto-fixed
                    attempts=attempts,
                )

            # carry forward what was learned for the next attempt's context
            problem_context = (
                f"{problem_context}\nAttempt {n} tried: {attempt.action_taken or 'no action'}. "
                f"Result: still unresolved. {attempt.diagnosis}"
            )

        # 3. Exhausted attempts — honest-stop, notify with full history.
        return LoopExecutionResult(
            status="failed",
            summary=(
                f"Could not resolve after {max_attempts} attempts. "
                f"Last diagnosis: {attempts[-1].diagnosis}"
            ),
            should_notify=True,
            attempts=attempts,
        )

    def _execute_llm_driven(self, loop, allowed_tools) -> LoopExecutionResult:
        # Gap 2's single-pass implementation, unchanged — used as the fast path here
        ...

    def _execute_deterministic(self, loop, allowed_tools) -> LoopExecutionResult:
        # existing cost_monitor / health_monitor fixed logic, unchanged — used as the
        # fast path here. On failure (status != "ok"), control passes to the bounded
        # fix-retest loop above, which is the NEW escalation path for these types.
        ...
```

### Updated `loop_executor.md` — add attempt-aware fields to the contract

```yaml
# harness/agents/loop_executor.md — extend the output_contract from Gap 2

output_contract:
  type: json_object
  description: >
    A single JSON object reporting this attempt's diagnosis, any action taken,
    and whether a retest confirms the problem is resolved.
  item_schema:
    status:
      type: string
      enum: ["ok", "attention_needed", "failed"]
    diagnosis:
      type: string
      description: >
        What's wrong, in one to two sentences. On attempt 2+, account for what prior
        attempts already tried — do not repeat an action that already failed.
    action_taken:
      type: "string | null"
      description: >
        The fix attempted this pass, if auto_fix is true and the action is safe and
        reversible. Null if only diagnosing or if no safe action is available.
    retest_result:
      type: string
      enum: ["resolved", "unresolved", "action_skipped"]
      description: >
        After taking action, re-check the original condition. "resolved" only if
        directly verified — do not assume a fix worked without checking.
    should_notify:
      type: boolean
```

```markdown
# Loop Executor — additional system prompt rules for multi-attempt cycles

## Multi-attempt awareness

- You may be called multiple times within one scheduled cycle (`loop.attempt_number` of
  `loop.max_attempts`). Read `loop.prior_attempts` before doing anything — never repeat
  an action that a prior attempt already tried and that didn't resolve the problem.
- Always retest after taking action. Report `retest_result: "resolved"` only if you
  directly verified the original problem is gone — never assume a fix worked.
- If you're on the final attempt (`attempt_number == max_attempts`) and the problem
  persists, focus your diagnosis on a clear, specific explanation of why — this becomes
  the message the user sees when the loop honest-stops. Vague diagnoses ("something
  went wrong") are not acceptable on the final attempt.
- You may diagnose without acting (`action_taken: null`) if no safe, reversible fix is
  available — this is a valid outcome, not a failure to find one.
```

### Why bounded retries, not unbounded
This was an explicit decision, not a default. Claude Code's `/goal` can run unbounded
because a developer is the one who invoked it and remains implicitly available to
interrupt. Duckln's loop fires on a timer with no one watching. Unbounded retries here
would mean: a wrong diagnosis on attempt 1 could compound through attempts 4, 5, 6...
with no cost ceiling and no natural point where a human reviews what's happening. Bounded
retries (default 3) caps the blast radius of a single cycle, composes with the existing
`max_contract_retries` pattern already used elsewhere in the harness, and still gives
real persistence — try, check, try again — without becoming an unattended unbounded
agent loop.

### Test to add
```python
def test_fix_retest_loop_resolves_within_attempts(tmp_config_dir, mock_llm_client):
    # mock returns unresolved on attempt 1, resolved on attempt 2
    mock_llm_client.set_responses([
        {"diagnosis": "port closed", "action_taken": "restarted service", "retest_result": "unresolved"},
        {"diagnosis": "service now up", "action_taken": None, "retest_result": "resolved"},
    ])
    loop = create_test_loop(type="health_monitor", auto_fix=True)
    result = LoopSubAgent().execute(loop)
    assert result.status == "fixed"
    assert len(result.attempts) == 2

def test_fix_retest_loop_honest_stops_after_max_attempts(tmp_config_dir, mock_llm_client):
    mock_llm_client.set_responses([
        {"diagnosis": f"attempt {n} failed", "action_taken": "tried X", "retest_result": "unresolved"}
        for n in range(1, 4)
    ])
    loop = create_test_loop(type="ci_monitor", auto_fix=True)
    result = LoopSubAgent().execute(loop)
    assert result.status == "failed"
    assert len(result.attempts) == 3
    assert result.should_notify is True

def test_deterministic_type_escalates_to_llm_on_fixed_logic_failure(tmp_config_dir, mock_llm_client):
    # health_monitor's fixed restart logic fails to resolve; should escalate
    loop = create_test_loop(type="health_monitor", auto_fix=True)
    result = LoopSubAgent().execute(loop)
    assert mock_llm_client.was_called()   # confirms escalation actually happened

def test_no_escalation_when_auto_fix_disabled(tmp_config_dir, mock_llm_client):
    loop = create_test_loop(type="ci_monitor", auto_fix=False)
    result = LoopSubAgent().execute(loop)
    assert result.status == "attention_needed"
    assert mock_llm_client.call_count <= 1   # only the single diagnostic pass, no fix loop

def test_each_attempt_sees_prior_attempt_history(tmp_config_dir, mock_llm_client):
    loop = create_test_loop(type="custom", auto_fix=True)
    LoopSubAgent().execute(loop)
    second_call_state = mock_llm_client.call_args_list[1].kwargs["available_state"]
    assert len(second_call_state["loop.prior_attempts"]) == 1
```

---

## What NOT to change

These parts of Duckln's `/loop` already match the documented Desktop-tier pattern
correctly and should be left alone:

- APScheduler + SQLAlchemy jobstore for restart-persistent scheduling.
- Per-type fixed tool scope with out-of-scope rejection.
- S0/S1 safety ceiling, S2+ blocked+escalate.
- `_safe_loop_command` blocklist on auto-fix commands.
- Fail-and-continue per-cycle error handling (don't kill the whole loop on one bad
  cycle) — matches the official default exactly.
- In-process scheduler, not a standalone daemon — this is the correct, honestly-stated
  limitation of the Desktop tier; the docs don't claim Desktop tasks survive the machine
  being off either, only the session/app being closed.

---

## Summary for the engineer

Three PRs, independently shippable, in this order:

1. **Expiry PR** — add `expires_at`/`expiry_days` to `LoopSpec`, check it in
   `_run_scheduled_loop`, deactivate + notify on expiry, surface days-remaining in
   `/loop list`. Default 30 days, configurable per loop.

2. **LLM-driven execution PR** — add `harness/agents/loop_executor.md`, route
   `repo_watcher`/`model_quality`/`ci_monitor`/`custom` through it instead of the current
   stub, keep `cost_monitor`/`health_monitor` deterministic and untouched. Tool scope and
   safety ceiling are enforced identically to today — the LLM call happens inside the
   existing sandbox, not instead of it.

3. **Bounded fix-retest PR** — add the diagnose-fix-retest cycle to `LoopSubAgent.execute`,
   shared by all loop types. Deterministic types try their fixed logic first and escalate
   to LLM reasoning only on failure; LLM-driven types use it directly after their single
   diagnostic pass identifies a problem. Capped at `max_fix_attempts` (default 3) per
   cycle, honest-stops with full attempt history on exhaustion, always notifies on both
   resolution and exhaustion.

Together, these three bring Duckln's `/loop` to genuine feature-parity with what Claude
Code's own loop does — observe, reason, act, verify, persist within a bound, report
honestly — while keeping the same safety floor (tool scope, S0/S1 ceiling, blocklist) that
made Duckln's original design sound. None of the three change the correct architectural
choice of building this as a Desktop-tier, restart-persistent, local feature; they change
what happens *inside* one scheduled cycle, not how cycles are scheduled or persisted.