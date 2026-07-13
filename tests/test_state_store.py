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
                    "deletion_audit",
                    "healthcheck_state",
                    "learning_records",
                    "loop_results",
                    "loops",
                    "managed_memory",
                    "managed_resources",
                    "promoted_heuristics",
                    "recent_custom_repos",
                    "repo_knowledge",
                    "repo_state",
                    "routing_decisions",
                    "run_history",
                    "usage_snapshots",
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
                    "user_name": "there",
                    "safety_accepted_at": "1970-01-01T00:00:00+00:00",
                    "onboarding_complete": "true",
                    "failure_window_hours": "24.0",
                    "font_setup_acknowledged": "False",
                    "plan_mode_enabled": "false",
                    "plan_precheck": "ask",
                    "duckln_ui": "auto",
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
            store.upsert_config_values({"conversation.followup.pending_repo_name": "\"whisper\""})

            deleted_rows = store.clear_session_history()

            self.assertEqual(3, deleted_rows)
            with sqlite3.connect(store.database_file) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM run_history").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM managed_memory WHERE memory_kind = 'session'").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM config_state WHERE key LIKE 'conversation.followup.%'").fetchone()[0])

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

    def test_repo_state_round_trips_registry_fields_for_local_and_vm_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)
            store.upsert_repo_state(
                repo_key="https://example.com/whisper",
                repo_url="https://example.com/whisper",
                repo_path="/tmp/whisper",
                execution_target="vm",
                vm_name="duckln-vm-1",
                active_flag=True,
                managed_by_duckln=False,
                status="ready",
                summary="Whisper tracked in the VM.",
                last_verified_at="2026-04-11T10:00:00+00:00",
                metadata={"repo_name": "whisper"},
            )

            latest = store.get_latest_repo_state()
            assert latest is not None

            self.assertEqual("vm", latest.execution_target)
            self.assertEqual("duckln-vm-1", latest.vm_name)
            self.assertTrue(latest.active_flag)
            self.assertFalse(latest.managed_by_duckln)
            self.assertEqual("2026-04-11T10:00:00+00:00", latest.last_verified_at)

    def test_learning_record_promotion_creates_promoted_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)

            store.upsert_learning_record(
                learning_key="recommendation:https://example.com/whisper",
                family="recommendation",
                subject_key="https://example.com/whisper",
                summary="Whisper recommended for Apple Silicon because it verifies cleanly on lighter paths.",
                signal="success",
            )
            record = store.upsert_learning_record(
                learning_key="recommendation:https://example.com/whisper",
                family="recommendation",
                subject_key="https://example.com/whisper",
                summary="Whisper recommended for Apple Silicon because it verifies cleanly on lighter paths.",
                signal="success",
            )

            self.assertEqual("promoted", record.state)
            heuristics = store.list_promoted_heuristics(family="recommendation")
            self.assertEqual(1, len(heuristics))
            self.assertIn("Whisper recommended", heuristics[0].summary)

    def test_usage_snapshots_and_managed_resources_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = initialize_state_store(resolve_config_paths({"DUCKLN_CONFIG_DIR": temp_dir}).config_dir)

            store.upsert_usage_snapshot(
                scope_key="session.current",
                provider="openai",
                model="gpt-5.4-pro",
                prompt_tokens=120,
                completion_tokens=80,
                total_tokens=200,
                estimated_cost_usd=0.0042,
            )
            store.upsert_managed_resource(
                resource_key="docker:whisper",
                resource_kind="docker_container",
                provider="docker",
                display_name="whisper",
                execution_target="docker",
                install_root="/tmp/whisper",
                status="running",
                idle_timeout_minutes=30,
                metadata={"duckln:managed": "true"},
            )

            usage = store.get_usage_snapshot()
            assert usage is not None
            resources = store.list_managed_resources(provider="docker")

            self.assertEqual(200, usage.total_tokens)
            self.assertAlmostEqual(0.0042, usage.estimated_cost_usd or 0.0)
            self.assertEqual(1, len(resources))
            self.assertEqual("whisper", resources[0].display_name)
            self.assertEqual("docker", resources[0].execution_target)


if __name__ == "__main__":
    unittest.main()
