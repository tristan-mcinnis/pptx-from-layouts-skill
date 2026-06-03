# Workflows & Subagents

## The 3-step pipeline (Profile → Author → Render)

The deck-building flow is split into three skills, one per step, so the workflow
is explicit and you can enter at any stage:

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

| Step | Skill | Entry script | Output |
|------|-------|--------------|--------|
| 1 · Profile | `pptx-profile` | `scripts/catalog.py template.pptx` | `*-layout-catalog.md` + `*-config.json` |
| 2 · Author | `pptx-author` | `scripts/lint_hints.py slides.md --config …` | a `[HINT:]`-tagged, lint-clean `slides.md` |
| 3 · Render | `pptx-from-layouts` | `scripts/generate.py slides.md -o deck.pptx --config …` | a validated `deck.pptx` |

`[HINT: layout_name]` is what ties the steps together — Step 1 enumerates a
template's real layout names, Step 2 names one per slide, Step 3 fills that exact
layout. The three **subagents** below automate the work *inside* each step (the
onboarder wraps Step 1, the architect wraps Step 2, QA follows Step 3). Using the
bundled Inner Chapter template? You can skip Step 1 and start at Step 2/3.

---

This skill ships three subagents and a small set of end-to-end workflows that
chain them. The subagents let an orchestrating agent delegate focused work
(authoring, onboarding, QA) instead of doing everything in one context.

## Subagents

| Subagent | Job | When to delegate |
|----------|-----|------------------|
| `pptx-outline-architect` | Raw material → generation-ready `outline.md` with correct `**Visual:**` declarations | User has content/a brief but no structured outline; "make a deck about X" |
| `pptx-template-onboarder` | A user's `.pptx` → profiled, config'd, smoke-tested, reusable default template | User provides a custom template; "use my company template" |
| `pptx-deck-qa` | A `.pptx` → validation verdict + surgical fix or regenerate recommendation | After generate/edit; "is this deck good?", reported visual problem |

The canonical definitions live in
`.claude/skills/pptx-from-layouts/agents/`. To use them as Claude Code
subagents, copy them into `~/.claude/agents/` (the one-prompt installer in the
README does this for you). They also live in this repo's `.claude/agents/` so
they're active when developing the skill itself.

## Workflow A — New deck from a brief (most common)

```
brief / notes / source doc
        │  delegate → pptx-outline-architect
        ▼
   outline.md  (visual types chosen, length limits respected)
        │  python scripts/generate.py outline.md -o deck.pptx --validate
        ▼
   deck.pptx
        │  delegate → pptx-deck-qa
        ▼
   PASS  (or a surgical edit / regenerate recommendation)
```

The orchestrator owns the `generate.py` call in the middle; the subagents own
the authoring and the QA. This keeps each context small and each step
verifiable.

## Workflow B — First time with your own template

```
your-template.pptx
        │  delegate → pptx-template-onboarder
        ▼
   your-template-config.json  (+ profile, digest, smoke-test report)
        │  set as default (optional)
        ▼
   ready: every future deck is one generate.py command
```

Do this once. See `rules/bring-your-own-template.md` for the manual steps.

## Workflow C — Editing an existing deck

```
deck.pptx
        │  edit.py --inventory -o inv.json
        ▼
   inv.json  (edit paragraphs[i].text on the slides you care about)
        │  edit.py --replace inv.json -o edited.pptx
        ▼
   edited.pptx
        │  delegate → pptx-deck-qa
        ▼
   PASS
```

Only for text changes on <30% of slides, or reordering. Layout changes,
added/removed slides, or >30% churn → regenerate via Workflow A instead.

## Why delegate at all?

- **Separation of concerns.** Authoring (creative), onboarding (setup), and QA
  (verification) have different failure modes and different "done" criteria.
- **Smaller contexts.** The architect doesn't need the validator's output in
  its window, and vice versa.
- **Repeatability.** Each subagent encodes the hard rules for its phase, so the
  same standards apply whether you run it once or fifty times.

For a single quick deck you don't have to delegate — running the scripts
directly is fine. Delegate when the work is large, repeated, or when you want a
clean QA gate before the deck reaches a client.
