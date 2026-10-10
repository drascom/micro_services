#!/usr/bin/env bash
# server2/deploy.sh - diziflix server2'yi (port 8091, servis diziflix2) rsync ile dağıtır / geri alır. Eski ../deploy.sh'tan ayrıdır.
# macOS bash 3.2 uyumlu. Yardım: ./deploy.sh --help
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

HOST=${DZ2_HOST:-root@192.168.0.61}
DEST=${DZ2_DEST:-/root/micro_services/diziflix/server2}
SERVICE=${DZ2_SERVICE:-diziflix2}
PORT=${DZ2_PORT:-8091}
BACKUP_DIR=${DZ2_BACKUP_DIR:-/root}
KEEP=${DZ2_KEEP:-5}
DRAIN_WAIT=${DZ2_DRAIN_WAIT:-120}      # en çok bu kadar saniye çalışan işlerin bitmesi beklenir
POLL_S=${DZ2_POLL_S:-3}
CHECK_CMD=${DZ2_CHECK_CMD:-./check.sh}
SSH_OPTS=${DZ2_SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=10}
# Fallback when the running (old) server has no /api/control/llm/kinds yet.
LLM_KINDS_DEFAULT="stage routing_suggest"
# Anchored to the transfer root: only server2/.env, server2/data/, server2/venv/ are protected (not e.g. app/data/).
EXCLUDES=(--exclude=/.env --exclude=/data/ --exclude=/venv/ --exclude=/deploy-backups/
          --exclude=__pycache__/ --exclude=.DS_Store --exclude='*.pyc')
R_EXCLUDES="--exclude=/.env --exclude=/data/ --exclude=/venv/ --exclude=__pycache__/"   # same, for rsync run on the server

usage() {
  cat <<EOF
diziflix server2 deploy

Kullanım:
  ./deploy.sh                    dağıt: [0] ./check.sh  [1] sunucuda kod yedeği  [2] fark özeti  [3] rsync -> server2.next/
                                 (+ import app.main)  [4] drain  [5] server2.next -> server2, kurulum/pip, import
                                 [6] restart  [7] uç doğrulama + drain kapat, yedek budama
                                 Drain başarısızsa (çıkış 3) server2.next silinir; canlı dizine dokunulmaz.
  ./deploy.sh --dry-run          yalnız [0] ve [2] (rsync -n); sunucuda HİÇBİR ŞEY değişmez
  ./deploy.sh --print-plan       hiçbir şey çalıştırmaz (ssh/rsync dahil); tam deploy'un komutlarını yazar
  ./deploy.sh --rollback [-y]    son yedeğe dön (.env/data/venv korunur) + drain + restart + doğrulama
  --force-restart                drain süresi dolduğunda LLM işi sürse de restart et
  -y, --yes                      onay sorma (rollback)

Hedef: $HOST:$DEST  servis $SERVICE  port $PORT  yedek $BACKUP_DIR/diziflix2-bak-*.tgz (son $KEEP)
Dışlananlar (sunucuda korunur, silinmez): .env data/ venv/ __pycache__/
Site haritaları ve provider tarifleri data/ ve DB'dedir: deploy onlara hiç dokunmaz. .env içeriği yazdırılmaz.
Ortam: DZ2_HOST DZ2_DEST DZ2_SERVICE DZ2_PORT DZ2_BACKUP_DIR DZ2_KEEP DZ2_DRAIN_WAIT DZ2_POLL_S DZ2_CHECK_CMD DZ2_SSH_OPTS
Çıkış: 0 tamam | 1 hata | 2 restart/doğrulama başarısız | 3 LLM işi sürüyor, restart edilmedi (drain geri kapatıldı)
EOF
}

MODE=deploy; DRY=0; PLAN=0; FORCE=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --print-plan) PLAN=1 ;;
    --force-restart) FORCE=1 ;;
    --rollback) MODE=rollback ;;
    -y|--yes) YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "bilinmeyen seçenek: $1 (./deploy.sh --help)" >&2; exit 1 ;;
  esac
  shift
done

valid() { printf '%s' "$1" | grep -Eq "$2" || { echo "$3 geçersiz: '$1'" >&2; exit 1; }; }
valid "$HOST" '^[A-Za-z0-9][A-Za-z0-9._@:-]*$' DZ2_HOST
valid "$DEST" '^/[A-Za-z0-9._/-]+/server2$' DZ2_DEST
valid "$SERVICE" '^[A-Za-z0-9][A-Za-z0-9._@-]*$' DZ2_SERVICE
valid "$PORT" '^[0-9]{1,5}$' DZ2_PORT
valid "$BACKUP_DIR" '^/[A-Za-z0-9._/-]+$' DZ2_BACKUP_DIR
valid "$KEEP" '^[1-9][0-9]*$' DZ2_KEEP
[ "$SERVICE" != "diziflix" ] || { echo "servis adı eski sunucuyla çakışıyor: diziflix" >&2; exit 1; }
if [ "$MODE" = rollback ] && [ "$DRY" = 1 ]; then echo "--rollback --dry-run ile kullanılamaz" >&2; exit 1; fi

PARENT=$(dirname "$DEST")
NEXT="$PARENT/server2.next"   # staging: new code lands here first, the live dir changes only after a successful drain
TS=$(date +%Y%m%d-%H%M%S)
BACKUP="$BACKUP_DIR/diziflix2-bak-$TS.tgz"
API="http://127.0.0.1:$PORT"
DRAIN_SET=0
STAGED=0     # 1 = $NEXT exists and has not been switched in yet (discarded on failure)

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf 'HATA %s\n' "$1" >&2; exit "${2:-1}"; }

# Remote calls. read = runs unless --print-plan; write = only printed with --dry-run / --print-plan.
show() { printf '   [%s] %s\n' "$1" "$2" >&2; }
r_read() {
  if [ "$PLAN" = 1 ]; then show "plan ssh" "$1"; return 0; fi
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$HOST" "$1"
}
r_write() {
  if [ "$PLAN" = 1 ] || [ "$DRY" = 1 ]; then show "yazmaz ssh" "$1"; return 0; fi
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$HOST" "$1"
}
rsync_to() {   # rsync_to <target dir> <extra flags...>; -n = read-only listing
  local target=$1; shift
  local args=(-az --delete -i "${EXCLUDES[@]}" -e "ssh $SSH_OPTS" "$@" "$SCRIPT_DIR/" "$HOST:$target/")
  local listing=0 a
  for a in "$@"; do [ "$a" = "-n" ] && listing=1; done
  if [ "$PLAN" = 1 ] && [ "$listing" = 1 ]; then show "plan rsync" "rsync ${args[*]}"; return 0; fi
  if [ "$PLAN" = 1 ] || { [ "$DRY" = 1 ] && [ "$listing" = 0 ]; }; then show "yazmaz rsync" "rsync ${args[*]}"; return 0; fi
  rsync "${args[@]}"
}

drain_set() {   # drain_set true|false
  r_write "curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d '{\"on\": $1}' $API/api/control/drain" >/dev/null
}
# Fallback when the HTTP call fails (process down/hung): clear the persisted flag straight in the DB.
drain_off_db() {
  r_write "cd '$DEST' && venv/bin/python -c \"from app import settings; settings.set('jobs:drain', False)\""
}
cleanup() {
  local rc=$?
  if [ "$DRAIN_SET" = 1 ]; then
    info "drain geri kapatılıyor"
    drain_set false || drain_off_db \
      || info "UYARI: drain kapatılamadı; admin Ayarlar > Sistem'den kapatın (açılışta admin notu da düşer)"
  fi
  if [ "$STAGED" = 1 ]; then
    info "server2.next atılıyor; canlı dizin değişmedi"
    r_write "rm -rf '$NEXT'" || true
  fi
  exit "$rc"
}
trap cleanup EXIT

# Running jobs from /api/control/jobs/active: prints "<running> <llm_running>" (llm = kind in $1).
count_running() {
  python3 -c '
import json, sys
kinds = set(sys.argv[1].split())
try:
    jobs = json.load(sys.stdin).get("jobs") or []
except ValueError:
    print("? ?"); sys.exit(0)
run = [j for j in jobs if j.get("state") == "running"]
print(len(run), sum(1 for j in run if j.get("kind") in kinds))
' "$1"
}

service_active() { r_read "systemctl is-active --quiet $SERVICE" >/dev/null 2>&1; }

# [4] drain: no new job is leased; wait for running jobs; an LLM job still running at the deadline stops the deploy.
do_drain() {
  step "[4] drain"
  if [ "$PLAN" = 0 ] && ! service_active; then info "servis çalışmıyor; drain atlandı"; return 0; fi
  drain_set true; DRAIN_SET=1
  local kinds waited=0 jobs counts running llm
  kinds=$(r_read "curl -fsS -m 10 $API/api/control/llm/kinds" 2>/dev/null \
          | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)["kinds"]))' 2>/dev/null || true)
  [ -n "$kinds" ] || kinds=$LLM_KINDS_DEFAULT
  if [ "$PLAN" = 1 ]; then
    show "plan" "her ${POLL_S} sn: curl $API/api/control/jobs/active (en çok ${DRAIN_WAIT} sn; LLM türleri: $kinds)"; return 0
  fi
  while :; do
    jobs=$(r_read "curl -fsS -m 10 $API/api/control/jobs/active" || echo '{}')
    counts=$(printf '%s' "$jobs" | count_running "$kinds")
    running=${counts% *}; llm=${counts#* }
    [ "$running" = 0 ] && { info "çalışan iş yok"; return 0; }
    if [ "$waited" -ge "$DRAIN_WAIT" ]; then break; fi
    info "çalışan iş: $running (LLM: $llm), bekleniyor ($waited/${DRAIN_WAIT} sn)"
    sleep "$POLL_S"; waited=$((waited + POLL_S))
  done
  if [ "$llm" != 0 ] && [ "$FORCE" = 0 ]; then
    die "LLM işi hâlâ çalışıyor ($llm); restart edilmedi. Bekleyip yeniden deneyin ya da --force-restart" 3
  fi
  info "UYARI: $running iş sürüyor; kaldıkları adımdan (checkpoint) devam edecekler"
}

# [6] restart + is-active + journal scan (this run only: the unit's InvocationID, no client/server clock mix)
do_restart() {
  step "[6] restart"
  r_write "systemctl restart $SERVICE" || die "systemctl restart başarısız" 2
  [ "$PLAN" = 1 ] && { show "plan ssh" "systemctl is-active $SERVICE; journalctl _SYSTEMD_INVOCATION_ID=<InvocationID> | grep Traceback"; return 0; }
  local i=0
  until service_active; do
    i=$((i + 1)); [ "$i" -ge 15 ] && die "$SERVICE etkin değil (journalctl -u $SERVICE)" 2
    sleep 1
  done
  info "$SERVICE etkin"
  if r_read "I=\$(systemctl show -p InvocationID --value $SERVICE); [ -n \"\$I\" ] && journalctl _SYSTEMD_INVOCATION_ID=\$I --no-pager -o cat 2>/dev/null | grep -E 'Traceback|CRITICAL|Error loading'" ; then
    die "journal'da hata var (yukarıda)" 2
  fi
}

# [7] endpoint checks; finally drain off (the flag is persisted, the new process would still be draining)
do_verify() {
  step "[7] uç doğrulama"
  local path code i
  for path in /api/health /admin /api/control/summary /api/boot; do
    code=000; i=0
    while :; do
      code=$(r_read "curl -s -m 10 -o /dev/null -w '%{http_code}' $API$path" || true)
      [ "$PLAN" = 1 ] && break
      [ "$code" = 200 ] && break
      i=$((i + 1)); [ "$i" -ge 10 ] && die "$path -> HTTP $code" 2
      sleep 1
    done
    info "$path $code"
  done
  drain_set false
  if [ "$PLAN" = 0 ]; then
    r_read "curl -fsS -m 10 $API/api/control/drain" | grep -q '"on": *false' || die "drain kapanmadı" 2
    info "drain kapalı"
  fi
  DRAIN_SET=0
STAGED=0     # 1 = $NEXT exists and has not been switched in yet (discarded on failure)
}

if [ "$MODE" = rollback ]; then
  step "[R] son yedek"
  LAST=$(r_read "ls -1t $BACKUP_DIR/diziflix2-bak-*.tgz 2>/dev/null | head -n 1" || true)
  [ "$PLAN" = 1 ] && LAST="$BACKUP_DIR/diziflix2-bak-<son>.tgz"
  [ -n "$LAST" ] || die "sunucuda yedek yok ($BACKUP_DIR/diziflix2-bak-*.tgz)"
  info "$LAST"
  if [ "$YES" = 0 ] && [ "$PLAN" = 0 ]; then
    [ -t 0 ] || die "onay gerekli; --yes ile çalıştırın"
    printf 'Bu yedeğe dönülsün mü? (evet/hayır) '; read -r ans; [ "$ans" = evet ] || die "vazgeçildi"
  fi
  do_drain
  step "[R] geri aç (.env/data/venv korunur)"
  r_write "set -e; T=\$(mktemp -d); tar -C \"\$T\" -xzf '$LAST'; rsync -a --delete $R_EXCLUDES \"\$T/server2/\" '$DEST/'; rm -rf \"\$T\""
  do_restart
  do_verify
  step "geri alma tamam"
  exit 0
fi

step "[0] ön kontrol ($CHECK_CMD)"
if [ "$PLAN" = 1 ]; then show "plan yerel" "cd $SCRIPT_DIR && $CHECK_CMD"
else (cd "$SCRIPT_DIR" && $CHECK_CMD) >/dev/null 2>&1 || die "yerel check.sh yeşil değil; deploy yok"; info "yeşil"; fi

if [ "$DRY" = 0 ]; then
  step "[1] sunucuda yedek ($BACKUP)"
  r_write "if [ -d '$DEST' ]; then mkdir -p '$BACKUP_DIR' && tar -C '$PARENT' --exclude=server2/data --exclude=server2/venv --exclude=server2/.env --exclude='__pycache__' -czf '$BACKUP' server2; else echo 'ilk kurulum: yedek yok'; fi"
fi

step "[2] fark özeti"
DIFF=$(rsync_to "$DEST" -n || true)
if [ "$PLAN" = 1 ]; then printf '%s\n' "$DIFF"
else
  printf '%s\n' "$DIFF" | grep -v '/$' | head -n 40 | sed 's/^/   /' || true
  info "değişen: $(printf '%s\n' "$DIFF" | grep -c '^[<>c]' || true), silinecek: $(printf '%s\n' "$DIFF" | grep -c '^\*deleting' || true)"
fi
if [ "$DRY" = 1 ]; then step "dry-run bitti: sunucuda hiçbir şey değişmedi"; exit 0; fi

REQ=0
if printf '%s\n' "$DIFF" | grep -q 'requirements.txt'; then REQ=1; fi
IMPORT_CHECK="D=\$(mktemp -d) && DATA_DIR=\"\$D\" '$DEST/venv/bin/python' -c 'import app.main'; rc=\$?; rm -rf \"\$D\"; exit \$rc"

step "[3] hazırlık: rsync -> $NEXT (canlı dizin değişmez)"
r_write "rm -rf '$NEXT' && mkdir -p '$NEXT' && if [ -d '$DEST' ]; then rsync -a $R_EXCLUDES '$DEST/' '$NEXT/'; fi"
STAGED=1
rsync_to "$NEXT"
if [ "$REQ" = 0 ]; then   # new deps are not installed yet -> the import check then runs after [5]
  r_read "if [ -x '$DEST/venv/bin/python' ]; then cd '$NEXT' && $IMPORT_CHECK; fi" \
    || die "server2.next: import app.main başarısız; canlı dizine dokunulmadı"
  info "server2.next import tamam"
fi

do_drain

step "[5] geçiş: server2.next -> server2, kurulum + import app.main"
r_write "mkdir -p '$DEST' && rsync -a --delete $R_EXCLUDES '$NEXT/' '$DEST/' && rm -rf '$NEXT'"
STAGED=0
r_write "cd '$DEST' && if [ ! -x venv/bin/python ] || ! systemctl cat $SERVICE >/dev/null 2>&1; then ./install.sh; fi"
if [ "$REQ" = 1 ]; then r_write "cd '$DEST' && venv/bin/python -m pip install -q -r requirements.txt"; fi
r_read "cd '$DEST' && $IMPORT_CHECK" || die "sunucuda import app.main başarısız; restart edilmedi (./deploy.sh --rollback)" 2
info "import tamam"

do_restart
do_verify

step "yedek budama (son $KEEP)"
r_write "ls -1t $BACKUP_DIR/diziflix2-bak-*.tgz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -f --"
step "deploy tamam"
