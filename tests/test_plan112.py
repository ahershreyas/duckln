from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln import resource_manager as rm
from duckln.cloud_runtime import build_cloud_resize_commands
from duckln.repo_bringup import _declared_prebuild_script_steps, _tauri_prebuild_steps
import duckln.repo_bringup as rb
from state.repo_catalog import RepoCatalogRecord


def _repo():
    return RepoCatalogRecord("demo", "https://github.com/x/demo", 0, "", "", "", "")


# --- P113: full build sequence ------------------------------------------------


class TestPrebuildSequence(unittest.TestCase):
    def test_declared_prebuild_scripts_selected(self):
        pkg = {"scripts": {"dev": "vite", "prepare": "husky", "build:sidecar": "cargo build", "generate": "codegen", "test": "jest"}}
        cmds = [c for _t, c, _v in _declared_prebuild_script_steps(payload=pkg, package_manager="npm")]
        # Plan 129: emitted with --if-present so an absent declared script no-ops.
        self.assertIn("npm run --if-present prepare", cmds)
        self.assertIn("npm run --if-present build:sidecar", cmds)
        self.assertIn("npm run --if-present generate", cmds)
        self.assertFalse(any("dev" in c for c in cmds))
        self.assertFalse(any(c.endswith("test") for c in cmds))

    def test_tauri_before_command_parsed(self):
        conf = '{"build": {"beforeBuildCommand": "npm run build:sidecar", "beforeDevCommand": "vite"}}'
        orig = rb._read_repo_text_file
        rb._read_repo_text_file = lambda *a, **k: conf
        try:
            steps = _tauri_prebuild_steps(config_dir=Path("/tmp"), repo=_repo(), detected_files=("tauri.conf.json",))
        finally:
            rb._read_repo_text_file = orig
        self.assertTrue(steps)
        self.assertIn("build:sidecar", steps[0][1])

    def test_no_tauri_no_steps(self):
        self.assertEqual(_tauri_prebuild_steps(config_dir=Path("/tmp"), repo=_repo(), detected_files=("package.json",)), ())


# --- P114: container resize ---------------------------------------------------


class TestContainerResize(unittest.TestCase):
    def test_docker_update_for_ram(self):
        rec = rm.ResourceRecommendation("ram", 2048, 4096, 4096, True, "x")
        cmds = rm.resize_commands_for_target(rec, execution_target="container", vm_name="box")
        self.assertEqual(cmds, ("docker update --memory 4g --memory-swap 4g box",))

    def test_container_disk_unsupported_in_place(self):
        rec = rm.ResourceRecommendation("disk", 13000, 25000, 25000, True, "x")
        self.assertEqual(rm.resize_commands_for_target(rec, execution_target="container", vm_name="box"), ())

    def test_vm_still_multipass(self):
        rec = rm.ResourceRecommendation("disk", 13000, 25000, 25000, True, "x")
        cmds = rm.resize_commands_for_target(rec, execution_target="vm", vm_name="duckln-vm")
        self.assertIn("multipass set local.duckln-vm.disk=25G", cmds)


# --- P115: cloud resize builders ---------------------------------------------


class TestCloudResize(unittest.TestCase):
    def test_aws_disk_modify_volume(self):
        rec = SimpleNamespace(provider="aws", display_name="i", metadata={"instance_id": "i-1", "root_volume_id": "vol-1"})
        cmds = build_cloud_resize_commands(rec, resource="disk", disk_gb=40)
        self.assertEqual(cmds, ("aws ec2 modify-volume --volume-id vol-1 --size 40",))

    def test_aws_ram_changes_instance_type(self):
        rec = SimpleNamespace(provider="aws", display_name="i", metadata={"instance_id": "i-1"})
        cmds = build_cloud_resize_commands(rec, resource="ram", instance_type="t3.large")
        self.assertTrue(any("modify-instance-attribute" in c and "t3.large" in c for c in cmds))
        self.assertTrue(any("stop-instances" in c for c in cmds) and any("start-instances" in c for c in cmds))

    def test_gcp_set_machine_type(self):
        rec = SimpleNamespace(provider="gcp", display_name="vm1", metadata={"instance_name": "vm1", "zone": "us-central1-a"})
        cmds = build_cloud_resize_commands(rec, resource="ram", instance_type="e2-standard-4")
        self.assertTrue(any("set-machine-type" in c and "e2-standard-4" in c for c in cmds))

    def test_missing_params_returns_empty(self):
        rec = SimpleNamespace(provider="aws", display_name="i", metadata={})
        self.assertEqual(build_cloud_resize_commands(rec, resource="ram", instance_type="t3.large"), ())


if __name__ == "__main__":
    unittest.main()
