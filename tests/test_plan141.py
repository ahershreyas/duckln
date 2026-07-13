"""Plan 141 — two desktop-stream/UX root causes:
F1 a window manager (fluxbox) so a streamed GUI app is VISIBLE (not a black noVNC screen) —
   universal for ANY desktop toolkit, in the base stream stack.
F2 the reasoning logical-thinking.md opens from inside the TUI (Textual eats terminal
   OSC-8 clicks) — `/reasoning` command + a clicked file:// link both open it.
"""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb


class WindowManagerUniversal(unittest.TestCase):
    def test_fluxbox_in_base_for_all_flavors(self):
        for flavor in ("tauri", "electron"):
            self.assertIn("fluxbox", rb._desktop_stream_packages(flavor), flavor)

    def test_wm_started_after_xvfb_idempotent(self):
        steps = rb._desktop_stream_steps(flavor="tauri", present_tools=())
        titles = [t for t, _, _ in steps]
        cmds = [c for _, c, _ in steps]
        self.assertTrue(any("window manager" in t.lower() for t in titles))
        xvfb_i = next(i for i, t in enumerate(titles) if "Xvfb" in t)
        wm_i = next(i for i, t in enumerate(titles) if "window manager" in t.lower())
        self.assertGreater(wm_i, xvfb_i)  # WM starts AFTER the display exists
        wm_cmd = cmds[wm_i]
        self.assertIn("fluxbox", wm_cmd)
        self.assertIn("pgrep -f", wm_cmd)            # idempotent
        self.assertIn(f"DISPLAY={rb._VNC_DISPLAY}", wm_cmd)

    def test_electron_also_gets_a_wm_step(self):
        steps = rb._desktop_stream_steps(flavor="electron", present_tools=())
        self.assertTrue(any("window manager" in t.lower() for t, _, _ in steps))


class ReasoningLinkOpens(unittest.TestCase):
    def test_link_renderable_carries_file_uri(self):
        try:
            from duckln.textual_ui import _reasoning_link_renderable
        except Exception:
            self.skipTest("textual not available")
        line = "🧠 Reasoning captured → [logical-thinking.md](file:///Users/x/.duckln/memory/logical-thinking.md) (click to open)"
        t = _reasoning_link_renderable(line)
        self.assertIsNotNone(t)
        import io
        from rich.console import Console
        c = Console(file=io.StringIO(), force_terminal=True)
        c.print(t)
        out = c.file.getvalue()
        # the OSC-8 link (which on_click reads as event.style.link) targets the file
        self.assertIn("file:///Users/x", out)

    def test_reasoning_command_descriptor_present(self):
        from duckln.main import get_slash_command_descriptors
        names = {d.command for d in get_slash_command_descriptors()}
        self.assertIn("/reasoning", names)


if __name__ == "__main__":
    unittest.main()
