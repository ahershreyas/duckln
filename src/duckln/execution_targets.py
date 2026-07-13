"""Plan 155 F1/F6: the ONE canonical source for execution-target values + membership.

Before this module the target value was spelled `"docker"` in some flows and `"container"`
in others, and three different modules each kept their own literal set
(`_REMOTE_EXECUTION_TARGETS`, `_FS_REMOTE_TARGETS`, `_SANDBOX_TARGETS`). That split let a
`"docker"` target fall through the command wrapper and run on the HOST. Centralizing the
constants + a `normalize_execution_target()` (which maps the `docker` alias → `container`)
removes the divergence and the silent fall-through.
"""

from __future__ import annotations

LOCAL = "local"
VM = "vm"
CONTAINER = "container"
AWS = "aws"
GCP = "gcp"

# `docker` is a user-facing alias for the container runtime — one canonical value downstream.
_ALIASES = {"docker": CONTAINER}


def normalize_execution_target(value: str | None) -> str:
    """Canonicalize a raw target string: lowercase/trim, map the `docker` alias → `container`,
    and treat empty/None as `local`. Use at every persistence + read + routing boundary."""
    t = str(value or LOCAL).strip().lower()
    return _ALIASES.get(t, t) or LOCAL


# --- Membership sets (canonical values only; preserve the pre-existing semantics) ----------
# Off-host targets (run via multipass/cloud, NOT this machine's process namespace).
REMOTE_TARGETS = frozenset({VM, AWS, GCP})
# Targets isolated from the host (recovery may auto-apply system-wide fixes here).
SANDBOX_TARGETS = frozenset({VM, CONTAINER, AWS, GCP, "ssh"})
# Targets whose filesystem/shell tools must go through the canonical command wrapper.
FS_REMOTE_TARGETS = frozenset({VM, CONTAINER, AWS, GCP})
# Targets hosted by THIS machine's hypervisor/engine (resource sizing is host-bounded).
LOCAL_HOSTED_TARGETS = frozenset({VM, CONTAINER})
# Every target Duckln recognizes.
KNOWN_TARGETS = frozenset({LOCAL, VM, CONTAINER, AWS, GCP})


def is_remote_target(value: str | None) -> bool:
    return normalize_execution_target(value) in REMOTE_TARGETS


def is_sandbox_target(value: str | None) -> bool:
    return normalize_execution_target(value) in SANDBOX_TARGETS


def is_fs_remote_target(value: str | None) -> bool:
    return normalize_execution_target(value) in FS_REMOTE_TARGETS


def is_local_hosted_target(value: str | None) -> bool:
    return normalize_execution_target(value) in LOCAL_HOSTED_TARGETS
