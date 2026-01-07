#!/usr/bin/env python3
"""
Script: Extract PDF Text
Purpose: Extract text from digitally-generated PDFs using local libraries
Usage: python execution/extract_pdf_text.py <pdf_path> [--method pymupdf|pdfplumber]

Supports multiple extraction methods:
- pymupdf (fitz): Fastest, good Unicode checkbox detection
- pdfplumber: Best for form-like layouts with positioning

Inputs:
    - pdf_path: Path to PDF file, or base64 content with --base64 flag
    - --method: Extraction method (default: pymupdf)

Outputs:
    - JSON object with status and extracted text
"""

import json
import sys
import os
import base64
import tempfile
from typing import Optional
from dataclasses import dataclass, asdict
from enum import Enum


class ExtractionMethod(Enum):
    PYMUPDF = "pymupdf"
    PDFPLUMBER = "pdfplumber"


@dataclass
class ExtractionResult:
    """Result of PDF text extraction."""
    status: str
    text: Optional[str] = None
    method: Optional[str] = None
    page_count: int = 0
    message: Optional[str] = None


def extract_with_pymupdf(pdf_path: str) -> ExtractionResult:
    """
    Extract text using PyMuPDF (fitz).

    Fastest method, excellent for:
    - Unicode checkbox symbols (✔, ☐, ✓, ☑)
    - Preserving reading order
    - Text block detection
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ExtractionResult(
            status="error",
            message="PyMuPDF not installed. Run: pip install PyMuPDF"
        )

    try:
        doc = fitz.open(pdf_path)
        text_parts = []

        for page_num, page in enumerate(doc):
            # Extract text with preserved layout
            # Using "text" mode for clean extraction
            page_text = page.get_text("text")

            if page_text.strip():
                text_parts.append(page_text)

        doc.close()

        full_text = "\n\n".join(text_parts)

        return ExtractionResult(
            status="success",
            text=full_text,
            method="pymupdf",
            page_count=len(text_parts)
        )

    except Exception as e:
        return ExtractionResult(
            status="error",
            method="pymupdf",
            message=f"PyMuPDF extraction failed: {str(e)}"
        )


def extract_with_pdfplumber(pdf_path: str) -> ExtractionResult:
    """
    Extract text using pdfplumber.

    Best for form-like layouts:
    - Precise character positioning
    - Table detection
    - Question-answer proximity matching
    """
    try:
        import pdfplumber
    except ImportError:
        return ExtractionResult(
            status="error",
            message="pdfplumber not installed. Run: pip install pdfplumber"
        )

    try:
        text_parts = []
        page_count = 0

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_count += 1

                # Extract text - pdfplumber handles layout well
                page_text = page.extract_text()

                if page_text and page_text.strip():
                    text_parts.append(page_text)

        full_text = "\n\n".join(text_parts)

        return ExtractionResult(
            status="success",
            text=full_text,
            method="pdfplumber",
            page_count=page_count
        )

    except Exception as e:
        return ExtractionResult(
            status="error",
            method="pdfplumber",
            message=f"pdfplumber extraction failed: {str(e)}"
        )


def extract_pdf_text(
    pdf_input: str,
    method: ExtractionMethod = ExtractionMethod.PYMUPDF,
    is_base64: bool = False
) -> ExtractionResult:
    """
    Extract text from a PDF file.

    Args:
        pdf_input: File path or base64-encoded content
        method: Extraction method to use
        is_base64: Whether pdf_input is base64-encoded content

    Returns:
        ExtractionResult with extracted text
    """
    pdf_path = pdf_input
    temp_file = None

    # Handle base64 input
    if is_base64:
        try:
            pdf_content = base64.b64decode(pdf_input)
            temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            temp_file.write(pdf_content)
            temp_file.close()
            pdf_path = temp_file.name
        except Exception as e:
            return ExtractionResult(
                status="error",
                message=f"Failed to decode base64 PDF: {str(e)}"
            )

    # Verify file exists
    if not os.path.exists(pdf_path):
        return ExtractionResult(
            status="error",
            message=f"PDF file not found: {pdf_path}"
        )

    try:
        # Extract based on method
        if method == ExtractionMethod.PYMUPDF:
            result = extract_with_pymupdf(pdf_path)
        elif method == ExtractionMethod.PDFPLUMBER:
            result = extract_with_pdfplumber(pdf_path)
        else:
            result = ExtractionResult(
                status="error",
                message=f"Unknown extraction method: {method}"
            )

        return result

    finally:
        # Cleanup temp file
        if temp_file and os.path.exists(temp_file.name):
            os.unlink(temp_file.name)


def extract_from_bytes(
    pdf_content: bytes,
    method: ExtractionMethod = ExtractionMethod.PYMUPDF
) -> ExtractionResult:
    """
    Extract text from PDF bytes directly.

    Args:
        pdf_content: Raw PDF bytes
        method: Extraction method to use

    Returns:
        ExtractionResult with extracted text
    """
    b64_content = base64.b64encode(pdf_content).decode("utf-8")
    return extract_pdf_text(b64_content, method=method, is_base64=True)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "message": "Usage: python extract_pdf_text.py <pdf_path> [--method pymupdf|pdfplumber] [--base64]"
        }))
        sys.exit(1)

    pdf_input = sys.argv[1]
    is_base64 = "--base64" in sys.argv

    # Parse method
    method = ExtractionMethod.PYMUPDF  # default
    if "--method" in sys.argv:
        idx = sys.argv.index("--method")
        if idx + 1 < len(sys.argv):
            method_str = sys.argv[idx + 1].lower()
            if method_str == "pdfplumber":
                method = ExtractionMethod.PDFPLUMBER
            elif method_str == "pymupdf":
                method = ExtractionMethod.PYMUPDF
            else:
                print(json.dumps({
                    "status": "error",
                    "message": f"Unknown method: {method_str}. Use 'pymupdf' or 'pdfplumber'"
                }))
                sys.exit(1)

    result = extract_pdf_text(pdf_input, method=method, is_base64=is_base64)
    print(json.dumps(asdict(result), indent=2))

    if result.status == "error":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
