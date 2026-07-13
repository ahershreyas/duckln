# Duckln Agent Specs (`harness/agents/`)

Every LLM agent's behavior is **authored here as data**, not in Python. Each `*.md` file is a
spec: YAML frontmatter (the structured contract) + a markdown body (the system prompt). This is
the single source of truth — editing an agent's behavior is a spec edit, no Python change.

---

## The specs at a glance

| Spec | Role (one line) | Output | Coordinator | Required |
|---|---|---|---|---|
| `planner` | Proposes candidate steps for the objective | `json_array` | — | ✅ |
| `critic` | Drops/orders candidates into a dependency DAG | `json_object` | — | ✅ |
| `clarifier` | Asks ≤3 questions to resolve blocking uncertainty | `json_object` | — | ✅ |
| `verdict` | Final approve/revise/block on the drafted plan | `json_object` | — | — |
| `attributor` | Explains a failed step + proposes one fix | `json_object` | — | ✅ |
| `supervisor` | Orchestrates inspect→plan→critique pipeline | `json_object` | ✅ | ✅ |
| `repo_inspector` | Read-only facts about repo + host, pre-planning | `json_object` | — | — |
| `planning_investigator` | Anticipates full setup, implicit deps (brief) | prose | — | — |
| `recovery_agent` | Investigates a failed step, returns fixed/skip/block | `json_object` | — | ✅ |
| `recovery_supervisor` | Skeptical review of a recovery conclusion | `json_object` | — | — |
| `recovery_coordinator` | Spawns investigate/search/memory, commits one fix | `json_object` | ✅ | — |
| `investigate_agent` | Read-only system/project probing after a failure | `json_object` | — | — |
| `search_agent` | Authoritative web search/fetch for the exact error | `json_object` | — | — |
| `memory_agent` | Consults persistent memory for prior failures/skills | `json_object` | — | — |
| `web_reader` | Extracts the verbatim fix from one fetched page | `json_object` | — | — |
| `loop_executor` | One cycle of an LLM-driven monitoring loop | `json_object` | — | — |
| `repo_qa` | Answers questions about the repo (read-only) | `json_object` | — | — |
| `repo_engineer` | Edits/adds/fixes code, then verifies | `json_object` | — | — |
| `node_typescript_specialist` | Brings up Node/TS repos with approval gates | `json_object` | — | — |
| `conversation_agent` | Static persona for the user-facing reply | prose | — | — |

Two specs output prose (no `output_contract`): `planning_investigator` (a markdown brief) and
`conversation_agent` (the conversational reply). All others emit validated JSON.

---

## How to add a new agent

1. Copy `planner.md` (the reference format).
2. Fill the frontmatter: `name`, `version`, `role`, `tools`, budgets, optional `can_spawn`,
   `max_contract_retries`, `input_contract`, `output_contract`.
3. Write the body — that IS the system prompt (rules, examples, a self-check, a "must NOT" list).
4. Run the suite:
   `PYTHONPATH=src python -m unittest tests.test_harness_agent_def tests.test_plan157`.
   The spec must parse (PyYAML) and pass `validate_against_tool_registry` (only reference tools
   that exist).
5. If it's a new core capability, add it to `REQUIRED_AGENTS` (see below).

---

## Frontmatter fields (parsed by `AgentDefinition`)

`name`, `role`, `tools` (list), `max_turns`, `budget_seconds`, `budget_llm_calls`, `can_spawn`
(list), `is_coordinator`, `version`, `input_contract`, `output_contract`, `max_contract_retries`.
The body becomes `system_prompt`.

---

## Contracts (Plan 157 P2)

- `input_contract.required_state_keys` — checked before a run (`validate_input_contract`); a
  missing key is surfaced, not run blind.
- `output_contract` (`type: json_array | json_object` + `item_schema`/`schema`) — the model's
  reply is validated (`validate_agent_output`); on violation the executor re-asks up to
  `max_contract_retries` (default 2) with the exact schema, then honest-fails.
- **Prose agents** (`planning_investigator`, `conversation_agent`) declare NO `output_contract`;
  the executor must skip JSON validation for them. Don't add one — their output is free-form.

---

## `SUPPORTED_SPEC_VERSION` gate

The harness understands spec `version` ≤ `SUPPORTED_SPEC_VERSION` (currently `2.0`,
`agent_def.py`). A spec declaring a HIGHER version raises a hard error at load — never run a
newer contract on an older harness. **Bump `SUPPORTED_SPEC_VERSION`** when you change the spec
schema (and migrate the specs in the same change).

---

## Required agents (`REQUIRED_AGENTS` in `agent_def.py`)

`planner, critic, clarifier, supervisor, attributor, recovery_agent` MUST load. A malformed
OPTIONAL spec is skipped + logged (one bad file can't disable the harness); a missing/malformed
REQUIRED agent raises. If you add a new core capability, add it to `REQUIRED_AGENTS`.

---

## Model-tiered budgets

Some specs declare `budget_profiles` (e.g. `repo_qa`) with `capable` and `weak` tiers. The
harness selects the profile from the Plan-156 capability probe's OBSERVED verdict — not the
model name — consistent with "judge by output, not by label." A small local model gets tighter
turn/call budgets (to force convergence) and one more `max_contract_retries` (for weaker JSON
adherence); the safety floor (tool scope, S0/S1 ceiling) is identical across tiers.

---

## Who drives what (live wiring)

- **Planning** (`plan_mode.py`): `planner`, `critic`, `clarifier`, `verdict`, `attributor` —
  prompts sourced via `_spec_prompt(name)`. Note the three distinct roles that are easy to
  confuse:
  - `critic` = critique + order candidates into a DAG.
  - `verdict` = the final approve/revise/block call on the assembled plan.
  - `supervisor` = orchestration of the pipeline, with NO LLM call of its own.
- **Recovery** (`recovery.py`): `recovery_agent`, `recovery_supervisor`, `planning_investigator`
  — sourced via `_recovery_spec_prompt(name)`. `recovery_coordinator` spawns
  `investigate_agent` / `search_agent` / `memory_agent` in parallel and commits one fix.
- **Loops** (`loop_runtime.py`): `loop_executor` runs one cycle of an LLM-driven monitoring
  loop, and is also the escalation path when a deterministic loop type's fixed logic can't
  resolve a problem.
- **Repo work**: `repo_qa` (read-only Q&A) and `repo_engineer` (edit/verify) run via the agent
  loop; `node_typescript_specialist` handles Node/TS bring-up with per-action approval gates.

---

## The deterministic floor — never make these specs

Two things are intentionally NOT spec-driven, by design. Do not "fix" them into specs:

- **The primary router** (`ConversationSupervisor`, `conversation_agent.py`) is deterministic
  code — intent routing must be predictable, not a free-LLM guess. What the router DELEGATES
  to (the conversational reply) sources its **static persona** from `conversation_agent.md`;
  the router appends live runtime context (tool policy, mode/model, platform) deterministically.
- **The S0–S4 safety classifier** (`_classify_and_verify`) is deterministic code — a code-level
  safety gate that blocks destructive/empty plans before any LLM decision. It can only BLOCK,
  never APPROVE in the LLM's place. Making it a spec would defeat its purpose.

---

## Verification checklist for any spec change

Before merging a spec edit or addition:

- [ ] Parses with PyYAML (`test_harness_agent_def`).
- [ ] `version` ≤ `SUPPORTED_SPEC_VERSION`.
- [ ] Every tool in `tools` exists in the registry (`validate_against_tool_registry`).
- [ ] `input_contract.required_state_keys` cover what the body actually reads.
- [ ] `output_contract` matches what the body tells the model to emit (or is absent for prose
      agents).
- [ ] Body has a self-check and a "must NOT" list.
- [ ] If it's a coordinator, `is_coordinator: true` and `can_spawn` lists real agents.
- [ ] If core, it's in `REQUIRED_AGENTS`.
- [ ] Full suite green: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'`.