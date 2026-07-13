# Duckln integration matrix (real, opt-in)

Drives **real** public repos through Duckln's actual bring-up engine
(`bring_up_selected_repo`) — clone → classify → install/build → run — and reports a
**measured** pass-rate + the real failure class per row. This is the empirical gate
for "Duckln runs any repo"; it is NOT part of the unit suite (it clones + builds).

## Run

```bash
# everything available on this host (local rows; vm rows only if multipass present)
DUCKLN_INTEGRATION=1 PYTHONPATH=src python -m tests.integration.run

# restrict environments / a single row / timeout
DUCKLN_INTEGRATION=1 DUCKLN_IT_ENVS=local        PYTHONPATH=src python -m tests.integration.run
DUCKLN_INTEGRATION=1 DUCKLN_IT_ENVS=vm           PYTHONPATH=src python -m tests.integration.run
DUCKLN_INTEGRATION=1 DUCKLN_IT_ONLY=vite-react   PYTHONPATH=src python -m tests.integration.run
DUCKLN_INTEGRATION=1 DUCKLN_IT_ALLOW_GPU=1       PYTHONPATH=src python -m tests.integration.run   # include gpu rows
```

Env vars: `DUCKLN_IT_ENVS` (comma list: local,vm,container), `DUCKLN_IT_ONLY` (one
row name), `DUCKLN_IT_TIMEOUT` (seconds/row, default 1200), `DUCKLN_IT_ALLOW_GPU=1`.

## Safety

- **local** rows are pure-Python (only a temp venv + pip) so they never mutate the
  host. node/go/rust/heavy/GPU rows are **vm-only** — run them in a multipass VM so
  toolchain installs are sandboxed.

## Reporting failures back

For each `✗ FAIL` line, paste:
```
<row> [<env>]: <FAILURE_CLASS>: <detail tail>
```
Each real failure is a real engine gap → it gets a deterministic fix + a regression
test, then the row is re-run until green.

## Measured so far (local, this Mac)
- `flask-hello`    [local] ✓ PASS (python)
- `fastapi-min`    [local] ✓ PASS (python)
- `streamlit-demo` [local] ✓ PASS (python; data/ML app)
→ 3/3 host-safe pure-Python rows.

node / go / rust / heavy-ML / gpu / vm rows: pending a run on a multipass VM (or a
Mac run where Homebrew toolchain installs are acceptable). Plan 124 added macOS/ML
deterministic fixes (brew-missing block, CPU/MPS torch wheel, CUDA-on-Mac honest
block, gated-HF token ask, pyenv fallback) — to be exercised by those rows.
