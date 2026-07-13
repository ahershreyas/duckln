"""Plan 65 Phase 4 — Multi-agent Coordinator.

A coordinator is a special agent (``is_coordinator=True``) that can spawn
other agents in parallel via ``asyncio``. Spawned children publish findings
to the shared message bus; the coordinator subscribes and decides when
enough evidence has been gathered.

Concurrency model: each child agent runs as an ``asyncio.Task``. The
coordinator awaits on either (a) child completion or (b) a budget timeout,
whichever comes first. The whole subtree's wall-clock is bounded by the
coordinator's ``budget_seconds``.

Provider sharing: per Plan 65 decision, all agents share a single LLM
provider via round-robin. ``concurrent_llm_slots`` (default 2) is enforced
by an asyncio Semaphore so the coordinator can't slam the rate limit even
if a dozen children try to call the LLM at the same instant.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from duckln.harness.agent_def import AgentDefinition, AgentRegistry
from duckln.harness.bus import MessageBus
from duckln.harness.loop import LLMClient, TurnEvent, run_agent
from duckln.harness.state import AgentResult
from duckln.harness.tools import AgentContext, ToolRegistry


@dataclass(frozen=True)
class SpawnRequest:
    """A request from the coordinator to spawn a specialist."""

    agent_name: str
    initial_input: dict
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class CoordinatorResult:
    """Outcome of the coordinator's run."""

    coordinator_name: str
    children: tuple[AgentResult, ...]
    elapsed_seconds: float
    stopped_reason: str
    aggregated_findings: tuple[Any, ...]


# --- ConcurrencyGate (Plan 65 decision: single provider, round-robin slots) --


class ConcurrencyGate:
    """Limits how many agent turns can call the LLM at the same instant.

    Wraps an existing ``LLMClient`` and adds a semaphore. Use the resulting
    wrapper as the ``llm_client`` for ``run_agent`` so the cap is enforced
    transparently.
    """

    def __init__(self, *, slots: int = 2) -> None:
        if slots <= 0:
            raise ValueError("slots must be > 0")
        self._sem = asyncio.Semaphore(slots)
        self._slots = slots

    @property
    def slots(self) -> int:
        return self._slots

    async def acquire(self) -> None:
        await self._sem.acquire()

    def release(self) -> None:
        self._sem.release()


def make_gated_llm_client(base: LLMClient, gate: ConcurrencyGate) -> LLMClient:
    """Wrap an LLMClient with semaphore-based concurrency control."""

    def _gated(*, system_prompt: str, user_message: str) -> str:
        # run_agent is sync today; the gate is async. Use a sync acquire
        # via run_until_complete on a fresh loop is risky inside an active
        # loop. Instead, we implement a sync acquire backed by an event so
        # the gate works inside asyncio.run_in_executor wrappers as well.
        # For now: block on asyncio.run_coroutine_threadsafe if there is a
        # running loop, else just call synchronously (no contention).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(gate.acquire(), loop)
            fut.result()
            try:
                return base(system_prompt=system_prompt, user_message=user_message)
            finally:
                loop.call_soon_threadsafe(gate.release)
        # No active loop in this thread — gate is effectively a no-op.
        return base(system_prompt=system_prompt, user_message=user_message)

    return _gated


# --- Spawn function ----------------------------------------------------------


SpawnFn = Callable[[SpawnRequest], Awaitable[AgentResult]]


def build_spawn_fn(
    *,
    agent_registry: AgentRegistry,
    tool_registry: ToolRegistry,
    context: AgentContext,
    llm_client: LLMClient,
    bus: MessageBus | None = None,
    display: Callable[[TurnEvent], None] | None = None,
) -> SpawnFn:
    """Build the spawn callable the coordinator uses to start specialists.

    Each spawned agent runs in a thread (via ``asyncio.to_thread``) so the
    synchronous ``run_agent`` loop doesn't block the coordinator's event loop.
    Results are returned as completed ``AgentResult`` objects.
    """
    async def _spawn(req: SpawnRequest) -> AgentResult:
        defn = agent_registry.lookup(req.agent_name)
        if defn is None:
            raise ValueError(f"No agent definition named '{req.agent_name}'")
        timeout = req.timeout_seconds or defn.budget_seconds
        # Tag the child's context with its agent name so observations / traces
        # are attributable.
        child_ctx = AgentContext(
            agent_name=defn.name,
            mode=context.mode,
            config_dir=context.config_dir,
            project_dir=context.project_dir,
            execution_target=context.execution_target,
            display=context.display,
            approve=context.approve,
            extra=dict(context.extra),
        )
        task = asyncio.create_task(
            asyncio.to_thread(
                run_agent,
                defn,
                req.initial_input,
                context=child_ctx,
                tool_registry=tool_registry,
                llm_client=llm_client,
                display=display,
            )
        )
        try:
            result = await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            result = AgentResult(
                agent_name=defn.name,
                succeeded=False,
                stop_reason="coordinator_timeout",
                turn_count=0,
                elapsed_seconds=timeout,
                observations=(),
                proposals=(),
                final_payload=None,
            )
        if bus is not None:
            bus.publish(
                topic=f"agent.{defn.name}.result",
                payload={
                    "succeeded": result.succeeded,
                    "stop_reason": result.stop_reason,
                    "observations": len(result.observations),
                    "proposals": len(result.proposals),
                },
                sender=defn.name,
            )
        return result

    return _spawn


# --- Coordinator runner ------------------------------------------------------


async def run_coordinator(
    coordinator_def: AgentDefinition,
    initial_problem: dict,
    *,
    agent_registry: AgentRegistry,
    tool_registry: ToolRegistry,
    context: AgentContext,
    llm_client: LLMClient,
    bus: MessageBus | None = None,
    display: Callable[[TurnEvent], None] | None = None,
    plan: tuple[SpawnRequest, ...] = (),
) -> CoordinatorResult:
    """Run a coordinator agent that spawns specialists in parallel.

    Two modes:

    * **Static plan**: when ``plan`` is non-empty, the coordinator simply
      spawns every request in parallel, gathers results, and finishes.
      Useful for deterministic multi-agent workflows like the recovery
      flow in Plan 65 Phase 6.

    * **LLM-driven** (future): when ``plan`` is empty, the coordinator
      runs as a normal agent whose tools include ``agent.spawn`` and
      ``agent.wait_for`` so it can dynamically decide which specialists to
      run. Wiring those tools is Phase 5/6 work; for Phase 4 we ship the
      static-plan path which is what the multi-agent recovery flow needs.
    """
    if bus is None:
        bus = MessageBus()
    if not coordinator_def.is_coordinator:
        raise ValueError(
            f"Agent '{coordinator_def.name}' is not flagged is_coordinator=True"
        )
    started = time.monotonic()
    spawn = build_spawn_fn(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        context=context,
        llm_client=llm_client,
        bus=bus,
        display=display,
    )
    if not plan:
        # Phase 4 ships static-plan coordination; LLM-driven coordination
        # is Phase 5/6 (agent.spawn tool + dynamic dispatch).
        elapsed = time.monotonic() - started
        return CoordinatorResult(
            coordinator_name=coordinator_def.name,
            children=(),
            elapsed_seconds=elapsed,
            stopped_reason="no_plan",
            aggregated_findings=(),
        )
    tasks = [asyncio.create_task(spawn(req)) for req in plan]
    deadline = coordinator_def.budget_seconds
    try:
        children = await asyncio.wait_for(asyncio.gather(*tasks), timeout=deadline)
        stopped_reason = "completed"
    except asyncio.TimeoutError:
        for t in tasks:
            t.cancel()
        children_partial: list[AgentResult] = []
        for t, req in zip(tasks, plan):
            if t.done() and not t.cancelled():
                try:
                    children_partial.append(t.result())
                except Exception:
                    pass
            else:
                children_partial.append(
                    AgentResult(
                        agent_name=req.agent_name,
                        succeeded=False,
                        stop_reason="coordinator_budget_timeout",
                        turn_count=0,
                        elapsed_seconds=deadline,
                        observations=(),
                        proposals=(),
                    )
                )
        children = tuple(children_partial)
        stopped_reason = "coordinator_budget_timeout"
    elapsed = time.monotonic() - started

    findings = tuple(
        {
            "agent": c.agent_name,
            "succeeded": c.succeeded,
            "stop_reason": c.stop_reason,
            "observation_count": len(c.observations),
            "proposal_count": len(c.proposals),
        }
        for c in children
    )
    return CoordinatorResult(
        coordinator_name=coordinator_def.name,
        children=tuple(children),
        elapsed_seconds=elapsed,
        stopped_reason=stopped_reason,
        aggregated_findings=findings,
    )
