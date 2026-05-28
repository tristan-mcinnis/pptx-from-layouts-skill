# Bring Your Own Template

Use your own PowerPoint as the template once; generate on-brand decks with one
command forever after. This is the highest-leverage thing a new user can do —
the bundled Inner Chapter template is only a demo.

## The mental model

A template is not a background. It is a set of **slide-master layouts** (Title,
Content + Image, 3-Column, Grid, Contact, …). This skill places your content
into those layouts' real placeholders. So the quality of your decks is bounded
by the quality and variety of layouts in your `.pptx`. A template with rich,
well-named layouts produces rich decks; a one-layout template produces
monotony.

## One-time setup (≈2 minutes)

Run the onboarding once per template. The `pptx-template-onboarder` subagent
automates all of this, but here are the raw steps:

```bash
SKILL=~/.claude/skills/pptx-from-layouts

# 1. Profile the template and generate its config in one step.
python $SKILL/scripts/profile.py /path/to/your-template.pptx \
    --name your-template --generate-config --output-dir $SKILL/templates/

# 2. Read the stderr summary: layout count, categories, and any
#    "signature collisions" (ambiguous layouts the generator can't tell apart).

# 3. Smoke-test with a throwaway outline so you can eyeball real output.
#    (Write your own 2–3 slide outline.md; the bundled examples/ folder is not
#     present in an installed skill.)
python $SKILL/scripts/generate.py outline.md \
    -o /tmp/byo-smoke.pptx \
    --template /path/to/your-template.pptx \
    --config $SKILL/templates/your-template-config.json --validate
```

## Make it the default (so future runs are one command)

Copy your template + config to the default names the generator looks for, and
you can drop the `--template`/`--config` flags entirely:

```bash
cp /path/to/your-template.pptx          $SKILL/templates/default.pptx
cp $SKILL/templates/your-template-config.json $SKILL/templates/default-config.json
```

> Note: `generate.py`'s built-in defaults point at the bundled
> `inner-chapter.pptx` + `inner-chapter-config.json`. To make *your* template
> the zero-flag default, either pass `--template/--config` in your saved
> workflow, or keep a tiny wrapper/alias. The onboarder subagent will set this
> up and tell you exactly which form it used.

## What "good config" looks like

Open `your-template-config.json` and sanity-check two sections:

- `layout_mappings` — each capability (`title_cover`, `column_3`,
  `content_with_image`, `grid_3x2_3body`, …) points at a real layout index.
  Missing keys mean the profiler couldn't find a matching layout in your
  template.
- `content_type_routing` — maps semantic content types to those capabilities.
  If `comparison` routes somewhere odd, your template may lack a 2-column
  layout.

`templates/inner-chapter-config.json` in this repo is a complete, working
example to compare against.

## When the auto-config isn't enough

- **Signature collisions:** two layouts have identical placeholder shapes.
  Add a `use_case` note or hand-pick the index in the config.
- **A content type lands on the wrong layout:** edit the
  `content_type_routing` entry to point at a better `layout_mappings` key.
- **Hand-editing is fine.** The config is plain JSON; nothing is generated at
  runtime that depends on it staying machine-written.

## Automating it afterward

Once the config exists, every future deck is:

```bash
python $SKILL/scripts/generate.py outline.md -o deck.pptx \
    --template /path/to/your-template.pptx \
    --config $SKILL/templates/your-template-config.json --validate
```

Write the outline (or have `pptx-outline-architect` write it), run that one
line, and let `pptx-deck-qa` check the result. That is the whole loop.
