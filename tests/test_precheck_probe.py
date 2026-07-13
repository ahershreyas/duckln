from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln.repo_bringup import (
    _build_precheck_script,
    _parse_precheck_block,
    _plan_precheck_probe,
    _runtime_major,
)
from state.repo_catalog import RepoCatalogRecord


def _repo():
    return RepoCatalogRecord("demo", "https://github.com/acme/demo", 1, "d", "Custom", "x", "2026-04-01")


_REALISTIC_BLOCK = """ubuntu@vm:~$ bash -lc ...
DUCKLN_PRECHECK_BEGIN
TOOL:node
TOOL:npm
TOOL:git
TOOL:docker
NODEV:v18.19.1
PYV:Python 3.12.3
GOV:
RUSTV:rustc 1.95.0 (59807616e 2026-04-14)
CLONED:yes
DUCKLN_PRECHECK_END
DUCKLN-DONE-08e2f84daf49:0
"""


class TestParseBlock(unittest.TestCase):
    def test_realistic_block_with_marker_noise(self):
        present, cloned, vers = _parse_precheck_block(_REALISTIC_BLOCK, runtime_dir="/r")
        self.assertEqual(present, {"node", "npm", "git", "docker"})
        self.assertNotIn("go", present)  # go was NOT in TOOL: lines
        self.assertTrue(cloned)
        self.assertEqual(vers["node"], "18")   # not "0", not a hash digit
        self.assertEqual(vers["python"], "3.12")
        self.assertEqual(vers["rust"], "1.95")
        self.assertNotIn("go", vers)

    def test_garbled_block_is_empty(self):
        present, cloned, vers = _parse_precheck_block("total garbage\nDUCKLN-DONE-x:1", runtime_dir="/r")
        self.assertEqual(present, set())
        self.assertFalse(cloned)
        self.assertEqual(vers, {})

    def test_runtime_major(self):
        self.assertEqual(_runtime_major("node", "v18.19.1"), "18")
        self.assertEqual(_runtime_major("node", "v22.12.0\nDUCKLN-DONE-abc:0"), "22")
        self.assertIsNone(_runtime_major("node", "DUCKLN-DONE-08e2f84daf49:0"))
        self.assertEqual(_runtime_major("python", "Python 3.12.3"), "3.12")

    def test_script_has_no_single_quotes(self):
        # Must survive `bash -lc '<script>'` wrapping.
        self.assertNotIn("'", _build_precheck_script("/home/u/.duckln/projects/demo"))
        self.assertIn("DUCKLN_PRECHECK_BEGIN", _build_precheck_script("/r"))

    def test_script_detects_desktop_markers(self):
        # Plan 88: the probe reports a VM-side Tauri/Electron app so a remote/private
        # repo is still classified desktop_gui.
        script = _build_precheck_script("/r")
        self.assertIn("DESKTOP:tauri", script)
        self.assertIn("/r/src-tauri", script)
        self.assertIn("DESKTOP:electron", script)

    def test_parse_desktop_flavor_tauri_wins(self):
        block = "DUCKLN_PRECHECK_BEGIN\nTOOL:node\nDESKTOP:tauri\nDESKTOP:electron\nDUCKLN_PRECHECK_END"
        _present, _cloned, vers = _parse_precheck_block(block, runtime_dir="/r")
        self.assertEqual(vers.get("desktop"), "tauri")

    def test_script_reports_ram_and_swap(self):
        # Plan 89: probe reports total RAM + swap so a low-RAM Tauri build gets swap.
        script = _build_precheck_script("/r")
        self.assertIn("MemTotal", script)
        self.assertIn("MEMKB:", script)
        self.assertIn("SWAP:", script)

    def test_parse_ram_and_swap(self):
        block = "DUCKLN_PRECHECK_BEGIN\nMEMKB:2048000\nSWAP:no\nDUCKLN_PRECHECK_END"
        _present, _cloned, vers = _parse_precheck_block(block, runtime_dir="/r")
        self.assertEqual(vers.get("mem_kb"), "2048000")
        self.assertEqual(vers.get("swap"), "no")


class _OneShotRunner:
    """Counts run() calls; returns the realistic block as stdout."""

    def __init__(self):
        self.calls = 0

    def run(self, command, **kwargs):
        self.calls += 1
        return SimpleNamespace(stdout=_REALISTIC_BLOCK, exit_code=0, timed_out=False)


class TestProbeSingleRoundTrip(unittest.TestCase):
    def test_one_round_trip_and_live_thoughts(self):
        import duckln.repo_bringup as rb

        runner = _OneShotRunner()
        orig = rb.ControlledCommandRunner
        rb.ControlledCommandRunner = lambda **kw: runner
        thoughts: list[str] = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                present, cloned, vers = _plan_precheck_probe(
                    repo=_repo(), execution_target="vm", config_dir=Path(tmp),
                    vm_name="duckln-vm", pane_executor=object(),
                    display=lambda _m: None, emit_thought=thoughts.append,
                )
        finally:
            rb.ControlledCommandRunner = orig

        self.assertEqual(runner.calls, 1)  # ONE round-trip, not 16
        self.assertEqual(vers.get("node"), "18")
        self.assertNotIn("go", present)
        self.assertTrue(cloned)
        # Live thoughts: a "probing" line and a findings summary.
        joined = "\n".join(thoughts)
        self.assertIn("probing", joined.lower())
        self.assertTrue(any("node 18" in t.lower() for t in thoughts))


if __name__ == "__main__":
    unittest.main()
