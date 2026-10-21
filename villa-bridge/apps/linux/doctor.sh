#!/bin/sh
set -eu

DATA_DIR="${VILLA_BRIDGE_DATA_DIR:-/var/lib/villa-bridge}"
FAILED=0

check() {
  if "$@" >/dev/null 2>&1; then
    printf 'OK   %s\n' "$*"
  else
    printf 'FAIL %s\n' "$*"
    FAILED=1
  fi
}

wait_for_ready() {
  attempt=1
  while [ "$attempt" -le 30 ]; do
    if curl --fail --silent --max-time 3 \
      http://127.0.0.1:8092/api/ready >/dev/null 2>&1; then
      printf 'OK   runtime ready after at most %s seconds\n' "$((attempt * 2))"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  printf 'FAIL runtime did not become ready within 60 seconds\n'
  FAILED=1
}

echo "Villa Bridge Linux doctor"
echo "Host: $(uname -s) $(uname -m)"
if command -v node >/dev/null 2>&1; then
  echo "Node: $(node --version)"
else
  echo "Node: missing"
  FAILED=1
fi

# Yalnizca dizinin kendisi burada denetlenir: onu isletim sistemi duzeyinde
# `install.sh` yaratir. ICINDEKI dosyalari paylasilan calisma zamani tohumlar
# (apps/runtime/first-run.cjs), cunku Android tablette kabuk ve kurulum betigi
# yoktur. Dosya listesini burada tekrarlamak o bilgiyi tek konaga hapsederdi;
# tohumlamanin sonucu asagida diagnostics uzerinden sorulur.
check test -d "$DATA_DIR"
check systemctl is-enabled villa-bridge.service
check systemctl is-active villa-bridge.service

if command -v curl >/dev/null 2>&1; then
  # A fresh install is not "not ready", it is "waiting for the setup wizard".
  # Reporting that as a failure would send people looking for a broken service.
  DIAGNOSTICS="$(curl --silent --max-time 3 \
    http://127.0.0.1:8092/api/android/diagnostics 2>/dev/null || true)"
  MODE="$(printf '%s' "$DIAGNOSTICS" | sed -n 's/.*"mode":"\([^"]*\)".*/\1/p')"
  # Ilk acilis tohumlamasinin sonucunu calisma zamani bildirir; hangi dosyalari
  # yazdigi onun bilgisidir, bu betigin degil.
  if [ -n "$DIAGNOSTICS" ]; then
    case "$DIAGNOSTICS" in
      *'"provisioned":true'*) printf 'OK   runtime configuration is seeded and readable\n' ;;
      *) printf 'INFO runtime reports no usable configuration yet\n' ;;
    esac
  fi
  case "$MODE" in
    *-setup)
      printf 'INFO runtime is in setup mode; no coordinator is owned yet\n'
      printf '     finish the setup at http://%s:8091\n' \
        "$(hostname -I 2>/dev/null | awk '{print $1}')"
      check curl --fail --silent --max-time 3 http://127.0.0.1:8091/api/health
      ;;
    *)
      wait_for_ready
      check curl --fail --silent --max-time 3 http://127.0.0.1:8091/api/health
      ;;
  esac
fi

if [ "$FAILED" -ne 0 ]; then
  echo "One or more checks need attention."
  exit 1
fi
echo "All Linux runtime checks passed."
