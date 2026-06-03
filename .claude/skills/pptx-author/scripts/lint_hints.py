#!/usr/bin/env python3
"""
lint_hints.py — Step 2 of the PPTX-from-layouts pipeline: AUTHOR (preflight).

Validates a markdown slide draft BEFORE you render it (Step 3), so you catch
mistakes while editing instead of in PowerPoint. It checks, per slide:

  • [HINT: layout_name] names a layout that actually exists in the template
    (validated against the Step-1 config / catalog). Unknown names get a
    "did you mean …?" suggestion. This is a P0 — it WILL mis-render.
  • Each slide has a title (## or # heading) — P1.
  • [HINT:] and a markdown table aren't mixed with many bullets — P2 advisory.

Exit codes:  0 = clean,  2 = P0 issues found,  3 = P0 found with --strict-any.

Usage:
    python lint_hints.py slides.md --config my-template-config.json
    python lint_hints.py slides.md --catalog my-template-layout-catalog.md
    python lint_hints.py slides.md            # falls back to bundled Inner Chapter
"""

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILLS_ROOT = _SCRIPT_DIR.parent.parent  # .claude/skills/
# Bundled default config travels with the repo (the render engine's template).
_DEFAULT_CONFIG = (
    _SKILLS_ROOT.parents[1] / "templates" / "inner-chapter-config.json"
)


def _norm(s: str) -> str:
    return s.lower().strip().replace(" ", "_").replace("-", "_")


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


def load_valid_hints(config_path: Path | None, catalog_path: Path | None) -> set[str]:
    """Collect the set of valid hint names (normalized) from config and/or catalog."""
    names: set[str] = set()

    if config_path and config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            for cap_key, cap in cfg.get("layout_mappings", {}).items():
                names.add(_norm(cap_key))
                if cap.get("layout_name"):
                    names.add(_norm(cap["layout_name"]))
        except Exception as e:
            print(f"[warn] could not read config {config_path}: {e}", file=sys.stderr)

    if catalog_path and catalog_path.exists():
        # Pull every `name` from inline-code cells in the catalog markdown.
        for m in re.finditer(r"`([a-z0-9][a-z0-9 _-]+)`", catalog_path.read_text(encoding="utf-8")):
            names.add(_norm(m.group(1)))

    return names


def suggest(hint: str, valid: set[str]) -> str | None:
    target = _norm(hint)
    best, best_d = None, 99
    for v in valid:
        d = _levenshtein(target, v)
        if d < best_d:
            best, best_d = v, d
    # Only suggest if reasonably close.
    if best is not None and best_d <= max(2, len(target) // 3):
        return best.replace("_", "-")
    return None


def split_slides(md: str) -> list[str]:
    """Split on --- dividers (the engine's primary slide separator)."""
    blocks = re.split(r"^\s*---\s*$", md, flags=re.MULTILINE)
    return [b for b in blocks if b.strip()]


def lint(md: str, valid: set[str]) -> list[tuple[str, int, str]]:
    """Return a list of (tier, slide_no, message)."""
    issues: list[tuple[str, int, str]] = []
    have_catalog = bool(valid)

    for i, block in enumerate(split_slides(md), 1):
        hint_m = re.search(r"\[HINT:\s*([^\]]+)\]", block, re.IGNORECASE)
        has_title = bool(re.search(r"^#{1,2}\s+\S", block, re.MULTILINE))
        has_table = bool(re.search(r"^\s*\|[\s\-:]+\|", block, re.MULTILINE))
        bullet_n = len(re.findall(r"^[\-\*]\s+\S", block, re.MULTILINE))

        if hint_m and have_catalog:
            hint = hint_m.group(1).strip()
            if _norm(hint) not in valid:
                tip = suggest(hint, valid)
                msg = f"unknown layout hint '[HINT: {hint}]'"
                if tip:
                    msg += f" — did you mean '[HINT: {tip}]'?"
                else:
                    msg += " — not in this template's catalog"
                issues.append(("P0", i, msg))

        if not has_title:
            issues.append(("P1", i, "slide has no title (add a `## Headline` claim)"))

        if hint_m and has_table and bullet_n > 2:
            issues.append(("P2", i, f"{bullet_n} bullets mixed with a table — "
                                    "split into two slides or cap bullets at 2"))

    return issues


def main():
    ap = argparse.ArgumentParser(description="Step 2 preflight: validate [HINT:] usage before render.")
    ap.add_argument("markdown", help="The slide draft to lint")
    ap.add_argument("--config", "-c", help="Template config JSON from Step 1 (pptx-profile)")
    ap.add_argument("--catalog", help="Layout-catalog markdown from Step 1 (alternative to --config)")
    ap.add_argument("--strict-any", action="store_true",
                    help="Treat P1/P2 as blocking too (exit 3)")
    args = ap.parse_args()

    md_path = Path(args.markdown)
    if not md_path.exists():
        print(f"ERROR: file not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    config_path = Path(args.config) if args.config else (
        _DEFAULT_CONFIG if _DEFAULT_CONFIG.exists() else None)
    catalog_path = Path(args.catalog) if args.catalog else None
    valid = load_valid_hints(config_path, catalog_path)

    if not valid:
        print("[warn] no catalog/config found — hint names cannot be validated. "
              "Run Step 1 (pptx-profile) and pass --config.", file=sys.stderr)

    issues = lint(md_path.read_text(encoding="utf-8"), valid)

    if not issues:
        src = config_path.name if config_path else catalog_path.name if catalog_path else "no catalog"
        print(f"✓ preflight clean ({src}) — ready to render.")
        sys.exit(0)

    order = {"P0": 0, "P1": 1, "P2": 2}
    issues.sort(key=lambda t: (order[t[0]], t[1]))
    p0 = sum(1 for t in issues if t[0] == "P0")
    for tier, slide_no, msg in issues:
        marker = "✗" if tier == "P0" else ("!" if tier == "P1" else "·")
        print(f"  {marker} [{tier}] slide {slide_no}: {msg}")

    print(f"\n{p0} P0 issue(s). " + ("Fix the markdown and re-run." if p0 else
          "No blocking issues."))

    if p0:
        sys.exit(2)
    if args.strict_any and len(issues) > p0:
        sys.exit(3)
    sys.exit(0)


if __name__ == "__main__":
    main()
