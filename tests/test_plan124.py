"""Plan 124 — macOS / AI-ML deterministic fixes (brew block, torch CPU/MPS wheel,
CUDA-on-Mac honest block, gated-HF token ask) + pyenv fallback on macOS.

Host-independent: we patch platform.system() so the macOS rules are exercised
whatever machine runs the suite."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from duckln.diagnostics import ErrorCategory, match_deterministic_fix


def _mac():
    return patch("duckln.diagnostics.platform.system", return_value="Darwin")


class MacToolchain(unittest.TestCase):
    def test_brew_missing_blocks_with_instruction(self):
        with _mac():
            f = match_deterministic_fix(stderr="/bin/sh: brew: command not found", execution_target="local")
        self.assertEqual(f.category, ErrorCategory.PACKAGE_MANAGER_MISSING)
        self.assertTrue(f.block)
        self.assertIn("Homebrew", f.block_question)

    def test_brew_missing_does_not_fire_on_linux(self):
        # On a vm/linux target the macOS brew rule must not trigger.
        with patch("duckln.diagnostics.platform.system", return_value="Linux"):
            f = match_deterministic_fix(stderr="brew: command not found", execution_target="vm")
        if f is not None:
            self.assertNotEqual(f.category, ErrorCategory.PACKAGE_MANAGER_MISSING)


class MacTorchWheel(unittest.TestCase):
    def test_cuda_pinned_torch_reinstalls_cpu_wheel_into_venv(self):
        err = ("ERROR: Could not find a version that satisfies the requirement torch==2.3.1+cu121\n"
               "  used by /home/u/app/.venv/bin/python")
        with _mac():
            f = match_deterministic_fix(stderr=err, execution_target="local")
        self.assertEqual(f.category, ErrorCategory.TORCH_WHEEL_MISMATCH)
        self.assertIn("/home/u/app/.venv/bin/pip", f.fix_command)
        self.assertIn("torch", f.fix_command)

    def test_unsupported_platform_torch_system_pip_when_no_venv(self):
        with _mac():
            f = match_deterministic_fix(stderr="torch: Unsupported platform darwin arm64", execution_target="local")
        self.assertEqual(f.category, ErrorCategory.TORCH_WHEEL_MISMATCH)
        self.assertTrue(f.fix_command.startswith("python3 -m pip"))

    def test_linux_cuda_pin_does_not_trigger_mac_rule(self):
        with patch("duckln.diagnostics.platform.system", return_value="Linux"):
            f = match_deterministic_fix(
                stderr="Could not find a version that satisfies the requirement torch==2.3.1+cu121",
                execution_target="vm",
            )
        if f is not None:
            self.assertNotEqual(f.category, ErrorCategory.TORCH_WHEEL_MISMATCH)


class MacCudaBlock(unittest.TestCase):
    def test_cuda_required_blocks_honestly_on_mac(self):
        with _mac():
            f = match_deterministic_fix(stderr="AssertionError: Torch not compiled with CUDA enabled", execution_target="local")
        self.assertEqual(f.category, ErrorCategory.GPU_UNAVAILABLE)
        self.assertTrue(f.block)
        self.assertIn("GPU", f.block_question)


class GatedHuggingFace(unittest.TestCase):
    def test_gated_model_asks_for_token(self):
        err = "Cannot access gated repo for https://huggingface.co/meta-llama/Llama-3 — you must be authenticated."
        f = match_deterministic_fix(stderr=err, execution_target="local")
        self.assertEqual(f.category, ErrorCategory.MODEL_AUTH_REQUIRED)
        self.assertTrue(f.block)
        self.assertIn("HF_TOKEN", f.block_question)


class MacPyenvFallback(unittest.TestCase):
    def test_python_version_install_has_pyenv_fallback_on_mac(self):
        from duckln.repo_bringup import _python_version_install_step
        with patch("duckln.repo_bringup.platform.system", return_value="Darwin"):
            step = _python_version_install_step(required="3.11", execution_target="local")
        self.assertIn("brew install python@3.11", step.command)
        self.assertIn("pyenv", step.command)  # falls back to pyenv when brew can't


if __name__ == "__main__":
    unittest.main()
