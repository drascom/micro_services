#!/usr/bin/env bash
# deploy.sh scraper configs karar/çekme mantığı testi (sunucuya bağlanmaz, ağ yok).
# Sahte remote/ ve local/ dizinleri üzerinde REMOTEONLY, KORU, EZ, --sync-configs ve --pull-configs senaryoları.
# Çalıştırma (proje kökünden): bash tests/deploy_configs_test.sh
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
set --   # deploy.sh'ye argüman geçmesin
# shellcheck disable=SC1091
source "$HERE/../deploy.sh"   # yalnız fonksiyonlar yüklenir (ana akış çalışmaz)
set +e                        # deploy.sh set -e açtı; testler başarısızlıkta devam eder

NO_COLOR=1; C_RED=''; C_GRN=''; C_YEL=''; C_BLD=''; C_RST=''
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/dz-cfgtest.XXXXXX")
BOOT_TMP=$TMP                 # deploy.sh'ın kendi geçici dizini (senaryolar $TMP'yi değiştirir)
trap 'rm -rf "$ROOT" "$BOOT_TMP"' EXIT
PASS=0; FAIL=0

t_ok()   { PASS=$((PASS + 1)); }
t_fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; if [ -s "$OUT" ]; then sed 's/^/    | /' "$OUT"; fi; }
assert() { # assert "ad" komut...   (komut 0 dönerse geçer)
  local name=$1; shift
  if "$@"; then t_ok; else t_fail "$name"; fi
}
has()     { grep -Fq -- "$1" "$2"; }                       # has METİN DOSYA
lacks()   { ! grep -Fq -- "$1" "$2"; }
same_f()  { cmp -s "$1" "$2"; }

# mk SİTE DİZİN SÜRÜM [NOT]  -> SİTE.yaml (+ baseline)
mk() {
  mkdir -p "$2"
  printf 'site_id: %s\nversion: %s\nnote: %s\n' "$1" "$3" "${4:-x}" > "$2/$1.yaml"
  printf '{"site":"%s","v":%s}\n' "$1" "$3" > "$2/$1.baseline.json"
}

# mkp AD DİZİN SÜRÜM [NOT]  -> DİZİN/providers/AD.yaml (provider tarifi; .baseline.json yok)
mkp() {
  mkdir -p "$2/providers"
  printf 'name: %s\nversion: %s\nnote: %s\n' "$1" "$3" "${4:-x}" > "$2/providers/$1.yaml"
}

# scenario ADI -> temiz ortam: $LOCAL_CFG=local/, $REMOTE=remote/, $TMP=çalışma
scenario() {
  SC="$ROOT/$1"; rm -rf "$SC"; mkdir -p "$SC/local" "$SC/remote" "$SC/tmp"
  LOCAL_CFG="$SC/local"; REMOTE="$SC/remote"; TMP="$SC/tmp"; CFG_EXCL="$TMP/cfg.excl"; : > "$CFG_EXCL"
  OUT="$SC/out.txt"; : > "$OUT"
  DRY_RUN=0; KEEP_CONFIGS=0; OVERWRITE_CONFIGS=0; SYNC_CONFIGS=0; MODE=deploy; YES=1
  CFG_BACKUP_DIR=""; CFG_WILL_DOWNLOAD=0; CFG_KEPT=""; PULLED_SITES=""
  HEALS=""   # "site toplam uygulanan" satırları (sunucu heal özeti)
  DELSITES=""; DSS_STATE=ok   # sunucudaki deleted_sites.json: "DS <site>" satırları ve okunabilirlik (ok|err)
  REMOVED_SITES=""; LOCAL_REMOVED_BASE="$SC/backup"   # yerelden kaldırılan siteler; yedek klasörü tabanı (gerçek deploy-backups/ değil)
}

# cfg_download_backup'ın ssh'sız karşılığı: sahte remote/ -> yedek klasörü
cfg_download_backup() {
  CFG_BACKUP_DIR="$SC/backup/remote-configs-test"
  mkdir -p "$CFG_BACKUP_DIR"
  cp -R "$REMOTE" "$CFG_BACKUP_DIR/configs"
}

# run_decide [lo]: yerel+sunucu listesini kurar, cfg_decide'ı çalıştırır (çıktı $OUT'a)
run_decide() {
  local_config_list_checked
  ( LOCAL_CFG="$REMOTE"; local_config_list ) > "$TMP/cfg.remote"
  { [ -z "$HEALS" ] || printf '%s\n' "$HEALS" | sed 's/^/H /'; echo "HS ok"
    for ds in $DELSITES; do echo "DS $ds"; done; echo "DSS ${DSS_STATE:-ok}"; } >> "$TMP/cfg.remote"
  cfg_decide "${1:-0}" > "$OUT" 2>&1
}

act_of() { awk -v s="$1" '$1 == s { print $3 }' "$TMP/cfg.act"; }
cls_of() { awk -v s="$1" '$1 == s { print $2 }' "$TMP/cfg.act"; }
in_excl() { grep -Fxq "./$1" "$CFG_EXCL"; }

# ---------------------------------------------------------------- 1) REMOTEONLY
scenario remoteonly
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1                       # ortak, aynı
mk alpha "$REMOTE" 2; printf 'version: 1\n' > "$REMOTE/alpha.v1.yaml"   # yalnız sunucuda (+ arşiv)
echo lock > "$REMOTE/.config.lock"                                 # site olmayan dosya: çekilmez
run_decide 0
assert "REMOTEONLY sınıfı"            test "$(cls_of alpha)" = REMOTEONLY
assert "REMOTEONLY eylemi PULL"       test "$(act_of alpha)" = PULL
assert "beta AYNI"                    test "$(cls_of beta)" = SAME
assert "alpha.yaml yerele çekildi"    same_f "$REMOTE/alpha.yaml" "$LOCAL_CFG/alpha.yaml"
assert "alpha.baseline.json çekildi"  same_f "$REMOTE/alpha.baseline.json" "$LOCAL_CFG/alpha.baseline.json"
assert "alpha.v1.yaml (arşiv) çekildi" same_f "$REMOTE/alpha.v1.yaml" "$LOCAL_CFG/alpha.v1.yaml"
assert ".config.lock çekilmedi"       test ! -e "$LOCAL_CFG/.config.lock"
assert "çıktı: yerele çekildi: alpha (v2)" has "yerele çekildi: alpha (v2)" "$OUT"
assert "tar'dan dışlandı: yaml"       in_excl alpha.yaml
assert "tar'dan dışlandı: baseline"   in_excl alpha.baseline.json
assert "tar'dan dışlandı: arşiv"      in_excl alpha.v1.yaml
assert "beta dışlanmadı"              test ! "$(grep -c beta "$CFG_EXCL")" -gt 0
assert "PULLED_SITES alpha"           test "$PULLED_SITES" = " alpha"
assert "indirme yapıldı (yedek var)"  test -d "$SC/backup/remote-configs-test/configs"
assert "REMOTEONLY satırı yazıldı"    has "REMOTEONLY (sunucuda var, yerelde yok): alpha" "$OUT"

# ---------------------------------------------------------------- 2) REMOTEONLY dry-run
scenario remoteonly_dry
mk alpha "$REMOTE" 3
DRY_RUN=1
run_decide 0
assert "dry-run: kopyalanmadı"        test ! -e "$LOCAL_CFG/alpha.yaml"
assert "dry-run: çekilecekti"         has "yerele çekilecekti: alpha (v3)" "$OUT"
assert "dry-run: indirme yok"         test ! -d "$SC/backup"
assert "dry-run: PULLED_SITES boş"    test -z "$PULLED_SITES"

# ---------------------------------------------------------------- 3) REMOTEONLY yok -> boş liste bildirilir
scenario remoteonly_none
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
DRY_RUN=1
run_decide 0
assert "boş REMOTEONLY listesi"       has "REMOTEONLY (sunucuda var, yerelde yok): (yok)" "$OUT"

# ---------------------------------------------------------------- 4) güvensiz ad atlanır
scenario unsafe
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mk 'ev;il' "$REMOTE" 1
run_decide 0
assert "güvensiz adlı site çekilmedi" test ! -e "$LOCAL_CFG/ev;il.yaml"
assert "güvensiz site REMOTEONLY değil" test -z "$(cls_of 'ev;il')"

# ---------------------------------------------------------------- 5) KORU (sunucu daha yeni), sync kapalı
scenario keep
mk gamma "$LOCAL_CFG" 1 yerel; mk gamma "$REMOTE" 2 sunucu
run_decide 0
assert "KEEPVER sınıfı"               test "$(cls_of gamma)" = KEEPVER
assert "KORU eylemi"                  test "$(act_of gamma)" = KEEP
assert "sync kapalı: yerel dokunulmadı" grep -q 'note: yerel' "$LOCAL_CFG/gamma.yaml"
assert "KORU: tar'dan dışlandı"       in_excl gamma.yaml
assert "sync kapalı: eşitleme çıktısı yok" lacks "yerele eşitlendi" "$OUT"
assert "PULLED_SITES boş"             test -z "$PULLED_SITES"

# ---------------------------------------------------------------- 6) KORU + --sync-configs
scenario keep_sync
mk gamma "$LOCAL_CFG" 1 yerel; mk gamma "$REMOTE" 2 sunucu
printf 'version: 1\n' > "$REMOTE/gamma.v1.yaml"                   # sunucuda yeni arşiv
SYNC_CONFIGS=1
run_decide 0
assert "sync: yaml sunucuyla aynı"    same_f "$REMOTE/gamma.yaml" "$LOCAL_CFG/gamma.yaml"
assert "sync: baseline sunucuyla aynı" same_f "$REMOTE/gamma.baseline.json" "$LOCAL_CFG/gamma.baseline.json"
assert "sync: arşiv de geldi"         same_f "$REMOTE/gamma.v1.yaml" "$LOCAL_CFG/gamma.v1.yaml"
assert "sync: çıktı"                  has "yerele eşitlendi: gamma (yerel v1 -> v2)" "$OUT"
assert "sync: tar'dan dışlandı"       in_excl gamma.yaml
assert "sync: arşiv dışlandı"         in_excl gamma.v1.yaml
assert "sync: PULLED_SITES gamma"     test "$PULLED_SITES" = " gamma"
assert "dışlama listesinde tekrar yok" test "$(sort "$CFG_EXCL" | uniq -d | wc -l | tr -d ' ')" = 0

# ---------------------------------------------------------------- 7) EZ (yerel daha yeni, heal yok): sync dokunmaz
scenario auto
mk delta "$LOCAL_CFG" 3 yerel; mk delta "$REMOTE" 2 sunucu
SYNC_CONFIGS=1
run_decide 0
assert "AUTO sınıfı"                  test "$(cls_of delta)" = AUTO
assert "EZ eylemi (SEND)"             test "$(act_of delta)" = SEND
assert "EZ: yerel korunur"            grep -q 'note: yerel' "$LOCAL_CFG/delta.yaml"
assert "EZ: dışlanmaz"                test ! -s "$CFG_EXCL"
assert "EZ: çekme yok"                test -z "$PULLED_SITES"

# ---------------------------------------------------------------- 8) --keep-configs + --sync-configs: yerel daha yeni -> atlanır
scenario auto_keep
mk delta "$LOCAL_CFG" 3 yerel; mk delta "$REMOTE" 2 sunucu
KEEP_CONFIGS=1; SYNC_CONFIGS=1
run_decide 0
assert "AUTO+keep: KORU"              test "$(act_of delta)" = KEEP
assert "AUTO+keep: yerel ezilmedi"    grep -q 'note: yerel' "$LOCAL_CFG/delta.yaml"
assert "AUTO+keep: atlandı bilgisi"   has "--sync-configs atlanan" "$OUT"

# ---------------------------------------------------------------- 9) KORU (heal kaydı) + sync
scenario heal_sync
mk eps "$LOCAL_CFG" 4 yerel; mk eps "$REMOTE" 3 sunucu
HEALS="eps 2 1"
SYNC_CONFIGS=1
run_decide 0
assert "KEEPHEAL sınıfı"              test "$(cls_of eps)" = KEEPHEAL
assert "KEEPHEAL sync: sunucuyla aynı" same_f "$REMOTE/eps.yaml" "$LOCAL_CFG/eps.yaml"
assert "yerel>sunucu uyarısı"         has "yerel v4 sunucudan (v3) yüksek" "$OUT"

# ---------------------------------------------------------------- 10) yaml aynı, baseline farklı (BASELINE) + sync
scenario baseline
mk zeta "$LOCAL_CFG" 2; cp "$LOCAL_CFG/zeta.yaml" "$REMOTE/zeta.yaml"
printf '{"runtime":1}\n' > "$REMOTE/zeta.baseline.json"; printf '{"old":1}\n' > "$LOCAL_CFG/zeta.baseline.json"
run_decide 0
assert "BASELINE sınıfı"              test "$(cls_of zeta)" = BASELINE
assert "BASELINE sync kapalı: yerel kaldı" grep -q old "$LOCAL_CFG/zeta.baseline.json"
scenario baseline_sync
mk zeta "$LOCAL_CFG" 2; cp "$LOCAL_CFG/zeta.yaml" "$REMOTE/zeta.yaml"
printf '{"runtime":1}\n' > "$REMOTE/zeta.baseline.json"; printf '{"old":1}\n' > "$LOCAL_CFG/zeta.baseline.json"
SYNC_CONFIGS=1
run_decide 0
assert "BASELINE sync: baseline geldi" same_f "$REMOTE/zeta.baseline.json" "$LOCAL_CFG/zeta.baseline.json"
assert "BASELINE sync: yaml aynı kaldı" same_f "$REMOTE/zeta.yaml" "$LOCAL_CFG/zeta.yaml"

# ---------------------------------------------------------------- 11) --sync-configs dry-run: kopyalanmaz
scenario sync_dry
mk gamma "$LOCAL_CFG" 1 yerel; mk gamma "$REMOTE" 2 sunucu
SYNC_CONFIGS=1; DRY_RUN=1
run_decide 0
assert "sync dry-run: kopyalanmadı"   grep -q 'note: yerel' "$LOCAL_CFG/gamma.yaml"
assert "sync dry-run: eşitlenecekti"  has "yerele eşitlenecekti: gamma" "$OUT"

# ---------------------------------------------------------------- 11b) AYNI site + yalnız sunucuda arşiv
scenario same_arch
mk sigma "$LOCAL_CFG" 9; mk sigma "$REMOTE" 9; printf 'version: 8\n' > "$REMOTE/sigma.v8.yaml"
run_decide 0
assert "AYNI+arşiv: sınıf SAME"       test "$(cls_of sigma)" = SAME
assert "sync kapalı: arşiv gelmedi"   test ! -e "$LOCAL_CFG/sigma.v8.yaml"
assert "sync kapalı: ipucu"           has "--sync-configs ile çekilir" "$OUT"
scenario same_arch_sync
mk sigma "$LOCAL_CFG" 9 yerel; mk sigma "$REMOTE" 9 yerel; printf 'version: 8\n' > "$REMOTE/sigma.v8.yaml"
SYNC_CONFIGS=1
run_decide 0
assert "sync: arşiv eklendi"          same_f "$REMOTE/sigma.v8.yaml" "$LOCAL_CFG/sigma.v8.yaml"
assert "sync: var olan yaml aynı"     same_f "$REMOTE/sigma.yaml" "$LOCAL_CFG/sigma.yaml"
assert "sync: çıktı"                  has "yerele eklendi: sigma" "$OUT"

# ---------------------------------------------------------------- 12) --pull-configs modu
scenario pullmode
mk alpha "$REMOTE" 2                                              # REMOTEONLY
mk gamma "$LOCAL_CFG" 1 yerel; mk gamma "$REMOTE" 2 sunucu       # KORU adayı
mk delta "$LOCAL_CFG" 3 yerel; mk delta "$REMOTE" 2 sunucu       # EZ adayı (pull'da gönderilmez)
mk onlylocal "$LOCAL_CFG" 1                                       # yalnız yerelde
MODE=pull
run_decide 0
assert "pull: REMOTEONLY çekildi"     same_f "$REMOTE/alpha.yaml" "$LOCAL_CFG/alpha.yaml"
assert "pull: sync yok -> gamma yerel" grep -q 'note: yerel' "$LOCAL_CFG/gamma.yaml"
assert "pull: delta yerel kaldı"      grep -q 'note: yerel' "$LOCAL_CFG/delta.yaml"
assert "pull: gönderim eylemi yok"    test -z "$(awk '$3 == "SEND" || $3 == "ASK"' "$TMP/cfg.act")"
assert "pull: 'deploy dışlanacak' uyarısı yok" lacks "deploy'dan dışlanacak" "$OUT"
assert "pull: gamma için sync ipucu"  has "--sync-configs" "$OUT"

scenario pullmode_sync
mk alpha "$REMOTE" 2
mk gamma "$LOCAL_CFG" 1 yerel; mk gamma "$REMOTE" 2 sunucu
mk delta "$LOCAL_CFG" 3 yerel; mk delta "$REMOTE" 2 sunucu
MODE=pull; SYNC_CONFIGS=1
run_decide 0
assert "pull+sync: alpha çekildi"     same_f "$REMOTE/alpha.yaml" "$LOCAL_CFG/alpha.yaml"
assert "pull+sync: gamma eşitlendi"   same_f "$REMOTE/gamma.yaml" "$LOCAL_CFG/gamma.yaml"
assert "pull+sync: delta (yerel yeni) dokunulmadı" grep -q 'note: yerel' "$LOCAL_CFG/delta.yaml"

scenario pullmode_dry
mk alpha "$REMOTE" 2
MODE=pull; DRY_RUN=1
run_decide 0
assert "pull dry-run: kopya yok"      test ! -e "$LOCAL_CFG/alpha.yaml"
assert "pull dry-run: çekilecekti"    has "yerele çekilecekti: alpha" "$OUT"

# ---------------------------------------------------------------- 12b) çekilen dosyalar tar'dan dışlanır; server/pi/ gider
scenario tar
SCRIPT_DIR="$SC"; LOCAL_SERVER="$SC/server"; LOCAL_CFG="$LOCAL_SERVER/app/scraper/configs"
mkdir -p "$LOCAL_CFG" "$LOCAL_SERVER/pi/skills" "$LOCAL_SERVER/data"
touch "$LOCAL_SERVER/app/main.py" "$LOCAL_SERVER/requirements.txt" "$LOCAL_SERVER/pi/skills/SKILL.md" "$LOCAL_SERVER/data/x.db" "$LOCAL_SERVER/.env"
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mk alpha "$REMOTE" 2
run_decide 0
list_archive > "$OUT" 2>&1
assert "tar: çekilen alpha.yaml gönderilmez"   lacks "server/app/scraper/configs/alpha.yaml" "$TMP/list.files"
assert "tar: çekilen baseline gönderilmez"     lacks "server/app/scraper/configs/alpha.baseline.json" "$TMP/list.files"
assert "tar: ortak site (beta) normal gider"   has "server/app/scraper/configs/beta.yaml" "$TMP/list.files"
assert "tar: server/pi/ otomatik gider"        has "server/pi/skills/SKILL.md" "$TMP/list.files"
assert "tar: data/.env gitmez"                 test -z "$(grep -E '^server/(data|\.env)' "$TMP/list.files")"
SCRIPT_DIR=$(cd "$HERE/.." && pwd); LOCAL_SERVER="$SCRIPT_DIR/server"

# ---------------------------------------------------------------- 12c) provider tarifleri (configs/providers/)
scenario recipe_remoteonly
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mkp trdizi_player "$REMOTE" 1; printf 'version: 0\n' > "$REMOTE/providers/trdizi_player.v0.yaml"
mkp old_player "$REMOTE" 3
mkdir -p "$LOCAL_CFG"
run_decide 0
assert "tarif: REMOTEONLY sınıfı"          test "$(cls_of providers/trdizi_player)" = REMOTEONLY
assert "tarif: eylem PULL"                 test "$(act_of providers/trdizi_player)" = PULL
assert "tarif: yaml yerele çekildi"        same_f "$REMOTE/providers/trdizi_player.yaml" "$LOCAL_CFG/providers/trdizi_player.yaml"
assert "tarif: arşiv yerele çekildi"       same_f "$REMOTE/providers/trdizi_player.v0.yaml" "$LOCAL_CFG/providers/trdizi_player.v0.yaml"
assert "tarif: ikinci tarif de çekildi"    same_f "$REMOTE/providers/old_player.yaml" "$LOCAL_CFG/providers/old_player.yaml"
assert "tarif: çıktı yerele çekildi"       has "yerele çekildi: provider trdizi_player (v1)" "$OUT"
assert "tarif: çıktı sürüm v3"             has "yerele çekildi: provider old_player (v3)" "$OUT"
assert "tarif: tar'dan dışlandı"           in_excl providers/trdizi_player.yaml
assert "tarif: arşiv tar'dan dışlandı"     in_excl providers/trdizi_player.v0.yaml
assert "tarif: site beta etkilenmedi"      test "$(cls_of beta)" = SAME
assert "tarif: PULLED_SITES"               has "providers/trdizi_player" <(printf '%s\n' "$PULLED_SITES")
assert "tarif: tablo satırı provider:ad"   has "provider:trdizi_player" "$OUT"

scenario recipe_dry
mkp trdizi_player "$REMOTE" 2
DRY_RUN=1
run_decide 0
assert "tarif dry-run: kopyalanmadı"       test ! -e "$LOCAL_CFG/providers/trdizi_player.yaml"
assert "tarif dry-run: çekilecekti"        has "yerele çekilecekti: provider trdizi_player (v2)" "$OUT"

scenario recipe_keep
mkp ply "$LOCAL_CFG" 1 yerel; mkp ply "$REMOTE" 2 sunucu          # sunucu sürümü yerelden yüksek
run_decide 0
assert "tarif KEEPVER sınıfı"              test "$(cls_of providers/ply)" = KEEPVER
assert "tarif KORU eylemi"                 test "$(act_of providers/ply)" = KEEP
assert "tarif KORU: tar'dan dışlandı"      in_excl providers/ply.yaml
assert "tarif KORU: yerel dokunulmadı"     grep -q 'note: yerel' "$LOCAL_CFG/providers/ply.yaml"
scenario recipe_keep_sync
mkp ply "$LOCAL_CFG" 1 yerel; mkp ply "$REMOTE" 2 sunucu
SYNC_CONFIGS=1
run_decide 0
assert "tarif sync: sunucuyla aynı"        same_f "$REMOTE/providers/ply.yaml" "$LOCAL_CFG/providers/ply.yaml"
assert "tarif sync: çıktı"                 has "yerele eşitlendi: provider ply (yerel v1 -> v2)" "$OUT"

scenario recipe_auto
mkp ply "$LOCAL_CFG" 3 yerel; mkp ply "$REMOTE" 2 sunucu          # yerel daha yeni, heal/arşiv izi yok
run_decide 0
assert "tarif AUTO sınıfı"                 test "$(cls_of providers/ply)" = AUTO
assert "tarif EZ eylemi (SEND)"            test "$(act_of providers/ply)" = SEND
assert "tarif EZ: dışlanmaz"               test ! -s "$CFG_EXCL"
scenario recipe_auto_keep
mkp ply "$LOCAL_CFG" 3 yerel; mkp ply "$REMOTE" 2 sunucu
KEEP_CONFIGS=1
run_decide 0
assert "tarif AUTO + --keep-configs: KORU" test "$(act_of providers/ply)" = KEEP
assert "tarif AUTO + --keep-configs: dışlandı" in_excl providers/ply.yaml

scenario recipe_heal_trace
mkp ply "$LOCAL_CFG" 3 yerel; mkp ply "$REMOTE" 2 sunucu
printf 'version: 1\n' > "$REMOTE/providers/ply.v1.yaml"            # sunucuda yerelde olmayan arşiv = heal izi
run_decide 0
assert "tarif arşiv izi: KEEPHEAL"         test "$(cls_of providers/ply)" = KEEPHEAL
assert "tarif arşiv izi: KORU"             test "$(act_of providers/ply)" = KEEP
assert "tarif arşiv izi: dışlandı"         in_excl providers/ply.yaml

scenario recipe_newlocal
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mkp fresh_player "$LOCAL_CFG" 1                                    # yalnız yerelde: yeni tarif gider
run_decide 0
assert "tarif yalnız yerelde: NEWSITE"     test "$(cls_of providers/fresh_player)" = NEWSITE
assert "tarif yalnız yerelde: GÖNDER"      test "$(act_of providers/fresh_player)" = SEND
assert "tarif yalnız yerelde: dışlanmaz"   test ! -s "$CFG_EXCL"

scenario recipe_unsafe
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mkp Bad_Name "$REMOTE" 1; mkp x "$REMOTE" 1                        # büyük harf / tek harf: ad kalıbına uymaz
mkdir -p "$REMOTE/providers"; printf 'a: 1\n' > "$REMOTE/providers/notes.txt"; printf 'a: 1\n' > "$REMOTE/providers/ok_name.json"
run_decide 0
assert "tarif güvensiz ad: çekilmedi"      test ! -e "$LOCAL_CFG/providers/Bad_Name.yaml"
assert "tarif tek harfli ad: çekilmedi"    test ! -e "$LOCAL_CFG/providers/x.yaml"
assert "tarif başka uzantı: çekilmedi"     test ! -e "$LOCAL_CFG/providers/notes.txt"
assert "tarif güvensiz: REMOTEONLY değil"  test -z "$(cls_of providers/Bad_Name)"
scenario recipe_unknown_file
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mkdir -p "$LOCAL_CFG/providers" "$REMOTE/providers"
printf 'yerel\n' > "$LOCAL_CFG/providers/README.txt"; printf 'sunucu\n' > "$REMOTE/providers/README.txt"
run_decide 0
assert "tarif: tanınmayan dosya KORU"      test "$(act_of providers/README.txt)" = KEEP
assert "tarif: tanınmayan dosya UNKFILE"   test "$(cls_of providers/README.txt)" = UNKFILE

scenario recipe_pullmode
mkp trdizi_player "$REMOTE" 1
mkp ply "$LOCAL_CFG" 1 yerel; mkp ply "$REMOTE" 2 sunucu
MODE=pull
run_decide 0
assert "tarif pull: REMOTEONLY çekildi"    same_f "$REMOTE/providers/trdizi_player.yaml" "$LOCAL_CFG/providers/trdizi_player.yaml"
assert "tarif pull: sync yok -> yerel kaldı" grep -q 'note: yerel' "$LOCAL_CFG/providers/ply.yaml"
scenario recipe_pullmode_sync
mkp ply "$LOCAL_CFG" 1 yerel; mkp ply "$REMOTE" 2 sunucu
MODE=pull; SYNC_CONFIGS=1
run_decide 0
assert "tarif pull+sync: eşitlendi"        same_f "$REMOTE/providers/ply.yaml" "$LOCAL_CFG/providers/ply.yaml"

# çekilen tarif tar'dan dışlanır, yalnız yerelde olan tarif gider; mevcut site davranışı aynı
scenario recipe_tar
SCRIPT_DIR="$SC"; LOCAL_SERVER="$SC/server"; LOCAL_CFG="$LOCAL_SERVER/app/scraper/configs"
mkdir -p "$LOCAL_CFG" "$LOCAL_SERVER/data"
touch "$LOCAL_SERVER/app/main.py" "$LOCAL_SERVER/requirements.txt"
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mkp mine "$LOCAL_CFG" 1                                            # yalnız yerelde
mkp pulled "$REMOTE" 1                                             # yalnız sunucuda
run_decide 0
list_archive > "$OUT" 2>&1
assert "tarif tar: çekilen tarif gönderilmez"   lacks "server/app/scraper/configs/providers/pulled.yaml" "$TMP/list.files"
assert "tarif tar: yerel yeni tarif gider"      has "server/app/scraper/configs/providers/mine.yaml" "$TMP/list.files"
assert "tarif tar: site beta normal gider"      has "server/app/scraper/configs/beta.yaml" "$TMP/list.files"
SCRIPT_DIR=$(cd "$HERE/.." && pwd); LOCAL_SERVER="$SCRIPT_DIR/server"

# ---------------------------------------------------------------- 12d) sunucuda silinmiş siteler (DELETED)
scenario deleted
mk gone "$LOCAL_CFG" 3; printf 'version: 2\n' > "$LOCAL_CFG/gone.v2.yaml"; printf 'version: 1\n' > "$LOCAL_CFG/gone.v1.yaml"
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
mkp pl "$LOCAL_CFG" 1; mkp pl "$REMOTE" 1                          # provider tarifi: dokunulmaz
DELSITES="gone"
run_decide 0
assert "silinmiş: DELETED sınıfı"        test "$(cls_of gone)" = DELETED
assert "silinmiş: REMOVE eylemi"         test "$(act_of gone)" = REMOVE
assert "silinmiş: yaml yerelden gitti"   test ! -e "$LOCAL_CFG/gone.yaml"
assert "silinmiş: baseline gitti"        test ! -e "$LOCAL_CFG/gone.baseline.json"
assert "silinmiş: arşivler gitti"        test ! -e "$LOCAL_CFG/gone.v1.yaml" -a ! -e "$LOCAL_CFG/gone.v2.yaml"
assert "silinmiş: yaml yedeğe taşındı"   test -f "$SC/backup/local-removed-$TS/gone.yaml"
assert "silinmiş: baseline yedekte"      test -f "$SC/backup/local-removed-$TS/gone.baseline.json"
assert "silinmiş: arşivler yedekte"      test -f "$SC/backup/local-removed-$TS/gone.v1.yaml" -a -f "$SC/backup/local-removed-$TS/gone.v2.yaml"
assert "silinmiş: çıktı"                 has "yerelden kaldırıldı: gone (sunucuda silinmiş)" "$OUT"
assert "silinmiş: tablo satırı SİL"      has "SİL" "$OUT"
assert "silinmiş: REMOVED_SITES"         test "$REMOVED_SITES" = " gone"
assert "silinmiş: tar'dan dışlandı"      in_excl gone.yaml
assert "silinmiş: baseline dışlandı"     in_excl gone.baseline.json
assert "silinmiş: diğer site AYNI"       test "$(cls_of beta)" = SAME
assert "silinmiş: beta dokunulmadı"      test -f "$LOCAL_CFG/beta.yaml"
assert "silinmiş: providers dokunulmadı" test -f "$LOCAL_CFG/providers/pl.yaml"
assert "silinmiş: sunucu configs indirilmedi" test ! -d "$SC/backup/remote-configs-test"
assert "silinmiş: sunucuya yazma yok (ssh yok)" test -z "$(grep -c rssh "$OUT" | grep -v '^0$')"

scenario deleted_dry
mk gone "$LOCAL_CFG" 3; mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
DELSITES="gone"; DRY_RUN=1
run_decide 0
assert "dry-run: DELETED sınıfı"         test "$(cls_of gone)" = DELETED
assert "dry-run: dosyalar yerinde"       test -f "$LOCAL_CFG/gone.yaml" -a -f "$LOCAL_CFG/gone.baseline.json"
assert "dry-run: kaldırılacaktı listesi" has "yerelden kaldırılacaktı: gone (sunucuda silinmiş" "$OUT"
assert "dry-run: dosya adları listelenir" has "gone.yaml" "$OUT"
assert "dry-run: yedek klasörü yok"      test ! -d "$SC/backup"
assert "dry-run: REMOVED_SITES boş"      test -z "$REMOVED_SITES"
assert "dry-run: yine de tar'dan dışlanır" in_excl gone.yaml
assert "dry-run: 'kaldırıldı' yazılmaz"  lacks "yerelden kaldırıldı:" "$OUT"

scenario deleted_but_remote_has_it                                  # tutarsız: kayıt var ama sunucuda config de var
mk gone "$LOCAL_CFG" 1; mk gone "$REMOTE" 1
DELSITES="gone"
run_decide 0
assert "tutarsız: DELETED değil (AYNI)"  test "$(cls_of gone)" = SAME
assert "tutarsız: yerel dosya yerinde"   test -f "$LOCAL_CFG/gone.yaml"
assert "tutarsız: kaldırma yok"          lacks "yerelden kaldırıldı" "$OUT"

scenario deleted_unreadable                                         # sunucudaki kayıt okunamadı: hiçbir şey kaldırılmaz
mk gone "$LOCAL_CFG" 1; mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
DELSITES="gone"; DSS_STATE=err
run_decide 0
assert "okunamadı: DELETED değil"        test "$(cls_of gone)" = NEWSITE
assert "okunamadı: yerel dosya yerinde"  test -f "$LOCAL_CFG/gone.yaml"
assert "okunamadı: tar'dan dışlanmaz"    test ! -s "$CFG_EXCL"

scenario deleted_nolocal                                            # silinmiş ama yerelde zaten yok: sessizce geçer
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
DELSITES="ghost"
run_decide 0
assert "yerelde yok: satır üretilmez"    test -z "$(cls_of ghost)"
assert "yerelde yok: yedek klasörü yok"  test ! -d "$SC/backup"
assert "yerelde yok: beta AYNI"          test "$(cls_of beta)" = SAME

scenario deleted_pull
mk gone "$LOCAL_CFG" 2; mk alpha "$REMOTE" 2; mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1
DELSITES="gone"; MODE=pull
run_decide 0
cfg_pull_hints >> "$OUT" 2>&1                                       # do_pull_configs'in kapanış ipuçları
assert "pull: DELETED kaldırıldı"        test ! -e "$LOCAL_CFG/gone.yaml" -a -f "$SC/backup/local-removed-$TS/gone.yaml"
assert "pull: REMOTEONLY yine çekildi"   same_f "$REMOTE/alpha.yaml" "$LOCAL_CFG/alpha.yaml"
assert "pull: satır SİL"                 has "SİL" "$OUT"
assert "pull: ipucu"                     has "yerelden kaldırılan (sunucuda silinmiş) siteler: gone" "$OUT"
assert "pull: PULLED_SITES alpha"        test "$PULLED_SITES" = " alpha"

scenario deleted_pull_dry
mk gone "$LOCAL_CFG" 2; DELSITES="gone"; MODE=pull; DRY_RUN=1
run_decide 0
assert "pull dry-run: dosya yerinde"     test -f "$LOCAL_CFG/gone.yaml"
assert "pull dry-run: kaldırılacaktı"    has "yerelden kaldırılacaktı: gone" "$OUT"

scenario deleted_localonly                                          # --local-only: sunucu bilinmez, hiçbir şey kaldırılmaz
mk gone "$LOCAL_CFG" 1
DRY_RUN=1
local_config_list_checked; : > "$TMP/cfg.remote"
cfg_decide 1 > "$OUT" 2>&1
assert "local-only: LOCALONLY sınıfı"    test "$(cls_of gone)" = LOCALONLY
assert "local-only: dosya yerinde"       test -f "$LOCAL_CFG/gone.yaml"

# silinmiş site tar'a girmez (hem dry-run'da hem kaldırılmış halde); geri kalan normal gider
scenario deleted_tar
SCRIPT_DIR="$SC"; LOCAL_SERVER="$SC/server"; LOCAL_CFG="$LOCAL_SERVER/app/scraper/configs"
mkdir -p "$LOCAL_CFG" "$LOCAL_SERVER/data"
touch "$LOCAL_SERVER/app/main.py" "$LOCAL_SERVER/requirements.txt"
mk beta "$LOCAL_CFG" 1; mk beta "$REMOTE" 1; mk gone "$LOCAL_CFG" 4; printf 'version: 3\n' > "$LOCAL_CFG/gone.v3.yaml"
DELSITES="gone"; DRY_RUN=1
run_decide 0
list_archive > "$OUT" 2>&1
assert "tar dry-run: silinmiş yaml gitmez"     lacks "configs/gone.yaml" "$TMP/list.files"
assert "tar dry-run: silinmiş baseline gitmez" lacks "configs/gone.baseline.json" "$TMP/list.files"
assert "tar dry-run: silinmiş arşiv gitmez"    lacks "configs/gone.v3.yaml" "$TMP/list.files"
assert "tar dry-run: beta normal gider"        has "server/app/scraper/configs/beta.yaml" "$TMP/list.files"
assert "tar dry-run: dosyalar yerinde"         test -f "$LOCAL_CFG/gone.yaml"
DRY_RUN=0; : > "$CFG_EXCL"
run_decide 0
list_archive > "$OUT" 2>&1
assert "tar: kaldırılan site gitmez"           lacks "configs/gone." "$TMP/list.files"
assert "tar: kaldırılan dosyalar yerelde yok"  test ! -e "$LOCAL_CFG/gone.yaml" -a -f "$SC/backup/local-removed-$TS/gone.v3.yaml"
assert "tar: beta normal gider"                has "server/app/scraper/configs/beta.yaml" "$TMP/list.files"
SCRIPT_DIR=$(cd "$HERE/.." && pwd); LOCAL_SERVER="$SCRIPT_DIR/server"

# ---------------------------------------------------------------- 13) local-only: REMOTEONLY hesaplanmaz
scenario localonly
mk beta "$LOCAL_CFG" 1
DRY_RUN=1
local_config_list_checked; : > "$TMP/cfg.remote"
cfg_decide 1 > "$OUT" 2>&1
assert "local-only: LOCALONLY sınıfı" test "$(cls_of beta)" = LOCALONLY
assert "local-only: çekme yok"        test -z "$PULLED_SITES"

# ---------------------------------------------------------------- 14) bayrak doğrulama (alt süreç)
DEPLOY="$HERE/../deploy.sh"
OUT="$ROOT/flags.txt"
for combo in "--pull-configs --local-only" "--pull-configs --rollback" "--pull-configs --overwrite-configs" "--pull-configs --keep-configs"; do
  # shellcheck disable=SC2086
  if NO_COLOR=1 bash "$DEPLOY" $combo > "$OUT" 2>&1; then t_fail "bayrak çakışması reddedilmeli: $combo"; else t_ok; fi
done
assert "--help provider tariflerini anlatır" bash -c "NO_COLOR=1 bash '$DEPLOY' --help | grep -q 'configs/providers/' && NO_COLOR=1 bash '$DEPLOY' --help | grep -q 'yerele çekildi: provider'"
assert "--help silinmiş siteleri anlatır" bash -c "NO_COLOR=1 bash '$DEPLOY' --help | grep -q 'deleted_sites.json' && NO_COLOR=1 bash '$DEPLOY' --help | grep -q 'local-removed'"
assert "--help yeni bayrakları anlatır" bash -c "NO_COLOR=1 bash '$DEPLOY' --help | grep -q -- '--sync-configs' && NO_COLOR=1 bash '$DEPLOY' --help | grep -q -- '--pull-configs' && NO_COLOR=1 bash '$DEPLOY' --help | grep -q REMOTEONLY"

printf '\n%d geçti, %d başarısız\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
