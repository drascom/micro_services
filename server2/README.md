# diziflix server2

diziflix istemcilerine (`../API.md`) katalog ve oynatma sunan, siteleri adresle ekleyip kendi haritasını çıkaran yeni sunucu.
Port 8091, servis `diziflix2`, veritabanı `data/diziflix2.db`. Eski `../server/` (8090) ondan bağımsızdır.

## Kurulum

```bash
cd server2
./install.sh          # venv (uv varsa uv) + requirements + .env (.env.example'dan) ; Linux'ta systemd servisi diziflix2
```
Gereken: Python 3.11+, `node` (yalnız `check.sh` için), LLM için `pi` CLI (oturumu açık), isteğe bağlı Obscura.

## Çalıştırma

```bash
venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8091   # geliştirme (systemd'siz)
systemctl status diziflix2 ; journalctl -u diziflix2 -f     # sunucuda
./check.sh                                                  # tüm denetimler + testler
```
Yönetim paneli: `http://<sunucu>:8091/admin` (Siteler, Katalog, Sorunlar, Ayarlar). Sağlık: `/api/health`.
Site ekleme denemesi terminalden: `venv/bin/python -m tools.smoke_site https://ornek.tld` (çalışan sunucu, gerçek ağ ve LLM).

## Deploy

```bash
./deploy.sh --dry-run      # check.sh + fark özeti; sunucuda hiçbir şey değişmez
./deploy.sh --print-plan   # hiçbir komut çalıştırmadan tam planı yaz
./deploy.sh                # yedek -> rsync --delete -> import -> drain -> restart -> doğrulama
./deploy.sh --rollback     # son yedeğe dön
```
Hedef `root@192.168.0.61:/root/micro_services/diziflix/server2`; `.env`, `data/`, `venv/` sunucuda korunur. Ayrıntı: `./deploy.sh --help`.

## .env anahtarları (yalnız bunlar)

| Anahtar | Varsayılan | Ne |
|---|---|---|
| `HOST` | `0.0.0.0` | dinlenecek adres |
| `PORT` | `8091` | port |
| `DATA_DIR` | `data` | tüm çalışma verisi (göreli yol `server2/`'ye göre) |
| `TMDB_ACCESS_KEY` | boş | TMDB v4 bearer ya da v3 anahtarı; loglanmaz, panelde yalnız "tanımlı/tanımsız" |
| `PI_BIN` | `pi` | LLM çağırıcısının kullandığı pi CLI |
| `LLM_MODEL` | boş (pi varsayılanı) | `sağlayıcı/model` |
| `STREAM_PROXY_SECRET` | boş (süreç başına üretilir) | akış vekili jetonlarının HMAC sırrı |
| `OBSCURA_BIN` | boş | browser kipi için Obscura (boşsa browser kipi yok) |

Diğer her ayar (`backfill_days`, `refresh_hour`, `llm_daily_calls_per_site`, `repair_autoapply`, `problem_deadline_s`,
`confirm_seconds`, `tmdb_auto`, `tmdb_types`, `image_hosts_extra`, `stream_proxy_hosts`, `stream_proxy_force`) DB'dedir ve
panelin Ayarlar ekranından değiştirilir.

Mimari: `docs/ARCHITECTURE.md`. Geliştirici kuralları: `CLAUDE.md`.
