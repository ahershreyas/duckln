"""Plan 67: end-to-end integration tests for Plan Mode."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.ai_client import Provider
from duckln.config import AppConfig, resolve_config_paths, save_app_config
from duckln.conversation_agent import _plan_mode_nudge_for_message
from duckln.modes import ControlMode
from duckln.plan_mode import looks_like_multistep_request


class PlanModeIntegrationTest(unittest.TestCase):
    def _config(self, plan_mode_enabled: bool):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td.name})
        cfg = AppConfig(
            provider=Provider.OPENAI, model="gpt", api_key="k",
            mode=ControlMode.HOTL, plan_mode_enabled=plan_mode_enabled,
        )
        save_app_config(cfg, paths)
        return paths, cfg

    def test_multistep_detector_recognises_setup_requests(self) -> None:
        self.assertTrue(looks_like_multistep_request("set up justhireme"))
        self.assertTrue(looks_like_multistep_request("install pytorch"))
        self.assertTrue(looks_like_multistep_request("run the dev server please"))
        self.assertFalse(looks_like_multistep_request("hi"))
        self.assertFalse(looks_like_multistep_request("what time is it?"))

    def test_nudge_only_fires_when_plan_mode_on(self) -> None:
        paths_off, cfg_off = self._config(False)
        paths_on, cfg_on = self._config(True)
        self.assertIsNone(
            _plan_mode_nudge_for_message(
                message="install pytorch",
                current=cfg_off,
                config_dir=paths_off.config_dir,
            )
        )
        reply = _plan_mode_nudge_for_message(
            message="install pytorch",
            current=cfg_on,
            config_dir=paths_on.config_dir,
        )
        self.assertIsNotNone(reply)
        self.assertEqual(reply.intent, "plan_mode_nudge")

    def test_nudge_skips_non_multistep_messages(self) -> None:
        paths, cfg = self._config(True)
        reply = _plan_mode_nudge_for_message(
            message="how are you?",
            current=cfg,
            config_dir=paths.config_dir,
        )
        self.assertIsNone(reply)


class PlanModeBringupBranchTest(unittest.TestCase):
    def test_plan_mode_branch_no_llm_returns_friendly_error(self) -> None:
        from duckln.repo_bringup import _generate_plan_for_bringup
        from state.repo_catalog import RepoCatalogRecord

        repo = RepoCatalogRecord(
            name="x", repo_url="owner/x", stars=0,
            description="", category="", framework="", last_updated="",
        )
        with tempfile.TemporaryDirectory() as td:
            paths = resolve_config_paths({"DUCKLN_CONFIG_DIR": td})
            out: list[str] = []
            result = _generate_plan_for_bringup(
                repo=repo,
                current_mode=ControlMode.HOTL,
                paths=paths,
                execution_target="local",
                display=out.append,
                llm_client=None,  # forces "no provider configured" branch
            )
        self.assertFalse(result.verification_passed)
        joined = "\n".join(out)
        self.assertTrue(
            "no provider configured" in joined.lower()
            or "plan mode" in joined.lower()
        )


if __name__ == "__main__":
    unittest.main()
