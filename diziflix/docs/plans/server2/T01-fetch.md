# T01: Paralel sayfa getirme (`core/fetch`)

**Bağımlı:** T00. **Açar:** T03, T08, T11. **Oku:** README §5.4; `inventory/01 §2`; `server/app/scraper/{fetch,transport,netguard,obscura_worker}.py`,
`server/app/routers/onboard_sandbox.py:804-1050` (`_fetch_store`, `_page_signals`, ince gövdede yükseltme).

## Amaç
Eski sistemde tarama getirmesi küresel seriydi (tek kilit + her istekte 2 sn + alt süreç). `server2`'de tek giriş noktası, host başına
hız sınırlı ama **paralel**, SSRF denetimi içeride olan bir getirici.

## Yapılacaklar
1. `core/fetch/netguard.py`: `netguard.check_url` kopyası (yalnız http/https, 80/443, özel/CGNAT/loopback yasak). Yönlendirme sonrası
   her adres yeniden denetlenir.
2. `core/fetch/robots.py`: host başına robots.txt (okunamazsa izinli), 1 saat bellek önbelleği.
3. `core/fetch/limiter.py`: global eşzamanlılık (8), host başına eşzamanlılık (`SiteCtx.concurrency`) ve istekler arası en az `delay_ms`;
   `429`/challenge sonrası host için artan bekleme (1x, 2x, 4x, en çok 60 sn), başarıyla sıfırlanır. İş parçacığı güvenli.
4. `core/fetch/http.py`: süreç içi getirme, `curl_cffi` Chrome TLS taklidi (eski `fetch.impersonated_get` temel alınır): süre sınırı
   (bağlantı 5 sn, toplam 20 sn), boyut sınırı (3 MB), en çok 3 yönlendirme, GET + urlencoded POST (yalnız `base_url` host'una, gövde <= 4096 bayt).
   Cloudflare/challenge gövdesi tanıma -> `FetchError("challenge")`.
5. `core/fetch/browser.py`: Obscura alt süreci (eski `obscura_worker.py` + `transport` çağrı biçimi); **havuz 2**, istek başına 45 sn,
   `wait_for` desteği; `OBSCURA_BIN` yoksa `FetchError("browser_unavailable")`. Crawlee yolu ALINMAZ.
6. `core/fetch/__init__.py`: `get(...)`, `Page`, `SiteCtx`, `FetchError` (README §5.4). `mode="auto"`: http; sonuç inceyse
   (`THIN_CHARS` mantığı, `sb:818` civarı) ya da `challenge` ise bir kez browser. Dönen `Page.engine` gerçekte kullanılanı söyler.
7. `get_many(urls, *, site, mode="auto", limit=None) -> list[Page | FetchError]`: sıralı sonuç, limiter üzerinden paralel; bir adresin
   hatası diğerlerini durdurmaz.
8. `core/fetch/pages.py` (örnek sayfa deposu): `save(site_id, page_type, page) -> path`, `load(site_id, url) -> Page | None`,
   `list(site_id, page_type)`; gövde `DATA_DIR/pages/<site>/<sha1>.html`, kayıt `sample_pages` tablosu (migrasyon bu görevde).
   `get(..., cache_s=N)`: N saniyeden taze kayıt varsa ağa çıkmaz (`from_cache=True`). Site başına en çok 200 sayfa, eskisi silinir.
9. `page_signals(html, requested, final) -> {canonical_url, redirect_hint}`: `sb:931` kopyası (saf).
10. Oynatma taşıması için ince sarmalayıcılar aynen kopyalanır ve T03'e açılır: `fetch_url`, `post_url`, `fetch_limited`, `reachable`,
    `impersonated_session` (hepsi içeride netguard çağıracak şekilde).

## Kapsam dışı
Sayfa ayrıştırma, tarama planı, resolver mantığı.

## Kabul (testler yerel sahte HTTP sunucusuyla; sandbox'ta 127.0.0.1 testte izinli bir "test host" bayrağıyla açılır, üretimde yasak)
- Özel adres, `file://`, port 8080, yönlendirmeyle özel adrese giden istek -> `FetchError("blocked_ssrf")`.
- Aynı host'a 6 istek, `concurrency=2, delay_ms=100`: aynı anda en çok 2 istek, ardışık başlangıçlar arası >= 100 ms (ölçümle).
- İki farklı host'a istekler birbirini beklemez (toplam süre seri süreden belirgin kısa).
- `auto`: ince gövde -> browser'a yükseltme çağrılır (browser motoru testte sahte); challenge gövdesi -> `challenge`.
- `get_many`: 5 adresten biri 500 dönerse 4 `Page` + 1 `FetchError`, sıra korunur.
- `429` iki kez -> üçüncü istek öncesi bekleme arttı; başarı sonrası sıfırlandı.
- Sayfa deposu: kaydet/yükle, `cache_s` ile ağa çıkmama, 200 sınırı.
- Boyut ve süre sınırı aşımı `too_large` / `timeout`.
