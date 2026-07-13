"""Approval-gated managed memory updates for core Duckln manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from duckln.subagents import validate_subagent_contract
from duckln.tool_registry import validate_tools_manifest
from state.access import write_knowledge_memory_state, write_skill_memory_state


@dataclass(frozen=True)
class MemoryReviewResult:
    """Outcome of a proposed managed memory update."""

    accepted: bool
    summary: str
    path: Path | None = None


def validate_memory_manifest(relative_path: str, content: str) -> tuple[str, ...]:
    """Validate a core manifest or memory content update before approval."""

    if relative_path == "tools.json":
        return validate_tools_manifest(content)
    if relative_path.startswith("subagents/"):
        return validate_subagent_contract(content)
    if relative_path == "AGENTS.md":
        if not content.strip().startswith("#"):
            return ("AGENTS.md must start with a heading.",)
        return ()
    return ()


def review_managed_memory_update(
    *,
    config_dir: Path,
    relative_path: str,
    title: str,
    content: str,
    approve: Callable[[str], bool] | None,
) -> MemoryReviewResult:
    """Apply approval-gated updates to core memory files and low-risk notes."""

    errors = validate_memory_manifest(relative_path, content)
    if errors:
        return MemoryReviewResult(False, " ".join(errors))
    prompt = f"Duckln proposes updating {relative_path}. Do you want to approve this memory change?"
    if approve is not None and not approve(prompt):
        return MemoryReviewResult(False, f"Duckln left {relative_path} unchanged.")
    if relative_path.startswith("skills/"):
        path = write_skill_memory_state(config_dir, slug=Path(relative_path).stem, title=title, summary=content)
        return MemoryReviewResult(True, f"Updated {relative_path}.", path=path)
    if relative_path.startswith("knowledge/"):
        path = write_knowledge_memory_state(config_dir, slug=Path(relative_path).stem, title=title, summary=content)
        return MemoryReviewResult(True, f"Updated {relative_path}.", path=path)
    return MemoryReviewResult(True, f"{relative_path} passed review.", path=None)
