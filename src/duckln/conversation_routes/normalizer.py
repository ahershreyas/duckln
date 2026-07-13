"""Normalize free-text turns into stable internal conversation intents."""

from __future__ import annotations

from dataclasses import dataclass
import re

from duckln.conversation_routes.intent_schema import parse_structured_intent
from duckln.conversation_routes.repo import (
    looks_like_repo_lifecycle_active_query,
    looks_like_repo_lifecycle_inventory_query,
    looks_like_repo_lifecycle_previous_work_query,
    looks_like_repo_lifecycle_specific_status_query,
    looks_like_repo_live_sessions_cloud_query,
    looks_like_repo_live_sessions_docker_query,
    looks_like_repo_live_sessions_local_query,
    looks_like_repo_live_sessions_query,
    looks_like_repo_live_sessions_vm_query,
    looks_like_repo_path_inventory_query,
    looks_like_repo_source_inventory_query,
    looks_like_repo_source_query,
)
from duckln.conversation_routes.social import extract_user_alias, looks_like_next_step_question
from duckln.conversation_routes.vm import (
    looks_like_docker_list_query,
    looks_like_repo_inventory_cloud_query,
    looks_like_repo_inventory_docker_query,
    looks_like_repo_inventory_local_query,
    looks_like_repo_inventory_vm_query,
    looks_like_repo_path_query,
    looks_like_vm_capability_query,
    looks_like_vm_delete_query,
    looks_like_vm_definition_query,
    looks_like_vm_list_query,
    looks_like_vm_open_query,
    looks_like_vm_repo_recommendation_query,
)


@dataclass(frozen=True)
class NormalizedConversationTurn:
    """Stable internal representation of one free-text turn."""

    raw_message: str
    normalized_compact: str
    base_intent: str


_GITHUB_REPO_URL_PATTERN = re.compile(r"https?://github\.com/[^/\s]+/[^/\s?#]+(?:\.git)?")
_INTENT_PHRASE_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("user_identity_meta", ("do you know who i am", "do yu kno who i am", "remember me", "who am i to you", "who am i")),
    ("memory_meta", ("did you learn anything", "what do you remember", "what did you learn from this conversation", "what did you learn from our conversation", "what have you learned")),
    ("greeting", ("hi", "hello", "hey", "hiya", "good morning", "good afternoon", "good evening", "hi there",
                  "wassup", "wsup", "whatsup", "what is up")),
    # Plan 192 F1/§10: casual well-being + gratitude + light chit-chat forms (matched on the
    # normalized/contraction-expanded text) so they route to conversation, not a repo clarifier.
    ("rapport", ("how are you", "how r u", "howre you", "how u doing", "how you doing", "how ya doing", "how’s it going", "how's it going", "hows it going", "how is it going", "how are things going",
                 "how you doin", "how ya doin", "how you been", "how ya been", "how have you been", "how goes it", "hows things", "how are ya",
                 "are you ok", "you ok", "are you okay", "you okay", "you good", "you alright", "you doing ok", "you doing okay", "how is your day", "how is your day going")),
    ("smalltalk", ("thanks", "thank you", "thanks so much", "thanks a lot", "cheers", "appreciate it", "much appreciated",
                   "tell me a joke", "say something funny", "make me laugh", "got any jokes", "tell a joke")),
    ("repo_active", ("do i have an active repo in my system", "do i have an active repo", "is there an active repo", "which repo is active", "what repo is active", "what is the active repo", "current active repo", "what repo are you currently tracking")),
    ("conversation_repair", ("sorry i did not understand", "i did not understand", "didn't understand", "did not get that", "what are you talking about", "can you elaborate", "can you explain that", "explain what you mean", "do you understand what i am asking", "that's not what i asked", "that is not what i asked", "that is not what i meant", "you are not understanding me")),
    ("conversation_restate", ("say that again", "say that again simply", "say that again plainly", "restate that", "repeat that", "say it plainly", "say it in simple words", "in simple words")),
    ("capability", ("what can you do", "what do you do", "what are you expertise", "what is your expertise", "what are your expertise", "what are you good at", "what can your help me with", "what can u help me with", "what all can you help me with", "what can you help me with", "what exactly can you help me do here")),
    ("repo_inventory", ("which repos do i have", "what repos do i have", "which repos are setup", "which repos are set up", "what repos are installed", "which repos are installed", "do i have any repos setup", "do i have any repo setup", "do i have any repos set up", "do we have any repos that we have setup", "repos setup right now", "repos set up right now")),
)


# Plan 192 F1 (Tier 1): a small, static chat-contraction/typo map, expanded at the WORD level
# BEFORE any deterministic keyword matching — so "how r you?" → "how are you" and matches the
# rapport bucket, instead of falling through to a wrong "assume repo" default. A convenience
# layer only (the structural fix is the repo-signal fallback in F2); keep the map small.
_CHAT_CONTRACTIONS: dict[str, str] = {
    "r": "are",
    "u": "you",
    "ur": "your",
    "plz": "please",
    "pls": "please",
    "thx": "thanks",
    "thnx": "thanks",
    "wanna": "want to",
    "gonna": "going to",
    "gotta": "got to",
    "dunno": "dont know",
    "hows": "how is",
    "whats": "what is",
    "wats": "what is",
    "abt": "about",
    "cuz": "because",
    "ure": "you are",
}
_CONTRACTION_TOKEN = re.compile(r"[a-z']+")


def _expand_chat_contractions(compact: str) -> str:
    """Expand whole-token chat contractions only (never touch inside a word)."""

    def _sub(match: re.Match[str]) -> str:
        return _CHAT_CONTRACTIONS.get(match.group(0), match.group(0))

    return _CONTRACTION_TOKEN.sub(_sub, compact)


def normalize_compact_message(message: str) -> str:
    """Return a compact lower-cased form for routing, with chat contractions expanded (Plan 192 F1)."""

    compact = " ".join(message.strip().lower().split())
    return _expand_chat_contractions(compact)


# Plan 192 F2 (Tier 1/2): the SMALL, finite set of POSITIVE repo signals. The doc's structural fix
# is to enumerate repo triggers (not the infinite set of small-talk phrasings): a repo/URL/path
# reference, a file/dir path, or a "repo job" action verb. No signal ⇒ the message is conversation,
# and no repo clarifier may fire — even when an active repo objective exists.
_REPO_ACTION_VERBS: tuple[str, ...] = (
    "set up", "setup", "set-up", "run", "build", "clone", "install", "reinstall", "fix",
    "deploy", "test", "lint", "migrate", "start", "compile", "launch", "serve", "package",
    "bundle", "dockerize", "containerize", "provision", "spin up", "bring up", "pull the repo",
    "check out", "checkout",
)
_OWNER_NAME_RE = re.compile(r"\b[\w.-]+/[\w.-]+\b")
_FILE_PATH_RE = re.compile(r"(?:^|\s)(?:~?/|\./|[\w.-]+/)[\w./-]+")
_FILE_EXT_RE = re.compile(r"\b[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|cpp|c|md|json|toml|yml|yaml|txt|sh|cfg|ini|lock)\b")


def has_repo_signal(normalized_compact: str) -> bool:
    """True when the message carries a POSITIVE repo signal (repo/URL/path or a repo action verb).
    Deterministic — the Tier-1/Tier-2 core: no signal ⇒ conversation, never a repo guess."""

    text = str(normalized_compact or "").strip()
    if not text:
        return False
    if extract_github_repo_url(text) or _GITHUB_REPO_URL_PATTERN.search(text):
        return True
    for verb in _REPO_ACTION_VERBS:
        if text == verb or text.startswith(verb + " ") or f" {verb} " in f" {text} ":
            return True
    if _FILE_EXT_RE.search(text):
        return True
    # An owner/name or file/dir path token (but not a bare word) — e.g. "vasu-devs/JustHireMe".
    if _OWNER_NAME_RE.search(text) or _FILE_PATH_RE.search(text):
        return True
    return False


def normalize_conversation_turn(message: str) -> NormalizedConversationTurn:
    """Normalize a raw free-text turn into a stable internal representation."""

    normalized_compact = normalize_compact_message(message)
    return NormalizedConversationTurn(
        raw_message=message,
        normalized_compact=normalized_compact,
        base_intent=classify_conversation_intent(normalized_compact),
    )


def extract_github_repo_url(text: str) -> str | None:
    """Extract a GitHub repo URL when present."""

    match = _GITHUB_REPO_URL_PATTERN.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,)")


def _matches_any_phrase(normalized_compact: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        pattern = rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])"
        if re.search(pattern, normalized_compact) is not None:
            return True
    return False


def _bucketed_intent(normalized_compact: str) -> str | None:
    for intent, phrases in _INTENT_PHRASE_BUCKETS:
        if normalized_compact in phrases or _matches_any_phrase(normalized_compact, phrases):
            return intent
    return None


def classify_conversation_intent(normalized_compact: str) -> str:
    """Map raw user phrasing onto a stable internal intent label."""

    normalized_compact = normalize_compact_message(normalized_compact)
    structured = parse_structured_intent(normalized_compact)

    if extract_github_repo_url(normalized_compact):
        if any(token in normalized_compact for token in ("requirement", "need", "fit", "comfortably", "heavy")):
            return "repo_requirements"
        if any(token in normalized_compact for token in ("set up", "setup", "install", "run", "deploy")):
            return "setup_request"
        return "repo_overview"
    if _bucketed_intent(normalized_compact) == "user_identity_meta" and "who are you" not in normalized_compact:
        return "user_identity_meta"
    if extract_user_alias(normalized_compact):
        return "user_alias_set"
    if "active repo" in normalized_compact and any(phrase in normalized_compact for phrase in ("what do you mean", "what does that mean", "what does active repo mean", "mean by")):
        return "repo_active_explanation"
    if any(phrase in normalized_compact for phrase in ("what shall i call you", "what should i call you", "what do i call you", "who are you")):
        return "greeting"
    if (
        " and " in normalized_compact
        and any(token in normalized_compact for token in ("how much compute", "system capacity", "my system have"))
        and any(token in normalized_compact for token in ("required by", "requirements", "need for", "need by"))
    ):
        return "repo_requirements"
    if _bucketed_intent(normalized_compact) == "memory_meta":
        return "memory_meta"
    if _bucketed_intent(normalized_compact) == "greeting":
        return "greeting"
    if _bucketed_intent(normalized_compact) == "rapport":
        return "rapport"
    if _bucketed_intent(normalized_compact) == "smalltalk":
        return "smalltalk"
    if _bucketed_intent(normalized_compact) == "conversation_repair":
        return "conversation_repair"
    if _bucketed_intent(normalized_compact) == "conversation_restate":
        return "conversation_restate"
    if _bucketed_intent(normalized_compact) == "repo_active":
        return "repo_active"
    if looks_like_repo_live_sessions_local_query(normalized_compact):
        return "repo_live_sessions_local"
    if looks_like_repo_live_sessions_vm_query(normalized_compact):
        return "repo_live_sessions_vm"
    if looks_like_repo_live_sessions_cloud_query(normalized_compact):
        return "repo_live_sessions_cloud"
    if looks_like_repo_live_sessions_docker_query(normalized_compact):
        return "repo_live_sessions_docker"
    if looks_like_repo_live_sessions_query(normalized_compact):
        return "repo_live_sessions"
    if looks_like_docker_list_query(normalized_compact):
        return "docker_list"
    if looks_like_vm_delete_query(normalized_compact):
        return "vm_delete"
    if looks_like_vm_open_query(normalized_compact):
        return "vm_open"
    if structured.subject == "vm" and structured.action == "define":
        return "vm_definition"
    if structured.subject == "vm" and structured.action == "capability":
        return "vm_capability"
    if structured.subject == "repo" and structured.target == "vm" and structured.action in {"recommend", "compare"}:
        return "vm_repo_recommendation"
    if structured.subject == "repo" and structured.scope == "inventory_vm":
        return "repo_inventory_vm"
    if structured.subject == "repo" and structured.scope == "inventory_cloud":
        return "repo_inventory_cloud"
    if structured.subject == "repo" and structured.scope == "inventory_docker":
        return "repo_inventory_docker"
    if structured.subject == "repo" and structured.scope == "inventory_local":
        return "repo_inventory_local"
    if looks_like_repo_path_inventory_query(normalized_compact):
        return "repo_path_inventory"
    if looks_like_repo_source_inventory_query(normalized_compact):
        return "repo_source_inventory"
    if structured.subject == "repo" and structured.scope == "inventory":
        return "repo_lifecycle_inventory"
    if structured.subject == "repo" and structured.scope == "path_inventory":
        return "repo_path_inventory"
    if structured.subject == "repo" and structured.scope == "source_inventory":
        return "repo_source_inventory"
    if (
        structured.subject == "repo"
        and structured.action == "verify"
        and not looks_like_repo_lifecycle_inventory_query(normalized_compact)
        and not looks_like_repo_inventory_local_query(normalized_compact)
        and not looks_like_repo_inventory_vm_query(normalized_compact)
        and not looks_like_repo_inventory_cloud_query(normalized_compact)
        and not looks_like_repo_inventory_docker_query(normalized_compact)
    ):
        return "repo_verify"
    if structured.subject == "repo" and structured.scope == "active":
        return "repo_lifecycle_active"
    if structured.subject == "repo" and structured.scope == "specific_status":
        return "repo_lifecycle_specific_status"
    if structured.subject == "repo" and structured.action == "restart":
        return "repo_restart"
    if structured.subject == "repo" and structured.action == "run":
        return "repo_run"
    if structured.subject == "repo" and structured.action == "stop":
        return "repo_stop"
    if structured.subject == "repo" and structured.action == "logs":
        return "repo_logs"
    if structured.subject == "repo" and structured.action == "remove":
        return "repo_removal"
    if structured.subject == "repo" and (structured.action == "access" or structured.scope == "access"):
        return "repo_access"
    if structured.subject == "repo" and structured.action == "compare":
        return "repo_recommendation_compare"
    if structured.subject == "repo" and (structured.action == "setup" or structured.scope == "setup"):
        return "setup_request"
    if structured.subject == "repo" and structured.scope == "source":
        return "repo_source_show"
    if structured.subject == "repo" and structured.scope == "path":
        return "repo_path_show"
    if normalized_compact.startswith("what about ") and structured.subject == "repo" and structured.repo_name_hint is not None:
        return "repo_lifecycle_specific_status"
    if structured.subject == "repo" and structured.scope == "top_n" and structured.target == "vm":
        return "vm_repo_recommendation"
    if structured.action == "verify" and any(token in normalized_compact for token in ("duckln", "healthcheck", "health", "error free")):
        return "system_verify"
    if structured.action == "run" and normalized_compact in {"run it", "launch it", "start it"}:
        return "repo_run"
    if structured.action == "restart" and normalized_compact in {"restart it", "rerun it", "re-run it", "start it again", "run it again"}:
        return "repo_restart"
    if structured.action == "stop" and normalized_compact in {"stop it", "kill it", "terminate it"}:
        return "repo_stop"
    if structured.action == "logs" and normalized_compact in {"show logs", "show me logs", "tail logs"}:
        return "repo_logs"
    if structured.action == "remove" and normalized_compact in {"remove it", "delete it", "uninstall it"}:
        return "repo_removal"
    if structured.action == "access" and normalized_compact in {"access it", "open it"}:
        return "repo_access"
    if structured.action == "setup" and normalized_compact in {"set it up", "setup it", "install it"}:
        return "setup_request"
    if looks_like_vm_definition_query(normalized_compact):
        return "vm_definition"
    if looks_like_vm_capability_query(normalized_compact):
        return "vm_capability"
    if looks_like_vm_delete_query(normalized_compact):
        return "vm_delete"
    if looks_like_vm_open_query(normalized_compact):
        return "vm_open"
    if looks_like_vm_repo_recommendation_query(normalized_compact):
        return "vm_repo_recommendation"
    if looks_like_repo_inventory_vm_query(normalized_compact):
        return "repo_inventory_vm"
    if looks_like_repo_inventory_cloud_query(normalized_compact):
        return "repo_inventory_cloud"
    if looks_like_repo_inventory_docker_query(normalized_compact):
        return "repo_inventory_docker"
    if looks_like_vm_list_query(normalized_compact):
        return "vm_list"
    if looks_like_docker_list_query(normalized_compact):
        return "docker_list"
    if looks_like_repo_inventory_local_query(normalized_compact):
        return "repo_inventory_local"
    if looks_like_repo_path_query(normalized_compact):
        return "repo_path_show"
    if looks_like_repo_source_query(normalized_compact):
        return "repo_source_show"
    if looks_like_repo_lifecycle_inventory_query(normalized_compact):
        return "repo_lifecycle_inventory"
    if looks_like_repo_lifecycle_active_query(normalized_compact):
        return "repo_lifecycle_active"
    if looks_like_repo_lifecycle_specific_status_query(normalized_compact):
        return "repo_lifecycle_specific_status"
    if looks_like_repo_lifecycle_previous_work_query(normalized_compact):
        return "repo_lifecycle_previous_work"
    if looks_like_next_step_question(normalized_compact):
        return "next_step_guidance"
    if any(
        phrase in normalized_compact
        for phrase in (
            "how good are you with repo",
            "how good are you with the setup of repos",
            "how good are you at repo",
            "good with the setup of repos",
            "good at setting up repos",
        )
    ):
        return "repo_confidence"
    if any(token in normalized_compact for token in ("confident", "confidence", "ability", "capable")) and "you" in normalized_compact:
        return "confidence"
    if any(phrase in normalized_compact for phrase in ("how do you know", "why do you think", "how do u know")):
        return "system_followup"
    if any(
        phrase in normalized_compact
        for phrase in (
            "tell me my system capacity",
            "tell me about my system capacity",
            "tell me about my machine",
            "what system do i have",
            "what is my system capacity",
            "how much ram/cpu/gpu do i have",
            "how much ram cpu gpu do i have",
            "how much ram do i have",
            "how much cpu do i have",
            "can you tell me my system capacity",
            "can you tell me about my system capacity",
            "can you tell me about my machine",
            "my machine specs",
            "my system specs",
        )
    ):
        return "system_capacity"
    if any(
        phrase in normalized_compact
        for phrase in (
            "which repos can you help me with",
            "what repos can you help me with",
            "which repositories can you help me with",
            "what repositories can you help me with",
            "what repos can you setup",
            "what repos can you set up",
            "which repos can you help me with my system",
            "what repos can you help me with my system",
        )
    ):
        if any(token in normalized_compact for token in ("recommend", "best fit", "best suited", "best suited for my system", "best for my system")):
            return "repo_recommendation_single"
        return "repo_capability_coverage"
    if any(
        phrase in normalized_compact
        for phrase in (
            "whisperx or whisper",
            "whisper x or whisper",
            "whisper or whisperx",
            "whisper or whisper x",
            "do you recommend whisperx or whisper",
            "do you recommend whisper or whisperx",
            "do you recommend whisperx or whisper from /repos",
        )
    ):
        return "repo_recommendation_compare"
    if "recommend" in normalized_compact and " or " in normalized_compact:
        return "repo_recommendation_compare"
    if any(
        phrase in normalized_compact
        for phrase in (
            "whih repos do you recommend",
            "what repos you recommend",
            "what repo you recommend",
            "what repos do you recommend",
            "what repo do you recommend",
            "recommend me one",
            "recommend me a repo",
            "best repo for my system",
            "repo you think will best",
            "repo you think will run best",
            "repo you think will work best",
            "repo think will best to run",
            "which repos do you recommend for my machine",
            "which /repos do you recommend for my machine",
            "can you suggest a repo for my machine",
            "can you not suggest a repo for my machine",
            "yes please suggest one best for my machine",
            "yes please suggest one best for my system",
            "suggest one best for my machine",
        )
    ):
        return "repo_recommendation_single"
    if any(
        phrase in normalized_compact
        for phrase in (
            "which will best suit my system",
            "which will best suite my system",
            "what will best suit my system",
            "what will suit my system best",
            "what fits my system best",
            "what suits my machine best",
        )
    ):
        return "repo_recommendation_single"
    if any(
        phrase in normalized_compact
        for phrase in (
            "what is this repo",
            "what does this repo do",
            "what is that repo",
            "what does that repo do",
            "what is private-gpt",
            "what is kokoro",
            "give me an idea what",
            "give me an idea what this repo is",
            "tell me what this repo is",
            "tell me about this repo",
        )
    ):
        return "repo_overview"
    if any(
        phrase in normalized_compact
        for phrase in (
            "second choice",
            "second best",
            "second recommendation",
            "another choice",
            "other repo",
            "other option",
            "what else would you pick",
            "what else do you recommend",
            "is there any other repo you recommend",
            "what the second best recommendation",
            "what the second recommendation",
            "so no second choice",
            "is there a second choice",
        )
    ):
        return "repo_alternatives"
    if _bucketed_intent(normalized_compact) == "repo_inventory":
        return "repo_inventory"
    if "in your memory" in normalized_compact or "in your memeory" in normalized_compact:
        return "repo_memory_meta"
    if (
        " is " in f" {normalized_compact} "
        and any(token in normalized_compact for token in ("setup", "set up", "installed", "running", "active", "ready"))
        and "repo" not in normalized_compact
    ):
        return "repo_status"
    if any(
        phrase in normalized_compact
        for phrase in (
            "is whisper setup",
            "is whisper set up",
            "is whisper installed",
            "is whisper running",
            "is open-webui setup",
            "is open-webui installed",
            "is speechbrain setup",
        )
    ):
        return "repo_status"
    if any(
        phrase in normalized_compact
        for phrase in (
            "check if it is ready",
            "check if its ready",
            "error free to run",
            "setup correctly",
            "verify whisper",
            "verify open-webui",
        )
    ):
        return "repo_verify"
    if any(
        phrase in normalized_compact
        for phrase in (
            "run this repo",
            "run repo",
            "start this repo",
            "start repo",
            "launch this repo",
            "launch repo",
            "run whisper",
            "start whisper",
            "launch whisper",
        )
    ):
        return "repo_run"
    if any(
        phrase in normalized_compact
        for phrase in (
            "why do you recommend",
            "why dont you recommend",
            "why don't you recommend",
            "tell me why that repo",
            "tell me why that one",
            "why that repo",
            "why that one",
            "why not ",
            "would whisper be lighter",
            "would open webui be lighter",
        )
    ):
        return "repo_recommendation_rationale"
    if any(
        token in normalized_compact
        for token in (
            "compare repos",
            "few repo options",
            "a few repos",
            "repo options",
            "which repos would",
            "top repos",
            "must setup",
            "must set up",
            "should setup",
            "should set up",
        )
    ):
        return "repo_recommendation_compare"
    if any(token in normalized_compact for token in ("recommend one repo", "best repo", "which repo", "what repo should", "which repository", "what repository")):
        if any(token in normalized_compact for token in ("which repos", "what repos", "options", "compare", "few", "top repos")):
            return "repo_recommendation_compare"
        if any(token in normalized_compact for token in ("one repo", "best", "single", "start with")) or "which repo" in normalized_compact or "which repository" in normalized_compact:
            return "repo_recommendation_single"
        return "repo_recommendation_single"
    if any(
        phrase in normalized_compact
        for phrase in (
            "where is ",
            "where did you install",
            "where is it installed",
            "install location",
        )
    ):
        return "repo_path_show"
    if any(
        phrase in normalized_compact
        for phrase in (
            "how do i access",
            "how can i access",
            "how do i run",
            "how can i run",
            "how to run",
            "how to access",
        )
    ):
        return "repo_access"
    if any(
        phrase in normalized_compact
        for phrase in (
            "remove ",
            "uninstall ",
            "delete ",
            "how do i remove",
            "how can i remove",
        )
    ):
        return "repo_removal"
    if any(
        phrase in normalized_compact
        for phrase in (
            "how much memory",
            "how much ram",
            "how much cpu",
            "what cpu",
            "what ram",
            "system requirements",
            "minimum requirement",
            "basic requirement",
            "what does whisper need",
            "what does open-webui need",
            "what does speechbrain need",
            "what does open webui need",
            "requirements for",
            "required for",
        )
    ):
        return "repo_requirements"
    if any(
        phrase in normalized_compact
        for phrase in (
            "will this run comfortably",
            "run comfortably on my system",
            "run comfortably on this machine",
            "will it run comfortably",
            "will this repo run comfortably",
            "comfortable on my system",
            "is this too heavy for my laptop",
            "too heavy for my laptop",
            "will this be okay on my machine",
            "okay on my machine",
        )
    ):
        return "repo_fit_judgment"
    if any(token in normalized_compact for token in ("robotic", "alive", "mechanical", "stiff", "dumb")):
        return "feedback_style"
    if _bucketed_intent(normalized_compact) == "conversation_repair":
        return "conversation_repair"
    if "what do you mean" in normalized_compact and "active repo" not in normalized_compact:
        return "conversation_repair"
    if _bucketed_intent(normalized_compact) == "capability":
        return "capability"
    if normalized_compact in {"repo", "repos", "repository", "project", "repo help", "repository help"}:
        return "setup_request"
    if any(phrase in normalized_compact for phrase in ("provider help", "model help", "mode help", "vm help", "config help")):
        return "help_request"
    if any(phrase in normalized_compact for phrase in ("help me set up", "set up ", "setup ", "install ", "run ")):
        return "setup_request"
    if any(token in normalized_compact for token in ("help me", "can you help", "could you help", "need help")):
        return "help_request"
    return "generic"
