"""Generic observe-decide-act loop driving step-by-step recovery flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a single step or remediation invocation."""

    ok: bool
    outcome: str = ""
    raw_stdout: str = ""
    raw_stderr: str = ""


@dataclass(frozen=True)
class IssueClassification:
    """A recognised failure surface from a step's ActionResult."""

    kind: str
    message: str
    source_url: str | None = None


@dataclass(frozen=True)
class Remediation:
    """A user-approvable fix for a classified issue."""

    kind: str
    label: str
    run: Callable[[], ActionResult]
    source_url: str | None = None


@dataclass(frozen=True)
class LoopStep:
    """One action in the loop, with a per-step classifier and remediation resolver."""

    name: str
    run: Callable[[], ActionResult]
    classify: Callable[[ActionResult], IssueClassification | None]
    resolve_remediation: Callable[[IssueClassification], Remediation | None]


class LoopChoice(str, Enum):
    """User decision in response to a classified failure."""

    FIX_NOW = "fix_now"
    FIX_LATER = "fix_later"
    IGNORE = "ignore"


@dataclass(frozen=True)
class LoopOutcome:
    """Terminal result of run_agent_loop."""

    completed: bool
    last_step: str | None
    stop_reason: str
    deferred: tuple[IssueClassification, ...] = field(default_factory=tuple)
    ignored: tuple[IssueClassification, ...] = field(default_factory=tuple)


def _noop_display(_message: str) -> None:
    return None


def _noop_defer(_issue: IssueClassification) -> None:
    return None


def run_agent_loop(
    *,
    steps: tuple[LoopStep, ...],
    surface_choice: Callable[[IssueClassification, Remediation | None], LoopChoice],
    on_defer: Callable[[IssueClassification], None] = _noop_defer,
    display_output: Callable[[str], None] = _noop_display,
    max_attempts_per_step: int = 3,
) -> LoopOutcome:
    """Drive an ordered list of steps with classifier-guided remediation."""

    if max_attempts_per_step < 1:
        raise ValueError("max_attempts_per_step must be at least 1")

    deferred: list[IssueClassification] = []
    ignored: list[IssueClassification] = []
    last_step: str | None = None

    for step in steps:
        last_step = step.name
        attempts = 0
        stop_for_step = False
        while attempts < max_attempts_per_step:
            attempts += 1
            result = step.run()
            if result.ok:
                if result.outcome:
                    display_output(result.outcome)
                break

            issue = step.classify(result)
            if issue is None:
                if result.outcome:
                    display_output(result.outcome)
                elif result.raw_stderr:
                    display_output(result.raw_stderr.strip().splitlines()[0])
                return LoopOutcome(
                    completed=False,
                    last_step=last_step,
                    stop_reason="unclassified_failure",
                    deferred=tuple(deferred),
                    ignored=tuple(ignored),
                )

            remediation = step.resolve_remediation(issue)
            display_output(issue.message)
            choice = surface_choice(issue, remediation)

            if choice is LoopChoice.FIX_NOW and remediation is not None:
                fix_result = remediation.run()
                if fix_result.outcome:
                    display_output(fix_result.outcome)
                if not fix_result.ok:
                    return LoopOutcome(
                        completed=False,
                        last_step=last_step,
                        stop_reason="remediation_failed",
                        deferred=tuple(deferred),
                        ignored=tuple(ignored),
                    )
                continue

            if choice is LoopChoice.FIX_NOW and remediation is None:
                # No auto-fix available; treat as Fix later.
                choice = LoopChoice.FIX_LATER

            if choice is LoopChoice.FIX_LATER:
                deferred.append(issue)
                on_defer(issue)
                return LoopOutcome(
                    completed=False,
                    last_step=last_step,
                    stop_reason="deferred_by_user",
                    deferred=tuple(deferred),
                    ignored=tuple(ignored),
                )

            if choice is LoopChoice.IGNORE:
                ignored.append(issue)
                stop_for_step = True
                break

        else:
            return LoopOutcome(
                completed=False,
                last_step=last_step,
                stop_reason="max_attempts_exhausted",
                deferred=tuple(deferred),
                ignored=tuple(ignored),
            )

        if stop_for_step:
            continue

    return LoopOutcome(
        completed=True,
        last_step=last_step,
        stop_reason="all_steps_completed",
        deferred=tuple(deferred),
        ignored=tuple(ignored),
    )
