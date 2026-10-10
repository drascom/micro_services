# Microservices

A monorepo of standalone services. Each top-level directory is an independent
microservice with its own dependencies, configuration, and install steps.
**See each service's own README for full instructions.**

## Services

| Service | What it is | Platform | Branch | Docs |
|---------|------------|----------|--------|------|
| **scan** | LivAuto Scan — processes hair-transplant pre-op questionnaires from email attachments (FastAPI) | Linux (systemd) | `scan` | [scan/README.md](scan/README.md) |
| **diziflix** | Catalogue & playback API for the diziflix Tizen TV client (FastAPI) | Linux (systemd) / macOS | `diziflix` | `diziflix/server/README.md` |
| **assistant** | Local personal assistant — SwiftUI desktop ring with a TypeScript/Pi backend | macOS | `assistant` | `assistant/README.md` |

diziflix and assistant live on their own branches.

## Quick start

Each service is self-contained — `cd` into it and follow its README.

**scan** (Linux / systemd):
```bash
cd scan
cp .env.example .env   # then edit secrets
chmod +x install.sh
./install.sh
systemctl start scan-emails
```

**diziflix** (on the `diziflix` branch; Linux / systemd, runs on macOS too):
```bash
cd diziflix/server
./install.sh           # venv + deps + fixture + systemd unit (Linux only)
curl http://localhost:8090/api/health
```

## Adding a service

Create a new top-level directory containing its own `README.md` and an
install script (e.g. `install.sh`), then add a row to the **Services** table above.
