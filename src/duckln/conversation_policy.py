"""Conversation routing policy, examples, and response contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClarificationOption:
    """One short clarification choice shown when intent is ambiguous."""

    label: str
    route_family: str
    description: str


@dataclass(frozen=True)
class ResponseContract:
    """Behavior contract for one route family."""

    route_family: str
    content_obligations: tuple[str, ...]
    style_name: str
    allow_provider_polish: bool = False
    allow_command_steer: bool = False
    example_keys: tuple[str, ...] = ()
    surface_style: str = "natural"
    detail_level: str = "balanced"
    restatement_budget: str = "normal"
    trust_render_mode: str = "implicit"

    @property
    def answer_shape(self) -> str:
        """Backward-compatible string summary for prompts and tests."""

        return "; ".join(self.content_obligations)


@dataclass(frozen=True)
class ConversationExample:
    """Canonical example used to stabilize natural supervisor replies."""

    key: str
    route_family: str
    user: str
    assistant: str


@dataclass(frozen=True)
class ConversationBootstrapHeuristic:
    """Shipped default routing/style heuristic used before learning exists."""

    route_family: str
    phrases: tuple[str, ...]
    base_score: float
    style_bias: str


@dataclass(frozen=True)
class RouteCandidate:
    """Candidate route plus score and routing explanation."""

    route_family: str
    intent: str
    score: float
    reason: str


@dataclass(frozen=True)
class RouteDecision:
    """Final conversation route emitted by the front-door router."""

    route_family: str
    intent: str
    confidence: float
    margin: float
    resolved_subject: str | None
    requires_clarification: bool
    clarification_options: tuple[ClarificationOption, ...]
    response_contract: ResponseContract
    front_door_category: str = "ambiguous"
    front_door_reason: str | None = None
    workflow_priority_applied: bool = False


RESPONSE_CONTRACTS: dict[str, ResponseContract] = {
    "small_talk": ResponseContract(
        route_family="small_talk",
        content_obligations=("answer directly", "sound natural", "optionally invite the next concrete ask"),
        style_name="warm_direct",
        allow_provider_polish=True,
        example_keys=("hi", "how_are_you"),
        detail_level="short",
        restatement_budget="low",
    ),
    "capability": ResponseContract(
        route_family="capability",
        content_obligations=("scope Duckln's lane", "mention terminal work", "stay concise"),
        style_name="scoped_capability",
        allow_provider_polish=True,
        example_keys=("what_can_you_help_me_with",),
        detail_level="short",
        restatement_budget="low",
    ),
    "system_summary": ResponseContract(
        route_family="system_summary",
        content_obligations=("state detected machine facts", "mention compute path", "add one practical note"),
        style_name="grounded_system",
        allow_provider_polish=True,
        example_keys=("system_capacity",),
        detail_level="balanced",
        restatement_budget="medium",
    ),
    "system_verify": ResponseContract(
        route_family="system_verify",
        content_obligations=("treat this as a bounded verification request", "point to the healthcheck path directly", "avoid recommendation fallback"),
        style_name="system_verify",
        detail_level="short",
    ),
    "memory_meta": ResponseContract(
        route_family="memory_meta",
        content_obligations=("state bounded memory honestly", "separate session context from durable learning"),
        style_name="honest_memory",
        allow_provider_polish=True,
        example_keys=("memory_meta",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "vm_info": ResponseContract(
        route_family="vm_info",
        content_obligations=("answer the VM question directly", "separate VM capability from repo recommendation", "stay concrete"),
        style_name="vm_direct",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "vm_repo_recommendation": ResponseContract(
        route_family="vm_repo_recommendation",
        content_obligations=("answer in VM terms", "mention Linux isolation vs no extra GPU", "offer one bounded next move"),
        style_name="vm_recommendation",
        allow_provider_polish=True,
        detail_level="balanced",
        restatement_budget="low",
    ),
    "user_identity_meta": ResponseContract(
        route_family="user_identity_meta",
        content_obligations=("answer the social/meta question directly", "stay brief", "avoid repo-thread hijack"),
        style_name="social_meta",
        allow_provider_polish=True,
        example_keys=("user_identity_meta",),
        detail_level="short",
        restatement_budget="low",
    ),
    "user_alias": ResponseContract(
        route_family="user_alias",
        content_obligations=("acknowledge the alias update", "confirm how Duckln will address the user"),
        style_name="user_alias",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "repo_active_explanation": ResponseContract(
        route_family="repo_active_explanation",
        content_obligations=("define what active repo means", "keep it concrete", "avoid sounding like hidden background state"),
        style_name="active_repo_explainer",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "next_step_guidance": ResponseContract(
        route_family="next_step_guidance",
        content_obligations=("answer what to do next directly", "prefer the live offer or smallest bounded step"),
        style_name="next_step_direct",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "conversation_repair": ResponseContract(
        route_family="conversation_repair",
        content_obligations=("explain the last answer in plain language", "do not continue the stale thread unless asked", "offer a reset when useful"),
        style_name="repair_plainly",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "conversation_restate": ResponseContract(
        route_family="conversation_restate",
        content_obligations=("restate the last answer more simply", "do not continue the stale thread", "keep it short"),
        style_name="restate_plainly",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "repo_memory_meta": ResponseContract(
        route_family="repo_memory_meta",
        content_obligations=("answer what Duckln remembers about the repo", "separate tracked state from durable learning", "stay direct"),
        style_name="repo_memory",
        allow_provider_polish=True,
        example_keys=("repo_memory_meta",),
        detail_level="short",
        restatement_budget="low",
        trust_render_mode="on_state_only",
    ),
    "repo_capability_coverage": ResponseContract(
        route_family="repo_capability_coverage",
        content_obligations=("answer catalog coverage", "narrow to realistic options when the machine is mentioned"),
        style_name="catalog_then_filter",
        allow_provider_polish=False,
        allow_command_steer=False,
        example_keys=("repo_coverage",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_recommendation_single": ResponseContract(
        route_family="repo_recommendation_single",
        content_obligations=("state the chosen repo", "explain why it fits", "mention one caveat", "offer one next move"),
        style_name="grounded_recommendation",
        allow_provider_polish=True,
        allow_command_steer=True,
        example_keys=("recommend_one",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_recommendation_compare": ResponseContract(
        route_family="repo_recommendation_compare",
        content_obligations=("compare the requested options", "choose one", "explain the tradeoff", "offer one next move"),
        style_name="grounded_compare",
        allow_provider_polish=True,
        allow_command_steer=True,
        example_keys=("whisperx_or_whisper",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_recommendation_rationale": ResponseContract(
        route_family="repo_recommendation_rationale",
        content_obligations=("explain why the chosen repo fits", "explain why alternatives ranked lower"),
        style_name="grounded_rationale",
        allow_provider_polish=True,
        allow_command_steer=True,
        example_keys=("why_that_repo",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_alternatives": ResponseContract(
        route_family="repo_alternatives",
        content_obligations=("name the next best repo", "explain why it ranked behind the first choice", "offer one next move"),
        style_name="grounded_alternative",
        allow_provider_polish=True,
        allow_command_steer=True,
        example_keys=("second_choice",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_overview": ResponseContract(
        route_family="repo_overview",
        content_obligations=("say what the repo is", "keep it brief", "mention one practical angle when useful"),
        style_name="repo_identity",
        allow_provider_polish=True,
        example_keys=("repo_overview",),
        detail_level="short",
        restatement_budget="low",
    ),
    "repo_requirements": ResponseContract(
        route_family="repo_requirements",
        content_obligations=("cover the machine reality", "cover repo needs", "state the practical consequence", "offer one next move"),
        style_name="grounded_requirements",
        allow_provider_polish=True,
        allow_command_steer=True,
        example_keys=("inspect_requirements",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_fit": ResponseContract(
        route_family="repo_fit",
        content_obligations=("state the fit judgment", "state the main reason", "keep the consequence practical"),
        style_name="grounded_fit",
        allow_provider_polish=True,
        allow_command_steer=False,
        example_keys=("repo_fit",),
        detail_level="balanced",
        restatement_budget="low",
    ),
    "repo_inventory": ResponseContract(
        route_family="repo_inventory",
        content_obligations=("answer from tracked lifecycle state", "stay direct"),
        style_name="stateful_inventory",
        detail_level="short",
    ),
    "repo_live_sessions": ResponseContract(
        route_family="repo_live_sessions",
        content_obligations=("answer only from live repo session state", "separate live sessions from tracked or selected repos", "stay direct"),
        style_name="stateful_inventory",
        detail_level="short",
        trust_render_mode="on_state_only",
    ),
    "repo_status": ResponseContract(
        route_family="repo_status",
        content_obligations=("answer from tracked lifecycle state", "be honest about tracked vs verified facts"),
        style_name="stateful_status",
        detail_level="short",
        trust_render_mode="on_state_only",
    ),
    "repo_verify": ResponseContract(
        route_family="repo_verify",
        content_obligations=("treat this as live verification, not just tracked state", "offer the bounded verification step", "be explicit about last recorded state vs fresh check"),
        style_name="verify_then_run",
        detail_level="short",
    ),
    "repo_access": ResponseContract(
        route_family="repo_access",
        content_obligations=("give the stored run command or access hint directly", "stay practical"),
        style_name="stateful_access",
        detail_level="short",
    ),
    "repo_path": ResponseContract(
        route_family="repo_path",
        content_obligations=("show the stored repo path directly", "include local vs VM target when known"),
        style_name="stateful_path",
        detail_level="short",
    ),
    "repo_run": ResponseContract(
        route_family="repo_run",
        content_obligations=("acknowledge the resolved repo", "offer the bounded run step"),
        style_name="action_ready",
        detail_level="short",
    ),
    "repo_restart": ResponseContract(
        route_family="repo_restart",
        content_obligations=("acknowledge the resolved repo", "state whether Duckln will stop a live session first or fall back to a fresh run"),
        style_name="action_ready",
        detail_level="short",
    ),
    "repo_stop": ResponseContract(
        route_family="repo_stop",
        content_obligations=("acknowledge the resolved repo session", "be explicit about whether Duckln has a live session to stop"),
        style_name="action_ready",
        detail_level="short",
    ),
    "repo_logs": ResponseContract(
        route_family="repo_logs",
        content_obligations=("keep this grounded in the tracked runtime session", "show the bounded log path or be honest if none exists"),
        style_name="stateful_access",
        detail_level="short",
    ),
    "repo_remove": ResponseContract(
        route_family="repo_remove",
        content_obligations=("give the bounded removal path", "keep it grounded in stored state"),
        style_name="bounded_remove",
        detail_level="short",
    ),
    "workflow_action": ResponseContract(
        route_family="workflow_action",
        content_obligations=("acknowledge the requested workflow move", "route into the bounded runtime action"),
        style_name="workflow_direct",
        detail_level="short",
    ),
    "recommendation_followup": ResponseContract(
        route_family="recommendation_followup",
        content_obligations=("continue the live recommendation thread", "answer with only the next useful detail"),
        style_name="recommendation_followup",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "slash_runtime_followup": ResponseContract(
        route_family="slash_runtime_followup",
        content_obligations=("continue the live slash/runtime thread", "keep the answer grounded in the selected repo and current step"),
        style_name="slash_runtime_followup",
        allow_provider_polish=True,
        detail_level="short",
        restatement_budget="low",
    ),
    "clarify": ResponseContract(
        route_family="clarify",
        content_obligations=("ask one short clarification question", "offer 2-3 contextual options"),
        style_name="clarify_once",
        detail_level="short",
    ),
    "utility_fallback": ResponseContract(
        route_family="utility_fallback",
        content_obligations=("acknowledge uncertainty briefly", "offer likely contextual topics", "stay useful"),
        style_name="utility_fallback",
        allow_command_steer=False,
        example_keys=("utility_fallback",),
        detail_level="short",
        restatement_budget="low",
    ),
}


CONVERSATION_EXAMPLES: tuple[ConversationExample, ...] = (
    ConversationExample("hi", "small_talk", "hi", "Hi. Tell me the repo, blocker, or machine question and I'll keep it tight."),
    ConversationExample("how_are_you", "small_talk", "how are you", "Doing well. If you have something concrete in mind, I can answer it directly."),
    ConversationExample("what_can_you_help_me_with", "capability", "what can you help me with", "I can recommend repos, check fit on this machine, set things up, verify them, and help with run or repair issues."),
    ConversationExample("user_identity_meta", "user_identity_meta", "do you know who i am", "Not unless you tell me. I only keep short bounded preferences and repo context, not a hidden profile about you."),
    ConversationExample("repo_active_explanation", "repo_active_explanation", "what do you mean by active repo", "Active repo just means the repo Duckln is currently tracking for follow-up questions like 'run it' or 'what next'."),
    ConversationExample("conversation_repair", "conversation_repair", "sorry i did not understand", "I was talking about the last topic Duckln was tracking. If you want, I can restate it plainly or reset and answer again."),
    ConversationExample("conversation_restate", "conversation_restate", "say that again simply", "Plain version: I was still talking about the current topic Duckln had active in this chat."),
    ConversationExample("next_step_guidance", "next_step_guidance", "tell me what to do next", "The next step depends on whether you want to stay on the current repo or start a fresh recommendation. I can keep either path bounded."),
    ConversationExample("repo_coverage", "repo_capability_coverage", "which repos can you help me with on my system", "I can work across Duckln's curated catalog. On your machine, I can narrow that to the most realistic options and pick one when you want me to."),
    ConversationExample("recommend_one", "repo_recommendation_single", "recommend me one", "On this machine I'd start with the repo that has the cleanest first run, not the heaviest feature set. The main thing to watch is whether RAM stays comfortable after normal system overhead."),
    ConversationExample("whisperx_or_whisper", "repo_recommendation_compare", "WhisperX or Whisper", "Between those two, I'd start with the lighter one here. The heavier option can still work, but it adds more runtime weight for a first local run."),
    ConversationExample("why_that_repo", "repo_recommendation_rationale", "why that repo", "I put it first because it matches this machine more cleanly and is more likely to verify without extra friction than the heavier alternatives."),
    ConversationExample("second_choice", "repo_alternatives", "is there a second choice", "Yes. The next option is the lighter backup that still fits this machine reasonably well, even though it trails the first choice."),
    ConversationExample("repo_overview", "repo_overview", "what is kokoro", "Kokoro is the repo we’re looking at right now. I can give you the short idea first, then go into requirements or fit on this machine."),
    ConversationExample("inspect_requirements", "repo_requirements", "inspect its requirements", "On your machine, the tight part is memory after normal overhead. This repo looks workable, but I wouldn't call it roomy unless you keep the workload small."),
    ConversationExample("system_capacity", "system_summary", "tell me my system capacity", "Duckln sees your OS, architecture, CPU count, RAM, and compute path. The practical takeaway is whether lighter local repos or heavier accelerator-first repos make sense here."),
    ConversationExample("system_verify", "system_verify", "can you check if duckln is error free", "The bounded way to verify Duckln itself is /healthcheck. That checks the current environment directly instead of trusting old state."),
    ConversationExample("memory_meta", "memory_meta", "did you learn anything today", "Yes, but only in bounded form. Duckln keeps short session context and promoted heuristics, not a full transcript."),
    ConversationExample("vm_definition", "vm_info", "what is vm", "A VM here means a separate Ubuntu machine Duckln can create with Multipass so repo setup stays isolated from your host."),
    ConversationExample("vm_capability", "vm_info", "can you deploy vm", "Yes. Duckln can create the VM, configure Duckln inside it if you want, and keep VM repo work separate from local work."),
    ConversationExample("vm_repo_recommendation", "vm_repo_recommendation", "which repo do you recommend to put in vm", "For a VM path, I answer in Linux-isolation terms, not extra GPU power. A VM helps dependency isolation, but it does not create CUDA on this Mac."),
    ConversationExample("repo_memory_meta", "repo_memory_meta", "is whisper in your memory", "Yes. Duckln can remember tracked setup state and repo notes for a repo like Whisper, but it keeps that memory bounded and does not store a full transcript."),
    ConversationExample("repo_path", "repo_path", "show me the repo path", "Duckln can show the tracked repo path directly when it has one recorded."),
    ConversationExample("repo_verify", "repo_verify", "check if whisper is ready and error free to run", "I can verify Whisper with a bounded run check instead of only trusting the last tracked ready state."),
    ConversationExample("repo_restart", "repo_restart", "restart whisper", "Duckln can restart Whisper by stopping the tracked live session first, or fall back to a fresh bounded run if no live session is active."),
    ConversationExample("clarify_repo_or_system", "clarify", "help me with it", "Do you mean the last repo we discussed, your current machine capacity, or a setup issue?"),
    ConversationExample("clarify_run_subject", "clarify", "run that", "Do you want me to run the active repo, the last recommended repo, or check fit first?"),
    ConversationExample("utility_fallback", "utility_fallback", "the thing is not working", "I’m not sure what 'the thing' is yet. If you want, we can narrow it to repo setup, repo status, or system fit."),
)


BOOTSTRAP_HEURISTICS: tuple[ConversationBootstrapHeuristic, ...] = (
    ConversationBootstrapHeuristic("small_talk", ("hi", "hello", "hey", "good morning", "good afternoon", "good evening"), 0.95, "warm_direct"),
    ConversationBootstrapHeuristic("small_talk", ("how are you", "how r u", "howre you"), 0.95, "warm_direct"),
    ConversationBootstrapHeuristic("capability", ("what can you do", "what can you help me with", "what are you good at", "what is your expertise"), 0.9, "scoped_capability"),
    ConversationBootstrapHeuristic("system_summary", ("system capacity", "my machine", "my system specs", "ram cpu gpu"), 0.92, "grounded_system"),
    ConversationBootstrapHeuristic("system_verify", ("duckln is error free", "duckln healthy", "check duckln", "verify duckln", "healthcheck"), 0.93, "system_verify"),
    ConversationBootstrapHeuristic("memory_meta", ("did you learn anything", "what do you remember", "what did you learn"), 0.92, "honest_memory"),
    ConversationBootstrapHeuristic("vm_info", ("what is vm", "what is a vm", "vm help", "can you deploy vm", "can you set up vm"), 0.93, "vm_direct"),
    ConversationBootstrapHeuristic("vm_repo_recommendation", ("recommend to put in vm", "recommend in vm", "repo in vm"), 0.9, "vm_recommendation"),
    ConversationBootstrapHeuristic("user_identity_meta", ("do you know who i am", "remember me", "who am i to you"), 0.9, "social_meta"),
    ConversationBootstrapHeuristic("conversation_repair", ("sorry i did not understand", "i did not understand", "what are you talking about", "can you elaborate", "what do you mean"), 0.91, "repair_plainly"),
    ConversationBootstrapHeuristic("conversation_restate", ("say that again", "say that again simply", "restate that", "repeat that", "say it plainly", "in simple words"), 0.9, "restate_plainly"),
    ConversationBootstrapHeuristic("repo_memory_meta", ("in your memory", "remember whisper", "remember this repo"), 0.9, "repo_memory"),
    ConversationBootstrapHeuristic("repo_capability_coverage", ("which repos can you help me with", "what repos can you help me with"), 0.88, "catalog_then_filter"),
    ConversationBootstrapHeuristic("repo_recommendation_single", ("recommend me one", "what repo do you recommend", "best suit my system", "best repo for my system"), 0.9, "grounded_recommendation"),
    ConversationBootstrapHeuristic("repo_recommendation_compare", (" or ", "compare", "which is better"), 0.83, "grounded_compare"),
    ConversationBootstrapHeuristic("repo_recommendation_rationale", ("why that repo", "why do you recommend", "why not "), 0.88, "grounded_rationale"),
    ConversationBootstrapHeuristic("repo_alternatives", ("second choice", "other option", "what else would you pick"), 0.88, "grounded_alternative"),
    ConversationBootstrapHeuristic("repo_overview", ("what is this repo", "what does this repo do", "give me an idea what"), 0.88, "repo_identity"),
    ConversationBootstrapHeuristic("repo_requirements", ("what does ", "requirements", "required for", "minimum requirement", "inspect its requirements"), 0.89, "grounded_requirements"),
    ConversationBootstrapHeuristic("repo_fit", ("run comfortably", "too heavy", "okay on my machine", "fit my system"), 0.87, "grounded_fit"),
    ConversationBootstrapHeuristic("repo_inventory", ("which repos do i have", "repos set up", "repos setup"), 0.89, "stateful_inventory"),
    ConversationBootstrapHeuristic("repo_live_sessions", ("live repo sessions", "running repo sessions", "actually running repos", "live repos"), 0.93, "stateful_inventory"),
    ConversationBootstrapHeuristic("repo_path", ("repo path", "show me the path", "where is the repo"), 0.9, "stateful_path"),
    ConversationBootstrapHeuristic("repo_status", ("is whisper setup", "is it setup", "is it installed", "is it running"), 0.88, "stateful_status"),
    ConversationBootstrapHeuristic("repo_verify", ("check if it is ready", "check if its ready", "error free to run", "verify whisper", "check whisper"), 0.91, "verify_then_run"),
    ConversationBootstrapHeuristic("repo_access", ("how do i run", "how do i access", "how to access", "how to run"), 0.87, "stateful_access"),
    ConversationBootstrapHeuristic("repo_run", ("run it", "run this repo", "start it", "launch it"), 0.85, "action_ready"),
    ConversationBootstrapHeuristic("repo_restart", ("restart it", "restart whisper", "rerun whisper", "re-run whisper", "start it again"), 0.88, "action_ready"),
    ConversationBootstrapHeuristic("repo_stop", ("stop it", "stop whisper", "kill whisper", "terminate whisper"), 0.88, "action_ready"),
    ConversationBootstrapHeuristic("repo_logs", ("show logs", "tail logs", "repo logs", "whisper logs"), 0.88, "stateful_access"),
    ConversationBootstrapHeuristic("repo_remove", ("remove", "uninstall", "delete"), 0.84, "bounded_remove"),
)


def response_contract_for_family(route_family: str) -> ResponseContract:
    """Return the response contract for a route family."""

    return RESPONSE_CONTRACTS[route_family]


def conversation_examples_for_family(route_family: str, *, limit: int = 4) -> tuple[ConversationExample, ...]:
    """Return canonical examples for one route family."""

    matches = [example for example in CONVERSATION_EXAMPLES if example.route_family == route_family]
    return tuple(matches[:limit])


def bootstrap_heuristics() -> tuple[ConversationBootstrapHeuristic, ...]:
    """Return the shipped routing heuristics used before learning exists."""

    return BOOTSTRAP_HEURISTICS
