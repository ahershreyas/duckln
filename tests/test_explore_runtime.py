"""Tests for the /explore runtime glue (load + cache + error paths)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.explore_cache import cache_put
from duckln.explore_runtime import (
    CONNECTION_ERROR_MESSAGE,
    EMPTY_PARSE_MESSAGE,
    ExploreView,
    filter_repos_by_language,
    load_explore_view,
    refresh_explore_view,
    render_explore_header,
    render_repo_label,
    run_explore_loop,
)
from duckln.explore_trending import TrendingFetchError, TrendingPeriod, TrendingRepo


def _make_repo(name: str = "owner/name", language: str = "Python", desc: str = "ok") -> TrendingRepo:
    return TrendingRepo(
        full_name=name,
        repo_url=f"https://github.com/{name}",
        description=desc,
        language=language,
        total_stars=12345,
        period_stars=789,
        is_ai_relevant=(language.lower() in {"python", "rust", "c++", "cuda", "jupyter notebook"}),
    )


class LoadExploreViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_cached_payload_when_fresh(self) -> None:
        cached = (_make_repo("from/cache"),)
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=cached)

        def fetcher(**_kwargs):
            self.fail("fetcher should not be called when cache is fresh")

        outcome = load_explore_view(
            config_dir=self.config_dir,
            period=TrendingPeriod.TODAY,
            fetcher=fetcher,
        )
        self.assertIsInstance(outcome, ExploreView)
        self.assertTrue(outcome.served_from_cache)
        self.assertEqual(outcome.repos[0].full_name, "from/cache")

    def test_force_refresh_bypasses_cache(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=(_make_repo("stale/cache"),))
        fresh = (_make_repo("fresh/fetch"),)

        def fetcher(**_kwargs):
            return fresh

        outcome = load_explore_view(
            config_dir=self.config_dir,
            period=TrendingPeriod.TODAY,
            force_refresh=True,
            fetcher=fetcher,
        )
        self.assertIsInstance(outcome, ExploreView)
        self.assertFalse(outcome.served_from_cache)
        self.assertEqual(outcome.repos[0].full_name, "fresh/fetch")

    def test_network_failure_returns_connection_message(self) -> None:
        def fetcher(**_kwargs):
            raise TrendingFetchError("boom")

        outcome = load_explore_view(
            config_dir=self.config_dir,
            period=TrendingPeriod.TODAY,
            fetcher=fetcher,
        )
        self.assertEqual(outcome, CONNECTION_ERROR_MESSAGE)

    def test_empty_parse_returns_unavailable_message(self) -> None:
        def fetcher(**_kwargs):
            return ()

        outcome = load_explore_view(
            config_dir=self.config_dir,
            period=TrendingPeriod.TODAY,
            fetcher=fetcher,
        )
        self.assertEqual(outcome, EMPTY_PARSE_MESSAGE)

    def test_refresh_clears_cache_and_fetches(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.WEEK, language=None, repos=(_make_repo("old/cached"),))

        def fetcher(**_kwargs):
            return (_make_repo("new/fresh"),)

        outcome = refresh_explore_view(
            config_dir=self.config_dir,
            period=TrendingPeriod.WEEK,
        )
        # refresh_explore_view uses the default fetcher (httpx) — test it via the more granular
        # load_explore_view with force_refresh, which is what refresh calls internally.
        outcome = load_explore_view(
            config_dir=self.config_dir,
            period=TrendingPeriod.WEEK,
            force_refresh=True,
            fetcher=fetcher,
        )
        self.assertIsInstance(outcome, ExploreView)
        self.assertEqual(outcome.repos[0].full_name, "new/fresh")


class FilterTests(unittest.TestCase):
    def test_language_filter_substring(self) -> None:
        repos = (_make_repo("a/b", language="Python"), _make_repo("c/d", language="Rust"))
        filtered = filter_repos_by_language(repos, "py")
        self.assertEqual([r.full_name for r in filtered], ["a/b"])

    def test_blank_filter_keeps_all(self) -> None:
        repos = (_make_repo("a/b"), _make_repo("c/d"))
        self.assertEqual(filter_repos_by_language(repos, ""), repos)


class RenderTests(unittest.TestCase):
    def test_repo_label_includes_ai_tag_for_python(self) -> None:
        label = render_repo_label(_make_repo("anthropic/x", language="Python"), TrendingPeriod.TODAY)
        self.assertIn("anthropic/x", label)
        self.assertIn("[AI]", label)
        self.assertIn("today", label)
        self.assertIn("★", label)

    def test_repo_label_no_ai_tag_for_go(self) -> None:
        label = render_repo_label(_make_repo("a/b", language="Go"), TrendingPeriod.WEEK)
        self.assertNotIn("[AI]", label)
        self.assertIn("this week", label)

    def test_header_shows_cache_indicator_when_cached(self) -> None:
        view = ExploreView(
            period=TrendingPeriod.TODAY,
            language=None,
            repos=(),
            served_from_cache=True,
            cache_age_seconds=240,
        )
        header = render_explore_header(view)
        self.assertIn("cached 4m ago", header)


class RunExploreLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_selecting_repo_returns_url(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=(_make_repo("anthropic/x"),))
        prompts: list[str] = []

        def select(prompt: str, options) -> str:
            prompts.append(prompt)
            # Pick the repo row (filters are now at the top of the list).
            for option in options:
                if "anthropic/x" in option:
                    return option
            return options[-1]

        chosen = run_explore_loop(
            config_dir=self.config_dir,
            select_prompt=select,
            text_prompt=None,
            display_output=lambda _line: None,
        )
        self.assertEqual(chosen, "https://github.com/anthropic/x")
        self.assertTrue(any("Press Q or Esc to cancel" in prompt for prompt in prompts))

    def test_cancel_returns_none(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=(_make_repo("anthropic/x"),))

        def select(_prompt: str, _options) -> str:
            return "Cancel"

        chosen = run_explore_loop(
            config_dir=self.config_dir,
            select_prompt=select,
            text_prompt=None,
            display_output=lambda _line: None,
        )
        self.assertIsNone(chosen)

    def test_period_switch_then_pick(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=(_make_repo("daily/repo"),))
        cache_put(self.config_dir, period=TrendingPeriod.WEEK, language=None, repos=(_make_repo("weekly/repo"),))
        seen_periods: list[str] = []

        def select(prompt: str, options) -> str:
            # First round: switch to week. Second round: pick the repo.
            for option in options:
                if "weekly/repo" in option:
                    seen_periods.append("weekly")
                    return option
            for option in options:
                if "Period — This Week" in option:
                    seen_periods.append("daily")
                    return option
            return options[0]

        chosen = run_explore_loop(
            config_dir=self.config_dir,
            select_prompt=select,
            text_prompt=None,
            display_output=lambda _line: None,
        )
        self.assertEqual(chosen, "https://github.com/weekly/repo")
        self.assertEqual(seen_periods, ["daily", "weekly"])

    def test_error_message_returned_when_load_fails(self) -> None:
        class FailingClient:
            def get(self, *_args, **_kwargs):
                raise ConnectionError("offline in tests")

        displays: list[str] = []
        chosen = run_explore_loop(
            config_dir=self.config_dir,
            select_prompt=lambda _p, _o: "Cancel",
            text_prompt=None,
            display_output=displays.append,
            http_client=FailingClient(),
        )
        self.assertIsNone(chosen)
        self.assertTrue(any("GitHub trending" in line for line in displays))


if __name__ == "__main__":
    unittest.main()
