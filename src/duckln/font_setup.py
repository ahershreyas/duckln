"""Plan 63 Fix 4b: install the bundled Symbols Nerd Font on first run.

The font file ships inside the wheel at
``src/duckln/assets/fonts/SymbolsNerdFont-Regular.ttf`` (SIL OFL 1.1).
On the first Duckln launch we copy it into the OS font directory so that
modern terminals resolve the nerdfont codepoints via system font fallback.

Cross-platform paths:

* macOS (Intel + Apple Silicon): ``~/Library/Fonts/SymbolsNerdFont-Regular.ttf``
* Linux (any distro): ``~/.local/share/fonts/SymbolsNerdFont-Regular.ttf`` plus
  a ``fc-cache -f`` invocation when ``fontconfig`` is available.
* Windows: ``%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts\\SymbolsNerdFont-Regular.ttf``
  plus a per-user registry entry under
  ``HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts``.

VS Code's integrated terminal has stricter fallback rules; we detect it via
env vars and surface a one-time settings.json guidance message.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


FONT_FILENAME = "SymbolsNerdFont-Regular.ttf"
LICENSE_FILENAME = "LICENSE-SymbolsNerdFont.txt"


def bundled_font_path() -> Path:
    """Path to the .ttf file packaged inside the Duckln wheel."""
    return Path(__file__).parent / "assets" / "fonts" / FONT_FILENAME


def bundled_license_path() -> Path:
    return Path(__file__).parent / "assets" / "fonts" / LICENSE_FILENAME


def target_font_path() -> Path | None:
    """Return the OS-specific destination path for the font. None when the
    OS isn't supported by our auto-install."""
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Fonts" / FONT_FILENAME
    if system == "Linux":
        return home / ".local" / "share" / "fonts" / FONT_FILENAME
    if system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "Microsoft" / "Windows" / "Fonts" / FONT_FILENAME
        return home / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts" / FONT_FILENAME
    return None


def ensure_nerd_font_installed() -> tuple[bool, str]:
    """Install the bundled Symbols Nerd Font if it isn't already in the OS
    font directory. Idempotent — second call exits early.

    Returns ``(installed_now, message)`` where ``installed_now`` is True only
    if the font was newly installed this call. The message describes what
    happened (suitable for logging or display).
    """
    src = bundled_font_path()
    if not src.exists():
        return False, "Bundled font file not found in package; skipping install."

    dst = target_font_path()
    if dst is None:
        return False, f"Unsupported OS ({platform.system()}); skipping font install."

    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return False, "Symbols Nerd Font already installed."

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except (OSError, PermissionError) as exc:
        return False, f"Could not install Symbols Nerd Font: {exc}"

    # Refresh the font cache on Linux when fontconfig is available.
    if platform.system() == "Linux" and shutil.which("fc-cache") is not None:
        try:
            subprocess.run(
                ["fc-cache", "-f", str(dst.parent)],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Add the per-user registry entry on Windows.
    if platform.system() == "Windows":
        try:
            _register_windows_font(dst)
        except Exception:
            # Registration is best-effort; the font usually loads after a
            # reboot anyway because it lives in the user Fonts folder.
            pass

    return True, f"Installed Symbols Nerd Font at {dst}."


def _register_windows_font(font_path: Path) -> None:
    """Register a font under HKCU so Windows picks it up without a restart."""
    if sys.platform != "win32":
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return
    key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(
            key,
            "Symbols Nerd Font Mono (TrueType)",
            0,
            winreg.REG_SZ,
            str(font_path),
        )


def is_vscode_terminal() -> bool:
    """True when Duckln is running inside VS Code's integrated terminal."""
    return bool(
        os.environ.get("VSCODE_PID")
        or os.environ.get("TERM_PROGRAM", "").lower() == "vscode"
    )


def vscode_settings_guidance() -> str:
    """Return the one-line user-facing instruction for enabling the nerdfont
    in VS Code's integrated terminal."""
    return (
        "Duckln noticed you're in VS Code. For icons to render, add to your "
        "settings.json:\n"
        '    "terminal.integrated.fontFamily": "Menlo, \'Symbols Nerd Font Mono\'"\n'
        "Then reload VS Code. (One-time setup.) Run /setup-fonts for the exact line."
    )
