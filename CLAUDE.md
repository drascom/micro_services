# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Architecture

This is a **microservices repository** where each top-level directory contains a standalone application that operates as an independent microservice. Each service has its own dependencies, configuration, and deployment setup.

### Current Services

- **scan/** - Email questionnaire processing service (LivAuto Scan)
  - FastAPI REST API that processes medical questionnaires from email attachments
  - Runs as systemd service on Linux
  - Listens on `http://0.0.0.0:1000`

- **gemma/** - Local Gemma 4 12B server with a macOS menu-bar app
  - Serves Gemma 4 12B via llama.cpp as an OpenAI-compatible API on `http://0.0.0.0:8080`
  - `GemmaServer.app` (Swift/AppKit) starts/stops the server from the menu bar
  - macOS / Apple Silicon only; `install.sh` builds llama.cpp + the app and downloads the model

## Development Commands

### scan/ Service

**Setup and Installation:**
```bash
cd scan
cp .env.example .env
# Edit .env with required credentials
chmod +x install.sh
./install.sh
```

**Service Management (systemd):**
```bash
# Start service
systemctl start scan-emails

# Stop service
systemctl stop scan-emails

# Check status
systemctl status scan-emails

# View logs
journalctl -u scan-emails -f
```

**Local Development:**
```bash
cd scan

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
pip install -r requirements.txt
# OR using uv (faster)
uv pip install -r requirements.txt

# Run locally (development mode)
uvicorn app:app --host 0.0.0.0 --port 1000 --reload
```

**Testing the API:**
```bash
# Health check
curl http://localhost:1000/health

# Process email by UID (GET)
curl "http://localhost:1000/scan?uid=12345"

# Process email by UID (POST)
curl -X POST http://localhost:1000/scan \
  -H "Content-Type: application/json" \
  -d '{"uid": "12345"}'
```

### gemma/ Service

**Setup and Installation (macOS / Apple Silicon):**
```bash
cd gemma
./install.sh        # builds llama.cpp + GemmaServer.app, downloads the model
open GemmaServer.app
```
`install.sh` is idempotent: it reuses an existing llama.cpp build and skips the
model download if the files are already present. Override paths/port/ctx with
env vars (`GEMMA_MODEL`, `GEMMA_PORT`, `GEMMA_CTX`, …) — they are written to a
gitignored `config.env` and baked into the app bundle as `config.json`.

**Running the server without the app:**
```bash
cd gemma
./run-gemma.sh      # starts llama-server in the terminal (Ctrl-C to stop)
```

**Rebuilding just the app (after editing app/ sources):**
```bash
cd gemma
./app/build.sh      # recompiles GemmaServer.app and regenerates icons
```

**Testing the API:**
```bash
curl http://localhost:8080/health
curl http://localhost:8080/props        # shows loaded context size (n_ctx)
```

## Code Architecture

### scan/ Service Architecture

**Processing Pipeline:**
```
Email (IMAP)
    ↓
fetch_email_attachments.py → Extract PDFs from email by UID
    ↓
extract_pdf_text.py → Convert PDF to raw text
    ├─ PyMuPDF (fastest, default)
    ├─ pdfplumber (better for forms)
    └─ convert_pdf_docling.py (remote service, legacy)
    ↓
normalize_markdown.py → Clean text, preserve checkboxes
    ↓
extract_medical_data.py → LLM extraction with schema validation
    ↓
JSON Response → Structured medical data + metadata
```

**Module Organization:**

- `app.py` - FastAPI application entry point, HTTP endpoints, orchestration
- `execution/` - Processing pipeline modules (each handles one step)
  - `config.py` - Configuration loader with typed dataclasses and validation
  - `fetch_email_attachments.py` - IMAP email fetching
  - `extract_pdf_text.py` - Local PDF text extraction (PyMuPDF/pdfplumber)
  - `convert_pdf_docling.py` - Remote PDF conversion via Docling service
  - `normalize_markdown.py` - Text cleanup and checkbox preservation
  - `extract_medical_data.py` - LLM-based data extraction
  - `process_questionnaire.py` - Standalone orchestration script
  - `_template.py` - Template for creating new modules

**Configuration System:**

All configuration is managed through `.env` file (never commit secrets). The system supports:

- **Pluggable PDF extraction** - Switch between `pymupdf`, `pdfplumber`, or `docling` via `PDF_EXTRACTION_METHOD`
- **Multi-LLM support** - Works with OpenAI, Anthropic, or local Ollama via `LLM_PROVIDER`
- **IMAP configuration** - Email server settings for fetching attachments
- **Questionnaire detection** - Pattern-based filename matching to identify form documents

All environment variables are validated at startup via `config.validate_config()`.

**Key Design Patterns:**

1. **Dataclass results** - Each module returns a dataclass with `status`, `data`, and `message` fields
2. **Configuration-driven** - No code changes needed for different environments
3. **Error propagation** - Errors bubble up through the pipeline with clear messages
4. **Checkbox preservation** - Special handling of form checkboxes across all extraction methods
5. **Standalone scripts** - Each execution module can run independently for testing

## Adding New Microservices

When adding a new service to this repository:

1. Create a new top-level directory for the service
2. Include service-specific `.env.example` with all required configuration
3. Add `install.sh` if systemd service integration is needed
4. Update this CLAUDE.md with service-specific commands and architecture
5. Each service should be independently deployable and runnable

## Important Notes

- **Environment files** - `.env` files contain secrets and are gitignored. Always use `.env.example` as template
- **Virtual environments** - Each service uses its own venv in `venv/` directory
- **Systemd services** - Service names follow pattern `{directory-name}-emails` (e.g., `scan-emails`)
- **Dependencies** - Use `uv` package manager when available (faster than pip), fallback to pip
- **PDF extraction** - PyMuPDF is default and fastest; use pdfplumber for complex form layouts
- **LLM providers** - Configure only the provider you're using (OpenAI, Anthropic, or Ollama)
