# T33 mining paketi B (getirme/görsel: madde 1, 7, 8)

- **1 karakter kodlaması** `core/fetch/http.py` `decode_body`: başlık charset > ilk 4 KB `<meta charset>`/`http-equiv` > utf-8 (katı);
  bildirimsiz ve utf-8 olmayan gövde cp1254 ile çözülür (`�` yerine Türkçe harf). Bilinmeyen charset adı -> yok sayılır.
- **7 afiş hotlink** `core/catalog/images.py` `_http_fetch`: 401/403/boş gövdede tek yeniden deneme, `Referer` = alan adı görselle
  örtüşen aktif sitenin `base_url`'i, yoksa görselin kendi kökü; başarılı Referer host başına hatırlanır (`_referer_ok`, `reset()` temizler).
  Diğer hatalar (404, ssrf, timeout) yeniden denenmez.
- **8 srcset/background** `core/extract/cards.py` `poster_spec`: `data-srcset` (lazy'lerden sonra), `[srcset]` (`<picture><source>` dahil,
  `src`'den sonra), regex son aday = en geniş (srcset küçük->büyük yazılır varsayımı; gerçek `w` kıyası için `parse.py`'de yeni dönüşüm
  gerekirdi, dokunulmadı). Inline stil yedeği önce `background(-image):url()`, sonra eski genel `url()`.
- Test: `server2/tests/test_T33_mining_B.py` (6 test, ~2 ms). Not: görevdeki `test_T33-mining-B.py` adı tire içerdiği için
  `unittest discover` tarafından atlanırdı; alt çizgili ad kullanıldı.
- COPIED.md: bir satır (fikir, kod kopyası yok). check.sh YESIL, 427 test 17.8 sn.
