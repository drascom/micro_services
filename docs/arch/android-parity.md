# Android parity list

Features added to the web/Tizen client (`tizen-client/`) that the Android client (`android/`) still needs. Add a line whenever the web client gains something; tick it when Android has it.

## Open

- [ ] No built-in server address: remove `DEFAULT_BASE_URL` (`data/net/UrlUtil.kt`); fix the tests that use it (`UrlUtilTest.kt`, `LayoutAndErrorLogicTest.kt`) and `android/README.md`.
- [ ] Cleartext HTTP allowed for any LAN host, not one fixed IP (`res/xml/network_security_config.xml`).
- [ ] First-run "Sunucu adresi" screen: IP or URL, `192.168.1.10` -> `http://192.168.1.10:8090`, test `/api/health`, save; change later in Settings (web: `js/screens/server_setup.js`).
- [ ] Profile picker on every cold start (focus on the last profile); resuming from background keeps the profile (web: `js/screens/profiles.js`, `js/app.js`).
- [ ] "Profil Ekle" on the picker.
- [ ] Guest profile: profiles carry `guest` (API.md); show "Misafir" as a button, not a card; no edit/delete; the server stores no progress / continue / Listem for it.
- [ ] New UI design (`ui-proto/`, MagicPath / CleanFrame style) once it replaces the web client's screens.
- [ ] Section menu of the new home (Öne Çıkanlar · Diziler · Filmler · Canlı TV · Animeler · DVR; `GET /api/sections`, `GET /api/section/{id}`) + "Devam Et" strip on the menu screen (`/api/row/continue`).
- [ ] Dynamic backgrounds (`GET /api/backgrounds`) once the server has them.
- [ ] Day-night scene background driven by server time: `type: "scene"` keyframes (dawn/day/sunset/night of the same view) blended by opacity; clock = HTTP `Date` header offset (re-sync ~30 min, local fallback); Settings time-preview slider (web: `ui-proto/js/backgrounds.js`, `ui-proto/js/app.js`).
- [ ] Release build feels sluggish on the Android box — profile before porting more screens.

## Done

(none yet)
