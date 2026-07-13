"""Pre-generation routing for Duckln free-text conversation turns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from duckln.conversation_routes.confidence import (
    build_confidence_snapshot,
    explicit_base_candidate,
    should_answer_without_clarify,
    should_clarify_route,
)
from duckln.conversation_routes.front_door import classify_front_door
from duckln.conversation_routes.intent_schema import parse_structured_intent
from duckln.conversation_routes.repo import (
    looks_like_repo_lifecycle_active_query,
    looks_like_repo_lifecycle_inventory_query,
    looks_like_repo_lifecycle_previous_work_query,
    looks_like_repo_lifecycle_specific_status_query,
    looks_like_repo_live_sessions_cloud_query,
    looks_like_repo_live_sessions_docker_query,
    looks_like_repo_live_sessions_local_query,
    looks_like_repo_live_sessions_query,
    looks_like_repo_live_sessions_vm_query,
    looks_like_repo_source_query,
    looks_like_repo_lifecycle_turn,
)
from duckln.conversation_routes.vm import (
    looks_like_docker_list_query,
    looks_like_repo_inventory_cloud_query,
    looks_like_repo_inventory_docker_query,
    looks_like_repo_inventory_local_query,
    looks_like_repo_inventory_vm_query,
    looks_like_repo_path_query,
    looks_like_vm_capability_query,
    looks_like_vm_delete_query,
    looks_like_vm_definition_query,
    looks_like_vm_list_query,
    looks_like_vm_open_query,
    looks_like_vm_repo_recommendation_query,
)
from duckln.conversation_policy import (
    RouteCandidate,
    RouteDecision,
    bootstrap_heuristics,
    response_contract_for_family,
)
from duckln.conversation_routes.normalizer import NormalizedConversationTurn

if TYPE_CHECKING:
    from pathlib import Path

    from duckln.conversation_agent import ConversationContext, ConversationTurn, FreeTextReply
    from duckln.conversation_policy import ClarificationOption


def route_family_for_intent(intent: str) -> str:
    mapping = {
        "greeting": "small_talk",
        "rapport": "small_talk",
        "smalltalk": "small_talk",
        "capability": "capability",
        "confidence": "capability",
        "repo_confidence": "capability",
        "user_identity_meta": "user_identity_meta",
        "user_alias_set": "user_alias",
        "conversation_repair": "conversation_repair",
        "conversation_restate": "conversation_restate",
        "repo_active_explanation": "repo_active_explanation",
        "next_step_guidance": "next_step_guidance",
        "system_summary": "system_summary",
        "system_capacity": "system_summary",
        "system_fit_context": "system_summary",
        "system_followup": "system_summary",
        "system_verify": "system_verify",
        "memory_meta": "memory_meta",
        "repo_memory_meta": "repo_memory_meta",
        "vm_definition": "vm_info",
        "vm_capability": "vm_info",
        "vm_list": "vm_info",
        "vm_open": "vm_info",
        "vm_delete": "vm_info",
        "docker_list": "vm_info",
        "vm_repo_recommendation": "vm_repo_recommendation",
        "repo_lifecycle_inventory": "repo_inventory",
        "repo_lifecycle_active": "repo_status",
        "repo_lifecycle_specific_status": "repo_status",
        "repo_lifecycle_previous_work": "repo_inventory",
        "repo_inventory_local": "repo_inventory",
        "repo_inventory_vm": "repo_inventory",
        "repo_inventory_cloud": "repo_inventory",
        "repo_inventory_docker": "repo_inventory",
        "repo_live_sessions": "repo_live_sessions",
        "repo_live_sessions_local": "repo_live_sessions",
        "repo_live_sessions_vm": "repo_live_sessions",
        "repo_live_sessions_cloud": "repo_live_sessions",
        "repo_live_sessions_docker": "repo_live_sessions",
        "repo_capability_coverage": "repo_capability_coverage",
        "repo_recommendation_single": "repo_recommendation_single",
        "repo_recommendation_compare": "repo_recommendation_compare",
        "repo_recommendation_rationale": "repo_recommendation_rationale",
        "repo_alternatives": "repo_alternatives",
        "repo_overview": "repo_overview",
        "repo_recommendation_followup": "repo_recommendation_rationale",
        "recommendation_followup": "recommendation_followup",
        "slash_runtime_followup": "slash_runtime_followup",
        "repo_requirements": "repo_requirements",
        "repo_fit_judgment": "repo_fit",
        "repo_inventory": "repo_inventory",
        "repo_status": "repo_status",
        "repo_active": "repo_status",
        "repo_verify": "repo_verify",
        "repo_location": "repo_access",
        "repo_path_show": "repo_path",
        "repo_path_inventory": "repo_path",
        "repo_source_show": "repo_path",
        "repo_source_inventory": "repo_path",
        "repo_access": "repo_access",
        "repo_run": "repo_run",
        "repo_restart": "repo_restart",
        "repo_stop": "repo_stop",
        "repo_logs": "repo_logs",
        "repo_removal": "repo_remove",
        "setup_request": "workflow_action",
        "help_request": "workflow_action",
        "command_hint": "workflow_action",
        "feedback_style": "capability",
        "generic": "utility_fallback",
    }
    return mapping.get(intent, "utility_fallback")


def intent_for_route_family(route_family: str, normalized_compact: str) -> str:
    mapping = {
        "small_talk": "rapport" if any(phrase in normalized_compact for phrase in ("how are you", "how r u", "howre you", "how u doing")) else "greeting",
        "capability": "capability",
        "user_identity_meta": "user_identity_meta",
        "user_alias": "user_alias_set",
        "conversation_repair": "conversation_repair",
        "conversation_restate": "conversation_restate",
        "repo_active_explanation": "repo_active_explanation",
        "next_step_guidance": "next_step_guidance",
        "system_summary": "system_capacity",
        "system_verify": "system_verify",
        "memory_meta": "memory_meta",
        "vm_info": "vm_definition",
        "vm_repo_recommendation": "vm_repo_recommendation",
        "repo_memory_meta": "repo_memory_meta",
        "repo_capability_coverage": "repo_capability_coverage",
        "repo_recommendation_single": "repo_recommendation_single",
        "repo_recommendation_compare": "repo_recommendation_compare",
        "repo_recommendation_rationale": "repo_recommendation_rationale",
        "repo_alternatives": "repo_alternatives",
        "repo_overview": "repo_overview",
        "recommendation_followup": "recommendation_followup",
        "repo_requirements": "repo_requirements",
        "repo_fit": "repo_fit_judgment",
        "repo_inventory": "repo_inventory",
        "repo_live_sessions": "repo_live_sessions",
        "repo_path": "repo_path_show",
        "repo_status": "repo_status",
        "repo_verify": "repo_verify",
        "repo_access": "repo_access",
        "repo_run": "repo_run",
        "repo_restart": "repo_restart",
        "repo_stop": "repo_stop",
        "repo_logs": "repo_logs",
        "repo_remove": "repo_removal",
        "workflow_action": "setup_request",
        "slash_runtime_followup": "slash_runtime_followup",
        "clarify": "clarify",
        "utility_fallback": "generic",
    }
    return mapping[route_family]


def base_intent_score(intent: str) -> float:
    return {
        "greeting": 0.95,
        "rapport": 0.95,
        "smalltalk": 0.95,
        "capability": 0.9,
        "confidence": 0.86,
        "repo_confidence": 0.86,
        "user_identity_meta": 0.9,
        "user_alias_set": 0.93,
        "conversation_repair": 0.92,
        "conversation_restate": 0.9,
        "repo_active_explanation": 0.9,
        "next_step_guidance": 0.88,
        "system_followup": 0.84,
        "system_capacity": 0.92,
        "system_verify": 0.95,
        "memory_meta": 0.92,
        "vm_definition": 0.94,
        "vm_capability": 0.93,
        "vm_list": 0.95,
        "docker_list": 0.95,
        "vm_repo_recommendation": 0.91,
        "repo_memory_meta": 0.9,
        "repo_lifecycle_inventory": 0.94,
        "repo_lifecycle_active": 0.95,
        "repo_lifecycle_specific_status": 0.95,
        "repo_lifecycle_previous_work": 0.92,
        "repo_capability_coverage": 0.88,
        "repo_inventory_local": 0.93,
        "repo_inventory_vm": 0.93,
        "repo_inventory_cloud": 0.93,
        "repo_inventory_docker": 0.93,
        "repo_live_sessions": 0.95,
        "repo_live_sessions_local": 0.95,
        "repo_live_sessions_vm": 0.95,
        "repo_live_sessions_cloud": 0.95,
        "repo_live_sessions_docker": 0.95,
        "repo_recommendation_single": 0.91,
        "repo_recommendation_compare": 0.86,
        "repo_recommendation_rationale": 0.88,
        "repo_alternatives": 0.88,
        "repo_overview": 0.88,
        "recommendation_followup": 0.86,
        "repo_inventory": 0.89,
        "repo_status": 0.88,
        "repo_verify": 0.95,
        "slash_runtime_followup": 0.86,
        "repo_active": 0.87,
        "repo_run": 0.86,
        "repo_restart": 0.9,
        "repo_stop": 0.9,
        "repo_logs": 0.88,
        "repo_location": 0.85,
        "repo_path_show": 0.91,
        "repo_path_inventory": 0.92,
        "repo_source_show": 0.91,
        "repo_source_inventory": 0.92,
        "repo_access": 0.85,
        "repo_removal": 0.84,
        "repo_requirements": 0.89,
        "repo_fit_judgment": 0.87,
        "feedback_style": 0.84,
        "setup_request": 0.82,
        "help_request": 0.78,
        "generic": 0.2,
    }.get(intent, 0.4)


def aggregate_route_candidates(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    grouped: dict[tuple[str, str], RouteCandidate] = {}
    for candidate in candidates:
        key = (candidate.route_family, candidate.intent)
        previous = grouped.get(key)
        if previous is None or candidate.score > previous.score:
            grouped[key] = candidate
    return list(grouped.values())


def route_conversation(
    turn: NormalizedConversationTurn,
    *,
    context: "ConversationContext",
    recent_turns: tuple["ConversationTurn", ...],
    recent_replies: tuple["FreeTextReply", ...],
    config_dir: "Path | None",
    resolve_followup_intent: Callable[[str, object], str | None],
    resolve_corrected_intent: Callable[[str, str, tuple["ConversationTurn", ...], tuple["FreeTextReply", ...], "Path | None"], str],
    clarification_options_for_message: Callable[[str, "ConversationContext"], tuple["ClarificationOption", ...]],
    clarification_options_for_candidates: Callable[[tuple[RouteCandidate, ...] | list[RouteCandidate], "ConversationContext", str, tuple["ClarificationOption", ...]], tuple["ClarificationOption", ...]],
    phrase_matches: Callable[[str, str], bool],
    looks_like_command_surface_request: Callable[[str], bool],
    looks_like_repo_pronoun_followup: Callable[[str], bool],
    looks_like_social_or_identity_turn: Callable[[str], bool],
    looks_like_next_step_question: Callable[[str], bool],
) -> RouteDecision:
    normalized_compact = turn.normalized_compact
    base_intent = turn.base_intent
    structured = parse_structured_intent(normalized_compact)
    candidates: list[RouteCandidate] = []
    base_route_family = route_family_for_intent(base_intent)
    front_door = classify_front_door(
        normalized_compact=normalized_compact,
        base_intent=base_intent,
        structured=structured,
        context=context,
        looks_like_social_or_identity_turn=looks_like_social_or_identity_turn,
        looks_like_next_step_question=looks_like_next_step_question,
    )
    subject_name = context.mentioned_repo.name if context.mentioned_repo is not None else None
    current_turn_priority_families = {
        "small_talk",
        "capability",
        "vm_info",
        "vm_repo_recommendation",
        "user_identity_meta",
        "user_alias",
        "conversation_repair",
        "conversation_restate",
        "repo_active_explanation",
        "repo_path",
        "repo_status",
        "repo_verify",
        "repo_inventory",
        "repo_live_sessions",
        "repo_run",
        "repo_restart",
        "repo_stop",
        "repo_logs",
        "repo_remove",
        "system_summary",
        "system_verify",
        "memory_meta",
        "next_step_guidance",
    }
    active_workflow_issue = getattr(getattr(context, "workflow_state", None), "active_issue_kind", None)
    active_workflow_repo = getattr(getattr(context, "workflow_state", None), "repo_name", None)
    workflow_next_step_phrases = (
        "what do you recommend",
        "what should i do",
        "what should we do",
        "what now",
        "what next",
        "what do you suggest",
        "how should i proceed",
    )

    if active_workflow_issue and active_workflow_repo and any(phrase in normalized_compact for phrase in workflow_next_step_phrases):
        base_intent = "next_step_guidance"
        base_route_family = "next_step_guidance"

    if looks_like_repo_live_sessions_local_query(normalized_compact):
        base_intent = "repo_live_sessions_local"
        base_route_family = "repo_live_sessions"
    elif looks_like_repo_live_sessions_vm_query(normalized_compact):
        base_intent = "repo_live_sessions_vm"
        base_route_family = "repo_live_sessions"
    elif looks_like_repo_live_sessions_cloud_query(normalized_compact):
        base_intent = "repo_live_sessions_cloud"
        base_route_family = "repo_live_sessions"
    elif looks_like_repo_live_sessions_docker_query(normalized_compact):
        base_intent = "repo_live_sessions_docker"
        base_route_family = "repo_live_sessions"
    elif looks_like_repo_live_sessions_query(normalized_compact):
        base_intent = "repo_live_sessions"
        base_route_family = "repo_live_sessions"

    if base_route_family == "utility_fallback" and looks_like_repo_lifecycle_inventory_query(normalized_compact):
        base_intent = "repo_lifecycle_inventory"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and looks_like_repo_lifecycle_active_query(normalized_compact):
        base_intent = "repo_lifecycle_active"
        base_route_family = "repo_status"
    if base_route_family == "utility_fallback" and looks_like_repo_lifecycle_specific_status_query(normalized_compact):
        base_intent = "repo_lifecycle_specific_status"
        base_route_family = "repo_status"
    if base_route_family == "utility_fallback" and looks_like_repo_lifecycle_previous_work_query(normalized_compact):
        base_intent = "repo_lifecycle_previous_work"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and looks_like_repo_inventory_vm_query(normalized_compact):
        base_intent = "repo_inventory_vm"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and looks_like_repo_inventory_cloud_query(normalized_compact):
        base_intent = "repo_inventory_cloud"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and looks_like_repo_inventory_docker_query(normalized_compact):
        base_intent = "repo_inventory_docker"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and looks_like_repo_inventory_local_query(normalized_compact):
        base_intent = "repo_inventory_local"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and looks_like_repo_path_query(normalized_compact):
        base_intent = "repo_path_show"
        base_route_family = "repo_path"
    if base_route_family == "utility_fallback" and looks_like_repo_source_query(normalized_compact):
        base_intent = "repo_source_show"
        base_route_family = "repo_path"
    if base_route_family == "utility_fallback" and looks_like_vm_definition_query(normalized_compact):
        base_intent = "vm_definition"
        base_route_family = "vm_info"
    if base_route_family == "utility_fallback" and looks_like_vm_capability_query(normalized_compact):
        base_intent = "vm_capability"
        base_route_family = "vm_info"
    if base_route_family == "utility_fallback" and looks_like_vm_list_query(normalized_compact):
        base_intent = "vm_list"
        base_route_family = "vm_info"
    if base_route_family == "utility_fallback" and looks_like_vm_open_query(normalized_compact):
        base_intent = "vm_open"
        base_route_family = "vm_info"
    if base_route_family == "utility_fallback" and looks_like_vm_delete_query(normalized_compact):
        base_intent = "vm_delete"
        base_route_family = "vm_info"
    if base_route_family == "utility_fallback" and looks_like_docker_list_query(normalized_compact):
        base_intent = "docker_list"
        base_route_family = "vm_info"
    if base_route_family == "utility_fallback" and structured.action == "verify" and any(
        token in normalized_compact for token in ("duckln", "healthcheck", "health", "error free")
    ):
        base_intent = "system_verify"
        base_route_family = "system_verify"
    if base_route_family == "utility_fallback" and looks_like_vm_repo_recommendation_query(normalized_compact):
        base_intent = "vm_repo_recommendation"
        base_route_family = "vm_repo_recommendation"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.action == "verify":
        base_intent = "repo_verify"
        base_route_family = "repo_verify"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.action == "run":
        base_intent = "repo_run"
        base_route_family = "repo_run"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.action == "restart":
        base_intent = "repo_restart"
        base_route_family = "repo_restart"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.action == "stop":
        base_intent = "repo_stop"
        base_route_family = "repo_stop"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.action == "logs":
        base_intent = "repo_logs"
        base_route_family = "repo_logs"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.action == "remove":
        base_intent = "repo_removal"
        base_route_family = "repo_remove"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.scope == "path":
        base_intent = "repo_path_show"
        base_route_family = "repo_path"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.scope == "source":
        base_intent = "repo_source_show"
        base_route_family = "repo_path"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.scope == "inventory":
        base_intent = "repo_lifecycle_inventory"
        base_route_family = "repo_inventory"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.scope == "specific_status":
        base_intent = "repo_lifecycle_specific_status"
        base_route_family = "repo_status"
    if base_route_family == "utility_fallback" and structured.subject == "repo" and structured.target == "vm" and structured.action in {"recommend", "compare"}:
        base_intent = "vm_repo_recommendation"
        base_route_family = "vm_repo_recommendation"

    if base_route_family == "utility_fallback" and context.thread_state.active_topic in {
        "repo_recommendation_single",
        "repo_recommendation_rationale",
        "repo_alternatives",
    }:
        if any(phrase in normalized_compact for phrase in ("second", "other repo", "other option", "what else", "another")):
            base_intent = "repo_alternatives"
            base_route_family = "repo_alternatives"
        elif any(phrase in normalized_compact for phrase in ("what is ", "what does ", "what is it", "what does it")):
            base_intent = "repo_overview"
            base_route_family = "repo_overview"
        elif "memory" in normalized_compact:
            base_intent = "repo_memory_meta"
            base_route_family = "repo_memory_meta"
    if base_route_family == "utility_fallback" and (
        "who i am" in normalized_compact or "remember me" in normalized_compact or "kno who i am" in normalized_compact
    ):
        base_intent = "user_identity_meta"
        base_route_family = "user_identity_meta"
    if "active repo" in normalized_compact and any(
        phrase in normalized_compact for phrase in ("what do you mean", "mean by", "what does that mean")
    ):
        base_intent = "repo_active_explanation"
        base_route_family = "repo_active_explanation"
    elif any(
        phrase in normalized_compact
        for phrase in (
            "sorry i did not understand",
            "i did not understand",
            "didn't understand",
            "what are you talking about",
            "can you elaborate",
            "can you explain that",
            "explain what you mean",
            "do you understand what i am asking",
        )
    ):
        base_intent = "conversation_repair"
        base_route_family = "conversation_repair"
    if base_route_family == "utility_fallback" and looks_like_next_step_question(normalized_compact):
        base_intent = "next_step_guidance"
        base_route_family = "next_step_guidance"

    if subject_name is None and normalized_compact in {"help me with it", "run that", "what about that one", "what about that repo"}:
        options = clarification_options_for_message(normalized_compact, context)
        return RouteDecision(
            route_family="clarify",
            intent="clarify",
            confidence=0.82,
            margin=0.4,
            resolved_subject=None,
            requires_clarification=True,
            clarification_options=options,
            response_contract=response_contract_for_family("clarify"),
            front_door_category=front_door.category,
            front_door_reason=front_door.reason,
            workflow_priority_applied=front_door.workflow_priority_applied,
        )

    followup_intent = resolve_followup_intent(normalized_compact, context.followup_state)
    if base_route_family in current_turn_priority_families:
        followup_intent = None
    corrected_intent = resolve_corrected_intent(
        normalized_compact,
        base_intent,
        recent_turns,
        recent_replies,
        config_dir,
    )
    clarification_options = clarification_options_for_message(normalized_compact, context)

    if front_door.category == "workflow_followup":
        if followup_intent is not None:
            candidates.append(
                RouteCandidate(
                    route_family=route_family_for_intent(followup_intent),
                    intent=followup_intent,
                    score=0.94,
                    reason="front-door workflow follow-up classification resolved to the pending action",
                )
            )
        else:
            candidates.append(
                RouteCandidate(
                    route_family="next_step_guidance",
                    intent="next_step_guidance",
                    score=0.91,
                    reason="front-door workflow follow-up classification",
                )
            )
    elif front_door.category == "repo_state":
        repo_state_route_family = {
            "inventory": "repo_inventory",
            "specific_status": "repo_status",
            "path": "repo_path",
            "source": "repo_path",
        }.get(structured.scope or "", "repo_status")
        repo_state_intent = {
            "inventory": "repo_lifecycle_inventory",
            "specific_status": "repo_lifecycle_active",
            "path": "repo_path_show",
            "source": "repo_source_show",
        }.get(structured.scope or "", "repo_lifecycle_active")
        candidates.append(
            RouteCandidate(
                route_family=repo_state_route_family,
                intent=repo_state_intent,
                score=0.9,
                reason="front-door repo-state classification",
            )
        )
    elif front_door.category == "repo_runtime":
        runtime_route_family = {
            "run": "repo_run",
            "restart": "repo_restart",
            "stop": "repo_stop",
            "verify": "repo_verify",
            "logs": "repo_logs",
            "remove": "repo_remove",
        }.get(structured.action or "", "workflow_action")
        candidates.append(
            RouteCandidate(
                route_family=runtime_route_family,
                intent=intent_for_route_family(runtime_route_family, normalized_compact),
                score=0.92,
                reason="front-door repo-runtime classification",
            )
        )

    for heuristic in bootstrap_heuristics():
        if any(phrase_matches(normalized_compact, phrase) for phrase in heuristic.phrases):
            intent = intent_for_route_family(heuristic.route_family, normalized_compact)
            candidates.append(
                RouteCandidate(
                    route_family=heuristic.route_family,
                    intent=intent,
                    score=heuristic.base_score,
                    reason=f"bootstrap phrase family matched {heuristic.route_family}",
                )
            )

    if base_intent != "generic":
        candidates.append(
            RouteCandidate(
                route_family=base_route_family,
                intent=base_intent,
                score=base_intent_score(base_intent),
                reason=f"base intent matched {base_intent}",
            )
        )
    if corrected_intent != base_intent:
        candidates.append(
            RouteCandidate(
                route_family=route_family_for_intent(corrected_intent),
                intent=corrected_intent,
                score=0.82,
                reason="correction-aware reroute signal",
            )
        )
    if followup_intent is not None:
        candidates.append(
            RouteCandidate(
                route_family=route_family_for_intent(followup_intent),
                intent=followup_intent,
                score=0.94,
                reason="explicit follow-up continuation",
            )
        )
    if looks_like_command_surface_request(normalized_compact):
        candidates.append(
            RouteCandidate(
                route_family="workflow_action",
                intent="generic",
                score=0.8,
                reason="command surface request should reach command hints before fallback",
            )
        )

    if subject_name is not None and not looks_like_social_or_identity_turn(normalized_compact) and not looks_like_repo_lifecycle_turn(normalized_compact):
        for index, candidate in enumerate(candidates):
            if candidate.route_family in {
                "repo_recommendation_single",
                "repo_recommendation_compare",
                "repo_recommendation_rationale",
                "repo_alternatives",
                "repo_overview",
                "repo_memory_meta",
                "repo_requirements",
                "repo_fit",
                "repo_status",
                "repo_verify",
                "repo_access",
                "repo_path",
                "repo_run",
                "repo_restart",
                "repo_remove",
                "vm_repo_recommendation",
                "workflow_action",
            }:
                candidates[index] = RouteCandidate(
                    route_family=candidate.route_family,
                    intent=candidate.intent,
                    score=min(candidate.score + 0.06, 0.99),
                    reason=f"{candidate.reason}; repo subject resolved",
                )

    if any(token in normalized_compact for token in (" my system", " this machine", " my machine", " my laptop")):
        for index, candidate in enumerate(candidates):
            if candidate.route_family in {
                "repo_capability_coverage",
                "repo_recommendation_single",
                "repo_recommendation_compare",
                "repo_recommendation_rationale",
                "repo_requirements",
                "repo_fit",
                "vm_repo_recommendation",
                "system_summary",
                "system_verify",
            }:
                candidates[index] = RouteCandidate(
                    route_family=candidate.route_family,
                    intent=candidate.intent,
                    score=min(candidate.score + 0.04, 0.99),
                    reason=f"{candidate.reason}; machine-aware wording present",
                )
    if (
        base_route_family not in current_turn_priority_families
        and not looks_like_repo_lifecycle_turn(normalized_compact)
        and context.thread_state.active_topic in {"repo_recommendation_single", "repo_alternatives", "repo_recommendation_rationale"}
    ):
        for index, candidate in enumerate(candidates):
            if candidate.route_family in {"repo_alternatives", "repo_overview", "repo_memory_meta", "repo_recommendation_rationale"}:
                candidates[index] = RouteCandidate(
                    route_family=candidate.route_family,
                    intent=candidate.intent,
                    score=min(candidate.score + 0.05, 0.99),
                    reason=f"{candidate.reason}; active recommendation thread",
                )

    if not candidates:
        if clarification_options:
            return RouteDecision(
                route_family="clarify",
                intent="clarify",
                confidence=0.55,
                margin=0.0,
                resolved_subject=subject_name,
                requires_clarification=True,
                clarification_options=clarification_options,
                response_contract=response_contract_for_family("clarify"),
                front_door_category=front_door.category,
                front_door_reason=front_door.reason,
                workflow_priority_applied=front_door.workflow_priority_applied,
            )
        top_score = 0.0
    else:
        aggregated = aggregate_route_candidates(candidates)
        snapshot = build_confidence_snapshot(aggregated)
        top = snapshot.top
        assert top is not None
        clarification_options = clarification_options_for_candidates(
            aggregated,
            context,
            normalized_compact,
            clarification_options,
        )

        base_candidate = explicit_base_candidate(
            snapshot,
            base_intent=base_intent,
            base_route_family=base_route_family,
        )
        if base_candidate is not None:
            second_score = snapshot.second.score if snapshot.second is not None else 0.0
            return RouteDecision(
                route_family=base_candidate.route_family,
                intent=base_candidate.intent,
                confidence=base_candidate.score,
                margin=max(0.0, base_candidate.score - second_score),
                resolved_subject=subject_name,
                requires_clarification=False,
                clarification_options=(),
                response_contract=response_contract_for_family(base_candidate.route_family),
                front_door_category=front_door.category,
                front_door_reason=front_door.reason,
                workflow_priority_applied=front_door.workflow_priority_applied,
            )
        if should_answer_without_clarify(
            snapshot,
            followup_intent=followup_intent,
            subject_name=subject_name,
        ):
            return RouteDecision(
                route_family=top.route_family,
                intent=top.intent,
                confidence=top.score,
                margin=snapshot.margin,
                resolved_subject=subject_name,
                requires_clarification=False,
                clarification_options=(),
                response_contract=response_contract_for_family(top.route_family),
                front_door_category=front_door.category,
                front_door_reason=front_door.reason,
                workflow_priority_applied=front_door.workflow_priority_applied,
            )
        if should_clarify_route(
            snapshot,
            clarification_option_count=len(clarification_options),
            subject_name=subject_name,
            top_route_family=top.route_family,
            looks_like_repo_pronoun_followup=looks_like_repo_pronoun_followup(normalized_compact),
        ):
            return RouteDecision(
                route_family="clarify",
                intent="clarify",
                confidence=max(top.score, 0.55),
                margin=snapshot.margin,
                resolved_subject=subject_name,
                requires_clarification=True,
                clarification_options=clarification_options,
                response_contract=response_contract_for_family("clarify"),
                front_door_category=front_door.category,
                front_door_reason=front_door.reason,
                workflow_priority_applied=front_door.workflow_priority_applied,
            )
        top_score = top.score

    return RouteDecision(
        route_family="clarify",
        intent="clarify",
        confidence=max(top_score, 0.5),
        margin=0.0,
        resolved_subject=subject_name,
        requires_clarification=True,
        clarification_options=clarification_options,
        response_contract=response_contract_for_family("clarify"),
        front_door_category=front_door.category,
        front_door_reason=front_door.reason,
        workflow_priority_applied=front_door.workflow_priority_applied,
    )
