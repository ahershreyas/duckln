"""Tests: runtime prerequisite install dedup short-circuits on identical repeat failures (Plan 55)."""

from __future__ import annotations

import unittest

from duckln.main import (
    _RUNTIME_PREREQUISITE_FAILED,
    _clear_runtime_install_failure,
    _record_runtime_install_failure,
    _runtime_install_recently_failed_same,
)
from duckln.repair_intake import fingerprint_stderr


class RuntimeDedupTest(unittest.TestCase):
    def setUp(self) -> None:
        _RUNTIME_PREREQUISITE_FAILED.clear()

    def tearDown(self) -> None:
        _RUNTIME_PREREQUISITE_FAILED.clear()

    def test_first_failure_records_fingerprint(self) -> None:
        stderr = "E: Unable to locate package nodejs\n"
        _record_runtime_install_failure(
            repo_url="https://example.com/foo",
            install_command="sudo apt install -y nodejs",
            stderr=stderr,
        )
        fp = fingerprint_stderr(stderr)
        self.assertTrue(
            _runtime_install_recently_failed_same(
                repo_url="https://example.com/foo",
                install_command="sudo apt install -y nodejs",
                current_stderr_fp=fp,
            )
        )

    def test_different_stderr_does_not_short_circuit(self) -> None:
        _record_runtime_install_failure(
            repo_url="https://example.com/foo",
            install_command="sudo apt install -y nodejs",
            stderr="E: Unable to locate package nodejs\n",
        )
        other_fp = fingerprint_stderr("Could not resolve dependency tree\n")
        self.assertFalse(
            _runtime_install_recently_failed_same(
                repo_url="https://example.com/foo",
                install_command="sudo apt install -y nodejs",
                current_stderr_fp=other_fp,
            )
        )

    def test_clear_removes_dedup_entry(self) -> None:
        _record_runtime_install_failure(
            repo_url="https://example.com/foo",
            install_command="sudo apt install -y nodejs",
            stderr="E: nope\n",
        )
        _clear_runtime_install_failure(
            repo_url="https://example.com/foo",
            install_command="sudo apt install -y nodejs",
        )
        self.assertFalse(
            _runtime_install_recently_failed_same(
                repo_url="https://example.com/foo",
                install_command="sudo apt install -y nodejs",
                current_stderr_fp=fingerprint_stderr("E: nope\n"),
            )
        )

    def test_empty_fingerprint_does_not_match_anything(self) -> None:
        _record_runtime_install_failure(
            repo_url="https://example.com/foo",
            install_command="sudo apt install -y nodejs",
            stderr="",
        )
        self.assertFalse(
            _runtime_install_recently_failed_same(
                repo_url="https://example.com/foo",
                install_command="sudo apt install -y nodejs",
                current_stderr_fp="",
            )
        )


if __name__ == "__main__":
    unittest.main()
