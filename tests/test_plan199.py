"""Plan 199 — auto-fix VM ensure-loop (F6), verify-via-list (F1), unmask the state-summary
ValueError (F2), real error reason (F3), last-repo routing (F5), model-aware error distill (F7)."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from types import SimpleNamespace


def _run_ok(exit_code=0, stdout="", timed_out=False):
    return SimpleNamespace(run=lambda cmd, **kw: SimpleNamespace(
        exit_code=exit_code, stdout=stdout, stderr="", timed_out=timed_out))


class F2CompactSummary(unittest.TestCase):
    def test_never_exceeds_max(self):
        from duckln.vm import _compact_vm_summary
        from state.store import MAX_SUMMARY_LENGTH

        long = "x" * 5000
        out = _compact_vm_summary(long)
        self.assertLessEqual(len(out), MAX_SUMMARY_LENGTH)
        self.assertTrue(out.endswith("…"))

    def test_empty_and_short(self):
        from duckln.vm import _compact_vm_summary

        self.assertEqual(_compact_vm_summary(""), "VM state updated.")
        self.assertEqual(_compact_vm_summary("VM ready."), "VM ready.")

    def test_record_vm_state_does_not_raise_on_long_summary(self):
        import duckln.vm as V

        store = mock.Mock()
        with mock.patch.object(V, "initialize_state_store", return_value=store), \
             mock.patch.object(V, "write_config_snapshot"):
            # A 3000-char raw error must NOT raise (it used to raise "must stay concise").
            V._record_vm_state(SimpleNamespace(config_dir="/tmp"), "duckln-vm",
                               status="create_failed", summary="E" * 3000)
        # upsert_vm_linkage got a compact summary (≤ max)
        from state.store import MAX_SUMMARY_LENGTH
        _, kwargs = store.upsert_vm_linkage.call_args
        self.assertLessEqual(len(kwargs["summary"]), MAX_SUMMARY_LENGTH)


class F1VerifyViaList(unittest.TestCase):
    def test_present_but_info_flaky_is_not_a_failure(self):
        import duckln.vm as V
        from duckln import shell

        shell.clear_global_abort()
        # info always errors → "Unknown", but the VM IS in the list → treat as present (exit 0)
        with mock.patch.object(V, "get_multipass_vm_state", return_value="Unknown"), \
             mock.patch.object(V, "list_multipass_vm_names", return_value=("duckln-vm",)):
            res = V._wait_for_vm_running_via_polling(
                vm_name="duckln-vm", runner=_run_ok(exit_code=2), display=lambda _m: None,
                step_callback=lambda *a, **k: None, step_id="x", step_label="y",
                timeout_seconds=300, poll_interval_seconds=0.01,
            )
        self.assertEqual(res.exit_code, 0)  # not a failure

    def test_absent_and_erroring_still_bails(self):
        import duckln.vm as V
        from duckln import shell

        shell.clear_global_abort()
        with mock.patch.object(V, "get_multipass_vm_state", return_value="Unknown"), \
             mock.patch.object(V, "list_multipass_vm_names", return_value=()):
            res = V._wait_for_vm_running_via_polling(
                vm_name="duckln-vm", runner=_run_ok(exit_code=2), display=lambda _m: None,
                step_callback=lambda *a, **k: None, step_id="x", step_label="y",
                timeout_seconds=300, poll_interval_seconds=0.01,
            )
        self.assertEqual(res.exit_code, 1)
        self.assertIn("not in", (res.stderr or "").lower())


class F6EnsureVmReady(unittest.TestCase):
    def setUp(self):
        from duckln import shell
        shell.clear_global_abort()

    def test_already_running(self):
        import duckln.vm as V

        with mock.patch.object(V, "get_multipass_vm_state", return_value="Running"), \
             mock.patch.object(V, "list_multipass_vms_or_none", return_value=("duckln-vm",)):
            self.assertTrue(V.ensure_vm_ready("duckln-vm", display=lambda _m: None, runner=_run_ok()))

    def test_stopped_gets_started(self):
        import duckln.vm as V

        seq = iter(["Stopped", "Running"])
        with mock.patch.object(V, "get_multipass_vm_state", side_effect=lambda *a, **k: next(seq)), \
             mock.patch.object(V, "list_multipass_vms_or_none", return_value=("duckln-vm",)):
            self.assertTrue(V.ensure_vm_ready("duckln-vm", display=lambda _m: None, runner=_run_ok()))

    def test_daemon_down_restarts(self):
        import duckln.vm as V

        probe = iter([None, ("duckln-vm",)])
        with mock.patch.object(V, "list_multipass_vms_or_none", side_effect=lambda *a, **k: next(probe)), \
             mock.patch.object(V, "get_multipass_vm_state", return_value="Running"), \
             mock.patch.object(V, "_restart_multipass_daemon") as rd, \
             mock.patch.object(V.time, "sleep", lambda *_: None):
            ok = V.ensure_vm_ready("duckln-vm", display=lambda _m: None, runner=_run_ok(), os_type="Darwin")
        self.assertTrue(ok)
        self.assertTrue(rd.called)

    def test_corrupt_offers_delete_recreate(self):
        import duckln.vm as V

        # Absent + recover fails → delete+recreate (confirm=yes); after recreate the VM exists+Running.
        st = {"present": False}
        called = {"recreate": False}

        def _recreate():
            called["recreate"] = True
            st["present"] = True
            return True

        with mock.patch.object(V, "list_multipass_vms_or_none", side_effect=lambda **k: (("duckln-vm",) if st["present"] else ())), \
             mock.patch.object(V, "get_multipass_vm_state", side_effect=lambda *a, **k: ("Running" if st["present"] else "Deleted")):
            ok = V.ensure_vm_ready(
                "duckln-vm", display=lambda _m: None, runner=_run_ok(exit_code=1),
                confirm=lambda _m: True, recreate=_recreate, os_type="Darwin", max_cycles=1,
            )
        self.assertTrue(called["recreate"])
        self.assertTrue(ok)


class F5LastRepoRouting(unittest.TestCase):
    def test_recency_defers_to_single_repo(self):
        from duckln import main as M

        store = SimpleNamespace(
            get_latest_repo_state=lambda: SimpleNamespace(repo_url="https://github.com/x/JustHireMe", repo_key="k", metadata={"repo_name": "JustHireMe"}),
            list_repo_states=lambda: (SimpleNamespace(execution_target="vm", repo_url="u", metadata={"repo_name": "R"}),),
            list_recent_custom_repos=lambda: (SimpleNamespace(repo_name="AutoGPT", repo_url="u"),),
        )
        with mock.patch.object(M, "initialize_state_store", return_value=store):
            out = []
            self.assertTrue(M._maybe_answer_inventory_question("what was the last repo we ran?", SimpleNamespace(), out.append))
        self.assertIn("previously working on", out[0].lower())
        self.assertNotIn("custom repos you added", out[0].lower())

    def test_list_query_still_lists(self):
        from duckln import main as M

        store = SimpleNamespace(
            list_repo_states=lambda: (SimpleNamespace(execution_target="vm", repo_url="u", metadata={"repo_name": "R"}),),
            list_recent_custom_repos=lambda: (SimpleNamespace(repo_name="AutoGPT", repo_url="u"),),
        )
        with mock.patch.object(M, "initialize_state_store", return_value=store):
            out = []
            self.assertTrue(M._maybe_answer_inventory_question("show custom repo list", SimpleNamespace(), out.append))
        self.assertIn("custom repos you added", out[0].lower())


class F7DistillError(unittest.TestCase):
    def test_leads_with_key_error(self):
        from duckln.recovery import _distilled_error_block

        err = "ubuntu@host:~$ npm run build\nError: Cannot find module 'vite'\n" + ("noise\n" * 40)
        block = _distilled_error_block(err, "", capable=False)
        self.assertTrue(block.startswith("KEY ERROR:"))
        self.assertIn("vite", block)

    def test_capable_gets_more_raw(self):
        from duckln.recovery import _distilled_error_block

        big = "line\n" * 4000  # ~20k chars
        weak = _distilled_error_block(big, "", capable=False)
        cap = _distilled_error_block(big, "", capable=True)
        self.assertGreater(len(cap), len(weak))


if __name__ == "__main__":
    unittest.main()
