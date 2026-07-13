from __future__ import annotations

import unittest

from duckln.repo_bringup import (
    _detect_app_archetype,
    _desktop_run_command_from_scripts,
    _run_command_from_scripts,
)


class TestDetectAppArchetype(unittest.TestCase):
    def test_tauri_dep_is_desktop_gui(self) -> None:
        payload = {"dependencies": {"@tauri-apps/api": "^1.5.0"}, "scripts": {"dev": "vite"}}
        self.assertEqual(_detect_app_archetype(payload, ("package.json",)), ("desktop_gui", "tauri"))

    def test_tauri_conf_file_is_desktop_gui(self) -> None:
        payload = {"scripts": {"dev": "vite"}}
        self.assertEqual(
            _detect_app_archetype(payload, ("package.json", "tauri.conf.json")),
            ("desktop_gui", "tauri"),
        )

    def test_src_tauri_dir_is_desktop_gui(self) -> None:
        self.assertEqual(
            _detect_app_archetype({"scripts": {"dev": "vite"}}, ("package.json", "src-tauri")),
            ("desktop_gui", "tauri"),
        )

    def test_electron_dep_is_desktop_gui(self) -> None:
        payload = {"devDependencies": {"electron": "^28"}, "scripts": {"start": "electron ."}}
        self.assertEqual(_detect_app_archetype(payload, ("package.json",)), ("desktop_gui", "electron"))

    def test_vite_web_app_is_web(self) -> None:
        payload = {"dependencies": {"react": "18", "react-dom": "18"}, "devDependencies": {"vite": "5"}, "scripts": {"dev": "vite"}}
        self.assertEqual(_detect_app_archetype(payload, ("package.json",)), ("web", ""))

    def test_bin_entry_is_cli(self) -> None:
        payload = {"bin": {"mytool": "bin/cli.js"}, "dependencies": {"commander": "11"}}
        self.assertEqual(_detect_app_archetype(payload, ("package.json",)), ("cli", ""))

    def test_python_django_readme_is_web(self) -> None:
        kind, flavor = _detect_app_archetype(None, ("manage.py", "requirements.txt"))
        self.assertEqual((kind, flavor), ("web", ""))

    def test_no_signal_is_library(self) -> None:
        self.assertEqual(_detect_app_archetype({"dependencies": {"lodash": "4"}}, ("package.json",)), ("library", ""))


class TestDesktopRunCommand(unittest.TestCase):
    def test_tauri_passthrough_script_gets_dev(self) -> None:
        scripts = {"dev": "vite", "tauri": "tauri"}
        self.assertEqual(_desktop_run_command_from_scripts(scripts, "npm", "tauri"), "npm run tauri dev")

    def test_tauri_dev_script_picked_over_frontend_dev(self) -> None:
        scripts = {"dev": "vite", "tauri:dev": "tauri dev"}
        self.assertEqual(_desktop_run_command_from_scripts(scripts, "npm", "tauri"), "npm run tauri:dev")

    def test_tauri_build_script_skipped(self) -> None:
        scripts = {"dev": "vite", "tauri:build": "tauri build"}
        # No dev script → falls back to the direct binary.
        self.assertEqual(_desktop_run_command_from_scripts(scripts, "npm", "tauri"), "npx tauri dev")

    def test_no_tauri_script_falls_back_to_npx(self) -> None:
        self.assertEqual(_desktop_run_command_from_scripts({"dev": "vite"}, "npm", "tauri"), "npx tauri dev")

    def test_electron_script_picked(self) -> None:
        scripts = {"dev": "vite", "electron:dev": "electron ."}
        self.assertEqual(_desktop_run_command_from_scripts(scripts, "npm", "electron"), "npm run electron:dev")

    def test_pnpm_format(self) -> None:
        scripts = {"dev": "vite", "tauri:dev": "tauri dev"}
        self.assertEqual(_desktop_run_command_from_scripts(scripts, "pnpm", "tauri"), "pnpm tauri:dev")


class TestRunCommandFromScriptsArchetype(unittest.TestCase):
    def test_desktop_archetype_routes_to_desktop_command(self) -> None:
        scripts = {"dev": "vite", "tauri:dev": "tauri dev"}
        self.assertEqual(
            _run_command_from_scripts(scripts, "npm", archetype="desktop_gui", flavor="tauri"),
            "npm run tauri:dev",
        )

    def test_web_archetype_unchanged(self) -> None:
        scripts = {"dev": "vite"}
        self.assertEqual(_run_command_from_scripts(scripts, "npm"), "npm run dev")
        self.assertEqual(
            _run_command_from_scripts(scripts, "npm", archetype="web"), "npm run dev"
        )


if __name__ == "__main__":
    unittest.main()
