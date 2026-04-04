# Duckln Uninstall Flow

## Purpose
Define a safe, user-controlled uninstall experience that works across supported operating systems and installation methods.

## Command Entry
Duckln should support:

duckln uninstall

This should be a dedicated CLI flow, not a hidden manual cleanup process.

## Core Principles
- never uninstall silently
- always explain what will be removed
- always require confirmation
- support partial removal
- support OS-aware uninstall behavior
- support pip and pipx best-effort detection
- preserve user trust

## Uninstall Menu
Recommended terminal flow:

⚠ This will remove Duckln and associated data.

What would you like to remove?
1. Duckln application only (keep memory and sessions)
2. Duckln + all memory and session history
3. Everything including config and stored credentials
4. Cancel

Select (1-4):

## Confirmation Step
After the user selects an option, show exactly what will be removed and ask for final confirmation.

Example:

You selected: Duckln + all memory and session history

This will remove:
- Duckln application
- session history
- memory files
- SQLite state

This will keep:
- OS-level credentials if not included in the selected tier

Continue? (y/n)

## Removal Tiers

### Tier 1 — Application only
Remove:
- Duckln application/package only

Keep:
- config
- memory
- sessions
- stored provider credentials

### Tier 2 — Application + memory/session state
Remove:
- Duckln application/package
- sessions
- memory files
- SQLite runtime state

Keep:
- config
- stored provider credentials unless user selected full removal

### Tier 3 — Everything
Remove:
- Duckln application/package
- config
- memory
- sessions
- SQLite state
- stored provider credentials

## OS-Aware Uninstall Behavior
Duckln must detect operating system and install method where possible.

### Supported install-method handling for v1
- pip install duckln
- pipx install duckln

### macOS / Linux
If installed with pip:
- use `python -m pip uninstall duckln` or the active interpreter path

If installed with pipx:
- use `pipx uninstall duckln`

### Windows
If installed with pip:
- use `py -m pip uninstall duckln` or `python -m pip uninstall duckln`

If installed with pipx:
- use `pipx uninstall duckln`

## Detection Rules
Duckln should best-effort detect:
- pipx install
- pip install under current interpreter
- unknown install method

If install method is unknown:
- explain that clearly
- show the exact command Duckln recommends
- do not bluff

## Safety Rules
- HITL: suggest uninstall command only
- HOTL: ask approval before executing uninstall command
- HOOTLWO: only run uninstall command if explicitly allowed and safe

## Credential Removal
If credentials are stored:
- remove them only in Tier 3
- confirm this clearly

## Final Success Messages
Examples:
- ✓ Duckln application removed. Memory and config were kept.
- ✓ Duckln and local memory were removed.
- ✓ Duckln, config, memory, and stored credentials were removed.

## Failure Handling
If uninstall cannot be completed:
- explain what succeeded
- explain what remains
- show the exact next step

Example:
✗ Duckln package removal failed, but memory cleanup completed.
Run: python -m pip uninstall duckln

## Cancel Behavior
If the user selects Cancel:
- print a short confirmation
- return safely without changing state

Example:
Uninstall cancelled.