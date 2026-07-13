"""Plan 196 F7: a small Docker inventory primitive.

Duckln had NO way to list Docker images/containers (the whole "how many docker images
are in use / list docker images and their project" class was unanswerable). This module
provides read-only (S0) probes that the natural-language inventory answerers (F6) consume.

Each probe DISTINGUISHES a probe FAILURE (docker missing / daemon down / timeout → the
`ok` flag is False) from a genuine EMPTY inventory (`ok=True`, empty tuple) — mirroring the
Multipass F4 failure-vs-empty design — so callers never report "0 images" when Docker is
simply unreachable. Never raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from duckln.shell import ControlledCommandRunner


@dataclass(frozen=True)
class DockerImage:
    """One `docker images` row (the fields we surface)."""

    repository: str
    tag: str
    image_id: str
    size: str = ""

    @property
    def name(self) -> str:
        if self.repository and self.repository != "<none>":
            return f"{self.repository}:{self.tag}" if self.tag and self.tag != "<none>" else self.repository
        return self.image_id[:12] if self.image_id else "<none>"


@dataclass(frozen=True)
class DockerContainer:
    """One `docker ps [-a]` row."""

    container_id: str
    name: str
    image: str = ""
    status: str = ""
    running: bool = False


@dataclass(frozen=True)
class DockerInventory:
    """Result of a docker probe. `ok=False` → the probe FAILED (docker missing / daemon
    down / timeout); the tuples are then empty and meaningless. `ok=True` with empty
    tuples → Docker is reachable and there is genuinely nothing."""

    ok: bool
    images: tuple[DockerImage, ...] = field(default_factory=tuple)
    containers: tuple[DockerContainer, ...] = field(default_factory=tuple)
    error: str = ""


def _run_json_lines(runner: ControlledCommandRunner, command: str) -> tuple[bool, tuple[dict, ...]]:
    """Run a `docker … --format '{{json .}}'` command → (ok, parsed rows). ok=False on a
    probe failure (non-zero/timeout). Each stdout line is one JSON object."""
    try:
        result = runner.run(command, timeout_seconds=15.0)
    except Exception:
        return False, ()
    if getattr(result, "timed_out", False) or getattr(result, "exit_code", 1) != 0:
        return False, ()
    rows: list[dict] = []
    for line in (getattr(result, "stdout", "") or "").splitlines():
        line = line.strip()
        if not line or "DUCKLN-DONE" in line or "DUCKLN_PRECHECK" in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return True, tuple(rows)


def list_docker_images(*, runner: ControlledCommandRunner | None = None) -> DockerInventory:
    """List Docker images. `ok=False` when Docker isn't installed / the daemon is down /
    the probe timed out (so a caller can say 'Docker isn't running' rather than '0 images')."""
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    ok, rows = _run_json_lines(runner_instance, "docker images --format '{{json .}}'")
    if not ok:
        return DockerInventory(ok=False, error="docker not available (not installed or daemon not running)")
    images = tuple(
        DockerImage(
            repository=str(r.get("Repository") or ""),
            tag=str(r.get("Tag") or ""),
            image_id=str(r.get("ID") or r.get("ImageID") or ""),
            size=str(r.get("Size") or ""),
        )
        for r in rows
    )
    return DockerInventory(ok=True, images=images)


def list_docker_containers(
    *, running_only: bool = False, runner: ControlledCommandRunner | None = None
) -> DockerInventory:
    """List Docker containers. `running_only=True` → only RUNNING containers (`docker ps`,
    i.e. "active / in use"); otherwise ALL (`docker ps -a`). `ok=False` on a probe failure."""
    runner_instance = runner or ControlledCommandRunner(execution_target="local")
    cmd = "docker ps --format '{{json .}}'" if running_only else "docker ps -a --format '{{json .}}'"
    ok, rows = _run_json_lines(runner_instance, cmd)
    if not ok:
        return DockerInventory(ok=False, error="docker not available (not installed or daemon not running)")
    containers = tuple(
        DockerContainer(
            container_id=str(r.get("ID") or ""),
            name=str(r.get("Names") or r.get("Name") or ""),
            image=str(r.get("Image") or ""),
            status=str(r.get("Status") or r.get("State") or ""),
            # `docker ps` (no -a) only lists running; for `-a`, infer from the status/state.
            running=running_only or str(r.get("State") or "").lower() == "running"
            or str(r.get("Status") or "").lower().startswith("up"),
        )
        for r in rows
    )
    return DockerInventory(ok=True, containers=containers)
