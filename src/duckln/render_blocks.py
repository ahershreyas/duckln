"""Typed terminal render blocks for readable Duckln output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RenderBlock:
    """Base renderable block."""

    def render(self) -> str:
        raise NotImplementedError

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True)
class ParagraphBlock(RenderBlock):
    text: str

    def render(self) -> str:
        return " ".join(self.text.strip().split())


@dataclass(frozen=True)
class BulletListBlock(RenderBlock):
    items: tuple[str, ...]

    def render(self) -> str:
        cleaned = [item.strip() for item in self.items if item and item.strip()]
        return "\n".join(f"- {item}" for item in cleaned)


@dataclass(frozen=True)
class ActionBlock(RenderBlock):
    title: str
    actions: tuple[str, ...]

    def render(self) -> str:
        return RenderBundle((ParagraphBlock(self.title), BulletListBlock(self.actions))).render()


@dataclass(frozen=True)
class FileMapBlock(RenderBlock):
    title: str
    entries: tuple[tuple[str, str], ...]

    def render(self) -> str:
        lines = [f"- {label}: {path}" for label, path in self.entries if label and path]
        return RenderBundle((ParagraphBlock(self.title), RawBlock("\n".join(lines)))).render()


@dataclass(frozen=True)
class StatusBlock(RenderBlock):
    title: str
    body: str

    def render(self) -> str:
        return RenderBundle((ParagraphBlock(self.title), ParagraphBlock(self.body))).render()


@dataclass(frozen=True)
class StepListBlock(RenderBlock):
    items: tuple[str, ...]

    def render(self) -> str:
        cleaned = [item.strip() for item in self.items if item and item.strip()]
        return "\n".join(f"{index}. {item}" for index, item in enumerate(cleaned, start=1))


@dataclass(frozen=True)
class ComparisonBlock(RenderBlock):
    title: str
    pairs: tuple[tuple[str, str], ...]

    def render(self) -> str:
        lines = [f"- {label}: {value}" for label, value in self.pairs if label and value]
        return RenderBundle((ParagraphBlock(self.title), RawBlock("\n".join(lines)))).render()


@dataclass(frozen=True)
class RawBlock(RenderBlock):
    text: str

    def render(self) -> str:
        return self.text.strip()


@dataclass(frozen=True)
class RenderBundle(RenderBlock):
    blocks: tuple[RenderBlock | str, ...]

    def render(self) -> str:
        return "\n\n".join(
            rendered
            for rendered in (render_block_text(block) for block in self.blocks)
            if rendered
        )


def render_block_text(block: RenderBlock | str | None) -> str:
    if block is None:
        return ""
    if isinstance(block, RenderBlock):
        return block.render().strip()
    return str(block).strip()


def join_blocks(*blocks: RenderBlock | str) -> str:
    return RenderBundle(tuple(blocks)).render()


def paragraph_block(text: str) -> ParagraphBlock:
    return ParagraphBlock(text)


def bullet_block(items: tuple[str, ...] | list[str]) -> BulletListBlock:
    return BulletListBlock(tuple(items))


def action_block(title: str, actions: tuple[str, ...] | list[str]) -> ActionBlock:
    return ActionBlock(title, tuple(actions))


def file_map_block(title: str, entries: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> FileMapBlock:
    return FileMapBlock(title, tuple(entries))


def status_block(title: str, body: str) -> StatusBlock:
    return StatusBlock(title, body)


def step_list_block(items: tuple[str, ...] | list[str]) -> StepListBlock:
    return StepListBlock(tuple(items))


def comparison_block(title: str, pairs: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> ComparisonBlock:
    return ComparisonBlock(title, tuple(pairs))


def render_sequence(blocks: Iterable[RenderBlock | str]) -> str:
    return RenderBundle(tuple(blocks)).render()
