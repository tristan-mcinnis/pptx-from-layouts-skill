---
name: pptx-author
description: >-
  STEP 2 of the PPTX-from-layouts pipeline. Write a slide deck in markdown using
  the [HINT: layout_name] system so each slide targets a real template layout,
  then lint it before rendering. Use when the user wants to "draft slides",
  "write a deck outline", "author presentation markdown", "add layout hints", or
  has a layout catalog from Step 1 and needs to turn content into slides. Pairs
  with pptx-profile (Step 1) and pptx-from-layouts (Step 3).
---

# Step 2 — Author (pptx-author)

Write the deck as markdown where **every slide names the exact template layout it
should land on**, using `[HINT: layout_name]`. This is the bridge between "I know
my template's layouts" (Step 1) and "render the deck" (Step 3).

```text
  STEP 1 — PROFILE          STEP 2 — AUTHOR           STEP 3 — RENDER
  pptx-profile              pptx-author               pptx-from-layouts
  ───────────────           ───────────────           ─────────────────
  your-template.pptx        slides.md (+ [HINT:])     slides.md + config
        │                         │                         │
        ▼                         ▼                         ▼
   catalog.py  ──catalog.md──▶  write & lint  ──slides──▶  generate.py ─▶ deck.pptx
        └────────────────── config.json ───────────────────▲   (validated)
```

**You are here: Step 2.** You need the `layout-catalog.md` from Step 1 (or use the
bundled Inner Chapter layouts). Output is a `slides.md` the renderer consumes.

## The contract

1. **Slides are separated by `---`.** Each begins with a title and (optionally) a
   hint.
2. **`# Slide N: Title`** or **`## Title`** — the title. Make it a *claim*, not a
   label ("Three briefs, one foundation" — not "Overview").
3. **`[HINT: layout_name]`** — names a real layout from the Step-1 catalog. Copy
   the value verbatim from the catalog's **Hint** column. The engine fills that
   layout's actual placeholders.
4. **Content matches the layout:**
   - `column-*` / `grid-*` layouts → `[Column N: Header]` blocks (or `**Header**`
     + body lines).
   - image-bearing layouts → `[Image: path/to.jpg]`.
   - tabular data → a markdown table.
   - everything else → `-` bullets (the last resort, not the reflex).

### Hints vs. Visual types — when to use which

| You have… | Author with | Why |
|-----------|-------------|-----|
| A profiled template + its catalog | **`[HINT: real-layout-name]`** | Precise: targets the exact layout. The "creating" path. |
| No catalog / unsure of layouts | **`**Visual: type**`** (e.g. `process-4-phase`) | Semantic: the engine auto-picks a layout for that intent. |

`[HINT:]` always wins if both are present. A hint that doesn't match the template
is ignored with a warning and falls back to auto-detection — so **lint first**.

## Lint before you render

```bash
python scripts/lint_hints.py slides.md --config your-template-config.json
# or, against the Step-1 markdown catalog:
python scripts/lint_hints.py slides.md --catalog your-template-layout-catalog.md
# no flag → validates against the bundled Inner Chapter template
```

The linter flags, per slide: **P0** unknown hint (with a "did you mean…?"
suggestion — this *will* mis-render), **P1** missing title, **P2** bullets mixed
with a table. Exit code `2` means fix-and-re-run.

## Example

```markdown
# Slide 1: Personas that respond like real humans

[HINT: title-cover]

### Inner Chapter

---

# Slide 2: Three outcomes the research must achieve

[HINT: column-3-centered-a]

[Column 1: Level-set the teams]
- Confirm what's known about each athlete

[Column 2: Capture room content]
- In-home video, voice clips, phone recordings

[Column 3: Deliver for leadership]
- A visceral understanding for senior stakeholders

---

# Slide 3: Why personas are the hard part

[HINT: content-image-right-a]

[Image: assets/persona-diagram.png]

- No world model — pattern-matching only
- Bias toward agreeableness — more positive than real consumers
```

## Typography markers (inline)

| Marker | Result | | Marker | Result |
|--------|--------|-|--------|--------|
| `{bold}text{/bold}` | Bold | | `{bullet:-}` | Dash bullet |
| `{italic}text{/italic}` | Italic | | `{bullet:1}` | Numbered |
| `{blue}text{/blue}` | Brand color | | `{level:N}` | Indent level |

## Then what

Hand `slides.md` (plus the template + config) to **Step 3 — pptx-from-layouts**:

```bash
python ../pptx-from-layouts/scripts/generate.py slides.md -o deck.pptx \
    --template your-template.pptx --config your-template-config.json --validate
```

For turning raw notes/source docs into a hint-tagged `slides.md` automatically,
the `pptx-outline-architect` subagent does the heavy lifting.
