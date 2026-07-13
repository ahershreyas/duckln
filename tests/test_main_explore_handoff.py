"""Integration test: selecting a repo in /explore hands its URL to bring_up_selected_repo."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from duckln.config import ConfigPaths
from duckln.explore_cache import cache_put
from duckln.explore_trending import TrendingPeriod, TrendingRepo
from duckln.main import _handle_explore_command
from duckln.modes import ControlMode


def _make_repo(name: str = "anthropic/claude-cookbook") -> TrendingRepo:
    return TrendingRepo(
        full_name=name,
        repo_url=f"https://github.com/{name}",
        description="example",
        language="Python",
        total_stars=12345,
        period_stars=789,
        is_ai_relevant=True,
    )


class ExploreHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        config_dir = Path(self._tmp.name)
        self.paths = ConfigPaths(config_dir=config_dir, config_file=config_dir / "config.json")
        # Seed cache so the loop doesn't try to hit the network.
        cache_put(
            config_dir,
            period=TrendingPeriod.TODAY,
            language=None,
            repos=(_make_repo(),),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_select_repo_calls_bring_up_with_resolved_record(self) -> None:
        captured: dict[str, object] = {}

        fake_record = SimpleNamespace(
            name="anthropic/claude-cookbook",
            repo_url="https://github.com/anthropic/claude-cookbook",
            stars=12345,
            description="example",
            category="ai",
            framework="python",
            last_updated="2026",
            warning=None,
        )

        def fake_resolve(url, *, client=None):
            captured["resolved_url"] = url
            return fake_record

        def fake_bring_up(record, mode, paths, **kwargs):
            captured["bring_up_record"] = record
            captured["bring_up_mode"] = mode
            captured["approve"] = kwargs.get("approve")
            captured["display"] = kwargs.get("display")
            return SimpleNamespace(ok=True, message="done")

        def select(prompt: str, options) -> str:
            # Filters are at the top — locate the trending repo row in the list.
            for option in options:
                if "anthropic/claude-cookbook" in option:
                    return option
            return options[-1]

        approve_calls: list[str] = []

        current = SimpleNamespace(
            mode=ControlMode.HITL,
            provider=SimpleNamespace(value="anthropic"),
            user_name="shreyas",
        )
        with patch(
            "duckln.main.resolve_public_github_repo_record",
            side_effect=fake_resolve,
        ), patch(
            "duckln.main.bring_up_selected_repo",
            side_effect=fake_bring_up,
        ):
            _handle_explore_command(
                paths=self.paths,
                current=current,
                select_prompt=select,
                text_prompt=lambda _p, _d: _d,
                approve_prompt=lambda message: approve_calls.append(message) or True,
                display_output=lambda _line: None,
                terminal_interface=None,
                system_probe=None,
            )

        self.assertEqual(captured.get("resolved_url"), "https://github.com/anthropic/claude-cookbook")
        self.assertIs(captured.get("bring_up_record"), fake_record)
        self.assertEqual(captured.get("bring_up_mode"), ControlMode.HITL)
        # No "are you sure" confirmation between selection and handoff.
        self.assertEqual(approve_calls, [])

    def test_cancel_does_not_invoke_bring_up(self) -> None:
        called: dict[str, bool] = {"resolve": False, "bring_up": False}

        def fake_resolve(*_args, **_kwargs):
            called["resolve"] = True
            return None

        def fake_bring_up(*_args, **_kwargs):
            called["bring_up"] = True
            return None

        current = SimpleNamespace(
            mode=ControlMode.HITL,
            provider=SimpleNamespace(value="anthropic"),
            user_name="shreyas",
        )
        with patch(
            "duckln.main.resolve_public_github_repo_record",
            side_effect=fake_resolve,
        ), patch(
            "duckln.main.bring_up_selected_repo",
            side_effect=fake_bring_up,
        ):
            _handle_explore_command(
                paths=self.paths,
                current=current,
                select_prompt=lambda _p, _o: "Cancel",
                text_prompt=lambda _p, _d: _d,
                approve_prompt=lambda _m: True,
                display_output=lambda _line: None,
                terminal_interface=None,
                system_probe=None,
            )
        self.assertFalse(called["resolve"])
        self.assertFalse(called["bring_up"])


if __name__ == "__main__":
    unittest.main()
