# scan/ — LivAuto Scan

FastAPI service that processes medical questionnaires from email attachments. Listens on `http://0.0.0.0:1000`, runs as systemd service `scan-emails` on Linux.

## Commands
```bash
cp .env.example .env && ./install.sh          # setup; edit .env first
uvicorn app:app --host 0.0.0.0 --port 1000 --reload   # local dev (venv/ + uv pip install -r requirements.txt)
systemctl start|stop|status scan-emails       # service; logs: journalctl -u scan-emails -f
curl "http://localhost:1000/scan?uid=12345"   # GET or POST {"uid": "..."}; /health for health
```

## Architecture
Pipeline (`execution/`, one module per step, each runnable standalone): fetch email PDFs by UID (IMAP) → extract PDF text → `normalize_markdown.py` (preserves checkboxes) → `extract_medical_data.py` (LLM + schema validation) → JSON response. `app.py` is the FastAPI entry/orchestrator; `execution/config.py` loads `.env` into typed dataclasses and `validate_config()` checks it at startup.

- Each module returns a dataclass with `status`, `data`, `message`; errors bubble up with clear messages.
- PDF extraction is pluggable via `PDF_EXTRACTION_METHOD`: `pymupdf` (default, fastest), `pdfplumber` (better for complex form layouts), `docling` (remote service, legacy).
- `LLM_PROVIDER` selects OpenAI, Anthropic, or local Ollama — configure only the one in use.
- Questionnaires are detected by filename patterns (config-driven).
- Checkbox handling is special-cased across all extraction methods — keep it when touching extraction.
- `.env` holds secrets and is gitignored; never commit it.
