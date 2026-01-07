#!/usr/bin/env python3
"""
Lightweight FastAPI app to process medical questionnaire PDFs.
"""

import os
import secrets
import sys
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Depends, File, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Ensure local scan folder is on sys.path for execution module imports
project_root = Path(__file__).resolve().parent
execution_root = project_root / "execution"
for path in (project_root, execution_root):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Load environment variables for LLM configs
load_dotenv(project_root / ".env")

# Import local execution modules
from execution.config import (
    validate_config,
    get_pdf_config,
    PDFExtractionMethod,
)
from execution.extract_pdf_text import extract_from_bytes, ExtractionMethod
from execution.convert_pdf_docling import convert_pdf_to_markdown
from execution.normalize_markdown import normalize_for_llm
from execution.extract_medical_data import extract_medical_data


app = FastAPI(title="LivAuto Scan", version="1.0.0")

# Load auth credentials from environment
BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "changeme123")

# Simple in-memory session store
sessions = {}  # {token: username}


def verify_token(authorization: str = Query(None, alias="token")):
    """Verify session token."""
    if not authorization or authorization not in sessions:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )
    return sessions[authorization]


@dataclass
class ProcessingResult:
    status: str
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


def process_pdf_content(filename: str, pdf_content: bytes) -> ProcessingResult:
    extracted_text, extraction_error = extract_text_from_pdf(pdf_content, filename)

    if extraction_error:
        return ProcessingResult(
            status="error",
            source_filename=filename,
            message=f"PDF text extraction failed: {extraction_error}",
        )

    if not extracted_text or len(extracted_text.strip()) < 50:
        return ProcessingResult(
            status="no_actionable_document",
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
                source_filename=filename,
                message=f"Text normalization failed: {str(exc)}",
            )
    else:
        cleaned_text = extracted_text

    extraction_result = extract_medical_data(cleaned_text)

    if extraction_result.status == "error":
        return ProcessingResult(
            status="error",
            source_filename=filename,
            message=f"Data extraction failed: {extraction_result.message}",
        )

    extracted_data = extraction_result.data or {}

    # Use full_name from extracted data for filename, fallback to original
    full_name = extracted_data.get("full_name", "").strip()
    output_filename = f"{full_name}-form.pdf" if full_name else filename

    validation_errors = extraction_result.validation_errors
    error_message = "; ".join(validation_errors) if validation_errors else None

    extracted_data["_metadata"] = {
        "source_filename": filename,
        "extraction_method": pdf_config.method.value,
        "is_valid": extraction_result.is_valid,
        "validation_errors": extraction_result.validation_errors,
        "error_message": error_message,
    }

    return ProcessingResult(
        status="success",
        source_filename=output_filename,
        data=extracted_data,
    )


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/login")
def login(payload: LoginRequest):
    """Verify credentials and return session token."""
    username_correct = secrets.compare_digest(
        payload.username.encode("utf8"), BASIC_AUTH_USERNAME.encode("utf8")
    )
    password_correct = secrets.compare_digest(
        payload.password.encode("utf8"), BASIC_AUTH_PASSWORD.encode("utf8")
    )

    if not (username_correct and password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Generate session token
    token = secrets.token_urlsafe(32)
    sessions[token] = payload.username

    return {"token": token, "username": payload.username}


@app.post("/logout")
def logout(token: str = Query(...)):
    """Remove session token."""
    if token in sessions:
        del sessions[token]
    return {"status": "logged out"}


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
      <h1>LivAuto Scan</h1>
      <div class=\"user-info\">
        Logged in as: <strong id=\"currentUser\"></strong>
        <button class=\"logout\" onclick=\"handleLogout()\">Logout</button>
      </div>
    </div>
    <div class=\"card\">
      <label for=\"pdfFile\">Upload PDF</label>
      <input id=\"pdfFile\" type=\"file\" accept=\"application/pdf,.pdf\" />
      <button id=\"submitBtn\" onclick=\"runScan()\">Scan</button>
      <pre id=\"result\">Waiting for scan...</pre>
    </div>
  </div>

  <script>
    // Check for stored token on page load
    window.addEventListener('DOMContentLoaded', function() {
      const storedToken = localStorage.getItem('session_token');
      const storedUsername = localStorage.getItem('username');

      if (storedToken && storedUsername) {
        document.getElementById('currentUser').textContent = storedUsername;
        document.getElementById('loginModal').classList.remove('active');
      }
    });

    async function handleLogin() {
      const username = document.getElementById('modalUsername').value.trim();
      const password = document.getElementById('modalPassword').value.trim();
      const errorEl = document.getElementById('loginError');

      if (!username || !password) {
        errorEl.textContent = 'Please enter both username and password.';
        errorEl.classList.add('active');
        return;
      }

      try {
        const response = await fetch('/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password })
        });

        if (!response.ok) {
          errorEl.textContent = 'Invalid username or password.';
          errorEl.classList.add('active');
          return;
        }

        const data = await response.json();

        // Store token and username
        localStorage.setItem('session_token', data.token);
        localStorage.setItem('username', data.username);

        // Update UI
        document.getElementById('currentUser').textContent = data.username;
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

    async function handleLogout() {
      const token = localStorage.getItem('session_token');

      if (token) {
        try {
          await fetch(`/logout?token=${encodeURIComponent(token)}`, { method: 'POST' });
        } catch (err) {
          // Ignore errors on logout
        }
      }

      localStorage.removeItem('session_token');
      localStorage.removeItem('username');
      document.getElementById('loginModal').classList.add('active');
      document.getElementById('currentUser').textContent = '';
      document.getElementById('result').textContent = 'Waiting for scan...';
      document.getElementById('pdfFile').value = '';
    }

    async function runScan() {
      const fileInput = document.getElementById('pdfFile');
      const file = fileInput.files[0];
      const token = localStorage.getItem('session_token');
      const resultEl = document.getElementById('result');
      const btn = document.getElementById('submitBtn');

      if (!file) {
        resultEl.textContent = 'Please select a PDF file.';
        return;
      }

      if (!token) {
        resultEl.textContent = 'Please login first.';
        document.getElementById('loginModal').classList.add('active');
        return;
      }

      const formData = new FormData();
      formData.append('file', file);

      btn.disabled = true;
      resultEl.textContent = 'Scanning...';

      try {
        const response = await fetch(`/scan?token=${encodeURIComponent(token)}`, {
          method: 'POST',
          body: formData
        });
        const data = await response.json();

        if (!response.ok) {
          if (response.status === 401) {
            localStorage.removeItem('session_token');
            localStorage.removeItem('username');
            document.getElementById('loginModal').classList.add('active');
            resultEl.textContent = JSON.stringify({ error: 'Session expired. Please login again.' }, null, 2);
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


def is_pdf_upload(upload: UploadFile) -> bool:
    filename = (upload.filename or "").lower()
    if filename.endswith(".pdf"):
        return True
    return upload.content_type == "application/pdf"


def wipe_upload_tempfile(upload: UploadFile, content_size: Optional[int] = None) -> None:
    file_obj = upload.file
    try:
        if content_size is not None and hasattr(file_obj, "seek") and hasattr(file_obj, "write"):
            try:
                file_obj.seek(0)
                file_obj.write(b"\x00" * content_size)
                file_obj.flush()
                file_obj.seek(0)
                file_obj.truncate(0)
            except Exception:
                pass
    finally:
        try:
            file_obj.close()
        except Exception:
            pass

    temp_path = getattr(file_obj, "name", None)
    if isinstance(temp_path, str) and os.path.isfile(temp_path):
        try:
            with open(temp_path, "r+b") as temp_file:
                temp_file.seek(0, os.SEEK_END)
                size = temp_file.tell()
                temp_file.seek(0)
                if size:
                    temp_file.write(b"\x00" * size)
                    temp_file.flush()
            os.remove(temp_path)
        except Exception:
            pass


def close_upload(upload: UploadFile) -> None:
    try:
        upload.file.close()
    except Exception:
        pass


@app.post("/scan")
def scan(
    file: UploadFile = File(...),
    username: str = Depends(verify_token),
):
    """Process a single PDF file and extract medical data."""
    config_errors = validate_config(require_imap=False)
    filename = file.filename or "unknown"
    content_size = None

    if config_errors:
        close_upload(file)
        return JSONResponse(content=asdict(ProcessingResult(
            status="error",
            source_filename=filename,
            message=f"Configuration errors: {'; '.join(config_errors)}",
        )))

    if not is_pdf_upload(file):
        close_upload(file)
        return JSONResponse(content=asdict(ProcessingResult(
            status="error",
            source_filename=filename,
            message="Only PDF files are supported",
        )))

    try:
        pdf_content = file.file.read()
        content_size = len(pdf_content)
    except Exception as exc:
        return JSONResponse(content=asdict(ProcessingResult(
            status="error",
            source_filename=filename,
            message=f"Failed to read uploaded file: {str(exc)}",
        )))
    finally:
        wipe_upload_tempfile(file, content_size)

    if not pdf_content:
        return JSONResponse(content=asdict(ProcessingResult(
            status="error",
            source_filename=filename,
            message="Uploaded file is empty",
        )))

    result = process_pdf_content(filename, pdf_content)
    return JSONResponse(content=asdict(result))


@app.get("/health")
def health_check():
    return {"status": "healthy"}
