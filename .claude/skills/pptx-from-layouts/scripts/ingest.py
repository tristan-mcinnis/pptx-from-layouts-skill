#!/usr/bin/env python3
"""
ingest.py: Parse outline and match to template layouts.

Supports template-agnostic generation via --config flag, or uses Inner Chapter
defaults when no config is provided.

Usage:
    # Inner Chapter (default)
    python ingest.py outline.md --output slides.json

    # Any template with config
    python ingest.py outline.md --config my-template-config.json --output slides.json

Parser Hardening (v2):
- Line-attributed error messages
- State machine parsing
- Skip-and-log for malformed slides
- Pre-flight validation
- Fallback defaults for non-critical fields
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

# Set up paths for skill's local modules
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPT_DIR.parent
_LIB_DIR = _SKILL_DIR / "lib"
_SCHEMAS_DIR = _SKILL_DIR / "schemas"
_PROJECT_ROOT = _SKILL_DIR.parents[2]

# Add skill directories to path
for _path in [str(_SKILL_DIR), str(_LIB_DIR), str(_SCHEMAS_DIR), str(_SCRIPT_DIR)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Template config support (optional - falls back to IC_LAYOUTS if not available)
try:
    from schemas.template_config import TemplateConfig
    _HAS_TEMPLATE_CONFIG = True
except ImportError:
    _HAS_TEMPLATE_CONFIG = False
    TemplateConfig = None

# Graceful degradation framework (optional)
try:
    from graceful_degradation import (
        Severity as DegradationSeverity,
        DegradationIssue,
        DegradationResult,
    )
    _HAS_GRACEFUL_DEGRADATION = True
except ImportError:
    _HAS_GRACEFUL_DEGRADATION = False
    DegradationSeverity = None
    DegradationIssue = None
    DegradationResult = None

# Performance instrumentation (optional)
try:
    from performance import PerfContext, PerfTimer, identify_bottlenecks
    _HAS_PERFORMANCE = True
except ImportError:
    _HAS_PERFORMANCE = False
    PerfContext = None
    PerfTimer = None
    identify_bottlenecks = None

# Content recovery module for enhanced error tracking
try:
    from content_recovery import (
        ContentRecovery,
        ContentIssue,
        IssueCategory,
        IssueSeverity as RecoverySeverity,
        get_fix_suggestion,
        find_line_number,
        find_line_number_for_text,
        find_column_in_line,
    )
    _HAS_CONTENT_RECOVERY = True
except ImportError:
    _HAS_CONTENT_RECOVERY = False
    ContentRecovery = None
    ContentIssue = None
    IssueCategory = None
    RecoverySeverity = None
    get_fix_suggestion = None
    find_line_number = None
    find_line_number_for_text = None
    find_column_in_line = None


# =============================================================================
# ERROR HANDLING INFRASTRUCTURE
# =============================================================================

class ParseState(Enum):
    """State machine states for parsing."""
    INIT = auto()
    PREAMBLE = auto()       # Before first slide
    SLIDE_HEADER = auto()   # Processing "# Slide N:" header
    SLIDE_CONTENT = auto()  # Processing slide body
    TABLE = auto()          # Inside a table
    APPENDIX = auto()       # In appendix section (skip)


@dataclass
class ParseIssue:
    """Represents a parsing error or warning with line context."""
    line_num: int
    slide_num: int
    message: str
    line_content: str = ""
    is_fatal: bool = False

    def __str__(self):
        prefix = "ERROR" if self.is_fatal else "Warning"
        loc = f"Line {self.line_num}"
        if self.slide_num > 0:
            loc += f" (Slide {self.slide_num})"
        if self.line_content:
            content_preview = self.line_content[:50] + ('...' if len(self.line_content) > 50 else '')
            return f"{prefix} at {loc}: {self.message}\n  Content: {content_preview}"
        return f"{prefix} at {loc}: {self.message}"


@dataclass
class SkippedSlide:
    """Represents a slide that failed to parse with detailed recovery info."""
    original_slide_number: int
    raw_content: str
    error: str
    suggested_fix: str = ""
    fix_example: str = ""
    start_line: int = 0
    end_line: int = 0
    error_line: int = 0  # Specific line where error occurred
    error_column: int | None = None  # Column position if applicable
    category: str = "content"  # Error category for grouping

    def to_dict(self) -> dict:
        result = {
            'original_slide_number': self.original_slide_number,
            'raw_content': self.raw_content,
            'error': self.error,
            'suggested_fix': self.suggested_fix,
            'category': self.category,
        }
        if self.fix_example:
            result['fix_example'] = self.fix_example
        if self.start_line > 0:
            result['start_line'] = self.start_line
        if self.end_line > 0:
            result['end_line'] = self.end_line
        if self.error_line > 0:
            result['error_line'] = self.error_line
        if self.error_column is not None:
            result['error_column'] = self.error_column
        return result

    def format_location(self) -> str:
        """Format the error location."""
        if self.error_line > 0:
            loc = f"Line {self.error_line}"
            if self.error_column is not None:
                loc += f", Col {self.error_column}"
        elif self.start_line > 0:
            loc = f"Lines {self.start_line}-{self.end_line}"
        else:
            loc = f"Slide {self.original_slide_number}"
        return loc


def _categorize_error(error_msg: str, raw_content: str) -> str:
    """Determine error category from message and content."""
    error_lower = error_msg.lower()
    content_lower = raw_content.lower() if raw_content else ""

    if 'visual' in error_lower or 'visual type' in content_lower:
        return 'visual_type'
    if 'column' in error_lower:
        return 'column'
    if 'table' in error_lower or ('|' in content_lower and '---' in content_lower):
        return 'table'
    if 'timeline' in error_lower:
        return 'timeline'
    if 'deliverable' in error_lower:
        return 'deliverable'
    if 'marker' in error_lower or '{' in error_lower:
        return 'typography'
    if 'no slides' in error_lower or 'empty' in error_lower:
        return 'structure'
    if 'image' in error_lower:
        return 'image'
    return 'content'


def suggest_fix_for_error(error_msg: str, raw_content: str) -> str:
    """Generate a suggested fix based on the error message and content.

    Returns a string with the fix suggestion. For structured suggestions with
    examples, use suggest_fix_with_example() instead.
    """
    suggestion, _ = suggest_fix_with_example(error_msg, raw_content)
    return suggestion


def suggest_fix_with_example(error_msg: str, raw_content: str) -> tuple[str, str]:
    """Generate a suggested fix with example based on the error message and content.

    Returns:
        Tuple of (suggestion_text, example_text)
    """
    error_lower = error_msg.lower()
    content_lower = raw_content.lower() if raw_content else ""

    # Use content_recovery module if available for structured suggestions
    if _HAS_CONTENT_RECOVERY and get_fix_suggestion:
        category = _categorize_error(error_msg, raw_content)
        suggestion, example = get_fix_suggestion(category, error_msg, raw_content)
        if suggestion:
            return suggestion, example

    # Fallback to inline suggestions

    # Visual type issues
    if 'visual' in error_lower and 'expected' in error_lower:
        return (
            "Add visual type after title",
            "**Visual: bullets**\nor: **Visual: process-3-phase**"
        )
    if 'visual' in error_lower and 'unknown' in error_lower:
        return (
            "Use a valid visual type",
            "Valid types: bullets, column-2/3/4, process-2/3/4-phase, comparison-2/3, table, timeline, cards-3/4/5"
        )

    # Column issues
    if 'column' in error_lower:
        if 'mismatch' in error_lower or 'expected' in error_lower:
            return (
                "Column count must match visual type",
                "For process-3-phase, define exactly 3 [Column] sections"
            )
        if 'missing' in error_lower or 'empty' in error_lower:
            return (
                "Add column content",
                "[Column 1: Header]\n- Bullet point\n- Another bullet"
            )

    # Table issues
    if 'table' in error_lower:
        if 'header' in error_lower or 'separator' in error_lower:
            return (
                "Table needs header row with separator",
                "| Col 1 | Col 2 |\n|-------|-------|\n| data  | data  |"
            )
        return (
            "Check table format",
            "Use | for columns, --- for header separator, one row per line"
        )

    # Timeline issues
    if 'timeline' in error_lower:
        if 'date' in error_lower or 'format' in error_lower:
            return (
                "Timeline entries need date and description",
                "- **Q1 2026:** Launch product\n- **Q2 2026:** Expand team"
            )
        return (
            "Add timeline entries with bold dates",
            "- **Date:** Description"
        )

    # Deliverables issues
    if 'deliverable' in error_lower:
        return (
            "Add numbered deliverables",
            "1. **Report:** Detailed analysis document\n2. **Dashboard:** Interactive metrics view"
        )

    # Typography marker issues
    if 'marker' in error_lower or 'unclosed' in error_lower:
        if 'unclosed' in error_lower:
            return (
                "Close typography markers properly",
                "{bold}text{/bold}, {blue}text{/blue}"
            )
        return (
            "Check typography marker syntax",
            "{bold}Bold{/bold}, {color:#FF0000}Red{/color}"
        )

    # Slide structure issues
    if 'no slides' in error_lower:
        return (
            "Start each slide with a header",
            "# Slide 1: Introduction\nor use --- dividers between slides"
        )
    if 'empty' in error_lower and 'slide' in error_lower:
        return (
            "Slide has no content",
            "Add body text, bullets, columns, or other content after the title"
        )

    # Body content issues
    if 'body' in error_lower and ('missing' in error_lower or 'empty' in error_lower):
        return (
            "Add bullet points after the visual type",
            "- First point\n- Second point"
        )

    # Image issues
    if 'image' in error_lower:
        return (
            "Use image placeholder format",
            "[Image: description of image needed]"
        )

    # Generic with content hint
    if 'column' in content_lower and 'column' not in error_lower:
        return (
            "Check column formatting",
            "Use [Column N: Header] followed by bullets"
        )
    if '|' in content_lower and 'table' not in error_lower:
        return (
            "If this should be a table, add visual type and check row formatting",
            "**Visual: table**\n| Header | Header |\n|--------|--------|\n| data   | data   |"
        )

    return (
        "Review slide structure",
        "Each slide needs: title, optional visual type, and content (bullets, columns, or table)"
    )


@dataclass
class ParseContext:
    """Tracks parsing state, errors, and warnings."""
    state: ParseState = ParseState.INIT
    line_num: int = 0
    current_slide_num: int = 0
    issues: list = field(default_factory=list)

    # Tracking for validation
    slides_found: int = 0

    # Track skipped slides for recovery
    skipped_slides: list = field(default_factory=list)

    # Source lines for context extraction
    _source_lines: list = field(default_factory=list)

    # Content recovery integration (optional)
    content_recovery: "ContentRecovery | None" = None

    def set_source_content(self, content: str):
        """Set the source content for context extraction."""
        self._source_lines = content.split('\n')
        # Initialize content recovery if available
        if _HAS_CONTENT_RECOVERY and ContentRecovery:
            self.content_recovery = ContentRecovery(source_name="outline")
            self.content_recovery.set_source_lines(content)

    def _get_line_content(self, line_num: int) -> str:
        """Get the content of a specific line (1-indexed)."""
        if not self._source_lines or line_num < 1:
            return ""
        idx = line_num - 1
        if idx < len(self._source_lines):
            return self._source_lines[idx]
        return ""

    def add_skipped_slide(
        self,
        slide_num: int,
        raw_content: str,
        error: str,
        start_line: int = 0,
        end_line: int = 0,
        error_line: int = 0,
        error_column: int | None = None
    ):
        """Record a slide that was skipped due to parsing errors.

        Args:
            slide_num: The slide number that was skipped
            raw_content: The raw content that failed to parse
            error: Error message describing what went wrong
            start_line: Line number where slide content starts (1-indexed)
            end_line: Line number where slide content ends (1-indexed)
            error_line: Specific line where error occurred (1-indexed)
            error_column: Column position of error (1-indexed, optional)
        """
        suggested_fix, fix_example = suggest_fix_with_example(error, raw_content)
        category = _categorize_error(error, raw_content)

        self.skipped_slides.append(SkippedSlide(
            original_slide_number=slide_num,
            raw_content=raw_content[:500] if raw_content else "",
            error=error,
            suggested_fix=suggested_fix,
            fix_example=fix_example,
            start_line=start_line or self.line_num,
            end_line=end_line or self.line_num,
            error_line=error_line or self.line_num,
            error_column=error_column,
            category=category,
        ))

        # Also track in content recovery if available
        if self.content_recovery:
            self.content_recovery.add_issue(
                line_num=error_line or self.line_num,
                column=error_column,
                category=category,
                message=error,
                severity=RecoverySeverity.ERROR if RecoverySeverity else "error",
                slide_num=slide_num,
                suggested_fix=suggested_fix,
                fix_example=fix_example,
                raw_content=raw_content[:500] if raw_content else "",
            )

    def add_error(self, message: str, line_content: str = "", fatal: bool = False):
        """Record a parsing error."""
        self.issues.append(ParseIssue(
            line_num=self.line_num,
            slide_num=self.current_slide_num,
            message=message,
            line_content=line_content,
            is_fatal=fatal
        ))

    def add_warning(self, message: str, line_content: str = ""):
        """Record a non-fatal warning."""
        self.issues.append(ParseIssue(
            line_num=self.line_num,
            slide_num=self.current_slide_num,
            message=message,
            line_content=line_content,
            is_fatal=False
        ))

    @property
    def errors(self) -> list:
        """Return only fatal errors."""
        return [i for i in self.issues if i.is_fatal]

    @property
    def warnings(self) -> list:
        """Return only warnings."""
        return [i for i in self.issues if not i.is_fatal]

    @property
    def has_fatal_errors(self) -> bool:
        return any(i.is_fatal for i in self.issues)

    def report(self) -> str:
        """Generate a formatted report of all issues."""
        if not self.issues and not self.skipped_slides:
            return "Parsing completed with no issues."

        lines = []
        errors = self.errors
        warnings = self.warnings

        if errors:
            lines.append(f"=== {len(errors)} Error(s) ===")
            for e in errors:
                lines.append(str(e))

        if warnings:
            lines.append(f"=== {len(warnings)} Warning(s) ===")
            for w in warnings:
                lines.append(str(w))

        if self.skipped_slides:
            lines.append(f"\n=== {len(self.skipped_slides)} Skipped Slide(s) ===")
            for ss in self.skipped_slides:
                lines.append(f"Slide {ss.original_slide_number} at {ss.format_location()}:")
                lines.append(f"  Error: {ss.error}")
                if ss.suggested_fix:
                    lines.append(f"  Fix: {ss.suggested_fix}")
                if ss.fix_example:
                    lines.append("  Example:")
                    for ex_line in ss.fix_example.split('\n'):
                        lines.append(f"    {ex_line}")

        return "\n".join(lines)

    def recovery_report(self, verbose: bool = True) -> str:
        """Generate a detailed recovery report with context.

        Args:
            verbose: Include source context lines in output

        Returns:
            Formatted recovery report string
        """
        if not self.skipped_slides:
            return "No slides were skipped."

        lines = [
            "=" * 60,
            "RECOVERY REPORT",
            "=" * 60,
            f"Total skipped slides: {len(self.skipped_slides)}",
            ""
        ]

        for i, ss in enumerate(self.skipped_slides, 1):
            lines.append(f"[{i}] Slide {ss.original_slide_number}")
            lines.append(f"    Location: {ss.format_location()}")
            lines.append(f"    Category: {ss.category}")
            lines.append(f"    Error: {ss.error}")

            # Show context if verbose and source lines available
            if verbose and self._source_lines and ss.error_line > 0:
                lines.append("    Context:")
                start_idx = max(0, ss.error_line - 3)
                end_idx = min(len(self._source_lines), ss.error_line + 2)
                for idx in range(start_idx, end_idx):
                    line_num = idx + 1
                    line_content = self._source_lines[idx]
                    marker = ">>>" if line_num == ss.error_line else "   "
                    lines.append(f"    {marker} {line_num:>4} | {line_content}")

            lines.append(f"    Suggested fix: {ss.suggested_fix}")
            if ss.fix_example:
                lines.append("    Example:")
                for ex_line in ss.fix_example.split('\n'):
                    lines.append(f"        {ex_line}")
            lines.append("")

        return "\n".join(lines)

    def to_degradation_result(self) -> "DegradationResult | None":
        """Convert ParseContext to a DegradationResult for unified error handling.

        Returns None if graceful_degradation module is not available.
        """
        if not _HAS_GRACEFUL_DEGRADATION:
            return None

        issues = []
        for pi in self.issues:
            # Map ParseIssue.is_fatal to Severity
            severity = DegradationSeverity.ERROR if pi.is_fatal else DegradationSeverity.WARN
            issues.append(DegradationIssue(
                severity=severity,
                category="parse",
                message=pi.message,
                location=f"line {pi.line_num}" if pi.line_num > 0 else None,
                slide_number=pi.slide_num if pi.slide_num > 0 else None,
                context={"line_content": pi.line_content} if pi.line_content else None,
            ))

        # Add skipped slides as ERROR-level issues
        for ss in self.skipped_slides:
            location = ss.format_location() if hasattr(ss, 'format_location') else f"slide {ss.original_slide_number}"
            context = {
                "category": ss.category,
                "raw_content": ss.raw_content[:200] if ss.raw_content else None,
            }
            if ss.fix_example:
                context["fix_example"] = ss.fix_example
            if ss.error_line > 0:
                context["error_line"] = ss.error_line
            if ss.error_column is not None:
                context["error_column"] = ss.error_column

            issues.append(DegradationIssue(
                severity=DegradationSeverity.ERROR,
                category=ss.category if hasattr(ss, 'category') else "parse",
                message=f"Slide {ss.original_slide_number} skipped: {ss.error}",
                location=location,
                slide_number=ss.original_slide_number,
                suggestion=ss.suggested_fix,
                fallback_action="Slide skipped from output",
                context=context,
            ))

        return DegradationResult(
            component="ingest",
            issues=issues,
            partial_output=len(self.skipped_slides) > 0,
            aborted=self.has_fatal_errors,
        )


# =============================================================================
# FUZZY VISUAL TYPE MATCHING
# =============================================================================
# Provides typo detection and "Did you mean?" suggestions for visual types.

# Canonical list of valid visual types (matches visual_type_map keys)
VALID_VISUAL_TYPES = frozenset([
    # Core process types
    'process-5-phase',
    'process-4-phase',
    'process-3-phase',
    'process-2-phase',
    # Comparison types
    'comparison-5',
    'comparison-4',
    'comparison-3',
    'comparison-2',
    'comparison-tables',
    # Card types
    'cards-5',
    'cards-4',
    'cards-3',
    'cards-2',
    # Other visual types
    'data-contrast',
    'quote-hero',
    'hero-statement',
    'story-card',
    'table-with-image',
    'timeline-horizontal',
    'table',
    'bullets',
    'framework',
    # Grid layouts
    'grid-3x2-image-top-3-body',
    'grid-3x2-image-top-6-body-a',
    'grid-2x2-image-top-2-body-a',
    'grid-2x2-image-top-2-body-b',
    'content-image-top-4-body',
    # Layout name variants
    'content-image-right-a',
    'content-image-right-b',
    '1_content-image-right-a',
    'title-centered',
    'column-5-centered',
    'column-4-centered',
    'column-3-centered-a',
    'column-2-centered',
    'contact-black',
    'contact-white',
])

# Visual types that expect column structure - used to convert tables to columns
COLUMN_BASED_VISUAL_TYPES = frozenset([
    'cards-2', 'cards-3', 'cards-4', 'cards-5',
    'process-2-phase', 'process-3-phase', 'process-4-phase', 'process-5-phase',
    'comparison-2', 'comparison-3', 'comparison-4', 'comparison-5',
    'data-contrast',  # 2-column layout
    'framework',  # Generic framework infers column count
])


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein (edit) distance between two strings.

    Uses dynamic programming approach with O(min(m,n)) space complexity.

    Args:
        s1: First string
        s2: Second string

    Returns:
        Number of single-character edits (insertions, deletions, substitutions)
        needed to transform s1 into s2.
    """
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    # Use two rows instead of full matrix for space efficiency
    previous_row = list(range(len(s2) + 1))
    current_row = [0] * (len(s2) + 1)

    for i, c1 in enumerate(s1):
        current_row[0] = i + 1
        for j, c2 in enumerate(s2):
            # Cost is 0 if characters match, 1 otherwise
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (0 if c1 == c2 else 1)
            current_row[j + 1] = min(insertions, deletions, substitutions)
        previous_row, current_row = current_row, previous_row

    return previous_row[len(s2)]


def _normalized_similarity(s1: str, s2: str) -> float:
    """Calculate normalized similarity between two strings.

    Returns a value between 0.0 (completely different) and 1.0 (identical).

    Args:
        s1: First string
        s2: Second string

    Returns:
        Similarity score from 0.0 to 1.0
    """
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    max_len = max(len(s1), len(s2))
    distance = _levenshtein_distance(s1, s2)
    return 1.0 - (distance / max_len)


def find_similar_visual_type(
    visual_type: str,
    threshold: float = 0.6,
    max_suggestions: int = 3
) -> list[tuple[str, float]]:
    """Find similar visual types for a potentially misspelled input.

    Args:
        visual_type: The visual type string to match (will be lowercased)
        threshold: Minimum similarity score (0.0-1.0) to include a suggestion
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of (visual_type, similarity_score) tuples, sorted by score descending.
        Empty list if no matches above threshold or if input is already valid.
    """
    visual_type_lower = visual_type.lower().strip()

    # If it's already a valid type, return empty (no suggestion needed)
    if visual_type_lower in VALID_VISUAL_TYPES:
        return []

    matches = []
    for valid_type in VALID_VISUAL_TYPES:
        similarity = _normalized_similarity(visual_type_lower, valid_type)
        if similarity >= threshold:
            matches.append((valid_type, similarity))

    # Sort by similarity (descending) and return top matches
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[:max_suggestions]


def validate_visual_type(visual_type: str) -> tuple[bool, str | None, list[str]]:
    """Validate a visual type and provide suggestions for typos.

    Args:
        visual_type: The visual type string to validate

    Returns:
        Tuple of:
        - is_valid: True if visual type is recognized
        - best_match: The best matching valid type if invalid, None otherwise
        - suggestions: List of suggested alternatives (empty if valid)
    """
    if not visual_type:
        return True, None, []

    visual_type_lower = visual_type.lower().strip()

    # Check exact match
    if visual_type_lower in VALID_VISUAL_TYPES:
        return True, None, []

    # Check substring match (for backwards compatibility with visual_type_map logic)
    for valid_type in VALID_VISUAL_TYPES:
        if valid_type in visual_type_lower:
            return True, None, []

    # Find similar types for suggestions
    similar = find_similar_visual_type(visual_type_lower)
    if similar:
        suggestions = [s[0] for s in similar]
        return False, suggestions[0], suggestions

    return False, None, []


def format_visual_type_suggestion(visual_type: str, suggestions: list[str]) -> str:
    """Format a "Did you mean?" suggestion message.

    Args:
        visual_type: The original (invalid) visual type
        suggestions: List of suggested alternatives

    Returns:
        Formatted suggestion message
    """
    if not suggestions:
        return f"Unknown visual type '{visual_type}'"

    if len(suggestions) == 1:
        return f"Unknown visual type '{visual_type}'. Did you mean '{suggestions[0]}'?"

    # Format multiple suggestions
    quoted = [f"'{s}'" for s in suggestions]
    return f"Unknown visual type '{visual_type}'. Did you mean {', '.join(quoted[:-1])} or {quoted[-1]}?"


# =============================================================================
# INNER CHAPTER LAYOUT CONSTANTS (Default for backwards compatibility)
# =============================================================================
# These are used when no --config is provided. For template-agnostic generation,
# use --config to specify a template config JSON file.

IC_LAYOUTS = {
    'master_base': {'index': 58, 'name': 'master-base', 'use': 'Branding slide (always Slide 1)'},
    'title_cover': {'index': 0, 'name': 'title-cover', 'use': 'Cover page with client, date, logos'},
    'title_centered': {'index': 2, 'name': 'title-centered', 'use': 'Section dividers, closing statements'},
    'content_image_right': {'index': 3, 'name': 'content-image-right-a', 'use': 'Bullets with image placeholder'},
    # 1_content-image-right-a (index 58 in reference) doesn't exist in current template
    # Falls back to content-image-right-a with same content handling
    'content_image_right_1': {'index': 3, 'name': 'content-image-right-a', 'use': 'Bullets without image placeholder (fallback)'},
    'content_image_right_b': {'index': 10, 'name': 'content-image-right-b', 'use': 'Full-bleed image with text overlay'},
    'content_image_right_text_left': {'index': 8, 'name': 'content-image-right-text-left', 'use': 'Story card with image right, text left'},
    'column_5': {'index': 27, 'name': 'column-5-centered', 'use': '5-column cards/comparisons'},
    'column_4': {'index': 31, 'name': 'column-4-centered', 'use': '4-column framework/flow diagrams'},
    'column_3': {'index': 35, 'name': 'column-3-centered-a', 'use': '3-column comparisons'},
    'column_2': {'index': 6, 'name': 'column-2-centered', 'use': '2-column comparisons'},
    'contact_black': {'index': 52, 'name': 'contact-black', 'use': 'Contact information (dark)'},
    'contact_white': {'index': 51, 'name': 'contact-white', 'use': 'Contact information (light)'},
    # Grid layouts with image placeholders
    'grid_3x2_3body': {'index': 36, 'name': 'grid-3x2-image-top-3-body', 'use': '3 columns with images top, 3 body areas'},
    'grid_3x2_6body': {'index': 37, 'name': 'grid-3x2-image-top-6-body-a', 'use': '3 columns with images top, 6 body areas (header+body)'},
    'grid_2x2_2body': {'index': 42, 'name': 'grid-2x2-image-top-2-body-a', 'use': '2 columns with images top, 2 body areas'},
    'grid_2x2_2body_b': {'index': 44, 'name': 'grid-2x2-image-top-2-body-b', 'use': '2 columns with images top, 2 body areas (variant B)'},
    'content_image_top_4body': {'index': 32, 'name': 'content-image-top-4-body', 'use': '4 columns with images top'},
}


# Default content type routing (maps content_type to capability key)
IC_CONTENT_TYPE_ROUTING = {
    'title_slide': 'title_cover',
    'section_divider': 'title_centered',
    'closing': 'title_centered',
    'thank_you': 'title_centered',
    'contact': 'contact_black',
    'framework_5col': 'column_5',
    'framework_4col': 'column_4',
    'framework_3col': 'column_3',
    'framework_2col': 'column_2',
    'framework_cards': 'column_4',
    'story_card': 'content_image_right_text_left',
    'comparison_tables': 'title_centered',
    'table_with_image': 'content_image_right',
    'timeline': 'content_image_right',
    'pricing': 'title_centered',
    'table': 'title_centered',
    'deliverables': 'content_image_right',
    'content': 'content_image_right',
    'content_no_image': 'content_image_right_1',
    'quote': 'title_centered',
    'grid_3x2_3body': 'grid_3x2_3body',
    'grid_3x2_6body': 'grid_3x2_6body',
    'grid_2x2_2body': 'grid_2x2_2body',
    'grid_2x2_2body_b': 'grid_2x2_2body_b',
    'content_image_top_4body': 'content_image_top_4body',
}


class LayoutConfigAdapter:
    """Adapter that provides unified interface for layout selection.

    Works with either a TemplateConfig object or falls back to IC_LAYOUTS.
    """

    def __init__(self, config: "TemplateConfig | None" = None):
        self.config = config
        self._use_config = config is not None and _HAS_TEMPLATE_CONFIG

    @property
    def template_name(self) -> str:
        if self._use_config:
            return self.config.template_name
        return "inner-chapter"

    @property
    def requires_branding_slide(self) -> bool:
        if self._use_config:
            return self.config.requires_branding_slide
        return True  # IC always has branding slide

    @property
    def branding_layout_index(self) -> int:
        if self._use_config and self.config.branding_layout_index is not None:
            return self.config.branding_layout_index
        return IC_LAYOUTS['master_base']['index']

    @property
    def branding_layout_name(self) -> str:
        if self._use_config:
            master = self.config.layout_mappings.get('master_base')
            if master:
                return master.layout_name
        return IC_LAYOUTS['master_base']['name']

    def get_layout(self, key: str) -> dict:
        """Get layout by capability key (e.g., 'column_3', 'title_cover')."""
        if self._use_config:
            layout = self.config.get_layout_for_capability(key)
            return {
                'index': layout['index'],
                'name': layout['name'],
                'use': layout.get('use', ''),
            }
        # Fallback to IC_LAYOUTS
        if key in IC_LAYOUTS:
            return IC_LAYOUTS[key]
        # Try remapped keys
        key_remap = {
            'content_with_image': 'content_image_right',
            'content_centered': 'content_image_right',
            'contact': 'contact_black',
        }
        remapped = key_remap.get(key)
        if remapped and remapped in IC_LAYOUTS:
            return IC_LAYOUTS[remapped]
        # Return content_image_right as fallback
        return IC_LAYOUTS.get('content_image_right', {'index': 3, 'name': 'content-image-right-a', 'use': 'fallback'})

    def get_layout_by_name(self, name: str) -> dict | None:
        """Resolve a layout by its real layout name (or capability key).

        Used by [HINT: ...] / **Layout: ...** to target a template's actual
        slide-master layout. Searches the ACTIVE config first (so custom,
        profiled templates resolve their own layout names), then the Inner
        Chapter defaults. Matches exactly first, then by substring.

        Returns a {'index','name','use'} dict, or None if nothing matches.
        """
        def _norm(s: str) -> str:
            return s.lower().strip().replace(' ', '_').replace('-', '_')

        target = _norm(name)

        # 1. Active custom config: search its layout_mappings by capability key
        #    and by the real layout_name.
        if self._use_config and self.config is not None:
            candidates = []
            for cap_key, cap in self.config.layout_mappings.items():
                candidates.append((cap_key, cap.layout_name, cap.layout_index))
            # Exact match on layout_name or capability key
            for cap_key, lname, lidx in candidates:
                if _norm(lname) == target or _norm(cap_key) == target:
                    return {'index': lidx, 'name': lname, 'use': ''}
            # Substring match
            for cap_key, lname, lidx in candidates:
                if target in _norm(lname) or target in _norm(cap_key):
                    return {'index': lidx, 'name': lname, 'use': ''}
            # A custom config is authoritative for its own template. Do NOT fall
            # back to Inner Chapter layout indices — they may not exist in, or may
            # mis-map onto, the user's template. Let the caller auto-detect.
            return None

        # 2. No custom config → Inner Chapter defaults: exact then substring.
        for key, layout in IC_LAYOUTS.items():
            if _norm(layout['name']) == target or _norm(key) == target:
                return dict(layout)
        for key, layout in IC_LAYOUTS.items():
            if target in _norm(layout['name']) or target in _norm(key):
                return dict(layout)

        return None

    def get_layout_for_content_type(self, content_type: str) -> dict:
        """Get layout for a content type, going through routing."""
        if self._use_config:
            layout = self.config.get_layout_for_content_type(content_type)
            return {
                'index': layout['index'],
                'name': layout['name'],
                'use': layout.get('use', ''),
            }
        # Use IC routing
        capability = IC_CONTENT_TYPE_ROUTING.get(content_type)
        if capability:
            return self.get_layout(capability)
        return self.get_layout('content_image_right')


# Global config adapter (set in main() when --config is used)
_layout_config: LayoutConfigAdapter | None = None


def get_layout_config() -> LayoutConfigAdapter:
    """Get the current layout config adapter."""
    global _layout_config
    if _layout_config is None:
        _layout_config = LayoutConfigAdapter()  # IC defaults
    return _layout_config


def set_layout_config(config: "TemplateConfig | None"):
    """Set the layout config adapter."""
    global _layout_config
    _layout_config = LayoutConfigAdapter(config)


# =============================================================================
# OUTLINE PARSING
# =============================================================================

def validate_preflight(lines: list[str], ctx: ParseContext) -> bool:
    """Pre-flight validation: check for slides before parsing.

    Returns True if validation passes, False if fatal errors found.
    """
    has_slides = False
    has_slide_header = False

    for line in lines:
        line_stripped = line.strip()
        # Check for "# Slide N" or "## SLIDE N" header format
        if re.match(r'^#{1,2}\s*SLIDE\s+\d+', line_stripped, re.IGNORECASE):
            has_slide_header = True
            has_slides = True
            break
        # Check for --- divider format (alternative slide separation)
        if line_stripped == '---':
            has_slides = True

    if not has_slides:
        ctx.line_num = 0
        ctx.add_error(
            'No slides found. Expected "# Slide 1: Title" headers or "---" dividers.',
            fatal=True
        )
        return False

    if not has_slide_header:
        ctx.line_num = 0
        ctx.add_warning(
            'No "# Slide N:" headers found. Using "---" dividers to separate slides. '
            'Consider using explicit slide headers for better structure.'
        )

    return True


def create_title_slide_from_preamble(preamble_lines: list[str], ctx: ParseContext) -> dict | None:
    """Create a title_slide from accumulated preamble content.

    Preamble structure:
    - # Title → slide title
    - ## Subtitle → subtitle
    - Plain text lines → metadata (client name, date)

    Returns a slide dict with content_type 'title_slide', or None if preamble is empty.
    """
    if not preamble_lines:
        return None

    title = None
    subtitle = None
    metadata = []

    for line in preamble_lines:
        if line.startswith('# ') and not line.startswith('## '):
            title = line[2:].strip()
        elif line.startswith('## '):
            subtitle = line[3:].strip()
        elif not line.startswith('#'):
            metadata.append(line)

    if not title and not subtitle:
        return None

    slide = {
        'outline_number': 2,  # Title slide is position 2 (after branding)
        'title': title or subtitle or 'Untitled',
        'subtitle': subtitle if title else None,
        'headline': None,
        'body': metadata if metadata else [],
        'metadata': metadata,
        'has_table': False,
        'table': None,
        'has_image': False,
        'image_count': 0,
        'has_quote': False,
        'quote': None,
        'callout': None,
        'raw_content': '\n'.join(preamble_lines),
        'content_type': 'title_slide',
    }

    return slide


def parse_outline(content: str) -> tuple[list[dict], ParseContext]:
    """Parse markdown outline into slide array using state machine.

    Returns:
        Tuple of (slides list, ParseContext) - check ctx.has_fatal_errors
    """
    ctx = ParseContext()
    slides = []
    lines = content.split('\n')

    # Store source content for context extraction in error reporting
    ctx.set_source_content(content)

    # Pre-flight validation
    validate_preflight(lines, ctx)

    # Lookahead: Check if file uses explicit "## SLIDE N:" headers
    # This helps us know whether to treat --- as slide separators
    has_explicit_headers = any(
        re.match(r'^#{1,2}\s*SLIDE\s+\d+', line.strip(), re.IGNORECASE)
        for line in lines
    )

    # State machine parsing
    ctx.state = ParseState.PREAMBLE
    current_slide_lines = []
    current_slide_start_line = 0
    slide_number = 0
    in_appendix = False
    using_explicit_headers = has_explicit_headers  # Pre-set if we detected explicit headers
    preamble_lines = []  # Accumulate preamble content for title slide creation

    for line_num, line in enumerate(lines, 1):
        ctx.line_num = line_num
        line_stripped = line.strip()

        # Check for appendix - skip everything after
        if re.match(r'^---\s*$', line_stripped):
            # Look ahead for appendix
            remaining = '\n'.join(lines[line_num:])
            if re.match(r'^\s*##?\s*Appendix', remaining, re.IGNORECASE):
                in_appendix = True
                ctx.state = ParseState.APPENDIX

        if in_appendix:
            continue

        # Check for slide header: "# Slide N:", "# Slide N", or "## SLIDE N: Title"
        # Supports both single and double hash prefix
        slide_header_match = re.match(r'^#{1,2}\s*SLIDE\s+(\d+)[:\s]*(.*)', line_stripped, re.IGNORECASE)
        if slide_header_match:
            # Save previous slide if any
            # But skip if we're transitioning to explicit headers and previous was auto-generated preamble
            was_using_explicit = using_explicit_headers
            if current_slide_lines and was_using_explicit:
                # Only save if we were already using explicit headers
                slide = parse_slide_content_safe(
                    '\n'.join(current_slide_lines),
                    slide_number,
                    current_slide_start_line,
                    ctx
                )
                if slide:
                    slides.append(slide)
                    ctx.slides_found += 1

            # Create title slide from accumulated preamble (if any)
            # BUT skip if the upcoming explicit slide is also a title slide
            # (to avoid creating duplicate title slides)
            slide_label = slide_header_match.group(2).strip() if slide_header_match.group(2) else ''
            upcoming_is_title = slide_label and slide_label.lower() == 'title'

            if preamble_lines and not upcoming_is_title:
                title_slide = create_title_slide_from_preamble(preamble_lines, ctx)
                if title_slide:
                    slides.append(title_slide)
                    ctx.slides_found += 1
            preamble_lines = []  # Reset after processing (whether used or skipped)

            # Start new slide
            using_explicit_headers = True  # Mark that we're using explicit headers
            slide_number = int(slide_header_match.group(1))
            ctx.current_slide_num = slide_number
            ctx.state = ParseState.SLIDE_HEADER
            current_slide_lines = [line]
            current_slide_start_line = line_num
            continue

        # Check for --- divider (alternative slide separator)
        if line_stripped == '---':
            if current_slide_lines:
                slide = parse_slide_content_safe(
                    '\n'.join(current_slide_lines),
                    slide_number,
                    current_slide_start_line,
                    ctx
                )
                if slide:
                    slides.append(slide)
                    ctx.slides_found += 1

            # When using explicit "# Slide N:" headers, --- just finalizes the slide
            # and waits for the next header. Don't auto-create new slides.
            if using_explicit_headers:
                current_slide_lines = []
                ctx.state = ParseState.PREAMBLE  # Wait for next header
                continue

            # Legacy mode: auto-number slides separated by ---
            slide_number += 1
            ctx.current_slide_num = slide_number
            ctx.state = ParseState.SLIDE_CONTENT
            current_slide_lines = []
            current_slide_start_line = line_num + 1
            continue

        # Accumulate preamble content (before first slide) for title slide
        if ctx.state == ParseState.PREAMBLE:
            if line_stripped:
                preamble_lines.append(line_stripped)
            continue

        # Accumulate slide content
        if ctx.state in (ParseState.SLIDE_HEADER, ParseState.SLIDE_CONTENT, ParseState.TABLE):
            current_slide_lines.append(line)
            # Track table state for multiline tables
            if '|' in line and re.search(r'\|.*\|', line):
                ctx.state = ParseState.TABLE
            elif ctx.state == ParseState.TABLE and '|' not in line:
                ctx.state = ParseState.SLIDE_CONTENT

    # Don't forget the last slide
    if current_slide_lines:
        slide = parse_slide_content_safe(
            '\n'.join(current_slide_lines),
            slide_number,
            current_slide_start_line,
            ctx
        )
        if slide:
            slides.append(slide)
            ctx.slides_found += 1

    # Final validation
    if not slides:
        ctx.line_num = 0
        ctx.add_error("No valid slides could be parsed from input", fatal=True)

    return slides, ctx


def parse_slide_content_safe(content: str, slide_number: int, start_line: int,
                              ctx: ParseContext) -> dict | None:
    """Wrapper around parse_slide_content with error handling.

    Uses skip-and-log strategy: returns None on failure, logs error.
    Also captures the raw content for recovery purposes with line numbers.
    """
    try:
        slide = parse_slide_content(content, slide_number, ctx)
        return slide
    except Exception as e:
        ctx.line_num = start_line
        ctx.current_slide_num = slide_number
        error_msg = f"Failed to parse slide: {e}"

        # Calculate end line based on content line count
        content_lines = content.split('\n') if content else []
        end_line = start_line + len(content_lines) - 1

        ctx.add_error(
            error_msg,
            line_content=content[:100] if content else "",
            fatal=False  # Non-fatal: skip this slide, continue with others
        )
        # Capture for recovery with line number info
        ctx.add_skipped_slide(
            slide_num=slide_number,
            raw_content=content,
            error=error_msg,
            start_line=start_line,
            end_line=end_line,
            error_line=start_line  # Default to start; more specific in future
        )
        return None


def parse_slide_content(content: str, slide_number: int, ctx: ParseContext = None) -> dict:
    """Parse individual slide content with error tracking.

    Args:
        content: Raw slide content string
        slide_number: Slide number for reference
        ctx: ParseContext for warnings (optional for backward compat)

    Returns:
        Parsed slide dictionary
    """
    slide = {
        'outline_number': slide_number,  # Original number from outline
        'title': None,
        'subtitle': None,
        'headline': None,
        'body': [],
        'metadata': [],
        'has_table': False,
        'table': None,
        'has_image': False,
        'image_count': 0,
        'has_quote': False,
        'quote': None,
        'callout': None,
        'raw_content': content
    }

    # Helper to log warnings if context available
    def warn(msg: str, line_content: str = ""):
        if ctx:
            ctx.add_warning(msg, line_content)

    # Extract slide label from header (e.g., "# Slide 1: Title" or "## SLIDE 1: Title" -> "Title")
    slide_header = re.search(r'^#{1,2}\s*SLIDE\s+\d+[:\s]*(.*)$', content, re.MULTILINE | re.IGNORECASE)
    slide_label = slide_header.group(1).strip() if slide_header else None

    # Extract all headings
    # H1: # Title (used for main title on title slides)
    # H2: ## Title (used for slide labels or section titles)
    # H3: ### Subtitle
    #
    # Note: We need to exclude slide headers like "# Slide 1: Title" from H1 matches
    h1_matches = re.findall(r'^#\s+([^#].+)$', content, re.MULTILINE)
    # Filter out slide headers from H1 matches
    h1_match = None
    for match in h1_matches:
        if not re.match(r'^SLIDE\s+\d+', match.strip(), re.IGNORECASE):
            h1_match = match.strip()
            break

    h2_match = re.search(r'^##\s+(.+)$', content, re.MULTILINE)
    h3_match = re.search(r'^###\s+(.+)$', content, re.MULTILINE)

    # Determine if this is a title slide
    has_bullets = bool(re.search(r'^[\-\*]\s+', content, re.MULTILINE))
    # Proper markdown tables require a separator row: |---|---|
    # This distinguishes tables from text that happens to contain pipe characters
    has_table = bool(re.search(r'^\s*\|[\s\-:]+\|[\s\-:]*\|?', content, re.MULTILINE))
    is_title_slide = slide_label and slide_label.lower() == 'title'

    if is_title_slide:
        # Title slide: prefer H1 for main title, fall back to H2
        # Common patterns:
        #   ## SLIDE 1: Title
        #   # Main Title Here      <- H1 is the actual title
        #   ### Subtitle
        # OR:
        #   ## SLIDE 1: Title
        #   ## Main Title Here     <- H2 is the actual title (legacy)
        if h1_match:
            slide['title'] = h1_match  # Already a string
        elif h2_match:
            # Check if H2 is just the slide header (e.g., "SLIDE 1: Title")
            h2_text = h2_match.group(1).strip()
            if not re.match(r'^SLIDE\s+\d+', h2_text, re.IGNORECASE):
                slide['title'] = h2_text
            else:
                warn("Title slide missing # or ## heading for main title")
                slide['title'] = "Untitled"  # Fallback default
        else:
            warn("Title slide missing # or ## heading for main title")
            slide['title'] = "Untitled"  # Fallback default

        if h3_match:
            slide['subtitle'] = h3_match.group(1).strip()

        # Extract metadata lines (plain text, not headings or bullets)
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('---'):
                if not line.startswith('**') and not line.startswith('-') and not line.startswith('*'):
                    # Skip table lines and bracketed markers ([HINT:], [IMG:], ...)
                    if '|' not in line and not line.startswith('['):
                        slide['metadata'].append(line)
    else:
        # Regular slide: use slide label, then H1, then H2 as title
        # Priority:
        #   1. Slide label from "# Slide N: Label" header
        #   2. H1 heading (# Title)
        #   3. H2 heading (## Title) - but not if it's the slide header
        #   4. First non-empty line as fallback
        if slide_label and slide_label.lower() not in ['title', 'slide', '']:
            slide['title'] = slide_label
        elif h1_match:
            slide['title'] = h1_match  # Already a string
        elif h2_match:
            h2_text = h2_match.group(1).strip()
            # Skip if H2 is just the slide header
            if not re.match(r'^SLIDE\s+\d+', h2_text, re.IGNORECASE):
                slide['title'] = h2_text
            else:
                slide['title'] = f"Slide {slide_number}"  # Fallback
        else:
            # Fallback: use first non-empty line or generate default
            first_line = None
            for line in content.split('\n'):
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('['):
                    first_line = line[:50]  # Truncate long lines
                    break
            if first_line:
                warn(f"No title found, using first line as fallback", first_line)
                slide['title'] = clean_markdown(first_line)
            else:
                warn("Slide has no title")
                slide['title'] = f"Slide {slide_number}"  # Fallback default

    # Extract headline (first bold text that's not a visual marker, typically ≤25 words)
    # Find all standalone bold lines
    bold_lines = re.findall(r'^\*\*([^*]+)\*\*\s*$', content, re.MULTILINE)
    for text in bold_lines:
        text = text.strip()
        # Skip visual markers
        if text.lower().startswith('visual:'):
            continue
        # Skip common non-headline patterns
        if text.lower().startswith(('note:', 'image:', 'contact:')):
            continue
        # This is the headline - check for typography markers
        parsed = parse_typography_markers(text)
        if len(parsed['plain'].split()) <= 25:
            slide['headline'] = parsed['plain']
            slide['headline_bold'] = True  # Headline from **...** line is bold
            if parsed['has_typography']:
                slide['headline_styled'] = parsed['runs']
        else:
            warn(f"Headline exceeds 25 words ({len(parsed['plain'].split())} words), may overflow")
            slide['headline'] = parsed['plain']  # Include anyway with warning
            slide['headline_bold'] = True  # Headline from **...** line is bold
            if parsed['has_typography']:
                slide['headline_styled'] = parsed['runs']
        break  # Use first non-marker bold line as headline

    # Extract bullets with recursive hierarchy support
    # Parse all bullets (top-level and nested) to build hierarchical structure
    # Supports 3+ indent levels with recursive children
    # Top-level: ^[\-\*]\s+ (no leading whitespace)
    # Nested: ^\s+[\-\*]\s+ (has leading whitespace, depth determined by indent)
    hierarchical_bullets = []
    # Stack to track parent nodes at each indent level: [(indent_level, node), ...]
    parent_stack = []

    def get_indent_level(whitespace: str) -> int:
        """Convert whitespace to indent level (2 or 4 spaces = 1 level, tab = 1 level)."""
        if not whitespace:
            return 0
        # Count tabs as 1 level each, spaces as 1 level per 2-4 spaces
        tabs = whitespace.count('\t')
        spaces = len(whitespace.replace('\t', ''))
        # Flexible: 2, 3, or 4 spaces = 1 level
        space_levels = (spaces + 1) // 2  # Round up: 2-3 spaces = 1, 4-5 = 2, etc.
        return tabs + space_levels

    for line in content.split('\n'):
        # Check for bullet (with or without leading whitespace)
        bullet_match = re.match(r'^(\s*)([\-\*✦•▸►◆])\s+(.+)$', line)
        if bullet_match:
            whitespace = bullet_match.group(1)
            bullet_text = bullet_match.group(3).strip()
            indent_level = get_indent_level(whitespace)

            node = {
                'text': bullet_text,
                'children': []
            }

            if indent_level == 0:
                # Top-level bullet
                hierarchical_bullets.append(node)
                parent_stack = [(0, node)]
            else:
                # Nested bullet - find appropriate parent
                # Pop parents that are at same or deeper indent level
                while parent_stack and parent_stack[-1][0] >= indent_level:
                    parent_stack.pop()

                if parent_stack:
                    # Add as child of the most recent shallower parent
                    parent_node = parent_stack[-1][1]
                    parent_node['children'].append(node)
                else:
                    # No valid parent (orphan nested bullet) - treat as top-level
                    hierarchical_bullets.append(node)

                parent_stack.append((indent_level, node))
            continue

        # Check for numbered list at top level
        num_match = re.match(r'^(\d+)\.\s+(.+)$', line)
        if num_match:
            node = {
                'text': num_match.group(2).strip(),
                'children': [],
                'number': int(num_match.group(1))
            }
            hierarchical_bullets.append(node)
            parent_stack = [(0, node)]
            continue

    # Numbered headings format: ### 1. Title\nDescription
    numbered_headings = re.findall(r'^###\s*\d+\.\s*(.+)$', content, re.MULTILINE)

    # Build flat body list (backward compatible) from top-level bullets
    all_bullets = [b['text'] for b in hierarchical_bullets] + numbered_headings
    slide['body'] = [b.strip() for b in all_bullets]

    # Store hierarchical structure for column-based layouts
    if hierarchical_bullets:
        slide['_hierarchical_bullets'] = hierarchical_bullets

    # Parse typography markers from bullets and store styled runs
    # This allows the generator to apply colors, sizes, bullet chars, etc.
    body_styled = []
    has_body_typography = False
    for bullet in slide['body']:
        parsed = parse_typography_markers(bullet)
        item = {
            'text': parsed['plain'],
            'runs': parsed['runs'] if parsed['has_typography'] else None
        }
        # Include paragraph-level formatting if present
        if parsed.get('paragraph'):
            item['paragraph'] = parsed['paragraph']
        body_styled.append(item)
        if parsed['has_typography']:
            has_body_typography = True

    if has_body_typography:
        slide['body_styled'] = body_styled

    # Warn about very long bullet points
    for i, bullet in enumerate(slide['body']):
        if len(bullet) > 200:
            warn(f"Bullet {i+1} is very long ({len(bullet)} chars), may overflow")

    # Capture paragraph body text when no bullets were found
    # This catches story-card style slides that use paragraphs instead of bullets
    if not slide['body'] and slide.get('headline'):
        # Also capture second bold line as subtitle (first body item)
        non_marker_bolds = [
            t.strip() for t in bold_lines
            if not t.strip().lower().startswith(('visual:', 'note:', 'image:', 'contact:', 'layout:', 'title:'))
        ]
        subtitle_line = None
        if len(non_marker_bolds) >= 2:
            parsed_sub = parse_typography_markers(non_marker_bolds[1])
            subtitle_line = parsed_sub['plain']

        # Extract paragraph text by stripping known non-body lines
        paragraph_lines = []
        current_paragraph = []
        headline_plain = slide.get('headline', '')
        for line in content.split('\n'):
            stripped = line.strip()
            # Skip empty lines (paragraph boundary)
            if not stripped:
                if current_paragraph:
                    paragraph_lines.append(' '.join(current_paragraph))
                    current_paragraph = []
                continue
            # Skip headers
            if stripped.startswith('#'):
                continue
            # Skip bold visual/layout/title markers
            if re.match(r'^\*\*(?:Visual|Layout|Note|Image|Contact|Title):', stripped, re.IGNORECASE):
                continue
            # Skip the headline bold line itself
            if re.match(r'^\*\*[^*]+\*\*\s*$', stripped):
                continue
            # Skip [Image:], [Background:], [HINT:], [IMG:], [LOGO:], [NOTES:] markers
            if re.match(r'^\[(?:Image|Background|HINT|IMG|LOGO|NOTES):', stripped, re.IGNORECASE):
                continue
            # Skip table rows
            if '|' in stripped and re.search(r'\|.*\|', stripped):
                continue
            # Skip bullets (already handled above)
            if re.match(r'^[\-\*✦•▸►◆]\s+', stripped):
                continue
            if re.match(r'^\d+\.\s+', stripped):
                continue
            # Skip --- dividers
            if stripped == '---':
                continue
            # Skip typography-only lines like {question}...{/question}
            if re.match(r'^\{[a-z]+\}.*\{/[a-z]+\}$', stripped):
                # Include these as body content (they contain meaningful text)
                clean = re.sub(r'\{/?[a-z]+\}', '', stripped).strip()
                if clean:
                    current_paragraph.append(clean)
                continue
            # This is paragraph body text
            current_paragraph.append(stripped)

        if current_paragraph:
            paragraph_lines.append(' '.join(current_paragraph))

        # Build body from subtitle + paragraphs
        body_items = []
        if subtitle_line:
            body_items.append(subtitle_line)
        body_items.extend(paragraph_lines)

        if body_items:
            slide['body'] = body_items

    # Extract quote (blockquote) and attribution
    quote_match = re.search(r'^>\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    if quote_match:
        slide['has_quote'] = True
        quote_text = quote_match.group(1).strip().strip('"\'*')
        parsed_quote = parse_typography_markers(quote_text)
        slide['quote'] = parsed_quote['plain']
        if parsed_quote['has_typography']:
            slide['quote_styled'] = parsed_quote['runs']

        # Look for attribution line (starts with — or - after the quote)
        # Pattern: line starting with em-dash or hyphen, optionally inside blockquote (> prefix)
        # Try blockquote format first (> — attribution), then standalone (— attribution)
        attribution_match = re.search(r'^>\s*[—–-]\s*(.+)$', content, re.MULTILINE)
        if not attribution_match:
            attribution_match = re.search(r'^[—–-]\s*(.+)$', content, re.MULTILINE)
        if attribution_match:
            slide['quote_attribution'] = attribution_match.group(1).strip()

    # Parse table
    if has_table:
        slide['has_table'] = True
        table_result = parse_table_safe(content, ctx)
        slide['table'] = table_result

    # Check for image markers
    image_markers = re.findall(r'\[image[:\s]|\{\{image', content, re.IGNORECASE)
    if image_markers:
        slide['has_image'] = True
        slide['image_count'] = len(image_markers)

    # Check for visual/diagram indicators - support both **Visual:** and **Visual: type**
    visual_match = re.search(r'\*\*Visual:\s*(.+?)\*\*', content, re.IGNORECASE)
    if not visual_match:
        visual_match = re.search(r'\*\*Visual:\*\*\s*(.+)', content, re.IGNORECASE)
    if visual_match:
        raw_visual_type = visual_match.group(1).strip()
        slide['visual_type'] = raw_visual_type

        # Validate visual type and provide suggestions for typos
        is_valid, best_match, suggestions = validate_visual_type(raw_visual_type)
        if not is_valid and suggestions:
            # Store the suggestion for later reporting
            slide['_visual_type_suggestion'] = {
                'original': raw_visual_type,
                'best_match': best_match,
                'suggestions': suggestions,
            }
            # Add warning to context if available
            if ctx:
                suggestion_msg = format_visual_type_suggestion(raw_visual_type, suggestions)
                ctx.add_warning(f"Slide {slide_number}: {suggestion_msg}")

    # Convert table to columns if a column-based visual type is specified
    # This handles the case where content is expressed as markdown table but should
    # be rendered as columns (cards-N, process-N-phase, comparison-N)
    visual_type_lower = slide.get('visual_type', '').lower()
    if has_table and slide.get('table') and visual_type_lower:
        # Check if the visual type expects column structure
        is_column_visual = any(vt in visual_type_lower for vt in COLUMN_BASED_VISUAL_TYPES)
        if is_column_visual:
            # Convert table to columns
            table_columns = convert_table_to_columns(slide['table'])
            if table_columns:
                slide['columns'] = table_columns
                # Clear the table flags since we're treating this as columns
                slide['has_table'] = False
                del slide['table']
                # No warning needed - this is expected behavior

    # Check for explicit layout override - **Layout: name** or **Layout:** name
    # This allows manual override when auto-detection picks the wrong layout
    layout_match = re.search(r'\*\*Layout:\s*(.+?)\*\*', content, re.IGNORECASE)
    if not layout_match:
        layout_match = re.search(r'\*\*Layout:\*\*\s*(.+)', content, re.IGNORECASE)
    if layout_match:
        slide['layout_override'] = layout_match.group(1).strip()

        # Capture leading content before Visual declaration
        # This is paragraph text between slide header and Visual: line
        leading_content = content[:visual_match.start()].strip()
        if leading_content:
            # Filter out the title/heading lines
            lines = leading_content.split('\n')
            leading_lines = []
            for line in lines:
                line_stripped = line.strip()
                # Skip empty lines, headers, and already-captured elements
                if not line_stripped:
                    continue
                if line_stripped.startswith('#'):
                    continue
                if line_stripped.startswith('---'):
                    continue
                leading_lines.append(line_stripped)

            if leading_lines:
                slide['leading_text'] = '\n'.join(leading_lines)

    # Check for [HINT: layout_name] — the Step-2 authoring marker.
    # A hint names one of the template's REAL slide-master layouts (the names
    # emitted by Step 1 / pptx-profile into layout-catalog.md). It is the same
    # mechanism as **Layout:**, just the friendlier syntax borrowed from the
    # IC deck renderer. An explicit **Layout:** marker, if present, wins.
    if not slide.get('layout_override'):
        hint_match = re.search(r'\[HINT:\s*([^\]]+)\]', content, re.IGNORECASE)
        if hint_match:
            slide['layout_override'] = hint_match.group(1).strip()

    # Check for explicit **Title:** marker (with phase prefix support)
    # Pattern: **Title:** {blue}Phase N: Label{/blue}Title Text
    # This overrides the title extracted from slide header
    title_marker_match = re.search(r'\*\*Title:\*\*\s*(.+)$', content, re.MULTILINE)
    if title_marker_match:
        full_title = title_marker_match.group(1).strip()
        # Parse typography markers to get styled title
        parsed_title = parse_typography_markers(full_title)
        slide['title'] = parsed_title['plain']
        if parsed_title['has_typography']:
            slide['title_styled'] = parsed_title['runs']

    # Extract column content for multi-column layouts
    columns, trailing_content = extract_columns(content)
    if columns:
        # Check for 6+ columns (template limit is 5)
        if len(columns) > 5:
            if ctx:
                ctx.add_warning(
                    f"Slide has {len(columns)} columns, truncating to 5 (template limit)",
                    line_content=f"Columns: {', '.join(c['header'] for c in columns[:6])}"
                )
            columns = columns[:5]  # Keep first 5 columns
        slide['columns'] = columns

        # Clear redundant body - column content is more structured and already captures bullets
        slide['body'] = []

        # Add trailing content as slide-level body/footnote
        if trailing_content:
            # Parse trailing content for key-value pairs
            kv_match = re.match(r'^\*\*([^*]+):\*\*\s*(.+)$', trailing_content, re.DOTALL)
            if kv_match:
                slide['trailing_note'] = {
                    'label': kv_match.group(1).strip(),
                    'text': kv_match.group(2).strip()
                }
            else:
                slide['trailing_note'] = {'text': trailing_content}

    # If no explicit columns but we have hierarchical bullets with children,
    # create columns from the hierarchy (for comparison-N, cards-N, process-N visual types)
    if not columns and slide.get('_hierarchical_bullets'):
        h_bullets = slide['_hierarchical_bullets']
        # Only create columns if bullets have nested children (indicates column structure)
        has_nested = any(b.get('children') for b in h_bullets)
        if has_nested:
            auto_columns = []
            for i, bullet in enumerate(h_bullets):
                header_text = bullet['text']
                # Parse typography markers from header (for custom colors, etc.)
                header_parsed = parse_typography_markers(header_text)
                col = {
                    'number': bullet.get('number', i + 1),
                    'header': header_parsed['plain'],
                    'body': _flatten_hierarchy_to_body(bullet.get('children', [])),
                    'intro': [],
                }
                # Add header_styled if typography markers were found
                if header_parsed['has_typography']:
                    col['header_styled'] = header_parsed['runs']
                auto_columns.append(col)
            if auto_columns:
                slide['columns'] = auto_columns
                # Clear flat body since we have structured columns now
                slide['body'] = []

    # Extract card content for cards layouts
    cards = extract_cards(content)
    if cards:
        slide['cards'] = cards
        # Clear redundant body - card content is more structured
        slide['body'] = []

    # Extract timeline entries
    timeline = extract_timeline(content)
    if timeline:
        slide['timeline'] = timeline

    # Extract pricing entries (investment/pricing tables)
    pricing = extract_pricing(content)
    if pricing:
        slide['pricing'] = pricing

    # Extract contact info (for contact slides)
    contact = extract_contact_info(content)
    if contact:
        slide['contact'] = contact

    # Extract table blocks (for comparison-tables visual type)
    table_blocks = extract_table_blocks(content)
    if table_blocks:
        slide['table_blocks'] = table_blocks

    # Extract background image (for story-card visual type)
    background = extract_background(content)
    if background:
        slide['background'] = background

    # Note: paragraph body extraction (for story-card and similar slides) is now handled
    # above in the "Capture paragraph body text" block after bullet extraction.
    visual_type = slide.get('visual_type', '').lower()

    # Extract data-contrast metrics (bold key-value pairs for data-contrast slides)
    if visual_type == 'data-contrast':
        data_contrast = extract_data_contrast(content)
        if data_contrast:
            slide['data_contrast'] = data_contrast

    # Validation: warn if column-based visual type specified but no columns detected
    # This helps users understand when to use correct column format
    if visual_type and not slide.get('columns') and not slide.get('cards'):
        is_column_visual = any(vt in visual_type for vt in COLUMN_BASED_VISUAL_TYPES)
        if is_column_visual:
            if ctx:
                ctx.add_warning(
                    f"Slide {slide_number}: visual type '{visual_type}' expects column content, "
                    f"but no columns were detected. Use [Column N: Header] syntax, "
                    f"hierarchical bullets, or markdown tables to define columns."
                )

    # Determine content type
    slide['content_type'] = detect_content_type(slide)

    return slide


def _split_table_row(line: str) -> list[str]:
    """Split a table row on pipes, respecting escaped pipes (\\|).

    Handles the edge case where a cell value contains a literal pipe character
    that should not be treated as a column delimiter.

    Args:
        line: A single table row string (e.g., "| A | B \\| C | D |")

    Returns:
        List of cell values with escaped pipes restored to literal pipes.

    Examples:
        >>> _split_table_row("| A | B |")
        ['A', 'B']
        >>> _split_table_row("| A \\| B | C |")
        ['A | B', 'C']
    """
    # Placeholder for escaped pipes - use a character unlikely in table content
    PLACEHOLDER = '\x00ESCAPED_PIPE\x00'

    # Temporarily replace escaped pipes
    line_with_placeholders = line.replace('\\|', PLACEHOLDER)

    # Split on unescaped pipes
    raw_cells = line_with_placeholders.split('|')

    # Restore escaped pipes in each cell
    cells = [cell.replace(PLACEHOLDER, '|') for cell in raw_cells]

    # Remove outer empty strings from leading/trailing pipes
    if cells and cells[0].strip() == '':
        cells = cells[1:]
    if cells and cells[-1].strip() == '':
        cells = cells[:-1]

    return cells


def parse_table_safe(content: str, ctx: ParseContext = None) -> dict:
    """Extract table from markdown with error handling.

    Wrapper around parse_table that catches errors.
    """
    try:
        return parse_table(content, ctx)
    except Exception as e:
        if ctx:
            ctx.add_warning(f"Failed to parse table: {e}")
        return {'headers': [], 'rows': [], 'column_count': 0, 'row_count': 0}


def parse_table(content: str, ctx: ParseContext = None) -> dict:
    """Extract table from markdown.

    A valid markdown table requires:
    1. A header row with pipes
    2. A separator row (|---|---|)
    3. Zero or more data rows

    Edge case handling:
    - Escaped pipes (\\|) are preserved as literal pipe characters in cell values
    - Ragged rows (fewer columns than header) are padded with empty strings
    - Rows with extra columns are truncated to match header count
    """
    table = {'headers': [], 'rows': [], 'column_count': 0, 'row_count': 0}

    lines = [line.strip() for line in content.split('\n') if '|' in line]

    if not lines:
        if ctx:
            ctx.add_warning("Table markers found but no valid table rows")
        return table

    # Validate that a separator row exists (required for valid markdown table)
    # Use _split_table_row to handle escaped pipes in separator detection
    has_separator = any(
        all(set(c.strip()) <= set('-: ') for c in _split_table_row(line) if c.strip())
        for line in lines
    )
    if not has_separator:
        # Not a valid table - just text with pipe characters
        return table

    for line_idx, line in enumerate(lines):
        # Parse all cells including empty ones (for spanning row detection)
        # Use _split_table_row to handle escaped pipes (\|)
        raw_cells = _split_table_row(line)

        # Get both raw cell count and non-empty cells
        all_cells = [c.strip() for c in raw_cells]
        non_empty_cells = [c for c in all_cells if c]

        # Skip separator lines (---|---|---)
        if all_cells and all(set(c) <= set('-: ') for c in all_cells):
            continue

        if not table['headers']:
            # Preserve all cells including empty ones for proper table structure
            table['headers'] = all_cells
            table['column_count'] = len(all_cells)
        else:
            # Detect section header rows: single text cell with rest empty
            # e.g., "| Part 1: Exploratory | | | | |" spans the full row
            is_section_header = (
                len(non_empty_cells) == 1 and
                len(all_cells) >= table['column_count'] - 1
            )

            if is_section_header:
                # Mark as spanning section header, don't warn
                table['rows'].append({
                    'type': 'section_header',
                    'text': non_empty_cells[0],
                    'cells': all_cells
                })
            else:
                # Handle ragged rows (mismatched column count)
                if len(all_cells) != table['column_count']:
                    if ctx:
                        ctx.add_warning(
                            f"Table row {line_idx + 1} has {len(all_cells)} columns, "
                            f"expected {table['column_count']}",
                            line_content=line
                        )
                    # Pad short rows with empty strings for graceful degradation
                    if len(all_cells) < table['column_count']:
                        all_cells.extend([''] * (table['column_count'] - len(all_cells)))
                    # Truncate long rows to match header count
                    elif len(all_cells) > table['column_count']:
                        all_cells = all_cells[:table['column_count']]

                # Preserve all cells including empty ones for proper table structure
                table['rows'].append(all_cells)

    table['row_count'] = len(table['rows'])

    # Warn about potentially large tables
    if table['row_count'] > 15 and ctx:
        ctx.add_warning(
            f"Table has {table['row_count']} rows, may not fit on slide"
        )

    return table


def convert_table_to_columns(table: dict) -> list[dict]:
    """Convert a parsed markdown table to column structure.

    When a column-based visual type (cards-N, process-N-phase, comparison-N) is
    specified with a markdown table, convert the table headers to column headers
    and table row data to column body content.

    Table format expected:
        | Header1 | Header2 | Header3 |
        |---------|---------|---------|
        | Body1   | Body2   | Body3   |

    Returns:
        List of column dicts with 'header' and 'body' keys.

    Example:
        Input: {'headers': ['Scale', 'Retention'], 'rows': [['Grow...', 'Keep...']]}
        Output: [
            {'header': 'Scale', 'body': ['Grow...']},
            {'header': 'Retention', 'body': ['Keep...']}
        ]
    """
    columns = []
    headers = table.get('headers', [])
    rows = table.get('rows', [])

    if not headers:
        return columns

    # Create column for each header
    for col_idx, header in enumerate(headers):
        column = {
            'number': col_idx + 1,
            'header': header.strip() if header else '',
            'intro': [],
            'body': [],
            'table': None,
            'image_placeholder': None
        }

        # Collect all row data for this column
        for row in rows:
            # Handle section header rows (dict format)
            if isinstance(row, dict):
                # Section headers span all columns - add to first column only
                if col_idx == 0 and row.get('type') == 'section_header':
                    column['body'].append(row.get('text', ''))
                continue

            # Regular row (list format)
            if isinstance(row, list) and col_idx < len(row):
                cell_value = row[col_idx]
                if cell_value and cell_value.strip():
                    column['body'].append(cell_value.strip())

        columns.append(column)

    return columns


def _flatten_hierarchy_to_body(children: list, base_level: int = 0) -> list:
    """Flatten recursive hierarchy into body items with indent levels.

    Converts nested children structure into a flat list where each item
    can be a string (level 0) or a dict with 'text' and 'level' keys.
    Preserves typography markers for custom colors, etc.

    Args:
        children: List of child nodes, each being either a string or
                  a dict with 'text' and optional 'children' keys.
        base_level: Starting indent level for these children.

    Returns:
        List of body items. Simple strings for level 0, or dicts with
        'text' and 'level' keys for nested items.

    Example:
        Input: [{'text': 'A', 'children': [{'text': 'A1', 'children': []}]}]
        Output: [{'text': 'A', 'level': 0}, {'text': 'A1', 'level': 1}]
    """
    result = []
    for child in children:
        if isinstance(child, str):
            # Legacy format: plain string - preserve typography markers
            # Only strip markdown bold/italic, not typography markers
            text = clean_markdown(child)
            if base_level == 0:
                result.append(text)
            else:
                result.append({'text': text, 'level': base_level})
        elif isinstance(child, dict):
            # New format: node with text and optional children
            # Preserve typography markers - only strip markdown bold/italic
            text = clean_markdown(child.get('text', ''))
            if base_level == 0:
                result.append(text)
            else:
                result.append({'text': text, 'level': base_level})
            # Recursively add nested children at deeper level
            nested = child.get('children', [])
            if nested:
                result.extend(_flatten_hierarchy_to_body(nested, base_level + 1))
    return result


def clean_markdown(text: str) -> str:
    """Remove markdown formatting."""
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    return text.strip()


# =============================================================================
# TYPOGRAPHY MARKER PARSING
# =============================================================================

# Typography marker patterns
# Format: 'name': (regex_pattern, style_dict_or_special_flag)
#
# INLINE TEXT MARKERS (wrap text):
# - {blue}text{/blue}          - Primary blue color (0196FF)
# - {color:#RRGGBB}text{/color} - Any hex color
# - {size:N}text{/size}        - Font size in points
# - {font:Name}text{/font}     - Font family name
# - {bold}text{/bold}          - Bold text
# - {italic}text{/italic}      - Italic text
# - {underline}text{/underline} - Underlined text
# - {strike}text{/strike}      - Strikethrough text
# - {super}text{/super}        - Superscript
# - {sub}text{/sub}            - Subscript
# - {caps}text{/caps}          - All caps
# - {signpost}text{/signpost}  - Signposting (14pt, blue)
# - {question}text{/question}  - Key questions (italic, blue)
#
# PARAGRAPH-LEVEL MARKERS (at start of line, no closing tag):
# - {bullet:-}                 - Dash bullet character
# - {bullet:•}                 - Round bullet character
# - {bullet:none}              - No bullet (remove default)
# - {bullet:1}                 - Numbered list (auto-increment)
# - {level:N}                  - Indent level (0-4)
# - {space:before:Npt}         - Space before paragraph
# - {space:after:Npt}          - Space after paragraph

# Simple marker styles (marker_name -> style dict)
# Used for tokenization-based parsing that supports nesting
TYPOGRAPHY_STYLES = {
    # Colors
    'blue': {'color': '0196FF'},
    # Fonts (size, color, font handled specially during tokenization)
    # Basic text formatting
    'bold': {'bold': True},
    'italic': {'italic': True},
    'underline': {'underline': True},
    'strike': {'strike': True},
    # Text position
    'super': {'superscript': True},
    'sub': {'subscript': True},
    # Text transform
    'caps': {'caps': True},
    # Semantic styles (compound)
    'signpost': {'type': 'signpost', 'color': '0196FF', 'size': 14},
    'question': {'type': 'question', 'italic': True, 'color': '0196FF'},
}

# Regex patterns for opening markers (with optional parameters)
OPENING_MARKER_PATTERN = re.compile(
    r'\{(blue|color|size|font|bold|italic|underline|strike|super|sub|caps|signpost|question)'
    r'(?::#?([^}]*))?\}'  # Optional parameter like :18 or :#FF0000 or :Arial
)

# Regex for closing markers
CLOSING_MARKER_PATTERN = re.compile(
    r'\{/(blue|color|size|font|bold|italic|underline|strike|'
    r'super|sub|caps|signpost|question)\}'
)

# Legacy patterns for backwards compatibility and plain text extraction
TYPOGRAPHY_PATTERNS = {
    # Colors
    'blue': (r'\{blue\}(.*?)\{/blue\}', {'color': '0196FF'}),
    'color': (r'\{color:#([0-9A-Fa-f]{6})\}(.*?)\{/color\}', {'color_pattern': True}),  # Special: extract hex

    # Fonts
    'size': (r'\{size:(\d+)\}(.*?)\{/size\}', {'size_pattern': True}),  # Special: extract size
    'font': (r'\{font:([^}]+)\}(.*?)\{/font\}', {'font_pattern': True}),  # Special: extract font name

    # Basic text formatting
    'bold': (r'\{bold\}(.*?)\{/bold\}', {'bold': True}),
    'italic': (r'\{italic\}(.*?)\{/italic\}', {'italic': True}),
    'underline': (r'\{underline\}(.*?)\{/underline\}', {'underline': True}),
    'strike': (r'\{strike\}(.*?)\{/strike\}', {'strike': True}),

    # Text position
    'super': (r'\{super\}(.*?)\{/super\}', {'superscript': True}),
    'sub': (r'\{sub\}(.*?)\{/sub\}', {'subscript': True}),

    # Text transform
    'caps': (r'\{caps\}(.*?)\{/caps\}', {'caps': True}),

    # Semantic styles (compound)
    'signpost': (r'\{signpost\}(.*?)\{/signpost\}', {'type': 'signpost', 'color': '0196FF', 'size': 14}),
    'question': (r'\{question\}(.*?)\{/question\}', {'type': 'question', 'italic': True, 'color': '0196FF'}),
}

# Paragraph-level markers (parsed separately, not wrapped)
PARAGRAPH_MARKERS = {
    'bullet': r'\{bullet:([^}]+)\}',     # {bullet:-}, {bullet:•}, {bullet:none}, {bullet:1}
    'level': r'\{level:(\d)\}',          # {level:0} through {level:4}
    'space_before': r'\{space:before:(\d+)pt\}',  # {space:before:12pt}
    'space_after': r'\{space:after:(\d+)pt\}',    # {space:after:6pt}
}


def _tokenize_typography(text: str) -> list[dict]:
    """Tokenize text into opening markers, closing markers, and plain text.

    Returns a list of token dicts:
    - {'type': 'open', 'marker': 'bold', 'param': None, 'pos': 0, 'end': 6}
    - {'type': 'close', 'marker': 'bold', 'pos': 10, 'end': 18}
    - {'type': 'text', 'content': 'hello', 'pos': 6, 'end': 11}
    """
    tokens = []
    pos = 0

    while pos < len(text):
        # Try to match opening marker
        open_match = OPENING_MARKER_PATTERN.match(text, pos)
        if open_match:
            marker = open_match.group(1)
            param = open_match.group(2)  # Could be None
            tokens.append({
                'type': 'open',
                'marker': marker,
                'param': param,
                'pos': open_match.start(),
                'end': open_match.end()
            })
            pos = open_match.end()
            continue

        # Try to match closing marker
        close_match = CLOSING_MARKER_PATTERN.match(text, pos)
        if close_match:
            marker = close_match.group(1)
            tokens.append({
                'type': 'close',
                'marker': marker,
                'pos': close_match.start(),
                'end': close_match.end()
            })
            pos = close_match.end()
            continue

        # Find the next marker (open or close) to know where plain text ends
        next_open = OPENING_MARKER_PATTERN.search(text, pos)
        next_close = CLOSING_MARKER_PATTERN.search(text, pos)

        next_pos = len(text)
        if next_open:
            next_pos = min(next_pos, next_open.start())
        if next_close:
            next_pos = min(next_pos, next_close.start())

        # Add plain text token
        if next_pos > pos:
            tokens.append({
                'type': 'text',
                'content': text[pos:next_pos],
                'pos': pos,
                'end': next_pos
            })
        pos = next_pos

    return tokens


def _get_style_for_marker(marker: str, param: str | None) -> dict:
    """Get the style dict for a marker, handling parameterized markers."""
    if marker == 'color' and param:
        # param is hex color like "FF0000"
        return {'color': param.upper()}
    elif marker == 'size' and param:
        # param is size like "18"
        try:
            return {'size': int(param)}
        except ValueError:
            return {}
    elif marker == 'font' and param:
        # param is font name
        return {'font': param}
    elif marker in TYPOGRAPHY_STYLES:
        return TYPOGRAPHY_STYLES[marker].copy()
    return {}


def _merge_styles(base: dict | None, overlay: dict) -> dict:
    """Merge two style dicts, overlay takes precedence."""
    if base is None:
        return overlay.copy()
    result = base.copy()
    result.update(overlay)
    return result


def _parse_nested_typography(text: str) -> list[dict]:
    """Parse text with potentially nested typography markers into text runs.

    Uses a stack-based approach to properly handle nested markers like:
    {bold}{blue}text{/blue}{/bold} -> merged bold+blue style

    Returns list of {'text': str, 'style': dict|None} dicts.
    """
    tokens = _tokenize_typography(text)

    if not tokens:
        return [{'text': text, 'style': None}] if text else []

    # Stack of active styles: [(marker_name, style_dict), ...]
    style_stack = []
    runs = []

    def get_current_merged_style() -> dict | None:
        """Get the merged style from all active markers."""
        if not style_stack:
            return None
        merged = {}
        for _, style in style_stack:
            merged.update(style)
        return merged if merged else None

    for token in tokens:
        if token['type'] == 'text':
            content = token['content']
            if content:  # Don't add empty runs
                runs.append({
                    'text': content,
                    'style': get_current_merged_style()
                })
        elif token['type'] == 'open':
            style = _get_style_for_marker(token['marker'], token.get('param'))
            style_stack.append((token['marker'], style))
        elif token['type'] == 'close':
            # Pop matching marker from stack (handle mismatched markers gracefully)
            marker = token['marker']
            # Find and remove the most recent matching marker
            for i in range(len(style_stack) - 1, -1, -1):
                if style_stack[i][0] == marker:
                    style_stack.pop(i)
                    break
            # If no matching marker found, just ignore the close tag

    return runs


def parse_typography_markers(text: str) -> dict:
    """Parse typography markers from text and return structured format.

    Supports inline text markers (including nested markers):
    - {blue}text{/blue} - Blue emphasis (0196FF)
    - {color:#RRGGBB}text{/color} - Any hex color
    - {size:N}text{/size} - Explicit font size
    - {font:Name}text{/font} - Font family
    - {bold}text{/bold} - Bold text
    - {italic}text{/italic} - Italic text
    - {underline}text{/underline} - Underlined text
    - {strike}text{/strike} - Strikethrough text
    - {super}text{/super} - Superscript
    - {sub}text{/sub} - Subscript
    - {caps}text{/caps} - All caps
    - {signpost}label{/signpost} - Signposting (14pt, blue)
    - {question}How do we...?{/question} - Key questions (italic, blue)

    Nested markers are fully supported:
    - {bold}{blue}text{/blue}{/bold} -> bold blue text
    - {size:18}{italic}text{/italic}{/size} -> 18pt italic text

    Returns:
        dict with:
        - 'plain': Clean text with markers removed
        - 'runs': List of text runs with style info [{text, style}]
        - 'has_typography': Boolean indicating if any markers were found
        - 'paragraph': Dict with paragraph-level styles (bullet, level, spacing)
    """
    result = {
        'plain': text,
        'runs': [],
        'has_typography': False,
        'paragraph': {}  # Paragraph-level formatting
    }

    # Parse paragraph-level markers first (these are at line start, no closing tag)
    working_text = text
    for marker_name, pattern in PARAGRAPH_MARKERS.items():
        match = re.search(pattern, working_text)
        if match:
            result['has_typography'] = True
            if marker_name == 'bullet':
                bullet_char = match.group(1)
                if bullet_char == 'none':
                    result['paragraph']['bullet'] = None  # Explicit no bullet
                elif bullet_char == '1':
                    result['paragraph']['bullet'] = 'numbered'
                else:
                    result['paragraph']['bullet'] = bullet_char  # '-', '•', etc.
            elif marker_name == 'level':
                result['paragraph']['level'] = int(match.group(1))
            elif marker_name == 'space_before':
                result['paragraph']['space_before'] = int(match.group(1))
            elif marker_name == 'space_after':
                result['paragraph']['space_after'] = int(match.group(1))
            # Remove the marker from working text
            working_text = working_text[:match.start()] + working_text[match.end():]

    # Check if text has any inline typography markers
    has_inline_markers = bool(OPENING_MARKER_PATTERN.search(working_text))

    if not has_inline_markers and not result['paragraph']:
        # No markers at all - return plain text as single run
        result['runs'] = [{'text': working_text.strip(), 'style': None}]
        result['plain'] = working_text.strip()
        return result

    result['has_typography'] = True

    # Use nested-aware parsing for inline markers
    runs = _parse_nested_typography(working_text)

    # Build the plain text by concatenating all run text
    plain_text = ''.join(run['text'] for run in runs)

    result['plain'] = plain_text.strip()
    result['runs'] = runs

    return result


def extract_typography_from_text(text: str) -> tuple[str, list[dict]]:
    """Extract typography markers and return clean text + styled runs.

    Convenience function that returns just the essential parts.

    Returns:
        Tuple of (clean_text, runs) where runs is [{text, style}, ...]
    """
    parsed = parse_typography_markers(text)
    return parsed['plain'], parsed['runs']


# =============================================================================
# MARKER VALIDATION
# =============================================================================


class MarkerValidationError:
    """Represents a validation error or warning for typography markers."""

    def __init__(
        self,
        error_type: str,
        message: str,
        position: int | None = None,
        marker: str | None = None,
        severity: str = 'error'
    ):
        """Initialize a marker validation error.

        Args:
            error_type: Type of error (e.g., 'unclosed_marker', 'unmatched_close', 'invalid_param')
            message: Human-readable error message
            position: Character position in the original text (0-indexed)
            marker: The marker name involved (e.g., 'bold', 'color')
            severity: 'error' or 'warning'
        """
        self.error_type = error_type
        self.message = message
        self.position = position
        self.marker = marker
        self.severity = severity

    def __repr__(self) -> str:
        pos_str = f" at position {self.position}" if self.position is not None else ""
        return f"MarkerValidationError({self.error_type}: {self.message}{pos_str})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, MarkerValidationError):
            return False
        return (
            self.error_type == other.error_type
            and self.message == other.message
            and self.position == other.position
            and self.marker == other.marker
            and self.severity == other.severity
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'error_type': self.error_type,
            'message': self.message,
            'position': self.position,
            'marker': self.marker,
            'severity': self.severity
        }


# Valid marker names (must match OPENING_MARKER_PATTERN)
VALID_MARKERS = {
    'blue', 'color', 'size', 'font', 'bold', 'italic', 'underline',
    'strike', 'super', 'sub', 'caps', 'signpost', 'question'
}

# Markers that require parameters
PARAMETERIZED_MARKERS = {'color', 'size', 'font'}

# Hex color pattern for validation
HEX_COLOR_PATTERN = re.compile(r'^[0-9A-Fa-f]{6}$')


def _validate_marker_parameter(marker: str, param: str | None, position: int) -> list[MarkerValidationError]:
    """Validate a marker's parameter value.

    Args:
        marker: The marker name
        param: The parameter value (may be None)
        position: Position in text for error reporting

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    if marker == 'color':
        if not param:
            errors.append(MarkerValidationError(
                error_type='missing_param',
                message="Color marker requires a hex color value (e.g., {color:#FF0000})",
                position=position,
                marker=marker,
                severity='error'
            ))
        elif not HEX_COLOR_PATTERN.match(param):
            errors.append(MarkerValidationError(
                error_type='invalid_param',
                message=f"Invalid hex color '{param}' - must be 6 hex digits (e.g., FF0000)",
                position=position,
                marker=marker,
                severity='error'
            ))

    elif marker == 'size':
        if not param:
            errors.append(MarkerValidationError(
                error_type='missing_param',
                message="Size marker requires a numeric value (e.g., {size:18})",
                position=position,
                marker=marker,
                severity='error'
            ))
        else:
            try:
                size = int(param)
                if size <= 0:
                    errors.append(MarkerValidationError(
                        error_type='invalid_param',
                        message=f"Size must be positive, got {size}",
                        position=position,
                        marker=marker,
                        severity='error'
                    ))
                elif size > 400:
                    errors.append(MarkerValidationError(
                        error_type='invalid_param',
                        message=f"Size {size} seems unreasonably large (max 400pt)",
                        position=position,
                        marker=marker,
                        severity='warning'
                    ))
            except ValueError:
                errors.append(MarkerValidationError(
                    error_type='invalid_param',
                    message=f"Size must be a number, got '{param}'",
                    position=position,
                    marker=marker,
                    severity='error'
                ))

    elif marker == 'font':
        if not param:
            errors.append(MarkerValidationError(
                error_type='missing_param',
                message="Font marker requires a font name (e.g., {font:Arial})",
                position=position,
                marker=marker,
                severity='error'
            ))
        elif not param.strip():
            errors.append(MarkerValidationError(
                error_type='invalid_param',
                message="Font name cannot be empty",
                position=position,
                marker=marker,
                severity='error'
            ))

    return errors


def validate_markers(text: str) -> dict:
    """Validate typography markers in text using stack-based parsing.

    Performs comprehensive validation:
    - Checks for properly balanced opening/closing markers
    - Reports unclosed markers with their positions
    - Reports unexpected closing markers
    - Validates marker parameters (colors, sizes, fonts)
    - Checks for common issues like wrong close order

    Args:
        text: Text containing typography markers

    Returns:
        dict with:
        - 'valid': bool - True if no errors (warnings are ok)
        - 'errors': list[MarkerValidationError] - All validation errors
        - 'warnings': list[MarkerValidationError] - All validation warnings
        - 'unclosed': list[dict] - Markers that were opened but not closed
        - 'unmatched_closes': list[dict] - Close markers without matching opens
    """
    result = {
        'valid': True,
        'errors': [],
        'warnings': [],
        'unclosed': [],
        'unmatched_closes': []
    }

    # Strip paragraph markers first (they don't need closing)
    working_text = text
    for marker_name, pattern in PARAGRAPH_MARKERS.items():
        working_text = re.sub(pattern, '', working_text)

    tokens = _tokenize_typography(working_text)

    # Stack of open markers: [(marker_name, param, position), ...]
    marker_stack = []

    for token in tokens:
        if token['type'] == 'open':
            marker = token['marker']
            param = token.get('param')
            position = token['pos']

            # Validate parameter
            param_errors = _validate_marker_parameter(marker, param, position)
            for err in param_errors:
                if err.severity == 'error':
                    result['errors'].append(err)
                    result['valid'] = False
                else:
                    result['warnings'].append(err)

            # Push to stack
            marker_stack.append({
                'marker': marker,
                'param': param,
                'position': position
            })

        elif token['type'] == 'close':
            marker = token['marker']
            position = token['pos']

            # Try to find matching open marker
            found_match = False
            for i in range(len(marker_stack) - 1, -1, -1):
                if marker_stack[i]['marker'] == marker:
                    # Found match - check if it's the most recent (proper nesting)
                    if i != len(marker_stack) - 1:
                        # Out-of-order close - this is a warning, not error
                        # The parser handles this gracefully
                        result['warnings'].append(MarkerValidationError(
                            error_type='out_of_order_close',
                            message=f"Closing {{/{marker}}} but {marker_stack[-1]['marker']} was opened more recently",
                            position=position,
                            marker=marker,
                            severity='warning'
                        ))
                    marker_stack.pop(i)
                    found_match = True
                    break

            if not found_match:
                result['errors'].append(MarkerValidationError(
                    error_type='unmatched_close',
                    message=f"Closing {{/{marker}}} without matching opening marker",
                    position=position,
                    marker=marker,
                    severity='error'
                ))
                result['unmatched_closes'].append({
                    'marker': marker,
                    'position': position
                })
                result['valid'] = False

    # Any remaining items in stack are unclosed
    for open_marker in marker_stack:
        result['errors'].append(MarkerValidationError(
            error_type='unclosed_marker',
            message=f"Unclosed {{{open_marker['marker']}}} marker",
            position=open_marker['position'],
            marker=open_marker['marker'],
            severity='error'
        ))
        result['unclosed'].append(open_marker)
        result['valid'] = False

    return result


def parse_typography_markers_with_validation(text: str) -> dict:
    """Parse typography markers with full validation.

    Combines parsing and validation into a single call.

    Args:
        text: Text containing typography markers

    Returns:
        dict with all fields from parse_typography_markers plus:
        - 'validation': dict from validate_markers()
    """
    parsed = parse_typography_markers(text)
    validation = validate_markers(text)
    parsed['validation'] = validation
    return parsed


def has_typography_markers(text: str) -> bool:
    """Check if text contains any typography markers (inline or paragraph-level)."""
    # Inline markers
    inline_patterns = [
        r'\{blue\}', r'\{color:#[0-9A-Fa-f]{6}\}', r'\{size:\d+\}', r'\{font:[^}]+\}',
        r'\{bold\}', r'\{italic\}', r'\{underline\}', r'\{strike\}',
        r'\{super\}', r'\{sub\}', r'\{caps\}', r'\{signpost\}', r'\{question\}'
    ]
    # Paragraph markers
    paragraph_patterns = [
        r'\{bullet:[^}]+\}', r'\{level:\d\}',
        r'\{space:before:\d+pt\}', r'\{space:after:\d+pt\}'
    ]
    all_patterns = inline_patterns + paragraph_patterns
    return any(re.search(p, text) for p in all_patterns)


def parse_inline_table(text: str) -> dict | None:
    """Parse a markdown table from text.

    Returns dict with headers and rows if a valid table is found.
    A valid table requires a separator row (|---|---|).

    Edge case handling:
    - Escaped pipes (\\|) are preserved as literal pipe characters in cell values
    - Ragged rows (fewer columns than header) are padded with empty strings
    - Rows with extra columns are truncated to match header count
    """
    lines = text.strip().split('\n')
    table_lines = []
    in_table = False

    for line in lines:
        line = line.strip()
        if '|' in line and re.search(r'\|.*\|', line):
            in_table = True
            table_lines.append(line)
        elif in_table and not line:
            # Empty line ends table
            break

    if not table_lines:
        return None

    # Validate that a separator row exists (required for valid markdown table)
    # Use _split_table_row to handle escaped pipes in separator detection
    has_separator = any(
        all(set(c.strip()) <= set('-: ') for c in _split_table_row(line) if c.strip())
        for line in table_lines
    )
    if not has_separator:
        return None

    headers = []
    rows = []
    column_count = 0

    for i, line in enumerate(table_lines):
        # Parse cells using _split_table_row to handle escaped pipes (\|)
        raw_cells = _split_table_row(line)
        cells = [clean_markdown(c.strip()) for c in raw_cells]

        # Skip separator lines
        if all(set(c) <= set('-: ') for c in cells):
            continue

        if not headers:
            headers = cells
            column_count = len(cells)
        else:
            # Handle ragged rows
            if len(cells) < column_count:
                # Pad short rows with empty strings
                cells.extend([''] * (column_count - len(cells)))
            elif len(cells) > column_count:
                # Truncate long rows
                cells = cells[:column_count]
            rows.append(cells)

    if headers:
        return {'headers': headers, 'rows': rows}
    return None


def extract_columns(content: str) -> tuple[list[dict] | None, str | None]:
    """Extract column blocks from markdown content.

    Looks for patterns like:
    [Column 1: Header]
    - bullet
    - bullet
    [Image: description]

    Returns:
        Tuple of (columns, trailing_content) where trailing_content is any
        slide-level content that appears after all column blocks.
    """
    columns = []
    trailing_content = None

    # Match [Column N: Header] blocks - use finditer to get positions
    column_pattern = r'\[Column\s+(\d+):\s*([^\]]+)\]'
    column_starts = list(re.finditer(column_pattern, content, re.IGNORECASE))

    if not column_starts:
        return None, None

    # Process each column block
    for i, match in enumerate(column_starts):
        num = match.group(1)
        header = match.group(2)
        start_pos = match.end()

        # End position is either next column start or end of content
        if i + 1 < len(column_starts):
            end_pos = column_starts[i + 1].start()
        else:
            end_pos = len(content)

        body = content[start_pos:end_pos]

        # For the last column, check for trailing slide-level content
        # Slide-level content typically starts with special markers like [Right Panel]
        # or standalone bold headlines (not followed by bullets)
        if i == len(column_starts) - 1:
            # Look for explicit slide-level markers: [Right Panel], [Caption], etc.
            slide_level_markers = [r'\[Right Panel\]', r'\[Caption\]', r'\[Footer\]', r'\[Note\]']
            for marker_pattern in slide_level_markers:
                marker_match = re.search(marker_pattern, body, re.IGNORECASE)
                if marker_match:
                    trailing_content = body[marker_match.start():].strip()
                    body = body[:marker_match.start()]
                    break
        column = {
            'number': int(num),
            'header': header.strip(),
            'intro': [],  # Plain text before bullets
            'body': [],   # Bullet points
            'table': None,  # Inline table if present
            'image_placeholder': None
        }

        # Check for inline table in body
        if '|' in body and re.search(r'\|.*\|', body):
            inline_table = parse_inline_table(body)
            if inline_table:
                column['table'] = inline_table

        # Parse body line by line to capture both intro text and bullets
        lines = body.strip().split('\n')
        found_bullet = False
        in_table = False

        for line in lines:
            line_stripped = line.strip()

            # Skip table lines (already parsed)
            if '|' in line_stripped and re.search(r'\|.*\|', line_stripped):
                in_table = True
                continue
            if in_table and not line_stripped:
                in_table = False
                continue

            # Skip empty lines, [Image:] markers, headers
            if not line_stripped or line_stripped.startswith('[') or line_stripped.startswith('#'):
                continue

            # Skip bold key-value pairs (handled separately below)
            if re.match(r'^\*\*[^*]+:\*\*', line_stripped):
                continue

            # Check if this is a bullet
            bullet_match = re.match(r'^[\-\*•]\s+(.+)$', line_stripped)
            if bullet_match:
                found_bullet = True
                column['body'].append(clean_markdown(bullet_match.group(1)))
            elif not found_bullet:
                # Before any bullets - this is intro text
                column['intro'].append(clean_markdown(line_stripped))

        # If no bullets found, intro becomes the body (backward compatibility)
        if not column['body'] and column['intro']:
            column['body'] = column['intro']
            column['intro'] = []

        # Extract bold items as key-value pairs (common in comparison columns)
        kv_pairs = re.findall(r'\*\*([^*]+):\*\*\s*(.+)$', body, re.MULTILINE)
        if kv_pairs:
            column['key_values'] = {k.strip(): v.strip() for k, v in kv_pairs}

        # Extract image placeholder
        image_match = re.search(r'\[Image:\s*([^\]]+)\]', body, re.IGNORECASE)
        if image_match:
            column['image_placeholder'] = image_match.group(1).strip()

        columns.append(column)

    # Sort by column number
    columns.sort(key=lambda x: x['number'])
    return (columns, trailing_content) if columns else (None, None)


def extract_cards(content: str) -> list[dict] | None:
    """Extract card blocks from markdown content.

    Looks for patterns like:
    [Card 1: Title]
    Description text
    """
    cards = []
    # Match [Card N: Title] blocks
    card_pattern = r'\[Card\s+(\d+):\s*([^\]]+)\](.*?)(?=\[Card\s+\d+:|$)'
    matches = re.findall(card_pattern, content, re.DOTALL | re.IGNORECASE)

    if not matches:
        return None

    for num, title, body in matches:
        card = {
            'number': int(num),
            'title': title.strip(),
            'body': body.strip()
        }

        # Clean up body - remove leading/trailing whitespace from lines
        body_lines = [line.strip() for line in body.strip().split('\n') if line.strip()]
        card['body'] = '\n'.join(body_lines)

        cards.append(card)

    # Sort by card number
    cards.sort(key=lambda x: x['number'])
    return cards if cards else None


def is_pricing_entry(entry_text: str) -> bool:
    """Detect if a timeline-like entry is actually pricing.

    Returns True if the text contains currency/pricing patterns.
    """
    currency_patterns = [
        r'[¥$€£][\d,]+',           # ¥20,000, $500, €100
        r'[\d,]+\s*(RMB|USD|EUR|CNY|JPY|GBP)',  # 20,000 RMB
        r'\b(Total|Subtotal|VAT|Tax)\b',  # Accounting terms
        r'^\d{1,3}(,\d{3})+$',     # Just numbers with commas like "20,000"
    ]
    return any(re.search(p, entry_text, re.IGNORECASE) for p in currency_patterns)


def extract_timeline(content: str) -> list[dict] | None:
    """Extract timeline entries from markdown content.

    Looks for patterns like:
    [Week 1] Activity description
    [Jan 22] Key milestone ← annotation

    Excludes known non-timeline patterns like [Image:], [Column N:], [Card N:]
    Also excludes pricing entries (currency patterns).
    """
    timeline = []
    pricing = []
    # Match [Date/Period] Activity patterns
    timeline_pattern = r'^\[([^\]]+)\]\s+(.+)$'
    matches = re.findall(timeline_pattern, content, re.MULTILINE)

    if not matches:
        return None

    # Known non-timeline prefixes to exclude
    excluded_prefixes = ('image:', 'column', 'card', 'visual:', 'note:', 'background:', 'table')

    for date, activity in matches:
        date_lower = date.strip().lower()

        # Skip if this looks like a non-timeline marker
        if any(date_lower.startswith(prefix) for prefix in excluded_prefixes):
            continue

        # Check if this is a pricing entry, not a timeline entry
        if is_pricing_entry(activity):
            pricing.append({
                'item': date.strip(),
                'amount': activity.strip()
            })
            continue

        activity_text = activity.strip()
        annotation = None

        # Extract annotation (text after ← arrow)
        arrow_match = re.search(r'←\s*(.+)$', activity_text)
        if arrow_match:
            annotation = arrow_match.group(1).strip()
            activity_text = activity_text[:arrow_match.start()].strip()

        # Check for milestone markers
        is_milestone = '**' in activity_text or annotation or 'key milestone' in activity.lower()

        # Clean the activity text (remove markdown formatting)
        activity_text = clean_markdown(activity_text)

        entry = {
            'date': date.strip(),
            'activity': activity_text,
            'is_milestone': is_milestone
        }

        # Add annotation if present
        if annotation:
            entry['annotation'] = annotation

        timeline.append(entry)

    # If we found only pricing entries, return None for timeline
    # The pricing will be handled separately in parse_slide_content
    return timeline if timeline else None


def extract_pricing(content: str) -> list[dict] | None:
    """Extract pricing entries from markdown content.

    Looks for patterns like:
    [Setup & Project Management] ¥20,000
    [Total] $500

    Returns pricing table if majority of [Label] Value entries contain currency.
    """
    pricing = []
    # Match [Label] Value patterns
    pattern = r'^\[([^\]]+)\]\s+(.+)$'
    matches = re.findall(pattern, content, re.MULTILINE)

    if not matches:
        return None

    # Known non-pricing prefixes to exclude
    excluded_prefixes = ('image:', 'column', 'card', 'visual:', 'note:', 'background:', 'table')

    pricing_count = 0
    non_pricing_count = 0

    for label, value in matches:
        label_lower = label.strip().lower()

        # Skip known non-pricing markers
        if any(label_lower.startswith(prefix) for prefix in excluded_prefixes):
            continue

        # Check if this is a pricing entry
        if is_pricing_entry(value):
            pricing.append({
                'item': label.strip(),
                'amount': value.strip()
            })
            pricing_count += 1
        else:
            non_pricing_count += 1

    # Only return pricing if majority of entries are pricing
    if pricing and pricing_count > non_pricing_count:
        return pricing
    return None


def extract_contact_info(content: str) -> dict | None:
    """Extract contact information from markdown content.

    Looks for patterns like:
    **Contact:** Name
    **Email:** email@example.com
    name@example.com (standalone email)

    Also handles simple format:
    Name
    Title/Role

    email@example.com
    +1 (555) 123-4567
    """
    contact = {}

    # Look for explicit contact name
    # Stop at pipe (|), at-sign (@), or newline to avoid capturing email in name
    name_match = re.search(r'\*\*Contact:\*\*\s*([^|@\n]+)', content, re.IGNORECASE)
    if name_match:
        contact['name'] = clean_markdown(name_match.group(1).strip())

    # Look for explicit email
    email_match = re.search(r'\*\*Email:\*\*\s*(\S+@\S+)', content, re.IGNORECASE)
    if email_match:
        contact['email'] = email_match.group(1).strip()
    else:
        # Look for standalone email pattern
        standalone_email = re.search(r'(?<!\S)([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?!\S)', content)
        if standalone_email:
            contact['email'] = standalone_email.group(1)

    # Look for phone number patterns
    phone_match = re.search(r'(\+?1?\s*[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', content)
    if phone_match:
        contact['phone'] = phone_match.group(1).strip()

    # If no explicit name found, try simple format: first non-empty line that's not email/phone
    if not contact.get('name'):
        lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        for i, line in enumerate(lines):
            # Skip if line contains email or phone
            if '@' in line or re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', line):
                continue
            # Skip markdown headers
            if line.startswith('#'):
                continue
            # Skip visual type markers
            if line.startswith('**Visual:'):
                continue
            # This is likely the name
            contact['name'] = clean_markdown(line)
            # Next non-email/phone line might be title
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if '@' in next_line or re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', next_line):
                    continue
                if next_line.startswith('#') or next_line.startswith('**Visual:'):
                    continue
                # This is likely the title
                contact['title'] = clean_markdown(next_line)
                break
            break

    return contact if contact else None


def extract_table_blocks(content: str) -> list[dict] | None:
    """Extract table blocks from markdown content.

    Supports two formats:

    Format A: Explicit [Table N: Header] markers
    [Table 1: Option A - Full Study]
    | Category | Investment |
    |----------|------------|
    | Item | ¥20,000 |

    Format B: Natural text header followed by table (for comparison-tables)
    Option A: Two Cities (GZ / SH)

    | Category | Total (RMB) |
    |----------|-------------|
    | Item | 20,000 |

    Option B: One City (SH)

    | Category | Total (RMB) |
    |----------|-------------|
    | Item | 12,000 |

    Returns list of dicts with header and table data.
    """
    table_blocks = []

    # First, try Format A: [Table N: Header] blocks
    table_pattern = r'\[Table\s+(\d+):\s*([^\]]+)\](.*?)(?=\[Table\s+\d+:|$)'
    matches = re.findall(table_pattern, content, re.DOTALL | re.IGNORECASE)

    if matches:
        for num, header, body in matches:
            block = {
                'number': int(num),
                'header': header.strip(),
                'table': None
            }
            table_data = parse_inline_table(body)
            if table_data:
                block['table'] = table_data
            table_blocks.append(block)
        table_blocks.sort(key=lambda x: x['number'])
        return table_blocks if table_blocks else None

    # Format B: Natural text headers followed by tables
    # Find all tables with preceding headers
    lines = content.split('\n')
    current_header = None
    current_table_lines = []
    in_table = False
    block_number = 0

    for line in lines:
        line_stripped = line.strip()

        # Skip visual markers and slide headers
        if line_stripped.lower().startswith(('**visual:', '# slide', '---')):
            continue

        # Check if this is a table line
        is_table_line = '|' in line_stripped and re.search(r'\|.*\|', line_stripped)

        if is_table_line:
            in_table = True
            current_table_lines.append(line)
        elif in_table and not line_stripped:
            # Empty line might end the table, or might be between tables
            # Continue collecting in case there's a header after
            continue
        elif in_table and not is_table_line:
            # Non-table, non-empty line after table - save current table
            if current_table_lines:
                block_number += 1
                table_data = parse_inline_table('\n'.join(current_table_lines))
                if table_data:
                    table_blocks.append({
                        'number': block_number,
                        'header': current_header or '',
                        'table': table_data
                    })
                current_table_lines = []

            # Check if this line is a header for next table
            # Headers are plain text lines that aren't bullets, markers, or empty
            if (line_stripped and
                not line_stripped.startswith(('-', '*', '#', '[', '|')) and
                not line_stripped.lower().startswith('part ')):  # Skip "Part 1:" type headers
                current_header = line_stripped
            else:
                current_header = None
            in_table = False
        elif not in_table and line_stripped:
            # Not in table, non-empty line - potential header for next table
            if (not line_stripped.startswith(('-', '*', '#', '[', '|')) and
                not line_stripped.lower().startswith('part ')):
                current_header = line_stripped
            else:
                current_header = None

    # Don't forget the last table
    if current_table_lines:
        block_number += 1
        table_data = parse_inline_table('\n'.join(current_table_lines))
        if table_data:
            table_blocks.append({
                'number': block_number,
                'header': current_header or '',
                'table': table_data
            })

    # Only return if we have 2+ table blocks (for comparison-tables)
    return table_blocks if len(table_blocks) >= 2 else None


def extract_background(content: str) -> str | None:
    """Extract [Background: description] from markdown content.

    Looks for pattern like:
    [Background: young woman snacking alone at desk, cozy lighting]

    Returns the background description string or None.
    """
    match = re.search(r'\[Background:\s*([^\]]+)\]', content, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_story_card_body(content: str) -> dict:
    """Extract story-card body text (headline, subheadline, body paragraphs).

    Story-card slides have a specific structure:
    - First non-empty line after [Background:] is the headline
    - Second line is the subheadline (in quotes or following the headline)
    - Remaining paragraphs are body text

    Example:
    [Background: image description]

    The Moment
    The "Permission Structure" of Solo Indulgence

    This demand space is 100% solo...

    Returns dict with headline, subheadline, and body list.
    """
    result = {
        'headline': None,
        'subheadline': None,
        'body': []
    }

    # Remove the background marker
    text = re.sub(r'\[Background:\s*[^\]]+\]', '', content, flags=re.IGNORECASE)
    # Remove visual type marker
    text = re.sub(r'\*\*Visual:\s*[^*]+\*\*', '', text, flags=re.IGNORECASE)
    # Remove layout override marker
    text = re.sub(r'\*\*Layout:\s*[^*]+\*\*', '', text, flags=re.IGNORECASE)
    # Remove [HINT: layout_name] marker
    text = re.sub(r'\[HINT:\s*[^\]]+\]', '', text, flags=re.IGNORECASE)
    # Remove slide header
    text = re.sub(r'^# Slide \d+.*$', '', text, flags=re.MULTILINE)

    # Split into paragraphs (double newline or blank line separation)
    paragraphs = []
    current = []
    for line in text.split('\n'):
        stripped = line.strip()
        # Skip markdown headers and empty lines for paragraph splitting
        if stripped.startswith('#'):
            continue
        if not stripped:
            if current:
                paragraphs.append('\n'.join(current))
                current = []
        else:
            current.append(stripped)
    if current:
        paragraphs.append('\n'.join(current))

    # Filter out empty paragraphs
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if paragraphs:
        # First paragraph: headline (short, bold statement)
        first = paragraphs[0]
        # Split headline and subheadline if they're on consecutive lines in first paragraph
        first_lines = first.split('\n')
        if len(first_lines) >= 1:
            result['headline'] = first_lines[0].strip()
        if len(first_lines) >= 2:
            result['subheadline'] = first_lines[1].strip()
            # Remaining lines of first paragraph go to body
            if len(first_lines) > 2:
                paragraphs[0] = '\n'.join(first_lines[2:])
            else:
                paragraphs = paragraphs[1:]  # Remove first paragraph
        else:
            paragraphs = paragraphs[1:]  # Remove first paragraph (headline only)

        # Remaining paragraphs are body
        result['body'] = paragraphs

    return result


def extract_data_contrast(content: str) -> dict | None:
    """Extract data-contrast content from markdown.

    Data-contrast slides show a gap/tension between two metrics.

    Format A - Bold key-value:
    **Metric A:** +12%
    **Metric B:** -3%
    **Interpretation:** Explanation of the gap

    Format B - Bracketed sections:
    [Left Metric]
    78%
    of millennials seek premium experiences

    [Right Metric]
    23%
    of current offerings meet quality expectations

    Returns dict with metrics list and supporting text.
    """
    result = {
        'metrics': [],
        'interpretation': None,
        'question': None
    }

    # Try Format B first: [Left Metric] / [Right Metric] sections
    bracket_pattern = r'\[(Left Metric|Right Metric)\]\s*\n([^\[]+?)(?=\[|$)'
    bracket_matches = re.findall(bracket_pattern, content, re.DOTALL | re.IGNORECASE)

    if bracket_matches:
        for label, body in bracket_matches:
            lines = [l.strip() for l in body.strip().split('\n') if l.strip()]
            if lines:
                # First line is the value (e.g., "78%"), rest is description
                value = lines[0]
                description = ' '.join(lines[1:]) if len(lines) > 1 else ''
                result['metrics'].append({
                    'label': label.strip(),
                    'value': value,
                    'description': description
                })

    # Format A: Find all bold key-value pairs: **Key:** Value
    if not result['metrics']:
        kv_pattern = r'^\*\*([^*:]+):\*\*\s*(.+)$'
        matches = re.findall(kv_pattern, content, re.MULTILINE)

        for key, value in matches:
            key_lower = key.strip().lower()
            value_clean = value.strip()

            # Skip visual type marker
            if key_lower == 'visual':
                continue

            # Categorize the key-value pairs
            if key_lower in ('interpretation', 'insight', 'analysis'):
                result['interpretation'] = value_clean
            elif key_lower in ('core question', 'question', 'key question'):
                result['question'] = value_clean
            else:
                # This is a metric (e.g., "Category Growth: +1.1%")
                result['metrics'].append({
                    'label': key.strip(),
                    'value': value_clean
                })

    # Only return if we found at least one metric
    if result['metrics']:
        return result
    return None


def detect_content_type(slide: dict) -> str:
    """Determine content type from slide structure.

    Priority:
    1. Explicit visual_type declaration (from **Visual: type**)
    2. Structural detection (columns, cards, timeline, table_blocks)
    3. Title keyword matching
    4. Content-based inference
    """
    # 1. Check for explicit visual type declaration
    visual_type = slide.get('visual_type', '').lower()
    if visual_type:
        # Map visual types to content types
        visual_type_map = {
            'process-5-phase': 'framework_5col',
            'process-4-phase': 'framework_4col',
            'process-3-phase': 'framework_3col',
            'process-2-phase': 'framework_2col',
            'comparison-5': 'framework_5col',
            'comparison-4': 'framework_4col',
            'comparison-3': 'framework_3col',
            'comparison-2': 'framework_2col',
            'comparison-tables': 'comparison_tables',
            'cards-5': 'framework_cards',
            'cards-4': 'framework_4col',
            'cards-3': 'framework_3col',
            'cards-2': 'framework_2col',
            'data-contrast': 'framework_2col',
            'quote-hero': 'quote',
            'hero-statement': 'closing',
            'story-card': 'story_card',
            'table-with-image': 'table_with_image',
            'timeline-horizontal': 'timeline',
            'table': 'table',
            'bullets': 'content',
            '1_content-image-right-a': 'content_no_image',  # Layout 58 variant
            # Grid layouts - map exact visual type names to content types
            'grid-3x2-image-top-3-body': 'grid_3x2_3body',
            'grid-3x2-image-top-6-body-a': 'grid_3x2_6body',
            'grid-2x2-image-top-2-body-a': 'grid_2x2_2body',
            'grid-2x2-image-top-2-body-b': 'grid_2x2_2body_b',
            'content-image-top-4-body': 'content_image_top_4body',
            # Generic framework - infers column count from content
            'framework': 'framework',
            # Explicit layout names - return content type that maps to the same layout
            'content-image-right-a': 'content',
            'content-image-right-b': 'content',
            'title-centered': 'section_divider',
            'column-5-centered': 'framework_5col',
            'column-4-centered': 'framework_4col',
            'column-3-centered-a': 'framework_3col',
            'column-2-centered': 'framework_2col',
            'contact-black': 'contact',
            'contact-white': 'contact',
        }
        for vt, ct in visual_type_map.items():
            if vt in visual_type:
                return ct

    # 2. Check for structural content (columns, cards, timeline, table_blocks)
    if slide.get('columns'):
        col_count = len(slide['columns'])
        if col_count >= 5:
            return 'framework_5col'
        elif col_count == 4:
            return 'framework_4col'
        elif col_count == 3:
            return 'framework_3col'
        elif col_count == 2:
            return 'framework_2col'

    if slide.get('cards'):
        card_count = len(slide['cards'])
        if card_count >= 4:
            return 'framework_cards'
        elif card_count == 3:
            return 'framework_3col'
        elif card_count == 2:
            return 'framework_2col'

    # Comparison tables: multiple [Table N:] blocks detected
    if slide.get('table_blocks'):
        table_count = len(slide['table_blocks'])
        if table_count >= 2:
            return 'comparison_tables'

    if slide.get('pricing'):
        return 'pricing'

    if slide.get('timeline'):
        return 'timeline'

    title = (slide.get('title') or '').lower()
    body = slide.get('body', [])

    # 3. Check for specific slide types by title keywords
    if any(kw in title for kw in ['next step', 'contact', 'get in touch']):
        return 'contact'
    
    if any(kw in title for kw in ['picture we', 'closing', 'in summary', 'conclusion', 'paint together', 'final thought', 'parting']):
        return 'closing'
    
    if any(kw in title for kw in ['thank']):
        return 'thank_you'
    
    # Deliverables: "What You'll Get", "Deliverables", etc. with body content
    if any(kw in title for kw in ["you'll get", "deliverable", "what we deliver", "you will receive"]):
        return 'deliverables'
    
    # "Why Us" type slides - multiple proof points, should be left-aligned
    if any(kw in title for kw in ['why inner chapter', 'why us', 'why choose', 'our experience', 'our track record']):
        return 'deliverables'  # Same layout treatment as deliverables
    
    # Timeline slides
    if any(kw in title for kw in ['timeline', 'schedule', 'project plan', 'milestones']):
        return 'content'  # Left-aligned content layout
    
    if slide.get('has_table'):
        return 'table'
    
    # Title slide: has subtitle or metadata but no bullets
    if slide.get('subtitle') or (slide.get('metadata') and not body):
        return 'title_slide'
    
    # Check for framework/flow indicators
    visual_note = slide.get('visual_note', '').lower()
    if 'column' in visual_note or 'flow' in visual_note:
        if '4' in visual_note or 'four' in visual_note:
            return 'framework_4col'
        if '3' in visual_note or 'three' in visual_note:
            return 'framework_3col'
        if '2' in visual_note or 'two' in visual_note:
            return 'framework_2col'
    
    # Section divider: title only, no content at all
    if not body and not slide.get('headline') and not slide.get('has_table') and not slide.get('has_quote'):
        return 'section_divider'
    
    # Headline but no bullets: story_card layout (text left, image right)
    if slide.get('headline') and not body:
        return 'story_card'

    # Quote slide
    if slide.get('has_quote') and len(body) <= 1:
        return 'quote'

    # Image + content: use standard content layout (image handled via placeholder)
    if slide.get('has_image'):
        return 'content'
    
    return 'content'  # Default: title + bullets


# =============================================================================
# LAYOUT SELECTION (Template-Agnostic)
# =============================================================================

def select_layout(slide: dict) -> dict:
    """Select the appropriate layout based on content type.

    Uses the layout config adapter (from --config or IC defaults).
    Supports explicit layout override via 'layout_override' field.
    """
    config = get_layout_config()

    # Check for explicit layout override first ([HINT: ...] or **Layout: ...**).
    # Resolves against the ACTIVE template config so custom/profiled templates
    # honor their own layout names — then falls back to Inner Chapter defaults.
    layout_override = slide.get('layout_override', '').strip()
    if layout_override:
        matched = config.get_layout_by_name(layout_override)
        if matched:
            return {**matched, 'match_type': 'explicit_override'}
        # If still not found, warn but continue with auto-detection
        print(f"Warning: Layout hint '{layout_override}' did not match any layout "
              f"in the active template; using auto-detection. Run pptx-profile to "
              f"see this template's valid layout names.", file=sys.stderr)

    content_type = slide.get('content_type', 'content')

    # Special case: cards with varying counts
    if content_type == 'framework_cards':
        cards = slide.get('cards', [])
        if len(cards) == 5:
            layout = config.get_layout('column_5')
        elif len(cards) >= 6:
            # 6+ cards: render as deliverables in content layout
            layout = config.get_layout('content_image_right')
        else:
            layout = config.get_layout_for_content_type(content_type)
        return {
            'name': layout['name'],
            'index': layout['index'],
            'match_type': 'semantic',
            'use_case': layout.get('use', '')
        }

    # Special case: generic 'framework' infers column count from content
    if content_type == 'framework':
        columns = slide.get('columns', [])
        cards = slide.get('cards', [])
        col_count = len(columns) if columns else len(cards)
        if col_count >= 6:
            layout = config.get_layout('content_image_right')
        elif col_count == 5:
            layout = config.get_layout('column_5')
        elif col_count == 4:
            layout = config.get_layout('column_4')
        elif col_count == 3:
            layout = config.get_layout('column_3')
        elif col_count == 2:
            layout = config.get_layout('column_2')
        else:
            layout = config.get_layout('content_image_right')
        return {
            'name': layout['name'],
            'index': layout['index'],
            'match_type': 'semantic',
            'use_case': layout.get('use', '')
        }

    # Use content type routing
    layout = config.get_layout_for_content_type(content_type)

    if not layout or layout.get('use') == 'fallback':
        # For unmapped types, choose based on content structure
        body_count = len(slide.get('body', []))
        has_headline_only = slide.get('headline') and body_count == 0
        has_table = slide.get('has_table', False)

        if has_table:
            layout = config.get_layout('title_centered')
        elif has_headline_only or body_count <= 2:
            layout = config.get_layout('title_centered')
        else:
            layout = config.get_layout('content_image_right')

    return {
        'name': layout['name'],
        'index': layout['index'],
        'match_type': 'semantic',
        'use_case': layout.get('use', '')
    }


# Backwards compatibility alias
def select_ic_layout(slide: dict) -> dict:
    """Alias for select_layout (backwards compatibility)."""
    return select_layout(slide)


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def process_outline(outline_content: str) -> tuple[dict, ParseContext]:
    """Full pipeline: parse outline and assign IC layouts.

    Returns:
        Tuple of (result dict, ParseContext)
    """
    # Parse outline with hardened parser
    slides, ctx = parse_outline(outline_content)

    # Assign layouts and collect additional warnings
    legacy_warnings = []  # For backward compat with _meta.warnings
    for slide in slides:
        ctx.current_slide_num = slide.get('outline_number', 0)
        layout = select_ic_layout(slide)
        slide['layout'] = layout

        # Check for potential issues - layout-aware bullet limits
        body_count = len(slide.get('body', []))
        layout_name = slide['layout']['name']

        # Multi-column layouts can accommodate more bullets distributed across columns
        BULLET_LIMITS = {
            'title-centered': 4,        # Hero/statement slides - minimal bullets
            'content-image-right-a': 8, # Image takes space
            'column-2-centered': 12,    # 6 per column
            'column-3-centered-a': 15,  # 5 per column
            'column-4-centered': 18,    # 4-5 per column
            'column-5-centered': 20,    # 4 per column
        }
        bullet_limit = BULLET_LIMITS.get(layout_name, 10)

        if body_count > bullet_limit:
            msg = f"{body_count} bullets may overflow {layout_name} (limit ~{bullet_limit})"
            ctx.add_warning(msg)
            legacy_warnings.append(
                f"Slide {slide['outline_number']} ({slide.get('title', 'Untitled')}): {msg}"
            )

        # Flag complex visual slides
        if slide.get('visual_note'):
            msg = f"Has visual note '{slide['visual_note']}' - may need manual adjustment"
            ctx.add_warning(msg)
            legacy_warnings.append(
                f"Slide {slide['outline_number']} ({slide.get('title', 'Untitled')}): {msg}"
            )

    result = {
        'slides': slides,
        'count': len(slides),
        'warnings': legacy_warnings  # Keep for backward compat
    }

    return result, ctx


def generate_layout_plan(result: dict) -> dict:
    """Generate layout plan with optional branding slide prepended.

    Uses the layout config adapter to determine template-specific settings.
    """
    config = get_layout_config()

    slides = []
    slide_offset = 0

    # Conditionally add branding slide based on config
    if config.requires_branding_slide:
        branding_slide = {
            'slide_number': 1,
            'layout': {
                'name': config.branding_layout_name,
                'index': config.branding_layout_index,
                'match_type': 'mandatory',
                'signature': 'empty',
                'placeholders': []
            },
            'content': {},
            'content_type': 'branding',
            'extras': {}
        }
        slides.append(branding_slide)
        slide_offset = 1

    plan = {
        '_meta': {
            'type': 'slide_layout_plan',
            'version': '1.3',
            'template': config.template_name,
            'slide_count': result['count'] + slide_offset,
            'warnings': result.get('warnings', []),
            'notes': f'{config.template_name} template.' + (
                ' Slide 1 is mandatory branding slide. All outline slides offset by +1.'
                if config.requires_branding_slide else ''
            )
        },
        'template_summary': {
            'max_body': 10,
            'max_picture': 12,
            'layout_count': 60
        },
        'slides': slides
    }
    
    # Add content slides with offset numbering
    for slide in result['slides']:
        slide_plan = {
            'slide_number': slide['outline_number'],  # Use outline numbers directly (explicit headers already correct)
            'layout': {
                'name': slide['layout']['name'],
                'index': slide['layout']['index'],
                'match_type': slide['layout']['match_type'],
                'signature': build_signature_string(slide),
                'placeholders': []
            },
            'content': {
                'title': slide.get('title'),
                'subtitle': slide.get('subtitle'),
                'headline': slide.get('headline'),
                'body': slide.get('body', []),
                'body_count': len(slide.get('body', [])),
                'metadata': slide.get('metadata', [])
            },
            'content_type': slide.get('content_type'),
            'extras': {}
        }
        
        # Add optional content to extras
        if slide.get('has_table'):
            slide_plan['extras']['table'] = slide.get('table')
        if slide.get('has_quote'):
            slide_plan['extras']['quote'] = slide.get('quote')
            if slide.get('quote_attribution'):
                slide_plan['extras']['quote_attribution'] = slide.get('quote_attribution')
        if slide.get('callout'):
            slide_plan['extras']['callout'] = slide.get('callout')
        if slide.get('has_image'):
            slide_plan['extras']['image_count'] = slide.get('image_count', 1)
        if slide.get('visual_type'):
            slide_plan['visual_type'] = slide.get('visual_type')
        if slide.get('columns'):
            slide_plan['columns'] = slide.get('columns')
        if slide.get('cards'):
            slide_plan['cards'] = slide.get('cards')
        if slide.get('timeline'):
            slide_plan['timeline'] = slide.get('timeline')
        if slide.get('pricing'):
            slide_plan['pricing'] = slide.get('pricing')
        if slide.get('contact'):
            slide_plan['extras']['contact'] = slide.get('contact')
        if slide.get('table_blocks'):
            slide_plan['table_blocks'] = slide.get('table_blocks')
        if slide.get('background'):
            slide_plan['background'] = slide.get('background')
        if slide.get('data_contrast'):
            slide_plan['data_contrast'] = slide.get('data_contrast')
        if slide.get('trailing_note'):
            slide_plan['trailing_note'] = slide.get('trailing_note')
        if slide.get('leading_text'):
            slide_plan['leading_text'] = slide.get('leading_text')

        # Typography styled runs (for generator to apply colors, sizes, etc.)
        if slide.get('title_styled'):
            slide_plan['title_styled'] = slide.get('title_styled')
        if slide.get('headline_styled'):
            slide_plan['headline_styled'] = slide.get('headline_styled')
        if slide.get('headline_bold'):
            slide_plan['headline_bold'] = slide.get('headline_bold')
        if slide.get('body_styled'):
            slide_plan['body_styled'] = slide.get('body_styled')
        if slide.get('quote_styled'):
            slide_plan['quote_styled'] = slide.get('quote_styled')

        plan['slides'].append(slide_plan)
    
    return plan


def build_signature_string(slide: dict) -> str:
    """Build a signature string for reference (not used for matching)."""
    parts = []
    if slide.get('body'):
        parts.append(f"body:{len(slide['body'])}")
    if slide.get('has_table') and slide.get('table'):
        parts.append(f"table:{slide['table'].get('column_count', 0)}x{slide['table'].get('row_count', 0)}")
    if slide.get('title'):
        parts.append('title:1')
    parts.append('slide_number:1')
    return '_'.join(sorted(parts)) if parts else 'empty'


def recover_skipped_slides(skipped_slides: list) -> list:
    """Attempt to recover skipped slides with default layouts.

    Creates basic slide entries using a default 'bullets' layout.
    The raw content is preserved in a body field for manual review.

    Args:
        skipped_slides: List of SkippedSlide objects

    Returns:
        List of recovered slide dicts that can be added to the plan
    """
    recovered = []

    for skip in skipped_slides:
        # Extract title from raw content if possible
        title = f"Recovered Slide {skip.original_slide_number}"
        lines = skip.raw_content.split('\n')
        for line in lines:
            # Look for heading-style lines
            if line.startswith('# Slide'):
                # Extract title after the colon
                parts = line.split(':', 1)
                if len(parts) > 1:
                    title = parts[1].strip()
                    break
            elif line.startswith('## ') or line.startswith('### '):
                title = line.lstrip('#').strip()
                break

        # Create a basic slide with the raw content as body
        body_lines = []
        for line in lines:
            clean_line = line.strip()
            if clean_line and not clean_line.startswith('#'):
                body_lines.append(clean_line)

        recovered_slide = {
            'slide_number': skip.original_slide_number,
            'content_type': 'content',
            'visual_type': 'bullets',
            'layout': {
                'name': 'content-image-right-a',
                'index': 3  # Default content layout
            },
            'content': {
                'title': title,
                'body': body_lines[:10] if body_lines else ['[Content could not be parsed - see _recovery section]']
            },
            '_recovered': True,
            '_original_error': skip.error
        }

        recovered.append(recovered_slide)

    return recovered


def validate_content_completeness(plan: dict) -> list:
    """Validate that the layout plan has complete content for each slide type.

    Returns list of issue strings. Empty list means validation passed.
    """
    issues = []

    for slide in plan.get('slides', []):
        slide_num = slide.get('slide_number', '?')
        content_type = slide.get('content_type', 'unknown')
        visual_type = slide.get('visual_type', '')
        content = slide.get('content', {})

        # Title slides need a title
        if content_type == 'title_slide':
            if not content.get('title'):
                issues.append(f"Slide {slide_num}: title_slide missing title")

        # Content slides need some content (title OR body)
        elif content_type == 'content':
            if not content.get('title') and not content.get('body') and not content.get('headline'):
                issues.append(f"Slide {slide_num}: content slide has no title, headline, or body")

        # Column visuals need columns
        elif content_type in ('framework', 'comparison'):
            columns = slide.get('columns', [])
            if not columns:
                issues.append(f"Slide {slide_num}: {content_type} slide missing columns array")
            else:
                # Check visual type matches column count
                if visual_type:
                    expected_cols = None
                    if 'process-2' in visual_type or 'comparison-2' in visual_type or 'cards-2' in visual_type:
                        expected_cols = 2
                    elif 'process-3' in visual_type or 'comparison-3' in visual_type or 'cards-3' in visual_type:
                        expected_cols = 3
                    elif 'process-4' in visual_type or 'comparison-4' in visual_type or 'cards-4' in visual_type:
                        expected_cols = 4
                    elif 'process-5' in visual_type or 'comparison-5' in visual_type or 'cards-5' in visual_type:
                        expected_cols = 5

                    if expected_cols and len(columns) != expected_cols:
                        issues.append(
                            f"Slide {slide_num}: visual_type '{visual_type}' expects {expected_cols} columns, got {len(columns)}"
                        )

        # Timeline visuals need timeline entries (stored in content.body or extras.timeline)
        elif content_type == 'timeline':
            extras = slide.get('extras', {})
            timeline = extras.get('timeline', []) or content.get('body', [])
            if not timeline:
                issues.append(f"Slide {slide_num}: timeline slide missing timeline entries")

        # Deliverables need deliverable items
        elif content_type == 'deliverables':
            deliverables = slide.get('deliverables', []) or content.get('deliverables', [])
            # Also check body for object array pattern
            body = content.get('body', [])
            has_deliverables = deliverables or (
                body and isinstance(body, list) and body and isinstance(body[0], dict)
            )
            if not has_deliverables:
                issues.append(f"Slide {slide_num}: deliverables slide missing deliverable items")

        # Table slides need table data (in extras.table or content.table)
        elif content_type in ('table', 'table_with_image', 'comparison_tables'):
            extras = slide.get('extras', {})
            tables = slide.get('tables', []) or slide.get('table_blocks', [])
            table = extras.get('table') or content.get('table') or content.get('tables')
            if not tables and not table:
                issues.append(f"Slide {slide_num}: {content_type} slide missing table data")

        # Contact slides need contact info (in extras.contact or content.contact)
        elif content_type == 'contact':
            extras = slide.get('extras', {})
            contact = extras.get('contact', {}) or content.get('contact', {})
            if not contact.get('name') and not contact.get('email'):
                issues.append(f"Slide {slide_num}: contact slide missing contact information")

    return issues


def main():
    parser = argparse.ArgumentParser(
        description='Parse outline and generate layout plan (template-agnostic with --config)'
    )
    parser.add_argument('input', help='Input outline file (markdown)')
    parser.add_argument('--output', '-o',
                        help='Output JSON file (default: {input_stem}.json)')
    parser.add_argument('--config', '-c',
                        help='Template config JSON file (for template-agnostic generation)')
    parser.add_argument('--pretty', action='store_true', default=True,
                        help='Pretty-print JSON output (default: True)')
    parser.add_argument('--compact', action='store_true',
                        help='Compact JSON output')
    parser.add_argument('--strict', action='store_true',
                        help='Exit on any parsing warnings (not just errors)')
    parser.add_argument('--validate-only', action='store_true',
                        help='Validate outline without writing output file')
    parser.add_argument('--include-recovered', action='store_true',
                        help='Include recovered malformed slides with default layouts')
    parser.add_argument('--perf', action='store_true',
                        help='Enable performance instrumentation and report timing')
    parser.add_argument('--perf-json', type=str, metavar='FILE',
                        help='Write performance data to JSON file')

    args = parser.parse_args()

    # Initialize performance context if requested
    perf_ctx = None
    if (args.perf or args.perf_json) and _HAS_PERFORMANCE:
        perf_ctx = PerfContext("ingest")
        perf_ctx.start()

    # Load template config if provided
    if args.config:
        if perf_ctx:
            config_timer = PerfTimer("load_config").start()
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: Config file not found: {args.config}", file=sys.stderr)
            sys.exit(1)
        try:
            config_data = json.loads(config_path.read_text(encoding='utf-8'))
            if _HAS_TEMPLATE_CONFIG:
                config = TemplateConfig.model_validate(config_data)
                set_layout_config(config)
                print(f"Using template config: {config.template_name}", file=sys.stderr)
            else:
                print("Warning: TemplateConfig not available, using IC defaults", file=sys.stderr)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            sys.exit(1)
        if perf_ctx:
            config_timer.stop()
            perf_ctx.record_phase("load_config", config_timer.duration_ms)

    # Determine output path
    output_path = args.output
    if not output_path:
        input_stem = Path(args.input).stem
        # Remove any existing suffix like '-outline'
        if input_stem.endswith('-outline'):
            input_stem = input_stem[:-8]
        output_path = f"{input_stem}.json"

    # Load outline
    if perf_ctx:
        with perf_ctx.phase("load_outline"):
            try:
                outline = Path(args.input).read_text(encoding='utf-8')
            except FileNotFoundError:
                print(f"Error: Input file not found: {args.input}", file=sys.stderr)
                sys.exit(1)
            except IOError as e:
                print(f"Error reading input file: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        try:
            outline = Path(args.input).read_text(encoding='utf-8')
        except FileNotFoundError:
            print(f"Error: Input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        except IOError as e:
            print(f"Error reading input file: {e}", file=sys.stderr)
            sys.exit(1)

    # Process with hardened parser
    if perf_ctx:
        with perf_ctx.phase("parse_outline"):
            result, ctx = process_outline(outline)
    else:
        result, ctx = process_outline(outline)

    # Report parsing issues
    if ctx.issues:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Parsing Report for: {args.input}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(ctx.report(), file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

    # Check for fatal errors
    if ctx.has_fatal_errors:
        print(f"Aborting: {len(ctx.errors)} fatal error(s) found.", file=sys.stderr)
        sys.exit(1)

    # In strict mode, warnings also cause exit
    if args.strict and ctx.warnings:
        print(f"Aborting (--strict mode): {len(ctx.warnings)} warning(s) found.", file=sys.stderr)
        sys.exit(1)

    # Generate layout plan
    if perf_ctx:
        with perf_ctx.phase("generate_layout_plan"):
            plan = generate_layout_plan(result)
    else:
        plan = generate_layout_plan(result)

    # Add parsing issues to plan metadata (as strings per schema)
    plan['_meta']['parsing_issues'] = [
        f"{'ERROR' if i.is_fatal else 'Warning'} at Line {i.line_num}: {i.message}"
        for i in ctx.issues
    ]

    # Add recovery section if there are skipped slides
    if ctx.skipped_slides:
        plan['_recovery'] = {
            'malformed_slides': [s.to_dict() for s in ctx.skipped_slides]
        }

        # Report skipped slides
        print(f"\nRecovery info: {len(ctx.skipped_slides)} slide(s) were skipped due to errors", file=sys.stderr)
        for s in ctx.skipped_slides:
            print(f"  Slide {s.original_slide_number}: {s.error[:60]}...", file=sys.stderr)
            if s.suggested_fix:
                print(f"    Suggested fix: {s.suggested_fix}", file=sys.stderr)

        # Handle --include-recovered flag
        if args.include_recovered:
            print(f"\nAttempting to recover {len(ctx.skipped_slides)} slide(s) with default layout...", file=sys.stderr)
            recovered_slides = recover_skipped_slides(ctx.skipped_slides)
            if recovered_slides:
                # Insert recovered slides at their original positions
                # (simplified: append to end for now, could be improved)
                plan['slides'].extend(recovered_slides)
                plan['_meta']['slide_count'] = len(plan['slides'])
                plan['_meta']['recovered_slide_count'] = len(recovered_slides)
                print(f"  Recovered {len(recovered_slides)} slide(s) with default 'bullets' layout", file=sys.stderr)

    # Content completeness validation
    if perf_ctx:
        with perf_ctx.phase("validate_content"):
            validation_issues = validate_content_completeness(plan)
    else:
        validation_issues = validate_content_completeness(plan)
    if validation_issues:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Content Validation Issues:", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        for issue in validation_issues:
            print(f"  - {issue}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        if args.validate_only:
            print(f"Validation failed: {len(validation_issues)} content issue(s) found.", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Warning: {len(validation_issues)} content issue(s) found. Proceeding anyway.", file=sys.stderr)

    # If validate-only mode, stop here
    if args.validate_only:
        print(f"\nValidation passed.", file=sys.stderr)
        print(f"  Slides: {plan['_meta']['slide_count']} (including branding)", file=sys.stderr)
        if ctx.warnings:
            print(f"  Warnings: {len(ctx.warnings)} (non-fatal)", file=sys.stderr)
        else:
            print(f"  Parsed cleanly with no warnings", file=sys.stderr)
        sys.exit(0)

    # Format output
    if perf_ctx:
        with perf_ctx.phase("serialize_json"):
            indent = None if args.compact else 2
            output = json.dumps(plan, indent=indent, ensure_ascii=False)
    else:
        indent = None if args.compact else 2
        output = json.dumps(plan, indent=indent, ensure_ascii=False)

    # Write file
    if perf_ctx:
        with perf_ctx.phase("write_output"):
            Path(output_path).write_text(output, encoding='utf-8')
    else:
        Path(output_path).write_text(output, encoding='utf-8')

    # Report success
    print(f"Created: {output_path}", file=sys.stderr)
    print(f"  Slides: {plan['_meta']['slide_count']} (including branding)", file=sys.stderr)

    # Summarize parsing stats
    if ctx.warnings:
        print(f"  Warnings: {len(ctx.warnings)} (see above)", file=sys.stderr)
    else:
        print(f"  Parsed cleanly with no warnings", file=sys.stderr)

    # Print summary table
    print(f"\nLayout Plan:", file=sys.stderr)
    print(f"{'Slide':<6} {'Title':<35} {'Layout':<25} {'Type':<15}", file=sys.stderr)
    print("-" * 85, file=sys.stderr)

    for slide in plan['slides']:
        title = (slide['content'].get('title') or slide['content_type'])[:33]
        layout = slide['layout']['name'][:23]
        content_type = slide['content_type'][:13]
        print(f"{slide['slide_number']:<6} {title:<35} {layout:<25} {content_type:<15}", file=sys.stderr)

    # Performance report
    if perf_ctx:
        perf_ctx.stop()
        report = perf_ctx.get_report()

        # Print performance summary
        if args.perf:
            print(f"\n{'='*60}", file=sys.stderr)
            print(report.format_summary(), file=sys.stderr)

            # Identify bottlenecks
            bottlenecks = identify_bottlenecks(report)
            if bottlenecks:
                print(f"\nIdentified Bottlenecks:", file=sys.stderr)
                for b in bottlenecks:
                    print(f"  - {b}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)

        # Write performance JSON if requested
        if args.perf_json:
            perf_data = report.to_dict()
            Path(args.perf_json).write_text(
                json.dumps(perf_data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            print(f"Performance data written to: {args.perf_json}", file=sys.stderr)


if __name__ == '__main__':
    main()