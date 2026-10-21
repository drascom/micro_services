#!/bin/sh
# Ulusal TV (TR) Stremio addon sunucusu
DIR=$(cd "$(dirname "$0")" && pwd)
NODE=${NODE:-node}
export PORT=${PORT:-7788}
exec "$NODE" "$DIR/server.js"
