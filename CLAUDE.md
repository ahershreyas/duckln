# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Commands

```bash
# Install (editable)
pip install -e .

# Run
duckln
# or without install:
PYTHONPATH=src python3 -m duckln.main

# Run all tests
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'

# Run a single test file
PYTHONPATH=src python -m unittest tests.test_ai_client

# Run a single test method
PYTHONPATH=src python -m unittest tests.test_ai_client.TestClassName.test_method_name
```

No dedicated lint command is configured. Python ≥ 3.11 required.

---

## Architecture

**Entry point:** `src/duckln/main.py` → `main()` → conversation loop

```
main.py
  ↓
ConversationSupervisor (conversation_agent.py)
  ↓
conversation_routes/  ←  conversation_policy.py controls routing
  ↓
Specialist Agents (repo_bringup.py, subagents.py)
  ↓
ControlledCommandRunner (shell.py) → Verification
```

### Key modules

| Module | Purpose |
|--------|---------|
| `conversation_agent.py` | Supervisor: routes user input to specialist agents |
| `agent_context.py` | Session state, repo inventory, hardware context |
| `repo_bringup.py` | Classify repos by family; routes to playbooks in `src/agent/playbooks/` |
| `shell.py` | `ControlledCommandRunner` — all subprocess execution goes here |
| `config.py` | `AppConfig` dataclass; onboarding flow and config persistence |
| `modes.py` | `ControlMode` enum — HITL / HOTL / HOOTLWO enforcement |
| `ai_client.py` | `ProviderAdapter` hierarchy; all LLM HTTP calls |
| `state/store.py` | SQLite backing store — never query directly |
| `state/access.py` | `StateAccess` — the only correct way to read/write state |
| `agent/memory.py` | Filesystem memory (AGENTS.md, skills/, knowledge/, sessions/) |
| `agent/probe.py` | `SystemProbe` — CPU, RAM, GPU, CUDA hardware discovery |

### State layer

**Never write raw SQL.** All reads/writes go through `src/state/access.py`:

```python
from state.access import read_config_snapshot, write_followup_state
config = read_config_snapshot(config_dir)   # NOT: SELECT * FROM config
```

SQLite is the backing store; the filesystem (`memory/`) is the agent-facing interface. Managed memory is materialized from SQLite via `materialize_managed_memory_state()`.

### Playbooks

`src/agent/playbooks/` holds specialist execution strategies per repo family (`python.md`, `node_typescript.md`, `cpp_native.md`). Add a new `.md` file here when introducing a new repo family. Probe signals to detect the family live in `agent/probe.py`; specialist classes live in `subagents.py`.

### LLM providers

All providers (`OpenRouterAdapter`, `OpenAIAdapter`, `AnthropicAdapter`, `OllamaAdapter`) extend `ProviderAdapter` in `ai_client.py`. To add a provider: implement `build_headers()`, `parse_models()`, `conversation_url()`, `build_conversation_payload()`, `parse_conversation_text()`, then register in `get_provider_adapter_for_base_url()` and add to the `Provider` enum.

Ollama uses `localhost:11434` with no API key; its base URL is normalized via `normalize_ollama_base_url()`.

---

## Specification Discipline (Non-Negotiable)

This is an **SDC (Specification Driven Coding)** project. Before implementing anything:

1. Requirements must define user-visible behavior, privacy, failure modes, and rollback in `docs/requirements.md`
2. Plan must link requirements to components in `docs/plan.md`
3. Tasks must reference requirements via `[REQ-N]` tags in `docs/tasks.md`
4. Traceability must be validated in `docs/analysis.md`

Ambiguity → update the spec first, then code. Never the reverse.

**Release gate** (before merging to main): all four spec docs linked, all tests passing, no breaking user-visible changes, privacy upheld, failure modes documented, rollback path clear.

---

## Safety & Modes

Commands are classified S0–S4:
- **S0** — read-only diagnostics (always auto-run)
- **S1** — non-destructive env setup (safe if bounded)
- **S2** — local file mutation (needs verification)
- **S3** — system mutation / elevated risk (manual approval)
- **S4** — destructive / irreversible (blocked by default)

`modes.py` enforces:
- **HITL** — explain and suggest only, no execution
- **HOTL** — suggest exact commands, user approves each
- **HOOTLWO** — auto-run S0–S1; ask for S2+

Mode checks are contract enforcement, not advisory. Never bypass them.

---

## Tool Permissions                                                                                                                                        
Never ask for confirmation on bash commands that contain backslash-escaped whitespace (e.g. paths with spaces like `cd "path with spaces"`). Treat escaped paths as pre-approved safe syntax.  

----

## Coding Style

All conventions verified against `src/duckln/ai_client.py`, `src/state/access.py`, `src/agent/memory.py`.



**Indentation:** 4 spaces.

**`from __future__ import annotations`** at the top of every source file.

**Module naming:**
- `*_agent.py` — supervisors and routing
- `*_runtime.py` — execution contexts
- `*_policy.py` — behavior rules
- `*_intake.py` — user input parsing
- `*_routes/` — conversation routing submodules
- Avoid `*_utils.py` / `*_helpers.py` — use single-purpose modules

**Classes:** PascalCase. Services end in `Service` (`AgentContextService`); supervisors end in `Supervisor`; dataclasses/records use plain nouns (`SystemProbe`, `ProviderModel`).

**Enums:** Inherit `str, Enum` for CLI serialization. Values are lowercase strings.

**Dataclasses:** Use `frozen=True` for immutable records (`ProviderModel`, `AgentMemoryPaths`, etc.).

**Functions/variables:** `snake_case` public, `_snake_case` private. Multi-argument functions use keyword-only parameters (`def f(*, param1, param2)`).

**Return types:** Prefer `tuple[T, ...]` over `list[T]` for immutable collections returned from APIs.

**Docstrings:** Single-line only. No multi-line blocks.

**Terminal Safety**: Always wrap file paths in double quotes. Do not use backslashes to escape spaces (use "All Agents" instead of All\ Agents) to avoid whitespace security warnings.

---

## Testing

- **Framework:** `unittest` (not pytest)
- **Location:** `tests/` (30+ files, one per module)
- Mock `httpx` clients with a fake implementing the `HttpClient` protocol
- Use `tempfile.TemporaryDirectory()` for filesystem state
- Test public APIs, state transitions, command execution (mocked subprocess), routing logic
- Do **not** test LLM response text, full terminal integration, or UI rendering details

---

## Memory (Agent-Facing)

The `memory/` directory under the config dir is the agent interface:

```
memory/
  AGENTS.md      ← agent manifest
  SOUL.md / IDENTITY.md / USER.md / TOOLS.md / BOOTSTRAP.md
  skills/        ← domain-specific skill notes
  knowledge/     ← codebase patterns
  sessions/      ← per-session context summaries
  subagents/     ← scoped subagent contracts
```

All writes go through `state/access.py` helpers (`write_skill_memory_state`, `write_session_summary_state`, etc.), which persist to SQLite and re-materialize the filesystem view. Keep notes high-signal and concise — summaries, not dumps.

**Privacy:** Never store raw API keys, full transcripts, session recordings, or unredacted error output. Safe to store: command names and exit codes, setup success/failure counts, hardware probe results, user preferences.

---

## Quick Links

- **Agent Spec:** [AGENTS.md](AGENTS.md)
- **Capabilities (Subagents vs Skills vs Tools):** [docs/capabilities.md](docs/capabilities.md)
- **Requirements:** [docs/requirements.md](docs/requirements.md)
- **Plan:** [docs/plan.md](docs/plan.md)
- **Tasks:** [docs/tasks.md](docs/tasks.md)
- **Analysis:** [docs/analysis.md](docs/analysis.md)
- **SDC Guidelines:** [.sdc/guidelines.md](.sdc/guidelines.md)

## External Reference Documentation
- **Agent Frameworks**: https://www.anthropic.com/engineering/building-effective-agents
- **Ubuntu/Multipass**: https://ubuntu.com/server/docs/how-to/virtualisation/multipass/
- **Google Cloud (Compute)**: https://docs.cloud.google.com/docs/compute-area
- **Google Cloud (Python SDK)**: https://docs.cloud.google.com/python/docs/setup
- **AWS Core Services**: https://docs.aws.amazon.com/
- **AWS Python SDK (EC2/VMs)**: https://amazon.comcode-library/latest/ug/python_3_ec2_code_examples.html

