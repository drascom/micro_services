# T33 — eski sistem dersleri, paket A (metin/bölüm)

- **2 Sezon/final eki:** `textnorm._SEASON_TAIL` (dizi başlığı): ` - 4. Sezon`, ` 2. Sezon Finali`, ` Sezon 3`, ` Season 2`, ` Final [Bölümü]`, ` Finali` kırpılır; baştaki kelime (`Final Destination`) ve film başlığı korunur.
- **3 Çift bölüm:** `episodes.episode_span`: regex'te isteğe bağlı `episode_end` grubu ya da satır metni `17-18. Bölüm` / `Bölüm 17-18` (en çok 3 fark). İlk numara asıl kayıt, sonraki numaralar aynı URL ile `mirror_of: s1e17` girdisi.
- **4 Numarasız Final/Özel/OVA:** `episodes.unnumbered_kind`. `numbering.from` içinde `text` varsa: özel -> sezon 0 (metindeki sayı ya da sıra), final -> metin numarası ya da sezonun son numarası+1. Yalnız `url` ise satır okunmaz, uyarı verilir ("numarasız N satır atlandı"); t30 sözleşmesi (`from: url` katı) korunur. `position` varsa önceki davranış (sıra numarası) değişmez.
- **5 Başka sitede ara:** `textnorm.search_query` + `first_words`; `search_run.find_elsewhere` -> `_elsewhere_queries` (temiz sorgu) + `_elsewhere_ids` (hiç sonuç yoksa ilk 2 kelime bir kez; yalnız başlığı bu kelimelerle başlayan isabetler içe alınır). Hedef bulununca erken durur.
- **6 Özet kuyruğu:** `detail.clean_overview` ("Devamını Göster/Oku", "Daha fazla", "Read/Show more"; `----` iki dilli ayraçta ilk kısım).
- **Liste dışı dosya (minimal):** `app/control/crawl/process.py` — `_title_record`'da `overview` `x_detail.clean_overview(...)` ile (1 import + 1 satır).
- Testler: `tests/test_T33-mining-A.py` (6 test, <0.01 s). `COPIED.md` 1 satır. check.sh YEŞİL, testler 19.0 s (20 sn sınırına yakın; benim payım ihmal edilebilir).
