---
name: pptx-profile
description: >-
  STEP 1 of the PPTX-from-layouts pipeline. Profile a PowerPoint template to
  discover its real slide-master layouts and emit a layout catalog + render
  config. Use when the user wants to "use my own PowerPoint template", "see
  what layouts my deck has", "onboard a template", "profile this pptx", or
  before authoring slides against a custom (non-Inner-Chapter) template.
  Pairs with pptx-author (Step 2) and pptx-from-layouts (Step 3).
---

# Step 1 — Profile (pptx-profile)

Turn any `.pptx` into a **map of its real layouts** so the next steps can target
them precisely. This is the answer to *"what layouts does my template actually
have, and what is each one for?"*

```text
  STEP 1 — PROFILE          STEP 2 — AUTHOR           STEP 3 — RENDER
  pptx-profile              pptx-author               pptx-from-layouts
  ───────────────           ───────────────           ─────────────────
  your-template.pptx        slides.md (+ [HINT:])     slides.md + config
        │                         │                         │
        ▼                         ▼                         ▼
   catalog.py  ──catalog.md──▶  write & lint  ──slides──▶  generate.py ─▶ deck.pptx
        └────────────────── config.json ───────────────────▲   (validated)
  "what layouts do I        "write slides that        "fill the template's
   have?"                    name real layouts"        actual placeholders"
```

**You are here: Step 1.** Output feeds Steps 2 and 3.

## Dependencies

Python 3.10+ and **python-pptx** (`pip install python-pptx`). The config step
shells out to the sibling `pptx-from-layouts` skill, which ships in this repo.

## Run it

```bash
python scripts/catalog.py /path/to/your-template.pptx \
    --name your-template --output-dir ./build
```

This writes two files into `./build`:

| File | For whom | Purpose |
|------|----------|---------|
| `your-template-layout-catalog.md` | **you / Step 2 author** | Human-readable menu of every hintable layout: its `[HINT:]` name, placeholders, and when to use it. |
| `your-template-config.json` | **Step 3 render engine** | Machine map of capability → real layout index/name. Passed to `generate.py --config`. |

Options: `--catalog-only` skips the config (catalog only); `--name` sets the
output basename (defaults to the filename).

## What the catalog gives you

The catalog's **Hintable layouts** table lists only layouts the engine knows how
to populate — every name there is a safe `[HINT:]` value. A collapsed section
lists *all* raw layouts in the file for reference. Each row shows the layout
index, the exact hint name to copy, its placeholders (e.g. `picture`, `body`,
`center-title`), and a one-line "when to use".

## Then what

1. **Step 2 — author:** open the catalog, write `slides.md` using
   `[HINT: layout_name]` per slide, and lint with
   `pptx-author/scripts/lint_hints.py slides.md --config your-template-config.json`.
2. **Step 3 — render:**
   `pptx-from-layouts/scripts/generate.py slides.md -o deck.pptx
   --template your-template.pptx --config your-template-config.json --validate`.

## Notes

- **You only profile a template once.** Reuse the catalog + config for every
  future deck on that template.
- The bundled **Inner Chapter** template is already profiled (see
  `templates/inner-chapter-config.json`) and is the default if you skip Step 1.
- For a fully automated, on-brand onboarding (brand colors, fonts, smoke-test),
  the `pptx-template-onboarder` subagent wraps this step.
