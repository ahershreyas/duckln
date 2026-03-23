"""Terminal UI helpers for branding, prompts, and output formatting."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


BANNER_COLOR = "\033[33m"
RESET_COLOR = "\033[0m"
COMPACT_BANNER = "\n".join(
    (
        ">(.)__ <  Duckln",
        " (___/    AI Terminal Mentor",
    )
)


@lru_cache(maxsize=1)
def load_banner_asset() -> str:
    """Load the Duckln banner from the bundled documentation asset."""

    asset_path = Path(__file__).resolve().parents[2] / "docs" / "assets" / "duckln-banner.txt"
    return asset_path.read_text(encoding="utf-8").strip("\n")


def render_banner(width: int) -> str:
    """Render the full banner when it fits, otherwise fall back to the compact duck."""

    banner = load_banner_asset()
    widest_line = max(len(line) for line in banner.splitlines())
    body = banner if width >= widest_line else COMPACT_BANNER
    return f"{BANNER_COLOR}{body}{RESET_COLOR}"
