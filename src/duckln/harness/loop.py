"""Plan 65 Phase 3 — Single-agent harness loop.

``run_agent`` drives one agent through its reasoning loop:

1. Render state for the LLM (truncated to fit a token budget).
2. One LLM call → returns ``{tool, args, reason}`` OR ``{stop, reason}``.
3. Validate tool name against the agent's allow-list and the args against
   the tool's schema.
4. Dispatch via ``ToolRegistry``.
5. Append the observation; check budgets; loop.

The loop is provider-agnostic — callers inject an ``LLMClient`` callable.
For tests, inject a deterministic mock; in production, wrap
``duckln.ai_client.generate_provider_reply``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from duckln.harness.agent_def import AgentDefinition
from duckln.harness.state import (
    AgentBudgets,
    AgentResult,
    AgentState,
    Observation,
    ProposalAttempt,
)
from duckln.harness.tools import AgentContext, ToolRegistry, ToolResult


# --- LLM client abstraction --------------------------------------------------


class LLMClient(Protocol):
    """Minimal interface: given a system prompt + user message, return a
    JSON-string decision. The harness parses the JSON."""

    def __call__(self, *, system_prompt: str, user_message: str) -> str: ...


@dataclass(frozen=True)
class TurnEvent:
    """One turn's full record, emitted to the display callback for streaming."""

    turn: int
    agent_name: str
    tool: str
    args: dict
    reason: str
    ok: bool
    latency_ms: float
    error_code: str | None = None
    result: dict | None = None


# --- State serialization for the LLM prompt ----------------------------------


# Plan 142 F3: don't starve the agent's context. The old 2400/3500-char keyhole meant the
# agent lost most of what it read (file contents, the error log) to truncation — so even a
# capable model reasoned poorly. These generous-but-bounded budgets let it RETAIN evidence;
# very large logs are still folded by the rolling-summary map-reduce (context_budget.py).
_OBSERVATION_BUDGET_CHARS = 8000
_PROPOSAL_BUDGET_CHARS = 1200
_HINT_BUDGET_CHARS = 600


def render_state_for_llm(state: AgentState, *, max_chars: int = 14000, model_id: str | None = None) -> str:
    """Serialize the agent state into a user-message payload for the LLM.

    Smart truncation: most recent observations first, then proposals, then
    user hints, then the initial input. Total stays under ``max_chars``.
    Plan 142 F3: ``max_chars`` is generous so the agent keeps real evidence; callers with a
    small-context model can pass a smaller value.
    Plan 194 F4: when ``model_id`` is given, the budget becomes MODEL-AWARE — capped to the model's
    usable context window (a small local model compacts sooner, a big model later) — while the
    pinned facts (F1) always survive.
    """
    # Plan 194 F4: MODEL-AWARE compaction — a small local window keeps FEWER recent observations
    # verbatim (compacts sooner) and caps the char budget to the model's usable window; a big model
    # keeps more. The pinned facts (F1) always survive regardless.
    live_window = 6
    if model_id:
        from duckln.harness.context_budget import usable_context_tokens

        usable = usable_context_tokens(model_id)
        max_chars = min(max_chars, max(2000, usable * 4))
        live_window = 4 if usable < 8000 else (6 if usable < 40000 else 10)
    parts: list[str] = []
    # Plan 194 F1 (§5 guardrail): pinned critical facts FIRST and VERBATIM, so compaction/truncation
    # can never turn "what actually happened" into a lossy story. Small, never folded.
    pinned = [str(f).strip() for f in getattr(state, "pinned_facts", []) or [] if str(f).strip()]
    if pinned:
        parts.append("Pinned facts (always true this session — never ignore):\n"
                     + "\n".join(f"- {f}" for f in pinned[:8]))
    # Plan 142 F3: the FAILING error/command lives in initial_input — 500 chars truncated
    # the real evidence away. Keep enough of it to actually reason on.
    parts.append(f"Initial input:\n{json.dumps(state.initial_input, indent=2, default=str)[:4000]}\n")

    if state.user_hints:
        hint_text = "User hints:\n" + "\n".join(f"- {h}" for h in state.user_hints[-5:])
        parts.append(hint_text[:_HINT_BUDGET_CHARS])

    if state.proposals:
        prop_lines = ["Proposals so far (do NOT propose any of these again):"]
        for prop in state.proposals[-5:]:
            outcome = (
                "succeeded" if prop.succeeded is True
                else "failed" if prop.succeeded is False
                else "pending"
            )
            prop_lines.append(
                f"- turn {prop.turn}: `{prop.command[:80]}` "
                f"({outcome}; rationale: {prop.rationale[:120]})"
            )
        parts.append("\n".join(prop_lines)[:_PROPOSAL_BUDGET_CHARS])

    if state.observations:
        # Plan 104: keep the recent observations full-fidelity and fold older ones
        # into a rolling summary (context engineering) instead of blunt truncation.
        from duckln.harness.context_budget import assemble_observation_block

        block = assemble_observation_block(
            state.observations, live_window=live_window, summarizer=getattr(state, "summarizer", None)
        )
        parts.append(block[:_OBSERVATION_BUDGET_CHARS])

    serialized = "\n\n".join(parts)
    if len(serialized) > max_chars:
        serialized = serialized[:max_chars] + "\n…(truncated)"
    return serialized


# --- LLM decision parsing ----------------------------------------------------


@dataclass(frozen=True)
class AgentDecision:
    tool: str | None  # None when stop is requested
    args: dict
    reason: str
    stop: bool = False


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def parse_llm_decision(raw: str) -> AgentDecision:
    """Parse the LLM's JSON reply into an ``AgentDecision``.

    Tolerates:
    - Optional ```json``` fencing
    - Leading/trailing whitespace and prose
    - Missing optional fields (defaults to empty)
    """
    text = (raw or "").strip()
    if not text:
        return AgentDecision(tool=None, args={}, reason="empty_llm_reply", stop=True)
    # Extract from code fences if present.
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()
    # Find the JSON object — the LLM sometimes prefaces with prose.
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start < 0 or obj_end <= obj_start:
        return AgentDecision(
            tool=None, args={}, reason=f"unparseable: {text[:120]}", stop=True
        )
    try:
        parsed = json.loads(text[obj_start:obj_end + 1])
    except json.JSONDecodeError as exc:
        return AgentDecision(
            tool=None, args={}, reason=f"json_error: {exc}", stop=True
        )
    if not isinstance(parsed, dict):
        return AgentDecision(tool=None, args={}, reason="not_an_object", stop=True)
    stop_field = parsed.get("stop")
    if stop_field:
        return AgentDecision(
            tool=None, args={}, reason=str(parsed.get("reason", "stop_requested")), stop=True
        )
    tool = parsed.get("tool")
    if tool == "give_up":
        return AgentDecision(
            tool=None, args={}, reason=str(parsed.get("reason", "give_up")), stop=True
        )
    if not isinstance(tool, str) or not tool:
        return AgentDecision(
            tool=None, args={}, reason="missing_tool_field", stop=True
        )
    args = parsed.get("args", {})
    if not isinstance(args, dict):
        args = {}
    reason = str(parsed.get("reason", ""))
    return AgentDecision(tool=tool, args=args, reason=reason, stop=False)


# --- The loop itself ---------------------------------------------------------


def run_agent(
    definition: AgentDefinition,
    initial_input: dict,
    *,
    context: AgentContext,
    tool_registry: ToolRegistry,
    llm_client: LLMClient,
    display: Callable[[TurnEvent], None] | None = None,
    tool_decider: Callable[[str, str], AgentDecision] | None = None,
) -> AgentResult:
    """Drive a single agent through its reasoning loop until done.

    Returns when:
    - The LLM emits a stop signal (or ``give_up``)
    - Budgets exhaust (turns / LLM calls / wall-clock)
    - An unrecoverable error occurs (e.g. zero allowed tools)
    """
    started = time.monotonic()
    budgets = AgentBudgets(
        seconds_remaining=definition.budget_seconds,
        llm_calls_remaining=definition.budget_llm_calls,
        turns_remaining=definition.max_turns,
        started_at=started,
    )
    state = AgentState(initial_input=initial_input, budgets=budgets)

    if not definition.tools:
        state.stop("no_tools_allowed", final_result="agent has no allowed tools")
        return _finalize(definition, state, started)

    # Plan 174 F4: count identical tool calls so a weak model can't loop the SAME call (e.g.
    # `web.search "how was your day"` ×7) until the budget drains.
    _call_signatures: dict[str, int] = {}

    while not state.stopped:
        exhausted, reason = budgets.exhausted()
        if exhausted:
            state.stop(reason)
            break

        # Build the user message from the current state.
        user_message = render_state_for_llm(state)
        budgets.consume_llm_call()
        system_prompt = _compose_system_prompt(definition)
        try:
            if tool_decider is not None:
                # Plan 99: native provider function-calling path (Anthropic/OpenAI).
                decision = tool_decider(system_prompt, user_message)
            else:
                raw = llm_client(system_prompt=system_prompt, user_message=user_message)
                decision = parse_llm_decision(raw)
        except Exception as exc:
            state.stop(f"llm_error: {type(exc).__name__}: {exc}")
            break
        budgets.consume_turn()
        state.turn_count = definition.max_turns - budgets.turns_remaining

        if decision.stop:
            state.stop(decision.reason or "stop_requested")
            break

        # Validate against the agent's tool allow-list.
        if decision.tool not in definition.tools:
            obs = Observation(
                turn=state.turn_count,
                tool=decision.tool or "<unknown>",
                args=decision.args,
                ok=False,
                error_code="tool_not_allowed",
                error_message=(
                    f"Agent '{definition.name}' is not allowed to call '{decision.tool}'. "
                    f"Allowed tools: {', '.join(definition.tools)}"
                ),
            )
            state.record_observation(obs)
            _emit(display, _event_from(decision, obs, definition))
            continue  # let the LLM try again next turn

        # Plan 174 F4: bound REPEATED identical calls. After the 2nd identical (tool, args),
        # stop dispatching it and nudge the agent to conclude or try something different — so a
        # weak model can't loop the same call to budget exhaustion.
        _sig = f"{decision.tool}::{json.dumps(decision.args, sort_keys=True, default=str)}"
        _call_signatures[_sig] = _call_signatures.get(_sig, 0) + 1
        if _call_signatures[_sig] >= 3:
            obs = Observation(
                turn=state.turn_count,
                tool=decision.tool,
                args=decision.args,
                ok=False,
                error_code="repeated_call",
                error_message=(
                    f"You already ran this exact call {_call_signatures[_sig] - 1}× with no new "
                    "result — do NOT repeat it. Conclude from the evidence you have, or try a "
                    "DIFFERENT approach."
                ),
            )
            state.record_observation(obs)
            _emit(display, _event_from(decision, obs, definition))
            continue

        # Dispatch via the tool registry.
        call_start = time.monotonic()
        result: ToolResult = tool_registry.dispatch(decision.tool, decision.args, context)
        latency_ms = (time.monotonic() - call_start) * 1000.0

        obs = Observation(
            turn=state.turn_count,
            tool=decision.tool,
            args=decision.args,
            ok=result.ok,
            payload=result.payload,
            error_code=result.error_code,
            error_message=result.error_message,
            latency_ms=latency_ms,
        )
        state.record_observation(obs)

        # If the tool was a fix-style command, also track it as a proposal.
        if decision.tool == "shell.run" and result.ok and isinstance(result.payload, dict):
            exit_code = result.payload.get("exit_code")
            state.record_proposal(
                ProposalAttempt(
                    turn=state.turn_count,
                    command=str(decision.args.get("command", "")),
                    rationale=decision.reason,
                    approved=True,
                    succeeded=(exit_code == 0) if isinstance(exit_code, int) else None,
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                    stderr_excerpt=str(result.payload.get("stderr", ""))[:200],
                )
            )

        _emit(display, _event_from(decision, obs, definition))

    return _finalize(definition, state, started)


def _compose_system_prompt(definition: AgentDefinition) -> str:
    """Wrap the agent's authored system prompt with the harness's
    JSON-output (tool-use) contract, plus the spec's own output_contract (Plan 157 P2-3)."""
    tool_list = "\n".join(f"  - {t}" for t in definition.tools)
    contract = (
        "OUTPUT FORMAT (REQUIRED):\n"
        "Return ONLY a JSON object with this shape:\n"
        '  {"tool": "<one of the allowed tools>", "args": {...}, "reason": "short why"}\n'
        '  OR {"stop": true, "reason": "why stopping"}\n'
        '  OR {"tool": "give_up", "reason": "why"}  to abandon the task.\n'
        "Do NOT wrap in markdown, do NOT add prose around the JSON.\n\n"
        f"Your allowed tools:\n{tool_list}\n"
    )
    base = f"{definition.system_prompt}\n\n{contract}"
    # Plan 157 P2-3: surface the spec's output_contract (the final-result schema) too.
    from duckln.harness.agent_def import render_output_contract_text

    oc = render_output_contract_text(definition)
    return base + (f"\n\nFINAL RESULT — {oc}" if oc else "")


@dataclass(frozen=True)
class SpecRunResult:
    """Plan 157 P2: outcome of a structured-output spec run (non-tool-use agents)."""

    ok: bool
    parsed: object
    raw: str
    errors: tuple[str, ...]
    missing_input_keys: tuple[str, ...]


def run_spec(
    definition: AgentDefinition,
    user_message: str,
    llm_client,
    *,
    available_state=None,
    display=None,
):
    """Plan 157 P2/P3: run a STRUCTURED-OUTPUT agent spec (planner/critic/clarifier/attributor)
    directly against the model — input-validate, compose `system_prompt = body + output_contract`,
    call the model, validate the output against the contract, and on violation RE-ASK up to
    `definition.max_contract_retries` with the exact schema + the specific violation. Returns a
    SpecRunResult. This is the spec-driven replacement for the hardcoded plan_mode prompts;
    it keeps the tight pipeline IN-PROCESS (a direct call, not the tool loop)."""
    from duckln.harness.agent_def import (
        render_output_contract_text,
        validate_agent_output,
        validate_input_contract,
    )

    missing = validate_input_contract(definition, available_state if available_state is not None else {})
    oc = render_output_contract_text(definition)
    system_prompt = definition.system_prompt + (f"\n\n{oc}" if oc else "")
    attempts = 1 + max(0, int(definition.max_contract_retries))
    msg = user_message
    raw = ""
    last_errors: tuple[str, ...] = ()
    for _attempt in range(attempts):
        try:
            raw = llm_client(system_prompt=system_prompt, user_message=msg)
        except Exception as exc:  # noqa: BLE001 — surface as a contract failure, caller falls back
            return SpecRunResult(False, None, "", (f"llm_error: {type(exc).__name__}: {exc}",), missing)
        ok, parsed, errors = validate_agent_output(definition, raw)
        if ok:
            return SpecRunResult(True, parsed, raw, (), missing)
        last_errors = errors
        # Bounded re-ask: include the exact schema + the specific violation.
        msg = (
            f"{user_message}\n\nYour previous reply did NOT satisfy the output contract: "
            f"{'; '.join(errors)}.\n{oc}\nReturn ONLY the corrected output — no prose, no fences."
        )
    return SpecRunResult(False, None, raw, last_errors, missing)


def _emit(display: Callable[[TurnEvent], None] | None, event: TurnEvent) -> None:
    if display is None:
        return
    try:
        display(event)
    except Exception:
        pass  # never let display callbacks crash the loop


def _event_from(
    decision: AgentDecision, obs: Observation, definition: AgentDefinition
) -> TurnEvent:
    return TurnEvent(
        turn=obs.turn,
        agent_name=definition.name,
        tool=obs.tool,
        args=obs.args,
        reason=decision.reason,
        ok=obs.ok,
        latency_ms=obs.latency_ms or 0.0,
        error_code=obs.error_code,
        result=obs.payload if isinstance(obs.payload, dict) else None,
    )


def _finalize(
    definition: AgentDefinition, state: AgentState, started: float
) -> AgentResult:
    elapsed = time.monotonic() - started
    return AgentResult(
        agent_name=definition.name,
        succeeded=state.stop_reason in ("", "stop_requested") and bool(state.observations),
        stop_reason=state.stop_reason or "ok",
        turn_count=state.turn_count,
        elapsed_seconds=elapsed,
        observations=tuple(state.observations),
        proposals=tuple(state.proposals),
        final_payload=state.final_result,
    )
