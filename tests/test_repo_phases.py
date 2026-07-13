from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln.harness.tools import AgentContext, build_default_registry
from duckln.modes import ControlMode
from duckln.repo_agent import run_repo_task
from state.access import (
    read_repo_memory,
    read_tool_policy,
    read_user_preferences,
    write_repo_memory,
    write_tool_policy,
    write_user_preference,
)
from state.store import MemoryStore, initialize_state_store, set_state_store_factory


def _repo(tmp: str) -> Path:
    d = Path(tmp) / "repo"
    d.mkdir()
    (d / "a.py").write_text("x = 1\nfoo = 2\n", encoding="utf-8")
    return d


def _ctx(tmp, repo, *, mode=ControlMode.HOOTLWO, approve=None, policy=None):
    return AgentContext(
        agent_name="t", mode=mode, config_dir=Path(tmp), project_dir=repo,
        execution_target="local", approve=approve, extra={"tool_policy": policy or {}},
    )


# --- Phase 2: filesystem tools + offload --------------------------------------


class TestFilesystemTools(unittest.TestCase):
    def setUp(self):
        self.reg = build_default_registry(include_handlers=True)

    def test_search_returns_file_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            r = self.reg.dispatch("fs.search", {"pattern": "foo"}, _ctx(tmp, repo))
            self.assertTrue(r.ok)
            self.assertIn("a.py:2", r.payload["text"])

    def test_glob_lists_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            r = self.reg.dispatch("fs.glob", {"pattern": "**/*.py"}, _ctx(tmp, repo))
            self.assertIn("a.py", r.payload["matches"])

    def test_write_and_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            self.assertTrue(self.reg.dispatch("fs.write", {"path": "b.txt", "content": "hi"}, _ctx(tmp, repo)).ok)
            self.assertEqual((repo / "b.txt").read_text(), "hi")
            r = self.reg.dispatch("fs.edit", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 9"}, _ctx(tmp, repo))
            self.assertTrue(r.ok)
            self.assertIn("x = 9", (repo / "a.py").read_text())

    def test_edit_not_unique_requires_replace_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            (repo / "a.py").write_text("v=1\nv=1\n")
            r = self.reg.dispatch("fs.edit", {"path": "a.py", "old_string": "v=1", "new_string": "v=2"}, _ctx(tmp, repo))
            self.assertFalse(r.ok)
            self.assertEqual(r.error_code, "not_unique")

    def test_write_path_escape_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            r = self.reg.dispatch("fs.write", {"path": "../evil.txt", "content": "x"}, _ctx(tmp, repo))
            self.assertFalse(r.ok)
            self.assertEqual(r.error_code, "path_escape")
            self.assertFalse((Path(tmp) / "evil.txt").exists())

    def test_large_result_offloads_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            (repo / "big.txt").write_text("A" * 20000)
            r = self.reg.dispatch("fs.read_file", {"path": "big.txt"}, _ctx(tmp, repo))
            self.assertTrue(r.ok)
            self.assertIn("offloaded_to", r.payload)
            self.assertTrue(Path(r.payload["offloaded_to"]).exists())
            self.assertIn("head", r.payload)

    def test_git_blocks_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            r = self.reg.dispatch("git.run", {"args": "push origin main"}, _ctx(tmp, repo))
            self.assertFalse(r.ok)
            self.assertEqual(r.error_code, "git_blocked")


# --- Phase 5a: configurable HITL gate -----------------------------------------


class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.reg = build_default_registry(include_handlers=True)

    def test_hotl_prompts_and_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            ctx = _ctx(tmp, repo, mode=ControlMode.HOTL, approve=lambda p: False)
            r = self.reg.dispatch("fs.write", {"path": "x.txt", "content": "no"}, ctx)
            self.assertEqual(r.error_code, "user_denied")
            self.assertFalse((repo / "x.txt").exists())

    def test_policy_allow_skips_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)

            def _boom(_p):
                raise AssertionError("should not prompt when policy=allow")

            ctx = _ctx(tmp, repo, mode=ControlMode.HOTL, approve=_boom, policy={"fs.write": "allow"})
            self.assertTrue(self.reg.dispatch("fs.write", {"path": "ok.txt", "content": "y"}, ctx).ok)

    def test_policy_deny_blocks_even_in_hootlwo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            ctx = _ctx(tmp, repo, mode=ControlMode.HOOTLWO, policy={"fs.write": "deny"})
            r = self.reg.dispatch("fs.write", {"path": "z.txt", "content": "y"}, ctx)
            self.assertEqual(r.error_code, "policy_denied")


# --- Phase 4: orchestrated subagents (isolated contexts) ----------------------


class _ScriptedLLM:
    def __init__(self, loop_turns):
        self._turns = list(loop_turns)

    def __call__(self, *, system_prompt: str, user_message: str) -> str:
        if "OUTPUT FORMAT" in system_prompt:
            return self._turns.pop(0) if self._turns else '{"stop": true, "reason": "done"}'
        if "engineering task" in system_prompt:
            return "Changed `a.py`: x = 1 → x = 2. Verification: not run."
        return "Relevant file: `a.py`."


class TestOrchestratedTask(unittest.TestCase):
    def test_delegate_explorer_then_engineer_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(tmp)
            llm = _ScriptedLLM([
                # Explorer (repo_qa) context
                '{"tool": "fs.list_dir", "args": {"path": "."}, "reason": "layout"}',
                '{"stop": true, "reason": "found a.py"}',
                # Engineer (repo_engineer) context — fresh
                '{"tool": "fs.read_file", "args": {"path": "a.py"}, "reason": "read"}',
                '{"tool": "fs.edit", "args": {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, "reason": "change"}',
                '{"stop": true, "reason": "done"}',
            ])
            thoughts: list[str] = []
            outcome = run_repo_task(
                task="set x to 2 in a.py",
                config_dir=Path(tmp),
                repo_name="repo",
                project_dir=repo,
                mode=ControlMode.HOOTLWO,
                emit_thought=thoughts.append,
                llm_client=llm,
                delegate=True,
            )
            self.assertIn("x = 2", (repo / "a.py").read_text())  # the edit happened
            self.assertIn("a.py", outcome.answer)
            self.assertIn("fs.edit", outcome.tools_used)
            self.assertTrue(any("Explorer" in t for t in thoughts))
            self.assertTrue(any("Engineer" in t for t in thoughts))


# --- Phase 5b: persistence (policy / prefs / repo memory / pluggable store) ----


class TestPersistence(unittest.TestCase):
    def test_tool_policy_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tool_policy(Path(tmp), tool="fs.write", decision="deny")
            write_tool_policy(Path(tmp), tool="fs.read_file", decision="allow")
            self.assertEqual(read_tool_policy(Path(tmp)), {"fs.write": "deny", "fs.read_file": "allow"})
            write_tool_policy(Path(tmp), tool="fs.write", decision="default")
            self.assertNotIn("fs.write", read_tool_policy(Path(tmp)))

    def test_user_prefs_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_user_preference(Path(tmp), key="answer_style", value="concise")
            self.assertEqual(read_user_preferences(Path(tmp))["answer_style"], "concise")

    def test_repo_memory_persists_and_trims(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(20):
                write_repo_memory(Path(tmp), repo_slug="demo", question=f"q{i}", answer=f"a{i}")
            mem = read_repo_memory(Path(tmp), "demo")
            self.assertLessEqual(len(mem), 12)
            self.assertEqual(mem[-1]["q"], "q19")  # newest kept

    def test_pluggable_store_factory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = initialize_state_store(Path(tmp))
            self.assertIsInstance(store, MemoryStore)
            calls = {"n": 0}
            real = type(store)

            def _factory(cfg):
                calls["n"] += 1
                from state.store import resolve_state_store_paths, SQLiteStateStore
                return SQLiteStateStore(resolve_state_store_paths(cfg).database_file)

            try:
                set_state_store_factory(_factory)
                s2 = initialize_state_store(Path(tmp))
                self.assertIsInstance(s2, MemoryStore)
                self.assertGreaterEqual(calls["n"], 1)
            finally:
                set_state_store_factory(None)


# --- Phase 1 (P97): environment-agnostic file ops (command construction) ------


class TestRemoteFsCommands(unittest.TestCase):
    def test_remote_safe_join_confines(self):
        from duckln.harness.tools import _remote_safe_join
        self.assertEqual(_remote_safe_join("/home/ubuntu/repo", "a.py"), "/home/ubuntu/repo/a.py")
        self.assertEqual(_remote_safe_join("/home/ubuntu/repo", "sub/x"), "/home/ubuntu/repo/sub/x")
        self.assertIsNone(_remote_safe_join("/home/ubuntu/repo", "../../etc/passwd"))

    def test_remote_command_builders(self):
        from duckln.harness.tools import (
            _remote_read_command, _remote_search_command, _remote_write_command, _remote_glob_command,
        )
        self.assertIn("head -c", _remote_read_command("/r/a.py", 1024))
        self.assertIn("grep -rnI", _remote_search_command("foo", "/r", 50))
        w = _remote_write_command("/r/b.txt", "hi")
        self.assertIn("base64 -d", w)
        self.assertIn("mkdir -p", w)
        self.assertIn("glob", _remote_glob_command("**/*.py", "/r", 100))

    def test_container_wrap_uses_docker_exec(self):
        import tempfile
        from duckln.repo_bringup import _wrap_command_for_execution_target
        with tempfile.TemporaryDirectory() as t:
            wrapped, meta = _wrap_command_for_execution_target(
                config_dir=Path(t), execution_target="container",
                command="echo hi", cwd="/app", preferred_vm_name="mybox",
            )
            self.assertIsNotNone(wrapped)
            self.assertIn("docker exec", wrapped)
            self.assertIn("mybox", wrapped)
            self.assertIn("cd", wrapped)


# --- Phase 2 (P98): browser reach on any target ------------------------------


class TestExposeAppAndOpen(unittest.TestCase):
    def test_local_opens_served_url(self):
        from unittest import mock
        import duckln.repo_bringup as rb
        opened = {}
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(rb, "_open_in_local_browser", side_effect=lambda url, **k: opened.update(u=url)):
                url, note = rb.expose_app_and_open(
                    "http://localhost:3000/", execution_target="local", vm_name=None,
                    config_dir=Path(t), display=lambda _m: None,
                )
        self.assertEqual(url, "http://localhost:3000/")
        self.assertEqual(opened["u"], "http://localhost:3000/")

    def test_vm_uses_ip_when_reachable(self):
        from unittest import mock
        import duckln.repo_bringup as rb
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(rb, "_vm_ipv4", return_value="192.168.2.4"), \
                 mock.patch.object(rb, "_vm_port_reachable", return_value=True), \
                 mock.patch.object(rb, "_open_in_local_browser", lambda url, **k: None):
                url, note = rb.expose_app_and_open(
                    "http://localhost:3000/", execution_target="vm", vm_name="duckln-vm",
                    config_dir=Path(t), display=lambda _m: None,
                )
        self.assertIn("192.168.2.4:3000", url)

    def test_vm_unreachable_gives_remedy_not_dead_link(self):
        from unittest import mock
        import duckln.repo_bringup as rb
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(rb, "_vm_ipv4", return_value="192.168.2.4"), \
                 mock.patch.object(rb, "_vm_port_reachable", return_value=False):
                url, note = rb.expose_app_and_open(
                    "http://localhost:3000/", execution_target="vm", vm_name="duckln-vm",
                    config_dir=Path(t), display=lambda _m: None,
                )
        self.assertIsNone(url)
        self.assertIn("ufw allow 3000", note)

    def test_container_published_url(self):
        from unittest import mock
        from types import SimpleNamespace
        import duckln.repo_bringup as rb
        with tempfile.TemporaryDirectory() as t:
            fake = SimpleNamespace(stdout="0.0.0.0:32770\n", exit_code=0)
            with mock.patch("duckln.shell.ControlledCommandRunner") as Runner, \
                 mock.patch.object(rb, "_open_in_local_browser", lambda url, **k: None):
                Runner.return_value.run.return_value = fake
                url, note = rb.expose_app_and_open(
                    "http://localhost:3000/", execution_target="container", vm_name="mybox",
                    config_dir=Path(t), display=lambda _m: None,
                )
        self.assertEqual(url, "http://localhost:32770/")

    def test_cloud_tunnel_command_built(self):
        # The tunnel command builder produces a backgrounded ssh -L for aws.
        from types import SimpleNamespace
        from unittest import mock
        from duckln import cloud_runtime as cr
        rec = SimpleNamespace(provider="aws")
        with mock.patch.object(cr, "build_cloud_remote_session_plan", return_value=SimpleNamespace(attach_command="ssh ubuntu@1.2.3.4")):
            cmd = cr.build_cloud_tunnel_command(rec, local_port=3000, remote_port=3000)
        self.assertIn("ssh ubuntu@1.2.3.4", cmd)
        self.assertIn("-L 3000:localhost:3000", cmd)
        self.assertIn("-N -f", cmd)


if __name__ == "__main__":
    unittest.main()
