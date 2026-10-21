#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  printf 'Run as root after building the release artifacts: sudo deploy/install.sh\n' >&2
  exit 1
fi

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
binary="$root/target/release/inet-viewer-server"
web="$root/crates/web/dist"

[[ -x "$binary" ]] || { printf 'Missing %s; run cargo build --release -p inet-viewer-server\n' "$binary" >&2; exit 1; }
[[ -f "$web/index.html" ]] || { printf 'Missing frontend output; run (cd crates/web && trunk build --release)\n' >&2; exit 1; }

install -Dm0755 "$binary" /usr/local/bin/inet-viewer-server
install -d -m0755 /usr/local/lib/inet-viewer/web
cp -R --no-preserve=ownership "$web/." /usr/local/lib/inet-viewer/web/
find /usr/local/lib/inet-viewer/web -type d -exec chmod 0755 {} +
find /usr/local/lib/inet-viewer/web -type f -exec chmod 0644 {} +
install -Dm0644 "$root/deploy/inet-viewer.service" /etc/systemd/system/inet-viewer.service
install -d -m0750 /etc/inet-viewer
if [[ ! -e /etc/inet-viewer/inet-viewer.env ]]; then
  install -m0640 "$root/deploy/inet-viewer.env.example" /etc/inet-viewer/inet-viewer.env
  printf 'Created /etc/inet-viewer/inet-viewer.env; verify bind addresses before starting.\n'
fi
install -Dm0644 "$root/OPERATIONS.md" /usr/local/share/doc/inet-viewer/OPERATIONS.md
systemctl daemon-reload
printf 'Installed. Review /etc/inet-viewer/inet-viewer.env, then run:\n  sudo systemctl enable --now inet-viewer\n'
