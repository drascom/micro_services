#!/usr/bin/env bash
# deploy.sh - diziflix sunucusunu (server/) tar-over-ssh ile dağıtır / geri alır.
# macOS (bash 3.2 uyumlu) üzerinde çalışır. Ayrıntılar: ./deploy.sh --help
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LOCAL_SERVER="$SCRIPT_DIR/server"

DEF_HOST="root@192.168.0.61"
DEF_DEST="/root/micro_services/diziflix/server"
DEF_SERVICE="diziflix"
DEF_PORT="8090"
DEF_BACKUP_DIR="/root"
DEF_KEEP="3"

DZ_HOST=${DZ_HOST:-$DEF_HOST}
DZ_DEST=${DZ_DEST:-$DEF_DEST}
DZ_SERVICE=${DZ_SERVICE:-$DEF_SERVICE}
DZ_PORT=${DZ_PORT:-$DEF_PORT}
DZ_BACKUP_DIR=${DZ_BACKUP_DIR:-$DEF_BACKUP_DIR}   # yedeklerin durduğu sunucu dizini

# ---------------------------------------------------------------- çıktı ----
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_BLD=''; C_RST=''
fi

step() { printf '\n%s%s%s\n' "$C_BLD" "$*" "$C_RST"; }
info() { printf '  %s\n' "$*"; }
ok()   { printf '  %sOK%s     %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '  %sUYARI%s  %s\n' "$C_YEL" "$C_RST" "$*"; }
bad()  { printf '  %sHATA%s   %s\n' "$C_RED" "$C_RST" "$*"; }
die()  { printf '%sHATA%s %s\n' "$C_RED" "$C_RST" "$1" >&2; exit "${2:-1}"; }

usage() {
  cat <<EOF
diziflix deploy - server/ dizinini sunucuya tar-over-ssh ile gönderir.

Kullanım:
  ./deploy.sh [seçenekler]                dağıt (yedek, fark kontrolü, kopyala, import, restart, doğrula, eski yedekleri buda)
  ./deploy.sh --rollback [YEDEK_YOLU]     geri al (yolsuz: sunucudaki en son diziflix-server-bak-*.tgz)
  ./deploy.sh --pull-configs              deploy YOK: sunucu scraper configs'ini yerel aynaya çek (sunucuya yazmaz)

Seçenekler:
  --dry-run             yerelde nelerin gönderileceğini listeler + uzak fark kontrolü; sunucuya YAZMAZ, restart etmez
  --local-only          yalnız yerel analiz (sunucuya hiç bağlanmaz; --dry-run anlamına gelir)
  --no-restart          servisi yeniden başlatma (yeni kod restart'a kadar canlı olmaz)
  --overwrite-configs   sunucuda KORUNAN configs'i de ez (heal/elle değişmiş olabilir; 'evet' onayı ister)
  --keep-configs        hiçbir sunucu configs'ini ezme (otomatik ezme dahil kapalı; yeni site dosyaları gider)
  --sync-configs        KORU kararı verilen sitelerin sunucu config dosyalarını yerele de kopyalar, AYNI sitelerde yalnız
                        sunucuda olan dosyaları (heal arşivi) ekler (yerel aynayı tazeler; yalnız sunucu -> yerel, sunucuya
                        yazmaz; varsayılan KAPALI; yerel daha yeni olan siteye dokunmaz)
  --pull-configs        deploy yapmaz: yalnız sunucu configs'ini indirir/yedekler, sunucuda olup yerelde olmayan siteleri
                        (REMOTEONLY) yerele çeker (+ --sync-configs ile KORU siteleri); restart/tar/budama yok, sunucuya
                        HİÇBİR ŞEY yazmaz; --dry-run ile çalışır (--local-only/--overwrite-configs/--keep-configs ile olmaz)
  --no-prune            deploy sonunda eski yedekleri SİLME (varsayılan: son $DEF_KEEP yedek tutulur, eskiler silinir)
  --keep N              budamada tutulacak yedek sayısı (en az 1; DZ_KEEP_BACKUPS'ı ezer; varsayılan $DEF_KEEP)
  --prune-backups       KULLANIMDAN KALKTI, etkisiz: budama artık varsayılan (kapatmak için --no-prune)
  --rollback [YOL]      yedeği .env/data/venv hariç geri açar, restart eder, doğrular (onay ister)
  -y, --yes             soru sorma (configs ezme ve rollback onayları dahil)
  -v, --verbose         --dry-run'da dosya listesinin tamamını yaz
  -h, --help            bu yardım

Ortam değişkenleri (varsayılan):
  DZ_HOST=$DEF_HOST  DZ_DEST=$DEF_DEST
  DZ_SERVICE=$DEF_SERVICE  DZ_PORT=$DEF_PORT  DZ_BACKUP_DIR=$DEF_BACKUP_DIR  DZ_KEEP_BACKUPS=$DEF_KEEP  NO_COLOR=1 (renksiz)

Örnekler:
  ./deploy.sh --dry-run                       ne gidecek, sunucuda ne farklı? (yazmaz)
  ./deploy.sh --local-only -v                 yalnız yerel dosya listesi
  ./deploy.sh                                 tam deploy
  ./deploy.sh --keep 5                        deploy sonunda son 5 yedeği tut (eskiler silinir)
  ./deploy.sh --no-prune                      deploy et, eski yedeklere dokunma
  ./deploy.sh --keep-configs                  mevcut sunucu configs'ine hiç dokunma
  ./deploy.sh --overwrite-configs             korunan (heal'lenmiş olabilir) configs'i de ez
  ./deploy.sh --sync-configs                  deploy et + korunan sitelerin sunucu configs'ini yerele de kopyala
  ./deploy.sh --pull-configs --dry-run        sunucuda yerelde olmayan site var mı? (hiçbir şey yazmaz)
  ./deploy.sh --pull-configs [--sync-configs] yalnız sunucu configs'ini yerele çek (deploy yok)
  ./deploy.sh --rollback                      en son yedeğe dön
  ./deploy.sh --rollback /root/diziflix-server-bak-20260929-221530.tgz --yes

Scraper configs kararı (site bazında; yaml + .baseline.json birlikte; md5 farkı varsa):
  sunucu sürümü < yerel VE sunucuda o site için uygulanmış heal kaydı / yeni arşiv yok -> EZ (yerel daha yeni)
  sunucu sürümü >= yerel, ya da heal kaydı/yeni arşiv var                              -> KORU (--overwrite-configs ezer)
  sürüm ya da heal kaydı okunamadı                                                      -> KORU (belirsiz)
  yaml aynı ama .baseline.json farklı (çalışma zamanı last_good)                        -> baseline sunucuda KALIR
  sunucuda var, yerelde hiç dosyası yok (REMOTEONLY; admin'den eklenen yeni site)       -> ÇEK: <site>.yaml/.baseline.json/.v*.yaml
                                                                                           yerel configs/'e kopyalanır, o deploy'dan dışlanır
  --sync-configs: KORU kararlı siteler (KEEPVER/KEEPHEAL/BASELINE/belirsiz sürüm) için sunucu dosyaları yerele de kopyalanır;
                  --keep-configs'in AUTO kararı (yerel daha yeni) ve tanınmayan dosyalar atlanır.
  Provider tarifleri (configs/providers/<ad>.yaml + <ad>.vN.yaml; ad ^[a-z][a-z0-9_]{1,31}$) AYNI mantığa tabidir; "site" yerine
  "providers/<ad>" olarak listelenir: sunucu sürümü yerelden yüksek / heal-arşiv izi var -> KORU; sunucuda var, yerelde yok -> ÇEK
  ("yerele çekildi: provider <ad> (vN)"); yerel daha yeni ve iz yok -> EZ; yalnız yerelde -> GÖNDER. providers/ altındaki
  kalıba uymayan dosyalar tanınmayan dosya sayılır (KORU). Tarifin .baseline.json'u yoktur.
  Çekme yönü yalnız sunucu -> yerel; çekilen dosyalar git'te görünür (kontrol edip commit edin). --local-only'de REMOTEONLY hesaplanamaz.
  Sunucuda admin panelinden SİLİNMİŞ site (sunucudaki data/deleted_sites.json, salt-okunur okunur; deploy ve --pull-configs'te)
                                                                                       -> SİL: yerel <site>.yaml/.baseline.json/.v*.yaml
                                                                                          deploy-backups/local-removed-<ts>/ altına TAŞINIR ("yerelden kaldırıldı:
                                                                                          <site> (sunucuda silinmiş)"), o site tar'dan dışlanır (geri itilmez);
                                                                                          providers/ tarifleri dokunulmaz; --dry-run'da yalnız "kaldırılacaktı"
                                                                                          listesi; --local-only'de sunucu bilinmediği için yapılmaz. Sunucuda hem
                                                                                          silinmiş kaydı hem <site>.yaml varsa (tutarsız) site normal işlenir.
  Sunucudaki config'ler her durumda önce ./deploy-backups/ altına indirilir (dry-run hariç).
  Yalnız korunan dosyalar tar'dan --exclude edilir; karar tablosu dry-run'da ve deploy'da yazılır.

Yedek budama (varsayılan AÇIK; yalnız BAŞARILI deploy sonunda, restart + tüm endpoint doğrulaması geçtikten sonra):
  sunucuda  \$DZ_BACKUP_DIR/diziflix-server-bak-YYYYMMDD-HHMMSS.tgz  -> en yeni N (ad içindeki zaman damgasına göre) kalır,
            eskiler silinir; bu deploy'un yeni aldığı yedek HER ZAMAN kalanlar arasındadır.
  yerelde   ./deploy-backups/remote-configs-YYYYMMDD-HHMMSS/            -> aynı mantık, aynı N
  Yalnız bu KESİN ad kalıpları silinir. Dokunulmaz: diziflix-server-prerollback-*, diziflix-db-bak-*, data/diziflix.db.bak.*,
  ad kalıbına uymayan dosyalar (yalnız sayıları "bilgi" olarak yazılır). Silme hatası deploy'u başarısız saymaz (uyarı).
  Budama OLMAZ: başarısız/doğrulanamayan deploy, import hatası, --rollback, --no-restart (yeni kod doğrulanmadı), --no-prune.
  --dry-run / --local-only: hiçbir şey silinmez; yalnız "şunlar silinecekti" listesi (sunucuda salt-okunur listeleme) gösterilir.

Çıkış kodları: 0 başarılı | 1 hata (ssh/import/kopyalama) | 2 deploy sonrası doğrulama başarısız
Sunucuda unittest, ingest, scraper, backfill ÇALIŞTIRILMAZ; .env içeriği yazdırılmaz.
EOF
}

# ------------------------------------------------------------ argümanlar ---
MODE=deploy
DRY_RUN=0
LOCAL_ONLY=0
NO_RESTART=0
OVERWRITE_CONFIGS=0
KEEP_CONFIGS=0
SYNC_CONFIGS=0         # KORU kararlı sitelerin sunucu dosyalarını yerele de kopyala (varsayılan KAPALI)
PULL_ONLY=0            # --pull-configs: deploy yok, yalnız sunucu configs -> yerel
PRUNE=1                # eski yedekleri budama (varsayılan AÇIK; --no-prune kapatır)
PRUNE_DEPRECATED=0     # eski --prune-backups verildi (etkisiz)
KEEP_ARG=""; KEEP_ARG_SET=0
YES=0
VERBOSE=0
ROLLBACK_PATH=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)           DRY_RUN=1 ;;
    --local-only)        LOCAL_ONLY=1; DRY_RUN=1 ;;
    --no-restart)        NO_RESTART=1 ;;
    --overwrite-configs) OVERWRITE_CONFIGS=1 ;;
    --keep-configs)      KEEP_CONFIGS=1 ;;
    --sync-configs)      SYNC_CONFIGS=1 ;;
    --pull-configs)      PULL_ONLY=1 ;;
    --no-prune)          PRUNE=0 ;;
    --prune-backups)     PRUNE_DEPRECATED=1 ;;
    --keep)
      [ $# -gt 1 ] || die "--keep bir sayı ister (örn. --keep 3)"
      KEEP_ARG=$2; KEEP_ARG_SET=1; shift
      ;;
    --keep=*)            KEEP_ARG=${1#--keep=}; KEEP_ARG_SET=1 ;;
    -y|--yes)            YES=1 ;;
    -v|--verbose)        VERBOSE=1 ;;
    -h|--help)           usage; exit 0 ;;
    --rollback)
      MODE=rollback
      if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then ROLLBACK_PATH=$2; shift; fi
      ;;
    *) die "bilinmeyen seçenek: $1 (yardım: ./deploy.sh --help)" ;;
  esac
  shift
done

# ------------------------------------------------------ ayar doğrulama -----
valid() { # valid DEĞER REGEX ADI
  if ! printf '%s' "$1" | grep -Eq "$2"; then die "$3 geçersiz: '$1'"; fi
}
while [ "${DZ_DEST%/}" != "$DZ_DEST" ]; do DZ_DEST=${DZ_DEST%/}; done
while [ "${DZ_BACKUP_DIR%/}" != "$DZ_BACKUP_DIR" ]; do DZ_BACKUP_DIR=${DZ_BACKUP_DIR%/}; done
valid "$DZ_HOST" '^[A-Za-z0-9][A-Za-z0-9._@:-]*$' DZ_HOST
valid "$DZ_DEST" '^/[A-Za-z0-9._/-]+$' DZ_DEST
valid "$DZ_SERVICE" '^[A-Za-z0-9][A-Za-z0-9._@-]*$' DZ_SERVICE
valid "$DZ_PORT" '^[0-9]{1,5}$' DZ_PORT
valid "$DZ_BACKUP_DIR" '^/[A-Za-z0-9._/-]+$' DZ_BACKUP_DIR
# tutulacak yedek sayısı: --keep > DZ_KEEP_BACKUPS > varsayılan; sayı olmalı ve >= 1 (1'in altına İNDİRİLEMEZ)
if [ "$KEEP_ARG_SET" = 1 ]; then KEEP=$KEEP_ARG; KEEP_SRC="--keep"; else KEEP=${DZ_KEEP_BACKUPS:-$DEF_KEEP}; KEEP_SRC="DZ_KEEP_BACKUPS"; fi
printf '%s' "$KEEP" | grep -Eq '^[0-9]{1,4}$' || die "$KEEP_SRC geçersiz: '$KEEP' (1 veya daha büyük bir tam sayı olmalı; en az 1 yedek her zaman tutulur)"
KEEP=$((10#$KEEP))
[ "$KEEP" -ge 1 ] || die "$KEEP_SRC en az 1 olmalı (0 verilemez; yedekleri silmek istemiyorsanız --no-prune kullanın)"
[ "$(basename "$DZ_DEST")" = "server" ] || die "DZ_DEST 'server' ile bitmeli (yerel dizin adı server/): $DZ_DEST"
PARENT=$(dirname "$DZ_DEST")
if [ -n "$ROLLBACK_PATH" ]; then valid "$ROLLBACK_PATH" '^/[A-Za-z0-9._/-]+$' "yedek yolu"; fi
if [ "$MODE" = rollback ] && [ "$LOCAL_ONLY" = 1 ]; then die "--local-only, --rollback ile kullanılamaz"; fi
if [ "$OVERWRITE_CONFIGS" = 1 ] && [ "$KEEP_CONFIGS" = 1 ]; then die "--overwrite-configs ve --keep-configs birlikte kullanılamaz"; fi
if [ "$PULL_ONLY" = 1 ]; then
  [ "$MODE" = deploy ] || die "--pull-configs, --rollback ile kullanılamaz"
  [ "$LOCAL_ONLY" = 0 ] || die "--pull-configs sunucuya bağlanır; --local-only ile kullanılamaz"
  [ "$OVERWRITE_CONFIGS" = 0 ] && [ "$KEEP_CONFIGS" = 0 ] || die "--pull-configs deploy yapmaz; --overwrite-configs/--keep-configs ile kullanılamaz"
  MODE=pull
fi
# onay gerektiren işlemler etkileşimsiz ortamda baştan reddedilir (yarım iş bırakmasın)
if [ "$YES" = 0 ] && [ "$DRY_RUN" = 0 ] && [ ! -t 0 ] && { [ "$MODE" = rollback ] || [ "$OVERWRITE_CONFIGS" = 1 ]; }; then
  die "onay gerekli ama etkileşimli terminal yok; --yes ile tekrar çalıştırın"
fi

TS=$(date +%Y%m%d-%H%M%S)
BACKUP_PATH="$DZ_BACKUP_DIR/diziflix-server-bak-$TS.tgz"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/dz-deploy.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
trap 'printf "\nKesildi; işlem yarım kalmış olabilir.\n" >&2; exit 130' INT TERM

# ------------------------------------------------------------ ssh yardım ---
RSSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
rssh()    { ssh -n "${RSSH_OPTS[@]}" "$DZ_HOST" "$@"; }   # stdin kullanmayan komut
rssh_in() { ssh "${RSSH_OPTS[@]}" "$DZ_HOST" "$@"; }      # stdin'i uzağa aktarır
# rscript ARG...: stdin'deki betiği uzakta `bash -s` ile çalıştırır; ARG'lar $1.. olur
# (ARG'lar doğrulanmış, boşluksuz değerlerdir).
rscript() { rssh_in "bash -s -- $*"; }

md5_of() {
  if command -v md5sum >/dev/null 2>&1; then md5sum "$1" | awk '{print $1}'; else md5 -q "$1"; fi
}

confirm() { # confirm "soru" -> 0 = evet
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || die "onay gerekli ama etkileşimli terminal yok; --yes ile tekrar çalıştırın"
  local ans=""
  printf '  %s [evet/hayır]: ' "$1"
  read -r ans || ans=""
  ans=$(printf '%s' "$ans" | tr 'A-Z' 'a-z')
  [ "$ans" = "evet" ]
}

rollback_cmd() { # rollback_cmd YEDEK_YOLU
  local pre=""
  [ "$DZ_HOST" = "$DEF_HOST" ]             || pre="${pre}DZ_HOST=$DZ_HOST "
  [ "$DZ_DEST" = "$DEF_DEST" ]             || pre="${pre}DZ_DEST=$DZ_DEST "
  [ "$DZ_SERVICE" = "$DEF_SERVICE" ]       || pre="${pre}DZ_SERVICE=$DZ_SERVICE "
  [ "$DZ_PORT" = "$DEF_PORT" ]             || pre="${pre}DZ_PORT=$DZ_PORT "
  [ "$DZ_BACKUP_DIR" = "$DEF_BACKUP_DIR" ] || pre="${pre}DZ_BACKUP_DIR=$DZ_BACKUP_DIR "
  printf '%s./deploy.sh --rollback %s' "$pre" "$1"
}

CURRENT_BACKUP=""
show_rollback_hint() {
  if [ -n "$CURRENT_BACKUP" ]; then info "Geri almak için:  $(rollback_cmd "$CURRENT_BACKUP")"; fi
  return 0
}

# ---------------------------------------------------------- ön kontrol -----
local_preflight() {
  [ -d "$LOCAL_SERVER" ] || die "yerelde server/ dizini yok: $LOCAL_SERVER"
  [ -f "$LOCAL_SERVER/app/main.py" ] || die "server/app/main.py yok; doğru dizinde misiniz? ($SCRIPT_DIR)"
  [ -f "$LOCAL_SERVER/requirements.txt" ] || die "server/requirements.txt yok"
  command -v tar >/dev/null 2>&1 || die "yerelde tar yok"
  ok "yerel server/ dizini: $LOCAL_SERVER"
}

TMDB_STATE=""
getv() { sed -n "s/^$1=//p" "$TMP/pre" | head -n 1; }

remote_preflight() { # remote_preflight [pull]  (pull: servis/venv/.env gerekmez)
  local err k mode=${1:-deploy}
  command -v ssh >/dev/null 2>&1 || die "yerelde ssh yok"
  if ! err=$(rssh true 2>&1); then
    printf '%s\n' "$err" | tail -n 5 | sed 's/^/      /' >&2
    die "SSH erişimi yok: $DZ_HOST (BatchMode: parola sorulmaz; anahtar/ağ/DZ_HOST kontrol edin). Hiçbir şey değiştirilmedi."
  fi
  ok "ssh erişimi: $DZ_HOST"

  rscript "$DZ_DEST" "$DZ_SERVICE" > "$TMP/pre" <<'EOF' || die "uzak ön kontrol çalıştırılamadı"
dest=$1; svc=$2; envf="$1/.env"
hk() { # hk ANAHTAR: .env'de boş olmayan değer var mı (değer ASLA yazdırılmaz)
  [ -f "$envf" ] || return 1
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1[[:space:]]*=//p" "$envf" | tr -d "\"' \r\t" | grep -q .
}
[ -d "$dest" ] && echo dest=ok || echo dest=missing
[ -x "$dest/venv/bin/python" ] && echo venv=ok || echo venv=missing
[ -f "$envf" ] && echo env=ok || echo env=missing
if hk TMDB_ACCESS_KEY; then echo tmdb=var
elif hk TMDB_TOKEN; then echo tmdb=alt:TMDB_TOKEN
elif hk TMDB_API_KEY; then echo tmdb=alt:TMDB_API_KEY
else echo tmdb=yok; fi
command -v md5sum >/dev/null 2>&1 && echo md5=ok || echo md5=missing
command -v curl >/dev/null 2>&1 && echo curl=ok || echo curl=missing
systemctl cat "$svc" >/dev/null 2>&1 && echo unit=ok || echo unit=missing
echo "active=$(systemctl is-active "$svc" 2>/dev/null || true)"
exit 0
EOF

  [ "$(getv dest)" = ok ] || die "sunucuda $DZ_DEST yok (ilk kurulum için sunucuda server/install.sh). Hiçbir şey değiştirilmedi."
  ok "sunucuda $DZ_DEST var"
  if [ "$mode" != pull ]; then
    [ "$(getv venv)" = ok ] || die "sunucuda $DZ_DEST/venv/bin/python yok; önce sunucuda install.sh çalıştırın"
  fi
  [ "$(getv md5)" = ok ] || die "sunucuda md5sum yok"
  if [ "$mode" = pull ]; then return 0; fi
  [ "$(getv curl)" = ok ] || warn "sunucuda curl yok; doğrulama tablosu başarısız görünecek"
  if [ "$(getv unit)" != ok ]; then
    if [ "$NO_RESTART" = 1 ] || [ "$DRY_RUN" = 1 ]; then
      warn "systemd servisi '$DZ_SERVICE' bulunamadı"
    else
      die "sunucuda systemd servisi '$DZ_SERVICE' yok (DZ_SERVICE doğru mu? ya da --no-restart)"
    fi
  else
    info "servis $DZ_SERVICE: $(getv active)"
  fi
  [ "$(getv env)" = ok ] || warn "sunucuda $DZ_DEST/.env yok"
  k=$(getv tmdb)
  case "$k" in
    var)   info "TMDB_ACCESS_KEY (sunucu .env): var" ;;
    alt:*) info "TMDB_ACCESS_KEY (sunucu .env): yok (alternatif ${k#alt:} var)"; TMDB_STATE=alt ;;
    *)     info "TMDB_ACCESS_KEY (sunucu .env): yok"; TMDB_STATE=yok ;;
  esac
}

# ---------------------------------------------------------------- yedek ----
make_backup() { # make_backup HEDEF_YOL  (sunucuda; .env dahil olduğu için 0600)
  local target=$1
  rscript "$PARENT" "$target" > "$TMP/bak.out" <<'EOF' || die "yedek alınamadı: $target"
umask 077
rc=0
tar --exclude=venv --exclude=data/imgcache -czf "$2" -C "$1" server || rc=$?
# rc=1: okunurken değişen dosya (canlı sqlite/log) - kabul; >1: gerçek hata
if [ "$rc" -gt 1 ]; then rm -f "$2"; echo "tar hata kodu $rc" >&2; exit 1; fi
tar tzf "$2" >/dev/null || { echo "yedek doğrulanamadı" >&2; exit 1; }
chmod 600 "$2"
du -h "$2" | cut -f1
EOF
  ok "yedek: $target ($(tail -n 1 "$TMP/bak.out"))"
}

# ------------------------------------------------------------ yedek budama --
# Varsayılan: BAŞARILI deploy sonunda (restart + doğrulama geçtikten sonra) sunucuda ($DZ_BACKUP_DIR)
# ve yerelde (deploy-backups/) yalnız EN YENİ $KEEP yedek tutulur. Sıralama dosya ADINDAKİ zaman
# damgasına göre (mtime'a güvenilmez); bu deploy'un yedeği her zaman korunur; yalnız KESİN ad kalıbı
# silinir. Karar yerelde tek yerde (prune_select) verilir; sunucu yalnız listeler ve adı verilenleri siler.
# Budama hatası deploy'u ASLA başarısız saymaz (uyarı yazılır).
CFGDIR_NAME_RE='^remote-configs-[0-9]{8}-[0-9]{6}$'

fmt_mb()    { awk -v b="${1:-0}" 'BEGIN { printf "%.1f", b / 1048576 }'; }
short_name() { sed -e 's/^diziflix-server-bak-//' -e 's/\.tgz$//' -e 's/^remote-configs-//'; }
join_csv()  { awk '{ printf "%s%s", (NR > 1 ? ", " : ""), $0 } END { if (NR == 0) printf "(yok)" }'; }
has_name()  { awk -v c="$2" '$1 == c { f = 1 } END { exit !f }' "$1"; }   # has_name DOSYA AD

# prune_select DOSYA("ad boyut" satırları) KEEP CUR -> stdout: "K|D ad boyut" (yeni -> eski)
# En yeni KEEP ad kalır; CUR (bu deploy'un yedeği) varsa her zaman kalır ve kontenjandan sayılır.
prune_select() {
  LC_ALL=C sort -r -k1,1 "$1" | awk -v keep="$2" -v cur="$3" '
    { nm[NR] = $1; sz[NR] = $2; n = NR }
    END {
      left = keep
      for (i = 1; i <= n; i++) if (nm[i] == cur) { st[i] = "K"; left-- }
      for (i = 1; i <= n; i++) {
        if (st[i] == "K") continue
        if (left > 0) { st[i] = "K"; left-- } else st[i] = "D"
      }
      for (i = 1; i <= n; i++) print st[i], nm[i], sz[i]
    }'
}

# prune_report plan|done ETIKET SEL [RES]
#   SEL: prune_select çıktısı; RES: silme sonuçları ("DEL ad boyut" / "FAIL ad")
prune_report() {
  local mode=$1 label=$2 sel=$3 res=${4:-/dev/null} nd nf bytes kept n b
  if [ "$mode" = plan ]; then
    nd=$(awk '$1 == "D" { n++ } END { print n + 0 }' "$sel")
    bytes=$(awk '$1 == "D" { b += $3 } END { print b + 0 }' "$sel")
    kept=$(awk '$1 == "K" { print $2 }' "$sel" | short_name | join_csv)
    if [ "$nd" = 0 ]; then
      info "$label: silinecek eski yedek yok (kalacak: $kept)"
    else
      info "$label: $nd yedek silinecekti, $(fmt_mb "$bytes") MB boşalacaktı (dry-run: SİLİNMEDİ); kalacak: $kept"
      awk '$1 == "D" { print $2, $3 }' "$sel" | while read -r n b; do
        printf '        silinecekti: %s (%s MB)\n' "$n" "$(fmt_mb "$b")"
      done
    fi
    return 0
  fi
  nd=$(awk '$1 == "DEL" { n++ } END { print n + 0 }' "$res")
  nf=$(awk '$1 == "FAIL" { n++ } END { print n + 0 }' "$res")
  bytes=$(awk '$1 == "DEL" { b += $3 } END { print b + 0 }' "$res")
  kept=$(awk 'FILENAME == ARGV[1] { if ($1 == "DEL") gone[$2] = 1; next } !($2 in gone) { print $2 }' "$res" "$sel" | short_name | join_csv)
  if [ "$nd" = 0 ] && [ "$nf" = 0 ]; then
    info "$label: silinecek eski yedek yok (kalan: $kept)"
  else
    if [ "$nd" != 0 ]; then
      ok "$label: $nd yedek silindi, $(fmt_mb "$bytes") MB boşaldı; kalan: $kept"
      awk '$1 == "DEL" { print $2, $3 }' "$res" | while read -r n b; do
        printf '        silindi: %s (%s MB)\n' "$n" "$(fmt_mb "$b")"
      done
    fi
    if [ "$nf" != 0 ]; then
      warn "$label: $nf yedek SİLİNEMEDİ (deploy başarısız sayılmadı): $(awk '$1 == "FAIL" { print $2 }' "$res" | tr '\n' ' ')"
      info "kalan: $kept"
    fi
  fi
}

# prune_remote plan|apply  -> sunucudaki diziflix-server-bak-*.tgz; her zaman 0 döner
prune_remote() {
  local mode=$1 cur dels pre dbb dat nom
  cur=$(basename "$BACKUP_PATH")
  info "sunucu: $DZ_BACKUP_DIR/diziflix-server-bak-*.tgz (zaman damgasına göre son $KEEP kalır; bu deploy'un yedeği hep korunur)"
  # 1) salt-okunur listeleme: B <ad> <bayt> (kesin kalıp) | N <ad> (kalıba uymayan) | I <tür> <sayı> (bilgi)
  rscript "$DZ_BACKUP_DIR" "$DZ_DEST" > "$TMP/pr.raw" <<'EOF' || { warn "sunucudaki yedekler listelenemedi; budama yapılmadı (deploy başarılı sayıldı)"; return 0; }
dir=$1; dest=$2
cd "$dir" 2>/dev/null || { echo "yedek dizini yok: $dir" >&2; exit 1; }
cnt() { find "$1" -maxdepth 1 -name "$2" 2>/dev/null | grep -c . || true; }
find . -maxdepth 1 -name 'diziflix-server-bak-*' 2>/dev/null | sed 's#^\./##' | LC_ALL=C sort |
while IFS= read -r f; do
  if printf '%s\n' "$f" | grep -Eq '^diziflix-server-bak-[0-9]{8}-[0-9]{6}\.tgz$' && [ -f "$f" ] && [ ! -L "$f" ]; then
    echo "B $f $(wc -c < "$f" | tr -d ' ')"
  else
    echo "N $f"
  fi
done
echo "I prerollback $(cnt . 'diziflix-server-prerollback-*')"
echo "I dbbak $(cnt . 'diziflix-db-bak-*')"
echo "I databak $(cnt "$dest/data" 'diziflix.db.bak.*')"
exit 0
EOF
  grep -E '^B diziflix-server-bak-[0-9]{8}-[0-9]{6}\.tgz [0-9]+$' "$TMP/pr.raw" | awk '{ print $2, $3 }' > "$TMP/pr.items" || true
  nom=$(grep -c '^N ' "$TMP/pr.raw" || true)
  pre=$(sed -n 's/^I prerollback \([0-9][0-9]*\)$/\1/p' "$TMP/pr.raw" | head -n 1)
  dbb=$(sed -n 's/^I dbbak \([0-9][0-9]*\)$/\1/p' "$TMP/pr.raw" | head -n 1)
  dat=$(sed -n 's/^I databak \([0-9][0-9]*\)$/\1/p' "$TMP/pr.raw" | head -n 1)
  info "bilgi (ASLA silinmez): prerollback ${pre:-?}, db-bak ${dbb:-?}, data/diziflix.db.bak.* ${dat:-?}, ad kalıbına uymayan diziflix-server-bak-* ${nom:-0}"
  if ! has_name "$TMP/pr.items" "$cur"; then
    if [ "$mode" = plan ]; then
      echo "$cur 0" >> "$TMP/pr.items"   # dry-run: bu deploy'un alacağı yedek var sayılır
    else
      warn "bu deploy'un yedeği ($cur) sunucuda görünmüyor; GÜVENLİK: hiçbir yedek silinmedi"
      return 0
    fi
  fi
  prune_select "$TMP/pr.items" "$KEEP" "$cur" > "$TMP/pr.sel"
  : > "$TMP/pr.res"
  if [ "$mode" = plan ]; then prune_report plan sunucu "$TMP/pr.sel"; return 0; fi
  # 2) adı verilen eski yedekleri sil (sunucu her adı yeniden doğrular; cur'a asla dokunmaz)
  dels=$(awk '$1 == "D" { printf " %s", $2 }' "$TMP/pr.sel")
  if [ -n "$dels" ]; then
    # shellcheck disable=SC2086
    rscript "$DZ_BACKUP_DIR" "$cur" $dels > "$TMP/pr.res" <<'EOF' || { warn "eski sunucu yedekleri silinirken hata oluştu (deploy başarılı sayıldı)"; return 0; }
dir=$1; cur=$2; shift 2
cd "$dir" 2>/dev/null || exit 1
[ -f "$cur" ] && [ ! -L "$cur" ] || exit 1
for f in "$@"; do
  printf '%s\n' "$f" | grep -Eq '^diziflix-server-bak-[0-9]{8}-[0-9]{6}\.tgz$' || continue
  [ "$f" != "$cur" ] || continue
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  sz=$(wc -c < "$f" | tr -d ' ')
  if rm -f -- "$f" && [ ! -e "$f" ]; then echo "DEL $f $sz"; else echo "FAIL $f"; fi
done
exit 0
EOF
  fi
  prune_report done sunucu "$TMP/pr.sel" "$TMP/pr.res"
  return 0
}

# prune_local plan|apply -> ./deploy-backups/remote-configs-YYYYMMDD-HHMMSS (yalnız bu kesin ad kalıbı)
prune_local() {
  local mode=$1 base="$SCRIPT_DIR/deploy-backups" cur="" f kb st name sz
  [ -d "$base" ] || return 0
  if [ "$mode" = apply ]; then
    if [ -n "$CFG_BACKUP_DIR" ]; then cur=$(basename "$CFG_BACKUP_DIR"); fi
  elif [ "$CFG_WILL_DOWNLOAD" = 1 ]; then
    cur="remote-configs-$TS"
  fi
  info "yerel: deploy-backups/remote-configs-* (zaman damgasına göre son $KEEP kalır)"
  ls -1 "$base" 2>/dev/null | grep -E "$CFGDIR_NAME_RE" | while IFS= read -r f; do
    [ -d "$base/$f" ] && [ ! -L "$base/$f" ] || continue
    kb=$(du -sk "$base/$f" 2>/dev/null | cut -f1)
    echo "$f $(( ${kb:-0} * 1024 ))"
  done > "$TMP/pl.items" || true
  if [ -n "$cur" ] && [ "$mode" = plan ] && ! has_name "$TMP/pl.items" "$cur"; then
    echo "$cur 0" >> "$TMP/pl.items"   # dry-run: bu deploy'un indireceği klasör var sayılır
  fi
  prune_select "$TMP/pl.items" "$KEEP" "$cur" > "$TMP/pl.sel"
  : > "$TMP/pl.res"
  if [ "$mode" = plan ]; then prune_report plan yerel "$TMP/pl.sel"; return 0; fi
  while read -r st name sz; do
    [ "$st" = D ] || continue
    printf '%s\n' "$name" | grep -Eq "$CFGDIR_NAME_RE" && [ -d "$base/$name" ] && [ ! -L "$base/$name" ] || continue
    if rm -rf -- "${base:?}/$name" && [ ! -e "$base/$name" ]; then echo "DEL $name $sz" >> "$TMP/pl.res"; else echo "FAIL $name" >> "$TMP/pl.res"; fi
  done < "$TMP/pl.sel"
  prune_report done yerel "$TMP/pl.sel" "$TMP/pl.res"
  return 0
}

# Başarılı deploy'un sonu (restart + doğrulama geçti): sunucu + yerel budama
do_prune_backups() {
  step "Yedek budama (son $KEEP yedek tutulur)"
  if [ "$PRUNE" = 0 ]; then info "--no-prune: eski yedekler silinmedi"; return 0; fi
  if [ "$NO_RESTART" = 1 ]; then
    info "--no-restart: yeni kod canlı/doğrulanmış değil; eski yedekler silinmedi (sonraki tam deploy'da budanır)"
    return 0
  fi
  prune_remote apply
  prune_local apply
  return 0
}

# --dry-run / --local-only: hiçbir şey silinmez; "şunlar silinecekti" listesi (uzak yalnız salt-okunur listeleme)
prune_plan_section() {
  step "Yedek budama planı (dry-run: hiçbir şey SİLİNMEZ; deploy sonunda son $KEEP yedek tutulur)"
  if [ "$PRUNE" = 0 ]; then info "--no-prune: deploy'da eski yedekler silinmeyecek"; return 0; fi
  if [ "$NO_RESTART" = 1 ]; then info "--no-restart: deploy'da eski yedekler silinmeyecek (yeni kod doğrulanmaz)"; return 0; fi
  if [ "$LOCAL_ONLY" = 1 ]; then info "(local-only) sunucu yedekleri listelenmedi"; else prune_remote plan; fi
  prune_local plan
  return 0
}

# -------------------------------------------------------- fark kontrolü ----
REQ_CHANGED=0
CFG_BACKUP_DIR=""
CFG_WILL_DOWNLOAD=0        # bu deploy sunucu configs'ini deploy-backups/'a indirecek (dry-run'da: indirirdi)
CFG_KEPT=""                # korunan (ezilmeyen) siteler, başında boşlukla
LOCAL_CFG="$LOCAL_SERVER/app/scraper/configs"
CFG_EXCL="$TMP/cfg.excl"   # tar'dan dışlanacak config dosyaları (configs/ göreli), satır başına bir
: > "$CFG_EXCL"

yaml_version() { # yaml_version DOSYA -> üst düzey 'version:' sayısı, okunamazsa ?
  local v
  v=$(grep '^version:' "$1" 2>/dev/null | head -n 1 | tr -d "\"' \r\t" |
      sed -n 's/^version:\([0-9][0-9]*\)\(#.*\)\{0,1\}$/\1/p') || true
  printf '%s' "${v:-?}"
}

# Çıktı satırları:  F <md5> ./dosya   |   V ./x.yaml <sürüm|?>
local_config_list() {
  ( cd "$LOCAL_CFG" 2>/dev/null || exit 0
    find . -type f ! -name '._*' ! -name '.DS_Store' ! -path '*__pycache__*' | LC_ALL=C sort |
    while IFS= read -r f; do printf 'F %s %s\n' "$(md5_of "$f")" "$f"; done
    for f in ./*.yaml ./providers/*.yaml; do
      [ -f "$f" ] || continue
      printf 'V %s %s\n' "$f" "$(yaml_version "$f")"
    done )
}

# Karar girdisi: yerel liste ($TMP/cfg.local) + sunucu listesi ($TMP/cfg.remote).
# Sunucu listesi (salt-okunur, gizli/token yazdırmaz): F/V satırları + heal özeti:
#   H <site> <toplam> <uygulanan>   (site için heal kaydı; '?' = okunamadı)
#   HS ok|err                        (heal geçmişi okunabildi mi)
# Çıktı ($TMP/cfg.dec):
#   D <site> <SINIF> <yerel-sürüm> <sunucu-sürüm> <heal-uygulanan> <heal-toplam> <yeni-arşiv>
#   Fl <site> <dosya>                (site grubunda sunucudan farklı / yalnız-yerel dosya)
#   D <site> REMOTEONLY ? <sunucu-sürüm> 0 0 0   (sunucuda var, yerelde hiç dosyası yok)
#   Fr <site> <dosya>                (yerele çekilebilir sunucu dosyası: REMOTEONLY'de hepsi, diğerlerinde farklı/yerelde yok)
#   DS <site> / DSS ok|err           (admin'den silinmiş siteler: sunucudaki deleted_sites.json; DSS ok değilse yok sayılır)
# SINIF: SAME NEWSITE AUTO KEEPVER KEEPHEAL UNKVER UNKHEAL UNKFILE BASELINE LOCALONLY REMOTEONLY DELETED
#   DELETED: sunucuda silinmiş (kayıtlı) site, sunucuda <site>.yaml yok, yerelde dosyası var -> yerelden kaldırılır
cfg_classify() { # cfg_classify LOCALONLY(0|1)
  awk -v localonly="${1:-0}" '
    # provider tarifi: providers/<ad>.yaml | providers/<ad>.vN.yaml, ad ^[a-z][a-z0-9_]{1,31}$ (aralık ifadesi kullanılmaz: eski awk)
    function recipe_ok(n,   b) {
      b = n; sub(/^providers\//, "", b); sub(/(\.v[0-9]+)?\.yaml$/, "", b)
      return (b ~ /^[a-z][a-z0-9_]+$/ && length(b) >= 2 && length(b) <= 32)
    }
    function safe(f,   n) {
      if (f ~ /^\.\/[A-Za-z0-9_-]+(\.baseline\.json|\.v[0-9]+\.yaml|\.yaml)$/) return 1
      n = f; sub(/^\.\//, "", n)
      return (n ~ /^providers\/[^\/]+(\.v[0-9]+)?\.yaml$/ && recipe_ok(n))
    }
    function classify(f,   n) {
      n = f; sub(/^\.\//, "", n)
      CT = "other"; CS = n; CK = "other:" n
      if (n ~ /^providers\//) {   # provider tarifleri: "site" anahtarı providers/<ad>; kalıba uymayanlar tanınmayan dosya
        if (n ~ /^providers\/[^\/]+\.v[0-9]+\.yaml$/ && recipe_ok(n))   { CT = "arch"; CS = n; sub(/\.v[0-9]+\.yaml$/, "", CS); CK = CS }
        else if (n ~ /^providers\/[^\/]+\.yaml$/ && recipe_ok(n))         { CT = "yaml"; CS = n; sub(/\.yaml$/, "", CS); CK = CS }
        return
      }
      if (n ~ /^[^\/]+\.v[0-9]+\.yaml$/)      { CT = "arch";     CS = n; sub(/\.v[0-9]+\.yaml$/, "", CS); CK = CS }
      else if (n ~ /^[^\/]+\.baseline\.json$/) { CT = "baseline"; CS = n; sub(/\.baseline\.json$/, "", CS); CK = CS }
      else if (n ~ /^[^\/]+\.yaml$/)           { CT = "yaml";     CS = n; sub(/\.yaml$/, "", CS); CK = CS }
    }
    FILENAME == ARGV[1] {
      if ($1 == "F" && NF == 3) {
        lm[$3] = $2; classify($3)
        if (!(CK in seen)) { seen[CK] = 1; order[++nsite] = CK; disp[CK] = CS }
        lfiles[CK] = lfiles[CK] " " $3
      } else if ($1 == "V" && NF == 3) lv[$2] = $3
      next
    }
    {
      if ($1 == "F" && NF == 3) {
        rm[$3] = $2; classify($3)
        if (!(CK in rseen)) { rseen[CK] = 1; rorder[++nrsite] = CK; rdisp[CK] = CS }
        rfiles[CK] = rfiles[CK] " " $3
      }
      else if ($1 == "V" && NF == 3) rv[$2] = $3
      else if ($1 == "HS") hs = $2
      else if ($1 == "DSS") dss = $2
      else if ($1 == "DS" && NF == 2) ds[$2] = 1
      else if ($1 == "H" && NF >= 3) { hsite[$2] = 1; ht[$2] = $3; ha[$2] = $4 }
    }
    END {
      # sunucuda olup yerelde aynısı bulunmayan arşivler (<site>.v<N>.yaml) = heal izi
      for (f in rm) { classify(f); if (CT == "arch" && (!(f in lm) || lm[f] != rm[f])) an[CK]++ }
      for (i = 1; i <= nsite; i++) {
        k = order[i]; s = disp[k]; y = "./" s ".yaml"
        n = split(lfiles[k], fl, " ")
        nd = 0; nl = 0
        for (j = 1; j <= n; j++) {
          f = fl[j]
          if (!(f in rm)) { nl++; st[j] = "L" } else if (rm[f] != lm[f]) { nd++; st[j] = "D" } else st[j] = "S"
        }
        lvv = (y in lv) ? lv[y] : "?"
        rvv = (y in rv) ? rv[y] : "?"
        if (hs != "ok" || ((s in hsite) && ht[s] == "?")) { hA = "?"; hT = "?" }
        else { hA = (s in hsite) ? ha[s] : 0; hT = (s in hsite) ? ht[s] : 0 }
        a = (k in an) ? an[k] : 0
        if (localonly)                                         cls = "LOCALONLY"
        else if (dss == "ok" && (s in ds) && !(y in rm))       cls = "DELETED"
        else if (nd + nl == 0)                                 cls = "SAME"
        else if (!(y in rm) && (y in lm) && nd == 0)           cls = "NEWSITE"
        else if (k ~ /^other:/)                                cls = "UNKFILE"
        else if (!((y in lm) && (y in rm) && lm[y] != rm[y]))  cls = "BASELINE"
        else if (lvv !~ /^[0-9]+$/ || rvv !~ /^[0-9]+$/)       cls = "UNKVER"
        else if (rvv + 0 >= lvv + 0)                           cls = "KEEPVER"
        else if (hA == "?")                                    cls = "UNKHEAL"
        else if (hA + 0 > 0 || a > 0)                          cls = "KEEPHEAL"
        else                                                   cls = "AUTO"
        printf "D %s %s %s %s %s %s %d\n", s, cls, lvv, rvv, hA, hT, a
        for (j = 1; j <= n; j++) if (st[j] != "S") printf "Fl %s %s\n", s, fl[j]
      }
      # sunucu grupları: yerelde hiç dosyası olmayan site = REMOTEONLY (yalnız güvenli ad kalıbı + yaml var);
      # yerelde olan sitede sunucunun farklı/yerelde olmayan dosyaları yalnız aday (Fr) olarak yazılır
      for (i = 1; i <= nrsite; i++) {
        k = rorder[i]
        if (k ~ /^other:/) continue
        s = rdisp[k]; y = "./" s ".yaml"
        n = split(rfiles[k], rf, " ")
        if (!(k in seen)) {
          if (localonly || !(y in rm)) continue
          good = 1
          for (j = 1; j <= n; j++) if (!safe(rf[j])) good = 0
          if (!good) continue
          printf "D %s REMOTEONLY ? %s 0 0 0\n", s, ((y in rv) ? rv[y] : "?")
          for (j = 1; j <= n; j++) printf "Fr %s %s\n", s, rf[j]
        } else {
          for (j = 1; j <= n; j++)
            if (safe(rf[j]) && (!(rf[j] in lm) || lm[rf[j]] != rm[rf[j]])) printf "Fr %s %s\n", s, rf[j]
        }
      }
    }' "$TMP/cfg.local" "$TMP/cfg.remote" > "$TMP/cfg.dec"
}

cfg_ver() { case "$1" in ''|'?'|-) printf '?' ;; *) printf 'v%s' "$1" ;; esac; }

# --sync-configs'in sunucu dosyalarını yerele kopyalayabildiği KORU sınıfları (AUTO = yerel daha yeni, UNKFILE = site değil: atlanır)
cfg_syncable() { case "$1" in KEEPVER|KEEPHEAL|BASELINE|UNKVER|UNKHEAL) return 0 ;; *) return 1 ;; esac; }

cfg_row_text() { # cfg_row_text SINIF EYLEM YEREL SUNUCU
  local cls=$1 act=$2 lvs rvs
  lvs=$(cfg_ver "$3"); rvs=$(cfg_ver "$4")
  if [ "$MODE" = pull ]; then   # --pull-configs: hiçbir şey gönderilmez; yalnız yerele çekme kararı
    case "$cls" in
      SAME)       printf 'AYNI' ;;
      REMOTEONLY) printf 'ÇEK     sunucuda var, yerelde yok -> yerele çekilecek' ;;
      DELETED)    printf 'SİL     sunucuda silinmiş -> yerel dosyaları kaldırılır (yedek: deploy-backups/local-removed-*)' ;;
      NEWSITE)    printf 'YEREL   yalnız yerelde (pull sunucuya göndermez)' ;;
      AUTO)       printf 'ATLA    yerel daha yeni (yerel %s, sunucu %s)' "$lvs" "$rvs" ;;
      UNKFILE)    printf 'ATLA    tanınmayan config dosyası' ;;
      *)          if [ "$SYNC_CONFIGS" = 1 ]; then printf 'EŞİTLE  sunucu dosyaları yerele kopyalanacak (--sync-configs)'
                  else printf 'ATLA    yerel farklı (eşitlemek için --sync-configs)'; fi ;;
    esac
    return 0
  fi
  case "$cls:$act" in
    SAME:*)            printf 'AYNI' ;;
    REMOTEONLY:*)      printf "ÇEK     sunucuda var, yerelde yok -> yerele çekilecek (bu deploy'dan dışlanır)" ;;
    DELETED:*)         printf "SİL     sunucuda silinmiş -> yerel dosyaları kaldırılır (yedek: deploy-backups/local-removed-*; deploy'dan dışlanır)" ;;
    NEWSITE:*)         printf 'GÖNDER  yeni site (sunucuda yok)' ;;
    AUTO:SEND)         printf 'EZ      yerel daha yeni (%s → %s), sunucuda uygulanmış heal/yeni arşiv yok' "$rvs" "$lvs" ;;
    AUTO:KEEP)         printf 'KORU    --keep-configs (yerel %s, sunucu %s)' "$lvs" "$rvs" ;;
    KEEPVER:ASK)       printf 'EZ?     --overwrite-configs: sunucu sürümü >= yerel; onay sorulur' ;;
    KEEPVER:*)         printf 'KORU    sunucu sürümü >= yerel (sunucuda değişmiş olabilir)' ;;
    KEEPHEAL:ASK)      printf 'EZ?     --overwrite-configs: sunucuda heal kaydı/yeni arşiv var; onay sorulur' ;;
    KEEPHEAL:*)        printf 'KORU    sunucuda uygulanmış heal kaydı/yeni arşiv var' ;;
    UNKVER:ASK)        printf 'EZ?     --overwrite-configs: sürüm okunamadı; onay sorulur' ;;
    UNKVER:*)          printf 'KORU    belirsiz: config sürümü okunamadı' ;;
    UNKHEAL:ASK)       printf 'EZ?     --overwrite-configs: heal kaydı okunamadı; onay sorulur' ;;
    UNKHEAL:*)         printf 'KORU    belirsiz: sunucudaki heal kaydı okunamadı' ;;
    UNKFILE:ASK)       printf 'EZ?     --overwrite-configs: tanınmayan dosya farklı; onay sorulur' ;;
    UNKFILE:*)         printf 'KORU    belirsiz: tanınmayan config dosyası farklı' ;;
    BASELINE:ASK)      printf 'EZ?     --overwrite-configs: baseline/arşiv farkı; onay sorulur' ;;
    BASELINE:*)        printf 'KORU    yaml aynı; baseline/arşiv farkı (çalışma zamanı) sunucuda kalır' ;;
    LOCALONLY:SEND|LOCALONLY:ASK) printf 'GÖNDER  --overwrite-configs (sunucu bilinmiyor)' ;;
    LOCALONLY:*)       printf 'KORU    sunucu bilinmiyor (--local-only)' ;;
    *)                 printf '%s %s' "$cls" "$act" ;;
  esac
}

# $TMP/cfg.dec -> $TMP/cfg.act ("site sınıf eylem"); eylem: SAME | SEND | KEEP | ASK | PULL | REMOVE
# (PULL: REMOTEONLY, yerele çekilir; pull modunda SEND/ASK üretilmez)
# (REMOVE: DELETED, sunucuda silinmiş site; yerel dosyaları yedeğe taşınır, tar'dan dışlanır)
# (ASK: --overwrite-configs ile ezilebilir; onaya bağlı)
cfg_plan() {
  local tag site cls lv rv ha ht an act
  : > "$TMP/cfg.act"
  while read -r tag site cls lv rv ha ht an; do
    [ "$tag" = D ] || continue
    case "$cls" in
      SAME)       act=SAME ;;
      REMOTEONLY) act=PULL ;;
      DELETED)    act=REMOVE ;;
      NEWSITE)    if [ "$MODE" = pull ]; then act=KEEP; else act=SEND; fi ;;   # pull: hiçbir şey gönderilmez
      AUTO)       if [ "$KEEP_CONFIGS" = 1 ] || [ "$MODE" = pull ]; then act=KEEP; else act=SEND; fi ;;
      *)          if [ "$OVERWRITE_CONFIGS" = 1 ] && [ "$MODE" != pull ]; then act=ASK; else act=KEEP; fi ;;
    esac
    printf '%s %s %s\n' "$site" "$cls" "$act" >> "$TMP/cfg.act"
  done < "$TMP/cfg.dec"
}

cfg_set_ask() { # cfg_set_ask SEND|KEEP  (ASK eylemlerini kesinleştirir)
  awk -v to="$1" '{ if ($3 == "ASK") $3 = to; print }' "$TMP/cfg.act" > "$TMP/cfg.act.n" &&
    mv "$TMP/cfg.act.n" "$TMP/cfg.act"
}

cfg_table() { # karar tablosu (planlanan eylemlerle)
  local tag site cls lv rv ha ht an act ver_l ver_r heal text n
  printf '\n  %-16s %-6s %-7s %-13s %s\n' SITE YEREL SUNUCU 'HEAL(uyg/top)' KARAR
  while read -r tag site cls lv rv ha ht an; do
    [ "$tag" = D ] || continue
    act=$(awk -v s="$site" '$1 == s { print $3; exit }' "$TMP/cfg.act")
    ver_l=$(cfg_ver "$lv"); ver_r=$(cfg_ver "$rv")
    case "$cls" in NEWSITE|LOCALONLY|DELETED) ver_r='-' ;; REMOTEONLY) ver_l='-' ;; esac
    if [ "$cls" = LOCALONLY ]; then heal='-'
    elif [ "$cls" = NEWSITE ] || [ "$cls" = REMOTEONLY ] || [ "$cls" = DELETED ]; then heal='-'
    elif [ "$ha" = '?' ]; then heal='?'
    else heal="$ha/$ht"; [ "$an" = 0 ] || heal="$heal +${an}arsiv"; fi
    text=$(cfg_row_text "$cls" "$act" "$lv" "$rv")
    if [ "$cls" = SAME ]; then
      n=$(awk -v s="$site" '$1 == "Fr" && $2 == s { n++ } END { print n + 0 }' "$TMP/cfg.dec")
      if [ "$n" != 0 ]; then
        if [ "$SYNC_CONFIGS" = 1 ]; then text="$text (sunucuda yerelde olmayan $n dosya var; yerele eklenecek)"
        else text="$text (sunucuda yerelde olmayan $n dosya var; --sync-configs ile çekilir)"; fi
      fi
    fi
    if [ "$MODE" != pull ] && [ "$SYNC_CONFIGS" = 1 ] && [ "$act" = KEEP ] && cfg_syncable "$cls"; then
      text="$text; yerele eşitlenecek (--sync-configs)"
    fi
    printf '  %-16s %-6s %-7s %-13s %s\n' "${site/#providers\//provider:}" "$ver_l" "$ver_r" "$heal" "$text"
  done < "$TMP/cfg.dec"
  info "HEAL = sunucuda configi değiştirebilen (uygulanmış) heal kaydı / toplam kayıt; +Narşiv = yerelde olmayan sunucu arşivi"
}

cfg_download_backup() { # sunucudaki configs/ -> ./deploy-backups/remote-configs-<ts>/configs
  CFG_BACKUP_DIR="$SCRIPT_DIR/deploy-backups/remote-configs-$TS"
  mkdir -p "$CFG_BACKUP_DIR"
  if rssh "tar czf - -C $DZ_DEST/app/scraper configs" | tar xzf - -C "$CFG_BACKUP_DIR"; then
    ok "sunucu configs indirildi: ${CFG_BACKUP_DIR#$SCRIPT_DIR/}/configs"
  else
    die "sunucu configs indirilemedi; configs'e dokunulmadan durduruldu"
  fi
}

# Kararı verir/yazar; sonunda $CFG_EXCL (dışlanacak dosyalar) ve CFG_KEPT hazırdır.
# cfg.local + cfg.remote hazır olmalı. $1=1: yalnız-yerel (sunucu bilinmiyor).
cfg_decide() {
  local lo=${1:-0} tag site cls lv rv ha ht an act sites
  cfg_classify "$lo"
  if [ ! -s "$TMP/cfg.dec" ]; then info "yerelde scraper configs yok"; return 0; fi
  cfg_plan
  cfg_table

  # sunucudaki mevcut configs (ezilecek ya da korunacak) her durumda önce indirilir
  if [ "$lo" = 0 ] && { [ "$MODE" = pull ] || { [ "$SYNC_CONFIGS" = 1 ] && grep -q '^Fr ' "$TMP/cfg.dec"; } ||
                         awk '$2 != "SAME" && $2 != "NEWSITE" && $2 != "DELETED" { f = 1 } END { exit !f }' "$TMP/cfg.act"; }; then
    CFG_WILL_DOWNLOAD=1
    if [ "$DRY_RUN" = 1 ]; then
      info "(dry-run) sunucu configs indirilmedi ($([ "$MODE" = pull ] && echo "gerçek çalıştırmada" || echo "deploy'da") ./deploy-backups/ altına indirilir)"
    else
      cfg_download_backup
    fi
  fi

  # AUTO ezmeler: bilgi mesajı (sunucu sürümü yukarıda yedeğe alındı)
  while read -r tag site cls lv rv ha ht an; do
    [ "$tag" = D ] && [ "$cls" = AUTO ] || continue
    act=$(awk -v s="$site" '$1 == s { print $3; exit }' "$TMP/cfg.act")
    if [ "$act" = SEND ]; then
      if [ "$DRY_RUN" = 1 ]; then info "$site: $(cfg_ver "$rv") → $(cfg_ver "$lv") (yerel daha yeni; deploy'da otomatik ezilir)"
      else info "$site: $(cfg_ver "$rv") → $(cfg_ver "$lv") (yerel daha yeni; sunucu sürümü yedeklendi, ezilecek)"; fi
    fi
  done < "$TMP/cfg.dec"

  # --overwrite-configs: korunacak olanları ezmek için onay
  sites=$(awk '$3 == "ASK" { printf " %s", $1 }' "$TMP/cfg.act")
  if [ -n "$sites" ]; then
    if [ "$lo" = 1 ]; then
      cfg_set_ask SEND
      info "(local-only) --overwrite-configs: şu configs gönderilirdi:$sites"
    elif [ "$DRY_RUN" = 1 ]; then
      cfg_set_ask SEND
      info "(dry-run) --overwrite-configs: şu configs ezilirdi ('evet' onayı sorulurdu):$sites"
    elif confirm "Sunucuda korunan scraper configs (${sites# }) YEREL sürümle EZİLSİN mi? (sunucu sürümleri indirildi)"; then
      cfg_set_ask SEND; warn "korunan configs ezilecek:$sites"
    else
      cfg_set_ask KEEP; info "onaylanmadı -> şu configs deploy'dan dışlanacak:$sites"
    fi
  fi

  # dışlanacaklar: KORU kararlı siteler + sunucuda silinmiş (REMOVE) sitelerin sunucudan farklı/yalnız-yerel dosyaları
  awk 'FILENAME == ARGV[1] { if ($3 == "KEEP" || $3 == "REMOVE") k[$1] = 1; next }
       $1 == "Fl" && ($2 in k) { print $3 }' "$TMP/cfg.act" "$TMP/cfg.dec" > "$CFG_EXCL"
  CFG_KEPT=$(awk '$3 == "KEEP" && $2 != "SAME" { printf " %s", $1 }' "$TMP/cfg.act")
  if [ "$MODE" = pull ]; then
    :   # pull: tar gönderimi yok; dışlama anlamsız
  elif [ -n "$CFG_KEPT" ]; then
    if [ "$lo" = 0 ]; then
      warn "sunucudaki configs korunuyor (deploy'dan dışlanacak):$CFG_KEPT"
      info "ezmek için --overwrite-configs (onay ister); sunucu kopyası deploy-backups/ altında"
    else
      info "(local-only) configs dışlanıyor:$CFG_KEPT"
    fi
  else
    ok "hiçbir config dışlanmıyor"
  fi

  # sunucuda admin'den silinmiş siteler yerel aynadan da kaldırılır (yedeğe taşınır)
  if [ "$lo" = 0 ]; then cfg_remove_deleted; fi

  # sunucuda olup yerelde olmayan siteler (+ --sync-configs ile KORU siteleri) yerel aynaya çekilir
  if [ "$lo" = 0 ]; then cfg_pull_step; fi
}

# DELETED siteleri (sunucudaki deleted_sites.json'da, sunucuda config'i yok) yerel configs/'ten kaldırır: dosyalar
# deploy-backups/local-removed-<ts>/ altına TAŞINIR (silinmez), tar'dan zaten dışlanmıştır ($CFG_EXCL). providers/ dokunulmaz.
# dry-run: dosyalara dokunulmaz, yalnız "kaldırılacaktı" listesi. Yön yalnız sunucu -> yerel; sunucuya yazılmaz.
REMOVED_SITES=""           # sunucuda silinmiş oldukları için yerelden gerçekten kaldırılan siteler, başında boşlukla
cfg_remove_deleted() {
  local site files disp f name n dest
  awk '$3 == "REMOVE" { print $1 }' "$TMP/cfg.act" > "$TMP/cfg.rm"
  [ -s "$TMP/cfg.rm" ] || return 0
  dest="${LOCAL_REMOVED_BASE:-$SCRIPT_DIR/deploy-backups}/local-removed-$TS"
  info "sunucuda silinmiş (deleted_sites.json) ama yerelde duran siteler: $(join_csv < "$TMP/cfg.rm")"
  while read -r site; do
    files=$(awk -v s="$site" '$1 == "Fl" && $2 == s { print $3 }' "$TMP/cfg.dec")
    disp=$(awk -v s="$site" '$1 == "Fl" && $2 == s { printf " %s", substr($3, 3) }' "$TMP/cfg.dec")
    if [ "$DRY_RUN" = 1 ]; then
      info "(dry-run) yerelden kaldırılacaktı: $site (sunucuda silinmiş; deploy'dan dışlanır)"
      info "    dosyalar:$disp"
      continue
    fi
    n=0
    mkdir -p "$dest"
    for f in $files; do
      name=${f#./}
      if ! printf '%s' "$name" | grep -Eq '^[A-Za-z0-9_-]+(\.baseline\.json|\.v[0-9]+\.yaml|\.yaml)$'; then
        warn "$site: güvenli olmayan dosya adı atlandı: $name"; continue
      fi
      if [ ! -f "$LOCAL_CFG/$name" ] || [ -L "$LOCAL_CFG/$name" ]; then continue; fi
      if mv -- "$LOCAL_CFG/$name" "$dest/$name"; then n=$((n + 1)); else warn "$site: taşınamadı: $name"; fi
    done
    if [ "$n" = 0 ]; then warn "$site: yerelden hiçbir dosya kaldırılamadı"; continue; fi
    ok "yerelden kaldırıldı: $site (sunucuda silinmiş)"
    info "    dosyalar:$disp"
    info "    yedek: ${dest#$SCRIPT_DIR/}"
    REMOVED_SITES="$REMOVED_SITES $site"
  done < "$TMP/cfg.rm"
  return 0
}

# REMOTEONLY siteleri (+ --sync-configs ile KORU kararlı siteleri) indirilen yedekten yerel configs/'e kopyalar.
# Yön yalnız sunucu -> yerel; sunucuya HİÇBİR ŞEY yazılmaz. Kopyalanan dosyalar sunucudakiyle aynı olduğundan
# bu deploy'un tar'ından dışlanır ($CFG_EXCL). dry-run: kopyalanmaz, yalnız "çekilecekti" listesi.
PULLED_SITES=""            # gerçekten yerele kopyalanan siteler, başında boşlukla
cfg_pull_step() {
  local site cls files disp f name src dst rv lv ver n skipped shown
  info "REMOTEONLY (sunucuda var, yerelde yok): $(awk '$1 == "D" && $3 == "REMOTEONLY" { print $2 }' "$TMP/cfg.dec" | join_csv)"
  # cfg.act: "site sınıf eylem"; cfg.dec: "Fr site dosya" -> cfg.pull: "site dosya sınıf"
  awk -v sync="$SYNC_CONFIGS" '
    FILENAME == ARGV[1] { c[$1] = $2; a[$1] = $3; next }
    $1 == "Fr" && ($2 in a) {
      # SAME sitede yalnız sunucuda olan dosya (örn. heal arşivi) varsa --sync-configs onu EKLER (var olan yerel dosya ezilmez)
      if (a[$2] == "PULL" || (sync == 1 && (a[$2] == "SAME" || (a[$2] == "KEEP" && c[$2] ~ /^(KEEPVER|KEEPHEAL|BASELINE|UNKVER|UNKHEAL)$/)))) print $2, $3, c[$2]
    }' "$TMP/cfg.act" "$TMP/cfg.dec" > "$TMP/cfg.pull"
  if [ "$SYNC_CONFIGS" = 1 ]; then
    skipped=$(awk '$3 == "KEEP" && ($2 == "AUTO" || $2 == "UNKFILE") { printf " %s", $1 }' "$TMP/cfg.act")
    if [ -n "$skipped" ]; then info "--sync-configs atlanan (yerel daha yeni ya da tanınmayan dosya):$skipped"; fi
  fi
  if [ ! -s "$TMP/cfg.pull" ]; then
    if [ "$SYNC_CONFIGS" = 1 ]; then info "--sync-configs: yerele kopyalanacak sunucu dosyası yok"; fi
    return 0
  fi
  awk '!seen[$1]++ { print $1, $3 }' "$TMP/cfg.pull" > "$TMP/cfg.pull.sites"
  while read -r site cls; do
    files=$(awk -v s="$site" '$1 == s { print $2 }' "$TMP/cfg.pull")
    disp=$(awk -v s="$site" '$1 == s { printf " %s", substr($2, 3) }' "$TMP/cfg.pull")
    lv=$(awk -v s="$site" '$1 == "D" && $2 == s { print $4; exit }' "$TMP/cfg.dec")
    rv=$(awk -v s="$site" '$1 == "D" && $2 == s { print $5; exit }' "$TMP/cfg.dec")
    shown=${site/#providers\//provider }   # provider tarifi: "provider <ad>"
    if [ "$cls" != REMOTEONLY ] && [ "$cls" != SAME ] && [ "${lv:-?}" -gt "${rv:-?}" ] 2>/dev/null; then
      warn "$shown: yerel $(cfg_ver "$lv") sunucudan ($(cfg_ver "$rv")) yüksek; yerel kopya sunucununkiyle ezilecek (git'ten kurtarılabilir)"
    fi
    if [ "$DRY_RUN" = 1 ]; then
      if [ "$cls" = REMOTEONLY ]; then info "(dry-run) yerele çekilecekti: $shown ($(cfg_ver "$rv"))"
      elif [ "$cls" = SAME ]; then info "(dry-run) yerele eklenecekti: $shown (yalnız sunucuda olan dosyalar)"
      else info "(dry-run) yerele eşitlenecekti: $shown (yerel $(cfg_ver "$lv") -> sunucu $(cfg_ver "$rv"))"; fi
      info "    dosyalar:$disp"
      printf '%s\n' "$files" >> "$CFG_EXCL"
      continue
    fi
    n=0
    mkdir -p "$LOCAL_CFG"
    for f in $files; do
      name=${f#./}
      if ! printf '%s' "$name" | grep -Eq '^([A-Za-z0-9_-]+(\.baseline\.json|\.v[0-9]+\.yaml|\.yaml)|providers/[a-z][a-z0-9_]{1,31}(\.v[0-9]+)?\.yaml)$'; then
        warn "$site: güvenli olmayan dosya adı atlandı: $name"; continue
      fi
      src="$CFG_BACKUP_DIR/configs/$name"; dst="$LOCAL_CFG/$name"
      if [ ! -f "$src" ] || [ -L "$src" ]; then warn "$site: indirilen yedekte yok, atlandı: $name"; continue; fi
      case "$name" in providers/*) mkdir -p "$LOCAL_CFG/providers" ;; esac
      if cp -- "$src" "$dst" && chmod 644 "$dst"; then
        n=$((n + 1)); printf '%s\n' "$f" >> "$CFG_EXCL"
      else
        warn "$site: kopyalanamadı: $name"
      fi
    done
    if [ "$n" = 0 ]; then warn "$site: hiçbir dosya yerele kopyalanamadı"; continue; fi
    ver=$(yaml_version "$LOCAL_CFG/$site.yaml"); [ "$ver" != "?" ] || ver=$rv
    if [ "$cls" = REMOTEONLY ]; then ok "yerele çekildi: $shown ($(cfg_ver "$ver"))"
    elif [ "$cls" = SAME ]; then ok "yerele eklendi: $shown ($(cfg_ver "$ver"); yalnız sunucuda olan dosyalar)"
    else ok "yerele eşitlendi: $shown (yerel $(cfg_ver "$lv") -> $(cfg_ver "$ver"))"; fi
    info "    dosyalar:$disp"
    PULLED_SITES="$PULLED_SITES $site"
  done < "$TMP/cfg.pull.sites"
  LC_ALL=C sort -u "$CFG_EXCL" | grep . > "$TMP/cfg.excl.u" || true
  mv "$TMP/cfg.excl.u" "$CFG_EXCL"
  return 0
}

cfg_pull_hints() { # yerel aynada değişiklik olduysa git ipucu
  if [ -n "$PULLED_SITES" ]; then
    info "yerele çekilen/eşitlenen configs:$PULLED_SITES"
    info "git'te kontrol et ve commit et:  git status -- server/app/scraper/configs   (yedek: deploy-backups/ commit edilmez)"
  fi
  if [ -n "$REMOVED_SITES" ]; then
    info "yerelden kaldırılan (sunucuda silinmiş) siteler:$REMOVED_SITES (dosyalar deploy-backups/local-removed-*/ altında; git'te kontrol et)"
  fi
}

check_diffs() {
  local lreq rreq
  lreq=$(md5_of "$LOCAL_SERVER/requirements.txt")
  rreq=$(rssh "md5sum $DZ_DEST/requirements.txt 2>/dev/null | cut -d' ' -f1" || true)
  if [ "$lreq" = "$rreq" ]; then
    ok "requirements.txt aynı"
  else
    REQ_CHANGED=1
    if [ "$DRY_RUN" = 1 ]; then warn "requirements.txt farklı -> deploy sonrası pip install çalışırdı"
    else warn "requirements.txt farklı -> deploy sonrası venv/bin/pip install -r requirements.txt çalışacak"; fi
  fi

  local_config_list_checked
  fetch_remote_cfg_list
  cfg_decide 0
}

# sunucu configs listesi (salt-okunur) -> $TMP/cfg.remote (F/V/H/HS satırları; bkz. cfg_classify)
fetch_remote_cfg_list() {
  rscript "$DZ_DEST" > "$TMP/cfg.remote" <<'EOF' || die "sunucudaki configs listesi alınamadı"
dest=$1
cd "$dest/app/scraper/configs" 2>/dev/null || exit 0
find . -type f ! -name '._*' ! -name '.DS_Store' ! -path '*__pycache__*' | LC_ALL=C sort |
while IFS= read -r f; do echo "F $(md5sum "$f" | cut -d' ' -f1) $f"; done
for f in ./*.yaml ./providers/*.yaml; do
  [ -f "$f" ] || continue
  v=$(grep '^version:' "$f" | head -n 1 | tr -d "\"' \r\t" | sed -n 's/^version:\([0-9][0-9]*\)\(#.*\)\{0,1\}$/\1/p')
  echo "V $f ${v:-?}"
done
# heal geçmişi (salt-okunur; yalnız site adı + sayaçlar yazılır): data/_ops.json + data/scraper_state/*.json
dd=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}DATA_DIR[[:space:]]*=//p' "$dest/.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r\t")
[ -n "$dd" ] || dd=data
case "$dd" in /*) ;; *) dd="$dest/$dd" ;; esac
py=$(command -v python3 || true)
[ -n "$py" ] || py="$dest/venv/bin/python"
"$py" - "$dd/scraper_state" <<'PY' || echo "HS err"
import json, os, re, sys
sd = sys.argv[1]
NONAPPLY = ("failed", "skipped_cooldown", "not_applied", "heal_failed")
def applied(e):
    # config'i değiştirmiş olabilecek kayıt mı? (bilinmeyen biçim = evet, temkinli)
    if not isinstance(e, dict):
        return True
    if e.get("applied") is True or e.get("new_version"):
        return True
    if e.get("applied") is False:
        return False
    if e.get("outcome") in NONAPPLY or e.get("status") in NONAPPLY:
        return False
    return True
def load(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
ok_name = re.compile(r"^[A-Za-z0-9._-]+$")
try:
    if not os.path.isdir(sd):
        raise RuntimeError("scraper_state yok")
    tot, app, bad = {}, {}, set()
    ops = load(os.path.join(sd, "_ops.json")) or {}
    for e in ops.get("heals", []):
        s = e.get("site") if isinstance(e, dict) else None
        if not s or not ok_name.match(s):
            continue
        tot[s] = tot.get(s, 0) + 1
        if applied(e):
            app[s] = app.get(s, 0) + 1
    for n in sorted(os.listdir(sd)):
        if n.startswith(("_", ".")) or not n.endswith(".json"):
            continue
        s = n[:-5]
        if not ok_name.match(s):
            continue
        try:
            lh = (load(os.path.join(sd, n)) or {}).get("last_heal")
        except Exception:
            bad.add(s)
            continue
        if lh:
            tot[s] = max(tot.get(s, 0), 1)
            if applied(lh):
                app[s] = max(app.get(s, 0), 1)
    out = []
    for s in sorted(set(tot) | bad):
        out.append("H %s ? ?" % s if s in bad else "H %s %d %d" % (s, tot.get(s, 0), app.get(s, 0)))
    out.append("HS ok")
    print("\n".join(out))
except Exception:
    print("HS err")
PY
# admin'den silinmiş siteler (salt-okunur): data/deleted_sites.json -> "DS <site>" + "DSS ok|err"
"$py" - "$dd/deleted_sites.json" <<'PYD' || echo "DSS err"
import json, os, re, sys
p = sys.argv[1]
ok_site = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
status = "ok"
names = []
if os.path.exists(p):
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("beklenmeyen biçim")
        names = sorted(s for s in data if isinstance(s, str) and ok_site.match(s))
    except Exception:
        status = "err"
for s in names:
    print("DS %s" % s)
print("DSS %s" % status)
PYD
exit 0
EOF
}

local_config_list_checked() { # yerel liste -> $TMP/cfg.local (güvensiz dosya adı = durdur)
  local_config_list > "$TMP/cfg.local"
  if grep -Ev '^(F [0-9a-f]{32} \./[A-Za-z0-9._/-]+|V \./(providers/)?[A-Za-z0-9._-]+\.yaml ([0-9]+|\?))$' "$TMP/cfg.local" | grep -q .; then
    die "yerel configs/ içinde güvenli olmayan dosya adı var (yalnız A-Za-z0-9._- ve /); durduruldu"
  fi
}

# ---------------------------------------------------------------- kopya ----
TAR_ARGS=()
build_tar_args() {
  local f
  TAR_ARGS=(--exclude='._*' --exclude='.DS_Store' --exclude='server/.env' --exclude='server/data' \
            --exclude='server/venv' --exclude='__pycache__' --exclude='*.pyc')
  # scraper configs: yalnız KORU kararı verilen dosyalar (yaml/baseline) dışlanır
  if [ -s "$CFG_EXCL" ]; then
    while IFS= read -r f; do TAR_ARGS+=(--exclude="server/app/scraper/configs/${f#./}"); done < "$CFG_EXCL"
  fi
  if tar --no-xattrs -cf /dev/null /dev/null >/dev/null 2>&1; then TAR_ARGS+=(--no-xattrs); fi
}

make_archive() { ( cd "$SCRIPT_DIR" && COPYFILE_DISABLE=1 tar czf - "${TAR_ARGS[@]}" server ); }

list_archive() {
  local n nx f
  build_tar_args
  make_archive | tar tzf - > "$TMP/list.all"
  grep -v '/$' "$TMP/list.all" > "$TMP/list.files" || true
  # güvenlik ağı: bu yollar ASLA gönderilmemeli
  if grep -Eq '^server/(\.env|data|venv)(/|$)' "$TMP/list.all"; then
    die "güvenlik: .env/data/venv arşivde görünüyor (tar --exclude çalışmadı); durduruldu"
  fi
  if grep -q '/\._' "$TMP/list.all"; then die "güvenlik: arşivde ._* dosyaları var; durduruldu"; fi
  # güvenlik ağı: KORU kararlı config dosyaları arşivde OLMAMALI
  while IFS= read -r f; do
    if grep -Fxq "server/app/scraper/configs/${f#./}" "$TMP/list.all"; then
      die "güvenlik: korunacak config arşivde görünüyor ($f); durduruldu"
    fi
  done < "$CFG_EXCL"
  n=$(wc -l < "$TMP/list.files" | tr -d ' ')
  nx=$(wc -l < "$CFG_EXCL" | tr -d ' ')
  info "gönderilecek dosya sayısı: $n  (configs: $([ "$nx" = 0 ] && echo "tümü dahil" || echo "$nx dosya dışlandı"))"
  awk -F/ '{ if (NF >= 3) k = $2 "/"; else k = "(kök dosyalar)"; c[k]++ }
           END { for (k in c) printf "      %5d  %s\n", c[k], k }' "$TMP/list.files" | LC_ALL=C sort -k2
  if [ "$DRY_RUN" = 1 ]; then
    info "configs gönderilecek: $(grep '^server/app/scraper/configs/' "$TMP/list.files" | sed 's#^server/app/scraper/configs/##' | tr '\n' ' ' | grep . || echo '(hiçbiri)')"
    if [ "$nx" != 0 ]; then info "configs dışlanan:     $(sed 's#^\./##' "$CFG_EXCL" | tr '\n' ' ')"; fi
    if [ "$VERBOSE" = 1 ] || [ "$n" -le 25 ]; then
      info "dosyalar:"; sed 's/^/      /' "$TMP/list.files"
    else
      info "ilk 25 dosya (tamamı için -v):"; head -n 25 "$TMP/list.files" | sed 's/^/      /'
    fi
  fi
}

OLD_FILES=""
send_files() {
  local f
  for f in app/routers/admin.py app/static/admin.html app/static/admin-advanced.html; do
    [ -e "$LOCAL_SERVER/$f" ] || OLD_FILES="$OLD_FILES $f"   # yerelde de yoksa sunucudan silinir
  done
  build_tar_args
  info "gönderiliyor (silme yok): $DZ_HOST:$PARENT"
  if ! make_archive | rssh_in "tar xzf - --no-same-owner -C $PARENT"; then
    bad "kopyalama başarısız (sunucuda yarım kalmış olabilir)"
    show_rollback_hint
    exit 1
  fi
  ok "kopyalandı"
  # shellcheck disable=SC2086
  rscript "$DZ_DEST" $OLD_FILES > "$TMP/clean.out" <<'EOF' || warn "temizlik adımı hata verdi (önemsiz)"
dest=$1; shift
for d in app tests tools; do
  [ -d "$dest/$d" ] && find "$dest/$d" -name '._*' -type f -delete
done
for f in "$@"; do
  if [ -e "$dest/$f" ]; then rm -f -- "$dest/$f" && echo "eski dosya silindi: $f"; fi
done
exit 0
EOF
  ok "sunucuda ._* çöp dosyaları temizlendi"
  if [ -s "$TMP/clean.out" ]; then sed 's/^/          /' "$TMP/clean.out"; fi
  return 0
}

# ---------------------------------------- import / restart / doğrulama -----
import_check() { # 0 = tamam; değilse hata çıktısını gösterir, 1 döner
  if [ "$REQ_CHANGED" = 1 ]; then
    info "requirements.txt değişti -> venv/bin/pip install -r requirements.txt"
    if ! rssh "cd $DZ_DEST && PIP_DISABLE_PIP_VERSION_CHECK=1 venv/bin/pip install -q -r requirements.txt 2>&1" > "$TMP/pip.out" 2>&1; then
      tail -n 30 "$TMP/pip.out" | sed 's/^/      /'
      bad "pip install başarısız"
      return 1
    fi
    ok "pip install tamam"
  fi
  if ! rssh "cd $DZ_DEST && venv/bin/python -c 'import app.main' 2>&1" > "$TMP/imp.out" 2>&1; then
    tail -n 40 "$TMP/imp.out" | sed 's/^/      /'
    bad "import app.main başarısız"
    return 1
  fi
  ok "import app.main"
}

RC=0
do_restart() {
  local since active
  if [ "$NO_RESTART" = 1 ]; then
    warn "--no-restart: servis yeniden başlatılmadı; yeni kod restart'a kadar CANLI DEĞİL (systemctl restart $DZ_SERVICE)"
    return 0
  fi
  since=$(rssh "date '+%Y-%m-%d %H:%M:%S'" || true)
  since=${since//[^0-9: -]/}
  [ -n "$since" ] || since="1 minute ago"
  if ! rssh "systemctl restart $DZ_SERVICE" 2> "$TMP/restart.err"; then
    sed 's/^/      /' "$TMP/restart.err"
    bad "systemctl restart $DZ_SERVICE başarısız"
    RC=2
  fi
  info "6 sn bekleniyor..."
  sleep 6
  active=$(rssh "systemctl is-active $DZ_SERVICE" 2>/dev/null || true)
  if [ "$active" = active ]; then
    ok "servis $DZ_SERVICE: active"
  else
    bad "servis $DZ_SERVICE: ${active:-bilinmiyor}"; RC=2
  fi
  rssh "journalctl -u $DZ_SERVICE --since '$since' -n 40 --no-pager 2>&1 | grep -iE 'error|traceback' | cut -c1-240 || true" > "$TMP/jrn.out" 2>&1 || true
  if [ -s "$TMP/jrn.out" ]; then
    warn "journal (restart sonrası, son 40 satır) içinde error/traceback:"
    sed 's/^/      /' "$TMP/jrn.out"
  else
    ok "journal (restart sonrası, son 40 satır): error/traceback yok"
  fi
}

VERIFY_FAIL=0
verify_endpoints() {
  local code path
  rscript "$DZ_PORT" > "$TMP/verify.out" <<'EOF' || true
i=0
while [ "$i" -lt 25 ]; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$1/api/health" 2>/dev/null || true)
  [ "$c" = 200 ] && break
  i=$((i + 1)); sleep 1
done
for p in '/admin' '/api/health' '/api/ops/overview' '/api/ops/settings' '/api/ops/tmdb/status' '/api/ops/library?limit=1'; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "http://127.0.0.1:$1$p" 2>/dev/null || true)
  echo "${c:-000} $p"
done
exit 0
EOF
  printf '\n  %-6s %-30s %s\n' HTTP ENDPOINT DURUM
  VERIFY_FAIL=0
  [ -s "$TMP/verify.out" ] || VERIFY_FAIL=1
  while read -r code path; do
    if [ "$code" = 200 ]; then
      printf '  %s%-6s%s %-30s %sOK%s\n' "$C_GRN" "$code" "$C_RST" "$path" "$C_GRN" "$C_RST"
    else
      VERIFY_FAIL=1
      printf '  %s%-6s%s %-30s %sBAŞARISIZ%s\n' "$C_RED" "$code" "$C_RST" "$path" "$C_RED" "$C_RST"
    fi
  done < "$TMP/verify.out"
  return 0
}

import_step() { # import (+pip) başarısızsa restart ETMEDEN çıkar
  if ! import_check; then
    bad "servis RESTART EDİLMEDİ (çalışan süreç eski kodla devam ediyor)"
    if [ "$MODE" = deploy ]; then show_rollback_hint
    else info "yedek arşivi bozuk/uyumsuz olabilir; başka bir yedek deneyin"; fi
    exit 1
  fi
}

# ---------------------------------------------------------------- akışlar --
do_deploy() {
  step "[0/6] Ön kontrol"
  local_preflight
  if [ "$LOCAL_ONLY" = 1 ]; then info "--local-only: sunucuya bağlanılmıyor"; else remote_preflight; fi

  step "[1/6] Yedek (sunucuda)"
  if [ "$LOCAL_ONLY" = 1 ]; then
    info "(local-only) atlandı"
  elif [ "$DRY_RUN" = 1 ]; then
    info "(dry-run) yedek alınmayacak; deploy'da yol: $BACKUP_PATH"
  else
    make_backup "$BACKUP_PATH"
    CURRENT_BACKUP=$BACKUP_PATH
  fi

  step "[2/6] Fark kontrolü (yerel vs sunucu)"
  if [ "$LOCAL_ONLY" = 1 ]; then
    info "(local-only) sunucu farkı bilinmiyor; configs varsayılan olarak dışlanır (--overwrite-configs gönderir)"
    local_config_list_checked
    : > "$TMP/cfg.remote"
    cfg_decide 1
    info "(local-only) REMOTEONLY (sunucuda olup yerelde olmayan site) hesaplanamaz: sunucuya bağlanılmıyor"
    info "(local-only) sunucuda silinmiş siteler (deleted_sites.json) bilinmiyor: yerelden site kaldırılmaz, silinmiş site sunucuya geri gönderilebilir"
    if [ "$SYNC_CONFIGS" = 1 ]; then info "(local-only) --sync-configs: sunucu bilinmiyor; yerele çekme yapılmaz"; fi
  else
    check_diffs
  fi

  step "[3/6] Kopyala"
  list_archive
  if [ "$DRY_RUN" = 1 ]; then
    prune_plan_section
    printf '\n%sDRY-RUN tamamlandı%s: sunucuya hiçbir şey yazılmadı, servis restart edilmedi.\n' "$C_BLD" "$C_RST"
    return 0
  fi
  send_files

  step "[4/6] Import kontrolü (venv/bin/python -c 'import app.main')"
  import_step

  step "[5/6] Restart"
  do_restart

  step "[6/6] Doğrulama (sunucuda curl, port $DZ_PORT)"
  verify_endpoints
  finish_deploy
}

finish_deploy() {
  if [ "$VERIFY_FAIL" = 1 ]; then
    RC=2
    printf '\n%s!! DOĞRULAMA BAŞARISIZ: 200 dışında yanıt veren uç var (veya servis cevap vermiyor) !!%s\n' "$C_RED" "$C_RST"
    show_rollback_hint
  fi
  # budama YALNIZ tam başarıdan sonra (restart + tüm uçlar 200); aksi halde yedekler geri dönüş için kalır
  if [ "$RC" = 0 ] && [ "$VERIFY_FAIL" = 0 ]; then
    do_prune_backups
  elif [ "$PRUNE" = 1 ]; then
    step "Yedek budama"
    info "deploy sorunlu/doğrulanamadı: eski yedekler geri dönüş için KORUNDU (budama yapılmadı)"
  fi
  step "Sonraki adımlar"
  info "admin:  http://${DZ_HOST#*@}:$DZ_PORT/admin"
  if [ -n "$CURRENT_BACKUP" ]; then info "yedek:  $CURRENT_BACKUP"; fi
  if [ "$RC" = 0 ]; then show_rollback_hint; fi
  if [ -n "$CFG_KEPT" ]; then
    info "configs sunucudaki haliyle bırakıldı:$CFG_KEPT (ezmek için --overwrite-configs)"
  fi
  if [ -n "$CFG_BACKUP_DIR" ]; then
    info "sunucu configs yedeği: ${CFG_BACKUP_DIR#$SCRIPT_DIR/}/configs"
  fi
  cfg_pull_hints
  if [ "$TMDB_STATE" = yok ]; then
    info "sunucu .env'de TMDB_ACCESS_KEY yok -> TMDB zenginleştirme için ekleyin (sonra: systemctl restart $DZ_SERVICE)"
  fi
  if [ "$RC" = 0 ]; then
    printf '\n%sDeploy tamam.%s\n' "$C_GRN" "$C_RST"
  else
    printf '\n%sDeploy sorunlu bitti (çıkış kodu %s).%s\n' "$C_RED" "$RC" "$C_RST"
  fi
}

# --pull-configs: deploy YOK. Sunucu configs'ini indirir/yedekler; REMOTEONLY siteleri (+ --sync-configs ile KORU
# siteleri) yerele çeker. Sunucuya yazmaz; restart, tar gönderimi, yedek budama yok.
do_pull_configs() {
  step "[0/2] Ön kontrol"
  local_preflight
  remote_preflight pull

  step "[1/2] Sunucu configs: fark, yedek (indirme), yerele çekme"
  info "yön yalnız sunucu -> yerel; sunucuya HİÇBİR ŞEY yazılmaz (restart, tar gönderimi, yedek budama yok)"
  local_config_list_checked
  fetch_remote_cfg_list
  cfg_decide 0

  step "Sonraki adımlar"
  if [ -n "$CFG_BACKUP_DIR" ]; then info "sunucu configs yedeği: ${CFG_BACKUP_DIR#$SCRIPT_DIR/}/configs"; fi
  cfg_pull_hints
  if [ "$DRY_RUN" = 1 ]; then
    printf '\n%sDRY-RUN tamamlandı%s: sunucuya hiçbir şey yazılmadı, yerele hiçbir şey kopyalanmadı.\n' "$C_BLD" "$C_RST"
  elif [ -z "$PULLED_SITES" ] && [ -z "$REMOVED_SITES" ]; then
    printf '\n%sPull tamam.%s Yerel configs zaten güncel; yerele kopyalanan site yok.\n' "$C_GRN" "$C_RST"
  else
    printf '\n%sPull tamam.%s\n' "$C_GRN" "$C_RST"
  fi
}

do_rollback() {
  local bak pre
  step "[0/4] Ön kontrol"
  local_preflight
  remote_preflight

  step "[1/4] Yedek seçimi"
  bak=$ROLLBACK_PATH
  if [ -z "$bak" ]; then
    bak=$(rssh "ls -1t $DZ_BACKUP_DIR/diziflix-server-bak-*.tgz 2>/dev/null | head -n 1" || true)
    [ -n "$bak" ] || die "sunucuda $DZ_BACKUP_DIR/diziflix-server-bak-*.tgz yedeği yok"
  fi
  valid "$bak" '^/[A-Za-z0-9._/-]+$' "yedek yolu"
  rscript "$bak" > "$TMP/bak.info" <<'EOF' || die "yedek okunamadı: $bak (yok mu? bozuk mu?)"
[ -f "$1" ] || { echo "dosya yok: $1" >&2; exit 1; }
first=$(tar tzf "$1" 2>/dev/null | head -n 1)
case "$first" in server/*|server) ;; *) echo "beklenen 'server/' kökünde bir arşiv değil" >&2; exit 1 ;; esac
echo "$(du -h "$1" | cut -f1), $(tar tzf "$1" 2>/dev/null | grep -vc '/$') dosya, $(ls -l "$1" | awk '{print $6, $7, $8}')"
exit 0
EOF
  ok "yedek: $bak ($(tail -n 1 "$TMP/bak.info"))"

  if [ "$DRY_RUN" = 1 ]; then
    info "(dry-run) geri yüklenecekti: .env, data/, venv/ hariç; sonra import kontrolü, restart, doğrulama"
    printf '\n%sDRY-RUN tamamlandı%s: sunucuya hiçbir şey yazılmadı.\n' "$C_BLD" "$C_RST"
    return 0
  fi
  if ! confirm "$bak geri yüklensin mi? (.env, data/, venv/ korunur; mevcut kodun üzerine yazılır)"; then
    info "iptal edildi; hiçbir şey değiştirilmedi."
    return 0
  fi

  step "[2/4] Geri yükleme"
  pre="$DZ_BACKUP_DIR/diziflix-server-prerollback-$TS.tgz"
  make_backup "$pre"
  info "(rollback öncesi mevcut durum yukarıdaki dosyaya alındı)"
  rscript "$bak" "$DZ_DEST" > "$TMP/req.out" <<'EOF' || true
old=$(tar xzOf "$1" server/requirements.txt 2>/dev/null | md5sum | cut -d' ' -f1)
cur=$(md5sum "$2/requirements.txt" 2>/dev/null | cut -d' ' -f1)
[ "$old" = "$cur" ] && echo same || echo differ
EOF
  if ! rssh "tar xzf $bak -C $PARENT --no-same-owner --exclude=server/.env --exclude=server/data --exclude=server/venv"; then
    die "geri yükleme başarısız (mevcut durumun yedeği: $pre)"
  fi
  ok "yedek açıldı (.env/data/venv hariç; yedekten sonra eklenen dosyalar silinmez)"
  rssh "for d in app tests tools; do [ -d $DZ_DEST/\$d ] && find $DZ_DEST/\$d -name '._*' -type f -delete; done; exit 0" || true
  if [ "$(tail -n 1 "$TMP/req.out" 2>/dev/null)" = differ ]; then
    warn "yedekteki requirements.txt mevcuttan farklıydı; gerekirse sunucuda: cd $DZ_DEST && venv/bin/pip install -r requirements.txt"
  fi

  step "[3/4] Import kontrolü (venv/bin/python -c 'import app.main')"
  import_step

  step "[4/4] Restart + doğrulama (port $DZ_PORT)"
  do_restart
  verify_endpoints
  if [ "$VERIFY_FAIL" = 1 ]; then
    RC=2
    printf '\n%s!! DOĞRULAMA BAŞARISIZ !!%s  rollback öncesi durum: %s\n' "$C_RED" "$C_RST" "$pre"
  fi
  step "Sonraki adımlar"
  info "admin:  http://${DZ_HOST#*@}:$DZ_PORT/admin"
  info "rollback öncesi durum: $pre"
  if [ "$RC" = 0 ]; then
    printf '\n%sRollback tamam.%s\n' "$C_GRN" "$C_RST"
  else
    printf '\n%sRollback sorunlu bitti (çıkış kodu %s).%s\n' "$C_RED" "$RC" "$C_RST"
  fi
}

# ------------------------------------------------------------------ main ---
# `source deploy.sh` yalnız fonksiyonları yükler (tests/deploy_configs_test.sh); normal çalıştırmada etkisiz
if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi
BANNER_TAG=""
if [ "$DRY_RUN" = 1 ]; then BANNER_TAG="  [DRY-RUN]"; fi
if [ "$MODE" = pull ]; then
  printf '%sdiziflix pull-configs%s  %s:%s/app/scraper/configs -> yerel configs/  (sunucuya yazılmaz)%s\n' \
    "$C_BLD" "$C_RST" "$DZ_HOST" "$DZ_DEST" "$BANNER_TAG"
else
  printf '%sdiziflix deploy%s  %s -> %s  (servis %s, port %s)%s\n' \
    "$C_BLD" "$C_RST" "$DZ_HOST" "$DZ_DEST" "$DZ_SERVICE" "$DZ_PORT" "$BANNER_TAG"
fi

if [ "$PRUNE_DEPRECATED" = 1 ]; then
  warn "--prune-backups KULLANIMDAN KALKTI ve etkisiz: budama artık varsayılan (başarılı deploy sonunda son $KEEP yedek tutulur; kapatmak için --no-prune, sayı için --keep N)"
fi

case "$MODE" in
  deploy)   do_deploy ;;
  rollback) do_rollback ;;
  pull)     do_pull_configs ;;
esac
exit "$RC"
