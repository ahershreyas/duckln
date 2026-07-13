"""Plan 118/123 — the integration matrix: representative REAL repos × environments.

A RepoSpec is repo-AGNOSTIC data (no special-casing in the engine). The runner drives
each through Duckln's normal clone→setup→run path and records whether it reached
`running_signal` / verification. The measured pass-rate over the matrix is the gate.

Environment tagging (safety): `local` rows are kept PURE-PYTHON so running them here
only creates a temp venv + pip installs (no system mutation — python3/git already
present). Rows that need a toolchain install (node/go/rust) or heavy/GPU work are
tagged vm-only so they run sandboxed in a multipass VM, not on the host.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RepoSpec:
    name: str
    url: str
    stack: str                      # python | node-web | go | rust | ml | desktop | smoke
    running_signal: str             # substring proving "running"; "" → rely on verification_passed
    environments: tuple[str, ...] = ("local", "vm", "container")
    needs: tuple[str, ...] = ()     # ("db",) ("desktop",) ("prebuild",) ("gpu",)
    # Plan 125: a synthetic smoke (no repo clone) — pip-install these into a temp
    # venv then run this check; PASS on exit 0. Used for the torch-free MLX GPU smoke.
    smoke_pip: tuple[str, ...] = ()
    smoke_check: str = ""           # python -c <smoke_check>


# Curated, public repos. Grow over time. Local = pure-Python (host-safe);
# toolchain/heavy/GPU rows are vm-only.
MATRIX: tuple[RepoSpec, ...] = (
    # --- Pure-Python (host-safe local) -------------------------------------
    RepoSpec("flask-hello", "https://github.com/render-examples/flask-hello-world", "python", "", environments=("local", "vm")),
    RepoSpec("fastapi-min", "https://github.com/render-examples/fastapi", "python", "", environments=("local", "vm")),
    # --- Torch-free Apple-Silicon GPU smoke (host-safe, ~tens of MB, fits 8GB) ---
    RepoSpec(
        "mlx-smoke", "", "smoke", "MLX-OK", environments=("local",),
        smoke_pip=("mlx",),
        smoke_check="import mlx.core as mx; x=mx.sum(mx.ones(1024)); mx.eval(x); print('MLX-OK', float(x))",
    ),
    # --- AI/ML, CPU-friendly (host-safe: pure-pip data/ML app) --------------
    RepoSpec("streamlit-demo", "https://github.com/streamlit/streamlit-example", "python", "", environments=("local", "vm")),
    # heavy torch/CUDA variants stay vm/gpu-only
    RepoSpec("gradio-demo", "https://github.com/gradio-app/gradio", "ml", "Running on", environments=("vm",), needs=("prebuild",)),
    # --- Toolchain rows — vm-only (would install node/go/rust on the host) --
    RepoSpec("vite-react", "https://github.com/vitejs/vite", "node-web", "Local:", environments=("vm",)),
    RepoSpec("go-example", "https://github.com/golang/example", "go", "", environments=("vm",)),
    # --- Plan 146 B3: SERVICE-BACKED rows — the #1 real-repo blocker. Pass requires the
    # B2 OUTCOME check (the app actually serves a healthy response / its tests pass), not a
    # banner. Duckln must provision the DB (start + readiness wait + .env + migrate) first.
    RepoSpec("django-postgres", "https://github.com/heroku/python-getting-started", "python", "", environments=("vm",), needs=("db",)),
    RepoSpec("redis-flask", "https://github.com/redis-developer/basic-analytics-dashboard-redis-bitmaps-nodejs", "node-web", "", environments=("vm",), needs=("db",)),
    # --- Polyglot-desktop-sidecar CLASS (Tauri/Electron + backend + native sidecar) ---
    # Proven with ≥2 real instances of the SAME class (engine fixes stay signal-keyed,
    # never repo-name-keyed). JustHireMe is the user's private instance #1 (run via the
    # TUI); these public repos are instances Duckln must also handle:
    RepoSpec("tauri-py-sidecar", "https://github.com/dieharders/example-tauri-v2-python-server-sidecar", "desktop", "", environments=("vm",), needs=("desktop", "prebuild")),
    RepoSpec("electron-quickstart", "https://github.com/electron/electron-quick-start", "desktop", "", environments=("vm",), needs=("desktop",)),
)


@dataclass
class MatrixResult:
    total: int = 0
    passed: int = 0
    failures: list[tuple[str, str, str]] = field(default_factory=list)  # (repo, env, reason)

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0
