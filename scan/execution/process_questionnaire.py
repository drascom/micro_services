#!/usr/bin/env python3
"""
Script: Process Medical Questionnaire
Purpose: Main orchestrator - fetches email, extracts PDF text, extracts medical data
Usage: python execution/process_questionnaire.py <email_uid>

This is the primary entry point for the medical questionnaire processing pipeline.

Inputs:
    - email_uid: The UID of the email containing questionnaire PDF

Outputs:
    - JSON object with extracted medical data or status message
"""

import json
import sys
import base64
from typing import Optional
from dataclasses import dataclass, asdict

from config import (
    validate_config,
    is_questionnaire_filename,
    get_pdf_config,
    PDFExtractionMethod
)
from fetch_email_attachments import fetch_attachments
from extract_pdf_text import extract_from_bytes, ExtractionMethod
from convert_pdf_docling import convert_pdf_to_markdown
from normalize_markdown import normalize_for_llm
from extract_medical_data import extract_medical_data


@dataclass
class ProcessingResult:
    """Final result of the questionnaire processing pipeline."""
    status: str
    email_uid: str
    source_filename: Optional[str] = None
    data: Optional[dict] = None
    message: Optional[str] = None


def extract_text_from_pdf(pdf_content: bytes, filename: str) -> tuple[str, Optional[str]]:
    """
    Extract text from PDF using configured method.

    Returns:
        Tuple of (extracted_text, error_message)
    """
    pdf_config = get_pdf_config()

    if pdf_config.method == PDFExtractionMethod.PYMUPDF:
        result = extract_from_bytes(pdf_content, ExtractionMethod.PYMUPDF)
        if result.status == "success":
            return result.text or "", None
        return "", result.message

    elif pdf_config.method == PDFExtractionMethod.PDFPLUMBER:
        result = extract_from_bytes(pdf_content, ExtractionMethod.PDFPLUMBER)
        if result.status == "success":
            return result.text or "", None
        return "", result.message

    elif pdf_config.method == PDFExtractionMethod.DOCLING:
        # Use Docling server
        result = convert_pdf_to_markdown(pdf_content, filename)
        if result.status == "success":
            # Normalize the markdown output
            try:
                cleaned = normalize_for_llm(result.markdown or "")
                return cleaned, None
            except ValueError as e:
                return "", str(e)
        return "", result.message

    else:
        return "", f"Unknown PDF extraction method: {pdf_config.method}"


def process_questionnaire(email_uid: str) -> ProcessingResult:
    """
    Main processing pipeline for medical questionnaire.

    Steps:
    1. Fetch email attachments
    2. Find relevant questionnaire PDF
    3. Extract text from PDF (using configured method)
    4. Normalize text for LLM
    5. Extract structured data
    6. Validate and return

    Args:
        email_uid: Email UID to process

    Returns:
        ProcessingResult with extracted data or status
    """
    # Step 0: Validate configuration
    config_errors = validate_config()
    if config_errors:
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            message=f"Configuration errors: {'; '.join(config_errors)}"
        )

    # Step 1: Fetch email attachments
    fetch_result = fetch_attachments(email_uid)

    if fetch_result.status == "error":
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            message=fetch_result.message
        )

    if fetch_result.status == "not_found":
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            message=f"Email with UID {email_uid} not found"
        )

    # Step 2: Find relevant questionnaire PDF
    if not fetch_result.attachments:
        return ProcessingResult(
            status="no_actionable_document",
            email_uid=email_uid,
            message="No PDF attachments found in email"
        )

    # Prefer attachments flagged as questionnaires
    questionnaire_pdfs = [a for a in fetch_result.attachments if a.get("is_questionnaire")]

    if questionnaire_pdfs:
        selected_pdf = questionnaire_pdfs[0]
    else:
        # Fall back to first PDF if no questionnaire-named file found
        selected_pdf = fetch_result.attachments[0]

    filename = selected_pdf["filename"]
    pdf_content_b64 = selected_pdf["content_base64"]

    # Step 3: Decode PDF content
    try:
        pdf_content = base64.b64decode(pdf_content_b64)
    except Exception as e:
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            source_filename=filename,
            message=f"Failed to decode PDF content: {str(e)}"
        )

    # Step 4: Extract text from PDF
    extracted_text, extraction_error = extract_text_from_pdf(pdf_content, filename)

    if extraction_error:
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            source_filename=filename,
            message=f"PDF text extraction failed: {extraction_error}"
        )

    if not extracted_text or len(extracted_text.strip()) < 50:
        return ProcessingResult(
            status="no_actionable_document",
            email_uid=email_uid,
            source_filename=filename,
            message="Document contains insufficient text content"
        )

    # Step 5: Normalize text for LLM (for local extraction methods)
    pdf_config = get_pdf_config()
    if pdf_config.method != PDFExtractionMethod.DOCLING:
        # Local extraction gives plain text, normalize it
        try:
            cleaned_text = normalize_for_llm(extracted_text)
        except ValueError as e:
            return ProcessingResult(
                status="error",
                email_uid=email_uid,
                source_filename=filename,
                message=f"Text normalization failed: {str(e)}"
            )
    else:
        # Docling output already normalized in extract_text_from_pdf
        cleaned_text = extracted_text

    # Step 6: Extract medical data via LLM
    extraction_result = extract_medical_data(cleaned_text)

    if extraction_result.status == "error":
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            source_filename=filename,
            message=f"Data extraction failed: {extraction_result.message}"
        )

    # Step 7: Return result
    extracted_data = extraction_result.data or {}

    # Add metadata
    extracted_data["_metadata"] = {
        "email_uid": email_uid,
        "source_filename": filename,
        "extraction_method": pdf_config.method.value,
        "is_valid": extraction_result.is_valid,
        "validation_errors": extraction_result.validation_errors
    }

    return ProcessingResult(
        status="success",
        email_uid=email_uid,
        source_filename=filename,
        data=extracted_data
    )


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "message": "Usage: python process_questionnaire.py <email_uid>"
        }))
        sys.exit(1)

    email_uid = sys.argv[1]
    result = process_questionnaire(email_uid)

    # Format output based on result status
    if result.status == "success" and result.data:
        # Output the extracted data directly (matches expected schema)
        # Remove internal metadata for clean output
        output = {k: v for k, v in result.data.items() if not k.startswith("_")}
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif result.status == "no_actionable_document":
        print(json.dumps({
            "status": "no_actionable_document",
            "message": result.message
        }, indent=2))
    else:
        # Error case
        print(json.dumps({
            "status": "error",
            "message": result.message
        }, indent=2))

    # Exit codes
    if result.status == "error":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
