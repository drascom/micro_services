# T33 madencilik paketi C (sağlık: madde 9, 10)

- **9 Fragman canlılığı:** eski `library/trailer_check.py` -> `server2/app/core/play/trailer_check.py` (COPIED.md satırı). Ağ `core.fetch.playback.probe_get`
  (netguard, yönlendirme yok); TTL/bütçe modül sabiti (sağlam 24 sa, ölü 6 sa, bütçe 2.5 sn); hüküm `trailer_checks` + `video_sources.trailer_dead`,
  değişince `cache.refresh_soon`. `api/rows.detail` aksiyonlardan önce `trailer_check.apply_detail(out)` çağırır (583. satırdaki atıf artık gerçek).
- **10 Kapsama + bölüm boşluğu:** `core/catalog/seasons.py` `episode_gap(conn, cid, source)` (TMDB `ok` sezonların yayınlanmış bölümleri, sezon 0 hariç)
  + `library_episodes(..., source=None)`. `control/problems/system.py` `scan_coverage(site)`: dizilerin >%50'si bölümsüz VEYA TMDB boşluğu >%20
  (≥10 beklenen) -> `missing_field(page_type=episodes, field=episodes)`; >%50 bölümlü ama kullanılabilir kaynaksız -> `missing_field(page_type=player,
  field=sources)`. Engelli (blocked) / listesiz diziler sayılmaz; ≥5 dizi şartı. Günlük `scan()` sonucuna `coverage` eklendi.
- Test: `server2/tests/test_T33-mining-C.py` (5 test, ~0.06 sn).
- check.sh: YESIL (6 adım), testler 18.5 sn (<20; sınıra yakın). Ara koşuda Paket A'nın t30 testi geçici kırmızıydı, sonra düzeldi.
- Dokunulmayan ama gereken: `control/problems/views.py` `CLASS_TR["missing_field"]` "afiş yok" diyor; alan `evidence.field`'a göre metin vermeli
  (episodes/sources). `diagnose.TABLE["missing_field"]` varsayılan sayfa `title`; problem satırının `page_type`'ı (episodes/player) kullanılıyor.
