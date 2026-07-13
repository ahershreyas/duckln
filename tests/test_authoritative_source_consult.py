"""Tests: authoritative-source consultation for unknown tools (Plan 61 Fix G)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from duckln.authoritative_sources import (
    is_authoritative_url,
    consult_authoritative_source,
    format_user_guidance,
    _AUTHORITATIVE_PUBLISHERS,
)


class AuthoritativeUrlTest(unittest.TestCase):
    def test_official_docs_accepted(self) -> None:
        self.assertTrue(is_authoritative_url("https://docs.python.org/3/library/os.html"))
        self.assertTrue(is_authoritative_url("https://nodejs.org/en/download"))
        self.assertTrue(is_authoritative_url("https://bazel.build/install"))

    def test_subdomain_of_allowlist_accepted(self) -> None:
        # learn.microsoft.com is a subdomain of microsoft.com.
        self.assertTrue(is_authoritative_url("https://learn.microsoft.com/en-us/cli/azure"))

    def test_random_blog_rejected(self) -> None:
        self.assertFalse(is_authoritative_url("https://example.com/blog/install-bazel"))
        self.assertFalse(is_authoritative_url("https://medium.com/@user/install-tool"))

    def test_empty_url_rejected(self) -> None:
        self.assertFalse(is_authoritative_url(""))
        self.assertFalse(is_authoritative_url("not a url"))


class ConsultAuthoritativeTest(unittest.TestCase):
    def test_returns_none_for_empty_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = consult_authoritative_source(
                tool_name="",
                config_dir=Path(tmp),
            )
            self.assertIsNone(result)

    def test_returns_url_when_search_finds_authoritative_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_summary = "Bazel install docs at https://bazel.build/install/ubuntu"
            with patch(
                "duckln.internet_skill.internet_search_summary",
                return_value=fake_summary,
            ):
                result = consult_authoritative_source(
                    tool_name="bazel",
                    config_dir=Path(tmp),
                )
            self.assertIsNotNone(result)
            self.assertIn("bazel.build", result)

    def test_rejects_non_authoritative_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_summary = "Check this blog post at https://random-blog.example/install-bazel"
            with patch(
                "duckln.internet_skill.internet_search_summary",
                return_value=fake_summary,
            ):
                result = consult_authoritative_source(
                    tool_name="bazel",
                    config_dir=Path(tmp),
                )
            self.assertIsNone(result)

    def test_cache_avoids_second_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_summary = "Install from https://bazel.build/install"
            with patch(
                "duckln.internet_skill.internet_search_summary",
                return_value=fake_summary,
            ) as mock_search:
                consult_authoritative_source(tool_name="bazel", config_dir=Path(tmp))
                consult_authoritative_source(tool_name="bazel", config_dir=Path(tmp))
            # Cached → only one search call.
            self.assertEqual(mock_search.call_count, 1)


class FormatGuidanceTest(unittest.TestCase):
    def test_url_present_message_mentions_url(self) -> None:
        msg = format_user_guidance(tool_name="bazel", url="https://bazel.build/install")
        self.assertIn("bazel", msg)
        self.assertIn("https://bazel.build/install", msg)
        self.assertIn("retry", msg.lower())

    def test_no_url_message_acknowledges_missing_source(self) -> None:
        msg = format_user_guidance(tool_name="obscure", url=None)
        self.assertIn("obscure", msg)
        self.assertIn("manually", msg.lower())


if __name__ == "__main__":
    unittest.main()
