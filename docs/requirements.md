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
- Duckln MUST support repo-family classification before setup execution.
- Duckln MUST use a supervisor agent to inspect the repo, classify the repo family, select the correct specialist agent, and verify the result.
- Duckln MUST support specialist setup agents for at least Python, C++/native runtime, Node/TypeScript, audio pipelines, diffusion-heavy repos, VM/environment setup, and provider/model routing.
- Specialist agents MUST operate with bounded domain-specific playbooks and must not execute outside their domain without supervisor escalation.
- Duckln MUST support detailed agent playbooks and guardrails so setup behavior is explicit, reviewable, and consistent.
- Duckln MUST support Ollama as a local provider option for users who want local model execution without cloud API keys.
- Duckln MUST distinguish between Python-primary repos, C++/native runtime repos, Node/TypeScript repos, multi-service repos, audio repos, and diffusion-heavy repos before choosing the bring-up path.
- Duckln MUST preserve launch-catalog curation and allow manually curated launch repos such as Coqui TTS to remain in the bundled catalog even if they are not surfaced by topic ranking alone.
- Duckln MUST treat Ollama as a first-class local provider with a provider-specific terminal flow separate from cloud API-key providers.
- WHEN the user selects Ollama THEN Duckln MUST first detect whether Ollama is reachable at `http://localhost:11434`.
- WHEN Ollama is reachable THEN Duckln MUST fetch and display locally available models and allow the user to select one or pull a new model.
- WHEN Ollama is not reachable THEN Duckln MUST show clear install/start instructions and allow the user to retry detection or choose a different provider.
- Duckln MUST NOT ask for or store an API key for Ollama.
- Duckln MUST store Ollama configuration with `api_key: null`.
- Duckln MUST support pulling recommended Ollama models based on detected system RAM.
- Duckln MUST support the same Ollama model selection/pull flow from `/model` after setup.
- WHEN Ollama is the configured provider THEN Duckln MUST check runtime availability at session start and guide the user to run `ollama serve` if Ollama is not running.
- All provider/model menus MUST include a Cancel / Exit / Back option and preserve current state when cancelled.
- Duckln MUST treat Ollama as a first-class local provider with a provider-specific terminal UX separate from cloud API-key providers.
- WHEN the user selects Ollama THEN Duckln MUST first detect whether Ollama is reachable at the configured base URL, defaulting to `http://localhost:11434/api/tags`.
- WHEN Ollama is reachable THEN Duckln MUST show a clear success message, fetch locally available models, and allow the user to select an existing model or pull a new one.
- WHEN Ollama is installed but not running THEN Duckln SHOULD offer to start Ollama automatically and continue once the runtime is reachable.
- WHEN Ollama is not installed THEN Duckln MUST show OS-specific install guidance and MAY offer automatic installation according to the active mode and safety rules.
- Duckln MUST NOT ask for or store an API key for Ollama.
- Duckln MUST store Ollama configuration with `api_key: null` and a persisted local or custom `base_url`.
- WHEN the default Ollama URL is not reachable THEN Duckln SHOULD allow the user to enter a custom Ollama URL and retry detection there.
- WHEN Ollama detection fails repeatedly THEN Duckln MUST offer a clean fallback to choose a different provider instead of dead-ending the user.
- Duckln MUST support reusing the Ollama model selection and model-pull flow from `/model` when Ollama is the current provider.
- WHEN Ollama is the configured provider at session start THEN Duckln MUST check runtime availability and guide the user clearly if Ollama is not running.
- All provider and model flows MUST include Cancel / Exit / Back options and preserve current state when cancelled.
- Duckln MUST support a dedicated Debug / Recovery specialist agent for failed setup attempts.
- WHEN a specialist setup path fails verification THEN the supervisor agent MUST escalate the failure to the Debug / Recovery agent before declaring failure to the user, unless the case is clearly unsupported or blocked by safety rules.
- The Debug / Recovery agent MUST classify failure types such as dependency-install failure, command-not-found, missing compiler/tooling, pip or venv mismatch, Node/npm mismatch, service-not-running, port conflict, model/runtime missing, GPU/CUDA mismatch, unsupported platform, and mixed-stack setup conflicts.
- The Debug / Recovery agent MUST return a bounded recovery decision: retry with revised plan, reroute to a different specialist, request a missing prerequisite from the user, or mark the case unsupported.
- Duckln MUST NOT bluff or loop indefinitely after setup failure; retries and reroutes MUST be bounded and explicit.
- The supervisor agent MUST use confidence-aware routing and MUST prefer escalation to the Debug / Recovery agent when the first specialist path fails or when repo classification confidence is low.
- Duckln MUST support explicit debug/recovery playbooks and guardrails so troubleshooting remains predictable, reviewable, and safe.
- Memory written from failed setup attempts MUST be concise, generalized, and high-signal; Duckln MUST NOT store raw logs or case-by-case clutter as memory.
- [ ] Unify persisted config, preferences, and in-session runtime state under a single authoritative contract (Plan: 43; Req: R12)
- Fix `/memory clear` factory reset so it resets SQLite state, filesystem materialization, config/preferences state, and current in-session state consistently (Plan: 43; Req: R12)
- Remove API-key echo from terminal flows and preserve last good state on cancelled/interrupted secret entry (Plan: 44; Req: R12)
- Add safe KeyboardInterrupt handling to secret-input flows (Plan: 44; Req: R12)
- Implement redaction-safe provider/runtime logging so raw secrets never reach terminal, logs, memory, or SQLite (Plan: 44; Req: R12)
- Pass execution-target context into `/repos` bring-up and persist selected execution target for later setup decisions (Plan: 45; Req: R11,R12)
- Implement bounded Debug / Recovery execution loop after failed specialist verification (Plan: 46; Req: R11,R12)
- Support exactly one bounded Debug / Recovery decision: retry, reroute, request prerequisite, or mark unsupported (Plan: 46; Req: R11,R12)
- Write concise recovery learnings through the SQLite-backed memory API as session/knowledge notes (Plan: 46; Req: R11,R12)
- Enforce playbook-backed behavior in runtime code or remove unsupported playbook-backed claims from implementation/docs (Plan: 46; Req: R11,R12)
- Duckln MUST use the curated seeded catalog as the default `/repos` experience.
- Duckln MUST NOT show broad unfiltered discovery results in `/repos` by default.
- `/repos refresh` MUST refresh the curated seeded catalog using GitHub enrichment and overrides while preserving the curated repo set.

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
- WHEN the user selects a repo and a new VM is created before bring-up, Duckln MUST NOT raise an unhandled exception due to an uninitialized `choice_prompt` variable; the bring-up flow MUST proceed without crashing regardless of whether the choice prompt was shown.
- WHEN `execution_target` is `"vm"` and a `vm_name` is known, `ControlledCommandRunner` MUST wrap every executed command as `multipass exec <vm_name> -- bash -lc "<command>"` so repo setup commands run inside the VM rather than on the host machine.
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
- On first run, Duckln MUST show a lightweight safety and permissions screen and require explicit user acceptance before continuing.
- On first run, Duckln MUST ask what to call the user and persist that preference for future sessions.
- Duckln MUST store user preferences including name, safety acceptance, onboarding completion, and preferred mode.
- At session start, Duckln MUST display a compact header showing active provider, model, mode, user name, and memory state, with short hints for `/provider`, `/model`, and `/mode`.
- Duckln MUST standardize `cancel` vs `exit` behavior across all interactive flows. `cancel` returns to the previous menu or prompt without changing state; `exit` or `/exit` ends the session.
- Duckln MUST provide brief work-status updates for non-trivial actions without exposing full internal reasoning.
- Duckln MUST provide a bounded uninstall flow that supports removing: (1) application only, (2) application plus memory/session data, or (3) everything including config and stored credentials.
- Duckln uninstall behavior MUST be OS-aware and must use the correct uninstall path for the detected installation method and operating system.
- Duckln SHOULD keep versioned product UX specifications for terminal UI, onboarding, and uninstall behavior in repository spec files.
- Duckln MUST render the startup session header responsively so it remains readable when terminal width changes.
- Duckln MUST avoid hardcoded alignment that breaks when the terminal is narrowed.
- Duckln MUST color only actual slash commands as command-colored text; surrounding explanatory text MUST remain neutral system text.
- Duckln SHOULD provide a conversational terminal interaction style rather than only command-only fallback responses.
- Duckln MUST present the prompt and transcript in a consistent chat-shell layout that remains compact and readable.
Duckln MUST render the startup session header responsively and stack content cleanly on narrow terminals.
Duckln MUST show the Duckln version in the startup session header.
Duckln MUST present transcript and input in a compact chat-shell layout with messages flowing upward above the prompt area.
Duckln SHOULD render brief work-status / thinking updates in a compact status box without exposing full internal reasoning.
Duckln SHOULD show a compact footer hint line near the input area for help and send/exit behavior.
Duckln MAY render the duckln> prompt in brand gold while preserving user-entered text in the user-input color.
- Duckln MUST keep a cached session system prompt and rebuild it only when meaningful session context changes, such as user name, mode, active repo, active skill, or execution target.
- Duckln MUST maintain dynamic per-session conversation history and pass both the cached system prompt and current conversation history on every provider call.
- Duckln MUST compact long session history by summarizing older exchanges into a concise session summary instead of allowing unbounded growth.
- Duckln SHOULD compact history using bounded summary length and preserve current repo state, attempted fixes, failures, successes, and current setup status.
- Duckln MUST keep the last 40 exchanges in active session history.
- WHEN history exceeds 40 exchanges THEN Duckln MUST summarise the oldest 20 into a single compact summary message and replace them.
- The summary MUST be concise (≤150 words) and capture repo, attempts, failures, successes, and current state.
- Duckln SHOULD respond naturally and contextually to non-slash user input instead of repeating a fixed fallback response.
- Duckln MUST acknowledge user intent before redirecting to commands or setup actions.
- Duckln SHOULD maintain a lightweight conversational layer for greetings, clarifications, and general help within the terminal session.


### R13 — Command Discovery and Safe Interaction
- Implement `/help` command to display available slash commands with one-line descriptions (Plan: 19; Req: R11)
- Add post-onboarding hint directing users to `/help` for command discovery (Plan: 19; Req: R11)
- Add cancel/return option to `/repos` selector and preserve session state on cancel (Plan: 17; Req: R11)

### R14 — Cloud Setup Agent Loop [REQ-CLOUD-LOOP-1, REQ-CLOUD-LOOP-2]

**User story**: As a user setting up cloud provisioning via `/cloud`, when a provider step fails (API disabled, expired SSO, missing region/zone, etc.), I want Duckln to classify the failure, propose a concrete remediation, and let me choose Fix now / Fix later / Ignore — rather than silently exit and leave me to debug.

**Acceptance Criteria**
- WHEN `/cloud` runs any provider step (auth check, project config, region/zone config) and the step fails THEN Duckln MUST classify the failure into a stable issue kind (e.g. `gcp_api_disabled:<api>`, `aws_sso_token_expired`).
- WHEN a classified issue has an automatic remediation THEN Duckln MUST surface a Fix now / Fix later / Ignore picker before taking any further action.
- WHEN the user picks Fix now AND the remediation succeeds THEN Duckln MUST retry the failed step automatically (bounded by a per-step attempt ceiling).
- WHEN the user picks Fix later THEN Duckln MUST persist the pending remediation (provider, kind, label, project) so it is surfaced the next time `/cloud` opens.
- WHEN the user picks Ignore THEN Duckln MUST exit the loop cleanly without persisting follow-up state.
- WHEN the user selects a GCP zone or AWS region THEN Duckln MUST present a live picker populated from the provider's CLI; Duckln MUST NOT fall through to a hardcoded default list (REQ-CLOUD-LOOP-2).
- The agent-loop driver MUST be a reusable module (not bound to /cloud) so future provider or non-cloud flows can adopt the same observe-decide-act pattern (REQ-CLOUD-LOOP-1).
- WHEN GCP auth completes and a project is already configured THEN Duckln MUST prompt "Continue with project X?" so the user can keep or switch projects; Duckln MUST NOT silently use the pre-existing `gcloud config get-value project` (REQ-CLOUD-LOOP-3).
- WHEN GCP setup completes THEN Duckln MUST audit project API keys via `gcloud services api-keys list` and surface a Fix now / Fix later / Ignore picker for any unrestricted key — especially keys whose api targets include `generativelanguage.googleapis.com` without browser/IP/app restrictions. Fix now MUST open `https://console.cloud.google.com/apis/credentials?project=<id>`; Duckln MUST NOT auto-restrict the key (REQ-CLOUD-LOOP-4).
- WHEN any cloud auth completes THEN the right-pane header MUST switch from "Local" to "Google Cloud" (GCP) or "AWS Cloud" (AWS) and the active execution target MUST be persisted so the label survives across restarts (REQ-CLOUD-LOOP-5).
- WHEN the user selects a VM shape during `/cloud → Create … VM` THEN Duckln MUST present every shape returned by the provider for the chosen zone/region (no hardcoded allowlist). Shapes MUST be grouped by category — CPU General Purpose / Compute Optimized / Memory Optimized, and per-GPU buckets (L4, T4, V100, A100, H100, etc.). A "Show all shapes" option MUST also be offered (REQ-CLOUD-LOOP-6).
- WHEN Duckln starts THEN it MUST seed the status-bar `_connection_hint` from the persisted `active_runtime_execution_target` (Local / Ubuntu VM / Google Cloud / AWS Cloud) so the bar reflects the saved cloud target on cold start, and MUST re-assert the bar on every `/cloud` invocation (REQ-CLOUD-LOOP-7).

### R15 — Cloud picker cancellation [REQ-UX-CANCEL-1]
- WHEN the user is in any cloud setup or creation picker THEN pressing **Q** or **Escape** MUST cancel the picker the same way as picking "Cancel".
- Every cloud-flow picker prompt MUST include the affordance text `(Press Q or Esc to cancel)` so the user knows the keys are available.

### R16 — `/explore` GitHub trending browser [REQ-EXPLORE-1..4]
- REQ-EXPLORE-1: `/explore` (and `duckln explore`) MUST scrape `https://github.com/trending` directly via httpx + BeautifulSoup. No third-party trending library may be added.
- REQ-EXPLORE-2: The browser MUST present up to 25 repos for the active period (Today/Week/Month), accept a language filter, and let the user switch period or refresh inline; cached results MUST be served within 15 minutes with a `[cached Xm ago]` indicator and the user MUST be able to refresh on demand.
- REQ-EXPLORE-3: AI/ML languages (Python, Jupyter Notebook, CUDA, Rust, C++) MUST be tagged `[AI]` so users can spot stress-test candidates. Repos with no description MUST sink to the bottom of the list.
- REQ-EXPLORE-4: Pressing Enter on a repo MUST immediately hand its URL to `resolve_public_github_repo_record` + `bring_up_selected_repo` with no extra confirmation. Network or parse failures MUST surface specific user-facing messages and exit gracefully without crashing.

### R17 — Repo bring-up resilience and Rust specialist [REQ-BRINGUP-LOOP-1, REQ-BRINGUP-LOOP-2, REQ-BRINGUP-README-1, REQ-RUST-1]

**User story**: As a user setting up any cloned repo (Python, Rust, Node/TypeScript, C++, Go), I want Duckln to actually read the README, ask the LLM to extract install/run commands, run them under my approval, and — when setup fails — search the web with a clean error query, not with Duckln's own log narrative.

**Acceptance Criteria**
- REQ-BRINGUP-LOOP-1: The Debug/Recovery classifier MUST NOT consume Duckln's own narrative summary (Specialist route: …, Toolchain: …, Fatal line: …) as the failure signal. When a command produces no stderr/stdout, the classifier MUST receive empty error output instead of the formatted summary. Defense-in-depth: the classifier MUST scrub any synthetic narrative lines before substring matching.
- REQ-BRINGUP-LOOP-2: Web search queries built from failure output MUST strip shell prompt lines (e.g. `ubuntu@duckln-vm:~/path$ …`) and Duckln's own narrative lines before constructing the query. The query MUST surface the real error, not the log noise.
- REQ-BRINGUP-README-1: When a cloned repo has a README, the bring-up planner MUST consult the LLM README classifier (not only as a heuristic-failure fallback) and MUST send the full README content (chunked at ~16k chars) so commands documented anywhere in the file are reachable. Multi-segment install commands joined by `&&`, `;`, or `||` MUST be allowed when every segment passes the per-segment safety check; piping into shell interpreters (`| sh`, `| bash`) and destructive patterns MUST remain blocked.
- REQ-RUST-1: Cargo-based repos (detected via `Cargo.toml`, `rust-toolchain.toml`, or `rust-toolchain`) MUST route to a dedicated Rust specialist that plans `cargo --version` → `cargo fetch` → `cargo build --release` and surfaces a verified run command via `cargo run --release` or the `target/release/<bin>` path. The Rust specialist MUST appear in the subagent runtime descriptors and ship with `src/agent/playbooks/rust.md`.
- Web search MUST retry transient connection errors once with backoff and MUST fall back to Bing when DuckDuckGo returns a connection failure (not just empty results). The user-facing error message MUST report the specific reason (DNS, timeout, HTTP code) rather than the generic "could not reach DuckDuckGo".

### R18 — Self-skill-acquisition after successful repair [REQ-SKILL-SELF-1, REQ-SKILL-SELF-2, REQ-SKILL-SELF-3, REQ-SKILL-SELF-4]

**User story**: As a user in any control mode, when Duckln successfully recovers from a repo setup failure (≥1 retry was needed), I want Duckln to generate a reusable skill note capturing what went wrong and what fixed it — asking for my approval in HITL/HOTL, or auto-saving in HOOTLWO — so the next time the same error pattern appears in the same repo family, Duckln already knows what to do.

**Acceptance Criteria**
- REQ-SKILL-SELF-1: After any repo repair succeeds (≥1 retry was needed before verification passed), Duckln MUST call the LLM to generate a structured skill note that captures: the error pattern, the failed command, the recovery command sequence, a verification command, and any caveats. The skill note MUST be stripped of shell-prompt lines and Duckln synthetic narrative before being sent to the LLM.
- REQ-SKILL-SELF-2: In HITL and HOTL modes, the generated skill note MUST be displayed to the user verbatim before any persistence. Duckln MUST prompt for explicit approval (yes/no); the skill MUST NOT be saved if the user declines. Declining MUST be the safe default.
- REQ-SKILL-SELF-3: In HOOTLWO mode, the generated skill note MUST be auto-persisted to `memory/skills/` via `write_skill_memory_state` without an approval prompt. Duckln MUST print a one-line confirmation ("Skill saved: {title}") so the user can see what was learned.
- REQ-SKILL-SELF-4: On subsequent bring-up of a repo belonging to the same family, Duckln MUST check `memory/skills/` for any skill whose slug matches the repo family. If found, the skill content MUST be passed to the specialist as a `prior_skill_hint` so it can prepend hint-derived steps before its default heuristic inference. If no matching skill exists, bring-up proceeds unchanged.

### R19 — Bring-up loop must not hang [REQ-BRINGUP-NOLOOP-1, REQ-BRINGUP-NOLOOP-2, REQ-BRINGUP-NOLOOP-3, REQ-BRINGUP-NOLOOP-4, REQ-BRINGUP-NOLOOP-5]

**User story**: As a user setting up any cloned repo, when a prerequisite (Node, Python, etc.) is missing on the target, I want Duckln to act on the OS's own install hint, never re-run the same failing command more than twice, enforce a hard wall-clock budget so the repair workflow can't spin silently for minutes, and surface the actual run command from `package.json` scripts without diving into web search.

**Acceptance Criteria**
- REQ-BRINGUP-NOLOOP-1: When stderr contains an OS-volunteered install hint (`can be installed with: sudo apt install X`, `brew install X`, `dnf install X`, `yum install X`), Duckln MUST parse it and prefer that command over hardcoded install scripts. The parsed package name MUST contain only alphanumerics, dots, dashes, plus, and spaces; any shell metacharacter MUST disqualify the hint. The resulting command MUST pass `assess_command` before being added to the plan.
- REQ-BRINGUP-NOLOOP-2: Duckln MUST NOT execute the same command more than twice when both attempts produce the same exit code and stderr fingerprint (first 200 chars after stripping synthetic and shell-prompt lines). On the second identical failure, Duckln MUST surface a single sentence naming the command and the first line of stderr, then stop the recovery loop.
- REQ-BRINGUP-NOLOOP-3: The runtime repair workflow MUST enforce a per-attempt budget of 90 seconds. The discovery sub-flow that runs sequential web searches for a run command MUST be capped at 30 seconds aggregate. Exceeding either MUST stop further web tool calls, persist the partial trace, and return control to the user with a message naming the elapsed seconds.
- REQ-BRINGUP-NOLOOP-4: When the workflow's persisted `active_runtime_execution_target` disagrees with the live session execution target, the live session target MUST win. Duckln MUST display a single notice that the stale target was overridden.
- REQ-BRINGUP-NOLOOP-5: When a repo has a `package.json` with a `scripts` table, Duckln MUST surface those scripts (`dev`, `start`, `serve`, `preview` in priority order) as run candidates before falling back to web-search-based run-command discovery.

### R20 — Runtime repair must not loop and must inform the user [REQ-RUNTIME-NOLOOP-1, REQ-RUNTIME-NOLOOP-2, REQ-RUNTIME-NOLOOP-3, REQ-RUNTIME-NOLOOP-4, REQ-RUNTIME-NOLOOP-5, REQ-RUNTIME-NOLOOP-6]

**User story**: As a user who has successfully set up any repo, when Duckln tries to launch it and a prerequisite is missing or a launch error occurs, I want Duckln's runtime repair to (a) act on the OS install hint instead of a hardcoded curl script, (b) never run the same install more than twice with identical failure output, (c) state the exact command it wants to run with a one-line reason from the most recent failure, (d) stream concrete status lines so I always know what is happening, and (e) infer the run command from local project files for ANY supported repo family before falling back to a web search — so that Duckln can get any repo running end-to-end without getting stuck.

**Acceptance Criteria**
- REQ-RUNTIME-NOLOOP-1: The runtime prerequisite install path MUST short-circuit on the second identical failure for the same `(repo_url, install_command)` pair when the new stderr fingerprint matches the previous one (computed via `fingerprint_stderr`). On the second identical failure, Duckln MUST display a one-line message naming the failing command and return a distinct stop status that callers handle as a final repair phase.
- REQ-RUNTIME-NOLOOP-2: When the runtime prerequisite plan is built from an incident whose stderr contains a parseable OS install hint (apt/dnf/yum/brew), Duckln MUST override the hardcoded install command with the parsed hint. The hinted command MUST pass `assess_command` before replacing the planned command.
- REQ-RUNTIME-NOLOOP-3: Narrative output produced by Duckln itself during runtime repair (dedup messages, budget messages, target-override notices, install hint notices, "Duckln running" / "Duckln installing prerequisite" / "Duckln wants to run" prefixes) MUST be listed in `DUCKLN_SYNTHETIC_LINE_MARKERS` so it is stripped before any captured stderr is used to seed a search query.
- REQ-RUNTIME-NOLOOP-4: The top-level approval prompt for a runtime repair MUST state the exact proposed command (or repair action key when no command exists) and a one-line reason derived from the compact incident summary — never just "Do you want Duckln to solve the {repo} issue?".
- REQ-RUNTIME-NOLOOP-5: During the runtime prerequisite install path, Duckln MUST emit at least one `Duckln installing prerequisite: {command}` status line before running the install so the user always sees forward motion.
- REQ-RUNTIME-NOLOOP-6: For any supported repo family (Node, Python including `pyproject.toml` PEP 621 / Poetry script entries, Rust, Go including `cmd/` sub-modules, C++ with `CMakeLists.txt` + `build/`), `_infer_start_command_from_files` MUST attempt to derive the run command from local project files before falling back to web search.
- REQ-RUNTIME-NOLOOP-7: When `_execute_plan_with_bounded_recovery` short-circuits via the duplicate-failure dedup guard, the returned `RepoBringUpResult` MUST have `should_offer_repair=False`. This prevents the outer orchestrator from calling the runtime repair workflow and the run loop for a repo whose prerequisite could not be installed.
- REQ-RUNTIME-NOLOOP-8: The total wall-clock time spent by `_drain_terminal_runtime_incidents` across all incidents in a single drain pass MUST NOT exceed `_DRAIN_TOTAL_BUDGET_SECONDS` (180 s). When the budget is reached, the loop MUST break and emit a one-line message informing the user that manual action is needed.

## R21 — End-to-end README-driven setup, run, and usage (Plan 57)

**Description**: For any repo with a reasonable README, Duckln must (a) extract declared prerequisites from the README and install them sequentially with verification BEFORE running install/setup commands, (b) surface the README-declared prerequisites in runtime repair so the user sees the full needed toolchain, (c) display a usage summary and offer to demo an example after successful run. The fix is repo-agnostic — it works for Node, Python, Rust, Go, C++, Tauri, Django, FastAPI, Vite, Next.js, etc.

**Acceptance Criteria**
- REQ-README-SETUP-1: When a repo's README declares system prerequisites in a Requirements / Prerequisites / Setup / Getting Started section, `RepoSetupSpecialist.build_plan` MUST prepend probe+install steps for each declared prereq to the plan, BEFORE the README-derived install/run commands. Each step MUST use the prereq's probe command as its verification command.
- REQ-README-SETUP-2: The install command for each prereq MUST be chosen from the prereq's known package-manager mappings, selected by the live `SystemProbe.operating_system` (Linux → apt, Darwin → brew, otherwise → fallback installer if available).
- REQ-README-RUNTIME-1: When `_build_runtime_prerequisite_plan` builds a plan for a missing dependency, it MUST read the repo's local README (if present) and append the names of OTHER declared prerequisites to the plan's `reason` field as `README also declares: ...`. This is informational only — it MUST NOT change which install command is executed.
- REQ-README-USAGE-1: After a successful run in `_orchestrate_repo_to_running`, Duckln MUST attempt to extract a `usage_summary` and `usage_example` from the README. When at least one is non-empty, the `usage_summary` MUST be displayed inline. When `usage_example` is non-empty AND an approve_prompt is available, Duckln MUST ask the user whether to surface the example; only if the user agrees does the example get displayed as a copyable command.
- REQ-README-USAGE-2: Usage display failures MUST never break the successful-run flow — any exception in the usage display path MUST be swallowed silently.

## R22 — Target-aware install paths + cross-session failure learning (Plan 58)

**Description**: Duckln must (a) pick install commands appropriate for the EXECUTION target's OS rather than the local host's, (b) extract prereqs from README code blocks (not just Requirements sections), (c) reject brew commands on Linux VMs at the safety layer, (d) persistently remember install failures across sessions so the planner stops proposing known-failed commands.

**Acceptance Criteria**
- REQ-PLAN58-1 (Bug A): When `execution_target` is `vm`/`aws`/`gcp`/`ssh`, the prereq preflight package manager MUST be apt regardless of the local host's OS. brew MUST NOT appear in any preflight install command for a remote target.
- REQ-PLAN58-2 (Bug B): The README extractor MUST scan fenced code blocks for shell command lines, strip wrapper prefixes (sudo, env-var assignments, time, watch, nice, nohup, stdbuf, env, xargs), and register the first real token as a prereq. Shell builtins (cd, ls, pwd, echo, exit, cat, mkdir, etc.) MUST be skipped. Tools known to `_KNOWN_PREREQUISITES` get full install metadata; unknown tools are registered probe-only with `command -v <tool>` as the probe.
- REQ-PLAN58-3 (Bug B): Code-block-extracted prereqs with no install path on the active target MUST be filtered out of the preflight plan to avoid false-positive failures. They remain in `ReadmeMetadata.prerequisites` for inspection.
- REQ-PLAN58-4 (Bug C): `assess_command` MUST reject `brew ...` commands (with or without `sudo` prefix) when `execution_target` is `vm`/`aws`/`gcp`/`ssh`, with a reason that names macOS and recommends apt.
- REQ-PLAN58-5 (Bug D): A persistent failure log MUST be maintained at `~/.duckln/memory/failures/<repo_slug>.md` recording (command, execution_target, exit_code, stderr_fingerprint, last_seen, retry_count). Duplicate `(command, execution_target)` writes MUST merge by incrementing `retry_count` and refreshing `last_seen`.
- REQ-PLAN58-6 (Bug D): Before adding an install step to the prereq preflight plan, the planner MUST consult `lookup_recent_failure(config_dir, repo_slug, command, execution_target)`. If a record exists within the past 24 hours, the planner MUST skip that step. The window is configurable via `window_hours` (default 24).
- REQ-PLAN58-7 (Bug D): Both the setup dedup short-circuit and the runtime install failure recorder MUST write to the persistent failure log so failures from either path are visible to future Duckln runs.

## R23 — Cross-repo failure learning + persistent first-failure + LLM-refined prereqs (Plan 59)

**Description**: Plan 58's failure memory had three gaps: per-repo only (no cross-repo learning), narrow recording (only dedup-fired failures), and heuristic-only README extraction. Plan 59 closes them.

**Acceptance Criteria**
- REQ-PLAN59-1: A second failure log MUST live at `~/.duckln/memory/failures/_global.md` storing the same record shape as per-repo logs. Lookups MUST check per-repo first, then fall back to global. Both honour the 24h freshness window.
- REQ-PLAN59-2: `write_failure_memory_state` MUST classify each failure via `_is_environmental_failure(stderr_fingerprint)`. Environmental failures (command not found, unable to locate package, unsupported architecture, sudo+permission denied, etc.) MUST dual-write to the global log. Non-environmental (e.g., `ModuleNotFoundError: my_pkg`) MUST stay per-repo only.
- REQ-PLAN59-3: `_execute_plan_with_bounded_recovery` MUST call `write_failure_memory_state` at TWO additional sites: (a) at the general failure handler before dedup state mutation (first-failure record), and (b) at the recovery-exhausted branch (after `recovery_count >= max_recovery_attempts`).
- REQ-PLAN59-4: `_build_readme_prereq_preflight_steps` MUST attempt an optional LLM refinement of the heuristic prereq list when an AppConfig with a model is available. The LLM MUST be constrained to a known allow-list (node, npm, python, rust, cargo, uv, go, docker, git, make, cmake, pnpm, yarn, poetry).
- REQ-PLAN59-5: The LLM refinement MUST fail open — any missing config, malformed JSON, low confidence (<0.5), or raised exception MUST cause `_llm_refine_readme_prereqs` to return None so the heuristic prereqs are used unchanged.
- REQ-PLAN59-6: LLM refinement results MUST be cached at `~/.duckln/memory/skills/llm-prereq-refine-<repo_url_slug>.md` with a `cached_at` ISO timestamp. Cache MUST be served for 24h; older entries MUST be ignored.

## R24 — User-local installer probes + None-safe exit_code (Plan 60)

**Description**: Plan 58/59 left two real issues exposed in the JustHireMe-on-Ubuntu-VM smoke test: (a) probes for tools installed via `curl ... | sh` (rustup, uv, poetry) fail right after a successful install because PATH wasn't refreshed; (b) the failure-memory recording calls crashed with `TypeError: int(None)` when a runner returned `exit_code=None`.

**Acceptance Criteria**
- REQ-PLAN60-1: Probes for tools that install to user-local directories (`rust`, `cargo`, `uv`, `poetry`) MUST try the PATH-resident command first and fall back to the well-known install location (`$HOME/.cargo/bin/cargo`, `$HOME/.local/bin/uv`, `$HOME/.local/bin/poetry`) so a freshly-installed binary is detectable without a shell restart.
- REQ-PLAN60-2: Probes for tools installed via system package managers (apt/brew: node, npm, python, git, make, cmake, docker, go) MUST NOT include `$HOME` fallback paths — those tools land on PATH naturally.
- REQ-PLAN60-3: Every `int(result.exit_code)` call site in `_execute_plan_with_bounded_recovery` MUST be guarded with a None check, falling back to `-1` as a sentinel (never collides with Unix exit codes 0-255).

## R25 — Production-ready bring-up across OSes (Plan 61, six gaps closed)

**Description**: Plan 60 left six honesty gaps. R25 closes all six so Duckln runs setup-and-run for any well-documented repo on macOS, Linux (local or VM), and Windows; classifies apt-specific failure modes with OS-specific user guidance; surfaces an authoritative-source URL for unknown tools; offers controlled README doc-link following; supports multi-choice clarification when probes leave ambiguity; persists prerequisites and usage info from a single LLM call; and exposes `/failures` for inspection and tuning.

**Acceptance Criteria**
- REQ-PLAN61-A: `_KNOWN_PREREQUISITES` MUST gain a 5th column `install_winget` populated for node, npm, python, rust, cargo, uv, go, docker, git, cmake. `detect_package_manager("Windows")` MUST return `"winget"`. `ReadmePrerequisite.install_command_for("winget")` MUST return `winget install --id <id> --silent --accept-package-agreements --accept-source-agreements`. `assess_command` MUST block winget/choco on Linux VM targets AND apt on Windows-local targets.
- REQ-PLAN61-B: `repair_intake.classify_apt_failure(stderr)` MUST return one of `sudo_password | network | locale | lock_file | other`. `apt_failure_guidance(category)` MUST return user-actionable OS-specific instructions for the first four; runtime install failure path MUST display the guidance before recording the failure.
- REQ-PLAN61-C: `/failures show | clear | window <hours>` slash command MUST be registered and dispatched. AppConfig MUST gain a `failure_window_hours: float = 24.0` field that persists via the existing serialize/deserialize/state-store path. `lookup_recent_failure` MUST consult AppConfig when no explicit `window_hours` is passed.
- REQ-PLAN61-D: A `readme_link_follower` module MUST extract install-labeled hyperlinks from a README, filter by same-domain or authoritative-publisher allow-list, enforce a 2-link-per-repo-per-24h cap, and use the existing `fetch_web_reference_summary` for the actual fetch.
- REQ-PLAN61-E: A `clarify_prompts` module MUST provide a `ClarifyPrompt = Callable[[str, Sequence[str]], int | None]` type and two adapters: `make_cli_clarify_prompt` wrapping `duckln_select`, and `make_textual_clarify_prompt` wrapping `show_choice_overlay`. `resolve_clarify_prompt(chat=...)` MUST always return a callable, never None.
- REQ-PLAN61-F: `_default_llm_readme_classifier` JSON schema MUST be extended to include `prerequisites`, `usage_summary`, and `usage_example` alongside the existing `commands`. `_parse_llm_readme_extras` MUST parse the new fields with safe defaults on any malformed input. Prereq names MUST be filtered to the existing `_LLM_REFINEMENT_ALLOWLIST`.
- REQ-PLAN61-G: `authoritative_sources.consult_authoritative_source` MUST do a single bounded (5s, 1 result) lookup via `internet_search_summary`, filter results by a strict allow-list `_AUTHORITATIVE_PUBLISHERS`, cache hits and negative results for 24h. When a code-block scanner detects an unknown tool, the result MUST be persisted as a per-repo skill note so the user is informed of the official install URL.

## R26 — Connection indicators render correctly; bundled Nerd Font + `+` dropdown (Plan 63)

**Description**: Plan 62 left two regressions visible in the screenshot — the header showed literal `[red]●[/red]` text and the chat input box disappeared from the input bar. Plan 63 fixes both, ships a bundled Symbols Nerd Font that auto-installs on first run so the icons render across macOS/Linux/Windows, and replaces the always-visible globe icon with a `+`-button dropdown menu (Upload from computer / Add context / Browse the web).

**Acceptance Criteria**
- REQ-PLAN63-1: `SplitPaneChatInterface._status_text()` MUST return a `rich.text.Text` object built via `.append(glyph, style)` calls. `_render_status_bar` MUST pass a `Text` input through unchanged (and keep accepting `str` for backward compat). No bracket markup like `[red]` may appear in the rendered header.
- REQ-PLAN63-2: The `#input-bar` Horizontal MUST contain ONLY `#attach-button`, `#input-prompt`, and `#chat-input`. The Plan 62 `#internet-icon` Static MUST NOT be present.
- REQ-PLAN63-3: `src/duckln/glyphs.py` MUST expose `DOT_SOLID`, `WEB_ONLINE`, `WEB_OFFLINE`, `SLASH`, `PLUS`, `UPLOAD`, `DOCUMENT` constants resolved via the `nerdfonts` package. `nerdfonts` is a hard dependency declared in `pyproject.toml`.
- REQ-PLAN63-4: `src/duckln/font_setup.py` MUST ship `ensure_nerd_font_installed()` that idempotently copies the bundled `SymbolsNerdFont-Regular.ttf` to the per-OS font directory (macOS, Linux, Windows). On Linux it MUST invoke `fc-cache -f` when `fontconfig` is available. On Windows it MUST add a per-user registry entry. The function MUST be safe to call on every Duckln launch.
- REQ-PLAN63-5: Duckln MUST call `ensure_nerd_font_installed()` once at session startup, BEFORE the chat interface is built. The `SymbolsNerdFont-Regular.ttf` and `LICENSE-SymbolsNerdFont.txt` MUST ship in the wheel via `[tool.setuptools.package-data]`.
- REQ-PLAN63-6: When `is_vscode_terminal()` returns True AND `AppConfig.font_setup_acknowledged` is False, Duckln MUST display the one-line VS Code settings.json guidance once and persist `font_setup_acknowledged=True`.
- REQ-PLAN63-7: Clicking `#attach-button` MUST open a dropdown overlay (`#attach-overlay`) with three options: Upload from computer, Add context, Browse the web — currently ON/OFF. Selecting "Browse the web" MUST toggle `internet_skill.set_internet_enabled`. Selecting "Upload from computer" MUST invoke the existing attachment flow. Escape MUST close the overlay.

## R27 — Multi-agent harness foundation (Plan 65)

**Description**: Duckln becomes a real multi-agent system: tools live in a central registry, agents are declarative markdown specs (not Python modules), a coordinator can spawn specialists in parallel via asyncio, and every turn is traceable. Behind feature flag `DUCKLN_HARNESS=1` to preserve legacy behaviour by default.

**Acceptance Criteria**
- REQ-PLAN65-1 (Tool Registry): `ToolRegistry` MUST expose `register`, `lookup`, `available_for_mode`, `dispatch`. Tools MUST declare a `SafetyClass`; mode policy enforces availability (HITL→S0; HOTL→S0..S2; HOOTLWO→S0..S3). 11 built-in tools MUST be registered: `shell.run`, `shell.probe`, `fs.read_file`, `fs.list_dir`, `web.search`, `web.fetch`, `state.read`, `state.write_skill`, `state.write_failure`, `user.approve`, `user.clarify`.
- REQ-PLAN65-2 (AgentDefinition): agent specs MUST live in `src/duckln/harness/agents/<name>.md` with YAML frontmatter + markdown body. `AgentRegistry.from_directory` MUST load all `*.md` files; `validate_against_tool_registry` MUST catch tools and `can_spawn` references that don't exist.
- REQ-PLAN65-3 (Single-Agent Loop): `run_agent` MUST drive one agent through a reasoning loop with budget enforcement (max_turns, llm_calls, wall-clock). Each turn = one LLM call → parse decision → validate tool name + args → dispatch → record observation. Stop reasons: `max_turns_reached`, `llm_call_budget_exhausted`, `wall_clock_budget_exhausted`, `stop_requested`, `llm_error:*`, `no_tools_allowed`.
- REQ-PLAN65-4 (MessageBus + Coordinator): `MessageBus` MUST support publish/subscribe with bounded history. `run_coordinator` MUST spawn specialists in parallel via asyncio, respect a coordinator-level budget that bounds the whole subtree, return partial results on timeout, and refuse to run agents that aren't marked `is_coordinator=True`.
- REQ-PLAN65-5 (Observability): `TraceLogger` MUST write JSON-lines per-agent log files under `~/.duckln/logs/sessions/<session_id>/agents/<agent>.jsonl`. Sensitive payloads MUST pass through `redact_sensitive_data` before being written. `/agents`, `/agents trace <session>`, `/agents costs <session>` slash commands MUST render the logs.
- REQ-PLAN65-6 (Migration + Multi-Agent Recovery): four built-in agent specs MUST be shipped: `recovery_coordinator`, `investigate_agent`, `search_agent`, `memory_agent`. When `DUCKLN_HARNESS=1`, the exhausted-recovery branch of `_execute_plan_with_bounded_recovery` MUST escalate to `run_multi_agent_recovery` before returning the legacy give-up result. When the env var is unset, legacy behaviour MUST be byte-identical.

## R29 — Plan Mode

**Description**: Duckln adds an opt-in "Plan Mode" (Codex/Claude-style): when enabled, multi-step actions (repo bring-up, multi-step free-text requests) generate a reasoned, structured plan first, render it for the user, and wait for explicit approval before running. The plan generator is a five-stage pipeline (repo understanding → candidate proposal → critique + ordering → safety classification → optional clarification) orchestrated by a harness-wired Supervisor agent that delegates to three new specialists (`repo_inspector`, `planner`, `critic`). Strict adherence + visible error attribution apply during execution.

**Acceptance Criteria**
- REQ-PLAN67-1 (Toggle + Persistence): `AppConfig.plan_mode_enabled: bool` MUST persist via the SQLite-backed config snapshot. `/plan on` and `/plan off` MUST flip the flag and survive restart. Default is False — existing 1378-test baseline behaviour MUST be byte-identical.
- REQ-PLAN67-2 (Pipeline): plan generation MUST be a five-stage pipeline. Stage 1 (`gather_repo_understanding`) MUST be deterministic, reading files + probe + failure memory without any LLM call. Stages 2/3 MUST each issue exactly one LLM call. Stage 4 MUST classify safety via `assess_command` and refuse S4. Stage 5 MUST fire at most one clarification LLM call AND surface at most 3 questions (`MAX_CLARIFICATION_QUESTIONS`).
- REQ-PLAN67-3 (Targeted clarification): Duckln MUST ask the user ONLY when step confidence is < 0.6 OR a command references a tool not in `detected_runtimes`. It MUST NOT ask for anything determinable from the repo files or probe. Hard cap = 3 questions per plan.
- REQ-PLAN67-4 (Strict adherence): once a plan is approved, the executor MUST run only the steps in `plan.steps` in order. It MUST NOT improvise recovery commands. The HITL/HOTL/HOOTLWO mode policy MUST apply per-step exactly as for direct execution.
- REQ-PLAN67-5 (Visible attribution + amendment cap): on a step failure, Duckln MUST run a one-call attribution flow producing `{cause, fix_step}`. The fix MUST be rendered to the user (`render_error_attribution`) AND inserted as a visible `PlanStep` amendment before the failed step. The plan's `amendment_count` MUST be capped at `MAX_AMENDMENTS=3`; further failures MUST mark the plan `irrecoverable` and halt.
- REQ-PLAN67-6 (Supervisor wiring): four agent specs MUST be registered: `supervisor`, `repo_inspector`, `planner`, `critic`. `run_plan_supervisor` MUST orchestrate the pipeline AND emit trace events so `/agents trace <session>` renders the multi-agent flow. If any spec is missing, `_local_plan_pipeline` MUST be the fallback.
- REQ-PLAN67-7 (UI surface): a Nerd-Font clipboard glyph (`PLAN_NOTE`) MUST appear in the textual UI header when Plan Mode is on. Status colours: green ("Plan: on"), orange ("Plan: pending|amended|approved|edited"), red ("Plan: failed"). The header indicator MUST update on the next render after `/plan on` / `/plan off`.
- REQ-PLAN67-8 (Slash command surface): `/plan`, `/plan on`, `/plan off`, `/plan show`, `/plan approve`, `/plan reject`, `/plan edit`, `/plan history` MUST be registered in `get_slash_command_descriptors()` and dispatched in `handle_session_command`. `/plan edit` MUST open the plan in `$EDITOR` (fallback `nano`), re-parse on save via `parse_plan_markdown`, and reject edits that fail `validate_plan_dict`.
- REQ-PLAN67-9 (Privacy): pending plans MUST be stored in SQLite via `write_pending_plan` AND mirrored to `~/.duckln/memory/plans/<plan_id>.md`. Plan history MUST be capped at `PLAN_MODE_HISTORY_LIMIT=50` entries. No raw API keys, secrets, or unredacted error output MUST be written into plan files; existing `redact_sensitive_data` MUST cover any LLM payload echoed into trace events.
- REQ-PLAN68-1 (Initial-setup routing): when `plan_mode_enabled` is True, every INITIAL repo-setup entry point (`/repos` via `_orchestrate_repo_to_running`, the `/repos` VM-warning branch, and `/explore`) MUST route through the plan-generation branch and stop before any clone/install/run — NOT execute the legacy setup directly. Repair/runtime-retry call sites MUST remain legacy.
- REQ-PLAN68-2 (UI toggle): the `+` dropdown MUST include a "Plan Mode — currently ON/OFF" entry that flips `AppConfig.plan_mode_enabled` and persists it, mirroring the "Browse the web" toggle. The label MUST reflect the saved state each time the dropdown opens.
- REQ-PLAN68-3 (Target-agnostic): Plan Mode MUST work for all execution targets — local, local container (docker), VM (Multipass), AWS, GCP. Execution MUST wrap every approved step via `_wrap_command_for_execution_target` (local→passthrough, vm→`multipass exec`, aws/gcp→cloud remote-exec) using the live `execution_target` from the config snapshot, and MUST halt with a clear message when the target is unresolvable.
- REQ-PLAN69-1 (Generation timeout): the conversation/generation POST MUST use a generous timeout (`CONVERSATION_TIMEOUT_SECONDS=120` cloud, `OLLAMA_CONVERSATION_TIMEOUT_SECONDS=300` local) distinct from the fast model-list GET timeout, and MUST retry once on a transport error so cold-loading local models don't fail the first call.
- REQ-PLAN69-2 (No pre-approval execution): plan GENERATION MUST NOT execute any command on any target. Understanding is read only from the local clone (if present) and repo-catalog metadata (`family_hint_from_metadata`); the clone/setup runs only as approved plan steps after `/plan approve`.
- REQ-PLAN69-3 (Editable without TTY): `/plan edit` MUST NOT spawn a blocking TTY editor inside the Textual UI. It writes the markdown, opens it non-blocking when possible, and `/plan reload` (alias `/plan edit apply`) re-parses + validates the saved file, keeping the original on failure.
- REQ-PLAN69-4 (Branded activity + numbering): the activity indicator for `/plan*` MUST use the `PLAN_NOTE` glyph label, never the generic "Processing …". Generated plan steps MUST be numbered 1..N densely (`_renumber_steps`) and render in order.
- REQ-PLAN69-5 (Header not stuck on failed): the Plan Mode header indicator MUST only show an orange actionable status for `pending`/`amended`/`approved`/`edited`; any terminal/non-actionable status (failed/completed/rejected/irrecoverable) or no pending plan MUST render the steady green "Plan: on". A failed generation MUST clear the pending plan so it never sticks the header or blocks a `/repos` retry.
- REQ-PLAN69-6 (Consistent target label): the header connection label MUST be derived the same way as the footer (`_terminal_connection_context`), so a stale `active_runtime_execution_target=vm` from a prior session shows "Local" (matching the footer and terminal pane) rather than "Ubuntu VM".
- REQ-PLAN70-1 (No stuck failed plan): the `/repos` plan-mode guard MUST block regeneration ONLY when a pending plan is actionable (`pending`/`amended`/`approved`/`edited`). A terminal/failed pending plan MUST be cleared and a fresh plan generated — a single failed generation MUST NOT permanently pin the user to it.
- REQ-PLAN70-2 (Reliable single-call generation): the critique stage MUST be best-effort. When `_critique_and_order` fails (common with small local models), `generate_plan` and `run_plan_supervisor` MUST fall back to a deterministic linear ordering of the proposed candidates (`_steps_from_candidates`) so a single successful propose call yields a numbered, reviewable `pending` plan. Only a propose-stage failure (or all-steps-blocked-S4) MAY yield `failed`.

## R30 — Full Proper Plan Mode (PRD): plan-first spine for all mutating work

**Description**: Plan Mode becomes the production-grade, plan-first spine. The `/plan` toggle stays but defaults ON; when ON, all mutating work goes through understand → draft → supervisor critic → user approval → execute → verify → reflect, and only S0 diagnostics run without an approved plan. Plan Mode OFF preserves the legacy direct path (escape hatch).

**Acceptance Criteria**
- REQ-PLAN72-1 (Grounded, complete plans): plan generation MUST read the real repo read-only (`inspect_repo_for_bringup` → README + manifests over HTTP, no target execution), classify the family correctly, and build a complete deterministic clone → install → build → run sequence via the per-family setup specialists; the panel MUST render wrapped/aligned/coloured with no mid-word breaks. A correct plan MUST be produced even with no LLM.
- REQ-PLAN72-2 (Lifecycle + terminal states): a single `PlanLifecycle` MUST own transitions and guarantee every task ends in exactly one of `complete | waiting_on_user | blocked_external | duckln_internal_bug_reported`, clearing UI activity on exit (including on exception → `duckln_internal_bug_reported`).
- REQ-PLAN72-3 (Mandatory critic, no silent bypass): every drafted plan MUST get an explicit supervisor verdict (`approve`/`revise`/`block`); `block` surfaces the exact missing question or external blocker and shows no approvable plan; when the critic LLM is unavailable the verdict is a DISCLOSED `skipped` banner — never a silent pass. A destructive (S4) or run-less plan MUST be blocked deterministically.
- REQ-PLAN72-4 (Plan-first default ON + chokepoint): `plan_mode_enabled` defaults ON for loaded/onboarded configs (explicit `false` honoured; programmatic field default stays False). `plan_first_required`/`require_plan_for_mutation` MUST gate S1+ mutations when ON; S0 passes through.
- REQ-PLAN72-5 (Redaction): all LLM/search/memory payloads (attribution stderr/stdout, understanding config/readme, critic payload, learned skills) MUST pass through `redact_sensitive_data`.
- REQ-PLAN72-6 (Tightened HOOTLWO): the executor MUST auto-run only S0 (HITL) / S0–S1 (HOTL, HOOTLWO); S2/S3 ALWAYS require explicit approval; S4 is blocked in every mode.
- REQ-PLAN72-7 (Learning loop): a reusable setup skill MUST be extracted ONLY after a verified success, keyed by `family+os+target`, with redacted content, and injected into future plan understanding.
- REQ-PLAN72-8 (Clarification hygiene): clarifications are situation-built (not hardcoded), de-duplicated, and never repeated identically.

## R31 — Finish plan-first gating + self-learning loop

**Description**: Complete Plan 72's partials and give Duckln a true self-learning loop (Run → Reflect → Extract → Loop) where each run learns from the last and injects learned skills into future plan drafts.

**Acceptance Criteria**
- REQ-PLAN73-1 (Gate VM/cloud/runtime-repair): when Plan Mode is ON, `/vm`, `/cloud`/`/create … vm`, and runtime-repair MUST NOT execute S2+ mutations directly — they draft an approvable plan (`gate_mutation_or_draft`) or surface a blocker; nothing runs until `/plan approve`. S0 passes through; Plan Mode OFF preserves legacy direct behavior.
- REQ-PLAN73-2 (Spinner ceiling): the activity spinner MUST stop and show a static "still working or blocked" notice past `_ACTIVITY_HARD_CEILING_SECONDS` (600s) so the UI never appears hung.
- REQ-PLAN73-3 (`/skills` + `/learn`): `/skills` MUST distinguish auto-learned skills (keyed `family·os·target`, source ✓ verified-run / ⚠ known-failure) from built-in/manual; `/skills show <slug>` and `/skills clear` exist; `/learn` shows the loop status and which verified skill the next plan will inject.
- REQ-PLAN73-4 (Inject — learning loop closes): `_generate_plan_for_bringup` MUST look up a verified skill by `plan_skill_signature(family,os,target)` and, when found, USE its known-good command sequence as the plan backbone and disclose the injection. This is the previously-broken link.
- REQ-PLAN73-5 (Reflect): after a run (success AND failure) Duckln MUST produce a REDACTED reflection (`reflect_on_run` + `persist_reflection`) — never raw logs/secrets.
- REQ-PLAN73-6 (Extract on failure): an irrecoverable run MUST write a redacted failure-pattern skill (`…-avoid`) keyed by family·os·target so the next run avoids the dead-end; verified success still writes a verified skill; failure MUST NOT write a verified skill.
- REQ-PLAN73-7 (Loop): `/plan retry` MUST discard the pending plan so the next `/repos` regenerates with the latest reflection + matching skill injected.

## R32 — Preparing-plan feedback, a supervisor that never fails, an opt-in pre-check, and a thoughts box

**Description**: Make plan generation legible and trustworthy. Duckln tells the user it is *preparing the plan* with lively processing words; the supervisor ALWAYS produces a verdict + concise rationale (it never "could not complete"); Duckln can do an opt-in read-only pre-check before drafting so the plan only includes what's actually missing; brittle prerequisite checks are quote-safe; and Duckln surfaces its reasoning in a collapsible "thinking" box inside the chat panel.

**Acceptance Criteria**
- REQ-PLAN76-1 (Preparing-plan feedback): plan generation MUST emit staged progress ("Preparing the plan…", "Supervisor is reviewing the plan…") and the activity spinner MUST cycle a rotating processing verb (Preparing/Reasoning/Drafting/Reviewing/Cooking/Chanting/Polishing) while a task spins; the Plan 73 hard ceiling (spinner off past 600s) MUST be preserved.
- REQ-PLAN76-2 (Supervisor never fails): every drafted plan MUST receive an `approve`/`revise`/`block` verdict with a ≤5-line rationale describing what was checked and why the plan is sound. When the LLM is missing/errors/returns junk, a deterministic checklist review (`_deterministic_supervisor_review`) is authoritative — `critic_review` MUST NEVER return `skipped`/"could not complete". A run-less plan → `revise`; an S4/empty plan → `block`.
- REQ-PLAN76-3 (Opt-in pre-check): `plan_precheck` (on|off|ask, default ask) controls a read-only (S0) pre-check before drafting. When `on`, or `ask` and the user says yes, Duckln probes the target (`command -v <tool>`, `test -d <dir>/.git`) and tailors the plan: skip the clone when already cloned, drop "Ensure X installed" steps when X is present. The probe MUST run only S0 commands (no mutation before approval). `/plan precheck on|off|ask` persists the preference.
- REQ-PLAN76-4 (Quote-safe prerequisite checks): the Node/pnpm/yarn prerequisite checks MUST be simple `<runtime> --version` commands that survive `_wrap_command_for_execution_target` wrapping on every target — no nested `node -e "…'…'…"` payload that collides with `bash -lc '…'` quoting.
- REQ-PLAN76-5 (Simplified plan panel): the plan panel MUST NOT show S0/S1/S2 safety jargon; it shows a plain ETA and step count, noting how many steps need approval, with each command rendered prominently.
- REQ-PLAN76-6 (Thinking box): when Plan Mode is ON, Duckln MUST route its REDACTED reasoning (detected family, pre-check findings, chosen run command, supervisor rationale) into a collapsible "Duckln's thinking" box mounted in the chat pane (never inside the input box); clicking the header expands/collapses it. In the plain CLI, reasoning falls back to dim `· <thought>` lines. The box is presentational only — approve/reject actions stay in the plan panel.

## R33 — Detect the runtime version in the pre-check, approve once, no per-step re-prompts, live thinking, and web self-repair

**Description**: Make Plan Mode reason about the *required* runtime version up front, treat plan approval as authorization for the whole plan, stream reasoning live, and actually self-repair a failed step from the web when internet is on. Driven by a real failure: a Vite repo needing Node ≥20 was set up against Node 18, with per-step re-prompts after approval and no web-based recovery.

**Acceptance Criteria**
- REQ-PLAN77-1 (Version-aware planning): before drafting, Duckln MUST determine the repo's minimum required runtime version from `package.json` `engines.node`, `.nvmrc`/`.node-version`, or a README hint, AND probe the active target's installed version in the pre-check. When the target version is lower than required (or unknown), the drafted plan MUST state the correct version and include an install/switch step (NodeSource on Linux targets, Homebrew on local macOS) BEFORE the dependency install — so the user approves the right version once. When the target already satisfies the requirement, no install step is added.
- REQ-PLAN77-2 (Plan approval authorizes its steps): once a plan is approved, its original planner steps MUST execute without a per-step approval prompt. S4 MUST still be blocked in every mode, and any amendment/user step added AFTER approval (e.g. a post-failure fix) MUST still require an explicit prompt.
- REQ-PLAN77-3 (Live thinking): the "Duckln's thinking" box MUST receive reasoning lines BEFORE each long-running step (reading the repo, checking the required version, asking the supervisor), not only after the plan is finalized, so it fills while Duckln works.
- REQ-PLAN77-4 (Web self-repair): when local attribution cannot produce a fix for a failed step AND the internet skill is enabled, Duckln MUST search public sources for the exact (redacted) error and re-attribute with that evidence to propose a concrete fix step. When the internet skill is OFF, Duckln MUST tell the user to run `/internet on` (and MUST NOT make silent network calls). All web/LLM payloads stay redacted.

## R34 — Enforced supervisor reasoning: evidence-rich steps, deeper critic, one replan loop, re-reviewed recovery, honest model-connection failures

**Description**: Duckln must truly reason before any plan reaches the user — questioning evidence, OS/target fit, and prior failures — and must be honest when it cannot reach the model. Closes the remaining gaps on top of R30–R33.

**Acceptance Criteria**
- REQ-PLAN78-1 (Honest model-connection failures): when an LLM call fails because the model is UNREACHABLE (connection refused/timeout/host down), Duckln MUST surface "Duckln is not able to connect to the model" and NOT present a plan as if the model reviewed it; this is a recoverable `waiting_on_user` state. (Garbage-response handling is governed by REQ-PLAN79-1, which supersedes the earlier fall-back wording.)
- REQ-PLAN78-2 (Evidence-rich steps): every `PlanStep` MUST carry `target`, `source`, `evidence_excerpt`, and `cwd` (defaults empty, backward-compatible). The supervisor MUST `revise` a plan whose mutating (S2+) step lacks a verification command or a clear target; `source`/`evidence_excerpt` are best-effort. The plan panel MUST show each step's target and source.
- REQ-PLAN78-3 (Deeper critic checks): the deterministic supervisor MUST additionally `revise` on a mutating step missing verification, a mutating step with no target, a wrong-OS command (e.g. `brew` on Linux), or a command that matches a recorded prior failure for this repo; it MUST keep blocking S4/empty plans. The LLM critic prompt MUST request the same checks. The rationale stays ≤5 lines.
- REQ-PLAN78-4 (One replan loop): a `revise` verdict MUST trigger exactly one automatic re-draft that repairs the named mechanical issues, followed by a single re-review; it MUST NOT loop more than once. If still weak, Duckln shows the revised suggestion or blocks with one exact question.
- REQ-PLAN78-5 (Re-reviewed recovery): after a failed step, the amended plan MUST be run through the supervisor critic BEFORE being offered; a blocked amendment surfaces the blocker, and the failure payload MUST include the step's `cwd`. User re-approval is still required before any mutation.
- REQ-PLAN78-6 (Internal-bug surfacing): a genuine Duckln CODE failure during plan drafting MUST end in `duckln_internal_bug_reported` with a REDACTED diagnostic and a cleared activity spinner — never a half-finished plan or a stale "Working" state. Plan-mode drafting MUST clear the spinner on every terminal outcome.
- REQ-PLAN78-7 (Redaction additions): redaction MUST also mask `Authorization:` headers and OAuth/authorization-code values (`code`/`state`/`access_token`/`id_token`/`refresh_token`/`client_secret`) before any LLM/search/memory payload.

## R35 — Never act on a garbage model response; resume the last operation on "retry"

**Description**: A model response that cannot be read is never treated as a review (in plan critique or anywhere else), and the user can resume an interrupted operation with a natural-language "retry"/"try again"/"check now".

**Acceptance Criteria**
- REQ-PLAN79-1 (No garbage response, ever): when a REACHABLE model returns an unreadable/garbage response to a supervisor critique, Duckln MUST retry once and then DISCARD it — a garbage response is never accepted as a verdict. The deterministic structural review is the authoritative supervisor whenever a clean model verdict is unavailable. (Unreachable model → REQ-PLAN78-1.)
- REQ-PLAN79-2 (Resume on retry): when the user says "retry" / "try again" / "check now" (or similar) and Duckln has an interrupted operation (e.g. a plan draft halted because the model was unreachable), Duckln MUST pick up the SAME operation where it left off — re-running it in the SAME mode (Plan Mode → re-draft the plan and ask for approval again) using the saved repo + execution target — rather than starting over or doing nothing.

## R36 — Smarter, evidence-grounded planning (read README, plan logically, get it running, learn)

**Description**: Make Duckln reason logically about how a repo is actually set up and run, grounding steps in the repo's own evidence and learning per-repo facts across runs.

**Acceptance Criteria**
- REQ-PLAN79-3 (Package-manager correctness): the supervisor MUST `revise` a plan whose install command uses a package manager that conflicts with the lockfile present (e.g. `npm install` when only `pnpm-lock.yaml` exists).
- REQ-PLAN79-4 (Logical run command): for Node repos, the run step MUST be derived from the repo's `package.json` scripts (preferring a dev server: dev > develop > start > serve > preview) formatted for the repo's package manager, before falling back to repo-knowledge/README. "Get it running" MUST prefer a dev server over a production `start` that needs a build.
- REQ-PLAN79-5 (Environment file): when a repo ships a `.env.example`/`.env.sample`/`.env.template` and the plan does not already create `.env`, Duckln MUST add a step to copy it before the run step, noting the user may need to fill in real secret values.
- REQ-PLAN79-6 (README evidence on steps): a step traced to the README MUST carry the exact (redacted) README line it came from as `evidence_excerpt`, shown in the plan panel.
- REQ-PLAN79-7 (Actionable verification): a run step's verification MUST be concrete — for a web/dev server, that its printed local URL responds; otherwise that the process stays alive.
- REQ-PLAN79-8 (Surface the URL): when a run step starts a web server, Duckln MUST surface the URL the user can open, rewriting `localhost` → the VM IP on a VM target.
- REQ-PLAN79-9 (Per-repo learning): after a verified run, Duckln MUST record per-repo facts (the working run command, the Node version) and, on the next draft for the SAME repo, prefer the learned run command and fall back to the learned Node version when none is detected.

## R37 — Robust & legible: detect requirements generically, fix deterministically, browse for real (live), and surface thinking/tasks

**Description**: Duckln must stop erroring on detectable requirements, fix failures (often without the LLM), browse the web for real with live per-URL visibility, and present its reasoning/tasks legibly (Claude/Codex-style). Driven by the Vite/Node failure + follow-up.

**Acceptance Criteria**
- REQ-PLAN80-1 (Framework Node floor): the required Node major MUST be inferred from framework dependencies (vite/next/@angular…) when not declared in `engines`/`.nvmrc`, so a Vite app's Node-20 floor is detected in the pre-check round.
- REQ-PLAN80-2 (Generic multi-ecosystem detection): Duckln MUST read each ecosystem's OWN declared version constraint — Python (`requires-python`/`.python-version`), Go (`go` directive in go.mod), Rust (`rust-version`/rust-toolchain), and `.tool-versions` — index those files, and inject the right install step when the target is missing/below it.
- REQ-PLAN80-3 (Deterministic error→fix): a known failure pattern (Node engine mismatch, command-not-found, missing native toolchain, missing Python module, port-in-use, permission) MUST be fixed by a deterministic rule BEFORE the LLM; the fix becomes a reviewed amendment. Unmatched errors fall back to LLM/web.
- REQ-PLAN80-4 (Cross-repo lessons): lessons (pitfall + fix) MUST be recorded (redacted) keyed by an error/framework signature and consulted on every future repo — at draft (surfaced) and at repair (applied) — so a pitfall solved once is pre-empted everywhere. A seeded baseline exists.
- REQ-PLAN80-5 (Real, live web repair): when local attribution can't fix a failure and internet is on, Duckln MUST surface `Browsing <url>…` LIVE per source, fetch the actual page CONTENT (not just the snippet), pass that to attribution, bound the number of pages, and report a concrete fix or a clean "no usable fix" — never stall on garbage.
- REQ-PLAN80-6 (Execution legibility): plan execution MUST show a per-step completion checkbox and end with "✅ Plan executed — your repo is ready! 🎉" on success, and stream live reasoning into the thinking box during execution.
- REQ-PLAN80-7 (Inline rendering): a `duckln_ui` setting (`auto`|`inline`|`full`) MUST let Duckln render inline (flowing in the terminal, not fullscreen); `/ui` sets it. The fullscreen split-pane (with the live VM terminal) remains the `full` path.
- REQ-PLAN80-8 (Thinking time + task hierarchy): a user question MUST show a "Thought for Ns" marker before the answer in the chat; multi-step work MUST render a prominent main-task header distinct from nested sub-steps.

## R38 — Robust setup for ANY repo (env, monorepo, services, codegen, native deps, VM ports, private repos)

**Description**: Extend Duckln's first-go reliability beyond a single-package Node/Vite repo to arbitrary repos, closing the seven gaps that can break a similar-looking repo, with honest one-question blocks only where a human truly must act.

**Acceptance Criteria**
- REQ-PLAN81-1 (Env vars): Duckln MUST parse `.env.example` KEYS and name the required secret-looking, placeholder/empty keys (e.g. `DATABASE_URL`, `OPENAI_API_KEY`) on the env-copy step so the user knows what to fill; a run-time missing-config error is still caught by the deterministic fix layer.
- REQ-PLAN81-2 (Monorepo): Duckln MUST detect a workspace monorepo (`workspaces`/`pnpm-workspace.yaml`/`turbo.json`/`nx.json`/`lerna.json`) and run env/codegen/run steps from the chosen app subdir (e.g. `apps/web`) while clone + workspace install stay at the root.
- REQ-PLAN81-3 (Services): when a repo declares backing services via docker-compose (postgres/redis/…), Duckln MUST start them (`docker compose up -d`) before the app when Docker is available, or surface ONE clear blocker when it is not.
- REQ-PLAN81-4 (Codegen/build): Duckln MUST insert required pre-run generation/build steps (e.g. `npx prisma generate`, graphql codegen, `build` before a production `start`) after install and before run.
- REQ-PLAN81-5 (Broader deterministic fixes): the deterministic error→fix layer MUST also handle Prisma-not-generated, missing native libs (libpq/Python.h/openssl/ffi), Playwright browser deps, and service-unreachable (Postgres/Redis) — fixed without the LLM and recorded as cross-repo lessons.
- REQ-PLAN81-6 (VM port reachability): on a VM target, after a server starts Duckln MUST verify the served URL is reachable from the host before telling the user to open it; if not, it MUST give precise guidance (`--host 0.0.0.0` / `ufw allow <port>`) rather than a dead link.
- REQ-PLAN81-7 (Private repos): a clone failing as private/unreachable (auth error / exit 128) MUST be deterministically BLOCKED with one precise question (provide a token / check the URL), not retried or web-searched.
- REQ-PLAN81-8 (UI works across flows): inline mode MUST render the non-split interface; the plan panel MUST render all new step types (compose/codegen/env/monorepo-cwd) cleanly; `/ui inline|full|auto` switches rendering.

## R39 — Correct, fast, legible pre-check (one probe, real values, live agent narration)

**Description**: The target pre-check must report ACCURATE results quickly and narrate its reasoning live, so the system-check is trustworthy and never looks stuck.

**Acceptance Criteria**
- REQ-PLAN82-1 (One-shot probe): the pre-check MUST gather all tool presence, runtime versions, and clone status in a SINGLE delimited command (one round-trip), so per-command output cannot bleed and the result is fast.
- REQ-PLAN82-2 (Accurate parsing): parsing MUST read only the delimited block and strip `DUCKLN-DONE` marker noise; a tool absent from the probe MUST NOT be reported present, and a runtime version MUST be parsed from its real `--version` output (e.g. `v18.19.1 → 18`), never a marker/hash digit or "0". When the version is unknown it MUST say "unknown", never "Node 0".
- REQ-PLAN82-3 (Live agent narration): during planning Duckln MUST emit live, role-labelled thoughts (Inspector → Pre-check with REAL values → Planner → Supervisor) in README→system→draft→review order, not a single burst after the fact.
- REQ-PLAN82-4 (Never frozen): the probe MUST have a bounded timeout and a clear activity message; on timeout Duckln MUST proceed by drafting from repo evidence rather than hanging.

## R40 — Lean, intelligent plans (no redundant checks) + pre-check/target visibility + smart API-key entry

**Description**: The drafted plan must reflect only what's actually needed for the specific repo and target — never re-checking satisfied prerequisites — show what was found and where it installs, and intelligently help set required API keys.

**Acceptance Criteria**
- REQ-PLAN83-1 (Lean plan): the supervisor/assembler MUST drop BOTH "Ensure X is installed" AND "Verify README prerequisite: …" steps for any tool already present on the target OR being installed by a version step. A genuinely-missing tool's prereq MUST remain. No duplicate handling of a runtime that a version-install step already covers.
- REQ-PLAN83-2 (Pre-check + target visible): the plan panel MUST show, under their own headings, what the pre-check found (tools + versions + clone status) and the target machine (e.g. "Ubuntu VM 'duckln-vm'", "Local machine").
- REQ-PLAN83-3 (Smart API-key entry): when a repo requires secret env keys, Duckln MUST detect them during planning and, AFTER a successful setup, interactively help set them — asking which provider only when the keys are alternatives (e.g. OPENAI/ANTHROPIC/…), prompting for each needed key, and writing the value into `.env` on the target. The secret value MUST be redacted from all logs/thoughts/memory. The flow MUST be repo-driven (no prompt when no keys are required) and skippable (Enter skips a key). Applies to all repos/ecosystems.

## R41 — Idempotent clone, accurate clone-failure diagnosis, continuous planning progress

**Description**: A clone must never fail or repeat just because the repo is already present, a clone failure must be diagnosed by its actual cause (not a bare exit code), and the planning phase must always show what Duckln is doing.

**Acceptance Criteria**
- REQ-PLAN85-1 (Already-present is not a failure/re-clone): when the repo is already on the target, Duckln MUST NOT add a clone step (pre-check), and if a clone step runs it MUST detect the existing checkout and continue (idempotent guard) rather than re-clone or fail with exit 128. Already-cloned detection MUST be reliable (a fallback check), not dependent on output-capture timing.
- REQ-PLAN85-2 (Accurate clone diagnosis): a clone failure MUST be classified by its stderr — "destination already exists" → already-present (continue), real auth/not-found/DNS signals → private/unreachable block with one token question. A bare exit code 128 with no auth/not-found signal MUST NOT be labelled "private".
- REQ-PLAN85-3 (Continuous progress): during planning Duckln MUST keep the user informed at each slow step (network fetch of README/manifests, and the supervisor/critic review — labelled as possibly slow on a local model), with no long silent gap.

## R42 — Long-running dev servers succeed and open in the local browser; no crash/spam

**Description**: A dev server that stays running is a success (not a timeout failure); the served app opens in the user's local browser even on a headless VM/container/cloud; amendment panels never crash; install output never spams the chat with mirror links.

**Acceptance Criteria**
- REQ-PLAN86-1 (Run = serving, not exit): a run/serve step MUST be launched detached and judged by readiness (served URL / ready banner) — a server that keeps running is SUCCESS and is LEFT running; only a real crash/early-exit is a failure routed to the amendment flow. It MUST NOT be timeout-failed for not exiting.
- REQ-PLAN86-2 (Open locally, headless target): on a remote target Duckln MUST bind the server to all interfaces (`0.0.0.0`, per the resolved tool), compute a host-reachable URL (VM IP / published port), verify reachability, and open it in the user's LOCAL default browser — with a clear clickable line. If unreachable, it MUST give the exact port-forward / `ufw` / `--host 0.0.0.0` remedy, never a dead localhost link.
- REQ-PLAN86-3 (No amendment-panel crash): rendering a plan that contains an amendment step MUST NOT raise (use a defined warning colour).
- REQ-PLAN86-4 (No link-capture spam): terminal link capture MUST announce each unique app URL at most once and MUST skip package-mirror/doc/registry hosts.

## R43 — Understand the README first; detect the app archetype; run any repo correctly (incl. cloud GUI streaming); legible agent thinking

**Description**: Duckln MUST understand the whole README + manifests before recommending anything, detect what KIND of app the repo is (desktop GUI / web / CLI / service / library) and run it the right way — including streaming a desktop app's window from a headless VM/cloud/Docker to the user's local browser — and the in-flight thinking MUST read like the agents communicating and be readable.

**Acceptance Criteria**
- REQ-PLAN87-0 (Understand before recommend): before any run command or recommendation, Duckln MUST read the whole README + manifests and build a grounded `RequirementsSpec` (archetype, runtimes, services, env keys, migrations), reconciling README prose against repo files (file evidence wins on conflict) and surfacing — never silently dropping — anything it cannot provision. It MUST then probe the system, then plan. An unreachable enrichment model MUST fall back to the deterministic spec, not block.
- REQ-PLAN87-1 (Archetype-correct run): a desktop app (Tauri/Electron — by dep, `tauri.conf.json`/`src-tauri`, or script) MUST run its native shell (`tauri dev` / `electron .`), NEVER the frontend-only `dev` script; web/CLI/library repos keep their correct run/no-run behaviour.
- REQ-PLAN87-2 (Desktop on headless = streamed, not broken): on a remote headless target a desktop app MUST be run under a virtual display (Xvfb) with the webview libs and streamed to the user's local browser via noVNC over a host-reachable forwarded port — NOT surfaced as a web URL that errors with `window.__TAURI_INTERNALS__ is undefined`. On local it launches the native window. Streaming-stack installs MUST be bounded, idempotent, and approved; a blocked install MUST be surfaced, never a dead URL.
- REQ-PLAN87-3 (Provision what the README declares): services the README declares in PROSE (e.g. "install Postgres 15") — not just docker-compose — MUST be provisioned system-aware (install + start + create DB + wire `DATABASE_URL` + migrations), idempotently.
- REQ-PLAN87-4 (Legible agent thinking): planning/execution MUST emit agent-to-agent handoff thoughts (Inspector→Classifier→Planner→Supervisor→Executor), the one-line activity status above the input MUST stay populated while Duckln is working, and rapid thoughts MUST be paced so each line is readable (no flicker) and none is lost.

## R44 — A desktop app actually BUILDS & RUNS on the VM (Tauri/Electron toolchain + remote detection)

**Description**: Detecting a desktop app is not enough — it must actually start. A Tauri app's backend is a Rust binary that must compile; without the Rust toolchain + full Tauri build deps it never starts and the frontend shows `window.__TAURI_INTERNALS__ is undefined`. Duckln MUST install the complete toolchain, render the webview headlessly, detect desktop apps on remote/private repos, and allow for a long first build.

**Acceptance Criteria**
- REQ-PLAN88-1 (Full Tauri toolchain before run): for a Tauri app the plan MUST install the complete Tauri-on-Linux prerequisites (`build-essential`, `libssl-dev`, `libwebkit2gtk` (4.1 with a 4.0 fallback), `librsvg2`, `libayatana-appindicator3`, `patchelf`) AND the Rust toolchain (rustup) AND ensure the Tauri CLI — all idempotent — BEFORE `tauri dev`. Electron MUST NOT pull Rust.
- REQ-PLAN88-2 (Headless webview renders): a Tauri app launched under Xvfb MUST set `WEBKIT_DISABLE_COMPOSITING_MODE=1` + `WEBKIT_DISABLE_DMABUF_RENDERER=1` and have `cargo` on PATH (`source ~/.cargo/env`), and only the noVNC stream URL (never the Vite frontend URL) is surfaced.
- REQ-PLAN88-3 (Remote/private desktop detection): the pre-check probe MUST detect a VM-side desktop app (`src-tauri`/`tauri.conf.json`, or `electron` in `package.json`) so a remote or private repo is classified `desktop_gui` and its run step is the native shell — even when the host has no clone and GitHub-raw is unavailable.
- REQ-PLAN88-4 (Long first build): the readiness window for a Tauri run MUST allow for a multi-minute first Rust compile, and Duckln MUST say the backend is still compiling rather than false-failing.

## R45 — Survive a heavy build (OOM), stop honestly, route headless GUIs, and don't redo finished work

**Description**: A heavy Rust/Tauri build OOMs on a small VM (the kernel SIGKILLs the compiler). Duckln must diagnose that deterministically and fix it, prevent it proactively, stop honestly (no false "failed" while alive, no leaked build, no stuck "Working…"), stream the GUI whenever there's no display, and skip already-finished setup on a re-run.

**Acceptance Criteria**
- REQ-PLAN89-1 (Deterministic OOM diagnosis + fix): a build killed by OOM (`signal: 9`/`SIGKILL`/`Killed` in a build/`cc1plus: out of memory` context) MUST be recognized by the deterministic error→fix library as `OUT_OF_MEMORY` and remedied by adding swap (when absent) + a single-threaded build (`~/.cargo/config.toml jobs=1`) — WITHOUT depending on the LLM. A bare "memory" mention MUST NOT trigger it.
- REQ-PLAN89-2 (Proactive low-RAM guard): when the pre-check shows a low-RAM target (< ~6 GB) with no swap and the app is a Tauri/Rust build, the plan MUST add swap + cap build parallelism BEFORE the run step so the compile isn't OOM-killed.
- REQ-PLAN89-3 (Honest long-build lifecycle): a detached run MUST capture its real PID (no `;`/`.` prefix that breaks `nohup`/`$!`); Duckln MUST NOT declare failure while the build is alive and compiling; and the "Duckln is working" activity indicator MUST clear once the thought stream goes idle (no spinning after a terminal state).
- REQ-PLAN89-4 (Headless routing by display): a desktop app MUST use the Xvfb + noVNC streaming path whenever the target has no usable `$DISPLAY` (Linux), regardless of whether the session is labelled local or remote.
- REQ-PLAN89-5 (Idempotent setup): re-runnable setup steps MUST be guarded so a re-run skips finished work — `[ -d node_modules ] || <install>`, `[ -f .env ] || cp …`, `command -v <tool> || apt install <tool>`.

## R46 — Repo agent: answer questions about the active repo by reading its code (Plan 90 Phase 1)

**Description**: Duckln must do more than set up a repo — it must answer questions ABOUT the active repo by reading/searching its actual files (not just the README/metadata), via a real LLM tool-use loop, confined to the repo workspace. This is the foundation for later repo actions/subagents.

**Acceptance Criteria**
- REQ-PLAN91-1 (Live tool-use loop): a repo question MUST be answered by driving the harness tool-use loop (`run_agent`) with a read-only tool set (`fs.read_file`, `fs.list_dir`, `shell.probe`, `web.search`, `state.read`, `user.clarify`), provider-agnostic via the JSON decision contract (works with local models that lack native function-calling).
- REQ-PLAN91-2 (Grounded answer): the answer MUST be synthesized from the files the agent actually read and SHOULD cite `path`/file:line; if the repo lacks the answer, Duckln says so rather than inventing.
- REQ-PLAN91-3 (Workspace confinement): every filesystem tool MUST be confined to the active repo workspace; path-traversal attempts (`../`) MUST be rejected and never surface out-of-repo content.
- REQ-PLAN91-4 (Honest no-model): with no AI provider configured, `/ask` MUST say so plainly, not fail silently.
- REQ-PLAN91-5 (Legible trace): the agent's tool calls MUST stream into the thinking box as agent-handoff lines while it works.

## R47 — Repo agent: filesystem write/edit/search + offload (Plan 90 Phase 2)

**Description**: The repo agent must read/write/edit/search files in the active repo with absolute paths, confined to the workspace, and offload large results to files to avoid context overflow.

**Acceptance Criteria**
- REQ-PLAN92-1 (Tools): `fs.search` (regex grep, file:line), `fs.glob`, `fs.write`, `fs.edit` (unique string-replace unless replace_all) MUST exist, registered with safety classes (search/glob S0; write/edit S2), confined by the path-traversal guard.
- REQ-PLAN92-2 (Offload): a tool result larger than a budget MUST spill to a workspace results file and return a path + head/tail summary instead of the full text (no context overflow); small results pass through.

## R48 — Repo agent: take actions (edit/fix/run/git) with verification (Plan 90 Phase 3)

**Description**: Beyond Q&A, the agent must perform engineering actions on the repo and verify them.

**Acceptance Criteria**
- REQ-PLAN93-1 (Action tools): `repo.run` (run tests/scripts in the repo) and `git.run` (safe subcommands; push/force/reset --hard BLOCKED) MUST exist; a `repo_engineer` agent reads→edits→verifies.
- REQ-PLAN93-2 (Honest summary): `/do <task>` MUST report what changed, what was run to verify, and pass/fail honestly (no claimed success without evidence).

## R49 — Subagent delegation with isolated contexts (Plan 90 Phase 4)

**Acceptance Criteria**
- REQ-PLAN94-1: a complex task MUST be split across subagents with SEPARATE context windows — an Explorer (read-only) gathers facts in its own `run_agent` context, then an Engineer acts in a FRESH context seeded with the Explorer's findings; the main flow orchestrates and merges.

## R50 — Configurable HITL + pluggable memory (Plan 90 Phase 5)

**Acceptance Criteria**
- REQ-PLAN95-1 (Per-tool approval): side-effecting tools MUST go through the approve callback in HITL/HOTL (HOOTLWO auto); a persisted per-tool policy (`allow`/`deny`) MUST override the mode gate, configurable via `/policy`.
- REQ-PLAN95-2 (Pluggable memory): a `MemoryStore` Protocol MUST define the backend contract; the default SQLite store satisfies it; `set_state_store_factory` allows swapping backends without touching `state/access.py`.
- REQ-PLAN95-3 (Persistent repo/user memory): per-repo Q&A is remembered across conversations (redacted, bounded) and recalled as hints; user preferences persist (`/prefs`).

## R51 — Environment-agnostic repo agent (Plan 96 Phase 1)

**Acceptance Criteria**
- REQ-PLAN97-1: `fs.read/write/edit/search/glob/list` MUST work on `vm`, `container`, and `cloud` targets (routed via the canonical wrapper: `multipass exec`/`docker exec`/`ssh`), confined to the in-target repo dir; `../` escapes rejected. Local still uses the host Path fast path. (Verified live on multipass `duckln-vm`.)
- REQ-PLAN97-2: `container` is a first-class execution target (`docker exec`).

## R52 — App reachable from the browser on any target (Plan 96 Phase 2)
- REQ-PLAN98-1: `expose_app_and_open` MUST turn a served URL into a host-browser-reachable URL and open it for local (direct), VM (VM-IP/port-forward), container (published port), cloud (SSH tunnel), and stream desktop GUIs via noVNC. When reach can't be auto-established it MUST give the exact remedy, never a dead link. `app.serve` tool runs the app on the active target and opens it.

## R53 — Native provider function-calling (Plan 96 Phase 3)
- REQ-PLAN99-1: tool schemas MUST be derivable for Anthropic/OpenAI and their tool-call responses parsed into a uniform decision; `run_agent` accepts a `tool_decider` to use the native path, falling back to the JSON contract for Ollama/others.

## R54 — Parallel isolated subagents (Plan 96 Phase 4)
- REQ-PLAN100-1: `run_parallel_explorers` MUST fan out read-only Explorer subagents concurrently, each in its OWN `run_agent` context, bounded by `max_parallel`, and return all outcomes for merging.

## R55 — Smarter, pluggable memory (Plan 96 Phase 5)
- REQ-PLAN101-1: repo memory recall MUST be relevance-ranked (keyword overlap, recency tiebreak, stopword-filtered) — used by `/ask`. A file backend (`FileMemoryStore`) MUST satisfy `MemoryStore` and be selectable via `set_state_store_factory`.

## R56 — Auto-routing + git safety (Plan 96 Phase 6)
- REQ-PLAN102-1: a free-form repo question/action MUST auto-route to the repo agent when an active repo + provider exist (explicit `/ask`//`/do` still work); non-repo chat is unaffected.
- REQ-PLAN102-2: `git.run` MUST refuse `commit` on the default branch (main/master) — require a feature branch — in addition to blocking push/force/reset --hard.

## R57–R62 — Context engineering + token optimization + long-context + plan-first + live A2A + autonomous skills (Plan 103 / Plans 104–109)

- REQ-PLAN104-1 (Context compaction): the harness loop MUST keep recent observations full-fidelity and fold older ones into a rolling summary (deterministic fallback or LLM summarizer), not blunt-truncate.
- REQ-PLAN105-1 (Token-aware): a token estimator + model→context-window map MUST exist; consecutive near-duplicate observations MUST be deduped.
- REQ-PLAN106-1 (Long context): a map-reduce summarizer MUST let the agent reason over a file/result larger than the window (chunk→map→reduce), query-biased.
- REQ-PLAN107-1 (Plan-first): a non-trivial `/do` MUST draft + show a TODO checklist before any mutating action.
- REQ-PLAN108-1 (Live A2A): orchestrated subagents MUST communicate over a real `MessageBus` (Explorer publishes findings, Engineer reports back) — a persisted bidirectional transcript, not one-way hints.
- REQ-PLAN109-1 (Autonomous skills): on a verified success Duckln MUST distill a reusable per-repo skill and recall the most relevant one (keyword-ranked) on a later similar task.

## R63 — Real-error attribution, generalized OOM guard, correct target/label, weak-model guardrails (Plan 110)

**Description**: Failure handling must use the REAL error (not a placeholder), prevent OOM for ANY heavy build, label the target precisely, and never present a hallucinated fix — all repo-agnostic (keyed on signals, never a repo name).

**Acceptance Criteria**
- REQ-PLAN110-1 (Real error reaches diagnosis): a detached run's death MUST pass the captured run log to attribution so `match_deterministic_fix` sees the actual signature (OOM/compiler/missing-lib) and the correct deterministic fix fires.
- REQ-PLAN110-2 (Generalized OOM guard): a swap + single-threaded-build step MUST be injected before ANY heavy build (`cargo`/`rustc`/`tauri`/`cmake`/`make`/`gcc`/`gradle`/`mvn`/`webpack build`/…) on a low/unknown-RAM remote target — detected by build command, not repo.
- REQ-PLAN110-3 (Composite target label): the heading MUST show `location-resource` — `local-<vm>` (local VM), `<provider>-<name>` (cloud), `local-<container>`.
- REQ-PLAN110-4 (No invented fixes): an LLM fix implausible for the error class (e.g. a `timeout` flag for a `SIGKILL`/compile/OOM crash) MUST be rejected; Duckln prefers deterministic → web → honest "no automatic fix".
- REQ-PLAN110-5 (Low-RAM honesty): when the target is genuinely too small, recommend more RAM (e.g. `multipass set local.<vm>.memory=6G`) — a real remedy, not silent grinding.

## R64 — Resource sub-agent: detect any resource crunch, explain with real numbers, scale on approval, log (Plan 111)

**Description**: When a target is short on ANY resource (RAM/disk/CPU/swap), Duckln must see what's available, derive the requirement from real signals (never hardcoded), explain with numbers + the minimum, ask the user to approve a sensible increase (bounded by host capacity; cost-aware on cloud; with an option to enter a custom value), act on approval, and log per target. Repo-agnostic.

**Acceptance Criteria**
- REQ-PLAN111-1 (Probe): `probe_target_resources` reads RAM/swap/disk/CPU for local/vm/container/cloud; `probe_host_resources` reads the host (caps local-VM growth).
- REQ-PLAN111-2 (Derived, not hardcoded): the requirement is DERIVED — from the failure (OOM ⇒ ≳current×2; disk-full ⇒ current+footprint), measured footprint, or learned history; the recommendation MOVES with the probe inputs (a test asserts no per-stack magic minimum).
- REQ-PLAN111-3 (Host-bound + cost-aware): a local VM/container recommendation MUST be capped at host capacity (honest "can't" when impossible); cloud recommends the smallest fitting size + a cost note.
- REQ-PLAN111-4 (Approve / custom / decline; never auto): a resize requires explicit approval, offers a custom value, and NEVER runs unprompted.
- REQ-PLAN111-5 (No hard-cap; logged): a resource crunch at failure time PAUSES (waiting_on_user) without consuming the amendment cap, and every event is logged per target (`/resources` shows usage + log).

## R65 — Run any repo's full declared build sequence + complete resource scaling + integration gate (Plan 112)

**Acceptance Criteria**
- REQ-PLAN113-1 (Full build sequence): before the run, Duckln MUST run a repo's DECLARED prebuilds — `tauri.conf.json` beforeBuild/beforeDevCommand, package.json `prepare`/`prebuild`/`generate`/`build:*`/`sidecar*` scripts, (and Makefile/README build steps) — keyed on manifests, not repo name, so a required prebuild artifact is never skipped.
- REQ-PLAN114-1 (Container resize): `apply_resize`/`resize_commands_for_target` MUST use `docker update --memory/--cpus` for a container (not multipass); container rootfs disk-in-place is honestly unsupported.
- REQ-PLAN115-1 (Cloud resize): `build_cloud_resize_commands` MUST emit correct AWS (`modify-volume`, stop→`modify-instance-attribute`→start) and GCP (`disks resize`, stop→`set-machine-type`→start) commands, cost-careful + approval-gated.
- REQ-PLAN117-1 (Proactive crunch): a heavy build on a remote target MUST surface a disk crunch (≥90% used) with real numbers BEFORE running.
- REQ-PLAN118-1 (Integration gate): an opt-in real matrix (repos × {local,vm,container}) measures a pass-rate; "99%" = that measured rate, not a claim.

## R66 (Plan 119) — Real-agent reliability: truthful status, mandatory gate, dependable fixes, polyglot ordering, clean progress

**Acceptance Criteria**
- REQ-PLAN119-1 (Truthful status): the header provider dot MUST reflect true connectivity (`validate_api_key` → reachable host + listable models), NOT model-membership — so a connected provider whose model id isn't enumerated (e.g. gpt-5-mini) shows GREEN, matching `/healthcheck`.
- REQ-PLAN119-2 (Mandatory gate): LLM-dependent actions (free-text chat, `/ask`, `/do`) MUST be gated on `ensure_provider_connected`; a genuine disconnect stops with a helpful "Connect an LLM provider…/run /healthcheck" message; a connected provider is NEVER blocked.
- REQ-PLAN119-3 (Reliable attribution): fix-attribution MUST request provider JSON mode + a sufficient token budget (no truncation) and RETRY once with a strict "JSON only" reprompt; `parse_plan_json` MUST repair a truncated object — so capable models reliably yield a usable fix instead of "no automatic fix."
- REQ-PLAN119-4 (Polyglot ordering): a repo's secondary ecosystem (e.g. a Python backend in a subdir) MUST be set up BEFORE a JS prebuild that depends on it; a "venv not found: <path>" error MUST map to a deterministic create-venv+install fix (no LLM).
- REQ-PLAN119-5 (Clean progress): `ollama pull` MUST render as a single updating progress line (bar when a % is present), never a stacked stream of "pulling manifest"/elapsed-time noise.

## R67 (Plan 120) — Polyglot prebuild ordering must execute on the TARGET
- REQ-PLAN120-1: the proactive secondary-ecosystem (Python backend) setup MUST run on the execution target (local/VM/container/cloud), not via a host-side filesystem scan — so a polyglot prebuild (`build:sidecar` needing `backend/.venv/bin/python`) is prevented from failing on a remote target, not merely recovered after the fact. Keyed on manifests in immediate subdirs; idempotent; injected only when a prebuild/codegen step exists; the Plan 119 `MISSING_VENV` deterministic fix remains the safety net.

## R68 (Plan 121) — Truthful dot for default-URL providers + masked key entry + proven connection
- REQ-PLAN121-1: the provider dot's reachability pre-check MUST use the adapter's effective base_url when the config stores none (OpenAI/OpenRouter/Anthropic store base_url=None) — otherwise the dot is a permanent false-RED despite a working connection. Cache key includes api-key-presence + base_url so a credential change busts a stale result.
- REQ-PLAN121-2: the API key MUST be entered in a masked pop-out (hidden field), never echoed into the chat transcript.
- REQ-PLAN121-3: connection success MUST be proven by a real LLM round-trip whose reply is shown to the user; onboarding/`/provider` finalize only when a non-empty reply is received.

## R69 (Plan 122) — Venv-aware missing-module recovery + transient rate-limit resilience
- REQ-PLAN122-1: a "No module named X" failure (quoted import OR unquoted `python -m` form) MUST be fixed deterministically by installing X into the environment that ran it — when the error names a venv python, install via THAT venv's pip and verify with `pip show` (no LLM).
- REQ-PLAN122-2: a transient HTTP 429/503 from the provider MUST be retried with bounded backoff (honoring Retry-After, capped) before failing; a persistent limit surfaces a clear rate-limit/quota message, never a silent "no usable fix".
- REQ-PLAN122-3: the polyglot backend venv setup SHOULD also install declared build/dev requirement files (requirements-dev/-build, dev-/build-requirements) so build tools land before the prebuild.

## R70 (Plan 123) — Empirical "any repo runs" gate (real bring-up matrix)
- REQ-PLAN123-1: the integration matrix MUST drive repos through the REAL engine (`bring_up_selected_repo`), not stubs, and report PASS/FAIL + the real failure class per row. Local rows MUST be host-safe (pure-Python: temp venv + pip only); toolchain/heavy/GPU rows are vm-only.
- REQ-PLAN123-2: stale bytecode MUST NOT shadow source — `*.pyc`/`__pycache__` untracked + gitignored; no unreachable/dead code (audited via AST sweep).

## R71 (Plan 124) — macOS (Apple Silicon) + AI/ML readiness
- REQ-PLAN124-1: on macOS, a CUDA-pinned PyTorch wheel that can't install MUST be replaced deterministically by the default CPU/MPS wheel (into the active venv); a repo that HARD-requires CUDA MUST stop with an honest "needs an NVIDIA GPU" block (not a loop).
- REQ-PLAN124-2: a gated HuggingFace model MUST ask for HF_TOKEN (block), not hang; a missing Homebrew MUST block with the install instruction (the installer needs the user's password); macOS Python-version install MUST have a pyenv fallback.
- REQ-PLAN124-3: the integration driver MUST pass the real SystemProbe (MPS/arch/RAM); host-safe pure-Python rows (incl. a data/ML app) are run on the Mac and measured.

## R72 (Plan 125) — Framework-agnostic Mac ML + torch-free Apple-GPU smoke
- REQ-PLAN125-1: Mac ML MUST be framework-agnostic — install the repo's DECLARED stack (torch/MLX/TF/JAX/CoreML/ONNX), never assume torch. Add a TensorFlow-on-Apple-Silicon recovery (plain `tensorflow` → `tensorflow-macos`/`tensorflow-metal`) paralleling the torch rule.
- REQ-PLAN125-2: provide a torch-free, low-RAM Apple-Silicon GPU smoke (MLX) as a host-safe live check; heavy ML/training on a small (8GB) Mac MUST stay an honest cloud/VM redirect, not a local OOM.

## R73 (Plan 126) — Polyglot-desktop-sidecar handled as a CLASS, not a repo
- REQ-PLAN126-1: the polyglot-desktop-sidecar archetype (Tauri/Electron + JS + secondary backend + native sidecar build + native compile) MUST be handled by detected SIGNALS only (tauri.conf/src-tauri, electron dep, subdir python manifest, build:sidecar/.spec, Cargo.toml) — never by repo name. No repo-name branch may exist in src/.
- REQ-PLAN126-2: the class MUST be validated by ≥2 distinct real repos reaching a running desktop via the real engine (VM); JustHireMe is one instance, not the target.

## R74 (Plan 127) — Resumable re-runs + disk reclamation (don't fill a small VM)
- REQ-PLAN127-1: install idempotency MUST be completion-aware — skip only when an install genuinely COMPLETED (package-manager marker / venv success sentinel); a half/partial install MUST be cleaned and redone, never skipped-broken or piled on.
- REQ-PLAN127-2: when the target disk is near-full before a heavy build, Duckln MUST reclaim safe space (package-manager caches + stale temp builds, never the repo/user data), not merely warn.

## R75 (Plan 129) — Reasoning-based recovery (not a coded rule per error), all targets, captured thinking
- REQ-PLAN129-1: when the deterministic fast-path finds no fix, recovery MUST investigate with tools and DECIDE (fix/skip/block) via the agent loop — not a blind one-shot — on EVERY target (local/Mac, VM, container, AWS, GCP). It degrades to the existing path when no LLM/harness is available.
- REQ-PLAN129-2: autonomy MUST be keyed on the fix's BLAST RADIUS — repo-scoped reversible fixes auto-apply on every target; system-wide auto-applies only on disposable sandboxes (ask on local Mac); destructive/secret ops always block for a human.
- REQ-PLAN129-3: the recovery agent's reasoning MUST be captured to a persistent, redacted, human-readable thinking log; a declared prebuild script absent on the target MUST no-op (`--if-present`).

## R76 (Plan 132) — Reasoning by default across surfaces (plan/answer/act/recover), bounded + production-complete
- REQ-PLAN132-1: reasoning is ON by default (no flag) for planning + recovery; auto-off only under the unit suite; bounded by per-pass turn/time + a wall-clock timeout + a global per-bring-up budget so it can never hang or run away.
- REQ-PLAN132-2: complex/uncertain repos get a read-only reasoning PLANNING pass (investigate manifests/scripts/toolchain) whose brief enriches planning; clear repos use the deterministic fast-path.
- REQ-PLAN132-3: a too-small model is flagged in /healthcheck (reasoning needs a capable model; degrades to fast-path); verified reasoned fixes are distilled to lessons + telemetry; thinking shown live + logged (redacted).

## R77 (Plan 133) — Live-run fixes: custom resize · resume+cleanup · live-investigate planning · failure dot · logical-thinking.md
- REQ-PLAN133-1: a resource resize MUST let the user type a CUSTOM size (not only y/n).
- REQ-PLAN133-2: a re-run MUST RESUME (skip completed steps) not restart, and stale build scratch (PyInstaller build_cache/.codex-temp-sidecar) MUST be reclaimable so re-runs don't fill the disk.
- REQ-PLAN133-3: the reasoning planning pass MUST investigate the LIVE target (real scripts/build files), and the critic MUST see that brief.
- REQ-PLAN133-4: the existing message dot MUST recolor RED on failure/interrupt (green on done) — same glyph; a maintained, redacted `logical-thinking.md` MUST be created+appended and surfaced as a clickable link on a major reasoning conclusion.
- REQ-PLAN133-5 (F7a): a step cancelled mid-flight (Ctrl-C / cancel / provider switch) MUST surface a tracked "Tool interrupted" line (red dot) in the message window — never silently dropped — leaving completed work in place for resume.
- REQ-PLAN133-6 (F7b): when Duckln edits a repo file (fs.edit/fs.write), the message window MUST show a Claude-style edit card (`✎ Edit <path>  +N -M`); the added/removed counts + a bounded unified diff MUST be computed and carried (TurnEvent.result) so the edit is reviewable. The compact header is shown by default; the full diff is available for the expandable detail view.

## R78 (Plan 134) — Live-VM defects: authoritative target chip · recognized fix auto-applies · plan-time build-tool anticipation
- REQ-PLAN134-1 (F1): the header/target chip MUST reflect the AUTHORITATIVE execution target (config `execution_target` + resolved `vm_name`), never scraped terminal stdout. A VM bring-up shows "local" (local pane + `multipass exec`, the tested `_terminal_connection_context` decision); the stale/sticky generic "local-vm" from output-scraping is removed.
- REQ-PLAN134-2 (F2): a RECOGNIZED (deterministic) repo-scoped, reversible fix MUST auto-apply + verify + resume under the same blast-radius autonomy + mode policy as reasoned recovery (HOOTLWO auto; HITL/HOTL propose; system→ask on local/auto on sandbox; destructive→block) — NOT always pause for `/plan approve`. Shared helper `_auto_apply_recovery_fix` used by both the deterministic and reasoned branches.
- REQ-PLAN134-3 (F3): plan it right the first time — when a Python backend subdir contains a PyInstaller-shaped `*.spec`, the target-side setup MUST install PyInstaller into that venv BEFORE the prebuild, so a `build:sidecar`-style script doesn't fail with "No module named PyInstaller". Signal-keyed (the spec), repo-agnostic, idempotent. Residual undeclared tools are caught by F2's auto-heal — a perfect a-priori plan for arbitrary repos is not generally achievable, so the design is both layers.

## R79 (Plan 135) — Active agent, not a passive 30s timer: route run/build correctly, diagnose from the real error, auto-heal disk-full, surface reasoning, `/plan continue`, stop the spinning timer, terminal-busy queue
- REQ-PLAN135-1 (F1): a run/desktop command (e.g. `npm run tauri dev`) MUST be recognized as a run command and routed to the detached, progress-aware path — not the synchronous 30s timeout. Build/prebuild scripts (`npm run build:*`, `cargo/tauri/make/...`) get a generous build-tier timeout. The readiness wait MUST watch real build progress (log growth / `Compiling N/M`) and only abandon on a crash, an idle stall, or a generous cap — never kill a healthy, progressing build.
- REQ-PLAN135-2 (F2): Duckln MUST diagnose from the REAL terminal completion (`DUCKLN-DONE-<id>:<code>` + actual error text), not a timeout artifact. A heavy build that merely times out with no error retries with more time (active agent), not a hallucinated code fix; a finished build is marked done.
- REQ-PLAN135-3 (F6): a recoverable resource crunch (disk-full / OOM) MUST auto-heal on a disposable sandbox in the autonomous mode — reclaim caches + stale build scratch, resize (host-/cost-bounded), re-run the failed step, and resume — never a `/plan` pause for a recoverable case. A genuinely un-growable host or non-auto mode is an honest ask.
- REQ-PLAN135-4 (F4): the maintained, redacted `logical-thinking.md` MUST mirror the live "Duckln's thinking" stream (not just the final LLM brief) and surface a clickable link on every major reasoning conclusion (planning pass / recovery episode), even when the model returns an empty brief.
- REQ-PLAN135-5 (F3): `/plan continue` MUST resume a paused/amended plan from where it left (skipping completed steps), surviving a mid-setup model/provider switch; it shares the approve/resume path.
- REQ-PLAN135-6 (F7): the "Working… • taking longer than expected" activity indicator MUST clear when a plan pauses/halts/finishes — never keep spinning after Duckln has stopped.
- REQ-PLAN135-7 (F5): when the terminal pane is busy, a user-submitted command MUST be accepted + queued with a "Terminal busy" notice and run automatically when the pane returns to a prompt; the queue is bounded.

## R80 (Plan 136) — Self-correct + reason deeply; never give up on a solvable issue
- REQ-PLAN136-1 (F1): Duckln MUST sanitize an obviously malformed command before running it — a stray leading slash on a shell builtin/tool (`/cd`→`cd`, `/sudo`→`sudo`), wrapping backticks, a `$ ` prompt artifact — so a model hallucination never executes and fails with exit 127. A genuine absolute-path executable (`/usr/bin/python`) MUST be left untouched.
- REQ-PLAN136-2 (F2): a 127 / "command not found" / "No such file or directory" failure caused by a stray-slash builtin MUST deterministically self-correct (re-run the sanitized command, auto-applied since it's self-inflicted) — NEVER "no automatic fix available."
- REQ-PLAN136-3 (F3): the failure-recovery agent MUST investigate the environment before concluding — OS/distro, privileges (`id`, `sudo -n`), access/permissions (`command -v`, `ls -la`), and a least-privilege alternative — validate its own fix command (no stray slash), and may only BLOCK after checking OS + privileges + at least one alternative, stating the exact missing thing.
- REQ-PLAN136-4 (F4): the amendment hard cap MUST count only DISTINCT genuine blockers — the same (failed command → fix command) signature repeated halts with an honest "already tried" message naming the real cause, rather than looping/exhausting the cap; reset on success.

## R81 (Plan 137) — Detect & clean a half-install: reclaim partial state before retrying, and resume from the failed part
- REQ-PLAN137-1 (Fix1): before re-running a step that ended mid-way (interrupt/SIGKILL/OOM/disk-full), Duckln MUST remove that step's marker-less PARTIAL artifact (node_modules without a lock-marker, `.venv` without `.duckln-deps-ok`, stale build scratch) — never a COMPLETED artifact or repo/user data. On a genuine resource crunch it MUST also reclaim caches + apt partial archives; on a generic fix re-run it removes only the partial (keeps shared caches so the retry isn't slowed). A healthy-but-slow build timeout MUST NOT be disturbed.
- REQ-PLAN137-2 (Fix2): on a fresh re-run, the read-only precheck MUST detect a half-install (marker-less node_modules/`.venv`, stale build scratch, broken dpkg) and the planner MUST insert an EXPLICIT, visible cleanup step that removes the partial (and reconfigures dpkg) BEFORE the matching install; execution then resumes from the failed part (completed steps skip via `_done_steps`).

## R82 (Plan 138) — Stop the self-inflicted cascade; read the real error; search the web when stuck; clickable reasoning log
- REQ-PLAN138-1 (F1): Duckln MUST emit a VALID detached launch — env applied via `env VAR=val … <cmd>` (never a bare `VAR=val` prefix that `nohup` execs as a program → exit 127). A self-inflicted broken command must not run and then be misdiagnosed.
- REQ-PLAN138-2 (F2): recovery MUST diagnose from the actual error text, not a token match — an apt dependency conflict / "held broken packages" is resolved as a conflict (never "install a compiler"); the compiler rule fires only on a genuine `gyp ERR!`/compile error; an implausible "install build-essential" fix for an apt conflict MUST be rejected.
- REQ-PLAN138-3 (F3): the maintained `logical-thinking.md` MUST be surfaced as a CLICKABLE link (OSC-8 in the TUI, markdown in the panel) that OPENS the file, after plan creation, after each step success, after each failure, and before the final summary; the executor's thinking is mirrored into the file.
- REQ-PLAN138-4 (F4): when stuck, if internet is ON Duckln MUST announce "🌐 Let me check the web…" and search (`web.search`/`gather_repair_fix`); if OFF it MUST ask the user to `/internet on`. `web.search` stays in the recovery agent's tool set, gated by `is_internet_enabled`.

## R83 (Plan 139) — Land on the streamed app, not the noVNC Connect page
- REQ-PLAN139-1: the noVNC stream URL MUST auto-connect (`autoconnect=true`), scale to the window (`resize=scale`), and re-attach on a drop (`reconnect=true`), so opening the link lands directly on the streamed desktop app instead of noVNC's "Connect" landing page; the host rewrite (localhost→VM IP) is preserved.

## R84 (Plan 141) — Visible streamed app (window manager) + openable reasoning log
- REQ-PLAN141-1: the headless desktop-streaming stack MUST start a window manager (fluxbox) on the virtual display so ANY GUI app's window is mapped/visible (not a black noVNC screen). Universal for all desktop toolkits — fluxbox is in the BASE stream packages, started after Xvfb, idempotent.
- REQ-PLAN141-2: the maintained `logical-thinking.md` reasoning log MUST be openable from inside the TUI — clicking the `file://` reasoning link opens it (Duckln reads the Rich link under the cursor since Textual captures the terminal click), and a `/reasoning` command opens the latest log as a reliable fallback.

## R85 (Plan 142) — A world-class, self-reliant reasoning FLOOR (perceive → ground → think → keep → extend)
- REQ-PLAN142-1 (F1 perceive): the agent's read-only investigation commands (ls, cat, head, tail, grep, find, file, stat, uname, id, whoami, `cat /etc/os-release`, `command -v`, version checks, `npm ls`, `dpkg -l/--audit`, `apt-cache policy`, `git status/log/...`, `sudo -n true`, df/du/free) MUST classify as S0 so `shell.probe` runs them; mutating commands (incl. `find -delete`/`-exec`, installs, rm/mv) MUST stay S2/S3 and `match_blocked_command` runs first.
- REQ-PLAN142-2 (F2 ground): on a remote target the recovery agent MUST receive the runtime/target repo dir (so its fs/shell tools resolve to the actual VM/container/cloud repo, not the local path).
- REQ-PLAN142-3 (F3 think): the reasoning loop MUST have a generous-but-bounded budget (≥12 turns / ≥240s, with the wall-clock + per-bring-up pass caps intact) and a non-keyhole context window (observations ≥8k chars, state ≥12k), receiving the FULL error.
- REQ-PLAN142-4 (F4 keep+deepen): the agent's reasoning MUST be kept (tolerant parse — infer a confident decision from prose, else None so the deterministic/web fallback runs), persisted as a real chain (what it inspected + found → root cause → fix → why) in logical-thinking.md, and elicited by a prompt demanding that chain.
- REQ-PLAN142-5 (F5 extend): when blocked purely for lack of a capability, the agent MUST name the tool/MCP it would need (pointing to `/mcp`); full MCP-into-agent bridging + self-authoring of tool configs is a scoped follow-up.

## R86 (Plan 143) — Actually resolve a full VM disk (grow guest FS, reclaim hogs, ask before resize)
- REQ-PLAN143-1 (F1): a multipass disk resize MUST grow the GUEST filesystem (growpart + resize2fs inside the VM, auto-detecting the root device) after `multipass set disk`+start — the virtual disk alone is unusable, leaving `df` full.
- REQ-PLAN143-2 (F2): a disk-FULL crunch MUST aggressively reclaim the real hogs (Rust `target/`, cargo registry, `~/.cache`, stale `node_modules`) — rebuilt/re-fetched, never the repo source/user data; routine reclaim stays conservative.
- REQ-PLAN143-3 (F3): after reclaim (+ approved resize), Duckln MUST re-probe free disk and only retry when there's enough; if still tight it pauses with the REAL free/needed numbers + "recreate the VM larger via `/vm`", never a blind `/plan continue` loop; the disk is resolved BEFORE re-attempting any fix.
- REQ-PLAN143-4 (F4): Duckln MUST NEVER auto-resize — reclaim is automatic (safe) but a resize ALWAYS asks the user (real approve + custom GB), and the prompt MUST suggest the minimum required size (and recommended).

## R87 (Plan 144) — Reason through a multi-stage failure (changed error = progress; don't delete the venv the fix needs; re-run a deleted-artifact setup step)
- REQ-PLAN144-1 (F1): when an applied fix makes the failed step exit nonzero with a DIFFERENT error (a later build stage surfaced), Duckln MUST treat it as PROGRESS and chain the next known fix (bounded by max-stages), not declare "fix did not verify". An UNCHANGED error (same/no/already-tried next fix) is an honest stop.
- REQ-PLAN144-2 (F2): a generic fix re-run MUST NOT delete a marker-less venv/node_modules (the fix often installs INTO that venv); only a genuine resource crunch may. The MISSING_VENV recovery fix MUST build a COMPLETE venv — stamp `.duckln-deps-ok` and install the spec-detected PyInstaller — so it survives later cleanup and resolves in one pass.
- REQ-PLAN144-3 (F3): on resume, a marker-guarded setup step (the venv setup keyed on `.duckln-deps-ok`) MUST be re-run, never permanently skipped via done-steps — it no-ops when complete and rebuilds the venv if it was deleted.

## R88 (Plan 145) — Evidence-backed, supervisor-challenged reasoning + deep logging (model is the reasoner; the fast-path may only hint/verify, never bypass thinking)
- REQ-PLAN145-2 (F2): a recovery conclusion MUST carry cited EVIDENCE (files/lines read, probe outputs, the exact error) — parsed from the model JSON or fallen back to the tool-use chain; a BLOCK/SKIP must prove itself; the recovery prompt requires it. [DONE]
- REQ-PLAN145-3 (F3): a skeptical SUPERVISOR MUST review every recovery conclusion before it's trusted — ACCEPT or CHALLENGE→re-investigate (bounded); an empirically verified fix short-circuits the debate; an unproven BLOCK/SKIP is downgraded to an honest EXHAUSTED, never rubber-stamped. [DONE]
- REQ-PLAN145-5 (F5): every recovery outcome (deterministic/auto-apply/block/skip/amendment) MUST write a structured episode (symptom→evidence→fix→outcome) to logical-thinking.md; the pre-diagnosis shallow stub is removed so episodes never dead-end at "reading the error…". [DONE]
- REQ-PLAN145-1 (F1): the deterministic matcher MUST be a HINT to the model, never a decider that bypasses reasoning/supervisor; no offline fallback (no model/quota → honest stop). In production (reasoning enabled) the matcher decides nothing — the agent reasons with the hint + supervisor; the legacy deterministic decider runs ONLY under the unit suite (reasoning disabled) for deterministic tests; a deterministic BLOCK is kept as an honest user-ask guardrail. [DONE]
- REQ-PLAN145-0 (F0): the reasoning planning investigation is the DEFAULT for any non-trivial repo (a build surface / ≥3 files / polyglot / low-confidence) — reads declared script bodies + the files they call to anticipate the full setup chain upfront. [DONE]
- REQ-PLAN145-6 (F6): verified fixes (deterministic AND model-reasoned/supervisor-accepted) persist as common lessons + skills for reuse on similar repos (existing learning loop, now covering the reasoned path); "show your reasoning" surfaces the thinking log. [DONE; deep Q&A-over-log is partial — see analysis] 
- REQ-PLAN145-7 (F7): natural-language intent is the primary interface — a plain message classifies to ask/do/reasoning/recheck (and the supervisor routes bring-up/settings/etc.) with NO slash required; reasoning/recheck checked first so they aren't misread; the "Usage: /ask"/"/do" prompts are replaced with natural language. [DONE]

## R89 (Plan 146) — Provision the runtime + verify it ACTUALLY works + self-extend (close 145 partials)
- REQ-PLAN146-A1: the model-backed supervisor + the deterministic-match-as-hint are proven by tests (model double for accept/challenge; the hint is advisory). [DONE]
- REQ-PLAN146-A2: a question about Duckln's OWN solution/reasoning surfaces the redacted thinking-log + active plan to the Q&A agent so it can answer/defend it. [DONE]
- REQ-PLAN146-B1: a repo's `.env` is POPULATED with safe dev defaults (service URLs + random secrets; externals excluded); services WAIT for readiness before migrations; a declared seed runs. [DONE — helpers + the .env step; full executor wiring of readiness/seed into every service path is partial]
- REQ-PLAN146-B2: "running"/"verified" prefers a REAL outcome check (the repo's own tests / an HTTP-200 probe), not a `--help`/presence no-op. [DONE — builders + smoke preference; gating the live launch on the probe is partial]
- REQ-PLAN146-B3: the eval matrix has service-backed rows whose pass relies on the B2 outcome check. [DONE — rows added; measured pass-rate is the VM run]
- REQ-PLAN146-B4: a supervisor-confirmed capability gap is extractable ("need tool/MCP X") and `/tools add` drafts a registerable config. [DONE — detect+draft; live confirm→register is the follow-on]
- REQ-PLAN146-C1: a backend+frontend split is detected (separate runnable subdirs) vs a self-managed concurrent script. [DONE — detection; concurrent launch/wire wiring is partial]
- REQ-PLAN146-C2: a repo SECRET key (no safe default) is identified for masked intake and never logged (redaction holds). [DONE — classifier + redaction; the masked TUI intake→inject wiring is partial]
- REQ-PLAN146-C3: running a fresh untrusted repo's lifecycle scripts on local surfaces a sandbox-recommendation posture. [DONE — notice helper; the gate wiring into the executor is partial]
- REQ-PLAN146-C4: a heavy repo is detected at plan time to right-size a fresh target up front via estimate_requirement. [DONE — detection; right-size-on-create wiring is partial]
- REQ-PLAN146-C5: services/PIDs/tunnels Duckln starts are torn down on stop/reject without touching pre-existing services. [DONE — teardown builder; the start-tracking + teardown-trigger wiring is partial]

## R90 (Plan 147) — Duckln was BLIND, not dumb: the recovery agent's fs tools couldn't read a `~/…` repo on the VM (the tilde was shell-quoted, never expanded), so it gathered no evidence and honest-stopped
- REQ-PLAN147-1 (F1): the remote fs tools MUST expand a leading `~`→`"${HOME}"` (mirroring the executor wrapper) and the local fs tools MUST `expanduser`, so `fs.read_file/list_dir/search/glob` actually read the repo on every target — the prerequisite for any evidence-based reasoning. [DONE]
- REQ-PLAN147-2 (F2): the thinking log MUST key an episode by the repo NAME (consistent across the recovery-agent and halt loggers), not the dir path in one and the clone URL in the other. [DONE]
- REQ-PLAN147-3 (F3): when every tool observation is a failure, the agent MUST report a tool/path-ACCESS problem ("couldn't ACCESS … not a missing repo"), not "make sure the repo is cloned locally". [DONE]
- REQ-PLAN147-4 (F4): the reasoning prompt MUST demand a 5-part chain (symptom → inspected → root cause+mechanism → fix+why → confidence) with a one-shot exemplar; a weak-model note (`Note — For depth reasoning, configure a stronger model.`) shows ONLY when the configured model is not capable (a strong model shows nothing). Reasoning DEPTH still scales with the model. [DONE]

## R95 (Plan 153) — AI/ML: accelerator + environment awareness at the initial check [DONE: cores + plan-assembly wiring; live GPU/conda VM run = remaining]
- REQ-PLAN153-A1: the resource probe senses the TARGET's GPU (nvidia-smi → `ResourceSnapshot.gpu_present/gpu_name/vram_mb/cuda_version`, `.accelerator`); not host-only. [DONE]
- REQ-PLAN153-A2: `diagnostics.framework_install_command(framework, accelerator, cuda_version)` installs the torch/jax/TF build MATCHING the accelerator (cuda wheel `whl/cu<NNN>` / cpu wheel / mps default + tensorflow-macos); preserves a pinned version. [DONE]
- REQ-PLAN153-A3: `_repo_needs_gpu` detects a GPU-required repo (deps like bitsandbytes/flash-attn, README "requires CUDA", diffusion family) → caller routes to GPU cloud / honest-block on a no-accelerator target. [DONE]
- REQ-PLAN153-B: a conda repo (`environment.yml`) is set up via mamba/conda (`_conda_env_setup_steps`/`_conda_setup_command` — installs Miniforge if absent, idempotent), pip-venv stays the default. [DONE — env-create wired into plan assembly; run-through-`conda run -n <env>` (B2) = remaining]
- REQ-PLAN153-WIRING: `_generate_plan_for_bringup_impl` now (a) inserts the conda env-create step before setup when `environment.yml` is present, (b) probes the target accelerator (remote nvidia-smi / local MPS-or-CPU) and surfaces it in the thinking log, (c) appends the accelerator-matched framework-wheel install (`_detect_dl_frameworks`+`_ml_framework_install_steps`) after the generic deps install, (d) surfaces an honest GPU-route note when `_repo_needs_gpu` and the target has no accelerator. [DONE — test_plan153::MlPlanAssemblyWiring]
- REQ-PLAN153 (remaining): B2 run/verify a conda repo THROUGH `conda run -n <env>` (run-command path); live GPU/conda VM end-to-end.

## R96 (Plan 154) — Apple MLX / Metal first-class on the Mac (native-host execution) [DONE: deterministic + wired; live Mac run = remaining]
- Premise (verified): Apple `container`/`containerization` run LINUX containers and CANNOT pass Metal/MLX to the guest (apple/containerization#46); the ONLY MLX path is a native macOS process — Duckln's `local` target (`_wrap_command_for_execution_target` runs `local` unwrapped). So NO new Apple-container target is added (user-chosen scope).
- REQ-PLAN154-F1: `_detect_metal_frameworks(req, pyproject)` detects the declared Apple-Metal frameworks (`mlx`/`mlx-lm`/`mlx-vlm`/`mlx-data`, `jax-metal`, `tensorflow-metal`); signal-keyed; `mlx-lm` not shadowed by `mlx`. [DONE]
- REQ-PLAN154-F2: on a LOCAL Apple-Silicon target the plan surfaces the accelerator as "Apple Metal (MLX)" (`_metal_accelerator_label`) when MLX is declared, else the MPS line. [DONE — wired in `_generate_plan_for_bringup_impl`]
- REQ-PLAN154-F3: `framework_install_command("mlx"/"mlx-lm"/…)` → `pip install mlx…` (Metal-only, accelerator-agnostic, version-pin preserved); the MLX install step is appended ONLY on a local Apple-Silicon target. [DONE]
- REQ-PLAN154-F4: `_repo_needs_apple_metal` + `_apple_metal_routing_note` warn + recommend `local` (`/target local`) when an MLX/Metal repo is aimed at a Linux target (vm/docker/aws/gcp); SOFT (recommend, never block). Symmetric inverse of `_repo_needs_gpu` (CUDA→cloud). [DONE — test_plan154]
- REQ-PLAN154 (remaining): live Mac run — an `mlx`/`mlx-lm` repo on `local` surfaces "Apple Metal (MLX)", installs mlx, runs on the Apple GPU; the `mlx-smoke` row confirms end-to-end.

## R97 (Plan 155) — Production hardening of VERIFIED debt [DONE: deterministic + wired; live = remaining]
- REQ-PLAN155-F1: ONE canonical `execution_targets.py` (constants + `normalize_execution_target` mapping the `docker` alias → `container` + membership sets). `_wrap_command_for_execution_target` normalizes its input AND returns `None` for an unrecognized non-local target — fixing the silent "docker"→HOST fall-through. `repo_bringup`/`harness.tools`/`recovery`/`resource_manager` import the canonical sets. [DONE — test_plan155::CanonicalExecutionTargets]
- REQ-PLAN155-F2: a conda repo (`environment.yml`) RUNS through `conda run -n <env>` (`_conda_run_prefix_for_repo`, wired into the run step) — `_conda_run_prefix` is no longer dead; pip-venv stays default. [DONE — test_plan155::CondaRunThrough]
- REQ-PLAN155-F3: `assess_existing_setup`/`build_fix_only_plan_steps` are CALLED in `_generate_plan_for_bringup_impl` for an already-cloned repo — surface health and PREPEND minimal repair steps when unhealthy (not a full re-setup). [DONE]
- REQ-PLAN155-bug: codegen tuples are `(title, command, verification|None)` — the Plan-153 conda/ml steps wrongly put a SAFETY class in the 3rd slot (would run `S2` as a verify command); now stripped to `None` at every codegen insertion (conda/ml/mlx/fix-only). [DONE]
- REQ-PLAN155-F4: the delete-by-`@name` flow is reachable — `main._handle_unified_delete_free_text` routes a "delete @container/cloud/repo" message through `delete_controls` (one confirm + 7-day audit, clears the active ref); `textual_ui` adds an `@`-mention overlay mirroring the slash overlay. VM delete unchanged. [DONE — test_plan155 + test_plan150]
- REQ-PLAN155-F5: `.DS_Store` untracked + `.gitignore` hardened (`.DS_Store`, build artifacts); `*.pyc`/`__pycache__` stay ignored. [DONE]
- REQ-PLAN155-F7: the supervisor-reviewer swallow in `recovery.supervise_recovery_conclusion` logs (DEBUG, redacted) before falling through — no longer silent. [DONE — test_plan155::RecoveryReviewerNotSilent]
- REQ-PLAN155-F8: `/tools add`//`/mcp` detect→draft→confirm→REGISTER — a confirmed extension persists as `registered_tool`/`registered_mcp` and `tool_registry.tool_registry_entries(config_dir)` merges it into the agent-visible manifest (no longer proposal-only). [DONE — test_plan155::RegisteredExtensionVisible]
- REQ-PLAN155 (remaining): live — a "docker" target runs IN the container; a conda repo runs through its env; `delete @name` works from the chat + `@` popup; a registered MCP tool is callable. Deferred (separate effort): the `repo_bringup.py` ~12.5k-line monolith refactor.

## R98 (Plan 156) — Reasoning-first production engine (any repo, Claude-shaped) [PHASED: P1–P3 done; P4–P5 = remaining]
- Principle: Quality = Architecture (floor) × Model (ceiling). Floor is Claude-shaped (think→act→observe, evidence-first, never-fabricate); depth scales with the model. Deterministic NEVER decides a plan alone — it's a HINT provider to the LLM + the S0–S4/verify/critic safety floor.
- REQ-PLAN156-P1: behavior-based capability probe `recovery.probe_model_reasoning_capability(llm_client, model_id, config_dir)` → `capable`/`weak`/`unknown` by whether the model returns valid structured JSON; cached per model id; `model_is_reasoning_capable`/`weak_model_reasoning_note` prefer the cached verdict over the name heuristic. Provider-agnostic (Ollama / OpenRouter-free judged by behavior). Wired (lazy + cached) at the planning seam. [DONE — test_plan156::CapabilityProbe]
- REQ-PLAN156-P2: reasoning is the CORE — a NON-TRIVIAL repo (`_planning_is_nontrivial`: build surface / unknown family / ≥3 files) with **no reachable model** → HARD honest-stop (`MODEL_UNREACHABLE_MESSAGE` + blocked result), NEVER a deterministic-only plan. A configured-but-unreachable model is still caught by the critic's `model_unreachable`. [DONE — test_plan156::PlanningRequiresReasoning]
- REQ-PLAN156-P3: a `generalist` sub-agent descriptor + `agent/playbooks/generic.md` handles ANY unmatched stack (Java/Maven/Gradle, Scala, odd native) by reading its build files + reasoning; `select_subagent_descriptor` routes an unmatched non-Python repo to `generalist` (not a Python mislabel); a Python-signal repo still gets `python_setup`. [DONE — test_plan156::GeneralistRouting]
- REQ-PLAN156 logging: the capability probe verdict + the planning honest-stop are appended to `logical-thinking.md` (reasoning captured). [DONE]
- REQ-PLAN156-P4: `harness/plan_supervisor.run_plan_supervisor` (supervisor→inspector→planner→critic) is now CALLED live in `_generate_plan_for_bringup_impl` for a non-trivial repo on a reachable model — it runs the pipeline, emits the agent trace (`/agents trace`), and its critic reasoning is surfaced + logged to logical-thinking.md. Bounded + best-effort; the deterministic bring-up plan stays the structural base (no regression). [DONE — test_plan156::MultiAgentWiring; live model = end-to-end proof]
- REQ-PLAN156-P5: `harness/recovery_flow.run_multi_agent_recovery` (recovery_coordinator → investigate/search/memory in parallel) is now CALLED live in `recover_failed_step_with_agent` — its consolidated findings feed as a HINT into the structured single-agent recovery (which still produces the RecoveryReport); captured to logical-thinking.md. Bounded + best-effort + graceful when the spec is absent; skipped for injected test runners. [DONE — test_plan156::MultiAgentWiring; live model = end-to-end proof]
- REQ-PLAN156 (remaining): live-model end-to-end proof — the multi-agent pipelines produce useful plans/fixes on a real model; quality scales with the model (the wiring + gating + fallback are deterministic + green).

## R99 (Plan 157) — Spec-driven agent harness: agent `.md` specs are the single source of truth [DONE: P1–P6; live-model end-to-end = remaining]
- Principle: every LLM agent's behavior is authored in its `.md` spec (real YAML frontmatter + contracts + body), not in Python prompt constants. Editing behavior = editing a spec. Quality still scales with the model (Plan-156 probe + honest-stop intact).
- REQ-PLAN157-P1: `harness/agent_def.py` parses frontmatter with PyYAML (`yaml.safe_load` — folded scalars + nested maps), `AgentDefinition` gains `version`/`input_contract`/`output_contract`/`max_contract_retries(=2)`, `SUPPORTED_SPEC_VERSION="2.0"` hard-rejects a newer spec, and `AgentRegistry.from_directory` is ROBUST (skip+log a malformed OPTIONAL spec; raise on a missing REQUIRED agent via `REQUIRED_AGENTS`). pyyaml added to pyproject. [DONE — test_harness_agent_def, test_plan157]
- REQ-PLAN157-P2: `validate_input_contract` + `validate_agent_output` + `loop.run_spec` enforce contracts — input keys checked before the call; output validated against the contract with a bounded re-ask up to `max_contract_retries` (schema + violation in the re-ask), then honest-fail; `_compose_system_prompt` injects the output_contract. [DONE — test_plan157::InputContract/OutputContract/RunSpecReAsk]
- REQ-PLAN157-P3: the planning agents are spec-driven — `_propose_candidates`/`_critique_and_order`/`_collect_clarifications`/`critic_review`/`attribute_failure` source their prompt from `planner/critic/clarifier/verdict/attributor.md` via `_spec_prompt(...)`; the five `SYSTEM_PROMPT_PLAN_*` constants are RETIRED from prompts.py. Naming resolved (verified in code): `supervisor` orchestrates (no LLM call), `verdict` is the separate approve/revise/block call → its own `verdict.md`. [DONE — test_plan157::PlanningAgentsAreSpecDriven; 100 plan-mode tests green]
- REQ-PLAN157-P4: recovery prompts are spec-driven — `_RECOVERY_SYSTEM`→`recovery_agent.md`, `_SUPERVISOR_SYSTEM`→`recovery_supervisor.md`, `_PLANNING_SYSTEM`→`planning_investigator.md` (a planning-investigation prompt, NOT folded into the inspector). The three constants are DELETED from recovery.py; sourced via `_recovery_spec_prompt(...)`; the 6 tests that asserted the constants now read the specs. [DONE — test_plan157::AllSpecsOnReferenceFormat]
- REQ-PLAN157-P5: the PRIMARY router (`ConversationSupervisor`) stays DETERMINISTIC code (audited); the provider-backed conversational reply's STATIC persona is migrated to `conversation_agent.md` (sourced via `_conversation_persona_base()`, with a literal fallback) while the router appends live runtime context. `agents/README.md` documents why the router is code. [DONE]
- REQ-PLAN157-P6: all 18 shipped specs declare `version: "2.0"`, parse with PyYAML, and pass `validate_against_tool_registry`; `agents/README.md` documents the format, the `SUPPORTED_SPEC_VERSION` gate, `REQUIRED_AGENTS`, the live wiring, and the router rationale. [DONE]
- REQ-PLAN157 (remaining): live-model proof — editing a spec changes behavior end-to-end; contract violations are caught + re-asked on a real model. Deterministic mechanics (parse/validate/gate/source) are green.

## R101 (Plan 159) — A read-timeout ≠ "unreachable"; lean planner prompt + just-in-time stack detail [DONE]
- Symptom: a CONNECTED Ollama model (`qwen2.5:1.5b`, produced tokens) was reported "not able to connect" — a false unreachable caused by a read/response TIMEOUT (slow model) being misclassified as a connection failure.
- REQ-PLAN159-F1: `plan_mode._model_connection_error` walks the `__cause__`/`__context__` chain and is httpx-type-aware: `httpx.ReadTimeout`/`PoolTimeout`/`WriteTimeout` → REACHABLE (False); `httpx.ConnectError`/`ConnectTimeout` (+ builtin ConnectionError/socket.timeout/URLError + connect-only text needles) → unreachable; the generic "timed out"/"timeout"/"11434" needles are REMOVED. So a reachable-but-slow model is never called "unreachable". [DONE — test_plan159::ConnectionErrorClassifier, test_model_connection]
- REQ-PLAN159-F2: with F1, `critic_review` does not call a read-timeout "unreachable". [SUPERSEDED by Plan 160: a read-timeout now RETRIES then HONEST-STOPS — never a deterministic verdict.]
- REQ-PLAN159-F3: `planner.md` slimmed from ~8.3k → ~4.7k tokens — the Django/Monorepo/Docker examples + the 25-row inference table moved to `agents/reference/planner_stack_guides.md` (anchored `<!-- ref:KEY -->`, NOT loaded as an agent); `_propose_candidates` appends ONLY the stack-matching section just-in-time via `stack_reference_snippet(repo_family, detected_files)`. Node + AI/ML stay as the two anchor examples in the core. [DONE — test_plan159::LeanPromptAndJitDetail]
- REQ-PLAN159 (remaining): live-model proof — Ollama `qwen2.5:1.5b` no longer falsely "unreachable"; the leaner prompt is faster/cheaper. Deterministic mechanics are green.

## R102 (Plan 160) — The LLM is the decision-maker (never deterministic); the USER owns destructive steps [DONE: A/B/C/D1/D2; live-model = remaining]
- Principle (user): never decide deterministically when the LLM can't — the LLM decides, else honest-stop; and the HUMAN is the final authority on destructive ops. Supersedes the Plan-145/156 "deterministic floor decides/falls-back" stance.
- REQ-PLAN160-B: `critic_review` — a CONNECT failure → `model_unreachable` honest-stop; a reachable model that times out or returns garbage AFTER its retry budget → `model_unresponsive` honest-stop ("reachable but too slow/weak — retry or stronger model"), NEVER a deterministic approve. `llm_client=None` still returns the pure structural verdict (the caller gates). Demoted `_deterministic_supervisor_review` to a flag (empty→block) — it never approves in the LLM's place. [DONE — test_plan160::PhaseB, test_plan_mode_critic, test_model_connection]
- REQ-PLAN160-C: the bring-up honest-stops when the LLM critic returns `model_unreachable`/`model_unresponsive` (gated by reasoning in prod; `reasoning_enabled()` off under unittest keeps the deterministic path green) — so a deterministic-only plan never ships without the LLM's sign-off; the deterministic backbone is the candidate/hint the LLM decides over. [DONE — wired in `_generate_plan_for_bringup_impl`, gate, amendment]
- REQ-PLAN160-A: a DESTRUCTIVE (S4) step is no longer auto-blocked — `_step_allowed_in_mode` asks the USER (binary `approve` fallback; `make_destructive_decider` adds Yes/No/Approve-all-this-session with session memory). Even an approved plan asks for a destructive step. Unattended (no approver) → never runs destructive. [DONE — test_plan160::PhaseA, test_plan_mode_hootlwo, test_plan_step_authorization]
- REQ-PLAN160-D1: `recovery.effective_contract_retries` sets the retry budget from the cached probe verdict — full for `capable`, reduced (≤1) for `weak` — so a weak model honest-stops fast; used by `critic_review`'s retry loop. [DONE — test_plan160::D1]
- REQ-PLAN160-D2: `recovery.record_capability_observation` — a probe-`weak` model is NOT promoted to `capable` on one valid pass; it upgrades only after N consecutive valid deliveries (failure resets the streak). [DONE — test_plan160::D2]
- REQ-PLAN160 (remaining): live-model proof — on a real model the LLM decides every plan + a destructive step prompts Yes/No/Approve-all; the deterministic mechanics (honest-stop routing, retry budget, N-of-M, destructive gate) are green. Honest dependency: with no/weak model the bring-up honest-stops (produces nothing) by design.

## R103 (Plan 161) — `/loop` Claude-Code parity: forced expiry, LLM-driven stub types, bounded diagnose-fix-retest — inside the existing Desktop-tier scheduler + S0/S1 + tool-scope floor [DONE: PR1/PR2/PR3 mechanics; live-model = remaining]
- Principle (`.sdc/loop_doc.md`, grounded in Anthropic's documented scheduling patterns): a forgotten loop must hit a forced review point; the 4 stub types must reason (observe→decide→report), not silently return `ok`; a problem must be worked (try→retest→repeat) within a bound. Reconciled to Duckln's real APIs (frozen records, instance-method store, `run_spec`, real read-only tools); the safety floor (APScheduler+jobstore, per-type tool scope, S0/S1 ceiling, `_safe_loop_command`, fail-and-continue, in-process) is unchanged.
- REQ-PLAN161-PR1 (expiry): `LoopRecord`/`loops` gain `expires_at`/`expiry_days` (ALTER-TABLE migration for existing DBs); per-type defaults (ci 7 / cost 90 / else 30, user-overridable via a 4th creation question); `_run_scheduled_loop` deactivates + unschedules + notifies a loop past `expires_at` and never runs the expired cycle; `/loops` + `/loop history` surface days-remaining; `/loop edit` renews + reactivates. [DONE — test_plan161::PR1Expiry, test_main]
- REQ-PLAN161-PR2 (LLM-driven): `repo_watcher`/`model_quality`/`ci_monitor`/`custom` run `harness/agents/loop_executor.md` via `run_spec` scoped to REAL per-type read-only tools; `cost_monitor`/`health_monitor` stay deterministic. With NO usable model an LLM-driven cycle reports `attention_needed` + notifies — NEVER a fake `ok` (the doc's false-confidence concern). [DONE — test_plan161::PR2LLMDriven]
- REQ-PLAN161-PR3 (fix-retest): a fast-path problem + `auto_fix` escalates into a bounded diagnose→act→retest loop (≤`max_fix_attempts`, default 3) shared by ALL types (deterministic types escalate to the LLM on fixed-logic failure); `resolved`→`fixed`+notify, exhausted→`failed`+notify with full `attempts` history; each attempt sees `prior_attempts`; `auto_fix=False`→notify-only single pass; `_run_scheduled_loop` honors the cycle's `should_notify`. [DONE — test_plan161::PR3FixRetest]
- REQ-PLAN161 (remaining): live-model proof — on a real model an LLM-driven cycle reasons over real read-only tools and a failing auto-fix loop tries→retests→honest-stops; the deterministic mechanics (expiry, routing, fix-retest bound, no-fake-ok) are green. `_safe_loop_command` extended to block force-push/drop, backing the spec's "never irreversible" rule.

## R117 (Plan 176) — The aware-interaction capability is now a SKILL (.md, detailed + one-shot examples) + a deterministic FLOOR + an LLM VOICE, and the helper is actually WIRED (it was half-built with inline strings) [DONE]
- The user asked: is the awareness work deterministic+LLM, is it a skill, is there a .md? Verified: it was mostly deterministic, had NO skill .md, and `build_failure_proposal`/`propose_and_confirm` were UNWIRED (the G3 messaging used inline strings). Corrected to match the `resource_management` pattern.
- REQ-PLAN176-F1: NEW SKILL `src/agent/playbooks/aware_interaction.md` — the discipline (comprehend → explain situation → recommend best approach + why → offer Yes/No/write-your-own → act; never a dumb dead-end), DETAILED with 3 worked ONE-SHOT examples (build-failure, OOM, ambiguous) so even the weak `qwen2.5:1.5b` imitates the shape. Listed in capabilities.md (Skills, 15 playbooks). [DONE — test_plan176::F1]
- REQ-PLAN176-F2: `interaction.build_failure_proposal` is deterministic FLOOR (factual situation + the `match_deterministic_fix` candidate) + LLM VOICE (loaded with the skill + the facts, voices situation/recommendation/why in persona); the floor is used verbatim with no/weak model. The LLM owns the voice over the facts (Plan 160). [DONE — test_plan176::F2]
- REQ-PLAN176-F3: the amendment-pause now CALLS `build_failure_proposal` (not an inline string) — the primitive is genuinely wired. [DONE — test_plan176::F3]
- REQ-PLAN176 (convention): EVERY playbook/skill carries one-shot worked examples — `resource_management.md` backfilled with a disk-full + an OOM example. [DONE — test_plan176::BackfillAndCapabilities]
- Honest: `propose_and_confirm` stays the deterministic TOOL/floor (the action); `aware_interaction.md` is the SKILL (the WHAT); the LLM voices it over the facts. Explanation depth scales with the model; the floor + the 3-way prompt work model-free.

## R116 (Plan 175) — Production audit: aware-not-dumb interaction primitive + AI/ML coverage (ONNX, honest GPU) landed; larger items scoped as next development [PARTIAL — phased]
- A whole-codebase audit (3 Explore passes, findings vetted by hand — false positives dropped). The first, safe, tested slice landed; the larger/riskier items are scoped as next development to keep the 2324-test core green.
- REQ-PLAN175-G1: `src/duckln/interaction.py` `propose_and_confirm(situation, recommendation, why) → ACCEPT/REJECT/CUSTOM` — the human-in-the-loop "explain → Yes / No / write-your-own" primitive, reusing `select`/`approve`/`text_prompt`. [DONE — test_plan175::G1]
- REQ-PLAN175-G2: `build_failure_proposal` — comprehends a failed step into (situation, recommendation, why); deterministic `match_deterministic_fix` recommendation, optional LLM, honest fallback (never a dumb dead-end). [DONE — test_plan175::G2]
- REQ-PLAN175-A1: ONNX Runtime detected (`_detect_dl_frameworks`) + accelerator-matched wheel (`framework_install_command`: cuda→onnxruntime-gpu, else onnxruntime). [DONE — test_plan175::A1]
- REQ-PLAN175-A2: the GPU-needed-but-absent message is HONEST about AMD ROCm / Intel (auto-configures CUDA/MPS/CPU only) — no silent CPU wheel for an AMD user. [DONE — test_plan175::A2]
- REQ-PLAN175-A3: the JAX CUDA wheel is matched to the sensed CUDA major (`jax[cuda11_pip]` vs `jax[cuda12]`); TF already uses the auto-matching `tensorflow[and-cuda]`. [DONE — test_plan175::A3]
- REQ-PLAN175-G3 (messaging): the amendment-pause and the no-fix honest-stop are AWARE proposals — they explain the situation + the best approach + why, surface WHAT was investigated (F1), and offer apply / skip / "tell me what to do instead" (resolved via `/plan continue`/`/plan reject` or the follow-up flow). Additive/display-safe; the synchronous apply-on-accept is deferred (autonomy rewire). [DONE — test_plan175::G3F1AwareRecovery]
- REQ-PLAN175-F1: the honest-stop surfaces "I checked: <evidence>" so the user sees Duckln actually investigated. [DONE]
- REQ-PLAN175-C1: `plan_lifecycle.py` documented DORMANT-BY-DESIGN (kept as the canonical invariant contract, referenced by plan-mode invariant tests; live flow uses `workflow_state`) — NOT deleted. [DONE — test_plan175::C1]
- REQ-PLAN175-B2: VERIFIED already handled — `_db_provision_steps` starts the service then inserts a "Wait for <engine> to be ready" step BEFORE migrations (Plan 146 B1); the audit flag was a false positive. [VERIFIED — no change]
- REQ-PLAN175 (NEXT DEVELOPMENT, scoped + prioritised, production-grade approach): **G3-synchronous** — apply-on-accept at the failure (replace the `/plan continue` round-trip with an inline propose→apply+verify) needs a mode-gated rewire of `_attempt_amendment_and_halt` (gate on the live `text_prompt`/`select` so the heavily-tested unit paths are unaffected) — staged for its own PR + targeted tests. **B1** dedicated Java/Gradle/Maven family + specialist: add `RepoFamily.JAVA` + `build.gradle`/`pom.xml`/`settings.gradle` to the language-signal sets + a `JavaRepoSetupSpecialist.infer_steps` (ensure JDK, `./gradlew build` / `mvn -q package`, run the jar/boot) + a `java.md` playbook + multi-module (`<modules>`/`include`) ordering — its own PR (touches the family classifier + many classification tests). **A4** model/dataset download disk sizing: at the Plan-149 proactive seam, scan requirements/README for HF model ids / `datasets`/`transformers`/large-checkpoint signals and add download headroom to `estimate_requirement`. **A5** thread `_conda_run_prefix_for_repo` into the codegen/prebuild step assembly for a pure-conda repo (run step already covered). **E1–E3** follow-up resolution robustness: extend the Plan-174 `llm_intent` classifier to acceptance/follow-up resolution; check `active_thread_expires_at` in `resolve_followup_intent` + emit an honest "lost the thread"; a last-mentioned-repo fallback for a pronoun with a single prior repo. **F2/F3** supervisor-LLM-failure DEBUG log (verify Plan-155 F7 covers it) + a faster (2nd-identical) repeated-call block for a probe-weak model.
- Honest: the audit dropped agent false positives (bun IS handled, .env IS auto-populated, venv is idempotent, **B2 readiness wait IS inserted**). This round landed the awareness primitive + aware recovery messaging (G1/G2/G3-msg/F1) + AI/ML coverage (A1/A2/A3) + the stale-code decision (C1); the deferred items are staged by risk/size with a concrete approach each, not skipped.

## R115 (Plan 174) — Intent routing was brittle DETERMINISTIC keyword-matching ("how was your day?" → repo_qa + web.search ×7). Make it a HYBRID CASCADE: deterministic = precision, LLM classifier = the open-ended tail, free-form answer + safe default, loop-guard; + a dry-British 'repo manager' off-topic persona and a content-safety guardrail [DONE: cores; live tone = remaining]
- Verified (screenshot + code + research): "hi" handled fine, but "how was your day?" → repo Q&A → `web.search "how was your day JustHireMe"` ×7 → robotic non-answer. The whole routing layer is deterministic keyword/phrase matching (the LLM only PHRASES replies, never classifies). A denylist can't cover open-ended input — the researched best practice is a hybrid cascade router (Anthropic Routing; Arize/Patronus).
- REQ-PLAN174-F1: `classify_repo_agent_intent` is HIGH-PRECISION — dropped the greedy `m.endswith("?")→ask` and the ambiguous `what is`/`what's` cues; a bare/social "?" no longer hijacks repo_qa (falls through). [DONE — test_plan174::F1NarrowDeterministic]
- REQ-PLAN174-F2: NEW `conversation_routes/llm_intent.classify_repo_relevance` — when deterministic is ambiguous + an active repo + a model, the LLM classifies repo_question/repo_action/general (one cheap JSON call); only a confident repo verdict routes to the repo agent; wired in `_maybe_autoroute_repo_agent`. [DONE — test_plan174::F2LlmClassifier]
- REQ-PLAN174-F3: a general/random/ambiguous message (`utility_fallback`) gets a real FREE-FORM LLM answer in persona (`build_provider_backed_reply`) when a model is live, NOT a canned clarify dead-end; with no model → one clarification (unchanged). Never repo_qa. [DONE — conversation suite green; live = the free-form tone]
- REQ-PLAN174-F4: `harness/loop.py` bounds REPEATED identical tool calls (tool+args signature; the 3rd+ identical call is blocked + nudged) — so a weak model can't loop the same `web.search` to budget exhaustion. [DONE — test_plan174::F4RepeatedToolGuard]
- REQ-PLAN174-F5: persona role = "repo manager" (renamed from "mentor" everywhere); the off-topic register is an LLM-GENERATED, VARIED, one-line dry-British redirect to repo work (detailed prompt in `conversation_agent.md`, not hardcoded) — fires ONLY on off-topic, a genuine repo question stays crisp. [DONE — test_plan174::F5Persona]
- REQ-PLAN174-F6 (safety): `conversation_routes/safety.py` — `is_inappropriate_request` (high-precision floor → refuse + clean dry-British decline, never answered, checked at the free-text entry BEFORE routing) + `scrub_reply` (output guard so Duckln never emits vulgar text even if the model slips) + the persona guardrail. [DONE — test_plan174::F6Safety]
- Honest: deterministic = precision, LLM = coverage, safe default + loop-guard = graceful worst case (the hybrid cascade the research recommends). No router makes qwen2.5:1.5b converse brilliantly (model ceiling), but it's always handled — a real answer with a model, a clarification without, never a repo-search loop. The safety floor is deterministic by design (must not depend on a weak LLM); no filter catches every adversarial phrasing but the output scrub keeps Duckln's own replies clean.
- REQ-PLAN174 (remaining): live tone — off-topic gets a varied British one-liner; a random question gets a real free-form answer; an inappropriate request gets a clean decline.

## R114 (Plan 173) — Two coupled build bugs: the frontend build TOOLS (devDependencies: vite/@vitejs/plugin-react/typescript) weren't installed so `build:all` failed with no deterministic node fix, and the weak qwen recovery looped 3× (~127k tokens). Add a model-free node-deps fix + ensure devDeps + stop the wasteful loop [DONE: cores; live VM = remaining]
- Verified (screenshot + code): disk fixed (R112/R113); now `build:all` → frontend `vite build` → `[UNRESOLVED_IMPORT] Could not resolve 'vite'` / `ERR_MODULE_NOT_FOUND: Cannot find package 'vite'` / `@vitejs/plugin-react` / "This is not the tsc command" — devDependencies absent from node_modules. Root: the node install is SKIPPED when node_modules exists (`dir_exists_check("node_modules")`, repo_bringup.py:3224) so a partial/production node_modules never gets the build tools; AND `match_deterministic_fix`'s `MISSING_MODULE` is Python-only (no node "missing package" rule) → no model-free fix → it fell to the weak `qwen2.5:1.5b` recovery, which loops `1 + _MAX_SUPERVISOR_ROUNDS = 3` rounds (recovery.py:905) re-running investigate/search/memory, can't produce evidence (worsened by `shell.probe → not_a_probe`), and honest-stops after ~127k tokens.
- REQ-PLAN173-F1 (keystone): `ErrorCategory.NODE_DEP_MISSING` + a `match_deterministic_fix` rule for `cannot find package`/`err_module_not_found`/`[unresolved_import] could not resolve`/`cannot find module`/`this is not the tsc command` → install node deps WITH devDependencies in the failing dir (PM from the failed command: `npm install --include=dev` / `npm ci --include=dev` / `pnpm install --prod=false` / `yarn install --production=false`); repo-scoped → auto-applies, model-free. Python `No module named` still → pip; DNS "could not resolve host" not matched. [DONE — test_plan173::NodeDepMissingDeterministicFix]
- REQ-PLAN173-F2: `_node_install_command` always includes devDependencies (`--include=dev` / `--prod=false` / `--production=false`) so a `NODE_ENV=production` env can't strip the build tools. [DONE — test_plan173::NodeInstallIncludesDevDeps]
- REQ-PLAN173-F3: `recover_failed_step_with_agent` — a probe-WEAK model (`model_is_reasoning_capable` False) does a SINGLE pass (`_MAX_SUPERVISOR_ROUNDS=0`), not 3; any challenged round that gathered NO NEW evidence (empty or same chain as last round) breaks early. A capable model keeps the full rounds when new evidence is gathered. [DONE — test_plan173::WeakModelStopsRecoveryEarly]
- REQ-PLAN173-F4: verified the Plan-142 S0 whitelist already covers the common read-only investigation commands (`cat`/`ls`/`npm ls`/`node -v`/`find`/`grep`/`test`) — `not_a_probe` was the weak model misusing `shell.probe` with mutating commands, which F3's no-new-evidence break now stops. No whitelist change needed. [DONE — verified]
- Honest: F1 (model-free) is the keystone — it fixes the build regardless of WHY devDeps were missing, and makes the weak-model loop unnecessary. F3 makes a weak model fail fast + cheap. The exact first-cause (skip vs production vs partial) needs the live VM; F1+F2 fix it either way. qwen2.5:1.5b remains too weak for LLM-led recovery (surfaced honestly).
- REQ-PLAN173 (remaining): live VM — `build:all` hits the missing vite → Duckln runs `npm install --include=dev`, the build tools install, the build proceeds; no 3-round loop.

## R113 (Plan 172) — Resource management (disk/CPU/RAM/GPU/NPU/Apple-MLX) was DUMB deterministic Python with NO LLM knowledge: size the resize from the REAL measured need, add a `resource_management` SKILL, feed skill+facts to the LLM, honest-stop — generic for regular + AI/ML [DONE: cores; live VM = remaining]
- Verified (screenshot + code): after Plan 171 fixed the crash, the resize was still wrong — `recommend` = `disk_total + 5 GB` (the `estimate_requirement` disk floor; `repo_size_mb` unmeasured), so it grew 16→21 GB and the build STILL `ENOSPC`'d, printing the self-contradiction "8 GB free, needs ≥5 GB". And there is NO `.md` managing disk — it's all deterministic Python with only a raw facts blob to the LLM (no skill to reason).
- REQ-PLAN172-F1 (keystone): `_handle_resource_crunch` sizes from the REAL build-output footprint (`du` of `target/`/`dist/`/`build/`/`node_modules`, the artifacts the build regenerates) — recommend = `disk_total + footprint + headroom`, and the retry gate / min_gb / rec_gb / messages use it; replaces the flat-5-GB guess so an approved resize actually fits. [DONE — test_plan172::BuildStepResizesToRealNeed]
- REQ-PLAN172-F2: for a BUILD step (`_is_heavy_build` OR a build/compile/bundle command — `_is_heavy_build("npm run build:all")` is False), do only SAFE cache reclaim (keep `target/`, which the build regenerates) and resize; a NON-build disk crunch still aggressively reclaims stale hogs. [DONE — test_plan172::NonBuildDiskCrunchUsesAggressiveReclaim]
- REQ-PLAN172-F3: a `resource_management.md` SKILL (procedural knowledge — the WHAT, NOT a tool) in `src/agent/playbooks/`, covering disk/RAM/CPU/GPU(CUDA)/NPU/Apple-MLX-Metal with signals → ranked options; the probe/reclaim/resize/route ACTIONS stay the deterministic floor. [DONE — test_plan172::ResourceSkillExists]
- REQ-PLAN172-F4/F7: the persisted `active_resource_crunch` facts name the SKILL + carry a deterministic `guidance` nudge AND the full accelerator state (cpu_cores/ram_mb/swap, gpu_present/gpu_name/vram_mb/cuda_version, an `accelerator` field incl. `mps`, build_regen_mb) — so the LLM reasons over the whole matrix; deterministic floor works model-free for the weak qwen. [DONE — test_plan172::FactsCarryGuidanceAndAccelerator]
- REQ-PLAN172-F5: honest-stop names the REAL GB (corrected min_gb/rec_gb); a target whose probe returns nothing (disk_total 0 = the VM powered off, the screenshot case) → honest "the VM appears unreachable — restart via /vm" instead of reclaiming a VM that isn't there. [DONE — test_plan172::VmDownHonestStop]
- Honest: classified correctly per Claude's defs (capabilities.md) — `resource_management.md` is a SKILL, probe/reclaim/resize are TOOLS (kept as the deterministic floor; registering `resource.*` tools is a noted follow-on). Reuses Plan 153 (GPU wheel + GPU→cloud routing) and Plan 154 (MLX native-macOS). VM-poweroff root cause needs the live VM to diagnose.
- REQ-PLAN172 (remaining): live VM — a disk-full `build:all` sizes the resize to actually fit (≈≥24 GB), keeps the build cache, resumes to completion (or honest-stops with the true number + a capable model's nudge).

## R112 (Plan 171) — The disk-full resource handler must not CRASH (missing `import math`), must SURFACE the space numbers, keep custom-GB resize, resume from the failed step, and pass the facts to the LLM as a hint [DONE: cores; live VM = remaining]
- Verified (screenshot + code): on a disk-full `build:all` (`No space left on device`), `_handle_resource_crunch` printed the "blocker" line then raised `Plan execution raised: name 'math' is not defined` — `repo_bringup.py` called `math.ceil` (line ~12440) but never imported `math` — so the reclaim/resize/delete recommendation that comes AFTER that line never ran ("no recommendation"). No test caught it: `test_plan143` checks the handler via `inspect.getsource` (a string) and the only executing test used a non-resource error that early-returns.
- REQ-PLAN171-F1 (keystone): add `import math` to `repo_bringup.py` — un-crashes `_handle_resource_crunch`, restoring the aggressive reclaim (delete target/caches/node_modules), the resize recommendation, and the honest pause that already existed. [DONE — test_plan171::DiskCrunchDoesNotCrash]
- REQ-PLAN171-F4: SURFACE the real numbers up front — `Disk on <target>: X used / Y free of Z total (P% used) — needs ≈N more free` (RAM analogue), from the snapshot + `needed_free_mb`, before any action. [DONE — test_plan171::F4]
- REQ-PLAN171-F5: MEASURE + report how much the reclaim freed (`_free_mb()` before/after → "Reclaimed ~M GB — now Y free"; marginal otherwise). [DONE — test_plan171::F5]
- REQ-PLAN171-F6: the resize is USER-gated with a CUSTOM-GB entry — `apply_resize`'s `prompt_value` (Enter = recommended, or type a GB, min shown) is threaded `main.py`→`resume_with_approved_plan`→`_handle_resource_crunch`→`apply_resize`; never auto. [DONE — verified wiring + test_plan171::F6]
- REQ-PLAN171-F7: RESUME from the exact failed step — `_retry_and_resume` re-runs the failed step, marks it done (`_done_steps`), returns `_RECOVERY_CONTINUE` (continue, not restart); still-tight → `awaiting_user` so `/plan continue` resumes at the failed step. [DONE — test_plan171::F7]
- REQ-PLAN171-F8 (LLM + deterministic): the deterministic facts (used/free/needed/reclaimed + options) persist to `active_resource_crunch` workflow-state and thread into the conversation agent's `workflow_state` hint (Plan-169 F4 seam), so the LLM owns the voice/recommendation over them; sensing/reclaim/resize-math/resume + user-gate stay the deterministic floor; cleared on resolve; degrades to deterministic wording with no model. [DONE — test_plan171::F8 + CrunchFactsPlumbing]
- REQ-PLAN171 (remaining): live VM proof — a disk-full `build:all` shows the numbers, reclaims + reports freed, asks to resize (custom GB), grows the disk, and resumes from `build:all`.

## R111 (Plan 170) — Stop the FALSE "model unreachable" honest-stop on a CONFIGURED, working model [DONE]
- Verified (batch repro): Ollama reachable, `/api/chat`+`/api/generate` and Duckln's own `generate_provider_reply` return 200/valid output, `build_default_llm_client_or_none` returns a working client — so the "can't connect" was a FALSE honest-stop, not a real failure (my reset/num_ctx hypothesis was disproven + retracted).
- REQ-PLAN170-F1: the Plan-156 honest-stop (`repo_bringup` `_model_reachable156`) keys on a CONFIGURED provider+model (config snapshot) — its documented intent — not a transient `build_default_llm_client_or_none` that can return None for a configured, working model; a configured model never false-stops (a real failure is caught by the critic), and a diagnostic thinking-line records configured/client-built for traceability. [DONE — test_plan170::F1]
- REQ-PLAN170-F2: `_model_connection_error` (defense-in-depth) — only CONNECT-establishment failures (`ConnectError`/`ConnectionRefusedError`/refused/DNS/no-route/max-retries) are "unreachable"; a READ timeout or a MID-REQUEST drop (`ConnectionResetError`/abort/broken-pipe/`RemoteProtocolError`/`TimeoutError`) is reachable-but-failed → `MODEL_UNRESPONSIVE`, never "run ollama serve". [DONE — test_plan170::F2, test_model_connection, test_plan159]
- REQ-PLAN170-F3: a re-runnable batch reproduction (tags + chat/generate + Duckln client round-trip) is the verification that drove the correction. [DONE]
- Honest: I diagnosed wrong twice and verified by reproduction before re-planning; F1 is the grounded fix, F2 a labeled correctness hardening.

## R110 (Plan 169) — Resume-greeting UX: no idle "Working…" spinner, honest kind-aware wording, no phantom "/plan approve", objective facts → LLM hint, honest reachability gate [DONE]
- REQ-PLAN169-F1: `_sync_chat_objective_status` is a status refresh — it never spins the footer (`activity_spinner=False`); only real execution spins. So a persisted objective no longer fakes "Working… 30s" at the greeting/idle. [DONE — test_plan169::F1]
- REQ-PLAN169-F2: the resume-greeting fallback hint is kind-aware (`_objective_resume_verb`) — `repo_deploy`→"setting up", `runtime_repair`→"fixing", else "working on"; an explicit stored hint still wins. [DONE — test_plan169::F2]
- REQ-PLAN169-F3: the Plan-Mode runtime-repair branch no longer says "review the proposed fix and /plan approve" (no PlanRecord is written) — it names the real next step (`/plan off` or describe the fix) and clears the footer. [DONE — test_plan169::F3]
- REQ-PLAN169-F4: `active_objective_kind`/`active_objective_status` are threaded into the conversation agent's `workflow_state` context as HINTS, so the LLM voice frames "what's happening/continue" from the true facts (deterministic only hints; LLM decides/voices). [DONE — test_plan169::F4]
- REQ-PLAN169-F5: `_run_runtime_repair_workflow` checks model reachability up front (`_repair_model_reachable`); if unreachable it shows the honest provider-aware message + context hint, clears the spinner, and returns BEFORE the minutes-long loop — never a false "working" (honest-stop parity with bring-up). [DONE — test_plan169::F5]
- Honest: deterministic UI/messaging + a reachability gate; the LLM stays the decider/voice (F4). Live look is the user's run.

## R109 (Plan 168) — An interrupted setup resumes as "setting up" from where it left, not as a "runtime repair" [DONE: F1–F4; live NL-resume = remaining]
- REQ-PLAN168-F1: objective labels are per-kind (`_objective_kind_label`) — `repo_deploy`→"setting up", `runtime_repair`→"runtime repair" — so a paused SETUP never reads as a repair. [DONE — test_plan168::F1]
- REQ-PLAN168-F2: a bring-up honest-stop on a model/provider blocker writes a RESUMABLE `repo_deploy` objective (status needs_user_decision + resume hint), superseding any stale `runtime_repair` for the repo. [DONE — test_plan168::F2]
- REQ-PLAN168-F3: `/plan continue` with no pending plan but an active `repo_deploy` objective gives an actionable setup-resume message (not the dead "Run /repos"); the bring-up itself resumes via the existing Plan-133 `_done_steps` (skips done steps). [DONE — message; live NL "continue the setup" auto-resume = remaining]
- REQ-PLAN168-F4: the honest-stop message names the resume verbs (`continue the setup` / `/plan continue` / `/plan show`). [DONE]
- Honest: F1/F2/F4 + the /plan-continue message are deterministic; the deep NL-resume→bring-up auto-routing relies on F2's corrected objective + the supervisor and is confirmed live.

## R108 (Plan 167) — Make the token counter trustworthy (no stale on a 429, mark estimates, real context window, honest session label) [DONE]
- REQ-PLAN167-F1: `record_call_estimate` resets the per-call counts at call start; `record_provider_usage` sets exact counts + `last_prompt_is_exact` — so a failed/429 call shows the estimate, never the prior call's exact values. [DONE — test_plan167::F1]
- REQ-PLAN167-F2: the activity bar marks an estimate `↑~N` vs exact `↑N` via `last_prompt_is_exact`. [DONE — test_plan167::F2]
- REQ-PLAN167-F3: `ProviderModel.context_length` (from OpenRouter `/models`) + `persist_model_context_window` at selection → `ctx %` uses the model's real context (override beats the family guess; cleared when unknown). [DONE — test_plan167::F3]
- REQ-PLAN167-F4: the thinking-line counter is labelled `· N tokens (session)` so it isn't read as per-call. [DONE — test_plan167::F4]

## R107 (Plan 166) — Claude-style live token counter on the "Duckln's thinking" line in the conversation window [DONE]
- REQ-PLAN166-F1: the "Duckln's thinking" toggle header shows `· N tokens` (session cumulative `total_tokens`, k-formatted) — `▾ Duckln's thinking · 1.9k tokens` / `▸ Duckln's thinking (N) · 1.9k tokens`; `_thoughts_token_suffix(total)` is the pure formatter. [DONE — test_plan166]
- REQ-PLAN166-F2: the counter refreshes on the 0.1s activity tick (`_update_activity_bar` → `_refresh_thoughts_token_header`, header-only update, no body flicker) so it grows after every LLM call. [DONE]
- Honest: pure UI surfacing of the Plan-164 usage meter; no new accounting. Live tick is the visual confirmation.

## R106 (Plan 165) — Ollama live-check HTTP 500: surface the real error body, classify it, and auto-fall-back to /api/chat on a generate-5xx [DONE: F1–F3; live = remaining]
- Root defect: the ≥400 path discarded the provider's response body, so an Ollama 500 read as a generic "could not generate a reply. Please retry." and the cause was unknowable (why prior message-only changes didn't help).
- REQ-PLAN165-F1: `generate_reply`'s ≥400 branch surfaces the real body (`_extract_error_body` — `error`/`message` field else raw text, redacted + capped) in the message, for ALL providers. [DONE — test_plan165::F1]
- REQ-PLAN165-F2: `_ollama_error_hint(body, model_id)` → actionable next step (OOM → smaller model; not-found → `ollama pull <model>`), folded in via the `_error_hint` hook. [DONE — test_plan165::F2]
- REQ-PLAN165-F3: `OllamaAdapter.generate_reply` tries `/api/generate`, and on a 5xx retries ONCE via `/api/chat` (what `ollama run` uses); chat success returns it, else the original generate error (body+hint) is surfaced; default `/api/generate` unchanged for working setups; a 4xx/transport error does not fall back. [DONE — test_plan165::F3]
- Honest: I did NOT assert the endpoint is the cause; F1 makes the real reason visible, F3 auto-recovers + confirms the one safe hypothesis. Live confirmation (Ollama connecting / showing the exact reason) is the user's machine.

## R105 (Plan 164) — Token visibility: show per-call tokens sent + context fullness, and turn a context-overflow into an honest message [DONE: F1–F5]
- Problem: no token info was shown anywhere; `usage_meter` tracked only cumulative response totals (nothing per-call, nothing on failure, no context-window awareness), so a small free model's overflow read as a false "can't connect".
- REQ-PLAN164-F1: `UsageSnapshot` gains `last_prompt/completion/total_tokens`, `last_estimated_prompt_tokens`, `context_window`; `record_call_estimate(...)` sets the estimate BEFORE a call (survives a failed/overflowed call); `record_provider_usage` fills exact `last_*` from the response. [DONE — test_plan164::F1PerCallAccounting]
- REQ-PLAN164-F2: `ai_client.model_context_window(model_id, config_dir)` — best-effort per-family window (gemma ~8k, gpt-4o ~128k, claude ~200k, …), conservative 8192 default, `model_context_tokens` config override. [DONE — test_plan164::F2ContextWindow]
- REQ-PLAN164-F3: `generate_provider_reply` estimates the prompt (`context_budget.estimate_tokens`) + records the window BEFORE dispatch, covering every caller even when the call raises. [DONE — test_plan164::F3RecordedAtChokePoint]
- REQ-PLAN164-F4: the activity bar shows `↑sent ↓recv · ctx ~X%` (with `⚠` at ≥90%) via `_token_activity_segment` / `_activity_bar_segments`, fed from `current_usage_snapshot()`. [DONE — test_plan164::F4ActivityBarReadout]
- REQ-PLAN164-F5: `near_context_limit` + `context_overflow_hint`; the model-unreachable display sites append the hint (`_with_context_hint`) so a near/over-context failure reads as a SIZE problem, not "can't connect"; a normal-size failure is unchanged. [DONE — test_plan164::F5OverflowHonesty]
- Honest: the count is a chars/4 estimate until exact provider usage arrives; windows are a best-effort table + override; observability only, no model-behavior change (Plan 159 lean-prompt stays the real token-reduction lever).

## R104 (Plan 162) — Connect the built-but-UNWIRED execution features into the live bring-up + DELETE the verified dead code [DONE: F1–F10; live-model/VM = remaining]
- Audit finding: 9 features were implemented + unit-tested but had ZERO production references (never wired), plus genuinely-dead modules/functions. This plan connects all 9 and removes the dead code; signal-keyed, safety floor unchanged.
- REQ-PLAN162-F1 (B2): `_launch_and_await_server` gates a served-URL "ready" on a real HTTP outcome probe (`_probe_served_outcome`→`_outcome_check_command`, `curl -fsS` on the target); a non-2xx/refused → "dead" into recovery, never a silent "running". [DONE — test_plan162::F1OutcomeGate]
- REQ-PLAN162-F2 (C2): before launch, `resume_with_approved_plan` masked-prompts (threaded `secret_prompt`) for required repo secrets with no safe default (`_secret_env_keys_needing_intake`) and injects them into `.env` via a NO-TRACE runner (`_secret_upsert_command`) — never displayed/logged/persisted. [DONE — test_plan162::F2SecretIntake]
- REQ-PLAN162-F3 (C1): a backend+frontend split (`_subdir_run_topology`, no orchestrating root script) launches BOTH via `_launch_concurrent_processes`, wires the backend URL into the frontend `.env`, reports all served URLs. [DONE — test_plan162::F3ConcurrentRun]
- REQ-PLAN162-F4 (C4): a heavy-by-stack repo (`_repo_is_resource_heavy`, e.g. Cargo/conda/CMake) on a remote target surfaces a proactive right-size recommendation up front (user-approved `/resources`/`/vm`, never auto). [DONE — test_plan162::F4Sizing]
- REQ-PLAN162-F5 (Plan160-A): the executor builds a session-scoped `make_destructive_decider(select, approve)` and passes it to `_step_allowed_in_mode`, so a destructive step offers Yes/No/Approve-all-this-session. [DONE — test_plan162::F5DestructiveDecider]
- REQ-PLAN162-F6 (Plan160-D2): `critic_review` records a capability observation per reachable delivery (`record_capability_observation`), so a probe-`weak` model is promoted to `capable` only after N consecutive valid reviews. [DONE — test_plan162::F6CapabilityPromotion]
- REQ-PLAN162-F7 (C3): `resume_with_approved_plan` surfaces `_untrusted_local_notice` once for a fresh repo on a local target (sandbox recommendation); informational, S-class gates unchanged. [DONE — test_plan162::F7UntrustedPosture]
- REQ-PLAN162-F8 (B4): the recovery BLOCK + honest-stop paths surface `capability_gap_from_text` → a concrete "connect `<tool/MCP>` via `/mcp`" next step. [DONE — test_plan162::F8CapabilityGap]
- REQ-PLAN162-F9 (Plan143): `_handle_resource_crunch` grows a cloud (AWS/GCP) disk via `build_cloud_resize_commands` (user-approved, host CLI), then verifies + retries; un-growable → honest pause. [DONE — test_plan162::F9CloudResize]
- REQ-PLAN162-F10: deleted the dead `readme_link_follower.py` module + `_drop_present_prereqs`/`_parse_env_example_keys`/`_existing_manifest_paths`/`_parse_llm_readme_extras`/`_fresh_install_full_command`/`run_parallel_explorers`; orphaned tests removed or repointed to the live `_drop_satisfied_prereqs`. [DONE — test_plan162::F10DeadCodeRemoved]
- REQ-PLAN162 (remaining): live VM/model proof — F1/F3/F4/F9 runtime effects + F2 masked prompts on a real run; all deterministic mechanics are green.

## R94 (Plan 152) — "Already set up" means VERIFIED: re-verify, confirm ready, or report the issue + a FIX-ONLY plan [DONE: cores; flow = integration]
- REQ-PLAN152-1 (F1): `assess_existing_setup(versions, verification_passed)` judges an already-cloned repo HEALTHY only when there's no `PARTIAL:*` and verification didn't fail; returns concrete issues. [DONE]
- REQ-PLAN152-3 (F3): `build_fix_only_plan_steps(issues)` maps a detected issue to the MINIMAL repair step(s) (venv/node_modules/dpkg/build → the deterministic builder), never a full setup; a novel issue → an empty-command step routed to reasoned recovery. [DONE]
- REQ-PLAN152-2 (F2): the already-set-up branch must re-verify → confirm "set up and ready" OR report the issue + ask "plan a fix?" → on yes run the fix-only plan. [PENDING — main.py flow wiring + live]

## R93 (Plan 151) — Don't run `multipass exec` inside the VM; cd to the VM path not the Mac path [DONE: cores; pane-unwrap wired]
- REQ-PLAN151-1 (F1): when the pane is already a VM shell, a host `multipass exec <vm> -- bash -lc '<body>'` is unwrapped to `<body>` before dispatch (multipass isn't in the VM). [DONE — `run_command_in_terminal` + `_unwrap_vm_pane_command`]
- REQ-PLAN151-2 (F2): the open-in-pane cd target for a VM repo is `"$HOME/.duckln/projects/<name>"` (expands), not the stored Mac `install_location`. [DONE — `_build_repo_attach_command`]

## R92 (Plan 150) — Clean-slate controls: fresh-install (keep source, wipe installed), delete a VM/container/cloud target, remove a custom repo from the selection list — every delete confirmed once [DONE: cores; UI flow/menu = integration]
- REQ-PLAN150-1 (F1): a "start fresh" wipe removes everything INSTALLED (node_modules/.venv/build/dist/target/caches/markers) + Duckln-started services + reclaims space, but KEEPS the repo source, `.git`, lockfiles, and `.env`; repo-root-confined, never `..`/absolute/`.git`. [DONE — `_fresh_install_cleanup_command`/`_fresh_install_full_command`]
- REQ-PLAN150-2 (F2): a fresh-install resets Duckln's per-repo done-steps so setup re-runs fully. [DONE — `_clear_done_steps` reused]
- REQ-PLAN150-5 (F5): a unified delete builds the correct destroy command per target (VM `multipass delete --purge`, container `docker rm -f -v`, AWS `terminate-instances`, GCP `instances delete --quiet`); None for local; destructive → confirm once. [DONE — `_delete_target_command`]
- REQ-PLAN150-6 (F6): a custom repo can be removed from the SELECTION list via `store.delete_recent_custom_repo` (forgets only the picker entry; no installed files touched); idempotent. [DONE]
- REQ-PLAN150-7 (F7): the target is picked via an `@<name>` popup (NOT `/delete`); `delete_controls.mention_suggestions`/`resolve_delete_target`/`perform_delete` provide the type-labeled list, phrase resolution, and confirm→delete→tell-user logic. [DONE — logic/module; the Textual `@`-overlay rendering + main.py command hookup = integration]
- REQ-PLAN150-8 (audit): every confirmed deletion is recorded for 7 DAYS (`store.record_deletion` self-prunes >7d; `list_recent_deletions`), and the user is TOLD on confirm ("A record of this is kept for 7 days."). [DONE]
- REQ-PLAN150-3/4 (F3/F4): natural-language "start fresh" flow (confirm → wipe → reinstall) + the suggest-when-stuck offer = integration/UI layer. [PENDING — wiring + live verification]

## R91 (Plan 148) — Duckln must SEARCH (web.search was silently broken), JUSTIFY each step with evidence, and stop blaming a capable model [DONE]
- REQ-PLAN148-1 (F1): `web.search` MUST pass the required `config_dir` and format results — it currently always crashes (missing kwarg), so Duckln cannot search at all; a regression test must call the real handler so the signature can't drift silently again.
- REQ-PLAN148-2 (F2): when stuck, Duckln MUST attempt `web.search` on the exact error before any honest-stop; if internet is OFF it surfaces "run `/internet on` so I can search this", not a silent give-up.
- REQ-PLAN148-3 (F3): the honest-stop MUST be model-aware — only a non-capable model gets the "stronger model" note; a capable model is told the REAL blocker (search/internet/the error).
- REQ-PLAN148-4 (F4): every plan step MUST carry a concrete evidence citation (manifest field / script body / config file / README line / error text / cited web result) in `rationale`/`evidence_excerpt`/`source`, surfaced in the plan; a step with no derivable evidence is flagged speculative. Step-selection evidence is LOCAL (repo reading), not the internet.
- REQ-PLAN148-5 (F5): `_SECONDARY_PY_SETUP_CMD` MUST install requirements + the spec-detected PyInstaller even when `pip install -e .` fails (the `-e .` is `|| true`, non-chain-breaking).
- REQ-PLAN148-6 (F6): RAG depth = snippet-level + a chained `web.fetch` of the top AUTHORITATIVE-allow-listed result (official docs) so the model reasons over full doc content; non-authoritative hosts stay snippet-only (Plan-61 allow-list kept).

## R118 (Plan 178) — Robust `<thought>` capture, uniform reasoning discipline, finished plugin usability [DONE]
- REQ-PLAN178-1 (F1): a fault-tolerant `extract_thinking_and_content(raw)` (`src/duckln/reasoning.py`) MUST split the inner `<thought>…</thought>` monologue from the action payload, surviving a matched / truncated-unclosed / missing tag — never raising (a weak model that ran out of tokens mid-reasoning must not crash the loop).
- REQ-PLAN178-2 (F2): the recovery (Error Agent) path MUST capture the model's `<thought>` monologue into `logical-thinking.md` even when the JSON payload is malformed/truncated; the parse seam parses the post-`</thought>` payload, falling back to the full answer when the tag is skipped.
- REQ-PLAN178-3 (F3): the deep reasoning agents (recovery_agent, recovery_supervisor, planning_investigator) MUST emit a leading `<thought>` block; the agent-output parser (`_extract_json`) MUST be uniformly tolerant of a leading `<thought>` block. The lean planner (Plan 159) is intentionally NOT forced into a `<thought>` prefix.
- REQ-PLAN178-4 (F4): a pulled/registered SKILL MUST be LOADED into the agent's context (task-matched via `registered_skill_hints`) so it is actually consulted; `/skill add` can register a live `registered_skill`.
- REQ-PLAN178-5 (F5): a pulled/registered TOOL/MCP MUST be EXECUTABLE (not just visible) — a shell tool runs its configured command through the shell.run approval/target/S-class path; an MCP tool is invoked over stdio JSON-RPC (`tools/call`); both S2 + approval-gated; an unconfigured record fails gracefully. (MCP execution is exercised live against a real MCP server.)

## R119 (Plan 179) — Smart file-read windows, runtime capability adapter, per-agent model routing [DONE]
- REQ-PLAN179-A1 (A): `fs.read_file` MUST return a LINE window sized by file type (code 600, logs 150, manifests/docs 150) and NEVER raise on a large file; `start_line`/`end_line` paginate, `focus_line` centers on a crash line.
- REQ-PLAN179-A2 (A): a truncated read MUST carry a `[SYSTEM WARNING: showing lines X–Y of Z …]` marker telling the model how to fetch the next slice (`start_line=Y+1`); a whole-file read returns the original text untouched (byte fidelity).
- REQ-PLAN179-A3 (A): a LOCAL-class model MUST get the strict "don't guess a patch from a truncated snippet — read the next range first" footer; a cloud-reasoning model omits it.
- REQ-PLAN179-B1 (B): `model_capability_class(model_id)` MUST classify cloud_reasoning vs local (reusing the Plan-156 probe cache), and `adapt_reasoning_prompt` MUST append the `<thought>` + don't-guess directive ONLY for the local class.
- REQ-PLAN179-C1 (C): an optional `active_routing` map (SUPERVISOR/REPO_AGENT/ERROR_AGENT/WEB_READER → model) MUST be persisted; empty = Unified Mode (every role uses the global model — zero behavior change).
- REQ-PLAN179-C2 (C): `build_llm_client_for_role(config_dir, role)` MUST build the role's assigned-model client (same provider/key/base-url) or fall back to the global client; the repo agent (REPO_AGENT), recovery (ERROR_AGENT), and plan critic (SUPERVISOR) seams are wired to it.
- REQ-PLAN179-C3 (C): `/models` MUST offer Unified vs Specialized per-agent assignment; `/status` MUST show the live role→model table (+ host memory/GPU best-effort).

## R120 (Plan 180) — Close the disclosed loose ends from Plans 178/179 [DONE]
- REQ-PLAN180-1 (F1): the assignable agent ROLES MUST be only the three with a real LLM consumer (`SUPERVISOR`, `REPO_AGENT`, `ERROR_AGENT`). The non-functional `WEB_READER` knob is removed — web reading has no separate LLM step (web.search/web.fetch are deterministic tools the running agent reasons over); `/status` states this.
- REQ-PLAN180-2 (F2): the MCP execution path (`_call_mcp_stdio_tool`) MUST be proven end-to-end against a fake stdio MCP server (`initialize → tools/call` round-trip returns the server's result), not only on its error branches.
- REQ-PLAN180-3 (F3): the Specialized per-agent model picker MUST offer the provider's LIVE model list via select (arrow-key UX) when it can be fetched, falling back to model-id text entry offline.

## R121 (Plan 181) — Production-grade Web Reader: structured, deterministic-floored, additive digest [DONE]
- REQ-PLAN181-1 (F2): `read_web_page` (web_runtime) MUST distill a fetched page into a STRUCTURED digest (`relevant`/`fix_commands`/`key_excerpt`/`confidence`) via the WEB_READER model, and MUST return None (→ caller uses the raw excerpt) when the page is below the size gate, no reader model exists, or the read errors/can't parse — so the fix can never be summarized away.
- REQ-PLAN181-2 (F1): `web.fetch` MUST return the compact digest ONLY for a relevant, high-confidence read of a large page; otherwise it returns the raw excerpt unchanged (today's behavior). The tool stays fully functional with no model.
- REQ-PLAN181-3 (F2b): the deterministic recovery evidence (`gather_repair_fix`) stays the FLOOR; the reader's verbatim `fix_commands` are UNIONED in (enrich, never replace).
- REQ-PLAN181-4 (F3): `WEB_READER` is a routable role again (it now has a real consumer) — assignable via `/models`, shown in `/status`; point it at a cheap model to save tokens on long pages.
- REQ-PLAN181-5: a `web_reader.md` spec (json_object contract, `<thought>`-tolerant) defines the reader; it copies commands VERBATIM and returns `confidence:"low"` when unsure (so the fallback fires).

## R122 (Plan 182) — Consistent live connection test, truthful header green, repo-run guard, curated Ollama pull list [DONE]
- REQ-PLAN182-1 (F1): every provider/model change path (onboarding, `/provider`, `/model`) MUST send a quick LIVE test message (`verify_live_reply`) and only report "ready" when the model actually answers — `/model` previously used only `validate_model` (a metadata/listing check). Provider/location-agnostic (local Ollama / remote / cloud).
- REQ-PLAN182-2 (F4): the header provider dot shows GREEN only when THIS exact (provider, model) returned a live reply (recorded marker via `record_live_verified`); reachable-but-unverified → orange (no fake green). Callers without `config_dir` keep the reachability signal.
- REQ-PLAN182-3 (F5): trying to run a repo with no working model STOPS with a clear "select a provider and model" message naming `/provider` and `/model`; a reachable-but-unverified provider is still allowed (the bring-up honest-stops downstream) — only a genuinely red/unconfigured provider blocks.
- REQ-PLAN182-4 (F2): Ollama "Pull a model" presents a CURATED pick-list (RAM-recommended + popular, excluding installed) with a "type an exact name" fallback — not a blank text box. (Honest: Ollama has no full-registry API, so it's a maintained shortlist.)
- REQ-PLAN182-5 (F3): after starting `ollama serve`, Duckln WAITS for the daemon to bind (bounded readiness poll) before re-validating, fixing the cold-start "installed but not running" loop.

## R123 (Plan 183) — Live-run polish batch: UX wrap/dot, planning correctness, cost, model-independent command fixes, actionable honest-stop, native editor, mode elevation [PARTIAL — 10/12 fixes]
- REQ-PLAN183-1 (F1): the chat pane MUST wrap text (`RichLog(wrap=True)`) so long lines reflow — no horizontal scroll.
- REQ-PLAN183-2 (F2): the header provider dot MUST pulse (spinner frame) while a request is in-flight, settling to the steady colour when idle.
- REQ-PLAN183-3 (F4): the precheck MUST re-probe tools/versions ONCE when the first capture is empty, so the plan uses the real toolchain (no "no relevant tools found" + redundant Node install over an existing Node 24).
- REQ-PLAN183-4 (F5): a failed step MUST name the step + a one-line error hint, not "that step failed".
- REQ-PLAN183-5 (F6): an honest-stop MUST offer an actionable choice (Continue/run-it, do-it-manually with the exact command, or Cancel) via `propose_and_confirm`, not a text dead-end.
- REQ-PLAN183-6 (F7): `logical-thinking.md` MUST open in the OS-native TEXT editor (TextEdit / gedit / Notepad), not a random `.md` app.
- REQ-PLAN183-7 (F8/F9): BASIC setup commands are deterministic + model-independent — install EVERY sub-package (monorepo), AUTO-APPLY a known repo-scoped+verifiable fix FIRST (dir-aware) even with a model configured, and skip the multi-agent recovery pre-pass for a weak model (the ~100k-token sink). The `npm run --if-present build:all` command was valid; the real fix is the un-installed frontend devDeps.
- REQ-PLAN183-8 (F11): "complete it without asking / do it all" in a non-autonomous mode MUST offer a CONSENTED, scoped switch to HOOTLWO (one confirm, not per-step) — never a silent HITL violation, never a refusal — with the destructive/irreversible (S4) floor kept intact.
- REQ-PLAN183-9 (F10 — NEXT PASS): a pasted GitHub URL already resolves+registers a repo record; the remaining gap is the proactive "set up this repo?" offer + target-picker handoff, plus the paste-a-command reconfirm-and-run. (Foundation: `_resolve_runtime_repo_from_message`.)
- REQ-PLAN183-10 (F3 — NEXT PASS): the interactive fact-grounded capability/recommendation advisor (host probe + repo estimate, voiced via `aware_interaction` + multi-turn questions over the existing capability/recommendation routes).

## R124 (Plan 184) — Complete the deferred Plan-183 features: paste command/repo-link + interactive capability advisor [DONE]
- REQ-PLAN184-1 (F10a): a pasted GitHub repo LINK (not inside a question) MUST prompt "set up this repo?" and, on yes, flow through the EXISTING `/repos` target picker + bring-up via a new `preselected_repo` short-circuit on `open_repo_catalog`/`handle_session_command` (zero duplication of the VM/cloud setup flow).
- REQ-PLAN184-2 (F10b): a pasted shell COMMAND (backtick-wrapped, an explicit "run <cmd>", or a bare known-binary line — never prose) MUST echo Duckln's understanding, reconfirm ONCE (the reconfirm IS the approval), then run it as written; a destructive/irreversible (S4) command is still refused.
- REQ-PLAN184-3 (F3): "how much RAM/CPU/disk does this need / can I run it locally / which target do you recommend" MUST be answered from GROUNDED facts (host probe + the active repo's GPU/heavy estimate) with a concrete recommendation; "recommend a repo" MUST be an interactive multi-turn dialogue (library vs trending → specific vs pick-a-machine-fit). A normal question falls through to the conversation router.

## R125 (Plan 185) — LLM-voice the capability/recommendation advisor over deterministic facts (the model explains "why"); stop hardcoded response sentences [DONE]
- REQ-PLAN185-1 (F1): a deterministic `capability_facts` floor (host probe: CPU/RAM/GPU/disk; active-repo signals: needs_gpu/resource_heavy; a recommended-target SIGNAL + structured REASONS) MUST be gathered without hallucination — pure data, never response prose.
- REQ-PLAN185-2 (F2): these facts MUST be injected into the LLM conversation grounding (`context_assembler.assemble_supervisor_prompt_context`) for the capability + recommendation route families (reaching BOTH the fresh-generate and the polish prompts), so the model explains the recommendation + "why / why not local / why a VM" in its OWN words with the real numbers; non-capability turns are not bloated.
- REQ-PLAN185-3 (F2b): the polish prompt MUST let the model EXPLAIN the why grounded in those facts for recommendation/capability routes (keeping repo choice/fit/numbers accurate), while staying a strict fact-preserving polish elsewhere.
- REQ-PLAN185-4 (F3): the Plan-184 deterministic capability/recommendation chat handler (which emitted hardcoded recommendation/why sentences + a canned select dialogue) MUST be REMOVED — these queries flow to the now-grounded LLM conversation. The existing canned rationale templates remain only as the model-absent fallback.
- REQ-PLAN185-5 (F4): weak vs strong is handled by ARCHITECTURE, not scripted branches — one constant deterministic facts floor; a strong model defends "why" richly, a weak model is grounded-but-shallow over the same facts; no model → the existing Plan-119 conversation gate. There is NO "if weak/down say X" hardcoded text.

## R126 (Plan 186) — Production-grade GUIDANCE + clarity: one clean Ollama pull bar + model uninstall, plain-English connectivity + smart startup reconnect + situation-aware next steps, an always-guide Yes/No/Other/Cancel popup, and a header Plan label that shows "off" [DONE]
- REQ-PLAN186-1 (F1a): pulling an Ollama model MUST render as ONE progress bar (collapsed into the single activity line) with a single success confirmation — raw phase noise (`pulling manifest` / bare `pulling` / `verifying` / `writing manifest`) MUST NOT stack as chat lines.
- REQ-PLAN186-2 (F1b): the user MUST be able to remove/uninstall an installed Ollama model (`ollama rm`) from the model menu (reachable via `/provider` and `/model`), with a confirm; the list refreshes afterward.
- REQ-PLAN186-3 (F2a): the offline/connectivity notice MUST be plain, guiding language (no "replies that need the model will fail" jargon), tailored per provider (local Ollama vs a cloud API).
- REQ-PLAN186-4 (F2b): a previously-configured LOCAL Ollama that isn't running at launch MUST be auto-reconnected (safe, mode-gated `ollama serve` + re-probe) so the user sees "Reconnected", not a scary "offline", every launch; cloud providers show the friendly notice only.
- REQ-PLAN186-5 (F2c): Duckln MUST be situation-aware, never dead-end — after `/plan off` with a paused task it guides the next step; a "no reliable run command" stall asks in plain language with clear options (run-this / type the command / skip / keep looking) and NO "post specialist rerun / attempt 0/5" jargon.
- REQ-PLAN186-6 (F3): a reusable Yes / No / Other(write-your-own) / Cancel decision popup (`propose_and_confirm(allow_cancel=…)`) is the always-guide primitive at decision points, so even a non-technical user is never stuck.
- REQ-PLAN186-7 (F4): the header Plan label MUST always be present — `Plan: on` (green) / `Plan: <status>` (orange) / `Plan: off` (dim) — never disappearing when Plan Mode is off.

## R127 (Plan 187) — live-run polish + adopt the upgraded agent specs: silent reconnect, clean repo-Q&A answers (no tool-internal leak), direct-state "which repo" answer, and per-verdict `budget_profiles` [DONE]
- REQ-PLAN187-1 (F1): the startup Ollama auto-reconnect MUST be SILENT on success — the green header dot is the signal; no "Starting Ollama…/Ollama is up./Reconnected" chat lines. A genuine failure still surfaces the friendly offline notice.
- REQ-PLAN187-2 (F2): a repo Q&A answer MUST be clean, natural English and MUST NEVER surface Duckln's internal tool names (`state.read`, `fs.read_file`, `shell.probe`, …) or a fabricated line number as a citation — cite a real repo `file:line` ONLY for a code claim; a state/meta answer is plain prose. (The synth prompt + `_observation_digest` labeling enforce this on any model — it was a prompt-discipline bug, not a model limit.)
- REQ-PLAN187-3 (F3): a "which/last/previous repo were we working on" question MUST be answered directly from session state in one clean sentence (no repo_qa loop, no leak); no active repo → an honest "no previous repo on record" line. High-precision (fires only on this narrow past-tense class).
- REQ-PLAN187-4 (F4): a spec MAY declare per-verdict `budget_profiles` (capable/weak); the harness selects the tier from the OBSERVED Plan-156 capability verdict (behavior, not model name). ONLY turns/seconds/calls/retries flex — the safety floor (tools, scope, S0 ceiling, output_contract) is IDENTICAL across tiers. For a local model the tight tier is for CONVERGENCE + LATENCY + answer quality, not quota. Unknown verdict / no profiles → the flat default block. A malformed block is ignored (never a load failure).
- REQ-PLAN187-5 (F4e): a shipped-spec GUARD test MUST verify every `harness/agents/*.md` parses, `version` ≤ `SUPPORTED_SPEC_VERSION`, every declared tool exists in the registry, and any `budget_profiles` is well-formed — so a future spec edit can't silently break the registry.

## R128 (Plan 188) — Plan Mode is ALWAYS ON + auto-decide (no OFF); robust "which repo did we run last" answer [DONE]
- REQ-PLAN188-1 (F1): a "which/last/previous repo were we working on / did we run last" question MUST be answered directly from session state (no repo_qa loop), via a ROBUST structural match (repo + recency/past-work cue + first-person subject, minus future/recommendation phrasing) — not a brittle phrase list.
- REQ-PLAN188-2 (F2): Plan Mode MUST be always on for the running app — there is no OFF. Duckln auto-decides: a repo setup/deploy → the plan flow (pre-check + reviewable plan the user approves); a runtime repair → a consented Yes/No/Cancel offer (never a "/plan off" dead-end); everything else → a normal answer. `/plan on`/`/plan off` are no-op explanations; the header has no `Plan: off` state.
- REQ-PLAN188-3 (F2a): the always-on coercion happens at the LIVE load boundary (`load_app_config`) — a legacy stored `plan_mode_enabled: false` loads as on. Serialize/deserialize stay FAITHFUL (schema round-trips are not lossy).
- REQ-PLAN188-4 (F2d): the stale plan-mode-OFF surface MUST be removed (the `/plan off`/`/plan on` toggle + descriptors, the `Plan: off` header branch, the dead resume-hint + status/next-action ternaries), while KEEPING the `bring_up_selected_repo` `plan_mode_enabled` PARAMETER + direct-execution flow (internal repair/retry callers pass `False` to execute directly — deleting it would break the executor).
- REQ-PLAN188-5: intent detection MUST NOT force a normal conversation through the plan flow — only a setup/deploy engages the plan gate; normal questions answer directly.

## R129 (Plan 189) — answer ANY question, clearly + cited; route without confusion (proven on real gemma2:9b) [DONE]
- REQ-PLAN189-1 (F1): a SESSION-STATE question (status / which VM / which repo) MUST be answered directly from state (model-independent, never the repo_qa loop).
- REQ-PLAN189-2 (F2): the intent classifier MUST be 4-way — repo_question / repo_action / technical / social — so a general technical/capability question is NOT misrouted to repo_qa.
- REQ-PLAN189-3 (F3): only DEFINITIVE repo cues stay deterministic; ambiguous cues ("how does", "what does", "explain", …) MUST defer to the LLM so a general concept isn't force-read against the repo.
- REQ-PLAN189-4 (F4): a `technical` question MUST be ANSWERED (not redirected) in clear, plain language (not the dry persona) WITH citations to build trust — a repo file for in-repo grounding, real web source URLs when `/internet` is ON, or an HONEST "general knowledge — turn on /internet for sources" note when OFF; and the FIRST offline technical answer MUST offer (Yes/No, once only) to turn the internet on. Only genuinely non-technical chatter gets the persona redirect.
- REQ-PLAN189-5 (F5): a REAL Ollama integration test (opt-in via DUCKLN_OLLAMA_IT) MUST pass actual questions to a local model and assert the routing + a grounded technical answer; deterministic units keep CI green without a model.

## R130 (Plan 190) — ground questions about DUCKLN ITSELF + repo RECOMMENDATION in real facts; route them without confusion (proven on real gemma2:9b + 2b) [DONE]
- REQ-PLAN190-1 (F1): a question about DUCKLN ITSELF — its repo catalog/library ("how many repos", "list your repos"), capabilities ("what can you do", "can you run commands / support docker"), identity ("who are you", "what model are you"), or internet state — MUST be answered from Duckln's REAL facts (the 24-repo catalog + `/repos`, the live model/provider/mode, the internet toggle), model-independent, and MUST NEVER emit the hallucinated "I'm just a language model with no repo access" disclaimer, NEVER act on the repo (repo_action), and NEVER read it (repo_question).
- REQ-PLAN190-2 (F2b): a repo RECOMMENDATION / DISCOVERY ask ("recommend a repo", "which repo should I try", "best repo for X", "top N repos") MUST be answered from the catalog — never repo_action/repo_question/technical. When the ask is AMBIGUOUS (no domain) it MUST ask exactly ONE clarifying question (aware, not a frustrating loop); when a domain is named it MUST answer straight from the catalog filtered by that domain. (The actual GitHub-trending / top-5-ML fetch is a later phase; here Duckln only answers/routes correctly.)
- REQ-PLAN190-3 (F2): the 4-way intent classifier MUST route a Duckln-self or recommendation question to `social` as the safety net — NEVER `repo_action` or `repo_question` — reserving `technical` for impersonal general concepts.
- REQ-PLAN190-4 (F3): the technical-answer system prompt MUST carry an identity guard ("You ARE Duckln — never claim to be a disconnected language model with no repo access") PLUS a one-shot worked example anchoring the best presentation shape (direct one-liner → concise bullets → cite a source). The one-shot example lives in `src/duckln/main.py` (`_TECH_SYSTEM_CLEAR`).
- REQ-PLAN190-5 (F4): deterministic units (`tests/test_plan190.py`) MUST cover each self-class row + the recommendation clarify/answer split + the classifier prompt + the technical guard; a REAL Ollama integration test (`Plan190GroundedIT`, opt-in) MUST prove on gemma2:9b + gemma2:2b that self/recommendation answers are grounded (never the disclaimer) and never misroute to repo_action/repo_question.

## R131 (Plan 191) — an INTELLIGENT, adaptive clarification engine (replaces the rigid single question); proven on real gemma2:9b + 2b [DONE]
- REQ-PLAN191-1 (F1): clarification MUST be adaptive — the LLM decides HOW MANY questions to ask based on how much clarity it needs, asking as FEW as possible (often 0–1) and STOPPING as soon as it can act, with a HARD CAP of 4 questions (never more). One reusable engine `src/duckln/clarify.py` (`run_clarification` / `ClarifyFacts` / `ClarifyResult`); each question carries 3–4 CLEVER options grounded in real facts (never dumb). With NO model it falls back to a single grounded question from the surface's deterministic facts (never a dumb dead-end).
- REQ-PLAN191-2 (F6): the shared choice overlay MUST render clarifications in the Accept-this-plan format — NUMBERED options (1/2/3…), an INLINE "Other — type your own" free-text row (opt-in via `allow_other`, so Yes/No confirms are unaffected), an "Esc to cancel" footer — and MUST fit/wrap long option statements (folding renderables) instead of truncating.
- REQ-PLAN191-3 (F2): when a clarification is pending and the user TYPES a message, Duckln MUST reason CONTINUE (the message answers the pending question → resolve) vs NEW (a different request → clear the clarify and route fresh) — `clarify.decide_continue_or_new`, a single-shot gate (never a loop), with a deterministic floor (option-match / bare-reply → continue; a set-up verb / URL / `/slash` → new) and the LLM for the ambiguous tail.
- REQ-PLAN191-4 (F3): every clarification decision (how many questions + why, why these options, the continue-vs-new verdict + why, the resolution) MUST be recorded to `logical-thinking.md` (`append_thinking_log`, surface="clarify"), redacted, with a clickable link surfaced on resolution.
- REQ-PLAN191-5 (F4): the engine MUST be wired into the recommendation flow (`_maybe_answer_recommendation`) — the surface that owns an interactive channel and needed adaptiveness. `aware_interaction.md` gains an adaptive-multi-option one-shot (Example 4). (The conversation-supervisor/pronoun clarifications are TEXT-based single-round with their own grounded options + followup-state resolution; they inherit F6's overlay format and are deliberately NOT rebuilt around the async engine to avoid destabilizing the pure router.)
- REQ-PLAN191-6 (F5): deterministic units (`tests/test_plan191.py`) MUST cover the adaptive count (1 when resolved / cap at 4 / Esc-cancel / no-model floor / no-overlay pending), continue-vs-new, the thinking-log episode, the numbered-overlay label, and the recommendation wiring; a REAL Ollama IT (`Plan191ClarifyIT`, opt-in) MUST prove on gemma2:9b + 2b that the engine plans clever grounded options, resolves a domain-named ask quickly, and classifies continue-vs-new.

## R132 (Plan 192) — a 4-tier intent-routing cascade so casual/typo'd input is never misrouted to a repo clarifier (per docs/knowledge/intent_routing_design.md); proven on real gemma2:9b + 2b [DONE]
- REQ-PLAN192-1 (F1, Tier 1): a small deterministic chat-contraction map MUST expand whole-token casual forms (`r`→are, `u`→you, `ur`→your, plz/pls, wanna/gonna/gotta, dunno, hows→how is, whats→what is, …) inside `normalize_compact_message` BEFORE keyword matching, so "how r you?" → "how are you" → conversation. Whole-token only (never inside a word); the RAW message is what the Tier-3 LLM sees.
- REQ-PLAN192-2 (F2, Tier 2): the fallback MUST be FLIPPED — a message with NO positive repo SIGNAL (`has_repo_signal`: a repo URL/owner-name/path or a fixed "repo job" verb) is CONVERSATION, and MUST NEVER reach a repo clarifier (the workflow-anchored "continue repairing {repo}/status/path") even when an active repo objective exists. The repo axis is only offered when a repo signal (or a pronoun follow-up / a mentioned repo) is present.
- REQ-PLAN192-3 (F3, Tier 3): the uncertain middle MUST use a context-aware LLM classifier returning a CONFIDENCE — `classify_intent_with_context` using the design brief's VERBATIM system/user prompts (intents repo_task/status/conversation/ambiguous). It fires only when the deterministic tiers didn't resolve AND the message has a repo signal OR is a context-dependent status question. Threshold 0.6: act (repo_task→ask/do on an active repo, status→session-meta, conversation→supervisor) else Tier 4; "ambiguous" always clarifies. One retry for shaky small-model JSON; recent turns are the context.
- REQ-PLAN192-4 (F4, Tier 4): on low confidence or "ambiguous", Duckln MUST ask ONE clarifying question on the RIGHT axis — "a repo job, or just chatting?" — via `phrase_axis_clarification` using the brief's VERBATIM prompt; it MUST NOT list repo-only options (status/path). A deterministic neutral line is the no-model floor (never a dead-end).
- REQ-PLAN192-5 (F5): tiers 1–2 + the cascade control flow stay deterministic; only tiers 3–4 call the model (model-agnostic). Each Tier-3/Tier-4 decision (tier, intent, confidence, rationale) MUST be recorded to the runtime `logical-thinking.md` (surface="routing"); the design reasoning is recorded in `docs/knowledge/logical_thinkinglog.md`.
- REQ-PLAN192-6 (F6): deterministic units (`tests/test_plan192.py`) MUST cover F1 normalization, `has_repo_signal`, an END-TO-END router assertion (a greeting with an active repair objective routes to conversation, not the repo clarifier), the Tier-3 parse/threshold + verbatim prompt, the Tier-4 one-line/floor + verbatim prompt, and the doc §10 battery; a REAL Ollama IT (`Plan192RoutingIT`, opt-in) MUST prove on gemma2:9b + 2b that a greeting is never a repo_task and Tier 4 asks the repo-vs-chat axis (a weak model may return "ambiguous" → clarify, which is the designed safe behavior).
