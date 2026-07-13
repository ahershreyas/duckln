"""Recommendation scoring and support helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.probe import SystemProbe
from duckln.agent_context import build_hardware_reasoning, build_repo_feasibility_assessment
from state.repo_catalog import RepoCatalogRecord
from state.store import initialize_state_store


@dataclass(frozen=True)
class RecommendationCandidate:
    """Ranked repo recommendation with fit context."""

    repo: RepoCatalogRecord
    score: float
    fit_reason: str
    caveat: str
    downgrade_reason: str
    hardware_summary: str
    fit_label: str
    trust_summary: str


def recommendation_system_hint(system_probe: SystemProbe) -> str:
    if system_probe.is_apple_silicon:
        return "Apple Silicon Mac"
    if system_probe.operating_system == "Darwin":
        return "Mac"
    if system_probe.gpu.cuda_available:
        return "CUDA-ready system"
    return system_probe.operating_system.lower()


def machine_explanation(system_probe: SystemProbe) -> str:
    if system_probe.is_apple_silicon:
        return "The probe shows macOS on Apple Silicon, so I’m using that when I suggest repos and setup paths."
    if system_probe.operating_system == "Darwin":
        return "The probe shows macOS, so I’m using that to keep the setup path realistic for your machine."
    if system_probe.gpu.cuda_available:
        return "The probe shows a CUDA-ready system, so I can factor GPU-heavy repos into the recommendation."
    return f"The probe shows {system_probe.operating_system} on {system_probe.architecture}, so I’m grounding the answer in that local system."


def fit_label_text(fit_label: str) -> str:
    if fit_label == "comfortable":
        return "a comfortable fit"
    if fit_label == "workable_but_tight":
        return "workable, but tight"
    return "not a good fit"


def fit_label_sentence(fit_label: str) -> str:
    if fit_label == "comfortable":
        return "It looks comfortable on this machine."
    if fit_label == "workable_but_tight":
        return "It should work, but it will be tight."
    return "I would not call it a good fit on this machine."


def short_caveat_text(caveat: str) -> str:
    cleaned = caveat.strip().rstrip(".")
    if not cleaned:
        return "the exact workload still matters"
    return cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()


def short_reason_text(reason: str) -> str:
    cleaned = reason.strip().rstrip(".")
    if not cleaned:
        return "it has the cleanest path here"
    return cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()


def repo_knowledge_lookup(config_dir: Path | None) -> dict[str, object]:
    if config_dir is None:
        return {}
    store = initialize_state_store(config_dir)
    lookup: dict[str, object] = {}
    for record in store.list_repo_knowledge_records():
        lookup[record.repo_key.lower()] = record
        lookup[record.repo_name.lower()] = record
        if record.repo_url:
            lookup[record.repo_url.lower()] = record
    return lookup


def promoted_heuristic_lookup(config_dir: Path | None) -> dict[str, dict[str, object]]:
    if config_dir is None:
        return {}
    lookup: dict[str, dict[str, object]] = {}
    for record in initialize_state_store(config_dir).list_promoted_heuristics():
        lookup[record.subject_key.lower()] = {
            "family": record.family,
            "summary": record.summary,
            "confidence": record.confidence,
            "metadata": record.metadata,
        }
    return lookup


def has_promoted_style_feedback(promoted_heuristics: tuple[dict[str, object], ...]) -> bool:
    for heuristic in promoted_heuristics:
        if heuristic.get("family") != "conversation":
            continue
        subject_key = str(heuristic.get("subject_key") or "")
        if "feedback_style" in subject_key:
            return True
    return False


def looks_like_repo_knowledge(value: object | None) -> bool:
    return value is not None and all(
        hasattr(value, attr)
        for attr in ("cpu_profile", "ram_profile", "gpu_profile", "metadata")
    )


def hardware_summary_for_candidate(system_probe: SystemProbe, knowledge: object | None) -> str:
    if looks_like_repo_knowledge(knowledge):
        reasoning = build_hardware_reasoning(system_probe=system_probe, repo_knowledge=knowledge)
        return reasoning.summary
    if system_probe.gpu.cuda_available:
        return "Your compute path is CUDA-capable, so Duckln can consider heavier GPU-first repos when the rest of the machine fit is clean."
    if system_probe.gpu.mps_capable:
        return "Your compute path is MPS-capable, so Duckln favors MPS-safe or CPU-safe repos over CUDA-heavy ones."
    return "Your compute path is CPU-only, so Duckln favors lighter repos that do not rely on accelerator-heavy workflows."


def knowledge_blob(knowledge: object | None) -> str:
    if knowledge is None:
        return ""
    parts = [
        str(getattr(knowledge, "summary", "") or ""),
        str(getattr(knowledge, "cpu_profile", "") or ""),
        str(getattr(knowledge, "ram_profile", "") or ""),
        str(getattr(knowledge, "gpu_profile", "") or ""),
        str(getattr(knowledge, "apple_silicon_notes", "") or ""),
    ]
    return " ".join(parts).lower()


def first_distinct_reason(options: list[str], *, fallback: str) -> str:
    seen: set[str] = set()
    for option in options:
        normalized = " ".join(option.split()).strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        return normalized.rstrip(".")
    return fallback.rstrip(".")


def find_recommendation_candidate(
    candidates: tuple[RecommendationCandidate, ...] | list[RecommendationCandidate],
    repo_name: str,
) -> RecommendationCandidate | None:
    lowered = repo_name.strip().lower()
    if not lowered:
        return None
    for candidate in candidates:
        if candidate.repo.name.lower() == lowered:
            return candidate
    return None


def requested_repo_comparison_candidates(
    normalized_compact: str,
    recommendations: tuple[RecommendationCandidate, ...],
) -> tuple[RecommendationCandidate, ...]:
    selected: list[RecommendationCandidate] = []
    for candidate in recommendations:
        aliases = {
            candidate.repo.name.lower(),
            candidate.repo.name.lower().replace("-", " "),
        }
        if any(alias in normalized_compact for alias in aliases):
            selected.append(candidate)
    if len(selected) < 2 and ("whisperx" in normalized_compact or "whisper x" in normalized_compact):
        whisperx = find_recommendation_candidate(recommendations, "whisperx")
        whisper = find_recommendation_candidate(recommendations, "whisper")
        if whisperx is not None and whisper is not None:
            return (whisperx, whisper)
    deduped: list[RecommendationCandidate] = []
    seen: set[str] = set()
    for candidate in selected:
        key = candidate.repo.name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return tuple(deduped)


def score_recommendation_candidates(
    records: tuple[RepoCatalogRecord, ...],
    system_probe: SystemProbe,
    *,
    config_dir: Path | None,
    normalized_compact: str,
) -> tuple[RecommendationCandidate, ...]:
    if not records:
        return ()

    repo_knowledge = repo_knowledge_lookup(config_dir)
    heuristic_lookup = promoted_heuristic_lookup(config_dir)
    wants_lightweight = any(token in normalized_compact for token in ("lightweight", "light", "small", "simple", "smallest"))
    ram_gib = (system_probe.ram_bytes or 0) / (1024**3) if system_probe.ram_bytes else None
    cpu_cores = system_probe.cpu_logical_cores
    usable_ram_gib = None if ram_gib is None else max(0.0, ram_gib - (3.0 if ram_gib <= 16 else 4.0))
    disk_free_gib = (system_probe.disk_free_bytes or 0) / (1024**3) if system_probe.disk_free_bytes else None

    candidates: list[RecommendationCandidate] = []
    for record in records:
        knowledge = repo_knowledge.get(record.repo_url.lower()) or repo_knowledge.get(record.name.lower())
        name_key = record.name.lower()
        category_key = record.category.lower()
        framework_key = record.framework.lower()
        feasibility = build_repo_feasibility_assessment(
            system_probe=system_probe,
            repo=record,
            repo_knowledge=knowledge if looks_like_repo_knowledge(knowledge) else None,
        )
        score = 50.0
        fit_reasons: list[str] = []
        caveats: list[str] = []
        downgrade_reasons: list[str] = []

        if feasibility.fit_label == "comfortable":
            score -= 6
            fit_reasons.append("it looks comfortable for this system")
        elif feasibility.fit_label == "workable_but_tight":
            score += 2
            fit_reasons.append("it is workable but still tight on this system")
        else:
            score += 11
            downgrade_reasons.append("it is not recommended for this system profile")
        if feasibility.failure_modes:
            caveats.append("; ".join(feasibility.failure_modes[:2]))

        if system_probe.is_apple_silicon:
            if category_key in {"audio", "llm"}:
                score -= 12
                fit_reasons.append("it fits Apple Silicon without leaning on CUDA")
            if "stable diffusion" in category_key or "cuda" in framework_key:
                score += 18
                caveats.append("it leans on heavier GPU-oriented setup")
                downgrade_reasons.append("it carries more GPU friction on Apple Silicon")
        elif system_probe.gpu.cuda_available:
            if "stable diffusion" in category_key:
                score -= 8
                fit_reasons.append("your CUDA-ready system can support heavier GPU-first repos")
            if category_key == "audio":
                score -= 4
                fit_reasons.append("audio repos stay practical even without using the full GPU path")
        else:
            if "stable diffusion" in category_key:
                score += 14
                caveats.append("it is much heavier without a CUDA-ready GPU")
                downgrade_reasons.append("it is a poorer fit on a non-CUDA machine")
            if category_key in {"audio", "llm"}:
                score -= 8
                fit_reasons.append("it is realistic on a CPU or lighter local path")

        if wants_lightweight:
            if category_key in {"audio", "llm"}:
                score -= 6
                fit_reasons.append("it matches the lighter-weight setup you asked for")
            if "stable diffusion" in category_key:
                score += 10
                downgrade_reasons.append("it is not a lightweight first repo")

        if ram_gib is not None:
            if ram_gib < 12 and ("16 gb" in knowledge_blob(knowledge) or "16-32" in knowledge_blob(knowledge)):
                score += 8
                caveats.append("your available RAM makes it a tighter fit")
                downgrade_reasons.append("it is likely to want more memory than this machine exposes comfortably")
            elif ram_gib >= 16 and category_key in {"audio", "llm"}:
                score -= 3
                fit_reasons.append("your RAM is enough for a comfortable first local run")
            if usable_ram_gib is not None and category_key in {"audio", "llm"} and usable_ram_gib < 10:
                caveats.append("after system overhead, the usable RAM budget is still fairly tight")
            if usable_ram_gib is not None and ("stable diffusion" in category_key or name_key in {"comfyui", "stable-diffusion-webui"}):
                if usable_ram_gib < 12:
                    score += 12
                    caveats.append("the usable RAM budget is tight for a comfortable diffusion workflow")
                    downgrade_reasons.append("it is likely to feel cramped on this machine after normal system overhead")
        else:
            caveats.append("Duckln could not read the full RAM profile, so this is a bounded estimate")

        if cpu_cores is not None and cpu_cores >= 8 and category_key in {"audio", "llm"}:
            score -= 2
            fit_reasons.append("your CPU headroom is enough for a practical local start")
        elif cpu_cores is None:
            caveats.append("Duckln could not confirm the CPU core count, so this is a bounded estimate")

        if knowledge is not None:
            complexity = (knowledge.setup_complexity or "").lower()
            if complexity == "low":
                score -= 5
                fit_reasons.append("its setup path is relatively light")
            elif complexity == "high":
                score += 7
                caveats.append("its setup path is heavier than the simpler options")
                downgrade_reasons.append("it takes a heavier setup path than the smaller options")
            if knowledge.local_vm_recommendation and "local" in knowledge.local_vm_recommendation.lower():
                score -= 2
            if knowledge.local_vm_recommendation and "vm" in knowledge.local_vm_recommendation.lower():
                score += 4
                caveats.append("it may push you toward a VM or more careful environment handling")
            if knowledge.apple_silicon_notes and system_probe.is_apple_silicon and "not" in knowledge.apple_silicon_notes.lower():
                score += 8
                caveats.append(knowledge.apple_silicon_notes)
                downgrade_reasons.append(knowledge.apple_silicon_notes)

        if name_key in {"localai", "open-webui"}:
            score -= 2
            fit_reasons.append("it can give you a practical local-first experience without a huge setup jump")
        if name_key == "open-webui":
            caveats.append("you still need an underlying model runtime behind the UI")
        if name_key == "ollama":
            caveats.append("it is a runtime foundation more than a complete end-user app")
        if name_key == "autogpt":
            score += 10
            caveats.append("it is broader and less like a clean first local wow-moment repo")
            downgrade_reasons.append("it is a less reliable first local experience than the smaller curated options")
        if disk_free_gib is not None:
            if disk_free_gib < 20 and ("stable diffusion" in category_key or name_key in {"comfyui", "stable-diffusion-webui"}):
                score += 10
                caveats.append("disk headroom is tight for heavier downloads and model caches")
                downgrade_reasons.append("it is likely to want more download and cache space than this machine has comfortably free")
            elif disk_free_gib >= 40 and category_key in {"audio", "llm"}:
                score -= 1
                fit_reasons.append("your disk headroom is enough for a practical local-first setup")

        heuristic = heuristic_lookup.get((record.repo_url or record.name).lower()) or heuristic_lookup.get(name_key)
        if heuristic is not None:
            if heuristic.get("family") == "setup":
                score -= min(float(heuristic.get("confidence", 0.0)) * 5.0, 5.0)
                fit_reasons.append("Duckln has seen this repo verify successfully on similar bounded paths")
            if heuristic.get("family") == "repair":
                score += min(float(heuristic.get("confidence", 0.0)) * 4.0, 4.0)
                caveats.append("Duckln has seen this repo need repair on prior attempts")

        wow_moment_score = None
        if knowledge is not None:
            wow_moment_score = getattr(knowledge, "metadata", {}).get("wow_moment_score")
        if isinstance(wow_moment_score, (int, float)):
            score -= max(0.0, min(float(wow_moment_score), 1.0) * 2.0)

        score -= min(record.stars / 75000.0, 4.0)
        hardware_summary = hardware_summary_for_candidate(system_probe, knowledge)
        fit_reason = first_distinct_reason(
            fit_reasons,
            fallback="it has one of the cleanest paths from the curated catalog for this machine",
        )
        caveat = first_distinct_reason(
            caveats,
            fallback="it still depends on the exact model size and workflow you want to run",
        )
        downgrade_reason = first_distinct_reason(
            downgrade_reasons,
            fallback=caveat,
        )
        candidates.append(
            RecommendationCandidate(
                repo=record,
                score=score,
                fit_reason=fit_reason,
                caveat=caveat,
                downgrade_reason=downgrade_reason,
                hardware_summary=hardware_summary,
                fit_label=feasibility.fit_label,
                trust_summary=feasibility.trust.summary,
            )
        )

    ordered = sorted(candidates, key=lambda item: (item.score, -item.repo.stars, len(item.repo.name)))
    seen_names: set[str] = set()
    unique: list[RecommendationCandidate] = []
    for candidate in ordered:
        name_key = candidate.repo.name.lower()
        if name_key in seen_names:
            continue
        unique.append(candidate)
        seen_names.add(name_key)
    return tuple(unique)


def recommendation_options(
    candidate: RecommendationCandidate,
    *,
    system_hint: str,
    thread_state: Any,
    recent_replies: tuple[Any, ...],
    join_blocks: Callable[..., str],
    next_step_phrase: Callable[..., str],
) -> tuple[str, ...]:
    repo_name = candidate.repo.name
    reason = short_reason_text(candidate.fit_reason)
    caveat = short_caveat_text(candidate.caveat)
    next_step = next_step_phrase(
        recent_replies=recent_replies,
        thread_state=thread_state,
        inspect_repo_name=repo_name,
    )
    return (
        join_blocks(
            f"On this {system_hint}, I’d start with {repo_name}.",
            f"It fits best here because {reason}.",
            f"The main watch-out is {caveat}.",
            next_step,
        ),
        join_blocks(
            f"On this {system_hint}, I’d still point you to {repo_name} first.",
            f"The practical reason is {reason}.",
            f"One thing to watch is {caveat}.",
            next_step,
        ),
    )


def rationale_options(
    candidate: RecommendationCandidate,
    *,
    alternative: RecommendationCandidate | None = None,
    thread_state: Any,
    recent_replies: tuple[Any, ...],
    join_blocks: Callable[..., str],
    next_step_phrase: Callable[..., str],
) -> tuple[str, ...]:
    repo_name = candidate.repo.name
    reason = short_reason_text(candidate.fit_reason)
    caveat = short_caveat_text(candidate.caveat)
    next_step = next_step_phrase(
        recent_replies=recent_replies,
        thread_state=thread_state,
        inspect_repo_name=repo_name,
    )
    if alternative is not None:
        alt_reason = short_reason_text(alternative.downgrade_reason)
        return (
            join_blocks(
                f"I kept {repo_name} ahead of {alternative.repo.name}.",
                f"{repo_name} fits better here because {reason}.",
                f"{alternative.repo.name} ranked lower because {alt_reason}.",
                next_step,
            ),
            join_blocks(
                f"{repo_name} stayed in front.",
                f"The reason is {reason}.",
                f"{alternative.repo.name} drops behind because {alt_reason}.",
                next_step,
            ),
        )
    return (
        join_blocks(
            f"{repo_name} stayed on top because {reason}.",
            f"The main constraint is {caveat}.",
            next_step,
        ),
        join_blocks(
            f"I put {repo_name} first because {reason}.",
            f"The catch is {caveat}.",
            next_step,
        ),
    )


def compare_options(
    chosen: RecommendationCandidate,
    other: RecommendationCandidate,
    *,
    system_hint: str,
    thread_state: Any,
    recent_replies: tuple[Any, ...],
    join_blocks: Callable[..., str],
    next_step_phrase: Callable[..., str],
) -> tuple[str, ...]:
    chosen_reason = short_reason_text(chosen.fit_reason)
    other_reason = short_reason_text(other.downgrade_reason)
    next_step = next_step_phrase(
        recent_replies=recent_replies,
        thread_state=thread_state,
        inspect_repo_name=chosen.repo.name,
    )
    return (
        join_blocks(
            f"Between {chosen.repo.name} and {other.repo.name}, I'd start with {chosen.repo.name} on this {system_hint}.",
            f"{chosen_reason.capitalize()}.",
            f"{other.repo.name} falls behind because {other_reason}.",
            next_step,
        ),
        join_blocks(
            f"I'd choose {chosen.repo.name} over {other.repo.name} here.",
            f"{chosen_reason.capitalize()}.",
            f"The other one slips mainly because {other_reason}.",
            next_step,
        ),
    )


def alternative_options(
    candidate: RecommendationCandidate,
    *,
    system_hint: str,
    better_name: str | None = None,
    thread_state: Any,
    recent_replies: tuple[Any, ...],
    join_blocks: Callable[..., str],
    next_step_phrase: Callable[..., str],
) -> tuple[str, ...]:
    repo_name = candidate.repo.name
    reason = short_reason_text(candidate.fit_reason)
    caveat = short_caveat_text(candidate.caveat)
    next_step = next_step_phrase(
        recent_replies=recent_replies,
        thread_state=thread_state,
        inspect_repo_name=repo_name,
        allow_compare=False,
    )
    if better_name:
        return (
            join_blocks(
                f"The next best option is {repo_name}.",
                f"It trails {better_name}, but {reason}.",
                f"The main watch-out is {caveat}.",
                next_step,
            ),
            join_blocks(
                f"{repo_name} would be my second pick here.",
                f"It ranks behind {better_name}, but {reason}.",
                f"The tradeoff is {caveat}.",
                next_step,
            ),
        )
    return (
        join_blocks(
            f"{repo_name} would be the next option I'd test here.",
            f"{reason.capitalize()}.",
            f"The main watch-out is {caveat}.",
            next_step,
        ),
        join_blocks(
            f"If you want another route, I'd try {repo_name} next.",
            f"{reason.capitalize()}.",
            f"The tradeoff is {caveat}.",
            next_step,
        ),
    )
