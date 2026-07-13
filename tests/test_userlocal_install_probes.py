"""Tests: user-local installer probes (rustup/uv/poetry) survive un-updated PATH (Plan 60 Bug E)."""

from __future__ import annotations

import unittest

from duckln.readme_skill import _KNOWN_PREREQUISITES


class UserLocalProbeFallbackTest(unittest.TestCase):
    def test_rust_probe_falls_back_to_home_cargo_bin(self) -> None:
        probe = _KNOWN_PREREQUISITES["rust"][0]
        self.assertIn("$HOME/.cargo/bin/cargo", probe)
        self.assertIn("cargo --version", probe)
        # Probe must try PATH first.
        self.assertIn("command -v cargo", probe)

    def test_cargo_probe_falls_back_to_home_cargo_bin(self) -> None:
        probe = _KNOWN_PREREQUISITES["cargo"][0]
        self.assertIn("$HOME/.cargo/bin/cargo", probe)

    def test_uv_probe_falls_back_to_home_local_bin(self) -> None:
        probe = _KNOWN_PREREQUISITES["uv"][0]
        self.assertIn("$HOME/.local/bin/uv", probe)
        self.assertIn("command -v uv", probe)

    def test_poetry_probe_falls_back_to_home_local_bin(self) -> None:
        probe = _KNOWN_PREREQUISITES["poetry"][0]
        self.assertIn("$HOME/.local/bin/poetry", probe)
        self.assertIn("command -v poetry", probe)

    def test_apt_only_probes_unchanged(self) -> None:
        """Tools installed via apt/brew (not via curl-sh) land on PATH naturally —
        their probes MUST NOT include $HOME fallback paths."""
        for name in ("node", "npm", "python", "git", "make", "cmake", "docker", "go"):
            probe = _KNOWN_PREREQUISITES[name][0]
            self.assertNotIn(
                "$HOME/",
                probe,
                f"Probe for '{name}' should not include $HOME fallback (apt/brew handles PATH).",
            )


if __name__ == "__main__":
    unittest.main()
