#!/usr/bin/env python3
"""
validate.py: Quality validation for PowerPoint presentations.

Checks for common issues and validates against references.

Usage:
    # Basic quality check
    python validate.py deck.pptx

    # With template coverage analysis
    python validate.py deck.pptx --template template.pptx

    # Compare against reference
    python validate.py deck.pptx --reference expected.pptx

    # With layout plan for advanced validation
    python validate.py deck.pptx --layout-plan layout.json
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Resolve paths
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _SKILL_DIR.parents[2]

# Sibling script paths (all live alongside this file)
QUALITY_CHECK_SCRIPT = _SCRIPT_DIR / "quality_check.py"
VALIDATE_PPTX_SCRIPT = _SCRIPT_DIR / "validate_pptx.py"
DIFF_PPTX_SCRIPT = _SCRIPT_DIR / "diff_pptx.py"

# Default template
DEFAULT_TEMPLATE = _PROJECT_ROOT / "templates" / "inner-chapter.pptx"


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
        description="Validate PowerPoint presentation quality.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python validate.py deck.pptx
    Run quality checks (empty slides, placeholder text, overflow, etc.)

  python validate.py deck.pptx --template template.pptx
    Include layout coverage analysis

  python validate.py deck.pptx --reference expected.pptx
    Compare against reference PPTX

  python validate.py deck.pptx --layout-plan layout.json
    Include Tier 2 validation (typography, visual types, columns)

  python validate.py deck.pptx --diff reference.pptx --output diff.md
    Generate detailed diff report
        """
    )

    parser.add_argument('presentation', help='PPTX file to validate')
    parser.add_argument('--template', '-t',
                        help='Template PPTX for layout coverage analysis')
    parser.add_argument('--reference', '-r',
                        help='Reference PPTX for comparison')
    parser.add_argument('--layout-plan', '-l',
                        help='Layout plan JSON for Tier 2 validation')
    parser.add_argument('--diff',
                        help='Generate detailed diff against this PPTX')
    parser.add_argument('--output', '-o',
                        help='Output file for report')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('--strict', action='store_true',
                        help='Exit with error on any warning')
    parser.add_argument('--no-parallel', action='store_true',
                        help='Disable parallel validation')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed progress')

    args = parser.parse_args()

    # Validate input file exists
    input_path = Path(args.presentation)
    if not input_path.exists():
        print(f"Error: Presentation not found: {args.presentation}")
        sys.exit(1)

    # Handle diff mode separately
    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(f"Error: Diff reference not found: {args.diff}")
            sys.exit(1)

        if args.verbose:
            print(f"Generating diff: {input_path} vs {diff_path}")

        cmd = [
            sys.executable, str(DIFF_PPTX_SCRIPT),
            str(input_path.resolve()),
            str(diff_path.resolve())
        ]
        if args.output:
            cmd.extend(["--output", str(Path(args.output).resolve())])

        success, output = run_command(cmd, "Generate diff")

        if not success:
            print(f"Error generating diff: {output}")
            sys.exit(1)

        print(output)
        sys.exit(0)

    # Handle reference comparison
    if args.reference:
        ref_path = Path(args.reference)
        if not ref_path.exists():
            print(f"Error: Reference not found: {args.reference}")
            sys.exit(1)

        if args.verbose:
            print(f"Comparing against reference: {ref_path}")

        cmd = [
            sys.executable, str(VALIDATE_PPTX_SCRIPT),
            str(input_path.resolve()),
            str(ref_path.resolve())
        ]

        success, output = run_command(cmd, "Validate against reference")

        if not success:
            print(f"Validation failed: {output}")
            sys.exit(1)

        print(output)
        sys.exit(0)

    # Standard quality check
    if args.verbose:
        print("Running quality checks...")

    cmd = [
        sys.executable, str(QUALITY_CHECK_SCRIPT),
        str(input_path.resolve())
    ]

    if args.template:
        template_path = Path(args.template)
        if not template_path.exists():
            print(f"Error: Template not found: {args.template}")
            sys.exit(1)
        cmd.extend(["--template", str(template_path.resolve())])

    if args.layout_plan:
        layout_path = Path(args.layout_plan)
        if not layout_path.exists():
            print(f"Error: Layout plan not found: {args.layout_plan}")
            sys.exit(1)
        cmd.extend(["--layout-plan", str(layout_path.resolve())])

    if args.json:
        cmd.append("--json")

    if args.output:
        cmd.extend(["--output", str(Path(args.output).resolve())])

    if args.strict:
        cmd.append("--strict")

    if args.no_parallel:
        cmd.append("--no-parallel")

    success, output = run_command(cmd, "Quality check")

    # Quality check may return non-zero for warnings in strict mode
    print(output)

    if not success and args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
