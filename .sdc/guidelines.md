# Duckln SDC Guidelines

## Purpose

These guidelines govern all specification-driven work for Duckln.

## Mandatory Workflow

For every feature:
1. Fill intake
2. Update requirements
3. Update plan
4. Update tasks
5. Run analysis
6. Implement
7. Test
8. Pass release gate

No skipping.

## Coding Standards

- Python 3.11+
- Clear module boundaries
- Type hints encouraged for public interfaces
- No hidden side effects in provider adapters
- No shell execution outside the controlled runner
- No raw secrets in logs, tests, or fixtures

## Product Rules

- Duckln is not a generic chatbot.
- Duckln must prioritize:
  1. diagnosis
  2. next action
  3. verification
  4. teaching
- The first release must feel fast, safe, and memorable.
- Every AI answer must be bounded and actionable.
- Every automation behavior must be mode-aware.

## Safety Rules

- Block S4 commands completely.
- S3 commands require explicit user approval in all modes.
- HOOTLWO auto-run must stay limited to S0–S1.
- No provider call may include unredacted secrets.
- Show the user what extra context is being sent in HOTL/HOOTLWO when context expands.

## Testing Rules

Minimum required before merge:
- unit tests for new deterministic logic
- integration test for any provider or command execution change
- one manual real-world scenario for user-facing behavior
- update analysis if assumptions changed

## Documentation Rules

Every feature PR must include:
- requirement references
- plan references
- task completion
- updated analysis if behavior changed
- user-facing release note if behavior is visible

## Production Rules

- Treat stderr and logs as potentially sensitive.
- Redact first, log second.
- Prefer deterministic heuristics before LLM calls.
- Never market unsupported autonomy.
- If behavior changes across providers, document it explicitly.

## Done Definition

A task is done only when:
- implementation is complete
- tests pass
- linked requirement is satisfied
- analysis is still clean
- task checkbox is marked `[x]`
