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
