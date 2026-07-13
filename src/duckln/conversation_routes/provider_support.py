"""Provider-backed conversation and polish helpers."""

from __future__ import annotations

from typing import Any, Callable
import os
import platform

from agent.probe import GpuProbeState, SystemProbe
from duckln.ai_client import ProviderRequestError, generate_provider_reply
from duckln.config import AppConfig
from duckln.context_assembler import assemble_supervisor_prompt_context
from duckln.conversation_policy import RouteDecision, conversation_examples_for_family
from duckln.tool_registry import tool_policy_summary

from functools import lru_cache

# Prompt-bloat guard: pure conversational/greeting routes where the tool/infra policy is
# irrelevant (the conversation agent is the voice, it executes nothing). On these the tool
# policy block is DROPPED from the system prompt so a small model isn't muddied. `capability`
# is deliberately EXCLUDED (a "do you support docker/vm?" turn wants the tool-list signal),
# as are all repo/action routes (they may act).
_TOOL_POLICY_SKIP_FAMILIES = {
    None,
    "small_talk",
    "rapport",
    "help_request",
    "user_identity_meta",
    "user_alias",
    "conversation_repair",
    "conversation_restate",
}

# Plan 157 P5: the literal persona, kept ONLY as a safe fallback if the optional
# conversation_agent.md spec is missing (the primary conversation must never break).
_PERSONA_FALLBACK = (
    "You are Duckln, a terminal-first mentor and bounded execution agent. "
    "Reply in 1-2 short sentences. Sound calm, practical, and human. "
    "Answer the user directly first, then suggest the next useful terminal action only if it clearly helps. "
    "Do not be chatty, do not roleplay, do not dump command lists, and do not claim work was done unless it was verified. "
    "Answer normal conversation naturally, keep trust markers implicit unless uncertainty matters, and plan before repo-changing work."
)


@lru_cache(maxsize=1)
def _conversation_persona_base() -> str:
    """Plan 157 P5: the STATIC conversation persona, sourced from conversation_agent.md
    (the single source of truth). Falls back to the literal text only if the spec can't be
    loaded — the primary conversation must always have a voice."""
    try:
        from duckln.harness.agent_def import builtin_agents_directory, load_agent_definition_from_path

        body = load_agent_definition_from_path(builtin_agents_directory() / "conversation_agent.md").system_prompt
        return body.strip() or _PERSONA_FALLBACK
    except Exception:
        return _PERSONA_FALLBACK


def build_provider_backed_reply(
    *,
    current: AppConfig | None,
    message: str,
    intent: str,
    system_probe: SystemProbe,
    config_dir,
    context,
    recent_turns,
    client,
    specialist_name: str,
    reply_factory,
    recommendation_system_hint: Callable[[SystemProbe], str],
    route_family_for_intent: Callable[[str], str],
):
    if current is None or not can_use_live_conversation(current):
        raise ProviderRequestError("Live conversation is not configured.", "no live conversation provider")

    reply = generate_provider_reply(
        current.provider,
        model_id=current.model,
        api_key=current.api_key,
        base_url=current.base_url,
        config_dir=config_dir,
        system_prompt=conversation_system_prompt(
            current,
            system_probe,
            config_dir=config_dir,
            context=context,
            execution_target=context.execution_target,
            active_vm_name=context.active_vm_name,
            route_family=route_family_for_intent(intent),
            recommendation_system_hint=recommendation_system_hint,
            user_message=message,
        ),
        user_message=message,
        recent_turns=tuple(
            (turn.role, turn.content)
            for turn in recent_turns[-2:]
            if turn.role in {"user", "assistant"}
        ),
        client=client,
    )
    # Plan 174 F6: output guard — Duckln never emits vulgar/inappropriate text even if the model slips.
    from duckln.conversation_routes.safety import scrub_reply
    reply = scrub_reply(reply, seed=len(message))
    return reply_factory(
        text=reply,
        intent=intent,
        specialist_name=specialist_name,
        provider_backed=True,
    )


def maybe_refine_reply_with_provider(
    reply,
    *,
    decision: RouteDecision,
    current: AppConfig | None,
    system_probe: SystemProbe,
    config_dir,
    context,
    message: str,
    recent_turns,
    client,
    reply_factory,
    recommendation_system_hint: Callable[[SystemProbe], str],
):
    contract = decision.response_contract
    if not contract.allow_provider_polish or current is None or not can_use_live_conversation(current):
        return reply
    try:
        polished = generate_provider_reply(
            current.provider,
            model_id=current.model,
            api_key=current.api_key,
            base_url=current.base_url,
            config_dir=config_dir,
            system_prompt=conversation_polish_system_prompt(
                current=current,
                system_probe=system_probe,
                config_dir=config_dir,
                context=context,
                decision=decision,
                reply=reply,
                recommendation_system_hint=recommendation_system_hint,
            ),
            user_message=(
                f"User message: {message}\n"
                f"Deterministic draft: {reply.text}\n"
                "Polish the draft only if you can keep every fact, repo choice, fit label, and hardware claim unchanged. "
                "Do not replay stale rationale if this turn is a follow-up."
            ),
            recent_turns=tuple((turn.role, turn.content) for turn in recent_turns[-2:] if turn.role in {"user", "assistant"}),
            client=client,
        ).strip()
    except ProviderRequestError:
        return reply
    # Plan 174 F6: output guard on the polished text too.
    from duckln.conversation_routes.safety import scrub_reply
    polished = scrub_reply(polished, seed=len(message))
    if not polished or not provider_polish_is_safe(polished, reply=reply):
        return reply
    return reply_factory(
        text=polished,
        intent=reply.intent,
        steer=reply.steer,
        specialist_name=reply.specialist_name,
        provider_backed=True,
        action=reply.action,
        action_repo_key=reply.action_repo_key,
        recommendation_repo_key=reply.recommendation_repo_key,
        recommendation_repo_name=reply.recommendation_repo_name,
        recommendation_reason=reply.recommendation_reason,
        recommendation_alternatives=reply.recommendation_alternatives,
        recommendation_caveat=reply.recommendation_caveat,
        hardware_summary=reply.hardware_summary,
        recommendation_fit_label=reply.recommendation_fit_label,
        trust_evidence_summary=reply.trust_evidence_summary,
        route_family=reply.route_family,
        route_confidence=reply.route_confidence,
        response_contract_name=reply.response_contract_name,
        answer_style=reply.answer_style,
        user_alias=reply.user_alias,
        pending_offer_kind=reply.pending_offer_kind,
        pending_offer_label=reply.pending_offer_label,
        pending_offer_repo_key=reply.pending_offer_repo_key,
        pending_offer_repo_name=reply.pending_offer_repo_name,
    )


def provider_polish_is_safe(polished: str, *, reply) -> bool:
    lowered = polished.lower()
    if reply.recommendation_repo_name and reply.recommendation_repo_name.lower() not in lowered:
        return False
    if reply.recommendation_fit_label and reply.recommendation_fit_label.replace("_", " ") not in lowered:
        return False
    if reply.hardware_summary is not None:
        for marker in ("cpu-only", "mps-capable", "cuda-capable"):
            if marker in reply.hardware_summary.lower() and marker not in lowered:
                return False
    if reply.trust_evidence_summary is not None and any(marker in reply.text.lower() for marker in ("detected:", "inferred:", "verified:")):
        for marker in ("detected:", "inferred:"):
            if marker in reply.trust_evidence_summary.lower() and marker not in lowered:
                return False
    return True


def can_use_live_conversation(current: AppConfig) -> bool:
    if not current.model.strip():
        return False
    if current.provider.requires_api_key:
        return bool((current.api_key or "").strip())
    return True


def build_repo_memory_recall(config_dir, repo) -> str:
    """Plan 194 F5/F6 (long-term SELECT-on-return): a BOUNDED, redacted recall of what Duckln
    remembers about a repo — per-repo facts (needs-venv, pkg manager, prior fixes) + relevant user
    preferences. Returns a compact one-liner (empty when nothing is remembered). Reused by the
    conversation grounding (F5) and the repo-open recall surface (F6)."""
    try:
        from state.access import read_repo_facts, read_user_preferences
    except Exception:
        return ""
    slug = str(getattr(repo, "repo_url", "") or getattr(repo, "name", "") or "").strip()
    name = str(getattr(repo, "name", "") or "the repo").strip()
    if not slug:
        return ""
    facts = {}
    try:
        facts = read_repo_facts(config_dir, slug) or {}
    except Exception:
        facts = {}
    fact_bits = [str(v).strip() for v in list(facts.values())[:3] if str(v).strip()]
    pref_bits = []
    try:
        prefs = read_user_preferences(config_dir) or {}
        pref_bits = [f"{k}: {v}" for k, v in list(prefs.items())[:2] if str(v).strip()]
    except Exception:
        pref_bits = []
    if not fact_bits and not pref_bits:
        return ""
    pieces = []
    if fact_bits:
        pieces.append("; ".join(fact_bits))
    if pref_bits:
        pieces.append("prefs — " + ", ".join(pref_bits))
    return f"I remember {name}: " + " · ".join(pieces)


def _repo_memory_slice(config_dir, repo) -> str:
    recall = build_repo_memory_recall(config_dir, repo)
    if not recall:
        return ""
    return ("\nRemembered about this repo (answer from this when the user asks what it needs / how it "
            f"was set up; do not re-derive): {recall[:400]}")


def conversation_system_prompt(
    current: AppConfig,
    system_probe: SystemProbe,
    *,
    config_dir,
    context,
    execution_target: str = "local",
    active_vm_name: str | None = None,
    route_family: str | None = None,
    recommendation_system_hint: Callable[[SystemProbe], str],
    user_message: str | None = None,
) -> str:
    platform_hint = recommendation_system_hint(system_probe)
    vm_hint = "" if execution_target != "vm" else f" The active execution target is a Multipass Ubuntu VM{'' if not active_vm_name else f' named {active_vm_name}'}."
    prompt_context = assemble_supervisor_prompt_context(
        config_dir=config_dir,
        route_family=route_family,
        context=context,
    )
    workspace_text = prompt_context.render()
    # Plan 157 P5: the STATIC persona base is sourced from the conversation_agent.md spec
    # (single source of truth). The ConversationSupervisor router stays deterministic and
    # appends the live runtime context (tool policy, mode/provider/model, platform, vm) here.
    # Prompt-bloat guard (verified on gemma2:9b): the conversation agent is the VOICE — it
    # executes nothing. On a pure social/greeting turn the tool/infra policy (shell runner,
    # multipass, docker, cloud idle shutdown) is irrelevant and only muddies a small model's
    # reasoning, so it is DROPPED there. Capability turns keep it (the tool list is real signal
    # for "do you support docker/vm?"); repo/action routes keep it (they may act).
    tool_policy = "" if route_family in _TOOL_POLICY_SKIP_FAMILIES else f"{tool_policy_summary(execution_target=execution_target)} "
    prompt = (
        f"{_conversation_persona_base()} "
        f"{tool_policy}"
        f"The current mode is {current.mode.label}, provider is {current.provider.label}, model is {current.model}, and the local system is {platform_hint}.{vm_hint}"
    )
    composed = prompt + ("\n" + workspace_text if workspace_text else "")
    # Plan 193 F3 (SELECT + §7 responder): when the turn is NOT pure small talk and a blocker is
    # active this session, inject ONLY the incident slice with a "answer from it, don't be generic"
    # instruction (the doc §7 shape) — so a context-dependent reply is grounded in what just
    # happened. Greetings (small_talk/capability) select nothing (context rot: less, chosen well).
    if route_family not in (None, "small_talk", "capability", "conversation_repair", "conversation_restate"):
        try:
            from state.access import read_workflow_state

            _wf = read_workflow_state(config_dir)
            _inc = str(_wf.get("active_incident_summary") or "").strip()
            if _inc and not _wf.get("active_incident_resolved"):
                composed += (
                    "\nThe user may be referring to the current blocker in this session. If so, answer "
                    "directly from it — do not give a generic reply or ask them to restate what is known: "
                    f"{_inc[:400]}"
                )
        except Exception:
            pass
        # Plan 194 F5 (long-term SELECT-on-return): when the turn concerns a repo, inject a BOUNDED
        # "Remembered about this repo" slice (per-repo facts + user prefs) so chatting about a repo
        # answers from memory instead of re-learning. Only a few relevant facts (context rot).
        try:
            _repo = getattr(context, "mentioned_repo", None) or getattr(context, "active_repo", None)
            if _repo is not None:
                composed += _repo_memory_slice(config_dir, _repo)
        except Exception:
            pass
    # Item: append fresh internet results to the system prompt when the toggle is on
    # and we're talking to a local-only model. Bounded, never silent on miss.
    web_block = maybe_inject_internet_results(
        user_message=str(user_message or "").strip(),
        config_dir=config_dir,
        provider_label=getattr(getattr(current, "provider", None), "label", ""),
    )
    if web_block:
        composed = f"{composed}\n\n{web_block}"
    return composed


def maybe_inject_internet_results(
    *,
    user_message: str,
    config_dir,
    provider_label: str,
) -> str:
    """Item: when /internet is ON and the model is local-only (Ollama/llama-cpp), run a
    bounded DuckDuckGo search and return a plaintext context block to inject as a system
    hint. Returns empty string when internet is off, when the provider already has
    browsing, or when no results came back. Never fails silently — caller surfaces.
    """

    try:
        from duckln.internet_skill import (
            internet_search_summary,
            is_internet_enabled,
            render_search_summary,
        )
    except Exception:
        return ""
    if not is_internet_enabled(config_dir):
        return ""
    # Only inject for providers that don't have built-in browsing. Local models (ollama,
    # llama.cpp) and bare API calls without web tools benefit; skip Anthropic/OpenAI
    # where the model can call its own tools when given them.
    pl = (provider_label or "").strip().lower()
    if any(token in pl for token in ("anthropic", "openai")):
        return ""
    query = (user_message or "").strip()
    if not query:
        return ""
    results = internet_search_summary(query, config_dir=config_dir, max_results=3)
    if not results:
        return ""
    return (
        "Internet search results (DuckDuckGo, snippets only — verify before quoting):\n"
        + render_search_summary(results)
    )


def conversation_polish_system_prompt(
    *,
    current: AppConfig,
    system_probe: SystemProbe,
    config_dir,
    context,
    decision: RouteDecision,
    reply,
    recommendation_system_hint: Callable[[SystemProbe], str],
) -> str:
    platform_hint = recommendation_system_hint(system_probe)
    examples = conversation_examples_for_family(decision.route_family, limit=2)
    example_text = "\n".join(
        f"User: {example.user}\nAssistant: {example.assistant}"
        for example in examples
    )
    prompt_context = assemble_supervisor_prompt_context(
        config_dir=config_dir,
        route_family=decision.route_family,
        context=context,
    )
    workspace_text = prompt_context.render()
    # Plan 185: for a capability/recommendation/"why" turn, let the model EXPLAIN + defend the
    # recommendation in its own words — GROUNDED IN the machine facts injected into the context
    # below — instead of only rewording a canned draft. Keep the repo choice / fit / numbers
    # accurate; everything else stays a strict, fact-preserving polish.
    _why_families = {
        "capability", "repo_capability_coverage", "repo_recommendation_single",
        "repo_recommendation_compare", "repo_recommendation_rationale", "repo_alternatives",
        "recommendation_followup",
    }
    if decision.route_family in _why_families:
        _scope_rule = (
            "You MAY explain the recommendation and the WHY (incl. why-not-local / why-a-VM / GPU need) "
            "in your own words, grounded ONLY in the machine facts in the context below — keep the repo "
            "choice, fit label, hardware numbers, and trust markers accurate, offer honest alternatives, "
            "and stay concise and terminal-first (not waffly). Do not invent hardware or facts not in the context."
        )
        _keep_rule = "Keep every repo choice, fit label, hardware claim, and trust marker accurate."
    else:
        _scope_rule = "Do not add new facts, do not widen scope, and do not turn this into chatty conversation."
        _keep_rule = "Keep every fact, repo choice, fit label, hardware claim, and trust marker unchanged."
    prompt = (
        "You are polishing a deterministic Duckln supervisor reply. "
        f"{_keep_rule} "
        "Keep it terminal-first, concise, and natural. "
        "Answer only the current turn and prefer the next useful delta over replaying the previous answer. "
        f"{_scope_rule} "
        "Tools come from tools.json and execution-target continuity matters. "
        f"Route family: {decision.route_family}. "
        f"Response contract: {decision.response_contract.answer_shape} "
        f"Style: {decision.response_contract.style_name}. "
        f"Surface style: {decision.response_contract.surface_style}. "
        f"Detail level: {decision.response_contract.detail_level}. "
        f"Restatement budget: {decision.response_contract.restatement_budget}. "
        f"Trust rendering: {decision.response_contract.trust_render_mode}. "
        f"Mode: {current.mode.label}. Machine: {platform_hint}. "
        "Examples:\n"
        f"{example_text}\n"
        f"Current draft must preserve: {reply.text}"
    )
    if workspace_text:
        return prompt + "\n" + workspace_text
    return prompt


def lightweight_system_probe() -> SystemProbe:
    operating_system = platform.system() or "Unknown"
    architecture = platform.machine() or "unknown"
    return SystemProbe(
        operating_system=operating_system,
        architecture=architecture,
        cpu_logical_cores=os.cpu_count(),
        ram_bytes=None,
        disk_free_bytes=None,
        python_version=platform.python_version(),
        gpu=GpuProbeState(
            backend="cpu",
            summary="Conversation probe is using CPU-safe fallback signals.",
            cuda_capable=False,
            cuda_available=False,
            mps_capable=operating_system == "Darwin" and architecture == "arm64",
            mps_available=False,
        ),
    )


def format_gib(value: int) -> str:
    return f"{value / (1024**3):.1f}"
