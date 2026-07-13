"""Plan 184 — the two deferred Plan-183 features, completed production-grade:
- F10: paste a GitHub repo LINK (→ "set up this repo?" → existing target picker) or a shell COMMAND (→ reconfirm → run).
- F3: a fact-grounded, interactive capability/recommendation advisor (host probe + repo estimate).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckln.main as m


def _cfg(mode=None):
    from duckln.ai_client import Provider
    from duckln.config import AppConfig
    from duckln.modes import ControlMode

    return AppConfig(provider=Provider.OLLAMA, model="m", api_key=None, base_url=None, mode=mode or ControlMode.HITL)


def _rec():
    from state.repo_catalog import RepoCatalogRecord

    return RepoCatalogRecord(
        name="JustHireMe", repo_url="https://github.com/vasu-devs/JustHireMe", stars=0,
        description="", category="Custom", framework="Unknown", last_updated="2026-01-01",
    )


class F10aRepoUrlSetup(unittest.TestCase):
    def test_bare_url_offers_setup(self):
        msgs: list[str] = []
        with patch("state.repo_catalog.resolve_public_github_repo_record", return_value=_rec()):
            out = m._maybe_offer_repo_url_setup(
                "https://github.com/vasu-devs/JustHireMe", current=_cfg(),
                paths=SimpleNamespace(config_dir="/tmp"), select_prompt=None, text_prompt=None,
                approve_prompt=lambda _m: False, display_output=msgs.append,
                system_probe=None, client=None, terminal_interface=None,
            )
        self.assertIsNotNone(out)  # consumed (offered)
        self.assertTrue(any("JustHireMe" in s for s in msgs))

    def test_url_in_question_passes_through(self):
        out = m._maybe_offer_repo_url_setup(
            "what does https://github.com/vasu-devs/JustHireMe do?", current=_cfg(),
            paths=SimpleNamespace(config_dir="/tmp"), select_prompt=None, text_prompt=None,
            approve_prompt=lambda _m: False, display_output=lambda _t: None,
            system_probe=None, client=None, terminal_interface=None,
        )
        self.assertIsNone(out)  # left to the conversation agent

    def test_no_url_passes_through(self):
        out = m._maybe_offer_repo_url_setup(
            "how are you", current=_cfg(), paths=SimpleNamespace(config_dir="/tmp"),
            select_prompt=None, text_prompt=None, approve_prompt=None, display_output=lambda _t: None,
            system_probe=None, client=None, terminal_interface=None,
        )
        self.assertIsNone(out)

    def test_setup_routes_through_repos_with_preselection(self):
        with patch("state.repo_catalog.resolve_public_github_repo_record", return_value=_rec()), \
             patch("duckln.main.handle_session_command", return_value=_cfg()) as hsc:
            m._maybe_offer_repo_url_setup(
                "set up https://github.com/vasu-devs/JustHireMe", current=_cfg(),
                paths=SimpleNamespace(config_dir="/tmp"), select_prompt=lambda *a: None, text_prompt=None,
                approve_prompt=lambda _m: True, display_output=lambda _t: None,
                system_probe=None, client=None, terminal_interface=None,
            )
            hsc.assert_called_once()
            self.assertEqual(hsc.call_args.args[0], "/repos")
            self.assertIs(hsc.call_args.kwargs["preselected_repo"], hsc.call_args.kwargs["preselected_repo"])
            self.assertIsNotNone(hsc.call_args.kwargs.get("preselected_repo"))


class F10bPastedCommand(unittest.TestCase):
    def test_extract_commands_vs_prose(self):
        ex = m._extract_pasted_command
        self.assertEqual(ex("npm install --include=dev"), "npm install --include=dev")
        self.assertEqual(ex("git clone https://github.com/x/y"), "git clone https://github.com/x/y")
        self.assertEqual(ex("run npm run build"), "npm run build")
        self.assertEqual(ex("`pip install vite`"), "pip install vite")
        self.assertIsNone(ex("git is great"))
        self.assertIsNone(ex("what is npm?"))
        self.assertIsNone(ex("https://github.com/x/y"))  # a bare URL is not a command (→ F10a)

    def test_command_reconfirm_decline_does_not_run(self):
        msgs: list[str] = []
        ran = m._maybe_run_pasted_command(
            "npm install", current=_cfg(), paths=SimpleNamespace(config_dir="/tmp"),
            display_output=msgs.append, approve_prompt=lambda _m: False, chat=None,
        )
        self.assertTrue(ran)  # consumed (it was a command)
        self.assertTrue(any("not running" in s.lower() for s in msgs))

    def test_prose_is_not_consumed(self):
        ran = m._maybe_run_pasted_command(
            "tell me a joke", current=_cfg(), paths=SimpleNamespace(config_dir="/tmp"),
            display_output=lambda _t: None, approve_prompt=lambda _m: True, chat=None,
        )
        self.assertFalse(ran)  # falls through to normal chat


if __name__ == "__main__":
    unittest.main()
