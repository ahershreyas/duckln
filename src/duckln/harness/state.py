"""Plan 65 Phase 3 — AgentState (mutable per-run state for one harness agent).

The state object is the single source of truth for a running agent. Tool
handlers read context from it (the ``AgentContext``); the loop writes back
observations, decisions, and budget bookkeeping after each turn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Observation:
    """One thing the agent learned by calling a tool."""

    turn: int
    tool: str
    args: dict
    ok: bool
    payload: Any = None
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: float | None = None


@dataclass(frozen=True)
class ProposalAttempt:
    """A fix the agent proposed and the outcome of executing it."""

    turn: int
    command: str
    rationale: str
    approved: bool
    succeeded: bool | None  # None means not yet verified
    exit_code: int | None = None
    stderr_excerpt: str = ""


@dataclass
class AgentBudgets:
    """Mutable budgets enforced by the harness loop."""

    seconds_remaining: float
    llm_calls_remaining: int
    turns_remaining: int
    started_at: float = field(default_factory=time.monotonic)

    def consume_turn(self) -> None:
        self.turns_remaining -= 1

    def consume_llm_call(self) -> None:
        self.llm_calls_remaining -= 1

    def remaining_seconds(self) -> float:
        # Track wall-clock from started_at; seconds_remaining is the deadline budget.
        elapsed = time.monotonic() - self.started_at
        return max(0.0, self.seconds_remaining - elapsed)

    def exhausted(self) -> tuple[bool, str]:
        if self.turns_remaining <= 0:
            return True, "max_turns_reached"
        if self.llm_calls_remaining <= 0:
            return True, "llm_call_budget_exhausted"
        if self.remaining_seconds() <= 0:
            return True, "wall_clock_budget_exhausted"
        return False, ""


@dataclass
class AgentState:
    """Mutable state for one running agent."""

    initial_input: dict
    observations: list[Observation] = field(default_factory=list)
    proposals: list[ProposalAttempt] = field(default_factory=list)
    user_hints: list[str] = field(default_factory=list)
    # Plan 194 F1 (§5 guardrail): critical facts (active incident, active repo, key decisions) that
    # must survive VERBATIM through any compaction — never folded into a lossy summary.
    pinned_facts: list[str] = field(default_factory=list)
    budgets: AgentBudgets | None = None
    turn_count: int = 0
    stopped: bool = False
    stop_reason: str = ""
    final_result: Any = None

    def record_observation(self, obs: Observation) -> None:
        self.observations.append(obs)

    def record_proposal(self, prop: ProposalAttempt) -> None:
        self.proposals.append(prop)

    def add_hint(self, hint: str) -> None:
        if hint and hint not in self.user_hints:
            self.user_hints.append(hint)

    def attempted_commands(self) -> tuple[str, ...]:
        """Every command that has been proposed (succeeded or not)."""
        return tuple(p.command for p in self.proposals)

    def failed_commands(self) -> tuple[str, ...]:
        return tuple(
            p.command for p in self.proposals
            if p.succeeded is False
        )

    def stop(self, reason: str, *, final_result: Any = None) -> None:
        self.stopped = True
        self.stop_reason = reason
        self.final_result = final_result


@dataclass(frozen=True)
class AgentResult:
    """Final outcome of an agent run."""

    agent_name: str
    succeeded: bool
    stop_reason: str
    turn_count: int
    elapsed_seconds: float
    observations: tuple[Observation, ...]
    proposals: tuple[ProposalAttempt, ...]
    final_payload: Any = None
