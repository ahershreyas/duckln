"""Plan 118/123 — integration matrix runner (opt-in, REAL).

  DUCKLN_INTEGRATION=1 PYTHONPATH=src python -m tests.integration.run
  # restrict environments / rows:
  DUCKLN_INTEGRATION=1 DUCKLN_IT_ENVS=local DUCKLN_IT_ONLY=flask-min python -m tests.integration.run

For each RepoSpec × available environment it drives Duckln's REAL bring-up
(clone → setup → run) via tests/integration/driver.run_spec and checks the repo
reached its `running_signal` (or verification passed). Prints a measured pass-rate
and the real failure class per row — the empirical gate for "any repo runs".

Refuses to run unless DUCKLN_INTEGRATION=1 (it clones + builds real repos).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from tests.integration.matrix import MATRIX, MatrixResult


def _env_available(env: str) -> bool:
    if env == "local":
        return True
    if env == "vm":
        return shutil.which("multipass") is not None
    if env == "container":
        if shutil.which("docker") is None:
            return False
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    return False


def _selected_envs() -> set[str] | None:
    raw = os.environ.get("DUCKLN_IT_ENVS", "").strip()
    return {e.strip() for e in raw.split(",") if e.strip()} or None


def run_matrix() -> MatrixResult:
    from tests.integration.driver import run_spec

    result = MatrixResult()
    only = os.environ.get("DUCKLN_IT_ONLY", "").strip()
    env_filter = _selected_envs()
    allow_gpu = os.environ.get("DUCKLN_IT_ALLOW_GPU") == "1"
    timeout = float(os.environ.get("DUCKLN_IT_TIMEOUT", "1200"))

    for spec in MATRIX:
        if only and spec.name != only:
            continue
        if "gpu" in spec.needs and not allow_gpu:
            continue
        for env in spec.environments:
            if env_filter is not None and env not in env_filter:
                continue
            if not _env_available(env):
                continue
            result.total += 1
            print(f"▶ {spec.name} [{env}] — running real bring-up…", flush=True)
            outcome = run_spec(spec, env, timeout_seconds=timeout)
            if outcome.passed:
                result.passed += 1
                print(f"  ✓ PASS ({outcome.repo_family})", flush=True)
            else:
                result.failures.append((spec.name, env, f"{outcome.failure_class}: {outcome.detail[:200]}"))
                print(f"  ✗ FAIL [{outcome.failure_class}] {outcome.detail[:200]}", flush=True)
    return result


def main() -> int:
    if os.environ.get("DUCKLN_INTEGRATION") != "1":
        print("Refusing to run: set DUCKLN_INTEGRATION=1 to run the real integration matrix.")
        return 2
    res = run_matrix()
    pct = res.pass_rate * 100
    print(f"\nIntegration matrix: {res.passed}/{res.total} passed ({pct:.0f}%).")
    for repo, env, reason in res.failures:
        print(f"  - {repo} [{env}]: {reason}")
    # The gate: ≥99% once rows are curated and the engine fixes land.
    return 0 if res.total and res.pass_rate >= 0.99 else 1


if __name__ == "__main__":
    sys.exit(main())
