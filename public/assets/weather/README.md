# Hava durumu görselleri — Meteocons

Kaynak: [basmilius/weather-icons](https://github.com/basmilius/weather-icons) (Meteocons, Bas Milius).
Lisans: MIT — koşulu gereği `LICENSE` dosyası bu klasörde aynen duruyor.

Buraya yalnız panelin kullandığı durumlar alındı, setin tamamı değil. Dosyalar
`production/fill/svg` (animasyonlu) ve `production/fill/svg-static` (durağan) klasörlerinden
indirildi; durağan olanlar `-static` sonekiyle duruyor çünkü sunum tek düz klasörden yapılıyor
(`GET /assets/weather/:file`, `src/index.ts` içindeki beyaz liste haritası).

| Panel sahnesi (`data-weather-scene`) | Dosya |
| --- | --- |
| `clear-day` | `clear-day.svg` |
| `clear-night` | `clear-night.svg` |
| `partly-day` | `partly-cloudy-day.svg` |
| `partly-night` | `partly-cloudy-night.svg` |
| `cloudy` | `cloudy.svg` |
| `fog` | `fog.svg` |
| `rain` | `rain.svg` |
| `snow` | `snow.svg` |
| `storm` | `thunderstorms-rain.svg` |
| `unknown` | `not-available.svg` |

Animasyon setin kendi SMIL animasyonudur; panel kendi keyframe'ini yazmaz.
`prefers-reduced-motion: reduce` açıkken panel `-static` dosyasına geçer
(`public/js/45-clock-weather.js`, `weatherSceneAsset`).

Dosyalar olduğu gibi durur — elde düzenlenmez; güncelleme gerekiyorsa yukarıdaki depodan
yeniden indirilir.
