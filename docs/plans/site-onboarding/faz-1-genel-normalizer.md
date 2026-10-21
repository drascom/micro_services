# Faz 1: Genel normalizer ve dinamik afiş host izni

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç.

## Amaç
Bir site için `@register` ile yazılmış Python normalize fonksiyonu yoksa, site yaml'ındaki `normalize:` bloğuyla
çalışan **genel normalizer** devreye girsin. Böylece panelden eklenen site kod yazılmadan ingest edilebilir.
Mevcut iki fonksiyon (`normalize_sinemalar`, `normalize_yabancidizi`) aynen kalır ve öncelik onlardadır.

## Mevcut durum
- `server/app/library/normalize.py` (138 satır, tamamını oku):
  - `_REGISTRY` sözlüğü, `register`, `normalize(source, raw)`. Kayıtsız site için `KeyError` fırlatır;
    `library/ingest.py` (yaklaşık satır 795) bunu yakalar ve koşu `error` olur.
  - Çıktı alanları: `source_key, type, title, original_title, year, overview, genres, rating, runtime, country,
    followers, cast, poster_url, backdrop_url, trailer_url, source_url`. İsteğe bağlı olarak `video_sources`
    (bölüm kaynakları). `normalize()` ayrıca `_detail_checked`, `tmdb_id`, `imdb_id`, `video_sources` alanlarını
    raw'dan taşır.
- İki fonksiyon arasındaki farklar: kimlik çıkarma regex'i, tür (sabit ya da URL grubundan), host denetimi,
  kanonik URL şablonu, türlerin virgülle bölünmesi, URL'deki sezon/bölümden `video_sources` üretimi.
- Afiş proxy izni: `config.py` (yaklaşık satır 81-88) `REMOTE_IMG_HOSTS`, `images.py` (yaklaşık satır 304-312).
  Env'de tanımlıysa varsayılan listeyi tamamen ezer.

## `normalize:` blok şeması (bunu uygula)

```yaml
normalize:
  host: yabancidizi.news            # str | list; detail_url host'u bunlardan biri olmalı (yoksa denetim yok)
  base_url: https://yabancidizi.news/   # isteğe bağlı; yoksa cfg.base_url (göreli URL'leri mutlaklamak için)
  key:
    from: [detail_url]              # sırayla denenen raw alanları (örn. [detail_url, poster_url])
    regex: '...'                    # str | list[str]; URL PATH'ine (ya da `match: url` ise tam URL'ye) uygulanır
    match: path                     # path | url  (varsayılan path)
    template: '{kind}/{slug}'       # isimli gruplar; yoksa ilk grup ya da tüm eşleşme
  type: movie                       # sabit: movie | series
  # veya:
  # type: {from_group: kind, map: {dizi: series, film: movie}, default: movie}
  source_url: 'https://yabancidizi.news/{kind}/{slug}'   # isteğe bağlı şablon; yoksa mutlak detail_url
  fields:                           # isteğe bağlı raw → kanonik eşleme (varsayılanların üstüne)
    overview: synopsis
  split: {genres: ','}              # liste alanlarındaki öğeleri ayırıcıyla böl, kırp, boşları at
  episode_source:                   # isteğe bağlı; season+episode grubu/alanı varsa video_sources üret
    enabled: true
    url: detail                     # detail: mutlak detail_url (sezon var ama bölüm yoksa "/bolum-{episode}" ekleme kuralı yok; gerekiyorsa url_template kullan)
    url_template: null              # örn. '{source_url}/sezon-{season}/bolum-{episode}'
    label: '{season}. Sezon {episode}. Bölüm'
```

### Davranış kuralları
- `title` boşsa → `None`. Anahtar çıkmazsa → `None`. Host eşleşmezse → `None`.
- **Varsayılan alan eşlemesi** (`fields` ile ezilebilir): `overview←synopsis`, `original_title`, `year`, `rating`,
  `runtime`, `country`, `followers`, `cast` (liste), `poster_url`, `backdrop_url`, `trailer_url`, `genres` (liste).
- URL alanları `base_url` ile mutlak yapılır. `http`/`https` olmayan şemalar `None` olur.
- `season`/`episode` önce raw alanlarından okunur, yoksa regex gruplarından alınır (int'e çevrilir).
- `video_sources` öğesinin biçimi yabancidizi'dekiyle aynıdır:
  `{key: "s{season}e{episode}", url, kind: "episode", resolver: "page", season, episode, label}`.
- `type == series` ve sezon/bölüm varsa `episode_source` uygulanır.

## Yapılacaklar

1. **`library/normalize.py`**
   - `generic_normalize(rules: dict, raw: dict, *, base_url: str) -> Optional[dict]` ekle.
   - `explain(rules, raw, *, base_url) -> dict` ekle: `{ok, result|None, reason}`. `reason` değerleri: `no_title`,
     `no_key`, `host_mismatch`, `bad_rules:<detay>`. Sandbox ve LLM tool'u bunu kullanacak.
   - `normalize(source, raw)` akışı: kayıtlı fonksiyon varsa onu kullan. Yoksa site config'ini yükle
     (`scraper.config.load_site`, dosyanın mtime'ı/sürümüyle önbelleklenmiş) ve `normalize:` bloğu varsa generic'i
     çalıştır. Hiçbiri yoksa bugünkü `KeyError`'ı daha açıklayıcı bir mesajla fırlat. Ortak son işlemler
     (`trailer_checked`, `tmdb_id`, ...) her iki yol için de aynen uygulanır.
   - `validate_rules(rules) -> list[str]`: zorunlu `key.regex`, `key.from`; regex'ler derlenebilir mi; şablondaki
     `{grup}` adları regex'te var mı; `type` geçerli mi.
   - `preview(rules, raws, *, base_url) -> dict`: `{total, ok, rejected: {reason: n}, duplicate_keys: [..],
     types: {movie: n, series: n}, samples: [ilk 10 sonuç]}`. Kabul kriteri hesapları bundan yapılır.
   - Config yükleme hatası normalize'ı patlatmasın: logla ve `KeyError` ile aynı yola düş.
2. **Dinamik afiş host izni (`images.py`, gerekiyorsa `config.py`)**
   - İzin listesi = env/varsayılan `REMOTE_IMG_HOSTS` **∪** tüm site config'lerinin `base_url` host'u **∪** yaml
     `image_hosts:` listesi. Birleşim yapılır, env listesi ezilmez.
   - Site listesi `scraper.config.list_sites()` ile alınır, kısa süreli (örn. 60 sn) önbelleklenir.
   - Host eşleşme kuralı mevcut fonksiyonla aynı kalır (alt alan adı davranışı neyse o).
3. `ingest.py`'de değişiklik gerekmemeli. `normalize()` aynı imzayla çalışıyor. Gerekirse yalnızca hata mesajı.

## Dokunulacak dosyalar (sahiplik)
- `library/normalize.py`, `images.py`, `config.py` (yalnızca img host'larıyla ilgili kısım; Faz 0 aynı dosyada
  `SiteConfig`'e dokunur, dolayısıyla `SiteConfig` sınıfına DOKUNMA, yaml'ı `cfg.data.get("normalize")` ile oku).
- DOKUNMA: `scraper/*` (Faz 0'ın), configs yaml'ları.

## Testler (`server/tests/test_normalize_generic.py`, yeni)
- **Eşdeğerlik testi (asıl kanıt):** test dosyası içinde iki kural seti yaz: sinemalar ve yabancidizi.
  - sinemalar: `key.from: [detail_url, poster_url]`, regex `['/film/(\d+)', '/movie/(\d+)/']`, `type: movie`.
  - yabancidizi: README'deki örneğe benzer; `host`, `kind/slug/season/episode` grupları, `type.map`, `source_url`
    şablonu, `split.genres`, `episode_source`.
  - Her iki site için gerçekçi raw örnekleri üret (en az 15'er): mevcut fixture'ları (`tests/fixtures/*.html` +
    `tools.homepage_probe.probe` ya da parse) kullanarak ya da elle; uç durumları da kat: göreli URL, boş başlık,
    yanlış host, sezon var/bölüm yok, poster `data:` URL'si.
  - `generic_normalize(rules, raw) == normalize_<site>(raw)` her örnek için. Farkı küçük ve bilinçli bir kural
    değişikliği gerektiriyorsa şemaya en az genişletmeyi yap; yine de eşleşmeyen alan varsa rapora yaz.
    (Bilinen ince nokta: yabancidizi'de sezon var ama bölüm yoksa URL'ye `/bolum-{episode}` eklenir;
    `url_template` ile ifade et.)
- Fallback: kayıtlı fonksiyonu olmayan sahte site, geçici `CONFIG_DIR`'de `normalize:` bloklu yaml ile çalışıyor.
  Blok yoksa `KeyError` fırlıyor.
- `validate_rules` ve `preview` (tekrar eden anahtar, ret nedenleri sayımı).
- `images`: env listesi + config host'larının birleşimi; env doluyken config host'u da izinli.

## Kabul kriterleri
- Eşdeğerlik testi iki site için yeşil, tüm sunucu testleri yeşil.
- Mevcut `@register` fonksiyonları değişmemiş ve hâlâ öncelikli.
- Raporda: şemaya eklenen ya da şemadan çıkarılan her alan ve eşleşmeyen bir durum kaldıysa o.
