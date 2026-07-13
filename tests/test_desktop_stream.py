from __future__ import annotations

import unittest

from duckln.repo_bringup import (
    _VNC_WEB_PORT,
    _desktop_launch_command,
    _desktop_stream_install_command,
    _desktop_stream_packages,
    _desktop_stream_steps,
    _looks_like_desktop_run_command,
    _novnc_url,
    _tauri_toolchain_steps,
)


class TestLooksLikeDesktopRun(unittest.TestCase):
    def test_tauri_dev(self) -> None:
        self.assertEqual(_looks_like_desktop_run_command("npm run tauri dev"), "tauri")
        self.assertEqual(_looks_like_desktop_run_command("npx tauri dev"), "tauri")

    def test_electron(self) -> None:
        self.assertEqual(_looks_like_desktop_run_command("npx electron ."), "electron")
        self.assertEqual(_looks_like_desktop_run_command("npm run electron:dev"), "electron")

    def test_web_dev_is_not_desktop(self) -> None:
        self.assertEqual(_looks_like_desktop_run_command("npm run dev"), "")
        self.assertEqual(_looks_like_desktop_run_command("vite --host 0.0.0.0"), "")


class TestDesktopStreamPackages(unittest.TestCase):
    def test_tauri_includes_full_build_set(self) -> None:
        pkgs = _desktop_stream_packages("tauri")
        self.assertIn("xvfb", pkgs)
        self.assertIn("novnc", pkgs)
        self.assertTrue(any("webkit2gtk" in p for p in pkgs))
        # Plan 88: the full Tauri build prerequisites — without these the Rust
        # backend can't compile and __TAURI_INTERNALS__ stays undefined.
        for required in ("build-essential", "libssl-dev", "patchelf"):
            self.assertIn(required, pkgs)

    def test_electron_includes_nss(self) -> None:
        pkgs = _desktop_stream_packages("electron")
        self.assertIn("libnss3", pkgs)
        self.assertIn("websockify", pkgs)
        # Electron is Node-based — no Rust build deps.
        self.assertNotIn("build-essential", pkgs)

    def test_tauri_install_has_webkit_40_fallback(self) -> None:
        cmd = _desktop_stream_install_command("tauri", present_tools=())
        self.assertIn("libwebkit2gtk-4.1-dev", cmd)
        self.assertIn("libwebkit2gtk-4.0-dev", cmd)  # fallback for older Ubuntu


class TestTauriToolchain(unittest.TestCase):
    def test_installs_rust_when_cargo_absent(self) -> None:
        steps = _tauri_toolchain_steps(present_tools=("node", "npm"))
        joined = "\n".join(f"{t}|{c}" for t, c, _s in steps)
        self.assertIn("rustup", joined)
        self.assertIn("Rust toolchain", joined)
        self.assertTrue(any("tauri" in c.lower() for _t, c, _s in steps))

    def test_skips_rust_when_cargo_present(self) -> None:
        steps = _tauri_toolchain_steps(present_tools=("cargo",))
        self.assertFalse(any("rustup" in c for _t, c, _s in steps))

    def test_stream_steps_include_toolchain_for_tauri(self) -> None:
        steps = _desktop_stream_steps(flavor="tauri", present_tools=("node",))
        joined = "\n".join(c for _t, c, _s in steps)
        self.assertIn("rustup", joined)

    def test_stream_steps_no_rust_for_electron(self) -> None:
        steps = _desktop_stream_steps(flavor="electron", present_tools=("node",))
        joined = "\n".join(c for _t, c, _s in steps)
        self.assertNotIn("rustup", joined)


class TestDesktopLaunchCommand(unittest.TestCase):
    def test_tauri_headless_has_webkit_flags_and_cargo(self) -> None:
        cmd = _desktop_launch_command("npm run tauri dev", "tauri", headless=True)
        self.assertIn("DISPLAY=:99", cmd)
        self.assertIn("WEBKIT_DISABLE_COMPOSITING_MODE=1", cmd)
        self.assertIn("WEBKIT_DISABLE_DMABUF_RENDERER=1", cmd)
        self.assertIn(".cargo/bin", cmd)
        # Plan 89: no `;`/`.` prefix that would break the detached nohup PID capture.
        self.assertNotIn(";", cmd)

    def test_tauri_local_puts_cargo_on_path_no_display(self) -> None:
        cmd = _desktop_launch_command("npm run tauri dev", "tauri", headless=False)
        self.assertIn(".cargo/bin", cmd)
        self.assertNotIn("DISPLAY=:99", cmd)
        self.assertNotIn(";", cmd)

    def test_electron_headless_display_only(self) -> None:
        cmd = _desktop_launch_command("npx electron .", "electron", headless=True)
        self.assertIn("DISPLAY=:99", cmd)
        self.assertNotIn(".cargo", cmd)


class TestDesktopStreamInstall(unittest.TestCase):
    def test_install_when_missing(self) -> None:
        cmd = _desktop_stream_install_command("electron", present_tools=())
        self.assertIsNotNone(cmd)
        self.assertIn("apt-get install", cmd)
        self.assertIn("xvfb", cmd)

    def test_idempotent_skip_when_all_present(self) -> None:
        present = _desktop_stream_packages("electron")
        self.assertIsNone(_desktop_stream_install_command("electron", present_tools=present))


class TestDesktopStreamSteps(unittest.TestCase):
    def test_chain_present(self) -> None:
        steps = _desktop_stream_steps(flavor="tauri", present_tools=())
        titles = [t for t, _c, _s in steps]
        joined = "\n".join(c for _t, c, _s in steps)
        self.assertTrue(any("stack" in t for t in titles))
        self.assertIn("Xvfb :99", joined)
        self.assertIn("x11vnc", joined)
        self.assertIn("websockify", joined)
        self.assertIn(str(_VNC_WEB_PORT), joined)

    def test_idempotent_starts(self) -> None:
        # Start commands must guard with pgrep so re-running is safe.
        steps = _desktop_stream_steps(flavor="electron", present_tools=_desktop_stream_packages("electron"))
        joined = "\n".join(c for _t, c, _s in steps)
        self.assertIn("pgrep", joined)
        # All packages present → no install step.
        self.assertFalse(any("apt-get install" in c for _t, c, _s in steps))


class TestNovncUrl(unittest.TestCase):
    def test_local_url(self) -> None:
        # Plan 139: the URL auto-connects (lands on the app, not noVNC's Connect page).
        url = _novnc_url(execution_target="docker", vm_name=None)
        self.assertEqual(
            url,
            f"http://localhost:{_VNC_WEB_PORT}/vnc.html?autoconnect=true&resize=scale&reconnect=true",
        )


if __name__ == "__main__":
    unittest.main()
