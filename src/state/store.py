"""SQLite-backed runtime state helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping


STATE_DIR_NAME = "state"
STATE_DB_FILE_NAME = "duckln-state.sqlite3"
MAX_SUMMARY_LENGTH = 600
MAX_MANAGED_CONTENT_LENGTH = 24000

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_history (
    run_id TEXT PRIMARY KEY,
    command_name TEXT NOT NULL,
    mode TEXT,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    repo_key TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS repo_state (
    repo_key TEXT PRIMARY KEY,
    repo_path TEXT,
    repo_url TEXT,
    branch TEXT,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    last_verified_at TEXT,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vm_linkage (
    vm_name TEXT PRIMARY KEY,
    provider TEXT,
    mode TEXT,
    status TEXT NOT NULL,
    local_repo_path TEXT,
    vm_repo_path TEXT,
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS healthcheck_state (
    scope_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS managed_memory (
    memory_key TEXT PRIMARY KEY,
    memory_kind TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    title TEXT,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
"""


@dataclass(frozen=True)
class StateStorePaths:
    """Filesystem locations used by the SQLite state store."""

    state_dir: Path
    database_file: Path


@dataclass(frozen=True)
class ManagedMemoryRecord:
    """SQLite-backed managed memory record."""

    memory_key: str
    memory_kind: str
    relative_path: str
    title: str | None
    content: str
    updated_at: str


@dataclass(frozen=True)
class RepoStateRecord:
    """SQLite-backed repo state row used for scoped cleanup."""

    repo_key: str
    repo_path: str | None
    repo_url: str | None
    status: str
    summary: str
    updated_at: str


def resolve_state_store_paths(config_dir: Path) -> StateStorePaths:
    """Resolve the SQLite state store path relative to Duckln config."""

    state_dir = config_dir / STATE_DIR_NAME
    return StateStorePaths(
        state_dir=state_dir,
        database_file=state_dir / STATE_DB_FILE_NAME,
    )


class SQLiteStateStore:
    """Small SQLite wrapper for structured, high-signal runtime state."""
    
    def insert_system_snapshot(self, run_id: str, data: dict) -> None:
        with self._connect() as connection:
            cursor = connection.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    os TEXT,
                    architecture TEXT,
                    cpu_cores INTEGER,
                    ram_gb REAL,
                    disk_free_gb REAL,
                    gpu TEXT,
                    cuda_available INTEGER,
                    mps_available INTEGER,
                    summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                INSERT INTO system_snapshots (
                run_id, os, architecture, cpu_cores, ram_gb,
                disk_free_gb, gpu, cuda_available, mps_available, summary
                )   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                   run_id,
                   data.get("os"),
                   data.get("architecture"),
                   data.get("cpu_cores"),
                   data.get("ram_gb"),
                   data.get("disk_free_gb"),
                   data.get("gpu"),
                   int(data.get("cuda_available", False)),
                   int(data.get("mps_available", False)),
                   data.get("summary"),
            ))

    def __init__(self, database_file: Path) -> None:
        self.database_file = database_file

    def initialize(self) -> Path:
        """Create the database and schema if needed."""

        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute("PRAGMA foreign_keys=ON;")
            connection.executescript(SCHEMA_SQL)
        return self.database_file

    def upsert_config_values(self, values: Mapping[str, str]) -> None:
        """Persist non-secret config values for runtime restore and audit."""

        if not values:
            return
        timestamp = _utc_now()
        rows = [(key, str(value), timestamp) for key, value in values.items()]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO config_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def read_config_values(self, *, prefix: str | None = None) -> dict[str, str]:
        """Load persisted config/state values, optionally filtered by key prefix."""

        query = "SELECT key, value FROM config_state"
        params: tuple[str, ...] = ()
        if prefix is not None:
            query += " WHERE key LIKE ?"
            params = (f"{prefix}%",)
        query += " ORDER BY key"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def upsert_managed_memory(
        self,
        *,
        memory_key: str,
        memory_kind: str,
        relative_path: str,
        content: str,
        title: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a SQLite-backed managed memory record."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO managed_memory (
                    memory_key,
                    memory_kind,
                    relative_path,
                    title,
                    content,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_key) DO UPDATE SET
                    memory_kind=excluded.memory_kind,
                    relative_path=excluded.relative_path,
                    title=excluded.title,
                    content=excluded.content,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    memory_key,
                    memory_kind,
                    relative_path,
                    _normalize_memory_title(title),
                    _normalize_managed_content(content),
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def get_managed_memory_record(self, memory_key: str) -> ManagedMemoryRecord | None:
        """Load a managed memory record by key."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT memory_key, memory_kind, relative_path, title, content, updated_at
                FROM managed_memory
                WHERE memory_key = ?
                """,
                (memory_key,),
            ).fetchone()

        if row is None:
            return None
        return ManagedMemoryRecord(
            memory_key=str(row["memory_key"]),
            memory_kind=str(row["memory_kind"]),
            relative_path=str(row["relative_path"]),
            title=str(row["title"]) if row["title"] is not None else None,
            content=str(row["content"]),
            updated_at=str(row["updated_at"]),
        )

    def list_managed_memory_records(self) -> tuple[ManagedMemoryRecord, ...]:
        """Load all managed memory records in materialization order."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT memory_key, memory_kind, relative_path, title, content, updated_at
                FROM managed_memory
                ORDER BY
                    CASE memory_kind
                        WHEN 'agents' THEN 0
                        WHEN 'skill' THEN 1
                        WHEN 'knowledge' THEN 2
                        WHEN 'session' THEN 3
                        ELSE 9
                    END,
                    relative_path
                """
            ).fetchall()

        return tuple(
            ManagedMemoryRecord(
                memory_key=str(row["memory_key"]),
                memory_kind=str(row["memory_kind"]),
                relative_path=str(row["relative_path"]),
                title=str(row["title"]) if row["title"] is not None else None,
                content=str(row["content"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def get_latest_repo_state(self) -> RepoStateRecord | None:
        """Return the most recently updated tracked repo state."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT repo_key, repo_path, repo_url, status, summary, updated_at
                FROM repo_state
                ORDER BY updated_at DESC, rowid DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None
        return RepoStateRecord(
            repo_key=str(row["repo_key"]),
            repo_path=str(row["repo_path"]) if row["repo_path"] is not None else None,
            repo_url=str(row["repo_url"]) if row["repo_url"] is not None else None,
            status=str(row["status"]),
            summary=str(row["summary"]),
            updated_at=str(row["updated_at"]),
        )

    def record_run(
        self,
        *,
        run_id: str,
        command_name: str,
        status: str,
        summary: str,
        mode: str | None = None,
        repo_key: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a concise run-history row."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO run_history (
                    run_id,
                    command_name,
                    mode,
                    status,
                    summary,
                    repo_key,
                    started_at,
                    finished_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    command_name,
                    mode,
                    status,
                    _normalize_summary(summary, field_name="run summary"),
                    repo_key,
                    started_at or _utc_now(),
                    finished_at,
                    _dump_metadata(metadata),
                ),
            )

    def upsert_repo_state(
        self,
        *,
        repo_key: str,
        status: str,
        summary: str,
        repo_path: str | None = None,
        repo_url: str | None = None,
        branch: str | None = None,
        last_verified_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist concise repo state without storing logs or transcripts."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO repo_state (
                    repo_key,
                    repo_path,
                    repo_url,
                    branch,
                    status,
                    summary,
                    last_verified_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key) DO UPDATE SET
                    repo_path=excluded.repo_path,
                    repo_url=excluded.repo_url,
                    branch=excluded.branch,
                    status=excluded.status,
                    summary=excluded.summary,
                    last_verified_at=excluded.last_verified_at,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    repo_key,
                    repo_path,
                    repo_url,
                    branch,
                    status,
                    _normalize_summary(summary, field_name="repo summary"),
                    last_verified_at,
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def upsert_vm_linkage(
        self,
        *,
        vm_name: str,
        status: str,
        summary: str,
        provider: str | None = None,
        mode: str | None = None,
        local_repo_path: str | None = None,
        vm_repo_path: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist concise VM linkage state for future VM flows."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vm_linkage (
                    vm_name,
                    provider,
                    mode,
                    status,
                    local_repo_path,
                    vm_repo_path,
                    summary,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vm_name) DO UPDATE SET
                    provider=excluded.provider,
                    mode=excluded.mode,
                    status=excluded.status,
                    local_repo_path=excluded.local_repo_path,
                    vm_repo_path=excluded.vm_repo_path,
                    summary=excluded.summary,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    vm_name,
                    provider,
                    mode,
                    status,
                    local_repo_path,
                    vm_repo_path,
                    _normalize_summary(summary, field_name="VM summary"),
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def upsert_healthcheck_state(
        self,
        *,
        scope_key: str,
        status: str,
        summary: str,
        checked_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist the latest concise healthcheck result for a scope."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO healthcheck_state (
                    scope_key,
                    status,
                    summary,
                    checked_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    status=excluded.status,
                    summary=excluded.summary,
                    checked_at=excluded.checked_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    scope_key,
                    status,
                    _normalize_summary(summary, field_name="healthcheck summary"),
                    checked_at or _utc_now(),
                    _dump_metadata(metadata),
                ),
            )

    def list_table_names(self) -> tuple[str, ...]:
        """Return user-defined table names for verification."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return tuple(row["name"] for row in rows)

    def clear_session_history(self) -> int:
        """Delete session-history rows and SQLite-backed session summaries."""

        with self._connect() as connection:
            run_count = connection.execute("DELETE FROM run_history").rowcount
            session_count = connection.execute(
                "DELETE FROM managed_memory WHERE memory_kind = ?",
                ("session",),
            ).rowcount
        return int(run_count) + int(session_count)

    def clear_project_state(self, *, repo_key: str) -> int:
        """Delete tracked state for a single project scope."""

        with self._connect() as connection:
            repo_count = connection.execute(
                "DELETE FROM repo_state WHERE repo_key = ?",
                (repo_key,),
            ).rowcount
            run_count = connection.execute(
                "DELETE FROM run_history WHERE repo_key = ?",
                (repo_key,),
            ).rowcount
            memory_count = connection.execute(
                """
                DELETE FROM managed_memory
                WHERE json_extract(metadata_json, '$.repo_key') = ?
                """,
                (repo_key,),
            ).rowcount
        return int(repo_count) + int(run_count) + int(memory_count)

    def clear_factory_state(self) -> int:
        """Delete all tracked SQLite state so Duckln can rebuild a clean baseline."""

        table_names = (
            "config_state",
            "run_history",
            "repo_state",
            "vm_linkage",
            "healthcheck_state",
            "managed_memory",
        )
        deleted_rows = 0
        with self._connect() as connection:
            for table_name in table_names:
                deleted_rows += int(connection.execute(f"DELETE FROM {table_name}").rowcount)
        return deleted_rows

    def vacuum(self) -> None:
        """Run SQLite VACUUM after bounded cleanup work."""

        connection = sqlite3.connect(self.database_file)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_file)
        connection.row_factory = sqlite3.Row
        return connection


def initialize_state_store(config_dir: Path) -> SQLiteStateStore:
    """Initialize the SQLite store for the resolved Duckln config dir."""

    paths = resolve_state_store_paths(config_dir)
    store = SQLiteStateStore(paths.database_file)
    store.initialize()
    return store


def _normalize_summary(summary: str, *, field_name: str) -> str:
    normalized = " ".join(summary.split())
    if not normalized:
        raise ValueError(f"{field_name.capitalize()} cannot be empty.")
    if len(normalized) > MAX_SUMMARY_LENGTH:
        raise ValueError(
            f"{field_name.capitalize()} must stay concise; store a compact summary instead of raw logs."
        )
    return normalized


def _normalize_managed_content(content: str) -> str:
    cleaned_lines = tuple(line.rstrip() for line in content.strip().splitlines())
    normalized = "\n".join(cleaned_lines).strip()
    if not normalized:
        raise ValueError("Managed memory content cannot be empty.")
    if len(normalized) > MAX_MANAGED_CONTENT_LENGTH:
        raise ValueError("Managed memory content must stay concise; do not store raw logs or transcripts.")
    return normalized


def _normalize_memory_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = " ".join(title.split())
    return normalized or None


def _dump_metadata(metadata: Mapping[str, Any] | None) -> str:
    if not metadata:
        return "{}"
    return json.dumps(dict(metadata), sort_keys=True)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
