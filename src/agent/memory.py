"""Filesystem-facing agent memory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


AGENT_MEMORY_DIR_NAME = "memory"
AGENTS_FILE_NAME = "AGENTS.md"
SKILLS_DIR_NAME = "skills"
KNOWLEDGE_DIR_NAME = "knowledge"
SESSIONS_DIR_NAME = "sessions"
MAX_NOTE_LENGTH = 1200

DEFAULT_AGENTS_STUB = """# Duckln Memory Contract

This memory area stores short, high-signal notes only.

- Keep summaries concise and reusable.
- Do not store raw logs, transcripts, secrets, or full file dumps.
- Prefer validated setup notes and durable learnings.
"""


@dataclass(frozen=True)
class AgentMemoryPaths:
    """Filesystem locations for the agent-readable memory structure."""

    memory_root: Path
    agents_file: Path
    skills_dir: Path
    knowledge_dir: Path
    sessions_dir: Path


def resolve_agent_memory_paths(config_dir: Path) -> AgentMemoryPaths:
    """Resolve the agent-readable memory paths under Duckln config."""

    memory_root = config_dir / AGENT_MEMORY_DIR_NAME
    return AgentMemoryPaths(
        memory_root=memory_root,
        agents_file=memory_root / AGENTS_FILE_NAME,
        skills_dir=memory_root / SKILLS_DIR_NAME,
        knowledge_dir=memory_root / KNOWLEDGE_DIR_NAME,
        sessions_dir=memory_root / SESSIONS_DIR_NAME,
    )


def initialize_agent_memory(config_dir: Path, *, contract_source: Path | None = None) -> AgentMemoryPaths:
    """Create the agent-readable memory skeleton and seed AGENTS.md."""

    paths = resolve_agent_memory_paths(config_dir)
    for directory in (paths.memory_root, paths.skills_dir, paths.knowledge_dir, paths.sessions_dir):
        directory.mkdir(parents=True, exist_ok=True)

    return paths


def write_session_summary(paths: AgentMemoryPaths, *, session_id: str, summary: str) -> Path:
    """Persist a concise session summary file."""

    return materialize_memory_file(
        paths,
        relative_path=f"{SESSIONS_DIR_NAME}/{_slugify(session_id)}.md",
        content=_normalize_note(summary),
    )


def read_session_summary(paths: AgentMemoryPaths, *, session_id: str) -> str | None:
    """Load a concise session summary file if it exists."""

    destination = paths.sessions_dir / f"{_slugify(session_id)}.md"
    if not destination.exists():
        return None
    return destination.read_text(encoding="utf-8").strip()


def write_knowledge_note(paths: AgentMemoryPaths, *, slug: str, title: str, summary: str) -> Path:
    """Persist a concise reusable knowledge note."""

    return materialize_memory_file(
        paths,
        relative_path=f"{KNOWLEDGE_DIR_NAME}/{_slugify(slug)}.md",
        content=f"# {title.strip()}\n\n{_normalize_note(summary)}",
    )


def materialize_memory_file(paths: AgentMemoryPaths, *, relative_path: str, content: str) -> Path:
    """Materialize a single managed memory file under the filesystem view."""

    destination = _resolve_materialized_path(paths, relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_normalize_materialized_content(content) + "\n", encoding="utf-8")
    return destination


def materialize_memory_view(
    paths: AgentMemoryPaths,
    records: Iterable[tuple[str, str]],
) -> tuple[Path, ...]:
    """Materialize managed memory records into the agent-facing filesystem view."""

    written_paths: list[Path] = []
    for relative_path, content in records:
        written_paths.append(materialize_memory_file(paths, relative_path=relative_path, content=content))
    return tuple(written_paths)


def _normalize_note(summary: str) -> str:
    normalized = " ".join(summary.split())
    if not normalized:
        raise ValueError("Memory notes cannot be empty.")
    if len(normalized) > MAX_NOTE_LENGTH:
        raise ValueError("Memory notes must stay concise; store summaries instead of raw transcripts.")
    return normalized


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "memory-note"


def _resolve_materialized_path(paths: AgentMemoryPaths, relative_path: str) -> Path:
    normalized_path = relative_path.strip().replace("\\", "/")
    if normalized_path == AGENTS_FILE_NAME:
        return paths.agents_file
    for prefix, directory in (
        (f"{SKILLS_DIR_NAME}/", paths.skills_dir),
        (f"{KNOWLEDGE_DIR_NAME}/", paths.knowledge_dir),
        (f"{SESSIONS_DIR_NAME}/", paths.sessions_dir),
    ):
        if normalized_path.startswith(prefix):
            filename = normalized_path.removeprefix(prefix)
            if not filename or "/" in filename or filename.startswith(".") or ".." in filename:
                break
            return directory / filename
    raise ValueError(f"Unsupported managed memory path: {relative_path}")


def _normalize_materialized_content(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines()).strip()
    if not normalized:
        raise ValueError("Materialized memory content cannot be empty.")
    return normalized
