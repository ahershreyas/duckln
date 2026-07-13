from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.harness.bus import MessageBus
from duckln.modes import ControlMode
from duckln.repo_agent import _draft_todo, run_repo_task
from state.access import read_repo_skills_ranked, write_repo_skill


class _EngineerLLM:
    """Explorer stops immediately; Engineer reads a file then stops; synthesis = success."""
    def __init__(self):
        self.eng_turns = 0

    def __call__(self, *, system_prompt: str, user_message: str) -> str:
        if "OUTPUT FORMAT" in system_prompt:
            if "Repo Engineer" in system_prompt:  # the engineer
                self.eng_turns += 1
                if self.eng_turns == 1:
                    return '{"tool": "fs.read_file", "args": {"path": "a.py"}, "reason": "read"}'
                return '{"stop": true, "reason": "done"}'
            return '{"stop": true, "reason": "done"}'  # the explorer
        if "TODO" in system_prompt:
            return '["find code", "edit", "verify"]'
        if "engineering task" in system_prompt:
            return "Changed a.py; tests pass."
        return "auth.py handles it."


def _repo(tmp):
    d = Path(tmp) / "r"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    return d


class TestPlanFirstTodo(unittest.TestCase):
    def test_todo_fallback(self):
        todo = _draft_todo("do x", llm_client=None)
        self.assertGreaterEqual(len(todo), 3)

    def test_todo_from_llm(self):
        todo = _draft_todo("do x", llm_client=lambda **k: '["a", "b", "c"]')
        self.assertEqual(todo, ("a", "b", "c"))

    def test_todo_emitted_before_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            thoughts = []
            run_repo_task(task="set x to 2", config_dir=Path(tmp), repo_name="r", project_dir=repo,
                          mode=ControlMode.HOOTLWO, emit_thought=thoughts.append, llm_client=_EngineerLLM())
            todo_lines = [t for t in thoughts if "TODO" in t]
            self.assertGreaterEqual(len(todo_lines), 3)


class TestLiveA2A(unittest.TestCase):
    def test_explorer_and_engineer_publish_to_bus(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            bus = MessageBus()
            run_repo_task(task="set x to 2", config_dir=Path(tmp), repo_name="r", project_dir=repo,
                          mode=ControlMode.HOOTLWO, llm_client=_EngineerLLM(), bus=bus, delegate=True)
            senders = {m.sender for m in bus.history("findings")} | {m.sender for m in bus.history("report")}
            self.assertIn("explorer", senders)
            self.assertIn("engineer", senders)


class TestSkillDistillation(unittest.TestCase):
    def test_distilled_on_success_and_recalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            out = run_repo_task(task="set x to 2 in a.py", config_dir=Path(tmp), repo_name="r", project_dir=repo,
                                mode=ControlMode.HOOTLWO, llm_client=_EngineerLLM())
            self.assertTrue(out.succeeded)
            recalled = read_repo_skills_ranked(Path(tmp), "r", query="set x in a.py", top_k=1)
            self.assertTrue(recalled)

    def test_skill_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_repo_skill(Path(tmp), repo_slug="r", task="add auth login endpoint", lesson="edited auth.py")
            write_repo_skill(Path(tmp), repo_slug="r", task="update database schema", lesson="edited db.py")
            top = read_repo_skills_ranked(Path(tmp), "r", query="fix the authentication login", top_k=1)
            self.assertEqual(top, ("edited auth.py",))


if __name__ == "__main__":
    unittest.main()
