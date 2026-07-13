"""Repo-state reply builders extracted from the main conversation specialist."""

from __future__ import annotations

from dataclasses import dataclass
import re

from duckln.conversation_routes.replies import (
    repo_inventory_reply_text,
    repo_inventory_scoped_reply_text,
    repo_live_sessions_reply_text,
    repo_inventory_paths_reply_text,
    repo_inventory_sources_reply_text,
    repo_lifecycle_inventory_reply_text,
    repo_lifecycle_status_reply_text,
    repo_memory_meta_summary,
    repo_path_reply_text,
    repo_status_reply_text,
)
from duckln.render_blocks import bullet_block, join_blocks


@dataclass(frozen=True)
class RouteDraft:
    """Lightweight reply draft emitted by a route helper."""

    text: str
    intent: str
    steer: str | None = None
    action: str | None = None
    action_repo_key: str | None = None


_VERIFICATION_HINT_PATTERNS = (
    re.compile(r"(^|\s)--help(\s|$)"),
    re.compile(r"(^|\s)--version(\s|$)"),
    re.compile(r"(^|\s)checkhealth(\s|$)"),
    re.compile(r"(^|\s)healthcheck(\s|$)"),
    re.compile(r"(^|\s)pytest(\s|$)"),
    re.compile(r"(^|\s)python\s+-m\s+pip(\s|$)"),
)


def _looks_like_verification_command(command: str | None) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False
    normalized = " ".join(command.lower().split())
    return any(pattern.search(normalized) is not None for pattern in _VERIFICATION_HINT_PATTERNS)


def build_repo_memory_meta_draft(
    *,
    repo_name: str | None,
    tracked_status: str | None,
    has_repo_knowledge: bool,
    in_recommendation_memory: bool,
    has_learning_signal: bool,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly and I’ll tell you whether Duckln is carrying tracked setup state, repo notes, or only current thread context for it.",
            intent="repo_memory_meta",
        )
    facts: list[str] = []
    if tracked_status is not None:
        facts.append(f"Tracked setup state says {repo_name} is {tracked_status}")
    if has_repo_knowledge:
        facts.append("Duckln has bounded repo notes for it")
    if in_recommendation_memory:
        facts.append("It is also part of the current recommendation context")
    if has_learning_signal:
        facts.append("Duckln has at least one durable learning signal tied to it")
    return RouteDraft(
        repo_memory_meta_summary(repo_name=repo_name, facts=facts),
        intent="repo_memory_meta",
    )


def build_repo_location_draft(
    *,
    repo_name: str | None,
    install_location: str | None,
    environment_path: str | None,
) -> RouteDraft:
    if repo_name and install_location:
        env_text = f" The project environment is at {environment_path}." if environment_path else ""
        return RouteDraft(
            f"{repo_name} is installed in Duckln’s managed workspace at {install_location}.{env_text}",
            intent="repo_location",
        )
    return RouteDraft(
        "I can answer that once Duckln has either selected or prepared the repo. Right now I don’t have a stored install path for it.",
        intent="repo_location",
    )


def build_repo_access_draft(
    *,
    repo_name: str | None,
    repo_key: str | None = None,
    run_command: str | None,
    access_hint: str | None,
    first_entrypoint: str | None,
    install_location: str | None = None,
    execution_target: str = "local",
    vm_name: str | None = None,
    docker_name: str | None = None,
    cloud_vendor: str | None = None,
    cloud_region: str | None = None,
    runtime_status: str | None = None,
    active_attach_hint: str | None = None,
) -> RouteDraft:
    hint = active_attach_hint or access_hint
    if run_command:
        if runtime_status == "interactive":
            return RouteDraft(
                f"Duckln already has {repo_name} open in the terminal pane, so you can keep working there now.",
                intent="repo_access",
            )
        extra = f" Access: {hint}" if hint else ""
        return RouteDraft(
            f"Duckln can open the tracked access path for {repo_name} now. Start point: `{run_command}`.{extra}",
            intent="repo_access",
            action="attach_repo",
            action_repo_key=repo_key,
        )
    if install_location:
        target_text = "local machine"
        if execution_target == "vm":
            target_text = f"VM {vm_name}" if vm_name else "the active VM"
        elif execution_target == "docker":
            target_text = f"Docker container {docker_name}" if docker_name else "the active Docker target"
        elif execution_target in {"aws", "gcp"}:
            provider = cloud_vendor or execution_target.upper()
            target_text = f"{provider} cloud target in {cloud_region}" if cloud_region else f"{provider} cloud target"
        extra = f" Access: {hint}" if hint else ""
        return RouteDraft(
            f"Duckln can open {repo_name} from its tracked repo path on the {target_text}.{extra}",
            intent="repo_access",
            action="attach_repo",
            action_repo_key=repo_key,
        )
    if repo_name and first_entrypoint:
        return RouteDraft(
            f"For {repo_name}, the most likely first run path is `{first_entrypoint}` from the managed project directory.",
            intent="repo_access",
        )
    return RouteDraft(
        "I can answer that once Duckln has a selected repo or a stored setup outcome. Name the repo directly and I’ll ground the run/access path.",
        intent="repo_access",
    )


def build_repo_removal_draft(
    *,
    repo_name: str | None,
    repo_key: str | None = None,
    removal_hint: str | None,
    tracked_status: str | None,
    install_location: str | None,
    execution_target: str = "local",
    vm_name: str | None = None,
    docker_name: str | None = None,
    cloud_vendor: str | None = None,
    cloud_region: str | None = None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo you want removed and I’ll give you the smallest safe removal path.",
            intent="repo_removal",
        )
    if removal_hint:
        return RouteDraft(
            join_blocks(
                f"I can help remove {repo_name}.",
                removal_hint.rstrip(".") + ".",
                "If you confirm it, Duckln can handle that removal from here.",
            ),
            intent="repo_removal",
            action="remove_repo",
            action_repo_key=repo_key,
        )
    if tracked_status is not None:
        steps = (
            "stop the repo if it is running",
            "remove the managed project directory",
            "clear Duckln's tracked repo state for it",
        )
        if execution_target == "vm":
            target_line = f"This is tracked in VM {vm_name or 'the active VM'}."
        elif execution_target == "docker":
            target_line = (
                f"This is tracked in Docker container {docker_name}."
                if docker_name
                else "This is tracked on the active Docker target."
            )
        elif execution_target in {"aws", "gcp"}:
            provider = (cloud_vendor or execution_target.upper()).strip() or execution_target.upper()
            region = (cloud_region or "the active region").strip()
            target_line = f"This is tracked on {provider} in {region}."
        else:
            target_line = "This is tracked on the local machine."
        location_line = f"Duckln currently has it tracked at {install_location}." if install_location else "Duckln is tracking it as a prepared repo on this system."
        return RouteDraft(
            join_blocks(
                f"I can help remove {repo_name}.",
                location_line,
                target_line,
                bullet_block(steps),
                "Duckln will ask for confirmation before it removes anything.",
            ),
            intent="repo_removal",
            action="remove_repo",
            action_repo_key=repo_key,
        )
    return RouteDraft(
        join_blocks(
            f"Duckln is not currently tracking {repo_name} as installed on this system.",
            "If you still want it gone, I can check for a stored location or help you verify whether it was set up outside Duckln.",
        ),
        intent="repo_removal",
    )


def build_repo_inventory_draft(*, inventory: list[dict[str, str]]) -> RouteDraft:
    if not inventory:
        return RouteDraft(
            "Duckln does not have any prepared repos recorded yet. Once you set one up, I can list it here and track which one is active.",
            intent="repo_inventory",
        )
    return RouteDraft(repo_inventory_reply_text(inventory), intent="repo_inventory")


def build_repo_inventory_scope_draft(
    *,
    inventory: list[dict[str, str]],
    scope_label: str,
    intent: str,
) -> RouteDraft:
    if not inventory:
        return RouteDraft(
            f"Duckln does not currently have any {scope_label} repos tracked.",
            intent=intent,
        )
    return RouteDraft(
        repo_inventory_scoped_reply_text(
            title=f"Here are the {scope_label} repos Duckln currently has tracked:",
            inventory=inventory,
        ),
        intent=intent,
    )


def build_repo_live_sessions_draft(
    *,
    sessions: list[dict[str, str]],
    intent: str,
    scope_label: str | None = None,
) -> RouteDraft:
    if not sessions:
        label = f" on the {scope_label}" if scope_label else ""
        return RouteDraft(
            f"Duckln does not currently have any live repo sessions tracked{label}.",
            intent=intent,
        )
    title = "Here are the live repo sessions Duckln currently has tracked:"
    if scope_label:
        title = f"Here are the live repo sessions Duckln currently has tracked on the {scope_label}:"
    return RouteDraft(
        repo_live_sessions_reply_text(title=title, sessions=sessions),
        intent=intent,
    )


def build_repo_path_inventory_draft(*, inventory: list[dict[str, str]]) -> RouteDraft:
    if not inventory:
        return RouteDraft(
            "Duckln does not currently have any tracked repo paths recorded.",
            intent="repo_path_inventory",
        )
    return RouteDraft(
        repo_inventory_paths_reply_text(
            title="Here are the tracked repo locations Duckln currently has recorded:",
            inventory=inventory,
        ),
        intent="repo_path_inventory",
    )


def build_repo_source_inventory_draft(*, inventory: list[dict[str, str]]) -> RouteDraft:
    if not inventory:
        return RouteDraft(
            "Duckln does not currently have any tracked repo source URLs recorded.",
            intent="repo_source_inventory",
        )
    return RouteDraft(
        repo_inventory_sources_reply_text(
            title="Here are the tracked repo source URLs Duckln currently has recorded:",
            inventory=inventory,
        ),
        intent="repo_source_inventory",
    )


def build_repo_lifecycle_inventory_draft(*, inventory: list[dict[str, str]]) -> RouteDraft:
    if not inventory:
        return RouteDraft(
            "Duckln does not currently have any repos tracked as installed, active, or prepared.",
            intent="repo_lifecycle_inventory",
        )
    return RouteDraft(repo_lifecycle_inventory_reply_text(inventory), intent="repo_lifecycle_inventory")


def build_repo_status_draft(
    *,
    repo_name: str | None,
    tracked_status: str | None,
    install_location: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly and I’ll tell you whether Duckln has it set up, running, failed, or not tracked yet.",
            intent="repo_status",
        )
    if tracked_status is None:
        return RouteDraft(
            f"Duckln is not currently tracking {repo_name} as set up on this system.",
            intent="repo_status",
        )
    return RouteDraft(
        repo_status_reply_text(repo_name=repo_name, status=tracked_status, location=install_location),
        intent="repo_status",
    )


def build_repo_lifecycle_status_draft(
    *,
    repo_name: str | None,
    tracked_status: str | None,
    install_location: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly and I’ll tell you whether Duckln has it installed, active, prepared, or not tracked.",
            intent="repo_lifecycle_specific_status",
        )
    if tracked_status is None:
        return RouteDraft(
            f"Duckln is not currently tracking {repo_name} as installed or active on this system.",
            intent="repo_lifecycle_specific_status",
        )
    return RouteDraft(
        repo_lifecycle_status_reply_text(repo_name=repo_name, status=tracked_status, location=install_location),
        intent="repo_lifecycle_specific_status",
    )


def build_repo_active_draft(*, active_name: str | None, active_status: str | None) -> RouteDraft:
    if not active_name or not active_status:
        return RouteDraft(
            "There is no active repo context or live repo session in Duckln right now.",
            intent="repo_active",
        )
    if active_status not in {"running", "interactive"}:
        return RouteDraft(
            f"Duckln’s current active repo context is {active_name}, but there is no live running repo session right now. That active repo context is only the selected repo thread. Its last recorded state is {active_status}.",
            intent="repo_active",
        )
    return RouteDraft(
        f"Duckln currently has a live repo session for {active_name}, and its runtime status is {active_status}.",
        intent="repo_active",
    )


def build_repo_lifecycle_active_draft(*, active_name: str | None, active_status: str | None) -> RouteDraft:
    if not active_name or not active_status:
        return RouteDraft(
            "Duckln does not currently have an active repo context on this system.",
            intent="repo_lifecycle_active",
        )
    if active_status not in {"running", "interactive"}:
        return RouteDraft(
            f"Duckln’s current active repo context is {active_name}, but there is no live running repo session right now. That active repo context is only the selected repo thread. Its last recorded state is {active_status}.",
            intent="repo_lifecycle_active",
        )
    return RouteDraft(
        f"Yes. Duckln currently has a live repo session for {active_name}, and its runtime status is {active_status}.",
        intent="repo_lifecycle_active",
    )


def build_repo_path_draft(
    *,
    repo_name: str | None,
    install_location: str | None,
    execution_target: str,
    vm_name: str | None,
    docker_name: str | None = None,
    cloud_vendor: str | None = None,
    cloud_region: str | None = None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly or ask for the active repo path and I’ll show the tracked location.",
            intent="repo_path_show",
        )
    return RouteDraft(
        repo_path_reply_text(
            repo_name=repo_name,
            location=install_location,
            execution_target=execution_target,
            vm_name=vm_name,
            docker_name=docker_name,
            cloud_vendor=cloud_vendor,
            cloud_region=cloud_region,
        ),
        intent="repo_path_show",
    )


def build_repo_source_draft(
    *,
    repo_name: str | None,
    source_repo_url: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly or ask for the active repo GitHub link and I’ll show the tracked source URL.",
            intent="repo_source_show",
        )
    if source_repo_url:
        return RouteDraft(
            f"{repo_name} is tracked from {source_repo_url}.",
            intent="repo_source_show",
        )
    return RouteDraft(
        f"Duckln is tracking {repo_name}, but it does not have a public source repo URL recorded for it yet.",
        intent="repo_source_show",
    )


def build_repo_run_draft(
    *,
    repo_name: str | None,
    repo_key: str | None,
    run_command: str | None,
    verify_command: str | None,
    manual_command: str | None,
    runtime_kind: str | None,
    last_blocker: str | None,
    last_run_result: str | None,
    runtime_status: str | None = None,
    logs_hint: str | None = None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "I can run a prepared repo once I know which one you mean. Name the repo directly or ask which repos Duckln has set up.",
            intent="repo_run",
        )
    if runtime_status == "running":
        log_line = f" Logs: `{logs_hint}`." if logs_hint else ""
        return RouteDraft(
            f"Duckln is already tracking a live runtime session for {repo_name}.{log_line}",
            intent="repo_run",
        )
    if runtime_kind == "cli_tool" and (not run_command or _looks_like_verification_command(run_command)):
        guidance: list[str] = [f"{repo_name} is installed as a CLI tool, not a long-running repo service."]
        if verify_command:
            guidance.append(f"Duckln can verify it with `{verify_command}`.")
        if manual_command:
            guidance.append(f"Manual use: `{manual_command}`.")
        guidance.append("If you want a real run, Duckln can ask for the input file or task next and then materialize the CLI command.")
        return RouteDraft(
            " ".join(guidance),
            intent="repo_run",
            action="run_repo",
            action_repo_key=repo_key,
        )
    if run_command:
        return RouteDraft(
            f"Duckln is ready to run {repo_name} when you want to start it. The stored run command is {run_command}.",
            intent="repo_run",
            action="run_repo",
            action_repo_key=repo_key,
        )
    if last_run_result in {"failed", "missing_command"}:
        blocker = last_blocker or "Duckln does not have a verified run path stored yet."
        return RouteDraft(
            f"I can help get {repo_name} running, but Duckln is still carrying a run issue for it. {blocker}",
            intent="repo_run",
            action="run_repo",
            action_repo_key=repo_key,
        )
    return RouteDraft(
        f"I can try to run {repo_name}, but Duckln does not have a verified run command stored for it yet.",
        intent="repo_run",
        action="run_repo",
        action_repo_key=repo_key,
    )


def build_repo_restart_draft(
    *,
    repo_name: str | None,
    repo_key: str | None,
    run_command: str | None,
    runtime_status: str | None,
    stop_hint: str | None,
    last_blocker: str | None,
    last_run_result: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly or ask to restart the active repo and I’ll use Duckln’s tracked runtime state.",
            intent="repo_restart",
        )
    if runtime_status in {"running", "interactive"}:
        hint_text = f" {stop_hint}" if stop_hint else ""
        return RouteDraft(
            f"Duckln can restart {repo_name} by stopping the tracked live session first and then re-running it.{hint_text}".strip(),
            intent="repo_restart",
            action="restart_repo",
            action_repo_key=repo_key,
        )
    if run_command:
        return RouteDraft(
            f"Duckln is not tracking a live runtime for {repo_name} right now, so restart will fall back to a fresh bounded run using {run_command}.",
            intent="repo_restart",
            action="restart_repo",
            action_repo_key=repo_key,
        )
    if last_run_result in {"failed", "missing_command"}:
        blocker_text = f" {last_blocker}" if last_blocker else ""
        return RouteDraft(
            f"Duckln can try to restart {repo_name}, but it is still carrying a run issue for it.{blocker_text}",
            intent="repo_restart",
            action="restart_repo",
            action_repo_key=repo_key,
        )
    return RouteDraft(
        f"Duckln can try to restart {repo_name}, but it does not have a verified run path stored for it yet.",
        intent="repo_restart",
        action="restart_repo",
        action_repo_key=repo_key,
    )


def build_repo_stop_draft(
    *,
    repo_name: str | None,
    repo_key: str | None,
    runtime_status: str | None,
    runtime_pid: int | None,
    stop_hint: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly or ask me to stop the active repo and I’ll check whether Duckln has a live runtime session to stop.",
            intent="repo_stop",
        )
    if runtime_status in {"running", "interactive"}:
        pid_text = f" The tracked session PID is {runtime_pid}." if runtime_pid is not None else ""
        hint_text = f" {stop_hint}" if stop_hint else ""
        return RouteDraft(
            f"Duckln is tracking a live runtime session for {repo_name}.{pid_text}{hint_text}".strip(),
            intent="repo_stop",
            action="stop_repo",
            action_repo_key=repo_key,
        )
    return RouteDraft(
        f"Duckln is not currently tracking a live repo session for {repo_name}, so there is nothing reliable to stop yet.",
        intent="repo_stop",
    )


def build_repo_logs_draft(
    *,
    repo_name: str | None,
    repo_key: str | None,
    runtime_status: str | None,
    log_path: str | None,
    logs_hint: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly or ask for the active repo logs and I’ll show the bounded log path when Duckln has one.",
            intent="repo_logs",
        )
    if runtime_status == "running" and log_path:
        extra = f" Fast path: `{logs_hint}`." if logs_hint else ""
        return RouteDraft(
            f"Duckln is tracking runtime logs for {repo_name} at {log_path}.{extra}",
            intent="repo_logs",
            action="show_repo_logs",
            action_repo_key=repo_key,
        )
    return RouteDraft(
        f"Duckln does not currently have a live runtime log stream recorded for {repo_name}.",
        intent="repo_logs",
    )


def build_repo_verify_draft(
    *,
    repo_name: str | None,
    repo_key: str | None,
    tracked_status: str | None,
    install_location: str | None,
    run_command: str | None,
    last_blocker: str | None,
    last_run_result: str | None,
) -> RouteDraft:
    if not repo_name:
        return RouteDraft(
            "Name the repo directly or ask me to verify the active repo and I’ll run the smallest bounded check instead of trusting old tracked state.",
            intent="repo_verify",
        )
    status_line = (
        f"The last recorded state for {repo_name} is {tracked_status}."
        if tracked_status
        else f"Duckln does not have a stored ready state for {repo_name} yet."
    )
    location_line = f" It is recorded at {install_location}." if install_location else ""
    if run_command:
        return RouteDraft(
            join_blocks(
                f"I can verify whether {repo_name} is still error free to run with a bounded live check instead of only trusting tracked state.",
                status_line + location_line,
                f"The stored verification path is `{run_command}`.",
            ),
            intent="repo_verify",
            action="verify_repo",
            action_repo_key=repo_key,
        )
    blocker_line = f" Duckln is still carrying this blocker: {last_blocker}" if last_blocker else ""
    if last_run_result in {"failed", "missing_command"}:
        return RouteDraft(
            join_blocks(
                f"I can verify {repo_name}, but Duckln is still carrying a run issue for it.",
                status_line + location_line + blocker_line,
                "If you want, Duckln can rerun the bounded verification path and stay on the current issue.",
            ),
            intent="repo_verify",
            action="verify_repo",
            action_repo_key=repo_key,
        )
    return RouteDraft(
        join_blocks(
            f"I can check whether {repo_name} is really ready to run, but Duckln needs to do a bounded live verification instead of only reading tracked state.",
            status_line + location_line,
        ),
        intent="repo_verify",
        action="verify_repo",
        action_repo_key=repo_key,
    )


def build_recommendation_followup_draft(
    *,
    primary_repo: str | None,
    secondary_repo: str | None,
) -> RouteDraft | None:
    if primary_repo and secondary_repo:
        return RouteDraft(
            join_blocks(
                f"Current ranking is {primary_repo} first and {secondary_repo} second.",
                "I can compare them directly or keep moving toward setup.",
            ),
            intent="recommendation_followup",
        )
    if primary_repo:
        return RouteDraft(
            join_blocks(
                f"{primary_repo} is still the current pick.",
                "I can explain why it won, inspect its requirements, or move toward setup.",
            ),
            intent="recommendation_followup",
        )
    return None


def build_slash_runtime_followup_draft(
    *,
    repo_name: str | None,
    pending_next_action: str | None,
) -> RouteDraft | None:
    if not repo_name:
        return None
    if pending_next_action == "set_up_repo":
        return RouteDraft(
            join_blocks(
                f"{repo_name} is still the active repo.",
                "The next bounded step is setup unless you want one more requirements check first.",
            ),
            intent="slash_runtime_followup",
        )
    if pending_next_action == "run_repo":
        return RouteDraft(
            join_blocks(
                f"{repo_name} is still the active repo.",
                "I can move to the run step, or pause on status and requirements first.",
            ),
            intent="slash_runtime_followup",
        )
    return RouteDraft(
        join_blocks(
            f"{repo_name} is still the active repo from /repos.",
            "I can explain the repo, recall what Duckln remembers about it, or take the next setup step.",
        ),
        intent="slash_runtime_followup",
    )
