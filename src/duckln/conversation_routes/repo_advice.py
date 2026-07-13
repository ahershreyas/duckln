"""Repo advice and evaluation replies extracted from the conversation specialist."""

from __future__ import annotations

from typing import Any, Callable


def build_repo_advice_reply(
    *,
    intent: str,
    normalized_compact: str,
    context: Any,
    config_dir,
    system_probe,
    recent_replies: tuple[Any, ...],
    specialist_name: str,
    reply_factory: Callable[..., Any],
    pick_reply: Callable[[tuple[Any, ...], str, tuple[Any, ...]], Any],
    coverage_options: Callable[..., tuple[str, ...]],
    score_recommendation_candidates: Callable[..., tuple[Any, ...]],
    repo_requirement_reply: Callable[..., str],
    repo_overview_reply_texts: Callable[..., tuple[str, ...]],
    next_step_phrase: Callable[..., str],
    fit_label_text: Callable[[str], str],
    fit_label_sentence: Callable[[str], str],
    natural_trust_note: Callable[..., str],
    should_surface_trust_note: Callable[..., bool],
    build_hardware_reasoning: Callable[..., Any],
    build_repo_feasibility_assessment: Callable[..., Any],
    verified_fact_summary: Callable[..., str | None],
) -> Any | None:
    if intent == "repo_capability_coverage":
        if context.records:
            sample = ", ".join(record.name for record in context.records[:4])
            if any(token in normalized_compact for token in ("my system", "this machine", "my machine", "my laptop")):
                realistic = score_recommendation_candidates(
                    context.records,
                    system_probe,
                    config_dir=config_dir,
                    normalized_compact="best fit for my system",
                )
                realistic_names = ", ".join(candidate.repo.name for candidate in realistic[:3])
                options = tuple(
                    reply_factory(
                        text,
                        intent="repo_capability_coverage",
                        specialist_name=specialist_name,
                        pending_offer_kind="recommend_best_fit",
                        pending_offer_label="pick the best repo for this machine",
                    )
                    for text in coverage_options(sample=sample, realistic_names=realistic_names)
                )
                return pick_reply(options, normalized_compact, recent_replies)
            options = tuple(
                reply_factory(
                    text,
                    intent="repo_capability_coverage",
                    specialist_name=specialist_name,
                )
                for text in coverage_options(sample=sample)
            )
            return pick_reply(options, normalized_compact, recent_replies)
        return reply_factory(
            "Once the curated catalog is loaded, I can recommend, set up, run, verify, and help remove those repos from here.",
            intent="repo_capability_coverage",
            specialist_name=specialist_name,
        )

    if intent == "repo_requirements":
        repo = context.mentioned_repo
        repo_knowledge = context.repo_knowledge
        if repo is not None and repo_knowledge is not None:
            return reply_factory(
                repo_requirement_reply(
                    repo_knowledge,
                    system_probe,
                    recent_replies=recent_replies,
                ),
                intent="repo_requirements",
                specialist_name=specialist_name,
            )
        return reply_factory(
            "I can answer that once I know the repo. Name it directly and I’ll estimate the practical CPU, RAM, and setup weight for this machine.",
            intent="repo_requirements",
            specialist_name=specialist_name,
        )

    if intent == "repo_overview":
        repo = context.mentioned_repo
        repo_knowledge = context.repo_knowledge
        if repo is not None:
            description = repo.description.strip().rstrip(".") if repo.description else repo.name
            practical_bits: list[str] = []
            if repo_knowledge is not None and repo_knowledge.setup_complexity:
                practical_bits.append(f"Setup looks {repo_knowledge.setup_complexity.lower()}.")
            if repo_knowledge is not None and repo_knowledge.required_tools:
                practical_bits.append(f"You'd likely want {', '.join(repo_knowledge.required_tools[:3])} ready.")
            next_step = next_step_phrase(
                recent_replies=recent_replies,
                thread_state=context.thread_state,
                inspect_repo_name=repo.name,
                allow_compare=False,
            )
            options = tuple(
                reply_factory(
                    text,
                    intent="repo_overview",
                    specialist_name=specialist_name,
                )
                for text in repo_overview_reply_texts(
                    repo_name=repo.name,
                    description=description,
                    practical_bits=tuple(practical_bits),
                    next_step=next_step,
                )
            )
            return pick_reply(options, normalized_compact, recent_replies)
        return reply_factory(
            "Name the repo directly and I’ll give you a quick idea of what it is, how heavy it looks, and whether it makes sense on this machine.",
            intent="repo_overview",
            specialist_name=specialist_name,
        )

    if intent == "repo_fit_judgment":
        repo = context.mentioned_repo
        repo_knowledge = context.repo_knowledge
        if repo is not None and repo_knowledge is not None:
            hardware = build_hardware_reasoning(system_probe=system_probe, repo_knowledge=repo_knowledge)
            feasibility = build_repo_feasibility_assessment(
                system_probe=system_probe,
                repo=repo,
                repo_knowledge=repo_knowledge,
                verified_summary=verified_fact_summary(config_dir, repo),
            )
            ram_text = ""
            if hardware.minimum_recommended_ram_gib is not None and hardware.comfortable_ram_gib is not None:
                ram_text = (
                    f" I’d treat about {hardware.minimum_recommended_ram_gib:.0f} GiB as the tight lower bound and "
                    f"{hardware.comfortable_ram_gib:.0f} GiB as the more comfortable target."
                )
            trust_note = natural_trust_note(feasibility=feasibility, repo_knowledge=repo_knowledge) if should_surface_trust_note(
                recent_replies=recent_replies,
                feasibility=feasibility,
                repo_knowledge=repo_knowledge,
            ) else ""
            options = (
                reply_factory(
                    f"{repo.name} looks {fit_label_text(feasibility.fit_label)} on this machine. {hardware.summary}{ram_text}{trust_note}",
                    intent="repo_fit_judgment",
                    specialist_name=specialist_name,
                ),
                reply_factory(
                    f"For {repo.name}, I’d call it {feasibility.fit_label.replace('_', ' ')} here. {hardware.summary}{ram_text}{trust_note}",
                    intent="repo_fit_judgment",
                    specialist_name=specialist_name,
                ),
                reply_factory(
                    f"{fit_label_sentence(feasibility.fit_label)} For {repo.name}, the main reason is {hardware.summary[0].lower() + hardware.summary[1:]}{ram_text}{trust_note}",
                    intent="repo_fit_judgment",
                    specialist_name=specialist_name,
                ),
            )
            return pick_reply(options, normalized_compact, recent_replies)
        return reply_factory(
            "Name the repo directly and I’ll judge whether it should run comfortably on this machine or only in a tighter fallback mode.",
            intent="repo_fit_judgment",
            specialist_name=specialist_name,
        )

    return None
