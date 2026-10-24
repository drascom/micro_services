# client/ notları

Tek web istemcisi `client/` (vanilla ES5 HTML/CSS/JS, build adımı yok). Eski tizen-client ve native Android (Compose)
istemcileri kaldırıldı (2026-10-24; git geçmişinde). Ayrıntılı ekran/tuş anlatımı: `client/README.md`.

- Dağıtım: tarayıcı (sunucunun kendisinden 8090 ya da `python3 -m http.server`), Tizen TV `.wgt` (`client/build-wgt.sh` ->
  `dist/diziflix.wgt`), Android WebView kabuğu (`android/`, derlemede `client/` -> `app/src/main/assets/client/` kopyalanır;
  `android/README.md`). Özellik eşitliği otomatik: hepsi aynı kodu çalıştırır.
- Sunucu adresi gömülü değil: kayıtlı `dzproto.server` -> `?server=` -> 8090 origin -> "Sunucu adresi" ekranı.
- Cache-bust: `index.html`'deki `?v=<n>` (css/js birlikte artırılır).
- Motor seçimi (`js/player.js`): karar `streams[].type`'a göre (`hls|mp4|embed`), URL uzantısına değil. Sunucu
  `streams[].engine_hint` (`native|hlsjs|avplay`, `engine_hint_source` = `learned|seed`) verirse doğrudan o motor; istemci
  `platform` (`tizen|android|browser`, `js/api.js`) gönderir. Yoksa varsayılan: Tizen'de Ayarlar > Oynatıcı (`dzproto.engine`:
  HTML5 `<video>` / AVPlay), tarayıcıda `type=hls` için gerekirse `js/vendor/hls.min.js` tembel yüklenir. Sözleşme: `API.md`
  "Oynatıcı motoru ipucu".
- Başarısız oynatma `POST /api/playback-report` (isteğe bağlı `detail`, `platform`, `provider`) ile bildirilir; sunucu
  platform + sağlayıcı + motor istatistiği tutar (`engine_hint` öğrenmesi buradan).
- Hata ayıklama modu (`js/debug.js`, Ayarlar; `dzproto.debug` = Kapalı/Özet/Ayrıntılı): istekler `debug=1` gönderir, sunucunun
  `debug_trace` adımları oynatıcıdaki panelde Türkçe gösterilir (Yeşil tuş / masaüstü G gizle-göster).
- Uzak log: hata ayıklama açıkken log satırları `POST /api/debug-log`'a toplu gider (cihaz kimliği kalıcı); admin
  `GET /api/ops/debug-log` ile cihaz bazında izler. Görsel test cihazda kullanıcı tarafından yapılır.
- Testler: `node client/tests/{player,search,exitconfirm}.js` (sahte DOM/XHR, sunucusuz).
