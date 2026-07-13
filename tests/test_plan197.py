"""Plan 197 — Duckln MUST FIX a missing prerequisite (install + verify + resume), and when it
genuinely can't, give DETAILED numbered step-by-step remediation, never a one-line hint."""

from __future__ import annotations

import unittest
import unittest.mock as mock
from types import SimpleNamespace


class F1InstallPlan(unittest.TestCase):
    def test_official_commands_per_os(self):
        from duckln.vm import build_local_service_install_plan

        self.assertEqual(build_local_service_install_plan("multipass", "Darwin").command, "brew install --cask multipass")
        self.assertEqual(build_local_service_install_plan("multipass", "Linux").command, "sudo snap install multipass")
        self.assertEqual(build_local_service_install_plan("multipass", "Windows").command, "")  # no clean auto-install
        self.assertEqual(build_local_service_install_plan("docker", "Darwin").command, "brew install --cask docker")
        p = build_local_service_install_plan("multipass", "Darwin")
        self.assertEqual(p.verify_command, "multipass version")
        self.assertTrue(p.source_url)


class F7DetailedSteps(unittest.TestCase):
    def test_numbered_multi_step_not_a_hint(self):
        from duckln.vm import render_remediation_steps

        steps = render_remediation_steps("multipass", "Darwin", reason="declined")
        # detailed: multiple numbered lines, the command, a verify step, official URL, next action
        self.assertIn("1.", steps)
        self.assertIn("2.", steps)
        self.assertIn("brew install --cask multipass", steps)
        self.assertIn("multipass version", steps)          # verify step
        self.assertIn("https://multipass.run", steps)      # official source
        self.assertIn("/vm", steps)                        # exact resume action
        self.assertGreater(steps.count("\n"), 3)           # genuinely multi-line

    def test_windows_installer_walkthrough(self):
        from duckln.vm import render_remediation_steps

        steps = render_remediation_steps("multipass", "Windows", reason="unsupported")
        self.assertIn("installer", steps.lower())
        self.assertIn("multipass version", steps)

    def test_daemon_down_start_steps(self):
        from duckln.vm import render_remediation_steps

        steps = render_remediation_steps("docker", "Darwin", reason="daemon_down")
        self.assertIn("Docker Desktop", steps)
        self.assertIn("docker --version", steps)


class F1OfferInstall(unittest.TestCase):
    def _runner(self, code=0):
        return SimpleNamespace(run=lambda cmd, timeout_seconds=900: SimpleNamespace(exit_code=code, timed_out=False, stdout="", stderr=""))

    def test_yes_installs_and_verifies(self):
        from duckln import main as M

        out: list[str] = []
        with mock.patch.object(M, "_local_service_installed", return_value=True):
            ok = M._offer_local_service_install(
                service="multipass", runner=self._runner(0), approve_prompt=lambda p: True,
                select_prompt=None, display_output=out.append, system_probe=SimpleNamespace(operating_system="Darwin"),
            )
        self.assertTrue(ok)
        self.assertTrue(any("is installed" in x for x in out))

    def test_decline_shows_detailed_steps(self):
        from duckln import main as M

        out: list[str] = []
        ok = M._offer_local_service_install(
            service="multipass", runner=self._runner(0), approve_prompt=lambda p: False,
            select_prompt=None, display_output=out.append, system_probe=SimpleNamespace(operating_system="Darwin"),
        )
        self.assertFalse(ok)
        self.assertTrue(any("follow these steps" in x for x in out))

    def test_install_failed_shows_detailed_steps(self):
        from duckln import main as M

        out: list[str] = []
        with mock.patch.object(M, "_local_service_installed", return_value=False):
            ok = M._offer_local_service_install(
                service="multipass", runner=self._runner(0), approve_prompt=lambda p: True,
                select_prompt=None, display_output=out.append, system_probe=SimpleNamespace(operating_system="Darwin"),
            )
        self.assertFalse(ok)
        self.assertTrue(any("couldn't finish" in x for x in out))


class F6EnsureSeam(unittest.TestCase):
    def test_already_present_short_circuits(self):
        from duckln import main as M

        with mock.patch.object(M, "_local_service_installed", return_value=True):
            self.assertTrue(M._ensure_service_available(
                service="multipass", runner=SimpleNamespace(), approve_prompt=None,
                select_prompt=None, display_output=lambda _m: None,
            ))

    def test_missing_routes_to_offer(self):
        from duckln import main as M

        with mock.patch.object(M, "_local_service_installed", return_value=False), \
             mock.patch.object(M, "_offer_local_service_install", return_value=True) as offer:
            ok = M._ensure_service_available(
                service="multipass", runner=SimpleNamespace(), approve_prompt=None,
                select_prompt=None, display_output=lambda _m: None,
            )
        self.assertTrue(ok)
        offer.assert_called_once()


class F2VmFailureRecoveryInstalls(unittest.TestCase):
    def test_install_multipass_first_offers_and_retries(self):
        from duckln import main as M

        failure = SimpleNamespace(vm_name="duckln-vm")
        with mock.patch.object(M, "_classify_vm_failure", return_value="install_multipass_first"), \
             mock.patch.object(M, "_offer_local_service_install", return_value=True):
            action, cont = M._present_vm_failure_recovery(
                failure, SimpleNamespace(config_dir="."), lambda _m: None, None,
                system_probe=SimpleNamespace(operating_system="Darwin"),
            )
        self.assertEqual((action, cont), ("retry_vm_creation", True))

    def test_install_declined_does_not_retry(self):
        from duckln import main as M

        failure = SimpleNamespace(vm_name="duckln-vm")
        with mock.patch.object(M, "_classify_vm_failure", return_value="install_multipass_first"), \
             mock.patch.object(M, "_offer_local_service_install", return_value=False):
            action, cont = M._present_vm_failure_recovery(
                failure, SimpleNamespace(config_dir="."), lambda _m: None, None,
                system_probe=SimpleNamespace(operating_system="Darwin"),
            )
        self.assertFalse(cont)


class F5InventoryPointsToOffer(unittest.TestCase):
    def test_vm_answer_not_installed_points_to_vm(self):
        from duckln import main as M

        with mock.patch.object(M, "list_multipass_vms_or_none", return_value=None), \
             mock.patch.object(M, "is_multipass_installed", return_value=False):
            out = M._inventory_vm_answer(SimpleNamespace())
        self.assertIn("isn't installed", out)
        self.assertIn("/vm", out)


if __name__ == "__main__":
    unittest.main()
