"""Plan 187 — live-run polish + adopt the upgraded agent specs:
F1 silent Ollama auto-reconnect (source guard); F2 repo Q&A answers are clean English and never
leak internal tool names / fabricated citations; F3 a "which repo were we working on" question is
answered directly from state; F4 per-verdict `budget_profiles` (capable/weak) selected by the
observed capability verdict, plus a shipped-spec guard so a future .md edit can't break the registry.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


# --- F1: silent Ollama reconnect on success (source guard) ------------------

class F1SilentReconnect(unittest.TestCase):
    def test_reconnect_is_silent_on_success(self):
        src = Path("src/duckln/textual_ui.py").read_text(encoding="utf-8")
        # The ollama reconnect passes a no-op display (swallows "Starting/Ollama is up")…
        self.assertIn("display=lambda _message: None", src)
        # …and no longer prints a "Reconnected to Ollama" chat line on success.
        self.assertNotIn("Reconnected to Ollama", src)


# --- F2: answer discipline — clean English, no tool-name/line leak ----------

def _obs(tool, *, ok=True, path="", command="", query="", payload=None, error=""):
    args = {}
    if path:
        args["path"] = path
    if command:
        args["command"] = command
    if query:
        args["query"] = query
    return SimpleNamespace(tool=tool, args=args, ok=ok, payload=payload, error_message=error, error_code="")


class F2AnswerDiscipline(unittest.TestCase):
    def test_synth_prompt_forbids_tool_names_and_fabricated_lines(self):
        from duckln.repo_agent import _SYNTH_SYSTEM

        ans = _SYNTH_SYSTEM["answer"].lower()
        self.assertIn("clean, natural", ans)
        self.assertIn("never surface", ans)
        self.assertIn("state.read", ans)  # named as a forbidden internal, not a source
        self.assertIn("never invent", ans)

    def test_digest_labels_state_read_as_known_facts_not_tool(self):
        from duckln.repo_agent import _observation_digest, _observation_label

        self.assertEqual(_observation_label("state.read", ""), "Known facts (from Duckln's session memory):")
        self.assertEqual(_observation_label("fs.read_file", "src/app.py"), "File src/app.py:")
        digest = _observation_digest([
            _obs("state.read", payload={"repo_name": "JustHireMe", "repo_url": "https://x/JustHireMe"}),
            _obs("fs.read_file", path="src/app.py", payload={"text": "def main(): ..."}),
        ])
        self.assertIn("Known facts", digest)
        self.assertIn("File src/app.py:", digest)
        # The raw internal tool token must NOT appear as a bracketed source label.
        self.assertNotIn("[state.read", digest)


# --- F3: direct-state answer for "which repo were we working on" ------------

class F3PreviousRepo(unittest.TestCase):
    def _row(self):
        return SimpleNamespace(
            metadata={"repo_name": "JustHireMe"}, repo_path="/x/JustHireMe",
            repo_key="k", repo_url="https://github.com/vasu-devs/JustHireMe",
        )

    def test_answers_directly_from_state(self):
        from duckln import main

        seen: list[str] = []
        store = SimpleNamespace(get_latest_repo_state=lambda: self._row())
        with patch.object(main, "initialize_state_store", return_value=store):
            handled = main._maybe_answer_previous_repo(
                "which repo were we working on previously", Path("/tmp"), seen.append
            )
        self.assertTrue(handled)
        self.assertEqual(len(seen), 1)
        self.assertIn("JustHireMe", seen[0])
        self.assertIn("https://github.com/vasu-devs/JustHireMe", seen[0])
        self.assertNotIn("state.read", seen[0])

    def test_no_active_repo(self):
        from duckln import main

        seen: list[str] = []
        store = SimpleNamespace(get_latest_repo_state=lambda: None)
        with patch.object(main, "initialize_state_store", return_value=store):
            handled = main._maybe_answer_previous_repo("what repo were we working on", Path("/tmp"), seen.append)
        self.assertTrue(handled)
        self.assertIn("don't have a previous repo", seen[0])

    def test_does_not_fire_on_unrelated_or_future(self):
        from duckln import main

        for msg in ("how does auth work?", "which repo should I set up next", "recommend a repo to work on"):
            handled = main._maybe_answer_previous_repo(msg, Path("/tmp"), lambda _m: None)
            self.assertFalse(handled, msg)


# --- F4: budget_profiles parsing + verdict-driven resolver ------------------

class F4BudgetProfiles(unittest.TestCase):
    def test_repo_qa_profiles_parsed(self):
        from duckln.harness.agent_def import load_agent_definition_from_path

        d = load_agent_definition_from_path(Path("src/duckln/harness/agents/repo_qa.md"))
        self.assertIn("capable", d.budget_profiles)
        self.assertIn("weak", d.budget_profiles)
        self.assertEqual(int(d.budget_profiles["weak"]["max_turns"]), 6)

    def test_spec_without_profiles_is_empty(self):
        from duckln.harness.agent_def import load_agent_definition_from_path

        d = load_agent_definition_from_path(Path("src/duckln/harness/agents/critic.md"))
        self.assertEqual(d.budget_profiles, {})

    def test_malformed_profiles_ignored_no_raise(self):
        from duckln.harness.agent_def import load_agent_definition_from_text

        text = (
            "---\nname: t\nversion: \"2.0\"\nrole: r\ntools: []\n"
            "max_turns: 5\nbudget_seconds: 50.0\nbudget_llm_calls: 5\n"
            "budget_profiles:\n  weak: not-a-dict\n---\nbody\n"
        )
        d = load_agent_definition_from_text(text)
        self.assertEqual(d.budget_profiles, {})  # non-dict profile dropped
        self.assertEqual(d.max_turns, 5)         # flat defaults stand

    def test_resolver_selects_by_verdict(self):
        from duckln.harness.agent_def import load_agent_definition_from_path
        from duckln.recovery import resolve_agent_budget, write_cached_capability

        d = load_agent_definition_from_path(Path("src/duckln/harness/agents/repo_qa.md"))
        with tempfile.TemporaryDirectory() as td:
            write_cached_capability(td, "gemma2:9b", "weak")
            w = resolve_agent_budget(d, model_id="gemma2:9b", config_dir=td)
            self.assertEqual((w.max_turns, w.budget_seconds, w.budget_llm_calls, w.max_contract_retries),
                             (6, 90.0, 6, 3))
            write_cached_capability(td, "frontier", "capable")
            c = resolve_agent_budget(d, model_id="frontier", config_dir=td)
            self.assertEqual((c.max_turns, c.budget_seconds, c.budget_llm_calls, c.max_contract_retries),
                             (10, 120.0, 10, 2))
            u = resolve_agent_budget(d, model_id="unprobed", config_dir=td)
            self.assertEqual(u.max_turns, d.max_turns)  # unknown → default block
            # Safety floor identical across tiers.
            self.assertEqual(w.tools, c.tools)
            self.assertEqual(w.output_contract, d.output_contract)

    def test_resolver_noop_without_profiles(self):
        from duckln.harness.agent_def import load_agent_definition_from_path
        from duckln.recovery import resolve_agent_budget, write_cached_capability

        d = load_agent_definition_from_path(Path("src/duckln/harness/agents/critic.md"))
        with tempfile.TemporaryDirectory() as td:
            write_cached_capability(td, "gemma2:9b", "weak")
            out = resolve_agent_budget(d, model_id="gemma2:9b", config_dir=td)
        self.assertIs(out, d)  # no profiles → unchanged (same object)


# --- F4e: shipped-spec guard (no future .md edit can break the registry) ----

class F4eShippedSpecGuard(unittest.TestCase):
    def test_all_specs_parse_version_tools_and_profiles(self):
        from duckln.harness.agent_def import (
            load_agent_definition_from_path, SUPPORTED_SPEC_VERSION, _version_tuple,
        )
        from duckln.harness.tools import build_default_registry

        registry_names = set(build_default_registry(include_handlers=False).names())
        agents_dir = Path("src/duckln/harness/agents")
        for spec in sorted(agents_dir.glob("*.md")):
            if spec.name == "README.md":
                continue
            d = load_agent_definition_from_path(spec)  # parses or raises
            self.assertTrue(
                _version_tuple(d.version or "0") <= _version_tuple(SUPPORTED_SPEC_VERSION),
                f"{spec.name} version {d.version} > {SUPPORTED_SPEC_VERSION}",
            )
            for tool in d.tools:
                self.assertIn(tool, registry_names, f"{spec.name} declares unknown tool {tool}")
            for pname, prof in (d.budget_profiles or {}).items():
                self.assertIsInstance(prof, dict, f"{spec.name} profile {pname} not a dict")


if __name__ == "__main__":
    unittest.main()
