# Duckln Implementation Plan

Analyzed from `requirements.md`.

## Architecture Direction

Duckln v1 should be implemented as a terminal-native Python CLI with modular boundaries:

- `main.py` — app entrypoint and loop
- `config.py` — provider and mode onboarding, config persistence
- `ui.py` — banner, prompt, output sections
- `shell.py` — safe command runner
- `safety.py` — blocked patterns, safety classes, mode enforcement
- `diagnostics.py` — error classification and minimal context gathering
- `prompts.py` — provider-neutral system and task prompt builders
- `ai_client.py` — OpenRouter/OpenAI/Anthropic adapters
- `modes.py` — HITL/HOTL/HOOTLWO behavior policy

## Plan Items

1. **Create provider abstraction for OpenRouter, OpenAI, Anthropic**  
   Covers: R1, R10  
   Why: provider flexibility is a launch requirement.

2. **Implement first-run onboarding and local config**  
   Covers: R1  
   Why: the product must feel polished from first launch.

3. **Add pixel banner and duck headshot fallback**  
   Covers: R2  
   Why: launch excitement and memorable identity.

4. **Implement mode policy engine**  
   Covers: R3, R4, R5  
   Why: mode behavior must be deterministic and production-safe.

5. **Build safe shell execution layer**  
   Covers: R4  
   Why: this is the operational core.

6. **Build privacy-first redaction and payload shaping**  
   Covers: R5, R10  
   Why: must be non-negotiable for production.

7. **Implement error classification for Python/AI environment issues**  
   Covers: R6  
   Why: this is the first-release wedge.

8. **Implement exact next-step suggestion loop**  
   Covers: R7  
   Why: user value comes from moving to the next action quickly.

9. **Implement verification loop after fixes**  
   Covers: R8  
   Why: trust depends on verifying fixes.

10. **Implement concise teaching layer**  
    Covers: R9  
    Why: Duckln must feel smarter than a plain error explainer.

11. **Implement runtime command palette and slash command configuration flow**
    Covers: R1, R3
    Why: users must be able to change mode/provider/model safely during an active session without restarting Duckln.

12. **Implement environment healthcheck command**
    Covers: R6
    Why: allows users to quickly verify setup and diagnose environment issues proactively.

11. **Add mode-aware output verbosity tuning**  
    Covers: R3, R9  
    Why: support learners without slowing experts.

12. **Add structured production logging**  
    Covers: R10  
    Why: needed for production hardening and incident response.

13. **Create deterministic heuristics before LLM calls**  
    Covers: R6, R7  
    Why: reduce cost and improve trust.

14. **Create whitelisted safe auto-run set for HOOTLWO**  
    Covers: R3, R4  
    Why: partial autonomy without irresponsible execution.

15. **Add richer onboarding polish and provider docs links**  
    Covers: R1  
    Why: useful, but not launch-critical.

16. **Add session summaries for repeated failures**  
    Covers: R8, R9  
    Why: valuable after core loop is proven.

17. **Implement repo catalog loader with static cache + manual refresh**
    Covers: R11
    Why: ensures fast startup and user-controlled freshness without API dependency
    
18. **Implement repo bring-up engine**
    Covers: R11
    Why: core product value — clone, setup, verify

19. **Implement VM orchestration (Multipass Ubuntu) with optional fresh Duckln install inside the VM Includes `/vm` command to trigger VM setup outside onboarding.**
    Covers: R11
    Why: gives users a clean isolated environment while keeping credentials and config under explicit user control Includes: `/vm` command to trigger VM setup outside onboarding.

20. **Implement manual provider/model/API configuration flow inside the VM**
    Covers: R11
    Why: avoids automatic credential transfer and keeps VM setup explicit, safe, and user-controlled

21. **Implement agent memory system (filesystem + SQLite hybrid)**
    Covers: R11
    Why: enables agent learning and state persistence

22. **Implement memory control commands**
    Covers: R11
    Why: user trust and control over stored data

23. **Adopt AGENTS.md as the primary agent behavior contract**
    Covers: R12
    Why: Codex and future runtime agent behavior need a single authoritative contract

24. **Implement filesystem-facing memory with SQLite-backed state**
    Covers: R11, R12
    Why: gives the agent structured memory while preserving performance and reliability

25. **Implement hardware-aware behavior across Apple Silicon, non-Apple, and NVIDIA systems**
    Covers: R11, R12
    Why: setup and guidance must adapt to real hardware constraints

26. **Implement VM configuration UX (CPU, memory, naming)**
    Covers: R11
    Why: gives user control over VM resources and identification

27. **Implement post-setup guidance and completion feedback**
    Covers: R11
    Why: improves usability and clarity after environment setup

28. **Implement cross-hardware detection and adaptation (Apple Silicon, CPU-only, NVIDIA CUDA)**
    Covers: R12
    Why: prevents incorrect setup guidance across systems

29. **Implement VM naming conflict resolution with auto-increment suffix**
    Covers: R11
    Why: prevents VM creation failure due to duplicate names

30. **Implement SQLite-backed virtual memory contract**
    Covers: R12
    Why: makes SQLite the source of truth while preserving a filesystem-shaped interface for the agent

31. **Implement memory materialization and sync layer**
    Covers: R12
    Why: allows Duckln to expose agent-readable files from SQLite-backed memory safely and consistently
    
32. **Implement curated launch-catalog overrides for bundled repo generation**
    Covers: R11
    Why: launch repo quality requires product-level curation beyond raw GitHub topic filtering

## Technical Decisions

- Language: Python 3.11+
- OS target: macOS + Ubuntu
- Shell target: zsh + bash
- HTTP client: `requests` or `httpx`
- Interactive selection: `InquirerPy`
- Config file: local user config file in home directory
- Logs: JSONL or structured plain text with redaction
- Tests:
  - unit tests for safety/classification
  - integration tests for provider adapters and fix verification
  - manual scenario tests for venv / pip / torch / CUDA

## Release Strategy

### Release 0.1
- working loop
- onboarding
- HITL
- OpenRouter only
- missing package diagnosis

### Release 0.2
- OpenAI + Anthropic
- HOTL
- verification
- privacy redaction
- path and permission diagnosis

### Release 0.3
- HOOTLWO whitelist
- CUDA / torch mismatch handling
- production logging
- launch-ready polish

## Risks and Mitigation

- **Risk: generic LLM answers**
  - Mitigation: deterministic classification first, bounded prompts, verification loop.

- **Risk: unsafe command execution**
  - Mitigation: safety classes, blocked patterns, mode enforcement, whitelist auto-run only.

- **Risk: provider differences**
  - Mitigation: normalize prompt and response schema in adapter layer.

- **Risk: product feels like existing tools**
  - Mitigation: prioritize diagnosis + verification + teaching, not just explanation.
