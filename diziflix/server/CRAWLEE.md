# Ortak Obscura ve Crawlee tarama katmanı

Sinemalar ve Yabancidizi aynı `fetch.page(cfg, url)` girişini kullanır.
Sinemalar'ın YAML'ında `fetch_mode: http`, Yabancidizi'de `fetch_mode: browser`
seçilidir. Liste, koleksiyon, detay ve manuel iyileştirme okumaları bu yoldan
geçer. Medya çözümleyicisinin JSON istekleri ve görsel API'si ayrı kalır.

Tarayıcı modunun tek motoru Obscura'dır. Yabancidizi katalog sayfaları, aynı
oturumla indirilen afişler ve kullanıcı film ya da bölüm açtığında yapılan
provider keşfi Obscura üzerinden çalışır. Site extractor ve ortak provider
modülleri motoru bilmez; aynı JSON page-bundle sözleşmesini tüketir.

Her okuma ayrı bir worker sürecinde çalışır: API'nin event loop'u ile çakışmaz.
Süre sınırı 95 saniyedir; zaman aşımında süreç grubu kapatılır. Sunucuda tek
tarama aynı anda çalışır; çağrılar arasında iki saniye beklenir. Robots kuralları
kontrol edilir; HTTP/Cloudflare engeli parser'a başarılı HTML diye aktarılmaz.

## Linux kurulumu ve dağıtım

```bash
cd /root/micro_services/diziflix/server
bash tools/install_crawler.sh
```

Bu komut `/opt/diziflix-crawler` altında yalnızca HTTP Crawlee bağımlılıklarını,
`/opt/diziflix-obscura` altında sabitlenmiş no-render stealth Obscura sürümünü
kurar ve `diziflix-crawler` sistem kullanıcısını oluşturur. Eski tarayıcı
paketleri ile browser binary dizini kurulum sırasında temizlenir. API root ile
çalışıyorsa tarama süreci otomatik olarak bu yetkisiz kullanıcıya geçirilir.
API'nin `.env`, `data/` ve `venv/` dosyaları değiştirilmez.

Sonraki deploy'larda `crawlee_worker.py` ve `obscura_worker.py` dosyalarını ayrıca
`/opt/diziflix-crawler/` konumuna `install -m 644` ile kopyalayın.
Crawler bağımlılıkları değiştiğinde kurulum komutunu yeniden çalıştırın.
Yol/kullanıcı ayarları `.env.example` içindeki `SCRAPER_WORKER_*` değişkenleri
ile özelleştirilebilir. Varsayılan Linux kurulumu otomatik algılanır.

Yerel geliştirmede aynı Python ortamına `requirements-crawler.txt` kurulabilir;
ayrı ortam kullanılıyorsa `SCRAPER_WORKER_PYTHON` ile Python yolu verilir.

## Kullanım

- Yönetim paneli: `/admin` → Kaynaklar → **Tara**.
- API: `POST /api/ops/sites/sinemalar/scan`, ardından
  `POST /api/ops/sites/yabancidizi/scan`.
- Sonuç: `GET /api/ops/runs?site={site}` (süre, öğe sayısı, hata).
- CLI: `venv/bin/python -m tools.ingest sinemalar`, ardından
  `venv/bin/python -m tools.ingest yabancidizi`. CLI sonrasında mevcut API
  önbelleği yenilenene kadar bekleyin veya yeni tarama bitince otomatik yenilenir.
- Zamanlayıcı mevcut `INGEST_SITES` ve `INGEST_INTERVAL` ayarlarını kullanır.
  Bu geçiş, mevcut zamanlama ayarlarını değiştirmez.

Sinemalar'ın `new`, `yakinda` ve tür koleksiyonları korunur. Yabancidizi kendi
`genre_yabancidizi` satırına yazılır; diğer kaynağın listelerini silmez.
Tekrarlanan kartların dolu alanları aktarım öncesinde birleştirilir. Eksik
metadata ile sonraki çalıştırmalar mevcut kimliği ve dolu alanları korur.
Hatalı ana liste kataloğa aktarılmaz; başarısız ek koleksiyonların önceki
kayıtları korunur ve sonuç `partial` olarak bildirilir.

Yabancidizi içerik detayındaki dikey `series-profile-thumb` poster ve yatay
`series/cover` görseli ayrı alanlara kaydedilir. Böylece TV raflarda posteri,
odakta ise gerçek yatay afişi kullanır; yatay afişi olmayan içerikte istemci
sade bir odak efekti gösterir. Görseller sayfayı açan tarayıcıda yüklenip görsel
önbelleğine alınır. TV aynı `/img` uçlarını kullanır. Özel `REMOTE_IMG_HOSTS` ayarı varsa
`yabancidizi.news` eklenmelidir; varsayılan liste bu kaynağı içerir.

Yabancidizi aktarımı sezon sayfalarını gezerek bölüm metadata'sını da kaydeder;
bu aşamada provider çözmez. Oynatılabilir video yalnızca içerik açıldığında
çözülür ve altı saat önbelleğe alınır. Site extractor VidMolly ve OK.ru
alternatiflerini keşfeder; `/dl/<file-code>` → canonical embed dönüşümü ve HLS
çıkarımı ortak VidMolly provider modülündedir. OK.ru'nun oturuma bağlı katalog
geçişi Yabancidizi modülünde, imzalı MP4 kalite çözümlemesi ortak OK.ru provider
modülündedir. Obscura çerez oturumu yalnız önceki adaylar başarısız olursa açılır.
Sinemalar'ın mevcut fragman çözümlemesi korunur.

## Doğrulama

2026-09-05 tarihinde 61'de yönetim API'sinden sırayla doğrulandı:

- Sinemalar: 23 koleksiyon, 387 benzersiz içerik, hata yok.
- Yabancidizi: 66 kart, 52 benzersiz içerik, hata yok.
- Güncel API kataloğu: 464 içerik.
- Yabancidizi'nin 52 afişi de önbellekte; örnek kart görseli JPEG/200 döndü.
- `/api/boot` içinde `genre_yabancidizi` satırı ve satır API'sinde 52 kayıt mevcut.
- Obscura ana sayfayı 63 oturum korumalı görselle yaklaşık 20 saniyede aldı.
- Lanterns 1. bölüm sıfırdan çözüldü; gerçek HLS manifesti HTTP 200 döndü.
- Chromium/Playwright çalışma ortamı kaldırıldı; crawler alanı 656 MB'dan 58 MB'a indi.
- Yerelde ve 61'de 67 sunucu testi ile TV doğrudan bölüm oynatma ve ana ekran
  poster/yatay-afiş testleri geçti.
  Kaynak dosyaları birebir aynı ve `.env` değişmedi.

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m tools.homepage_probe sinemalar
venv/bin/python -m tools.homepage_probe yabancidizi
```

Kaynak: https://crawlee.dev/python/docs/guides/http-crawlers ve
https://github.com/h4ckf0r0day/obscura
