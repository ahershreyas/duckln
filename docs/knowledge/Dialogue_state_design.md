# Duckln — Dialogue State & Expectation Tracking (Design)

**Audience:** the engineer fixing Duckln's conversational follow-through.
**Scope:** approach, lifecycle, evaluation order, and prompt guidance. No implementation code.
**Reads on top of:** the intent-routing cascade (INTENT_ROUTING_DESIGN.md) and the context
pipeline (DUCKLN_CONTEXT_ENGINEERING.md). This is the missing layer that makes those two
actually follow a conversation.

---

## 1. The failure this fixes (from the screenshot)

Observed sequence:
- Duckln: "We were previously working on JustHireMe (github.com/vasu-devs/JustHireMe)."
- User: "yes lets run this" → Duckln: "Did you mean a repo task, or are we just chatting?"
- User: "yes the repo task" → Duckln asks a DIFFERENT clarifier.
- User: "yes repo setup issue" → Duckln repeats the FIRST clarifier. → infinite clarify loop.

What DID work: recalling JustHireMe from context. So recall is fine. The break is in follow-
through: Duckln treats each message as a fresh, standalone utterance to classify, when several
of these messages are REPLIES to something Duckln just said.

---

## 2. Root cause — the router is stateless; conversation is stateful

The router classifies every message in isolation. But:
- "yes lets run this" has NO repo signal on its own — its meaning is entirely "confirm the thing
  Duckln just proposed (run JustHireMe)."
- "yes the repo task" is an ANSWER to the clarify question just asked — not a new request to be
  re-classified.

A per-message classifier — however good, however large the model — can NEVER fix this, because
the signal is not in the message. It is in the DIALOGUE STATE: what Duckln last proposed or
asked. The fix is to track that state and consult it first.

This is standard dialogue-state tracking. It is NOT a word-list fix; "yes/sure/go ahead/yep run
it" and every future variant are handled because the message is matched against a tiny closed
set (affirm / decline / pick-an-option), not open-ended intent.

---

## 3. The core concept — a pending "expectation"

Add one object to session state: the current `expectation` — what Duckln is waiting for a reply
to. It is set whenever Duckln proposes an action or asks a question, and consumed by the next
user message.

Expectation shape (conceptual):
- `type`: one of `confirm_action` | `choose_option` | `provide_value` | `none`
- `payload`: the concrete thing pending
  - for `confirm_action`: the action to run if confirmed (e.g. "run repo JustHireMe")
  - for `choose_option`: the list of options offered (so an answer can be matched to one)
  - for `provide_value`: what value is being requested (e.g. "DATABASE_URL")
- `origin`: what created it (a proposal, a clarify question, a plan gate)
- `age`/`turn`: to expire stale expectations

Only ONE expectation is active at a time (the most recent). Setting a new one replaces the old.

---

## 4. When to WRITE an expectation

Duckln sets an expectation whenever its own message invites a reply:

- **It proposes an action** → `confirm_action`. "We were working on JustHireMe" (implicit
  proposal to resume) or "I'll run X — go ahead?" sets `{type: confirm_action, payload: run
  JustHireMe}`.
- **It asks a clarify question** → `choose_option`, with the offered options in the payload so an
  answer can be matched. "npm or pnpm?" → `{type: choose_option, options: [npm, pnpm]}`.
- **It requests a value** → `provide_value`. "What DATABASE_URL should I use?" → `{type:
  provide_value, payload: DATABASE_URL}`.
- **It says something that needs no reply** (a plain statement, a completed result) → clear the
  expectation (`type: none`).

Rule: if Duckln's message ends by inviting a decision from the user, an expectation MUST be
written. The screenshot bug is partly that proposals/clarifies weren't registering an
expectation at all.

---

## 5. The evaluation order — consult the expectation FIRST

This ordering is the heart of the fix. For each incoming user message, in THIS order:

1. **Is there an active expectation?** If yes, try to resolve the message AGAINST it before any
   fresh intent classification:
   - `confirm_action`: is the message an affirmation, a negation, or neither?
     - Affirm ("yes", "sure", "go ahead", "yep run it", any phrasing) → EXECUTE the payload
       action. Clear the expectation.
     - Negate ("no", "not that", "cancel") → drop the action, ask what they'd prefer. Clear.
     - Neither / clearly a new topic → the user moved on; clear the expectation and fall to step
       2 (fresh classification).
   - `choose_option`: does the message pick one of the offered options (by name, number, or
     paraphrase)? If yes → proceed with that option, clear. If it's a fresh affirm with a single
     obvious default, take it. If unrelated → clear and go to step 2.
   - `provide_value`: does the message supply the value (or refuse)? Consume it, clear.
2. **No active expectation (or message didn't address it):** run the normal intent cascade
   (deterministic signals → LLM classifier with context → clarify).

The affirm/negate/option-match check is a SMALL closed classification (yes / no / which-option),
far easier and more reliable than open-ended intent — and it's where "yes lets run this" resolves
correctly, because it's read as "affirm the pending confirm_action," not as a standalone message
with no repo signal.

Use the LLM tier for the affirm/negate/option match only when it's not an obvious lexical match —
but always against the closed set, with the pending expectation in the prompt as context.

---

## 6. Clarify-loop breakers (hard rules — these stop the screenshot loop)

- **A clarify answer is ALWAYS consumed against the pending clarify.** When an expectation of
  type `choose_option` (or any clarify) is active, the next message is treated as its answer —
  never re-classified from scratch. This alone prevents "yes the repo task" from triggering a new
  clarifier.
- **Never ask the same clarify question twice in a thread.** Track asked clarifies; if resolution
  is still unclear, do NOT re-ask — either take the best interpretation or ask a DIFFERENT,
  narrower question.
- **Cap clarifies at 2 per thread.** After two, stop asking: act on the most likely
  interpretation and say so plainly ("I'll assume the repo task — say 'stop' if not"), rather than
  looping. A wrong assumption the user can correct beats an infinite loop.
- **Expire stale expectations.** If the user clearly changes topic, clear the expectation so a
  dangling clarify doesn't hijack an unrelated later message.

---

## 7. Generate clarify options from state, not a fixed menu

Second bug in the screenshot: the clarifier offered "repo setup issue, all tracked repos, or
system capacity?" — a canned menu unrelated to the live context (the pending thing was RUN
JustHireMe).

- Clarify options MUST be generated from the current expectation/context, not a static list.
- If the pending action is "run JustHireMe", the only sensible clarify (if any) is about THAT —
  e.g. "run it locally or in the VM?" — never a generic repo menu.
- If there's a clear pending `confirm_action` and the user affirms, there is NOTHING to clarify —
  just execute. Do not clarify a confirmation.

---

## 8. Worked trace — how the screenshot SHOULD go

- Duckln: "We were working on JustHireMe." → WRITE `{confirm_action: run JustHireMe}`.
- User: "yes lets run this" → expectation active, message = affirm → EXECUTE run JustHireMe. No
  clarifier. (This is the whole fix.)

And if a clarify were genuinely needed:
- Duckln: "Run JustHireMe locally or in the VM?" → WRITE `{choose_option: [local, vm]}`.
- User: "vm" (or "the second one", or "in the vm please") → matched to option `vm` → proceed. No
  re-classification, no loop.

---

## 9. How this sits on the existing designs

- **Context pipeline (WRITE/SELECT):** already captures active_repo/incident/plan. Expectation is
  a NEW piece of session state in the same scratchpad — written by Duckln's own messages, selected
  first on the next turn.
- **Intent cascade:** unchanged, but now runs SECOND — only when there's no expectation to resolve
  the message against. This is the ordering fix: reply-to-pending before classify-fresh.
- **Incident handling:** an incident question is still handled by intent; expectation handles
  replies to Duckln's proposals/questions. They're complementary, both consulting session state.

---

## 10. Test cases the system MUST pass

- Duckln proposes resuming JustHireMe → "yes lets run this" → RUNS it, no clarifier. ← the bug
- "sure", "go ahead", "yep do it", "run it" after a proposal → all execute (any phrasing).
- "no" / "not that one" after a proposal → cancels, asks preference, no loop.
- Clarify "npm or pnpm?" → "pnpm" / "the second" / "use pnpm" → proceeds with pnpm.
- Answering a clarify NEVER triggers a fresh clarifier.
- The same clarify question is never asked twice; max 2 clarifies, then act with a stated
  assumption.
- User changes topic mid-expectation ("actually, what's the weather") → expectation cleared,
  handled fresh.
- Clarify options always reflect the live pending thing, never a canned unrelated menu.

---

## 11. Summary for the engineer

The router is stateless but conversation is stateful — that's the whole bug. "yes lets run this"
and clarify answers carry no standalone signal; their meaning is "reply to what Duckln just said."
Fix it by adding a single `expectation` object to session state, WRITTEN whenever Duckln proposes/
asks/requests-a-value, and EVALUATED FIRST on the next message (affirm/negate/pick-option against
a tiny closed set) before any fresh intent classification. Add hard loop-breakers: clarify answers
are always consumed against the pending clarify, never re-classified; never ask the same clarify
twice; cap at 2 then act on the best interpretation; expire stale expectations; and generate
clarify options from live state, not a fixed menu. This is standard dialogue-state tracking — it
works for every phrasing because it matches replies against yes/no/which-option, not open-ended
intent — and it's the missing follow-through layer on top of the context and routing designs.