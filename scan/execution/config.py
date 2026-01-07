#!/usr/bin/env python3
"""
Shared configuration module for LivAuto execution scripts.
Loads environment variables and provides typed access to configuration.
"""

import os
from dataclasses import dataclass
from typing import Optional
from enum import Enum
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class PDFExtractionMethod(Enum):
    """Available PDF text extraction methods."""
    PYMUPDF = "pymupdf"      # Fastest, good Unicode support
    PDFPLUMBER = "pdfplumber"  # Best for form layouts
    DOCLING = "docling"      # Remote server (legacy)


@dataclass
class ImapConfig:
    """IMAP email server configuration."""
    server: str
    port: int
    username: str
    password: str
    mailbox: str = "INBOX"
    use_ssl: bool = True


@dataclass
class PDFConfig:
    """PDF extraction configuration."""
    method: PDFExtractionMethod
    # Docling settings (only used if method=docling)
    docling_url: str = ""
    docling_timeout: int = 120


@dataclass
class LLMConfig:
    """LLM provider configuration."""
    provider: str  # "openai", "anthropic", or "ollama"
    api_key: str
    model: str
    base_url: Optional[str] = None  # For Ollama/custom endpoints
    temperature: float = 0.0
    max_tokens: int = 4096


def get_imap_config() -> ImapConfig:
    """Load IMAP configuration from environment."""
    return ImapConfig(
        server=os.getenv("IMAP_SERVER", ""),
        port=int(os.getenv("IMAP_PORT", "993")),
        username=os.getenv("IMAP_USERNAME", ""),
        password=os.getenv("IMAP_PASSWORD", ""),
        mailbox=os.getenv("IMAP_MAILBOX", "INBOX"),
        use_ssl=os.getenv("IMAP_USE_SSL", "true").lower() == "true",
    )


def get_pdf_config() -> PDFConfig:
    """Load PDF extraction configuration from environment."""
    method_str = os.getenv("PDF_EXTRACTION_METHOD", "pymupdf").lower()

    # Map string to enum
    method_map = {
        "pymupdf": PDFExtractionMethod.PYMUPDF,
        "pdfplumber": PDFExtractionMethod.PDFPLUMBER,
        "docling": PDFExtractionMethod.DOCLING,
    }

    method = method_map.get(method_str, PDFExtractionMethod.PYMUPDF)

    return PDFConfig(
        method=method,
        docling_url=os.getenv("DOCLING_URL", "http://192.168.0.249:5001/v1/convert/file"),
        docling_timeout=int(os.getenv("DOCLING_TIMEOUT", "120")),
    )


def get_llm_config() -> LLMConfig:
    """Load LLM configuration from environment."""
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        base_url = None
    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        base_url = None
    elif provider == "ollama":
        api_key = os.getenv("OLLAMA_API_KEY", "")
        model = os.getenv("OLLAMA_MODEL", "ministral-3:8b")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
    )


def validate_config() -> list[str]:
    """
    Validate that all required configuration is present.
    Returns list of missing/invalid config items.
    """
    errors = []

    # Check IMAP config
    imap = get_imap_config()
    if not imap.server:
        errors.append("IMAP_SERVER is not set")
    if not imap.username:
        errors.append("IMAP_USERNAME is not set")
    if not imap.password:
        errors.append("IMAP_PASSWORD is not set")

    # Check PDF config (only validate Docling URL if using Docling)
    pdf = get_pdf_config()
    if pdf.method == PDFExtractionMethod.DOCLING and not pdf.docling_url:
        errors.append("DOCLING_URL is not set (required when PDF_EXTRACTION_METHOD=docling)")

    # Check LLM config
    try:
        llm = get_llm_config()
        # Ollama may not require API key for local instances
        if llm.provider != "ollama" and not llm.api_key:
            errors.append(f"{llm.provider.upper()}_API_KEY is not set")
        if llm.provider == "ollama" and not llm.base_url:
            errors.append("OLLAMA_BASE_URL is not set")
    except ValueError as e:
        errors.append(str(e))

    return errors


# Questionnaire detection patterns
QUESTIONNAIRE_FILENAME_PATTERNS = [
    "questionnaire",
    "medical_form",
    "patient_form",
    "health_form",
    "intake_form",
    "pre-op",
    "preop",
    "pre_op",
    "medical_history",
    "health_history",
]


def is_questionnaire_filename(filename: str) -> bool:
    """Check if filename suggests a medical questionnaire."""
    filename_lower = filename.lower()
    return any(pattern in filename_lower for pattern in QUESTIONNAIRE_FILENAME_PATTERNS)
