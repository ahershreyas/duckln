"""Tests for the filesystem-facing agent memory skeleton."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent.memory import (
    AGENTS_FILE_NAME,
    KNOWLEDGE_DIR_NAME,
    SESSIONS_DIR_NAME,
    SKILLS_DIR_NAME,
    initialize_agent_memory,
    materialize_memory_file,
    materialize_memory_view,
    read_session_summary,
    resolve_agent_memory_paths,
    write_knowledge_note,
    write_session_summary,
)


class AgentMemoryTest(unittest.TestCase):
    def test_initialize_agent_memory_creates_expected_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = initialize_agent_memory(Path(temp_dir))

            self.assertTrue(paths.memory_root.is_dir())
            self.assertTrue(paths.skills_dir.is_dir())
            self.assertTrue(paths.knowledge_dir.is_dir())
            self.assertTrue(paths.sessions_dir.is_dir())
            self.assertFalse(paths.agents_file.exists())

    def test_resolve_agent_memory_paths_uses_memory_root(self) -> None:
        paths = resolve_agent_memory_paths(Path("/tmp/duckln"))

        self.assertEqual("/tmp/duckln/memory", str(paths.memory_root))
        self.assertEqual(f"/tmp/duckln/memory/{AGENTS_FILE_NAME}", str(paths.agents_file))
        self.assertEqual(f"/tmp/duckln/memory/{SKILLS_DIR_NAME}", str(paths.skills_dir))
        self.assertEqual(f"/tmp/duckln/memory/{KNOWLEDGE_DIR_NAME}", str(paths.knowledge_dir))
        self.assertEqual(f"/tmp/duckln/memory/{SESSIONS_DIR_NAME}", str(paths.sessions_dir))

    def test_session_and_knowledge_writes_stay_high_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = initialize_agent_memory(Path(temp_dir))

            session_file = write_session_summary(
                paths,
                session_id="repo bring-up / alpha",
                summary="Validated Python version and captured the shortest useful next step.",
            )
            knowledge_file = write_knowledge_note(
                paths,
                slug="venv-mismatch",
                title="Virtualenv mismatch",
                summary="Prefer the repo-local interpreter and verify pip resolves inside the same environment.",
            )

            self.assertTrue(session_file.exists())
            self.assertTrue(knowledge_file.exists())
            self.assertNotIn("\n\n\n", session_file.read_text(encoding="utf-8"))
            self.assertEqual(
                "Validated Python version and captured the shortest useful next step.",
                read_session_summary(paths, session_id="repo bring-up / alpha"),
            )

    def test_materialize_memory_view_supports_agents_skills_knowledge_and_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = initialize_agent_memory(Path(temp_dir))

            materialized = materialize_memory_view(
                paths,
                (
                    (AGENTS_FILE_NAME, "# Contract\n\nKeep memory concise."),
                    (f"{SKILLS_DIR_NAME}/venv.md", "# Venv\n\nPrefer repo-local Python."),
                    (f"{KNOWLEDGE_DIR_NAME}/gpu.md", "# GPU\n\nCheck CUDA before recommending it."),
                    (f"{SESSIONS_DIR_NAME}/alpha.md", "Saved the shortest useful session summary."),
                ),
            )

            self.assertEqual(4, len(materialized))
            self.assertEqual("# Contract", paths.agents_file.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue((paths.skills_dir / "venv.md").exists())
            self.assertTrue((paths.knowledge_dir / "gpu.md").exists())
            self.assertTrue((paths.sessions_dir / "alpha.md").exists())

    def test_materialize_memory_file_rejects_unsupported_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = initialize_agent_memory(Path(temp_dir))

            with self.assertRaises(ValueError):
                materialize_memory_file(paths, relative_path="../unsafe.txt", content="nope")


if __name__ == "__main__":
    unittest.main()
