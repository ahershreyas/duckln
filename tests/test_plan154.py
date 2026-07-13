"""Plan 154 — First-class Apple MLX / Metal, native-macOS-only.
F1 detect MLX/Metal frameworks from manifests.
F2 surface "Apple Metal (MLX)" on a local Apple-Silicon target.
F3 framework_install_command installs MLX natively.
F4 warn + recommend `local` when an MLX repo is aimed at a Linux target.
"""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb
from duckln.diagnostics import framework_install_command


class DetectMetalFrameworks(unittest.TestCase):
    def test_detects_mlx_family(self):
        self.assertEqual(rb._detect_metal_frameworks("mlx==0.1\nnumpy\n"), ("mlx",))
        self.assertEqual(rb._detect_metal_frameworks("mlx-lm\n"), ("mlx-lm",))

    def test_detects_metal_builds(self):
        self.assertIn("jax-metal", rb._detect_metal_frameworks("jax-metal\n"))
        self.assertIn("tensorflow-metal", rb._detect_metal_frameworks("tensorflow-metal\n"))

    def test_pyproject(self):
        self.assertIn("mlx", rb._detect_metal_frameworks("", '[project]\ndependencies = ["mlx>=0.1"]\n'))

    def test_mlx_lm_not_shadowed_by_mlx(self):
        # mlx-lm should be reported once, not also bare mlx.
        got = rb._detect_metal_frameworks("mlx-lm\n")
        self.assertEqual(got, ("mlx-lm",))

    def test_plain_repo_none(self):
        self.assertEqual(rb._detect_metal_frameworks("flask\nrequests\n"), ())


class MetalAcceleratorLabel(unittest.TestCase):
    def test_mlx_named(self):
        self.assertIn("MLX", rb._metal_accelerator_label(("mlx",)))
        self.assertIn("Metal", rb._metal_accelerator_label(("mlx-lm",)))

    def test_metal_build_without_mlx(self):
        self.assertIn("Apple Metal", rb._metal_accelerator_label(("jax-metal",)))

    def test_empty_falls_back_to_mps(self):
        self.assertEqual(rb._metal_accelerator_label(()), "Apple Silicon GPU (MPS)")


class RepoNeedsAppleMetal(unittest.TestCase):
    def test_declared_mlx(self):
        self.assertTrue(rb._repo_needs_apple_metal(requirements_text="mlx\n"))
        self.assertTrue(rb._repo_needs_apple_metal(pyproject_text='dependencies = ["mlx-lm"]'))

    def test_readme_statement(self):
        self.assertTrue(rb._repo_needs_apple_metal(readme="This requires Apple Silicon and Metal."))

    def test_plain_repo_false(self):
        self.assertFalse(rb._repo_needs_apple_metal(requirements_text="flask\nrequests\n", readme="a web app"))


class AppleMetalRoutingGuard(unittest.TestCase):
    def test_warns_on_linux_targets(self):
        for tgt in ("vm", "docker", "aws", "gcp"):
            note = rb._apple_metal_routing_note(needs_metal=True, execution_target=tgt)
            self.assertIsNotNone(note)
            self.assertIn("local", note)
            self.assertIn("Metal", note)

    def test_none_on_local(self):
        self.assertIsNone(rb._apple_metal_routing_note(needs_metal=True, execution_target="local"))

    def test_none_when_not_metal(self):
        self.assertIsNone(rb._apple_metal_routing_note(needs_metal=False, execution_target="vm"))


class MlxInstallCommand(unittest.TestCase):
    def test_mlx_installs_natively(self):
        self.assertEqual(framework_install_command("mlx", "mps"), "pip install mlx")
        self.assertEqual(framework_install_command("mlx-lm", "mps"), "pip install mlx-lm")

    def test_mlx_accelerator_agnostic(self):
        # MLX is Metal-only; the command is the same regardless of the accelerator arg.
        self.assertEqual(framework_install_command("mlx", "cpu"), "pip install mlx")

    def test_version_pin_preserved(self):
        self.assertEqual(framework_install_command("mlx", "mps", version="0.18.0"), "pip install mlx==0.18.0")

    def test_uses_given_pip(self):
        self.assertTrue(framework_install_command("mlx", "mps", pip="/x/.venv/bin/python -m pip").startswith("/x/.venv/bin/python -m pip install"))


if __name__ == "__main__":
    unittest.main()
