"""Supervisor persistence helpers extracted from conversation_agent."""

from __future__ import annotations


def persist_followup_state(
    *,
    reply,
    context,
    config_dir,
    read_config_snapshot,
    thread_id_for_reply,
    offer_id_for_reply,
    thread_expiry_for_reply,
    followup_expiry_for_reply,
    reply_has_repo_thread,
    resolve_reply_recommendation_repo,
    reply_fingerprint,
    next_action_for_reply,
    offer_kind_for_reply,
    offer_label_for_reply,
    pending_question_type_for_reply,
    clarification_option_labels_for_reply,
    build_system_summary_reply,
    verified_fact_summary,
    active_topic_for_reply,
    write_followup_state,
):
    if config_dir is None:
        return
    repo = None
    if reply_has_repo_thread(reply):
        repo = resolve_reply_recommendation_repo(context.records, reply) or context.mentioned_repo or context.active_repo
    session_snapshot = read_config_snapshot(config_dir, prefix="session.")
    active_session_id = str(session_snapshot.get("session.current_id") or "").strip() or None
    thread_id = thread_id_for_reply(
        has_repo_thread=reply_has_repo_thread(reply),
        intent=reply.intent,
        route_family=reply.route_family,
        repo_token=repo.repo_url if repo is not None else reply.recommendation_repo_key or reply.recommendation_repo_name,
        prior_thread_id=context.thread_state.active_thread_id,
    )
    offer_kind = offer_kind_for_reply(reply)
    next_action = next_action_for_reply(reply)
    offer_label = offer_label_for_reply(reply)
    offer_id = offer_id_for_reply(offer_kind=offer_kind, thread_id=thread_id)
    thread_expires_at = thread_expiry_for_reply(thread_id=thread_id)
    offer_expires_at = followup_expiry_for_reply(offer_id=offer_id)
    shortlist_names = tuple(
        name
        for name in (
            ((reply.recommendation_repo_name,) + tuple(reply.recommendation_alternatives))
            if reply.recommendation_repo_name
            else context.thread_state.shortlist.repo_names
        )
        if name
    )
    fingerprint = reply_fingerprint(
        route_family=reply.route_family,
        repo_name=repo.name if repo is not None else None,
        topic=reply.intent,
        action=next_action,
    )
    state = {
        "last_supervisor_decision": reply.intent,
        "last_route_family": reply.route_family,
        "last_route_confidence": reply.route_confidence,
        "last_response_contract": reply.response_contract_name,
        "active_topic": active_topic_for_reply(reply),
        "pending_repo_key": repo.repo_url if repo is not None and offer_kind else None,
        "pending_repo_name": repo.name if repo is not None and offer_kind else None,
        "pending_next_action": next_action,
        "pending_offer_kind": offer_kind,
        "pending_offer_label": offer_label,
        "pending_offer_repo_key": reply.pending_offer_repo_key or (repo.repo_url if repo is not None and offer_kind else None),
        "pending_offer_repo_name": reply.pending_offer_repo_name or (repo.name if repo is not None and offer_kind else None),
        "pending_offer_id": offer_id,
        "pending_offer_thread_id": thread_id if offer_id is not None else None,
        "pending_offer_expires_at": offer_expires_at,
        "pending_question_type": pending_question_type_for_reply(reply),
        "pending_subject_type": "repo" if repo is not None and offer_kind else None,
        "pending_clarification_options": clarification_option_labels_for_reply(reply),
        "last_detected_system_summary": build_system_summary_reply(context.agent_context.system_probe).summary,
        "last_recommendation_fit": reply.recommendation_fit_label or reply.hardware_summary or reply.recommendation_reason,
        "last_verified_fact_summary": verified_fact_summary(config_dir, repo),
        "last_answer_style": reply.answer_style,
        "last_discussed_repo_key": repo.repo_url if repo is not None and reply_has_repo_thread(reply) else None,
        "last_discussed_repo_name": repo.name if repo is not None and reply_has_repo_thread(reply) else None,
        "last_recommendation_alternatives": reply.recommendation_alternatives or (),
        "shortlist_repo_names": shortlist_names,
        "shortlist_primary_repo": reply.recommendation_repo_name or context.thread_state.shortlist.primary_repo,
        "shortlist_secondary_repo": (
            reply.recommendation_alternatives[0]
            if reply.recommendation_alternatives
            else context.thread_state.shortlist.secondary_repo
        ),
        "last_next_step_offered": offer_label,
        "last_render_fingerprint": fingerprint,
        "last_slash_runtime_action": context.thread_state.last_slash_runtime_action,
        "active_thread_id": thread_id,
        "active_thread_expires_at": thread_expires_at,
        "active_session_id": active_session_id,
        "active_execution_target": context.execution_target,
        "active_vm_name": context.active_vm_name,
        "last_plan_path": context.followup_state.last_plan_path,
    }
    write_followup_state(config_dir, state)


def persist_grounded_conversation_learning(
    message: str,
    reply,
    *,
    context,
    config_dir,
    intent: str,
    base_intent: str,
    recent_replies,
    context_service,
    resolve_reply_recommendation_repo,
    looks_like_repeated_deflection_breakout,
    phrase_learning_bucket,
):
    recommendation_repo = resolve_reply_recommendation_repo(context.records, reply)
    repo = recommendation_repo or context.mentioned_repo
    if config_dir is None:
        return
    if repo is None and intent not in {
        "greeting",
        "rapport",
        "capability",
        "feedback_style",
        "repo_capability_coverage",
        "system_summary",
        "system_capacity",
        "system_fit_context",
        "memory_meta",
    }:
        return
    corrected = intent != base_intent or looks_like_repeated_deflection_breakout(intent, recent_replies)
    signal = "corrected" if intent == "feedback_style" or corrected else "success"
    context_service.record_conversation_learning(
        config_dir=config_dir,
        intent=intent,
        message=message,
        reply_text=reply.text,
        signal=signal,
        repo=repo,
    )
    phrase_bucket = phrase_learning_bucket(message)
    if phrase_bucket is not None and corrected:
        context_service.record_conversation_learning(
            config_dir=config_dir,
            intent=intent,
            message=message,
            reply_text=reply.text,
            signal="corrected",
            repo=None,
            subject_key=f"phrase:{phrase_bucket}",
        )
    if repo is None or not (intent.startswith("repo_recommendation") or intent in {"repo_requirements", "repo_fit_judgment"}):
        return
    context_service.record_repo_learning(
        config_dir=config_dir,
        repo=repo,
        summary=reply.text,
        source=f"conversation-{intent}",
        metadata={"intent": intent},
    )
    if reply.recommendation_repo_name and reply.recommendation_reason:
        context_service.record_recommendation_context(
            config_dir=config_dir,
            repo=recommendation_repo or repo,
            reason=reply.recommendation_reason,
            alternatives=reply.recommendation_alternatives,
            caveat=reply.recommendation_caveat,
            system_hint=context.platform_hint,
        )
