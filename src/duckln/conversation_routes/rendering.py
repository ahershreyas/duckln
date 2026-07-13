"""Conversation render helpers kept outside the main supervisor module."""

from __future__ import annotations

from duckln.render_blocks import bullet_block, comparison_block, join_blocks, step_list_block


def coverage_options(*, sample: str, realistic_names: str | None = None) -> tuple[str, ...]:
    if realistic_names:
        return (
            join_blocks(
                f"Duckln can help across the curated catalog, including {sample}.",
                f"On this machine, I’d start with {realistic_names}.",
                step_list_block(("Pick the best one for this machine", "Explain the tradeoff", "Move straight into setup")),
            ),
            join_blocks(
                f"Duckln can help across the curated catalog, including {sample}.",
                comparison_block(
                    "These look like the most realistic starting points on this machine:",
                    tuple((name.strip(), "practical starting point here") for name in realistic_names.split(",") if name.strip()),
                ),
                "If you want, I’ll choose one and explain the tradeoff.",
            ),
        )
    return (
        join_blocks(
            f"Duckln can help across the curated catalog, including {sample}.",
            "When you want, I can narrow that to the best fit for this machine.",
        ),
        join_blocks(
            f"I can work across repos like {sample}.",
            "If you want a starting point instead of the full catalog, I can pick the best fit for this machine.",
        ),
    )
