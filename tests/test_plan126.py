"""Plan 126 — the polyglot-desktop-sidecar archetype must be handled by SIGNALS,
never by repo name. These tests assert the class machinery fires identically for
arbitrary/fabricated repo names — guarding against accidental over-fit to one repo.
The end-to-end VM runs (2 real class repos) are the separate empirical bar."""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb
from duckln.repo_bringup import (
    _detect_app_archetype,
    _secondary_python_setup_steps,
    _tauri_prebuild_steps,
)
from duckln.diagnostics import ErrorCategory, match_deterministic_fix
from state.repo_catalog import RepoCatalogRecord


def _repo(name: str):
    return RepoCatalogRecord(name, f"https://github.com/x/{name}", 0, "", "", "", "")


class DesktopArchetypeIsSignalKeyed(unittest.TestCase):
    def test_tauri_detected_from_signals_not_name(self):
        payload = {"dependencies": {"@tauri-apps/api": "^1"}}
        files = ("package.json", "tauri.conf.json", "src-tauri")
        kind, flavor = _detect_app_archetype(payload, files)
        self.assertEqual((kind, flavor), ("desktop_gui", "tauri"))

    def test_electron_detected_from_signals(self):
        payload = {"dependencies": {"electron": "^30"}, "scripts": {"start": "electron ."}}
        kind, flavor = _detect_app_archetype(payload, ("package.json",))
        self.assertEqual((kind, flavor), ("desktop_gui", "electron"))

    def test_same_signals_different_names_give_identical_result(self):
        # The function takes no repo name; two unrelated repos with the same signals
        # must classify identically (proves name-independence by construction).
        payload = {"dependencies": {"@tauri-apps/api": "^1"}}
        files = ("package.json", "src-tauri")
        a = _detect_app_archetype(payload, files)
        b = _detect_app_archetype(dict(payload), tuple(files))
        self.assertEqual(a, b)
        self.assertEqual(a[0], "desktop_gui")


class TauriPrebuildIsSignalKeyed(unittest.TestCase):
    def test_before_build_command_runs_for_any_repo_name(self):
        conf = '{"build": {"beforeBuildCommand": "npm run build:sidecar"}}'
        orig = rb._read_repo_text_file
        rb._read_repo_text_file = lambda *a, **k: conf
        try:
            for name in ("alpha-desktop", "zzz-random-9999"):
                steps = _tauri_prebuild_steps(
                    config_dir=rb.Path("/tmp"), repo=_repo(name), detected_files=("tauri.conf.json",),
                )
                self.assertTrue(steps, f"no prebuild for {name}")
                self.assertIn("build:sidecar", steps[0][1])
        finally:
            rb._read_repo_text_file = orig


class SecondaryBackendIsSignalKeyed(unittest.TestCase):
    def test_keyed_on_node_primary_plus_prebuild_not_name(self):
        # Pure data in → identical out regardless of any repo name.
        self.assertTrue(_secondary_python_setup_steps(("package.json",), has_prebuild=True))
        self.assertEqual(_secondary_python_setup_steps(("package.json",), has_prebuild=False), ())


class SidecarMissingToolIsPathKeyed(unittest.TestCase):
    def test_pyinstaller_missing_installs_into_the_error_named_venv(self):
        # Works for ANY repo — keyed on the venv path in the error, not the repo name.
        for slug in ("foo-app", "bar-9000"):
            err = f"/home/ubuntu/.duckln/projects/{slug}/backend/.venv/bin/python: No module named PyInstaller"
            f = match_deterministic_fix(stderr=err, execution_target="vm")
            self.assertEqual(f.category, ErrorCategory.MISSING_MODULE)
            self.assertIn(f"/{slug}/backend/.venv/bin/pip", f.fix_command)
            self.assertIn("install pyinstaller", f.fix_command)


if __name__ == "__main__":
    unittest.main()
