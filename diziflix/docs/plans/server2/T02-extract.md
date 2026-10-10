# T02: Çıkarma + LLM'siz keşif kütüphanesi (`core/extract`)

**Bağımlı:** T00. **Açar:** T07, T08, T09, T11. **Oku:** README §5.5-5.6; `inventory/01 §3, §5`; `inventory/03 §1` (anahat ve tanılama
satırları); `server/app/scraper/{parse,discover,drift}.py`, `server/app/routers/onboard_sandbox.py:433-773, 1052-1594`.

## Amaç
HTML'den kayıt çıkaran ve bir sayfanın yapısını **LLM'siz** anlayan saf fonksiyonlar. Ağ yok, DB yok, durum yok: girdi HTML + kural, çıktı dict.
Aşamaların "keşif" ve "ölçüm" adımları bunun üstüne kurulur.

## Yapılacaklar
1. `core/extract/parse.py`: `parse_list(html, row_selector, fields)`, `parse_detail(html, fields)`, `apply_field(node, spec)`,
   `turkish_date` (kopya, `parse.py`). Alan kuralı sözlüğü README §5.5 ile aynı.
2. `core/extract/outline.py`: `onboard_sandbox`'tan saf anahat kümesi: `_outline_of` (`:1478`), `_blocks` (`:1249`), `_sections` (`:1302`),
   `_nav_links` (`:1115`), `_episode_links` (`:1431`), `_link_kind` (`:1187`), `_scoped_selector` (`:1154`), `_anchor_selector` (`:1401`).
   Herkese açık adlarla (`outline(html, base, requested, final="")`, `blocks`, `sections`, `nav_links`, ...). FastAPI/store/ContextVar bağımlılığı
   bırakılmaz; tanılama dönüş değerine girer.
3. `core/extract/cards.py`: `discover.extract_fields(cards, base, kind)` (`discover.py:347`), `role_of` (`:426`) kopyası: tekrar eden kart
   bloğundan `url/title/poster/year` seçicileri; her seçici kartların kendisinde doğrulanmış döner (`evidence` ile).
4. `core/extract/detail.py`: `discover.detail_fields(html, page_url)` (`:1281`): etiketli metin -> JSON-LD -> og/meta sırasıyla alan önerisi;
   `_json_ld` yardımcıyla.
5. `core/extract/episodes.py`: `discover.episode_regex(path)` (`:499`), `build_series_page(...)` (`:1142`) ve genel dizi envanter motoru
   `series_generic.series_inventory(html, page_url, spec)` + `validate_spec` (`series_generic.py:386, 195`). Siteye özel modül yolu
   (`site_extractors.series_inventory` dallanması) ALINMAZ.
6. `core/extract/urlpatterns.py` (**yeni, eski kodda yok**):
   - `cluster(urls, base) -> [Cluster(pattern_regex, example_urls, count, shape)]`: yol parçalarını (sabit / sayı / slug) soyutlayıp
     aynı biçimdeki adresleri kümeler; `pattern_regex` örneklerin hepsini eşler, diğer kümelerin örneklerini eşlemez.
   - `classify(clusters, outline) -> {listing: [...], title: [...], episode: [...]}`: ipuçları = kümenin anahatta nerede geçtiği
     (nav / kart bloğu / bölüm listesi), sayı parçaları, tekrar sayısı. Emin değilse boş bırakır (tahmin uydurmaz) ve `evidence` döner.
   - `pagination(html, page_url) -> {kind, selector|template, evidence} | None`: `rel=next`, "sonraki/next/»" bağlantısı, `?page=N` /
     `/page/N` / `/sayfa/N` kalıbı; bulunan kural 2. sayfanın adresini üretebilmeli.
7. `core/extract/fingerprint.py` (**yeni**): `make(html, rule) -> Fingerprint` = `{rows: n, field_fill: {alan: oran}, skeleton: <kısa özet>}`;
   `skeleton` = satır düğümünün etiket/sınıf yolunun kararlı özeti (içerikten bağımsız). `compare(reference, current, *, tolerance) ->
   {ok, reasons[]}`: satır sayısı tabanın altına düştü, zorunlu alan doluluğu düştü, iskelet değişti. Detay türü sayfalar için `rows=1`.
8. `core/extract/player.py`: `discover.player_candidates(html, page_url, base)` (`:1416`): iframe, sekme/data özniteliği, bilinen host
   bağlantısı, script içi adresler + önerilen resolver öğesi. (Provider havuzunu parametre alır; T03 verir.)
9. `core/extract/search.py`: `discover._search_form` (`:459`) + arama kuralı doğrulayıcı `search_generic.validate_spec` (`:211`).
   Aramayı çalıştıran ağ kodu T09'da.
10. `core/extract/htmlclip.py`: `heal._clean_html(html, limit, anchor_tokens)` (`heal.py:134`): LLM'e verilecek kırpılmış HTML
    (script/style/svg atılır, uzun öznitelik kısaltılır, çapa belirteçlerinin çevresi korunur). T06/T08 kullanır.
11. `core/extract/checks.py`: saf ölçüm yardımcıları: `criterion(value, bound, kind)` (`sb:2022`), alan doluluğu (`heal.field_fill`),
    `rows_diagnostics` / `detail_diagnostics` (`sb:575, 640`) dönüş değeri olarak.
12. `tests/fixtures/fakesite/`: uydurma bir sitenin 8-10 küçük HTML dosyası (ana sayfa: 3 bölüm + menü; 2 tür listesi + 2. sayfa;
    film detayı; dizi detayı + bölüm listesi; bölüm sayfası iframe'li; bölüm sayfası script içi adresli; arama sonucu; telif yer tutuculu
    bölüm). İkinci bir küçük varyant (`fakesite_b/`): farklı işaretleme (tablo tabanlı liste, sezon sayfaları ayrı). Sonraki görevler
    aynı fixture'ı kullanır. Gerçek site HTML'i kopyalanmaz.

## Kapsam dışı
Ağ, DB, LLM, aşama mantığı. `discover.discover()` tek parça akışı (yerine T08/T09 aşamaları gelir).

## Kabul
- `parse_list`/`parse_detail`: fixture'da beklenen kayıtlar; `fallback`, `regex`, `cast` kuralları çalışır.
- `outline`: ana sayfa fixture'ında 3 bölüm ve menü bağlantıları bulunur; iki varyantta da.
- `cluster` + `classify`: fixture adreslerinden listing/title/episode kümeleri ayrılır; üretilen regex kendi örneklerini eşler,
  diğer kümeyi eşlemez. Belirsiz girdide (3 adres) sınıflandırma boş + evidence döner.
- `pagination`: `next_link` ve `page_param` türleri iki fixture'da doğru bulunur; sayfalama yoksa `None`.
- `fingerprint`: aynı sayfanın içerik-değişmiş kopyası `ok`; satır sınıfı değişmiş kopyası `ok=False` + neden; alan doluluğu düşmüş kopya `ok=False`.
- `player_candidates`: iframe'li ve script içi adresli fixture'da aday bulunur; yer tutucu sayfada aday yok.
- `core/extract` içinde ağ/DB/`app.control` importu yok (`check_imports` + grep `httpx|curl_cffi|sqlite3`).
