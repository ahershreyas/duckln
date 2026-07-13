"""Plan 148 — Duckln must SEARCH (web.search was broken), justify steps, not blame a capable model.
F1 web.search passes the required config_dir + formats results (it always crashed before).
F3 the honest-stop is model-aware (no "stronger model" for a capable model; internet nudge instead).
F4 a step with no cited evidence + low confidence is flagged speculative (conservative).
F5 the venv setup installs PyInstaller even when `pip install -e .` fails (non-chain-breaking).
F6 RAG: the recovery agent has web.fetch and is told to fetch the top authoritative doc.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import duckln.recovery as rec
import duckln.repo_bringup as rb
from duckln.harness import tools as T


class WebSearchFixed(unittest.TestCase):
    """F1: web.search passes config_dir and returns a readable, citable result block."""

    def _ctx(self, tmp):
        from duckln.modes import ControlMode
        return T.AgentContext(agent_name="repo_qa", mode=ControlMode.HOOTLWO, config_dir=Path(tmp))

    def test_passes_config_dir_and_formats(self):
        captured = {}

        def fake_search(*, query, config_dir, max_results, timeout_seconds):
            captured["config_dir"] = config_dir
            return (
                SimpleNamespace(title="Setuptools flat-layout", url="https://setuptools.pypa.io/x", snippet="set packages explicitly"),
            )

        with mock.patch("duckln.internet_skill.internet_search_summary", fake_search):
            res = T._web_search_handler({"query": "setuptools multiple top-level packages"}, self._ctx("/tmp"))
        self.assertTrue(res.ok)
        self.assertIsNotNone(captured["config_dir"])             # the missing kwarg is now passed
        self.assertIn("setuptools.pypa.io", res.payload["summary"])
        self.assertEqual(res.payload["result_count"], 1)

    def test_internet_off_is_actionable_not_a_crash(self):
        with mock.patch("duckln.internet_skill.internet_search_summary", lambda **k: ()):
            res = T._web_search_handler({"query": "x"}, self._ctx("/tmp"))
        self.assertTrue(res.ok)
        self.assertIn("/internet on", res.payload["summary"])     # tells the user how to enable

    def test_real_signature_does_not_raise_typeerror(self):
        # The bug was a missing required kwarg → TypeError every call. Calling with the REAL
        # function (internet off → returns ()) must NOT raise.
        res = T._web_search_handler({"query": "x"}, self._ctx("/tmp"))
        self.assertTrue(res.ok)


class ModelAwareHonestStop(unittest.TestCase):
    """F3: never blame a capable model; point at the real action."""

    def test_honest_stop_is_model_aware(self):
        src = inspect.getsource(rb._attempt_amendment_and_halt)
        self.assertIn("weak_model_reasoning_note", src)           # only-when-weak note
        self.assertIn("/internet on", src)                        # capable model → search nudge
        # the OLD unconditional "stronger model or working quota" blame is gone
        self.assertNotIn("A stronger model or working quota may let me reason this out", src)


class SpeculativeStepFlag(unittest.TestCase):
    """F4: an unbacked, low-confidence step is flagged speculative; backed ones are not."""

    def _step(self, **kw):
        from duckln.plan_mode import PlanStep
        base = dict(index=1, title="t", description="d", command="echo x", safety_class="S0",
                    verification=None, rationale="", estimated_seconds=5, confidence=1.0,
                    origin="planner", source="", evidence_excerpt="")
        base.update(kw)
        return PlanStep(**base)

    def test_unbacked_low_confidence_is_speculative(self):
        from duckln.ui import _plan_step_is_speculative
        self.assertTrue(_plan_step_is_speculative(self._step(confidence=0.3, source="", evidence_excerpt="")))

    def test_evidence_or_source_or_highconf_is_not_speculative(self):
        from duckln.ui import _plan_step_is_speculative
        self.assertFalse(_plan_step_is_speculative(self._step(confidence=0.3, evidence_excerpt="package.json scripts.build")))
        self.assertFalse(_plan_step_is_speculative(self._step(confidence=0.3, source="README")))
        self.assertFalse(_plan_step_is_speculative(self._step(confidence=0.9)))
        self.assertFalse(_plan_step_is_speculative(self._step(confidence=0.3, origin="amendment")))


class ResilientVenvSetup(unittest.TestCase):
    """F5: PyInstaller installs even when `pip install -e .` fails."""

    def test_editable_install_is_non_blocking(self):
        cmd = rb._SECONDARY_PY_SETUP_CMD
        self.assertIn("install -e . 2>/dev/null", cmd)
        self.assertIn("install . 2>/dev/null", cmd)               # non-editable fallback
        self.assertIn("pip install pyinstaller", cmd)             # still reached
        self.assertIn("touch .venv/.duckln-deps-ok", cmd)

    def test_recovery_venv_fix_also_resilient(self):
        from duckln.diagnostics import match_deterministic_fix
        fix = match_deterministic_fix(
            stderr="Error: Python virtual environment not found: /x/backend/.venv/bin/python",
            execution_target="vm",
        )
        self.assertIn("install -e . 2>/dev/null", fix.fix_command)
        self.assertIn("pyinstaller", fix.fix_command.lower())


class DeeperRagFetch(unittest.TestCase):
    """F6: the recovery/repo agent can web.fetch and is told to read the top authoritative doc."""

    def test_repo_agent_has_web_fetch(self):
        from duckln.repo_agent import load_repo_qa_definition
        self.assertIn("web.fetch", load_repo_qa_definition().tools)

    def test_recovery_prompt_directs_authoritative_fetch(self):
        s = rec._recovery_spec_prompt("recovery_agent")
        self.assertIn("web.fetch", s)
        self.assertIn("authoritative", s.lower())


if __name__ == "__main__":
    unittest.main()
