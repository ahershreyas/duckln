"""Tests for the SQLite-backed state store foundation."""

from __future__ import annotations

import tempfile
import unittest
import sqlite3

from duckln.ai_client import Provider
from duckln.config import AppConfig, resolve_config_paths, save_app_config
from duckln.modes import ControlMode
from state.store import RepoStateRecord, initialize_state_store, resolve_state_store_paths


class StateStoreTest(unittest.TestCase):
    def test_initialize_state_store_creates_expected_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)

            self.assertEqual(
                (
                    "config_state",
                    "healthcheck_state",
                    "managed_memory",
                    "repo_state",
                    "run_history",
                    "vm_linkage",
                ),
                store.list_table_names(),
            )

    def test_save_app_config_syncs_non_secret_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir})
            config = AppConfig(
                provider=Provider.OPENAI,
                model="gpt-4o-mini",
                api_key="super-secret-key",
                mode=ControlMode.HOTL,
            )

            save_app_config(config, paths)

            store = initialize_state_store(paths.config_dir)
            database_file = resolve_state_store_paths(paths.config_dir).database_file
            self.assertTrue(database_file.exists())

            with sqlite3.connect(database_file) as connection:
                rows = dict(connection.execute("SELECT key, value FROM config_state").fetchall())

            self.assertEqual(
                {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "mode": "hotl",
                },
                rows,
            )
            self.assertNotIn("api_key", rows)

    def test_read_config_values_can_filter_by_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)
            store.upsert_config_values(
                {
                    "provider": "openai",
                    "system.os": "Darwin",
                    "system.architecture": "arm64",
                }
            )

            self.assertEqual(
                {
                    "system.architecture": "arm64",
                    "system.os": "Darwin",
                },
                store.read_config_values(prefix="system."),
            )

    def test_summary_validation_rejects_log_sized_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)

            with self.assertRaises(ValueError):
                store.record_run(
                    run_id="run-1",
                    command_name="/healthcheck",
                    status="fail",
                    summary="x" * 601,
                )

    def test_clear_session_history_deletes_runs_and_session_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)
            store.record_run(
                run_id="run-1",
                command_name="/repos",
                status="saved",
                summary="Saved session state.",
            )
            store.upsert_managed_memory(
                memory_key="session:alpha",
                memory_kind="session",
                relative_path="sessions/alpha.md",
                content="short summary",
            )

            deleted_rows = store.clear_session_history()

            self.assertEqual(2, deleted_rows)
            with sqlite3.connect(store.database_file) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM run_history").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM managed_memory WHERE memory_kind = 'session'").fetchone()[0])

    def test_get_latest_repo_state_and_clear_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)
            store.upsert_repo_state(repo_key="https://example.com/alpha", status="ready", summary="Alpha ready.")
            store.upsert_repo_state(repo_key="https://example.com/beta", status="ready", summary="Beta ready.")
            store.record_run(
                run_id="run-beta",
                command_name="/repos",
                status="ready",
                summary="Beta run.",
                repo_key="https://example.com/beta",
            )

            latest = store.get_latest_repo_state()
            assert latest is not None

            self.assertIsInstance(latest, RepoStateRecord)
            self.assertEqual("https://example.com/beta", latest.repo_key)
            self.assertEqual(2, store.clear_project_state(repo_key=latest.repo_key))

            with sqlite3.connect(store.database_file) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM repo_state").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM run_history WHERE repo_key = 'https://example.com/beta'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
