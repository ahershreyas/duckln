"""Plan 125 — framework-agnostic Mac ML: TensorFlow-on-Apple-Silicon recovery +
the torch-free MLX smoke row. (Live MLX run is opt-in via DUCKLN_INTEGRATION.)"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from tests.integration.matrix import MATRIX


def _mac():
    return patch("duckln.diagnostics.platform.system", return_value="Darwin")


class TensorFlowAppleSilicon(unittest.TestCase):
    def test_plain_tensorflow_maps_to_tensorflow_macos(self):
        err = "Could not find a version that satisfies the requirement tensorflow==2.15 (/app/.venv/bin/python)"
        with _mac():
            f = match_deterministic_fix(stderr=err, execution_target="local")
        self.assertEqual(f.category, ErrorCategory.TF_MACOS_REQUIRED)
        self.assertIn("tensorflow-macos", f.fix_command)
        self.assertIn("tensorflow-metal", f.fix_command)
        self.assertIn("/app/.venv/bin/pip", f.fix_command)

    def test_does_not_fire_on_linux(self):
        with patch("duckln.diagnostics.platform.system", return_value="Linux"):
            f = match_deterministic_fix(
                stderr="Could not find a version that satisfies the requirement tensorflow==2.15",
                execution_target="vm",
            )
        if f is not None:
            self.assertNotEqual(f.category, ErrorCategory.TF_MACOS_REQUIRED)


class MlxSmokeRow(unittest.TestCase):
    def test_mlx_smoke_row_is_synthetic_and_host_safe(self):
        row = next((s for s in MATRIX if s.name == "mlx-smoke"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row.stack, "smoke")
        self.assertEqual(row.url, "")
        self.assertEqual(row.environments, ("local",))
        self.assertIn("mlx", row.smoke_pip)
        self.assertIn("mlx.core", row.smoke_check)
        # torch-free by construction
        self.assertNotIn("torch", " ".join(row.smoke_pip).lower())


if __name__ == "__main__":
    unittest.main()
