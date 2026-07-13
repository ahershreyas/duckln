"""Tests for the filesystem-facing agent memory skeleton."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent.memory import (
    AGENTS_FILE_NAME,
    BOOTSTRAP_FILE_NAME,
    IDENTITY_FILE_NAME,
    KNOWLEDGE_DIR_NAME,
    SESSIONS_DIR_NAME,
    SKILLS_DIR_NAME,
    SOUL_FILE_NAME,
    SUBAGENTS_DIR_NAME,
    TOOLS_DOC_FILE_NAME,
    TOOLS_FILE_NAME,
    USER_FILE_NAME,
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
            self.assertTrue(paths.subagents_dir.is_dir())
            self.assertFalse(paths.agents_file.exists())
            self.assertFalse(paths.soul_file.exists())
            self.assertFalse(paths.user_file.exists())
            self.assertFalse(paths.identity_file.exists())
            self.assertFalse(paths.tools_doc_file.exists())
            self.assertFalse(paths.bootstrap_file.exists())
            self.assertFalse(paths.tools_file.exists())

    def test_resolve_agent_memory_paths_uses_memory_root(self) -> None:
        paths = resolve_agent_memory_paths(Path("/tmp/duckln"))

        self.assertEqual("/tmp/duckln/memory", str(paths.memory_root))
        self.assertEqual(f"/tmp/duckln/memory/{AGENTS_FILE_NAME}", str(paths.agents_file))
        self.assertEqual(f"/tmp/duckln/memory/{SOUL_FILE_NAME}", str(paths.soul_file))
        self.assertEqual(f"/tmp/duckln/memory/{USER_FILE_NAME}", str(paths.user_file))
        self.assertEqual(f"/tmp/duckln/memory/{IDENTITY_FILE_NAME}", str(paths.identity_file))
        self.assertEqual(f"/tmp/duckln/memory/{TOOLS_DOC_FILE_NAME}", str(paths.tools_doc_file))
        self.assertEqual(f"/tmp/duckln/memory/{BOOTSTRAP_FILE_NAME}", str(paths.bootstrap_file))
        self.assertEqual(f"/tmp/duckln/memory/{TOOLS_FILE_NAME}", str(paths.tools_file))
        self.assertEqual(f"/tmp/duckln/memory/{SKILLS_DIR_NAME}", str(paths.skills_dir))
        self.assertEqual(f"/tmp/duckln/memory/{KNOWLEDGE_DIR_NAME}", str(paths.knowledge_dir))
        self.assertEqual(f"/tmp/duckln/memory/{SESSIONS_DIR_NAME}", str(paths.sessions_dir))
        self.assertEqual(f"/tmp/duckln/memory/{SUBAGENTS_DIR_NAME}", str(paths.subagents_dir))

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
                    (SOUL_FILE_NAME, "# Soul\n\nStay calm."),
                    (USER_FILE_NAME, "# User\n\nAlias: Shreyas"),
                    (IDENTITY_FILE_NAME, "# Identity\n\nDuckln is a terminal companion."),
                    (TOOLS_DOC_FILE_NAME, "# Tools\n\nUse the smallest useful tool."),
                    (BOOTSTRAP_FILE_NAME, "# Bootstrap\n\nAsk for an alias first."),
                    (TOOLS_FILE_NAME, '{\n  "tools": []\n}'),
                    (f"{SKILLS_DIR_NAME}/venv.md", "# Venv\n\nPrefer repo-local Python."),
                    (f"{KNOWLEDGE_DIR_NAME}/gpu.md", "# GPU\n\nCheck CUDA before recommending it."),
                    (f"{SESSIONS_DIR_NAME}/alpha.md", "Saved the shortest useful session summary."),
                    (f"{SUBAGENTS_DIR_NAME}/python_setup/AGENTS.md", "# Python setup specialist\n\n## Scope\nBounded."),
                ),
            )

            self.assertEqual(11, len(materialized))
            self.assertEqual("# Contract", paths.agents_file.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(paths.soul_file.exists())
            self.assertTrue(paths.user_file.exists())
            self.assertTrue(paths.identity_file.exists())
            self.assertTrue(paths.tools_doc_file.exists())
            self.assertTrue(paths.bootstrap_file.exists())
            self.assertTrue(paths.tools_file.exists())
            self.assertTrue((paths.skills_dir / "venv.md").exists())
            self.assertTrue((paths.knowledge_dir / "gpu.md").exists())
            self.assertTrue((paths.sessions_dir / "alpha.md").exists())
            self.assertTrue((paths.subagents_dir / "python_setup" / "AGENTS.md").exists())

    def test_materialize_memory_view_removes_stale_managed_files_but_keeps_other_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = initialize_agent_memory(Path(temp_dir))
            stale_session = paths.sessions_dir / "old.md"
            stale_session.write_text("old\n", encoding="utf-8")
            repo_cache = paths.knowledge_dir / "repos.json"
            repo_cache.write_text('{"repos":[]}\n', encoding="utf-8")

            materialize_memory_view(
                paths,
                (
                    (AGENTS_FILE_NAME, "# Contract\n\nKeep memory concise."),
                ),
            )

            self.assertFalse(stale_session.exists())
            self.assertTrue(repo_cache.exists())

    def test_materialize_memory_file_rejects_unsupported_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = initialize_agent_memory(Path(temp_dir))

            with self.assertRaises(ValueError):
                materialize_memory_file(paths, relative_path="../unsafe.txt", content="nope")


if __name__ == "__main__":
    unittest.main()
