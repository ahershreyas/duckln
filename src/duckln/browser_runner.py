"""Subprocess entrypoint for Duckln's managed pywebview browser window."""

from __future__ import annotations

import argparse
import json
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import webview


_MONITOR_INSTALL_JS = r"""
(function () {
  if (window.__ducklnMonitorInstalled) {
    return "already-installed";
  }
  window.__ducklnMonitorInstalled = true;
  window.__ducklnBrowserEvents = window.__ducklnBrowserEvents || [];
  const queue = window.__ducklnBrowserEvents;
  const push = function (kind, message, detail) {
    try {
      queue.push({
        kind: kind,
        message: String(message || ""),
        detail: detail == null ? null : String(detail),
        url: String(window.location.href || "")
      });
    } catch (error) {
      // Keep the browser running even if monitoring fails.
    }
  };

  const originalConsoleError = console.error ? console.error.bind(console) : null;
  console.error = function () {
    const message = Array.from(arguments).map(function (item) {
      if (item instanceof Error) {
        return item.stack || item.message || String(item);
      }
      if (typeof item === "object") {
        try {
          return JSON.stringify(item);
        } catch (error) {
          return String(item);
        }
      }
      return String(item);
    }).join(" ");
    push("browser_console_error", message, null);
    if (originalConsoleError) {
      return originalConsoleError.apply(console, arguments);
    }
  };

  window.addEventListener("error", function (event) {
    push(
      "browser_page_error",
      event.message || "Unhandled page error",
      [event.filename || "", event.lineno || 0, event.colno || 0].join(":")
    );
  });

  window.addEventListener("unhandledrejection", function (event) {
    const reason = event.reason;
    const message = reason && reason.message ? reason.message : String(reason);
    push("browser_page_error", message || "Unhandled promise rejection", "unhandledrejection");
  });

  push("browser_loaded", "Duckln browser monitor attached.", null);
  return "installed";
})();
"""

_MONITOR_POLL_JS = r"""
(function () {
  const queue = window.__ducklnBrowserEvents || [];
  const items = queue.splice(0, queue.length);
  return JSON.stringify(items);
})();
"""


def _emit(kind: str, message: str, **extra: Any) -> None:
    payload = {"kind": kind, "message": message, **extra}
    print(json.dumps(payload), flush=True)


def _localhost_label(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port}"


def _origin_label(url: str) -> str:
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname or 'localhost'}{port}"


def _same_origin(left: str, right: str) -> bool:
    a = urlparse(left)
    b = urlparse(right)
    return (
        a.scheme == b.scheme
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and a.port == b.port
    )


def _configure_security() -> None:
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False


def _probe_target(url: str) -> None:
    try:
        response = httpx.get(url, follow_redirects=True, timeout=3.0)
    except Exception as exc:
        _emit(
            "browser_load_failed",
            f"Duckln could not reach {_localhost_label(url)} from the managed browser window yet",
            detail=str(exc),
            url=url,
        )
        return
    if response.is_success:
        return
    _emit(
        "browser_load_failed",
        f"Duckln reached {_localhost_label(url)} but the app returned HTTP {response.status_code}",
        detail=response.text[:200].strip() or None,
        url=str(response.url),
    )


def _monitor_window(window, url: str, stop_event: threading.Event) -> None:
    _probe_target(url)
    last_url = None
    monitor_ready = False
    monitor_failure_reported = False
    while not stop_event.is_set():
        try:
            current_url = window.get_current_url()
        except Exception:
            current_url = None
        if current_url and current_url != last_url:
            if not _same_origin(current_url, url):
                _emit(
                    "browser_runner_error",
                    f"Duckln blocked the managed browser window from leaving {_origin_label(url)}.",
                    detail=f"Attempted navigation: {current_url}",
                    url=current_url,
                )
                stop_event.set()
                try:
                    window.destroy()
                except Exception:
                    pass
                return
            _emit("browser_navigation", f"Duckln browser is at {current_url}", url=current_url)
            last_url = current_url
        if monitor_ready:
            try:
                raw_events = window.evaluate_js(_MONITOR_POLL_JS)
                events = json.loads(raw_events) if raw_events else []
                for event in events:
                    kind = str(event.get("kind") or "").strip()
                    if not kind or kind == "browser_loaded":
                        continue
                    _emit(
                        kind,
                        str(event.get("message") or kind),
                        detail=str(event.get("detail")).strip() if event.get("detail") else None,
                        url=str(event.get("url")).strip() if event.get("url") else current_url,
                    )
            except Exception as exc:
                if not monitor_failure_reported:
                    _emit(
                        "browser_runner_error",
                        "Duckln could not poll browser-side diagnostics from the managed browser window.",
                        detail=str(exc),
                        url=current_url or url,
                    )
                    monitor_failure_reported = True
        time.sleep(0.75)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Duckln managed pywebview browser window")
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", default="Duckln Browser")
    args = parser.parse_args(argv)

    stop_event = threading.Event()
    _configure_security()
    window = webview.create_window(
        args.title,
        args.url,
        js_api=None,
        text_select=True,
    )

    monitor_ready = {"value": False}

    def on_loaded() -> None:
        try:
            window.run_js(_MONITOR_INSTALL_JS)
            monitor_ready["value"] = True
            current_url = window.get_current_url() or args.url
            _emit("browser_loaded", f"Duckln loaded {current_url}", url=current_url)
        except Exception as exc:
            _emit(
                "browser_runner_error",
                "Duckln could not install browser-side monitoring in the managed browser window.",
                detail=str(exc),
                url=args.url,
            )

    def on_closed() -> None:
        stop_event.set()
        _emit("browser_closed", "Duckln browser window closed.", url=args.url)

    window.events.loaded += on_loaded
    window.events.closed += on_closed

    def startup(window_ref) -> None:
        monitor_thread = threading.Thread(
            target=_monitor_window,
            args=(window_ref, args.url, stop_event),
            daemon=True,
        )
        monitor_thread.start()
        while not stop_event.is_set():
            if monitor_ready["value"]:
                pass
            time.sleep(0.5)

    try:
        webview.start(startup, window, private_mode=True, debug=False, http_server=False)
    except Exception as exc:
        _emit("browser_runner_error", "Duckln could not start the managed browser window.", detail=str(exc), url=args.url)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
