# LivAuto Scan

## Brief Introduction and Purpose

LivAuto Scan is a FastAPI service that processes hair-transplant pre-operative questionnaires received as email attachments. It extracts and normalizes questionnaire data for downstream review.

## Quick Install

From the repository root on the `scan` branch:

```bash
cd scan
cp .env.example .env
# Set the required credentials and configuration in .env.
chmod +x install.sh
./install.sh
```

The installer creates the Python environment and configures the `scan-emails` systemd service where systemd is available.

## Developing Mode

From the repository root:

```bash
cd scan
uv venv venv
uv pip install --python venv/bin/python -r requirements.txt
venv/bin/uvicorn app:app --reload --host 0.0.0.0 --port 1000
```

The local service listens on port `1000`. Use the project configuration and authentication settings documented in `.env.example` and the application source.

## Source Citations

- [Application entry point](scan/app.py)
- [Installer and service configuration](scan/install.sh)
- [Project login notes](scan/README.md)
- [Python dependencies](scan/requirements.txt)
