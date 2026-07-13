# Duckln Message Panel — Execution Guide (concrete values + iteration)

**Audience:** the engineer implementing the panel polish.
**Companion to:** MESSAGE_PANEL_FORMATTING_TEXTUAL.md (the what). This doc is the HOW —
concrete starting values, the exact primitives verified against current Textual/Rich, the
gotchas, and the iteration loop to reach the screenshot look. No code — named objects, values,
and steps.

**Verified against current Textual/Rich docs (July 2026):** `RichLog.write()` accepts a string
OR any Rich renderable; `Table.grid()` is the stripped-down layout table; `Panel`, `Tree`,
`Syntax`, `Padding`, `Text` are all Rich renderables that pass straight into `RichLog`. All
confirmed real and current.

---

## 0. Critical gotchas (read first — these WILL bite)

- **Textual `DataTable` is NOT a Rich renderable.** Writing it to `RichLog` prints
  "DataTable()". For in-log tables use **Rich `Table`** (from `rich.table`), never Textual's
  `DataTable` widget. (Confirmed in Textual discussions.)
- **`RichLog` defers rendering until its size is known** — wrapping/layout is computed when the
  widget has a width. Don't compute widths yourself at write time; let the grid/renderable size
  to the log. Set `RichLog(wrap=True, markup=True)`.
- **Markup injection risk:** if you build styled strings with f-strings and user/repo text
  contains `[` `]`, Rich may misinterpret it as markup. Use `Text` objects (or
  `Content.from_markup` with variables) for anything containing dynamic text — do NOT
  string-concat markup around untrusted content.
- **`RichLog` has `max_lines`** — set it (e.g. a few thousand) so the log prunes old lines and
  doesn't grow unbounded in a long session.

---

## 1. Layout constants — concrete starting values

Put these in ONE module-level constants block so they're tunable in one place:

- GUTTER_WIDTH = 3 (cells). Holds the marker + one space. Start at 3; if markers look cramped,
  try 4. This is the fixed first column of every message grid.
- CONTENT_MAX_WIDTH = 100 (cells). The readable cap. Content column width =
  min(available_log_width − GUTTER_WIDTH, CONTENT_MAX_WIDTH). On narrow terminals it shrinks;
  the gutter stays fixed so the hanging indent always holds.
- MESSAGE_SPACING = 1 blank line between messages (achieve by writing a blank line AFTER each
  message renderable, not inside it).
- PANEL_PADDING = (0, 1) inside boxes (0 vertical, 1 horizontal) — matches the tight look in the
  screenshots. Bump horizontal to 2 if it feels cramped.
- TREE_GUIDE_STYLE = the `meta` (dim) style, so the `└`/`├` branch lines are subtle, not loud.

Tune order later: GUTTER_WIDTH first (alignment), then CONTENT_MAX_WIDTH (line length), then
PANEL_PADDING (box density). Change one, look, repeat.

---

## 2. The message-row builder — exact recipe

For a normal prose message, build a **`Table.grid`** with two columns:

- Create it via `Table.grid(padding=0)`.
- Add column 1 with a FIXED width = GUTTER_WIDTH, no wrap, vertical-align top.
- Add column 2 with width = the computed content width (or `ratio=1` / flexible so it fills),
  wrap enabled.
- Add ONE row: (marker_text, content_renderable).
  - marker_text = a `Text` with the marker glyph styled by role (● success/green, ▸ prose,
    ✱ pending/amber, ✗ error/red).
  - content_renderable = a `Text` (styled `prose`) for plain messages, so wrapping stays inside
    column 2 → hanging indent for free.
- `RichLog.write(grid)`, then write one blank line for spacing.

That grid IS the fix for wrap-to-column-0. Every continuation line of column 2 aligns under the
content edge because the grid owns the column geometry.

---

## 3. The palette — exact roles and starting colors

Define ONE Rich `Theme` (map style-name → style) attached to the panel's Console, OR Textual CSS
classes. Starting values (adjust to taste against your terminal theme):

- prose      = default foreground (no explicit color)
- meta       = "dim" (grey) — timings, counts, token totals, tree guides
- action     = "bold" — action labels (Task, Read, Write, Update, Search, Call)
- path       = "cyan" — file paths, slash commands, repo names, inline code (ONE accent only)
- success    = "green" — completed markers, added diff lines
- pending    = "yellow" — in-progress lines ("Searching… 27s", "Computing… 52s")
- error      = "red" — failures, honest-stops, removed diff lines
- user_echo  = "dim" prefix on the `>` echo line

Rules: reference by NAME everywhere (never inline hex in builders); one accent (cyan) for
code/paths; dim ALL meta the same; bold ONLY actions. If your terminal is light-themed, swap the
palette in the Theme once — builders don't change.

---

## 4. Tool-call sequences — `rich.tree.Tree`, exact recipe

For output like `Read(file) └ Done (7 tool uses · 24.7k tokens · 32.9s)`:

- Root = a `Text`: action label styled `action`, plus the target styled `path`
  (e.g. Read + (path)).
- Add a child for each sub-step; add a final child = the completion line styled `meta`
  ("Done · N tool uses · Xs").
- Set the Tree's `guide_style` = TREE_GUIDE_STYLE (dim) so branches are subtle.
- Write the Tree to `RichLog`. Rich draws the `└`/`├` glyphs and indentation — you don't.

Nest a Tree under a grid's content column if you want the tool sequence to sit under a message
marker; otherwise write the Tree directly.

---

## 5. File/code output box — `Panel` + `Syntax`, exact recipe

For the bordered "Create file → CLAUDE.md" block:

- Build a `Syntax` renderable: the code, the lexer name (e.g. "markdown", "python", "bash"),
  `line_numbers=True`, `indent_guides=True`, and a theme (Rich ships "monokai", "ansi_dark",
  etc. — pick one that matches your palette; "ansi_dark" respects terminal colors).
- Wrap it in a `Panel`: title = the filename styled `action`, `padding=PANEL_PADDING`, a subtle
  border style (use `meta`/dim or a single accent). 
- Write the Panel to `RichLog`.

For DIFFS (red/green add/remove like the screenshot): build a `Text` line-by-line — each added
line prefixed `+` styled `success` (green), each removed line prefixed `-` styled `error` (red),
context lines `prose`, with line numbers in a fixed-width `meta` gutter. Put that `Text` inside
the same `Panel`. (A diff lexer via `Syntax` also works but line-by-line `Text` gives exact
control over the +/- gutter.)

---

## 6. Lists (e.g. `/repos`) — `rich.table.Table`, exact recipe

- Use Rich `Table` (NOT Textual DataTable — see gotchas).
- Columns: name (styled `path`), stars (styled `meta`, right-aligned), language (styled `meta`).
- `box=None` or a light box; `show_header` on or off to taste; `pad_edge=False` for a tight
  look. One row per repo. Alignment is automatic.
- Write the Table to `RichLog`.

---

## 7. The iteration loop — how to actually hit the screenshot look

Polish is iterative; this is the loop, made concrete:

1. Implement the builders (Sections 2–6) with the starting constants (Section 1) and Theme
   (Section 3).
2. Run the app, generate one of EACH message type (prose, tool tree, file panel, diff, list).
3. Screenshot your output next to the target screenshots. Compare THREE things in order:
   a. **Alignment** — do continuation lines hang-indent? do all content left-edges line up? If
      not, adjust GUTTER_WIDTH and confirm every message goes through the grid builder.
   b. **Line length** — do lines wrap at a comfortable width, no mid-phrase breaks? Adjust
      CONTENT_MAX_WIDTH.
   c. **Color/density** — is meta consistently dim, actions bold, one accent for paths? Do boxes
      feel too tight/loose? Adjust the Theme and PANEL_PADDING.
4. Change ONE variable, re-run, re-compare. Never change several at once — you won't know which
   helped.
5. Stop when alignment is perfect and color is consistent. The remaining gap to "pixel-identical"
   is glyph/shade taste — acceptable to differ slightly from an Ink-based CLI.

Budget: 1–2 short sessions of this loop after the builders work. The builders get you to
"clearly professional"; the loop gets you to "screenshot-class."

---

## 8. Definition of done (verifiable)

- [ ] Every message type routes through a builder that returns a Rich renderable; nothing writes
      a raw multi-line string to `RichLog`.
- [ ] Prose continuation lines hang-indent under the content column (via `Table.grid`).
- [ ] Content wraps at ≤ CONTENT_MAX_WIDTH; narrow terminals keep the indent.
- [ ] One `Theme`/CSS drives all color; meta dim, actions bold, paths one accent, errors red.
- [ ] Tool sequences render as `Tree` (dim guides); files/code as `Panel`+`Syntax`; diffs with
      green/red +/- and line numbers; lists as Rich `Table`.
- [ ] Exactly one blank line between messages.
- [ ] `max_lines` set on `RichLog`; dynamic text goes through `Text`/`Content.from_markup` (no
      markup injection).
- [ ] `compose()` + Textual CSS layout unchanged; plain-CLI fallback still works.
- [ ] Side-by-side with the target screenshots: alignment matches, color is consistent.

---

## 9. Summary for the engineer

Everything needed already ships with Textual + Rich — confirmed against current docs. Build the
five renderable builders (grid row, tool Tree, file Panel+Syntax, diff Text, list Table) with the
concrete constants and Theme in Sections 1–6, mind the gotchas in Section 0 (Rich `Table` not
Textual `DataTable`; `Text` for dynamic content; set `max_lines`; let `RichLog` size the widths),
then run the tight iteration loop in Section 7 — change one constant, compare to the screenshots,
repeat — until alignment is exact and color is consistent. That is the guaranteed path from the
current amateur look to the Claude-CLI-class result, with the execution details pinned down rather
than left to guesswork.