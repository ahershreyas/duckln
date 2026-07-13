# Duckln Intent Routing — Developer Brief (Layered Cascade)

**Audience:** the engineer building Duckln's message router.
**Scope:** approach, architecture, and the EXACT prompts to use — no implementation code.
**Problem being fixed:** the router misroutes casual/typo'd input. "how r you?" was sent to
a repo clarifier ("do you mean JustHireMe status or path?") while "how are you?" was handled
correctly. The router guesses instead of understanding, and its wrong default is "assume repo
task."

---

## 1. The goal — restdate honestly

Do NOT target 99.999% intent-classification accuracy. It is not achievable and no credible
research claims it. The evidence:

- Best transformer intent classifiers reach ~94.8% macro-F1; the irreducible error sources
  are multi-intent utterances (~21%) and semantic ambiguity (~32%).
- On genuinely ambiguous messages, even human annotators only agree ~75% of the time. If
  humans can't agree what "when will this be done?" means without context, no classifier can
  hit five-nines on it.

**The real target:** high accuracy on clear input, and GRACEFUL handling on unclear input —
i.e. ask a clarifying question instead of silently firing the wrong path. Near-100% *graceful
behavior*, not near-100% classification.

---

## 2. The architecture — a 4-tier cascade with confidence-gated escalation

This is the production-standard pattern (fast heuristics first, expensive reasoning only when
needed). Each tier handles what it can and escalates only what it can't.

```
user message
    │
    ▼
TIER 1  Fast deterministic filter  (microseconds)
    │   normalize + detect positive REPO signals
    ├── clear repo task ──────────────► repo/plan pipeline
    ├── clear conversation ───────────► conversation_agent
    └── uncertain ▼
TIER 2  Fallback default = CONVERSATION
    │   (no repo signal ⇒ treat as chat, do NOT assume repo)
    └── but signals present AND still ambiguous ▼
TIER 3  LLM intent classifier  (local model, e.g. Gemini ~9B)
    │   classify WITH conversation context → repo | conversation | status | ambiguous
    ├── confident ───────────────────► route accordingly
    └── low confidence ▼
TIER 4  Clarify — ask ONE question, never silently guess
        (this is what fixes the screenshot)
```

Why this shape: fast heuristics run in microseconds and handle the bulk; the expensive LLM
tier fires only on the hard minority; and the clarify tier converts an impossible top-1 guess
into an easy top-k question answered by the user.

---

## 3. Tier 1 — fast deterministic filter (approach, no code)

**Step A: Normalize.** Before any matching: lowercase, collapse whitespace, and expand common
chat contractions via a small static map — `r`→are, `u`→you, `ur`→your, `plz`→please,
`wanna`→want to, `gonna`→going to, `dunno`→don't know, etc. This alone fixes "how r you?".
Keep the map small; it is a convenience layer, not the main mechanism.

**Step B: Detect positive REPO signals** (the scalable core). Do NOT try to enumerate every
way a human says hi — that set is infinite. Instead enumerate the small, finite set of signals
that mean "this is a repo job":
- A repo reference: a URL, an owner/name pattern, or a known local repo path.
- A file/dir path.
- An action verb from a fixed list: set up, run, build, clone, install, fix, deploy, test,
  lint, migrate, start, compile, etc.
- An explicit slash command (`/discover`, `/loop`, etc.) — always deterministic.

**Step C: Route on signal presence.**
- Repo signal present, unambiguous → repo/plan pipeline.
- No repo signal at all → fall through to Tier 2 (conversation).
- Repo signal present but ambiguous (e.g. two candidate repos, unclear verb target) → Tier 3.

Hint: keep the verb/keyword lists and the contraction map in one config module so they can be
edited without touching routing logic.

---

## 4. Tier 2 — flip the fallback (the structural fix)

The current bug is that the unmatched-default is "assume repo task." **Invert it.**

- New rule: **no positive repo signal ⇒ the message is conversation**, routed to
  `conversation_agent`. Full stop.
- Only invoke the repo/plan machinery when Tier 1 found an actual repo signal.

This is why you don't need to list small-talk phrasings. "r you ok?", "wassup", "u good?",
"tell me a joke" all have zero repo signals → they land in conversation automatically, with no
per-phrase rule. You maintain the *small* list of repo triggers; everything else is chat by
construction.

---

## 5. Tier 3 — LLM intent classifier (the uncertain middle)

Fires ONLY when Tier 1 found a repo signal but couldn't resolve it, OR when a message is
context-dependent (e.g. "when will this be done?", "is it ready?", "how long?") which has no
repo keyword yet clearly refers to active work.

**Model:** Duckln's local model is fine here (a ~9B such as Gemini 9B works). It gives real
language understanding — far better than keyword rules on "r you ok?" and context-dependent
phrasing — but it is NOT frontier-level, so:
- Expect weaker results on genuinely ambiguous / long multi-turn cases.
- Give it the conversation context, not just the single message (this is what resolves status
  questions — the research calls this intent-with-context / intent rewriting).
- Keep its budget tight and allow one extra retry for shaky JSON adherence (same small-model
  tiering used elsewhere in Duckln).

**It must return a confidence.** Low confidence ⇒ do NOT act ⇒ fall to Tier 4 (clarify).

### Tier 3 — EXACT prompts (use verbatim; do not paraphrase away the structure)

**SYSTEM PROMPT (Tier-3 classifier):**
```
You are Duckln's intent classifier. Duckln is a terminal-first repository manager: it sets up,
runs, explains, and fixes code repositories, and it can run recurring monitoring loops.

You are given the recent conversation and the user's latest message. Decide what the user's
latest message intends, USING the conversation context — a short message like "when will this
be done?" or "is it ready?" usually refers to work already in progress earlier in the
conversation, not a new request.

Classify into EXACTLY ONE intent:
- "repo_task": the user wants Duckln to act on a repository (set up, run, build, clone,
  install, fix, deploy, test, or similar), OR is answering/continuing such a request.
- "status": the user is asking about the state, progress, or timing of work already underway
  in this conversation ("when will this be done?", "is it ready?", "how long?").
- "conversation": small talk, greetings, thanks, questions about you, or anything not about a
  repository or ongoing work ("how are you?", "r you ok?", "tell me a joke").
- "ambiguous": you genuinely cannot tell which of the above it is, even with the context.

Rules:
- Judge the LATEST message, using earlier turns only as context.
- Do not assume "repo_task" just because Duckln is a repo tool — most greetings are
  "conversation".
- If the message has no repository reference and no tie to ongoing work, it is "conversation",
  not "ambiguous".
- Use "ambiguous" only when two or more intents are genuinely plausible and you cannot choose.
- Output ONLY one JSON object, no prose, no markdown fences:
  {"intent": "repo_task|status|conversation|ambiguous", "confidence": 0.0-1.0, "rationale": "one short line"}
- "confidence" is your genuine certainty. Use < 0.6 when unsure — a low score sends the
  message to a clarifying question, which is safer than guessing wrong.
```

**USER PROMPT (Tier-3 classifier) — fill the two slots, do not add anything else:**
```
Recent conversation (oldest to newest):
{{conversation_context}}

User's latest message:
{{latest_user_message}}

Classify the latest message. Output only the JSON object.
```

Notes for the engineer:
- `{{conversation_context}}` = the last few turns (roughly 4–6), each labeled with who spoke.
  Keep it short; the classifier needs recent context, not the whole history.
- `{{latest_user_message}}` = the raw latest message (do NOT normalize this one — the model
  should see it as typed; normalization is only for the Tier-1 keyword match).
- Parse the JSON; if `confidence` ≥ threshold (start at 0.6, tune later) act on `intent`, else
  go to Tier 4. If `intent` is "ambiguous" at any confidence, go to Tier 4.

---

## 6. Tier 4 — clarify, never silently guess (the actual screenshot fix)

When Tier 3 is low-confidence or returns "ambiguous", Duckln asks ONE short clarifying question
instead of firing the repo clarifier or guessing. This is the single change that would have
prevented the screenshot.

Crucial: the clarify question must offer the RIGHT axis — "did you mean a repo task, or are we
just chatting?" — NOT the wrong repo-only clarifier ("do you mean JustHireMe status or path?")
that assumes repo intent it hasn't established.

### Tier 4 — EXACT prompt

**SYSTEM PROMPT (Tier-4 clarifier):**
```
You are Duckln, a terminal-first repo manager. The user's last message was ambiguous — it
might be a request to work on a repository, or it might just be conversation, and you could not
tell which.

Ask ONE short, friendly clarifying question that lets the user resolve it in a word or two. Do
NOT assume it is about a repository. Do NOT list repo-specific options (like "status or path")
unless you have already established the user is talking about a repo — you have not.

Keep it to one line, in Duckln's dry, practical terminal voice. Offer the two plausible
directions plainly. Do not lecture, do not over-explain.

Output ONLY the clarifying question text — no JSON, no preamble.
```

**USER PROMPT (Tier-4 clarifier) — fill the slots:**
```
Recent conversation (oldest to newest):
{{conversation_context}}

User's latest (ambiguous) message:
{{latest_user_message}}

Ask one clarifying question.
```

Example of the SHAPE it should produce (do not hardcode — let the model phrase it):
"Not sure if you're after a repo job or just saying hello — which is it?"

After the user answers, feed their reply back through Tier 1 (it will now carry a clear signal)
and proceed normally.

---

## 7. The confidence threshold — how to set it

- Start the Tier-3 accept threshold at **0.6**. Above it, act on the intent; at or below it,
  clarify.
- Tune with real logs: if the router clarifies too often (annoying), raise the bar for
  clarifying (lower the threshold slightly); if it still misroutes, lower the bar (raise the
  threshold). This is a dial, set it empirically.
- Never let Tier 3 act on "ambiguous" regardless of confidence — ambiguous always clarifies.

---

## 8. What stays deterministic (do NOT turn these into LLM calls)

- Tier 1 normalization + signal detection: pure code.
- Tier 2 fallback rule: pure code.
- Slash commands: always deterministic, never classified.
- The overall cascade control flow: pure code.
Only Tier 3 (classify the uncertain middle) and Tier 4 (phrase the clarifying question) use the
model. This matches Duckln's principle: mechanical flows stay deterministic; the model is used
only where genuine language understanding is required.

---

## 9. Optional upgrade (later, not now): a small local classifier as Tier 2.5

If, after logging real traffic, the LLM tier fires too often and adds latency, insert a small
fine-tuned classifier between Tiers 2 and 3. Research shows a SetFit-style model can be trained
on as few as ~8 labeled examples per intent in ~30 seconds, runs far faster than a full LLM,
and lands within ~8–10% F1 of a frontier model. It would absorb most of the "uncertain middle"
cheaply, leaving only the hardest cases for the LLM tier. Do this only if the logs justify it —
don't build it up front.

---

## 10. Test cases the router MUST pass (build these as regression tests)

- "how r you?" → conversation (NOT repo clarifier). ← the screenshot bug
- "r you ok?" / "u good?" / "wassup" → conversation.
- "set up JustHireMe" → repo_task.
- "clone github.com/x/y" → repo_task.
- "when will this be done?" (after a bring-up started) → status, resolved against context.
- "is it ready?" (no prior work) → ambiguous → clarify.
- "tell me a joke" → conversation.
- "fix the failing test" (repo active) → repo_task.
- "thanks!" → conversation.
- Random gibberish → clarify, not a repo guess.

---

## 11. Summary for the engineer

Replace the single guessing router with a 4-tier cascade: (1) fast deterministic normalize +
repo-signal filter, (2) fallback default = conversation (never assume repo), (3) a local-LLM
classifier WITH conversation context for the uncertain middle, returning a confidence, (4) a
clarify step that asks one honest question — never the wrong repo-only clarifier — whenever
confidence is low or the intent is ambiguous. Keep everything except Tiers 3 and 4 as
deterministic code. Use the exact system/user prompts above for Tiers 3 and 4. Target graceful
handling, not impossible classification accuracy: right when clear, ask when not.