# T24 - Kategoriler (sabit + dinamik), admin yönetimi
**Eski sistem (server/):** `home_categories` iki tür satır tutar. *Sabit* (`kind=system`): ana ekran iskeleti `continue,
trending_series, series, trending_movies, noteworthy_movies, movies, mylist` (başlıklar `homelayout.TITLES`, tembel tohum
`ensure_system`); silinemez, gizlenemez, adı değişmez (409 `locked`), yalnız sırası değişir. *Dinamik* (`kind=category`):
admin'in eklediği satırlar (`cat_<slug>`); üyelik tabloda değil `library_lists` `category_<slug>_<site>` listelerinde
(scraper'ın `category` rolü yazar), satır = sitelerin listeleri round-robin, yalnız oynatılabilir; kapalıysa ya da
oynatılabilir sayısı `min_items` (vars. 6, 1-100) altındaysa gizli; silme yalnız satırı siler, üyelik kalır. Sıra admin
sırası (iskelet + kategoriler karışık; yeni kategori son kategorinin / `trending_series`'in arkasına). Profil kuralı yok.

**server2'de vardı (T04a/T04b/T12 kopyası):** `core/catalog/categories.py`, `homelayout.layout/_category`, `/api/row/cat_*`,
yönlendirmenin `target_kind=category` hedefi + "yeni kategori öner" kabulü. **Yoktu:** yönetim API'si, admin ekranı,
sabit satır tohumu (yalnız tembel), dinamik kaynak tanımı (yalnız yönlendirme listeleri), satır sınırı.

## Yapılan
- Migrasyon `020_categories.py`: `home_categories.source` (JSON) + `max_items` (0 = sınırsız); sabit satırlar burada tohumlanır
  (`categories.seed_system`, idempotent; `ensure_system` onu kullanır).
- `core/catalog/categories.py`: `clean_source` (kaynak: `lists` | `genre` | `type` | `site`(section/listing/genre origin) |
  `list`(library_lists id), hepsine isteğe bağlı `type` süzgeci), `source_items`, `category_items` (önce yönlendirme
  listeleri, sonra kaynak; tekrarsız), `SYSTEM_TITLES`, `get_any`; sabit satır artık gizlenir/yeniden adlanır/sınır alır.
- `api/homelayout.py`: gizli sabit satır boot'ta yok; `title()` admin başlığı; `limit_pool` boot'ta ve `/api/row/<id>`'de.
  Yanıt alanları değişmedi (API.md'ye ek yok).
- Admin: `admin/routes_categories.py` (`/api/control/categories` GET/POST, `/{slug}` PUT/DELETE, `-order` PUT, `/preview`
  POST, `/{slug}/preview` GET) + `static/js/categories.js` (sırala ▲▼/sürükle, görünür, ad, en az / sınır, yeni dinamik
  kategori formu + önizleme ilk 10 afiş, yalnız dinamik silinir) + kayıt satırları (main, assets, index.html, style.css).
- Yönlendirme bağı: Yönlendirme sekmesi hedef listesi aynı tablodan (her dinamik kategori hedef olabilir); öneri kabulüyle
  açılan kategori `source=lists` dinamik olarak Kategoriler'de görünür (T12 testleri yeşil).
- Test: `tests/test_t24_categories.py` (5 test: tohum + gizle/ad, genre kategorisi sıra + boş gizli, sıra değişimi,
  `/api/row` içerik + sınır + önizleme, yönlendirme kuralı -> kategori dolar). check.sh YEŞİL, 366 test ~14 sn.

## Sapmalar
- Sabit satırlar artık gizlenebilir ve yeniden adlandırılabilir (eskide kilitliydi); silinmez, kaynağı/en az sayısı yok.
- Profil (çocuk vb.) kuralı eklenmedi: eski sistemde yoktu. "Elle başlık listesi" kaynağı yok (eskide yoktu); `list` kaynağı
  var olan bir `library_lists` listesini gösterir.
- `/api/row/<sabit>` sınırı uygular, gizli sabit satırı yine döner. Yönetim yolu `/api/control/categories` (eski `/api/ops/...`).
- Migrasyon numarası 020: paralel çalışan worker da 020 alırsa çakışır (`db.init` iki kez tanımlı sürümde hata verir).
