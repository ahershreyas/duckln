"""Plan 198 — "stop" actually stops (F1), robust VM lifecycle (F2), delete provision for VMs +
Docker (F3), and the "show custom repo list" misroute fix (F4)."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from types import SimpleNamespace


def _runner(exit_code=0, stdout="", timed_out=False):
    return SimpleNamespace(run=lambda cmd, **kw: SimpleNamespace(
        exit_code=exit_code, stdout=stdout, stderr="", timed_out=timed_out))


class F1StopWorks(unittest.TestCase):
    def test_poll_loop_bails_on_abort(self):
        import duckln.vm as V
        from duckln import shell

        shell.clear_global_abort()
        shell.request_global_abort()  # simulate user typing "stop"
        try:
            res = V._wait_for_vm_running_via_polling(
                vm_name="duckln-vm", runner=_runner(exit_code=1), display=lambda _m: None,
                step_callback=lambda *a, **k: None, step_id="x", step_label="y", timeout_seconds=300,
            )
        finally:
            shell.clear_global_abort()
        self.assertEqual(res.exit_code, 130)  # cancelled
        self.assertIn("cancelled", (res.stderr or "").lower())

    def test_poll_loop_bails_on_broken_instance(self):
        import duckln.vm as V
        from duckln import shell

        shell.clear_global_abort()
        # multipass info always errors → state "Unknown" → bail after N consecutive
        res = V._wait_for_vm_running_via_polling(
            vm_name="duckln-vm", runner=_runner(exit_code=2), display=lambda _m: None,
            step_callback=lambda *a, **k: None, step_id="x", step_label="y",
            timeout_seconds=300, poll_interval_seconds=0.01,
        )
        self.assertEqual(res.exit_code, 1)
        # Plan 199 F1 refined the message: an ABSENT + erroring VM bails (present-in-list no longer does).
        self.assertIn("not in", (res.stderr or "").lower())

    def test_stop_words_defined(self):
        from duckln.textual_ui import _STOP_WORDS

        for w in ("stop", "cancel", "abort", "halt"):
            self.assertIn(w, _STOP_WORDS)

    def test_shell_abort_accessor(self):
        from duckln import shell

        shell.clear_global_abort()
        self.assertFalse(shell.is_global_abort_requested())
        shell.request_global_abort()
        self.assertTrue(shell.is_global_abort_requested())
        shell.clear_global_abort()


class F2VmLifecycle(unittest.TestCase):
    def test_delete_command_image(self):
        from duckln.repo_bringup import _delete_target_command

        self.assertEqual(_delete_target_command(execution_target="image", name="python:3.12"), "docker rmi -f python:3.12")
        self.assertEqual(_delete_target_command(execution_target="container", name="app"), "docker rm -f -v app")
        self.assertEqual(_delete_target_command(execution_target="vm", name="duckln-vm"), "multipass delete duckln-vm --purge")

    def test_list_failure_vs_empty_used_by_create(self):
        # The create path must use the failure-aware probe (Plan 196 F4) — assert the helper
        # distinguishes failure (None) from empty (()).
        from duckln.vm import list_multipass_vms_or_none

        self.assertIsNone(list_multipass_vms_or_none(runner=_runner(exit_code=1)))
        self.assertEqual(list_multipass_vms_or_none(runner=_runner(stdout='{"list": []}')), ())


class F3DeleteProvision(unittest.TestCase):
    def test_mention_suggestions_includes_docker(self):
        from duckln import delete_controls as DC
        import duckln.docker_inventory as DI

        inv_c = SimpleNamespace(ok=True, containers=[SimpleNamespace(name="app", container_id="c1", image="nginx")])
        inv_i = SimpleNamespace(ok=True, images=[SimpleNamespace(name="python:3.12")])
        with mock.patch.object(DI, "list_docker_containers", return_value=inv_c), \
             mock.patch.object(DI, "list_docker_images", return_value=inv_i):
            targets = DC.mention_suggestions(
                "/tmp", vm_lister=lambda: ("duckln-vm",),
                store=SimpleNamespace(list_recent_custom_repos=lambda: []),
            )
        kinds = {t.kind for t in targets}
        self.assertEqual(kinds, {"vm", "container", "image"})

    def test_perform_delete_image_confirms_and_runs(self):
        from duckln import delete_controls as DC

        ran: list[str] = []
        ok = DC.perform_delete(
            DC.DeletableTarget("image", "python:3.12", "@python:3.12 (Docker image)"),
            config_dir="/tmp", run_cmd=lambda c: (ran.append(c) or 0), confirm=lambda _m: True,
            display=lambda _m: None, store=SimpleNamespace(record_deletion=lambda **k: None),
        )
        self.assertTrue(ok)
        self.assertEqual(ran, ["docker rmi -f python:3.12"])

    def test_perform_delete_declined_runs_nothing(self):
        from duckln import delete_controls as DC

        ran: list[str] = []
        ok = DC.perform_delete(
            DC.DeletableTarget("vm", "duckln-vm", "@duckln-vm (VM)"),
            config_dir="/tmp", run_cmd=lambda c: (ran.append(c) or 0), confirm=lambda _m: False,
            display=lambda _m: None, store=SimpleNamespace(record_deletion=lambda **k: None),
        )
        self.assertFalse(ok)
        self.assertEqual(ran, [])

    def test_cleanup_command_registered(self):
        from duckln.constants import SLASH_COMMANDS

        self.assertIn("/cleanup", SLASH_COMMANDS)


class F4RepoListRouting(unittest.TestCase):
    def test_screenshot_phrases_route_to_repo_inventory(self):
        from duckln import main as M

        for msg in ("show custom repo list", "can you show me all customer repos", "list my custom repos"):
            m = msg.lower()
            self.assertTrue(M._INV_REPO_RE.search(m), msg)
            self.assertFalse(M._INV_DOCKER_RE.search(m), msg)

    def test_does_not_hijack_repo_qa(self):
        from duckln import main as M

        self.assertFalse(M._INV_REPO_RE.search("show me the entrypoint of the repo"))
        self.assertFalse(M._INV_REPO_RE.search("how does auth work in the repo"))

    def test_answer_lists_custom_and_tracked(self):
        from duckln import main as M

        store = SimpleNamespace(
            list_repo_states=lambda: (SimpleNamespace(execution_target="vm", repo_url="https://github.com/x/JustHireMe", metadata={"repo_name": "JustHireMe"}),),
            list_recent_custom_repos=lambda: (SimpleNamespace(repo_name="AutoGPT", repo_url="https://github.com/x/AutoGPT"),),
        )
        with mock.patch.object(M, "initialize_state_store", return_value=store):
            out = M._inventory_repo_answer(SimpleNamespace())
        self.assertIn("AutoGPT", out)
        self.assertIn("JustHireMe", out)


if __name__ == "__main__":
    unittest.main()
