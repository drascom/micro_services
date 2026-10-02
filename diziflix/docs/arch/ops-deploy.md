# Deploy, geri alma, yedek budama, config karar tablosu, sunucu DB sıfırlama

> Bu bölüm CLAUDE.md'den taşındı (2026-10-02). Metin birebir taşınmıştır; kısa harita ve kurallar `diziflix/CLAUDE.md`'dedir.

## Deploy ve config kararı

Deploy: host `61`'e tar-over-ssh (`--exclude "._*"`); `.env`, `data/`, `venv/` korunur. Servis: `systemctl restart diziflix` (`.env` değişince).

Otomatik deploy (proje kökünden): `./deploy.sh` = sunucuda yedek + requirements/configs fark kontrolü + tar-over-ssh + `import app.main` + restart + `/admin`,`/api/*` doğrulama; önce `./deploy.sh --dry-run` (yazmaz). Scraper `configs/` site bazında akıllı karar (yaml+baseline birlikte, tabloyla): sunucu sürümü < yerel VE sunucuda uygulanmış heal kaydı (`data/_ops.json`, `scraper_state/<site>.json`) / yeni arşiv (`<site>.vN.yaml`) yoksa otomatik EZER ("v7 → v8"); sunucu sürümü ≥ yerel, heal izi var ya da sürüm/heal okunamadıysa KORUR (yalnız o dosyalar `--exclude`; `--overwrite-configs` ezer, onay ister; `--keep-configs` hiç ezmez); sunucu configs'i her durumda önce `deploy-backups/`e iner


## Geri alma, bayraklar, env

Geri alma: `./deploy.sh --rollback [YEDEK_YOLU]` (yolsuz: en son `/root/diziflix-server-bak-*.tgz`; `.env`/`data/`/`venv/` korunur). Diğer: `--keep-configs`, `--overwrite-configs`, `--no-restart`, `--no-prune`, `--keep N`, `--yes`, `--help`; env: `DZ_HOST`, `DZ_DEST`, `DZ_SERVICE`, `DZ_PORT`, `DZ_BACKUP_DIR`, `DZ_KEEP_BACKUPS`


## Yedek budama

Yedek budama (varsayılan AÇIK): başarılı deploy'un sonunda (restart + tüm uç doğrulaması geçtikten sonra) sunucuda `/root/diziflix-server-bak-YYYYMMDD-HHMMSS.tgz` yedeklerinden yalnız EN YENİ 3'ü (ad zaman damgasına göre; bu deploy'un yedeği hep kalır) ve yerelde `deploy-backups/remote-configs-YYYYMMDD-HHMMSS/` klasörlerinden en yeni 3'ü kalır, eskiler silinir. `--keep N` / `DZ_KEEP_BACKUPS` (varsayılan 3, en az 1), `--no-prune` kapatır; `--prune-backups` kullanımdan kalktı (etkisiz). Başarısız/doğrulanamayan deploy, import hatası, `--rollback`, `--no-restart`, `--dry-run`/`--local-only`'de silme YOK (dry-run yalnız "silinecekti" listesi); `diziflix-server-prerollback-*`, `diziflix-db-bak-*`, `data/diziflix.db.bak.*` ve ad kalıbına uymayan dosyalara asla dokunulmaz


## REMOTEONLY / `--pull-configs`

Sunucuda (admin "Site ekle" ile) eklenen yeni siteler `REMOTEONLY` sınıfıdır: deploy'da yerel `configs/`'e otomatik çekilir (`yerele çekildi: <site> (vN)`) ve o tar'dan dışlanır (`server/pi/` ise normal kod gibi gider, yerel kazanır). `./deploy.sh --pull-configs [--dry-run] [--sync-configs]`: deploy yok, sunucuya yazmaz; yalnız sunucu configs'ini indirir/yedekler ve REMOTEONLY siteleri çeker. `--sync-configs` (varsayılan KAPALI) KORU kararlı sitelerin sunucu dosyalarını da yerele kopyalar, AYNI sitelerdeki yalnız-sunucu dosyalarını (heal arşivi) ekler; yerel daha yeni siteye dokunmaz. Çekilenler git'te görünür: kontrol edip commit et. Test: `bash tests/deploy_configs_test.sh`


## Sunucu veritabanını sıfırlama

Test için sunucu veritabanını SIFIRLAMA: `./clear-remote.sh [--cache] [--no-backup] [--keep N] [--wipe-categories] [--yes]` (ya da `ssh root@192.168.0.61 'bash -s' < clear-remote.sh`): servisi durdurur, `data/diziflix.db*` -> `data/diziflix.db.bak.<ts>` yedekler, DOSYAYI SİLMEZ: `server/tools/reset_db_keep.py` (sunucuda deploy edilmiş olmalı) tek işlemde `KEEP_TABLES` (varsayılan `home_categories`: admin kategorileri + sistem iskelet satırları) dışındaki tüm tabloları boşaltır, VACUUM yapar; servisi başlatıp `/api/health` bekler. Kategoriler KORUNUR, `--wipe-categories` onları da siler; kategori üyeliği (`library_lists` category_*) silinir, scraper bir sonraki taramada yazar. Profiller/ilerleme/listem de gider. `.env`, `configs/`, `data/scraper_state/`, `ops_settings.json` DOKUNULMAZ (deploy.sh heal izine bakar). Yalnızca kullanıcı isterse çalıştırılır


## Yerel test

Yerel test (sunucuya bağlanmaz): `./deploy.sh --local-only -v` (gönderilecek dosya listesi); yedekler `/root/diziflix-server-bak-<ts>.tgz`, indirilen sunucu configs `./deploy-backups/` altına (commit etme)


## Scraper config referansı sunucudur (61)

- **Scraper config'inde referans SUNUCUDUR (61)**: sunucuda gerçek LLM bağlı (`SCRAPER_HEAL_*`, autoapply açık), `configs/<site>.yaml` + `.baseline.json` düzeltmelerini (selector/drift heal, sürüm artışı) o kendi yapar. Biz yalnızca uygulama kodunu (normalize, site_extractors, providers, ingest, API, istemci, testler) düzenleriz; var olan bir sitenin yaml/baseline'ını yerelde ELLE düzeltmeyiz. Yerel `server/app/scraper/configs/` sunucunun AYNASIDIR: sunucudan çekilir (deploy'un indirdiği `deploy-backups/remote-configs-<ts>/configs`), yerelden sunucuya itilmez; yerel sürüm ≤ sunucu kaldıkça `deploy.sh` zaten KORUR. Yalnızca YENİ site config'i yerelde yazılır ve deploy'la gider (sonra referans sunucu olur). Bir scraper bozulursa önce admin Olay defteri/heal'e bak (`POST /api/ops/sites/{site}/heal`), elle config düzeltmesi gerekirse kullanıcıya sor. Yerel fixture testleri sunucu config'iyle uyuşmazsa düzeltilen FIXTURE/test olur (canlı sayfadan yenile, `tools.homepage_probe`); config yerel eski sürüme döndürülmez
