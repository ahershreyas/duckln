"""Plan 65 — Duckln multi-agent harness.

Public surface (Phase 1 only — Tool Registry):

* ``AgentContext`` — per-run context passed to every tool handler.
* ``ToolSpec`` / ``ToolResult`` / ``ToolCostHint`` — declarative tool schema.
* ``ToolRegistry`` — central catalog with mode-aware dispatch.
* ``build_default_registry`` — registry pre-populated with the 11 initial tools.

Later phases will add ``AgentDefinition`` (Phase 2), ``run_agent`` (Phase 3),
``MessageBus`` + ``Coordinator`` (Phase 4), and ``trace`` (Phase 5).
"""

from __future__ import annotations

from duckln.harness.agent_def import (
    AgentDefinition,
    AgentRegistry,
    builtin_agents_directory,
    load_agent_definition_from_path,
    load_agent_definition_from_text,
    parse_agent_spec_markdown,
)
from duckln.harness.bus import Message, MessageBus
from duckln.harness.coordinator import (
    ConcurrencyGate,
    CoordinatorResult,
    SpawnRequest,
    build_spawn_fn,
    make_gated_llm_client,
    run_coordinator,
)
from duckln.harness.recovery_flow import (
    HARNESS_ENV_VAR,
    RecoveryOutcome,
    RecoveryRequest,
    harness_enabled,
    run_multi_agent_recovery,
)
from duckln.harness.plan_supervisor import (
    PlanSupervisorResult,
    REQUIRED_AGENT_NAMES,
    run_plan_supervisor,
)
from duckln.harness.loop import (
    AgentDecision,
    LLMClient,
    TurnEvent,
    parse_llm_decision,
    render_state_for_llm,
    run_agent,
)
from duckln.harness.state import (
    AgentBudgets,
    AgentResult,
    AgentState,
    Observation,
    ProposalAttempt,
)
from duckln.harness.tools import (
    AgentContext,
    ToolCostHint,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_default_registry,
)

__all__ = (
    "AgentBudgets",
    "AgentContext",
    "AgentDecision",
    "AgentDefinition",
    "AgentRegistry",
    "AgentResult",
    "AgentState",
    "ConcurrencyGate",
    "CoordinatorResult",
    "HARNESS_ENV_VAR",
    "LLMClient",
    "Message",
    "MessageBus",
    "Observation",
    "PlanSupervisorResult",
    "ProposalAttempt",
    "RecoveryOutcome",
    "RecoveryRequest",
    "REQUIRED_AGENT_NAMES",
    "SpawnRequest",
    "ToolCostHint",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "TurnEvent",
    "build_default_registry",
    "build_spawn_fn",
    "builtin_agents_directory",
    "harness_enabled",
    "load_agent_definition_from_path",
    "load_agent_definition_from_text",
    "make_gated_llm_client",
    "parse_agent_spec_markdown",
    "parse_llm_decision",
    "render_state_for_llm",
    "run_agent",
    "run_coordinator",
    "run_multi_agent_recovery",
    "run_plan_supervisor",
)
