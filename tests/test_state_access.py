"""Tests for high-signal state access helpers."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent.probe import GpuProbeState, SystemProbe
from state.access import (
    initialize_managed_memory_state,
    materialize_managed_memory_state,
    read_config_snapshot,
    read_session_summary_state,
    record_system_probe,
    write_knowledge_memory_state,
    write_config_snapshot,
    write_skill_memory_state,
    write_session_summary_state,
)


class StateAccessTest(unittest.TestCase):
    def test_config_snapshot_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            write_config_snapshot(
                temp_dir,
                {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "mode": "hotl",
                },
            )

            self.assertEqual(
                {
                    "mode": "hotl",
                    "model": "gpt-4o-mini",
                    "provider": "openai",
                },
                read_config_snapshot(temp_dir),
            )

    def test_record_system_probe_persists_summary_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            probe = SystemProbe(
                operating_system="Darwin",
                architecture="arm64",
                cpu_logical_cores=8,
                ram_bytes=16 * 1024**3,
                disk_free_bytes=200 * 1024**3,
                python_version="3.11.8",
                gpu=GpuProbeState(
                    backend="mps",
                    summary="Apple Silicon detected; MPS available.",
                    cuda_capable=False,
                    cuda_available=False,
                    mps_capable=True,
                    mps_available=True,
                ),
            )

            record_system_probe(temp_dir, probe)

            state_values = read_config_snapshot(temp_dir, prefix="system.")
            self.assertEqual("Darwin", state_values["system.os"])
            self.assertEqual("mps", state_values["system.gpu_backend"])

            database_file = temp_dir + "/state/duckln-state.sqlite3"
            with sqlite3.connect(database_file) as connection:
                row = connection.execute(
                    "SELECT command_name, summary FROM run_history WHERE run_id = ?",
                    ("system-probe",),
                ).fetchone()

            self.assertEqual(("system_probe", probe.summary()), row)

    def test_session_summary_round_trip_uses_filesystem_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            write_session_summary_state(
                temp_dir,
                session_id="alpha setup",
                summary="Validated the interpreter path and saved the next reusable fix pattern.",
            )

            self.assertEqual(
                "Validated the interpreter path and saved the next reusable fix pattern.",
                read_session_summary_state(temp_dir, session_id="alpha setup"),
            )

    def test_managed_memory_initialization_seeds_agents_and_materializes_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            contract_source = Path(temp_dir) / "AGENTS.md"
            contract_source.write_text("# Root Contract\n\nStay concise.\n", encoding="utf-8")

            initialize_managed_memory_state(temp_dir, contract_source=contract_source)

            self.assertEqual(
                "# Root Contract\n\nStay concise.",
                (Path(temp_dir) / "memory" / "AGENTS.md").read_text(encoding="utf-8").strip(),
            )

    def test_skill_and_knowledge_memory_round_trip_from_sqlite_to_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = write_skill_memory_state(
                temp_dir,
                slug="venv-fix",
                title="Virtualenv fix",
                summary="Use the repo-local interpreter before retrying package installs.",
            )
            knowledge_path = write_knowledge_memory_state(
                temp_dir,
                slug="cuda-check",
                title="CUDA check",
                summary="Confirm actual CUDA availability before recommending GPU-only setup steps.",
            )

            skill_path.unlink()
            knowledge_path.unlink()
            materialize_managed_memory_state(temp_dir)

            self.assertIn("Virtualenv fix", skill_path.read_text(encoding="utf-8"))
            self.assertIn("CUDA check", knowledge_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
