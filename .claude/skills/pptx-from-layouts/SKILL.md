---
name: pptx-from-layouts
description: >-
  Generate and edit consultant-grade PowerPoint decks from markdown outlines
  using a template's real slide-master layouts (not text overlays). Use when
  the user wants to create slides/a deck/a presentation from an outline or
  brief, modify an existing .pptx, turn their own PowerPoint into a reusable
  template, or check a deck's quality. Triggers: "make a deck", "build a
  presentation", "slides for…", "use my company template", "fix slide N".
---

# PPTX from Layouts

Generate consultant-ready PowerPoint presentations from markdown outlines by
filling the template's actual slide-master layouts. **Core thesis: use the
template's layouts and placeholders — never overlay text boxes on slides.**

## Dependencies (check first)

Requires **Python 3.10+**, **python-pptx**, and **pydantic**:

```bash
pip install python-pptx pydantic     # or: pip install -r requirements.txt
```

If you hit `ModuleNotFoundError: No module named 'pptx'` or `'pydantic'`, this
is why. The entry-point scripts set their own `PYTHONPATH`; run them directly
with `python`.

## Decision: which path am I on?

| The user wants… | Do this |
|-----------------|---------|
| A deck from a brief / notes / source doc (no outline yet) | Delegate to **`pptx-outline-architect`**, then generate |
| A deck and they already wrote an outline | `generate.py` → validate |
| To use **their own** PowerPoint as the template | Delegate to **`pptx-template-onboarder`** (one-time setup) |
| Small text fixes / reorder on an existing deck (<30% slides) | `edit.py` (inventory → replace / reorder) |
| Layout changes, added/removed slides, or >30% churn | Regenerate via `generate.py` — **never** edit |
| To know if a deck is good | Delegate to **`pptx-deck-qa`** or run `validate.py` |

## Mandatory generation workflow (do not skip steps)

1. **Pick or confirm the template.** First time with a custom template? Onboard
   it once (see *Bring your own template* below). Otherwise the bundled Inner
   Chapter template is the default.
2. **Produce an outline** with a `**Visual: <type>**` declaration on EVERY
   content slide. Choose visual types with the decision order below — do not
   default everything to bullets.
3. **Generate:**
   ```bash
   python scripts/generate.py outline.md -o deck.pptx --validate
   ```
   (Add `--template <t.pptx> --config <c.json>` for a custom template.)
4. **Validate and read the report.** A deck is not done until validation is
   clean of errors and text overflow. If it isn't, fix and re-run.
5. **Report the score and the output path** to the user.

### Definition of done (self-check before claiming success)

- [ ] Every content slide had an explicit `**Visual:**` declaration.
- [ ] `generate.py` exited successfully and the `.pptx` exists.
- [ ] `validate.py` reports **zero errors** and **no text overflow**.
- [ ] No anti-pattern below was used (hero-statement for lists, tables for
      process flows, bullets for comparisons, …).
- [ ] You reported the validation score and file path, not just "done".

If any box is unchecked, the task is not finished — fix it or say explicitly
what blocked you.

## Subagents

Canonical definitions in `agents/`; install to `~/.claude/agents/` to use them.

| Subagent | Use it to |
|----------|-----------|
| `pptx-outline-architect` | Turn raw material into a generation-ready `outline.md` with correct visual types |
| `pptx-template-onboarder` | Onboard a user's `.pptx` as a reusable, on-brand template (one-time) |
| `pptx-deck-qa` | Validate a deck and apply a surgical fix or recommend regeneration |

They chain as **architect → `generate.py` → QA**; for your own template,
**onboarder** runs once up front. (Full write-up: `docs/workflows.md` in the repo.)

## Quick Start

```bash
# Generate from outline (bundled Inner Chapter template, with validation)
python scripts/generate.py outline.md -o deck.pptx --validate

# Use your own template
python scripts/generate.py outline.md -o deck.pptx \
    --template your-template.pptx --config your-template-config.json --validate

# Edit existing deck (dump inventory, edit its JSON, apply as a file)
python scripts/edit.py deck.pptx --inventory -o inv.json
#   edit inv.json: change the text on the paragraph(s) you care about
python scripts/edit.py deck.pptx --replace inv.json -o edited.pptx

# Validate quality
python scripts/validate.py deck.pptx

# Profile a custom template (one-time onboarding)
python scripts/profile.py template.pptx --name my-template --generate-config
```

## Bring your own template (one-time, then automate forever)

The bundled template is only a demo. To make on-brand decks, onboard your own
`.pptx` **once** — profile it, generate its layout-mapping config, smoke-test,
and reuse it for every future deck.

```bash
python scripts/profile.py /path/to/your-template.pptx \
    --name your-template --generate-config --output-dir templates/
```

This emits `your-template-config.json`. After that, every deck is a single
`generate.py` call with `--template`/`--config`. Full guide:
`rules/bring-your-own-template.md`. To automate the whole thing, delegate to
`pptx-template-onboarder`.

## Visual Types

Declare in outlines with `**Visual: type-name**`.

| Type | Use When |
|------|----------|
| `process-N-phase` | Sequential steps (N=2-5) |
| `comparison-N` | Side-by-side options (N=2-5) |
| `cards-N` | Non-sequential parallel items (N=2-5) |
| `data-contrast` | Two opposing metrics |
| `quote-hero` | Powerful quote |
| `hero-statement` | Single punchy statement ONLY |
| `timeline-horizontal` | Date-based sequences |
| `table` | Genuinely tabular data |
| `bullets` | Default (3+ items) — last resort |

**Decision order:** sequence → comparison → parallel items → data contrast →
quote → table → hero → bullets. Full reference + per-type length limits:
`rules/visual-types.md`.

## Typography Markers

| Inline | Result | | Paragraph | Result |
|--------|--------|-|-----------|--------|
| `{blue}text{/blue}` | IC brand blue | | `{bullet:-}` | Dash bullet (–) |
| `{bold}text{/bold}` | Bold | | `{bullet:1}` | Numbered |
| `{italic}text{/italic}` | Italic | | `{level:N}` | Indent level |
| `{question}text?{/question}` | Blue italic | | | |
| `{signpost}LABEL{/signpost}` | Section label | | | |

## Example outline

```markdown
# Project Overview
**Visual: hero-statement**
Transforming operations through digital innovation

# Our Approach
**Visual: process-4-phase**

[Column 1: Discover]
- Stakeholder interviews
- Competitive audit
[Image: research process]

[Column 2: Define]
- Workshop facilitation
- Strategic framework

[Column 3: Design]
- Solution architecture
- Prototype development

[Column 4: Deliver]
- Implementation
- Training & handover
```

## Edit workflow

```bash
# 1. Dump the inventory — this is the schema replace.py consumes
python scripts/edit.py project.pptx --inventory -o inv.json
# 2. Open inv.json, find the target paragraph, change only its "text" field.
#    e.g. inv.json["slide-2"]["shape-3"]["paragraphs"][0]["text"] = "Q2 2026"
# 3. Apply, then validate
python scripts/edit.py project.pptx --replace inv.json -o updated.pptx
python scripts/validate.py updated.pptx
```

## Mode Decision

| Change Type | Action |
|-------------|--------|
| New presentation | generate.py |
| Typos/values (< 30% slides) | edit.py |
| Reorder slides | edit.py --reorder |
| Layout changes | Regenerate |
| Add/remove slides | Regenerate |
| > 30% slide changes | Regenerate |

## Anti-Patterns (FORBIDDEN)

- DON'T skip the `**Visual:**` declaration — the parser default may not match.
- DON'T default to bullets when a richer visual type fits — bullets are the
  last resort, not the reflex.
- DON'T use `hero-statement` for 3+ items or multi-sentence content.
- DON'T use tables for methodology/process flows (use `process-N-phase`).
- DON'T use bullet lists for side-by-side comparisons (use `comparison-N`).
- DON'T use edit mode for layout changes or to add/remove slides — regenerate.
- DON'T edit > 30% of slides — regenerate instead.
- DON'T claim success without a clean validation pass.

## See Also

Author-facing rules in `rules/`:
`outline-format.md`, `visual-types.md`, `typography.md`, `columns.md`,
`tables.md`, `editing.md`, `bring-your-own-template.md`, `decisions.md`.

Reference: `references/layouts.md` (Inner Chapter layout indices).
Subagents: `agents/` (this skill). Workflows write-up: `docs/workflows.md` (repo).
