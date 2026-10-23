# Android parity list

Features added to the web/Tizen client (`tizen-client/`) that the Android client (`android/`) still needs. Add a line whenever the web client gains something; tick it when Android has it.

## Open

- [ ] No built-in server address: remove `DEFAULT_BASE_URL` (`data/net/UrlUtil.kt`); fix the tests that use it (`UrlUtilTest.kt`, `LayoutAndErrorLogicTest.kt`) and `android/README.md`.
- [ ] Cleartext HTTP allowed for any LAN host, not one fixed IP (`res/xml/network_security_config.xml`).
- [ ] First-run "Sunucu adresi" screen: IP or URL, `192.168.1.10` -> `http://192.168.1.10:8090`, test `/api/health`, save; change later in Settings (web: `js/screens/server_setup.js`).
- [ ] Profile picker on every cold start (focus on the last profile); resuming from background keeps the profile (web: `js/screens/profiles.js`, `js/app.js`).
- [ ] "Profil Ekle" on the picker.
- [ ] Guest profile: profiles carry `guest` (API.md); show "Misafir" as a button, not a card; no edit/delete; the server stores no progress / continue / Listem for it.
- [ ] Top control menu: quick profile switcher pill (avatar + name / "Misafir"; OK = popover: profiles, Misafir, Profiller (full profile page); OK switches instantly, Back closes unchanged) (ui-proto: `js/app.js` `CTRLS`, `openPop()`).
- [ ] New UI design (`ui-proto/`, MagicPath / CleanFrame style) once it replaces the web client's screens.
- [ ] New UI search screen: "Ara" control pill (magnifier; Red key shortcut) -> glass search box; >= 2 chars library (`/api/catalog?q=`), >= 3 chars also live site search (`/api/search`), rows "Kütüphanede" / "Sitelerde" (library items not repeated), unresponsive-sites note; detail Back keeps query + focus (ui-proto: `js/search.js`, `js/app.js` `renderSearch()`).
- [ ] Section menu of the new home (Öne Çıkanlar · Diziler · Filmler · Canlı TV · Animeler · DVR; `GET /api/sections`, `GET /api/section/{id}`) + "Devam Et" strip on the menu screen (`/api/row/continue`).
- [ ] User backgrounds: Settings > Background "Görsellerim" = images uploaded to the server (`GET /api/backgrounds`, `blur_url` for glass panels), shuffled, ~60 s opacity crossfade; empty -> dark gradient + note "upload in admin > Arka planlar"; browser-only upload row (`POST /api/backgrounds` multipart `file`) (ui-proto: `js/backgrounds.js`, `js/app.js`).
- [ ] Home background = rotating artwork of the focused section's hero items (full-screen backdrop, opacity crossfade, synced with the hero text; no hero card in any background mode; Live TV/DVR or no image -> one of "Görsellerim", else dark gradient); Settings default "Afişler (bölüme göre)" (ui-proto: `js/app.js` `posterFor()`).
- [ ] Player overlay of the new UI: manual source pick ("Kaynak" list of `streams[]`) and a quick "Bölümler" episode list (‹ › season) inside the player (ui-proto: `js/player.js` `openPanel()`).
- [ ] Detail "Baştan başla" action when there is progress (movie or the episode to resume): plays from 0, ignoring `resume_position`; focus returns to the entering button (ui-proto: `js/app.js` `detailRows()`, `js/player.js` `restart`).
- [ ] Home hero "Oynat" button under the hero text ("Devam et" with progress, series episode code): opens the title's detail and starts playback automatically (same as detail İzle); player Back -> detail (focus İzle), detail Back -> home (focus on the button); hero rotation pauses while it is focused (ui-proto: `js/app.js` `heroPlay()`).
- [ ] Release build feels sluggish on the Android box — profile before porting more screens.

## Done

(none yet)
