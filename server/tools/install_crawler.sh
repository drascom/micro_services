#!/usr/bin/env bash
# Root-only Linux setup. Does not restart the API or modify its .env/data/venv.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
  echo 'Run as root to create the isolated crawler runtime.' >&2
  exit 1
fi
SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR=/opt/diziflix-crawler
mkdir -p "$RUNTIME_DIR"
if ! id diziflix-crawler >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/diziflix-crawler --shell /usr/sbin/nologin diziflix-crawler
fi
python3 -m venv --clear "$RUNTIME_DIR/venv"
"$RUNTIME_DIR/venv/bin/python" -m pip install -q -r "$SERVER_DIR/requirements-crawler.txt"
install -m 644 "$SERVER_DIR/app/scraper/crawlee_worker.py" "$RUNTIME_DIR/crawlee_worker.py"
install -m 644 "$SERVER_DIR/app/scraper/obscura_worker.py" "$RUNTIME_DIR/obscura_worker.py"
rm -rf "$RUNTIME_DIR/browsers"
"$SERVER_DIR/tools/install_obscura.sh"
echo "Crawlee HTTP worker and Obscura installed; account: diziflix-crawler"
