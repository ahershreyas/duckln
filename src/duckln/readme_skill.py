"""README skill (Plan 57): enriched README extraction for any repo.

Extracts the data Duckln needs to set up and run ANY repo with a reasonable README:
prerequisites (with probe and install commands), the run-time usage summary, and
a concrete usage example to show the user once the repo is healthy.

Heuristic-first design: works without an LLM call by scanning the README for
common patterns (Requirements tables, Quick Start sections, install snippets).
An LLM enhancement path is layered on top in repo_bringup.py via the existing
_default_llm_readme_classifier and is purely additive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable


@dataclass(frozen=True)
class ReadmePrerequisite:
    """One declared system-level prerequisite extracted from a README."""

    name: str
    version_constraint: str | None
    probe_command: str
    install_apt: str | None = None
    install_brew: str | None = None
    install_fallback: str | None = None
    install_winget: str | None = None  # Plan 61 Fix A: Windows winget package ID
    source_line: str = ""

    def install_command_for(self, *, package_manager: str | None) -> str | None:
        """Pick the install command appropriate for the detected package manager."""
        if package_manager == "apt" and self.install_apt:
            return f"sudo apt install -y {self.install_apt}"
        if package_manager == "brew" and self.install_brew:
            return f"brew install {self.install_brew}"
        if package_manager == "winget" and self.install_winget:
            return (
                f"winget install --id {self.install_winget} "
                "--silent --accept-package-agreements --accept-source-agreements"
            )
        if self.install_fallback:
            return self.install_fallback
        return None


@dataclass(frozen=True)
class ReadmeMetadata:
    """Everything extracted from a README that helps run a repo."""

    prerequisites: tuple[ReadmePrerequisite, ...] = ()
    usage_summary: str = ""
    usage_example: str = ""
    confidence: float = 0.0


# Known runtime prerequisites: name -> (probe, install_apt, install_brew, install_fallback, install_winget).
# Plan 61 Fix A adds the 5th slot for Windows winget package IDs. None means no
# winget package; the install_fallback is used instead when available.
_KNOWN_PREREQUISITES: dict[str, tuple[str, str | None, str | None, str | None, str | None]] = {
    "node": (
        "node --version",
        "nodejs",
        "node",
        None,
        "OpenJS.NodeJS.LTS",
    ),
    "npm": (
        "npm --version",
        "npm",
        "node",  # brew's node bundles npm
        None,
        "OpenJS.NodeJS.LTS",  # winget node bundles npm
    ),
    "python": (
        "python3 --version",
        "python3 python3-pip python3-venv",
        "python@3.12",
        None,
        "Python.Python.3.12",
    ),
    "rust": (
        # Plan 60 Bug E: rustup installs cargo to ~/.cargo/bin/ but doesn't
        # update PATH for fresh shells. Probe tries PATH first, then falls
        # back to the well-known install location so a freshly-installed
        # cargo is detected without needing a shell restart.
        'command -v cargo >/dev/null 2>&1 && cargo --version || "$HOME/.cargo/bin/cargo" --version',
        None,
        "rust",
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
        "Rustlang.Rustup",
    ),
    "cargo": (
        'command -v cargo >/dev/null 2>&1 && cargo --version || "$HOME/.cargo/bin/cargo" --version',
        None,
        "rust",
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
        "Rustlang.Rustup",
    ),
    "uv": (
        # Plan 60 Bug E: uv's curl installer puts the binary at ~/.local/bin/uv.
        'command -v uv >/dev/null 2>&1 && uv --version || "$HOME/.local/bin/uv" --version',
        None,
        "uv",
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "astral-sh.uv",
    ),
    "go": (
        "go version",
        "golang",
        "go",
        None,
        "GoLang.Go",
    ),
    "docker": (
        "docker --version",
        "docker.io",
        "docker",
        None,
        "Docker.DockerDesktop",
    ),
    "git": (
        "git --version",
        "git",
        "git",
        None,
        "Git.Git",
    ),
    "make": (
        "make --version",
        "build-essential",
        None,
        None,
        None,  # make on Windows: use chocolatey or msys2 — no winget id
    ),
    "cmake": (
        "cmake --version",
        "cmake",
        "cmake",
        None,
        "Kitware.CMake",
    ),
    "pnpm": (
        "pnpm --version",
        None,
        "pnpm",
        "npm install -g pnpm",
        None,  # no winget id; fall back to npm-g
    ),
    "yarn": (
        "yarn --version",
        None,
        "yarn",
        "npm install -g yarn",
        None,
    ),
    "poetry": (
        # Plan 60 Bug E: poetry's curl installer puts the binary at ~/.local/bin/poetry.
        'command -v poetry >/dev/null 2>&1 && poetry --version || "$HOME/.local/bin/poetry" --version',
        None,
        "poetry",
        "curl -sSL https://install.python-poetry.org | python3 -",
        None,  # poetry on Windows: install via pipx through Python.Python.3.12
    ),
}


# Patterns that indicate a version constraint near a tool name.
# Matches: "Node 20+", "Python 3.13+", "Node >=20", "v1.2", "version 3.11".
_VERSION_HINT_PATTERN = re.compile(
    r"(?:>=\s*|>\s*|version\s+|v\.?\s*|\b)(\d+(?:\.\d+){0,2})\+?", re.IGNORECASE
)


def extract_readme_metadata(readme_text: str) -> ReadmeMetadata:
    """Parse a README and return prerequisites + usage info using heuristics only.

    Repo-agnostic: works for any README that mentions tool names from
    _KNOWN_PREREQUISITES in a Requirements / Prerequisites / Setup section.
    """
    if not readme_text or not readme_text.strip():
        return ReadmeMetadata()

    prereqs = _extract_prerequisites(readme_text)
    usage_summary = _extract_usage_summary(readme_text)
    usage_example = _extract_usage_example(readme_text)

    # Confidence is a simple proxy: prereqs found + usage info present.
    score = 0.0
    if prereqs:
        score += min(0.6, 0.15 * len(prereqs))
    if usage_summary:
        score += 0.2
    if usage_example:
        score += 0.2
    confidence = min(1.0, score)

    return ReadmeMetadata(
        prerequisites=tuple(prereqs),
        usage_summary=usage_summary,
        usage_example=usage_example,
        confidence=confidence,
    )


def _extract_prerequisites(readme_text: str) -> list[ReadmePrerequisite]:
    """Scan README for prerequisites using four complementary passes.

    Plan 58 Bug B: README extraction must also see tools that only appear in
    code blocks (e.g. JustHireMe's README only lists "Node.js" in Requirements,
    but uses `npm install` in code blocks — npm is therefore required even
    though it's not in the Requirements table).

    Strategy:
    1. Explicit Requirements / Prerequisites sections — strongest signal.
    2. Version-constrained prose mentions ("Node 20+") — strong signal.
    3. Install-keyword prose phrases ("Install with uv", "uv (recommended)").
    4. Code-block first-token scan — picks up the tools the README's commands
       actually USE. Aggressive: any first token is a candidate, with shell
       builtins and known prefixes (sudo, env vars, time, watch) stripped.

    Deduplicated by name. Order: pass 1 first (highest signal), then 2, then 3,
    then 4 (most permissive). First-seen wins so primary signal dominates.
    """
    seen: dict[str, ReadmePrerequisite] = {}
    sections = _slice_sections(readme_text)

    has_requirements_section = False
    for heading, body in sections:
        if _is_strict_requirements_heading(heading):
            has_requirements_section = True
            for prereq in _find_prerequisites_in_text(body, require_version=False):
                if prereq.name not in seen:
                    seen[prereq.name] = prereq

    if not has_requirements_section:
        for prereq in _find_prerequisites_in_text(readme_text, require_version=True):
            if prereq.name not in seen:
                seen[prereq.name] = prereq
        for prereq in _find_install_keyword_prerequisites(readme_text):
            if prereq.name not in seen:
                seen[prereq.name] = prereq

    # Pass 4 (always runs): scan code blocks for actual tool invocations.
    for prereq in _scan_code_blocks_for_tools(readme_text):
        if prereq.name not in seen:
            seen[prereq.name] = prereq

    return list(seen.values())


# Shell builtins and trivial commands that should NOT be registered as prereqs.
_SHELL_BUILTINS_AND_TRIVIA: frozenset[str] = frozenset({
    "cd", "ls", "pwd", "echo", "exit", "cat", "mkdir", "rm", "cp", "mv",
    "touch", "export", "source", ".", "set", "unset", "true", "false",
    "pushd", "popd", "alias", "unalias", "eval", "read", "test", "[",
    "printf", "shift", "trap", "ulimit", "umask", "wait", "type", "which",
    "command", "help", "history", "fc", "jobs", "bg", "fg", "kill", "exec",
})

# Wrapper prefixes to strip before taking the first token.
_WRAPPER_PREFIXES: frozenset[str] = frozenset({
    "sudo", "time", "watch", "nice", "nohup", "stdbuf", "env", "xargs",
})


def _strip_prefixes_and_first_token(line: str) -> str | None:
    """Strip env-var assignments / sudo / wrapper prefixes and return the first
    real command token. Returns None for empty / comment / continuation lines."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith(">") or stripped.startswith("$"):
        # Remove ">" prompts and "$" command-prompt markers.
        stripped = stripped.lstrip("> $").strip()
        if not stripped:
            return None
    # Drop trailing line-continuation backslashes.
    if stripped.endswith("\\"):
        stripped = stripped[:-1].rstrip()
    tokens = stripped.split()
    if not tokens:
        return None
    # Strip env-var assignments at the start (e.g. PORT=3000 npm start).
    i = 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("="):
        head = tokens[i].split("=", 1)[0]
        # Heuristic: env-var names are uppercase / alphanumeric / underscore.
        if head and all(ch.isalnum() or ch == "_" for ch in head) and head[0].isalpha():
            i += 1
            continue
        break
    # Strip wrapper prefixes (sudo, time, etc.) — possibly multiple chained.
    while i < len(tokens) and tokens[i] in _WRAPPER_PREFIXES:
        i += 1
    if i >= len(tokens):
        return None
    candidate = tokens[i].lower()
    # Strip path prefix (./foo, /usr/bin/foo → foo).
    if "/" in candidate:
        candidate = candidate.rsplit("/", 1)[-1]
    if not candidate or candidate in _SHELL_BUILTINS_AND_TRIVIA:
        return None
    # Reject anything that looks like an arg/flag/value rather than a command.
    if candidate.startswith("-") or candidate.startswith("("):
        return None
    return candidate


def _scan_code_blocks_for_tools(readme_text: str) -> list[ReadmePrerequisite]:
    """Plan 58 Bug B: extract the first-token tool of every command line
    inside fenced code blocks. Aggressive — any first token counts as a
    candidate prereq, after stripping wrappers (sudo, env vars, time)."""
    found: list[ReadmePrerequisite] = []
    seen_names: set[str] = set()
    in_code = False
    fence_lang = ""
    for line in readme_text.splitlines():
        fence_match = line.strip()
        if fence_match.startswith("```"):
            if in_code:
                in_code = False
                fence_lang = ""
            else:
                in_code = True
                fence_lang = fence_match[3:].strip().lower()
            continue
        if not in_code:
            continue
        # Skip non-shell code blocks (e.g. python, json, yaml).
        if fence_lang and fence_lang not in {"", "bash", "sh", "shell", "zsh", "console", "terminal"}:
            continue
        token = _strip_prefixes_and_first_token(line)
        if token is None:
            continue
        # Map common aliases.
        if token in {"node"}:
            normalized = "node"
        elif token in {"python3", "python"}:
            normalized = "python"
        elif token in {"pip3", "pip"}:
            normalized = "python"  # pip implies python
        elif token in {"rustc"}:
            normalized = "rust"
        else:
            normalized = token
        if normalized in seen_names:
            continue
        seen_names.add(normalized)
        if normalized in _KNOWN_PREREQUISITES:
            spec_known = _KNOWN_PREREQUISITES[normalized]
            probe = spec_known[0]
            install_apt = spec_known[1]
            install_brew = spec_known[2]
            install_fallback = spec_known[3]
            install_winget = spec_known[4] if len(spec_known) > 4 else None
            found.append(
                ReadmePrerequisite(
                    name=normalized,
                    version_constraint=None,
                    probe_command=probe,
                    install_apt=install_apt,
                    install_brew=install_brew,
                    install_fallback=install_fallback,
                    install_winget=install_winget,
                    source_line=line.strip()[:160],
                )
            )
        else:
            # Unknown tool: register as probe-only so the user is told what's
            # required, but Duckln does not attempt to auto-install it.
            found.append(
                ReadmePrerequisite(
                    name=normalized,
                    version_constraint=None,
                    probe_command=f"command -v {normalized}",
                    install_apt=None,
                    install_brew=None,
                    install_fallback=None,
                    install_winget=None,
                    source_line=line.strip()[:160],
                )
            )
    return found


# Strong install-prerequisite signal phrases — when a known tool name appears
# near one of these in non-code prose, treat it as a declared prereq.
_INSTALL_KEYWORD_PATTERNS = (
    r"install\s+(?:[\w\-./]+)\s+with\s+",     # "Install vllm with uv"
    r"installed\s+via\s+",                      # "installed via uv"
    r"recommended\s*\)?\s*$",                   # "uv (recommended)"
    r"\brequires?\s+",                          # "Requires Python"
    r"\bneed(?:s|ed)?\s+",                      # "Needs Node"
    r"\busing\s+",                              # "Using uv"
)


def _find_install_keyword_prerequisites(readme_text: str) -> list[ReadmePrerequisite]:
    """Catch prose prereqs by looking for tool names near strong install-keyword phrases."""
    found: list[ReadmePrerequisite] = []
    if not readme_text:
        return found
    # Strip code blocks.
    non_code_lines: list[str] = []
    in_code = False
    for line in readme_text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        non_code_lines.append(line)
    text = "\n".join(non_code_lines)
    text_lower = text.lower()

    for name, spec in _KNOWN_PREREQUISITES.items():
        probe = spec[0]
        install_apt = spec[1]
        install_brew = spec[2]
        install_fallback = spec[3]
        install_winget = spec[4] if len(spec) > 4 else None
        name_pattern = re.compile(rf"\b{re.escape(name)}(?:\.js)?\b", re.IGNORECASE)
        for match in name_pattern.finditer(text):
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 30)
            window = text_lower[start:end]
            for kw_pattern in _INSTALL_KEYWORD_PATTERNS:
                if re.search(kw_pattern, window):
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    line_end = text.find("\n", match.end())
                    if line_end == -1:
                        line_end = len(text)
                    source_line = text[line_start:line_end].strip()[:160]
                    found.append(
                        ReadmePrerequisite(
                            name=name,
                            version_constraint=None,
                            probe_command=probe,
                            install_apt=install_apt,
                            install_brew=install_brew,
                            install_fallback=install_fallback,
                            install_winget=install_winget,
                            source_line=source_line,
                        )
                    )
                    break
            else:
                continue
            break  # one mention per tool is enough
    return found


def _is_strict_requirements_heading(heading: str) -> bool:
    """A stricter version of _is_requirements_heading that only matches headings
    that explicitly indicate prerequisites — not generic Setup or Installation
    sections (those usually contain commands, not prereqs)."""
    h = heading.lower().strip()
    return any(
        keyword in h
        for keyword in (
            "requirement",
            "prerequisite",
            "dependenc",
            "before you begin",
            "before installing",
            "system requirement",
            "what you need",
        )
    )


def _slice_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown by H1/H2/H3 headings into (heading, body) pairs."""
    pieces: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            if current_lines:
                pieces.append((current_heading, "\n".join(current_lines)))
            current_heading = stripped.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        pieces.append((current_heading, "\n".join(current_lines)))
    return pieces


def _is_requirements_heading(heading: str) -> bool:
    h = heading.lower().strip()
    return any(
        keyword in h
        for keyword in (
            "requirement",
            "prerequisite",
            "dependenc",
            "before you begin",
            "before installing",
            "system requirement",
            "what you need",
            "setup",
            "installation",
            "getting started",
        )
    )


def _find_prerequisites_in_text(text: str, *, require_version: bool = False) -> list[ReadmePrerequisite]:
    """Find every known tool mentioned in `text` and build a ReadmePrerequisite.

    A 'mention' is a NON-CODE-BLOCK line that contains the tool name as a whole
    word. Lines inside fenced code blocks (```...```) are skipped so we don't
    pick up `npm install` in a Quick Start example as a prereq.

    If require_version=True, only mentions with a nearby version token (e.g.
    "Node 20+", "Python 3.11") are accepted.
    """
    found: list[ReadmePrerequisite] = []
    if not text:
        return found
    # Strip fenced code blocks so install commands in code samples don't count.
    non_code_lines: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        non_code_lines.append(line)
    text_lower_lines = [(line.lower(), line) for line in non_code_lines]

    for name, spec in _KNOWN_PREREQUISITES.items():
        probe = spec[0]
        install_apt = spec[1]
        install_brew = spec[2]
        install_fallback = spec[3]
        install_winget = spec[4] if len(spec) > 4 else None
        pattern = re.compile(rf"\b{re.escape(name)}(?:\.js)?\b", re.IGNORECASE)
        for line_lower, line in text_lower_lines:
            if pattern.search(line_lower):
                version_match = _looks_versioned(line, tool_name=name)
                if require_version and version_match is None:
                    continue
                version_constraint: str | None = None
                if version_match is not None:
                    version_constraint = f">={version_match.rstrip('+')}"
                found.append(
                    ReadmePrerequisite(
                        name=name,
                        version_constraint=version_constraint,
                        probe_command=probe,
                        install_apt=install_apt,
                        install_brew=install_brew,
                        install_fallback=install_fallback,
                        install_winget=install_winget,
                        source_line=line.strip()[:160],
                    )
                )
                break
    return found


def _looks_versioned(line: str, *, tool_name: str) -> str | None:
    """Return the version string if the line near `tool_name` contains a version constraint.

    Catches patterns like "Node 20+", "Node.js 20+", "Python 3.13+", "Node >=20",
    "version 3.11", "v1.2". Returns None if no version token is found near the name.
    """
    # Look around the tool name (within ~30 chars on either side).
    name_pattern = re.compile(rf"\b{re.escape(tool_name)}(?:\.js)?\b", re.IGNORECASE)
    match = name_pattern.search(line)
    if match is None:
        return None
    start = max(0, match.start() - 5)
    end = min(len(line), match.end() + 30)
    nearby = line[start:end]
    version_match = _VERSION_HINT_PATTERN.search(nearby)
    if version_match is None:
        return None
    return version_match.group(1)


def _extract_usage_summary(readme_text: str) -> str:
    """Pull the first paragraph of a 'Usage' / 'How to use' section.

    Preference order: Usage / How to use → Quick Start / Getting Started → first
    substantive paragraph anywhere. This is because Usage typically describes
    what the running app does, while Quick Start is often command-only.
    """
    sections = _slice_sections(readme_text)
    # Pass 1: prefer Usage / How to use (user-facing description).
    for heading, body in sections:
        h = heading.lower()
        if any(k in h for k in ("usage", "how to use", "how to run")):
            paragraph = _first_paragraph(body)
            if paragraph:
                return paragraph[:280]
    # Pass 2: Quick Start / Getting Started as fallback.
    for heading, body in sections:
        h = heading.lower()
        if any(k in h for k in ("quick start", "quickstart", "getting started")):
            paragraph = _first_paragraph(body)
            if paragraph:
                return paragraph[:280]
    # Pass 3: first substantive paragraph anywhere.
    if sections:
        for heading, body in sections:
            paragraph = _first_paragraph(body)
            if paragraph and len(paragraph) > 40:
                return paragraph[:280]
    return ""


def _extract_usage_example(readme_text: str) -> str:
    """Pull the first code block from a 'Usage' or 'Quick Start' section.

    Returns a single concrete shell command (or short example block) the user
    can try once the repo is running.
    """
    sections = _slice_sections(readme_text)
    for heading, body in sections:
        h = heading.lower()
        if any(k in h for k in ("usage", "quick start", "quickstart", "how to use", "example")):
            block = _first_code_block(body)
            if block:
                # Strip language fence; keep just the first non-empty line.
                lines = [ln for ln in block.splitlines() if ln.strip()]
                if lines:
                    return lines[0][:200]
    return ""


def _first_paragraph(text: str) -> str:
    pieces: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if pieces:
                break
            continue
        if line.lstrip().startswith(("```", "|", ">", "- ", "* ", "#")):
            if pieces:
                break
            continue
        pieces.append(line.strip())
    return " ".join(pieces).strip()


def _first_code_block(text: str) -> str:
    in_block = False
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_block:
                break
            in_block = True
            continue
        if in_block:
            lines.append(line)
    return "\n".join(lines).strip()


def detect_package_manager(system_probe_os: str) -> str | None:
    """Map a SystemProbe.operating_system value to a package manager identifier.

    Plan 61 Fix A: Windows now returns "winget" so the prereq preflight can plan
    the right install commands on Windows hosts.
    """
    os_norm = (system_probe_os or "").lower()
    if "linux" in os_norm:
        return "apt"
    if "darwin" in os_norm or "mac" in os_norm:
        return "brew"
    if "windows" in os_norm:
        return "winget"
    return None


def build_prerequisite_preflight_steps(
    prerequisites: Iterable[ReadmePrerequisite],
    *,
    system_probe_os: str,
    execution_target: str = "local",
) -> tuple[tuple[str, str, str], ...]:
    """For each prereq, return a tuple of (purpose, probe_command, install_command).

    The caller wraps these in RepoBringUpStep objects (or whatever the plan needs).
    Each prereq becomes a SINGLE attempt: probe; if probe fails, install; verify.
    This is the sequential preflight from Plan 57 Phase 2.

    Returns empty tuple if no prereqs.
    """
    pm = detect_package_manager(system_probe_os)
    result: list[tuple[str, str, str]] = []
    for prereq in prerequisites:
        install_command = prereq.install_command_for(package_manager=pm)
        if install_command is None:
            # No install path known for this OS — surface as probe-only step so the
            # user is at least told what's missing rather than silently skipping.
            install_command = f"echo 'Duckln has no automatic install for {prereq.name} on this OS — install it manually.'"
        version_label = f" ({prereq.version_constraint})" if prereq.version_constraint else ""
        purpose = f"Ensure {prereq.name}{version_label} is installed before setup runs"
        result.append((purpose, prereq.probe_command, install_command))
    return tuple(result)
