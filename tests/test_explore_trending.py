"""Tests for the GitHub trending scraper used by /explore."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from duckln.explore_trending import (
    AI_RELEVANT_LANGUAGES,
    TrendingFetchError,
    TrendingPeriod,
    build_trending_url,
    fetch_trending,
    parse_trending_html,
    sort_for_display,
)


_FIXTURE_HTML = """
<html><body>
<article class="Box-row">
  <h2><a href="/anthropic/claude-cookbook">anthropic/claude-cookbook</a></h2>
  <p>Recipes for building production agents with Claude.</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/anthropic/claude-cookbook/stargazers">12,345</a>
  <span class="d-inline-block float-sm-right">1,367 stars today</span>
</article>
<article class="Box-row">
  <h2><a href="/ggerganov/llama.cpp">ggerganov/llama.cpp</a></h2>
  <p>Port of Facebook's LLaMA in C/C++.</p>
  <span itemprop="programmingLanguage">C++</span>
  <a href="/ggerganov/llama.cpp/stargazers">98,765</a>
  <span class="d-inline-block float-sm-right">412 stars today</span>
</article>
<article class="Box-row">
  <h2><a href="/some-org/no-desc-repo">some-org/no-desc-repo</a></h2>
  <span itemprop="programmingLanguage">Go</span>
  <a href="/some-org/no-desc-repo/stargazers">8,432</a>
  <span class="d-inline-block float-sm-right">200 stars today</span>
</article>
</body></html>
"""


class BuildUrlTests(unittest.TestCase):
    def test_period_only(self) -> None:
        self.assertEqual(
            build_trending_url(TrendingPeriod.TODAY, None),
            "https://github.com/trending?since=daily",
        )

    def test_with_language(self) -> None:
        self.assertEqual(
            build_trending_url(TrendingPeriod.WEEK, "Python"),
            "https://github.com/trending/python?since=weekly",
        )

    def test_language_with_space_uri_encoded(self) -> None:
        self.assertEqual(
            build_trending_url(TrendingPeriod.MONTH, "Jupyter Notebook"),
            "https://github.com/trending/jupyter%20notebook?since=monthly",
        )


class ParserTests(unittest.TestCase):
    def test_parses_three_repos_with_descriptions_and_stars(self) -> None:
        repos = parse_trending_html(_FIXTURE_HTML, period=TrendingPeriod.TODAY)
        self.assertEqual(len(repos), 3)
        names = [r.full_name for r in repos]
        self.assertIn("anthropic/claude-cookbook", names)
        self.assertIn("ggerganov/llama.cpp", names)
        first = repos[0]
        self.assertEqual(first.full_name, "anthropic/claude-cookbook")
        self.assertEqual(first.repo_url, "https://github.com/anthropic/claude-cookbook")
        self.assertEqual(first.language, "Python")
        self.assertEqual(first.total_stars, 12345)
        self.assertEqual(first.period_stars, 1367)
        self.assertTrue(first.is_ai_relevant)

    def test_missing_description_yields_empty_string(self) -> None:
        repos = parse_trending_html(_FIXTURE_HTML, period=TrendingPeriod.TODAY)
        no_desc = next(r for r in repos if r.full_name == "some-org/no-desc-repo")
        self.assertEqual(no_desc.description, "")
        self.assertFalse(no_desc.is_ai_relevant)

    def test_ai_relevant_languages_match_documented_set(self) -> None:
        self.assertIn("python", AI_RELEVANT_LANGUAGES)
        self.assertIn("jupyter notebook", AI_RELEVANT_LANGUAGES)
        self.assertIn("cuda", AI_RELEVANT_LANGUAGES)
        self.assertIn("rust", AI_RELEVANT_LANGUAGES)
        self.assertIn("c++", AI_RELEVANT_LANGUAGES)
        self.assertNotIn("go", AI_RELEVANT_LANGUAGES)


class SortTests(unittest.TestCase):
    def test_no_description_repos_sink_to_bottom(self) -> None:
        repos = parse_trending_html(_FIXTURE_HTML, period=TrendingPeriod.TODAY)
        ordered = sort_for_display(repos)
        # 3rd in the input had no description; should be last after sorting.
        self.assertEqual(ordered[-1].full_name, "some-org/no-desc-repo")

    def test_within_bucket_ordered_by_period_stars_desc(self) -> None:
        repos = parse_trending_html(_FIXTURE_HTML, period=TrendingPeriod.TODAY)
        ordered = sort_for_display(repos)
        self.assertEqual(ordered[0].full_name, "anthropic/claude-cookbook")  # 1367 today
        self.assertEqual(ordered[1].full_name, "ggerganov/llama.cpp")  # 412 today


class FetchTests(unittest.TestCase):
    def test_fetch_uses_client_and_returns_sorted_repos(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def get(self, url: str, *, headers, timeout):
                calls.append(url)
                return SimpleNamespace(status_code=200, text=_FIXTURE_HTML)

        repos = fetch_trending(
            period=TrendingPeriod.WEEK,
            language="Python",
            client=FakeClient(),
        )
        self.assertEqual(len(repos), 3)
        self.assertEqual(repos[0].full_name, "anthropic/claude-cookbook")  # sorted
        self.assertIn("python", calls[0])
        self.assertIn("weekly", calls[0])

    def test_fetch_raises_on_network_failure(self) -> None:
        class FakeClient:
            def get(self, *_args, **_kwargs):
                raise ConnectionError("DNS down")

        with self.assertRaises(TrendingFetchError):
            fetch_trending(period=TrendingPeriod.TODAY, client=FakeClient())

    def test_fetch_raises_on_non_200(self) -> None:
        class FakeClient:
            def get(self, *_args, **_kwargs):
                return SimpleNamespace(status_code=503, text="")

        with self.assertRaises(TrendingFetchError):
            fetch_trending(period=TrendingPeriod.TODAY, client=FakeClient())


if __name__ == "__main__":
    unittest.main()
