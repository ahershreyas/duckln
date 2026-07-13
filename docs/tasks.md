# Duckln Task Checklist
(Task must reference Req + Plan explicitly before implementation)

Phases based on `plan.md`.

## Phase 1 — Project Setup
- [x] Create repo skeleton with `docs/`, `.sdc/`, `src/`, `tests/` (Plan: 1,2)
- [x] Add Python project metadata and dependency management (Plan: 1,2)
- [x] Add local config path helpers and environment defaults (Plan: 2)
- [x] Add pixel banner asset and narrow-terminal fallback rendering (Plan: 3)

## Phase 2 — Provider and Onboarding
- [x] Implement provider enum and shared config schema (Plan: 1,2; Req: R1)
- [x] Implement OpenRouter adapter (Plan: 1; Req: R1)
- [x] Implement OpenAI adapter (Plan: 1; Req: R1)
- [x] Implement Anthropic adapter (Plan: 1; Req: R1)
- [x] Implement provider key validation and model validation flow (Plan: 2; Req: R1)
- [x] Implement onboarding wizard with mode selection and config save (Plan: 2; Req: R1,R3)
- [x] Implement slash commands `/mode`, `/provider`, `/model`, and `/config` to update runtime configuration safely during an active session (Plan: 11; Req: R1,R3)
- [x] Implement `/` command palette to display available slash commands with short descriptions and allow arrow-key selection (Plan: 11; Req: R1,R3)
- [x] Prompt user to enter provider API key during onboarding and validate it before saving config (Plan: 2; Req: R1)
- [x] Fetch available model list from the selected provider (OpenRouter, OpenAI, Anthropic) after successful API key validation (Plan: 2; Req: R1)
- [x] Sort fetched models alphabetically and present them via arrow-key selection UI (Plan: 2; Req: R1)
- [x] Save the selected model in config after validation (Plan: 2; Req: R1)
- [x] Display clear error messages for invalid API keys, failed connections, or empty model lists, and allow user to retry without crashing (Plan: 2; Req: R1)
- [x] Display mode selection with full names and one-line explanations during onboarding (Plan: 4; Req: R3)

## Phase 3 — Modes and Safety
- [x] Implement mode model for HITL/HOTL/HOOTLWO (Plan: 4; Req: R3)
- [x] Implement blocked command matcher (Plan: 5; Req: R4)
- [x] Implement safety classes S0–S4 (Plan: 5,14; Req: R3,R4)
- [x] Implement timeout and controlled subprocess execution (Plan: 5; Req: R4)
- [x] Implement HOOTLWO whitelist for safe diagnostics and installs only (Plan: 14; Req: R3,R4)

## Phase 4 — Privacy and Diagnostics
- [x] Implement redaction rules for tokens, keys, and obvious secrets (Plan: 6; Req: R5)
- [x] Implement minimal payload builder by mode (Plan: 6; Req: R5)
- [x] Implement classification for:
  - [x] missing module
  - [x] pip/python mismatch
  - [x] file/path not found
  - [x] permission denied
  - [x] CUDA/torch mismatch
  (Plan: 7,13; Req: R6)
- [x] Implement minimal context gatherer for Python/AI issues (Plan: 7,13; Req: R6)
- [x] Implement `/healthcheck` command to validate environment, dependencies, and provider connectivity with clear pass/fail output (Plan: 12; Req: R6)

## Phase 5 — Suggestion and Verification Loop
- [x] Implement bounded prompt templates with concise explanation rules (Plan: 8,10,13; Req: R7,R9)
- [x] Return 1–3 exact next commands with purpose labels (Plan: 8; Req: R7)
- [x] Implement HITL behavior: suggest only (Plan: 4,8; Req: R3,R7)
- [x] Implement HOTL behavior: ask approval before run (Plan: 4,8; Req: R3,R7)
- [x] Implement verification checks after suggested or auto-run fixes (Plan: 9; Req: R8)
- [x] Implement short teaching snippets for venv, pip, CUDA, torch mismatch (Plan: 10; Req: R9)

## Phase 6 — Logging and Production Hardening
- [x] Implement structured redaction-safe logs (Plan: 12; Req: R10)
- [x] Add provider failure logging without secret leakage (Plan: 12; Req: R10)
- [x] Add command blocked-event logging (Plan: 12; Req: R10)
- [x] Add traceability map from requirement → plan → task → test (Plan: 12; Req: R10)

## Phase 7 — Testing
- [x] Unit tests for safety rules (Plan: 5,14; Req: R3,R4)
- [x] Unit tests for classifier rules (Plan: 7,13; Req: R6)
- [x] Unit tests for payload redaction (Plan: 6; Req: R5)
- [x] Integration tests for provider adapters (Plan: 1; Req: R1)
- [x] Integration tests for verification loop (Plan: 9; Req: R8)
- [x] Manual scenario tests:
  - [x] missing torch in wrong environment
  - [x] broken venv activation
  - [x] file not found
  - [x] permission denied
  - [x] torch compiled without CUDA
  (Plan: 7,8,9; Req: R6,R7,R8)

## Phase 8 — Launch Readiness
- [x] Record first-launch demo script (Plan: 3,8,10)
- [x] Prepare known limitations list (Plan: 15,16)
- [x] Prepare rollout checklist and rollback notes (Plan: 12)
- [ ] Mark all linked tasks complete before release sign-off
- [ ] Package Duckln as standalone executables for macOS (Intel x86_64 + Apple Silicon ARM64) and Ubuntu; include install instructions (Plan: 12, Release Readiness)

## Phase 9 — Agentic Repo Bring-up

- [x] Implement repo catalog loader from local repos.json cache (Plan: 17; Req: R11)
- [x] Implement `/repos refresh` command to fetch live GitHub data and update cache (Plan: 17; Req: R11)
- [x] Implement repo dropdown with metadata (name, description, stars, category tag,language/framework tag, updated) (Plan: 17; Req: R11)
- [x] Implement repo clone and inspection (README, requirements, pyproject) (Plan: 18; Req: R11)
- [x] Implement setup inference engine (Plan: 18; Req: R11)
- [x] Implement execution flow per mode (HITL/HOTL/HOOTLWO) (Plan: 18; Req: R11)
- [x] Implement system probe (CPU, RAM, GPU, OS, Apple Silicon detection) (Plan: 20; Req: R11)
- [x] Implement Multipass VM creation and connection (Plan: 19; Req: R11)
- [x] Implement `/vm` slash command to trigger VM creation flow during an active session (Plan: 19; Req: R11)
- [x] Implement Duckln bootstrap inside VM (Plan: 19; Req: R11)
- [x] Prompt user after VM creation whether to install Duckln inside the VM (Plan: 19; Req: R11)
- [x] Implement fresh Duckln installation flow inside the VM after explicit user consent (Plan: 19; Req: R11)
- [x] If user declines Duckln installation inside the VM, leave the VM ready for manual use and show essential connection commands (Plan: 19; Req: R11)
- [x] Prompt user to configure provider/model/API key manually inside the VM using Duckln config commands instead of transferring local credentials (Plan: 20; Req: R11)
- [x] Continue non-auth setup inside the VM and pause only API-dependent steps with a clear message (Plan: 20; Req: R11)
- [x] Implement filesystem memory structure (AGENTS.md, skills, sessions) (Plan: 20; Req: R11)
- [x] Implement SQLite state store (runs, configs, VM linkage) (Plan: 20; Req: R11)
- [x] Add root `AGENTS.md` and align implementation to it as the primary agent contract (Plan: 22; Req: R12)
- [x] Implement filesystem-facing memory structure (`AGENTS.md`, `skills/`, `knowledge/`, `sessions/`) (Plan: 23; Req: R12)
- [x] Implement SQLite-backed state store for config, history, repo state, VM linkage, and healthcheck state (Plan: 23; Req: R12)
- [x] Implement hardware-aware branching for Apple Silicon, non-Apple systems, and NVIDIA/CUDA-capable systems (Plan: 24; Req: R12)
- [x] Implement `/memory clear` flow with session/project/factory reset options and confirmation (Plan: 23; Req: R12)
- [x] Add cancel/return option to `/memory clear` flow so users can back out without confirmation or state changes (Plan: 23; Req: R12)
- [x] Implement VM configuration prompts (CPU, memory, optional name with default duckln-vm) (Plan: 25; Req: R11)
- [x] Implement post-VM setup guidance (connection commands display) (Plan: 26; Req: R11)
- [ ] Implement final success message "Your environment is ready! 🦆" for local and VM flows (Plan: 26; Req: R11)
- [x] Implement hardware detection for Apple Silicon, CPU-only, and NVIDIA CUDA systems with correct branching (Plan: 27; Req: R12)
- [x] Implement VM name conflict handling with auto-increment suffix (duckln-vm, duckln-vm-1, etc.) (Plan: 28; Req: R11)
- [x] Implement SQLite-backed memory records as the source of truth for managed agent memory (Plan: 29; Req: R12)
- [x] Implement filesystem materialization layer for `AGENTS.md`, `skills/`, `knowledge/`, and `sessions/` from SQLite-backed memory (Plan: 30; Req: R12)
- [x] Implement sync rules to keep SQLite and materialized memory files aligned without storing raw logs or transcripts (Plan: 30; Req: R12)
- [x] Implement `/help` command to display available slash commands with one-line descriptions (Plan: 19; Req: R11)
- [x] Add post-onboarding hint directing users to `/help` for command discovery (Plan: 19; Req: R11)
- [x] Add cancel/return option to `/repos` selector and preserve session state on cancel (Plan: 17; Req: R11)
- [x] Make VM CUDA guidance hardware-aware based on host architecture, NVIDIA/CUDA detection, and VM GPU access capability (Plan: 27; Req: R12)
- [x] Generate real bundled `src/assets/repos.json` from GitHub topic data for launch (Plan: 17; Req: R11)
- [x] Add a repo-catalog update script to regenerate `src/assets/repos.json` from GitHub topic data (Plan: 17; Req: R11)
- [x] Add a GitHub Actions workflow to refresh `src/assets/repos.json` automatically on a schedule (Plan: 17; Req: R11)
- [x] Filter bundled repo catalog to exclude training-focused repos and keep inference/serving/tooling repos only (Plan: 17; Req: R11)
- [x] Tighten bundled repo-catalog filtering to exclude frameworks, course material, docs/resource repos, suspicious repos, and non-runnable language targets at launch (Plan: 17; Req: R11)
- [x] Refine bundled repo catalog to remove framework/library/course repos and tighten category/framework mapping for launch quality (Plan: 17; Req: R11)
- [x] Add curated launch catalog overrides file for explicit allowlist, blocklist, and metadata fixes (Plan: 17; Req: R11)
- [x] Update bundled repo catalog generation to apply launch allowlist/blocklist and metadata overrides before writing `src/assets/repos.json` (Plan: 17; Req: R11)
- [x] Support launch warnings in bundled repo metadata for repos that are runnable but require special hardware or non-standard setup (Plan: 17; Req: R11)
- [x] Expand curated launch catalog to ~20–25 high-value runnable repos and remove provider/runtime repos such as `ollama` from the bundled repo catalog (Plan: 17; Req: R11)
- [x] Add curated launch seed file to define the default bundled launch repo catalog independently of GitHub topic ranking (Plan: 17; Req: R11)
- [x] Add Coqui TTS to the curated core launch catalog and preserve it in bundled repo generation (Plan: 37; Req: R11)
- [x] Implement repo-family classification before bring-up (Python, C++/native, Node/TypeScript, audio, diffusion, multi-service) (Plan: 33; Req: R11)
- [x] Implement supervisor agent to inspect repo signals, select specialist agent, and verify outcomes (Plan: 32; Req: R11)
- [x] Implement Python specialist bring-up agent and playbook (Plan: 34; Req: R11)
- [x] Implement C++/native runtime specialist bring-up agent and playbook (Plan: 34; Req: R11)
- [x] Implement Node/TypeScript specialist bring-up agent and playbook (Plan: 34; Req: R11)
- [x] Implement audio specialist bring-up agent and playbook (Plan: 34; Req: R11)
- [x] Implement diffusion-heavy specialist bring-up agent and playbook (Plan: 34; Req: R11)
- [x] Implement VM/environment specialist agent and playbook (Plan: 34; Req: R11)
- [x] Implement provider-routing specialist agent including Ollama local support (Plan: 35; Req: R11)
- [x] Add detailed guardrails and playbook-backed decision rules for supervisor and specialist agents (Plan: 36; Req: R11,R12)
- [x] Show clear provider/model connection success confirmation after validation and model selection (Plan: 35; Req: R11)
- [x] Wire Ollama into onboarding and runtime provider selection as a first-class provider option (Plan: 35; Req: R11)
- [x] Implement Ollama local detection, bounded retry/back handling, model pull selection, and null-secret config persistence (Plan: 35; Req: R11)
- [x] Add Cancel / Exit / Back options to `/provider` and `/model` flows and preserve current state when cancelled (Plan: 35; Req: R11)
- [x] Implement Ollama-specific provider onboarding flow separate from cloud API-key providers (Plan: 35; Req: R11)
- [x] Detect local Ollama runtime before model selection and show install/start guidance when unavailable (Plan: 35; Req: R11)
- [x] Implement Ollama local model listing and selection from `http://localhost:11434/api/tags` (Plan: 35; Req: R11)
- [x] Implement Ollama curated model recommendations and pull flow based on detected system RAM (Plan: 35; Req: R11)
- [x] Store Ollama provider config with `api_key: null` and no secret prompt (Plan: 35; Req: R11)
- [x] Reuse Ollama model selection/pull flow in `/model` for existing Ollama sessions (Plan: 35; Req: R11)
- [x] Add Ollama startup health check on session start with clear retry guidance (Plan: 35; Req: R11)
- [x] Add Cancel / Exit / Back options to `/provider` and `/model` flows (Plan: 35; Req: R11)
- [x] Add custom Ollama base URL retry/persistence for onboarding, `/provider`, `/model`, and session startup health checks (Plan: 35; Req: R11)
- [x] Add mode-aware `ollama serve` startup handling when local Ollama is installed but not running (Plan: 35; Req: R11)
- [x] Keep user-facing provider/runtime errors concise while routing structured provider logs to internal logging only (Plan: 35; Req: R10,R11)
- [x] Remove raw provider-error logs from the user-facing Ollama terminal flow and show only clean retryable guidance (Plan: 35; Req: R11)
- [x] Implement OS-specific Ollama install guidance for macOS, Linux, and Windows (Plan: 35; Req: R11)
- [x] Detect whether Ollama is installed, not running, or unreachable, and route the terminal UX accordingly (Plan: 35; Req: R11)
- [x] Offer automatic Ollama start when installed but not running, subject to mode and safety rules (Plan: 35; Req: R11)
- [x] Support custom Ollama base URL entry and persistence when the default localhost URL is not reachable (Plan: 35; Req: R11)
- [x] Reuse Ollama model selection and pull flow from `/model` when Ollama is the current provider (Plan: 35; Req: R11)
- [x] Add graceful fallback from failed Ollama setup back to provider selection (Plan: 35; Req: R11)
- [x] Add Cancel / Exit / Back options to all `/provider` and `/model` selection flows and preserve state on cancel (Plan: 35; Req: R11)
- [x] Implement Debug / Recovery specialist agent for failed setup attempts (Plan: 38; Req: R11,R12)
- [x] Implement failure classification for dependency, runtime, environment, tooling, and mixed-stack setup failures (Plan: 39; Req: R11,R12)
- [x] Implement supervisor escalation to Debug / Recovery agent after failed specialist verification (Plan: 40; Req: R11,R12)
- [x] Implement bounded recovery decisions: retry same specialist, reroute to another specialist, request prerequisite from user, or mark unsupported (Plan: 39; Req: R11,R12)
- [x] Add `debug_recovery.md` playbook with cross-stack troubleshooting guardrails and verification rules (Plan: 38; Req: R11,R12)
- [x] Add concise post-failure memory reflection and compaction rules for recovery learnings (Plan: 40; Req: R11,R12)
- [x] Implement confidence-aware supervisor fallback behavior for low-confidence repo-family classification and repeated failure cases (Plan: 40; Req: R11,R12)
- [x] Implement first-run safety and permissions screen with persisted acceptance (Plan: 41; Req: R12)
- [x] Implement first-run user name capture and persistence in user preferences (Plan: 41; Req: R12)
- [x] Add user_preferences state for name, safety acceptance, onboarding completion, and preferred mode (Plan: 41; Req: R12)
- [x] Implement compact session header with provider, model, mode, user, and memory state (Plan: 41; Req: R12)
- [x] Standardize cancel vs exit behavior across all interactive flows and top-level session handling (Plan: 41; Req: R12)
- [x] Add concise work-status updates for non-trivial setup and recovery actions (Plan: 41; Req: R12)
- [x] Implement `duckln uninstall` flow with tiered removal options and confirmations (Plan: 42; Req: R12)
- [x] Implement OS-aware uninstall handling for macOS, Linux, and Windows based on detected install method (Plan: 42; Req: R12)
- [x] Reconcile config/user preferences/runtime session state through one SQLite-backed non-secret contract plus file-backed secret payload (Plan: 30,41; Req: R10,R12)
- [x] Make `/memory clear` factory reset clear config, preferences, managed memory, and stale in-session state consistently (Plan: 22,30; Req: R11,R12)
- [x] Remove API-key echo and handle interrupted secret prompts without leaking secrets to terminal, logs, memory, or SQLite (Plan: 1,12,41; Req: R1,R10,R12)
- [x] Persist VM execution-target context and pass provider/target routing signals into `/repos` bring-up (Plan: 19,33,35; Req: R11)
- [x] Convert Debug / Recovery from advisory-only output into one bounded retry/reroute/prerequisite/unsupported execution branch with concise SQLite-backed session memory notes (Plan: 39,40,41; Req: R11,R12)
- [x] Enforce specialist playbook coverage at runtime instead of treating `src/agent/playbooks/*.md` as metadata-only placeholders (Plan: 35,37; Req: R11,R12)

## Phase 10 — UX, Onboarding, and Product Experience
- [x] Add `docs/specs/ui_standards.md` for Duckln terminal color, header, command, and panel conventions (Plan: 41; Req: R12)
- [x] Add `docs/specs/onboarding_flow.md` for first-run safety, name capture, provider, and model setup flow (Plan: 41; Req: R12)
- [x] Add `docs/specs/uninstall_flow.md` for bounded uninstall UX and OS-aware removal behavior (Plan: 42; Req: R12)
- [x] Make the startup session header responsive to terminal width and prevent broken alignment on narrow terminals (Plan: 41; Req: R12)
- [x] Restrict cyan command color to actual slash commands only and render surrounding explanatory text in system color (Plan: 41; Req: R12)
- [x] Refine the main terminal layout to a compact chat-shell style with clear transcript and input separation (Plan: 41; Req: R12)
- [x] Improve default non-slash user interaction so Duckln responds conversationally instead of only falling back to command help text (Plan: 41; Req: R12)
- [x] Make the startup session header responsive and stack gracefully on narrow terminals (Plan: 41; Req: R12)
 - [x] Show Duckln version in the startup session header (Plan: 41; Req: R12)
 - [x] Refine transcript/input into a compact chat-shell layout with upward message flow (Plan: 41; Req: R12)
 - [x] Add compact boxed work-status / thinking updates for non-trivial actions (Plan: 41; Req: R12)
 - [x] Add compact footer help/send/exit hints near the prompt area (Plan: 41; Req: R12)
 - [x] Render duckln> in brand color while keeping typed user text in the user-input color (Plan: 41; Req: R12)
- [x] Replace repetitive fallback replies with contextual conversational responses for non-slash input (Plan: 41; Req: R12)
- [x] Add lightweight intent-aware conversational handling before generic command fallback (Plan: 41; Req: R12)
- [x] Make Duckln greeting/help responses vary naturally and use session context where available (Plan: 41; Req: R12)
- [x] Make non-slash conversation answer user intent first, avoid repeated stock phrasing, and steer to the next useful terminal action only when appropriate (Plan: 41; Req: R12)
- [x] Route non-slash conversation through a supervised conversation layer with bounded specialists and a live provider-backed reply path when configured (Plan: 41,43; Req: R12)
- [x] Show supervisor and specialist agent names with bounded phase progress and elapsed time during repo bring-up work instead of a static generic thinking label (Plan: 41,43; Req: R12)
- [x] Route repo bring-up approvals through the same in-chat arrow-key confirmation flow and enforce HITL, HOTL, and HOOTLWO setup execution behavior consistently (Plan: 41,43; Req: R12)
- [x] Ground non-slash conversation in cached repo knowledge, local system facts, and recent turn context so follow-up questions stay specific instead of falling back to generic routing (Plan: 41,43; Req: R12)
- [x] Make recommendation replies hardware-aware across macOS, Linux, and Windows CPU/RAM probes and answer repo recommendation questions with grounded rationale before steering to `/repos` (Plan: 41,43; Req: R12)

## Phase 11 — Agent Consistency, Safety, and Recovery
- [x] Add a shared agent context service so conversation and repo bring-up consume the same bounded memory, system, and repo grounding contract (Plan: 43; Req: R12)
- [x] Add SQLite-backed repo knowledge caching with concise normalized setup facts instead of raw README or log storage (Plan: 43; Req: R12)
- [x] Make repo-specific conversation grounding prefer cached memory, then local repo setup files, then bounded remote repo file inspection before fallback (Plan: 43; Req: R12)
- [x] Route repo bring-up planning and recovery through the shared repo-knowledge context and persist concise post-action repo learnings (Plan: 43; Req: R12)
- [x] Remove managed-memory session-summary filesystem fallback so SQLite stays authoritative for Duckln-managed memory reads (Plan: 43; Req: R12)
- [x] Add supervisor-led repo preflight confirmation with an inspect-requirements option and compatibility warnings before bring-up starts (Plan: 44; Req: R12)
- [x] Add a bounded verification agent after repo setup and require the supervisor to author the final success or partial-failure summary (Plan: 44; Req: R12)
- [x] Persist repo setup outcomes such as install path, environment path, run/access hints, verification results, and removal hints for later follow-up answers (Plan: 44; Req: R12)
- [x] Answer repo follow-up questions like requirements, install location, run/access, and removal from stored repo setup context before falling back to command routing (Plan: 44; Req: R12)
- [x] Track prepared and active repos in SQLite-backed repo state so Duckln can answer repo inventory and active-repo questions directly (Plan: 45; Req: R12)
- [x] Allow the supervisor to run a prepared repo from Duckln’s managed workspace using stored run commands and access hints (Plan: 45; Req: R12)
- [x] On repo run failure, have the supervisor ask whether Duckln should fix the issue, then route bounded repair through setup specialists and retry the run when verification recovers (Plan: 45; Req: R12)
- [x] Answer natural-language repo inventory, setup-status, and repo-coverage questions from tracked SQLite-backed repo state instead of falling back to generic setup routing (Plan: 45; Req: R12)
- [x] Add `/remember` to persist a concise high-signal snapshot of the current Duckln context into managed session memory (Plan: 43; Req: R12)
- [x] After a successful repo run, let the supervisor ask whether Duckln should open the detected local endpoint and do so with mode-aware approval handling (Plan: 45; Req: R12)
- [x] Persist concise recommendation context so follow-up questions like “why that repo?” stay grounded in the chosen repo, machine fit, and alternatives instead of falling back to generic routing (Plan: 43; Req: R12)
- [x] Add SQLite-backed bounded learning records and promoted heuristics so Duckln can improve routing, recommendation quality, and repair guidance from repeated validated outcomes without self-editing code (Plan: 46; Req: R12)
- [x] Add explicit CPU/MPS/GPU supervisor reasoning to recommendation, repo requirements, and repo preflight answers so Duckln explains accelerator fit and practical effect instead of vague hardware hints (Plan: 46; Req: R12)
- [x] Upgrade `/remember` and memory-clearing flows so learned conversation/recommendation heuristics are treated as real Duckln memory and can be reset cleanly by scope (Plan: 46; Req: R12)
- [x] Make recommendation routing robust to messy natural phrasing and repeated `/repos` or `/help` deflections by treating them as correction signals that can promote phrase-family heuristics (Plan: 47; Req: R12)
- [x] Make repo requirements and fit answers explain total RAM, usable RAM after overhead, lower-bound vs comfortable memory, compute path, and likely failure modes (Plan: 47; Req: R12)
- [x] Feed verified setup outcomes back into recommendation learning so successful recommendations gain future ranking weight and bad recommendations can be penalized over time (Plan: 47; Req: R12)
- [x] Persist a supervisor follow-up state so Duckln can carry accepted recommendations, next-step invitations, and last discussed repo/system facts across free-text turns (Plan: 48; Req: R12)
- [x] Resolve follow-up replies like `yes please`, `inspect its requirements`, `run it`, and `that repo` against the pending supervisor state before generic reclassification (Plan: 48; Req: R12)
- [x] Add direct system-capacity and memory/meta conversational answers so Duckln can explain machine facts and bounded learnings without falling back to generic command prompts (Plan: 48; Req: R12)
- [x] Make repo capability coverage answers machine-aware by separating curated catalog coverage from the smaller set of realistic options for the current system (Plan: 48; Req: R12)
- [x] Preserve normalized repo-knowledge fields when storing conversation learnings so recommendation chatter does not erase structured requirement metadata needed by later follow-up answers (Plan: 48; Req: R12)
- [ ] Cache the session system prompt and rebuild it only on meaningful session-context changes (Plan: 43; Req: R12)
- [ ] Maintain dynamic per-session conversation history and pass it with the cached system prompt on every agent call (Plan: 43; Req: R12)
- [ ] Implement bounded history compaction with concise summary replacement for long sessions (Plan: 43; Req: R12)

## Phase 13 — VM Bring-up Bug Fixes
- [x] Initialize `choice_prompt = None` before the `auto_trigger_repo_setup` branch in `main.py` to prevent `NameError` when a VM is freshly created and bring-up is auto-triggered (Plan: 49; Req: R11)
- [x] Add `vm_name` parameter to `ControlledCommandRunner` in `shell.py` and wrap commands with `multipass exec <vm_name> -- bash -lc "<command>"` when `execution_target == "vm"` and `vm_name` is set (Plan: 49; Req: R11)
- [x] Add `vm_name` parameter to `bring_up_selected_repo` in `repo_bringup.py` and pass it to `ControlledCommandRunner` so repo setup commands route into the VM (Plan: 49; Req: R11)
- [x] Pass `vm_name=vm_name` to all `bring_up_selected_repo` call sites in `main.py` where `execution_target` may be `"vm"` (Plan: 49; Req: R11)

## Phase 12 - Repo Catalog
- [x] Make the curated seeded catalog the default `/repos` experience (Plan: 17; Req: R11)
- [x] Fix `/repos refresh` so it refreshes the curated seeded catalog instead of replacing it with broad unfiltered discovery results (Plan: 17; Req: R11)
- [x] Ensure `/repos` reads the curated refreshed cache consistently after refresh (Plan: 17; Req: R11)

## Phase 14 — Cloud Setup Agent Loop
- [x] Add generic `run_agent_loop` driver in `src/agent/agent_loop.py` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-1)
- [x] Add GCP and AWS classifier + remediation registry in `src/duckln/cloud_remediation.py` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-1)
- [x] Expose failed `CommandResult` from `discover_gcp_zones_detailed` and `discover_aws_regions_detailed` in `cloud_runtime.py` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-2)
- [x] Wire `/cloud` GCP zone and AWS region branches in `main.py` to `run_agent_loop` with Fix now / Fix later / Ignore picker (Plan: 50; Req: R14)
- [x] Persist deferred remediations via `pending_cloud_remediation` followup state and surface them on next `/cloud` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-1)
- [x] Add unit tests for the agent loop, classifier registry, and end-to-end GCP/AWS scenarios (Plan: 50; Req: R14)
- [x] Add `_confirm_or_switch_gcp_project` to always prompt "Continue with project X?" on GCP auth (Plan: 50; Req: R14, REQ-CLOUD-LOOP-3)
- [x] Add `audit_gcp_api_keys` + classifier + remediation for unrestricted Gemini/generativelanguage API keys (Plan: 50; Req: R14, REQ-CLOUD-LOOP-4)
- [x] Wire `_run_gcp_api_key_audit_step` into `_continue_cloud_auth_setup` after the zone loop (Plan: 50; Req: R14, REQ-CLOUD-LOOP-4)
- [x] Lock the cloud-header update contract with a unit test on `_mark_cloud_provider_target` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-5)
- [x] Create `src/duckln/cloud_shapes.py` with `DiscoveredCloudShape` + provider parsers + categorizer + label/disk helpers (Plan: 50; Req: R14, REQ-CLOUD-LOOP-6)
- [x] Drop approved-shortlist filter from `discover_available_cloud_shapes` and surface the full provider catalogue (Plan: 50; Req: R14, REQ-CLOUD-LOOP-6)
- [x] Replace `_handle_cloud_create_command` shape picker with two-tier `_pick_cloud_shape_two_tier` (category → shape) (Plan: 50; Req: R14, REQ-CLOUD-LOOP-6)
- [x] Seed `SplitPaneChatInterface` `_connection_hint` from persisted execution target via `initial_connection_type`; plumb through `build_chat_interface` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-7)
- [x] Add `_reassert_cloud_terminal_target_from_state` and call it at chat startup and at the top of `/cloud` (Plan: 50; Req: R14, REQ-CLOUD-LOOP-7)
- [x] Add `update_connection` no-op to fallback `TerminalChatInterface` so callers can safely refresh the hint (Plan: 50; Req: R14, REQ-CLOUD-LOOP-7)

## Phase 15 — /explore + Q/Esc cancel
- [x] Add Q/Esc cancel to the Textual choice overlay `on_key` handler (Plan: 51; Req: R15, REQ-UX-CANCEL-1)
- [x] Add `_with_cancel_hint`, `_wrap_select_with_cancel_hint`, `_wrap_text_with_cancel_hint` and apply at `_handle_cloud_command` / `_handle_cloud_create_command` (Plan: 51; Req: R15, REQ-UX-CANCEL-1)
- [x] Create `src/duckln/explore_trending.py` — pure scraper + parser + sorter + AI flag (Plan: 51; Req: R16, REQ-EXPLORE-1, REQ-EXPLORE-3)
- [x] Create `src/duckln/explore_cache.py` — SQLite-backed 15-min TTL (Plan: 51; Req: R16, REQ-EXPLORE-2)
- [x] Create `src/duckln/explore_runtime.py` — cache-aware view loader, render helpers, and `run_explore_loop` (Plan: 51; Req: R16, REQ-EXPLORE-1, REQ-EXPLORE-2, REQ-EXPLORE-4)
- [x] Add `/explore` to `SLASH_COMMANDS` and slash-help; wire `_handle_explore_command` into the slash dispatcher (Plan: 51; Req: R16, REQ-EXPLORE-1, REQ-EXPLORE-4)
- [x] Support `duckln explore` CLI form by queuing `/explore` at REPL start (Plan: 51; Req: R16, REQ-EXPLORE-1)
- [x] Add `beautifulsoup4>=4.12,<5.0` dependency (Plan: 51; Req: R16, REQ-EXPLORE-1)
- [x] Add unit tests for scraper, cache, runtime, and the bring-up handoff (Plan: 51; Req: R15, R16)

## Phase 16 — Bring-up resilience and Rust specialist
- [x] Extract `is_duckln_synthetic_line`, `is_shell_prompt_line`, `strip_synthetic_and_prompt_lines` into `src/duckln/repair_intake.py` as the shared source of truth (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-1, REQ-BRINGUP-LOOP-2)
- [x] Stop feeding `failure_message` to the recovery classifier when stderr/stdout are empty (`repo_bringup.py:5165`, `_apply_recovery_decision`) (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-1)
- [x] Scrub synthetic/shell-prompt lines in `DebugRecoverySpecialist._classify_failure` before substring matching (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-1)
- [x] Add `_sanitize_error_lines` in `web_runtime.py` and wire it into `_build_search_query` + `_exact_error_query_fragment` so shell prompts and Duckln narrative never leak into search queries (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-2)
- [x] Add retry-once + per-provider diagnostic in `_search_ddg_candidates` and `_search_bing_candidates`; fall back to Bing on connection failures; expose `describe_search_failure` to callers (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-2)
- [x] Replace the generic "could not reach DuckDuckGo" message with the specific diagnostic from `describe_search_failure` (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-2)
- [x] In `infer_readme_workflow_steps`, always run the LLM classifier and prefer its result over the heuristic when it produces ≥1 setup step (Plan: 52; Req: R17, REQ-BRINGUP-README-1)
- [x] Widen `_readme_workflow_snippet` to send the full README (cap 16k chars with head+tail slicing) (Plan: 52; Req: R17, REQ-BRINGUP-README-1)
- [x] Loosen `_is_valid_llm_readme_command` to allow `&&`/`;`/`||` chains when each segment passes the safety assessor, while still blocking pipe-into-shell and destructive patterns (Plan: 52; Req: R17, REQ-BRINGUP-README-1)
- [x] Add `RepoFamily.RUST`, `RustRepoSetupSpecialist`, register in `RepoBringUpSupervisor`, update `classify_repo_family` + `_suggest_alternate_repo_family` (Plan: 52; Req: R17, REQ-RUST-1)
- [x] Add Rust subagent runtime descriptor + contract definition in `src/duckln/subagents.py` (Plan: 52; Req: R17, REQ-RUST-1)
- [x] New playbook `src/agent/playbooks/rust.md` covering allowed setup paths, run verification, guardrails, cross-platform notes (Plan: 52; Req: R17, REQ-RUST-1)
- [x] Tests: `test_repo_bringup_recovery_input.py`, `test_web_runtime_query_sanitization.py`, `test_repo_bringup_readme_llm.py`, `test_repo_bringup_rust.py` (Plan: 52; Req: R17)

## Phase 17 — Self-skill-acquisition after successful repair
- [ ] Add `build_repair_skill_prompt()` to `src/duckln/prompts.py`; strip synthetic lines from `error_output` via `strip_synthetic_and_prompt_lines` before building prompt (Plan: 53; Req: R18, REQ-SKILL-SELF-1)
- [ ] Add `_generate_repair_skill_note()` to `src/duckln/repo_bringup.py`; call it inside `_apply_recovery_decision` after verification passes on a recovery attempt; return `(slug, title, summary)` or `None` (Plan: 53; Req: R18, REQ-SKILL-SELF-1)
- [ ] Add `_offer_skill_approval()` to `src/duckln/main.py`; HITL/HOTL gate displays skill and requires explicit yes; HOOTLWO auto-persists and prints confirmation (Plan: 53; Req: R18, REQ-SKILL-SELF-2, REQ-SKILL-SELF-3)
- [ ] Wire `_generate_repair_skill_note` + `_offer_skill_approval` into the bring-up recovery call chain in `main.py` (Plan: 53; Req: R18, REQ-SKILL-SELF-1, REQ-SKILL-SELF-2, REQ-SKILL-SELF-3)
- [ ] Enrich existing post-deploy skill write at `main.py:5945` to include executed command sequence and verification command (Plan: 53; Req: R18, REQ-SKILL-SELF-1)
- [ ] Extend `inspect_repo_for_bringup()` in `repo_bringup.py` to scan `memory/skills/` for slugs matching the repo family and pass first match as `prior_skill_hint` to specialist `infer_steps()` (Plan: 53; Req: R18, REQ-SKILL-SELF-4)
- [ ] Tests: `test_repair_skill_generation.py`, `test_repair_skill_approval.py`, `test_skill_prior_hint.py` (Plan: 53; Req: R18, REQ-SKILL-SELF-1, REQ-SKILL-SELF-2, REQ-SKILL-SELF-3, REQ-SKILL-SELF-4)
- [x] Tests: `test_repo_bringup_recovery_input.py`, `test_web_runtime_query_sanitization.py`, `test_repo_bringup_readme_llm.py`, `test_repo_bringup_rust.py` (Plan: 52; Req: R17, REQ-BRINGUP-LOOP-1, REQ-BRINGUP-LOOP-2, REQ-BRINGUP-README-1, REQ-RUST-1)

## Phase 18 — Break the bring-up infinite loop
- [ ] Add `parse_install_hint()` and `fingerprint_stderr()` to `src/duckln/repair_intake.py` (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-1, REQ-BRINGUP-NOLOOP-2)
- [ ] Wire `parse_install_hint` into `_prerequisite_recovery_plan` so OS-volunteered install commands beat hardcoded scripts; validate via `assess_command` (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-1)
- [ ] Add `seen_failures` dedup in `_execute_plan_with_bounded_recovery`; short-circuit on second identical `(command, exit_code, fingerprint)` (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-2)
- [ ] Surface `package.json` scripts (`dev`/`start`/`serve`/`preview`) in `_infer_start_command` before falling back to web search (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-5)
- [ ] Add per-attempt 90s budget to `_run_runtime_repair_workflow`; bail out with elapsed-seconds message before any new web call (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-3)
- [ ] Add 30s aggregate cap to the three sequential web calls in `_discover_repo_run_command` (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-3)
- [ ] Reconcile stale `active_runtime_execution_target` against `_session_execution_target` at the top of `_run_runtime_repair_workflow`; live session target wins (Plan: 54; Req: R19, REQ-BRINGUP-NOLOOP-4)
- [ ] Tests: `test_install_hint_parser.py`, `test_prereq_uses_os_hint.py`, `test_repo_bringup_no_repeat.py`, `test_runtime_repair_budget.py`, `test_stale_runtime_target.py`, `test_package_json_scripts_surfacing.py` (Plan: 54; Req: R19)

## Phase 19 — Fix the runtime repair loop and universal repo launch
- [ ] Extend `DUCKLN_SYNTHETIC_LINE_MARKERS` in `src/duckln/repair_intake.py` with Plan 54/55 narrative prefixes so they cannot leak into web search queries (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-3)
- [ ] Wire `parse_install_hint` into `_build_runtime_prerequisite_plan` in `src/duckln/main.py`; override the plan's `install_command` when the OS hint passes `assess_command` (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-2)
- [ ] Add module-level `_RUNTIME_PREREQUISITE_FAILED` dedup cache and `_record_runtime_install_failure` / `_runtime_install_recently_failed_same` / `_clear_runtime_install_failure` helpers in `src/duckln/main.py` (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-1)
- [ ] Short-circuit `_execute_runtime_prerequisite_install` with `stopped_repeated_failure` status on the second identical `(install_command, stderr_fingerprint)` failure; handle the new status in the orchestrator's prerequisite branch (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-1)
- [ ] Stream a `Duckln installing prerequisite: {command}` status line before each runtime install in `src/duckln/main.py` (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-5)
- [ ] Replace the generic `Do you want Duckln to solve the {repo} issue?` approve_prompt with an actionable `Duckln wants to run: ... / Reason: ... / Approve repair of {repo}? [y/n]` (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-4)
- [ ] Extend `_infer_start_command_from_files` in `src/duckln/repo_bringup.py` with `pyproject.toml` PEP 621 / Poetry scripts, Go `cmd/` sub-modules, and CMake `build/` executable detection; add `_infer_pyproject_script_command` helper (Plan: 55; Req: R20, REQ-RUNTIME-NOLOOP-6)
- [ ] Tests: `test_synthetic_markers.py`, `test_runtime_dedup.py`, `test_runtime_os_hint.py`, `test_runtime_approval_prompt.py`, `test_run_command_inference.py` (Plan: 55; Req: R20)

## Phase 20 — Fix the 15-minute runtime hang (Plan 56)

- [x] Set `should_offer_repair=False` in the dedup short-circuit return of `_execute_plan_with_bounded_recovery` in `src/duckln/repo_bringup.py` (Plan: 56; Req: R20, REQ-RUNTIME-NOLOOP-7)
- [x] Add `_DRAIN_TOTAL_BUDGET_SECONDS = 180` and a `drain_started_at` cumulative budget check in `_drain_terminal_runtime_incidents` in `src/duckln/main.py` (Plan: 56; Req: R20, REQ-RUNTIME-NOLOOP-8)
- [x] Add budget guard (`if _budget_exceeded()`) before `_attempt_runtime_prerequisite_install` in `_run_runtime_repair_workflow` in `src/duckln/main.py` (Plan: 56; Req: R20, REQ-RUNTIME-NOLOOP-3)
- [x] Tests: `test_bringup_stopped_repeat_no_repair.py`, `test_drain_total_budget.py`, `test_budget_before_prereq_install.py` (Plan: 56; Req: R20, REQ-RUNTIME-NOLOOP-7, REQ-RUNTIME-NOLOOP-8)

## Phase 21 — End-to-end README skill (Plan 57)

- [x] Create `src/duckln/readme_skill.py` with `extract_readme_metadata`, `ReadmePrerequisite`, `build_prerequisite_preflight_steps`, `detect_package_manager`. Known prereqs covered: node, npm, python, rust, cargo, uv, go, docker, git, make, cmake, pnpm, yarn, poetry (Plan: 57; Req: R21, REQ-README-SETUP-1)
- [x] Wire `_build_readme_prereq_preflight_steps` into `RepoSetupSpecialist.build_plan` so prereq preflight steps are prepended to README install/run steps (Plan: 57; Req: R21, REQ-README-SETUP-1)
- [x] Update playbook validator to accept `source="readme-prereq"` (Plan: 57; Req: R21, REQ-README-SETUP-1)
- [x] Map `SystemProbe.operating_system` → package manager in `detect_package_manager` (Plan: 57; Req: R21, REQ-README-SETUP-2)
- [x] Add `_runtime_readme_declared_extras` and enrich `_build_runtime_prerequisite_plan` reason line with README extras (Plan: 57; Req: R21, REQ-README-RUNTIME-1)
- [x] Add `_emit_repo_usage_summary_and_offer_demo` to surface usage info post-success in `_orchestrate_repo_to_running` (Plan: 57; Req: R21, REQ-README-USAGE-1, REQ-README-USAGE-2)
- [x] Persist usage info to skill memory alongside the existing verified-deploy-path skill (Plan: 57; Req: R21, REQ-README-USAGE-1)
- [x] Tests: `test_readme_skill.py`, `test_readme_prereq_in_build_plan.py`, `test_runtime_repair_readme_extras.py` (Plan: 57; Req: R21)

## Phase 22 — Target-aware prereqs + persistent failure memory (Plan 58)

- [x] Bug A: `_build_readme_prereq_preflight_steps` forces `effective_os="Linux"` for `vm`/`aws`/`gcp`/`ssh` (Plan: 58; Req: R22, REQ-PLAN58-1)
- [x] Bug B: `_scan_code_blocks_for_tools` + `_strip_prefixes_and_first_token` + shell-builtin skip list (Plan: 58; Req: R22, REQ-PLAN58-2)
- [x] Bug B integration: filter out unknown-tool prereqs before adding to plan steps (Plan: 58; Req: R22, REQ-PLAN58-3)
- [x] Bug C: `assess_command` blocks brew on Linux VM/cloud targets (Plan: 58; Req: R22, REQ-PLAN58-4)
- [x] Bug D: `write_failure_memory_state` + `read_failure_memory_state` + `lookup_recent_failure` in state/access.py; `FAILURES_DIR_NAME` in agent/memory.py (Plan: 58; Req: R22, REQ-PLAN58-5)
- [x] Bug D: planner consultation before adding prereq preflight steps with 24h window (Plan: 58; Req: R22, REQ-PLAN58-6)
- [x] Bug D: setup dedup + runtime install failure recorder both dual-write to persistent log (Plan: 58; Req: R22, REQ-PLAN58-7)
- [x] Tests: `test_readme_prereq_target_aware.py`, `test_readme_code_block_scan.py`, `test_safety_blocks_brew_on_linux_vm.py`, `test_persistent_failure_memory.py` + openclaw test update (Plan: 58; Req: R22)

## Phase 23 — Cross-repo + first-failure + LLM-refined prereqs (Plan 59)

- [x] Fix 1: `_GLOBAL_FAILURE_SLUG`, `_is_environmental_failure`, dual-write in `write_failure_memory_state`, fallback lookup in `lookup_recent_failure` (Plan: 59; Req: R23, REQ-PLAN59-1, REQ-PLAN59-2)
- [x] Fix 2: persist first-failure and recovery-exhausted in `_execute_plan_with_bounded_recovery` (Plan: 59; Req: R23, REQ-PLAN59-3)
- [x] Fix 3: `_llm_refine_readme_prereqs` + JSON parser + allow-list filter + cache helpers + wire into `_build_readme_prereq_preflight_steps` with fail-open semantics (Plan: 59; Req: R23, REQ-PLAN59-4, REQ-PLAN59-5, REQ-PLAN59-6)
- [x] Tests: `test_global_failure_memory.py`, `test_recovery_exhausted_persistence.py`, `test_llm_prereq_refinement.py` (Plan: 59; Req: R23)

## Phase 24 — User-local probes + None-safe exit_code (Plan 60)

- [x] Update `_KNOWN_PREREQUISITES` rust/cargo/uv/poetry probes to fall back to $HOME paths (Plan: 60; Req: R24, REQ-PLAN60-1)
- [x] None-safe int(result.exit_code) at all 4 sites in _execute_plan_with_bounded_recovery (Plan: 60; Req: R24, REQ-PLAN60-3)
- [x] Tests: test_userlocal_install_probes.py, test_int_exit_code_none_safe.py (Plan: 60; Req: R24)

## Phase 25 — Production-ready bring-up across OSes (Plan 61)

- [x] Fix A: Windows winget package-manager end-to-end (table, dataclass, detect, safety) (Plan: 61; Req: R25, REQ-PLAN61-A)
- [x] Fix B: classify_apt_failure + apt_failure_guidance + runtime wiring (Plan: 61; Req: R25, REQ-PLAN61-B)
- [x] Fix C: /failures slash command + failure_window_hours AppConfig field + lookup_recent_failure default (Plan: 61; Req: R25, REQ-PLAN61-C)
- [x] Fix D: readme_link_follower module + 2-link/24h cap (Plan: 61; Req: R25, REQ-PLAN61-D)
- [x] Fix E: clarify_prompts module (CLI + Textual adapters) (Plan: 61; Req: R25, REQ-PLAN61-E)
- [x] Fix F: extended LLM classifier schema + _parse_llm_readme_extras (Plan: 61; Req: R25, REQ-PLAN61-F)
- [x] Fix G: authoritative_sources module + unknown-tool guidance persistence (Plan: 61; Req: R25, REQ-PLAN61-G)
- [x] Tests: 7 new files (59 tests) + 5 existing files updated for new schema (Plan: 61; Req: R25)

## Phase 26 — Live indicators render correctly; bundled Nerd Font; `+` dropdown (Plan 63)

- [x] Fix 1: `_status_text` returns Rich Text via `.append(glyph, style)`; `_render_status_bar` passes Text through unchanged (Plan: 63; Req: R26, REQ-PLAN63-1)
- [x] Fix 2: removed `#internet-icon` from `#input-bar`; `_update_internet_icon` deleted (Plan: 63; Req: R26, REQ-PLAN63-2)
- [x] Fix 4a: `src/duckln/glyphs.py` with `nerdfonts` codepoints (Plan: 63; Req: R26, REQ-PLAN63-3)
- [x] Fix 4b: `src/duckln/font_setup.py` with per-OS auto-install + VS Code detection (Plan: 63; Req: R26, REQ-PLAN63-4, REQ-PLAN63-6)
- [x] Fix 4c: AppConfig.font_setup_acknowledged + main.py startup wiring (Plan: 63; Req: R26, REQ-PLAN63-5, REQ-PLAN63-6)
- [x] Fix 3: `#attach-overlay` Vertical + `show_plus_dropdown_overlay` + selection routing (Plan: 63; Req: R26, REQ-PLAN63-7)
- [x] Tests: test_glyphs.py, test_font_setup.py, test_status_text_segments.py, test_header_renders_text_not_markup.py, test_input_bar_layout.py + 2 existing exact-dict tests updated (Plan: 63; Req: R26)

## Phase 27 — Multi-Agent Harness Foundation (Plan 65)

- [x] Phase 1: Tool Registry — `ToolSpec`, `ToolRegistry`, `AgentContext`, 11 default tools, mode-aware availability, schema validation, path-traversal guard, S0 enforcement for probes, authoritative-URL guard for web.fetch (Plan: 65; Req: R27, REQ-PLAN65-1; 33 tests)
- [x] Phase 2: AgentDefinition + YAML-frontmatter loader + AgentRegistry + 6 pilot specs + tool-allowlist + can_spawn validation (Plan: 65; Req: R27, REQ-PLAN65-2; 31 tests)
- [x] Phase 3: `run_agent` single-agent reasoning loop + budget enforcement + state serialization for LLM + decision parser + display callback (Plan: 65; Req: R27, REQ-PLAN65-3; 25 tests)
- [x] Phase 4: MessageBus pub/sub + Coordinator parallel spawn via asyncio + ConcurrencyGate + budget cascade + partial results on timeout (Plan: 65; Req: R27, REQ-PLAN65-4; 25 tests)
- [x] Phase 5: TraceLogger + redaction + `/agents`, `/agents trace`, `/agents costs` slash commands (Plan: 65; Req: R27, REQ-PLAN65-5; 17 tests)
- [x] Phase 6a: DUCKLN_HARNESS feature flag + harness routing in `_execute_plan_with_bounded_recovery` exhausted branch + `build_default_llm_client_or_none` (Plan: 65; Req: R27, REQ-PLAN65-6)
- [x] Phase 6b: 4 recovery agent specs + `run_multi_agent_recovery` entry point (Plan: 65; Req: R27, REQ-PLAN65-6; 31 tests)
- [x] Full regression: 1378 tests pass, 0 failures (Plan: 65; Req: R27)

## Phase 29 — Plan Mode (Plan 67)

- [x] Phase B: 5 Plan Mode nerdfont glyphs (`PLAN_NOTE`, `PLAN_APPROVED`, `PLAN_REJECTED`, `PLAN_PENDING`, `PLAN_EDIT`) in `glyphs.py` (Plan: 67; Req: R29, REQ-PLAN67-7)
- [x] Phase C: `AppConfig.plan_mode_enabled: bool` with SQLite snapshot round-trip via `CONFIG_STATE_KEYS` (Plan: 67; Req: R29, REQ-PLAN67-1)
- [x] Phase N: 4 system prompts in `prompts.py` — `SYSTEM_PROMPT_PLAN_PROPOSE`, `..._CRITIQUE`, `..._CLARIFY`, `..._ATTRIBUTE` (Plan: 67; Req: R29, REQ-PLAN67-2)
- [x] Phase A+H: `src/duckln/plan_mode.py` — `PlanStep`, `PlanRecord`, `RepoUnderstanding`, `ClarificationQuestion`, five-stage pipeline, JSON parsers, markdown round-trip, attribution, amendment (Plan: 67; Req: R29, REQ-PLAN67-2, REQ-PLAN67-3, REQ-PLAN67-5)
- [x] Phase E: `state/access.py` plan persistence helpers + `PLAN_MODE_HISTORY_LIMIT=50` (Plan: 67; Req: R29, REQ-PLAN67-9)
- [x] Phase F: `render_plan_panel`, `render_plan_oneline`, `render_clarification_prompt`, `render_error_attribution` in `ui.py` (Plan: 67; Req: R29, REQ-PLAN67-7)
- [x] Phase M (specs): `supervisor.md` (updated), `repo_inspector.md`, `planner.md`, `critic.md` agent specs (Plan: 67; Req: R29, REQ-PLAN67-6)
- [x] Phase M (code): `harness/plan_supervisor.py` with `run_plan_supervisor`, `PlanSupervisorResult`, `REQUIRED_AGENT_NAMES`, `_local_plan_pipeline` fallback; exported from `harness/__init__.py` (Plan: 67; Req: R29, REQ-PLAN67-6)
- [x] Phase D+K: `/plan` slash command family registered in `get_slash_command_descriptors()` and dispatched via `_handle_plan_command` in `main.py`; `/plan edit` opens `$EDITOR` and re-parses (Plan: 67; Req: R29, REQ-PLAN67-1, REQ-PLAN67-8)
- [x] Phase I+J: `bring_up_selected_repo` Plan Mode branch (`_generate_plan_for_bringup`) + `resume_with_approved_plan` strict-adherence executor with `_attempt_amendment_and_halt` (Plan: 67; Req: R29, REQ-PLAN67-4, REQ-PLAN67-5)
- [x] Phase L: `_plan_mode_nudge_for_message` in `conversation_agent.py` for free-text multi-step requests (Plan: 67; Req: R29, REQ-PLAN67-1)
- [x] Phase G: `_plan_mode_header_segment` in `textual_ui.py` + header injection (Plan: 67; Req: R29, REQ-PLAN67-7)
- [x] Tests: test_plan_mode_understanding.py (5), test_plan_mode_pipeline.py (12), test_plan_mode_state.py (4), test_plan_mode_render.py (5), test_plan_mode_command.py (8), test_plan_mode_supervisor.py (3), test_plan_mode_attribution.py (5), test_plan_mode_integration.py (4) — 45 new tests; 2 existing tests updated for the new config field (Plan: 67; Req: R29)
- [x] Full regression: 1423 tests pass, 0 failures (Plan: 67; Req: R29)

## Phase 30 — Plan Mode bugfixes (Plan 68)

- [x] Fix 1: `+` dropdown Plan Mode toggle — `_compose_plan_mode_label`, dropdown option, `_handle_attach_selection` branch, `_toggle_plan_mode` in `textual_ui.py` (Plan: 68; Req: R29, REQ-PLAN68-2)
- [x] Fix 2: route initial setup through Plan Mode — short-circuit in `_orchestrate_repo_to_running`; pass `plan_mode_enabled` at the `/repos` VM-warning branch and `/explore` setup call (Plan: 68; Req: R29, REQ-PLAN68-1)
- [x] Fix 3a: target-agnostic generation — `gather_repo_understanding(override_detected_files=…)` + `_generate_plan_for_bringup` reads remote files via `inspect_remote_repo_setup_files` for vm/aws/gcp (Plan: 68; Req: R29, REQ-PLAN68-3)
- [x] Fix 3b: target-agnostic execution — `resume_with_approved_plan` reads live `execution_target`, accepts `vm_name`/`pane_executor`, wraps each step via `_wrap_command_for_execution_target` (Plan: 68; Req: R29, REQ-PLAN68-3)
- [x] Fix 3c: `/plan approve` threads `terminal_interface` + session target/vm_name into `resume_with_approved_plan`; pending amendment treated as pause not failure (Plan: 68; Req: R29, REQ-PLAN68-1, REQ-PLAN68-3)
- [x] Tests: test_plan_mode_targets.py (5) — orchestrate routing, local raw vs vm `multipass exec` wrapping, config-flip round-trip (Plan: 68; Req: R29)
- [x] Full regression: 1428 tests pass, 0 failures (Plan: 68; Req: R29)

## Phase 31 — Plan Mode hardening (Plan 69)

- [x] Fix 1: generous conversation timeout (`CONVERSATION_TIMEOUT_SECONDS`/`OLLAMA_CONVERSATION_TIMEOUT_SECONDS`) + `conversation_timeout_seconds` adapter attr + one cold-load retry in `ai_client.generate_reply` (Plan: 69; Req: R29, REQ-PLAN69-1)
- [x] Fix 2: `/plan edit` non-TTY flow (`_open_path_non_blocking`) + `/plan reload` (`_reload_pending_plan_from_disk`) + descriptor (Plan: 69; Req: R29, REQ-PLAN69-3)
- [x] Fix 3: remove `inspect_remote_repo_setup_files` from `_generate_plan_for_bringup`; `gather_repo_understanding(family_hint=…)` + `family_hint_from_metadata` (Plan: 69; Req: R29, REQ-PLAN69-2)
- [x] Fix 4: guaranteed dense 1..N numbering via `_renumber_steps` (already authoritative) + render-order test (Plan: 69; Req: R29, REQ-PLAN69-4)
- [x] Fix 5: branded `PLAN_NOTE` activity labels for `/plan*` in `_activity_message_for_slash_command` (Plan: 69; Req: R29, REQ-PLAN69-4)
- [x] Tests: test_ai_client.py (3), test_plan_mode_targets.py (4), test_plan_mode_command.py (2), test_plan_mode_pipeline.py (1); palette tuple updated (Plan: 69; Req: R29)
- [x] Full regression: 1437 tests pass, 0 failures (Plan: 69; Req: R29)
- [x] Fix 6: header not stuck on failed — `_plan_mode_header_segment` shows green "Plan: on" for terminal/non-actionable status; `_generate_plan_for_bringup` clears pending on failure (Plan: 69; Req: R29, REQ-PLAN69-5)
- [x] Fix 7: header target label consistent with footer — `initial_connection_type` derives from `_terminal_connection_context` so a stale vm runtime shows "Local" (Plan: 69; Req: R29, REQ-PLAN69-6)
- [x] Tests: test_plan_mode_targets.py HeaderStatusPlan69Test (3) — failed→green, pending→orange, stale vm→Local

## Phase 32 — Plan Mode reliability (Plan 70)

- [x] Fix 1: `_generate_plan_for_bringup` guard blocks only on actionable status; clears stale terminal/failed plan and regenerates (Plan: 70; Req: R29, REQ-PLAN70-1)
- [x] Fix 2: `_steps_from_candidates` + `_assemble_ordered_steps` make critique best-effort in `generate_plan` (Plan: 70; Req: R29, REQ-PLAN70-2)
- [x] Fix 2b: `run_plan_supervisor` uses `_assemble_ordered_steps` (Plan: 70; Req: R29, REQ-PLAN70-2)
- [x] Tests: test_plan_mode_pipeline.py (2 — critique-fallback→pending, propose-fail→failed), test_plan_mode_targets.py StalePlanGuardPlan70Test (2 — failed cleared+regenerates, actionable short-circuits) (Plan: 70; Req: R29)
- [x] Full regression: 1446 tests pass, 0 failures (Plan: 70; Req: R29)

## Phase 33 — Full Proper Plan Mode PRD (Plan 72)

- [x] Phase 1: grounded understanding + deterministic clone→install→build→run backbone + readable coloured panel (Plan: 72; Req: R30, REQ-PLAN72-1; test_plan_mode_grounding)
- [x] Phase 2: `plan_lifecycle.py` state machine + 4 terminal states (Plan: 72; Req: R30, REQ-PLAN72-2; test_plan_lifecycle)
- [x] Phase 3: mandatory supervisor critic verdict, disclosed skip, deterministic S4/run-less block (Plan: 72; Req: R30, REQ-PLAN72-3; test_plan_mode_critic)
- [x] Phase 4: Plan Mode defaults ON (load/onboarding) + plan-first chokepoint (Plan: 72; Req: R30, REQ-PLAN72-4; test_plan_first_enforcement)
- [x] Phase 5: redaction before all LLM payloads + learned skills (Plan: 72; Req: R30, REQ-PLAN72-5; test_plan_mode_redaction)
- [x] Phase 6: tightened HOOTLWO/executor mode policy (Plan: 72; Req: R30, REQ-PLAN72-6; test_plan_mode_hootlwo)
- [x] Phase 7: activity clears on terminal/exception (Plan: 72; Req: R30, REQ-PLAN72-2; test_plan_mode_activity_state)
- [x] Phase 8: verified-success skill extraction keyed by family+os+target, redacted (Plan: 72; Req: R30, REQ-PLAN72-7; test_plan_mode_learning)
- [x] Phase 9: de-duplicated, situation-built clarifications (Plan: 72; Req: R30, REQ-PLAN72-8; test_plan_mode_clarification)
- [x] Phase 10: consolidated PRD invariant gate + per-family backbone fixtures (Plan: 72; Req: R30; test_plan_mode_prd_invariants)
- [~] PARTIAL: VM/`/cloud`/Docker interactive handlers and runtime-repair are not yet fully rerouted through the lifecycle (chokepoint available; repo bring-up fully gated); hard spinner ceiling in the Textual render loop is a follow-up.

## Phase 34 — Plan-first partials + self-learning loop (Plan 73)

- [x] Phase A: `gate_mutation_or_draft` + wire `/vm`, `/cloud` create, runtime-repair entry guard (Plan: 73; Req: R31, REQ-PLAN73-1; test_plan73_partials)
- [x] Phase B: spinner hard ceiling `_activity_bar_segments`/`_ACTIVITY_HARD_CEILING_SECONDS` (Plan: 73; Req: R31, REQ-PLAN73-2; test_plan73_partials)
- [x] Phase C: `/skills` learned grouping + `/skills show`/`clear` + `/learn` + `/plan retry` + descriptors (Plan: 73; Req: R31, REQ-PLAN73-3, REQ-PLAN73-7)
- [x] Phase D Inject: matching verified skill becomes the plan backbone (Plan: 73; Req: R31, REQ-PLAN73-4; test_learning_loop)
- [x] Phase D Reflect: redacted `reflect_on_run`/`persist_reflection` on success+failure (Plan: 73; Req: R31, REQ-PLAN73-5)
- [x] Phase D Extract: failure-pattern `…-avoid` skill on irrecoverable; verified on success (Plan: 73; Req: R31, REQ-PLAN73-6)
- [x] Phase D Loop: `/plan retry` re-plans with injection (Plan: 73; Req: R31, REQ-PLAN73-7)
- [~] PARTIAL: `_handle_cloud_command` non-create mutation branches and `_drain_terminal_runtime_incidents` rely on the runtime-repair entry guard / chokepoint; the fully-interactive cloud auth wizard is gated at create-entry, deeper wizard rerouting is a follow-up.

## Phase 35 — Preparing-plan feedback, supervisor never fails, opt-in pre-check, quote-safe prereqs, thinking box (Plan 76 / R32)

- [x] Fix A: staged "Preparing the plan…"/"Supervisor is reviewing…" + rotating spinner verb (`_PROCESSING_VERBS`/`_processing_verb`/`_activity_bar_segments`) (Plan: 76; Req: R32, REQ-PLAN76-1; test_processing_words)
- [x] Fix B: `_deterministic_supervisor_review` + `critic_review` never returns `skipped` (Plan: 76; Req: R32, REQ-PLAN76-2; test_supervisor_review)
- [x] Fix C: `plan_precheck` config + `_plan_precheck_probe` + `_drop_present_prereqs` + `skip_clone` + `/plan precheck on|off|ask` (Plan: 76; Req: R32, REQ-PLAN76-3; test_plan_precheck)
- [x] Fix D: quote-safe `node|pnpm|yarn --version` prereq checks (Plan: 76; Req: R32, REQ-PLAN76-4; test_node_prereq_check)
- [x] Panel: drop S0/S1/S2 chips, plain ETA + step count in `render_plan_panel` (Plan: 76; Req: R32, REQ-PLAN76-5)
- [x] Fix E: collapsible `#thoughts-panel` in chat pane + `add_thought`/`clear_thoughts`/`toggle_thoughts` + `_emit_thought`/`_chat_supports_thoughts` reasoning routing (Plan: 76; Req: R32, REQ-PLAN76-6; test_thoughts_box)

## Phase 36 — Version-aware pre-check, approve once, no per-step re-prompts, live thinking, web self-repair (Plan 77 / R33)

- [x] Fix 1: `_detect_required_node_version` (engines/.nvmrc/README) + `_fetch_github_raw_file` + `_node_major_from_spec` + probe `node --version` (`present_versions`) + `_node_version_install_step` injected when target<required (Plan: 77; Req: R33, REQ-PLAN77-1; test_runtime_version_detect)
- [x] Fix 2: `_step_allowed_in_mode(plan_approved, step_origin)` — approved planner steps skip the prompt, S4 still blocked, amendments still prompt; `resume_with_approved_plan` passes the flags (Plan: 77; Req: R33, REQ-PLAN77-2; test_plan_step_authorization)
- [x] Fix 3: emit `_think(...)` before each blocking call in `_generate_plan_for_bringup` (Plan: 77; Req: R33, REQ-PLAN77-3)
- [x] Fix 4: `_web_evidence_for_failed_step` + `attribute_failure(web_evidence=…)` second pass + `/internet on` guidance when off (Plan: 77; Req: R33, REQ-PLAN77-4; test_web_self_repair)

## Phase 37 — Enforced supervisor reasoning (Plan 78 / R34)

- [x] Fix A: `_model_connection_error` + `critic_review` model-unreachable block + `_generate_plan_for_bringup` surfaces "not able to connect to the model" (Plan: 78; Req: R34, REQ-PLAN78-1; test_model_connection)
- [x] Fix B: `PlanStep` target/source/evidence_excerpt/cwd + assembly populates + panel "Where:" line (Plan: 78; Req: R34, REQ-PLAN78-2; test_planstep_evidence)
- [x] Fix C: deeper `_deterministic_supervisor_review` checks (verify/target on mutating, wrong-OS, duplicate-failure) + verdict prompt update (Plan: 78; Req: R34, REQ-PLAN78-3; test_supervisor_review)
- [x] Fix D: `_repair_plan_for_revise` + one re-review loop in `_generate_plan_for_bringup` (Plan: 78; Req: R34, REQ-PLAN78-4; test_replan_loop)
- [x] Fix E: `attribute_failure` stamps target/cwd/verify + `_attempt_amendment_and_halt` re-reviews amendment via `critic_review` (Plan: 78; Req: R34, REQ-PLAN78-5; test_amendment_review)
- [x] Fix F: `_generate_plan_for_bringup` internal-bug guard → `duckln_internal_bug_reported` + `/repos` plan-mode `finally` clears spinner (Plan: 78; Req: R34, REQ-PLAN78-6)
- [x] Fix G: `redact_sensitive_data` masks Authorization headers + OAuth code/state/token values (Plan: 78; Req: R34, REQ-PLAN78-7; test_diagnostics)

## Phase 38 — No garbage response ever; resume the last operation (Plan 79 / R35)

- [x] Fix 1: `critic_review` retries once then discards a garbage model response; deterministic structural review is authoritative; garbage never used (Plan: 79; Req: R35, REQ-PLAN79-1; test_model_connection)
- [x] Fix 2: resume phrases in `_looks_like_runtime_repair_request` + plan-mode-aware `_handle_pending_repo_followup` set_up_repo branch + `/repos` persists `set_up_repo` next action in Plan Mode (Plan: 79; Req: R35, REQ-PLAN79-2; test_resume_operation)

## Phase 39 — Smarter, evidence-grounded planning (Plan 79b / R36)

- [x] L1: lockfile/package-manager mismatch revise + `_is_run` recognizes pnpm/yarn/bun (Plan: 79b; Req: R36, REQ-PLAN79-3; test_smart_planning)
- [x] L3/L7: `_infer_run_command` from package.json scripts (`_read_repo_package_json`/`_run_command_from_scripts`/`_package_manager_for`, prefer dev) (Plan: 79b; Req: R36, REQ-PLAN79-4; test_smart_planning)
- [x] L2: `_env_example_file` + assembler injects `cp .env.example .env` (Plan: 79b; Req: R36, REQ-PLAN79-5; test_smart_planning)
- [x] L5: `_evidence_line_for` attaches README line + panel "Source:" (Plan: 79b; Req: R36, REQ-PLAN79-6; test_smart_planning)
- [x] L6: `_verification_for_run_command` URL-vs-alive (Plan: 79b; Req: R36, REQ-PLAN79-7; test_smart_planning)
- [x] L4: `_extract_served_url`/`_vm_ipv4` + surfaced after run step (Plan: 79b; Req: R36, REQ-PLAN79-8; test_smart_planning)
- [x] L8: `state.access.write_repo_facts`/`read_repo_facts` + record on success + prefer on draft (Plan: 79b; Req: R36, REQ-PLAN79-9; test_smart_planning)

## Phase 40 — Robust & legible (Plan 80 / R37)

- [x] Fix 1: `_framework_node_requirement` (Plan: 80; Req: R37, REQ-PLAN80-1; test_plan80_robustness)
- [x] Fix 5: generic py/go/rust/.tool-versions detection + install steps + probe + injection loop + SETUP_FILE_ORDER (Plan: 80; Req: R37, REQ-PLAN80-2; test_plan80_robustness)
- [x] Fix 6: `diagnostics.match_deterministic_fix` consulted before LLM in `_attempt_amendment_and_halt` (Plan: 80; Req: R37, REQ-PLAN80-3; test_plan80_robustness)
- [x] Fix 9: `write_common_lesson`/`read_common_lessons` + `_common_lesson_fix`/`_record_common_lesson_from_fix` + seed + draft surface (Plan: 80; Req: R37, REQ-PLAN80-4; test_plan80_robustness)
- [x] Fix 7: `web_runtime.gather_repair_fix` (live `Browsing <url>` + real page fetch + bounded) (Plan: 80; Req: R37, REQ-PLAN80-5; test_plan80_robustness)
- [x] Fix 2: per-step `☑` + "✅ Plan executed — your repo is ready! 🎉" + `/plan approve` shows message (Plan: 80; Req: R37, REQ-PLAN80-6; test_plan_execution_ux)
- [x] Fix 3: `resume_with_approved_plan(emit_thought=…)` live execution thoughts (Plan: 80; Req: R37, REQ-PLAN80-6; test_plan_execution_ux)
- [x] Fix 4: `config.duckln_ui` + `build_chat_interface(ui_mode=…)` inline + `/ui` (Plan: 80; Req: R37, REQ-PLAN80-7; test_ui_mode)
- [x] Fix 8: `format_thought_for_seconds` before answers + `render_main_task_header` + `render_step_line(indent)` (Plan: 80; Req: R37, REQ-PLAN80-8; test_ui_mode)

## Phase 41 — Robust setup for ANY repo (Plan 81 / R38)

- [x] Fix 1: `_parse_env_example_keys`/`_required_env_vars` + env-copy step names secrets (Plan: 81; Req: R38, REQ-PLAN81-1; test_plan81_any_repo)
- [x] Fix 2: `_detect_project_subdir` + `project_subdir` cwd threading + SETUP_FILE_ORDER markers (Plan: 81; Req: R38, REQ-PLAN81-2; test_plan81_any_repo)
- [x] Fix 3: `_compose_services` + `docker compose up -d` step / docker_available gate (Plan: 81; Req: R38, REQ-PLAN81-3; test_plan81_any_repo)
- [x] Fix 4: `_codegen_steps` (prisma/graphql/build) before run (Plan: 81; Req: R38, REQ-PLAN81-4; test_plan81_any_repo)
- [x] Fix 5: broadened `match_deterministic_fix` (prisma/native-lib/playwright/service) (Plan: 81; Req: R38, REQ-PLAN81-5; test_plan81_any_repo)
- [x] Fix 6: `_vm_port_reachable`/`_port_from_url` + reachability guidance in `resume_with_approved_plan` (Plan: 81; Req: R38, REQ-PLAN81-6; test_plan81_any_repo)
- [x] Fix 7: private-repo `DeterministicFix(block=True)` + halt-with-question in `_attempt_amendment_and_halt` (Plan: 81; Req: R38, REQ-PLAN81-7; test_plan81_any_repo)
- [x] Fix 8: UI smoke (inline build + rich panel render) (Plan: 81; Req: R38, REQ-PLAN81-8; test_ui_smoke)

## Phase 42 — Correct, fast, legible pre-check (Plan 82 / R39)

- [x] Fix A+B: one-shot `_plan_precheck_probe` + `_build_precheck_script`/`_parse_precheck_block`/`_runtime_major` (Plan: 82; Req: R39, REQ-PLAN82-1, REQ-PLAN82-2; test_precheck_probe)
- [x] Fix B: version-compare treats "0"/unparseable as unknown (Plan: 82; Req: R39, REQ-PLAN82-2; test_precheck_probe)
- [x] Fix C: `emit_thought` threaded into probe + role-labelled staged thoughts (Plan: 82; Req: R39, REQ-PLAN82-3; test_precheck_probe)
- [x] Fix D: probe timeout + activity narration on timeout (Plan: 82; Req: R39, REQ-PLAN82-4)

## Phase 43 — Lean plans + pre-check/target visibility + smart API-key entry (Plan 83 / R40)

- [x] Fix 1: `_drop_satisfied_prereqs` (verify-steps + version-managed) + satisfied set after version blocks (Plan: 83; Req: R40, REQ-PLAN83-1; test_plan83_lean)
- [x] Fix 2: `PlanRecord.precheck_summary`/`target_label`/`required_env_keys` + `_format_precheck_summary`/`_target_label` + panel Target/Pre-check lines (Plan: 83; Req: R40, REQ-PLAN83-2; test_plan83_lean)
- [x] Fix 4: `_classify_required_env` + `offer_env_key_setup` (provider ask + write .env redacted) + `_handle_plan_command` threads prompts (Plan: 83; Req: R40, REQ-PLAN83-3; test_plan83_lean)

## Phase 44 — End-to-end lock-in for lean plans (Plan 84 / R40)

- [x] Integration test drives the full `_generate_plan_for_bringup_impl` draft path: present-tool prereqs ("Ensure git/npm", "Verify README prerequisite") dropped, Node-20 install kept, `precheck_summary`/`target_label`/`required_env_keys` populated (Plan: 84; Req: R40, REQ-PLAN83-1/2/3; test_draft_lean_integration). NOTE: a long-running `duckln` process must be RESTARTED to pick up source changes — screenshots showing old behavior were a pre-Plan-83 session, not a code bug.

## Phase 45 — Idempotent clone + accurate diagnosis + continuous progress (Plan 85 / R41)

- [x] Fix 0: `_plan_precheck_probe` fallback `test -d .git` so already_cloned is reliable (Plan: 85; Req: R41, REQ-PLAN85-1; test_plan85_clone_and_diagnosis)
- [x] Fix 1: self-guarding idempotent clone command (Plan: 85; Req: R41, REQ-PLAN85-1; test_plan85_clone_and_diagnosis)
- [x] Fix 2: `match_deterministic_fix` ALREADY_PRESENT + stderr-driven PRIVATE_REPO (no bare exit-128) + publickey defer (Plan: 85; Req: R41, REQ-PLAN85-2; test_plan85_clone_and_diagnosis)
- [x] Fix 3: progress lines before network grounding + critic review (Plan: 85; Req: R41, REQ-PLAN85-3)

## Phase 46 — Dev server success + local-browser open + crash/spam fixes (Plan 86 / R42)

- [x] Fix 1: `DUCKLN_WARNING`→`DUCKLN_ORANGE` (amendment-panel crash) (Plan: 86; Req: R42, REQ-PLAN86-3; test_plan86_run_server)
- [x] Fix 2: `_launch_and_await_server` (detached + readiness poll) — serving=success, crash=amendment (Plan: 86; Req: R42, REQ-PLAN86-1; test_plan86_run_server)
- [x] Fix 5: `_ensure_host_bind`/`_host_reachable_url`/`_open_in_local_browser` — 0.0.0.0 bind + host URL + local browser + remedy (Plan: 86; Req: R42, REQ-PLAN86-2; test_plan86_run_server)
- [x] Fix 4: confirming "now serving" thought closes recovery loop (Plan: 86; Req: R42, REQ-PLAN86-1)
- [x] Fix 3: `_capture_terminal_urls` dedupe + `_is_infra_url` host filter (Plan: 86; Req: R42, REQ-PLAN86-4; test_link_capture_filter)

## Phase 47 — Understand README first + archetype-correct run + cloud GUI streaming + legible thinking (Plan 87 / R43)

- [x] Fix 0: `RequirementsSpec` + `extract_requirements_spec` (grounded README understanding, reconcile, LLM-add-only + deterministic fallback) drafted before recommend; persisted via `write_repo_facts` (Plan: 87; Req: R43, REQ-PLAN87-0; test_requirements_spec, test_agent_thoughts)
- [x] Fix 0b: `_detect_prose_services` + `_db_provision_steps` provision README-prose DBs, injected before run (Plan: 87; Req: R43, REQ-PLAN87-3; test_requirements_spec)
- [x] Fix 1: `_detect_app_archetype` + archetype-aware `_run_command_from_scripts`/`_desktop_run_command_from_scripts`/`_infer_run_command` — desktop runs native shell, not frontend (Plan: 87; Req: R43, REQ-PLAN87-1; test_app_archetype, test_agent_thoughts)
- [x] Fix 1b: `_desktop_stream_steps`/`_launch_desktop_stream`/`_novnc_url` + desktop branch in `resume_with_approved_plan` — Xvfb+noVNC stream on remote, native window on local (Plan: 87; Req: R43, REQ-PLAN87-2; test_desktop_stream, test_agent_thoughts)
- [x] Fix 2: agent-handoff thoughts in draft/execute + `_THOUGHTS_MAX`=40 (Plan: 87; Req: R43, REQ-PLAN87-4; test_agent_thoughts)
- [x] Fix 3: activity one-liner kept alive + `_VERB_DWELL_TICKS` slow verb (Plan: 87; Req: R43, REQ-PLAN87-4; test_thought_pacing)
- [x] Fix 4: `_release_due_thoughts` pacing + drain interval, no thought lost (Plan: 87; Req: R43, REQ-PLAN87-4; test_thought_pacing)

## Phase 48 — Tauri/desktop app actually builds & runs on the VM (Plan 88 / R44)

- [x] Fix 1: full Tauri build pkg set + `_tauri_toolchain_steps` (rustup + Tauri CLI) before run; electron no Rust (Plan: 88; Req: R44, REQ-PLAN88-1; test_desktop_stream)
- [x] Fix 2: `_desktop_launch_command` headless webkit flags + cargo PATH; noVNC-only (Plan: 88; Req: R44, REQ-PLAN88-2; test_desktop_stream)
- [x] Fix 3: pre-check `DESKTOP:tauri|electron` marker + draft reclassify + run-step rewrite to native shell (Plan: 88; Req: R44, REQ-PLAN88-3; test_precheck_probe, test_agent_thoughts)
- [x] Fix 4: Tauri readiness window 300s + compiling note, no false-fail (Plan: 88; Req: R44, REQ-PLAN88-4; test_desktop_stream)
- [x] Fix 5: blocked install → honest amendment (no dead URL) (Plan: 88; Req: R44)

## Phase 49 — Survive OOM, stop honestly, headless routing, idempotent setup (Plan 89 / R45)

- [x] Fix 1: `ErrorCategory.OUT_OF_MEMORY` + deterministic OOM rule (swap + jobs=1), benign-memory guard (Plan: 89; Req: R45, REQ-PLAN89-1; test_oom_fix)
- [x] Fix 2: probe RAM/swap + proactive swap + single-job build for low-RAM Tauri (Plan: 89; Req: R45, REQ-PLAN89-2; test_precheck_probe, test_plan89_helpers, test_agent_thoughts)
- [x] Fix 3: robust detached PID capture (PATH prefix, no `;`) + idle auto-clear of "Working…" (Plan: 89; Req: R45, REQ-PLAN89-3; test_desktop_stream)
- [x] Fix 4: `_target_is_headless` ($DISPLAY) routes desktop apps to Xvfb/noVNC regardless of local/remote (Plan: 89; Req: R45, REQ-PLAN89-4; test_plan89_helpers)
- [x] Fix 5: `_idempotent_guard` (npm ci/.env/apt) so re-runs skip finished work (Plan: 89; Req: R45, REQ-PLAN89-5; test_plan89_helpers, test_agent_thoughts)

## Phase 50 — Repo agent foundation: answer questions about the active repo (Plan 90 Phase 1 / Plan 91 / R46)

- [x] Repo-scoped tool-use runtime `repo_agent.run_repo_agent` + `harness/agents/repo_qa.md` (read-only tools, JSON-contract loop) (Plan: 91; Req: R46, REQ-PLAN91-1; test_repo_agent)
- [x] Grounded, file-citing answer synthesized from observations (Plan: 91; Req: R46, REQ-PLAN91-2; test_repo_agent)
- [x] Workspace confinement via path-traversal guard; `../` rejected (Plan: 91; Req: R46, REQ-PLAN91-3; test_repo_agent::test_path_traversal_is_blocked)
- [x] `/ask` wired into `handle_session_command`; honest no-provider/no-clone/remote messages (Plan: 91; Req: R46, REQ-PLAN91-1/4; test_repo_agent::test_no_llm_configured_is_honest)
- [x] Tool calls stream into the thinking box (Plan: 91; Req: R46, REQ-PLAN91-5; test_repo_agent)

## Phase 51 — Repo filesystem write/edit/search + offload (Plan 92 / R47)
- [x] fs.search/fs.glob/fs.write/fs.edit, path-confined (Plan: 92; Req: R47, REQ-PLAN92-1; test_repo_phases::TestFilesystemTools)
- [x] `_maybe_offload` large results → workspace file + head/tail (Plan: 92; Req: R47, REQ-PLAN92-2; test_repo_phases::test_large_result_offloads_to_file)

## Phase 52 — Repo actions + engineer agent (Plan 93 / R48)
- [x] repo.run + git.run (push/force blocked); repo_engineer.md; `/do` (Plan: 93; Req: R48, REQ-PLAN93-1/2; test_repo_phases::TestOrchestratedTask, test_git_blocks_push)
- [x] action-summary synthesis (honest pass/fail) (Plan: 93; Req: R48, REQ-PLAN93-2; test_repo_phases)

## Phase 53 — Subagent delegation, isolated contexts (Plan 94 / R49)
- [x] run_repo_task delegates Explorer→Engineer in separate run_agent contexts (Plan: 94; Req: R49, REQ-PLAN94-1; test_repo_phases::test_delegate_explorer_then_engineer_edits)

## Phase 54 — Configurable HITL + pluggable memory (Plan 95 / R50)
- [x] dispatch approval gate + per-tool policy; `/policy`; persist (Plan: 95; Req: R50, REQ-PLAN95-1; test_repo_phases::TestApprovalGate, test_tool_policy_roundtrip)
- [x] MemoryStore Protocol + set_state_store_factory (Plan: 95; Req: R50, REQ-PLAN95-2; test_repo_phases::test_pluggable_store_factory)
- [x] per-repo Q&A memory recall + user prefs; `/prefs` (Plan: 95; Req: R50, REQ-PLAN95-3; test_repo_phases::test_repo_memory_persists_and_trims, test_user_prefs_roundtrip)

## Phases 55–60 — Robust everywhere + polish (Plan 96 / Plans 97–102 / R51–R56)
- [x] P55/P97: container target + env-agnostic fs.* (vm/container/cloud), confined; live-verified on duckln-vm (Req: R51, REQ-PLAN97-1/2; test_repo_phases::TestRemoteFsCommands)
- [x] P56/P98: expose_app_and_open (all targets) + app.serve + cloud tunnel + container port (Req: R52, REQ-PLAN98-1; test_repo_phases::TestExposeAppAndOpen)
- [x] P57/P99: native function-calling schema+parse + run_agent tool_decider (Req: R53, REQ-PLAN99-1; test_native_tools)
- [x] P58/P100: run_parallel_explorers isolated-context fan-out (Req: R54, REQ-PLAN100-1; test_repo_phases456::TestParallelExplorers)
- [x] P59/P101: ranked repo-memory recall + FileMemoryStore backend (Req: R55, REQ-PLAN101-1; test_repo_phases456::TestRankedMemory)
- [x] P60/P102: auto-route intent + _maybe_autoroute_repo_agent + git default-branch guard (Req: R56, REQ-PLAN102-1/2; test_repo_phases456::TestIntentClassifier, test_repo_phases::test_git_blocks_push)

## Phases 61–66 — depth gaps (Plan 103 / Plans 104–109 / R57–R62)
- [x] P61/P104: context compaction + token estimator (Req: R57, REQ-PLAN104-1; test_context_budget)
- [x] P62/P105: model windows + dedup observations (Req: R58, REQ-PLAN105-1; test_context_budget)
- [x] P63/P106: map-reduce over large files (Req: R59, REQ-PLAN106-1; test_context_budget::TestMapReduce)
- [x] P64/P107: plan-first TODO before acting (Req: R60, REQ-PLAN107-1; test_repo_plan103::TestPlanFirstTodo)
- [x] P65/P108: live A2A via MessageBus (Req: R61, REQ-PLAN108-1; test_repo_plan103::TestLiveA2A)
- [x] P66/P109: skill distillation + ranked recall (Req: R62, REQ-PLAN109-1; test_repo_plan103::TestSkillDistillation)

## Phase 67 — real-error attribution + generalized OOM + target label + guardrails (Plan 110 / R63)
- [x] Fix 1: launcher returns log_tail; death branches feed real error to attribution (Req: REQ-PLAN110-1; test_plan110::TestRealErrorAttribution)
- [x] Fix 2: _is_heavy_build + generalized proactive swap (Req: REQ-PLAN110-2; test_plan110::TestHeavyBuildDetection, test_agent_thoughts)
- [x] Fix 4: composite target label (Req: REQ-PLAN110-3; test_plan110::TestCompositeLabel, test_textual_ui)
- [x] Fix 6: low-RAM resize recommendation (Req: REQ-PLAN110-5)
- [x] Fix 7: weak-model plausibility guard (Req: REQ-PLAN110-4; test_plan110::TestPlausibilityGuard)
- [x] Fix 8: amendment retry re-runs (resume-from-start + idempotent guards)

## Phase 68 — Resource sub-agent (Plan 111 / R64)
- [x] resource_manager: probe (target+host), crunch detect/from-error, derived estimate, host-bounded+cost-aware recommend, apply_resize, resize cmds (Req: R64, REQ-PLAN111-1/2/3/4; test_resource_manager)
- [x] diagnostics DISK_FULL/RESOURCE_LIMIT (Req: R64)
- [x] reactive _handle_resource_crunch — waiting_on_user pause, no hard-cap, logged (Req: R64, REQ-PLAN111-5)
- [x] /resources command + write/read_resource_event log (Req: R64, REQ-PLAN111-5; test_resource_manager::TestResourceLog)
- [x] live VM probe verified (duckln-vm 96% disk) (Req: R64, REQ-PLAN111-1)

## Phase 69 — to-99% production (Plan 112 / R65)
- [x] P113: full declared build sequence (tauri.conf + prebuild scripts) (Req: REQ-PLAN113-1; test_plan112::TestPrebuildSequence)
- [x] P114: container docker-update resize (Req: REQ-PLAN114-1; test_plan112::TestContainerResize)
- [x] P115: cloud resize builders (Req: REQ-PLAN115-1; test_plan112::TestCloudResize)
- [x] P117: proactive disk-crunch warning (Req: REQ-PLAN117-1)
- [x] P118: integration matrix scaffold + opt-in runner + gate (Req: REQ-PLAN118-1)
- [ ] P116: native-FC live wiring + deterministic-fix expansion (deferred — needs real API key)
- [ ] P118: wire+curate matrix rows to live bring-up (ongoing — measures the 99%)

### Phase 70 — Plan 119 (R66) real-agent reliability
- [x] P119.1: truthful provider dot (Req: REQ-PLAN119-1; test_plan119::TruthfulProviderStatus)
- [x] P119.2: mandatory-but-correct LLM gate (Req: REQ-PLAN119-2; test_plan119::ProviderGate)
- [x] P119.3: JSON-mode + retry + truncation repair attribution (Req: REQ-PLAN119-3; test_plan119::{TruncationRepair,AttributionCall,JsonModePayload})
- [x] P119.4: polyglot ordering + deterministic venv fix (Req: REQ-PLAN119-4; test_plan119::{DeterministicVenvFix,SecondaryEcosystemOrdering})
- [x] P119.5: Ollama pull single progress bar (Req: REQ-PLAN119-5; test_plan119::OllamaProgress)

### Phase 71 — Plan 120 (R67) target-side polyglot ordering
- [x] P120: target-side backend venv setup before prebuild (Req: REQ-PLAN120-1; test_plan119::SecondaryEcosystemOrdering)

### Phase 72 — Plan 121 (R68) truthful dot + masked key + proven connection
- [x] P121.1: adapter base_url fallback + cache-key (Req: REQ-PLAN121-1; test_plan119::TruthfulProviderStatus, test_plan121::CacheKeyBustsOnCredentialChange)
- [x] P121.2: masked secret pop-out + wiring (Req: REQ-PLAN121-2; test_plan121::MaskedSecretPrompt)
- [x] P121.3: real round-trip verification (Req: REQ-PLAN121-3; test_plan121::VerifyLiveReply)

### Phase 73 — Plan 122 (R69) venv-aware missing-module + 429 resilience
- [x] P122.1: venv-aware missing-module fix (Req: REQ-PLAN122-1; test_plan122::VenvAwareMissingModule)
- [x] P122.2: 429/503 backoff + clear message (Req: REQ-PLAN122-2; test_plan122::RateLimitBackoff)
- [x] P122.3: backend build/dev requirements (Req: REQ-PLAN122-3; test_plan122::BackendBuildRequirements)

### Phase 74 — Plan 123 (R70) empirical real-bring-up matrix
- [x] P123.0: cleanup (dead line, untrack/gitignore .pyc) (Req: REQ-PLAN123-2; test_plan123::CleanupRegression)
- [x] P123.1: real driver + runner + curated matrix (Req: REQ-PLAN123-1; test_plan123::{DriverPassFail,MatrixSanity})
- [x] measured local: flask-hello ✓, fastapi-min ✓ (2/2 pure-python rows)
- [ ] run vm/gpu/heavy rows (user machine) → fix surfaced gaps iteratively

### Phase 75 — Plan 124 (R71) macOS + AI/ML readiness
- [x] P124.1: Mac/ML deterministic rules (Req: REQ-PLAN124-1/2; test_plan124::{MacTorchWheel,MacCudaBlock,GatedHuggingFace,MacToolchain})
- [x] P124.2: pyenv fallback on macOS (Req: REQ-PLAN124-2; test_plan124::MacPyenvFallback)
- [x] P124.3: driver real probe + host-safe ML row; measured flask/fastapi/streamlit ✓ (Req: REQ-PLAN124-3)
- [ ] run node/go/rust/heavy-ML/CUDA rows (VM or brew-on-Mac) → iterate to ≥99%
- [~] bounded-process archetype: deferred until a real ML/CLI row proves it

### Phase 76 — Plan 125 (R72) framework-agnostic Mac ML
- [x] P125.1: TF-macOS recovery + framework-agnostic playbook (Req: REQ-PLAN125-1; test_plan125::TensorFlowAppleSilicon)
- [x] P125.2: torch-free MLX smoke; live PASS on this Mac (Req: REQ-PLAN125-2; test_plan125::MlxSmokeRow)

### Phase 77 — Plan 126 (R73) polyglot-desktop-sidecar class
- [x] P126.1: no repo-name hardcoding; class machinery signal-keyed (Req: REQ-PLAN126-1; test_plan126)
- [x] P126.2: name-independence regression tests + 2 public same-class matrix rows (Req: REQ-PLAN126-2)
- [ ] drive tauri-py-sidecar + electron-quickstart (+ JustHireMe) to a running desktop on a VM → fix each blocker generically

### Phase 78 — Plan 127 (R74) resumable re-runs + disk reclamation
- [x] P127.1: completion-aware npm guard + venv sentinel (Req: REQ-PLAN127-1; test_plan127::{CompletionAwareNpmGuard,CompletionAwareVenv})
- [x] P127.2: safe reclaim_commands + injected on ≥90% disk before heavy build (Req: REQ-PLAN127-2; test_plan127::DiskReclamation)

### Phase 79 — Plan 129 (R75) reasoning-based recovery
- [x] P129.1: recovery agent wrapper + wiring (reason before guess) (Req: REQ-PLAN129-1; test_plan129::{DecisionParsing,RecoveryAgentWrapper})
- [x] P129.2: blast-radius autonomy policy (Req: REQ-PLAN129-2; test_plan129::{BlastRadius,Autonomy})
- [x] P129.3: thinking log + --if-present prebuild (Req: REQ-PLAN129-3; test_plan129::{ThinkingLog,AbsentScriptIfPresent})
- [ ] VM-staged: multi-turn act→verify→iterate, auto-apply (autonomy), skip-continue executor threading — validate on the matrix

### Phase 80 — Plan 132 (R76) reasoning by default, production-complete
- [x] B infra: gate/bounds/budget/context/thinking (Req: REQ-PLAN132-1; test_plan132::{ReasoningGate,BoundedAgent,Budget})
- [x] A1 recovery default-on; A2 reasoning planning pass (Req: REQ-PLAN132-1/2; test_plan132::ReasoningPlanningPass)
- [x] C/D learning + telemetry + model-capability gate (Req: REQ-PLAN132-3; test_plan132::ModelCapabilityGate)
- [ ] E2 (VM): live loop end-to-end + measured matrix pass-rate (the production gate)

### Phase 81 — Plan 133 (R77) live-run fixes
- [x] F1 custom resize value (Req: REQ-PLAN133-1)
- [x] F2 resume + F3 build-scratch reclaim (Req: REQ-PLAN133-2; test_plan133::{ResumeState,BuildScratchReclaim})
- [x] F4 live-investigate planning + critic-sees-brief (Req: REQ-PLAN133-3)
- [x] F5 dot recolor + F6 logical-thinking.md + clickable link (Req: REQ-PLAN133-4; test_plan133::{DotRecolor,ThinkingLogFile})
- [x] F7a "Tool interrupted" emission on cancel/switch (Req: REQ-PLAN133-5; test_plan133::InterruptEmission)
- [x] F7b edit/diff cards: diff computed in fs.edit/fs.write, carried on TurnEvent.result, rendered as Claude-style edit card in chat (Req: REQ-PLAN133-6; test_plan133::EditDiffCard). Inline step timeline + recolored dots + expandable trace panel + clickable reasoning link already existed; full per-card click-to-expand widget reuses the existing trace-panel detail toggle — verified live in the TUI.

### Phase 82 — Plan 134 (R78) live-VM defects
- [x] F1 authoritative target chip: removed stdout-inference override in `_handle_terminal_stdout`; `_reassert_cloud_terminal_target_from_state` resolves VM→"local" (Req: REQ-PLAN134-1; test_plan134::HeaderTargetLabel)
- [x] F2 shared `_auto_apply_recovery_fix` for deterministic + reasoned branches — recognized repo-scoped fix auto-applies+verifies+resumes in HOOTLWO, proposes in HITL/HOTL, asks/blocks per blast radius (Req: REQ-PLAN134-2; test_plan134::AutoApplyRecoveryFix)
- [x] F3 target-side PyInstaller-from-spec install before the prebuild in `_SECONDARY_PY_SETUP_CMD` (Req: REQ-PLAN134-3; test_plan134::PlanTimeBuildToolScan)
- [ ] Live (VM): JustHireMe re-run — step 6 passes first time (PyInstaller pre-installed); residual undeclared tools self-heal; header reads "local" throughout

### Phase 83 — Plan 135 (R79) active agent: routing, real-error diagnosis, disk-full auto-heal, reasoning file, /plan continue, stuck timer, terminal queue
- [x] F1 route run/desktop commands (`_looks_like_run_command` incl. tauri/`npm run tauri dev`) + build-tier timeout (`_recommended_timeout_seconds`) + progress-aware readiness (`_launch_and_await_server` idle-on-log-growth) (Req: REQ-PLAN135-1; test_plan135::{RouteRunBuild,ProgressAwareReadiness})
- [x] F2 retry-with-more-time on a no-error build timeout; honest "took too long" message; mark done on completion (Req: REQ-PLAN135-2; test_plan135::RouteRunBuild)
- [x] F6 disk-full/OOM auto-heal in `_handle_resource_crunch` (reclaim+resize+rerun+resume on sandbox) (Req: REQ-PLAN135-3; test_plan135::DiskFullAutoHeal)
- [x] F4 `reason_about_plan`/`recover_failed_step_with_agent` mirror emit_thought + always surface link (Req: REQ-PLAN135-4; test_plan135::ReasoningFileAlwaysSurfaced)
- [x] F3 `/plan continue` verb + descriptors + post-amendment message (Req: REQ-PLAN135-5)
- [x] F7 clear activity spinner on pause/halt/done (`_is_activity_halting_message`) (Req: REQ-PLAN135-6; test_plan135::TerminalBusyAndActivity)
- [x] F5 terminal-busy queue (`run_command_in_terminal` enqueue + `_mark_terminal_idle` drain + `_looks_like_shell_prompt`) (Req: REQ-PLAN135-7; test_plan135::TerminalBusyAndActivity)
- [ ] Live (VM): JustHireMe re-run — `npm run tauri dev` runs to completion; disk-full auto-reclaims+resizes+resumes; no 30s misfire; no spinning timer; reasoning link clickable; `/plan continue` resumes; queued command runs when free

### Phase 84 — Plan 136 (R80) self-correct + reason deeply, never give up
- [x] F1 `sanitize_shell_command` (strip stray `/cd`/`/sudo`, backticks, `$ `; keep real abs paths) applied in `_wrap_command_for_execution_target`, the pane run boundary, and the recovery report (Req: REQ-PLAN136-1; test_plan136::SanitizeCommand)
- [x] F2 deterministic self-correct for 127/command-not-found slash-builtin typos in `match_deterministic_fix` (Req: REQ-PLAN136-2; test_plan136::DeterministicSelfCorrect)
- [x] F3 deepened `_RECOVERY_SYSTEM` (OS/privileges/access/least-privilege/validate before BLOCK) (Req: REQ-PLAN136-3; test_plan136::DeeperRecoveryReasoning)
- [x] F4 amendment cap counts only distinct (failed→fix) signatures; repeat halts honestly; reset on success (Req: REQ-PLAN136-4; test_plan136::AmendmentCapDistinctBlockers)
- [ ] Live (VM): the `apt --fix-broken install` recovery runs cleanly (no `/cd`), or auto-corrects; recovery investigates OS+sudo+access; no irrecoverable halt on a self-inflicted typo

### Phase 85 — Plan 137 (R81) detect & clean a half-install; reclaim partial state on retry
- [x] Fix1a `reclaim_commands` clears apt partial archives + stray `/tmp` partials (Req: REQ-PLAN137-1; test_plan137::PartialCleanupCommand)
- [x] Fix1b `reclaim_partial_before_retry` (+ `_partial_cleanup_command`) — full reclaim in `_handle_resource_crunch`; partial-only in `_auto_apply_recovery_fix`; build timeout left undisturbed (Req: REQ-PLAN137-1)
- [x] Fix2a precheck probes completion → `PARTIAL:node_modules|venv|build_cache|dpkg`; `_parse_precheck_block` collects them (Req: REQ-PLAN137-2; test_plan137::PrecheckDetectsHalfInstall)
- [x] Fix2b planner inserts an explicit "remove the half-installed deps (reclaim space)" step before the installs; resume via `_done_steps` (Req: REQ-PLAN137-2)
- [ ] Live (VM): restart a half-died bring-up — precheck reports the half-install, plan shows the cleanup step, it runs, then resumes from the failed step; `df -h` flat across retries

### Phase 86 — Plan 138 (R82) stop the cascade; read the real error; web search; clickable reasoning log
- [x] F1 `_desktop_launch_command` env-prefixed (`env VAR=val …`) so `nohup env …` runs — no exit-127 cascade (Req: REQ-PLAN138-1; test_plan138::ValidDetachedLaunch)
- [x] F2 apt-conflict deterministic rule (above node-gyp) + tightened MISSING_COMPILER + `_amendment_fix_is_plausible` rejects build-essential for apt conflict (Req: REQ-PLAN138-2; test_plan138::ReadTheRealError)
- [x] F3 executor `_think` mirrored to `logical-thinking.md` + `surface_thinking_link` at plan/step-success/fail/summary + TUI `_reasoning_link_renderable` clickable OSC-8 (Req: REQ-PLAN138-3; test_plan138::ClickableReasoningLog)
- [x] F4 `_web_evidence_for_failed_step` announces "🌐 Let me check the web…" / nudges `/internet on`; `_RECOVERY_SYSTEM` directs `web.search` (Req: REQ-PLAN138-4; test_plan138::WebSearchWhenStuck)
- [ ] Live (VM): `npm run tauri dev` launches under Xvfb/noVNC (no nohup-127, no Node-install detour); apt conflict diagnosed correctly; web search announced when stuck; reasoning link clickable at every major point

### Phase 87 — Plan 139 (R83) noVNC auto-connect
- [x] `_novnc_url` appends `?autoconnect=true&resize=scale&reconnect=true` (Req: REQ-PLAN139-1; test_plan139, test_desktop_stream::TestNovncUrl)
- [ ] Live (VM): opening the streamed link lands directly on the app (scaled), no manual Connect click

### Phase 88 — Plan 141 (R84) visible streamed app + openable reasoning log
- [x] F1 fluxbox in BASE `_desktop_stream_packages` + WM-start step after Xvfb (universal, idempotent) (Req: REQ-PLAN141-1; test_plan141::WindowManagerUniversal)
- [x] F2 `on_click` opens a clicked `file://` link; `_last_reasoning_path` tracked; `open_last_reasoning_file` (webbrowser); `/reasoning` command + descriptor (Req: REQ-PLAN141-2; test_plan141::ReasoningLinkOpens)
- [ ] Live (VM): after the build, noVNC shows the real app window (not black); clicking the reasoning link / `/reasoning` opens logical-thinking.md

### Phase 89 — Plan 142 (R85) world-class reasoning floor
- [x] F1 widen S0 read-only whitelist in `HOOTLWO_DIAGNOSTIC_PATTERNS` (perceive); mutations stay gated (Req: REQ-PLAN142-1; test_plan142::PerceiveS0)
- [x] F2 recovery agent gets the runtime/VM repo dir via `resolve_runtime_project_dir` on remote targets (Req: REQ-PLAN142-2)
- [x] F3 raise reasoning budget (12 turns/240s) + context (obs 8k / state 14k / full error) (Req: REQ-PLAN142-3; test_plan142::GroundedAndUnstarved)
- [x] F4 tolerant `_parse_recovery_report` (prose→decision) + `_format_investigation_chain` persisted + deeper `_RECOVERY_SYSTEM` (Req: REQ-PLAN142-4; test_plan142::ReasoningKeptDeepPersisted)
- [x] F5 'need a tool/MCP' outcome in the prompt (groundwork); MCP-into-agent bridge = follow-up (Req: REQ-PLAN142-5)
- [ ] Live (VM): recovery agent's ls/cat/uname/id + fs.read_file SUCCEED; logical-thinking.md shows a real root-cause chain (not "I couldn't read anything")

### Phase 90 — Plan 143 (R86) actually resolve a full VM disk
- [x] F1 `multipass_resize_commands` grows the guest FS (growpart+resize2fs via `multipass exec`, auto-detected device) after start (Req: REQ-PLAN143-1; test_plan143::ResizeGrowsGuestFilesystem)
- [x] F2 `reclaim_commands(aggressive=True)` clears target/ + cargo registry + node_modules + ~/.cache; routine reclaim unchanged (Req: REQ-PLAN143-2; test_plan143::AggressiveReclaim)
- [x] F3 `_handle_resource_crunch` re-probes free disk, gates the retry, honest pause with real numbers (Req: REQ-PLAN143-3)
- [x] F4 resize is never auto — asks the user (real approve) + suggests the minimum/recommended size (Req: REQ-PLAN143-4; test_plan143::ResizeAsksUser)
- [ ] Live (VM): disk-full → reclaim target/ + (user-approved) resize grows the FS → build advances past Step 6; if the VM truly can't grow, one honest message not a loop

### Phase 91 — Plan 144 (R87) reason through a multi-stage failure (venv ↔ PyInstaller oscillation)
- [x] F1 `apply_fix_and_verify` is progress-aware: chains `next_fix` when the error CHANGES, bounded by max_stages; `_auto_apply_recovery_fix` supplies the deterministic next-stage callback (Req: REQ-PLAN144-1; test_plan144::ProgressAwareVerify)
- [x] F2 `_partial_cleanup_command(aggressive=False)` keeps venv/node_modules (only scratch); `reclaim_partial_before_retry` ties aggressive removal to a crunch; MISSING_VENV fix stamps `.duckln-deps-ok` + installs spec-detected PyInstaller (Req: REQ-PLAN144-2; test_plan144::{CleanupKeepsTheVenvOnAFixReRun,MissingVenvFixIsComplete})
- [x] F3 `_is_marker_guarded_setup` excludes the venv setup from the resume done-skip so it re-runs/self-heals (Req: REQ-PLAN144-3; test_plan144::MarkerGuardedSetupRerunsOnResume)
- [ ] Live (VM): `build:sidecar` resolves in ONE recovery pass (venv + PyInstaller + marker) or chains venv→PyInstaller without the venv ever being deleted; no more venv ↔ PyInstaller alternation

### Phase 92 — Plan 145 (R88) evidence-backed, supervisor-challenged reasoning + deep logging
- [x] F2 `RecoveryReport.evidence` + parsers (JSON + prose) + tool-use-chain fallback + prompt requires evidence (Req: REQ-PLAN145-2; test_plan145::EvidenceField)
- [x] F3 `supervise_recovery_conclusion` + `_llm_supervisor_review` + bounded re-investigation loop in `recover_failed_step_with_agent`; unproven BLOCK/SKIP→EXHAUSTED, FIXED proves itself (Req: REQ-PLAN145-3; test_plan145::{SupervisorGate,SupervisorWiredIntoRecovery})
- [x] F5 structured recovery episode (symptom→evidence→fix→outcome) in `_auto_apply_recovery_fix` + `_attempt_amendment_and_halt` block/skip/halt/amend paths; pre-diagnosis stub removed (Req: REQ-PLAN145-5)
- [x] F1 deterministic matcher → HINT only in production (gated by reasoning_enabled); agent reasons first with the hint; no offline/one-shot fallback → honest stop; legacy decider under unit suite only (Req: REQ-PLAN145-1; test_plan145::DeterministicHintNotDecider + recovery.recover_failed_step_with_agent `hint`)
- [x] F0 anticipatory reasoning-planning made the default for non-trivial repos (build surface / ≥3 files / polyglot / low-confidence); _PLANNING_SYSTEM reads script bodies + referenced files (Req: REQ-PLAN145-0)
- [x] F6 verified deterministic + reasoned/supervisor-accepted fixes persist via _record_common_lesson_from_fix + record_plan_skill; reasoning Q&A via the F7 "reasoning" intent (Req: REQ-PLAN145-6; partial: deep Q&A-over-log not wired into the agent)
- [x] F7 classify_repo_agent_intent adds reasoning/recheck (checked first); _maybe_autoroute_repo_agent opens the reasoning log (no repo needed) + routes recheck→action; "Usage: /ask"/"/do" prompts replaced with natural language (Req: REQ-PLAN145-7; test_plan145::NaturalLanguageIntent)
- [ ] Live (VM): plan anticipates venv+PyInstaller upfront; logical-thinking.md shows symptom→evidence→supervisor challenge→verified outcome; a sub-agent that gives up early is challenged; plain messages route with no slash; weak model/429 → honest stop

### Phase 93 — Plan 146 (multi-PR program): close 145 partials + provision/verify runtime + self-extend
- [x] A1 model-backed supervisor proven with an llm_client double; deterministic match proven non-authoritative (hint) (test_plan146::{ModelBackedSupervisor,DeterministicHintNonAuthoritative})
- [x] A2 Q&A-over-log: `_reasoning_context_hints` surfaces the redacted thinking-log + active plan into the repo Q&A agent for solution/reasoning questions (test_plan146::ReasoningContextForQA)
- [x] B2 real outcome verification: `_repo_test_command`/`_http_outcome_check_command`/`_outcome_check_command`; smoke prefers the repo's own tests over --help (test_plan146::OutcomeVerification)
- [x] B1 env/service provisioning: `_populate_env_defaults_command` (safe dev defaults, idempotent, externals excluded) + `_service_readiness_command` (wait before migrate) + declared seed step (test_plan146::EnvAndServiceProvisioning)
- [x] C1 `_subdir_run_topology`/`_has_concurrent_run_script` · C2 `_is_secret_env_key`/`_secret_env_keys_needing_intake` (excludes safe-default keys) · C5 `_teardown_command` (test_plan146::ConcurrentSecretsLifecycle)
- [x] C3 `_untrusted_local_notice` (sandbox posture on fresh local) · C4 `_repo_is_resource_heavy` (feeds estimate_requirement up front) (test_plan146::ConcurrentSecretsLifecycle)
- [x] B4 `recovery.capability_gap_from_text` + `_draft_extension_config` (detect→draft); /tools add stores a registerable draft (test_plan146::SelfExtension) — PARTIAL: live confirm→register into the running dispatcher is the follow-on
- [x] B3 service-backed matrix rows (django-postgres, redis-flask) with running_signal="" so pass relies on the B2 outcome check (tests/integration/matrix.py)
- [ ] PARTIAL/integration (needs VM or larger wiring): B2 gating `_launch_and_await_server` on the live HTTP probe; B1/C1/C2/C5 plan-assembly wiring of the new helpers into the executor + masked secret intake via the TUI; C4 right-size on fresh-VM create; B4 live register; B3 measured pass-rate
- [ ] Live (VM): Postgres-backed repo provisioned+migrated+served-healthy before "success"; frontend+backend run wired; capability gap → "connect tool X"

### Phase 94 — Plan 147 (R90): fix the BLINDNESS (fs tools couldn't read a ~/ repo on the VM)
- [x] F1 `harness/tools._remote_path_expr` (~→"${HOME}") used in all remote fs command builders + `expanduser` in `_resolve_safe_project_path` (test_plan147::{TildeExpansionRemote,TildeExpansionLocal})
- [x] F2 recovery-agent + halt + auto-apply episodes keyed by repo NAME (test_plan147::ConsistentLogIdentifier)
- [x] F3 `repo_agent._synthesize_answer` reports all-tool-failure as a tool/path-ACCESS problem, not "make sure it's cloned" (test_plan147::BlindnessIsDiagnosable)
- [x] F4 `_RECOVERY_SYSTEM` 5-part chain + exemplar; `recovery.weak_model_reasoning_note` (only when weak) wired into the planning pass (test_plan147::ClaudeShapedReasoning)
- [ ] Live (VM): on a build:sidecar failure the recovery agent READS package.json/the build script/the venv (real observations), reasons a structured symptom→cause→fix chain, supervisor accepts a verified fix — no "I couldn't read anything"

### Phase 95 — Plan 148 (R91): Duckln must SEARCH (web.search was broken), justify steps, not blame a capable model
- [x] F1 `_web_search_handler` passes the required `config_dir` + formats results; internet-off is actionable not a crash (test_plan148::WebSearchFixed)
- [x] F3 honest-stop is model-aware: weak→note, capable→"/internet on" search nudge, no unconditional model-blame (test_plan148::ModelAwareHonestStop)
- [x] F4 `ui._plan_step_is_speculative` flags an unbacked low-confidence step; backed/amendment/high-conf steps are not (test_plan148::SpeculativeStepFlag)
- [x] F5 `_SECONDARY_PY_SETUP_CMD` + diagnostics MISSING_VENV fix: `pip install -e .` is non-blocking → PyInstaller + marker still run (test_plan148::ResilientVenvSetup)
- [x] F6 repo_qa agent gains `web.fetch`; `_RECOVERY_SYSTEM` directs search→fetch-the-top-authoritative-doc (test_plan148::DeeperRagFetch)
- [ ] Live (VM): on the setuptools/PyInstaller failure Duckln searches the web (snippets + authoritative doc fetch), or F5 installs PyInstaller anyway; a capable model is never told to "get stronger"; internet-off → asks to enable

### Phase 96 — Plan 150 (R92): clean-slate controls (fresh-install, delete target, remove custom from selection)
- [x] F1 `_fresh_install_cleanup_command` / `_fresh_install_full_command` — wipe installed (keep source/.git/lockfiles/.env) + teardown + reclaim (test_plan150::{FreshInstallWipe,FreshInstallComposite})
- [x] F2 fresh-install resets per-repo state (`_clear_done_steps` reused — the flow calls it)
- [x] F5 `_delete_target_command` builds VM/container/AWS/GCP destroy commands; None for local/missing-id (test_plan150::DeleteTarget)
- [x] F6 `store.delete_recent_custom_repo` removes a custom repo from the /repos selection list, no files touched, idempotent (test_plan150::RemoveCustomRepoFromSelection)
- [x] F7 logic: `delete_controls` (mention_suggestions/resolve_delete_target/perform_delete) — `@<name>` picker data + phrase resolution + confirm→delete→tell-user; NO /delete (test_plan150b)
- [x] Audit: `store.record_deletion`/`list_recent_deletions` — 7-day retention (self-prunes >7d); user told "kept for 7 days" on confirm (test_plan150b)
- [x] Audit WIRED into the real VM-delete path (`_handle_vm_delete_free_text`): records each deletion + tells the user "A record of this is kept for 7 days."
- [ ] F3/F4/F7 (integration/UI): the Textual `@`-overlay (mirror the slash overlay) + main.py "delete @name"/"start fresh" command hookup + the suggest-when-stuck offer
- [ ] Live (VM): typing `@` lists VM/container/cloud/custom-repo; "delete @vm" purges after one confirm + "kept 7 days"; "start fresh" wipes installed (keeps source/.env) + reinstalls; remove-custom drops it from the picker

### Phase 97 — Plan 151 (R93): stop running multipass inside the VM + cd to the VM path
- [x] F1 `run_command_in_terminal` unwraps `multipass exec <vm> -- bash -lc '<body>'` when the pane is already in the VM (reuses `_unwrap_vm_pane_command`) (test_plan151::UnwrapMultipassInVmPane)
- [x] F2 `_build_repo_attach_command` cd's to `"$HOME/.duckln/projects/<name>"` (expands), not the Mac install_location (test_plan151::OpenInPaneUsesVmPath)
- [ ] Live (VM): "open repo in pane" while inside the VM runs `cd "$HOME/.duckln/projects/<name>" && exec /bin/bash -l` — no "multipass: command not found"

### Phase 98 — Plan 152 (R94): "already set up" means VERIFIED + fix-only repair plan
- [x] F1 `assess_existing_setup` → (healthy, concrete issues) from PARTIAL signals + verify result (test_plan152::AssessExistingSetup)
- [x] F3 `build_fix_only_plan_steps` → minimal repair step(s) per issue (venv/node_modules/dpkg/build), novel→reasoned recovery; never a full setup (test_plan152::FixOnlyPlan)
- [ ] F2 (integration): the already-set-up branch re-verifies → confirms "ready" OR reports issue + asks "plan a fix?" → runs the fix-only plan, then re-verifies
- [ ] Live (VM): a repo with a broken venv → "set up but venv incomplete", asks to fix, runs JUST the venv repair, confirms ready

### Phase 99 — Plan 153 (R95): AI/ML accelerator + environment awareness
- [x] A1 `ResourceSnapshot` gpu fields + `RESOURCE_PROBE_SCRIPT` nvidia-smi + `parse_resource_block` GPU parse + `.accelerator` (test_plan153::GpuProbe)
- [x] A2 `diagnostics.framework_install_command` (torch/jax/tf × cuda/mps/cpu) + `_nearest_cuda_tag` (test_plan153::FrameworkWheelMatcher)
- [x] A3 `_repo_needs_gpu` (deps/README/diffusion-family signals) (test_plan153::GpuNeedDetection)
- [x] B `_repo_uses_conda`/`_conda_env_name_from_yaml`/`_conda_setup_command`/`_conda_env_setup_steps`/`_conda_run_prefix` (Miniforge install, idempotent) (test_plan153::CondaEnvSetup)
- [x] Integration: `_generate_plan_for_bringup_impl` inserts the conda env-create step (environment.yml) before setup; probes the target accelerator (remote nvidia-smi / local MPS-or-CPU) + surfaces it; appends the accelerator-matched framework-wheel install (`_detect_dl_frameworks`+`_ml_framework_install_steps`) after the generic deps install; surfaces an honest GPU-route note when `_repo_needs_gpu` + no accelerator; ml_python.md playbook updated (proactive accelerator-match + conda) (test_plan153::MlPlanAssemblyWiring)
- [ ] B2: run/verify a conda repo THROUGH `conda run -n <env>` (run-command path) — best landed with a live conda VM
- [ ] Live (GPU VM): CUDA repo → cu-wheel + conda env → uses GPU; CPU VM → CPU wheel or honest GPU-route; conda repo sets up via mamba

### Phase 100 — Plan 154 (R96): Apple MLX / Metal first-class (native-macOS-only)
- [x] F1 `_detect_metal_frameworks` (mlx/mlx-lm/mlx-vlm/mlx-data, jax-metal, tensorflow-metal; signal-keyed) (test_plan154::DetectMetalFrameworks)
- [x] F2 `_metal_accelerator_label` → "Apple Metal (MLX)" on local Apple Silicon; wired into the Plan-153 accelerator block (test_plan154::MetalAcceleratorLabel)
- [x] F3 `framework_install_command` mlx branch (`pip install mlx…`, accelerator-agnostic, pin-preserving); MLX install step appended ONLY on local Apple Silicon (test_plan154::MlxInstallCommand)
- [x] F4 `_repo_needs_apple_metal` + `_apple_metal_routing_note` — warn + recommend `local` on a Linux target; soft (test_plan154::RepoNeedsAppleMetal, AppleMetalRoutingGuard)
- [x] F5 ml_python.md playbook: MLX/Metal native-macOS-only + the Linux-target warning
- [ ] Live (Mac): `mlx`/`mlx-lm` repo on `local` → "Apple Metal (MLX)" surfaced + `pip install mlx` + runs on the Apple GPU; same repo on a VM → warn-recommend-local; `mlx-smoke` confirms the GPU

### Phase 101 — Plan 155 (R97): production hardening of verified debt
- [x] F1 canonical `execution_targets.py` (normalize docker→container, membership sets); wrapper normalizes + returns None for unknown non-local (fixes docker→HOST fall-through); repo_bringup/tools/recovery/resource_manager import it (test_plan155::CanonicalExecutionTargets)
- [x] F2 conda repos run through `conda run -n <env>` (`_conda_run_prefix_for_repo` wired into the run step) (test_plan155::CondaRunThrough)
- [x] F3 `assess_existing_setup`/`build_fix_only_plan_steps` called in plan assembly for an already-cloned repo (surface health + prepend minimal repair)
- [x] BUG (Plan-153 regression): codegen 3rd element is verification|None — stripped the wrongly-placed safety class at all conda/ml/mlx/fix insertions
- [x] F4 `_handle_unified_delete_free_text` (container/cloud/custom-repo + `@name`, one confirm + 7-day audit) + `@`-mention overlay in textual_ui mirroring the slash overlay
- [x] F5 `.DS_Store` untracked + `.gitignore` hardened
- [x] F7 supervisor-reviewer swallow logged (DEBUG) instead of silent (test_plan155::RecoveryReviewerNotSilent)
- [x] F8 `/tools add`//`/mcp` detect→draft→confirm→register; `tool_registry.tool_registry_entries(config_dir)` merges registered extensions into the agent manifest (test_plan155::RegisteredExtensionVisible)
- [ ] Live: docker target runs IN the container; conda repo runs through its env; `delete @name` from chat + `@` popup; registered MCP tool callable
- [ ] Deferred (separate effort): refactor the `repo_bringup.py` ~12.5k-line monolith

### Phase 102 — Plan 156 (R98): reasoning-first production engine (phased)
- [x] P1 `recovery.probe_model_reasoning_capability` (behavior JSON test, cached per model id) + `model_is_reasoning_capable`/`weak_model_reasoning_note` prefer the cached verdict; wired lazily at the planning seam (test_plan156::CapabilityProbe)
- [x] P2 `_planning_is_nontrivial` + HARD honest-stop in `_generate_plan_for_bringup_impl`: non-trivial repo + no reachable model → `MODEL_UNREACHABLE_MESSAGE` blocked result, never a deterministic-only plan (test_plan156::PlanningRequiresReasoning)
- [x] P3 `generalist` descriptor + `agent/playbooks/generic.md`; `select_subagent_descriptor` routes an unmatched non-Python stack to generalist, Python-signal → python_setup (test_plan156::GeneralistRouting)
- [x] Reasoning logging: capability probe verdict + planning honest-stop appended to logical-thinking.md
- [x] P4 `plan_supervisor.run_plan_supervisor` (supervisor→inspector→planner→critic) wired live in `_generate_plan_for_bringup_impl` (non-trivial + reachable model): runs the pipeline, emits trace, surfaces + logs critic reasoning; deterministic plan stays the base; bounded + fallback (test_plan156::MultiAgentWiring)
- [x] P5 `recovery_flow.run_multi_agent_recovery` (coordinator→investigate/search/memory) wired live in `recover_failed_step_with_agent`: findings feed as a hint into the structured recovery; captured to logical-thinking.md; bounded + graceful + skipped for injected runners (test_plan156::MultiAgentWiring)
- [ ] Live (your VM/model): a Java/Gradle + an unusual-stack repo each reach a running app via reasoning; the supervisor/coordinator pipelines produce useful plans/fixes; weak/free model = same shape + honest degradation; no-model = honest-stop; measured matrix pass-rate

### Phase 103 — Plan 157 (R99): spec-driven agent harness
- [x] P1 PyYAML loader (`yaml.safe_load`) + `AgentDefinition` {version, input_contract, output_contract, max_contract_retries=2} + `SUPPORTED_SPEC_VERSION="2.0"` gate + `REQUIRED_AGENTS` + robust `from_directory` (skip+log optional, raise required); pyyaml in pyproject (test_harness_agent_def, test_plan157)
- [x] P2 `validate_input_contract` + `validate_agent_output` + `loop.run_spec` (bounded re-ask w/ schema+violation, honest-fail) + `_compose_system_prompt` injects output_contract (test_plan157::InputContract/OutputContract/RunSpecReAsk)
- [x] P3 planning agents spec-driven via `_spec_prompt`: planner/critic/clarifier/verdict/attributor; `SYSTEM_PROMPT_PLAN_*` retired; supervisor=orchestration / verdict=separate spec resolved + documented in plan_mode.py (test_plan157::PlanningAgentsAreSpecDriven; 100 plan-mode tests green)
- [x] P4 recovery spec-driven via `_recovery_spec_prompt`: `_RECOVERY_SYSTEM`→recovery_agent.md, `_SUPERVISOR_SYSTEM`→recovery_supervisor.md, `_PLANNING_SYSTEM`→planning_investigator.md; 3 constants deleted; 6 dependent tests updated to read the specs
- [x] P5 ConversationSupervisor router stays deterministic (audited); static persona → conversation_agent.md (`_conversation_persona_base()` + fallback); agents/README.md documents the rationale
- [x] P6 all 18 specs declare version 2.0, parse + pass tool-registry validation; agents/README.md (format, version gate, REQUIRED_AGENTS, live wiring, router rationale) (test_plan157::AllSpecsOnReferenceFormat)
- [ ] Live (your model): editing a spec changes behavior end-to-end; contract violations re-asked on a real model

### Phase 104 — Plan 161 (R103): `/loop` Claude-Code parity (expiry · LLM-driven stubs · bounded fix-retest)
- [x] PR1 expiry: `LoopRecord`/`loops` `expires_at`/`expiry_days` + `_ensure_loop_columns` ALTER-TABLE migration; per-type defaults (ci 7 / cost 90 / else 30) + 4th creation question + `_parse_expiry_days`; `_run_scheduled_loop` deactivate+unschedule+notify on expiry (no expired cycle run); `render_loops`/`render_loop_history` days-remaining; `/loop edit` renews+reactivates (test_plan161::PR1Expiry, test_main)
- [x] PR2 LLM-driven: NEW `harness/agents/loop_executor.md` (v1.0, optional, json_object contract, hard rules + self-check + multi-attempt); `LLM_DRIVEN_TYPES` + `_LOOP_TYPE_REAL_TOOLS` (real read-only scope) routed through `run_spec`; deterministic cost/health untouched; no usable model → honest `attention_needed`+notify, never fake `ok` (test_plan161::PR2LLMDriven)
- [x] PR3 fix-retest: `LoopExecutionResult` + `should_notify`/`attempts`; `FixAttempt`; `_diagnose_fix_retest_loop` (≤`max_fix_attempts`, all types escalate, resolved→fixed+notify / exhausted→failed+notify+history, prior_attempts threaded, auto_fix=False→single notify-only pass); `_run_scheduled_loop` honors `should_notify`; `_safe_loop_command` extended (force-push/drop) backing the spec's "never irreversible" rule (test_plan161::PR3FixRetest)
- [ ] Live (your machine): each loop type runs; a repo_watcher/ci_monitor cycle reasons over real read-only tools; a failing auto-fix loop tries→retests→honest-stops; loops auto-pause+notify at expiry

### Phase 105 — Plan 162 (R104): connect built-but-unwired execution features + delete dead code
- [x] F1 outcome verify: `_probe_served_outcome` gates a served-URL "ready" on a real HTTP-2xx probe in `_launch_and_await_server`; non-2xx → dead into recovery (test_plan162::F1OutcomeGate)
- [x] F2 secret intake: `secret_prompt` threaded into `resume_with_approved_plan`; `_secret_upsert_command` injects required repo secrets into `.env` via a NO-TRACE runner before launch — never logged/persisted (test_plan162::F2SecretIntake)
- [x] F3 concurrent run: `_launch_concurrent_processes` + `_subdir_run_command` launch backend+frontend, wire backend URL→frontend `.env`, report all URLs (test_plan162::F3ConcurrentRun)
- [x] F4 proactive sizing: `_repo_is_resource_heavy` drives an up-front right-size recommendation for a heavy stack on a remote target (user-approved) (test_plan162::F4Sizing)
- [x] F5 destructive decider: session-scoped `make_destructive_decider(select, approve)` wired at the executor gate (Yes/No/Approve-all-session) (test_plan162::F5DestructiveDecider)
- [x] F6 capability promote: `critic_review` calls `record_capability_observation` per reachable delivery → N-of-M weak→capable promotion (test_plan162::F6CapabilityPromotion)
- [x] F7 untrusted posture: `_untrusted_local_notice` surfaced once for a fresh repo on local (test_plan162::F7UntrustedPosture)
- [x] F8 capability gap: BLOCK + honest-stop surface `capability_gap_from_text` → "connect tool/MCP via /mcp" (test_plan162::F8CapabilityGap)
- [x] F9 cloud resize: `_handle_resource_crunch` grows an AWS/GCP disk via `build_cloud_resize_commands` (user-approved) + verify/retry (test_plan162::F9CloudResize)
- [x] F10 dead code removed: `readme_link_follower.py` + 6 dead funcs deleted; orphaned tests removed/repointed (test_plan162::F10DeadCodeRemoved)
- [ ] Live (your VM/model): a secret+GPU AI/ML repo is prompted, right-sized, run, and confirmed actually serving; a backend+frontend repo runs both wired; a destructive step offers Approve-all; a cloud disk-full grows or honest-stops

### Phase 106 — Plan 164 (R105): token visibility + honest context-overflow
- [x] F1 per-call accounting: `UsageSnapshot` last_*/context_window + `record_call_estimate` (pre-call) + exact-from-response supersede (test_plan164::F1PerCallAccounting)
- [x] F2 context window: `model_context_window(model_id, config_dir)` family table + override + conservative default (test_plan164::F2ContextWindow)
- [x] F3 choke-point estimate: `generate_provider_reply` records estimate+window before dispatch, even on failure (test_plan164::F3RecordedAtChokePoint)
- [x] F4 live readout: `_token_activity_segment`/`_activity_bar_segments` show ↑sent ↓recv · ctx ~X% (⚠ at ≥90%), fed from current_usage_snapshot() (test_plan164::F4ActivityBarReadout)
- [x] F5 honest overflow: `near_context_limit`/`context_overflow_hint` + `_with_context_hint` woven into the 3 model-unreachable display sites (test_plan164::F5OverflowHonesty)
- [ ] Live (your model): on a small free model each call shows ↑~Nk · ctx ~X%, and a near-limit failure reads as a context-overflow with "larger-context model" guidance, not "can't connect"

### Phase 107 — Plan 165 (R106): Ollama 500 honesty (surface body + hint + chat fallback)
- [x] F1 surface body: `_extract_error_body` + ≥400 message carries the real cause (all providers) (test_plan165::F1)
- [x] F2 actionable hint: `_ollama_error_hint` (OOM → smaller model; not-found → `ollama pull`) via `_error_hint` hook (test_plan165::F2)
- [x] F3 chat fallback: `OllamaAdapter.generate_reply` /api/generate → one-shot /api/chat on a 5xx; 4xx/transport don't fall back; default unchanged (test_plan165::F3)
- [ ] Live (your Mac): `/provider`→Ollama connects via chat fallback OR shows the exact reason, never a bare "HTTP 500. Please retry."

### Phase 108 — Plan 166 (R107): live token counter on the "Duckln's thinking" line
- [x] F1 `_thoughts_token_suffix` + `· N tokens` on the thinking toggle header (test_plan166)
- [x] F2 `_refresh_thoughts_token_header` on the 0.1s activity tick (header-only, no flicker)
- [ ] Live (your run): the "Duckln's thinking · N tokens" counter grows after each LLM call

### Phase 109 — Plan 167 (R108): trustworthy token counter
- [x] F1 reset per-call counts at call start + exact-from-response flag (test_plan167::F1)
- [x] F2 estimate `↑~N` vs exact `↑N` marker (test_plan167::F2)
- [x] F3 real context window from OpenRouter /models + persist at selection (test_plan167::F3)
- [x] F4 thinking-line "· N tokens (session)" label (test_plan167::F4)

### Phase 110 — Plan 168 (R109): resume setup, framed right
- [x] F1 per-kind objective labels (repo_deploy → "setting up") (test_plan168::F1)
- [x] F2 honest-stop writes resumable repo_deploy objective, supersedes stale repair (test_plan168::F2)
- [x] F3 /plan continue no-plan + active repo_deploy → resume guidance (not "Run /repos")
- [x] F4 honest-stop message names continue/show + resume verb
- [ ] Live (your VM): 429 on setup → switch provider → "continue the setup" resumes the bring-up (reads "setting up", skips done steps)

### Phase 111 — Plan 169 (R110): resume-greeting UX + honest reachability gate
- [x] F1 status refresh never spins the footer (no idle "Working…") (test_plan169::F1)
- [x] F2 kind-aware resume verb (setting up / fixing / working on) (test_plan169::F2)
- [x] F3 honest Plan-Mode repair message (no phantom /plan approve) (test_plan169::F3)
- [x] F4 objective kind/status → conversation LLM context as a hint (test_plan169::F4)
- [x] F5 `_repair_model_reachable` gate → honest-stop + clear spinner before the loop (test_plan169::F5)
- [ ] Live (your run): "Yes" with a disconnected model → honest message in ~1s, no spinning; greeting reads "setting up" for a setup

### Phase 112 — Plan 170 (R111): stop the FALSE "model unreachable" on a configured/working model
- [x] F1 Plan-156 gate keys on CONFIGURED provider+model (snapshot), not a transient client-build; + diagnostic log (test_plan170::F1)
- [x] F2 `_model_connection_error`: connect-establishment = unreachable; read-timeout / mid-request drop = reachable-but-failed (test_plan170::F2, test_model_connection, test_plan159 updated)
- [x] F3 batch reproduction (disproved the reset/num_ctx hypothesis; confirmed model reachable + generation works)
- [ ] Live (your Mac): re-run JustHireMe setup on the configured Ollama — no false "run ollama serve"; a real failure surfaces honestly via the critic

### Phase 113 — Plan 171 (R112): disk-full handler crash fix + visible numbers + custom resize + resume + LLM hint
- [x] F1 add `import math` to repo_bringup.py — un-crashes `_handle_resource_crunch` (reclaim/resize/delete were all dead behind the NameError) (test_plan171::DiskCrunchDoesNotCrash)
- [x] F4 surface used/free/total + "needs ≈N more free" up front; F5 measure + report reclaimed amount (test_plan171::F4/F5)
- [x] F6 custom-GB resize verified threaded (`main`→`resume`→handler→`apply_resize.prompt_value`); F7 resume-from-failed-step verified (`_retry_and_resume`+`_done_steps`) (test_plan171::F6/F7)
- [x] F8 crunch facts → `active_resource_crunch` workflow-state → conversation-agent hint (LLM voice); floor stays deterministic; cleared on resolve (test_plan171::F8 + CrunchFactsPlumbing)
- [x] F2/F3 executing handler test (no NameError) + Disk/RAM `recommend` + aggressive `reclaim_commands` correctness; grep guard (no other module uses `math.` unimported)
- [ ] Live (your VM): disk-full `build:all` → shows numbers, reclaims + reports freed, asks to resize (custom GB), grows disk, resumes from `build:all`

### Phase 114 — Plan 172 (R113): right-size from real need + resource_management SKILL + full accelerator matrix
- [x] F1 size from real build-output footprint (`du` target//dist//build/), recommend = total+footprint+headroom (not flat 5 GB) (test_plan172::BuildStepResizesToRealNeed)
- [x] F2 build step → keep the cache (safe reclaim only), resize-first; non-build disk crunch → aggressive reclaim (test_plan172::NonBuildDiskCrunchUsesAggressiveReclaim)
- [x] F3 `resource_management.md` SKILL (disk/RAM/CPU/GPU/NPU/Apple-MLX), listed in capabilities.md (test_plan172::ResourceSkillExists)
- [x] F4/F7 facts: `skill` + `guidance` nudge + full accelerator state (gpu/vram/cuda/accelerator/build_regen_mb) (test_plan172::FactsCarryGuidanceAndAccelerator)
- [x] F5 honest-stop with the real GB; VM-down (probe returns nothing) → honest "VM unreachable" stop (test_plan172::VmDownHonestStop)
- [x] Skill-vs-Tool aligned to Claude defs (capabilities.md); reuse Plan 153 GPU + Plan 154 MLX; full suite 2302 OK
- [ ] Live (your VM): disk-full build sizes the resize to actually fit (≈≥24 GB), keeps the cache, resumes to completion (or honest-stops with the true number)

### Phase 115 — Plan 173 (R114): model-free node-deps fix + devDeps + stop the wasteful recovery loop
- [x] F1 `ErrorCategory.NODE_DEP_MISSING` + match rule (cannot find package/ERR_MODULE_NOT_FOUND/UNRESOLVED_IMPORT/cannot find module/tsc) → node install w/ devDeps, repo-scoped auto, model-free (test_plan173::NodeDepMissingDeterministicFix)
- [x] F2 `_node_install_command` always includes devDeps (`--include=dev`/`--prod=false`/`--production=false`) (test_plan173::NodeInstallIncludesDevDeps)
- [x] F3 weak model → 1 recovery pass (not 3); break on no-new-evidence; capable keeps full rounds (test_plan173::WeakModelStopsRecoveryEarly)
- [x] F4 verified S0 whitelist already covers read-only probes (not_a_probe = weak-model misuse, handled by F3) — no code change
- [ ] Live (your VM): `build:all` missing vite → `npm install --include=dev` installs the build tools, build proceeds; no 3-round loop

### Phase 116 — Plan 174 (R115): hybrid cascade router + British 'repo manager' persona + safety guardrail
- [x] F1 narrow `classify_repo_agent_intent` to high-precision (drop greedy `?`→ask + ambiguous `what is`/`what's`) (test_plan174::F1)
- [x] F2 NEW `conversation_routes/llm_intent.py` classifier; wired in `_maybe_autoroute_repo_agent` (ambiguous + active repo + model → LLM classify) (test_plan174::F2)
- [x] F3 `utility_fallback` → free-form LLM answer in persona when live (not canned clarify); no model → clarify (conversation suite green)
- [x] F4 repeated identical tool-call guard in `harness/loop.py` (3rd+ blocked + nudge) (test_plan174::F4)
- [x] F5 role = "repo manager" (renamed); detailed LLM-generated varied dry-British off-topic register in `conversation_agent.md` (test_plan174::F5)
- [x] F6 `conversation_routes/safety.py` — `is_inappropriate_request` (refuse at free-text entry) + `scrub_reply` (output guard) + persona guardrail (test_plan174::F6)
- [ ] Live (your Mac): off-topic → varied British one-liner; random question → real free-form answer; inappropriate → clean decline; genuine repo question → crisp answer

### Phase 117 — Plan 175 (R116): production audit — first slice landed; rest scoped as next development
- [x] G1 `interaction.propose_and_confirm` (explain → Yes/No/write-your-own), reuses select/approve/text_prompt (test_plan175::G1)
- [x] G2 `build_failure_proposal` (comprehend failure → situation/recommendation/why; det + LLM + honest fallback) (test_plan175::G2)
- [x] A1 ONNX detection + accelerator wheel; A2 honest AMD/ROCm message; A3 JAX cuda11/cuda12 match (test_plan175::A1/A2/A3)
- [x] G3-msg aware proposal at amendment-pause + honest-stop; F1 "I checked:" investigated-summary (test_plan175::G3F1AwareRecovery)
- [x] C1 plan_lifecycle documented DORMANT-BY-DESIGN (not deleted — invariant tests) (test_plan175::C1)
- [x] B2 VERIFIED already handled (db readiness wait before migrations, Plan 146 B1) — audit false positive
- [ ] NEXT DEV: G3-synchronous apply-on-accept (mode-gated rewire of _attempt_amendment_and_halt)
- [ ] NEXT DEV: B1 Java/Gradle/Maven family+specialist; A4 model-download disk sizing; A5 conda codegen prefix
- [ ] NEXT DEV: E1-E3 follow-up robustness (extend llm_intent; expiry check; pronoun fallback); F2/F3 supervisor-log + faster weak-model repeated-call

### Phase 118 — Plan 176 (R117): aware-interaction as a SKILL + deterministic FLOOR + LLM VOICE, actually wired
- [x] F1 `aware_interaction.md` skill (discipline + 3 one-shot examples), listed in capabilities.md (test_plan176::F1)
- [x] F2 `build_failure_proposal` = deterministic floor + LLM voice over facts+skill (test_plan176::F2)
- [x] F3 amendment-pause calls `build_failure_proposal` (helper wired, not inline strings) (test_plan176::F3)
- [x] resource_management.md backfilled with one-shot examples; convention: every playbook carries them (test_plan176::BackfillAndCapabilities)

### Phase 119 — Plan 178 (R118): robust `<thought>` capture + uniform discipline + plugin usability
- [x] F1 `reasoning.py extract_thinking_and_content` (matched/truncated/missing) (test_plan178::F1)
- [x] F2 recovery captures `<thought>` into logical-thinking.md; payload parsed post-tag (test_plan178::F2)
- [x] F3 recovery_agent/recovery_supervisor/planning_investigator emit `<thought>`; `_extract_json` strips it; planner stays lean (test_plan178::F3)
- [x] F4 `registered_skill_hints` loads pulled skills into the agent; `/skill add` can register a live skill (test_plan178::F4)
- [x] F5 registered shell/MCP tools become executable specs; unconfigured fails gracefully (test_plan178::F5)

### Phase 120 — Plan 179 (R119): smart file-read windows + capability adapter + per-agent routing
- [x] A file-type line ceilings + paginator + focus-line + `[SYSTEM WARNING]` marker + local footer; never raises (test_plan179::APart*)
- [x] B `model_capability_class` + `adapt_reasoning_prompt` (cloud vs local) (test_plan179::BPart*)
- [x] C `active_routing` + `build_llm_client_for_role` + role wiring (REPO_AGENT/ERROR_AGENT/SUPERVISOR) + `/models`/`/status` (test_plan179::CPart*)
- [ ] NEXT DEV: per-agent Specialized model picker could use the live model-list arrow selector (today: id entry per role)

### Phase 121 — Plan 180 (R120): close the disclosed 178/179 loose ends
- [x] F1 removed dead WEB_READER role; AGENT_ROLES = 3 routable; /status explains web reading (test_plan180::F1, test_plan179::CPart)
- [x] F2 MCP tools/call proven against a fake stdio server; spawn-failure graceful (test_plan180::F2)
- [x] F3 Specialized picker uses the live model list (select) with text-entry fallback (test_plan180::F3)

### Phase 122 — Plan 181 (R121): production-grade Web Reader (additive, deterministic-floored)
- [x] F2 `read_web_page` + `web_reader.md` spec; structured digest; size-gated; None on no-model/error/low-conf (test_plan181::F2)
- [x] F1 web.fetch returns compact digest on high-confidence large page, else raw excerpt (test_plan181::F1)
- [x] F2b reader fix_commands unioned with deterministic evidence in _web_evidence_for_failed_step (additive)
- [x] F3 WEB_READER re-enabled as a routable role (now has a consumer) — /models + /status (test_plan180::F1, test_plan179::CPart)

### Phase 123 — Plan 182 (R122): live connection test + truthful header + repo-run guard + Ollama pull list
- [x] F1 /model runs verify_live_reply (all providers); not switched on a dead reply (test_plan182::F1)
- [x] F4 header green requires a live-verified marker; reachable-but-unverified → orange (test_plan182::F4)
- [x] F5 repo-run guard → "select a provider and model" (/provider, /model); orange allowed, red blocked (test_plan182::F5)
- [x] F2 Ollama "Pull a model" → curated pick-list + type fallback, excludes installed (test_plan182::F2)
- [x] F3 ollama-serve readiness wait before re-check (test_plan182::F3)

### Phase 124 — Plan 183 (R123): live-run polish batch [10/12 done; F10 + F3 next pass]
- [x] F1 chat wrap; F2 provider-dot pulse while busy (test_plan183::F1F2UI)
- [x] F4 precheck re-probe tools/versions when empty; F5 step+error hint (test_plan183::F4/F5)
- [x] F6 actionable honest-stop popup; F7 native text editor for logical-thinking.md (test_plan183::F6/F7)
- [x] F8/F9 monorepo install + auto-apply-known-fix-first (dir-aware) + F8b weak-model cap (test_plan183::F8*)
- [x] F11 consented scoped mode elevation in non-autonomous modes (test_plan183::F11)
- [ ] F10 NEXT: proactive "set up this repo?" offer + target picker on a pasted URL; paste-a-command reconfirm-run
- [ ] F3 NEXT: interactive fact-grounded capability/recommendation advisor (multi-turn) over the existing routes

### Phase 125 — Plan 184 (R124): finish F10 (paste command/repo-link) + F3 (capability advisor)
- [x] F10a pasted repo URL → "set up this repo?" → existing target picker via `preselected_repo` (test_plan184::F10a)
- [x] F10b pasted command → reconfirm-once → run; prose/questions never misfire; S4 refused (test_plan184::F10b)
- [x] F3 grounded capability answer (host probe + repo estimate) + interactive recommendation (test_plan184::F3)

### Phase 126 — Plan 185 (R125): LLM-voiced capability/recommendation advisor over deterministic facts
- [x] F1 `capability_facts.py` (host probe + repo estimate + target signal + reasons) (test_plan185::F1)
- [x] F2 inject facts into context_assembler for capability/recommendation routes (fresh + polish) (test_plan185::F2)
- [x] F2b polish explains the 'why' grounded in facts for recommendation routes; strict elsewhere (test_plan185::F2b)
- [x] F3 removed the Plan-184 hardcoded capability handler → flows to the grounded LLM (test_plan185::F3)

### Phase 127 — Plan 186 (R126): production-grade GUIDANCE + clarity batch
- [x] F1a one clean Ollama pull progress bar + single confirm; bare-`pulling` collapses (test_plan186::F1a)
- [x] F1b remove/uninstall an installed Ollama model from the menu (test_plan186::F1b)
- [x] F2a plain-English connectivity offline notice (ollama vs cloud) (test_plan186::F2a)
- [x] F2b smart startup auto-reconnect to saved local Ollama + re-probe (textual_ui startup probe)
- [x] F2c situation-aware next steps: `/plan off` resume hint + plain-language no-run-command popup (test_plan186::F2c, test_main::SlashCommandTest)
- [x] F3 Yes/No/Other/Cancel popup primitive (`propose_and_confirm(allow_cancel)`) (test_plan186::F3)
- [x] F4 header Plan label shows "off" (never disappears) (test_plan186::F4)

### Phase 128 — Plan 187 (R127): live-run polish + adopt the upgraded agent specs
- [x] F1 silent Ollama auto-reconnect on success (no chat noise) (test_plan187::F1)
- [x] F2 clean repo-Q&A answers; no tool-name/fabricated-line leak (`_SYNTH_SYSTEM` + `_observation_digest`) (test_plan187::F2)
- [x] F3 direct-state answer for "which repo were we working on" (test_plan187::F3)
- [x] F4a/b parse `budget_profiles` + verdict-driven `resolve_agent_budget` (behavior, not name; floor identical) (test_plan187::F4)
- [x] F4c wire the resolver into `run_repo_agent` (REPO_AGENT-role model, else global)
- [x] F4e shipped-spec guard (parse + version + tools ⊆ registry + profiles well-formed) (test_plan187::F4e)
- [x] adopt: fixed 3 stale tests pinned to old spec wording (test_plan136/146/147)

### Phase 129 — Plan 188 (R128): Plan Mode always-on + auto-decide (no OFF); robust "which repo" answer
- [x] F1 robust structural matcher for "which repo did we run last" (test_plan188::F1)
- [x] F2a always-on via `load_app_config` coercion; `/plan on`/`off` no-op; descriptors dropped (test_plan188::F2a)
- [x] F2c runtime repair is a consented Yes/No/Cancel offer, no "/plan off" dead-end (test_plan188::F2c)
- [x] F2d removed the stale OFF surface (header/off, resume-hint, ternaries); KEPT the executor (test_plan188::F2d)
- [x] serialize/deserialize stay faithful; coercion only at the live load boundary
- [x] reconciled plan-mode tests to always-on (test_plan_mode_command/targets/first_enforcement/169/186/73, test_main round-trips, test_onboarding)

### Phase 130 — Plan 189 (R129): answer ANY question clearly + cited; route without confusion (proven on real gemma2:9b)
- [x] F1 deterministic session-meta fast-path — status/VM/repo answered from state (test_plan189::F1)
- [x] F2 4-way LLM classifier: repo_question/repo_action/technical/social (test_plan189::F2)
- [x] F3 tightened deterministic cues (ambiguous cues defer to the LLM) (test_plan189::F3, test_plan174, test_repo_phases456)
- [x] F4 technical-answer handler: clear format + citations (repo file / web URL / honest note) + one-time internet offer (test_plan189::F4)
- [x] F5 REAL Ollama IT (opt-in) — passed actual questions to gemma2:9b, routing + technical answer green (test_ollama_routing_it)
- [x] proven on real gemma2:9b: "do you support docker"/"how does recursion work" → technical (was repo_qa); status/VM → F1

### Phase 131 — Plan 190 (R130): ground Duckln-self + recommendation questions in real facts; route without confusion (proven on real gemma2:9b + 2b)
- [x] F1 `_maybe_answer_duckln_self` in main.py — catalog/capabilities/identity/internet answered from real facts, model-independent, never the disclaimer (test_plan190::F1DucklnSelf)
- [x] F2b `_maybe_answer_recommendation` in main.py — ambiguous → ONE clarify, domain → grounded catalog answer, never repo_action/repo_question (test_plan190::F2bRecommendation)
- [x] F2 classifier prompt hardened (llm_intent._SYSTEM_PROMPT) — self + recommendation → social, never repo_action/repo_question; still 4 routes (test_plan190::F2Classifier)
- [x] F3 `_TECH_SYSTEM_CLEAR` identity guard + one-shot presentation example (in src/duckln/main.py) (test_plan190::F3TechnicalGuard)
- [x] F4 REAL Ollama IT `Plan190GroundedIT` (opt-in) — 4/4 on gemma2:9b (the screenshot-bug model) AND gemma2:2b: self/recommendation grounded, never the disclaimer, no misroute (test_ollama_routing_it)
- [x] fixes the screenshot bug: "how many repos you have access to" → "curated library of N repos … `/repos`" (was "I'm a language model, no repo access")

### Phase 132 — Plan 191 (R131): adaptive clarification engine (replace the rigid single question); proven on real gemma2:9b + 2b
- [x] F1 `src/duckln/clarify.py` — `run_clarification`/`ClarifyFacts`/`ClarifyResult`: LLM-planned adaptive 1–4 questions (hard cap 4), clever grounded options, single-question no-model floor (test_plan191::F1Engine)
- [x] F6 shared overlay upgraded to the Accept-this-plan format — numbered options + inline "Other" row (`allow_other`) + "Esc to cancel" footer + wrapping/folding long labels (textual_ui `show_choice_overlay`/`_overlay_choice_label`/`select_choice`; test_plan191::F6OverlayLabel)
- [x] F2 `decide_continue_or_new` + `_maybe_resume_pending_clarify` (main.py) — a typed reply is reasoned CONTINUE vs NEW, single-shot gate, deterministic floor + LLM tail (test_plan191::F2ContinueOrNew)
- [x] F3 every clarification decision recorded to `logical-thinking.md` (append_thinking_log surface="clarify") + link on resolution (test_plan191::F3ThinkingLog)
- [x] F4 recommendation flow (`_maybe_answer_recommendation`) wired to the engine; `aware_interaction.md` gains the adaptive-multi-option Example 4; supervisor/pronoun clarify (text-based) inherit F6's format, router not destabilized (test_plan191::F4Recommendation)
- [x] F5 REAL Ollama IT `Plan191ClarifyIT` (opt-in) — 3/3 on gemma2:2b AND gemma2:9b: clever grounded options, domain-named ask resolves quickly, continue-vs-new correct (test_ollama_routing_it)

### Phase 133 — Plan 192 (R132): 4-tier intent-routing cascade — fix the "how r you ?" misroute (per docs/knowledge/intent_routing_design.md); proven on real gemma2:9b + 2b
- [x] F1 Tier-1 chat-contraction normalization in `normalize_compact_message` (`_CHAT_CONTRACTIONS`) — "how r you?" → "how are you" → rapport/conversation; whole-token only (test_plan192::F1Normalization)
- [x] F2 `has_repo_signal` + router flip — no repo signal ⇒ conversation, never the repo clarifier (even with an active repair objective); end-to-end router test proves it (test_plan192::F2RepoSignal)
- [x] F3 Tier-3 `classify_intent_with_context` (llm_intent.py) — the doc's VERBATIM prompts, confidence 0.6, context; wired at the front door for the uncertain middle (test_plan192::F3Tier3Classifier)
- [x] F4 Tier-4 `phrase_axis_clarification` — the doc's VERBATIM prompt, one-line repo-vs-chat axis, deterministic floor; never the repo-only clarifier (test_plan192::F4Tier4Clarify)
- [x] F5 tiers 1–2 deterministic, only 3–4 use the model; each Tier-3/4 decision → runtime `logical-thinking.md` (surface="routing"); design reasoning in `docs/knowledge/logical_thinkinglog.md`
- [x] F6 REAL Ollama IT `Plan192RoutingIT` (opt-in) — 4/4 on gemma2:2b (+ gemma2:9b): a greeting is never a repo_task, Tier 4 asks the repo-vs-chat axis (weak model → "ambiguous" → clarify, the designed safe net)
- [x] fixes the screenshot: "how r you ?" (active JustHireMe repair) → a friendly reply, NOT "continue repairing JustHireMe / status / path"
