"""High-signal state access helpers built on top of the SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from agent.memory import (
    AGENTS_FILE_NAME,
    DEFAULT_AGENTS_STUB,
    KNOWLEDGE_DIR_NAME,
    SESSIONS_DIR_NAME,
    SKILLS_DIR_NAME,
    initialize_agent_memory,
    materialize_memory_view,
    read_session_summary,
    resolve_agent_memory_paths,
)
from agent.probe import SystemProbe
from state.store import initialize_state_store


@dataclass(frozen=True)
class MemoryClearResult:
    """Outcome of a bounded memory-clear operation."""

    cleared: bool
    scope: str
    summary: str


def _as_path(config_dir: str | PathLike[str] | Path) -> Path:
    return Path(config_dir)


def write_config_snapshot(config_dir: str | PathLike[str] | Path, values: dict[str, str]) -> None:
    """Persist a non-secret config snapshot to SQLite."""

    initialize_state_store(_as_path(config_dir)).upsert_config_values(values)


def read_config_snapshot(
    config_dir: str | PathLike[str] | Path,
    *,
    prefix: str | None = None,
) -> dict[str, str]:
    """Load a config snapshot from SQLite."""

    return initialize_state_store(_as_path(config_dir)).read_config_values(prefix=prefix)


def record_system_probe(config_dir: str | PathLike[str] | Path, probe: SystemProbe) -> None:
    """Persist a concise system probe summary and normalized fields."""

    store = initialize_state_store(_as_path(config_dir))
    store.upsert_config_values(probe.to_state_values())
    store.record_run(
        run_id="system-probe",
        command_name="system_probe",
        status="ready",
        summary=probe.summary(),
        metadata={
            "os": probe.operating_system,
            "architecture": probe.architecture,
            "gpu_backend": probe.gpu.backend,
        },
    )


def initialize_managed_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    contract_source: Path | None = None,
) -> None:
    """Ensure SQLite-managed memory exists and materialize the agent-facing view."""

    resolved_config_dir = _as_path(config_dir)
    initialize_agent_memory(resolved_config_dir)
    store = initialize_state_store(resolved_config_dir)
    if store.get_managed_memory_record("agents:root") is None:
        store.upsert_managed_memory(
            memory_key="agents:root",
            memory_kind="agents",
            relative_path=AGENTS_FILE_NAME,
            title="Duckln Agent Contract",
            content=_load_contract_content(contract_source),
        )
    materialize_managed_memory_state(resolved_config_dir)


def materialize_managed_memory_state(config_dir: str | PathLike[str] | Path) -> tuple[Path, ...]:
    """Materialize the SQLite-backed managed memory view to the filesystem."""

    resolved_config_dir = _as_path(config_dir)
    initialize_agent_memory(resolved_config_dir)
    records = initialize_state_store(resolved_config_dir).list_managed_memory_records()
    paths = resolve_agent_memory_paths(resolved_config_dir)
    return materialize_memory_view(paths, ((record.relative_path, record.content) for record in records))


def write_skill_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    slug: str,
    title: str,
    summary: str,
) -> Path:
    """Persist a managed skill note in SQLite, then materialize it."""

    relative_path = f"{SKILLS_DIR_NAME}/{_slugify(slug)}.md"
    content = f"# {title.strip()}\n\n{_normalize_summary_text(summary)}"
    return _upsert_and_materialize_memory(
        config_dir,
        memory_key=f"skill:{_slugify(slug)}",
        memory_kind="skill",
        relative_path=relative_path,
        title=title,
        content=content,
    )


def write_knowledge_memory_state(
    config_dir: str | PathLike[str] | Path,
    *,
    slug: str,
    title: str,
    summary: str,
) -> Path:
    """Persist a managed knowledge note in SQLite, then materialize it."""

    relative_path = f"{KNOWLEDGE_DIR_NAME}/{_slugify(slug)}.md"
    content = f"# {title.strip()}\n\n{_normalize_summary_text(summary)}"
    return _upsert_and_materialize_memory(
        config_dir,
        memory_key=f"knowledge:{_slugify(slug)}",
        memory_kind="knowledge",
        relative_path=relative_path,
        title=title,
        content=content,
    )


def write_session_summary_state(
    config_dir: str | PathLike[str] | Path,
    *,
    session_id: str,
    summary: str,
) -> Path:
    """Persist a concise session summary to filesystem memory and SQLite."""

    resolved_config_dir = _as_path(config_dir)
    destination = _upsert_and_materialize_memory(
        resolved_config_dir,
        memory_key=f"session:{_slugify(session_id)}",
        memory_kind="session",
        relative_path=f"{SESSIONS_DIR_NAME}/{_slugify(session_id)}.md",
        title=session_id,
        content=_normalize_summary_text(summary),
    )
    initialize_state_store(resolved_config_dir).record_run(
        run_id=f"session-summary:{session_id}",
        command_name="session_summary",
        status="saved",
        summary=summary,
        metadata={"session_id": session_id},
    )
    return destination


def read_session_summary_state(
    config_dir: str | PathLike[str] | Path,
    *,
    session_id: str,
) -> str | None:
    """Load a concise session summary from filesystem memory."""

    resolved_config_dir = _as_path(config_dir)
    store = initialize_state_store(resolved_config_dir)
    record = store.get_managed_memory_record(f"session:{_slugify(session_id)}")
    if record is not None:
        return record.content.strip()
    return read_session_summary(resolve_agent_memory_paths(resolved_config_dir), session_id=session_id)


def clear_memory_scope(
    config_dir: str | PathLike[str] | Path,
    *,
    scope: str,
    contract_source: Path | None = None,
    config_file: Path | None = None,
) -> MemoryClearResult:
    """Clear a bounded memory scope, resync files, and reclaim SQLite space."""

    resolved_config_dir = _as_path(config_dir)
    store = initialize_state_store(resolved_config_dir)

    if scope == "session":
        deleted_rows = store.clear_session_history()
        materialize_managed_memory_state(resolved_config_dir)
        store.vacuum()
        return MemoryClearResult(
            cleared=True,
            scope=scope,
            summary="Cleared session history and session summaries." if deleted_rows else "Session history was already clear.",
        )

    if scope == "project":
        repo_state = store.get_latest_repo_state()
        if repo_state is None:
            return MemoryClearResult(
                cleared=False,
                scope=scope,
                summary="No tracked project memory was found.",
            )
        deleted_rows = store.clear_project_state(repo_key=repo_state.repo_key)
        materialize_managed_memory_state(resolved_config_dir)
        store.vacuum()
        return MemoryClearResult(
            cleared=True,
            scope=scope,
            summary="Cleared tracked memory for the current project." if deleted_rows else "Current project memory was already clear.",
        )

    if scope == "factory":
        store.clear_factory_state()
        resolved_config_file = config_file or (resolved_config_dir / "config.json")
        if resolved_config_file.exists():
            resolved_config_file.unlink()
        initialize_managed_memory_state(resolved_config_dir, contract_source=contract_source)
        store.vacuum()
        return MemoryClearResult(
            cleared=True,
            scope=scope,
            summary="Cleared Duckln state and reset managed memory.",
        )

    raise ValueError(f"Unsupported memory clear scope: {scope}")


def _upsert_and_materialize_memory(
    config_dir: str | PathLike[str] | Path,
    *,
    memory_key: str,
    memory_kind: str,
    relative_path: str,
    title: str | None,
    content: str,
) -> Path:
    resolved_config_dir = _as_path(config_dir)
    initialize_managed_memory_state(resolved_config_dir)
    store = initialize_state_store(resolved_config_dir)
    store.upsert_managed_memory(
        memory_key=memory_key,
        memory_kind=memory_kind,
        relative_path=relative_path,
        title=title,
        content=content,
    )
    materialize_managed_memory_state(resolved_config_dir)
    paths = resolve_agent_memory_paths(resolved_config_dir)
    if relative_path == AGENTS_FILE_NAME:
        return paths.agents_file
    if relative_path.startswith(f"{SKILLS_DIR_NAME}/"):
        return paths.skills_dir / relative_path.removeprefix(f"{SKILLS_DIR_NAME}/")
    if relative_path.startswith(f"{KNOWLEDGE_DIR_NAME}/"):
        return paths.knowledge_dir / relative_path.removeprefix(f"{KNOWLEDGE_DIR_NAME}/")
    if relative_path.startswith(f"{SESSIONS_DIR_NAME}/"):
        return paths.sessions_dir / relative_path.removeprefix(f"{SESSIONS_DIR_NAME}/")
    raise ValueError(f"Unsupported managed memory path: {relative_path}")


def _load_contract_content(contract_source: Path | None) -> str:
    if contract_source is not None and contract_source.exists():
        return contract_source.read_text(encoding="utf-8")
    return DEFAULT_AGENTS_STUB


def _normalize_summary_text(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Managed memory summaries cannot be empty.")
    return normalized


def _slugify(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value.strip()).strip(
        "-._"
    ) or "memory-note"
