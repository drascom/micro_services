# T15: Ayarlar ekranı, deploy betiği, belgeler

**Bağımlı:** T05, T10 (içerik olarak T06 ve T11 hazırsa onların ayarları da görünür). **Açar:** yok. **Oku:** README §6;
`inventory/03 §9` (eski `deploy.sh` dersleri: restart öncesi iş kontrolü yok, silme yok, sabit doğrulama listesi);
`server/install.sh`, `deploy.sh` (yalnız okumak için; DEĞİŞTİRİLMEZ), `docs/arch/ops-deploy.md`.

## Amaç
Sistemi işletilebilir kılmak: tek bir Ayarlar ekranı, `server2`'ye özel küçük ve güvenli bir deploy betiği, kısa belgeler.
Bu görev deploy YAPMAZ; yalnız betiği yazar ve `--dry-run` ile sınar.

## Yapılacaklar
1. **Ayarlar ekranı** (`static/js/settings.js`, `routes_settings.py`):
   - LLM: hesap sağlığı (`health.check`, `[yeniden kontrol et]`), model adı, son 7 günün çağrı sayısı site başına (`budget.usage`),
     günlük arka plan sınırı.
   - Onarım: `repair_autoapply` anahtarı (açık: doğrulanan düzeltme kendiliğinden uygulanır + bilgi notu).
   - Tarama varsayılanları: `backfill_days` (3), `refresh_hour`.
   - Sorunlar: istemciye söz süresi (`problem_deadline_s`), kullanıcı onayı süresi (`confirm_seconds`, 10).
   - TMDB: anahtar var/yok (değeri ASLA gösterilmez), `tmdb_auto`, `tmdb_types`.
   - Sistem: iş kuyruğu özeti (sıradaki/çalışan/başarısız, `[başarısızları göster]`), drain durumu.
   Uçlar: `GET/PUT /api/control/settings` (yalnız README §6'daki anahtarlar; bilinmeyen anahtar 400; tip/aralık doğrulaması),
   `GET /api/control/llm/health`, `POST /api/control/llm/health`.
2. **systemd**: `install.sh`'a Linux'ta `diziflix2` servisi (uvicorn `app.main:app --host $HOST --port $PORT`, `--reload` YOK,
   `Restart=on-failure`, `KillSignal=SIGTERM`, `TimeoutStopSec=30`: kapanışta drain'e süre). Servis adı eski `diziflix` ile çakışmaz.
3. **`server2/deploy.sh`** (yeni, küçük; eski `deploy.sh`'a dokunulmaz). Hedef: `root@192.168.0.61:/root/micro_services/diziflix/server2`.
   Adımlar: [0] ön kontrol (yerelde `./check.sh` yeşil değilse dur) -> [1] sunucuda yedek (`server2` kodu, `data/` hariç; son 5 yedek tutulur)
   -> [2] fark özeti -> [3] kopyala (`rsync --delete`, dışlananlar `.env`, `data/`, `venv/`, `__pycache__`: **silinen dosyalar sunucuda da silinir**)
   -> [4] sunucuda `import app.main` -> [5] **drain**: `POST /api/control/drain {"on": true}`, `GET /api/control/jobs/active` boşalana kadar
   en çok 120 sn bekle; LLM işi sürüyorsa ve `--force-restart` verilmediyse DUR ve drain'i kapat -> [6] `systemctl restart diziflix2` +
   `is-active` + journal'da hata taraması -> [7] uç doğrulama: `/api/health`, `/admin`, `/api/control/summary`, `/api/boot` (ya da sözleşmedeki
   eşdeğeri) -> drain kapalı mı kontrol.
   Bayraklar: `--dry-run` (yalnız [0] ve [2], sunucuda hiçbir şey değiştirmez), `--force-restart`, `--rollback` (son yedeğe dön + restart + doğrulama).
   **Site haritaları ve provider tarifleri `data/` ve DB'dedir: deploy onlara hiç dokunmaz** (eski config karar tablosu, tombstone, ayna yok).
   `.env` içeriği yazdırılmaz.
4. **Belgeler**:
   - `server2/CLAUDE.md` (<= 80 satır): amaç, kararlar (README K1-K12 kısa), komutlar, katman kuralı, "yeni iş türü / yeni aşama / yeni
     migrasyon / yeni admin ekranı nasıl eklenir" (her biri 2-3 satır), tuzaklar.
   - `server2/docs/ARCHITECTURE.md` (<= 200 satır): modül haritası, veri akışı, durum makineleri (iş, aşama, sorun), tablo listesi.
   - `server2/README.md`: kurulum, çalıştırma, `.env` anahtarları.
   Üst düzey `CLAUDE.md` DEĞİŞTİRİLMEZ (orkestratör kesimde günceller).
5. `tools/smoke_site.py`: elle deneme için komut satırı aracı: `python -m tools.smoke_site <adres>` -> site oluşturur, aşamaları sırayla
   bekler, her aşamanın durumunu ve özetini terminale basar, `needs_human` olursa kanıtı gösterip çıkar. Gerçek ağ ve gerçek LLM kullanır;
   testlerde ÇALIŞTIRILMAZ, yalnız kullanıcı/orkestratör elle koşar.

## Kapsam dışı
Gerçek deploy, eski `server/`'ın kaldırılması (kesim: ayrı görev, kullanıcı kararıyla), istemci adres değişikliği, Kütüphane/Kategoriler ekranları.

## Kabul
- Ayar uçları: geçerli güncelleme saklanır ve restart'tan (yeni bağlantı) sonra okunur; bilinmeyen anahtar ve aralık dışı değer 400;
  yanıtlarda ve loglarda TMDB anahtarı yok.
- `deploy.sh --dry-run`: sunucuya yazan hiçbir komut çalıştırmaz (betik `ssh`/`rsync` çağrılarını `DRY=1` iken yalnız yazdırır; test:
  sahte `ssh`/`rsync` ile `PATH` gölgeleme, yazma komutu sayacı 0).
- Betikte drain adımı restart'tan önce; LLM işi varken `--force-restart` olmadan çıkış kodu sıfırdan farklı ve drain geri kapatılıyor
  (sahte uçlarla betik testi ya da raporda adım adım gösterim).
- `rsync` dışlama listesi `.env`, `data/`, `venv/` içerir (grep testi).
- `shellcheck` (varsa) temiz; `bash -n` temiz.
- `node --check` temiz; `check.sh` yeşil.
- Belgeler satır sınırlarında; `CLAUDE.md` komutları gerçekten var olan dosya/komutlara işaret ediyor.
