"""Plan 143 — actually RESOLVE a full VM disk:
F1 the multipass resize grows the GUEST filesystem (growpart+resize2fs), not just the virtual disk.
F2 a disk-FULL crunch reclaims the real hogs (target/, cargo registry, node_modules) — aggressive.
F4 a resize is NEVER auto — it asks the user and suggests the minimum required size.
"""

from __future__ import annotations

import unittest

import duckln.resource_manager as rm


class ResizeGrowsGuestFilesystem(unittest.TestCase):
    def test_disk_resize_includes_growpart_after_start(self):
        rec = rm.ResourceRecommendation("disk", 5120, 12288, 10240, True, "disk full")
        cmds = rm.multipass_resize_commands(rec, "duckln-vm")
        joined = "\n".join(cmds)
        self.assertIn("multipass set local.duckln-vm.disk=12G", joined)
        self.assertTrue(any("growpart" in c and "resize2fs" in c for c in cmds))
        # growpart must run AFTER the VM is started, and inside the VM (multipass exec)
        start_i = next(i for i, c in enumerate(cmds) if "multipass start" in c)
        grow_i = next(i for i, c in enumerate(cmds) if "growpart" in c)
        self.assertGreater(grow_i, start_i)
        self.assertIn("multipass exec duckln-vm", cmds[grow_i])
        # device is auto-detected (no hardcoded /dev/sda1)
        self.assertNotIn("/dev/sda1", cmds[grow_i])

    def test_ram_resize_has_no_growpart(self):
        rec = rm.ResourceRecommendation("ram", 2048, 4096, 3072, True, "oom")
        cmds = rm.multipass_resize_commands(rec, "duckln-vm")
        self.assertFalse(any("growpart" in c for c in cmds))
        self.assertTrue(any("memory=4G" in c for c in cmds))


class AggressiveReclaim(unittest.TestCase):
    def test_aggressive_clears_the_real_hogs(self):
        agg = " ".join(rm.reclaim_commands(execution_target="vm", aggressive=True))
        self.assertIn("rm -rf target", agg)            # Rust build output
        self.assertIn("~/.cargo/registry", agg)
        self.assertIn("rm -rf node_modules", agg)
        # never the repo source / user data
        self.assertNotIn(" src ", f" {agg} ")          # not the source dir
        self.assertTrue(all("|| true" in c for c in rm.reclaim_commands(execution_target="vm", aggressive=True)))

    def test_routine_reclaim_unchanged(self):
        norm = " ".join(rm.reclaim_commands(execution_target="vm", aggressive=False))
        self.assertNotIn("rm -rf target ", norm)        # routine reclaim keeps the build
        self.assertNotIn("rm -rf node_modules", norm)
        self.assertIn("pip cache purge", norm)          # still clears caches


class ResizeAsksUser(unittest.TestCase):
    def test_no_auto_approve_lambda_in_resource_crunch(self):
        # Plan 143 F4: the disk-crunch handler must NOT auto-approve a resize.
        import inspect
        import duckln.repo_bringup as rb
        src = inspect.getsource(rb._handle_resource_crunch)
        self.assertNotIn("approve=(lambda", src)            # no auto-approve
        self.assertIn("apply_resize", src)                   # still offers a resize
        self.assertIn("won't resize without your OK", src)   # explicitly asks
        self.assertIn("needs at least", src)                 # suggests the minimum


if __name__ == "__main__":
    unittest.main()
