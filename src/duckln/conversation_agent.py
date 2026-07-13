"""Supervised conversation routing for Duckln non-slash terminal input."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path

from agent.probe import SystemProbe
from duckln.agent_context import (
    AgentContext,
    AgentContextService,
    RepoInventoryEntry,
    LiveRepoSessionEntry,
    RepoKnowledgeContext,
    RepoStateSnapshot,
    WorkflowStateSnapshot,
    build_hardware_reasoning,
    build_repo_feasibility_assessment,
)
from duckln.config import AppConfig
from duckln.conversation_routes.context_resolution import (
    repo_status_record as _repo_status_record_from_module,
    resolve_active_repo as _resolve_active_repo_from_module,
    resolve_conversation_subject as _resolve_conversation_subject_from_module,
)
from duckln.conversation_routes.context_building import (
    build_conversation_context as _build_conversation_context_from_module,
    build_conversation_thread_state as _build_conversation_thread_state_from_module,
)
from duckln.conversation_routes.followups import resolve_followup_intent as _resolve_followup_intent_from_state
from duckln.conversation_routes.local_general import build_local_general_reply as _build_local_general_reply
from duckln.conversation_routes.normalizer import (
    classify_conversation_intent as _classify_conversation_intent_from_normalizer,
    extract_github_repo_url as _extract_github_repo_url_from_normalizer,
    normalize_conversation_turn as _normalize_conversation_turn,
)
from duckln.conversation_routes.recommendation_support import (
    RecommendationCandidate,
    alternative_options as _alternative_options,
    compare_options as _compare_options,
    find_recommendation_candidate as _find_recommendation_candidate,
    fit_label_sentence as _fit_label_sentence,
    fit_label_text as _fit_label_text,
    has_promoted_style_feedback as _has_promoted_style_feedback,
    machine_explanation as _machine_explanation,
    promoted_heuristic_lookup as _promoted_heuristic_lookup,
    rationale_options as _rationale_options,
    recommendation_options as _recommendation_options,
    recommendation_system_hint as _recommendation_system_hint,
    repo_knowledge_lookup as _repo_knowledge_lookup,
    requested_repo_comparison_candidates as _requested_repo_comparison_candidates,
    score_recommendation_candidates as _score_recommendation_candidates,
    short_reason_text as _short_reason_text,
)
from duckln.conversation_routes.recovery import (
    clarification_options_for_candidates as _clarification_options_for_candidates_from_recovery,
    clarification_options_for_message as _clarification_options_for_message_from_recovery,
    clarification_reply_texts as _clarification_reply_texts_from_recovery,
    repair_reply_texts as _repair_reply_texts_from_recovery,
    restatement_reply_texts as _restatement_reply_texts_from_recovery,
    utility_fallback_reply_texts as _utility_fallback_reply_texts_from_recovery,
)
from duckln.conversation_routes.entity_resolution import resolve_repo_name_hint as _resolve_repo_name_hint
from duckln.conversation_routes.intent_schema import parse_structured_intent as _parse_structured_intent
from duckln.conversation_routes.intent_corrections import (
    looks_like_repeated_deflection_breakout as _looks_like_repeated_deflection_breakout_from_module,
    phrase_learning_bucket as _phrase_learning_bucket_from_module,
    recent_replies_were_command_deflections as _recent_replies_were_command_deflections_from_module,
    resolve_corrected_intent as _resolve_corrected_intent_from_module,
)
from duckln.conversation_routes.repo import (
    repo_alias_map as _repo_alias_map_from_route,
    repo_from_followup_state as _repo_from_followup_state_route,
    repo_from_recommendation_context as _repo_from_recommendation_context_route,
    repo_from_workflow_state as _repo_from_workflow_state_route,
    repo_name_from_state as _repo_name_from_state_route,
    resolve_mentioned_repo as _resolve_mentioned_repo_route,
)
from duckln.conversation_routes.repo_advice import build_repo_advice_reply as _build_repo_advice_reply
from duckln.conversation_routes.repo_recommendation import build_repo_recommendation_reply as _build_repo_recommendation_reply
from duckln.conversation_routes.repo_route import build_repo_route_reply as _build_repo_route_reply
from duckln.conversation_routes.repo_responses import (
    RouteDraft as _RouteDraft,
)
from duckln.conversation_routes.router import (
    route_conversation as _route_conversation_from_router,
    route_family_for_intent as _route_family_for_intent_from_router,
)
from duckln.conversation_routes.decision_responses import (
    build_clarification_reply as _build_clarification_reply_from_module,
    build_utility_fallback_reply as _build_utility_fallback_reply_from_module,
)
from duckln.conversation_routes.provider_support import (
    build_provider_backed_reply as _build_provider_backed_reply,
    can_use_live_conversation as _can_use_live_conversation,
    format_gib as _format_gib,
    lightweight_system_probe as _lightweight_system_probe,
    maybe_refine_reply_with_provider as _maybe_refine_reply_with_provider,
)
from duckln.conversation_routes.reply_runtime import (
    clarification_option_labels_for_reply as _clarification_option_labels_for_reply_from_module,
    free_text_reply_from_draft as _free_text_reply_from_draft_from_module,
    looks_like_command_surface_request as _looks_like_command_surface_request_from_module,
    phrase_matches as _phrase_matches_from_module,
    pick_free_text_reply as _pick_free_text_reply_from_module,
    pick_text_option as _pick_text_option_from_module,
    with_route_metadata as _with_route_metadata_from_module,
)
from duckln.conversation_routes.replies import (
    memory_meta_summary as _memory_meta_summary,
    repo_overview_reply_texts as _repo_overview_reply_texts,
)
from duckln.conversation_routes.rendering import coverage_options as _coverage_options
from duckln.conversation_routes.social import (
    acceptance_phrase as _acceptance_phrase,
    extract_user_alias as _extract_user_alias,
    looks_like_next_step_question as _looks_like_next_step_question,
    looks_like_social_or_identity_turn as _looks_like_social_or_identity_turn,
)
from duckln.conversation_routes.state_views import (
    DurableRecommendationView,
    DurableRepoMemoryView,
    LiveThreadView,
    PendingOfferView,
    build_durable_recommendation_view,
    build_durable_repo_memory_view,
    build_live_thread_view,
    build_pending_offer_view,
)
from duckln.conversation_routes.threading import (
    followup_expiry_for_reply as _followup_expiry_for_reply,
    state_is_expired as _state_is_expired,
    thread_expiry_for_reply as _thread_expiry_for_reply,
    offer_id_for_reply as _offer_id_for_reply,
    thread_id_for_reply as _thread_id_for_reply,
    thread_ids_match as _thread_ids_match,
)
from duckln.conversation_routes.supervisor_state import (
    read_supervisor_followup_state as _read_supervisor_followup_state_from_module,
    reconcile_fit_with_followup_state as _reconcile_fit_with_followup_state,
    verified_fact_summary as _verified_fact_summary,
)
from duckln.conversation_routes.supervisor_persistence import (
    persist_followup_state as _persist_followup_state_from_module,
    persist_grounded_conversation_learning as _persist_grounded_conversation_learning_from_module,
)
from duckln.conversation_policy import (
    ClarificationOption,
    ResponseContract,
    RouteCandidate,
    RouteDecision,
    bootstrap_heuristics,
    response_contract_for_family,
)
from duckln.render_blocks import bullet_block as _bullet_block, join_blocks as _join_reply_blocks
from state.access import read_config_snapshot, read_followup_state, write_followup_state
from state.repo_catalog import RepoCatalogRecord, RepoCatalogRefreshError, load_sorted_local_repo_catalog, resolve_public_github_repo_record
from state.store import initialize_state_store


@dataclass(frozen=True)
class FreeTextReply:
    """Bounded conversational reply plus light metadata for de-repetition."""

    text: str
    intent: str
    steer: str | None = None
    specialist_name: str = "fallback"
    provider_backed: bool = False
    action: str | None = None
    action_repo_key: str | None = None
    recommendation_repo_key: str | None = None
    recommendation_repo_name: str | None = None
    recommendation_reason: str | None = None
    recommendation_alternatives: tuple[str, ...] = ()
    recommendation_caveat: str | None = None
    hardware_summary: str | None = None
    recommendation_fit_label: str | None = None
    trust_evidence_summary: str | None = None
    route_family: str | None = None
    route_confidence: float | None = None
    response_contract_name: str | None = None
    answer_style: str | None = None
    user_alias: str | None = None
    pending_offer_kind: str | None = None
    pending_offer_label: str | None = None
    pending_offer_repo_key: str | None = None
    pending_offer_repo_name: str | None = None


@dataclass(frozen=True)
class ConversationTurn:
    """Small recent-turn context for the conversation supervisor."""

    role: str
    content: str


@dataclass(frozen=True)
class ConversationContext:
    """Grounded context for bounded non-slash conversation."""

    records: tuple[RepoCatalogRecord, ...]
    mentioned_repo: RepoCatalogRecord | None
    active_repo: RepoCatalogRecord | None
    platform_hint: str
    machine_explanation: str
    execution_target: str
    active_vm_name: str | None
    requested_target_name: str | None
    vm_inventory_snapshot: tuple[dict[str, object], ...]
    agent_context: AgentContext
    repo_knowledge: RepoKnowledgeContext | None
    repo_state_snapshot: RepoStateSnapshot | None
    active_repo_snapshot: RepoStateSnapshot | None
    repo_inventory_snapshot: tuple[RepoInventoryEntry, ...]
    live_repo_sessions_snapshot: tuple[LiveRepoSessionEntry, ...]
    workflow_state: WorkflowStateSnapshot | None
    recommendation_memory: dict[str, object] | None
    recent_thread_summary: str | None
    project_summary: str | None
    promoted_heuristics: tuple[dict[str, object], ...]
    followup_state: "SupervisorFollowupState"
    thread_state: "ConversationThreadState"
    live_thread: LiveThreadView
    pending_offer: PendingOfferView
    durable_recommendation: DurableRecommendationView
    durable_repo_memory: DurableRepoMemoryView | None = None


@dataclass(frozen=True)
class SupervisorFollowupState:
    """Persisted supervisor state for free-text follow-up resolution."""

    last_supervisor_decision: str | None = None
    last_route_family: str | None = None
    last_route_confidence: float | None = None
    last_response_contract: str | None = None
    active_topic: str | None = None
    pending_repo_key: str | None = None
    pending_repo_name: str | None = None
    pending_next_action: str | None = None
    pending_offer_kind: str | None = None
    pending_offer_label: str | None = None
    pending_offer_repo_key: str | None = None
    pending_offer_repo_name: str | None = None
    pending_offer_id: str | None = None
    pending_offer_thread_id: str | None = None
    pending_offer_expires_at: str | None = None
    pending_question_type: str | None = None
    pending_subject_type: str | None = None
    pending_clarification_options: tuple[str, ...] = ()
    last_detected_system_summary: str | None = None
    last_recommendation_fit: str | None = None
    last_verified_fact_summary: str | None = None
    last_answer_style: str | None = None
    last_discussed_repo_key: str | None = None
    last_discussed_repo_name: str | None = None
    last_choice_prompt: str | None = None
    last_chosen_option: str | None = None
    last_next_step_offered: str | None = None
    last_completed_step: str | None = None
    last_recommendation_alternatives: tuple[str, ...] = ()
    shortlist_repo_names: tuple[str, ...] = ()
    shortlist_primary_repo: str | None = None
    shortlist_secondary_repo: str | None = None
    last_render_fingerprint: str | None = None
    last_slash_runtime_action: str | None = None
    active_thread_id: str | None = None
    active_thread_expires_at: str | None = None
    active_session_id: str | None = None
    active_execution_target: str | None = None
    active_vm_name: str | None = None
    last_plan_path: str | None = None


@dataclass(frozen=True)
class ResolvedConversationSubject:
    """Resolved repo subject plus the source used to resolve it."""

    repo: RepoCatalogRecord | None
    source: str | None = None


@dataclass(frozen=True)
class SystemSummaryReply:
    """Bounded system-summary response contract."""

    summary: str


@dataclass(frozen=True)
class RecommendationShortlistState:
    """Persisted recommendation shortlist for thread-aware follow-ups."""

    repo_names: tuple[str, ...] = ()
    primary_repo: str | None = None
    secondary_repo: str | None = None


@dataclass(frozen=True)
class ConversationThreadState:
    """High-signal thread context shared across chat and /repos."""

    active_topic: str | None = None
    active_repo_name: str | None = None
    active_repo_key: str | None = None
    execution_target: str = "local"
    active_vm_name: str | None = None
    shortlist: RecommendationShortlistState = RecommendationShortlistState()
    pending_offer_kind: str | None = None
    pending_offer_label: str | None = None
    pending_offer_repo_name: str | None = None
    pending_offer_id: str | None = None
    pending_offer_thread_id: str | None = None
    pending_offer_expires_at: str | None = None
    active_thread_id: str | None = None
    active_thread_expires_at: str | None = None
    active_session_id: str | None = None
    last_slash_runtime_action: str | None = None
    last_next_step_offered: str | None = None
    last_render_fingerprint: str | None = None


@dataclass(frozen=True)
class RenderedPreflightReply:
    """Rendered /repos preflight summary."""

    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class RenderedRequirementsReply:
    """Rendered /repos requirements summary."""

    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _reply_has_repo_thread(reply: FreeTextReply) -> bool:
    return bool(
        reply.intent.startswith("repo_")
        or reply.intent in {"recommendation_followup", "slash_runtime_followup", "setup_request", "workflow_action"}
        or reply.action in {"run_repo", "set_up_repo"}
    )


def _reply_fingerprint(*, route_family: str | None, repo_name: str | None = None, topic: str | None = None, action: str | None = None) -> str:
    bits = [route_family or "generic", repo_name or "-", topic or "-", action or "-"]
    return "|".join(bits)


def _next_step_options(
    *,
    recent_replies: tuple[FreeTextReply, ...],
    thread_state: ConversationThreadState,
    inspect_repo_name: str | None = None,
    allow_compare: bool = True,
    allow_repos: bool = True,
) -> tuple[str, ...]:
    inspect_target = inspect_repo_name or "it"
    options = [
        f"If you want more confidence first, I can inspect {inspect_target}'s requirements. If you're ready to act now, /repos is the quickest path.",
        f"If you want the tradeoff, I can line {inspect_target} up against the runner-up.",
        "If you're ready to act, /repos is the quickest handoff.",
        f"If you'd rather stay in chat, I can keep narrowing {inspect_target}.",
    ]
    if not allow_compare:
        options = [option for option in options if "runner-up" not in option]
    if not allow_repos:
        options = [option for option in options if "/repos" not in option]
    if thread_state.last_next_step_offered:
        lowered = thread_state.last_next_step_offered.lower().replace("_", " ")
        filtered = [option for option in options if lowered not in option.lower()]
        if filtered:
            options = filtered
    return tuple(options)


def _next_step_phrase(
    *,
    recent_replies: tuple[FreeTextReply, ...],
    thread_state: ConversationThreadState,
    inspect_repo_name: str | None = None,
    allow_compare: bool = True,
    allow_repos: bool = True,
) -> str:
    return _pick_text_option(
        _next_step_options(
            recent_replies=recent_replies,
            thread_state=thread_state,
            inspect_repo_name=inspect_repo_name,
            allow_compare=allow_compare,
            allow_repos=allow_repos,
        ),
        recent_replies,
    )


def _restatement_budget_exhausted(recent_replies: tuple[FreeTextReply, ...], *, route_families: tuple[str, ...]) -> bool:
    for reply in recent_replies[-2:]:
        if reply.route_family in route_families:
            return True
    return False


def _should_surface_trust_note(
    *,
    recent_replies: tuple[FreeTextReply, ...],
    feasibility,
    repo_knowledge: RepoKnowledgeContext | None = None,
) -> bool:
    if feasibility.trust.verified is not None:
        return True
    if repo_knowledge is not None and repo_knowledge.source == "catalog":
        return True
    lowered_recent = " ".join(reply.text.lower() for reply in recent_replies[-2:])
    return "how do you know" in lowered_recent or "detected:" in lowered_recent or "inferred:" in lowered_recent


def _natural_trust_note(
    *,
    feasibility,
    repo_knowledge: RepoKnowledgeContext | None = None,
) -> str:
    if feasibility.trust.verified is not None:
        return " Duckln is also carrying a tracked verification result for this repo."
    if repo_knowledge is not None and repo_knowledge.source == "catalog":
        return " This is still a bounded estimate from catalog-level repo notes."
    return ""


def _plan_mode_nudge_for_message(
    *,
    message: str,
    current: AppConfig | None,
    config_dir: Path | None,
) -> FreeTextReply | None:
    """Return a FreeTextReply nudging toward `/plan` when Plan Mode is on
    and the user typed a multi-step request in free text.

    Returns None when no nudge is needed — the caller proceeds with the
    normal conversation routing.
    """
    if current is None or not getattr(current, "plan_mode_enabled", False):
        return None
    try:
        from duckln.plan_mode import looks_like_multistep_request
    except Exception:
        return None
    if not looks_like_multistep_request(message):
        return None
    pending_summary = ""
    if config_dir is not None:
        try:
            from state.access import read_pending_plan

            pending = read_pending_plan(config_dir)
            if pending is not None:
                pending_summary = (
                    f" A plan is already pending (id={str(pending.get('plan_id', ''))[:8]}, "
                    f"status={pending.get('status', '?')}). Use `/plan show` to view, "
                    f"`/plan approve` to run, `/plan reject` to discard, "
                    f"`/plan edit` to revise."
                )
        except Exception:
            pending_summary = ""
    if pending_summary:
        text = (
            "Plan Mode is on. " + pending_summary
        )
    else:
        text = (
            "Plan Mode is on, so Duckln will plan this before doing it. "
            "Run `/repos` to pick a repo (a plan is generated automatically), "
            "or run `/plan off` to skip planning."
        )
    return FreeTextReply(
        text,
        intent="plan_mode_nudge",
        specialist_name="plan_mode",
    )


class ConversationSpecialist(ABC):
    """Bounded specialist for one conversation domain."""

    specialist_name: str

    @abstractmethod
    def respond(
        self,
        *,
        message: str,
        intent: str,
        current: AppConfig | None,
        config_dir: Path | None,
        system_probe: SystemProbe,
        context: ConversationContext,
        recent_turns: tuple[ConversationTurn, ...],
        recent_replies: tuple[FreeTextReply, ...],
        client,
    ) -> FreeTextReply:
        """Return one concise conversational response."""


class LocalFallbackConversationSpecialist(ConversationSpecialist):
    """Deterministic local specialist for bounded non-slash conversation."""

    specialist_name = "conversation-fallback"

    def respond(
        self,
        *,
        message: str,
        intent: str,
        current: AppConfig | None,
        config_dir: Path | None,
        system_probe: SystemProbe,
        context: ConversationContext,
        recent_turns: tuple[ConversationTurn, ...],
        recent_replies: tuple[FreeTextReply, ...],
        client,
    ) -> FreeTextReply:
        normalized_compact = " ".join(message.strip().lower().split())
        prefers_direct_tone = _has_promoted_style_feedback(context.promoted_heuristics)
        early_reply = _build_local_general_reply(
            intent=intent,
            message=message,
            normalized_compact=normalized_compact,
            current=current,
            config_dir=config_dir,
            system_probe=system_probe,
            context=context,
            recent_turns=recent_turns,
            recent_replies=recent_replies,
            specialist_name=self.specialist_name,
            prefers_direct_tone=prefers_direct_tone,
            reply_factory=FreeTextReply,
            pick_reply=_pick_free_text_reply,
            extract_user_alias=_extract_user_alias,
            join_blocks=_join_reply_blocks,
            bullet_block=_bullet_block,
            build_system_summary_reply=_build_system_summary_reply,
            memory_meta_summary=_memory_meta_summary,
            repair_reply_texts=_repair_reply_texts_from_recovery,
            restatement_reply_texts=_restatement_reply_texts_from_recovery,
            list_learning_records=lambda target_config_dir: initialize_state_store(target_config_dir).list_learning_records(),
        )
        if early_reply is not None:
            return early_reply

        repo_advice_reply = _build_repo_advice_reply(
            intent=intent,
            normalized_compact=normalized_compact,
            context=context,
            config_dir=config_dir,
            system_probe=system_probe,
            recent_replies=recent_replies,
            specialist_name=self.specialist_name,
            reply_factory=FreeTextReply,
            pick_reply=_pick_free_text_reply,
            coverage_options=_coverage_options,
            score_recommendation_candidates=_score_recommendation_candidates,
            repo_requirement_reply=_repo_requirement_reply,
            repo_overview_reply_texts=_repo_overview_reply_texts,
            next_step_phrase=_next_step_phrase,
            fit_label_text=_fit_label_text,
            fit_label_sentence=_fit_label_sentence,
            natural_trust_note=_natural_trust_note,
            should_surface_trust_note=_should_surface_trust_note,
            build_hardware_reasoning=build_hardware_reasoning,
            build_repo_feasibility_assessment=build_repo_feasibility_assessment,
            verified_fact_summary=lambda target_config_dir, repo: _verified_fact_summary(
                target_config_dir,
                repo,
                repo_status_record=_repo_status_record,
            ),
        )
        if repo_advice_reply is not None:
            return repo_advice_reply

        repo_route_reply = _build_repo_route_reply(
            intent=intent,
            context=context,
            reply_from_draft=_free_text_reply_from_draft,
            specialist_name=self.specialist_name,
        )
        if repo_route_reply is not None:
            return repo_route_reply

        tail_reply = _build_local_general_reply(
            intent="command_hint" if intent in {"help_request", "setup_request", "generic"} else intent,
            message=message,
            normalized_compact=normalized_compact,
            current=current,
            config_dir=config_dir,
            system_probe=system_probe,
            context=context,
            recent_turns=recent_turns,
            recent_replies=recent_replies,
            specialist_name=self.specialist_name,
            prefers_direct_tone=prefers_direct_tone,
            reply_factory=FreeTextReply,
            pick_reply=_pick_free_text_reply,
            extract_user_alias=_extract_user_alias,
            join_blocks=_join_reply_blocks,
            bullet_block=_bullet_block,
            build_system_summary_reply=_build_system_summary_reply,
            memory_meta_summary=_memory_meta_summary,
            repair_reply_texts=_repair_reply_texts_from_recovery,
            restatement_reply_texts=_restatement_reply_texts_from_recovery,
            list_learning_records=lambda target_config_dir: initialize_state_store(target_config_dir).list_learning_records(),
        )
        if tail_reply is not None and (tail_reply.intent == "command_hint" or intent == "generic"):
            return tail_reply

        generic_reply = _build_local_general_reply(
            intent="generic",
            message=message,
            normalized_compact=normalized_compact,
            current=current,
            config_dir=config_dir,
            system_probe=system_probe,
            context=context,
            recent_turns=recent_turns,
            recent_replies=recent_replies,
            specialist_name=self.specialist_name,
            prefers_direct_tone=prefers_direct_tone,
            reply_factory=FreeTextReply,
            pick_reply=_pick_free_text_reply,
            extract_user_alias=_extract_user_alias,
            join_blocks=_join_reply_blocks,
            bullet_block=_bullet_block,
            build_system_summary_reply=_build_system_summary_reply,
            memory_meta_summary=_memory_meta_summary,
            repair_reply_texts=_repair_reply_texts_from_recovery,
            restatement_reply_texts=_restatement_reply_texts_from_recovery,
            list_learning_records=lambda target_config_dir: initialize_state_store(target_config_dir).list_learning_records(),
        )
        return generic_reply


class RepoRecommendationConversationSpecialist(ConversationSpecialist):
    """Use the cached repo catalog plus system signals for recommendations."""

    specialist_name = "conversation-repo-recommendation"

    def respond(
        self,
        *,
        message: str,
        intent: str,
        current: AppConfig | None,
        config_dir: Path | None,
        system_probe: SystemProbe,
        context: ConversationContext,
        recent_turns: tuple[ConversationTurn, ...],
        recent_replies: tuple[FreeTextReply, ...],
        client,
    ) -> FreeTextReply:
        return _build_repo_recommendation_reply(
            message=message,
            intent=intent,
            context=context,
            config_dir=config_dir,
            system_probe=system_probe,
            recent_replies=recent_replies,
            specialist_name=self.specialist_name,
            reply_factory=FreeTextReply,
            pick_reply=_pick_free_text_reply,
            score_recommendation_candidates=_score_recommendation_candidates,
            recommendation_system_hint=_recommendation_system_hint,
            find_recommendation_candidate=_find_recommendation_candidate,
            requested_repo_comparison_candidates=_requested_repo_comparison_candidates,
            recommendation_options=lambda candidate, *, system_hint, thread_state, recent_replies: _recommendation_options(
                candidate,
                system_hint=system_hint,
                thread_state=thread_state,
                recent_replies=recent_replies,
                join_blocks=_join_reply_blocks,
                next_step_phrase=_next_step_phrase,
            ),
            rationale_options=lambda candidate, *, alternative=None, thread_state, recent_replies: _rationale_options(
                candidate,
                alternative=alternative,
                thread_state=thread_state,
                recent_replies=recent_replies,
                join_blocks=_join_reply_blocks,
                next_step_phrase=_next_step_phrase,
            ),
            compare_options=lambda chosen, other, *, system_hint, thread_state, recent_replies: _compare_options(
                chosen,
                other,
                system_hint=system_hint,
                thread_state=thread_state,
                recent_replies=recent_replies,
                join_blocks=_join_reply_blocks,
                next_step_phrase=_next_step_phrase,
            ),
            alternative_options=lambda candidate, *, system_hint, better_name=None, thread_state, recent_replies: _alternative_options(
                candidate,
                system_hint=system_hint,
                better_name=better_name,
                thread_state=thread_state,
                recent_replies=recent_replies,
                join_blocks=_join_reply_blocks,
                next_step_phrase=_next_step_phrase,
            ),
            join_blocks=_join_reply_blocks,
            bullet_block=_bullet_block,
            next_step_phrase=_next_step_phrase,
            short_reason_text=_short_reason_text,
        )


class ProviderBackedConversationSpecialist(ConversationSpecialist):
    """Use the configured provider/model for bounded normal conversation."""

    specialist_name = "conversation-provider"

    def respond(
        self,
        *,
        message: str,
        intent: str,
        current: AppConfig | None,
        config_dir: Path | None,
        system_probe: SystemProbe,
        context: ConversationContext,
        recent_turns: tuple[ConversationTurn, ...],
        recent_replies: tuple[FreeTextReply, ...],
        client,
    ) -> FreeTextReply:
        return _build_provider_backed_reply(
            current=current,
            message=message,
            intent=intent,
            system_probe=system_probe,
            config_dir=config_dir,
            context=context,
            recent_turns=recent_turns,
            client=client,
            specialist_name=self.specialist_name,
            reply_factory=FreeTextReply,
            recommendation_system_hint=_recommendation_system_hint,
            route_family_for_intent=_route_family_for_intent,
        )


class ConversationSupervisor:
    """Supervise bounded conversation specialists for non-slash terminal input."""

    def __init__(self, *, context_service: AgentContextService | None = None) -> None:
        self._context_service = context_service or AgentContextService()
        self._fallback = LocalFallbackConversationSpecialist()
        self._repo_recommendation = RepoRecommendationConversationSpecialist()
        self._provider = ProviderBackedConversationSpecialist()

    def respond(
        self,
        message: str,
        *,
        current: AppConfig | None,
        config_dir: Path | None,
        system_probe: SystemProbe | None = None,
        recent_turns: tuple[ConversationTurn, ...] = (),
        recent_replies: tuple[FreeTextReply, ...] = (),
        client=None,
    ) -> FreeTextReply:
        # Plan 67: when Plan Mode is on and the user types a multi-step request
        # in free text, nudge them through the planning flow rather than
        # dispatching directly to a specialist that would execute commands.
        plan_mode_nudge = _plan_mode_nudge_for_message(
            message=message, current=current, config_dir=config_dir,
        )
        if plan_mode_nudge is not None:
            return plan_mode_nudge
        normalized_turn = _normalize_conversation_turn(message)
        normalized_compact = normalized_turn.normalized_compact
        probe = system_probe or _lightweight_system_probe()
        try:
            _resolve_runtime_repo_from_message(normalized_compact, config_dir=config_dir, client=client)
        except (RepoCatalogRefreshError, ValueError) as exc:
            return FreeTextReply(
                f"That looks like a GitHub repo link, but Duckln can inspect only public GitHub repos right now. {exc}",
                intent="repo_overview",
                specialist_name="conversation-custom-repo",
            )
        followup_state = _read_supervisor_followup_state(config_dir)
        base_intent = normalized_turn.base_intent
        context = _build_conversation_context(
            normalized_compact,
            current=current,
            config_dir=config_dir,
            system_probe=probe,
            recent_turns=recent_turns,
            context_service=self._context_service,
            followup_state=followup_state,
        )
        decision = _route_conversation(
            normalized_turn,
            context=context,
            recent_turns=recent_turns,
            recent_replies=recent_replies,
            config_dir=config_dir,
        )
        intent = decision.intent
        if decision.route_family == "utility_fallback":
            # Plan 174 F3: a general / random / ambiguous message gets a real FREE-FORM LLM answer
            # (in persona — incl. the dry-British off-topic redirect, F5) when a model is available,
            # NOT a canned "clarify" dead-end. Only with NO usable model do we fall back to one
            # clarification. (Unit tests without a configured provider keep the clarify behavior.)
            _free_form = None
            if current is not None and _can_use_live_conversation(current):
                try:
                    _free_form = self._provider.respond(
                        message=message, intent=(intent or "general"), current=current,
                        config_dir=config_dir, system_probe=probe, context=context,
                        recent_turns=recent_turns, recent_replies=recent_replies, client=client,
                    )
                except Exception:
                    _free_form = None
            if _free_form is not None:
                reply = _with_route_metadata(_free_form, decision)
                self._persist_grounded_conversation_learning(
                    message, reply, context=context, config_dir=config_dir,
                    intent=intent, base_intent=base_intent, recent_replies=recent_replies,
                )
                self._persist_followup_state(reply=reply, context=context, config_dir=config_dir)
                return reply
            decision = replace(
                decision,
                route_family="clarify",
                intent="clarify",
                requires_clarification=True,
                response_contract=response_contract_for_family("clarify"),
            )
            intent = decision.intent
        self._record_routing_decision(
            message=message,
            normalized_turn=normalized_turn,
            context=context,
            decision=decision,
            config_dir=config_dir,
        )

        if decision.route_family == "clarify":
            reply = _with_route_metadata(
                _build_clarification_reply(decision, context=context, recent_replies=recent_replies),
                decision,
            )
            self._persist_grounded_conversation_learning(
                message,
                reply,
                context=context,
                config_dir=config_dir,
                intent=intent,
                base_intent=base_intent,
                recent_replies=recent_replies,
            )
            self._persist_followup_state(reply=reply, context=context, config_dir=config_dir)
            return reply

        if decision.route_family == "utility_fallback":
            reply = _with_route_metadata(
                _build_utility_fallback_reply(context=context, recent_replies=recent_replies),
                decision,
            )
            self._persist_grounded_conversation_learning(
                message,
                reply,
                context=context,
                config_dir=config_dir,
                intent=intent,
                base_intent=base_intent,
                recent_replies=recent_replies,
            )
            self._persist_followup_state(reply=reply, context=context, config_dir=config_dir)
            return reply

        if intent.startswith("repo_recommendation") or intent == "repo_alternatives":
            reply = self._repo_recommendation.respond(
                message=message,
                intent=intent,
                current=current,
                config_dir=config_dir,
                system_probe=probe,
                context=context,
                recent_turns=recent_turns,
                recent_replies=recent_replies,
                client=client,
            )
            reply = _reconcile_fit_with_followup_state(reply, followup_state=followup_state)
            reply = _with_route_metadata(reply, decision)
            reply = _maybe_refine_reply_with_provider(
                reply,
                decision=decision,
                current=current,
                system_probe=probe,
                config_dir=config_dir,
                context=context,
                message=message,
                recent_turns=recent_turns,
                client=client,
                reply_factory=FreeTextReply,
                recommendation_system_hint=_recommendation_system_hint,
            )
            self._persist_grounded_conversation_learning(
                message,
                reply,
                context=context,
                config_dir=config_dir,
                intent=intent,
                base_intent=base_intent,
                recent_replies=recent_replies,
            )
            self._persist_followup_state(reply=reply, context=context, config_dir=config_dir)
            return reply

        reply = self._fallback.respond(
            message=message,
            intent=intent,
            current=current,
            config_dir=config_dir,
            system_probe=probe,
            context=context,
            recent_turns=recent_turns,
            recent_replies=recent_replies,
            client=client,
        )
        reply = _with_route_metadata(reply, decision)
        reply = _maybe_refine_reply_with_provider(
            reply,
            decision=decision,
            current=current,
            system_probe=probe,
            config_dir=config_dir,
            context=context,
            message=message,
            recent_turns=recent_turns,
            client=client,
            reply_factory=FreeTextReply,
            recommendation_system_hint=_recommendation_system_hint,
        )
        self._persist_grounded_conversation_learning(
            message,
            reply,
            context=context,
            config_dir=config_dir,
            intent=intent,
            base_intent=base_intent,
            recent_replies=recent_replies,
        )
        self._persist_followup_state(reply=reply, context=context, config_dir=config_dir)
        return reply

    def _record_routing_decision(
        self,
        *,
        message: str,
        normalized_turn,
        context: ConversationContext,
        decision: RouteDecision,
        config_dir: Path | None,
    ) -> None:
        if config_dir is None:
            return
        initialize_state_store(config_dir).record_routing_decision(
            message_text=message,
            normalized_text=normalized_turn.normalized_compact,
            top_level_category=decision.front_door_category,
            final_route_family=decision.route_family,
            final_intent=decision.intent,
            resolved_subject=decision.resolved_subject,
            confidence=decision.confidence,
            margin=decision.margin,
            workflow_state={
                "active_issue_kind": None if context.workflow_state is None else context.workflow_state.active_issue_kind,
                "active_issue_summary": None if context.workflow_state is None else context.workflow_state.active_issue_summary,
                "active_repair_phase": None if context.workflow_state is None else context.workflow_state.active_repair_phase,
                "repo_name": None if context.workflow_state is None else context.workflow_state.repo_name,
                "execution_target": None if context.workflow_state is None else context.workflow_state.execution_target,
                # Plan 169 F4: pass the objective FACTS (setup vs runtime repair, and its status)
                # as a HINT so the LLM voice frames "what's happening / continue" from the truth,
                # rather than a hardcoded label deciding for it. Deterministic only hints here.
                "active_objective_kind": None if context.workflow_state is None else context.workflow_state.active_objective_kind,
                "active_objective_status": None if context.workflow_state is None else context.workflow_state.active_objective_status,
                # Plan 171 F8: the deterministic resource-crunch FACTS (used/free/needed/reclaimed
                # + options) as a HINT, so the LLM voice can explain the disk/RAM situation and
                # reason about the best option (reclaim vs resize vs route-to-cloud vs lighten)
                # over the real numbers — the sensing/reclaim/resize stay the deterministic floor.
                "active_resource_crunch": None if context.workflow_state is None else context.workflow_state.active_resource_crunch,
            },
            followup_state={
                "pending_next_action": context.followup_state.pending_next_action,
                "pending_offer_kind": context.followup_state.pending_offer_kind,
                "pending_repo_name": context.followup_state.pending_repo_name,
                "active_topic": context.followup_state.active_topic,
            },
            metadata={
                "front_door_reason": decision.front_door_reason,
                "workflow_priority_applied": decision.workflow_priority_applied,
                "base_intent": normalized_turn.base_intent,
            },
        )

    def _persist_followup_state(
        self,
        *,
        reply: FreeTextReply,
        context: ConversationContext,
        config_dir: Path | None,
    ) -> None:
        _persist_followup_state_from_module(
            reply=reply,
            context=context,
            config_dir=config_dir,
            read_config_snapshot=read_config_snapshot,
            thread_id_for_reply=_thread_id_for_reply,
            offer_id_for_reply=_offer_id_for_reply,
            thread_expiry_for_reply=_thread_expiry_for_reply,
            followup_expiry_for_reply=_followup_expiry_for_reply,
            reply_has_repo_thread=_reply_has_repo_thread,
            resolve_reply_recommendation_repo=_resolve_reply_recommendation_repo,
            reply_fingerprint=_reply_fingerprint,
            next_action_for_reply=_next_action_for_reply,
            offer_kind_for_reply=_offer_kind_for_reply,
            offer_label_for_reply=_offer_label_for_reply,
            pending_question_type_for_reply=_pending_question_type_for_reply,
            clarification_option_labels_for_reply=_clarification_option_labels_for_reply,
            build_system_summary_reply=_build_system_summary_reply,
            verified_fact_summary=lambda target_config_dir, repo: _verified_fact_summary(
                target_config_dir,
                repo,
                repo_status_record=_repo_status_record,
            ),
            active_topic_for_reply=_active_topic_for_reply,
            write_followup_state=write_followup_state,
        )

    def _persist_grounded_conversation_learning(
        self,
        message: str,
        reply: FreeTextReply,
        *,
        context: ConversationContext,
        config_dir: Path | None,
        intent: str,
        base_intent: str,
        recent_replies: tuple[FreeTextReply, ...],
    ) -> None:
        _persist_grounded_conversation_learning_from_module(
            message,
            reply,
            context=context,
            config_dir=config_dir,
            intent=intent,
            base_intent=base_intent,
            recent_replies=recent_replies,
            context_service=self._context_service,
            resolve_reply_recommendation_repo=_resolve_reply_recommendation_repo,
            looks_like_repeated_deflection_breakout=_looks_like_repeated_deflection_breakout,
            phrase_learning_bucket=_phrase_learning_bucket,
        )


def classify_conversation_intent(normalized_compact: str) -> str:
    return _classify_conversation_intent_from_normalizer(normalized_compact)


def _route_family_for_intent(intent: str) -> str:
    return _route_family_for_intent_from_router(intent)


def _route_conversation(
    normalized_turn,
    *,
    context: ConversationContext,
    recent_turns: tuple[ConversationTurn, ...],
    recent_replies: tuple[FreeTextReply, ...],
    config_dir: Path | None,
) -> RouteDecision:
    return _route_conversation_from_router(
        normalized_turn,
        context=context,
        recent_turns=recent_turns,
        recent_replies=recent_replies,
        config_dir=config_dir,
        resolve_followup_intent=lambda text, followup_state: _resolve_followup_intent(text, followup_state=followup_state),
        resolve_corrected_intent=lambda text, current_base_intent, current_recent_turns, current_recent_replies, current_config_dir: _resolve_corrected_intent(
            text,
            base_intent=current_base_intent,
            recent_turns=current_recent_turns,
            recent_replies=current_recent_replies,
            config_dir=current_config_dir,
        ),
        clarification_options_for_message=lambda text, current_context: _clarification_options_for_message(text, context=current_context),
        clarification_options_for_candidates=lambda candidates, current_context, current_text, existing: _clarification_options_for_candidates(
            candidates,
            context=current_context,
            normalized_compact=current_text,
            existing=existing,
        ),
        phrase_matches=_phrase_matches,
        looks_like_command_surface_request=_looks_like_command_surface_request,
        looks_like_repo_pronoun_followup=_looks_like_repo_pronoun_followup,
        looks_like_social_or_identity_turn=_looks_like_social_or_identity_turn,
        looks_like_next_step_question=_looks_like_next_step_question,
    )


def _aggregate_route_candidates(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    from duckln.conversation_routes.router import aggregate_route_candidates as _aggregate_route_candidates_from_router

    return _aggregate_route_candidates_from_router(candidates)


def _intent_for_route_family(route_family: str, normalized_compact: str) -> str:
    from duckln.conversation_routes.router import intent_for_route_family as _intent_for_route_family_from_router

    return _intent_for_route_family_from_router(route_family, normalized_compact)


def _base_intent_score(intent: str) -> float:
    from duckln.conversation_routes.router import base_intent_score as _base_intent_score_from_router

    return _base_intent_score_from_router(intent)


def _clarification_options_for_message(
    normalized_compact: str,
    *,
    context: ConversationContext,
) -> tuple[ClarificationOption, ...]:
    return _clarification_options_for_message_from_recovery(
        normalized_compact,
        context=context,
        looks_like_repo_pronoun_followup=_looks_like_repo_pronoun_followup,
    )


def _clarification_options_for_candidates(
    candidates: tuple[RouteCandidate, ...] | list[RouteCandidate],
    *,
    context: ConversationContext,
    normalized_compact: str = "",
    existing: tuple[ClarificationOption, ...] = (),
) -> tuple[ClarificationOption, ...]:
    return _clarification_options_for_candidates_from_recovery(
        candidates,
        context=context,
        normalized_compact=normalized_compact,
        existing=existing,
    )


def _phrase_matches(normalized_compact: str, phrase: str) -> bool:
    return _phrase_matches_from_module(normalized_compact, phrase)


def _looks_like_command_surface_request(normalized_compact: str) -> bool:
    return _looks_like_command_surface_request_from_module(normalized_compact)


def _pick_free_text_reply(
    options: tuple[FreeTextReply, ...],
    normalized_compact: str,
    recent_replies: tuple[FreeTextReply, ...],
) -> FreeTextReply:
    return _pick_free_text_reply_from_module(
        options,
        normalized_compact,
        recent_replies,
        reply_factory=FreeTextReply,
    )


def _pick_text_option(options: tuple[str, ...], recent_replies: tuple[FreeTextReply, ...]) -> str:
    return _pick_text_option_from_module(options, recent_replies)


def _free_text_reply_from_draft(draft: _RouteDraft, *, specialist_name: str) -> FreeTextReply:
    return _free_text_reply_from_draft_from_module(
        draft,
        specialist_name=specialist_name,
        reply_factory=FreeTextReply,
    )


def _with_route_metadata(reply: FreeTextReply, decision: RouteDecision) -> FreeTextReply:
    return _with_route_metadata_from_module(
        reply,
        decision,
        reply_factory=FreeTextReply,
    )


def _build_clarification_reply(
    decision: RouteDecision,
    *,
    context: ConversationContext,
    recent_replies: tuple[FreeTextReply, ...],
) -> FreeTextReply:
    return _build_clarification_reply_from_module(
        decision=decision,
        recent_replies=recent_replies,
        reply_factory=FreeTextReply,
        pick_reply=_pick_free_text_reply,
        clarification_reply_texts=_clarification_reply_texts_from_recovery,
    )


def _build_utility_fallback_reply(
    *,
    context: ConversationContext,
    recent_replies: tuple[FreeTextReply, ...],
) -> FreeTextReply:
    return _build_utility_fallback_reply_from_module(
        context=context,
        recent_replies=recent_replies,
        reply_factory=FreeTextReply,
        pick_reply=_pick_free_text_reply,
        utility_fallback_reply_texts=_utility_fallback_reply_texts_from_recovery,
    )


def _clarification_option_labels_for_reply(reply: FreeTextReply) -> tuple[str, ...] | None:
    return _clarification_option_labels_for_reply_from_module(reply)


def _resolve_followup_intent(
    normalized_compact: str,
    *,
    followup_state: SupervisorFollowupState,
) -> str | None:
    return _resolve_followup_intent_from_state(
        normalized_compact,
        followup_state=followup_state,
        acceptance_phrase=_acceptance_phrase,
        thread_ids_match=_thread_ids_match,
    )


def _is_followup_acceptance(normalized_compact: str) -> bool:
    return _acceptance_phrase(normalized_compact) is not None


def _read_supervisor_followup_state(config_dir: Path | None) -> SupervisorFollowupState:
    return _read_supervisor_followup_state_from_module(
        config_dir,
        followup_state_type=SupervisorFollowupState,
        read_followup_state=read_followup_state,
        read_config_snapshot=read_config_snapshot,
        state_is_expired=_state_is_expired,
    )


def _resolve_corrected_intent(
    normalized_compact: str,
    *,
    base_intent: str,
    recent_turns: tuple[ConversationTurn, ...],
    recent_replies: tuple[FreeTextReply, ...],
    config_dir: Path | None,
) -> str:
    return _resolve_corrected_intent_from_module(
        normalized_compact,
        base_intent=base_intent,
        recent_turns=recent_turns,
        recent_replies=recent_replies,
        config_dir=config_dir,
        has_promoted_phrase_bucket=_has_promoted_phrase_bucket,
    )


def _phrase_learning_bucket(normalized_or_raw_message: str) -> str | None:
    return _phrase_learning_bucket_from_module(normalized_or_raw_message)


def _has_promoted_phrase_bucket(config_dir: Path | None, bucket: str) -> bool:
    if config_dir is None:
        return False
    for record in initialize_state_store(config_dir).list_promoted_heuristics(family="conversation"):
        if record.subject_key == f"phrase:{bucket}":
            return True
    return False


def _recent_replies_were_command_deflections(recent_replies: tuple[FreeTextReply, ...]) -> bool:
    return _recent_replies_were_command_deflections_from_module(recent_replies)


def _looks_like_repeated_deflection_breakout(intent: str, recent_replies: tuple[FreeTextReply, ...]) -> bool:
    return _looks_like_repeated_deflection_breakout_from_module(intent, recent_replies)


def _looks_like_repo_pronoun_followup(normalized_compact: str) -> bool:
    return any(
        phrase in normalized_compact
        for phrase in (
            " it ",
            " its ",
            "that repo",
            "that one",
            "this repo",
            "run it",
            "set it up",
            "inspect its",
            "is it ",
        )
    ) or normalized_compact in {"it", "its", "that one", "that repo", "this repo"}


def _extract_github_repo_url(text: str) -> str | None:
    return _extract_github_repo_url_from_normalizer(text)


def _load_recommendation_records(config_dir: Path | None) -> tuple[RepoCatalogRecord, ...]:
    if config_dir is None:
        return ()
    try:
        curated = list(load_sorted_local_repo_catalog(config_dir))
        recent_custom = [
            RepoCatalogRecord(
                name=record.repo_name,
                repo_url=record.repo_url,
                stars=record.stars,
                description=record.description,
                category=record.category,
                framework=record.framework,
                last_updated=record.last_updated,
            )
            for record in initialize_state_store(config_dir).list_recent_custom_repos()
        ]
        combined: dict[str, RepoCatalogRecord] = {}
        for record in (*recent_custom, *curated):
            combined.setdefault(record.repo_url.lower(), record)
        return tuple(combined.values())
    except Exception:
        return ()


def _resolve_runtime_repo_from_message(
    normalized_compact: str,
    *,
    config_dir: Path | None,
    client=None,
) -> RepoCatalogRecord | None:
    repo_url = _extract_github_repo_url(normalized_compact)
    if repo_url is None or config_dir is None:
        return None
    store = initialize_state_store(config_dir)
    for record in store.list_recent_custom_repos():
        if record.repo_url.lower() == repo_url.lower():
            return RepoCatalogRecord(
                name=record.repo_name,
                repo_url=record.repo_url,
                stars=record.stars,
                description=record.description,
                category=record.category,
                framework=record.framework,
                last_updated=record.last_updated,
            )
    record = resolve_public_github_repo_record(repo_url, client=client)
    store.upsert_recent_custom_repo(
        repo_url=record.repo_url,
        repo_name=record.name,
        stars=record.stars,
        description=record.description,
        category=record.category,
        framework=record.framework,
        last_updated=record.last_updated,
        metadata={"source": "custom_github"},
    )
    return record


def _build_conversation_context(
    normalized_compact: str,
    *,
    current: AppConfig | None,
    config_dir: Path | None,
    system_probe: SystemProbe,
    recent_turns: tuple[ConversationTurn, ...],
    context_service: AgentContextService,
    followup_state: SupervisorFollowupState,
) -> ConversationContext:
    return _build_conversation_context_from_module(
        normalized_compact,
        current=current,
        config_dir=config_dir,
        system_probe=system_probe,
        recent_turns=recent_turns,
        context_service=context_service,
        followup_state=followup_state,
        load_recommendation_records=_load_recommendation_records,
        resolve_active_repo=_resolve_active_repo,
        resolve_conversation_subject=_resolve_conversation_subject,
        recommendation_system_hint=_recommendation_system_hint,
        read_config_snapshot=read_config_snapshot,
        build_conversation_thread_state=_build_conversation_thread_state,
        build_live_thread_view=build_live_thread_view,
        build_pending_offer_view=build_pending_offer_view,
        build_durable_recommendation_view=build_durable_recommendation_view,
        build_durable_repo_memory_view=build_durable_repo_memory_view,
        initialize_state_store=initialize_state_store,
        context_factory=ConversationContext,
        machine_explanation=_machine_explanation,
    )


def _build_conversation_thread_state(
    *,
    followup_state: SupervisorFollowupState,
    recommendation_context: dict[str, object] | None,
    mentioned_repo: RepoCatalogRecord | None,
    active_repo: RepoCatalogRecord | None,
    execution_target: str,
    active_vm_name: str | None,
) -> ConversationThreadState:
    return _build_conversation_thread_state_from_module(
        followup_state=followup_state,
        recommendation_context=recommendation_context,
        mentioned_repo=mentioned_repo,
        active_repo=active_repo,
        execution_target=execution_target,
        active_vm_name=active_vm_name,
        thread_state_factory=ConversationThreadState,
        shortlist_state_factory=RecommendationShortlistState,
    )


def _resolve_mentioned_repo(
    normalized_compact: str,
    records: tuple[RepoCatalogRecord, ...],
    recent_turns: tuple[ConversationTurn, ...],
) -> RepoCatalogRecord | None:
    explicit_url = _extract_github_repo_url(normalized_compact)
    if explicit_url is not None:
        for record in records:
            if record.repo_url.lower() == explicit_url.lower():
                return record
    return _resolve_mentioned_repo_route(normalized_compact, records, recent_turns)


def _resolve_conversation_subject(
    normalized_compact: str,
    records: tuple[RepoCatalogRecord, ...],
    recent_turns: tuple[ConversationTurn, ...],
    *,
    active_repo: RepoCatalogRecord | None,
    workflow_state,
    followup_state: SupervisorFollowupState,
    recommendation_context: dict[str, object] | None,
) -> ResolvedConversationSubject:
    return _resolve_conversation_subject_from_module(
        normalized_compact,
        records,
        recent_turns,
        active_repo=active_repo,
        workflow_state=workflow_state,
        followup_state=followup_state,
        recommendation_context=recommendation_context,
        resolved_subject_factory=ResolvedConversationSubject,
        looks_like_social_or_identity_turn=_looks_like_social_or_identity_turn,
        resolve_mentioned_repo=_resolve_mentioned_repo,
        parse_structured_intent=_parse_structured_intent,
        resolve_repo_name_hint=_resolve_repo_name_hint,
        acceptance_phrase=_acceptance_phrase,
        thread_ids_match=_thread_ids_match,
        repo_from_followup_state=_repo_from_followup_state,
        repo_from_workflow_state=_repo_from_workflow_state,
        looks_like_repo_pronoun_followup=_looks_like_repo_pronoun_followup,
        repo_from_recommendation_context=_repo_from_recommendation_context,
    )


def _resolve_active_repo(
    config_dir: Path | None,
    records: tuple[RepoCatalogRecord, ...],
) -> RepoCatalogRecord | None:
    return _resolve_active_repo_from_module(
        config_dir,
        records,
        initialize_state_store=initialize_state_store,
    )


def _repo_status_record(
    config_dir: Path | None,
    repo: RepoCatalogRecord | None,
) -> dict[str, object] | None:
    return _repo_status_record_from_module(
        config_dir,
        repo,
        initialize_state_store=initialize_state_store,
        repo_name_from_state=_repo_name_from_state,
    )


def _repo_name_from_state(row) -> str:
    return _repo_name_from_state_route(row)


def _repo_alias_map(records: tuple[RepoCatalogRecord, ...]) -> dict[str, RepoCatalogRecord]:
    return _repo_alias_map_from_route(records)


def _repo_from_followup_state(
    records: tuple[RepoCatalogRecord, ...],
    followup_state: SupervisorFollowupState,
) -> RepoCatalogRecord | None:
    return _repo_from_followup_state_route(records, followup_state)


def _repo_from_recommendation_context(
    records: tuple[RepoCatalogRecord, ...],
    recommendation_context: dict[str, object] | None,
) -> RepoCatalogRecord | None:
    return _repo_from_recommendation_context_route(records, recommendation_context)


def _repo_from_workflow_state(
    records: tuple[RepoCatalogRecord, ...],
    workflow_state,
) -> RepoCatalogRecord | None:
    return _repo_from_workflow_state_route(records, workflow_state)


def _build_system_summary_reply(system_probe: SystemProbe) -> SystemSummaryReply:
    compute_path = "CUDA-capable" if system_probe.gpu.cuda_available else "MPS-capable" if (system_probe.gpu.mps_available or system_probe.gpu.mps_capable) else "CPU-only"
    cpu_text = f"{system_probe.cpu_logical_cores} logical CPU cores" if system_probe.cpu_logical_cores is not None else "unknown CPU core count"
    ram_text = f"{_format_gib(system_probe.ram_bytes)} GiB RAM" if system_probe.ram_bytes is not None else "unknown RAM"
    disk_text = f"{_format_gib(system_probe.disk_free_bytes)} GiB free disk" if system_probe.disk_free_bytes is not None else "unknown free disk"
    if compute_path == "CUDA-capable":
        practical = "This machine can support heavier GPU-aware repos if the rest of the environment is clean."
    elif compute_path == "MPS-capable":
        practical = "This machine is a better fit for CPU-safe or MPS-safe repos than CUDA-heavy ones."
    else:
        practical = "This machine is better suited to lighter CPU-first repos unless a repo has a very small workload."
    summary = (
        f"Duckln detects {system_probe.operating_system} on {system_probe.architecture} with {cpu_text}, {ram_text}, and {disk_text}. "
        f"Your compute path is {compute_path}. {practical}"
    )
    return SystemSummaryReply(summary=summary)


def _next_action_for_reply(reply: FreeTextReply) -> str | None:
    if reply.intent == "repo_capability_coverage":
        return "recommend_best_fit"
    if reply.intent.startswith("repo_recommendation"):
        return "inspect_requirements"
    if reply.intent == "repo_requirements":
        return "set_up_repo"
    if reply.intent == "repo_fit_judgment":
        return "inspect_requirements"
    if reply.intent == "repo_run":
        return "run_repo"
    if reply.intent == "repo_verify":
        return "verify_repo"
    return None


def _offer_kind_for_reply(reply: FreeTextReply) -> str | None:
    if reply.pending_offer_kind:
        return reply.pending_offer_kind
    return _next_action_for_reply(reply)


def _offer_label_for_reply(reply: FreeTextReply) -> str | None:
    if reply.pending_offer_label:
        return reply.pending_offer_label
    return {
        "recommend_best_fit": "pick the best repo for this machine",
        "inspect_requirements": "inspect requirements",
        "set_up_repo": "set up the repo",
        "run_repo": "run the repo",
        "verify_repo": "verify the repo",
        "explain_rationale": "explain the recommendation",
    }.get(_offer_kind_for_reply(reply))


def _active_topic_for_reply(reply: FreeTextReply) -> str:
    if reply.intent.startswith("repo_recommendation") or reply.intent == "repo_alternatives":
        return "repo_recommendation_single"
    if reply.intent in {"repo_overview", "repo_inventory", "repo_status", "repo_verify", "repo_memory_meta", "repo_active_explanation", "next_step_guidance", "system_verify"}:
        return reply.intent
    if reply.route_family is not None:
        return reply.route_family
    return reply.intent


def _pending_question_type_for_reply(reply: FreeTextReply) -> str | None:
    if reply.intent.startswith("repo_recommendation"):
        return "repo"
    if reply.intent in {"repo_requirements", "repo_fit_judgment", "repo_status", "repo_verify", "repo_run", "system_verify"}:
        return reply.intent
    return None


def _resolve_reply_recommendation_repo(
    records: tuple[RepoCatalogRecord, ...],
    reply: FreeTextReply,
) -> RepoCatalogRecord | None:
    if not reply.recommendation_repo_name and not reply.recommendation_repo_key:
        return None
    wanted_key = (reply.recommendation_repo_key or "").lower()
    wanted_name = (reply.recommendation_repo_name or "").lower()
    for record in records:
        if wanted_name and record.name.lower() == wanted_name:
            return record
        if wanted_key and (record.repo_url.lower() == wanted_key or record.name.lower() == wanted_key):
            return record
    return None


def _repo_requirement_reply(
    knowledge: RepoKnowledgeContext,
    system_probe: SystemProbe,
    *,
    recent_replies: tuple[FreeTextReply, ...] = (),
) -> str:
    record = knowledge.repo
    profiles = [profile for profile in (knowledge.cpu_profile, knowledge.ram_profile, knowledge.gpu_profile) if profile]
    profile_text = "; ".join(profiles) if profiles else "bounded from current repo knowledge"
    tools_text = ", ".join(knowledge.required_tools)
    hardware = build_hardware_reasoning(system_probe=system_probe, repo_knowledge=knowledge)
    feasibility = build_repo_feasibility_assessment(system_probe=system_probe, repo=record, repo_knowledge=knowledge)

    total_ram_text = f"{_format_gib(system_probe.ram_bytes)} GiB total RAM" if system_probe.ram_bytes is not None else "unknown total RAM"
    cpu_text = f"{system_probe.cpu_logical_cores} logical CPU cores" if system_probe.cpu_logical_cores is not None else "unknown CPU cores"
    usable_ram_text = (
        f"about {hardware.usable_ram_gib:.1f} GiB usable after system overhead"
        if hardware.usable_ram_gib is not None
        else "usable RAM is bounded because the probe is partial"
    )
    comfort_text = ""
    if hardware.minimum_recommended_ram_gib is not None and hardware.comfortable_ram_gib is not None:
        comfort_text = (
            f" It starts to get tight below about {hardware.minimum_recommended_ram_gib:.0f} GiB and feels healthier closer to {hardware.comfortable_ram_gib:.0f} GiB."
        )
    failure_text = ""
    if feasibility.failure_modes:
        failure_text = f" The main pain points are {', '.join(feasibility.failure_modes[:3])}."
    next_step = (
        "I can compare this with one lighter alternative next."
        if feasibility.fit_label != "comfortable"
        else "I can take this into /repos and set it up with the smallest verifiable path."
    )
    trust_text = _natural_trust_note(feasibility=feasibility, repo_knowledge=knowledge) if _should_surface_trust_note(
        recent_replies=recent_replies,
        feasibility=feasibility,
        repo_knowledge=knowledge,
    ) else ""
    recent_requirement_context = _restatement_budget_exhausted(
        recent_replies,
        route_families=("repo_recommendation_single", "repo_recommendation_compare", "repo_requirements", "repo_fit"),
    )

    if recent_requirement_context:
        options = (
            f"For {record.name}, the tight part is memory, not CPU. Your compute path is {hardware.compute_path}, and after system overhead you have {usable_ram_text}. This repo looks closer to {profile_text}.{comfort_text}{failure_text} {_fit_label_sentence(feasibility.fit_label)} {next_step}{trust_text}",
            f"{record.name} still looks {_fit_label_text(feasibility.fit_label)} here. Your compute path is {hardware.compute_path}, and you have {usable_ram_text}, while the repo looks more like {profile_text}.{comfort_text}{failure_text} {next_step}{trust_text}",
        )
        return _pick_text_option(options, recent_replies)

    tools_sentence = f" You'd still want {tools_text}." if tools_text else ""
    options = (
        f"On your machine, {record.name} has {cpu_text}, {total_ram_text}, and {usable_ram_text} to work with. Your compute path is {hardware.compute_path}. The repo itself looks more like {profile_text}.{comfort_text}{tools_sentence}{failure_text} {_fit_label_sentence(feasibility.fit_label)} {next_step}{trust_text}",
        f"{record.name} is mostly a memory question on this machine. You have {cpu_text}, {total_ram_text}, {usable_ram_text}, and a {hardware.compute_path} compute path. The repo looks closer to {profile_text}.{comfort_text}{tools_sentence}{failure_text} {_fit_label_sentence(feasibility.fit_label)} {next_step}{trust_text}",
        f"For {record.name}, your system has {cpu_text}, {total_ram_text}, {usable_ram_text}, and a {hardware.compute_path} compute path. The repo looks more like {profile_text}.{comfort_text}{tools_sentence}{failure_text} {_fit_label_sentence(feasibility.fit_label)} {next_step}{trust_text}",
    )
    return _pick_text_option(options, recent_replies)
