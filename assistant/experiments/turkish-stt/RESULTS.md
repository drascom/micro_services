# 2026-10-10 sonuçlar — mihuai/turkish-stt vs asistan Whisper

> **Not:** mihu modeli (`models/turkish-stt/`) kullanıcı kararıyla silindi; mihu sonuçları aşağıda tarihsel
> olarak duruyor. Yeniden indirme: `README.md > Kurulum` (git clone + curl). Model yokken `eval.py mihu` atlar.

## Kalıcı whisper-server (2026-10-10, uygulandı)

Asistan artık `runtime/whisper/whisper-server` (v1.9.4, aynı kaynak/bayraklar) kullanıyor: ilk kayıtta başlar,
model yüklü kalır, 10 dk boşta kalınca kapanır; hata olursa istek başına tek seferlik `whisper-cli` fallback.
Aynı oturumda sıralı, şarjda. `-l auto -t 8 -nt`, sunucuda `-bo 5 -bs 5` (= CLI varsayılanı).

| yol | set | n | WER ham | WER sayı | CER sayı | ms/dosya medyan (p95) | soğuk ms | tepe RSS |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| whisper-cli (eski, dosya başı süreç) | user | 29 | 16,4 | 16,3 | 10,4 | 1950 (2169) | – (her istek ≈ soğuk) | 2,0 GB |
| **whisper-server (yeni, sıcak)** | user | 29 | 16,4 | 16,3 | 10,4 | **1569 (1784)** | 1851 | 2,0 GB |
| whisper-cli (eski) | human | 60 | 8,4 | 6,4 | 2,2 | 2155 (3872) | – | 2,0 GB |
| **whisper-server (yeni, sıcak)** | human | 60 | 8,4 | 6,4 | 2,2 | **1782 (3524)** | 2539 | 2,0 GB |

- Transkriptler iki yolda **birebir aynı** (user 29/29, human 90/90 klip) — WER farkı yok.
- Kazanç ~380 ms/dosya (%18–20). Kalan süre çözme + `-l auto` dil tespiti (~0,7 s); model yükleme değil.
- Soğuk = süreç başlatma -> ilk metin (sunucu sağlık hazır ~340 ms + ilk istek). Gemma yüklüyken asistan
  içinden soğuk 2095 ms, sıcak 1588 ms.
- RAM: Whisper sunucusu ~2,0 GB RSS (footprint 2,05 GB) sürekli; Gemma llama-server ~7,1 GB RSS (mmap,
  footprint ~0,6 GB) -> ikisi birlikte ~9,1 GB RSS. Boşta 10 dk sonra Whisper serbest kalır (`whisper_idle_ms`).

Makine: Apple M4 Pro (12 çekirdek, 24 GB), şarjda (`pmset`: AC Power, %100), ölçümler sıralı.
mihu: sherpa-onnx 1.13.8, `modified_beam_search`. Whisper: whisper.cpp v1.9.4, `ggml-large-v3-q5_0`, Metal.
Tam döküm (tüm örnekler): `runs/summary.md` (`./run.sh` ile yeniden üretilir).

## Ana tablo

WER/CER: Türkçe normalize (küçük harf, İ/ı, noktalama yok). "ham" = rakamlar normalize edilmeden,
"sayı" = rakam->yazı. ms/dosya = medyan (p95). mihu süresi = sıcak modelde saf hesap;
Whisper süresi = asistandaki gibi süreç + model yükleme dahil (asistan her istekte `whisper-cli` başlatıyor).

| model/ayar | set | n | WER ham | WER sayı | CER sayı | RTF | ms/dosya | tepe RSS | akış: ilk ara / son |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **whisper-asistan** (`-l auto -t 8`) | human | 60 | 8,4 | **6,4** | **2,2** | 0,240 | 2279 (3925) | 2,0 GB | – (toplu) |
| whisper `-l tr -t 8` | human | 60 | 8,4 | 6,4 | 2,2 | 0,170 | 1510 (3249) | 2,0 GB | – |
| whisper `-l tr -t 4` | human | 60 | 8,4 | 6,4 | 2,2 | 0,170 | 1516 (3271) | 2,0 GB | – |
| **mihu int8, 1 thread** | human | 60 | 17,4 | **17,7** | **6,7** | 0,030 | 193 (837) | 279 MB | 1,21 s / 18 ms |
| mihu int8, 4 thread | human | 60 | 17,4 | 17,7 | 6,6 | 0,031 | 215 (823) | 284 MB | 1,21 s / 17 ms |
| mihu fp32, 1 thread | human | 60 | 17,5 | 17,9 | 6,8 | 0,040 | 261 (1147) | 541 MB | 1,21 s / 25 ms |
| whisper-asistan | say-tr (Yelda) | 24 | 3,4 | 3,4 | 0,9 | 0,538 | 1985 | 2,0 GB | – |
| whisper `-l tr -t 1` | say-tr | 24 | 3,4 | 3,4 | 0,9 | 0,373 | 1396 | 2,0 GB | – |
| whisper `-l tr -t 4 -ng` (CPU) | say-tr | 24 | 3,4 | 3,4 | 0,9 | 0,928 | 3463 | 2,1 GB | – |
| mihu int8, 1 thread | say-tr | 24 | 15,5 | 15,3 | 4,3 | 0,029 | 106 | 292 MB | 0,51 s / 17 ms |
| mihu fp32, 1 thread | say-tr | 24 | 13,8 | 13,1 | 4,0 | 0,042 | 159 | 533 MB | 0,51 s / 25 ms |
| whisper-asistan | mihu-synth* | 81 | 14,2 | 9,4 | 2,3 | 0,558 | 2072 | 2,0 GB | – |
| whisper `-l tr -t 8` | mihu-synth* | 81 | 13,8 | 9,0 | 2,2 | 0,385 | 1437 | 2,0 GB | – |
| mihu int8, 1 thread | mihu-synth* | 81 | 15,4 | 13,1 | 3,3 | 0,029 | 112 | 258 MB | 0,51 s / 17 ms |
| whisper-asistan | say-en | 5 | 5,1 | 5,1 | 5,4 | 0,786 | 1890 | 2,0 GB | – |
| mihu int8, 1 thread | say-en | 5 | 100 | 100 | 63,1 | 0,031 | 74 | 319 MB | – |

\* mihu-synth = modelin kendi yayımladığı sentetik set (yanlı olabilir). Kendi script'leriyle
(`eval/run_eval.py`, 24 kHz + librosa) %15,20 WER / %3,55 CER — ilan edilen 15,37 / 3,57 tekrarlandı.

Soğuk yükleme: mihu ~0,65–0,8 s (bir kez, süreç ömrü boyunca); Whisper `load time` ~0,25 s (sıcak disk
önbelleği) ama asistanda **her istekte** tekrarlanır ve dosya başı süreye dahildir.

## human (read) — konuşmacı bazında, WER/CER (sayı)

| | arda | ayhan | havva | melis | neva |
|---|---:|---:|---:|---:|---:|
| whisper-asistan | 17,9 / 6,3 | 3,6 / 1,6 | 0,9 / 0,1 | 7,1 / 2,0 | 2,7 / 0,8 |
| mihu int8 t1 | 48,2 / 22,4 | 9,8 / 3,0 | 2,7 / 0,3 | 14,3 / 4,1 | 13,4 / 3,7 |

Natural (referanssız) kliplerde de aynı tablo: Whisper metni anlamca doğru ("Bugün günüm çok güzel başladı.
Saat 10'a kadar uyudum…"); mihu akıcı ama kelime düzeyinde daha çok kayıyor ("Bugün benim çok güzel
başladı… kahvaltıma hazırladım"; arda: "Bu müsait… Sposon'a gittik" vs Whisper "Bugün saat… spor salonuna
gittik"; neva: mihu "Cime gittik", Whisper "gym'e gittik").

## Hata örnekleri (kategori bazlı)

mihu int8:
- marka: "Trendyol siparişim…" -> "**Tirangiol** siparişin…"
- özel isim: "Çağlar'a mesaj atmayı" -> "**çağlara rağmen** mesajla atmayı"
- tarih/saat: "3'ünde saat 20:15'te kimle" -> "üçünde saat **2015 Tekim'le**"
- tarih: "hangi güne denk geliyor" -> "hangi gün **neden** geliyor"
- Türkçe karakter (human): "Çağrı… ıhlamur… jilet" -> "**Çağırım**… **ızlamur**… (jilet düştü)"

Whisper (asistan ayarı):
- marka: "Garanti BBVA" -> "garanti **BB veya**"
- sayı: "Faturam 1250 lira" -> "Fatura**n**" (mihu'da da aynı)
- saat: "20:15'te" -> "20.15'te" (biçim; sayı WER'inde ufak ceza)
- human: "jilet" -> "**cilet**", "otobüs yirmi üç dakika gecikti" -> "otobüsü yirmi üç dakika**ya yedik**"
- adres: "kırk iki" -> "42" (rakam yazar; "sayı" normalizasyonunda doğru sayılır)

Sayılar: Whisper rakam yazar ("10'a", "9.30'a"), mihu çoğunlukla yazıyla ("dokuzda", "dokuz otuza") — bu yüzden
human sette Whisper'ın ham WER'i (8,4) sayı-normalize WER'inden (6,4) yüksek.

## İngilizce davranışı

mihu İngilizcede **boş değil, çöp** üretir (Türkçe fonetik uydurma): "Summarize my unread emails from today."
-> `Samris Mihnari Meoz Fırıntı Day`; "Play some relaxing jazz on Spotify." -> `Kaysamıyla laksin Cazonspari Fi`;
"Send a message to John…" -> `Sen de mesaj çıkacağım Z'ye gaybobi lay`. Güven/dil sinyali vermediği için
çıktısından İngilizce olduğu anlaşılamaz. Whisper `-l auto` 5/5 doğru; `-l tr` sabitlenirse 3/5'i Türkçeye
çevirip uyduruyor ("Bugün izlediğim yorumlarımı kısaltıyorum.").

## Hız / bellek notları

- mihu: RTF ~0,03 (M4 Pro, 1 thread) — ilan edilen EPYC 0,128'den hızlı. 4 thread kazanç sağlamıyor (100 ms
  parçalarda iş küçük). Final metin bas-konuş bırakıldıktan **~20 ms** sonra hazır; ilk ara sonuç konuşma
  başladıktan ~0,5 s sonra. RSS ~0,26–0,3 GB (fp32 ~0,54 GB, doğrulukta kazanç yok).
- Whisper: kısa komutta dosya başı ~1,4–2,3 s (30 sn'lik pencere sabit maliyeti + her istekte model yükleme).
  **`-l auto` dil tespiti istek başına ~0,6–0,8 s ekliyor** (2279 vs 1510 ms). Thread sayısı (1/4/8) Metal'de
  etkisiz; Metal kapalı CPU'da 2,5x yavaş. RSS ~2 GB.

## Lisans (mihu Community License 1.0)

Yıllık geliri < 500 bin USD olan için ücretsiz (kişisel, araştırma; ticari kullanım mihu'nun istediği kayıt sonrası).
Zorunlu: "Powered by mihu" ibaresi + NOTICE dosyası. Yasak: çıktılarla rakip model eğitmek, modeli yeniden
dağıtmak/satmak. Eşik aşılınca ayrı sözleşme; ihlalde lisans kendiliğinden sona erer.

## Öneri

**Şu an asistanda Türkçe için mihu'ya geçmek önerilmez.** Gerçek insan sesinde Whisper'ın hatası ~2,8 kat
daha düşük (WER 6,4 vs 17,7; CER 2,2 vs 6,7), marka/özel isimde de mihu daha zayıf ("Trendyol", "Çağlar") —
model kartındaki "brand-aware" iddiası bu sette görülmedi. mihu'nun gerçek avantajı gecikme (final ~20 ms vs
~1,5–2,3 s), bellek (0,3 vs 2 GB) ve akış; ama İngilizcede çöp ürettiği ve dil sinyali vermediği için
"Türkçe -> mihu, İngilizce -> Whisper" yönlendirmesi, dili **önceden** bilmeyi gerektirir (ayrı LID modeli ya da
Whisper'ı yine çalıştırmak), bu da kazancı büyük ölçüde siler.

Daha ucuz ve hemen uygulanabilir kazanım Whisper tarafında: kalıcı whisper sunucusu (istek başına model
yüklememek) ve dil sabitleme/ön-tespit (`-l auto` ~0,7 s).

mihu yine denenecekse (UYGULANMADI), gereken değişiklikler:
1. `src/speech.ts`: STT soyutlaması — `stt_engine: whisper | mihu | hybrid`; mihu için kalıcı Python
   (sherpa-onnx ≥ 1.13.8) işçi süreci (EmaWorker/KokoroWorker kalıbı), stdin'den PCM16, stdout'a metin.
2. Dil yönlendirme: `config.language` sabit `tr` ise mihu; `auto` ise hızlı LID (ör. Whisper tiny/base yalnız
   dil tespiti, ya da mihu ara metninin Türkçe sözlük oranı) — düşük güvende Whisper'a düş.
3. Akış: kayıt sırasında 100 ms parçaları mihu'ya canlı besle (ara sonuç UI'de), bırakınca final ~20 ms.
4. `guardText` eşikleri mihu çıktısına göre gözden geçirilmeli (Whisper'a özgü "altyazı m.k." kalıpları
   mihu'da yok; mihu'nun kısa çöp çıktıları için yeni kural).
5. Lisans: "Powered by mihu" + NOTICE; model dosyaları repoya konmaz (indirme betiği).

Kullanıcı kayıtları (`audio/user/`, `record.py`) eklenince `./run.sh user` ile bu tabloya ayrı satır gelir;
karar o sette de doğrulanmalı.
