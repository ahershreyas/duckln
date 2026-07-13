"""Tests: apt failure classifier with OS-specific guidance (Plan 61 Fix B)."""

from __future__ import annotations

import unittest

from duckln.repair_intake import classify_apt_failure, apt_failure_guidance


class ClassifyAptFailureTest(unittest.TestCase):
    def test_sudo_password_pattern_detected(self) -> None:
        stderr = "sudo: a password is required\nE: Sub-process /usr/bin/dpkg returned an error"
        self.assertEqual(classify_apt_failure(stderr), "sudo_password")

    def test_sudo_no_tty_pattern_detected(self) -> None:
        self.assertEqual(classify_apt_failure("sudo: no tty present"), "sudo_password")

    def test_network_resolve_pattern_detected(self) -> None:
        stderr = "E: Failed to fetch http://archive.ubuntu.com/...: Could not resolve 'archive.ubuntu.com'"
        self.assertEqual(classify_apt_failure(stderr), "network")

    def test_network_temporary_resolve_pattern_detected(self) -> None:
        self.assertEqual(
            classify_apt_failure("Temporary failure resolving 'archive.ubuntu.com'"),
            "network",
        )

    def test_locale_pattern_detected(self) -> None:
        stderr = "perl: warning: Setting locale failed.\nperl: warning: Falling back to C"
        self.assertEqual(classify_apt_failure(stderr), "locale")

    def test_lock_file_pattern_detected(self) -> None:
        stderr = "E: Could not get lock /var/lib/dpkg/lock-frontend - open (11: Resource temporarily unavailable)"
        self.assertEqual(classify_apt_failure(stderr), "lock_file")

    def test_unrecognized_returns_other(self) -> None:
        self.assertEqual(classify_apt_failure("some random error"), "other")
        self.assertEqual(classify_apt_failure(""), "other")

    def test_case_insensitive_matching(self) -> None:
        self.assertEqual(
            classify_apt_failure("SUDO: A PASSWORD IS REQUIRED"),
            "sudo_password",
        )


class AptFailureGuidanceTest(unittest.TestCase):
    def test_each_category_has_guidance(self) -> None:
        for category in ("sudo_password", "network", "locale", "lock_file"):
            guidance = apt_failure_guidance(category)
            self.assertIsNotNone(guidance, f"Missing guidance for {category}")
            self.assertTrue(len(guidance) > 30, f"Guidance for {category} too short")

    def test_other_returns_none(self) -> None:
        self.assertIsNone(apt_failure_guidance("other"))

    def test_sudo_guidance_mentions_sudo_v(self) -> None:
        guidance = apt_failure_guidance("sudo_password")
        self.assertIn("sudo -v", guidance)

    def test_network_guidance_mentions_proxy(self) -> None:
        guidance = apt_failure_guidance("network")
        self.assertIn("proxy", guidance.lower())


if __name__ == "__main__":
    unittest.main()
