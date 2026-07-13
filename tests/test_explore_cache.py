"""Tests for the /explore SQLite-backed 15-minute cache."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.explore_cache import (
    DEFAULT_TTL_SECONDS,
    cache_age_seconds,
    cache_clear,
    cache_get,
    cache_put,
)
from duckln.explore_trending import TrendingPeriod, TrendingRepo


def _make_repo(name: str = "owner/name") -> TrendingRepo:
    return TrendingRepo(
        full_name=name,
        repo_url=f"https://github.com/{name}",
        description="example",
        language="Python",
        total_stars=1000,
        period_stars=200,
        is_ai_relevant=True,
    )


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmp.name)
        # Start clock at 1700000000 (a fixed Unix epoch) so we can advance it deterministically.
        self.now = 1_700_000_000.0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _clock(self) -> float:
        return self.now

    def test_miss_returns_none(self) -> None:
        result = cache_get(
            self.config_dir,
            period=TrendingPeriod.TODAY,
            language=None,
            clock=self._clock,
        )
        self.assertIsNone(result)

    def test_put_then_get_roundtrip(self) -> None:
        repos = (_make_repo("a/b"), _make_repo("c/d"))
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=repos, clock=self._clock)
        result = cache_get(self.config_dir, period=TrendingPeriod.TODAY, language=None, clock=self._clock)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].full_name, "a/b")
        self.assertEqual(result[0].repo_url, "https://github.com/a/b")
        self.assertTrue(result[0].is_ai_relevant)

    def test_expires_after_ttl(self) -> None:
        repos = (_make_repo(),)
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=repos, clock=self._clock)
        self.now += DEFAULT_TTL_SECONDS + 1
        result = cache_get(self.config_dir, period=TrendingPeriod.TODAY, language=None, clock=self._clock)
        self.assertIsNone(result)

    def test_still_valid_within_ttl(self) -> None:
        repos = (_make_repo(),)
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=repos, clock=self._clock)
        self.now += DEFAULT_TTL_SECONDS - 1
        result = cache_get(self.config_dir, period=TrendingPeriod.TODAY, language=None, clock=self._clock)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)

    def test_age_reports_elapsed_seconds(self) -> None:
        repos = (_make_repo(),)
        cache_put(self.config_dir, period=TrendingPeriod.WEEK, language="python", repos=repos, clock=self._clock)
        self.now += 240
        age = cache_age_seconds(self.config_dir, period=TrendingPeriod.WEEK, language="python", clock=self._clock)
        self.assertEqual(age, 240)

    def test_different_keys_do_not_collide(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=(_make_repo("today/x"),), clock=self._clock)
        cache_put(self.config_dir, period=TrendingPeriod.WEEK, language="python", repos=(_make_repo("week/python"),), clock=self._clock)
        cache_put(self.config_dir, period=TrendingPeriod.WEEK, language="rust", repos=(_make_repo("week/rust"),), clock=self._clock)
        today = cache_get(self.config_dir, period=TrendingPeriod.TODAY, language=None, clock=self._clock)
        week_python = cache_get(self.config_dir, period=TrendingPeriod.WEEK, language="python", clock=self._clock)
        week_rust = cache_get(self.config_dir, period=TrendingPeriod.WEEK, language="rust", clock=self._clock)
        self.assertEqual(today[0].full_name, "today/x")
        self.assertEqual(week_python[0].full_name, "week/python")
        self.assertEqual(week_rust[0].full_name, "week/rust")

    def test_clear_removes_only_target_key(self) -> None:
        cache_put(self.config_dir, period=TrendingPeriod.TODAY, language=None, repos=(_make_repo(),), clock=self._clock)
        cache_put(self.config_dir, period=TrendingPeriod.WEEK, language=None, repos=(_make_repo(),), clock=self._clock)
        cache_clear(self.config_dir, period=TrendingPeriod.TODAY, language=None)
        self.assertIsNone(
            cache_get(self.config_dir, period=TrendingPeriod.TODAY, language=None, clock=self._clock)
        )
        self.assertIsNotNone(
            cache_get(self.config_dir, period=TrendingPeriod.WEEK, language=None, clock=self._clock)
        )


if __name__ == "__main__":
    unittest.main()
