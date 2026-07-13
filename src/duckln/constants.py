"""Shared UI colors and slash command tokens."""

from __future__ import annotations


COLOUR_GOLD = "#F5A623"
COLOUR_COMMAND = "#00FFFF"
COLOUR_USER_INPUT = "#FFFFFF"
COLOUR_SYSTEM = "#A9A9A9"
COLOUR_SUCCESS = "#4CAF50"
COLOUR_ERROR = "#E53935"
COLOUR_WARNING = "#FF6D00"
COLOUR_GREY = "#616161"

SLASH_COMMANDS = frozenset(
    {
        "/provider",
        "/model",
        "/mode",
        "/help",
        "/repos refresh",
        "/repos tracked",
        "/repos active",
        "/repos status",
        "/repos history",
        "/repos live",
        "/repos path",
        "/repos link",
        "/repos remove",
        "/repos",
        "/memory clear",
        "/memory",
        "/remember",
        "/healthcheck",
        "/exit",
        "/config",
        "/vm",
        "/cloud",
        "/cleanup",
        "/explore",
        "/internet",
        "/skills",
        "/skill add",
        "/tools",
        "/tools add",
        "/mcp",
        "/",
    }
)
