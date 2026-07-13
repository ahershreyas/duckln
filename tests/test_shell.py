"""Phase 7 alignment for controlled shell execution.

Req: R4
Plan: 5
Tasks:
- Unit tests for safety rules
"""

from __future__ import annotations

import json
import logging
import os
import unittest

from duckln.shell import ControlledCommandRunner, _looks_like_install_command, _unwrap_vm_pane_command


class _FakePaneExecutor:
    """Stand-in for SplitPaneChatInterface in run_in_pane unit tests."""

    def __init__(self, *, completed: bool, captured: list[str], exit_code: int) -> None:
        self.completed = completed
        self.captured = captured
        self.exit_code = exit_code
        self.calls: list[dict] = []

    def run_command_in_pane_with_capture(self, *, command: str, cwd: str | None = None, timeout_seconds: float = 60.0):
        self.calls.append({"command": command, "cwd": cwd, "timeout_seconds": timeout_seconds})
        return self.completed, list(self.captured), self.exit_code


class RunInPaneTest(unittest.TestCase):
    def test_run_in_pane_returns_command_result_for_successful_capture(self) -> None:
        runner = ControlledCommandRunner(execution_target="vm", vm_name="duckln-vm")
        executor = _FakePaneExecutor(completed=True, captured=["hello", "world"], exit_code=0)

        result = runner.run_in_pane("echo hello; echo world", pane_executor=executor, timeout_seconds=5.0)

        self.assertEqual(0, result.exit_code)
        self.assertEqual("hello\nworld", result.stdout)
        self.assertFalse(result.timed_out)
        self.assertEqual(1, len(executor.calls))
        self.assertEqual("echo hello; echo world", executor.calls[0]["command"])

    def test_run_in_pane_marks_timed_out_when_executor_did_not_complete(self) -> None:
        runner = ControlledCommandRunner(execution_target="vm", vm_name="duckln-vm")
        executor = _FakePaneExecutor(completed=False, captured=["partial line"], exit_code=-1)

        result = runner.run_in_pane("sleep 1000", pane_executor=executor, timeout_seconds=2.0)

        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertIn("timed out", result.stderr.lower())

    def test_run_in_pane_rejects_executor_without_capture_method(self) -> None:
        runner = ControlledCommandRunner()
        with self.assertRaises(RuntimeError):
            runner.run_in_pane("ls", pane_executor=object())


class ControlledCommandRunnerReqR4Plan5Test(unittest.TestCase):
    """Covers stdout/stderr capture and timeout handling."""

    def _parse_log_payload(self, entry: str) -> dict:
        return json.loads(entry[entry.find("{"):])

    def test_r4_plan5_runner_captures_stdout_and_stderr_separately(self) -> None:
        runner = ControlledCommandRunner()

        with self.assertLogs("duckln", level=logging.INFO) as captured:
            result = runner.run("printf 'hello'; printf 'oops' >&2", timeout_seconds=1)

        self.assertEqual("hello", result.stdout)
        self.assertEqual("oops", result.stderr)
        self.assertEqual(0, result.exit_code)
        self.assertFalse(result.timed_out)
        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("execution_result", payload["event"])
        self.assertEqual("True", payload["metadata"]["success"])

    def test_r4_plan5_runner_marks_timeout_and_kills_process(self) -> None:
        runner = ControlledCommandRunner()

        with self.assertLogs("duckln", level=logging.ERROR) as captured:
            result = runner.run("sleep 1", timeout_seconds=0.01)

        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertIn("timed out", result.stderr)
        payload = self._parse_log_payload(captured.output[0])
        self.assertEqual("execution_result", payload["event"])
        self.assertEqual("True", payload["metadata"]["timed_out"])

    def test_runner_emits_shared_trace_messages_when_trace_sink_is_present(self) -> None:
        traces: list[str] = []
        runner = ControlledCommandRunner(trace=traces.append, execution_target="local")

        result = runner.run("printf 'hello world'", timeout_seconds=1)

        self.assertEqual(0, result.exit_code)
        self.assertTrue(any("Duckln trace: shell command execution." in trace for trace in traces))
        self.assertTrue(any("Duckln trace: shell command result." in trace for trace in traces))
        self.assertTrue(any("shell.command_runner" in trace for trace in traces))

    def test_runner_returns_failure_result_when_cwd_does_not_exist(self) -> None:
        runner = ControlledCommandRunner()

        result = runner.run("printf 'should not run'", cwd="~/.duckln/definitely-missing-test-dir", timeout_seconds=1)

        self.assertEqual(127, result.exit_code)
        self.assertFalse(result.timed_out)
        self.assertIn("could not start", result.stderr.lower())

    def test_runner_expands_tilde_in_cwd(self) -> None:
        runner = ControlledCommandRunner()

        result = runner.run("pwd", cwd="~", timeout_seconds=1)

        self.assertEqual(0, result.exit_code)
        self.assertEqual(os.path.expanduser("~"), result.stdout.strip())


class UnwrapVmPaneCommandTest(unittest.TestCase):
    def test_strips_multipass_exec_wrapper_and_returns_inner_command(self) -> None:
        wrapped = "multipass exec duckln-vm -- bash -lc 'cd /home/ubuntu/projects && python app.py'"
        result = _unwrap_vm_pane_command(wrapped)
        self.assertEqual("cd /home/ubuntu/projects && python app.py", result)

    def test_leaves_plain_command_unchanged(self) -> None:
        plain = "python app.py"
        self.assertEqual(plain, _unwrap_vm_pane_command(plain))

    def test_leaves_non_multipass_command_unchanged(self) -> None:
        not_wrapped = "ssh user@host 'python app.py'"
        self.assertEqual(not_wrapped, _unwrap_vm_pane_command(not_wrapped))

    def test_returns_original_on_malformed_input(self) -> None:
        malformed = "multipass exec -- bash"
        result = _unwrap_vm_pane_command(malformed)
        self.assertEqual(malformed, result)

    def test_vm_pane_routing_strips_wrapper_before_sending_to_pane(self) -> None:
        captured_commands: list[str] = []

        class _FakeTerminalExecutor:
            def run_command_in_pane_with_capture(self, *, command: str, cwd, timeout_seconds: float):
                captured_commands.append(command)
                return (True, ["ok"], 0)

        executor = _FakeTerminalExecutor()
        runner = ControlledCommandRunner(execution_target="vm", pane_executor=executor)
        wrapped = "multipass exec duckln-vm -- bash -lc 'python app.py'"
        runner.run(wrapped, timeout_seconds=2)
        self.assertEqual(["python app.py"], captured_commands)

    def test_local_install_command_routes_through_visible_pane_when_available(self) -> None:
        executor = _FakePaneExecutor(completed=True, captured=["installed"], exit_code=0)
        runner = ControlledCommandRunner(execution_target="local", pane_executor=executor)

        result = runner.run("npm install -g openclaw@latest", cwd="/tmp/repo", timeout_seconds=2)

        self.assertEqual(0, result.exit_code)
        self.assertEqual("installed", result.stdout)
        self.assertEqual("npm install -g openclaw@latest", executor.calls[0]["command"])
        self.assertEqual("/tmp/repo", executor.calls[0]["cwd"])

    def test_install_command_detection_covers_readme_global_installs(self) -> None:
        self.assertTrue(_looks_like_install_command("npm install -g openclaw@latest"))
        self.assertTrue(_looks_like_install_command("pnpm add -g openclaw@latest"))
        self.assertFalse(_looks_like_install_command("openclaw gateway --port 18789"))


if __name__ == "__main__":
    unittest.main()
