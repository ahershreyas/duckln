"""Filesystem-facing agent memory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


AGENT_MEMORY_DIR_NAME = "memory"
AGENTS_FILE_NAME = "AGENTS.md"
SOUL_FILE_NAME = "SOUL.md"
USER_FILE_NAME = "USER.md"
IDENTITY_FILE_NAME = "IDENTITY.md"
TOOLS_DOC_FILE_NAME = "TOOLS.md"
BOOTSTRAP_FILE_NAME = "BOOTSTRAP.md"
TOOLS_FILE_NAME = "tools.json"
SKILLS_DIR_NAME = "skills"
KNOWLEDGE_DIR_NAME = "knowledge"
SESSIONS_DIR_NAME = "sessions"
SUBAGENTS_DIR_NAME = "subagents"
# Plan 58 Bug D: cross-session failure memory for install commands.
FAILURES_DIR_NAME = "failures"
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
    soul_file: Path
    user_file: Path
    identity_file: Path
    tools_doc_file: Path
    bootstrap_file: Path
    tools_file: Path
    skills_dir: Path
    knowledge_dir: Path
    sessions_dir: Path
    subagents_dir: Path


def resolve_agent_memory_paths(config_dir: Path) -> AgentMemoryPaths:
    """Resolve the agent-readable memory paths under Duckln config."""

    memory_root = config_dir / AGENT_MEMORY_DIR_NAME
    return AgentMemoryPaths(
        memory_root=memory_root,
        agents_file=memory_root / AGENTS_FILE_NAME,
        soul_file=memory_root / SOUL_FILE_NAME,
        user_file=memory_root / USER_FILE_NAME,
        identity_file=memory_root / IDENTITY_FILE_NAME,
        tools_doc_file=memory_root / TOOLS_DOC_FILE_NAME,
        bootstrap_file=memory_root / BOOTSTRAP_FILE_NAME,
        tools_file=memory_root / TOOLS_FILE_NAME,
        skills_dir=memory_root / SKILLS_DIR_NAME,
        knowledge_dir=memory_root / KNOWLEDGE_DIR_NAME,
        sessions_dir=memory_root / SESSIONS_DIR_NAME,
        subagents_dir=memory_root / SUBAGENTS_DIR_NAME,
    )


def initialize_agent_memory(config_dir: Path, *, contract_source: Path | None = None) -> AgentMemoryPaths:
    """Create the agent-readable memory skeleton and seed AGENTS.md."""

    paths = resolve_agent_memory_paths(config_dir)
    for directory in (paths.memory_root, paths.skills_dir, paths.knowledge_dir, paths.sessions_dir, paths.subagents_dir):
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

    desired_files: list[tuple[Path, str]] = []
    for relative_path, content in records:
        destination = _resolve_materialized_path(paths, relative_path)
        desired_files.append((destination, _normalize_materialized_content(content)))

    desired_paths = {path for path, _ in desired_files}
    for existing_path in _list_managed_memory_files(paths):
        if existing_path not in desired_paths:
            existing_path.unlink()

    written_paths: list[Path] = []
    for destination, normalized_content in desired_files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(normalized_content + "\n", encoding="utf-8")
        written_paths.append(destination)
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
    if normalized_path == SOUL_FILE_NAME:
        return paths.soul_file
    if normalized_path == USER_FILE_NAME:
        return paths.user_file
    if normalized_path == IDENTITY_FILE_NAME:
        return paths.identity_file
    if normalized_path == TOOLS_DOC_FILE_NAME:
        return paths.tools_doc_file
    if normalized_path == BOOTSTRAP_FILE_NAME:
        return paths.bootstrap_file
    if normalized_path == TOOLS_FILE_NAME:
        return paths.tools_file
    for prefix, directory in (
        (f"{SKILLS_DIR_NAME}/", paths.skills_dir),
        (f"{KNOWLEDGE_DIR_NAME}/", paths.knowledge_dir),
        (f"{SESSIONS_DIR_NAME}/", paths.sessions_dir),
        (f"{SUBAGENTS_DIR_NAME}/", paths.subagents_dir),
    ):
        if normalized_path.startswith(prefix):
            filename = normalized_path.removeprefix(prefix)
            if not filename or filename.startswith(".") or ".." in filename:
                break
            resolved = (directory / filename).resolve()
            if directory.resolve() not in resolved.parents and resolved != directory.resolve():
                break
            return resolved
    raise ValueError(f"Unsupported managed memory path: {relative_path}")


def _normalize_materialized_content(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines()).strip()
    if not normalized:
        raise ValueError("Materialized memory content cannot be empty.")
    return normalized


def _list_managed_memory_files(paths: AgentMemoryPaths) -> tuple[Path, ...]:
    managed_files: list[Path] = []
    for path in (
        paths.agents_file,
        paths.soul_file,
        paths.user_file,
        paths.identity_file,
        paths.tools_doc_file,
        paths.bootstrap_file,
        paths.tools_file,
    ):
        if path.exists():
            managed_files.append(path)
    for directory in (paths.skills_dir, paths.knowledge_dir, paths.sessions_dir):
        managed_files.extend(sorted(path for path in directory.glob("*.md") if path.is_file()))
    managed_files.extend(sorted(path for path in paths.subagents_dir.rglob("*.md") if path.is_file()))
    return tuple(managed_files)
