"""Route-scoped prompt context assembly for conversation turns."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from state.access import read_supervisor_workspace_sections


@dataclass(frozen=True)
class SupervisorPromptContext:
    """Minimal prompt context bundle for one supervisor turn."""

    workspace_sections: dict[str, str]
    context_blocks: tuple[str, ...]
    recent_turn_limit: int

    def render(self) -> str:
        blocks: list[str] = []
        if self.workspace_sections:
            blocks.extend(
                f"{name}\n{content}"
                for name, content in self.workspace_sections.items()
                if content
            )
        blocks.extend(block for block in self.context_blocks if block.strip())
        return "\n".join(blocks).strip()


_SOCIAL_FAMILIES = {
    None,
    "small_talk",
    "rapport",
    "help_request",
    "capability",
    "user_identity_meta",
    "user_alias",
}

_REPAIR_FAMILIES = {
    "conversation_repair",
    "conversation_restate",
    "clarify",
    "utility_fallback",
}

_RECOMMENDATION_FAMILIES = {
    "repo_capability_coverage",
    "repo_recommendation_single",
    "repo_recommendation_compare",
    "repo_recommendation_rationale",
    "repo_alternatives",
    "recommendation_followup",
}

_REPO_STATE_FAMILIES = {
    "repo_status",
    "repo_verify",
    "repo_active",
    "repo_active_explanation",
    "repo_memory_meta",
    "repo_access",
    "repo_remove",
    "repo_run",
    "repo_stop",
    "repo_logs",
    "repo_location",
    "repo_requirements",
    "repo_overview",
}


def assemble_supervisor_prompt_context(
    *,
    config_dir: Path | None,
    route_family: str | None,
    context,
) -> SupervisorPromptContext:
    """Build a minimal prompt bundle for the current conversation turn."""

    workspace_sections = (
        read_supervisor_workspace_sections(config_dir, route_family=route_family)
        if config_dir is not None
        else {}
    )
    blocks: list[str] = []
    recent_turn_limit = 2

    if route_family in _SOCIAL_FAMILIES:
        blocks.append("Current turn type: social or capability. Ignore stale repo or recommendation threads unless the user asks about them directly.")
        # Plan 185: ground a CAPABILITY question (can I run this / how much RAM / which target /
        # does this need a GPU) in real host + active-repo facts, so the model explains the 'why'
        # precisely — in its own words, never canned. Other social turns aren't bloated with it.
        if route_family == "capability" and config_dir is not None:
            from duckln.capability_facts import capability_facts_block_for

            _cap_block = capability_facts_block_for(config_dir)
            if _cap_block:
                blocks.append(_cap_block)
        return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=2)

    if route_family in _REPAIR_FAMILIES:
        last_route_family = str(context.followup_state.last_route_family or "").strip() or "unknown"
        repo_name = None
        if context.durable_repo_memory is not None:
            repo_name = context.durable_repo_memory.repo_name
        blocks.append(f"Current turn type: repair or clarification. Last answered route family: {last_route_family}.")
        if repo_name:
            blocks.append(f"Only reuse the active repo if the explanation clearly depends on {repo_name}.")
        blocks.append("Do not pull in recommendation shortlist details unless the user is explicitly asking about that shortlist.")
        return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=2)

    if route_family in _REPO_STATE_FAMILIES:
        durable = context.durable_repo_memory
        repo_name = durable.repo_name if durable is not None else None
        if repo_name:
            blocks.append(f"Tracked repo subject: {repo_name}.")
        if durable is not None and durable.tracked_status:
            blocks.append(f"Tracked repo status: {durable.tracked_status}.")
        if durable is not None and durable.install_location:
            blocks.append(f"Tracked install location: {durable.install_location}.")
        if durable is not None and durable.has_repo_knowledge:
            blocks.append("Repo knowledge is available for this turn.")
        blocks.append("Prefer tracked repo state and repo knowledge over recommendation memory.")
        return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=3)

    if route_family == "system_verify":
        blocks.append("Current turn type: bounded system verification.")
        blocks.append("Prefer the live healthcheck path over stale recommendation or repo-thread context.")
        return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=2)

    if route_family in _RECOMMENDATION_FAMILIES:
        recommendation = context.durable_recommendation
        if recommendation.primary_repo:
            blocks.append(f"Current recommendation pick: {recommendation.primary_repo}.")
        if recommendation.secondary_repo:
            blocks.append(f"Current runner-up: {recommendation.secondary_repo}.")
        if recommendation.alternatives:
            blocks.append("Known shortlist alternatives: " + ", ".join(recommendation.alternatives[:3]) + ".")
        if context.pending_offer.kind:
            blocks.append(f"Live pending offer: {context.pending_offer.kind}.")
        blocks.append("Keep recommendation context bounded to the current shortlist and machine fit only.")
        # Plan 185: ground the recommendation + the 'why / why not local / why a VM' in real host +
        # repo facts so the model defends its pick precisely, in its own words (no canned rationale).
        if config_dir is not None:
            from duckln.capability_facts import capability_facts_block_for

            _cap_block = capability_facts_block_for(config_dir)
            if _cap_block:
                blocks.append(_cap_block)
        return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=3)

    if route_family == "workflow_action" or route_family == "slash_runtime_followup" or route_family == "next_step_guidance":
        blocks.append(f"Execution target: {context.execution_target}.")
        if context.active_vm_name:
            blocks.append(f"Active VM: {context.active_vm_name}.")
        if context.pending_offer.kind:
            blocks.append(f"Pending offer: {context.pending_offer.kind}.")
        blocks.append("Keep the action scoped to the current execution target and bounded next step.")
        return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=3)

    blocks.append("Keep only the current turn and the smallest relevant bounded context active.")
    return SupervisorPromptContext(workspace_sections=workspace_sections, context_blocks=tuple(blocks), recent_turn_limit=2)
