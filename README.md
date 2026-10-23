# diziflix

## Brief Introduction and Purpose

A FastAPI catalogue and playback service for the diziflix Tizen TV client. It provides a stable API over fixture or library-backed catalogue data and handles server-side media discovery. The detailed server guide is in [`server/README.md`](server/README.md).

## Quick Install

From the repository root on the `diziflix` branch:

```bash
cd server
./install.sh
```

The installer creates the Python environment, installs requirements, prepares fixture data, and installs the systemd service on supported Linux systems.

Clients have no built-in server address: on first launch the web/TV client asks for your server's IP or URL (for example `192.168.1.10`, which becomes `http://192.168.1.10:8090`). To deploy from a workstation with `./deploy.sh`, copy `deploy.env.example` to `deploy.env` (not tracked) and set your own host and paths.

## Developing Mode

From the repository root:

```bash
cd server
source venv/bin/activate
uvicorn app.main:app --reload --port 8090
```

The server health endpoint is available at `http://localhost:8090/api/health` while it is running.

## Source Citations

- [Detailed server documentation](server/README.md)
- [API contract](API.md)
- [Server dependencies](server/requirements.txt)
