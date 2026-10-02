#!/usr/bin/env bash
# clear-remote.sh: diziflix sunucusunun VERİTABANINI SIFIRLAR (test amaçlı). GERİ ALINAMAZ ama önce yedek alınır.
#
# Kullanım (ikisi de çalışır):
#   ./clear-remote.sh [--cache] [--no-backup] [--keep N] [--wipe-categories] [--yes]   # yerelden: ssh ile sunucuda çalıştırır, önce onay ister
#   ssh root@192.168.0.61 'bash -s' < clear-remote.sh                  # eski yol: doğrudan sunucuda, onaysız, varsayılan ayarlarla
#   ssh root@192.168.0.61 'bash -s -- --cache' < clear-remote.sh       # bayraklı hali
#
# Yaptığı (sunucuda):
#   1. `diziflix` servisini durdurur.
#   2. diziflix.db (+ -wal/-shm) dosyalarını data/diziflix.db.bak.<ts>* olarak yedekler (--no-backup yedeksiz; --keep N verilirse
#      yalnızca en yeni N yedek kalır, eskileri silinir; varsayılan: hiçbir yedek silinmez).
#   3. Veritabanı DOSYASI SİLİNMEZ: servis durukken `server/tools/reset_db_keep.py` korunan tablolar (KEEP_TABLES: ana ekran
#      kategorileri `home_categories` = admin kategorileri + sistem iskelet satırları) dışındaki TÜM tabloların satırlarını siler
#      (şema kalır, AUTOINCREMENT sıfırlanır, wal checkpoint + VACUUM). --wipe-categories kategorileri de siler.
#      Gerekçe: satır silme servis başlangıcına/şema yeniden kurulumuna bağımlı değil, tek işlemde atomik. Kategori üyeliği
#      (`library_lists` category_*) silinir; başlıklar gittiği için bir sonraki taramada scraper yeniden yazar.
#   4. --cache ile data/imgcache/ ve data/subcache/ içeriğini de siler (görsel/altyazı önbelleği; zararsız).
#   5. Servisi başlatır; açılışta şema yeniden kurulur. /api/health bekler ve sonucu yazar.
# Kütüphane boş başlar; siteler bir sonraki otomatik taramada (ya da admin "Şimdi tara" ile) yeniden dolar.
# Profiller, izleme ilerlemesi ve listem de SİLİNİR (hepsi aynı veritabanında). Kategoriler KORUNUR (--wipe-categories ile silinir).
# Gerekir: sunucuda server/tools/reset_db_keep.py (deploy ile gider) + python3 (venv/bin/python varsa o).
#
# DOKUNMAZ: .env, configs/, data/scraper_state/ (heal/tarama kayıtları; deploy.sh config kararında bunlara bakar),
#   data/ops_settings.json, data/_ops.json, data/fixture.json, diğer yedekler.
# Ortam: DZ_HOST (root@192.168.0.61), DZ_DEST (/root/micro_services/diziflix/server), DZ_SERVICE (diziflix), DZ_PORT (8090)
set -euo pipefail

DZ_HOST=${DZ_HOST:-root@192.168.0.61}
DZ_DEST=${DZ_DEST:-/root/micro_services/diziflix/server}
DZ_SERVICE=${DZ_SERVICE:-diziflix}
DZ_PORT=${DZ_PORT:-8090}
# Sıfırlamada satırları KORUNAN tablolar (virgülle). Kategoriye ihtiyaç duyan başka tablo çıkarsa yalnız burayı güncelle.
KEEP_TABLES=${KEEP_TABLES:-home_categories}

die() { echo "HATA: $*" >&2; exit 1; }

valid() { [[ "$1" =~ $2 ]] || die "geçersiz $3: $1"; }
valid "$DZ_HOST" '^[A-Za-z0-9][A-Za-z0-9._@:-]*$' DZ_HOST
valid "$DZ_DEST" '^/[A-Za-z0-9._/-]+$' DZ_DEST
valid "$DZ_SERVICE" '^[A-Za-z0-9][A-Za-z0-9._@-]*$' DZ_SERVICE
valid "$DZ_PORT" '^[0-9]{1,5}$' DZ_PORT
[ "$(basename "$DZ_DEST")" = "server" ] || die "DZ_DEST 'server' ile bitmeli: $DZ_DEST"

CACHE=0 BACKUP=1 KEEP=0 YES=0 WIPECAT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cache) CACHE=1 ;;
    --no-backup) BACKUP=0 ;;
    --keep) shift; KEEP=${1:-}; valid "$KEEP" '^[1-9][0-9]?$' "--keep (1-99)" ;;
    --wipe-categories) WIPECAT=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,/^[^#]/p' "$0" 2>/dev/null | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) die "bilinmeyen bayrak: $1 (--help)" ;;
  esac
  shift
done

# Sunucu tarafı. Tek fonksiyon: `ssh ... 'bash -s' < betik` ile stdin'den okunurken bash gövdeyi bütünüyle ayrıştırır.
main() {
  [ -d "$DZ_DEST" ] || die "sunucuda $DZ_DEST yok"
  # .env'den yalnızca yol anahtarlarını oku (başka satır okunmaz/yazdırılmaz).
  envval() { { grep -E "^$1=" "$DZ_DEST/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2- | tr -d "\"'" ; }
  abspath() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$DZ_DEST" "$1" ;; esac; }
  local db_env data_env data db
  db_env=$(envval DB_PATH); data_env=$(envval DATA_DIR)
  data=$(abspath "${data_env:-data}")
  db=$(abspath "${db_env:-${data_env:-data}/diziflix.db}")
  [[ "$db" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "veritabanı yolu beklenmedik: $db"
  [[ "$data" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "data yolu beklenmedik: $data"
  local tool="$DZ_DEST/tools/reset_db_keep.py" py="$DZ_DEST/venv/bin/python"
  [ -f "$tool" ] || die "sunucuda $tool yok (önce deploy et)"
  [ -x "$py" ] || py=python3
  command -v "$py" >/dev/null 2>&1 || die "python3 bulunamadı"
  valid "$KEEP_TABLES" '^[A-Za-z0-9_,]*$' KEEP_TABLES
  [ -f "$db" ] || echo "uyarı: $db yok (zaten sıfır?); yine de servis yeniden başlatılır"

  echo "== $DZ_SERVICE durduruluyor"
  systemctl stop "$DZ_SERVICE"

  if [ "$BACKUP" = 1 ] && [ -f "$db" ]; then
    local ts bak f
    ts=$(date +%Y%m%d-%H%M%S); bak="$db.bak.$ts"
    cp -p "$db" "$bak"
    for f in "$db-wal" "$db-shm"; do [ -f "$f" ] && cp -p "$f" "${bak}${f#"$db"}"; done
    echo "== yedek: $bak"
    if [ "$KEEP" -gt 0 ]; then   # yalnızca --keep N verilirse: ana yedek dosyaları (.bak.<ts>) arasında en yeni N kalır
      # shellcheck disable=SC2012
      ls -1t "$db".bak.[0-9]*-[0-9]* 2>/dev/null | grep -Ev -- '-(wal|shm)$' | tail -n +"$((KEEP + 1))" | while read -r old; do
        rm -f -- "$old" "$old-wal" "$old-shm"; echo "   eski yedek silindi: $old"
      done
    fi
  fi

  local rc=0 keepargs=(--keep "$KEEP_TABLES") out
  [ "$WIPECAT" = 1 ] && keepargs=(--wipe-categories)
  if [ -f "$db" ]; then
    out=$("$py" "$tool" "$db" "${keepargs[@]}") || rc=$?
    if [ "$rc" != 0 ]; then
      echo "HATA: sıfırlama başarısız (işlem geri alındı, veri değişmedi); servis eski veriyle başlatılıyor" >&2
      systemctl start "$DZ_SERVICE" || true
      return 1
    fi
    echo "== veritabanı sıfırlandı (dosya durur): $db"
    echo "   $out"
    if [ "$WIPECAT" = 1 ]; then echo "   kategoriler de silindi (--wipe-categories)"
    else echo "   korunan tablolar: $KEEP_TABLES (kategori satırı sayısı yukarıda)"; fi
  else
    echo "== veritabanı dosyası yok; sıfırlanacak bir şey yok"
  fi
  if [ "$CACHE" = 1 ]; then
    for d in "$data/imgcache" "$data/subcache"; do
      [ -d "$d" ] && find "$d" -mindepth 1 -delete && echo "== önbellek temizlendi: $d"
    done
  fi

  echo "== $DZ_SERVICE başlatılıyor"
  systemctl start "$DZ_SERVICE"
  local i
  for i in $(seq 1 20); do
    if curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$DZ_PORT/api/health" 2>/dev/null; then
      echo "== TAMAM: /api/health yanıt veriyor (veritabanı sıfırlandı)"; return 0
    fi
    sleep 1
  done
  echo "UYARI: 20 sn içinde /api/health yanıt vermedi. Bak: journalctl -u $DZ_SERVICE -n 50 --no-pager" >&2
  return 1
}

# Sunucudaysak (DZ_DEST var) doğrudan çalış; yerelse onay iste ve kendini ssh ile sunucuya gönder.
if [ -d "$DZ_DEST" ]; then
  main
  exit $?
fi
[ -f "$0" ] || die "sunucuda $DZ_DEST bulunamadı"
if [ "$YES" != 1 ]; then
  printf '%s\n   -> %s veritabanı SIFIRLANACAK (yedek: %s, önbellek temizliği: %s, kategoriler: %s). Devam için "evet" yaz: ' \
    "SUNUCU: $DZ_HOST" "$DZ_DEST/data" "$([ "$BACKUP" = 1 ] && echo var || echo YOK)" "$([ "$CACHE" = 1 ] && echo evet || echo hayır)" "$([ "$WIPECAT" = 1 ] && echo SİLİNECEK || echo korunur)"
  read -r answer
  [ "$answer" = "evet" ] || die "iptal edildi"
fi
# Düz dizge (macOS bash 3.2'de `set -u` ile boş dizi açılımı "unbound variable" verir); değerler yukarıda doğrulandı.
args=""
[ "$CACHE" = 1 ] && args="$args --cache"
[ "$BACKUP" = 1 ] || args="$args --no-backup"
[ "$WIPECAT" = 1 ] && args="$args --wipe-categories"
[ "$KEEP" -gt 0 ] && args="$args --keep $KEEP"
exec ssh "$DZ_HOST" "DZ_DEST='$DZ_DEST' DZ_SERVICE='$DZ_SERVICE' DZ_PORT='$DZ_PORT' bash -s --$args" < "$0"
