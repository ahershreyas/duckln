# Duckln — Production-Grade Context Understanding (Research-Backed Design)

**Audience:** the engineer improving Duckln's context understanding across the board — not
just the incident-question screenshot.
**Scope:** approach, architecture, and prompt guidance. No implementation code.
**Basis:** grounded in current published practice (Anthropic's "Effective Context Engineering
for AI Agents" and its memory/compaction cookbook; LangChain's write/select/compress/isolate
framework; the 2026 agent-memory literature — Mem0, Zep, LoCoMo/LongMemEval benchmarks; the
"LLMs Get Lost in Multi-Turn Conversation" finding). Sources listed at the end.

---

## 1. Reframe: this is a context-engineering problem, not a prompt problem

The published consensus is explicit: for anything beyond a single-turn chatbot, the dominant
discipline is **context engineering** — deliberately curating what the model sees on every
call — not prompt wording. The core principle from Anthropic: find the smallest set of
high-signal tokens that maximize the likelihood of the desired outcome; treat context as a
finite resource with diminishing returns.

Two research facts that directly explain Duckln's failures:
- **Multi-turn degradation:** the Microsoft/Salesforce "LLMs Get Lost in Multi-Turn
  Conversation" result found accuracy can drop ~40% after back-and-forth when context isn't
  managed. Duckln's generic replies after an error are a textbook case.
- **Context rot:** as tokens grow, the model's ability to recall any specific fact DECREASES.
  So "just stuff more history in" makes understanding worse, not better — especially on a
  local model with a smaller window.

Conclusion: the fix is not "send more history" and not "add phrases." It is a deliberate
context pipeline built on the four published levers.

---

## 2. The four levers (LangChain/Anthropic framework) applied to Duckln

Every production context system organizes around four strategies. Here's each mapped to Duckln.

### WRITE — persist state outside the context window (the scratchpad)
Duckln should maintain a structured **session scratchpad** (a runtime state object, not raw
chat), holding the facts that must survive across turns:
- `active_repo` (path, family, package manager, entry point) — from repo_inspector.
- `active_incident` (summary, command, stderr excerpt, resolved flag) — from failures.
- `active_plan` / `active_task` (what's underway, current step, status).
- `user_prefs` (chosen package manager, target, etc. the user has stated).
- `session_facts` (repos tracked this session, key decisions).
This is exactly Anthropic's scratchpad/structured-note-taking pattern: write useful state to a
field in runtime state, expose selected parts each turn.

### SELECT — pull only the RELEVANT state into each turn
Do NOT dump the whole scratchpad every turn. Based on the current message's intent, select the
slice that matters:
- Incident question → inject `active_incident`.
- "What repo is this?" → inject `active_repo`.
- Status question → inject `active_plan`/`active_task`.
- Plain conversation → inject little or nothing (greetings need no repo state).
This is the fine-grained "expose selected parts of state at each step" control the sources
describe. Selection is what makes understanding feel sharp without bloating context.

### COMPRESS — summarize/bound before it overflows
- **Bound every capture:** stderr excerpts, tool outputs, and file reads go in as bounded
  summaries or last-N-lines, never full dumps (tool outputs are the #1 source of context bloat
  in the literature).
- **Observation masking / tool-result clearing:** once a tool result has been used, replace the
  raw result with a compact reference ("read package.json → npm project, 12 deps") and keep only
  the most recently relevant file(s) verbatim. This is Anthropic's tool-result clearing.
- **Compaction for long sessions:** when the running history approaches the model's window,
  summarize the trajectory (preserve decisions, active repo, unresolved incidents; discard
  redundant tool output) and continue from the summary plus the most recent items — Claude
  Code's auto-compact pattern.

### ISOLATE — keep heavy work out of the main context
Duckln already does this well and should keep doing it: sub-agents (planner, critic,
investigators) run in their own context and return only a distilled result to the main thread.
Keep large repo scans, searches, and investigations isolated; the main conversation sees the
summary, not the raw work. This is the multi-agent isolation pattern.

---

## 3. The central mechanism: a structured Session State + intent-driven selection

Tie the four levers together with one architecture:

```
                         SESSION STATE (the scratchpad — WRITE)
   ┌───────────────────────────────────────────────────────────────┐
   │ active_repo | active_incident | active_plan | user_prefs | ... │
   └───────────────────────────────────────────────────────────────┘
                                  ▲                      │
        writes (bounded/COMPRESS) │                      │ SELECT slice by intent
   errors, inspector, plan steps ─┘                      ▼
   ─────────────────────────────────────────────────────────────────
   user message ─► intent router ─► pick relevant state slice ─► responder
   (cascade from the                (incident? repo? status?      (answers FROM
    intent-routing design)           plan? none?)                  the selected state)
```

- Everything Duckln learns is WRITTEN to session state as bounded, structured records.
- The intent router (the 4-tier cascade already designed) classifies the message.
- Based on intent, SELECT the relevant slice of state and inject ONLY that.
- The responder answers from the injected slice — so "what is the issue?" gets the incident,
  "what repo is this?" gets the repo, "when will it be done?" gets the plan status.

This generalizes the incident fix into a whole-system capability: any follow-up about ongoing
work reaches the right context because the right state slice is selected by intent, not by
keyword matching.

---

## 4. Memory tiers (short-term vs long-term) — the standard production split

The literature is unanimous on two tiers; Duckln should have both.

**Short-term (session) memory** — the scratchpad above. Lives for the session, holds active
repo/incident/plan/prefs, compacted when it grows. This alone fixes the observed bugs.

**Long-term (cross-session) memory** — persists across sessions: per-repo facts ("this repo
needs a venv + PyInstaller"), user preferences ("always pnpm"), and recovery skills (already in
Duckln's `memory/` layer). Retrieved by relevance when a session starts or a matching situation
recurs. Duckln already has the bones of this (memory_agent, `memory/skills/`); the upgrade is to
also write per-repo/per-user profiles and select them into context when that repo or user
returns.

Keep long-term retrieval SELECTIVE and bounded — pull a small set of relevant facts, not the
whole store (context rot). For Duckln's scale, a simple keyed store + the existing signature
lookup is enough; a vector/graph memory framework (Mem0/Zep-style) is a later option only if
cross-session recall becomes a real need, not day one.

---

## 5. Guardrails the research specifically warns about

- **Don't let compaction silently become reality.** A known danger: after summarizing, the
  agent works from "a story about what happened," not what happened, and can't tell the summary
  is wrong. Mitigation: preserve verbatim the small set of critical facts (active incident,
  active repo, key decisions) as structured state ALONGSIDE any prose summary, so they're never
  lost to lossy compression.
- **Tool outputs dominate context — mask them.** Replace verbose tool results with compact
  references once processed; keep only the most recently relevant verbatim.
- **Token estimates drift for code/paths.** The ~4 chars/token heuristic breaks on code and file
  paths (each slash/dot is a token). Use the real tokenizer for any budget-critical trimming,
  or Duckln will mis-budget its own context.
- **More context ≠ better.** Because of context rot, selecting the RIGHT small slice beats
  sending everything. Bias toward less, chosen well.

---

## 6. How this fixes real Duckln understanding failures (not just the screenshot)

| Failure | Which lever fixes it |
|---|---|
| "what is the issue?" → generic after an error | WRITE incident + SELECT it by intent |
| Greeting triggers 8s think | SELECT nothing for chat; deterministic route (no LLM) |
| "when will this be done?" → lost | WRITE plan status + SELECT it for status intent |
| Long session drifts / forgets the repo | COMPRESS via compaction, preserving active_repo |
| Re-runs a command that already failed | WRITE prior_failures + SELECT into planning |
| "what repo am I on?" → unsure | WRITE active_repo + SELECT it |
| Context window bloats, replies degrade | COMPRESS tool outputs + bounded captures |
| Returns to a repo, re-learns everything | Long-term per-repo memory, SELECTed on return |

The screenshot was one symptom of a missing context pipeline. Building the pipeline fixes the
whole class.

---

## 7. Responder guidance (how the model should USE selected context)

When a slice is selected, inject it with a clear instruction to answer FROM it. General shape:

```
The user's message concerns {{selected_context_type}} in this session. Answer directly from the
context below; do not give a generic reply or ask them to restate what is already known.

{{selected_state_slice}}

Answer concisely (1–2 sentences for status/incident questions), state the most useful next step
if there's an obvious one, and do not invent details beyond the provided context.
```

Keep the injected slice SMALL and RELEVANT (Select + Compress). The responder's job is to speak
from the curated context, not to re-derive it.

---

## 8. Rollout — phased, matches the published maturity path

The sources describe a clear maturity path; Duckln should follow it, not over-build.

- **Phase 1 (fixes the observed bugs):** structured session state (WRITE) + intent-driven
  selection (SELECT) + bounded captures (COMPRESS-lite). No new dependencies. This is the MVP
  and it closes the incident/greeting/status failures.
- **Phase 2 (long sessions):** add compaction when history nears the window, preserving
  structured critical facts; add tool-result clearing/observation masking.
- **Phase 3 (cross-session, only if needed):** per-repo and per-user long-term profiles,
  selected on return; consider a dedicated memory framework only if recall demands it.

Don't jump to Phase 3 frameworks up front — the literature is clear that a scratchpad + a simple
store handles most single-tenant agents well into production; premature memory infrastructure is
a common over-build.

---

## 9. Summary for the engineer

Duckln's understanding gaps are a missing context-engineering pipeline, not a prompting or
phrase-matching problem. Build the four published levers around one structured Session State:
WRITE everything Duckln learns as bounded structured records (active_repo, active_incident,
active_plan, prefs); SELECT only the intent-relevant slice into each turn (driven by the routing
cascade, not keywords); COMPRESS captures and long histories (bounded excerpts, tool-result
clearing, compaction that preserves critical facts verbatim); and keep ISOLATING heavy work in
sub-agents. Add a short-term/long-term memory split, respect the context-rot and lossy-compaction
warnings, and roll out in three phases starting with the state+selection MVP that fixes the
current bugs. This is the same architecture Anthropic and the 2026 agent-memory field converge
on — adapted to Duckln's local-first, terminal scale.

---

## Sources consulted (all current as of this writing)
- Anthropic — "Effective Context Engineering for AI Agents" (engineering blog): the finite-
  resource principle, compaction, structured note-taking, tool-result clearing.
- Anthropic — memory & context-management cookbook (Claude Developer Platform): compaction,
  context editing/tool-result clearing, memory tool primitives.
- LangChain — "Context Engineering for Agents": the write / select / compress / isolate
  framework; scratchpad as runtime state; summarization at boundaries.
- Microsoft/Salesforce — "LLMs Get Lost in Multi-Turn Conversation": ~40% multi-turn accuracy
  drop without context management.
- Chroma / Anthropic — "context rot": recall degrades as context grows.
- 2026 agent-memory landscape — Mem0 (ECAI 2025; token-efficient memory, April 2026), Zep,
  LoCoMo / LongMemEval / BEAM benchmarks, Sourcegraph and Fountain City context-engineering
  guides (short-term vs long-term tiers, phased rollout, tokenizer-accurate budgeting).