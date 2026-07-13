"""Repo state/action reply ownership extracted from the conversation specialist."""

from __future__ import annotations

from typing import Any, Callable

from duckln.conversation_routes.repo_responses import (
    build_recommendation_followup_draft,
    build_repo_access_draft,
    build_repo_active_draft,
    build_repo_inventory_draft,
    build_repo_inventory_scope_draft,
    build_repo_lifecycle_active_draft,
    build_repo_lifecycle_inventory_draft,
    build_repo_lifecycle_status_draft,
    build_repo_live_sessions_draft,
    build_repo_location_draft,
    build_repo_logs_draft,
    build_repo_memory_meta_draft,
    build_repo_path_draft,
    build_repo_path_inventory_draft,
    build_repo_removal_draft,
    build_repo_restart_draft,
    build_repo_run_draft,
    build_repo_source_draft,
    build_repo_source_inventory_draft,
    build_repo_stop_draft,
    build_repo_status_draft,
    build_repo_verify_draft,
    build_slash_runtime_followup_draft,
)


def _inventory_target_label(item: Any) -> str:
    execution_target = getattr(item, "execution_target", "local") or "local"
    if execution_target == "vm":
        vm_name = getattr(item, "vm_name", None)
        return f"VM {vm_name}" if vm_name else "VM"
    if execution_target == "docker":
        docker_name = getattr(item, "docker_name", None)
        return f"Docker {docker_name}" if docker_name else "Docker"
    if execution_target in {"aws", "gcp"}:
        provider = str(getattr(item, "cloud_vendor", None) or execution_target).upper()
        cloud_region = getattr(item, "cloud_region", None)
        return f"{provider} {cloud_region}" if cloud_region else f"{provider} remote"
    return "local machine"


def _status_with_target(item: Any) -> str:
    status = getattr(item, "status", "unknown")
    target = _inventory_target_label(item)
    created = str(getattr(item, "created_at", None) or "").split("T")[0]
    verified = str(getattr(item, "last_verified_at", None) or "").split("T")[0]
    suffixes: list[str] = []
    if created:
        suffixes.append(f"created {created}")
    if verified:
        suffixes.append(f"verified {verified}")
    suffix = f" ({'; '.join(suffixes)})" if suffixes else ""
    return f"{status} on {target}{suffix}"


def _requested_target_name(context: Any) -> str | None:
    value = str(getattr(context, "requested_target_name", None) or "").strip()
    return value or None


def _matches_requested_target(item: Any, target_name: str | None) -> bool:
    if not target_name:
        return True
    wanted = target_name.casefold()
    candidates = (
        getattr(item, "vm_name", None),
        getattr(item, "docker_name", None),
        getattr(item, "cloud_vendor", None),
        getattr(item, "cloud_region", None),
    )
    return any(str(candidate or "").casefold() == wanted for candidate in candidates)


def build_repo_route_reply(
    *,
    intent: str,
    context: Any,
    reply_from_draft: Callable[..., Any],
    specialist_name: str,
) -> Any | None:
    repo = context.mentioned_repo or context.active_repo
    durable = context.durable_repo_memory
    active = context.active_repo_snapshot
    repo_knowledge = context.repo_knowledge
    metadata = repo_knowledge.metadata if repo_knowledge is not None else {}

    if intent == "repo_memory_meta":
        return reply_from_draft(
            build_repo_memory_meta_draft(
                repo_name=repo.name if repo is not None else None,
                tracked_status=durable.tracked_status if durable is not None else None,
                has_repo_knowledge=bool(durable and durable.has_repo_knowledge),
                in_recommendation_memory=bool(durable and durable.in_recommendation_memory),
                has_learning_signal=bool(durable and durable.has_learning_signal),
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_location":
        install_location = metadata.get("install_location") if isinstance(metadata, dict) else None
        environment_path = metadata.get("environment_path") if isinstance(metadata, dict) else None
        return reply_from_draft(
            build_repo_location_draft(
                repo_name=repo_knowledge.repo.name if repo_knowledge is not None else None,
                install_location=install_location if isinstance(install_location, str) and install_location else None,
                environment_path=environment_path if isinstance(environment_path, str) and environment_path else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_access":
        run_command = durable.run_command if durable is not None else metadata.get("run_command") if isinstance(metadata, dict) else None
        access_hint = durable.access_hint if durable is not None else metadata.get("access_hint") if isinstance(metadata, dict) else None
        workflow_state = context.workflow_state
        return reply_from_draft(
            build_repo_access_draft(
                repo_name=repo_knowledge.repo.name if repo_knowledge is not None else None,
                repo_key=durable.repo_key if durable is not None else (repo.repo_url or repo.name) if repo is not None else None,
                run_command=run_command if isinstance(run_command, str) and run_command else None,
                access_hint=access_hint if isinstance(access_hint, str) and access_hint else None,
                first_entrypoint=repo_knowledge.entrypoints[0] if repo_knowledge is not None and repo_knowledge.entrypoints else None,
                install_location=durable.install_location if durable is not None else None,
                execution_target=durable.execution_target if durable is not None else context.execution_target,
                vm_name=durable.vm_name if durable is not None else context.active_vm_name,
                docker_name=durable.docker_name if durable is not None else None,
                cloud_vendor=durable.cloud_vendor if durable is not None else None,
                cloud_region=durable.cloud_region if durable is not None else None,
                runtime_status=(
                    workflow_state.active_runtime_status
                    if workflow_state is not None
                    and repo is not None
                    and workflow_state.active_runtime_repo_key in {repo.repo_url, repo.name}
                    else None
                ),
                active_attach_hint=(
                    workflow_state.active_runtime_attach_hint
                    if workflow_state is not None
                    and repo is not None
                    and workflow_state.active_runtime_repo_key in {repo.repo_url, repo.name}
                    else None
                ),
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_removal":
        removal_hint = durable.removal_hint if durable is not None else metadata.get("removal_hint") if isinstance(metadata, dict) else None
        return reply_from_draft(
            build_repo_removal_draft(
                repo_name=repo.name if repo is not None else None,
                repo_key=durable.repo_key if durable is not None else (repo.repo_url or repo.name) if repo is not None else None,
                removal_hint=removal_hint if isinstance(removal_hint, str) and removal_hint else None,
                tracked_status=durable.tracked_status if durable is not None else None,
                install_location=durable.install_location if durable is not None else None,
                execution_target=(
                    durable.execution_target
                    if durable is not None
                    else active.execution_target
                    if active is not None
                    else context.execution_target
                ),
                vm_name=durable.vm_name if durable is not None else active.vm_name if active is not None else context.active_vm_name,
                docker_name=durable.docker_name if durable is not None else None,
                cloud_vendor=durable.cloud_vendor if durable is not None else None,
                cloud_region=durable.cloud_region if durable is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_path_show":
        install_location = durable.install_location if durable is not None else active.install_location if active is not None else None
        execution_target = (
            durable.execution_target
            if durable is not None
            else active.execution_target
            if active is not None
            else context.execution_target
        )
        vm_name = durable.vm_name if durable is not None else active.vm_name if active is not None else context.active_vm_name
        return reply_from_draft(
            build_repo_path_draft(
                repo_name=repo.name if repo is not None else active.repo_name if active is not None else None,
                install_location=install_location,
                execution_target=execution_target,
                vm_name=vm_name,
                docker_name=durable.docker_name if durable is not None else None,
                cloud_vendor=durable.cloud_vendor if durable is not None else None,
                cloud_region=durable.cloud_region if durable is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_source_show":
        source_repo_url = durable.source_repo_url if durable is not None else active.source_repo_url if active is not None else None
        return reply_from_draft(
            build_repo_source_draft(
                repo_name=repo.name if repo is not None else active.repo_name if active is not None else None,
                source_repo_url=source_repo_url,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_path_inventory":
        inventory = []
        for item in context.repo_inventory_snapshot:
            target_text = "local machine"
            if item.execution_target == "vm":
                target_text = f"VM {item.vm_name}" if item.vm_name else "the active VM"
            elif item.execution_target == "docker":
                target_text = f"Docker container {item.docker_name}" if item.docker_name else "the active Docker target"
            elif item.execution_target in {"aws", "gcp"}:
                provider = item.cloud_vendor or item.execution_target.upper()
                target_text = f"{provider} in {item.cloud_region}" if item.cloud_region else provider
            location = item.install_location or f"no stored path on the {target_text}"
            if item.install_location:
                location = f"{item.install_location} ({target_text})"
            inventory.append({"name": item.repo_name, "location": location})
        return reply_from_draft(
            build_repo_path_inventory_draft(inventory=inventory),
            specialist_name=specialist_name,
        )

    if intent == "repo_source_inventory":
        inventory = [
            {"name": item.repo_name, "source": item.source_repo_url or "no public source URL recorded"}
            for item in context.repo_inventory_snapshot
        ]
        return reply_from_draft(
            build_repo_source_inventory_draft(inventory=inventory),
            specialist_name=specialist_name,
        )

    if intent == "repo_inventory":
        inventory = [{"name": item.repo_name, "status": _status_with_target(item)} for item in context.repo_inventory_snapshot]
        return reply_from_draft(build_repo_inventory_draft(inventory=inventory), specialist_name=specialist_name)

    if intent == "repo_inventory_local":
        inventory = [
            {"name": item.repo_name, "status": _status_with_target(item)}
            for item in context.repo_inventory_snapshot
            if item.execution_target == "local"
        ]
        return reply_from_draft(
            build_repo_inventory_scope_draft(inventory=inventory, scope_label="local", intent="repo_inventory_local"),
            specialist_name=specialist_name,
        )

    if intent == "repo_live_sessions":
        sessions = [
            {
                "name": item.repo_name,
                "status": item.status,
                "target": item.execution_target,
                "location": item.install_location or item.attach_hint or "no stored attach path",
            }
            for item in context.live_repo_sessions_snapshot
        ]
        return reply_from_draft(
            build_repo_live_sessions_draft(sessions=sessions, intent="repo_live_sessions"),
            specialist_name=specialist_name,
        )

    if intent == "repo_live_sessions_local":
        sessions = [
            {
                "name": item.repo_name,
                "status": item.status,
                "target": item.execution_target,
                "location": item.install_location or item.attach_hint or "no stored attach path",
            }
            for item in context.live_repo_sessions_snapshot
            if item.execution_target == "local"
        ]
        return reply_from_draft(
            build_repo_live_sessions_draft(
                sessions=sessions,
                intent="repo_live_sessions_local",
                scope_label="local machine",
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_live_sessions_vm":
        sessions = [
            {
                "name": item.repo_name,
                "status": item.status,
                "target": item.vm_name or item.execution_target,
                "location": item.install_location or item.attach_hint or "no stored attach path",
            }
            for item in context.live_repo_sessions_snapshot
            if item.execution_target == "vm"
        ]
        return reply_from_draft(
            build_repo_live_sessions_draft(
                sessions=sessions,
                intent="repo_live_sessions_vm",
                scope_label="VM",
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_live_sessions_cloud":
        sessions = [
            {
                "name": item.repo_name,
                "status": item.status,
                "target": item.cloud_vendor or item.execution_target,
                "location": item.install_location or item.attach_hint or "no stored attach path",
            }
            for item in context.live_repo_sessions_snapshot
            if item.execution_target in {"aws", "gcp"}
        ]
        return reply_from_draft(
            build_repo_live_sessions_draft(
                sessions=sessions,
                intent="repo_live_sessions_cloud",
                scope_label="cloud targets",
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_live_sessions_docker":
        sessions = [
            {
                "name": item.repo_name,
                "status": item.status,
                "target": item.docker_name or item.execution_target,
                "location": item.install_location or item.attach_hint or "no stored attach path",
            }
            for item in context.live_repo_sessions_snapshot
            if item.execution_target == "docker"
        ]
        return reply_from_draft(
            build_repo_live_sessions_draft(
                sessions=sessions,
                intent="repo_live_sessions_docker",
                scope_label="Docker",
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_inventory_vm":
        requested_target = _requested_target_name(context)
        inventory = [
            {"name": item.repo_name, "status": _status_with_target(item)}
            for item in context.repo_inventory_snapshot
            if item.execution_target == "vm" and _matches_requested_target(item, requested_target)
        ]
        scope_label = f"VM {requested_target}" if requested_target else "VM"
        return reply_from_draft(
            build_repo_inventory_scope_draft(inventory=inventory, scope_label=scope_label, intent="repo_inventory_vm"),
            specialist_name=specialist_name,
        )

    if intent == "repo_inventory_cloud":
        inventory = [
            {"name": item.repo_name, "status": _status_with_target(item)}
            for item in context.repo_inventory_snapshot
            if item.execution_target in {"aws", "gcp"}
        ]
        return reply_from_draft(
            build_repo_inventory_scope_draft(inventory=inventory, scope_label="cloud", intent="repo_inventory_cloud"),
            specialist_name=specialist_name,
        )

    if intent == "repo_inventory_docker":
        requested_target = _requested_target_name(context)
        inventory = [
            {"name": item.repo_name, "status": _status_with_target(item)}
            for item in context.repo_inventory_snapshot
            if item.execution_target == "docker" and _matches_requested_target(item, requested_target)
        ]
        scope_label = f"Docker {requested_target}" if requested_target else "Docker"
        return reply_from_draft(
            build_repo_inventory_scope_draft(inventory=inventory, scope_label=scope_label, intent="repo_inventory_docker"),
            specialist_name=specialist_name,
        )

    if intent in {"repo_lifecycle_inventory", "repo_lifecycle_previous_work"}:
        inventory = [{"name": item.repo_name, "status": _status_with_target(item)} for item in context.repo_inventory_snapshot]
        return reply_from_draft(
            build_repo_lifecycle_inventory_draft(inventory=inventory),
            specialist_name=specialist_name,
        )

    if intent == "repo_status":
        return reply_from_draft(
            build_repo_status_draft(
                repo_name=repo.name if repo is not None else None,
                tracked_status=durable.tracked_status if durable is not None else None,
                install_location=durable.install_location if durable is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_lifecycle_specific_status":
        return reply_from_draft(
            build_repo_lifecycle_status_draft(
                repo_name=repo.name if repo is not None else None,
                tracked_status=durable.tracked_status if durable is not None else None,
                install_location=durable.install_location if durable is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_active":
        return reply_from_draft(
            build_repo_active_draft(
                active_name=active.repo_name if active is not None else None,
                active_status=active.status if active is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_lifecycle_active":
        return reply_from_draft(
            build_repo_lifecycle_active_draft(
                active_name=active.repo_name if active is not None else None,
                active_status=active.status if active is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_run":
        workflow_state = context.workflow_state
        return reply_from_draft(
            build_repo_run_draft(
                repo_name=repo.name if repo is not None else None,
                repo_key=(repo.repo_url or repo.name) if repo is not None else None,
                run_command=durable.run_command if durable is not None else None,
                verify_command=durable.verify_command if durable is not None else None,
                manual_command=durable.manual_command if durable is not None else None,
                runtime_kind=durable.runtime_kind if durable is not None else None,
                last_blocker=durable.last_blocker if durable is not None else None,
                last_run_result=durable.last_run_result if durable is not None else None,
                runtime_status=workflow_state.active_runtime_status if workflow_state is not None else None,
                logs_hint=workflow_state.active_runtime_logs_hint if workflow_state is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_stop":
        workflow_state = context.workflow_state
        return reply_from_draft(
            build_repo_stop_draft(
                repo_name=repo.name if repo is not None else (workflow_state.active_runtime_repo_name if workflow_state is not None else None),
                repo_key=(repo.repo_url or repo.name) if repo is not None else (workflow_state.active_runtime_repo_key if workflow_state is not None else None),
                runtime_status=workflow_state.active_runtime_status if workflow_state is not None else None,
                runtime_pid=workflow_state.active_runtime_pid if workflow_state is not None else None,
                stop_hint=workflow_state.active_runtime_stop_hint if workflow_state is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_restart":
        workflow_state = context.workflow_state
        runtime_status = None
        stop_hint = None
        repo_matches_live_runtime = False
        if workflow_state is not None:
            if repo is None:
                repo_matches_live_runtime = True
            else:
                repo_matches_live_runtime = workflow_state.active_runtime_repo_key in {
                    repo.repo_url,
                    repo.name,
                } or workflow_state.active_runtime_repo_name == repo.name
        if workflow_state is not None and repo_matches_live_runtime:
            runtime_status = workflow_state.active_runtime_status
            stop_hint = workflow_state.active_runtime_stop_hint
        return reply_from_draft(
            build_repo_restart_draft(
                repo_name=repo.name if repo is not None else (workflow_state.active_runtime_repo_name if workflow_state is not None else None),
                repo_key=(repo.repo_url or repo.name) if repo is not None else (workflow_state.active_runtime_repo_key if workflow_state is not None else None),
                run_command=durable.run_command if durable is not None else None,
                runtime_status=runtime_status,
                stop_hint=stop_hint,
                last_blocker=durable.last_blocker if durable is not None else None,
                last_run_result=durable.last_run_result if durable is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_logs":
        workflow_state = context.workflow_state
        return reply_from_draft(
            build_repo_logs_draft(
                repo_name=repo.name if repo is not None else (workflow_state.active_runtime_repo_name if workflow_state is not None else None),
                repo_key=(repo.repo_url or repo.name) if repo is not None else (workflow_state.active_runtime_repo_key if workflow_state is not None else None),
                runtime_status=workflow_state.active_runtime_status if workflow_state is not None else None,
                log_path=workflow_state.active_runtime_log_path if workflow_state is not None else None,
                logs_hint=workflow_state.active_runtime_logs_hint if workflow_state is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "repo_verify":
        return reply_from_draft(
            build_repo_verify_draft(
                repo_name=repo.name if repo is not None else None,
                repo_key=(repo.repo_url or repo.name) if repo is not None else None,
                tracked_status=durable.tracked_status if durable is not None else None,
                install_location=durable.install_location if durable is not None else None,
                run_command=durable.run_command if durable is not None else None,
                last_blocker=durable.last_blocker if durable is not None else None,
                last_run_result=durable.last_run_result if durable is not None else None,
            ),
            specialist_name=specialist_name,
        )

    if intent == "recommendation_followup":
        draft = build_recommendation_followup_draft(
            primary_repo=context.durable_recommendation.primary_repo or (context.mentioned_repo.name if context.mentioned_repo is not None else None),
            secondary_repo=context.durable_recommendation.secondary_repo,
        )
        if draft is not None:
            return reply_from_draft(draft, specialist_name=specialist_name)

    if intent == "slash_runtime_followup":
        draft = build_slash_runtime_followup_draft(
            repo_name=repo.name if repo is not None else None,
            pending_next_action=context.followup_state.pending_next_action,
        )
        if draft is not None:
            return reply_from_draft(draft, specialist_name=specialist_name)

    return None
