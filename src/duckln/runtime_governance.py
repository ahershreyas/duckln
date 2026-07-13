"""Runtime guardrails and metadata helpers for Docker, VM, and future cloud flows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Literal


DEFAULT_IDLE_SHUTDOWN_MINUTES = 30
ResourceProvider = Literal["multipass", "docker", "aws", "gcp", "local"]


@dataclass(frozen=True)
class ApprovedCloudShape:
    """One pre-approved cloud shape Duckln may offer to the user."""

    provider: str
    shape: str
    label: str
    notes: str
    cpu_count: int
    memory_gb: int
    default_disk_gb: int
    source_url: str


@dataclass(frozen=True)
class RuntimeTransportHint:
    """Best-effort metadata Duckln can extract from a runtime command."""

    connection_type: str
    docker_name: str | None = None
    stop_command: str | None = None
    logs_command: str | None = None
    resource_kind: str | None = None


APPROVED_CLOUD_SHAPES: tuple[ApprovedCloudShape, ...] = (
    ApprovedCloudShape(
        provider="aws",
        shape="t3.large",
        label="Small CPU",
        notes="Low-cost CPU-first setup and repair tasks.",
        cpu_count=2,
        memory_gb=8,
        default_disk_gb=30,
        source_url="https://aws.amazon.com/ec2/instance-types/t3/",
    ),
    ApprovedCloudShape(
        provider="aws",
        shape="g4dn.xlarge",
        label="T4 GPU",
        notes="Basic NVIDIA T4 GPU path for lighter bring-up and bounded inference.",
        cpu_count=4,
        memory_gb=16,
        default_disk_gb=50,
        source_url="https://aws.amazon.com/ec2/instance-types/g4/",
    ),
    ApprovedCloudShape(
        provider="aws",
        shape="g5.xlarge",
        label="A10G GPU",
        notes="Mid-tier NVIDIA A10G path for heavier model work with stricter approval.",
        cpu_count=4,
        memory_gb=16,
        default_disk_gb=80,
        source_url="https://aws.amazon.com/ec2/instance-types/g5/",
    ),
    ApprovedCloudShape(
        provider="gcp",
        shape="e2-standard-4",
        label="Small CPU",
        notes="Low-cost CPU-first setup and repair tasks.",
        cpu_count=4,
        memory_gb=16,
        default_disk_gb=30,
        source_url="https://cloud.google.com/compute/docs/general-purpose-machines#e2_standard_machine_types",
    ),
    ApprovedCloudShape(
        provider="gcp",
        shape="g2-standard-4",
        label="L4 GPU",
        notes="GPU-backed bounded runtime path with Duckln approval and idle shutdown.",
        cpu_count=4,
        memory_gb=16,
        default_disk_gb=80,
        source_url="https://cloud.google.com/compute/docs/gpus#l4_gpus",
    ),
)


def default_resource_tags(
    *,
    resource_name: str,
    resource_kind: str,
    execution_target: str,
    repo_key: str | None = None,
) -> dict[str, str]:
    """Return the standard Duckln tags recorded for managed resources."""

    tags = {
        "duckln:managed": "true",
        "duckln:resource-name": resource_name,
        "duckln:resource-kind": resource_kind,
        "duckln:execution-target": execution_target,
    }
    if repo_key:
        tags["duckln:repo-key"] = repo_key
    return tags


def approved_cloud_shapes(provider: str | None = None) -> tuple[ApprovedCloudShape, ...]:
    """Return the active approved shortlist for one provider or all providers."""

    if provider is None:
        return APPROVED_CLOUD_SHAPES
    lowered = provider.strip().lower()
    return tuple(shape for shape in APPROVED_CLOUD_SHAPES if shape.provider == lowered)


def render_cloud_guardrail_lines() -> tuple[str, ...]:
    """Return stable cloud safety lines for prompts and user-facing traces."""

    shortlist = ", ".join(f"{shape.provider.upper()} {shape.shape}" for shape in APPROVED_CLOUD_SHAPES)
    return (
        "Cloud actions must stay on Duckln's approved instance shortlist.",
        "Every Duckln-created resource must carry Duckln ownership tags for cleanup and audit.",
        f"Idle shutdown defaults to {DEFAULT_IDLE_SHUTDOWN_MINUTES} minutes unless the user explicitly keeps a resource alive.",
        f"Current shortlist: {shortlist}.",
    )


def describe_runtime_transport(command: str | None, *, cwd: str | None = None, repo_name: str | None = None) -> RuntimeTransportHint:
    """Extract best-effort Docker/runtime metadata from a stored run command."""

    if not isinstance(command, str) or not command.strip():
        return RuntimeTransportHint(connection_type="local")
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    lowered = [token.lower() for token in tokens]
    if lowered[:2] == ["docker", "run"]:
        docker_name = _extract_flag_value(tokens, "--name") or _fallback_docker_name(tokens, repo_name=repo_name)
        return RuntimeTransportHint(
            connection_type="docker",
            docker_name=docker_name,
            stop_command=f"docker stop {shlex.quote(docker_name)}" if docker_name else None,
            logs_command=f"docker logs --tail 40 {shlex.quote(docker_name)}" if docker_name else None,
            resource_kind="docker_container",
        )
    if lowered[:2] == ["docker", "compose"] and "up" in lowered:
        project_name = (
            _extract_flag_value(tokens, "--project-name")
            or _extract_flag_value(tokens, "-p")
            or (Path(cwd).name if cwd else None)
            or (repo_name or "duckln-compose").lower().replace(" ", "-")
        )
        return RuntimeTransportHint(
            connection_type="docker",
            docker_name=project_name,
            stop_command="docker compose down",
            logs_command="docker compose logs --tail 40",
            resource_kind="docker_compose",
        )
    return RuntimeTransportHint(connection_type="local")


def format_usage_footer(*, prompt_tokens: int, completion_tokens: int, total_tokens: int, estimated_cost_usd: float | None) -> str:
    """Render a concise footer-friendly usage string."""

    if total_tokens <= 0 and estimated_cost_usd is None:
        return "tokens 0"
    if estimated_cost_usd is None:
        return f"tokens {prompt_tokens}/{completion_tokens}/{total_tokens}"
    return f"tokens {prompt_tokens}/{completion_tokens}/{total_tokens} • est ${estimated_cost_usd:.4f}"


def _extract_flag_value(tokens: list[str], flag: str) -> str | None:
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return None


def _fallback_docker_name(tokens: list[str], *, repo_name: str | None) -> str | None:
    if repo_name:
        return repo_name.lower().replace(" ", "-")
    for token in reversed(tokens):
        if token.startswith("-"):
            continue
        if "/" in token or ":" in token:
            candidate = token.rsplit("/", 1)[-1].split(":", 1)[0]
            return candidate or None
    return None
