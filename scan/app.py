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
    button.logout {
      background: #dc2626;
      font-size: 12px;
      padding: 6px 12px;
      margin-left: 8px;
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
    .header-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    .user-info {
      font-size: 13px;
      color: #6b7280;
    }
    .modal-overlay {
      display: none;
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.5);
      z-index: 1000;
      align-items: center;
      justify-content: center;
    }
    .modal-overlay.active {
      display: flex;
    }
    .modal {
      background: #ffffff;
      border-radius: 10px;
      padding: 32px;
      max-width: 400px;
      width: 90%;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
    }
    .modal h2 {
      margin-top: 0;
      font-size: 20px;
      margin-bottom: 20px;
    }
    .error-message {
      background: #fee2e2;
      color: #dc2626;
      padding: 10px;
      border-radius: 6px;
      margin-bottom: 12px;
      font-size: 12px;
      display: none;
    }
    .error-message.active {
      display: block;
    }
  </style>
</head>
<body>
  <!-- Login Modal -->
  <div id=\"loginModal\" class=\"modal-overlay active\">
    <div class=\"modal\">
      <h2>Login Required</h2>
      <div id=\"loginError\" class=\"error-message\"></div>
      <label for=\"modalUsername\">Username</label>
      <input id=\"modalUsername\" type=\"text\" placeholder=\"Enter username\" autofocus />
      <label for=\"modalPassword\">Password</label>
      <input id=\"modalPassword\" type=\"password\" placeholder=\"Enter password\" />
      <button onclick=\"handleLogin()\">Login</button>
    </div>
  </div>

  <div class=\"wrap\">
    <div class=\"header-bar\">
      <h1>LivAuto UID Scan</h1>
      <div class=\"user-info\">
        Logged in as: <strong id=\"currentUser\"></strong>
        <button class=\"logout\" onclick=\"handleLogout()\">Logout</button>
      </div>
    </div>
    <div class=\"card\">
      <label for=\"uid\">Email UID</label>
      <input id=\"uid\" placeholder=\"176\" />
      <button id=\"submitBtn\" onclick=\"runScan()\">Run Scan</button>
      <pre id=\"result\">Waiting for scan...</pre>
    </div>
  </div>

  <script>
    // Check for stored credentials on page load
    window.addEventListener('DOMContentLoaded', function() {
      const storedUsername = localStorage.getItem('auth_username');
      const storedPassword = localStorage.getItem('auth_password');

      if (storedUsername && storedPassword) {
        // Verify stored credentials
        verifyStoredCredentials(storedUsername, storedPassword);
      }
    });

    async function verifyStoredCredentials(username, password) {
      try {
        const credentials = btoa(`${username}:${password}`);
        const response = await fetch('/health');

        // For now, just trust stored credentials and hide modal
        // In production, you might want to verify with a protected endpoint
        document.getElementById('currentUser').textContent = username;
        document.getElementById('loginModal').classList.remove('active');
      } catch (err) {
        // If verification fails, clear storage and show login
        localStorage.removeItem('auth_username');
        localStorage.removeItem('auth_password');
      }
    }

    async function handleLogin() {
      const username = document.getElementById('modalUsername').value.trim();
      const password = document.getElementById('modalPassword').value.trim();
      const errorEl = document.getElementById('loginError');

      if (!username || !password) {
        errorEl.textContent = 'Please enter both username and password.';
        errorEl.classList.add('active');
        return;
      }

      // Test credentials with a dummy scan request to /health (no auth needed)
      // We'll verify on first actual scan request
      try {
        const credentials = btoa(`${username}:${password}`);

        // Store credentials
        localStorage.setItem('auth_username', username);
        localStorage.setItem('auth_password', password);

        // Update UI
        document.getElementById('currentUser').textContent = username;
        document.getElementById('loginModal').classList.remove('active');
        errorEl.classList.remove('active');

        // Clear modal fields
        document.getElementById('modalUsername').value = '';
        document.getElementById('modalPassword').value = '';
      } catch (err) {
        errorEl.textContent = 'Login failed. Please try again.';
        errorEl.classList.add('active');
      }
    }

    function handleLogout() {
      localStorage.removeItem('auth_username');
      localStorage.removeItem('auth_password');
      document.getElementById('loginModal').classList.add('active');
      document.getElementById('currentUser').textContent = '';
      document.getElementById('result').textContent = 'Waiting for scan...';
    }

    async function runScan() {
      const uid = document.getElementById('uid').value.trim();
      const username = localStorage.getItem('auth_username');
      const password = localStorage.getItem('auth_password');
      const resultEl = document.getElementById('result');
      const btn = document.getElementById('submitBtn');

      if (!uid) {
        resultEl.textContent = 'Please enter a UID.';
        return;
      }

      if (!username || !password) {
        resultEl.textContent = 'Please login first.';
        document.getElementById('loginModal').classList.add('active');
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
            // Invalid credentials - clear storage and show login
            localStorage.removeItem('auth_username');
            localStorage.removeItem('auth_password');
            document.getElementById('loginModal').classList.add('active');
            resultEl.textContent = JSON.stringify({ error: 'Authentication failed. Please login again.' }, null, 2);
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

    // Allow Enter key to submit in modal
    document.getElementById('modalPassword').addEventListener('keypress', function(e) {
      if (e.key === 'Enter') {
        handleLogin();
      }
    });
    document.getElementById('modalUsername').addEventListener('keypress', function(e) {
      if (e.key === 'Enter') {
        handleLogin();
      }
    });
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
