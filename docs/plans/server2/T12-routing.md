# T12: Yönlendirme kuralları (`control/routing`)

**Bağımlı:** T04a, T06, T08, T10 (T11 varsa `listing`/`genre` origin'leri de dolar). **Açar:** yok. **Oku:** README §5.1 (`routing_rules`,
`source_origins`), §5.8; `inventory/02 §3`; `server/app/homelayout.py`, `server/app/library/categories.py`, `server/app/scraper/collections.py:23-42`.

## Amaç
"Şuradan gelen medya şu gruba gitsin." Sitelerden gelen içerik farklı bölüm/tür/kategorilerden gelir; hangi ana ekran satırına ya da
kategoriye gireceğini **kural tablosu** belirler. LLM kuralı önerir ve serbest metni kurala çevirir; uygulama LLM'sizdir ve kural
değişince yeniden tarama gerekmez (girdi `source_origins`).

## Yapılacaklar
1. Migrasyon `060_routing.py`: `routing_rules`.
   - `match_kind`: `section` (ana sayfa bölüm başlığı), `listing` (liste adı), `genre` (tür adı; origin ya da başlığın `genres` alanı),
     `type` (`movie|series`), `url_pattern` (kaynak adresi regex).
   - `match_value`: karşılaştırma `fold` ile (büyük/küçük harf ve Türkçe karakter duyarsız); `url_pattern` için regex.
   - `target_kind`: `role` (`trending, latest_episodes, latest_series, latest_movies, featured, noteworthy_movies`) | `category` (`home_categories.slug`) | `ignore`.
   - `site_id` boş = tüm siteler için geçerli kural.
2. `control/routing/engine.py`:
   - `targets_for(site_id, source_key) -> [(target_kind, target_value, position)]`: o kaydın origin'leri + alanları kurallarla eşleştirilir;
     `priority` yüksek olan kazanır; `ignore` eşleşirse o origin hiçbir gruba gitmez; bir kayıt birden çok gruba girebilir.
   - `apply(site_id) -> {lists_written, items}`: kuralların ürettiği `library_lists` satırları (`list_id` = `<role>_<site>` /
     `category_<slug>_<site>`; okuma tarafı ve istemci değişmez). Yalnız kuralların yönettiği `list_id`'ler silinip yeniden yazılır;
     sıra = origin `position`. Sonunda `cache.refresh_soon()`.
   - İş türü `routing` (anahtar `routing:<site>`): kural değişince ve her tarama partisinden sonra (T11 `crawl` işi sonunda `enqueue`) çalışır.
   - `preview(rule, site_id=None) -> {count, examples[5]}`: kural kaydedilmeden etkilenen başlık sayısı ve örnekler (yazmaz).
3. Varsayılan kural yok. Kuralı olmayan origin hiçbir özel gruba girmez; başlık yine "tüm diziler / tüm filmler" satırlarında görünür
   (bunlar türden türetilir, listeye bağlı değil).
4. LLM önerisi (`control/routing/suggest.py`):
   - `suggest(site_id) -> [rule taslağı]`: TEK çağrı. Kanıt: sitenin origin değerleri (bölüm başlıkları, liste/tür adları, her birinden 3 örnek
     başlık) + var olan roller ve kategoriler (ad + açıklama). Yanıt: yaml kural listesi. Her taslak şemadan geçirilir; bilinmeyen hedef
     kategorisi "yeni kategori öner: <ad>" olarak işaretlenir (kendiliğinden kategori açılmaz).
   - `from_text(site_id, text) -> rule taslağı`: kullanıcının serbest metni ("Kore dizileri Asya grubuna gitsin") -> tek kural; aynı kanıtla.
   - İki çağrı da admin başlatmalıdır (bütçe: `background=False`). A3 geçip K0 bitince `suggest` bir kez kendiliğinden çalışır ve taslaklar
     "öneri" olarak bekler (uygulanmaz) + `admin_notes` ("N yönlendirme önerisi hazır").
5. Admin **Yönlendirme** sekmesi (`static/js/routing.js`, `routes_routing.py`): üstte "kaynak grupları" tablosu (origin türü, adı, başlık
   sayısı, şu an gittiği yer); kural tablosu (etkin/pasif, eşleşme, hedef, öncelik, kim ekledi: LLM/sen, sil); "öneriler" bölümü (her öneri:
   önizleme sayısı + 5 örnek, `[onayla] [reddet]`, hepsi için `[tümünü onayla]`); serbest metin kutusu -> çevrilen kural + önizleme -> `[onayla]`;
   elle kural ekleme formu (açılır listelerle). Uçlar: `GET /api/control/sites/{id}/routing`, `POST .../routing/rules`, `PUT/DELETE
   .../routing/rules/{rid}`, `POST .../routing/preview`, `POST .../routing/suggest`, `POST .../routing/from-text {text}`,
   `POST .../routing/suggestions/{n}/accept|reject`.

## Kapsam dışı
Kategori CRUD ekranı (kategoriler `home_categories`'ten okunur; yeni kategori açma bu sekmede tek satırlık "kategori ekle" ile sınırlı),
ana ekran satır sırası düzenleme.

## Kabul
- Kural `section="Yeni Bölümler" -> role=latest_episodes`: `apply` sonrası `latest_episodes_<site>` listesi origin sırasıyla dolu; boot/rows
  yanıtında o satır görünür (T04b okuma kodu değişmeden).
- Kural değişikliği yeniden tarama olmadan sonuç verir (getirme sayacı 0).
- `ignore` kuralı: eşleşen origin hiçbir listeye girmez. Öncelik: çakışan iki kuralda yüksek öncelikli kazanır.
- Genel kural (`site_id` boş) iki sitede de uygulanır.
- `fold` eşleşmesi: "KORE DİZİLERİ" değeri "kore dizileri" kuralıyla eşleşir.
- `preview` yazmaz, sayı ve örnek döner.
- `suggest`: sahte LLM yanıtından taslaklar oluşur, hiçbiri kendiliğinden uygulanmaz; geçersiz taslak (bilinmeyen rol) elenir ve sayılır.
- `from_text`: tek taslak + önizleme; onaylanınca kural `created_by=llm` olarak kaydedilir (formdan elle eklenen kural `created_by=admin`).
- `apply` kuralların yönetmediği listelere dokunmaz.
- Uygulama yolunda LLM çağrısı yok.

## Elle kontrol (kullanıcı)
Öneri kartlarının anlaşılırlığı, serbest metinle kural yazma akışı, önizleme örnekleri.
