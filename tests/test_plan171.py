"""Plan 171 — the disk-full resource handler must not CRASH on a missing `import math`, must
SURFACE the space numbers (used/free/needed + reclaimed), keep the custom-GB resize prompt,
RESUME from the exact failed step, and pass the crunch FACTS to the LLM as a hint.

The pre-existing tests only checked `_handle_resource_crunch` via `inspect.getsource` (a source
string) or with a non-resource error that early-returns — so a runtime `NameError` shipped. These
tests EXECUTE the handler on a real disk-full error so that class of bug can't ship again.
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
from duckln.repo_bringup import _RECOVERY_CONTINUE, _handle_resource_crunch, _load_done_steps
from duckln.agent_context import WorkflowStateSnapshot
from state.access import WORKFLOW_STATE_KEYS, read_workflow_state, write_workflow_state

_DISK_ERROR = "rustc-LLVM ERROR: No space left on device (os error 28)\nENOSPC"


def _snapshot(free_mb: int):
    return types.SimpleNamespace(
        disk_total_mb=20000, disk_free_mb=free_mb, disk_used_pct=95,
        ram_mb=2048, cpu_cores=2, swap_on=False,
    )


def _rec(feasible=True):
    return types.SimpleNamespace(
        resource="disk", current_mb=20000, recommended_mb=30000, min_mb=25000,
        feasible=feasible, host_capped=False, explanation="disk full — grow it.", cost_note="",
    )


def _common_kwargs(td: str, *, free_mb: int, text_prompt=None):
    paths = types.SimpleNamespace(config_dir=Path(td))
    # A NON-build step: under Plan 172 a build step that regenerates its reclaimed output is
    # gated stricter (needs regen+headroom free); these Plan-171 tests assert the simple
    # "reclaim frees enough → resume" / "still tight → resize" paths, so use an install step.
    step = types.SimpleNamespace(command="pip install -r requirements.txt", cwd="/repo", index=10, title="install")
    plan = types.SimpleNamespace(repo_slug="repo-x", steps=[step])
    fake_repo = types.SimpleNamespace(repo_url="https://x/y", name="y")
    from duckln.modes import ControlMode
    current = types.SimpleNamespace(mode=ControlMode.HOOTLWO, text_prompt=None)
    understanding = types.SimpleNamespace(execution_target="vm")
    displays: list[str] = []
    return displays, dict(
        stderr=_DISK_ERROR, stdout="", paths=paths, plan=plan, understanding=understanding,
        display=displays.append, approve=(lambda _m: True), executed=[], fake_repo=fake_repo,
        project_dir=Path(td), current=current, text_prompt=text_prompt, step=step,
    )


class _BumpRunner:
    """A fake ControlledCommandRunner whose .run() bumps the simulated free space (reclaim
    freed GBs) and reports the step succeeded — so the resolve→resume path is exercised."""

    def __init__(self, state, *a, **k):
        self._state = state

    def run(self, cmd, **k):
        self._state["free"] = 9000
        return types.SimpleNamespace(exit_code=0, timed_out=False, stdout="", stderr="")


class _NoopRunner:
    def __init__(self, *a, **k):
        pass

    def run(self, cmd, **k):
        return types.SimpleNamespace(exit_code=0, timed_out=False, stdout="", stderr="")


def _patch_rm(*, snapshot_fn, recommend_rec):
    """Patch the resource_manager seams the handler calls (it imports rm inside the fn)."""
    return [
        patch.object(rm, "crunch_from_error", lambda *_a, **_k: types.SimpleNamespace(resource="disk", used_pct=95)),
        patch.object(rm, "probe_target_resources", lambda **_k: snapshot_fn()),
        patch.object(rm, "probe_host_resources", lambda **_k: _snapshot(40000)),
        patch.object(rm, "estimate_requirement", lambda **_k: types.SimpleNamespace(disk_total_mb=0, ram_mb=0, reason="x")),
        patch.object(rm, "recommend", lambda *_a, **_k: recommend_rec),
        patch.object(rm, "reclaim_commands", MagicMock(return_value=("true",))),
        # never touch the host: stub the runner + the wrapper + vm-name + partial reclaim.
        patch.object(rb, "_wrap_command_for_execution_target", lambda **kw: (kw.get("command"), {})),
        patch.object(rb, "_active_vm_name", lambda _cd: "duckln-vm"),
        patch.object(rb, "reclaim_partial_before_retry", lambda **_k: None),
    ]


class DiskCrunchDoesNotCrash(unittest.TestCase):
    """F1 + F4 + F5 + F7: the disk-full path runs end-to-end (no NameError), surfaces numbers,
    reports the reclaimed amount, and on freeing enough RESUMES the exact failed step."""

    def test_resolve_and_resume(self):
        state = {"free": 1000}
        recommend_rec = _rec(feasible=True)
        with tempfile.TemporaryDirectory() as td:
            displays, kwargs = _common_kwargs(td, free_mb=1000)
            patches = _patch_rm(snapshot_fn=lambda: _snapshot(state["free"]), recommend_rec=recommend_rec)
            patches.append(patch.object(rb, "ControlledCommandRunner", lambda *a, **k: _BumpRunner(state)))
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])

            result = _handle_resource_crunch(**kwargs)  # must NOT raise NameError (F1)
            # F7: the failed step is marked done so the executor continues (resume, not restart).
            # Read the store WHILE the tempdir still exists.
            done = _load_done_steps(Path(td), "repo-x")

        self.assertIs(result, _RECOVERY_CONTINUE)  # F7: resolved → resume, not a crash/pause
        blob = "\n".join(displays)
        self.assertIn("free of", blob)            # F4: used/free/total numbers
        self.assertIn("needs ≈", blob)            # F4: how much more is needed
        self.assertIn("Reclaimed ~", blob)        # F5: reclaimed amount reported
        self.assertIn("resolved", blob)           # F7: the failed step re-ran and passed
        self.assertIn("pip install -r requirements.txt", done)
        # F1 regression: `math` is importable in the module namespace.
        self.assertTrue(hasattr(rb, "math"))


class StillTightAsksToResizeWithCustomGB(unittest.TestCase):
    """F6 + F8: when reclaim isn't enough, the user is asked to resize (custom GB via prompt_value)
    and the crunch FACTS are persisted for the LLM hint; nothing auto-resizes."""

    def test_resize_prompt_and_facts_persisted(self):
        state = {"free": 1000}  # never bumped → stays tight
        recommend_rec = _rec(feasible=True)
        text_prompt = MagicMock(return_value="25")
        with tempfile.TemporaryDirectory() as td:
            displays, kwargs = _common_kwargs(td, free_mb=1000, text_prompt=text_prompt)
            patches = _patch_rm(snapshot_fn=lambda: _snapshot(state["free"]), recommend_rec=recommend_rec)
            patches.append(patch.object(rb, "ControlledCommandRunner", lambda *a, **k: _NoopRunner()))
            apply_resize = MagicMock(return_value=False)  # user declines → honest pause
            patches.append(patch.object(rm, "apply_resize", apply_resize))
            patches.append(patch("duckln.plan_mode.mark_status", lambda plan, status, note="": types.SimpleNamespace(to_dict=lambda: {})))
            patches.append(patch("state.access.write_pending_plan", lambda *a, **k: None))
            for p in patches:
                p.start()
            self.addCleanup(lambda: [p.stop() for p in patches])

            result = _handle_resource_crunch(**kwargs)
            # F8: read the persisted facts WHILE the tempdir still exists.
            facts_raw = read_workflow_state(Path(td)).get("active_resource_crunch")

        # F6: a resize was OFFERED, threading the user's value-prompt (custom GB), never auto.
        self.assertTrue(apply_resize.called)
        self.assertIs(apply_resize.call_args.kwargs["prompt_value"], text_prompt)
        # declined → a paused result, not a silent continue/crash.
        self.assertIsNot(result, _RECOVERY_CONTINUE)
        self.assertIsNotNone(result)
        # F8: the deterministic crunch FACTS are persisted for the LLM hint.
        self.assertIsInstance(facts_raw, str)
        facts = json.loads(facts_raw)
        self.assertEqual(facts["resource"], "disk")
        self.assertEqual(facts["disk_total_mb"], 20000)
        self.assertIn("resize", facts["options"])
        self.assertEqual(facts["min_gb"], 5)  # needed_free 5120 MB → 5 GB minimum


class ResourceToolsAreCorrect(unittest.TestCase):
    """F3: the Disk/RAM recommendation + the aggressive delete are correct (and never crash)."""

    def test_recommend_disk_is_feasible_and_derived(self):
        crunch = rm.ResourceCrunch(resource="disk", available_mb=400, used_pct=98)
        snap = rm.ResourceSnapshot(disk_total_mb=20000, disk_free_mb=400, disk_used_pct=98, ram_mb=4096)
        host = rm.ResourceSnapshot(disk_total_mb=200000, disk_free_mb=150000, ram_mb=16384)
        need = rm.estimate_requirement(snapshot=snap, error_text=_DISK_ERROR)
        rec = rm.recommend(crunch, snapshot=snap, need=need, host=host, execution_target="vm", vm_name="vm")
        self.assertEqual(rec.resource, "disk")
        self.assertTrue(rec.feasible)
        self.assertGreater(rec.recommended_mb, rec.current_mb)

    def test_estimate_oom_uses_math_without_crashing(self):
        snap = rm.ResourceSnapshot(ram_mb=2048)
        need = rm.estimate_requirement(snapshot=snap, error_text="Killed: signal 9 out of memory")
        self.assertGreater(need.ram_mb, 2048)  # exercises math.ceil in resource_manager

    def test_aggressive_reclaim_deletes_real_hogs(self):
        cmds = " ".join(rm.reclaim_commands(execution_target="vm", aggressive=True))
        self.assertIn("target", cmds)
        self.assertIn("~/.cargo/registry", cmds)
        self.assertIn("node_modules", cmds)

    def test_safe_reclaim_does_not_delete_target(self):
        cmds = " ".join(rm.reclaim_commands(execution_target="vm", aggressive=False))
        self.assertNotIn("rm -rf target", cmds)


class CrunchFactsPlumbing(unittest.TestCase):
    """F8: the `active_resource_crunch` hint round-trips through the workflow-state layer and the
    WorkflowStateSnapshot the conversation agent reads."""

    def test_workflow_state_key_and_view_field(self):
        self.assertIn("active_resource_crunch", WORKFLOW_STATE_KEYS)
        self.assertIn("active_resource_crunch", WorkflowStateSnapshot.__dataclass_fields__)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            write_workflow_state(Path(td), {"active_resource_crunch": json.dumps({"resource": "disk"})})
            got = read_workflow_state(Path(td)).get("active_resource_crunch")
            self.assertEqual(json.loads(got)["resource"], "disk")
            # clearing (on resolve) removes it.
            write_workflow_state(Path(td), {"active_resource_crunch": None})
            self.assertIsNone(read_workflow_state(Path(td)).get("active_resource_crunch"))


if __name__ == "__main__":
    unittest.main()
