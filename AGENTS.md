# AGENTS.md

Guidance for Codex. Microservices repository: each top-level directory is a standalone application with its own dependencies, config, and deployment. Service-specific commands and architecture live in each service's own `CLAUDE.md`.

## Services
- **scan/** — email questionnaire processing (FastAPI, systemd `scan-emails`, port 1000)
- **diziflix/** — dizi/film catalog server + Tizen TV client (lives on the `diziflix` branch; see `diziflix/CLAUDE.md`)
- **assistant/** — local personal assistant (SwiftUI ring + TypeScript/Pi backend; lives on the `assistant` branch)

Each service is developed on its own branch (`scan`, `diziflix`, `assistant`); `main` holds the shared root files.

## Adding a new service
1. New top-level directory; include a `.env.example` with all required config.
2. Add `install.sh` if systemd integration is needed (service names follow `{directory-name}-emails`, e.g. `scan-emails`).
3. Give it its own `CLAUDE.md`, and add it to the list above.

## Conventions
- `.env` files hold secrets and are gitignored — always start from `.env.example`.
- Each service uses its own `venv/`; prefer `uv` over pip when available.
