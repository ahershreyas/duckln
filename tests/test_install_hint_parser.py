"""Tests for parse_install_hint and fingerprint_stderr in repair_intake."""

from __future__ import annotations

import unittest

from duckln.repair_intake import fingerprint_stderr, parse_install_hint


class ParseInstallHintTest(unittest.TestCase):
    def test_apt_hint_canonical_form(self) -> None:
        text = "Command 'node' not found, but can be installed with:\n  sudo apt install nodejs\n"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y nodejs"))

    def test_apt_hint_without_sudo_prefix(self) -> None:
        text = "Command 'go' not found, but can be installed with:\napt install golang-go\n"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y golang-go"))

    def test_apt_hint_multi_package(self) -> None:
        text = "Command 'gcc' not found, but can be installed with:\n  sudo apt install gcc build-essential\n"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y gcc build-essential"))

    def test_apt_try_hint(self) -> None:
        text = "bash: yarn: command not found. Try: sudo apt install yarnpkg"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y yarnpkg"))

    def test_dnf_hint(self) -> None:
        text = "bash: cmake: command not found.\nTry: sudo dnf install cmake"
        result = parse_install_hint(text)
        self.assertEqual(result, ("dnf", "sudo dnf install -y cmake"))

    def test_yum_hint(self) -> None:
        text = "Try: yum install httpd"
        result = parse_install_hint(text)
        self.assertEqual(result, ("yum", "sudo yum install -y httpd"))

    def test_brew_hint(self) -> None:
        text = "bash: jq: command not found. Try: brew install jq"
        result = parse_install_hint(text)
        self.assertEqual(result, ("brew", "brew install jq"))

    def test_semicolon_injection_truncated_to_safe_package(self) -> None:
        """An attempt to chain a destructive command via `;` is dropped — the
        regex character class refuses `;`, so only the package name is captured."""
        text = "can be installed with:\n  sudo apt install nodejs; rm -rf /\n"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y nodejs"))

    def test_backtick_injection_rejected(self) -> None:
        text = "can be installed with:\n  sudo apt install `evil`\n"
        result = parse_install_hint(text)
        # The regex won't capture a backtick, so the line yields no package — None.
        self.assertIsNone(result)

    def test_pipe_injection_truncated_to_safe_package(self) -> None:
        """A pipe-to-shell attempt is dropped — the captured package excludes `|`."""
        text = "can be installed with:\n  sudo apt install nodejs | sh\n"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y nodejs"))

    def test_no_hint_returns_none(self) -> None:
        text = "Something completely unrelated failed."
        result = parse_install_hint(text)
        self.assertIsNone(result)

    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(parse_install_hint(""))
        self.assertIsNone(parse_install_hint("   \n  \n"))

    def test_apt_preferred_over_dnf_when_both_present(self) -> None:
        """When stderr has multiple hints, apt wins (priority order)."""
        text = "can be installed with:\n  sudo apt install nodejs\n  or dnf install nodejs"
        result = parse_install_hint(text)
        self.assertEqual(result, ("apt", "sudo apt install -y nodejs"))


class FingerprintStderrTest(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        self.assertEqual(fingerprint_stderr(""), "")
        self.assertEqual(fingerprint_stderr(None), "")

    def test_strips_shell_prompt_lines(self) -> None:
        text = "ubuntu@duckln-vm:~/path$ node --version\nbash: node: command not found"
        result = fingerprint_stderr(text)
        self.assertNotIn("ubuntu@duckln-vm", result)
        self.assertIn("node: command not found", result)

    def test_strips_duckln_narrative(self) -> None:
        text = (
            "Ducklin classified this blocker as rust dependency failure.\n"
            "Specialist route: rust. Toolchain: cargo.\n"
            "Actual error: ENOENT\n"
        )
        result = fingerprint_stderr(text)
        self.assertNotIn("Specialist route", result)
        self.assertNotIn("Toolchain", result)
        self.assertIn("ENOENT", result)

    def test_truncates_to_max_length(self) -> None:
        text = "x" * 500
        result = fingerprint_stderr(text)
        self.assertEqual(len(result), 200)

    def test_same_inputs_produce_same_fingerprint(self) -> None:
        text = "bash: node: command not found"
        self.assertEqual(fingerprint_stderr(text), fingerprint_stderr(text))

    def test_collapses_whitespace(self) -> None:
        text_a = "bash: node:    command   not   found"
        text_b = "bash: node: command not found"
        self.assertEqual(fingerprint_stderr(text_a), fingerprint_stderr(text_b))


if __name__ == "__main__":
    unittest.main()
