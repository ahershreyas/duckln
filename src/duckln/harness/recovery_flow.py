"""Plan 65 Phase 6b — Multi-agent recovery flow.

When a setup or runtime step fails AND the cheap classifier doesn't have a
high-confidence fix, this module runs the harness's parallel-investigation
recovery: it spawns ``investigate_agent``, ``search_agent``, and
``memory_agent`` concurrently under ``recovery_coordinator``, then the
coordinator weighs their findings and proposes a single fix.

The entry point ``run_multi_agent_recovery`` is callable from the legacy
``_execute_plan_with_bounded_recovery`` loop (Phase 6a integration). When
the feature flag ``DUCKLN_HARNESS`` is unset, this module is never reached —
legacy code paths handle the failure as today.

This file is the bridge between the legacy state-machine and the new
harness; it owns the translation from "step failed with stderr X" into the
initial_input dict the coordinator expects.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from duckln.harness.agent_def import AgentRegistry, builtin_agents_directory
from duckln.harness.bus import MessageBus
from duckln.harness.coordinator import (
    CoordinatorResult,
    SpawnRequest,
    run_coordinator,
)
from duckln.harness.loop import LLMClient, TurnEvent
from duckln.harness.state import AgentResult
from duckln.harness.tools import AgentContext, ToolRegistry, build_default_registry
from duckln.harness.trace import TraceLogger
from duckln.modes import ControlMode


# --- Feature flag ------------------------------------------------------------


HARNESS_ENV_VAR = "DUCKLN_HARNESS"


def harness_enabled() -> bool:
    """True when the user opted into the harness via env var.

    Default = legacy code paths (existing 1330 tests' behaviour preserved).
    Set ``DUCKLN_HARNESS=1`` to route recovery through the multi-agent flow.
    """
    return os.environ.get(HARNESS_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


# --- Public entry points -----------------------------------------------------


@dataclass(frozen=True)
class RecoveryRequest:
    """Inputs from the legacy loop to start a multi-agent recovery."""

    failed_command: str
    step_purpose: str
    stderr: str
    stdout: str
    exit_code: int | None
    repo_slug: str
    project_dir: Path
    execution_target: str
    config_dir: Path
    mode: ControlMode


@dataclass(frozen=True)
class RecoveryOutcome:
    """What the multi-agent recovery delivered back to the legacy loop."""

    succeeded: bool
    summary: str
    session_id: str
    coordinator_elapsed_seconds: float
    children: tuple[AgentResult, ...]


def run_multi_agent_recovery(
    request: RecoveryRequest,
    *,
    llm_client: LLMClient,
    display: Callable[[str], None] | None = None,
    approve: Callable[[str], bool] | None = None,
    tool_registry: ToolRegistry | None = None,
    agent_registry: AgentRegistry | None = None,
) -> RecoveryOutcome:
    """Run the recovery coordinator + three specialists in parallel.

    Synchronous wrapper around the async coordinator so legacy callers don't
    need to be async-aware. Internally spins an asyncio event loop just for
    this recovery session.
    """
    if tool_registry is None:
        tool_registry = build_default_registry(include_handlers=True)
    if agent_registry is None:
        agent_registry = AgentRegistry.from_directory(builtin_agents_directory())

    coordinator_def = agent_registry.lookup("recovery_coordinator")
    if coordinator_def is None:
        return RecoveryOutcome(
            succeeded=False,
            summary="harness agent 'recovery_coordinator' not registered",
            session_id="",
            coordinator_elapsed_seconds=0.0,
            children=(),
        )

    trace = TraceLogger(config_dir=request.config_dir)
    bus = MessageBus()

    initial_problem = {
        "failed_command": request.failed_command,
        "step_purpose": request.step_purpose,
        "stderr_excerpt": (request.stderr or "")[-2000:],
        "stdout_excerpt": (request.stdout or "")[-500:],
        "exit_code": request.exit_code,
        "repo_slug": request.repo_slug,
        "execution_target": request.execution_target,
    }

    context = AgentContext(
        agent_name="recovery_coordinator",
        mode=request.mode,
        config_dir=request.config_dir,
        project_dir=request.project_dir,
        execution_target=request.execution_target,
        display=lambda msg: display(msg) if display else None,
        approve=approve,
        extra={"session_id": trace.session_id},
    )

    # Static parallel plan: spawn the three specialists with the same initial
    # context.
    specialist_input = dict(initial_problem)
    plan = (
        SpawnRequest(
            agent_name="investigate_agent",
            initial_input=specialist_input,
            timeout_seconds=120.0,
        ),
        SpawnRequest(
            agent_name="search_agent",
            initial_input=specialist_input,
            timeout_seconds=90.0,
        ),
        SpawnRequest(
            agent_name="memory_agent",
            initial_input=specialist_input,
            timeout_seconds=30.0,
        ),
    )

    def _on_turn(event: TurnEvent) -> None:
        try:
            trace.record_turn(
                agent=event.agent_name,
                turn=event.turn,
                tool=event.tool,
                args=event.args,
                ok=event.ok,
                result_summary=("ok" if event.ok else event.error_code or "error"),
                latency_ms=event.latency_ms,
                error_code=event.error_code,
                reason=event.reason,
            )
        except Exception:
            pass
        if display is not None and event.tool:
            display(f"[{event.agent_name}] {event.tool} → {('ok' if event.ok else event.error_code)}")

    started = time.monotonic()
    try:
        coord_result = asyncio.run(
            run_coordinator(
                coordinator_def,
                initial_problem,
                agent_registry=agent_registry,
                tool_registry=tool_registry,
                context=context,
                llm_client=llm_client,
                bus=bus,
                display=_on_turn,
                plan=plan,
            )
        )
    except RuntimeError as exc:
        # asyncio.run cannot be called from within an active event loop. If
        # that happens (e.g. integration into an async caller), fall back to
        # running on the existing loop.
        if "asyncio.run() cannot be called" not in str(exc):
            raise
        loop = asyncio.get_event_loop()
        coord_result = loop.run_until_complete(
            run_coordinator(
                coordinator_def,
                initial_problem,
                agent_registry=agent_registry,
                tool_registry=tool_registry,
                context=context,
                llm_client=llm_client,
                bus=bus,
                display=_on_turn,
                plan=plan,
            )
        )
    elapsed = time.monotonic() - started

    return RecoveryOutcome(
        succeeded=_did_recovery_succeed(coord_result),
        summary=_summarize_recovery(coord_result),
        session_id=trace.session_id,
        coordinator_elapsed_seconds=elapsed,
        children=coord_result.children,
    )


# --- Helpers -----------------------------------------------------------------


def _did_recovery_succeed(result: CoordinatorResult) -> bool:
    """Heuristic: recovery succeeded if at least one specialist reported a
    successful run AND coordinator completed (not timed-out)."""
    if result.stopped_reason != "completed":
        return False
    return any(child.succeeded for child in result.children)


def _summarize_recovery(result: CoordinatorResult) -> str:
    """One-line human-readable summary for display."""
    if result.stopped_reason == "no_plan":
        return "Recovery coordinator received no plan; nothing executed."
    if result.stopped_reason == "coordinator_budget_timeout":
        return (
            f"Coordinator timed out after {result.elapsed_seconds:.1f}s; "
            f"{len(result.children)} specialists ran partially."
        )
    parts = []
    for child in result.children:
        status = "ok" if child.succeeded else child.stop_reason or "failed"
        parts.append(f"{child.agent_name}={status}")
    return f"Recovery completed in {result.elapsed_seconds:.1f}s — {', '.join(parts)}"
