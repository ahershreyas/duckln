"""Plan 127 — completion-aware idempotency (heal half-installs, don't re-do or
skip-broken) + safe disk reclamation so a small VM doesn't fill up on re-runs."""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb
from duckln.repo_bringup import _idempotent_guard, _SECONDARY_PY_SETUP_CMD
from duckln import resource_manager as rm


class CompletionAwareNpmGuard(unittest.TestCase):
    def test_skips_only_on_completion_marker_else_cleans_and_reinstalls(self):
        g = _idempotent_guard("npm ci")
        # completion marker gates the skip
        self.assertIn("node_modules/.package-lock.json", g)
        # half-installed node_modules (no marker) → clean + reinstall, not skip-broken
        self.assertIn("rm -rf node_modules", g)
        self.assertIn("npm ci", g)

    def test_covers_pnpm_yarn_bun_markers(self):
        for cmd in ("pnpm install", "yarn install", "bun install", "npm install"):
            g = _idempotent_guard(cmd)
            self.assertIn(".pnpm", g)
            self.assertIn(".yarn-integrity", g)
            self.assertIn("rm -rf node_modules", g)

    def test_does_not_double_wrap(self):
        already = "[ -d node_modules ] || npm ci"
        self.assertEqual(_idempotent_guard(already), already)

    def test_non_install_commands_unchanged(self):
        self.assertEqual(_idempotent_guard("npm run build"), "npm run build")


class CompletionAwareVenv(unittest.TestCase):
    def test_secondary_venv_uses_success_marker_and_recreates(self):
        cmd = _SECONDARY_PY_SETUP_CMD
        self.assertIn(".duckln-deps-ok", cmd)     # skip only when the marker exists
        self.assertIn("rm -rf .venv", cmd)         # broken/partial venv is recreated
        self.assertIn("touch .venv/.duckln-deps-ok", cmd)  # marker written only on success


class DiskReclamation(unittest.TestCase):
    def test_reclaim_is_safe_caches_only_never_repo(self):
        cmds = rm.reclaim_commands(execution_target="vm")
        joined = " ".join(cmds)
        # clears caches
        self.assertIn("npm cache clean", joined)
        self.assertIn("pip cache purge", joined)
        self.assertIn("cargo/registry", joined)
        # apt clean only on linux targets
        self.assertIn("apt-get clean", joined)
        # NEVER touches the repo / user project data
        self.assertNotIn("node_modules", joined)
        self.assertNotIn("projects", joined)
        self.assertNotIn(".venv", joined)
        # every command is failure-tolerant
        self.assertTrue(all("|| true" in c for c in cmds))

    def test_local_target_omits_apt(self):
        cmds = rm.reclaim_commands(execution_target="local")
        self.assertFalse(any("apt-get" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
