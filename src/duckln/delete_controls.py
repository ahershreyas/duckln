"""Plan 150 F5/F6/F7 — clean-slate delete controls: the data + logic behind the `@<name>`
mention picker and the confirmed delete/forget actions, with a 7-day deletion audit.

Kept deliberately UI-free + dependency-injected so it's unit-testable: the TUI (textual_ui)
renders the `@` popup from `mention_suggestions(...)`; the command loop (main.py) resolves a
`delete @name` / `delete the vm` phrase via `resolve_delete_target(...)` and runs it through
`perform_delete(...)` with the host's confirm + runner. Every destructive action is confirmed
ONCE, recorded for 7 days, and the user is told the record is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DeletableTarget:
    kind: str            # "vm" | "container" | "aws" | "gcp" | "custom_repo"
    name: str            # the VM/container/instance name, or the repo url/name
    label: str           # human, type-labeled, e.g. "@duckln-vm (VM)"
    detail: str = ""     # extra (e.g. region/instance-id/repo url)


_KIND_SUFFIX = {
    "vm": "VM", "container": "container", "image": "Docker image", "aws": "AWS instance",
    "gcp": "GCP instance", "custom_repo": "custom repo",
}


def mention_suggestions(config_dir, *, query: str = "", vm_lister: Callable | None = None,
                        store=None) -> tuple[DeletableTarget, ...]:
    """Plan 150 F7: the type-labeled list of selectable targets for the `@` popup — VMs,
    the active container/cloud, and custom repos in the selection list. `query` filters by
    substring. Listers are injectable for tests; best-effort (a failing source is skipped)."""
    out: list[DeletableTarget] = []
    # VMs (multipass)
    try:
        lister = vm_lister
        if lister is None:
            from duckln.vm import list_multipass_vm_names as lister  # type: ignore
        for name in lister() or ():
            out.append(DeletableTarget("vm", name, f"@{name} (VM)"))
    except Exception:
        pass
    # Plan 198 F3: live Docker containers + images from the local daemon (best-effort — skipped
    # when Docker isn't installed/running). So the user can delete installed Docker resources too.
    try:
        from duckln.docker_inventory import list_docker_containers, list_docker_images
        _c = list_docker_containers(running_only=False)
        if _c.ok:
            for c in _c.containers:
                nm = c.name or (c.container_id[:12] if c.container_id else "")
                if nm:
                    out.append(DeletableTarget("container", nm, f"@{nm} (container)", detail=c.image))
        _i = list_docker_images()
        if _i.ok:
            for img in _i.images:
                nm = img.name
                if nm and nm != "<none>":
                    out.append(DeletableTarget("image", nm, f"@{nm} (Docker image)"))
    except Exception:
        pass
    # custom repos in the /repos selection list
    try:
        st = store
        if st is None:
            from state.access import initialize_state_store
            st = initialize_state_store(Path(config_dir))
        for rec in st.list_recent_custom_repos():
            nm = getattr(rec, "repo_name", "") or getattr(rec, "repo_url", "")
            out.append(DeletableTarget("custom_repo", getattr(rec, "repo_url", "") or nm,
                                       f"@{nm} (custom repo)", detail=getattr(rec, "repo_url", "")))
    except Exception:
        pass
    q = (query or "").lstrip("@").strip().lower()
    if q:
        out = [t for t in out if q in t.label.lower() or q in t.name.lower()]
    return tuple(out)


_DELETE_VERB = re.compile(r"\b(delete|remove|destroy|purge|wipe|tear\s*down)\b", re.IGNORECASE)
_MENTION = re.compile(r"@([A-Za-z0-9_.\-/]+)")


def message_requests_delete(message: str) -> bool:
    return bool(_DELETE_VERB.search(message or ""))


def resolve_delete_target(message: str, config_dir, *, vm_lister: Callable | None = None,
                          store=None) -> DeletableTarget | None:
    """Plan 150 F7: resolve a 'delete @name' / 'delete the vm/container/repo' phrase to a
    concrete target by matching against the live listers. Returns None when ambiguous/unknown
    (the caller then shows the `@` picker). Does NOT delete anything."""
    if not message_requests_delete(message):
        return None
    targets = mention_suggestions(config_dir, vm_lister=vm_lister, store=store)
    m = _MENTION.search(message or "")
    if m:
        token = m.group(1).lower()
        for t in targets:
            if token == t.name.lower() or token in t.label.lower():
                return t
    # no @mention → if the phrase names exactly one kind and there's exactly one of it, use it
    low = (message or "").lower()
    for kind in ("vm", "container", "image", "custom_repo"):
        word = "repo" if kind == "custom_repo" else ("image" if kind == "image" else kind)
        if word in low:
            of_kind = [t for t in targets if t.kind == kind]
            if len(of_kind) == 1:
                return of_kind[0]
    return None


def perform_delete(target: DeletableTarget, *, config_dir, run_cmd: Callable[[str], int] | None,
                   confirm: Callable[[str], bool], display: Callable[[str], None],
                   store=None, execution_target: str | None = None, region: str | None = None,
                   instance_id: str | None = None) -> bool:
    """Plan 150 F5/F6/F7: CONFIRM once → delete → record for 7 days → tell the user. Returns
    True if deleted. `run_cmd(command)->exit_code` runs a shell delete on the host (injectable).
    A custom-repo target is forgotten from the selection list (no files). Destructive → never
    without the confirm."""
    from state.access import initialize_state_store
    st = store if store is not None else initialize_state_store(Path(config_dir))

    label = _KIND_SUFFIX.get(target.kind, target.kind)
    if not confirm(f"Delete {target.name} ({label})? This is IRREVERSIBLE and cannot be undone."):
        display(f"Cancelled — {target.name} was NOT deleted.")
        return False

    if target.kind == "custom_repo":
        st.delete_recent_custom_repo(repo_url=target.name)
        st.record_deletion(kind="custom_repo", name=target.name, detail="removed from selection list")
        display(f"Removed {target.name} from the repo selection list (your installed files are untouched). "
                "A record of this is kept for 7 days.")
        return True

    # vm / container / cloud → a real destroy command.
    from duckln.repo_bringup import _delete_target_command
    et = execution_target or target.kind  # vm/container/aws/gcp
    cmd = _delete_target_command(execution_target=et, name=target.name, instance_id=instance_id, region=region)
    if cmd is None or run_cmd is None:
        display(f"Can't build a delete command for {target.name} ({label}).")
        return False
    code = run_cmd(cmd)
    if code == 0:
        st.record_deletion(kind=target.kind, name=target.name, detail=cmd)
        display(f"Deleted {target.name} ({label}). A record of this is kept for 7 days.")
        return True
    display(f"Delete of {target.name} ({label}) did not complete (exit {code}).")
    return False
