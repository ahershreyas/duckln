"""VM-scoped helpers for conversation routing and direct capability answers."""

from __future__ import annotations

import re


def _phrase_present(text: str, phrase: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def looks_like_vm_definition_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    phrases = (
        "what is vm",
        "what is a vm",
        "do you know what is vm",
        "what does vm mean",
        "what is ubuntu vm",
        "what is multipass vm",
    )
    return any(_phrase_present(cleaned, phrase) for phrase in phrases)


def looks_like_vm_capability_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if not _phrase_present(cleaned, "vm") and not _phrase_present(cleaned, "multipass"):
        return False
    phrases = (
        "do you have the skills deploy vm",
        "can you deploy vm",
        "can you help deploy vm",
        "can you set up vm",
        "can you setup vm",
        "can you work in vm",
        "can you help with vm",
        "what vm help do you provide",
        "can you track repos inside vm",
        "can you install duckln in vm",
    )
    return any(_phrase_present(cleaned, phrase) for phrase in phrases)


def looks_like_vm_repo_recommendation_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    return (
        (_phrase_present(cleaned, "vm") or _phrase_present(cleaned, "virtual machine"))
        and any(_phrase_present(cleaned, phrase) for phrase in ("recommend", "best repo", "which repo", "what repo"))
    )


def looks_like_repo_path_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    phrases = (
        "repo path",
        "show me the repo path",
        "show repo path",
        "what is the repo path",
        "where is the repo",
        "where is whisper installed",
        "where is whisper located",
        "location of whisper",
        "location for whisper",
        "install path for whisper",
        "what is the location of whisper",
        "what is the install location of whisper",
        "show me the path",
        "show me its path",
        "can you show me its path",
        "what is its path",
        "what is the path for it",
        "show me its location",
        "can you show me its location",
        "path of the repo",
    )
    return any(_phrase_present(cleaned, phrase) for phrase in phrases) or (
        any(_phrase_present(cleaned, phrase) for phrase in ("location of", "install location of", "install path for", "where is"))
        and any(_phrase_present(cleaned, phrase) for phrase in ("repo", "whisper"))
    )


def looks_like_repo_inventory_vm_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    inventory_markers = (
        "show me vm repos",
        "what repos are in vm",
        "which repos are in vm",
        "what repos are on vm",
        "which repos are on vm",
        "show me all repos on",
        "show all repos on",
        "repos on duckln-vm",
        "repos in duckln-vm",
        "show vm repos",
        "repos in vm",
        "repos on vm",
        "vm repos",
    )
    lifecycle_markers = ("installed", "active", "tracked", "ready", "set up", "setup")
    return any(_phrase_present(cleaned, marker) for marker in inventory_markers) or (
        (_phrase_present(cleaned, "vm") or re.search(r"\b[a-z0-9_.-]*vm[a-z0-9_.-]*\b", cleaned) is not None)
        and _phrase_present(cleaned, "repo")
        and any(_phrase_present(cleaned, marker) for marker in lifecycle_markers)
    )


def looks_like_repo_inventory_cloud_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    inventory_markers = (
        "show me cloud repos",
        "what repos are on cloud",
        "which repos are on cloud",
        "show cloud repos",
        "cloud repos",
        "what repos are on aws",
        "what repos are on gcp",
        "which repos are on aws",
        "which repos are on gcp",
        "aws repos",
        "gcp repos",
    )
    lifecycle_markers = ("installed", "active", "tracked", "ready", "set up", "setup")
    return any(_phrase_present(cleaned, marker) for marker in inventory_markers) or (
        any(_phrase_present(cleaned, marker) for marker in ("cloud", "aws", "gcp"))
        and _phrase_present(cleaned, "repo")
        and any(_phrase_present(cleaned, marker) for marker in lifecycle_markers)
    )


def looks_like_repo_inventory_docker_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    inventory_markers = (
        "show me docker repos",
        "what repos are in docker",
        "which repos are in docker",
        "show docker repos",
        "docker repos",
        "docker repo",
        "container repos",
        "repos in docker",
    )
    lifecycle_markers = ("installed", "active", "tracked", "ready", "set up", "setup", "running")
    return any(_phrase_present(cleaned, marker) for marker in inventory_markers) or (
        any(_phrase_present(cleaned, marker) for marker in ("docker", "container"))
        and _phrase_present(cleaned, "repo")
        and any(_phrase_present(cleaned, marker) for marker in lifecycle_markers)
    )


def looks_like_docker_list_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if _phrase_present(cleaned, "repo") or _phrase_present(cleaned, "repos"):
        return False
    if (
        not _phrase_present(cleaned, "docker")
        and not _phrase_present(cleaned, "container")
        and not _phrase_present(cleaned, "containers")
    ):
        return False
    list_markers = (
        "how many",
        "list",
        "show",
        "what docker",
        "which docker",
        "what container",
        "which container",
        "running container",
        "existing container",
        "my container",
        "docker ps",
        "containers in my system",
        "containers on my system",
        "containers on local system",
        "all container",
    )
    return any(_phrase_present(cleaned, marker) for marker in list_markers)


def looks_like_vm_list_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if (
        not _phrase_present(cleaned, "vm")
        and not _phrase_present(cleaned, "vms")
        and not _phrase_present(cleaned, "multipass")
        and not _phrase_present(cleaned, "virtual machine")
    ):
        return False
    list_markers = (
        "list",
        "show",
        "what vm",
        "which vm",
        "active vm",
        "running vm",
        "existing vm",
        "how many",
        "my vm",
        "vms in my system",
        "vms on my system",
        "all vm",
        "available vm",
    )
    return any(_phrase_present(cleaned, marker) for marker in list_markers)


def looks_like_vm_open_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if (
        not _phrase_present(cleaned, "vm")
        and not _phrase_present(cleaned, "vms")
        and not _phrase_present(cleaned, "multipass")
        and not _phrase_present(cleaned, "virtual machine")
    ):
        return False
    open_markers = (
        "run vm",
        "run the vm",
        "start vm",
        "start the vm",
        "open vm",
        "open the vm",
        "connect vm",
        "connect to vm",
        "shell vm",
        "use vm",
        "launch vm",
    )
    if any(_phrase_present(cleaned, marker) for marker in open_markers):
        return True
    return bool(re.search(r"\b(?:run|start|open|connect|use|launch)\s+[a-z0-9_.-]*vm[a-z0-9_.-]*\b", cleaned))


def looks_like_vm_delete_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    if (
        not _phrase_present(cleaned, "vm")
        and not _phrase_present(cleaned, "vms")
        and not _phrase_present(cleaned, "multipass")
        and not _phrase_present(cleaned, "virtual machine")
    ):
        return False
    delete_markers = (
        "delete vm",
        "delete vms",
        "delete all vm",
        "delete all vms",
        "remove vm",
        "remove vms",
        "remove all vm",
        "remove all vms",
        "destroy vm",
        "purge vm",
    )
    return any(_phrase_present(cleaned, marker) for marker in delete_markers)


def looks_like_repo_inventory_local_query(normalized_compact: str) -> bool:
    cleaned = normalized_compact.replace("?", "").replace(".", "").replace(",", "")
    inventory_markers = (
        "show me local repos",
        "what repos are local",
        "which repos are local",
        "repos on local",
        "local repos",
    )
    lifecycle_markers = ("installed", "active", "tracked", "ready", "set up", "setup")
    return any(_phrase_present(cleaned, marker) for marker in inventory_markers) or (
        _phrase_present(cleaned, "local")
        and _phrase_present(cleaned, "repo")
        and any(_phrase_present(cleaned, marker) for marker in lifecycle_markers)
    )
