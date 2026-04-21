# Editing Mode

Decision framework for choosing between surgical editing and full regeneration.

## When to Use Edit Mode

- Fixing specific typos or wording
- Updating dates, numbers, or values
- Shortening text to fix overflow
- Reordering slides
- Making < 30% of slides need changes

## When to Regenerate Instead

- Changing slide layouts
- Adding or removing slides (except reorder)
- Restructuring content flow
- More than 30% of slides flagged
- Changing the fundamental outline

## Decision Flowchart

```
Is the change structural?
├─ Yes → Regenerate (use generate.py)
└─ No → Is it text-only?
         ├─ Yes → How many slides?
         │        ├─ 1-3 slides → edit.py
         │        └─ 4+ slides → Consider regenerate
         └─ No → What kind?
                  ├─ Reorder → edit.py --reorder
                  └─ Layout change → Regenerate
```

## Structural vs Non-Structural

**Structural changes** (regenerate):
- Different layout template
- Adding/removing columns
- Changing visual type (e.g., bullets to table)
- Adding new slides

**Non-structural changes** (edit mode):
- Fixing typos
- Updating values
- Shortening overflow text
- Moving existing slides

## Cost Comparison

| Approach | Speed | Risk |
|----------|-------|------|
| Single slide edit | Fast | Low |
| Multiple slide edits | Medium | Medium |
| Full regeneration | Medium | Medium |
| Regenerate + validate | Slower | Low |

For 4+ slide changes, regeneration is often faster and safer.

## User Request Mapping

| User Says | Action |
|-----------|--------|
| "Fix the typo on slide 3" | `edit.py --replace` |
| "Change 2025 to 2026 everywhere" | `edit.py --replace` (with replace_all) |
| "This slide has too much text" | `edit.py --replace` (shorten) |
| "Move the summary to the end" | `edit.py --reorder` |
| "Use a different layout for slide 4" | Regenerate that slide |
| "Add a new section after slide 2" | Regenerate |
| "Split this slide into two" | Regenerate |
| "Fix all the broken slides" | Check count, then decide |

## Edit Workflow

1. **Extract inventory first** (this is the schema `--replace` consumes):
   ```bash
   python edit.py deck.pptx --inventory -o inv.json
   ```

2. **Review shape IDs and content in `inv.json`**. Each slide/shape has a `paragraphs` array; the text you want to change lives under `paragraphs[i].text`.

3. **Apply targeted replacements** (edit the `text` field(s), then pass the file back):
   ```bash
   python edit.py deck.pptx --replace inv.json -o edited.pptx
   ```

4. **Or reorder slides:**
   ```bash
   python edit.py deck.pptx --reorder "0,2,1,3,4" -o reordered.pptx
   ```

## FORBIDDEN

- NEVER use edit mode to change layouts (will fail silently)
- NEVER edit more than 30% of slides (suggest regenerate)
- NEVER attempt to add slides via editing (use generate.py)
- NEVER skip the decision step - always evaluate structural vs text-only
