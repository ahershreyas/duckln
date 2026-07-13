"""Controlled command execution interfaces for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import platform
import shlex
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping

from duckln.execution_trace import render_tool_invocation_trace
from duckln.logging_utils import log_execution_result


DEFAULT_TIMEOUT_SECONDS = 30.0


def _resolve_default_shell() -> str:
    """Pick a shell that exists on the host: $SHELL → bash → sh (Unix) / cmd.exe (Windows)."""

    if platform.system() == "Windows":
        comspec = os.environ.get("ComSpec")
        if comspec and Path(comspec).exists():
            return comspec
        return "cmd.exe"
    env_shell = os.environ.get("SHELL")
    if env_shell and Path(env_shell).exists():
        return env_shell
    for candidate in ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh", "/bin/zsh"):
        if Path(candidate).exists():
            return candidate
    return "/bin/sh"


DEFAULT_SHELL = _resolve_default_shell()


def _shell_command_argv(shell_path: str, command: str) -> list[str]:
    """Return argv for invoking `command` under `shell_path` portably."""

    base = Path(shell_path).name.lower()
    if base in {"cmd.exe", "cmd"}:
        return [shell_path, "/C", command]
    if base in {"powershell.exe", "powershell", "pwsh", "pwsh.exe"}:
        return [shell_path, "-NoProfile", "-Command", command]
    # POSIX shells: -l = login shell so user's PATH/aliases are available; -c runs the command.
    return [shell_path, "-lc", command]


def _prepare_cwd(cwd: str | None) -> str | None:
    """Expand shell-style home directories for subprocess cwd values."""

    if cwd is None:
        return None
    return str(Path(cwd).expanduser())


# Module-level abort event: set by the UI exit handler to cancel any running command.
_global_abort: threading.Event = threading.Event()


def request_global_abort() -> None:
    """Signal all ControlledCommandRunner.run() calls to terminate their subprocess."""
    _global_abort.set()


def clear_global_abort() -> None:
    """Clear the abort signal after the abort has been handled."""
    _global_abort.clear()


def is_global_abort_requested() -> bool:
    """Plan 198 F1: True when a user asked to stop/cancel the running operation. Long-running
    watch/poll loops check this each iteration and bail promptly (public accessor so callers
    don't reach into the private event)."""
    return _global_abort.is_set()


def _terminate_process_group(process: subprocess.Popen) -> None:
    """Best-effort SIGTERM then SIGKILL to the subprocess tree.

    Safe only when the process was started with start_new_session=True, making
    process.pid the process group leader so killpg targets only its tree.
    """
    try:
        if platform.system() == "Windows":
            process.terminate()
        else:
            # With start_new_session=True, process.pid IS the process group leader.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if platform.system() != "Windows":
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.kill()
    except Exception:
        pass


@dataclass(frozen=True)
class CommandResult:
    """Captured result for a controlled shell command."""

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


@dataclass(frozen=True)
class BackgroundCommandSession:
    """Metadata for a background command Duckln started intentionally."""

    command: str
    pid: int
    cwd: str | None
    log_path: str | None
    started_at: float


# Plan 136 F1: shell builtins / common tools that a model sometimes prefixes with a
# stray slash (`/cd`, `/sudo`, `/npm`). A leading `/<word>` is only a real command when
# it's an absolute path to an executable — these names never live at filesystem root, so
# a leading slash on them is a model typo we strip rather than run (→ exit 127).
_SLASH_TYPO_FIRST_TOKENS = frozenset({
    "cd", "sudo", "npm", "pnpm", "yarn", "bun", "pip", "pip3", "python", "python3",
    "node", "cargo", "go", "make", "apt", "apt-get", "git", "source", "export",
    "echo", "ls", "mkdir", "rm", "cp", "mv", "bash", "sh", "rustup", "brew",
})


def sanitize_shell_command(command: str) -> str:
    """Plan 136 F1: repair an obviously MALFORMED command before it runs, so a model
    hallucination like `/cd …` (stray leading slash on a shell builtin) never executes
    and fails with exit 127. Conservative: only strips a leading slash when the first
    token is a known builtin/tool that never lives at `/<name>` (a genuine absolute path
    like `/usr/bin/python` is left untouched). Also trims wrapping backticks / a leading
    `$ ` prompt artifact. Anything ambiguous is returned unchanged."""
    if not command:
        return command
    fixed = command.strip()
    # Strip a wrapping markdown code fence/backticks and a leading shell-prompt `$ `.
    if fixed.startswith("`") and fixed.endswith("`") and len(fixed) >= 2:
        fixed = fixed[1:-1].strip()
    if fixed.startswith("$ "):
        fixed = fixed[2:].lstrip()
    # Strip a stray leading slash on a builtin/tool first token (`/cd` → `cd`).
    if fixed.startswith("/"):
        first = fixed.split(maxsplit=1)[0]
        if first[1:] in _SLASH_TYPO_FIRST_TOKENS:
            fixed = fixed[1:]
    return fixed


def _unwrap_vm_pane_command(command: str) -> str:
    """Extract the inner shell command from a `multipass exec ... -- bash -lc '...'` wrapper.

    When ControlledCommandRunner routes through a pane executor, the pane shell IS
    already inside the VM (via `multipass shell`). Sending a host-side `multipass exec`
    wrapper to that shell fails with "Command 'multipass' not found". This function strips
    the wrapper so only the inner bash command is sent to the pane."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not (
        len(parts) >= 7
        and parts[0] == "multipass"
        and parts[1] == "exec"
        and "--" in parts
        and "bash" in parts
        and "-lc" in parts
    ):
        return command
    try:
        bash_idx = parts.index("bash", 2)
        lc_idx = parts.index("-lc", bash_idx)
        if lc_idx + 1 < len(parts):
            return parts[lc_idx + 1]
    except (ValueError, IndexError):
        pass
    return command


def _looks_like_install_command(command: str) -> bool:
    normalized = " ".join(command.casefold().split())
    return any(
        marker in f" {normalized} "
        for marker in (
            " pip install ",
            " -m pip install ",
            " npm install ",
            " pnpm install ",
            " pnpm add ",
            " yarn install ",
            " yarn add ",
            " bun install ",
            " conda env create ",
            " cargo build ",
            " cargo install ",
            " go mod download ",
            " go build ",
            " docker build ",
            " docker compose build ",
            " sudo apt install ",
            " sudo apt-get install ",
            " apt install ",
            " apt-get install ",
            " sudo snap install ",
        )
    )


def _recommended_timeout_seconds(command: str, requested_timeout_seconds: float) -> float:
    """Use longer bounded timeouts for heavy setup/install commands.

    Keep caller-provided timeouts when they are already larger.
    """

    timeout_seconds = max(float(requested_timeout_seconds), 1.0)
    normalized = " ".join(str(command or "").casefold().split())
    install_like = _looks_like_install_command(command)
    long_setup_tokens = (
        " git clone ",
        " pnpm install ",
        " npm install ",
        " yarn install ",
        " pip install ",
        " docker compose up ",
        " docker compose build ",
        " docker build ",
        " apt-get update ",
        " apt update ",
    )
    # Plan 135 F1: a build/prebuild script (`npm run build:*`, `cargo build`, a Tauri
    # compile, make, gradle, etc.) can run for MINUTES — a Rust/Tauri build of hundreds
    # of crates on a small VM far exceeds 240s. Recognize these (incl. `npm run <script>`
    # which internally drives the compiler) and give them a generous BUILD-TIER cap so a
    # healthy-but-slow build is never killed mid-compile. Signal-keyed, repo-agnostic.
    padded = f" {normalized} "
    build_tier_tokens = (
        " run build", " run tauri", " run vector", " run sidecar", " run runtime",
        " cargo build", " cargo test", " cargo tauri", " tauri build", " tauri dev",
        " make ", " cmake --build", " gradle ", " ./gradlew", " mvn ", " go build",
        " vite build", " tsc -b", " webpack", " next build", " ninja ",
    )
    build_tier = (
        any(token in padded for token in build_tier_tokens)
        or (
            # any package-manager `run` of a build-shaped script (flags like
            # `--if-present` may sit between `run` and the script name).
            bool(re.search(r"\b(npm|pnpm|yarn|bun)\s+run\b", normalized))
            and any(k in normalized for k in (
                "build", "tauri", "sidecar", "runtime", "vector", "compile", "bundle", "dist",
            ))
        )
    )
    if build_tier:
        return max(timeout_seconds, 1800.0)
    if install_like or any(token in padded for token in long_setup_tokens):
        return max(timeout_seconds, 240.0)
    return timeout_seconds


class ControlledCommandRunner:
    """Run shell commands with timeout and separated output capture."""

    def __init__(
        self,
        *,
        shell_path: str = DEFAULT_SHELL,
        trace: Callable[[str], None] | None = None,
        execution_target: str = "local",
        vm_name: str | None = None,
        pane_executor: object | None = None,
    ) -> None:
        self.shell_path = shell_path
        self.trace = trace
        self.execution_target = execution_target
        self.vm_name = vm_name
        # Item 1: when set and target is vm, run() routes the unwrapped command into the
        # user's open pane shell via the sentinel-capture path (run_in_pane) instead of
        # spawning a hidden `multipass exec` subprocess. Falls back to subprocess silently
        # if the pane returns a transport error.
        self.pane_executor = pane_executor

    def run_in_pane(
        self,
        command: str,
        *,
        pane_executor: object,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cwd: str | None = None,
    ) -> CommandResult:
        """Item 1: route the command through `pane_executor.run_command_in_pane_with_capture`
        so it executes inside the user-visible pane shell. Caller is responsible for
        ensuring the pane shell is already inside the right context (e.g. a `multipass shell`
        session for VM target). Returns a CommandResult-shaped tuple so callers can treat
        the outcome identically to `run`.
        """

        capture_method = getattr(pane_executor, "run_command_in_pane_with_capture", None)
        if not callable(capture_method):
            raise RuntimeError("pane_executor lacks run_command_in_pane_with_capture")
        effective_timeout = _recommended_timeout_seconds(command, timeout_seconds)
        started_at = time.monotonic()
        completed, captured_lines, exit_code = capture_method(
            command=command,
            cwd=cwd,
            timeout_seconds=effective_timeout,
        )
        duration = time.monotonic() - started_at
        stdout = "\n".join(captured_lines)
        if completed:
            stderr = ""
            timed_out = False
        else:
            stderr = f"Duckln pane execution timed out after {effective_timeout:.1f}s."
            timed_out = True
        result = CommandResult(
            command=command,
            exit_code=None if timed_out else exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_seconds=duration,
        )
        log_execution_result(
            command=result.command,
            success=(not result.timed_out and result.exit_code == 0),
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )
        return result

    def run_streaming(
        self,
        command: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Run a command and call on_line for every output line as it arrives.

        Stdout and stderr are merged so the user sees output in chronological order.
        Suitable for long-running commands where progress visibility matters.
        """

        effective_timeout = _recommended_timeout_seconds(command, timeout_seconds)
        started_at = time.monotonic()
        actual_command = command
        if self.execution_target == "vm" and self.vm_name:
            actual_command = f"multipass exec {self.vm_name} -- bash -lc {shlex.quote(command)}"
        resolved_cwd = _prepare_cwd(cwd)
        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "cwd": resolved_cwd,
            "env": dict(env) if env is not None else None,
        }
        if platform.system() != "Windows":
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(_shell_command_argv(self.shell_path, actual_command), **popen_kwargs)
        except FileNotFoundError as exc:
            return self._command_start_failure_result(command, started_at=started_at, error=exc)

        _abort_flag = _global_abort
        collected: list[str] = []
        timed_out = False

        def _abort_watchdog() -> None:
            while not _abort_flag.wait(timeout=0.25):
                if process.poll() is not None:
                    return
            if process.poll() is None:
                _terminate_process_group(process)

        watchdog = threading.Thread(target=_abort_watchdog, daemon=True)
        watchdog.start()

        try:
            assert process.stdout is not None
            deadline = started_at + effective_timeout
            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                collected.append(line)
                if on_line is not None:
                    try:
                        on_line(line.rstrip("\n"))
                    except Exception:
                        pass
                if time.monotonic() > deadline:
                    timed_out = True
                    _terminate_process_group(process)
                    break
            process.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process)
        except KeyboardInterrupt:
            _terminate_process_group(process)
            raise
        finally:
            watchdog.join(timeout=1)

        if _abort_flag.is_set():
            raise KeyboardInterrupt

        stdout = "".join(collected)
        stderr = ""
        if timed_out:
            stderr = f"Duckln timed out this command after {effective_timeout:.1f}s."
        duration_seconds = time.monotonic() - started_at
        result = CommandResult(
            command=command,
            exit_code=None if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_seconds=duration_seconds,
        )
        log_execution_result(
            command=result.command,
            success=(not result.timed_out and result.exit_code == 0),
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )
        return result

    def run(
        self,
        command: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        effective_timeout = _recommended_timeout_seconds(command, timeout_seconds)

        # Item 1 + Item 4: pane-routed remote execution. Runs the raw command inside the
        # user's open pane shell when the target is a remote runtime (vm / aws / gcp) and
        # a pane_executor is wired. Falls back to subprocess on any pane error.
        should_route_to_pane = self.execution_target in {"vm", "aws", "gcp"} or _looks_like_install_command(command)
        if should_route_to_pane and self.pane_executor is not None and hasattr(self.pane_executor, "run_command_in_pane_with_capture"):
            try:
                # For VM: the pane shell IS already inside the VM (via `multipass shell`),
                # so strip any `multipass exec ... -- bash -lc '...'` wrapper before sending.
                # Sending that wrapper to the VM shell fails with "multipass: not found".
                pane_command = _unwrap_vm_pane_command(command) if self.execution_target == "vm" else command
                # Plan 136 F1: repair an obviously malformed command (e.g. a model-typo'd
                # `/cd …`) before it runs, so it never fails with exit 127.
                pane_command = sanitize_shell_command(pane_command)
                return self.run_in_pane(
                    pane_command,
                    pane_executor=self.pane_executor,
                    timeout_seconds=effective_timeout,
                    cwd=cwd,
                )
            except Exception:
                # Fall back to subprocess on any pane-routing failure so we never block.
                pass
        self._emit_trace(
            render_tool_invocation_trace(
                title="shell command execution",
                tool_id="shell.command_runner",
                action="Run a bounded shell command",
                detail_lines=(
                    f"Command: {command}",
                    f"Working directory: {_prepare_cwd(cwd) or os.getcwd()}",
                    f"Timeout: {effective_timeout:.1f}s",
                ),
                execution_target=self.execution_target,
            )
        )
        started_at = time.monotonic()
        actual_command = command
        if self.execution_target == "vm" and self.vm_name:
            actual_command = f"multipass exec {self.vm_name} -- bash -lc {shlex.quote(command)}"
        resolved_cwd = _prepare_cwd(cwd)
        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "cwd": resolved_cwd,
            "env": dict(env) if env is not None else None,
        }
        if platform.system() != "Windows":
            # Own session → own process group, so killpg is safe and kills the full tree.
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(_shell_command_argv(self.shell_path, actual_command), **popen_kwargs)
        except FileNotFoundError as exc:
            return self._command_start_failure_result(command, started_at=started_at, error=exc)

        # Watchdog: if the global abort event is set (e.g. UI exit), kill the subprocess.
        _abort_flag = _global_abort

        def _abort_watchdog() -> None:
            while not _abort_flag.wait(timeout=0.25):
                if process.poll() is not None:
                    return
            if process.poll() is None:
                _terminate_process_group(process)

        watchdog = threading.Thread(target=_abort_watchdog, daemon=True)
        watchdog.start()

        try:
            stdout, stderr = process.communicate(timeout=effective_timeout)
            timed_out = False
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            stdout, stderr = process.communicate()
            timed_out = True
            exit_code = None
            stderr = f"{stderr}Duckln timed out this command after {effective_timeout:.1f}s.".strip()
        except KeyboardInterrupt:
            _terminate_process_group(process)
            raise
        finally:
            watchdog.join(timeout=1)

        if _abort_flag.is_set():
            raise KeyboardInterrupt

        duration_seconds = time.monotonic() - started_at
        result = CommandResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_seconds=duration_seconds,
        )
        log_execution_result(
            command=result.command,
            success=(not result.timed_out and result.exit_code == 0),
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )
        outcome = "timed out" if result.timed_out else f"exit code {result.exit_code}"
        self._emit_trace(
            render_tool_invocation_trace(
                title="shell command result",
                tool_id="shell.command_runner",
                action="Completed bounded shell command",
                detail_lines=(
                    f"Command: {result.command}",
                    f"Outcome: {outcome}",
                    f"Duration: {result.duration_seconds:.2f}s",
                ),
                execution_target=self.execution_target,
            )
        )
        return result

    def start_background(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        log_path: str | Path | None = None,
    ) -> BackgroundCommandSession:
        """Start a detached background shell command and return bounded session metadata."""

        self._emit_trace(
            render_tool_invocation_trace(
                title="background shell launch",
                tool_id="shell.command_runner",
                action="Start a detached bounded shell command",
                detail_lines=(
                    f"Command: {command}",
                    f"Working directory: {_prepare_cwd(cwd) or os.getcwd()}",
                    f"Log path: {str(log_path) if log_path is not None else 'not recorded'}",
                ),
                execution_target=self.execution_target,
            )
        )
        started_at = time.time()
        stdout_target = subprocess.DEVNULL
        stderr_target = subprocess.DEVNULL
        log_handle = None
        if log_path is not None:
            resolved_log_path = Path(log_path)
            resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = resolved_log_path.open("a", encoding="utf-8")
            stdout_target = log_handle
            stderr_target = subprocess.STDOUT
        else:
            resolved_log_path = None

        kwargs: dict[str, object] = {
            "stdout": stdout_target,
            "stderr": stderr_target,
            "cwd": _prepare_cwd(cwd),
            "env": dict(env) if env is not None else None,
            "text": True,
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(_shell_command_argv(self.shell_path, command), **kwargs)
        finally:
            if log_handle is not None:
                log_handle.close()

        session = BackgroundCommandSession(
            command=command,
            pid=process.pid,
            cwd=cwd,
            log_path=str(resolved_log_path) if resolved_log_path is not None else None,
            started_at=started_at,
        )
        self._emit_trace(
            render_tool_invocation_trace(
                title="background shell session ready",
                tool_id="shell.command_runner",
                action="Bounded background shell session started",
                detail_lines=(
                    f"Command: {command}",
                    f"PID: {session.pid}",
                ),
                execution_target=self.execution_target,
            )
        )
        return session

    def _command_start_failure_result(
        self,
        command: str,
        *,
        started_at: float,
        error: FileNotFoundError,
    ) -> CommandResult:
        duration_seconds = time.monotonic() - started_at
        result = CommandResult(
            command=command,
            exit_code=127,
            stdout="",
            stderr=f"Duckln could not start this command: {error}",
            timed_out=False,
            duration_seconds=duration_seconds,
        )
        log_execution_result(
            command=result.command,
            success=False,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )
        self._emit_trace(
            render_tool_invocation_trace(
                title="shell command result",
                tool_id="shell.command_runner",
                action="Failed to start bounded shell command",
                detail_lines=(
                    f"Command: {result.command}",
                    f"Outcome: start failed ({result.stderr})",
                    f"Duration: {result.duration_seconds:.2f}s",
                ),
                execution_target=self.execution_target,
            )
        )
        return result

    def stop_background(self, pid: int) -> bool:
        """Best-effort stop for a detached background session."""

        self._emit_trace(
            render_tool_invocation_trace(
                title="background shell stop",
                tool_id="shell.command_runner",
                action="Stop a bounded background shell session",
                detail_lines=(f"PID: {pid}",),
                execution_target=self.execution_target,
            )
        )
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                stopped = result.returncode == 0
                self._emit_trace(
                    render_tool_invocation_trace(
                        title="background shell stop result",
                        tool_id="shell.command_runner",
                        action="Finished background stop attempt",
                        detail_lines=(f"PID: {pid}", f"Stopped: {'yes' if stopped else 'no'}"),
                        execution_target=self.execution_target,
                    )
                )
                return stopped
            os.killpg(pid, signal.SIGTERM)
            self._emit_trace(
                render_tool_invocation_trace(
                    title="background shell stop result",
                    tool_id="shell.command_runner",
                    action="Finished background stop attempt",
                    detail_lines=(f"PID: {pid}", "Stopped: yes"),
                    execution_target=self.execution_target,
                )
            )
            return True
        except ProcessLookupError:
            self._emit_trace(
                render_tool_invocation_trace(
                    title="background shell stop result",
                    tool_id="shell.command_runner",
                    action="Finished background stop attempt",
                    detail_lines=(f"PID: {pid}", "Stopped: no"),
                    execution_target=self.execution_target,
                )
            )
            return False
        except PermissionError:
            self._emit_trace(
                render_tool_invocation_trace(
                    title="background shell stop result",
                    tool_id="shell.command_runner",
                    action="Finished background stop attempt",
                    detail_lines=(f"PID: {pid}", "Stopped: no"),
                    execution_target=self.execution_target,
                )
            )
            return False
        except OSError:
            self._emit_trace(
                render_tool_invocation_trace(
                    title="background shell stop result",
                    tool_id="shell.command_runner",
                    action="Finished background stop attempt",
                    detail_lines=(f"PID: {pid}", "Stopped: no"),
                    execution_target=self.execution_target,
                )
            )
            return False

    def _emit_trace(self, message: str) -> None:
        if self.trace is not None and message:
            self.trace(message)
