---
applyTo:
  - "src/duckln/**"
  - "src/agent/**"
  - "src/state/**"
  - "tests/**"
---

# Duckln Workspace Instructions

This workspace enforces **Specification Driven Coding (SDC)**. Before implementing features, requirements, plans, and tasks must be linked and validated.

## Quick Start

**Entry point:** `src/duckln/main.py` → `main()` → conversation loop  
**Install:** `pip install -e .`  
**Test:** `python -m unittest discover -s tests -p 'test_*.py'`  
**Run:** `duckln` (or `PYTHONPATH=src python3 -m duckln.main`)

---

## Specification Discipline (Non-Negotiable)

**See:** [docs/requirements.md](../../docs/requirements.md), [docs/plan.md](../../docs/plan.md), [docs/tasks.md](../../docs/tasks.md)

### Before you code:

1. **Requirements** must define user-visible behavior, privacy, failure modes, rollback
2. **Plan** must link requirements to implementation components
3. **Tasks** must link to specific requirements via `[REQ-N]` references
4. **Analysis** must validate traceability via `docs/analysis.md`
5. No PR without all four connected

### Anti-patterns:

- ❌ "Build X" without requirements
- ❌ Requirements that don't link to plan items
- ❌ Tasks without requirement references
- ❌ Ambiguity → code it anyway
- ✅ Ambiguity → update spec first

---

## Architecture Reference

### Core Supervisor Loop

```
main.py (entry)
  ↓
ConversationSupervisor (agent_context.py)
  ↓
conversation_routes/ (policy → routing)
  ↓
Specialist Agents (repo_bringup, subagents, etc.)
  ↓
ControlledCommandRunner (shell.py) → Verification
```

### Key Modules

| Module | Purpose | See |
|--------|---------|-----|
| `conversation_agent.py` | Supervisor routing based on user input | ConversationSupervisor |
| `agent_context.py` | Session state, repo inventory, hardware | AgentContextService |
| `repo_bringup.py` | Classify and bring up repos per family | playbooks in src/agent/playbooks/ |
| `cloud_runtime.py` | Managed cloud resource lifecycle | Resource contract |
| `subagent_runtime.py` | Specialist agent execution | Contracts in subagents.py |
| `shell.py` | Safe command execution with capture | ControlledCommandRunner |
| `config.py` | Load/save app config, onboarding | AppConfig |
| `modes.py` | HITL / HOTL / HOOTLWO enforcement | ControlMode enum |
| `state/store.py` | SQLite config + runtime state | No raw SQL — use state/access.py |
| `agent/memory.py` | Filesystem state (AGENTS.md, skills/, etc.) | Sync logic |
| `agent/probe.py` | Hardware discovery (CPU, RAM, GPU, CUDA) | SystemProbe |

### State Layer

**Never write raw SQL.** Access state via high-level APIs:
- `src/state/access.py` → `StateAccess` class for session/workflow/config reads
- SQLite is backing; filesystem (AGENTS.md, memory/) is the agent interface

### Playbooks & Specialists

**Location:** `src/agent/playbooks/`

Playbooks define specialist execution strategies for repo families:
- `python.md` → Python apps
- `node_typescript.md` → Node/TypeScript
- `cpp_native.md` → C++ runtime repos
- Add new playbooks for new repo families

**Supervisor route:** User selects repo → probe repo signals → classify family → route to specialist playbook → specialist agent executes

---

## Naming Conventions

### Modules

- `*_agent.py` → Supervisors / routing (conversation_agent.py, subagent_runtime.py)
- `*_runtime.py` → Execution contexts (cloud_runtime, browser_runtime)
- `*_policy.py` → Behavior rules (conversation_policy.py, runtime_governance.py)
- `*_intake.py` → User input parsing (repair_intake.py)
- `*_routes/` → Conversation routing submodules
- **Avoid:** `*_utils.py`, `*_helpers.py` — use single-purpose modules instead

### Classes

- `ServiceName` for services → `AgentContextService`, `ConversationSupervisor`
- `DataName` (lowercase `name`) for dataclasses / records → `SystemProbe`, `AgentContext`, `RepoInventoryEntry`
- Enums inherit `str, Enum` for CLI serialization → `Provider`, `ControlMode`, `UninstallTier`
- Immutable dataclasses use `frozen=True`

### Functions & Variables

- Public APIs: `snake_case`
- Private internals: `_snake_case`
- Async: prefix with `async_` if mixed with sync in same module

---

## Testing

### Framework & Location

- **Framework:** Python `unittest` (not pytest)
- **Location:** `tests/` directory (30+ test files)
- **Run:** `python -m unittest discover -s tests -p 'test_*.py'`

### Patterns

- Import directly from `src/` modules (flat structure)
- Mock external deps: `httpx` client, subprocess
- Use `tempfile.TemporaryDirectory()` for filesystem state
- Example: [tests/test_agent_memory.py](../../tests/test_agent_memory.py) — temp dir + FileSystemMemory
- Example: [tests/test_ai_client.py](../../tests/test_ai_client.py) — FakeHttpClient mock

### What to test

- **Public APIs** of services (AgentContextService, ControlledCommandRunner)
- **State transitions** (config load/save, session lifecycle)
- **Command execution** with mocked subprocess
- **Routing logic** (conversation policy)
- **Specialist playbook** selection logic

### What NOT to test

- LLM API responses (mock the httpx call)
- Full terminal integration (mock shell.py)
- UI rendering details (test logic, not formatting)

---

## Dependencies & Providers

### Key Dependencies

| Dependency | Purpose | Note |
|------------|---------|------|
| `httpx` | LLM API calls (OpenAI, Anthropic, OpenRouter, Ollama) | Sync client |
| `rich` | Colored terminal output, tables, panels | CLI rendering |
| `textual` | TUI framework | Rare use; mostly CLI prompt_toolkit |
| `InquirerPy` + `prompt_toolkit` | Interactive CLI selection/input | List selection, text input |
| `ptyprocess` / `pywinpty` / `pyte` | PTY management + terminal emulation | Command capture & output |
| `pywebview` | Local web UI runtime | Optional browser interface |

### LLM Provider Pattern

All providers (OpenAI, Anthropic, OpenRouter, Ollama) use a generic HTTP POST pattern.

**To add a provider:**
1. Extend `ProviderAdapter` protocol in `ai_client.py`
2. Implement `post()` and `list_models()` methods
3. Register in `provider_mapping` enum
4. Add provider to config flow in `config.py`

**Ollama special case:** Local HTTP at `localhost:11434` — no API key needed.

---

## Common Development Tasks

### Add a new repo family & playbook

1. Create `src/agent/playbooks/family_name.md` with specialist strategy
2. Add probe signals to `agent/probe.py` to identify the family
3. Create specialist class in `subagents.py` (inherit `BasePlaybookAgent`)
4. Register in supervisor routing (conversation_routes/)
5. Test via `test_repo_bringup.py` classification + smoke test

### Add a new LLM provider

1. Extend `ProviderAdapter` in `ai_client.py::post()` and `list_models()`
2. Add provider to `Provider` enum and `provider_mapping`
3. Update `config.py` onboarding flow with provider prompt
4. Add to `test_ai_client.py` with mocked HTTP response
5. Document in AGENTS.md Provider section

### Add a new command to shell.py

1. Add method to `ControlledCommandRunner` class
2. Ensure it uses `_run_controlled()` internally
3. Mock in tests via `FakeCommandRunner` (see test_shell.py)
4. Document timeout, output capture, safety classification (S0–S4)

### Modify conversation routes

1. Edit `src/duckln/conversation_routes/route_name.py`
2. Update routing policy in `conversation_policy.py` if logic changes
3. Add test in `tests/test_conversation_routes.py`
4. Verify in supervisor happy-path test

---

## Safety & Modes

### Safety Classification

Commands are classified S0–S4:
- **S0** → read-only diagnostics (always safe)
- **S1** → non-destructive env setup/fix (safe if bounded)
- **S2** → local file mutation (needs verification)
- **S3** → system mutation / elevated risk (manual approval)
- **S4** → destructive or irreversible (blocked by default)

### Mode Enforcement

See `src/duckln/modes.py`:
- **HITL** → Explain and suggest only (no auto-execution)
- **HOTL** → Suggest exact commands, user approves (user confirms each execution)
- **HOOTLWO** → Auto-run only whitelisted S0–S1 (auto-run safe, ask for S2+)

**Never skip mode checks.** Treat mode as contract enforcement, not advisory.

---

## Memory & State

### Filesystem Memory (Agent-facing)

Stored in `src/agent/memory.py`. Exposed as filesystem:
- `AGENTS.md` — agent manifest
- `skills/` — domain-specific skills
- `knowledge/` — codebase patterns / reference
- `sessions/` — per-session context

**Keep high-signal, concise.** Summarize, don't dump logs.

### SQLite State (System-facing)

Backing store for config, workflow state, telemetry. Accessed via `state/access.py`:
```python
from src.state.access import StateAccess
state = StateAccess(db_path)
config = state.get_config()  # NOT: SELECT * FROM config
```

### Privacy-First Telemetry

**Never store:**
- Raw API keys
- Full transcripts or logs
- User session recordings
- Unredacted error output

**Safe to store:**
- Command names and exit codes
- Setup success/failure counts
- Hardware probe results (anonymized)
- User preferences and past selections

---

## Release Gate

**See:** [docs/requirements.md](../../docs/requirements.md#release)

Before merging to main:
1. ✅ Requirements, plan, tasks, analysis linked
2. ✅ All tests passing
3. ✅ User-visible behavior stable (no breaking changes)
4. ✅ Privacy policy upheld (no secrets in logs/memory)
5. ✅ Failure modes documented
6. ✅ Rollback path clear
7. ✅ Code review + `LGTM`

**No shortcuts.** Do not merge incomplete specs or untested features.

---

## Troubleshooting

### "I don't understand the flow"

→ Read [docs/plan.md](../../docs/plan.md) and follow the supervisor loop diagram above  
→ Run `duckln` with `--debug` flag (if available) to see routing

### "Test fails with 'module not found'"

→ Set `PYTHONPATH=src` before running tests:  
```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'
```

### "Where do I add user-facing UI text?"

→ UI strings: `src/duckln/render_blocks.py` or `textual_ui.py`  
→ Error messages: Define in `*_policy.py` or `*_intake.py`  
→ Do NOT hardcode strings in logic modules

### "How do I add a config option?"

→ Add field to `AppConfig` dataclass in `config.py`  
→ Add to onboarding flow in `config.py::run_onboarding()`  
→ Persist via `state/store.py` (no manual JSON/toml)

---

## Quick Links

- **Agent Spec:** [AGENTS.md](../../AGENTS.md)
- **Requirements:** [docs/requirements.md](../../docs/requirements.md)
- **Plan:** [docs/plan.md](../../docs/plan.md)
- **Tasks:** [docs/tasks.md](../../docs/tasks.md)
- **Known Limitations:** [docs/known_limitations.md](../../docs/known_limitations.md)
- **Architecture Deep Dive:** [docs/analysis.md](../../docs/analysis.md)
- **First-Launch Demo:** [docs/first_launch_demo.md](../../docs/first_launch_demo.md)
- **Release Notes:** [docs/release_rollout.md](../../docs/release_rollout.md)

---

## Summary

**This is an SDC project.** Specifications are contracts, not suggestions. Keep them linked, validated, and concise.

- **Code is implementation of spec** → never vice versa
- **Spec ambiguity blocks coding** → resolve first
- **Testing is verification** → mock externals, test logic
- **Safety is enforced** → mode + classification = no override
- **State is managed** → no raw SQL, use high-level APIs
- **Memory is high-signal** → summaries, not dumps

Ask for help when spec or architecture is unclear. Update this file when patterns change.
