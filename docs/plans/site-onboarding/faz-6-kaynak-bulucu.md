# Faz 6 (sonra; kullanıcı onayıyla başlar): toplu arama + kaynak bulucu

Sen bir worker'sın. Önce `README.md` ortak kurallarını oku. **Önkoşul:** Faz 5 (ajan tabanlı heal) ve çözülmüş bağlantı önbelleği bitmiş, canlıda denenmiş olmalı. Kullanıcı "Faz 6'ya başla" demedikçe başlama.

## Kullanıcının istediği akış (2026-10-01)
Dizi bulunur, sayfa açılır, detaylar/sezon/bölüm alınır, en az 1 bölümden provider ve video kaynağı bilgisi çıkarılıp kaydedilir. Kullanıcı bölümü açınca henüz taranmamışsa o an bilinen provider denenir ve video oynar. Kaynak bulunamazsa hata mesajı gösterilir; ARKA PLANDA kaynak aranır/eşleştirilir, kullanıcı sayfada kalabilir ya da uygulamada gezinebilir; bulununca bildirim gelir ve bulunan yöntem sonra kullanılmak üzere host adıyla sisteme kaydedilir. Çözülmüş bağlantı saklanır, kullanıcı geri gelince yeniden çözülmez (önbellek: ayrı iş, bitti).

## Parçalar
1. **Toplu arama:** her site yaml'ına `search:` bloğu (endpoint/şablon, parametre, yöntem, sonuç ayrıştırma `row_selector`+`fields`; onboarding ajanı sitenin arama formundan yazar, notlardaki `search-hint:` satırı başlangıçtır; sandbox'ta `test_search`). `/api/search` tüm sitelere dağılır (paralel, süre sınırlı), sonuçlar kanonik kimliğe (TMDB eşleşmesi) göre birleşir, her sonuçta kaynak listesi (site, bölüm sayısı, çözülebilirlik) döner; istemcide kaynak seçimi. Bugün canlı arama yalnız yabancidizi'ye sabit (`routers/search.py`, `site_search/yabancidizi.py`).
2. **Kaynak bulucu işi** (tek başlık/bölüm): oynatma `streams()` boş döner ya da tüm kaynaklar başarısızsa iş kuyruğa alınır (başlık+bölüm başına tek-uçuş, oran sınırı): (a) mevcut kaynakları ZORLA yeniden çöz; (b) olmazsa başlığı diğer sitelerde ara (parça 1), eşleştir (TMDB/kimlik), yeni `video_sources` ekle, çöz; (c) provider bilinmiyorsa ajan onarım modunda o host/biçim için kütüphane tarifi yazar (Faz 5 çekirdeği, kanıt = bu başlığın çözümleme izi); (d) sonuç kaydı: bulunan yöntem host adıyla kütüphanede (tarif) ve kaynak satırında.
3. **Bildirim kanalı:** `GET /api/notifications?since=` (profil bazlı, hafif) ya da detay `poll` yanıtına `source_found` olayı; istemcide "kaynak bulundu" toast'u ve tek dokunuşla oynat. Hata mesajı durumları: "kaynak aranıyor" / "kaynak bulunamadı" (şu anki yanıltıcı "Bölümler alınamadı" metninin yerine; bkz. memory diziflix-client-batch).
4. **Güvenlik/oran:** ajan çalıştırmaları günlük bütçe (`SOURCEFINDER_DAILY_BUDGET`), site başına cooldown, `heal_autoapply` kuralları; tarif yazımı Faz 5 doğrulama/regresyon kapılarından geçer.
## Kabul
Boş kaynaklı bir bölüm açılınca iş başlar, sayfada kalan kullanıcı bildirim alır; bulunan yöntem tarif olarak kaydolur ve ikinci siteyle tekrar kullanılır; testler sahte süreç/sahte sitelerle yeşil.
