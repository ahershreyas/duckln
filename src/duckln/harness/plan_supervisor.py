"""Plan 67 Phase M — Plan-Mode supervisor wiring.

Wires the `supervisor.md` agent spec into Python by orchestrating a
sequential three-stage pipeline (repo_inspector → planner → critic) and
emitting trace events so `/agents trace <session>` shows the multi-agent
flow.

The plan content itself is produced by `duckln.plan_mode.generate_plan()` —
that module owns the deterministic stages and the LLM call wrapping. This
file's job is to:

1. Verify that the supervisor + three specialist agent specs are registered.
2. Drive the pipeline sequentially, logging each stage as a separate
   trace agent so observability works the same way as `run_multi_agent_recovery`.
3. Fall back to a local-only pipeline (no harness trace) when the specs are
   missing or fail to load.

The fallback exists so a corrupted spec file never breaks Plan Mode in
production — the user still gets a plan, just without the agent trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from duckln.harness.agent_def import AgentRegistry, builtin_agents_directory
from duckln.harness.trace import TraceEvent, TraceLogger
from duckln.modes import ControlMode
from duckln.plan_mode import (
    LLMClient,
    PlanRecord,
    RepoUnderstanding,
    PLAN_STATUS_FAILED,
    _assemble_ordered_steps,
    _classify_and_verify,
    _critique_and_order,
    _collect_clarifications,
    _derive_risks,
    _derive_rollback,
    _iso_now,
    _propose_candidates,
    _summarize_understanding,
    gather_repo_understanding,
    generate_plan,
)


REQUIRED_AGENT_NAMES: tuple[str, ...] = (
    "supervisor",
    "repo_inspector",
    "planner",
    "critic",
)


@dataclass(frozen=True)
class PlanSupervisorResult:
    """Output of run_plan_supervisor — the plan plus observability metadata."""

    plan: PlanRecord
    session_id: str
    elapsed_seconds: float
    used_harness: bool
    missing_agents: tuple[str, ...]


def run_plan_supervisor(
    *,
    objective: str,
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
    config_dir: Path,
    mode: ControlMode | str,
    display: Callable[[str], None] | None = None,
    agent_registry: AgentRegistry | None = None,
    plan_id: str | None = None,
) -> PlanSupervisorResult:
    """Run the Plan-Mode supervisor pipeline.

    The supervisor coordinates three sub-agents (repo_inspector, planner,
    critic) in order. Trace events are emitted so `/agents trace
    <session_id>` renders the pipeline. The final PlanRecord comes from
    `plan_mode.generate_plan`.

    On any failure (LLM error, missing agent specs, malformed JSON) the
    fallback path runs the local pipeline without trace; the caller still
    gets a PlanRecord (potentially with status="failed").
    """

    started = time.monotonic()
    registry = agent_registry or AgentRegistry.from_directory(builtin_agents_directory())
    missing = tuple(
        name for name in REQUIRED_AGENT_NAMES if registry.lookup(name) is None
    )

    if missing:
        if display is not None:
            display(
                "Plan supervisor: missing agent spec(s) "
                f"{', '.join(missing)} — using local plan pipeline."
            )
        plan = _local_plan_pipeline(
            objective=objective,
            understanding=understanding,
            llm_client=llm_client,
            mode=mode,
            plan_id=plan_id,
        )
        return PlanSupervisorResult(
            plan=plan,
            session_id="",
            elapsed_seconds=time.monotonic() - started,
            used_harness=False,
            missing_agents=missing,
        )

    trace = TraceLogger(config_dir=Path(config_dir))
    _emit_phase(trace, agent="supervisor", turn=1, phase="start", note=f"objective={objective[:80]}")

    try:
        _emit_phase(trace, agent="repo_inspector", turn=1, phase="start", note=_summarize_understanding(understanding))
        # The inspector's "output" is already in `understanding`; we record
        # that fact rather than re-running it.
        _emit_phase(
            trace,
            agent="repo_inspector",
            turn=2,
            phase="end",
            note=(
                f"family={understanding.repo_family}, "
                f"files={len(understanding.detected_files)}, "
                f"failures={len(understanding.recent_failures)}"
            ),
        )

        _emit_phase(trace, agent="planner", turn=1, phase="start", note="proposing candidates")
        candidates = _propose_candidates(understanding=understanding, llm_client=llm_client)
        _emit_phase(
            trace,
            agent="planner",
            turn=2,
            phase="end",
            note=f"{len(candidates)} candidate(s)",
        )

        _emit_phase(trace, agent="critic", turn=1, phase="start", note="critique + order")
        # Plan 70: critique is best-effort — a failure falls back to a
        # deterministic ordering of the proposed candidates instead of
        # discarding them (and re-running propose via the local fallback).
        ordered, dropped, reasoning = _assemble_ordered_steps(
            candidates=candidates,
            understanding=understanding,
            llm_client=llm_client,
        )
        classified, blocked = _classify_and_verify(ordered)
        _emit_phase(
            trace,
            agent="critic",
            turn=2,
            phase="end",
            note=f"{len(classified)} step(s), {len(dropped)} dropped, {len(blocked)} blocked",
        )

        if not classified:
            _emit_phase(trace, agent="supervisor", turn=2, phase="fail", note="all steps blocked")
            plan = generate_plan(
                objective=objective,
                understanding=understanding,
                llm_client=llm_client,
                mode=mode,
                plan_id=plan_id,
            )
            return PlanSupervisorResult(
                plan=plan,
                session_id=trace.session_id,
                elapsed_seconds=time.monotonic() - started,
                used_harness=True,
                missing_agents=(),
            )

        clarifications: tuple = ()
        try:
            clarifications = _collect_clarifications(
                steps=classified, understanding=understanding, llm_client=llm_client
            )
        except Exception:
            clarifications = ()

        if clarifications:
            _emit_phase(
                trace,
                agent="supervisor",
                turn=2,
                phase="clarify",
                note=f"{len(clarifications)} question(s)",
            )

        estimated_seconds = sum(s.estimated_seconds for s in classified)
        risks = _derive_risks(classified, understanding, blocked)
        rollback = _derive_rollback(classified, understanding)
        created_at = _iso_now()

        plan = PlanRecord(
            plan_id=plan_id or _new_plan_id(),
            objective=objective,
            context_summary=_summarize_understanding(understanding),
            steps=classified,
            risks=risks,
            rollback=rollback,
            estimated_seconds=estimated_seconds,
            created_at=created_at,
            status="pending",
            repo_slug=understanding.repo_slug,
            mode_at_creation=(mode.value if isinstance(mode, ControlMode) else str(mode)),
            clarifications=clarifications,
            dropped_candidates=dropped,
            critic_reasoning=reasoning,
            amendment_count=0,
            history=(f"{created_at}: plan generated via supervisor (session {trace.session_id})",),
        )

        _emit_phase(
            trace,
            agent="supervisor",
            turn=3,
            phase="end",
            note=f"plan_id={plan.plan_id}, steps={len(plan.steps)}",
        )
        return PlanSupervisorResult(
            plan=plan,
            session_id=trace.session_id,
            elapsed_seconds=time.monotonic() - started,
            used_harness=True,
            missing_agents=(),
        )
    except Exception as exc:
        _emit_phase(trace, agent="supervisor", turn=99, phase="fail", note=str(exc)[:200])
        # Hard-fall back to the local pipeline so the user still gets something.
        plan = _local_plan_pipeline(
            objective=objective,
            understanding=understanding,
            llm_client=llm_client,
            mode=mode,
            plan_id=plan_id,
        )
        return PlanSupervisorResult(
            plan=plan,
            session_id=trace.session_id,
            elapsed_seconds=time.monotonic() - started,
            used_harness=True,
            missing_agents=(),
        )


def _local_plan_pipeline(
    *,
    objective: str,
    understanding: RepoUnderstanding,
    llm_client: LLMClient,
    mode: ControlMode | str,
    plan_id: str | None,
) -> PlanRecord:
    """Fallback: run the same pipeline in-process without trace emission."""
    return generate_plan(
        objective=objective,
        understanding=understanding,
        llm_client=llm_client,
        mode=mode,
        plan_id=plan_id,
    )


def _new_plan_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _emit_phase(trace: TraceLogger, *, agent: str, turn: int, phase: str, note: str) -> None:
    """Emit a single trace event labelled by stage phase."""
    trace.emit(
        TraceEvent(
            ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            session_id=trace.session_id,
            agent=agent,
            turn=turn,
            kind=phase,
            tool=None,
            args=None,
            result_summary=note,
        )
    )
