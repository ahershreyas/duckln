"""Plan 65 Phase 3 — single-agent harness loop tests."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from duckln.harness.agent_def import AgentDefinition
from duckln.harness.loop import (
    AgentDecision,
    TurnEvent,
    parse_llm_decision,
    render_state_for_llm,
    run_agent,
)
from duckln.harness.state import AgentState, Observation, AgentBudgets
from duckln.harness.tools import (
    AgentContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from duckln.modes import ControlMode
from duckln.safety import SafetyClass


# --- Helpers -----------------------------------------------------------------


def _make_definition(*, tools=("noop.tool",), max_turns=4, budget_seconds=10.0, budget_llm_calls=4) -> AgentDefinition:
    return AgentDefinition(
        name="test_agent",
        role="A deterministic agent for unit tests.",
        system_prompt="Be terse.",
        tools=tools,
        max_turns=max_turns,
        budget_seconds=budget_seconds,
        budget_llm_calls=budget_llm_calls,
    )


def _make_registry(*specs: ToolSpec) -> ToolRegistry:
    reg = ToolRegistry()
    for spec in specs:
        reg.register(spec)
    return reg


def _noop_tool(name: str, *, returns=None, fails: tuple[str, str] | None = None) -> ToolSpec:
    def _handler(args, ctx):
        if fails is not None:
            return ToolResult.failure(fails[0], fails[1])
        return ToolResult.success(returns if returns is not None else {"args": args})
    return ToolSpec(
        name=name, description="noop", args_schema={},
        safety_class=SafetyClass.S0, handler=_handler,
    )


def _ctx() -> AgentContext:
    return AgentContext(
        agent_name="test_agent",
        mode=ControlMode.HOTL,
        config_dir=Path("/tmp/duckln-test-harness"),
    )


def _scripted_llm(decisions: list[str | dict]):
    """Build an LLMClient that returns the next scripted decision each call.

    Strings are returned verbatim; dicts are json-encoded.
    """
    queue = list(decisions)

    def _client(*, system_prompt: str, user_message: str) -> str:
        if not queue:
            return json.dumps({"stop": True, "reason": "out_of_script"})
        nxt = queue.pop(0)
        return nxt if isinstance(nxt, str) else json.dumps(nxt)

    return _client


# --- Decision parser ---------------------------------------------------------


class ParseDecisionTests(unittest.TestCase):
    def test_valid_tool_call(self) -> None:
        d = parse_llm_decision('{"tool": "noop.tool", "args": {"x": 1}, "reason": "test"}')
        self.assertFalse(d.stop)
        self.assertEqual(d.tool, "noop.tool")
        self.assertEqual(d.args, {"x": 1})
        self.assertEqual(d.reason, "test")

    def test_stop_signal(self) -> None:
        d = parse_llm_decision('{"stop": true, "reason": "done"}')
        self.assertTrue(d.stop)
        self.assertEqual(d.reason, "done")

    def test_give_up_alias(self) -> None:
        d = parse_llm_decision('{"tool": "give_up", "reason": "no idea"}')
        self.assertTrue(d.stop)
        self.assertIn("no idea", d.reason)

    def test_json_fence_tolerated(self) -> None:
        d = parse_llm_decision('```json\n{"tool":"x.y","args":{},"reason":"ok"}\n```')
        self.assertEqual(d.tool, "x.y")

    def test_prose_before_json_tolerated(self) -> None:
        d = parse_llm_decision('I will use the tool: {"tool":"x.y","args":{},"reason":"ok"}')
        self.assertEqual(d.tool, "x.y")

    def test_empty_reply_stops(self) -> None:
        d = parse_llm_decision("")
        self.assertTrue(d.stop)

    def test_malformed_json_stops(self) -> None:
        d = parse_llm_decision("{not json at all")
        self.assertTrue(d.stop)

    def test_missing_tool_stops(self) -> None:
        d = parse_llm_decision('{"reason": "?"}')
        self.assertTrue(d.stop)


# --- State serialization -----------------------------------------------------


class RenderStateTests(unittest.TestCase):
    def test_includes_initial_input(self) -> None:
        state = AgentState(initial_input={"goal": "investigate failure"})
        rendered = render_state_for_llm(state)
        self.assertIn("investigate failure", rendered)

    def test_includes_recent_observations(self) -> None:
        state = AgentState(initial_input={})
        state.record_observation(Observation(
            turn=1, tool="shell.probe", args={"command": "node --version"},
            ok=True, payload={"stdout": "v18.19"},
        ))
        rendered = render_state_for_llm(state)
        self.assertIn("shell.probe", rendered)
        self.assertIn("v18.19", rendered)

    def test_truncates_to_max_chars(self) -> None:
        state = AgentState(initial_input={"x": "y" * 10000})
        rendered = render_state_for_llm(state, max_chars=500)
        self.assertLessEqual(len(rendered), 500 + 20)  # truncation marker
        self.assertIn("truncated", rendered.lower())


# --- run_agent: happy path & control flow ------------------------------------


class RunAgentHappyPathTests(unittest.TestCase):
    def test_single_tool_call_then_stop(self) -> None:
        defn = _make_definition(tools=("noop.tool",))
        reg = _make_registry(_noop_tool("noop.tool", returns={"ok": True}))
        llm = _scripted_llm([
            {"tool": "noop.tool", "args": {"x": 1}, "reason": "test"},
            {"stop": True, "reason": "done"},
        ])
        result = run_agent(defn, {"goal": "test"}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].tool, "noop.tool")
        self.assertTrue(result.observations[0].ok)

    def test_multi_turn_sequence(self) -> None:
        defn = _make_definition(tools=("noop.tool",), max_turns=5)
        reg = _make_registry(_noop_tool("noop.tool"))
        llm = _scripted_llm([
            {"tool": "noop.tool", "args": {"step": 1}, "reason": "first"},
            {"tool": "noop.tool", "args": {"step": 2}, "reason": "second"},
            {"tool": "noop.tool", "args": {"step": 3}, "reason": "third"},
            {"stop": True, "reason": "done"},
        ])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(len(result.observations), 3)
        self.assertEqual([o.args["step"] for o in result.observations], [1, 2, 3])

    def test_display_callback_receives_events(self) -> None:
        events: list[TurnEvent] = []
        defn = _make_definition(tools=("noop.tool",))
        reg = _make_registry(_noop_tool("noop.tool"))
        llm = _scripted_llm([
            {"tool": "noop.tool", "args": {}, "reason": "go"},
            {"stop": True, "reason": "done"},
        ])
        run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm, display=events.append)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].tool, "noop.tool")
        self.assertTrue(events[0].ok)


# --- Budgets -----------------------------------------------------------------


class BudgetExhaustionTests(unittest.TestCase):
    def test_max_turns_exhausts(self) -> None:
        defn = _make_definition(tools=("noop.tool",), max_turns=2)
        reg = _make_registry(_noop_tool("noop.tool"))
        # LLM never asks to stop — loop should hit max_turns.
        llm = _scripted_llm([{"tool": "noop.tool", "args": {}, "reason": "loop"}] * 10)
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(result.stop_reason, "max_turns_reached")
        self.assertLessEqual(len(result.observations), 2)

    def test_llm_call_budget_exhausts(self) -> None:
        defn = _make_definition(tools=("noop.tool",), budget_llm_calls=2, max_turns=10)
        reg = _make_registry(_noop_tool("noop.tool"))
        llm = _scripted_llm([{"tool": "noop.tool", "args": {}, "reason": "loop"}] * 10)
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(result.stop_reason, "llm_call_budget_exhausted")

    def test_wall_clock_exhausts(self) -> None:
        defn = _make_definition(tools=("slow.tool",), budget_seconds=0.05, max_turns=10, budget_llm_calls=10)
        def _slow_handler(args, ctx):
            time.sleep(0.1)
            return ToolResult.success({})
        spec = ToolSpec(
            name="slow.tool", description="slow", args_schema={},
            safety_class=SafetyClass.S0, handler=_slow_handler,
        )
        reg = _make_registry(spec)
        llm = _scripted_llm([{"tool": "slow.tool", "args": {}, "reason": "slow"}] * 5)
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(result.stop_reason, "wall_clock_budget_exhausted")


# --- Validation --------------------------------------------------------------


class ValidationTests(unittest.TestCase):
    def test_tool_not_in_allowlist_observed_as_error(self) -> None:
        defn = _make_definition(tools=("allowed.tool",))
        reg = _make_registry(
            _noop_tool("allowed.tool"),
            _noop_tool("forbidden.tool"),
        )
        llm = _scripted_llm([
            {"tool": "forbidden.tool", "args": {}, "reason": "try"},
            {"stop": True, "reason": "done"},
        ])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(len(result.observations), 1)
        self.assertFalse(result.observations[0].ok)
        self.assertEqual(result.observations[0].error_code, "tool_not_allowed")

    def test_schema_violation_observed(self) -> None:
        defn = _make_definition(tools=("strict.tool",))
        strict = ToolSpec(
            name="strict.tool", description="needs cmd",
            args_schema={"cmd": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0,
            handler=lambda args, ctx: ToolResult.success({}),
        )
        reg = _make_registry(strict)
        llm = _scripted_llm([
            {"tool": "strict.tool", "args": {}, "reason": "missing"},
            {"stop": True, "reason": "done"},
        ])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(result.observations[0].error_code, "schema_violation")

    def test_empty_tools_stops_via_run_agent(self) -> None:
        # Construct AgentDefinition directly (the dataclass allows empty tools).
        defn = AgentDefinition(
            name="empty_agent", role="r", system_prompt="p", tools=(),
        )
        reg = ToolRegistry()
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=lambda **k: "")
        self.assertEqual(result.stop_reason, "no_tools_allowed")


# --- Stop signals ------------------------------------------------------------


class StopSignalsTests(unittest.TestCase):
    def test_give_up_signals_stop(self) -> None:
        defn = _make_definition(tools=("noop.tool",))
        reg = _make_registry(_noop_tool("noop.tool"))
        llm = _scripted_llm([{"tool": "give_up", "reason": "no leads"}])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertIn("no leads", result.stop_reason)

    def test_empty_llm_reply_stops(self) -> None:
        defn = _make_definition(tools=("noop.tool",))
        reg = _make_registry(_noop_tool("noop.tool"))
        llm = _scripted_llm([""])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(result.stop_reason, "empty_llm_reply")

    def test_llm_exception_caught(self) -> None:
        def _broken(**_kw):
            raise RuntimeError("provider down")
        defn = _make_definition(tools=("noop.tool",))
        reg = _make_registry(_noop_tool("noop.tool"))
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=_broken)
        self.assertIn("llm_error", result.stop_reason)
        self.assertIn("provider down", result.stop_reason)


# --- Proposal tracking -------------------------------------------------------


class ProposalTrackingTests(unittest.TestCase):
    def test_shell_run_success_tracked_as_succeeded_proposal(self) -> None:
        def _shell(args, ctx):
            return ToolResult.success({"exit_code": 0, "stdout": "ok", "stderr": ""})
        spec = ToolSpec(
            name="shell.run", description="shell",
            args_schema={"command": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0, handler=_shell,
        )
        defn = _make_definition(tools=("shell.run",))
        reg = _make_registry(spec)
        llm = _scripted_llm([
            {"tool": "shell.run", "args": {"command": "echo hi"}, "reason": "say hi"},
            {"stop": True, "reason": "done"},
        ])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(len(result.proposals), 1)
        self.assertTrue(result.proposals[0].succeeded)
        self.assertEqual(result.proposals[0].command, "echo hi")

    def test_shell_run_failure_tracked_as_failed_proposal(self) -> None:
        def _shell(args, ctx):
            return ToolResult.success({"exit_code": 1, "stdout": "", "stderr": "boom"})
        spec = ToolSpec(
            name="shell.run", description="shell",
            args_schema={"command": {"type": "str", "required": True}},
            safety_class=SafetyClass.S0, handler=_shell,
        )
        defn = _make_definition(tools=("shell.run",))
        reg = _make_registry(spec)
        llm = _scripted_llm([
            {"tool": "shell.run", "args": {"command": "false"}, "reason": "expect fail"},
            {"stop": True, "reason": "done"},
        ])
        result = run_agent(defn, {}, context=_ctx(), tool_registry=reg, llm_client=llm)
        self.assertEqual(len(result.proposals), 1)
        self.assertFalse(result.proposals[0].succeeded)
        self.assertEqual(result.proposals[0].exit_code, 1)
        self.assertIn("boom", result.proposals[0].stderr_excerpt)


if __name__ == "__main__":
    unittest.main()
