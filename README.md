# Microservices

A monorepo of standalone services. Each top-level directory is an independent
microservice with its own dependencies, configuration, and install steps.
**See each service's own README for full instructions.**

## Services

| Service | What it is | Platform | Docs |
|---------|------------|----------|------|
| **gemma** | Local Gemma 4 12B server with an OpenAI-compatible API, driven by a macOS menu-bar app | macOS (Apple Silicon) | [gemma/README.md](gemma/README.md) |
| **scan** | LivAuto Scan — processes hair-transplant pre-op questionnaires from email attachments (FastAPI) | Linux (systemd) | [scan/README.md](scan/README.md) |

## Quick start

Each service is self-contained — `cd` into it and follow its README.

**gemma** (macOS):
```bash
cd gemma
./install.sh        # builds llama.cpp + the app, downloads the model
open GemmaServer.app
```

**scan** (Linux / systemd):
```bash
cd scan
cp .env.example .env   # then edit secrets
chmod +x install.sh
./install.sh
systemctl start scan-emails
```

## Adding a service

Create a new top-level directory containing its own `README.md` and an
install script (e.g. `install.sh`), then add a row to the **Services** table above.
