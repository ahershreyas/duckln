"""Plan 164 — token visibility: per-call sent/received tokens, context-window awareness,
and an honest context-overflow message (not a false "can't connect")."""

from __future__ import annotations

import unittest

from duckln import usage_meter as um
from duckln.ai_client import model_context_window
import duckln.textual_ui as tui


class F1PerCallAccounting(unittest.TestCase):
    def setUp(self):
        um.reset_usage_snapshot()

    def test_estimate_recorded_before_call_and_survives(self):
        um.record_call_estimate(estimated_prompt_tokens=7400, context_window=8192)
        s = um.current_usage_snapshot()
        self.assertEqual(7400, s.last_estimated_prompt_tokens)
        self.assertEqual(8192, s.context_window)

    def test_exact_response_supersedes_estimate(self):
        um.record_call_estimate(estimated_prompt_tokens=7000, context_window=8192)
        um.record_provider_usage(
            provider="openai", model="m",
            payload={"usage": {"prompt_tokens": 7321, "completion_tokens": 412, "total_tokens": 7733}},
        )
        s = um.current_usage_snapshot()
        self.assertEqual(7321, s.last_prompt_tokens)        # exact wins
        self.assertEqual(412, s.last_completion_tokens)
        self.assertEqual(8192, s.context_window)            # context window preserved


class F2ContextWindow(unittest.TestCase):
    def test_known_families(self):
        self.assertEqual(8192, model_context_window("google/gemma-4-31b-it:free"))
        self.assertEqual(128000, model_context_window("gpt-4o-mini"))
        self.assertEqual(200000, model_context_window("claude-opus-4-8"))

    def test_unknown_defaults_conservative(self):
        self.assertEqual(8192, model_context_window("some-unknown-model-xyz"))


class F3RecordedAtChokePoint(unittest.TestCase):
    def setUp(self):
        um.reset_usage_snapshot()

    def test_estimate_recorded_before_call_even_on_failure(self):
        import duckln.ai_client as ai
        from duckln.ai_client import Provider

        class _Boom:
            def generate_reply(self, *a, **k):
                raise RuntimeError("network down")

        orig = ai.get_provider_adapter_for_base_url
        ai.get_provider_adapter_for_base_url = lambda *a, **k: _Boom()
        try:
            with self.assertRaises(RuntimeError):
                ai.generate_provider_reply(
                    Provider.OPENAI, model_id="gpt-4o-mini", api_key="k", base_url=None,
                    system_prompt="x" * 4000, user_message="y" * 4000,
                )
        finally:
            ai.get_provider_adapter_for_base_url = orig
        s = um.current_usage_snapshot()
        self.assertGreater(s.last_estimated_prompt_tokens, 0)   # estimate set BEFORE the failed call
        self.assertEqual(128000, s.context_window)             # gpt-4o-mini window recorded


class F4ActivityBarReadout(unittest.TestCase):
    def test_token_segment_format(self):
        seg = tui._token_activity_segment(prompt_tokens=7400, completion_tokens=1200, context_window=8192)
        self.assertIn("↑7.4k", seg)
        self.assertIn("↓1.2k", seg)
        self.assertIn("ctx ~90%", seg)
        self.assertIn("⚠", seg)

    def test_sub_1k_count_and_no_context(self):
        self.assertEqual("↑900", tui._token_activity_segment(prompt_tokens=900, completion_tokens=0, context_window=0))

    def test_no_tokens_no_segment(self):
        self.assertEqual("", tui._token_activity_segment(prompt_tokens=0, completion_tokens=0, context_window=0))

    def test_segment_appended_to_activity_bar(self):
        _prefix, body, _on = tui._activity_bar_segments(
            activity_text="Reasoning", spinner_on=True, elapsed_seconds=2.0, spinner_frame="*",
            prompt_tokens=3000, completion_tokens=0, context_window=8192,
        )
        self.assertIn("↑3.0k", body)
        self.assertIn("ctx ~37%", body)
        self.assertNotIn("⚠", body)  # 37% is not near the limit


class F5OverflowHonesty(unittest.TestCase):
    def setUp(self):
        um.reset_usage_snapshot()

    def test_near_limit_true_only_when_known_and_high(self):
        um.record_call_estimate(estimated_prompt_tokens=7800, context_window=8192)
        self.assertTrue(um.near_context_limit(um.current_usage_snapshot()))
        um.reset_usage_snapshot()
        um.record_call_estimate(estimated_prompt_tokens=1000, context_window=8192)
        self.assertFalse(um.near_context_limit(um.current_usage_snapshot()))
        um.reset_usage_snapshot()
        um.record_call_estimate(estimated_prompt_tokens=99999, context_window=0)  # unknown window
        self.assertFalse(um.near_context_limit(um.current_usage_snapshot()))

    def test_hint_mentions_context_when_near_else_empty(self):
        um.record_call_estimate(estimated_prompt_tokens=8000, context_window=8192)
        hint = um.context_overflow_hint()
        self.assertIn("context", hint.lower())
        self.assertIn("tokens", hint.lower())
        um.reset_usage_snapshot()
        um.record_call_estimate(estimated_prompt_tokens=500, context_window=8192)
        self.assertEqual("", um.context_overflow_hint())

    def test_repo_bringup_with_context_hint_appends_only_when_near(self):
        import duckln.repo_bringup as rb
        um.record_call_estimate(estimated_prompt_tokens=8000, context_window=8192)
        out = rb._with_context_hint("Can't connect.")
        self.assertIn("context", out.lower())
        um.reset_usage_snapshot()
        self.assertEqual("Can't connect.", rb._with_context_hint("Can't connect."))


if __name__ == "__main__":
    unittest.main()
