"""Plan 137 — detect & clean a HALF-INSTALL:
Fix1 reclaim a step's marker-less PARTIAL artifact (+ caches on a crunch) before retrying.
Fix2 the precheck spots a half-install up front, the plan adds an explicit cleanup step,
     and execution resumes from the failed part (completed steps still skip).
"""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb
import duckln.resource_manager as rm


class PartialCleanupCommand(unittest.TestCase):
    def test_only_removes_marker_less_partials(self):
        c = rb._partial_cleanup_command()
        # marker-aware: only rm node_modules when the lock-marker is ABSENT
        self.assertIn("node_modules/.package-lock.json", c)
        self.assertIn("rm -rf node_modules", c)
        # marker-aware venv (root + subdirs), keyed on .duckln-deps-ok
        self.assertIn(".duckln-deps-ok", c)
        self.assertIn(".venv", c)
        # build scratch
        self.assertIn("build_cache", c)
        self.assertIn(".codex-temp-sidecar", c)
        # never the repo sources / a complete artifact unconditionally
        self.assertNotIn("rm -rf node_modules;", c.replace("then rm -rf node_modules;", ""))

    def test_reclaim_includes_apt_partial_on_remote(self):
        joined = " ".join(rm.reclaim_commands(execution_target="vm"))
        self.assertIn("/var/cache/apt/archives/partial", joined)
        self.assertIn("*.partial", joined)  # stray temp partials
        # local target doesn't run apt cleanup
        self.assertNotIn("apt", " ".join(rm.reclaim_commands(execution_target="local")))


class PrecheckDetectsHalfInstall(unittest.TestCase):
    def test_precheck_script_probes_completion(self):
        s = rb._build_precheck_script("/home/ubuntu/.duckln/projects/Repo")
        self.assertIn("PARTIAL:node_modules", s)
        self.assertIn("PARTIAL:venv", s)
        self.assertIn("PARTIAL:build_cache", s)
        self.assertIn("PARTIAL:dpkg", s)
        self.assertIn(".duckln-deps-ok", s)  # marker-aware venv probe

    def test_parse_collects_partials(self):
        out = ("DUCKLN_PRECHECK_BEGIN\nTOOL:node\nCLONED:yes\n"
               "PARTIAL:node_modules\nPARTIAL:venv\nPARTIAL:venv\nDUCKLN_PRECHECK_END")
        present, cloned, versions = rb._parse_precheck_block(out, runtime_dir="/x")
        self.assertTrue(cloned)
        self.assertEqual(versions.get("partial"), "node_modules,venv")  # de-duped

    def test_no_partial_when_complete(self):
        out = "DUCKLN_PRECHECK_BEGIN\nTOOL:node\nCLONED:yes\nDUCKLN_PRECHECK_END"
        _, _, versions = rb._parse_precheck_block(out, runtime_dir="/x")
        self.assertIsNone(versions.get("partial"))


if __name__ == "__main__":
    unittest.main()
