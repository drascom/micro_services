# T17 raporu: A1 inceleme kartı (bölüm/bağlantı etiketleri)

**Yapılan:** A1 geçince yeni durum **`review`** (§5.6 listesine eklendi): taslağa/sürüme hiçbir şey yazılmaz, A2/A3/etkinleşme/tarama başlamaz,
`action` admin notu + "SENİ BEKLİYOR". Kart: bölüm (başlık, kart sayısı, 3 örnek adres, öneri title|episode|yoksay), menü bağlantısı
(ad, adres, anlam: dizi/film arşivi, tür, kategori, bölüm listesi, yoksay), "adres ekle" (netguard + aynı site), "Onayla". Öneriler LLM'siz
(`urlpatterns` kümeleri + bağlantı sözcükleri); LLM çağrısı yok. Onay -> etiketler haritaya (`sections[].target/ignore`,
`nav.links[].kind/content/title_type/ignore/manual`, `meta.home.reviewed`) + site özetine satır; sonra normal geçiş (taslak, sürüm/etkinleşme, A2/A3).
A2 ve K1 tohumlama yalnız `content: title` listeleri kullanır (arşiv > type > tür > kategori); `episode`/`ignore` asla. Sona eklenmiş numara
sayfalaması (`-01` -> `-02`, şablon `-{n:02}/`) genel olarak eklendi. Yeniden çalıştırmada önceki etiketler taşınır, yalnız YENİ bölüm/bağlantı sorulur.
Onayda yoksayılan/bölüm listesi bağlantısından gelen frontier satırları (liste + sonraki sayfaları + yalnız oradan bulunan okunmamış sayfalar) silinir;
başlık listeleri değiştiyse ve A2 geçmişse A2 yeniden kuyruğa girer.
**Dosyalar (server2/):** yeni `app/control/sitemap/labels.py`, `tests/test_t17_a1_review.py` (5 test, ~0.4 sn). Değişen: `stages/{a1_home,a2_listing,a3_title,
a5_player,engine,actions,status,state,base,brief}.py`, `sitemap/{schema,adapters}.py`, `crawl/plan.py` (`seed_listings`, `drop_excluded`),
`core/extract/urlpatterns.py`, `problems/views.py`, `admin/routes_sites.py` (`POST .../stages/A1/review`), `admin/static/js/{site,sites}.js`;
testler: t08/t09/e2e setUp'a `engine.A1_REVIEW=False` (eski akışlar), t01 bir zamanlama testi 6 -> 4 istek (süre). Doküman: README §5.5/§5.6.

| Kabul | Kanıt (`test_t17_a1_review`) |
|---|---|
| A1 geçti -> `review`, site taslak, tarama işi yok | `test_a1_pass_waits_for_review_and_a_foreign_added_address_is_400` |
| Bölüm listesi `content: episode` A2'de kullanılmaz; elle eklenen arşiv A2'de ilk, sayfalama `-{n:02}/` | `test_confirmed_labels_drive_a2_and_the_k1_seeds` |
| Onay -> etkin + K1 tohumları yalnız `content: title` | aynı test (`listing_seeds`, `/dizi-arsivi-02/` izlendi) |
| Yeni bağlantıyla rerun yalnız yenisini sorar; yenisi yoksa soru yok | `test_rerun_asks_only_about_new_links` |
| Var olan sitede onay yoksayılan listenin satırlarını düşürür | `test_confirm_on_an_existing_site_drops_the_ignored_lists_rows` |
| Uç: başka host'taki eklenen adres -> 400 | ilk test (TestClient) |
**Sapmalar**
- `engine.A1_REVIEW` modül bayrağı (ayar değil): eski akış testleri kapatır; üretimde hep açık.
- Onarım (T14 `repair`) koşusu inceleme sormaz; önceki etiketler taşınır, yeni öğeler öneriyle yazılır.
- Frontier satırları `dropped` durumu yerine silinir (okunmuş sayfalar ve katalog kayıtları kalır).
- Sayfalama kuralı site başına tek: sona-numara kuralı numarasız listelerde (`/tur/x/`) sayfa 2 üretmez. Sayfa önbelleği 1 sa: aynı saatteki A1 rerun eski ana sayfayı görebilir (önceden de öyle).
**check.sh:** YESIL (6 adım), 292 test, ~10.7-10.9 sn. **Elle kontrol (kullanıcı):** gerçek sitede A1 kartı, açılır listeler, "adres ekle", Onayla sonrası Tarama sekmesi.
