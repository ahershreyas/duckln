# Duckln — Logical Thinking Log

A running record of the reasoning behind Duckln's design decisions, so anyone can review
WHY a choice was made, not just what was built. Newest entries at the top.

---

## Entry: Context understanding — a context-engineering pipeline, not a phrase fix (Plan 193)

**Trigger:** after multiple "VM `duckln-vm` is not accessible" errors, "what is the issue?" got a
generic "name the repo" reply; and "how you doin?" (a greeting, active repo present) still hit a
repo-only clarifier ("active repo status / verify / repo path"). Per `docs/knowledge/context_engg.md`
these are a MISSING CONTEXT PIPELINE (Anthropic "Effective Context Engineering"; LangChain
write/select/compress/isolate; multi-turn-degradation + context-rot findings), not a prompt problem.

**Root causes (in code):** (1) the VM-inaccessible path just displayed + returned — it never WROTE
an incident, so there was nothing to SELECT; and error lines shown via `display_output` weren't
captured into the turn context. (2) Plan 192 gated only ONE of three repo-clarifier branches — the
`if repo is not None` fallback + the candidates fallback still assumed repo intent from AMBIENT
state. (3) greetings paid an LLM classifier round-trip ("Thought for 4-10s").

**Decision — build the four levers around one Session State (Phase 1 landed):**
- **WRITE** (`state.access.write_active_incident`/`append_recent_event`): a bounded, redacted
  `active_incident` `{summary, command, stderr excerpt (last-N-lines), category, resolved}` + a
  bounded `recent_events` ring, written on failures (incl. the VM-inaccessible/transport paths).
- **SELECT** (intent-driven, not keyword dumps): `_maybe_answer_incident_question` answers "what is
  the issue / why did it fail / what happened / is it fixed" FROM the incident (with the concrete
  next step, e.g. `/vm`); `_incident_context_slice` selects ONLY the incident+recent-events into the
  Tier-3 classifier context; greetings SELECT nothing.
- **RESPOND-from-slice** (`provider_support.conversation_system_prompt`): for a context-relevant
  route with an unresolved incident, inject ONLY that slice with the doc §7 "answer from this, don't
  be generic" instruction; small_talk injects nothing.
- **COMPRESS-lite**: every capture is bounded + redacted (last-N-lines) — never a raw dump.
- **ISOLATE**: unchanged — sub-agents already run in their own context and return distilled results.
- The routing cascade DRIVES selection: the DANGEROUS repo-ASSUMING clarifiers (continue repairing
  {repo} / active repo status / repo path) are gated on a MESSAGE signal so a signal-less greeting
  never gets them; greetings route to CONVERSATION via the deterministic buckets (add "how you
  doin"-class variants); a genuinely AMBIGUOUS no-signal turn still ASKS via the GENERIC
  (non-repo-assuming) clarify ("ask, don't guess" — the brief's ambiguous-turn behavior, and the
  transcript-benchmark quality gate); greetings skip the LLM classifier; and Tier 3 fires for the
  incident/context class with the selected slice. (An earlier draft flipped the router's whole
  terminal-clarify to conversation, but that wrongly converted genuinely-ambiguous turns — which the
  brief says should CLARIFY — into chatter and broke the benchmarks, so it was narrowed to gating
  just the dangerous clarifiers + routing greetings via the buckets.)

**Why deterministic-first + grounded:** when the incident/repo/plan facts exist, the answer is
model-independent and correct; the LLM adds depth over the SAME curated slice, never a disconnected
guess. Selecting the RIGHT small slice beats stuffing history (context rot).

**Guardrails (§5):** preserve critical facts (incident/repo/decisions) as structured state so they
survive any future compaction; MASK tool outputs; tokenizer-accurate budgeting is the correct trim
signal (chars/4 drifts on code/paths); less-is-more selection.

**Phase 2/3 — BUILT in Plan 194 (the §5 guardrails + long-term recall, excluding the vector/graph
framework):** F1 PINS the critical facts (active_incident/active_repo/decisions) VERBATIM in the
agent-loop render (`AgentState.pinned_facts` → `render_state_for_llm`), so compaction can never turn
them into a lossy story. F2 adds a code/path-AWARE `budget_tokens` (chars/4 + one token per `/`·`.`·
`_`·`-` run) for budget-critical trims, alongside the existing model-aware `usable_context_tokens`.
F3 MASKS tool results to a compact `<tool> → gist` reference once they leave the most-recent slot
(Anthropic tool-result clearing). F4 makes the render MODEL-AWARE — a small local window keeps fewer
recent observations verbatim (compacts sooner) + caps the char budget to the model window; the
pinned facts always survive. F5 SELECTs a repo's long-term profile (per-repo facts + user prefs) into
the CONVERSATION prompt (planning already consumed it), so chatting about a repo answers from memory;
F6 surfaces a one-line recall when a repo is re-selected. The vector/graph FRAMEWORK stays deferred
(doc §4/§8 + the keyed store suffices). WHY: compaction-with-verbatim-facts prevents the ~40%
multi-turn drift from losing the active repo/incident; masking stops tool-output bloat; long-term
recall stops re-learning a returning repo. — The remainder below is the original Plan-193 framing.

**(Original Plan-193 next-list, now largely delivered by Plan 194):** (2) COMPRESS at scale — conversation-history
COMPACTION (reuse `context_budget.map_reduce_summarize`) when history nears the model window,
preserving the critical structured facts VERBATIM, plus tool-result clearing/observation masking
(extend `loop._OBSERVATION_BUDGET_CHARS`) and tokenizer-accurate budgeting. (3) LONG-TERM tier —
SELECT `read_repo_facts`/`read_user_preferences`/`read_common_lessons` into context when a repo/user
RETURNS (store + write-on-success already exist; consumption is the wiring). A vector/graph memory
FRAMEWORK (Mem0/Zep) stays deferred — the doc itself (§4/§8) calls it premature for Duckln's
single-tenant scale; the keyed store + signature lookup suffice. WHY build these: multi-turn accuracy
degrades ~40% without context management, and long sessions otherwise drift/forget the active repo —
compaction-with-verbatim-facts and long-term recall keep understanding sharp as sessions grow and
across returns.

---

## Entry: Intent routing — why a 4-tier cascade, not a better classifier

**Date:** 2026-07 (recorded during design review)
**Trigger:** A user typed "how r you?" and Duckln replied with a repo clarifier ("do you mean
JustHireMe status or path?"). The same user typing "how are you?" was handled correctly. This
exposed a routing bug.

### The problem, diagnosed
Duckln's message router is deterministic (correct by design — intent routing must be
predictable, not a free-LLM guess). But it was matching on keywords/patterns, so "how r you?"
did not match the small-talk pattern that "how are you?" matched. It then fell through to a
WRONG default: "assume this is a repo task" → fired the repo clarifier. Two faults: (1) no
handling of casual/typo'd phrasing, (2) the unmatched-default assumed repo intent instead of
conversation.

### Why not "just fix it with more keyword rules"
A contraction map (r→are, u→you) fixes "how r you?" but does NOT scale — "r you ok?",
"wassup", "u good?", "hows it goin" and endless variants would still slip through. You cannot
enumerate every way a human makes small talk; that set is effectively infinite.

### Why not "just be Claude" (use a big model for every message)
Claude gets these right because it is a frontier model understanding each message natively,
with full context, in one pass — no separate routing step to misfire. But running a
frontier-scale model on every message is expensive and slow. Duckln is terminal-first and
often local, so it cannot afford that on every turn. It uses cheap routing precisely to stay
fast/cheap.

### The honest accuracy ceiling (why not target 99.999%)
Research reviewed:
- Best transformer intent classifiers reach ~94.8% macro-F1; dominant errors are multi-intent
  (~21%) and semantic ambiguity (~32%) — irreducible.
- On ambiguous messages, even human annotators agree only ~75% of the time. If humans can't
  agree what "when will this be done?" means without context, no classifier hits five-nines.
Conclusion: 99.999% classification is not a real target. The real target is high accuracy on
clear input + GRACEFUL handling (ask, don't guess) on unclear input.

### The decision: 4-tier cascade with confidence-gated escalation
This is the production-standard pattern in the literature (fast heuristics first, expensive
reasoning only when needed):
1. Fast deterministic filter: normalize + detect positive REPO signals (repo/URL/path/action
   verbs). Handles the clear majority in microseconds.
2. Flip the fallback: no repo signal ⇒ conversation by default. Enumerate the SMALL set of
   repo triggers; everything else is chat by construction. (This is the structural fix — you
   list repo intents, not small-talk phrasings.)
3. LLM classifier for the uncertain middle: a LOCAL model (e.g. Gemini ~9B) classifies with
   conversation context → repo_task / status / conversation / ambiguous, returning a
   confidence. Context is what resolves "when will this be done?" correctly (intent-with-
   context / intent rewriting in the research).
4. Clarify, never silently guess: on low confidence or "ambiguous", ask ONE honest question
   offering the right axis ("repo job or just chatting?") — NOT the wrong repo-only clarifier
   that assumes repo intent. This is the single change that would have prevented the
   screenshot.

### Why a local 9B model is acceptable for Tier 3
It gives real language understanding (far better than keyword rules on "r you ok?" and
context-dependent phrasing). It is not frontier-level, so: expect weaker results on genuinely
ambiguous/long multi-turn cases, give it conversation context, keep its budget tight, allow one
extra retry for shaky JSON. Good enough as the escalation tier, backed by the Tier-4 clarify
safety net.

### Optional future upgrade (deferred)
If logs show the LLM tier fires too often (latency), insert a small fine-tuned classifier
(SetFit-style: ~8 examples/intent, ~30s training, near-frontier F1, far faster) between Tiers 2
and 3 to absorb the uncertain middle cheaply. Deferred — build only if traffic justifies it.

### What stays deterministic
Tiers 1–2, slash commands, and the cascade control flow stay pure code. Only Tier 3
(classify uncertain) and Tier 4 (phrase clarification) use the model. Consistent with Duckln's
principle: mechanical flows deterministic; the model only where real language understanding is
needed.

### Net outcome expected
Mid-90s% classification accuracy (the research ceiling) PLUS near-100% graceful behavior —
right when clear, ask when not. The screenshot bug is fixed not by a smarter classifier but by
Tier 4: when unsure, ask; never silently fire the wrong path.

### Sources consulted
Anthropic "Building Effective Agents" (routing pattern); production routing write-ups
(tianpan.co, getmaxim.ai) for the cascade + embedding/SetFit figures; RECAP (arXiv 2509.04472)
and IntentRL (arXiv 2511.10453) for ambiguity/intent-rewriting; "Balancing Accuracy and
Efficiency in Multi-Turn Intent Classification" (arXiv 2411.12307); intent-detection SLR
(~94.8% macro-F1, error taxonomy).

### Implementation (Duckln) — Plan 192, what was built and how it maps to Duckln
Built the 4-tier cascade by ALIGNING Duckln's existing front-door cascade + conversation
supervisor to the doc, not by rewriting them (lowest risk for the 2500-test suite):
- **Tier 1 normalization (F1):** a small static `_CHAT_CONTRACTIONS` map expanded WORD-level inside
  `conversation_routes/normalizer.py::normalize_compact_message` — `r`→are, `u`→you, `ur`→your,
  plz/pls, wanna/gonna/gotta, dunno, hows→how is, whats→what is, abt, cuz, ure. "how r you?" now
  normalizes to "how are you" → the rapport bucket → conversation. Whole-token only (never inside a
  word); the RAW message is what the Tier-3 LLM sees. (Dropped bare `y`/`n` as too aggressive — not
  in the doc's examples.)
- **Tier 2 flip (F2):** a deterministic `normalizer.has_repo_signal()` (repo URL / owner-name / file
  path / a fixed "repo job" verb list). The dangerous, repo-ASSUMING clarifier — the workflow-anchored
  `continue repairing {repo} / status / path` fallback in `conversation_routes/recovery.py`
  (`clarification_options_for_message`) — is now GATED on a real MESSAGE signal (`has_repo_signal` OR a
  lifecycle-ambiguity phrasing OR a pronoun follow-up). A signal-less greeting/small-talk with an
  active repair objective ("how r you?") can no longer produce it; a genuinely ambiguous turn ("the
  thing is not working") still gets the GENERIC narrow-down clarify (doc-faithful: ask, don't guess).
  (An earlier draft flipped the whole router terminal-clarify to `small_talk`, but that wrongly
  converted legitimately-ambiguous/opaque turns — which the doc's Tier 4 says should CLARIFY — into
  chatter, so it was narrowed to gating just the dangerous clarifier.) Plus a small convenience layer
  (like the contraction map): the common §10 chatter forms ("r you ok?", "u good?", "wassup",
  "thanks!", "tell me a joke") are added to the greeting/rapport/`smalltalk` buckets so they classify
  as conversation (mapped to the `small_talk` family), not a clarify.
- **Tier 3 classifier (F3):** `conversation_routes/llm_intent.py::classify_intent_with_context` uses
  the doc's SYSTEM + USER prompts VERBATIM (intents repo_task/status/conversation/ambiguous +
  a genuine confidence; JSON-only; one retry for shaky small-model JSON). Wired at the front door
  (`main.py::_maybe_autoroute_repo_agent`) as the uncertain-middle escalation — fires only when the
  deterministic + repo-relevance tiers didn't resolve AND the message has a repo signal OR is a
  context-dependent status question. Threshold 0.6: act (repo_task→ask/do on an active repo,
  status→session-meta answer, conversation→supervisor) else Tier 4. Recent turns (labeled) are the
  `{{conversation_context}}`.
- **Tier 4 clarify (F4):** `llm_intent.py::phrase_axis_clarification` uses the doc's Tier-4 prompts
  VERBATIM — one line, the repo-vs-chat axis, never repo-only options; a deterministic neutral line
  when there's no model (never a dead-end).
- **Deterministic vs model (doc §8):** tiers 1–2 + `has_repo_signal` + the cascade control flow are
  pure code; only tiers 3–4 call the model. Model-agnostic (one prompt, any provider), proven on
  real gemma2:9b + 2b.
- **Recording:** each Tier-3/Tier-4 decision (tier, intent, confidence, rationale) is appended to
  the runtime `memory/logical-thinking.md` (`recovery.append_thinking_log(surface="routing")`) so
  routing is observable — this design entry records the WHY.
- **Honest partial:** the doc's optional §9 SetFit Tier-2.5 is deferred (build only if logs justify).
  Tier-3 accuracy on a small local model is mid-90s (doc §1) — Tier-4 clarify is the safety net.