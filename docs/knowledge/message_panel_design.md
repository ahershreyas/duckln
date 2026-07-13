# Duckln Message Panel — Formatting Spec (Textual + Rich, stack-exact)

**Audience:** the engineer polishing Duckln's message panel.
**Stack (confirmed):** Python ≥3.11, Textual full-screen TUI (`src/duckln/textual_ui.py`,
`DucklnSplitPaneApp`). The message panel is a `RichLog` widget inside the app; styling is via
Rich markup + Textual CSS. A plain-CLI fallback exists when Textual isn't available.
**Scope:** approach and exact primitives — NO code. Names of the Rich/Textual objects to use and
where each goes.

**Headline:** you do NOT need any new package. Textual + Rich already cover 100% of this spec.
The screenshots look amateur because messages are being written to `RichLog` as mostly-plain
strings and left to wrap raw. The fix is to write Rich *renderables* instead of strings — almost
all of it lives in `append_message`.

---

## 1. The root cause and the one principle

`RichLog.write()` accepts any Rich *renderable*, not just text. Today the panel likely calls
`write(some_string)`, so wrapped lines fall back to column 0 and color is inconsistent.

**Principle:** every message becomes a Rich renderable built by a small set of "builder"
helpers, and `append_message` writes THAT renderable. No message bypasses the builders. This
single change fixes hanging indent, wrap width, and color consistency at once.

---

## 2. Hanging indent + fixed gutter — use `Table.grid`

The biggest visual fix. For each message, build a **`rich.table.Table.grid`** with two columns:

- Column 1 (gutter): fixed width (e.g. 3–4 cells), holds the status marker (● ▸ └ etc.).
- Column 2 (content): the message text/renderable. Text wraps INSIDE this column, so every
  continuation line aligns under the content edge — the hanging indent — instead of column 0.

Set the grid's column widths explicitly (fixed gutter, flexible content). Write the grid to
`RichLog`. This replaces all manual spacing; do not hand-pad strings.

Alternative for simple prose lines: wrap text in `rich.padding.Padding` with a left pad equal to
the gutter width, marker rendered on the first line. `Table.grid` is cleaner and preferred
because it keeps marker and content aligned as one unit.

---

## 3. Wrap width — constrain the content column

- Keep `RichLog(wrap=True)` so Rich word-wraps rather than hard-cutting.
- To get the readable ~90–100 column cap (instead of stretching across an ultra-wide terminal),
  constrain the content column's max width in the grid, OR set a max width on the renderable.
  Let it shrink on narrow terminals — the hanging indent still holds, so it degrades gracefully.
- Never rely on the raw terminal edge to wrap; that's what produces mid-phrase breaks.

---

## 4. The palette — a Rich `Theme` with named styles

Do NOT scatter color codes or ad-hoc markup. Define ONE Rich `rich.theme.Theme` (or a Textual
theme / CSS classes) with named styles, and reference names everywhere:

| Style name | Rendering | Used for |
|---|---|---|
| `prose` | default | normal reply text |
| `meta` | dim / grey | timings, counts ("Done · 7 tool uses · 24.7s"), token counts |
| `action` | bold | action labels: Task, Read, Write, Update, Search, Call |
| `path` | cyan (one accent) | file paths, slash commands, repo names, inline code |
| `success` | green | completed-step marker, added diff lines |
| `pending` | yellow/amber | in-progress ("Searching… 27s", "Computing… 52s") |
| `error` | red | failures, honest-stops, removed diff lines |
| `user_echo` | subtle prefix | echo of the user's own `>` input line |

Attach the Theme to the Rich Console that backs the panel (or use Textual CSS classes on the
widget). One accent color for code/paths — do not mix cyan, blue, magenta. Dim ALL meta
consistently. Bold ONLY action labels.

---

## 5. Structured blocks — Rich built-ins, appended as renderables

These match the Claude-CLI screenshots and are all native Rich objects. Build them and
`RichLog.write` them:

- **Tool-call sequences** (the `└` branches, e.g. `Read(...) └ Done (7 tool uses · 24.7s)`):
  use **`rich.tree.Tree`**. Root = the action label (styled `action`), children = sub-steps and
  the `meta` completion line. Rich draws the branch glyphs and indentation.
- **File creation / code output** (the bordered "Create file → CLAUDE.md" box): use
  **`rich.panel.Panel`** with a title. Put the code inside as `Syntax` (below).
- **Code and diffs** (the red/green diff lines in the screenshots): use
  **`rich.syntax.Syntax`** with the right lexer for highlighting. For diffs, either a diff lexer
  or a `rich.text.Text` where added lines carry the `success` style and removed lines the
  `error` style, each with a `+`/`-` gutter. Line numbers via `Syntax`'s line-number option.
- **Option / repo lists** (e.g. the `/repos` listing): use a **`rich.table.Table`** with aligned
  columns (name · stars · language), not wrapped prose. Alignment comes free from the Table.

---

## 6. Vertical rhythm

- One blank line between messages; none within a wrapped message. In `RichLog` this means write
  the message as a single renderable (the grid/panel/tree) and add spacing between writes, not
  blank `write("")` calls scattered mid-message.
- Keep a marker and its content as one renderable (the grid) so a blank line can never separate a
  bullet from the text it introduces.
- Keep `meta` lines tight under their `action` line (they're children of the same Tree/grid).

---

## 7. Where each change lives (do not touch the rest)

- **`append_message` / the RichLog write path:** ALL the work. Replace "write a string" with
  "build a renderable via a builder, then write it." Add a small set of builders:
  `build_prose(marker, text)`, `build_tool_tree(action, substeps, meta)`,
  `build_file_panel(title, code, lexer)`, `build_diff(...)`, `build_table(...)`. Route every
  message type through the matching builder.
- **The Theme/CSS:** define once (Rich Theme on the panel's Console, or Textual CSS classes), in
  one place, so the palette is consistent and tunable centrally.
- **`compose()` + Textual CSS (regions/layout):** already correct — header, chat pane, terminal
  pane, divider, status bar, modals. Do NOT change these; the fix is message rendering, not
  layout.
- **Plain-CLI fallback:** leave its simpler string path as-is (it can't use Rich renderables).
  Optionally give it a much lighter version (bare markers, no boxes) — but it's the fallback, so
  don't over-invest.

---

## 8. Acceptance checklist (what "fixed" looks like)

- [ ] Every message is written to `RichLog` as a Rich renderable, never a raw multi-line string.
- [ ] Wrapped continuation lines hang-indent under the content column (never column 0) — via
      `Table.grid` gutter+content.
- [ ] Content wraps at a readable capped width; narrow terminals still hold the indent.
- [ ] Color/weight come from ONE named Theme/CSS: meta dim, action bold, path one accent, error
      red — applied consistently.
- [ ] Tool sequences render as `Tree` with `└` branches; file/code output as `Panel` + `Syntax`;
      diffs with green/red add/remove lines and line numbers; lists as aligned `Table`.
- [ ] Exactly one blank line between messages; none inside a message.
- [ ] `compose()`/CSS layout unchanged; all changes in `append_message` + the Theme.
- [ ] Plain-CLI fallback still works (unchanged or lightly styled).

---

## 9. Summary for the engineer

No new dependency — Textual + Rich already do everything. The panel looks amateur because
`RichLog` is being fed plain strings. Fix it by routing every message through a small set of
renderable builders in `append_message`: a `Table.grid` (fixed gutter + wrapping content column)
for the hanging indent, a single Rich `Theme` for the palette (meta dim / action bold / path
accent / error red), and Rich's `Tree`, `Panel`, `Syntax`, and `Table` for tool sequences, file/
code boxes, diffs, and lists — exactly matching the Claude-CLI screenshots. Leave `compose()`,
the Textual CSS layout, and the plain-CLI fallback alone. All the polish lives in how messages
are built and written, not in the app structure.