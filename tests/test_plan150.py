"""Plan 150 — clean-slate controls.
F1 fresh-install wipe removes everything INSTALLED but keeps the source/.git/lockfiles/.env.
F5 delete-target builds the right destroy command per VM/container/cloud (None for local).
F6 a custom repo can be removed from the SELECTION list (recent-custom store), no files touched.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckln.repo_bringup as rb


class FreshInstallWipe(unittest.TestCase):
    """F1: wipe installed artifacts, keep the user's source + git + lockfiles + .env."""

    def test_removes_installed_artifacts(self):
        cmd = rb._fresh_install_cleanup_command()
        for installed in ("node_modules", ".venv", "build", "dist", "target", "build_cache",
                           "__pycache__", ".duckln-deps-ok"):
            self.assertIn(installed, cmd)

    def test_keeps_source_git_lockfiles_env(self):
        cmd = rb._fresh_install_cleanup_command()
        for kept in (".git", "package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock",
                     "requirements.txt", ".env", "src/", "README"):
            self.assertNotIn(kept, cmd)

    def test_is_safe_repo_confined(self):
        cmd = rb._fresh_install_cleanup_command()
        self.assertNotIn("..", cmd)               # no parent traversal
        self.assertNotIn("rm -rf /", cmd)          # never an absolute root
        self.assertIn("|| true", cmd)              # best-effort, never aborts


class DeleteTarget(unittest.TestCase):
    """F5: the destroy command per target; None for local."""

    def test_vm(self):
        self.assertEqual(rb._delete_target_command(execution_target="vm", name="duckln-vm"),
                         "multipass delete duckln-vm --purge")

    def test_container(self):
        self.assertIn("docker rm -f -v", rb._delete_target_command(execution_target="container", name="duckln-box"))

    def test_aws(self):
        c = rb._delete_target_command(execution_target="aws", instance_id="i-0abc", region="us-east-1")
        self.assertIn("terminate-instances", c)
        self.assertIn("i-0abc", c)
        self.assertIn("us-east-1", c)

    def test_gcp(self):
        c = rb._delete_target_command(execution_target="gcp", name="duckln-gcp", region="us-central1-a")
        self.assertIn("gcloud compute instances delete", c)
        self.assertIn("--quiet", c)

    def test_local_and_missing_id_return_none(self):
        self.assertIsNone(rb._delete_target_command(execution_target="local", name="x"))
        self.assertIsNone(rb._delete_target_command(execution_target="vm", name=None))
        self.assertIsNone(rb._delete_target_command(execution_target="aws", instance_id=None))


class RemoveCustomRepoFromSelection(unittest.TestCase):
    """F6: forget a custom repo from the /repos picker list (no files touched)."""

    def test_delete_recent_custom_repo(self):
        from state.access import initialize_state_store
        with tempfile.TemporaryDirectory() as d:
            store = initialize_state_store(Path(d))
            store.upsert_recent_custom_repo(
                repo_url="https://github.com/vasu-devs/JustHireMe", repo_name="JustHireMe",
                stars=1, description="x", category="web", framework="TS", last_updated="2026-06-15",
            )
            self.assertTrue(any(r.repo_url.endswith("JustHireMe") for r in store.list_recent_custom_repos()))
            removed = store.delete_recent_custom_repo(repo_url="https://github.com/vasu-devs/JustHireMe")
            self.assertTrue(removed)
            self.assertFalse(any(r.repo_url.endswith("JustHireMe") for r in store.list_recent_custom_repos()))
            # idempotent: removing again is a no-op
            self.assertFalse(store.delete_recent_custom_repo(repo_url="https://github.com/vasu-devs/JustHireMe"))


if __name__ == "__main__":
    unittest.main()
