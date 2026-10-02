# Site ekleme (onboarding) projesi: genel plan ve ortak kurallar

Amaç: admin panelinden, sunucudaki pi (LLM ajanı) kullanılarak, kod yazmadan yeni bir scraper sitesi eklemek.
Mimari ilke: **"beyin" pi'de (skill + extension tool'ları), "motor" kodda (deterministik).** pi yalnızca yaml
üretir; her saatlik tarama ve her oynatma isteği bu yaml'ı deterministik kodla çalıştırır, LLM'e gitmez.

## Fazlar ve bağımlılıklar

| Faz | Dosya | Bağımlılık | Paralel? |
|---|---|---|---|
| 0 | `faz-0-resolver-kutuphanesi.md` | — | 1, 2b, 4 ile paralel |
| 1 | `faz-1-genel-normalizer.md` | — | 0, 2b, 4 ile paralel |
| 2a | `faz-2a-sandbox-uclari.md` | 0 + 1 bitmiş | 2b, 4 ile paralel |
| 2b | `faz-2b-pi-spike.md` | — (sunucuda, salt deneme) | her şeyle paralel |
| 2c | `faz-2c-pi-skill-ve-onboard.md` | 2a + 2b bitmiş | 4 ile paralel |
| 3 | `faz-3-admin-panel.md` | 2c bitmiş | 4 ile paralel |
| 4 | `faz-4-deploy-geri-cekme.md` | — | her şeyle paralel |
| 5 | `faz-5-heal-gocu.md` | 2c bitmiş, ayrıca kullanıcı onayı | sonra |

Paralel çalışan fazlar aynı dosyaya dokunmamalı. Dosya sahipliği her fazın "Dokunulacak dosyalar" bölümünde yazılı.
Ortak dosya çakışmasını önlemek için **`CLAUDE.md`'yi hiçbir worker düzenlemez**. Worker raporunun sonuna 3-6 satırlık
"CLAUDE.md notu" ekler; orkestratör bunu birleştirir.

## Ortak kurallar (her worker için geçerli)

1. **Rol:** Sen bir worker'sın. İşi kendin yaparsın, başka ajana devretmezsin.
2. **Kod tabanı:** `/Users/drascom/Documents/work/micro_services/diziflix` (git kökü bir üst dizindir). Önce
   `diziflix/CLAUDE.md`'yi oku. Sunucu kodu `server/app/`, testler `server/tests/`.
3. **Scraper config'leri sunucunundur:** `server/app/scraper/configs/yabancidizi.yaml`, `sinemalar.yaml` ve
   bunların `.baseline.json` dosyaları ELLE DEĞİŞTİRİLMEZ. Yeni yaml alanları için gereken örnekler test dosyalarında
   veya test fixture'larında yazılır. Mevcut iki sitenin davranışı **birebir aynı kalmalı** (geriye uyumluluk zorunlu).
4. **Testler:** her yeni test dosyası ilk satırda `import _sandbox` içerir (`server/tests/_sandbox.py`). Gerçek LLM,
   gerçek ağ ve gerçek `data/` kullanılmaz; `fetch.page`, `heal._provider_generate` ve subprocess çağrıları mock'lanır.
   Çalıştırma: `cd server && venv/bin/python -m unittest discover -s tests -v`. Tamamı yeşil olmalı. Mevcut testleri
   kırma; kırılan bir test varsa önce sebebini anla, testi "geçsin diye" gevşetme.
5. **Görsel test yok:** tarayıcı açma, ekran görüntüsü alma, GUI doğrulama YAPMA. Yalnızca test/derleme doğrulaması.
   İstemci JS değişirse `node --check <dosya>` ile sözdizimi kontrolü yap.
6. **Deploy yok:** `deploy.sh` çalıştırma, sunucuya dosya kopyalama, servis yeniden başlatma YOK. İstisna: Faz 2b
   (sunucuda izole klasörde pi denemesi) ve Faz 4'ün `--dry-run`/`--local-only` denemeleri.
7. **Sözleşme:** istemciye (Tizen) giden API alanlarına dokunma. Yalnızca admin ve yeni uçlar eklenir.
8. **Stil:** çevredeki kodun dilini, adlandırmasını, yorum yoğunluğunu taklit et. Gereksiz soyutlama yok.
9. **Gizli bilgi:** `.env` değerlerini, token'ları ve anahtarları asla loglama veya yazdırma.
10. **Rapor:** KISA ve ÖZ olsun. Yazılacaklar: değişen dosyalar (tek satır açıklamayla), test sonucu (geçen/başarısız
    sayısı), bilinen eksikler veya riskler, "CLAUDE.md notu". Uzun anlatım yok.

## Ortak veri sözlüğü (fazlar arası sözleşme)

Site yaml'ına eklenen yeni üst düzey anahtarlar (hepsi isteğe bağlı; yoksa bugünkü davranış):

```yaml
resolvers:            # Faz 0: aday bulma ve çözme zinciri, sırası öncelik
  - type: iframe
    selector: "iframe#player"
    attr: src
providers: [vidmolly, okru]   # Faz 0: izin verilen video host modülleri (yoksa hepsi)
normalize:            # Faz 1: genel normalizer kuralları
  host: example.com
  key: {from: [detail_url], regex: '...', template: '{slug}'}
  type: movie
image_hosts: [img.example.com]   # Faz 1: afiş proxy izin listesine eklenir
```

Taslak (draft) kimliği: `od_<12 hex>`. Site kimliği (`site_id`) deseni: `^[a-z][a-z0-9_]{1,31}$`.
Onboarding verisi: `DATA_DIR/onboard/` (`pages/`, `drafts/`, `sessions/`).
