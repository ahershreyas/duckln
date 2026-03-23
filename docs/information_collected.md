# Information Collected in Duckln Specification Driven Coding

This document defines **exactly what information must be collected** before coding any Duckln feature.

## 1. Product Information

For every feature, capture:

- Feature name
- Problem statement
- Why this matters now
- Target user segment
- Primary use case
- Success metric
- Non-goal
- Release target
- Owner
- Reviewer

## 2. User Context Information

Collect:

- User type
  - beginner AI learner
  - ML engineer
  - Python developer
  - internal developer
- OS
  - macOS
  - Ubuntu
- Shell
  - zsh
  - bash
- Environment type
  - local machine
  - VM
  - container
- Provider choice
  - OpenRouter
  - OpenAI
  - Anthropic
- Mode used
  - HITL
  - HOTL
  - HOOTLWO

## 3. Functional Specification Information

For each requirement, define:

- user story
- trigger
- expected behavior
- error behavior
- edge cases
- validation rule
- data collected
- data not collected
- performance expectation
- observability requirement

## 4. Terminal Execution Information

For any command-related feature, define:

- command source
  - user typed
  - AI suggested
  - AI auto-executed
- shell compatibility
- blocking rules
- timeout rules
- stdout capture rules
- stderr capture rules
- truncation rules
- retry rules
- rollback or recovery behavior

## 5. Privacy and Data Handling Information

For each flow, specify:

- exact payload sent to LLM
- redacted fields
- fields never sent
- consent surface
- local-only processing steps
- logging behavior
- retention duration
- analytics emitted
- secrets handling policy

### Minimum privacy promise for Duckln v1

Duckln must not send:
- full file contents by default
- full directory listings by default
- API keys
- shell history unrelated to the current task
- hidden environment variables unless explicitly approved

Duckln may send, depending on mode:
- current command
- short stderr snippet
- minimal environment diagnostics
- selected path or file name only when necessary

## 6. AI Behavior Information

Collect:

- provider
- selected model
- mode
- tone
- maximum response length
- command suggestion style
- verification style
- refusal behavior
- hallucination guardrails
- what the model is allowed to infer
- what the model is not allowed to infer

## 7. Safety Information

Every feature must state:

- destructive risk
- command safety class
- approval requirement
- sandbox recommendation
- blocked commands
- allowed auto-run commands
- escalation policy
- failure fallback

### Suggested safety classes

- S0: read-only local diagnostic
- S1: install / non-destructive environment fix
- S2: file mutation
- S3: system mutation
- S4: destructive / irreversible

Duckln v1 HOOTLWO must allow only S0–S1 auto-execution.

## 8. Quality Information

Collect:

- unit test cases
- integration test cases
- manual scenario tests
- acceptance checklist
- known limitations
- release risks
- support notes

## 9. Launch Information

For each release, define:

- release name
- user-facing summary
- top 3 demo scenarios
- known unsupported scenarios
- feedback channel
- rollback plan

## 10. Required SDC Traceability

Every feature must support this chain:

- Intake item
- Requirement
- Plan item
- Task
- Test
- Release note

No orphan item is allowed.
