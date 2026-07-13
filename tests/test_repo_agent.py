from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.modes import ControlMode
from duckln.repo_agent import (
    RepoAgentOutcome,
    load_repo_qa_definition,
    run_repo_agent,
)


class _ScriptedLLM:
    """A fake LLMClient: returns scripted JSON tool-calls for loop turns, and a
    fixed answer for the synthesis call (distinguished by the system prompt)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.loop_calls = 0
        self.synthesis_calls = 0
        self.last_evidence = ""

    def __call__(self, *, system_prompt: str, user_message: str) -> str:
        if "OUTPUT FORMAT" in system_prompt:  # the harness loop contract
            self.loop_calls += 1
            return self._turns.pop(0) if self._turns else '{"stop": true, "reason": "done"}'
        # otherwise: the synthesis call
        self.synthesis_calls += 1
        self.last_evidence = user_message
        return "The entry point is defined in `main.py`."


def _make_repo(tmp: str) -> Path:
    d = Path(tmp) / "demo"
    d.mkdir()
    (d / "main.py").write_text("def main():\n    print('hi')\n", encoding="utf-8")
    (d / "README.md").write_text("# Demo\n", encoding="utf-8")
    return d


class TestRepoQaDefinition(unittest.TestCase):
    def test_spec_loads_and_is_read_only(self):
        d = load_repo_qa_definition()
        self.assertEqual(d.name, "repo_qa")
        # No write/run/install tools in the Phase-1 read-only Q&A agent.
        for forbidden in ("fs.write", "fs.edit", "shell.run", "repo.run"):
            self.assertNotIn(forbidden, d.tools)
        self.assertIn("fs.read_file", d.tools)
        self.assertIn("shell.probe", d.tools)


class TestRunRepoAgent(unittest.TestCase):
    def test_reads_files_and_synthesizes_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            llm = _ScriptedLLM([
                '{"tool": "fs.list_dir", "args": {"path": "."}, "reason": "see layout"}',
                '{"tool": "fs.read_file", "args": {"path": "main.py"}, "reason": "read entry"}',
                '{"stop": true, "reason": "have enough"}',
            ])
            thoughts: list[str] = []
            outcome = run_repo_agent(
                question="Where is the entry point?",
                config_dir=Path(tmp),
                repo_name="demo",
                project_dir=repo,
                execution_target="local",
                mode=ControlMode.HOTL,
                emit_thought=thoughts.append,
                llm_client=llm,
            )
            self.assertIsInstance(outcome, RepoAgentOutcome)
            self.assertIn("main.py", outcome.answer)
            self.assertIn("fs.read_file", outcome.tools_used)
            self.assertTrue(outcome.succeeded)
            self.assertGreaterEqual(llm.synthesis_calls, 1)
            # the actual file content reached the synthesis evidence
            self.assertIn("def main", llm.last_evidence)
            # the thinking stream narrated the tool calls
            self.assertTrue(any("fs.read_file" in t for t in thoughts))

    def test_path_traversal_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            (Path(tmp) / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
            llm = _ScriptedLLM([
                '{"tool": "fs.read_file", "args": {"path": "../secret.txt"}, "reason": "escape"}',
                '{"stop": true, "reason": "done"}',
            ])
            outcome = run_repo_agent(
                question="read the secret",
                config_dir=Path(tmp),
                repo_name="demo",
                project_dir=repo,
                llm_client=llm,
            )
            # The escape attempt must not surface the out-of-repo content.
            self.assertNotIn("TOPSECRET", llm.last_evidence)
            self.assertNotIn("TOPSECRET", outcome.answer)

    def test_no_llm_configured_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            outcome = run_repo_agent(
                question="anything",
                config_dir=Path(tmp),
                repo_name="demo",
                project_dir=repo,
                llm_client=None,  # and no provider configured under this temp config_dir
            )
            self.assertFalse(outcome.succeeded)
            self.assertIn("provider", outcome.answer.lower())


if __name__ == "__main__":
    unittest.main()
