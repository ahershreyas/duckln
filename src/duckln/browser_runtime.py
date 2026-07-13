"""Managed pywebview browser runtime for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import util as importlib_util
import json
from pathlib import Path
import subprocess
import sys
import threading
from typing import Callable
from urllib.parse import urlparse


_LOCAL_BROWSER_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class BrowserRuntimeAvailability:
    """Whether Duckln can open the managed browser window."""

    available: bool
    error_message: str | None = None


@dataclass(frozen=True)
class BrowserRuntimeEvent:
    """Structured event emitted by the managed browser subprocess."""

    kind: str
    message: str
    url: str | None = None
    detail: str | None = None


def managed_browser_runtime_available() -> BrowserRuntimeAvailability:
    """Return whether pywebview is importable in the current environment."""

    if importlib_util.find_spec("webview") is None:
        return BrowserRuntimeAvailability(
            available=False,
            error_message="Duckln could not import pywebview for the managed browser window.",
        )
    return BrowserRuntimeAvailability(available=True, error_message=None)


def is_safe_managed_browser_url(url: str) -> bool:
    """Only allow Duckln-managed browser windows to open localhost endpoints."""

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname or parsed.hostname.lower() not in _LOCAL_BROWSER_HOSTS:
        return False
    if parsed.username or parsed.password:
        return False
    return True


def render_browser_runtime_event(event: BrowserRuntimeEvent) -> tuple[str, str]:
    """Convert a browser event into a transcript message and role."""

    if event.kind == "browser_console_error":
        return f"Duckln noticed a browser console error: {event.message}", "proactive"
    if event.kind == "browser_page_error":
        return f"Duckln noticed a page error in the managed browser window: {event.message}", "proactive"
    if event.kind == "browser_load_failed":
        detail = f" {event.detail}" if event.detail else ""
        return f"Duckln could not reach the managed browser target yet: {event.message}.{detail}".strip(), "proactive"
    if event.kind == "browser_closed":
        return "Duckln noticed that the managed browser window was closed.", "proactive"
    if event.kind == "browser_runner_error":
        detail = f" {event.detail}" if event.detail else ""
        return f"Duckln could not keep the managed browser window running.{detail}".strip(), "proactive"
    if event.kind == "browser_loaded":
        return f"Duckln loaded the repo UI in the managed browser window at {event.url or event.message}.", "proactive"
    if event.kind == "browser_navigation":
        return f"Duckln noticed the managed browser window navigated to {event.url or event.message}.", "proactive"
    return event.message, "proactive"


class ManagedBrowserWindow:
    """Launch and monitor a single Duckln-managed pywebview browser window."""

    def __init__(self, *, emit: Callable[[str, str], None]) -> None:
        self._emit = emit
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_requested = False
        self._current_url: str | None = None

    def open(self, *, url: str, title_hint: str | None = None) -> bool:
        availability = managed_browser_runtime_available()
        if not availability.available:
            return False
        normalized_url = url.strip()
        if not is_safe_managed_browser_url(normalized_url):
            return False
        self.stop()
        command = [
            sys.executable,
            "-m",
            "duckln.browser_runner",
            "--url",
            normalized_url,
            "--title",
            title_hint or "Duckln Browser",
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                cwd=str(Path.cwd()),
                bufsize=1,
            )
        except Exception as exc:
            self._emit(f"Duckln could not start the managed browser window. {exc}", "proactive")
            self._process = None
            return False
        self._stop_requested = False
        self._current_url = normalized_url
        self._reader_thread = threading.Thread(target=self._read_events, daemon=True)
        self._reader_thread.start()
        return True

    def stop(self) -> None:
        self._stop_requested = True
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        self._reader_thread = None
        self._process = None
        self._current_url = None

    def _read_events(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            event = _parse_browser_runtime_event(line)
            if event is None:
                continue
            message, role = render_browser_runtime_event(event)
            self._emit(message, role)
        return_code = process.wait()
        if return_code != 0 and not self._stop_requested:
            self._emit(
                f"Duckln noticed the managed browser window exited unexpectedly with status {return_code}.",
                "proactive",
            )


def _parse_browser_runtime_event(line: str) -> BrowserRuntimeEvent | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    kind = str(payload.get("kind") or "").strip()
    if not kind:
        return None
    return BrowserRuntimeEvent(
        kind=kind,
        message=str(payload.get("message") or kind).strip(),
        url=str(payload.get("url")).strip() if payload.get("url") else None,
        detail=str(payload.get("detail")).strip() if payload.get("detail") else None,
    )
