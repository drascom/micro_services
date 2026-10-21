# LivAuto Scan

## Brief Introduction and Purpose

LivAuto Scan is a FastAPI service that processes hair-transplant pre-operative questionnaires received as email attachments. It extracts and normalizes questionnaire data for downstream review.

## Quick Install

From the repository root on the `scan` branch:

```bash
cp .env.example .env
# Set the required credentials and configuration in .env.
chmod +x install.sh
./install.sh
```

The installer creates the Python environment and configures the `scan-emails` systemd service where systemd is available.

## Developing Mode

From the repository root:

```bash
uv venv venv
uv pip install --python venv/bin/python -r requirements.txt
venv/bin/uvicorn app:app --reload --host 0.0.0.0 --port 1000
```

The local service listens on port `1000`. Use the project configuration and authentication settings documented in `.env.example` and the application source.

### Login

The web interface uses session-based authentication. Set `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` in `.env`, open the web interface, and sign in through the login modal; credentials are checked against the `.env` values and a session token is stored in the browser for later requests. Logout invalidates the token. Sessions are kept in memory and reset when the server restarts.

## Source Citations

- [Application entry point](app.py)
- [Installer and service configuration](install.sh)
- [Python dependencies](requirements.txt)
