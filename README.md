# Duckln — Specification Driven Coding Framework

![Duckln](./docs/assets/duckln-banner.txt)

Duckln is an AI-powered terminal mentor focused on **diagnosing, fixing, verifying, and teaching** inside the terminal loop.
This repo contains a production-ready **Specification Driven Coding (SDC)** framework tailored for Duckln so a developer can implement features with clear requirements, plan coverage, tasks, validation, and release discipline.

## First-release product intent

Duckln v1 should feel exciting because it does more than explain an error:
1. Runs a terminal command safely.
2. Detects what failed.
3. Gathers only minimal relevant context.
4. Produces exact next steps.
5. Optionally runs approved fixes.
6. Verifies whether the fix worked.
7. Teaches the user what happened.

## v1 scope

- Platforms: macOS + Ubuntu
- Shells: zsh + bash
- LLM providers:
  - OpenRouter
  - OpenAI / ChatGPT API
  - Anthropic / Claude API
- Modes:
  - HITL — explain and suggest only
  - HOTL — suggest exact commands, user approves execution
  - HOOTLWO — auto-run only whitelisted safe commands with explicit warning

## Folder structure

```text
duckln_sdc_framework/
├── docs/
│   ├── assets/
│   │   └── duckln-banner.txt
│   ├── requirements.md
│   ├── plan.md
│   ├── tasks.md
│   ├── analysis.md
│   └── information_collected.md
├── .sdc/
│   ├── guidelines.md
│   ├── feature_template.md
│   ├── intake_checklist.md
│   └── release_gate.md
├── src/
├── tests/
└── README.md
```

## SDC workflow

For every feature branch:

1. Fill `.sdc/feature_template.md`
2. Update `docs/requirements.md`
3. Update `docs/plan.md`
4. Update `docs/tasks.md`
5. Run `docs/analysis.md`
6. Implement in `src/`
7. Add or update tests in `tests/`
8. Pass `.sdc/release_gate.md`
9. Merge

## Non-negotiables

- No feature implementation without requirements, plan, tasks, and analysis.
- No vague prompts like “build X.”
- Every task must link back to at least one requirement.
- Every requirement must map to at least one plan item and at least one task.
- Every release must document:
  - user-visible behavior
  - privacy behavior
  - failure behavior
  - rollback path

## Suggested developer handoff

Give the developer this exact instruction:

> Implement Duckln by following the SDC artifacts in this repository. Do not skip requirements, plan, task linkage, or analysis. Preserve privacy-first telemetry, safe command execution, and mode-specific behavior. Any ambiguity must be resolved by updating the spec before coding.
