"""Plan 123 — the REAL integration driver.

Drives a RepoSpec through Duckln's actual non-interactive bring-up
(`repo_bringup.bring_up_selected_repo`, NOT a mock): clone → inspect → classify →
specialist install/build → codegen/prebuild → run, with the deterministic-recovery
loop. Reports PASS/FAIL + the real failure class from `RepoBringUpResult`.

Only imported by the opt-in matrix runner (DUCKLN_INTEGRATION=1); never by the unit
suite. Heavyweight by design — it actually clones and builds real repos.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RowOutcome:
    passed: bool
    failure_class: str          # "" when passed; else e.g. "MISSING_MODULE" / "timeout"
    detail: str                 # short tail of captured output / message
    repo_family: str = ""
    timed_out: bool = False


def _tail(lines: list[str], n: int = 12) -> str:
    return "\n".join(lines[-n:]).strip()


def run_smoke(spec, *, timeout_seconds: float = 600.0) -> RowOutcome:
    """Plan 125: a synthetic, torch-free smoke (no repo clone). Builds a temp venv,
    pip-installs `smoke_pip`, runs `python -c smoke_check`; PASS on exit 0 + signal.
    Used for the lightweight Apple-Silicon (MLX) GPU check that fits 8GB."""
    import subprocess
    import sys as _sys

    tmp = Path(tempfile.mkdtemp(prefix=f"duckln-smoke-{spec.name}-"))
    try:
        venv = tmp / ".venv"
        py = venv / "bin" / "python"
        pip = venv / "bin" / "pip"
        steps = [
            [_sys.executable, "-m", "venv", str(venv)],
            [str(pip), "install", "--quiet", "--upgrade", "pip", *spec.smoke_pip],
            [str(py), "-c", spec.smoke_check],
        ]
        last = None
        for cmd in steps:
            last = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
            if last.returncode != 0:
                return RowOutcome(False, "smoke-failed", _tail((last.stdout + last.stderr).splitlines()))
        out = (last.stdout or "").strip()
        passed = (not spec.running_signal) or (spec.running_signal in out)
        return RowOutcome(passed, "" if passed else "no-signal", out[-300:])
    except subprocess.TimeoutExpired:
        return RowOutcome(False, "timeout", "", timed_out=True)
    except Exception as exc:  # noqa: BLE001
        return RowOutcome(False, "exception", f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_spec(spec, env: str, *, timeout_seconds: float = 1200.0) -> RowOutcome:
    """Run one (spec, env) through the real engine. Bounded by a thread join."""
    if getattr(spec, "stack", "") == "smoke":
        return run_smoke(spec, timeout_seconds=timeout_seconds)
    from duckln.config import ConfigPaths
    from duckln.modes import ControlMode
    from duckln.repo_bringup import bring_up_selected_repo
    from state.repo_catalog import RepoCatalogRecord
    from state.store import initialize_state_store

    # Plan 124: use the REAL hardware probe (MPS/arch/RAM/disk) so Mac/ML planning
    # sees the actual machine — matches what the live app passes in.
    try:
        from agent.probe import probe_system
        real_probe = probe_system()
    except Exception:
        real_probe = None

    tmp = Path(tempfile.mkdtemp(prefix=f"duckln-it-{spec.name}-"))
    captured: list[str] = []
    holder: dict[str, object] = {}

    def _display(msg: str) -> None:
        captured.append(str(msg))

    def _work() -> None:
        try:
            os.environ["DUCKLN_CONFIG_DIR"] = str(tmp)
            paths = ConfigPaths(config_dir=tmp, config_file=tmp / "config.json")
            try:
                initialize_state_store(tmp)
            except Exception:
                pass
            repo = RepoCatalogRecord(
                name=spec.name, repo_url=spec.url, stars=0, description="",
                category=spec.stack, framework="", last_updated="",
            )
            holder["result"] = bring_up_selected_repo(
                repo,
                ControlMode.HOOTLWO,
                paths,
                approve=lambda _msg: True,          # opt-in harness: auto-approve
                display=_display,
                execution_target=env,
                plan_mode_enabled=False,            # execute the real plan, don't just draft
                system_probe=real_probe,            # real MPS/arch/RAM, not a CPU stub
            )
        except Exception as exc:  # noqa: BLE001 — capture, never crash the matrix
            holder["error"] = exc

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout_seconds)
    try:
        if t.is_alive():
            return RowOutcome(False, "timeout", _tail(captured), timed_out=True)
        if "error" in holder:
            return RowOutcome(False, "exception", f"{type(holder['error']).__name__}: {holder['error']}\n{_tail(captured)}")
        result = holder.get("result")
        if result is None:
            return RowOutcome(False, "no-result", _tail(captured))

        text = "\n".join(captured)
        signal_hit = bool(spec.running_signal) and spec.running_signal in text
        passed = bool(getattr(result, "verification_passed", False)) or signal_hit
        if passed:
            return RowOutcome(True, "", _tail(captured), repo_family=str(getattr(result, "repo_family", "")))
        ft = getattr(result, "failure_type", None)
        failure_class = getattr(ft, "value", None) or getattr(ft, "name", None) or "unverified"
        detail = (getattr(result, "message", "") or "").strip() or _tail(captured)
        return RowOutcome(False, str(failure_class), detail, repo_family=str(getattr(result, "repo_family", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
