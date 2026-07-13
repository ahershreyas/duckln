# Duckln Message Panel — Formatting & Alignment Spec

**Goal:** make Duckln's terminal output read as polished and scannable as the Claude CLI —
consistent gutters, hanging indents, controlled wrap width, and a small semantic color/weight
palette. No implementation code here — approach, rules, and the target visual model.

**Problem observed:** wrapped lines fall back to column 0 (e.g. "repo," / "can" landing at the
far-left margin), text wraps at the raw terminal edge producing mid-phrase breaks, and color/
weight are applied inconsistently. The result reads amateur next to Claude CLI.

---

## 1. The core fix — a two-column layout with hanging indent

Every message is rendered as **two columns**:

```
[gutter] [content..................................]
   ●     First line of the message text wraps
         within the content column, and every
         continuation line aligns to THIS indent —
         never back to column 0.
```

- **Gutter column:** a fixed width (e.g. 3–4 chars) holding the status marker (bullet, arrow,
  tree branch). Always the same width so all content left-edges line up vertically down the
  whole panel.
- **Content column:** everything else. Text wraps INSIDE this column. Continuation lines are
  indented to the content column's left edge — a **hanging indent**. This single change is
  the biggest visual upgrade; it's why Claude CLI looks clean and the current panel doesn't.

Hint: most terminal UI libraries (e.g. Rich/Textual in Python, or equivalent) give you
"padding" / "hanging indent" primitives — use them rather than manually spacing. The key
requirement is: measure the gutter width once, and left-pad every wrapped continuation line by
exactly that width.

---

## 2. Wrap to a fixed content width, not the terminal edge

- Compute a **content width** = min(terminal_width − gutter_width − right_margin, MAX_MEASURE),
  where MAX_MEASURE is a readable cap (~90–100 columns). Word-wrap the text to THIS width
  yourself before printing.
- Never let the raw terminal hard-wrap the text — that's what produces "point / me at that
  first" breaks. Wrap on word boundaries at the computed width.
- On very narrow terminals, the content width shrinks but the hanging indent still holds, so it
  degrades gracefully.

---

## 3. A small, consistent semantic palette (roles, not ad-hoc colors)

Define a FIXED set of roles and always render each the same way. Claude CLI's restraint is what
makes it feel professional — a few roles, used consistently, not a rainbow.

| Role | Weight / Color | Used for |
|---|---|---|
| `prose` | normal, default fg | Duckln's normal reply text |
| `meta` | dim / gray | timings, counts, "Done · 3 tool uses · 12s", token counts |
| `action` | bold | action labels: "Task", "Read", "Write", "Search" |
| `path_code` | cyan (or one accent) | file paths, slash commands (`/repos`), inline code, repo names |
| `success` | green marker | completed step bullet |
| `pending` | yellow/amber marker | in-progress ("Computing… 52s") |
| `error` | red | failures, honest-stops |
| `user_echo` | subtle prefix (`>`) | echo of the user's own input line |

Rules: one accent color for code/paths (don't mix cyan and blue and magenta), dim ALL meta
consistently, bold ONLY action labels. If a token doesn't map to a role, it's `prose`.

---

## 4. Structured blocks for structured content

Not everything is a flat bullet line. Match content to a container:

- **Tool/step sequences** → a light **tree** with branch glyphs (`├`, `└`) and consistent
  indentation, as in Claude CLI's `Read(...) └ Done`. Groups related sub-steps visually.
- **File creation / code output** → a **bordered box** with a title bar ("Create file" →
  `CLAUDE.md`), as in Image 3. The border sets the block apart from prose.
- **Lists of options / repos** → aligned rows, not wrapped prose. If listing repos, one per
  line with aligned columns (name · stars · language), not a run-on sentence.
- **Plans (plan mode)** → Duckln already renders plans; reuse that same bordered/numbered
  style for other structured output so the whole tool feels consistent.

---

## 5. Vertical rhythm

- **One blank line between messages**, none within a wrapped message. Claude CLI's breathing
  room comes from consistent single-line gaps between blocks, not scattered blank lines.
- **Group a marker with its content** — the bullet and its text are one unit; don't let a
  blank line separate a bullet from the text it introduces.
- Keep meta lines (timings) tight under their action, not floating.

---

## 6. Alignment checklist (what "fixed" looks like)

- [ ] Every message's content left-edge lines up vertically down the panel (fixed gutter).
- [ ] Wrapped continuation lines are hanging-indented to the content column — never column 0.
- [ ] Text wraps at a computed readable width (~90–100 col cap), on word boundaries.
- [ ] Meta (timings/counts) is uniformly dim; action labels uniformly bold; paths/code one
      accent color.
- [ ] Tool sequences use a consistent tree; file/code output uses a bordered block.
- [ ] Exactly one blank line between messages; none inside a wrapped message.
- [ ] Narrow-terminal case still holds the hanging indent (degrades gracefully).

---

## 7. Implementation hints (no code, just direction)

- Use a terminal-UI rendering library that supports **padding, hanging indent, styled spans,
  boxes, and trees** natively (e.g. Rich/Textual for Python) rather than hand-emitting ANSI
  escape codes — hand-rolled ANSI is exactly how alignment drifts.
- Centralize the palette (the role→style map) and the layout constants (gutter width, max
  measure) in ONE style module, so the whole panel stays consistent and is tunable in one
  place.
- Render every message through a single "render_message(role, marker, text)" style path so no
  output bypasses the two-column layout. Ad-hoc `print()` calls are what make it look amateur —
  route everything through the formatter.

---

## 8. Summary for the engineer

The amateur look comes from three fixable things: wrapped text falling to column 0 (fix with a
fixed gutter + hanging indent), wrapping at the raw terminal edge (fix by computing a readable
content width and word-wrapping yourself), and inconsistent color/weight (fix with a small fixed
role→style palette). Add bordered blocks for file/code output and tree glyphs for tool
sequences, keep one blank line between messages, and route ALL output through one formatter so
nothing bypasses the layout. Use a terminal-UI library's native padding/box/tree primitives
rather than hand-rolled ANSI. That gets Duckln to Claude-CLI-level polish.