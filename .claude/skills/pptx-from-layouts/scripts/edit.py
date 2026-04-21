#!/usr/bin/env python3
"""
edit.py: Surgical editing of existing PowerPoint presentations.

Provides inventory extraction, text replacement, and slide reordering.

Usage:
    # Extract content inventory (this is the schema --replace consumes)
    python edit.py deck.pptx --inventory -o inv.json

    # Apply replacements from an edited inventory-shaped JSON file
    # (edit inv.json to change the text on the paragraph(s) you want, then pass it back)
    python edit.py deck.pptx --replace inv.json -o edited.pptx

    # Reorder slides
    python edit.py deck.pptx --reorder "0,2,1,3,4" -o reordered.pptx
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Resolve paths
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPT_DIR.parent
_LIB_DIR = _SKILL_DIR / "lib"
_PROJECT_ROOT = _SKILL_DIR.parents[2]

# Script paths (edit primitives live in the skill's lib/)
INVENTORY_SCRIPT = _LIB_DIR / "inventory.py"
REPLACE_SCRIPT = _LIB_DIR / "replace.py"
REARRANGE_SCRIPT = _LIB_DIR / "rearrange.py"


def run_command(cmd: list, description: str) -> tuple[bool, str]:
    """Run a command and return success status and output."""
    try:
        # Set up environment with skill directories in PYTHONPATH
        env = os.environ.copy()
        pythonpath = env.get('PYTHONPATH', '')
        skill_paths = f"{_SKILL_DIR}:{_LIB_DIR}:{_SKILL_DIR / 'schemas'}:{_SCRIPT_DIR}"
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
        description="Surgically edit PowerPoint presentations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python edit.py deck.pptx --inventory
    Extract text content inventory to JSON (stdout)

  python edit.py deck.pptx --inventory -o inventory.json
    Save inventory to file (this is the schema --replace consumes)

  python edit.py deck.pptx --replace inventory.json -o edited.pptx
    Apply replacements from an edited inventory-shaped JSON file.
    Workflow: dump inventory, hand-edit the paragraph "text" fields you want
    to change, leave every other entry as-is, then pass the file back here.

  python edit.py deck.pptx --reorder "0,2,1,3" -o reordered.pptx
    Reorder slides (0-indexed)
        """
    )

    parser.add_argument('presentation', help='Input PPTX file')
    parser.add_argument('--output', '-o',
                        help='Output file path (default: overwrites input for replace/reorder)')

    # Operation modes (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument('--inventory', action='store_true',
                            help='Extract content inventory')
    mode_group.add_argument('--replace',
                            help='Apply replacements from an inventory-shaped JSON file '
                                 '(dump with --inventory, edit paragraph text, pass back)')
    mode_group.add_argument('--reorder',
                            help='Reorder slides (comma-separated 0-indexed positions)')

    parser.add_argument('--issues-only', action='store_true',
                        help='With --inventory: only show shapes with overflow/overlap issues')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed progress')

    args = parser.parse_args()

    # Validate input file exists
    input_path = Path(args.presentation)
    if not input_path.exists():
        print(f"Error: Presentation not found: {args.presentation}")
        sys.exit(1)

    # Handle inventory mode
    if args.inventory:
        if args.verbose:
            print("Extracting content inventory...")

        # Determine output
        if args.output:
            output_path = Path(args.output)
        else:
            # Output to stdout via temp file
            import tempfile
            output_path = Path(tempfile.mktemp(suffix=".json"))

        cmd = [
            sys.executable, str(INVENTORY_SCRIPT),
            str(input_path.resolve()),
            str(output_path)
        ]
        if args.issues_only:
            cmd.append("--issues-only")

        success, output = run_command(cmd, "Extract inventory")

        if not success:
            print(f"Error extracting inventory: {output}")
            sys.exit(1)

        # Read and output result
        with open(output_path) as f:
            inventory = json.load(f)

        if not args.output:
            # Print to stdout
            print(json.dumps(inventory, indent=2))
            output_path.unlink()
        else:
            print(f"Inventory saved to: {output_path}")

        sys.exit(0)

    # Handle replace mode
    if args.replace:
        if args.verbose:
            print("Applying text replacements...")

        # Parse replacement spec
        replace_arg = args.replace
        replace_path = Path(replace_arg)

        if replace_path.exists():
            # It's a file path
            replacements_file = replace_path
        else:
            # Try parsing as JSON
            try:
                replacements = json.loads(replace_arg)
                # Write to temp file for replace.py
                import tempfile
                replacements_file = Path(tempfile.mktemp(suffix=".json"))
                with open(replacements_file, 'w') as f:
                    json.dump(replacements, f)
            except json.JSONDecodeError:
                print(f"Error: Invalid JSON or file not found: {replace_arg}")
                sys.exit(1)

        # Determine output path
        output_path = Path(args.output) if args.output else input_path

        cmd = [
            sys.executable, str(REPLACE_SCRIPT),
            str(input_path.resolve()),
            str(replacements_file.resolve()),
            str(output_path.resolve())
        ]

        success, output = run_command(cmd, "Apply replacements")

        # Clean up temp file if we created one
        if not replace_path.exists():
            replacements_file.unlink()

        if not success:
            print(f"Error applying replacements: {output}")
            sys.exit(1)

        print(f"Replacements applied: {output_path}")
        sys.exit(0)

    # Handle reorder mode
    if args.reorder:
        if args.verbose:
            print("Reordering slides...")

        # Validate sequence format
        try:
            sequence = [int(x.strip()) for x in args.reorder.split(",")]
        except ValueError:
            print("Error: Invalid sequence format. Use comma-separated integers (e.g., 0,2,1,3)")
            sys.exit(1)

        # Determine output path
        output_path = Path(args.output) if args.output else input_path

        cmd = [
            sys.executable, str(REARRANGE_SCRIPT),
            str(input_path.resolve()),
            str(output_path.resolve()),
            args.reorder
        ]

        success, output = run_command(cmd, "Reorder slides")

        if not success:
            print(f"Error reordering slides: {output}")
            sys.exit(1)

        print(f"Slides reordered: {output_path}")
        sys.exit(0)


if __name__ == "__main__":
    main()
