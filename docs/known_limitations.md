# Duckln Known Limitations

**Scope**
- Task: `Prepare known limitations list`
- Requirements / Plan basis:
  - `R1` / `15`
  - `R8`, `R9` / `16`
  - current checked tasks in `docs/tasks.md`

This list reflects the current repository behavior only.

## Current Limitations

1. Packaging is not complete.
   - Standalone executables for macOS Intel, macOS Apple Silicon, and Ubuntu are still an open Phase 8 task.

2. Release sign-off is not yet complete.
   - The release gate cannot be fully closed while packaging remains unchecked.

3. Prompt rendering does not yet show a dedicated active-mode prompt indicator.
   - Mode behavior exists, but `R3` mentions prompt reflection and there is no explicit prompt-state artifact for that yet.

4. Verification is covered in logic and tests, but repeated-failure session summaries are not implemented.
   - `Plan 16` remains open.

5. Provider adapter testing is mocked rather than using live external services.
   - This is intentional for safety and repeatability, but it means live-provider behavior is not exercised in CI.

6. Manual scenario testing is documented but requires human execution.
   - The repository now includes scenario scripts/checklists, not recorded execution results.

7. `/healthcheck` validates the core environment and provider connectivity, but it is not a full system auditor.
   - It checks Python, `pip`, key dependencies, provider connection, and configured model validation only.

8. The command palette and runtime config flows exist, but broader onboarding polish and provider docs links are not implemented.
   - `Plan 15` remains open.

9. The current release artifacts are documentation-driven.
   - Launch readiness docs exist, but no binary distribution or installer flow is present yet.

## Not Limitations

The following are already present and should not be listed as missing:
- OpenRouter, OpenAI, and Anthropic onboarding
- provider key validation and model validation
- HITL, HOTL, and HOOTLWO mode policy behavior
- blocked command matching and controlled subprocess execution
- privacy redaction and minimal payload shaping
- Python/AI error classification
- bounded command suggestions
- verification planning
- concise teaching snippets
- structured redaction-safe logging
- traceability map
