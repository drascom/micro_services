#!/usr/bin/env python3
"""
Script: Convert PDF via Docling
Purpose: Send PDF to Docling document conversion server and retrieve Markdown output
Usage: python execution/convert_pdf_docling.py <pdf_path_or_base64>

Inputs:
    - pdf_input: Either a file path to a PDF, or base64-encoded PDF content
    - --base64: Flag indicating input is base64 encoded

Outputs:
    - JSON object with status and converted Markdown content
"""

import requests
import base64
import json
import sys
import os
import tempfile
from typing import Optional
from dataclasses import dataclass, asdict

from config import get_pdf_config, PDFConfig


@dataclass
class ConversionResult:
    """Result of Docling PDF conversion."""
    status: str
    markdown: Optional[str] = None
    source_filename: Optional[str] = None
    message: Optional[str] = None


def convert_pdf_to_markdown(
    pdf_content: bytes,
    filename: str = "document.pdf",
    config: Optional[PDFConfig] = None,
    retry_count: int = 1
) -> ConversionResult:
    """
    Convert PDF to Markdown using Docling service.

    Args:
        pdf_content: Raw PDF bytes
        filename: Original filename for the PDF
        config: PDF configuration (uses default if None)
        retry_count: Number of retries on failure

    Returns:
        ConversionResult with status and Markdown content
    """
    if config is None:
        config = get_pdf_config()

    if not config.docling_url:
        return ConversionResult(
            status="error",
            message="DOCLING_URL is not configured"
        )

    # Prepare multipart form data
    files = {
        "files": (filename, pdf_content, "application/pdf")
    }

    data = {
        "to_formats": "md",
        "image_export_mode": "placeholder"
    }

    last_error = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.post(
                config.docling_url,
                files=files,
                data=data,
                timeout=config.docling_timeout
            )

            if response.status_code == 200:
                # Docling returns the conversion result
                # The response format may vary; handle common patterns
                content_type = response.headers.get("Content-Type", "")

                if "application/json" in content_type:
                    # JSON response containing markdown
                    result = response.json()

                    # Handle various Docling response formats
                    if isinstance(result, dict):
                        # Check for markdown in common keys
                        markdown = (
                            result.get("markdown") or
                            result.get("md") or
                            result.get("content") or
                            result.get("text") or
                            result.get("result")
                        )

                        # If result contains a documents array
                        if not markdown and "documents" in result:
                            docs = result["documents"]
                            if docs and len(docs) > 0:
                                markdown = docs[0].get("markdown") or docs[0].get("md")

                        if markdown:
                            return ConversionResult(
                                status="success",
                                markdown=markdown,
                                source_filename=filename
                            )

                    elif isinstance(result, str):
                        return ConversionResult(
                            status="success",
                            markdown=result,
                            source_filename=filename
                        )

                    # If we can't find markdown in expected places, dump the full response
                    return ConversionResult(
                        status="error",
                        message=f"Unexpected Docling response format: {json.dumps(result)[:500]}"
                    )

                elif "text/markdown" in content_type or "text/plain" in content_type:
                    # Direct markdown response
                    return ConversionResult(
                        status="success",
                        markdown=response.text,
                        source_filename=filename
                    )

                else:
                    # Try to parse as text/markdown anyway
                    text = response.text
                    if text and len(text) > 10:
                        return ConversionResult(
                            status="success",
                            markdown=text,
                            source_filename=filename
                        )

                    return ConversionResult(
                        status="error",
                        message=f"Unexpected content type: {content_type}"
                    )

            elif response.status_code == 422:
                # Validation error
                return ConversionResult(
                    status="error",
                    message=f"Docling validation error: {response.text[:500]}"
                )

            elif response.status_code >= 500:
                # Server error - retry
                last_error = f"Docling server error ({response.status_code}): {response.text[:200]}"
                continue

            else:
                return ConversionResult(
                    status="error",
                    message=f"Docling error ({response.status_code}): {response.text[:500]}"
                )

        except requests.Timeout:
            last_error = f"Docling request timed out after {config.timeout}s"
            continue

        except requests.ConnectionError as e:
            last_error = f"Cannot connect to Docling server at {config.url}: {str(e)}"
            continue

        except Exception as e:
            last_error = f"Docling conversion error: {str(e)}"
            continue

    # All retries exhausted
    return ConversionResult(
        status="error",
        message=last_error or "Conversion failed after retries"
    )


def convert_from_file(file_path: str) -> ConversionResult:
    """Convert a PDF file to Markdown."""
    if not os.path.exists(file_path):
        return ConversionResult(
            status="error",
            message=f"File not found: {file_path}"
        )

    try:
        with open(file_path, "rb") as f:
            pdf_content = f.read()
    except Exception as e:
        return ConversionResult(
            status="error",
            message=f"Error reading file: {str(e)}"
        )

    filename = os.path.basename(file_path)
    return convert_pdf_to_markdown(pdf_content, filename)


def convert_from_base64(b64_content: str, filename: str = "document.pdf") -> ConversionResult:
    """Convert a base64-encoded PDF to Markdown."""
    try:
        pdf_content = base64.b64decode(b64_content)
    except Exception as e:
        return ConversionResult(
            status="error",
            message=f"Invalid base64 content: {str(e)}"
        )

    return convert_pdf_to_markdown(pdf_content, filename)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "message": "Usage: python convert_pdf_docling.py <pdf_path> [--base64] [--filename <name>]"
        }))
        sys.exit(1)

    pdf_input = sys.argv[1]
    is_base64 = "--base64" in sys.argv
    filename = "document.pdf"

    # Parse optional filename
    if "--filename" in sys.argv:
        idx = sys.argv.index("--filename")
        if idx + 1 < len(sys.argv):
            filename = sys.argv[idx + 1]

    if is_base64:
        result = convert_from_base64(pdf_input, filename)
    else:
        result = convert_from_file(pdf_input)

    print(json.dumps(asdict(result), indent=2))

    if result.status == "error":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
