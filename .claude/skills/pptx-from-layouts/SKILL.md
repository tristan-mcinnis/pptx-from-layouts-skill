---
name: pptx-from-layouts
description: Generate and edit PowerPoint presentations from templates. Use when user needs to create slides from outlines, modify existing decks, profile custom templates, or validate presentation quality.
---

# PPTX Presentation System

Generate consultant-ready PowerPoint presentations from markdown outlines.

## Quick Start

```bash
# Generate from outline (Inner Chapter template)
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o deck.pptx

# Edit existing deck (dump inventory, edit its JSON, apply as a file)
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --inventory -o inv.json
#   edit inv.json: change the text on the paragraph(s) you care about
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --replace inv.json -o edited.pptx

# Validate quality
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx

# Profile custom template
python .claude/skills/pptx-from-layouts/scripts/profile.py template.pptx --generate-config
```

## Core Workflow

### Generate (outline → PPTX)

1. Create outline with visual type declarations
2. Run generate command
3. Validate output

```bash
# Basic generation
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o output.pptx

# With validation
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o output.pptx --validate

# Custom template
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o output.pptx \
    --config custom-config.json --template custom-template.pptx

# Parse only (no PPTX)
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md --layout-only -o layout.json
```

### Edit (surgical changes)

Use for text-only changes to < 30% of slides.

```bash
# Extract content inventory (this is the schema replace.py consumes)
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --inventory -o inv.json

# Apply replacements (pass an edited inventory-shaped JSON file)
# Edit inv.json: find the slide-N/shape-N/paragraphs[i].text you want to change,
# leave everything else as-is, then pass the file back:
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --replace inv.json -o edited.pptx

# Reorder slides (0-indexed)
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --reorder "0,2,1,3,4" -o reordered.pptx
```

### Validate

```bash
# Basic quality check
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx

# With layout coverage analysis
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx --template template.pptx

# Compare to reference
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx --reference expected.pptx

# Generate diff report
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx --diff other.pptx -o diff.md
```

### Profile (custom templates)

```bash
# Profile and generate config
python .claude/skills/pptx-from-layouts/scripts/profile.py template.pptx --generate-config

# Specify output location
python .claude/skills/pptx-from-layouts/scripts/profile.py template.pptx \
    --name my-template --output-dir ./configs/
```

## Visual Types

Declare visual types in outlines with `**Visual: type-name**`.

| Type | Use When |
|------|----------|
| `process-N-phase` | Sequential steps (N=2-5) |
| `comparison-N` | Side-by-side options (N=2-5) |
| `cards-N` | Non-sequential items (N=2-5) |
| `data-contrast` | Two opposing metrics |
| `quote-hero` | Powerful quote |
| `hero-statement` | Single punchy statement |
| `timeline-horizontal` | Date-based sequences |
| `table` | Genuinely tabular data |
| `bullets` | Default (3+ items) |

**Decision order:** sequence → comparison → parallel items → data contrast → quote → table → hero → bullets

## Typography Markers

### Inline

| Marker | Result |
|--------|--------|
| `{blue}text{/blue}` | IC brand blue |
| `{bold}text{/bold}` | Bold |
| `{italic}text{/italic}` | Italic |
| `{question}text?{/question}` | Blue italic |
| `{signpost}LABEL{/signpost}` | Section label |

### Paragraph

| Marker | Result |
|--------|--------|
| `{bullet:-}` | Dash bullet (–) |
| `{bullet:1}` | Numbered |
| `{level:N}` | Indent level |

## Example: Full Generation

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
[Image: workshop]

[Column 3: Design]
- Solution architecture
- Prototype development
[Image: design work]

[Column 4: Deliver]
- Implementation
- Training & handover
[Image: delivery]
```

```bash
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o project.pptx --validate
```

## Example: Edit Workflow

```bash
# 1. Dump the inventory — this is the schema replace.py consumes
python .claude/skills/pptx-from-layouts/scripts/edit.py project.pptx --inventory -o inv.json

# 2. Open inv.json, find the target paragraph, change its "text" field.
#    Example: inv.json["slide-2"]["shape-3"]["paragraphs"][0]["text"] = "Q2 2026"
#    Leave every other slide/shape entry as-is (they will be re-applied verbatim).

# 3. Apply
python .claude/skills/pptx-from-layouts/scripts/edit.py project.pptx --replace inv.json -o updated.pptx

# 4. Validate
python .claude/skills/pptx-from-layouts/scripts/validate.py updated.pptx
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

## Anti-Patterns

- DON'T use edit mode for layout changes (regenerate instead)
- DON'T skip visual type decisions (bullets are boring)
- DON'T edit > 30% of slides (regenerate instead)
- DON'T forget validation step
- DON'T use `hero-statement` for content with 3+ items
- DON'T use tables for methodology/process flows
- DON'T use bullet lists for side-by-side comparisons

## Files

| Path | Purpose |
|------|---------|
| `template/inner-chapter.pptx` | Default IC template |
| `template/inner-chapter-config.json` | IC template config |
| `.claude/schemas/layout_plan.py` | Layout plan schema |

## See Also

Detailed rules in `rules/`:
- `outline-format.md` - Markdown outline syntax
- `visual-types.md` - Visual type selection
- `typography.md` - Text formatting markers
- `columns.md` - Column/card structures
- `tables.md` - Table patterns
- `editing.md` - Edit vs regenerate
- `decisions.md` - Quick reference

Reference files in `references/`:
- `layouts.md` - Inner Chapter template layout indices
