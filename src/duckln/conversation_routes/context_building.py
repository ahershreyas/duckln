"""Conversation context assembly helpers extracted from the supervisor."""

from __future__ import annotations

from duckln.conversation_routes.intent_schema import parse_structured_intent
from state.repo_catalog import RepoCatalogRecord


def build_conversation_context(
    normalized_compact: str,
    *,
    current,
    config_dir,
    system_probe,
    recent_turns,
    context_service,
    followup_state,
    load_recommendation_records,
    resolve_active_repo,
    resolve_conversation_subject,
    recommendation_system_hint,
    read_config_snapshot,
    build_conversation_thread_state,
    build_live_thread_view,
    build_pending_offer_view,
    build_durable_recommendation_view,
    build_durable_repo_memory_view,
    initialize_state_store,
    context_factory,
    machine_explanation,
):
    structured = parse_structured_intent(normalized_compact)
    records = load_recommendation_records(config_dir)
    active_repo = resolve_active_repo(config_dir, records)
    active_repo_snapshot = context_service.load_active_repo_snapshot(config_dir=config_dir)
    workflow_state = context_service.load_workflow_state(
        config_dir=config_dir,
        active_repo_snapshot=active_repo_snapshot,
    )
    recommendation_context = context_service.load_recommendation_memory(config_dir=config_dir)
    subject = resolve_conversation_subject(
        normalized_compact,
        records,
        recent_turns,
        active_repo=active_repo,
        workflow_state=workflow_state,
        followup_state=followup_state,
        recommendation_context=recommendation_context if config_dir is not None else None,
    )
    mentioned_repo = subject.repo
    if mentioned_repo is None and config_dir is not None:
        repo_name_hint = str(getattr(structured, "repo_name_hint", None) or "").strip()
        if repo_name_hint:
            wanted = repo_name_hint.lower()
            for row in initialize_state_store(config_dir).list_repo_states():
                row_name = str(row.metadata.get("repo_name") or "").strip()
                if row_name.lower() != wanted:
                    continue
                mentioned_repo = RepoCatalogRecord(
                    name=row_name,
                    repo_url=str(row.repo_url or row.repo_key or "").strip(),
                    stars=0,
                    description=row.summary or "",
                    category="Tracked",
                    framework="Unknown",
                    last_updated="",
                )
                break
    if (
        mentioned_repo is not None
        and active_repo is not None
        and not mentioned_repo.repo_url
        and mentioned_repo.name.lower() == active_repo.name.lower()
    ):
        mentioned_repo = active_repo
    platform_hint = recommendation_system_hint(system_probe)
    config_snapshot = {} if config_dir is None else read_config_snapshot(config_dir)
    execution_target = str(config_snapshot.get("execution_target") or followup_state.active_execution_target or "local")
    active_vm_name = str(config_snapshot.get("execution_vm_name") or followup_state.active_vm_name or "").strip() or None
    context_repo = mentioned_repo or active_repo
    agent_context = context_service.build_agent_context(
        current=current,
        system_probe=system_probe,
        config_dir=config_dir,
        repo=context_repo,
    )
    recommendation_memory = context_service.load_recommendation_memory(config_dir=config_dir)
    session_project_summary = context_service.load_session_project_summary(config_dir=config_dir)
    promoted_heuristics = context_service.load_promoted_heuristics(config_dir=config_dir)
    requested_target_name = str(getattr(structured, "target_name", None) or "").strip() or None
    repo_inventory_snapshot = context_service.load_repo_inventory_snapshot(config_dir=config_dir)
    vm_inventory_snapshot: tuple[dict[str, object], ...] = ()
    if config_dir is not None:
        try:
            store = initialize_state_store(config_dir)
            repo_counts: dict[str, int] = {}
            for row in store.list_repo_states():
                if row.execution_target != "vm":
                    continue
                vm_name = str(row.vm_name or "").strip()
                if vm_name:
                    repo_counts[vm_name] = repo_counts.get(vm_name, 0) + 1
            vm_inventory_snapshot = tuple(
                {
                    "name": row.vm_name,
                    "status": row.status,
                    "provider": row.provider,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "repo_count": repo_counts.get(row.vm_name, 0),
                }
                for row in store.list_vm_linkages()
            )
        except Exception:
            vm_inventory_snapshot = ()
    live_repo_sessions_snapshot = context_service.load_live_repo_sessions_snapshot(config_dir=config_dir)
    thread_state = build_conversation_thread_state(
        followup_state=followup_state,
        recommendation_context=recommendation_memory if config_dir is not None else None,
        mentioned_repo=mentioned_repo,
        active_repo=active_repo,
        execution_target=execution_target,
        active_vm_name=active_vm_name,
    )
    repo_state_target = "vm" if requested_target_name and "vm" in requested_target_name.casefold() else None
    repo_state_snapshot = context_service.load_repo_state_snapshot(
        config_dir=config_dir,
        repo=context_repo,
        execution_target=repo_state_target,
        vm_name=requested_target_name if repo_state_target == "vm" else None,
    )
    if repo_state_snapshot is None:
        repo_state_snapshot = context_service.load_repo_state_snapshot(config_dir=config_dir, repo=context_repo)
    live_thread = build_live_thread_view(
        followup_state=followup_state,
        execution_target=execution_target,
        active_vm_name=active_vm_name,
    )
    pending_offer = build_pending_offer_view(followup_state)
    durable_recommendation = build_durable_recommendation_view(
        followup_state=followup_state,
        recommendation_context=recommendation_memory,
        active_repo_name=active_repo.name if active_repo is not None else None,
    )
    has_learning_signal = False
    if config_dir is not None and context_repo is not None:
        repo_keys = {
            (context_repo.repo_url or "").strip().lower(),
            context_repo.name.strip().lower(),
        }
        for record in initialize_state_store(config_dir).list_learning_records():
            subject_key = str(record.subject_key or "").strip().lower()
            metadata_repo_name = str(record.metadata.get("repo_name") or "").strip().lower() if isinstance(record.metadata, dict) else ""
            if subject_key in repo_keys or metadata_repo_name in repo_keys:
                has_learning_signal = True
                break
    durable_repo_memory = None
    if context_repo is not None:
        repo_metadata = agent_context.repo_knowledge.metadata if agent_context.repo_knowledge is not None else {}
        durable_repo_memory = build_durable_repo_memory_view(
            repo=context_repo,
            status_record=(
                None
                if repo_state_snapshot is None
                else {
                    "repo_key": repo_state_snapshot.repo_key,
                    "name": repo_state_snapshot.repo_name,
                    "status": repo_state_snapshot.status,
                    "source_repo_url": repo_state_snapshot.source_repo_url
                    or (repo_metadata.get("source_repo_url") if isinstance(repo_metadata, dict) else None),
                    "install_location": repo_state_snapshot.install_location
                    or (repo_metadata.get("install_location") if isinstance(repo_metadata, dict) else None),
                    "execution_target": repo_state_snapshot.execution_target,
                    "vm_name": repo_state_snapshot.vm_name,
                    "docker_name": repo_state_snapshot.docker_name,
                    "cloud_vendor": repo_state_snapshot.cloud_vendor,
                    "cloud_region": repo_state_snapshot.cloud_region,
                    "access_hint": repo_state_snapshot.access_hint
                    or (repo_metadata.get("access_hint") if isinstance(repo_metadata, dict) else None),
                    "run_command": repo_state_snapshot.run_command
                    or (repo_metadata.get("run_command") if isinstance(repo_metadata, dict) else None),
                    "verify_command": repo_state_snapshot.verify_command
                    or (repo_metadata.get("verify_command") if isinstance(repo_metadata, dict) else None),
                    "manual_command": repo_state_snapshot.manual_command
                    or (repo_metadata.get("manual_command") if isinstance(repo_metadata, dict) else None),
                    "runtime_kind": repo_state_snapshot.runtime_kind
                    or (repo_metadata.get("runtime_kind") if isinstance(repo_metadata, dict) else None),
                    "removal_hint": repo_state_snapshot.removal_hint
                    or (repo_metadata.get("removal_hint") if isinstance(repo_metadata, dict) else None),
                    "last_run_result": repo_state_snapshot.last_run_result,
                    "last_blocker": repo_state_snapshot.last_blocker,
                }
            ),
            has_repo_knowledge=agent_context.repo_knowledge is not None,
            recommendation_context=recommendation_memory,
            has_learning_signal=has_learning_signal,
        )
    return context_factory(
        records=records,
        mentioned_repo=mentioned_repo,
        active_repo=active_repo,
        platform_hint=platform_hint,
        machine_explanation=machine_explanation(system_probe),
        execution_target=execution_target,
        active_vm_name=active_vm_name,
        requested_target_name=requested_target_name,
        vm_inventory_snapshot=vm_inventory_snapshot,
        agent_context=agent_context,
        repo_knowledge=agent_context.repo_knowledge,
        repo_state_snapshot=repo_state_snapshot,
        active_repo_snapshot=active_repo_snapshot,
        repo_inventory_snapshot=repo_inventory_snapshot,
        live_repo_sessions_snapshot=live_repo_sessions_snapshot,
        workflow_state=workflow_state,
        recommendation_memory=recommendation_memory,
        recent_thread_summary=session_project_summary.session_summary,
        project_summary=session_project_summary.project_summary,
        promoted_heuristics=promoted_heuristics,
        followup_state=followup_state,
        thread_state=thread_state,
        live_thread=live_thread,
        pending_offer=pending_offer,
        durable_recommendation=durable_recommendation,
        durable_repo_memory=durable_repo_memory,
    )


def build_conversation_thread_state(
    *,
    followup_state,
    recommendation_context,
    mentioned_repo,
    active_repo,
    execution_target: str,
    active_vm_name: str | None,
    thread_state_factory,
    shortlist_state_factory,
):
    recommendation_context = recommendation_context or {}
    active_repo_name = (
        (mentioned_repo.name if mentioned_repo is not None else None)
        or (active_repo.name if active_repo is not None else None)
        or (followup_state.pending_repo_name or None)
        or (followup_state.last_discussed_repo_name or None)
        or (str(recommendation_context.get("repo_name") or "").strip() or None)
    )
    active_repo_key = (
        (mentioned_repo.repo_url if mentioned_repo is not None else None)
        or (active_repo.repo_url if active_repo is not None else None)
        or followup_state.pending_repo_key
        or followup_state.last_discussed_repo_key
    )
    shortlist_names = tuple(
        name
        for name in (
            followup_state.shortlist_repo_names
            or (
                tuple(str(item) for item in recommendation_context.get("alternatives", ()) if str(item).strip())
                if recommendation_context
                else ()
            )
            or followup_state.last_recommendation_alternatives
            or ()
        )
        if name
    )
    primary_repo = followup_state.shortlist_primary_repo or active_repo_name
    secondary_repo = followup_state.shortlist_secondary_repo or (shortlist_names[0] if shortlist_names else None)
    return thread_state_factory(
        active_topic=followup_state.active_topic or followup_state.last_route_family,
        active_repo_name=active_repo_name,
        active_repo_key=active_repo_key,
        execution_target=execution_target,
        active_vm_name=active_vm_name,
        shortlist=shortlist_state_factory(
            repo_names=shortlist_names,
            primary_repo=primary_repo,
            secondary_repo=secondary_repo,
        ),
        pending_offer_kind=followup_state.pending_offer_kind,
        pending_offer_label=followup_state.pending_offer_label,
        pending_offer_repo_name=followup_state.pending_offer_repo_name,
        pending_offer_id=followup_state.pending_offer_id,
        pending_offer_thread_id=followup_state.pending_offer_thread_id,
        pending_offer_expires_at=followup_state.pending_offer_expires_at,
        active_thread_id=followup_state.active_thread_id,
        active_thread_expires_at=followup_state.active_thread_expires_at,
        active_session_id=followup_state.active_session_id,
        last_slash_runtime_action=followup_state.last_slash_runtime_action,
        last_next_step_offered=followup_state.last_next_step_offered,
        last_render_fingerprint=followup_state.last_render_fingerprint,
    )
