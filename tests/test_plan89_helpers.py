from __future__ import annotations

import unittest
from types import SimpleNamespace

from duckln.repo_bringup import (
    _idempotent_guard,
    _low_memory_swap_command,
    _needs_low_memory_guard,
    _target_is_headless,
)


class TestLowMemoryGuard(unittest.TestCase):
    def test_low_ram_no_swap_needs_guard(self) -> None:
        self.assertTrue(_needs_low_memory_guard(mem_kb=2 * 1024 * 1024, has_swap=False))

    def test_has_swap_no_guard(self) -> None:
        self.assertFalse(_needs_low_memory_guard(mem_kb=2 * 1024 * 1024, has_swap=True))

    def test_plenty_of_ram_no_guard(self) -> None:
        self.assertFalse(_needs_low_memory_guard(mem_kb=16 * 1024 * 1024, has_swap=False))

    def test_unknown_ram_no_guard(self) -> None:
        self.assertFalse(_needs_low_memory_guard(mem_kb=0, has_swap=False))

    def test_swap_command_adds_swap_and_caps_jobs(self) -> None:
        cmd = _low_memory_swap_command()
        self.assertIn("swapon", cmd)
        self.assertIn("/swapfile", cmd)
        self.assertIn("jobs = 1", cmd)


class TestIdempotentGuard(unittest.TestCase):
    def test_npm_ci_guarded(self) -> None:
        # Plan 127: completion-aware — skip only on a completion marker, else clean+redo.
        g = _idempotent_guard("npm ci")
        self.assertIn("node_modules/.package-lock.json", g)
        self.assertIn("rm -rf node_modules", g)
        self.assertIn("npm ci", g)

    def test_cp_env_guarded(self) -> None:
        self.assertEqual(_idempotent_guard("cp .env.example .env"), "[ -f .env ] || cp .env.example .env")

    def test_apt_git_guarded(self) -> None:
        self.assertEqual(
            _idempotent_guard("sudo apt install -y git"),
            "command -v git >/dev/null 2>&1 || sudo apt install -y git",
        )

    def test_apt_nodejs_guards_on_node_binary(self) -> None:
        self.assertIn("command -v node", _idempotent_guard("sudo apt-get install -y nodejs"))

    def test_run_command_not_guarded_here(self) -> None:
        # Run commands are guarded out by the caller, but the helper also leaves
        # unrecognized commands untouched.
        self.assertEqual(_idempotent_guard("npm run tauri dev"), "npm run tauri dev")

    def test_already_guarded_unchanged(self) -> None:
        already = "[ -d node_modules ] || npm ci"
        self.assertEqual(_idempotent_guard(already), already)


class _FakeRunner:
    def __init__(self, stdout: str):
        self._stdout = stdout

    def run(self, _cmd, **_kw):
        return SimpleNamespace(stdout=self._stdout, stderr="", exit_code=0, timed_out=False)


class TestHeadlessProbe(unittest.TestCase):
    def test_linux_no_display_is_headless(self) -> None:
        runner = _FakeRunner("DUCKLN_OS:Linux\nDUCKLN_DISP:\n")
        self.assertTrue(
            _target_is_headless(runner, config_dir=None, execution_target="local", vm_name=None)
        )

    def test_linux_with_display_not_headless(self) -> None:
        runner = _FakeRunner("DUCKLN_OS:Linux\nDUCKLN_DISP::0\n")
        self.assertFalse(
            _target_is_headless(runner, config_dir=None, execution_target="local", vm_name=None)
        )

    def test_macos_not_headless(self) -> None:
        runner = _FakeRunner("DUCKLN_OS:Darwin\nDUCKLN_DISP:\n")
        self.assertFalse(
            _target_is_headless(runner, config_dir=None, execution_target="local", vm_name=None)
        )


if __name__ == "__main__":
    unittest.main()
