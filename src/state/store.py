"""SQLite-backed runtime state helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


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
    execution_target TEXT NOT NULL DEFAULT 'local',
    vm_name TEXT,
    active_flag INTEGER NOT NULL DEFAULT 0,
    managed_by_duckln INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    last_verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT '',
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
    created_at TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS repo_knowledge (
    repo_key TEXT PRIMARY KEY,
    repo_name TEXT NOT NULL,
    repo_url TEXT,
    source TEXT NOT NULL,
    summary TEXT NOT NULL,
    setup_complexity TEXT,
    local_vm_recommendation TEXT,
    cpu_profile TEXT,
    ram_profile TEXT,
    gpu_profile TEXT,
    apple_silicon_notes TEXT,
    required_tools_json TEXT NOT NULL DEFAULT '[]',
    setup_files_json TEXT NOT NULL DEFAULT '[]',
    entrypoints_json TEXT NOT NULL DEFAULT '[]',
    validation_status TEXT NOT NULL DEFAULT 'observed',
    freshness_checked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS learning_records (
    learning_key TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    state TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    correction_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS promoted_heuristics (
    heuristic_key TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS recent_custom_repos (
    repo_url TEXT PRIMARY KEY,
    repo_name TEXT NOT NULL,
    stars INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    framework TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS usage_snapshots (
    scope_key TEXT PRIMARY KEY,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS managed_resources (
    resource_key TEXT PRIMARY KEY,
    resource_kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    display_name TEXT NOT NULL,
    execution_target TEXT NOT NULL DEFAULT 'local',
    region TEXT,
    shape TEXT,
    install_root TEXT,
    status TEXT NOT NULL,
    idle_timeout_minutes INTEGER,
    last_activity_at TEXT,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    message_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    top_level_category TEXT NOT NULL,
    final_route_family TEXT NOT NULL,
    final_intent TEXT NOT NULL,
    resolved_subject TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    margin REAL NOT NULL DEFAULT 0.0,
    workflow_state_json TEXT NOT NULL DEFAULT '{}',
    followup_state_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS loops (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    schedule TEXT NOT NULL,
    task_description TEXT NOT NULL,
    tool_scope TEXT NOT NULL,
    auto_fix INTEGER NOT NULL DEFAULT 1,
    max_fix_attempts INTEGER NOT NULL DEFAULT 3,
    notify_on TEXT NOT NULL DEFAULT 'failure',
    safety_class TEXT NOT NULL DEFAULT 'S1',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run TEXT,
    last_status TEXT,
    last_summary TEXT,
    os_type TEXT NOT NULL,
    expires_at TEXT,
    expiry_days INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS loop_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    actions_taken TEXT,
    fix_attempts INTEGER NOT NULL DEFAULT 0,
    escalated INTEGER NOT NULL DEFAULT 0,
    raw_output TEXT,
    FOREIGN KEY (loop_id) REFERENCES loops(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS deletion_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    deleted_at TEXT NOT NULL
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
    execution_target: str
    vm_name: str | None
    active_flag: bool
    managed_by_duckln: bool
    status: str
    summary: str
    last_verified_at: str | None
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VmLinkageRecord:
    """Tracked VM state used by target-aware inventory views."""

    vm_name: str
    provider: str | None
    mode: str | None
    status: str
    local_repo_path: str | None
    vm_repo_path: str | None
    summary: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RepoKnowledgeRecord:
    """SQLite-backed repo knowledge row for shared agent grounding."""

    repo_key: str
    repo_name: str
    repo_url: str | None
    source: str
    summary: str
    setup_complexity: str | None
    local_vm_recommendation: str | None
    cpu_profile: str | None
    ram_profile: str | None
    gpu_profile: str | None
    apple_silicon_notes: str | None
    required_tools: tuple[str, ...]
    setup_files: tuple[str, ...]
    entrypoints: tuple[str, ...]
    validation_status: str
    freshness_checked_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LearningRecord:
    """Structured bounded learning observation."""

    learning_key: str
    family: str
    subject_key: str
    state: str
    summary: str
    evidence_count: int
    success_count: int
    failure_count: int
    correction_count: int
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PromotedHeuristicRecord:
    """Compact promoted heuristic used to bias future routing and ranking."""

    heuristic_key: str
    family: str
    subject_key: str
    summary: str
    confidence: float
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RecentCustomRepoRecord:
    """Recent user-provided public GitHub repo stored for reuse."""

    repo_url: str
    repo_name: str
    stars: int
    description: str
    category: str
    framework: str
    last_updated: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class UsageSnapshotRecord:
    """Aggregated LLM usage totals Duckln can surface in the UI."""

    scope_key: str
    provider: str | None
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None
    turn_count: int
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ManagedResourceRecord:
    """Tracked VM, Docker, or future cloud resources Duckln manages."""

    resource_key: str
    resource_kind: str
    provider: str
    display_name: str
    execution_target: str
    region: str | None
    shape: str | None
    install_root: str | None
    status: str
    idle_timeout_minutes: int | None
    last_activity_at: str | None
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RoutingDecisionRecord:
    """Observed front-door routing decision stored for debugging and regression work."""

    decision_id: int
    created_at: str
    message_text: str
    normalized_text: str
    top_level_category: str
    final_route_family: str
    final_intent: str
    resolved_subject: str | None
    confidence: float
    margin: float
    workflow_state: dict[str, Any]
    followup_state: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LoopRecord:
    """Persistent background loop specification."""

    id: str
    name: str
    type: str
    schedule: str
    task_description: str
    tool_scope: tuple[str, ...]
    auto_fix: bool
    max_fix_attempts: int
    notify_on: str
    safety_class: str
    active: bool
    created_at: str
    last_run: str | None
    last_status: str | None
    last_summary: str | None
    os_type: str
    expires_at: str | None = None
    expiry_days: int = 30


@dataclass(frozen=True)
class LoopResultRecord:
    """One bounded background-loop execution result."""

    id: int
    loop_id: str
    run_at: str
    status: str
    summary: str
    actions_taken: str | None
    fix_attempts: int
    escalated: bool
    raw_output: str | None


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
            self._ensure_repo_state_columns(connection)
            self._ensure_created_at_columns(connection)
            self._ensure_loop_columns(connection)
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

    def delete_config_values(self, keys: tuple[str, ...]) -> int:
        """Delete stale config keys from the SQLite-backed state contract."""

        if not keys:
            return 0
        with self._connect() as connection:
            return int(
                connection.executemany(
                    "DELETE FROM config_state WHERE key = ?",
                    ((key,) for key in keys),
                ).rowcount
            )

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
                        WHEN 'identity' THEN 1
                        WHEN 'soul' THEN 2
                        WHEN 'user' THEN 3
                        WHEN 'tools_doc' THEN 4
                        WHEN 'bootstrap' THEN 5
                        WHEN 'tools' THEN 6
                        WHEN 'subagent' THEN 7
                        WHEN 'skill' THEN 8
                        WHEN 'knowledge' THEN 9
                        WHEN 'session' THEN 10
                        ELSE 10
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
                SELECT
                    repo_key,
                    repo_path,
                    repo_url,
                    execution_target,
                    vm_name,
                    active_flag,
                    managed_by_duckln,
                    status,
                    summary,
                    last_verified_at,
                    created_at,
                    updated_at,
                    metadata_json
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
            execution_target=str(row["execution_target"] or "local"),
            vm_name=str(row["vm_name"]) if row["vm_name"] is not None else None,
            active_flag=bool(int(row["active_flag"] or 0)),
            managed_by_duckln=bool(int(row["managed_by_duckln"] or 0)),
            status=str(row["status"]),
            summary=str(row["summary"]),
            last_verified_at=str(row["last_verified_at"]) if row["last_verified_at"] is not None else None,
            created_at=str(row["created_at"] or row["updated_at"]),
            updated_at=str(row["updated_at"]),
            metadata=json.loads(str(row["metadata_json"]) or "{}"),
        )

    def list_repo_states(self) -> tuple[RepoStateRecord, ...]:
        """Return tracked repo states ordered by recency."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    repo_key,
                    repo_path,
                    repo_url,
                    execution_target,
                    vm_name,
                    active_flag,
                    managed_by_duckln,
                    status,
                    summary,
                    last_verified_at,
                    created_at,
                    updated_at,
                    metadata_json
                FROM repo_state
                ORDER BY updated_at DESC, rowid DESC
                """
            ).fetchall()
        return tuple(
            RepoStateRecord(
                repo_key=str(row["repo_key"]),
                repo_path=str(row["repo_path"]) if row["repo_path"] is not None else None,
                repo_url=str(row["repo_url"]) if row["repo_url"] is not None else None,
                execution_target=str(row["execution_target"] or "local"),
                vm_name=str(row["vm_name"]) if row["vm_name"] is not None else None,
                active_flag=bool(int(row["active_flag"] or 0)),
                managed_by_duckln=bool(int(row["managed_by_duckln"] or 0)),
                status=str(row["status"]),
                summary=str(row["summary"]),
                last_verified_at=str(row["last_verified_at"]) if row["last_verified_at"] is not None else None,
                created_at=str(row["created_at"] or row["updated_at"]),
                updated_at=str(row["updated_at"]),
                metadata=json.loads(str(row["metadata_json"]) or "{}"),
            )
            for row in rows
        )

    def get_repo_knowledge(self, repo_key: str) -> RepoKnowledgeRecord | None:
        """Load concise repo knowledge for a repo key."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    repo_key,
                    repo_name,
                    repo_url,
                    source,
                    summary,
                    setup_complexity,
                    local_vm_recommendation,
                    cpu_profile,
                    ram_profile,
                    gpu_profile,
                    apple_silicon_notes,
                    required_tools_json,
                    setup_files_json,
                    entrypoints_json,
                    validation_status,
                    freshness_checked_at,
                    updated_at,
                    metadata_json
                FROM repo_knowledge
                WHERE repo_key = ?
                """,
                (repo_key,),
            ).fetchone()
        return None if row is None else _row_to_repo_knowledge(row)

    def list_repo_knowledge_records(self) -> tuple[RepoKnowledgeRecord, ...]:
        """List repo knowledge rows ordered by recency."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    repo_key,
                    repo_name,
                    repo_url,
                    source,
                    summary,
                    setup_complexity,
                    local_vm_recommendation,
                    cpu_profile,
                    ram_profile,
                    gpu_profile,
                    apple_silicon_notes,
                    required_tools_json,
                    setup_files_json,
                    entrypoints_json,
                    validation_status,
                    freshness_checked_at,
                    updated_at,
                    metadata_json
                FROM repo_knowledge
                ORDER BY updated_at DESC, repo_name ASC
                """
            ).fetchall()
        return tuple(_row_to_repo_knowledge(row) for row in rows)

    def upsert_repo_knowledge(
        self,
        *,
        repo_key: str,
        repo_name: str,
        source: str,
        summary: str,
        repo_url: str | None = None,
        setup_complexity: str | None = None,
        local_vm_recommendation: str | None = None,
        cpu_profile: str | None = None,
        ram_profile: str | None = None,
        gpu_profile: str | None = None,
        apple_silicon_notes: str | None = None,
        required_tools: tuple[str, ...] = (),
        setup_files: tuple[str, ...] = (),
        entrypoints: tuple[str, ...] = (),
        validation_status: str = "observed",
        freshness_checked_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist concise normalized repo knowledge."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO repo_knowledge (
                    repo_key,
                    repo_name,
                    repo_url,
                    source,
                    summary,
                    setup_complexity,
                    local_vm_recommendation,
                    cpu_profile,
                    ram_profile,
                    gpu_profile,
                    apple_silicon_notes,
                    required_tools_json,
                    setup_files_json,
                    entrypoints_json,
                    validation_status,
                    freshness_checked_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_key) DO UPDATE SET
                    repo_name=excluded.repo_name,
                    repo_url=excluded.repo_url,
                    source=excluded.source,
                    summary=excluded.summary,
                    setup_complexity=excluded.setup_complexity,
                    local_vm_recommendation=excluded.local_vm_recommendation,
                    cpu_profile=excluded.cpu_profile,
                    ram_profile=excluded.ram_profile,
                    gpu_profile=excluded.gpu_profile,
                    apple_silicon_notes=excluded.apple_silicon_notes,
                    required_tools_json=excluded.required_tools_json,
                    setup_files_json=excluded.setup_files_json,
                    entrypoints_json=excluded.entrypoints_json,
                    validation_status=excluded.validation_status,
                    freshness_checked_at=excluded.freshness_checked_at,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    repo_key,
                    " ".join(repo_name.split()),
                    repo_url,
                    source,
                    _normalize_summary(summary, field_name="repo knowledge summary"),
                    setup_complexity,
                    local_vm_recommendation,
                    cpu_profile,
                    ram_profile,
                    gpu_profile,
                    apple_silicon_notes,
                    json.dumps(sorted({tool.strip() for tool in required_tools if tool.strip()})),
                    json.dumps(sorted({file_name.strip() for file_name in setup_files if file_name.strip()})),
                    json.dumps(sorted({entry.strip() for entry in entrypoints if entry.strip()})),
                    validation_status,
                    freshness_checked_at or timestamp,
                    timestamp,
                    _dump_metadata(metadata),
                ),
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
        execution_target: str = "local",
        vm_name: str | None = None,
        active_flag: bool = False,
        managed_by_duckln: bool = True,
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
                    execution_target,
                    vm_name,
                    active_flag,
                    managed_by_duckln,
                    status,
                    summary,
                    last_verified_at,
                    created_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM repo_state WHERE repo_key = ?), ?), ?, ?)
                ON CONFLICT(repo_key) DO UPDATE SET
                    repo_path=excluded.repo_path,
                    repo_url=excluded.repo_url,
                    branch=excluded.branch,
                    execution_target=excluded.execution_target,
                    vm_name=excluded.vm_name,
                    active_flag=excluded.active_flag,
                    managed_by_duckln=excluded.managed_by_duckln,
                    status=excluded.status,
                    summary=excluded.summary,
                    last_verified_at=excluded.last_verified_at,
                    created_at=CASE WHEN repo_state.created_at = '' THEN excluded.created_at ELSE repo_state.created_at END,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    repo_key,
                    repo_path,
                    repo_url,
                    branch,
                    execution_target,
                    vm_name,
                    1 if active_flag else 0,
                    1 if managed_by_duckln else 0,
                    status,
                    _normalize_summary(summary, field_name="repo summary"),
                    last_verified_at,
                    repo_key,
                    timestamp,
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
                    created_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM vm_linkage WHERE vm_name = ?), ?), ?, ?)
                ON CONFLICT(vm_name) DO UPDATE SET
                    provider=excluded.provider,
                    mode=excluded.mode,
                    status=excluded.status,
                    local_repo_path=excluded.local_repo_path,
                    vm_repo_path=excluded.vm_repo_path,
                    summary=excluded.summary,
                    created_at=CASE WHEN vm_linkage.created_at = '' THEN excluded.created_at ELSE vm_linkage.created_at END,
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
                    vm_name,
                    timestamp,
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def list_vm_linkages(self) -> tuple[VmLinkageRecord, ...]:
        """Return tracked VM linkage records ordered by recency."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    vm_name,
                    provider,
                    mode,
                    status,
                    local_repo_path,
                    vm_repo_path,
                    summary,
                    created_at,
                    updated_at,
                    metadata_json
                FROM vm_linkage
                ORDER BY updated_at DESC, vm_name ASC
                """
            ).fetchall()
        return tuple(
            VmLinkageRecord(
                vm_name=str(row["vm_name"]),
                provider=str(row["provider"]) if row["provider"] is not None else None,
                mode=str(row["mode"]) if row["mode"] is not None else None,
                status=str(row["status"]),
                local_repo_path=str(row["local_repo_path"]) if row["local_repo_path"] is not None else None,
                vm_repo_path=str(row["vm_repo_path"]) if row["vm_repo_path"] is not None else None,
                summary=str(row["summary"]),
                created_at=str(row["created_at"] or row["updated_at"]),
                updated_at=str(row["updated_at"]),
                metadata=json.loads(str(row["metadata_json"]) or "{}"),
            )
            for row in rows
        )

    def upsert_usage_snapshot(
        self,
        *,
        scope_key: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        provider: str | None = None,
        model: str | None = None,
        estimated_cost_usd: float | None = None,
        turn_count_increment: int = 1,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Accumulate concise LLM usage totals for the active Duckln scope."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO usage_snapshots (
                    scope_key,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    turn_count,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    provider=COALESCE(excluded.provider, usage_snapshots.provider),
                    model=COALESCE(excluded.model, usage_snapshots.model),
                    prompt_tokens=usage_snapshots.prompt_tokens + excluded.prompt_tokens,
                    completion_tokens=usage_snapshots.completion_tokens + excluded.completion_tokens,
                    total_tokens=usage_snapshots.total_tokens + excluded.total_tokens,
                    estimated_cost_usd=CASE
                        WHEN usage_snapshots.estimated_cost_usd IS NULL AND excluded.estimated_cost_usd IS NULL THEN NULL
                        WHEN usage_snapshots.estimated_cost_usd IS NULL THEN excluded.estimated_cost_usd
                        WHEN excluded.estimated_cost_usd IS NULL THEN usage_snapshots.estimated_cost_usd
                        ELSE usage_snapshots.estimated_cost_usd + excluded.estimated_cost_usd
                    END,
                    turn_count=usage_snapshots.turn_count + excluded.turn_count,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    scope_key,
                    provider,
                    model,
                    max(0, int(prompt_tokens)),
                    max(0, int(completion_tokens)),
                    max(0, int(total_tokens)),
                    estimated_cost_usd,
                    max(0, int(turn_count_increment)),
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def get_usage_snapshot(self, scope_key: str = "session.current") -> UsageSnapshotRecord | None:
        """Load an aggregated usage snapshot by scope key."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    scope_key,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    turn_count,
                    updated_at,
                    metadata_json
                FROM usage_snapshots
                WHERE scope_key = ?
                """,
                (scope_key,),
            ).fetchone()
        if row is None:
            return None
        return UsageSnapshotRecord(
            scope_key=str(row["scope_key"]),
            provider=str(row["provider"]) if row["provider"] is not None else None,
            model=str(row["model"]) if row["model"] is not None else None,
            prompt_tokens=int(row["prompt_tokens"] or 0),
            completion_tokens=int(row["completion_tokens"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            estimated_cost_usd=float(row["estimated_cost_usd"]) if row["estimated_cost_usd"] is not None else None,
            turn_count=int(row["turn_count"] or 0),
            updated_at=str(row["updated_at"]),
            metadata=json.loads(str(row["metadata_json"]) or "{}"),
        )

    def clear_usage_snapshot(self, scope_key: str = "session.current") -> int:
        """Delete one usage snapshot when a new session starts."""

        with self._connect() as connection:
            return int(connection.execute("DELETE FROM usage_snapshots WHERE scope_key = ?", (scope_key,)).rowcount)

    def upsert_managed_resource(
        self,
        *,
        resource_key: str,
        resource_kind: str,
        provider: str,
        display_name: str,
        status: str,
        execution_target: str = "local",
        region: str | None = None,
        shape: str | None = None,
        install_root: str | None = None,
        idle_timeout_minutes: int | None = None,
        last_activity_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a concise Duckln-managed resource row."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO managed_resources (
                    resource_key,
                    resource_kind,
                    provider,
                    display_name,
                    execution_target,
                    region,
                    shape,
                    install_root,
                    status,
                    idle_timeout_minutes,
                    last_activity_at,
                    created_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM managed_resources WHERE resource_key = ?), ?), ?, ?)
                ON CONFLICT(resource_key) DO UPDATE SET
                    resource_kind=excluded.resource_kind,
                    provider=excluded.provider,
                    display_name=excluded.display_name,
                    execution_target=excluded.execution_target,
                    region=excluded.region,
                    shape=excluded.shape,
                    install_root=excluded.install_root,
                    status=excluded.status,
                    idle_timeout_minutes=excluded.idle_timeout_minutes,
                    last_activity_at=excluded.last_activity_at,
                    created_at=CASE WHEN managed_resources.created_at = '' THEN excluded.created_at ELSE managed_resources.created_at END,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    resource_key,
                    resource_kind,
                    provider,
                    display_name,
                    execution_target,
                    region,
                    shape,
                    install_root,
                    status,
                    idle_timeout_minutes,
                    last_activity_at,
                    resource_key,
                    timestamp,
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def list_managed_resources(self, *, provider: str | None = None) -> tuple[ManagedResourceRecord, ...]:
        """List managed resources ordered by recency."""

        query = """
            SELECT
                resource_key,
                resource_kind,
                provider,
                display_name,
                execution_target,
                region,
                shape,
                install_root,
                status,
                idle_timeout_minutes,
                last_activity_at,
                created_at,
                updated_at,
                metadata_json
            FROM managed_resources
        """
        params: tuple[str, ...] = ()
        if provider is not None:
            query += " WHERE provider = ?"
            params = (provider,)
        query += " ORDER BY updated_at DESC, resource_key ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(
            ManagedResourceRecord(
                resource_key=str(row["resource_key"]),
                resource_kind=str(row["resource_kind"]),
                provider=str(row["provider"]),
                display_name=str(row["display_name"]),
                execution_target=str(row["execution_target"] or "local"),
                region=str(row["region"]) if row["region"] is not None else None,
                shape=str(row["shape"]) if row["shape"] is not None else None,
                install_root=str(row["install_root"]) if row["install_root"] is not None else None,
                status=str(row["status"]),
                idle_timeout_minutes=int(row["idle_timeout_minutes"]) if row["idle_timeout_minutes"] is not None else None,
                last_activity_at=str(row["last_activity_at"]) if row["last_activity_at"] is not None else None,
                created_at=str(row["created_at"] or row["updated_at"]),
                updated_at=str(row["updated_at"]),
                metadata=json.loads(str(row["metadata_json"]) or "{}"),
            )
            for row in rows
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

    def next_loop_id(self) -> str:
        """Return the next stable human-readable loop id."""

        with self._connect() as connection:
            rows = connection.execute("SELECT id FROM loops WHERE id LIKE 'loop_%'").fetchall()
        max_seen = 0
        for row in rows:
            match = re.match(r"^loop_(\d+)$", str(row["id"]))
            if match:
                max_seen = max(max_seen, int(match.group(1)))
        return f"loop_{max_seen + 1:03d}"

    def upsert_loop(
        self,
        *,
        loop_id: str,
        name: str,
        loop_type: str,
        schedule: str,
        task_description: str,
        tool_scope: tuple[str, ...],
        os_type: str,
        auto_fix: bool = True,
        max_fix_attempts: int = 3,
        notify_on: str = "failure",
        safety_class: str = "S1",
        active: bool = True,
        expires_at: str | None = None,
        expiry_days: int = 30,
    ) -> None:
        """Create or update one persistent background loop."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO loops (
                    id,
                    name,
                    type,
                    schedule,
                    task_description,
                    tool_scope,
                    auto_fix,
                    max_fix_attempts,
                    notify_on,
                    safety_class,
                    active,
                    created_at,
                    os_type,
                    expires_at,
                    expiry_days
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM loops WHERE id = ?), ?), ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    type=excluded.type,
                    schedule=excluded.schedule,
                    task_description=excluded.task_description,
                    tool_scope=excluded.tool_scope,
                    auto_fix=excluded.auto_fix,
                    max_fix_attempts=excluded.max_fix_attempts,
                    notify_on=excluded.notify_on,
                    safety_class=excluded.safety_class,
                    active=excluded.active,
                    os_type=excluded.os_type,
                    expires_at=excluded.expires_at,
                    expiry_days=excluded.expiry_days
                """,
                (
                    loop_id,
                    _normalize_summary(name, field_name="loop name"),
                    loop_type,
                    schedule,
                    _normalize_summary(task_description, field_name="loop task description"),
                    json.dumps(tuple(tool_scope)),
                    1 if auto_fix else 0,
                    max(0, int(max_fix_attempts)),
                    notify_on,
                    safety_class,
                    1 if active else 0,
                    loop_id,
                    timestamp,
                    os_type,
                    expires_at,
                    max(1, int(expiry_days)),
                ),
            )

    def get_loop(self, loop_id: str) -> LoopRecord | None:
        """Load one background loop by id."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id, name, type, schedule, task_description, tool_scope,
                    auto_fix, max_fix_attempts, notify_on, safety_class, active,
                    created_at, last_run, last_status, last_summary, os_type,
                    expires_at, expiry_days
                FROM loops
                WHERE id = ?
                """,
                (loop_id,),
            ).fetchone()
        return None if row is None else _row_to_loop(row)

    def list_loops(self, *, include_inactive: bool = True) -> tuple[LoopRecord, ...]:
        """List background loops, newest first."""

        query = """
            SELECT
                id, name, type, schedule, task_description, tool_scope,
                auto_fix, max_fix_attempts, notify_on, safety_class, active,
                created_at, last_run, last_status, last_summary, os_type,
                expires_at, expiry_days
            FROM loops
        """
        params: tuple[object, ...] = ()
        if not include_inactive:
            query += " WHERE active = 1"
        query += " ORDER BY created_at DESC, id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_row_to_loop(row) for row in rows)

    def set_loop_active(self, loop_id: str, active: bool) -> bool:
        """Pause or resume a background loop."""

        with self._connect() as connection:
            count = connection.execute(
                "UPDATE loops SET active = ? WHERE id = ?",
                (1 if active else 0, loop_id),
            ).rowcount
        return bool(count)

    def delete_loop(self, loop_id: str) -> bool:
        """Delete a loop and its execution results."""

        with self._connect() as connection:
            connection.execute("DELETE FROM loop_results WHERE loop_id = ?", (loop_id,))
            count = connection.execute("DELETE FROM loops WHERE id = ?", (loop_id,)).rowcount
        return bool(count)

    def record_loop_result(
        self,
        *,
        loop_id: str,
        status: str,
        summary: str,
        actions_taken: str | None = None,
        fix_attempts: int = 0,
        escalated: bool = False,
        raw_output: str | None = None,
    ) -> None:
        """Persist one bounded loop result and update the loop's latest status."""

        timestamp = _utc_now()
        clean_summary = _normalize_summary(summary, field_name="loop result summary")
        clean_raw = _sanitize_loop_raw_output(raw_output)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO loop_results (
                    loop_id,
                    run_at,
                    status,
                    summary,
                    actions_taken,
                    fix_attempts,
                    escalated,
                    raw_output
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    loop_id,
                    timestamp,
                    status,
                    clean_summary,
                    actions_taken,
                    max(0, int(fix_attempts)),
                    1 if escalated else 0,
                    clean_raw,
                ),
            )
            connection.execute(
                """
                UPDATE loops
                SET last_run = ?, last_status = ?, last_summary = ?
                WHERE id = ?
                """,
                (timestamp, status, clean_summary, loop_id),
            )

    def list_loop_results(self, loop_id: str, *, limit: int = 10) -> tuple[LoopResultRecord, ...]:
        """List recent results for one loop."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, loop_id, run_at, status, summary, actions_taken, fix_attempts, escalated, raw_output
                FROM loop_results
                WHERE loop_id = ?
                ORDER BY run_at DESC, id DESC
                LIMIT ?
                """,
                (loop_id, max(1, int(limit))),
            ).fetchall()
        return tuple(_row_to_loop_result(row) for row in rows)

    def delete_old_loop_results(self, *, days: int = 30) -> int:
        """Delete loop results older than the retention window."""

        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")
        with self._connect() as connection:
            return int(connection.execute("DELETE FROM loop_results WHERE run_at < ?", (cutoff,)).rowcount)

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
            usage_count = connection.execute("DELETE FROM usage_snapshots WHERE scope_key = 'session.current'").rowcount
            session_count = connection.execute(
                "DELETE FROM managed_memory WHERE memory_kind = ?",
                ("session",),
            ).rowcount
            config_count = connection.execute(
                "DELETE FROM config_state WHERE key LIKE 'conversation.followup.%'"
            ).rowcount
            learning_count = connection.execute(
                """
                DELETE FROM learning_records
                WHERE family IN ('conversation', 'recommendation')
                """
            ).rowcount
            heuristic_count = connection.execute(
                """
                DELETE FROM promoted_heuristics
                WHERE family IN ('conversation', 'recommendation')
                """
            ).rowcount
        return int(run_count) + int(usage_count) + int(session_count) + int(config_count) + int(learning_count) + int(heuristic_count)

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
            knowledge_count = connection.execute(
                "DELETE FROM repo_knowledge WHERE repo_key = ?",
                (repo_key,),
            ).rowcount
            learning_count = connection.execute(
                "DELETE FROM learning_records WHERE subject_key LIKE ?",
                (f"%{repo_key}%",),
            ).rowcount
            heuristic_count = connection.execute(
                "DELETE FROM promoted_heuristics WHERE subject_key LIKE ?",
                (f"%{repo_key}%",),
            ).rowcount
            resource_count = connection.execute(
                """
                DELETE FROM managed_resources
                WHERE json_extract(metadata_json, '$.\"duckln:repo-key\"') = ?
                   OR json_extract(metadata_json, '$.repo_key') = ?
                """,
                (repo_key, repo_key),
            ).rowcount
        return int(repo_count) + int(run_count) + int(memory_count) + int(knowledge_count) + int(learning_count) + int(heuristic_count) + int(resource_count)

    def clear_factory_state(self) -> int:
        """Delete all tracked SQLite state so Duckln can rebuild a clean baseline."""

        table_names = (
            "config_state",
            "run_history",
            "repo_state",
            "vm_linkage",
            "healthcheck_state",
            "managed_memory",
            "repo_knowledge",
            "learning_records",
            "promoted_heuristics",
            "recent_custom_repos",
            "usage_snapshots",
            "managed_resources",
            "loops",
            "loop_results",
        )
        deleted_rows = 0
        with self._connect() as connection:
            for table_name in table_names:
                deleted_rows += int(connection.execute(f"DELETE FROM {table_name}").rowcount)
        return deleted_rows

    def upsert_recent_custom_repo(
        self,
        *,
        repo_url: str,
        repo_name: str,
        stars: int,
        description: str,
        category: str,
        framework: str,
        last_updated: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a recently used public custom GitHub repo."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO recent_custom_repos (
                    repo_url,
                    repo_name,
                    stars,
                    description,
                    category,
                    framework,
                    last_updated,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_url) DO UPDATE SET
                    repo_name=excluded.repo_name,
                    stars=excluded.stars,
                    description=excluded.description,
                    category=excluded.category,
                    framework=excluded.framework,
                    last_updated=excluded.last_updated,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    repo_url.strip(),
                    " ".join(repo_name.split()),
                    int(stars),
                    _normalize_summary(description, field_name="recent custom repo description"),
                    category.strip() or "Unknown",
                    framework.strip() or "Unknown",
                    last_updated.strip() or timestamp[:10],
                    timestamp,
                    json.dumps(dict(metadata or {}), sort_keys=True),
                ),
            )

    def list_recent_custom_repos(self, *, limit: int = 8) -> tuple[RecentCustomRepoRecord, ...]:
        """Return recently used public GitHub repos, newest first."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    repo_url,
                    repo_name,
                    stars,
                    description,
                    category,
                    framework,
                    last_updated,
                    updated_at,
                    metadata_json
                FROM recent_custom_repos
                ORDER BY updated_at DESC, repo_name ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return tuple(_row_to_recent_custom_repo(row) for row in rows)

    def delete_recent_custom_repo(self, *, repo_url: str) -> bool:
        """Plan 150 F6: remove a custom GitHub repo from the SELECTION list (the `/repos`
        picker). Forgets ONLY the selection entry — it does NOT touch any installed files or
        workspace. Returns True if a row was removed. Idempotent (no-op if absent)."""
        key = (repo_url or "").strip()
        if not key:
            return False
        with self._connect() as connection:
            cur = connection.execute(
                "DELETE FROM recent_custom_repos WHERE repo_url = ?", (key,)
            )
            return (cur.rowcount or 0) > 0

    def record_deletion(self, *, kind: str, name: str, detail: str = "", retain_days: int = 7) -> None:
        """Plan 150: log a confirmed deletion (vm/container/cloud/custom-repo/installed-repo/
        fresh-install) for auditability, and PRUNE entries older than `retain_days` (default 7)
        so the log self-expires. Never stores secrets — just what was removed + when."""
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO deletion_audit (kind, name, detail, deleted_at) VALUES (?, ?, ?, ?)",
                (str(kind).strip() or "item", str(name).strip(), str(detail).strip(), timestamp),
            )
            # self-expiring: drop anything older than the retention window.
            connection.execute(
                "DELETE FROM deletion_audit WHERE deleted_at < datetime('now', ?)",
                (f"-{max(1, int(retain_days))} days",),
            )

    def list_recent_deletions(self, *, within_days: int = 7, limit: int = 50) -> tuple[dict, ...]:
        """Plan 150: deletions in the last `within_days` (default 7), newest first — so the
        user can see what Duckln removed recently."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, name, detail, deleted_at FROM deletion_audit "
                "WHERE deleted_at >= datetime('now', ?) ORDER BY deleted_at DESC LIMIT ?",
                (f"-{max(1, int(within_days))} days", max(1, int(limit))),
            ).fetchall()
        return tuple({"kind": r[0], "name": r[1], "detail": r[2], "deleted_at": r[3]} for r in rows)

    def upsert_learning_record(
        self,
        *,
        learning_key: str,
        family: str,
        subject_key: str,
        summary: str,
        signal: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> LearningRecord:
        """Persist one bounded learning observation and auto-promote repeated validated patterns."""

        timestamp = _utc_now()
        existing = self.get_learning_record(learning_key)
        evidence_count = 1 if existing is None else existing.evidence_count + 1
        success_count = 0 if existing is None else existing.success_count
        failure_count = 0 if existing is None else existing.failure_count
        correction_count = 0 if existing is None else existing.correction_count
        normalized_signal = signal.strip().lower()
        if normalized_signal in {"accepted", "verified_success", "success"}:
            success_count += 1
        elif normalized_signal in {"rejected", "verified_failure", "failure"}:
            failure_count += 1
        elif normalized_signal in {"corrected", "feedback"}:
            correction_count += 1
        state = "candidate"
        if success_count >= 1 and failure_count == 0:
            state = "validated"
        if failure_count > success_count and failure_count >= 2:
            state = "rejected"
        if success_count >= 2 or correction_count >= 2:
            state = "promoted"
        merged_metadata = dict(existing.metadata) if existing is not None else {}
        if metadata:
            merged_metadata.update(dict(metadata))
        merged_metadata["last_signal"] = normalized_signal
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO learning_records (
                    learning_key,
                    family,
                    subject_key,
                    state,
                    summary,
                    evidence_count,
                    success_count,
                    failure_count,
                    correction_count,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(learning_key) DO UPDATE SET
                    family=excluded.family,
                    subject_key=excluded.subject_key,
                    state=excluded.state,
                    summary=excluded.summary,
                    evidence_count=excluded.evidence_count,
                    success_count=excluded.success_count,
                    failure_count=excluded.failure_count,
                    correction_count=excluded.correction_count,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    learning_key,
                    family,
                    subject_key,
                    state,
                    _normalize_summary(summary, field_name="learning summary"),
                    evidence_count,
                    success_count,
                    failure_count,
                    correction_count,
                    timestamp,
                    _dump_metadata(merged_metadata),
                ),
            )
        record = self.get_learning_record(learning_key)
        if record is None:
            raise RuntimeError("Learning record was not persisted.")
        if record.state == "promoted":
            self.upsert_promoted_heuristic(
                heuristic_key=f"{family}:{subject_key}",
                family=family,
                subject_key=subject_key,
                summary=record.summary,
                confidence=min(1.0, 0.4 + (record.success_count * 0.2) + (record.correction_count * 0.1)),
                metadata=record.metadata,
            )
        return record

    def get_learning_record(self, learning_key: str) -> LearningRecord | None:
        """Load a learning record by key."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    learning_key,
                    family,
                    subject_key,
                    state,
                    summary,
                    evidence_count,
                    success_count,
                    failure_count,
                    correction_count,
                    updated_at,
                    metadata_json
                FROM learning_records
                WHERE learning_key = ?
                """,
                (learning_key,),
            ).fetchone()
        return None if row is None else _row_to_learning(row)

    def list_learning_records(self, *, family: str | None = None) -> tuple[LearningRecord, ...]:
        """List learning records, optionally filtered by family."""

        query = """
            SELECT
                learning_key,
                family,
                subject_key,
                state,
                summary,
                evidence_count,
                success_count,
                failure_count,
                correction_count,
                updated_at,
                metadata_json
            FROM learning_records
        """
        params: tuple[str, ...] = ()
        if family is not None:
            query += " WHERE family = ?"
            params = (family,)
        query += " ORDER BY updated_at DESC, learning_key ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_row_to_learning(row) for row in rows)

    def upsert_promoted_heuristic(
        self,
        *,
        heuristic_key: str,
        family: str,
        subject_key: str,
        summary: str,
        confidence: float,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a compact promoted heuristic derived from repeated validated learnings."""

        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO promoted_heuristics (
                    heuristic_key,
                    family,
                    subject_key,
                    summary,
                    confidence,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(heuristic_key) DO UPDATE SET
                    family=excluded.family,
                    subject_key=excluded.subject_key,
                    summary=excluded.summary,
                    confidence=excluded.confidence,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    heuristic_key,
                    family,
                    subject_key,
                    _normalize_summary(summary, field_name="heuristic summary"),
                    max(0.0, min(1.0, float(confidence))),
                    timestamp,
                    _dump_metadata(metadata),
                ),
            )

    def list_promoted_heuristics(self, *, family: str | None = None) -> tuple[PromotedHeuristicRecord, ...]:
        """List promoted heuristics for future routing and ranking."""

        query = """
            SELECT
                heuristic_key,
                family,
                subject_key,
                summary,
                confidence,
                updated_at,
                metadata_json
            FROM promoted_heuristics
        """
        params: tuple[str, ...] = ()
        if family is not None:
            query += " WHERE family = ?"
            params = (family,)
        query += " ORDER BY confidence DESC, updated_at DESC, heuristic_key ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_row_to_promoted_heuristic(row) for row in rows)

    def vacuum(self) -> None:
        """Run SQLite VACUUM after bounded cleanup work."""

        connection = sqlite3.connect(self.database_file)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

    def record_routing_decision(
        self,
        *,
        message_text: str,
        normalized_text: str,
        top_level_category: str,
        final_route_family: str,
        final_intent: str,
        resolved_subject: str | None = None,
        confidence: float = 0.0,
        margin: float = 0.0,
        workflow_state: Mapping[str, Any] | None = None,
        followup_state: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist one router decision for observability and regressions."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO routing_decisions (
                    created_at,
                    message_text,
                    normalized_text,
                    top_level_category,
                    final_route_family,
                    final_intent,
                    resolved_subject,
                    confidence,
                    margin,
                    workflow_state_json,
                    followup_state_json,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now(),
                    " ".join(str(message_text).split()),
                    " ".join(str(normalized_text).split()),
                    str(top_level_category or "ambiguous"),
                    str(final_route_family or "clarify"),
                    str(final_intent or "clarify"),
                    str(resolved_subject).strip() if resolved_subject is not None else None,
                    float(confidence),
                    float(margin),
                    _dump_metadata(workflow_state),
                    _dump_metadata(followup_state),
                    _dump_metadata(metadata),
                ),
            )

    def list_routing_decisions(self, *, limit: int = 50) -> tuple[RoutingDecisionRecord, ...]:
        """Load recent routing observations in reverse chronological order."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    decision_id,
                    created_at,
                    message_text,
                    normalized_text,
                    top_level_category,
                    final_route_family,
                    final_intent,
                    resolved_subject,
                    confidence,
                    margin,
                    workflow_state_json,
                    followup_state_json,
                    metadata_json
                FROM routing_decisions
                ORDER BY decision_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return tuple(_row_to_routing_decision(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_file)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_repo_state_columns(self, connection: sqlite3.Connection) -> None:
        """Backfill newer repo-state registry columns for existing databases."""

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(repo_state)").fetchall()
        }
        migrations = (
            ("execution_target", "ALTER TABLE repo_state ADD COLUMN execution_target TEXT NOT NULL DEFAULT 'local'"),
            ("vm_name", "ALTER TABLE repo_state ADD COLUMN vm_name TEXT"),
            ("active_flag", "ALTER TABLE repo_state ADD COLUMN active_flag INTEGER NOT NULL DEFAULT 0"),
            ("managed_by_duckln", "ALTER TABLE repo_state ADD COLUMN managed_by_duckln INTEGER NOT NULL DEFAULT 1"),
        )
        for column_name, statement in migrations:
            if column_name not in columns:
                connection.execute(statement)

    def _ensure_loop_columns(self, connection: sqlite3.Connection) -> None:
        """Backfill the Plan-161 loop expiry columns for existing databases."""

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(loops)").fetchall()
        }
        migrations = (
            ("expires_at", "ALTER TABLE loops ADD COLUMN expires_at TEXT"),
            ("expiry_days", "ALTER TABLE loops ADD COLUMN expiry_days INTEGER NOT NULL DEFAULT 30"),
        )
        for column_name, statement in migrations:
            if column_name not in columns:
                connection.execute(statement)

    def _ensure_created_at_columns(self, connection: sqlite3.Connection) -> None:
        """Backfill stable creation timestamps for state rows created by older Duckln versions."""

        for table_name in ("repo_state", "vm_linkage", "managed_resources"):
            columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if "created_at" not in columns:
                connection.execute(f"ALTER TABLE {table_name} ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
            connection.execute(
                f"UPDATE {table_name} SET created_at = updated_at WHERE created_at = ''"
            )


@runtime_checkable
class MemoryStore(Protocol):
    """Plan 95: the pluggable backend contract for Duckln's persistent state/memory.

    `SQLiteStateStore` is the default implementation. A deployment can register an
    alternative backend (file-based, remote, Postgres) with `set_state_store_factory`
    as long as it satisfies this protocol — the rest of Duckln talks to it through
    `state/access.py` and never depends on SQLite directly."""

    def initialize(self) -> None: ...
    def upsert_config_values(self, values: dict[str, str]) -> None: ...
    def read_config_values(self, *, prefix: str = "") -> dict[str, str]: ...


# A deployment can override how the backend is constructed (e.g. to point at a
# remote store). Defaults to the bundled SQLite store.
_STATE_STORE_FACTORY: Callable[[Path], "MemoryStore"] | None = None


class FileMemoryStore:
    """Plan 101: a portable, dependency-free file backend satisfying ``MemoryStore``.

    Stores config values as a single JSON file under the config dir — useful offline,
    for tests, or where SQLite isn't desired. Demonstrates that the backend is truly
    pluggable. (Only the config-values surface is implemented; richer SQLite-only
    features remain SQLite's.)"""

    def __init__(self, config_dir: Path) -> None:
        self._path = Path(config_dir) / "state" / "memory_store.json"

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def upsert_config_values(self, values: Mapping[str, str]) -> None:
        data = self._load()
        data.update({str(k): str(v) for k, v in values.items()})
        self._path.write_text(json.dumps(data), encoding="utf-8")

    def read_config_values(self, *, prefix: str | None = None) -> dict[str, str]:
        data = self._load()
        if prefix:
            return {k: v for k, v in data.items() if k.startswith(prefix)}
        return dict(data)


def set_state_store_factory(factory: Callable[[Path], "MemoryStore"] | None) -> None:
    """Plan 95: register (or clear with None) the backend factory. The factory takes
    the config dir and returns an initialized object satisfying `MemoryStore`."""
    global _STATE_STORE_FACTORY
    _STATE_STORE_FACTORY = factory


def initialize_state_store(config_dir: Path):
    """Initialize the configured state-store backend for the Duckln config dir.

    Plan 95: returns the registered `MemoryStore` backend when one is set, otherwise
    the default SQLite store. Annotated loosely so a custom backend type is accepted."""

    if _STATE_STORE_FACTORY is not None:
        store = _STATE_STORE_FACTORY(config_dir)
        store.initialize()
        return store
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


def _load_metadata(raw: object) -> dict[str, Any]:
    text = str(raw or "{}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_loop_raw_output(raw_output: str | None) -> str | None:
    if raw_output is None:
        return None
    cleaned = str(raw_output)
    cleaned = re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=<redacted>", cleaned)
    cleaned = re.sub(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b", "<redacted-mac>", cleaned)
    cleaned = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<redacted-ip>", cleaned)
    return cleaned[:2000]


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _row_to_loop(row: sqlite3.Row) -> LoopRecord:
    try:
        tool_scope = tuple(str(item) for item in json.loads(str(row["tool_scope"] or "[]")) if str(item).strip())
    except json.JSONDecodeError:
        tool_scope = ()
    return LoopRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        type=str(row["type"]),
        schedule=str(row["schedule"]),
        task_description=str(row["task_description"]),
        tool_scope=tool_scope,
        auto_fix=bool(int(row["auto_fix"] or 0)),
        max_fix_attempts=int(row["max_fix_attempts"] or 0),
        notify_on=str(row["notify_on"]),
        safety_class=str(row["safety_class"]),
        active=bool(int(row["active"] or 0)),
        created_at=str(row["created_at"]),
        last_run=str(row["last_run"]) if row["last_run"] is not None else None,
        last_status=str(row["last_status"]) if row["last_status"] is not None else None,
        last_summary=str(row["last_summary"]) if row["last_summary"] is not None else None,
        os_type=str(row["os_type"]),
        expires_at=(
            str(row["expires_at"])
            if "expires_at" in row.keys() and row["expires_at"] is not None
            else None
        ),
        expiry_days=(
            int(row["expiry_days"])
            if "expiry_days" in row.keys() and row["expiry_days"] is not None
            else 30
        ),
    )


def _row_to_loop_result(row: sqlite3.Row) -> LoopResultRecord:
    return LoopResultRecord(
        id=int(row["id"]),
        loop_id=str(row["loop_id"]),
        run_at=str(row["run_at"]),
        status=str(row["status"]),
        summary=str(row["summary"]),
        actions_taken=str(row["actions_taken"]) if row["actions_taken"] is not None else None,
        fix_attempts=int(row["fix_attempts"] or 0),
        escalated=bool(int(row["escalated"] or 0)),
        raw_output=str(row["raw_output"]) if row["raw_output"] is not None else None,
    )


def _row_to_repo_knowledge(row: sqlite3.Row) -> RepoKnowledgeRecord:
    return RepoKnowledgeRecord(
        repo_key=str(row["repo_key"]),
        repo_name=str(row["repo_name"]),
        repo_url=str(row["repo_url"]) if row["repo_url"] is not None else None,
        source=str(row["source"]),
        summary=str(row["summary"]),
        setup_complexity=str(row["setup_complexity"]) if row["setup_complexity"] is not None else None,
        local_vm_recommendation=str(row["local_vm_recommendation"]) if row["local_vm_recommendation"] is not None else None,
        cpu_profile=str(row["cpu_profile"]) if row["cpu_profile"] is not None else None,
        ram_profile=str(row["ram_profile"]) if row["ram_profile"] is not None else None,
        gpu_profile=str(row["gpu_profile"]) if row["gpu_profile"] is not None else None,
        apple_silicon_notes=str(row["apple_silicon_notes"]) if row["apple_silicon_notes"] is not None else None,
        required_tools=tuple(json.loads(str(row["required_tools_json"]) or "[]")),
        setup_files=tuple(json.loads(str(row["setup_files_json"]) or "[]")),
        entrypoints=tuple(json.loads(str(row["entrypoints_json"]) or "[]")),
        validation_status=str(row["validation_status"]),
        freshness_checked_at=str(row["freshness_checked_at"]),
        updated_at=str(row["updated_at"]),
        metadata=json.loads(str(row["metadata_json"]) or "{}"),
    )


def _row_to_routing_decision(row: sqlite3.Row) -> RoutingDecisionRecord:
    return RoutingDecisionRecord(
        decision_id=int(row["decision_id"]),
        created_at=str(row["created_at"]),
        message_text=str(row["message_text"]),
        normalized_text=str(row["normalized_text"]),
        top_level_category=str(row["top_level_category"]),
        final_route_family=str(row["final_route_family"]),
        final_intent=str(row["final_intent"]),
        resolved_subject=str(row["resolved_subject"]) if row["resolved_subject"] is not None else None,
        confidence=float(row["confidence"] or 0.0),
        margin=float(row["margin"] or 0.0),
        workflow_state=_load_metadata(row["workflow_state_json"]),
        followup_state=_load_metadata(row["followup_state_json"]),
        metadata=_load_metadata(row["metadata_json"]),
    )


def _row_to_learning(row: sqlite3.Row) -> LearningRecord:
    return LearningRecord(
        learning_key=str(row["learning_key"]),
        family=str(row["family"]),
        subject_key=str(row["subject_key"]),
        state=str(row["state"]),
        summary=str(row["summary"]),
        evidence_count=int(row["evidence_count"]),
        success_count=int(row["success_count"]),
        failure_count=int(row["failure_count"]),
        correction_count=int(row["correction_count"]),
        updated_at=str(row["updated_at"]),
        metadata=json.loads(str(row["metadata_json"]) or "{}"),
    )


def _row_to_promoted_heuristic(row: sqlite3.Row) -> PromotedHeuristicRecord:
    return PromotedHeuristicRecord(
        heuristic_key=str(row["heuristic_key"]),
        family=str(row["family"]),
        subject_key=str(row["subject_key"]),
        summary=str(row["summary"]),
        confidence=float(row["confidence"]),
        updated_at=str(row["updated_at"]),
        metadata=json.loads(str(row["metadata_json"]) or "{}"),
    )


def _row_to_recent_custom_repo(row: sqlite3.Row) -> RecentCustomRepoRecord:
    return RecentCustomRepoRecord(
        repo_url=str(row["repo_url"]),
        repo_name=str(row["repo_name"]),
        stars=int(row["stars"]),
        description=str(row["description"]),
        category=str(row["category"]),
        framework=str(row["framework"]),
        last_updated=str(row["last_updated"]),
        updated_at=str(row["updated_at"]),
        metadata=json.loads(str(row["metadata_json"]) or "{}"),
    )
