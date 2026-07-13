"""Separated runtime state views for live threads, offers, and durable repo memory."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingOfferView:
    """Live pending-offer state only."""

    kind: str | None = None
    label: str | None = None
    repo_name: str | None = None
    offer_id: str | None = None
    thread_id: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class LiveThreadView:
    """Current live-thread state only."""

    topic: str | None = None
    thread_id: str | None = None
    expires_at: str | None = None
    session_id: str | None = None
    execution_target: str = "local"
    active_vm_name: str | None = None
    last_slash_runtime_action: str | None = None
    last_next_step_offered: str | None = None
    last_render_fingerprint: str | None = None


@dataclass(frozen=True)
class DurableRecommendationView:
    """Durable recommendation memory separate from the live thread."""

    primary_repo: str | None = None
    secondary_repo: str | None = None
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class DurableRepoMemoryView:
    """Durable repo lifecycle and knowledge state."""

    repo_name: str | None = None
    repo_key: str | None = None
    tracked_status: str | None = None
    source_repo_url: str | None = None
    install_location: str | None = None
    execution_target: str = "local"
    vm_name: str | None = None
    docker_name: str | None = None
    cloud_vendor: str | None = None
    cloud_region: str | None = None
    access_hint: str | None = None
    run_command: str | None = None
    verify_command: str | None = None
    manual_command: str | None = None
    runtime_kind: str | None = None
    removal_hint: str | None = None
    last_run_result: str | None = None
    last_blocker: str | None = None
    has_repo_knowledge: bool = False
    in_recommendation_memory: bool = False
    has_learning_signal: bool = False


def build_pending_offer_view(followup_state) -> PendingOfferView:
    """Extract only pending-offer fields from follow-up state."""

    return PendingOfferView(
        kind=followup_state.pending_offer_kind,
        label=followup_state.pending_offer_label,
        repo_name=followup_state.pending_offer_repo_name,
        offer_id=followup_state.pending_offer_id,
        thread_id=followup_state.pending_offer_thread_id,
        expires_at=followup_state.pending_offer_expires_at,
    )


def build_live_thread_view(
    *,
    followup_state,
    execution_target: str,
    active_vm_name: str | None,
) -> LiveThreadView:
    """Extract only active-thread fields from follow-up state."""

    return LiveThreadView(
        topic=followup_state.active_topic or followup_state.last_route_family,
        thread_id=followup_state.active_thread_id,
        expires_at=followup_state.active_thread_expires_at,
        session_id=followup_state.active_session_id,
        execution_target=execution_target,
        active_vm_name=active_vm_name,
        last_slash_runtime_action=followup_state.last_slash_runtime_action,
        last_next_step_offered=followup_state.last_next_step_offered,
        last_render_fingerprint=followup_state.last_render_fingerprint,
    )


def build_durable_recommendation_view(
    *,
    followup_state,
    recommendation_context: dict[str, object] | None,
    active_repo_name: str | None,
) -> DurableRecommendationView:
    """Extract durable recommendation memory separately from the live thread."""

    recommendation_context = recommendation_context or {}
    alternatives = tuple(
        name
        for name in (
            followup_state.shortlist_repo_names
            or tuple(str(item) for item in recommendation_context.get("alternatives", ()) if str(item).strip())
            or followup_state.last_recommendation_alternatives
            or ()
        )
        if name
    )
    return DurableRecommendationView(
        primary_repo=followup_state.shortlist_primary_repo or active_repo_name,
        secondary_repo=followup_state.shortlist_secondary_repo or (alternatives[0] if alternatives else None),
        alternatives=alternatives,
    )


def build_durable_repo_memory_view(
    *,
    repo,
    status_record: dict[str, object] | None,
    has_repo_knowledge: bool,
    recommendation_context: dict[str, object] | None,
    has_learning_signal: bool,
) -> DurableRepoMemoryView:
    """Build durable repo memory from lifecycle state, repo knowledge, and learning signals."""

    recommendation_context = recommendation_context or {}
    repo_name = repo.name if repo is not None else None
    repo_key = (repo.repo_url or repo.name) if repo is not None else None
    in_recommendation_memory = False
    if repo_name is not None:
        in_recommendation_memory = str(recommendation_context.get("repo_name") or "").strip().lower() == repo_name.lower()
    source_repo_url = None
    install_location = None
    execution_target = "local"
    vm_name = None
    docker_name = None
    cloud_vendor = None
    cloud_region = None
    tracked_status = None
    access_hint = None
    run_command = None
    verify_command = None
    manual_command = None
    runtime_kind = None
    removal_hint = None
    last_run_result = None
    last_blocker = None
    if status_record is not None:
        state_repo_key = status_record.get("repo_key")
        if isinstance(state_repo_key, str) and state_repo_key.strip():
            repo_key = state_repo_key.strip()
        source_value = status_record.get("source_repo_url")
        if isinstance(source_value, str) and source_value.strip() and not source_value.strip().startswith("linked://"):
            source_repo_url = source_value.strip()
    if source_repo_url is None and repo is not None:
        repo_url = str(repo.repo_url or "").strip()
        if repo_url and not repo_url.startswith("linked://"):
            source_repo_url = repo_url
    if status_record is not None:
        tracked_status = str(status_record.get("status") or "").strip() or None
        execution_target_value = status_record.get("execution_target")
        if isinstance(execution_target_value, str) and execution_target_value.strip():
            execution_target = execution_target_value.strip()
        vm_name_value = status_record.get("vm_name")
        if isinstance(vm_name_value, str) and vm_name_value.strip():
            vm_name = vm_name_value.strip()
        docker_name_value = status_record.get("docker_name")
        if isinstance(docker_name_value, str) and docker_name_value.strip():
            docker_name = docker_name_value.strip()
        cloud_vendor_value = status_record.get("cloud_vendor")
        if isinstance(cloud_vendor_value, str) and cloud_vendor_value.strip():
            cloud_vendor = cloud_vendor_value.strip()
        cloud_region_value = status_record.get("cloud_region")
        if isinstance(cloud_region_value, str) and cloud_region_value.strip():
            cloud_region = cloud_region_value.strip()
        location = status_record.get("install_location")
        if isinstance(location, str) and location.strip():
            install_location = location.strip()
        access_value = status_record.get("access_hint")
        if isinstance(access_value, str) and access_value.strip():
            access_hint = access_value.strip()
        run_value = status_record.get("run_command")
        if isinstance(run_value, str) and run_value.strip():
            run_command = run_value.strip()
        verify_value = status_record.get("verify_command")
        if isinstance(verify_value, str) and verify_value.strip():
            verify_command = verify_value.strip()
        manual_value = status_record.get("manual_command")
        if isinstance(manual_value, str) and manual_value.strip():
            manual_command = manual_value.strip()
        runtime_kind_value = status_record.get("runtime_kind")
        if isinstance(runtime_kind_value, str) and runtime_kind_value.strip():
            runtime_kind = runtime_kind_value.strip()
        removal_value = status_record.get("removal_hint")
        if isinstance(removal_value, str) and removal_value.strip():
            removal_hint = removal_value.strip()
        last_run_result_value = status_record.get("last_run_result")
        if isinstance(last_run_result_value, str) and last_run_result_value.strip():
            last_run_result = last_run_result_value.strip()
        blocker_value = status_record.get("last_blocker")
        if isinstance(blocker_value, str) and blocker_value.strip():
            last_blocker = blocker_value.strip()
    return DurableRepoMemoryView(
        repo_name=repo_name,
        repo_key=repo_key,
        tracked_status=tracked_status,
        source_repo_url=source_repo_url,
        install_location=install_location,
        execution_target=execution_target,
        vm_name=vm_name,
        docker_name=docker_name,
        cloud_vendor=cloud_vendor,
        cloud_region=cloud_region,
        access_hint=access_hint,
        run_command=run_command,
        verify_command=verify_command,
        manual_command=manual_command,
        runtime_kind=runtime_kind,
        removal_hint=removal_hint,
        last_run_result=last_run_result,
        last_blocker=last_blocker,
        has_repo_knowledge=has_repo_knowledge,
        in_recommendation_memory=in_recommendation_memory,
        has_learning_signal=has_learning_signal,
    )
