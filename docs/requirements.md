# Duckln Requirements Document

## Introduction

Duckln is an AI-powered terminal mentor that transforms the terminal into a guided execution environment.
The first production release focuses on **Python and AI environment debugging** on macOS and Ubuntu, with three user-selectable control modes and support for OpenRouter, OpenAI, and Anthropic providers.

## Product Objective

Enable users to fix broken Python/AI terminal workflows quickly without copying errors into Google or any search engine or direct LLM's, while also learning what happened and preserving privacy.

## Release Scope

Included in v1:
- command execution loop
- safe output/error capture
- provider onboarding for OpenRouter, OpenAI, Anthropic
- mode selection
- Python/AI environment error diagnosis
- exact next-step command suggestions
- optional approved execution
- fix verification
- privacy-first LLM payload handling

Excluded from v1:
- arbitrary full autonomy
- paper-to-system execution
- voice control
- deep codebase editing
- multi-user collaboration

## Requirements

### R1 — First Run Onboarding
**User Story**: As a new user, I want Duckln to guide setup so that I can start using it without manual config edits.

**Acceptance Criteria**
- WHEN Duckln runs without config THEN it MUST show the Duckln pixel banner and provider selection UI.
- WHEN a user selects OpenRouter, OpenAI, or Anthropic THEN Duckln MUST collect the required API key securely and test connectivity.
- WHEN provider auth succeeds THEN Duckln MUST fetch or validate the selected model before saving config.
- WHEN setup completes THEN Duckln MUST save local config and allow immediate use.
- WHEN setup fails THEN Duckln MUST show a precise recovery message and not save broken config.
- WHEN the user is in an active Duckln session THEN Duckln MUST support slash commands for updating runtime configuration without restarting:
  - `/mode` — change active mode
  - `/provider` — change LLM provider
  - `/model` — change selected model
  - `/config` — open configuration menu
- WHEN a slash command changes provider or model THEN Duckln MUST validate the new selection before saving it.
- WHEN a slash command fails THEN Duckln MUST show a clear error and keep the previous working configuration.
- WHEN the user types `/` in an active Duckln session THEN Duckln MUST display an interactive command menu showing available slash commands.
- WHEN the command menu is displayed THEN each command MUST include a short, beginner-friendly description.
- WHEN the user selects a command from the menu THEN Duckln MUST execute or open that command flow without requiring the full command to be typed manually.
- WHEN no command is selected THEN Duckln MUST return the user to the prompt without changing session state.
- WHEN the user is in an active Duckln session THEN Duckln MUST support slash commands for updating runtime configuration without restarting:
  - `/mode` — change active mode
  - `/provider` — change LLM provider
  - `/model` — change selected model
  - `/config` — open configuration menu
- WHEN the user types `/` in an active Duckln session THEN Duckln MUST display an interactive command palette showing available slash commands.
- WHEN the command palette is displayed THEN each command MUST include a short, beginner-friendly description.
- WHEN a slash command changes provider or model THEN Duckln MUST validate the new selection before saving it.
- WHEN a slash command fails THEN Duckln MUST show a clear error and keep the previous working configuration.
- WHEN no command is selected from the command palette THEN Duckln MUST return the user to the prompt without changing session state.

### R2 — Pixel Brand Presence
**User Story**: As a user, I want Duckln to feel memorable on first launch so that the tool feels differentiated and premium.

**Acceptance Criteria**
- WHEN Duckln starts THEN it MUST render a pixel-style Duckln text yellow colour banner.
- WHEN no image renderer is available THEN it MUST display an ASCII/pixel duck headshot fallback.
- WHEN displayed in a narrow terminal THEN the banner MUST degrade gracefully without breaking the prompt.
- WHEN branding is shown THEN launch time impact MUST remain negligible.

### R3 — Three Mode System
**User Story**: As a user, I want control over Duckln autonomy so that I can choose between learning, guided help, and limited automation.
- WHEN the user is selecting a mode during onboarding THEN Duckln MUST display the full form and a one-line explanation for each mode:
  - HITL — Human-in-the-Loop: AI explains and suggests only; user executes all commands manually.
  - HOTL — Human-on-the-Loop: AI suggests exact commands; user must approve before execution.
  - HOOTLWO — Human-out-of-the-Loop with Oversight: AI can auto-run safe, whitelisted fixes; user is warned to use sandbox/VM.
- WHEN modes are displayed THEN the explanations MUST be concise, clear, and beginner-friendly.

**Acceptance Criteria**
- WHEN the user selects HITL THEN Duckln MUST only explain and suggest commands.
- WHEN the user selects HOTL THEN Duckln MUST suggest exact commands and request approval before execution.
- WHEN the user selects HOOTLWO THEN Duckln MUST warn that only safe whitelisted commands are auto-run and recommend sandbox/VM use.
- WHEN mode changes THEN the terminal prompt MUST reflect the active mode clearly.
- WHEN a command exceeds the allowed autonomy for that mode THEN Duckln MUST block it and explain why and ask for user approval.

### R4 — Safe Command Execution
**User Story**: As a user, I want Duckln to execute commands safely so that the tool never surprises me with destructive behavior.

**Acceptance Criteria**
- WHEN a command is entered THEN Duckln MUST execute it through a controlled runner.
- WHEN a command matches blocked patterns THEN Duckln MUST refuse execution.
- WHEN a command hangs THEN Duckln MUST time out and explain what happened.
- WHEN stdout/stderr is captured THEN Duckln MUST separate them clearly in output.
- WHEN a command exits non-zero THEN Duckln MUST enter diagnosis flow.

### R5 — Minimal Privacy Payload
**User Story**: As a privacy-conscious user, I want Duckln to share only needed context so that my machine state is not overshared.

**Acceptance Criteria**
- WHEN Duckln sends context to an LLM THEN it MUST include only the current command plus minimal relevant diagnostics.
- WHEN in HITL THEN file paths, directory listings, and system state MUST be excluded unless strictly required and approved.
- WHEN in HOTL or HOOTLWO THEN extra context MAY be included only if relevant to diagnosis and visible to the user.
- WHEN secrets or tokens appear in command output THEN Duckln MUST redact them before sending.
- WHEN the user requests inspection of full context THEN Duckln MUST show exactly what will be sent.

### R6 — Python/AI Error Classification
**User Story**: As a user hitting a broken AI environment, I want Duckln to recognize common failure types so that I get specific fixes instead of generic advice.

**Acceptance Criteria**
- WHEN stderr includes `ModuleNotFoundError` THEN Duckln MUST classify it as missing package or environment mismatch.
- WHEN stderr indicates missing file or path THEN Duckln MUST classify it as path/file issue.
- WHEN stderr indicates permission denied THEN Duckln MUST classify it as permission issue.
- WHEN stderr indicates CUDA or torch mismatch THEN Duckln MUST classify it as GPU/environment issue.
- WHEN classification confidence is low THEN Duckln MUST state uncertainty and gather the smallest next diagnostic step.
- WHEN the user runs `/healthcheck` THEN Duckln MUST validate the current environment (Python, pip, venv, key packages, provider connection).
- WHEN issues are found THEN Duckln MUST list problems clearly with suggested fixes.
- WHEN everything is correct THEN Duckln MUST confirm system is healthy.
- WHEN validation requires external checks (e.g., provider API) THEN Duckln MUST handle failures gracefully and report connection status.

### R7 — Exact Next-Step Suggestions
**User Story**: As a user, I want exact commands to try next so that I can move to resolution immediately.

**Acceptance Criteria**
- WHEN diagnosis completes THEN Duckln MUST return 1–3 exact next commands maximum.
- WHEN commands are shown THEN each MUST include a short purpose label.
- WHEN multiple plausible causes exist THEN Duckln MUST order commands from safest/highest-signal to lowest-signal.
- WHEN no safe command exists THEN Duckln MUST explain the likely root cause without inventing a fix.
- WHEN a suggested command is destructive or high risk THEN Duckln MUST not auto-run it.

### R8 — Fix Verification
**User Story**: As a user, I want Duckln to verify whether a fix actually worked so that I do not waste time on “almost right” AI help.

**Acceptance Criteria**
- WHEN a suggested fix runs THEN Duckln MUST perform a verification step.
- WHEN the original command can be re-run safely THEN Duckln MUST offer rerun verification.
- WHEN targeted verification is possible THEN Duckln MUST run a narrower check first.
- WHEN verification fails THEN Duckln MUST explain the new state and suggest the next best step.
- WHEN verification succeeds THEN Duckln MUST tell the user what changed and why it is fixed.

### R9 — Teaching Layer
**User Story**: As a learner, I want Duckln to teach briefly while fixing so that I improve without slowing down.

**Acceptance Criteria**
- WHEN Duckln explains an issue THEN it MUST use concise human language.
- WHEN the user is in HITL THEN explanations MAY be slightly richer than HOTL/HOOTLWO.
- WHEN a fix involves a concept like virtual environments or CUDA THEN Duckln MUST explain that concept in 1–3 lines max.
- WHEN the user only wants commands THEN Duckln MUST avoid long teaching text.
- WHEN the explanation risks overwhelming the user THEN Duckln MUST prioritize action first.

### R10 — Production Logging and Traceability
**User Story**: As a production team, we want stable observability so that issues can be debugged without exposing sensitive content.

**Acceptance Criteria**
- WHEN Duckln logs an event THEN it MUST log metadata rather than full sensitive payloads.
- WHEN a provider call fails THEN Duckln MUST log provider, model, status code, and redaction-safe error metadata.
- WHEN a command is blocked THEN Duckln MUST log the safety reason.
- WHEN analytics are enabled THEN logs MUST exclude raw secrets, file contents, and unredacted stderr.
- WHEN production incidents happen THEN traceability from requirement to release note MUST be possible.

## R11 — Agentic Repo Bring-up and Environment Orchestration

- - WHEN displaying repositories THEN Duckln MUST load a locally cached repos.json file bundled at install time, containing GitHub metadata (name, description, stars, category tag,language/framework tag,last updated).
- WHEN user runs `/repos refresh` THEN Duckln MUST fetch fresh repository data from GitHub API, deduplicate, sort by stars, and overwrite the local cache.
- Duckln MUST NOT auto-refresh repository data without explicit user action.
- WHEN repositories are displayed THEN Duckln MUST sort them by star count and present them in an arrow-key dropdown with one-line descriptions.
- WHEN a repository is selected THEN Duckln MUST clone the repository, inspect setup files (README, requirements.txt, pyproject.toml), and infer setup steps.
- WHEN API credentials are not available THEN Duckln MUST continue repo setup and clearly indicate which steps require authentication.
- WHEN setup is inferred THEN Duckln MUST propose or execute setup depending on mode (HITL/HOTL/HOOTLWO).
- WHEN VM setup completes THEN Duckln MUST display essential connection commands for the user.
- WHEN setup completes successfully (local or VM) THEN Duckln MUST display a clear success message: "Your environment is ready! 🦆"
- WHEN execution environment is selected THEN Duckln MUST support:
  - Local machine execution
  - Ubuntu VM via Multipass
- WHEN VM is selected THEN Duckln MUST:
  - create VM
  - install Duckln inside VM
  - WHEN running on Apple Silicon THEN Duckln MUST:
  - detect lack of CUDA
  - recommend CPU/MPS
  - avoid suggesting CUDA setup
- WHEN storing memory THEN Duckln MUST:
  - store only high-signal summaries
  - avoid raw logs
  - keep filesystem memory small and structured
- WHEN user runs `/memory clear` THEN Duckln MUST:
  - offer options: session / project / full reset
  - show one-line summary of deletion
  - require confirmation before execution
  - WHEN the default VM name already exists THEN Duckln MUST generate a unique name by appending an incrementing suffix (e.g., duckln-vm-1, duckln-vm-2).
- WHEN a VM is created THEN Duckln MUST ask the user: "Duckln can work inside the VM to manage dependencies and fix errors there. Do you want to install Duckln in your VM?"
- WHEN the user agrees THEN Duckln MUST install a fresh Duckln runtime inside the VM and initialize fresh Duckln folders there.
- WHEN the user declines THEN Duckln MUST leave the VM ready for manual use and display essential commands to connect to and use the VM.
- Duckln MUST NOT automatically transfer local provider credentials, API keys, or local Duckln config into the VM.
- WHEN Duckln is installed inside the VM THEN Duckln MUST ask the user to configure provider, model, and API key manually inside the VM using Duckln config commands.
- WHEN API credentials are not configured inside the VM THEN Duckln MUST continue non-auth setup steps and pause only API-dependent steps with a clear message.
- Duckln MUST provide a `/vm` command to allow users to create and manage a VM at any time during an active session.
- WHEN the user runs `/vm` THEN Duckln MUST trigger the same VM setup flow as onboarding (CPU, memory, name, creation, optional Duckln install).
- The `/vm` command MUST display a short one-line description of what it does in the command palette.
- Duckln MUST exclude training-focused repositories from the bundled repo catalog at launch.
- Duckln MUST prefer repositories focused on inference, serving, demos, APIs, deployment, or end-user tooling.
- The repo catalog generation flow MUST filter out repositories whose description or topics indicate training-heavy workflows, including keywords such as `training`, `fine-tuning`, `pretraining`, and `GRPO`.
- The repo catalog generation flow SHOULD prefer repositories whose description or topics indicate inference or deployment workflows, including keywords such as `inference`, `serving`, `demo`, `api`, and `deployment`.
- The bundled launch catalog MUST prioritize runnable local projects over frameworks, course material, documentation, resource lists, or textbooks.
- The bundled launch catalog MUST exclude repositories whose primary language is HTML, JavaScript, TypeScript, Java, C, or Unknown unless explicitly allowlisted.
- The bundled launch catalog MUST exclude repositories whose name, description, or topics contain non-runnable learning/resource signals such as `course`, `tutorial`, `awesome`, `guide`, `papers`, `resources`, `beginners`, `textbook`, or `from scratch`.
- The bundled launch catalog MUST exclude foundational frameworks and libraries that are not practical end-user repo targets for Duckln setup at launch.
- The bundled launch catalog MAY preserve a small explicit allowlist of practical runnable repos even if they would otherwise be filtered out.
- Duckln MUST support a curated launch catalog source of truth separate from raw GitHub topic discovery.
- Duckln MUST allow explicit launch allowlist, blocklist, and metadata overrides for bundled repo catalog generation.
- The bundled launch catalog MUST prioritize curated high-value repos over raw topic-fetch results when determining the final default catalog.
- Duckln MUST support metadata overrides for category, framework, and launch warnings when GitHub-derived metadata is insufficient or misleading.

## R12 — Agent Behavior and Memory Policy

- Duckln MUST follow the behavior defined in the root `AGENTS.md` file as the primary agent contract.
- Duckln MUST use a filesystem-facing memory structure for agent-readable state, including `AGENTS.md`, `skills/`, `knowledge/`, and `sessions/`.
- Duckln MUST use SQLite as an internal system-state layer for configuration, run history, repo state, VM linkage, and healthcheck state without requiring user database setup.
- Duckln MUST keep memory small and high-signal, storing summaries and reusable knowledge rather than raw logs or transcripts.
- Duckln MUST provide user-controlled memory clearing through `/memory clear` with explicit confirmation and selectable scope.
- WHEN the user runs `/memory clear` THEN Duckln MUST include a Cancel / Back option in the selection menu.
- WHEN the user selects Cancel / Back in `/memory clear` THEN Duckln MUST return to the terminal prompt without changing any state.
- Duckln MUST never silently truncate or auto-delete agent memory.
- Duckln MUST present slash commands with a short readable one-line description.
- Duckln MUST behave correctly across Apple Silicon, non-Apple systems, and NVIDIA/CUDA-capable systems by adapting recommendations to actual detected hardware.
- WHEN Duckln is performing a long-running step THEN it MAY show short friendly progress messages, but they MUST stay concise, readable, and not spam the terminal.
Duckln MAY show short friendly progress messages, but they MUST stay concise and non-intrusive.
Example good message: Duckln is working hard for you ❤️.
- Duckln MUST detect local system hardware including Apple Silicon (ARM/MPS), CPU-only systems, and NVIDIA CUDA-capable systems on Linux/Windows, and adapt setup guidance accordingly.
- Duckln MUST NOT show a fixed “no CUDA in VM” warning for all systems.
- WHEN the host is Apple Silicon THEN Duckln MUST clearly state that CUDA is not supported and recommend CPU or MPS instead.
- WHEN the host is a non-Apple system THEN Duckln MUST detect whether NVIDIA/CUDA is available on the host and whether the VM has GPU access before making CUDA recommendations.
- Duckln MUST only suggest CUDA installation when hardware support and VM access make it a realistic path.
- Duckln MUST NOT suggest CUDA setup on unsupported systems (e.g., Apple Silicon).
- Duckln MUST verify CUDA availability before recommending GPU-dependent steps.
- Duckln MUST treat SQLite as the authoritative backing store for agent memory metadata and managed memory content.
- Duckln MUST expose agent memory to the runtime agent in a filesystem-shaped structure, materialized from SQLite as needed.
- Duckln MUST support synchronization between SQLite-backed memory records and the agent-facing filesystem view without storing raw logs or transcripts.
- Duckln MUST preserve small, high-signal memory files and avoid uncontrolled memory expansion.

### R13 — Command Discovery and Safe Interaction
- [ ] Implement `/help` command to display available slash commands with one-line descriptions (Plan: 19; Req: R11)
- [ ] Add post-onboarding hint directing users to `/help` for command discovery (Plan: 19; Req: R11)
- [ ] Add cancel/return option to `/repos` selector and preserve session state on cancel (Plan: 17; Req: R11)