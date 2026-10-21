# diziflix

## Brief Introduction and Purpose

A FastAPI catalogue and playback service for the diziflix Tizen TV client. It provides a stable API over fixture or library-backed catalogue data and handles server-side media discovery. The detailed server guide is in [`diziflix/server/README.md`](diziflix/server/README.md).

## Quick Install

From the repository root on the `diziflix` branch:

```bash
cd diziflix/server
./install.sh
```

The installer creates the Python environment, installs requirements, prepares fixture data, and installs the systemd service on supported Linux systems.

## Developing Mode

From the repository root:

```bash
cd diziflix/server
source venv/bin/activate
uvicorn app.main:app --reload --port 8090
```

The server health endpoint is available at `http://localhost:8090/api/health` while it is running.

## Source Citations

- [Detailed server documentation](diziflix/server/README.md)
- [API contract](diziflix/API.md)
- [Server dependencies](diziflix/server/requirements.txt)
