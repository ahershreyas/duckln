"""Tests for Duckln's managed pywebview browser runtime."""

from __future__ import annotations

import io
import json
import time
import unittest
from unittest.mock import patch

from duckln.browser_runtime import (
    BrowserRuntimeEvent,
    ManagedBrowserWindow,
    is_safe_managed_browser_url,
    managed_browser_runtime_available,
    render_browser_runtime_event,
)


class _FakeProcess:
    def __init__(self, lines: list[str] | None = None, return_code: int = 0) -> None:
        self.stdout = io.StringIO("\n".join(lines or []) + ("\n" if lines else ""))
        self._return_code = return_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._return_code

    def wait(self, timeout=None):
        return self._return_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class BrowserRuntimeTest(unittest.TestCase):
    def test_managed_browser_runtime_reports_missing_dependency_cleanly(self) -> None:
        with patch("duckln.browser_runtime.importlib_util.find_spec", return_value=None):
            availability = managed_browser_runtime_available()

        self.assertFalse(availability.available)
        self.assertIn("pywebview", availability.error_message or "")

    def test_managed_browser_url_policy_only_allows_localhost(self) -> None:
        self.assertTrue(is_safe_managed_browser_url("http://localhost:3000"))
        self.assertTrue(is_safe_managed_browser_url("https://127.0.0.1:8443"))
        self.assertFalse(is_safe_managed_browser_url("https://example.com"))
        self.assertFalse(is_safe_managed_browser_url("file:///tmp/index.html"))

    def test_render_browser_runtime_event_marks_errors_as_proactive(self) -> None:
        message, role = render_browser_runtime_event(
            BrowserRuntimeEvent(kind="browser_console_error", message="TypeError: boom")
        )

        self.assertEqual("proactive", role)
        self.assertIn("console error", message.lower())

    def test_managed_browser_window_refuses_remote_urls(self) -> None:
        emitted: list[tuple[str, str]] = []
        manager = ManagedBrowserWindow(emit=lambda message, role: emitted.append((message, role)))

        with patch("duckln.browser_runtime.importlib_util.find_spec", return_value=object()):
            opened = manager.open(url="https://example.com", title_hint="Remote")

        self.assertFalse(opened)
        self.assertEqual([], emitted)

    def test_managed_browser_window_streams_browser_events(self) -> None:
        emitted: list[tuple[str, str]] = []
        process = _FakeProcess(
            lines=[
                json.dumps({"kind": "browser_loaded", "message": "loaded", "url": "http://localhost:3000"}),
                json.dumps({"kind": "browser_console_error", "message": "TypeError: boom"}),
            ],
            return_code=0,
        )
        manager = ManagedBrowserWindow(emit=lambda message, role: emitted.append((message, role)))

        with (
            patch("duckln.browser_runtime.importlib_util.find_spec", return_value=object()),
            patch("duckln.browser_runtime.subprocess.Popen", return_value=process),
        ):
            opened = manager.open(url="http://localhost:3000", title_hint="App")
            time.sleep(0.05)
            manager.stop()

        self.assertTrue(opened)
        self.assertTrue(any("managed browser window" in message.lower() for message, _ in emitted))
        self.assertTrue(any("console error" in message.lower() for message, _ in emitted))


if __name__ == "__main__":
    unittest.main()
