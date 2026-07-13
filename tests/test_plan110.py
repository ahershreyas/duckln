from __future__ import annotations

import unittest

from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from duckln.repo_bringup import (
    _amendment_fix_is_plausible,
    _is_heavy_build,
    composite_target_label,
)


# --- Fix 1+3: the REAL error now produces the right deterministic fix ----------


class TestRealErrorAttribution(unittest.TestCase):
    SCREENSHOT_LOG = (
        "Compiling tauri-plugin-opener v2.5.4\n"
        "error: could not compile `gtk` (lib)\n"
        "Caused by:\n"
        "  process did not exit successfully: `rustc --crate-name gtk ...` (signal: 9, SIGKILL: kill)\n"
        "warning: build failed, waiting for other jobs to finish..."
    )

    def test_screenshot_oom_yields_swap_fix_not_timeout(self):
        fix = match_deterministic_fix(stderr=self.SCREENSHOT_LOG, command="npm run tauri dev", execution_target="vm")
        self.assertIsNotNone(fix)
        self.assertEqual(fix.category, ErrorCategory.OUT_OF_MEMORY)
        self.assertIn("swap", fix.fix_command.lower())
        self.assertNotIn("--timeout", fix.fix_command)

    def test_cc1plus_oom_also_caught(self):
        fix = match_deterministic_fix(stderr="cc1plus: out of memory allocating 65536 bytes", execution_target="vm")
        self.assertEqual(fix.category, ErrorCategory.OUT_OF_MEMORY)


# --- Fix 2: heavy-build detection (repo-agnostic) -----------------------------


class TestHeavyBuildDetection(unittest.TestCase):
    def test_heavy(self):
        for c in ("npm run tauri dev", "cargo build", "cmake --build .", "gradle assemble", "go build ./...", "npm run build && webpack"):
            self.assertTrue(_is_heavy_build(c), c)

    def test_light(self):
        for c in ("npm run dev", "vite", "python app.py", "npm start", "flask run"):
            self.assertFalse(_is_heavy_build(c), c)


# --- Fix 4: composite location-resource label ---------------------------------


class TestCompositeLabel(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(composite_target_label("vm", "duckln-vm"), "local-duckln-vm")
        self.assertEqual(composite_target_label("container", "box"), "local-box")
        self.assertEqual(composite_target_label("aws", "web1"), "aws-web1")
        self.assertEqual(composite_target_label("gcp", "n1"), "gcp-n1")
        self.assertEqual(composite_target_label("local", None), "local")


# --- Fix 7: weak-model guardrail ----------------------------------------------


class TestPlausibilityGuard(unittest.TestCase):
    def test_rejects_timeout_for_crash(self):
        self.assertFalse(_amendment_fix_is_plausible("(signal: 9, SIGKILL: kill)", "run --timeout=60s"))
        self.assertFalse(_amendment_fix_is_plausible("error: could not compile gtk (lib)", "increase the timeout"))

    def test_allows_timeout_for_real_timeout(self):
        self.assertTrue(_amendment_fix_is_plausible("operation timed out after 30s", "--timeout 120"))

    def test_allows_normal_fixes(self):
        self.assertTrue(_amendment_fix_is_plausible("ModuleNotFoundError: no module named requests", "pip install requests"))


if __name__ == "__main__":
    unittest.main()
