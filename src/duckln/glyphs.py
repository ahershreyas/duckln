"""Nerd Font glyph constants used across the Duckln UI.

The Symbols Nerd Font is bundled with the Duckln package and auto-installed
on first run by `font_setup.ensure_nerd_font_installed()`. Modern terminals
on macOS / Linux / Windows resolve these codepoints via system font fallback
once the font is installed — no terminal config change required.

`nerdfonts` is a hard dependency declared in pyproject.toml. The package
exposes a flat `icons` dict mapping `<font>_<icon>` names to single-codepoint
strings.
"""

from __future__ import annotations

import nerdfonts as _nf


def _glyph(name: str, fallback: str) -> str:
    value = _nf.icons.get(name)
    if isinstance(value, str) and value:
        return value
    # Defensive fallback. Should never fire because the names below are
    # vetted against the installed nerdfonts==1.0.1 dictionary.
    return fallback


# Status dot — solid filled circle.
DOT_SOLID = _glyph("fa_circle", "●")  # ●

# Internet status — Font Awesome globe for online, chain-broken for offline.
WEB_ONLINE = _glyph("fa_globe", "○")  # ○
WEB_OFFLINE = _glyph("fa_chain_broken", "⊘")  # ⊘

# Input bar.
SLASH = _glyph("ple_forwardslash_separator", "/")  # Powerline forward slash
PLUS = _glyph("fa_plus", "+")

# `+` dropdown options.
UPLOAD = _glyph("fa_upload", "↑")  # ↑
DOCUMENT = _glyph("fa_file_text_o", "▤")  # ▤

# Plan Mode — clipboard / check / cross / hourglass / pencil.
# Used by the /plan UI and by the header indicator that shows Plan Mode is on.
PLAN_NOTE = _glyph("fa_clipboard", "▤")
PLAN_APPROVED = _glyph("fa_check_square_o", "☑")
PLAN_REJECTED = _glyph("fa_times_circle", "✗")
PLAN_PENDING = _glyph("fa_hourglass_half", "⌛")
PLAN_EDIT = _glyph("fa_pencil_square_o", "✎")
