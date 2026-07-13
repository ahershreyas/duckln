"""Plan 176 — the aware-interaction capability is a SKILL (.md, detailed + one-shot examples) +
a deterministic FLOOR + an LLM VOICE, and the helper is actually WIRED (not inline strings).
"""

from __future__ import annotations

import types
import unittest
from pathlib import Path

from duckln.interaction import _aware_interaction_skill, build_failure_proposal


class F1AwareInteractionSkill(unittest.TestCase):
    def test_skill_exists_detailed_with_one_shot_examples(self):
        p = Path("src/agent/playbooks/aware_interaction.md")
        txt = p.read_text(encoding="utf-8")
        low = txt.lower()
        for kw in ("comprehend", "explain", "recommend", "yes / no", "your-own", "tell me what to do", "aware"):
            self.assertIn(kw, low, kw)
        # ≥2 worked one-shot examples with Input → Correct output.
        self.assertGreaterEqual(txt.count("### Example"), 2)
        self.assertIn("Input:", txt)
        self.assertIn("Correct output:", txt)
        # loadable by the helper.
        self.assertIn("comprehend", _aware_interaction_skill().lower())


class F2DeterministicFloorPlusLlmVoice(unittest.TestCase):
    def test_floor_uses_matched_fix_no_model(self):
        det = types.SimpleNamespace(fix_command="npm install --include=dev", cause="vite missing")
        sit, rec, why = build_failure_proposal(
            failed_command="npm run build", error_text="Cannot find package 'vite'",
            repo_name="JustHireMe", det_fix=det, llm_client=None,
        )
        self.assertIn("JustHireMe", sit)
        self.assertIn("npm install --include=dev", rec)
        self.assertEqual(why, "vite missing")

    def test_llm_voices_over_facts(self):
        det = types.SimpleNamespace(fix_command="npm install --include=dev", cause="vite missing")
        def _llm(**k):
            return ('{"situation": "the frontend build failed: vite not installed", '
                    '"recommendation": "run npm install --include=dev", "why": "adds the build tools"}')
        sit, rec, why = build_failure_proposal(
            failed_command="npm run build", error_text="Cannot find package 'vite'",
            det_fix=det, llm_client=_llm,
        )
        self.assertIn("frontend build failed", sit)
        self.assertIn("npm install --include=dev", rec)

    def test_honest_when_no_fix_no_model(self):
        sit, rec, why = build_failure_proposal(failed_command="x", error_text="y", det_fix=None, llm_client=None)
        self.assertIn("don't have a confident", rec.lower())
        self.assertIn("x", sit)


class F3HelperIsWired(unittest.TestCase):
    def test_amendment_pause_uses_build_failure_proposal(self):
        src = Path("src/duckln/repo_bringup.py").read_text(encoding="utf-8")
        self.assertIn("from duckln.interaction import build_failure_proposal", src)


class BackfillAndCapabilities(unittest.TestCase):
    def test_resource_management_has_one_shot_examples(self):
        txt = Path("src/agent/playbooks/resource_management.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(txt.count("### Example"), 2)

    def test_capabilities_lists_aware_interaction(self):
        txt = Path("docs/capabilities.md").read_text(encoding="utf-8")
        self.assertIn("aware_interaction", txt)


if __name__ == "__main__":
    unittest.main()
