"""Plan 152 — "already set up" means VERIFIED: assess health, then a FIX-ONLY repair plan.
F1 assess_existing_setup → (healthy, concrete issues). F3 build_fix_only_plan_steps → minimal
   repair step(s) for the detected issue, NOT a full setup.
"""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb


class AssessExistingSetup(unittest.TestCase):
    def test_healthy_when_no_partial(self):
        healthy, issues = rb.assess_existing_setup({"node": "20"})
        self.assertTrue(healthy)
        self.assertEqual(issues, ())

    def test_partial_venv_is_unhealthy(self):
        healthy, issues = rb.assess_existing_setup({"partial": "venv"})
        self.assertFalse(healthy)
        self.assertTrue(any("virtual environment" in i for i in issues))

    def test_partial_multiple_and_verification_fail(self):
        healthy, issues = rb.assess_existing_setup({"partial": "node_modules,dpkg"}, verification_passed=False)
        self.assertFalse(healthy)
        self.assertTrue(any("node_modules" in i for i in issues))
        self.assertTrue(any("system package" in i for i in issues))
        self.assertTrue(any("verification" in i or "run check" in i for i in issues))


class FixOnlyPlan(unittest.TestCase):
    def test_venv_issue_yields_only_venv_step(self):
        steps = rb.build_fix_only_plan_steps(("the Python virtual environment is incomplete (no completion marker)",))
        self.assertEqual(len(steps), 1)
        title, cmd, _sc = steps[0]
        self.assertIn("venv", title.lower())
        self.assertEqual(cmd, rb._SECONDARY_PY_SETUP_CMD)   # reuses the deterministic builder

    def test_node_modules_issue_yields_install_step(self):
        steps = rb.build_fix_only_plan_steps(("node_modules is incomplete (a prior install was interrupted)",),
                                             package_manager="pnpm")
        self.assertEqual(len(steps), 1)
        self.assertIn("rm -rf node_modules && pnpm install", steps[0][1])

    def test_dpkg_issue_yields_repair_step(self):
        steps = rb.build_fix_only_plan_steps(("the system package state is broken (dpkg needs configuring)",))
        self.assertIn("dpkg --configure -a", steps[0][1])

    def test_is_scoped_not_full_setup(self):
        # multiple issues → exactly that many repair steps, deduped — never a 12-step setup.
        steps = rb.build_fix_only_plan_steps((
            "the Python virtual environment is incomplete (no completion marker)",
            "node_modules is incomplete (a prior install was interrupted)",
        ))
        self.assertEqual(len(steps), 2)

    def test_novel_issue_routed_for_investigation(self):
        steps = rb.build_fix_only_plan_steps(("a CUDA driver mismatch blocks the app",))
        self.assertEqual(len(steps), 1)
        self.assertIn("Investigate", steps[0][0])
        self.assertEqual(steps[0][1], "")   # empty command → caller routes to reasoned recovery


if __name__ == "__main__":
    unittest.main()
