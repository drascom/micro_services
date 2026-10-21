# Ulusal TV (TR) — yerel Stremio addon

iptv-org Türkiye listesini (https://iptv-org.github.io/iptv/countries/tr.m3u) Stremio addon
protokolüyle sunan, bağımlılıksız bir Node sunucusu. Liste açılışta çekilir, 6 saatte bir yenilenir.

## Başlatma
    ./start.sh              # varsayılan port 7788
    PORT=8000 ./start.sh    # farklı port

## Nuvio'ya ekleme (Android emülatör)
Emülatörden host'a `10.0.2.2` ile erişilir. Addon URL'i:

    http://10.0.2.2:7788/manifest.json

Nuvio içinde Settings > Addons > Add addon (URL yapıştır). Ya da:
`adb shell am start -a android.intent.action.VIEW -d "stremio://10.0.2.2:7788/manifest.json" com.nuvio.app`

Not: Bu addon yerel makinede çalışır — **sunucu kapalıysa kanallar görünmez** ve oynatılmaz.
