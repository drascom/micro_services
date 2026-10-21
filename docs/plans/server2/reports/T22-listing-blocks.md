# T22 raporu: bir liste sayfasında birden çok blok + etiketleme

**Madde 1 nedeni:** kullanıcının sitesinde A1 onayında `/trends` = `kind: category, content: title`. A2 `_confirmed()` yalnız `archive`/`type` bağlantılarını "onaylı" sayıyordu; kategori/tür onaylı listeler `confirmed` bayrağı almadı, tek-örnek istisnası uygulanmadı (`samples_count=1 (>=2)`). Düzeltme: `_confirmed_paths` = A1'de onaylanan her `content: title` bağlantısı (her tür) + admin örnekleri.

**Yapılan**
- `core/extract/blocks.py` (LLM'siz): satır seçicisinin satırları en yakın ortak atanın ayrı çocuklarında ve ayrı başlıklar altında (h1-h4/caption/legend/başlık sınıfı/önceki kardeş/sekme metni) toplanıyorsa bloklar: sıra, başlık, satır, 3 örnek adres, adres türü (`link_kind`) + baş segment. Başlıksız bölünme (ızgara `.row`) tek liste sayılır. `parse_rows` yalnız seçilen blokların satırlarını okur (`_block`, `_type`).
- Öneri (`a2_listing.propose_blocks`): ipucu/site notu sıra sözcükleri (`hints.block_order`: ilk/ikinci/üçüncü, first/second… + dizi/film/kullanıcı/karışık) > başlık sözcüğü (kullanıcı/üye/user/profil -> yoksay) > adres kümesi (ana sayfa başlık kartlarının/film-dizi bloklarının baş segmentine uymayan = yoksay) > film/dizi adresi > karışık.
- A2: birden çok blok varsa ölçüm geçince `review` ("SENİ BEKLİYOR"), kartta blok başına `dizi listesi / film listesi / karışık başlık listesi / yoksay`; onay -> `pages.listing.blocks: [{url, index, selector, heading?, type|ignore}]` + `meta.listing.reviewed`. Yeniden koşuda aynı sayfa/başlık etiketleri taşınır, yalnız yeni bloklar sorulur. Ölçüm, sayfa-2 denemesi, devir yalnız seçili bloklardan; devir blokları sırayla karıştırır + `types: {url: series|movie}`.
- A3: devirdeki `types` tür oyu/varsayılanı olarak kullanılır. Tarama `_listing` yalnız seçili blokların satırlarını ekler; bloğun türü `block_type` kökeniyle saklanır (katalog kökenine girmez), `_record_type`: harita eşleşmesi > blok türü > varsayılan.
- Şema: `listing.blocks` doğrulaması; `adapters.listing_rule` `blocks` döner; site özeti "Listing blocks: - /trends: blok1 dizi, blok2 film, blok3 yoksay"; `result_line` aynı satır.
- Admin: `POST .../stages/A2/review` `{blocks: [{url, index, choice}]}` (`actions.review` doğrular); `site.js` blok kartı (`A.esc`, A1 form stili, mevcut akordeon/cardsync kuralı değişmedi); `node --check` temiz.

**Dosyalar (server2/):** yeni `app/core/extract/blocks.py`, `tests/test_t22_listing_blocks.py` (5 test, ~0.1 sn). Değişen: `stages/{a2_listing,a3_title,hints,actions,engine,status,brief}.py`, `sitemap/{schema,labels,adapters}.py`, `crawl/{process,plan}.py`, `admin/routes_sites.py`, `admin/static/js/site.js`.

**Sapmalar**
- Blok eşleşmesi önce başlıkla, yoksa sırayla; bloklar sayfaya (`url` yolu) bağlı, sayfalı listelerde sorgu farkı yok sayılır.
- Var olan sitede blok onayı, yoksayılan bloktan önceden frontier'a girmiş satırları silmez (yalnız sonraki okumalar süzülür).
- LLM yaması `blocks` vermezse ölçüm önerilerle yapılır, inceleme blokları yamaya ekler. Önbellekteki gerçek `/trends` sayfası tek tablo içeriyordu (blok yok); 3 bloklu yapı uydurma fixture ile sınandı.

**check.sh:** YESIL (6 adım), 345 test, ~12.1-12.4 sn. **Elle kontrol (kullanıcı):** A2'yi yeniden dene -> blok kartı, seçimler, Onayla sonrası A3 örnekleri (dizi + film).
