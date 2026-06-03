# PPTX from Layouts

Generate consultant-quality PowerPoint presentations from markdown outlines using your template's actual slide master layouts.

## Overview

Most PowerPoint generation approaches overlay text on template slides, fighting with backgrounds and decorative elements. This skill properly uses slide master layouts, placing content in actual placeholders to preserve professional design.

## Installation

```bash
npx skills add <owner>/pptx-from-layouts-skill
```

Then install dependencies:
```bash
pip install python-pptx
```

## Core Commands

### Generate Presentation
```bash
python .claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o presentation.pptx
```

### Profile Custom Template
```bash
python .claude/skills/pptx-from-layouts/scripts/profile.py your-template.pptx --generate-config
```

### Edit Existing Deck
```bash
# View content inventory
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --inventory

# Replace text
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --replace '{"slide":3,"old":"2025","new":"2026"}'

# Reorder slides
python .claude/skills/pptx-from-layouts/scripts/edit.py deck.pptx --reorder "0,2,1,3,4" -o reordered.pptx
```

### Validate Output
```bash
python .claude/skills/pptx-from-layouts/scripts/validate.py deck.pptx
```

## Outline Format

Write markdown with visual type declarations:

```markdown
# Project Overview
**Visual: hero-statement**
Transforming operations through digital innovation

---

# Our Approach
**Visual: process-3-phase**

[Column 1: Discover]
- Stakeholder interviews
- Competitive audit

[Column 2: Define]
- Workshop facilitation
- Strategic framework

[Column 3: Deliver]
- Implementation
- Training & handover

---

# Investment Summary
**Visual: table**

| Category | Investment |
|----------|------------|
| Research | $50,000 |
| Design | $75,000 |
| **Total** | **$125,000** |
```

## Visual Types

| Type | Use When |
|------|----------|
| `hero-statement` | Single punchy tagline |
| `process-N-phase` | Sequential steps (N=2-5) |
| `comparison-N` | Side-by-side options |
| `cards-N` | Discrete parallel items |
| `data-contrast` | Two opposing metrics |
| `quote-hero` | Powerful quote |
| `timeline-horizontal` | Date-based sequences |
| `table` | Genuinely tabular data |
| `bullets` | Default for 3+ items |

**Selection order:** sequence → comparison → parallel items → data contrast → quote → table → hero → bullets

## Typography Markers

### Inline Formatting
| Marker | Result |
|--------|--------|
| `{blue}text{/blue}` | Brand accent color |
| `{bold}text{/bold}` | Bold |
| `{italic}text{/italic}` | Italic |
| `{question}text?{/question}` | Emphasized question |

### Paragraph Formatting
| Marker | Result |
|--------|--------|
| `{bullet:-}` | Dash bullet |
| `{bullet:1}` | Numbered |
| `{level:N}` | Indent level |

## Decision Guide

| Change Type | Action |
|-------------|--------|
| New presentation | `generate.py` |
| Typos/values (< 30% slides) | `edit.py` |
| Reorder slides | `edit.py --reorder` |
| Layout changes | Regenerate |
| Add/remove slides | Regenerate |
| > 30% slide changes | Regenerate |

## Anti-Patterns

- DON'T use edit mode for layout changes (regenerate instead)
- DON'T skip visual type decisions (bullets are boring)
- DON'T edit > 30% of slides (regenerate instead)
- DON'T use `hero-statement` for content with 3+ items
- DON'T use tables for methodology/process flows
- DON'T use bullet lists for side-by-side comparisons

## Requirements

- Python 3.10+
- python-pptx library

## File Structure

```
.claude/skills/pptx-from-layouts/
  scripts/
    generate.py      # Outline → PPTX generation
    edit.py          # Surgical edits to existing decks
    validate.py      # Quality validation
    profile.py       # Template profiling
  references/
    layouts.md       # Template layout indices
  rules/
    visual-types.md  # Visual type selection guide
    typography.md    # Text formatting reference
    outline-format.md # Markdown syntax
  lib/               # Core library modules
  schemas/           # Pydantic schemas
templates/
  inner-chapter.pptx # Default template
```
