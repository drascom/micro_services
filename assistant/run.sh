#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec "${ASSISTANT_NODE:-node}" src/main.ts "$@"
