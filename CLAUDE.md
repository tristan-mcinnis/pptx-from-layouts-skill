# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

This repo is the distribution for a single Claude Code **skill**, `pptx-from-layouts`, that generates consultant-grade PowerPoint decks from markdown outlines. The skill itself lives at `.claude/skills/pptx-from-layouts/`; everything else (`docs/`, `examples/`, `templates/`, `alternatives/`) is supporting material.

Installation is just a copy: `cp -r .claude/skills/pptx-from-layouts ~/.claude/skills/`. There is no package to build, no test suite, no lockfile — dependencies are `Python 3.10+` and `pip install python-pptx` only.

## Commands

All entry-point scripts live in `.claude/skills/pptx-from-layouts/scripts/`. They can be run directly — they set their own `PYTHONPATH` to include the skill's `lib/`, `schemas/`, and `scripts/` when shelling out to sibling scripts.

```bash
# Generate a deck from an outline
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o deck.pptx

# Same, with validation pass
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o deck.pptx --validate

# Parse only (outline → layout_plan.json, no PPTX)
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md --layout-only -o layout.json

# Edit an existing deck (surgical text replacement / reorder)
# Replace workflow: dump inventory, edit paragraph "text" fields, pass file back.
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --inventory -o inv.json
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --replace inv.json -o edited.pptx
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --reorder "0,2,1,3,4" -o reordered.pptx

# Validate output (structural + content + layout coverage)
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx --template template.pptx
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx --diff other.pptx -o diff.md

# Profile a new template (emits a visual-type → layout-index config)
python .claude/skills/pptx-from-layouts/scripts/profile.py template.pptx --generate-config
```

### Template path gotcha

`generate.py` defaults to `<project-root>/template/inner-chapter.pptx` (singular), but in this repo the template is shipped under `templates/` (plural) at `templates/inner-chapter.pptx`, and there is no bundled `inner-chapter-config.json`. When running from this repo, pass the template explicitly:

```bash
python .claude/skills/pptx-from-layouts/scripts/generate.py \
    examples/q1-strategy/outline.md -o out.pptx \
    --template templates/inner-chapter.pptx
```

The example in `examples/q1-strategy/` is the canonical smoke test — regenerate it and diff against `examples/q1-strategy/output.pptx` to verify changes.

## Architecture

The whole skill is organized around one thesis: **use the template's slide-master layouts, don't overlay text boxes.** Every design choice flows from that.

### Pipeline

```
outline.md
   │  ingest.py        (markdown + visual-type declarations → structured plan)
   ▼
layout_plan.json
   │  generate_pptx.py (looks up layout index per visual type, fills placeholders)
   ▼
output.pptx
   │  quality_check.py / validate_pptx.py
   ▼
validation report
```

Template profiling is a separate offline step: `profile_template.py` walks a `.pptx`'s slide masters and emits a config that maps visual-type names (`hero-statement`, `process-3-phase`, `table`, …) to layout indices. Generation is just plan + config + template → PPTX.

### Directory map (skill-internal)

```
.claude/skills/pptx-from-layouts/
├── SKILL.md                    # Skill manifest + user-facing quick start
├── scripts/                    # CLI entry points and core pipeline steps
│   ├── generate.py  edit.py  validate.py  profile.py   ← user-facing entry points
│   ├── ingest.py  generate_pptx.py  profile_template.py  validate_pptx.py
│   ├── content_fitter.py  content_splitter.py  content_recovery.py
│   ├── diff_pptx.py  preview_layout.py  gantt_renderer.py
│   └── quality_check.py  visual_validator.py  validation_orchestrator.py
├── lib/                        # Reusable modules imported by scripts/
│   ├── inventory.py  replace.py  rearrange.py    # edit-mode primitives
│   ├── font_fallback.py  margins.py  pptx_compat.py
│   ├── graceful_degradation.py  performance.py  confidence.py  thumbnail.py
├── schemas/                    # Pydantic models (layout_plan, template_config,
│   │                             brand_config, checklist, generation_result, …)
├── rules/                      # Prose guidance consumed by LLMs writing outlines
│   └── visual-types.md  outline-format.md  typography.md
│       columns.md  tables.md  editing.md  decisions.md
└── references/layouts.md       # Inner Chapter template layout-index catalog (59 layouts)
```

`lib/` and `schemas/` are pure Python libraries. `scripts/` shells out between steps rather than importing across them, so each script stays runnable standalone. `rules/` and `references/` are for the *author* of the outline (Claude, or a human) — they are not imported.

### Repo map (outside the skill)

```
templates/inner-chapter.pptx    # Default template (note: singular `template/` in script defaults)
examples/q1-strategy/           # outline.md + output.pptx + thumbnail.jpg — canonical demo
alternatives/                   # 7 preserved competing skills (comparison artifact, not code in use)
docs/architecture.md            # Deeper internals writeup
docs/visual-types.md            # Visual-type decision tree + content-length limits
docs/comparison.md              # Why slide-master approach beats inventory/replace
```

### Visual types are the API

The outline-author contract is the set of visual-type declarations (`**Visual: cards-3**`, `**Visual: process-4-phase**`, …). They're resolved to layout indices by the template config. Decision order when choosing one is sequence → comparison → parallel items → data contrast → quote → tabular → hero statement → bullets. Full catalog lives in `docs/visual-types.md` and `.claude/skills/pptx-from-layouts/rules/visual-types.md`; the Inner Chapter layout-index catalog lives in `references/layouts.md`.

### Generate vs. edit

These are distinct modes with different code paths:

- **Generate** (`scripts/generate.py` → `ingest.py` → `generate_pptx.py`) builds a deck fresh from an outline. Use for new decks, > 30% slide changes, layout changes, or adding/removing slides.
- **Edit** (`scripts/edit.py` + `lib/inventory.py` + `lib/replace.py` + `lib/rearrange.py`) mutates an existing deck without regenerating. Use only for text replacement or slide reordering on < 30% of slides.

Don't use edit for layout changes — regenerate.

## Working in this repo

- When modifying the pipeline, touch `scripts/` + `lib/` in the skill; don't add new top-level directories.
- When changing outline syntax or visual-type semantics, update both `rules/` (author-facing) and `docs/visual-types.md` (external docs) — they overlap deliberately.
- When profiling new templates, drop the `.pptx` into `templates/` and commit the emitted config alongside it.
- The `alternatives/` directory is a frozen comparison artifact — do not edit those skills; they exist to document the design tradeoffs called out in `docs/comparison.md`.
