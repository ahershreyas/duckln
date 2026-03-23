"""Controlled command execution interfaces for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time
from typing import Mapping

from duckln.logging_utils import log_execution_result


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_SHELL = "/bin/zsh"


@dataclass(frozen=True)
class CommandResult:
    """Captured result for a controlled shell command."""

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class ControlledCommandRunner:
    """Run shell commands with timeout and separated output capture."""

    def __init__(self, *, shell_path: str = DEFAULT_SHELL) -> None:
        self.shell_path = shell_path

    def run(
        self,
        command: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        started_at = time.monotonic()
        process = subprocess.Popen(
            [self.shell_path, "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=dict(env) if env is not None else None,
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            timed_out = False
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            timed_out = True
            exit_code = None
            stderr = f"{stderr}Duckln timed out this command after {timeout_seconds:.1f}s.".strip()

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
        return result
