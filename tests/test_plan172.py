"""Plan 172 — resource management must SIZE the resize from the real build-output footprint
(not a flat 5 GB), keep the build cache for a build step (don't delete what it regenerates),
carry the full accelerator facts + a guidance nudge naming the `resource_management` SKILL,
and honest-stop when the VM is down. Executes `_handle_resource_crunch` on real disk errors.
"""

from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from duckln import repo_bringup as rb
from duckln import resource_manager as rm
from duckln.repo_bringup import _RECOVERY_CONTINUE, _handle_resource_crunch

_DISK_ERROR = "rustc-LLVM ERROR: No space left on device (os error 28)\nENOSPC"
SN = types.SimpleNamespace


class _Runner:
    """Fake ControlledCommandRunner: returns a `du` footprint for the probe, exit 0 otherwise."""

    def __init__(self, *, footprint_mb=0, retry_exit=0):
        self._fp = footprint_mb
        self._retry_exit = retry_exit

    def run(self, cmd, **k):
        if "du -scm" in cmd:
            return SN(exit_code=0, timed_out=False, stdout=f"{self._fp}\ttotal\n", stderr="")
        return SN(exit_code=self._retry_exit, timed_out=False, stdout="", stderr="")


def _snap(free_mb, total_mb=16400, **extra):
    base = dict(ram_mb=2048, swap_on=False, disk_total_mb=total_mb, disk_free_mb=free_mb,
                disk_used_pct=98, cpu_cores=2, gpu_present=False, gpu_name="", vram_mb=0, cuda_version="")
    base.update(extra)
    return rm.ResourceSnapshot(**base)


def _kwargs(td, *, command, free_mb, et="vm"):
    step = SN(command=command, cwd="/repo", index=10, title="step")
    plan = SN(repo_slug="repo-x", steps=[step])
    from duckln.modes import ControlMode
    displays: list[str] = []
    return displays, dict(
        stderr=_DISK_ERROR, stdout="", paths=SN(config_dir=Path(td)), plan=plan,
        understanding=SN(execution_target=et), display=displays.append, approve=(lambda _m: True),
        executed=[], fake_repo=SN(repo_url="https://x/y", name="y"), project_dir=Path(td),
        current=SN(mode=ControlMode.HOOTLWO, text_prompt=None), text_prompt=None, step=step,
    )


def _patches(*, snap, host_free=400000, runner, reclaim_spy):
    return [
        patch.object(rm, "probe_target_resources", lambda **_k: snap),
        patch.object(rm, "probe_host_resources", lambda **_k: _snap(host_free, total_mb=500000, ram_mb=16384)),
        patch.object(rm, "reclaim_commands", reclaim_spy),
        patch.object(rb, "_wrap_command_for_execution_target", lambda **kw: (kw.get("command"), {})),
        patch.object(rb, "_active_vm_name", lambda _cd: "duckln-vm"),
        patch.object(rb, "reclaim_partial_before_retry", lambda **_k: None),
        patch.object(rb, "ControlledCommandRunner", lambda *a, **k: runner),
    ]


class BuildStepResizesToRealNeed(unittest.TestCase):
    """F1 + F2: a heavy build that regenerates ~7.4 GB of artifacts on a near-full disk is NOT
    aggressively reclaimed (keep the cache), is sized to disk_total + footprint + headroom, and
    goes to resize without a premature doomed retry."""

    def test_build_sizes_and_keeps_cache(self):
        reclaim_spy = MagicMock(return_value=("true",))
        runner = _Runner(footprint_mb=7400)
        apply_resize = MagicMock(return_value=False)
        with tempfile.TemporaryDirectory() as td:
            displays, kw = _kwargs(td, command="npm run --if-present build:all", free_mb=500)
            ps = _patches(snap=_snap(500), runner=runner, reclaim_spy=reclaim_spy)
            ps.append(patch.object(rm, "apply_resize", apply_resize))
            ps.append(patch("duckln.plan_mode.mark_status", lambda p, s, note="": SN(to_dict=lambda: {})))
            ps.append(patch("state.access.write_pending_plan", lambda *a, **k: None))
            for p in ps:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in ps])
            result = _handle_resource_crunch(**kw)
            facts = json.loads(__import__("state.access", fromlist=["read_workflow_state"]).read_workflow_state(Path(td))["active_resource_crunch"])

        blob = "\n".join(displays)
        # F2: a build step does NOT aggressively delete target/ (keep the cache).
        self.assertIn(False, [c.kwargs.get("aggressive") for c in reclaim_spy.call_args_list])
        self.assertNotIn(True, [c.kwargs.get("aggressive") for c in reclaim_spy.call_args_list])
        # F1: sized to disk_total(16.4) + footprint(7.4) + headroom(5) ≈ 29 GB; min free ≥ 12 GB.
        self.assertGreaterEqual(facts["recommended_gb"], 24)
        self.assertGreaterEqual(facts["min_gb"], 12)
        self.assertIn("regenerates", blob)            # the honest footprint message
        # F1: no premature doomed retry (free 500 MB < the real need).
        self.assertNotIn("Retrying the step", blob)
        self.assertTrue(apply_resize.called)          # resize offered (right-sized)
        self.assertIsNot(result, _RECOVERY_CONTINUE)


class NonBuildDiskCrunchUsesAggressiveReclaim(unittest.TestCase):
    """F2: a NON-build disk crunch (a download filled the disk) DOES aggressively reclaim stale
    hogs, and if that frees enough it resolves + resumes."""

    def test_non_build_aggressive_then_resolve(self):
        reclaim_spy = MagicMock(return_value=("true",))
        runner = _Runner(footprint_mb=0, retry_exit=0)
        with tempfile.TemporaryDirectory() as td:
            displays, kw = _kwargs(td, command="curl -O https://host/model.bin", free_mb=9000)
            ps = _patches(snap=_snap(9000), runner=runner, reclaim_spy=reclaim_spy)
            for p in ps:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in ps])
            result = _handle_resource_crunch(**kw)

        # non-build → aggressive reclaim used.
        self.assertIn(True, [c.kwargs.get("aggressive") for c in reclaim_spy.call_args_list])
        # 9 GB free ≥ the 5 GB floor (footprint 0) → retry → resolve.
        self.assertIs(result, _RECOVERY_CONTINUE)


class FactsCarryGuidanceAndAccelerator(unittest.TestCase):
    """F4 + F7: the persisted facts name the SKILL, carry a guidance nudge, and the full
    accelerator state (gpu/vram/cuda + an accelerator field)."""

    def test_facts(self):
        reclaim_spy = MagicMock(return_value=("true",))
        runner = _Runner(footprint_mb=7400)
        with tempfile.TemporaryDirectory() as td:
            displays, kw = _kwargs(td, command="npm run build:all", free_mb=500)
            ps = _patches(snap=_snap(500, gpu_present=True, gpu_name="A100", vram_mb=40000, cuda_version="12.1"),
                          runner=runner, reclaim_spy=reclaim_spy)
            ps.append(patch.object(rm, "apply_resize", MagicMock(return_value=False)))
            ps.append(patch("duckln.plan_mode.mark_status", lambda p, s, note="": SN(to_dict=lambda: {})))
            ps.append(patch("state.access.write_pending_plan", lambda *a, **k: None))
            for p in ps:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in ps])
            _handle_resource_crunch(**kw)
            from state.access import read_workflow_state
            facts = json.loads(read_workflow_state(Path(td))["active_resource_crunch"])

        self.assertEqual(facts["skill"], "resource_management")
        self.assertIn("regenerates", facts["guidance"])
        self.assertEqual(facts["accelerator"], "cuda")  # gpu_present on a vm target
        self.assertEqual(facts["gpu_name"], "A100")
        self.assertEqual(facts["vram_mb"], 40000)
        self.assertEqual(facts["cuda_version"], "12.1")
        self.assertIn("build_regen_mb", facts)


class VmDownHonestStop(unittest.TestCase):
    """F5: a target whose probe returns nothing (disk_total 0) is unreachable/down → honest-stop,
    no reclaim, no resize."""

    def test_vm_down(self):
        reclaim_spy = MagicMock(return_value=("true",))
        runner = _Runner()
        with tempfile.TemporaryDirectory() as td:
            displays, kw = _kwargs(td, command="npm run build:all", free_mb=0)
            ps = _patches(snap=_snap(0, total_mb=0), runner=runner, reclaim_spy=reclaim_spy)
            ps.append(patch("duckln.plan_mode.mark_status", lambda p, s, note="": SN(to_dict=lambda: {})))
            ps.append(patch("state.access.write_pending_plan", lambda *a, **k: None))
            for p in ps:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in ps])
            result = _handle_resource_crunch(**kw)

        blob = "\n".join(displays)
        self.assertIn("unreachable", blob)
        self.assertFalse(reclaim_spy.called)          # didn't try to reclaim a VM that isn't there
        self.assertIsNot(result, _RECOVERY_CONTINUE)


class ResourceSkillExists(unittest.TestCase):
    """F3: the resource_management SKILL exists and covers the full matrix."""

    def test_skill_matrix(self):
        txt = (rb.PLAYBOOK_DIR / "resource_management.md").read_text(encoding="utf-8")
        for section in ("## Disk", "## RAM", "## CPU", "## GPU", "## NPU", "## Apple MLX"):
            self.assertIn(section, txt)
        self.assertIn("local", txt)        # MLX runs on local only
        self.assertIn("route to a GPU", txt)
        from duckln.subagents import _playbook_excerpt
        self.assertNotEqual(_playbook_excerpt("resource_management.md"), "Playbook excerpt unavailable.")


if __name__ == "__main__":
    unittest.main()
