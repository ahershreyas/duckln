# Duckln Capabilities — Subagents, Skills, and Tools (three distinct primitives)

This is the authoritative map of what Duckln can do, organized by **Claude's three
capability primitives**. They are commonly confused, so the distinction is stated first.

> **Important:** a **Subagent ≠ a Skill ≠ a Tool.** A subagent may *use* skills and tools
> if permitted, but they are different things. (Grounded in Anthropic's Claude Code docs —
> the `plugin-dev` `agent-development` and `skill-development` skills.)

---

## The three primitives (per Anthropic's docs)

- **Subagent (Agent) — the WHO.** An *autonomous subprocess* that handles a complex,
  multi-step task in its **own context window**, with its **own system prompt, tool
  allow-list, and model**. Triggered for autonomous work; it decides and acts.
- **Skill — the WHAT (procedural knowledge).** A *self-contained package of specialized
  knowledge / workflow* (canonically a `SKILL.md` + optional bundled `scripts/`,
  `references/`, `assets/`), **progressively disclosed** (only its name+description sit in
  context until it triggers). A skill is **loaded and used by an agent** — it is not an
  actor. "Onboarding guide that turns a general agent into a specialized one."
- **Tool — the ACTION.** A single callable capability (read a file, run a command, fetch a
  URL). Agents call tools; skills tell an agent *how* to use them.

**Composition:** an agent (the WHO) loads a skill (the procedural WHAT) and acts through
tools (the actions). A subagent can be scoped to a subset of tools and skills.

---

## Duckln → Subagents (19 harness agents)

Defined as spec files in [`src/duckln/harness/agents/`](../src/duckln/harness/agents/); each
is an `AgentDefinition` with its own tool allow-list, budgets, and input/output contracts,
run via `run_agent` / `run_spec`.

| Agent | Role | Tools (allow-list) |
|-------|------|--------------------|
| `supervisor` | Coordinates the multi-stage plan-generation pipeline; delegates to inspector → planner → critic. | state.read, state.write_skill, user.clarify |
| `repo_inspector` | Read-only inspection of the repo + host to build a structured understanding before planning. | shell.probe, fs.read_file, fs.list_dir, state.read, user.clarify |
| `planning_investigator` | Before setup, investigates read-only and anticipates the full setup so the plan is right first time. | fs.read_file, fs.list_dir, fs.search, shell.probe |
| `planner` | Proposes a ranked set of candidate steps to achieve the objective from repo intelligence. | state.read |
| `critic` | Drops wrong/redundant candidates and orders survivors into a dependency DAG with rationale. | state.read |
| `verdict` | Decides whether a complete, ordered plan is safe + complete enough to run. | state.read |
| `clarifier` | Produces ≤3 short questions to resolve low-confidence / undetected-tool steps. | state.read |
| `attributor` | On a failed step, explains the cause and proposes a single fix step to insert before it. | state.read |
| `repo_engineer` | Performs an action on the repo — edit/add/fix/refactor — then verifies by running tests/scripts. | fs.*, repo.run, app.serve, git.run, web.search, state.read, user.clarify |
| `repo_qa` | Answers a question about the active repo by reading + searching its actual code. | fs.read_file, fs.list_dir, shell.probe, web.search, web.fetch, state.read, user.clarify |
| `node_typescript_specialist` | Sets up + runs Node/TypeScript projects with approval at every install step. | shell.run, shell.probe, fs.*, web.*, state.write_*, user.* |
| `conversation_agent` | The primary user-facing conversational voice; answers briefly + suggests the next useful action. | (no tools — voice only) |
| `recovery_coordinator` | On a failure, coordinates parallel specialists to diagnose + propose a fix. | state.read, state.write_skill, state.write_failure, user.approve, user.clarify |
| `investigate_agent` | Read-only investigation of the failing target system + project files. | shell.probe, fs.read_file, fs.list_dir |
| `search_agent` | Web search + fetch against authoritative sources only. | web.search, web.fetch |
| `memory_agent` | Consults persistent memory for prior failures + successful skills matching the failure signature. | state.read |
| `recovery_agent` | Investigates a failed bring-up step with read-only tools (+ web) and proposes an evidence-backed fix. | fs.*, shell.probe, web.*, state.read |
| `recovery_supervisor` | A skeptical reviewer; accepts only evidence-backed recovery conclusions. | state.read |
| `loop_executor` | Runs one cycle of an LLM-driven monitoring loop (repo_watcher/model_quality/ci_monitor/custom). | state.read (+ per-type real read-only scope injected per call) |

---

## Duckln → Skills (procedural knowledge an agent loads)

Duckln's skill-equivalents — the *procedural WHAT*, distinct from the agents above:

**1. Built-in playbooks (15)** — per-stack setup/run strategies in
[`src/agent/playbooks/`](../src/agent/playbooks/), loaded by the matching specialist:
`python`, `node_typescript`, `ml_python`, `rust`, `go_native`, `cpp_native`, `diffusion`,
`audio`, `vm`, `provider`, `supervisor`, `debug_recovery`, `generic`,
`resource_management` (Plan 172 — how to reason about a disk/RAM/CPU/GPU/NPU/MLX-Metal
shortfall: signals → ranked options [safe-reclaim → right-sized resize → recreate → route-to-cloud
→ lighten]; loaded as a HINT when a resource crunch is the blocker, the actions stay the
deterministic floor), and `aware_interaction` (Plan 176 — the interaction discipline at any
decision point: comprehend → explain the situation → recommend the best approach + why → offer
**Yes / No / write-your-own** → act; loaded so the LLM VOICES the proposal over the deterministic
facts, with `propose_and_confirm` the deterministic floor. Both carry one-shot worked examples).

**2. Learned skill-memory** — verified, reusable recipes written to
`~/.duckln/memory/skills/<slug>.md` via `record_plan_skill` / `write_skill_memory_state`
([`src/state/access.py`](../src/state/access.py)), keyed by `family+OS+target`
(`plan_skill_signature`). Surfaced to the user via **`/skills`** (list) and **`/skill add`**
(capture). On a later run of a similar repo, the matching skill is injected as a known-good
backbone — Duckln "learning on success."

> **Honest caveat:** these are skill-*like* in **role** (procedural knowledge an agent uses)
> but they do **not** use Anthropic's literal `SKILL.md` + progressive-disclosure format, and
> they are selected by **repo-family detection**, not by description-triggering. Converting the
> playbooks into the canonical `SKILL.md` format would be a separate behavioral refactor, not
> done here.

---

## Duckln → Tools (the actions agents call)

The default tool registry — [`build_default_registry`](../src/duckln/harness/tools.py) — with
each tool's declared safety class (S0 read-only … S2 mutating). Agents only call tools in
their allow-list; the S0–S4 floor + mode gate (HITL/HOTL/HOOTLWO) govern execution.

| Tool | Safety | What it does |
|------|--------|--------------|
| `shell.probe` | S0 | Run a read-only diagnostic shell command (must classify S0 or it fails). |
| `shell.run` | S2 | Run a shell command on the active target; side effects allowed (safety varies by command). |
| `fs.read_file` | S0 | Read a file inside the project dir as a line window (Plan 179: file-type ceiling, `start_line`/`end_line`/`focus_line`, `[SYSTEM WARNING]` next-slice marker; never raises on a large file). |
| `fs.list_dir` | S0 | List entries in a project directory. |
| `fs.search` | S0 | grep-like regex search across repo text files; returns file:line matches. |
| `fs.glob` | S0 | Find files matching a glob (e.g. `**/*.py`) in the repo. |
| `fs.write` | S2 | Create/overwrite a repo file (needs approval). |
| `fs.edit` | S2 | Exact-string replace in a repo file (needs approval). |
| `repo.run` | S2 | Run a build/test/dev command in the repo (e.g. `npm test`, `pytest`). |
| `app.serve` | S2 | Start the app on the target, wait until serving, open it (desktop apps stream via noVNC). |
| `git.run` | S2 | Safe git subcommands (status/diff/log/branch/add/commit/checkout -b); never push/force. |
| `web.search` | S0 | DuckDuckGo search via the internet skill; results filtered to safe/authoritative hosts. |
| `web.fetch` | S0 | Fetch a URL → redacted summary; allow-listed authoritative hosts only. |
| `state.read` | S0 | Read Duckln state (config snapshot, followup, workflow). |
| `state.write_skill` | S1 | Persist a skill-memory note (`~/.duckln/memory/skills/`). |
| `state.write_failure` | S1 | Record a persistent install-failure memory record. |
| `user.approve` | S0 | Ask the user a yes/no question. |
| `user.clarify` | S0 | Ask the user a multi-choice question. |

Custom **MCP / SDK tools** can be added at runtime via **`/tools add`** / **`/mcp`** (detect →
draft → confirm → register), after which agents can call them like any built-in tool. **Plan 178**
makes a registered tool/MCP actually **executable** (a shell tool runs its configured command
through the approval/target path; an MCP tool is invoked over stdio JSON-RPC) and a registered/
learned **skill** is **loaded into the agent's context** (task-matched) so it is actually consulted.

**Per-agent models (Plan 179):** **`/models`** assigns a distinct model per agent node
(Supervisor / Repo agent / Error agent / Web reader) — *Unified* (one global model, the default)
or *Specialized*; **`/status`** shows the live role→model table. A *local* model also gets a
strict "don't guess from a truncated file snippet" directive (the runtime capability adapter).

---

## How it all composes (one flow)

The bring-up engine detects the repo family → routes to a **specialist subagent** → that
agent loads the matching **playbook (skill)** → it acts through its **scoped tools** under the
S0–S4 safety floor + the active mode gate → a verified outcome is distilled back into
**skill-memory** for the next similar repo. Background **`/loop`** cycles run the
`loop_executor` subagent the same way, inside the same tool-scope + S0/S1 ceiling.
