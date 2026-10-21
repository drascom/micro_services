#!/usr/bin/env python3
"""
Script: Normalize Markdown
Purpose: Clean and normalize Docling Markdown output for LLM consumption
Usage: python execution/normalize_markdown.py <markdown_text>

Inputs:
    - markdown_text: Raw Markdown from Docling (via stdin or argument)

Outputs:
    - JSON object with status and cleaned text
"""

import re
import html
import json
import sys
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class NormalizationResult:
    """Result of markdown normalization."""
    status: str
    cleaned_text: Optional[str] = None
    original_length: int = 0
    cleaned_length: int = 0
    message: Optional[str] = None


# Checkbox patterns to preserve semantic meaning
CHECKBOX_PATTERNS = [
    # Unicode checkboxes
    (r"☑", "[CHECKED]"),
    (r"☐", "[UNCHECKED]"),
    (r"✓", "[CHECKED]"),
    (r"✔", "[CHECKED]"),
    (r"✗", "[UNCHECKED]"),
    (r"✘", "[UNCHECKED]"),
    (r"□", "[UNCHECKED]"),
    (r"■", "[CHECKED]"),

    # Markdown checkboxes
    (r"\[x\]", "[CHECKED]"),
    (r"\[X\]", "[CHECKED]"),
    (r"\[ \]", "[UNCHECKED]"),
    (r"\[\]", "[UNCHECKED]"),

    # Text-based patterns (preserve context)
    (r"\bYes\s*☑", "Yes: [CHECKED]"),
    (r"\bNo\s*☑", "No: [CHECKED]"),
    (r"\bYes\s*☐", "Yes: [UNCHECKED]"),
    (r"\bNo\s*☐", "No: [UNCHECKED]"),
]


def normalize_markdown(markdown_text: str) -> NormalizationResult:
    """
    Clean and normalize Docling Markdown for LLM consumption.

    Processing steps:
    1. Preserve checkbox semantics
    2. Decode HTML entities
    3. Remove markdown headers (# symbols)
    4. Remove table pipes and formatting
    5. Remove bold/italic markers
    6. Normalize whitespace
    7. Remove image placeholders (but note their presence)

    Args:
        markdown_text: Raw Markdown from Docling

    Returns:
        NormalizationResult with cleaned text
    """
    if not markdown_text:
        return NormalizationResult(
            status="error",
            message="Empty input text"
        )

    original_length = len(markdown_text)
    text = markdown_text

    try:
        # Step 1: Preserve checkbox semantics FIRST (before other processing)
        for pattern, replacement in CHECKBOX_PATTERNS:
            text = re.sub(pattern, replacement, text)

        # Step 2: Decode HTML entities
        text = html.unescape(text)

        # Step 3: Remove markdown header markers but keep the text
        # Convert "## Header" to "Header:"
        text = re.sub(r"^#{1,6}\s*(.+)$", r"\1:", text, flags=re.MULTILINE)

        # Step 4: Process tables - convert pipe tables to readable format
        # First, handle table rows by replacing pipes with spaces/tabs
        text = re.sub(r"\|", " ", text)

        # Remove table separator lines (---+---)
        text = re.sub(r"^[\s\-:]+$", "", text, flags=re.MULTILINE)

        # Step 5: Remove bold/italic markers but keep text
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)  # Bold+italic
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # Bold
        text = re.sub(r"\*(.+?)\*", r"\1", text)  # Italic
        text = re.sub(r"___(.+?)___", r"\1", text)  # Bold+italic
        text = re.sub(r"__(.+?)__", r"\1", text)  # Bold
        text = re.sub(r"_(.+?)_", r"\1", text)  # Italic

        # Step 6: Remove code block markers
        text = re.sub(r"```[\w]*\n?", "", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)

        # Step 7: Handle image placeholders
        # Docling uses placeholder mode, so images become references
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[Image: \1]", text)
        text = re.sub(r"\[IMAGE_PLACEHOLDER[^\]]*\]", "[Image placeholder]", text)

        # Step 8: Remove link formatting but keep text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

        # Step 9: Remove horizontal rules
        text = re.sub(r"^[-*_]{3,}$", "", text, flags=re.MULTILINE)

        # Step 10: Normalize list markers
        text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)

        # Step 11: Normalize whitespace
        # Replace multiple spaces with single space
        text = re.sub(r"[ \t]+", " ", text)

        # Replace 3+ newlines with 2 newlines
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Trim whitespace from each line
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)

        # Remove empty lines at start and end
        text = text.strip()

        # Step 12: Remove any remaining artifacts
        # Remove standalone punctuation lines
        text = re.sub(r"^\s*[.,;:]+\s*$", "", text, flags=re.MULTILINE)

        # Final cleanup of excessive newlines created by removals
        text = re.sub(r"\n{3,}", "\n\n", text)

        return NormalizationResult(
            status="success",
            cleaned_text=text,
            original_length=original_length,
            cleaned_length=len(text)
        )

    except Exception as e:
        return NormalizationResult(
            status="error",
            original_length=original_length,
            message=f"Normalization error: {str(e)}"
        )


def normalize_for_llm(markdown_text: str) -> str:
    """
    Convenience function that returns just the cleaned text.
    Raises exception on error.
    """
    result = normalize_markdown(markdown_text)
    if result.status == "error":
        raise ValueError(result.message)
    return result.cleaned_text or ""


def main():
    """Main entry point."""
    # Read from stdin if no argument provided
    if len(sys.argv) < 2:
        if sys.stdin.isatty():
            print(json.dumps({
                "status": "error",
                "message": "Usage: python normalize_markdown.py <markdown_text> or pipe via stdin"
            }))
            sys.exit(1)
        markdown_text = sys.stdin.read()
    else:
        markdown_text = sys.argv[1]

    result = normalize_markdown(markdown_text)
    print(json.dumps(asdict(result), indent=2))

    if result.status == "error":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
