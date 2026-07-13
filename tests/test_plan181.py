"""Plan 181 — the production-grade Web Reader: a routable model distills a long fetched page
into a STRUCTURED digest, strictly ADDITIVE with a raw-excerpt fallback so the fix can never be
summarized away; the deterministic extraction stays the floor."""

from __future__ import annotations

import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from duckln.web_runtime import _WEB_READER_MIN_CHARS, read_web_page


_BIG = "Some docs. " * 1000  # well over the size gate


def _high_conf_reader(**_kw):
    return '{"relevant": true, "fix_commands": ["pip install pyinstaller"], "key_excerpt": "run pip install pyinstaller", "confidence": "high"}'


def _low_conf_reader(**_kw):
    return '{"relevant": false, "fix_commands": [], "key_excerpt": "", "confidence": "low"}'


class F2ReadWebPage(unittest.TestCase):
    def test_small_page_is_never_distilled(self):
        self.assertIsNone(read_web_page(excerpt="too short", url="u", llm_client=_high_conf_reader))

    def test_no_model_returns_none(self):
        self.assertIsNone(read_web_page(excerpt=_BIG, url="u", config_dir=None, llm_client=None))

    def test_structured_digest_parsed(self):
        d = read_web_page(excerpt=_BIG, url="https://pypi.org/x", llm_client=_high_conf_reader)
        self.assertIsNotNone(d)
        self.assertTrue(d["relevant"])
        self.assertEqual(d["fix_commands"], ["pip install pyinstaller"])
        self.assertEqual(d["confidence"], "high")

    def test_reader_error_falls_back_to_none(self):
        def _boom(**_kw):
            raise RuntimeError("model down")

        self.assertIsNone(read_web_page(excerpt=_BIG, url="u", llm_client=_boom))

    def test_garbage_output_returns_none(self):
        self.assertIsNone(read_web_page(excerpt=_BIG, url="u", llm_client=lambda **k: "not json at all"))


class F1WebFetchDigest(unittest.TestCase):
    def _ctx(self, d):
        from duckln.harness.tools import AgentContext

        return AgentContext(agent_name="t", mode=None, config_dir=Path(d), execution_target="local")

    def test_high_confidence_digest_replaces_raw_excerpt(self):
        import duckln.web_runtime as wr
        from duckln.harness.tools import _web_fetch_handler

        orig_fetch = wr.fetch_web_reference_summary
        orig_auth = None
        import duckln.authoritative_sources as auth

        orig_auth = auth.is_authoritative_url
        wr.fetch_web_reference_summary = lambda *, url, timeout_seconds=4.0: _BIG
        auth.is_authoritative_url = lambda _u: True
        # Patch the reader the handler uses (build_llm_client_for_role → our fake).
        import duckln.ai_client as ai

        orig_role = ai.build_llm_client_for_role
        ai.build_llm_client_for_role = lambda _cfg, _role: _high_conf_reader
        try:
            with TemporaryDirectory() as d:
                res = _web_fetch_handler({"url": "https://pypi.org/project/x"}, self._ctx(d))
                self.assertTrue(res.ok)
                self.assertIn("digest", res.payload)
                self.assertEqual(res.payload["digest"]["fix_commands"], ["pip install pyinstaller"])
        finally:
            wr.fetch_web_reference_summary = orig_fetch
            auth.is_authoritative_url = orig_auth
            ai.build_llm_client_for_role = orig_role

    def test_low_confidence_falls_back_to_raw_excerpt(self):
        import duckln.web_runtime as wr
        import duckln.authoritative_sources as auth
        import duckln.ai_client as ai
        from duckln.harness.tools import _web_fetch_handler

        orig_fetch, orig_auth, orig_role = (
            wr.fetch_web_reference_summary, auth.is_authoritative_url, ai.build_llm_client_for_role,
        )
        wr.fetch_web_reference_summary = lambda *, url, timeout_seconds=4.0: _BIG
        auth.is_authoritative_url = lambda _u: True
        ai.build_llm_client_for_role = lambda _cfg, _role: _low_conf_reader
        try:
            with TemporaryDirectory() as d:
                res = _web_fetch_handler({"url": "https://pypi.org/project/x"}, self._ctx(d))
                self.assertTrue(res.ok)
                self.assertNotIn("digest", res.payload)  # fix never summarized away
                self.assertEqual(res.payload["excerpt"], _BIG)
        finally:
            wr.fetch_web_reference_summary, auth.is_authoritative_url, ai.build_llm_client_for_role = (
                orig_fetch, orig_auth, orig_role,
            )


class F2bSpecAndFloor(unittest.TestCase):
    def test_web_reader_spec_parses(self):
        from duckln.harness.agent_def import (
            builtin_agents_directory,
            load_agent_definition_from_path,
        )

        d = load_agent_definition_from_path(builtin_agents_directory() / "web_reader.md")
        self.assertEqual(d.name, "web_reader")
        self.assertEqual((d.output_contract or {}).get("type"), "json_object")


if __name__ == "__main__":
    unittest.main()
