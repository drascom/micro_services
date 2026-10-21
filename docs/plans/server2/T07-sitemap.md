# T07: Site haritası modeli (`control/sitemap`)

**Bağımlı:** T02 (T03 hazırsa resolver doğrulayıcısı ona bağlanır; değilse enjekte edilebilir). **Açar:** T08, T09, T10, T11, T14.
**Oku:** README §5.5, §5.6; `inventory/01 §1, §8` (eski yaml ile eşleme ve eksikler); `server/app/routers/onboard_sandbox.py:1596-1762`
(statik denetimler), `server/app/scraper/config.py:85, 389-560` (regex lint, sürümleme).

## Amaç
Sitenin tasarımını **sayfa türüne göre** tutan tek belge: şema, doğrulama, sürümleme, yama ve motor adaptörleri. Eski yaml'da karşılığı
olmayan dört şey birinci sınıftır: `url_pattern`, `samples`, `fingerprint`, `skipped`.

## Yapılacaklar
1. Migrasyon `030_sites.py`: `sites`, `site_versions`.
2. `control/sitemap/schema.py`: README §5.5 şeması. `validate(data) -> list[Problem(path, message, level)]`:
   - üst düzey ve sayfa türü başına izinli anahtarlar (bilinmeyen anahtar = hata, sessizce yutulmaz);
   - seçici sözdizimi (selectolax ile derlenir), alan kuralı (`sb:1608 _check_spec`), regex derleme + `regex_lint` (`config.py:85`: çift ters bölü vb.);
   - `url_pattern` kendi `meta.samples` adreslerini eşlemeli; `title.key.regex` örneklerde boş olmayan, birbirinden farklı anahtar üretmeli;
   - `player.resolvers` -> `core.play.resolvers.validate`; `search` -> `core.extract.search` doğrulayıcısı; `gate` -> `blocked.validate` / `validate_gate`;
   - `base_url` netguard'dan geçmeli; `site_id` `[a-z0-9_]{2,32}`.
3. `control/sitemap/store.py`:
   - `create_site(base_url, display_name=None) -> site_id` (id host'tan türetilir, çakışmada sonek), `get(site_id)`, `list_sites()`, `set_status`.
   - `draft(site_id) -> dict`, `save_draft(site_id, data)` (doğrulama hatalıysa reddeder).
   - `apply_patch(site_id, page_type, patch, *, meta=None) -> dict`: yalnız `pages.<page_type>` (ve A7 için `gate`) ile `meta.<page_type>`
     değişir; yama başka anahtara dokunuyorsa `ScopeError` (eski `heal_agent.scope_violation` fikri).
   - `commit(site_id, *, created_by, note) -> version`: taslağı `site_versions`'a yeni sürüm olarak yazar, `active_version` günceller.
     Taslak etkin sürümle aynıysa yeni sürüm açmaz.
   - `active(site_id) -> dict | None`, `versions(site_id)`, `diff(site_id, a, b) -> [{path, before, after}]`, `rollback(site_id, version)`
     (yeni sürüm olarak yazar; geçmiş silinmez), `export_yaml(site_id, version=None) -> str`, `import_yaml(text) -> site_id` (taslak olarak).
   - `delete_site(site_id, *, purge)` -> katalog temizliği için `core.catalog.write.delete_site`.
4. `control/sitemap/adapters.py`: haritadan motor girdileri:
   `site_ctx(map) -> core.fetch.SiteCtx`; `home_rules(map) -> [(section, row_selector, fields)]`; `listing_rule(map)`; `title_rule(map)`;
   `episodes_spec(map)` (`core.extract.episodes` genel motorunun beklediği `series_page` biçimi); `play_ctx(map) -> core.play.PlayCtx`;
   `search_spec(map)`; `gate_rules(map)`. Adaptörler saf fonksiyondur; eksik sayfa türü için `None` döner.
5. `control/sitemap/classify.py`: `page_type_of(map, url) -> str | None` (`url_pattern`'lere göre; birden çok eşleşmede en özgül olan),
   `title_key(map, url)`, `title_type(map, url, fields)`.
6. `control/sitemap/meta.py`: `set_samples`, `set_fingerprint`, `skip(page_type, field)`, `expect(page_type) -> dict` (varsayılan kabul
   eşikleri tek tabloda: `inventory/03 §1` "Kabul kriterleri" tablosundan; `skipped` alanlar eşikten muaf).

## Kapsam dışı
Aşamaların çalışması (T08/T09), admin uçları (T10), tarama (T11).

## Kabul
- Geçerli örnek harita (fixture `fakesite` için elle yazılmış) doğrulamadan geçer; her kural ihlali için bir olumsuz test: bilinmeyen
  anahtar, bozuk seçici, derlenmeyen regex, örneğini eşlemeyen `url_pattern`, çakışan `key`, özel adresli `base_url`.
- `apply_patch("title", ...)` başka sayfa türünü değiştirmeye çalışınca `ScopeError`.
- `commit` sürüm artırır; değişiklik yokken artırmaz; `rollback` yeni sürüm üretir ve içerik eski sürümle aynıdır; `diff` değişen yolu verir.
- `export_yaml` -> `import_yaml` gidiş dönüşü aynı haritayı verir.
- Adaptörler: fixture haritasından üretilen kurallarla `core.extract.parse_list` fixture HTML'inde beklenen kayıtları çıkarır.
- `page_type_of`: fixture adresleri doğru türe düşer; hiçbirine uymayan adres `None`.
- Harita dosya sisteminde değil DB'de; `server2` içinde `configs/<site>.yaml` yazan kod yok.
