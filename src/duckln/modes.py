"""Mode policy definitions for HITL, HOTL, and HOOTLWO."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from duckln.safety import SafetyAssessment, SafetyClass


class ControlMode(str, Enum):
    """Supported Duckln control modes."""

    HITL = "hitl"
    HOTL = "hotl"
    HOOTLWO = "hootlwo"

    @property
    def label(self) -> str:
        labels = {
            ControlMode.HITL: "HITL",
            ControlMode.HOTL: "HOTL",
            ControlMode.HOOTLWO: "HOOTLWO",
        }
        return labels[self]


@dataclass(frozen=True)
class ModeDescriptor:
    """Presentation details for a user-selectable control mode."""

    mode: ControlMode
    title: str
    full_name: str
    summary: str

    @property
    def choice_label(self) -> str:
        return f"{self.title} — {self.full_name}: {self.summary}"


def get_mode_descriptors() -> tuple[ModeDescriptor, ...]:
    """Return the first-release control modes in selection order."""

    return (
        ModeDescriptor(
            mode=ControlMode.HITL,
            title="HITL",
            full_name="Human-in-the-Loop",
            summary="AI explains and suggests only; you run commands yourself.",
        ),
        ModeDescriptor(
            mode=ControlMode.HOTL,
            title="HOTL",
            full_name="Human-on-the-Loop",
            summary="AI suggests exact commands; you approve before execution.",
        ),
        ModeDescriptor(
            mode=ControlMode.HOOTLWO,
            title="HOOTLWO",
            full_name="Human-out-of-the-Loop with Oversight",
            summary="AI can auto-run safe whitelisted fixes; use a sandbox or VM.",
        ),
    )


def parse_mode(value: str) -> ControlMode:
    """Parse a persisted or selected control mode."""

    return ControlMode(value.strip().lower())


@dataclass(frozen=True)
class ModeDecision:
    """Result of applying a mode policy to a command."""

    allowed: bool
    requires_approval: bool
    auto_run: bool
    reason: str


def evaluate_mode_action(
    mode: ControlMode,
    assessment: SafetyAssessment,
    *,
    is_ai_suggested: bool,
    user_approved: bool = False,
) -> ModeDecision:
    """Determine whether a command may execute under the active control mode."""

    if assessment.blocked:
        return ModeDecision(
            allowed=False,
            requires_approval=False,
            auto_run=False,
            reason=assessment.reason,
        )

    if not is_ai_suggested:
        return ModeDecision(
            allowed=True,
            requires_approval=False,
            auto_run=False,
            reason="User-entered commands may run through the controlled runner.",
        )

    if mode is ControlMode.HITL:
        return ModeDecision(
            allowed=False,
            requires_approval=False,
            auto_run=False,
            reason="HITL never executes AI-suggested commands.",
        )

    if mode is ControlMode.HOTL:
        if not user_approved:
            return ModeDecision(
                allowed=False,
                requires_approval=True,
                auto_run=False,
                reason="HOTL requires user approval before executing AI-suggested commands.",
            )
        return ModeDecision(
            allowed=True,
            requires_approval=False,
            auto_run=False,
            reason="User approved the AI-suggested command in HOTL mode.",
        )

    if assessment.whitelisted and assessment.safety_class in {SafetyClass.S0, SafetyClass.S1}:
        return ModeDecision(
            allowed=True,
            requires_approval=False,
            auto_run=True,
            reason="HOOTLWO may auto-run whitelisted safe diagnostics and install commands.",
        )

    if not user_approved:
        return ModeDecision(
            allowed=False,
            requires_approval=True,
            auto_run=False,
            reason="HOOTLWO only auto-runs whitelisted S0-S1 commands; this command needs approval.",
        )

    return ModeDecision(
        allowed=True,
        requires_approval=False,
        auto_run=False,
        reason="User approved a non-whitelisted AI-suggested command in HOOTLWO mode.",
    )
