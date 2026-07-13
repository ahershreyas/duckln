"""Local non-repo conversation handlers extracted from the supervisor."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable


def _plain_table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    """Render compact terminal-safe tables for inventory answers."""
    if not headers:
        return ""
    normalized_rows = tuple(tuple(str(cell or "") for cell in row) for row in rows)
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in normalized_rows if index < len(row)))
        for index in range(len(headers))
    ]

    def _render_row(row: tuple[str, ...]) -> str:
        cells = [
            (row[index] if index < len(row) else "").ljust(widths[index])
            for index in range(len(headers))
        ]
        return "| " + " | ".join(cells) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join((_render_row(headers), separator, *(_render_row(row) for row in normalized_rows)))


def _local_docker_container_rows() -> tuple[str | None, tuple[tuple[str, str], ...]]:
    if shutil.which("docker") is None:
        return "Docker is not installed on this machine, so there are no local Docker containers to list.", ()
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Docker did not answer within 5 seconds. Docker may be starting or the daemon may be stuck.", ()
    except OSError as exc:
        return f"Duckln could not run Docker locally: {exc}", ()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Docker returned a non-zero exit.").strip().splitlines()
        first_line = detail[0] if detail else "Docker returned a non-zero exit."
        return f"Docker is installed, but Duckln could not read containers: {first_line}", ()
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        name, separator, status = line.partition("\t")
        if not separator:
            name, status = line.strip(), ""
        name = name.strip()
        if name:
            rows.append((name, status.strip()))
    return None, tuple(rows)


def build_local_general_reply(
    *,
    intent: str,
    message: str,
    normalized_compact: str,
    current,
    config_dir,
    system_probe,
    context,
    recent_turns: tuple[Any, ...],
    recent_replies: tuple[Any, ...],
    specialist_name: str,
    prefers_direct_tone: bool,
    reply_factory: Callable[..., Any],
    pick_reply: Callable[[tuple[Any, ...], str, tuple[Any, ...]], Any],
    extract_user_alias: Callable[[str], str | None],
    join_blocks: Callable[..., str],
    bullet_block: Callable[[tuple[str, ...]], str],
    build_system_summary_reply: Callable[[Any], Any],
    memory_meta_summary: Callable[..., str],
    repair_reply_texts: Callable[..., tuple[str, ...]],
    restatement_reply_texts: Callable[..., tuple[str, ...]],
    list_learning_records: Callable[[Any], tuple[Any, ...] | list[Any]],
) -> Any | None:
    last_reply = recent_replies[-1] if recent_replies else None
    last_user_input = " ".join(recent_turns[-1].content.strip().lower().split()) if recent_turns else ""
    alias = (current.user_name if current is not None else "there").strip() if current is not None else "there"
    alias_text = "" if alias.lower() == "there" else f" {alias}"

    if intent == "greeting":
        if any(
            phrase in normalized_compact
            for phrase in ("what shall i call you", "what should i call you", "what do i call you", "who are you")
        ):
            return reply_factory(
                "Duckln is fine. I’m your terminal-side repo manager here.",
                intent="greeting",
                specialist_name=specialist_name,
            )
        options = (
            reply_factory(
                f"Hi{alias_text}. Tell me what you want to get running and I’ll keep the path small.",
                intent="greeting",
                specialist_name=specialist_name,
            ),
            reply_factory(
                f"Hello{alias_text}. If there’s a repo, blocker, or machine question on your mind, point me at it.",
                intent="greeting",
                specialist_name=specialist_name,
            ),
            reply_factory(
                f"Hey{alias_text}. I’m here for repo bring-up, setup fixes, and quick system checks.",
                intent="greeting",
                specialist_name=specialist_name,
            ),
        )
        if prefers_direct_tone:
            options = (
                reply_factory(
                    f"Hi{alias_text}. Tell me the repo, blocker, or environment question and I’ll keep the next step small.",
                    intent="greeting",
                    specialist_name=specialist_name,
                ),
            ) + options
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "user_alias_set":
        alias_value = extract_user_alias(message)
        if alias_value:
            return reply_factory(
                join_blocks(
                    f"Got it. I’ll call you {alias_value}.",
                    f"Hey {alias_value}, that’s a unique name.",
                ),
                intent="user_alias_set",
                specialist_name=specialist_name,
                action="update_user_alias",
                user_alias=alias_value,
            )
        return reply_factory(
            "Tell me the alias you want me to use and I’ll switch to it.",
            intent="user_alias_set",
            specialist_name=specialist_name,
        )

    if intent == "user_identity_meta":
        options = (
            reply_factory(
                "Not unless you tell me. Duckln keeps short bounded preferences and repo context, not a hidden profile about you.",
                intent="user_identity_meta",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Only in the limited way you’ve told me here. I keep the current thread and a few high-signal preferences, not a background identity record.",
                intent="user_identity_meta",
                specialist_name=specialist_name,
            ),
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "repo_active_explanation":
        repo = context.active_repo or context.mentioned_repo
        if repo is not None:
            return reply_factory(
                join_blocks(
                    f"Active repo just means Duckln is still tracking {repo.name} as the current repo thread.",
                    "It is not a hidden background action. It is only the repo Duckln will assume when you say things like 'run it' or 'what next'.",
                ),
                intent="repo_active_explanation",
                specialist_name=specialist_name,
            )
        return reply_factory(
            "Active repo means the repo Duckln is currently tracking for follow-up questions. Right now there is not one locked in.",
            intent="repo_active_explanation",
            specialist_name=specialist_name,
        )

    if intent == "rapport":
        options = (
            reply_factory(
                "Doing well. I’m strongest when there’s a concrete setup, failure, or repo to work through if you want help with one.",
                intent="rapport",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Doing well and ready to work. If you have a repo or setup issue in mind, point me at that first.",
                intent="rapport",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Good. I’m set up for terminal work, so if something is blocked we can narrow it down quickly.",
                intent="rapport",
                specialist_name=specialist_name,
            ),
        )
        if prefers_direct_tone:
            options = (
                reply_factory(
                    "Doing well. If you give me the concrete repo or blocker, I’ll answer it directly.",
                    intent="rapport",
                    specialist_name=specialist_name,
                ),
            ) + options
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "system_followup":
        project_summary = context.project_summary
        suffix = f" The latest tracked project context is: {project_summary}" if project_summary else ""
        return reply_factory(
            f"I know because Duckln checked and stored the local system for this session. {context.machine_explanation}{suffix}",
            intent="system_followup",
            specialist_name=specialist_name,
        )

    if intent in {"system_summary", "system_capacity", "system_fit_context"}:
        reply = build_system_summary_reply(system_probe)
        return reply_factory(
            reply.summary,
            intent=intent,
            specialist_name=specialist_name,
        )

    if intent == "system_verify":
        return reply_factory(
            "The bounded way to verify Duckln itself is /healthcheck. That checks the current environment directly instead of trusting stale tracked state or recommendation context.",
            intent="system_verify",
            steer="/healthcheck",
            specialist_name=specialist_name,
        )

    if intent == "vm_definition":
        vm_name = context.active_vm_name
        vm_line = f" Right now the active VM is {vm_name}." if vm_name else ""
        return reply_factory(
            join_blocks(
                "A VM here means a separate Ubuntu machine Duckln can create with Multipass so repo setup stays isolated from your host system.",
                f"Duckln uses it for cleaner Linux-only dependency work, not to magically add more GPU power.{vm_line}",
            ),
            intent="vm_definition",
            specialist_name=specialist_name,
        )

    if intent == "vm_capability":
        vm_target = "The current execution target is the VM." if context.execution_target == "vm" else "Duckln can keep VM work separate from local work."
        return reply_factory(
            join_blocks(
                "Yes. Duckln can create a Multipass Ubuntu VM, configure Duckln inside it if you want, track repos there separately, and guide setup or repair work in that VM.",
                bullet_block(
                    (
                        "create or configure the Ubuntu VM",
                        "keep VM repo paths separate from local repo paths",
                        "set up or repair repos inside the VM",
                        "avoid copying local provider secrets into the VM automatically",
                    )
                ),
                vm_target,
            ),
            intent="vm_capability",
            specialist_name=specialist_name,
        )

    if intent == "vm_list":
        from duckln.vm import is_multipass_installed, list_multipass_vm_names
        if not is_multipass_installed():
            return reply_factory(
                "Multipass is not installed on this machine, so there are no local VMs to list. Install it first with `brew install --cask multipass` (macOS) or `sudo snap install multipass` (Linux).",
                intent="vm_list",
                specialist_name=specialist_name,
            )
        vm_names = list_multipass_vm_names()
        active_vm = str(getattr(context, "active_vm_name", "") or "").strip()
        if not vm_names:
            return reply_factory(
                "No Multipass VMs found on this machine. Run `/vm` to create one.",
                intent="vm_list",
                specialist_name=specialist_name,
            )
        indexed_vms = {
            str(row.get("name") or "").strip(): row
            for row in getattr(context, "vm_inventory_snapshot", ()) or ()
            if str(row.get("name") or "").strip()
        }
        repo_counts: dict[str, int] = {}
        for item in getattr(context, "repo_inventory_snapshot", ()) or ():
            if getattr(item, "execution_target", "") != "vm":
                continue
            vm_name = str(getattr(item, "vm_name", "") or "").strip()
            if vm_name:
                repo_counts[vm_name] = repo_counts.get(vm_name, 0) + 1

        def _vm_row(name: str) -> tuple[str, str, str, str, str]:
            row = indexed_vms.get(name, {})
            created = str(row.get("created_at") or "").split("T")[0] or "-"
            status = str(row.get("status") or "").strip() or "-"
            count = int(row.get("repo_count") or repo_counts.get(name, 0) or 0)
            count_text = f"{count} repo{'s' if count != 1 else ''}"
            active = "yes" if name == active_vm else ""
            return (name, created, status, count_text, active)

        vm_rows = tuple(
            _vm_row(name)
            for name in vm_names
        )
        hint = f"Duckln is currently targeting '{active_vm}'." if active_vm and active_vm in vm_names else "No VM is set as the active target. Run `/vm` to create or attach one."
        return reply_factory(
            join_blocks(
                f"{len(vm_names)} Multipass VM{'s' if len(vm_names) != 1 else ''} found on this machine:",
                _plain_table(("Name", "Created", "State", "Repos", "Active"), vm_rows),
                hint,
            ),
            intent="vm_list",
            specialist_name=specialist_name,
        )

    if intent == "vm_open":
        return reply_factory(
            "I can open a VM shell. If you name one, Duckln will start it if needed; otherwise it will show the VM list first.",
            intent="vm_open",
            specialist_name=specialist_name,
        )

    if intent == "vm_delete":
        return reply_factory(
            "That is a VM deletion request, not a repo removal. Duckln will only delete VMs after explicit confirmation.",
            intent="vm_delete",
            specialist_name=specialist_name,
        )

    if intent == "docker_list":
        error, containers = _local_docker_container_rows()
        if error is not None:
            return reply_factory(
                error,
                intent="docker_list",
                specialist_name=specialist_name,
            )
        if not containers:
            return reply_factory(
                "Docker is installed, but no local containers were found.",
                intent="docker_list",
                specialist_name=specialist_name,
            )
        visible = tuple((name, status or "-") for name, status in containers[:8])
        extra = len(containers) - len(visible)
        parts = [
            f"{len(containers)} local Docker container{'s' if len(containers) != 1 else ''} found on this machine:",
            _plain_table(("Container", "Status"), visible),
        ]
        if extra > 0:
            parts.append(f"{extra} more container{'s' if extra != 1 else ''} not shown.")
        return reply_factory(
            join_blocks(*parts),
            intent="docker_list",
            specialist_name=specialist_name,
        )

    if intent == "vm_repo_recommendation":
        current_pick = context.durable_recommendation.primary_repo
        wants_top_list = any(token in normalized_compact for token in ("top ", "top three", "choice repos", "choices", "vm friendly", "vm-friendly"))
        if wants_top_list:
            names: list[str] = []
            if current_pick:
                names.append(current_pick)
            for record in context.records:
                if record.name not in names:
                    names.append(record.name)
                if len(names) >= 3:
                    break
            if names:
                return reply_factory(
                    join_blocks(
                        "For a VM-first path, these are the first repos I’d compare:",
                        bullet_block(tuple(names[:3])),
                        "I’m weighting cleaner Ubuntu isolation higher than raw GPU assumptions here.",
                    ),
                    intent="vm_repo_recommendation",
                    specialist_name=specialist_name,
                )
        recommendation_line = (
            f"If you want a VM-first path right now, Duckln’s current pick is still {current_pick}, but the reason is cleaner Linux isolation rather than extra GPU headroom."
            if current_pick
            else "For a VM-first path, Duckln should rank repos by Linux isolation value, not by pretending the VM adds CUDA or extra GPU power."
        )
        return reply_factory(
            join_blocks(
                recommendation_line,
                "A VM helps when the repo wants a cleaner Ubuntu environment or Linux-first dependencies. It does not make this Mac behave like a CUDA box.",
                "If you want, I can narrow the VM-friendly choices from /repos or compare local vs VM for a specific repo.",
            ),
            intent="vm_repo_recommendation",
            specialist_name=specialist_name,
        )

    if intent == "memory_meta":
        recommendation_repo_name = context.durable_recommendation.primary_repo
        session_summary = context.recent_thread_summary
        heuristic_count = len(context.promoted_heuristics)
        learning_records = () if config_dir is None else tuple(list_learning_records(config_dir))
        candidate_count = sum(1 for record in learning_records if record.state in {"candidate", "validated"})
        promoted_count = sum(1 for record in learning_records if record.state == "promoted")
        return reply_factory(
            memory_meta_summary(
                config_dir_present=config_dir is not None,
                recommendation_repo_name=recommendation_repo_name,
                has_session_summary=bool(session_summary),
                candidate_count=candidate_count,
                promoted_count=promoted_count,
                heuristic_count=heuristic_count,
            ),
            intent=intent,
            specialist_name=specialist_name,
        )

    if intent == "repo_confidence":
        options = (
            reply_factory(
                "Pretty strong when the repo has enough clues in its setup files. I’ll usually start with the smallest working path and verify it.",
                intent="repo_confidence",
                steer="/repos",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "That’s one of the things I’m built for. I’m best at reading the repo, choosing the lightest setup path, and checking what actually works.",
                intent="repo_confidence",
                steer="/repos",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Strongest on setup, dependency issues, and bring-up verification. If you want, pick a repo with /repos and I’ll take the shortest path through it.",
                intent="repo_confidence",
                steer="/repos",
                specialist_name=specialist_name,
            ),
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "confidence":
        options = (
            reply_factory(
                "Confident on repo setup, provider configuration, and narrowing down bring-up failures, especially when I can inspect the repo and verify each step.",
                intent="confidence",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Pretty confident in my lane. I’m best at terminal setup work, bounded fixes, and checking whether a change actually worked.",
                intent="confidence",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Most confident on practical setup and debugging work rather than open-ended chat. If there’s a concrete target, I can usually get to a smaller next step quickly.",
                intent="confidence",
                specialist_name=specialist_name,
            ),
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "capability":
        wants_expertise = any(token in normalized_compact for token in ("expertise", "expert", "good at", "strong at"))
        wants_points = any(token in normalized_compact for token in ("5 points", "five points", "bullet", "list"))
        if wants_points or "what can you help me with" in normalized_compact:
            return reply_factory(
                join_blocks(
                    "Here’s where I’m useful in the terminal:",
                    bullet_block(
                        (
                            "Pick a repo from /repos or inspect a custom GitHub repo link.",
                            "Set it up locally or guide the Ubuntu VM path when isolation is cleaner.",
                            "Diagnose setup failures and keep the next fix bounded.",
                            "Verify whether the repo actually runs after a change.",
                            "Remember short high-signal context so you do not have to restate everything.",
                        )
                    ),
                ),
                intent="capability",
                specialist_name=specialist_name,
            )
        options = (
            reply_factory(
                "I’m strongest at provider setup, repo bring-up, environment checks, and narrowing down setup failures without a lot of noise.",
                intent="capability",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "I handle practical terminal tasks: switching providers or models, bringing repos up, checking health, and guiding VM setup when local isn’t a good fit.",
                intent="capability",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "My lane is getting you unstuck in the terminal: setup, verification, bounded fixes, and the next smallest useful step.",
                intent="capability",
                specialist_name=specialist_name,
            ),
        )
        if prefers_direct_tone:
            options = (
                reply_factory(
                    "I can recommend repos, inspect requirements, set them up, verify them, run them, and help repair or remove them from here.",
                    intent="capability",
                    specialist_name=specialist_name,
                ),
            ) + options
        reply = pick_reply(options, normalized_compact, recent_replies)
        if wants_expertise and "strongest" not in reply.text and "lane" not in reply.text:
            return reply_factory(
                "I’m strongest at repo setup, provider and model configuration, health checks, and diagnosing bring-up failures with a bounded next step.",
                intent="capability",
                steer=reply.steer,
                specialist_name=specialist_name,
            )
        return reply

    if intent == "conversation_repair":
        repo = context.mentioned_repo or context.active_repo
        options = tuple(
            reply_factory(
                text,
                intent="conversation_repair",
                specialist_name=specialist_name,
            )
            for text in repair_reply_texts(
                last_route_family=context.followup_state.last_route_family,
                repo_name=repo.name if repo is not None else context.durable_repo_memory.repo_name if context.durable_repo_memory is not None else context.thread_state.active_repo_name,
            )
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "conversation_restate":
        repo = context.mentioned_repo or context.active_repo
        options = tuple(
            reply_factory(
                text,
                intent="conversation_restate",
                specialist_name=specialist_name,
            )
            for text in restatement_reply_texts(
                last_route_family=context.followup_state.last_route_family,
                repo_name=repo.name if repo is not None else context.durable_repo_memory.repo_name if context.durable_repo_memory is not None else None,
                recommendation_primary=context.durable_recommendation.primary_repo,
            )
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "next_step_guidance":
        repo = context.mentioned_repo or context.active_repo
        workflow = context.workflow_state
        if workflow is not None and workflow.active_issue_kind and workflow.repo_name:
            issue_summary = workflow.active_issue_summary or "Duckln is still carrying the latest repo issue."
            if str(workflow.active_repair_phase or "").strip().lower().startswith("awaiting_"):
                return reply_factory(
                    join_blocks(
                        f"Duckln is paused on {workflow.repo_name} because the current step still needs your approval.",
                        issue_summary,
                        "If you want to continue, tell me to proceed and I’ll stay on this workflow instead of switching to a fresh question.",
                    ),
                    intent="next_step_guidance",
                    specialist_name=specialist_name,
                )
            return reply_factory(
                join_blocks(
                    f"The next bounded step is still to resolve the current {workflow.repo_name} issue, not to jump to a fresh repo recommendation.",
                    issue_summary,
                    "If you want, I can stay on that repo and either retry the run path or narrow the smallest repair step.",
                ),
                intent="next_step_guidance",
                specialist_name=specialist_name,
            )
        offer_kind = context.pending_offer.kind
        if offer_kind == "recommend_best_fit":
            return reply_factory(
                "If you want me to choose, the next step is simple: say yes and I’ll pick the best repo for this machine.",
                intent="next_step_guidance",
                specialist_name=specialist_name,
            )
        if offer_kind == "inspect_requirements" and repo is not None:
            return reply_factory(
                f"If you want to keep moving on {repo.name}, I’d inspect its requirements next so we know whether local is still the right path.",
                intent="next_step_guidance",
                specialist_name=specialist_name,
            )
        if offer_kind == "set_up_repo" and repo is not None:
            return reply_factory(
                f"If you want to stay on {repo.name}, the next bounded step is setup.",
                intent="next_step_guidance",
                specialist_name=specialist_name,
            )
        if repo is not None:
            return reply_factory(
                join_blocks(
                    f"If you want to keep going with {repo.name}, the next bounded step is to check its status or requirements before changing anything.",
                    "If you want a fresh recommendation instead, ask me to pick one for this machine.",
                ),
                intent="next_step_guidance",
                specialist_name=specialist_name,
            )
        return reply_factory(
            "The cleanest next step is to tell me whether you want a repo recommendation, a setup check, or help with something Duckln already tracked.",
            intent="next_step_guidance",
            specialist_name=specialist_name,
        )

    if intent == "feedback_style":
        return reply_factory(
            "Fair point. I was sounding too mechanical there. I’ll keep the next replies more direct and grounded to your repo and this machine.",
            intent="feedback_style",
            specialist_name=specialist_name,
        )

    if intent == "setup_request":
        if any(keyword in normalized_compact for keyword in ("whisper", "faster-whisper", "whisperx", "bark", "speechbrain", "coqui")):
            return reply_factory(
                "The cleanest move is to pick the repo in /repos first. From there I can keep the setup path local if it fits, or pivot to a VM if that is the safer path.",
                intent="setup_request",
                steer="/repos",
                specialist_name=specialist_name,
            )
        if any(keyword in normalized_compact for keyword in ("repo", "repository", "project")):
            return reply_factory(
                "Pick the repo in /repos and I’ll keep the setup path as small and verifiable as I can.",
                intent="setup_request",
                steer="/repos",
                specialist_name=specialist_name,
            )
        if any(keyword in normalized_compact for keyword in ("provider", "openrouter", "openai", "anthropic", "ollama")):
            return reply_factory(
                "Use /provider and I’ll keep the provider change isolated from everything else.",
                intent="setup_request",
                steer="/provider",
                specialist_name=specialist_name,
            )
        return reply_factory(
            "Tell me the tool or repo you want to set up and I’ll point you to the smallest useful path.",
            intent="setup_request",
            specialist_name=specialist_name,
        )

    if intent == "help_request":
        if any(token in normalized_compact for token in ("whisper", "faster-whisper", "whisperx", "bark", "speechbrain", "coqui")):
            return reply_factory(
                "For Whisper-style repos, the cleanest path is usually to choose the repo first and then decide whether local or VM setup makes more sense.",
                intent="help_request",
                steer="/repos",
                specialist_name=specialist_name,
            )
        options = (
            reply_factory(
                "Tell me what you’re trying to change or set up and I’ll keep the next step narrow.",
                intent="help_request",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Name the repo, provider, model, or failure and I’ll point to the smallest next move.",
                intent="help_request",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "If you’re not sure where to start, /help shows the command surface and /repos is the quickest repo entry point.",
                intent="help_request",
                specialist_name=specialist_name,
            ),
        )
        return pick_reply(options, normalized_compact, recent_replies)

    if intent == "command_hint":
        command_hints = (
            ("provider", "/provider", "switch or reconnect a provider"),
            ("model", "/model", "pick a different model"),
            ("mode", "/mode", "change how much Duckln can do"),
            ("repo", "/repos", "browse and set up a repository"),
            ("repository", "/repos", "browse and set up a repository"),
            ("vm", "/vm", "create or configure an Ubuntu VM"),
            ("health", "/healthcheck", "run a quick environment check"),
            ("config", "/config", "open the configuration menu"),
        )
        for keyword, command_name, purpose in command_hints:
            if keyword in normalized_compact:
                if last_reply is not None and last_reply.steer == command_name and keyword in last_user_input:
                    return reply_factory(
                        f"{command_name} is still the right path for that. I can stay with you while you use it.",
                        intent="command_hint",
                        steer=command_name,
                        specialist_name=specialist_name,
                    )
                return reply_factory(
                    f"For that, start with {command_name} to {purpose}.",
                    intent="command_hint",
                    steer=command_name,
                    specialist_name=specialist_name,
                )

    if intent == "generic":
        options = (
            reply_factory(
                "Tell me what you want to set up, fix, or change and I’ll keep the next step small.",
                intent="generic",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "Give me the repo, provider, model, or blocker and I’ll point to the shortest useful next move.",
                intent="generic",
                specialist_name=specialist_name,
            ),
            reply_factory(
                "If you want the command surface, use /help. If you already know the task, say it plainly and I’ll respond directly.",
                intent="generic",
                steer="/help",
                specialist_name=specialist_name,
            ),
        )
        return pick_reply(options, normalized_compact, recent_replies)

    return None
