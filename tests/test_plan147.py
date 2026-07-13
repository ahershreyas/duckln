"""Plan 147 — the agent was BLIND (fs tools couldn't read a `~/…` repo on the VM), not dumb.
F1 the fs tools expand `~`→${HOME} (remote) / expanduser (local) so reads actually succeed.
F2 one consistent repo-name identifier in the thinking log (no path-here / URL-there split).
F3 an all-tool-failure is reported as a tool/path-ACCESS problem, not "make sure it's cloned".
F4 elicit a Claude-shaped 5-part reasoning chain; a weak-model note ONLY when the model is weak.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckln.recovery as rec
from duckln.harness import tools as T


class TildeExpansionRemote(unittest.TestCase):
    """F1: a `~`-rooted repo path must expand on the remote shell (it was quoted literally)."""

    def test_path_expr_expands_tilde(self):
        self.assertEqual(T._remote_path_expr("~/.duckln/projects/x/package.json"),
                         '"${HOME}"/.duckln/projects/x/package.json')
        self.assertEqual(T._remote_path_expr("~"), '"${HOME}"\'\'')
        # a normal absolute path is just shell-quoted (unchanged behaviour)
        self.assertEqual(T._remote_path_expr("/home/ubuntu/x"), "/home/ubuntu/x")

    def test_remote_commands_use_home_for_tilde_repo(self):
        p = "~/.duckln/projects/JustHireMe/package.json"
        d = "~/.duckln/projects/JustHireMe"
        self.assertIn("${HOME}", T._remote_read_command(p, 1024))
        self.assertIn("${HOME}", T._remote_list_command(d))
        self.assertIn("${HOME}", T._remote_search_command("PyInstaller", d, 50))
        self.assertIn("${HOME}", T._remote_glob_command("**/*.spec", d, 50))
        self.assertIn("${HOME}", T._remote_write_command(p, "x"))
        # the literal single-quoted tilde (the bug) must be gone
        self.assertNotIn("'~/.duckln", T._remote_read_command(p, 1024))


class TildeExpansionLocal(unittest.TestCase):
    """F1: a `~` local project_dir must expand (`.resolve()` alone does NOT expand it)."""

    def test_local_path_expands_home(self):
        resolved = T._resolve_safe_project_path("file.txt", Path("~/duckln-proj"))
        self.assertIsNotNone(resolved)
        home = os.path.expanduser("~")
        self.assertTrue(str(resolved).startswith(home))   # under the REAL home, not cwd/~
        self.assertNotIn("/~/", str(resolved))


class ConsistentLogIdentifier(unittest.TestCase):
    """F2: the thinking-log episode is keyed by the repo NAME, not the dir path."""

    def test_recovery_logs_under_repo_name(self):
        def fake_runner(**kw):
            return SimpleNamespace(answer='{"decision":"skip","cause":"absent","evidence":"listing shows it absent"}')

        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d)
            rec.recover_failed_step_with_agent(
                config_dir=cfg, repo_name="JustHireMe", project_dir=cfg / "some" / "deep" / "path",
                execution_target="vm", vm_name="box", failed_command="npm run build",
                stderr="boom", stdout="", mode=None, approve=None,
                llm_client=lambda **k: "", agent_runner=fake_runner,
            )
            log = (cfg / "memory" / "logical-thinking.md").read_text(encoding="utf-8")
        self.assertIn("· JustHireMe ·", log)               # keyed by the NAME
        self.assertNotIn("deep/path ·", log)               # not the raw dir path


class BlindnessIsDiagnosable(unittest.TestCase):
    """F3: all-tool-failure → a tool/path-ACCESS message, not 'make sure it's cloned'."""

    def _obs(self, ok, code=""):
        return SimpleNamespace(tool="fs.list_dir", args={"path": "."}, ok=ok,
                               error_code=code, error_message="x", payload={"entries": ["a"]})

    def test_all_failed_says_access_problem(self):
        from duckln.repo_agent import _synthesize_answer
        ans = _synthesize_answer(question="why?", observations=[self._obs(False, "not_found"), self._obs(False, "not_found")],
                                 llm_client=None, repo_name="JustHireMe")
        self.assertIn("couldn't ACCESS", ans)
        self.assertIn("not a missing repo", ans)
        self.assertNotIn("Make sure the repo is cloned locally", ans)

    def test_some_success_is_not_blindness(self):
        from duckln.repo_agent import _synthesize_answer
        ans = _synthesize_answer(question="why?", observations=[self._obs(True)], llm_client=None, repo_name="X")
        self.assertNotIn("couldn't ACCESS", ans)


class ClaudeShapedReasoning(unittest.TestCase):
    """F4: the prompt demands a 5-part chain + exemplar; the weak-model note is conditional."""

    def test_recovery_prompt_has_chain_and_exemplar(self):
        s = rec._recovery_spec_prompt("recovery_agent")
        for part in ("SYMPTOM", "WHAT I INSPECTED", "ROOT CAUSE", "CONFIDENCE"):
            self.assertIn(part, s)
        # the worked exemplar ("imitate the structure, not the content")
        self.assertIn("Example of the SHAPE", s)

    def test_weak_model_note_only_when_weak(self):
        self.assertEqual(rec.weak_model_reasoning_note("gemma2:2b"),
                         "Note — For depth reasoning, configure a stronger model.")
        # a capable model → NO note (no nag); 49B and frontier models are capable
        self.assertIsNone(rec.weak_model_reasoning_note("nvidia/llama-3.3-nemotron-super-49b"))
        self.assertIsNone(rec.weak_model_reasoning_note("claude-opus-4"))
        self.assertNotIn("Claude", rec._WEAK_MODEL_REASONING_NOTE)


if __name__ == "__main__":
    unittest.main()
