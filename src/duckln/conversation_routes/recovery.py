"""Bounded recovery helpers for clarification, repair, restatement, and fallback."""

from __future__ import annotations

from duckln.conversation_policy import ClarificationOption
from duckln.conversation_routes.repo import looks_like_repo_lifecycle_turn
from duckln.conversation_routes.replies import clarification_options, utility_fallback_options
from duckln.conversation_routes.repair import repair_reply_options, restatement_reply_options


def _candidate_option(
    route_family: str,
    *,
    repo_name: str | None,
) -> ClarificationOption | None:
    if route_family == "capability":
        return ClarificationOption("what Duckln can help with", "capability", "Explain Duckln's terminal lane directly.")
    if route_family == "system_summary":
        return ClarificationOption("system capacity", "system_summary", "Answer from the current machine probe.")
    if route_family == "system_verify":
        return ClarificationOption("run Duckln healthcheck", "system_verify", "Use the bounded healthcheck path instead of stale state.")
    if route_family == "workflow_action":
        return ClarificationOption("repo setup issue", "workflow_action", "Help with setup or the next bounded action.")
    if route_family == "repo_recommendation_single":
        return ClarificationOption("repo recommendation", "repo_recommendation_single", "Pick the best repo for this machine.")
    if route_family == "repo_recommendation_rationale":
        return ClarificationOption("why that repo", "repo_recommendation_rationale", "Explain the current recommendation.")
    if route_family == "repo_alternatives":
        return ClarificationOption("another repo option", "repo_alternatives", "Continue with alternatives instead of the first pick.")
    if route_family == "repo_overview":
        label = f"what {repo_name} is" if repo_name else "what that repo is"
        return ClarificationOption(label, "repo_overview", "Give the short repo overview.")
    if route_family == "repo_requirements":
        label = f"{repo_name} requirements" if repo_name else "repo requirements"
        return ClarificationOption(label, "repo_requirements", "Check whether the repo fits this machine.")
    if route_family == "repo_inventory":
        return ClarificationOption("all tracked repos", "repo_inventory", "List the repos Duckln currently has tracked.")
    if route_family == "repo_status":
        label = f"whether {repo_name} is installed or active" if repo_name else "active repo status"
        return ClarificationOption(label, "repo_status", "Check the tracked repo state.")
    if route_family == "repo_verify":
        label = f"verify whether {repo_name} still runs cleanly" if repo_name else "verify the active repo"
        return ClarificationOption(label, "repo_verify", "Run a bounded live verification instead of only reading tracked state.")
    if route_family == "repo_memory_meta":
        label = f"what Duckln remembers about {repo_name}" if repo_name else "what Duckln remembers about that repo"
        return ClarificationOption(label, "repo_memory_meta", "Explain tracked repo memory and notes.")
    if route_family == "repo_run":
        return ClarificationOption("run the active repo", "repo_run", "Move toward the bounded run step.")
    if route_family == "conversation_repair":
        return ClarificationOption("explain the last answer plainly", "conversation_repair", "Repair the last answer in simple words.")
    if route_family == "conversation_restate":
        return ClarificationOption("say that again simply", "conversation_restate", "Restate the last answer more plainly.")
    if route_family == "repo_active_explanation":
        return ClarificationOption("what active repo means", "repo_active_explanation", "Define Duckln's active repo concept.")
    return None


def _looks_like_repo_lifecycle_ambiguity(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if looks_like_repo_lifecycle_turn(cleaned):
        return False
    ambiguous_scope = (
        "already have a repo",
        "already have an repo",
        "do we already have a repo",
        "what repo do we already have",
        "which repo do we already have",
        "repo in process",
    )
    lifecycle_terms = ("installed", "active", "ready", "in process", "worked previously", "worked on previously")
    return (
        "repo" in cleaned
        and (
            any(marker in cleaned for marker in ambiguous_scope)
            or ("already have" in cleaned and any(term in cleaned for term in lifecycle_terms))
        )
    )


def clarification_options_for_message(
    normalized_compact: str,
    *,
    context,
    looks_like_repo_pronoun_followup,
) -> tuple[ClarificationOption, ...]:
    """Return short clarification choices for ambiguous turns."""

    options: list[ClarificationOption] = []
    repo = context.mentioned_repo or context.active_repo
    if _looks_like_repo_lifecycle_ambiguity(normalized_compact):
        options.extend(
            (
                ClarificationOption("active repo status", "repo_status", "Check the repo Duckln is currently tracking."),
                ClarificationOption("all tracked repos", "repo_inventory", "List the repos Duckln currently has tracked."),
                ClarificationOption("repo recommendation", "repo_recommendation_single", "Pick the best repo for this machine."),
            )
        )
    if looks_like_repo_pronoun_followup(normalized_compact) and repo is None:
        options.extend(
            (
                ClarificationOption("last recommended repo", "repo_recommendation_rationale", "Continue the last repo recommendation thread."),
                ClarificationOption("active repo status", "repo_status", "Check the repo Duckln is currently tracking."),
                ClarificationOption("verify the active repo", "repo_verify", "Run a bounded live check for the active repo."),
                ClarificationOption("system capacity", "system_summary", "Answer from the current machine probe."),
            )
        )
    elif "run" in normalized_compact and repo is None:
        options.extend(
            (
                ClarificationOption("run the active repo", "repo_run", "Start the repo Duckln already tracks."),
                ClarificationOption("verify the active repo", "repo_verify", "Check whether the active repo still runs cleanly."),
                ClarificationOption("inspect repo requirements", "repo_requirements", "Check whether a repo fits this machine first."),
            )
        )
    elif any(
        phrase in normalized_compact
        for phrase in ("help me with it", "the thing is not working", "what about that one", "what about that repo")
    ):
        options.extend(
            (
                ClarificationOption("repo setup issue", "workflow_action", "Help with bringing a repo up or fixing setup."),
                ClarificationOption("repo recommendation", "repo_recommendation_single", "Choose a repo that fits this machine."),
                ClarificationOption("system capacity", "system_summary", "Explain the current machine capacity directly."),
            )
        )
    if context.followup_state.pending_clarification_options and not _looks_like_repo_lifecycle_ambiguity(normalized_compact):
        for label in context.followup_state.pending_clarification_options[:3]:
            label_text = str(label).strip()
            if not label_text:
                continue
            options.append(ClarificationOption(label_text, "workflow_action", "Continue the last clarification thread."))
    deduped: list[ClarificationOption] = []
    seen: set[str] = set()
    for option in options:
        key = option.label.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    if deduped:
        return tuple(deduped[:3])

    # Plan 192 F2 (Tier 2): the workflow-anchored "continue repairing {repo} / status / path"
    # clarifier ASSUMES repo intent — it must only fire when the MESSAGE carries a repo signal (a
    # repo reference/verb, a lifecycle-ambiguity phrasing, or a pronoun follow-up). A signal-less
    # greeting/small-talk with an active repair objective ("how r you?") must NOT get it — that was
    # the screenshot bug. No signal ⇒ fall through to the generic narrow-down below.
    from duckln.conversation_routes.normalizer import has_repo_signal as _has_repo_signal

    _msg_repo_signal = (
        _has_repo_signal(normalized_compact)
        or _looks_like_repo_lifecycle_ambiguity(normalized_compact)
        or looks_like_repo_pronoun_followup(normalized_compact)
    )
    workflow = getattr(context, "workflow_state", None)
    if _msg_repo_signal and workflow is not None and getattr(workflow, "active_issue_kind", None):
        repo_name = getattr(workflow, "repo_name", None) or getattr(context.thread_state, "active_repo_name", None) or "the current repo"
        return (
            ClarificationOption(f"continue repairing {repo_name}", "next_step_guidance", "Stay on the active workflow instead of changing topics."),
            ClarificationOption(f"{repo_name} status", "repo_status", "Answer from Duckln's tracked repo state."),
            ClarificationOption(f"{repo_name} path", "repo_path", "Show the tracked repo location directly."),
        )

    # Plan 193 F7: the ambient-active-repo clarifier ("active repo status / verify / repo path")
    # ALSO assumes repo intent — it must not fire for a signal-less greeting even when an active
    # repo is present ("how you doin?" was still hitting this). Gate it on a MESSAGE signal too.
    if repo is not None and _msg_repo_signal:
        return (
            ClarificationOption("active repo status", "repo_status", "Check the tracked repo state."),
            ClarificationOption("verify the active repo", "repo_verify", "Run a bounded live check for the active repo."),
            ClarificationOption("repo path", "repo_path", "Show the recorded repo path directly."),
        )

    # No MESSAGE repo signal + an ambient repo ⇒ fall through to the GENERIC (non-repo-assuming)
    # narrow-down below — "ask, don't guess" (the intent-routing brief's ambiguous-turn behavior),
    # NOT the dangerous repo-only clarifier that was gated above.
    return (
        ClarificationOption("repo setup issue", "workflow_action", "Help with the next bounded repo step."),
        ClarificationOption("all tracked repos", "repo_inventory", "List the repos Duckln currently has tracked."),
        ClarificationOption("system capacity", "system_summary", "Answer from the current machine probe."),
    )


def clarification_options_for_candidates(
    candidates,
    *,
    context,
    normalized_compact: str = "",
    existing: tuple[ClarificationOption, ...] = (),
) -> tuple[ClarificationOption, ...]:
    """Derive clarification choices from competing route candidates and current context."""

    repo = context.mentioned_repo or context.active_repo
    repo_name = repo.name if repo is not None else context.durable_repo_memory.repo_name if context.durable_repo_memory is not None else None
    deduped: list[ClarificationOption] = []
    seen: set[str] = set()

    for option in existing:
        key = option.label.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)

    lifecycle_ambiguous = _looks_like_repo_lifecycle_ambiguity(normalized_compact)
    for candidate in candidates:
        if candidate.route_family in {"utility_fallback", "clarify", "small_talk", "user_alias", "user_identity_meta"}:
            continue
        if lifecycle_ambiguous and candidate.route_family in {"repo_recommendation_rationale", "repo_alternatives"}:
            continue
        option = _candidate_option(candidate.route_family, repo_name=repo_name)
        if option is None:
            continue
        key = option.label.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
        if len(deduped) >= 3:
            break

    if deduped:
        return tuple(deduped[:3])

    # Plan 193 F7: the ambient-repo FALLBACK ("active repo status / verify / repo setup issue")
    # assumes repo intent — gate it on a real MESSAGE signal so a signal-less turn with an active
    # repo doesn't get a repo clarifier (Tier-2 flip).
    from duckln.conversation_routes.normalizer import has_repo_signal as _has_repo_signal

    _msg_repo_signal = (
        _has_repo_signal(normalized_compact)
        or _looks_like_repo_lifecycle_ambiguity(normalized_compact)
    )
    if repo_name is not None and _msg_repo_signal:
        return (
            ClarificationOption("active repo status", "repo_status", "Check the tracked repo state."),
            ClarificationOption("verify the active repo", "repo_verify", "Run a bounded live check for the active repo."),
            ClarificationOption("repo setup issue", "workflow_action", "Help with setup or the next bounded action."),
        )
    if context.pending_offer.kind == "recommend_best_fit":
        return (
            ClarificationOption("repo recommendation", "repo_recommendation_single", "Pick the best repo for this machine."),
            ClarificationOption("another repo option", "repo_alternatives", "Continue with alternatives instead of the first pick."),
            ClarificationOption("system capacity", "system_summary", "Answer from the current machine probe."),
        )
    return ()


def clarification_reply_texts(labels: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Render one short clarification question with contextual options."""

    return clarification_options(labels)


def repair_reply_texts(*, last_route_family: str | None, repo_name: str | None) -> tuple[str, ...]:
    """Render plain-language repair replies."""

    return repair_reply_options(last_route_family=last_route_family, repo_name=repo_name)


def restatement_reply_texts(
    *,
    last_route_family: str | None,
    repo_name: str | None,
    recommendation_primary: str | None,
) -> tuple[str, ...]:
    """Render simpler restatement replies."""

    return restatement_reply_options(
        last_route_family=last_route_family,
        repo_name=repo_name,
        recommendation_primary=recommendation_primary,
    )


def utility_fallback_reply_texts(
    *,
    repo_name: str | None,
    shortlist_primary: str | None,
    shortlist_secondary: str | None,
    pending_offer_kind: str | None,
) -> tuple[str, ...]:
    """Render bounded fallback replies that keep the conversation moving."""

    options = utility_fallback_options(
        repo_name=repo_name,
        shortlist_primary=shortlist_primary,
        shortlist_secondary=shortlist_secondary,
        pending_offer_kind=pending_offer_kind,
    )
    return tuple(
        text
        for text in options
        if "second-best repo" not in text.lower() and "full shortlist" not in text.lower()
    ) or options
