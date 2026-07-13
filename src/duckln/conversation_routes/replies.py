"""Reply-shaping helpers extracted from the main conversation supervisor."""

from __future__ import annotations

from duckln.render_blocks import bullet_block, comparison_block, join_blocks, paragraph_block


def clarification_options(labels: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    cleaned = [label.strip() for label in labels if label and label.strip()]
    if not cleaned:
        cleaned = ["repo recommendation", "repo status", "system capacity"]
    if len(cleaned) == 1:
        question = f"Do you mean {cleaned[0]}?"
    elif len(cleaned) == 2:
        question = f"Do you mean {cleaned[0]} or {cleaned[1]}?"
    else:
        question = f"Do you mean {cleaned[0]}, {cleaned[1]}, or {cleaned[2]}?"
    return (
        question,
        f"Let me keep this tight: {question}",
        f"I’m missing one detail. {question}",
    )


def utility_fallback_options(
    *,
    repo_name: str | None,
    shortlist_primary: str | None,
    shortlist_secondary: str | None,
    pending_offer_kind: str | None,
) -> tuple[str, ...]:
    if pending_offer_kind == "recommend_best_fit":
        return (
            "I’m not fully sure what you want yet. Do you want me to pick the best repo, compare the top options, or explain the current shortlist?",
            "I can take this three ways: choose the best repo, compare the shortlist, or step out of recommendations and answer a different repo question.",
        )
    if shortlist_secondary is not None:
        return (
            "I’m not fully sure which recommendation follow-up you mean. Do you want the current best-fit pick, a compare view, or to switch from recommendations to repo status or setup?",
            "I can keep this moving in one of three ways: explain the current pick, compare the leading options, or step out of recommendations and answer the repo question directly.",
        )
    if repo_name is not None:
        return (
            f"I’m not fully sure what part of {repo_name} you mean. Do you want its status, what Duckln remembers, or the next step?",
            f"If you mean {repo_name}, I can explain the repo, check its tracked state, or keep moving with setup.",
        )
    return (
        "I’m not fully sure what you mean yet. We can narrow this to repo setup, repo status, or system fit on this machine.",
        "I’m missing one detail. If you tell me the repo, the state question, or the machine question, I can answer it directly.",
    )


def clarification_reply_texts(labels: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Render clarification replies for supervisor fallback."""

    return clarification_options(labels)


def utility_fallback_reply_texts(
    *,
    repo_name: str | None,
    shortlist_primary: str | None,
    shortlist_secondary: str | None,
    pending_offer_kind: str | None,
) -> tuple[str, ...]:
    """Render utility-fallback replies for supervisor fallback."""

    return utility_fallback_options(
        repo_name=repo_name,
        shortlist_primary=shortlist_primary,
        shortlist_secondary=shortlist_secondary,
        pending_offer_kind=pending_offer_kind,
    )


def memory_meta_summary(
    *,
    config_dir_present: bool,
    recommendation_repo_name: str | None,
    has_session_summary: bool,
    candidate_count: int,
    promoted_count: int,
    heuristic_count: int,
) -> str:
    if not config_dir_present:
        return "Duckln only has the current in-memory turn here, so there is no persisted learning summary to report."
    bits: list[str] = []
    if recommendation_repo_name:
        bits.append(f"Duckln is carrying recommendation context for {recommendation_repo_name}")
    if has_session_summary:
        bits.append("Duckln has a concise session summary for this conversation")
    if candidate_count:
        bits.append(f"{candidate_count} candidate learning signal{'s' if candidate_count != 1 else ''}")
    promoted_total = max(promoted_count, heuristic_count)
    if promoted_total:
        bits.append(f"{promoted_total} promoted heuristic{'s' if promoted_total != 1 else ''}")
    if not bits:
        return "Duckln has not promoted any durable learning from this thread yet. Right now it only has the current bounded session context."
    return "Yes. " + "; ".join(bits) + ". Duckln keeps that memory bounded, so it stores short verified context and promoted heuristics rather than a full transcript."


def repo_memory_meta_summary(*, repo_name: str, facts: tuple[str, ...] | list[str]) -> str:
    cleaned = tuple(fact.strip() for fact in facts if fact and fact.strip())
    if not cleaned:
        return f"Not in any durable way yet. Duckln can discuss {repo_name} right now, but it is not carrying tracked setup state or promoted learning for it."
    return join_blocks(
        f"Yes. Duckln is carrying some bounded memory for {repo_name}.",
        bullet_block(cleaned),
        "It keeps that memory to short tracked context rather than a full transcript.",
    )


def repo_overview_options(
    *,
    repo_name: str,
    description: str,
    practical_bits: tuple[str, ...] | list[str],
    next_step: str | None,
) -> tuple[str, ...]:
    body = tuple(bit.strip() for bit in practical_bits if bit and bit.strip())
    brief_blocks = [paragraph_block(f"{repo_name} is {description}.")]
    if body:
        brief_blocks.append(paragraph_block(" ".join(body)))
    detailed_blocks = list(brief_blocks)
    if next_step:
        detailed_blocks.append(paragraph_block(next_step))
    return (
        join_blocks(*detailed_blocks),
        join_blocks(*brief_blocks),
    )


def repo_overview_reply_texts(
    *,
    repo_name: str,
    description: str,
    practical_bits: tuple[str, ...] | list[str],
    next_step: str | None,
) -> tuple[str, ...]:
    """Render detailed and brief overview replies for a repo."""

    return repo_overview_options(
        repo_name=repo_name,
        description=description,
        practical_bits=practical_bits,
        next_step=next_step,
    )


def repo_inventory_text(inventory: list[dict[str, str]]) -> str:
    pairs = tuple((item["name"], item["status"]) for item in inventory[:4])
    return join_blocks(
        paragraph_block("Duckln is currently tracking these repos:"),
        comparison_block("Tracked repo state", pairs),
    )


def repo_inventory_reply_text(inventory: list[dict[str, str]]) -> str:
    """Render repo inventory output for conversation replies."""

    return repo_inventory_text(inventory)


def repo_inventory_scoped_reply_text(*, title: str, inventory: list[dict[str, str]]) -> str:
    """Render scoped inventory output for local or VM inventory questions."""

    pairs = tuple((item["name"], item["status"]) for item in inventory[:6])
    return join_blocks(
        paragraph_block(title),
        comparison_block("Tracked repo lifecycle", pairs),
    )


def repo_inventory_paths_reply_text(*, title: str, inventory: list[dict[str, str]]) -> str:
    """Render tracked repo inventory with stored install paths."""

    pairs = tuple((item["name"], item["location"]) for item in inventory[:6])
    return join_blocks(
        paragraph_block(title),
        comparison_block("Tracked repo paths", pairs),
    )


def repo_inventory_sources_reply_text(*, title: str, inventory: list[dict[str, str]]) -> str:
    """Render tracked repo inventory with recorded source URLs."""

    pairs = tuple((item["name"], item["source"]) for item in inventory[:6])
    return join_blocks(
        paragraph_block(title),
        comparison_block("Tracked repo sources", pairs),
    )


def repo_live_sessions_reply_text(*, title: str, sessions: list[dict[str, str]]) -> str:
    """Render only live repo runtime sessions."""

    pairs = tuple((item["name"], item["status"]) for item in sessions[:6])
    return join_blocks(
        paragraph_block(title),
        comparison_block("Live repo sessions", pairs),
    )


def repo_lifecycle_inventory_text(inventory: list[dict[str, str]]) -> str:
    pairs = tuple((item["name"], item["status"]) for item in inventory[:6])
    return join_blocks(
        paragraph_block("Here are the repos Duckln currently has tracked as installed, active, or otherwise prepared:"),
        comparison_block("Tracked repo lifecycle", pairs),
    )


def repo_lifecycle_inventory_reply_text(inventory: list[dict[str, str]]) -> str:
    """Render lifecycle-specific repo inventory output for conversation replies."""

    return repo_lifecycle_inventory_text(inventory)


def repo_status_text(*, repo_name: str, status: str, location: str | None) -> str:
    blocks: list[object] = [paragraph_block(f"Duckln currently has {repo_name} tracked as {status} on this system.")]
    if location:
        blocks.append(paragraph_block(f"It is recorded at {location}."))
    return join_blocks(*blocks)


def repo_status_reply_text(*, repo_name: str, status: str, location: str | None) -> str:
    """Render repo status output for conversation replies."""

    return repo_status_text(repo_name=repo_name, status=status, location=location)


def repo_lifecycle_status_text(*, repo_name: str, status: str, location: str | None) -> str:
    blocks: list[object] = [paragraph_block(f"Yes. Duckln currently has {repo_name} tracked as {status}.")]
    if location:
        blocks.append(paragraph_block(f"It is recorded at {location}."))
    return join_blocks(*blocks)


def repo_lifecycle_status_reply_text(*, repo_name: str, status: str, location: str | None) -> str:
    """Render lifecycle-specific repo status output for conversation replies."""

    return repo_lifecycle_status_text(repo_name=repo_name, status=status, location=location)


def repo_path_reply_text(
    *,
    repo_name: str,
    location: str | None,
    execution_target: str,
    vm_name: str | None,
    docker_name: str | None = None,
    cloud_vendor: str | None = None,
    cloud_region: str | None = None,
) -> str:
    """Render repo path output for conversation replies."""

    if not location:
        return f"Duckln does not currently have a stored path for {repo_name}."
    target_text = "local machine"
    if execution_target == "vm":
        target_text = f"VM {vm_name}" if vm_name else "the active VM"
    elif execution_target == "docker":
        target_text = f"Docker container {docker_name}" if docker_name else "the active Docker target"
    elif execution_target in {"aws", "gcp"}:
        provider = cloud_vendor or execution_target.upper()
        if cloud_region:
            target_text = f"{provider} cloud target in {cloud_region}"
        else:
            target_text = f"{provider} cloud target"
    return join_blocks(
        paragraph_block(f"{repo_name} is tracked at {location}."),
        paragraph_block(f"That path belongs to the {target_text}."),
    )
