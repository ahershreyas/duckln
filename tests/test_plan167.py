"""Plan 167 — make the token counter trustworthy: no stale per-call counts on a failed/429
call, mark estimate vs exact (~), use the model's real context window, label session total."""

from __future__ import annotations

import unittest

from duckln import usage_meter as um
from duckln.ai_client import OpenRouterAdapter, model_context_window, persist_model_context_window
import duckln.textual_ui as tui


class F1NoStaleOnFailure(unittest.TestCase):
    def setUp(self):
        um.reset_usage_snapshot()

    def test_estimate_resets_prior_exact_counts(self):
        um.record_call_estimate(estimated_prompt_tokens=3000, context_window=8192)
        um.record_provider_usage(provider="openrouter", model="m",
                                 payload={"usage": {"prompt_tokens": 3100, "completion_tokens": 90, "total_tokens": 3190}})
        s1 = um.current_usage_snapshot()
        self.assertTrue(s1.last_prompt_is_exact and s1.last_prompt_tokens == 3100 and s1.last_completion_tokens == 90)
        # A new call starts (which then fails/429 → no record_provider_usage): per-call counts reset.
        um.record_call_estimate(estimated_prompt_tokens=3500, context_window=8192)
        s2 = um.current_usage_snapshot()
        self.assertEqual(0, s2.last_prompt_tokens)        # no stale exact
        self.assertEqual(0, s2.last_completion_tokens)    # no stale ↓
        self.assertFalse(s2.last_prompt_is_exact)
        self.assertEqual(3500, s2.last_estimated_prompt_tokens)


class F2EstimateMarker(unittest.TestCase):
    def test_tilde_when_estimate(self):
        self.assertIn("↑~3.5k", tui._token_activity_segment(prompt_tokens=3500, context_window=8192, is_estimate=True))

    def test_no_tilde_when_exact(self):
        seg = tui._token_activity_segment(prompt_tokens=3500, context_window=8192, is_estimate=False)
        self.assertIn("↑3.5k", seg)
        self.assertNotIn("~3.5k", seg)


class F3RealContextWindow(unittest.TestCase):
    def test_parse_models_captures_context_length(self):
        models = OpenRouterAdapter().parse_models({"data": [{"id": "x/y", "name": "Y", "context_length": 32768}]})
        self.assertEqual(32768, models[0].context_length)

    def test_persist_uses_real_window_over_family_guess(self):
        import tempfile
        from pathlib import Path

        models = OpenRouterAdapter().parse_models({"data": [{"id": "google/gemma-4-31b-it:free", "context_length": 32768}]})
        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            # family-table guess for gemma is 8192…
            self.assertEqual(8192, model_context_window("google/gemma-4-31b-it:free", config_dir=cd))
            persist_model_context_window(cd, models, "google/gemma-4-31b-it:free")
            # …but the persisted real window (32768) now wins.
            self.assertEqual(32768, model_context_window("google/gemma-4-31b-it:free", config_dir=cd))

    def test_persist_clears_stale_when_unknown(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            cd = Path(td)
            persist_model_context_window(cd, OpenRouterAdapter().parse_models({"data": [{"id": "a", "context_length": 50000}]}), "a")
            self.assertEqual(50000, model_context_window("a", config_dir=cd))
            # switch to a model with no known context → the stale override is cleared.
            persist_model_context_window(cd, OpenRouterAdapter().parse_models({"data": [{"id": "b"}]}), "b")
            self.assertEqual(8192, model_context_window("b", config_dir=cd))  # falls back to default


class F4SessionLabel(unittest.TestCase):
    def test_session_suffix(self):
        self.assertEqual(" · 8.2k tokens (session)", tui._thoughts_token_suffix(8200))


if __name__ == "__main__":
    unittest.main()
