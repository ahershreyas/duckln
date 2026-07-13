"""Plan 122 — venv-aware missing-module fix, 429/503 backoff, and backend build/dev
requirements in the polyglot setup."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from duckln.ai_client import (
    AnthropicAdapter,
    OpenAIAdapter,
    ProviderRequestError,
    _backoff_seconds,
    _retry_after_seconds,
)
from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from duckln.repo_bringup import _SECONDARY_PY_SETUP_CMD


# --- Fix 1: venv-aware missing-module --------------------------------------


class VenvAwareMissingModule(unittest.TestCase):
    def test_python_dash_m_unquoted_form_in_venv(self):
        # The `python -m PyInstaller` error is UNQUOTED and names a venv python.
        f = match_deterministic_fix(
            stderr="/home/ubuntu/.duckln/projects/JustHireMe/backend/.venv/bin/python: No module named PyInstaller",
            execution_target="vm",
        )
        self.assertEqual(f.category, ErrorCategory.MISSING_MODULE)
        self.assertEqual(
            f.fix_command,
            '"/home/ubuntu/.duckln/projects/JustHireMe/backend/.venv/bin/pip" install pyinstaller',
        )
        self.assertIn("show pyinstaller", f.verification)  # pip show, not import

    def test_quoted_import_form_without_venv_uses_system_pip(self):
        f = match_deterministic_fix(stderr="ModuleNotFoundError: No module named 'flask'", execution_target="local")
        self.assertEqual(f.category, ErrorCategory.MISSING_MODULE)
        self.assertEqual(f.fix_command, "python3 -m pip install flask")
        self.assertEqual(f.verification, "python3 -m pip show flask")

    def test_dotted_module_takes_top_package(self):
        f = match_deterministic_fix(stderr="No module named 'google.protobuf'", execution_target="local")
        self.assertEqual(f.fix_command, "python3 -m pip install google")


# --- Fix 2: 429/503 backoff -------------------------------------------------


class _SeqClient:
    """Returns queued responses in order for post(); get() unused here."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls = 0

    def get(self, url, *, headers, timeout):  # pragma: no cover - not used
        raise AssertionError("get not expected")

    def post(self, url, *, headers, json, timeout):
        self.post_calls += 1
        return self._responses.pop(0)


class _Resp:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


_OK_PAYLOAD = {"choices": [{"message": {"content": "hello"}}]}


class RateLimitBackoff(unittest.TestCase):
    def test_backoff_is_bounded_and_exponential(self):
        self.assertEqual([_backoff_seconds(i) for i in range(5)], [0.5, 1.0, 2.0, 4.0, 5.0])

    def test_retry_after_header_honored_and_capped(self):
        self.assertEqual(_retry_after_seconds(_Resp(429, headers={"Retry-After": "3"})), 3.0)
        self.assertEqual(_retry_after_seconds(_Resp(429, headers={"Retry-After": "999"})), 5.0)
        self.assertEqual(_retry_after_seconds(_Resp(200)), 0.0)

    def test_retries_then_succeeds_on_429(self):
        client = _SeqClient([_Resp(429, headers={"Retry-After": "0"}), _Resp(200, _OK_PAYLOAD)])
        with patch("duckln.ai_client.time.sleep"):
            text = OpenAIAdapter().generate_reply(
                "k", model_id="gpt-5", system_prompt="s", user_message="u", client=client,
            )
        self.assertEqual(text, "hello")
        self.assertEqual(client.post_calls, 2)

    def test_persistent_429_raises_clear_message(self):
        client = _SeqClient([_Resp(429) for _ in range(4)])
        with patch("duckln.ai_client.time.sleep"):
            with self.assertRaises(ProviderRequestError) as ctx:
                OpenAIAdapter().generate_reply(
                    "k", model_id="gpt-5", system_prompt="s", user_message="u", client=client,
                )
        self.assertIn("rate-limited or over quota", str(ctx.exception))
        self.assertEqual(client.post_calls, 4)  # bounded attempts


# --- Fix 3: backend build/dev requirements ----------------------------------


class BackendBuildRequirements(unittest.TestCase):
    def test_setup_cmd_installs_dev_and_build_requirements(self):
        for name in ("requirements-dev.txt", "requirements-build.txt", "dev-requirements.txt", "build-requirements.txt"):
            self.assertIn(name, _SECONDARY_PY_SETUP_CMD)
        # still keyed on the primary venv setup + idempotent guard
        self.assertIn("python3 -m venv .venv", _SECONDARY_PY_SETUP_CMD)
        self.assertIn(".duckln-deps-ok", _SECONDARY_PY_SETUP_CMD)  # Plan 127 completion marker


if __name__ == "__main__":
    unittest.main()
