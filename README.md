# Duckln — an AI terminal agent that fixes, verifies, and keeps you informed.

![Duckln](./docs/assets/duckln-banner.txt)

Duckln is an AI-powered, terminal-first agent that **sets up, runs, fixes, and explains code
repositories** — locally, on an Ubuntu VM, in Docker, or in the cloud. It runs commands under a
safety gate, recovers from failures with a reasoning + web-search loop, and keeps you in control
of anything that changes your machine.

> ### 🚧 Status: early-stage, in active development
> Duckln is **pre-1.0 (v0.1.0)** and evolving quickly. Interfaces, behavior, and internals can
> change without notice, and some flows are best-effort. It is **not yet production-ready** — use
> it for experimentation, and expect rough edges. Issues and PRs are welcome. (Following the
> open-source norm, this notice is here so you know exactly what you're installing.)

---

## What it does

1. Runs a terminal command safely (S0–S4 classification + your control mode).
2. Detects what failed.
3. Gathers only the minimal relevant context (distilled, not a wall of logs).
4. Produces exact next steps.
5. Optionally runs approved fixes (and installs missing prerequisites like Multipass/Docker on consent).
6. Verifies whether the fix actually worked.
7. Teaches you what happened — and records its reasoning to a readable `logical-thinking.md`.

**Supported today:** macOS + Ubuntu · zsh + bash · LLM providers **Ollama (local, free)**,
OpenRouter, OpenAI, and Anthropic · modes **HITL** (explain only), **HOTL** (approve each step),
**HOOTLWO** (auto-run safe steps, ask for the rest).

---

## Requirements

- **Python ≥ 3.11**
- macOS or Ubuntu Linux
- An LLM provider — either:
  - **[Ollama](https://ollama.com/)** for a fully local, free model (e.g. `ollama pull gemma2:9b`), or
  - an API key for OpenRouter / OpenAI / Anthropic
- Optional (only for the VM/container targets): **[Multipass](https://multipass.run/)** and/or **Docker** — Duckln can offer to install these for you when a flow needs them.

---

## Installation

Duckln isn't published to PyPI yet, so install it from source:

```bash
git clone https://github.com/ahershreyas/duckln.git
cd duckln
python3 -m venv .venv && source .venv/bin/activate   # recommended
pip install -e .
```

Then run it:

```bash
duckln
```

On first run, an onboarding flow helps you pick a provider + model (and pull an Ollama model if
you go local). Type `/help` inside the app for commands.

### Run without installing (from a source checkout)

```bash
PYTHONPATH=src python3 -m duckln.main
```

---

## Usage

- Just talk to it in plain language: *"set up github.com/owner/repo"*, *"why did the build fail?"*,
  *"list my VMs and their repos"*, *"stop"*.
- Slash commands (type `/` to see them): `/repos`, `/vm`, `/cloud`, `/cleanup`, `/model`,
  `/provider`, `/mode`, `/internet`, `/healthcheck`, `/help`.
- Duckln keeps you in control: system-changing actions (installs, VM create, destructive steps)
  are gated by your mode and a one-tap confirmation.

---

## Development

```bash
# from a source checkout, in your venv
pip install -e .

# run the full unit suite (unittest, ~2600 tests)
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'

# a single test file / method
PYTHONPATH=src python -m unittest tests.test_ai_client
PYTHONPATH=src python -m unittest tests.test_ai_client.ClassName.test_method
```

There's no dedicated lint step configured. Python ≥ 3.11 is required.

### Project layout

```text
duckln/
├── src/duckln/        # the app (main.py entry point, agents, tools, runtimes)
├── src/agent/         # probes + per-stack playbooks (skills)
├── src/state/         # SQLite-backed state (accessed only via state/access.py)
├── tests/             # unittest suite, one file per module
├── docs/              # SDC specs (requirements / plan / tasks / analysis) + knowledge
└── .sdc/              # SDC guidelines, templates, release gate
```

### Contributing — Specification Driven Coding (SDC)

This project follows an **SDC** discipline: specs come before code. For a non-trivial change:

1. Update `docs/requirements.md` (user-visible behavior, privacy, failure modes, rollback).
2. Link it in `docs/plan.md` (requirements → components).
3. Add tasks in `docs/tasks.md` tagged `[REQ-N]`.
4. Validate traceability in `docs/analysis.md`.
5. Implement in `src/`, add/adjust tests in `tests/`, keep the suite green.

Ambiguity is resolved by updating the spec first, then coding — never the reverse. See
[`.sdc/guidelines.md`](.sdc/guidelines.md) and [`CLAUDE.md`](CLAUDE.md) for the full conventions.

---

## Safety & privacy

- Commands are classified **S0–S4** (read-only → destructive); destructive/irreversible steps are
  blocked by default and always require explicit consent.
- Your control **mode** (HITL/HOTL/HOOTLWO) is enforced, never bypassed.
- Duckln never stores raw API keys, full transcripts, or unredacted secrets — only compact,
  redacted summaries (command names, exit codes, hardware probes, preferences).

---

## License

See the repository for license details. Contributions are welcome via issues and pull requests.
