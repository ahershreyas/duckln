"""Deterministic runtime failure intake and dependency-approval evidence helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


MAX_INCIDENT_LINES = 6
MAX_INCIDENT_CHARS = 420
MAX_DEPENDENCY_ITEMS = 16

_NOISE_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(collecting|downloading|building|installing|looking in indexes)", re.IGNORECASE),
    re.compile(r"^\s*(warning:|note:)", re.IGNORECASE),
    re.compile(r"ubuntu\.com/esm|sudo pro status|ubuntu pro|esm infra|esm apps", re.IGNORECASE),
    re.compile(r"\d+ (standard|security|esm) updates? (available|can be applied)", re.IGNORECASE),
    re.compile(r"^\s*\*\s+\d+ (standard|security|esm)", re.IGNORECASE),
)
_SIGNAL_PATTERNS = (
    re.compile(r"(traceback|error:|exception|failed|not found|no module named|permission denied)", re.IGNORECASE),
    re.compile(r"(npm err!|pnpm err!|cargo|go:|docker:|module not found)", re.IGNORECASE),
)
_PACKAGE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+")
_VERSION_SPLIT = re.compile(r"(==|>=|<=|~=|!=|>|<)")

DUCKLN_SYNTHETIC_LINE_MARKERS: tuple[str, ...] = (
    "duckln classified",
    "specialist route:",
    "toolchain:",
    "supervisor agent does not have",
    "does not have a reliable run command",
    "duckln checked",
    "supervisor classified",
    "supervisor agent paused",
    "best next step:",
    "duckln tried",
    "stopping the repair loop",
    "duckln using the os install hint",
    "duckln spent",
    "duckln noticed a stale",
    "duckln running:",
    "duckln installing prerequisite:",
    "duckln wants to run:",
)

_SHELL_PROMPT_PATTERN = re.compile(r"^\s*[\w.+-]+@[\w.+-]+:[^$#\n]*[#$]\s*")


def is_duckln_synthetic_line(line: str) -> bool:
    """True when the line is Duckln's own narrative output rather than real command stderr."""

    lowered = str(line or "").casefold()
    return any(marker in lowered for marker in DUCKLN_SYNTHETIC_LINE_MARKERS)


def is_shell_prompt_line(line: str) -> bool:
    """True for shell prompts like 'ubuntu@duckln-vm:~/path$ command' that leak into stderr."""

    return bool(_SHELL_PROMPT_PATTERN.match(str(line or "")))


def strip_synthetic_and_prompt_lines(text: str) -> str:
    """Return text with Duckln-narrative and shell-prompt lines removed."""

    if not text:
        return ""
    kept: list[str] = []
    for line in str(text).splitlines():
        if is_duckln_synthetic_line(line):
            continue
        if is_shell_prompt_line(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


_APT_HINT_PATTERN = re.compile(
    r"can be installed with:\s*[\r\n]+\s*(?:sudo\s+)?apt(?:-get)?\s+install\s+([\w\-\.\+ ]+)",
    re.IGNORECASE,
)
_APT_TRY_PATTERN = re.compile(
    r"try:\s*(?:sudo\s+)?apt(?:-get)?\s+install\s+([\w\-\.\+ ]+)",
    re.IGNORECASE,
)
_DNF_PATTERN = re.compile(
    r"(?:try:|can be installed with:)?\s*(?:sudo\s+)?dnf\s+install\s+([\w\-\.\+ ]+)",
    re.IGNORECASE,
)
_YUM_PATTERN = re.compile(
    r"(?:try:|can be installed with:)?\s*(?:sudo\s+)?yum\s+install\s+([\w\-\.\+ ]+)",
    re.IGNORECASE,
)
_BREW_PATTERN = re.compile(
    r"(?:try:|can be installed with:)?\s*brew\s+install\s+([\w\-\.\+ ]+)",
    re.IGNORECASE,
)
_PACKAGE_NAME_GUARD = re.compile(r"^[\w\-\.\+ ]{1,80}$")
_SHELL_METACHAR = re.compile(r"[;|&`$><\n\r]")


def parse_install_hint(error_text: str) -> tuple[str, str] | None:
    """Parse an OS-volunteered install hint from stderr.

    Returns (package_manager, install_command) when a recognized hint with a
    safe package list is found; otherwise None. The package list is validated
    against shell metacharacters before being returned.
    """
    if not error_text:
        return None
    text = str(error_text)
    # Truncate before matching so a huge stderr cannot blow up regex engines.
    sample = text[:4000]
    candidates: tuple[tuple[str, re.Pattern[str], str], ...] = (
        ("apt", _APT_HINT_PATTERN, "sudo apt install -y "),
        ("apt", _APT_TRY_PATTERN, "sudo apt install -y "),
        ("dnf", _DNF_PATTERN, "sudo dnf install -y "),
        ("yum", _YUM_PATTERN, "sudo yum install -y "),
        ("brew", _BREW_PATTERN, "brew install "),
    )
    for manager, pattern, prefix in candidates:
        match = pattern.search(sample)
        if not match:
            continue
        raw = (match.group(1) or "").strip()
        # Stop at the end of a line — never let a hint flow into subsequent text.
        raw = raw.splitlines()[0].strip() if raw else ""
        if not raw or _SHELL_METACHAR.search(raw):
            continue
        # Collapse runs of whitespace; reject anything that doesn't match the guard.
        packages = " ".join(raw.split())
        if not _PACKAGE_NAME_GUARD.match(packages):
            continue
        return manager, f"{prefix}{packages}"
    return None


def fingerprint_stderr(text: str, *, length: int = 200) -> str:
    """Compute a stable fingerprint of stderr for duplicate-failure detection.

    Strips Duckln narrative and shell prompts, collapses runs of whitespace,
    and returns the first `length` characters. Empty input returns an empty
    string.
    """
    cleaned = strip_synthetic_and_prompt_lines(text)
    if not cleaned:
        return ""
    collapsed = " ".join(cleaned.split())
    return collapsed[:length]


@dataclass(frozen=True)
class FailureIncidentSummary:
    """Compressed high-signal runtime incident that can be shown or sent upstream."""

    category: str
    specialist_name: str
    route_family: str
    summary: str
    fatal_line: str | None
    package_hint: str | None
    tool_hint: str | None
    relevant_lines: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeRepairPlan:
    """Deterministic smallest-next-step repair plan for a runtime blocker."""

    action_key: str
    reason: str
    specialist_name: str = "general"
    route_family: str = "runtime_repair"
    verification_hint: str | None = None
    requires_official_docs_lookup: bool = False
    repair_command: str | None = None
    approval_request: "DependencyApprovalRequest | None" = None
    repair_env_vars: tuple[tuple[str, str], ...] = ()
    repair_directories: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyApprovalItem:
    """One installable dependency or install artifact surfaced for explicit approval."""

    item_id: str
    dependency: str
    version: str | None
    reason: str
    source_url: str
    installer: str
    risk_note: str
    manifest_path: str | None = None


@dataclass(frozen=True)
class DependencyApprovalRequest:
    """Structured dependency-install approval payload for the UI layer."""

    prompt: str
    command: str
    project_dir: str | None
    manifest_paths: tuple[str, ...]
    source_urls: tuple[str, ...]
    items: tuple[DependencyApprovalItem, ...]


@dataclass(frozen=True)
class DependencyApprovalDecision:
    """Outcome of a dependency approval review."""

    approved: bool
    approve_all: bool
    selected_item_ids: tuple[str, ...] = ()


def summarize_failure_incident(
    *,
    command: str | None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> FailureIncidentSummary:
    """Compress raw command output into a bounded structured incident summary."""

    combined = "\n".join(part for part in (stderr or "", stdout or "") if part)
    lines = [_normalize_line(line) for line in combined.splitlines()]
    lines = [line for line in lines if line]
    relevant = _collect_relevant_lines(lines)
    signal_lines = [line for line in relevant if any(pattern.search(line) for pattern in _SIGNAL_PATTERNS)]
    fatal_line = signal_lines[0] if signal_lines else (relevant[0] if relevant else (lines[-1] if lines else None))
    searchable = " ".join(part for part in (command or "", fatal_line or "", " ".join(relevant)) if part).lower()
    category = _classify_incident(searchable, command or "")
    package_hint = _extract_package_hint(searchable)
    tool_hint = _extract_tool_hint(command or "", searchable)
    specialist_name = _resolve_specialist_name(category=category, tool_hint=tool_hint, searchable=searchable)
    route_family = _route_family_for_category(category)
    summary = _build_incident_summary(
        category=category,
        specialist_name=specialist_name,
        fatal_line=fatal_line,
        package_hint=package_hint,
        tool_hint=tool_hint,
        relevant_lines=relevant,
    )
    return FailureIncidentSummary(
        category=category,
        specialist_name=specialist_name,
        route_family=route_family,
        summary=summary,
        fatal_line=fatal_line,
        package_hint=package_hint,
        tool_hint=tool_hint,
        relevant_lines=tuple(relevant),
    )


def compress_failure_for_remote(
    *,
    command: str | None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> str:
    """Return a bounded text summary safe to forward instead of raw logs."""

    incident = summarize_failure_incident(command=command, stdout=stdout, stderr=stderr)
    pieces = [incident.summary]
    if incident.relevant_lines:
        pieces.append("Signals: " + " | ".join(incident.relevant_lines[:3]))
    return " ".join(piece.strip() for piece in pieces if piece.strip())[:MAX_INCIDENT_CHARS]


def build_dependency_approval_request(
    *,
    prompt: str,
    command: str,
    project_dir: Path | None,
) -> DependencyApprovalRequest:
    """Build structured dependency approval evidence from repo manifests and install command."""

    normalized = " ".join(command.lower().split())
    manifests, source_urls = _dependency_manifest_sources(normalized, project_dir)
    items = _dependency_items_for_command(normalized, project_dir, source_urls)
    if not items:
        items = (
            DependencyApprovalItem(
                item_id="command",
                dependency=command,
                version=None,
                reason="Duckln detected a dependency install command and could not safely expand individual packages from repo manifests.",
                source_url=source_urls[0] if source_urls else "",
                installer=_installer_label(normalized),
                risk_note="Review the command and manifest source before approving.",
                manifest_path=manifests[0] if manifests else None,
            ),
        )
    return DependencyApprovalRequest(
        prompt=prompt,
        command=command,
        project_dir=str(project_dir) if project_dir is not None else None,
        manifest_paths=manifests,
        source_urls=source_urls,
        items=items,
    )


def build_runtime_dependency_repair_request(
    *,
    repo_name: str,
    project_dir: Path | None,
) -> DependencyApprovalRequest | None:
    """Build the smallest repo-scoped dependency repair request Duckln can verify."""

    command = infer_runtime_dependency_install_command(project_dir)
    if command is None:
        return None
    return build_dependency_approval_request(
        prompt=f"Duckln security review for {repo_name}: repair repo dependencies",
        command=command,
        project_dir=project_dir,
    )


def infer_runtime_dependency_install_command(project_dir: Path | None) -> str | None:
    """Infer the smallest manifest-backed dependency repair command for a repo."""

    if project_dir is None:
        return None
    if (project_dir / "requirements.txt").exists():
        return ".venv/bin/python -m pip install -r requirements.txt"
    if (project_dir / "pyproject.toml").exists() or (project_dir / "setup.py").exists():
        return ".venv/bin/python -m pip install -e ."
    if (project_dir / "environment.yml").exists():
        return "conda env create -f environment.yml"
    if (project_dir / "package.json").exists():
        if (project_dir / "pnpm-lock.yaml").exists():
            return "pnpm install --frozen-lockfile"
        if (project_dir / "yarn.lock").exists():
            return "yarn install --frozen-lockfile"
        if (project_dir / "package-lock.json").exists():
            return "npm ci"
        return "npm install"
    if (project_dir / "Cargo.toml").exists():
        return "cargo build"
    if (project_dir / "go.mod").exists():
        return "go mod download"
    if any((project_dir / name).exists() for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")):
        return "docker compose build"
    if (project_dir / "Dockerfile").exists():
        return "docker build ."
    return None


def plan_runtime_repair(
    *,
    repo_name: str,
    project_dir: Path | None,
    incident: FailureIncidentSummary,
) -> RuntimeRepairPlan:
    """Choose the smallest deterministic runtime repair path before escalating."""

    if incident.category == "user_input_error":
        return RuntimeRepairPlan(
            action_key="guidance_only",
            reason=f"{repo_name} looks blocked by command shape or missing user input rather than a broken environment.",
            specialist_name=incident.specialist_name,
            route_family=incident.route_family,
            verification_hint="Ask Duckln to rerun the exact documented command or provide the missing input value.",
        )

    if incident.category == "auth_missing":
        return RuntimeRepairPlan(
            action_key="auth_guidance",
            reason=(
                "Duckln found a credentials or token blocker. This is not a repo dependency problem, "
                "so Duckln should pause for the smallest auth fix instead of retrying setup blindly."
            ),
            specialist_name="auth",
            route_family="auth_repair",
            verification_hint="Provide the required credential, then rerun the bounded auth or runtime check.",
            requires_official_docs_lookup=True,
        )

    if incident.category == "cloud_auth_failure":
        return RuntimeRepairPlan(
            action_key="cloud_auth_guidance",
            reason=(
                "Duckln found a cloud authentication blocker. The next bounded step is to repair or refresh "
                "cloud auth before any further launch or runtime action."
            ),
            specialist_name="cloud",
            route_family="cloud_repair",
            verification_hint="Re-check cloud auth before retrying the launch or run path.",
            requires_official_docs_lookup=True,
        )

    if incident.category == "cloud_quota_failure":
        return RuntimeRepairPlan(
            action_key="cloud_capacity_escalation",
            reason=(
                "Duckln found a cloud quota or capacity blocker. This needs an explicit user decision on shape, "
                "region, or provider capacity instead of repeated retries."
            ),
            specialist_name="cloud",
            route_family="cloud_repair",
            verification_hint="Choose a different approved cloud target or retry after quota/capacity is available.",
            requires_official_docs_lookup=True,
        )

    if incident.category == "vm_bootstrap_failure":
        return RuntimeRepairPlan(
            action_key="vm_repair_escalation",
            reason=(
                "Duckln found a VM bootstrap or transport blocker. The next step is to repair the VM path "
                "itself before continuing repo setup inside it."
            ),
            specialist_name="vm",
            route_family="vm_repair",
            verification_hint="Verify the VM can launch and accept commands before resuming repo setup.",
            requires_official_docs_lookup=True,
        )

    if incident.category == "docker_missing":
        return RuntimeRepairPlan(
            action_key="docker_install",
            reason=(
                "Docker is not installed. Duckln will install docker.io and docker-compose-plugin "
                "via apt, then re-run the container recipe."
            ),
            specialist_name="docker",
            route_family="docker_repair",
            verification_hint="Verify with `docker --version && docker compose version` after installation.",
            requires_official_docs_lookup=True,
            repair_command=(
                "sudo apt-get update -qq && "
                "sudo apt-get install -y docker.io docker-compose-plugin && "
                "sudo usermod -aG docker ubuntu && "
                "sudo systemctl enable --now docker"
            ),
        )

    if incident.category == "runtime_version_mismatch":
        return RuntimeRepairPlan(
            action_key="runtime_prerequisite_repair",
            reason=(
                "Duckln found a missing or incompatible runtime prerequisite. The next bounded step is to "
                "repair the local toolchain before escalating to a broader repo repair."
            ),
            specialist_name=incident.specialist_name,
            route_family=incident.route_family,
            verification_hint="Install or correct the required runtime, then re-run the smallest verification check.",
            requires_official_docs_lookup=incident.category == "runtime_version_mismatch",
        )

    request = build_runtime_dependency_repair_request(
        repo_name=repo_name,
        project_dir=project_dir,
    )

    docker_env_plan = _build_docker_env_runtime_plan(
        repo_name=repo_name,
        project_dir=project_dir,
        incident=incident,
    )
    if docker_env_plan is not None:
        return docker_env_plan

    if incident.category in {
        "missing_python_module",
        "dependency_install_failure",
        "node_dependency_failure",
        "rust_dependency_failure",
        "go_module_failure",
    } and request is not None:
        return RuntimeRepairPlan(
            action_key="repo_dependency_repair",
            reason=(
                "Duckln found a repo-scoped dependency issue and can retry the smallest manifest-backed "
                "dependency install before escalating to a broader repair path."
            ),
            specialist_name=incident.specialist_name,
            route_family=incident.route_family,
            verification_hint="Retry the smallest manifest-backed dependency step, then rerun the tracked runtime check.",
            repair_command=request.command,
            approval_request=request,
        )

    if incident.category == "missing_command" and request is not None:
        searchable = " ".join(
            part
            for part in (
                incident.package_hint or "",
                incident.tool_hint or "",
                incident.fatal_line or "",
                " ".join(incident.relevant_lines),
            )
            if part
        ).lower()
        if ".bin/" in searchable or any(token in searchable for token in ("node", "npm", "pnpm", "yarn")):
            return RuntimeRepairPlan(
                action_key="repo_dependency_repair",
                reason=(
                    "Duckln found a missing repo-scoped command and will retry the repo dependency install "
                    "before treating this as a system-level prerequisite issue."
                ),
                specialist_name=incident.specialist_name,
                route_family=incident.route_family,
                verification_hint="Retry the repo install step, then rerun the failing repo command.",
                repair_command=request.command,
                approval_request=request,
            )

    if incident.category == "missing_command":
        return RuntimeRepairPlan(
            action_key="runtime_prerequisite_repair",
            reason=(
                "Duckln found a missing runtime command and could not prove it is only repo-local. "
                "The next bounded step is to repair the required toolchain before broader escalation."
            ),
            specialist_name=incident.specialist_name,
            route_family=incident.route_family,
            verification_hint="Install or restore the missing runtime command, then rerun the failing repo command.",
        )

    return RuntimeRepairPlan(
        action_key="escalate",
        reason="Duckln could not prove a smaller manifest-backed repair step, so it should escalate to the bounded broader repair workflow.",
        specialist_name=incident.specialist_name,
        route_family=incident.route_family,
        verification_hint="Use bounded repo-grounded evidence and official docs before choosing the next repair step.",
        requires_official_docs_lookup=incident.category in {
            "network_failure",
            "docker_compose_failure",
            "unsupported_environment",
            "repo_upstream_failure",
            "unknown_failure",
        },
    )


def render_dependency_approval_lines(request: DependencyApprovalRequest) -> tuple[str, ...]:
    """Render compact table-like text lines for fallback approval prompts."""

    lines = [
        "Duckln dependency approval review:",
        f"Command: {request.command}",
    ]
    if request.manifest_paths:
        lines.append("Manifest: " + "; ".join(request.manifest_paths))
    for index, item in enumerate(request.items, start=1):
        version = f" {item.version}" if item.version else ""
        lines.append(
            f"{index}. {item.dependency}{version} | {item.installer} | {item.source_url}"
        )
    return tuple(lines)


def build_approved_dependency_command(
    *,
    request: DependencyApprovalRequest,
    decision: DependencyApprovalDecision,
) -> str | None:
    """Return the command Duckln should execute after dependency approval."""

    if not decision.approved:
        return None
    selected_ids = set(decision.selected_item_ids)
    if decision.approve_all or not selected_ids or len(selected_ids) >= len(request.items):
        return request.command

    selected_items = tuple(item for item in request.items if item.item_id in selected_ids)
    if not selected_items:
        return None

    normalized = " ".join(request.command.lower().split())
    if (
        normalized.startswith("sudo ")
        or " -g " in f" {normalized} "
        or " --global " in f" {normalized} "
        or "deb.nodesource.com" in normalized
    ):
        return request.command
    specs = [_dependency_spec_for_command(item=item, normalized_command=normalized) for item in selected_items]
    specs = [spec for spec in specs if spec]
    if not specs:
        return None

    if "pip install -r requirements.txt" in normalized:
        prefix = request.command.replace("-r requirements.txt", "").strip()
        return f"{prefix} {' '.join(specs)}".strip()
    if "npm install" in normalized:
        return f"npm install {' '.join(specs)}"
    if "pnpm install" in normalized:
        return f"pnpm add {' '.join(specs)}"
    if "yarn install" in normalized:
        return f"yarn add {' '.join(specs)}"
    if "conda env create" in normalized:
        return f"conda install {' '.join(specs)}"
    return request.command if len(selected_items) == len(request.items) else None


def _collect_relevant_lines(lines: list[str]) -> list[str]:
    signals: list[str] = []
    weak_context_lines: list[str] = []
    for line in lines:
        if any(pattern.search(line) for pattern in _SIGNAL_PATTERNS):
            signals.append(line)
            continue
        if not any(pattern.search(line) for pattern in _NOISE_PATTERNS) and len(line) < 180 and len(weak_context_lines) < 2:
            weak_context_lines.append(line)
        if len(signals) >= MAX_INCIDENT_LINES:
            break
    if not signals:
        if weak_context_lines:
            return weak_context_lines
        return lines[-MAX_INCIDENT_LINES:]
    return (signals + weak_context_lines)[:MAX_INCIDENT_LINES]


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", str(line).strip())


def _classify_incident(searchable: str, command: str) -> str:
    searchable = f"{command} {searchable}".lower()
    if any(token in searchable for token in ("no module named", "modulenotfounderror")):
        return "missing_python_module"
    if "invalid spec:" in searchable and "empty section between colons" in searchable:
        return "docker_env_missing"
    if "variable is not set" in searchable and "defaulting to a blank string" in searchable:
        return "docker_env_missing"
    if any(
        token in searchable
        for token in (
            "unable to locate credentials",
            "could not load the default credentials",
            "expiredtoken",
            "request had invalid authentication credentials",
            "gcloud auth",
            "aws configure",
        )
    ):
        return "cloud_auth_failure"
    if any(
        token in searchable
        for token in (
            "quota exceeded",
            "insufficient capacity",
            "resourceexhausted",
            "limitexceeded",
            "instance limit",
        )
    ):
        return "cloud_quota_failure"
    if any(
        token in searchable
        for token in (
            "authentication failed",
            "unauthorized",
            "forbidden",
            "access token",
            "api key",
            "huggingface",
            "hf_token",
            "anthropic_api_key",
            "openai_api_key",
            "docker login",
            "not logged in",
        )
    ):
        return "auth_missing"
    if any(
        token in searchable
        for token in (
            "multipass launch failed",
            "cloud-init",
            "ssh connection failed",
            "instance does not exist",
            "failed to start multipass",
        )
    ):
        return "vm_bootstrap_failure"
    if ("command not found" in searchable or "not found" in searchable) and (
        "docker" in searchable or "docker compose" in searchable
    ):
        return "docker_missing"
    if "command not found" in searchable or re.search(r"\bcommand\s+['\"][^'\"]+['\"]\s+not found\b", searchable):
        return "missing_command"
    if any(
        token in searchable
        for token in (
            "requires python",
            "unsupported engine",
            "node version",
            "python version",
            "requires node",
            "requires go",
            "requires rust",
            "not supported on this python",
        )
    ):
        return "runtime_version_mismatch"
    if any(token in searchable for token in ("permission denied", "operation not permitted")):
        return "permission_denied"
    if any(token in searchable for token in ("address already in use", "eaddrinuse", "port is already allocated")):
        return "port_conflict"
    if any(token in searchable for token in ("could not resolve host", "temporary failure in name resolution", "network is unreachable", "certificate verify failed")):
        return "network_failure"
    if any(token in searchable for token in ("npm err!", "pnpm err!", "yarn error", "cannot find module", "package-lock.json")):
        return "node_dependency_failure"
    if any(token in searchable for token in ("cargo", "rustc", "crate", "cargo.toml")):
        return "rust_dependency_failure"
    if any(token in searchable for token in ("go: ", "go.mod", "no required module provides package")):
        return "go_module_failure"
    if any(token in searchable for token in ("docker compose", "failed to solve", "pull access denied", "no such service")):
        return "docker_compose_failure"
    if any(token in searchable for token in ("no matching distribution", "failed building wheel", "npm err!", "cargo", "go: ", "dependency resolution")):
        return "dependency_install_failure"
    if any(token in searchable for token in ("cuda is not available", "cuda is unsupported", "mps is not supported", "unsupported platform", "apple silicon detected")):
        return "unsupported_environment"
    if any(token in searchable for token in ("usage:", "unrecognized arguments", "invalid choice", "no such option")):
        return "user_input_error"
    if any(token in searchable for token in ("known issue", "upstream bug", "segmentation fault", "panic:", "fatal error:")):
        return "repo_upstream_failure"
    if any(token in searchable for token in ("traceback", "exception", "failed to start", "usage:")):
        return "runtime_failure"
    return "unknown_failure"


def _route_family_for_category(category: str) -> str:
    if category.startswith("cloud_"):
        return "cloud_repair"
    if category.startswith("vm_"):
        return "vm_repair"
    if category.startswith("docker_"):
        return "docker_repair"
    if category == "auth_missing":
        return "auth_repair"
    if category in {"node_dependency_failure", "runtime_version_mismatch"}:
        return "node_repair"
    if category == "rust_dependency_failure":
        return "rust_repair"
    if category == "go_module_failure":
        return "go_repair"
    if category in {"missing_python_module", "dependency_install_failure"}:
        return "python_repair"
    return "runtime_repair"


def _resolve_specialist_name(*, category: str, tool_hint: str | None, searchable: str) -> str:
    if category.startswith("cloud_"):
        return "cloud"
    if category.startswith("vm_"):
        return "vm"
    if category.startswith("docker_"):
        return "docker"
    if category == "auth_missing":
        return "auth"
    if category == "node_dependency_failure":
        return "node"
    if category == "rust_dependency_failure":
        return "rust"
    if category == "go_module_failure":
        return "go"
    if category in {"missing_python_module", "dependency_install_failure"}:
        if tool_hint in {"node", "cargo", "go", "docker"}:
            return tool_hint
        return "python"
    if category == "runtime_version_mismatch":
        if tool_hint:
            return tool_hint
        if "node" in searchable or "npm" in searchable:
            return "node"
        if "python" in searchable:
            return "python"
    return tool_hint or "general"


def _extract_package_hint(searchable: str) -> str | None:
    module_match = re.search(r"no module named ['\"]?([a-zA-Z0-9_.-]+)", searchable)
    if module_match is not None:
        return module_match.group(1)
    distribution_match = re.search(r"for ([a-zA-Z0-9_.-]+)==", searchable)
    if distribution_match is not None:
        return distribution_match.group(1)
    missing_binary_match = re.search(
        r"(?:command not found|no such file or directory:)\s*['\"]?([a-zA-Z0-9_.-]+)['\"]?",
        searchable,
    )
    if missing_binary_match is None:
        missing_binary_match = re.search(
            r"\bcommand\s+['\"]([a-zA-Z0-9_.-]+)['\"]\s+not found\b",
            searchable,
        )
    if missing_binary_match is not None:
        candidate = missing_binary_match.group(1)
        if candidate not in {"venv", "python", "python3"} or "command not found" in searchable:
            return candidate
    ffmpeg_match = re.search(r"['\"](ffmpeg)['\"]", searchable)
    if ffmpeg_match is not None:
        return ffmpeg_match.group(1)
    return None


def _extract_tool_hint(command: str, searchable: str) -> str | None:
    if "pip" in command or "python" in command or "pip" in searchable:
        return "python"
    if any(token in command for token in ("npm", "pnpm", "yarn")) or any(token in searchable for token in ("npm", "pnpm", "yarn")):
        return "node"
    if "cargo" in command or "cargo" in searchable:
        return "cargo"
    if "go " in command or "go " in searchable:
        return "go"
    if "docker" in command or "docker" in searchable:
        return "docker"
    return None


def _build_incident_summary(
    *,
    category: str,
    specialist_name: str,
    fatal_line: str | None,
    package_hint: str | None,
    tool_hint: str | None,
    relevant_lines: list[str],
) -> str:
    parts = [f"Duckln classified this blocker as {category.replace('_', ' ')}."]
    if specialist_name and specialist_name != "general":
        parts.append(f"Specialist route: {specialist_name}.")
    if tool_hint:
        parts.append(f"Toolchain: {tool_hint}.")
    if package_hint:
        parts.append(f"Likely package/module: {package_hint}.")
    if fatal_line:
        parts.append(f"Fatal line: {fatal_line[:180]}.")
    elif relevant_lines:
        parts.append(f"Primary signal: {relevant_lines[0][:180]}.")
    return " ".join(parts)[:MAX_INCIDENT_CHARS]


def _build_docker_env_runtime_plan(
    *,
    repo_name: str,
    project_dir: Path | None,
    incident: FailureIncidentSummary,
) -> RuntimeRepairPlan | None:
    if project_dir is None:
        return None
    searchable = " ".join(
        part
        for part in (
            incident.summary,
            incident.fatal_line or "",
            " ".join(incident.relevant_lines),
        )
        if part
    )
    env_names = tuple(dict.fromkeys(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", searchable)))
    compose_exists = any((project_dir / name).exists() for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"))
    if incident.category != "docker_env_missing" or not compose_exists or not env_names:
        return None
    repo_slug = re.sub(r"[^a-z0-9]+", "-", repo_name.lower()).strip("-") or "repo"
    repairs: list[tuple[str, str]] = []
    directories: list[str] = []
    for name in env_names:
        suffix = name.lower().replace("_", "-")
        value = str(project_dir / f".duckln-{repo_slug}-{suffix}")
        repairs.append((name, value))
        if any(token in name.lower() for token in ("dir", "path", "workspace", "config", "data", "cache")):
            directories.append(value)
    if not repairs:
        return None
    env_prefix = " ".join(f"{name}={value}" for name, value in repairs)
    return RuntimeRepairPlan(
        action_key="docker_env_repair",
        reason=(
            "Duckln found a Docker compose environment-variable blocker and can retry the documented "
            "compose path with Duckln-managed local directories instead of asking to rerun the same command."
        ),
        specialist_name="docker",
        route_family="docker_repair",
        verification_hint="Retry the compose path with managed env vars, then rerun the failing Docker command.",
        requires_official_docs_lookup=True,
        repair_command=f"{env_prefix} docker compose up",
        repair_env_vars=tuple(repairs),
        repair_directories=tuple(dict.fromkeys(directories)),
    )


def _dependency_manifest_sources(normalized_command: str, project_dir: Path | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    manifests: list[str] = []
    sources: list[str] = []
    if "pip install -r requirements.txt" in normalized_command:
        manifests.extend(_existing_paths(project_dir, ("requirements.txt",)))
        sources.append("https://packaging.python.org/en/latest/tutorials/installing-packages/")
    elif "pip install -e ." in normalized_command:
        manifests.extend(_existing_paths(project_dir, ("pyproject.toml", "setup.py")))
        sources.append("https://packaging.python.org/en/latest/tutorials/packaging-projects/")
    elif any(token in normalized_command for token in ("npm install", "pnpm install", "yarn install")):
        manifests.extend(_existing_paths(project_dir, ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")))
        if "pnpm install" in normalized_command:
            sources.append("https://pnpm.io/installation")
        elif "yarn install" in normalized_command:
            sources.append("https://yarnpkg.com/getting-started")
        else:
            sources.append("https://docs.npmjs.com/")
    elif "deb.nodesource.com" in normalized_command or "nodejs" in normalized_command or "corepack enable" in normalized_command:
        sources.append("https://github.com/nodesource/distributions")
        sources.append("https://nodejs.org/en/download")
    elif "conda env create" in normalized_command:
        manifests.extend(_existing_paths(project_dir, ("environment.yml",)))
        sources.append("https://docs.conda.io/projects/conda/en/latest/")
    elif any(token in normalized_command for token in ("cargo build", "cargo fetch")):
        manifests.extend(_existing_paths(project_dir, ("Cargo.toml",)))
        sources.append("https://doc.rust-lang.org/cargo/")
    elif any(token in normalized_command for token in ("go mod download", "go build")):
        manifests.extend(_existing_paths(project_dir, ("go.mod",)))
        sources.append("https://go.dev/doc/modules/managing-dependencies")
    elif "docker compose build" in normalized_command:
        manifests.extend(_existing_paths(project_dir, ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "Dockerfile")))
        sources.append("https://docs.docker.com/compose/")
    elif "docker build" in normalized_command:
        manifests.extend(_existing_paths(project_dir, ("Dockerfile",)))
        sources.append("https://docs.docker.com/build/")
    return tuple(dict.fromkeys(manifests)), tuple(dict.fromkeys(sources))


def _dependency_items_for_command(
    normalized_command: str,
    project_dir: Path | None,
    source_urls: tuple[str, ...],
) -> tuple[DependencyApprovalItem, ...]:
    source = source_urls[0] if source_urls else ""
    if "pip install -r requirements.txt" in normalized_command:
        return _requirements_items(project_dir, source)
    if "pip install -e ." in normalized_command:
        return _editable_python_items(project_dir, source)
    if any(token in normalized_command for token in ("npm install", "pnpm install", "yarn install")):
        return _package_json_items(project_dir, source, installer=_installer_label(normalized_command))
    if "conda env create" in normalized_command:
        return _conda_items(project_dir, source)
    if any(token in normalized_command for token in ("cargo build", "cargo fetch")):
        return _single_manifest_item(project_dir, "Cargo.toml", "Rust crate dependencies", source, installer="cargo")
    if any(token in normalized_command for token in ("go mod download", "go build")):
        return _go_module_items(project_dir, source)
    if "docker compose build" in normalized_command:
        return _single_manifest_item(project_dir, "docker-compose.yml", "Docker compose services", source, installer="docker")
    if "docker build" in normalized_command:
        return _single_manifest_item(project_dir, "Dockerfile", "Docker image build context", source, installer="docker")
    return ()


def _requirements_items(project_dir: Path | None, source: str) -> tuple[DependencyApprovalItem, ...]:
    if project_dir is None:
        return ()
    target = project_dir / "requirements.txt"
    if not target.exists():
        return ()
    items: list[DependencyApprovalItem] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        dependency, version = _split_requirement(stripped)
        items.append(
            DependencyApprovalItem(
                item_id=f"req:{dependency}",
                dependency=dependency,
                version=version,
                reason="Required by requirements.txt for this repo setup path.",
                source_url=source,
                installer="pip",
                risk_note="Review the pinned package and version before approving.",
                manifest_path=str(target.resolve()),
            )
        )
        if len(items) >= MAX_DEPENDENCY_ITEMS:
            break
    return tuple(items)


def _editable_python_items(project_dir: Path | None, source: str) -> tuple[DependencyApprovalItem, ...]:
    if project_dir is None:
        return ()
    pyproject = project_dir / "pyproject.toml"
    setup_py = project_dir / "setup.py"
    manifest = pyproject if pyproject.exists() else setup_py if setup_py.exists() else None
    if manifest is None:
        return ()
    name = project_dir.name
    return (
        DependencyApprovalItem(
            item_id=f"editable:{name}",
            dependency=name,
            version=None,
            reason="Editable install of the selected repo package.",
            source_url=source,
            installer="pip",
            risk_note="Editable installs can run project-defined build hooks; review the package manifest first.",
            manifest_path=str(manifest.resolve()),
        ),
    )


def _package_json_items(project_dir: Path | None, source: str, *, installer: str) -> tuple[DependencyApprovalItem, ...]:
    if project_dir is None:
        return ()
    target = project_dir / "package.json"
    if not target.exists():
        return ()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ()
    combined: list[tuple[str, str, str]] = []
    for field, reason in (("dependencies", "Application dependency from package.json."), ("devDependencies", "Development dependency from package.json.")):
        mapping = data.get(field)
        if isinstance(mapping, dict):
            for dependency, version in mapping.items():
                combined.append((str(dependency), str(version), reason))
    items = [
        DependencyApprovalItem(
            item_id=f"npm:{dependency}",
            dependency=dependency,
            version=version,
            reason=reason,
            source_url=source,
            installer=installer,
            risk_note="Review package origin and version range before approving.",
            manifest_path=str(target.resolve()),
        )
        for dependency, version, reason in combined[:MAX_DEPENDENCY_ITEMS]
    ]
    return tuple(items)


def _conda_items(project_dir: Path | None, source: str) -> tuple[DependencyApprovalItem, ...]:
    if project_dir is None:
        return ()
    target = project_dir / "environment.yml"
    if not target.exists():
        return ()
    items: list[DependencyApprovalItem] = []
    active_dependencies = False
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("dependencies:"):
            active_dependencies = True
            continue
        if active_dependencies and stripped.startswith("- "):
            entry = stripped[2:].strip()
            if entry.startswith("pip:"):
                continue
            dependency, version = _split_requirement(entry)
            items.append(
                DependencyApprovalItem(
                    item_id=f"conda:{dependency}",
                    dependency=dependency,
                    version=version,
                    reason="Environment dependency from environment.yml.",
                    source_url=source,
                    installer="conda",
                    risk_note="Review the conda package and channel assumptions before approving.",
                    manifest_path=str(target.resolve()),
                )
            )
        if len(items) >= MAX_DEPENDENCY_ITEMS:
            break
    return tuple(items)


def _go_module_items(project_dir: Path | None, source: str) -> tuple[DependencyApprovalItem, ...]:
    if project_dir is None:
        return ()
    target = project_dir / "go.mod"
    if not target.exists():
        return ()
    items: list[DependencyApprovalItem] = []
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith(("require ",)):
            continue
        remainder = stripped[len("require ") :].strip().strip("()")
        pieces = remainder.split()
        if not pieces:
            continue
        dependency = pieces[0]
        version = pieces[1] if len(pieces) > 1 else None
        items.append(
            DependencyApprovalItem(
                item_id=f"go:{dependency}",
                dependency=dependency,
                version=version,
                reason="Go module dependency from go.mod.",
                source_url=source,
                installer="go",
                risk_note="Review the module path and version before approving.",
                manifest_path=str(target.resolve()),
            )
        )
        if len(items) >= MAX_DEPENDENCY_ITEMS:
            break
    if items:
        return tuple(items)
    return (
        DependencyApprovalItem(
            item_id=f"go:{target.name}",
            dependency="go.mod",
            version=None,
            reason="Go module graph will be resolved from go.mod.",
            source_url=source,
            installer="go",
            risk_note="Review the repo's module graph before approving.",
            manifest_path=str(target.resolve()),
        ),
    )


def _single_manifest_item(
    project_dir: Path | None,
    manifest_name: str,
    reason: str,
    source: str,
    *,
    installer: str,
) -> tuple[DependencyApprovalItem, ...]:
    manifest_path = None
    if project_dir is not None:
        candidate = project_dir / manifest_name
        if candidate.exists():
            manifest_path = str(candidate.resolve())
    return (
        DependencyApprovalItem(
            item_id=f"{installer}:{manifest_name}",
            dependency=manifest_name,
            version=None,
            reason=reason,
            source_url=source,
            installer=installer,
            risk_note="Review the build manifest before approving.",
            manifest_path=manifest_path,
        ),
    )


def _existing_paths(project_dir: Path | None, names: tuple[str, ...]) -> list[str]:
    if project_dir is None:
        return list(names)
    resolved: list[str] = []
    for name in names:
        path = project_dir / name
        if path.exists():
            resolved.append(str(path.resolve()))
    return resolved or list(names)


def _split_requirement(raw: str) -> tuple[str, str | None]:
    cleaned = raw.split(";", 1)[0].strip()
    parts = _VERSION_SPLIT.split(cleaned, maxsplit=1)
    dependency_match = _PACKAGE_TOKEN.match(parts[0].strip()) if parts else None
    dependency = dependency_match.group(0) if dependency_match is not None else cleaned
    version = None
    if len(parts) >= 3:
        version = f"{parts[1]}{parts[2].strip()}"
    return dependency, version


def _installer_label(normalized_command: str) -> str:
    if "deb.nodesource.com" in normalized_command:
        return "nodesource"
    if "apt-get install" in normalized_command or "apt install" in normalized_command:
        return "apt"
    if "dnf install" in normalized_command:
        return "dnf"
    if "yum install" in normalized_command:
        return "yum"
    if "brew install" in normalized_command:
        return "brew"
    if "winget install" in normalized_command:
        return "winget"
    if "pnpm install" in normalized_command:
        return "pnpm"
    if "yarn install" in normalized_command:
        return "yarn"
    if "npm install" in normalized_command:
        return "npm"
    if "conda env create" in normalized_command:
        return "conda"
    if "cargo " in normalized_command:
        return "cargo"
    if "go " in normalized_command:
        return "go"
    if "docker" in normalized_command:
        return "docker"
    return "pip"


def _dependency_spec_for_command(*, item: DependencyApprovalItem, normalized_command: str) -> str:
    version = item.version or ""
    if any(token in normalized_command for token in ("npm install", "pnpm install", "yarn install")):
        if version and version[0].isdigit():
            return f"{item.dependency}@{version}"
        if version and version.startswith(("^", "~")):
            return f"{item.dependency}@{version}"
        return item.dependency if not version else f"{item.dependency}@{version.lstrip('=')}"
    if "conda" in normalized_command and version:
        cleaned = version.lstrip("=<>~!")
        return f"{item.dependency}={cleaned}" if cleaned else item.dependency
    return f"{item.dependency}{version}"


# Plan 61 Fix B: classify apt-style install failures so Duckln can surface
# OS-specific user guidance instead of silently retrying.
_APT_SUDO_PASSWORD_PATTERNS = (
    "sudo: a password is required",
    "sudo: no tty",
    "a terminal is required to read the password",
    "sudo: a terminal is required",
    "sudo: could not read password",
)

_APT_NETWORK_PATTERNS = (
    "could not resolve",
    "temporary failure resolving",
    "unable to fetch some archives",
    "failed to fetch",
    "connection timed out",
    "network is unreachable",
    "no route to host",
)

_APT_LOCALE_PATTERNS = (
    "setlocale: lc_",
    "locale.error",
    "locale not supported",
    "perl: warning: setting locale failed",
    "cannot set lc_",
)

_APT_LOCK_PATTERNS = (
    "could not get lock",
    "dpkg was interrupted",
    "unable to acquire the dpkg frontend lock",
    "another process is using",
    "e: could not open lock file",
)


def classify_apt_failure(stderr: str) -> str:
    """Classify an apt/install stderr into one of:
    'sudo_password', 'network', 'locale', 'lock_file', 'other'.

    Returns 'other' for any unrecognized failure. Repo-agnostic — works for
    any install command that uses apt under the hood on Ubuntu/Debian.
    """
    if not stderr:
        return "other"
    text = stderr.lower()
    if any(p in text for p in _APT_SUDO_PASSWORD_PATTERNS):
        return "sudo_password"
    if any(p in text for p in _APT_NETWORK_PATTERNS):
        return "network"
    if any(p in text for p in _APT_LOCALE_PATTERNS):
        return "locale"
    if any(p in text for p in _APT_LOCK_PATTERNS):
        return "lock_file"
    return "other"


_APT_FAILURE_GUIDANCE: dict[str, str] = {
    "sudo_password": (
        "Duckln couldn't install (sudo wants a password). On the VM, run `sudo -v` "
        "once to cache your password, then retry. Or configure passwordless sudo for "
        "this user via `sudo visudo` and add: <user> ALL=(ALL) NOPASSWD: ALL."
    ),
    "network": (
        "Duckln couldn't install (network unreachable). On the VM, verify: "
        "`ping -c1 1.1.1.1`. If behind a corporate proxy, set `http_proxy` and "
        "`https_proxy` environment variables before retrying."
    ),
    "locale": (
        "Duckln couldn't install (locale not configured). Run on the VM: "
        "`sudo locale-gen en_US.UTF-8 && sudo update-locale LANG=en_US.UTF-8`, "
        "then start a new shell and retry."
    ),
    "lock_file": (
        "Duckln couldn't install (apt lock held by another process — usually "
        "unattended-upgrades). Wait for it to finish, then retry. Check what's "
        "holding the lock with: `sudo lsof /var/lib/dpkg/lock-frontend`."
    ),
}


def apt_failure_guidance(category: str) -> str | None:
    """Return the user-facing OS-specific guidance for a classified apt failure,
    or None when no guidance is registered (category 'other')."""
    return _APT_FAILURE_GUIDANCE.get(category)
