# js/vendor

Ucuncu taraf kutuphaneler (degistirilmeden, build adimi yok). `.wgt` paketine girer (`build-wgt.sh` tum klasoru paketler).

## hls.min.js — hls.js 1.7.3

- Kaynak: `https://cdn.jsdelivr.net/npm/hls.js@1.7.3/dist/hls.min.js` (npm `hls.js@1.7.3`, UMD, `window.Hls`)
- Lisans: Apache-2.0, Copyright (c) 2017 Dailymotion (http://www.dailymotion.com); metni `hls.js.LICENSE`
- sha256: `a12e7ee1cd64a69dcdb314157e45dafcba705bfb0b1440b7935cb265d374423e`
- Dosyanin sonundaki `sourceMappingURL=hls.min.js.map` satiri bilerek birakildi (dosya CDN ile birebir; .map yok, zararsiz)
- Kullanim: YALNIZ tarayici/HTML5 motorunda, `streams[].type == "hls"` ve `video.canPlayType('application/vnd.apple.mpegurl')` bossa,
  `js/screens/player.js` tarafindan tembel `<script>` ile BIR kez yuklenir (index.html'de yok). Tizen TV'de AVPlay HLS'i kendisi
  oynatir; hls.js oraya dokunmaz. Safari/dogal destek varsa hic yuklenmez.
- Surum guncelleme: yeni surumu ayni yoldan indir, sha256'yi bu dosyaya yaz, `HLS_SRC` (`player.js`) icindeki `?v=hlsjs-<surum>`'u artir.
