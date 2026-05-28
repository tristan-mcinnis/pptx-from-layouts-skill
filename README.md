# pptx-from-layouts

> Generate consultant-quality PowerPoint decks from markdown outlines —
> using your template's actual slide layouts, not text overlays.

## The Problem

Most PowerPoint generation tools treat templates as backgrounds to overlay text on. They:
- Duplicate decorative slides and add text boxes on top
- Fight with background designs and watermarks
- Ignore the template's carefully designed slide layouts
- Produce amateur-looking results

## The Solution

**pptx-from-layouts** understands PowerPoint's architecture. It:
- Profiles your template's slide master layouts
- Maps your semantic content to appropriate layouts
- Places content in proper placeholders
- Preserves your template's professional design

## Quick Start

### Install it yourself (one prompt)

Paste this into Claude Code, Codex, OpenCode, or any other coding agent and it'll
install the skill, wire it up to your own template, and leave you with a working
`generate.py` you can run from anywhere:

```
Please install the pptx-from-layouts Claude skill from
https://github.com/tristan-mcinnis/pptx-from-layouts-skill and adapt it to my
setup:

1. Clone the repo to a scratch directory, or fetch the .claude/skills/pptx-from-layouts
   folder directly.
2. Copy that folder to ~/.claude/skills/pptx-from-layouts/ (create the parent if
   needed). Confirm `~/.claude/skills/pptx-from-layouts/SKILL.md` exists.
3. Copy the three bundled subagents so they're usable:
     mkdir -p ~/.claude/agents
     cp ~/.claude/skills/pptx-from-layouts/agents/*.md ~/.claude/agents/
4. Install the two Python deps (Python 3.10+):
     pip install python-pptx pydantic
5. Ask me which PowerPoint template I want to use as the default. If I give you
   one, copy it to ~/.claude/skills/pptx-from-layouts/templates/default.pptx and
   onboard it (delegate to the pptx-template-onboarder subagent, or run):
     python ~/.claude/skills/pptx-from-layouts/scripts/profile.py \
       ~/.claude/skills/pptx-from-layouts/templates/default.pptx \
       --name default --generate-config \
       --output-dir ~/.claude/skills/pptx-from-layouts/templates/
   to emit the matching config JSON alongside it. If I don't have one, use the
   bundled Inner Chapter template shipped in templates/.
6. Smoke test: generate the canonical example with my new default template and
   open it so I can eyeball it.
7. Tell me the three commands I'll actually use day-to-day (generate, edit,
   validate) with my template path baked in.
```

### Or install it manually

```bash
# 1. Clone or download this repo
git clone https://github.com/tristan-mcinnis/pptx-from-layouts-skill.git
cd pptx-from-layouts-skill

# 2. Copy the skill into your Claude Code skills directory
mkdir -p ~/.claude/skills
cp -r .claude/skills/pptx-from-layouts ~/.claude/skills/

# 3. Copy the bundled subagents so Claude Code can delegate to them
mkdir -p ~/.claude/agents
cp ~/.claude/skills/pptx-from-layouts/agents/*.md ~/.claude/agents/

# 4. Install the Python dependencies (Python 3.10+)
pip install python-pptx pydantic   # or: pip install -r requirements.txt
```

### Generate a Presentation

```bash
python ~/.claude/skills/pptx-from-layouts/scripts/generate.py \
  outline.md -o presentation.pptx --template your-template.pptx
```

### Use Your Own Template (do this once, automate forever)

The bundled Inner Chapter template is only a demo. To make on-brand decks,
**onboard your own `.pptx` once** — the skill profiles its slide-master
layouts, writes a layout-mapping config, and reuses it for every future deck:

```bash
python ~/.claude/skills/pptx-from-layouts/scripts/profile.py \
  your-template.pptx --name your-template --generate-config \
  --output-dir ~/.claude/skills/pptx-from-layouts/templates/
```

That emits `your-template-config.json`. From then on every deck is one command:

```bash
python ~/.claude/skills/pptx-from-layouts/scripts/generate.py outline.md -o deck.pptx \
  --template your-template.pptx \
  --config ~/.claude/skills/pptx-from-layouts/templates/your-template-config.json --validate
```

The easiest way to do all of this is to let the **`pptx-template-onboarder`**
subagent handle it — it profiles, audits, smoke-tests, and tells you the exact
commands to use. Full guide: [Bring Your Own Template →](.claude/skills/pptx-from-layouts/rules/bring-your-own-template.md).

## Outline Format

Write markdown with visual type declarations:

```markdown
# Slide 1: Project Overview
**Visual: hero-statement**
Transforming operations through digital innovation

---

# Slide 2: Our Approach
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

# Slide 3: Investment Summary
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
| `process-N-phase` | Sequential steps (2-5) |
| `comparison-N` | Side-by-side options |
| `cards-N` | Discrete parallel items |
| `table` | Tabular data |
| `timeline-horizontal` | Date-based sequences |
| `quote-hero` | Powerful quote |

[See full reference →](docs/visual-types.md)

## Features

- **Semantic Layout Selection** — Visual types map to appropriate template layouts
- **Typography Markers** — `{blue}`, `{bold}`, `{question}` for rich formatting
- **Template Profiling** — Works with any well-designed PPTX template
- **Edit Mode** — Surgical changes to existing decks
- **Validation** — Built-in quality checks
- **Subagents** — `pptx-outline-architect`, `pptx-template-onboarder`, and `pptx-deck-qa` for delegated authoring, template onboarding, and QA

## Workflows

| Scenario | Command |
|----------|---------|
| New presentation from outline | `generate.py outline.md -o deck.pptx` |
| Use corporate template | `profile.py template.pptx` then generate |
| Fix typos in existing deck | `edit.py deck.pptx --inventory -o inv.json` → edit `paragraphs[i].text` → `edit.py deck.pptx --replace inv.json` |
| Reorder slides | `edit.py deck.pptx --reorder "0,2,1,3,4"` |

## Subagents

The skill ships three subagents (in `.claude/skills/pptx-from-layouts/agents/`,
copy to `~/.claude/agents/` to use) so an orchestrating agent can delegate
focused work:

| Subagent | Job |
|----------|-----|
| `pptx-outline-architect` | Raw brief/notes/doc → generation-ready `outline.md` with the right visual types |
| `pptx-template-onboarder` | Your `.pptx` → profiled, config'd, smoke-tested, reusable template (one-time) |
| `pptx-deck-qa` | A deck → validation verdict + surgical fix or regenerate recommendation |

Typical chain: **architect → `generate.py` → QA**. For your own template,
**onboarder** runs once up front. See
[Workflows & Subagents →](docs/workflows.md).

## Mode Decision

| Change Type | Action |
|-------------|--------|
| New presentation | generate.py |
| Typos/values (< 30% slides) | edit.py |
| Reorder slides | edit.py --reorder |
| Layout changes | Regenerate |
| Add/remove slides | Regenerate |
| > 30% slide changes | Regenerate |

## Example Output

See `examples/q1-strategy/` for a complete example:
- `outline.md` — Input markdown outline
- `output.pptx` — Generated presentation
- `thumbnail.jpg` — Visual preview

## Why This Skill?

We tested 32 PowerPoint generation skills. Most use an "inventory/replace" approach
that overlays text on template slides — which breaks with many professional templates.

**pptx-from-layouts** takes a different approach: it uses your template's slide master
layouts properly, placing content in actual placeholders instead of fighting the design.

| Skill | Score | Approach | Limitation |
|-------|-------|----------|------------|
| **pptx-from-layouts** | **95** | **Slide master layouts** | **This repo** |
| pptx-jjuidev | 94 | Template inventory/replace | Assumes text placeholders exist |
| anthropics-pptx | 90.6 | Template inventory/replace | Same limitation |
| elite-powerpoint-designer | 90 | HTML to PPTX | Creates from scratch, no template reuse |
| pptx-samhvw8 | 88 | Template inventory/replace | Same limitation |
| k-dense-pptx | 85 | Direct python-pptx | Low-level, no semantic understanding |
| python-pptx | 82 | Library wrapper | Manual slide construction |
| powerpoint-igorwarzocha | 80 | Mixed approach | Incomplete implementation |

The 7 alternatives above scored 80+/100 and are included in `alternatives/` for comparison.
See [detailed analysis →](docs/comparison.md)

## Requirements

- Python 3.10+
- python-pptx and pydantic (`pip install python-pptx pydantic`, or `pip install -r requirements.txt`)
- Claude Code (for skill + subagent integration)

## License

MIT — See [LICENSE](LICENSE) for details.
