"""Plan 72 Phase 2 — central Plan Mode lifecycle state machine.

DORMANT-BY-DESIGN (Plan 175 C1): this state machine + its transition rules are kept as the
CANONICAL definition of the Plan-Mode invariants (and are asserted by the plan-mode invariant
tests), but the LIVE bring-up/conversation paths do NOT route through it — they use the ad-hoc
`workflow_state` objective keys (Plan 168/169) instead. It is intentionally retained (not dead
code to delete) as the reference contract; wiring the live flow through it is a future refactor.

Every non-trivial (mutating) Duckln action flows through one lifecycle so the
PRD's invariants hold: a task always reaches exactly one terminal state, and no
UI state may remain spinning afterward.

Happy path:
    intent_detected → context_collected → plan_drafted → supervisor_review
    → user_review → approved → executing → verifying → reflecting → complete

Blocker path (during execution):
    executing → blocker_captured → reflect → amend_plan → supervisor_review
    → user_review

Defect path (Duckln's own bug):
    internal_exception → redacted_diagnostic → duckln_internal_bug_reported
    → stop_cleanly

Terminal states: complete | waiting_on_user | blocked_external |
duckln_internal_bug_reported.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Callable


class PlanLifecycleState(str, Enum):
    INTENT_DETECTED = "intent_detected"
    CONTEXT_COLLECTED = "context_collected"
    PLAN_DRAFTED = "plan_drafted"
    SUPERVISOR_REVIEW = "supervisor_review"
    USER_REVIEW = "user_review"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REFLECTING = "reflecting"
    BLOCKER_CAPTURED = "blocker_captured"
    AMEND_PLAN = "amend_plan"
    INTERNAL_EXCEPTION = "internal_exception"
    REDACTED_DIAGNOSTIC = "redacted_diagnostic"
    # Terminal states.
    COMPLETE = "complete"
    WAITING_ON_USER = "waiting_on_user"
    BLOCKED_EXTERNAL = "blocked_external"
    DUCKLN_INTERNAL_BUG_REPORTED = "duckln_internal_bug_reported"


TERMINAL_STATES: frozenset[PlanLifecycleState] = frozenset(
    {
        PlanLifecycleState.COMPLETE,
        PlanLifecycleState.WAITING_ON_USER,
        PlanLifecycleState.BLOCKED_EXTERNAL,
        PlanLifecycleState.DUCKLN_INTERNAL_BUG_REPORTED,
    }
)

# Canonical objective-status strings mirrored to workflow state.
TERMINAL_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in TERMINAL_STATES)


_ALLOWED: dict[PlanLifecycleState, frozenset[PlanLifecycleState]] = {
    PlanLifecycleState.INTENT_DETECTED: frozenset(
        {PlanLifecycleState.CONTEXT_COLLECTED, PlanLifecycleState.INTERNAL_EXCEPTION,
         PlanLifecycleState.BLOCKED_EXTERNAL, PlanLifecycleState.WAITING_ON_USER}
    ),
    PlanLifecycleState.CONTEXT_COLLECTED: frozenset(
        {PlanLifecycleState.PLAN_DRAFTED, PlanLifecycleState.INTERNAL_EXCEPTION,
         PlanLifecycleState.BLOCKED_EXTERNAL, PlanLifecycleState.WAITING_ON_USER}
    ),
    PlanLifecycleState.PLAN_DRAFTED: frozenset(
        {PlanLifecycleState.SUPERVISOR_REVIEW, PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.SUPERVISOR_REVIEW: frozenset(
        {PlanLifecycleState.USER_REVIEW, PlanLifecycleState.PLAN_DRAFTED,  # revise → redraft
         PlanLifecycleState.WAITING_ON_USER, PlanLifecycleState.BLOCKED_EXTERNAL,
         PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.USER_REVIEW: frozenset(
        {PlanLifecycleState.APPROVED, PlanLifecycleState.WAITING_ON_USER,
         PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.APPROVED: frozenset(
        {PlanLifecycleState.EXECUTING, PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.EXECUTING: frozenset(
        {PlanLifecycleState.VERIFYING, PlanLifecycleState.BLOCKER_CAPTURED,
         PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.VERIFYING: frozenset(
        {PlanLifecycleState.REFLECTING, PlanLifecycleState.BLOCKER_CAPTURED,
         PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.REFLECTING: frozenset(
        {PlanLifecycleState.COMPLETE, PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.BLOCKER_CAPTURED: frozenset(
        {PlanLifecycleState.REFLECTING, PlanLifecycleState.AMEND_PLAN,
         PlanLifecycleState.BLOCKED_EXTERNAL, PlanLifecycleState.WAITING_ON_USER,
         PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.AMEND_PLAN: frozenset(
        {PlanLifecycleState.SUPERVISOR_REVIEW, PlanLifecycleState.INTERNAL_EXCEPTION}
    ),
    PlanLifecycleState.INTERNAL_EXCEPTION: frozenset(
        {PlanLifecycleState.REDACTED_DIAGNOSTIC}
    ),
    PlanLifecycleState.REDACTED_DIAGNOSTIC: frozenset(
        {PlanLifecycleState.DUCKLN_INTERNAL_BUG_REPORTED}
    ),
}


class LifecycleTransitionError(RuntimeError):
    """Raised on an illegal lifecycle transition."""


class PlanLifecycle:
    """Tracks one task's lifecycle, persists status, guarantees a terminal end.

    Use `with PlanLifecycle(...) as lc:` — on exit it forces a terminal state
    (defaulting to `duckln_internal_bug_reported` if an exception escaped, else
    `waiting_on_user` if nothing terminal was set) and always clears UI activity.
    """

    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        objective_id: str | None = None,
        on_clear_activity: Callable[[], None] | None = None,
        persist: Callable[[str], None] | None = None,
    ) -> None:
        self.state = PlanLifecycleState.INTENT_DETECTED
        self.history: list[PlanLifecycleState] = [self.state]
        self._config_dir = Path(config_dir) if config_dir is not None else None
        self._objective_id = objective_id
        self._on_clear_activity = on_clear_activity
        self._persist = persist
        self._persist_state(self.state)

    # --- transitions -------------------------------------------------------

    def advance(self, to: PlanLifecycleState) -> PlanLifecycleState:
        if self.is_terminal:
            raise LifecycleTransitionError(
                f"cannot advance from terminal state {self.state.value}"
            )
        allowed = _ALLOWED.get(self.state, frozenset())
        if to not in allowed and to not in TERMINAL_STATES:
            raise LifecycleTransitionError(
                f"illegal transition {self.state.value} → {to.value}"
            )
        self.state = to
        self.history.append(to)
        self._persist_state(to)
        return to

    def terminal(self, outcome: PlanLifecycleState) -> PlanLifecycleState:
        if outcome not in TERMINAL_STATES:
            raise LifecycleTransitionError(f"{outcome.value} is not a terminal state")
        self.state = outcome
        self.history.append(outcome)
        self._persist_state(outcome)
        if self._on_clear_activity is not None:
            try:
                self._on_clear_activity()
            except Exception:
                pass
        return outcome

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    # --- context manager ---------------------------------------------------

    def __enter__(self) -> "PlanLifecycle":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and not self.is_terminal:
            # Duckln's own defect path.
            try:
                if self.state is not PlanLifecycleState.INTERNAL_EXCEPTION:
                    self.state = PlanLifecycleState.INTERNAL_EXCEPTION
                    self.history.append(self.state)
                self.state = PlanLifecycleState.REDACTED_DIAGNOSTIC
                self.history.append(self.state)
            except Exception:
                pass
            self.terminal(PlanLifecycleState.DUCKLN_INTERNAL_BUG_REPORTED)
            return False  # never swallow the exception
        if not self.is_terminal:
            # Nothing terminal was set and no exception — default to
            # waiting_on_user so the UI never stays mid-flight.
            self.terminal(PlanLifecycleState.WAITING_ON_USER)
        else:
            # Already terminal — still guarantee the activity bar is cleared.
            if self._on_clear_activity is not None:
                try:
                    self._on_clear_activity()
                except Exception:
                    pass
        return False

    # --- persistence -------------------------------------------------------

    def _persist_state(self, state: PlanLifecycleState) -> None:
        if self._persist is not None:
            try:
                self._persist(state.value)
            except Exception:
                pass
            return
        if self._config_dir is None:
            return
        try:
            from state.access import write_workflow_state

            write_workflow_state(self._config_dir, {"active_objective_status": state.value})
        except Exception:
            pass
