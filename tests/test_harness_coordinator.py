"""Plan 65 Phase 4 — Coordinator tests (parallel spawn, budget cascade, partial results)."""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path

from duckln.harness.agent_def import AgentDefinition, AgentRegistry
from duckln.harness.bus import MessageBus
from duckln.harness.coordinator import (
    ConcurrencyGate,
    SpawnRequest,
    build_spawn_fn,
    make_gated_llm_client,
    run_coordinator,
)
from duckln.harness.tools import AgentContext, ToolRegistry, ToolResult, ToolSpec
from duckln.modes import ControlMode
from duckln.safety import SafetyClass


def _ctx() -> AgentContext:
    return AgentContext(
        agent_name="coord",
        mode=ControlMode.HOTL,
        config_dir=Path("/tmp/duckln-harness-test"),
    )


def _scripted_llm(decisions: list[dict]):
    queue = list(decisions)
    def _client(*, system_prompt: str, user_message: str) -> str:
        if not queue:
            return json.dumps({"stop": True, "reason": "out_of_script"})
        return json.dumps(queue.pop(0))
    return _client


def _build_registries(*, child_tools=("noop.tool",)):
    """Make a minimal agent registry with one coordinator + one child spec
    that uses ``noop.tool`` (a SafetyClass.S0 noop)."""
    coord = AgentDefinition(
        name="coord", role="lead", system_prompt="lead the team.",
        tools=("noop.tool",), max_turns=2, budget_seconds=5.0, budget_llm_calls=2,
        can_spawn=("childA", "childB"), is_coordinator=True,
    )
    childA = AgentDefinition(
        name="childA", role="investigator A", system_prompt="probe.",
        tools=child_tools, max_turns=3, budget_seconds=5.0, budget_llm_calls=3,
    )
    childB = AgentDefinition(
        name="childB", role="investigator B", system_prompt="probe.",
        tools=child_tools, max_turns=3, budget_seconds=5.0, budget_llm_calls=3,
    )
    agent_reg = AgentRegistry()
    agent_reg.register(coord)
    agent_reg.register(childA)
    agent_reg.register(childB)
    tool_reg = ToolRegistry()
    tool_reg.register(ToolSpec(
        name="noop.tool", description="echo",
        args_schema={"label": {"type": "str", "required": False}},
        safety_class=SafetyClass.S0,
        handler=lambda args, ctx: ToolResult.success({"echoed": args, "agent": ctx.agent_name}),
    ))
    return agent_reg, tool_reg, coord


class CoordinatorStaticPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_plan_returns_no_plan_reason(self) -> None:
        agent_reg, tool_reg, coord = _build_registries()
        result = await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(), llm_client=_scripted_llm([{"stop": True, "reason": "n/a"}]),
        )
        self.assertEqual(result.stopped_reason, "no_plan")
        self.assertEqual(result.children, ())

    async def test_spawns_one_child_completes(self) -> None:
        agent_reg, tool_reg, coord = _build_registries()
        llm = _scripted_llm([
            {"tool": "noop.tool", "args": {"label": "A"}, "reason": "first"},
            {"stop": True, "reason": "done"},
        ])
        result = await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(), llm_client=llm,
            plan=(SpawnRequest(agent_name="childA", initial_input={"goal": "probe"}),),
        )
        self.assertEqual(len(result.children), 1)
        self.assertEqual(result.children[0].agent_name, "childA")
        self.assertEqual(result.stopped_reason, "completed")

    async def test_spawns_two_children_in_parallel(self) -> None:
        agent_reg, tool_reg, coord = _build_registries()
        # Use a stub LLM that needs different scripts per child. Build a
        # shared queue and a router so each call to the LLM gets the next
        # available decision regardless of which child made the call.
        queue = [
            {"tool": "noop.tool", "args": {"label": "x"}, "reason": "go"},
            {"stop": True, "reason": "done"},
            {"tool": "noop.tool", "args": {"label": "y"}, "reason": "go"},
            {"stop": True, "reason": "done"},
        ]
        def _shared(*, system_prompt, user_message):
            if not queue:
                return json.dumps({"stop": True, "reason": "drained"})
            return json.dumps(queue.pop(0))
        started = time.monotonic()
        result = await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(), llm_client=_shared,
            plan=(
                SpawnRequest(agent_name="childA", initial_input={"k": "a"}),
                SpawnRequest(agent_name="childB", initial_input={"k": "b"}),
            ),
        )
        elapsed = time.monotonic() - started
        self.assertEqual({c.agent_name for c in result.children}, {"childA", "childB"})
        # Parallelism: 2 children of ~0 work each should complete WELL under 5s
        self.assertLess(elapsed, 4.0)

    async def test_aggregated_findings_summarize_children(self) -> None:
        agent_reg, tool_reg, coord = _build_registries()
        result = await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(),
            llm_client=_scripted_llm([
                {"tool": "noop.tool", "args": {}, "reason": "g"},
                {"stop": True, "reason": "d"},
                {"tool": "noop.tool", "args": {}, "reason": "g"},
                {"stop": True, "reason": "d"},
            ]),
            plan=(
                SpawnRequest(agent_name="childA", initial_input={}),
                SpawnRequest(agent_name="childB", initial_input={}),
            ),
        )
        self.assertEqual(len(result.aggregated_findings), 2)
        for finding in result.aggregated_findings:
            self.assertIn(finding["agent"], {"childA", "childB"})
            self.assertIn("succeeded", finding)
            self.assertIn("observation_count", finding)


class CoordinatorBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_child_raises(self) -> None:
        agent_reg, tool_reg, coord = _build_registries()
        spawn = build_spawn_fn(
            agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(), llm_client=_scripted_llm([{"stop": True, "reason": "n/a"}]),
        )
        with self.assertRaises(ValueError):
            await spawn(SpawnRequest(agent_name="ghost", initial_input={}))

    async def test_non_coordinator_rejected(self) -> None:
        # A non-coordinator agent (is_coordinator=False) must not be runnable
        # via run_coordinator.
        not_coord = AgentDefinition(
            name="not_coord", role="r", system_prompt="p", tools=("noop.tool",),
            is_coordinator=False,
        )
        agent_reg, tool_reg, _ = _build_registries()
        with self.assertRaises(ValueError):
            await run_coordinator(
                not_coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
                context=_ctx(), llm_client=_scripted_llm([]),
                plan=(SpawnRequest(agent_name="childA", initial_input={}),),
            )


class CoordinatorBusIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_result_published_to_bus(self) -> None:
        agent_reg, tool_reg, coord = _build_registries()
        bus = MessageBus()
        await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(),
            llm_client=_scripted_llm([
                {"tool": "noop.tool", "args": {}, "reason": "g"},
                {"stop": True, "reason": "d"},
            ]),
            bus=bus,
            plan=(SpawnRequest(agent_name="childA", initial_input={}),),
        )
        history = bus.history("agent.childA.result")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].sender, "childA")


class ConcurrencyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_gate_limits_concurrent_acquires(self) -> None:
        gate = ConcurrencyGate(slots=2)
        # Three tasks compete for 2 slots; only 2 should hold the gate at once.
        in_flight = 0
        peak = 0
        sem_lock = asyncio.Lock()

        async def _task():
            nonlocal in_flight, peak
            await gate.acquire()
            async with sem_lock:
                in_flight += 1
                peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.05)
            finally:
                async with sem_lock:
                    in_flight -= 1
                gate.release()

        await asyncio.gather(_task(), _task(), _task())
        self.assertLessEqual(peak, 2)

    def test_invalid_slot_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ConcurrencyGate(slots=0)

    def test_slots_property_exposes_configured_value(self) -> None:
        self.assertEqual(ConcurrencyGate(slots=3).slots, 3)


class GatedLLMClientTests(unittest.TestCase):
    def test_no_running_loop_calls_through(self) -> None:
        # When no asyncio loop is active, the wrapper passes through.
        called = []
        def _base(*, system_prompt, user_message):
            called.append((system_prompt, user_message))
            return "ok"
        gate = ConcurrencyGate(slots=2)
        wrapped = make_gated_llm_client(_base, gate)
        result = wrapped(system_prompt="sys", user_message="user")
        self.assertEqual(result, "ok")
        self.assertEqual(called, [("sys", "user")])


class CoordinatorTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_coordinator_timeout_returns_partial_results(self) -> None:
        # A coordinator with a tiny budget that gets exceeded.
        coord = AgentDefinition(
            name="quick_coord", role="r", system_prompt="p",
            tools=("noop.tool",), is_coordinator=True,
            budget_seconds=0.1,  # very small
        )
        slow_child = AgentDefinition(
            name="slow_child", role="r", system_prompt="p",
            tools=("slow.tool",), max_turns=10, budget_seconds=10.0, budget_llm_calls=10,
        )
        agent_reg = AgentRegistry()
        agent_reg.register(coord)
        agent_reg.register(slow_child)

        def _slow_handler(args, ctx):
            time.sleep(0.3)
            return ToolResult.success({})
        tool_reg = ToolRegistry()
        tool_reg.register(ToolSpec(
            name="slow.tool", description="slow", args_schema={},
            safety_class=SafetyClass.S0, handler=_slow_handler,
        ))
        tool_reg.register(ToolSpec(
            name="noop.tool", description="noop", args_schema={},
            safety_class=SafetyClass.S0,
            handler=lambda a, c: ToolResult.success({}),
        ))
        llm = _scripted_llm([
            {"tool": "slow.tool", "args": {}, "reason": "g"},
            {"stop": True, "reason": "d"},
        ])
        result = await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(), llm_client=llm,
            plan=(SpawnRequest(agent_name="slow_child", initial_input={}),),
        )
        # The coordinator's tight budget should trip the timeout path.
        self.assertEqual(result.stopped_reason, "coordinator_budget_timeout")
        self.assertEqual(len(result.children), 1)
        # Child reports either coordinator_budget_timeout or coordinator_timeout
        # depending on which path raised first; both signal correct partial-result handling.
        self.assertIn(
            result.children[0].stop_reason,
            ("coordinator_budget_timeout", "coordinator_timeout"),
        )


class SpawnPerRequestTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_request_timeout_overrides_definition(self) -> None:
        """When SpawnRequest carries an explicit timeout, it overrides the
        agent's default budget_seconds."""
        coord = AgentDefinition(
            name="coord", role="lead", system_prompt="p",
            tools=("noop.tool",), is_coordinator=True, budget_seconds=10.0,
        )
        slow_child = AgentDefinition(
            name="slow_child", role="r", system_prompt="p",
            tools=("slow.tool",), max_turns=10, budget_seconds=60.0, budget_llm_calls=10,
        )
        agent_reg = AgentRegistry()
        agent_reg.register(coord)
        agent_reg.register(slow_child)
        tool_reg = ToolRegistry()
        tool_reg.register(ToolSpec(
            name="slow.tool", description="slow", args_schema={},
            safety_class=SafetyClass.S0,
            handler=lambda a, c: (time.sleep(0.3), ToolResult.success({}))[-1],
        ))
        tool_reg.register(ToolSpec(
            name="noop.tool", description="noop", args_schema={},
            safety_class=SafetyClass.S0,
            handler=lambda a, c: ToolResult.success({}),
        ))
        llm = _scripted_llm([
            {"tool": "slow.tool", "args": {}, "reason": "g"},
            {"stop": True, "reason": "d"},
        ])
        result = await run_coordinator(
            coord, {}, agent_registry=agent_reg, tool_registry=tool_reg,
            context=_ctx(), llm_client=llm,
            plan=(SpawnRequest(
                agent_name="slow_child", initial_input={},
                timeout_seconds=0.05,
            ),),
        )
        # Spawn-level timeout fires before coord-level budget.
        self.assertEqual(len(result.children), 1)
        self.assertEqual(result.children[0].stop_reason, "coordinator_timeout")


if __name__ == "__main__":
    unittest.main()
