#!/usr/bin/env python3
"""
Lightweight FastAPI app to process a questionnaire by email UID.
"""

import base64
import os
import secrets
import sys
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

# Ensure local scan folder is on sys.path for execution module imports
project_root = Path(__file__).resolve().parent
execution_root = project_root / "execution"
for path in (project_root, execution_root):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Load environment variables for IMAP/LLM configs
load_dotenv(project_root / ".env")

# Import local execution modules
from execution.config import (
    validate_config,
    get_pdf_config,
    PDFExtractionMethod,
)
from execution.fetch_email_attachments import fetch_attachments
from execution.extract_pdf_text import extract_from_bytes, ExtractionMethod
from execution.convert_pdf_docling import convert_pdf_to_markdown
from execution.normalize_markdown import normalize_for_llm
from execution.extract_medical_data import extract_medical_data


app = FastAPI(title="LivAuto Scan", version="1.0.0")
security = HTTPBasic()

# Load basic auth credentials from environment
BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "changeme123")


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify basic auth credentials."""
    username_correct = secrets.compare_digest(
        credentials.username.encode("utf8"), BASIC_AUTH_USERNAME.encode("utf8")
    )
    password_correct = secrets.compare_digest(
        credentials.password.encode("utf8"), BASIC_AUTH_PASSWORD.encode("utf8")
    )

    if not (username_correct and password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@dataclass
class ProcessingResult:
    status: str
    email_uid: str
    source_filename: Optional[str] = None
    data: Optional[dict] = None
    message: Optional[str] = None


def extract_text_from_pdf(pdf_content: bytes, filename: str) -> tuple[str, Optional[str]]:
    pdf_config = get_pdf_config()

    if pdf_config.method == PDFExtractionMethod.PYMUPDF:
        result = extract_from_bytes(pdf_content, ExtractionMethod.PYMUPDF)
        if result.status == "success":
            return result.text or "", None
        return "", result.message

    if pdf_config.method == PDFExtractionMethod.PDFPLUMBER:
        result = extract_from_bytes(pdf_content, ExtractionMethod.PDFPLUMBER)
        if result.status == "success":
            return result.text or "", None
        return "", result.message

    if pdf_config.method == PDFExtractionMethod.DOCLING:
        result = convert_pdf_to_markdown(pdf_content, filename)
        if result.status == "success":
            try:
                cleaned = normalize_for_llm(result.markdown or "")
                return cleaned, None
            except ValueError as exc:
                return "", str(exc)
        return "", result.message

    return "", f"Unknown PDF extraction method: {pdf_config.method}"


def process_questionnaire(email_uid: str) -> ProcessingResult:
    config_errors = validate_config()
    if config_errors:
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            message=f"Configuration errors: {'; '.join(config_errors)}",
        )

    fetch_result = fetch_attachments(email_uid)

    if fetch_result.status == "error":
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            message=fetch_result.message,
        )

    if fetch_result.status == "not_found":
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            message=f"Email with UID {email_uid} not found",
        )

    if not fetch_result.attachments:
        return ProcessingResult(
            status="no_actionable_document",
            email_uid=email_uid,
            message="No PDF attachments found in email",
        )

    questionnaire_pdfs = [a for a in fetch_result.attachments if a.get("is_questionnaire")]
    selected_pdf = questionnaire_pdfs[0] if questionnaire_pdfs else fetch_result.attachments[0]

    filename = selected_pdf["filename"]
    pdf_content_b64 = selected_pdf["content_base64"]

    try:
        pdf_content = base64.b64decode(pdf_content_b64)
    except Exception as exc:
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            source_filename=filename,
            message=f"Failed to decode PDF content: {str(exc)}",
        )

    extracted_text, extraction_error = extract_text_from_pdf(pdf_content, filename)

    if extraction_error:
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            source_filename=filename,
            message=f"PDF text extraction failed: {extraction_error}",
        )

    if not extracted_text or len(extracted_text.strip()) < 50:
        return ProcessingResult(
            status="no_actionable_document",
            email_uid=email_uid,
            source_filename=filename,
            message="Document contains insufficient text content",
        )

    pdf_config = get_pdf_config()
    if pdf_config.method != PDFExtractionMethod.DOCLING:
        try:
            cleaned_text = normalize_for_llm(extracted_text)
        except ValueError as exc:
            return ProcessingResult(
                status="error",
                email_uid=email_uid,
                source_filename=filename,
                message=f"Text normalization failed: {str(exc)}",
            )
    else:
        cleaned_text = extracted_text

    extraction_result = extract_medical_data(cleaned_text)

    if extraction_result.status == "error":
        return ProcessingResult(
            status="error",
            email_uid=email_uid,
            source_filename=filename,
            message=f"Data extraction failed: {extraction_result.message}",
        )

    extracted_data = extraction_result.data or {}
    extracted_data["_metadata"] = {
        "email_uid": email_uid,
        "source_filename": filename,
        "extraction_method": pdf_config.method.value,
        "is_valid": extraction_result.is_valid,
        "validation_errors": extraction_result.validation_errors,
    }

    return ProcessingResult(
        status="success",
        email_uid=email_uid,
        source_filename=filename,
        data=extracted_data,
    )


class ScanRequest(BaseModel):
    uid: str


HTML_PAGE = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>LivAuto Scan</title>
  <style>
    body {
      font-family: "Courier New", monospace;
      background: #f7f7f2;
      color: #1f2937;
      margin: 0;
      padding: 32px;
    }
    .wrap {
      max-width: 760px;
      margin: 0 auto;
    }
    h1 {
      font-size: 24px;
      margin-bottom: 16px;
    }
    .card {
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 20px;
      box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08);
      margin-bottom: 16px;
    }
    label {
      font-size: 13px;
      display: block;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    input {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid #cbd5f5;
      border-radius: 6px;
      font-size: 14px;
      margin-bottom: 12px;
      font-family: "Courier New", monospace;
      box-sizing: border-box;
    }
    input[type=\"password\"] {
      font-family: monospace;
    }
    button {
      background: #111827;
      color: #ffffff;
      border: none;
      border-radius: 6px;
      padding: 10px 16px;
      cursor: pointer;
      font-weight: 600;
    }
    button:disabled {
      background: #9ca3af;
      cursor: not-allowed;
    }
    pre {
      background: #0f172a;
      color: #e2e8f0;
      padding: 16px;
      border-radius: 8px;
      min-height: 220px;
      overflow: auto;
      margin-top: 16px;
      white-space: pre-wrap;
    }
    .auth-section {
      margin-bottom: 20px;
      padding-bottom: 20px;
      border-bottom: 1px solid #e5e7eb;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>LivAuto UID Scan</h1>
    <div class=\"card\">
      <div class=\"auth-section\">
        <div class=\"grid-2\">
          <div>
            <label for=\"username\">Username</label>
            <input id=\"username\" type=\"text\" placeholder=\"Enter username\" />
          </div>
          <div>
            <label for=\"password\">Password</label>
            <input id=\"password\" type=\"password\" placeholder=\"Enter password\" />
          </div>
        </div>
      </div>

      <label for=\"uid\">Email UID</label>
      <input id=\"uid\" placeholder=\"176\" />
      <button id=\"submitBtn\" onclick=\"runScan()\">Run Scan</button>
      <pre id=\"result\">Waiting for scan...</pre>
    </div>
  </div>

  <script>
    async function runScan() {
      const uid = document.getElementById('uid').value.trim();
      const username = document.getElementById('username').value.trim();
      const password = document.getElementById('password').value.trim();
      const resultEl = document.getElementById('result');
      const btn = document.getElementById('submitBtn');

      if (!uid) {
        resultEl.textContent = 'Please enter a UID.';
        return;
      }

      if (!username || !password) {
        resultEl.textContent = 'Please enter username and password.';
        return;
      }

      btn.disabled = true;
      resultEl.textContent = 'Scanning...';

      try {
        // Create Basic Auth header
        const credentials = btoa(`${username}:${password}`);

        const response = await fetch(`/scan?uid=${encodeURIComponent(uid)}`, {
          headers: {
            'Authorization': `Basic ${credentials}`
          }
        });

        const data = await response.json();

        if (!response.ok) {
          if (response.status === 401) {
            resultEl.textContent = JSON.stringify({ error: 'Authentication failed. Invalid username or password.' }, null, 2);
          } else {
            resultEl.textContent = JSON.stringify({ error: data.detail || data }, null, 2);
          }
          return;
        }

        resultEl.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        resultEl.textContent = JSON.stringify({ error: err.message }, null, 2);
      } finally {
        btn.disabled = false;
      }
    }
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home_page():
    return HTML_PAGE


@app.get("/scan")
def scan_uid(uid: str = Query(..., min_length=1), username: str = Depends(verify_credentials)):
    result = process_questionnaire(uid)
    return JSONResponse(content=asdict(result))


@app.post("/scan")
def scan_uid_post(payload: ScanRequest, username: str = Depends(verify_credentials)):
    result = process_questionnaire(payload.uid)
    return JSONResponse(content=asdict(result))


@app.get("/health")
def health_check():
    return {"status": "healthy"}
