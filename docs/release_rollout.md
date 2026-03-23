# Duckln Rollout Checklist and Rollback Notes

**Scope**
- Task: `Prepare rollout checklist and rollback notes`
- Requirements / Plan basis:
  - `R10` / `12`
  - release discipline from `README.md`
  - current task status from `docs/tasks.md`

## Rollout Checklist

### Documentation and Traceability

- [x] `docs/requirements.md` is present and current.
- [x] `docs/plan.md` is present and current.
- [x] `docs/tasks.md` is present and current.
- [x] `docs/analysis.md` is present.
- [x] `docs/traceability.md` maps requirement → plan → task → test.

### Core Product Readiness

- [x] Onboarding supports OpenRouter, OpenAI, and Anthropic.
- [x] Model validation happens before config save.
- [x] Modes HITL, HOTL, and HOOTLWO are implemented.
- [x] Controlled shell execution, timeout handling, and blocked-command safety are implemented.
- [x] Privacy redaction and minimal payload shaping are implemented.
- [x] Python/AI error classification is implemented.
- [x] Suggestion and verification planning are implemented.
- [x] Structured redaction-safe logging is implemented.

### Testing Readiness

- [x] Unit tests for safety rules are marked complete.
- [x] Unit tests for classifier rules are marked complete.
- [x] Unit tests for payload redaction are marked complete.
- [x] Integration tests for provider adapters are marked complete.
- [x] Integration tests for verification loop are marked complete.
- [x] Manual scenario documentation is present in `docs/manual_scenarios.md`.

### Launch Docs

- [x] First-launch demo script is present in `docs/first_launch_demo.md`.
- [x] Known limitations list is present in `docs/known_limitations.md`.
- [x] Rollout checklist and rollback notes are present in this document.

### Open Release Blockers

- [ ] Standalone packaging/install instructions for macOS Intel, macOS Apple Silicon, and Ubuntu are complete.
- [ ] Final release sign-off can be marked complete.

## Release Decision Rule

Release sign-off may be marked complete only when:
- all linked tasks needed for the intended release are checked in `docs/tasks.md`
- no required launch artifact is missing
- packaging status matches the intended release channel

Under the current repository state:
- documentation-only launch readiness is complete
- packaging is still open
- final release sign-off should remain blocked

## Rollback Notes

If a release candidate is unsafe or incomplete:

1. Stop release promotion.
   - Do not mark release sign-off complete.

2. Revert to the last known good revision.
   - Use normal version control rollback practices for the release branch or tag.

3. Preserve traceability.
   - Keep `docs/tasks.md`, `docs/traceability.md`, and release notes aligned with the rollback point.

4. Review redaction and safety behavior first.
   - If the issue involves privacy or command execution, confirm:
     - secrets are still redacted
     - blocked-command behavior still fires
     - HOTL approval is still enforced
     - HOOTLWO auto-run remains whitelist-only

5. Re-run the minimum required validation set.
   - provider adapter tests
   - verification-loop tests
   - safety tests
   - manual scenario checklist as needed for the failed area

6. Update launch docs before attempting another rollout.
   - revise known limitations
   - revise rollout blockers
   - revise sign-off status

## Sign-Off Status

Current status: `Blocked`

Reason:
- The Phase 8 packaging task is still open.
- Because of that open linked task, `Mark all linked tasks complete before release sign-off` should remain unchecked.
