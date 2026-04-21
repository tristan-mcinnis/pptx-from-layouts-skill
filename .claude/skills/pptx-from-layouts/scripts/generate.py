#!/usr/bin/env python3
"""
generate.py: Full PPTX generation pipeline.

Combines outline parsing, layout matching, and PPTX generation into a single command.
Optionally runs quality validation at the end.

Usage:
    # Basic generation (Inner Chapter template)
    python generate.py outline.md -o output.pptx

    # With custom template config
    python generate.py outline.md -o output.pptx --config custom-config.json

    # Parse only (no PPTX generation)
    python generate.py outline.md --layout-only -o layout.json

    # With validation
    python generate.py outline.md -o output.pptx --validate

    # Generate from existing layout JSON
    python generate.py layout.json -o output.pptx --from-layout
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Resolve paths relative to this skill
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _SKILL_DIR.parents[2]

# Local script paths (within this skill)
INGEST_SCRIPT = _SCRIPT_DIR / "ingest.py"
GENERATE_SCRIPT = _SCRIPT_DIR / "generate_pptx.py"
VALIDATE_SCRIPT = _SCRIPT_DIR / "quality_check.py"

# Default template and config (project level)
DEFAULT_TEMPLATE = _PROJECT_ROOT / "templates" / "inner-chapter.pptx"
DEFAULT_CONFIG = _PROJECT_ROOT / "templates" / "inner-chapter-config.json"


def run_command(cmd: list, description: str) -> tuple[bool, str]:
    """Run a command and return success status and output."""
    try:
        # Set up environment with skill directories in PYTHONPATH
        env = os.environ.copy()
        pythonpath = env.get('PYTHONPATH', '')
        skill_paths = f"{_SKILL_DIR}:{_SKILL_DIR / 'lib'}:{_SKILL_DIR / 'schemas'}:{_SCRIPT_DIR}"
        env['PYTHONPATH'] = f"{skill_paths}:{pythonpath}" if pythonpath else skill_paths

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
            env=env
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout
        return True, result.stdout
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Generate PowerPoint presentations from markdown outlines or layout plans.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate.py outline.md -o presentation.pptx
    Generate PPTX from outline using Inner Chapter template

  python generate.py outline.md -o deck.pptx --config custom-config.json
    Generate using custom template config

  python generate.py outline.md --layout-only -o layout.json
    Parse outline to layout plan JSON (no PPTX)

  python generate.py layout.json -o deck.pptx --from-layout
    Generate PPTX from existing layout plan JSON

  python generate.py outline.md -o deck.pptx --validate
    Generate and validate output quality
        """
    )

    parser.add_argument('input', help='Input file (markdown outline or layout JSON with --from-layout)')
    parser.add_argument('--output', '-o', required=True,
                        help='Output file path (.pptx or .json with --layout-only)')
    parser.add_argument('--config', '-c',
                        help='Template config JSON for custom templates')
    parser.add_argument('--template', '-t',
                        help='Template PPTX file (default: Inner Chapter)')
    parser.add_argument('--layout-only', action='store_true',
                        help='Only parse to layout JSON, skip PPTX generation')
    parser.add_argument('--from-layout', action='store_true',
                        help='Input is a layout JSON file, skip parsing')
    parser.add_argument('--validate', action='store_true',
                        help='Run quality validation after generation')
    parser.add_argument('--strict', action='store_true',
                        help='Exit with error on any validation warning')
    parser.add_argument('--json', action='store_true',
                        help='Output results as JSON')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed progress')

    args = parser.parse_args()

    # Validate input file exists
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    # Resolve template and config
    template_path = Path(args.template) if args.template else DEFAULT_TEMPLATE
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG

    if not template_path.exists():
        print(f"Error: Template not found: {template_path}")
        sys.exit(1)

    results = {
        "success": True,
        "stages": []
    }

    # Determine if we need to parse or already have layout JSON
    if args.from_layout:
        layout_path = input_path
        if args.verbose:
            print(f"Using existing layout plan: {layout_path}")
    else:
        # Stage 1: Parse outline to layout JSON
        if args.verbose:
            print("Stage 1: Parsing outline to layout plan...")

        if args.layout_only:
            layout_path = Path(args.output)
        else:
            layout_path = Path(tempfile.mktemp(suffix=".json"))

        ingest_cmd = [
            sys.executable, str(INGEST_SCRIPT),
            str(input_path.resolve()),
            "--output", str(layout_path)
        ]
        if config_path.exists():
            ingest_cmd.extend(["--config", str(config_path.resolve())])

        success, output = run_command(ingest_cmd, "Parse outline")

        stage_result = {
            "stage": "parse",
            "success": success,
            "output": str(layout_path) if success else None,
            "message": output if not success else None
        }
        results["stages"].append(stage_result)

        if not success:
            results["success"] = False
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(f"Error parsing outline: {output}")
            sys.exit(1)

        if args.verbose:
            print(f"  Layout plan written to: {layout_path}")

        # If layout-only, we're done
        if args.layout_only:
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(f"Layout plan generated: {layout_path}")
            sys.exit(0)

    # Stage 2: Generate PPTX from layout
    if args.verbose:
        print("Stage 2: Generating PPTX from layout plan...")

    output_path = Path(args.output)
    generate_cmd = [
        sys.executable, str(GENERATE_SCRIPT),
        str(layout_path),
        "--template", str(template_path.resolve()),
        "--output", str(output_path.resolve())
    ]
    if config_path.exists():
        generate_cmd.extend(["--config", str(config_path.resolve())])

    success, output = run_command(generate_cmd, "Generate PPTX")

    stage_result = {
        "stage": "generate",
        "success": success,
        "output": str(output_path) if success else None,
        "message": output if not success else None
    }
    results["stages"].append(stage_result)

    # Clean up temp layout file if we created one
    if not args.from_layout and not args.layout_only and layout_path.exists():
        try:
            layout_path.unlink()
        except:
            pass

    if not success:
        results["success"] = False
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"Error generating PPTX: {output}")
        sys.exit(1)

    if args.verbose:
        print(f"  PPTX generated: {output_path}")

    # Stage 3: Validate (optional)
    if args.validate and VALIDATE_SCRIPT.exists():
        if args.verbose:
            print("Stage 3: Validating output quality...")

        validate_cmd = [
            sys.executable, str(VALIDATE_SCRIPT),
            str(output_path.resolve()),
            "--template", str(template_path.resolve())
        ]
        if args.json:
            validate_cmd.append("--json")

        success, output = run_command(validate_cmd, "Validate")

        stage_result = {
            "stage": "validate",
            "success": success,
            "output": output
        }
        results["stages"].append(stage_result)

        if not success and args.strict:
            results["success"] = False

    # Final output
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Generated: {output_path}")
        if args.validate and results["stages"]:
            print("\nValidation results:")
            print(results["stages"][-1].get("output", ""))


if __name__ == "__main__":
    main()
