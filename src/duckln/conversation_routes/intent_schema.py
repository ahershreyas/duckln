"""Structured turn parsing for free-text conversation routing."""

from __future__ import annotations

from dataclasses import dataclass
import re


_PATH_PATTERN = re.compile(r"(~?/[/A-Za-z0-9._-]+(?:/[/A-Za-z0-9._-]+)+)")
_TOP_N_PATTERN = re.compile(r"\btop\s+(\d+)\b")
_REPO_HINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:restart|rerun|re-run|launch|run|start|stop|open|attach|uninstall|remove|delete)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\b(?:access)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bhow can we (?:restart|rerun|re-run|launch|run|start|stop|open|attach)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bhow can we access\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bcan you help (?:uninstall|remove|delete)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bcan you (?:set up|setup|install)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bdo\s+(?:we|i)\s*have\s+([a-z0-9][a-z0-9._/-]*)(?:\s+repo)?\b"),
    re.compile(r"\bis\s+there\s+([a-z0-9][a-z0-9._/-]*)(?:\s+repo)?\b"),
    re.compile(r"\bdo\s+wehave\s+([a-z0-9][a-z0-9._/-]*)(?:\s+repo)?\b"),
    re.compile(r"\bis\s+([a-z0-9][a-z0-9._/-]*)\s+(?:installed|active|running|ready|set up|setup)\b"),
    re.compile(r"\bwhere is\s+([a-z0-9][a-z0-9._/-]*)\s+(?:installed|set up|setup)\b"),
    re.compile(r"\bwhere did you install\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\b(?:what is )?(?:the )?location of\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\b(?:what is )?(?:the )?install location of\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\binstall path for\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bwhere is\s+([a-z0-9][a-z0-9._/-]*)\s+located\b"),
    re.compile(r"\b(?:github|source|origin|remote)\s+(?:repo|repository|url|link|path)\s+(?:for|of)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\b([a-z0-9][a-z0-9._/-]*)\s+(?:github|source)\s+(?:repo|url|link|path)\b"),
    re.compile(r"\bshow me (?:the )?(?:repo )?path (?:for|of)?\s*([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bshow (?:me )?logs (?:for|of)?\s*([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bpath (?:for|of)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bcan you tell me if\s+([a-z0-9][a-z0-9._/-]*)\s+is\s+(?:installed|active|running|ready)\b"),
    re.compile(r"\bpath (?:for|of)\s+([a-z0-9][a-z0-9._/-]*)\b"),
    re.compile(r"\bwhat about\s+([a-z0-9][a-z0-9._/-]*)\b"),
)
_STOP_REPO_HINTS = {
    "a",
    "an",
    "the",
    "on",
    "my",
    "your",
    "our",
    "their",
    "repo",
    "repos",
    "repo?",
    "it",
    "that",
    "this",
    "one",
    "there",
    "already",
    "active",
    "installed",
    "setup",
    "ready",
    "running",
    "tracked",
    "system",
    "machine",
    "local",
    "vm",
}
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_LIFECYCLE_MARKERS = ("installed", "active", "tracked", "ready", "set up", "setup", "prepared", "in process", "running")
_INVENTORY_QUERY_MARKERS = (
    "all repos",
    "show me repos",
    "show me all repos",
    "which repos",
    "what repos",
    "tell me repos",
    "tell me active repos",
    "tell me running repos",
    "list repos",
    "do we have any repos",
    "do i have any repos",
    "a repo that we have setup",
    "a repo we have setup",
    "a repo that is setup",
    "a repo that we have set up",
    "repos in duckln",
    "repos in system",
    "active repos",
    "running repos",
    "installed repos",
    "ready repos",
)
_STATUS_QUERY_MARKERS = (
    "do we have",
    "do i have",
    "do wehave",
    "do ihave",
    "is there",
    "can you tell me if",
    "tell me if",
    "check if",
    "whether",
    "have we got",
)
_RECOMMENDATION_MARKERS = ("recommend", "suggest", "best", "choice", "choices", "options", "friendly")
_REPO_PRONOUN_TERMS = (" it ", " its ", "that repo", "that one", "this repo", "this one")


def _looks_like_activeish_repo_placeholder(token: str) -> bool:
    token = token.strip().lower()
    if token in {"active", "aactive"}:
        return True
    return re.fullmatch(r"a+ctive", token) is not None


@dataclass(frozen=True)
class StructuredConversationIntent:
    """A lightweight structural parse for one user turn."""

    action: str | None = None
    subject: str | None = None
    scope: str | None = None
    target: str | None = None
    target_name: str | None = None
    repo_name_hint: str | None = None
    path_hint: str | None = None
    quantity: int | None = None
    destructive: bool = False


def parse_structured_intent(normalized_compact: str) -> StructuredConversationIntent:
    """Extract a stable action/subject/scope shape from a free-text turn."""

    text = " ".join(normalized_compact.strip().lower().split())
    path_hint = _extract_path_hint(text)
    repo_name_hint = _extract_repo_name_hint(text, path_hint=path_hint)
    action = _detect_action(text)
    subject = _detect_subject(text, path_hint=path_hint, repo_name_hint=repo_name_hint)
    if any(token in text for token in (" vm", "vm ", "virtual machine", "multipass", "ubuntu vm")):
        target = "vm"
    elif any(token in text for token in (" aws", "aws ", "gcp", " cloud", "cloud ", "ec2", "compute engine")):
        target = "cloud"
    elif any(token in text for token in (" docker", "docker ", "container", "containers")):
        target = "docker"
    else:
        target = None
    target_name = _extract_target_name(text, target=target)
    quantity = _extract_top_n(text)
    scope = _detect_scope(
        text,
        action=action,
        subject=subject,
        repo_name_hint=repo_name_hint,
        quantity=quantity,
        path_hint=path_hint,
    )
    destructive = action == "remove"
    return StructuredConversationIntent(
        action=action,
        subject=subject,
        scope=scope,
        target=target,
        target_name=target_name,
        repo_name_hint=repo_name_hint,
        path_hint=path_hint,
        quantity=quantity,
        destructive=destructive,
    )


def _extract_target_name(text: str, *, target: str | None) -> str | None:
    """Extract an explicit VM/Docker/cloud target label from natural language."""

    patterns = (
        r"\b(?:on|in|inside|within|from|targeting|to)\s+([a-z0-9][a-z0-9_.-]*(?:vm|docker|container|cloud)[a-z0-9_.-]*)\b",
        r"\b(?:vm|machine|multipass)\s+([a-z0-9][a-z0-9_.-]*vm[a-z0-9_.-]*)\b",
        r"\b(?:container|docker)\s+([a-z0-9][a-z0-9_.-]*)\b",
        r"\b(?:run|start|open|connect|use|launch)\s+([a-z0-9][a-z0-9_.-]*vm[a-z0-9_.-]*)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is not None:
            candidate = match.group(1).strip(" .,:;")
            if candidate not in _STOP_REPO_HINTS:
                return candidate
    if target == "vm":
        match = re.search(r"\b([a-z0-9][a-z0-9_.-]*vm[a-z0-9_.-]*)\b", text)
        if match is not None:
            return match.group(1).strip(" .,:;")
    return None


def _extract_path_hint(text: str) -> str | None:
    match = _PATH_PATTERN.search(text)
    if match is None:
        return None
    return match.group(1)


def _extract_top_n(text: str) -> int | None:
    match = _TOP_N_PATTERN.search(text)
    if match is not None:
        return int(match.group(1))
    for word, value in _NUMBER_WORDS.items():
        if f"top {word}" in text:
            return value
        if f"{word} choices" in text or f"{word} repos" in text or f"give me {word}" in text:
            return value
    return None


def _extract_repo_name_hint(text: str, *, path_hint: str | None) -> str | None:
    if path_hint:
        slug = path_hint.rstrip("/").split("/")[-1].strip()
        if slug:
            return slug
    for pattern in _REPO_HINT_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        hint = match.group(1).strip().strip(".,?!")
        if hint and hint not in _STOP_REPO_HINTS and not _looks_like_activeish_repo_placeholder(hint):
            return hint
    return None


def _detect_action(text: str) -> str | None:
    if any(
        phrase in text
        for phrase in (
            " restart ",
            "restart ",
            "rerun ",
            "re-run ",
            "restart it",
            "rerun it",
            "start it again",
            "run it again",
        )
    ):
        return "restart"
    if any(token in text for token in (" uninstall ", " remove ", " delete ", "uninstall ", "remove ", "delete ")):
        return "remove"
    if any(token in text for token in (" stop ", " kill ", " terminate ", "stop ", "kill ", "terminate ")):
        return "stop"
    if any(token in text for token in (" launch ", " start ", "launch ", "start ")):
        return "run"
    if (
        text.startswith("run ")
        or any(
            phrase in text
            for phrase in (
                "can you run ",
                "how can i run ",
                "how do i run ",
                "how can we run ",
                "how do we run ",
                "how to run ",
            )
        )
    ):
        return "run"
    if any(
        phrase in text
        for phrase in (
            "show logs",
            "show me logs",
            "tail logs",
            "repo logs",
            "what are the logs",
            "what do the logs say",
        )
    ):
        return "logs"
    if any(
        phrase in text
        for phrase in (
            "how can i access ",
            "how do i access ",
            "how can we access ",
            "how do we access ",
            "attach to ",
            "attach it",
            "open shell",
            "shell in ",
            "terminal in ",
            "access repo",
            "access this repo",
            "access whisper",
            "access it",
            "open it",
        )
    ):
        return "access"
    if any(
        phrase in text
        for phrase in (
            "where is ",
            "where did you install ",
            "show me the repo path",
            "show me the path",
            "repo path",
            "install location",
        )
    ):
        return "inspect"
    if any(
        phrase in text
        for phrase in (
            "help me set up ",
            "help me setup ",
            "can you set up ",
            "can you setup ",
            "can you install ",
            "can you set up ",
            "bring up ",
        )
    ):
        return "setup"
    if any(
        phrase in text
        for phrase in (
            "verify ",
            "check if ",
            "is it running",
            "is it ready",
            "is it installed",
        )
    ):
        return "verify"
    if any(token in text for token in ("compare", "vs", "versus", "top ", "options")):
        return "compare"
    if any(token in text for token in _RECOMMENDATION_MARKERS) or "top " in text:
        return "recommend"
    if text.startswith("what is ") or text.startswith("what does ") or text.startswith("do you know what is "):
        return "define"
    if any(token in text for token in ("can you", "do you have the skills", "what help do you provide", "what can you do", "skills deploy", "skills to deploy", "work in vm", "manage in vm")):
        return "capability"
    if any(token in text for token in ("show", "list", "tell me", "check", "which", "what", "where")):
        return "inspect"
    return None


def _detect_subject(text: str, *, path_hint: str | None, repo_name_hint: str | None) -> str | None:
    vm_present = any(token in text for token in (" vm", "vm ", "virtual machine", "multipass", "ubuntu vm"))
    repo_present = any(token in text for token in ("repo", "repos", "repository", "repositories"))
    compare_present = any(token in text for token in (" vs ", " versus ", "compare "))
    padded = f" {text} "
    repo_pronoun_present = any(token in padded for token in _REPO_PRONOUN_TERMS) or text in {"it", "its", "that repo", "that one", "this repo", "this one"}
    if vm_present and not repo_present and repo_name_hint is None and path_hint is None:
        return "vm"
    if repo_present or repo_name_hint is not None or path_hint is not None or compare_present or repo_pronoun_present:
        return "repo"
    if vm_present:
        return "vm"
    return None


def _detect_scope(
    text: str,
    *,
    action: str | None,
    subject: str | None,
    repo_name_hint: str | None,
    quantity: int | None,
    path_hint: str | None,
) -> str | None:
    target_scoped_inventory = any(
        token in text
        for token in (
            "local repos",
            "vm repos",
            "cloud repos",
            "aws repos",
            "gcp repos",
            "docker repos",
            "repos in docker",
            "repos on cloud",
            "repos on aws",
            "repos on gcp",
        )
    )
    source_reference = any(
        token in text
        for token in (
            "github path",
            "github repo",
            "github url",
            "github link",
            "repo url",
            "repository url",
            "repo link",
            "repository link",
            "source repo",
            "source url",
            "source link",
            "origin url",
            "remote url",
            "github location",
        )
    )
    plural_repo_reference = (
        subject == "repo"
        and repo_name_hint is None
        and any(token in text for token in ("all repos", "repos", "repositories", "tracked repos"))
    )
    if subject == "vm" and ("what is" in text or "what does" in text):
        return "definition"
    if source_reference and plural_repo_reference:
        return "source_inventory"
    if source_reference and (subject == "repo" or repo_name_hint is not None or " it " in f" {text} " or "that repo" in text or "this repo" in text):
        return "source"
    if (
        plural_repo_reference
        and any(
            token in text
            for token in (
                "repo paths",
                "repos path",
                "paths of all repos",
                "location of all repos",
                "location of all the repos",
                "locations of all repos",
                "locations of all the repos",
                "where are all repos installed",
                "where did you install all repos",
            )
        )
    ):
        return "path_inventory"
    if path_hint is not None or "repo path" in text or "path of" in text or "where is" in text or "where did you install" in text:
        return "path"
    if quantity is not None:
        return "top_n"
    if (
        subject == "repo"
        and any(token in text for token in ("repos", "repositories"))
        and any(token in text for token in ("active", "running"))
        and any(token in text for token in ("show", "list", "tell me", "which", "what", "can you show me", "can you tell me", "do we have", "do i have"))
        and not any(token in text for token in ("installed", "set up", "setup", "prepared", "ready", "tracked"))
        and not target_scoped_inventory
    ):
        return "inventory"
    if (repo_name_hint is not None or " it " in f" {text} " or "that repo" in text or "this repo" in text) and any(token in text for token in ("where is", "where did you install", "install location", "location of", "install path", "path", "installed at")):
        return "path"
    if subject != "repo":
        return None
    if (
        any(token in text for token in ("active repos", "running repos"))
        and any(token in text for token in ("show", "list", "tell me", "which", "what", "can you show me", "can you tell me"))
        and not any(token in text for token in ("installed", "set up", "setup", "prepared", "ready"))
        and not target_scoped_inventory
    ):
        return "inventory"
    if "docker repos" in text or "repos in docker" in text or (
        any(token in text for token in ("docker", "container"))
        and "repo" in text
        and any(token in text for token in _LIFECYCLE_MARKERS)
    ):
        return "inventory_docker"
    if any(token in text for token in ("cloud repos", "aws repos", "gcp repos", "repos on cloud", "repos on aws", "repos on gcp")) or (
        any(token in text for token in ("cloud", "aws", "gcp"))
        and "repo" in text
        and any(token in text for token in _LIFECYCLE_MARKERS)
    ):
        return "inventory_cloud"
    if "local repos" in text or ("local" in text and "repo" in text and any(token in text for token in _LIFECYCLE_MARKERS)):
        return "inventory_local"
    if "vm repos" in text or ("vm" in text and "repo" in text and any(token in text for token in _LIFECYCLE_MARKERS)):
        return "inventory_vm"
    if (
        any(token in text for token in _INVENTORY_QUERY_MARKERS)
        and any(token in text for token in _LIFECYCLE_MARKERS)
    ) or (
        any(token in text for token in ("repos", "repositories"))
        and any(token in text for token in _LIFECYCLE_MARKERS)
        and any(token in text for token in ("which", "what", "show", "list", "tell me", "do we have", "do i have", "any", "can you tell me"))
    ):
        return "inventory"
    if repo_name_hint is None and (
        any(token in text for token in ("currently have active", "worked previously", "worked on previously"))
        or (
            "repo" in text
            and re.search(r"\ba+ctive\b", text) is not None
            and any(token in text for token in ("is there", "which", "what", "current", "currently", "tracking", "do i have", "do we have"))
        )
    ):
        return "active"
    if text.startswith("what about ") and repo_name_hint is not None:
        return "specific_status"
    if (
        action not in {"setup", "remove", "run", "stop"}
        and repo_name_hint is not None
        and (
            any(token in text for token in _LIFECYCLE_MARKERS)
            or any(token in text for token in _STATUS_QUERY_MARKERS)
        )
    ):
        return "specific_status"
    if repo_name_hint is not None and any(token in text for token in ("access", "endpoint", "url", "open")):
        return "access"
    if repo_name_hint is not None and any(token in text for token in ("logs", "log output", "tail")):
        return "logs"
    if repo_name_hint is not None and any(token in text for token in ("bring up",)):
        return "setup"
    return None
