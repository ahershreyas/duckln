"""Tests for _offer_skill_approval — mode-gated skill persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch, MagicMock

from duckln.main import _offer_skill_approval
from duckln.modes import ControlMode


_SLUG = "repair-node-typescript-enoent"
_TITLE = "Repair: ENOENT during npm install"
_SUMMARY = "## Trigger\nError pattern: ENOENT\n\n## Fix sequence\n1. npm cache clean --force"


class HitlApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_hitl_approved_persists_skill(self) -> None:
        displayed: list[str] = []
        approve_calls: list[str] = []

        def approve(prompt: str) -> bool:
            approve_calls.append(prompt)
            return True

        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HITL,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=approve,
                display=displayed.append,
            )
        self.assertTrue(result)
        mock_write.assert_called_once_with(
            self.config_dir, slug=_SLUG, title=_TITLE, summary=_SUMMARY
        )
        self.assertTrue(any("Skill saved" in line for line in displayed))

    def test_hitl_declined_does_not_persist(self) -> None:
        displayed: list[str] = []

        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HITL,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=lambda _: False,
                display=displayed.append,
            )
        self.assertFalse(result)
        mock_write.assert_not_called()

    def test_hitl_no_approve_callback_does_not_persist(self) -> None:
        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HITL,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=None,
                display=lambda _: None,
            )
        self.assertFalse(result)
        mock_write.assert_not_called()


class HotlApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_hotl_approved_persists_skill(self) -> None:
        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HOTL,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=lambda _: True,
                display=lambda _: None,
            )
        self.assertTrue(result)
        mock_write.assert_called_once()

    def test_hotl_declined_does_not_persist(self) -> None:
        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HOTL,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=lambda _: False,
                display=lambda _: None,
            )
        self.assertFalse(result)
        mock_write.assert_not_called()


class HootlwoApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_hootlwo_auto_persists_without_approval_callback(self) -> None:
        displayed: list[str] = []

        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HOOTLWO,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=None,
                display=displayed.append,
            )
        self.assertTrue(result)
        mock_write.assert_called_once_with(
            self.config_dir, slug=_SLUG, title=_TITLE, summary=_SUMMARY
        )
        self.assertTrue(any("Skill saved" in line for line in displayed))

    def test_hootlwo_auto_persists_ignoring_false_approve_callback(self) -> None:
        """Even if approve would return False, HOOTLWO auto-persists."""
        with patch("duckln.main.write_skill_memory_state") as mock_write:
            result = _offer_skill_approval(
                ControlMode.HOOTLWO,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=lambda _: False,
                display=lambda _: None,
            )
        self.assertTrue(result)
        mock_write.assert_called_once()

    def test_hootlwo_skill_saved_message_contains_title(self) -> None:
        displayed: list[str] = []

        with patch("duckln.main.write_skill_memory_state"):
            _offer_skill_approval(
                ControlMode.HOOTLWO,
                slug=_SLUG,
                title=_TITLE,
                summary=_SUMMARY,
                config_dir=self.config_dir,
                approve=None,
                display=displayed.append,
            )
        self.assertTrue(any(_TITLE in line for line in displayed))


if __name__ == "__main__":
    unittest.main()
