# diziflix — Tizen TV istemcisi

Vanilla HTML/CSS/JS. Framework yok, build adimi yok, npm yok. ES2017 hedefi
(eski TV Chromium'lari icin `?.` / `??` kullanilmaz).

## Dosyalar

```
tizen-client/
├── config.xml            Tizen widget tanimi (tv profili, privileges)
├── icon.png              512x512 uygulama ikonu (opak, sicak koyu zemin + karakter; bkz. "Marka gorselleri")
├── index.html            tek sayfa, script'ler sirayla yuklenir
├── css/                  base · home · detail · player · profiles · profile_edit · settings
├── img/                  ornament-corner.png (bildirim kose susu, sari) · logo-wide/logo-square/mascot/mascot-sm/favicon .png (marka, bkz. "Marka gorselleri")
├── js/
│   ├── stage.js          dinamik sahne: saf fit(innerW, innerH) + --stage-w/--stage-h/--stage-scale + resize dinleyicisi
│   ├── keys.js           kumanda keyCode sabitleri + onKey()
│   ├── hydrate_watch.js  detay hidrasyon izleyicisi (?poll=1 yoklamasi, global toast, bellek ici)
│   ├── finder.js         kaynak bulucu metinleri + durum yoklamasi (/api/source-finder, 5 sn)
│   ├── notifications.js  bildirim yoklayicisi (/api/notifications, 30 sn; "Kaynak bulundu" toast'i)
│   ├── api.js            API istemcisi + localStorage + ?delay/?fail bayraklari
│   ├── nav.js            spatial navigation (sutun hafizali, translate3d)
│   ├── app.js            router + baslatma + cikis onayi
│   ├── proverbs.js       yukleme modalinda donen Turk atasozleri (tekrarsiz rastgele secici)
│   ├── playflow.js       Oynat akisi: yukleme modali + sirali akis denemesi + saglik raporu (+ tercihe gore ilk akis)
│   ├── subs.js           WebVTT ayristirici + ikili arama + motor-bagimsiz DOM altyazi katmani (div.pl-subs)
│   ├── tracks.js         ses/altyazi secim modeli: secenekler, varsayilan kural, kalici tercih, akis gecisi karari
│   ├── vendor/           hls.min.js (hls.js, yalniz tarayici HLS'i; README'de surum + sha256) + lisans
│   ├── ui/               row · card · hero · skeleton · modal · navigation (ust menu + yazarken arama + "← Geri") · toast (tek bildirim bileseni) · tracks_panel ("Ses ve Altyazilar" paneli)
│   └── screens/          profiles · home · detail · player · settings
└── README.md
```

## Sunucu adresi

Varsayilan `http://192.168.0.61:8090`. `localStorage['dz.baseUrl']` icinde tutulur ve
uygulama icindeki **Ayarlar** ekranindan degistirilebilir (TV klavyesi/IME ile).
Ayarlar ekranindaki "Baglantiyi test et" `/api/health` ucunu cagirir.
API sozlesmesi: `../API.md`.

## Kademeli yukleme

1. Acilista **aninda** iskelet cizilir (hero iskeleti + 5 shimmer satir); bos ekran gorunmez.
2. `localStorage`'daki onceki `boot` yaniti varsa hemen render edilir, arka planda tazelenir.
3. `GET /api/boot?profile=` → hero + `loaded:true` satirlar dolar.
4. Odak bir satira yaklastikca (gorunen + 1 alt satir) `GET /api/row/{id}` cekilir; ayni satir icin
   ikinci istek atilmaz (in-flight guard).
5. Basarili `boot` yaniti onbellege yazilir.
6. Hata: onbellek varsa icerik + ustte ince uyari seridi, yoksa "Tekrar dene" butonlu hata ekrani. Cevrimdisiyken
   sunucu mesaji yerine "Baglanti yok. Internet baglantinizi kontrol edin." yazar (`DZ.toast.errorText`).

Ana ekran Netflix benzeri bir gorsel hiyerarsi kullanir: yatay raflardaki
icerikler dikey poster olarak gorunur. Odaklanan kartin gercek bir backdrop'u
varsa kart ayni konumda posterle ayni yukseklikteki 640x360 yatay afise genisler;
baslik, yil ve puan afis uzerinde
gosterilir. Yatay afis yalniz odakta indirilir. Backdrop'u olmayan kart poster
olarak kalir ve hafif bir odak efekti kullanir.

**Bolum kartlari** (`card_kind=episode`, `new_episodes` ve dizi icin "Izlemeye Devam Et"): diger kartlar gibi POSTER
kutusudur (240 genis `.row-tile.poster`, dinlenirken dizi posteri `portrait`; yoksa still/`card`'a duser). Odaklaninca
baslik kartlari gibi yatay genisler (`card-focus-landscape`/`focus-expanded`, 640x360): gercek bolum gorseli `still_url`
(`has_still !== false`, /img'de izinli boyut) oncelikli, yoksa dizinin yatay afisi (`has_backdrop`); ikisi de yoksa
genisleme yok, hafif odak efekti. Onizlemede dizi adi + `episode_label` ("S03 B01 · Yanki"; yil/puan yok), kart altinda
ayni iki satir; izlenme cubugu (`progress.pct`) poster ustunde ve genislemede de gorunur; odak kimligi `card_key`.
Uretilmis still yer tutucusu indirilmez. Raflar tek tip oldugu icin `nav.js` kaydirma adimi tile taban genisligidir.
Yuklenemeyen gorselde baslik metni yer tutucu olarak kalir
(kart basina en cok 2 istek; odak her gezindiginde sunucu dovulmez).

Dikey gezinmede odak ekran boyunca asagi inmez. Hero disindaki secili raf,
basligi sabit menunun 20 piksel altinda kalacak sekilde yukariya tasinir; son
raflarda da ayni konum korunur ve gerekirse ekranin altinda bosluk birakilir.

## Yukleniyor / bos / hata durumlari

- **Iskelet**: ana sayfa (hero + 5 poster boyutlu satir: gercek icerik gelince yerlesim kaymaz), detay (hero), profiller.
- **Ana sayfa hatasi**: onbellek yoksa "Icerik yuklenemedi" ekrani (odaklanabilir **Tekrar dene** + **Ayarlar**); onbellek varsa ince
  uyari seridi. **Tek satir hatasi**: o satirda odaklanabilir "Yuklenemedi · Tekrar dene" karti (Enter satiri yerinde yeniden
  ister, odak kaybolmaz; kendiliginden yeniden istek yok). **Bos satir gizlenir** (bos "Icerik yok" karti cizilmez).
- **Detay hatasi**: "Detay yuklenemedi" + Tekrar dene / Geri. Listem guncellenemezse toast.
- **Detay hidrasyonu** (`js/hydrate_watch.js`): yanit `hydrating: true` ise bos sezon/bolum alani yerine "Detaylar yukleniyor…"
  (odaksiz) cizilir ve yapim bellek ici izleyiciye alinir: her 3 sn `GET /api/detail/{id}?poll=1` (sunucuda is baslatmaz), en cok 120 sn,
  en cok 3 eszamanli, id basina tek; ag hatasinda sessiz durur. Bitince acik detay yeni veriyle yeniden cizilir (odak korunur) ve ekran
  ne olursa olsun belirgin bildirim (`DZ.toast.showRich`): basari "Izlemeye hazir" etiketi + buyuk baslik (dizi/film adi) / uyari
  "Bilgi" + "Bolumler su an alinamadi, daha sonra tekrar deneyin" (zaman asiminda ve ag hatasinda bildirim yok).
  `hydrating` yoksa (eski sunucu) false sayilir. Testler: `tests/{hydrate_watch,detail_hydrating}.js`.
- **Kaynak yok** (`availability.state=='unavailable'` + `reason=='no_video_source'`): getirme hatasi DEGIL. Dizi: bolum alaninda "Bu dizi icin henuz izleme kaynagi yok."
  (hidrasyon sonucu `no_source`: ayni cumle belirgin "Bilgi" bildirimi); film: ust bilgi satirinda "Bu film icin henuz izleme kaynagi yok." (fragman varsa
  "Tam izleme kaynagi yok · Fragman mevcut" aynen). Oynat/bolum eylemleri sunucunun `actions: []`'inden dolayi hic cizilmez. "Bolumler su an alinamadi" YALNIZ
  gercek getirme hatasinda (hidrasyon bitti, sezon yok, kaynak-yok isareti de yok). Test: `tests/no_source_state.js`.
- **Toast** (`js/ui/toast.js`, tek yerde tanimli): `DZ.toast.show(mesaj)`; odaga girmez, ~3,5 sn, alttan 60 px.
  **Belirgin bildirim** `DZ.toast.showRich({title, message, kind:'success'|'warn', ms})` (varsayilan 7 sn): ust-ortada (~%14 yukaridan)
  780 px kart, sari ince cerceve, sari etiket + buyuk beyaz baslik (en cok 2 satir) + istege bagli aciklama; sol-ust koseye tasan sari
  kose susu `img/ornament-corner.png` (480 px, `--accent` #F5C518'e boyali PNG), sag-altta ayni gorsel CSS ile 180 derece doner; `warn` soluk.
  Modal/oynatici dahil her ekranin ustunde (z-index 10050), `pointer-events:none`, odak/tus almaz; opacity/transform animasyonu.
  Tek kart, gelenler sira bekler (en cok 3 bekleyen, fazlasi dusurulur). `hideRich()` hepsini kapatir. Test: `tests/toast_rich.js`. **Cevrimdisi**:
  tarayicinin `offline`/`online` olaylariyla sol altta kalici "Cevrimdisi" uyarisi.
- Butonlar/kartlar odak halkasini `.focused` ile gosterir (buton: vurgu dolgusu, kart: 7 px vurgu cercevesi, gri "devre disi"
  dugme: gri dolgu + vurgu cercevesi). Uzun basliklar uc nokta (detay 2 satir), profil adi/player basligi tek satir uc nokta.

## Chrome'da gelistirme (masaustu)

`tizen` / `webapis` objeleri yoksa uygulama tarayicida tam calisir: AVPlay yerine HTML5 `<video>`,
`exit()` yerine konsol logu.

```bash
cd diziflix/tizen-client
python3 -m http.server 8091
# tarayici: http://127.0.0.1:8091/index.html
```

AVPlay yalniz gercek TV'de; tarayicida HTML5 `<video>` + embed iframe fallback devreye girer.

**HLS (hls.js yalniz tarayicida)**: motor/embed karari `streams[].type`'a (`hls|mp4|embed`) gore verilir, URL uzantisina DEGIL
(uzantisiz mp4 vekil adresi ve `.txt` uzantili gercek HLS iframe'e dusmez; uzanti yalniz `type` yoksa yedek). Tarayicida (HTML5 motoru)
`type=hls` ve `video.canPlayType('application/vnd.apple.mpegurl')` bossa `js/vendor/hls.min.js` (hls.js 1.7.3, Apache-2.0; surum/sha256:
`js/vendor/README.md`) tembel `<script>` ile BIR kez yuklenir (`index.html`'de yok); Safari/dogal destek varsa hic yuklenmez. Tizen TV'de AVPlay
HLS'i kendisi oynatir, hls.js'e dokunulmaz. Fatal hls.js hatasi mevcut `playback_failed` akisina baglanir (fatal ag hatasinda `startLoad`,
fatal medya hatasinda `recoverMediaError` BIR kez; fatal olmayanlari hls.js kendi kurtarir). `POST /api/playback-report` basarisizlikta istege bagli
`detail` (<=120 karakter: `hls:<tur>/<ayrinti>[/<http>]`, `video.error.code=N`, `avplay:<hata>`, `start-timeout`) gonderir. Test: `tests/player_hls.js`.
Server CORS `*` verdigi icin Chrome'da tam calisir.

Faydali parametreler (api.js debug bayraklari, her istege eklenir):

- `http://127.0.0.1:8091/?delay=800` → yapay gecikme, iskelet/kademeli yukleme testi
- `http://127.0.0.1:8091/?fail=1` → uclar 500 doner, hata ekrani testi

Klavye: yon tuslari, Enter, `Esc`/Backspace = BACK, `F1` = Kirmizi taklidi (arama / "Sorun bildir"), `Y` = Sari.
`R` harfi artik kisayol DEGIL; Cmd/Ctrl/Alt + tus birlesimleri (Cmd+R, Cmd+Shift+R, Cmd+L...) uygulama
tarafindan yakalanmaz, tarayiciya kalir.

## Oynatma akisi (siyah player ekrani yok)

Oynat / Devam Et / bolum / fragman `DZ.playflow.start(...)` ile baslar (`js/playflow.js`):

1. Detay (ya da sonraki-bolum icin player) ekraninin USTUNDE tam ekran **yukleme modali** acilir:
   donen gosterge + `js/proverbs.js`'den rastgele Turk atasozu (tekrarsiz sira, ~5 sn'de bir
   yumusak opacity gecisiyle degisir) + kucuk asama satiri ("Kaynak araniyor…" → "Video hazirlaniyor…";
   deneme 8 sn'yi asarsa / siradaki akisa gecilince "Baska kaynak deneniyor…"). **Geri** tusu
   denemeyi durdurur, kullanici oldugu ekranda kalir.
2. `/api/streams` akislari sirayla, ekrandan bagimsiz (`detached`) bir motorda denenir (AVPlay
   prepare + play; HTML5'te loadedmetadata + play). Bir akis prepare hatasi / `onerror` / ~15 sn
   zaman asimi verirse o akisin `failure`'i (kendi `attempt_token`, `code`, `engine`) `/api/playback-report`
   ile bildirilir ve siradaki denenir; hepsi modalin altinda. Dogrulanabilir (mp4/hls) akislar once,
   embed (iframe) akislar son care olarak sona siralanir (embed'de basari/hata gozlenemez, rapor yazilmaz).
3. Bir akis **gercekten oynamaya basladiginda** (play() sonrasi ilk zaman ilerlemesi) `success` bildirilir,
   modal kapanir ve player ekrani hazir motorla acilir (`params.prepared`); player motoru devralir,
   yeniden `open()` yapmaz. Player'daki tum gecici durum/etiketler basarida temizlenir; embed modundaki
   elle "Sorun bildir" dugmesi Geri ile birlikte belirir, 3 sn sonra gizlenir (kirmizi tus kisayolu duruyor).
4. Hepsi basarisizsa modal "Kaynak calismiyor" hatasina doner (Tekrar dene / Geri).
5. **Hic akis yoksa + kaynak bulucu** (`js/finder.js`; `/api/streams` yanitinda istege bagli `finder`): `searching` -> "Kaynak araniyor… Bulununca haber verecegiz.
   Sayfada kalabilir ya da uygulamada gezinebilirsin." (panel acikken 5 sn'de bir `GET /api/source-finder/{id}?episode=`; `found` -> "Kaynak bulundu, yeniden
   deneniyor…" ile akis otomatik yeniden istenir; `not_found` -> panel yenilenir; `idle`/panel kapaninca yoklama durur, en cok 10 dk); `not_found` -> "Bu bolum
   (film) icin kaynak bulunamadi."; `finder` yoksa eski genel mesaj. Dugmeler ayni (Tekrar dene/Geri; dogrudan acilan player'da Geri/Yeniden dene).
   `proxied` akis alani yok sayilir: `url` aynen oynatilir. Test: `tests/finder_flow.js`.

Player dogrudan `DZ.app.go('player', {...})` ile acilirsa (prepared yok) eski davranis: kendi akis
listesini ister ve orada dener. Oynatma sirasinda kopan akis yine oynatici icinde bir sonrakine gecer.

## Ses ve altyazi (Netflix tarzi)

Sunucu `/api/streams` yanitinda akis basina `variant_id · audio_lang · sub_mode (hard|soft|none) · hard_lang`, kokte
`subtitles[]` (ayri VTT izleri, `/api/subtitles/<id>.vtt`) ve `audio[]` verir (`../API.md`). Istemci bunlardan secenek uretir;
alan yoksa (eski sunucu, mock kaynak, tek dosya) dugme hic gorunmez.

- **Panel**: oynaticida **Sari** tus ya da **Asagi ok** (sag altta "Ses ve Altyazilar · <secili>" dugmesi, Kaynak/kalite dugmesinin yaninda).
  Iki sutun: **SES | ALTYAZI**; altyazida "Kapali"; secili oge ●; Yukari/Asagi = secenek, Sol/Sag = sutun, OK = uygula,
  Geri/Sari = kapat (player'dan cikmaz). Tek (ya da hic) secenekli sutun gri ve odaklanmaz. Soft degisiklik (iz <-> Kapali)
  paneli acik birakir; baska dosyaya gecis paneli kapatir.
- **Altyazi turleri**: (a) *soft* iz: mevcut akis + DOM katmani; (b) *gomulu* altyazi ("Turkce [gomulu]"): altyazi goruntuye gomulu ayri dosya,
  secilince o akisa GECILIR (`startStream`, konum korunur, "Akis degistiriliyor…" gostergesi; playflow'un yedek-akis mantigi aynen isler);
  (c) *Kapali*: altyazisiz akisa gecer; gomulu-tek kaynakta Kapali gri ve secilirse "Bu kaynakta altyazi goruntuye gomulu; kapatilamiyor." notu.
- **Katman** (`subs.js`): AVPlay/HTML5 fark etmeksizin metin motorun oynatma saniyesinden (`eng.now()`: AVPlay `getCurrentTime`, yoksa
  `oncurrentplaytime`; HTML5 `currentTime`) ~4 Hz ile secilir; DOM yalniz cue degisince yazilir; seek/duraklat/devam zamanla dogal senkron.
  Kontroller gorunurken metin ilerleme cubugunun ustune cikar, gizlenince ekranin alt %10'unda kalir. Iframe (embed) modunda katman/panel yok.
  VTT indirilemezse (404, ~6 sn zaman asimi, bozuk dosya) sessizce Kapali'ya gecilir; tercih silinmez.
- **Varsayilan + tercih** (`tracks.js`): tercih yoksa Turkce soft > Turkce gomulu akis > (Turkce sesli dosya) > Ingilizce soft > Kapali. Secim
  `localStorage`'a profil bazli yazilir (`dz_pref_sub_<profil>` = `off` | dil kodu, `dz_pref_audio_<profil>` = dil kodu | `orig`) ve sonraki
  oynatmada uygulanir: `playflow` ilk denenecek akisi tercihe gore one alir (digerleri sunucu sirasinda yedek kalir). Tercih karsilanamazsa
  varsayilana dusulur, silinmez.

## Bildirimler ("Kaynak bulundu")

`js/notifications.js` (`DZ.notifications`, `app.js` her ekran gecisinde `onScreen(ad)` der): `GET /api/notifications?profile=&since=<id>` yalniz ana ekran /
detay / katalog (film, dizi, Listem, arama) ekranlarinda **30 sn'de bir**; oynaticida (ve Profil/Ayarlar'da) DURAKLAR, bu ekranlara donuste bir kez hemen sorgular.
`last_id` profil basina `localStorage['dz_notif_since_<profil>']`: anahtar yoksa (ilk calistirma) eski bildirimler toast'lanmaz, yalniz `last_id` ilerler (50'den
fazlaysa sayfa sayfa). Yeni `source_found` -> belirgin toast (`showRich`, etiket "Kaynak bulundu", baslik "<ad> S04 B02"; film icin yalniz ad), gosterildikten sonra
`POST /api/notifications/read {upto}`. Toast kuyrugu doluysa kalanlar sonraki yoklamaya kalir. Toast yalniz bilgidir (odak/tus almaz: ilgili detaya gitme yok).
Ag hatasi/cevrimdisi sessiz. Test: `tests/notifications.js`.

## Detay eylemleri ve fragman

Eylem dugmeleri sunucunun `actions[]` alanindan gelir (hedefi sunucu belirler); alan yoksa (eski sunucu) eski alanlara
(`resume`/`progress`/`playback`/`availability`) dusulur:

| `actions[]` | Dugme |
|---|---|
| `play_movie` / `resume_movie` | **Oynat** (`check_required` iken **Yeniden Dene**) / **Devam Et** |
| `play_episode` / `resume_episode` | **Oynat · S01 B01** / **Devam Et · S04 B02** (+ `resume_episode` ise, hedef ilk oynatilabilir bolum degilse, **Ilk Bolumden Basla**) |
| `play_trailer` | **Fragmani Oynat** |

`actions: []` = oynatilacak bir sey yok (uydurulmaz). "Ilk Bolumden Basla" hedefi: numara sirasinda ilk OYNATILABILIR bolum (ozel
bolumler en sona). **Fragman**: canli fragmanda "Fragmani Oynat"; **olu fragmanda** (`availability.trailer==='dead'` ya da
`has_trailer:false` ama `availability.trailer` dolu) dugme gizlenmez, gri/devre disi **"Fragman yok"** gorunur: odak alir, Enter
bilgi toast'i gosterir ve oynatma denenmez; hic fragman kaydi yoksa dugme cizilmez.

## "Izlemeye Devam Et": Listeden kaldir

YALNIZ `continue` satiri kartlarinda (diger raflar degismez). Menu: baslik = dizi/film adi (+ `episode_label`), "Listeden kaldir" (birincil,
ilk odak) ve "Vazgec" (`DZ.modal.open`; Geri tusu = Vazgec, menuyu kapatir ekrani degil).
- **Kumanda**: Tamam/OK ~600 ms basili tut (`js/screens/home.js`: keydown'da zamanlayici, `js/keys.js` `onKeyUp`). Bu kartlarda kisa basis detayi
  **keyup'ta** acar (tus biraktigi an; fark edilir gecikme yok), diger kartlarda Enter eskisi gibi keydown'da. Uzun basistan sonra gelen keyup detayi acmaz;
  tus hala basiliyken otomatik tekrar keydown'lari menu dugmesini tetiklemez (`ignoreEnterWhile`; yeni basis icin yeni keydown gerekir).
- **Fare/dokunma** (`js/ui/card.js` `onLongPress`): ~600 ms basili tutma (kayma > 10 px iptal) ya da sag tik/`contextmenu` menuyu acar; uzun basistan sonra
  gelen `click` bastirilir. Fare imleci kartin ustundeyken sag ustte yuvarlak sari **"✕"** (title "Listeden kaldir") gorunur (yalniz `(hover:hover) and (pointer:fine)`,
  odakli kartta gizli, `data-nav` yok = kumanda odak sirasina girmez); tiklamasi detayi acmaz, ayni menuyu acar (yanlislikla silmeye karsi onay).
- **Kaldirma**: `DZ.api.removeFromContinue(item.id)` = `DELETE /api/continue/{yapim id}?profile=` (bolum kartinda `episode_id` degil yapim id'si; `../API.md`).
  Basarida kart satirdan cikar, bellek verisi + boot onbellegi (`dz.boot.*`) guncellenir, odak komsu karta (yoksa oncekine, satir bos kalirsa gizlenir ve
  ust satira) gecer, "Listeden kaldirildi" bildirimi. Hata/uc henuz yoksa (404): kart yerinde, "Kaldirilamadi, baglantiyi kontrol edin"; cevrimdisiyken istek
  atilmaz ("Internet baglantisi gerekli"). Devam eden id icin ikinci istek gitmez. Test: `tests/continue_remove.js`.

## Ana ekran "Tümü" kartlari ve katalog

`series`/`movies` satirlari ("Tüm Diziler/Filmler", yeni eklenen) ile `trending_series`/`trending_movies` (`sort=trending`, "Tüm Trend Diziler/Filmler") ve
`noteworthy_movies` (`sort=popular`, "Tüm Dikkate Değer Filmler") satirlarinin sonunda ayni "Tümü" karti vardir (`home.js` `CATALOG_ENDS`); katalog ekranini tur + siralama
ile acar (`DZ.navigation.go(view, {sort})`). Katalog `sort`'u `GET /api/catalog?type=&sort=` ile iletir, basligi siralamaya uyar ("Haftanın Trendleri · Diziler/Filmler",
"Dikkate Değer Filmler"), "Sıra:" filtresine "Haftanın trendleri"/"Dikkate değer" secenekleri eklendi; gelen sort yalniz ilk giriste uygulanir (detaydan donuste sayfa/sort
korunur). Slider 6 oge (gostergeler + sarma degismedi). Test: `tests/home_catalog_end.js`.

## Dizi detayi

Dizi ozet sayfasi tek ekrandir. Hero'nun altinda **solda dikey "Sezonlar" listesi** (gercek sezon posteri
varsa kucuk poster — `has_poster:false` iken poster gosterilmez, yalnizca metin; "N. Sezon" + bolum sayisi;
0. sezon "Ozel Bolumler" olarak sona), **sagda "Bolumler" basligi ve secili sezonun bolumleri** (bolum
gorseli `still_url`/`has_still`, numara, baslik, kisa ozet, sure, `air_date`, ilerleme cubugu; yayin tarihi
gelecekte ve kaynak hazir degilse "Yakinda · tarih", `availability.state` unavailable/check_required ise
"Kaynak yok"/"Kaynak kontrol ediliyor").

- Kumanda: Sag = sezondan bolumlere, Sol = bolumlerden sezonlara, Yukari/Asagi liste icinde; listenin
  ustunden Yukari hero satirlarina, altindan Asagi "Benzer Yapimlar"a. Sezon satirinda odak degisince
  bolum listesi 150 ms debounce ile o sezona gecer ve bolum odagi oynatilabilir ilk izlenmemis bolume gider.
  Enter bolumde oynatma akisini baslatir; yayinlanmamis/kaynagi olmayan bolum bilgi modali acar.
- Performans: iki liste sabit yukseklikli goruntu alani + `translate3d` ile kayar (`data-nav-noscroll`
  satirlari nav'in sayfa kaydirmasini atlar, sayfa konumunu ekran ayarlar). Bolum satirlari mutlak konumlu ve
  **pencereli**: yalnizca gorunen ± 3 satir DOM'da (1000+ bolumde de ~12 satir). Sezon posterleri odak
  cevresinde tembel yuklenir.
- Gorseller `/img` uzerinden en kucuk izinli boyutlarda istenir (bolum 320x180, sezon posteri 200x300);
  ayni boyut = ayni URL, ETag/304 ile uyumlu. `has_still:false` iken sunucu yer tutucusu indirilmez, yerel
  yer tutucu cizilir; yuklenemeyen gorsel kaldirilip yer tutucu kalir.

Ana sayfa bolum karti (`card_kind=episode`, `episode_id`, `card_key`) ve "Devam Et" karti ayri bir tek-bolumluk
sayfa acmaz: dizinin normal ozet sayfasi (`id` = yapim id'si, `params.episodeId` ile) acilir, ilgili sezon secili
olur ve ilgili bolume odaklanir (sayfa listeye kayar). Kart odak kimligi `card_key`'dir (ayni dizinin iki bolum
karti karismaz); Geri ana sayfada ayni satir/karta doner. Oynaticidan Geri ile donuste son bolum/sezon hatirlanir.

## Oynatici: bolum basligi ve sonraki bolum

- Dizi bolumunde basligin altinda **"S04 B02 · Bolum adi"** (genel "10. Bolum" adi tekrarlanmaz; ozel bolum "Ozel B02"); altinda
  kaynak etiketi. Detay gelmemisse once yalniz "S04 B02" (bolum kimliginden), detay gelince ad eklenir.
- **Sonraki bolum karti** sag ustte (altyazi alt ortada, kontroller altta: cakismaz; kontroller gizlenince de kalir): bolumun son
  ~45 sn'sinde (10 dk'dan uzun bolumlerde) "Sonraki bolum · S04 B03 · Ad · Simdi oynat". **OK** = simdi oynat (duraklatilmissa OK
  yine oynat/duraklat; Oynat/Duraklat tusu her zaman oynat/duraklat), **GERI** = teklifi ve otomatik gecisi iptal (oynatici acik
  kalir; ikinci GERI cikar). Bolum bitince iptal edilmediyse **5 sn geri sayim** ("Simdi oynat (5)") ve otomatik gecis; sonraki bolum
  yukleme modalinin altinda hazirlanir (siyah ekran yok), modal iptal edilirse detaya donulur. Geri sarilinca kart kapanir.
- **Sonraki bolum = ardisik bolum** (TV-EXPERIENCE-V1): ayni sezonun sonraki bolumu, sezon bitince sonraki normal sezonun ilk
  bolumu; ozel bolumler kendi aralarinda. Ardisik bolumun kaynagi yok / yayinlanmadiysa teklif YOK (sessizce sonrakine atlanmaz).
  Film, fragman ve dizinin son bolumunde kart cikmaz.

## Arama (yazarken) ve ust menu

Android `SearchViewModel` ile ayni kurallar (`js/ui/navigation.js` + `js/screens/catalog.js`): her tusta bekleyen/calisan arama iptal edilir,
**450 ms** sessizlikten sonra aranir; **< 2 karakter** arama yok (sonuclar temizlenir, "Aramak icin bir film veya dizi adi girin."),
**2 karakter** yalniz yerel katalog (`/api/catalog?q=`), **>= 3 karakter** yerel (anlik) + canli `/api/search` ("Kaynakta araniyor…";
hata/`remote_error` olursa yerel sonuclar kalir + not). Bayat yanit sonucu ezmez (`AbortController` varsa istek iptal, yoksa sira numarasi).
**Enter / IME "Ara"** beklemeden arar ve sonuclara iner; Android gibi **2 karakter yeter** (1 karakterde "En az 2 harf yazin" uyarisi).
Ayni sorgu zaten calisiyorsa yeniden istek atilmaz. `dz.search.query` arama boyunca guncel; detaydan donuste sorgu korunur.
- **Kaynak etiketleri** (`/api/search` `source_options`): sonuc kartinda kucuk etiketler (site adi; ilk 2 + "+N"; `broken` = ⚠ uyari + sari, `unknown` = soluk; kartlar
  gibi odaklanmaz). `remote_sites`'ta `ok:false` olan (`skipped` olmayan) siteler icin sonuc izgarasinin altinda silik tek satir "Bazı kaynaklar yanıt vermedi: <adlar>"
  (bazi siteler yanit verdiyse ust satirdaki genel "Canlı kaynak şu an yanıt vermedi" notu yazilmaz). Odak/secim davranisi ayni. Test: `tests/search_sources.js`.
- **Odak/imlec korunur:** yazarken ekran ve ust menu YENIDEN KURULMAZ. Arama gorunumundeyken yalniz sonuc alani guncellenir
  (`catalog.liveSearch`); baska ekrandan ilk gecis ayni `input` DOM dugumunu tutar (`navigation.create({reuse})`, catalog yalniz digerlerini siler).
- Neden: odagi/ekran klavyesini sonradan geri vermek TV'de guvenilmez (klavye kapanir); dugum hic DOM'dan cikmazsa odak, imlec ve deger oldugu gibi kalir.
- **Ust menu:** logo | arama kutusu + **✕** | Listem | Profil | Ayarlar. "Ana Sayfa" dugmesi yok (Geri tusu ana sayfaya goturur). Logo yalniz fare/
  dokunma ile tiklanir (Ana Sayfa; ana sayfadaysa en uste), kumanda odak sirasinda DEGIL → odak sirasi arama kutusundan baslar (sutun 0).
  **✕** kendi odaklanabilir dugmemiz (yerel `type=search` "x"i TV'de odaklanamaz, CSS'le gizli): yalniz kutu doluyken gorunur/gezilebilir;
  Enter/tik kutuyu + sonuclari + saklanan sorguyu temizler, odagi kutuya verir. Imlec metnin ucundayken Sag/Sol komsu ogeye (✕/Listem) gecer.
- **Arama disi ekrana gecince** (Ana Sayfa/Geri, Listem, Profil, Ayarlar) kutu bosalir ve `dz.search.query` silinir; detaydan donuste korunur.
- **"← Geri" dugmesi** (fare/dokunma; Ayarlar, Profil, Profil duzenle): Geri tusuyla ayni islem (`DZ.app.goBack`), kumanda odak sirasina
  girmez; gidilecek yer yoksa (ilk acilista profil secimi) gosterilmez. Testler: `live_search.js`, `navigation_search.js`, `back_button.js`.
  Film/dizi **detay** ekraninda da ayni bilesen (`DZ.navigation.backButton`) hero'nun sol ustunde: koyu yari saydam hap + beyaz metin, hover'da sari
  (`css/detail.css` `.detail .back-btn`); Geri tusuyla ayni hedefe gider, toast'in (overlay) altinda kalir. Test: `back_button.js`.

## Ayarlar

Yerlesim (`js/screens/settings.js`, `css/settings.css`; yatay kenar 80 px, sayfa kaydirilmaz, hepsi 1080p'ye sigar):
1. **Ust satir:** sol basta "← Geri" (yalniz fare/dokunma, odak sirasinda degil; `DIZIFLIX` logosu bu ekranda YOK), SAGDA **Profil degistir** (kumandayla odaklanir). Altinda "Ayarlar".
2. **Sunucu karti:** `[ adres girdisi ............ [Baglantiyi test et] ]  [Kaydet]`. "Baglantiyi test et" girdinin ICINDE, sag uc; **Kaydet** (sari dolgu, birincil)
   girdiden sonra sagda. Durum/test sonucu kartin altinda tek satir (tum mesajlar eskisiyle ayni). Girdide `aria-label`.
3. **Iki yan yana kart** (yuvarlak kenarlik, dikey radyo listesi, `role=radiogroup`/`radio` + `aria-checked`): sol **Varsayilan altyazi dili**
   (Turkce / Ingilizce / Kapali; oynaticidaki panelin yazdigi `dz_pref_sub_<profil>` ile ayni tercih; profil secili degilse kartta "Once bir profil secin"),
   sag **En yuksek kalite** (`dz_pref_quality`: **1080p** varsayilan / **1440p** / **Otomatik**; ipucu: bu cozunurlugun ustundeki akislar en son denenir).
   Secili secenek = dolu nokta + hafif sari zemin/kenar; odakli secenek = sari halka (ana sayfa kartlariyla ayni dil); ikisi ayri gorunur.
4. **Alt:** **Onbellegi temizle** (yalniz `dz.boot.*`/`dz.row.*`; tercihler, sunucu adresi, profil ve arama sorgusu korunur), altinda soluk bilgi satirlari:
   build damgasi + motor, sunucu adresi, sunucu bilgisi (`/api/health`: kaynak · icerik sayisi; yanitta `version` varsa o da). Alttaki ayri "Geri" dugmesi YOK
   (TV'de Geri tusu, fare icin ustteki "← Geri").

Kumanda (nav satirlari ust→alt): `settings-top` [Profil degistir] · `url` [girdi, Test et, Kaydet] · `prefs` [altyazi secenekleri…, kalite secenekleri…] ·
`settings-cache` [Onbellegi temizle]. Yukari/Asagi satirlar arasi (her satir son sutununu hatirlar); `prefs` tek nav satiridir (kartlar ayri DOM kutulari),
ic hareket `screen.key` icinde: kart icinde Yukari/Asagi secenekler arasi, Sol/Sag kartlar arasi (ayni sira; hedef kart kisaysa en yakin), kartin ilk/son
secenegindeki Yukari/Asagi nav'a (sunucu satiri / Onbellegi temizle) birakilir. Enter: secenek = sec + kaydet (mevcut tercih anahtarlari/mantigi), dugmeler
mevcut isleyicilerle; girdide Enter alani kapatir ve odagi Kaydet'e verir. Acilis odagi **Kaydet** (eski davranis): girdiye odak ekran klavyesini acar, Profil degistir
ise yanlislikla profil cikisi yapardi; Kaydet ayni adresi kaydedip test eden zararsiz eylem. Odak geri yukleme icin her oge `data-focus-key` tasir.
Testler: `settings_layout.js` (yerlesim + odak haritasi), `settings_prefs.js`, `quality_pref.js`, `back_button.js`.

**Kalite tercihi** `playflow.js` akis siralamasina uygulanir: `quality`/`label`'dan ayristirilan cozunurluk (`1440p`, `1920x1080`,
`4K`…) tercihi ASAN dogrudan akislar (1080 tercihinde 1440p/2160p) dogrudan akislarin sonuna (embed'lerin onune) atilir; ayristirilamayan
(`auto`, HLS) akisa dokunulmaz, "Otomatik" hicbir sey degistirmez. Dil/altyazi tercihi bundan SONRA uygulanir (ayni dilin dusuk
cozunurluklu dosyasi once; tek eslesen dosya 1440p ise dil tercihi kazanir). Nedeni: OK.ru mp4 `quad` = 1440p sunucuda en yuksek
kalite diye en basa geliyordu ve TV'de takiliyordu.

## Marka gorselleri

Kaynaklar (kullanicinin verdigi, seffaf arka planli orijinaller): `../docs/brand/logo-a.png` (kare, klaket sagda) ve
`../docs/brand/logo-b.png` (klaket solda). Asagidaki turev PNG'ler bunlardan Pillow ile uretildi (seffaf kenar kirpildi,
LANCZOS, alfa korunur, 256 renk paletli + `optimize`; hepsi < 150 KB; gorunum boyutunun ~2x'i -> 4K TV'de keskin).
"Karakter" = logo-a'nin yazisiz hali (yazi baslamadan once kesilip alt kenar yumusatildi).

| Dosya | Boyut | Nerede |
|---|---|---|
| `icon.png` | 512x512, opak | Tizen uygulama ikonu (`config.xml` `<icon src="icon.png"/>`); karakter, koyu-sicak zeminde |
| `img/favicon.png` | 128x128 | web sekmesi ikonu (`index.html` `<link rel="icon">`) |
| `img/logo-wide.png` | 720x710 | Profil secimi ekrani: basligin ustunde 360px genislikte tam logo (`js/screens/profiles.js`, `.brand-logo`); eski `DIZIFLIX` yazisinin yerine gecer |
| `img/logo-square.png` | 630x640 | Ilk acilis / yukleme: ana sayfa iskeletinin ustunde ortada ~316x320 (`js/ui/skeleton.js` `bootLogo()`, `.boot-logo`); veri gelince ana sayfa yeniden kuruldugu icin kalkar. Yalniz opacity girisi |
| `img/mascot.png` | 310x240 | Oynatma "yukleniyor" modalinda spinner'in ustunde 155x120 (`js/ui/modal.js` `loading()`, `.loading-mascot`); profil/ana sayfa hata ekranlarinda 194x150 (`.brand-mascot`) |
| `img/mascot-sm.png` | 124x96 | Ust menu: `DIZIFLIX` yazisinin solunda 62x48 amblem (`js/ui/navigation.js`, `.brand-emblem`; yazi ve sari aralik ayni, tiklama amblem+yazi birlikte = Ana Sayfa); Ayarlar alt bilgisinde sag altta 52x40 (`.set-info` arka plan) |

Gorsellerin hicbiri `data-nav` tasimaz (odak/gezinme ve ust menu odak sirasi/yuksekligi degismez), yuklenemezse gizlenir.
Yeniden uretmek: `Q=1 server/venv/bin/python docs/brand/make_brand.py` (repo kokunden; Pillow); olculer ve oranlar `tests/brand_logos.js` ile korunur.

## Paketleme (.wgt)

```bash
cd diziflix/tizen-client
./build-wgt.sh          # ../dist/diziflix.wgt uretir (config.xml paket kokunde)
```

Her build `js/version.js`'e tarih-saat damgasi (`window.DZ_BUILD`) yazar; uygulama bunu
kosede gosterir, boylece TV'de hangi surumun kurulu oldugu bellidir. Gizli dosyalar,
`__MACOSX`, `*.wgt` ve script'in kendisi pakete girmez.

> Apps2Samsung kurulumdan sonra kaynak `.wgt`'yi silebilir (ayardan kapatilabilir).
> Pratik olsun diye build'i `~/Downloads`'a kopyalamak ise yarar.

## TV'de kurulum (Apps2Samsung)

Tizen Studio, sertifika, DUID veya `sdb` **gerekmez**. Sertifikayi Apps2Samsung halleder.

1. **Developer Mode**: TV'de **Apps** ekranini ac → kumandadan **12345** yaz →
   Developer Mode **ON** → "Host PC IP" = gelistirme makinesinin IP'si → TV'yi yeniden baslat.
2. [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) calistir → TV agda otomatik
   bulunur → **Custom WGT** ile `dist/diziflix.wgt` (ya da `~/Downloads/diziflix.wgt`) sec ve kur.

## Kumanda tuslari

| Tus | keyCode | Islev |
|---|---|---|
| Sol / Sag | 37 / 39 | satir ici gezinme · oynatirken 10 sn seek (basili tutunca hizlanir) |
| Yukari / Asagi | 38 / 40 | satirlar arasi gezinme |
| Enter | 13 | sec · oynat/duraklat · "Sonraki bolum" karti gorunurken (oynuyorsa) sonraki bolume gec |
| RETURN (BACK) | 10009 | yukleme modali→iptal (detayda kal), player→detay ("Sonraki bolum" karti varsa once kart iptal), detay→ana ekran, ana ekran→cikis onayi |
| Play/Pause | 10252 / 415 / 19 | oynat / duraklat |
| Stop | 413 | oynatmayi bitir |
| FF / REW | 417 / 412 | ileri / geri seek |
| Kirmizi | 403 (masaustu: F1) | arama · oynaticida "Sorun bildir" |
| Sari | 405 | profil ekraninda secili profili sil · oynaticida "Ses ve Altyazilar" paneli (Asagi ok da acar) |

Medya tuslari `tizen.tvinputdevice.registerKey(...)` ile try/catch icinde kaydedilir.

## Responsive sahne (PC tarayici / TV)

Arayuz 1920x1080 "tasarim birimi" uzerinde cizilir; `#app` sahnesi pencereyi **tam doldurur** (ust/alt/yan siyah bant yok).
`js/stage.js` `innerWidth/innerHeight`'tan hesaplar (`DZ.stage.fit`, saf fonksiyon):

- pencere orani >= 16:9: `olcek = innerHeight/1080`, sahne `innerWidth/olcek` x 1080 (>= 1920x1080)
- pencere 16:9'dan dar/uzun: `olcek = innerWidth/1920`, sahne 1920 x `innerHeight/olcek` (>= 1920x1080)
- **TV (1920x1080) ve her 16:9 pencere: sahne tam 1920x1080** (TV'de `translate3d(0px,0px,0) scale(1)`, gorunum birebir ayni).
  Ornekler: 1440x900 -> 1920x1200, 2560x1080 -> 2560x1080, 1280x1024 -> 1920x1536. Cok kucuk pencere 320x180 gibi hesaplanir.

Sahne boyutu `:root` uzerindeki `--stage-w` / `--stage-h` (+ `--stage-scale`) CSS degiskenlerine yazilir; CSS'te tam-ekran kutular
(`#app`, `#screen`, `#overlay`, `.hero`, `.detail`, `.player*`, modal/toast, ust menu...) bu degiskenleri kullanir. Fazla alan: icerik
sola/uste hizali, hero/detay gorseli ve `--bg` arka plani doldurur; ortalanan katmanlar (modal, toast, "Ses ve Altyazilar" paneli) ortada kalir.
`resize`/`orientationchange` -> olcek ve degiskenler hemen, agir yeniden yerlesim 120 ms debounce ile: `DZ.app` aktif ekranin
`onResize(stage)` kancasini + `DZ.nav.relayout()` cagirir (detayda sezon/bolum listesi `viewH = sahne yuksekligi - 180` yeniden hesaplanir ve
pencereli bolum listesi yeniden cizilir; ana ekranda satir/gorsel pencereleri tazelenir; `row.js` gorunen kart sayisi sahne genisligiyle orantili).
Tarayici yakinlastirmasi (Cmd +/-) ve yuksek DPI `innerWidth/innerHeight` uzerinden zaten yansir. Video `object-fit: contain`: 16:9 disi
pencerede yalniz video etrafinda siyah kalir. TV AVPlay katmani (`#avplayer`, `setDisplayRect`) gercek ekran pikseli: 1920x1080 sabit.
Test: `node tests/stage_fit.js`.

## Performans notlari

- Satir basina DOM'da en fazla 20 kart; gorsel penceresi gorunen ± 4 kart.
- Posterler sunucudan `300x450`, odak afisleri `640x360` boyutunda istenir; yatay
  afis yalnız odak anında tembel yüklenir.
- Kaydirma `transform: translate3d(...)` ile yapilir (`left` / `scrollLeft` animasyonu yok).
- `box-shadow` / `blur` / `filter` kullanilmaz; gecisler yalnizca `transform` + `opacity`, 200 ms.
- `will-change: transform` sadece odakli kartta.
