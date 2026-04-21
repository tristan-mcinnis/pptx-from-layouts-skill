#!/usr/bin/env python3
"""
profile.py: Profile PowerPoint templates for use with the generation pipeline.

Extracts layout information from templates and generates config files.

Usage:
    # Profile template and generate config
    python profile.py template.pptx --generate-config

    # Profile with custom name
    python profile.py template.pptx --name my-template --generate-config

    # Output to specific directory
    python profile.py template.pptx --name my-template --output-dir ./configs/

    # Full profile (verbose layout info)
    python profile.py template.pptx --full
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
PROFILE_SCRIPT = _SCRIPT_DIR / "profile_template.py"
GENERATE_CONFIG_SCRIPT = _SCRIPT_DIR / "generate_config.py"


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
        description="Profile PowerPoint templates for the generation pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python profile.py template.pptx --generate-config
    Profile template and generate config JSON

  python profile.py template.pptx --name my-template --output-dir ./configs/
    Profile with custom name and output directory

  python profile.py template.pptx --full
    Generate full profile with all layout details

  python profile.py --clear-cache
    Clear cached profile data
        """
    )

    parser.add_argument('template', nargs='?',
                        help='Path to PPTX template file')
    parser.add_argument('--name', '-n',
                        help='Template name (default: derived from filename)')
    parser.add_argument('--output-dir', '-o', default='.',
                        help='Output directory for generated files')
    parser.add_argument('--generate-config', action='store_true',
                        help='Generate template config JSON for pipeline use')
    parser.add_argument('--full', action='store_true',
                        help='Generate full profile with placeholder positions')
    parser.add_argument('--use-cache', action='store_true',
                        help='Use cached profile if available and valid')
    parser.add_argument('--clear-cache', action='store_true',
                        help='Clear all cached profiles')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed progress')

    args = parser.parse_args()

    # Handle cache clear (no template needed)
    if args.clear_cache:
        cmd = [sys.executable, str(PROFILE_SCRIPT), "--clear-cache"]
        success, output = run_command(cmd, "Clear cache")
        if success:
            print("Cache cleared successfully.")
        else:
            print(f"Error clearing cache: {output}")
        sys.exit(0 if success else 1)

    # Require template for other operations
    if not args.template:
        parser.error("template is required unless using --clear-cache")

    template_path = Path(args.template)
    if not template_path.exists():
        print(f"Error: Template not found: {args.template}")
        sys.exit(1)

    if args.verbose:
        print(f"Profiling template: {template_path}")

    # Build command
    cmd = [
        sys.executable, str(PROFILE_SCRIPT),
        str(template_path.resolve()),
        "--output-dir", str(Path(args.output_dir).resolve())
    ]

    if args.name:
        cmd.extend(["--name", args.name])
    if args.generate_config:
        cmd.append("--generate-config")
    if args.use_cache:
        cmd.append("--use-cache")
    if args.full:
        # Full profile includes all outputs
        pass  # Default behavior

    success, output = run_command(cmd, "Profile template")

    if not success:
        print(f"Error profiling template: {output}")
        sys.exit(1)

    # Show output
    print(output)

    if args.generate_config:
        name = args.name or template_path.stem
        config_path = Path(args.output_dir) / f"{name}-config.json"
        if config_path.exists():
            print(f"\nConfig generated: {config_path}")
            print("\nTo use this config:")
            print(f"  python generate.py outline.md -o output.pptx --config {config_path}")


if __name__ == "__main__":
    main()
